# A2 — motor de programación de proyectos

## Contrato y archivos

API exportada desde `ganttier.engine`:

```python
compute_cpm(problem: ScheduleProblem) -> CPMResult
validate_network(problem: ScheduleProblem) -> list[str]
optimize(problem: ScheduleProblem, weights: ObjectiveWeights,
         options: OptimizeOptions) -> OptimizeResult
```

Implementación: `engine/{__init__,cpm,loads,heuristics,cpsat,optimize}.py`.
Pruebas: `tests/engine/{__init__,conftest,test_cpm,test_optimize,test_cpsat}.py`.
Medición reproducible: `ejemplos/engine_benchmark.py --suite` desde `Ganttier`.
No se modifican objetos de dominio, calendarios recibidos, BD ni archivos de otros módulos.

## Fechas y CPM

- Inicio/fin inclusivos. FS+0 empieza el siguiente día laborable del sucesor;
  SS/FF/SF+0 usa el primer día laborable del sucesor desde el evento predecesor.
  El desfase, incluso negativo, se aplica después de esa alineación. Los tests
  contrastan las cuatro relaciones contra enumeración exhaustiva con dos calendarios.
- Kahn y pasadas CPM O(n+m); construir los índices añade O(c·h), inevitable al
  leer los calendarios de c tipos sobre h días. Horas, días y prefijos son NumPy.
  Se consulta exclusivamente `WorkCalendar.hours(date)`: no se presume que los
  vectores públicos estén alineados con el horizonte del problema. Margen interno
  de 366 días por lado, compatible con el margen de A1. Fechas fuera del calendario
  producen error explícito; se debe ampliar el horizonte y reconstruir calendarios.
- `horizon_end` es un límite operativo de la búsqueda. `project.end_date`, si existe,
  es el plazo de la pasada hacia atrás: permite mostrar holguras negativas.
- MSO/MFO y `locked` fijan fechas. Las reales prevalecen sobre fechas calculadas;
  las contradicciones se reportan. El remanente fraccionario se redondea hacia
  arriba, coherente con la resolución diaria. El inicio real en curso se conserva;
  el trabajo pendiente comienza desde la fecha de corte. Si falta, se usa la fecha
  de corte del proyecto y, finalmente, el inicio del proyecto.
- SNLT/FNLT son plazos blandos: generan holgura negativa y advertencias, pero no
  eliminan fechas del dominio de optimización. También contribuyen al objetivo
  de ventanas. Las ventanas duras sí restringen el dominio.
- ALAP se aplica desde las fechas tardías y se propaga hacia sus descendientes.
  `critical_path` enumera **todas** las actividades críticas en orden topológico;
  puede contener ramas paralelas, no representa necesariamente un único camino.
- Entradas inválidas/ciclos: `ValueError` en español; `validate_network` devuelve
  todos los errores encontrados. Los ciclos identifican también las actividades
  bloqueadas aguas abajo, indicándolo explícitamente.

## Trabajo, capacidad y objetivos

- `work_hours` son HH **totales**, no HH por integrante: no se multiplican por
  `crew_size`. HH explícitas se distribuyen uniformemente; asignaciones sin HH usan
  `units * horas del calendario de actividad`. Ambas modalidades se suman cuando
  existen varias asignaciones al mismo recurso. En curso solo se carga la fracción
  presupuestada pendiente; actividades terminadas no consumen capacidad futura.
- La capacidad usa el calendario del recurso, `capacity_hours_day * max_units`,
  y, si se permiten, `max_overtime_hours_day * max_units`. `active=False` no aporta
  capacidad. Con restricciones blandas se muestra la sobreasignación, nunca se oculta.
- Los roles muestran la suma de capacidades de sus integrantes. Paralelamente se
  mantienen filas internas por recurso para no duplicar a una persona multihabilidad.
  La demanda genérica se reparte proporcionalmente a la capacidad nominal entre
  recursos elegibles. **Es una asignación fraccionaria fija y conservadora**: no
  busca todas las combinaciones de trabajadores y puede no encontrar un programa
  que sería factible redistribuyendo esa demanda, especialmente con calendarios
  distintos. No se devuelve una asignación nominal de trabajadores en modo proyecto.
- Nivelación: suma de cuadrados respecto de la media por día/semana, más cuadrados
  de sobrecarga diaria. Plazo: días corridos inclusivos desde inicio del proyecto.
  Ventanas: días corridos fuera de límites. Costo: HH extra permitidas por tarifa
  del recurso y penalidad de 1000 veces la mayor tarifa (mínimo base 1) para HH no
  cubiertas. Las métricas separan sobrecarga total, HH extra permitidas y su costo.
- `weights.normalized()` y escalas `max(1, término CPM inicial)` hacen comparables
  los pesos. Antes/después usan exactamente las mismas escalas y fechas de perfil.
  `load_std` y `peak_load` corresponden al período solicitado; capacidades en HH
  normales, sin sumar las HH extra al histograma. Período nulo usa semana.
- Preajustes GUI, sin acoplar el motor a la GUI: Nivelar carga `(1,0,0,0)`,
  Minimizar plazo `(0,1,0,0)`, Respetar ventanas macro `(0,0,1,0)`,
  Minimizar costo/HH extra `(0,0,0,1)`; orden level/makespan/window/cost.

## Búsqueda y estados

- SGS serial por holgura laborable, prioridad descendente y ventana; las fechas
  fijas se cargan primero. La búsqueda puede extender el término CPM temprano
  hasta el horizonte, porque nivelar tareas inicialmente críticas requiere crear
  plazo adicional. La mejora local respeta predecesores, sucesores ya colocados,
  ventanas duras y el término vigente; actualiza únicamente las celdas afectadas.
- Para ≥1000 tareas independientes de un día se agrupan dominios y demandas
  intercambiables. Un heap mantiene costos marginales y después se mejora entre
  fechas ocupadas. Los IDs se expanden al final: no se omiten actividades.
- El límite de tiempo detiene la mejora y CP-SAT. Lectura, validación, materialización
  de resultados y completar un SGS no interrumpible tienen un costo adicional;
  no se promete una interrupción estricta de todo el llamado al milisegundo.
- Se auditan todas las fechas y capacidades antes de devolver el resultado.
  `infactible` heurístico significa **no se encontró una solución factible**, no
  una prueba matemática de inexistencia; las advertencias lo aclaran. Las fechas
  conflictivas se conservan para diagnóstico. `optimo` solo lo informa CP-SAT.

## CP-SAT opcional y límites conocidos

- Importación diferida de `ortools.sat.python.cp_model`; sin OR-Tools se devuelve
  la heurística y una advertencia. Dependencia del stack: `ortools` (API 9.14+).
  Referencia consultada: [API oficial CpModel](https://or-tools.github.io/docs/python/classortools_1_1sat_1_1python_1_1cp__model_1_1CpModel.html).
- Intervalos en índice laborable de cada actividad y tablas de fechas para
  precedencias. `AddCumulative` usa intervalos diarios en un eje civil común y
  bloqueos de capacidad: comparar directamente índices de calendarios distintos
  produciría restricciones incorrectas.
- Objetivo lineal de nivelación: pico, desviaciones absolutas respecto de la media
  por período y sobrecarga. Se normaliza por su propia referencia CPM, pues no
  equivale al objetivo cuadrático de la heurística. El estado óptimo se refiere
  a este modelo lineal; las métricas públicas siguen mostrando el objetivo común.
- HH discretizadas a 0,001; demanda redondeada arriba y capacidad abajo. Se audita
  también en flotante. Una heurística factible se conserva si solo el modelo entero
  resulta infactible por ese redondeo. Ocho workers, seed y hints del SGS.
- Para proteger tiempo/memoria, vuelve a heurística si el objetivo diario requiere
  >800 000 opciones de inicio, >3 000 000 contribuciones de carga, >1 000 000 celdas
  recurso/día, o >150 000 intervalos de trabajo. Así el umbral auto de 3000 tareas
  se complementa con el tamaño real del modelo; no se garantiza que todo horizonte
  arbitrario de 3000 actividades admita una formulación diaria compacta.
- **Pendiente de integración:** ejecutar las pruebas reales CP-SAT en un entorno
  con OR-Tools. Aquí no está instalado y su instalación local falló por bloqueo
  de sockets de red. Los tests CP-SAT se omiten explícitamente, no se simula éxito.

## Verificación en este entorno

Comando (sin red): `cd backend; python -m pytest tests/engine -q`.
Durante la entrega se desactiva bytecode y caché pytest para no crear archivos
fuera de las carpetas asignadas.

52 pruebas pasaron y 4 pruebas CP-SAT se omitieron por dependencia ausente.
Incluye red de libro, 12 enumeraciones exhaustivas multicalendario, restricciones,
ALAP propagado, avance, hitos, errores, nivelación, costo, capacidad, ventanas,
integridad de entradas y equivalencia incremental y agrupada.

Mediciones observadas (sintético L–V, un recurso agregado; dependen del equipo):

| Caso | CPM | Optimización | Resultado |
|---|---:|---:|---|
| 10 000 actividades, 15 000 relaciones | 0,292 s | 11,026 s | heurístico, sin conflictos |
| 100 000 actividades independientes de un día | 5,772 s | 31,962 s | heurístico, sin conflictos |

La prueba automática exige CPM 10 000/15 000 <5 s. `engine_benchmark.py --suite`
permite repetir ambos casos con capacidad real y asignaciones, no solo redes vacías.
