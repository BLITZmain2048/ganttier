# Correcciones A4 (motor, control y GUI) y revisión de I4

Fecha: 04-10-2026. Fuente: `docs/auditoria/A4_codigo_nuevo.md`. Las correcciones de respaldos y servidor (A4-01, 02, 03, 07, 08 y 15) están en `correcciones_A4_respaldos.md`.

| ID | Corrección | Prueba |
|---|---|---|
| A4-04 | `solve_project` borra el JSON de la simulación **antes** de `ROLLBACK/RELEASE`: mientras el SAVEPOINT está abierto ningún otro escritor puede confirmar una corrida que reutilice ese ID. | `tests/solver/test_auditoria_a4_solve.py` |
| A4-05 | `build_activity_views`: sin registro de avance, una ficha con inicio real posterior al corte pierde también su porcentaje y término (no se gana valor antes de empezar). | `tests/control/test_auditoria_a4_control.py` |
| A4-06 | El prechequeo de intensidad omite toda actividad con asignaciones explícitas (nominales o genéricas): la demanda la definen las asignaciones (A1-01). | `tests/solver/test_auditoria_a4_solve.py` |
| A4-09 | «Actividad vencida» (alertas) y «actividades_vencidas» (calidad) usan el criterio de Control: 100 % o término real informado al corte. | `tests/control/test_auditoria_a4_control.py` |
| A4-10 | Pronóstico de duración: con todo el alcance presupuestado terminado, AT llega hasta el último término real, no hasta el corte. | idem |
| A4-11 | `project_summary` calcula EVM si hay línea base activa aunque existan actividades nuevas sin fechas fuera de ella. | idem |
| A4-12 | `_apply_local` reconstruye `RowModel` y `RelationIndex` cuando el cambio afecta filtro, orden por nombre o agregados WBS (≈ 0,05 s con 100 000). `RowModel.options` guarda las opciones de construcción. | `tests/gui/test_auditoria_a4_gui.py` |
| A4-13 | Solo un reflejo local exitoso suprime la recarga canónica. | idem |
| A4-14 | `AnalysisPage._call` lleva una generación por canal (función receptora) y descarta respuestas antiguas. | idem |

## Revisión de I4 (búsqueda y ausencias)

- **R3-01 (alta):** `set_absence` con disponibilidad parcial convertía en laborables los sábados, domingos, feriados y descansos del turno (fijaba `working=True, hours=h` en todos los días del intervalo). Ahora la jornada base de cada día se calcula sin las excepciones del intervalo; en días con base 0 no se toca la excepción existente, y en días laborables la disponibilidad queda en `min(h, base)`. Editar una ausencia anterior (p. ej. 0 h → 4 h) sigue funcionando. Prueba: `tests/core/test_resource_absences.py::test_ausencia_no_agrega_horas_en_dias_no_laborables`.
- Limitación aceptada: una excepción manual no laborable (p. ej. un feriado regional cargado a mano) en un día de semana se reemplaza si el usuario registra una ausencia encima; la vista previa la muestra en «previous_exception».
- No se reprograma al registrar una ausencia: se informan las asignaciones afectadas.

## Prueba ajustada

`tests/control/test_alerts.py::test_low_spi_and_critical_quality` construía el SPI bajo con una actividad cuyo inicio real era posterior al corte y esperaba que ganara 20 %. Con A4-05 esa actividad no gana valor; la prueba usa ahora una actividad iniciada dentro del corte (SPI 0,2) y otra con fecha real futura para conservar la condición de calidad crítica.
