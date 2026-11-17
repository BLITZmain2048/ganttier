# Segunda revisión del optimizador de proyectos — 2026-09-26

Alcance: `backend/ganttier/engine/`, `backend/tests/engine/` y este documento.
Reemplaza las decisiones anteriores que permitían extender por nivelación hasta
el horizonte técnico o medir la desviación sobre todos sus períodos vacíos.
No se modificaron `domain.py`, servicios, calendarios ni el libro de ejemplo.

## Causas verificadas

1. **Dominio excesivo y objetivo sin costo de extensión.** El SGS consideraba
   cada inicio hasta `horizon_end`. Ese horizonte permite construir calendarios
   para un programa prácticamente serial y no representa el plazo deseado.
   Separar demandas positivas reduce los cuadrados aunque no haya sobrecarga;
   con makespan=0 no había precio ni límite operativo para ese desplazamiento.
   Las métricas incluían años vacíos y su escala dependía del horizonte técnico.
2. **Decisiones parciales sin costo de los sucesores.** La construcción por
   costo marginal podía atrasar una predecesora y obligar a sus descendientes a
   incumplir ventanas. El costo de esas ventanas no estaba en la decisión de la
   predecesora. Tampoco se retenía el CPM factible si el resultado era peor.
3. **Presupuesto CP-SAT sobredimensionado.** Sus tablas se presupuestaban sobre
   los dominios de horizonte, no sobre ES/LS propagados del programa acotado.
   Las relaciones entre calendarios distintos se estimaban con todo el índice
   temporal. También se construían variables de carga diaria para años sin trabajo.
4. **Dos sospechas no confirmadas como errores de conversión.** El código ya
   distribuye 90 HH / 180 días como 0,5 HH/día y ya consulta
   `respect_resource_limits`. El objetivo anterior separaba incluso esas demandas
   diminutas sin necesidad. Con suficientes incentivos a evitar carga/costo,
   los modos duro y blando pueden legítimamente devolver el mismo calendario.
   Se añadieron regresiones independientes que comprueban ambas semánticas.

## Política de plazo y búsqueda

`search_bounds.prepare` propaga límites tempranos por precedencias y construye
un SGS temprano con la capacidad diaria permitida (normal más horas extra si
están habilitadas). Su término y el CPM establecen una referencia de plazo.
Cuando se solicitan ventanas, se considera también un calendario completo
alineado con sus inicios y sus sucesores; no se convierten en restricciones duras.
Para costo sin nivelación ni ventanas puede usarse una referencia con capacidad
normal, si resulta factible, para permitir eliminar horas extra.

La cota operativa predeterminada es esa referencia: **cero extensión voluntaria
para mejorar nivelación**. Se propaga hacia atrás y delimita la holgura disponible
de cada actividad. Calendarios, fechas reales, límites duros y horizonte siguen
siendo autoritativos. El SGS es una cota constructiva, no una demostración del
mínimo RCPSP: en otra red puede ser conservador o no encontrar una solución.
No se promete que toda red con capacidad agregada suficiente sea realizable
en el CPM; importan las precedencias y la disponibilidad diaria.

Configuración adicional, sin cambiar el contrato de `OptimizeOptions`:

```powershell
$env:GANTTIER_LEVEL_EXTENSION_RATIO = '0.15'
```

Permite como máximo `floor(0.15 * duración civil inclusiva CPM)` días adicionales
sobre la referencia, siempre dentro del horizonte. El valor por defecto es `0`;
negativos y no finitos se rechazan. Es una configuración del proceso, no un campo
expuesto por la API. La extensión voluntaria tiene además una penalidad implícita
`max(0, fin - referencia) / duración civil de referencia`, incluso con makespan=0.
La extensión que el SGS necesita para capacidad o una ventana posterior se
admite en la referencia; no se interpreta como autorización para estirar años
por una pequeña mejora de dispersión.

La mejora local empieza desde un **programa completo**, respeta sucesores ya
ubicados y considera solo movimientos admisibles. Se conserva el mejor programa
factible entre la semilla, la mejora y el plan inicial. Un calendario completo
alineado con ventanas permite mover una cadena cuando el movimiento aislado de
su predecesora estaría bloqueado por un sucesor temprano.
El camino agrupado de independientes sigue disponible para 100.000 actividades.

## Carga y comparación de métricas

Objetivo, escalas, perfiles y desviaciones usan un intervalo común desde el inicio
del proyecto hasta el máximo del término inicial y la cota operativa de búsqueda,
redondeado a los períodos solicitados. Así se comparan sobre los mismos días y
se conservan ceros interiores, pero se excluye el relleno técnico posterior.
Cambiar solo el horizonte de enero de 2026 a enero de 2035 no cambia fechas,
objetivo, desviación ni etiquetas en la regresión correspondiente.

La heurística mantiene cuadrados de carga respecto de la media y de sobrecarga.
CP-SAT mantiene su aproximación de pico y desviación absoluta. La penalidad de
extensión se añade a ambos. Sus objetivos de nivelación siguen siendo distintos;
`optimo` significa óptimo de la formulación CP-SAT **dentro de los dominios de
búsqueda**, no óptimo global del RCPSP ni del objetivo cuadrático mostrado.

`respect_resource_limits=False` omite el techo de capacidad durante la búsqueda
local, el modelo CP y la auditoría final; conserva la demanda y las penalidades
activadas por los pesos de nivelación/costo. La semilla se construye con capacidad
para contar con una referencia de plazo útil. La prueba con dos tareas de 8 HH,
ventana común y alta ponderación de ventana termina con 8 HH de sobrecarga en
modo blando y 0 HH en modo duro, con inicios distintos.

Las HH explícitas, unidades, calendarios, fracciones pendientes y redondeo CP a
0,001 HH conservan su semántica anterior. Cuatro tareas de 180 días y 90 HH pueden
solaparse: el total es 360 HH, sin sobrecarga ni extensión respecto del CPM.

## CP-SAT: dominios y alternativa semanal

Se presupuestan los dominios útiles de cada tarea tras las pasadas de límites.
Las tablas de relaciones usan el rango alcanzable del evento predecesor. Las
variables de objetivo diario terminan en la cota operativa, aunque los calendarios
originales tengan muchos años adicionales.

Para la demo real de 120 actividades / 133 relaciones / 18 recursos:

| Formulación | Opciones con demanda | Contribuciones de carga | Celdas de tablas |
|---|---:|---:|---:|
| Dominio diario útil | 10.570 | 263.294 | 22.056 |
| Alternativa semanal forzada en la prueba | 2.385 | 59.976 | 4.984 |

Si el dominio diario excede el presupuesto, se intenta una malla de inicios por
semana. Se conservan los extremos del dominio, fechas fijas, inicios de la
solución heurística y fechas relevantes de ventanas. Las tablas de rangos usan
`add_element`, por lo que funcionan también con candidatos no consecutivos.
Las duraciones, precedencias y **capacidades siguen siendo diarias**: no se
acepta una sobrecarga diaria por compensarla con horas libres otra jornada.
Se advierte que la malla restringe la búsqueda; no equivale al óptimo diario.

Permanecen los límites de 800.000 opciones, 3.000.000 contribuciones más tablas,
1.000.000 celdas recurso/día y 150.000 intervalos de trabajo. Una instancia muy
grande o con duraciones largas todavía puede excederlos después de la reducción.
Ese respaldo y la ausencia de OR-Tools tienen ahora mensajes diferenciados.
La prueba de presupuesto con 3.000 actividades y horizonte hasta 2035 cabe;
el número de actividades por sí solo no garantiza tiempo de solución.

Se contrastó el uso de intervalos y restricciones acumulativas con las
[recetas oficiales de scheduling de OR-Tools](https://github.com/google/or-tools/blob/stable/ortools/sat/docs/scheduling.md).

## Reproducción y métricas

El Python local carece de FastAPI y OR-Tools. La instalación en la ruta de pruebas
fue bloqueada por la red. Para evitar la API, `demo_problem()` importa el **mismo
Excel real** con `repo.save_import_bundle`, SQLite `:memory:`, sincroniza avances
y llama a `services.build_problem`; establece las fechas iniciales con CPM.
Solo durante la importación de prueba se fija la fecha de validación del repositorio
en 2028 para aceptar los avances ficticios de 2027 del libro. No se alteran las
fechas de las actividades ni los archivos del resto del sistema.

La reproducción local previa dio horizonte 2032-05-14, modo blando con término
2032-05-14 y **31.057 días** fuera de ventana; el modo duro no encontró solución
por 15 jornadas de conflicto en Gestión de suministros. Es distinta del resultado
2034 aportado por el integrador; no se presenta como una reproducción numérica
idéntica, pero confirma el mismo defecto de extensión hasta el horizonte.

Resultado corregido para pesos `(level=1, makespan=0, window=1, cost=0.2)`,
agrupación por habilidad y períodos semanales:

| Métrica | CPM inicial | Optimizado |
|---|---:|---:|
| Término | 2028-09-14 | **2028-09-14** |
| Extensión sobre CPM | — | **0 días** |
| Pico semanal | 304 HH | 240 HH |
| Desviación semanal media | 23,8378 HH | 21,4307 HH |
| HH sobre capacidad normal | 855,5 | 137,5 |
| HH extra permitidas | 155,5 | 137,5 |
| Sobrecarga no cubierta | 700 HH | **0 HH** |
| Costo de horas extra | 5.166.000 | 4.950.000 |
| Días fuera de ventana | 926 | 878 |
| Actividades fuera de ventana | 21 | 21 |
| Objetivo común | 1,0000 | 0,7995 |

Los dos valores de `respect_resource_limits` obtienen aquí el mismo calendario:
los pesos dados ya favorecen eliminar la sobrecarga no cubierta. Las 137,5 HH
restantes son horas extra autorizadas y no violaciones del techo duro.
La igualdad no prueba que el interruptor se ignore; la regresión de sobrecarga
forzada demuestra el efecto de cambiarlo. El resultado conserva tardanzas
inherentes al programa y advertencias de avance/holgura; no promete cero ventanas
incumplidas cuando el CPM ya incumple el plazo FNLT.

Las desviaciones históricas de 14,09 HH sobre todo el horizonte no son comparables
con esta tabla: se recalcularon ambos lados sobre el mismo intervalo útil.

## Verificación

Desde `Ganttier/backend`, sin cachés fuera de las rutas autorizadas:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest tests/engine -q -p no:cacheprovider
```

Resultado: **97 aprobadas, 12 omitidas** (requieren OR-Tools). Validación sintáctica
de todos los módulos del motor: `ENGINE_SYNTAX_OK`. El único aviso de entorno es
que el respaldo local de feriados de A1 cubre 2024–2030, mientras el horizonte
técnico del libro llega a 2032; el programa corregido termina en 2028.

La suite incorpora demo real, límite de extensión, reducción de desviación sin
extensión, conmutación de capacidad, demanda diminuta, ventana tardía de una
cadena, invariancia frente al horizonte, configuración, presupuesto diario de
120/3.000 actividades y alternativa semanal. La prueba real CP-SAT instrumenta
`CpSolver.solve` para exigir que llegue un modelo válido al solver; no basta con
un respaldo heurístico. Usa `importorskip`, igual que las demás pruebas OR-Tools.

La ejecución real de CP-SAT queda pendiente en un entorno con OR-Tools; los tests
de dominios y presupuesto sí se ejecutaron aquí. No se simuló un solver ni se
declara validada su ejecución por revisar el modelo.

El benchmark existente, leído sin modificar, también se ejecutó con assertions
de estado, ausencia de conflictos y tiempos máximos:

| Caso | CPM | Optimización |
|---|---:|---:|
| 10.000 actividades / 15.000 relaciones | 0,2223 s | 8,5684 s |
| 100.000 independientes | 6,5858 s | 24,0470 s |

Ambas optimizaciones cumplen <60 s; CPM de 10.000 cumple <5 s. Son mediciones de
las redes sintéticas del benchmark, no garantías para cualquier distribución.
