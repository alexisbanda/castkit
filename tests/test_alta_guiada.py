"""Tests de la entrevista guiada de `castkit alta` (ticket #65).

Cada test de entrada simulada parchea `sys.stdin.isatty` (para que devuelva
``True``) y `builtins.input` con una secuencia fija de respuestas, en el mismo
orden en que la entrevista las pregunta (ver Historia y criterios del
ticket #65). Los helpers de este módulo son locales a este archivo: los de
`tests/test_alta.py` cubren el modo no interactivo (sin cambios) y no se
reutilizan aquí a propósito.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image

from castkit.cli import main
from castkit.control1 import registrar_decision_control1
from castkit.diseno import DatosAlta, crear_diseno
from castkit.generacion import (
    InsumosBrief,
    desregistrar_proveedor,
    registrar_proveedor,
)
from castkit.ingesta import ingerir_malla
from castkit.limpieza import limpiar_malla
from castkit.versiones import cargar_diseno

FIXTURES = Path(__file__).parent / "fixtures"
CUBE_STL = FIXTURES / "cube.stl"

FECHA_ENTREGA_DEFECTO = (date.today() + timedelta(days=21)).isoformat()


def _activar_stdin_interactivo(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StdinInteractivo:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _StdinInteractivo())


def _simular_respuestas(monkeypatch: pytest.MonkeyPatch, respuestas: list[str]) -> list[str]:
    """Parchea `input()` para devolver `respuestas` en orden; registra los prompts."""
    preguntas: list[str] = []
    iterador = iter(respuestas)

    def _input_falso(prompt: str = "") -> str:
        preguntas.append(prompt)
        try:
            return next(iterador)
        except StopIteration:
            raise AssertionError(
                f"Se agotaron las respuestas simuladas; se preguntó: {prompt!r}"
            ) from None

    monkeypatch.setattr("builtins.input", _input_falso)
    return preguntas


@pytest.fixture
def destino(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Carpeta de diseños usada como destino (vía CASTKIT_DISENOS, #83)."""
    carpeta = tmp_path / "disenos"
    carpeta.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta))
    return carpeta


@pytest.fixture
def brief_con_imagen(tmp_path: Path) -> Path:
    """Brief con una única imagen legible, de fondo liso y sin ninguna vista (#93):
    así no dispara ninguna pregunta ni aviso nuevos y los tests que no son de
    imagen/vista siguen viendo la entrevista de antes del ticket #93.
    """
    carpeta = tmp_path / "brief-toro"
    carpeta.mkdir()
    Image.new("RGB", (40, 40), (210, 210, 210)).save(carpeta / "toro-frente-sinfondo.png")
    return carpeta


@pytest.fixture
def brief_sin_imagen(tmp_path: Path) -> Path:
    carpeta = tmp_path / "brief-sin-imagen"
    carpeta.mkdir()
    (carpeta / "notas.txt").write_text("Sin imagen todavía.\n", encoding="utf-8")
    return carpeta


# ---------------------------------------------------------------------------
# Criterio 1: orden de las preguntas (+ explicación y defecto entre corchetes)
# ---------------------------------------------------------------------------


def test_entrevista_pregunta_en_el_orden_del_ticket(
    destino: Path, brief_con_imagen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = [
        "toro",  # nombre
        str(brief_con_imagen),  # brief
        "",  # qué imprime castkit [cubeta]
        "",  # tipo de pieza [media]
        "",  # largo [80]
        "",  # grosor [15]
        "",  # material [silicona]
        "",  # cavidades [1]
        "10",  # cantidad
        "2026-11-15",  # fecha
        "",  # licencia [propio]
        "",  # origen [dibujo del cliente]
        "",  # resumen: Intro = crear
    ]
    preguntas = _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])

    assert codigo == 0
    diseno = destino / "toro"
    assert diseno.is_dir()

    # El orden de las preguntas sigue exactamente el del ticket.
    assert "Nombre" in preguntas[0]
    assert "brief" in preguntas[1].lower()
    assert "imprime" in preguntas[2]
    assert "pieza" in preguntas[3]
    assert "Largo" in preguntas[4] and "[80]" in preguntas[4]
    assert "Grosor" in preguntas[5] and "[15]" in preguntas[5]
    assert "Material" in preguntas[6]
    assert "cavidades" in preguntas[7].lower() and "[1]" in preguntas[7]
    assert "Cantidad" in preguntas[8]
    assert "entrega" in preguntas[9].lower()
    assert "Licencia" in preguntas[10]
    assert "Origen" in preguntas[11]

    contenido = json.loads((diseno / "diseno.json").read_text(encoding="utf-8"))
    v1 = contenido["versiones"]["v1"]
    assert v1["tipo_molde"] == "abierto"
    assert v1["proveedor"] == "profundidad"
    assert v1["modo_matriz"] == "cubeta"
    assert v1["largo_mm"] == 80
    assert v1["grosor_mm"] == 15
    assert contenido["cantidad"] == 10
    assert contenido["fecha_entrega"] == "2026-11-15"


# ---------------------------------------------------------------------------
# Criterio 2: valores por defecto fijos (Intro en todo salvo nombre/brief)
# ---------------------------------------------------------------------------


def test_entrevista_con_intro_en_todo_usa_los_defectos_fijos(
    destino: Path, brief_con_imagen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(brief_con_imagen)] + [""] * 11
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    v1 = contenido["versiones"]["v1"]

    assert v1["modo_matriz"] == "cubeta"
    assert v1["tipo_molde"] == "abierto"
    assert v1["proveedor"] == "profundidad"
    assert v1["largo_mm"] == 80
    assert v1["grosor_mm"] == 15
    assert v1["cavidades"] == 1
    assert contenido["material"] == "silicona"
    assert contenido["cavidades"] == 1
    assert contenido["cantidad"] == 1
    assert contenido["fecha_entrega"] == FECHA_ENTREGA_DEFECTO
    assert contenido["modelo_base"]["licencia"] == "propio"
    assert contenido["modelo_base"]["origen"] == "dibujo del cliente"


# ---------------------------------------------------------------------------
# Criterio 3: medidas con coma decimal y confirmación de medidas raras
# ---------------------------------------------------------------------------


def test_medida_acepta_coma_decimal(
    destino: Path, brief_con_imagen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = [
        "toro",
        str(brief_con_imagen),
        "",  # modo [cubeta]
        "1",  # tipo de pieza: plano (abierto + relieve)
        "92,5",  # largo
        "",  # grosor [15]
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    assert contenido["versiones"]["v1"]["largo_mm"] == 92.5


def test_medida_mayor_a_300mm_pide_confirmacion_y_n_repregunta(
    destino: Path, brief_con_imagen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = [
        "toro",
        str(brief_con_imagen),
        "",  # modo
        "1",  # plano
        "450",  # largo raro
        "n",  # no es correcto -> repregunta
        "80",  # largo definitivo
        "",  # grosor
        "",
        "",
        "",
        "",
        "",
        "",
        "",  # resumen: Intro = crear
    ]
    preguntas = _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    assert any("300 mm" in p for p in preguntas)
    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    assert contenido["versiones"]["v1"]["largo_mm"] == 80


def test_grosor_mayor_o_igual_que_largo_pide_confirmacion(
    destino: Path, brief_con_imagen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = [
        "toro",
        str(brief_con_imagen),
        "",
        "1",  # plano
        "",  # largo [80]
        "80",  # grosor == largo: raro
        "n",
        "20",  # grosor definitivo
        "",
        "",
        "",
        "",
        "",
        "",
        "",  # resumen: Intro = crear
    ]
    preguntas = _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0
    assert any("largo" in p and "mm" in p for p in preguntas if "¿Es correcto?" in p)

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    assert contenido["versiones"]["v1"]["grosor_mm"] == 20


# ---------------------------------------------------------------------------
# Criterio 4: respuesta inválida -> motivo en español, se repite solo esa
# pregunta, sin perder lo anterior.
# ---------------------------------------------------------------------------


def test_respuesta_invalida_muestra_motivo_y_repite_solo_esa_pregunta(
    destino: Path,
    brief_con_imagen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = [
        "toro",
        str(brief_con_imagen),
        "",
        "",
        "",
        "",
        "",
        "",
        "abc",  # cantidad inválida
        "5",  # cantidad válida
        "",
        "",
        "",
        "",
    ]
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "Respuesta inválida" in salida
    assert "entero" in salida

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    assert contenido["cantidad"] == 5
    assert contenido["nombre"] == "toro"  # lo anterior no se perdió


# ---------------------------------------------------------------------------
# Criterio 5: brief validado al responder, cuenta imágenes o avisa si no hay.
# ---------------------------------------------------------------------------


def test_brief_informa_cuantas_imagenes_encontro(
    destino: Path,
    brief_con_imagen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(brief_con_imagen)] + [""] * 11
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "1 imagen" in salida
    assert "toro-frente-sinfondo.png" in salida


def test_brief_sin_imagenes_avisa(
    destino: Path,
    brief_sin_imagen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(brief_sin_imagen)] + [""] * 11
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "ninguna imagen" in salida


def test_brief_inexistente_se_repregunta_sin_perder_el_nombre(
    destino: Path, brief_con_imagen: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    no_existe = str(tmp_path / "no-existe")
    respuestas = ["toro", no_existe, str(brief_con_imagen)] + [""] * 11
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0
    assert (destino / "toro").is_dir()


# ---------------------------------------------------------------------------
# Criterio 6: tipo de pieza -> tipo de molde + proveedor (y aviso #14 en
# figura completa), guardados en la versión.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("respuesta_pieza", "tipo_molde_esperado", "proveedor_esperado"),
    [
        ("1", "abierto", "relieve"),  # plano
        ("2", "abierto", "profundidad"),  # media figura
        ("3", "completo", "imagen-3d"),  # figura completa
    ],
)
def test_tipo_de_pieza_determina_tipo_de_molde_y_proveedor(
    destino: Path,
    brief_con_imagen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    respuesta_pieza: str,
    tipo_molde_esperado: str,
    proveedor_esperado: str,
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(brief_con_imagen), "", respuesta_pieza] + [""] * 9
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    if respuesta_pieza == "3":
        salida = capsys.readouterr().out
        assert "#14" in salida

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    v1 = contenido["versiones"]["v1"]
    assert v1["tipo_molde"] == tipo_molde_esperado
    assert v1["proveedor"] == proveedor_esperado


# ---------------------------------------------------------------------------
# Criterio 7: resumen numerado editable; Intro/«s» crea, «n» no crea (código
# 1), un número repregunta esa línea; mensaje final con el siguiente paso.
# ---------------------------------------------------------------------------


def test_resumen_con_s_crea_el_diseno_y_anuncia_el_siguiente_paso(
    destino: Path,
    brief_con_imagen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(brief_con_imagen)] + [""] * 10 + ["s"]
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "castkit flujo toro" in salida


def test_resumen_con_n_no_crea_nada(
    destino: Path,
    brief_con_imagen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(brief_con_imagen)] + [""] * 10 + ["n"]
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 1
    assert list(destino.iterdir()) == []
    error = capsys.readouterr().err
    assert "No se ha creado nada" in error


def test_resumen_con_numero_de_linea_repregunta_solo_esa(
    destino: Path, brief_con_imagen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    # Primer resumen: se pide cambiar la línea de cantidad (10: con la imagen
    # legible del brief, la línea 4 es «Imagen», ver #93); segundo resumen:
    # Intro para crear.
    respuestas = (
        ["toro", str(brief_con_imagen)]
        + [""] * 10  # modo..origen, todo por defecto
        + ["10", "25"]  # en el resumen: editar la línea de cantidad -> 25
        + [""]  # segundo resumen: Intro = crear
    )
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    assert contenido["cantidad"] == 25
    # El nombre (línea 1) no se volvió a preguntar: sigue siendo "toro".
    assert contenido["nombre"] == "toro"


# ---------------------------------------------------------------------------
# Criterio 9: con opciones en el comando, solo se pregunta lo que falte.
# ---------------------------------------------------------------------------


def test_opciones_en_el_comando_no_se_vuelven_a_preguntar(
    destino: Path, brief_con_imagen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    # --material, --cantidad, --licencia y --origen van en el comando: la
    # entrevista no debe preguntarlos (si lo hiciera, `_simular_respuestas`
    # agotaría la lista y levantaría AssertionError).
    respuestas = [
        "toro",  # nombre
        str(brief_con_imagen),  # brief
        "",  # modo [cubeta]
        "2",  # tipo de pieza: media
        "",  # largo
        "",  # grosor
        "",  # cavidades
        "",  # fecha
        "",  # resumen: Intro = crear
    ]
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(
        [
            "alta",
            "--material",
            "cera",
            "--cantidad",
            "7",
            "--licencia",
            "comercial",
            "--origen",
            "foto del cliente",
        ]
    )
    assert codigo == 0

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    assert contenido["material"] == "cera"
    assert contenido["cantidad"] == 7
    assert contenido["modelo_base"]["licencia"] == "comercial"
    assert contenido["modelo_base"]["origen"] == "foto del cliente"


# ---------------------------------------------------------------------------
# Criterio 8: generar/matriz/flujo usan el proveedor/modo guardado cuando no
# se pasa la opción explícita; una opción explícita manda.
# ---------------------------------------------------------------------------


class _ProveedorFalso:
    """Proveedor enchufable de prueba que copia `cube.stl` sin tocar los insumos."""

    def __init__(self) -> None:
        self.llamadas = 0

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        self.llamadas += 1
        shutil.copyfile(CUBE_STL, destino_stl)


def _crear_diseno_directo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    nombre: str = "guagua-pan",
    proveedor: str | None = None,
    modo_matriz: str | None = None,
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
        alto_mm=40.0,
        cavidades=4,
        cantidad=20,
        fecha=date(2026, 11, 1),
        licencia="propio",
        origen="prueba",
        proveedor=proveedor,
        modo_matriz=modo_matriz,
    )
    return crear_diseno(datos, destino=destino)


def test_generar_sin_proveedor_usa_el_guardado_en_el_diseno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("fake-guardado", fake)
    try:
        carpeta = _crear_diseno_directo(tmp_path, monkeypatch, proveedor="fake-guardado")

        codigo = main(["generar", "guagua-pan"])
        assert codigo == 0
        assert fake.llamadas == 1

        datos = cargar_diseno(carpeta)
        reg = datos["versiones"]["v1"]["parametros"]["generacion"]
        assert reg["proveedor"] == "fake-guardado"
    finally:
        desregistrar_proveedor("fake-guardado")


def test_generar_con_proveedor_explicito_manda_sobre_el_guardado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guardado = _ProveedorFalso()
    explicito = _ProveedorFalso()
    registrar_proveedor("fake-guardado", guardado)
    registrar_proveedor("fake-explicito", explicito)
    try:
        _crear_diseno_directo(tmp_path, monkeypatch, proveedor="fake-guardado")

        codigo = main(["generar", "guagua-pan", "--proveedor", "fake-explicito"])
        assert codigo == 0
        assert explicito.llamadas == 1
        assert guardado.llamadas == 0
    finally:
        desregistrar_proveedor("fake-guardado")
        desregistrar_proveedor("fake-explicito")


def _preparar_matriz_lista(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, modo_matriz: str | None
) -> Path:
    carpeta = _crear_diseno_directo(tmp_path, monkeypatch, modo_matriz=modo_matriz)
    ingerir_malla(carpeta, CUBE_STL)
    limpiar_malla(
        carpeta,
        arriba="z",
        caras_max=200000,
        tolerancia_mm=0.2,
        corte_base_mm=0.5,
        agujero_max_mm=10.0,
    )
    registrar_decision_control1(carpeta, decision="aprobado")
    return carpeta


def test_matriz_sin_modo_usa_el_guardado_sin_preguntar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_matriz_lista(tmp_path, monkeypatch, modo_matriz="figura")

    def _input_que_no_debe_llamarse(prompt: str = "") -> str:
        raise AssertionError(f"No debería preguntarse nada; se preguntó: {prompt!r}")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _input_que_no_debe_llamarse)

    codigo = main(["matriz", "guagua-pan"])
    assert codigo == 0

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["matriz"]
    assert reg["modo"] == "figura"


def test_matriz_con_modo_explicito_manda_sobre_el_guardado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta = _preparar_matriz_lista(tmp_path, monkeypatch, modo_matriz="figura")

    codigo = main(["matriz", "guagua-pan", "--modo", "cubeta"])
    assert codigo == 0

    datos = cargar_diseno(carpeta)
    reg = datos["versiones"]["v1"]["parametros"]["matriz"]
    assert reg["modo"] == "cubeta"


def test_flujo_sin_proveedor_ni_modo_usa_lo_guardado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blender_falso: object
) -> None:
    fake = _ProveedorFalso()
    registrar_proveedor("fake-flujo", fake)
    try:
        carpeta = _crear_diseno_directo(
            tmp_path, monkeypatch, proveedor="fake-flujo", modo_matriz="figura"
        )

        codigo = main(["flujo", "guagua-pan", "--aprobar-control1"])
        assert codigo == 0
        assert fake.llamadas == 1

        datos = cargar_diseno(carpeta)
        reg_matriz = datos["versiones"]["v1"]["parametros"]["matriz"]
        assert reg_matriz["modo"] == "figura"
        reg_gen = datos["versiones"]["v1"]["parametros"]["generacion"]
        assert reg_gen["proveedor"] == "fake-flujo"
    finally:
        desregistrar_proveedor("fake-flujo")


# ---------------------------------------------------------------------------
# Ticket #93: el alta guía el tipo de pieza por el aspecto de la imagen y
# elige imagen y vista.
# ---------------------------------------------------------------------------


class _ProveedorCapturaImagen:
    """Proveedor de prueba que registra qué imagen recibió y copia `cube.stl`."""

    def __init__(self) -> None:
        self.imagen_usada: str | None = None

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        self.imagen_usada = insumos.imagenes[0].name if insumos.imagenes else None
        shutil.copyfile(CUBE_STL, destino_stl)


def _pintar_cuadro(arr, fila0: int, col0: int, fila1: int, col1: int) -> None:
    arr[fila0:fila1, col0:col1] = (0, 0, 0)


def test_imagen_ilegible_avisa_por_nombre_y_sin_legibles_avisa_brief_vacio(
    destino: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    carpeta_brief = tmp_path / "brief-rota"
    carpeta_brief.mkdir()
    (carpeta_brief / "rota.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"no es un png real")

    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(carpeta_brief)] + [""] * 11
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "rota.png" in salida
    assert "no encontré ninguna imagen" in salida


def test_stl_en_brief_avisa_mencionando_ingesta(
    destino: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    carpeta_brief = tmp_path / "brief-stl"
    carpeta_brief.mkdir()
    shutil.copyfile(CUBE_STL, carpeta_brief / "modelo.stl")
    Image.new("RGB", (20, 20), (255, 255, 255)).save(carpeta_brief / "foto.png")

    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(carpeta_brief)] + [""] * 11
    _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "castkit ingesta" in salida


def test_dos_imagenes_legibles_pregunta_cual_y_la_usan_generar(
    destino: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    carpeta_brief = tmp_path / "brief-dos"
    carpeta_brief.mkdir()
    Image.new("RGB", (20, 20), (255, 255, 255)).save(carpeta_brief / "a-imagen.png")
    Image.new("RGB", (20, 20), (255, 255, 255)).save(carpeta_brief / "z-imagen.png")

    fake = _ProveedorCapturaImagen()
    registrar_proveedor("fake-dos-imagenes", fake)
    try:
        _activar_stdin_interactivo(monkeypatch)
        respuestas = [
            "toro",
            str(carpeta_brief),
            "2",  # ¿qué imagen?: 1) a-imagen.png 2) z-imagen.png -> la segunda
            "",  # modo
            "",  # tipo de molde (abierto, por --proveedor explícito)
            "",
            "",  # largo, grosor
            "",
            "",
            "",
            "",
            "",
            "",  # material, cavidades, cantidad, fecha, licencia, origen
            "",  # resumen: crear
        ]
        preguntas = _simular_respuestas(monkeypatch, respuestas)

        codigo = main(["alta", "--proveedor", "fake-dos-imagenes"])
        assert codigo == 0

        # La pregunta de qué imagen usar es la 3ª (tras nombre y brief).
        assert "imagen" in preguntas[2].lower()

        salida = capsys.readouterr().out
        assert "Imagen: z-imagen.png" in salida

        contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
        assert contenido["versiones"]["v1"]["imagen"] == "z-imagen.png"

        codigo = main(["generar", "toro"])
        assert codigo == 0
        # «z-imagen.png» se usó aunque «a-imagen.png» sea la primera por orden
        # alfabético.
        assert fake.imagen_usada == "z-imagen.png"
    finally:
        desregistrar_proveedor("fake-dos-imagenes")


def test_fondo_no_uniforme_avisa_y_sigue_sin_preguntar_vistas(
    destino: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import numpy as np

    carpeta_brief = tmp_path / "brief-degradado"
    carpeta_brief.mkdir()
    ancho = alto = 60
    gradiente = np.linspace(0, 255, ancho, dtype=np.uint8)
    arr = np.zeros((alto, ancho, 3), dtype=np.uint8)
    arr[:, :] = gradiente[np.newaxis, :, np.newaxis]
    Image.fromarray(arr, mode="RGB").save(carpeta_brief / "fondo-malo.png")

    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(carpeta_brief)] + [""] * 11
    preguntas = _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "fondo liso o transparente" in salida
    assert "Vista:" not in salida
    # La siguiente pregunta tras el brief es «qué imprime castkit» (el modo),
    # no una de vistas: no se preguntó nada de vistas.
    assert "imprime" in preguntas[2]

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    assert contenido["versiones"]["v1"]["imagen"] == "fondo-malo.png"


def test_tres_vistas_entrevista_informa_pregunta_recorta_y_genera(
    destino: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import numpy as np

    carpeta_brief = tmp_path / "brief-tres"
    carpeta_brief.mkdir()
    arr = np.full((60, 140, 3), 255, dtype=np.uint8)
    _pintar_cuadro(arr, 10, 10, 20, 20)
    _pintar_cuadro(arr, 10, 55, 20, 65)
    _pintar_cuadro(arr, 10, 100, 20, 110)
    Image.fromarray(arr, mode="RGB").save(carpeta_brief / "figuras.png")

    fake = _ProveedorCapturaImagen()
    registrar_proveedor("fake-tres-vistas", fake)
    try:
        _activar_stdin_interactivo(monkeypatch)
        respuestas = [
            "toro",
            str(carpeta_brief),
            "2",  # ¿cuál vista?: 1) izquierda 2) centro 3) derecha -> centro
            "",  # modo
            "",  # tipo de molde (abierto)
            "",
            "",  # largo, grosor
            "",
            "",
            "",
            "",
            "",
            "",  # material, cavidades, cantidad, fecha, licencia, origen
            "",  # resumen: crear
        ]
        preguntas = _simular_respuestas(monkeypatch, respuestas)

        codigo = main(["alta", "--proveedor", "fake-tres-vistas"])
        assert codigo == 0

        assert "vista" in preguntas[2].lower()

        salida = capsys.readouterr().out
        assert "Encontré 3 vistas" in salida
        assert "Vista: centro" in salida

        ruta_diseno = destino / "toro"
        contenido = json.loads((ruta_diseno / "diseno.json").read_text(encoding="utf-8"))
        imagen_guardada = contenido["versiones"]["v1"]["imagen"]
        assert imagen_guardada == "figuras-vista-centro.png"
        assert (ruta_diseno / "brief" / imagen_guardada).is_file()
        # La imagen original sigue intacta en el brief copiado.
        assert (ruta_diseno / "brief" / "figuras.png").is_file()

        codigo = main(["generar", "toro"])
        assert codigo == 0
        assert fake.imagen_usada == "figuras-vista-centro.png"
    finally:
        desregistrar_proveedor("fake-tres-vistas")


def test_una_sola_vista_no_pregunta_nada_de_vistas(
    destino: Path,
    brief_con_imagen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(brief_con_imagen)] + [""] * 11
    preguntas = _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert "Vista:" not in salida
    # La siguiente pregunta tras el brief es el modo, no una de vistas.
    assert "imprime" in preguntas[2]


def test_editar_linea_del_brief_repite_preguntas_de_imagen_y_vista(
    destino: Path,
    brief_con_imagen: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    carpeta_brief_2 = tmp_path / "brief-dos-v2"
    carpeta_brief_2.mkdir()
    Image.new("RGB", (20, 20), (255, 255, 255)).save(carpeta_brief_2 / "a-imagen.png")
    Image.new("RGB", (20, 20), (255, 255, 255)).save(carpeta_brief_2 / "z-imagen.png")

    _activar_stdin_interactivo(monkeypatch)
    respuestas = (
        ["toro", str(brief_con_imagen)]
        + [""] * 10  # modo..origen, todo por defecto (brief_con_imagen no pregunta nada de imagen)
        + ["3", str(carpeta_brief_2), "2"]  # resumen: editar brief -> 2 imágenes -> elegir la 2ª
        + [""]  # segundo resumen: crear
    )
    preguntas = _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    # Tras reeditar el brief se vuelve a preguntar qué imagen usar.
    assert any("imagen" in p.lower() for p in preguntas[-2:])

    salida = capsys.readouterr().out
    assert "Imagen: z-imagen.png" in salida

    contenido = json.loads((destino / "toro" / "diseno.json").read_text(encoding="utf-8"))
    assert contenido["versiones"]["v1"]["imagen"] == "z-imagen.png"


def test_opciones_tipo_de_pieza_describen_lo_que_se_ve_sin_citar_medalla(
    destino: Path,
    brief_con_imagen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activar_stdin_interactivo(monkeypatch)
    respuestas = ["toro", str(brief_con_imagen)] + [""] * 11
    preguntas = _simular_respuestas(monkeypatch, respuestas)

    codigo = main(["alta"])
    assert codigo == 0

    # preguntas[2] = modo, preguntas[3] = tipo de pieza (sin pregunta de
    # imagen: brief_con_imagen solo tiene 1 imagen legible, sin vistas).
    prompt_tipo_pieza = preguntas[3]
    assert "medalla" not in prompt_tipo_pieza.lower()
    assert "colores planos" in prompt_tipo_pieza
    assert "volumen" in prompt_tipo_pieza
    assert "figura completa" in prompt_tipo_pieza
