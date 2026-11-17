# A1 — Auditoría de defectos del motor de programación

Fecha: 2026-10-03. Se revisaron `engine/{cpm,cpsat,heuristics,loads,multistart,optimize,search_bounds,verify}.py`, `solve.py`, `maintenance/*.py`, `calendars.py` y `domain.py`. Se consultaron los contratos y pruebas existentes para distinguir errores de decisiones explícitas. No se modificó código productivo ni pruebas existentes. Los cuatro defectos previamente identificados por el encargo quedan excluidos.

**Resultado: 10 defectos confirmados, 4 de severidad alta y 6 de severidad media.** No se atribuye severidad crítica a los casos probados.

## Reproducción y alcance de la evidencia

Pruebas: [`test_a1_motor.py`](../../_encargos/auditoria_tests/test_a1_motor.py). Todos los nombres de prueba citados abajo pertenecen a ese archivo. Desde `Ganttier/backend`:

```powershell
../.venv/Scripts/python.exe -B -m pytest ../_encargos/auditoria_tests/test_a1_motor.py -p no:cacheprovider -q --tb=short
```

Ejecución final: **11 fallos y 25 pruebas aprobadas, 1,27 s**, Python 3.10.6, OR-Tools 9.15.6755. Los fallos son intencionales: las aserciones expresan el comportamiento correcto y reproducen los defectos actuales. A1-01 tiene dos reproducciones; los otros nueve defectos tienen una cada uno. No se marcan como `xfail` para que una corrección haga pasar la prueba correspondiente.

Las pruebas usan objetos y SQLite en memoria. En la prueba pública de `solve_project` se intercepta únicamente `_save_run` para capturar el resultado sin escribir archivos JSON; no se sustituye el algoritmo ni el verificador. Las pruebas del adaptador `_maintenance` utilizan tablas SQLite vacías para sus consultas auxiliares y un `ScheduleProblem` concreto. Se desactivaron bytecode y caché de pytest.

### A1-01 La asignación de cuadrilla sustituye las HH explícitas y certifica capacidad inexistente

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/engine/verify.py:164`, `:172`; `backend/ganttier/solve.py:38`, `:42`, `:77`.
- **Escenario que lo reproduce:** mantenimiento con horizonte de un día, lunes 2026-01-05; actividad de un día con `work_hours=8`, habilidad E; Ana dispone de 8 HH/día, sin horas extra. La asignación explícita de esa actividad a Ana tiene `work_hours=16`. Son datos admitidos por el repositorio y la validación de red. Se propone `worker_assignment={actividad: Ana}` y una cuadrilla de un miembro.
- **Resultado obtenido vs esperado:** sin el mapa de cuadrilla, el verificador detecta 16 > 8 HH. Con el mapa, devuelve `feasible=True`, sin violaciones: sustituye las asignaciones por `a.work_hours / len(crew)` y solo comprueba 8 HH. La reproducción mediante `solve_project(..., apply=False)` también devuelve `feasible=True`, `status='heuristico'` y ninguna advertencia. Se esperaba conservar la demanda explícita de 16 HH y rechazar el programa de un día, o rechazar la inconsistencia de datos antes de resolver. La búsqueda puede cambiar quién ejecuta el trabajo, pero no reducir silenciosamente su demanda.
- **Estado:** confirmado con `test_maintenance_crew_does_not_erase_explicit_assignment_demand` y `test_solve_project_does_not_reduce_assignment_hours`.
- **Corrección sugerida:** usar las asignaciones como fuente de HH cuando existen, tanto al construir las ocurrencias como al verificar la cuadrilla propuesta; recurrir a las HH de la actividad solo cuando no hay asignaciones. Como protección mínima inmediata, rechazar una discrepancia entre ambas demandas antes de sustituirlas.

### A1-02 La ruta de mantenimiento omite las precedencias durante la programación

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/solve.py:77`, `:181`; `backend/ganttier/maintenance/leveling.py:677`.
- **Escenario que lo reproduce:** mantenimiento desde 2026-01-05 hasta 2026-01-30; A1 y A2 duran un día y consumen 8 HH cada una; dos trabajadores E de 8 HH/día; relación A1 → A2 de tipo FS+0. Objetivo de plazo, sin horas extra. La programación A1 el 5 y A2 el 6 de enero pasa el verificador.
- **Resultado obtenido vs esperado:** `_maintenance` programa ambas el 5 de enero. Solo transmite restricciones individuales de fecha al nivelador; no transmite `problem.relations`. El verificador final detecta la violación FS, por lo que la orquestación descarta un caso con solución trivial. Se esperaba obtener A1 el 5 y A2 desde el 6. No se afirma que esta programación incorrecta supere el verificador.
- **Estado:** confirmado con `test_maintenance_honors_fs_relation`, que verifica primero una solución testigo factible.
- **Corrección sugerida:** incorporar las relaciones al orden de construcción y a las cotas de inicio/fin de cada candidato, reutilizando la semántica de `Model.edge_lower`/`edge_upper`; actualizar las cotas de los sucesores cuando cambia una fecha. Mantener la verificación final, pero no usarla como sustituto de aplicar las precedencias durante la búsqueda.

### A1-03 El trabajo en curso de mantenimiento conserva un fin anterior al corte

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/solve.py:62`, `:68`, `:69`, `:87`.
- **Escenario que lo reproduce:** calendario L–V sin feriados; actividad de cuatro días y 32 HH, inicio real 2026-01-05, avance 50 %, `remaining_duration=2`, fin planificado anterior 2026-01-08; corte al final del viernes 2026-01-09. Ana dispone de 8 HH/día. Horizonte hasta 2026-01-30.
- **Resultado obtenido vs esperado:** `_maintenance` excluye la actividad en curso de las pendientes, conserva el fin del 8 de enero y calcula una reserva vacía, porque el comienzo remanente es posterior a ese fin. Se esperaba conservar el inicio real y programar el remanente los días 12 y 13, terminando el 13 de enero; esa solución pasa el verificador independiente. El fin antiguo provoca rechazo por duración en la auditoría final y tampoco reserva la capacidad futura necesaria.
- **Estado:** confirmado con `test_maintenance_recalculates_remaining_finish_after_cutoff`.
- **Corrección sugerida:** recalcular el fin previsto de las actividades en curso desde `remaining_work_start(cutoff, actual_start)`, alineado al siguiente laborable y usando la duración remanente, antes de reservar cargas. Usar ese mismo fin en el resultado auditado, conservando las fechas reales.

### A1-04 Los recursos sin calendario explícito heredan otro calendario en mantenimiento

- **Severidad:** alta.
- **Archivo:línea:** `backend/ganttier/solve.py:77`; `backend/ganttier/maintenance/leveling.py:59`, `:145`, `:205`.
- **Escenario que lo reproduce:** calendario 1 = L–V; calendario 2 = todos los días. El proyecto usa el 2 como predeterminado. Actividad de mantenimiento de un día y 8 HH, con ventana dura únicamente el sábado 2026-01-10; Ana tiene habilidad E, capacidad 8 HH/día y `calendar_id=None`.
- **Resultado obtenido vs esperado:** la actividad hereda correctamente el calendario 2, pero el nivelador usa `min(calendars)`, es decir, el 1, para Ana. Devuelve `infactible` y ninguna asignación. Se esperaba programarla el sábado: el verificador, que aplica el calendario predeterminado del proyecto al recurso, confirma que esa solución es factible. El criterio del menor identificador está documentado para el nivelador aislado; el defecto está en la integración, que no resuelve la herencia antes de llamarlo.
- **Estado:** confirmado con `test_maintenance_resource_inherits_project_calendar`.
- **Corrección sugerida:** al llamar a `level_periodic` desde `_maintenance`, pasar copias de los recursos con `calendar_id=None` sustituido por `problem.default_calendar_id`. Esto evita cambiar la interfaz pública del nivelador.

### A1-05 La optimización elimina el efecto de ALAP sin advertencia

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/engine/cpm.py:332`; `backend/ganttier/engine/search_bounds.py:24`, `:28`; `backend/ganttier/engine/heuristics.py:159`.
- **Escenario que lo reproduce:** proyecto desde 2026-01-05 con fecha de fin 2026-01-30; una actividad de un día, calendario L–V, restricción ALAP y sin consumo de recursos. Se ejecuta CPM y luego optimización heurística con objetivo de plazo.
- **Resultado obtenido vs esperado:** CPM ubica la actividad el 30 de enero. La optimización reconstruye desde `m.lower` y la traslada al 5, devolviendo `heuristico` sin advertencias. Se esperaba conservar la semántica ALAP —última fecha admisible, el 30— también al optimizar. El tratamiento ALAP de CPM solo cambia sus fechas iniciales, no las cotas o la política posterior de colocación.
- **Estado:** confirmado con `test_alap_survives_optimization`.
- **Corrección sugerida:** trasladar la fecha ALAP calculada a las cotas/política de colocación del optimizador, en lugar de conservarla solo como semilla. Comprobar expresamente ALAP después de la búsqueda; si las capacidades cambian la fecha tardía admisible, recalcularla con esas capacidades y sucesores.

### A1-06 Un hito con HH positivas pierde toda su demanda y pasa la verificación

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/engine/cpm.py:71`, `:93`; `backend/ganttier/engine/loads.py:177`; `backend/ganttier/engine/verify.py:161`.
- **Escenario que lo reproduce:** actividad no iniciada con duración cero, 80 HH, habilidad E y ningún recurso disponible. Inicio y fin propuestos: 2026-01-05.
- **Resultado obtenido vs esperado:** `validate_network` devuelve una lista vacía; el verificador devuelve `feasible=True`, `resource_days=0`, sin violaciones ni advertencias. La optimización también genera resultado y métricas con carga/sobrecarga cero. Se esperaba rechazar el dato: un hito no puede absorber 80 HH instantáneamente. El generador y el nivelador de mantenimiento ya rechazan la combinación equivalente; el motor de proyectos no lo hace.
- **Estado:** confirmado con `test_verifier_rejects_positive_work_on_zero_duration`.
- **Corrección sugerida:** validar que una actividad de duración cero no tenga HH positivas, incluidas las de sus asignaciones. Añadir el mismo control independiente antes del `continue` por duración cero en `verify_schedule`. No aplicarlo indiscriminadamente a actividades terminadas cuya duración interna se convierte en cero para excluir carga histórica.

### A1-07 Las actividades sin HH explícitas se nivelan con carga cero y sin trabajador

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/maintenance/leveling.py:194`, `:477`; `backend/ganttier/engine/verify.py:175`, `:192`.
- **Escenario que lo reproduce:** mantenimiento con una actividad de un día, `work_hours=0`, `crew_size=1`, habilidad E; Ana tiene esa habilidad y 8 HH/día. Calendario L–V; comienzo 2026-01-05. No hay asignaciones previas.
- **Resultado obtenido vs esperado:** el nivelador calcula `amount=0`, no asigna trabajador y entrega fechas. El verificador interpreta la demanda sin HH mediante `units * horas_del_calendario`, es decir, 8 HH, y rechaza la salida con `worker_missing`. Se esperaba una asignación a Ana de 8 HH; la prueba demuestra que esa programación pasa el mismo verificador. Las dos etapas interpretan de forma incompatible el cero de HH.
- **Estado:** confirmado con `test_maintenance_zero_explicit_hours_uses_crew_demand`.
- **Corrección sugerida:** compartir la regla de demanda implícita entre nivelador y verificador: cuando no hay HH explícitas y la duración es positiva, reservar `crew_size * horas de cada jornada` y seleccionar trabajador/cuadrilla. Conservar el consumo cero de los hitos válidos.

### A1-08 El respaldo de feriados desplaza Semana Santa de 2029 una semana

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/calendars.py:23`, `:38`.
- **Escenario que lo reproduce:** impedir la importación opcional de `holidays` y construir un `WorkCalendar` de Chile L–V cuya cobertura incluya marzo y abril de 2029. La prueba usa horizonte 2027-01-01 a 2028-12-31 y el margen público de dos años, íntegramente dentro del respaldo 2024–2030.
- **Resultado obtenido vs esperado:** `_MOVABLE[2029]` contiene `04-06 04-07`; el viernes 2029-03-30 queda laborable y el viernes 2029-04-06 se elimina. Se esperaban `03-30 03-31`: Pascua de 2029 corresponde al 1 de abril. También se contrastó la tabla local con `holidays` 0.105 instalada: esas son las dos fechas ausentes y las dos sobrantes de 2029. Una instalación con la dependencia y otra que use el respaldo obtienen calendarios distintos.
- **Estado:** confirmado con `test_fallback_holidays_good_friday_2029`, que fuerza realmente la rama de respaldo y falla al consultar el 30 de marzo.
- **Corrección sugerida:** sustituir `04-06 04-07` por `03-30 03-31` en la entrada de 2029. Conservar una prueba del respaldo que no pueda resolverse accidentalmente mediante la dependencia opcional.

### A1-09 El estado de mantenimiento tolera hasta 0,5 HH de violación dura

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/maintenance/leveling.py:746`, `:763`.
- **Escenario que lo reproduce:** una ocurrencia de 8,25 HH y un día, ventana dura solo el 2026-01-05; Ana dispone de 8 HH, sin horas extra; `respect_resource_limits=True` y `assign_workers=True`.
- **Resultado obtenido vs esperado:** la búsqueda usa una asignación provisional con exceso de 0,25 HH. `after.overload_hours` muestra 0,25 y la advertencia reconoce la violación, pero `status` es `heuristico`, no `infactible`, porque la condición usa `excess_hours > 0.5`. Se esperaba marcar la violación dura como infactible. `solve_project` tiene una verificación adicional que puede bloquearla; el defecto confirmado es el estado contradictorio de la API pública `level_periodic`, no una aplicación demostrada de ese programa.
- **Estado:** confirmado con `test_maintenance_reports_small_capacity_violation`.
- **Corrección sugerida:** reemplazar el umbral de 0,5 HH por una tolerancia numérica coherente con el verificador, por ejemplo `1e-7`, y decidir el estado a partir de toda violación dura de capacidad detectada.

### A1-10 El reintento posterior a CP-SAT concede un presupuesto nuevo

- **Severidad:** media.
- **Archivo:línea:** `backend/ganttier/engine/optimize.py:129`, `:132`, `:134`.
- **Escenario que lo reproduce:** actividad de un día y 16 HH, trabajador de 8 HH, horizonte de un día; método `cpsat`, `time_limit_s=0.1`. CP-SAT no entrega candidato. La prueba instrumenta las fechas límite y fija el reloj local de `optimize` en 100, sin hacer esperas reales.
- **Resultado obtenido vs esperado:** la primera preparación recibe límite 100,025; el reintento recibe 105,0. Se esperaban límites no posteriores a 100,1, el presupuesto total solicitado. `perf_counter() + max(5.0, options.time_limit_s)` concede al menos cinco segundos nuevos, incluso después de consumir el presupuesto original. La prueba confirma la concesión incorrecta; no pretende medir cinco segundos reales de CPU. Este defecto es independiente del tope máximo previamente conocido.
- **Estado:** confirmado con `test_retry_does_not_get_a_new_time_budget`; se sustituye únicamente la salida de CP-SAT por ausencia de candidato y se ejecuta la preparación real.
- **Corrección sugerida:** mantener un único `requested_deadline` para todas las fases. Solo reintentar si queda tiempo y pasar ese mismo límite; si se agotó, conservar el mejor resultado disponible o informar que no se encontró uno factible.

## Controles aprobados y sospechas descartadas

Se aprobaron controles de FS/SS/FF/SF con desfases −2, 0 y +2 y una excepción no laborable; SNET/FNET/MSO/MFO; holgura negativa y advertencias SNLT/FNLT; remanente después del corte con turno 4x3; recuperación del 29 de febrero en recurrencias anuales; fin de mes sin deriva; índices de ocurrencia tras recortar horizonte y repetición de mantenimiento con la misma semilla.

Se descartaron el reparto proporcional fijo de demanda genérica del motor de proyectos y la excepción para desfases que superan el margen interno de 366 días: son limitaciones explícitas en `docs/decisiones/engine.md`, no se presentan aquí como funciones nuevas por implementar. También se descartó una sospecha de holgura con FF−1 entre calendarios: el límite por fin global del proyecto explicaba correctamente la fecha tardía obtenida. SNLT/FNLT son plazos blandos según el contrato del repositorio; su mera violación no se reporta como aceptación indebida.

Los controles no constituyen una prueba exhaustiva de ausencia de defectos. No se afirma haber demostrado determinismo general de CP-SAT ni haber realizado pruebas de carga de larga duración.

## Tabla resumen

| ID | Severidad | Defecto | Evidencia principal | Estado |
|---|---|---|---|---|
| A1-01 | Alta | Cuadrilla oculta HH explícitas | `solve_project` acepta 16 HH contra capacidad 8 al verificar solo 8 | Confirmado, 2 pruebas |
| A1-02 | Alta | Precedencias omitidas al nivelar mantenimiento | FS+0 devuelve ambas actividades el 5 de enero | Confirmado |
| A1-03 | Alta | Fin remanente sin recalcular tras el corte | Conserva 8 de enero; corresponde 13 de enero | Confirmado |
| A1-04 | Alta | Herencia incorrecta del calendario de recursos | Rechaza sábado permitido por el calendario del proyecto | Confirmado |
| A1-05 | Media | ALAP desaparece en optimización | CPM: 30 de enero; optimización: 5 de enero | Confirmado |
| A1-06 | Media | HH positivas en hitos desaparecen | 80 HH sin recursos: verificación factible, carga cero | Confirmado |
| A1-07 | Media | Demanda implícita incompatible entre etapas | Carga cero y sin trabajador frente a demanda de 8 HH | Confirmado |
| A1-08 | Media | Semana Santa 2029 mal fechada en respaldo | 30 de marzo laborable; 6 de abril feriado | Confirmado |
| A1-09 | Media | Violación dura de 0,25 HH con estado heurístico | Umbral de infactibilidad fijado en 0,5 HH | Confirmado |
| A1-10 | Media | Presupuesto nuevo en reintento | Límite 105,0 frente al solicitado 100,1 | Confirmado |
