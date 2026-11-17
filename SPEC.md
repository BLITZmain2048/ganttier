# Ganttier — Especificación técnica y contrato entre módulos (v1)

> Documento **normativo** para todos los agentes. Si algo no está aquí, decide con criterio,
> documenta la decisión en `docs/decisiones/<modulo>.md` y NO rompas el contrato de otros módulos.

## 1. Qué es

Programa web local (Windows) de planificación tipo Primavera P6 con dos **modos separados**:

| Modo | Para qué | Núcleo |
|---|---|---|
| **Proyecto** (`mode="proyecto"`) | Obras/proyectos con WBS, dependencias FS/SS/FF/SF con desfase, restricciones de fecha, ruta crítica, recursos | CPM + optimización RCPSP |
| **Mantenimiento** (`mode="mantenimiento"`) | Planes de mantenimiento periódico (subestaciones, líneas, equipos) hasta **100 000 actividades** | Generador de ocurrencias + nivelación masiva vectorizada |

Transversal a ambos: **programación macro** (el usuario indica el mes/ventana de cada actividad y el
programa la ubica día a día), **optimización con objetivo elegible** (nivelar carga, minimizar plazo,
respetar ventanas macro, minimizar costo/HH extra, o mezcla ponderada), **modo control** (líneas base,
avance, curvas S, EVM, proyección), **informes** semanales/mensuales/anuales en xlsx/pdf/docx/html,
**desempeño por trabajador**, e ingreso por **GUI** o **plantillas Excel**.

## 2. Stack y estructura de carpetas (propiedad por agente)

- Backend: Python ≥3.10, FastAPI, Uvicorn, sqlite3 (stdlib), numpy, OR-Tools (CP-SAT), openpyxl,
  reportlab, python-docx, matplotlib (backend `Agg`), holidays. **Prohibido pandas** (peso/arranque).
- Frontend: React 18 + TypeScript + Vite. Gráficos: canvas propio (Gantt, histogramas) y `echarts`
  para curvas S/tableros. Sin librerías de Gantt de terceros.
- El backend sirve el frontend compilado (`frontend/dist`) en `/` y la API en `/api`.

```
Ganttier/
  SPEC.md                      (Claude — contrato; no editar salvo Claude)
  README.md                    (Claude, al final)
  instalar.bat  iniciar.bat    (A1)
  requirements.txt             (A1; los demás agregan su dependencia en docs/decisiones si la necesitan)
  backend/
    ganttier/
      __init__.py
      domain.py                (Claude — dataclasses compartidas; NO modificar sin avisar)
      schema.sql               (Claude — esquema SQLite; A1 puede AGREGAR índices/columnas, nunca quitar)
      calendars.py             (A1) calendarios laborales, feriados Chile, turnos NxM
      db.py  repo.py           (A1) conexión, migración, repositorio
      services.py              (A1) orquestación: carga desde BD -> llama motores -> guarda
      api/                     (A1) routers FastAPI
      main.py                  (A1) app, estáticos, arranque
      engine/                  (A2) cpm.py, optimize.py, heuristics.py, cpsat.py
      maintenance/             (A3) generator.py, leveling.py
      control/                 (A4) progress.py, scurve.py, evm.py, performance.py
      reports/                 (A5) builder.py, xlsx.py, pdf.py, docx.py, html.py, charts.py
      excel_io/                (A5) templates.py, importer.py, exporter.py
    tests/<modulo>/            (cada agente las suyas, pytest)
  frontend/                    (A6)
  plantillas/                  (A5) .xlsx generados por excel_io/templates.py
  docs/                        manual de usuario (A6 GUI, A5 plantillas), decisiones/
  ejemplos/                    (A3 y A2) generadores de datos de ejemplo
```

**Regla de oro:** cada agente escribe SOLO en sus carpetas. Si necesitas algo de otro módulo que
aún no existe, programa contra la firma de este documento (con un *stub* local en tus tests).

## 3. Convenciones

- Fechas: ISO `YYYY-MM-DD` en API/BD; `datetime.date` en Python. Mes macro: `YYYY-MM`.
- Unidad de tiempo de programación: **día**. Duración en **días laborables** según el calendario
  de la actividad (o del proyecto). Trabajo en **horas-hombre (HH)**.
- Hitos: `duration_days = 0`.
- Identificadores: enteros autoincrementales en BD; `code` es el identificador legible del usuario
  (único por proyecto), y es la clave usada en plantillas Excel.
- Textos visibles al usuario: español neutro. Identificadores de código: inglés.
- Números: `.` decimal en API; en Excel se respeta la configuración regional del usuario.
- Errores de API: HTTP 4xx con `{"detail": "mensaje en español"}`; validaciones con
  `{"detail": "...", "errors": [{"row": n, "field": "...", "message": "..."}]}`.

## 4. Modelo de datos

Ver `backend/ganttier/schema.sql` (fuente de verdad del almacenamiento) y
`backend/ganttier/domain.py` (fuente de verdad de los objetos que se pasan entre módulos).

Resumen de entidades: `projects`, `calendars`, `calendar_exceptions`, `wbs`, `activities`,
`relations`, `resources`, `assignments`, `baselines`, `baseline_activities`, `progress`,
`periodic_tasks`, `schedule_runs`.

Restricciones de fecha soportadas (`constraint_type`): `ASAP` (defecto), `ALAP`, `SNET`
(comenzar no antes de), `SNLT` (comenzar no después de), `FNET`, `FNLT`, `MSO` (comenzar el),
`MFO` (terminar el).

Ventana macro: `window_start`/`window_end` (fechas). El campo `macro_month` (`YYYY-MM`) es un
atajo: al guardarlo el backend fija `window_start` = primer día del mes y `window_end` = último.
La ventana es **blanda** (penaliza) o **dura** (`window_hard=1`, restringe) según la actividad.

## 5. Calendarios (A1: `calendars.py`)

```python
class WorkCalendar:
    def __init__(self, cal: domain.Calendar, horizon_start: date, horizon_end: date): ...
    def is_workday(self, d: date) -> bool
    def hours(self, d: date) -> float              # horas laborables del día (0 si no laborable)
    def next_workday(self, d: date, include: bool=True) -> date
    def prev_workday(self, d: date, include: bool=True) -> date
    def add_workdays(self, start: date, n: int) -> date   # fin inclusive de una tarea de n días que parte en start (n>=1); n=0 -> start
    def workdays_between(self, a: date, b: date) -> int   # días laborables en [a, b]
    def workday_index(self) -> "numpy.ndarray"     # vector bool por día del horizonte (para motores vectorizados)
    def day_hours_vector(self) -> "numpy.ndarray"  # horas por día del horizonte
```
- `holidays_country="CL"` usa el paquete `holidays` (Chile, incluye feriados móviles: San Pedro y
  San Pablo, Encuentro de Dos Mundos, Pueblos Indígenas, Iglesias Evangélicas, etc.) con **tabla
  de respaldo** interna 2024-2030 por si el paquete no está.
- Turnos: `shift_pattern="7x7"` (N días trabajo × M descanso) con `shift_anchor` (fecha de inicio
  del ciclo). Si hay turno, reemplaza a `workweek`. Los feriados aplican solo si `holidays_apply_to_shift=1`.
- Excepciones puntuales (`calendar_exceptions`) prevalecen sobre todo.

## 6. Motor de programación (A2: `engine/`)

```python
from ganttier.domain import ScheduleProblem, CPMResult, ObjectiveWeights, OptimizeOptions, OptimizeResult

def compute_cpm(problem: ScheduleProblem) -> CPMResult
def optimize(problem: ScheduleProblem, weights: ObjectiveWeights, options: OptimizeOptions) -> OptimizeResult
def validate_network(problem: ScheduleProblem) -> list[str]   # ciclos, referencias rotas, etc. (mensajes en español)
```
- CPM: pasadas adelante/atrás con FS/SS/FF/SF y desfases (en días laborables del calendario del
  sucesor), restricciones de fecha, fecha de corte (`data_date`) y avance (actividades en curso
  se reprograman con su duración remanente desde `data_date`; las terminadas quedan fijas).
  Holgura total/libre, ruta crítica (holgura total ≤ 0), detección de ciclos.
- `optimize` decide fechas de inicio respetando precedencias y calendarios; objetivos:
  - `level` (nivelar): minimizar la suma de cuadrados (o desviación absoluta) de la carga diaria/semanal
    de cada recurso/rol respecto de su media, y la sobreasignación sobre capacidad.
  - `makespan`: minimizar la fecha de término del proyecto con capacidad de recursos como restricción.
  - `window`: minimizar días fuera de la ventana macro (dura = restricción).
  - `cost`: minimizar costo de HH extra (carga sobre capacidad normal, hasta `max_overtime_hours_day`,
    pagada a `overtime_cost_hour`) + penalidad por sobreasignación no cubierta.
  - Mezcla: suma ponderada normalizada de los cuatro términos (`ObjectiveWeights`). El usuario elige.
- Métodos: `options.method in {"auto","heuristic","cpsat"}`. `auto` → CP-SAT si
  `n_activities <= options.cpsat_max_activities` (defecto 3000) y hay tiempo; si no, heurística.
  La heurística (SGS serial por prioridades + búsqueda local por desplazamiento dentro de la holgura)
  debe manejar **100 000 actividades** sin relaciones en < 60 s y 10 000 con relaciones en < 60 s.
- Devuelve también perfiles de carga antes/después para el histograma de comparación.

## 7. Modo mantenimiento (A3: `maintenance/`)

```python
def generate_occurrences(tasks: list[domain.PeriodicTask], horizon_start: date, horizon_end: date,
                         calendars: dict[int, WorkCalendar], default_calendar_id: int) -> list[domain.Occurrence]
def level_periodic(occurrences: list[domain.Occurrence], resources: list[domain.Resource],
                   calendars: dict[int, WorkCalendar], weights: domain.ObjectiveWeights,
                   options: domain.OptimizeOptions) -> domain.OptimizeResult
```
- Frecuencias: `D` diaria, `W` semanal, `M` mensual, `Q` trimestral, `S` semestral, `A` anual,
  `ND` cada N días, `NW` cada N semanas, `NM` cada N meses, `NA` cada N años; `interval` = N.
  Opcional: `preferred_months` (lista `"1,4,7,10"`), `preferred_weekday` (0=lunes), `day_of_month`.
- Cada ocurrencia tiene fecha objetivo (`due_date`) y ventana `[due - tol_before, due + tol_after]`
  (tolerancia en días corridos). Se guardan como filas de `activities` con `periodic_task_id` y
  `occurrence_index`.
- `level_periodic`: nivelación vectorizada (numpy) por rol/recurso y día; asigna cada ocurrencia a un
  día laborable dentro de su ventana (y opcionalmente a un trabajador con la habilidad requerida,
  balanceando HH entre trabajadores). Requisito: **100 000 ocurrencias en < 30 s**.
- Requisitos que requieren indisponibilidad (`requires_outage=1`): agrupar por instalación en la misma
  fecha cuando sea posible (bonificación), útil para coordinar desconexiones con el Coordinador.

## 8. Control, curvas S y desempeño (A4: `control/`)

```python
def scurve(acts: list[domain.ActivityView], baseline: dict[int, domain.BaselineActivity] | None,
           progress: list[domain.ProgressEntry], calendar: WorkCalendar, data_date: date,
           period: str = "week", basis: str = "hours") -> domain.SCurve
def evm(...mismas entradas...) -> domain.EVMResult
def worker_performance(acts, assignments, progress, resources, period_start: date, period_end: date,
                       data_date: date) -> list[domain.WorkerKPI]
```
- Curva S planificada (línea base; si no hay, programa vigente), real (valor ganado acumulado),
  costo/HH real acumulado y **proyección** (pronóstico por SPI(t) de *Earned Schedule* y por
  reprogramación del remanente). Distribución lineal del peso de cada actividad en sus días laborables.
- `basis`: `"hours"` (HH, defecto), `"cost"` o `"count"` (número de actividades).
- EVM: PV, EV, AC, SV, CV, SPI, CPI, SPI(t), ES, EAC, ETC, VAC, fecha de término pronosticada.
- Desempeño por trabajador (período): cumplimiento % (terminadas a tiempo / programadas para
  terminar en el período), eficiencia HH (HH ganadas / HH reales), atraso promedio (días), SPI y CPI
  por trabajador, HH asignadas, HH reales, actividades terminadas/pendientes.

## 9. Informes y Excel (A5)

- `reports.builder.render(report: domain.ReportData, fmt: str, out_path: Path) -> Path` con
  `fmt in {"xlsx","pdf","docx","html"}`. `ReportData` lo arma A1 (`services.build_report_data`).
- Contenido mínimo por período (semanal/mensual/anual): portada, resumen ejecutivo con KPI,
  curva S, EVM, actividades programadas/terminadas/atrasadas del período, próximas del siguiente
  período (lookahead), histograma de carga por recurso, tabla de desempeño por trabajador, hitos.
- Plantillas: `excel_io.templates.build_project_template(path)` y `build_maintenance_template(path)`,
  con hojas `Instrucciones`, listas desplegables (validación de datos), ejemplos y formato.
- `excel_io.importer.parse_workbook(path, mode) -> domain.ImportBundle` (sin tocar BD) con errores
  por fila/columna; soporta 100 000 filas (openpyxl `read_only=True`).
- `excel_io.exporter.export_project(bundle: domain.ImportBundle, path)` genera un archivo que se puede
  reimportar (ida y vuelta).

## 10. API REST (A1 implementa; A6 consume)

Prefijo `/api`. JSON. Paginación con `offset`, `limit`.

```
GET    /api/health
GET    /api/projects                               -> [Project]
POST   /api/projects                               body Project (sin id) -> Project
GET    /api/projects/{pid}                         -> Project + counts
PUT    /api/projects/{pid}                         -> Project
DELETE /api/projects/{pid}
POST   /api/projects/{pid}/duplicate               -> Project

GET/POST        /api/calendars                     ; GET/PUT/DELETE /api/calendars/{cid}
GET             /api/calendars/{cid}/days?start=&end=   -> [{date, hours, working, reason}]
GET/POST        /api/projects/{pid}/wbs            ; PUT/DELETE /api/wbs/{id}
GET             /api/projects/{pid}/activities?offset=&limit=&q=&wbs_id=&status=&from=&to=&resource_id=
GET             /api/projects/{pid}/activities/columnar     -> {ids:[...], codes:[...], names:[...],
                    starts:[...], finishes:[...], es, ef, ls, lf, total_float, critical, pct, wbs_ids,
                    baseline_starts, baseline_finishes, window_starts, window_ends, kinds}  (para Gantt de 100k)
POST            /api/projects/{pid}/activities     body Activity | [Activity]  (alta masiva)
PUT             /api/activities/{id}               ; DELETE /api/activities/{id}
PATCH           /api/projects/{pid}/activities     body [{id, ...campos}]   (edición masiva, p. ej. macro)
GET/POST        /api/projects/{pid}/relations      ; DELETE /api/relations/{id}
GET/POST        /api/resources   (?project_id=)    ; PUT/DELETE /api/resources/{id}
GET/POST        /api/projects/{pid}/assignments    ; PUT/DELETE /api/assignments/{id}
GET/POST        /api/projects/{pid}/periodic-tasks ; PUT/DELETE /api/periodic-tasks/{id}
POST            /api/projects/{pid}/maintenance/generate  body {horizon_start, horizon_end, replace: true} -> {created}

POST   /api/projects/{pid}/schedule/cpm                    -> {run_id, finish, critical_count, warnings}
POST   /api/projects/{pid}/schedule/optimize   body {weights: ObjectiveWeights, options: OptimizeOptions, apply: bool}
                                                           -> OptimizeResult (resumen + perfiles)
GET    /api/projects/{pid}/schedule/runs                   -> historial
POST   /api/projects/{pid}/schedule/runs/{run_id}/apply
GET    /api/projects/{pid}/load?group=resource|skill&period=day|week|month&from=&to=  -> LoadProfile

GET/POST /api/projects/{pid}/baselines ; DELETE /api/baselines/{id} ; POST /api/baselines/{id}/activate
GET/POST /api/projects/{pid}/progress   (POST acepta lista)   ; DELETE /api/progress/{id}
PUT    /api/projects/{pid}/data-date   body {data_date}
GET    /api/projects/{pid}/control/scurve?period=week&basis=hours   -> SCurve
GET    /api/projects/{pid}/control/evm                               -> EVMResult
GET    /api/projects/{pid}/control/performance?start=&end=           -> [WorkerKPI]

POST   /api/projects/{pid}/reports   body {period_type: "weekly"|"monthly"|"annual", period_start, formats: ["pdf","xlsx",...]}
                                     -> [{format, filename, url}]
GET    /api/reports/files/{filename}                          (descarga)
GET    /api/templates/{kind}         kind=proyecto|mantenimiento   (descarga plantilla vacía con ejemplos)
POST   /api/projects/{pid}/import    multipart file, query mode=merge|replace, dry_run=bool -> {summary, errors}
POST   /api/import/new-project       multipart file -> Project   (crea proyecto desde plantilla)
GET    /api/projects/{pid}/export.xlsx
POST   /api/demo/{kind}              kind=proyecto|mantenimiento, query n= (crea proyecto de demostración)
```

Los esquemas JSON de `Project`, `Activity`, etc. son exactamente los campos de las dataclasses de
`domain.py` (A1 los expone con modelos Pydantic equivalentes en `api/schemas.py`).

## 11. Frontend (A6)

Pantallas (barra lateral):
1. **Proyectos**: lista, crear (elige modo), duplicar, importar desde Excel, demo.
2. **Actividades + Gantt** (vista principal tipo P6): tabla a la izquierda (WBS agrupable, columnas
   configurables, edición en línea) y Gantt en **canvas virtualizado** a la derecha (100 000 filas
   fluidas), escala día/semana/mes/trimestre/año, barras actual/línea base/ventana macro, ruta crítica en
   rojo, flechas de relaciones (solo visibles en pantalla), hitos, línea de fecha de corte, arrastrar
   para mover/estirar, crear relación arrastrando entre barras.
3. **Programación macro**: matriz actividades × meses; clic/arrastre para asignar el mes (o rango) de
   cada actividad; selección múltiple; luego "Programar".
4. **Recursos y calendarios**: CRUD, habilidades, capacidad, costos, calendario con vista mensual y feriados.
5. **Optimizar**: elige objetivo(s) con controles deslizantes de peso (4 objetivos + preajustes), método,
   límite de tiempo; muestra histograma de carga antes/después, KPIs (plazo, pico, desviación, HH
   extra, costo, días fuera de ventana) y botón Aplicar.
6. **Mantenimiento** (solo modo mantenimiento): plan maestro de tareas periódicas, generar ocurrencias,
   mapa de calor de carga (día × rol), calendario anual.
7. **Control**: fecha de corte, líneas base, registro de avance (grilla rápida por actividad y
   trabajador: % y HH reales), curvas S (planificada, ganada, real, proyección), EVM.
8. **Desempeño**: tabla y gráficos por trabajador, período seleccionable.
9. **Informes**: elegir período y formatos (xlsx/pdf/docx/html) → descargar; vista HTML en la app.
10. **Importar/Exportar**: descargar plantillas, subir archivo, vista previa de errores, confirmar.

## 12. Rendimiento (criterios de aceptación)

- Crear 100 000 actividades (demo mantenimiento): < 20 s. Cargar Gantt columnar 100 000: < 3 s.
- Nivelación periódica 100 000: < 30 s. CPM 10 000 actividades con 15 000 relaciones: < 5 s.
- Importar Excel de 100 000 filas: < 60 s.

## 13. Pruebas

Cada módulo entrega pruebas `pytest` en `backend/tests/<modulo>/` que corran sin red. Se ejecutan con
`cd backend && python -m pytest -q`.
