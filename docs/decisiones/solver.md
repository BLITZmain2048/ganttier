# E1 — Programación desde listas y verificación independiente

Fecha: 2026-09-26. Alcance: `engine/`, `maintenance/leveling.py`, `solve.py`,
`api/solve.py`, `tests/solver/` y este documento. No se editaron `services.py`,
el dominio, el esquema, los routers existentes ni las pruebas de otros módulos.
Estas decisiones reemplazan las partes incompatibles de `fix_motor.md` y las
revisiones anteriores: una habilidad requerida sin recursos **sí** impide una
solución cuando `respect_resource_limits=True`; un resultado infactible no
publica fechas aplicables, tampoco en mantenimiento.

## Entrada y flujo

`solve_project(conn, pid, weights=None, options=None, apply=True)` acepta pesos
y opciones del dominio o diccionarios. El diccionario de opciones admite además
`regenerate`, `horizon_start` y `horizon_end`. No requiere `planned_start`,
`planned_finish` ni una ejecución previa de CPM.

En proyecto construye el problema mediante `services.build_problem`, valida la
red, calcula CPM y cotas, busca, verifica, guarda mediante `_save_run` y llama a
`apply_run` únicamente si el informe independiente es factible. Conserva la
huella del escenario para que una edición concurrente impida aplicar una corrida
obsoleta. No modifica las tareas recibidas por `optimize`.

En mantenimiento genera las ocurrencias si no hay ninguna dentro del horizonte
pedido, o si se solicita regenerar. Usa el generador público y
`repo.save_occurrences`: las fechas tentativas del generador no se persisten
como programa de tareas nuevas. Una previsualización con regeneración conserva
las fechas vigentes de las ocurrencias existentes. Luego asigna trabajadores o
cuadrillas, nivela, verifica **todas** las actividades y aplica sólo las pendientes.
También admite actividades ordinarias sin tarea periódica en este modo.

La generación de ocurrencias es una edición del inventario incluso con
`apply=False`; lo que no se aplica son las fechas ni las asignaciones calculadas.
Se conserva la protección del repositorio para avance, bloqueos y líneas base.
`regenerate=False` no rellena automáticamente un horizonte parcialmente generado:
para reconstruirlo por completo se debe indicar `regenerate=True`.

Las HH explícitas de asignaciones fijas de mantenimiento se reservan antes de la
búsqueda. Las relaciones y restricciones no representadas por el nivelador se
comprueban en la verificación final. MSO/MFO/SNET/FNET sí se filtran directamente
en los candidatos del nivelador. El horizonte y la fecha de corte limitan los
candidatos aunque las ventanas sean blandas. Si la ventana blanda no permite
asignar recursos, se intenta el resto del horizonte, conservando la advertencia.

Respuesta: `status`, `feasible`, `violations`, `warnings`, `before`, `after`,
`metrics: {before, after}`, `method_used`, `elapsed_s`, `run_id`, `applied`.
Las fechas y los informes guardados son JSON serializables. Los errores de datos
(red cíclica, referencias, parámetros, horizonte) producen 422 en el router.
Una búsqueda sin solución produce una respuesta con `feasible=False`, conserva
el diagnóstico y guarda una corrida infactible que `apply_run` también rechaza.

## Método de búsqueda

Se mantiene el SGS vectorizado y la representación diaria existente. La
construcción inicial ya no depende de las fechas actuales. Para redes de hasta
500 actividades se comparan SGS serial y paralelo con:

- Fecha límite dura propagada y orden topológico como reglas de factibilidad.
- LFT: menor fecha tardía de fin.
- MTS: mayor número de sucesores transitivos únicos, mediante bitsets.
- GRPW: duración propia más duraciones de sucesores transitivos únicos.
- Menor holgura y cuatro aleatorizaciones sesgadas por GRPW, con RNG local y seed.

El SGS serial elige una actividad elegible por prioridad y su primer inicio
factible. El paralelo compara el primer inicio factible de todas las elegibles
y desempata por prioridad. Las precedencias generalizadas usan eventos, no
suponen que un predecesor SS/FF/SF tenga que terminar antes de habilitar al sucesor.
Se prueban primero las construcciones seriales para evitar que el costo del
paralelo agote el presupuesto antes de explorar otras prioridades.

Cada candidato factible se justifica hacia atrás sin ampliar su término y se
reconstruye hacia adelante según el orden resultante, hasta dos veces. Sólo se
conservan candidatos completos que pasan las restricciones internas; gana el
menor objetivo común. La mejora local existente comienza desde ese incumbente.
Para redes mayores se evita el cierre transitivo cuadrático: se usan límites,
LFT, holgura, longitud del camino restante y una prioridad aleatoria sesgada,
mediante SGS serial. El caso masivo de independientes de un día conserva su
construcción agrupada, sin repetir innecesariamente cada tarea intercambiable.

La cota operativa de nivelación se deriva de un incumbente **factible**. Si la
construcción falla no se recorta el horizonte a las fechas inválidas de esa
construcción. Se conserva la política existente de extensión voluntaria limitada.
Los límites de tiempo son presupuestos de búsqueda, no límites de tiempo real
para preparación, verificación y persistencia. La semilla es reproducible, pero
el número de intentos completados depende del tiempo de CPU disponible.

CP-SAT recibe el mejor incumbente mediante la implementación existente. `auto`
respeta tanto el umbral de actividades como la admisión por memoria y tamaño de
dominios. No se modificó `cpsat.py` y **no se reactivaron** `repair_hint` ni
`fix_variables_to_their_hinted_value`. Se conserva el fallback si falta OR-Tools.

La demanda genérica de proyecto se reparte proporcionalmente a disponibilidad
diaria cuando sus recursos tienen calendarios o capacidades diferentes. Así,
recursos con descansos complementarios pueden cubrir una misma habilidad, sin
exigir HH al recurso que descansa. Recursos homogéneos conservan el camino rápido.
Este reparto es una política conservadora; no resuelve una asignación combinatoria
óptima de personas polivalentes. El auditor independiente usa flujo máximo y no
depende de esa política de reparto.

## Garantías y límites de factibilidad

`engine.verify.verify_schedule` no importa CPM, Loads, SGS ni CP-SAT. Usa sólo
las entidades, las consultas públicas de calendario y aritmética de fechas.
Devuelve `VerificationReport(feasible, violations, stats, warnings)`; cada
violación/advertencia contiene `type`, `activities`, `date` ISO y `detail` español.

Comprueba fechas ausentes, duración, días laborables, horizonte, corte, bloqueos,
fechas reales, MSO/MFO/SNET/FNET, ventanas duras y FS/SS/FF/SF con desfase positivo
o negativo en el calendario del sucesor. SNLT/FNLT y ventanas blandas sólo generan
advertencias. Se conserva el contrato previo de Progress Override: inicio real
inmutable, trabajo remanente desde el corte, relaciones entrantes al avance real
omitidas con advertencia. Las terminadas no consumen HH futuras. Un hito obligatorio
de duración cero es un punto civil, incluso en día no laborable, como en CPM.

Capacidad normal diaria = `min(horas_calendario, capacity_hours_day) * max_units`;
se suman `max_overtime_hours_day * max_units` sólo en jornadas laborables. En
proyecto las HH se distribuyen proporcionalmente a las horas del calendario de
la actividad; en mantenimiento uniformemente por día laborable, conforme al
nivelador existente. Sin HH explícitas, el auditor usa unidades por horas del día.

La carga concreta se descuenta por recurso. La demanda genérica restante se
comprueba con un flujo máximo bipartito habilidad→recurso cada día: una persona
polivalente no proporciona su capacidad completa simultáneamente a varias
habilidades. Para mantenimiento verifica habilidad, actividad del recurso, tipo
trabajador/cuadrilla, calendario diario, miembros distintos, líder y dotación.
Las cuadrillas partidas distribuyen HH igualmente; un recurso agregado mantiene
toda la carga en su única asignación.

El adaptador `verify_with_options` elimina la capacidad extra si está prohibida.
Cuando se solicitan recursos como límite blando, convierte exclusivamente las
infracciones de capacidad en advertencias y declara esa política en `stats`.
La función independiente original siempre comprueba la capacidad dura permitida
por los recursos recibidos. Una actividad con HH pero sin recurso ni habilidad
se informa como capacidad no verificable; no se inventa una habilidad requerida.

La garantía es **no aplicar un programa que falle estas comprobaciones**, no
encontrar siempre una solución a cualquier RCPSP en un tiempo finito dado. Un
fallo heurístico no demuestra infactibilidad matemática. El reparto conservador
de habilidades de proyecto, las relaciones no modeladas en el nivelador de
mantenimiento y la carga fija genérica sin trabajador pueden impedir encontrar
una solución existente; la verificación rechaza los conflictos restantes.
Las métricas del nivelador con reservas incluyen el consumo concreto fijo en la
utilización de trabajadores; el perfil por ocurrencias sigue referido al trabajo
pendiente, igual que su referencia anterior agregada.

La palabra `optimo` queda limitada al modelo entero CP-SAT, sus dominios y su
posible malla semanal. Su aproximación lineal de nivelación/costo no es la
varianza diaria publicada. Demandas hacia arriba y capacidades hacia abajo a
0,1 HH pueden excluir soluciones reales. No se presenta `UNKNOWN` como prueba
de infactibilidad. Véanse los [estados oficiales de CP-SAT](https://developers.google.com/optimization/cp/cp_solver).

## Pruebas y mediciones

Ejecutado desde `Ganttier/backend` con Python 3.10, sin bytecode ni cachés fuera
del encargo:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest tests/solver -q -s -p no:cacheprovider --basetemp=tests/solver/_tmp_delivery
```

Resultado final: **87 aprobadas, 2 omisiones**, 46,78 s. La omisión de CP-SAT
requiere OR-Tools; la omisión del módulo HTTP
requiere FastAPI/httpx/python-multipart. No están instalados en los Python
disponibles. La orquestación sí se ejecutó contra SQLite real con BD y datos
temporales dentro de `tests/solver/`, sin sustituir los motores por mocks.

- Treinta instancias aleatorias, seed 0–29, 10–200 actividades, 1–5 habilidades,
  calendarios diferentes, redes y ventanas, sin fechas ni asignaciones iniciales:
  todas factibles según el auditor independiente. Sus ventanas se construyen a
  partir de un testigo serial que se descarta antes de invocar el motor.
- Trece instancias de 4–8 actividades y 1–2 recursos: enumeración exhaustiva de
  inicios laborables con poda exclusiva de precedencias/capacidad y plazo ya
  alcanzado. **Gap heurístico promedio 0,000 %, máximo 0,000 %**. También se
  comprueban cotas inferiores de ruta crítica y HH/capacidad por recurso.
- El test CP-SAT exige método `cpsat`, estado `optimo` y el mismo plazo óptimo por
  enumeración, sin aceptar un fallback. Queda pendiente su ejecución con OR-Tools.
- Casos manuales cubren tipos de violación, advertencias, calendarios reducidos,
  horas extra, multihabilidad, cuadrillas, avance, MFO y ventanas incompatibles.
- SQLite cubre generar/nivelar/verificar/aplicar, `apply=False`, regeneración,
  corte, restricciones obligatorias, reservas fijas y rechazo de aplicación posterior.
- `test_solve_api.py` crea las listas mediante HTTP y comprueba ambos modos,
  aplicación/previsualización y respuesta 422. Registra el router localmente en
  la app de prueba si todavía no está integrado por Claude.

Los gaps anteriores describen **ese conjunto pequeño**, no un límite de 10 %
garantizado para todas las instancias RCPSP. No se midió un gap CP-SAT en este
entorno ni se sustituyó por un solver simulado.

Se ejecutaron además las suites antiguas de motor y mantenimiento. No se declara
verde toda la batería histórica: hay expectativas incompatibles con E1 que piden
aceptar habilidades sin recursos o devolver fechas/métricas provisionales de
programas infactibles. Una prueba de ventana blanda exige sobrecarga dentro de
ella; ahora se obtiene una solución sin sobrecarga fuera de la ventana y se
advierte la desviación. Esas pruebas, fuera del alcance de escritura, no se editaron.

La repetición completa de `tests/engine` terminó con **115 aprobadas, 16
omisiones y 3 fallos** (121,26 s). Los tres fallos están en `test_fix_demanda.py`:
los dos parámetros de `test_uncovered_skills_aggregate_warning_and_preserve_hours`
y `test_explicit_skill_assignment_keeps_one_visible_uncovered_demand` esperan
capacidad no restringida para habilidades sin recursos, exactamente lo que E1
ordena rechazar. Las regresiones nuevas exigen el comportamiento contrario.

El benchmark existente de mantenimiento se repitió tras las modificaciones:
10.000 ocurrencias, 0,671 s; 100.000, 15,213 s; 100.000 con cuadrillas partidas y
costo, 14,956 s. Los tres criterios temporales pasan en esa repetición. La pasada
general previa había medido 3,897 s para 10.000 durante carga concurrente; estos
tiempos dependen del equipo y no son garantías para cualquier instancia.

## Registro pendiente para Claude

En `backend/ganttier/api/__init__.py`, añadir:

```python
from .solve import router as solve_router
router.include_router(solve_router)
```

El router define `/projects/{pid}/schedule/solve`; `main.py` ya agrega `/api`.
Después ejecutar `tests/solver/test_solve_api.py` con FastAPI y
`tests/solver/test_optimality.py` con OR-Tools real. No se editó el registro porque
E1 lo asigna expresamente a Claude.
