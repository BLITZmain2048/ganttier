# Correcciones de aceptación

## Importación

El avance de mantenimiento se valida después de generar las ocurrencias. Cada código ausente produce un error de hoja/fila y revierte toda la operación, incluidos los reemplazos y los proyectos nuevos. La vista previa devuelve los mismos errores sin persistir cambios. La API recupera las filas físicas de `Avance` mediante el lector existente, incluso si hay filas vacías; no se modifica `excel_io/` ni el contrato de dominio. Los resúmenes usan `calendarios`, `recursos`, `wbs`, `actividades`, `relaciones`, `asignaciones`, `tareas_periodicas` y `avance`.

## Corridas y mantenimiento

Cada corrida guarda `scenario_hash` (SHA-256) en su JSON. La huella se captura antes del cálculo e incluye el proyecto, fecha de corte efectiva, actividades completas, relaciones, asignaciones, recursos del proyecto y globales, tareas periódicas, calendarios, excepciones y registros de avance. La comparación es conservadora: también invalidan cambios descriptivos y calendarios compartidos no usados. No requiere migración del esquema.

La aplicación compara y escribe dentro de una transacción. Una huella distinta o ausente devuelve 422 y pide volver a simular; esto incluye las corridas antiguas y CPM. Después de aplicar, se guarda la huella resultante en `meta` (`run_applied_hash:<id>`) para permitir reaplicación idempotente, sin aceptar ediciones posteriores. El hash original se conserva.

La nivelación de mantenimiento recibe únicamente ocurrencias sin avance, inicio/fin real, HH reales ni bloqueo. También se excluyen actividades no periódicas y ocurrencias con avance registrado después del corte actual. Las actividades fijas conservan fechas y asignaciones. Se devuelve su cantidad en una advertencia y, si no quedan pendientes, un error 422 explícito.

**Límite del contrato vigente:** `level_periodic(occurrences, resources, calendars, weights, options)` no acepta carga fija/reservada. Por tanto, esa carga no se descuenta de la capacidad; se advierte expresamente en la respuesta. Los perfiles del resultado corresponden al subconjunto nivelado. No se modificó `maintenance/`.

## Control y edición

El calendario de control incluye fechas vigentes, reales, de línea base, registros de avance y corte, más diez años hacia adelante (acotados por `date.max`). La proyección puede desplazar el remanente de proyectos atrasados fuera del plan original. Es un margen finito, no una proyección ilimitada.

POST de avance completa el inicio real cuando el porcentaje es positivo: conserva el inicio previo, o usa el mínimo entre inicio planificado y fecha del registro. Al alcanzar 100 %, completa el fin con la fecha del registro. Los lotes se procesan cronológicamente, conservando el orden de respuesta y las validaciones existentes.

La respuesta columnar incorpora `window_hards` como booleanos. PUT/PATCH de duración de mantenimiento recalcula el fin inclusivo con `WorkCalendar.add_workdays`, usando el calendario de la actividad o del proyecto, incluidos descansos y excepciones.

## API y arranque

GET de asignaciones admite `activity_id`, siempre dentro del proyecto. Sin `offset` ni `limit` devuelve la lista completa, sin truncar. Si se pide paginación, devuelve `{items, total, offset, limit}`; el tamaño máximo de página es 100 000 y el total corresponde al filtro aplicado.

Los errores de informes distinguen dependencias ausentes (503, incluidos `RuntimeError` con causa/contexto `ImportError`), fallos de escritura (500 con detalle en español) y otros fallos de generación (500 con detalle). Se mantienen los manejadores de validación y base de datos.

El lanzador intenta los puertos 8765–8775. Conserva el socket reservado y lo entrega a Uvicorn para evitar una carrera entre comprobar disponibilidad y arrancar. Imprime la URL elegida y sondea `/api/health` en un hilo durante un máximo de veinte segundos antes de abrir el navegador.

## Verificación

Las regresiones HTTP están en `backend/tests/e2e/test_fix_aceptacion.py`. Usan `TestClient`, `GANTTIER_DB`, `GANTTIER_DATA_DIR` y una carpeta temporal de pytest. Cubren rollback, filas físicas de Excel, invalidación de corridas, actividades fijas, proyección, progreso, calendarios, paginación, errores de informes y arranque.

Se actualizó la expectativa del mensaje en `test_qa_regressions.py`: una corrida de mantenimiento invalidada por nuevo avance ahora responde con el mensaje común de escenario modificado. En `test_qa_api.py` se adaptaron las expectativas de avance sin inicio y nivelación con actividades fijas; el escenario de nivelación incluye ahora un recurso disponible.

Resultado del 26 de septiembre de 2026, Python 3.10 en Windows:

- Los 36 casos nuevos de aceptación aprobaron, con motores reales salvo la inyección de errores de informes y la simulación del arranque del servidor.
- Comprobación ampliada (`tests/core/`, aceptación y cuatro suites QA): **168 aprobadas, 13 omitidas y 2 fallidas**, en 73,40 s. Las omisiones corresponden a pruebas opcionales del entorno.
- `tests/core/test_api.py::test_import_export_and_templates_with_stubs` aún espera `summary.activities`; el requisito nuevo exige `summary.actividades`.
- `tests/core/test_services.py::test_schedule_persistence_apply_and_project_isolation` aún espera el texto `eliminadas`; el requisito nuevo exige el mensaje común de corrida obsoleta.
- No se editaron esas dos pruebas porque `tests/core/` está fuera de los archivos autorizados.
- Se verificó la sintaxis de los diez archivos Python modificados. Las dependencias HTTP faltantes se recuperaron de la caché local de pip para las pruebas, sin red ni instalación global.

Archivos de implementación modificados: `services.py`, `repo.py`, `main.py`, `api/files.py`, `api/control.py`, `api/dependencies.py` y `api/resources.py`. No se modificaron los motores ni las funciones de demostración.

Las dependencias temporales siguen en `backend/tests/e2e/.deps`: la revisión automática bloqueó su eliminación con el motivo `blocked by policy`. Para repetir los casos nuevos desde la carpeta compartida, sin instalación global:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$PWD/Ganttier/backend/tests/e2e/.deps;$PWD/Ganttier/backend"
python -B -m pytest -q -p no:cacheprovider Ganttier/backend/tests/e2e/test_fix_aceptacion.py --basetemp=Ganttier/backend/tests/e2e/.tmp/fix-repeticion
```
