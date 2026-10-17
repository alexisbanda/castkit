"""Lectura de la configuración de fabricación (`config/fabricacion.toml`).

El archivo está versionado en el repositorio: trae los valores de partida de
cada etapa del pipeline. Las opciones de la línea de comandos (por ejemplo,
`--caras-max` de `castkit limpieza`) sobrescriben, una a una, los valores que
traiga de aquí.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from castkit.diseno import AltaError

_CLAVES_LIMPIEZA = ("caras_max", "tolerancia_mm", "corte_base_mm", "agujero_max_mm")
_CLAVES_GENERACION = ("proveedor", "comando")
_CLAVES_MATRIZ = (
    "espesor_base_mm",
    "margen_silicona_mm",
    "espesor_pared_mm",
    "margen_superior_mm",
    "separacion_cavidades_mm",
)

# src/castkit/config.py -> src/castkit -> src -> raíz del repositorio.
_RUTA_POR_DEFECTO = Path(__file__).resolve().parents[2] / "config" / "fabricacion.toml"


class ConfigError(AltaError):
    """Se lanza cuando `config/fabricacion.toml` no existe o le faltan claves.

    El mensaje siempre está en español. Hereda de :class:`AltaError` para
    que quien ya captura esa excepción también capture esta.
    """


def ruta_config_fabricacion() -> Path:
    """Devuelve la ruta de `config/fabricacion.toml`.

    Usa la variable de entorno `CASTKIT_FABRICACION_TOML` si está definida
    (pensada para aislar los tests); si no, la carpeta `config/` del
    repositorio.
    """
    valor = os.environ.get("CASTKIT_FABRICACION_TOML")
    if valor:
        return Path(valor)
    return _RUTA_POR_DEFECTO


def _cargar_seccion(nombre_seccion: str, claves_requeridas: tuple[str, ...]) -> dict[str, Any]:
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

    return {clave: seccion[clave] for clave in claves_requeridas}


def cargar_config_limpieza() -> dict[str, Any]:
    """Carga la sección `[limpieza]` de `config/fabricacion.toml`.

    Devuelve un `dict` con las claves `caras_max`, `tolerancia_mm`,
    `corte_base_mm` y `agujero_max_mm`. Lanza :class:`ConfigError` si el
    archivo no existe, no se puede leer como TOML, o a la sección
    `[limpieza]` le falta alguna de esas claves.
    """
    return _cargar_seccion("limpieza", _CLAVES_LIMPIEZA)


def cargar_config_generacion() -> dict[str, Any]:
    """Carga la sección `[generacion]` de `config/fabricacion.toml`.

    Devuelve un `dict` con las claves `proveedor` y `comando`.
    """
    return _cargar_seccion("generacion", _CLAVES_GENERACION)


def cargar_config_matriz() -> dict[str, Any]:
    """Carga la sección `[matriz]` de `config/fabricacion.toml`.

    Devuelve un `dict` con las claves `espesor_base_mm`, `margen_silicona_mm`,
    `espesor_pared_mm`, `margen_superior_mm` y `separacion_cavidades_mm`.
    """
    return _cargar_seccion("matriz", _CLAVES_MATRIZ)
