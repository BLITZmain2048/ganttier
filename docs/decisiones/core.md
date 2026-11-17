# A1 — Núcleo, persistencia y API

Implementa `SPEC.md` y `_encargos/A1.md`. No modifica `domain.py`, `schema.sql` ni
las carpetas asignadas a otros agentes. No requiere índices ni columnas adicionales.

## Decisiones

- SQLite: una conexión por petición, WAL, synchronous=NORMAL, foreign_keys=ON,
  busy_timeout de 30 s y caché de 64 MiB. `meta.schema_version=1` registra la
  migración; se rechazan versiones futuras. `GANTTIER_DB` cambia la ruta de la BD.
- Las escrituras compuestas usan SAVEPOINT anidados: importar, reemplazar,
  duplicar y aplicar una corrida son atómicos. Los lotes se insertan con
  `executemany` en bloques de 2000 dentro de **una sola transacción**, limitando
  memoria. Las relaciones entre proyectos se rechazan, salvo recursos globales.
- El Gantt usa un SELECT con JOIN a la línea base activa. `kinds` contiene
  `actividad`, `hito` o `mantenimiento`. Los filtros de fecha incluyen actividades
  que se superponen con el rango consultado.
- `macro_month` fija ambos extremos del mes, incluido febrero bisiesto. Editar
  explícitamente una ventana sin enviar el mes limpia el atajo macro. Borrar
  solamente el mes conserva la ventana existente.
- Los calendarios mantienen prefijos y posiciones de días laborales, con margen
  interno de dos años civiles por lado. Los vectores públicos comienzan
  **exactamente en horizon_start**, terminan en horizon_end y son de solo lectura.
  Día con cero horas se considera no laborable. Excepciones prevalecen sobre turno
  y feriados. Una fecha fuera del margen produce ValueError, sin bucles infinitos.
- El respaldo nacional 2024–2030 incluye fechas móviles. Fuera de esos años emite
  una advertencia explícita: debe instalarse `holidays` para calendarios completos.
  Referencia contrastada: [implementación oficial de Chile en holidays](https://raw.githubusercontent.com/vacanza/holidays/dev/holidays/countries/chile.py).
  Los anclajes iniciales de turnos son 2026-01-01 y pueden editarse.
- Modelos Pydantic v2 generados desde los campos y tipos de las dataclasses para
  evitar divergencias. En altas se permite omitir `id` y `project_id`; la ruta y
  SQLite los asignan. PUT valida el objeto combinado y conserva campos omitidos.
- Las dependencias de A2/A3/A4/A5 se importan exclusivamente desde sus interfaces
  públicas al ejecutar el servicio. Un fallo de importación produce HTTP 503 sin
  impedir el arranque. EVM recibe `basis`, sin `period`, conforme a la interfaz
  pública entregada por A4; la curva S sí recibe ambos.
- Las corridas guardan fechas, métricas y asignaciones en `datos/runs/<id>.json`,
  con escritura temporal y reemplazo atómico. CPM se aplica inmediatamente. Una
  optimización se aplica solo a petición, y nunca si es infactible. Si supera
  5000 actividades, la respuesta conserva métricas/perfiles y `run_id`, devuelve
  mapas de fechas/asignaciones vacíos y añade `stored_activity_count`; los mapas
  completos permanecen en el archivo. Los escalares NumPy se normalizan para JSON.
- La aplicación de corridas valida pertenencia, actividades eliminadas y bloqueos.
  La nivelación de mantenimiento rechaza planes con avance o bloqueos: `Occurrence`
  no transporta esos estados y no sería correcto moverlos silenciosamente.
- El avance por actividad usa el último porcentaje al corte y suma consumos
  incrementales. POST/DELETE de avance, importación y cambios de fecha de corte
  sincronizan los resúmenes de `activities` que consume el Gantt. Los informes
  usan semanas lunes–domingo, meses y años civiles; los atrasos se evalúan al corte
  y el lookahead incluye actividades que se superponen con el siguiente período.
- Los perfiles distribuyen HH explícitas por días laborables y calculan carga por
  diferencias y sumas acumuladas NumPy. Recortar la consulta no redistribuye las HH.
  Capacidad = `capacity_hours_day * max_units` en días laborables del recurso,
  consistente con A3. Agrupar por habilidad muestra la disponibilidad potencial:
  un trabajador con varias habilidades contribuye a cada una, por lo que las
  capacidades de diferentes habilidades **no deben sumarse**.
- Importación resuelve nombres/códigos antes de persistir. `merge` actualiza por
  código; asignaciones se reemplazan por actividad y avance por actividad/fecha/
  recurso para permitir reimportación. `replace` conserva el proyecto y calendarios
  globales, pero sustituye su estructura, recursos locales, avance, líneas base e
  historial de corridas. Los datos del proyecto presentes en el libro se actualizan
  conservando su id. Los calendarios de igual nombre se reutilizan sin alterar
  otros proyectos. Se rechazan nombres de recurso ambiguos antes del intercambio.
- La duplicación conserva estructura, avance y líneas base con ids nuevos; no
  duplica el historial de corridas. Los archivos de corridas antiguos no se borran
  automáticamente al eliminar registros, para evitar pérdidas ajenas a SQLite.
- Informes y temporales residen en `datos/informes` y `datos/tmp`. Las descargas
  rechazan rutas fuera del directorio. Temporales de Excel se eliminan al terminar
  la respuesta. La importación de proyecto nuevo permite autodetectar el modo con
  `parse_workbook(path, None)` y admite `?mode=proyecto|mantenimiento` explícito.
- La demo de obra tiene 80 actividades, seis WBS, 12 cuadrillas de dos personas,
  relaciones FS/SS, HH y costos CLP; su horizonte permite nivelación factible.
  La demo de mantenimiento crea 40 tipos repetidos por instalación, 45 trabajadores
  y exactamente `n` ocurrencias; **siempre** llama a `generate_occurrences` de A3.

## Archivos entregados

```text
backend/ganttier/calendars.py
backend/ganttier/db.py
backend/ganttier/repo.py
backend/ganttier/services.py
backend/ganttier/main.py
backend/ganttier/api/__init__.py
backend/ganttier/api/schemas.py
backend/ganttier/api/dependencies.py
backend/ganttier/api/resources.py
backend/ganttier/api/planning.py
backend/ganttier/api/control.py
backend/ganttier/api/files.py
backend/tests/core/conftest.py
backend/tests/core/test_calendars.py
backend/tests/core/test_repo.py
backend/tests/core/test_services.py
backend/tests/core/test_api.py
requirements.txt
instalar.bat
iniciar.bat
docs/decisiones/core.md
```

## Firmas públicas

`conn` es `sqlite3.Connection`; `d` es `ganttier.domain`. Las funciones CRUD
genéricas reciben nombres de tablas de la lista permitida y devuelven dataclasses
para las tablas con tipo de dominio, o dicts para metadatos/excepciones/corridas.
`baseline_activities` usa las funciones específicas por su clave compuesta;
`meta` se lee con `list_entities` y se modifica con `set_meta`.

```python
# calendars.py
WorkCalendar(cal: d.Calendar, horizon_start: date, horizon_end: date)
WorkCalendar.is_workday(d: date) -> bool
WorkCalendar.hours(d: date) -> float
WorkCalendar.next_workday(d: date, include: bool = True) -> date
WorkCalendar.prev_workday(d: date, include: bool = True) -> date
WorkCalendar.add_workdays(start: date, n: int) -> date
WorkCalendar.workdays_between(a: date, b: date) -> int
WorkCalendar.workday_index() -> numpy.ndarray
WorkCalendar.day_hours_vector() -> numpy.ndarray
load_calendars(conn) -> dict[int, WorkCalendar]
ensure_default_calendars(conn) -> None

# db.py
database_path() -> Path
connect(path: str | Path | None = None) -> sqlite3.Connection
initialize(conn) -> None

# repo.py
transaction(conn) -> contextmanager
json_value(value: Any) -> Any
get(conn, table: str, entity_id: int) -> Any
list_entities(conn, table: str, project_id: int | None = None, *,
              offset: int = 0, limit: int | None = None,
              filters: Mapping[str, Any] | None = None) -> list
create(conn, table: str, obj: Any) -> Any
create_many(conn, table: str, objects: list[Any], *, return_entities: bool = True) -> list
update(conn, table: str, entity_id: int, changes: Mapping[str, Any]) -> Any
delete(conn, table: str, entity_id: int) -> None
patch_activities(conn, pid: int, patches: list[dict]) -> list[d.Activity]
activities_columnar(conn, pid: int) -> dict[str, list]
save_occurrences(conn, pid: int, occurrences: list[d.Occurrence], replace: bool = True,
                 *, return_entities: bool = True) -> list[d.Activity] | int
save_import_bundle(conn, pid: int, bundle: d.ImportBundle, mode: str = "merge") -> dict[str, int]
export_bundle(conn, pid: int) -> d.ImportBundle
duplicate_project(conn, pid: int) -> d.Project
save_baseline_activities(conn, entries: list[d.BaselineActivity]) -> None
delete_baseline_activity(conn, baseline_id: int, activity_id: int) -> None
set_meta(conn, key: str, value: str | None) -> None

# services.py
excel_functions() -> tuple  # cuatro funciones públicas del módulo Excel
build_activity_views(conn, pid: int) -> list[d.ActivityView]
sync_progress(conn, pid: int, activity_ids: list[int] | None = None) -> None
build_problem(conn, pid: int) -> d.ScheduleProblem
run_cpm(conn, pid: int) -> dict
run_optimize(conn, pid: int, weights: d.ObjectiveWeights | None = None,
             options: d.OptimizeOptions | None = None, apply: bool = False) -> dict
apply_run(conn, pid: int, run_id: int) -> dict
generate_maintenance(conn, pid: int, horizon_start: date, horizon_end: date,
                     replace: bool = True) -> dict
create_baseline(conn, pid: int, name: str = "Línea base", activate: bool = True) -> d.Baseline
control_result(conn, pid: int, kind: str, period: str = "week", basis: str = "hours",
               start: date | None = None, end: date | None = None) -> Any
load_profile(conn, pid: int, group: str = "resource", period: str = "day",
             from_date: date | None = None, to_date: date | None = None) -> d.LoadProfile
build_report_data(conn, pid: int, period_type: str, period_start: date) -> d.ReportData
render_reports(conn, pid: int, period_type: str, period_start: date,
               formats: list[str]) -> list[dict]
demo_project(conn: sqlite3.Connection | None = None) -> d.Project
demo_maintenance(n: int = 10000, conn: sqlite3.Connection | None = None) -> d.Project

# main.py
create_app() -> FastAPI
main() -> None
```

Todos los endpoints de SPEC §10 se registran bajo `/api`; sus parámetros y modelos
se publican en `/openapi.json` y `/docs`. `from` y `to` son alias HTTP de
`from_date` y `to_date`, ya que `from` es palabra reservada de Python.

## Verificación

Pruebas sin red, desde `backend`:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest tests/core -q -p no:cacheprovider --basetemp=tests/core/.tmp/pytest
```

Las pruebas crean y eliminan sus archivos dentro de `tests/core/.tmp`.
Resultado final: **27 aprobadas, 2 omitidas**, en 16,04 s. Las omisiones corresponden
al módulo de pruebas HTTP completo (FastAPI ausente) y al contraste con el paquete
`holidays` (ausente). Las pruebas del respaldo de feriados sí se ejecutaron.

Mediciones de aceptación en este equipo, con variación por carga concurrente:

| Operación | Resultado verificado | Límite |
|---|---:|---:|
| Alta de 100 000 actividades en BD temporal WAL | 11,538 s | < 20 s |
| Gantt columnar de esas 100 000 filas, un SELECT | 2,325 s | < 3 s |
| Demo completa de 100 000 ocurrencias con A3 real y BD WAL | 10,149 s | < 20 s |

Comprobaciones adicionales ejecutadas:

- 30 modelos Pydantic con conjuntos de campos idénticos a las dataclasses.
- Sintaxis Python verificada para los 17 archivos de implementación y pruebas.
- Integración real A2: CPM de 80 actividades; optimización heurística factible
  sin advertencias. A3: nivelación y aplicación de 1000 ocurrencias, sin advertencias.
- Integración real A4: EVM, curva S y composición del informe mensual/semanal.
- Integración real A5: exportación y reimportación de 80 actividades con 0 incidencias,
  persistencia del libro importado, informes HTML (260 470 bytes) y XLSX (56 344 bytes).

Limitaciones de verificación: el Python disponible carece de FastAPI, holidays,
ReportLab, python-docx y OR-Tools. HTTPX, multipart y Pydantic existen en otro entorno
local; se usó Pydantic para comprobar modelos sin modificar ese entorno. Se intentó
instalar dependencias dentro de `tests/core`, pero la red está bloqueada; no se
modificó la instalación global. Por tanto, no se ejecutaron TestClient, arranque
Uvicorn/navegador, generadores PDF/DOCX ni el método CP-SAT. Los tests HTTP completos
y `requirements.txt` quedan preparados para ejecutarlos tras `instalar.bat`.

La generación de archivos y scripts de arranque se limita a las rutas del proyecto.
No se editaron archivos de otros agentes ni el esquema/contrato compartido.
