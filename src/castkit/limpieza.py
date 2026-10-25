"""Limpieza y escalado automático de la malla: etapa `limpieza` del pipeline.

Parte de la malla en bruto incorporada con `castkit ingesta` (ver
:mod:`castkit.ingesta`) y deja, en `work/`, una malla cerrada (watertight),
escalada al `alto_mm` de la versión actual y con la base recortada y plana en
z = 0, lista para las siguientes etapas de fabricación.

Pasos del pipeline (ver detalles en cada función):

1. Localizar la malla en bruto más reciente (la de la versión actual, o la de
   la versión anterior más reciente que tenga una).
2. Orientar la malla según `--arriba` (qué eje debe quedar hacia +Z).
3. Escalar uniformemente para que la altura sea `alto_mm + corte_base_mm`.
4. Cerrar los huecos de la malla, siempre que ninguno supere
   `agujero_max_mm` de perímetro; si alguno lo supera, no se cierra nada y se
   informa de todas las zonas abiertas.
5. Recortar `corte_base_mm` desde abajo (con un corte que deja la malla
   cerrada, vía `manifold3d`) y trasladar para que la base quede en z = 0.
6. Simplificar a `caras_max` caras como máximo (si la malla ya tiene menos,
   no se simplifica). Se simplifica DESPUÉS de recortar la base para que el
   resultado nunca tenga más caras que `caras_max` (recortar después de
   simplificar añadiría de vuelta las caras del corte).
7. Arreglar normales y comprobar que el resultado esté cerrado y tenga
   volumen positivo.
8. Medir la distancia máxima contra la malla original orientada, escalada y
   recortada (sin simplificar), excluyendo de la medición las zonas que
   rellenó el paso 4; si supera `tolerancia_mm`, se rechaza el resultado. Si
   la malla se cerró con el respaldo de Blender, además se mide contra la
   original escalada (antes del remallado), y se registra la peor distancia.
9. Escribir la malla limpia en `work/` y registrar los parámetros usados.

Sigue la REGLA de :mod:`castkit.versiones`: la salida se nombra con
:func:`castkit.versiones.nombre_archivo` y los parámetros se registran con
:func:`castkit.versiones.registrar_parametros`, en la etapa `"limpieza"`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import trimesh
from trimesh.grouping import group_rows

from castkit.ingesta import sha256_archivo
from castkit.versiones import (
    VersionError,
    cargar_diseno,
    datos_version,
    nombre_archivo,
    registrar_parametros,
)

EJES_ACEPTADOS = ("x", "-x", "y", "-y", "z", "-z")

_VECTORES_EJE = {
    "x": np.array([1.0, 0.0, 0.0]),
    "-x": np.array([-1.0, 0.0, 0.0]),
    "y": np.array([0.0, 1.0, 0.0]),
    "-y": np.array([0.0, -1.0, 0.0]),
    "z": np.array([0.0, 0.0, 1.0]),
    "-z": np.array([0.0, 0.0, -1.0]),
}

_EJE_Z = np.array([0.0, 0.0, 1.0])

# Puntos muestreados en cada sentido al medir la distancia máxima entre la
# malla original (recortada, sin simplificar) y la malla limpia.
_N_MUESTRAS_DISTANCIA = 300

# Margen sobre el radio de cada hueco cerrado al excluir sus zonas de la
# medición de distancia (ver `_distancia_maxima`).
_MARGEN_EXCLUSION = 2.0

# Tope de pares punto × triángulo que `_distancias_a_superficie` procesa a
# la vez. Por encima de este tope, el array intermedio de
# `closest_point_naive` (puntos × triángulos) agota la memoria con mallas de
# millones de caras (#52): con 300 puntos contra una malla de 5 M de
# triángulos, ese array llega a varios GB. Medido con `tracemalloc`:
# `trimesh.triangles.closest_point` usa, por los arrays intermedios que
# construye internamente, del orden de 600-700 bytes por par (no los ~24
# bytes de los arrays de entrada/salida); 500_000 pares mantiene el pico de
# memoria de cada bloque por debajo de los ~350 MB, con margen bajo los
# 500 MB exigidos.
_TOPE_PARES_DISTANCIA = 500_000


class LimpiezaError(VersionError):
    """Se lanza cuando la limpieza de la malla no puede completarse.

    El mensaje siempre está en español. Hereda de :class:`VersionError` (y
    por lo tanto de :class:`castkit.diseno.AltaError`) para que quien ya
    captura esas excepciones también capture esta.
    """


def validar_arriba(valor: str) -> str:
    """Valida que ``valor`` sea uno de :data:`EJES_ACEPTADOS`.

    Lanza :class:`LimpiezaError` si no lo es.
    """
    if valor not in EJES_ACEPTADOS:
        raise LimpiezaError(
            f"--arriba debe ser uno de los siguientes valores: {', '.join(EJES_ACEPTADOS)}; "
            f'recibido "{valor}".'
        )
    return valor


def _transformacion_arriba(valor: str) -> np.ndarray:
    """Transformación 4x4 que rota el eje ``valor`` hasta +Z."""
    eje = _VECTORES_EJE[valor]
    if np.allclose(eje, _EJE_Z):
        return np.eye(4)
    return trimesh.geometry.align_vectors(eje, _EJE_Z)


def _localizar_malla_bruto(carpeta: Path, datos: dict[str, Any]) -> tuple[Path, int]:
    """Busca la malla en bruto más reciente, desde la versión actual hacia atrás.

    Devuelve ``(ruta, version)``. Lanza :class:`LimpiezaError` si ninguna
    versión, hasta la v1, tiene una malla `_bruto.stl` guardada.
    """
    nombre = datos["nombre"]
    actual = datos["version_actual"]
    for version in range(actual, 0, -1):
        candidata = carpeta / "input" / nombre_archivo(nombre, version, "bruto", "stl")
        if candidata.is_file():
            return candidata, version
    raise LimpiezaError(
        f'El diseño «{nombre}» no tiene ninguna malla en bruto guardada: usa "castkit ingesta" '
        "para incorporarla antes de limpiarla."
    )


def _detectar_huecos(malla: trimesh.Trimesh) -> list[dict[str, Any]]:
    """Devuelve las zonas abiertas (huecos) de ``malla``.

    Cada zona es un ``dict`` con ``centro`` (punto medio de los vértices del
    borde), ``perimetro`` (suma de las distancias entre vértices consecutivos
    del borde) y ``radio`` (distancia máxima del centro a un vértice del
    borde). Usa el mismo método que :func:`trimesh.repair.fill_holes` para
    agrupar los bordes abiertos en ciclos.
    """
    grupos = group_rows(malla.edges_sorted, require_count=1)
    if len(grupos) == 0:
        return []

    borde = malla.edges[grupos]
    ciclos = nx.cycle_basis(nx.from_edgelist(borde))

    huecos = []
    for ciclo in ciclos:
        puntos = malla.vertices[ciclo]
        centro = puntos.mean(axis=0)
        perimetro = float(np.linalg.norm(np.roll(puntos, -1, axis=0) - puntos, axis=1).sum())
        radio = float(np.linalg.norm(puntos - centro, axis=1).max()) if len(puntos) else 0.0
        huecos.append({"centro": centro, "perimetro": perimetro, "radio": radio})
    return huecos


def _formatear_zonas(huecos: list[dict[str, Any]]) -> str:
    lineas = []
    for i, hueco in enumerate(huecos, start=1):
        cx, cy, cz = hueco["centro"]
        lineas.append(
            f"  {i}. centro aproximado ({cx:.1f}, {cy:.1f}, {cz:.1f}) mm, tamaño aproximado "
            f"{hueco['perimetro']:.1f} mm de perímetro"
        )
    return "\n".join(lineas)


def _cerrar_huecos(malla: trimesh.Trimesh, agujero_max_mm: float) -> list[dict[str, Any]]:
    """Cierra los huecos de ``malla`` si todos caben en ``agujero_max_mm``.

    Modifica ``malla`` en el sitio. Devuelve la lista de huecos cerrados
    (para excluir luego sus zonas de la medición de distancia). Si alguno de
    los huecos supera ``agujero_max_mm`` de perímetro, no cierra nada y lanza
    :class:`LimpiezaError` listando todas las zonas abiertas, su posición y
    tamaño aproximados, y los siguientes pasos posibles. Si todos caben pero
    alguno no se puede cerrar (por ejemplo, por no ser una zona simple), lanza
    la misma clase de error listando las zonas que quedaron abiertas tras el
    intento, su posición y tamaño aproximados, y los siguientes pasos.
    """
    huecos = _detectar_huecos(malla)
    if not huecos:
        return []

    grandes = [hueco for hueco in huecos if hueco["perimetro"] > agujero_max_mm]
    if grandes:
        raise LimpiezaError(
            f"La malla tiene {len(huecos)} zona(s) abierta(s) y no se puede cerrar: "
            f"{len(grandes)} de ellas superan el perímetro máximo configurado "
            f"(agujero_max_mm = {agujero_max_mm} mm):\n"
            f"{_formatear_zonas(huecos)}\n"
            "Siguientes pasos: si las zonas grandes son aceptables, reintenta con un "
            "--agujero-max-mm mayor; si no, corrige el modelo y vuelve a hacer "
            '"castkit ingesta" con una malla nueva.'
        )

    # `use_fan=True` triangula cada hueco en abanico desde su centroide, lo
    # que permite cerrar huecos de cualquier número de aristas (por defecto,
    # `use_fan=False` solo cierra huecos de 3 o 4 aristas). En huecos no
    # convexos el abanico puede generar caras degeneradas o que se cruzan;
    # las comprobaciones posteriores de `limpiar_malla` (watertight, normales
    # consistentes, volumen positivo y distancia contra la tolerancia) vigilan
    # ese caso y rechazan el resultado si algo queda mal.
    exito = trimesh.repair.fill_holes(malla, use_fan=True)
    huecos_restantes = _detectar_huecos(malla) if not exito else []
    if not exito or huecos_restantes:
        raise LimpiezaError(
            f"La malla tiene {len(huecos)} zona(s) abierta(s) dentro del tamaño permitido, pero "
            f"{len(huecos_restantes) or len(huecos)} no se pudieron cerrar (es posible que no "
            "sean zonas simples, por ejemplo si no son convexas):\n"
            f"{_formatear_zonas(huecos_restantes or huecos)}\n"
            "Siguientes pasos: corrige el modelo y vuelve a hacer "
            '"castkit ingesta" con una malla nueva.'
        )

    return huecos


def _escalar_uniforme(malla: trimesh.Trimesh, alto_mm: float, corte_base_mm: float) -> float:
    """Escala ``malla`` (en el sitio) para que su altura sea ``alto_mm + corte_base_mm``.

    Devuelve el factor de escala aplicado. El escalado es uniforme: se
    conservan las proporciones ancho/fondo/alto.
    """
    altura_actual = float(malla.bounds[1][2] - malla.bounds[0][2])
    if not np.isfinite(altura_actual) or altura_actual <= 0:
        raise LimpiezaError("La malla no tiene altura (en Z): no se puede escalar.")

    factor = (alto_mm + corte_base_mm) / altura_actual
    malla.apply_scale(factor)
    return factor


def _escalar_uniforme_largo(malla: trimesh.Trimesh, largo_mm: float) -> float:
    """Escala ``malla`` (en el sitio, molde abierto) para que su mayor extensión horizontal
    (X o Y, tras orientarla) sea ``largo_mm``.

    Devuelve el factor de escala aplicado. El escalado es uniforme: se
    conservan las proporciones ancho/fondo/grosor.
    """
    extents = malla.bounds[1] - malla.bounds[0]
    extension_horizontal = float(max(extents[0], extents[1]))
    if not np.isfinite(extension_horizontal) or extension_horizontal <= 0:
        raise LimpiezaError("La malla no tiene extensión horizontal (en X/Y): no se puede escalar.")

    factor = largo_mm / extension_horizontal
    malla.apply_scale(factor)
    return factor


def _detectar_eje_vertical_automatico(malla: trimesh.Trimesh) -> str:
    """Elige, para molde abierto sin ``--arriba`` explícito, el eje de ``malla`` en bruto con
    menor extensión (el grosor de la figura) para ponerlo hacia +Z.

    Devuelve siempre un signo positivo (``"x"``, ``"y"`` o ``"z"``): elegir hacia qué lado
    queda "arriba" la cara bonita queda fuera de alcance (ver ticket #55) y se corrige, si
    hace falta, con ``--arriba`` explícito.
    """
    extents = malla.bounds[1] - malla.bounds[0]
    indice = int(np.argmin(extents))
    return ("x", "y", "z")[indice]


def _recortar_base(malla: trimesh.Trimesh, plano_z: float) -> trimesh.Trimesh:
    """Recorta ``malla`` por el plano horizontal ``z = plano_z`` y la traslada a z = 0.

    Usa `manifold3d` para que el corte deje la malla cerrada (con una cara
    plana nueva en el plano de corte). Lanza :class:`LimpiezaError` si
    ``malla`` no es una variedad 2D cerrada y consistente (por ejemplo, si no
    quedó watertight tras cerrar sus huecos).
    """
    import manifold3d

    malla_m3d = manifold3d.Mesh(
        vert_properties=malla.vertices.astype(np.float32),
        tri_verts=malla.faces.astype(np.uint32),
    )
    solido = manifold3d.Manifold(malla_m3d)
    if solido.status() != manifold3d.Error.NoError:
        raise LimpiezaError(
            "La malla no es un sólido cerrado y consistente (variedad 2D válida); no se pudo "
            "recortar la base. Siguientes pasos: corrige el modelo y vuelve a hacer "
            '"castkit ingesta" con una malla nueva.'
        )

    recortado = solido.trim_by_plane(normal=(0.0, 0.0, 1.0), origin_offset=plano_z)
    salida = recortado.to_mesh()
    vertices = np.array(salida.vert_properties)[:, :3]
    caras = np.array(salida.tri_verts)

    resultado = trimesh.Trimesh(vertices=vertices, faces=caras, process=False)
    resultado.vertices[:, 2] -= plano_z
    return resultado


def _filtrar_exclusiones(puntos: np.ndarray, exclusiones: list[dict[str, Any]]) -> np.ndarray:
    """Quita de ``puntos`` los que caen dentro de alguna zona de ``exclusiones``."""
    if not exclusiones or len(puntos) == 0:
        return puntos
    centros = np.array([zona["centro"] for zona in exclusiones])
    radios = np.array([zona["radio"] for zona in exclusiones]) * _MARGEN_EXCLUSION + 1e-6
    distancias = np.linalg.norm(puntos[:, None, :] - centros[None, :, :], axis=2)
    fuera_de_todas = ~(distancias <= radios[None, :]).any(axis=1)
    return puntos[fuera_de_todas]


def _distancias_a_superficie(malla: trimesh.Trimesh, puntos: np.ndarray) -> np.ndarray:
    """Distancia mínima de cada punto de ``puntos`` a la superficie de ``malla``.

    Calcula lo mismo que `trimesh.proximity.closest_point_naive` (búsqueda
    exacta por fuerza bruta, sin `rtree`: no es una dependencia del
    proyecto), pero repartiendo el trabajo en bloques en vez de construir de
    una sola vez el array de puntos × triángulos. Ese array es lo que agota
    la memoria con mallas de millones de caras (#52): con ``closest_point_naive``,
    300 puntos contra 5 M de triángulos ya llegan a varios GB.

    Primero reparte los puntos en bloques, con un tamaño tal que
    bloque_de_puntos × n_triángulos no pase de `_TOPE_PARES_DISTANCIA`. Si la
    malla sola ya supera ese tope (un único punto contra todos sus
    triángulos), también reparte los triángulos en bloques (calculando
    directamente sobre ``malla.triangles[i:j]``, sin construir una submalla)
    y se queda, para cada punto, con la distancia mínima entre todos los
    bloques de triángulos. El resultado es idéntico (± error de
    redondeo) al de `closest_point_naive`, que usa la misma rutina de
    distancia punto-triángulo (:func:`trimesh.triangles.closest_point`) sin
    trocear.
    """
    n_puntos = len(puntos)
    triangulos = malla.triangles.view(np.ndarray)
    n_triangulos = len(triangulos)
    if n_puntos == 0 or n_triangulos == 0:
        return np.zeros(n_puntos, dtype=np.float64)

    puntos = np.asarray(puntos, dtype=np.float64)
    distancias = np.empty(n_puntos, dtype=np.float64)
    puntos_por_bloque = max(1, _TOPE_PARES_DISTANCIA // n_triangulos)

    for inicio in range(0, n_puntos, puntos_por_bloque):
        fin = min(inicio + puntos_por_bloque, n_puntos)
        bloque_puntos = puntos[inicio:fin]
        n_bloque = len(bloque_puntos)
        triangulos_por_bloque = max(1, _TOPE_PARES_DISTANCIA // n_bloque)

        minimos_2 = np.full(n_bloque, np.inf, dtype=np.float64)
        for inicio_t in range(0, n_triangulos, triangulos_por_bloque):
            fin_t = min(inicio_t + triangulos_por_bloque, n_triangulos)
            bloque_tri = triangulos[inicio_t:fin_t]
            n_tri_bloque = len(bloque_tri)

            # Cada punto del bloque contra cada triángulo del bloque de
            # triángulos: como máximo _TOPE_PARES_DISTANCIA pares a la vez.
            puntos_rep = np.repeat(bloque_puntos, n_tri_bloque, axis=0)
            tri_rep = np.tile(bloque_tri, (n_bloque, 1, 1))
            cercanos = trimesh.triangles.closest_point(tri_rep, puntos_rep)
            dist_2 = ((cercanos - puntos_rep) ** 2).sum(axis=1).reshape(n_bloque, n_tri_bloque)
            minimos_2 = np.minimum(minimos_2, dist_2.min(axis=1))

        distancias[inicio:fin] = np.sqrt(minimos_2)

    return distancias


def _distancia_maxima(
    referencia: trimesh.Trimesh,
    limpia: trimesh.Trimesh,
    exclusiones: list[dict[str, Any]],
    n_muestras: int = _N_MUESTRAS_DISTANCIA,
    *,
    excluir_en_referencia: bool = True,
) -> float:
    """Distancia máxima entre las superficies de ``referencia`` y ``limpia``.

    Muestrea puntos de la superficie de cada malla y busca, para cada punto,
    el punto más cercano en la superficie de la otra (en ambos sentidos),
    con :func:`_distancias_a_superficie` (búsqueda exacta por fuerza bruta,
    repartida en bloques para no agotar la memoria con mallas grandes; ver
    #52).

    Las zonas de ``exclusiones`` (los huecos que se cerraron) se excluyen de
    los puntos muestreados en ``limpia``, para no penalizar el parche que los
    rellenó; como ``referencia`` conserva esos mismos parches (ver
    :func:`limpiar_malla`), también se excluyen allí por igual. Con
    ``excluir_en_referencia=False`` (referencia sin parches, como la malla
    original abierta) toda la superficie de ``referencia`` se mide: es
    superficie real y tiene que seguir presente en ``limpia``.
    """
    if len(referencia.faces) == 0 or len(limpia.faces) == 0:
        return 0.0

    puntos_ref, _ = trimesh.sample.sample_surface(referencia, n_muestras, seed=0)
    if excluir_en_referencia:
        puntos_ref = _filtrar_exclusiones(puntos_ref, exclusiones)
    if len(puntos_ref) == 0:
        distancia_ref = np.array([0.0])
    else:
        distancia_ref = _distancias_a_superficie(limpia, puntos_ref)

    puntos_limpia, _ = trimesh.sample.sample_surface(limpia, n_muestras, seed=0)
    puntos_limpia = _filtrar_exclusiones(puntos_limpia, exclusiones)
    if len(puntos_limpia) == 0:
        distancia_limpia = np.array([0.0])
    else:
        distancia_limpia = _distancias_a_superficie(referencia, puntos_limpia)

    return float(max(distancia_ref.max(), distancia_limpia.max()))


_SCRIPT_REMESH_BLENDER = Path(__file__).parent / "blender_scripts" / "remesh_voxel.py"


def _remallar_con_blender(malla: trimesh.Trimesh, voxel_mm: float) -> trimesh.Trimesh:
    """Ejecuta el remallado por vóxeles de Blender en modo headless sobre ``malla``."""
    import os
    import tempfile

    from castkit.blender import run_blender_script

    fd_in, ruta_in_str = tempfile.mkstemp(prefix=".castkit-remesh-in-", suffix=".stl")
    os.close(fd_in)
    fd_out, ruta_out_str = tempfile.mkstemp(prefix=".castkit-remesh-out-", suffix=".stl")
    os.close(fd_out)
    ruta_in = Path(ruta_in_str)
    ruta_out = Path(ruta_out_str)
    ruta_out.unlink(missing_ok=True)

    try:
        malla.export(ruta_in, file_type="stl")
        run_blender_script(_SCRIPT_REMESH_BLENDER, str(ruta_in), str(ruta_out), str(voxel_mm))
        if not ruta_out.is_file():
            raise LimpiezaError("Blender no generó ningún archivo STL tras el remallado.")
        remallada = trimesh.load(ruta_out, file_type="stl", force="mesh")
        trimesh.repair.fix_normals(remallada)
        return remallada
    except Exception as exc:
        if isinstance(exc, LimpiezaError):
            raise
        raise LimpiezaError(f"El remallado de respaldo con Blender falló: {exc}") from exc
    finally:
        ruta_in.unlink(missing_ok=True)
        ruta_out.unlink(missing_ok=True)


def limpiar_malla(
    carpeta: Path,
    *,
    arriba: str | None = None,
    caras_max: int,
    tolerancia_mm: float,
    corte_base_mm: float,
    agujero_max_mm: float,
    voxel_mm: float = 0.3,
    sin_blender: bool = False,
) -> dict[str, Any]:
    """Limpia y escala la malla en bruto de la versión actual del diseño en ``carpeta``.

    Ver el resumen de pasos en el docstring del módulo. Si el cierre rápido en
    Python no consigue cerrar la malla y ``sin_blender`` es ``False``, intenta
    cerrarla con el remallado por vóxeles de Blender (`voxel_mm`).

    Si ``arriba`` es ``None`` (por defecto), la orientación se elige
    automáticamente según el tipo de molde de la versión actual: en molde
    ``completo`` equivale a ``"z"`` (como siempre); en molde ``abierto`` se
    elige el eje de menor extensión de la malla en bruto (el grosor de la
    figura), con signo positivo, para tumbarla con esa cara hacia +Z (ver
    :func:`_detectar_eje_vertical_automatico`). Si se pasa ``arriba``
    explícito, manda sobre la elección automática en ambos tipos de molde.
    """
    from castkit.blender import BlenderNotFoundError, find_blender

    carpeta = Path(carpeta)
    if arriba is not None:
        arriba = validar_arriba(arriba)

    datos = cargar_diseno(carpeta)
    info_version = datos_version(datos)
    tipo_molde = info_version["tipo_molde"]

    ruta_bruto, version_bruto = _localizar_malla_bruto(carpeta, datos)
    sha_entrada = sha256_archivo(ruta_bruto)

    malla = trimesh.load(ruta_bruto, file_type="stl", force="mesh")

    arriba_automatico = False
    if arriba is not None:
        arriba_efectivo = arriba
    elif tipo_molde == "completo":
        arriba_efectivo = "z"
    else:
        arriba_efectivo = _detectar_eje_vertical_automatico(malla)
        arriba_automatico = True

    malla.apply_transform(_transformacion_arriba(arriba_efectivo))

    largo_mm: float | None = None
    grosor_mm_pedido: float | None = None
    if tipo_molde == "completo":
        alto_mm = info_version["alto_mm"]
        if alto_mm is None:
            raise LimpiezaError(
                "La versión actual no tiene un alto_mm definido: no se puede escalar la malla."
            )
        _escalar_uniforme(malla, alto_mm, corte_base_mm)
        corte_base_efectivo = corte_base_mm
    else:
        largo_mm = info_version["largo_mm"]
        if largo_mm is None:
            raise LimpiezaError(
                "La versión actual no tiene un largo_mm definido: no se puede escalar la malla."
            )
        grosor_mm_pedido = info_version["grosor_mm"]
        _escalar_uniforme_largo(malla, largo_mm)
        # corte_base_mm no se aplica en molde abierto: el corte lo decide
        # grosor_mm (ver más abajo), no un recorte fijo desde la base.
        corte_base_efectivo = 0.0

    malla_escalada_original = malla.copy()

    metodo = "python"
    huecos_cerrados: list[dict[str, Any]] = []
    distancia_remallado_mm = 0.0
    error_python: LimpiezaError | None = None

    try:
        huecos_cerrados = _cerrar_huecos(malla, agujero_max_mm)
        trimesh.repair.fix_normals(malla)
        if not malla.is_watertight:
            error_python = LimpiezaError(
                "La malla no quedó cerrada (watertight) tras rellenar sus huecos; no se puede "
                "continuar. Siguientes pasos: corrige el modelo y vuelve a hacer "
                '"castkit ingesta" con una malla nueva.'
            )
    except LimpiezaError as exc:
        error_python = exc

    if error_python is not None:
        if sin_blender:
            raise error_python
        try:
            find_blender()
        except BlenderNotFoundError as exc:
            raise LimpiezaError(
                f"{error_python} (Nota: instalando Blender LTS o configurando BLENDER_BIN "
                "se podría reintentar automáticamente con remallado por vóxeles)."
            ) from exc

        malla = _remallar_con_blender(malla_escalada_original, voxel_mm)
        metodo = "blender"
        huecos_cerrados = []
        # El remallado por vóxeles de Blender puede dejar muchísimas más
        # caras que `caras_max` (voxel_mm chico, figura grande). Se
        # simplifica aquí, antes de comprobar watertight/volumen y de medir
        # `distancia_remallado_mm`, para que esa distancia ya incluya el
        # efecto de la simplificación y el resto del flujo (huecos, corte de
        # base, medición final) trabaje sobre la malla reducida en vez de
        # sobre los millones de caras del remallado (#52). Si tras
        # simplificar no queda cerrada, se mantiene el mensaje de error de
        # siempre.
        if len(malla.faces) > caras_max:
            malla = malla.simplify_quadric_decimation(face_count=caras_max)
            trimesh.repair.fix_normals(malla)
        if not malla.is_watertight:
            raise LimpiezaError(
                "La malla tampoco quedó cerrada tras el remallado de respaldo con Blender."
            )
        if malla.volume <= 0:
            raise LimpiezaError(
                "El remallado de respaldo con Blender produjo una malla sin volumen; no se "
                "escribió ningún archivo. Siguientes pasos: corrige el modelo y vuelve a hacer "
                '"castkit ingesta" con una malla nueva.'
            )
        # La referencia de la medición final (paso 8) sale de la malla ya
        # remallada, así que aquí se mide la fidelidad contra la original:
        # sin esto, cualquier deformación de Blender pasaría con distancia 0.
        # Solo se excluyen los parches de los huecos en la malla remallada:
        # toda la superficie de la original tiene que seguir ahí.
        distancia_remallado_mm = _distancia_maxima(
            malla_escalada_original,
            malla,
            _detectar_huecos(malla_escalada_original),
            excluir_en_referencia=False,
        )
        if distancia_remallado_mm > tolerancia_mm:
            raise LimpiezaError(
                "El remallado de respaldo con Blender deformó la figura: la distancia máxima a "
                f"la malla original es {distancia_remallado_mm:.3f} mm, mayor que la tolerancia "
                f"configurada ({tolerancia_mm} mm). No se escribió ningún archivo. Siguientes "
                'pasos: corrige el modelo y vuelve a hacer "castkit ingesta" con una malla '
                "nueva."
            )

    if tipo_molde == "completo":
        plano_z = float(malla.bounds[0][2]) + corte_base_efectivo
        grosor_mm_efectivo: float | None = None
    else:
        z_min = float(malla.bounds[0][2])
        z_max = float(malla.bounds[1][2])
        grosor_total = z_max - z_min
        if grosor_mm_pedido is not None:
            if grosor_mm_pedido >= grosor_total:
                raise LimpiezaError(
                    f"--grosor-mm ({grosor_mm_pedido} mm) debe ser menor que el grosor total "
                    f"disponible de la figura escalada ({grosor_total:.3f} mm)."
                )
            grosor_mm_efectivo = grosor_mm_pedido
            plano_z = z_max - grosor_mm_efectivo
        else:
            grosor_mm_efectivo = grosor_total / 2.0
            plano_z = z_min + grosor_mm_efectivo

    zonas_excluidas = [
        {"centro": hueco["centro"] - np.array([0.0, 0.0, plano_z]), "radio": hueco["radio"]}
        for hueco in huecos_cerrados
    ]

    # Se recorta la base ANTES de simplificar (orden sugerido en el ticket):
    # `simplify_quadric_decimation` respeta el número de caras pedido, y
    # recortar después podría hacer que el resultado tuviera más caras que
    # `caras_max` (el propio corte añade caras nuevas en el plano de corte).
    malla_cortada = _recortar_base(malla, plano_z)
    # Snapshot sin simplificar: es la referencia contra la que se mide la
    # distancia máxima (ver decisión 2 del ticket).
    referencia_cortada = malla_cortada.copy()

    malla_limpia = malla_cortada
    if len(malla_limpia.faces) > caras_max:
        malla_limpia = malla_limpia.simplify_quadric_decimation(face_count=caras_max)

    trimesh.repair.fix_normals(malla_limpia)
    if not malla_limpia.is_watertight:
        raise LimpiezaError(
            "La malla limpia no quedó cerrada (watertight) tras el proceso; no se escribió "
            "ningún archivo."
        )
    if malla_limpia.volume <= 0:
        raise LimpiezaError(
            "La malla limpia tiene volumen nulo o negativo tras el proceso; no se escribió "
            "ningún archivo."
        )

    distancia_mm = _distancia_maxima(referencia_cortada, malla_limpia, zonas_excluidas)
    if metodo == "blender":
        distancia_mm = max(distancia_mm, distancia_remallado_mm)
    if distancia_mm > tolerancia_mm:
        raise LimpiezaError(
            "La malla limpia se alejó demasiado de la original: la distancia máxima medida es "
            f"{distancia_mm:.3f} mm, mayor que la tolerancia configurada ({tolerancia_mm} mm). "
            "No se escribió ningún archivo. Siguientes pasos: reintenta con un "
            "--tolerancia-mm mayor o con un --caras-max mayor."
        )

    nombre_diseno = datos["nombre"]
    version_actual = datos["version_actual"]
    nombre_salida = nombre_archivo(nombre_diseno, version_actual, "limpia", "stl")
    destino = carpeta / "work" / nombre_salida
    reemplazo = destino.is_file()

    malla_limpia.export(destino)

    parametros: dict[str, Any] = {
        "metodo": metodo,
        "caras_max": caras_max,
        "tolerancia_mm": tolerancia_mm,
        "corte_base_mm": corte_base_efectivo,
        "agujero_max_mm": agujero_max_mm,
        "arriba": arriba_efectivo,
        "arriba_automatico": arriba_automatico,
        "tipo_molde": tipo_molde,
        "largo_mm": largo_mm,
        "grosor_mm": grosor_mm_efectivo,
        "plano_corte_mm": plano_z,
        "version_bruto": version_bruto,
        "sha256": sha_entrada,
        "caras": len(malla_limpia.faces),
        "distancia_max_mm": distancia_mm,
    }
    if metodo == "blender":
        parametros["voxel_mm"] = voxel_mm

    try:
        registrar_parametros(carpeta, "limpieza", parametros)
    except Exception:
        if not reemplazo:
            destino.unlink(missing_ok=True)
        raise

    return {
        "destino": destino,
        "metodo": metodo,
        "version_bruto": version_bruto,
        "version_actual": version_actual,
        "caras": len(malla_limpia.faces),
        "distancia_mm": distancia_mm,
        "reemplazo": reemplazo,
    }
