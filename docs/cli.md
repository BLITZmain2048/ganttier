# CLI de Ganttier 1.0

Referencia del contrato [CLI_SPEC](../trabajos/cli_codex_20261019/CLI_SPEC.md), incluidas las precisiones de §8. La CLI usa el motor de la aplicación sin abrir Qt ni iniciar un servidor. Python 3.10 o superior; dependencias de `requirements.txt` en la raíz. OR-Tools es opcional para la instalación, pero obligatorio al pedir `cpsat` o roles indivisibles.

## Instalación e invocación

Desde la raíz de Ganttier, Windows con PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\ganttier-cli.bat describir
cd backend
..\.venv\Scripts\python.exe -m ganttier.cli describir
```

Linux/WSL:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash ganttier-cli.sh describir
cd backend
../.venv/bin/python -m ganttier.cli describir
```

Las dependencias opcionales se instalan desde la raíz con `python -m pip install -r requirements-opcional.txt`, usando el intérprete del entorno. El wrapper Windows selecciona `.venv\Scripts\python.exe` y UTF-8. El de Linux selecciona `GANTTIER_PYTHON`, `.venv/bin/python` o `python3`. En los ejemplos siguientes, `python` debe ser el intérprete del entorno y el directorio actual debe ser `backend/`. Las rutas de archivos son relativas a ese directorio, salvo que se indiquen absolutas.

Seleccione siempre una base de trabajo explícita; para ensayos use `trabajos/_temporales/`. Ejemplo sobre una base ya creada allí:

```powershell
$env:GANTTIER_DB = (Resolve-Path ..\trabajos\_temporales\mi_ensayo\test.db).Path
$env:GANTTIER_DATA_DIR = Join-Path (Split-Path $env:GANTTIER_DB) 'data'
$env:GANTTIER_CLI_ESCRITURA = '0'
$env:GANTTIER_CLI_PARALELO = '2'
```

```bash
export GANTTIER_DB="$PWD/../trabajos/_temporales/mi_ensayo/test.db"
export GANTTIER_DATA_DIR="$PWD/../trabajos/_temporales/mi_ensayo/data"
export GANTTIER_CLI_ESCRITURA=0 GANTTIER_CLI_PARALELO=2
```

No existe un comando `base crear` en el contrato. Para preparar una copia de una base autorizada, use `--db RUTA_ORIGEN base copiar --destino RUTA_NUEVA`; `base copiar` exige un destino inexistente. Las pruebas TST inicializan una base independiente mediante `db.initialize`.

## Globales y variables de entorno

Los globales van **antes** del grupo: `python -m ganttier.cli -p 3 --escribir actividades actualizar 10 --archivo cambio.json`.

| Global | Efecto |
|---|---|
| `--db RUTA` | SQLite; precede a `GANTTIER_DB`; sin ambos usa `datos/ganttier.db`. |
| `--data-dir RUTA` | Carpeta de `runs/`, `informes/`, `respaldos/`; precede a `GANTTIER_DATA_DIR`. Con `--db` explícito y sin esa variable usa la carpeta de la base; sin rutas explícitas, `Ganttier/datos`. |
| `-p X`, `--proyecto X` | ID o nombre exacto único. Prefiera ID si hay homónimos. |
| `--escribir` | Autoriza persistencia en la base seleccionada; también `GANTTIER_CLI_ESCRITURA=1`. |
| `--forzar-bloqueo` | Ignora el bloqueo de GUI y deja una advertencia; requiere decisión expresa del operador. |
| `--sin-respaldo` | Omite el respaldo previo; no es apropiado para el flujo normal. |
| `--salida ARCHIVO` | Guarda el sobre completo y también lo emite por stdout. |
| `--compacto` | Una línea JSON; predeterminado cuando stdout no es TTY. |
| `--verbose` | Logs y trazas por stderr. |
| `--version` | Versión de CLI y aplicación dentro del sobre. |

`GANTTIER_CLI_PARALELO` fija el valor predeterminado de `--paralelo`. Sin él: `max(1,min(4,cpu_count()//2))`. `GANTTIER_PYTHON` solo afecta a los wrappers. `PYTHONUTF8=1` evita depender de la página de códigos de la terminal. No exporte permanentemente `GANTTIER_CLI_ESCRITURA=1`: vuelve persistentes las mutaciones de futuras invocaciones.

## Salida y códigos de proceso

Stdout contiene exactamente un objeto JSON UTF-8 sin BOM, incluso en ayuda y errores. No mezcle stderr con stdout al analizarlo. El esquema es:

```json
{"ok":true,"command":"estado","cli_version":"1.0","ganttier":{"version":"1.0.0","schema":6},"db":{"path":"/ruta/test.db","data_dir":"/ruta/data","mode":"read","backup":null,"locked_by":null},"data":{},"warnings":[],"error":null,"elapsed_s":0.12}
```

Los valores de versión/esquema son ilustrativos: lea los emitidos. `db.mode` es `read`, `simulated` o `write`. `error`, cuando existe, contiene `code`, `message`, `detail`, `hint`; `ok` es falso. Códigos de error: `uso`, `datos_invalidos`, `no_encontrado`, `base_ocupada`, `escenario_cambio`, `dependencia_faltante`, `timeout`, `interno`, `infactible`.

| Exit | Interpretación |
|---|---|
| 0 | Éxito. |
| 1 | Uso, datos inválidos o elemento inexistente. |
| 2 | Resultado negativo legítimo: violaciones, infactibilidad o ninguna variante factible. `ok=true`, `error=null`; examine `data`. |
| 3 | Base ocupada o escenario distinto del que originó el resultado. |
| 4 | Tiempo/presupuesto agotado o cancelación; puede haber resultados parciales. |
| 5 | Error interno; conserve stderr y no aplique resultados. |
| 6 | Dependencia faltante. |

## Seguridad

Las lecturas abren SQLite en modo de solo lectura y no migran. Las mutaciones sin `--escribir` trabajan en una copia SQLite consistente con su propia carpeta de datos, descartada al terminar. No persisten IDs creados en simulación. Los archivos solicitados explícitamente para exportaciones, JSONL o `--salida` son entregables, no cambios en el escenario.

`programa resolver` sin `--aplicar` no modifica la base ni deja corridas en `runs/` real, incluso si se indicó `--escribir`. `programa aplicar` sin `--escribir` simula; `resolver --aplicar` requiere escritura. Las escrituras comprueban bloqueo, crean respaldo verificado y usan transacción. Los cambios de fechas deben pasar el verificador independiente. La aplicación de resultados exige el mismo hash de escenario; no edite el proyecto entre comparar y aplicar.

La GUI con esa base permite lecturas y simulaciones; la escritura devuelve 3. No borre archivos `.lock` para eludir esa protección. Si el resultado de mantenimiento creó ocurrencias en la copia, aplicar puede regenerar y resolver con los mismos parámetros: solo acepta factibilidad y un deterioro dentro de `--tolerancia` (0 por defecto). Un fallo de verificación posterior exige restauración del respaldo.

## Catálogo de comandos

`describir` es el catálogo autogenerado con ayuda, argumentos, tipos, valores por defecto, obligatoriedad y esquemas `variant`, `plan`, `weights`, `options`. Use `describir --comando "programa comparar"` para confirmar las opciones de la versión instalada. Los ejemplos con IDs requieren sustituirlos por los de `proyectos listar` y `actividades listar`.

### Sistema

| Comando y ejemplo | Resultado |
|---|---|
| `describir` / `describir --comando "control avance"` | Catálogo completo o filtrado. |
| `estado` | Versiones, esquema, rutas, tamaño, bloqueo, OR-Tools, CPU, Python y proyectos. |
| `base copiar --destino ../trabajos/_temporales/copia.db` | Copia consistente nueva. |
| `base respaldar --motivo "Antes de comparar"` | Respaldo verificado. |
| `base respaldos` | Inventario de respaldos. |
| `base verificar` | Integridad SQLite y respaldos. |
| `api GET /api/projects` | API FastAPI en proceso, con credencial interna. |
| `api GET /api/projects/3/activities --consulta limit=20 offset=0` | Consulta con parámetros. |
| `api POST /api/projects --archivo proyecto.json` | Mutación API simulada; con `--escribir`, persistente. |
| `api GET /api/projects/3/export.xlsx --binario-a ../trabajos/exportacion.xlsx` | Destino para respuesta binaria; confirme la ruta de la API instalada. |
| `servir --puerto 8765` | API local con URL y token; permanece ejecutando hasta Ctrl+C. |

No registre ni publique el token de `servir`. Su comportamiento es de proceso persistente; no lo trate como una consulta breve ni espere EOF inmediato.

### Modelo

`proyectos listar`, `proyectos ver 3`, `proyectos resumen 3`, `proyectos crear --archivo proyecto.json`, `proyectos duplicar 3`, `proyectos eliminar 3 --confirmar-nombre "Nombre exacto"`.

Ejemplo `proyecto.json`:

```json
{"name":"Ensayo de obra","mode":"proyecto","start_date":"2027-01-04","data_date":"2027-01-04"}
```

Todas las entidades de `repo.ENTITIES` tienen `listar`, `ver ID`, `agregar --json/--archivo`, `actualizar ID --json/--archivo`, `eliminar ID`. Sus alias actuales son:

| Tabla | Grupo en español |
|---|---|
| projects | proyectos |
| activities | actividades |
| resources | recursos |
| relations | relaciones |
| assignments | asignaciones |
| calendars | calendarios |
| periodic_tasks | periodicas |
| baselines | lineas-base (también baselines) |
| baseline_activities | actividades-base |
| progress | avances |
| wbs | wbs |

También existen `equipos`, `ausencias`, `rutas`, con las mismas cinco acciones. Los nombres ingleses del dominio siguen siendo las claves JSON. Para líneas base y avances operativos prefiera los comandos `control`, que sincronizan los servicios correspondientes.

```bash
python -m ganttier.cli -p 3 actividades listar --limite 20 --desde 0
python -m ganttier.cli -p 3 actividades ver 90
python -m ganttier.cli -p 3 actividades agregar --archivo actividad.json
python -m ganttier.cli -p 3 actividades actualizar 90 --archivo cambio.json
python -m ganttier.cli -p 3 actividades eliminar 90
python -m ganttier.cli -p 3 recursos listar --kind trabajador
python -m ganttier.cli -p 3 relaciones cadena --archivo cadena.json
```

`actividad.json`: `{"code":"NUEVA","name":"Montaje","duration_days":2,"work_hours":16,"required_skill":"electrico"}`. `cambio.json`: `{"name":"Montaje revisado"}`. `cadena.json`: `{"codigos":["T1","T2","T3"],"tipo":"FS","desfase":0}`. Las listas aceptan filtros de campos y devuelven `total`; consulte sus campos en `describir`. El ID de `actividades-base` es `BASELINE_ID:ACTIVITY_ID`; el de ausencias, `RECURSO_ID:AAAA-MM-DD`.

Edición masiva: `-p 3 lote aplicar --archivo ops.json --atomico`. Una transacción, todo o nada; se devuelven IDs y errores por fila:

```json
[{"op":"actualizar","entidad":"actividades","id":90,"valores":{"name":"Revisada"}},{"op":"crear","entidad":"actividades","valores":{"code":"NUEVA","name":"Nueva tarea","duration_days":1,"work_hours":8}}]
```

### Importación, exportación y demos

| Comando | Ejemplo de argumentos |
|---|---|
| `importar excel` | `entrada.xlsx --nuevo-proyecto` o, con `-p 3`, `entrada.xlsx --en-proyecto` |
| `importar json` | `caso.json` |
| `importar inventario` | `inventario.xlsx` |
| `importar detectar` | `entrada.xlsx` |
| `exportar xlsx` | `--nominal --a ../trabajos/proyecto.xlsx` |
| `exportar gantt` | `--a ../trabajos/gantt.xlsx` |
| `exportar json` | `--a ../trabajos/proyecto.json` |
| `plantilla proyecto` | `--a ../trabajos/plantilla_proyecto.xlsx` |
| `plantilla mantenimiento` | `--a ../trabajos/plantilla_mantenimiento.xlsx` |
| `plantilla inventario` | `--a ../trabajos/plantilla_inventario.xlsx` |
| `demo crear proyecto` | Demo oficial de proyecto. |
| `demo crear mantenimiento` | `--n 8` para un ejemplo pequeño. |

Las exportaciones de proyecto requieren `-p`. `exportar json` produce un documento reimportable de entidades, no un respaldo completo de SQLite ni de informes/corridas. El importador asistido puede exigir revisión y aceptación de supuestos; consulte su salida y [manual](manual_usuario.md). No afirme que un inventario sin tareas genera un programa. La cantidad `--n` corresponde a mantenimiento; la demo de proyecto usa su tamaño oficial.

### Programa

| Comando | Uso |
|---|---|
| `programa diagnosticar` | Demanda/capacidad por habilidad/período, intensidad, ventanas y advertencias. Ejecutar antes de resolver. |
| `programa verificar --fechas actuales` | Red y verificador independiente; exit 2 si hay violaciones. |
| `programa cpm` | Ruta crítica, fechas y holguras; simulación por defecto. CPM no sustituye nivelación. |
| `programa carga --agrupar habilidad --periodo semana` | Perfil; grupos `recurso`/`habilidad`, períodos `dia`/`semana`/`mes`, rango `--desde`/`--hasta`. |
| `programa resolver --objetivo plazo --metodo heuristic --tiempo 10 --semilla 7` | Una variante aislada con límite duro. |
| `programa comparar --objetivos plazo,nivelar,equilibrado --paralelo 2 --tiempo 10 --mejor-a mejor.json --resultados-a resultados.jsonl` | Tres variantes y ranking. Alternativas: `--plan plan.json` o `--semillas 0,1,2`. |
| `programa lote --plan plan.json --paralelo 2 --presupuesto 120 --resultados-a lote.jsonl --top 3` | Barrido incremental; añadir `--reanudar` para continuar. |
| `programa aplicar --resultado mejor.json` | Simula aplicar la ganadora; añadir global `--escribir` para persistir. |
| `programa aplicar --resultado lote.jsonl --variante v0 --tolerancia 0` | Selección por ID estable. |
| `programa corridas listar` | Historial persistido. |
| `programa corridas ver 5` | Datos de corrida existente. |
| `programa corridas aplicar 5` | Aplicación segura de historial, simulada por defecto. |

Opciones de resolver: `--objetivo plazo|nivelar|ventanas|costo|equilibrado`, `--pesos JSON`, `--metodo auto|heuristic|cpsat`, `--tiempo S`, `--semilla N`, `--modo-roles fractional|indivisible`, `--desde YYYY-MM-DD`, `--hasta YYYY-MM-DD`, `--regenerar`, `--sin-horas-extra`, `--opciones JSON`, `--aplicar`. Elija opciones contra el esquema `options` de `describir`; no suponga que un flag traduce literalmente al nombre de un campo.

Comparar acepta `--ranking-por "load_std,makespan_days"` o `--ranking-por "score:level=1,makespan=0.2"`. El orden por defecto es factibilidad descendente, actividades fuera de ventana, HH extra, plazo y desviación de carga ascendentes. Lote admite `--parar-si-mejora-menos-de X`; use un umbral solo si acepta detener la búsqueda por mejora marginal.

### Control e informes

Todos requieren `-p ID`. Las mutaciones se simulan salvo global `--escribir`.

| Comando | Ejemplo |
|---|---|
| `control avance registrar` | `--archivo avance.json` |
| `control avance lote` | `--archivo avances.json` o `avances.csv` |
| `control avance listar` | Historial. |
| `control fecha-corte ver` | Corte vigente. |
| `control fecha-corte fijar` | `2027-01-05` |
| `control linea-base crear` | Captura y activa el programa vigente. |
| `control linea-base activar` | `2` (ID de línea base). |
| `control linea-base listar` | Líneas base del proyecto. |
| `control curva-s` | `--base horas --periodo semana`; bases `horas|costo|actividades`, períodos `semana|mes`. |
| `control evm` | PV, EV, AC e índices. |
| `control evm-por` | `wbs`, `instalacion` o `habilidad`. |
| `control pronosticos` | Proyecciones de costo/plazo. |
| `control desempeno` | Indicadores por trabajador. |
| `control alertas` | `--resumen` para síntesis; su evaluación puede simular cambios de estado. |
| `control calidad` | Diagnóstico, no certificación. |
| `control tablero` | Vista conjunta de indicadores. |
| `control actividades` | `--pagina 1 --por-pagina 50`. |
| `informes generar` | `--tipo mensual --inicio 2027-01-01 --formatos html --a ../trabajos/informes` |
| `informes plantel-semanal` | `--inicio 2027-01-04 --a ../trabajos/plantel.xlsx` |
| `informes automaticos ver` | Configuración vigente. |
| `informes automaticos configurar` | `--archivo automaticos.json` (campos de la API instalada). |
| `informes automaticos ejecutar` | Emite períodos pendientes. |

`avance.json`: `{"codigo":"T1","fecha":"2027-01-05","porcentaje":50,"hh_reales":8}`. Puede usar `actividad` (ID) en lugar de `codigo` y añadir `inicio_real`/`fin_real`. El lote JSON es una lista de esos objetos; el CSV usa encabezados equivalentes. El corte se entiende al fin del día; el remanente empieza el día hábil siguiente. Ejemplo `automaticos.json`: `{"enabled":true,"weekly":false,"monthly":true,"annual":false,"formats":["html"],"weekday":0,"keep_last":12}`.

Informes admite tipos `semanal|mensual|anual` y formatos `pdf,xlsx,docx,html`. En simulación los informes son efímeros y se retiran con la copia, incluso si se indicó `--a`. Para conservarlos use escritura explícita; el sobre informa respaldo y las rutas de archivos. Los datos reales de avance nunca deben inventarse para completar una receta.

## Variante, plan y JSONL

Pesos de los presets (las claves omitidas no añaden objetivos):

| Objetivo | Pesos |
|---|---|
| plazo | `makespan=1, window=0.2` |
| nivelar | `level=1, window=1` |
| ventanas | `window=1` |
| costo | `cost=1, window=0.5` |
| equilibrado | `level=0.5, makespan=0.3, window=1, cost=0.2` |

Los pesos explícitos prevalecen. Una variante contiene `id`, `name`, `objective`, `weights`, `options`, `horizon_start`, `horizon_end`, `regenerate`, `tags`; los campos opcionales pueden omitirse. Ejemplo de plan pequeño:

```json
{
  "project": "TST proyecto",
  "defaults": {"options": {"method": "heuristic", "time_limit_s": 1, "allow_overtime": false}},
  "variants": [
    {"id": "v0", "name": "Plazo", "objective": "plazo", "options": {"seed": 0}},
    {"id": "v1", "name": "Carga", "objective": "nivelar", "options": {"seed": 1}},
    {"id": "v2", "name": "Equilibrio", "objective": "equilibrado", "options": {"seed": 2}}
  ],
  "rank_by": "lexicographic",
  "parallel": 2,
  "variant_timeout_s": 45,
  "budget_s": 120
}
```

Sustituya `project` por el ID/nombre real. Barrido cartesiano: `"grid":{"weights.level":[0,0.5,1],"options.seed":[0,1,2],"options.method":["heuristic","cpsat"]}` genera 18 combinaciones. Muestreo: `"random":{"n":200,"seed":7,"space":{"weights.level":[0,1],"options.time_limit_s":[10,60]}}`. `variants`, `grid` y `random` pueden aportar variantes al plan; evite IDs duplicados. Los IDs autogenerados dependen de los parámetros y permiten reanudación reproducible.

Cada línea JSONL es un `ResultadoVariante`, no un sobre CLI. Tiene `variant`, `status` (`ok|infactible|error|timeout|cancelado`), `feasible`, `infeasibility` (`demostrada|no_encontrada|null`), `metrics`, `before`, `after`, `verification`, `warnings`, `elapsed_s`, `scenario_hash`, `seed`, `run_payload_ref`, `error`. Métricas normalizadas: `finish`, `makespan_days`, `peak_load`, `load_std`, `overload_hours`, `overtime_hours`, `overtime_cost`, `days_out_of_window`, `activities_out_of_window`, `personnel_cost` (puede ser nulo).

El resultado aplicable conserva `run` con metadatos y payload, o referencia un archivo lateral con `run_payload_ref`. Conserve juntos JSON/JSONL y sus archivos laterales. Stdout nunca incluye `run.payload`. Comparar/lote devuelven `summary`, `top`, `results_file`, `counts`; `results` completo solo para hasta 50 variantes o `--completo`. Por defecto solo se conservan las tres mejores corridas factibles; una fila con métricas sin corrida conservada no basta para aplicar.

Reanudar usa el mismo plan y JSONL y salta IDs terminados con estado `ok`/`infactible`. Timeout/cancelación no son resultados factibles ni pruebas de imposibilidad. No cambie escenario o parámetros y reutilice el mismo archivo esperando una comparación válida.

## Límites y evidencia

El límite duro por variante es `time_limit_s * 1.5 + 30`, configurable por plan. El presupuesto global incluye supervisión; el tiempo solicitado al motor no equivale a tiempo total del comando. Distinga un proceso Python nuevo de cachés frías del sistema operativo: la prueba de arranque mide lo primero, no vacía cachés.

Los roles indivisibles requieren OR-Tools y restricciones del motor: calendarios continuos equivalentes, relaciones FS, dedicación completa, sin avance ni horas extra. Una capacidad por habilidad puede contar a una persona polivalente varias veces; no sume esas capacidades como si fueran personas distintas. La curva S necesita una línea base y avance coherente; el dato ausente no representa cero costo ni eficiencia perfecta. Con asignaciones explícitas la demanda procede de esas filas; editar solo `activities.work_hours` no cambia necesariamente la demanda evaluada. El prechequeo puede clasificar conservadoramente una demanda nominal imposible como `no_encontrada`; no convierta esa etiqueta en una prueba matemática sin diagnóstico adicional.

En la integración probada el 19-10-2026, el escenario obsoleto se rechaza, pero devuelve exit 5 en lugar del 3 contratado. Un lote inválido revierte sus filas de negocio, aunque avanza la secuencia interna de calendarios. No interprete ninguno como permiso para insistir o forzar escritura. Son defectos pendientes, no excepciones al contrato.

Los ejemplos de catálogo describen el contrato y no acreditan cada formato/ruta de API. El alcance ejecutado y los defectos de la versión en construcción están en [INFORME TST](../trabajos/cli_codex_20261019/agentes/TST/INFORME.md) y [CONFLICTOS TST](../trabajos/cli_codex_20261019/agentes/TST/CONFLICTOS.md). Consulte la [guía para Codex](codex_ganttier.md) para operar con decisiones verificables.

## Formatos de entrada

`formatos listar` devuelve `data.formats`: identificador `format`, `extensions`,
`description`, `templates`, `examples` y `existing_project`. Las rutas de archivos
son relativas a `data.asset_root` (raíz de la aplicación). Los lectores históricos
sin archivos distribuidos en `plantillas/formatos` y `ejemplos/formatos` muestran
listas vacías; solicitar su plantilla produce exit 1.

```bash
python -m ganttier.cli formatos listar
python -m ganttier.cli formatos plantilla msp_excel --destino ../trabajos/mi_cli/msp.xlsx
python -m ganttier.cli formatos plantilla personal --destino ../trabajos/mi_cli/personal.xlsx
python -m ganttier.cli formatos plantilla avance --destino ../trabajos/mi_cli/avance.xlsx
python -m ganttier.cli describir --comando "importar personal"
```

El directorio padre debe existir. `--destino` acepta archivo nuevo o directorio
existente: en este último conserva el nombre de la plantilla. La creación es
exclusiva y nunca sobrescribe, incluso si otro proceso crea el destino durante
la copia. Devuelve ruta, fuente y SHA-256. Es un artefacto explícito y no requiere
`--escribir`. Las plantillas predeterminadas son MSP español, P6 hoja única y
pauta A; el catálogo también enumera las otras variantes para copiarlas manualmente.

| Formato | Extensiones | Plantilla predeterminada en `plantillas/formatos/` |
|---|---|---|
| `msp_excel` | .xlsx, .csv | plantilla_msp_es.xlsx |
| `p6_excel` | .xlsx, .csv | plantilla_p6_unica.xlsx |
| `carta_gantt` | .xlsx | plantilla_carta_gantt.xlsx |
| `pauta_equipos` | .xlsx | plantilla_pauta_A.xlsx |
| `personal` | .xlsx | personal.xlsx |
| `avance` | .xlsx | avance.xlsx |

Para los cuatro formatos asistidos se conserva `importar detectar` y
`importar asistido`, con perfil y aceptación explícita de supuestos. Ejemplo de
fuente distribuida: `python -m ganttier.cli importar detectar ../ejemplos/formatos/se220_msp_es_40.xlsx`.

Personal y avance requieren **un proyecto existente** seleccionado con `-p`.
Configure primero la base y carpeta de datos como al comienzo de esta referencia.
Ejemplo reproducido por `tests/cli/test_formatos.py`: proyecto `G3`, actividad
`A1`, inicio 2027-01-01 y corte 2027-01-31. En `Personal`, una fila con
`Nombre=Nueva`, `Horas_Dia=8`; en `Avance`, una fila con `Actividad=A1`,
`Fecha=2027-01-02`, `Porcentaje=25`, `HH_Reales=4`.

```bash
python -m ganttier.cli -p G3 importar personal ../trabajos/mi_cli/personal.xlsx --modo merge
python -m ganttier.cli -p G3 --escribir importar personal ../trabajos/mi_cli/personal.xlsx --modo merge
python -m ganttier.cli -p G3 importar avance ../trabajos/mi_cli/avance.xlsx
python -m ganttier.cli -p G3 --escribir importar avance ../trabajos/mi_cli/avance.xlsx
```

Sin escritura se ejecuta la aplicación completa en una copia descartable mediante
`Context.mutation`; la base original no cambia. `data.dry_run=true` y
`data.applied=false`, aunque los conteos describen lo aplicado en la copia.
Con escritura, el mismo contexto obtiene bloqueo, respaldo verificado y conserva
la confirmación final; cualquier excepción revierte la transacción. El importador
mantiene además su respaldo propio: `db.backup` identifica el respaldo CLI y
`data.backup` el del dominio. En simulación ambas referencias son nulas.

El sobre estándar conserva `data.created`, `updated`, `ignored`, `deactivated`,
`errors`, `warnings`, `source_sha256`, `elapsed_s`, `project_id`, `dry_run`,
`applied` y `backup`. Los errores del libro producen **exit 2**, `ok=true`,
`error=null`, `applied=false`; cada entrada de `data.errors` contiene `sheet`,
`row`, `column` y `message`. No se aplica ninguna fila si alguna falla. Una fila
con `Porcentaje=150` o `Horas_Dia=-1` demuestra ese rechazo. Proyecto ausente,
archivo inexistente o modo inválido producen exit 1.

`--modo merge` conserva personas omitidas; `replace` las desactiva sin borrar
historia. Avance rechaza retrocesos de porcentaje y fechas posteriores al corte;
repetir un registro idéntico se ignora. No se habilita una excepción a esas reglas
por CLI. Columnas, hojas opcionales, idempotencia y supuestos se describen en
[personal y avance](decisiones/formato_personal_avance.md),
[MSP/P6](decisiones/formato_msp_p6.md), [carta Gantt](decisiones/formato_carta_gantt.md)
y [pauta de equipos](decisiones/formato_pauta_equipos.md).
