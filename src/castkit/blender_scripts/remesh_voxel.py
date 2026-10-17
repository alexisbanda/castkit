"""Script que corre DENTRO de Blender: remalla por vóxeles un STL para cerrarlo.

Se invoca como:

    blender --background --factory-startup --python remesh_voxel.py -- \\
        <entrada.stl> <salida.stl> <voxel_mm>
"""

import sys

import bpy  # solo disponible dentro del proceso de Blender


def main() -> int:
    argv = sys.argv
    if "--" not in argv:
        print("castkit: faltan argumentos tras '--'", file=sys.stderr)
        return 1
    args = argv[argv.index("--") + 1 :]
    if len(args) < 3:
        print(
            "castkit: uso: remesh_voxel.py -- <entrada.stl> <salida.stl> <voxel_mm>",
            file=sys.stderr,
        )
        return 1

    entrada_stl, salida_stl, voxel_str = args[0], args[1], args[2]
    try:
        voxel_mm = float(voxel_str)
    except ValueError:
        print(f"castkit: voxel_mm inválido: {voxel_str}", file=sys.stderr)
        return 1

    # Limpiar objetos iniciales de la escena.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

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
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    remesh = obj.modifiers.new(name="CastkitVoxelRemesh", type="REMESH")
    remesh.mode = "VOXEL"
    remesh.voxel_size = voxel_mm
    remesh.use_smooth_shade = False
    bpy.ops.object.modifier_apply(modifier=remesh.name)

    tri = obj.modifiers.new(name="CastkitTriangulate", type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=tri.name)

    try:
        bpy.ops.wm.stl_export(filepath=salida_stl, export_selected_objects=True)
    except Exception as exc:  # noqa: BLE001
        print(f"castkit: error al exportar {salida_stl}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
