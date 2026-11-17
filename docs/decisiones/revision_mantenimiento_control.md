# Revisión adversarial de mantenimiento y control

Fecha: 2026-09-26. Alcance: SPEC §§5, 7, 8 y 12, `domain.py`, módulos
`maintenance/` y `control/`. No se modificaron contratos, servicios, calendarios,
base de datos ni dependencias. Este documento reemplaza las decisiones anteriores
de esos módulos **en los puntos corregidos aquí**.

## Evidencia y hallazgos priorizados

La suite original pasó: **96 pruebas**. La primera batería nueva produjo
**29 fallos y 4 aprobaciones antes de modificar la implementación**. Otros cuatro
casos fallaron durante la revisión posterior: SPI por trabajador, pronóstico ES,
fin real consistente entre indicadores y comparativo del programa recibido. Se
reprodujo además la dependencia del resultado respecto del reloj con igual seed.
Los casos nuevos incluyen variantes de un mismo defecto; no representan 34
defectos independientes. La suite ampliada contiene **138 pruebas**.

Las pruebas de la tabla están en
[`tests/control/test_adversarial.py`](../../backend/tests/control/test_adversarial.py)
y [`tests/maintenance/test_adversarial.py`](../../backend/tests/maintenance/test_adversarial.py).

| ID | Severidad | Defecto demostrado y corrección | Prueba principal |
|---|---|---|---|
| M1 | Alta | Un calendario con excepción de 4 h permitía asignar 8 h. Capacidad normal = `min(horas_calendario, capacity_hours_day) × max_units`; las horas extra requieren habilitación y día laborable. | `test_calendar_exception_limits_real_daily_hours` |
| M2 | Alta | Un recurso fijo podía carecer de la habilidad. El filtro de habilidad también se aplica a asignaciones fijas, en tareas de uno o varios días. | `test_fixed_worker_must_have_required_skill` |
| M3 | Alta | Sin asignación pública, un trabajador polivalente se contaba en varios pools simultáneamente. Las restricciones duras reservan capacidad física compartida, incluso con `assign_workers=False`. | `test_multiskill_capacity_is_shared_without_public_worker_assignment` |
| M4 | Alta | Equipos/materiales podían contar como HH de trabajadores; un trabajador podía cubrir una cuadrilla mayor que sus unidades. Se filtran tipos y unidades. | `test_equipment_is_not_labor_capacity`, `test_crew_demand_is_not_assigned_to_one_person` |
| C1 | Alta | El snapshot vigente sobrescribía el historial al corte: avance futuro aparecía como ganado y terminado. El historial fechado es autoritativo; registros futuros no aportan EV/AC ni terminaciones. | `test_future_snapshot_cannot_leak_into_ev`, `test_worker_future_progress_is_pending` |
| C2 | Alta | Una línea base parcial se mezclaba con presupuesto vigente no aprobado. `None` usa el programa; un diccionario define el alcance aprobado, incluso si está vacío. BAC/PV/EV/AC usan ese mismo alcance. | `test_partial_baseline_does_not_invent_approved_budget`, `test_empty_active_baseline_is_not_current_schedule` |
| C3 | Alta | HH = 0 se sustituía por duración, mezclando días con HH. Se conserva el cero presupuestado. | `test_zero_hours_are_not_duration_units` |
| C4 | Alta | HH de registros sin trabajador desaparecían de desempeño. Se reparten una sola vez por participaciones de asignación, fusionando pares actividad/trabajador. No se agregan de nuevo los totales de ActivityView/Assignment. | `test_untagged_hours_are_shared_once_and_not_dropped` |
| C5 | Alta | Una actividad vencida de cinco días se proyectaba completa en el siguiente día laborable. Se preserva una duración remanente mínima y se extiende la proyección si ES predice una fecha posterior. | `test_overdue_remainder_keeps_working_duration`, `test_forecast_respects_slower_earned_schedule` |
| C6 | Media | SPI del trabajador comparaba EV del presupuesto de actividad con PV de un presupuesto de asignación distinto; podía dar SPI=4 al terminar según plan. PV y EV ahora usan presupuesto y participación idénticos. | `test_worker_spi_compares_the_same_budget_as_ev` |
| C7 | Media | ES invertía una meseta por su extremo inicial. Se toma el último período completo con PV ≤ EV antes del siguiente incremento. | `test_es_plateau_uses_last_complete_period` |
| C8 | Media | SPI(t) empeoraba después de terminado el proyecto. AT se congela en el último fin real explícito cuando todas las actividades presupuestadas tienen terminación conocida. | `test_completed_project_does_not_keep_aging` |
| C9 | Media | TCPI podía ser negativo con presupuesto agotado; CPI=0 cambiaba silenciosamente la hipótesis de EAC a rendimiento futuro perfecto. TCPI requiere fondos positivos; EAC/ETC/VAC indefinidos se devuelven como `None`. | `test_exhausted_budget_has_no_negative_tcpi`, `test_zero_cpi_does_not_silently_switch_forecast_model` |
| C10 | Media | Curvas semanales/mensuales carecían de ancla en data_date. Se incluye exactamente ese punto con forecast=EV y se conserva 100% al concluir. | `test_forecast_contains_exact_cutoff_anchor` |
| C11 | Media | Adelantos compensaban atrasos, incluso produciendo atraso medio negativo. Se promedia tardanza no negativa de las actividades vencidas del período. | `test_average_delay_cannot_cancel_lateness_with_early_completion` |
| C12 | Media | El fin real registrado en ProgressEntry se ignoraba en cumplimiento; el fin explícito y el porcentaje podían dar EV diferente entre módulos. Se comparte la resolución del estado al corte. | `test_history_actual_finish_supplies_compliance`, `test_explicit_finish_is_consistent_between_evm_and_worker` |
| C13 | Media | Un hito podía ganar 50%. Se adopta medición 0/100 para duración cero, tanto en historial como en snapshot. | `test_milestone_is_zero_or_one_hundred` |
| M5 | Media | `interval` multiplicaba también D/W/M/Q/S/A, cambiando el significado de frecuencias fijas. Solo ND/NW/NM/NA aplican N. | `test_only_n_frequencies_apply_interval` |
| M6 | Media | El perfil “antes” descartaba el programa recibido y usaba due_date, exagerando mejoras. Ahora usa planned_start; due_date es respaldo solo si no hay inicio. | `test_before_metrics_measure_existing_plan` |
| M7 | Media | El corte por reloj daba asignaciones distintas con idéntica seed. La búsqueda usa un presupuesto determinista de visitas y no publica estados dependientes de la velocidad del equipo. | `test_seed_result_does_not_depend_on_clock_speed` |

Se actualizaron las expectativas originales que afirmaban expresamente los
comportamientos defectuosos. Se mantuvieron sus oráculos numéricos y se corrigió
una entrada de prueba cuyo recurso fijo no poseía la habilidad exigida. La prueba
masiva de trabajadores ahora excluye explícitamente sus registros de enero de
2027 al informar al 31-dic-2026.

## Fórmulas y decisiones de control

Contraste con fuentes primarias públicas de PMI que remiten al *Practice Standard
for Earned Value Management*: [definiciones y fórmulas EVM](https://www.pmi.org/learning/library/earned-value-management-systems-analysis-8026),
[cálculo práctico](https://www.pmi.org/learning/library/practical-calculation-evm-6774)
y [TCPI](https://www.pmi.org/learning/library/to-complete-performance-index-tcpi-6009).
No se afirma certificación ni auditoría integral de una edición licenciada del estándar.

```text
BAC = suma de presupuestos del alcance informado
PV = presupuesto distribuido en días laborables transcurridos
EV = presupuesto × fracción física ganada al corte
AC = suma de consumos incrementales fechados hasta el corte
SV = EV − PV                    CV = EV − AC
SPI = EV / PV                   CPI = EV / AC
EAC = BAC / CPI                 ETC = EAC − AC
VAC = BAC − EAC                 TCPI(BAC) = (BAC − EV) / (BAC − AC)
```

La hipótesis de EAC conserva la eficiencia observada: requiere CPI>0. Antes de
tener EV o AC se usa BAC; si no queda trabajo, EAC=AC. No se devuelve un número
finito engañoso cuando CPI=0 con costos incurridos. TCPI contra EAC es otra
variante; el campo único del contrato informa exclusivamente TCPI contra BAC.
La base `count` no tiene consumo real comparable: AC=0 y CPI indefinido.

Para Earned Schedule se contrastó con
[Lipke y Henderson, formación IIPM 2005](https://www.earnedschedule.com/Docs/Earned%20Schedule%20Training%20IIPM%202005%20Lipke%20%26%20Henderson.pdf),
especialmente interpolación y predicción temporal. En intervalos diarios,
`ES = C + (EV − PV(C)) / (PV(C+1) − PV(C))`; C es el último cierre completo
anterior al siguiente incremento. `SPI(t)=ES/AT` e `IEAC(t)=PD/SPI(t)`.
PD y AT son días corridos inclusivos desde el inicio planificado; el informe es
al cierre del día. EV=0 implica ES=0; BAC=0 deja ES indefinido; AT=0 deja SPI(t)
indefinido. ES=PD al completar. El dominio no tiene campo IEAC(t): su fecha se
expone en `EVMResult.forecast_finish`, redondeando hacia arriba al cierre diario.

La curva combina reprogramación y ES: mantiene al menos
`ceil(duration_days × (1−avance))` días laborables por actividad remanente,
conserva términos vigentes posteriores y dilata su perfil temporal cuando IEAC(t)
queda después. La fecha de `SCurve.forecast_finish` corresponde al final de esa
curva; puede ser posterior a la fecha estadística EVM por duración o calendario.
La dilatación es una política de Ganttier, no una fórmula atribuida a Lipke.
EV/AC posteriores al corte siguen siendo `None`; el forecast anterior también.

Desempeño por trabajador:

- Cumplimiento: terminadas a tiempo / programadas para terminar en el período.
  Solo un fin real conocido acredita puntualidad; 100% sin fecha no la inventa.
- HH ganadas: presupuesto de actividad × participación × incremento de avance
  en el período, truncado al corte. Se conservan correcciones descendentes.
- Eficiencia HH y CPI: HH ganadas / HH reales del mismo período. SPI compara
  con PV del mismo presupuesto y participación; `planned_hours` conserva las
  HH asignadas como dato de planificación separado.
- Atraso medio: promedio de `max(0, fin_real_o_corte − fin_planificado)` entre
  las actividades vencidas del período. Conteos de terminadas/pendientes son
  estados al corte; no cuentan exclusivamente transiciones dentro del período.
- Registros con trabajador explícito se suman una vez. Los no identificados se
  reparten por participación. La parte genérica o sin destinatario conocido no
  se adjudica arbitrariamente. Sin historial fechado, el snapshot solo puede
  representar el corte solicitado, no reconstruir períodos anteriores.

## Mantenimiento: verificaciones y límites del contrato

Se verificaron sin encontrar regresiones: fin de mes sin deriva; recuperación
del 29-feb; fases e índices N conservados al recortar el horizonte; filtro de
preferred_months; day_of_month recortado al último día; preferred_weekday semanal
y mensual; ventanas limitadas por horizonte y horizon_end; fin de tareas de
varios días dentro de ventanas duras; turnos y excepciones; bonificación de
desconexiones por instalación, sin mezclar instalaciones diferentes.

La varianza semanal ya tenía una delta correcta: se acumulan HH por semana
antes de medir dispersión, incluidos trabajos de varios días. La nueva prueba
`test_weekly_multiday_variance_reduced_independently` calcula varianza con NumPy
directamente desde los perfiles, comprueba conservación de 192 HH y compara
dos ejecuciones. No se sustituyó ese objetivo por nivelación diaria.

Las restricciones físicas usan capacidad por recurso y día para todos los roles.
Sin asignación pública, reservas internas permiten repartir HH entre personas;
los resultados no inventan una asignación única para ese pool. Con asignación
concreta, el contrato solo permite **un resource_id por ocurrencia**: una demanda
de varias personas requiere una cuadrilla fija con unidades suficientes, o modo
pool. Un único trabajador no se presenta como una cuadrilla de dos personas.
El modelo diario no acredita simultaneidad intradía ni descompone cuadrillas en
integrantes nominales; eso requeriría ampliar el contrato.

El algoritmo no usa aleatoriedad: igual entrada, seed y opciones producen iguales
fechas y trabajadores. Seed se acepta por compatibilidad; no introduce variación.
El presupuesto de mejora es `min(2×N, floor(2000×time_limit_s))` visitas, después
de la construcción obligatoria. `time_limit_s` es un **objetivo blando**, no un
timeout duro. Se avisa cuando se agota ese presupuesto o se excede el tiempo.
Se prioriza reproducibilidad frente a devolver una iteración parcial dependiente
de la carga del equipo. Los tiempos medidos, no esta constante, acreditan el
criterio de rendimiento en el conjunto probado.

Limitaciones explícitas: control recibe un único calendario, no un mapa por
actividad; desempeño no recibe calendario y usa fracciones de días corridos.
La heurística no certifica óptimo global. Las fuentes de AC son los registros
fechados, no totales sin fecha. Un presupuesto aprobado ajeno a las actividades
recibidas no puede reconstruirse desde esta firma. No se editaron consumidores
fuera del alcance; `services._baseline` sí distingue `None` de línea base vacía.

## Validación ejecutada

Entorno: Python 3.10.6, NumPy 2.2.6, pytest 9.1.1, Windows 10.0.26200.
No se instalaron dependencias. Se deshabilitaron bytecode y caché de pytest para
evitar escrituras fuera de las rutas autorizadas.

Ejecución detallada: **138 passed in 38.35s**. Mediciones internas, excluyendo
la construcción de entradas:

| Operación | Cantidad | Tiempo | Criterio |
|---|---:|---:|---:|
| Generación | 100.000 | 0,204 s | <20 s SPEC §12 |
| Nivelación, con trabajadores | 100.000 | 26,813 s | <30 s |
| Curva S con historial | 100.000 | 3,697 s | <5 s, suite existente |
| Desempeño con historial | 100.000 | 2,260 s | <5 s, suite existente |

El caso masivo exige todas las ocurrencias programadas y asignadas, ventanas
respetadas y dispersión reducida. Los tiempos dependen de la carga del equipo;
no constituyen una garantía para cualquier cantidad de recursos u horizonte.

Comando final solicitado, desde `Ganttier/backend`, con
`PYTHONDONTWRITEBYTECODE=1` y `PYTEST_ADDOPTS=-p no:cacheprovider`:

```text
python -m pytest tests/maintenance tests/control -q
```

Resultado final del comando exacto: **138 passed in 34.37s**, código de salida 0.
