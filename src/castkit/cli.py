"""Interfaz de línea de comandos de castkit."""

from __future__ import annotations

import argparse
import contextlib
import math
import sys
from datetime import date
from pathlib import Path

from castkit.config import (
    ConfigError,
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
    AltaError,
    DatosAlta,
    carpeta_disenos,
    crear_diseno,
    validar_nombre,
)
from castkit.generacion import GeneracionError, generar_malla
from castkit.ingesta import IngestaError, ingerir_malla, validar_malla
from castkit.limpieza import EJES_ACEPTADOS, LimpiezaError, limpiar_malla, validar_arriba
from castkit.matriz import MODOS_MATRIZ, MatrizError, generar_matriz, validar_modo_matriz
from castkit.mecha import MechaError, ParedFinaMechaError, aplicar_canal_mecha
from castkit.versiones import (
    VersionError,
    cargar_diseno,
    crear_version,
    nombre_archivo,
    ubicar_diseno,
)

# Orden en el que se piden/validan las opciones del subcomando `alta`.
_OPCIONES = ("brief", "material", "alto_mm", "cavidades", "cantidad", "fecha", "licencia", "origen")

_NOMBRES_OPCION = {
    "brief": "--brief",
    "material": "--material",
    "alto_mm": "--alto-mm",
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
    alta.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    alta.add_argument("--brief", default=None, help="Carpeta con el brief del diseño.")
    alta.add_argument("--material", default=None, help=f"Material: {', '.join(MATERIALES)}.")
    alta.add_argument("--alto-mm", dest="alto_mm", default=None, help="Alto en milímetros.")
    alta.add_argument("--cavidades", default=None, help="Número de cavidades del molde.")
    alta.add_argument("--cantidad", default=None, help="Cantidad de piezas a producir.")
    alta.add_argument("--fecha", default=None, help="Fecha de entrega, en formato AAAA-MM-DD.")
    alta.add_argument(
        "--licencia", default=None, help=f"Licencia del modelo base: {', '.join(LICENCIAS)}."
    )
    alta.add_argument("--origen", default=None, help="Origen del modelo base (texto libre).")

    version = subparsers.add_parser(
        "version", help="Crea la siguiente versión de un diseño existente."
    )
    version.add_argument("nombre", help="Nombre del diseño (minúsculas, números y guiones).")
    version.add_argument("--motivo", default=None, help="Motivo de la nueva versión (qué cambió).")
    version.add_argument(
        "--alto-mm", dest="alto_mm", default=None, help="Nuevo alto en milímetros (opcional)."
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
        default="z",
        help=f"Eje que debe quedar hacia +Z antes de escalar: {', '.join(EJES_ACEPTADOS)} "
        '(por defecto "z").',
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
    control1.add_argument(
        "--sin-blender",
        dest="sin_blender",
        action="store_true",
        help="Genera las vistas en PNG con el renderizador de Python sin invocar a Blender.",
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


def _pedir_opciones_faltantes(valores: dict[str, str | None]) -> list[str]:
    """Pregunta por cada opción vacía en ``valores`` y la actualiza en el sitio.

    Cada respuesta se limpia con ``.strip()``. Devuelve la lista de opciones
    que, tras preguntar, sigan vacías.
    """
    for clave in _OPCIONES:
        if not valores[clave]:
            valores[clave] = input(_PREGUNTAS[clave]).strip()
    return [clave for clave in _OPCIONES if not valores[clave]]


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


def _ejecutar_alta(args: argparse.Namespace) -> int:
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

    valores: dict[str, str | None] = {
        "brief": args.brief,
        "material": args.material,
        "alto_mm": args.alto_mm,
        "cavidades": args.cavidades,
        "cantidad": args.cantidad,
        "fecha": args.fecha,
        "licencia": args.licencia,
        "origen": args.origen,
    }

    faltantes = [clave for clave in _OPCIONES if not valores[clave]]
    if faltantes:
        if sys.stdin.isatty():
            faltantes = _pedir_opciones_faltantes(valores)
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
        alto_mm = _parsear_numero_positivo(valores["alto_mm"], "--alto-mm")
        cavidades = _parsear_entero_minimo_uno(valores["cavidades"], "--cavidades")
        cantidad = _parsear_entero_minimo_uno(valores["cantidad"], "--cantidad")
        fecha = _parsear_fecha(valores["fecha"])
        brief = _limpiar_ruta(valores["brief"])

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
        )
        ruta = crear_diseno(datos, destino=destino)
    except AltaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Diseño «{datos.nombre}» creado en {ruta}")
    return 0


def _ejecutar_version(args: argparse.Namespace) -> int:
    # Se valida el nombre y se ubica el diseño ANTES de preguntar nada ni de
    # tocar `--alto-mm`/`--cavidades`: si el diseño no existe, no se pregunta.
    try:
        carpeta = ubicar_diseno(args.nombre)
    except (AltaError, VersionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        alto_mm = (
            _parsear_numero_positivo(args.alto_mm, "--alto-mm")
            if args.alto_mm is not None
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
        nueva_version = crear_version(carpeta, motivo, alto_mm=alto_mm, cavidades=cavidades)
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
        arriba = validar_arriba(args.arriba)
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
            sin_blender=bool(getattr(args, "sin_blender", False)),
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
        arriba="z",
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
        sin_blender=False,
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

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
