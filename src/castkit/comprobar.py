"""Comprobación de la instalación de castkit (`castkit comprobar`, ticket #64).

Informa, línea a línea y en español, si Blender está disponible y con qué
versión, si el extra opcional `profundidad` está instalado, dónde está la
configuración de fabricación y si es válida, dónde se crearán los diseños
nuevos y si la clave de Tripo está definida (sin mostrar su valor). Pensado
para ejecutarse justo después de instalar castkit con `uv tool install` y
confirmar que todo quedó en orden, sin tener que leer el README.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

from castkit import __version__
from castkit.blender import (
    BlenderNotFoundError,
    _buscar_en_carpeta_instalacion_windows,
    localizar_blender,
    mensaje_blender_no_encontrado,
)
from castkit.config import (
    ConfigError,
    cargar_config_generacion,
    cargar_config_limpieza,
    cargar_config_matriz,
    cargar_config_mecha,
    cargar_config_profundidad,
    cargar_config_relieve,
    carpeta_bajo_clon_castkit,
    ruta_config_fabricacion,
    version_blender_fijada,
)
from castkit.diseno import carpeta_disenos, origen_carpeta_disenos

_RELEASES = "https://github.com/alexisbanda/castkit/releases"

_TIMEOUT_VERSION_BLENDER_S = 10.0


def _version_instalada_blender(ejecutable) -> str | None:
    """Devuelve la primera línea de `<blender> --version`, o `None` si falla."""
    try:
        resultado = subprocess.run(
            [str(ejecutable), "--version"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_VERSION_BLENDER_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    primera_linea = resultado.stdout.splitlines()[0] if resultado.stdout else ""
    return primera_linea.strip() or None


def _comprobar_blender() -> bool:
    """Imprime el estado de Blender; devuelve `True` si está disponible."""
    version_fijada = version_blender_fijada()
    try:
        ejecutable, origen = localizar_blender()
    except BlenderNotFoundError:
        print(mensaje_blender_no_encontrado())
        return False

    print(f"Blender: {ejecutable} ({origen})")
    version_instalada = _version_instalada_blender(ejecutable)
    if version_instalada is None:
        print(f"Aviso: no se pudo obtener la versión de «{ejecutable}».")
    else:
        print(f"Versión instalada: {version_instalada}")
        if version_fijada not in version_instalada:
            print(
                f"Aviso: castkit fija Blender {version_fijada}; la versión instalada puede "
                "dar pequeñas diferencias en renders o remallado."
            )
    return True


def _estado_blender(plataforma: str | None = None) -> tuple[str, str]:
    """Determina el estado de Blender para `castkit comprobar --estado-blender` (#85).

    Devuelve `(estado, version)`, con `version` siempre
    `version_blender_fijada()` y `estado` uno de:

    - `"ok"`: el Blender localizado es la versión fijada; o, sin serlo (por
      `PATH` o `BLENDER_BIN`) y en Windows, hay un `blender.exe` con la
      versión fijada en la carpeta del instalador `.msi`
      (`_buscar_en_carpeta_instalacion_windows`, en `castkit.blender`): así
      no se vuelve a descargar el instalador si ya está puesto a mano o por
      el PATH de otra versión.
    - `"falta"`: no se localizó ningún Blender (`BlenderNotFoundError`,
      incluida una variable `BLENDER_BIN` que apunta a una ruta inexistente).
    - `"otra"`: hay un Blender localizado pero de otra versión, o no se pudo
      leer su versión.

    `plataforma` sigue el mismo patrón testeable que `localizar_blender`
    (ver `castkit.blender`): `None` usa `sys.platform`.
    """
    version_fijada = version_blender_fijada()
    try:
        ejecutable, origen = localizar_blender(plataforma=plataforma)
    except BlenderNotFoundError:
        return "falta", version_fijada

    version_instalada = _version_instalada_blender(ejecutable)
    if version_instalada is not None and version_fijada in version_instalada:
        return "ok", version_fijada

    plataforma_efectiva = plataforma if plataforma is not None else sys.platform
    if origen in ("PATH", "variable BLENDER_BIN") and plataforma_efectiva == "win32":
        encontrado = _buscar_en_carpeta_instalacion_windows()
        if encontrado is not None:
            version_en_carpeta = _version_instalada_blender(encontrado)
            if version_en_carpeta is not None and version_fijada in version_en_carpeta:
                return "ok", version_fijada

    return "otra", version_fijada


def imprimir_estado_blender(plataforma: str | None = None) -> int:
    """Imprime el estado de Blender para que lo lea `install.ps1`, no personas (#85).

    Dos líneas ASCII puro y nada más (sin avisos ni mensajes de error),
    pensadas para que PowerShell las parsee línea a línea:

        estado=ok|falta|otra
        version=5.2.2

    Siempre devuelve `0`: `install.ps1` decide qué hacer según `estado`, no
    es un código de error de castkit.
    """
    estado, version = _estado_blender(plataforma)
    print(f"estado={estado}")
    print(f"version={version}")
    return 0


def _comprobar_extra_profundidad() -> None:
    """Informa si el extra opcional `profundidad` (torch + transformers) está instalado."""
    instalado = (
        importlib.util.find_spec("torch") is not None
        and importlib.util.find_spec("transformers") is not None
    )
    if instalado:
        print("Extra «profundidad»: instalado (torch y transformers disponibles).")
    else:
        rueda = f"{_RELEASES}/download/v{__version__}/castkit-{__version__}-py3-none-any.whl"
        print(
            "Extra «profundidad»: no instalado (opcional; solo lo necesita "
            "`castkit generar --proveedor profundidad`). Para instalarlo en Windows, pega en "
            f"PowerShell: `irm {_RELEASES}/latest/download/install.ps1 | iex`. En Linux o macOS: "
            f'`uv tool install --force "castkit[profundidad] @ {rueda}" '
            "--index pytorch-cpu=https://download.pytorch.org/whl/cpu`. En un clon: "
            "`uv sync --extra profundidad`."
        )


def _comprobar_config() -> bool:
    """Imprime la ruta de la configuración y si es válida; devuelve `True` si lo es."""
    ruta = ruta_config_fabricacion()
    print(f"Configuración de fabricación: {ruta}")
    try:
        cargar_config_limpieza()
        cargar_config_generacion()
        cargar_config_mecha()
        cargar_config_matriz()
        cargar_config_relieve()
        cargar_config_profundidad()
    except ConfigError as exc:
        print(f"La configuración no es válida: {exc}")
        return False
    print("La configuración es válida.")
    return True


def _comprobar_carpeta_disenos() -> None:
    """Imprime dónde se crearán los diseños y avisa si cae dentro del propio repositorio."""
    try:
        carpeta = carpeta_disenos()
    except Exception as exc:  # noqa: BLE001 - se informa tal cual, no es fatal para el resto.
        print(f"Carpeta de diseños: error al resolverla: {exc}")
        return

    if origen_carpeta_disenos() == "variable":
        print(f"Carpeta de diseños: {carpeta} (variable CASTKIT_DISENOS)")
    else:
        print(
            f"Carpeta de diseños: {carpeta} (por defecto; para usar otra, define CASTKIT_DISENOS)"
        )
    raiz_clon = carpeta_bajo_clon_castkit(carpeta)
    if raiz_clon is not None:
        print(
            f"Aviso: la carpeta de diseños ({carpeta}) está dentro del clon de castkit en "
            f'"{raiz_clon}"; los diseños quedarían mezclados con el código. Define '
            "CASTKIT_DISENOS con una carpeta aparte."
        )


def _comprobar_clave_tripo() -> None:
    """Informa si `TRIPO_API_KEY` está definida, sin mostrar su valor."""
    if os.environ.get("TRIPO_API_KEY", "").strip():
        print("Clave de Tripo (TRIPO_API_KEY): presente.")
    else:
        print(
            "Clave de Tripo (TRIPO_API_KEY): no definida (opcional; solo la necesita el "
            "proveedor «tripo3d»)."
        )


def comprobar_instalacion() -> int:
    """Ejecuta todas las comprobaciones y las imprime en orden.

    Devuelve `0` si Blender está disponible y la configuración es válida; si
    no, devuelve `1` tras imprimir qué falló y cómo arreglarlo. El resto de
    comprobaciones (extra `profundidad`, carpeta de diseños, clave de Tripo)
    son informativas y nunca cambian el código de salida.
    """
    blender_ok = _comprobar_blender()
    _comprobar_extra_profundidad()
    config_ok = _comprobar_config()
    _comprobar_carpeta_disenos()
    _comprobar_clave_tripo()

    if blender_ok and config_ok:
        return 0

    print("\nHay problemas que corregir antes de usar castkit:", file=sys.stderr)
    if not blender_ok:
        print("- Instala Blender o define BLENDER_BIN (ver arriba).", file=sys.stderr)
    if not config_ok:
        print("- Corrige la configuración de fabricación (ver arriba).", file=sys.stderr)
    return 1
