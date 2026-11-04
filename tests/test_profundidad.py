"""Tests del proveedor «profundidad»: media figura para molde abierto desde una
imagen frontal (#56).

Cubre: el modelo permitido (Small, Apache-2.0) y el rechazo de otros modelos
(licencia CC-BY-NC), escribir/leer `profundidad.toml`, construir la malla con
un mapa de profundidad sintético (cerrada, espalda plana, altura máxima
`grosor_mm`, contorno trazado como polígono suavizado sin escalones), el
registro de parámetros (modelo, revisión, sha256 de la imagen y del TOML), el
error de molde completo/sin `grosor_mm`, el mensaje del extra opcional no
instalado (real: `torch` no está instalado en este entorno de tests), la cota
de memoria al construir la malla de una imagen grande, el tope de caras de la
malla en bruto (`caras_max`, #56 ronda 2) y que la extensión del campo de
alturas fuera de la figura no deja saltos en escalón (origen de la "ceja" y
las "planchas" de la pared, #56 ronda 2).
"""

from __future__ import annotations

import tracemalloc
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image, ImageDraw
from scipy import ndimage

from castkit import profundidad
from castkit.cli import main
from castkit.diseno import DatosAlta, crear_diseno
from castkit.generacion import (
    GeneracionError,
    InsumosBrief,
    ProveedorProfundidad,
    desregistrar_proveedor,
    generar_malla,
    registrar_proveedor,
)
from castkit.limpieza import limpiar_malla
from castkit.relieve import CORTE_SACRIFICABLE_MM
from castkit.versiones import cargar_diseno

_CONFIG_PROFUNDIDAD: dict[str, float] = {
    "borde_mm": 4.0,
    "realce_mm": 1.2,
    "suavizado": 0.6,
    "ancho_min_mm": 2.5,
    "engrose_mm": 0.6,
    "mm_por_px": 0.2,
    "tolerancia_mm": 0.03,
    "caras_max": 120000,
}


def _estimador_sintetico(ruta_imagen: Path, modelo: str, revision: str) -> np.ndarray:
    """Mapa de profundidad sintético (sin torch): el inverso de la luminancia.

    Determinista y atado al contenido de la imagen, para no depender de un
    modelo real en los tests (ver criterio de aceptación #5 del ticket).
    """
    imagen = Image.open(ruta_imagen).convert("L")
    gris = np.asarray(imagen, dtype=np.float32)
    return 255.0 - gris


def _imagen_blob(ruta: Path, *, w: int = 160, h: int = 160) -> None:
    """Figura con cara simple (óvalo + dos "ojos" oscuros) sobre fondo blanco."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([20, 15, w - 20, h - 15], fill=(200, 160, 120))
    d.ellipse([w // 2 - 30, h // 2 - 15, w // 2 - 15, h // 2], fill=(30, 30, 30))
    d.ellipse([w // 2 + 15, h // 2 - 15, w // 2 + 30, h // 2], fill=(30, 30, 30))
    img.save(ruta)


def _imagen_circulo_exacto(ruta: Path, *, w: int = 300, r: int = 100) -> None:
    """Círculo exacto (máscara analítica, no rasterizada con antialias) para medir precisión."""
    yy, xx = np.mgrid[0:w, 0:w]
    cx = cy = w / 2 + 0.5
    rr = np.sqrt((xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2)
    mascara = rr <= r
    rgb = np.empty((w, w, 3), dtype=np.uint8)
    rgb[mascara] = (90, 130, 170)
    rgb[~mascara] = (255, 255, 255)
    Image.fromarray(rgb).save(ruta)


def _crear_diseno_profundidad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    nombre: str = "guagua-prueba",
    tipo_molde: str = "abierto",
    largo_mm: float | None = 50.0,
    grosor_mm: float | None = 10.0,
    material: str = "cera",
    constructor_imagen=_imagen_blob,
) -> Path:
    brief = tmp_path / "brief-origen"
    brief.mkdir()
    constructor_imagen(brief / "frontal.png")

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
# Criterio: modelo Small fijado; Base/Large (y cualquier otro) rechazados con
# un mensaje que explica la licencia.
# ---------------------------------------------------------------------------


def test_modelo_permitido_no_lanza_error() -> None:
    profundidad._validar_modelo(profundidad.MODELO_PERMITIDO)  # no debe lanzar


@pytest.mark.parametrize(
    "modelo",
    [
        "depth-anything/Depth-Anything-V2-Base-hf",
        "depth-anything/Depth-Anything-V2-Large-hf",
        "otro-modelo/cualquiera",
    ],
)
def test_modelos_no_permitidos_se_rechazan_con_mensaje_de_licencia(modelo: str) -> None:
    with pytest.raises(profundidad.ProfundidadError) as excinfo:
        profundidad._validar_modelo(modelo)
    mensaje = str(excinfo.value)
    assert "CC-BY-NC" in mensaje
    assert "no comercial" in mensaje
    assert modelo in mensaje


def test_estimar_profundidad_rechaza_modelo_no_permitido_sin_necesitar_torch(
    tmp_path: Path,
) -> None:
    ruta_imagen = tmp_path / "frontal.png"
    _imagen_blob(ruta_imagen)
    with pytest.raises(profundidad.ProfundidadError, match="CC-BY-NC"):
        profundidad.estimar_profundidad(
            ruta_imagen, "depth-anything/Depth-Anything-V2-Base-hf", "main"
        )


# ---------------------------------------------------------------------------
# Criterio: `profundidad.toml` (escribir/leer).
# ---------------------------------------------------------------------------


def test_escribir_y_leer_profundidad_toml_roundtrip(tmp_path: Path) -> None:
    ruta_toml = tmp_path / "profundidad.toml"
    profundidad.escribir_profundidad_toml(ruta_toml, _CONFIG_PROFUNDIDAD)

    contenido = ruta_toml.read_text(encoding="utf-8")
    assert "borde_mm" in contenido
    assert "mm_por_px" in contenido

    releido = profundidad.leer_profundidad_toml(ruta_toml)
    assert releido == pytest.approx(_CONFIG_PROFUNDIDAD)


def test_leer_profundidad_toml_exige_todas_las_claves(tmp_path: Path) -> None:
    ruta_toml = tmp_path / "profundidad.toml"
    ruta_toml.write_text("borde_mm = 4.0\n", encoding="utf-8")
    with pytest.raises(profundidad.ProfundidadError, match="le faltan estas claves"):
        profundidad.leer_profundidad_toml(ruta_toml)


# ---------------------------------------------------------------------------
# Criterio: la malla en bruto sigue cerrada al recargarla, sin aristas no
# variedad, con la espalda plana en z=0 y el grosor bruto = grosor_mm + 0.5.
# El máximo de altura es exactamente grosor_mm.
# ---------------------------------------------------------------------------


def test_generar_malla_profundidad_cerrada_espalda_plana_y_altura_maxima(
    tmp_path: Path,
) -> None:
    ruta_imagen = tmp_path / "frontal.png"
    _imagen_blob(ruta_imagen)
    mapa = _estimador_sintetico(ruta_imagen, profundidad.MODELO_PERMITIDO, "main")

    destino = tmp_path / "bruto.stl"
    resultado = profundidad.generar_malla_profundidad(
        ruta_imagen,
        mapa,
        _CONFIG_PROFUNDIDAD,
        largo_mm=50.0,
        grosor_mm=10.0,
        destino_stl=destino,
    )

    assert destino.is_file()
    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight

    # Sin aristas no variedad: cada arista la comparten exactamente 2 caras.
    _, conteos = np.unique(malla.edges_sorted, axis=0, return_counts=True)
    assert np.all(conteos == 2)

    assert malla.bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert malla.bounds[1][2] == pytest.approx(10.0 + CORTE_SACRIFICABLE_MM, abs=0.02)

    extents_xy = malla.bounds[1][:2] - malla.bounds[0][:2]
    assert float(max(extents_xy)) == pytest.approx(50.0, abs=0.1)

    assert resultado["parametros_registro"]["grosor_mm"] == pytest.approx(10.0)
    assert resultado["parametros_registro"]["borde_mm"] == pytest.approx(4.0)


def test_generar_malla_profundidad_exige_largo_y_grosor_positivos(tmp_path: Path) -> None:
    ruta_imagen = tmp_path / "frontal.png"
    _imagen_blob(ruta_imagen)
    mapa = _estimador_sintetico(ruta_imagen, profundidad.MODELO_PERMITIDO, "main")

    with pytest.raises(profundidad.ProfundidadError, match="largo_mm"):
        profundidad.generar_malla_profundidad(
            ruta_imagen,
            mapa,
            _CONFIG_PROFUNDIDAD,
            largo_mm=0.0,
            grosor_mm=10.0,
            destino_stl=tmp_path / "x.stl",
        )

    with pytest.raises(profundidad.ProfundidadError, match="borde_mm"):
        profundidad.generar_malla_profundidad(
            ruta_imagen,
            mapa,
            _CONFIG_PROFUNDIDAD,
            largo_mm=50.0,
            grosor_mm=2.0,  # menor que borde_mm (4.0): sin espacio para el relieve
            destino_stl=tmp_path / "x.stl",
        )


# ---------------------------------------------------------------------------
# Criterio: contorno trazado como polígono suavizado (no por celdas): con una
# imagen de un círculo, la pared se desvía del radio teórico menos de 0,1 mm.
# ---------------------------------------------------------------------------


def test_contorno_de_un_circulo_es_un_poligono_suave_sin_escalones(tmp_path: Path) -> None:
    """``_trazar_contorno`` traza un polígono (no celdas de píxel): con un círculo,
    sus vértices quedan a menos de 0,1 mm del radio teórico (criterio de aceptación).
    """
    mm_por_px = 0.2
    radio_px = 125  # radio de trabajo (en "píxeles" de la rejilla, ver _trazar_contorno)
    radio_teorico_mm = radio_px * mm_por_px

    tam = radio_px * 2 + 40
    yy, xx = np.mgrid[0:tam, 0:tam]
    cx = cy = tam / 2
    mascara = (xx - cx) ** 2 + (yy - cy) ** 2 <= radio_px**2

    contorno_cs = profundidad._trazar_contorno(mascara, mm_por_px)
    assert contorno_cs is not None

    poligono = np.asarray(contorno_cs.to_polygons()[0])
    centro = poligono.mean(axis=0)
    radios = np.linalg.norm(poligono - centro, axis=1)

    # Sin escalones: la desviación de CADA vértice del polígono respecto al
    # radio teórico es submilimétrica (una pared por celdas de píxel, en vez
    # de un polígono suavizado, se desviaría varias veces más que esto).
    assert float(np.max(np.abs(radios - radio_teorico_mm))) < 0.1


def test_malla_de_un_circulo_coincide_con_largo_mm_en_planta(tmp_path: Path) -> None:
    """Punta a punta: la malla completa de un círculo mide ``largo_mm`` de diámetro."""
    ruta_imagen = tmp_path / "circulo.png"
    _imagen_circulo_exacto(ruta_imagen, w=300, r=100)
    mapa = _estimador_sintetico(ruta_imagen, profundidad.MODELO_PERMITIDO, "main")

    largo_mm = 50.0
    destino = tmp_path / "circulo.stl"
    profundidad.generar_malla_profundidad(
        ruta_imagen,
        mapa,
        _CONFIG_PROFUNDIDAD,
        largo_mm=largo_mm,
        grosor_mm=10.0,
        destino_stl=destino,
    )
    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight
    extents_xy = malla.bounds[1][:2] - malla.bounds[0][:2]
    assert float(max(extents_xy)) == pytest.approx(largo_mm, abs=0.3)


# ---------------------------------------------------------------------------
# Criterio: molde completo o sin grosor_mm -> error con las opciones
# correctas, sin generar ninguna malla.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "info_version",
    [
        {"tipo_molde": "completo", "largo_mm": None, "grosor_mm": None},
        {"tipo_molde": "abierto", "largo_mm": 50.0, "grosor_mm": None},
    ],
)
def test_proveedor_profundidad_exige_molde_abierto_con_largo_y_grosor(
    tmp_path: Path, info_version: dict
) -> None:
    ruta_imagen = tmp_path / "frontal.png"
    _imagen_blob(ruta_imagen)

    proveedor = _proveedor_con_estimador_sintetico()
    proveedor.usar_version(info_version)
    insumos = InsumosBrief(imagenes=[ruta_imagen])

    with pytest.raises(GeneracionError) as excinfo:
        proveedor.generar(insumos, tmp_path / "salida.stl")

    mensaje = str(excinfo.value)
    assert "--tipo-molde abierto" in mensaje
    assert "--largo-mm" in mensaje
    assert "--grosor-mm" in mensaje
    assert not (tmp_path / "salida.stl").exists()
    assert not (tmp_path / "profundidad.toml").exists()


def test_proveedor_profundidad_requiere_al_menos_una_imagen() -> None:
    proveedor = _proveedor_con_estimador_sintetico()
    proveedor.usar_version({"tipo_molde": "abierto", "largo_mm": 50.0, "grosor_mm": 10.0})
    with pytest.raises(GeneracionError, match="imagen"):
        proveedor.generar(InsumosBrief(), Path("salida.stl"))


def _config_profundidad_completa() -> dict[str, object]:
    """``[profundidad]`` con `modelo`/`revision` además de las alturas (ver config.py)."""
    return {
        "modelo": profundidad.MODELO_PERMITIDO,
        "revision": "5426e4f0f36572d16453bbda7a8389317b1bef99",
        **_CONFIG_PROFUNDIDAD,
    }


def _proveedor_con_estimador_sintetico() -> ProveedorProfundidad:
    """``ProveedorProfundidad`` con el estimador sintético inyectado (sin torch)."""
    return ProveedorProfundidad(
        _config_profundidad_completa(), estimar_profundidad=_estimador_sintetico
    )


# ---------------------------------------------------------------------------
# Criterio: la primera generación escribe brief/profundidad.toml; si se
# edita, la siguiente lo respeta. Registro de modelo, revisión y sha256.
# ---------------------------------------------------------------------------


def test_primera_generacion_escribe_profundidad_toml_y_la_siguiente_lo_respeta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _crear_diseno_profundidad(tmp_path, monkeypatch)
    proveedor = _proveedor_con_estimador_sintetico()
    registrar_proveedor("profundidad", proveedor)
    try:
        resultado1 = generar_malla(carpeta, proveedor="profundidad")
        ruta_toml = carpeta / "brief" / "profundidad.toml"
        assert ruta_toml.is_file()

        datos_diseno = cargar_diseno(carpeta)
        parametros = datos_diseno["versiones"]["v1"]["parametros"]["generacion"]
        assert parametros["modelo"] == profundidad.MODELO_PERMITIDO
        assert parametros["revision"] == "5426e4f0f36572d16453bbda7a8389317b1bef99"
        assert len(parametros["imagen_sha256"]) == 64
        assert len(parametros["profundidad_toml_sha256"]) == 64
        assert "segundos_calculo" in parametros
        assert parametros["borde_mm"] == pytest.approx(4.0)

        # Edita profundidad.toml: una siguiente generación debe respetarlo.
        datos_editados = dict(_CONFIG_PROFUNDIDAD, borde_mm=6.0)
        profundidad.escribir_profundidad_toml(ruta_toml, datos_editados)

        resultado2 = generar_malla(carpeta, proveedor="profundidad", reemplazar=True)
        datos_diseno2 = cargar_diseno(carpeta)
        parametros2 = datos_diseno2["versiones"]["v1"]["parametros"]["generacion"]
        assert parametros2["borde_mm"] == pytest.approx(6.0)
        assert resultado2["sha256"] != resultado1["sha256"]
    finally:
        desregistrar_proveedor("profundidad")


# ---------------------------------------------------------------------------
# Criterio: tras `castkit limpieza`, la figura mide largo_mm de largo y
# grosor_mm de alto (± 0,05 mm) — igual que `relieve` (#43).
# ---------------------------------------------------------------------------


def test_limpieza_deja_la_figura_en_largo_mm_y_grosor_mm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _crear_diseno_profundidad(tmp_path, monkeypatch, largo_mm=50.0, grosor_mm=10.0)
    proveedor = _proveedor_con_estimador_sintetico()
    registrar_proveedor("profundidad", proveedor)
    try:
        generar_malla(carpeta, proveedor="profundidad")
    finally:
        desregistrar_proveedor("profundidad")

    resultado = limpiar_malla(
        carpeta,
        arriba=None,
        caras_max=200000,
        tolerancia_mm=0.5,
        corte_base_mm=0.5,
        agujero_max_mm=10,
        sin_blender=True,
    )
    malla = trimesh.load(resultado["destino"], force="mesh")
    assert malla.is_watertight
    assert malla.bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert float(max(malla.extents[0], malla.extents[1])) == pytest.approx(50.0, abs=0.05)
    assert float(malla.extents[2]) == pytest.approx(10.0, abs=0.05)


# ---------------------------------------------------------------------------
# Criterio: sin el extra opcional instalado, el resto de castkit funciona y
# `--proveedor profundidad` termina con código != 0 y el mensaje en español
# de instalación. Real: `torch` no está instalado en este entorno de tests
# (el extra "profundidad" no se instala en CI, ver pyproject.toml).
# ---------------------------------------------------------------------------


def test_sin_extra_cli_termina_con_codigo_distinto_de_cero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        import torch  # noqa: F401

        pytest.skip("el extra opcional 'profundidad' está instalado en este entorno")
    except ImportError:
        pass

    carpeta = _crear_diseno_profundidad(tmp_path, monkeypatch)
    nombre = carpeta.name

    rc = main(["generar", nombre, "--proveedor", "profundidad"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "uv sync --extra profundidad" in err

    # El resto de castkit sigue funcionando (otro proveedor local, sin red).
    rc2 = main(["generar", nombre, "--proveedor", "imagen-3d", "--reemplazar"])
    assert rc2 == 0


# ---------------------------------------------------------------------------
# Criterio: construir la malla de una imagen de 1024 px con el mapa sintético
# no supera 500 MB (tracemalloc), y la limpieza posterior la acepta sin
# Blender.
# ---------------------------------------------------------------------------


def _imagen_blob_grande(ruta: Path, *, w: int = 1024) -> None:
    img = Image.new("RGB", (w, w), (255, 255, 255))
    d = ImageDraw.Draw(img)
    margen = w // 8
    d.ellipse([margen, margen, w - margen, w - margen], fill=(150, 110, 90))
    img.save(ruta)


def test_memoria_al_construir_la_malla_de_una_imagen_de_1024_px(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _crear_diseno_profundidad(
        tmp_path,
        monkeypatch,
        nombre="guagua-grande",
        largo_mm=50.0,
        grosor_mm=10.0,
        constructor_imagen=_imagen_blob_grande,
    )
    proveedor = _proveedor_con_estimador_sintetico()
    registrar_proveedor("profundidad", proveedor)

    tracemalloc.start()
    try:
        generar_malla(carpeta, proveedor="profundidad")
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        desregistrar_proveedor("profundidad")

    assert pico < 500 * 1024 * 1024, f"pico de memoria {pico / 1e6:.1f} MB (límite 500 MB)"

    resultado = limpiar_malla(
        carpeta,
        arriba=None,
        caras_max=200000,
        tolerancia_mm=0.5,
        corte_base_mm=0.5,
        agujero_max_mm=10,
        sin_blender=True,
    )
    malla_limpia = trimesh.load(resultado["destino"], force="mesh")
    assert malla_limpia.is_watertight


# ---------------------------------------------------------------------------
# Criterio (#56, ronda 2): menos caras en la malla en bruto, sin perder los
# motivos. Con la imagen sintética de 1024 px y largo 100 mm (la medida real
# que hizo fallar `castkit limpieza` en la prueba en vivo: 395 544 caras), la
# malla no pasa de `caras_max` y sigue cerrada al recargarla.
# ---------------------------------------------------------------------------


def test_malla_de_1024_px_y_largo_100_mm_no_pasa_de_caras_max(tmp_path: Path) -> None:
    ruta_imagen = tmp_path / "grande.png"
    _imagen_blob_grande(ruta_imagen)
    mapa = _estimador_sintetico(ruta_imagen, profundidad.MODELO_PERMITIDO, "main")

    destino = tmp_path / "grande.stl"
    profundidad.generar_malla_profundidad(
        ruta_imagen,
        mapa,
        _CONFIG_PROFUNDIDAD,
        largo_mm=100.0,
        grosor_mm=20.0,
        destino_stl=destino,
    )

    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight
    assert len(malla.faces) <= _CONFIG_PROFUNDIDAD["caras_max"]


def test_caras_max_demasiado_bajo_falla_con_mensaje_claro(tmp_path: Path) -> None:
    """Si ni subiendo la tolerancia 8 veces se baja de `caras_max`, falla con un mensaje
    que pide subir `caras_max` o `mm_por_px` (en vez de devolver una malla sobre el límite).
    """
    ruta_imagen = tmp_path / "frontal.png"
    _imagen_blob(ruta_imagen)
    mapa = _estimador_sintetico(ruta_imagen, profundidad.MODELO_PERMITIDO, "main")

    config = dict(_CONFIG_PROFUNDIDAD, caras_max=4)
    with pytest.raises(profundidad.ProfundidadError, match="caras_max"):
        profundidad.generar_malla_profundidad(
            ruta_imagen,
            mapa,
            config,
            largo_mm=50.0,
            grosor_mm=10.0,
            destino_stl=tmp_path / "x.stl",
        )


# ---------------------------------------------------------------------------
# Criterio (#56, ronda 2): la extensión del campo de alturas fuera de la
# figura (la que usa la pared, ver _extender_suave) no deja saltos en
# escalón entre celdas vecinas, a diferencia de un vecino más cercano puro
# (distance_transform_edt) — origen de la "ceja" (una cresta en el borde
# superior de la pared) y las "planchas" (escalones en planta) reportadas en
# la prueba en vivo con el cóndor.
# ---------------------------------------------------------------------------


def test_extender_suave_preserva_interior_y_evita_saltos_tipo_voronoi() -> None:
    tam = 60
    valido = np.zeros((tam, tam), dtype=bool)
    valores = np.zeros((tam, tam))
    valido[10, :] = True
    valores[10, :] = 2.0
    valido[49, :] = True
    valores[49, :] = 9.0

    extendido = profundidad._extender_suave(valido, valores)

    # El interior (donde `valido`) no cambia, y el resultado es finito en toda la
    # imagen (cubre de sobra el margen que lo usa, ver _DIFUSION_ITERACIONES).
    assert np.allclose(extendido[valido], valores[valido])
    assert np.all(np.isfinite(extendido))

    # Un vecino más cercano puro (lo que hacía la versión anterior) deja un salto de
    # 7 mm de golpe justo donde "el más cercano" cambia de la fila 10 a la 49 (a
    # mitad de camino entre ambas). El difusor, en cambio, reparte ese cambio en una
    # rampa: su mayor salto entre filas vecinas es mucho menor.
    _, (iy, ix) = ndimage.distance_transform_edt(~valido, return_indices=True)
    vecino_cercano = np.where(valido, valores, valores[iy, ix])
    salto_vecino = float(np.max(np.abs(np.diff(vecino_cercano[10:50, 5]))))
    salto_difusor = float(np.max(np.abs(np.diff(extendido[10:50, 5]))))

    assert salto_vecino == pytest.approx(7.0, abs=0.01)
    assert salto_difusor < salto_vecino / 2


def test_pared_no_se_engancha_a_la_rejilla_de_mm_por_px(tmp_path: Path) -> None:
    """La pared no "engancha" vértices a la rejilla de trabajo (``mm_por_px``).

    Una ronda anterior construía la pared intersecando (CSG, `Manifold.batch_boolean`)
    un sólido-rejilla (un vértice por píxel) con el prisma del contorno suavizado: eso
    "enganchaba" buena parte del corte a los ejes de la rejilla en vez de seguir el
    polígono (se midió hasta un 52% de los vértices de pared cayendo justo sobre la
    rejilla de 0,2 mm, cuando al azar debería ser ~10%), dejando escalones en planta
    ("planchas" a distinta profundidad) y una cresta en el borde superior ("ceja") —
    ver #56, ronda 2. Esta ronda construye la pared extruyendo el contorno a una altura
    unitaria y combando cada vértice con `Manifold.warp_batch` (solo mueve vértices, sin
    CSG): la fracción de vértices "de pared" sobre la rejilla debe volver a la que da el
    azar.
    """
    ruta_imagen = tmp_path / "frontal.png"
    _imagen_blob(ruta_imagen)
    mapa = _estimador_sintetico(ruta_imagen, profundidad.MODELO_PERMITIDO, "main")

    destino = tmp_path / "blob.stl"
    profundidad.generar_malla_profundidad(
        ruta_imagen,
        mapa,
        _CONFIG_PROFUNDIDAD,
        largo_mm=50.0,
        grosor_mm=10.0,
        destino_stl=destino,
    )

    malla = trimesh.load(destino, force="mesh")
    assert malla.is_watertight

    mm_por_px = _CONFIG_PROFUNDIDAD["mm_por_px"]
    normales = malla.face_normals
    pared = np.abs(normales[:, 2]) < 0.3
    assert pared.sum() > 0
    puntos = malla.vertices[malla.faces[pared].ravel()]
    resto_x = np.abs(np.mod(puntos[:, 0] + 1000 * mm_por_px, mm_por_px))
    resto_x = np.minimum(resto_x, mm_por_px - resto_x)
    fraccion_en_rejilla = float(np.mean(resto_x < 0.01))

    assert fraccion_en_rejilla < 0.25


# ---------------------------------------------------------------------------
# Criterio (#94): un canal alfa totalmente opaco (todo 255) se trata como sin
# alfa, igual que en `castkit.imagen_brief` — sin esto, una imagen RGBA opaca
# da como figura el rectángulo entero (y el aviso de vistas nombraría una
# vista que nunca se modeló).
# ---------------------------------------------------------------------------


def test_mascara_figura_con_alfa_totalmente_opaco_se_trata_como_sin_alfa() -> None:
    arr = np.zeros((40, 40, 4), dtype=np.uint8)
    arr[:, :] = (255, 255, 255, 255)
    arr[10:20, 10:20] = (0, 0, 0, 255)  # figura opaca, pero de color distinto del fondo
    rgb = arr[..., :3].astype(np.float64)
    alfa = arr[..., 3].astype(np.float64)

    mascara = profundidad._mascara_figura(rgb, alfa)

    assert not mascara.all()
    assert mascara[15, 15]
    assert not mascara[0, 0]
