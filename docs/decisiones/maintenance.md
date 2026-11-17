# A3 — Mantenimiento periódico

Implementación autónoma en `backend/ganttier/maintenance/`. Solo depende de
stdlib, NumPy y `ganttier.domain`; no modifica las dataclasses del dominio.
Las regresiones de la demo usan además los servicios y calendarios reales de A1.

## API pública

```python
generate_occurrences(
    tasks: list[PeriodicTask], horizon_start: date, horizon_end: date,
    calendars: dict[int, WorkCalendar], default_calendar_id: int,
) -> list[Occurrence]

level_periodic(
    occurrences: list[Occurrence], resources: list[Resource],
    calendars: dict[int, WorkCalendar], weights: ObjectiveWeights,
    options: OptimizeOptions,
) -> OptimizeResult

crew_assignment(result: OptimizeResult) -> dict[int, list[int]]
```

`WorkCalendar` es un protocolo estructural interno con la interfaz de SPEC §5.
Si el calendario expone `horizon_start`, se alinean y recortan sus vectores;
en otro caso se consultan `hours`/`is_workday` por fecha. No se deduce el origen
de un vector por su longitud. Se probó también la implementación actual de A1.

## Fechas y recurrencias

- Horizontes y ventanas inclusivos. `interval` multiplica la unidad de todas
  las frecuencias; Q, S y A equivalen a 3, 6 y 12 meses. El ancla no cambia al
  ajustar febrero, fines de semana o feriados. Se salta directamente al período
  inicial solicitado, aunque `first_due` sea muy antiguo.
- `preferred_months` filtra la serie anclada. Para D/ND, día de semana y día de
  mes son filtros. W/NW comienza en el primer día preferido desde el ancla.
  En series mensuales, `day_of_month` se limita al fin de mes y luego se elige
  el día de semana más próximo, permaneciendo en el mes. No se emiten fechas
  anteriores a `first_due`. Ambos ajustes son preferencias de la fecha objetivo;
  el calendario laboral puede desplazar el inicio planificado.
- Se conserva **`due_date` como objetivo contractual** y se ajustan
  `planned_start`/`planned_finish`. Cambiar el objetivo eliminaría la trazabilidad
  de desviaciones y alteraría las ventanas. En igualdad de distancia se prefiere
  el inicio anterior. La duración completa debe caber en la ventana generada.
- Si no cabe, la ocurrencia se conserva con fechas planificadas `None`: el
  nivelador informa el problema o aplica la tolerancia blanda. No se pierden
  mantenimientos silenciosamente. `horizon_end` de la tarea también recorta su
  ventana.
- `occurrence_index` empieza en 1 y corresponde al período desde el ancla,
  incluso al generar un subhorizonte. Los filtros pueden dejar saltos. Así los
  códigos existentes permanecen estables entre regeneraciones.

## Nivelación y asignaciones

- Claves de `starts`, `finishes` y `worker_assignment`: `activity_id` cuando no
  es `None`; de lo contrario, índice **base cero de la lista recibida**. Las
  colisiones se rechazan con `ValueError`, sin sobrescribir actividades.
- La firma no incluye horizonte ni calendario predeterminado. Se infiere el
  horizonte de las ventanas y objetivos, y se usa el menor id de calendario
  para entradas con `calendar_id=None`. El generador siempre rellena ese id.
- Las ventanas incluyen **inicio y fin**, tanto en construcción como en mejora.
  Si falta capacidad, se conserva una fecha provisional dentro de la ventana
  minimizando el incremento de sobrecarga `HH + HH²`, después la distancia al
  objetivo y finalmente la fecha. Solo se amplía cuando el calendario no ofrece
  un intervalo completo en ventana; se conserva la menor distancia a ella en
  todas las mejoras. La prioridad de ventana es estructural, no depende de que
  una penalidad monetaria o de nivelación sea menor que `1000 * weights.window`.
  Ventana dura infringida implica `status="infactible"`.
- HH totales se reparten por igual en `duration_days` días laborables del
  calendario de la ocurrencia. `crew_size` determina la dotación, pero nunca
  multiplica las HH ya presupuestadas. Duración cero exige HH cero y produce un hito.
- Capacidad normal por recurso: `min(hours_del_calendario, capacity_hours_day) * max_units` en días
  laborables de su calendario. Extra: `max_overtime_hours_day * max_units`,
  solo si `allow_overtime` y el día es laborable. Las excepciones de jornada
  reducida también limitan la capacidad normal.
- Sin asignación concreta se agrupa capacidad por habilidad; un recurso fijo
  usa su propio perfil. Con `assign_workers=True` se escoge un recurso activo
  de tipo `trabajador` capaz de cubrir `crew_size`. Si ninguno cumple ese tamaño,
  se forman cuadrillas de `ceil(crew_size)` trabajadores distintos con al menos
  una unidad, la habilidad requerida y disponibilidad **en todos los días**.
  Cada miembro recibe `work_hours / len(cuadrilla)` HH totales, repartidas por día.
  Se evalúa el costo extra exacto de todos los miembros y se balancean empates
  por HH acumuladas. Un recurso fijo trabajador o cuadrilla se reserva también
  con `assign_workers=False`; no se sustituye por personas de otro recurso.
- `worker_assignment` conserva el líder. `result.crew_assignment` es un atributo
  dinámico con la cuadrilla completa, líder primero y las mismas claves de fechas.
  La función pública `crew_assignment(result)` devuelve una copia del mapa y las
  listas. **`dataclasses.asdict` y `domain.to_dict` no incluyen ese atributo**:
  el integrador debe añadirlo explícitamente al JSON de corrida y persistir una
  asignación por miembro con HH iguales. Un único recurso agregado recibe todas
  las HH. Una dotación ausente o sin disponibilidad no genera miembros ficticios.
- Las reservas concretas son compartidas entre roles, evitando duplicar a un
  trabajador polivalente. Sin asignación concreta pero con límites activos se
  reservan internamente HH compartidas. Solo al desactivar también los límites
  la capacidad por habilidad es una estimación y se advierte sobre polivalencia.
  La búsqueda factible respeta capacidad diaria y horas extra; si no encuentra
  solución, las reservas provisionales pueden excederla y lo informan mediante
  una advertencia agregada y `status="infactible"`. No desaparecen fechas ni HH.
  Si ni siquiera existe un intervalo laborable en el calendario disponible,
  todas las HH se muestran en una fecha nominal provisional, sin asignar personas;
  ese intervalo no representa una duración laboral válida.
- Orden greedy: menor ventana, mayor HH, mayor prioridad, orden de entrada.
  Se evalúan candidatos con NumPy y se hacen hasta dos pasadas de mejora local.
  Tareas de un día usan vectores 1D y costos estáticos reutilizados. El cálculo
  semanal contempla correctamente tareas que cruzan semanas. Para cuadrillas
  de un día, una cuadrilla sin costo extra en la fecha de menor puntuación base
  es un óptimo de esa selección: se evita construir la matriz completa de
  personas por fechas. Si no existe, se evalúa la matriz y los costos de todos
  los candidatos. Las reservas de un día usan actualizaciones escalares.
- `weights.normalized()` pondera varianza por rol, desplazamiento absoluto más
  1000 por día fuera de ventana, fecha temprana y costo monetario extra. La
  sobrecarga no cubierta añade `10000 * (HH + HH²)` independientemente de los
  pesos. Agrupar desconexiones equivale a evitar un costo de 1 por nueva pareja
  `(location, fecha)`; no es una restricción ni implica autorización del CEN.
  Los objetivos mantienen sus unidades; costos monetarios grandes dominan si
  se les da mucho peso.
- El algoritmo es determinista; `seed` no introduce aleatoriedad. Siempre usa
  heurística; pedir CP-SAT produce una advertencia. `group_by` no cambia estos
  perfiles: la demanda fija va por recurso y la genérica por habilidad.
- `time_limit_s` limita la mejora local, no interrumpe la construcción inicial:
  devolver un prefijo por agotar tiempo perdería actividades. Se advierte al
  alcanzar el límite. Todas las ocurrencias conservan fechas. `heuristico` indica
  que no quedan colocaciones forzadas ni ventanas duras infringidas; `infactible`
  indica un plan provisional con conflictos agregados. No demuestra que no
  exista otra solución factible. El contrato anterior de omitir fechas se sustituye
  por solicitud expresa del integrador.
  El presupuesto determinista de visitas de mejora es
  `max(0, int(time_limit_s * 2000) - cantidad_de_cuadrillas_divididas)`;
  se descuenta la selección adicional de miembros que ya hizo la construcción.
  No depende de la velocidad del reloj y mantiene la reproducibilidad.

## Métricas e histogramas

- Referencia: inicio recibido en `planned_start`, o `due_date` si no existe,
  aunque sea no laborable; las HH
  posteriores siguen el calendario. Así se ven los excesos del plan nominal.
  Se amplía el horizonte del histograma para incluir sus fines, sin ampliar el
  horizonte disponible para programar. Si un calendario no cubre la referencia,
  conserva la demanda restante en el objetivo y advierte; no inventa días laborales.
- Perfiles semanales, etiquetas ISO del lunes, claves `skill:<habilidad>` o
  `resource:<id>`. Capacidad representa HH normales; las métricas separan extra.
  Picos se miden por día y desviación según `level_period` (día o semana).
- Antes de asignar trabajadores, el costo extra usa el promedio ponderado por
  capacidad extra de los recursos del rol. Después de asignarlos, costo, extra
  y sobrecarga se calculan desde reservas individuales. Un rol con capacidad
  agregada libre puede tener extra real si una tarea indivisible requiere más
  de la jornada de su trabajador.
- Las métricas incluyen todas las HH, incluso las provisionales y las que no
  pueden reservarse por falta de habilidad o calendario. Revisar `status` y
  `warnings` antes de interpretar 0 salidas de ventana como factibilidad.
  Las entradas no se modifican.

## Corrección de la demo (2026-09-26)

Se reprodujo `services.demo_maintenance(5000)` en SQLite `:memory:` y se llamó
directamente al mismo `level_periodic` que usa `run_optimize`, sin persistir
corridas ni editar `services.py`/`domain.py`. La versión presente al comenzar
generaba **4492** ocurrencias: el filtrado de meses preferidos reducía el número
solicitado. La tabla siguiente compara esa misma entrada antes/después.

1. **Dotación:** `_members` exigía `max_units >= crew_size` a una sola persona.
   Las 522 ocurrencias con dotación 2 se omitían. Se añadieron cuadrillas completas,
   reservas reversibles por miembro y el mapa público descrito arriba.
2. **Ventanas:** la capacidad anual oculta picos trimestrales. Las 114 tareas de
   Control con ventana 25-feb–9-mar demandan **684 HH**, frente a **458 HH**
   disponibles incluyendo extra; los otros grupos trimestrales tienen 444, 444
   y 418 HH. Por tanto no es posible mantener simultáneamente fechas dentro y
   toda la capacidad diaria. Desactivar agrupación de desconexiones dejó las
   mismas 400 salidas iniciales; los casos se concentraban en febrero, marzo,
   junio, agosto, septiembre y diciembre, no en ventanas recortadas en enero.
   La búsqueda ampliada y el costo extra monetario agravaban la distancia:
   sin mejoras sumaba 1767 días fuera; tras mejoras, 2038. Una hora extra de
   24000 ponderada por 0,2 cuesta más que varios días de penalidad 1000.

La nueva política mantiene las ventanas también en mejoras y aplica la salida
provisional de menor sobrecarga solicitada. **El 0 % fuera de ventana de la demo
no significa capacidad suficiente:** devuelve `infactible` y una advertencia
agregada. Una prueba adicional duplica trabajadores para la modalidad agregada:
obtiene 0 salidas, `heuristico` y ninguna colocación forzada.

| Métrica de la demo | Antes | Después |
|---|---:|---:|
| Fechas, con trabajadores | 3970 / 4492 | 4492 / 4492 |
| Cuadrillas de dos personas publicadas | 0 / 522 | 522 / 522 |
| Fuera de ventana, con trabajadores | 570 / 3970 (14,36 %) | 0 / 4492 |
| Fuera de ventana, sin trabajadores | 400 / 4492 (8,90 %) | 0 / 4492 |
| HH sobre jornada normal, con trabajadores | 516 (demanda parcial) | 3052 |
| HH sobre límite con extra, con trabajadores | 0 (demanda parcial) | 1992 |
| HH sobre jornada normal, sin trabajadores | 3049,67 | 4719,67 |
| HH sobre límite con extra, sin trabajadores | 0 | 1604 |

Estos excesos se conservan explícitamente; reducir salidas omitiendo tareas o
ignorando HH no sería una corrección válida. La integración/persistencia de las
asignaciones múltiples queda a cargo de Claude, fuera del ámbito de estos archivos.

Durante la verificación final, `services.py` recibió cambios externos a este
trabajo: ahora sobregenera tareas y dimensiona recursos según las HH conservadas.
La regresión también pasó sobre esa demo actualizada: **5000 / 5000 fechas y
0 fuera de ventana**, con y sin trabajadores. La sobrecarga sobre el límite con
extra es 2366 HH con trabajadores y 2010 HH sin asignación pública; se informa
en una advertencia agregada. No comparar esos valores directamente con el
«antes» de 4492, porque cambió también la entrada.

## Verificación

Desde `Ganttier/backend`, sin red ni escritura de cachés fuera de A3:

```powershell
python -B -m pytest tests/maintenance -q -s -p no:cacheprovider
```

Pruebas de las diez frecuencias, anclas antiguas, año bisiesto, preferencias,
ventanas, calendario sin origen de vector, habilidades, turnos, capacidad
compartida, horas extra y sus tarifas, tareas de varios días, hitos, conservación
de HH, agrupación de desconexiones, objetivos diarios/semanales y determinismo.
Diez escenarios aleatorios verifican independientemente reservas y HH.

`ejemplos/maintenance_benchmark.py` construye exactamente 100 000 ocurrencias
mensuales en 2024–2026: 80 subestaciones, cuatro especialidades, equipos y
desconexiones. Calendario lunes-viernes sin feriados para repetibilidad.
No persiste archivos. Permite `--count 10000` y `--no-workers`.

La suite incluye 96 pruebas funcionales y tres de rendimiento. Las nuevas
regresiones cubren dotaciones fraccionarias (`ceil`), cuadrillas de varios días,
turnos, horas extra y costo exacto, reservas compartidas, recursos agregados,
falta de habilidad/dotación, calendarios vacíos, conservación total de HH,
fechas provisionales de mínima sobrecarga y la demo real en ambos modos.

Para aislar la medición de rendimiento:

```powershell
python -B -m pytest tests/maintenance -q -p no:cacheprovider --ignore=tests/maintenance/test_performance.py
python -B -m pytest tests/maintenance/test_performance.py -q -s -p no:cacheprovider
```

No se rebajaron los umbrales: 10000 < 3 s y 100000 < 30 s. El ensayo adicional
usa 100000 cuadrillas de dos personas, pesos `{level:1, window:1, cost:0.2}` y
período semanal. Se observó variación considerable entre ejecuciones del entorno:
el benchmark estándar midió 8,230 s aislado y 42,165 s en una ejecución de la
suite completa; esa ejecución falló las tres aserciones temporales aunque
pasaron las 96 funcionales. Una ejecución aislada de las cuadrillas midió
14,764 s. Los tiempos dependen de la máquina y su carga, ventanas, duración y
candidatos; no se interpreta una medición rápida como garantía bajo toda carga.

Verificación de entrega tras el último ajuste de selección de miembros:

- Las **96 pruebas funcionales** pasaron en 35,81 s; después del ajuste final,
  volvieron a pasar las **19 regresiones** de demo/cuadrillas que ejercitan ese camino.
- Últimas mediciones aprobadas: **10000 en 0,576 s**, **100000 estándar en
  22,815 s**, **100000 cuadrillas con costo en 19,577 s**. Generación estándar
  de 100000: 1,038 s. Las dos corridas de 100000 pasaron en la misma ejecución
  de regresiones/rendimiento; se volvió a medir 10000 separadamente porque en
  esa ejecución había tardado 4,830 s y fallado su umbral de 3 s. No se afirma
  que las 99 pruebas hayan pasado juntas en una única corrida.

Limitaciones conocidas: búsqueda heurística sin optimalidad garantizada,
capacidad agregada de recursos polivalentes cuando se desactiva la asignación,
y aproximación del costo de referencia antes de conocer trabajadores concretos.
No se requieren dependencias adicionales ni cambios en otros módulos.


## Diario hábil (2026-10-05)

Se añade `DH` como frecuencia opt-in, conservando `D`. Filtra días con cero horas del calendario de la tarea y mantiene índices desde el ancla. No cambia duración ni tolerancias. Contrato, interfaz, supuestos y pruebas: [mejoras_20261005.md](mejoras_20261005.md).
