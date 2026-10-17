"""Script que corre DENTRO de Blender: carga una malla STL y valida que tenga vértices.

No se importa desde código Python normal (requiere el módulo `bpy`, que solo existe
dentro del proceso de Blender). Se invoca como:

    blender --background --factory-startup --python load_mesh.py -- <ruta_al_stl>
"""

import sys

import bpy  # solo disponible dentro del proceso de Blender


def main() -> int:
    argv = sys.argv
    if "--" not in argv:
        print("castkit: faltan argumentos tras '--'", file=sys.stderr)
        return 1
    args = argv[argv.index("--") + 1 :]
    if not args:
        print("castkit: se requiere la ruta de un archivo STL", file=sys.stderr)
        return 1

    stl_path = args[0]

    try:
        bpy.ops.wm.stl_import(filepath=stl_path)
    except Exception as exc:  # noqa: BLE001
        print(f"castkit: error al importar {stl_path}: {exc}", file=sys.stderr)
        return 1

    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        print("castkit: no se importó ninguna malla", file=sys.stderr)
        return 1

    total_vertices = sum(len(obj.data.vertices) for obj in mesh_objects)
    if total_vertices == 0:
        print("castkit: la malla importada no tiene vértices", file=sys.stderr)
        return 1

    print(f"castkit: malla cargada, {total_vertices} vértices")
    return 0


if __name__ == "__main__":
    sys.exit(main())
