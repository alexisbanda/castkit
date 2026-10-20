"""Tests del ticket #55: tipo de molde (`abierto`/`completo`) y orientación automática.

Cubre: `castkit alta --tipo-molde`, `castkit version --tipo-molde`, la limpieza de un molde
`abierto` (orientación automática, escalado por `largo_mm` y corte por `grosor_mm`), `castkit
mecha` en molde abierto y `castkit matriz` (ficha técnica) con un diseño `abierto`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from castkit.cli import main
from castkit.control1 import registrar_decision_control1
from castkit.diseno import DatosAlta, crear_diseno
from castkit.ingesta import ingerir_malla
from castkit.limpieza import limpiar_malla
from castkit.versiones import cargar_diseno

FIXTURES = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES / "cube.stl"

_ALTA_FLAGS = {
    "brief": "--brief",
    "material": "--material",
    "tipo_molde": "--tipo-molde",
    "alto_mm": "--alto-mm",
    "largo_mm": "--largo-mm",
    "grosor_mm": "--grosor-mm",
    "cavidades": "--cavidades",
    "cantidad": "--cantidad",
    "fecha": "--fecha",
    "licencia": "--licencia",
    "origen": "--origen",
}


def _argv_alta(nombre: str, brief: str, **overrides: str | None) -> list[str]:
    valores: dict[str, str | None] = {
        "brief": brief,
        "material": "porcelana-fria",
        "tipo_molde": None,
        "alto_mm": None,
        "largo_mm": None,
        "grosor_mm": None,
        "cavidades": "1",
        "cantidad": "1",
        "fecha": "2026-11-01",
        "licencia": "propio",
        "origen": "prueba",
    }
    valores.update(overrides)
    argv = ["alta", nombre]
    for clave, valor in valores.items():
        if valor is not None:
            argv.append(f"{_ALTA_FLAGS[clave]}={valor}")
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


@pytest.fixture
def brief_dir(tmp_path: Path) -> Path:
    carpeta = tmp_path / "brief-guagua"
    carpeta.mkdir()
    (carpeta / "especificacion.txt").write_text("Guagua tumbada.\n", encoding="utf-8")
    return carpeta


@pytest.fixture
def disenos_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    carpeta = tmp_path / "disenos"
    carpeta.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta))
    return carpeta


# ---------------------------------------------------------------------------
# castkit alta --tipo-molde
# ---------------------------------------------------------------------------


def test_alta_abierto_con_largo_y_grosor(disenos_dir: Path, brief_dir: Path) -> None:
    codigo = main(
        _argv_alta(
            "guagua-tumbada",
            str(brief_dir),
            tipo_molde="abierto",
            largo_mm="100",
            grosor_mm="20",
        )
    )
    assert codigo == 0

    datos = cargar_diseno(disenos_dir / "guagua-tumbada")
    v1 = datos["versiones"]["v1"]
    assert v1["tipo_molde"] == "abierto"
    assert v1["largo_mm"] == 100
    assert v1["grosor_mm"] == 20
    assert v1["alto_mm"] is None


def test_alta_abierto_con_solo_largo(disenos_dir: Path, brief_dir: Path) -> None:
    codigo = main(
        _argv_alta("guagua-tumbada", str(brief_dir), tipo_molde="abierto", largo_mm="100")
    )
    assert codigo == 0

    datos = cargar_diseno(disenos_dir / "guagua-tumbada")
    v1 = datos["versiones"]["v1"]
    assert v1["tipo_molde"] == "abierto"
    assert v1["largo_mm"] == 100
    assert v1["grosor_mm"] is None


def test_alta_completo_con_solo_alto_mantiene_compatibilidad(
    disenos_dir: Path, brief_dir: Path
) -> None:
    codigo = main(_argv_alta("guagua-pan", str(brief_dir), alto_mm="80"))
    assert codigo == 0

    datos = cargar_diseno(disenos_dir / "guagua-pan")
    v1 = datos["versiones"]["v1"]
    assert v1["tipo_molde"] == "completo"
    assert v1["alto_mm"] == 80
    assert v1["largo_mm"] is None
    assert v1["grosor_mm"] is None


def test_alta_tipo_molde_explicito_completo_sin_alto_pide_alto(
    disenos_dir: Path, brief_dir: Path
) -> None:
    codigo = main(_argv_alta("guagua-pan", str(brief_dir), tipo_molde="completo"))
    assert codigo != 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"tipo_molde": "completo", "largo_mm": "100"},
        {"tipo_molde": "completo", "grosor_mm": "20"},
        {"tipo_molde": "abierto", "alto_mm": "80"},
    ],
)
def test_alta_combinacion_invalida_de_tipo_y_medidas_se_rechaza(
    disenos_dir: Path,
    brief_dir: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
) -> None:
    codigo = main(_argv_alta("guagua-pan", str(brief_dir), **overrides))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "--tipo-molde" in error
    assert list(disenos_dir.iterdir()) == []


def test_alta_sin_terminal_y_sin_medidas_falla_listando_largo_mm(
    disenos_dir: Path, brief_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = main(_argv_alta("guagua-pan", str(brief_dir)))

    assert codigo != 0
    error = capsys.readouterr().err
    assert "--largo-mm" in error
    assert list(disenos_dir.iterdir()) == []


def test_alta_interactivo_pregunta_tipo_molde_con_abierto_por_defecto(
    disenos_dir: Path, brief_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    preguntas: list[str] = []

    def _input_falso(prompt: str = "") -> str:
        preguntas.append(prompt)
        if "Tipo de molde" in prompt:
            return ""  # acepta el valor por defecto propuesto: abierto
        if "Largo" in prompt:
            return "100"
        raise AssertionError(f"Pregunta inesperada: {prompt!r}")

    monkeypatch.setattr("builtins.input", _input_falso)

    codigo = main(_argv_alta("guagua-tumbada", str(brief_dir)))

    assert codigo == 0
    assert len(preguntas) == 2
    assert "abierto" in preguntas[0].lower()
    datos = cargar_diseno(disenos_dir / "guagua-tumbada")
    v1 = datos["versiones"]["v1"]
    assert v1["tipo_molde"] == "abierto"
    assert v1["largo_mm"] == 100


def test_alta_interactivo_puede_elegir_completo_explicitamente(
    disenos_dir: Path, brief_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)

    def _input_falso(prompt: str = "") -> str:
        if "Tipo de molde" in prompt:
            return "completo"
        if "Alto" in prompt:
            return "80"
        raise AssertionError(f"Pregunta inesperada: {prompt!r}")

    monkeypatch.setattr("builtins.input", _input_falso)

    codigo = main(_argv_alta("guagua-pan", str(brief_dir)))

    assert codigo == 0
    datos = cargar_diseno(disenos_dir / "guagua-pan")
    assert datos["versiones"]["v1"]["tipo_molde"] == "completo"
    assert datos["versiones"]["v1"]["alto_mm"] == 80


# ---------------------------------------------------------------------------
# castkit version: hereda y cambia tipo/largo/grosor
# ---------------------------------------------------------------------------


@pytest.fixture
def diseno_abierto_v1(disenos_dir: Path, brief_dir: Path) -> Path:
    codigo = main(
        _argv_alta(
            "guagua-tumbada",
            str(brief_dir),
            tipo_molde="abierto",
            largo_mm="100",
            grosor_mm="20",
        )
    )
    assert codigo == 0
    return disenos_dir / "guagua-tumbada"


def test_version_hereda_tipo_largo_y_grosor(diseno_abierto_v1: Path) -> None:
    codigo = main(["version", "guagua-tumbada", "--motivo=sin cambios de medida"])
    assert codigo == 0

    datos = cargar_diseno(diseno_abierto_v1)
    v2 = datos["versiones"]["v2"]
    assert v2["tipo_molde"] == "abierto"
    assert v2["largo_mm"] == 100
    assert v2["grosor_mm"] == 20


def test_version_cambia_largo_sin_tocar_tipo_ni_grosor(diseno_abierto_v1: Path) -> None:
    codigo = main(["version", "guagua-tumbada", "--motivo=largo nuevo", "--largo-mm=150"])
    assert codigo == 0

    datos = cargar_diseno(diseno_abierto_v1)
    v2 = datos["versiones"]["v2"]
    assert v2["tipo_molde"] == "abierto"
    assert v2["largo_mm"] == 150
    assert v2["grosor_mm"] == 20

    changelog = (diseno_abierto_v1 / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- Largo: 100 mm → 150 mm" in changelog


def test_version_cambia_a_completo_limpia_largo_y_grosor(diseno_abierto_v1: Path) -> None:
    codigo = main(
        [
            "version",
            "guagua-tumbada",
            "--motivo=pasar a completo",
            "--tipo-molde=completo",
            "--alto-mm=80",
        ]
    )
    assert codigo == 0

    datos = cargar_diseno(diseno_abierto_v1)
    v2 = datos["versiones"]["v2"]
    assert v2["tipo_molde"] == "completo"
    assert v2["alto_mm"] == 80
    assert v2["largo_mm"] is None
    assert v2["grosor_mm"] is None

    changelog = (diseno_abierto_v1 / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- Tipo de molde: abierto → completo" in changelog


def test_version_cambiar_a_completo_sin_alto_falla(diseno_abierto_v1: Path) -> None:
    codigo = main(
        ["version", "guagua-tumbada", "--motivo=pasar a completo", "--tipo-molde=completo"]
    )
    assert codigo != 0

    datos = cargar_diseno(diseno_abierto_v1)
    assert "v2" not in datos["versiones"]


def test_version_combinacion_invalida_de_tipo_y_medidas_se_rechaza(
    diseno_abierto_v1: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = main(
        [
            "version",
            "guagua-tumbada",
            "--motivo=combinación inválida",
            "--tipo-molde=completo",
            "--largo-mm=50",
        ]
    )

    assert codigo != 0
    error = capsys.readouterr().err
    assert "--tipo-molde" in error
    datos = cargar_diseno(diseno_abierto_v1)
    assert "v2" not in datos["versiones"]


# ---------------------------------------------------------------------------
# castkit limpieza en molde abierto: orientación automática, escalado por
# largo_mm, corte por grosor_mm, espalda plana en z = 0.
# ---------------------------------------------------------------------------


@pytest.fixture
def pieza_de_pie_stl(tmp_path: Path) -> Path:
    """Caja de 40 (X) × 50 (Y) × 100 (Z): "de pie", con el grosor (40) en X.

    Tras orientar automáticamente (el eje de menor extensión, X, hacia +Z) y
    escalar para que la mayor extensión horizontal sea `largo_mm`, el grosor
    total de la figura escalada queda determinado por este mismo valor (40),
    multiplicado por el factor de escala.
    """
    caja = trimesh.creation.box(extents=(40.0, 50.0, 100.0))
    ruta = tmp_path / "de-pie.stl"
    caja.export(ruta, file_type="stl")
    return ruta


def _dar_alta_abierto(
    disenos_dir: Path,
    brief_dir: Path,
    nombre: str,
    *,
    largo_mm: float,
    grosor_mm: float | None = None,
) -> Path:
    overrides: dict[str, str | None] = {"tipo_molde": "abierto", "largo_mm": str(largo_mm)}
    if grosor_mm is not None:
        overrides["grosor_mm"] = str(grosor_mm)
    assert main(_argv_alta(nombre, str(brief_dir), **overrides)) == 0
    return disenos_dir / nombre


def test_limpieza_abierta_orientacion_automatica_y_mitad_de_grosor(
    disenos_dir: Path, brief_dir: Path, pieza_de_pie_stl: Path
) -> None:
    carpeta = _dar_alta_abierto(disenos_dir, brief_dir, "guagua-tumbada", largo_mm=100.0)
    assert main(["ingesta", "guagua-tumbada", str(pieza_de_pie_stl)]) == 0

    codigo = main(["limpieza", "guagua-tumbada"])
    assert codigo == 0

    destino = carpeta / "work" / "guagua-tumbada_v1_limpia.stl"
    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight
    assert malla.volume > 0

    ancho, fondo, alto = malla.extents
    assert max(ancho, fondo) == pytest.approx(100.0, abs=0.05)
    # Grosor total en bruto es 40 mm; sin --grosor-mm se corta por la mitad.
    assert alto == pytest.approx(20.0, abs=0.05)

    # Cara inferior plana en z = 0.
    assert malla.bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    caras = malla.faces
    vertices_z = malla.vertices[caras][:, :, 2]
    en_base = (abs(vertices_z) < 1e-6).all(axis=1)
    assert en_base.any()
    area_base = malla.area_faces[en_base].sum()
    assert area_base == pytest.approx(ancho * fondo, rel=0.05)

    datos = cargar_diseno(carpeta)
    registro = datos["versiones"]["v1"]["parametros"]["limpieza"]
    assert registro["tipo_molde"] == "abierto"
    assert registro["arriba_automatico"] is True
    assert registro["arriba"] == "x"
    assert registro["largo_mm"] == 100.0
    assert registro["grosor_mm"] == pytest.approx(20.0, abs=0.05)
    assert registro["corte_base_mm"] == 0.0
    assert registro["plano_corte_mm"] is not None


def test_limpieza_abierta_con_grosor_explicito_corta_esa_medida(
    disenos_dir: Path, brief_dir: Path, pieza_de_pie_stl: Path
) -> None:
    carpeta = _dar_alta_abierto(
        disenos_dir, brief_dir, "guagua-tumbada", largo_mm=100.0, grosor_mm=15.0
    )
    assert main(["ingesta", "guagua-tumbada", str(pieza_de_pie_stl)]) == 0

    codigo = main(["limpieza", "guagua-tumbada"])
    assert codigo == 0

    destino = carpeta / "work" / "guagua-tumbada_v1_limpia.stl"
    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight

    _ancho, _fondo, alto = malla.extents
    assert alto == pytest.approx(15.0, abs=0.05)

    datos = cargar_diseno(carpeta)
    registro = datos["versiones"]["v1"]["parametros"]["limpieza"]
    assert registro["grosor_mm"] == pytest.approx(15.0, abs=0.05)


def test_limpieza_abierta_arriba_explicito_manda_sobre_automatico(
    disenos_dir: Path, brief_dir: Path, pieza_de_pie_stl: Path
) -> None:
    carpeta = _dar_alta_abierto(disenos_dir, brief_dir, "guagua-tumbada", largo_mm=100.0)
    assert main(["ingesta", "guagua-tumbada", str(pieza_de_pie_stl)]) == 0

    codigo = main(["limpieza", "guagua-tumbada", "--arriba", "y"])
    assert codigo == 0

    datos = cargar_diseno(carpeta)
    registro = datos["versiones"]["v1"]["parametros"]["limpieza"]
    assert registro["arriba"] == "y"
    assert registro["arriba_automatico"] is False


def test_limpieza_abierta_grosor_mayor_que_el_total_falla(
    disenos_dir: Path, brief_dir: Path, pieza_de_pie_stl: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # El grosor total en bruto es 40 mm (con largo_mm=100, factor de escala 1).
    carpeta = _dar_alta_abierto(
        disenos_dir, brief_dir, "guagua-tumbada", largo_mm=100.0, grosor_mm=45.0
    )
    assert main(["ingesta", "guagua-tumbada", str(pieza_de_pie_stl)]) == 0

    codigo = main(["limpieza", "guagua-tumbada"])

    assert codigo != 0
    destino = carpeta / "work" / "guagua-tumbada_v1_limpia.stl"
    assert not destino.exists()
    error = capsys.readouterr().err
    assert "grosor" in error.lower()
    assert "40" in error


def test_limpieza_completa_sigue_igual_que_antes(
    disenos_dir: Path, brief_dir: Path, pieza_de_pie_stl: Path
) -> None:
    """Un diseño `completo` (comportamiento preexistente) no se ve afectado."""
    codigo_alta = main(_argv_alta("guagua-pan", str(brief_dir), alto_mm="50"))
    assert codigo_alta == 0
    carpeta = disenos_dir / "guagua-pan"
    assert main(["ingesta", "guagua-pan", str(pieza_de_pie_stl)]) == 0

    codigo = main(["limpieza", "guagua-pan"])
    assert codigo == 0

    datos = cargar_diseno(carpeta)
    registro = datos["versiones"]["v1"]["parametros"]["limpieza"]
    assert registro["tipo_molde"] == "completo"
    assert registro["arriba"] == "z"
    assert registro["arriba_automatico"] is False
    assert registro["largo_mm"] is None
    assert registro["grosor_mm"] is None
    assert registro["corte_base_mm"] == pytest.approx(0.5)  # valor por defecto


# ---------------------------------------------------------------------------
# castkit mecha en molde abierto: no perfora, avisa y registra aplicado: false.
# ---------------------------------------------------------------------------


def _preparar_diseno_abierto_de_cera_limpio(
    disenos_dir: Path, brief_dir: Path, pieza_de_pie_stl: Path
) -> Path:
    carpeta = _dar_alta_abierto(
        disenos_dir,
        brief_dir,
        "vela-tumbada",
        largo_mm=100.0,
        grosor_mm=20.0,
    )
    # El material se fija en el alta; lo cambiamos tras crear el diseño
    # editando diseno.json directamente, ya que `castkit alta` no permite
    # material "cera" en este helper genérico de prueba.
    datos = cargar_diseno(carpeta)
    datos["material"] = "cera"
    from castkit.versiones import guardar_diseno

    guardar_diseno(carpeta, datos)

    assert main(["ingesta", "vela-tumbada", str(pieza_de_pie_stl)]) == 0
    assert main(["limpieza", "vela-tumbada"]) == 0
    return carpeta


def test_mecha_en_molde_abierto_no_perfora_y_avisa(
    disenos_dir: Path,
    brief_dir: Path,
    pieza_de_pie_stl: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    carpeta = _preparar_diseno_abierto_de_cera_limpio(disenos_dir, brief_dir, pieza_de_pie_stl)
    ruta_limpia = carpeta / "work" / "vela-tumbada_v1_limpia.stl"
    import hashlib

    sha_antes = hashlib.sha256(ruta_limpia.read_bytes()).hexdigest()

    codigo = main(["mecha", "vela-tumbada"])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "el canal de mecha solo se hace en molde completo" in salida
    assert hashlib.sha256(ruta_limpia.read_bytes()).hexdigest() == sha_antes

    datos = cargar_diseno(carpeta)
    registro = datos["versiones"]["v1"]["parametros"]["mecha"]
    assert registro["aplicado"] is False
    assert registro["tipo_molde"] == "abierto"
    assert "motivo" in registro


# ---------------------------------------------------------------------------
# castkit matriz (figura/cubeta) con un diseño abierto: ficha con tipo/largo/grosor.
# ---------------------------------------------------------------------------


def _preparar_diseno_abierto_matriz(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    nombre: str = "guagua-tumbada",
    largo_mm: float = 40.0,
    grosor_mm: float = 10.0,
    cavidades: int = 2,
) -> Path:
    carpeta_brief = tmp_path / f"brief-{nombre}"
    carpeta_brief.mkdir()
    (carpeta_brief / "ref.txt").write_text("brief", encoding="utf-8")

    destino = tmp_path / "disenos"
    destino.mkdir(exist_ok=True)
    monkeypatch.setenv("CASTKIT_DISENOS", str(destino))

    from datetime import date

    datos = DatosAlta(
        nombre=nombre,
        brief=carpeta_brief,
        material="porcelana-fria",
        alto_mm=None,
        cavidades=cavidades,
        cantidad=20,
        fecha=date(2026, 11, 1),
        licencia="propio",
        origen="prueba",
        tipo_molde="abierto",
        largo_mm=largo_mm,
        grosor_mm=grosor_mm,
    )
    carpeta = crear_diseno(datos, destino=destino)
    ingerir_malla(carpeta, CUBE_STL)
    limpiar_malla(
        carpeta,
        caras_max=200000,
        tolerancia_mm=0.5,
        corte_base_mm=0.5,
        agujero_max_mm=10.0,
    )
    registrar_decision_control1(carpeta, decision="aprobado")
    return carpeta


def test_matriz_modo_figura_funciona_con_diseno_abierto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_diseno_abierto_matriz(tmp_path, monkeypatch)

    rc = main(["matriz", "guagua-tumbada", "--modo", "figura"])
    assert rc == 0

    salida = carpeta / "output" / "guagua-tumbada_v1_matriz.stl"
    malla = trimesh.load(salida, file_type="stl", force="mesh")
    assert malla.is_watertight
    assert malla.volume > 0

    ficha = (carpeta / "output" / "guagua-tumbada_v1_ficha.md").read_text(encoding="utf-8")
    assert "Tipo de molde:** `abierto`" in ficha
    assert "Largo de la figura:** 40.0 mm" in ficha
    assert "Grosor de la figura:** 10.0 mm" in ficha
    assert "Altura objetivo de figura" not in ficha


def test_matriz_modo_cubeta_funciona_con_diseno_abierto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_diseno_abierto_matriz(tmp_path, monkeypatch, cavidades=2)

    rc = main(["matriz", "guagua-tumbada", "--modo", "cubeta"])
    assert rc == 0

    salida = carpeta / "output" / "guagua-tumbada_v1_matriz.stl"
    malla = trimesh.load(salida, file_type="stl", force="mesh")
    assert malla.is_watertight
    assert malla.volume > 0

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["matriz"]
    assert reg["modo"] == "cubeta"
    assert reg["cavidades"] == 2
