"""Tests de `castkit control1` y del módulo `castkit.control1` (ticket #10)."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from castkit.cli import main
from castkit.control1 import (
    VISTAS_CONTROL1,
    Control1Error,
    ejecutar_control1,
)
from castkit.diseno import DatosAlta, crear_diseno
from castkit.ingesta import ingerir_malla
from castkit.limpieza import limpiar_malla
from castkit.matriz import MatrizError, generar_matriz
from castkit.versiones import cargar_diseno

FIXTURES = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES / "cube.stl"


def _preparar_diseno_limpio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    nombre: str = "guagua-pan",
    alto_mm: float = 80.0,
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


def test_control1_genera_cuatro_png_con_nombre_de_version_y_medidas_sobreimpresas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=80.0)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    rc = main(["control1", "guagua-pan", "--sin-blender"])
    assert rc == 0

    esperados = [
        "guagua-pan_v1_render-frente.png",
        "guagua-pan_v1_render-perfil.png",
        "guagua-pan_v1_render-arriba.png",
        "guagua-pan_v1_render-3-4.png",
    ]
    hashes_centro: list[bytes] = []

    for vista, nombre_png in zip(VISTAS_CONTROL1, esperados, strict=True):
        ruta = carpeta / "output" / nombre_png
        assert ruta.is_file()

        with Image.open(ruta) as img:
            assert img.format == "PNG"
            assert img.size == (600, 600)

            medidas_meta = img.info.get("castkit_medidas", "")
            assert "Alto: 80.0 mm" in medidas_meta
            assert "Ancho: 80.5 mm" in medidas_meta
            assert "Fondo: 80.5 mm" in medidas_meta
            assert f"Vista: {vista}" in medidas_meta
            assert img.info.get("castkit_vista") == vista

            arr = np.asarray(img.convert("RGB"))
            # Verificar que la franja superior e inferior tienen texto sobreimpreso
            # (píxeles brillantes del texto sobre el fondo oscuro de la banda).
            franja_superior = arr[:44, :, :]
            franja_inferior = arr[-56:, :, :]
            assert int(franja_superior.max()) > 180
            assert int(franja_inferior.max()) > 180

            # Verificar que la zona central contiene el render de la figura.
            zona_central = arr[80:500, 80:520, :]
            assert float(zona_central.std()) > 5.0
            hashes_centro.append(zona_central.tobytes())

    # Las 4 vistas tienen diferencias visuales reales entre sí.
    assert len(set(hashes_centro)) == 4


def test_control1_aprobacion_registra_fecha_y_habilita_paso_a_matriz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=40.0, cavidades=2)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    # 1. Si se ejecuta control1 sin aprobar (queda "pendiente"), no puede pasar a matriz.
    assert main(["control1", "guagua-pan", "--sin-blender"]) == 0
    datos_pend = cargar_diseno(carpeta)
    reg_pend = datos_pend["versiones"]["v1"]["parametros"]["control_1"]
    assert reg_pend["estado"] == "pendiente"
    assert reg_pend["fecha"] == date.today().isoformat()

    rc_mat_bloqueada = main(["matriz", "guagua-pan", "--modo", "figura"])
    assert rc_mat_bloqueada == 1
    err_pend = capsys.readouterr().err
    assert "control 1" in err_pend
    assert not (carpeta / "output" / "guagua-pan_v1_matriz.stl").exists()

    # 2. Al aprobar con --aprobar, registra estado="aprobado" y fecha de hoy.
    assert main(["control1", "guagua-pan", "--aprobar", "--sin-blender"]) == 0
    datos_aprob = cargar_diseno(carpeta)
    reg_aprob = datos_aprob["versiones"]["v1"]["parametros"]["control_1"]
    assert reg_aprob["estado"] == "aprobado"
    assert reg_aprob["fecha"] == date.today().isoformat()
    assert reg_aprob["motivo"] is None
    assert len(reg_aprob["renders"]) == 4

    # 3. Ahora sí puede pasar a la caja de molde (matriz).
    rc_mat_ok = main(["matriz", "guagua-pan", "--modo", "cubeta"])
    assert rc_mat_ok == 0
    assert (carpeta / "output" / "guagua-pan_v1_matriz.stl").is_file()


def test_control1_rechazo_exige_motivo_lo_registra_y_bloquea_matriz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=50.0)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    # 1. Rechazar sin --motivo falla y no registra nada en diseno.json.
    rc_sin_motivo = main(["control1", "guagua-pan", "--rechazar", "--sin-blender"])
    assert rc_sin_motivo == 2
    err = capsys.readouterr().err
    assert "--motivo" in err
    assert "control_1" not in cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]

    # 2. Rechazar con --motivo en blanco falla y tampoco registra nada.
    rc_vacio = main(["control1", "guagua-pan", "--rechazar", "--motivo", "   ", "--sin-blender"])
    assert rc_vacio == 1
    assert "--motivo" in capsys.readouterr().err
    assert "control_1" not in cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]

    # 3. También desde la función Python directa exige motivo no vacío.
    with pytest.raises(Control1Error, match="--motivo"):
        ejecutar_control1(carpeta, decision="rechazado", motivo="  ", sin_blender=True)

    # 4. Rechazar con motivo válido lo registra en la versión con fecha
    # y deja el diseño sin avanzar.
    rc_rechazo = main(
        [
            "control1",
            "guagua-pan",
            "--rechazar",
            "--motivo",
            "la cara quedó aplastada",
            "--sin-blender",
        ]
    )
    assert rc_rechazo == 0

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["control_1"]
    assert reg["estado"] == "rechazado"
    assert reg["motivo"] == "la cara quedó aplastada"
    assert reg["fecha"] == date.today().isoformat()

    # Intentar avanzar a matriz falla mencionando el rechazo y su motivo.
    rc_matriz = main(["matriz", "guagua-pan", "--modo", "figura"])
    assert rc_matriz == 1
    err_mat = capsys.readouterr().err
    assert "rechazada en el punto de control 1" in err_mat
    assert "la cara quedó aplastada" in err_mat
    assert not (carpeta / "output" / "guagua-pan_v1_matriz.stl").exists()


def test_control1_interactivo_aprueba_y_rechaza_con_motivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=45.0)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    # 1. En terminal interactiva, responder "n" y dejar motivo vacío falla.
    respuestas = iter(["n", "   "])
    monkeypatch.setattr("builtins.input", lambda _: next(respuestas))
    rc_mal = main(["control1", "guagua-pan", "--sin-blender"])
    assert rc_mal == 1
    assert "motivo" in capsys.readouterr().err

    # 2. Responder "n" y dar motivo válido registra el rechazo.
    respuestas_rechazo = iter(["n", "proporciones incorrectas"])
    monkeypatch.setattr("builtins.input", lambda _: next(respuestas_rechazo))
    rc_rech = main(["control1", "guagua-pan", "--sin-blender"])
    assert rc_rech == 0
    reg_r = cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]["control_1"]
    assert reg_r["estado"] == "rechazado"
    assert reg_r["motivo"] == "proporciones incorrectas"

    # 3. Responder "sí" registra la aprobación.
    monkeypatch.setattr("builtins.input", lambda _: "sí")
    rc_aprob = main(["control1", "guagua-pan", "--sin-blender"])
    assert rc_aprob == 0
    reg_a = cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]["control_1"]
    assert reg_a["estado"] == "aprobado"
    assert reg_a["motivo"] is None


def test_control1_validaciones_de_error_y_exigir_control1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=40.0)

    # --aprobar y --rechazar a la vez se rechaza.
    rc = main(["control1", "guagua-pan", "--aprobar", "--rechazar"])
    assert rc == 1
    assert "--aprobar" in capsys.readouterr().err

    # generar_matriz con exigir_control1=True falla si aún no se corrió control1.
    with pytest.raises(MatrizError, match="control 1"):
        generar_matriz(
            carpeta,
            modo="figura",
            espesor_base_mm=3.0,
            margen_silicona_mm=10.0,
            espesor_pared_mm=3.0,
            margen_superior_mm=10.0,
            separacion_cavidades_mm=10.0,
            exigir_control1=True,
        )


def test_matriz_cli_se_niega_si_control1_nunca_se_ejecuto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=40.0)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    rc = main(["matriz", "guagua-pan", "--modo", "figura"])
    assert rc == 1
    assert "control 1" in capsys.readouterr().err
    assert not (carpeta / "output" / "guagua-pan_v1_matriz.stl").exists()


def test_control1_blender_sin_renders_recurre_a_matplotlib_y_no_reusa_pngs_viejos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import castkit.control1 as mod_c1

    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=60.0)
    # Renders de una corrida anterior que no deben confundirse con los nuevos.
    primera = ejecutar_control1(carpeta, sin_blender=True)
    marcas_previas = {r: r.stat().st_mtime_ns for r in primera["renders"]}

    monkeypatch.setattr(mod_c1, "find_blender", lambda: Path("/usr/bin/blender"))
    # Blender "termina bien" pero el script falló antes de escribir los PNG.
    monkeypatch.setattr(mod_c1, "run_blender_script", lambda script, *args: None)

    resultado = ejecutar_control1(carpeta, sin_blender=False)
    assert resultado["metodo"] == "matplotlib"
    for ruta in resultado["renders"]:
        assert ruta.is_file()
        assert ruta.stat().st_mtime_ns != marcas_previas[ruta]
        with Image.open(ruta) as img:
            assert "Alto: 60.0 mm" in img.info.get("castkit_medidas", "")


def test_control1_invoca_blender_headless_cuando_esta_disponible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import castkit.control1 as mod_c1

    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=60.0)
    llamadas_blender: list[tuple[Path, tuple[str, ...]]] = []

    monkeypatch.setattr(mod_c1, "find_blender", lambda: Path("/usr/bin/blender"))

    def _fake_run_blender(script: Path, *args: str) -> None:
        llamadas_blender.append((script, args))
        for idx, ruta_str in enumerate(args[1:5]):
            img = Image.new("RGB", (600, 600), color=(60 + idx * 30, 80 + idx * 20, 110))
            img.save(ruta_str, format="PNG")

    monkeypatch.setattr(mod_c1, "run_blender_script", _fake_run_blender)

    resultado = ejecutar_control1(carpeta, decision="aprobado", sin_blender=False)
    assert resultado["metodo"] == "blender"
    assert len(llamadas_blender) == 1
    assert llamadas_blender[0][0].name == "render_vistas.py"

    for ruta in resultado["renders"]:
        assert ruta.is_file()
        with Image.open(ruta) as img:
            assert img.format == "PNG"
            assert "Alto: 60.0 mm" in img.info.get("castkit_medidas", "")


@pytest.mark.blender
def test_control1_con_blender_headless_genera_los_cuatro_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=60.0)
    resultado = ejecutar_control1(carpeta, decision="aprobado", sin_blender=False)
    assert resultado["metodo"] == "blender"
    for ruta in resultado["renders"]:
        assert ruta.is_file()
        with Image.open(ruta) as img:
            assert img.format == "PNG"
            assert "Alto: 60.0 mm" in img.info.get("castkit_medidas", "")


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(cmd="blender", timeout=300),
        PermissionError("blender no es ejecutable"),
    ],
    ids=["timeout", "no-ejecutable"],
)
def test_control1_blender_colgado_o_no_ejecutable_recurre_a_matplotlib(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    import castkit.control1 as mod_c1

    carpeta = _preparar_diseno_limpio(tmp_path, monkeypatch, alto_mm=60.0)
    monkeypatch.setattr(mod_c1, "find_blender", lambda: Path("/usr/bin/blender"))

    def _fake_run_blender(script: Path, *args: str) -> None:
        raise error

    monkeypatch.setattr(mod_c1, "run_blender_script", _fake_run_blender)

    resultado = ejecutar_control1(carpeta, sin_blender=False)
    assert resultado["metodo"] == "matplotlib"
    for ruta in resultado["renders"]:
        assert ruta.is_file()
