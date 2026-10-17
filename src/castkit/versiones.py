"""Nueva versión de un diseño y registro de parámetros por etapa.

Cada diseño guarda en ``diseno.json`` un diccionario ``versiones`` con una
entrada por versión (``v1``, ``v2``, ...). La versión en curso la marca
``version_actual``. Al crear una versión nueva, se copian de la anterior el
alto, las cavidades y los parámetros (estos últimos como copia independiente,
para que modificarlos en la nueva versión no afecte a las anteriores);
``brief/``, ``input/``, ``work/`` y ``output/`` no se tocan, y las entradas de
versiones anteriores tampoco.

REGLA para las etapas del pipeline (limpieza, impresión, etc.), a partir del
ticket #6: toda salida que una etapa escriba en ``output/`` (o ``work/``) se
nombra con :func:`nombre_archivo`, y todo parámetro que la etapa use se
registra con :func:`registrar_parametros` en la versión actual del diseño.
Así cada archivo queda asociado a la versión que lo produjo y cada parámetro
usado queda documentado y es reproducible.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from castkit.diseno import AltaError, carpeta_disenos, validar_nombre

_PATRON_SEGMENTO = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class VersionError(AltaError):
    """Se lanza cuando no se puede crear o leer una versión de un diseño.

    El mensaje siempre está en español. Hereda de :class:`AltaError` para que
    quien ya captura esa excepción (p. ej. la CLI) también capture esta.
    """


def _validar_segmento(valor: str, descripcion: str) -> None:
    """Valida que ``valor`` cumpla ``^[a-z0-9]+(-[a-z0-9]+)*$``."""
    if not isinstance(valor, str) or not _PATRON_SEGMENTO.match(valor):
        raise VersionError(
            f'{descripcion} "{valor}" no es válido: usa solo minúsculas, números y guiones, '
            "sin mayúsculas, espacios ni puntos."
        )


def nombre_archivo(diseno: str, version: int, pieza: str, ext: str) -> str:
    """Devuelve el nombre estándar de un archivo de una versión de un diseño.

    Formato: ``<diseno>_v<version>_<pieza>.<ext>``. Por ejemplo,
    ``nombre_archivo("guagua-pan", 2, "master", "stl")`` devuelve
    ``"guagua-pan_v2_master.stl"``.

    Lanza :class:`VersionError` si ``diseno`` no es un nombre válido, si
    ``version`` no es un entero mayor o igual a 1, o si ``pieza`` o ``ext``
    (sin el punto) no cumplen ``^[a-z0-9]+(-[a-z0-9]+)*$``.
    """
    try:
        validar_nombre(diseno)
    except AltaError as exc:
        raise VersionError(str(exc)) from exc

    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise VersionError(
            f'La versión "{version}" no es válida: debe ser un entero mayor o igual a 1.'
        )

    _validar_segmento(pieza, "El nombre de pieza")
    _validar_segmento(ext, "La extensión")

    return f"{diseno}_v{version}_{pieza}.{ext}"


def ubicar_diseno(nombre: str) -> Path:
    """Devuelve la carpeta del diseño ``nombre`` dentro de :func:`carpeta_disenos`.

    Lanza :class:`VersionError` si el nombre no es válido, o si la carpeta no
    existe o no contiene un ``diseno.json`` (es decir, no es un diseño dado de
    alta con ``castkit alta``).
    """
    try:
        validar_nombre(nombre)
    except AltaError as exc:
        raise VersionError(str(exc)) from exc

    destino = carpeta_disenos()
    carpeta = destino / nombre
    if not carpeta.is_dir() or not (carpeta / "diseno.json").is_file():
        raise VersionError(f'No se encontró el diseño "{nombre}" en {destino}.')
    return carpeta


def cargar_diseno(carpeta: Path) -> dict[str, Any]:
    """Carga y devuelve el contenido de ``diseno.json`` dentro de ``carpeta``."""
    ruta = Path(carpeta) / "diseno.json"
    return json.loads(ruta.read_text(encoding="utf-8"))


def guardar_diseno(carpeta: Path, datos: dict[str, Any]) -> None:
    """Guarda ``datos`` en ``diseno.json`` dentro de ``carpeta`` de forma atómica.

    Escribe primero en un archivo temporal en la misma carpeta y luego lo
    reemplaza con ``os.replace`` (atómico en el mismo sistema de archivos). Si
    algo falla, el archivo temporal se borra y no queda ningún rastro.
    """
    carpeta = Path(carpeta)
    ruta_final = carpeta / "diseno.json"
    contenido = json.dumps(datos, ensure_ascii=False, indent=2) + "\n"

    descriptor, ruta_tmp_str = tempfile.mkstemp(
        prefix=".diseno.json.tmp-", dir=carpeta, suffix=".json"
    )
    ruta_tmp = Path(ruta_tmp_str)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
        os.replace(ruta_tmp, ruta_final)
    except Exception:
        ruta_tmp.unlink(missing_ok=True)
        raise


def _clave_version(version: int) -> str:
    return f"v{version}"


def datos_version(datos: dict[str, Any], version: int | None = None) -> dict[str, Any]:
    """Devuelve los datos efectivos de una versión de un diseño.

    Si ``version`` es ``None``, usa ``version_actual``. El resultado tiene las
    claves ``version``, ``alto_mm``, ``cavidades`` y ``parametros``. Si la
    entrada de la versión no tiene ``alto_mm`` o ``cavidades`` (diseños dados
    de alta antes de este ticket), se usan de respaldo ``medidas.alto_mm`` y
    ``cavidades`` de nivel superior en ``datos``.
    """
    if version is None:
        version = datos["version_actual"]

    clave = _clave_version(version)
    versiones = datos.get("versiones", {})
    if clave not in versiones:
        raise VersionError(f'El diseño no tiene una versión "{clave}" registrada.')

    entrada = versiones[clave]
    alto_mm = entrada.get("alto_mm")
    if alto_mm is None:
        alto_mm = datos.get("medidas", {}).get("alto_mm")

    cavidades = entrada.get("cavidades")
    if cavidades is None:
        cavidades = datos.get("cavidades")

    return {
        "version": version,
        "alto_mm": alto_mm,
        "cavidades": cavidades,
        "parametros": entrada.get("parametros", {}),
    }


def crear_version(
    carpeta: Path,
    motivo: str,
    alto_mm: float | None = None,
    cavidades: int | None = None,
) -> int:
    """Crea la siguiente versión del diseño en ``carpeta`` y la deja como actual.

    La nueva versión parte de los datos efectivos de la versión actual (ver
    :func:`datos_version`): copia su alto y sus cavidades salvo que se indiquen
    ``alto_mm`` o ``cavidades`` explícitos, y copia (de forma independiente)
    sus parámetros. No modifica ``brief/``, ``input/``, ``work/``, ``output/``
    ni las entradas de versiones anteriores.

    ``motivo`` no puede quedar vacío tras aplicarle ``.strip()``: si lo está,
    lanza :class:`VersionError` sin escribir nada. Devuelve el número de la
    versión creada.
    """
    motivo_limpio = motivo.strip() if isinstance(motivo, str) else ""
    if not motivo_limpio:
        raise VersionError(
            "El motivo de la nueva versión no puede estar vacío: describe qué cambió."
        )

    carpeta = Path(carpeta)
    datos = cargar_diseno(carpeta)

    actual = datos["version_actual"]
    anterior = datos_version(datos, actual)
    nueva_version = actual + 1

    alto_efectivo = alto_mm if alto_mm is not None else anterior["alto_mm"]
    cavidades_efectivas = cavidades if cavidades is not None else anterior["cavidades"]

    ahora = datetime.now(UTC).isoformat()
    entrada_nueva = {
        "creada": ahora,
        "motivo": motivo_limpio,
        "alto_mm": alto_efectivo,
        "cavidades": cavidades_efectivas,
        "parametros": copy.deepcopy(anterior["parametros"]),
    }

    datos_previos_json = json.dumps(datos, ensure_ascii=False, indent=2) + "\n"

    datos["versiones"][_clave_version(nueva_version)] = entrada_nueva
    datos["version_actual"] = nueva_version

    guardar_diseno(carpeta, datos)

    try:
        _anexar_changelog(carpeta, nueva_version, motivo_limpio, anterior, entrada_nueva)
    except Exception:
        # El diseno.json ya se actualizó; si el CHANGELOG falla, se restaura
        # el contenido anterior para no dejar el diseño en un estado a medias.
        (carpeta / "diseno.json").write_text(datos_previos_json, encoding="utf-8")
        raise

    return nueva_version


def _formatear_numero(valor: float) -> str:
    """Formatea un número para el CHANGELOG, sin ``.0`` cuando es un entero exacto."""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def _anexar_changelog(
    carpeta: Path,
    version: int,
    motivo: str,
    anterior: dict[str, Any],
    nueva: dict[str, Any],
) -> None:
    """Añade al final de ``CHANGELOG.md`` la entrada de la versión nueva."""
    ruta = Path(carpeta) / "CHANGELOG.md"
    hoy = date.today().isoformat()

    lineas = [f"## v{version} — {hoy}", "", f"- {motivo}"]

    if anterior["alto_mm"] != nueva["alto_mm"]:
        antes = _formatear_numero(anterior["alto_mm"])
        despues = _formatear_numero(nueva["alto_mm"])
        lineas.append(f"- Alto: {antes} mm → {despues} mm")
    if anterior["cavidades"] != nueva["cavidades"]:
        lineas.append(f"- Cavidades: {anterior['cavidades']} → {nueva['cavidades']}")

    entrada = "\n".join(lineas) + "\n"

    contenido_previo = ruta.read_text(encoding="utf-8").rstrip("\n")
    ruta.write_text(contenido_previo + "\n\n" + entrada, encoding="utf-8")


def registrar_parametros(carpeta: Path, etapa: str, parametros: dict[str, Any]) -> None:
    """Registra ``parametros`` de ``etapa`` en la versión actual del diseño.

    Reemplaza por completo ``versiones.v<actual>.parametros[etapa]`` sin tocar
    los parámetros de otras etapas ni los de otras versiones. El guardado de
    ``diseno.json`` es atómico (ver :func:`guardar_diseno`).
    """
    _validar_segmento(etapa, "El nombre de la etapa")

    carpeta = Path(carpeta)
    datos = cargar_diseno(carpeta)
    actual = datos["version_actual"]
    clave = _clave_version(actual)

    entrada = datos["versiones"][clave]
    entrada.setdefault("parametros", {})
    entrada["parametros"][etapa] = parametros

    guardar_diseno(carpeta, datos)
