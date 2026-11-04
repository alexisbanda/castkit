"""Tests de la lógica pura de `castkit.imagen_brief` (ticket #93).

Imágenes sintéticas creadas con Pillow en el propio test; nada de imágenes de
`~/castkit-pruebas`. No usa ningún proveedor real de generación 3D ni modelos.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from castkit.imagen_brief import (
    ImagenBriefError,
    analizar_imagen,
    recortar_vista,
)

BLANCO = (255, 255, 255)
NEGRO = (0, 0, 0)


def _lienzo(ancho: int, alto: int, color=BLANCO) -> np.ndarray:
    arr = np.zeros((alto, ancho, 3), dtype=np.uint8)
    arr[:, :] = color
    return arr


def _pintar(arr: np.ndarray, fila0: int, col0: int, fila1: int, col1: int, color=NEGRO) -> None:
    arr[fila0:fila1, col0:col1] = color


def _guardar(arr: np.ndarray, ruta: Path) -> None:
    Image.fromarray(arr, mode="RGB").save(ruta)


# ---------------------------------------------------------------------------
# Legibilidad
# ---------------------------------------------------------------------------


def test_imagen_corrupta_es_ilegible(tmp_path: Path) -> None:
    ruta = tmp_path / "rota.png"
    ruta.write_bytes(b"\x89PNG\r\n\x1a\n" + b"no soy un png de verdad")

    resultado = analizar_imagen(ruta)

    assert resultado.legible is False
    assert resultado.fondo_uniforme is None
    assert resultado.vistas == ()


def test_imagen_valida_es_legible(tmp_path: Path) -> None:
    ruta = tmp_path / "ok.png"
    _guardar(_lienzo(20, 20), ruta)

    resultado = analizar_imagen(ruta)

    assert resultado.legible is True


# ---------------------------------------------------------------------------
# Uniformidad del fondo
# ---------------------------------------------------------------------------


def test_fondo_liso_es_uniforme(tmp_path: Path) -> None:
    arr = _lienzo(40, 40)
    _pintar(arr, 10, 10, 20, 20)
    ruta = tmp_path / "liso.png"
    _guardar(arr, ruta)

    resultado = analizar_imagen(ruta)

    assert resultado.fondo_uniforme is True


def test_fondo_con_degradado_no_es_uniforme(tmp_path: Path) -> None:
    ancho, alto = 60, 60
    gradiente = np.linspace(0, 255, ancho, dtype=np.uint8)
    arr = np.zeros((alto, ancho, 3), dtype=np.uint8)
    arr[:, :, 0] = gradiente
    arr[:, :, 1] = gradiente
    arr[:, :, 2] = gradiente
    ruta = tmp_path / "degradado.png"
    _guardar(arr, ruta)

    resultado = analizar_imagen(ruta)

    assert resultado.legible is True
    assert resultado.fondo_uniforme is False
    # Una sola figura (aquí, ninguna de verdad) sobre fondo no uniforme no se
    # presenta como varias vistas: no se intenta separar nada.
    assert resultado.vistas == ()


def test_alfa_todo_255_se_trata_como_sin_alfa(tmp_path: Path) -> None:
    ancho, alto = 60, 60
    gradiente = np.linspace(0, 255, ancho, dtype=np.uint8)
    rgb = np.zeros((alto, ancho, 3), dtype=np.uint8)
    rgb[:, :, 0] = gradiente
    rgb[:, :, 1] = gradiente
    rgb[:, :, 2] = gradiente
    rgba = np.concatenate([rgb, np.full((alto, ancho, 1), 255, dtype=np.uint8)], axis=-1)
    ruta = tmp_path / "degradado-alfa-opaco.png"
    Image.fromarray(rgba, mode="RGBA").save(ruta)

    resultado = analizar_imagen(ruta)

    # Mismo resultado que la versión sin canal alfa: el fondo sigue siendo
    # el degradado (no uniforme), porque un alfa todo en 255 no es "real".
    assert resultado.fondo_uniforme is False


def test_alfa_real_se_considera_fondo_uniforme(tmp_path: Path) -> None:
    ancho, alto = 40, 40
    # RGB con ruido fuerte (sería "no uniforme" sin alfa), pero con alfa real
    # (transparente en el borde, opaco en la figura) se considera uniforme.
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 256, size=(alto, ancho, 3), dtype=np.uint8)
    alfa = np.zeros((alto, ancho), dtype=np.uint8)
    alfa[10:20, 10:20] = 255
    rgba = np.concatenate([rgb, alfa[..., None]], axis=-1)
    ruta = tmp_path / "alfa-real.png"
    Image.fromarray(rgba, mode="RGBA").save(ruta)

    resultado = analizar_imagen(ruta)

    assert resultado.legible is True
    assert resultado.fondo_uniforme is True
    assert len(resultado.vistas) == 1


# ---------------------------------------------------------------------------
# Vistas: cuántas, nombradas por posición, de izquierda a derecha
# ---------------------------------------------------------------------------


def _imagen_tres_figuras(ruta: Path) -> None:
    arr = _lienzo(140, 60)
    _pintar(arr, 10, 10, 20, 20)  # izquierda: filas 10-20, cols 10-20
    _pintar(arr, 10, 55, 20, 65)  # centro: filas 10-20, cols 55-65
    _pintar(arr, 10, 100, 20, 110)  # derecha: filas 10-20, cols 100-110
    _guardar(arr, ruta)


def test_tres_figuras_dan_tres_vistas_izquierda_centro_derecha(tmp_path: Path) -> None:
    ruta = tmp_path / "tres.png"
    _imagen_tres_figuras(ruta)

    resultado = analizar_imagen(ruta)

    assert resultado.fondo_uniforme is True
    assert [v.nombre for v in resultado.vistas] == ["izquierda", "centro", "derecha"]
    assert resultado.vistas[0].caja == (10, 10, 20, 20)
    assert resultado.vistas[1].caja == (10, 55, 20, 65)
    assert resultado.vistas[2].caja == (10, 100, 20, 110)


def test_dos_figuras_dan_izquierda_derecha(tmp_path: Path) -> None:
    arr = _lienzo(60, 40)
    _pintar(arr, 10, 30, 20, 40)  # a la derecha, se guarda primero en el array
    _pintar(arr, 10, 5, 20, 15)  # a la izquierda
    ruta = tmp_path / "dos.png"
    _guardar(arr, ruta)

    resultado = analizar_imagen(ruta)

    assert [v.nombre for v in resultado.vistas] == ["izquierda", "derecha"]
    # La "izquierda" es la que está más a la izquierda, sin importar el orden
    # en el que se pintó.
    assert resultado.vistas[0].caja[1] < resultado.vistas[1].caja[1]


def test_una_sola_figura_no_dispara_el_nombrado_de_varias(tmp_path: Path) -> None:
    arr = _lienzo(40, 40)
    _pintar(arr, 10, 10, 20, 20)
    ruta = tmp_path / "una.png"
    _guardar(arr, ruta)

    resultado = analizar_imagen(ruta)

    assert len(resultado.vistas) == 1


def test_mancha_menor_al_umbral_no_cuenta_como_vista(tmp_path: Path) -> None:
    arr = _lienzo(140, 60)
    _pintar(arr, 10, 10, 20, 20)  # figura grande: 10x10 = 100 px
    _pintar(arr, 50, 50, 53, 53)  # mancha pequeña: 3x3 = 9 px (<10% de 109)
    ruta = tmp_path / "mancha.png"
    _guardar(arr, ruta)

    resultado = analizar_imagen(ruta, area_min_pct=10.0)
    assert len(resultado.vistas) == 1

    # Con un umbral más bajo, la misma mancha sí cuenta.
    resultado_bajo = analizar_imagen(ruta, area_min_pct=1.0)
    assert len(resultado_bajo.vistas) == 2


# ---------------------------------------------------------------------------
# Recorte de una vista
# ---------------------------------------------------------------------------


def test_recorte_tiene_el_margen_y_pinta_fuera_de_su_componente(tmp_path: Path) -> None:
    arr = _lienzo(60, 60)
    _pintar(arr, 20, 5, 30, 15)  # "izquierda": filas 20-30, cols 5-15
    _pintar(arr, 20, 20, 30, 30)  # "derecha": filas 20-30, cols 20-30 (cerca)
    ruta = tmp_path / "vecinas.png"
    _guardar(arr, ruta)

    destino = tmp_path / "brief-diseno"
    ruta_salida = recortar_vista(ruta, "izquierda", destino, margen_px=10)

    assert ruta_salida == destino / "vecinas-vista-izquierda.png"
    assert ruta_salida.is_file()

    recorte = np.asarray(Image.open(ruta_salida).convert("RGB"))
    # Caja de "izquierda" (20,5,30,15) + margen 10, recortado a [0,60): filas
    # 10-40, cols 0-25.
    assert recorte.shape[:2] == (30, 25)

    # Dentro del recorte, un punto que en la imagen original pertenece a la
    # figura vecina ("derecha", original fila 25, col 22) queda pintado del
    # color de fondo, no del negro de la figura vecina.
    punto_vecino = recorte[25 - 10, 22 - 0]
    assert tuple(int(c) for c in punto_vecino) == BLANCO

    # Un punto propio de "izquierda" (original fila 25, col 10) sigue negro.
    punto_propio = recorte[25 - 10, 10 - 0]
    assert tuple(int(c) for c in punto_propio) == NEGRO


def test_recorte_no_modifica_la_imagen_original(tmp_path: Path) -> None:
    ruta = tmp_path / "original.png"
    _imagen_tres_figuras(ruta)
    original_bytes = ruta.read_bytes()

    recortar_vista(ruta, "centro", tmp_path / "brief-diseno")

    assert ruta.read_bytes() == original_bytes


def test_recorte_conserva_las_proporciones_de_la_caja(tmp_path: Path) -> None:
    # Figura rectangular (40x20, más ancha que alta) en un lienzo amplio para
    # que el margen no se recorte contra los bordes de la imagen.
    arr = _lienzo(200, 200)
    _pintar(arr, 80, 20, 100, 60)  # filas 80-100 (alto 20), cols 20-60 (ancho 40)
    ruta = tmp_path / "rectangulo.png"
    _guardar(arr, ruta)

    ruta_salida = recortar_vista(ruta, "izquierda", tmp_path / "brief-diseno", margen_px=10)
    recorte = np.asarray(Image.open(ruta_salida).convert("RGB"))

    alto_esperado = 20 + 2 * 10
    ancho_esperado = 40 + 2 * 10
    assert recorte.shape[:2] == (alto_esperado, ancho_esperado)
    # La proporción ancho/alto de la caja original (40/20 = 2) se mantiene del
    # mismo lado (más ancha que alta) en el recorte (60/40 = 1.5).
    assert (ancho_esperado / alto_esperado) > 1.0


def test_recorte_con_alfa_real_deja_transparente_fuera_de_la_figura(tmp_path: Path) -> None:
    ancho, alto = 40, 40
    rgb = _lienzo(ancho, alto, color=(10, 20, 30))
    alfa = np.zeros((alto, ancho), dtype=np.uint8)
    alfa[10:20, 10:20] = 255
    rgba = np.concatenate([rgb, alfa[..., None]], axis=-1)
    ruta = tmp_path / "alfa.png"
    Image.fromarray(rgba, mode="RGBA").save(ruta)

    ruta_salida = recortar_vista(ruta, "izquierda", tmp_path / "brief-diseno")
    recorte = Image.open(ruta_salida)

    assert recorte.mode == "RGBA"
    recorte_arr = np.asarray(recorte)
    # Esquina del recorte (fuera de la figura): transparente.
    assert recorte_arr[0, 0, 3] == 0


def test_recortar_vista_inexistente_lanza_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "una.png"
    arr = _lienzo(40, 40)
    _pintar(arr, 10, 10, 20, 20)
    _guardar(arr, ruta)

    with pytest.raises(ImagenBriefError):
        recortar_vista(ruta, "derecha", tmp_path / "brief-diseno")
