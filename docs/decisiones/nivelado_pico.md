# Nivelado por pico — Ronda 3, S1

## Contrato

`ObjectiveWeights.peak: float = 0.0` es opcional. Los pesos siguen normalizándose
por su suma, ahora incluidos los cinco componentes. Cero conserva el camino
histórico y los desempates. La API genera su esquema desde el dominio y rechaza
picos negativos o no finitos. El preset `pico` (alias `peak`) usa
`peak=1, level=0.1, window=0.05`; los pesos explícitos prevalecen como antes.

El término nuevo es `peak * sum(max(carga_habilidad))`, en HH. El máximo se toma
por día o por semana según `options.level_period`. Una tarea asignada a un equipo
fijo sigue contribuyendo a su habilidad: dos equipos de la misma especialidad no
se convierten en habilidades independientes. Se conservan los otros términos y
la penalidad de abrir una desconexión adicional.

El perfil de pico comprende las ocurrencias recibidas. `reserved_load` reserva
capacidad física, pero no informa la habilidad de ese trabajo fijo: no se le
inventa una atribución por habilidad para el objetivo.

`OptimizeMetrics.peak_load` conserva su contrato anterior: máximo **diario por
serie** (habilidad o recurso fijo). No se cambia silenciosamente esa métrica al
pedir semanas. El resultado directo del nivelador añade `peak_by_skill` con
`period`, `maximum` y `sum_of_maxima`, y `peak_search` con estadísticas de búsqueda.
Son atributos auxiliares, no campos nuevos del esquema persistente. El benchmark
S1 los guarda explícitamente. Los integradores que serializan sólo la dataclass
seguirán recibiendo las métricas históricas.

Ejemplo de variante compatible con `programa comparar --plan`:

```json
{"objective":"pico","options":{"method":"heuristic","time_limit_s":28,
 "level_period":"week","seed":7,"allow_overtime":false}}
```

El ranking explícito puede usar `peak_load`. El alias de ranking `score:peak=1`
requiere una modificación ajena a S1 en `optimizacion/metricas.py`; no se incluye.
La ayuda textual de la CLI todavía enumera los cinco presets anteriores, pero
el catálogo de variantes derivado del dominio anuncia `pico` y `peak`.

## Construcción y búsqueda

1. Se construye una referencia con los pesos solicitados excepto `peak=0`,
   incluido el refinamiento histórico. Así existe un programa completo conocido.
2. Si queda suficiente presupuesto, se ensaya una construcción por número de
   fechas laborables elegibles, mayor esfuerzo y prioridad, con desempate local
   mediante la semilla. La alternativa tiene un límite de reloj y estado
   reversible; no se ensayan de nuevo componentes con precedencias.
3. Sólo se conserva si no empeora dispersión, pico diario, sobrecarga, días fuera
   de ventana, costo extra, suma de picos por habilidad ni cantidad de grupos de
   desconexión, y mejora el objetivo solicitado. Una alternativa incompleta o
   forzada se descarta.
4. La búsqueda local prueba desplazamientos y pares de intercambios con las
   mismas cuadrillas/equipos. Calcula deltas en los días afectados. Para esfuerzo
   constante, `Δvarianza = Δsum(carga²) / períodos`. También verifica los máximos,
   el esfuerzo extra y las ventanas; ninguna mejora compra una sobrecarga.

Los swaps revisan capacidad **después de retirar ambos trabajos** y sumar ambos
destinos, incluidas personas compartidas. No se asignan trabajadores nuevos ni se
traslada un lote a otro equipo. Los candidatos ya incorporan calendarios,
ventanas duras y restricciones de fechas; las reservas de carga fija se mantienen.
Las ausencias deben llegar mediante los calendarios/reservas del servicio, como
en el contrato anterior. No se interpretan permisos de desconexión inexistentes
en los datos.

Los lotes múltiples de una misma ocurrencia/equipo, las demandas implícitas y los
componentes con precedencias conservan la construcción especializada. No se
separan durante los intercambios. Un grupo de un solo lote sí puede desplazarse.
Se inspeccionan hasta cuatro fechas y cuatro compañeros por fecha: no hay
enumeración cuadrática de todas las parejas. El máximo de visitas es
`min(2*N_elegibles, int(time_limit_s*20))`, con al menos una si hay presupuesto.

La selección rápida de trabajador prueba primero disponibilidad en la mejor
fecha; sólo crea la matriz trabajador × candidato si esa fecha está llena.
Conserva la elección histórica por carga acumulada y orden de recurso.

## Tiempo y reproducibilidad: límites explícitos

La **nueva** construcción alternativa y búsqueda local usan exclusivamente el
tiempo restante. Se reserva el mayor entre 10 ms y el 10 % del tiempo ya gastado
para materializar métricas, fechas y perfiles. Se comprueba el reloj antes de
iniciar propuestas y de confirmar sus deltas; una alternativa interrumpida
restaura la referencia completa.

Esto no convierte el motor en un servicio de tiempo real. La construcción de la
referencia y su ruta histórica conservan el contrato previo de no perder
ocurrencias, y pueden superar `time_limit_s`. El supervisor CLI ofrece su propio
límite duro de proceso. **No se acredita un límite absoluto del motor ni el margen
del 1 % de pared exigido en la ronda.** Cambiar ese contrato requiere coordinar
el resultado de timeout con consumidores y pruebas históricas.

La semilla y el límite de visitas hacen determinista el trabajo ejecutado. Con
presupuesto suficiente, las repeticiones conservan fechas y asignaciones. Bajo
saturación, alcanzar el corte de reloj puede conservar otra cantidad de mejoras:
no se promete el mismo hash si cambia el trabajo completado antes del límite.
`peak=0` mantiene la reproducibilidad histórica independiente del reloj.

## Verificación y evidencia

Las pruebas nuevas están en `backend/tests/maintenance/test_peak.py`: contrato y
validación, día/semana, equipos de una misma habilidad, tareas de varios días,
demanda implícita, cuadrillas divididas, reserva de capacidad, días de ausencia,
desconexiones, swaps con capacidad ocupada y reversión por presupuesto. Las
salidas se contrastan con `engine.verify.verify_schedule`, sin cambiar ese módulo.

La evidencia reproducible vive en
`../../../agentes/S1/INFORME.md` respecto de este archivo dentro de la copia de
ronda. Incluye JSONL antes/después, hashes, tres semillas por tamaño y modalidad,
comandos CLI y resultados reales de regresión. La máquina es compartida y S1
ejecuta un solo proceso de cálculo a la vez. No se infiere un óptimo global.
