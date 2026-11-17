# A2 — Auditoría de control y persistencia de datos

Fecha: 3 de octubre de 2026. Repositorio: `Ganttier/`.

Se identificaron **15 defectos confirmados: 9 de severidad alta y 6 media**. No se modificaron fuentes ni pruebas existentes. Se excluyen expresamente D1–D4. Las ubicaciones indicadas son relativas a la raíz del repositorio.

Las pruebas están en [`test_a2_control_datos.py`](../../_encargos/auditoria_tests/test_a2_control_datos.py). **Sus aserciones comprueban el comportamiento defectuoso observado**: que pasen confirma la reproducción, no que el producto esté corregido. Son 24 casos ejecutados: 17 reproducciones —incluidas tres variantes del scheduler— y 7 controles de contraste.

Ejecución desde `backend/`, con el Python del proyecto:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH=(Get-Location).Path
$env:TEMP=(Resolve-Path ../_encargos/auditoria_tests).Path
$env:TMP=$env:TEMP
$env:MPLCONFIGDIR=$env:TEMP
../.venv/Scripts/python.exe -B -m pytest ../_encargos/auditoria_tests/test_a2_control_datos.py -p no:cacheprovider --basetemp=../_encargos/auditoria_tests/.tmp_a2_final -q
```

Resultado: **24 passed, 1 warning**. La advertencia corresponde a la deprecación de la integración Starlette/httpx de TestClient. Las bases son independientes, en memoria salvo la prueba de concurrencia; los archivos temporales permanecen dentro de la carpeta de auditoría. No se ejecuta el lifespan de la aplicación ni su scheduler real.

Se revisaron los módulos solicitados de control, informes, Excel, API, servicios, repositorio, base de datos y esquema. Como contraste se comprobaron fechas `dd-mm-aaaa`, coma decimal y separadores de miles, rechazo de códigos duplicados y predecesoras inexistentes, filtros SQL y restricciones de descarga. Un caso numérico independiente verificó PV, EV, AC, SPI, CPI, ES, SPI(t), TCPI, EAC, ETC, VAC, fecha pronosticada y anclaje de la curva al corte; también se verificaron denominadores nulos. Estos controles no prueban ausencia general de defectos.

Se generaron los cuatro formatos con los mismos indicadores de entrada. Se verificaron las tablas guardadas en Excel, Word y HTML; para PDF se verificaron los valores entregados al renderizador y la generación del archivo, sin extracción textual ni inspección visual. No se afirma equivalencia exhaustiva de todos los documentos posibles.

### A2-01 Eliminar una tarea periódica borra sus actividades ejecutadas y su historial

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/schema.sql:135`, `backend/ganttier/schema.sql:197`; `backend/ganttier/repo.py:432`.
- **Escenario reproducible:** crear tarea periódica `T`, actividad `A` asociada, y registrar el 07-01-2026 un avance de 100%, 40 HH y costo 400. Ejecutar `DELETE /api/periodic-tasks/{id}`. No hay línea base en esta prueba.
- **Obtenido vs esperado:** HTTP 204; desaparecen la actividad y todos sus registros de avance. Se esperaba impedir el borrado de una tarea con ejecución, o retirar el maestro conservando actividades y consumos históricos. La cascada `periodic_tasks → activities → progress` destruye la trazabilidad de ejecución. Es independiente de D3.
- **Estado:** confirmado con prueba `test_a2_delete_periodic_task_cascades_executed_history`.
- **Corrección sugerida mínima:** rechazar el borrado cuando existan ocurrencias ejecutadas y usar el campo `active=False` para retirar el maestro. Proteger también la relación en el esquema para evitar eludir la regla desde otras rutas.

### A2-02 La importación merge borra fechas aunque esas columnas no existan en Excel

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/excel_io/importer.py:117`, `backend/ganttier/excel_io/importer.py:270`; `backend/ganttier/repo.py:594`.
- **Escenario reproducible:** actividad `A` existente, programada del 05 al 09-01-2026. Importar en modo `merge` un libro sin marcador de exportación, cuya hoja Actividades solo contiene `Codigo`, `Nombre`, `HH`, con valores `A`, `Actividad renombrada`, `40`.
- **Obtenido vs esperado:** HTTP 200 y nombre actualizado, pero `planned_start` y `planned_finish` quedan en NULL. Se esperaba conservar las fechas omitidas. `_defaults()` añade campos de dominio con valores iniciales, y el repositorio los interpreta como cambios explícitos. El mismo mecanismo alcanza otros campos ausentes.
- **Estado:** confirmado con prueba `test_a2_excel_template_merge_clears_existing_schedule`.
- **Corrección sugerida mínima:** mantener la distinción entre campo ausente y valor explícito; aplicar los valores por defecto al crear registros nuevos, no al preparar actualizaciones merge.

### A2-03 Completar el trabajador de una fila reimportada duplica HH y costos

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/repo.py:621`.
- **Escenario reproducible:** importar por HTTP una fila de Avance para `A`, fecha 07-01-2026, avance acumulado 50%, 8 HH, costo 80 y Trabajador vacío. Reimportarla idéntica: continúa en 8 HH. Completar únicamente Trabajador con `Ana` y volver a importar.
- **Obtenido vs esperado:** quedan dos registros, uno sin trabajador y otro de Ana; AC pasa de 8 a 16 HH y de 80 a 160 en costos. Se esperaba corregir la atribución conservando un solo consumo. La clave de reemplazo incluye `resource_id`, precisamente el atributo corregido, por lo que no encuentra la fila anterior.
- **Estado:** confirmado con prueba `test_a2_reimport_worker_correction_duplicates_consumption`.
- **Corrección sugerida mínima:** actualizar mediante identidad estable del registro cuando esté disponible. Para plantillas sin identidad, detectar esta colisión y rechazarla con diagnóstico antes de sumar otro consumo; no eliminar indiscriminadamente registros de otros trabajadores del mismo día.

### A2-04 Dos actualizaciones concurrentes válidas persisten un intervalo invertido

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/repo.py:377`, `backend/ganttier/repo.py:406`, `backend/ganttier/repo.py:413`; `backend/ganttier/api/dependencies.py:34`.
- **Escenario reproducible:** actividad originalmente del 01 al 10-01-2026. Dos conexiones ejecutan la ruta de validación de actualización: una cambia inicio a 08-01 y otra cambia fin a 04-01. Ambas leen y validan el estado original; la segunda confirma antes de que la primera entre en su transacción. La prueba controla ese intercalado con una barrera y un evento, sin sustituir SQLite ni la validación.
- **Obtenido vs esperado:** ambas operaciones terminan y la base conserva inicio 08-01, fin 04-01. Se esperaba rechazar una de las escrituras. La lectura y la validación del objeto combinado ocurren antes del SAVEPOINT; el bloqueo de la escritura no revalida el estado leído.
- **Estado:** confirmado con prueba `test_a2_concurrent_updates_persist_inverted_dates`.
- **Corrección sugerida mínima:** reservar la escritura antes de leer y validar el estado combinado, o usar control de versión y actualización condicional. La protección debe abarcar validación y escritura, con manejo compatible de transacciones anidadas.

### A2-05 Un presupuesto NULL importado se confirma y deja el control inutilizable

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/excel_io/importer.py:169`, `backend/ganttier/api/files.py:131`, `backend/ganttier/repo.py:141`.
- **Escenario reproducible:** libro marcado como `Ganttier intercambio 1`, actividad `A` del 05 al 09-01-2026 y `HH=\N`, el escape de NULL del intercambio. Importar mediante `POST /api/projects/{pid}/import`.
- **Obtenido vs esperado:** el parser no informa errores, el endpoint responde 200 y guarda `activities.work_hours=NULL`. Después, `/control/evm` responde 422 porque el presupuesto no es finito. Se esperaba rechazar el campo incompatible con `Activity.work_hours: float` antes de confirmar, o normalizarlo según una regla explícita. El camino de importación omite la validación Pydantic de cada actividad y la columna SQL admite NULL.
- **Estado:** confirmado con prueba `test_a2_excel_null_budget_commits_unusable_activity`.
- **Corrección sugerida mínima:** validar los nulos contra la nulabilidad del dominio antes de guardar el lote. Añadir restricciones de almacenamiento para campos numéricos obligatorios tras sanear datos existentes.

### A2-06 Una corrección de 100% a 50% sigue contabilizándose como 100% en EVM

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/control/progress.py:110`, `backend/ganttier/control/progress.py:131`.
- **Escenario reproducible:** actividad de 40 HH, corte 09-01-2026. Registrar por la API 100% el día 7 y luego corregir el avance acumulado a 50% el día 8 sin fin real. Ambas operaciones están admitidas.
- **Obtenido vs esperado:** `build_activity_views()` devuelve 50% y `actual_finish=None`, pero EVM devuelve EV=40 y avance 100%; se esperaba EV=20. `_actual_finishes()` omite registros sin fin y conserva el cierre anterior; `_current_at_cut()` fuerza entonces el 100%. El consumo no necesita duplicarse para producir el error.
- **Estado:** confirmado con prueba `test_a2_reopened_activity_keeps_obsolete_finish`.
- **Corrección sugerida mínima:** resolver el cierre a partir del estado vigente de cada actividad; una corrección posterior que reabre el trabajo debe invalidar el fin anterior. Mantener la misma resolución de estado en servicios y control.

### A2-07 La API acepta consumos acumulados de actividad que EVM descarta

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/api/planning.py:38`, `backend/ganttier/services.py:89`, `backend/ganttier/control/_core.py:180`.
- **Escenario reproducible:** crear por HTTP una actividad de 40 HH y presupuesto 400, avance 50%, inicio real 05-01-2026, `actual_hours=30` y `actual_cost=300`, sin filas en Progress. Es una entrada aceptada con HTTP 201; la vista de actividad conserva los consumos.
- **Obtenido vs esperado:** EV=20 HH, pero AC=0 tanto en base HH como en costos, en lugar de 30 y 300 respectivamente. CPI queda indefinido pese a existir consumo aceptado y almacenado. Control toma el avance del snapshot cuando no hay historial, pero los consumos exclusivamente de Progress.
- **Estado:** confirmado con prueba `test_a2_activity_snapshot_consumption_disappears_in_control`.
- **Corrección sugerida mínima:** cerrar la inconsistencia en la entrada: registrar esos consumos en el libro de avances con fecha válida, o rechazar su edición independiente indicando que deben registrarse como avance. Si se admite un respaldo desde el snapshot, limitarlo a actividades sin historial y definir su fecha para no duplicar consumos.

### A2-08 PV usa el calendario del proyecto e ignora el de la actividad

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/services.py:412`; `backend/ganttier/control/_core.py:173`.
- **Escenario reproducible:** proyecto con calendario lunes–viernes; actividad con calendario de siete días, duración 4 días, 32 HH, del viernes 09 al lunes 12-01-2026. Corte al cierre del viernes 9; sin línea base.
- **Obtenido vs esperado:** PV=16 HH, en lugar de 8 HH. La actividad tiene cuatro jornadas de 8 HH, pero el control reparte sus 32 HH únicamente entre viernes y lunes. La prueba calcula también PV con el calendario propio y obtiene 8. Esto altera SPI y la inversión de PV para Earned Schedule.
- **Estado:** confirmado con prueba `test_a2_activity_calendar_ignored_by_pv`.
- **Corrección sugerida mínima:** construir PV por calendario efectivo de cada actividad y sumar las series antes de calcular los índices y ES. Para líneas base, conservar el calendario aprobado o la distribución temporal necesaria para reproducir PV.

### A2-09 Una colisión de nombres sustituye silenciosamente el calendario importado

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/repo.py:545`; `backend/ganttier/services.py:589`.
- **Escenario reproducible:** existe `A2 L-V`, semana `1111100`, 8 horas/día. Importar un proyecto nuevo cuyo libro define un calendario con ese mismo nombre, semana `1111111`, 12 horas/día, y lo referencia desde proyecto y actividad.
- **Obtenido vs esperado:** HTTP 201; el nuevo proyecto utiliza `1111100` y 8 horas. Se esperaba conservar las características del calendario del libro o rechazar explícitamente el conflicto. La resolución por nombre reutiliza el ID existente sin comprobar equivalencia. No se propone sobrescribir un calendario compartido.
- **Estado:** confirmado con prueba `test_a2_import_calendar_name_collision_silently_changes_schedule`.
- **Corrección sugerida mínima:** comparar definición y excepciones antes de reutilizar un nombre. Rechazar diferencias indicando el calendario en conflicto; el usuario puede resolver el nombre antes de reimportar.

### A2-10 El scheduler pierde períodos intermedios después de una interrupción

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/autoreports.py:87`, `backend/ganttier/autoreports.py:151`.
- **Escenario reproducible:** habilitar un tipo de informe y ejecutar en las dos fechas de cada fila:

| Tipo | Primera ejecución | Siguiente ejecución | Período omitido |
|---|---|---|---|
| Semanal | 28-12-2026 | 11-01-2027 | 28-12-2026 a 03-01-2027, semana ISO 2026-W53 |
| Mensual | 01-12-2026 | 01-02-2027 | Diciembre de 2026 |
| Anual | 01-01-2026 | 01-01-2028 | Año 2026 |

- **Obtenido vs esperado:** en cada caso solo se emiten dos períodos: el inmediatamente anterior a cada ejecución. El intermedio no aparece en el historial ni se recupera al repetir la segunda ejecución. Se esperaba recorrer los períodos cerrados pendientes desde el último procesado. No es D4: se comprueba la selección de períodos, sin evaluar sus cifras.
- **Estado:** confirmado con las tres variantes de `test_a2_scheduler_loses_periods_after_downtime`. El renderizador está sustituido por un registrador de llamadas; selección, persistencia y deduplicación del scheduler son reales.
- **Corrección sugerida mínima:** enumerar períodos cerrados desde una marca persistida hasta el período actual elegible; consultar los formatos emitidos de cada período antes de generar. Reintentar también períodos anteriores incompletos.

### A2-11 Reimportar una fila sin cambios altera el avance acumulado del día

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/repo.py:621`; `backend/ganttier/control/progress.py:84`.
- **Escenario reproducible:** actividad de 40 HH. El 07-01-2026 se importan, en orden, Ana: 20% y 2 HH; Luis: 50% y 3 HH. EV=20 y AC=5. Reimportar solamente la fila idéntica de Ana mediante `save_import_bundle`.
- **Obtenido vs esperado:** los consumos siguen en 5 HH, pero EV baja a 8 y el avance del día pasa de 50% a 20%. Se esperaba que sustituir una fila por sus mismos datos conservara el resultado. El DELETE/INSERT cambia su ID; el control elige el mayor ID como último snapshot del día.
- **Estado:** confirmado con prueba `test_a2_reimport_unchanged_row_changes_latest_percent`.
- **Corrección sugerida mínima:** conservar el ID y el orden del registro cuando se actualice una fila existente. Si la clave actividad/fecha/recurso corresponde a varias filas, resolver o rechazar la ambigüedad antes de alterar el historial.

### A2-12 Las asignaciones de equipos reducen las HH atribuidas al personal

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/control/performance.py:59`, `backend/ganttier/control/performance.py:60`, `backend/ganttier/control/performance.py:65`.
- **Escenario reproducible:** actividad de un día y presupuesto 8 HH. Asignar a Ana y una grúa (`kind='equipo'`), una unidad de cada uno, sin HH explícitas. Registrar 100% y 8 HH reales sin trabajador identificado.
- **Obtenido vs esperado:** Ana tiene 8 HH planificadas, pero solo 4 HH ganadas y 4 HH reales. Se esperaban 8 HH de personal en ambas cifras: la grúa no representa otro trabajador. Primero se construye el denominador de participación con todos los recursos; después se filtran los trabajadores, dejando fuera la mitad del valor y consumo distribuido.
- **Estado:** confirmado con prueba `test_a2_equipment_dilutes_worker_earned_and_untagged_hours`.
- **Corrección sugerida mínima:** para indicadores en HH, excluir equipos y materiales del denominador de reparto entre personal. Conservar separadamente la participación de demanda genérica de mano de obra, sin reasignarla automáticamente.

### A2-13 El SPI por trabajador distribuye trabajo en sábados y domingos no laborables

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/control/performance.py:14`, `backend/ganttier/control/performance.py:88`.
- **Escenario reproducible:** calendario lunes–viernes, actividad del viernes 09 al lunes 12-01-2026, dos días laborables y 16 HH. Un único trabajador tiene las 16 HH asignadas y registra el viernes 50% y 8 HH reales. Corte el viernes.
- **Obtenido vs esperado:** SPI del proyecto=1, SPI del trabajador=2. Para el período sábado 10–domingo 11 se informan 8 HH planificadas en vez de 0. `_fraction()` reparte el presupuesto entre cuatro días corridos, mientras EVM lo reparte entre las dos jornadas laborables.
- **Estado:** confirmado con prueba `test_a2_worker_spi_counts_weekend_as_work`.
- **Corrección sugerida mínima:** usar en desempeño la misma distribución temporal de HH/PV por calendario que en control; pasar esa distribución o los calendarios efectivos desde servicios.

### A2-14 Un fallo al guardar metadatos deja archivos huérfanos que se duplican al reintentar

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/autoreports.py:167`, `backend/ganttier/autoreports.py:184`; `backend/ganttier/services.py:566`.
- **Escenario reproducible:** emitir HTML semanal el 12-01-2026. Después de generar el archivo, provocar `sqlite3.OperationalError` al guardar `autoreports_log`. Restablecer la escritura y reintentar el mismo período.
- **Obtenido vs esperado:** tras el fallo hay un archivo y ningún historial. El reintento crea otro archivo para el mismo período; hay dos archivos físicos y solo uno registrado. Se esperaba retirar el archivo no confirmado o recuperarlo en el reintento. Los nombres UUID y la transacción SQLite no proporcionan atomicidad conjunta con el sistema de archivos; el huérfano tampoco entra en la retención.
- **Estado:** confirmado con prueba `test_a2_scheduler_metadata_failure_leaves_duplicate_period_files`. Se inyecta el fallo de metadatos y se aligera el contenido renderizado; generación de rutas, archivos y transacciones son las del servicio real. No se afirma que el historial contenga dos emisiones exitosas.
- **Corrección sugerida mínima:** ante rollback, retirar los archivos nuevos de esa ejecución. Para cubrir terminaciones del proceso, usar una identidad determinista por proyecto/período/formato o un estado persistido recuperable de generación pendiente.

### A2-15 La reagrupación del gráfico Excel elimina el punto exacto de corte

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/reports/xlsx.py:223`, `backend/ganttier/reports/xlsx.py:230`.
- **Escenario reproducible:** actividad del 05 al 09-01-2026, corte el miércoles 7, avance 50%. Obtener una curva diaria del servicio y entregarla al renderizador público XLSX dentro del informe. La curva incluye 50% ganado y el origen de la proyección el día 7.
- **Obtenido vs esperado:** la tabla visible Curva S conserva el 50% al corte; la hoja oculta `_Grafico_Curva`, usada por el gráfico, elimina esa fecha y todos sus valores ganados quedan vacíos en este ejemplo. Se esperaba preservar el anclaje al corte al reducir la frecuencia. Se toma el último punto semanal, posterior al corte y con EV=None, y se descarta el punto válido intermedio. Los gráficos PNG de PDF/Word/HTML utilizan la curva original.
- **Estado:** confirmado con prueba `test_a2_excel_chart_resampling_drops_cutoff_anchor`, que genera el XLSX real y lee ambas hojas. El caso demostrado usa una curva diaria en el renderizador; el informe semanal habitual del servicio usa una curva semanal y no activa esta rama con horizontes cortos.
- **Corrección sugerida mínima:** mantener el punto de corte como muestra obligatoria al reagrupar, dividiendo el grupo que lo contiene; conservar allí EV, AC y proyección, sin trasladar valores históricos a fechas posteriores.

## Tabla resumen

| ID | Defecto confirmado | Severidad | Prueba (`test_a2_…`) |
|---|---|---|---|
| A2-01 | Borrado de tarea periódica destruye ejecución | Alta | `delete_periodic_task_cascades_executed_history` |
| A2-02 | Merge borra fechas de columnas ausentes | Alta | `excel_template_merge_clears_existing_schedule` |
| A2-03 | Corregir trabajador duplica HH/costos importados | Alta | `reimport_worker_correction_duplicates_consumption` |
| A2-04 | Carrera persiste fechas invertidas | Alta | `concurrent_updates_persist_inverted_dates` |
| A2-05 | NULL importado inutiliza control | Alta | `excel_null_budget_commits_unusable_activity` |
| A2-06 | Cierre obsoleto convierte 50% corregido en 100% | Alta | `reopened_activity_keeps_obsolete_finish` |
| A2-07 | Consumos admitidos por API desaparecen de AC | Alta | `activity_snapshot_consumption_disappears_in_control` |
| A2-08 | PV ignora calendario de actividad | Alta | `activity_calendar_ignored_by_pv` |
| A2-09 | Colisión sustituye calendario importado | Alta | `import_calendar_name_collision_silently_changes_schedule` |
| A2-10 | Scheduler omite períodos intermedios | Media | `scheduler_loses_periods_after_downtime` × 3 |
| A2-11 | Reimportación idéntica parcial cambia el avance | Media | `reimport_unchanged_row_changes_latest_percent` |
| A2-12 | Equipos reducen las HH atribuidas al personal | Media | `equipment_dilutes_worker_earned_and_untagged_hours` |
| A2-13 | SPI del trabajador cuenta días no laborables | Media | `worker_spi_counts_weekend_as_work` |
| A2-14 | Fallo de metadatos duplica archivos del período | Media | `scheduler_metadata_failure_leaves_duplicate_period_files` |
| A2-15 | Gráfico Excel pierde el anclaje al corte | Media | `excel_chart_resampling_drops_cutoff_anchor` |
