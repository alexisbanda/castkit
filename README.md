# castkit

Castkit — Agent-assisted pipeline that turns 3D models into production-ready mold masters and multi-cavity mold boxes for artisan casting.

## Requisitos

- [uv](https://docs.astral.sh/uv/) para gestionar el entorno y las dependencias (uv descarga automáticamente Python 3.12 si no lo tienes).
- Blender LTS en modo headless. La versión exacta requerida está fijada en [`.blender-version`](.blender-version) (por ejemplo, `5.2.2`). Los tests y scripts que lo necesitan lo invocan como subproceso.

## Instalación

```bash
uv sync
```

Esto instala Python 3.12 (si falta) y todas las dependencias del proyecto (CadQuery, trimesh, manifold3d, fast-simplification, networkx) junto con las de desarrollo (pytest, ruff), siguiendo `pyproject.toml` y `uv.lock`.

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

## Crear una nueva versión de un diseño

`castkit version` crea la siguiente versión de un diseño ya existente, a partir de la versión
actual: parte de sus parámetros, hereda el alto y las cavidades salvo que indiques nuevos valores,
y deja constancia del cambio en `CHANGELOG.md`. La versión anterior no se modifica.

```bash
uv run castkit version guagua-pan --motivo "aumentar altura a 90 mm" --alto-mm 90
```

Esto, si `guagua-pan` está en `v1`, deja `version_actual: 2` en `diseno.json` y añade a
`versiones` una entrada `v2` con la fecha de creación, el motivo, el alto y las cavidades
efectivos (el valor indicado por opción, o el de la versión anterior si no se indicó) y una copia
independiente de los parámetros de `v1` (modificarlos en `v2` no afecta a `v1`). También agrega a
`CHANGELOG.md` una entrada `## v2 — AAAA-MM-DD` con el motivo y, si cambiaron, los valores de alto
o cavidades. No se toca `brief/`, `input/`, `work/`, `output/` ni ninguna versión anterior.

### Opciones

| Opción | Obligatoria | Valores aceptados |
| --- | --- | --- |
| `nombre` (posicional) | sí | nombre de un diseño ya dado de alta |
| `--motivo` | sí | texto libre: qué cambió en esta versión |
| `--alto-mm` | no | número mayor que 0 (si se omite, se copia el de la versión actual) |
| `--cavidades` | no | entero mayor o igual a 1 (si se omite, se copia el de la versión actual) |

Un `--alto-mm` o `--cavidades` inválido se rechaza con el mismo mensaje que usa `castkit alta`, y
no modifica ni `diseno.json` ni `CHANGELOG.md`. Si falta `--motivo` y la terminal es interactiva,
`castkit` lo pregunta; si no es interactiva, falla pidiendo la opción. Un motivo vacío o de solo
espacios nunca crea la versión. Si el diseño no existe (o el nombre no es válido), se avisa del
error y no se pregunta nada.

### Regla para las etapas del pipeline

A partir del ticket #6, cada etapa del pipeline (limpieza, impresión, etc.) sigue dos
convenciones de `castkit.versiones` para que cada archivo y cada parámetro queden asociados a la
versión que los produjo:

- **Nombrar archivos** con `nombre_archivo(diseno, version, pieza, ext)`, que genera
  `<diseño>_v<versión>_<pieza>.<ext>` (p. ej. `nombre_archivo("guagua-pan", 2, "master", "stl")` →
  `guagua-pan_v2_master.stl`).
- **Registrar los parámetros usados** con `registrar_parametros(carpeta, etapa, parametros)`, que
  guarda `parametros` en `versiones.v<actual>.parametros.<etapa>` del `diseno.json`, sin tocar las
  demás etapas ni otras versiones.

## Entregar la malla en bruto

La malla en bruto (de imagen→3D, escaneo o escultura) se obtiene fuera de castkit.
`castkit ingesta` la incorpora a la versión actual del diseño:

```bash
uv run castkit ingesta guagua-pan ~/Descargas/guagua.stl
```

Esto, si `guagua-pan` está en `v1`, valida que `~/Descargas/guagua.stl` sea un STL legible
(con al menos una cara; no necesita estar cerrado) y lo copia a
`guagua-pan/input/guagua-pan_v1_bruto.stl` (nombre generado con `nombre_archivo`). Además
registra, con `registrar_parametros` en `versiones.v1.parametros.ingesta`, el nombre original
del archivo, su huella `sha256` y sus `caras` y `vertices`.

Se rechazan, con un mensaje en español y sin copiar ni registrar nada: archivos que no sean
`.stl` (sin distinguir mayúsculas, por ejemplo `.obj`), archivos vacíos, archivos que no se
puedan leer como STL (dañados) y STL sin ninguna cara.

### Opciones

| Opción | Obligatoria | Valores aceptados |
| --- | --- | --- |
| `nombre` (posicional) | sí | nombre de un diseño ya dado de alta |
| `archivo` (posicional) | sí | ruta a un archivo `.stl` existente y legible |
| `--reemplazar` | no | reemplaza sin preguntar la malla ya guardada en la versión actual, si existe |

Si la versión actual ya tiene una malla guardada y no se pasa `--reemplazar`: en una terminal
interactiva se pregunta «¿Reemplazarla? (s/N)» (solo «s», «si» o «sí» reemplazan; cualquier otra
respuesta no modifica nada); sin terminal, falla indicando que hay que usar `--reemplazar`.

## Limpiar y escalar la malla automáticamente

`castkit limpieza` toma la malla en bruto de la versión actual y deja, en `work/`, una malla
cerrada (watertight), escalada al `alto_mm` de la versión y con la base recortada y plana en
z = 0, lista para las siguientes etapas de fabricación:

```bash
uv run castkit limpieza guagua-pan
```

Esto, si `guagua-pan` está en `v1`:

1. Busca la malla en bruto más reciente (la de `v1`; si no la tiene, la de la versión anterior
   más reciente que sí la tenga, avisando en pantalla de cuál se usó).
2. La orienta según `--arriba` (qué eje debe quedar hacia +Z antes de escalar).
3. La escala de forma uniforme (conservando las proporciones) para que su altura sea
   `alto_mm + corte_base_mm`.
4. Cierra sus zonas abiertas (huecos), siempre que ninguna supere `agujero_max_mm` de
   perímetro; si alguna lo supera, no cierra nada y falla indicando cuántas zonas hay, su
   posición y tamaño aproximados (en mm) y los siguientes pasos posibles.
5. Recorta `corte_base_mm` desde la base (dejando la malla cerrada) y la traslada para que la
   base quede exactamente en z = 0.
6. La simplifica a `caras_max` caras como máximo (si ya tiene menos, no la toca).
7. Arregla las normales y comprueba que el resultado esté cerrado y tenga volumen positivo.
8. Mide la distancia máxima contra la malla original (orientada, escalada y recortada); si
   supera `tolerancia_mm`, rechaza el resultado sin escribir ningún archivo.

Si todo sale bien, escribe `guagua-pan/work/guagua-pan_v1_limpia.stl` (nombre generado con
`nombre_archivo`) y registra, con `registrar_parametros` en `versiones.v1.parametros.limpieza`:
`caras_max`, `tolerancia_mm`, `corte_base_mm`, `agujero_max_mm`, `arriba`, la versión de la que
salió la malla en bruto (`version_bruto`), su huella `sha256`, las caras resultantes (`caras`) y
la distancia máxima medida (`distancia_max_mm`). Volver a limpiar la misma versión reemplaza el
archivo y el registro, avisando en pantalla.

### Opciones

| Opción | Obligatoria | Valores aceptados |
| --- | --- | --- |
| `nombre` (posicional) | sí | nombre de un diseño ya dado de alta |
| `--arriba` | no | `x`, `-x`, `y`, `-y`, `z` (por defecto), `-z`: eje que debe quedar hacia +Z |
| `--caras-max` | no | entero mayor o igual a 1 (si se omite, se usa `config/fabricacion.toml`) |
| `--tolerancia-mm` | no | número mayor que 0 (si se omite, se usa `config/fabricacion.toml`) |
| `--corte-base-mm` | no | número mayor que 0 (si se omite, se usa `config/fabricacion.toml`) |
| `--agujero-max-mm` | no | número mayor que 0 (si se omite, se usa `config/fabricacion.toml`) |

Un valor inválido en cualquiera de estas opciones se rechaza con un mensaje en español que
nombra la opción.

### `config/fabricacion.toml`

Los valores de partida de `caras_max`, `tolerancia_mm`, `corte_base_mm` y `agujero_max_mm` viven,
versionados en el repositorio, en la sección `[limpieza]` de `config/fabricacion.toml`:

```toml
[limpieza]
caras_max = 200000
tolerancia_mm = 0.2
corte_base_mm = 0.5
agujero_max_mm = 10
```

Cada opción de `castkit limpieza` sobrescribe, una a una, el valor correspondiente de aquí; las
demás se siguen leyendo de la configuración.

## Generar la malla en bruto desde el brief (con proveedor enchufable)

Cuando el brief contiene imágenes de referencia o generadas con IA (`.png`, `.jpg`, `.jpeg`,
`.webp`), documentos o archivos de diseño (`.pdf`, `.ai`), texto o prompt (`.txt`, `.md`) o
incluso un `.stl` opcional, `castkit generar` obtiene o genera automáticamente la malla en bruto:

```bash
uv run castkit generar guagua-pan --prompt "guagua de pan tradicional sin socavados"
```

- Si en `guagua-pan/brief/` ya hay un `.stl` y no se fuerza `--proveedor`, lo valida y lo guarda
  directamente en `guagua-pan/input/guagua-pan_v1_bruto.stl` (`proveedor: "stl-directo"`).
- Si no hay `.stl`, invoca al **proveedor enchufable** configurado en `[generacion]` de
  `config/fabricacion.toml` (o el indicado con `--proveedor`), valida que el archivo devuelto sea
  un STL legible y lo guarda en `input/` registrando el proveedor, los archivos de `brief/`
  utilizados, el prompt, `sha256`, `caras` y `vertices` en `versiones.v<actual>.parametros.generacion`.

### Opciones de `castkit generar`

| Opción | Obligatoria | Valores aceptados |
| --- | --- | --- |
| `nombre` (posicional) | sí | nombre de un diseño ya dado de alta |
| `--proveedor` | no | nombre del proveedor enchufable registrado o configurado en `config/fabricacion.toml` |
| `--prompt` | no | texto o prompt de contexto adicional para el generador 3D |
| `--reemplazar` | no | reemplaza sin preguntar la malla ya guardada en la versión actual, si existe |

## Punto de control 1: renders de las 4 vistas y aprobación (`castkit control1`)

Antes de imprimir o pasar a la caja de molde (`castkit matriz`), `castkit control1` genera sin abrir interfaz gráfica cuatro imágenes PNG de la malla limpia de la versión actual (`frente`, `perfil`, `arriba` y `3-4`) en `output/`, con las medidas finales de **alto, ancho y fondo en mm** sobreimpresas de forma legible:

```bash
# Generar las 4 vistas y aprobar la versión actual
uv run castkit control1 guagua-pan --aprobar

# Rechazar la versión actual registrando el motivo (bloquea el paso a la matriz)
uv run castkit control1 guagua-pan --rechazar --motivo "la cara quedó aplastada"
```

Los cuatro archivos se guardan en `output/` nombrados con la versión actual:
- `guagua-pan_v1_render-frente.png`
- `guagua-pan_v1_render-perfil.png`
- `guagua-pan_v1_render-arriba.png`
- `guagua-pan_v1_render-3-4.png`

La decisión (`aprobado`, `rechazado` con motivo obligatorio, o `pendiente`) y la fecha quedan registradas en `versiones.v1.parametros.control_1` dentro de `diseno.json`. Mientras el punto de control 1 no se haya ejecutado, o esté pendiente o rechazado, `castkit matriz` rechaza generar la matriz e informa del motivo. `castkit flujo` también se detiene en este punto: en una terminal interactiva pregunta si apruebas la figura, y en scripts o CI solo avanza a la matriz si se le pasa `--aprobar-control1`.

### Opciones de `castkit control1`

| Opción | Obligatoria | Valores aceptados |
| --- | --- | --- |
| `nombre` (posicional) | sí | nombre de un diseño ya dado de alta y con malla limpia |
| `--aprobar` | no | aprueba el punto de control 1 y habilita el paso a `castkit matriz` |
| `--rechazar` | no | rechaza el punto de control 1 y deja el diseño sin avanzar |
| `--motivo` | sí (si se usa `--rechazar`) | texto libre explicando qué corregir |
| `--sin-blender` | no | genera las 4 vistas PNG con el renderizador de Python sin invocar a Blender |

## Generar la matriz imprimible (`.stl`) en modo `figura` o `cubeta`

Una vez limpia la malla (`work/<diseño>_v<actual>_limpia.stl`), `castkit matriz` construye el
archivo `.stl` final de la matriz para mandarlo a la impresora 3D de filamento:

```bash
# Modo figura: solo la figura apoyada sobre su base plana
uv run castkit matriz guagua-pan --modo figura

# Modo cubeta: una sola pieza con base + 1 o N figuras (según cavidades) + pared alrededor
# lista para verter la silicona líquida directamente
uv run castkit matriz guagua-pan --modo cubeta
```

Si no se indica `--modo` y la terminal es interactiva, `castkit matriz` pregunta antes de crear:
`¿Qué tipo de matriz deseas crear (figura, cubeta)?`.

El resultado se guarda en `guagua-pan/output/guagua-pan_v1_matriz.stl` (cerrado y apoyado en
`z = 0`) y registra en `versiones.v1.parametros.matriz` el modo elegido, las cavidades, los
parámetros geométricos en mm, las dimensiones exteriores, el volumen de una figura en cm³ y (en
modo `cubeta`) los mililitros estimados de silicona.

### Opciones de `castkit matriz`

| Opción | Obligatoria | Valores aceptados |
| --- | --- | --- |
| `nombre` (posicional) | sí | nombre de un diseño ya dado de alta y con malla limpia |
| `--modo` | sí (se pregunta si hay terminal) | `figura`, `cubeta` |
| `--espesor-base-mm` | no | número mayor que 0 (por defecto `3.0` en `config/fabricacion.toml`) |
| `--margen-silicona-mm` | no | número mayor que 0 (por defecto `10.0` en `config/fabricacion.toml`) |
| `--espesor-pared-mm` | no | número mayor que 0 (por defecto `3.0` en `config/fabricacion.toml`) |
| `--margen-superior-mm` | no | número mayor que 0 (por defecto `10.0` en `config/fabricacion.toml`) |
| `--separacion-cavidades-mm` | no | número mayor que 0 (por defecto `10.0` en `config/fabricacion.toml`) |

## Integración continua

GitHub Actions ejecuta en cada pull request y en cada push a `main`, en Linux, macOS y Windows: `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check` y `uv run pytest`. Solo el job de Linux instala la versión de Blender fijada en `.blender-version` y corre los tests marcados `blender`; en macOS y Windows esos tests se saltan.
