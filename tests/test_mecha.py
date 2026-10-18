"""Tests para `castkit mecha`, fallback de Blender en `limpieza`, proveedores
`fal`/`tripo3d`, `ficha.md` y el comando extremo a extremo `castkit flujo`."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
import trimesh

from castkit.cli import main
from castkit.versiones import cargar_diseno

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES_DIR / "cube.stl"
ROTA_STL = FIXTURES_DIR / "rota.stl"


def _dar_alta(
    nombre: str,
    brief: Path,
    *,
    material: str = "cera",
    alto_mm: float = 20.0,
    cavidades: int = 2,
) -> None:
    assert (
        main(
            [
                "alta",
                nombre,
                f"--alto-mm={alto_mm}",
                f"--material={material}",
                f"--cavidades={cavidades}",
                "--cantidad=10",
                "--fecha=2026-11-01",
                "--origen=propio",
                "--licencia=comercial",
                f"--brief={brief}",
            ]
        )
        == 0
    )


def _preparar_diseno_limpio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    material: str = "cera",
    alto_mm: float = 20.0,
    cavidades: int = 2,
) -> Path:
    disenos_dir = tmp_path / "disenos"
    disenos_dir.mkdir(exist_ok=True)
    monkeypatch.chdir(disenos_dir)
    brief = tmp_path / "brief_src"
    brief.mkdir(exist_ok=True)
    (brief / "pieza.stl").write_bytes(CUBE_STL.read_bytes())

    _dar_alta("vela-andina", brief, material=material, alto_mm=alto_mm, cavidades=cavidades)
    assert main(["generar", "vela-andina"]) == 0
    assert main(["limpieza", "vela-andina"]) == 0
    return disenos_dir / "vela-andina"


def test_mecha_perfora_canal_vertical_en_cera_y_mantiene_malla_cerrada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, material="cera", alto_mm=20.0)
    ruta_limpia = carpeta / "work" / "vela-andina_v1_limpia.stl"
    vol_antes = float(trimesh.load(ruta_limpia).volume)

    rc = main(["mecha", "vela-andina", "--diametro-mm", "3.0"])
    assert rc == 0

    malla = trimesh.load(ruta_limpia)
    assert malla.is_watertight
    assert malla.is_winding_consistent
    assert float(malla.volume) < vol_antes

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["mecha"]
    assert reg["aplicado"] is True
    assert reg["diametro_mecha_mm"] == pytest.approx(3.0)
    assert reg["espesor_pared_medido_mm"] > 1.2


@pytest.mark.parametrize("material", ["porcelana-fria", "silicona"])
def test_mecha_omite_perforacion_si_material_no_es_cera(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    material: str,
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, material=material, alto_mm=20.0)
    ruta_limpia = carpeta / "work" / "vela-andina_v1_limpia.stl"
    sha_antes = hashlib.sha256(ruta_limpia.read_bytes()).hexdigest()

    rc = main(["mecha", "vela-andina"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no requiere canal de mecha" in out
    assert hashlib.sha256(ruta_limpia.read_bytes()).hexdigest() == sha_antes

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["mecha"]
    assert reg["aplicado"] is False
    assert reg["material"] == material


def test_mecha_advierte_y_pide_confirmacion_si_pared_queda_fina(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Cubo de 10 mm de alto (y ~10.5 mm de ancho) con canal de 9 mm -> pared ~0.75 mm (< 1.2 mm)
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, material="cera", alto_mm=10.0)
    ruta_limpia = carpeta / "work" / "vela-andina_v1_limpia.stl"
    sha_antes = hashlib.sha256(ruta_limpia.read_bytes()).hexdigest()

    # 1. No interactivo sin --forzar -> código 2, no modifica el archivo
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc = main(["mecha", "vela-andina", "--diametro-mm", "9.0"])
    assert rc == 2
    assert "--forzar" in capsys.readouterr().err
    assert hashlib.sha256(ruta_limpia.read_bytes()).hexdigest() == sha_antes

    # 2. Interactivo respondiendo "n" -> código 1, no modifica el archivo
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    rc = main(["mecha", "vela-andina", "--diametro-mm", "9.0"])
    assert rc == 1
    assert hashlib.sha256(ruta_limpia.read_bytes()).hexdigest() == sha_antes

    # 3. No interactivo con --forzar -> código 0, perfora e informa el espesor
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc = main(["mecha", "vela-andina", "--diametro-mm", "9.0", "--forzar"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Canal de mecha" in out
    assert hashlib.sha256(ruta_limpia.read_bytes()).hexdigest() != sha_antes


@pytest.mark.parametrize(
    ("flag", "valor"),
    [
        ("--diametro-mm", "0"),
        ("--diametro-mm", "abc"),
        ("--espesor-minimo-mm", "-1"),
    ],
)
def test_mecha_opciones_invalidas_fallan_nombrando_la_opcion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    valor: str,
) -> None:
    _preparar_diseno_limpio(tmp_path, monkeypatch, material="cera")
    rc = main(["mecha", "vela-andina", flag, valor])
    assert rc != 0
    assert flag in capsys.readouterr().err


def test_limpieza_usa_respaldo_de_blender_cuando_python_no_cierra_la_malla(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import castkit.blender as mod_blender
    import castkit.limpieza as mod_limpieza

    disenos_dir = tmp_path / "disenos"
    disenos_dir.mkdir()
    monkeypatch.chdir(disenos_dir)
    brief = tmp_path / "brief_src"
    brief.mkdir()
    (brief / "rota.stl").write_bytes(ROTA_STL.read_bytes())

    _dar_alta("pieza-rota", brief, material="cera", alto_mm=15.0, cavidades=1)
    assert main(["generar", "pieza-rota"]) == 0

    # 1. Sin Blender instalado, falla e incluye nota sobre BLENDER_BIN
    def _sin_blender() -> Path:
        raise mod_blender.BlenderNotFoundError("no hay blender")

    monkeypatch.setattr(mod_blender, "find_blender", _sin_blender)
    rc = main(["limpieza", "pieza-rota"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "BLENDER_BIN" in err

    # 2. Con --sin-blender falla directamente sin nota de BLENDER_BIN
    rc = main(["limpieza", "pieza-rota", "--sin-blender"])
    assert rc != 0
    err2 = capsys.readouterr().err
    assert "BLENDER_BIN" not in err2

    # 3. Con Blender disponible pero un remallado que deforma la figura (una
    # caja), la limpieza lo rechaza: la fidelidad se mide contra la original.
    monkeypatch.setattr(mod_blender, "find_blender", lambda: Path("/usr/bin/blender"))

    def _fake_blender(malla: trimesh.Trimesh, voxel_mm: float) -> trimesh.Trimesh:
        caja = trimesh.creation.box(extents=malla.extents)
        caja.apply_translation(malla.bounds[0] - caja.bounds[0])
        return caja

    monkeypatch.setattr(mod_limpieza, "_remallar_con_blender", _fake_blender)
    rc = main(["limpieza", "pieza-rota", "--voxel-mm", "0.25"])
    assert rc != 0
    assert "deformó la figura" in capsys.readouterr().err


def test_proveedores_fal_y_tripo3d_validan_api_key_y_descargan_stl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import urllib.request

    disenos_dir = tmp_path / "disenos"
    disenos_dir.mkdir()
    monkeypatch.chdir(disenos_dir)
    brief = tmp_path / "brief_src"
    brief.mkdir()
    (brief / "ref.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    _dar_alta("vela-ia", brief, material="cera", alto_mm=25.0, cavidades=1)

    # Sin FAL_KEY falla indicando la variable
    monkeypatch.delenv("FAL_KEY", raising=False)
    rc = main(["generar", "vela-ia", "--proveedor", "fal"])
    assert rc != 0
    assert "FAL_KEY" in capsys.readouterr().err

    # Sin TRIPO_API_KEY falla indicando la variable
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    rc = main(["generar", "vela-ia", "--proveedor", "tripo3d"])
    assert rc != 0
    assert "TRIPO_API_KEY" in capsys.readouterr().err

    # Con FAL_KEY y respuesta HTTP simulada descarga y convierte el modelo a STL
    monkeypatch.setenv("FAL_KEY", "test-fal-key")
    stl_bytes = CUBE_STL.read_bytes()

    class _FakeResp:
        def __init__(self, payload: bytes) -> None:
            self._buf = io.BytesIO(payload)

        def read(self) -> bytes:
            return self._buf.read()

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    def _fake_urlopen(req: object, timeout: float = 60) -> _FakeResp:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "fal.run" in url:
            return _FakeResp(
                json.dumps({"model_mesh": {"url": "https://cdn.fal.ai/mesh.stl"}}).encode()
            )
        return _FakeResp(stl_bytes)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    rc = main(["generar", "vela-ia", "--proveedor", "fal"])
    assert rc == 0
    assert (disenos_dir / "vela-ia" / "input" / "vela-ia_v1_bruto.stl").is_file()


def test_flujo_completo_genera_matriz_y_ficha_tecnica_en_espanol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disenos_dir = tmp_path / "disenos"
    disenos_dir.mkdir()
    monkeypatch.chdir(disenos_dir)
    brief = tmp_path / "brief_src"
    brief.mkdir()
    (brief / "modelo.stl").write_bytes(CUBE_STL.read_bytes())

    _dar_alta("vela-flujo", brief, material="cera", alto_mm=20.0, cavidades=2)

    rc = main(["flujo", "vela-flujo", "--modo", "cubeta", "--aprobar-control1"])
    assert rc == 0

    carpeta = disenos_dir / "vela-flujo"
    ruta_matriz = carpeta / "output" / "vela-flujo_v1_matriz.stl"
    ruta_ficha = carpeta / "output" / "vela-flujo_v1_ficha.md"
    assert ruta_matriz.is_file()
    assert ruta_ficha.is_file()

    ficha_txt = ruta_ficha.read_text(encoding="utf-8")
    assert "# Ficha técnica: vela-flujo (v1)" in ficha_txt
    assert "Silicona líquida estimada:" in ficha_txt
    assert "canal de mecha" in ficha_txt

    datos = cargar_diseno(carpeta)
    parametros = datos["versiones"]["v1"]["parametros"]
    assert "generacion" in parametros
    assert "limpieza" in parametros
    assert parametros["mecha"]["aplicado"] is True
    assert parametros["matriz"]["modo"] == "cubeta"


def test_flujo_sin_terminal_se_detiene_en_control1_sin_aprobar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disenos_dir = tmp_path / "disenos"
    disenos_dir.mkdir()
    monkeypatch.chdir(disenos_dir)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    brief = tmp_path / "brief_src"
    brief.mkdir()
    (brief / "modelo.stl").write_bytes(CUBE_STL.read_bytes())

    _dar_alta("vela-gate", brief, material="cera", alto_mm=20.0, cavidades=2)

    rc = main(["flujo", "vela-gate", "--modo", "cubeta"])
    assert rc == 1
    assert "punto de control 1" in capsys.readouterr().err

    carpeta = disenos_dir / "vela-gate"
    reg = cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]["control_1"]
    assert reg["estado"] == "pendiente"
    assert not (carpeta / "output" / "vela-gate_v1_matriz.stl").exists()
