"""Comprueba que `README.md` quede al día con la CLI y con `config/fabricacion.toml`.

No valida el contenido en detalle: solo que cada subcomando, cada opción `--…` de la CLI
y cada clave de cada sección de `config/fabricacion.toml` aparezcan como texto literal
en `README.md`. Así, un subcomando u opción nuevos (o una clave de configuración nueva)
que no se documenten hacen fallar el test con un mensaje que los lista.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

from castkit.cli import _construir_parser

_RAIZ = Path(__file__).resolve().parents[1]
_README = (_RAIZ / "README.md").read_text(encoding="utf-8")


def test_subcomandos_y_opciones_de_la_cli_estan_en_el_readme() -> None:
    parser = _construir_parser()

    subparsers_actions = [
        accion for accion in parser._actions if isinstance(accion, argparse._SubParsersAction)
    ]

    faltantes: list[str] = []
    for accion in subparsers_actions:
        for subcomando, subparser in accion.choices.items():
            if f"castkit {subcomando}" not in _README:
                faltantes.append(f"castkit {subcomando}")

            for opcion_accion in subparser._actions:
                for opcion in opcion_accion.option_strings:
                    if not opcion.startswith("--") or opcion == "--help":
                        continue
                    if opcion not in _README:
                        faltantes.append(f"castkit {subcomando} {opcion}")

    assert not faltantes, (
        "Faltan en README.md estos subcomandos u opciones de la CLI: " + ", ".join(faltantes)
    )


def test_claves_de_fabricacion_toml_estan_en_el_readme() -> None:
    ruta_toml = _RAIZ / "config" / "fabricacion.toml"
    with ruta_toml.open("rb") as flujo:
        datos = tomllib.load(flujo)

    faltantes: list[str] = []
    for seccion, claves in datos.items():
        for clave in claves:
            if clave not in _README:
                faltantes.append(f"[{seccion}] {clave}")

    assert not faltantes, (
        "Faltan en README.md estas claves de config/fabricacion.toml: " + ", ".join(faltantes)
    )
