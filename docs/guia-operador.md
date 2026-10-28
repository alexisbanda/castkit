# Guía del operador: un pedido paso a paso

Esta guía te lleva por un pedido completo, de instalar castkit a tener la matriz lista para
imprimir, usando como ejemplo un pedido real: el toro futbolista (media figura, molde abierto,
en silicona). No necesitas saber de repositorios ni de la terminal más allá de copiar y pegar
los comandos de aquí; sí necesitas saber de matrices, moldes y medidas, que es lo tuyo.

Para el detalle de cada opción de cada comando (todas las que acepta, con sus valores posibles),
esta guía enlaza al [`README.md`](../README.md), que es la referencia completa. Aquí solo vas a
encontrar lo que necesitas para sacar un pedido adelante.

## 1. Instalar y comprobar

Si todavía no tienes castkit instalado, instálalo como herramienta con
[uv](https://docs.astral.sh/uv/) (una sola vez; no hace falta clonar nada):

```bash
uv tool install 'castkit @ git+ssh://git@github.com/alexisbanda/castkit.git'
```

El pedido del toro usa el proveedor `profundidad` (media figura desde una foto o dibujo
frontal), que necesita un paquete adicional. Si vas a trabajar con medias figuras, instala con
ese extra desde el principio:

```bash
uv tool install 'castkit[profundidad] @ git+ssh://git@github.com/alexisbanda/castkit.git'
```

Esto deja el comando `castkit` disponible directamente (sin nada delante). Para actualizarlo más
adelante: `uv tool upgrade castkit`.

Después de instalar, comprueba que todo quedó en orden con:

```bash
castkit comprobar
```

Debería verse algo así (las rutas de tu equipo serán distintas):

![Salida de «castkit comprobar»: Blender encontrado y su versión, el extra «profundidad» instalado, la configuración de fabricación válida, la carpeta de diseños y si la clave de Tripo está definida](img/comprobar.png)

Si alguna línea avisa de un problema (Blender no encontrado, extra `profundidad` que falta,
configuración inválida), corrígelo antes de seguir: en [«Cuando algo
falla»](#7-cuando-algo-falla), más abajo, están los casos más comunes. El detalle completo de la
instalación y de `castkit comprobar` está en el README, secciones [«Instalación para usar
castkit»](../README.md#instalación-para-usar-castkit) y [«Comprobar la
instalación»](../README.md#comprobar-la-instalación-castkit-comprobar).

## 2. Preparar el brief

Antes de dar de alta un pedido, reúne en una sola carpeta (el «brief») la imagen o imágenes que
vas a usar. Para que el resultado salga bien:

- **Fondo liso o transparente.** Si la foto tiene un fondo con textura, degradado o distinto del
  color del borde de la figura, castkit puede sacarle una placa de más a la pieza (ver el caso
  «la placa de más» en [«Cuando algo falla»](#7-cuando-algo-falla)).
- **Una sola figura por imagen.** Si el dibujo trae varias figuras o elementos sueltos, sepáralas
  en imágenes distintas antes de dar de alta.
- **Vista frontal o de perfil**, según la pieza. Para una media figura (como el toro) usa una
  vista frontal; el proveedor `profundidad` calcula el relieve a partir de esa única vista.

El pedido del toro llegó como una foto de papel con fondo degradado (`toro-original.png`, con
fondo). Antes de usarla hubo que quitarle el fondo y dejarlo transparente
(`toro-dibujo.png`):

| Foto original (con fondo) | Lista para el brief (fondo transparente) |
| --- | --- |
| ![Foto original del toro futbolista, con fondo de papel degradado](img/toro-original.png) | ![Dibujo del toro futbolista, media figura con fondo transparente](img/toro-dibujo.png) |

Guarda la imagen ya lista (sin fondo) en la carpeta del brief, por ejemplo
`~/pedidos/brief-toro/`. Esa es la carpeta que le vas a indicar a `castkit alta` en el siguiente
paso.

## 3. Dar de alta con la entrevista

Con el brief listo, ejecuta `castkit alta` **sin indicar un nombre**: castkit te hace una
entrevista guiada, pregunta por pregunta, con una explicación de una línea y un valor propuesto
entre corchetes que aceptas con solo pulsar Intro.

Para el pedido del toro —media figura, molde abierto, 80 mm de largo y 15 mm de grosor, en
silicona, matriz en modo cubeta con 1 cavidad, 10 piezas y entrega el 2026-11-15— la entrevista
se contesta así:

```text
$ castkit alta
Nombre del diseño: toro
Carpeta del brief: ~/pedidos/brief-toro
Encontré 1 imagen: toro-dibujo.png
¿Qué imprime castkit? 1) el molde listo para verter silicona (cubeta) 2) solo la figura, para hacer el molde aparte [1]: 1
¿Qué tipo de pieza es? 1) diseño plano (logo, escudo, medalla) 2) media figura (frente con relieve, espalda plana) 3) figura completa [2]: 2
Largo en mm [80]: 80
Grosor en mm [15]: 15
Material de la pieza final 1) porcelana-fria 2) silicona 3) cera [2]: 2
Número de cavidades [1]: 1
Cantidad a producir [1]: 10
Fecha de entrega [2026-11-15]: 2026-11-15
Licencia del modelo base [propio]: propio
Origen del modelo base: dibujo del cliente

Resumen del diseño:
1. Nombre: toro
2. Carpeta del brief: /home/usuario/pedidos/brief-toro
3. Qué imprime castkit: cubeta
4. Tipo de molde: abierto (proveedor: profundidad)
5. Medidas: largo 80 mm, grosor 15 mm
6. Material de la pieza final: silicona
7. Cavidades: 1
8. Cantidad a producir: 10
9. Fecha de entrega: 2026-11-15
10. Licencia del modelo base: propio
11. Origen del modelo base: dibujo del cliente
¿Crear el diseño? (S/n, o el número de la línea que quieres cambiar):
Diseño «toro» creado en /home/usuario/toro.
Siguiente paso: castkit flujo toro
```

Cada pregunta acepta el valor propuesto entre corchetes con solo Intro (por ejemplo, la
licencia o el número de cavidades, si ya te sirven los valores que propone). Si te equivocas en
algo, en el resumen final puedes indicar el número de la línea a corregir en vez de confirmar; si
una respuesta no es válida (por ejemplo, un formato de fecha incorrecto), castkit te dice el
motivo y repite solo esa pregunta, sin perder lo demás. El detalle de qué pregunta cada campo, y
cómo se deduce el tipo de molde y el proveedor según el tipo de pieza, está en el README, sección
[«Entrevista guiada»](../README.md#entrevista-guiada-castkit-alta-sin-nombre).

## 4. Generar con `castkit flujo`

Con el diseño dado de alta, un solo comando encadena todo el proceso de fabricación: generar la
malla, limpiarla, el canal de mecha (si aplica) y el punto de control 1, hasta la matriz:

```bash
castkit flujo toro
```

Como la entrevista del paso anterior ya guardó el proveedor (`profundidad`) y el modo de matriz
(`cubeta`) en el diseño, no hace falta repetirlos aquí. A mitad de camino, `castkit flujo` se
detiene en el punto de control 1 y te pregunta si apruebas la figura antes de seguir con la
matriz: ese es el paso siguiente de esta guía. El detalle completo del flujo (qué etapas
encadena y en qué orden) está en el README, sección [«Ejecutar el flujo
completo»](../README.md#ejecutar-el-flujo-completo-castkit-flujo).

## 5. Revisar el punto de control 1

Antes de pasar a la matriz, castkit genera cuatro renders de la figura limpia —**frente**,
**perfil**, **arriba** y **3-4**— con las medidas finales (alto, ancho y fondo en mm)
sobreimpresas. Revísalas así:

- **Frente**: la silueta de la figura, que se reconozca bien y no le falten partes.
- **Perfil**: el grosor de la pieza (en el toro, debería verse una lámina pareja de unos 15 mm;
  ni aplastada ni inflada).
- **Arriba**: el contorno visto en planta, con el largo y el ancho sobreimpresos; revisa que las
  medidas coincidan con lo que pediste (en el toro, largo 80 mm) y que no haya una placa extra de
  más alrededor de la figura (ver [«la placa de más»](#7-cuando-algo-falla) más abajo).
- **3-4**: una vista en perspectiva, la más parecida a cómo se va a ver la pieza terminada; úsala
  para una revisión general de proporciones y detalle.

Así se ve la vista «arriba» del toro, con las medidas finales sobreimpresas:

![Render «arriba» de la figura del toro, con las medidas 61,8 × 80,0 × 15,0 mm sobreimpresas](img/toro-render.png)

Si alguna vista no te convence, rechaza la versión con un motivo (esto bloquea el paso a la
matriz hasta que corrijas y vuelvas a generar):

```bash
castkit control1 toro --rechazar --motivo "la cara quedó aplastada"
```

Si todo se ve bien, apruébala (si ya estás dentro de `castkit flujo` en una terminal, basta con
responder que sí cuando te pregunte; si lo haces aparte):

```bash
castkit control1 toro --aprobar
```

El detalle de dónde quedan los cuatro archivos PNG y de las opciones de `castkit control1` está
en el README, secciones [«Punto de control 1»](../README.md#punto-de-control-1-renders-de-las-4-vistas-y-aprobación-castkit-control1)
y [«Opciones de `castkit control1`»](../README.md#opciones-de-castkit-control1).

## 6. La matriz y la ficha técnica

Con el punto de control 1 aprobado, `castkit flujo` (o `castkit matriz` por separado) deja en
`output/` dos archivos por versión:

- **`<diseño>_v<versión>_matriz.stl`** (en el toro, `toro_v1_matriz.stl`): el archivo que le
  mandas a la impresora 3D, ya sea de filamento (FDM) o de resina (MSLA). Es la pieza que imprimes
  tal cual, sin retocarla.
- **`<diseño>_v<versión>_ficha.md`**: la ficha técnica en texto, con el material de la pieza
  final, el modo de matriz (figura o cubeta), las cavidades, el tipo de molde y sus medidas, las
  dimensiones exteriores de la matriz, el volumen por figura y, en modo cubeta, los mililitros
  de silicona estimados, además de una advertencia de taller según el material (por ejemplo,
  recordar el desmoldante en moldes de silicona sobre silicona).

Así se ve la matriz del toro (modo cubeta, 1 cavidad), vista en perspectiva:

![Matriz del toro en modo cubeta, vista 3/4: la base con la pared alrededor y la cavidad de la figura en el centro](img/matriz-toro.png)

Los modos `figura` y `cubeta` escriben siempre el mismo nombre de archivo: si generas los dos
para la misma versión, el segundo pisa al primero. Si necesitas conservar ambos, copia el
`.stl` a otro nombre antes de generar el segundo modo. El detalle completo (todas las opciones y
el contenido exacto de la ficha técnica) está en el README, secciones [«Generar la matriz
imprimible»](../README.md#generar-la-matriz-imprimible-stl-en-modo-figura-o-cubeta),
[«Ficha técnica»](../README.md#ficha-técnica) y [«Opciones de `castkit
matriz`»](../README.md#opciones-de-castkit-matriz).

## 7. Cuando algo falla

Estos son los problemas más comunes, con el mensaje tal cual lo vas a ver (las partes entre
`<…>` cambian según tu caso) y qué hacer en cada uno.

### Blender no encontrado

Lo vas a ver al correr `castkit control1` o `castkit flujo` (el punto de control 1 necesita
Blender siempre, sin alternativa):

```text
No se puede generar el punto de control 1 sin Blender: No se encontró el ejecutable de Blender. Instala Blender LTS (la versión requerida está fijada en el archivo .blender-version) y asegúrate de que el comando 'blender' esté en el PATH, o bien configura la variable de entorno BLENDER_BIN con la ruta completa al ejecutable de Blender.
```

**Qué hacer**: instala Blender LTS en la versión fijada (puedes verla ejecutando `castkit
comprobar`, que también la compara con la que tengas instalada) o define la variable de entorno
`BLENDER_BIN` con la ruta completa al ejecutable. Más sobre cómo castkit busca Blender, en el
README, sección [«Cómo apuntar a Blender»](../README.md#cómo-apuntar-a-blender).

### El extra `profundidad` no está instalado

Lo vas a ver si tu pedido usa el proveedor `profundidad` (medias figuras) y lo instalaste sin
ese extra:

```text
El proveedor profundidad necesita el extra opcional: uv sync --extra profundidad
```

**Qué hacer**: si instalaste castkit como herramienta (lo habitual en el taller), reinstálalo
con el extra:

```bash
uv tool install 'castkit[profundidad] @ git+ssh://git@github.com/alexisbanda/castkit.git'
```

(El mensaje menciona `uv sync --extra profundidad`, que es la forma equivalente para quien
trabaja desde un clon del repositorio en modo desarrollo.)

### La placa de más por el fondo de la foto

Este caso no deja un mensaje de error: el resultado se genera, pero sale con una lámina extra
alrededor de la figura, pegada a su base. Pasa cuando la foto de origen tiene un fondo que no es
uniforme (una foto de papel con sombra o degradado, por ejemplo): el proveedor `profundidad` toma
el color del borde de la imagen como si fuera el fondo, y si ese borde no es parejo, confunde
parte del fondo con la figura.

**Qué hacer**: lo ves en el render «arriba» del punto de control 1 (paso 5) como una placa
rectangular sobrante alrededor de la silueta. Vuelve a la imagen original, quítale el fondo
dejándolo transparente (como se hizo con `toro-dibujo.png` en el paso 2, comparado con
`toro-original.png`) y repite `castkit generar` (o `castkit flujo` completo) con la imagen
corregida.

### La limpieza rechaza la malla por quedar lejos de la original (remallado de respaldo)

Cuando la malla en bruto no cierra por sí sola, castkit reintenta automáticamente remallándola
por vóxeles dentro de Blender; ese remallado no siempre es fiel, y si se aleja demasiado de la
figura original, `castkit limpieza` (o `castkit flujo`) lo rechaza:

```text
El remallado de respaldo con Blender deformó la figura: la distancia máxima a la malla original es <distancia> mm, mayor que la tolerancia configurada (<tolerancia> mm). No se escribió ningún archivo. Siguientes pasos: corrige el modelo y vuelve a hacer "castkit ingesta" con una malla nueva.
```

Por ejemplo, con una guagua de pan el remallado de respaldo llegó a alejarse 3,5 mm de la
original: hoy este remallado casi nunca entrega piezas fieles cuando la malla de entrada está muy
abierta (ver [#28](https://github.com/alexisbanda/castkit/issues/28)).

**Qué hacer**: no hay forma de forzar que el remallado quede fiel; corrige el modelo de origen
(una malla o imagen con menos huecos abiertos) y vuelve a generarlo. Más sobre cuándo se usa este
respaldo, en el README, sección [«Respaldo con Blender (remallado por
vóxeles)»](../README.md#respaldo-con-blender-remallado-por-vóxeles).

### Fecha o medida inválida en el alta

Si escribes una fecha o una medida con un formato que castkit no reconoce (al dar de alta con
`nombre` y opciones, fuera de la entrevista guiada), vas a ver mensajes como estos:

```text
--fecha debe ser una fecha AAAA-MM-DD válida; recibido "<valor>".
--largo-mm debe ser un número mayor que 0; recibido "<valor>".
--grosor-mm debe ser un número mayor que 0; recibido "<valor>".
```

**Qué hacer**: corrige el valor con el formato pedido (la fecha, como `2026-11-15`; las medidas,
un número mayor que 0, con punto decimal, como `12.5`) y repite el comando. Si en cambio usas la
entrevista guiada del paso 3, castkit te avisa del mismo problema ahí mismo y repite solo esa
pregunta, sin perder las respuestas anteriores.
