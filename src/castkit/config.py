"""Lectura de la configuración de fabricación (`fabricacion.toml`).

Trae los valores de partida de cada etapa del pipeline. Las opciones de la
línea de comandos (por ejemplo, `--caras-max` de `castkit limpieza`)
sobrescriben, una a una, los valores que traiga de aquí.

La ruta del archivo se resuelve en este orden (ver `ruta_config_fabricacion`,
ticket #64):

1. La variable de entorno `CASTKIT_FABRICACION_TOML`, si está definida.
2. En un clon de desarrollo del repositorio, `config/fabricacion.toml`.
3. Instalado como paquete (`uv tool install` / `pip install`), la copia en la
   carpeta de configuración del usuario (ver `carpeta_config_usuario`), que se
   crea sola la primera vez a partir de la que viaja dentro del paquete.
"""

from __future__ import annotations

import importlib.resources
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from castkit.diseno import AltaError

_CLAVES_LIMPIEZA = ("caras_max", "tolerancia_mm", "corte_base_mm", "agujero_max_mm")
_CLAVES_GENERACION = ("proveedor", "comando")
_CLAVES_MECHA = ("diametro_mecha_mm", "espesor_minimo_pared_mm")
_CLAVES_MATRIZ = (
    "espesor_base_mm",
    "margen_silicona_mm",
    "espesor_pared_mm",
    "margen_superior_mm",
    "separacion_cavidades_mm",
)
_OPCIONALES_RELIEVE = {
    "colores_max": 8,
    "base_mm": 2.0,
    "paso_mm": 0.4,
    "escalon_mm": 0.05,
    "area_min_mm2": 0.5,
    "placa_mm": 0.0,
    "placa_margen_mm": 1.0,
}
_OPCIONALES_PROFUNDIDAD = {
    "modelo": "depth-anything/Depth-Anything-V2-Small-hf",
    # Revisión (commit) fija del repo del modelo en Hugging Face: Apache-2.0.
    "revision": "5426e4f0f36572d16453bbda7a8389317b1bef99",
    "borde_mm": 4.0,
    "realce_mm": 1.2,
    "suavizado": 0.6,
    "ancho_min_mm": 2.5,
    "engrose_mm": 0.6,
    "mm_por_px": 0.2,
    "tolerancia_mm": 0.03,
    "caras_max": 120000,
}


class ConfigError(AltaError):
    """Se lanza cuando la configuración de fabricación no existe o le faltan claves.

    (Ver `ruta_config_fabricacion` para dónde se busca el archivo). El
    mensaje siempre está en español. Hereda de :class:`AltaError` para que
    quien ya captura esa excepción también capture esta.
    """


def _pyproject_es_castkit(ruta_pyproject: Path) -> bool:
    """Indica si `ruta_pyproject` es el `pyproject.toml` del propio castkit."""
    try:
        with ruta_pyproject.open("rb") as flujo:
            datos = tomllib.load(flujo)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return datos.get("project", {}).get("name") == "castkit"


def _raiz_clon_desarrollo() -> Path | None:
    """Si este código corre desde un clon del repositorio, devuelve su raíz.

    Comprueba que dos carpetas arriba de este archivo (`src/castkit/config.py`
    -> `src/castkit` -> `src` -> raíz) haya un `pyproject.toml` cuyo
    `[project] name` sea `"castkit"`. Instalado como paquete (wheel), no hay
    tal `pyproject.toml` ahí y devuelve `None`.
    """
    raiz = Path(__file__).resolve().parents[2]
    pyproject = raiz / "pyproject.toml"
    if pyproject.is_file() and _pyproject_es_castkit(pyproject):
        return raiz
    return None


def carpeta_bajo_clon_castkit(carpeta: Path) -> Path | None:
    """Si `carpeta` está dentro de (o es) un clon de castkit, devuelve su raíz.

    Sube por los padres de `carpeta` buscando un `pyproject.toml` cuyo
    `[project] name` sea `"castkit"`. Se usa en `castkit comprobar` (#64)
    para avisar si la carpeta de diseños configurada cae dentro del propio
    repositorio de castkit, en vez de en una carpeta aparte.
    """
    carpeta = carpeta.resolve()
    for ancestro in (carpeta, *carpeta.parents):
        pyproject = ancestro / "pyproject.toml"
        if pyproject.is_file() and _pyproject_es_castkit(pyproject):
            return ancestro
    return None


def carpeta_config_usuario(plataforma: str | None = None) -> Path:
    """Devuelve la carpeta de configuración de castkit para el usuario actual.

    Depende del sistema operativo (ver `sys.platform`, o el valor forzado en
    `plataforma`, pensado para los tests):

    - Linux (y el resto de *nix): `$XDG_CONFIG_HOME/castkit` si la variable
      está definida; si no, `~/.config/castkit`.
    - Windows (`win32`): `%APPDATA%\\castkit`.
    - macOS (`darwin`): `~/Library/Application Support/castkit`.
    """
    plataforma = plataforma if plataforma is not None else sys.platform
    if plataforma == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "castkit"
        return Path.home() / "AppData" / "Roaming" / "castkit"
    if plataforma == "darwin":
        return Path.home() / "Library" / "Application Support" / "castkit"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "castkit"
    return Path.home() / ".config" / "castkit"


def ruta_config_fabricacion() -> Path:
    """Devuelve la ruta de `fabricacion.toml`, creándola si hace falta.

    Orden de resolución (ticket #64):

    1. La variable de entorno `CASTKIT_FABRICACION_TOML`, si está definida
       (pensada también para aislar los tests, ver `tests/conftest.py`).
    2. En un clon de desarrollo del repositorio, `config/fabricacion.toml`.
    3. La copia en la carpeta de configuración del usuario (ver
       `carpeta_config_usuario`); si no existe ahí todavía, se crea (con sus
       carpetas padre) copiando la que viaja dentro del paquete instalado
       (`importlib.resources`), y se avisa por stderr de la ruta creada.
    """
    valor = os.environ.get("CASTKIT_FABRICACION_TOML")
    if valor:
        return Path(valor)

    raiz_clon = _raiz_clon_desarrollo()
    if raiz_clon is not None:
        ruta_clon = raiz_clon / "config" / "fabricacion.toml"
        if ruta_clon.is_file():
            return ruta_clon

    ruta_usuario = carpeta_config_usuario() / "fabricacion.toml"
    if not ruta_usuario.is_file():
        ruta_usuario.parent.mkdir(parents=True, exist_ok=True)
        ruta_usuario.write_bytes(_contenido_fabricacion_empaquetada())
        print(
            f'Aviso: se creó la configuración de fabricación por defecto en "{ruta_usuario}".',
            file=sys.stderr,
        )
    return ruta_usuario


def _contenido_fabricacion_empaquetada() -> bytes:
    """Lee el `fabricacion.toml` que viaja dentro del paquete instalado.

    Función aparte (en vez de inline en `ruta_config_fabricacion`) para que
    los tests puedan sustituirla: en un clon de desarrollo (instalación
    editable), `castkit/datos/fabricacion.toml` no existe de verdad, porque
    solo se copia ahí al construir el wheel (`force-include` en
    `pyproject.toml`, ver #64); esta rama solo se recorre realmente en una
    instalación hecha con `uv tool install`.
    """
    return (importlib.resources.files("castkit") / "datos" / "fabricacion.toml").read_bytes()


def version_blender_fijada() -> str:
    """Devuelve la versión de Blender que castkit fija (`.blender-version`).

    En un clon de desarrollo lee `.blender-version` del repositorio;
    instalado como paquete, lee `castkit/datos/blender-version`, empaquetado
    junto al wheel (ver `pyproject.toml`, ticket #64).
    """
    raiz_clon = _raiz_clon_desarrollo()
    if raiz_clon is not None:
        ruta_clon = raiz_clon / ".blender-version"
        if ruta_clon.is_file():
            return ruta_clon.read_text(encoding="utf-8").strip()

    return _contenido_version_blender_empaquetada()


def _contenido_version_blender_empaquetada() -> str:
    """Lee `castkit/datos/blender-version` del paquete instalado (ver #64).

    Función aparte por la misma razón que `_contenido_fabricacion_empaquetada`:
    permite a los tests sustituirla sin necesitar instalar un wheel real.
    """
    return (
        (importlib.resources.files("castkit") / "datos" / "blender-version")
        .read_text(encoding="utf-8")
        .strip()
    )


def _cargar_seccion(
    nombre_seccion: str,
    claves_requeridas: tuple[str, ...],
    opcionales: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Carga y valida una sección de `config/fabricacion.toml`."""
    ruta = ruta_config_fabricacion()
    if not ruta.is_file():
        raise ConfigError(f'No se encontró el archivo de configuración "{ruta}".')

    try:
        with ruta.open("rb") as flujo:
            datos = tomllib.load(flujo)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'El archivo de configuración "{ruta}" no es un TOML válido.') from exc

    seccion = datos.get(nombre_seccion, {})
    faltantes = [clave for clave in claves_requeridas if clave not in seccion]
    if faltantes:
        raise ConfigError(
            f'A la sección [{nombre_seccion}] de "{ruta}" le faltan estas claves: '
            f"{', '.join(faltantes)}."
        )

    resultado = {clave: seccion[clave] for clave in claves_requeridas}
    if opcionales:
        for clave, por_defecto in opcionales.items():
            resultado[clave] = seccion.get(clave, por_defecto)
    return resultado


def cargar_config_limpieza() -> dict[str, Any]:
    """Carga la sección `[limpieza]` de `config/fabricacion.toml`.

    Devuelve un `dict` con las claves `caras_max`, `tolerancia_mm`,
    `corte_base_mm`, `agujero_max_mm` y `voxel_mm`.
    """
    return _cargar_seccion("limpieza", _CLAVES_LIMPIEZA, opcionales={"voxel_mm": 0.3})


def cargar_config_generacion() -> dict[str, Any]:
    """Carga la sección `[generacion]` de `config/fabricacion.toml`.

    Devuelve un `dict` con las claves `proveedor`, `comando`, `tripo_modelo`,
    `tripo_espera_max_s` y `vista_area_min_pct`. Todas salvo `proveedor` y `comando`
    son opcionales (una copia de `fabricacion.toml` creada antes del ticket #93 no
    tiene `vista_area_min_pct`; se usa 10 por defecto).
    """
    return _cargar_seccion(
        "generacion",
        _CLAVES_GENERACION,
        opcionales={
            "tripo_modelo": "v2.5-20250123",
            "tripo_espera_max_s": 600,
            "vista_area_min_pct": 10.0,
        },
    )


def cargar_config_mecha() -> dict[str, Any]:
    """Carga la sección `[mecha]` de `config/fabricacion.toml`."""
    return _cargar_seccion("mecha", _CLAVES_MECHA)


def cargar_config_matriz() -> dict[str, Any]:
    """Carga la sección `[matriz]` de `config/fabricacion.toml`."""
    return _cargar_seccion("matriz", _CLAVES_MATRIZ)


def cargar_config_relieve() -> dict[str, Any]:
    """Carga la sección `[relieve]` de `config/fabricacion.toml`.

    Devuelve un `dict` con `colores_max`, `base_mm`, `paso_mm`, `escalon_mm`,
    `area_min_mm2`, `placa_mm` y `placa_margen_mm`. Todas las claves son
    opcionales (si la sección falta o está incompleta, se usan los valores
    por defecto del proveedor «relieve», ver #43).
    """
    return _cargar_seccion("relieve", (), opcionales=_OPCIONALES_RELIEVE)


def cargar_config_profundidad() -> dict[str, Any]:
    """Carga la sección `[profundidad]` de `config/fabricacion.toml`.

    Devuelve un `dict` con `modelo`, `revision`, `borde_mm`, `realce_mm`,
    `suavizado`, `ancho_min_mm`, `engrose_mm`, `mm_por_px`, `tolerancia_mm` y
    `caras_max`. Todas las claves son opcionales (si la sección falta o está
    incompleta, se usan los valores por defecto del proveedor «profundidad»,
    ver #56).
    """
    return _cargar_seccion("profundidad", (), opcionales=_OPCIONALES_PROFUNDIDAD)
