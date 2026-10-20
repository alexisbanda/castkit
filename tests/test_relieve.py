"""Tests del proveedor «relieve»: bajorrelieve por capas de color (#43).

Cubre: separar el fondo (el blanco encerrado no es fondo), proponer y
escribir/leer `relieve.toml`, construir la malla (cerrada, espalda plana,
alturas, huella, escalón entre niveles, manchas pequeñas descartadas, placa
o islas sueltas), el error de molde completo/sin `grosor_mm`, el registro de
parámetros y el `sha256` estable entre regeneraciones, el escalado final con
`castkit limpieza` y que `castkit flujo` llega a la matriz.
"""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image, ImageDraw

from castkit import relieve
from castkit.cli import main
from castkit.diseno import DatosAlta, crear_diseno
from castkit.generacion import (
    GeneracionError,
    InsumosBrief,
    ProveedorRelieve,
    generar_malla,
)
from castkit.limpieza import limpiar_malla
from castkit.versiones import cargar_diseno

# Colores del lienzo de prueba (planos, sin antialias, deterministas).
_BLANCO = (255, 255, 255)
_COLOR_A = (30, 60, 120)  # cuerpo principal (mayor área -> base del relieve)
_COLOR_B = (200, 30, 40)  # detalle anidado dentro de A
_COLOR_C = (210, 180, 60)  # pieza suelta (isla), separada de A por 1 px


def _imagen_relieve(ruta: Path) -> None:
    """Lienzo de 90x60 px: A (con un hueco blanco encerrado), B anidado en A,
    e isla C separada de A por un solo píxel de fondo.
    """
    img = Image.new("RGB", (90, 60), _BLANCO)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 69, 49], fill=_COLOR_A)
    d.rectangle([25, 20, 39, 34], fill=_BLANCO)  # hueco interior encerrado
    d.rectangle([45, 20, 59, 39], fill=_COLOR_B)  # detalle anidado
    d.rectangle([71, 10, 80, 19], fill=_COLOR_C)  # isla, 1 px de A
    img.save(ruta)


def _imagen_con_mancha(ruta: Path) -> None:
    """Lienzo con A y una mancha D de 2x2 px, lejos de A (para el criterio de
    `area_min_mm2`). No incluye un hueco ni una isla: solo lo necesario.
    """
    img = Image.new("RGB", (90, 60), _BLANCO)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 69, 49], fill=_COLOR_A)
    d.rectangle([85, 55, 86, 56], fill=(10, 200, 10))
    img.save(ruta)


def _datos_relieve_explicitos(*, placa_mm: float = 0.0, area_min_mm2: float = 0.5) -> dict:
    """`relieve.toml` ya resuelto (sin pasar por `proponer_relieve`), con alturas
    conocidas: A=base, hueco blanco, B y la isla C en terrazas por encima.
    """
    return {
        "colores": [
            {"hex": "#1e3c78", "altura_mm": 2.0},
            {"hex": "#ffffff", "altura_mm": 2.2},
            {"hex": "#c81e28", "altura_mm": 2.6},
            {"hex": "#d2b43c", "altura_mm": 3.0},
        ],
        "placa_mm": placa_mm,
        "placa_margen_mm": 1.0,
        "escalon_mm": 0.05,
        "area_min_mm2": area_min_mm2,
    }


def _crear_diseno_relieve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    nombre: str = "escudo-prueba",
    tipo_molde: str = "abierto",
    largo_mm: float | None = 47.0,
    grosor_mm: float | None = 6.0,
    material: str = "porcelana-fria",
    constructor_imagen=_imagen_relieve,
) -> Path:
    brief = tmp_path / "brief-origen"
    brief.mkdir()
    constructor_imagen(brief / "logo.png")

    destino = tmp_path / "disenos"
    destino.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(destino))

    datos = DatosAlta(
        nombre=nombre,
        brief=brief,
        material=material,
        alto_mm=None if tipo_molde == "abierto" else 80.0,
        cavidades=1,
        cantidad=1,
        fecha=date(2026, 11, 1),
        licencia="propio",
        origen="prueba",
        tipo_molde=tipo_molde,
        largo_mm=largo_mm if tipo_molde == "abierto" else None,
        grosor_mm=grosor_mm if tipo_molde == "abierto" else None,
    )
    return crear_diseno(datos, destino=destino)


# ---------------------------------------------------------------------------
# Criterio: primera generación propone alturas y escribe relieve.toml; el
# blanco encerrado no es fondo.
# ---------------------------------------------------------------------------


def test_proponer_relieve_separa_fondo_y_el_blanco_encerrado_no_es_fondo(tmp_path: Path) -> None:
    ruta_imagen = tmp_path / "logo.png"
    _imagen_relieve(ruta_imagen)

    config = relieve.ConfigRelieve()
    propuesta = relieve.proponer_relieve(ruta_imagen, config)

    hexes = {c["hex"] for c in propuesta["colores"]}
    assert "#ffffff" in hexes, "el blanco encerrado (el hueco) debe proponerse como color"
    assert len(propuesta["colores"]) == 4  # A, hueco blanco, B, isla C (sin el fondo)

    # El color de mayor área (A) es la base del relieve.
    altura_a = next(c["altura_mm"] for c in propuesta["colores"] if c["hex"] == "#1e3c78")
    assert altura_a == pytest.approx(config.base_mm)

    alturas = sorted(c["altura_mm"] for c in propuesta["colores"])
    pasos_esperados = [config.base_mm + k * config.paso_mm for k in range(4)]
    assert alturas == pytest.approx(pasos_esperados)

    assert propuesta["placa_mm"] == pytest.approx(config.placa_mm)
    assert propuesta["placa_margen_mm"] == pytest.approx(config.placa_margen_mm)
    assert propuesta["escalon_mm"] == pytest.approx(config.escalon_mm)
    assert propuesta["area_min_mm2"] == pytest.approx(config.area_min_mm2)


def test_escribir_y_leer_relieve_toml_roundtrip(tmp_path: Path) -> None:
    ruta_imagen = tmp_path / "logo.png"
    _imagen_relieve(ruta_imagen)
    propuesta = relieve.proponer_relieve(ruta_imagen, relieve.ConfigRelieve())

    ruta_toml = tmp_path / "relieve.toml"
    relieve.escribir_relieve_toml(ruta_toml, propuesta)

    contenido = ruta_toml.read_text(encoding="utf-8")
    assert "RELATIVAS" in contenido
    assert "grosor_mm" in contenido
    assert "[[color]]" in contenido

    with ruta_toml.open("rb") as flujo:
        datos_toml = tomllib.load(flujo)
    assert len(datos_toml["color"]) == len(propuesta["colores"])

    releida = relieve.leer_relieve_toml(ruta_toml)
    assert releida["colores"] == propuesta["colores"]
    assert releida["placa_mm"] == pytest.approx(propuesta["placa_mm"])
    assert releida["placa_margen_mm"] == pytest.approx(propuesta["placa_margen_mm"])
    assert releida["escalon_mm"] == pytest.approx(propuesta["escalon_mm"])
    assert releida["area_min_mm2"] == pytest.approx(propuesta["area_min_mm2"])


# ---------------------------------------------------------------------------
# Criterio: con un relieve.toml dado, la malla en bruto sigue cerrada al
# recargarla, tiene la espalda plana en z=0, cada región alcanza su altura y
# su huella en planta coincide con largo_mm.
# ---------------------------------------------------------------------------


def test_generar_malla_relieve_cerrada_espalda_plana_alturas_y_huella(tmp_path: Path) -> None:
    ruta_imagen = tmp_path / "logo.png"
    _imagen_relieve(ruta_imagen)
    datos_relieve = _datos_relieve_explicitos()

    destino = tmp_path / "bruto.stl"
    resultado = relieve.generar_malla_relieve(
        ruta_imagen, datos_relieve, largo_mm=47.0, grosor_mm=6.0, destino_stl=destino
    )

    assert destino.is_file()
    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight

    assert malla.bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert malla.bounds[1][2] == pytest.approx(6.0 + relieve.CORTE_SACRIFICABLE_MM, abs=0.01)

    extents_xy = malla.bounds[1][:2] - malla.bounds[0][:2]
    mm_por_px = resultado["parametros_registro"]["mm_por_px"]
    assert float(max(extents_xy)) == pytest.approx(47.0, abs=mm_por_px * 1.5)

    # Las terrazas son extrusiones verticales puras: los vértices solo viven
    # en el plano z=0 (base) o en los techos de cada nivel (± 0,01 mm). Como
    # `placa_mm=0` (por defecto en `_datos_relieve_explicitos`), la isla C
    # (#d2b43c, altura nominal 3.0) queda limitada a la altura base (2.0):
    # el pico relativo real pasa a ser el de B (2.6), no el de la isla.
    factor_altura = 6.0 / 2.6
    niveles_relativos = [2.0, 2.2, 2.6]
    z_esperados = [0.0, relieve.CORTE_SACRIFICABLE_MM] + [
        relieve.CORTE_SACRIFICABLE_MM + h * factor_altura for h in niveles_relativos
    ]
    zs = np.unique(np.round(malla.vertices[:, 2], 2))
    for z in z_esperados:
        assert np.any(np.isclose(zs, z, atol=0.02)), f"falta el nivel z={z:.2f} ({zs})"


def test_generar_malla_relieve_contrae_niveles_sin_paredes_coincidentes(tmp_path: Path) -> None:
    """El cierre morfológico y la contracción `escalon_mm·k` dejan la malla
    cerrada (sin paredes coincidentes de grosor cero entre niveles).
    """
    ruta_imagen = tmp_path / "logo.png"
    _imagen_relieve(ruta_imagen)
    datos_relieve = _datos_relieve_explicitos()

    destino = tmp_path / "bruto.stl"
    relieve.generar_malla_relieve(
        ruta_imagen, datos_relieve, largo_mm=47.0, grosor_mm=6.0, destino_stl=destino
    )
    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight
    assert malla.volume > 0


def test_manchas_menores_que_area_min_mm2_se_descartan(tmp_path: Path) -> None:
    ruta_imagen = tmp_path / "logo.png"
    _imagen_con_mancha(ruta_imagen)

    datos_base = {
        "colores": [
            {"hex": "#ffffff", "altura_mm": 2.0},
            {"hex": "#1e3c78", "altura_mm": 2.6},
            {"hex": "#0ac80a", "altura_mm": 3.0},
        ],
        "placa_mm": 0.0,
        "placa_margen_mm": 1.0,
        "escalon_mm": 0.05,
        "area_min_mm2": 0.5,
    }

    destino_defecto = tmp_path / "con_mancha_defecto.stl"
    relieve.generar_malla_relieve(
        ruta_imagen, datos_base, largo_mm=10.0, grosor_mm=2.0, destino_stl=destino_defecto
    )
    malla_defecto = trimesh.load(destino_defecto, force="mesh")
    cuerpos_defecto = malla_defecto.split(only_watertight=False)

    datos_sin_filtro = dict(datos_base, area_min_mm2=0.0001)
    destino_bajo = tmp_path / "con_mancha_bajo.stl"
    relieve.generar_malla_relieve(
        ruta_imagen, datos_sin_filtro, largo_mm=10.0, grosor_mm=2.0, destino_stl=destino_bajo
    )
    malla_bajo = trimesh.load(destino_bajo, force="mesh")
    cuerpos_bajo = malla_bajo.split(only_watertight=False)

    assert len(cuerpos_defecto) == 1, "con area_min_mm2 por defecto, la mancha se descarta"
    assert len(cuerpos_bajo) == 2, "con area_min_mm2 muy bajo, la mancha aparece como pieza"


# ---------------------------------------------------------------------------
# Criterio: piezas sueltas — sin placa, islas aceptadas pero limitadas a la
# altura base; con placa, todo es un solo cuerpo.
# ---------------------------------------------------------------------------


def test_sin_placa_las_islas_se_limitan_a_la_altura_base(tmp_path: Path) -> None:
    ruta_imagen = tmp_path / "logo.png"
    _imagen_relieve(ruta_imagen)
    datos_relieve = _datos_relieve_explicitos(placa_mm=0.0)

    destino = tmp_path / "bruto.stl"
    relieve.generar_malla_relieve(
        ruta_imagen, datos_relieve, largo_mm=47.0, grosor_mm=6.0, destino_stl=destino
    )
    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight

    cuerpos = malla.split(only_watertight=False)
    assert len(cuerpos) == 2, "la isla C queda como pieza suelta, separada del cuerpo principal"

    alturas_cuerpos = sorted(float(c.bounds[1][2] - c.bounds[0][2]) for c in cuerpos)
    # La isla (#d2b43c, altura nominal 3.0) queda limitada a la altura base
    # (2.0): el pico relativo real pasa a ser el de B (2.6), no el de la isla.
    factor_altura = 6.0 / 2.6
    altura_base_esperada = relieve.CORTE_SACRIFICABLE_MM + 2.0 * factor_altura
    altura_pico_esperada = relieve.CORTE_SACRIFICABLE_MM + 6.0
    # La isla (cuerpo más bajo) se limita a la altura base, no a su color nominal.
    assert alturas_cuerpos[0] == pytest.approx(altura_base_esperada, abs=0.02)
    assert alturas_cuerpos[1] == pytest.approx(altura_pico_esperada, abs=0.02)


def test_con_placa_todo_el_diseno_es_un_solo_cuerpo(tmp_path: Path) -> None:
    ruta_imagen = tmp_path / "logo.png"
    _imagen_relieve(ruta_imagen)
    datos_relieve = _datos_relieve_explicitos(placa_mm=1.0)

    destino = tmp_path / "bruto.stl"
    relieve.generar_malla_relieve(
        ruta_imagen, datos_relieve, largo_mm=47.0, grosor_mm=8.0, destino_stl=destino
    )
    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight

    cuerpos = malla.split(only_watertight=False)
    assert len(cuerpos) == 1
    assert malla.bounds[1][2] == pytest.approx(8.0 + relieve.CORTE_SACRIFICABLE_MM, abs=0.02)


# ---------------------------------------------------------------------------
# Criterio: molde completo o sin grosor_mm -> error con las opciones
# correctas, sin generar ninguna malla.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "info_version",
    [
        {"tipo_molde": "completo", "largo_mm": None, "grosor_mm": None},
        {"tipo_molde": "abierto", "largo_mm": 47.0, "grosor_mm": None},
    ],
)
def test_proveedor_relieve_exige_molde_abierto_con_largo_y_grosor(
    tmp_path: Path, info_version: dict
) -> None:
    ruta_imagen = tmp_path / "logo.png"
    _imagen_relieve(ruta_imagen)

    proveedor = ProveedorRelieve()
    proveedor.usar_version(info_version)
    insumos = InsumosBrief(imagenes=[ruta_imagen])

    with pytest.raises(GeneracionError) as excinfo:
        proveedor.generar(insumos, tmp_path / "salida.stl")

    mensaje = str(excinfo.value)
    assert "--tipo-molde abierto" in mensaje
    assert "--largo-mm" in mensaje
    assert "--grosor-mm" in mensaje
    assert not (tmp_path / "salida.stl").exists()
    assert not (tmp_path / "relieve.toml").exists()


def test_proveedor_relieve_requiere_al_menos_una_imagen(tmp_path: Path) -> None:
    proveedor = ProveedorRelieve()
    proveedor.usar_version({"tipo_molde": "abierto", "largo_mm": 47.0, "grosor_mm": 6.0})
    with pytest.raises(GeneracionError, match="imagen"):
        proveedor.generar(InsumosBrief(), tmp_path / "salida.stl")


# ---------------------------------------------------------------------------
# Criterio: registro de parámetros y sha256 estable entre regeneraciones.
# ---------------------------------------------------------------------------


def test_registro_de_parametros_y_sha256_estable_entre_regeneraciones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _crear_diseno_relieve(tmp_path, monkeypatch)

    resultado1 = generar_malla(carpeta, proveedor="relieve")
    datos_diseno = cargar_diseno(carpeta)
    parametros = datos_diseno["versiones"]["v1"]["parametros"]["generacion"]

    assert parametros["proveedor"] == "relieve"
    assert "color_altura_mm" in parametros
    assert parametros["placa_mm"] == pytest.approx(0.0)
    assert "escalon_mm" in parametros
    assert "colores_max" in parametros
    assert "relieve_toml_sha256" in parametros
    assert len(parametros["relieve_toml_sha256"]) == 64  # hexdigest sha256

    ruta_toml = carpeta / "brief" / "relieve.toml"
    assert ruta_toml.is_file()

    resultado2 = generar_malla(carpeta, proveedor="relieve", reemplazar=True)
    assert resultado2["sha256"] == resultado1["sha256"], (
        "el mismo relieve.toml debe producir el mismo STL (mismo sha256)"
    )


def test_primera_generacion_sin_relieve_toml_avisa_donde_quedo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _crear_diseno_relieve(tmp_path, monkeypatch)
    ruta_toml = carpeta / "brief" / "relieve.toml"
    assert not ruta_toml.is_file()

    generar_malla(carpeta, proveedor="relieve")

    salida = capsys.readouterr().out
    assert str(ruta_toml) in salida
    assert "generar" in salida
    assert ruta_toml.is_file()


# ---------------------------------------------------------------------------
# Criterio: tras `castkit limpieza`, la figura mide largo_mm x ... x
# grosor_mm (± 0,05 mm).
# ---------------------------------------------------------------------------


def test_limpieza_deja_la_figura_en_largo_mm_y_grosor_mm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _crear_diseno_relieve(tmp_path, monkeypatch, largo_mm=47.0, grosor_mm=6.0)
    generar_malla(carpeta, proveedor="relieve")

    resultado = limpiar_malla(
        carpeta,
        arriba=None,
        caras_max=200000,
        tolerancia_mm=0.5,
        corte_base_mm=0.5,
        agujero_max_mm=10,
    )
    malla = trimesh.load(resultado["destino"], force="mesh")
    assert malla.is_watertight
    assert malla.bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert float(max(malla.extents[0], malla.extents[1])) == pytest.approx(47.0, abs=0.05)
    assert float(malla.extents[2]) == pytest.approx(6.0, abs=0.05)


# ---------------------------------------------------------------------------
# Criterio: `castkit flujo` con un diseño de relieve llega a la matriz.
# ---------------------------------------------------------------------------


def test_flujo_con_relieve_llega_a_la_matriz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blender_falso: list
) -> None:
    nombre = "escudo-flujo"
    _crear_diseno_relieve(tmp_path, monkeypatch, nombre=nombre, largo_mm=47.0, grosor_mm=6.0)

    rc = main(
        [
            "flujo",
            nombre,
            "--modo",
            "figura",
            "--proveedor",
            "relieve",
            "--aprobar-control1",
        ]
    )
    assert rc == 0

    destino = tmp_path / "disenos" / nombre
    datos_diseno = cargar_diseno(destino)
    salidas_matriz = list((destino / "output").glob(f"{nombre}_v1_matriz*.stl"))
    assert salidas_matriz, "la etapa de matriz debe haber escrito un STL en output/"
    assert "matriz" in datos_diseno["versiones"]["v1"]["parametros"]
