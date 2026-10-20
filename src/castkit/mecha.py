"""Canal de mecha vertical para figuras de cera (velas) — ticket #9.

Cuando el material del diseño es `cera`, perfora un canal cilíndrico vertical
pasante del diámetro configurado (`diametro_mecha_mm`) a lo largo del eje
central `(centro_x, centro_y)` de la malla limpia en `work/`, verificando que:
- el resultado siga siendo una malla cerrada (`watertight`) con volumen > 0;
- el espesor mínimo de pared lateral alrededor del canal no baje de
  `espesor_minimo_pared_mm` sin confirmación explícita del operador.

Si el material del diseño es `porcelana-fria` o `silicona`, este paso no
modifica la malla limpia. Lo mismo si el tipo de molde de la versión actual es
`abierto` (ticket #55): el canal de mecha solo tiene sentido en un molde
`completo`, así que no se perfora nada (y se avisa del motivo), sea cual sea
el material.
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

from castkit.versiones import (
    VersionError,
    cargar_diseno,
    datos_version,
    nombre_archivo,
    registrar_parametros,
)


class MechaError(VersionError):
    """Se lanza cuando no se puede aplicar el canal de mecha a la figura.

    El mensaje siempre está en español. Hereda de :class:`VersionError`.
    """


class ParedFinaMechaError(MechaError):
    """Se lanza cuando el canal de mecha deja paredes por debajo del espesor mínimo."""


def _malla_a_manifold(malla: trimesh.Trimesh) -> manifold3d.Manifold:
    mesh_gl = manifold3d.Mesh(
        vert_properties=np.asarray(malla.vertices, dtype=np.float32),
        tri_verts=np.asarray(malla.faces, dtype=np.uint32),
    )
    return manifold3d.Manifold(mesh_gl)


def _manifold_a_malla(m: manifold3d.Manifold) -> trimesh.Trimesh:
    mesh_gl = m.to_mesh()
    vertices = np.asarray(mesh_gl.vert_properties[:, :3], dtype=np.float64)
    caras = np.asarray(mesh_gl.tri_verts, dtype=np.int64)
    malla = trimesh.Trimesh(vertices=vertices, faces=caras, process=True)
    trimesh.repair.fix_normals(malla)
    return malla


def _espesor_lateral_minimo_mm(
    malla: trimesh.Trimesh, centro_x: float, centro_y: float, radio_canal: float
) -> float:
    """Estima el espesor mínimo de pared lateral entre el canal y el exterior de la figura."""
    min_xyz, max_xyz = malla.bounds
    z_min, z_max = float(min_xyz[2]), float(max_xyz[2])
    alturas = np.linspace(z_min + 0.05 * (z_max - z_min), z_max - 0.05 * (z_max - z_min), 9)

    min_espesor = float("inf")
    for z in alturas:
        seccion = malla.section(plane_origin=[centro_x, centro_y, float(z)], plane_normal=[0, 0, 1])
        if seccion is None:
            continue
        puntos_3d = seccion.vertices
        if len(puntos_3d) == 0:
            continue
        distancias_xy = np.hypot(puntos_3d[:, 0] - centro_x, puntos_3d[:, 1] - centro_y)
        radio_exterior_min = float(distancias_xy.min())
        espesor_en_z = radio_exterior_min - radio_canal
        if espesor_en_z < min_espesor:
            min_espesor = espesor_en_z

    if not math.isfinite(min_espesor):
        ancho_mitad = min(
            float(max_xyz[0] - min_xyz[0]) / 2.0,
            float(max_xyz[1] - min_xyz[1]) / 2.0,
        )
        min_espesor = ancho_mitad - radio_canal

    return float(min_espesor)


def aplicar_canal_mecha(
    carpeta: Path,
    *,
    diametro_mecha_mm: float,
    espesor_minimo_pared_mm: float,
    confirmar_pared_fina: bool = False,
) -> dict[str, Any]:
    """Aplica el canal de mecha vertical pasante si el material del diseño es `cera`."""
    if isinstance(diametro_mecha_mm, bool) or not isinstance(diametro_mecha_mm, (int, float)):
        raise MechaError(
            f'--diametro-mm debe ser un número mayor que 0; recibido "{diametro_mecha_mm}".'
        )
    if not math.isfinite(diametro_mecha_mm) or diametro_mecha_mm <= 0:
        raise MechaError(
            f'--diametro-mm debe ser un número mayor que 0; recibido "{diametro_mecha_mm}".'
        )

    if isinstance(espesor_minimo_pared_mm, bool) or not isinstance(
        espesor_minimo_pared_mm, (int, float)
    ):
        raise MechaError(
            "--espesor-minimo-mm debe ser un número mayor que 0; "
            f'recibido "{espesor_minimo_pared_mm}".'
        )
    if not math.isfinite(espesor_minimo_pared_mm) or espesor_minimo_pared_mm <= 0:
        raise MechaError(
            "--espesor-minimo-mm debe ser un número mayor que 0; "
            f'recibido "{espesor_minimo_pared_mm}".'
        )

    carpeta = Path(carpeta)
    datos = cargar_diseno(carpeta)
    nombre_diseno = datos["nombre"]
    version_actual = datos["version_actual"]
    material = datos["material"]
    tipo_molde = datos_version(datos, version_actual)["tipo_molde"]

    ruta_limpia = carpeta / "work" / nombre_archivo(nombre_diseno, version_actual, "limpia", "stl")
    if not ruta_limpia.is_file():
        raise MechaError(
            f"La versión v{version_actual} de «{nombre_diseno}» no tiene malla limpia en "
            f'"{ruta_limpia}": ejecuta primero "castkit limpieza".'
        )

    if tipo_molde == "abierto":
        motivo = "el canal de mecha solo se hace en molde completo; este diseño es molde abierto."
        registrar_parametros(
            carpeta,
            "mecha",
            {
                "aplicado": False,
                "material": material,
                "tipo_molde": tipo_molde,
                "motivo": motivo,
            },
        )
        return {
            "aplicado": False,
            "material": material,
            "tipo_molde": tipo_molde,
            "motivo": motivo,
            "destino": ruta_limpia,
        }

    if material != "cera":
        registrar_parametros(
            carpeta,
            "mecha",
            {
                "aplicado": False,
                "material": material,
            },
        )
        return {
            "aplicado": False,
            "material": material,
            "destino": ruta_limpia,
        }

    malla = trimesh.load(ruta_limpia, file_type="stl", force="mesh")
    min_xyz, max_xyz = malla.bounds
    centro_x = float((min_xyz[0] + max_xyz[0]) / 2.0)
    centro_y = float((min_xyz[1] + max_xyz[1]) / 2.0)
    z_min = float(min_xyz[2])
    z_max = float(max_xyz[2])
    alto_z = z_max - z_min

    radio_canal = float(diametro_mecha_mm) / 2.0
    espesor_min_medido = _espesor_lateral_minimo_mm(malla, centro_x, centro_y, radio_canal)

    if espesor_min_medido < espesor_minimo_pared_mm and not confirmar_pared_fina:
        raise ParedFinaMechaError(
            f"El canal de mecha de {diametro_mecha_mm} mm deja un espesor de pared de "
            f"{espesor_min_medido:.2f} mm, menor que el mínimo configurado "
            f"({espesor_minimo_pared_mm} mm)."
        )

    cilindro = trimesh.creation.cylinder(
        radius=radio_canal,
        height=alto_z + 4.0,
        sections=64,
    )
    cilindro.apply_translation((centro_x, centro_y, z_min + alto_z / 2.0))

    malla_con_canal = _manifold_a_malla(_malla_a_manifold(malla) - _malla_a_manifold(cilindro))
    if not malla_con_canal.is_watertight or malla_con_canal.volume <= 0:
        raise MechaError(
            "La malla no quedó cerrada o perdió su volumen al perforar el canal de mecha."
        )

    descriptor, tmp_str = tempfile.mkstemp(
        prefix=f".{ruta_limpia.name}.mecha-", dir=ruta_limpia.parent, suffix=".stl"
    )
    os.close(descriptor)
    tmp_path = Path(tmp_str)
    try:
        malla_con_canal.export(tmp_path, file_type="stl")
        os.replace(tmp_path, ruta_limpia)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    parametros_mecha: dict[str, Any] = {
        "aplicado": True,
        "material": material,
        "diametro_mecha_mm": float(diametro_mecha_mm),
        "espesor_minimo_pared_mm": float(espesor_minimo_pared_mm),
        "espesor_pared_medido_mm": round(espesor_min_medido, 3),
        "eje_xy_mm": [round(centro_x, 3), round(centro_y, 3)],
        "caras": len(malla_con_canal.faces),
    }
    registrar_parametros(carpeta, "mecha", parametros_mecha)

    return {
        "aplicado": True,
        "material": material,
        "destino": ruta_limpia,
        "diametro_mecha_mm": float(diametro_mecha_mm),
        "espesor_pared_medido_mm": round(espesor_min_medido, 3),
        "caras": len(malla_con_canal.faces),
    }
