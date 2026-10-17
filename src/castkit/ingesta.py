"""Ingesta de la malla en bruto: primera etapa del pipeline.

La malla en bruto (imagen→3D, escaneo o escultura) se obtiene fuera de
castkit y el operador la incorpora al diseño con ``castkit ingesta``. Este
módulo valida el archivo STL recibido, lo copia a ``input/`` con el nombre
estándar de la versión en curso y registra su huella y sus métricas básicas.

Sigue la REGLA de :mod:`castkit.versiones`: la salida se nombra con
:func:`castkit.versiones.nombre_archivo` y los parámetros usados se registran
con :func:`castkit.versiones.registrar_parametros`, en la etapa ``"ingesta"``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from castkit.versiones import (
    VersionError,
    cargar_diseno,
    nombre_archivo,
    registrar_parametros,
)

_TAMANO_BLOQUE = 65536


class IngestaError(VersionError):
    """Se lanza cuando la malla en bruto no se puede validar o ingerir.

    El mensaje siempre está en español. Hereda de :class:`VersionError` (y por
    lo tanto de :class:`castkit.diseno.AltaError`) para que quien ya captura
    esas excepciones también capture esta.
    """


def validar_malla(archivo: Path) -> dict[str, int]:
    """Valida que ``archivo`` sea un STL legible y con al menos una cara.

    No exige que la malla esté cerrada (``watertight``): una malla abierta
    con al menos una cara es válida. Lanza :class:`IngestaError` con un
    mensaje en español si el archivo no existe, no es un archivo, no tiene
    extensión ``.stl`` (sin distinguir mayúsculas), está vacío, no se puede
    leer como STL o no contiene ninguna cara.

    Devuelve ``{"caras": int, "vertices": int}``.
    """
    archivo = Path(archivo)

    if not archivo.is_file():
        raise IngestaError(f'No se encontró el archivo "{archivo}".')

    if archivo.suffix.lower() != ".stl":
        raise IngestaError(f'Solo se aceptan archivos STL (.stl); recibido "{archivo.name}".')

    if archivo.stat().st_size == 0:
        raise IngestaError(f'El archivo "{archivo}" está vacío.')

    import trimesh  # Importado aquí: es una dependencia pesada que solo usa esta función.

    try:
        malla = trimesh.load(archivo, file_type="stl", force="mesh")
    except Exception as exc:
        raise IngestaError(
            f'No se pudo leer el archivo "{archivo}" como STL: puede estar dañado.'
        ) from exc

    caras = len(malla.faces)
    if caras < 1:
        raise IngestaError(f'El archivo "{archivo}" no contiene ninguna cara.')

    return {"caras": caras, "vertices": len(malla.vertices)}


def sha256_archivo(archivo: Path) -> str:
    """Calcula la huella SHA-256 de ``archivo``, leyéndolo por bloques."""
    hasher = hashlib.sha256()
    with Path(archivo).open("rb") as flujo:
        for bloque in iter(lambda: flujo.read(_TAMANO_BLOQUE), b""):
            hasher.update(bloque)
    return hasher.hexdigest()


def ingerir_malla(carpeta: Path, archivo: Path, reemplazar: bool = False) -> Path:
    """Valida e incorpora ``archivo`` como la malla en bruto de la versión actual.

    Valida todo (el STL y, si ya hay una malla en la versión actual, que se
    permita reemplazarla) antes de tocar el disco. Copia ``archivo`` a
    ``carpeta/input/<nombre_archivo(diseno, version_actual, "bruto", "stl")>``
    y registra con :func:`registrar_parametros`, en la etapa ``"ingesta"`` de
    la versión actual, el nombre original del archivo, su ``sha256``, sus
    ``caras`` y sus ``vertices``.

    Si el destino ya existe y ``reemplazar`` es ``False``, lanza
    :class:`IngestaError` mencionando ``--reemplazar`` (la pregunta
    interactiva la hace la CLI, no esta función). Devuelve la ruta destino.
    """
    carpeta = Path(carpeta)
    archivo = Path(archivo)

    info = validar_malla(archivo)

    datos = cargar_diseno(carpeta)
    nombre_diseno = datos["nombre"]
    version_actual = datos["version_actual"]

    nombre_destino = nombre_archivo(nombre_diseno, version_actual, "bruto", "stl")
    destino = carpeta / "input" / nombre_destino

    habia_malla_previa = destino.exists()
    if habia_malla_previa and not reemplazar:
        raise IngestaError(
            f"La versión v{version_actual} de «{nombre_diseno}» ya tiene una malla en "
            f'"{destino}"; usa --reemplazar para sustituirla.'
        )

    huella = sha256_archivo(archivo)

    descriptor, tmp_str = tempfile.mkstemp(
        prefix=f".{nombre_destino}.tmp-", dir=destino.parent, suffix=".stl"
    )
    os.close(descriptor)
    tmp_path = Path(tmp_str)
    try:
        shutil.copyfile(archivo, tmp_path)
        os.replace(tmp_path, destino)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    parametros: dict[str, Any] = {
        "archivo_original": archivo.name,
        "sha256": huella,
        "caras": info["caras"],
        "vertices": info["vertices"],
    }
    try:
        registrar_parametros(carpeta, "ingesta", parametros)
    except Exception:
        # El registro falló tras copiar el archivo: si no había malla previa,
        # se borra la copia para no dejar un archivo sin registrar.
        if not habia_malla_previa:
            destino.unlink(missing_ok=True)
        raise

    return destino
