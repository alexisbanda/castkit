"""Tests de `castkit generar` y del módulo `castkit.generacion` (ticket #25)."""

from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

from castkit.cli import main
from castkit.diseno import DatosAlta, crear_diseno
from castkit.generacion import (
    InsumosBrief,
    _partir_comando,
    desregistrar_proveedor,
    recolectar_insumos_brief,
    registrar_proveedor,
)
from castkit.ingesta import sha256_archivo
from castkit.versiones import cargar_diseno

FIXTURES = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES / "cube.stl"


def _crear_diseno_con_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archivos_brief: dict[str, bytes | str],
    *,
    nombre: str = "guagua-pan",
) -> Path:
    carpeta_brief = tmp_path / "brief-origen"
    carpeta_brief.mkdir()
    for nombre_archivo_b, contenido in archivos_brief.items():
        ruta = carpeta_brief / nombre_archivo_b
        if isinstance(contenido, bytes):
            ruta.write_bytes(contenido)
        else:
            ruta.write_text(contenido, encoding="utf-8")

    destino = tmp_path / "disenos"
    destino.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(destino))

    datos = DatosAlta(
        nombre=nombre,
        brief=carpeta_brief,
        material="porcelana-fria",
        alto_mm=80.0,
        cavidades=4,
        cantidad=48,
        fecha=date(2026, 11, 1),
        licencia="propio",
        origen="brief de prueba",
    )
    return crear_diseno(datos, destino=destino)


class _ProveedorFakeCopiaCubo:
    """Proveedor enchufable doble de prueba que copia `cube.stl`."""

    def __init__(self) -> None:
        self.llamadas: list[InsumosBrief] = []

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        self.llamadas.append(insumos)
        shutil.copyfile(CUBE_STL, destino_stl)


class _ProveedorFakeInvalido:
    """Proveedor que escribe un archivo STL corrupto."""

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        destino_stl.write_bytes(b"no es un stl valido")


def test_recolectar_insumos_reconoce_imagenes_pdf_ai_texto_y_stl(tmp_path: Path) -> None:
    brief = tmp_path / "brief"
    brief.mkdir()
    (brief / "ref.png").write_bytes(b"\x89PNG\r\n")
    (brief / "foto.JPG").write_bytes(b"\xff\xd8\xff")
    (brief / "render.webp").write_bytes(b"RIFF")
    (brief / "ficha.pdf").write_bytes(b"%PDF-1.4")
    (brief / "vector.AI").write_bytes(b"%!PS-Adobe")
    (brief / "contexto.txt").write_text("Figura tradicional ecuatoriana", encoding="utf-8")
    (brief / "notas.md").write_text("Con sombrero", encoding="utf-8")
    shutil.copyfile(CUBE_STL, brief / "base.stl")

    insumos = recolectar_insumos_brief(brief, prompt_extra="acabado liso")
    assert [p.name for p in insumos.imagenes] == ["foto.JPG", "ref.png", "render.webp"]
    assert [p.name for p in insumos.documentos] == ["ficha.pdf", "vector.AI"]
    assert [p.name for p in insumos.archivos_texto] == ["contexto.txt", "notas.md"]
    assert [p.name for p in insumos.mallas_stl] == ["base.stl"]
    assert "Figura tradicional ecuatoriana" in insumos.prompt
    assert "Con sombrero" in insumos.prompt
    assert "acabado liso" in insumos.prompt


def test_generar_con_stl_opcional_en_brief_lo_usa_directamente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta = _crear_diseno_con_brief(
        tmp_path,
        monkeypatch,
        {
            "referencia.png": b"\x89PNG\r\n",
            "pieza.stl": CUBE_STL.read_bytes(),
        },
    )

    rc = main(["generar", "guagua-pan"])
    assert rc == 0
    salida = capsys.readouterr().out
    assert "stl-directo" in salida

    destino = carpeta / "input" / "guagua-pan_v1_bruto.stl"
    assert destino.is_file()
    assert sha256_archivo(destino) == sha256_archivo(CUBE_STL)

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["generacion"]
    assert reg["proveedor"] == "stl-directo"
    assert reg["archivos_brief"] == ["pieza.stl"]
    assert reg["caras"] == 12
    assert reg["vertices"] == 8
    assert reg["sha256"] == sha256_archivo(CUBE_STL)


def test_generar_con_proveedor_enchufable_desde_imagen_documento_y_texto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _ProveedorFakeCopiaCubo()
    registrar_proveedor("fake-ia", fake)
    try:
        carpeta = _crear_diseno_con_brief(
            tmp_path,
            monkeypatch,
            {
                "referencia.png": b"\x89PNG\r\n\x1a\n",
                "boceto.pdf": b"%PDF-1.4...",
                "diseno.ai": b"%!PS-Adobe...",
                "notas.txt": "Guagua de pan con decoración",
            },
        )

        rc = main(["generar", "guagua-pan", "--proveedor", "fake-ia", "--prompt", "sin socavados"])
        assert rc == 0
        assert len(fake.llamadas) == 1
        insumo = fake.llamadas[0]
        assert [p.name for p in insumo.imagenes] == ["referencia.png"]
        assert [p.name for p in insumo.documentos] == ["boceto.pdf", "diseno.ai"]
        assert "Guagua de pan con decoración" in insumo.prompt
        assert "sin socavados" in insumo.prompt

        destino = carpeta / "input" / "guagua-pan_v1_bruto.stl"
        assert destino.is_file()

        datos = cargar_diseno(carpeta)
        reg = datos["versiones"]["v1"]["parametros"]["generacion"]
        assert reg["proveedor"] == "fake-ia"
        assert reg["archivos_brief"] == ["referencia.png", "boceto.pdf", "diseno.ai", "notas.txt"]
        assert "sin socavados" in reg["prompt"]
        assert reg["caras"] == 12
        assert reg["vertices"] == 8
    finally:
        desregistrar_proveedor("fake-ia")


def test_generar_con_proveedor_comando_configurado_en_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "gen_stl.py"
    script.write_text(
        f"import os, shutil, sys\nshutil.copyfile({str(CUBE_STL)!r}, sys.argv[-1])\n",
        encoding="utf-8",
    )

    toml_custom = tmp_path / "fabricacion.toml"
    cmd_escaped = f"{sys.executable} {script}".replace("\\", "\\\\")
    toml_custom.write_text(
        "[limpieza]\n"
        "caras_max = 200000\n"
        "tolerancia_mm = 0.2\n"
        "corte_base_mm = 0.5\n"
        "agujero_max_mm = 10\n\n"
        "[generacion]\n"
        'proveedor = "comando"\n'
        f'comando = "{cmd_escaped}"\n\n'
        "[matriz]\n"
        "espesor_base_mm = 3.0\n"
        "margen_silicona_mm = 10.0\n"
        "espesor_pared_mm = 3.0\n"
        "margen_superior_mm = 10.0\n"
        "separacion_cavidades_mm = 10.0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CASTKIT_FABRICACION_TOML", str(toml_custom))

    carpeta = _crear_diseno_con_brief(
        tmp_path,
        monkeypatch,
        {"idea.txt": "Vela de guagua de pan"},
    )

    rc = main(["generar", "guagua-pan"])
    assert rc == 0
    assert (carpeta / "input" / "guagua-pan_v1_bruto.stl").is_file()


def test_partir_comando_conserva_rutas_de_windows() -> None:
    comando = r'D:\a\castkit\.venv\Scripts\python.exe "C:\Users\runner admin\gen_stl.py" --x'

    partes = _partir_comando(comando, posix=False)

    assert partes == [
        r"D:\a\castkit\.venv\Scripts\python.exe",
        r"C:\Users\runner admin\gen_stl.py",
        "--x",
    ]


def test_generar_falla_si_no_hay_insumos_soportados_en_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _ProveedorFakeCopiaCubo()
    registrar_proveedor("fake-ia", fake)
    try:
        carpeta = _crear_diseno_con_brief(
            tmp_path,
            monkeypatch,
            {"archivo.bin": b"1234"},
        )
        rc = main(["generar", "guagua-pan", "--proveedor", "fake-ia"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "no contiene insumos utilizables" in err
        assert not (carpeta / "input" / "guagua-pan_v1_bruto.stl").exists()
        assert "generacion" not in cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]
    finally:
        desregistrar_proveedor("fake-ia")


def test_generar_falla_si_proveedor_devuelve_stl_invalido_sin_dejar_rastro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    registrar_proveedor("fake-malo", _ProveedorFakeInvalido())
    try:
        carpeta = _crear_diseno_con_brief(
            tmp_path,
            monkeypatch,
            {"referencia.png": b"\x89PNG\r\n"},
        )
        rc = main(["generar", "guagua-pan", "--proveedor", "fake-malo"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "no contiene ninguna cara" in err or "STL" in err
        assert not (carpeta / "input" / "guagua-pan_v1_bruto.stl").exists()
        assert "generacion" not in cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]
    finally:
        desregistrar_proveedor("fake-malo")


def test_generar_protege_reemplazo_interactivo_y_con_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _ProveedorFakeCopiaCubo()
    registrar_proveedor("fake-ia", fake)
    try:
        _crear_diseno_con_brief(
            tmp_path,
            monkeypatch,
            {"referencia.png": b"\x89PNG\r\n"},
        )
        assert main(["generar", "guagua-pan", "--proveedor", "fake-ia"]) == 0

        # Sin terminal y sin --reemplazar -> código 2
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        rc = main(["generar", "guagua-pan", "--proveedor", "fake-ia"])
        assert rc == 2
        assert "--reemplazar" in capsys.readouterr().err

        # Con terminal respondiendo "n" -> no modifica nada (código 1)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _: "n")
        rc = main(["generar", "guagua-pan", "--proveedor", "fake-ia"])
        assert rc == 1

        # Con terminal respondiendo "sí" -> reemplaza (código 0)
        monkeypatch.setattr("builtins.input", lambda _: "sí")
        rc = main(["generar", "guagua-pan", "--proveedor", "fake-ia"])
        assert rc == 0

        # Con --reemplazar sin terminal -> reemplaza (código 0)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        rc = main(["generar", "guagua-pan", "--proveedor", "fake-ia", "--reemplazar"])
        assert rc == 0
    finally:
        desregistrar_proveedor("fake-ia")
