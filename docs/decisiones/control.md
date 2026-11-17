# A4 — Control de avance

Implementa [SPEC §8](../../SPEC.md) y [encargo A4](../../_encargos/A4.md).
No modifica `domain.py`, almacenamiento, calendarios ni otros módulos. Dependencia:
NumPy, ya incluida en el contrato; pytest para pruebas. No necesita red.

## Interfaz pública

Importar desde `ganttier.control`:

```python
scurve(acts: list[ActivityView], baseline: dict[int, BaselineActivity] | None,
       progress: list[ProgressEntry], calendar: WorkCalendar, data_date: date,
       period: str = "week", basis: str = "hours") -> SCurve
evm(acts: list[ActivityView], baseline: dict[int, BaselineActivity] | None,
    progress: list[ProgressEntry], calendar: WorkCalendar, data_date: date,
    basis: str = "hours") -> EVMResult
worker_performance(acts: list[ActivityView], assignments: list[Assignment],
                   progress: list[ProgressEntry], resources: list[Resource],
                   period_start: date, period_end: date,
                   data_date: date) -> list[WorkerKPI]
```

`WorkCalendar` se tipa mediante un protocolo estructural local: solo se consumen
`is_workday` y `next_workday`, ambos de SPEC §5. No depende de que A1 esté instalado
al importar el módulo. Se aplica el calendario recibido a todas las actividades;
la firma no permite resolver calendarios distintos por `calendar_id`.

## Presupuesto, avance y acumulados

- Peso `w_i`: HH (duración si HH = 0), costo o 1 según `basis`. La línea base aporta
  fechas, duración y presupuesto; `BaselineActivity.cost` equivale a `cost_budget`.
  Una línea base parcial se completa por actividad con el programa vigente. Sus
  actividades ausentes de `acts` no se incorporan. `BAC = Σw_i`.
- `PV(t) = Σw_i × días laborables transcurridos / días laborables totales`, con
  intervalos inclusivos. Cada día laborable recibe igual peso, aunque varíen sus
  horas. Los hitos se ganan íntegramente en `finish`. Un intervalo sin ningún día
  laborable se representa como un punto en `finish`, conservando BAC.
- `EV(t) = Σw_i × p_i(t)/100`. Historial por **escalones**: cero antes del primer
  registro, luego el último porcentaje conocido. Para varios registros de una
  actividad/fecha prevalece el mayor `id`; a igual `id`, el último de la lista.
  Las correcciones descendentes se conservan. Exactamente en `data_date` prevalece
  `ActivityView.percent_complete`, aun cuando discrepe del historial o no lo haya.
- `AC(t)` suma todos los consumos **incrementales** registrados hasta t, incluidos
  los de varios trabajadores en una misma fecha. Usa `actual_hours` o `actual_cost`;
  no suma nuevamente los totales de `ActivityView` ni de `Assignment`. Registros
  posteriores al corte o de actividades ajenas se excluyen.
- `count` no tiene una unidad de consumo real en el modelo: AC = 0 y CPI/eficiencia
  de consumo quedan indefinidos; los campos derivados de AC siguen las fórmulas,
  pero no deben interpretarse como indicadores financieros.
- `*_abs` son **acumulados** en unidades de la base; `*_cum = 100 × *_abs / BAC`.
  Si BAC = 0, los porcentajes son 0 y los cocientes con denominador cero, `None`.
  Se rechazan presupuestos/consumos negativos o no finitos, avances fuera de
  [0,100], identificadores de actividad duplicados y fechas invertidas. Las
  actividades con peso positivo necesitan fechas planificadas; proyectar su
  remanente también requiere fechas vigentes. Los errores son `ValueError` en español.

## EVM y Earned Schedule

Convenciones de valor ganado de estilo PMI/ANSI-EIA-748:

```text
SV = EV − PV                 CV = EV − AC
SPI = EV / PV                CPI = EV / AC
EAC = BAC / CPI              ETC = EAC − AC
VAC = BAC − EAC              TCPI = (BAC − EV) / (BAC − AC)
```

Todo cociente de denominador cero devuelve `None`. Si CPI no existe **o es cero**,
`EAC = AC + (BAC − EV)`: se estima el remanente al presupuesto original, sin dividir
por cero. Por ejemplo, sin consumos ni avance, EAC = BAC. Un BAC ya totalmente
ganado sin consumos registrados produce EAC = 0; no se inventan consumos.

Earned Schedule de Lipke: se invierte PV diario para obtener `ES`, interpolando
linealmente dentro del día en que alcanza EV. En una meseta se usa el primer instante
en que se alcanzó ese EV. La escala es de **días corridos**, con origen al comienzo
del primer día planificado: `AT = max(0, corte − inicio + 1)` y
`PD = término planificado − inicio + 1`. Así, un proyecto al día obtiene SPI(t) = 1
al cierre del primer día, sin una división artificial por cero.

```text
SPI(t) = ES / AT
IEAC(t) = PD / SPI(t)
forecast_finish = inicio + ceil(IEAC(t)) − 1 día
```

Se redondea hacia arriba al día de término; no se predice un término anterior al
corte. EV = 0 implica ES = 0 y fecha indefinida; AT = 0 implica SPI(t) indefinido.
BAC = 0 implica ES/SPI(t)/fecha indefinidos. Si EV = BAC, ES = PD y la fórmula sitúa
el término en el corte. Pronósticos fuera del rango de `datetime.date` devuelven
`None`, sin desbordar ni construir horizontes de millones de años.

## Proyección y períodos: resolución de la ambigüedad del encargo

El encargo pide llegar a 100% en `forecast_finish`, pero también prescribe por defecto
el programa vigente y reservar ese campo para la fecha por SPI(t). Ambas condiciones
son incompatibles cuando las fechas difieren. Se conserva la regla explícita por
defecto: **la serie proyecta el programa vigente; el campo informa Earned Schedule**.
`planned_finish` informa el término de línea base (o vigente si no hay línea base).
No se cambian firmas ni dataclasses para introducir una opción de escalamiento.

La proyección diaria parte en EV al corte y distribuye por actividad
`w_i × (1 − p_i/100)` desde `max(start, corte + 1)` hasta su término vigente.
Si el remanente ya venció, se concentra en el primer día laborable posterior al
corte: tratamiento conservador de datos sin reprogramación, no un nuevo motor de
programación. La serie alcanza BAC al concluir ese remanente y no se estira
silenciosamente hasta la fecha de Earned Schedule. **Pendiente contractual:** una
opción para escalar al pronóstico ES necesita ampliar la firma o acordar otra política.

Los períodos son cierres diarios, domingos y fines de mes. El horizonte cubre
plan, historial, corte y remanente vigente; el último cierre puede ser posterior
al último día de actividad. Se muestrea el acumulado al cierre, no se suman acumulados.
EV/AC son `None` para etiquetas posteriores al corte; forecast es `None` antes de él.
Si el corte cae dentro de una semana/mes, no se inventa una etiqueta intermedia:
el ancla EV existe en la curva diaria y en EVM; el primer punto semanal/mensual de
forecast refleja el avance proyectado hasta ese cierre. Sin actividades, las listas
de SCurve están vacías.

## Desempeño por trabajador

- La firma no recibe línea base ni calendario: usa `ActivityView.start/finish` y
  distribuye HH uniformemente en **días corridos inclusivos**. No supone lunes a
  viernes ni consulta globales. El detalle laboral exacto requiere ampliar el contrato.
- Devuelve todos los `Resource.kind == "trabajador"`, en orden de entrada, incluidos
  inactivos con historial y trabajadores sin asignaciones. Excluye equipos/materiales.
  Asignaciones y registros ajenos a `acts` se ignoran; recursos desconocidos no
  generan filas. `resource_ids` no sustituye asignaciones ni registros.
- Unión de pares actividad/trabajador por asignaciones o por progreso hasta el
  corte. Asignaciones repetidas se suman, pero cada actividad se cuenta una sola vez.
  HH asignadas: `Assignment.work_hours`, o, si son cero,
  `units × duration_days × Resource.capacity_hours_day` (8 HH/día para demanda
  genérica o recurso no disponible).
- Share = HH asignadas / HH asignadas totales de la actividad, incluidas las
  asignaciones genéricas y otros tipos de recurso. Si todas son cero, se usan sus
  unidades relativas; si también son cero, no se atribuye EV. Un trabajador que
  solo registra consumos no duplica EV ya asignado. Sin asignaciones, se divide EV
  igualmente entre los trabajadores que registraron progreso hasta el corte; sus
  HH planificadas son cero porque no existe asignación presupuestada.
- `planned_hours`: HH de asignaciones multiplicadas por fracción de solapamiento
  con el período completo. `earned_hours = Σwork_hours_i × share_i ×
  [p_i(min(period_end, corte)) − p_i(period_start − 1)]/100`. Son HH presupuestadas
  reales de la actividad, sin el sustituto de duración que admite la curva S.
  Correcciones negativas pueden producir HH ganadas negativas. Un período futuro
  tiene EV/AC del período cero.
- `actual_hours`: consumos del trabajador en el período, truncados al corte.
  `hh_efficiency = CPI = earned_hours / actual_hours`.
  `SPI = earned_hours / HH planificadas desde period_start hasta min(period_end,corte)`.
  Esta truncación evita comparar EV parcial con trabajo todavía futuro.
- `assigned_activities`, `completed_activities` y `pending_activities` reflejan el
  conjunto vinculado **al corte**, no solo altas/bajas durante el período.
  Completa = porcentaje vigente 100% o `actual_finish <= corte`.
  `due_in_period` incluye las actividades cuyo término vigente cae en el período;
  `completed_on_time` es un subconjunto de ellas con `actual_finish <= finish` y
  `actual_finish <= corte`. Sin fecha real no se inventa cumplimiento a tiempo.
  `compliance_pct = 100 × completed_on_time / due_in_period`.
- Atraso medio: todas las actividades de ese trabajador vencidas al corte cuyo
  término cae en el período, incluidas tempranas y puntuales. Se promedia
  `(actual_finish conocido al corte, o corte) − finish`; el valor puede ser negativo.

## Verificación y rendimiento

`tests/control/` contiene FakeCalendar con toda la interfaz SPEC §5 y casos manuales
de una/dos actividades, bases HH/costo/conteo, línea base parcial, adelanto/atraso,
hitos, feriados, períodos, historia duplicada/corregida, asignación compartida,
entradas vacías/incorrectas, precisión fraccionaria y comparación con un cálculo
escalar independiente. No importa calendarios, BD ni servicios de otros agentes.

```powershell
cd Ganttier/backend
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest tests/control -q -s -W error -p no:cacheprovider
```

Resultado local: **38 pruebas aprobadas**. En la ejecución de referencia con Python
3.10.6 / NumPy 2.2.6: `scurve` de 100.000 actividades con historial, **0,170 s**;
`worker_performance` de 100.000 actividades/asignaciones/registros y 100 trabajadores,
**0,268 s**. Ambas pruebas exigen < 5 s por llamada, excluyendo la creación del conjunto.
Los tiempos varían con la carga concurrente y el equipo.

Distribución por diferencias de tasas y `bincount`: memoria O(N + P + D), sin matriz
N×D. Ordenar historial cuesta O(P log P); las agregaciones y el muestreo diario son
vectorizados. Preparar dataclasses usa recorridos lineales. No modifica los objetos
de entrada. La validación de integración con servicios/calendarios reales corresponde
al ensamblaje de A1; las limitaciones de contrato arriba están explícitas.
