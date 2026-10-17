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
   rellenó el paso 4; si supera `tolerancia_mm`, se rechaza el resultado.
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
    tamaño aproximados, y los siguientes pasos posibles.
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

    if not trimesh.repair.fill_holes(malla):
        raise LimpiezaError(
            f"La malla tiene {len(huecos)} zona(s) abierta(s) dentro del tamaño permitido "
            "pero no se pudieron cerrar (es posible que alguna no sea una zona simple, por "
            "ejemplo si no es convexa). Siguientes pasos: corrige el modelo y vuelve a hacer "
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


def _distancia_maxima(
    referencia: trimesh.Trimesh,
    limpia: trimesh.Trimesh,
    exclusiones: list[dict[str, Any]],
    n_muestras: int = _N_MUESTRAS_DISTANCIA,
) -> float:
    """Distancia máxima entre las superficies de ``referencia`` y ``limpia``.

    Muestrea puntos de la superficie de cada malla y busca, para cada punto,
    el punto más cercano en la superficie de la otra (en ambos sentidos). No
    usa `rtree` (no es una dependencia del proyecto): la búsqueda del punto
    más cercano es exacta pero fuerza bruta
    (:func:`trimesh.proximity.closest_point_naive`), razonable para los
    tamaños de malla de esta etapa.

    Las zonas de ``exclusiones`` (los huecos que se cerraron) se excluyen de
    los puntos muestreados en ``limpia``, para no penalizar el parche que los
    rellenó; como ``referencia`` conserva esos mismos parches (ver
    :func:`limpiar_malla`), también se excluyen allí por igual.
    """
    if len(referencia.faces) == 0 or len(limpia.faces) == 0:
        return 0.0

    puntos_ref, _ = trimesh.sample.sample_surface(referencia, n_muestras, seed=0)
    puntos_ref = _filtrar_exclusiones(puntos_ref, exclusiones)
    if len(puntos_ref) == 0:
        distancia_ref = np.array([0.0])
    else:
        _, distancia_ref, _ = trimesh.proximity.closest_point_naive(limpia, puntos_ref)

    puntos_limpia, _ = trimesh.sample.sample_surface(limpia, n_muestras, seed=0)
    puntos_limpia = _filtrar_exclusiones(puntos_limpia, exclusiones)
    if len(puntos_limpia) == 0:
        distancia_limpia = np.array([0.0])
    else:
        _, distancia_limpia, _ = trimesh.proximity.closest_point_naive(referencia, puntos_limpia)

    return float(max(distancia_ref.max(), distancia_limpia.max()))


def limpiar_malla(
    carpeta: Path,
    *,
    arriba: str = "z",
    caras_max: int,
    tolerancia_mm: float,
    corte_base_mm: float,
    agujero_max_mm: float,
) -> dict[str, Any]:
    """Limpia y escala la malla en bruto de la versión actual del diseño en ``carpeta``.

    Ver el resumen de pasos en el docstring del módulo. Escribe la malla
    limpia en
    ``carpeta/work/<nombre_archivo(diseno, version_actual, "limpia", "stl")>``
    y registra los parámetros usados con :func:`registrar_parametros` en la
    etapa ``"limpieza"`` de la versión actual. Todas las excepciones que
    puede lanzar son :class:`LimpiezaError`, con un mensaje en español.

    Devuelve un ``dict`` con ``destino`` (ruta de la malla limpia),
    ``version_bruto`` (versión de la que salió la malla en bruto usada),
    ``version_actual``, ``caras`` (caras de la malla limpia),
    ``distancia_mm`` (distancia máxima medida) y ``reemplazo`` (``True`` si
    ya existía una malla limpia de esta versión y se reemplazó).
    """
    carpeta = Path(carpeta)
    arriba = validar_arriba(arriba)

    datos = cargar_diseno(carpeta)
    info_version = datos_version(datos)
    alto_mm = info_version["alto_mm"]
    if alto_mm is None:
        raise LimpiezaError(
            "La versión actual no tiene un alto_mm definido: no se puede escalar la malla."
        )

    ruta_bruto, version_bruto = _localizar_malla_bruto(carpeta, datos)
    sha_entrada = sha256_archivo(ruta_bruto)

    malla = trimesh.load(ruta_bruto, file_type="stl", force="mesh")

    malla.apply_transform(_transformacion_arriba(arriba))
    _escalar_uniforme(malla, alto_mm, corte_base_mm)

    huecos_cerrados = _cerrar_huecos(malla, agujero_max_mm)
    trimesh.repair.fix_normals(malla)
    if not malla.is_watertight:
        raise LimpiezaError(
            "La malla no quedó cerrada (watertight) tras rellenar sus huecos; no se puede "
            "continuar. Siguientes pasos: corrige el modelo y vuelve a hacer "
            '"castkit ingesta" con una malla nueva.'
        )

    plano_z = float(malla.bounds[0][2]) + corte_base_mm
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
        "caras_max": caras_max,
        "tolerancia_mm": tolerancia_mm,
        "corte_base_mm": corte_base_mm,
        "agujero_max_mm": agujero_max_mm,
        "arriba": arriba,
        "version_bruto": version_bruto,
        "sha256": sha_entrada,
        "caras": len(malla_limpia.faces),
        "distancia_max_mm": distancia_mm,
    }
    try:
        registrar_parametros(carpeta, "limpieza", parametros)
    except Exception:
        if not reemplazo:
            destino.unlink(missing_ok=True)
        raise

    return {
        "destino": destino,
        "version_bruto": version_bruto,
        "version_actual": version_actual,
        "caras": len(malla_limpia.faces),
        "distancia_mm": distancia_mm,
        "reemplazo": reemplazo,
    }
