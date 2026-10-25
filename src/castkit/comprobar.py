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

from castkit.blender import BlenderNotFoundError, find_blender
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
from castkit.diseno import carpeta_disenos

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
        ejecutable = find_blender()
    except BlenderNotFoundError:
        print(
            f"No se encuentra Blender {version_fijada}: instálalo desde blender.org "
            "o define BLENDER_BIN con la ruta del ejecutable"
        )
        return False

    print(f"Blender: {ejecutable}")
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


def _comprobar_extra_profundidad() -> None:
    """Informa si el extra opcional `profundidad` (torch + transformers) está instalado."""
    instalado = (
        importlib.util.find_spec("torch") is not None
        and importlib.util.find_spec("transformers") is not None
    )
    if instalado:
        print("Extra «profundidad»: instalado (torch y transformers disponibles).")
    else:
        print(
            "Extra «profundidad»: no instalado (opcional; solo lo necesita "
            "`castkit generar --proveedor profundidad`). Para instalarlo: "
            "`uv tool install 'castkit[profundidad] @ "
            "git+ssh://git@github.com/alexisbanda/castkit.git'`, o `uv sync --extra profundidad` "
            "en un clon."
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

    print(f"Carpeta de diseños: {carpeta}")
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
