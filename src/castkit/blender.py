"""Utilidades para localizar y ejecutar Blender en modo headless como subproceso."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class BlenderNotFoundError(RuntimeError):
    """Se lanza cuando no se puede localizar un ejecutable de Blender válido."""


def find_blender() -> Path:
    """Localiza el ejecutable de Blender.

    Orden de búsqueda:
    1. Variable de entorno ``BLENDER_BIN`` (debe apuntar a un archivo existente).
    2. ``blender`` en el PATH del sistema.

    Lanza :class:`BlenderNotFoundError` si no se encuentra ninguno.
    """
    blender_bin = os.environ.get("BLENDER_BIN")
    if blender_bin:
        path = Path(blender_bin)
        if not path.is_file():
            raise BlenderNotFoundError(
                f"La variable de entorno BLENDER_BIN apunta a una ruta inexistente: {blender_bin}"
            )
        return path

    found = shutil.which("blender")
    if found:
        return Path(found)

    raise BlenderNotFoundError(
        "No se encontró el ejecutable de Blender. Instala Blender LTS "
        "(la versión requerida está fijada en el archivo .blender-version) y "
        "asegúrate de que el comando 'blender' esté en el PATH, o bien "
        "configura la variable de entorno BLENDER_BIN con la ruta completa "
        "al ejecutable de Blender."
    )


def run_blender_script(
    script: Path, *args: str, timeout: float = 300
) -> subprocess.CompletedProcess:
    """Ejecuta un script de Python dentro de Blender, en modo headless, como subproceso.

    Lanza ``blender --background --factory-startup --python <script> -- <args>``
    y devuelve el :class:`subprocess.CompletedProcess` resultante. Si el código de
    salida no es 0, lanza :class:`RuntimeError` incluyendo la salida de error estándar.
    """
    blender = find_blender()
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(script),
        "--",
        *args,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Blender terminó con código {result.returncode} al ejecutar {script}.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result
