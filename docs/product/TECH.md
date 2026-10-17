# Brief técnico: Castkit

> **Lo escribe el tech lead.** Ticket Studio solo deja esta plantilla y, en «Decisiones pendientes», las preguntas técnicas que salen del brief de producto. No propone stack ni arquitectura. El ticket 0 de cimientos depende de este documento. Los encabezados `##` son fijos: `brief_check.py` los comprueba.
>
> Rellenado en modo arranque exprés con las respuestas del tech lead (alexisbanda).

## Stack

- Python 3.12, con entorno y dependencias gestionados por uv (`pyproject.toml` + `uv.lock`).
- CadQuery para el modelado paramétrico de la caja de molde.
- trimesh y manifold3d para el procesamiento y la validación de mallas.
- Blender LTS en modo headless (scripts `bpy`) para la limpieza avanzada de mallas y los renders, invocado como subproceso (`blender --background --python …`); el ejecutable se toma de `BLENDER_BIN` o del PATH.
- Sin base de datos: los diseños viven como carpetas con archivos.

## Arquitectura

Pipeline por etapas, cada una invocable por separado desde la línea de comandos y encadenadas por comandos/skills del agente de código que guían al operador y paran en los puntos de control:

1. Proyecto de diseño: alta desde el brief y estructura de carpetas (`brief/`, `input/`, `work/`, `output/`, `CHANGELOG.md`).
2. Preparación de la malla (trimesh/manifold3d y Blender headless) → master + render del punto de control 1.
3. Caja de molde (CadQuery), de una pieza y multicavidad.
4. Validación automática → informe legible + renders del punto de control 2.
5. Empaquetado y entrega en la carpeta del pedido.

Las reglas por material y los parámetros de fabricación viven en configuración versionada, separada del código, para poder ajustarlos y extenderlos sin reescribir etapas.

## Datos

- Cada diseño es una carpeta con su brief, la malla en bruto, intermedios y salidas por versión (p. ej. `guagua_v2_master.stl`).
- Los parámetros usados en cada versión se registran junto a ella para poder reproducirla.
- Se registra el origen y la licencia de cada modelo base.
- No hay datos personales de clientes.

## Integraciones externas

- Google Drive: carpeta compartida por pedido donde se publica el paquete.
- WhatsApp: solo se redacta el texto del aviso al taller; no hay integración.
- Herramientas imagen→3D: fuera del MVP; el pipeline solo recibe su salida.

## Cómo se prueba

- `uv run pytest` con mallas de referencia pequeñas en `tests/fixtures/`. Los tests que necesitan Blender llevan la marca `blender` y se saltan si no está disponible.
- `ruff` para lint y formato.
- GitHub Actions ejecuta lint y tests en cada PR; solo el job de Linux instala Blender y corre los tests marcados `blender`.

## Entornos y despliegue

- Desarrollo y uso local en los equipos del estudio: Linux, macOS y Windows.
- CI en GitHub Actions con matriz de los tres sistemas operativos (Blender solo en Linux).
- No hay despliegue a servidores: la herramienta se instala en los equipos del estudio.

## Decisiones pendientes

- [x] ¿Qué gestor de entorno y dependencias se usa? Decidido: uv.
- [x] ¿Qué versión de Blender se fija y cómo se ejecuta en la CI? Decidido: LTS vigente como subproceso; en CI solo en Linux.
- [ ] Google Drive: ¿publicación mediante la API o mediante la carpeta sincronizada en el equipo? Si es la API, ¿cómo se guardan las credenciales sin exponerlas?
- [ ] ¿En qué formato se genera la ficha técnica (Markdown, PDF o ambos) y con qué herramienta?
- [ ] ¿Cómo se distinguen en la configuración los parámetros por tecnología de impresión (resina MSLA y filamento FDM)?
