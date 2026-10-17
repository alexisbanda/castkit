"""Interfaz de línea de comandos de castkit."""

from __future__ import annotations

import argparse
import contextlib
import math
import sys
from datetime import date
from pathlib import Path

from castkit.diseno import (
    LICENCIAS,
    MATERIALES,
    AltaError,
    DatosAlta,
    carpeta_disenos,
    crear_diseno,
    validar_nombre,
)
from castkit.versiones import VersionError, crear_version, ubicar_diseno

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


def _limpiar_ruta_brief(valor: str) -> str:
    """Normaliza una ruta de brief recibida por opción o por respuesta interactiva.

    Quita las comillas simples o dobles que la envuelvan (arrastrar una
    carpeta a la terminal suele añadirlas) y expande ``~`` a la carpeta
    personal antes de validar la ruta.
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
        brief = _limpiar_ruta_brief(valores["brief"])

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


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de `castkit`."""
    _configurar_salida_utf8()
    parser = _construir_parser()
    args = parser.parse_args(argv)

    if args.comando == "alta":
        return _ejecutar_alta(args)
    if args.comando == "version":
        return _ejecutar_version(args)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
