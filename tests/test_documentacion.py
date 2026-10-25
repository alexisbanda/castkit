"""Comprueba que `README.md` quede al día con la CLI y con `config/fabricacion.toml`.

No valida el contenido en detalle: solo que cada subcomando, cada opción `--…` de la CLI
y cada clave de cada sección de `config/fabricacion.toml` aparezcan como texto literal
en `README.md`. Así, un subcomando u opción nuevos (o una clave de configuración nueva)
que no se documenten hacen fallar el test con un mensaje que los lista.

También comprueba que los enlaces internos de `README.md` (anclas `#…` y rutas relativas)
apunten a algo que exista: un encabezado real (para las anclas) o un archivo del repositorio
(para las rutas).
"""

from __future__ import annotations

import argparse
import re
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


def _slug_github(encabezado: str) -> str:
    """Genera el ancla que GitHub asignaría a un encabezado de Markdown.

    Minúsculas; se quita todo lo que no sea letra (se conservan tildes y «ñ»), número,
    espacio o guion; los espacios se convierten en guiones.
    """

    texto = encabezado.lower()
    texto = "".join(ch for ch in texto if ch.isalnum() or ch in (" ", "-"))
    return re.sub(r"\s+", "-", texto)


def _anclas_disponibles() -> set[str]:
    anclas: set[str] = set()
    contador: dict[str, int] = {}
    for linea in _README.splitlines():
        coincidencia = re.match(r"^#{1,6}\s+(.*)$", linea)
        if not coincidencia:
            continue
        slug = _slug_github(coincidencia.group(1))
        n = contador.get(slug, 0)
        contador[slug] = n + 1
        anclas.add(slug if n == 0 else f"{slug}-{n}")
    return anclas


def test_enlaces_internos_del_readme_apuntan_a_algo_que_existe() -> None:
    anclas_disponibles = _anclas_disponibles()

    rotos: list[str] = []
    for destino in re.findall(r"\]\(([^)]+)\)", _README):
        if destino.startswith(("http://", "https://", "mailto:")):
            continue

        if destino.startswith("#"):
            ancla = destino[1:]
            if ancla not in anclas_disponibles:
                rotos.append(f"ancla #{ancla} (sin encabezado correspondiente)")
            continue

        ruta = (_RAIZ / destino).resolve()
        if not ruta.exists():
            rotos.append(f"ruta {destino} (no existe en el repositorio)")

    assert not rotos, "Enlaces internos rotos en README.md: " + ", ".join(rotos)
