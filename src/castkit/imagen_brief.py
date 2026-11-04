"""Lógica pura sobre las imágenes del brief: legibilidad, fondo y vistas (#93).

Este módulo no conoce la CLI ni el sistema de versiones de castkit: solo trabaja con
rutas de imágenes y devuelve datos (nunca pregunta nada ni imprime nada). Lo usa la
entrevista guiada de `castkit alta` (`cli.py`, ticket #93) para avisar de imágenes
ilegibles, detectar si el fondo es liso o transparente, y para contar y nombrar las
vistas (figuras separadas) de una imagen antes de generar la malla.

Solo depende de ``numpy``, ``scipy`` y ``Pillow``: nada de ``torch`` ni
``transformers`` (eso lo usa, aparte, el proveedor «profundidad»).

La separación figura/fondo (:func:`_mascara_figura`) es la misma lógica que usan los
proveedores locales de castkit (ver :mod:`castkit.profundidad`, que importa de aquí
la tolerancia compartida ``_TOLERANCIA_FONDO``) y la reutilizará, más adelante, el
proveedor «imagen-3d» (ticket #54).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

# Distancia RGB mínima al color de fondo (mediana del borde de la imagen) para que un
# píxel cuente como parte de la figura, cuando la imagen no tiene canal alfa real.
# Compartida con :mod:`castkit.profundidad` (que la importa de aquí) y, más adelante,
# con el proveedor «imagen-3d» (#54).
_TOLERANCIA_FONDO = 20.0

# Desviación media del borde (la distancia RGB euclidiana de cada píxel del borde a la
# mediana del borde, promediada) por debajo de la cual se considera que el fondo es
# liso. Medido sobre 25 imágenes reales de ~/castkit-pruebas: los fondos lisos o
# transparentes dan ≤2,5; una imagen con textura de fondo («guagua-pan-2.png») da 8,6;
# una con degradado («toro-original.png») da 16.
_DESVIACION_FONDO_UNIFORME_MAX = 5.0

# Porcentaje por defecto (del área total de la figura, tras rellenar huecos) que debe
# tener una componente conexa para contar como una vista; ajustable con
# `vista_area_min_pct` en `[generacion]` de `config/fabricacion.toml` (ver `config.py`).
AREA_MIN_PCT_DEFECTO = 10.0

# Margen, en píxeles de la imagen original, que se añade a la caja de una vista al
# recortarla (ver `recortar_vista`).
MARGEN_RECORTE_PX = 10


class ImagenBriefError(Exception):
    """Se lanza cuando no se puede recortar una vista de una imagen del brief.

    El mensaje siempre está en español. No depende de la jerarquía de excepciones de
    castkit (este módulo es lógica pura, sin CLI); quien lo use la traduce a la suya.
    """


@dataclass(frozen=True)
class Vista:
    """Una figura separada dentro de una imagen, ya nombrada por su posición."""

    nombre: str
    caja: tuple[int, int, int, int]  # (fila_min, col_min, fila_max, col_max), excl.
    area: int = 0  # Píxeles de la componente rellena (ver _candidatos_vistas).


@dataclass(frozen=True)
class AnalisisImagen:
    """Resultado de analizar una imagen del brief.

    ``fondo_uniforme`` es ``None`` cuando la imagen es ilegible (``legible`` en
    ``False``). ``vistas`` está vacía si la imagen es ilegible, si el fondo no es
    uniforme, o si no se detectó ninguna componente que alcance el umbral de área.
    """

    legible: bool
    fondo_uniforme: bool | None
    vistas: tuple[Vista, ...]


def _abrir(ruta: Path) -> Image.Image | None:
    """Abre y decodifica ``ruta`` con Pillow; ``None`` si no se puede (ilegible)."""
    try:
        imagen = Image.open(ruta)
        imagen.load()
        return imagen
    except Exception:
        return None


def _tiene_alfa_declarado(imagen: Image.Image) -> bool:
    return imagen.mode in ("RGBA", "LA", "PA") or "transparency" in imagen.info


def _separar_rgb_alfa(imagen: Image.Image) -> tuple[np.ndarray, np.ndarray | None]:
    arr = np.asarray(imagen.convert("RGBA"), dtype=np.float64)
    rgb = arr[..., :3]
    alfa = arr[..., 3] if _tiene_alfa_declarado(imagen) else None
    return rgb, alfa


def _alfa_es_real(alfa: np.ndarray | None) -> bool:
    """Un canal alfa con todo el valor 255 (totalmente opaco) se trata como sin alfa."""
    return alfa is not None and bool(np.any(alfa < 255.0))


def _color_fondo(rgb: np.ndarray) -> np.ndarray:
    borde = np.vstack([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    return np.median(borde, axis=0)


def _desviacion_fondo(rgb: np.ndarray) -> float:
    borde = np.vstack([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    fondo = np.median(borde, axis=0)
    return float(np.mean(np.linalg.norm(borde - fondo, axis=-1)))


def _mascara_figura(rgb: np.ndarray, alfa: np.ndarray | None) -> np.ndarray:
    """Separa la figura del fondo: el canal alfa real si lo hay; si no, la distancia
    al color de fondo (mediana del borde), con ``_TOLERANCIA_FONDO``.
    """
    if _alfa_es_real(alfa):
        return alfa > 128.0
    fondo = _color_fondo(rgb)
    return np.linalg.norm(rgb - fondo, axis=-1) > _TOLERANCIA_FONDO


def _nombres_vistas(n: int) -> list[str]:
    """Nombra ``n`` vistas por posición (ya ordenadas de izquierda a derecha)."""
    if n == 2:
        return ["izquierda", "derecha"]
    if n == 3:
        return ["izquierda", "centro", "derecha"]
    if n <= 1:
        return ["izquierda"][:n]
    return [f"vista {i}" for i in range(1, n + 1)]


def _candidatos_vistas(
    mascara: np.ndarray, area_min_pct: float
) -> list[tuple[float, int, tuple[int, int, int, int], int]]:
    """Componentes conexas de ``mascara`` (tras rellenar huecos) que alcanzan el
    umbral de área, como ``(centro_x, etiqueta, caja, area)``, sin ordenar todavía.
    """
    rellena = ndimage.binary_fill_holes(mascara)
    etiquetas, n = ndimage.label(rellena)
    area_total = int(np.count_nonzero(rellena))
    if n == 0 or area_total == 0:
        return []

    umbral = (area_min_pct / 100.0) * area_total
    cajas = ndimage.find_objects(etiquetas)

    candidatos: list[tuple[float, int, tuple[int, int, int, int], int]] = []
    for etiqueta in range(1, n + 1):
        caja = cajas[etiqueta - 1]
        if caja is None:
            continue
        fila_s, col_s = caja
        area = int(np.count_nonzero(etiquetas[caja] == etiqueta))
        if area < umbral:
            continue
        centro_x = (col_s.start + col_s.stop) / 2.0
        caja = (fila_s.start, col_s.start, fila_s.stop, col_s.stop)
        candidatos.append((centro_x, etiqueta, caja, area))
    return candidatos


def analizar_imagen(ruta: Path, area_min_pct: float = AREA_MIN_PCT_DEFECTO) -> AnalisisImagen:
    """Analiza una imagen del brief: legibilidad, uniformidad del fondo y vistas.

    No modifica ``ruta``. Si la imagen no se puede abrir (archivo corrupto o con una
    extensión de imagen que en realidad no lo es), devuelve ``legible=False``. Si el
    fondo no es uniforme, devuelve ``vistas=()`` sin intentar separar componentes
    (evita presentar manchas de un fondo ruidoso como vistas).
    """
    imagen = _abrir(Path(ruta))
    if imagen is None:
        return AnalisisImagen(legible=False, fondo_uniforme=None, vistas=())

    rgb, alfa = _separar_rgb_alfa(imagen)

    if _alfa_es_real(alfa):
        mascara = alfa > 128.0
    else:
        if _desviacion_fondo(rgb) > _DESVIACION_FONDO_UNIFORME_MAX:
            return AnalisisImagen(legible=True, fondo_uniforme=False, vistas=())
        mascara = _mascara_figura(rgb, alfa)

    candidatos = sorted(_candidatos_vistas(mascara, area_min_pct), key=lambda c: c[0])
    nombres = _nombres_vistas(len(candidatos))
    vistas = tuple(
        Vista(nombre=nombre, caja=caja, area=area)
        for (_, _, caja, area), nombre in zip(candidatos, nombres, strict=True)
    )
    return AnalisisImagen(legible=True, fondo_uniforme=True, vistas=vistas)


def vista_mayor(vistas: tuple[Vista, ...]) -> Vista | None:
    """Devuelve la vista de mayor área de ``vistas``; ``None`` si está vacía."""
    if not vistas:
        return None
    return max(vistas, key=lambda v: v.area)


def recortar_vista(
    ruta_imagen: Path,
    nombre_vista: str,
    destino: Path,
    *,
    area_min_pct: float = AREA_MIN_PCT_DEFECTO,
    margen_px: int = MARGEN_RECORTE_PX,
) -> Path:
    """Recorta de ``ruta_imagen`` la vista llamada ``nombre_vista`` y la guarda en
    ``destino`` (la carpeta ``brief/`` del diseño) como
    ``<nombre-sin-extension>-vista-<nombre_vista>.png``.

    La caja de recorte es la de esa componente conexa más ``margen_px`` por lado
    (recortado a los bordes de la imagen). Todo lo que, dentro de esa caja, no
    pertenezca a la componente elegida (fondo, manchas o una figura vecina) se pinta
    del color de fondo (la mediana del borde de la imagen original), o se vuelve
    transparente si la imagen original tiene canal alfa real: así el borde del
    recorte queda liso para cualquier proveedor. No modifica ``ruta_imagen``.
    """
    ruta_imagen = Path(ruta_imagen)
    imagen = _abrir(ruta_imagen)
    if imagen is None:
        raise ImagenBriefError(f'No se pudo abrir la imagen "{ruta_imagen.name}" para recortarla.')

    rgb, alfa = _separar_rgb_alfa(imagen)
    alfa_real = _alfa_es_real(alfa)
    mascara = _mascara_figura(rgb, alfa)

    candidatos = sorted(_candidatos_vistas(mascara, area_min_pct), key=lambda c: c[0])
    nombres = _nombres_vistas(len(candidatos))
    try:
        indice = nombres.index(nombre_vista)
    except ValueError:
        raise ImagenBriefError(
            f'La imagen "{ruta_imagen.name}" ya no tiene una vista "{nombre_vista}".'
        ) from None

    _, etiqueta, (fila_min_c, col_min_c, fila_max_c, col_max_c), _ = candidatos[indice]
    rellena = ndimage.binary_fill_holes(mascara)
    etiquetas, _ = ndimage.label(rellena)

    alto, ancho = rellena.shape
    fila_min = max(fila_min_c - margen_px, 0)
    fila_max = min(fila_max_c + margen_px, alto)
    col_min = max(col_min_c - margen_px, 0)
    col_max = min(col_max_c + margen_px, ancho)

    pertenece = (etiquetas == etiqueta)[fila_min:fila_max, col_min:col_max]
    recorte_rgb = rgb[fila_min:fila_max, col_min:col_max].copy()

    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    nombre_archivo = f"{ruta_imagen.stem}-vista-{nombre_vista}.png"
    ruta_salida = destino / nombre_archivo

    if alfa_real:
        recorte_alfa = alfa[fila_min:fila_max, col_min:col_max].copy()  # type: ignore[index]
        recorte_alfa[~pertenece] = 0.0
        salida = np.concatenate([recorte_rgb, recorte_alfa[..., None]], axis=-1)
        Image.fromarray(salida.astype(np.uint8), mode="RGBA").save(ruta_salida)
    else:
        fondo = _color_fondo(rgb)
        recorte_rgb[~pertenece] = fondo
        Image.fromarray(recorte_rgb.astype(np.uint8), mode="RGB").save(ruta_salida)

    return ruta_salida
