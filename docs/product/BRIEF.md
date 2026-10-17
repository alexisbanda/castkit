# Brief de producto: Castkit

> Lo redacta Ticket Studio a partir de los documentos y la entrevista con el PM, y lo confirma el PM. Es el contexto de producto del proyecto: lo leen el equipo, Ticket Studio al escribir tickets y, si el proyecto lo usa, agent-playbook al planificar. Los encabezados `##` son fijos: `brief_check.py` los comprueba.

## Problema

Un taller artesanal produce figuras decorativas y conmemorativas para festividades (religiosas, tradicionales, familiares) en porcelana fría, silicona y cera. Hoy fabrica las figuras con moldes de silicona, pero cada matriz impresa en 3D de la que sale un molde se encarga a un proveedor externo: hay tiempos de espera, un coste por diseño y el taller no es dueño de los archivos. Eso impide iterar rápido sobre pedidos personalizados y construir una biblioteca propia, versionada y reutilizable de diseños. Se encargan de 1 a 3 diseños nuevos por semana. El objetivo es internalizar el diseño de las matrices y de los moldes con un pipeline asistido por agentes que puedan operar personas sin formación profesional en modelado 3D, manteniendo el carácter artesanal del producto (acabado y pintura siguen siendo manuales).

## Usuarios

| Usuario | Qué necesita | Con qué frecuencia usa el producto |
| --- | --- | --- |
| Operador de diseño (estudio remoto) | Ejecutar el flujo guiado, revisar y aprobar en los puntos de control, sin saber modelado 3D | Varias veces por semana (1–3 diseños nuevos por semana) |
| Responsable de producción (taller) | Crear el brief de cada diseño y recibir un paquete de fabricación que pueda imprimir y convertir en molde sin pedir aclaraciones | Con cada diseño nuevo (1–3 por semana) |
| Administrador | Mantener el repositorio, las plantillas, las reglas por material y la configuración | Puntual, cuando cambian reglas o plantillas |
| Cliente B2B (futuro) | Comprar moldes estandarizados con su ficha técnica | Fuera del MVP |

## Flujos principales

1. **Del brief al paquete de fabricación (camino feliz).** El responsable de producción crea un brief (referencias visuales, medidas finales, material, número de cavidades, cantidad, fecha). El operador de diseño recibe una malla 3D en bruto obtenida fuera del pipeline (herramienta imagen→3D, escaneo o escultura digital) y el flujo guiado la limpia y prepara: malla cerrada, escala real en mm, base plana, espesores corregidos y operaciones por material (p. ej. canal de mecha para velas). En el **punto de control 1** el operador valida con un render que la pieza es fiel al diseño. Después se genera la **caja de molde** de una pieza o **multicavidad**, se ejecuta la **validación automática** y en el **punto de control 2** el operador revisa el informe y los renders del molde. Por último se exporta el **paquete de fabricación** (STL del master y de la caja, renders, ficha técnica, informe de validación), se versiona, se publica en la carpeta del pedido en Google Drive y se redacta el aviso para el taller. Objetivo: del brief aprobado al paquete entregado en menos de 24 h, con en torno a 1 hora de trabajo activo del operador una vez dominado el flujo.
2. **Recepción y fabricación en el taller.** El responsable de producción recibe el aviso, descarga el paquete de la carpeta del pedido, lamina e imprime el master en su impresora, lo cura y sella, fabrica el molde de silicona y hace una colada de prueba, todo sin pedir aclaraciones al estudio (están en zonas horarias con unas 7 horas de diferencia).
3. **Nueva versión de un diseño.** Cuando el taller o el cliente piden un cambio, el operador genera una nueva versión del mismo diseño a partir de sus parámetros registrados; la versión anterior se conserva y el cambio queda en el historial del diseño.

## Glosario del dominio

| Término | Significa |
| --- | --- |
| Diseño | Una figura del catálogo o un pedido personalizado, con su brief, su malla, sus parámetros y sus versiones |
| Brief | Lo que pide el responsable de producción para un diseño: referencias visuales, medidas finales, material, número de cavidades, cantidad y fecha |
| Malla en bruto | El modelo 3D de partida, generado fuera del pipeline (imagen→3D, escaneo o escultura digital) |
| Master (matriz) | La pieza impresa en 3D con la forma final de la figura, a partir de la cual se hace el molde |
| Molde | La pieza de silicona que se obtiene vertiendo silicona alrededor del master; con él se producen las figuras |
| Caja de molde | El contenedor donde se coloca el master y se vierte la silicona para fabricar el molde |
| Cavidad | Cada hueco del molde que da una figura; un molde **multicavidad** produce varias figuras por colada |
| Llaves de registro | Relieves que hacen encajar siempre igual las partes de un molde o la caja |
| Canal de vertido | Conducto por el que entra el material de la figura en el molde |
| Respiradero | Conducto que deja salir el aire al verter |
| Línea de partición | Donde se separan las dos partes de un molde de dos partes |
| Socavado | Zona de la figura que queda "atrapada" en el molde y dificulta desmoldar; decide si basta un molde de una pieza flexible o hace falta uno de dos partes |
| Malla cerrada (manifold) | Malla sin agujeros, caras invertidas ni geometría imposible de imprimir |
| Canal de mecha | Hueco que atraviesa una vela para la mecha |
| Desmoldante | Producto que impide que la silicona se pegue; obligatorio al verter silicona sobre silicona |
| Colada de prueba | La primera tirada con un molde nuevo para comprobar que funciona |
| Punto de control | Paso en el que una persona revisa y aprueba antes de seguir; nada se entrega sin pasar por ellos |
| Paquete de fabricación | Lo que se entrega al taller por cada versión de un diseño: STL del master y de la caja, renders, ficha técnica e informe de validación |
| Ficha técnica | Documento del paquete con las medidas, material, volúmenes, cavidades y advertencias de fabricación (p. ej. desmoldante, compatibilidad master–silicona) |
| Informe de validación | Resultado legible de las comprobaciones automáticas de un diseño |
| Laminado (slicing) | Preparar el STL para la impresora concreta; se hace en el taller |

## Qué no es

- No genera figuras orgánicas desde cero: recibe una malla en bruto hecha con otra herramienta.
- No controla la impresora 3D del taller ni obliga a laminar en el estudio: el laminado se hace en el taller.
- No es una tienda online ni gestiona facturación, pedidos de venta o clientes.
- No sustituye el trabajo artesanal: el acabado, la pintura y los detalles siguen siendo manuales.
- No envía el aviso al taller por WhatsApp: lo redacta y una persona lo envía.
- En el MVP no hace moldes de dos partes, ni biblioteca consultable de diseños, ni estimación de costes, ni integración directa con herramientas imagen→3D, ni catálogo para venta de moldes a terceros.

## Requisitos no funcionales

- Idioma: todos los mensajes del flujo guiado, la ficha técnica, el informe de validación y el aviso al taller, en español.
- Usuarios no expertos: pasos guiados, mensajes claros y errores que digan qué hacer; el pipeline debe proteger contra los errores comunes.
- Puntos de control humanos explícitos: nada se entrega sin aprobación del operador.
- Reproducibilidad: con los mismos datos de entrada y parámetros se obtiene el mismo paquete; los parámetros quedan registrados en cada versión.
- Versionado de los diseños y de las reglas y plantillas.
- Validación automática obligatoria antes de cualquier entrega.
- Unidades en milímetros y a escala real del objeto final.
- Materiales del MVP: porcelana fría, silicona y cera (velas), cada uno con sus reglas.
- El taller usa impresión en resina (MSLA) y en filamento (FDM): el pipeline debe soportar las dos, y los parámetros de fabricación (márgenes, espesores, tolerancias) deben poder ajustarse con facilidad porque solo se validan con pruebas físicas.
- Entrega asíncrona entre dos ubicaciones con unas 7 horas de diferencia: cada paquete debe ser autocontenido y sin ambigüedades, publicado en una carpeta de Google Drive por pedido.
- Debe funcionar en los equipos del estudio, en Linux, macOS y Windows.
- Coste: preferir herramientas libres o de bajo coste y documentar cualquier dependencia de pago.
- Seguridad: revisar servidores MCP y dependencias de terceros antes de adoptarlos; no exponer credenciales.
- Licencias: registrar el origen y la licencia de cada modelo base; solo diseños propios o con licencia comercial, sobre todo de cara a la venta futura de moldes.
- Extensibilidad: añadir reglas por material, tipos de molde y plantillas sin reescribir el pipeline.
- Sin fecha fija de entrega del MVP.

## Épicas por prioridad

1. **Proyecto de diseño.** Crear un diseño a partir de su brief con una estructura de carpetas, nombres y versiones estándar, y registrar sus parámetros por versión. Alcance: alta del diseño desde el brief, convención de nombres y versiones, carpetas de brief, entrada, intermedios y salida, historial de cambios y registro del origen y licencia del modelo base; no incluye biblioteca consultable de diseños.
2. **Preparación de la malla.** Convertir una malla en bruto en un master imprimible a escala real, con el punto de control 1. Alcance: ingesta de la malla externa, cierre de malla, reducción de polígonos, escalado a mm, aplanado de base, corrección de espesores, operaciones por material (canal de mecha para velas) y render para validar la fidelidad; no incluye generar la malla ni integrar herramientas imagen→3D.
3. **Caja de molde de una pieza y multicavidad.** Generar de forma paramétrica la caja para fabricar el molde de silicona. Alcance: margen uniforme de silicona, base de fijación, paredes desmontables o desmoldables, llaves de registro, canal de vertido, respiraderos y distribución multicavidad con separación mínima; no incluye moldes de dos partes ni línea de partición.
4. **Validación automática e informe.** Comprobar automáticamente que el master y el molde se pueden fabricar y explicarlo en un informe legible, con el punto de control 2. Alcance: malla cerrada, escala, espesores mínimos, estabilidad de la base, análisis y clasificación de socavados, volumen de la figura y volumen de silicona del molde, e informe con renders del molde; no incluye estimación de costes.
5. **Paquete de fabricación y entrega.** Exportar y entregar al taller todo lo que necesita para fabricar sin pedir aclaraciones. Alcance: STL del master y de la caja, renders, ficha técnica en español, informe de validación, versionado, publicación en la carpeta del pedido en Google Drive y aviso redactado para el taller; no incluye enviar el aviso ni laminar.
6. **Moldes de dos partes.** Diseñar moldes de dos partes para las piezas con socavados que no admiten molde de una pieza. Alcance: línea de partición asistida, llaves de registro entre partes y su validación; no incluye moldes de más de dos partes.
7. **Biblioteca de diseños.** Consultar y reutilizar los diseños y sus versiones. Alcance: catálogo consultable de diseños con sus parámetros, versiones y licencias; no incluye venta de moldes a terceros.
8. **Estimación de costes de material.** Calcular el coste de material por figura y por molde. Alcance: coste a partir de los volúmenes del informe de validación y de los precios de cada material; no incluye facturación ni presupuestos comerciales.

## Preguntas abiertas

- [ ] Pieza de prueba del MVP (una guagua de pan, de las fiestas tradicionales de Ecuador para el Día de los Muertos): ¿en qué material y a qué tamaño final en mm?
- [ ] ¿Qué valores iniciales de fabricación usar (margen de silicona, espesor mínimo de detalle, separación entre cavidades) para resina y para filamento, hasta validarlos con coladas de prueba?
- [ ] ¿Qué entrega hoy exactamente el proveedor (formatos, renders, fichas) y qué problemas recurrentes tiene, para replicarlo o mejorarlo?
- [ ] ¿Qué silicona y qué desmoldantes usa el taller (p. ej. silicona de platino o de estaño), para las advertencias de la ficha técnica?
- [ ] ¿En qué formato se entrega el brief del responsable de producción (plantilla, documento libre, fotos)?
- [ ] ¿Qué criterios hacen "bueno" a un molde para el taller, y cómo llega su opinión tras la colada de prueba al estudio?
- [ ] ¿Qué requisitos de estandarización y catálogo tendrá la venta de moldes a terceros (fase posterior)?
