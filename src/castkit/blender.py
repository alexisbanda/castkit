"""Utilidades para localizar y ejecutar Blender en modo headless como subproceso."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from castkit.config import version_blender_fijada

_RE_VERSION_MAYOR_MENOR = re.compile(r"(\d+)\.(\d+)")


class BlenderNotFoundError(RuntimeError):
    """Se lanza cuando no se puede localizar un ejecutable de Blender válido."""


def localizar_blender(plataforma: str | None = None) -> tuple[Path, str]:
    """Localiza el ejecutable de Blender y de dónde salió esa ruta.

    Orden de búsqueda:

    1. La variable de entorno `BLENDER_BIN`, si está definida: debe apuntar a
       un archivo existente. Si apunta a una ruta que no existe, falla aquí
       mismo (no sigue con el resto de la búsqueda, para no ocultar una
       variable mal escrita). Origen: «variable BLENDER_BIN».
    2. El comando `blender` disponible en el `PATH` del sistema. Origen:
       «PATH».
    3. Solo en Windows (`sys.platform == "win32"`, o el valor forzado en
       `plataforma`, pensado para los tests, con el mismo patrón que
       `carpeta_config_usuario` en `castkit.config`): las carpetas donde deja
       Blender el instalador oficial (`.msi`) de blender.org, bajo
       `%ProgramFiles%\\Blender Foundation` (o `C:\\Program Files` si la
       variable `ProgramFiles` no está definida), probando `Blender */blender.exe`.
       Si hay varias carpetas, se prefiere la que coincide con el
       mayor.menor de la versión que castkit fija (`version_blender_fijada`,
       ver `castkit.config`); si ninguna coincide, se usa la de versión más
       alta (comparación numérica, así que «Blender 5.10» es mayor que
       «Blender 5.9»). Las carpetas sin `blender.exe` dentro se ignoran; las
       que no tienen un número de versión en el nombre quedan al final.
       Origen: «carpeta de instalación de Blender».

    Fuera de Windows no se busca en ninguna carpeta fija (ver «Fuera de
    alcance» del ticket #77: macOS y Steam/Store quedan para otro ticket).

    Lanza :class:`BlenderNotFoundError` (con el texto de
    `mensaje_blender_no_encontrado`) si no se encuentra ninguno.
    """
    plataforma = plataforma if plataforma is not None else sys.platform

    blender_bin = os.environ.get("BLENDER_BIN")
    if blender_bin:
        path = Path(blender_bin)
        if not path.is_file():
            raise BlenderNotFoundError(
                f"La variable de entorno BLENDER_BIN apunta a una ruta inexistente: {blender_bin}"
            )
        return path, "variable BLENDER_BIN"

    found = shutil.which("blender")
    if found:
        return Path(found), "PATH"

    if plataforma == "win32":
        encontrado = _buscar_en_carpeta_instalacion_windows()
        if encontrado is not None:
            return encontrado, "carpeta de instalación de Blender"

    raise BlenderNotFoundError(mensaje_blender_no_encontrado(plataforma))


def _buscar_en_carpeta_instalacion_windows() -> Path | None:
    """Busca `blender.exe` bajo `%ProgramFiles%\\Blender Foundation` (instalador `.msi`).

    Devuelve la ruta preferida según el criterio descrito en
    `localizar_blender`, o `None` si no hay ninguna carpeta `Blender */blender.exe`
    válida.
    """
    base = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
    carpeta_foundation = base / "Blender Foundation"
    if not carpeta_foundation.is_dir():
        return None

    candidatos: list[tuple[tuple[int, int] | None, Path]] = []
    for carpeta in sorted(carpeta_foundation.glob("Blender *")):
        ejecutable = carpeta / "blender.exe"
        if not ejecutable.is_file():
            continue
        candidatos.append((_mayor_menor(carpeta.name), ejecutable))

    if not candidatos:
        return None

    try:
        mayor_menor_fijada = _mayor_menor(version_blender_fijada())
    except Exception:  # noqa: BLE001 - no debe impedir la búsqueda automática.
        mayor_menor_fijada = None

    if mayor_menor_fijada is not None:
        for version, ejecutable in candidatos:
            if version == mayor_menor_fijada:
                return ejecutable

    con_version = [c for c in candidatos if c[0] is not None]
    if con_version:
        return max(con_version, key=lambda c: c[0])[1]
    return candidatos[0][1]


def _mayor_menor(texto: str) -> tuple[int, int] | None:
    """Extrae `(mayor, menor)` del primer `X.Y` numérico en `texto`, o `None` si no hay."""
    coincidencia = _RE_VERSION_MAYOR_MENOR.search(texto)
    if not coincidencia:
        return None
    return int(coincidencia.group(1)), int(coincidencia.group(2))


def mensaje_blender_no_encontrado(plataforma: str | None = None) -> str:
    """Mensaje de «Blender no encontrado», con el texto propio de cada sistema.

    Incluye siempre el número de versión que castkit fija
    (`version_blender_fijada`, en `castkit.config`; nunca la frase «el
    archivo .blender-version», que no existe en una instalación hecha con
    `uv tool install`). En Windows explica que castkit busca en la carpeta
    del instalador `.msi` y cómo definir `BLENDER_BIN` de forma permanente
    con `setx` desde PowerShell; en el resto, cómo definirla con `export` en
    el archivo de arranque de la shell (por ejemplo `~/.bashrc`).
    """
    plataforma = plataforma if plataforma is not None else sys.platform
    version_fijada = version_blender_fijada()

    if plataforma == "win32":
        base = os.environ.get("ProgramFiles") or r"C:\Program Files"
        return (
            f"No se encuentra Blender {version_fijada}. Instálalo desde blender.org con el "
            f"instalador (.msi); castkit lo buscará en {base}\\Blender Foundation. Si lo "
            "instalaste en otra carpeta, define BLENDER_BIN en PowerShell: "
            'setx BLENDER_BIN "C:\\ruta\\a\\blender.exe" y abre una terminal nueva.'
        )

    return (
        f"No se encuentra Blender {version_fijada}: instálalo desde blender.org y asegúrate de "
        "que el comando 'blender' esté en el PATH, o bien define la variable de entorno "
        "BLENDER_BIN con la ruta completa al ejecutable, por ejemplo añadiendo "
        "`export BLENDER_BIN=/ruta/a/blender` a ~/.bashrc y abriendo una terminal nueva."
    )


def find_blender() -> Path:
    """Localiza el ejecutable de Blender (ver `localizar_blender` para el orden de búsqueda).

    Mantiene la firma histórica: solo devuelve la ruta, sin el origen (que sí
    usa `castkit comprobar` a través de `localizar_blender`).
    """
    return localizar_blender()[0]


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
