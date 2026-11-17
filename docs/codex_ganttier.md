# Operar Ganttier desde Codex

Lea la [referencia CLI](cli.md) y obtenga `describir` antes de construir llamadas. Use el intérprete de `.venv`, cwd `backend/`, una base explícita y una carpeta de entregables en `trabajos/`. No abra la GUI para automatizar. Lea el JSON de stdout, el código del proceso y stderr por separado.

## Preparación

Estas recetas usan `python` del entorno activado, el proyecto de prueba `TST proyecto` y el mantenimiento `TST equipos` creados por las pruebas TST. Para otro escenario sustituya esos nombres por IDs descubiertos, fechas y valores por datos del usuario. Los JSON de entrada y salida se guardan en `../trabajos/mi_cli/`; cree esa carpeta antes de comenzar. Configure `GANTTIER_DB`, `GANTTIER_DATA_DIR`, `GANTTIER_CLI_ESCRITURA=0` y `GANTTIER_CLI_PARALELO=2` como en la referencia.

```bash
python -m ganttier.cli describir
python -m ganttier.cli estado
python -m ganttier.cli proyectos listar
python -m ganttier.cli -p "TST proyecto" proyectos resumen
python -m ganttier.cli describir --comando "programa comparar"
```

Confirme que `db.path` apunta a la base solicitada. Si el nombre no es único, seleccione ID; no elija el primer resultado. Conserve versión, esquema, planes, semillas, métricas, hash de escenario y archivos laterales para reproducir la decisión.

## Optimizar un programa de proyecto

1. Diagnostique la demanda, ventanas e intensidad. Si hay una contradicción demostrada, corrija los datos con autorización del usuario antes de gastar CPU. Una advertencia por sí sola no autoriza a cambiar alcance, duración o recursos.
2. Compare al menos tres alternativas con el mismo escenario. Use el plan de tres variantes de la referencia, guardado como `../trabajos/mi_cli/plan.json`.
3. Examine factibilidad, verificación y criterios de negocio antes de escoger. La primera fila no es necesariamente aplicable si no hay factibles.

```bash
python -m ganttier.cli -p "TST proyecto" programa diagnosticar
python -m ganttier.cli -p "TST proyecto" programa comparar --plan ../trabajos/mi_cli/plan.json --paralelo 2 --tiempo 1 --mejor-a ../trabajos/mi_cli/mejor.json --resultados-a ../trabajos/mi_cli/comparacion.jsonl
python -m ganttier.cli -p "TST proyecto" programa aplicar --resultado ../trabajos/mi_cli/mejor.json
```

El último comando es una simulación. Exija `feasible=true`, verificación sin violaciones y una corrida conservada antes de proponer la aplicación real. Si `describir` no anuncia un comando necesario, informe «comando no implementado todavía»; no improvise SQL para sustituirlo.

## Nivelar carga en mantenimiento

Revise maestras, equipos, turnos, cantidades y horizonte. El rendimiento es por equipo completo; las HH no se multiplican otra vez por dotación. `batch_continuous_in_shift=false` permite continuar en el siguiente turno del mismo equipo; no autoriza trabajo durante descansos.

```bash
python -m ganttier.cli -p "TST equipos" equipos listar
python -m ganttier.cli -p "TST equipos" periodicas listar
python -m ganttier.cli -p "TST equipos" programa diagnosticar
python -m ganttier.cli -p "TST equipos" programa resolver --objetivo nivelar --metodo heuristic --tiempo 2 --semilla 7 --desde 2027-01-01 --hasta 2027-01-31 --sin-horas-extra
python -m ganttier.cli -p "TST equipos" programa carga --agrupar recurso --periodo semana
```

La carga consultada al final es la del programa vigente, no la simulada. Para decidir una aplicación, prepare un plan de al menos tres variantes de mantenimiento con `horizon_start`, `horizon_end` y semillas explícitas, compárelo y conserve el resultado. No reutilice el plan de proyecto sin cambiar su `project`. No use `--regenerar` sin entender qué ocurrencias se reemplazan y cuáles tienen avance protegido.

## Barrido de parámetros con presupuesto de tiempo

Guarde `barrido.json`:

```json
{
  "project":"TST proyecto",
  "defaults":{"options":{"method":"heuristic","time_limit_s":1,"allow_overtime":false}},
  "grid":{"weights.level":[0,0.5,1],"options.seed":[0,1,2]},
  "rank_by":"lexicographic",
  "parallel":2,
  "variant_timeout_s":45,
  "budget_s":120
}
```

```bash
python -m ganttier.cli -p "TST proyecto" programa lote --plan ../trabajos/mi_cli/barrido.json --paralelo 2 --presupuesto 120 --resultados-a ../trabajos/mi_cli/lote.jsonl --top 3
python -m ganttier.cli -p "TST proyecto" programa lote --plan ../trabajos/mi_cli/barrido.json --paralelo 2 --presupuesto 120 --resultados-a ../trabajos/mi_cli/lote.jsonl --top 3 --reanudar
```

Este grid tiene nueve variantes. Para presupuestar, mida primero una variante representativa y estime `N × tiempo_observado / paralelo`, añadiendo margen por inicio de procesos, SQLite, informes y variabilidad. `--tiempo` limita el motor; el tope duro puede llegar a `1.5 × tiempo + 30` segundos por variante. El presupuesto total debe dejar margen para guardar parciales y cerrar procesos.

En una máquina compartida use uno o dos procesos. No ejecute dos lotes simultáneos con dos procesos cada uno. Los hilos internos de OR-Tools pueden aumentar la carga: revise las opciones publicadas, la advertencia de sobresuscripción y `host_load` si está disponible. No compare velocidad de corridas bajo cargas distintas como si fuera una propiedad del algoritmo.

Exit 4 conserva lo terminado; revise JSONL antes de reanudar. Use el mismo plan y escenario. Los IDs `ok`/`infactible` se omiten; los timeout pueden reintentarse. No edite IDs manualmente para fingir continuidad ni mezcle resultados de escenarios diferentes.

## Aplicar el mejor resultado con respaldo

Use `mejor.json` generado por comparar, no el sobre de stdout guardado a ciegas. Presente la mejora y sus costos: plazo, ventanas, carga y horas extra. Cuando aplicar esté autorizado, ejecute:

```bash
python -m ganttier.cli -p "TST proyecto" --escribir programa aplicar --resultado ../trabajos/mi_cli/mejor.json --tolerancia 0
python -m ganttier.cli -p "TST proyecto" programa verificar
python -m ganttier.cli base respaldos
```

Exija respaldo existente y exit 0 del verificador. Con exit 3 por `escenario_cambio`, vuelva a diagnosticar y comparar sobre el estado actual: no sustituya el hash. Con base ocupada, coordine la escritura; no fuerce ni borre locks automáticamente. En mantenimiento se puede resolver de nuevo para reconstruir IDs, conservando parámetros y tolerancia; revise las métricas realmente aplicadas.

## Registrar avance y proyectar la curva S

Sobre un programa aplicado y verificado, capture la línea base antes del avance. Estos datos son solo un ejemplo de prueba; use mediciones reales en operación. Guarde `avance.json` como `{"codigo":"T1","fecha":"2027-01-05","porcentaje":50,"hh_reales":8}`.

```bash
python -m ganttier.cli -p "TST proyecto" --escribir control linea-base crear
python -m ganttier.cli -p "TST proyecto" --escribir control fecha-corte fijar 2027-01-05
python -m ganttier.cli -p "TST proyecto" control avance registrar --archivo ../trabajos/mi_cli/avance.json
python -m ganttier.cli -p "TST proyecto" --escribir control avance registrar --archivo ../trabajos/mi_cli/avance.json
python -m ganttier.cli -p "TST proyecto" control avance listar
python -m ganttier.cli -p "TST proyecto" control curva-s --base horas --periodo semana
python -m ganttier.cli -p "TST proyecto" control evm
python -m ganttier.cli -p "TST proyecto" control pronosticos
python -m ganttier.cli -p "TST proyecto" --escribir informes generar --tipo mensual --inicio 2027-01-01 --formatos html --a ../trabajos/mi_cli/informes
```

La primera carga de avance es una simulación; confirme el contenido antes de persistir. Tras una respuesta ambigua consulte el historial antes de repetir: duplicar HH cambia los indicadores. El corte incluye todo ese día. Una curva vacía o indicadores nulos requieren revisar línea base, presupuesto y fechas; no convierta automáticamente nulos en cero. Los informes simulados son efímeros; para entregar archivos persistentes, la receta usa escritura con respaldo.

## Interpretar métricas e infactibilidad

`makespan_days` mide plazo; `load_std` dispersión de carga; `peak_load` pico; `overload_hours` exceso sobre capacidad; `overtime_hours` y `overtime_cost` uso/costo extra; `activities_out_of_window` cuenta actividades y `days_out_of_window` días fuera de ventana. `personnel_cost=null` significa que no se calculó o no aplica, no costo cero. Compare mismos calendarios, horizonte, escenario y criterio.

`infeasibility=demostrada` exige evidencia: por ejemplo demanda imposible o contradicción de ventanas. `no_encontrada` significa que la búsqueda no obtuvo una solución; aumente presupuesto o pruebe método/semillas sin declarar imposible el proyecto. `status=timeout` o exit 4 nunca demuestra infactibilidad. `feasible=true` tampoco demuestra óptimo global. Con exit 2 y `ok=true`, el comando funcionó y produjo un resultado negativo legítimo.

## Errores que deben evitarse

- No use `--escribir` para aplicar una optimización sin haber comparado alternativas. Las ediciones y avances requieren revisar sus propios datos; no necesitan inventar una comparación de solver.
- No pruebe con `datos/ganttier.db`, no copie archivos WAL a mano y no use SQL directo para saltarse validaciones.
- No coloque globales después del grupo ni construya JSON por interpolación insegura de shell. Prefiera archivos UTF-8 y `--archivo` para entradas complejas.
- No use IDs obtenidos en una simulación como si fueran persistentes; vuelva a listar tras escribir.
- No borre los archivos laterales de la ganadora; sin ellos puede quedar solo una fila de métricas no aplicable.
- No trate exit 5 como un resultado de optimización ni lo reintente indefinidamente. Conserve argv, sobre, stderr y versión; comunique el módulo responsable.
- No confunda el total de capacidad por habilidades con personas físicas distintas ni tome indicadores de calidad como una certificación.

La evidencia de ejecución y las limitaciones detectadas durante esta integración están en el [informe TST](../trabajos/cli_codex_20261019/agentes/TST/INFORME.md). Los comandos de estas recetas son del contrato; su acreditación depende de los resultados allí registrados.
