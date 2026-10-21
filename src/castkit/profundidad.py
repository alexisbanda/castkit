"""Lógica pura del proveedor «profundidad»: media figura para molde abierto desde una
imagen frontal (#56).

Reconstruye la media figura de un molde abierto (#55, espalda plana) a partir de un
mapa de profundidad (Depth Anything V2 **Small**, Apache-2.0; las variantes Base/Large
son CC-BY-NC y no se admiten) y del sombreado de la imagen, que realza los motivos
finos que la profundidad por sí sola suaviza. Este módulo no conoce la CLI ni el
sistema de versiones de castkit: solo trabaja con imágenes, un mapa de profundidad ya
calculado, un archivo ``profundidad.toml`` y las medidas (``largo_mm``, ``grosor_mm``)
que le pase quien lo use (ver :class:`castkit.generacion.ProveedorProfundidad`).

Reutiliza de :mod:`castkit.relieve` el trazado de contorno con cierre morfológico
(``_seccion_mm``), la base sacrificable (``CORTE_SACRIFICABLE_MM``) y el formateo de
números de TOML (``_formatear_numero``), en vez de duplicarlos.

Pasos (ver también los prototipos de referencia y sus lecciones en
``~/castkit-pruebas/prototipos/LEEME.md``):
1. Separar la figura del fondo: el canal alfa si la imagen lo tiene; si no, el color
   mayoritario del borde (con una tolerancia), igual que el resto de proveedores
   locales de castkit. Se toma la componente conexa más grande y se rellenan los
   huecos.
2. Reescalar al tamaño de trabajo: ``mm_por_px`` fija la resolución del campo de
   alturas (y por tanto el tamaño de la malla), independientemente de la resolución
   de la imagen de entrada — así el costo de memoria/tiempo no depende del tamaño del
   archivo que suba el operador.
3. Ensanchar las partes más finas que ``ancho_min_mm`` (lo que desaparece al abrir la
   máscara con un disco de ese diámetro) ``engrose_mm`` por lado, para que no
   desaparezcan al moldear.
4. Altura = ``borde_mm`` (el mínimo) + la forma (profundidad normalizada por
   percentiles, hasta ``grosor_mm - borde_mm - realce_mm``) + ``realce_mm`` de realce
   del sombreado (motivos finos que la profundidad suaviza), todo suavizado con
   ``suavizado`` (sigma en píxeles de trabajo) y reescalado para que el máximo
   coincida exactamente con ``grosor_mm``. Fuera de la figura (en el margen que se usa
   para la pared) se extiende por vecino más cercano, para que la pared no caiga a 0.
5. Malla **sin escalones**: el campo de alturas se muestrea por vértice (cada píxel de
   la rejilla de trabajo es un vértice con su propia altura ya continua, no un valor
   constante por celda) y se tapa por abajo con una base plana en z=0, sobre una
   región bastante más ancha que la figura (margen de seguridad). El contorno final
   se traza sobre la máscara ensanchada, suavizada con un gaussiano (para que siga un
   polígono, no el borde pixelado) y convertida en un ``manifold3d.CrossSection`` con
   cierre morfológico (:func:`castkit.relieve._seccion_mm`). La intersección 3D de
   ambos sólidos deja paredes verticales lisas (las del contorno) con el borde
   superior interpolado del campo de alturas (continuo, sin escalones).
6. Se añaden :data:`castkit.relieve.CORTE_SACRIFICABLE_MM` (0,5 mm) de base
   sacrificable (misma silueta que el contorno final) debajo de todo; `castkit
   limpieza` la recorta en un molde abierto (#55), dejando la espalda plana y el
   grosor exacto. Antes de devolver el resultado, la malla exportada se recarga con
   `trimesh` y se comprueba que sigue cerrada.
7. ``brief/profundidad.toml`` guarda, resueltos, los valores de altura usados (copia
   de ``[profundidad]`` la primera vez que se genera esta versión; si el operador lo
   edita, se respeta).
"""

from __future__ import annotations

import math
import tomllib
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image
from scipy import ndimage

from castkit.relieve import CORTE_SACRIFICABLE_MM, _formatear_numero, _seccion_mm

NOMBRE_TOML = "profundidad.toml"

# Único modelo de Depth Anything V2 con licencia permisiva (Apache-2.0); las
# variantes Base y Large son CC-BY-NC (uso no comercial) y no se admiten.
MODELO_PERMITIDO = "depth-anything/Depth-Anything-V2-Small-hf"

CLAVES_PROFUNDIDAD = (
    "borde_mm",
    "realce_mm",
    "suavizado",
    "ancho_min_mm",
    "engrose_mm",
    "mm_por_px",
)

# Margen (en píxeles de la resolución de trabajo) con el que se dilata la figura
# ensanchada antes de muestrear el campo de alturas: cubre de sobra el contorno
# final (suavizado y con cierre morfológico), para que la pared nunca se corte
# contra una zona del campo sin datos (alturas en 0).
_MARGEN_CAMPO_PX = 4

# Semilla fija y magnitud (mm) de un jitter determinista aplicado en XY a los
# vértices del campo de alturas antes de intersecarlo con el prisma del
# contorno. manifold3d (CSG robusto en float32) puede dejar la malla sin
# cerrar al recargar el STL cuando una rejilla muy regular (muchos vértices
# alineados en los mismos ejes/valores) se corta con un polígono con miles de
# segmentos: un jitter minúsculo (muy por debajo de cualquier tolerancia de
# impresión o de los criterios de este proveedor) rompe esa alineación sin
# alterar la geometría de forma perceptible. Es determinista (misma semilla)
# para que la misma imagen produzca siempre el mismo STL.
_SEMILLA_JITTER = 0
_JITTER_MM = 0.005

# Sigma (px, resolución de trabajo) del suavizado de la máscara antes de trazar el
# contorno final: evita que el contorno siga el borde pixelado (en escalón).
_SUAVIZADO_CONTORNO_PX = 1.2

# Distancia RGB mínima al color de fondo (mediana del borde de la imagen) para que
# un píxel se considere parte de la figura, cuando la imagen no tiene canal alfa.
_TOLERANCIA_FONDO = 20.0


class ProfundidadError(Exception):
    """Se lanza cuando la lógica pura de «profundidad» no puede completarse.

    El mensaje siempre está en español. No depende de la jerarquía de
    excepciones de castkit (este módulo es lógica pura, sin CLI); quien lo use
    (por ejemplo, :class:`castkit.generacion.ProveedorProfundidad`) la traduce
    a :class:`castkit.generacion.GeneracionError`.
    """


class ExtraNoInstaladoError(ProfundidadError):
    """Se lanza cuando falta el extra opcional ``profundidad`` (torch/transformers)."""


def _validar_modelo(modelo: str) -> None:
    """Rechaza cualquier modelo que no sea :data:`MODELO_PERMITIDO`.

    Las variantes Base y Large de Depth Anything V2 (y cualquier otro modelo)
    usan licencia CC-BY-NC (uso no comercial) y no se pueden usar en castkit.
    """
    if modelo != MODELO_PERMITIDO:
        raise ProfundidadError(
            f'El proveedor "profundidad" solo admite el modelo "{MODELO_PERMITIDO}" '
            "(licencia Apache-2.0, uso comercial permitido). Las variantes Base y Large de "
            "Depth Anything V2 (y cualquier otro modelo) usan licencia CC-BY-NC (solo uso no "
            f'comercial) y no se pueden usar en castkit. Recibido: "{modelo}".'
        )


def estimar_profundidad(ruta_imagen: Path, modelo: str, revision: str) -> np.ndarray:
    """Calcula el mapa de profundidad de ``ruta_imagen`` con Depth Anything V2 (CPU).

    Importa ``torch`` y ``transformers`` de forma perezosa, solo al llamar esta
    función, para no cargarlos en quien no usa este proveedor. Lanza
    :class:`ExtraNoInstaladoError` si no están instalados (ver el extra opcional
    ``profundidad`` de `pyproject.toml`), y :class:`ProfundidadError` si
    ``modelo`` no es :data:`MODELO_PERMITIDO`. Devuelve un `ndarray` `float32`
    del mismo alto y ancho (en píxeles) que la imagen.
    """
    _validar_modelo(modelo)

    try:
        import torch  # noqa: F401  (basta con que la importación no falle)
        from transformers import pipeline
    except ImportError as exc:
        raise ExtraNoInstaladoError(
            "El proveedor profundidad necesita el extra opcional: uv sync --extra profundidad"
        ) from exc

    ruta_imagen = Path(ruta_imagen)
    try:
        imagen = Image.open(ruta_imagen).convert("RGB")
    except Exception as exc:
        raise ProfundidadError(f'No se pudo abrir la imagen "{ruta_imagen.name}": {exc}') from exc

    estimador = pipeline("depth-estimation", model=modelo, revision=revision, device="cpu")
    resultado = estimador(imagen)
    mapa = np.asarray(resultado["predicted_depth"].squeeze(), dtype=np.float32)
    if mapa.shape != (imagen.height, imagen.width):
        mapa = np.asarray(
            Image.fromarray(mapa).resize(imagen.size, Image.Resampling.BILINEAR),
            dtype=np.float32,
        )
    return mapa


def escribir_profundidad_toml(ruta_toml: Path, datos: dict[str, Any]) -> None:
    """Escribe ``datos`` (ver :data:`CLAVES_PROFUNDIDAD`) como ``profundidad.toml``."""
    faltantes = [clave for clave in CLAVES_PROFUNDIDAD if clave not in datos]
    if faltantes:
        raise ProfundidadError(
            f'Faltan estas claves para escribir "{ruta_toml}": {", ".join(faltantes)}.'
        )

    lineas = [
        "# brief/profundidad.toml — alturas del proveedor «profundidad», copiadas de",
        "# [profundidad] en config/fabricacion.toml la primera vez que se generó esta versión.",
        "#",
        "# Edita estos valores y vuelve a generar con --reemplazar para ajustarlos.",
        "",
        f"borde_mm = {_formatear_numero(datos['borde_mm'])}",
        "# Altura (mm) del punto más bajo de la figura (el borde). grosor_mm del diseño es el",
        "# máximo; entre borde_mm y grosor_mm se reparten la forma (profundidad) y el realce.",
        f"realce_mm = {_formatear_numero(datos['realce_mm'])}",
        "# Milímetros, dentro del rango anterior, dedicados a realzar los motivos finos a partir",
        "# del sombreado de la imagen (la profundidad por sí sola los suaviza).",
        f"suavizado = {_formatear_numero(datos['suavizado'])}",
        "# Sigma (en píxeles de la resolución de trabajo, ver mm_por_px) del suavizado gaussiano",
        "# del campo de alturas.",
        f"ancho_min_mm = {_formatear_numero(datos['ancho_min_mm'])}",
        "# Partes de la figura más estrechas que esto (mm) se ensanchan (ver engrose_mm) para que",
        "# no desaparezcan al moldear o imprimir.",
        f"engrose_mm = {_formatear_numero(datos['engrose_mm'])}",
        "# Milímetros que se ensancha, por lado, cada parte más estrecha que ancho_min_mm.",
        f"mm_por_px = {_formatear_numero(datos['mm_por_px'])}",
        "# Resolución de trabajo (mm por píxel) del campo de alturas: no es la resolución de la",
        "# imagen de entrada, sino la de la malla (más fino = más detalle y más memoria/tiempo).",
        "",
    ]
    contenido = "\n".join(lineas).rstrip("\n") + "\n"
    Path(ruta_toml).write_text(contenido, encoding="utf-8")


def leer_profundidad_toml(ruta_toml: Path) -> dict[str, Any]:
    """Lee un ``profundidad.toml`` existente; misma forma que ``escribir_profundidad_toml``."""
    ruta_toml = Path(ruta_toml)
    try:
        with ruta_toml.open("rb") as flujo:
            datos = tomllib.load(flujo)
    except tomllib.TOMLDecodeError as exc:
        raise ProfundidadError(f'El archivo "{ruta_toml}" no es un TOML válido: {exc}') from exc

    faltantes = [clave for clave in CLAVES_PROFUNDIDAD if clave not in datos]
    if faltantes:
        raise ProfundidadError(
            f'Al archivo "{ruta_toml}" le faltan estas claves: {", ".join(faltantes)}.'
        )
    return {clave: float(datos[clave]) for clave in CLAVES_PROFUNDIDAD}


def _cargar_rgb_y_alfa(ruta_imagen: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """Abre ``ruta_imagen``; ``(rgb, alfa)`` en `float64` (``alfa`` es ``None`` sin canal alfa)."""
    ruta_imagen = Path(ruta_imagen)
    try:
        imagen = Image.open(ruta_imagen)
    except Exception as exc:
        raise ProfundidadError(f'No se pudo abrir la imagen "{ruta_imagen.name}": {exc}') from exc

    tiene_alfa = imagen.mode in ("RGBA", "LA", "PA") or "transparency" in imagen.info
    arr = np.asarray(imagen.convert("RGBA"), dtype=np.float64)
    rgb = arr[..., :3]
    alfa = arr[..., 3] if tiene_alfa else None
    return rgb, alfa


def _mascara_figura(rgb: np.ndarray, alfa: np.ndarray | None) -> np.ndarray:
    """Separa la figura del fondo: el canal alfa si existe; si no, el color del borde (tolerancia).

    Se queda con la componente conexa más grande y rellena sus huecos.
    """
    if alfa is not None:
        figura = alfa > 128.0
    else:
        borde = np.vstack([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
        fondo = np.median(borde, axis=0)
        figura = np.linalg.norm(rgb - fondo, axis=-1) > _TOLERANCIA_FONDO

    figura = ndimage.binary_fill_holes(figura)
    etiquetas, n = ndimage.label(figura)
    if n > 1:
        tamanos = ndimage.sum(figura, etiquetas, range(1, n + 1))
        mayor = int(np.argmax(tamanos)) + 1
        figura = etiquetas == mayor
    return figura


def _trazar_contorno(mascara: np.ndarray, mm_por_px: float) -> Any | None:
    """Contorno de ``mascara`` como polígono suavizado (no por celdas de píxel).

    Suaviza ``mascara`` con un gaussiano (para que el polígono no siga el
    borde pixelado) y reutiliza :func:`castkit.relieve._seccion_mm` (mismo
    trazado de contorno, cierre morfológico y filtro de área) para construir
    el ``manifold3d.CrossSection``.
    """
    mascara_f64 = mascara.astype(np.float64)
    suave = ndimage.gaussian_filter(mascara_f64, _SUAVIZADO_CONTORNO_PX)
    return _seccion_mm(suave, mm_por_px, 0.01)


def _malla_campo_alturas(campo: np.ndarray, alto: np.ndarray, mm_por_px: float) -> trimesh.Trimesh:
    """Sólido del campo de alturas sobre la región ``campo``, sin escalones.

    Cada píxel de la rejilla de trabajo es un vértice con su propia altura (ya
    continua: ``alto`` no tiene niveles discretos, a diferencia de las terrazas de
    `relieve`), no un valor constante por celda; dos celdas vecinas comparten sus
    vértices y, por tanto, su altura en el borde común. La base es un plano en z=0
    con la misma huella. Usa la MISMA convención de coordenadas que
    :func:`castkit.relieve._seccion_mm` (``x = columna · mm_por_px``,
    ``y = (filas + 4 − fila) · mm_por_px``, con ``filas = campo.shape[0]``) para que
    este sólido quede exactamente alineado con el contorno (otro `CrossSection`
    construido con esa misma función) al intersecarlos en 3D.
    """
    filas, columnas = campo.shape
    campo = campo.copy()

    # Sin contactos en diagonal entre celdas activas (un vértice que solo tocara a
    # otra celda por una esquina dejaría una arista no variedad en la malla).
    for _ in range(10):
        a, b = campo[:-1, :-1], campo[:-1, 1:]
        c, d = campo[1:, :-1], campo[1:, 1:]
        diagonal = (a & d & ~b & ~c) | (b & c & ~a & ~d)
        if not diagonal.any():
            break
        for dr, dc in ((0, 0), (0, 1), (1, 0), (1, 1)):
            campo[dr : dr + filas - 1, dc : dc + columnas - 1] |= diagonal

    quad_activo = campo[:-1, :-1] & campo[:-1, 1:] & campo[1:, :-1] & campo[1:, 1:]
    if not quad_activo.any():
        raise ProfundidadError("El campo de alturas quedó vacío (revisa mm_por_px y los márgenes).")

    vertice_activo = np.zeros((filas, columnas), dtype=bool)
    vertice_activo[:-1, :-1] |= quad_activo
    vertice_activo[:-1, 1:] |= quad_activo
    vertice_activo[1:, :-1] |= quad_activo
    vertice_activo[1:, 1:] |= quad_activo

    indices_sup = np.full((filas, columnas), -1, dtype=np.int32)
    n_activos = int(vertice_activo.sum())
    indices_sup[vertice_activo] = np.arange(n_activos, dtype=np.int32)
    indices_inf = np.where(vertice_activo, indices_sup + n_activos, -1).astype(np.int32)

    filas_v, cols_v = np.where(vertice_activo)
    xs = cols_v.astype(np.float64) * mm_por_px
    ys = (filas + 4 - filas_v).astype(np.float64) * mm_por_px

    # Jitter determinista (ver _JITTER_MM): se aplica a xs/ys ANTES de
    # construir los vértices de techo y base, así ambos (misma columna/fila)
    # se desplazan igual y las paredes laterales siguen exactamente verticales.
    rng = np.random.default_rng(_SEMILLA_JITTER)
    xs = xs + rng.uniform(-_JITTER_MM, _JITTER_MM, size=xs.shape)
    ys = ys + rng.uniform(-_JITTER_MM, _JITTER_MM, size=ys.shape)
    zs = alto[filas_v, cols_v]

    vertices = np.empty((2 * n_activos, 3), dtype=np.float64)
    vertices[:n_activos] = np.column_stack([xs, ys, zs])
    vertices[n_activos:] = np.column_stack([xs, ys, np.zeros_like(zs)])

    # Construcción vectorizada (sin bucle por celda en Python: con cientos de
    # miles de celdas, ese bucle es lento y gasta mucha memoria en listas de
    # tuplas). Las 4 esquinas de cada celda activa, de techo y de base.
    filas_q, cols_q = np.where(quad_activo)
    s00 = indices_sup[filas_q, cols_q]
    s10 = indices_sup[filas_q + 1, cols_q]
    s11 = indices_sup[filas_q + 1, cols_q + 1]
    s01 = indices_sup[filas_q, cols_q + 1]
    i00 = indices_inf[filas_q, cols_q]
    i10 = indices_inf[filas_q + 1, cols_q]
    i11 = indices_inf[filas_q + 1, cols_q + 1]
    i01 = indices_inf[filas_q, cols_q + 1]

    bloques_caras = [
        np.column_stack([s00, s10, s11]),
        np.column_stack([s00, s11, s01]),
        np.column_stack([i00, i11, i10]),
        np.column_stack([i00, i01, i11]),
    ]

    # Paredes laterales: una celda activa necesita pared en cada lado cuyo
    # vecino (en esa dirección) no está activo (incluido el borde de la rejilla).
    vecino_arriba = np.zeros_like(quad_activo)
    vecino_arriba[1:, :] = quad_activo[:-1, :]
    vecino_abajo = np.zeros_like(quad_activo)
    vecino_abajo[:-1, :] = quad_activo[1:, :]
    vecino_izq = np.zeros_like(quad_activo)
    vecino_izq[:, 1:] = quad_activo[:, :-1]
    vecino_der = np.zeros_like(quad_activo)
    vecino_der[:, :-1] = quad_activo[:, 1:]

    def _agregar_pared(mascara_borde: np.ndarray, esquinas: tuple[str, str]) -> None:
        a, b = esquinas
        ra, ca = np.where(mascara_borde)
        if ra.size == 0:
            return
        esquina_a = {"00": (ra, ca), "10": (ra + 1, ca), "11": (ra + 1, ca + 1), "01": (ra, ca + 1)}
        fa, cola = esquina_a[a]
        fb, colb = esquina_a[b]
        sa, sb = indices_sup[fa, cola], indices_sup[fb, colb]
        ia, ib = indices_inf[fa, cola], indices_inf[fb, colb]
        bloques_caras.append(np.column_stack([sa, sb, ib]))
        bloques_caras.append(np.column_stack([sa, ib, ia]))

    _agregar_pared(quad_activo & ~vecino_arriba, ("00", "01"))
    _agregar_pared(quad_activo & ~vecino_abajo, ("11", "10"))
    _agregar_pared(quad_activo & ~vecino_izq, ("10", "00"))
    _agregar_pared(quad_activo & ~vecino_der, ("01", "11"))

    caras = np.concatenate(bloques_caras, axis=0).astype(np.int64)
    # Sin process=True: la conectividad ya es la de una malla cerrada válida
    # (vértices compartidos por construcción), y process=True repetiría ese
    # trabajo (fusión de vértices, caras degeneradas) sin necesidad, con el
    # único costo de más memoria para una malla de cientos de miles de caras.
    malla = trimesh.Trimesh(vertices=vertices, faces=caras, process=False)
    trimesh.repair.fix_normals(malla)
    return malla


def generar_malla_profundidad(
    ruta_imagen: Path,
    mapa_profundidad: np.ndarray,
    datos_profundidad: dict[str, Any],
    *,
    largo_mm: float,
    grosor_mm: float,
    destino_stl: Path,
) -> dict[str, Any]:
    """Construye la media figura por profundidad y la exporta a ``destino_stl``.

    ``mapa_profundidad`` es el `ndarray` devuelto por :func:`estimar_profundidad`
    (o, en los tests, cualquier mapa sintético del mismo tamaño que la imagen).
    ``largo_mm`` es la medida mayor en planta (ver #55: molde abierto) y
    ``grosor_mm`` es el grosor final que debe quedar tras `castkit limpieza`. La
    malla en bruto que este proveedor escribe mide
    ``grosor_mm + CORTE_SACRIFICABLE_MM`` de alto: los últimos
    ``CORTE_SACRIFICABLE_MM`` (0,5 mm) de abajo son una base sacrificable que
    `castkit limpieza` recorta, dejando la espalda plana y el grosor exacto.
    Devuelve ``{"parametros_registro": {...}}`` con los parámetros de altura
    usados (y las medidas finales).
    """
    import manifold3d as m3d

    if not math.isfinite(largo_mm) or largo_mm <= 0:
        raise ProfundidadError(f'"largo_mm" debe ser mayor que 0; recibido "{largo_mm}".')
    if not math.isfinite(grosor_mm) or grosor_mm <= 0:
        raise ProfundidadError(f'"grosor_mm" debe ser mayor que 0; recibido "{grosor_mm}".')

    faltantes = [clave for clave in CLAVES_PROFUNDIDAD if clave not in datos_profundidad]
    if faltantes:
        raise ProfundidadError(
            f"Faltan estas claves en los datos de profundidad: {', '.join(faltantes)}."
        )
    borde_mm = float(datos_profundidad["borde_mm"])
    realce_mm = float(datos_profundidad["realce_mm"])
    suavizado = float(datos_profundidad["suavizado"])
    ancho_min_mm = float(datos_profundidad["ancho_min_mm"])
    engrose_mm = float(datos_profundidad["engrose_mm"])
    mm_por_px = float(datos_profundidad["mm_por_px"])

    if mm_por_px <= 0:
        raise ProfundidadError(f'"mm_por_px" debe ser mayor que 0; recibido "{mm_por_px}".')
    if borde_mm <= 0:
        raise ProfundidadError(f'"borde_mm" debe ser mayor que 0; recibido "{borde_mm}".')
    if borde_mm >= grosor_mm:
        raise ProfundidadError(
            f'"borde_mm" ({borde_mm} mm) debe ser menor que "grosor_mm" ({grosor_mm} mm).'
        )
    alto_disponible_mm = grosor_mm - borde_mm
    if realce_mm < 0 or realce_mm > alto_disponible_mm:
        raise ProfundidadError(
            f'"realce_mm" ({realce_mm} mm) debe estar entre 0 y "grosor_mm - borde_mm" '
            f"({alto_disponible_mm} mm)."
        )
    if ancho_min_mm <= 0:
        raise ProfundidadError(f'"ancho_min_mm" debe ser mayor que 0; recibido "{ancho_min_mm}".')
    if engrose_mm < 0:
        raise ProfundidadError(f'"engrose_mm" no puede ser negativo; recibido "{engrose_mm}".')

    rgb, alfa = _cargar_rgb_y_alfa(ruta_imagen)
    mapa_profundidad = np.asarray(mapa_profundidad, dtype=np.float64)
    if mapa_profundidad.shape != rgb.shape[:2]:
        raise ProfundidadError(
            f"El mapa de profundidad {mapa_profundidad.shape} no coincide con el tamaño de "
            f'"{Path(ruta_imagen).name}" {rgb.shape[:2]}.'
        )

    figura = _mascara_figura(rgb, alfa)
    if not figura.any():
        raise ProfundidadError(
            f'La imagen "{Path(ruta_imagen).name}" no tiene ninguna figura detectable.'
        )

    ys, xs = np.where(figura)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    figura = figura[y0:y1, x0:x1]
    gris = rgb[y0:y1, x0:x1].mean(axis=-1)
    profundidad_cruda = mapa_profundidad[y0:y1, x0:x1]

    alto_px0, ancho_px0 = figura.shape
    extension_px0 = float(max(alto_px0, ancho_px0))
    if extension_px0 <= 0:
        raise ProfundidadError("La figura detectada no tiene extensión; no se puede escalar.")

    # Resolución de trabajo: mm_por_px (de config/profundidad.toml) fija, de forma
    # aproximada, el tamaño del campo de alturas; el valor EFECTIVO que se usa para
    # toda la geometría se deriva de las dimensiones ya redondeadas de la FIGURA
    # (no de la figura más el margen, ver abajo), para que largo_mm se cumpla con
    # exactitud y no se acumule el error de redondeo de dos reescalados distintos.
    alto_px_figura = max(8, int(round(alto_px0 * (largo_mm / mm_por_px) / extension_px0)))
    ancho_px_figura = max(8, int(round(ancho_px0 * (largo_mm / mm_por_px) / extension_px0)))
    mm_por_px_efectivo = largo_mm / float(max(alto_px_figura, ancho_px_figura))

    def _reescalar_a(array: np.ndarray, alto: int, ancho: int) -> np.ndarray:
        imagen_tmp = Image.fromarray(array.astype(np.float32))
        return np.asarray(
            imagen_tmp.resize((ancho, alto), Image.Resampling.BILINEAR), dtype=np.float64
        )

    figura_f64 = figura.astype(np.float64)
    figura_sin_margen = _reescalar_a(figura_f64, alto_px_figura, ancho_px_figura) > 0.5
    gris_sin_margen = _reescalar_a(gris, alto_px_figura, ancho_px_figura)
    profundidad_sin_margen = _reescalar_a(profundidad_cruda, alto_px_figura, ancho_px_figura)

    # Margen de aire alrededor de la figura, YA en resolución de trabajo (sin la
    # ambigüedad de redondeo de pasarlo por dos reescalados distintos): el campo
    # de alturas necesita sitio para crecer (_MARGEN_CAMPO_PX) más un colchón.
    margen_px = _MARGEN_CAMPO_PX + 6
    figura_f = np.pad(figura_sin_margen, margen_px, mode="constant", constant_values=False)
    gris_r = np.pad(gris_sin_margen, margen_px, mode="edge")
    profundidad_r = np.pad(profundidad_sin_margen, margen_px, mode="edge")
    mm_por_px = mm_por_px_efectivo

    if not figura_f.any():
        raise ProfundidadError(
            "La figura desapareció al escalar a la resolución de trabajo: reduce mm_por_px."
        )

    # Partes más finas que ancho_min_mm (lo que desaparece al abrir con un disco de
    # ese diámetro) se ensanchan engrose_mm por lado. El borde de una figura curva
    # (p. ej. un círculo) pierde y gana píxeles sueltos al abrirla con un disco
    # discreto, por el redondeo de la rejilla, no porque sea realmente angosta: se
    # descartan las manchas de "finas" más pequeñas que el propio disco de apertura
    # (ruido de un par de píxeles), para no ensanchar un borde que ya es ancho.
    radio_min_px = max(1, int(round(ancho_min_mm / 2.0 / mm_por_px)))
    yy, xx = np.mgrid[-radio_min_px : radio_min_px + 1, -radio_min_px : radio_min_px + 1]
    disco_min = (xx**2 + yy**2) <= radio_min_px**2
    finas_crudas = figura_f & ~ndimage.binary_opening(figura_f, structure=disco_min)
    etiquetas_finas, n_finas = ndimage.label(finas_crudas)
    if n_finas > 0:
        area_min_finas_px = max(4, int(disco_min.sum()) // 2)
        tamanos_finas = ndimage.sum(finas_crudas, etiquetas_finas, range(1, n_finas + 1))
        validas = np.flatnonzero(tamanos_finas >= area_min_finas_px) + 1
        finas = np.isin(etiquetas_finas, validas)
    else:
        finas = finas_crudas

    radio_engrose_px = max(1, int(round(engrose_mm / mm_por_px)))
    rango_engrose = (-radio_engrose_px, radio_engrose_px + 1)
    yy, xx = np.mgrid[rango_engrose[0] : rango_engrose[1], rango_engrose[0] : rango_engrose[1]]
    disco_engrose = (xx**2 + yy**2) <= radio_engrose_px**2
    figura_ancha = figura_f | ndimage.binary_dilation(finas, structure=disco_engrose)

    # Alturas: forma (profundidad normalizada por percentiles) + realce del
    # sombreado (motivos finos que la profundidad por sí sola suaviza).
    lo, hi = np.percentile(profundidad_r[figura_f], [1.0, 99.5])
    if hi <= lo:
        forma = np.zeros_like(profundidad_r)
    else:
        forma = np.clip((profundidad_r - lo) / (hi - lo), 0.0, 1.0)

    detalle = gris_r - ndimage.gaussian_filter(gris_r, 2.0)
    escala_detalle = float(np.percentile(np.abs(detalle[figura_f]), 98)) + 1e-9
    detalle = np.clip(detalle / escala_detalle, -1.0, 1.0)

    contrib = (alto_disponible_mm - realce_mm) * forma + realce_mm * (detalle * 0.5 + 0.5)
    if suavizado > 0:
        contrib = ndimage.gaussian_filter(contrib, sigma=suavizado)
    pico = float(contrib[figura_f].max())
    if pico <= 0:
        raise ProfundidadError(
            "El campo de alturas no produjo ninguna variación; revisa borde_mm, realce_mm y "
            "grosor_mm en profundidad.toml."
        )
    factor = alto_disponible_mm / pico
    alto = borde_mm + contrib * factor

    # Fuera de la figura (en el margen que se usa para la pared), se extiende por
    # vecino más cercano: así la pared no cae a 0 donde el contorno final (algo
    # más amplio que la figura estricta) la recorte.
    _, (iy, ix) = ndimage.distance_transform_edt(~figura_f, return_indices=True)
    alto_extendido = np.where(figura_f, alto, alto[iy, ix])
    alto_extendido = np.clip(alto_extendido, borde_mm, grosor_mm)

    # Se suma aquí la base sacrificable (en vez de extruirla por separado y
    # unirla al final): así toda la pieza sale de UNA sola intersección 3D con
    # el prisma del contorno, sin una segunda unión que podría dejar un pliegue
    # de grosor cero en la costura entre ambos sólidos.
    alto_con_base = alto_extendido + CORTE_SACRIFICABLE_MM

    # Región del campo de alturas: bastante más ancha que la figura ensanchada,
    # para que la pared nunca se corte contra una zona sin datos.
    campo = ndimage.binary_dilation(figura_ancha, iterations=_MARGEN_CAMPO_PX)
    malla_campo = _malla_campo_alturas(campo, alto_con_base, mm_por_px)

    # Contorno final: la máscara ensanchada, trazada como polígono suavizado
    # (no por celdas de píxel).
    contorno_cs = _trazar_contorno(figura_ancha, mm_por_px)
    if contorno_cs is None:
        raise ProfundidadError("No se pudo trazar el contorno de la figura.")

    solido_campo = m3d.Manifold(
        m3d.Mesh(
            vert_properties=malla_campo.vertices.astype(np.float32),
            tri_verts=malla_campo.faces.astype(np.uint32),
        )
    )
    if solido_campo.status() != m3d.Error.NoError:
        raise ProfundidadError(
            f"El campo de alturas no es una malla válida para manifold3d ({solido_campo.status()})."
        )

    # El prisma del contorno cubre de sobra el rango de alturas del campo: solo
    # recorta la silueta en planta (la "pared"), nunca limita el alto.
    margen_z = max(grosor_mm, 1.0)
    altura_prisma = grosor_mm + CORTE_SACRIFICABLE_MM + 2 * margen_z
    prisma = contorno_cs.extrude(altura_prisma).translate((0.0, 0.0, -margen_z))

    solido = m3d.Manifold.batch_boolean([solido_campo, prisma], m3d.OpType.Intersect)
    if solido.is_empty():
        raise ProfundidadError(
            "La intersección del campo de alturas con el contorno no produjo geometría."
        )

    malla_m3d = solido.to_mesh()
    vertices = np.array(malla_m3d.vert_properties)[:, :3]
    caras = np.array(malla_m3d.tri_verts)
    malla = trimesh.Trimesh(vertices=vertices, faces=caras, process=False)

    centro_xy = (malla.bounds[0][:2] + malla.bounds[1][:2]) / 2.0
    malla.apply_translation([-centro_xy[0], -centro_xy[1], 0.0])

    destino_stl = Path(destino_stl)
    malla.export(destino_stl, file_type="stl")

    recargada = trimesh.load(destino_stl, force="mesh")
    if not recargada.is_watertight:
        raise ProfundidadError(
            "La malla de profundidad no quedó cerrada (watertight) al recargarla; ajusta "
            "suavizado, ancho_min_mm o engrose_mm en profundidad.toml y vuelve a generar."
        )

    parametros_registro: dict[str, Any] = {
        "borde_mm": borde_mm,
        "realce_mm": realce_mm,
        "suavizado": suavizado,
        "ancho_min_mm": ancho_min_mm,
        "engrose_mm": engrose_mm,
        "mm_por_px": mm_por_px,
        "largo_mm": float(largo_mm),
        "grosor_mm": float(grosor_mm),
    }
    return {"parametros_registro": parametros_registro}
