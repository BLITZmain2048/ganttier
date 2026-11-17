# Importación MS Project / Primavera P6

Formatos `msp_excel` y `p6_excel`; adaptadores `AdaptadorMSP` y `AdaptadorP6`
en `importadores/proyecto_externo.py`. Admiten `.xlsx` y `.csv`. La conversión
produce un `ImportBundle` de proyecto y utiliza la confirmación transaccional
existente. No programa ni nivela actividades al importar.

## Flujo y alcance

1. Subir mediante `POST /api/importadores/archivos`.
2. Revisar detecciones y enviar `POST /api/importadores/borradores/{id}` con
   `perfil` y `supuestos` cuando corresponda.
3. Revisar errores con hoja, fila y columna; corregir la fuente si hay errores.
4. Confirmar con `POST /api/importadores/confirmar/{id}`, revisión vigente,
   `aceptar_supuestos=true` y, si corresponde, `aceptar_advertencias=true`.

La detección exige identificador, nombre y duración específicos del producto;
normaliza acentos, mayúsculas y orden de columnas. Examina las primeras 30 filas.
Un perfil permite seleccionar otra hoja y encabezado hasta la fila 1000. `mapeo`
usa campos internos (`code`, `name`, `duration`, `predecessors`, etc.) y letras
Excel o títulos; `"campo":""` desactiva un campo. Las tablas auxiliares P6
usan nombres de hoja fijos y encabezados en fila 1.

La copia de la ronda no incluía el registro opcional de la aplicación canónica al
iniciar I1. No se modifica `servicio.py`: se entrega el adaptador con sus nombres
preacordados. Las pruebas HTTP registran las dos instancias mediante el punto
público `servicio.registrar`. Integración: conservar `_registrar_opcionales()`
de la aplicación canónica al incorporar estos archivos.

## MSP ES y EN

| Campo | Ejemplos de encabezados | Regla |
|---|---|---|
| Identidad | ID / Unique ID / Id único | Único; identificador textual conservado |
| Nombre | Nombre de tarea / Task Name | Obligatorio |
| Duración | Duración / Duration | `4 d`, `2 sem`, `8 h`; número sin unidad = días |
| Fechas previstas | Comienzo / Start, Fin / Finish | `planned_start` / `planned_finish` informativos |
| Predecesoras | Predecesoras / Predecessors | FS, SS, FF, SF; ES FC, CC, FF, CF |
| Recursos | Nombres de los recursos / Resource Names | `Juan[50%];Ana`; unidades 0,5 y 1 |
| Avance | % completado / % Complete | 0–100; fracción Excel con formato `%` se multiplica por 100 |
| Jerarquía | Nivel de esquema / Outline Level; Resumen / Summary | Resúmenes a WBS, sin duplicar HH ni duración |
| Trabajo | Trabajo / Work; Trabajo real / Actual Work | HH, o sufijos h/d/sem |
| Presupuesto | Costo / Cost | Número no negativo; moneda como supuesto |
| Restricción | Restricción / Constraint Type + fecha | Ocho tipos nativos, equivalencias ES/EN |
| Fechas reales | Comienzo real / Actual Start; Fin real / Actual Finish | Conservadas, validadas contra corte |

Sin nivel explícito se admite sangría textual de dos espacios por nivel (tab se
cuenta como un carácter). No se extrae jerarquía del estilo visual de celda.
Una fila con hijos se considera resumen. Los saltos sin padre bloquean. Una
predecesora o asignación sobre una fila resumen requiere trasladarla a una tarea;
no se expande implícitamente a sus hijos.

## P6

Hoja única: Activity ID, Activity Name, Original Duration, Remaining Duration,
Start, Finish, Actual Start/Finish, Physical % Complete / % Complete,
Activity Type, WBS, Calendar, Primary Constraint/Date y Predecessors.
Admite Resource Names y Budgeted Labor Units. Los códigos WBS con puntos crean
padres explícitos. Un nombre de calendario sin definición usa un supuesto visible.

| Hoja multi | Columnas |
|---|---|
| TASK | `task_id`, `task_code`, `task_name`, `target_drtn_hr_cnt`, `remain_drtn_hr_cnt`, `target_start_date`, `target_end_date`, `phys_complete_pct`, `task_type`, `wbs_id`, `clndr_id`, `target_work_qty`, `act_start_date`, `act_end_date`, `act_work_qty`, `cstr_type`, `cstr_date`, `target_cost` |
| TASKPRED | `pred_task_id`, `task_id`, `pred_type`, `lag_hr_cnt` |
| PROJWBS | `wbs_id`, `wbs_short_name` (código único), `wbs_name`, `parent_wbs_id` (0/vacío = raíz) |
| RSRC | `rsrc_id`, `rsrc_name`, `rsrc_type`, `clndr_id`, `max_units`, `hours_per_day`, `skills`, `cost_per_qty` |
| TASKRSRC | `task_id`, `rsrc_id`, `target_qty_per_hr` (unidades), `target_qty` (HH), `act_reg_qty` (HH reales de asignación) |
| CALENDAR | `clndr_id`, `clndr_name`, `workweek`, `day_hr_cnt`, `holidays_country`, `exceptions` |

`*_hr_cnt` se convierte de horas a días con la jornada de la actividad sucesora
para desfases. `PR_FS`, `PR_SS`, `PR_FF`, `PR_SF` son las relaciones admitidas.
IDs internos se resuelven a códigos; referencias desconocidas y ciclos bloquean
con ubicación. Códigos WBS y nombres de recursos deben ser únicos; no se mezclan
proyectos P6 con códigos coincidentes.

Tipos TT_Task / Task Dependent, TT_Rsrc / Resource Dependent, TT_Mile /
Start Milestone, TT_FinMile / Finish Milestone, TT_WBSsum / WBS Summary y
TT_LOE / Level of Effort. LOE y Resource Dependent conservan duración explícita
y generan advertencia: el motor no reproduce la duración automática P6.

CALENDAR admite semana de siete dígitos lunes–domingo, jornada hasta 24 h,
país CL o vacío y excepciones JSON, por ejemplo:

```json
[{"date":"2027-05-21","working":false,"hours":0,"reason":"Feriado propio"}]
```

No se interpreta `clndr_data` propietario: si viene con datos se emite error.
No se admite `.xer`, calendarios de turnos complejos, costos por período,
porcentajes basados en unidades, duraciones transcurridas, macros ni vínculos
externos. El formato multi es una tabla exportada/enriquecida, no una promesa de
compatibilidad con cualquier versión o configuración de P6.

## Semántica y supuestos

Duraciones y relaciones se almacenan en días laborables enteros. El supuesto
`redondeo_dias=superior` redondea hacia +infinito (1,5 → 2; −0,5 → 0), emitiendo
advertencia por fila. `rechazar` bloquea cualquier fracción. Las HH se calculan
con la duración exacta antes del redondeo y conservan el esfuerzo de la fuente.
Las duraciones individuales y desfases se acotan a 36.600 días.

Varias predecesoras se separan con `;` o `,`; para desfases con coma decimal,
use `;` entre relaciones. La coincidencia de un ID completo tiene prioridad
sobre la interpretación de sufijos FS/SS/FF/SF. Se admite una relación por pareja.
El análisis de componentes cíclicos es iterativo O(V+E) y marca únicamente
aristas dentro del ciclo, sin señalar descendientes acíclicos como ciclos.

`fechas_como_ventana=false` conserva las fechas previstas sin imponer ventanas.
Al activarlo, pasan también a `window_start/window_end` y `window_hard=true`.
Las restricciones explícitas sí se conservan: ASAP, ALAP, MSO, MFO, SNET, SNLT,
FNET y FNLT. Se exige fecha para las seis restricciones fechadas.

Supuestos editables: inicio, fin (horizonte máximo de diez años), corte, calendario
de respaldo (5×2, 8 h, sin feriados), moneda CLP, dotación 1, habilidad vacía,
redondeo y ventanas. Sin fechas se propone inicio 2027-01-04 y horizonte de dos
años. Sin corte se propone la última fecha real, o el inicio. La confirmación
reproduce los mismos valores; no depende de la fecha del computador.

Sin HH se propone duración × jornada × dotación/unidades. Sin nombres no se
inventan trabajadores. A los nombres sin atributos se les propone una unidad
de capacidad, jornada del calendario y costo cero; habilidades no observadas
quedan vacías. Trabajo explícito de una tarea MSP se reparte entre asignaciones
proporcionalmente a sus unidades. TASKRSRC conserva HH explícitas; una diferencia
con HH de TASK genera advertencia y se conservan ambos valores. El motor usa
asignaciones explícitas al calcular demanda.

Avance: una entrada de saldo inicial por actividad con porcentaje/fechas/HH reales,
en la fecha de corte supuesta. No se inventa historial por períodos ni se suma
otra vez `act_reg_qty` a `act_work_qty`. Fechas reales posteriores al corte,
fin anterior al inicio y fin real con porcentaje distinto de 100 bloquean.
Fin real sin porcentaje propone 100 con advertencia. Hitos exigen cero HH.

Cada actividad conserva fuente SHA-256, ubicación, valores originales y supuestos
en notas. El borrador conserva filas resumen excluidas y sus motivos. La descripción
del proyecto incluye hash y supuestos. Calendarios se identifican por nombre y hash
de su definición, evitando reutilizar silenciosamente uno de distinta jornada.
Las tablas auxiliares conservan hoja, fila, coordenadas y valores originales en
`modelo.origen_auxiliar`, persistido en los metadatos de importación. Una actividad
P6 WBS Summary con `wbs_id` existente conserva esa WBS sin duplicarla. Los códigos
WBS con puntos se limitan a 64 niveles y no admiten componentes vacíos.

## Límites de lectura y ejemplos

Lectura secuencial `read_only`; 30 MB de archivo, 150 MB descomprimidos, 5.000
entradas ZIP, 100 hojas, 200.000 filas de datos y 200 columnas. Rechaza rutas ZIP
inseguras, archivos cifrados y compresión excesiva. Fórmulas solo aportan su valor
cacheado; sin caché se emite error. No se evalúan fórmulas ni entidades XML.
Celdas combinadas en tablas, encabezados repetidos, fechas y números inválidos
bloquean con coordenadas. CSV detecta `;`, `,` o tab; intenta UTF-8 con BOM,
CP1252 y Latin-1. CSV contiene una tabla, sin auxiliares.

Generación desde `app/backend`, usando el intérprete de la aplicación:

```text
python -B ../ejemplos/generar_formatos_msp_p6.py
```

Produce cuatro plantillas vacías con instrucciones/comentarios/listas y ocho
libros de ejemplo: MSP ES, MSP EN, P6 única y P6 multi, cada uno con 40 y 2.000
actividades. También seis CSV (las tres variantes de tabla única). Contenido
determinista, fechas y datos ficticios fijos; las marcas temporales del contenedor
XLSX pueden cambiar. Conteos en `ejemplos/formatos/conteos_msp_p6.json`:

| Actividades | Relaciones | WBS | Hitos | Recursos | Asignaciones | Avances | Calendarios | HH |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 40 | 36 | 5 | 4 | 8 | 72 | 4 | 2 | 288 |
| 2.000 | 1.996 | 5 | 4 | 8 | 3.992 | 4 | 2 | 15.968 |

Cuatro cadenas independientes: una por disciplina, un hito al final de cada una,
dos recursos por disciplina y la primera actividad terminada. Cada actividad
no hito demanda 8 HH (dos asignaciones de 4 HH). MSP tiene cinco filas resumen
adicionales, por lo que `items` es 45/2.005; P6 tiene 40/2.000 filas TASK.
Calendarios: uno de respaldo supuesto y otro nombrado por la fuente.

Pruebas: `backend/tests/importadores/test_proyecto_externo.py`. Los tiempos y
resultados medidos, junto con los comandos CLI y conflictos de integración,
se conservan en `agentes/I1/INFORME.md` de la ronda.
