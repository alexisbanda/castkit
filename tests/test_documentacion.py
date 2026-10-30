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
_RUTA_README = _RAIZ / "README.md"
_README = _RUTA_README.read_text(encoding="utf-8")
_GUIA_OPERADOR = _RAIZ / "docs" / "guia-operador.md"


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


def _anclas_disponibles(texto: str = _README) -> set[str]:
    anclas: set[str] = set()
    contador: dict[str, int] = {}
    for linea in texto.splitlines():
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

        ruta_str, _, ancla = destino.partition("#")
        ruta = (_RAIZ / ruta_str).resolve()
        if not ruta.exists():
            rotos.append(f"ruta {destino} (no existe en el repositorio)")
            continue

        if ancla:
            anclas_destino = _anclas_disponibles(ruta.read_text(encoding="utf-8"))
            if ancla not in anclas_destino:
                rotos.append(f"ancla {destino} (sin encabezado correspondiente)")

    assert not rotos, "Enlaces internos rotos en README.md: " + ", ".join(rotos)


def test_el_readme_enlaza_la_guia_del_operador() -> None:
    assert "docs/guia-operador.md" in _README, (
        "README.md debería enlazar docs/guia-operador.md (la guía del operador, #66)."
    )


def test_existe_la_guia_del_operador() -> None:
    assert _GUIA_OPERADOR.is_file(), "Falta docs/guia-operador.md (#66)."


def test_enlaces_e_imagenes_de_la_guia_del_operador_apuntan_a_algo_que_existe() -> None:
    """Los enlaces relativos e imágenes de `docs/guia-operador.md` se resuelven contra
    `docs/` (la carpeta donde vive el archivo), igual que lo haría GitHub."""

    texto_guia = _GUIA_OPERADOR.read_text(encoding="utf-8")
    anclas_propias = _anclas_disponibles(texto_guia)
    base = _GUIA_OPERADOR.parent

    rotos: list[str] = []
    for destino in re.findall(r"\]\(([^)]+)\)", texto_guia):
        if destino.startswith(("http://", "https://", "mailto:")):
            continue

        ruta_str, _, ancla = destino.partition("#")

        if not ruta_str:
            # Enlace a un ancla de la propia guía (p. ej. "#7-cuando-algo-falla").
            if ancla not in anclas_propias:
                rotos.append(f"ancla #{ancla} (sin encabezado correspondiente en la guía)")
            continue

        ruta = (base / ruta_str).resolve()
        if not ruta.exists():
            rotos.append(f"ruta {destino} (no existe en el repositorio)")
            continue

        if ancla:
            anclas_destino = _anclas_disponibles(ruta.read_text(encoding="utf-8"))
            if ancla not in anclas_destino:
                rotos.append(f"ancla {destino} (sin encabezado correspondiente)")

    assert not rotos, "Enlaces rotos en docs/guia-operador.md: " + ", ".join(rotos)


def test_las_imagenes_de_docs_img_pesan_500_kb_o_menos_en_total() -> None:
    carpeta_img = _RAIZ / "docs" / "img"
    total = sum(p.stat().st_size for p in carpeta_img.glob("*") if p.is_file())
    assert total <= 500 * 1024, f"docs/img pesa {total / 1024:.1f} KB en total; debe ser ≤ 500 KB."


def test_ningun_uv_tool_install_con_comillas_simples() -> None:
    """`uv tool install 'castkit ...'` falla en cmd en Windows (#78): solo comillas dobles.

    Comprueba README.md, docs/guia-operador.md y todo `src/castkit/**/*.py`.
    """
    patron = re.compile(r"uv tool install '")

    rutas = [_RUTA_README, _GUIA_OPERADOR, *sorted((_RAIZ / "src" / "castkit").rglob("*.py"))]

    encontrados: list[str] = []
    for ruta in rutas:
        texto = ruta.read_text(encoding="utf-8")
        for numero, linea in enumerate(texto.splitlines(), start=1):
            if patron.search(linea):
                encontrados.append(f"{ruta.relative_to(_RAIZ)}:{numero}")

    assert not encontrados, (
        "«uv tool install '» con comillas simples falla en cmd en Windows (#78); "
        "usa comillas dobles. Encontrado en: " + ", ".join(encontrados)
    )
