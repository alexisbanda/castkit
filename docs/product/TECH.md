# Brief técnico: Castkit

> **Lo escribe el tech lead.** Ticket Studio solo deja esta plantilla y, en «Decisiones pendientes», las preguntas técnicas que salen del brief de producto. No propone stack ni arquitectura. El ticket 0 de cimientos depende de este documento. Los encabezados `##` son fijos: `brief_check.py` los comprueba.
>
> Rellenado en modo arranque exprés con las respuestas del tech lead (alexisbanda).

## Stack

- Python 3.12, con entorno y dependencias gestionados por uv (`pyproject.toml` + `uv.lock`).
- manifold3d para la generación geométrica de la matriz final (modo `figura` o modo `cubeta` multicavidad con borde exterior).
- trimesh, manifold3d y fast-simplification para el procesamiento, limpieza y validación de mallas.
- Blender LTS en modo headless (scripts `bpy`), **obligatorio para los puntos de control**: renderiza las 4 vistas del punto de control 1 con el motor `CYCLES` en CPU y también se usa como remallado de respaldo en `castkit limpieza`, invocado como subproceso (`blender --background --python …`); el ejecutable se toma de `BLENDER_BIN` o del PATH. No existe ningún motor de respaldo para los renders: si Blender falla o no está disponible, el punto de control falla con un error explícito.
- Arquitectura de **proveedor enchufable** (protocolo/interfaz en Python configurable en `config/fabricacion.toml`) para la generación de mallas 3D a partir de imágenes, documentos (`.pdf`, `.ai`) o prompts de texto.
- scikit-image (BSD-3) para los proveedores locales `relieve` (bajorrelieve por capas de color) y `profundidad` (media figura): trazan los contornos con `skimage.measure.find_contours`.
- torch y transformers (extra opcional `profundidad`, no instalado por defecto) para el proveedor local `profundidad`: mapa de profundidad en CPU con Depth Anything V2 Small (Apache-2.0; las variantes Base/Large son CC-BY-NC y se rechazan).
- Sin base de datos: los diseños viven como carpetas con archivos.

## Arquitectura

Pipeline por etapas, cada una invocable por separado desde la línea de comandos y encadenadas por comandos/skills del agente de código que guían al operador y paran en los puntos de control:

1. Proyecto de diseño: alta desde el brief (con entradas flexibles: imágenes, texto/prompt, `.pdf`, `.ai` o `.stl` opcional) y estructura de carpetas (`brief/`, `input/`, `work/`, `output/`, `CHANGELOG.md`, `diseno.json`).
2. Obtención y preparación de la malla: generación de malla en bruto mediante proveedor enchufable (desde imagen, documento o texto) o ingesta directa de `.stl`, seguida de limpieza, escalado a mm, aplanado de base y operaciones por material → figura limpia + render del punto de control 1.
3. Generación de la matriz imprimible (`.stl`): pregunta interactivamente (o recibe por opción `--modo`) si generar en modo `figura` (solo la figura sobre su base plana) o en modo `cubeta` (una sola pieza con base, distribución de 1 o N cavidades y pared/borde perimetral para verter la silicona líquida directamente).
4. Validación automática → informe legible (con volúmenes de figura y de silicona) + renders del punto de control 2.
5. Empaquetado y entrega en la carpeta del pedido.

Las reglas por material, la selección del proveedor de generación 3D y los parámetros de fabricación viven en configuración versionada (`config/fabricacion.toml` en un clon de desarrollo; instalado con `uv tool install`, la copia que castkit crea sola en la carpeta de configuración del usuario, ver README), separada del código, para poder ajustarlos y extenderlos sin reescribir etapas.

## Datos

- Cada diseño es una carpeta con su brief (imágenes, textos/prompts, `.pdf`, `.ai` o `.stl`), la malla en bruto, intermedios y salidas por versión (p. ej. `guagua-pan_v2_matriz.stl`).
- Los parámetros usados en cada versión y etapa se registran en `diseno.json` junto a ella para poder reproducirla.
- Se registra el origen y la licencia de cada modelo base.
- No hay datos personales de clientes.

## Integraciones externas

- Proveedores de generación 3D (Imagen/Texto → 3D): integrados detrás de una interfaz enchufable; las credenciales de API (cuando el proveedor las requiera) se leen de variables de entorno sin guardarse en el repositorio.
- Google Drive: carpeta compartida por pedido donde se publica el paquete.
- WhatsApp: solo se redacta el texto del aviso al taller; no hay integración.

## Cómo se prueba

- `uv run pytest` con mallas de referencia pequeñas en `tests/fixtures/` y dobles de prueba (fakes/mocks) para el proveedor enchufable de generación 3D. Los tests que necesitan Blender llevan la marca `blender` y se saltan si no está disponible.
- `ruff` para lint y formato.
- GitHub Actions ejecuta lint y tests en cada PR; solo el job de Linux instala Blender y corre los tests marcados `blender`.

## Entornos y despliegue

- Desarrollo y uso local en los equipos del estudio: Linux, macOS y Windows.
- CI en GitHub Actions con matriz de los tres sistemas operativos (Blender solo en Linux).
- No hay despliegue a servidores: la herramienta se instala en los equipos del estudio.

## Decisiones pendientes

- [x] ¿Qué gestor de entorno y dependencias se usa? Decidido: uv.
- [x] ¿Qué versión de Blender se fija y cómo se ejecuta en la CI? Decidido: LTS vigente como subproceso; en CI solo en Linux.
- [x] ¿Cómo se integran los generadores de Imagen/Texto → 3D? Decidido: proveedor enchufable configurable, sin atarse a un único servicio.
- [x] ¿Qué entrega la etapa de matriz para la impresora 3D? Decidido: un `.stl` de la matriz permitiendo elegir entre modo `figura` (figura + base) y modo `cubeta` (base + 1 o N figuras + borde exterior para verter silicona).
- [x] ¿Cuál será el primer proveedor enchufable concreto de Imagen/Texto → 3D a implementar tras el adaptador base? Decidido: Tripo3D vía su API v3 (#36); además existe el reconstructor local `imagen-3d`.
- [x] ¿En qué formato se genera la ficha técnica y con qué herramienta? Decidido: Markdown, escrita en `output/<diseño>_v<n>_ficha.md` por `castkit matriz`.
- [ ] Google Drive: ¿publicación mediante la API o mediante la carpeta sincronizada en el equipo? Si es la API, ¿cómo se guardan las credenciales sin exponerlas?
- [ ] ¿Cómo se distinguen en la configuración los parámetros por tecnología de impresión (resina MSLA y filamento FDM)? Ver #29.
