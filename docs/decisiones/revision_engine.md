# Revisión adversarial del motor — 2026-09-26

Revisor: Codex. Alcance: SPEC §§5, 6 y 12, `domain.py` y el motor terminado.
Se modificaron exclusivamente `backend/ganttier/engine/`, `backend/tests/engine/`
y este documento. No se cambió el contrato de dominio ni los calendarios, servicios
o ejemplos. Esta revisión **reemplaza las decisiones incompatibles de engine.md**,
especialmente ALAP, la referencia «antes» y la devolución de fechas infactibles.

## Hallazgos y correcciones

| Severidad | Hallazgo | Corrección y evidencia |
|---|---|---|
| Alta | SGS elegía una ventana blanda tardía para el predecesor y dejaba imposible la ventana dura del sucesor, aun existiendo solución. | Propagación hacia atrás de límites **duros** antes de SGS/CP-SAT, sin convertir holguras o plazos blandos en límites duros. `test_hard_successor_window_propagates_to_predecessor`. |
| Alta | `infactible` devolvía fechas que violaban recursos/ventanas y podían ser aplicadas por un consumidor. | Resultados infactibles devuelven `starts={}`, `finishes={}`, `after=None`, `profile_after=None`; conservan `before` y advertencias. `test_infeasible_result_cannot_be_applied_as_schedule`. |
| Alta | ALAP consumía holgura total y empujaba sucesores; un ALAP terminal podía cambiar su fin sin actualizar `project_finish`. | Desplazamiento limitado por fecha tardía y fechas tempranas de sucesores; actualización del fin del proyecto. Tres pruebas ALAP nuevas y corrección del test anterior que exigía el comportamiento erróneo. |
| Alta | El remanente se calculaba después de límites basados en la duración original: una actividad avanzada larga fallaba aunque quedaran solo dos días. | Determinar duración remanente/cero terminado **antes** de construir límites. Dos casos `test_actual_duration_does_not_expand_remaining_horizon`. |
| Alta | Avance fuera de secuencia se continuaba desde data date, pero validación/pasada atrás lo trataban como una violación de lógica retenida. | Unificar Progress Override eliminando solo relaciones entrantes a actividades con fechas reales del grafo operativo; advertencia explícita y conservación de fechas reales. `test_progress_override_is_consistent_with_optimizer`. |
| Alta | `locked` podía aceptar inicio no laborable y cambiar silenciosamente un `planned_finish` existente. | Auditoría de fechas laborables, inmutabilidad y coherencia del fin bloqueado con duración/calendario. Se rechaza la incompatibilidad. Pruebas `test_locked_*`. |
| Alta | `activities_out_of_window` era `numpy.int64`: `json.dumps(domain.to_dict(result))` fallaba. | Conversión a `int` nativo en la salida, sin tocar `domain.py`. `test_metrics_are_json_serializable_domain_values`. |
| Alta | Una repetición de 100 000 independientes tardó 69,50 s, superando aceptación. El perfil atribuyó 32,79 s a dos reconstrucciones de carga. | Acumulación en bloque por recurso/día para tareas de un día, conservando el camino general. `test_bulk_profile_matches_individual_accumulation` verifica equivalencia con calendarios distintos, horas variables, mezcla HH/unidades y semanas. Benchmark final con assertions: 13,95 s. |
| Media | Un hito de inicio de duración cero introducía un día adicional por FS. MFO sobre un hito en domingo podía producir inicio viernes y fin domingo. | FS distingue predecesor puntual de tarea con duración; un hito MFO conserva inicio=fin. Ver la convención y limitación de hitos abajo. |
| Media | Holgura libre se truncaba a holgura total, incluso negativa por un plazo. | Calcularla con fechas tempranas, límite de fin de proyecto y mínimo cero, independientemente del plazo tardío. Pruebas de plazo vencido y SS con lead. |
| Media | Pasada atrás con un lead grande podía buscar fuera del calendario un límite que no restringía el problema. | Un límite superior posterior a todo el índice se trata como no vinculante. Caso SS −400 con calendario diferente. Los desbordes que sí impiden calcular una fecha siguen siendo errores explícitos. |
| Media | `before` siempre era CPM: ignoraba las fechas vigentes y podía mostrar que minimizar plazo empeoraba un plan que en realidad había acortado. | Usar fechas planificadas disponibles; fechas reales para fijas y CPM para actividades sin plan. Misma referencia y escalas para ambos resultados. `test_before_is_current_plan_and_makespan_reduces_finish`. |
| Media | Cuadrilla implícita con habilidad y HH=0 desaparecía de los perfiles. | Aplicar `crew_size × horas de calendario` cuando no hay HH explícitas. `test_units_only_implicit_crew_has_load`. |
| Media | Pesos finitos grandes desbordaban su suma y terminaban normalizados a cero. | Dividir primero por el máximo y después por la suma escalada, dentro del motor. `test_weight_normalization_does_not_overflow`. |
| Alta, revisión estática CP-SAT | Se enviaban hints sin verificar restricciones y redondeo entero. Un SGS factible en flotante puede no serlo a 0,001 HH. | Auditoría completa de fechas, duración, horizonte y HH redondeadas antes de agregar hints. Se omiten hints incompatibles y se permite que CP-SAT busque. Dos pruebas ejecutadas sin dependencia del solver. |
| Alta, revisión estática CP-SAT | Tablas por relación recorrían todo el horizonte global, incluidas fechas imposibles del predecesor; podían desbordar el calendario y crecer como relaciones × horizonte. | Relaciones de tareas móviles con calendario común se expresan directamente en rangos laborables. Las demás tabulan únicamente el intervalo alcanzable del evento predecesor. Prueba de tabla con lead −400. |
| Media, revisión estática CP-SAT | Cotas de variables de carga dependían de una estimación con 24 h y podían subestimar demandas implícitas. El límite del solver no protegía todas las fases de construcción. | Cotas derivadas de las contribuciones máximas redondeadas reales por grupo; admisión por celdas de tablas y comprobaciones de tiempo en actividades, contribuciones, relaciones, recursos y objetivos diarios. Integración real pendiente en este entorno. |

Las pruebas adversariales se ejecutaron primero en rojo y después de corregir.
Una hipótesis inicial se **descartó**: una MFO fija no tiene por qué mostrar holgura
negativa en la propia actividad. El déficit se propaga a sus predecesores; el test
final exige −5 días en el predecesor, fecha MFO conservada y advertencia de conflicto.
Se conserva `total_float = LS − ES` en días laborables, sin inventar una holgura
diagnóstica incompatible con las fechas tardías. La sospecha inicial sobre la
inversa ordinaria de calendarios distintos tampoco se confirmó.

## Criterio P6 y diferencias deliberadas

1. **Resolución diaria e inclusión.** Una tarea de un día empieza y termina en
   la misma fecha. FS+0 desde una tarea ordinaria pasa al siguiente día laborable;
   SS/FF/SF alinean el evento al primer día laborable admisible. El lag positivo
   o negativo se aplica después, en el **calendario del sucesor**, como exige SPEC.
   La pasada atrás invierte esta misma función, incluyendo descansos y feriados.
   Se mantienen las 12 pruebas existentes de enumeración exhaustiva FS/SS/FF/SF
   con lags −2/0/+2 y dos calendarios.
   P6 permite seleccionar el calendario del lag y trabaja con horas; aquí no
   existe la conversión de días a horas según calendario del predecesor.

2. **Hitos.** `Activity` no distingue Start Milestone de Finish Milestone y no se
   modificó el contrato. `duration_days=0` representa un punto sin trabajo; por
   defecto se trata como hito de inicio y su FS+0 no consume otro día. Un hito
   sucesor por FF coincide con la fecha de fin requerida; por FS desde una tarea
   ordinaria representa el inicio del día siguiente. **No se puede representar
   inequívocamente un hito de fin y su hora de salida FS como en P6** con los campos
   actuales. No se infiere el tipo a partir del nombre de la actividad. Un MFO/MSO
   puntual admite la fecha civil exacta, incluso no laborable; no consume recursos.

3. **ALAP.** Fecha elegida = fecha tardía limitada por la holgura libre disponible.
   No desplaza sucesores ASAP. Se procesa en orden topológico inverso, permitiendo
   que una cadena de ALAP aproveche el desplazamiento ya permitido del siguiente.
   Una actividad terminal puede utilizar la fecha tardía del proyecto. En optimize,
   ALAP es la preferencia inicial CPM; los objetivos elegidos pueden reubicarla.

4. **Avance: Progress Override.** Inicio real inmutable, remanente desde
   `problem.data_date`, luego `project.data_date`, luego inicio del proyecto.
   `remaining_duration` tiene precedencia; a falta de ella se estima con porcentaje
   y se redondea hacia arriba. No se espera a predecesores de una actividad ya
   iniciada. Se eligió este criterio para continuar el trabajo en curso sin añadir
   al contrato un segmento interrumpido/fecha de reanudación. Las relaciones
   salientes se conservan y SS usa inicio real. Las terminadas conservan ambas
   fechas reales y no cargan HH futuras. Las relaciones entrantes omitidas quedan
   advertidas. **No es Retained Logic ni una opción configurable**.

5. **Restricciones.** SNET/FNET restringen el inicio temprano. SNLT/FNLT afectan
   la pasada atrás, pueden producir holgura total negativa y penalizan el objetivo
   de ventanas; no son ventanas duras. MSO/MFO fijan fechas. Si contradicen lógica,
   CPM muestra las fechas forzadas, holgura negativa aguas arriba y advertencias;
   optimize no entrega un programa aplicable que viole precedencias activas.
   Así se evita aceptar silenciosamente el conflicto que una fecha obligatoria
   puede imponer en P6. `locked` fija el inicio y exige que el fin informado sea
   coherente con la duración: no se estira silenciosamente una tarea bloqueada.
   `horizon_end` limita la búsqueda; `project.end_date` es el plazo de la pasada atrás.

6. **Holguras y ciclos.** Total según Start Float, crítica si total ≤0; libre según
   sucesores tempranos y fin temprano del proyecto, con mínimo cero. Fechas reales
   y obligatorias fijas tienen LS=ES/LF=EF. `critical_path` contiene todas las
   actividades críticas ordenadas, incluidas ramas paralelas, no un único camino.
   Kahn sigue rechazando ciclos y referencias rotas con códigos afectados; puede
   listar también actividades bloqueadas aguas abajo y lo indica en el mensaje.

## Optimización y perfiles

- Se probaron los cuatro objetivos por separado y una mezcla dominada por plazo
  frente a otra dominada por ventana. La nivelación baja desviación; plazo adelanta
  el término respecto del plan vigente; ventana baja desviación en días; costo baja
  HH extra. Diez combinaciones de pesos y habilitación de horas extra verifican
  independientemente precedencias FS/SS/FF/SF, capacidad y ventana dura.
- Los pesos se normalizan de forma estable. Vector cero equivale a ventana=1.
  Las escalas son `max(1, término del programa before)`. El término de ventana
  mide **distancia civil de los extremos al límite**, más tardanza SNLT/FNLT;
  no cuenta exclusivamente jornadas de trabajo fuera de ventana.
- `before` usa el plan vigente cuando existe; entradas sin fechas usan CPM.
  Perfiles diario/semanal abarcan el horizonte pedido, incluyendo días sin trabajo.
  `load_std` y `peak_load` corresponden a ese período. Las HH de trabajo se reparten
  por días laborables de duración; se asume coherencia entre duración y fin del plan
  recibido. Los perfiles se recortan al horizonte, no incluyen cargas históricas.
- HH explícitas no se multiplican por cuadrilla. Sin HH, la demanda usa unidades
  por horas del calendario de la actividad. En curso, HH presupuestadas pendientes
  se estiman por porcentaje; no es una curva real ni una resta de `actual_hours`.
- Capacidad normal diaria = `capacity_hours_day × max_units` en días disponibles
  del recurso; horas extra = `max_overtime_hours_day × max_units` si se habilitan.
  Estos campos son capacidades diarias explícitas; una jornada positiva del
  calendario habilita la capacidad indicada, no la reescala automáticamente.
  `respect_resource_limits=True` impone normal+extra como techo duro. Si es False,
  la sobrecarga no cubierta se penaliza y se muestra, **no se cuenta como overtime**.
- Se mantiene la asignación genérica por habilidad proporcional a capacidad nominal.
  Las filas internas por recurso evitan duplicar personas multihabilidad. Esta
  asignación fraccionaria fija es conservadora: puede declarar que no encontró
  solución aunque una redistribución de personas la permita. No se implementó un
  asignador de trabajadores ni se afirma resolver ese problema adicional.
- `infactible` heurístico significa «no se encontró solución», no una demostración
  matemática. Nunca devuelve fechas aplicables. CPM conserva fechas diagnósticas
  y advertencias porque su resultado no representa una solución RCPSP.

## Formulación CP-SAT y límites de la revisión

Se consultó la API Python oficial actual. Se utilizan métodos snake_case:
`new_int_var`, `new_constant`, `new_fixed_size_interval_var`, `add_element`,
`add_cumulative`, `add_exactly_one`, `add_min_equality`, `add_max_equality`,
`add_abs_equality`, `add_hint`, `minimize`, `solve` y `value`.

- El intervalo de tarea usa su rango laborable. Los intervalos que comparten
  recursos duran **un día civil por jornada trabajada**, con tablas que excluyen
  descansos de cada actividad. `add_cumulative` opera sobre un eje civil común;
  no se comparan índices de calendarios incompatibles. Bloqueos de capacidad
  representan el calendario del recurso. No hay tareas opcionales: no se necesita
  `new_optional_interval_var`; elegir inicio mediante selectores no elimina tareas.
- Demanda se redondea hacia arriba a 0,001 HH y capacidad hacia abajo. Un hint
  solo se agrega si puede extenderse a una solución factible con ese redondeo;
  las variables auxiliares se deducen de sus fechas. Sin hint factible se busca
  sin hint. Al terminar se auditan también fechas y recursos en flotante.
- Costo usa `min(exceso, máximo_extra)` por tarifa y penalidad de sobrecarga no
  cubierta. Nivelación CP-SAT usa pico + desviación absoluta a la media + exceso;
  la heurística usa cuadrados. Ambas formulaciones están permitidas por SPEC,
  pero **son objetivos diferentes**: `optimo` significa óptimo del modelo entero
  CP-SAT, no del valor cuadrático mostrado en las métricas comunes.
- El número de actividades no determina por sí solo el tamaño. Se limita a
  800 000 opciones de objetivo diario, 3 000 000 contribuciones/celdas de tablas,
  1 000 000 celdas recurso/día y 150 000 intervalos de trabajo. Superar el presupuesto
  vuelve explícitamente a la heurística. Para calendario común, las precedencias
  entre tareas móviles son desigualdades en rangos y no grandes tablas por arco.
- El tiempo restante limita el solver y se comprueba durante la construcción.
  Lectura, CPM, preparación de arrays, auditoría y salida tienen costo adicional:
  no es una interrupción en tiempo real al milisegundo. No se garantiza optimalidad
  de RCPSP en 3000 tareas dentro de un límite arbitrariamente pequeño.
- **OR-Tools no está instalado en este Python 3.10.** La ejecución real del solver
  y la prueba de 3000 actividades quedan pendientes en el entorno del equipo que
  sí lo tiene. Se añadieron pruebas reales de objetivos, recursos multicalendario,
  hitos y escala; se omiten explícitamente aquí. No se sustituyó OR-Tools por un
  solver simulado ni se declara aprobada esa integración por revisar la API.

## Verificación

La suite inicial tenía 52 aprobadas y 4 omitidas. Resultado al cerrar la revisión:
**86 aprobadas y 11 omitidas** (todas las omitidas requieren OR-Tools).
La suite incluye las pruebas originales de CPM 10 000/15 000 <5 s.

Comando desde `Ganttier/backend`, evitando archivos fuera de las rutas autorizadas:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_ADDOPTS='-p no:cacheprovider'
python -m pytest tests/engine -q
```

Medición independiente con `ejemplos/engine_benchmark.py`, leído sin modificar,
calendario L–V y recurso agregado real. Se verifican tiempos, estado y ausencia
de advertencias mediante assertions. Los tiempos dependen del equipo:

| Caso | CPM | Optimización | Aceptación |
|---|---:|---:|---|
| 10 000 actividades / 15 000 relaciones | 0,2235 s | 7,5640 s | <5 s / <60 s; heurístico sin conflictos |
| 100 000 independientes, duración 1 | 4,8852 s | 13,9459 s | optimización <60 s; heurístico sin conflictos |

La repetición previa de 100 000 falló a 69,4987 s. Se conservó ese hallazgo, se
perfiló la causa y se repitió la aceptación tras acumular cargas en bloque.
La ejecución final imprimió `ACEPTACION_RENDIMIENTO_OK` y terminó con código 0.

La medición masiva demuestra ese conjunto sintético, no cualquier distribución
de duraciones, calendarios, recursos y ventanas. La agrupación rápida requiere
tareas independientes de un día; otras instancias usan SGS general.

## Fuentes primarias

- [Oracle P6: restricciones y ALAP](https://docs.oracle.com/cd/G18294_01/p6help/en/90151.htm).
- [Oracle P6: opciones, lag, progreso y cálculo de holguras](https://docs.oracle.com/cd/F88966_01/p6help/it/90519.htm).
- [Oracle P6: calendario del lag y conversión de unidades](https://docs.oracle.com/cd/F51303_01/client_help/en_US/choose_a_calendar_for_relationship_lag.htm).
- [OR-Tools: API Python cp_model actual](https://or-tools.github.io/docs/pdoc/ortools/sat/python/cp_model.html).
- [OR-Tools: formulaciones de scheduling](https://github.com/google/or-tools/blob/stable/ortools/sat/docs/scheduling.md).
