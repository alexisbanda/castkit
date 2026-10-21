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

    Devuelve un `dict` con las claves `proveedor`, `comando`, `tripo_modelo`
    y `tripo_espera_max_s`.
    """
    return _cargar_seccion(
        "generacion",
        _CLAVES_GENERACION,
        opcionales={"tripo_modelo": "v2.5-20250123", "tripo_espera_max_s": 600},
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
