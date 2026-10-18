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

import subprocess
from datetime import date
from pathlib import Path
from typing import Any

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

VISTAS_CONTROL1: tuple[str, ...] = ("frente", "perfil", "arriba", "3-4")
DECISIONES_CONTROL1: tuple[str, ...] = ("aprobado", "rechazado", "pendiente")

_SCRIPT_RENDER_BLENDER = Path(__file__).parent / "blender_scripts" / "render_vistas.py"


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
) -> dict[str, Any]:
    """Genera los 4 PNG del Punto de control 1 y registra el estado en `diseno.json`.

    Los renders se generan siempre con Blender en modo headless (motor CYCLES
    en CPU, vía :func:`castkit.blender.run_blender_script`). No existe ningún
    motor de respaldo: si Blender no está disponible, falla o se cuelga, se
    lanza :class:`Control1Error` sin dejar PNG a medio generar.
    """
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

    # Borramos renders previos para no confundirlos con los de esta corrida,
    # ni dejar PNG viejos si la corrida de Blender de ahora falla.
    for ruta_png in rutas_vistas.values():
        ruta_png.unlink(missing_ok=True)

    try:
        find_blender()
    except BlenderNotFoundError as exc:
        raise Control1Error(
            f"No se puede generar el punto de control 1 sin Blender: {exc}"
        ) from exc

    try:
        run_blender_script(
            _SCRIPT_RENDER_BLENDER,
            str(ruta_limpia),
            str(rutas_vistas["frente"]),
            str(rutas_vistas["perfil"]),
            str(rutas_vistas["arriba"]),
            str(rutas_vistas["3-4"]),
        )
    except subprocess.TimeoutExpired as exc:
        for ruta_png in rutas_vistas.values():
            ruta_png.unlink(missing_ok=True)
        raise Control1Error(
            "Blender superó el tiempo de espera "
            f"({exc.timeout:.0f} s) al generar los renders del punto de control 1."
        ) from exc
    except (RuntimeError, OSError) as exc:
        for ruta_png in rutas_vistas.values():
            ruta_png.unlink(missing_ok=True)
        raise Control1Error(
            "Blender falló al generar los renders del punto de control 1 "
            f"({type(exc).__name__}): {exc}"
        ) from exc

    faltantes = [str(r) for r in rutas_vistas.values() if not r.is_file()]
    if faltantes:
        for ruta_png in rutas_vistas.values():
            ruta_png.unlink(missing_ok=True)
        raise Control1Error(
            "Blender no generó los renders del punto de control 1: " + ", ".join(faltantes)
        )

    metodo = "blender"

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
