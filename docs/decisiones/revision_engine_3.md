# Tercera revisión del motor CP-SAT — 2026-09-26

Alcance: `backend/ganttier/engine/`, `backend/tests/engine/` y este documento.
Se mantienen las cotas operativas, los calendarios, la auditoría y las métricas
públicas de la segunda revisión. Cambian la formulación CP-SAT y su admisión.

## Diagnóstico

La ejecución real comunicada por Claude se detenía en presolve: 34.441 LinMax,
dominios de hasta 481.660.168.000, aproximadamente 1,2 GB y UNKNOWN a los 28 s.
El hint factible no bastaba porque la búsqueda no empezaba. La construcción
anterior materializaba cargas, excesos y mínimos diarios incluso al solicitar
nivelación semanal; después multiplicaba las cotas de desviación por el
horizonte y el número de períodos. No se desactiva presolve, dada la caída
nativa observada con esa configuración.

## Formulación

Cada actividad elige exactamente un inicio admisible mediante `x[i,k]`.
Inicio y término son combinaciones lineales de sus fechas candidatas; se
resta el mínimo común de los coeficientes. Las precedencias usan directamente
el evento de cada candidato predecesor y el calendario del sucesor, incluidos
FS/SS/FF/SF, desfases negativos, hitos y fechas reales. No se usan tablas
Element ni intervalos diarios. Las ventanas blandas también se precalculan
por candidato, evitando variables Max para sus desviaciones.

Para cada demanda se calculan sus días laborables exactos. Las contribuciones
se suman directamente por grupo y período, antes de añadirlas al modelo:

```text
load[g,p] = sum(HH[i,k,g,p] * x[i,k])
load[g,p] - target[g] = over[g,p] - under[g,p]
peak[g] >= load[g,p]
```

`target[g]` es la carga media del incumbente, redondeada, sobre el intervalo
común de evaluación. Se minimiza la suma de sobre/subdesviaciones y el pico.
Las cotas provienen de la suma de las máximas contribuciones alcanzables de
cada actividad **en ese período**. No contienen factores de horizonte.
La meta queda fija; si las horas del calendario varían según el inicio, no se
recalcula la media durante la búsqueda. Con `level_period='day'` se usa el
mismo esquema por día.

La capacidad dura sigue siendo diaria:

```text
sum(HH[i,k,g,d] * x[i,k]) <= normal[g,d] + extra_permitida[g,d]
```

Son filas lineales dispersas, sin variables diarias de carga, exceso u horas
extra en el modelo semanal. Se mantienen los límites tanto de recursos
individuales como de habilidades. Si `respect_resource_limits=False`, estas
filas no se construyen.

Para costo hay una variable `ot[g,p]` por recurso/período y una de sobrecarga
no cubierta:

```text
0 <= ot[g,p] <= sum(extra_permitida[g,d], d en p)
load[g,p] <= normal[g,p] + ot[g,p] + spill[g,p]
```

Se minimizan el precio de `ot` y la penalidad de `spill`. El precio semanal
es la media ponderada por disponibilidad extra. No hay Min/Max reificados.
**Este costo semanal es una aproximación:** capacidad normal libre del martes
puede compensar carga extra del lunes en el sustituto, pero nunca evita una
restricción dura diaria. El costo y la sobrecarga publicados se recalculan
con las fórmulas diarias originales. Se conserva la semilla certificada si el
resultado libre empeora el objetivo público. También se elimina del sustituto
de nivelación el antiguo término separado de exceso diario; la capacidad dura
y el costo semanal cumplen esa función parcialmente.

## Escala y precisión

Se emplean décimas de HH (`SCALE=10`): demandas diarias hacia arriba y
capacidades hacia abajo, con tolerancia numérica de 1e-8. La auditoría del hint
usa exactamente el mismo redondeo. Esto es conservador y puede rechazar un
programa factible en aritmética real cuando las demandas son muy pequeñas.
No se convierte tal rechazo en una prueba de infactibilidad del problema real.

El objetivo usa coeficientes enteros positivos. `objective_coefficients`
reserva una unidad para cada término y distribuye la precisión restante
proporcionalmente; la suma de coeficiente por cota no supera 10.000.000.
`objective_score` tiene ese dominio explícito. Las variables de fechas y HH
también se comprueban contra 10.000.000; los datos no representables reciben
respaldo heurístico y aviso. No se usa objetivo flotante ni se multiplican HH
por precios monetarios nominales gigantes.

La cuantización puede alterar pesos relativos pequeños. Por ello, `optimo`
significa óptimo del sustituto entero y, cuando corresponde, de la malla
restringida; se comunica esa limitación. No significa óptimo de la varianza
cuadrática publicada.

## Solución temprana y presupuesto temporal

El hint contiene **todas** las variables no constantes: selectores, fechas,
ventanas, cargas, desviaciones, pico, horas extra, sobrecarga y objetivo. Se
evitan variables constantes booleanas implícitas al añadir igualdades triviales.
Cuando el hint es factible se añade `objective_score <= score_heuristico`,
calculado con el mismo objetivo entero.

1. Una primera resolución fija el hint completo mediante
   `fix_variables_to_their_hinted_value=True` y detiene al encontrar solución.
   Tiene como máximo 2 s y 20% del tiempo restante. Solo si el solver devuelve
   FEASIBLE/OPTIMAL se guarda la semilla como **factible CP-SAT**. Nunca se
   llama óptima por haberla resuelto fijada.
2. Se destruye ese solver y otro intenta mejorar el mismo modelo con las
   decisiones libres, dentro del tiempo restante. Si devuelve UNKNOWN, se
   conserva la solución CP-SAT ya certificada. Si ninguna resolución encuentra
   solución, el resultado sigue siendo explícitamente `heuristico`.

Ambas resoluciones usan `repair_hint=True`, `hint_conflict_limit=100`,
`num_search_workers=min(8, os.cpu_count() or 1)`, presolve activado,
`max_presolve_iterations=1`, probing de presolve y simetría desactivados.
Los nombres y semántica se contrastaron con el
[código oficial de parámetros de OR-Tools](https://github.com/google/or-tools/blob/stable/ortools/sat/sat_parameters.proto)
y la [API Python oficial](https://github.com/google/or-tools/blob/stable/ortools/sat/python/cp_model.py).

La ruta CP-SAT dispone de hasta `min(time_limit_s, 19)` segundos desde la
entrada pública, incluida la heurística y la construcción. El segundo restante
es margen para reconstruir perfiles y auditar. Esto apunta a ≤20 s; un límite
de solver no constituye una garantía de tiempo real ante pausas del SO o
trabajo nativo no interrumpible. La prueba permite <25 s de pared.

## Admisión y escalamiento

`plan_model` no importa OR-Tools ni crea matrices candidato×duración. Estima
selectores, contribuciones, períodos, filas y memoria de los trabajadores.
Si hay más de 4.000 opciones o 100.000 contribuciones, restringe los inicios a
una malla semanal que conserva extremos, límites de ventanas y el incumbente.
No altera los días de trabajo dentro de las actividades.

Se rechazan planes con más de 30.000 selectores, 250.000 contribuciones o
320 MiB de memoria estimada. La fórmula incluye 64 MiB de base y copias por
trabajador; es una **estimación de admisión, no un límite de RSS**. En `auto`
se comprueba después de propagar cotas y antes de mejorar la heurística: si
excede el presupuesto, se usa la heurística con el tiempo original, sin
construir ni resolver CP-SAT. La ruta explícita `cpsat` tiene la misma defensa.

Se conserva `candidate_domains` y sus presupuestos históricos como herramienta
de inspección compatible con las pruebas previas. `plan_model` aplica después
los umbrales más estrictos; el estimador `tables` heredado es conservador y no
implica que el modelo nuevo construya tablas.

Las 3.000 actividades del caso compacto de capacidad compartida pasan la
admisión local. No se promete CP-SAT monolítico para 3.000 actividades con
dominios amplios, muchas asignaciones o calendarios extensos: esos casos van
directamente a la heurística. La cardinalidad por sí sola no decide.

## Tamaño esperado de la demo

Se midió la preparación numérica del libro de la demo, con 120 actividades,
133 relaciones y 18 recursos, sin construir un proto ni ejecutar un solver.
La fixture fija la fecha de importación en 2028-01-01 para aceptar el avance
ficticio del libro; cambios en esa fecha o en el incumbente pueden cambiar
los conteos marginalmente.

| Concepto | Preparación nueva |
|---|---:|
| Selectores no constantes | 2.441 |
| Contribuciones diarias, incluidos recursos y habilidades | 59.976 |
| Períodos semanales útiles | 89 |
| Cargas semanales con soporte | 947 |
| Desviaciones superiores / inferiores variables | 469 / 473 |
| Variables de horas extra / sobrecarga no cubierta | 474 / 355 |
| Filas de capacidad diaria con soporte | 4.352 |
| Cota estimada de variables totales usando el soporte real | 5.536 |
| Mayor cota de carga semanal encontrada | 19.670 décimas HH |
| LinMax / Element / intervalos | 0 / 0 / 0 |
| Memoria estimada de admisión con hasta 8 trabajadores | 195.672.352 bytes ≈ 187 MiB |

El estimador deliberadamente holgado anuncia 21.491 variables y 139.582
términos; el cálculo con soporte real reduce las variables esperadas a unas
5.500. Los valores de memoria y tiempo **no son mediciones CP-SAT**.

## Verificación y ejecución pendiente

En este entorno no está instalado OR-Tools. No se instaló ni se sustituyó por
un solver simulado. Se conservaron las pruebas existentes y se añadieron
regresiones de admisión de la demo y 3.000 actividades, escape temprano de
`auto`, escala entera y rechazo de magnitudes no representables.

También se añadieron pruebas reales con `pytest.importorskip('ortools')`:

- Demo: FEASIBLE/OPTIMAL obligatorio, <25 s, método `cpsat`, restricciones
  diarias auditadas, objetivo no peor que el programa inicial, hint completo,
  dominios ≤1e7, menos de 20.000 variables y parámetros de presolve.
- En Unix, esa prueba exige además RSS máximo del proceso <400 MB decimales. Ejecutarla
  aislada evita que el máximo histórico de otro test contamine la medición.
  En Windows no se comprueba RSS automáticamente.
- Caso de semanas partidas por fin de semana: valida el modelo, certifica
  el hint con CP-SAT y compara las cargas semanales con el perfil NumPy.

Comandos desde `Ganttier/backend`, sin cachés escritas fuera del alcance:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest tests/engine -q -p no:cacheprovider
python -m pytest tests/engine/test_cpsat_compact.py::test_real_demo_feasible_under_25_seconds -q -p no:cacheprovider
```

Resultado local: **102 aprobadas, 14 omitidas**; las omitidas necesitan
OR-Tools. Se verificó además sintaxis sin generar bytecode. Continúa el aviso
preexistente de feriados con cobertura local 2024–2030.

**Pendiente para Claude:** ejecutar el test aislado en el equipo de 2 núcleos,
anotar versión exacta de OR-Tools, tiempo total, estados de las dos resoluciones
y RSS. La reducción estructural está implementada; los objetivos de ≤20 s y
≤400 MB, así como una mejora sobre la heurística, requieren esa comprobación
real y no se declaran alcanzados a partir de estimaciones.
