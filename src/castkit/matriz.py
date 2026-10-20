"""Generación del STL de la matriz imprimible (modo `figura` o `cubeta`).

A partir de la malla limpia de la versión actual (`work/<diseño>_v<N>_limpia.stl`),
construye la matriz física que se mandará a la impresora 3D de filamento en
`output/<diseño>_v<N>_matriz.stl`.

Soporta dos modos:
- `figura`: la figura limpia apoyada sobre una placa base plana de espesor
  `espesor_base_mm` (con su cara inferior en `z = 0`).
- `cubeta`: una sola pieza sólida cerrada (`watertight`) que une la base plana,
  las `N` figuras según las `cavidades` de la versión actual distribuidas en
  cuadrícula (separadas por `separacion_cavidades_mm`), y una pared perimetral
  exterior de grosor `espesor_pared_mm` a distancia `margen_silicona_mm` de las
  figuras y con altura `alto_figura + margen_superior_mm` sobre la base, lista
  para verter la silicona líquida directamente encima.
"""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any

import manifold3d
import numpy as np
import trimesh

from castkit.control1 import Control1Error, verificar_control1_para_matriz
from castkit.versiones import (
    VersionError,
    cargar_diseno,
    datos_version,
    nombre_archivo,
    registrar_parametros,
)

MODOS_MATRIZ = ("figura", "cubeta")

# Pequeño solape vertical (en mm) al insertar la base de las figuras y las
# paredes dentro de la placa base para garantizar una unión booleana limpia de
# un único volumen cerrado en manifold3d.
_SOLAPE_UNION_MM = 0.05

# Margen perimetral de la placa base en modo "figura" alrededor de la figura.
_MARGEN_BASE_FIGURA_MM = 2.0


class MatrizError(VersionError):
    """Se lanza cuando no se puede generar la matriz imprimible de un diseño.

    El mensaje siempre está en español. Hereda de :class:`VersionError` (y por
    lo tanto de :class:`castkit.diseno.AltaError`).
    """


def validar_modo_matriz(modo: str) -> str:
    """Valida que ``modo`` sea `'figura'` o `'cubeta'`."""
    if modo not in MODOS_MATRIZ:
        raise MatrizError(
            f"--modo debe ser uno de los siguientes valores: {', '.join(MODOS_MATRIZ)}; "
            f'recibido "{modo}".'
        )
    return modo


def _malla_a_manifold(malla: trimesh.Trimesh) -> manifold3d.Manifold:
    """Convierte un :class:`trimesh.Trimesh` cerrado a :class:`manifold3d.Manifold`."""
    mesh_gl = manifold3d.Mesh(
        vert_properties=np.asarray(malla.vertices, dtype=np.float32),
        tri_verts=np.asarray(malla.faces, dtype=np.uint32),
    )
    return manifold3d.Manifold(mesh_gl)


def _manifold_a_malla(m: manifold3d.Manifold) -> trimesh.Trimesh:
    """Convierte un :class:`manifold3d.Manifold` a :class:`trimesh.Trimesh`."""
    mesh_gl = m.to_mesh()
    vertices = np.asarray(mesh_gl.vert_properties[:, :3], dtype=np.float64)
    caras = np.asarray(mesh_gl.tri_verts, dtype=np.int64)
    malla = trimesh.Trimesh(vertices=vertices, faces=caras, process=True)
    trimesh.repair.fix_normals(malla)
    return malla


def _caja_manifold(
    ancho_x: float,
    fondo_y: float,
    alto_z: float,
    *,
    centro_x: float = 0.0,
    centro_y: float = 0.0,
    z_min: float = 0.0,
) -> manifold3d.Manifold:
    """Crea un prisma rectangular centrado en `(centro_x, centro_y)` con base en `z_min`."""
    caja = trimesh.creation.box(extents=(ancho_x, fondo_y, alto_z))
    caja.apply_translation((centro_x, centro_y, z_min + alto_z / 2.0))
    return _malla_a_manifold(caja)


def _localizar_malla_limpia(carpeta: Path, datos: dict[str, Any]) -> Path:
    """Localiza `work/<diseño>_v<actual>_limpia.stl` o lanza :class:`MatrizError`."""
    nombre = datos["nombre"]
    actual = datos["version_actual"]
    ruta = carpeta / "work" / nombre_archivo(nombre, actual, "limpia", "stl")
    if not ruta.is_file():
        raise MatrizError(
            f'La versión v{actual} de «{nombre}» no tiene malla limpia en "{ruta}": '
            'ejecuta primero "castkit limpieza".'
        )
    return ruta


def _centrar_en_xy_y_apoyar_en_z(malla: trimesh.Trimesh, z_apoyo: float) -> trimesh.Trimesh:
    """Devuelve una copia de ``malla`` centrada en X/Y en (0, 0) y apoyada en ``z_apoyo``."""
    copia = malla.copy()
    min_xyz, max_xyz = copia.bounds
    centro_x = (min_xyz[0] + max_xyz[0]) / 2.0
    centro_y = (min_xyz[1] + max_xyz[1]) / 2.0
    copia.apply_translation((-centro_x, -centro_y, z_apoyo - min_xyz[2]))
    return copia


def generar_matriz(
    carpeta: Path,
    *,
    modo: str,
    espesor_base_mm: float,
    margen_silicona_mm: float,
    espesor_pared_mm: float,
    margen_superior_mm: float,
    separacion_cavidades_mm: float,
    exigir_control1: bool = True,
) -> dict[str, Any]:
    """Construye y guarda `output/<diseño>_v<actual>_matriz.stl` en modo `figura` o `cubeta`."""
    validar_modo_matriz(modo)

    parametros_numericos = {
        "--espesor-base-mm": espesor_base_mm,
        "--margen-silicona-mm": margen_silicona_mm,
        "--espesor-pared-mm": espesor_pared_mm,
        "--margen-superior-mm": margen_superior_mm,
        "--separacion-cavidades-mm": separacion_cavidades_mm,
    }
    for nombre_opcion, valor in parametros_numericos.items():
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise MatrizError(
                f'{nombre_opcion} debe ser un número mayor que 0; recibido "{valor}".'
            )
        if not math.isfinite(valor) or valor <= 0:
            raise MatrizError(
                f'{nombre_opcion} debe ser un número mayor que 0; recibido "{valor}".'
            )

    carpeta = Path(carpeta)
    datos = cargar_diseno(carpeta)

    nombre_diseno = datos["nombre"]
    version_actual = datos["version_actual"]
    info_version = datos_version(datos, version_actual)
    cavidades = int(info_version["cavidades"])

    ruta_limpia = _localizar_malla_limpia(carpeta, datos)
    try:
        verificar_control1_para_matriz(carpeta, datos, exigir_control1=exigir_control1)
    except Control1Error as exc:
        raise MatrizError(str(exc)) from exc
    malla_figura = trimesh.load(ruta_limpia, file_type="stl", force="mesh")

    if not malla_figura.is_watertight or malla_figura.volume <= 0:
        raise MatrizError(
            f'La malla limpia en "{ruta_limpia}" no está cerrada o no tiene volumen positivo.'
        )

    min_f, max_f = malla_figura.bounds
    ancho_fig = float(max_f[0] - min_f[0])
    fondo_fig = float(max_f[1] - min_f[1])
    alto_fig = float(max_f[2] - min_f[2])
    volumen_figura_mm3 = float(malla_figura.volume)
    volumen_figura_cm3 = round(volumen_figura_mm3 / 1000.0, 3)

    # Colocamos la figura apoyada en la cara superior de la base (con un leve
    # solape hacia abajo para garantizar unión booleana sólida).
    solape = min(_SOLAPE_UNION_MM, espesor_base_mm * 0.25)
    figura_pos = _centrar_en_xy_y_apoyar_en_z(malla_figura, espesor_base_mm - solape)
    # Estiramos ligeramente el borde inferior para compensar el solape y que la
    # cima de la figura quede exactamente en `espesor_base_mm + alto_fig`.
    figura_pos.apply_translation((0.0, 0.0, solape))
    min_fp, _ = figura_pos.bounds
    mascara_base = np.isclose(figura_pos.vertices[:, 2], min_fp[2], atol=1e-3)
    figura_pos.vertices[mascara_base, 2] -= solape

    if modo == "figura":
        ancho_total = ancho_fig + 2.0 * _MARGEN_BASE_FIGURA_MM
        fondo_total = fondo_fig + 2.0 * _MARGEN_BASE_FIGURA_MM
        base_m = _caja_manifold(ancho_total, fondo_total, espesor_base_mm, z_min=0.0)
        fig_m = _malla_a_manifold(figura_pos)
        matriz_manifold = base_m + fig_m
        cavidades_en_matriz = 1
        volumen_silicona_ml = None
    else:
        columnas = math.ceil(math.sqrt(cavidades))
        filas = math.ceil(cavidades / columnas)

        paso_x = ancho_fig + separacion_cavidades_mm
        paso_y = fondo_fig + separacion_cavidades_mm

        ancho_grupo = columnas * ancho_fig + (columnas - 1) * separacion_cavidades_mm
        fondo_grupo = filas * fondo_fig + (filas - 1) * separacion_cavidades_mm

        ancho_interior = ancho_grupo + 2.0 * margen_silicona_mm
        fondo_interior = fondo_grupo + 2.0 * margen_silicona_mm
        alto_interior = alto_fig + margen_superior_mm

        ancho_total = ancho_interior + 2.0 * espesor_pared_mm
        fondo_total = fondo_interior + 2.0 * espesor_pared_mm
        alto_total = espesor_base_mm + alto_interior

        bloque_exterior = _caja_manifold(ancho_total, fondo_total, alto_total, z_min=0.0)
        hueco_interior = _caja_manifold(
            ancho_interior,
            fondo_interior,
            alto_interior + 1.0,
            z_min=espesor_base_mm,
        )
        cubeta_manifold = bloque_exterior - hueco_interior

        x0 = -((columnas - 1) * paso_x) / 2.0
        y0 = -((filas - 1) * paso_y) / 2.0

        for idx in range(cavidades):
            fila = idx // columnas
            col = idx % columnas
            dx = x0 + col * paso_x
            dy = y0 + fila * paso_y
            copia = figura_pos.copy()
            copia.apply_translation((dx, dy, 0.0))
            cubeta_manifold = cubeta_manifold + _malla_a_manifold(copia)

        matriz_manifold = cubeta_manifold
        cavidades_en_matriz = cavidades
        volumen_hueco_mm3 = ancho_interior * fondo_interior * alto_interior
        volumen_libre_mm3 = max(0.0, volumen_hueco_mm3 - cavidades * volumen_figura_mm3)
        volumen_silicona_ml = round(volumen_libre_mm3 / 1000.0, 2)

    malla_matriz = _manifold_a_malla(matriz_manifold)
    if not malla_matriz.is_watertight or malla_matriz.volume <= 0:
        raise MatrizError("La matriz generada no quedó cerrada o no tiene volumen positivo.")

    min_m, max_m = malla_matriz.bounds
    dimensiones_mm = {
        "ancho_mm": round(float(max_m[0] - min_m[0]), 2),
        "fondo_mm": round(float(max_m[1] - min_m[1]), 2),
        "alto_total_mm": round(float(max_m[2] - min_m[2]), 2),
    }

    nombre_destino = nombre_archivo(nombre_diseno, version_actual, "matriz", "stl")
    destino = carpeta / "output" / nombre_destino
    reemplazo = destino.exists()

    descriptor, tmp_str = tempfile.mkstemp(
        prefix=f".{nombre_destino}.tmp-", dir=destino.parent, suffix=".stl"
    )
    os.close(descriptor)
    tmp_path = Path(tmp_str)
    try:
        malla_matriz.export(tmp_path, file_type="stl")
        os.replace(tmp_path, destino)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    nombre_ficha = nombre_archivo(nombre_diseno, version_actual, "ficha", "md")
    ruta_ficha = carpeta / "output" / nombre_ficha
    material = datos.get("material", "")
    advertencias = {
        "cera": "- Verificar que el canal de mecha esté despejado antes de verter la silicona.",
        "silicona": (
            "- Aplicar desmoldante obligatoriamente antes de verter silicona sobre silicona."
        ),
        "porcelana-fria": "- Considerar la contracción de secado de la porcelana fría.",
    }.get(material, "- Revisar el curado completo de la silicona antes de desmoldar.")

    linea_silicona = (
        f"- **Silicona líquida estimada:** ~{volumen_silicona_ml:.1f} ml\n"
        if volumen_silicona_ml is not None
        else ""
    )
    tipo_molde = info_version["tipo_molde"]
    if tipo_molde == "abierto":
        linea_tipo_y_medidas = (
            f"- **Tipo de molde:** `abierto`\n"
            f"- **Largo de la figura:** {info_version['largo_mm']} mm\n"
            f"- **Grosor de la figura:** {info_version['grosor_mm']} mm\n"
        )
    else:
        linea_tipo_y_medidas = (
            f"- **Tipo de molde:** `completo`\n"
            f"- **Altura objetivo de figura:** {info_version['alto_mm']} mm\n"
        )
    contenido_ficha = (
        f"# Ficha técnica: {nombre_diseno} (v{version_actual})\n\n"
        f"- **Material final de la figura:** `{material}`\n"
        f"- **Modo de matriz impresa:** `{modo}`\n"
        f"- **Cavidades en la matriz:** {cavidades_en_matriz}\n"
        f"{linea_tipo_y_medidas}"
        f"- **Dimensiones exteriores de la matriz:** "
        f"{dimensiones_mm['ancho_mm']} × {dimensiones_mm['fondo_mm']} × "
        f"{dimensiones_mm['alto_total_mm']} mm\n"
        f"- **Volumen por figura:** {volumen_figura_cm3} cm³\n"
        f"{linea_silicona}"
        f"- **Archivo STL para impresora 3D:** `{nombre_destino}`\n\n"
        f"## Advertencias de taller\n\n"
        f"{advertencias}\n"
    )
    ruta_ficha.write_text(contenido_ficha, encoding="utf-8")

    parametros_registro: dict[str, Any] = {
        "modo": modo,
        "cavidades": cavidades_en_matriz,
        "espesor_base_mm": espesor_base_mm,
        "margen_silicona_mm": margen_silicona_mm,
        "espesor_pared_mm": espesor_pared_mm,
        "margen_superior_mm": margen_superior_mm,
        "separacion_cavidades_mm": separacion_cavidades_mm,
        "dimensiones_mm": dimensiones_mm,
        "volumen_figura_cm3": volumen_figura_cm3,
        "volumen_silicona_ml": volumen_silicona_ml,
        "ficha": nombre_ficha,
        "caras": len(malla_matriz.faces),
    }
    registrar_parametros(carpeta, "matriz", parametros_registro)

    return {
        "destino": destino,
        "ficha": ruta_ficha,
        "modo": modo,
        "cavidades": cavidades_en_matriz,
        "dimensiones_mm": dimensiones_mm,
        "volumen_figura_cm3": volumen_figura_cm3,
        "volumen_silicona_ml": volumen_silicona_ml,
        "caras": len(malla_matriz.faces),
        "reemplazo": reemplazo,
    }
