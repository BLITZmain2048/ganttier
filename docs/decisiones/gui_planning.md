# Q2 — Programación macro, optimización, recursos y calendarios

Entrega del encargo `Ganttier/_encargos/GUI_Q2.md`. Cambios limitados a las cuatro
páginas asignadas, `backend/tests/gui/test_planning_pages.py` y este documento.
No se modificaron el cliente, estado, tema, ventana, componentes comunes ni motores.

## Pantallas

- `pages/macro.py`: `MacroPage` y `MacroMatrix`. Matriz `QAbstractScrollArea` con
  `QPainter`, filas y meses virtualizados, encabezado y pie fijos, desplazamiento
  horizontal, clic/arrastre, Shift/Ctrl, doble clic, teclado (flechas, Enter,
  Supr, Ctrl+A), búsqueda y filtro sin ventana. Mes único y rangos guardan los
  cuatro campos macro; las fechas programadas se muestran como barras interiores.
  Conserva pendientes por proyecto durante la navegación. PATCH secuencial de hasta
  5.000 actividades: retira solo los lotes confirmados; ante fallo conserva los
  restantes. Descartar recupera lo persistido. Generar guarda primero y aplica
  pesos `level=.25, makespan=.05, window=.70, cost=0`.
- `pages/optimize.py`: `OptimizePage`, cuatro pesos normalizados, siete preajustes,
  opciones de método, capacidad, horas extra, agrupación y mantenimiento. Generar,
  simular, optimizar/aplicar y aplicar una corrida desde la selección o su celda.
  Ocho KPI con valor anterior y delta coloreado, dos histogramas MplCanvas con
  barras apiladas y capacidad, agrupación de advertencias e informe de violaciones.
- `pages/resources.py`: `ResourcesPage`, tabla ordenable y filtros de texto/habilidad,
  FormDialog de alta/edición con tipo, habilidades, calendario, capacidad, unidades,
  costos, extra, correo, ámbito y activo. El alta usa el proyecto actual por defecto.
  Eliminación confirmada y capacidad normal por habilidad de recursos activos.
- `pages/calendars.py`: `CalendarsPage`, disponible sin proyecto. Lista, alta,
  propiedades mediante FormDialog, semana lun–dom, turnos NxM con ancla, feriados
  CL opcionales y aplicación al turno, excepciones con alta/baja, guardado,
  descarte y eliminación. Almanaque anual de 12 meses con QPainter, tooltips y clic
  para alternar excepciones; consulta días y horas del año mediante la API.

## Contratos y decisiones

Toda llamada HTTP de las páginas, incluyendo `reload_catalogs()`, está dentro
de `run_task`. Los parámetros de proyecto y formularios se capturan antes de
iniciar cada operación. Tickets de carga descartan respuestas antiguas al cambiar
proyecto, calendario o año. Las operaciones de escritura no pueden duplicarse
con un segundo clic. Los colores de dibujos, gráficos y deltas usan `tokens()`;
las cuatro páginas admiten el cambio de tema sin reinicio.

La acción principal llama `POST /projects/{pid}/schedule/solve` con `{weights,
options}`. Se verificó el contrato de `api/solve.py` que apareció durante esta
entrega: `apply` es verdadero por defecto. La respuesta puede presentar
`feasible` y `violations` al nivel superior y omitir perfiles de carga. La GUI
acepta ese formato, así como un objeto `verification`, y consulta la carga antes
de generar y después de una aplicación para completar los histogramas.

Solo 404/405 de `/solve` habilitan la compatibilidad con `/schedule/optimize`,
`apply=true`. No se ocultan errores de validación, conexión o servidor. Para
mantenimiento sin actividades, esta compatibilidad genera primero ocurrencias
sin reemplazar. El resultado indica que se usó la API anterior. Sin informe de
verificación, la GUI no anuncia «0 violaciones»; muestra que el informe no llegó.
Una respuesta infactible o con violaciones no habilita aplicar la simulación.

Los totales macro reparten HH por días corridos dentro de la ventana, o de las
fechas programadas si no hay ventana. El algoritmo usa diferencias por intervalo
y evita recorrer todos los meses por actividad. La capacidad macro mantiene la
estimación de la pantalla web: recursos humanos activos × unidades × HH/día ×
días L–V, sin feriados ni turnos. Esto se declara en el pie; no es el perfil de
capacidad exacto del optimizador. El panel por habilidad tampoco debe sumarse
entre habilidades: un recurso polivalente aparece en cada una.

En calendarios, el almanaque muestra días guardados con las excepciones en edición
superpuestas. Cambiar propiedades o eliminar una excepción persistida requiere
Guardar para recalcular el calendario base. Los totales están rotulados
«Guardado» y el aviso de cambios pendientes explica esta distinción; no se
reimplementa el motor de feriados/turnos en la GUI.

## Verificación

Ejecutado desde `Ganttier/backend`:

```powershell
python -B -m pytest -p no:cacheprovider tests/gui/test_planning_pages.py --basetemp=../../trabajos/pytest_gui_q2 -q -rs
```

Resultado: **8 aprobadas, 10 omitidas**. Las ocho pruebas ejecutables sin Qt
compilan directamente los cuerpos de las funciones puras del código real con
AST; comprueban reparto de HH (incluido febrero bisiesto), horizontes mayores de
20 años, esquema columnar, informes, advertencias y fallback de `/solve`.
Esta comprobación no sustituye importar/ejecutar las páginas con PySide6.

Las diez pruebas de integración usan `create_window()` con servidor embebido,
`GANTTIER_DB` y `GANTTIER_DATA_DIR` temporales de pytest, catálogos y demos reales
(`proyecto`, `mantenimiento` de 2.000 actividades). Cubren selección y arrastre
macro, guardar/borrar/descartar, guardar antes de generar, generación sin fechas,
simulación y aplicación, historial, CRUD de recursos/calendarios, excepciones y
turnos, rechazo de respuestas antiguas, fallo del segundo lote de 5.001 cambios,
matriz de presentación de 100.000 filas y capturas de las cuatro páginas en ambos
temas. Las capturas, ajustes Qt y caché matplotlib quedan en el directorio temporal
de pytest dentro del workspace usando el comando anterior.

También se comprobó la sintaxis de los cinco archivos Python mediante AST.
En una comprobación adicional del cálculo mensual, 100.000 actividades repartidas
en 24 meses conservaron sus 10.000.000 HH en 0,100 s. Esta medición corresponde
solo al cálculo numérico, sin dibujar ni medir la interacción de Qt.

## Pendiente de ejecutar en un entorno equipado

El Python disponible no tiene PySide6, pytest-qt, FastAPI, uvicorn, httpx,
holidays ni python-multipart; los dos entornos virtuales encontrados dentro del
workspace tampoco aportan las dependencias. No se instalaron paquetes ni se
modificaron archivos de dependencias ajenos al encargo.

Repetir el comando anterior en el entorno GUI completo, revisar las capturas
claro/oscuro y comprobar fluidez interactiva de 100.000 filas. La virtualización
está implementada y cubierta por una prueba escrita, pero no se midió su
rendimiento gráfico ni se verificó visualmente Qt en esta máquina.
