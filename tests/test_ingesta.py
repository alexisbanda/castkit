"""Tests de la ingesta de la malla en bruto (`castkit ingesta`) y de
`castkit.ingesta` (validar_malla, sha256_archivo, ingerir_malla).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from castkit.cli import main
from castkit.versiones import cargar_diseno

FIXTURES = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES / "cube.stl"
ABIERTA_STL = FIXTURES / "abierta.stl"


def _sha256(archivo: Path) -> str:
    return hashlib.sha256(archivo.read_bytes()).hexdigest()


ALTA_FLAGS = {
    "brief": "--brief",
    "material": "--material",
    "alto_mm": "--alto-mm",
    "cavidades": "--cavidades",
    "cantidad": "--cantidad",
    "fecha": "--fecha",
    "licencia": "--licencia",
    "origen": "--origen",
}


def _argv_alta(nombre: str, brief: str, **overrides: str) -> list[str]:
    valores = {
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
        argv.append(f"{ALTA_FLAGS[clave]}={valor}")
    return argv


def _argv_ingesta(nombre: str, archivo: str, reemplazar: bool = False) -> list[str]:
    argv = ["ingesta", nombre, archivo]
    if reemplazar:
        argv.append("--reemplazar")
    return argv


@pytest.fixture(autouse=True)
def _stdin_no_interactivo(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StdinNoInteractivo:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("sys.stdin", _StdinNoInteractivo())


def _activar_stdin_interactivo(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StdinInteractivo:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _StdinInteractivo())


def _input_que_no_debe_llamarse(prompt: str = "") -> str:
    raise AssertionError(f"No debería preguntarse nada; se preguntó: {prompt!r}")


@pytest.fixture
def brief_dir(tmp_path: Path) -> Path:
    carpeta = tmp_path / "brief-guagua"
    carpeta.mkdir()
    (carpeta / "especificacion.txt").write_text("Guagua de pan, 80mm de alto.\n", encoding="utf-8")
    return carpeta


@pytest.fixture
def disenos_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    carpeta = tmp_path / "disenos"
    carpeta.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta))
    return carpeta


@pytest.fixture
def diseno_v1(disenos_dir: Path, brief_dir: Path) -> Path:
    """Da de alta `guagua-pan` en v1 y devuelve su carpeta."""
    codigo = main(_argv_alta("guagua-pan", str(brief_dir)))
    assert codigo == 0
    return disenos_dir / "guagua-pan"


# ---------------------------------------------------------------------------
# Criterio: STL válido se copia y se registra
# ---------------------------------------------------------------------------


def test_ingesta_valida_copia_y_registra_parametros(
    diseno_v1: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = main(_argv_ingesta("guagua-pan", str(CUBE_STL)))

    assert codigo == 0
    destino = diseno_v1 / "input" / "guagua-pan_v1_bruto.stl"
    assert destino.is_file()
    assert _sha256(destino) == _sha256(CUBE_STL)

    datos = cargar_diseno(diseno_v1)
    parametros = datos["versiones"]["v1"]["parametros"]["ingesta"]
    assert parametros["archivo_original"] == "cube.stl"
    assert parametros["sha256"] == _sha256(CUBE_STL)
    assert parametros["caras"] == 12
    assert parametros["vertices"] == 8

    salida = capsys.readouterr().out
    assert "guagua-pan" in salida
    assert str(destino) in salida
    assert "12 caras" in salida
    assert "8 vértices" in salida


# ---------------------------------------------------------------------------
# Criterio: malla abierta se acepta
# ---------------------------------------------------------------------------


def test_malla_abierta_se_acepta(diseno_v1: Path) -> None:
    codigo = main(_argv_ingesta("guagua-pan", str(ABIERTA_STL)))

    assert codigo == 0
    destino = diseno_v1 / "input" / "guagua-pan_v1_bruto.stl"
    assert destino.is_file()
    assert _sha256(destino) == _sha256(ABIERTA_STL)

    datos = cargar_diseno(diseno_v1)
    parametros = datos["versiones"]["v1"]["parametros"]["ingesta"]
    assert parametros["caras"] >= 1


# ---------------------------------------------------------------------------
# Criterio: extensión distinta de .stl se rechaza
# ---------------------------------------------------------------------------


def test_extension_no_stl_se_rechaza_sin_copiar_ni_registrar(
    diseno_v1: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archivo_obj = tmp_path / "guagua.obj"
    archivo_obj.write_bytes(CUBE_STL.read_bytes())

    codigo = main(_argv_ingesta("guagua-pan", str(archivo_obj)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "STL" in error
    assert "guagua.obj" in error
    assert list((diseno_v1 / "input").iterdir()) == []
    datos = cargar_diseno(diseno_v1)
    assert "ingesta" not in datos["versiones"]["v1"]["parametros"]


def test_extension_mayuscula_tambien_se_rechaza(
    diseno_v1: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archivo_obj = tmp_path / "guagua.OBJ"
    archivo_obj.write_bytes(CUBE_STL.read_bytes())

    codigo = main(_argv_ingesta("guagua-pan", str(archivo_obj)))

    assert codigo != 0
    assert list((diseno_v1 / "input").iterdir()) == []


def test_extension_stl_en_mayusculas_se_acepta(diseno_v1: Path) -> None:
    mayus = diseno_v1.parent / "cube_mayus.STL"
    mayus.write_bytes(CUBE_STL.read_bytes())

    codigo = main(_argv_ingesta("guagua-pan", str(mayus)))

    assert codigo == 0
    assert (diseno_v1 / "input" / "guagua-pan_v1_bruto.stl").is_file()


# ---------------------------------------------------------------------------
# Criterio: STL vacío, ilegible o sin caras se rechaza
# ---------------------------------------------------------------------------


def test_stl_vacio_se_rechaza_sin_copiar_ni_registrar(
    diseno_v1: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archivo_vacio = tmp_path / "vacio.stl"
    archivo_vacio.write_bytes(b"")

    codigo = main(_argv_ingesta("guagua-pan", str(archivo_vacio)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "vacío" in error
    assert list((diseno_v1 / "input").iterdir()) == []
    datos = cargar_diseno(diseno_v1)
    assert "ingesta" not in datos["versiones"]["v1"]["parametros"]


def test_stl_sin_caras_se_rechaza_sin_copiar_ni_registrar(
    diseno_v1: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archivo_basura = tmp_path / "basura.stl"
    archivo_basura.write_bytes(b"esto no es un stl, son puros bytes de basura inutil" * 3)

    codigo = main(_argv_ingesta("guagua-pan", str(archivo_basura)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "ninguna cara" in error
    assert list((diseno_v1 / "input").iterdir()) == []
    datos = cargar_diseno(diseno_v1)
    assert "ingesta" not in datos["versiones"]["v1"]["parametros"]


def test_stl_ilegible_se_rechaza_sin_copiar_ni_registrar(
    diseno_v1: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archivo = tmp_path / "danado.stl"
    archivo.write_bytes(b"contenido cualquiera, no importa para este test")

    def _load_que_falla(*args: object, **kwargs: object) -> None:
        raise ValueError("fallo simulado de trimesh")

    monkeypatch.setattr("trimesh.load", _load_que_falla)

    codigo = main(_argv_ingesta("guagua-pan", str(archivo)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "dañado" in error
    assert list((diseno_v1 / "input").iterdir()) == []
    datos = cargar_diseno(diseno_v1)
    assert "ingesta" not in datos["versiones"]["v1"]["parametros"]


# ---------------------------------------------------------------------------
# Criterio: reemplazo de una malla ya existente
# ---------------------------------------------------------------------------


def test_reemplazo_interactivo_con_s_reemplaza_y_actualiza_registro(
    diseno_v1: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(_argv_ingesta("guagua-pan", str(CUBE_STL))) == 0

    _activar_stdin_interactivo(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")

    codigo = main(_argv_ingesta("guagua-pan", str(ABIERTA_STL)))

    assert codigo == 0
    destino = diseno_v1 / "input" / "guagua-pan_v1_bruto.stl"
    assert _sha256(destino) == _sha256(ABIERTA_STL)

    datos = cargar_diseno(diseno_v1)
    parametros = datos["versiones"]["v1"]["parametros"]["ingesta"]
    assert parametros["archivo_original"] == "abierta.stl"
    assert parametros["sha256"] == _sha256(ABIERTA_STL)


@pytest.mark.parametrize("respuesta", ["n", "", "no", "cualquier cosa"])
def test_reemplazo_interactivo_sin_s_no_cambia_nada(
    diseno_v1: Path, monkeypatch: pytest.MonkeyPatch, respuesta: str
) -> None:
    assert main(_argv_ingesta("guagua-pan", str(CUBE_STL))) == 0
    sha_antes = _sha256(diseno_v1 / "input" / "guagua-pan_v1_bruto.stl")
    datos_antes = cargar_diseno(diseno_v1)

    _activar_stdin_interactivo(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": respuesta)

    codigo = main(_argv_ingesta("guagua-pan", str(ABIERTA_STL)))

    assert codigo != 0
    destino = diseno_v1 / "input" / "guagua-pan_v1_bruto.stl"
    assert _sha256(destino) == sha_antes
    assert cargar_diseno(diseno_v1) == datos_antes


def test_reemplazo_sin_terminal_sin_flag_falla_mencionando_reemplazar(
    diseno_v1: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_argv_ingesta("guagua-pan", str(CUBE_STL))) == 0
    sha_antes = _sha256(diseno_v1 / "input" / "guagua-pan_v1_bruto.stl")

    codigo = main(_argv_ingesta("guagua-pan", str(ABIERTA_STL)))

    assert codigo == 2
    error = capsys.readouterr().err
    assert "--reemplazar" in error
    destino = diseno_v1 / "input" / "guagua-pan_v1_bruto.stl"
    assert _sha256(destino) == sha_antes


def test_reemplazo_con_flag_reemplaza_sin_preguntar(diseno_v1: Path) -> None:
    assert main(_argv_ingesta("guagua-pan", str(CUBE_STL))) == 0

    codigo = main(_argv_ingesta("guagua-pan", str(ABIERTA_STL), reemplazar=True))

    assert codigo == 0
    destino = diseno_v1 / "input" / "guagua-pan_v1_bruto.stl"
    assert _sha256(destino) == _sha256(ABIERTA_STL)


# ---------------------------------------------------------------------------
# Criterio: diseño inexistente, nombre inválido, archivo inexistente
# ---------------------------------------------------------------------------


def test_diseno_inexistente_produce_error_en_espanol(
    disenos_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    monkeypatch.setattr("builtins.input", _input_que_no_debe_llamarse)

    codigo = main(_argv_ingesta("no-existe", str(CUBE_STL)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "no-existe" in error


def test_nombre_invalido_produce_error_sin_preguntar(
    disenos_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    monkeypatch.setattr("builtins.input", _input_que_no_debe_llamarse)

    codigo = main(_argv_ingesta("Guagua De Pan", str(CUBE_STL)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "Guagua De Pan" in error


def test_archivo_inexistente_produce_error_sin_preguntar(
    diseno_v1: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    monkeypatch.setattr("builtins.input", _input_que_no_debe_llamarse)

    archivo_inexistente = tmp_path / "no-existe.stl"

    codigo = main(_argv_ingesta("guagua-pan", str(archivo_inexistente)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "no-existe.stl" in error


# ---------------------------------------------------------------------------
# Criterio: ingerir en v2 no modifica v1
# ---------------------------------------------------------------------------


def test_ingesta_en_v2_no_modifica_malla_ni_parametros_de_v1(diseno_v1: Path) -> None:
    assert main(_argv_ingesta("guagua-pan", str(CUBE_STL))) == 0
    datos_v1_antes = cargar_diseno(diseno_v1)["versiones"]["v1"]
    archivo_v1 = diseno_v1 / "input" / "guagua-pan_v1_bruto.stl"
    contenido_v1_antes = archivo_v1.read_bytes()

    codigo_version = main(["version", "guagua-pan", "--motivo=nueva version"])
    assert codigo_version == 0

    codigo_ingesta_v2 = main(_argv_ingesta("guagua-pan", str(ABIERTA_STL)))
    assert codigo_ingesta_v2 == 0

    assert archivo_v1.is_file()
    assert archivo_v1.read_bytes() == contenido_v1_antes
    datos_despues = cargar_diseno(diseno_v1)
    assert datos_despues["versiones"]["v1"] == datos_v1_antes

    archivo_v2 = diseno_v1 / "input" / "guagua-pan_v2_bruto.stl"
    assert archivo_v2.is_file()
    assert _sha256(archivo_v2) == _sha256(ABIERTA_STL)
    assert datos_despues["versiones"]["v2"]["parametros"]["ingesta"]["archivo_original"] == (
        "abierta.stl"
    )


# ---------------------------------------------------------------------------
# Ruta con comillas y con `~`
# ---------------------------------------------------------------------------


def test_ruta_con_comillas_se_acepta(diseno_v1: Path) -> None:
    codigo = main(_argv_ingesta("guagua-pan", f'"{CUBE_STL}"'))

    assert codigo == 0
    assert (diseno_v1 / "input" / "guagua-pan_v1_bruto.stl").is_file()


def test_ruta_con_home_se_acepta(
    diseno_v1: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta_home = tmp_path / "home-falso"
    carpeta_home.mkdir()
    archivo_en_home = carpeta_home / "cube.stl"
    archivo_en_home.write_bytes(CUBE_STL.read_bytes())

    monkeypatch.setenv("HOME", str(carpeta_home))
    monkeypatch.setenv("USERPROFILE", str(carpeta_home))

    codigo = main(_argv_ingesta("guagua-pan", "~/cube.stl"))

    assert codigo == 0
    assert (diseno_v1 / "input" / "guagua-pan_v1_bruto.stl").is_file()
