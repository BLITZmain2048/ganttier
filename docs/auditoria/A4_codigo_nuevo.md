# A4 — Auditoría del código nuevo de Ganttier

Fecha de verificación: **04-10-2026**. Repositorio examinado: `Ganttier`, dentro de la carpeta compartida. **15 defectos confirmados: 6 de severidad alta y 9 media.** No se incluyen recomendaciones de estilo ni sospechas sin reproducción.

Se añadieron únicamente este informe y cinco archivos de pruebas `test_a4_*.py` en `_encargos/auditoria_tests/`. Las bases, respaldos, archivos de corrida, preferencias Qt y registros usados por las pruebas son desechables y permanecen bajo esa misma carpeta. Esta auditoría no modificó código de producción ni pruebas existentes y no abrió la base de trabajo del usuario.

No había metadatos Git para atribuir los defectos a un commit. Durante la revisión se detectaron modificaciones externas en módulos de control; se releyeron las versiones presentes y se repitió la ejecución completa. Una comparación SHA-256 de 584 archivos `.py`, `.sql` e `.ini` de `backend` no detectó cambios entre esa revisión final y la entrega. Los resultados siguientes corresponden al código disponible al cierre de esta auditoría, no a una revisión Git inmutable.

## Ejecución y alcance

Pruebas ejecutadas desde `backend`, con `..\.venv\Scripts\python.exe`, Python 3.10 en Windows, PySide6 y pytest-qt disponibles. La última ejecución produjo **18 failed, 22 passed, 1 warning**, en **6,80 s**, código de salida 1. Las 18 pruebas que fallan exigen el comportamiento correcto y reproducen los 15 defectos; A4-03 tiene dos reproducciones y A4-12 tiene tres. No son fallos de importación o preparación de fixtures. El warning corresponde a la deprecación del transporte de TestClient.

Para repetir todas las pruebas desde `backend`:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$tests = Get-ChildItem ../_encargos/auditoria_tests/test_a4_*.py | ForEach-Object { $_.FullName }
$tempA4 = '../_encargos/auditoria_tests/.tmp_a4_repro_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
..\.venv\Scripts\python.exe -B -m pytest $tests -q --tb=short -p no:cacheprovider --basetemp=$tempA4
```

En cada hallazgo, `archivo.py::test_nombre` identifica una prueba bajo `_encargos/auditoria_tests/`; se puede pasar individualmente a pytest. Las pruebas de concurrencia usan barreras o puntos de entrega controlados, con SQLite, archivos y widgets reales, para reproducir un intercalado sin depender del azar.

Los controles aprobados cubren rollback de DDL y versión en migraciones; rechazo de esquema futuro; recuperación del WAL confirmado tras cierre anormal; rechazo de restauración con una conexión abierta en Windows conservando sus datos; recuperación de QLockFile tras terminar abruptamente el proceso; bloqueo de una segunda adquisición; token en cabecera y ausente de URL, salud y documentación; rechazo de Host externo y nombres de descarga con traversal; rollback completo de cadenas cíclicas; alcance y atomicidad de parches masivos; conservación de la transacción del llamador durante simulación; evaluaciones concurrentes de alertas; fórmulas EAC/TCPI/IEAC(t) con trabajo pendiente; denominador de cumplimiento; conciliación EVM; reparto semanal de HH sin redistribuir el total al recortar una semana; fechas chilenas en ese XLSX; y rotación normal de registros. Estos controles no equivalen a una certificación exhaustiva de esos módulos. No se confirmó un defecto adicional de formato de fechas visible al usuario.

### A4-01 Restaurar una base puede sobrescribir otra situada en la misma carpeta

**Severidad:** Alta (P1).

**Archivo:línea:** `backend/ganttier/backups.py:43`, `backups.py:213`, `backups.py:237`.

**Escenario reproducible:** Crear `a.db` y `b.db` en la misma carpeta. Respaldar A, modificarla y programar su restauración. Iniciar B, que llama a `apply_pending_restore(b.db)`.

**Obtenido vs esperado:** B termina conteniendo el proyecto «A respaldada», en lugar de «B vigente». Ambas bases comparten `respaldos/restauracion_pendiente.json`; aunque el JSON guarda `target=a.db`, la aplicación solo recupera `file` e ignora ese destino. También consume el marcador que correspondía a A. Se esperaba conservar B y mantener pendiente la restauración de A. El respaldo previo permite recuperar B manualmente, pero no impide el reemplazo incorrecto.

**Estado:** Confirmado con `test_a4_backups.py::test_restore_marker_is_scoped_to_its_database`.

**Corrección sugerida mínima:** Validar la ruta canónica de `target` antes de consumir el marcador y separar los marcadores por archivo de base. No borrar ni aplicar el marcador de otra base.

### A4-02 Un respaldo truncado a cero bytes se acepta y reemplaza la base por una vacía

**Severidad:** Alta (P1).

**Archivo:línea:** `backend/ganttier/backups.py:57`, `backups.py:207`, `backups.py:242`.

**Escenario reproducible:** Crear un respaldo normal y truncar su `.db` a cero bytes, conservando el manifiesto. Programar la restauración, aplicarla y ejecutar `db.initialize`, como hace el arranque.

**Obtenido vs esperado:** `integrity_check` devuelve `ok` para esta base SQLite vacía y `foreign_key_check` no encuentra referencias. Se acepta la copia; al inicializar se crea un esquema nuevo sin proyectos. La prueba obtiene `[]` donde existía un proyecto. Se esperaba rechazar el respaldo antes de reemplazar la base. La copia previa conserva una vía de recuperación, pero el arranque queda trabajando sobre datos vacíos.

**Estado:** Confirmado con `test_a4_backups.py::test_truncated_backup_is_rejected_before_replacing_live_database`.

**Corrección sugerida mínima:** Además de integridad SQLite, verificar tamaño, SHA-256 contra el manifiesto y presencia de un esquema Ganttier reconocido. Hacerlo al programar y justo antes de aplicar la restauración.

### A4-03 La retención elimina el respaldo seleccionado para restaurar

**Severidad:** Alta (P1).

**Archivo:línea:** `backend/ganttier/backups.py:149`, `backups.py:249`, `backups.py:255`.

**Escenario reproducible:** Con diez respaldos `pre`, seleccionar el más antiguo y reiniciar. Alternativamente, programar una copia manual y crear otras veinte antes de reiniciar.

**Obtenido vs esperado:** En el primer caso, `apply_pending_restore` verifica la copia elegida, crea el respaldo previo y su `prune()` elimina esa misma copia antes de `copy2`: se produce `FileNotFoundError`. En el segundo, la copia programada desaparece por retención mientras el marcador sigue apuntándola. Se esperaba conservar la copia seleccionada hasta aplicar o cancelar la restauración. No es necesario que haya corrupción ni concurrencia; se pierde precisamente el punto histórico solicitado.

**Estado:** Confirmado con `test_a4_backups.py::test_restore_oldest_pre_backup_survives_pre_restore_retention` y `test_pending_backup_survives_later_manual_retention`.

**Corrección sugerida mínima:** Incluir todo respaldo pendiente de restauración entre los archivos protegidos por `prune`. Copiar y verificar el origen en un temporal exclusivo antes de crear el respaldo previo y ejecutar retención.

### A4-04 La limpieza de una simulación puede borrar el resultado de otra corrida confirmada

**Severidad:** Alta (P1).

**Archivo:línea:** `backend/ganttier/solve.py:173`, `solve.py:176`; `backend/ganttier/services.py:195`.

**Escenario reproducible:** Simular mantenimiento cuando faltan ocurrencias. Después del rollback y `RELEASE` del SAVEPOINT, pero antes del `unlink` del JSON, guardar una corrida desde otra conexión. La prueba intercala esa escritura en el punto exacto de borrado.

**Obtenido vs esperado:** SQLite reutiliza el ID de la inserción revertida. La segunda conexión confirma una corrida cuyo `result_path` es `runs/1.json`; la simulación elimina después ese archivo por nombre. Queda una corrida persistida sin resultado. Se esperaba eliminar exclusivamente el archivo perteneciente a la simulación. El rollback de las filas funciona: el problema es liberar el bloqueo antes de limpiar un nombre reutilizable.

**Estado:** Confirmado con `test_a4_concurrency.py::test_simulation_cleanup_cannot_delete_a_subsequent_committed_run`.

**Corrección sugerida mínima:** Eliminar el archivo de simulación antes de liberar el SAVEPOINT que mantiene excluidos a los demás escritores, o usar nombres únicos por ejecución que no dependan exclusivamente del ID SQL revertible.

### A4-05 Un informe cerrado gana trabajo que comenzó después de su corte

**Severidad:** Alta (P1).

**Archivo:línea:** `backend/ganttier/services.py:99`, `services.py:103`; consumidor `services.py:559`.

**Escenario reproducible:** Guardar una actividad sin historial `progress`, con ficha al 100 %, 100 HH reales, inicio real 02-03-2026 y término real 06-03-2026. Con corte vigente 31-03-2026, emitir el informe de febrero.

**Obtenido vs esperado:** El informe declara corte 28-02-2026, pero obtiene `EV=100`, avance 100 % y `AC=0`. `build_activity_views` borra las fechas futuras y los consumos, pero conserva el porcentaje de la ficha; Control pierde la información que permitía reconocer que ese avance era futuro. Se esperaba `EV=0` antes del inicio real conocido.

**Estado:** Confirmado con `test_a4_control.py::test_closed_report_does_not_earn_snapshot_before_actual_start`.

**Corrección sugerida mínima:** Al excluir una ficha cuyo inicio real está después del corte, excluir también su porcentaje. Conservar la evidencia temporal necesaria hasta resolver el estado al corte; no entregar un porcentaje futuro con sus fechas eliminadas.

### A4-06 El prechequeo declara imposible un programa factible con asignación genérica

**Severidad:** Alta (P1).

**Archivo:línea:** `backend/ganttier/solve.py:133`, `solve.py:139`.

**Escenario reproducible:** Actividad de un día, presupuesto de 100 HH y habilidad eléctrica; asignación genérica explícita de 8 HH, sin `resource_id`; trabajador disponible con 8 HH/día. Ejecutar el optimizador y el verificador independientes, y luego `solve_project` sobre los mismos datos.

**Obtenido vs esperado:** El motor encuentra un programa y `verify_with_options` lo declara factible. `solve_project` lo rechaza como `infeasibility='demostrada'` porque compara las 100 HH de la ficha contra la capacidad, ignorando las 8 HH de la asignación. Solo excluye del prechequeo las asignaciones con recurso nominal. Se esperaba usar la misma demanda efectiva que el motor y su auditor.

**Estado:** Confirmado con `test_a4_concurrency.py::test_precheck_uses_generic_assignment_demand_instead_of_activity_budget`.

**Corrección sugerida mínima:** Obtener la demanda del mismo cálculo compartido que utiliza el motor, incluyendo asignaciones genéricas. Como corrección acotada, no aplicar este prechequeo basado en la ficha a actividades con asignaciones explícitas de cualquier tipo.

### A4-07 El cierre libera el bloqueo de instancia mientras el servidor todavía ejecuta trabajo

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/gui/server.py:135`, `server.py:143`.

**Escenario reproducible:** Mantener una petición HTTP en curso en un servidor embebido real y llamar a `stop` con un plazo menor que el de esa petición. La prueba usa 0,01 s para reproducir el mismo camino que ocurre con una petición que supera los 5 s predeterminados.

**Obtenido vs esperado:** `join(timeout)` regresa con `_thread.is_alive() == True`, pero `stop` cierra el socket y libera QLockFile. Otra adquisición del mismo bloqueo tiene éxito. Se esperaba conservar la exclusión mientras quedara trabajo del primer servidor. No se afirma corrupción de SQLite: se confirma que la garantía de instancia única termina antes que el servidor.

**Estado:** Confirmado con `test_a4_server.py::test_stop_keeps_instance_lock_until_server_thread_has_exited`.

**Corrección sugerida mínima:** Comprobar que el hilo terminó antes de liberar el bloqueo; si continúa, conservarlo hasta que el hilo salga y reportar que el cierre sigue pendiente. Dejar el cierre del listener coordinado con uvicorn.

### A4-08 Dos respaldos simultáneos comparten el mismo archivo temporal

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/backups.py:94`, `backups.py:97`.

**Escenario reproducible:** Lanzar dos creaciones de respaldo manual con el mismo motivo dentro del mismo segundo. La prueba fija el reloj y sincroniza ambas conexiones de destino después de elegir el nombre.

**Obtenido vs esperado:** Las dos llamadas ven inexistente el mismo nombre final y abren el mismo `.db.tmp`. En Windows, una petición falla con `PermissionError [WinError 32]`; la otra puede completar. Se esperaban dos respaldos distintos y consistentes. La prueba confirma colisión y fallo de la operación, no corrupción de la copia que terminó bien.

**Estado:** Confirmado con `test_a4_concurrency.py::test_concurrent_backup_names_are_reserved_exclusively`.

**Corrección sugerida mínima:** Reservar un temporal exclusivo y un nombre final inequívoco, por ejemplo con UUID o creación exclusiva. La comprobación `exists()` seguida de apertura no reserva el nombre.

### A4-09 Calidad y alertas declaran vencida una actividad con término real informado

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/control/schedule_quality.py:172`; `backend/ganttier/control/alerts.py:113`.

**Escenario reproducible:** Actividad planificada del 05-01-2026 al 09-01-2026, con esas mismas fechas reales y porcentaje de ficha predeterminado en 0. Evaluar al 31-03-2026. La ficha es admitida con inicio y término reales; no se requiere alterar SQL para construirla.

**Obtenido vs esperado:** EVM interpreta el término real y devuelve avance 100 %. Calidad emite `actividades_vencidas` y alertas persiste `actividad_vencida`: ambos comprueban solo el porcentaje y el término planificado. Se esperaba no denunciar como «sin terminar» una actividad cuyo término real ya es conocido al corte.

**Estado:** Confirmado con `test_a4_control.py::test_finished_activity_is_not_reported_overdue_by_quality_or_alerts`.

**Corrección sugerida mínima:** Usar el mismo criterio de terminación al corte de Control: porcentaje completo o término real válido. Mantener la comprobación de fechas reales futuras por separado.

### A4-10 El pronóstico remanente sigue aumentando después de terminar el proyecto

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/api/analysis.py:53`; consumidor `backend/ganttier/control/forecasts.py:54`.

**Escenario reproducible:** Plan de cinco días, iniciado el 05-01-2026 y terminado realmente el 06-01-2026, avance 100 %. Consultar pronósticos con corte 31-03-2026.

**Obtenido vs esperado:** `IEAC_t` devuelve correctamente 2 días, porque EVM detiene el tiempo real en la terminación. `duracion_remanente = AT+(PD−ES)` devuelve 86 días: la API calcula AT hasta el corte de marzo aunque ya no queda trabajo. Se esperaba duración 2 días y fecha 06-01-2026, o indicar que no corresponde pronosticar trabajo terminado, en vez de extenderlo hasta cada nuevo corte.

**Estado:** Confirmado con `test_a4_control.py::test_completed_duration_forecasts_use_actual_completion_time`.

**Corrección sugerida mínima:** Para el alcance presupuestado completamente terminado, calcular AT hasta el último término real conocido, igual que Earned Schedule. No cambiar la fórmula para proyectos todavía en ejecución.

### A4-11 Inicio oculta un EVM válido al agregar una actividad fuera de línea base sin fechas

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/services.py:408`; consumidor `backend/ganttier/gui/pages/home.py:219`.

**Escenario reproducible:** Guardar una línea base de una actividad programada con 100 HH. Agregar otra actividad sin fechas, fuera de esa línea base, y consultar Control e Inicio.

**Obtenido vs esperado:** Control devuelve EVM válido con `BAC=100`; el resumen devuelve `evm=None` porque una actividad, aunque no pertenezca al alcance de la línea base activa, está sin programar. Inicio convierte ese resultado en indicadores vacíos. Se esperaba conservar el mismo EVM de Control y señalar aparte que hay una actividad pendiente de programar.

**Estado:** Confirmado con `test_a4_control.py::test_summary_keeps_control_evm_when_new_out_of_baseline_task_is_unscheduled`.

**Corrección sugerida mínima:** Desacoplar la condición `scheduled` del cálculo EVM. Intentar el cálculo común y tratar su error de datos si corresponde, sin descartarlo anticipadamente por actividades fuera de línea base.

### A4-12 La actualización local no recalcula filtros, orden ni agregados WBS

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/gui/pages/activities.py:1163`, `activities.py:936`.

**Escenario reproducible:** En una edición de nombre o HH sin CPM, actualizar mediante `_apply_local`. Probar tres estados de la vista: agrupada por WBS, filtrada por nombre y ordenada por nombre.

**Obtenido vs esperado:** Dos actividades con 10 HH cada una y avances 100/0 muestran WBS 50 %. Al cambiar las HH de la primera a 90, el WBS sigue en 50 % en lugar de 90 %. Renombrar «Alfa» como «Otro nombre» mantiene la fila en el filtro «Alfa». Renombrarla como «Zeta» tampoco la mueve después de «Beta». Se actualiza el array, pero `RowModel` y sus agregados quedan antiguos; además se suprime la recarga normal.

**Estado:** Confirmado con `test_a4_gui.py::test_apply_local_recomputes_wbs_weighted_progress`, `test_apply_local_rebuilds_active_name_filter` y `test_apply_local_rebuilds_name_sort`, ejecutadas con pytest-qt.

**Corrección sugerida mínima:** Reconstruir `RowModel` y el índice de relaciones a partir de los arrays ya actualizados, conservando las opciones de vista. No es necesario descargar otra vez todas las actividades para recalcular estas estructuras locales.

### A4-13 Un resultado False de la actualización local suprime igualmente la recarga

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/gui/pages/activities.py:952`, `activities.py:936`.

**Escenario reproducible:** Entregar una confirmación de escritura cuando el array actual no contiene la actividad. `_apply_local` devuelve `False`; la prueba recorre `mutate`, su callback y las señales reales de la página visible.

**Obtenido vs esperado:** El callback agrega `False` a `applied_locally`. La lista `[False]` es verdadera al evaluar `if applied_locally`, por lo que se establece `_suppress_next` y no se solicita la recarga canónica. Se esperaba que la imposibilidad de reflejar el cambio localmente activara el refresco documentado. Este defecto es distinto del anterior: afecta el camino de recuperación cuando no se pudo actualizar ninguna fila.

**Estado:** Confirmado con `test_a4_gui.py::test_false_local_update_requests_canonical_refresh`, ejecutada con pytest-qt.

**Corrección sugerida mínima:** Guardar un booleano de éxito o agregar a la lista solo si `light()` devuelve verdadero. Suprimir el refresco únicamente cuando la actualización local haya tenido éxito.

### A4-14 Análisis acepta respuestas antiguas para la selección actual

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/gui/pages/analysis.py:280`.

**Escenario reproducible:** Pedir EVM por WBS, cambiar a Habilidad y dejar que la segunda consulta termine primero. Entregar después la respuesta lenta de WBS. La prueba controla el orden de callbacks sobre la página Qt real.

**Obtenido vs esperado:** El selector permanece en Habilidad, pero las filas vuelven a WBS. `_call` captura `self._request`, pero no lo compara ni lo incrementa; solo valida que coincida el proyecto. Se esperaba descartar la respuesta obsoleta. El mismo patrón puede afectar cálculos sucesivos con distinto ETC o base.

**Estado:** Confirmado con `test_a4_gui.py::test_analysis_discards_older_response_for_same_project`, ejecutada con pytest-qt.

**Corrección sugerida mínima:** Usar una generación de solicitud por análisis o pestaña y comprobarla al entregar el resultado. Invalidarla también al cambiar de proyecto; un único contador compartido no debe descartar respuestas válidas de otras pestañas.

### A4-15 El arranque directo falla al elegir una base en una carpeta nueva

**Severidad:** Media (P2).

**Archivo:línea:** `backend/ganttier/gui/server.py:107`, `server.py:63`.

**Escenario reproducible:** Definir `GANTTIER_DB` como `nueva_carpeta/ganttier.db`, donde aún no existe la carpeta, e iniciar el servidor embebido por la ruta directa de GUI. `iniciar_consola.bat` usa `python -m ganttier.gui`, que no pasa por `configure_runtime` del launcher.

**Obtenido vs esperado:** QLockFile no puede crear el archivo y el programa lanza `InstanceLocked` diciendo «Ganttier ya está abierto», aunque no hay otra instancia. `db.connect` habría creado la carpeta, pero el bloqueo se intenta antes. Se esperaba crear la carpeta y arrancar, o informar el error real de acceso. El launcher habitual prepara la carpeta y evita este caso; el hallazgo se limita al arranque directo o de diagnóstico.

**Estado:** Confirmado con `test_a4_server.py::test_first_start_creates_database_parent_before_locking`.

**Corrección sugerida mínima:** Crear el directorio padre de la base antes de adquirir QLockFile, dentro del arranque compartido del servidor. Distinguir errores de creación/permisos de una colisión real de bloqueo.

## Tabla resumen

| ID | Severidad | Defecto confirmado | Pruebas que lo reproducen |
|---|---|---|---:|
| A4-01 | Alta | Restauración aplicada a otra base de la misma carpeta | 1 |
| A4-02 | Alta | Respaldo vacío aceptado; base operativa reemplazada sin proyectos | 1 |
| A4-03 | Alta | Retención elimina una restauración pendiente | 2 |
| A4-04 | Alta | Simulación borra el archivo de otra corrida confirmada | 1 |
| A4-05 | Alta | Informe histórico gana avance anterior al inicio real | 1 |
| A4-06 | Alta | Falsa infactibilidad demostrada por ignorar asignaciones genéricas | 1 |
| A4-07 | Media | Bloqueo liberado antes de terminar el servidor | 1 |
| A4-08 | Media | Respaldos simultáneos colisionan en nombre y temporal | 1 |
| A4-09 | Media | Actividad terminada denunciada como vencida | 1 |
| A4-10 | Media | Duración remanente crece después de la terminación | 1 |
| A4-11 | Media | Inicio oculta el EVM que Control sí calcula | 1 |
| A4-12 | Media | Actualización local conserva filtros, orden y WBS obsoletos | 3 |
| A4-13 | Media | Fallo de actualización local impide la recarga de recuperación | 1 |
| A4-14 | Media | Respuesta antigua reemplaza el análisis de la selección nueva | 1 |
| A4-15 | Media | Carpeta de base inexistente se confunde con instancia abierta | 1 |
| **Total** | **6 altas / 9 medias** | **15 hallazgos confirmados; ninguno probable** | **18** |
