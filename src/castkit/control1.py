"""Render y gestión del Punto de control 1 (`castkit control1`).

A partir de la malla limpia de la versión actual (`work/<diseño>_v<N>_limpia.stl`),
genera cuatro imágenes PNG en `output/` sin abrir interfaz gráfica:
- `<diseño>_v<N>_render-frente.png`
- `<diseño>_v<N>_render-perfil.png`
- `<diseño>_v<N>_render-arriba.png`
- `<diseño>_v<N>_render-3-4.png`

Cada imagen muestra sobreimpresas de forma legible la vista y las medidas
finales reales de alto, ancho y fondo en mm, además de guardar el texto en los
metadatos PNG (`castkit_medidas` y `castkit_vista`).

También registra la decisión del operador (`aprobado`, `rechazado` con motivo
obligatorio, o `pendiente`) con fecha en `versiones.v<N>.parametros.control_1`,
y bloquea el paso a `castkit matriz` mientras la versión no esté aprobada.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from castkit.blender import BlenderNotFoundError, find_blender, run_blender_script
from castkit.versiones import (
    VersionError,
    cargar_diseno,
    datos_version,
    guardar_diseno,
    nombre_archivo,
    registrar_parametros,
)

matplotlib.use("Agg")

VISTAS_CONTROL1: tuple[str, ...] = ("frente", "perfil", "arriba", "3-4")
DECISIONES_CONTROL1: tuple[str, ...] = ("aprobado", "rechazado", "pendiente")

_SCRIPT_RENDER_BLENDER = Path(__file__).parent / "blender_scripts" / "render_vistas.py"

# Orientaciones de cámara (elev, azim) en matplotlib para cada vista:
# - frente: mirando desde -Y hacia +Y con +Z arriba
# - perfil: mirando desde +X hacia -X con +Z arriba
# - arriba: mirando desde +Z hacia -Z (planta)
# - 3-4: vista tres cuartos desde (+X, -Y, +Z) con +Z arriba
_ANGULOS_MATPLOTLIB: dict[str, tuple[float, float]] = {
    "frente": (0.0, -90.0),
    "perfil": (0.0, 0.0),
    "arriba": (90.0, -90.0),
    "3-4": (25.0, -45.0),
}

# Tinte base sutil por vista para diferenciar inequívocamente la orientación
# incluso en geometrías perfectamente simétricas como un cubo.
_TINTE_VISTA: dict[str, tuple[float, float, float]] = {
    "frente": (0.86, 0.90, 0.96),
    "perfil": (0.94, 0.89, 0.84),
    "arriba": (0.86, 0.95, 0.88),
    "3-4": (0.91, 0.88, 0.95),
}


class Control1Error(VersionError):
    """Se lanza cuando falla el render o la validación del Punto de control 1.

    El mensaje siempre está en español. Hereda de :class:`VersionError` (y por
    lo tanto de :class:`castkit.diseno.AltaError`).
    """


def _localizar_malla_limpia(carpeta: Path, datos: dict[str, Any]) -> Path:
    """Localiza `work/<diseño>_v<actual>_limpia.stl` o lanza :class:`Control1Error`."""
    nombre = datos["nombre"]
    actual = datos["version_actual"]
    ruta = carpeta / "work" / nombre_archivo(nombre, actual, "limpia", "stl")
    if not ruta.is_file():
        raise Control1Error(
            f'La versión v{actual} de «{nombre}» no tiene malla limpia en "{ruta}": '
            'ejecuta primero "castkit limpieza".'
        )
    return ruta


def medir_malla_mm(malla: trimesh.Trimesh) -> dict[str, float]:
    """Devuelve `alto_mm`, `ancho_mm` y `fondo_mm` del bounding box de ``malla``."""
    min_xyz, max_xyz = malla.bounds
    ancho_mm = round(float(max_xyz[0] - min_xyz[0]), 2)
    fondo_mm = round(float(max_xyz[1] - min_xyz[1]), 2)
    alto_mm = round(float(max_xyz[2] - min_xyz[2]), 2)
    return {
        "alto_mm": alto_mm,
        "ancho_mm": ancho_mm,
        "fondo_mm": fondo_mm,
    }


def formatear_leyenda_medidas(
    nombre: str,
    version: int,
    vista: str,
    dimensiones_mm: dict[str, float],
) -> str:
    """Construye el texto en español con la vista y las medidas de alto, ancho y fondo."""
    alto = dimensiones_mm["alto_mm"]
    ancho = dimensiones_mm["ancho_mm"]
    fondo = dimensiones_mm["fondo_mm"]
    return (
        f"{nombre} v{version} — Vista: {vista} | "
        f"Alto: {alto:.1f} mm · Ancho: {ancho:.1f} mm · Fondo: {fondo:.1f} mm"
    )


def _cargar_fuente(tamano: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=tamano)
    except TypeError:  # pragma: no cover
        return ImageFont.load_default()


def _sobreimprimir_medidas_png(
    ruta_png: Path,
    *,
    nombre: str,
    version: int,
    vista: str,
    dimensiones_mm: dict[str, float],
) -> str:
    """Sobreimprime en ``ruta_png`` el encabezado y pie con vista y medidas en mm."""
    with Image.open(ruta_png) as img_orig:
        img = img_orig.convert("RGB")
        if img.size != (600, 600):
            img = img.resize((600, 600), Image.Resampling.LANCZOS)

    draw = ImageDraw.Draw(img)
    ancho_img, alto_img = img.size

    # Franja superior e inferior de alto contraste para lectura inmediata.
    alto_encabezado = 46
    alto_pie = 58
    draw.rectangle([(0, 0), (ancho_img, alto_encabezado)], fill=(20, 24, 33))
    draw.line([(0, alto_encabezado), (ancho_img, alto_encabezado)], fill=(90, 140, 220), width=2)

    y_pie = alto_img - alto_pie
    draw.rectangle([(0, y_pie), (ancho_img, alto_img)], fill=(20, 24, 33))
    draw.line([(0, y_pie), (ancho_img, y_pie)], fill=(90, 140, 220), width=2)

    fuente_titulo = _cargar_fuente(17)
    fuente_medidas = _cargar_fuente(16)

    alto = dimensiones_mm["alto_mm"]
    ancho = dimensiones_mm["ancho_mm"]
    fondo = dimensiones_mm["fondo_mm"]

    texto_superior = f"{nombre} (v{version}) — Vista: {vista.upper()}"
    texto_inferior = f"Alto: {alto:.1f} mm   |   Ancho: {ancho:.1f} mm   |   Fondo: {fondo:.1f} mm"
    leyenda_completa = formatear_leyenda_medidas(nombre, version, vista, dimensiones_mm)

    draw.text((16, 13), texto_superior, fill=(255, 255, 255), font=fuente_titulo)
    draw.text((16, y_pie + 19), texto_inferior, fill=(255, 245, 190), font=fuente_medidas)

    metadata = PngInfo()
    metadata.add_text("castkit_medidas", leyenda_completa)
    metadata.add_text("castkit_vista", vista)
    metadata.add_text("castkit_alto_mm", f"{alto:.2f}")
    metadata.add_text("castkit_ancho_mm", f"{ancho:.2f}")
    metadata.add_text("castkit_fondo_mm", f"{fondo:.2f}")

    img.save(ruta_png, format="PNG", pnginfo=metadata)
    return leyenda_completa


def _renderizar_con_matplotlib(
    malla: trimesh.Trimesh,
    rutas_vistas: dict[str, Path],
) -> None:
    """Renderiza las 4 vistas de ``malla`` en PNG (600x600) usando matplotlib Agg."""
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    copia = malla.copy()
    # Si la malla tiene muchas caras, simplificar solo la vista previa para que
    # el render por software sea rápido sin alterar la malla limpia en disco.
    if len(copia.faces) > 6000:
        copia = copia.simplify_quadric_decimation(face_count=6000)

    min_xyz, max_xyz = copia.bounds
    centro = (min_xyz + max_xyz) / 2.0
    vertices = copia.vertices - centro
    triangulos = vertices[copia.faces]

    normales = np.asarray(copia.face_normals, dtype=np.float64)
    luz_dir = np.array([0.38, -0.58, 0.72], dtype=np.float64)
    luz_dir /= np.linalg.norm(luz_dir)
    intensidad = np.clip(normales @ luz_dir, -1.0, 1.0)
    brillo = 0.38 + 0.52 * (0.5 * (intensidad + 1.0))

    max_ext = float(np.max(max_xyz - min_xyz))
    semirango = max(max_ext * 0.68, 1.0)

    for vista, ruta_png in rutas_vistas.items():
        elev, azim = _ANGULOS_MATPLOTLIB[vista]
        tinte = np.array(_TINTE_VISTA[vista], dtype=np.float64)
        colores_caras = np.clip(brillo[:, None] * tinte[None, :], 0.12, 0.98)

        fig = plt.figure(figsize=(6.0, 6.0), dpi=100, facecolor="#242933")
        ax = fig.add_subplot(111, projection="3d", facecolor="#242933")
        ax.set_proj_type("ortho")

        coleccion = Poly3DCollection(
            triangulos,
            facecolors=colores_caras,
            edgecolors=(0.15, 0.18, 0.22, 0.28),
            linewidths=0.35,
        )
        ax.add_collection3d(coleccion)

        ax.set_xlim(-semirango, semirango)
        ax.set_ylim(-semirango, semirango)
        ax.set_zlim(-semirango, semirango)
        ax.set_box_aspect((1.0, 1.0, 1.0))
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        fig.subplots_adjust(left=0.0, right=1.0, bottom=0.08, top=0.92)

        fig.savefig(ruta_png, format="png", dpi=100, facecolor=fig.get_facecolor())
        plt.close(fig)


def _registrar_control1_en_diseno(carpeta: Path, registro: dict[str, Any]) -> None:
    """Registra ``registro`` en ``versiones.v<actual>.parametros.control_1``."""
    registrar_parametros(carpeta, "control-1", registro)
    datos = cargar_diseno(carpeta)
    actual = datos["version_actual"]
    clave = f"v{actual}"
    parametros = datos["versiones"][clave].setdefault("parametros", {})
    parametros["control_1"] = registro
    guardar_diseno(carpeta, datos)


def validar_decision_control1(
    decision: str | None,
    motivo: str | None,
) -> tuple[str, str | None]:
    """Valida ``decision`` y ``motivo`` y devuelve ``(estado, motivo_limpio)``."""
    if decision is None or decision == "pendiente":
        return "pendiente", None

    if decision not in ("aprobado", "rechazado"):
        raise Control1Error(
            f'La decisión de control 1 debe ser "aprobado" o "rechazado"; recibido "{decision}".'
        )

    if decision == "aprobado":
        return "aprobado", None

    motivo_limpio = motivo.strip() if isinstance(motivo, str) else ""
    if not motivo_limpio:
        raise Control1Error(
            "Para rechazar el punto de control 1 debes indicar un --motivo explicando qué corregir."
        )
    return "rechazado", motivo_limpio


def registrar_decision_control1(
    carpeta: Path,
    *,
    decision: str,
    motivo: str | None = None,
) -> dict[str, Any]:
    """Actualiza la decisión (`aprobado` o `rechazado`) sobre un `control_1` ya renderizado."""
    estado, motivo_limpio = validar_decision_control1(decision, motivo)
    carpeta = Path(carpeta)
    datos = cargar_diseno(carpeta)
    actual = datos["version_actual"]
    info_v = datos_version(datos, actual)
    previo = info_v["parametros"].get("control_1") or info_v["parametros"].get("control-1") or {}

    registro: dict[str, Any] = {
        **previo,
        "estado": estado,
        "fecha": date.today().isoformat(),
        "motivo": motivo_limpio,
    }
    _registrar_control1_en_diseno(carpeta, registro)
    return registro


def ejecutar_control1(
    carpeta: Path,
    *,
    decision: str | None = None,
    motivo: str | None = None,
    sin_blender: bool = False,
) -> dict[str, Any]:
    """Genera los 4 PNG del Punto de control 1 y registra el estado en `diseno.json`."""
    estado, motivo_limpio = validar_decision_control1(decision, motivo)

    carpeta = Path(carpeta)
    datos = cargar_diseno(carpeta)
    nombre = datos["nombre"]
    actual = datos["version_actual"]

    ruta_limpia = _localizar_malla_limpia(carpeta, datos)
    malla = trimesh.load(ruta_limpia, file_type="stl", force="mesh")
    dimensiones_mm = medir_malla_mm(malla)

    carpeta_output = carpeta / "output"
    carpeta_output.mkdir(parents=True, exist_ok=True)

    rutas_vistas: dict[str, Path] = {}
    nombres_renders: list[str] = []
    for vista in VISTAS_CONTROL1:
        nombre_png = nombre_archivo(nombre, actual, f"render-{vista}", "png")
        rutas_vistas[vista] = carpeta_output / nombre_png
        nombres_renders.append(nombre_png)

    # Borramos renders previos para no confundirlos con los de esta corrida.
    for ruta_png in rutas_vistas.values():
        ruta_png.unlink(missing_ok=True)

    metodo = "matplotlib"
    if not sin_blender:
        try:
            find_blender()
            run_blender_script(
                _SCRIPT_RENDER_BLENDER,
                str(ruta_limpia),
                str(rutas_vistas["frente"]),
                str(rutas_vistas["perfil"]),
                str(rutas_vistas["arriba"]),
                str(rutas_vistas["3-4"]),
            )
            faltantes = [str(r) for r in rutas_vistas.values() if not r.is_file()]
            if faltantes:
                raise RuntimeError(f"Blender no generó los renders: {', '.join(faltantes)}")
            metodo = "blender"
        except (BlenderNotFoundError, RuntimeError):
            _renderizar_con_matplotlib(malla, rutas_vistas)
            metodo = "matplotlib"
    else:
        _renderizar_con_matplotlib(malla, rutas_vistas)

    leyendas: dict[str, str] = {}
    for vista, ruta_png in rutas_vistas.items():
        leyendas[vista] = _sobreimprimir_medidas_png(
            ruta_png,
            nombre=nombre,
            version=actual,
            vista=vista,
            dimensiones_mm=dimensiones_mm,
        )

    hoy = date.today().isoformat()
    registro: dict[str, Any] = {
        "estado": estado,
        "fecha": hoy,
        "motivo": motivo_limpio,
        "renders": nombres_renders,
        "dimensiones_mm": dimensiones_mm,
        "metodo": metodo,
    }
    _registrar_control1_en_diseno(carpeta, registro)

    return {
        "nombre": nombre,
        "version": actual,
        "estado": estado,
        "fecha": hoy,
        "motivo": motivo_limpio,
        "renders": [rutas_vistas[v] for v in VISTAS_CONTROL1],
        "nombres_renders": nombres_renders,
        "dimensiones_mm": dimensiones_mm,
        "leyendas": leyendas,
        "metodo": metodo,
    }


def verificar_control1_para_matriz(
    carpeta: Path,
    datos: dict[str, Any],
    *,
    exigir_control1: bool = False,
) -> None:
    """Verifica que el Punto de control 1 no esté rechazado ni pendiente antes de crear la matriz.

    Si `control_1` existe en la versión actual y su `estado` no es `"aprobado"`,
    o si `exigir_control1=True` y aún no se ha aprobado, lanza :class:`Control1Error`.
    """
    nombre = datos["nombre"]
    actual = datos["version_actual"]
    info_version = datos_version(datos, actual)
    parametros = info_version.get("parametros", {})
    control1 = parametros.get("control_1") or parametros.get("control-1")

    if control1 is None:
        if exigir_control1:
            raise Control1Error(
                f"La versión v{actual} de «{nombre}» aún no ha pasado el punto de control 1: "
                f'ejecuta primero "castkit control1 {nombre} --aprobar" antes de generar la matriz.'
            )
        return

    estado = control1.get("estado")
    if estado == "aprobado":
        return

    if estado == "rechazado":
        motivo = control1.get("motivo") or "sin motivo especificado"
        raise Control1Error(
            f"La versión v{actual} de «{nombre}» fue rechazada en el punto de control 1 "
            f"(motivo: «{motivo}») y no puede pasar a la matriz. Crea una nueva versión o "
            f'aprueba la figura con "castkit control1 {nombre} --aprobar".'
        )

    raise Control1Error(
        f'La versión v{actual} de «{nombre}» tiene el punto de control 1 en estado "{estado}" '
        f'(sin aprobar): aprueba primero la figura con "castkit control1 {nombre} --aprobar" '
        "antes de pasar a la matriz."
    )
