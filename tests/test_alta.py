"""Tests del alta de un diseño (`castkit alta`): cli.main + castkit.diseno."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from castkit.cli import main

OPCION_A_FLAG = {
    "brief": "--brief",
    "material": "--material",
    "alto_mm": "--alto-mm",
    "cavidades": "--cavidades",
    "cantidad": "--cantidad",
    "fecha": "--fecha",
    "licencia": "--licencia",
    "origen": "--origen",
}


def _argv(
    nombre: str = "guagua-pan", brief: str | None = "__brief__", **overrides: str | None
) -> list[str]:
    """Construye un argv de `alta` con valores por defecto válidos.

    `brief` se resuelve con el valor pasado explícitamente (incluyendo None
    para omitir la opción); el resto de opciones toma los valores por
    defecto salvo que se pasen en `overrides` (None omite la opción).
    """
    valores: dict[str, str | None] = {
        "brief": brief,
        "material": "porcelana-fria",
        "alto_mm": "80",
        "cavidades": "4",
        "cantidad": "48",
        "fecha": "2026-11-01",
        "licencia": "propio",
        "origen": "escultura propia en Nomad",
    }
    valores.update(overrides)

    argv = ["alta", nombre]
    for clave, valor in valores.items():
        if valor is not None:
            argv.extend([OPCION_A_FLAG[clave], str(valor)])
    return argv


@pytest.fixture(autouse=True)
def _stdin_no_interactivo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Por defecto, simula una entrada no interactiva (sin terminal)."""

    class _StdinNoInteractivo:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("sys.stdin", _StdinNoInteractivo())


@pytest.fixture
def brief_dir(tmp_path: Path) -> Path:
    """Carpeta de brief de ejemplo con un documento y una foto falsa."""
    carpeta = tmp_path / "brief-guagua"
    carpeta.mkdir()
    (carpeta / "especificacion.txt").write_text("Guagua de pan, 80mm de alto.\n", encoding="utf-8")
    (carpeta / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    return carpeta


@pytest.fixture
def destino(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Carpeta actual (cwd) usada como destino cuando no hay CASTKIT_DISENOS."""
    carpeta = tmp_path / "disenos"
    carpeta.mkdir()
    monkeypatch.chdir(carpeta)
    monkeypatch.delenv("CASTKIT_DISENOS", raising=False)
    return carpeta


def test_alta_completa_crea_la_estructura_estandar(
    destino: Path, brief_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = main(_argv(brief=str(brief_dir)))

    assert codigo == 0
    diseno = destino / "guagua-pan"
    assert diseno.is_dir()
    assert (diseno / "input").is_dir()
    assert (diseno / "work").is_dir()
    assert (diseno / "output").is_dir()
    assert (diseno / "CHANGELOG.md").is_file()
    assert (diseno / "diseno.json").is_file()

    copia_brief = diseno / "brief"
    assert (copia_brief / "especificacion.txt").is_file()
    assert (copia_brief / "foto.png").is_file()

    salida = capsys.readouterr().out
    assert "guagua-pan" in salida
    assert str(diseno) in salida

    # No queda ninguna carpeta temporal.
    assert sorted(p.name for p in destino.iterdir()) == ["guagua-pan"]


def test_diseno_json_contiene_los_metadatos_esperados(destino: Path, brief_dir: Path) -> None:
    codigo = main(_argv(brief=str(brief_dir)))
    assert codigo == 0

    contenido = json.loads((destino / "guagua-pan" / "diseno.json").read_text(encoding="utf-8"))

    assert contenido["nombre"] == "guagua-pan"
    assert contenido["version_actual"] == 1
    assert contenido["material"] == "porcelana-fria"
    assert contenido["medidas"]["alto_mm"] == 80
    assert contenido["cavidades"] == 4
    assert contenido["cantidad"] == 48
    assert contenido["fecha_entrega"] == "2026-11-01"
    assert contenido["modelo_base"]["licencia"] == "propio"
    assert contenido["modelo_base"]["origen"] == "escultura propia en Nomad"

    # "creado" es una fecha y hora ISO 8601 (con información de zona horaria).
    from datetime import datetime

    creado = datetime.fromisoformat(contenido["creado"])
    assert creado.tzinfo is not None

    assert "v1" in contenido["versiones"]
    v1 = contenido["versiones"]["v1"]
    datetime.fromisoformat(v1["creada"])
    assert v1["parametros"] == {}


def test_changelog_registra_la_creacion_de_v1_con_fecha(destino: Path, brief_dir: Path) -> None:
    codigo = main(_argv(brief=str(brief_dir)))
    assert codigo == 0

    changelog = (destino / "guagua-pan" / "CHANGELOG.md").read_text(encoding="utf-8")
    lineas = changelog.splitlines()

    assert lineas[0] == "# Historial de guagua-pan"
    # La primera entrada es la v1, con fecha AAAA-MM-DD.
    entradas = [linea for linea in lineas if linea.startswith("## ")]
    assert len(entradas) >= 1
    assert entradas[0].startswith("## v1 — ")
    assert "- Creación del diseño." in changelog


def test_usa_castkit_disenos_cuando_esta_definida(
    tmp_path: Path, brief_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta_datos = tmp_path / "datos" / "disenos"
    carpeta_datos.mkdir(parents=True)
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta_datos))
    # cwd distinto, para comprobar que no se usa.
    otra_cwd = tmp_path / "otro-lugar"
    otra_cwd.mkdir()
    monkeypatch.chdir(otra_cwd)

    codigo = main(_argv(brief=str(brief_dir)))

    assert codigo == 0
    assert (carpeta_datos / "guagua-pan").is_dir()
    assert not (otra_cwd / "guagua-pan").exists()


def test_usa_la_carpeta_actual_cuando_no_hay_castkit_disenos(
    destino: Path, brief_dir: Path
) -> None:
    codigo = main(_argv(brief=str(brief_dir)))

    assert codigo == 0
    assert (destino / "guagua-pan").is_dir()


def test_castkit_disenos_inexistente_falla_sin_crear_nada(
    tmp_path: Path,
    brief_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    carpeta_inexistente = tmp_path / "no-existe" / "disenos"
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta_inexistente))
    otra_cwd = tmp_path / "cwd"
    otra_cwd.mkdir()
    monkeypatch.chdir(otra_cwd)

    codigo = main(_argv(brief=str(brief_dir)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "CASTKIT_DISENOS" in error
    assert not carpeta_inexistente.parent.exists() or not any(carpeta_inexistente.parent.iterdir())
    assert not (otra_cwd / "guagua-pan").exists()


@pytest.mark.parametrize(
    ("nombre_crudo", "propuesta_esperada"),
    [
        ("Guagua de Pan", "guagua-de-pan"),
        ("Niño Dios", "nino-dios"),
        ("GUAGUA_PAN", "guagua-pan"),
    ],
)
def test_nombre_invalido_se_rechaza_con_propuesta(
    destino: Path,
    brief_dir: Path,
    capsys: pytest.CaptureFixture[str],
    nombre_crudo: str,
    propuesta_esperada: str,
) -> None:
    codigo = main(_argv(nombre=nombre_crudo, brief=str(brief_dir)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert nombre_crudo in error
    assert propuesta_esperada in error
    assert list(destino.iterdir()) == []


@pytest.mark.parametrize(
    ("overrides", "fragmento_opcion"),
    [
        ({"material": "plastico"}, "--material"),
        ({"licencia": "prestado"}, "--licencia"),
        ({"alto_mm": "0"}, "--alto-mm"),
        ({"alto_mm": "-5"}, "--alto-mm"),
        ({"alto_mm": "abc"}, "--alto-mm"),
        ({"cavidades": "0"}, "--cavidades"),
        ({"cavidades": "1.5"}, "--cavidades"),
        ({"cantidad": "0"}, "--cantidad"),
        ({"cantidad": "abc"}, "--cantidad"),
        ({"fecha": "2026-13-40"}, "--fecha"),
        ({"fecha": "01/11/2026"}, "--fecha"),
    ],
)
def test_valores_invalidos_se_rechazan_sin_crear_nada(
    destino: Path,
    brief_dir: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
    fragmento_opcion: str,
) -> None:
    codigo = main(_argv(brief=str(brief_dir), **overrides))

    assert codigo != 0
    error = capsys.readouterr().err
    assert fragmento_opcion in error
    assert list(destino.iterdir()) == []


def test_brief_inexistente_se_rechaza(
    destino: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    brief_inexistente = tmp_path / "no-existe"

    codigo = main(_argv(brief=str(brief_inexistente)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "--brief" in error
    assert list(destino.iterdir()) == []


def test_brief_vacio_se_rechaza(
    destino: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    brief_vacio = tmp_path / "brief-vacio"
    brief_vacio.mkdir()

    codigo = main(_argv(brief=str(brief_vacio)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "--brief" in error
    assert list(destino.iterdir()) == []


def test_opciones_faltantes_no_interactivo_lista_todas_y_no_crea_nada(
    destino: Path, brief_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = main(_argv(brief=str(brief_dir), licencia=None, origen=None, material=None))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "--licencia" in error
    assert "--origen" in error
    assert "--material" in error
    assert list(destino.iterdir()) == []


def test_sin_licencia_nunca_crea_el_diseno(destino: Path, brief_dir: Path) -> None:
    codigo = main(_argv(brief=str(brief_dir), licencia=None))

    assert codigo != 0
    assert list(destino.iterdir()) == []


def test_sin_origen_nunca_crea_el_diseno(destino: Path, brief_dir: Path) -> None:
    codigo = main(_argv(brief=str(brief_dir), origen=None))

    assert codigo != 0
    assert list(destino.iterdir()) == []


def test_opciones_faltantes_interactivo_pregunta_en_espanol(
    destino: Path, brief_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _StdinInteractivo:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _StdinInteractivo())

    preguntas_hechas: list[str] = []

    def _input_falso(prompt: str = "") -> str:
        preguntas_hechas.append(prompt)
        if "Licencia" in prompt:
            return "propio"
        if "Origen" in prompt:
            return "escultura propia en Nomad"
        raise AssertionError(f"Pregunta inesperada: {prompt}")

    monkeypatch.setattr("builtins.input", _input_falso)

    codigo = main(_argv(brief=str(brief_dir), licencia=None, origen=None))

    assert codigo == 0
    assert (destino / "guagua-pan").is_dir()
    # Las preguntas están en español y mencionan las opciones pedidas.
    assert any("Licencia" in p for p in preguntas_hechas)
    assert any("Origen" in p for p in preguntas_hechas)


def test_alta_duplicada_no_modifica_y_avisa(
    destino: Path, brief_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    primer_codigo = main(_argv(brief=str(brief_dir)))
    assert primer_codigo == 0

    diseno = destino / "guagua-pan"
    marca = diseno / "diseno.json"
    contenido_original = marca.read_text(encoding="utf-8")

    segundo_codigo = main(_argv(brief=str(brief_dir), origen="otro origen distinto"))

    assert segundo_codigo != 0
    error = capsys.readouterr().err
    assert "guagua-pan" in error
    assert "ya existe" in error
    assert marca.read_text(encoding="utf-8") == contenido_original
    # Nada de carpetas temporales sueltas.
    assert sorted(p.name for p in destino.iterdir()) == ["guagua-pan"]
