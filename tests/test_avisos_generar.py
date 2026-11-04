"""Tests de los avisos de `castkit generar`/`castkit flujo` sobre el proveedor usado,
la imagen elegida y las vistas detectadas en la imagen (ticket #94).

Los helpers de este módulo son locales a este archivo (igual convención que
`tests/test_alta_guiada.py`). Reutiliza, adaptados aquí, los patrones de
`tests/test_profundidad.py` (estimador de profundidad sintético, sin torch) y
`tests/test_alta_guiada.py`/`tests/test_generacion.py` (proveedor enchufable de
prueba, `blender_falso`, proveedor «comando» con un script real).
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from castkit import profundidad
from castkit.cli import main
from castkit.diseno import DatosAlta, crear_diseno
from castkit.generacion import (
    InsumosBrief,
    ProveedorProfundidad,
    desregistrar_proveedor,
    registrar_proveedor,
)
from castkit.imagen_brief import analizar_imagen
from castkit.ingesta import sha256_archivo

FIXTURES = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES / "cube.stl"

BLANCO = (255, 255, 255)
NEGRO = (0, 0, 0)


# ---------------------------------------------------------------------------
# Imágenes sintéticas
# ---------------------------------------------------------------------------


def _lienzo(ancho: int, alto: int) -> np.ndarray:
    arr = np.zeros((alto, ancho, 3), dtype=np.uint8)
    arr[:, :] = BLANCO
    return arr


def _pintar(arr: np.ndarray, fila0: int, col0: int, fila1: int, col1: int) -> None:
    arr[fila0:fila1, col0:col1] = NEGRO


def _guardar(arr: np.ndarray, ruta: Path) -> None:
    Image.fromarray(arr, mode="RGB").save(ruta)


def _imagen_una_vista(ruta: Path) -> None:
    """Una sola figura, con dos colores (no solo negro): con un único color, el
    proveedor «relieve» degenera (su único color de figura vuelve trivial el
    `argmin` de clases y clasifica toda la imagen como fondo; ver
    `tests/test_relieve.py`, que usa el mismo patrón de 2+ colores).
    """
    arr = _lienzo(60, 60)
    _pintar(arr, 10, 10, 50, 50)
    arr[20:40, 20:40] = (90, 90, 90)
    _guardar(arr, ruta)


def _imagen_tres_vistas_centro_mayor(ruta: Path) -> None:
    """Tres figuras separadas; la del centro es, a propósito, la más grande (#94:
    el aviso no debe asumir que la vista mayor es siempre la de la izquierda).
    Áreas: izquierda=100 px, centro=300 px, derecha=100 px (total 500 px), todas
    por encima del umbral por defecto de `vista_area_min_pct` (10 % de 500 = 50).
    """
    arr = _lienzo(200, 60)
    _pintar(arr, 20, 10, 30, 20)  # izquierda: 10x10 = 100 px
    _pintar(arr, 10, 80, 25, 100)  # centro: 15x20 = 300 px (la mayor)
    _pintar(arr, 20, 170, 30, 180)  # derecha: 10x10 = 100 px
    _guardar(arr, ruta)


def _imagen_figura_y_mancha(ruta: Path, *, lado_mancha: int) -> None:
    """Figura grande (100 px) + una mancha cuyo tamaño decide si cuenta como vista
    (igual patrón que `tests/test_imagen_brief.py::test_mancha_menor_al_umbral...`).
    """
    arr = _lienzo(140, 60)
    _pintar(arr, 10, 10, 20, 20)  # figura grande: 10x10 = 100 px
    _pintar(arr, 40, 100, 40 + lado_mancha, 100 + lado_mancha)
    _guardar(arr, ruta)


# ---------------------------------------------------------------------------
# Alta de diseños de prueba
# ---------------------------------------------------------------------------


def _crear_diseno(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    nombre: str = "guagua-prueba",
    archivos_brief: dict[str, bytes | str] | None = None,
    imagenes: dict[str, Callable[[Path], None]] | None = None,
    stl_en_brief: bool = False,
    tipo_molde: str = "completo",
    largo_mm: float | None = None,
    grosor_mm: float | None = None,
    alto_mm: float | None = 40.0,
    proveedor: str | None = None,
    modo_matriz: str | None = None,
    imagen: str | None = None,
) -> Path:
    brief = tmp_path / f"brief-{nombre}"
    brief.mkdir()
    for nombre_archivo, contenido in (archivos_brief or {}).items():
        ruta = brief / nombre_archivo
        if isinstance(contenido, bytes):
            ruta.write_bytes(contenido)
        else:
            ruta.write_text(contenido, encoding="utf-8")
    for nombre_archivo, constructor in (imagenes or {}).items():
        constructor(brief / nombre_archivo)
    if stl_en_brief:
        shutil.copyfile(CUBE_STL, brief / "modelo.stl")

    destino = tmp_path / "disenos"
    destino.mkdir(exist_ok=True)
    monkeypatch.setenv("CASTKIT_DISENOS", str(destino))

    datos = DatosAlta(
        nombre=nombre,
        brief=brief,
        material="porcelana-fria",
        alto_mm=None if tipo_molde == "abierto" else alto_mm,
        cavidades=1,
        cantidad=1,
        fecha=date(2026, 11, 1),
        licencia="propio",
        origen="prueba #94",
        tipo_molde=tipo_molde,
        largo_mm=largo_mm if tipo_molde == "abierto" else None,
        grosor_mm=grosor_mm if tipo_molde == "abierto" else None,
        proveedor=proveedor,
        modo_matriz=modo_matriz,
        imagen=imagen,
    )
    return crear_diseno(datos, destino=destino)


class _ProveedorFalso:
    """Proveedor enchufable de prueba: copia `cube.stl` sin tocar los insumos."""

    def __init__(self) -> None:
        self.llamadas = 0

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        self.llamadas += 1
        shutil.copyfile(CUBE_STL, destino_stl)


def _config_profundidad_completa() -> dict[str, object]:
    return {
        "modelo": profundidad.MODELO_PERMITIDO,
        "revision": "main",
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
    """Mapa de profundidad sintético (sin torch): el inverso de la luminancia."""
    imagen = Image.open(ruta_imagen).convert("L")
    gris = np.asarray(imagen, dtype=np.float32)
    return 255.0 - gris


def _proveedor_profundidad_sintetico() -> ProveedorProfundidad:
    return ProveedorProfundidad(
        _config_profundidad_completa(), estimar_profundidad=_estimador_sintetico
    )


def _toml_generacion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, vista_area_min_pct: float
) -> None:
    """`fabricacion.toml` propio con `[generacion].vista_area_min_pct` a medida."""
    toml_custom = tmp_path / "fabricacion.toml"
    toml_custom.write_text(
        "[generacion]\n"
        'proveedor = "profundidad"\n'
        'comando = ""\n'
        f"vista_area_min_pct = {vista_area_min_pct}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CASTKIT_FABRICACION_TOML", str(toml_custom))


# ---------------------------------------------------------------------------
# Criterio: `--proveedor relieve` con 3 vistas falla y no escribe nada.
# ---------------------------------------------------------------------------


def test_relieve_con_tres_vistas_falla_y_no_escribe_malla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _crear_diseno(
        tmp_path,
        monkeypatch,
        imagenes={"frontal.png": _imagen_tres_vistas_centro_mayor},
        tipo_molde="abierto",
        largo_mm=50.0,
        grosor_mm=10.0,
    )

    rc = main(["generar", "guagua-prueba", "--proveedor", "relieve"])

    assert rc != 0
    assert not (carpeta / "input" / "guagua-prueba_v1_bruto.stl").exists()
    salida = capsys.readouterr()
    assert "frontal.png" in salida.err
    assert "3 vistas" in salida.err
    assert "--proveedor profundidad" in salida.err


def test_relieve_con_tres_vistas_y_reemplazar_conserva_la_malla_previa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _crear_diseno(
        tmp_path,
        monkeypatch,
        imagenes={"frontal.png": _imagen_una_vista},
        tipo_molde="abierto",
        largo_mm=50.0,
        grosor_mm=10.0,
    )

    rc1 = main(["generar", "guagua-prueba", "--proveedor", "relieve"])
    assert rc1 == 0
    destino = carpeta / "input" / "guagua-prueba_v1_bruto.stl"
    assert destino.is_file()
    huella_previa = sha256_archivo(destino)

    # Se cambia el contenido de la MISMA imagen por una de tres vistas.
    _imagen_tres_vistas_centro_mayor(carpeta / "brief" / "frontal.png")

    rc2 = main(["generar", "guagua-prueba", "--proveedor", "relieve", "--reemplazar"])

    assert rc2 != 0
    assert sha256_archivo(destino) == huella_previa


# ---------------------------------------------------------------------------
# Criterio: `--proveedor profundidad` con varias vistas genera e informa cuál
# modela (la de mayor área, sin asumir que es la izquierda).
# ---------------------------------------------------------------------------


def test_profundidad_con_tres_vistas_informa_cuantas_y_modela_la_mayor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _crear_diseno(
        tmp_path,
        monkeypatch,
        imagenes={"frontal.png": _imagen_tres_vistas_centro_mayor},
        tipo_molde="abierto",
        largo_mm=50.0,
        grosor_mm=10.0,
    )
    proveedor = _proveedor_profundidad_sintetico()
    registrar_proveedor("profundidad", proveedor)
    try:
        rc = main(["generar", "guagua-prueba", "--proveedor", "profundidad"])
    finally:
        desregistrar_proveedor("profundidad")

    assert rc == 0
    assert (carpeta / "input" / "guagua-prueba_v1_bruto.stl").is_file()
    salida = capsys.readouterr().out
    assert "encontré 3 vistas en frontal.png" in salida
    assert "se modelará la del centro." in salida
    assert "izquierda" not in salida

    # La vista que imprime el aviso coincide, por posición, con la componente que
    # «profundidad» realmente modela (la mayor según su propia máscara: ver #94).
    ruta_imagen = carpeta / "brief" / "frontal.png"
    rgb, alfa = profundidad._cargar_rgb_y_alfa(ruta_imagen)
    mascara = profundidad._mascara_figura(rgb, alfa)
    filas, columnas = np.where(mascara)
    centro_fila, centro_col = filas.mean(), columnas.mean()

    analisis = analizar_imagen(ruta_imagen)
    vista_centro = next(v for v in analisis.vistas if v.nombre == "centro")
    fila_min, col_min, fila_max, col_max = vista_centro.caja
    assert fila_min <= centro_fila < fila_max
    assert col_min <= centro_col < col_max


# ---------------------------------------------------------------------------
# Criterio: dos o más imágenes sin elección guardada informan cuál se usó;
# con elección guardada, o con un proveedor que recibe todas, no se informa.
# ---------------------------------------------------------------------------


def test_dos_imagenes_sin_eleccion_guardada_informa_cual_se_usa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("fake-imagenes-sin-eleccion", fake)
    try:
        _crear_diseno(
            tmp_path,
            monkeypatch,
            imagenes={"a.png": _imagen_una_vista, "b.png": _imagen_una_vista},
            proveedor="fake-imagenes-sin-eleccion",
        )
        rc = main(["generar", "guagua-prueba"])
    finally:
        desregistrar_proveedor("fake-imagenes-sin-eleccion")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Usando a.png, la primera de 2 imágenes del brief." in salida


def test_dos_imagenes_con_eleccion_guardada_no_informa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("fake-imagenes-con-eleccion", fake)
    try:
        _crear_diseno(
            tmp_path,
            monkeypatch,
            imagenes={"a.png": _imagen_una_vista, "b.png": _imagen_una_vista},
            proveedor="fake-imagenes-con-eleccion",
            imagen="b.png",
        )
        rc = main(["generar", "guagua-prueba"])
    finally:
        desregistrar_proveedor("fake-imagenes-con-eleccion")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Usando" not in salida


def test_proveedor_comando_con_comando_real_no_informa_imagen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "gen_stl.py"
    script.write_text(
        f"import shutil, sys\nshutil.copyfile({str(CUBE_STL)!r}, sys.argv[-1])\n",
        encoding="utf-8",
    )
    toml_custom = tmp_path / "fabricacion.toml"
    cmd_escaped = f"{sys.executable} {script}".replace("\\", "\\\\")
    toml_custom.write_text(
        f'[generacion]\nproveedor = "comando"\ncomando = "{cmd_escaped}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CASTKIT_FABRICACION_TOML", str(toml_custom))

    _crear_diseno(
        tmp_path,
        monkeypatch,
        imagenes={"a.png": _imagen_una_vista, "b.png": _imagen_una_vista},
    )

    rc = main(["generar", "guagua-prueba"])

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Usando" not in salida


# ---------------------------------------------------------------------------
# Criterio: `generar` y `flujo` informan el proveedor usado y su origen (flag,
# versión o configuración), y el recordado cuando el flag difiere.
# ---------------------------------------------------------------------------


def test_generar_informa_proveedor_por_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("fake-flag", fake)
    try:
        _crear_diseno(tmp_path, monkeypatch, archivos_brief={"idea.txt": "prueba"})
        rc = main(["generar", "guagua-prueba", "--proveedor", "fake-flag"])
    finally:
        desregistrar_proveedor("fake-flag")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Proveedor: fake-flag (del flag --proveedor)." in salida


def test_generar_informa_proveedor_por_flag_cuando_difiere_del_recordado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    guardado = _ProveedorFalso()
    explicito = _ProveedorFalso()
    registrar_proveedor("fake-guardado", guardado)
    registrar_proveedor("fake-explicito", explicito)
    try:
        _crear_diseno(
            tmp_path,
            monkeypatch,
            archivos_brief={"idea.txt": "prueba"},
            proveedor="fake-guardado",
        )
        rc = main(["generar", "guagua-prueba", "--proveedor", "fake-explicito"])
    finally:
        desregistrar_proveedor("fake-guardado")
        desregistrar_proveedor("fake-explicito")

    assert rc == 0
    salida = capsys.readouterr().out
    assert (
        "Proveedor: fake-explicito (del flag --proveedor; la versión recuerda "
        "fake-guardado)." in salida
    )


def test_generar_informa_proveedor_recordado_en_la_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("fake-recordado", fake)
    try:
        _crear_diseno(
            tmp_path,
            monkeypatch,
            archivos_brief={"idea.txt": "prueba"},
            proveedor="fake-recordado",
        )
        rc = main(["generar", "guagua-prueba"])
    finally:
        desregistrar_proveedor("fake-recordado")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Proveedor: fake-recordado (recordado en la versión)." in salida


def test_generar_informa_proveedor_de_la_configuracion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("comando", fake)
    try:
        _crear_diseno(tmp_path, monkeypatch, archivos_brief={"idea.txt": "prueba"})
        rc = main(["generar", "guagua-prueba"])
    finally:
        desregistrar_proveedor("comando")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Proveedor: comando (de la configuración)." in salida


def test_flujo_informa_proveedor_por_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    blender_falso: object,
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("fake-flujo-flag", fake)
    try:
        _crear_diseno(
            tmp_path,
            monkeypatch,
            archivos_brief={"idea.txt": "prueba"},
            modo_matriz="figura",
        )
        rc = main(
            ["flujo", "guagua-prueba", "--proveedor", "fake-flujo-flag", "--aprobar-control1"]
        )
    finally:
        desregistrar_proveedor("fake-flujo-flag")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Proveedor: fake-flujo-flag (del flag --proveedor)." in salida


def test_flujo_informa_proveedor_recordado_en_la_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    blender_falso: object,
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("fake-flujo-recordado", fake)
    try:
        _crear_diseno(
            tmp_path,
            monkeypatch,
            archivos_brief={"idea.txt": "prueba"},
            proveedor="fake-flujo-recordado",
            modo_matriz="figura",
        )
        rc = main(["flujo", "guagua-prueba", "--aprobar-control1"])
    finally:
        desregistrar_proveedor("fake-flujo-recordado")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Proveedor: fake-flujo-recordado (recordado en la versión)." in salida


def test_flujo_informa_proveedor_de_la_configuracion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    blender_falso: object,
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("comando", fake)
    try:
        _crear_diseno(
            tmp_path,
            monkeypatch,
            archivos_brief={"idea.txt": "prueba"},
            modo_matriz="figura",
        )
        rc = main(["flujo", "guagua-prueba", "--aprobar-control1"])
    finally:
        desregistrar_proveedor("comando")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Proveedor: comando (de la configuración)." in salida


# ---------------------------------------------------------------------------
# Criterio: el aviso de vistas usa el mismo umbral que `alta`
# (`vista_area_min_pct`); con una sola vista, ningún «Aviso:» ni «vistas».
# ---------------------------------------------------------------------------


def test_aviso_de_vistas_usa_el_umbral_configurado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _crear_diseno(
        tmp_path,
        monkeypatch,
        imagenes={"frontal.png": lambda ruta: _imagen_figura_y_mancha(ruta, lado_mancha=3)},
        tipo_molde="abierto",
        largo_mm=50.0,
        grosor_mm=10.0,
    )
    _toml_generacion(tmp_path, monkeypatch, vista_area_min_pct=1.0)

    proveedor = _proveedor_profundidad_sintetico()
    registrar_proveedor("profundidad", proveedor)
    try:
        rc = main(["generar", "guagua-prueba", "--proveedor", "profundidad"])
    finally:
        desregistrar_proveedor("profundidad")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "encontré 2 vistas" in salida


def test_con_umbral_por_defecto_la_mancha_no_cuenta_y_no_hay_aviso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _crear_diseno(
        tmp_path,
        monkeypatch,
        imagenes={"frontal.png": lambda ruta: _imagen_figura_y_mancha(ruta, lado_mancha=3)},
        tipo_molde="abierto",
        largo_mm=50.0,
        grosor_mm=10.0,
    )
    # Config real del repo (fixture autouse): vista_area_min_pct = 10.

    proveedor = _proveedor_profundidad_sintetico()
    registrar_proveedor("profundidad", proveedor)
    try:
        rc = main(["generar", "guagua-prueba", "--proveedor", "profundidad"])
    finally:
        desregistrar_proveedor("profundidad")

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Aviso:" not in salida
    assert "vistas" not in salida


# ---------------------------------------------------------------------------
# Criterio: la vía stl-directo informa ese origen y ningún aviso de imagen o
# de vistas.
# ---------------------------------------------------------------------------


def test_via_stl_directo_informa_origen_sin_avisos_de_imagen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _crear_diseno(
        tmp_path,
        monkeypatch,
        imagenes={"a.png": _imagen_una_vista, "b.png": _imagen_una_vista},
        stl_en_brief=True,
    )

    rc = main(["generar", "guagua-prueba"])

    assert rc == 0
    salida = capsys.readouterr().out
    assert "Proveedor: stl-directo (hay un STL en el brief)." in salida
    assert "Usando" not in salida
    assert "Aviso:" not in salida
