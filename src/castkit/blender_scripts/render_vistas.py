"""Script que corre DENTRO de Blender: renderiza las 4 vistas del Punto de control 1.

Se invoca en modo headless (sin interfaz gráfica) como:

    blender --background --factory-startup --python render_vistas.py -- \\
        <entrada.stl> <frente.png> <perfil.png> <arriba.png> <tres_cuartos.png>
"""

import math
import sys

import bpy  # solo disponible dentro del proceso de Blender
from mathutils import Vector


def _limpiar_escena() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _centrar_malla_en_origen(obj: bpy.types.Object) -> tuple[float, float, float]:
    """Traslada el objeto para que el centro de su bounding box quede en (0, 0, 0)."""
    bpy.context.view_layer.update()
    coords = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    zs = [c.z for c in coords]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    cz = (min(zs) + max(zs)) / 2.0
    obj.location = obj.location - Vector((cx, cy, cz))
    bpy.context.view_layer.update()
    ancho = max(xs) - min(xs)
    fondo = max(ys) - min(ys)
    alto = max(zs) - min(zs)
    return ancho, fondo, alto


def _aplicar_material_gris(obj: bpy.types.Object) -> None:
    mat = bpy.data.materials.new(name="CastkitControl1Mat")
    mat.diffuse_color = (0.78, 0.80, 0.84, 1.0)
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def _configurar_motor_render(scene: bpy.types.Scene) -> None:
    motores_disponibles = {
        item.identifier for item in scene.render.bl_rna.properties["engine"].enum_items
    }
    for candidato in ("BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        if candidato in motores_disponibles:
            scene.render.engine = candidato
            break

    scene.render.resolution_x = 600
    scene.render.resolution_y = 600
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False

    if scene.world is None:
        mundo = bpy.data.worlds.new("CastkitWorld")
        scene.world = mundo
    scene.world.color = (0.16, 0.18, 0.22)


def _crear_luces() -> None:
    orientaciones = [
        ("CastkitLuzPrincipal", (0.8, 0.2, 0.6), 3.0),
        ("CastkitLuzRelleno", (1.1, -0.4, -2.2), 1.5),
    ]
    for nombre, rotacion, energia in orientaciones:
        light_data = bpy.data.lights.new(name=nombre, type="SUN")
        light_data.energy = energia
        light_obj = bpy.data.objects.new(name=nombre, object_data=light_data)
        light_obj.rotation_euler = rotacion
        bpy.context.collection.objects.link(light_obj)


def main() -> int:
    argv = sys.argv
    if "--" not in argv:
        print("castkit: faltan argumentos tras '--'", file=sys.stderr)
        return 1
    args = argv[argv.index("--") + 1 :]
    if len(args) < 5:
        print(
            "castkit: uso: render_vistas.py -- <entrada.stl> "
            "<frente.png> <perfil.png> <arriba.png> <tres_cuartos.png>",
            file=sys.stderr,
        )
        return 1

    entrada_stl, ruta_frente, ruta_perfil, ruta_arriba, ruta_34 = args[:5]

    _limpiar_escena()

    try:
        bpy.ops.wm.stl_import(filepath=entrada_stl)
    except Exception as exc:  # noqa: BLE001
        print(f"castkit: error al importar {entrada_stl}: {exc}", file=sys.stderr)
        return 1

    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        print("castkit: no se importó ninguna malla", file=sys.stderr)
        return 1

    obj = mesh_objects[0]
    ancho, fondo, alto = _centrar_malla_en_origen(obj)
    _aplicar_material_gris(obj)

    max_dim = max(ancho, fondo, alto, 1.0)
    dist = max_dim * 3.0
    escala_orto = max_dim * 1.45

    scene = bpy.context.scene
    _configurar_motor_render(scene)
    _crear_luces()

    cam_data = bpy.data.cameras.new(name="CastkitCamara")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = escala_orto
    cam_data.clip_start = 0.1
    cam_data.clip_end = dist * 10.0
    cam_obj = bpy.data.objects.new(name="CastkitCamara", object_data=cam_data)
    bpy.context.collection.objects.link(cam_obj)
    scene.camera = cam_obj

    angulo_iso_x = math.atan2(math.sqrt(2.0), 1.0)
    vistas = [
        # frente: desde -Y mirando hacia +Y, con +Z arriba
        (ruta_frente, (0.0, -dist, 0.0), (math.pi / 2.0, 0.0, 0.0), escala_orto),
        # perfil: desde +X mirando hacia -X, con +Z arriba
        (ruta_perfil, (dist, 0.0, 0.0), (math.pi / 2.0, 0.0, math.pi / 2.0), escala_orto),
        # arriba: desde +Z mirando hacia -Z (planta)
        (ruta_arriba, (0.0, 0.0, dist), (0.0, 0.0, 0.0), escala_orto),
        # 3-4: desde (+X, -Y, +Z) mirando al origen, con +Z arriba
        (ruta_34, (dist, -dist, dist), (angulo_iso_x, 0.0, math.pi / 4.0), escala_orto * 1.15),
    ]

    for ruta_salida, ubicacion, rotacion, ortho_scale in vistas:
        cam_obj.location = ubicacion
        cam_obj.rotation_euler = rotacion
        cam_data.ortho_scale = ortho_scale
        bpy.context.view_layer.update()
        scene.render.filepath = ruta_salida
        bpy.ops.render.render(write_still=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
