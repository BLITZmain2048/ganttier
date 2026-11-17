# Control I1: pronósticos, calidad y agrupación EVM

- Las tres consultas son de solo lectura; no ejecutan CPM ni sincronizan avance.
- GET `/api/projects/{pid}/control/forecasts?basis=hours&etc=` devuelve una lista.
- Cada escenario contiene `name`, `formula`, `value`, `status`, `cause`,
  `finish_date` y `date_cause`. Un valor ausente usa estado «no calculable».
- ETC vacío significa dato no informado; ETC no finito o negativo se rechaza.
- Se publican cuatro EAC, cuatro TCPI respecto de cada EAC y dos duraciones.
- EAC_CPI usa BAC/CPI incluso si el EVM vigente aplica una convención especial
  al inicio o al cierre. No se modifica el resultado existente de EVM.
- PD y AT son días corridos inclusivos desde el inicio planificado de la línea
  base: PD = fin − inicio + 1; AT = max(0, corte − inicio + 1).
- El inicio es el primer día con presupuesto positivo según la base elegida,
  igual que en Earned Schedule. Sin línea base se usa el programa vigente.
- Las fechas son inicio + ceil(duración) − 1, sin ajuste a días hábiles ni
  desplazamiento al corte. Duración cero se representa en la fecha de inicio.
- Una duración válida sin origen conserva su valor y explica la fecha ausente.
- GET `/api/projects/{pid}/control/quality` devuelve `findings` y `rules`.
- `findings` contiene solo anomalías; `rules` incluye reglas sin hallazgos y
  «no aplica». Cada regla incluye severidad, explicación, acción y referencias.
- `population` es el número evaluado; `population_unit` distingue actividades
  de relaciones. `count` cuenta actividades, salvo desfases y duplicados.
- Los duplicados comparten predecesora, sucesora y tipo, aunque difiera el
  desfase; se cuentan las copias excedentes y se identifican todas las relaciones.
- Los ciclos identifican solo actividades dentro de componentes cíclicas.
- Por defecto se excluye el primer inicio fechado de «sin predecesora» y el
  último término fechado de «sin sucesora», prefiriendo hitos en empates.
- `start_ids`/`end_ids` repetibles reemplazan esos extremos;
  `exclude_terminals=false` desactiva las exclusiones.
- `long_lag_days=10`, `long_duration_days=44`, `high_float_days=44` son
  configurables y se comparan con >. Desfases/duraciones son días hábiles.
- Holgura solo usa resultados guardados de CPM (`total_float` no nulo).
- `profile` toma el modo del proyecto salvo sustitución explícita. Mantenimiento
  sin relaciones marca extremos abiertos, desfases, ciclos y duplicados «no aplica».
- Restricciones duras se reportan para revisión, sin afirmar que sean incorrectas.
- Avance se lee al corte; fechas reales se inspeccionan sin ocultar anomalías.
- GET `/api/projects/{pid}/control/evm-by?group=wbs&basis=hours` devuelve
  `groups`, `total` y `reconciliation`; cada grupo contiene su `evm` completo.
- WBS es el nodo directo, no el ancestro; location es instalación y skill usa
  exclusivamente `required_skill`. Cada actividad pertenece a un grupo.
- «Sin clasificar» siempre se incluye, aunque esté vacío.
- Los subconjuntos conservan la línea base activa (incluso vacía), progreso,
  calendario y corte usados por `services.control_result`.
- Cada grupo llama a `evm`; SPI y CPI son razones de sumas, nunca promedios.
- La conciliación suma BAC/PV/EV/AC y expone diferencias contra el total
  independiente, `matches` y tolerancia absoluta 1e-6. No es otro grupo sumable.
- Todas las consultas admiten las bases existentes: hours, cost y count.
