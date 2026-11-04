"""Interfaz de línea de comandos de castkit."""

from __future__ import annotations

import argparse
import contextlib
import math
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from castkit.comprobar import comprobar_instalacion, imprimir_estado_blender
from castkit.config import (
    ConfigError,
    cargar_config_generacion,
    cargar_config_limpieza,
    cargar_config_matriz,
    cargar_config_mecha,
)
from castkit.control1 import (
    Control1Error,
    ejecutar_control1,
    registrar_decision_control1,
    verificar_control1_para_matriz,
)
from castkit.diseno import (
    LICENCIAS,
    MATERIALES,
    TIPOS_MOLDE,
    AltaError,
    DatosAlta,
    carpeta_disenos,
    crear_diseno,
    validar_nombre,
)
from castkit.entrevista import (
    formatear_numero,
    preguntar_entero_minimo_uno,
    preguntar_fecha,
    preguntar_medida_mm,
    preguntar_opcion_numerada,
    preguntar_texto,
)
from castkit.generacion import GeneracionError, generar_malla, recolectar_insumos_brief
from castkit.imagen_brief import AnalisisImagen, analizar_imagen
from castkit.ingesta import IngestaError, ingerir_malla, validar_malla
from castkit.limpieza import EJES_ACEPTADOS, LimpiezaError, limpiar_malla, validar_arriba
from castkit.matriz import MODOS_MATRIZ, MatrizError, generar_matriz, validar_modo_matriz
from castkit.mecha import MechaError, ParedFinaMechaError, aplicar_canal_mecha
from castkit.versiones import (
    VersionError,
    cargar_diseno,
    crear_version,
    datos_version,
    nombre_archivo,
    ubicar_diseno,
)

# Orden en el que se piden/validan las opciones del subcomando `alta`, según el
# tipo de molde efectivo: la medida de alto (molde completo) o de largo (molde
# abierto) ocupa el mismo lugar que `alto_mm` ocupaba antes del ticket #55.
_OPCIONES_POR_TIPO_MOLDE = {
    "completo": (
        "brief",
        "material",
        "alto_mm",
        "cavidades",
        "cantidad",
        "fecha",
        "licencia",
        "origen",
    ),
    "abierto": (
        "brief",
        "material",
        "largo_mm",
        "cavidades",
        "cantidad",
        "fecha",
        "licencia",
        "origen",
    ),
}

_NOMBRES_OPCION = {
    "brief": "--brief",
    "material": "--material",
    "alto_mm": "--alto-mm",
    "largo_mm": "--largo-mm",
    "cavidades": "--cavidades",
    "cantidad": "--cantidad",
    "fecha": "--fecha",
    "licencia": "--licencia",
    "origen": "--origen",
}

_PREGUNTAS = {
    "brief": "Carpeta del brief: ",
    "material": f"Material ({', '.join(MATERIALES)}): ",
    "alto_mm": "Alto en mm: ",
    "largo_mm": "Largo en mm (medida mayor de la figura en planta): ",
    "cavidades": "Número de cavidades: ",
    "cantidad": "Cantidad a producir: ",
    "fecha": "Fecha de entrega (AAAA-MM-DD): ",
    "licencia": f"Licencia del modelo base ({', '.join(LICENCIAS)}): ",
    "origen": "Origen del modelo base (de dónde viene el modelo 3D): ",
}


def _configurar_salida_utf8() -> None:
    """Hace que stdout/stderr acepten UTF-8 en consolas que no lo den por defecto."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="castkit")
    subparsers = parser.add_subparsers(dest="comando", required=True)

    alta = subparsers.add_parser("alta", help="Da de alta un diseño nuevo a partir de su brief.")
    alta.add_argument(
        "nombre",
        nargs="?",
        default=None,
        help="Nombre del diseño (minúsculas, números y guiones). Si se omite en una "
        "terminal interactiva, se pregunta en la entrevista guiada.",
    )
    alta.add_argument("--brief", default=None, help="Carpeta con el brief del diseño.")
    alta.add_argument("--material", default=None, help=f"Material: {', '.join(MATERIALES)}.")
    alta.add_argument(
        "--tipo-molde",
        dest="tipo_molde",
        default=None,
        help=f"Tipo de molde: {', '.join(TIPOS_MOLDE)} (por defecto «abierto» si no se indica "
        "ninguna medida).",
    )
    alta.add_argument(
        "--alto-mm",
        dest="alto_mm",
        default=None,
        help="Alto en milímetros (molde completo).",
    )
    alta.add_argument(
        "--largo-mm",
        dest="largo_mm",
        default=None,
        help="Largo en milímetros, la medida mayor de la figura en planta (molde abierto).",
    )
    alta.add_argument(
        "--grosor-mm",
        dest="grosor_mm",
        default=None,
        help="Grosor en milímetros que sobresale de la placa (molde abierto, opcional).",
    )
    alta.add_argument("--cavidades", default=None, help="Número de cavidades del molde.")
    alta.add_argument("--cantidad", default=None, help="Cantidad de piezas a producir.")
    alta.add_argument("--fecha", default=None, help="Fecha de entrega, en formato AAAA-MM-DD.")
    alta.add_argument(
        "--licencia", default=None, help=f"Licencia del modelo base: {', '.join(LICENCIAS)}."
    )
    alta.add_argument("--origen", default=None, help="Origen del modelo base (texto libre).")
    alta.add_argument(
        "--modo",
        default=None,
        help=f"Modo de matriz a recordar en la versión: {', '.join(MODOS_MATRIZ)} (opcional; "
        "si se omite, `generar`/`matriz`/`flujo` seguirán preguntándolo o usando "
        "config/fabricacion.toml, como hoy).",
    )
    alta.add_argument(
        "--proveedor",
        default=None,
        help="Proveedor de generación 3D a recordar en la versión (opcional; si se omite, "
        "`generar`/`flujo` usarán config/fabricacion.toml, como hoy).",
    )

    version = subparsers.add_parser(
        "version", help="Crea la siguiente versión de un diseño existente."
    )
    version.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    version.add_argument("--motivo", default=None, help="Motivo de la nueva versión (qué cambió).")
    version.add_argument(
        "--tipo-molde",
        dest="tipo_molde",
        default=None,
        help=f"Nuevo tipo de molde (opcional): {', '.join(TIPOS_MOLDE)}.",
    )
    version.add_argument(
        "--alto-mm",
        dest="alto_mm",
        default=None,
        help="Nuevo alto en milímetros (opcional, molde completo).",
    )
    version.add_argument(
        "--largo-mm",
        dest="largo_mm",
        default=None,
        help="Nuevo largo en milímetros (opcional, molde abierto).",
    )
    version.add_argument(
        "--grosor-mm",
        dest="grosor_mm",
        default=None,
        help="Nuevo grosor en milímetros (opcional, molde abierto).",
    )
    version.add_argument(
        "--cavidades", default=None, help="Nuevo número de cavidades del molde (opcional)."
    )

    ingesta = subparsers.add_parser(
        "ingesta", help="Incorpora la malla en bruto de la versión actual de un diseño."
    )
    ingesta.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    ingesta.add_argument("archivo", help="Ruta al archivo STL con la malla en bruto.")
    ingesta.add_argument(
        "--reemplazar",
        action="store_true",
        help="Reemplaza sin preguntar la malla ya guardada en la versión actual, si existe.",
    )

    generar = subparsers.add_parser(
        "generar",
        help="Genera u obtiene la malla en bruto desde los insumos del brief "
        "con un proveedor enchufable.",
    )
    generar.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    generar.add_argument(
        "--proveedor",
        default=None,
        help="Proveedor enchufable de generación 3D (si se omite, se usa config/fabricacion.toml).",
    )
    generar.add_argument(
        "--prompt",
        default=None,
        help="Texto o prompt de contexto adicional para la generación 3D.",
    )
    generar.add_argument(
        "--reemplazar",
        action="store_true",
        help="Reemplaza sin preguntar la malla ya guardada en la versión actual, si existe.",
    )

    limpieza = subparsers.add_parser(
        "limpieza", help="Limpia y escala automáticamente la malla en bruto de un diseño."
    )
    limpieza.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    limpieza.add_argument(
        "--arriba",
        default=None,
        help=f"Eje que debe quedar hacia +Z antes de escalar: {', '.join(EJES_ACEPTADOS)} "
        '(si se omite: "z" en molde completo, o el eje de menor extensión en molde abierto).',
    )
    limpieza.add_argument(
        "--caras-max",
        dest="caras_max",
        default=None,
        help="Caras máximas de la malla limpia (si se omite, se usa config/fabricacion.toml).",
    )
    limpieza.add_argument(
        "--tolerancia-mm",
        dest="tolerancia_mm",
        default=None,
        help="Distancia máxima, en mm, permitida frente a la malla original "
        "(si se omite, se usa config/fabricacion.toml).",
    )
    limpieza.add_argument(
        "--corte-base-mm",
        dest="corte_base_mm",
        default=None,
        help="Milímetros a recortar desde la base (si se omite, se usa config/fabricacion.toml).",
    )
    limpieza.add_argument(
        "--agujero-max-mm",
        dest="agujero_max_mm",
        default=None,
        help="Perímetro máximo, en mm, de una zona abierta para cerrarla automáticamente "
        "(si se omite, se usa config/fabricacion.toml).",
    )
    limpieza.add_argument(
        "--voxel-mm",
        dest="voxel_mm",
        default=None,
        help="Tamaño de vóxel, en mm, para el remallado de respaldo con Blender.",
    )
    limpieza.add_argument(
        "--sin-blender",
        dest="sin_blender",
        action="store_true",
        help="Desactiva el respaldo con remallado de Blender si Python no cierra la malla.",
    )

    mecha = subparsers.add_parser(
        "mecha",
        help="Añade el canal vertical de mecha cuando el material del diseño es cera.",
    )
    mecha.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    mecha.add_argument(
        "--diametro-mm",
        dest="diametro_mm",
        default=None,
        help="Diámetro del canal de mecha en mm (por defecto en config/fabricacion.toml).",
    )
    mecha.add_argument(
        "--espesor-minimo-mm",
        dest="espesor_minimo_mm",
        default=None,
        help="Espesor mínimo de pared lateral en mm alrededor del canal.",
    )
    mecha.add_argument(
        "--forzar",
        action="store_true",
        help="Continúa sin preguntar aunque el espesor de pared quede bajo el mínimo.",
    )

    control1 = subparsers.add_parser(
        "control1",
        help="Genera las 4 vistas PNG del punto de control 1 con medidas y registra su decisión.",
    )
    control1.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    control1.add_argument(
        "--aprobar",
        action="store_true",
        help="Aprueba la figura en el punto de control 1 para habilitar el paso a la matriz.",
    )
    control1.add_argument(
        "--rechazar",
        action="store_true",
        help="Rechaza la figura en el punto de control 1 (requiere --motivo).",
    )
    control1.add_argument(
        "--motivo",
        default=None,
        help="Motivo del rechazo en el punto de control 1 (qué corregir).",
    )

    matriz = subparsers.add_parser(
        "matriz",
        help="Genera el STL de la matriz imprimible (modo figura o cubeta multicavidad).",
    )
    matriz.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    matriz.add_argument(
        "--modo",
        default=None,
        help=f"Modo de matriz a generar: {', '.join(MODOS_MATRIZ)} "
        "(si se omite en terminal interactiva, se pregunta antes de crear).",
    )
    matriz.add_argument(
        "--espesor-base-mm",
        dest="espesor_base_mm",
        default=None,
        help="Espesor de la placa base en mm (si se omite, se usa config/fabricacion.toml).",
    )
    matriz.add_argument(
        "--margen-silicona-mm",
        dest="margen_silicona_mm",
        default=None,
        help="Margen libre de silicona en mm entre figuras y pared (modo cubeta).",
    )
    matriz.add_argument(
        "--espesor-pared-mm",
        dest="espesor_pared_mm",
        default=None,
        help="Grosor de la pared perimetral en mm (modo cubeta).",
    )
    matriz.add_argument(
        "--margen-superior-mm",
        dest="margen_superior_mm",
        default=None,
        help="Margen superior en mm por encima de la figura (modo cubeta).",
    )
    matriz.add_argument(
        "--separacion-cavidades-mm",
        dest="separacion_cavidades_mm",
        default=None,
        help="Separación mínima en mm entre figuras vecinas (modo cubeta).",
    )

    flujo = subparsers.add_parser(
        "flujo",
        help="Ejecuta en cadena generar -> limpieza -> mecha (si es cera) -> control1 -> matriz.",
    )
    flujo.add_argument("nombre", help="Nombre del diseño ya dado de alta.")
    flujo.add_argument(
        "--modo",
        default=None,
        help=f"Modo de matriz ({', '.join(MODOS_MATRIZ)}).",
    )
    flujo.add_argument("--proveedor", default=None, help="Proveedor enchufable de generación 3D.")
    flujo.add_argument("--prompt", default=None, help="Prompt adicional para la generación 3D.")
    flujo.add_argument(
        "--reemplazar",
        action="store_true",
        help="Reemplaza la malla en bruto si ya existía.",
    )
    flujo.add_argument(
        "--aprobar-control1",
        dest="aprobar_control1",
        action="store_true",
        help="Aprueba el punto de control 1 sin preguntar (para scripts o CI).",
    )

    comprobar = subparsers.add_parser(
        "comprobar",
        help="Comprueba que la instalación de castkit esté lista (Blender, configuración, etc.).",
    )
    comprobar.add_argument(
        "--estado-blender",
        dest="estado_blender",
        action="store_true",
        help="Para que lo use install.ps1, no pensada para personas: en vez de la comprobación "
        "completa, imprime solo «estado=ok|falta|otra» y «version=<versión fijada>», y "
        "siempre termina con código 0.",
    )

    return parser


def _parsear_fecha(valor: str) -> date:
    try:
        return date.fromisoformat(valor)
    except ValueError as exc:
        raise AltaError(
            f'--fecha debe ser una fecha AAAA-MM-DD válida; recibido "{valor}".'
        ) from exc


def _parsear_numero_positivo(valor: str, opcion: str) -> float:
    try:
        numero = float(valor)
    except ValueError as exc:
        raise AltaError(f'{opcion} debe ser un número mayor que 0; recibido "{valor}".') from exc
    if not math.isfinite(numero) or numero <= 0:
        raise AltaError(f'{opcion} debe ser un número mayor que 0; recibido "{valor}".')
    return numero


def _parsear_entero_minimo_uno(valor: str, opcion: str) -> int:
    try:
        numero = int(valor)
    except ValueError as exc:
        raise AltaError(
            f'{opcion} debe ser un entero mayor o igual a 1; recibido "{valor}".'
        ) from exc
    if numero < 1:
        raise AltaError(f'{opcion} debe ser un entero mayor o igual a 1; recibido "{valor}".')
    return numero


def _validar_eleccion(valor: str, opcion: str, aceptados: tuple[str, ...]) -> str:
    if valor not in aceptados:
        raise AltaError(
            f"{opcion} debe ser uno de los siguientes valores: {', '.join(aceptados)}; "
            f'recibido "{valor}".'
        )
    return valor


def _pedir_opciones_faltantes(
    valores: dict[str, str | None], opciones: tuple[str, ...]
) -> list[str]:
    """Pregunta por cada opción vacía en ``valores`` y la actualiza en el sitio.

    Cada respuesta se limpia con ``.strip()``. Devuelve la lista de opciones
    que, tras preguntar, sigan vacías.
    """
    for clave in opciones:
        if not valores[clave]:
            valores[clave] = input(_PREGUNTAS[clave]).strip()
    return [clave for clave in opciones if not valores[clave]]


def _limpiar_ruta(valor: str) -> str:
    """Normaliza una ruta (de brief o de archivo) recibida por opción o interactivamente.

    Quita las comillas simples o dobles que la envuelvan (arrastrar un
    archivo o una carpeta a la terminal suele añadirlas) y expande ``~`` a la
    carpeta personal antes de validar la ruta.
    """
    valor = valor.strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
        valor = valor[1:-1].strip()
    return str(Path(valor).expanduser())


def _determinar_tipo_molde(
    *,
    tipo_molde_arg: str | None,
    alto_mm_arg: str | None,
    largo_mm_arg: str | None,
    grosor_mm_arg: str | None,
) -> str | None:
    """Deduce el tipo de molde efectivo a partir de las opciones recibidas.

    Si se pasó ``--tipo-molde`` explícito, se usa ese (sin validar aquí: lo
    valida quien llame). Si no, se deduce de qué medida se dio: `--largo-mm`
    o `--grosor-mm` implican `"abierto"`; `--alto-mm` implica `"completo"`.
    Si no se dio ninguna opción de tipo o medida, devuelve ``None`` (tipo
    indeterminado: quien llame decide si pregunta, usa un valor por defecto o
    hereda de una versión anterior).
    """
    if tipo_molde_arg:
        return tipo_molde_arg.strip().lower()
    if largo_mm_arg is not None or grosor_mm_arg is not None:
        return "abierto"
    if alto_mm_arg is not None:
        return "completo"
    return None


def _validar_combinacion_tipo_molde(
    tipo_molde: str | None,
    *,
    alto_mm_arg: str | None,
    largo_mm_arg: str | None,
    grosor_mm_arg: str | None,
) -> None:
    """Lanza :class:`AltaError` si las medidas recibidas no corresponden a ``tipo_molde``."""
    if tipo_molde == "completo" and (largo_mm_arg is not None or grosor_mm_arg is not None):
        raise AltaError(
            "--largo-mm y --grosor-mm son medidas de --tipo-molde abierto; con --tipo-molde "
            "completo solo se usa --alto-mm."
        )
    if tipo_molde == "abierto" and alto_mm_arg is not None:
        raise AltaError(
            "--alto-mm es la medida de --tipo-molde completo; con --tipo-molde abierto se "
            "usan --largo-mm (obligatorio) y --grosor-mm (opcional)."
        )


def _ejecutar_alta(args: argparse.Namespace) -> int:
    # Sin nombre: en terminal interactiva se hace la entrevista guiada
    # completa (ticket #65), que pregunta también el nombre; sin terminal,
    # `alta` sigue exigiéndolo como antes (ahora con un mensaje explícito, en
    # vez del error de argparse de cuando era un posicional obligatorio).
    if args.nombre is None:
        if sys.stdin.isatty():
            return _ejecutar_alta_guiada(args)
        print(
            "Error: falta el nombre del diseño; pásalo como primer argumento "
            "(en una terminal interactiva puedes omitirlo para la entrevista guiada).",
            file=sys.stderr,
        )
        return 2

    # Se valida el nombre, la carpeta de diseños y que el diseño no exista ya
    # ANTES de detectar opciones faltantes: si algo de esto falla, no se
    # pregunta nada.
    try:
        validar_nombre(args.nombre)
        destino = carpeta_disenos()
        if (destino / args.nombre).exists():
            raise AltaError(
                f'El diseño "{args.nombre}" ya existe en {destino}; no se ha modificado nada.'
            )
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    tipo_molde = _determinar_tipo_molde(
        tipo_molde_arg=args.tipo_molde,
        alto_mm_arg=args.alto_mm,
        largo_mm_arg=args.largo_mm,
        grosor_mm_arg=args.grosor_mm,
    )

    if tipo_molde is None:
        # Ni --tipo-molde ni ninguna medida: en terminal interactiva se
        # pregunta el tipo (proponiendo "abierto"); sin terminal, se asume
        # "abierto" y la medida que falte (--largo-mm) aparecerá más abajo en
        # la lista de opciones obligatorias faltantes, igual que hoy.
        if sys.stdin.isatty():
            respuesta = (
                input(f"Tipo de molde ({', '.join(TIPOS_MOLDE)}) [abierto]: ").strip().lower()
            )
            tipo_molde = respuesta or "abierto"
        else:
            tipo_molde = "abierto"

    try:
        tipo_molde = _validar_eleccion(tipo_molde, "--tipo-molde", TIPOS_MOLDE)
        _validar_combinacion_tipo_molde(
            tipo_molde,
            alto_mm_arg=args.alto_mm,
            largo_mm_arg=args.largo_mm,
            grosor_mm_arg=args.grosor_mm,
        )
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    opciones_requeridas = _OPCIONES_POR_TIPO_MOLDE[tipo_molde]
    clave_medida = "alto_mm" if tipo_molde == "completo" else "largo_mm"
    valor_medida = args.alto_mm if tipo_molde == "completo" else args.largo_mm

    valores: dict[str, str | None] = {
        "brief": args.brief,
        "material": args.material,
        clave_medida: valor_medida,
        "cavidades": args.cavidades,
        "cantidad": args.cantidad,
        "fecha": args.fecha,
        "licencia": args.licencia,
        "origen": args.origen,
    }

    faltantes = [clave for clave in opciones_requeridas if not valores[clave]]
    if faltantes:
        if sys.stdin.isatty():
            faltantes = _pedir_opciones_faltantes(valores, opciones_requeridas)
        if faltantes:
            nombres = ", ".join(_NOMBRES_OPCION[clave] for clave in faltantes)
            print(
                f"Error: faltan las siguientes opciones obligatorias: {nombres}.",
                file=sys.stderr,
            )
            return 2

    try:
        material = _validar_eleccion(valores["material"], "--material", MATERIALES)
        licencia = _validar_eleccion(valores["licencia"], "--licencia", LICENCIAS)
        cavidades = _parsear_entero_minimo_uno(valores["cavidades"], "--cavidades")
        cantidad = _parsear_entero_minimo_uno(valores["cantidad"], "--cantidad")
        fecha = _parsear_fecha(valores["fecha"])
        brief = _limpiar_ruta(valores["brief"])
        modo_matriz = validar_modo_matriz(args.modo.strip().lower()) if args.modo else None
        proveedor = args.proveedor.strip() if args.proveedor else None

        if tipo_molde == "completo":
            alto_mm = _parsear_numero_positivo(valores["alto_mm"], "--alto-mm")
            largo_mm = None
            grosor_mm = None
        else:
            largo_mm = _parsear_numero_positivo(valores["largo_mm"], "--largo-mm")
            grosor_mm = (
                _parsear_numero_positivo(args.grosor_mm, "--grosor-mm")
                if args.grosor_mm is not None
                else None
            )
            alto_mm = None

        datos = DatosAlta(
            nombre=args.nombre,
            brief=Path(brief),
            material=material,
            alto_mm=alto_mm,
            cavidades=cavidades,
            cantidad=cantidad,
            fecha=fecha,
            licencia=licencia,
            origen=valores["origen"],
            tipo_molde=tipo_molde,
            largo_mm=largo_mm,
            grosor_mm=grosor_mm,
            proveedor=proveedor,
            modo_matriz=modo_matriz,
        )
        ruta = crear_diseno(datos, destino=destino)
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Diseño «{datos.nombre}» creado en {ruta}")
    return 0


# Tipo de pieza -> (tipo_molde, proveedor) de la entrevista guiada (ticket #65).
_PIEZA_A_MOLDE_PROVEEDOR = {
    "plano": ("abierto", "relieve"),
    "media": ("abierto", "profundidad"),
    "completa": ("completo", "imagen-3d"),
}


def _ejecutar_alta_guiada(args: argparse.Namespace) -> int:
    """Entrevista guiada de `castkit alta` (ticket #65), paso a paso y editable.

    Pregunta, en orden, lo que no venga ya en `args` (nombre, carpeta del
    brief, qué imprime castkit, tipo de pieza, medidas, material, cavidades,
    cantidad, fecha, licencia y origen), cada pregunta con una explicación y
    un valor propuesto entre corchetes que se acepta con Intro. Al final
    muestra un resumen numerado: Intro o «s» crea el diseño, «n» no crea nada
    (código 1) y el número de una línea la vuelve a preguntar (y, si cambia el
    tipo de pieza o el modo, lo que depende de ella).
    """
    try:
        destino = carpeta_disenos()
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        _area_min_pct_vistas = float(cargar_config_generacion()["vista_area_min_pct"])
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    def _validar_nombre_disponible(valor: str) -> None:
        try:
            validar_nombre(valor)
        except AltaError as exc:
            raise ValueError(str(exc)) from exc
        if (destino / valor).exists():
            raise ValueError(f'el diseño "{valor}" ya existe en {destino}; elige otro nombre.')

    def _preguntar_nombre() -> str:
        return preguntar_texto(
            "Nombre del diseño",
            "Minúsculas, números y guiones, sin tildes ni espacios (p. ej. «toro»).",
            None,
            validar=_validar_nombre_disponible,
        )

    def _validar_brief_existe(valor: str) -> None:
        ruta = Path(_limpiar_ruta(valor))
        if not ruta.is_dir():
            raise ValueError(f'"{valor}" no existe o no es una carpeta.')

    def _informar_insumos_brief(ruta: Path) -> None:
        insumos = recolectar_insumos_brief(ruta)
        if insumos.imagenes:
            nombres_imgs = ", ".join(p.name for p in insumos.imagenes)
            n = len(insumos.imagenes)
            palabra = "imagen" if n == 1 else "imágenes"
            print(f"Encontré {n} {palabra}: {nombres_imgs}")
        else:
            print(
                "Aviso: no encontré ninguna imagen en el brief; los proveedores locales "
                "(relieve, profundidad) necesitan una."
            )

    def _elegir_imagen_y_vista(ruta: Path) -> tuple[str | None, str | None]:
        """Avisa de insumos problemáticos del brief y, si aplica, pregunta qué imagen
        y qué vista usar (ticket #93). Devuelve ``(imagen, vista)`` (nombres de
        archivo dentro del brief; ambos ``None`` si no hay ninguna imagen legible).
        """
        insumos = recolectar_insumos_brief(ruta)
        for stl in insumos.mallas_stl:
            print(
                f'Aviso: encontré "{stl.name}" (un .stl) en el brief; si ya tienes la malla 3D '
                "hecha, usa «castkit ingesta» en vez de generarla de nuevo."
            )

        legibles: list[tuple[Path, AnalisisImagen]] = []
        for ruta_img in insumos.imagenes:
            analisis = analizar_imagen(ruta_img, area_min_pct=_area_min_pct_vistas)
            if analisis.legible:
                legibles.append((ruta_img, analisis))
            else:
                print(
                    f'Aviso: no se pudo abrir "{ruta_img.name}" como imagen; no se '
                    "ofrecerá para elegir."
                )

        if not legibles:
            print(
                "Aviso: no encontré ninguna imagen en el brief; los proveedores locales "
                "(relieve, profundidad) necesitan una."
            )
            return None, None

        if len(legibles) >= 2:
            opciones_imagen = tuple((ruta_img.name, ruta_img.name) for ruta_img, _ in legibles)
            nombre_elegido = preguntar_opcion_numerada(
                "¿Qué imagen quieres usar?", None, opciones_imagen, legibles[0][0].name
            )
            ruta_elegida, analisis = next((r, a) for r, a in legibles if r.name == nombre_elegido)
        else:
            ruta_elegida, analisis = legibles[0]

        if not analisis.fondo_uniforme:
            print(
                f'Aviso: el fondo de "{ruta_elegida.name}" no es liso ni transparente; pide al '
                "cliente una versión con fondo liso o transparente antes de generar la malla "
                "(por ahora puedes seguir con la entrevista)."
            )
            return ruta_elegida.name, None

        if len(analisis.vistas) >= 2:
            n_vistas = len(analisis.vistas)
            print(f'Encontré {n_vistas} vistas en "{ruta_elegida.name}".')
            opciones_vista = tuple((v.nombre, v.nombre) for v in analisis.vistas)
            vista_elegida = preguntar_opcion_numerada(
                "¿Cuál vista quieres modelar?",
                None,
                opciones_vista,
                analisis.vistas[0].nombre,
            )
            return ruta_elegida.name, vista_elegida

        return ruta_elegida.name, None

    def _preguntar_brief() -> tuple[Path, str | None, str | None]:
        texto = preguntar_texto(
            "Carpeta del brief",
            "Carpeta con las imágenes, documentos o texto del brief del cliente.",
            None,
            validar=_validar_brief_existe,
        )
        ruta = Path(_limpiar_ruta(texto))
        _informar_insumos_brief(ruta)
        imagen, vista = _elegir_imagen_y_vista(ruta)
        return ruta, imagen, vista

    _OPCIONES_MODO = (
        ("cubeta", "el molde listo para verter silicona (cubeta)"),
        ("figura", "solo la figura, para hacer el molde aparte"),
    )

    def _preguntar_modo() -> str:
        return preguntar_opcion_numerada("¿Qué imprime castkit?", None, _OPCIONES_MODO, "cubeta")

    _OPCIONES_PIEZA = (
        ("plano", "colores planos, sin sombras (como un logo o un escudo)"),
        ("media", "con volumen, sombras, es una foto o un render"),
        ("completa", "la figura completa"),
    )

    def _preguntar_tipo_pieza() -> tuple[str, str]:
        pieza = preguntar_opcion_numerada(
            "¿Qué tipo de pieza es?",
            "Elige según lo que se ve en la imagen (castkit no lo detecta solo). Para "
            "piezas curvas, como una taza, todavía no hay proveedor "
            "(https://github.com/alexisbanda/castkit/issues/44).",
            _OPCIONES_PIEZA,
            "media",
        )
        tipo_molde_nuevo, proveedor_nuevo = _PIEZA_A_MOLDE_PROVEEDOR[pieza]
        if pieza == "completa":
            print(
                "Aviso: el molde de dos partes todavía no existe (#14); por ahora la figura "
                "completa se genera como una sola pieza."
            )
        return tipo_molde_nuevo, proveedor_nuevo

    def _preguntar_alto() -> float:
        return preguntar_medida_mm("Alto en mm", "La altura total de la figura, de pie.", 100.0)

    def _preguntar_largo() -> float:
        return preguntar_medida_mm(
            "Largo en mm", "La medida mayor de la figura vista de frente.", 80.0
        )

    def _preguntar_grosor(largo_actual: float) -> float:
        return preguntar_medida_mm(
            "Grosor en mm",
            "Lo que sobresale de la placa.",
            15.0,
            comparar_mayor_o_igual=("largo", largo_actual),
        )

    _OPCIONES_MATERIAL = tuple((m, m) for m in MATERIALES)

    def _preguntar_material() -> str:
        return preguntar_opcion_numerada(
            "Material de la pieza final",
            "El molde que se vierte en la cubeta es siempre de silicona; esto es el "
            "material de la pieza final, una vez desmoldada.",
            _OPCIONES_MATERIAL,
            "silicona",
        )

    def _preguntar_cavidades(defecto: int) -> int:
        return preguntar_entero_minimo_uno(
            "Número de cavidades", "Cuántas figuras caben en una sola cubeta.", defecto
        )

    def _preguntar_cantidad() -> int:
        return preguntar_entero_minimo_uno(
            "Cantidad a producir", "Cuántas piezas finales se van a producir en total.", 1
        )

    def _preguntar_fecha_entrega() -> date:
        return preguntar_fecha(
            "Fecha de entrega",
            "Cuándo tiene que estar lista la pieza.",
            date.today() + timedelta(days=21),
        )

    def _validar_licencia(valor: str) -> None:
        try:
            _validar_eleccion(valor, "--licencia", LICENCIAS)
        except AltaError as exc:
            raise ValueError(str(exc)) from exc

    def _preguntar_licencia() -> str:
        return preguntar_texto(
            "Licencia del modelo base",
            f"Opciones: {', '.join(LICENCIAS)}.",
            "propio",
            validar=_validar_licencia,
        )

    def _validar_origen(valor: str) -> None:
        if not valor.strip():
            raise ValueError("no puede estar vacío: describe de dónde viene el modelo base.")

    def _preguntar_origen() -> str:
        return preguntar_texto(
            "Origen del modelo base",
            "De dónde viene el modelo 3D (p. ej. «dibujo del cliente», «escultura propia»).",
            "dibujo del cliente",
            validar=_validar_origen,
        )

    # --- 1. Nombre --------------------------------------------------------
    if args.nombre is not None:
        try:
            _validar_nombre_disponible(args.nombre)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        nombre = args.nombre
    else:
        nombre = _preguntar_nombre()

    # --- 2. Brief -----------------------------------------------------------
    if args.brief is not None:
        try:
            _validar_brief_existe(args.brief)
        except ValueError as exc:
            print(f"Error: --brief {exc}", file=sys.stderr)
            return 1
        brief = Path(_limpiar_ruta(args.brief))
        _informar_insumos_brief(brief)
        imagen, vista = _elegir_imagen_y_vista(brief)
    else:
        brief, imagen, vista = _preguntar_brief()

    # --- 3. Qué imprime castkit (modo de matriz) ---------------------------
    if args.modo:
        try:
            modo = validar_modo_matriz(args.modo.strip().lower())
        except MatrizError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        modo = _preguntar_modo()

    # --- 4. Tipo de pieza -> tipo de molde + proveedor ----------------------
    tipo_molde_detectado = _determinar_tipo_molde(
        tipo_molde_arg=args.tipo_molde,
        alto_mm_arg=args.alto_mm,
        largo_mm_arg=args.largo_mm,
        grosor_mm_arg=args.grosor_mm,
    )
    proveedor_arg = args.proveedor.strip() if args.proveedor else None

    try:
        if proveedor_arg is not None:
            proveedor = proveedor_arg
            if tipo_molde_detectado is not None:
                tipo_molde = _validar_eleccion(tipo_molde_detectado, "--tipo-molde", TIPOS_MOLDE)
            else:
                tipo_molde = preguntar_opcion_numerada(
                    "Tipo de molde", None, tuple((t, t) for t in TIPOS_MOLDE), "abierto"
                )
        elif tipo_molde_detectado is not None:
            tipo_molde = _validar_eleccion(tipo_molde_detectado, "--tipo-molde", TIPOS_MOLDE)
            proveedor = None
        else:
            tipo_molde, proveedor = _preguntar_tipo_pieza()
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # --- 5. Medidas del tipo de molde efectivo ------------------------------
    alto_mm = largo_mm = grosor_mm = None
    try:
        if tipo_molde == "completo":
            alto_mm = (
                _parsear_numero_positivo(args.alto_mm, "--alto-mm")
                if args.alto_mm is not None
                else _preguntar_alto()
            )
        else:
            largo_mm = (
                _parsear_numero_positivo(args.largo_mm, "--largo-mm")
                if args.largo_mm is not None
                else _preguntar_largo()
            )
            grosor_mm = (
                _parsear_numero_positivo(args.grosor_mm, "--grosor-mm")
                if args.grosor_mm is not None
                else _preguntar_grosor(largo_mm)
            )
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # --- 6. Material de la pieza final -------------------------------------
    try:
        material = (
            _validar_eleccion(args.material.strip().lower(), "--material", MATERIALES)
            if args.material is not None
            else _preguntar_material()
        )
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # --- 7. Cavidades (solo si cubeta; en figura se registra 1) ------------
    if modo == "figura":
        cavidades = 1
    else:
        try:
            cavidades = (
                _parsear_entero_minimo_uno(args.cavidades, "--cavidades")
                if args.cavidades is not None
                else _preguntar_cavidades(1)
            )
        except AltaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    # --- 8. Cantidad ---------------------------------------------------------
    try:
        cantidad = (
            _parsear_entero_minimo_uno(args.cantidad, "--cantidad")
            if args.cantidad is not None
            else _preguntar_cantidad()
        )
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # --- 9. Fecha de entrega -------------------------------------------------
    try:
        fecha = _parsear_fecha(args.fecha) if args.fecha is not None else _preguntar_fecha_entrega()
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # --- 10. Licencia ---------------------------------------------------------
    try:
        licencia = (
            _validar_eleccion(args.licencia.strip().lower(), "--licencia", LICENCIAS)
            if args.licencia is not None
            else _preguntar_licencia()
        )
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # --- 11. Origen ------------------------------------------------------------
    origen = args.origen if args.origen is not None else _preguntar_origen()

    # --- Resumen numerado, editable antes de crear el diseño ----------------
    # Se construye como una lista de (texto, función que repregunta esa línea) en
    # vez de números fijos: las líneas de «Imagen» y «Vista» solo aparecen cuando
    # aplican (#93) y desplazan a las demás, así que el número de cada línea se
    # recalcula en cada vuelta del bucle en vez de quedar fijo en el código.
    def _no_editable(mensaje: str) -> Callable[[], None]:
        def _editar() -> None:
            print(mensaje)

        return _editar

    def _editar_nombre() -> None:
        nonlocal nombre
        nombre = _preguntar_nombre()

    def _editar_brief() -> None:
        nonlocal brief, imagen, vista
        brief, imagen, vista = _preguntar_brief()

    def _editar_imagen_y_vista() -> None:
        nonlocal imagen, vista
        imagen, vista = _elegir_imagen_y_vista(brief)

    def _editar_modo() -> None:
        nonlocal modo, cavidades
        modo = _preguntar_modo()
        if modo == "figura":
            cavidades = 1
        else:
            cavidades = _preguntar_cavidades(cavidades if cavidades else 1)

    def _editar_tipo_pieza() -> None:
        nonlocal tipo_molde, proveedor, alto_mm, largo_mm, grosor_mm
        tipo_molde, proveedor = _preguntar_tipo_pieza()
        if tipo_molde == "completo":
            alto_mm = _preguntar_alto()
            largo_mm = grosor_mm = None
        else:
            largo_mm = _preguntar_largo()
            grosor_mm = _preguntar_grosor(largo_mm)
            alto_mm = None

    def _editar_medidas() -> None:
        nonlocal alto_mm, largo_mm, grosor_mm
        if tipo_molde == "completo":
            alto_mm = _preguntar_alto()
        else:
            largo_mm = _preguntar_largo()
            grosor_mm = _preguntar_grosor(largo_mm)

    def _editar_material() -> None:
        nonlocal material
        material = _preguntar_material()

    def _editar_cavidades() -> None:
        nonlocal cavidades
        if modo == "figura":
            print(
                "Las cavidades están fijas en 1 porque el modo es «figura»; cambia la "
                "línea «Qué imprime castkit» si necesitas una cubeta con varias."
            )
        else:
            cavidades = _preguntar_cavidades(cavidades)

    def _editar_cantidad() -> None:
        nonlocal cantidad
        cantidad = _preguntar_cantidad()

    def _editar_fecha() -> None:
        nonlocal fecha
        fecha = _preguntar_fecha_entrega()

    def _editar_licencia() -> None:
        nonlocal licencia
        licencia = _preguntar_licencia()

    def _editar_origen() -> None:
        nonlocal origen
        origen = _preguntar_origen()

    def _construir_campos() -> list[tuple[str, Callable[[], None]]]:
        if tipo_molde == "completo":
            medida_txt = f"alto {formatear_numero(alto_mm)} mm"
        else:
            medida_txt = (
                f"largo {formatear_numero(largo_mm)} mm, grosor {formatear_numero(grosor_mm)} mm"
            )
        proveedor_txt = proveedor if proveedor else "sin asignar"

        campos: list[tuple[str, Callable[[], None]]] = [
            (f"Nombre: {nombre}", _editar_nombre),
            (
                f"Carpeta donde se creará: {(destino / nombre).resolve()}",
                _no_editable(
                    "La carpeta se calcula a partir del nombre; cambia esa línea si quieres "
                    "que el diseño se cree en otro sitio."
                ),
            ),
            (f"Carpeta del brief: {brief}", _editar_brief),
        ]
        if imagen is not None:
            campos.append((f"Imagen: {imagen}", _editar_imagen_y_vista))
            if vista is not None:
                campos.append((f"Vista: {vista}", _editar_imagen_y_vista))
        campos.extend(
            [
                (f"Qué imprime castkit: {modo}", _editar_modo),
                (f"Tipo de molde: {tipo_molde} (proveedor: {proveedor_txt})", _editar_tipo_pieza),
                (f"Medidas: {medida_txt}", _editar_medidas),
                (f"Material de la pieza final: {material}", _editar_material),
                (f"Cavidades: {cavidades}", _editar_cavidades),
                (f"Cantidad a producir: {cantidad}", _editar_cantidad),
                (f"Fecha de entrega: {fecha.isoformat()}", _editar_fecha),
                (f"Licencia del modelo base: {licencia}", _editar_licencia),
                (f"Origen del modelo base: {origen}", _editar_origen),
            ]
        )
        return campos

    while True:
        campos = _construir_campos()
        print("\nResumen del diseño:")
        for indice, (texto, _editar) in enumerate(campos, start=1):
            print(f"{indice}. {texto}")

        respuesta = (
            input("¿Crear el diseño? (S/n, o el número de la línea que quieres cambiar): ")
            .strip()
            .lower()
        )

        if not respuesta or respuesta in ("s", "si", "sí"):
            break
        if respuesta in ("n", "no"):
            print("No se ha creado nada.", file=sys.stderr)
            return 1
        if respuesta.isdigit() and 1 <= int(respuesta) <= len(campos):
            _, editar = campos[int(respuesta) - 1]
            editar()
            continue
        print(f"Respuesta inválida: escribe «s», «n» o un número de línea entre 1 y {len(campos)}.")

    try:
        datos = DatosAlta(
            nombre=nombre,
            brief=brief,
            material=material,
            alto_mm=alto_mm,
            cavidades=cavidades,
            cantidad=cantidad,
            fecha=fecha,
            licencia=licencia,
            origen=origen,
            tipo_molde=tipo_molde,
            largo_mm=largo_mm,
            grosor_mm=grosor_mm,
            proveedor=proveedor,
            modo_matriz=modo,
            imagen=imagen,
            vista=vista,
        )
        ruta = crear_diseno(datos, destino=destino)
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Diseño «{nombre}» creado en {ruta}.")
    print(f"Siguiente paso: castkit flujo {nombre}")
    return 0


def _ejecutar_version(args: argparse.Namespace) -> int:
    # Se valida el nombre y se ubica el diseño ANTES de preguntar nada ni de
    # tocar `--alto-mm`/`--cavidades`: si el diseño no existe, no se pregunta.
    try:
        carpeta = ubicar_diseno(args.nombre)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # El tipo de molde efectivo (si se indicó alguno de --tipo-molde,
    # --largo-mm, --grosor-mm o --alto-mm) sigue las mismas reglas que
    # `castkit alta`; si no se indicó ninguno, `crear_version` hereda el tipo
    # de la versión actual.
    tipo_molde = _determinar_tipo_molde(
        tipo_molde_arg=args.tipo_molde,
        alto_mm_arg=args.alto_mm,
        largo_mm_arg=args.largo_mm,
        grosor_mm_arg=args.grosor_mm,
    )

    try:
        if tipo_molde is not None:
            tipo_molde = _validar_eleccion(tipo_molde, "--tipo-molde", TIPOS_MOLDE)
            _validar_combinacion_tipo_molde(
                tipo_molde,
                alto_mm_arg=args.alto_mm,
                largo_mm_arg=args.largo_mm,
                grosor_mm_arg=args.grosor_mm,
            )
        alto_mm = (
            _parsear_numero_positivo(args.alto_mm, "--alto-mm")
            if args.alto_mm is not None
            else None
        )
        largo_mm = (
            _parsear_numero_positivo(args.largo_mm, "--largo-mm")
            if args.largo_mm is not None
            else None
        )
        grosor_mm = (
            _parsear_numero_positivo(args.grosor_mm, "--grosor-mm")
            if args.grosor_mm is not None
            else None
        )
        cavidades = (
            _parsear_entero_minimo_uno(args.cavidades, "--cavidades")
            if args.cavidades is not None
            else None
        )
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    motivo = args.motivo
    if not motivo:
        if sys.stdin.isatty():
            motivo = input("Motivo de la nueva versión: ").strip()
        else:
            print(
                "Error: falta la opción obligatoria --motivo: describe qué cambió en la "
                "nueva versión.",
                file=sys.stderr,
            )
            return 2

    try:
        nueva_version = crear_version(
            carpeta,
            motivo,
            alto_mm=alto_mm,
            cavidades=cavidades,
            tipo_molde=tipo_molde,
            largo_mm=largo_mm,
            grosor_mm=grosor_mm,
        )
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Versión v{nueva_version} de «{args.nombre}» creada en {carpeta}")
    return 0


def _ejecutar_ingesta(args: argparse.Namespace) -> int:
    # Se ubica el diseño ANTES de limpiar la ruta o validar el archivo: si el
    # diseño no existe, no se toca el archivo ni se pregunta nada.
    try:
        carpeta = ubicar_diseno(args.nombre)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    archivo = Path(_limpiar_ruta(args.archivo))

    try:
        info = validar_malla(archivo)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    datos = cargar_diseno(carpeta)
    version_actual = datos["version_actual"]
    nombre_destino = nombre_archivo(datos["nombre"], version_actual, "bruto", "stl")
    destino = carpeta / "input" / nombre_destino

    reemplazar = args.reemplazar
    if destino.exists() and not reemplazar:
        if sys.stdin.isatty():
            respuesta = (
                input(f"Esta versión ya tiene una malla ({nombre_destino}). ¿Reemplazarla? (s/N): ")
                .strip()
                .lower()
            )
            if respuesta in ("s", "si", "sí"):
                reemplazar = True
            else:
                print("No se ha modificado nada.", file=sys.stderr)
                return 1
        else:
            print(
                f"Error: la versión v{version_actual} de «{args.nombre}» ya tiene una malla "
                f"guardada; usa --reemplazar para sustituirla.",
                file=sys.stderr,
            )
            return 2

    try:
        destino_final = ingerir_malla(carpeta, archivo, reemplazar=reemplazar)
    except (AltaError, VersionError, IngestaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Malla de «{args.nombre}» guardada en {destino_final} "
        f"({info['caras']} caras, {info['vertices']} vértices)."
    )
    return 0


def _ejecutar_limpieza(args: argparse.Namespace) -> int:
    # Se ubica el diseño ANTES de leer la config o validar las opciones: si
    # el diseño no existe, no se hace nada más.
    try:
        carpeta = ubicar_diseno(args.nombre)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        config = cargar_config_limpieza()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        arriba = validar_arriba(args.arriba) if args.arriba is not None else None
        caras_max = (
            _parsear_entero_minimo_uno(args.caras_max, "--caras-max")
            if args.caras_max is not None
            else int(config["caras_max"])
        )
        tolerancia_mm = (
            _parsear_numero_positivo(args.tolerancia_mm, "--tolerancia-mm")
            if args.tolerancia_mm is not None
            else float(config["tolerancia_mm"])
        )
        corte_base_mm = (
            _parsear_numero_positivo(args.corte_base_mm, "--corte-base-mm")
            if args.corte_base_mm is not None
            else float(config["corte_base_mm"])
        )
        agujero_max_mm = (
            _parsear_numero_positivo(args.agujero_max_mm, "--agujero-max-mm")
            if args.agujero_max_mm is not None
            else float(config["agujero_max_mm"])
        )
        voxel_mm = (
            _parsear_numero_positivo(args.voxel_mm, "--voxel-mm")
            if getattr(args, "voxel_mm", None) is not None
            else float(config.get("voxel_mm", 0.3))
        )
    except (AltaError, LimpiezaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        resultado = limpiar_malla(
            carpeta,
            arriba=arriba,
            caras_max=caras_max,
            tolerancia_mm=tolerancia_mm,
            corte_base_mm=corte_base_mm,
            agujero_max_mm=agujero_max_mm,
            voxel_mm=voxel_mm,
            sin_blender=bool(getattr(args, "sin_blender", False)),
        )
    except (AltaError, VersionError, LimpiezaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if resultado["version_bruto"] != resultado["version_actual"]:
        print(
            f"Aviso: la versión v{resultado['version_actual']} de «{args.nombre}» no tiene "
            f"malla en bruto propia; se usó la de v{resultado['version_bruto']}."
        )
    if resultado.get("metodo") == "blender":
        print("Aviso: no se pudo cerrar con el método rápido; se cerró con remallado de Blender.")
    if resultado["reemplazo"]:
        print(f"Se reemplazó la malla limpia anterior de «{args.nombre}».")

    print(
        f"Malla limpia de «{args.nombre}» guardada en {resultado['destino']} "
        f"({resultado['caras']} caras, distancia máxima {resultado['distancia_mm']:.3f} mm)."
    )
    return 0


def _ejecutar_generar(args: argparse.Namespace) -> int:
    try:
        carpeta = ubicar_diseno(args.nombre)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    datos = cargar_diseno(carpeta)
    version_actual = datos["version_actual"]
    nombre_destino = nombre_archivo(datos["nombre"], version_actual, "bruto", "stl")
    destino = carpeta / "input" / nombre_destino

    reemplazar = args.reemplazar
    if destino.exists() and not reemplazar:
        if sys.stdin.isatty():
            respuesta = (
                input(f"Esta versión ya tiene una malla ({nombre_destino}). ¿Reemplazarla? (s/N): ")
                .strip()
                .lower()
            )
            if respuesta in ("s", "si", "sí"):
                reemplazar = True
            else:
                print("No se ha modificado nada.", file=sys.stderr)
                return 1
        else:
            print(
                f"Error: la versión v{version_actual} de «{args.nombre}» ya tiene una malla "
                "guardada; usa --reemplazar para sustituirla.",
                file=sys.stderr,
            )
            return 2

    try:
        resultado = generar_malla(
            carpeta,
            proveedor=args.proveedor,
            prompt=args.prompt,
            reemplazar=reemplazar,
        )
    except (AltaError, VersionError, ConfigError, GeneracionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Malla de «{args.nombre}» generada con «{resultado['proveedor']}» en "
        f"{resultado['destino']} ({resultado['caras']} caras, {resultado['vertices']} vértices)."
    )
    return 0


def _ejecutar_mecha(args: argparse.Namespace) -> int:
    try:
        carpeta = ubicar_diseno(args.nombre)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        config = cargar_config_mecha()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        diametro_mm = (
            _parsear_numero_positivo(args.diametro_mm, "--diametro-mm")
            if args.diametro_mm is not None
            else float(config["diametro_mecha_mm"])
        )
        espesor_minimo_mm = (
            _parsear_numero_positivo(args.espesor_minimo_mm, "--espesor-minimo-mm")
            if args.espesor_minimo_mm is not None
            else float(config["espesor_minimo_pared_mm"])
        )
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    forzar = bool(args.forzar)
    try:
        resultado = aplicar_canal_mecha(
            carpeta,
            diametro_mecha_mm=diametro_mm,
            espesor_minimo_pared_mm=espesor_minimo_mm,
            confirmar_pared_fina=forzar,
        )
    except ParedFinaMechaError as exc:
        if sys.stdin.isatty():
            respuesta = input(f"Aviso: {exc} ¿Continuar de todos modos? (s/N): ").strip().lower()
            if respuesta in ("s", "si", "sí"):
                resultado = aplicar_canal_mecha(
                    carpeta,
                    diametro_mecha_mm=diametro_mm,
                    espesor_minimo_pared_mm=espesor_minimo_mm,
                    confirmar_pared_fina=True,
                )
            else:
                print("No se ha modificado nada.", file=sys.stderr)
                return 1
        else:
            print(f"Error: {exc} Usa --forzar para continuar.", file=sys.stderr)
            return 2
    except (AltaError, VersionError, MechaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not resultado["aplicado"]:
        if resultado.get("tipo_molde") == "abierto":
            print(
                "Aviso: el canal de mecha solo se hace en molde completo; "
                f"«{args.nombre}» es molde abierto, no se perforó nada."
            )
        else:
            print(
                f"El diseño «{args.nombre}» es de «{resultado['material']}»: "
                "no requiere canal de mecha."
            )
        return 0

    print(
        f"Canal de mecha ({resultado['diametro_mecha_mm']} mm) aplicado en {resultado['destino']} "
        f"(espesor mínimo de pared {resultado['espesor_pared_medido_mm']:.2f} mm)."
    )
    return 0


def _ejecutar_control1(args: argparse.Namespace) -> int:
    try:
        carpeta = ubicar_diseno(args.nombre)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    aprobar = bool(getattr(args, "aprobar", False))
    rechazar = bool(getattr(args, "rechazar", False))
    if aprobar and rechazar:
        print(
            "Error: no puedes usar --aprobar y --rechazar al mismo tiempo.",
            file=sys.stderr,
        )
        return 1

    decision: str | None = None
    motivo: str | None = getattr(args, "motivo", None)

    if aprobar:
        decision = "aprobado"
        motivo = None
    elif rechazar:
        decision = "rechazado"
        if motivo is None and sys.stdin.isatty():
            motivo = input("Motivo del rechazo: ").strip()
        if not motivo or not motivo.strip():
            print(
                "Error: falta la opción obligatoria --motivo: para rechazar el punto de "
                "control 1 debes explicar qué corregir.",
                file=sys.stderr,
            )
            return 2 if getattr(args, "motivo", None) is None else 1

    try:
        resultado = ejecutar_control1(
            carpeta,
            decision=decision,
            motivo=motivo,
        )
    except (AltaError, VersionError, Control1Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    dims = resultado["dimensiones_mm"]
    rutas_str = ", ".join(str(r) for r in resultado["renders"])
    print(
        f"Renders del punto de control 1 de «{args.nombre}» (v{resultado['version']}) "
        f"generados en: {rutas_str} "
        f"(Alto: {dims['alto_mm']:.1f} mm, Ancho: {dims['ancho_mm']:.1f} mm, "
        f"Fondo: {dims['fondo_mm']:.1f} mm)."
    )

    if decision is None and sys.stdin.isatty():
        respuesta = input("¿Apruebas la figura en el punto de control 1? (s/N): ").strip().lower()
        if respuesta in ("s", "si", "sí"):
            reg = registrar_decision_control1(carpeta, decision="aprobado")
            resultado["estado"] = reg["estado"]
            resultado["fecha"] = reg["fecha"]
        else:
            motivo_input = input("Motivo del rechazo: ").strip()
            if not motivo_input:
                print(
                    "Error: para rechazar el punto de control 1 debes indicar un motivo.",
                    file=sys.stderr,
                )
                return 1
            reg = registrar_decision_control1(carpeta, decision="rechazado", motivo=motivo_input)
            resultado["estado"] = reg["estado"]
            resultado["motivo"] = reg["motivo"]
            resultado["fecha"] = reg["fecha"]

    if resultado["estado"] == "aprobado":
        print(
            f"Punto de control 1 aprobado para «{args.nombre}» "
            f"(v{resultado['version']}, {resultado['fecha']})."
        )
    elif resultado["estado"] == "rechazado":
        print(
            f"Punto de control 1 rechazado para «{args.nombre}» "
            f"(v{resultado['version']}): {resultado['motivo']}"
        )
    else:
        print(
            f"Punto de control 1 pendiente de decisión para «{args.nombre}» "
            f"(v{resultado['version']}). Usa --aprobar o --rechazar --motivo <texto>."
        )

    return 0


def _ejecutar_matriz(args: argparse.Namespace) -> int:
    try:
        carpeta = ubicar_diseno(args.nombre)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        config = cargar_config_matriz()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    modo = args.modo
    if not modo:
        # Sin --modo explícito, se usa el guardado en la versión actual (lo
        # dejó `castkit alta` o `castkit version`, ticket #65) antes de
        # preguntar o fallar; un diseño sin uno guardado se comporta como hoy.
        modo = datos_version(cargar_diseno(carpeta)).get("modo_matriz")
    if not modo:
        if sys.stdin.isatty():
            modo = (
                input(f"¿Qué tipo de matriz deseas crear ({', '.join(MODOS_MATRIZ)})? ")
                .strip()
                .lower()
            )
        else:
            print(
                "Error: falta la opción obligatoria --modo: indica --modo figura o --modo cubeta.",
                file=sys.stderr,
            )
            return 2

    try:
        modo = validar_modo_matriz(modo)
        espesor_base_mm = (
            _parsear_numero_positivo(args.espesor_base_mm, "--espesor-base-mm")
            if args.espesor_base_mm is not None
            else float(config["espesor_base_mm"])
        )
        margen_silicona_mm = (
            _parsear_numero_positivo(args.margen_silicona_mm, "--margen-silicona-mm")
            if args.margen_silicona_mm is not None
            else float(config["margen_silicona_mm"])
        )
        espesor_pared_mm = (
            _parsear_numero_positivo(args.espesor_pared_mm, "--espesor-pared-mm")
            if args.espesor_pared_mm is not None
            else float(config["espesor_pared_mm"])
        )
        margen_superior_mm = (
            _parsear_numero_positivo(args.margen_superior_mm, "--margen-superior-mm")
            if args.margen_superior_mm is not None
            else float(config["margen_superior_mm"])
        )
        separacion_cavidades_mm = (
            _parsear_numero_positivo(args.separacion_cavidades_mm, "--separacion-cavidades-mm")
            if args.separacion_cavidades_mm is not None
            else float(config["separacion_cavidades_mm"])
        )
    except (AltaError, MatrizError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        resultado = generar_matriz(
            carpeta,
            modo=modo,
            espesor_base_mm=espesor_base_mm,
            margen_silicona_mm=margen_silicona_mm,
            espesor_pared_mm=espesor_pared_mm,
            margen_superior_mm=margen_superior_mm,
            separacion_cavidades_mm=separacion_cavidades_mm,
        )
    except (AltaError, VersionError, MatrizError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    dims = resultado["dimensiones_mm"]
    extra_silicona = (
        f", ~{resultado['volumen_silicona_ml']:.1f} ml de silicona"
        if resultado["volumen_silicona_ml"] is not None
        else ""
    )
    print(
        f"Matriz ({resultado['modo']}) de «{args.nombre}» guardada en {resultado['destino']} "
        f"({dims['ancho_mm']} × {dims['fondo_mm']} × {dims['alto_total_mm']} mm"
        f"{extra_silicona}) y ficha en {resultado['ficha']}."
    )
    return 0


def _ejecutar_flujo(args: argparse.Namespace) -> int:
    rc_gen = _ejecutar_generar(args)
    if rc_gen != 0:
        return rc_gen

    args_limpieza = argparse.Namespace(
        nombre=args.nombre,
        arriba=None,
        caras_max=None,
        tolerancia_mm=None,
        corte_base_mm=None,
        agujero_max_mm=None,
        voxel_mm=None,
        sin_blender=False,
    )
    rc_limp = _ejecutar_limpieza(args_limpieza)
    if rc_limp != 0:
        return rc_limp

    args_mecha = argparse.Namespace(
        nombre=args.nombre,
        diametro_mm=None,
        espesor_minimo_mm=None,
        forzar=False,
    )
    rc_mecha = _ejecutar_mecha(args_mecha)
    if rc_mecha != 0:
        return rc_mecha

    args_control1 = argparse.Namespace(
        nombre=args.nombre,
        aprobar=bool(getattr(args, "aprobar_control1", False)),
        rechazar=False,
        motivo=None,
    )
    rc_c1 = _ejecutar_control1(args_control1)
    if rc_c1 != 0:
        return rc_c1

    # El flujo se detiene en el punto de control 1 si nadie aprobó la figura.
    try:
        carpeta = ubicar_diseno(args.nombre)
        verificar_control1_para_matriz(carpeta, cargar_diseno(carpeta), exigir_control1=True)
    except (AltaError, VersionError, Control1Error) as exc:
        print(f"Flujo detenido en el punto de control 1: {exc}", file=sys.stderr)
        return 1

    args_matriz = argparse.Namespace(
        nombre=args.nombre,
        modo=args.modo,
        espesor_base_mm=None,
        margen_silicona_mm=None,
        espesor_pared_mm=None,
        margen_superior_mm=None,
        separacion_cavidades_mm=None,
    )
    return _ejecutar_matriz(args_matriz)


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de `castkit`."""
    _configurar_salida_utf8()
    parser = _construir_parser()
    args = parser.parse_args(argv)

    if args.comando == "alta":
        return _ejecutar_alta(args)
    if args.comando == "version":
        return _ejecutar_version(args)
    if args.comando == "ingesta":
        return _ejecutar_ingesta(args)
    if args.comando == "generar":
        return _ejecutar_generar(args)
    if args.comando == "limpieza":
        return _ejecutar_limpieza(args)
    if args.comando == "mecha":
        return _ejecutar_mecha(args)
    if args.comando == "control1":
        return _ejecutar_control1(args)
    if args.comando == "matriz":
        return _ejecutar_matriz(args)
    if args.comando == "flujo":
        return _ejecutar_flujo(args)
    if args.comando == "comprobar":
        if getattr(args, "estado_blender", False):
            return imprimir_estado_blender()
        return comprobar_instalacion()

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
