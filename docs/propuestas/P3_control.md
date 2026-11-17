# P3 — Propuestas de control y reportes

Fecha de revisión: 29-09-2026. Alcance: propuestas; ninguna implementación.

Se contrastaron [README](../../README.md), [manual](../manual_usuario.md), las decisiones técnicas y el código de `control/`, `reports/`, `excel_io/`, `autoreports.py`, servicios, dominio y persistencia. Cuando una decisión antigua difiere del código, se toma la implementación actual y su revisión posterior: por ejemplo, [revision_mantenimiento_control.md](../decisiones/revision_mantenimiento_control.md) sustituye varias reglas de `control.md`.

El punto de partida ya incluye:

- Varias líneas base y selección de una activa; fechas, duración, HH y costo por actividad. `services.create_baseline` no captura relaciones, calendarios, asignaciones ni holguras. `baseline_activities` depende de la actividad mediante borrado en cascada.
- Historial de avance, consumos incrementales, EVM, `EAC=BAC/CPI`, TCPI contra BAC y fecha por `IEAC(t)=PD/SPI(t)`. La curva S ya preserva duración remanente y se extiende cuando Earned Schedule pronostica un término posterior; tiene ancla exacta al corte.
- CPM y holguras vigentes, historial de corridas, desempeño por trabajador y resúmenes WBS en el Gantt. Control usa un calendario de proyecto y desempeño distribuye el plan por días corridos.
- Informes en cuatro formatos, semáforo que prioriza SPI(t), conclusiones, cinco mayores atrasos, sobrecarga, hitos, lookahead y Excel de desconexiones. `reports.common.sections` define secciones fijas; `services.build_report_data` usa el corte actual aunque el período solicitado sea antiguo.
- Emisión automática con deduplicación por período/formato, reintentos parciales y retención. El scheduler funciona con la aplicación abierta y atiende el último período cerrado de cada frecuencia; no conserva estados completos de cierres anteriores.
- Excel bidireccional, validación por celda, hoja `Avance`, pegado con mapeo de columnas y combinación por códigos. El merge de avances sustituye registros por actividad/fecha/recurso; no ofrece un circuito de partes de terreno con revisión y versiones.

Las quince mejoras siguientes amplían esas capacidades. En «Dónde», las rutas son relativas a `backend/ganttier/`; «nuevo» identifica archivos propuestos. El esfuerzo incluye persistencia, API, GUI, integración y pruebas del alcance descrito, para una persona familiarizada con el proyecto: S ≤2 días, M ≤1 semana, L ≤1 mes, XL >1 mes. Son estimaciones, no compromisos; las dependencias se indican cuando condicionan la entrega. Las pruebas de aceptación son futuras y no se presentan como ejecutadas.

### P3-01 Cierres de control reproducibles

- Problema que resuelve: Un informe de agosto regenerado en septiembre usa el corte y parte del programa vigentes; puede cambiar después de enviado al mandante aunque su período siga diciendo agosto.
- Propuesta: Crear cierres identificados por proyecto, corte y revisión, con copia inmutable de entradas, calendarios, línea base, resultados, versión de cálculo y contenido del informe.
  Todos los formatos de una emisión consumirán el mismo cierre; una corrección generará una revisión vinculada. Los cierres aprobados tendrán retención independiente de los archivos automáticos.
  El scheduler usará cierres disponibles para recuperar períodos omitidos; sin cierre válido señalará «pendiente», sin fabricar historia ni cambiar el corte del proyecto.
- Dónde: `control/closures.py` (nuevo), `domain.py`, `schema.sql`, `db.py`, `repo.py`, `services.py`, `autoreports.py`, `api/control.py`, `gui/pages/control.py`, `gui/pages/reports.py`, `reports/common.py`.
- Valor para el usuario: Alto — permite defender exactamente qué se informó y con qué datos en cada reunión de control.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Alto — exige coherencia transaccional y versionado de entradas; el historial actual no basta para reconstruir cierres antiguos completos.
- Criterio de aceptación: 1) Tras cerrar agosto y editar fechas, avances y calendario en septiembre, reemitir agosto conserva los mismos indicadores y filas en los cuatro formatos. 2) Una corrección crea revisión 2 sin alterar revisión 1 y un período sin cierre queda pendiente, sin usar datos actuales.
- Referencias: Evidencia local: [informes_automaticos.md](../decisiones/informes_automaticos.md), apartado «Períodos y fecha de corte», y `services.build_report_data`. Política de cierre propuesta para Ganttier; no se atribuye certificación normativa.

### P3-02 Comparación de líneas base y alcance

- Problema que resuelve: Activar una nueva línea base permite cambiar la referencia, pero no explica si la mejora del SPI proviene de recuperar plazo o de ampliar presupuesto y mover fechas; una actividad retirada puede desaparecer de la comparación.
- Propuesta: Comparar base contractual, revisión aprobada y programa vigente, mostrando variaciones de inicio/fin, duración, HH y costo, con altas y bajas separadas del alcance común.
  Preservar identidad, código, WBS y datos de actividades retiradas en nuevas versiones de base; agregar motivo, estado y responsable de aprobación. La activación será distinta de la aprobación.
  Mostrar slippage con signo en días corridos y, opcionalmente, días laborables del calendario de referencia congelado; emitir tabla y curvas comparativas sin sustituir la base activa al consultar.
- Dónde: `control/baseline_diff.py` (nuevo), `domain.py`, `schema.sql`, `db.py`, `repo.py`, `services.py`, `api/control.py`, `gui/pages/control.py`, `reports/common.py`, `reports/charts.py`.
- Valor para el usuario: Alto — evita que una reprogramación o cambio de alcance oculte atrasos frente al compromiso original.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Alto — hay que desacoplar versiones del borrado de actividades y declarar incompletas las bases antiguas sin metadatos recuperables.
- Criterio de aceptación: 1) Con una actividad movida +5 días, otra agregada por 20 HH y otra retirada de 10 HH, se informan las tres causas y el cambio neto de presupuesto +10 HH. 2) Renombrar o retirar una actividad conserva su identidad histórica y consultar dos bases no cambia la activa.
- Referencias: [GAO Schedule Assessment Guide, GAO-16-89G](https://www.gao.gov/products/gao-16-89g), fundamento general de medición frente al plan aprobado; diseño concreto de comparación propuesto aquí.

### P3-03 Control según calendarios efectivos

- Problema que resuelve: Una cuadrilla 7×7 y otra L–V pueden parecer atrasadas bajo un único calendario; una jornada de 4 horas recibe hoy el mismo peso temporal que una de 8 horas en la distribución de PV.
- Propuesta: Resolver el calendario efectivo por actividad y el del recurso en desempeño, reutilizando la resolución del motor; calcular PV con distribución por horas laborables cuando esa sea la política aprobada.
  Versionar esa política y los calendarios con la base/cierre; conservar una opción explícita de reparto uniforme por día para contratos que la utilicen. Desempeño podrá elegir referencia vigente o aprobada y mostrar HH sin atribuir.
  Mantener el corte al fin de jornada y remanente desde el siguiente día hábil de cada actividad; las bases antiguas mostrarán qué calendario fue supuesto al migrarlas.
- Dónde: `control/_core.py`, `control/performance.py`, `calendars.py`, `services.py`, `domain.py`, `schema.sql`, `db.py`, `gui/pages/control.py`, `gui/pages/performance.py`; coordinación con P3-01/P3-02.
- Valor para el usuario: Alto — elimina desviaciones artificiales en obras eléctricas que mezclan turnos, feriados y jornadas parciales.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Alto — cambia resultados numéricos y requiere preservar comparabilidad histórica sin perder rendimiento en mantenimiento masivo.
- Criterio de aceptación: 1) Una tarea de 12 HH distribuida en jornadas de 4 y 8 horas gana PV=4 y PV=12 al cierre respectivo con política por horas. 2) Un caso mixto 7×7/L–V coincide con un cálculo independiente por actividad y conserva exactamente el BAC total.
- Referencias: Limitación explícita en [revision_mantenimiento_control.md](../decisiones/revision_mantenimiento_control.md) y contratos de `control_result`/`worker_performance`; la política de distribución debe acordarse con el usuario, no se presenta como obligación universal de EVM.

### P3-04 Pronósticos con métodos y supuestos

- Problema que resuelve: El EAC actual extrapola CPI y la fecha EVM extrapola SPI(t); no permiten distinguir un sobrecosto excepcional de baja productividad persistente ni incorporar una nueva estimación del remanente.
- Propuesta: Conservar el método actual y comparar `AC+(BAC−EV)`, `AC+(BAC−EV)/(CPI×SPI)` y `AC+ETC_estimado`; añadir TCPI contra el EAC elegido y justificación del método.
  Exponer la duración IEAC(t) ya implícita en la fecha y añadir `AT+(PD−ES)` como escenario de desempeño futuro normal, junto a `PD/SPI(t)`. Mostrar también término CPM y término de la curva combinada, con etiquetas distintas.
  Guardar hipótesis por cierre, unidades y causas de «no calculable»; los escenarios no modificarán fechas ni presupuestos. La dispersión entre métodos no se presentará como intervalo probabilístico.
- Dónde: `control/evm.py`, `control/forecasts.py` (nuevo), `domain.py`, `services.py`, `api/control.py`, `gui/pages/control.py`, `reports/common.py`.
- Valor para el usuario: Alto — convierte el pronóstico en una decisión explicable sobre recursos, remanente y presupuesto.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — las fórmulas son simples, pero deben manejar ceros, trabajo terminado, bases HH/costo y supuestos incompatibles sin resultados engañosos.
- Criterio de aceptación: 1) Con BAC=100, PV=50, EV=40, AC=50 y ETC estimado=65, los EAC son 125 actual, 110 a presupuesto, 143,75 combinado y 115 reestimado. 2) Con PD=20, AT=10 y ES=8, las duraciones son 25 y 22 días; SPI(t)=0 produce estado no calculable en el método que divide por él.
- Referencias: [PMI: Earned value—the good, the bad, and the ugly](https://www.pmi.org/learning/library/earned-value-controlling-forecasting-performance-7653), [PMI: caso EVM a precio fijo](https://www.pmi.org/learning/library/managing-fixed-price-projects-evm-8142) y [Earned Schedule: calculadoras y métodos](https://www.earnedschedule.com/calculator.shtml). La aplicación contractual del factor CPI×SPI debe justificarse; no es un pronóstico universal.

### P3-05 Evolución de criticidad y holgura

- Problema que resuelve: El Gantt muestra las críticas actuales, pero no permite detectar que el montaje de protecciones consumió cinco días de holgura durante tres cortes y ahora amenaza la energización.
- Propuesta: Guardar en cada cierre las relaciones, fechas tempranas/tardías, holguras, restricciones y resultado CPM; mostrar entradas/salidas de criticidad, consumo de holgura y tendencia de hitos seleccionados.
  Ofrecer seguimiento de actividades cercanas a criticidad con umbral configurable y navegación a las relaciones que gobiernan el hito. Separar el resultado lógico CPM de desplazamientos impuestos por recursos o ventanas.
  Reutilizar `engine/cpm.py`; en mantenimiento sin red lógica mostrar «no aplica», en vez de inventar ruta crítica. Depende de los cierres de P3-01.
- Dónde: `control/critical_history.py` (nuevo), `engine/cpm.py` (consumidor del resultado), `services.py`, `domain.py`, `gui/pages/control.py`, `gui/gantt/`, `reports/common.py`, `reports/charts.py`.
- Valor para el usuario: Alto — identifica dónde intervenir antes de que un atraso local mueva la fecha contractual.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — calendarios y restricciones pueden generar holgura negativa o múltiples caminos; la vista debe conservar el contexto de cálculo.
- Criterio de aceptación: 1) En dos ramas paralelas, una de ellas con cinco días de holgura que se alarga seis, el nuevo cierre muestra consumo, cambio de criticidad y desplazamiento del hito consistente con CPM. 2) Consultar el cierre anterior conserva su red y sus holguras aunque se editen las relaciones vigentes.
- Referencias: [GAO Schedule Assessment Guide](https://www.gao.gov/products/gao-16-89g), referencia general de confiabilidad del programa; semántica de CPM propia documentada en [revision_engine.md](../decisiones/revision_engine.md).

### P3-06 Bitácora de cambios y causas de atraso

- Problema que resuelve: Una nota de avance no permite reconstruir quién cambió la fecha, qué autorización faltaba ni qué acción se acordó; «responsable de la tarea» no necesariamente explica una desconexión postergada.
- Propuesta: Registrar cambios relevantes con antes/después, operador declarado, fecha de registro, origen —GUI, importación o corrida— y motivo; conservar los eventos al corregir o retirar datos.
  Vincular causas tipificadas —materiales, ingeniería, permisos, desconexión, clima o ejecución— a actividades y períodos, con evidencia, responsable de resolver, compromiso y estado. Separar causa declarada, validada y disputada.
  Incorporar al informe una matriz de variación, causa y acción; los días de causas concurrentes no se sumarán como atraso del proyecto ni se adjudicará responsabilidad contractual automáticamente.
- Dónde: `control/change_log.py`, `control/delay_causes.py` (nuevos), `schema.sql`, `db.py`, `repo.py`, `services.py`, `api/control.py`, `gui/pages/control.py`, `reports/common.py`.
- Valor para el usuario: Alto — reduce discusiones sin respaldo y permite dar seguimiento a compromisos de mandante, contratista y proveedores.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — todas las vías de escritura deben emitir eventos coherentes; identificar un operador local no equivale a autenticar una firma.
- Criterio de aceptación: 1) Cambiar una fecha por GUI, Excel y aplicación de corrida produce tres eventos con valores anteriores y posteriores correctos. 2) Dos causas superpuestas conservan su evidencia y acciones, sin presentar su suma como impacto contractual demostrado.
- Referencias: Diseño de trazabilidad propuesto; referencias específicas AACE de análisis forense de atrasos, edición y aplicabilidad: **a verificar** antes de incorporar métodos periciales.

### P3-07 Avance físico sustentado en mediciones

- Problema que resuelve: Declarar «80 % del tendido» sin metros aceptados ni criterios verificables infla EV y debilita el respaldo del informe; los hitos 0/100 existentes no cubren actividades con entregables parciales.
- Propuesta: Permitir por actividad porcentaje manual justificado, cantidad ejecutada/aceptada sobre cantidad aprobada, o entregables ponderados cuya suma sea 100 %; mantener el 0/100 actual para hitos.
  Registrar unidad, medición, evidencia y aceptación; el porcentaje calculado alimentará el mismo historial y los motores actuales. El rechazo/retrabajo podrá reducir avance mediante una corrección trazable.
  Versionar cantidades y ponderaciones: aumentar el alcance requerirá una revisión explícita, sin cambiar retroactivamente EV de un cierre aprobado.
- Dónde: `control/measurement.py` (nuevo), `control/progress.py`, `domain.py`, `schema.sql`, `db.py`, `repo.py`, `api/control.py`, `gui/pages/control.py`, `excel_io/schema.py`, `reports/common.py`.
- Valor para el usuario: Alto — aporta evidencia objetiva para controlar montaje, cableado, pruebas y aceptación de equipos.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — debe resolver unidades y revisiones de alcance, sin duplicar EV al combinar mediciones y registros manuales.
- Criterio de aceptación: 1) Aceptar 300 de 1.000 metros produce 30 % y EV=30 % del presupuesto, independientemente de las HH consumidas. 2) Entregables de 20/30/50 % con los dos primeros aceptados producen 50 %; rechazar el segundo deja 20 % y conserva la evidencia anterior.
- Referencias: Propuesta funcional basada en el contrato de `ProgressEntry`; técnicas y edición aplicable del *PMI Practice Standard for Earned Value Management*: **a verificar** antes de alegar conformidad.

### P3-08 Partes de terreno con revisión

- Problema que resuelve: La hoja Avance ya importa consumos, pero dos capataces pueden entregar correcciones incompatibles para la misma actividad/fecha; el merge actual no representa una revisión del parte.
- Propuesta: Añadir un flujo dedicado «exportar parte → recibir → revisar diferencias → aprobar», limitado a actividades/frente y corte, con identificador estable de parte/fila, versión de origen y estado de revisión.
  Reutilizar lectura, validaciones y mapeos Excel existentes; distinguir porcentaje acumulado de HH/costo incrementales, conservar varios consumos válidos del mismo día y detectar reenvíos o conflictos.
  Mantener los lotes recibidos fuera de los datos aprobados hasta resolver errores; aplicar el lote aceptado atómicamente y devolver observaciones por fila. Incorporar mediciones P3-07 cuando estén disponibles.
- Dónde: `excel_io/field_progress.py` (nuevo), `excel_io/importer.py`, `excel_io/schema.py`, `excel_io/templates.py`, `repo.py`, `schema.sql`, `db.py`, `api/files.py`, `gui/pages/importexport.py`, `gui/pages/control.py`.
- Valor para el usuario: Alto — reduce digitación y evita duplicar HH o perder correcciones al consolidar avances de varias cuadrillas.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — la idempotencia debe basarse en identificadores persistentes y versiones, no solo en nombre de archivo o fecha.
- Criterio de aceptación: 1) Aprobar dos veces el mismo parte agrega exactamente una vez sus 16 HH y su costo. 2) Dos filas independientes de 8 HH para la misma actividad/fecha/recurso suman 16 HH, mientras una modificación concurrente del porcentaje queda pendiente y no sobrescribe el avance aprobado.
- Referencias: Evidencia local: hoja `Avance` de `excel_io/schema.py`, merge en `repo.py` y [ux_UX3.md](../decisiones/ux_UX3.md). La mejora es conciliación y aprobación, no otro importador genérico.

### P3-09 Estados de pago y facturación

- Problema que resuelve: EV valorizado al presupuesto interno y AC no indican cuánto puede cobrarse: un equipo puede estar instalado, pendiente de recepción y todavía no ser facturable.
- Propuesta: Crear partidas contractuales vinculadas a actividades/entregables, con cantidades, precios de venta o hitos de pago; registrar solicitado, aprobado, facturado y cobrado con fechas y respaldos.
  Construir estados por período y acumulados, descontando cantidades ya certificadas y aplicando retenciones/anticipos expresamente configurados. Mostrar curvas de venta planificada, certificación, facturación y cobro separadas de PV/EV/AC.
  Acotar la primera versión a una moneda por contrato, importes decimales y exportación PDF/Excel; los impuestos y reajustes se ingresarán según contrato, sin integrar facturación tributaria ni contabilidad general.
- Dónde: `control/payments.py`, `reports/payments.py` (nuevos), `domain.py`, `schema.sql`, `db.py`, `repo.py`, `api/control.py`, `gui/pages/payments.py` (nuevo), `gui/app.py`, `excel_io/schema.py`.
- Valor para el usuario: Alto — vincula el avance aceptado con cobros y permite anticipar necesidades de caja en contratos de obra.
- Esfuerzo: XL.
- Riesgo técnico: Alto — el presupuesto de costo actual no representa precios de venta, y las reglas de certificación varían entre contratos.
- Criterio de aceptación: 1) Con 100 unidades contratadas, 30 ya certificadas y 50 aceptadas acumuladas, el estado siguiente cobra solo 20; su reemisión no las duplica. 2) Sobre 2.000.000 CLP brutos y retención contractual del 5 %, sin otros ajustes, se muestran 100.000 retenidos y 1.900.000 pagables, sin alterar AC ni EV.
- Referencias: Bases de medición y pago del contrato del usuario: **a verificar**. No se presupone un formato legal chileno único ni equivalencia entre EVM y facturación.

### P3-10 Plantillas por destinatario

- Problema que resuelve: El informe fijo obliga a editar Word antes de enviar: el mandante necesita hitos y decisiones, mientras el contratista necesita productividad, costos internos y acciones por cuadrilla.
- Propuesta: Crear plantillas versionadas con secciones, columnas, filtros, orden, logotipo, textos y bloques de conclusiones revisables; comenzar con perfiles «mandante» y «contratista» y una vista previa común.
  Reutilizar `ReportData` y los cuatro renderizadores con una definición declarativa, sin fórmulas arbitrarias ni un editor visual de documentos completo. El scheduler guardará plantilla y versión por emisión.
  El perfil mandante omitirá por configuración los costos internos y nombres individuales también en anexos; ambos perfiles mostrarán cierre, alcance, base y método. Los bloques de mejoras aún no instaladas se indicarán como no disponibles.
- Dónde: `reports/templates.py` (nuevo), `reports/common.py`, `reports/builder.py`, `reports/pdf.py`, `reports/docx.py`, `reports/html.py`, `reports/xlsx.py`, `services.py`, `autoreports.py`, `gui/pages/reports.py`.
- Valor para el usuario: Alto — reduce preparación manual recurrente y evita inconsistencias entre informes internos y entregables al cliente.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — mantener equivalencia entre formatos y aplicar exclusiones a todos los anexos exige un modelo común de presentación.
- Criterio de aceptación: 1) Dos perfiles del mismo cierre conservan idénticos KPI compartidos y el de mandante no contiene nombres individuales ni costos internos en ninguno de los cuatro archivos. 2) Reordenar secciones y guardar una plantilla cambia los cuatro formatos; reemitir una versión antigua conserva su orden original.
- Referencias: Arquitectura existente de `reports.common.sections` y `reports.builder`; propuesta de producto, sin una norma externa necesaria. Los perfiles de salida no se presentan como un sistema de permisos multiusuario.

### P3-11 Control por WBS, frente y disciplina

- Problema que resuelve: Un SPI global aceptable puede ocultar el atraso del paño de 220 kV bajo avance de obras civiles; los resúmenes WBS del Gantt y KPI por trabajador no entregan ese diagnóstico EVM.
- Propuesta: Agregar exploración de curvas y EVM por WBS, instalación, disciplina y responsable del paquete, con navegación a las actividades que explican SV/CV y conciliación con el total.
  Recalcular índices desde PV/EV/AC agregados y Earned Schedule desde la curva agregada; declarar alcance, base, moneda, actividades sin clasificar y cobertura de datos.
  Guardar filtros como vistas reutilizables en reportes P3-10; impedir sumar grupos superpuestos o mezclar HH y costos. Para historia comparable, fijar la clasificación con P3-01.
- Dónde: `control/aggregation.py` (nuevo), `control/evm.py`, `control/scurve.py`, `services.py`, `domain.py`, `api/control.py`, `gui/pages/control.py`, `reports/common.py`, `reports/charts.py`.
- Valor para el usuario: Alto — dirige la revisión semanal al frente que compromete el proyecto y permite asignar acciones concretas.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — los índices no son aditivos; filtros y clasificaciones históricas pueden producir doble conteo o aparentes cambios de desempeño.
- Criterio de aceptación: 1) Dos paquetes con EV/PV de 10/20 y 90/100 producen SPI global 100/120=0,8333, no el promedio 0,7. 2) Paquetes disjuntos más «sin clasificar» concilian BAC/PV/EV/AC con el total dentro de tolerancia numérica 1e-6.
- Referencias: Fórmulas de agregación derivadas del contrato EVM existente en [revision_mantenimiento_control.md](../decisiones/revision_mantenimiento_control.md); no se requiere una nueva definición normativa de SPI/CPI.

### P3-12 Intercambio con MS Project XML

- Problema que resuelve: Recibir el programa de un contratista en MS Project exige reconstruir WBS, relaciones y calendarios en Excel, con pérdida de trazabilidad frente al archivo recibido.
- Propuesta: Implementar importación y exportación MSPDI XML para un subconjunto declarado: WBS, tareas/hitos, dependencias, restricciones compatibles, calendarios, asignaciones, fechas reales, avance y una base seleccionada.
  Guardar UID externo, versión y archivo de origen; ofrecer previsualización de diferencias y pérdidas antes de aplicar. La programación recibida se conservará como referencia y cualquier recálculo será explícito.
  Definir conversión de horas/duración y fecha de estado al corte de fin de jornada; rechazar o marcar tareas partidas, precisión intradía y tipos no representables. No incluir lectura de `.mpp`.
- Dónde: `interchange/msproject_xml.py`, `interchange/mapping.py` (nuevos), `domain.py`, `repo.py`, `schema.sql`, `db.py`, `services.py`, `api/files.py`, `gui/pages/importexport.py`.
- Valor para el usuario: Medio — ahorra reprocesamiento cuando la contraparte usa Project; pierde prioridad si todo el intercambio actual es Excel.
- Esfuerzo: XL.
- Riesgo técnico: Alto — Ganttier usa fechas diarias y MS Project puede expresar horas, tareas partidas y semánticas de cálculo diferentes.
- Criterio de aceptación: 1) Un banco acordado de 50 tareas compatibles con WBS, cuatro tipos de vínculo, feriado, hito y base completa ida y vuelta sin perder UID, relaciones, presupuestos ni fechas diarias. 2) Un caso con precisión intradía no representable informa cada pérdida y no aplica conversiones silenciosas; el XML exportado se abre en la versión de Project declarada.
- Referencias: [Microsoft: Project XML Data Interchange Schema Reference](https://learn.microsoft.com/en-us/office-project/xml-data-interchange/project-xml-data-interchange-schema-reference?view=project-client-2016). Versión objetivo del producto y compatibilidad de cada campo: **a verificar** con archivos reales y prueba en Project.

### P3-13 Intercambio con Primavera P6

- Problema que resuelve: Un mandante entrega XER o solicita XML de P6; convertirlo a una planilla elimina información necesaria para contrastar la programación contractual y sus revisiones.
- Propuesta: Construir un adaptador P6 con matriz de cobertura por formato/versión: primera entrega, lectura XER/XML; segunda, escritura XML; tercera, escritura XER del subconjunto validado.
  Mapear proyectos, WBS, actividades, relaciones, calendarios, recursos, fechas/corte y presupuestos; mantener IDs externos y reportar diferencias por avance físico/duración/unidades, tipos de actividad, desfases y calendarios no representables.
  Usar XML para intercambio de bases cuando el producto destino lo soporte; no prometer bases en XER exportado por P6 Professional. Aplicar en un proyecto de revisión o como cambios conciliados, reutilizando P3-12 para identidad y diagnóstico.
- Dónde: `interchange/p6_xer.py`, `interchange/p6_xml.py`, `interchange/coverage.py` (nuevos), `interchange/mapping.py`, `schema.sql`, `db.py`, `repo.py`, `services.py`, `api/files.py`, `gui/pages/importexport.py`.
- Valor para el usuario: Medio — puede ser decisivo en contratos con P6, pero requiere una contraparte concreta y archivos de aceptación para justificar su costo.
- Esfuerzo: XL.
- Riesgo técnico: Alto — difieren versiones, productos P6, tipos de porcentaje y calendarios; la equivalencia del cálculo no queda garantizada por leer o escribir el archivo.
- Criterio de aceptación: 1) En un banco validado en P6 con 100 actividades, el subconjunto soportado conserva IDs, WBS, vínculos, presupuesto y fechas de referencia tras ida y vuelta, con diferencias de recálculo documentadas. 2) Cada campo/tipo no soportado aparece en el diagnóstico y los archivos XML/XER exportados se reabren en la versión objetivo sin error estructural.
- Referencias: [Oracle: exportación P6 Professional a XML o XER](https://docs.oracle.com/cd/E80480_01/English/admin/p6_import_guide/190894.htm), incluida su limitación de exportar bases en XER, y [mapas de intercambio P6](https://docs.oracle.com/cd/F25600_01/import_export_data_mappings.htm). Cobertura exacta por versión: **a verificar** antes de comprometer compatibilidad.

### P3-14 Alertas accionables sin duplicados

- Problema que resuelve: El semáforo y las conclusiones existentes se ven al abrir el control o el informe; nadie recibe una señal cuando un hito empeora, falta avance reciente o vence una acción correctiva.
- Propuesta: Incorporar reglas configurables sobre cambios entre cortes, atraso de hitos, holgura, datos desactualizados y compromisos vencidos, con evidencia del cálculo, responsable, prioridad y enlace al detalle.
  Crear una bandeja y notificación local con estados nueva/reconocida/resuelta, deduplicación por regla/objeto/cierre y recordatorios acotados. Evaluar al aprobar un cierre y en el scheduler existente, con la aplicación abierta.
  Dejar el correo para una segunda etapa condicionada a necesidad real; la primera entrega no requiere cuentas externas ni una tarea de Windows. Los indicadores ausentes generarán una alerta de datos, no un semáforo verde.
- Dónde: `control/alerts.py` (nuevo), `schema.sql`, `db.py`, `repo.py`, `api/control.py`, `autoreports.py`, `gui/pages/control.py`, `gui/app.py`; reutiliza cierres P3-01 y causas P3-06 cuando existan.
- Valor para el usuario: Alto — adelanta la intervención y convierte la desviación en una acción con seguimiento.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — las reglas pueden generar ruido y su evaluación repetida debe conservar estado sin bloquear la GUI ni el scheduler.
- Criterio de aceptación: 1) Evaluar diez veces el mismo cierre con un hito fuera de tolerancia crea una sola alerta; reconocerla y reiniciar conserva su estado. 2) Un cierre posterior dentro de tolerancia la resuelve, mientras un SPI(t) ausente muestra falta de datos y nunca «cumple».
- Referencias: Arquitectura local de `autoreports.py` y semáforos de `reports/common.py`; tolerancias de negocio configurables, sin umbrales normativos presumidos.

### P3-15 Diagnóstico de calidad del programa

- Problema que resuelve: Un programa puede superar el verificador de factibilidad y aun ser débil para control: tareas abiertas, restricciones que ocultan lógica, duraciones excesivas o avance inconsistente dificultan confiar en el pronóstico.
- Propuesta: Añadir un diagnóstico explicable con reglas de lógica incompleta, leads/lags, restricciones duras, duraciones/holguras extremas, fechas reales futuras, actividades vencidas y falta de recursos; cada hallazgo mostrará regla, población, excepción y acción sugerida.
  Usar perfiles distintos para proyecto y mantenimiento; excluir hitos frontera justificados y marcar reglas no aplicables. Consultar los verificadores existentes, sin volver a implementar comprobaciones de factibilidad ya cubiertas.
  Incorporar pruebas de sensibilidad de ruta crítica sobre una copia en memoria; ampliar hacia un perfil inspirado en DCMA 14 puntos solo tras fijar fuente, umbrales y fórmulas. El primer diagnóstico no se denominará certificación DCMA.
- Dónde: `control/schedule_quality.py` (nuevo), `engine/verify.py` y `engine/cpm.py` (reutilización), `services.py`, `api/control.py`, `gui/pages/control.py`, `reports/common.py`, `reports/xlsx.py`.
- Valor para el usuario: Alto — descubre problemas de confiabilidad antes de aprobar una base o enviar un programa al mandante.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — aplicar umbrales indiscriminados produce falsos positivos, especialmente en mantenimiento sin dependencias; algunas pruebas requieren contexto contractual.
- Criterio de aceptación: 1) Un programa sintético con ocho anomalías de tipos distintos devuelve ocho hallazgos trazables y excluye sus dos hitos frontera justificados del denominador correspondiente. 2) El perfil de mantenimiento marca «no aplica» en lógica CPM cuando no existe red, y el análisis de sensibilidad no cambia una sola fecha persistida.
- Referencias: [GAO Schedule Assessment Guide](https://www.gao.gov/products/gao-16-89g), como guía general de evaluación; **DCMA 14-Point: fuente primaria, edición, fórmulas y umbrales a verificar** antes de ofrecer un perfil con ese nombre.

## Filtro de valor y dependencias

Se descartan como propuestas nuevas los informes semanales/mensuales/anuales, más formatos de exportación, las curvas S básicas, IEAC(t) básico, semáforos, conclusiones automáticas, lookahead, exportación de desconexiones, hoja Excel de avance y pegado con mapeo: ya existen. Tampoco se priorizan un editor visual libre de informes, correo masivo, un ERP contable, lectura nativa de `.mpp`, resúmenes generativos ni Monte Carlo sin datos de incertidumbre: añaden costo sin resolver primero la confianza en los datos y la trazabilidad.

P3-01 establece la referencia histórica común; P3-02/P3-03 deben acordar juntos el versionado de bases y calendarios. P3-05 aprovecha esos cierres, P3-08 puede entregar partes de porcentaje/HH antes de incorporar P3-07, y P3-09 requiere criterios de aceptación de cantidades o hitos. P3-10 consume los resultados disponibles de P3-11 y de las demás mejoras sin exigir implementarlas todas. P3-12/P3-13 se iniciarían únicamente con necesidad contractual confirmada y archivos de prueba de la contraparte. Los plazos no deben sumarse como si las dependencias fueran independientes.

## Tabla resumen

| ID | Título | Valor | Esfuerzo | Riesgo |
|---|---|---|---|---|
| P3-01 | Cierres de control reproducibles | Alto | L | Alto |
| P3-02 | Comparación de líneas base y alcance | Alto | L | Alto |
| P3-03 | Control según calendarios efectivos | Alto | L | Alto |
| P3-04 | Pronósticos con métodos y supuestos | Alto | L | Medio |
| P3-05 | Evolución de criticidad y holgura | Alto | L | Medio |
| P3-06 | Bitácora de cambios y causas de atraso | Alto | L | Medio |
| P3-07 | Avance físico sustentado en mediciones | Alto | L | Medio |
| P3-08 | Partes de terreno con revisión | Alto | L | Medio |
| P3-09 | Estados de pago y facturación | Alto | XL | Alto |
| P3-10 | Plantillas por destinatario | Alto | L | Medio |
| P3-11 | Control por WBS, frente y disciplina | Alto | L | Medio |
| P3-12 | Intercambio con MS Project XML | Medio | XL | Alto |
| P3-13 | Intercambio con Primavera P6 | Medio | XL | Alto |
| P3-14 | Alertas accionables sin duplicados | Alto | L | Medio |
| P3-15 | Diagnóstico de calidad del programa | Alto | L | Medio |

## Top-5 recomendado

1. **P3-01 — Cierres reproducibles.** Prioridad estructural: hace comparables los informes sucesivos, preserva evidencia y sirve de base a tendencias, alertas y revisiones.
2. **P3-03 — Calendarios efectivos.** Prioridad de exactitud: evita que los turnos y jornadas parciales produzcan desvíos artificiales en indicadores que ya guían decisiones. Definir su política antes de congelar las primeras bases nuevas.
3. **P3-02 — Comparación de líneas base y alcance.** Prioridad contractual: explica qué cambió frente al compromiso original y separa recuperación de plazo de modificaciones aprobadas.
4. **P3-06 — Bitácora y causas.** Prioridad de gestión: transforma un atraso cuantificado en evidencia, responsables de resolver y compromisos verificables; complementa directamente P3-02.
5. **P3-08 — Partes de terreno.** Prioridad operativa: mejora la calidad y frecuencia de entrada de datos, ahorra consolidación semanal y evita pérdidas o duplicaciones antes de añadir más análisis.
