"""Tests de `castkit generar` y del módulo `castkit.generacion` (ticket #25, #36)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pytest
import trimesh

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


class _FakeHTTPResp:
    """Respuesta simulada de `urllib.request.urlopen` (compatible con `with`)."""

    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> _FakeHTTPResp:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _escribir_toml_tripo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tripo_modelo: str | None = None,
    tripo_espera_max_s: float | None = None,
) -> None:
    """Escribe un `fabricacion.toml` propio con `[generacion]` apuntando a tripo3d."""
    lineas_generacion = ["[generacion]", 'proveedor = "tripo3d"', 'comando = ""']
    if tripo_modelo is not None:
        lineas_generacion.append(f'tripo_modelo = "{tripo_modelo}"')
    if tripo_espera_max_s is not None:
        lineas_generacion.append(f"tripo_espera_max_s = {tripo_espera_max_s}")

    toml_custom = tmp_path / "fabricacion.toml"
    toml_custom.write_text(
        "[limpieza]\n"
        "caras_max = 200000\n"
        "tolerancia_mm = 0.2\n"
        "corte_base_mm = 0.5\n"
        "agujero_max_mm = 10\n\n" + "\n".join(lineas_generacion) + "\n\n"
        "[matriz]\n"
        "espesor_base_mm = 3.0\n"
        "margen_silicona_mm = 10.0\n"
        "espesor_pared_mm = 3.0\n"
        "margen_superior_mm = 10.0\n"
        "separacion_cavidades_mm = 10.0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CASTKIT_FABRICACION_TOML", str(toml_custom))


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


def test_tripo3d_v3_usa_rutas_cabeceras_y_cuerpo_sin_textura(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Imagen -> 3D en la API v3: rutas, cabecera Bearer, campo "file" y texture/pbr en false."""
    _escribir_toml_tripo(tmp_path, monkeypatch, tripo_modelo="v3-custom")

    carpeta = _crear_diseno_con_brief(
        tmp_path,
        monkeypatch,
        {"referencia.png": b"\x89PNG\r\n\x1a\n"},
    )
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test")
    monkeypatch.setattr(time, "sleep", lambda s: None)

    caja = trimesh.creation.box(extents=[10, 40, 10])
    glb_bytes = caja.export(file_type="glb")

    peticiones: list[dict] = []

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 60) -> _FakeHTTPResp:
        url = req.full_url
        peticiones.append(
            {
                "metodo": req.get_method(),
                "url": url,
                "auth": req.get_header("Authorization"),
                "content_type": req.get_header("Content-type"),
                "body": req.data,
            }
        )
        if url == "https://openapi.tripo3d.ai/v3/files":
            return _FakeHTTPResp(
                json.dumps({"code": 0, "data": {"file_token": "tok-123"}}).encode()
            )
        if url == "https://openapi.tripo3d.ai/v3/generation/image-to-model":
            return _FakeHTTPResp(json.dumps({"code": 0, "data": {"task_id": "task-abc"}}).encode())
        if url == "https://openapi.tripo3d.ai/v3/tasks/task-abc":
            return _FakeHTTPResp(
                json.dumps(
                    {
                        "code": 0,
                        "data": {
                            "status": "success",
                            "output": {"model_url": "https://cdn.tripo3d.ai/model.glb"},
                        },
                    }
                ).encode()
            )
        if url == "https://cdn.tripo3d.ai/model.glb":
            return _FakeHTTPResp(glb_bytes)
        raise AssertionError(f"URL inesperada en el proveedor tripo3d: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    rc = main(["generar", "guagua-pan", "--proveedor", "tripo3d"])
    assert rc == 0

    urls = [p["url"] for p in peticiones]
    assert all("api.tripo3d.ai/v2" not in u for u in urls)
    assert "https://openapi.tripo3d.ai/v3/files" in urls
    assert "https://openapi.tripo3d.ai/v3/generation/image-to-model" in urls
    assert "https://openapi.tripo3d.ai/v3/tasks/task-abc" in urls

    for p in peticiones:
        if "openapi.tripo3d.ai" in p["url"]:
            assert p["auth"] == "Bearer tsk_test"

    cuerpo_subida = next(
        p["body"] for p in peticiones if p["url"] == "https://openapi.tripo3d.ai/v3/files"
    )
    assert b'name="file"' in cuerpo_subida

    cuerpo_tarea = next(
        p["body"]
        for p in peticiones
        if p["url"] == "https://openapi.tripo3d.ai/v3/generation/image-to-model"
    )
    payload = json.loads(cuerpo_tarea.decode("utf-8"))
    assert payload == {
        "input": "tok-123",
        "model": "v3-custom",
        "texture": False,
        "pbr": False,
    }

    # El GLB de prueba (altura 40 en Y, convención glTF) debe quedar con la
    # altura en Z tras la conversión.
    destino = carpeta / "input" / "guagua-pan_v1_bruto.stl"
    assert destino.is_file()
    malla_resultante = trimesh.load(destino)
    assert malla_resultante.extents[2] == pytest.approx(40.0, abs=0.5)

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["generacion"]
    assert reg["tripo_modelo"] == "v3-custom"
    assert reg["tripo_task_id"] == "task-abc"


def test_tripo3d_v3_texto_a_modelo_usa_modelo_por_defecto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin `tripo_modelo` en la config, usa el valor por defecto `v2.5-20250123`."""
    carpeta = _crear_diseno_con_brief(
        tmp_path,
        monkeypatch,
        {"idea.txt": "Vela con forma de guagua de pan"},
    )
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test")
    monkeypatch.setattr(time, "sleep", lambda s: None)

    caja = trimesh.creation.box(extents=[10, 40, 10])
    glb_bytes = caja.export(file_type="glb")

    peticiones: list[dict] = []

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 60) -> _FakeHTTPResp:
        url = req.full_url
        peticiones.append({"url": url, "body": req.data})
        if url == "https://openapi.tripo3d.ai/v3/generation/text-to-model":
            return _FakeHTTPResp(json.dumps({"code": 0, "data": {"task_id": "task-txt"}}).encode())
        if url == "https://openapi.tripo3d.ai/v3/tasks/task-txt":
            return _FakeHTTPResp(
                json.dumps(
                    {
                        "code": 0,
                        "data": {
                            "status": "success",
                            "output": {"model_url": "https://cdn.tripo3d.ai/m.glb"},
                        },
                    }
                ).encode()
            )
        if url == "https://cdn.tripo3d.ai/m.glb":
            return _FakeHTTPResp(glb_bytes)
        raise AssertionError(f"URL inesperada en el proveedor tripo3d: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    rc = main(["generar", "guagua-pan", "--proveedor", "tripo3d"])
    assert rc == 0

    cuerpo_tarea = next(
        p["body"]
        for p in peticiones
        if p["url"] == "https://openapi.tripo3d.ai/v3/generation/text-to-model"
    )
    payload = json.loads(cuerpo_tarea.decode("utf-8"))
    assert payload["prompt"]
    assert payload["model"] == "v2.5-20250123"
    assert payload["texture"] is False
    assert payload["pbr"] is False

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["generacion"]
    assert reg["tripo_modelo"] == "v2.5-20250123"
    assert reg["tripo_task_id"] == "task-txt"


def test_tripo3d_v3_plazo_configurable_agota_tiempo_sin_escribir_malla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Con el estado siempre "running" y un plazo pequeño, falla sin escribir malla."""
    _escribir_toml_tripo(tmp_path, monkeypatch, tripo_espera_max_s=5)

    carpeta = _crear_diseno_con_brief(
        tmp_path,
        monkeypatch,
        {"idea.txt": "Vela con forma de guagua de pan"},
    )
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test")

    contador = {"valor": 0.0}

    def _fake_monotonic() -> float:
        contador["valor"] += 100.0
        return contador["valor"]

    monkeypatch.setattr(time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 60) -> _FakeHTTPResp:
        url = req.full_url
        if url == "https://openapi.tripo3d.ai/v3/generation/text-to-model":
            return _FakeHTTPResp(
                json.dumps({"code": 0, "data": {"task_id": "task-lento"}}).encode()
            )
        if url == "https://openapi.tripo3d.ai/v3/tasks/task-lento":
            return _FakeHTTPResp(json.dumps({"code": 0, "data": {"status": "running"}}).encode())
        raise AssertionError(f"URL inesperada en el proveedor tripo3d: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    rc = main(["generar", "guagua-pan", "--proveedor", "tripo3d"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "task-lento" in err
    assert "5 s" in err

    assert not (carpeta / "input" / "guagua-pan_v1_bruto.stl").exists()
    assert "generacion" not in cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]


def test_tripo3d_v3_estado_failed_no_escribe_malla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un estado `failed` en el sondeo termina en error, sin dejar malla escrita."""
    carpeta = _crear_diseno_con_brief(
        tmp_path,
        monkeypatch,
        {"idea.txt": "Vela con forma de guagua de pan"},
    )
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test")
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 60) -> _FakeHTTPResp:
        url = req.full_url
        if url == "https://openapi.tripo3d.ai/v3/generation/text-to-model":
            return _FakeHTTPResp(
                json.dumps({"code": 0, "data": {"task_id": "task-fallida"}}).encode()
            )
        if url == "https://openapi.tripo3d.ai/v3/tasks/task-fallida":
            return _FakeHTTPResp(json.dumps({"code": 0, "data": {"status": "failed"}}).encode())
        raise AssertionError(f"URL inesperada en el proveedor tripo3d: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    rc = main(["generar", "guagua-pan", "--proveedor", "tripo3d"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "task-fallida" in err
    assert "failed" in err

    assert not (carpeta / "input" / "guagua-pan_v1_bruto.stl").exists()
    assert "generacion" not in cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]


def test_tripo3d_v3_http_error_durante_sondeo_no_escribe_malla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un `HTTPError` durante el sondeo termina en error con el `task_id`, sin malla escrita."""
    carpeta = _crear_diseno_con_brief(
        tmp_path,
        monkeypatch,
        {"idea.txt": "Vela con forma de guagua de pan"},
    )
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test")
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 60) -> _FakeHTTPResp:
        url = req.full_url
        if url == "https://openapi.tripo3d.ai/v3/generation/text-to-model":
            return _FakeHTTPResp(
                json.dumps({"code": 0, "data": {"task_id": "task-caido"}}).encode()
            )
        if url == "https://openapi.tripo3d.ai/v3/tasks/task-caido":
            raise urllib.error.HTTPError(
                url, 500, "Internal Server Error", {}, io.BytesIO(b"servidor caido")
            )
        raise AssertionError(f"URL inesperada en el proveedor tripo3d: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    rc = main(["generar", "guagua-pan", "--proveedor", "tripo3d"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "task-caido" in err
    assert "500" in err

    assert not (carpeta / "input" / "guagua-pan_v1_bruto.stl").exists()
    assert "generacion" not in cargar_diseno(carpeta)["versiones"]["v1"]["parametros"]
