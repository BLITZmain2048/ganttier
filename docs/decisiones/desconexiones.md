# Programa de trabajos con desconexión (base para solicitudes)

Se implementa una lectura del programa vigente para preparar solicitudes de desconexión o
indisponibilidad. No envía solicitudes, no modifica la programación ni acredita viabilidad
operacional. El formato oficial y los plazos de solicitud deben verificarse en la normativa
vigente y los procedimientos del Coordinador Eléctrico Nacional. Esta advertencia aparece
también en el JSON y en Resumen. No se declara conformidad con un formato oficial.

## Contrato para la interfaz

- `GET /api/projects/{pid}/outages?from=2026-01-01&to=2026-12-31`
- `GET /api/projects/{pid}/outages.xlsx?from=2026-01-01&to=2026-12-31`

Ambos filtros son opcionales, de tipo fecha ISO. Omitir un límite deja ese extremo abierto.
Fechas inválidas o un intervalo invertido producen 422; un proyecto inexistente produce 404.
Un proyecto sin trabajos elegibles entrega grupos vacíos o un libro con encabezados y KPI cero.
Se aceptan ambos modos (`mantenimiento` y `proyecto`); solo entran actividades con
`requires_outage=True`. El botón de interfaz se implementará por separado.

El JSON es un objeto con `title`, `note`, `project_id`, `project_name`, `from`, `to`,
`excluded_activity_count`, `summary` y `groups` (lista). Cada grupo contiene:

| Campo | Contenido |
| --- | --- |
| `number` | Correlativo del resultado, desde 1; no es un ID persistente |
| `location` | Instalación, tal como está registrada |
| `voltage_kv` | Máxima tensión conocida del grupo; null si todas faltan |
| `start`, `finish` | Fechas ISO; término máximo entre sus actividades |
| `duration_days` | Días corridos inclusivos entre inicio y término |
| `activity_count`, `grouped` | Cantidad de trabajos y booleano verdadero si hay ≥2 |
| `work_hours` | Suma de las HH presupuestadas de las actividades |
| `activities` | Lista de trabajos, sin paginación ni recorte |

Cada actividad incluye `id`, `code`, `name`, `equipment`, `voltage_kv`, `start`, `finish`,
`duration_days`, `work_hours` y `workers`. Cada trabajador/cuadrilla incluye `id`, `name`,
`kind`. Se leen las asignaciones reales, incluidos recursos globales e inactivos aún
asignados; se deduplica por ID. Los equipos/materiales y las demandas genéricas por habilidad
no se presentan como personas. Una actividad sin personal asignado devuelve `workers: []`.
Las asignaciones no multiplican las HH. La columna Responsable(s) muestra personal asignado;
no constituye designación del responsable formal de una solicitud.

## Fechas, agrupación y límites

- Por cada extremo se usa `planned_start/finish` y, si falta, `early_start/finish`. No se
  sustituyen por fechas reales, vencimientos ni ventanas macro, ni se recalcula el programa.
- Los trabajos sin ambos extremos o con fechas invertidas se excluyen y se cuentan en
  `excluded_activity_count`, referido a todo el proyecto incluso al filtrar. Deben programarse
  o corregirse antes de preparar una solicitud; no se inventan fechas.
- La clave es exactamente `(location, start)`, sin normalizar nombres ni agrupar por tensión.
  Instalaciones con diferente escritura se mantienen separadas. La instalación vacía se
  conserva vacía; debe identificarse antes de usar la planilla. Dos trabajos con ubicación
  vacía e igual inicio quedan juntos por esta misma regla, sin afirmar identidad física.
- Se agrupa antes de filtrar. Se conserva la desconexión completa si su intervalo se solapa
  inclusivamente con `[from, to]`; no se recortan fechas, actividades ni HH. Por tanto, un grupo
  puede incluir un trabajo terminado antes de `from` si otro del mismo grupo sigue vigente.
- Los días son corridos inclusivos (misma fecha = 1), también para hitos. No equivalen a la
  duración laboral del motor ni a horas efectivas de indisponibilidad: el modelo no tiene horas
  de maniobra. La agrupación es una ayuda de planificación, pendiente de revisión operacional.
- Orden: inicio e instalación; trabajos por ID. Los correlativos se reasignan al filtrar.

`summary` contiene `outage_count`, `activity_count`, `grouped_outage_count`,
`grouped_activity_count`, `average_activities_per_outage` y `work_hours`. El promedio es
trabajos / desconexiones, incluyendo grupos unitarios, y vale cero sin datos. `by_month`
(clave `month`, YYYY-MM) y `by_voltage` (clave `voltage_kv`) contienen los mismos KPI.
Se asigna todo el grupo al mes de inicio y a su tensión máxima. No se prorratean HH entre
meses ni se cuentan varias veces los grupos con tensiones mixtas; null es “Sin informar”.

## Libro y rendimiento

Hojas en orden: **Resumen**, **Programa**, **Detalle**. Resumen incluye título, advertencia,
origen, filtros, exclusiones, interpretación de KPI, totales y desgloses. Programa tiene
una fila por desconexión con las columnas solicitadas:

`N° | Instalación | Tensión kV | Inicio | Término | Días | N° trabajos | Equipos |
Descripción de trabajos | HH | Responsable(s) | Observaciones`

Detalle tiene una fila por actividad: `N° desconexión | Instalación | Tensión kV | Código |
Actividad | Equipo | Inicio | Término | Días | HH | Responsable(s) | Agrupado`.
Las fechas son celdas de fecha con formato `dd-mm-yyyy` (dd-mm-aaaa visible). Se incluyen
filtros en ambas tablas, paneles inmovilizados, anchos definidos, texto ajustado, encabezados
azules y Observaciones vacías en amarillo. No se insertan trabajos ficticios como ejemplos.

Es una exportación de valores de BD, no un modelo de reprogramación Excel: los KPI y grupos
son una instantánea. Se puede completar Observaciones; para actualizar fechas o HH se cambia
Ganttier y se descarga otra vez. Textos que empiezan con `=` se escriben como texto literal.
Las listas concatenadas se abrevian explícitamente al aproximarse al límite de texto Excel;
Detalle conserva todas las filas. Un texto individual mayor de 32 767 caracteres también
se abrevia con aviso; el JSON conserva el original.

Se reutilizan `repo.get/list_entities` y la dependencia `connection`; no se cambia repo ni
services. Son cuatro consultas masivas, sin consultas por actividad. Agrupación O(N), orden
O(G log G), memoria O(N + asignaciones). `openpyxl.Workbook(write_only=True)` evita mantener
millones de celdas; el ZIP final se entrega en memoria. No se crean informes persistentes.
Los XML de trabajo de openpyxl usan el directorio temporal y se eliminan al guardar.

## Pruebas

`tests/reports/test_outages.py` usa TestClient y BD temporal, configura `GANTTIER_DB`,
`GANTTIER_DATA_DIR` y los temporales de openpyxl dentro de `tmp_path`. Cubre modos, prioridades
de fechas, HH, asignaciones, agrupación, filtros, formato, texto literal, errores y demo real.
La prueba marcada `slow` crea 100 000 actividades elegibles y verifica JSON y todas las filas
del Excel, además de informar sus tiempos, en dos variantes: reporte directo e integración
HTTP. Las pruebas HTTP se omiten explícitamente si faltan FastAPI o HTTPX; una omisión no
acredita la integración. Las pruebas directas siguen ejecutándose. Para mantener también los
temporales dentro del espacio compartido, ejecutar desde backend:

```text
python -B -m pytest tests/reports/test_outages.py -q -p no:cacheprovider --basetemp ../../trabajos/outages-tests
python -B -m pytest tests/reports/test_outages.py -m slow -s -q -p no:cacheprovider --basetemp ../../trabajos/outages-scale
```

Verificación local del 26-09-2026 (Python 3.10, openpyxl 3.1.5): **4 pruebas aprobadas,
10 pruebas HTTP omitidas**. Faltaban FastAPI/HTTPX y la restricción de red impidió instalarlos;
la integración HTTP queda pendiente de ejecución en un entorno con las dependencias del
proyecto. Se verificó directamente una BD con 100 000 actividades: 10 000 desconexiones,
800 000 HH, 100 000 filas de Detalle y 10 000 de Programa. Preparar la estructura JSON tomó
12,97 s y leer/exportar el Excel 99,00 s (5 998 829 bytes). Son medidas del reporte directo,
no tiempos HTTP ni un SLA. La reutilización de estilos redujo la exportación inicial de
150,16 s a 99,00 s sin cambiar el contenido.
