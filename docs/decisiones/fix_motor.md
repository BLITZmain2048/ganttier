# Demanda sin capacidad y jornadas reducidas — 2026-09-26

Se leyeron `revision_engine_2.md` y `revision_engine_3.md`. Se modificaron
únicamente `engine/loads.py`, `engine/heuristics.py`, tres expresiones de
`engine/cpsat.py`, `tests/engine/test_fix_demanda.py` y este documento.

## Demanda sin recurso o habilidad

Una actividad sin asignaciones, sin `required_skill` y con `work_hours > 0`
genera demanda en **Sin habilidad asignada**, visible tanto al agrupar por
recurso como por habilidad. Se conserva la fracción pendiente; las actividades
terminadas no aportan carga pendiente. Una sola advertencia enumera los códigos:

> N actividades con HH no tienen recurso ni habilidad: no se nivelan contra capacidad (A, B)

Las demandas genéricas de habilidades sin recursos activos que las posean
también quedan visibles, una serie por habilidad, sin duplicar HH. Una sola
advertencia enumera las habilidades únicas ordenadas. Esto incluye asignaciones
explícitas por habilidad sin `resource_id` y demandas calculadas por cuadrilla.

`Loads.capacity_defined` distingue **capacidad desconocida** de **capacidad
conocida igual a cero**. Los grupos nuevos participan en carga, pico, desviación
y nivelación de carga; no generan límites duros, sobrecarga, horas extra ni costo
por falta de capacidad. Un recurso existente con disponibilidad cero conserva
su restricción. Las asignaciones explícitas a recursos inactivos conservan su
diagnóstico y restricción anteriores.

El contrato `LoadSeries.capacity: list[float]` y el consumidor gráfico exigen
valores finitos y un valor por período. Por compatibilidad se publican ceros en
las series sin capacidad definida, acompañados de la advertencia. **Esos ceros
no son techos de capacidad en el motor**. No se publican infinitos, NaN o null,
ni se cambia el dominio o el frontend. El contrato actual no permite al gráfico
distinguir por sí solo capacidad desconocida de capacidad cero; exponer esa
distinción visual requeriría ampliar el contrato fuera del alcance autorizado.

## Capacidad y distribución diaria

Para cada recurso activo y fecha:

```text
normal = min(horas_calendario_recurso, capacity_hours_day) × max_units
extra = max_overtime_hours_day × max_units, si el día es laborable y se permiten extras
límite = normal + extra
```

Una excepción de 4 horas y dos unidades entrega 8 HH normales. Con 1,5 horas
extra por unidad entrega 11 HH permitidas. Un día no laborable aporta cero
capacidad y cero horas extra. La capacidad por habilidad suma las capacidades
diarias de sus miembros. No cambia el reparto nominal de demandas genéricas
entre recursos ni se introduce asignación dinámica de trabajadores.

Las HH totales se distribuyen ahora proporcionalmente a las horas del calendario
**de la actividad**, para cada inicio candidato:

```text
HH_día = HH_pendientes × horas_día / suma(horas_días_de_la_actividad)
```

Ejemplo: 12 HH sobre jornadas de 4 y 8 horas producen 4 y 8 HH; sobre dos jornadas
de 8 horas producen 6 y 6 HH. Se mantiene la duración en días laborables. Cuando
no hay HH explícitas, la demanda sigue siendo `units × horas_día`. El trabajo
en curso distribuye sus HH pendientes sobre su duración remanente desde el corte.
Los hitos conservan su semántica de duración cero, sin jornadas de carga.

La normalización se hace antes de recortar el perfil al horizonte: no se
redistribuyen dentro de él las HH de jornadas que quedan fuera. Perfiles,
candidatos heurísticos y CP-SAT usan el mismo cálculo. El camino agrupado de
actividades de un día mantiene la equivalencia y omite también los límites y
penalidades de capacidad desconocida.

## Excepción imprescindible en cpsat.py

Sin esta adaptación, CP-SAT rechazaría la nueva demanda contra los ceros de
relleno, aunque la heurística la aceptara. Se cambiaron solo tres expresiones:

1. `hint_is_feasible` compara carga redondeada y límite únicamente en filas
   con `capacity_defined=True`.
2. La validación de escala de capacidad considera esas mismas filas.
3. `build_model` agrega términos a las restricciones diarias únicamente para
   esas filas; conserva los términos de carga del objetivo.

No se modificaron la configuración del solver, los hints, el presolve, los
trabajadores, la política de respaldo ni los arreglos de estabilidad de Claude.

## Verificación y límite de aceptación pendiente

Desde `Ganttier/backend`, sin escribir bytecode ni caché de pytest:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest tests/engine/test_fix_demanda.py -q -p no:cacheprovider
python -m pytest tests/engine -q -p no:cacheprovider
```

- Nuevas regresiones: **16 aprobadas, 2 omitidas**. Antes de implementar,
  las primeras regresiones reprodujeron nueve fallos de aceptación.
- Suite completa: **114 aprobadas, 16 omitidas, 4 fallidas**, en 4,76 s.
- Sintaxis de todos los módulos del motor y de la prueba nueva: `ENGINE_SYNTAX_OK`.
- OR-Tools no está instalado. Las pruebas omitidas necesitan el solver real;
  no se sustituyó por un solver simulado. Hay dos pruebas nuevas que construyen,
  validan y resuelven modelos reales: demanda sin capacidad factible y jornada
  reducida con exceso infactible.
- Persiste el aviso previo de cobertura local de feriados 2024–2030.

**No se declara la suite completamente aprobada.** Las cuatro pruebas fallidas
suponen capacidades superiores a las horas del calendario para una sola unidad,
en contradicción directa con el nuevo `min` solicitado:

| Prueba existente | Datos anteriores | Ajuste necesario del fixture |
|---|---|---|
| `test_optimize.py::test_assignment_units_variable_hours_and_resource_calendar` | 16 HH/día, calendario de 8 h, una unidad | 8 HH/día × 2 unidades |
| `test_optimize.py::test_thousand_independent_tasks_with_windows` | 800 HH/día, calendario de 8 h, una unidad | 8 HH/día × 100 unidades |
| `test_optimize.py::test_mixed_explicit_hours_and_units_are_added` | 16 HH/día, calendario de 8 h, una unidad | 8 HH/día × 2 unidades |
| `test_project_leveling.py::test_cpsat_budget_3000_uses_float_instead_of_global_horizon` | 2400 HH/día, calendario de 8 h, una unidad | 8 HH/día × 300 unidades |

Diagnóstico aislado: restituir únicamente la fórmula antigua en memoria hace
pasar esas cuatro pruebas. Con el motor corregido, ejecutar directamente las
cuatro funciones de prueba con el constructor `case` ajustado **solo en memoria**
a las unidades indicadas también hace pasar sus assertions. Esto confirma el
conflicto de datos; no equivale a una ejecución verde de la suite sin ajustes.
Sus archivos no se modificaron porque están fuera de las rutas autorizadas.
Queda pendiente actualizar esos fixtures y ejecutar la suite con OR-Tools real.
