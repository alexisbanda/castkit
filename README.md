# castkit

Castkit — Agent-assisted pipeline that turns 3D models into production-ready mold masters and multi-cavity mold boxes for artisan casting.

## Requisitos

- [uv](https://docs.astral.sh/uv/) para gestionar el entorno y las dependencias (uv descarga automáticamente Python 3.12 si no lo tienes).
- Blender LTS en modo headless. La versión exacta requerida está fijada en [`.blender-version`](.blender-version) (por ejemplo, `5.2.2`). Los tests y scripts que lo necesitan lo invocan como subproceso.

## Instalación

```bash
uv sync
```

Esto instala Python 3.12 (si falta) y todas las dependencias del proyecto (CadQuery, trimesh, manifold3d) junto con las de desarrollo (pytest, ruff), siguiendo `pyproject.toml` y `uv.lock`.

## Tests

```bash
uv run pytest
```

Los tests marcados con `blender` ejecutan scripts dentro de Blender como subproceso y se saltan automáticamente, con un motivo visible, si no se encuentra un ejecutable de Blender disponible.

## Lint y formato

```bash
uv run ruff check
uv run ruff format --check
```

## Cómo apuntar a Blender

Castkit localiza el ejecutable de Blender en este orden:

1. La variable de entorno `BLENDER_BIN`, si está definida (debe apuntar a la ruta completa del ejecutable).
2. El comando `blender` disponible en el `PATH` del sistema.

Si no encuentra ninguno, lanza un error explicando cómo instalar Blender LTS (la versión fijada en `.blender-version`) o cómo configurar `BLENDER_BIN`.

## Integración continua

GitHub Actions ejecuta en cada pull request y en cada push a `main`, en Linux, macOS y Windows: `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check` y `uv run pytest`. Solo el job de Linux instala la versión de Blender fijada en `.blender-version` y corre los tests marcados `blender`; en macOS y Windows esos tests se saltan.
