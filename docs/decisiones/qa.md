# QA de integración y bordes — 26 de septiembre de 2026

## Alcance y evidencia

Se revisaron SPEC §10/§12, domain.py y decisiones/core.md. Los cambios de producción
se limitaron a `api/schemas.py`, `api/dependencies.py`, `api/control.py`, `repo.py`
y `services.py`. Se agregaron cinco archivos `tests/e2e/test_qa_*.py` y `pytest.ini`.
No se editaron `demo_project`, `demo_maintenance`, `_DEMO_MT_TYPES`, domain.py,
engine/, maintenance/, control/, reports/ ni excel_io/. La prueba e2e de Claude
permanece intacta.

Las pruebas usan BD temporal y `GANTTIER_DB`/`GANTTIER_DATA_DIR`; también ajustan
`services.DATA_DIR`, pues esa constante se calcula al importar el módulo.
Se ejecutó con `PYTHONDONTWRITEBYTECODE=1`, temporales de pytest dentro de
`backend/tests/e2e/.tmp`, sin instalar ni modificar paquetes globales.

**No se certifica la API HTTP en este entorno.** Falta FastAPI y no se pudo
instalar con pip, ni siquiera indicando PyPI. Las pruebas TestClient se omiten
con `importorskip`; PDF/DOCX y CP-SAT también se omiten si falta su dependencia.
Se pudo usar Pydantic 2 del entorno local `tools/.venv/Lib/site-packages` mediante
PYTHONPATH, sin escribir en ese entorno. Los motores heurísticos se probaron reales.

## Defectos corregidos

| ID | Severidad | Reproducción anterior y cambio |
|---|---|---|
| QA-01 | Crítica | Generar mantenimiento, guardar avance y línea base, volver a generar con `replace=true`: DELETE en cascada perdía avance y snapshots. Ahora se conservan ids, avance, asignaciones de trabajo registrado y filas de línea base. Las ocurrencias pendientes se actualizan; las obsoletas sin historia se eliminan. |
| QA-02 | Alta | Optimizar mantenimiento, registrar avance, aplicar la corrida anterior: movía trabajo registrado. Ahora se rechaza antes de escribir, incluso si no se sincronizó el resumen o el registro está después del corte. |
| QA-03 | Alta | Crear A→B→C→A: la última relación se almacenaba y solo fallaba CPM. Validación iterativa del grafo al escribir, con rollback de lotes/importaciones. |
| QA-04 | Alta | POST de avance al 100 % sin inicio real, o inicio/fin real posterior a la fecha del registro: se aceptaba. El adaptador valida inicio explícito o conocido en el historial; el repositorio valida cronología. El fin anterior a un inicio previo también se rechaza. |
| QA-05 | Alta | Optimizar una tarea de 16 HH, cuadrilla de dos trabajadores de 8 HH, y aplicar: solo se guardaba al líder con 16 HH. Ahora se serializa la extensión pública `crew_assignment` y se distribuyen HH/unidades entre todos los integrantes. |
| QA-06 | Alta | Volver a optimizar esa misma tarea después de aplicar: las asignaciones calculadas se confundían con recursos fijos, produciendo infactibilidad. Ahora la asignación fija procede del plan maestro. |
| QA-07 | Alta | PUT con `duration_days="3"`/`calendar_id="5"`: Pydantic aceptaba y convertía, pero se enviaba el diccionario original al repositorio; podía producir TypeError/KeyError. Se persisten los valores convertidos, únicamente para los campos presentes. Caso HTTP preparado; no ejecutado por falta de FastAPI. |
| QA-08 | Media | Se aceptaban meses preferidos `0,13`/`ene`, horizonte periódico invertido y nombre de línea base formado por espacios. Nuevas validaciones de esquema verificadas antes/después. |
| QA-09 | Alta | PATCH con `id=[]`/`id={}` o id/duración `2**100`: errores de binding/OverflowError no manejados, que la app convertiría en 500. El repositorio valida tipo del id y rango INTEGER de SQLite antes del binding; incluye filtros y paginación. |
| QA-10 | Media | Lectura columnar hacía un append por celda; control reconstruía Activity completas y leía asignaciones repetidas. Columnar ahora transpone lotes de 4000 filas; control proyecta solo los campos de ActivityView y reutiliza las lecturas de historial/asignaciones. |

Las regresiones QA-01 a QA-06, QA-08 y QA-09 se ejecutaron primero fallando y
después pasando. Para QA-07 se verificó la causa en código y se dejó la
regresión TestClient pendiente de ejecución. Los errores nuevos son ValueError
en español, que el manejador existente traduce a 422 con `detail`.

### Semántica de conservación y fechas

- `replace=false` no duplica ni cambia ocurrencias existentes. `replace=true`
  mantiene los ids de códigos existentes; no altera ocurrencias bloqueadas,
  con avance, fechas reales o registros de progreso. Una ocurrencia obsoleta
  referenciada por línea base también se conserva, para no borrar la historia.
- La fecha futura de un registro real se evalúa respecto de la fecha de su
  registro. Las fechas reales de una Activity se comparan con el corte del
  proyecto (o hoy si no tiene corte). Esto permite importar ejemplos con corte
  simulado en 2027 y consultar registros posteriores a un corte anterior.
- La terminación sin inicio se rechaza en POST de avance y en el modelo Activity
  de la API. El repositorio conserva compatibilidad con snapshots históricos
  de porcentaje sin inicio; la importación no inventa una fecha real.
- Una corrida antigua de proyecto sigue siendo aplicable si sus actividades
  existen y respeta bloqueos. Las de mantenimiento con avance se rechazan.

## Cobertura agregada

- `test_qa_api.py`: TestClient, CRUD/duplicación/borrado, altas y PATCH atómicos,
  fechas inválidas, duración negativa, macro_month, duplicados, WBS cíclica,
  relaciones cíclicas/consigo misma/entre proyectos, recursos y asignaciones
  ajenos, calendarios y turnos, parámetros inválidos y proyectos inexistentes.
  Flujo CPM→línea base→corte→avance→CPM, tres bases de control y tres períodos,
  EV≤BAC, curva final≈100, desempeño, carga, borrado de avance y línea base.
  Generación repetida, optimizar/aplicar/historial/corridas antiguas; 4 formatos
  × 3 períodos y descarga; traversal; plantillas, libros de ejemplos,
  dry_run comprobado con iterdump idéntico, merge/replace y roundtrip.
- `test_qa_regressions.py`: regresiones del repositorio y servicios con motores
  reales; informes 4×3, omitiendo únicamente los formatos sin dependencia.
- `test_qa_schemas.py`: esquemas Pydantic sin necesidad de arrancar FastAPI.
- `test_qa_imports.py`: lectura y persistencia de ambas plantillas y ambos
  ejemplos; resolución de referencias de avance a ocurrencias generadas.
  **No equivale a haber ejecutado `/import/new-project`**.
- `test_qa_performance.py`: aceptación HTTP 100k y diagnóstico de servicios
  independiente. Ambos usan `@pytest.mark.slow`. El diagnóstico aísla Excel
  en un subproceso con tope de 65 s y deja tiempos/traza en el temporal.

`pytest.ini` registra `slow` y `addopts = -m 'not slow'`. Los benchmarks de
otros propietarios que ya existían sin ese marcador no fueron modificados.

## Resultados y comandos

Desde `Ganttier/backend`, en PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$PWD/../../tools/.venv/Lib/site-packages"
$env:TEMP = "$PWD/tests/e2e/.tmp"
$env:TMP = $env:TEMP
$env:MPLCONFIGDIR = "$PWD/tests/e2e/.tmp/matplotlib"

python -m pytest tests/e2e/test_qa_api.py tests/e2e/test_qa_schemas.py tests/e2e/test_qa_regressions.py tests/e2e/test_qa_imports.py tests/e2e/test_qa_performance.py tests/core tests/control tests/engine tests/maintenance/test_generator.py tests/maintenance/test_leveling.py tests/excel_io/test_templates.py tests/excel_io/test_reader.py --import-mode=importlib -q -k 'not demo_project_and_maintenance_with_public_stub and not 100000 and not 100k' -p no:cacheprovider --basetemp=tests/e2e/.tmp/final-validation

python -m pytest -m slow -s tests/e2e/test_qa_performance.py -q -p no:cacheprovider --basetemp=tests/e2e/.tmp/performance-final
```

La ampliación ejecutada: **292 aprobadas, 21 omitidas, 6 deseleccionadas, 38,96 s**.
Ese conteo precede a los cinco casos adicionales de ids/overflow, verificados
en la pasada final de borde. La selección excluye un test previo de demo que
importa FastAPI sin importorskip: al ejecutarlo falló por dependencia ausente,
no por una aserción funcional. También se evita ejecutar benchmarks ajenos.
El e2e original no se ejecutó al faltar FastAPI. No se declara verde la suite
completa por defecto en este equipo.

La pasada final de borde (cinco archivos QA salvo rendimiento, más
`tests/core/test_repo.py`) terminó con **37 aprobadas, 7 omitidas, 17,47 s**;
incluye los cinco casos de ids/overflow ya corregidos.

La prueba lenta: **1 fallida por timeout de exportación y 1 omitida (HTTP)**,
158,42 s. Sus resultados se guardaron en
`backend/tests/e2e/.tmp/performance-final/test_services_100k_diagnostic0/`.

| Operación 100k | Pasada completa de servicios, s | Límite solicitado, s |
|---|---:|---:|
| Demo | 10,877 | 20 (§12) |
| Columnar | 1,202 | 3 |
| Optimizar heurístico, time_limit_s=30 | 69,200 | 60 |
| Curva S | 5,639 | 5 |
| Desempeño | 4,936 | 5 |
| Exportar XLSX | >65; interrumpida a 65,107 | 60 |

Tres lecturas posteriores de la misma BD, sin volver a generar/optimizar:
columnar **0,517 / 0,583 / 0,579 s**; curva S **1,627 / 4,236 / 4,306 s**;
desempeño **3,733 / 3,711 / 3,742 s**. Se preservan todas las muestras:
`read-times.json`. La curva terminó en 100 %. La variación y la primera muestra
de 5,639 s impiden certificar un límite estricto de 5 s para toda condición.
Estos son tiempos de SQL/servicios/motores, **sin transporte, JSON HTTP ni gzip**.

## Pendientes con reproducción mínima

### P-01 — Alta: mantenimiento 100k infactible y fuera del tiempo total

```text
POST /api/demo/mantenimiento?n=100000
POST /api/projects/{id}/schedule/optimize
{"options":{"method":"heuristic","time_limit_s":30}}
```

Equivalente sin HTTP: `services.demo_maintenance(100000, conn)` seguido de
`services.run_optimize(conn, pid, options=OptimizeOptions(method="heuristic", time_limit_s=30))`.
Resultado medido: **69,200 s**, `status="infactible"`, tiempo interno del
nivelador **53,230 s**, 28 700 ocurrencias que requieren revisar capacidad,
dotación/calendario y 65 182 HH sobre el límite permitido. Se almacenan 100 000
cuadrillas en `runs/1.json`. La API no debe aplicar ese resultado.

Requiere revisar con el propietario de maintenance/ y de la demo si el caso
debe ser factible y cómo reducir el costo del nivelador. `time_limit_s` es un
objetivo blando en el algoritmo actual. No se alteraron esos módulos ni la demo.
El tiempo total incluye además carga de objetos y persistencia en servicios.

### P-02 — Alta: exportación 100k excede 60 s

```text
POST /api/demo/mantenimiento?n=100000
GET /api/projects/{id}/export.xlsx
```

Reproducción ejecutada sin HTTP: `excel_functions()[3](repo.export_bundle(conn, pid), path)`.
El subproceso superó 65 s; `export-timeout.txt` registró ejecución en
`openpyxl/styles/alignment.py` y `openpyxl/compat/strings.py` a los 55 s.
`excel_io/templates.py::_table_sheet` crea y asigna Font/Alignment por celda
en un workbook normal, también al exportar 100k. Esa es la ruta que debe perfilar
su propietario; no se modificó excel_io/. Una primera corrida sin límite fue
interrumpida tras varios minutos, sin archivo terminado.

### P-03 — Media: aceptación HTTP y latencia de control pendientes

Instalar las dependencias en un entorno autorizado y ejecutar las pruebas
TestClient y `-m slow`. PDF/DOCX, descargas, `dry_run`, merge/replace y
`import/new-project` tienen casos HTTP preparados, pero todavía no evidencia
HTTP en este entorno. La latencia de curva S necesita repetición bajo carga
controlada: tuvo una muestra >5 s aun después de reducir el costo de lectura.
