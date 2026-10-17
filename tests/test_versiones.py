"""Tests de la nueva versión de un diseño (`castkit version`) y de
`castkit.versiones` (nombre_archivo, registrar_parametros, atomicidad, etc.).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from castkit.cli import main
from castkit.versiones import (
    VersionError,
    cargar_diseno,
    crear_version,
    datos_version,
    guardar_diseno,
    nombre_archivo,
    registrar_parametros,
    ubicar_diseno,
)

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


def _argv_version(nombre: str, motivo: str | None = None, **overrides: str) -> list[str]:
    argv = ["version", nombre]
    if motivo is not None:
        argv.append(f"--motivo={motivo}")
    for clave, valor in overrides.items():
        flag = "--alto-mm" if clave == "alto_mm" else f"--{clave}"
        argv.append(f"{flag}={valor}")
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


def _snapshot_carpeta(carpeta: Path) -> dict[str, bytes]:
    """Devuelve el contenido de todos los archivos bajo `carpeta`, por ruta relativa."""
    if not carpeta.exists():
        return {}
    return {
        str(p.relative_to(carpeta)): p.read_bytes()
        for p in sorted(carpeta.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# castkit version: caso básico
# ---------------------------------------------------------------------------


def test_version_basica_incrementa_version_y_copia_datos_de_v1(diseno_v1: Path) -> None:
    codigo = main(_argv_version("guagua-pan", motivo="aumentar altura a 90 mm", alto_mm="90"))
    assert codigo == 0

    datos = json.loads((diseno_v1 / "diseno.json").read_text(encoding="utf-8"))
    assert datos["version_actual"] == 2

    v2 = datos["versiones"]["v2"]
    datetime.fromisoformat(v2["creada"])
    assert v2["motivo"] == "aumentar altura a 90 mm"
    assert v2["alto_mm"] == 90
    assert v2["cavidades"] == 4  # copiada de v1
    assert v2["parametros"] == {}

    v1 = datos["versiones"]["v1"]
    assert v1["alto_mm"] == 80
    assert v1["cavidades"] == 4


def test_version_v2_parametros_es_copia_independiente_de_v1(diseno_v1: Path) -> None:
    registrar_parametros(diseno_v1, "limpieza", {"caras_max": 200000})

    codigo = main(_argv_version("guagua-pan", motivo="nueva version"))
    assert codigo == 0

    datos = cargar_diseno(diseno_v1)
    assert datos["versiones"]["v2"]["parametros"] == {"limpieza": {"caras_max": 200000}}

    # Modificar los parámetros de v2 no debe afectar a los de v1.
    datos["versiones"]["v2"]["parametros"]["limpieza"]["caras_max"] = 1
    assert datos["versiones"]["v1"]["parametros"]["limpieza"]["caras_max"] == 200000


# ---------------------------------------------------------------------------
# --alto-mm / --cavidades opcionales
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "alto_esperado", "cavidades_esperadas"),
    [
        ({"alto_mm": "90"}, 90, 4),
        ({"cavidades": "6"}, 80, 6),
        ({"alto_mm": "90", "cavidades": "6"}, 90, 6),
        ({}, 80, 4),
    ],
)
def test_alto_y_cavidades_opcionales_se_combinan_con_los_de_v1(
    diseno_v1: Path,
    overrides: dict[str, str],
    alto_esperado: float,
    cavidades_esperadas: int,
) -> None:
    codigo = main(_argv_version("guagua-pan", motivo="cambio", **overrides))
    assert codigo == 0

    datos = cargar_diseno(diseno_v1)
    v2 = datos["versiones"]["v2"]
    assert v2["alto_mm"] == alto_esperado
    assert v2["cavidades"] == cavidades_esperadas
    # v1 conserva lo suyo.
    assert datos["versiones"]["v1"]["alto_mm"] == 80
    assert datos["versiones"]["v1"]["cavidades"] == 4


@pytest.mark.parametrize(
    ("overrides", "fragmento"),
    [
        ({"alto_mm": "0"}, "--alto-mm"),
        ({"alto_mm": "nan"}, "--alto-mm"),
        ({"alto_mm": "-5"}, "--alto-mm"),
        ({"cavidades": "0"}, "--cavidades"),
        ({"cavidades": "abc"}, "--cavidades"),
    ],
)
def test_valor_invalido_no_modifica_diseno_ni_changelog(
    diseno_v1: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
    fragmento: str,
) -> None:
    json_antes = (diseno_v1 / "diseno.json").read_text(encoding="utf-8")
    changelog_antes = (diseno_v1 / "CHANGELOG.md").read_text(encoding="utf-8")

    codigo = main(_argv_version("guagua-pan", motivo="cambio", **overrides))

    assert codigo != 0
    error = capsys.readouterr().err
    assert fragmento in error
    assert (diseno_v1 / "diseno.json").read_text(encoding="utf-8") == json_antes
    assert (diseno_v1 / "CHANGELOG.md").read_text(encoding="utf-8") == changelog_antes


# ---------------------------------------------------------------------------
# No se tocan brief/, input/, work/, output/ ni v1
# ---------------------------------------------------------------------------


def test_version_no_toca_brief_input_work_output_ni_la_entrada_v1(diseno_v1: Path) -> None:
    (diseno_v1 / "output" / "guagua-pan_v1_master.stl").write_bytes(b"stl-falso")
    (diseno_v1 / "input" / "modelo.obj").write_bytes(b"obj-falso")
    (diseno_v1 / "work" / "nota.txt").write_text("nota de trabajo\n", encoding="utf-8")

    snap_brief = _snapshot_carpeta(diseno_v1 / "brief")
    snap_input = _snapshot_carpeta(diseno_v1 / "input")
    snap_work = _snapshot_carpeta(diseno_v1 / "work")
    snap_output = _snapshot_carpeta(diseno_v1 / "output")
    v1_antes = cargar_diseno(diseno_v1)["versiones"]["v1"]

    codigo = main(_argv_version("guagua-pan", motivo="aumentar altura a 90 mm", alto_mm="90"))
    assert codigo == 0

    assert _snapshot_carpeta(diseno_v1 / "brief") == snap_brief
    assert _snapshot_carpeta(diseno_v1 / "input") == snap_input
    assert _snapshot_carpeta(diseno_v1 / "work") == snap_work
    assert _snapshot_carpeta(diseno_v1 / "output") == snap_output
    assert cargar_diseno(diseno_v1)["versiones"]["v1"] == v1_antes


# ---------------------------------------------------------------------------
# CHANGELOG
# ---------------------------------------------------------------------------


def test_changelog_registra_v2_con_motivo_y_cambios(diseno_v1: Path) -> None:
    codigo = main(
        _argv_version(
            "guagua-pan", motivo="aumentar altura y cavidades", alto_mm="90", cavidades="6"
        )
    )
    assert codigo == 0

    changelog = (diseno_v1 / "CHANGELOG.md").read_text(encoding="utf-8")
    lineas = changelog.splitlines()
    entradas = [i for i, linea in enumerate(lineas) if linea.startswith("## ")]
    assert len(entradas) == 2
    assert lineas[entradas[1]].startswith("## v2 — ")

    bloque_v2 = "\n".join(lineas[entradas[1] :])
    assert "- aumentar altura y cavidades" in bloque_v2
    assert "- Alto: 80 mm → 90 mm" in bloque_v2
    assert "- Cavidades: 4 → 6" in bloque_v2


def test_changelog_no_menciona_cambios_si_alto_y_cavidades_no_cambiaron(diseno_v1: Path) -> None:
    codigo = main(_argv_version("guagua-pan", motivo="solo registro, sin cambios de medidas"))
    assert codigo == 0

    changelog = (diseno_v1 / "CHANGELOG.md").read_text(encoding="utf-8")
    bloque_v2 = changelog.split("## v2")[1]
    assert "Alto:" not in bloque_v2
    assert "Cavidades:" not in bloque_v2


# ---------------------------------------------------------------------------
# Motivo: interactivo, no interactivo, vacío
# ---------------------------------------------------------------------------


def test_motivo_interactivo_se_pregunta_en_espanol(
    diseno_v1: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    preguntas: list[str] = []

    def _input_falso(prompt: str = "") -> str:
        preguntas.append(prompt)
        return "cambio de prueba"

    monkeypatch.setattr("builtins.input", _input_falso)

    codigo = main(_argv_version("guagua-pan"))

    assert codigo == 0
    assert any("Motivo" in p for p in preguntas)
    datos = cargar_diseno(diseno_v1)
    assert datos["versiones"]["v2"]["motivo"] == "cambio de prueba"


def test_motivo_faltante_no_interactivo_falla_pidiendo_motivo(
    diseno_v1: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    json_antes = (diseno_v1 / "diseno.json").read_text(encoding="utf-8")

    codigo = main(_argv_version("guagua-pan"))

    assert codigo == 2
    error = capsys.readouterr().err
    assert "--motivo" in error
    assert (diseno_v1 / "diseno.json").read_text(encoding="utf-8") == json_antes


def test_motivo_vacio_tras_preguntar_no_crea_version(
    diseno_v1: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "   ")
    json_antes = (diseno_v1 / "diseno.json").read_text(encoding="utf-8")

    codigo = main(_argv_version("guagua-pan"))

    assert codigo != 0
    assert capsys.readouterr().err
    assert (diseno_v1 / "diseno.json").read_text(encoding="utf-8") == json_antes


def test_motivo_de_solo_espacios_por_opcion_no_crea_version(
    diseno_v1: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    json_antes = (diseno_v1 / "diseno.json").read_text(encoding="utf-8")

    codigo = main(["version", "guagua-pan", "--motivo=   "])

    assert codigo != 0
    assert capsys.readouterr().err
    assert (diseno_v1 / "diseno.json").read_text(encoding="utf-8") == json_antes


# ---------------------------------------------------------------------------
# Diseño inexistente / nombre inválido
# ---------------------------------------------------------------------------


def test_diseno_inexistente_produce_error_en_espanol(
    disenos_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    monkeypatch.setattr("builtins.input", _input_que_no_debe_llamarse)

    codigo = main(_argv_version("no-existe", motivo="algo"))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "no-existe" in error


def test_nombre_invalido_produce_error_sin_preguntar(
    disenos_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    monkeypatch.setattr("builtins.input", _input_que_no_debe_llamarse)

    codigo = main(_argv_version("Guagua De Pan", motivo="algo"))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "Guagua De Pan" in error


# ---------------------------------------------------------------------------
# nombre_archivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("diseno", "version", "pieza", "ext", "esperado"),
    [
        ("guagua-pan", 2, "master", "stl", "guagua-pan_v2_master.stl"),
        ("guagua-pan", 2, "caja-4x", "stl", "guagua-pan_v2_caja-4x.stl"),
        ("guagua-pan", 1, "master", "stl", "guagua-pan_v1_master.stl"),
    ],
)
def test_nombre_archivo_valido(
    diseno: str, version: int, pieza: str, ext: str, esperado: str
) -> None:
    assert nombre_archivo(diseno, version, pieza, ext) == esperado


@pytest.mark.parametrize(
    ("diseno", "version", "pieza", "ext"),
    [
        ("guagua-pan", 0, "master", "stl"),
        ("guagua-pan", -1, "master", "stl"),
        ("guagua-pan", 2, "Master Pieza", "stl"),
        ("guagua-pan", 2, "master", ".stl"),
        ("guagua-pan", 2, "master", ""),
        ("Guagua Pan", 2, "master", "stl"),
    ],
)
def test_nombre_archivo_invalido(diseno: str, version: int, pieza: str, ext: str) -> None:
    with pytest.raises(VersionError):
        nombre_archivo(diseno, version, pieza, ext)


# ---------------------------------------------------------------------------
# registrar_parametros
# ---------------------------------------------------------------------------


def test_registrar_parametros_escribe_en_la_etapa_indicada(diseno_v1: Path) -> None:
    registrar_parametros(diseno_v1, "limpieza", {"caras_max": 200000})

    datos = cargar_diseno(diseno_v1)
    assert datos["versiones"]["v1"]["parametros"]["limpieza"] == {"caras_max": 200000}


def test_registrar_parametros_no_toca_otras_etapas_ni_la_v1_cuando_la_actual_es_v2(
    diseno_v1: Path,
) -> None:
    registrar_parametros(diseno_v1, "limpieza", {"caras_max": 200000})
    assert crear_version(diseno_v1, "nueva version") == 2

    registrar_parametros(diseno_v1, "impresion", {"calidad": "alta"})

    datos = cargar_diseno(diseno_v1)
    v1 = datos["versiones"]["v1"]
    v2 = datos["versiones"]["v2"]

    # La v1 no se tocó: sigue solo con "limpieza".
    assert v1["parametros"] == {"limpieza": {"caras_max": 200000}}
    # La v2 conserva "limpieza" (copiada al crear la versión) y ahora también
    # tiene "impresion"; no se tocó ninguna otra etapa.
    assert v2["parametros"] == {
        "limpieza": {"caras_max": 200000},
        "impresion": {"calidad": "alta"},
    }


# ---------------------------------------------------------------------------
# Atomicidad de guardar_diseno
# ---------------------------------------------------------------------------


def test_guardar_diseno_es_atomico_ante_un_fallo(
    diseno_v1: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_antes = (diseno_v1 / "diseno.json").read_text(encoding="utf-8")

    def _replace_que_falla(*args: object, **kwargs: object) -> None:
        raise OSError("fallo simulado de disco")

    monkeypatch.setattr("castkit.versiones.os.replace", _replace_que_falla)

    datos = cargar_diseno(diseno_v1)
    datos["version_actual"] = 999

    with pytest.raises(OSError):
        guardar_diseno(diseno_v1, datos)

    # El diseno.json original sigue intacto.
    assert (diseno_v1 / "diseno.json").read_text(encoding="utf-8") == json_antes
    # No queda ningún archivo temporal suelto.
    assert list(diseno_v1.glob(".diseno.json.tmp-*")) == []


# ---------------------------------------------------------------------------
# datos_version: diseños nuevos vs. diseños "antiguos" (sin alto_mm/cavidades en la versión)
# ---------------------------------------------------------------------------


def test_datos_version_en_diseno_nuevo_usa_los_datos_de_la_version(diseno_v1: Path) -> None:
    datos = cargar_diseno(diseno_v1)
    resultado = datos_version(datos)
    assert resultado == {"version": 1, "alto_mm": 80, "cavidades": 4, "parametros": {}}


def test_datos_version_en_diseno_antiguo_usa_el_respaldo_de_nivel_superior(
    diseno_v1: Path,
) -> None:
    datos = cargar_diseno(diseno_v1)
    # Simula un diseño creado antes de este ticket: sin alto_mm/cavidades en la versión.
    del datos["versiones"]["v1"]["alto_mm"]
    del datos["versiones"]["v1"]["cavidades"]
    guardar_diseno(diseno_v1, datos)

    datos_releidos = cargar_diseno(diseno_v1)
    resultado = datos_version(datos_releidos)
    assert resultado["alto_mm"] == datos_releidos["medidas"]["alto_mm"]
    assert resultado["cavidades"] == datos_releidos["cavidades"]


def test_crear_version_funciona_sobre_un_diseno_antiguo_sin_alto_mm_en_la_version(
    diseno_v1: Path,
) -> None:
    datos = cargar_diseno(diseno_v1)
    del datos["versiones"]["v1"]["alto_mm"]
    del datos["versiones"]["v1"]["cavidades"]
    guardar_diseno(diseno_v1, datos)

    nueva = crear_version(diseno_v1, "cambio sobre diseño antiguo")
    assert nueva == 2

    datos_finales = cargar_diseno(diseno_v1)
    assert datos_finales["versiones"]["v2"]["alto_mm"] == datos_finales["medidas"]["alto_mm"]
    assert datos_finales["versiones"]["v2"]["cavidades"] == datos_finales["cavidades"]


# ---------------------------------------------------------------------------
# Dos versiones seguidas
# ---------------------------------------------------------------------------


def test_dos_versiones_seguidas_encadenan_bien(diseno_v1: Path) -> None:
    codigo1 = main(_argv_version("guagua-pan", motivo="primer cambio", alto_mm="90"))
    assert codigo1 == 0

    codigo2 = main(_argv_version("guagua-pan", motivo="segundo cambio", cavidades="6"))
    assert codigo2 == 0

    datos = cargar_diseno(diseno_v1)
    assert datos["version_actual"] == 3
    v3 = datos["versiones"]["v3"]
    assert v3["alto_mm"] == 90  # heredado de v2
    assert v3["cavidades"] == 6  # el nuevo indicado
    assert v3["motivo"] == "segundo cambio"

    changelog = (diseno_v1 / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## v2 — " in changelog
    assert "## v3 — " in changelog


# ---------------------------------------------------------------------------
# ubicar_diseno
# ---------------------------------------------------------------------------


def test_ubicar_diseno_devuelve_la_carpeta_correcta(diseno_v1: Path) -> None:
    assert ubicar_diseno("guagua-pan") == diseno_v1


def test_ubicar_diseno_falla_si_no_existe_diseno_json(disenos_dir: Path) -> None:
    carpeta_sin_json = disenos_dir / "sin-metadatos"
    carpeta_sin_json.mkdir()

    with pytest.raises(VersionError):
        ubicar_diseno("sin-metadatos")
