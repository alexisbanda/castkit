"""Lógica pura del proveedor «relieve»: bajorrelieve por capas de color (#43).

Convierte un diseño plano de colores definidos (logo, escudo, medalla,
aplique...) en un bajorrelieve de espalda plana: cada color pasa a ser un
nivel de altura, con aristas nítidas y una malla cerrada por construcción
(sin IA ni servicios externos). Este módulo no conoce la CLI ni el sistema
de versiones de castkit: solo trabaja con imágenes, un archivo
``relieve.toml`` y las medidas (``largo_mm``, ``grosor_mm``) que le pase
quien lo use (ver :class:`castkit.generacion.ProveedorRelieve`).

Pasos (ver también el prototipo de referencia y sus lecciones):
1. Separar el fondo (el color mayoritario en el borde de la imagen, relleno
   por inundación desde el borde); el mismo color encerrado dentro de la
   figura no es fondo.
2. Reducir la imagen a colores planos (``Image.quantize(method=MEDIANCUT)``,
   determinista) y proponer una altura relativa por color: el de mayor área
   es la base (``base_mm``); los demás, de más a menos área, suben
   ``paso_mm`` cada uno. Esto se escribe en ``relieve.toml`` para que el
   operador lo ajuste.
3. Al generar la malla, cada píxel se reclasifica al color más cercano de
   ``relieve.toml`` (no a la paleta cuantizada original: el operador pudo
   editar el archivo). Las piezas sueltas (sin placa) quedan limitadas a la
   altura base.
4. Cada nivel de altura se traza con ``skimage.measure.find_contours`` y se
   convierte en un ``manifold3d.CrossSection`` (regla par-impar) con un
   cierre morfológico (offset +ε/−ε) que funde contactos de un solo punto, y
   se descartan las manchas menores que ``area_min_mm2``. Los niveles forman
   terrazas apoyadas (cada uno sobre el anterior) contraídas ``escalon_mm·k``
   respecto al de abajo, para que no queden paredes coincidentes.
5. Las alturas de ``relieve.toml`` son relativas: se reescalan para que la
   más alta coincida con ``grosor_mm`` (menos la placa opcional). Se añaden
   0,5 mm de base sacrificable que `castkit limpieza` recorta en un molde
   abierto (#55), dejando la espalda plana y el grosor exacto.
6. Antes de devolver el resultado, la malla exportada se recarga con
   `trimesh` (igual que el resto de castkit) y se comprueba que sigue
   cerrada: un STL en float32 con aristas diminutas o paredes coincidentes
   puede romperse al recargarlo, y conviene detectarlo aquí, no más adelante.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image
from scipy import ndimage
from skimage import measure

NOMBRE_TOML = "relieve.toml"

# Milímetros de base sacrificable que se añaden bajo todo el relieve; un
# molde abierto (#55) la recorta al dejar la espalda plana.
CORTE_SACRIFICABLE_MM = 0.5

# Offset (mm) del cierre morfológico que funde contactos de un solo punto
# entre regiones vecinas del mismo nivel.
_CIERRE_MM = 0.03


class RelieveError(Exception):
    """Se lanza cuando la lógica pura de «relieve» no puede completarse.

    El mensaje siempre está en español. No depende de la jerarquía de
    excepciones de castkit (este módulo es lógica pura, sin CLI); quien lo
    use (por ejemplo, :class:`castkit.generacion.ProveedorRelieve`) la
    traduce a :class:`castkit.generacion.GeneracionError`.
    """


@dataclass
class ConfigRelieve:
    """Valores por defecto de la sección ``[relieve]`` de `config/fabricacion.toml`."""

    colores_max: int = 8
    base_mm: float = 2.0
    paso_mm: float = 0.4
    escalon_mm: float = 0.05
    area_min_mm2: float = 0.5
    placa_mm: float = 0.0
    placa_margen_mm: float = 1.0


def _cargar_imagen_rgb(ruta_imagen: Path) -> np.ndarray:
    """Abre ``ruta_imagen`` como RGB (sin transparencia) y la devuelve como `uint8`."""
    ruta_imagen = Path(ruta_imagen)
    try:
        imagen = Image.open(ruta_imagen).convert("RGB")
    except Exception as exc:
        raise RelieveError(f'No se pudo abrir la imagen "{ruta_imagen.name}": {exc}') from exc
    return np.asarray(imagen, dtype=np.uint8)


def _rgb_a_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(c) & 0xFF for c in rgb))


def _hex_a_rgb(valor_hex: str) -> tuple[int, int, int]:
    limpio = valor_hex.strip().lstrip("#")
    if len(limpio) != 6:
        raise RelieveError(f'El color "{valor_hex}" de relieve.toml no es un hex válido (#rrggbb).')
    try:
        return (int(limpio[0:2], 16), int(limpio[2:4], 16), int(limpio[4:6], 16))
    except ValueError as exc:
        raise RelieveError(
            f'El color "{valor_hex}" de relieve.toml no es un hex válido (#rrggbb).'
        ) from exc


def _cuantizar(rgb: np.ndarray, colores_max: int) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Reduce ``rgb`` a ``colores_max`` colores planos con `Image.quantize(MEDIANCUT)`.

    Determinista (a diferencia de los métodos basados en octree/k-means).
    Devuelve los índices de color por píxel y la paleta efectiva (una
    entrada RGB por índice realmente usado).
    """
    imagen = Image.fromarray(rgb, mode="RGB")
    imagen_q = imagen.quantize(colors=max(2, int(colores_max)), method=Image.MEDIANCUT)
    indices = np.asarray(imagen_q, dtype=np.int64)
    paleta_plana = imagen_q.getpalette() or []
    n_colores = int(indices.max()) + 1
    paleta: list[tuple[int, int, int]] = []
    for i in range(n_colores):
        base = i * 3
        canal = paleta_plana[base : base + 3]
        if len(canal) < 3:
            canal = [0, 0, 0]
        paleta.append((int(canal[0]), int(canal[1]), int(canal[2])))
    return indices, paleta


def _separar_fondo_por_indice(indices: np.ndarray) -> tuple[np.ndarray, int]:
    """Separa el fondo (color mayoritario en el borde, relleno desde el borde).

    El mismo color encerrado dentro de la figura (sin conexión con el borde)
    no cuenta como fondo. Devuelve ``(mascara_fondo, idx_fondo)``.
    """
    borde = np.concatenate([indices[0], indices[-1], indices[:, 0], indices[:, -1]])
    valores, conteos = np.unique(borde, return_counts=True)
    idx_fondo = int(valores[np.argmax(conteos)])

    es_color_fondo = indices == idx_fondo
    etiquetas, _ = ndimage.label(es_color_fondo)
    ids_borde = np.unique(
        np.concatenate([etiquetas[0], etiquetas[-1], etiquetas[:, 0], etiquetas[:, -1]])
    )
    ids_borde = ids_borde[ids_borde > 0]
    fondo = np.isin(etiquetas, ids_borde)
    return fondo, idx_fondo


def proponer_relieve(ruta_imagen: Path, config: ConfigRelieve) -> dict[str, Any]:
    """Analiza ``ruta_imagen`` y propone la estructura para escribir ``relieve.toml``.

    Separa el fondo, reduce la figura a colores planos (``config.colores_max``)
    y propone una altura relativa por color: el de mayor área (dentro de la
    figura, sin el fondo) es la base (``config.base_mm``); los demás, de más
    a menos área, suben ``config.paso_mm`` cada uno.
    """
    rgb = _cargar_imagen_rgb(ruta_imagen)
    indices, paleta = _cuantizar(rgb, config.colores_max)
    fondo, idx_fondo = _separar_fondo_por_indice(indices)
    figura = ~fondo

    if not figura.any():
        raise RelieveError(
            f'La imagen "{Path(ruta_imagen).name}" no tiene ninguna figura: todo el color '
            "detectado conecta con el borde (es fondo)."
        )

    # No se excluye `idx_fondo`: un color igual al del fondo pero encerrado dentro de la
    # figura (sin conexión con el borde) ya quedó fuera de `fondo`, y sigue siendo un color
    # válido de la figura (por ejemplo, la "N" blanca de un escudo con fondo blanco).
    valores_figura = [int(v) for v in np.unique(indices[figura])]
    areas = [
        (idx_color, int(np.count_nonzero((indices == idx_color) & figura)))
        for idx_color in valores_figura
    ]
    areas.sort(key=lambda par: -par[1])

    colores = [
        {
            "hex": _rgb_a_hex(paleta[idx_color]),
            "altura_mm": round(config.base_mm + rango * config.paso_mm, 3),
        }
        for rango, (idx_color, _area) in enumerate(areas)
    ]

    return {
        "colores": colores,
        "placa_mm": float(config.placa_mm),
        "placa_margen_mm": float(config.placa_margen_mm),
        "escalon_mm": float(config.escalon_mm),
        "area_min_mm2": float(config.area_min_mm2),
    }


def _formatear_numero(valor: float) -> str:
    """Formatea un número para `relieve.toml` sin ceros ni punto decimal de más."""
    texto = f"{float(valor):.6f}".rstrip("0").rstrip(".")
    return texto if texto and texto != "-" else "0"


def escribir_relieve_toml(ruta_toml: Path, datos: dict[str, Any]) -> None:
    """Escribe ``datos`` (ver :func:`proponer_relieve`) como ``relieve.toml``."""
    lineas = [
        "# brief/relieve.toml — alturas del bajorrelieve por color, propuestas por castkit.",
        "#",
        '# Edita "altura_mm" de cada color (o pon la misma altura a dos colores para',
        "# fundirlos en un nivel) y vuelve a generar con --reemplazar. Estas alturas son",
        "# RELATIVAS entre sí: castkit las reescala para que la más alta coincida con el",
        "# grosor_mm del diseño (es un molde abierto, ver «castkit version --grosor-mm»).",
        "# El color con más área de la figura es la base del relieve; los demás se apoyan",
        "# encima, de más a menos área.",
        "",
        f"placa_mm = {_formatear_numero(datos['placa_mm'])}",
        "# Grosor (mm) de la placa de apoyo. 0 = sin placa: las piezas sueltas (islas) se",
        "# aceptan como piezas separadas y su altura se limita al nivel más bajo.",
        f"placa_margen_mm = {_formatear_numero(datos['placa_margen_mm'])}",
        "# Margen (mm) de la placa alrededor de la silueta conjunta del diseño (si placa_mm > 0).",
        f"escalon_mm = {_formatear_numero(datos['escalon_mm'])}",
        "# Cuánto se contrae cada nivel respecto al de abajo, para que no queden paredes",
        "# coincidentes entre niveles.",
        f"area_min_mm2 = {_formatear_numero(datos['area_min_mm2'])}",
        "# Manchas (del antialias o del ruido de la imagen) más pequeñas que esto se descartan.",
        "",
    ]
    for color in datos["colores"]:
        lineas.append("[[color]]")
        lineas.append(f'hex = "{color["hex"]}"')
        lineas.append(f"altura_mm = {_formatear_numero(color['altura_mm'])}")
        lineas.append("")

    contenido = "\n".join(lineas).rstrip("\n") + "\n"
    Path(ruta_toml).write_text(contenido, encoding="utf-8")


def leer_relieve_toml(ruta_toml: Path) -> dict[str, Any]:
    """Lee un ``relieve.toml`` existente; devuelve la misma forma que :func:`proponer_relieve`."""
    ruta_toml = Path(ruta_toml)
    try:
        with ruta_toml.open("rb") as flujo:
            datos = tomllib.load(flujo)
    except tomllib.TOMLDecodeError as exc:
        raise RelieveError(f'El archivo "{ruta_toml}" no es un TOML válido: {exc}') from exc

    colores_raw = datos.get("color", [])
    if not colores_raw:
        raise RelieveError(f'El archivo "{ruta_toml}" no tiene ninguna tabla [[color]].')

    colores: list[dict[str, Any]] = []
    for entrada in colores_raw:
        hex_valor = entrada.get("hex")
        altura = entrada.get("altura_mm")
        if not isinstance(hex_valor, str) or not isinstance(altura, (int, float)):
            raise RelieveError(
                f'Cada [[color]] de "{ruta_toml}" necesita "hex" (texto) y "altura_mm" (número).'
            )
        colores.append({"hex": hex_valor, "altura_mm": float(altura)})

    return {
        "colores": colores,
        "placa_mm": float(datos.get("placa_mm", 0.0)),
        "placa_margen_mm": float(datos.get("placa_margen_mm", 1.0)),
        "escalon_mm": float(datos.get("escalon_mm", 0.05)),
        "area_min_mm2": float(datos.get("area_min_mm2", 0.5)),
    }


def _seccion_mm(mascara: np.ndarray, mm_por_px: float, area_min_mm2: float) -> Any | None:
    """Convierte una máscara booleana en un `manifold3d.CrossSection` en milímetros.

    Traza los contornos con `skimage.measure.find_contours`, aplica un cierre
    morfológico (offset +ε/−ε) que funde contactos de un solo punto y
    descarta las manchas de área menor que ``area_min_mm2``. Devuelve
    ``None`` si no queda ninguna región.
    """
    import manifold3d as m3d

    if not mascara.any():
        return None

    relleno = np.pad(mascara, 2).astype(np.float64)
    alto_relleno = relleno.shape[0]
    polis = []
    for contorno in measure.find_contours(relleno, 0.5):
        if len(contorno) < 4:
            continue
        xy = np.column_stack(
            [
                (contorno[:, 1] - 2) * mm_por_px,
                (alto_relleno - (contorno[:, 0] - 2)) * mm_por_px,
            ]
        )
        polis.append(xy)
    if not polis:
        return None

    cs = m3d.CrossSection(polis, fillrule=m3d.FillRule.EvenOdd)
    cs = cs.offset(_CIERRE_MM, m3d.JoinType.Miter).offset(-_CIERRE_MM, m3d.JoinType.Miter)
    cs = cs.simplify(_CIERRE_MM)
    partes = [parte for parte in cs.decompose() if parte.area() >= area_min_mm2]
    if not partes:
        return None
    return m3d.CrossSection.batch_boolean(partes, m3d.OpType.Add)


def generar_malla_relieve(
    ruta_imagen: Path,
    datos_relieve: dict[str, Any],
    *,
    largo_mm: float,
    grosor_mm: float,
    destino_stl: Path,
) -> dict[str, Any]:
    """Construye el bajorrelieve y lo exporta a ``destino_stl``.

    ``largo_mm`` es la medida mayor en planta del diseño (ver #55: molde
    abierto) y ``grosor_mm`` es el grosor final que debe quedar tras
    `castkit limpieza`. La malla en bruto que este proveedor escribe mide
    ``grosor_mm + CORTE_SACRIFICABLE_MM`` de alto: los últimos
    ``CORTE_SACRIFICABLE_MM`` (0,5 mm) de abajo son una base sacrificable
    que `castkit limpieza` recorta (molde abierto, #55), dejando la espalda
    plana y el grosor exacto. Devuelve ``{"parametros_registro": {...}}`` con
    la tabla color→altura usada (las relativas de ``relieve.toml``) y los
    demás parámetros.
    """
    import manifold3d as m3d

    if not math.isfinite(largo_mm) or largo_mm <= 0:
        raise RelieveError(f'"largo_mm" debe ser mayor que 0; recibido "{largo_mm}".')
    if not math.isfinite(grosor_mm) or grosor_mm <= 0:
        raise RelieveError(f'"grosor_mm" debe ser mayor que 0; recibido "{grosor_mm}".')

    colores = datos_relieve.get("colores") or []
    if not colores:
        raise RelieveError(
            f'"{Path(ruta_imagen).name}" no tiene colores definidos en relieve.toml.'
        )

    placa_mm = float(datos_relieve.get("placa_mm", 0.0))
    placa_margen_mm = float(datos_relieve.get("placa_margen_mm", 1.0))
    escalon_mm = float(datos_relieve.get("escalon_mm", 0.05))
    area_min_mm2 = float(datos_relieve.get("area_min_mm2", 0.5))

    if placa_mm < 0:
        raise RelieveError(f'"placa_mm" no puede ser negativo; recibido "{placa_mm}".')

    alturas_relativas = np.array([float(c["altura_mm"]) for c in colores], dtype=np.float64)
    if np.any(alturas_relativas <= 0):
        raise RelieveError(
            'Todas las "altura_mm" de relieve.toml deben ser mayores que 0 (colores sin altura '
            "válida)."
        )

    alto_disponible_mm = grosor_mm - placa_mm
    if alto_disponible_mm <= 0:
        raise RelieveError(
            f'"grosor_mm" ({grosor_mm} mm) no deja espacio para el relieve una vez restada la '
            f"placa (placa_mm={placa_mm} mm)."
        )

    rgb = _cargar_imagen_rgb(ruta_imagen)
    alto_px, ancho_px, _ = rgb.shape

    paleta_rgb = np.array([_hex_a_rgb(c["hex"]) for c in colores], dtype=np.float32)
    distancias = np.linalg.norm(
        rgb[..., None, :].astype(np.float32) - paleta_rgb[None, None, :, :], axis=-1
    )
    clase = distancias.argmin(axis=-1)

    borde = np.concatenate([clase[0], clase[-1], clase[:, 0], clase[:, -1]])
    valores, conteos = np.unique(borde, return_counts=True)
    idx_fondo = int(valores[np.argmax(conteos)])
    es_clase_fondo = clase == idx_fondo
    etiquetas_fondo, _ = ndimage.label(es_clase_fondo)
    ids_borde = np.unique(
        np.concatenate(
            [etiquetas_fondo[0], etiquetas_fondo[-1], etiquetas_fondo[:, 0], etiquetas_fondo[:, -1]]
        )
    )
    ids_borde = ids_borde[ids_borde > 0]
    fondo = np.isin(etiquetas_fondo, ids_borde)
    figura = ~fondo

    if not figura.any():
        raise RelieveError(
            f'La imagen "{Path(ruta_imagen).name}" no tiene ninguna figura: todo el color '
            "coincide con el fondo detectado en el borde."
        )

    alturas_px = np.zeros((alto_px, ancho_px), dtype=np.float64)
    for idx_color in range(len(colores)):
        alturas_px[(clase == idx_color) & figura] = alturas_relativas[idx_color]

    altura_base_relativa = float(alturas_relativas.min())

    if placa_mm <= 0:
        etiquetas_piezas, n_piezas = ndimage.label(figura)
        if n_piezas > 1:
            tamanos = ndimage.sum(figura, etiquetas_piezas, range(1, n_piezas + 1))
            pieza_principal = int(np.argmax(tamanos)) + 1
            for pieza in range(1, n_piezas + 1):
                if pieza == pieza_principal:
                    continue
                alturas_px[etiquetas_piezas == pieza] = altura_base_relativa

    ys_fig, xs_fig = np.where(figura)
    alto_figura_px = int(ys_fig.max() - ys_fig.min()) + 1
    ancho_figura_px = int(xs_fig.max() - xs_fig.min()) + 1
    extension_px = float(max(alto_figura_px, ancho_figura_px))
    if extension_px <= 0:
        raise RelieveError("La figura detectada no tiene extensión; no se puede escalar en planta.")
    mm_por_px = largo_mm / extension_px

    niveles = sorted({float(v) for v in np.unique(alturas_px[figura])})

    # Primera pasada: construye la sección de cada nivel (con su cierre
    # morfológico y, desde el segundo nivel, la contracción `escalon_mm·k`)
    # SIN escalar alturas todavía. Algunos niveles pueden no sobrevivir
    # (manchas menores que `area_min_mm2`, o que la contracción deja vacías):
    # `pico_relativo` se calcula sobre el nivel más alto que SÍ sobrevive,
    # no sobre el máximo nominal de `relieve.toml` (si se usara el nominal,
    # un color alto descartado comprimiría todo lo demás por debajo de
    # `grosor_mm`).
    secciones: list[tuple[float, Any]] = []
    for k, h in enumerate(niveles):
        mascara_nivel = alturas_px >= (h - 1e-9)
        cs = _seccion_mm(mascara_nivel, mm_por_px, area_min_mm2)
        if cs is not None and k > 0:
            contraccion = escalon_mm * k
            cs = cs.offset(-contraccion, m3d.JoinType.Miter).simplify(_CIERRE_MM)
            partes = [parte for parte in cs.decompose() if parte.area() >= area_min_mm2]
            cs = m3d.CrossSection.batch_boolean(partes, m3d.OpType.Add) if partes else None
        secciones.append((h, cs if (cs is not None and not cs.is_empty()) else None))

    niveles_construidos = [h for h, cs in secciones if cs is not None]
    if not niveles_construidos:
        raise RelieveError(
            "El relieve no produjo ninguna geometría visible; revisa colores_max y "
            "area_min_mm2 en relieve.toml (puede que todas las regiones se hayan descartado)."
        )
    pico_relativo = max(niveles_construidos)
    factor_altura = alto_disponible_mm / pico_relativo

    # Avisa (sin fallar) de qué colores de relieve.toml no llegaron a
    # construirse, para que el operador no los vea desaparecer en silencio.
    colores_sin_construir: list[dict[str, Any]] = []
    for idx_color, color in enumerate(colores):
        valores_color = np.unique(alturas_px[(clase == idx_color) & figura])
        if valores_color.size == 0:
            continue  # el color no tiene ningún píxel en la figura (p. ej. solo en el fondo).
        altura_efectiva = float(valores_color.max())
        if not any(math.isclose(altura_efectiva, h, abs_tol=1e-6) for h in niveles_construidos):
            colores_sin_construir.append(dict(color))

    # Cada capa debe apoyar en la cima del último nivel CONSTRUIDO (o en z=0),
    # nunca en la de un nivel descartado: si se actualizara `z_prev_rel` aunque
    # `cs` sea None, un nivel intermedio descartado dejaría un hueco entre la
    # capa de abajo y la siguiente que sí se construye (malla no conexa o con
    # un hueco flotante).
    solido_relieve = None
    z_prev_rel = 0.0
    for h, cs in secciones:
        if cs is not None:
            z0_mm = z_prev_rel * factor_altura
            z1_mm = h * factor_altura
            capa = cs.extrude(z1_mm - z0_mm).translate((0.0, 0.0, z0_mm))
            solido_relieve = capa if solido_relieve is None else solido_relieve + capa
            z_prev_rel = h

    if placa_mm > 0:
        margen_px = max(placa_margen_mm / mm_por_px, 0.0)
        iteraciones = max(1, int(math.ceil(margen_px)))
        silueta = ndimage.binary_dilation(figura, iterations=iteraciones)
        cs_base = _seccion_mm(silueta, mm_por_px, area_min_mm2)
        if cs_base is None:
            raise RelieveError("No se pudo construir la placa de apoyo del relieve.")
        espesor_base = CORTE_SACRIFICABLE_MM + placa_mm
    else:
        cs_base = _seccion_mm(figura, mm_por_px, area_min_mm2)
        if cs_base is None:
            raise RelieveError("No se pudo construir la base sacrificable del relieve.")
        espesor_base = CORTE_SACRIFICABLE_MM

    base = cs_base.extrude(espesor_base)
    solido = base + solido_relieve.translate((0.0, 0.0, espesor_base))

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
        raise RelieveError(
            "La malla del relieve no quedó cerrada (watertight) al recargarla; ajusta "
            "colores_max, area_min_mm2 o escalon_mm en relieve.toml y vuelve a generar."
        )

    parametros_registro: dict[str, Any] = {
        "color_altura_mm": [dict(c) for c in colores],
        "placa_mm": placa_mm,
        "placa_margen_mm": placa_margen_mm,
        "escalon_mm": escalon_mm,
        "area_min_mm2": area_min_mm2,
        "largo_mm": float(largo_mm),
        "grosor_mm": float(grosor_mm),
        "mm_por_px": mm_por_px,
        "colores_sin_construir": colores_sin_construir,
    }
    return {"parametros_registro": parametros_registro}
