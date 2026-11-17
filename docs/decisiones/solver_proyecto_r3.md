# Solver de proyecto — Ronda 3, S2

Se conservan las firmas de `optimize`, SGS, multiinicio y CP-SAT, los objetos de
dominio y el formato de salida. Sólo se modifican archivos propios de S2. La
verificación independiente no se modifica.

## Construcción y presupuesto

La construcción temprana busca la primera posición con capacidad, sin calcular
costos ni desviaciones de carga que no influyen en esa decisión. Para jornadas
uniformes utiliza prefijos de días saturados, evitando matrices de tamaño
inicios × duración. Las jornadas variables y el reparto dinámico entre recursos
mantienen el cálculo exacto de `Loads.amount`. Se inspeccionan bloques acotados;
las precedencias FS/SS/FF/SF y sus desfases continúan pasando por `Model`.

Durante la primera fase se da prioridad al límite duro de cada actividad y se
detienen los reinicios en cuanto existe un candidato completo que supera las
comprobaciones internas. La construcción puede usar hasta el 80 % del tiempo
restante; normalmente termina antes. El resto se reparte entre multiinicio,
movimientos individuales, CP-SAT completo cuando cabe y vecindades CP-SAT.
Todas las fases comparten límites absolutos; no se concede un segundo presupuesto
completo a un reintento. Las auditorías y la materialización final tienen costo
adicional, como en el contrato previo; no se promete un límite de pared estricto
al milisegundo. El supervisor CLI conserva su límite duro por proceso.

Si no cabe el CP-SAT completo, la heurística recibe el 85 % del remanente; para
más de 500 tareas sólo el 15 % de ese tramo va a reinicios, reservando la mayoría
para movimientos individuales. Esta decisión se tomó después de medir una
versión inicial que empeoraba la dispersión con presupuestos cortos de 1500
tareas. Sus resultados se conservan separados de la comparación final.
Si el CP-SAT completo devuelve `None`, el remanente se destina a movimientos
locales, evitando iniciar inmediatamente otra construcción CP-SAT. La prueba
existente del presupuesto de 40 s conserva su umbral y su firma de sustitución.

## Vecindades CP-SAT

`engine/lns.py` selecciona hasta 32 actividades próximas en el tiempo, alternando
con actividades que comparten recursos. Limita los dominios a la ventana temporal
seleccionada más 14 días de radio y conserva las fechas actuales. Fija las fechas
de todas las demás actividades, incluyendo sus cargas. El modelo mantiene todas
las precedencias, capacidades, calendarios, ventanas y fechas fijas del proyecto;
no resuelve un subproyecto que omita las relaciones que cruzan la vecindad.
Las cargas de períodos completamente fijos y sus desviaciones/costos se evalúan
como constantes, sin variables auxiliares. Esto vale también con un hint inválido:
la carga física constante se calcula desde el dominio, no desde ese hint.

Hasta doce vecindades, con un máximo de dos segundos cada una para construcción y
búsqueda, utilizan el tiempo disponible. Sólo se acepta una mejora estricta del
objetivo diario exacto y sin violaciones internas. El resultado completo pasa
por `verify_with_options` antes de devolver fechas aplicables. La optimalidad de
una vecindad o de una malla restringida no se presenta como óptimo del proyecto.
Sin OR-Tools se conserva el incumbente y se comunica la limitación.

## Hilos, reproducibilidad y diagnóstico

`GANTTIER_CPSAT_WORKERS` controla tanto la estimación de memoria como el solver.
Su valor por defecto pasa a uno; valores no enteros o menores de uno dan un error
en español. La semilla y los desempates de las vecindades son estables. Una misma
semilla reproduce búsquedas completadas con un hilo; los cortes por tiempo de
pared y la carga de la máquina pueden cambiar cuántas mejoras llegan a terminar.
No se promete identidad bit a bit entre máquinas o con varios hilos.

Las contradicciones temporales comprobables aun sin recursos se devuelven como
`method_used=precheck`, con los códigos y restricciones involucrados. Una búsqueda
agotada, una asignación nominal conservadora o la infactibilidad del modelo
entero/malla CP-SAT no prueban que el proyecto original sea imposible. Conservan
`status=infactible` sin fechas aplicables por compatibilidad y la capa de servicio
los clasifica como `infeasibility=no_encontrada`. No se modifica `domain.py`.
El precheck identifica la fecha MSO/MFO incluso cuando Model ya fijó su inicio o
fin y la contradicción aparece como una precedencia o un límite de horizonte.

Un RCPSP arbitrario no admite una garantía heurística universal de primer factible
dentro de un tiempo finito. También sigue vigente el límite del reparto nominal
fraccionario de recursos de `Loads`; el verificador independiente no se relaja.

## Evidencia

La línea base de engine/solver/modos tiene 387 pruebas aprobadas y dos slow
excluidas por la configuración heredada. App agrega 23 pruebas; el último
resultado observado de sus 410 casos únicos es 409 aprobadas y una fallida.
Este recuento combina una corrida completa con verificaciones dirigidas de las
correcciones; no equivale a una corrida completa verde. El fallo restante es
el umbral de 120 s de mantenimiento de 20000 ocurrencias, fuera del código S2:
311.216 s en la corrida completa y 385.665 s al repetirla aislada. En ambos
casos pasaron factibilidad y cantidad de fechas. Se conserva en CONFLICTOS.md.

La comparación usa 300/1500/3000 actividades, semillas 6 y 17, presupuestos
10/20/60 s; se declara el recorte de semillas y no se extrapola a 10000 tareas.
Las familias tienen las cuatro precedencias con desfases, tres habilidades y
ventanas duras/blandas. Se conoce un testigo factible, auditado antes de buscar,
que no se entrega al optimizador. Pesos: level=.3, makespan=1, window=.2, cost=0,
peak desactivado; método auto, período diario, recursos limitados y sin horas extra.

Un proceso pesado S2 y un hilo CP-SAT/BLAS/OMP/MKL; Windows, Python 3.10.6,
16 CPU lógicas y 15.65 GiB de RAM total. La máquina compartida no estuvo aislada:
se observaron sólo 526 MiB de RAM libre durante una regresión. Se registran
CPU, pared e instrumentación; las diferencias de pared no identifican por sí
solas el efecto del código. Los fallos y versiones iniciales también se conservan.
La tabla se genera exclusivamente desde los JSONL reales de `agentes/S2/`;
el generador comprueba las 18 claves de cada versión y cero violaciones
independientes en toda solución devuelta como factible.

Resultado definitivo: 16/18 factibles en pre y 17/18 en app. La mediana de load_std
mejora en 300 actividades, empeora en 1500/3000 y el makespan se conserva en
casos comparables. El caso 3000/semilla 6/10 s sigue sin candidato; la semilla 17
sí se resuelve donde pre fallaba. Persisten excesos de tiempo de pared; no se
afirma una mejora general ni una garantía de primer factible en todo caso.
CPU/pared acumulado: 75.2 % pre frente a 24.0 % app, sin aislamiento de la máquina.

Contrato CLI probado con base ficticia de 48 actividades: describir, diagnosticar,
comparar (plazo/nivelar/equilibrado) y resolver, todos exit 0; tres variantes y
resolver factibles, auditados sin violaciones, en modo simulado. Comandos exactos,
stdout/stderr, JSONL y payloads se conservan en agentes/S2. El metadato de hilos
del supervisor sigue mostrando 8 aunque el solver usa la variable establecida
en 1; se propone la corrección al propietario en CONFLICTOS.md.



<!-- S2 EVIDENCIA -->

## Tabla antes/después medida

Medianas de dos semillas (6, 17). Flecha: pre → app. Un proceso de solver y un hilo CP-SAT; máquina compartida.
Plazo en días corridos transcurridos. Primer factible: candidato completo observado y auditado, no callback nativo.
Una ausencia de métricas no es un plazo cero. Los tiempos incluyen instrumentación y auditorías.

| Tareas | Presupuesto (s) | Factibles | Plazo (d) | load_std (HH) | Primer factible (s) | Total (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 300 | 10 | 2/2 → 2/2 | 178.50 → 178.50 | 13.30 → 13.06 | 0.26 → 0.32 | 9.43 → 16.74 |
| 300 | 20 | 2/2 → 2/2 | 178.50 → 178.50 | 13.23 → 10.55 | 0.41 → 1.34 | 19.50 → 19.42 |
| 300 | 60 | 2/2 → 2/2 | 178.50 → 178.50 | 13.16 → 4.92 | 0.41 → 0.93 | 59.46 → 59.66 |
| 1500 | 10 | 2/2 → 2/2 | 979.50 → 979.50 | 7.75 → 12.15 | 2.18 → 5.55 | 12.25 → 33.99 |
| 1500 | 20 | 2/2 → 2/2 | 979.50 → 979.50 | 5.03 → 12.15 | 2.01 → 7.16 | 22.10 → 29.90 |
| 1500 | 60 | 2/2 → 2/2 | 979.50 → 979.50 | 4.84 → 5.79 | 1.58 → 6.58 | 53.75 → 63.60 |
| 3000 | 10 | 0/2 → 1/2 | — → 1977.00 | — → 12.05 | — → 4.63 | 21.33 → 65.83 |
| 3000 | 20 | 2/2 → 2/2 | 1978.50 → 1978.50 | 8.04 → 12.00 | 4.61 → 8.04 | 24.54 → 45.28 |
| 3000 | 60 | 2/2 → 2/2 | 1978.50 → 1978.50 | 4.95 → 6.27 | 4.40 → 9.07 | 64.74 → 65.15 |

Relación acumulada CPU/pared del proceso medido: pre 75.2% → app 24.0%. Son observaciones en una máquina compartida, no una estimación de carga global ni una comparación causal aislada.

### Corridas individuales

| Versión | Tareas | Semilla | Presupuesto | Factible | Plazo | load_std | Primer factible | Total | CPU | Instrumentación |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| pre | 300 | 6 | 10 | True | 177.000 | 13.097 | 0.062 | 9.426 | 6.547 | 0.005 |
| pre | 300 | 6 | 20 | True | 177.000 | 13.097 | 0.535 | 19.614 | 14.688 | 0.026 |
| pre | 300 | 6 | 60 | True | 177.000 | 13.051 | 0.472 | 59.530 | 41.656 | 0.027 |
| pre | 300 | 17 | 10 | True | 180.000 | 13.493 | 0.465 | 9.436 | 8.391 | 0.026 |
| pre | 300 | 17 | 20 | True | 180.000 | 13.360 | 0.284 | 19.386 | 13.406 | 0.019 |
| pre | 300 | 17 | 60 | True | 180.000 | 13.271 | 0.354 | 59.396 | 49.312 | 0.026 |
| pre | 1500 | 6 | 10 | True | 980.000 | 9.070 | 2.107 | 12.487 | 7.953 | 0.256 |
| pre | 1500 | 6 | 20 | True | 980.000 | 4.989 | 2.247 | 21.746 | 17.844 | 0.129 |
| pre | 1500 | 6 | 60 | True | 980.000 | 4.803 | 1.561 | 49.898 | 38.609 | 0.116 |
| pre | 1500 | 17 | 10 | True | 979.000 | 6.432 | 2.245 | 12.010 | 9.125 | 0.220 |
| pre | 1500 | 17 | 20 | True | 979.000 | 5.079 | 1.766 | 22.447 | 18.828 | 0.115 |
| pre | 1500 | 17 | 60 | True | 979.000 | 4.883 | 1.602 | 57.609 | 39.906 | 0.119 |
| pre | 3000 | 6 | 10 | False | — | — | — | 20.765 | 16.516 | 0.736 |
| pre | 3000 | 6 | 20 | True | 1980.000 | 7.614 | 4.779 | 24.958 | 17.625 | 0.312 |
| pre | 3000 | 6 | 60 | True | 1980.000 | 4.919 | 3.830 | 64.777 | 50.766 | 0.249 |
| pre | 3000 | 17 | 10 | False | — | — | — | 21.898 | 17.312 | 1.001 |
| pre | 3000 | 17 | 20 | True | 1977.000 | 8.463 | 4.438 | 24.122 | 17.203 | 0.243 |
| pre | 3000 | 17 | 60 | True | 1977.000 | 4.977 | 4.969 | 64.705 | 46.391 | 0.362 |
| app | 300 | 6 | 10 | True | 177.000 | 13.402 | 0.076 | 23.874 | 2.453 | 0.006 |
| app | 300 | 6 | 20 | True | 177.000 | 7.987 | 1.259 | 19.332 | 3.172 | 0.055 |
| app | 300 | 6 | 60 | True | 177.000 | 5.096 | 1.046 | 59.404 | 14.953 | 0.053 |
| app | 300 | 17 | 10 | True | 180.000 | 12.716 | 0.563 | 9.614 | 1.938 | 0.055 |
| app | 300 | 17 | 20 | True | 180.000 | 13.121 | 1.423 | 19.510 | 4.094 | 0.080 |
| app | 300 | 17 | 60 | True | 180.000 | 4.738 | 0.807 | 59.911 | 12.781 | 0.061 |
| app | 1500 | 6 | 10 | True | 980.000 | 12.211 | 5.530 | 35.351 | 6.266 | 0.522 |
| app | 1500 | 6 | 20 | True | 980.000 | 12.211 | 5.697 | 35.693 | 6.656 | 0.473 |
| app | 1500 | 6 | 60 | True | 980.000 | 6.247 | 9.151 | 63.983 | 12.953 | 0.923 |
| app | 1500 | 17 | 10 | True | 979.000 | 12.093 | 5.575 | 32.629 | 6.297 | 0.371 |
| app | 1500 | 17 | 20 | True | 979.000 | 12.093 | 8.614 | 24.109 | 6.391 | 0.421 |
| app | 1500 | 17 | 60 | True | 979.000 | 5.328 | 4.000 | 63.224 | 17.484 | 0.321 |
| app | 3000 | 6 | 10 | False | — | — | — | 88.404 | 16.578 | 2.637 |
| app | 3000 | 6 | 20 | True | 1980.000 | 11.936 | 7.426 | 43.667 | 12.688 | 0.636 |
| app | 3000 | 6 | 60 | True | 1980.000 | 6.159 | 6.331 | 66.521 | 20.641 | 0.723 |
| app | 3000 | 17 | 10 | True | 1977.000 | 12.054 | 4.631 | 43.246 | 13.094 | 0.617 |
| app | 3000 | 17 | 20 | True | 1977.000 | 12.054 | 8.662 | 46.902 | 12.281 | 0.830 |
| app | 3000 | 17 | 60 | True | 1977.000 | 6.383 | 11.804 | 63.780 | 21.188 | 0.840 |

### Regresión registrada

Se conservan las corridas completas y las repeticiones dirigidas. El recuento consolidado no representa una única corrida completa verde.
En ambas versiones pytest.ini excluye las dos pruebas slow existentes de 100000 elementos.

- pre_tests.xml: tests=387, failures=0, errors=0, skipped=0, time=675.539
- app_full_before_diagnostics.xml: tests=409, failures=2, errors=0, skipped=0, time=1103.755
- diagnostic_green.xml: tests=24, failures=0, errors=0, skipped=0, time=159.066
- maintenance_isolated.xml: tests=1, failures=1, errors=0, skipped=0, time=457.059

App, último resultado observado de 410 casos únicos: passed=409, failure=1, error=0, skipped=0.
La última corrección sólo agrega contexto MSO/MFO al diagnóstico anticipado; sus 23 pruebas propias y el caso API afectado se repitieron. Los demás resultados proceden de la corrida completa.
