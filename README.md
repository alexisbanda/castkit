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

## Dar de alta un diseño

`castkit alta` crea la carpeta estándar de un diseño nuevo a partir de su brief:

```bash
uv run castkit alta guagua-pan \
  --brief ~/Descargas/brief-guagua \
  --material porcelana-fria \
  --alto-mm 80 \
  --cavidades 4 \
  --cantidad 48 \
  --fecha 2026-11-01 \
  --licencia propio \
  --origen "escultura propia en Nomad"
```

Esto crea `guagua-pan/` con:

- `brief/`: copia completa de la carpeta indicada en `--brief`.
- `input/`, `work/`, `output/`: carpetas de trabajo, vacías.
- `CHANGELOG.md`: historial del diseño; la primera entrada registra la creación de la v1.
- `diseno.json`: metadatos del diseño y de cada versión (lo usará el alta de nuevas versiones).

### Opciones

| Opción | Obligatoria | Valores aceptados |
| --- | --- | --- |
| `nombre` (posicional) | sí | minúsculas, números y guiones, sin tildes ni espacios (p. ej. `guagua-pan`) |
| `--brief` | sí | carpeta existente y no vacía |
| `--material` | sí | `porcelana-fria`, `silicona`, `cera` |
| `--alto-mm` | sí | número mayor que 0 |
| `--cavidades` | sí | entero mayor o igual a 1 |
| `--cantidad` | sí | entero mayor o igual a 1 |
| `--fecha` | sí | fecha de entrega, formato `AAAA-MM-DD` |
| `--licencia` | sí | `propio`, `comercial` |
| `--origen` | sí | texto libre: de dónde viene el modelo base |

Si el nombre no cumple el formato (mayúsculas, espacios, tildes, etc.), el alta se rechaza y
propone un nombre corregido; por ejemplo, «Guagua de Pan» se corrige a `guagua-de-pan`.

Si falta alguna opción obligatoria y la terminal es interactiva, `castkit` la pregunta; si no
es interactiva, el alta falla y lista todas las opciones que faltan. Si el diseño ya existe, no
se modifica nada y se avisa del conflicto.

### Dónde se crea el diseño

Por defecto, el diseño se crea en la carpeta actual. Si la variable de entorno
`CASTKIT_DISENOS` está definida, se crea dentro de esa carpeta (debe existir de antemano):

```bash
export CASTKIT_DISENOS=/datos/disenos
uv run castkit alta guagua-pan --brief ... # se crea en /datos/disenos/guagua-pan
```

## Integración continua

GitHub Actions ejecuta en cada pull request y en cada push a `main`, en Linux, macOS y Windows: `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check` y `uv run pytest`. Solo el job de Linux instala la versión de Blender fijada en `.blender-version` y corre los tests marcados `blender`; en macOS y Windows esos tests se saltan.
