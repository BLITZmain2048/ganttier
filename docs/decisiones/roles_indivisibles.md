# Roles indivisibles en proyectos

Fecha: 2026-10-05. Estado: integrado en API y GUI (selector, asistente y vista nominal).

El reparto de HH por habilidad del motor habitual no selecciona personas distintas para los
roles de una actividad. En el caso de dos estaciones de prueba exige a Ana más de un bloque
simultáneo para C e I aunque Elena puede cubrir I. Se añade un contrato explícito de roles
enteros, manteniendo `role_mode="fractional"` como valor predeterminado.

## Uso desde plantilla y API

Importar una plantilla de Proyecto mediante `POST /api/import/new-project?mode=proyecto`.
Cada fila de Asignaciones solicita `Habilidad`, `Unidades` enteras y HH iguales a
`duración × block_hours × unidades` (o HH=0). Una fila con Recurso fija esa persona o equipo.
No es necesario proporcionar personas para las filas genéricas ni fechas planificadas.

Invocar `POST /api/projects/{pid}/schedule/solve`:

```json
{
  "apply": true,
  "options": {
    "role_mode": "indivisible",
    "block_hours": 4,
    "horizon_blocks": 60,
    "target_code": "A27",
    "allow_overtime": false,
    "respect_resource_limits": true,
    "time_limit_s": 120,
    "seed": 0
  }
}
```

`OptimizeOptions` es el contrato compartido del dominio y de los esquemas Pydantic.
El método debe ser `auto` o `cpsat`; los pesos habituales no intervienen en este objetivo fijo.
`schedule/optimize` también deriva al mismo servicio al seleccionar este modo.
`schedule/solve-blocks` conserva el contrato de previsualización del parche inicial;
guarda una corrida sin aplicar, reutilizando el mismo servicio.

La respuesta incluye `starts`, `finishes`, `blocks`, `role_assignment`, `verification`,
`primary_finish_block`, `personnel_cost`, cotas y estados de cada etapa. Con `apply=false`
solo se registra una corrida revisable; puede aplicarse por
`POST /api/projects/{pid}/schedule/runs/{run_id}/apply`.

## Eje temporal y alcance

Un día sintético representa un bloque equivalente. No representa cuatro horas consecutivas
de un calendario civil: el día siguiente representa el bloque siguiente. Las duraciones son
enteras; el intervalo de bloques es `[start, finish)`. Las fechas exportadas de actividades
con duración positiva tienen fin inclusivo. Un hito tiene inicio=fin y puede ubicarse en H.
El JSON declara `time_axis.encoding="synthetic_day_per_block"`.

El horizonte es duro: se usa `horizon_blocks`, el `horizon_end` superior de SolveRequest o,
si faltan ambos, la fecha de fin del proyecto. Dos límites explícitos incompatibles se rechazan.
No se usa la extensión automática del horizonte del motor fraccionario.

Se admiten proyectos de hasta 300 actividades y 2000 bloques, relaciones FS con desfase,
ventanas duras, bloqueos y restricciones ASAP/MSO/MFO/SNET/FNET. SNLT/FNLT conservan su
semántica habitual de advertencia; no pertenecen al objetivo lexicográfico. Las ventanas
blandas tampoco añaden un tercer objetivo. Sin `target_code` se minimiza el fin de todas
las actividades. Se rechazan avance, duración remanente, jornadas parciales, recursos de
tipo cuadrilla y trabajo que no sea de dedicación completa. OR-Tools es necesario.

Los calendarios de actividad deben ser continuos de `block_hours`. Cada trabajador tiene
una unidad y capacidad de un bloque; su calendario admite bloques completos o disponibilidad
cero. Los equipos tienen capacidad entera y calendario equivalente. Las ausencias se cargan
con `hours=0`; los extremos de la API de ausencias son inclusivos.

Una celda vacía de país de feriados en la plantilla hereda CL: para un calendario abstracto,
actualizar explícitamente `holidays_country=null` por API antes de crear ausencias. El modo
no elimina feriados automáticamente. El demostrador importa el libro original y hace esta
corrección explícita, además de cargar las indisponibilidades del JSON original.

## Modelo y certificación

Se crea una variable binaria por demanda/persona elegible. La suma cubre exactamente las
unidades del rol y cada persona participa como máximo en un rol de la actividad. Los intervalos
opcionales conservan a la misma persona durante toda la tarea. `NoOverlap` impide solapes y
ausencias; `Cumulative` controla equipos con múltiples unidades.

Primero se minimiza el fin del objetivo. Solo si CP-SAT prueba ese óptimo se fija y se minimiza
el costo normal del personal. Las tarifas se calculan con Decimal y se representan exactamente
en centavos; se rechaza precisión incompatible. Se excluyen equipos, horas extra y presupuestos
de actividad del costo objetivo. Solo dos estados OPTIMAL permiten declarar `status="optimo"`.
Una búsqueda incompleta puede entregar una solución factible verificada; UNKNOWN no demuestra
infactibilidad. No se fija el valor 36 ni se carga personal del testigo externo.

`engine/verify.py::verify_role_schedule` reconstruye las demandas nominales desde las demandas
originales y comprueba conteos, referencias, habilidad y personas distintas. Luego usa el auditor
independiente existente para duración, capacidad, ausencias, precedencias, ventanas y bloqueos,
complementado con límites de bloques. No importa CP-SAT, Loads ni el optimizador. Se ejecuta al
resolver y nuevamente antes de aplicar una corrida guardada. No se confía únicamente en el
indicador `feasible` del archivo. La aplicación de fechas y personas es transaccional.

## Persistencia y exportación

La migración incremental v3 añade `role_schedules` y `role_allocations`; sigue la cadena de
`migrations.py`, con respaldo previo para bases existentes. Las filas originales de `assignments`
conservan la demanda genérica para futuras optimizaciones. La asignación nominal almacena
actividad, fila de origen, persona y habilidad, junto con corrida, opciones, eje temporal y
huella del escenario. Los IDs negativos de origen representan demandas derivadas de la ficha
de actividad cuando no había filas de Asignaciones.

`GET /api/projects/{pid}/schedule/role-assignments` expone la asignación aplicada y `valid`.
Una edición del escenario la vuelve obsoleta; aplicar otro tipo de corrida la retira.
`GET /api/projects/{pid}/export.xlsx?nominal=true` exporta las personas elegidas y equipos,
y rechaza asignaciones obsoletas. El libro es reimportable como un programa con personal fijo.
La exportación habitual conserva las demandas originales y sus fechas vigentes.

## Validación y pendientes

Las pruebas nuevas en `backend/tests/solver` importan el caso original, resuelven por API,
exigen A27=36/costo=10420 con cotas iguales, verifican el JSON original, aplican, consultan la
persistencia y exportan. Incluyen ausencias, permanencia, roles distintos, equipos compartidos,
hito en H, precedencia con desfase, bloqueos, prioridad lexicográfica, alteración de corridas y
validación de opciones. La comparación completa antes/después y sus registros están en
`trabajos/ganttier_ejemplos_20261005/proyecto/APLICACION_PARCHE.md` de la carpeta compartida.

La continuación del 5 de octubre instaló los diez archivos del motor/API/persistencia tras
verificar los 293 hashes del respaldo. Los 33 casos nuevos incluyen la migración real v2→v3,
su rollback transaccional y la equivalencia de fechas entre omitir `role_mode` y pedir
`fractional`. La demostración con la API ASGI y SQLite persistió 81 asignaciones, obtuvo
A27=36 y costo=10420 con ambas cotas óptimas y reimportó el Excel nominal con ese resultado.
Las suites se ejecutan por directorio con límite de 15 minutos; Excel y slow tienen
procesos separados y límite de 20 minutos. El informe de aplicación distingue las pruebas
completadas, los fallos preexistentes y los casos interrumpidos o no ejecutados.

El selector del modo, el asistente y el panel nominal de la GUI están integrados; véase [mejoras_20261005.md](mejoras_20261005.md). Los perfiles de carga y los informes
generales siguen leyendo las demandas originales; para consultar el personal elegido se debe
usar el panel nominal de Generar programa, el endpoint nominal o el Excel nominal. No se amplía este contrato a mantenimiento,
calendarios civiles por turnos, trabajo parcial, avance ni relaciones distintas de FS.
Duplicar un proyecto conserva demandas y fechas, pero requiere volver a resolver para guardar
una nueva asignación nominal; las líneas base existentes no capturan esta tabla de personal.

Para revertir el código se dispone del respaldo íntegro con SHA-256. Una base migrada a v3
requiere restaurar su respaldo previo si se usa una versión antigua; no se baja el esquema.
