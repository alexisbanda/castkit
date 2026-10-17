"""Tests de la limpieza y escalado automático de la malla (`castkit limpieza`)."""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from castkit.cli import main
from castkit.versiones import cargar_diseno

FIXTURES = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES / "cube.stl"
AGUJEROS_STL = FIXTURES / "agujeros.stl"
ROTA_STL = FIXTURES / "rota.stl"
TUMBADA_STL = FIXTURES / "tumbada.stl"

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

LIMPIEZA_FLAGS = {
    "arriba": "--arriba",
    "caras_max": "--caras-max",
    "tolerancia_mm": "--tolerancia-mm",
    "corte_base_mm": "--corte-base-mm",
    "agujero_max_mm": "--agujero-max-mm",
}


def _argv_alta(nombre: str, brief: str, alto_mm: float = 13.5, **overrides: str) -> list[str]:
    valores = {
        "brief": brief,
        "material": "porcelana-fria",
        "alto_mm": str(alto_mm),
        "cavidades": "1",
        "cantidad": "1",
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


def _argv_limpieza(nombre: str, **opciones: str) -> list[str]:
    argv = ["limpieza", nombre]
    for clave, valor in opciones.items():
        argv.append(f"{LIMPIEZA_FLAGS[clave]}={valor}")
    return argv


@pytest.fixture(autouse=True)
def _stdin_no_interactivo(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StdinNoInteractivo:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("sys.stdin", _StdinNoInteractivo())


@pytest.fixture
def brief_dir(tmp_path: Path) -> Path:
    carpeta = tmp_path / "brief-guagua"
    carpeta.mkdir()
    (carpeta / "especificacion.txt").write_text("Guagua de pan.\n", encoding="utf-8")
    return carpeta


@pytest.fixture
def disenos_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    carpeta = tmp_path / "disenos"
    carpeta.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta))
    return carpeta


def _dar_alta(nombre: str, brief_dir: Path, alto_mm: float) -> None:
    codigo = main(_argv_alta(nombre, str(brief_dir), alto_mm=alto_mm))
    assert codigo == 0


# ---------------------------------------------------------------------------
# Criterio: malla con agujeros chicos queda cerrada, con normales
# consistentes, volumen > 0 y termina con código 0.
# ---------------------------------------------------------------------------


def test_limpieza_cierra_agujeros_y_queda_watertight(disenos_dir: Path, brief_dir: Path) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(AGUJEROS_STL))) == 0

    codigo = main(_argv_limpieza("guagua-pan"))
    assert codigo == 0

    destino = disenos_dir / "guagua-pan" / "work" / "guagua-pan_v1_limpia.stl"
    assert destino.is_file()

    malla = trimesh.load(destino)
    assert malla.is_watertight
    assert malla.is_winding_consistent
    assert malla.volume > 0


def test_limpieza_registra_todos_los_parametros_requeridos(
    disenos_dir: Path, brief_dir: Path
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(AGUJEROS_STL))) == 0
    assert main(_argv_limpieza("guagua-pan")) == 0

    datos = cargar_diseno(disenos_dir / "guagua-pan")
    parametros = datos["versiones"]["v1"]["parametros"]["limpieza"]

    assert parametros["caras_max"] == 200000
    assert parametros["tolerancia_mm"] == pytest.approx(0.2)
    assert parametros["corte_base_mm"] == pytest.approx(0.5)
    assert parametros["arriba"] == "z"
    assert parametros["version_bruto"] == 1
    assert len(parametros["sha256"]) == 64
    assert parametros["caras"] > 0
    assert parametros["distancia_max_mm"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Criterio: caras_max y tolerancia_mm (con --caras-max y --tolerancia-mm
# forzando la simplificación).
# ---------------------------------------------------------------------------


def test_caras_max_y_tolerancia_se_respetan_tras_simplificar(
    disenos_dir: Path, brief_dir: Path
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(AGUJEROS_STL))) == 0

    codigo = main(_argv_limpieza("guagua-pan", caras_max="40", tolerancia_mm="5"))
    assert codigo == 0

    destino = disenos_dir / "guagua-pan" / "work" / "guagua-pan_v1_limpia.stl"
    malla = trimesh.load(destino)
    assert len(malla.faces) <= 40

    datos = cargar_diseno(disenos_dir / "guagua-pan")
    parametros = datos["versiones"]["v1"]["parametros"]["limpieza"]
    assert parametros["caras_max"] == 40
    assert parametros["distancia_max_mm"] <= 5.0


# ---------------------------------------------------------------------------
# Criterio: alto_mm (±0.1 mm) y proporciones ancho/fondo/alto (±0.5 %).
# ---------------------------------------------------------------------------


def test_altura_final_y_proporciones_se_conservan(disenos_dir: Path, brief_dir: Path) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=50.0)
    assert main(_argv_ingesta("guagua-pan", str(TUMBADA_STL))) == 0
    assert main(_argv_limpieza("guagua-pan")) == 0

    destino = disenos_dir / "guagua-pan" / "work" / "guagua-pan_v1_limpia.stl"
    malla = trimesh.load(destino)

    alto_final = malla.bounds[1][2] - malla.bounds[0][2]
    assert alto_final == pytest.approx(50.0, abs=0.1)

    ancho, fondo, _alto = malla.extents
    # tumbada.stl mide 30 (X) x 20 (Y) x 10 (Z); con --arriba z (por defecto)
    # el alto es el eje Z, así que ancho/fondo deben conservar la razón 30/20.
    assert (ancho / fondo) == pytest.approx(30.0 / 20.0, rel=0.005)


# ---------------------------------------------------------------------------
# Criterio: la base queda plana en z = 0, recortada corte_base_mm desde
# abajo, con caras planas de área > 0.
# ---------------------------------------------------------------------------


def test_base_queda_plana_y_recortada(disenos_dir: Path, brief_dir: Path) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=9.5)
    assert main(_argv_ingesta("guagua-pan", str(CUBE_STL))) == 0
    assert main(_argv_limpieza("guagua-pan")) == 0

    destino = disenos_dir / "guagua-pan" / "work" / "guagua-pan_v1_limpia.stl"
    malla = trimesh.load(destino)

    assert malla.bounds[0][2] == pytest.approx(0.0, abs=0.01)
    assert malla.bounds[1][2] - malla.bounds[0][2] == pytest.approx(9.5, abs=0.1)

    caras = malla.faces
    vertices_z = malla.vertices[caras][:, :, 2]
    en_base = (abs(vertices_z) < 1e-6).all(axis=1)
    assert en_base.any()
    assert malla.area_faces[en_base].sum() > 0


# ---------------------------------------------------------------------------
# Criterio: --arriba acepta x, -x, y, -y, z, -z y rota antes de escalar;
# otro valor produce un error listando los valores aceptados.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arriba", "alto_mm"),
    [("x", 29.5), ("-x", 29.5), ("y", 19.5), ("-y", 19.5), ("z", 9.5), ("-z", 9.5)],
)
def test_arriba_acepta_los_seis_valores_y_reorienta(
    disenos_dir: Path, brief_dir: Path, arriba: str, alto_mm: float
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=alto_mm)
    assert main(_argv_ingesta("guagua-pan", str(TUMBADA_STL))) == 0

    codigo = main(_argv_limpieza("guagua-pan", arriba=arriba))
    assert codigo == 0

    destino = disenos_dir / "guagua-pan" / "work" / "guagua-pan_v1_limpia.stl"
    malla = trimesh.load(destino)
    alto_final = malla.bounds[1][2] - malla.bounds[0][2]
    assert alto_final == pytest.approx(alto_mm, abs=0.1)


def test_arriba_invalido_produce_error_con_valores_aceptados(
    disenos_dir: Path, brief_dir: Path
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(AGUJEROS_STL))) == 0

    import io
    import sys

    captura = io.StringIO()
    anterior = sys.stderr
    sys.stderr = captura
    try:
        codigo = main(_argv_limpieza("guagua-pan", arriba="w"))
    finally:
        sys.stderr = anterior

    assert codigo != 0
    error = captura.getvalue()
    assert "--arriba" in error
    for valor in ("x", "-x", "y", "-y", "z", "-z"):
        assert valor in error


# ---------------------------------------------------------------------------
# Criterio: si la versión actual no tiene malla en bruto, se usa la de la
# versión anterior más reciente que la tenga, se avisa y se registra.
# ---------------------------------------------------------------------------


def test_usa_malla_en_bruto_de_version_anterior_y_avisa(
    disenos_dir: Path, brief_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=9.5)
    assert main(_argv_ingesta("guagua-pan", str(CUBE_STL))) == 0
    assert main(["version", "guagua-pan", "--motivo=nueva version"]) == 0

    codigo = main(_argv_limpieza("guagua-pan"))
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "v1" in salida
    assert "v2" in salida

    destino = disenos_dir / "guagua-pan" / "work" / "guagua-pan_v2_limpia.stl"
    assert destino.is_file()

    datos = cargar_diseno(disenos_dir / "guagua-pan")
    assert datos["versiones"]["v2"]["parametros"]["limpieza"]["version_bruto"] == 1


def test_ninguna_version_con_malla_pide_ingesta(disenos_dir: Path, brief_dir: Path) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)

    codigo = main(_argv_limpieza("guagua-pan"))

    assert codigo != 0


# ---------------------------------------------------------------------------
# Criterio: malla no cerrable produce error ≠ 0, no escribe la malla limpia
# y el mensaje indica cuántas zonas, su posición/tamaño aproximados y los
# siguientes pasos.
# ---------------------------------------------------------------------------


def test_malla_no_cerrable_falla_sin_escribir_archivo(
    disenos_dir: Path, brief_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(ROTA_STL))) == 0

    codigo = main(_argv_limpieza("guagua-pan"))

    assert codigo != 0
    destino = disenos_dir / "guagua-pan" / "work" / "guagua-pan_v1_limpia.stl"
    assert not destino.exists()

    error = capsys.readouterr().err
    assert "1 zona" in error or "zona(s)" in error
    assert "mm" in error
    assert "siguientes pasos" in error.lower() or "Siguientes pasos" in error


# ---------------------------------------------------------------------------
# Criterio: la configuración se lee de config/fabricacion.toml y las
# opciones de la CLI la sobrescriben; un valor inválido nombra la opción.
# ---------------------------------------------------------------------------


def test_opcion_sobrescribe_config_sin_tocar_las_demas(disenos_dir: Path, brief_dir: Path) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(AGUJEROS_STL))) == 0

    codigo = main(_argv_limpieza("guagua-pan", corte_base_mm="1.0"))
    assert codigo == 0

    datos = cargar_diseno(disenos_dir / "guagua-pan")
    parametros = datos["versiones"]["v1"]["parametros"]["limpieza"]
    assert parametros["corte_base_mm"] == pytest.approx(1.0)
    assert parametros["caras_max"] == 200000
    assert parametros["tolerancia_mm"] == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("opcion", "valor"),
    [
        ("caras_max", "no-es-un-entero"),
        ("tolerancia_mm", "-1"),
        ("corte_base_mm", "0"),
        ("agujero_max_mm", "-5"),
    ],
)
def test_opcion_invalida_nombra_la_opcion_en_el_error(
    disenos_dir: Path,
    brief_dir: Path,
    capsys: pytest.CaptureFixture[str],
    opcion: str,
    valor: str,
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(AGUJEROS_STL))) == 0

    codigo = main(_argv_limpieza("guagua-pan", **{opcion: valor}))

    assert codigo != 0
    error = capsys.readouterr().err
    assert LIMPIEZA_FLAGS[opcion] in error


def test_agujero_max_mm_mas_estricto_rechaza_huecos_que_antes_pasaban(
    disenos_dir: Path, brief_dir: Path
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(AGUJEROS_STL))) == 0

    # Con el valor por defecto (10 mm) los huecos de agujeros.stl se cierran.
    assert main(_argv_limpieza("guagua-pan")) == 0

    # Con un --agujero-max-mm más chico que esos huecos (~6.3 mm), fallan.
    codigo = main(_argv_limpieza("guagua-pan", agujero_max_mm="5"))
    assert codigo != 0


# ---------------------------------------------------------------------------
# Criterio: volver a limpiar la misma versión reemplaza el archivo y el
# registro, avisando en pantalla.
# ---------------------------------------------------------------------------


def test_reejecutar_limpieza_reemplaza_archivo_y_avisa(
    disenos_dir: Path, brief_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _dar_alta("guagua-pan", brief_dir, alto_mm=13.5)
    assert main(_argv_ingesta("guagua-pan", str(AGUJEROS_STL))) == 0

    assert main(_argv_limpieza("guagua-pan")) == 0
    capsys.readouterr()

    codigo = main(_argv_limpieza("guagua-pan", caras_max="40", tolerancia_mm="5"))
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "reemplazó" in salida

    datos = cargar_diseno(disenos_dir / "guagua-pan")
    assert datos["versiones"]["v1"]["parametros"]["limpieza"]["caras_max"] == 40
