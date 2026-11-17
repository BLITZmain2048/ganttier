# Equipos de trabajo y reparto por cantidad

Fecha: 05-10-2026. Ámbito: mantenimiento, activación explícita por tarea maestra.

## Regla vigente: continuidad opcional

Desde fase 2, `Lote_Continuo_En_Turno=NO` es el valor predeterminado. Un lote permanece en el mismo equipo y consume días efectivos de su calendario; puede empezar al final de un bloque y continuar en el siguiente, siempre dentro de la ventana. Por ejemplo, un lote de dos días del Equipo B puede trabajar el 14 y el 22 de enero: duración 2, intervalo civil de 9 días y cero HH del 15 al 21. Se representa con inicio, fin y duración efectiva, usando el calendario del equipo para reconstruir los segmentos.

Con `SI` se conserva la regla estricta: todos los días del lote deben ser consecutivos de turno. Un lote que supera el bloque nominal se rechaza solo en este modo. El verificador independiente comprueba extremos de turno, cantidad de días efectivos, HH, ventana y, cuando corresponde, ausencia de cruce de descanso.

El ejemplo 2027 de 28.000 paneles/mes, lotes de 2.000, rendimiento de 1.000 y dos equipos 7x7 alternados resulta **FACTIBLE Y APLICADO** en modo predeterminado: siete lotes por equipo/mes. La versión estricta anterior permanece en `trabajos/ganttier_equipos_20261005/ejemplo_paneles_ESTRICTO_INFACTIBLE/`; los nuevos entregables y la auditoría están en `trabajos/ganttier_equipos_20261005/fase2/`.

## Modelo y compatibilidad

Se reutiliza `Resource` de tipo `cuadrilla`, con `is_work_team=True`. Su ID es la identidad del equipo; pertenece a un plan y puede ser compartido por todas sus tareas e instalaciones. `max_units` representa integrantes; `capacity_hours_day` y las horas del calendario representan horas por integrante y día. La capacidad total es su producto. El CRUD de equipos mantiene estos valores consistentes y crea un calendario propio al cambiar su definición, evitando alterar otros recursos.

No se crea un segundo sistema de recursos ni de calendarios. `WorkCalendar` sigue siendo la autoridad para turnos, feriados y excepciones. Las capacidades y reservas se integran con el nivelador existente y los perfiles de `engine/loads.py`.

La tarea maestra añade:

| Excel | API/dominio | Significado |
|---|---|---|
| Cantidad_Total | quantity_total | Cantidad por ocurrencia, positiva y finita |
| Unidad | unit | Texto, por ejemplo paneles o strings |
| Tamano_Lote | batch_size | Máximo por orden de trabajo |
| Rendimiento_Dia_Equipo | productivity_day_team | Cantidad diaria del equipo completo |
| Equipos | team_ids | Lista ordenada de equipos; Excel usa nombres separados por coma |
| Lote_Continuo_En_Turno | batch_continuous_in_shift | Booleano; NO predeterminado permite cruzar descansos sin trabajar en ellos; SI exige continuidad |
| Tolerancia_Equidad_Lotes | balance_tolerance_batches | Entero ≥ 0; predeterminado 1 para alertas por ciclo/mes |

Se exige el conjunto completo si se informa cualquier campo de reparto. `Primera_Fecha` es obligatoria para fijar una identidad y rotación estables. No se mezcla este reparto con un trabajador fijo en la maestra. El equipo se asigna nominalmente: la habilidad de la maestra no se utiliza para buscar otra cuadrilla. Los valores históricos de duración, HH y dotación de la maestra permanecen en el intercambio, pero los lotes calculan sus propios valores a partir de cantidad, rendimiento y equipo.

Actividades sin estos campos conservan el generador y nivelador tradicionales. Las nuevas columnas son opcionales en libros antiguos. La migración v4 incorporó equipos; **v5** añade el booleano con valor NO a maestras existentes. Ambas conservan datos y usan copia previa y transacción. No se reescriben migraciones anteriores. Las fechas guardadas se conservan; regenerar aplica el nuevo contrato. Para mantener una maestra estricta, marcar SI antes de regenerar.

Cada lote conserva `periodic_task_id`, `occurrence_index`, `batch_index`, `quantity`, `unit`, `team_id` y `equity_mode`. El código es `MAESTRA-0001-L01`; los dígitos se amplían cuando es necesario. El costo por ocurrencia se distribuye proporcionalmente a la cantidad, para no multiplicarlo por el número de lotes.

## Calendarios, duración y ventanas

- 7x7, 14x14 y otros NxM: N días de trabajo seguidos de M de descanso, anclados en `Inicio_Ciclo`. Por defecto se trabajan feriados; es configurable.
- 5x2: lunes a viernes; sábado y domingo siempre libres. Por defecto no se trabajan feriados de Chile. El ancla no convierte este régimen en una secuencia móvil de cinco días. No se admiten excepciones que habiliten fines de semana para estos equipos.
- `días_lote = ceil(cantidad_lote / rendimiento)`; el último lote puede ser menor.
- `HH_lote = días_lote × horas_día_calendario × integrantes`. Es dedicación de turno completo, incluso si la cantidad final utiliza solo parte de la productividad teórica.
- En modo estricto, un lote mayor que N, o mayor que cinco días para 5x2, se rechaza al validar. En modo divisible se cuenta la suma de días de turno dentro de la ventana. Feriados y ventanas pueden reducir aún más el bloque disponible: eso se informa como infactibilidad.
- Las ventanas de cantidad son duras. M, NM, Q, S, A y NA utilizan meses civiles desde el mes de la ocurrencia y la extensión de su frecuencia. Las frecuencias diarias/semanales más cortas que el ciclo utilizan su período natural (por ejemplo siete o catorce días desde la fecha objetivo). Las restantes conservan las tolerancias de la maestra. Se recortan al horizonte.
- Las tolerancias de las maestras tradicionales no cambian. Esta elección evita que una maestra semanal nueva herede una ventana de un solo día por tolerancias predeterminadas cero.

## Equidad por ocurrencia

Cuando la frecuencia alcanza o supera el ciclo, primero se reparten lotes enteros entre los equipos seleccionados; después se buscan fechas en el calendario de cada uno. Las diferencias de días disponibles **no cambian las cuotas**. Se emite una alerta con ocurrencia, nombres y días hábiles. El planificador resuelve manualmente.

Para L lotes y N equipos, cada uno recibe `L // N`; los `L % N` restantes se asignan desde un cursor que avanza por ese resto entre ocurrencias. El orden de la lista de equipos es parte del dato. La asignación de extras rota y los acumulados de cualquier prefijo de ocurrencias consecutivas difieren como máximo en un lote. No se elimina el último lote parcial ni se reparte físicamente entre equipos. Cambiar el orden de equipos, la fecha inicial o la cantidad cambia el escenario y puede cambiar asignaciones.

Ejemplo: 28.500 paneles produce catorce lotes de 2.000 y uno de 500. Los equipos alternan 8/7 y 7/8; en doce meses reciben 90 lotes cada uno. La equidad exigida es en **lotes**, no en HH ni cantidades parciales.

## Equidad por ciclo para frecuencias cortas

Si la frecuencia es menor que el ciclo de turno, no se exige 50/50 en cada ocurrencia. Todos sus lotes se asignan a los equipos que tienen turno dentro de la ventana; si solo hay uno, recibe la ocurrencia completa. Si hay varios, se consideran primero los que conservan espacio para el lote en sus días de turno (en un bloque si es estricto); entre ellos se prioriza menor acumulado del ciclo, luego menor acumulado del mes y finalmente el orden de la lista. La programación sigue exigiendo capacidad y el modo de continuidad de cada lote.

Para dos equipos 14x14 alternados y frecuencia semanal, cuatro ocurrencias alineadas con un ciclo completo se asignan A, A, B, B. No se emiten alertas por la diferencia de días disponibles de cada semana. Se comparan los lotes acumulados por ciclo de 28 días y por mes civil: solo una diferencia superior a la tolerancia genera alerta. El desequilibrio es una advertencia, no una autorización para exceder capacidad. Mensual con 14x14 conserva 7/7 por ocurrencia.

Los ciclos se anclan en la fecha de inicio más antigua de los equipos. Para calendarios con ciclos de distinta longitud se usa su mínimo común múltiplo como período común de auditoría; conviene usar el mismo régimen en una cuadrilla alternada. La clasificación usa períodos nominales conservadores: día=1, semana=7, mes=28, trimestre=84, semestre=168 y año=365, multiplicados por el intervalo. Para regímenes genéricos próximos a la duración variable de un mes, revisar este criterio antes de usarlo: no se reclasifica la misma maestra mes a mes.

Los acumulados se atribuyen por fecha objetivo de la ocurrencia, no por fecha de ejecución de cada lote. Un período recortado por el horizonte o por el inicio/fin de la maestra queda pendiente de evaluación: no se compara con una cuota de un período completo. Ciclos y meses no coinciden necesariamente. Un mes de cinco semanas puede quedar desbalanceado aunque todos los ciclos completos estén equilibrados; esa alerta mensual es real según la regla decidida. La tolerancia permite reflejar el criterio del planificador sin cambiar asignaciones ni ocultar infactibilidad. La equidad se evalúa por maestra, para no equiparar lotes de diferentes unidades o tamaños; la capacidad de cada equipo sí se suma entre todas sus maestras.

## Nivelación y verificación

Se extiende `maintenance/leveling.py`: se reutilizan candidatos, puntajes, calendarios, reservas y capacidades. Para cada grupo de lotes de una ocurrencia/equipo se escogen posiciones ordenadas sin superposición, cercanas a posiciones uniformes en sus días de turno. El puntaje conserva el efecto de las otras actividades del equipo. Los puntajes idénticos se reutilizan dentro del grupo, mientras su carga permanece fija; la caché se descarta al pasar al siguiente grupo. Las pruebas comparan fechas, asignaciones y objetivo con la versión sin caché. Las actividades ordinarias y cargas fijas consumen capacidad del mismo recurso; no existe una bolsa independiente de HH para lotes.

La nivelación es heurística entre grupos. No garantiza el óptimo global cuando muchas ventanas se superponen; la falta de una solución encontrada se distingue de una prueba de capacidad insuficiente. Tampoco se promete igualdad exacta entre espacios libres: el calendario y las cargas existentes pueden impedirla. En frecuencias cortas, elegir equipos con espacio en sus bloques es previo a la nivelación conjunta; una elección puede necesitar revisión manual por carga de otras maestras.

`engine/verify.py` reconstruye de forma independiente las ocurrencias exigibles, cantidades, duración, HH y ventanas. Comprueba ocurrencias completas ausentes, índices, cantidad total y por lote, equipo/asignación/calendario, días libres, descansos, cuotas, acumulados y alertas de ciclo/mes. No importa el generador ni el nivelador. La aplicación de una corrida vuelve a verificar también las asignaciones propuestas, además de fechas. No se suaviza capacidad mediante horas extra para lotes.

Generación, guardado, asignaciones y aplicación forman una transacción para planes con reparto. Si falla capacidad o verificación, se revierte el inventario y se mantiene el programa anterior. La respuesta incluye `applied=false`, `created=0`, advertencias y violaciones. Una simulación factible tampoco altera el inventario: cuando este ya existe conserva una corrida revisable/aplicable; si requería materializar lotes nuevos, revierte la materialización y devuelve el resultado sin corrida aplicable. El diagnóstico se conserva por huella del escenario para mostrarlo en Análisis e informes sin reutilizar evidencia de un escenario editado. Después de cambiar el equipo, calendario o maestra, vuelva a generar; la edición no mueve por sí sola las fechas previamente guardadas.

## API y recorrido del usuario

Rutas bajo `/api`, con la autenticación local existente:

| Método y ruta | Resultado |
|---|---|
| GET /projects/{pid}/teams | Equipos y calendarios del plan |
| POST /projects/{pid}/teams | Crear equipo |
| PUT /projects/{pid}/teams/{id} | Editar parcialmente |
| DELETE /projects/{pid}/teams/{id} | Eliminar si no tiene tareas ni lotes asociados |
| GET /projects/{pid}/maintenance/distribution | Filas por ocurrencia/equipo, demanda, programa, advertencias y violaciones; admite start/end |

Ejemplo de alta:

```json
{"name":"Equipo A","regime":"14x14","cycle_start":"2027-01-01","members":3,"hours_day":8,"holidays_work":true}
```

El resumen distingue cantidad, lotes, días requeridos, días disponibles y HH de demanda frente a lotes/cantidad/HH programados. Una respuesta infactible puede contener toda la demanda y cero programación. No confundir esa demanda con órdenes aprobadas. Para comprobar un plan anual, consultar el horizonte completo; un filtro puede recortar ventanas y diagnosticar solo ese subhorizonte.

En Plan maestro, **Equipos y reparto** permite gestionar equipos y consultar el resumen por mes y las alertas. El formulario de tarea permite elegir varios equipos. **Análisis y alertas** conserva las advertencias mediante su ciclo de evaluación existente.

Excel incorpora la hoja `Equipos` (Equipo, Regimen, Inicio_Ciclo, Integrantes, Horas_Dia, Feriados_Trabajan y referencia Calendario) y las columnas de la maestra. La importación resuelve nombres a IDs y la exportación hace el recorrido inverso; conserva cantidad/equipo/índice en actividades. Las instrucciones incluyen el ejemplo y su límite de capacidad. La plantilla se genera desde Importar/Exportar; los libros históricos no se modifican automáticamente.

El Gantt Excel en modo **por nivel**, agrupación **equipo**, utiliza el equipo de trabajo de los lotes. En actividades tradicionales conserva la agrupación anterior por equipo físico. Añade hojas Reparto por equipo y Carga por equipo y alertas en Leyenda. Las barras representan el intervalo del grupo, no ocupación continua de todos los días intermedios. Los informes mensuales/anuales incorporan cantidad y lotes por equipo y las alertas del plan.

## Caso futuro: dos equipos, tres plantas FV

Registrar ambas cuadrillas una sola vez en **el mismo plan** y seleccionar esos IDs en las maestras de las tres plantas. Distinguir plantas con ubicación/instalación; así comparten capacidad real. No crear una copia del equipo por planta. La capacidad no se agrega entre proyectos distintos.

`strings_por_SCB=24` es una conversión de dominio que el planificador debe aplicar al definir cantidades y unidad: por ejemplo, 10 SCB equivalen a 240 strings. El motor no convierte automáticamente paneles, strings o SCB ni conoce tiempos de traslado. Rendimiento y lote deben usar la misma unidad; agregar traslados como actividades que consumen el mismo equipo cuando corresponda.

## Validación y límites pendientes

Pruebas en `tests/maintenance/test_work_teams.py`, `test_team_performance.py`, `tests/core/test_team_migration.py`, `tests/gui/test_teams.py` y `tests/reports/test_team_distribution.py`. Cubren demanda mensual de doce meses, calendarios, rotación con dos/tres/cuatro equipos, equidad semanal por ciclo, tolerancia mensual, infactibilidad atómica, carga compartida, auditoría manipulada, GUI, migración y Excel.

El escenario de 100.000 lotes comprueba generación bajo 3 s y nivelación bajo 30 s con límites sin rebajar. Es una prueba del núcleo, no una medición integral de importación, SQLite, informe y GUI. La evidencia de regresión antes/después y las suites incompletas se detallan en `trabajos/ganttier_equipos_20261005/INFORME.md` del espacio compartido. No se atribuyen al cambio los fallos que ya estaban presentes, ni se declara ausencia de regresiones si una suite no termina.

## Ejemplo OM-WBS Chile Solar y límites de fase 2

Dos equipos compartidos por DST, AND y SDN, 14x14 alternados, horizonte noviembre 2026–octubre 2027. Strings/SCB=24 confirmado. Tasas, tamaños de lote, tres integrantes, ocho horas de turno y seis productivas conservan la marca **SUPUESTO**. Los faltantes se conservan como tareas globales con HH del ejemplo previo, sin inventar cantidades físicas. Los ceros de ausencia quedan inactivos y trazados; se conserva la matriz de campañas, incluyendo sus contradicciones con etiquetas de frecuencia.

La capacidad es de días enteros por lote: `ceil(cantidad/rendimiento)`. Lotes pequeños no comparten automáticamente un día aunque su tiempo teórico sea fraccionario. Aumentar integrantes sin cambiar el rendimiento y la organización del trabajo no reduce días por lote. No se modelan traslados ni una sola planta por día-equipo. Los informes nativos anuales usan años civiles: el horizonte Nov–Oct requiere los informes 2026 y 2027 más el resumen de los doce meses.

La equidad por ciclos completos no garantiza diferencia anual ≤1 lote en un horizonte recortado. Con 365 días, 14x14 y varios lotes diarios, el último día de A puede dejar una diferencia superior a uno aunque los trece ciclos completos estén equilibrados. Esta incompatibilidad se informa; no se mueve trabajo al descanso ni se reduce la demanda para ocultarla.
