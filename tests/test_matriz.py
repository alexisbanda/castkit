"""Tests de `castkit matriz` y del módulo `castkit.matriz` (ticket #26)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import trimesh

from castkit.cli import main
from castkit.diseno import DatosAlta, crear_diseno
from castkit.ingesta import ingerir_malla
from castkit.limpieza import limpiar_malla
from castkit.versiones import cargar_diseno

FIXTURES = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES / "cube.stl"


def _preparar_diseno_limpio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    nombre: str = "guagua-pan",
    alto_mm: float = 40.0,
    cavidades: int = 4,
) -> Path:
    carpeta_brief = tmp_path / f"brief-{nombre}"
    carpeta_brief.mkdir()
    (carpeta_brief / "ref.txt").write_text("brief", encoding="utf-8")

    destino = tmp_path / "disenos"
    destino.mkdir(exist_ok=True)
    monkeypatch.setenv("CASTKIT_DISENOS", str(destino))

    datos = DatosAlta(
        nombre=nombre,
        brief=carpeta_brief,
        material="porcelana-fria",
        alto_mm=alto_mm,
        cavidades=cavidades,
        cantidad=20,
        fecha=date(2026, 11, 1),
        licencia="propio",
        origen="prueba",
    )
    carpeta = crear_diseno(datos, destino=destino)
    ingerir_malla(carpeta, CUBE_STL)
    limpiar_malla(
        carpeta,
        arriba="z",
        caras_max=200000,
        tolerancia_mm=0.2,
        corte_base_mm=0.5,
        agujero_max_mm=10.0,
    )
    return carpeta


def test_matriz_modo_figura_genera_stl_cerrado_con_base_y_registra_parametros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=40.0, cavidades=4)

    rc = main(["matriz", "guagua-pan", "--modo", "figura"])
    assert rc == 0

    salida = carpeta / "output" / "guagua-pan_v1_matriz.stl"
    assert salida.is_file()

    malla = trimesh.load(salida, file_type="stl", force="mesh")
    assert malla.is_watertight
    assert malla.volume > 0

    min_xyz, max_xyz = malla.bounds
    assert min_xyz[2] == pytest.approx(0.0, abs=0.05)
    # Altura total = espesor_base_mm (3.0) + alto_mm (40.0) = 43.0 mm
    assert (max_xyz[2] - min_xyz[2]) == pytest.approx(43.0, abs=0.2)

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["matriz"]
    assert reg["modo"] == "figura"
    assert reg["cavidades"] == 1
    assert reg["volumen_figura_cm3"] > 0
    assert reg["volumen_silicona_ml"] is None


def test_matriz_modo_cubeta_multicavidad_genera_pieza_unica_cerrada_con_borde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=40.0, cavidades=4)

    rc = main(["matriz", "guagua-pan", "--modo", "cubeta"])
    assert rc == 0

    salida = carpeta / "output" / "guagua-pan_v1_matriz.stl"
    assert salida.is_file()

    malla = trimesh.load(salida, file_type="stl", force="mesh")
    assert malla.is_watertight
    assert malla.volume > 0

    min_xyz, max_xyz = malla.bounds
    assert min_xyz[2] == pytest.approx(0.0, abs=0.05)
    # Altura total en modo cubeta = espesor_base (3) + alto (40) + margen_superior (10) = 53 mm
    assert (max_xyz[2] - min_xyz[2]) == pytest.approx(53.0, abs=0.2)

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["matriz"]
    assert reg["modo"] == "cubeta"
    assert reg["cavidades"] == 4
    assert reg["volumen_figura_cm3"] > 0
    assert reg["volumen_silicona_ml"] is not None
    assert reg["volumen_silicona_ml"] > 0


def test_matriz_sin_modo_pregunta_en_terminal_interactiva(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=30.0, cavidades=2)

    preguntas: list[str] = []

    def fake_input(prompt: str) -> str:
        preguntas.append(prompt)
        return "cubeta"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", fake_input)

    rc = main(["matriz", "guagua-pan"])
    assert rc == 0
    assert len(preguntas) == 1
    assert "figura" in preguntas[0] and "cubeta" in preguntas[0]
    assert (carpeta / "output" / "guagua-pan_v1_matriz.stl").is_file()


def test_matriz_sin_modo_no_interactivo_falla_pidiendo_modo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    rc = main(["matriz", "guagua-pan"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--modo" in err
    assert not (carpeta / "output" / "guagua-pan_v1_matriz.stl").exists()


@pytest.mark.parametrize(
    ("args_extra", "esperado"),
    [
        (["--modo", "invalido"], "--modo"),
        (["--modo", "figura", "--espesor-base-mm", "0"], "--espesor-base-mm"),
        (["--modo", "cubeta", "--margen-silicona-mm", "-2"], "--margen-silicona-mm"),
        (["--modo", "cubeta", "--espesor-pared-mm", "abc"], "--espesor-pared-mm"),
        (["--modo", "cubeta", "--margen-superior-mm", "0"], "--margen-superior-mm"),
        (["--modo", "cubeta", "--separacion-cavidades-mm", "-1"], "--separacion-cavidades-mm"),
    ],
)
def test_matriz_opciones_invalidas_se_rechazan_sin_escribir_nada(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args_extra: list[str],
    esperado: str,
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch)
    rc = main(["matriz", "guagua-pan", *args_extra])
    assert rc == 1
    err = capsys.readouterr().err
    assert esperado in err
    assert not (carpeta / "output" / "guagua-pan_v1_matriz.stl").exists()


def test_matriz_sin_malla_limpia_indica_ejecutar_limpieza(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta_brief = tmp_path / "brief"
    carpeta_brief.mkdir()
    (carpeta_brief / "ref.txt").write_text("brief", encoding="utf-8")

    destino = tmp_path / "disenos"
    destino.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(destino))

    datos = DatosAlta(
        nombre="sin-limpiar",
        brief=carpeta_brief,
        material="cera",
        alto_mm=50.0,
        cavidades=1,
        cantidad=10,
        fecha=date(2026, 11, 1),
        licencia="propio",
        origen="prueba",
    )
    crear_diseno(datos, destino=destino)

    rc = main(["matriz", "sin-limpiar", "--modo", "figura"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "castkit limpieza" in err
