# Q1 — Actividades y Gantt de escritorio

Implementación del encargo `_encargos/GUI_Q1.md`, 26-09-2026. Se conserva el
contrato REST y no se modifican los archivos compartidos `app.py`, `client.py`,
`state.py`, `theme.py`, `widgets/common.py` ni el backend de dominio/API.

## Archivos

| Archivo relativo a `backend/` | Responsabilidad |
| --- | --- |
| `ganttier/gui/gantt/model.py` | Arreglos NumPy, bosque WBS iterativo, filtros, orden y relaciones indexadas |
| `ganttier/gui/gantt/table.py` | Modelo Qt virtual, formato chileno, validación y editor de fechas |
| `ganttier/gui/gantt/view.py` | Tabla y lienzo QPainter, desplazamientos, escalas, selección y arrastres |
| `ganttier/gui/gantt/profile_model.py` | Validación numérica y búsqueda de períodos de carga |
| `ganttier/gui/gantt/profile.py` | Perfil de recursos, selección de series y carga asíncrona |
| `ganttier/gui/gantt/editing.py` | Validación y reglas de calendario, mes macro, ventana y advertencias |
| `ganttier/gui/gantt/dialogs.py` | Formularios de actividad, relaciones, asignaciones y administración WBS |
| `ganttier/gui/pages/activities.py` | `ActivitiesPage`, acciones, navegación y coordinación de tareas |
| `tests/gui/test_activities.py` | Pruebas puras y de integración Qt/API embebida |

## Datos y rendimiento

- Las fechas son días NumPy desde 1970-01-01. `NaN` representa una fecha ausente.
  El término es inclusivo; se suma un día solo para calcular el borde gráfico.
- No se crean objetos Qt ni diccionarios por actividad para la tabla. Esta lee
  directamente los arreglos por medio de `ActivityTableModel`.
- Las filas negativas identifican encabezados WBS y las positivas índices de
  actividad. Los agregados incluyen descendientes y ponderan el avance por HH,
  usando duración cuando no hay HH positivas.
- El recorrido WBS usa pilas, tolera padres ausentes y rompe ciclos de forma
  determinista. Contraer no elimina los datos del grupo. La búsqueda consulta
  código, nombre, habilidad e instalación; puede combinarse con críticas.
- Las relaciones se ordenan por fila predecesora y sucesora. Un índice CSR y
  búsquedas binarias evitan recorrer relaciones fuera del área vertical visible,
  incluso cuando una sola actividad tiene miles de sucesoras.
- El pintado recorre únicamente filas, días y períodos en el área visible. La
  tabla mantiene su desplazamiento interno oculto y comparte su valor en píxeles
  con la única barra vertical visible. Tabla y lienzo usan filas de 26 px,
  encabezados de 44 px y barras horizontales alineadas.
- La construcción de arreglos, filtros e índices se ejecuta mediante `run_task`.
  Las consultas del calendario se limitan al área horizontal visible con margen.

## Interacción

El Gantt ofrece Día/Semana/Mes/Trimestre/Año, Ctrl+rueda con anclaje al cursor,
Shift+rueda horizontal, encabezado de dos niveles, fines de semana y feriados,
barras vigentes y críticas, progreso, línea base, ventanas translúcidas, hitos,
desconexiones, relaciones FS/SS/FF/SF, líneas de hoy y corte, y tooltips.

Arrastrar el cuerpo mueve; el borde derecho modifica la duración; el círculo del
extremo permite crear una relación FS. Escape cancela el arrastre. La selección
se comparte con la tabla y puede recorrerse con flechas. Los nodos WBS se
contraen/expanden por clic o Enter. Vista permite gestionar capas, columnas,
orden, agrupación, expansión completa y recálculo CPM.

La tabla edita nombre, duración, inicio, avance, HH y mes macro. Enter abre el
detalle, Supr elimina con confirmación y F9 programa. Los formularios emplean
`FormDialog`/`Field`; los campos calculados e identificadores del detalle se
muestran como lectura. Relaciones y asignaciones tienen pestañas con alta,
edición y baja. WBS permite crear, renombrar, cambiar padre/orden y eliminar;
se excluyen descendientes del selector de padre.

El perfil inferior se puede redimensionar con el divisor vertical. Comparte
exactamente el origen, zoom y desplazamiento del Gantt, sin un segundo control
horizontal. Presenta carga apilada por habilidad o recurso, capacidad seleccionada
discontinua y exceso rojo. Usa `day` desde 4 px/día y `week` por debajo de ese
umbral. Las casillas conservan elecciones por proyecto y agrupación. Los cambios
de período descartan el perfil anterior mientras llega la nueva respuesta.

## Escrituras y coherencia

- Todas las llamadas de la GUI a la API se ejecutan dentro de `run_task`.
  Las escrituras se serializan; las respuestas de carga llevan versiones y
  verifican la identidad del proyecto antes de aplicarse.
- Tras cada escritura se emite `bump_schedule()`. Si la escritura se guardó pero
  CPM falló, se recargan los datos guardados y se informa explícitamente el fallo
  de CPM, sin presentar la operación completa como no guardada.
- En proyecto, mover/iniciar impone `SNET`; CPM se recalcula cuando la opción
  está habilitada. En mantenimiento, inicio y término se obtienen de días
  laborables del calendario de actividad o proyecto, dentro del horizonte.
  Redimensionar cuenta días laborables inclusivos. Las actividades bloqueadas
  rechazan movimientos y cambios de duración desde la tabla/lienzo.
- El avance de tabla y detalle usa `POST /progress` a la fecha de corte, o al día
  actual cuando no hay corte, con HH/costo del registro en cero. No se escribe
  `percent_complete` directamente como sustituto de su historial.
- El detalle envía solo campos modificados. Si cambia a un mes macro no vacío,
  omite las fechas de ventana para que el backend las derive; si cambian las
  fechas de ventana sin un nuevo mes, envía `macro_month=null`.
- Crear línea base también la activa y recarga las columnas de comparación.
- Generar programa intenta `/projects/{pid}/schedule/solve`. Solo ante 404/405
  usa CPM e informa esa alternativa; 422, 500 y errores de conexión se propagan.
  La API inspeccionada no contiene `solve`: la alternativa no genera ocurrencias
  periódicas. El aviso remite a Plan de mantenimiento para hacerlo.
- El API no admite `PUT /relations/{id}`. Editar una relación requiere eliminarla
  y crear la nueva; si falla el alta se intenta restaurar la anterior. Esto no es
  una transacción del servidor y la relación restaurada recibe otro ID. El
  diálogo recarga sus relaciones también en caso de error. Si falla además la
  restauración, el error conserva los datos originales para recuperarla.
- Las advertencias de programación se agrupan con conteos y conservan todos los
  ejemplos y la respuesta completa en el diálogo de resumen.

## Verificación realizada

En el entorno disponible, Python 3.10, sin PySide6, pytest-qt ni FastAPI:

- **10 pruebas aprobadas; 7 omitidas por falta de Qt.**
- Análisis de sintaxis de todos los módulos Q1 y su archivo de pruebas correcto.
- Construcción medida de **100.000 actividades + 100 WBS + 99.999 relaciones:
  0,187 s**, incluido el índice de relaciones. La prueba automatizada exige <1 s.
- La instalación de dependencias se intentó en una ubicación temporal dentro
  del módulo; falló por acceso de red bloqueado (`WinError 10013`).

Las pruebas puras cubren fechas ausentes y fin inclusivo, longitudes e IDs
inválidos, WBS con 1.500 niveles/ciclos/huérfanos, agregados, colapso, filtros,
orden, relaciones de alto grado, presupuesto de tiempo, mes/ventana,
validaciones, calendarios y validación del perfil de carga.

Las siete pruebas Qt usan `create_window()` y el servidor real, con BD, datos y
QSettings aislados bajo `tmp_path_factory`. Crean las demos de proyecto y de
**20.000** actividades de mantenimiento. Cubren CPM, edición en línea y avance
histórico, alta/baja, relaciones y su restauración, WBS, detalle, asignaciones,
línea base, cambio de tema, zoom/Ctrl+rueda, desplazamiento común, alineación del
perfil, cambio día/semana, bloqueo, calendario, arrastres y respuestas tardías.

La prueba de rendimiento gráfico renderiza **el widget completo, tabla y
lienzo**, en un `QImage` offscreen, tras calentar la caché de fuentes. Exige un
cuadro <50 ms y menos de 30 filas pintadas para 100.000 actividades. **Ese tiempo
no pudo medirse aquí**, pues la prueba requiere Qt.

## Ejecución pendiente en un entorno con Qt

Desde `Ganttier/backend`, con PySide6, pytest-qt y las dependencias del proyecto
ya disponibles en el intérprete:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = (New-Item -ItemType Directory -Force ganttier/gui/gantt/.validation/tmp).FullName
$env:TMP = $env:TEMP
python -B -m pytest tests/gui/test_activities.py -q -p no:cacheprovider --basetemp=ganttier/gui/gantt/.validation/pytest
```

Los puntos que aún requieren comprobación práctica son las siete pruebas
Qt/API, el umbral de 50 ms, el aspecto con escalado de pantalla de Windows y
los arrastres con ratón real. No se afirma que esas comprobaciones hayan pasado.
