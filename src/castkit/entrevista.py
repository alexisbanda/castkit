"""Primitivas de la entrevista guiada de `castkit alta` (ticket #65).

Cada pregunta imprime, si la tiene, una línea de explicación y pide la
respuesta con el valor por defecto entre corchetes (``[defecto]``), que se
acepta con Intro. Una respuesta inválida imprime el motivo en español («Respuesta
inválida: ...») y vuelve a preguntar solo esa pregunta, sin perder nada de lo
ya contestado (eso lo garantiza quien orquesta la entrevista en `cli.py`, que
solo avanza cuando cada pregunta devuelve un valor válido).

Usa `input()` y `print()`, igual que el resto de la CLI, para que los tests
puedan parchear `builtins.input` como ya hacen con el resto de preguntas
interactivas de castkit.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date


def formatear_numero(valor: float) -> str:
    """Formatea ``valor`` sin ``.0`` cuando es un entero exacto (p. ej. ``80`` y no ``80.0``)."""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def preguntar[T](
    etiqueta: str,
    explicacion: str | None,
    defecto: str | None,
    *,
    parsear: Callable[[str], T],
) -> T:
    """Pregunta ``etiqueta`` [``defecto``] y reintenta mientras ``parsear`` lance ``ValueError``.

    Si ``explicacion`` no es ``None``, se imprime antes como línea aparte. Si
    ``defecto`` es ``None``, el prompt no muestra corchetes y una respuesta
    vacía se pasa tal cual a ``parsear`` (que normalmente la rechazará, p. ej.
    una carpeta de brief sin valor por defecto posible).
    """
    if explicacion:
        print(explicacion)
    sufijo = f" [{defecto}]" if defecto is not None else ""
    while True:
        respuesta = input(f"{etiqueta}{sufijo}: ").strip()
        if not respuesta and defecto is not None:
            respuesta = defecto
        try:
            return parsear(respuesta)
        except ValueError as exc:
            print(f"Respuesta inválida: {exc}")


def preguntar_texto(
    etiqueta: str,
    explicacion: str | None,
    defecto: str | None,
    *,
    validar: Callable[[str], None] | None = None,
) -> str:
    """Pregunta un texto libre; ``validar`` lanza ``ValueError`` si no es válido."""

    def _parsear(valor: str) -> str:
        if validar is not None:
            validar(valor)
        return valor

    return preguntar(etiqueta, explicacion, defecto, parsear=_parsear)


def preguntar_numero_positivo(etiqueta: str, explicacion: str | None, defecto: float) -> float:
    """Pregunta un número mayor que 0; acepta punto o coma decimal (``92,5``)."""

    def _parsear(valor: str) -> float:
        try:
            numero = float(valor.replace(",", "."))
        except ValueError:
            raise ValueError(f'debe ser un número mayor que 0; recibido "{valor}".') from None
        if not math.isfinite(numero) or numero <= 0:
            raise ValueError(f'debe ser un número mayor que 0; recibido "{valor}".')
        return numero

    return preguntar(etiqueta, explicacion, formatear_numero(defecto), parsear=_parsear)


def preguntar_medida_mm(
    etiqueta: str,
    explicacion: str | None,
    defecto: float,
    *,
    limite_mm: float = 300.0,
    comparar_mayor_o_igual: tuple[str, float] | None = None,
) -> float:
    """Pregunta una medida en mm; pide confirmación (s/N) si es rara.

    Es rara si supera ``limite_mm``, o si ``comparar_mayor_o_igual`` (una
    medida ya contestada, con su etiqueta) es menor o igual que la respuesta
    (p. ej. el grosor es mayor o igual que el largo). Con «n» o Intro en la
    confirmación se vuelve a preguntar la medida, sin perder el resto de la
    entrevista.
    """
    while True:
        valor = preguntar_numero_positivo(etiqueta, explicacion, defecto)
        explicacion = None  # No repetir la explicación en los reintentos de esta medida.
        aviso = None
        if valor > limite_mm:
            aviso = (
                f"{formatear_numero(valor)} mm es mayor que {formatear_numero(limite_mm)} mm, "
                "poco habitual para este taller."
            )
        elif comparar_mayor_o_igual is not None:
            otra_etiqueta, otro_valor = comparar_mayor_o_igual
            if otro_valor is not None and valor >= otro_valor:
                aviso = (
                    f"{formatear_numero(valor)} mm es igual o mayor que el {otra_etiqueta} "
                    f"({formatear_numero(otro_valor)} mm)."
                )
        if aviso is None:
            return valor
        respuesta = input(f"{aviso} ¿Es correcto? (s/N): ").strip().lower()
        if respuesta in ("s", "si", "sí"):
            return valor


def preguntar_entero_minimo_uno(etiqueta: str, explicacion: str | None, defecto: int) -> int:
    """Pregunta un entero mayor o igual a 1."""

    def _parsear(valor: str) -> int:
        try:
            numero = int(valor)
        except ValueError:
            raise ValueError(
                f'debe ser un entero mayor o igual que 1; recibido "{valor}".'
            ) from None
        if numero < 1:
            raise ValueError(f'debe ser un entero mayor o igual que 1; recibido "{valor}".')
        return numero

    return preguntar(etiqueta, explicacion, str(defecto), parsear=_parsear)


def preguntar_fecha(etiqueta: str, explicacion: str | None, defecto: date) -> date:
    """Pregunta una fecha AAAA-MM-DD; rechaza fechas anteriores a hoy."""

    def _parsear(valor: str) -> date:
        try:
            fecha = date.fromisoformat(valor)
        except ValueError:
            raise ValueError(
                f'debe ser una fecha AAAA-MM-DD válida; recibido "{valor}".'
            ) from None
        if fecha < date.today():
            raise ValueError(f'debe ser hoy o una fecha futura; recibido "{valor}".')
        return fecha

    return preguntar(etiqueta, explicacion, defecto.isoformat(), parsear=_parsear)


def preguntar_opcion_numerada(
    etiqueta: str,
    explicacion: str | None,
    opciones: Sequence[tuple[str, str]],
    defecto_valor: str,
) -> str:
    """Pregunta eligiendo entre ``opciones`` (valor, descripción) numeradas desde 1.

    Intro acepta ``defecto_valor``; también se acepta escribir el valor
    directamente (p. ej. «silicona» en vez de «2»).
    """
    if explicacion:
        print(explicacion)
    indice_defecto = next(
        indice for indice, (valor, _) in enumerate(opciones, start=1) if valor == defecto_valor
    )
    descripciones = " ".join(
        f"{indice}) {desc}" for indice, (_, desc) in enumerate(opciones, start=1)
    )
    prompt = f"{etiqueta} {descripciones} [{indice_defecto}]: "
    while True:
        respuesta = input(prompt).strip()
        if not respuesta:
            return defecto_valor
        if respuesta.isdigit():
            indice = int(respuesta)
            if 1 <= indice <= len(opciones):
                return opciones[indice - 1][0]
        for valor, _ in opciones:
            if respuesta.lower() == valor.lower():
                return valor
        rango = f"1 a {len(opciones)}" if len(opciones) > 1 else "1"
        print(f"Respuesta inválida: elige una opción entre {rango}.")


def confirmar(prompt: str, *, defecto: bool = False) -> bool:
    """Pregunta ``prompt`` (ya con su propio «(s/N)» o «(S/n)») y devuelve ``bool``."""
    respuesta = input(prompt).strip().lower()
    if not respuesta:
        return defecto
    return respuesta in ("s", "si", "sí")
