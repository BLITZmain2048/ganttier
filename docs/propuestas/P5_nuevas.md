# P5 — Funciones nuevas para el trabajo diario del planificador

Revisión: 03-10-2026. Se seleccionan **11 funciones nuevas**, con entregas mínimas de S (≤2 días) o M (≤1 semana laboral). Son propuestas de desarrollo, no funciones implementadas ni pruebas ejecutadas.

## Base revisada y criterio de selección

Se leyeron [README](../../README.md), [manual de usuario](../manual_usuario.md), las **58 propuestas completas** —[P1: 15](P1_planificacion.md), [P2: 14](P2_mantenimiento.md), [P3: 15](P3_control.md), [P4: 14](P4_plataforma.md)— y las nueve páginas de [Propuestas_Mejora_Ganttier.pdf](../../Propuestas_Mejora_Ganttier.pdf), mediante extracción de texto en memoria. También se inspeccionaron dominio, esquema, persistencia, servicios, calendarios, API, motores de carga y precedencias, intercambio Excel y GUI/reportes pertinentes de `backend/ganttier/`. El frontend web está en desuso: las integraciones propuestas son para PySide6.

El PDF también excluye novedades aparentes: sus recortes P2-12a, P3-15, P4-06a y sus defectos D1–D4 siguen siendo trabajo ya evaluado. No se los vuelve a presentar con un ID P5. Que una función se haya pospuesto tampoco la convierte en nueva.

Durante la revisión hubo cambios concurrentes en el repositorio. Al cierre se contrastaron también `api/analysis.py`, `control/forecasts.py`, `control/aggregation.py`, `control/schedule_quality.py` y las extensiones de `domain.py`: incorporan capacidades de P3 y no las funciones P5 seleccionadas. Esta revisión no modificó esos archivos.

**Estimación:** una persona familiarizada con el repositorio, incluyendo API, interfaz mínima, validación y pruebas del alcance indicado. Los límites de volumen son de la primera entrega, no nuevas restricciones generales del producto. Las propuestas no requieren implementar previamente un módulo L/XL. P5-02 incluye su migración puntual; las demás aprovechan el esquema actual o archivos de plantilla independientes. Los archivos marcados **nuevo** son ubicaciones futuras; todas las rutas de «Dónde» parten de `backend/ganttier/`, salvo indicación expresa.

## Evaluación crítica de las ideas sugeridas

| Idea | Decisión y fundamento |
|---|---|
| Lookahead imprimible de 2–6 semanas por cuadrilla | **No contabilizar como nueva.** `services.build_report_data` ya obtiene el período siguiente; `reports/charts.py` dibuja un lookahead. Horizonte configurable y filtrado por cuadrilla son ampliaciones razonables del informe y de P3-10, no una función nueva independiente. P5-01 sí añade una matriz nominal diaria, que ese listado no representa. |
| Plan semanal por trabajador a Excel | **Aceptar: P5-01.** Falta la relación visible persona × día × actividades; el desempeño existente es retrospectivo. |
| Histograma por habilidad con línea de capacidad | **No contabilizar.** `services.load_profile` acepta `group="skill"`; `reports/charts.py` y `reports/xlsx.py` ya dibujan carga y capacidad. El informe estándar pide `resource`: añadir un selector de agrupación es una mejora menor de integración, no otro histograma nuevo. |
| Programa versus ejecutado de mantenimiento por mes | **Descartar por duplicación.** P2-12 y el recorte P2-12a del PDF ya cubren cumplimiento y backlog; P3-01 cubre conservar el compromiso histórico. Los conteos existentes de programadas y terminadas tampoco deben venderse otra vez. |
| Códigos de actividad y UDF filtrables | **Aceptar: P5-02.** `Activity.code` ya existe como identificador; la novedad son clasificaciones adicionales configurables, independientes de WBS. |
| Plantillas de proyecto y copiar/pegar entre proyectos | **Fusionar en P5-03.** Comparten selección, limpieza y remapeo de una estructura. Duplicar el proyecto completo ya existe y conserva avance y bases. |
| Renumeración automática | **Aceptar acotada: P5-04.** Transformación de identificadores visibles con control de colisiones, distinta de editar nombres o HH. |
| Detección de bucles y relaciones redundantes | **Descartar del listado.** `repo._validate_relation_cycles` ya rechaza ciclos, también por lotes. La auditoría de redundancias corresponde a P3-15; eliminar una relación por mera transitividad sería incorrecto con tipos, desfases y calendarios distintos. |
| Feriados regionales e irrenunciables | **Aceptar con límites: P5-05.** Existen feriados nacionales y excepciones manuales; falta asistencia territorial con fuente y aplicabilidad. «Irrenunciable» no se aplicará indiscriminadamente a personal eléctrico. |
| Faena 7×7, 10×10, 14×14 y días de traslado | **Descartar como paquete S/M nuevo.** `WorkCalendar` ya admite NxM. El traslado dependiente de la secuencia está en P1-04/P2-10; su tratamiento laboral requiere antecedentes concretos. Agregar patrones precargados no aporta una función nueva. |
| Trabajos nocturnos | **Posponer.** Una etiqueta «noche» no permite comprobar simultaneidad ni descanso. La solución funcional está en P1-09/P2-05, de alcance XL. |
| HH extra según jornada | **Posponer.** El motor ya estima exceso de capacidad y costo; calcular horas extraordinarias laborales exige jornada pactada, asistencia y régimen aplicable. No basta cambiar una fórmula diaria. Véase la revisión normativa al final. |
| Resumen ejecutivo de una página | **No contabilizar.** `reports/common.py` ya incluye resumen y conclusiones; reducir su extensión es una variante de presentación de P3-10. |
| Gantt a Excel con barras | **Aceptar solo el delta de P5-07:** matriz completa de celdas y fechas utilizables en Excel. El XLSX actual ya inserta una imagen del lookahead de hasta 20 actividades. |
| Notas y adjuntos por actividad | **Descartar del listado.** `Activity.notes` y su formulario ya existen. Evidencia y documentos asociados se solapan con P3-06/P3-07 y P2-13; no se suma una biblioteca de adjuntos como otra novedad. |
| Búsqueda global | **Aceptar: P5-06.** La paleta busca páginas, acciones y proyectos; la consulta de actividades está restringida al proyecto. Falta localizar tareas por TAG/código sin conocer su proyecto. |
| Estado suspendida con reanudación | **Posponer.** Hoy hay tres estados y el avance se sincroniza al corte. Suspender debe separar trabajo ejecutado/remanente y liberar su carga durante la interrupción; no basta añadir un valor al selector. Se cruza con P1-10 y excede una entrega M segura. |

## Propuestas

### P5-01 Plan semanal nominal por trabajador en Excel

- **Problema real:** El supervisor de pruebas de una S/E de 220 kV necesita saber qué hace Ana cada día y qué ensayos siguen sin persona asignada. Un histograma muestra HH y el informe de desempeño muestra resultados, pero ninguno entrega su agenda diaria de actividades.
- **Propuesta concreta:** Exportar una semana lunes–domingo del proyecto activo: filas por trabajador identificado por ID, siete columnas diarias y códigos/nombres/instalación de las actividades programadas. Incluir tareas que comenzaron antes de la semana y continúan en ella. Usar asignaciones explícitas y días laborables de la actividad; señalar si el recurso tiene ese día sin disponibilidad. Cuadrillas agregadas y demandas genéricas tendrán bloques separados: no inventar integrantes ni asignar personas por una habilidad coincidente. Añadir hoja Detalle con una fila por asignación/día, filtros, cabeceras de impresión y fecha de extracción. La primera versión comunica tareas y coincidencias diarias, sin prometer secuencia horaria, HH reales ni factibilidad intradía. Hasta 250 trabajadores y 1.000 actividades del intervalo, con aviso previo si se excede.
- **Dónde:** `reports/weekly_roster.py` (**nuevo**), `services.py`, `api/files.py`, `gui/client.py`, `gui/pages/reports.py`; reutilizar `repo.py` para asignaciones y `calendars.py` para días efectivos.
- **Valor:** alto — sustituye la planilla semanal que el planificador prepara para cada supervisor.
- **Esfuerzo:** M, 4–5 días.
- **Riesgo:** medio — confundir una cuadrilla agregada con personas nominales o interpretar dos actividades del día como solape horario demostrado.
- **Criterio de aceptación medible:** Con Ana asignada a A el lunes–martes y a B el miércoles, esas tres celdas contienen las tareas correctas; C, sin persona, aparece solo en «Sin asignación nominal». Una D iniciada la semana anterior y vigente el lunes también aparece. Dos trabajadores homónimos conservan filas distintas. Un descanso del recurso genera marca de conflicto, no desaparición silenciosa de la tarea. La exportación no cambia fechas ni asignaciones.
- **Por qué no duplica P1–P4:** P3-08 recibe avance desde terreno; aquí se comunica trabajo futuro, sin importación. P3-10 configura informes y P3-11 agrega control; ninguno especifica esta matriz de asignaciones nominales por día. No implementa la asignación automática de P1-02 ni reservas entre proyectos de P1-15.

### P5-02 Clasificaciones y campos personalizados filtrables

- **Problema real:** En el montaje de una línea, el planificador necesita filtrar simultáneamente «Contrato = C07» y «Zona = Norte». WBS, instalación y disciplina ya tienen otro significado; incrustar esos datos en el nombre impide mantenerlos consistentes.
- **Propuesta concreta:** Hasta diez campos por proyecto, de tipo texto o lista cerrada, aplicados a actividades existentes. Una lista sirve como código de clasificación; `Activity.code` conserva su función de identificador. Ofrecer definición de campos, edición en el detalle y un filtro de hasta dos condiciones AND: igualdad, contiene para texto y vacío/no vacío. Mostrar resultados navegables al Gantt y exportarlos a Excel como tabla. Admitir valores vacíos, impedir borrar una opción utilizada sin resolver sus valores y preservar campos nativos. Sin fórmulas, jerarquías de códigos, herencia desde tareas periódicas ni importación bidireccional de UDF en esta entrega.
- **Dónde:** `custom_fields.py` y `gui/custom_fields.py` (**nuevos**); `domain.py`, `schema.sql`, `db.py`, `repo.py`, `api/planning.py`, `api/schemas.py`, `gui/gantt/dialogs.py`, `gui/pages/activities.py`, `gui/client.py`. Incluir definición y valores en `repo.export_bundle`/duplicación cuando corresponda mediante un contrato explícito, sin introducirlos silenciosamente en la plantilla Excel antigua.
- **Valor:** alto — permite ordenar paquetes por contrato, zona o empresa sin deformar la WBS.
- **Esfuerzo:** M, 5 días, con dos tablas aditivas y migración puntual transaccional desde el esquema vigente; no incluye la infraestructura general de P4-07.
- **Riesgo:** medio — valores huérfanos, filtros que no distinguen vacío de texto y pérdida de datos al duplicar. El alcance estrecho de tipos y filtros es necesario para sostener M.
- **Criterio de aceptación medible:** En 1.000 actividades, un conjunto conocido de 37 con C07/Norte produce exactamente 37 resultados y filas exportadas. Reiniciar conserva definiciones y valores; duplicar el proyecto conserva clasificaciones con nuevos IDs. Una base v1 con actividades migra sin perder filas; un fallo inducido revierte el paso completo. Un campo obligatorio del dominio no puede sustituirse por un UDF homónimo.
- **Por qué no duplica P1–P4:** P4-03 guarda vistas sobre atributos ya definidos; P3-11 usa dimensiones para EVM. Aquí se crea el dato clasificatorio configurable y su filtro, sin vistas guardadas ni nuevos KPI. P3-13 intercambia datos de P6 y no implementa un editor local de UDF.

### P5-03 Biblioteca de paquetes reutilizables con WBS y relaciones

- **Problema real:** Al preparar un nuevo paño de 220 kV se vuelve a escribir la secuencia de montaje, cableado y ensayos. Duplicar toda una S/E arrastra trabajos ajenos, avance y líneas base; copiar filas de Excel exige reconstruir relaciones.
- **Propuesta concreta:** Capturar un subárbol WBS o una lista explícita de hasta 250 actividades de modo Proyecto en un archivo de plantilla versionado. Incluir WBS necesaria, duraciones, HH, habilidad, dotación y relaciones internas con tipo/desfase. Permitir insertarlo en otro proyecto existente o en uno nuevo, con prefijo de códigos y nodo WBS destino. El mismo diálogo admite copiar directamente desde otro proyecto: no se cuentan dos mejoras. Mostrar y excluir relaciones hacia fuera del paquete; no seguirlas automáticamente. Reiniciar avance, reales, bases, fechas calculadas, ventanas, restricciones fechadas y bloqueos; usar calendario elegido en destino y demandas por habilidad, sin copiar asignaciones nominales. La vista previa enumera lo conservado y lo excluido. No traslada recurrencias de mantenimiento ni crea un catálogo técnico homologado.
- **Dónde:** `project_templates.py` y `gui/project_templates.py` (**nuevos**); `repo.py`, `api/planning.py`, `gui/client.py`, `gui/pages/projects.py`, `gui/pages/activities.py`. Reutilizar `ImportBundle`, remapeo y `repo.transaction`, con validación completa antes de insertar.
- **Valor:** alto — ahorra preparación repetida de paquetes habituales y evita arrastrar historia de otra obra.
- **Esfuerzo:** M, 5 días; biblioteca de archivos con selector simple, sin editor de versiones ni miniaturas.
- **Riesgo:** medio — omitir un vínculo externo importante o reutilizar un procedimiento para un equipo distinto. La plantilla representa una secuencia de planificación, no una autorización técnica de ejecución.
- **Criterio de aceptación medible:** Un paquete de 20 tareas, tres nodos WBS y 19 vínculos internos se inserta dos veces con prefijos distintos: genera 40 tareas y 38 vínculos internos, sin referencias al proyecto origen. Un vínculo externo aparece en la vista previa como excluido. No hay avance, base ni fecha antigua en las copias. Un código en conflicto deja cero inserciones.
- **Por qué no duplica P1–P4:** `repo.duplicate_project` copia el proyecto completo con avance y bases; las plantillas Excel actuales son contratos de intercambio. P1-07 conserva escenarios para comparar alternativas. Este alcance reutiliza una estructura parcial limpia y permite insertarla en un proyecto existente; ninguna de las 58 propuestas cubre esa operación.

### P5-04 Renumeración de códigos antes de emitir el programa

- **Problema real:** Antes de enviar el programa de una S/E, el mandante pide pasar de códigos provisionales a `SE-PAT-0010`, `SE-PAT-0020`, etc. Editarlos uno a uno puede crear colisiones o romper la correspondencia de la planilla de preparación.
- **Propuesta concreta:** Asistente con lista ordenada explícitamente, prefijo, número inicial, incremento y relleno con ceros; vista previa código anterior/nuevo y exportación de equivalencias. Primera entrega para hasta 1.000 actividades ordinarias de modo Proyecto, sin avances ni presencia en líneas base. Excluir ocurrencias periódicas y avisar por qué. Cambiar solo el código visible manteniendo el ID; resolver intercambios de códigos mediante nombres temporales internos en una transacción. Verificar colisiones con filas fuera del lote y revalidar los códigos de origen antes de aplicar. Las antiguas corridas quedan sometidas a la huella existente; los libros Excel ya exportados deben regenerarse porque el merge utiliza códigos.
- **Dónde:** `activity_codes.py` y `gui/gantt/renumber.py` (**nuevos**); `repo.py`, `api/planning.py`, `gui/pages/activities.py`, `gui/client.py`; consulta de `services._scenario_fingerprint` para compatibilidad con corridas.
- **Valor:** medio — útil al preparar entregables; menor frecuencia que la agenda y búsqueda diaria.
- **Esfuerzo:** M, 3–4 días.
- **Riesgo:** medio — referencias externas por código y textos libres no se actualizan automáticamente. La tabla de equivalencias forma parte obligatoria del resultado.
- **Criterio de aceptación medible:** Renumerar 100 actividades produce exactamente 100 códigos únicos con salto 10; IDs, asignaciones y los pares de IDs de todas las relaciones permanecen idénticos. Un intercambio A↔B funciona atómicamente. Una colisión con una actividad no seleccionada o un avance registrado después de abrir la vista previa deja cero cambios.
- **Por qué no duplica P1–P4:** P4-02 define edición masiva de nombre, habilidad, calendario, prioridad y HH; no incluye transformación de claves legibles, colisiones ni equivalencias. P5-03 crea registros nuevos; P5-04 conserva los existentes. No es una bitácora histórica como P3-06.

### P5-05 Asistente de feriados territoriales con aplicabilidad explícita

- **Problema real:** Una cuadrilla que trabaja en Arica necesita considerar el 7 de junio sin bloquear ese día en el calendario de una obra en Santiago. El calendario actual admite país y excepciones, pero no ayuda a identificar la regla territorial ni su fuente.
- **Propuesta concreta:** Seleccionar territorio y año, ver feriados territoriales con nombre, fuente, artículo y ámbito, y aplicar los confirmados como excepciones sobre una copia identificada del calendario. Primera entrega: Arica y Parinacota con su feriado recurrente verificado, más carga manual de otras reglas territoriales mediante el mismo formulario. Conservar excepciones previas; si una fecha ya tiene excepción, pedir resolverla en la vista previa. Mostrar los feriados del art. 2 de la Ley 19.973 con la etiqueta «irrenunciable para dependientes del comercio, con excepciones», sin bloquear por esa etiqueta a todo recurso eléctrico. Una regla «a verificar» puede registrarse, pero no aplicarse automáticamente. No se promete un catálogo nacional exhaustivo ni actualización legal automática.
- **Dónde:** `holiday_catalog.py` (**nuevo**), `calendars.py`, `api/resources.py`, `gui/pages/calendars.py`, `gui/client.py`; reutilizar `CalendarException.reason` para fuente/ámbito y el CRUD de calendarios, sin cambiar el motor temporal.
- **Valor:** medio — alto para faenas en territorios afectados; menor para una cartera localizada en otra región.
- **Esfuerzo:** M, 4 días, limitado al piloto y alta manual descritos.
- **Riesgo:** medio — extender una regla territorial a todo Chile o confundir feriado legal con imposibilidad operacional universal. El usuario confirma el régimen aplicable a la faena.
- **Criterio de aceptación medible:** Con un calendario L–V sin otras excepciones, el 07-06-2027 resulta no laborable en la copia para Arica y laborable en la copia equivalente de Santiago. Reaplicar no duplica excepciones; una excepción previa laborable se conserva hasta resolver el conflicto. Seleccionar un trabajador eléctrico no activa automáticamente la categoría irrenunciable del comercio.
- **Por qué no duplica P1–P4:** Los feriados nacionales y las excepciones ya existen; la novedad es seleccionar y aplicar una regla territorial trazable. P2-11 gestiona obligaciones y vencimientos de mantenimiento por activo, no días laborables territoriales. No implementa calendarios intradía de P1-09.
- **Referencia normativa:** [Ley 20.663, artículo único](https://www.bcn.cl/leychile/navegar?idNorma=1050508): feriado anual del 7 de junio en Arica y Parinacota. [Ley 19.973, artículo 2](https://www.bcn.cl/leychile/navegar?idNorma=230132): feriados irrenunciables del comercio y excepciones. Aplicabilidad laboral concreta a cada cuadrilla: **a verificar**, considerando los arts. 35 y 38 del Código del Trabajo; véase revisión normativa.

### P5-06 Búsqueda de actividades y tareas entre proyectos

- **Problema real:** En una llamada se solicita el trabajo sobre «52-J1», pero el planificador no recuerda si está en la ampliación de la S/E o en el mantenimiento anual. Debe abrir proyectos sucesivamente y repetir la búsqueda.
- **Propuesta concreta:** Añadir a la paleta existente una acción «Buscar en todos los proyectos» que consulte actividades y tareas del plan maestro por código, nombre, instalación y equipo/TAG. Resultados con proyecto, modo, tipo, código y fechas, con navegación al detalle correcto. Buscar primero código/TAG exacto y después coincidencias parciales; usar parámetros SQL, paginación de 50 filas y ejecución en segundo plano. Separar explícitamente tarea maestra de ocurrencia. Primera versión limitada a la base local y esos campos, sin indexar documentos ni construir un buscador semántico. No sustituye los filtros dentro del Gantt.
- **Dónde:** `api/search.py` y `gui/search.py` (**nuevos**), `repo.py`, `api/__init__.py`, `gui/app.py`, `gui/client.py`, `gui/pages/activities.py`, `gui/pages/maintenance.py`; reutilizar `gui/widgets/palette.py` como acceso.
- **Valor:** alto — reduce interrupciones diarias y búsquedas manuales en carteras mixtas.
- **Esfuerzo:** M, 3–4 días.
- **Riesgo:** bajo — resultados homónimos, respuesta obsoleta al cambiar consulta y pérdida de un formulario sin guardar al navegar. La navegación debe respetar las guardas de borradores existentes.
- **Criterio de aceptación medible:** Tres actividades con TAG 52-J1 en proyectos distintos aparecen con su proyecto; abrir una lleva al ID correspondiente. Una tarea maestra y sus ocurrencias se distinguen. En una base de referencia de 100.000 actividades totales y diez proyectos, obtener la primera página en ≤2 s en 20 búsquedas, registrando equipo y tiempos; nunca se descarga toda la cartera a la GUI. Una respuesta tardía no reemplaza resultados de una consulta posterior.
- **Por qué no duplica P1–P4:** La paleta actual busca comandos y proyectos, y `api/planning.py::activities` exige un `pid`. P4-10 mejora rendimiento del recorrido existente; P4-12 busca ayuda. Ninguna ofrece búsqueda de contenido de planificación entre proyectos.

### P5-07 Gantt tabular de celdas en Excel

**Implementado el 05-10-2026 con alcance ampliado:** resumen por nivel y carga por
trabajador, escalas día/semana/mes, agregación para proyectos masivos y diálogo de
exportación. La especificación vigente y sus límites están en
[Gantt Excel](../decisiones/gantt_excel.md). La propuesta original se conserva abajo
como antecedente; sus límites de 500 actividades/90 días fueron reemplazados.

- **Problema real:** El jefe de terreno necesita filtrar por instalación, agregar comentarios y trabajar con las fechas del programa en Excel. La imagen del lookahead actual muestra hasta 20 filas y no permite manipular sus barras como una matriz de fechas.
- **Propuesta concreta:** Exportación dedicada con filas de actividades y columnas de fechas, barras mediante formato condicional sobre inicio/fin y valores de fecha reales. Incluir código, nombre, WBS, instalación, calendario, inicio, fin y avance; marcar hitos con símbolo y fechas fuera del intervalo con indicadores de continuación. Elegir escala diaria hasta 90 días o semanal hasta 52 semanas, con un máximo inicial de 500 actividades seleccionadas mediante filtro propio. Congelar encabezados, añadir autofiltro y conservar todas las filas aceptadas. En escala diaria, marcar los días no laborables según calendario de actividad. El libro es una salida de planificación; editarlo no reprograma ni reimporta Ganttier. No incluye conectores de precedencia ni composición de plano A1/A3.
- **Dónde:** `reports/gantt_xlsx.py` (**nuevo**), `services.py`, `api/files.py`, `gui/client.py`, `gui/pages/reports.py`; reutilizar consulta de actividades/WBS y `calendars.py`. Para este tamaño puede usarse un libro normal de openpyxl sin modificar el exportador masivo existente.
- **Valor:** alto — entrega un programa utilizable en la herramienta habitual de coordinación de terreno.
- **Esfuerzo:** M, 4 días.
- **Riesgo:** medio — confundir barra de intervalo con ocupación continua o perder filas al paginar. El límite debe informarse antes de exportar, sin truncamiento silencioso.
- **Criterio de aceptación medible:** Un caso de 120 tareas y 60 días produce 120 filas y 60 columnas diarias; cada celda coloreada coincide con el intervalo y calendario definido, y tres hitos aparecen en su fecha. El XLSX contiene fechas numéricas y reglas de formato condicional, no una imagen como sustituto de las barras. Un filtro por instalación mantiene alineadas fila y barra. Una solicitud de 501 actividades solicita acotar antes de crear el archivo.
- **Por qué no duplica P1–P4:** `reports/xlsx.py` ya incorpora una imagen del lookahead; aquí se añade una matriz de todas las actividades seleccionadas con fechas y barras manipulables en Excel. P4-04 es un plano PDF vectorial paginado, y P3-10 configura secciones de informes; no entregan esta estructura de celdas.

### P5-08 Calculadora de fechas laborables del proyecto

- **Problema real:** El proveedor anuncia que necesita doce días laborables desde la entrega de un interruptor. Durante la reunión, el planificador cuenta a mano fines de semana, descansos de turno y feriados para responder qué fecha resulta.
- **Propuesta concreta:** Diálogo de solo consulta con calendario e intervalo: calcular término inclusivo desde inicio + N días laborables, o contar días laborables y horas disponibles entre dos fechas. Mostrar calendario elegido, regla inclusiva y días excluidos con motivo. Permitir dos calendarios lado a lado para comparar resultados, sin cambiar actividades. Reutilizar `WorkCalendar.add_workdays`, `workdays_between` y `hours`; N=0 conserva la fecha como hito. No calcular productividad, dotación óptima ni autorización de jornada.
- **Dónde:** `gui/calendar_calculator.py` (**nuevo**), `gui/app.py`, `gui/client.py`, `api/resources.py`; reutilizar `calendars.py` sin duplicar su aritmética.
- **Valor:** medio — ahorro pequeño pero frecuente, con alcance claro y riesgo bajo.
- **Esfuerzo:** S, 1–2 días.
- **Riesgo:** bajo — contar el inicio dos veces o confundir días corridos con laborables. La convención será visible junto al resultado.
- **Criterio de aceptación medible:** Calendario ficticio L–V de 8 h, sin feriados: desde lunes 05-10-2026, cinco días terminan el viernes 09; con una excepción no laborable el miércoles 07, terminan el lunes 12. Entre el 05 y el 09 hay cuatro días/32 h en ese segundo calendario. N=0 devuelve el día inicial. Ninguna consulta escribe en la base.
- **Por qué no duplica P1–P4:** La aritmética ya existe dentro del motor; falta la herramienta interactiva de consulta sin crear una tarea ni ejecutar CPM. P4-12 explica conceptos mediante ayuda, y P1-09 cambia la resolución temporal; ninguna ofrece esta calculadora con los calendarios reales del proyecto.

### P5-09 Creación de cadenas de relaciones desde una lista ordenada

- **Problema real:** Para preparar veinte paquetes de montaje y pruebas, el planificador enlaza manualmente cada actividad consecutiva. Copiar la lista desde Excel crea filas, pero todavía exige escribir las predecesoras o dibujar cada vínculo.
- **Propuesta concreta:** Diálogo donde pegar códigos existentes, reordenarlos y previsualizar una cadena FS con desfase cero. Permitir separadores de grupo para crear varias cadenas independientes. Aplicar todas las relaciones en una transacción y reutilizar la detección de ciclos del repositorio. Un par que ya tiene FS/0 se cuenta como existente; un par con otro vínculo se informa y bloquea ese lote, sin reemplazarlo. Límite de 250 actividades, solo modo Proyecto y sin inferir el orden desde una tabla cuya ordenación puede cambiar. El usuario dispara después el CPM existente.
- **Dónde:** `gui/gantt/link_sequence.py` (**nuevo**), `api/planning.py`, `repo.py`, `gui/pages/activities.py`, `gui/client.py`; reutilizar `repo.create_many` y `_validate_relation_cycles`.
- **Valor:** alto — reduce edición repetitiva al construir redes pequeñas antes de optimizar.
- **Esfuerzo:** M, 3 días.
- **Riesgo:** medio — imponer una secuencia que técnicamente no existe. El orden y los enlaces propuestos deben revisarse explícitamente; no se generan por similitud de nombres.
- **Criterio de aceptación medible:** Una lista A,B,C y otra D,E producen A→B, B→C y D→E, sin C→D. Repetir no agrega vínculos. Si ya existe C→A, el intento se rechaza y no inserta ninguno de los otros enlaces. Veinte actividades válidas de un único grupo producen exactamente 19 vínculos FS/0.
- **Por qué no duplica P1–P4:** P4-02 modifica atributos de actividades, no construye relaciones. P3-15 diagnostica calidad y P1-01 programa relaciones en mantenimiento. Aquí se asiste la autoría de una red de Proyecto, reutilizando validaciones actuales, sin proponer una nueva detección de ciclos.

### P5-10 Ausencias personales por intervalo sin alterar el calendario compartido

- **Problema real:** Un técnico de protecciones estará cinco días en capacitación. Marcar esos días sobre el calendario compartido de la S/E dejaría indisponible a toda la cuadrilla; crear y editar a mano un calendario exclusivo es propenso a errores.
- **Propuesta concreta:** Desde un trabajador del proyecto activo, registrar desde/hasta, motivo breve y disponibilidad diaria cero o parcial. Crear una copia personal del calendario efectivo, conservando semana/turno, ancla y excepciones, aplicar el intervalo y asignar la copia solo a ese recurso dentro de una transacción. Mostrar capacidad antes/después y asignaciones explícitas afectadas; no reprogramar automáticamente. Primera entrega para trabajadores locales con `max_units=1`; rechazar recursos globales y cuadrillas agregadas, explicando que afectan varios ámbitos. Si el recurso ya usa un calendario exclusivo, permitir reutilizarlo con comprobación de referencias. Resolver excepciones coincidentes en vista previa. No gestionar saldos de vacaciones, licencias médicas ni remuneraciones.
- **Dónde:** `resource_absences.py` y `gui/resource_absences.py` (**nuevos**), `repo.py`, `api/resources.py`, `gui/client.py`, `gui/pages/resources.py`; reutilizar `CalendarException` y `WorkCalendar`.
- **Valor:** alto — permite preparar la disponibilidad real de la semana sin perjudicar a compañeros que comparten turno.
- **Esfuerzo:** M, 4 días.
- **Riesgo:** medio — proliferación de copias y calendarios personales que dejan de heredar futuras ediciones del original. La interfaz debe mostrar el calendario origen y advertir que la copia es independiente; no prometer herencia dinámica.
- **Criterio de aceptación medible:** Dos trabajadores comparten calendario de 8 h. Al registrar ausencia lunes–viernes para uno, solo él queda con cero horas esos cinco días; el otro y el calendario original conservan 40 h. Una disponibilidad parcial de 4 h conserva las otras fechas y aparece como tal en la vista previa. Cancelar o provocar un error antes del commit no deja calendario huérfano ni cambio de recurso. Las actividades ya asignadas aparecen para revisión, sin mover sus fechas.
- **Por qué no duplica P1–P4:** Hoy se editan excepciones por calendario, no se ofrece el flujo de ausencia por recurso con aislamiento automático del compartido. P1-05 repara el programa después de una perturbación; aquí solo se registra esa disponibilidad. P4-02 edita actividades y no cubre calendarios personales.

### P5-11 Consulta «Dónde se usa» para calendarios y recursos

- **Problema real:** Antes de cambiar un calendario 7×7, el planificador necesita saber qué obras y tareas lo utilizan. El calendario es compartido, y revisar solo la pantalla del proyecto abierto oculta parte de su alcance.
- **Propuesta concreta:** Acción de consulta desde Calendarios y Recursos que liste referencias por proyecto y tipo. Para calendario: proyectos, actividades, tareas periódicas y recursos con referencia explícita, más actividades que lo heredan del proyecto, distinguiendo ambos casos y evitando duplicados. Para recurso: asignaciones explícitas y referencias fijas en tareas periódicas; las demandas genéricas por habilidad se muestran solo como candidatas, sin atribuirles una reserva. Incluir recuento y navegación al objeto. Mostrar esta consulta antes de editar un calendario compartido, sin cambiar las reglas de guardado. Primera versión de referencias actuales: no simula fechas futuras ni reconstruye usos históricos en bases.
- **Dónde:** `reference_usage.py` (**nuevo**), `repo.py`, `api/resources.py`, `gui/client.py`, `gui/pages/calendars.py`, `gui/pages/resources.py` y diálogo `gui/reference_usage.py` (**nuevo**).
- **Valor:** medio — previene modificaciones de alcance inesperado, especialmente con calendarios compartidos.
- **Esfuerzo:** M, 3 días.
- **Riesgo:** bajo — presentar una referencia heredada como explícita o contar una demanda por habilidad como compromiso nominal. La tabla debe declarar qué clase de relación muestra.
- **Criterio de aceptación medible:** Un calendario usado por dos proyectos, tres actividades explícitas y cuatro que lo heredan lista exactamente esos siete IDs de actividad y ambos proyectos, sin duplicarlos. Una actividad con calendario propio diferente no aparece como heredera. Un recurso con dos asignaciones y una tarea periódica fija muestra tres referencias tipificadas; una demanda genérica coincidente no aumenta ese total. Abrir la consulta no modifica datos.
- **Por qué no duplica P1–P4:** P4-05 controla concurrencia; P1-15 reserva capacidad entre proyectos. Esta función responde dónde están las referencias antes de editar un maestro, sin resolver conflictos ni reservar capacidad. La vista actual de recursos y el calendario anual no entregan este inventario inverso entre proyectos.

## Revisión normativa para jornadas y feriados

Estas referencias delimitan las decisiones anteriores; no convierten Ganttier en un sistema de asistencia o cálculo de remuneraciones. Se consultaron fuentes oficiales para esta revisión. La aplicabilidad contractual y la resolución de cada faena quedan **a verificar** antes de codificar reglas automáticas.

- **Jornada ordinaria:** Código del Trabajo, **arts. 22 y 28**, y Ley 21.561, **artículo primero transitorio**. Para el régimen general sujeto a esa reducción, desde el 26-04-2026 el límite semanal es 42 h, con la siguiente etapa de 40 h desde el 26-04-2028. No adoptar 45 h como constante ni suponer que una plantilla de 12 h diarias es una jornada ordinaria autorizada. [DT, ORD. 253/21](https://www.dt.gob.cl/legislacion/1624/w3-article-129189.html), [DT, límites de jornada](https://www.dt.gob.cl/portal/1628/w3-article-60058.html).
- **Horas extraordinarias:** Código del Trabajo, **arts. 30, 31 y 32**: definición respecto de la jornada legal o pactada menor, límites y condiciones del trabajo extraordinario, pacto y remuneración. La carga que excede la capacidad configurada de un recurso es una estimación de planificación; no acredita horas extraordinarias efectivamente trabajadas. Por eso se descarta un cálculo laboral automático S/M con los datos actuales. Casos particulares, compensaciones y reglas contractuales: **a verificar**. [DT, jornada extraordinaria](https://dt.gob.cl/portal/1628/w3-article-60151.html), [DT, ORD. 771, que desarrolla los arts. 30–32](https://www.dt.gob.cl/legislacion/1624/w3-article-127018.html).
- **Descansos y sistemas excepcionales:** Código del Trabajo, **art. 35**, sobre descanso dominical y festivo, y **art. 38**, sobre excepciones y sistemas excepcionales de distribución de jornada y descansos. Introducir 7×7, 10×10 o 14×14 en el programa no prueba contar con autorización aplicable. Tratamiento del traslado, trabajo nocturno y resolución específica: **a verificar** con sus antecedentes; no se presume un recargo nocturno uniforme ni que todo traslado sea descanso. [Código del Trabajo, texto BCN](https://www.bcn.cl/leychile/navegar?idNorma=207436), [DT, autorización de jornada excepcional](https://dt.gob.cl/portal/1628/w3-article-60106.html).
- **Feriados territoriales e irrenunciables:** La Ley 20.663, **artículo único**, establece el 7 de junio para Arica y Parinacota. La Ley 19.973, **art. 2**, regula los feriados irrenunciables de los dependientes del comercio, con excepciones; esa categoría no se extiende automáticamente a todos los trabajadores eléctricos. Nuevos feriados extraordinarios y otras reglas regionales/comunales fuera del piloto: **a verificar** individualmente antes de incorporarlos. [Ley 20.663](https://www.bcn.cl/leychile/navegar?idNorma=1050508), [Ley 19.973](https://www.bcn.cl/leychile/navegar?idNorma=230132).

## Tabla ordenada por valor/esfuerzo

Orden: valor alto antes de medio; dentro de cada valor, menor límite superior de días estimados primero; empate por ID. S/M son categorías de esfuerzo, no una puntuación numérica de rentabilidad. Ninguna de las funciones seleccionadas requiere integrar SAP PM, Maximo, P6 o MS Project: los intercambios corporativos y los flujos propios de CMMS/EAM permanecen fuera de estas entregas.

| Orden | ID | Función | Valor | Esfuerzo | Días estimados | Riesgo |
|---:|---|---|---|---|---:|---|
| 1 | P5-09 | Cadenas desde lista ordenada | alto | M | 3 | medio |
| 2 | P5-06 | Búsqueda entre proyectos | alto | M | 3–4 | bajo |
| 3 | P5-07 | Gantt de celdas en Excel | alto | M | 4 | medio |
| 4 | P5-10 | Ausencias personales por intervalo | alto | M | 4 | medio |
| 5 | P5-01 | Plan semanal nominal | alto | M | 4–5 | medio |
| 6 | P5-02 | Clasificaciones y UDF | alto | M | 5 | medio |
| 7 | P5-03 | Paquetes reutilizables | alto | M | 5 | medio |
| 8 | P5-08 | Calculadora laborable | medio | S | 1–2 | bajo |
| 9 | P5-11 | Dónde se usa un maestro | medio | M | 3 | bajo |
| 10 | P5-04 | Renumeración de códigos | medio | M | 3–4 | medio |
| 11 | P5-05 | Feriados territoriales | medio | M | 4 | medio |

## Top-5 recomendado

1. **P5-01 — Plan semanal nominal.** Resuelve un entregable recurrente para los supervisores y aprovecha asignaciones ya disponibles. Priorizarlo sobre otros formatos si hoy se arma una planilla manual cada semana.
2. **P5-06 — Búsqueda entre proyectos.** Beneficio inmediato en ambos modos, sin migración ni cambio del motor. Tiene el menor riesgo del grupo de valor alto.
3. **P5-09 — Cadenas desde lista ordenada.** La entrega de valor alto más corta: reduce el trabajo de construir redes antes de programar y reutiliza el rechazo de ciclos existente.
4. **P5-10 — Ausencias personales.** Cierra un problema cotidiano de disponibilidad sin modificar calendarios compartidos ni esperar la reprogramación incremental de P1-05.
5. **P5-03 — Paquetes reutilizables.** Mayor ahorro al preparar obras repetitivas de paños, protecciones y montaje; conviene pilotear con un paquete pequeño revisado por el planificador responsable.

El top-5 pondera frecuencia de uso y contexto eléctrico, además del orden mecánico de la tabla. En una cartera exclusivamente de mantenimiento, P5-07 puede sustituir a P5-03; en una cartera de una sola obra, P5-02 puede adelantarse a la búsqueda entre proyectos. P5-08 es una entrega S independiente que puede hacerse entre desarrollos mayores. Ninguna cifra de aceptación anterior se presenta como rendimiento ya medido.
