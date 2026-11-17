# Emisión automática de informes

Implementación aditiva: no cambia esquema, servicios, arranque ni agregador de rutas.
Se reutiliza `services.render_reports` y la descarga existente. Sin dependencias nuevas.

## API pública

- `AutoReportConfig`: validación estricta; enabled=false, weekly/monthly/annual=true,
  formats=["pdf","xlsx"], weekday=0 y keep_last=12. Formatos admitidos: pdf/xlsx/docx/html.
  Lista no vacía, duplicados normalizados; weekday 0–6, keep_last entero positivo.
- `get_config(conn, pid) -> dict`, `set_config(conn, pid, config: dict) -> dict`.
  PUT reemplaza la configuración; los campos omitidos recuperan sus valores iniciales.
- `get_log(conn, pid) -> list[dict]`: historial cronológico, incluidos errores.
- `run_due_reports(conn, now: datetime | None = None) -> list[dict]`: todos los proyectos configurados.
- `run_project_reports(conn, pid, now=None) -> list[dict]`: sólo el proyecto indicado.
  Ambos devuelven registros con project_id, period_type, period_start, period_end,
  emitted_at, status, error y files=[{format,filename,url}]. El resultado contiene
  sólo archivos nuevos; un fallo total produce [] y queda registrado en el historial.
- `start_scheduler(db_connect: Callable, interval_s: int = 3600) -> threading.Thread`.
- `stop_scheduler() -> None`: activa el Event interno, despierta la espera y hace join.
- `scheduler(db_connect, interval_s=3600)`: context manager para integrar el lifespan.

Rutas del router, al incluirlo con el prefijo habitual `/api`:

| Método | Ruta | Respuesta |
|---|---|---|
| GET | /api/projects/{pid}/autoreports | Configuración (sin crear filas por leer) |
| PUT | /api/projects/{pid}/autoreports | Configuración persistida |
| POST | /api/projects/{pid}/autoreports/run | Emisiones nuevas del proyecto |
| GET | /api/projects/{pid}/autoreports/log | Historial con archivos y URL de descarga |

Proyecto inexistente: 404. Configuración inválida: 422. POST respeta enabled,
las frecuencias y weekday: adelanta la comprobación, no fuerza una reemisión.
`now` se inyecta en Python para pruebas; no permite alterar el reloj desde HTTP.

## Períodos y fecha de corte

Se considera únicamente el último período cerrado de cada frecuencia, sin reconstruir
un backlog histórico: semana anterior lunes–domingo, mes anterior y año anterior.
El semanal se habilita al llegar al weekday configurado de la semana actual; puede
recuperarse cualquier día posterior de esa semana si la aplicación estuvo cerrada.
Antes del weekday no se genera. Los otros períodos están disponibles desde el primer
día del mes/año siguiente. No se emiten períodos abiertos ni se calcula desde el inicio
del proyecto. Los años bisiestos y cruces de año usan aritmética de fechas civiles.

Sin now se usa la zona horaria local del equipo; con now se respeta su fecha civil,
incluida la zona si la trae. La fecha de corte del contenido sigue siendo data_date
del proyecto, según build_report_data: se automatiza la emisión sin modificar el corte
ni prometer una instantánea histórica de datos que no almacena el servicio existente.

## Persistencia, fallos y retención

`meta[autoreports:{pid}]` contiene la configuración JSON completa.
`meta[autoreports_log:{pid}]` contiene `{emitted, history, pending_delete}`:
emitted relaciona `frecuencia:inicio` con formatos ya emitidos; history contiene
los registros públicos, y pending_delete permite reintentar la retirada de archivos.
Un error de formato se guarda y no impide los demás formatos, frecuencias o proyectos.
Se invoca render_reports por formato para conservar éxitos parciales y reintentar
sólo los fallidos. Agregar un formato habilita su emisión sin duplicar los anteriores.
Un JSON de configuración inválido produce un registro de error. Si no se puede escribir
en SQLite o el historial está corrupto, el error va a logging y se conserva ese historial.

keep_last conserva esa cantidad de períodos exitosos **por frecuencia**, más hasta
keep_last registros con errores. Se aplica al ejecutar el proyecto habilitado.
La poda elimina exclusivamente los archivos registrados de los períodos retirados,
con validación de directorio y nombre de proyecto; nunca busca archivos por glob.
Los informes manuales quedan intactos. El índice emitted permanece aun después de
podar el historial, para impedir duplicados al retroceder el reloj o cambiar preferencias.
Las eliminaciones se realizan después de confirmar el historial y se reintentan ante
errores del sistema de archivos. No se regeneran automáticamente archivos borrados a mano.

BEGIN IMMEDIATE serializa lectura de marcas, renderizado y escritura por proyecto,
incluso entre conexiones/procesos, sin alterar transacciones del llamador: se exige
una conexión sin transacción activa. Esto puede bloquear otras escrituras mientras
se renderiza; se usa el busy_timeout de db.connect. Un fallo abrupto del proceso entre
la escritura del archivo y el commit puede dejar archivos huérfanos: SQLite y el sistema
de archivos no ofrecen una transacción conjunta y render_reports usa nombres aleatorios.
La idempotencia cubre ejecuciones completadas y errores capturados, no ese corte abrupto.

## Scheduler e integración por Claude

Un hilo daemon por proceso, primer ciclo inmediato y después espera interrumpible de
interval_s segundos tras cada ciclo. Abre/cierra su propia conexión dentro del hilo en
cada ciclo, incluso si falla. No inicializa el esquema: arrancar después de db.initialize.
start_scheduler repetido devuelve el hilo vivo; tras stop_scheduler puede reiniciarse.
stop_scheduler espera terminar la emisión en curso antes de cerrar, sin matar el hilo.
La aplicación debe permanecer abierta; no instala una tarea de Windows.

En `api/__init__.py`, después de crear router (dos líneas):

```python
from .autoreports import router as autoreports_router
router.include_router(autoreports_router)
```

En `main.py`, agregar el import y sustituir el `yield` del lifespan, después de
inicializar/cerrar la conexión de arranque (tres líneas; respetar la indentación local):

```python
from .autoreports import scheduler
        with scheduler(db.connect):
            yield
```

El context manager llama a start_scheduler al entrar y garantiza stop_scheduler en finally.
No iniciar el hilo al importar el módulo ni volver a agregar `/api` al router hijo.

## Verificación

Desde backend: `python -B -m pytest -q -p no:cacheprovider tests/e2e/test_autoreports.py`.
La suite crea una app FastAPI aislada con el router y la descarga existente, usa
GANTTIER_DB/GANTTIER_DATA_DIR temporales dentro del workspace y un proyecto de una actividad.
Cubre validación, persistencia, rutas, períodos cerrados, año bisiesto, weekday,
idempotencia tras reconectar, fallos parciales, aislamiento por proyecto, retención,
ejecución concurrente, generación real en los cuatro formatos y cierre/reinicio del hilo.

Resultado local: **18 passed, 19 skipped**, en 8,75 s. Se ejecutaron las pruebas del
núcleo y se generaron HTML/XLSX reales de los tres períodos. Se omitieron explícitamente
17 pruebas HTTP por falta de FastAPI, una de PDF por falta de ReportLab y una de DOCX
por falta de python-docx. No se afirma verificación HTTP ni PDF/DOCX en este entorno.
Se observó una advertencia del calendario de respaldo, que sólo cubre 2024–2030.
Se usó Python 3.10 con `PYTHONDONTWRITEBYTECODE=1` y el PYTHONPATH de sólo lectura
`C:/Users/<usuario>/Claude/Codex-Compartido/tools/.venv/Lib/site-packages` para Pydantic.
No se instalaron dependencias ni se modificaron archivos de integración.
