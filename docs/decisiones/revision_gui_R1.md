# Revisión senior de GUI — R1

Fecha: 26-09-2026. Dictamen: **requiere correcciones antes de distribuir**.
Revisión estática de `GUI_SPEC.md`, los 31 módulos GUI, API relacionada y archivos de empaquetado. No se ejecutó la GUI, no se importó PySide6, no se construyó el ejecutable y no se modificó código fuente.
`pages/activities.py` y `gantt/` tienen implementaciones completas y memoria de entrega (`gui_gantt.md`); se incluyeron. Las referencias corresponden al contenido leído, sujeto al trabajo concurrente de otros agentes.
En los hallazgos, `gui/` abrevia `backend/ganttier/gui/`. **Crítico** bloquea el contrato central; **Alto** afecta datos, estabilidad o un flujo principal; **Medio** afecta corrección o uso habitual; **Bajo** afecta presentación. Los riesgos pendientes de comprobación dinámica se identifican expresamente.

## Hallazgos críticos

### R1-01 · Crítico · El servidor embebido no registra el endpoint de generación

- **Ubicación:** `backend/ganttier/api/__init__.py:4`, `backend/ganttier/api/__init__.py:7`, `backend/ganttier/main.py:84`; endpoint existente en `backend/ganttier/api/solve.py:25`.
- **Escenario:** «Generar programa» llama a `/schedule/solve`, pero `solve.router` nunca se incorpora al router publicado. El 404/405 activa CPM en `gui/pages/activities.py:405` o el optimizador anterior en `gui/pages/optimize.py:40` y `gui/pages/maintenance.py:422`. El usuario no recibe el recorrido de generación y verificación contratado; CPM no comprueba por sí solo factibilidad de recursos.
- **Corrección:** Registrar `solve.router` y comprobar el recorrido real desde actividades sin fechas y desde tareas periódicas. En el servidor embebido de esta versión, tratar la ausencia de `/solve` como error de integración; reservar la compatibilidad para servidores anteriores identificados explícitamente.

## Hallazgos altos

### R1-02 · Alto · Estado compartido mutado desde trabajadores, apertura obsoleta y estado parcial

- **Ubicación:** `gui/app.py:269`, `gui/state.py:38`, `gui/state.py:49`, `gui/pages/resources.py:172`, `gui/pages/resources.py:193`, `gui/pages/calendars.py:355`, `gui/pages/calendars.py:387`.
- **Escenario:** Abrir A y enseguida B permite que la respuesta tardía de A sobrescriba `state.project`; no hay generación de apertura. `reload_catalogs()` consulta el `pid` mutable entre llamadas y puede combinar proyecto y recursos distintos. Si falla un catálogo después de asignar `project`, cambia el estado sin emitir la notificación de apertura. Todo ello ocurre dentro de `run_task`, incluyendo emisiones de `AppState`.
- **Corrección:** Capturar cliente/pid y devolver un paquete inmutable de proyecto y catálogos desde el trabajador. Aplicarlo íntegramente en GUI solo si coincide la generación vigente; emitir las señales después de esa publicación y conservar el estado anterior ante fallo.

### R1-03 · Alto · La entrega de `run_task` no tiene un receptor GUI explícito

- **Ubicación:** `gui/widgets/common.py:87`, `gui/widgets/common.py:118`, `gui/widgets/common.py:130`; conexión adicional a lambda en `gui/pages/base.py:29`.
- **Escenario:** Los trabajadores emiten señales conectadas a funciones Python locales `ok`/`bad`, que llaman a `busy_end`, modifican modelos/widgets y pueden crear `QMessageBox`. No existe un receptor `QObject` con slots y afinidad GUI explícita. Es un **riesgo de afinidad pendiente de comprobar en la versión instalada**, no un segfault demostrado: Qt distingue los receptores Python de los métodos `@Slot` al cruzar hilos. [Documentación de Qt](https://doc.qt.io/qtforpython-6/tutorials/basictutorial/signals_and_slots.html#thread-affinity).
- **Corrección:** Usar un despachador `QObject` creado en GUI, métodos `@Slot(object)` y conexiones encoladas; retenerlo hasta finalizar. Verificar identidad de hilo en éxito/error, incluso cuando el callback inicia otra tarea, y conectar señales de estado a slots GUI explícitos.

### R1-04 · Alto · Filas del proyecto anterior siguen habilitadas durante la recarga

- **Ubicación:** `gui/pages/maintenance.py:209`, `gui/pages/maintenance.py:353`, `gui/pages/maintenance.py:387`; `gui/pages/resources.py:72`, `gui/pages/resources.py:147`, `gui/pages/resources.py:185`.
- **Escenario:** Tras visitar A, abrir B y volver rápidamente a Mantenimiento/Recursos, la nueva consulta conserva filas y selección de A. Editar/eliminar no comprueba `loading` ni el proyecto propietario del modelo: usa el ID global de la fila antigua y puede modificar o borrar datos de A mientras la cabecera dice B. Los tickets de respuesta no protegen esta ventana de interacción.
- **Corrección:** Vaciar selección/modelos al cambiar de proyecto y bloquear acciones dependientes de datos mientras cargan. Asociar cada modelo a un `data_pid` y exigir su coincidencia con el proyecto activo antes de toda mutación.

### R1-05 · Alto · Doble clic y acciones repetidas generan más de un POST

- **Ubicación:** `gui/pages/projects.py:135`, `gui/pages/projects.py:146`; `gui/pages/activities.py:310`, `gui/pages/activities.py:399`, `gui/pages/activities.py:421`.
- **Escenario:** Pulsar dos veces Demo o Duplicar crea dos proyectos: el cursor ocupado no deshabilita botones. En Actividades, `mutate()` serializa, pero encola cada petición repetida; dos clics en Generar ejecutan dos corridas, y se pueden encadenar altas de líneas base mientras una escritura sigue pendiente. La exclusión de Optimize/Maintenance tampoco impide lanzar otro solve desde otra página.
- **Corrección:** Añadir una exclusión compartida por proyecto/operación para generación y guardas de acción en altas/duplicaciones, con desbloqueo en éxito/error. La cola debe distinguir ediciones diferentes de reenvíos de la misma operación; usar idempotencia del servidor para altas no repetibles.

### R1-06 · Alto · REST síncrono y errores fuera del manejador en slots de Proyectos

- **Ubicación:** `gui/pages/projects.py:106`, `gui/pages/projects.py:126`, `gui/pages/projects.py:162`; `gui/state.py:44`, `gui/state.py:49`; timeout en `gui/client.py:44`.
- **Escenario:** Crear el primer proyecto hace dos GET de catálogos directamente en el slot. Editar el proyecto abierto ejecuta otro GET en `done`, y borrar el activo llama a `open_project(None)`, que también recarga catálogos. Una API lenta congela la ventana; un `ApiError` en estas rutas no pasa por el `try` del trabajador ni por `show_error`.
- **Corrección:** Cargar estos datos con trabajadores y publicar el resultado en GUI. Separar «cerrar proyecto» de «consultar catálogos» y mostrar expresamente los fallos de las lecturas posteriores a una escritura confirmada.

### R1-07 · Alto · Cambiar de proyecto o cerrar elimina borradores sin aviso

- **Ubicación:** `gui/pages/control.py:207`, `gui/pages/control.py:209`, `gui/app.py:264`, `gui/app.py:310`; borradores macro en `gui/pages/macro.py:298`.
- **Escenario:** Registrar HH, costo o avance en Control, navegar a Proyectos y abrir otro elimina `drafts` al volver a Control. No hay confirmación ni conservación por proyecto. Cerrar la ventana pierde también ventanas macro y ediciones de calendario pendientes, aunque estas páginas adviertan localmente al cambiar de elemento.
- **Corrección:** Incorporar una consulta de cambios pendientes antes de cambiar proyecto/cerrar, con Guardar/Descartar/Cancelar. Conservar borradores por proyecto y no limpiar los de Control hasta una decisión explícita o un guardado confirmado.

### R1-08 · Alto · Fallo de recarga se presenta como fallo del alta ya confirmada

- **Ubicación:** `gui/pages/resources.py:169`, `gui/pages/resources.py:181`; `gui/pages/calendars.py:352`, `gui/pages/calendars.py:367`.
- **Escenario:** El POST de recurso/calendario termina, pero falla `reload_catalogs()`. La tarea entra en `_failed` y comunica fracaso de toda la operación. En un calendario nuevo, `self.editing` sigue sin el ID devuelto; pulsar Guardar otra vez vuelve a hacer POST y puede duplicarlo. Lo mismo induce al usuario a repetir un alta de recurso.
- **Corrección:** Separar resultado de escritura y error de recarga; publicar primero el ID confirmado. Informar «guardado; no se pudo actualizar» y permitir reintentar únicamente la lectura, siguiendo el patrón ya usado en Control.

### R1-09 · Alto · Cierre sin coordinación con escrituras en curso

- **Ubicación:** `gui/app.py:310`, `gui/app.py:344`, `gui/server.py:56`; cola de escrituras en `gui/pages/activities.py:320` y tareas retenidas en `gui/widgets/common.py:97`.
- **Escenario:** Cerrar mientras se guarda o programa termina el bucle GUI y comienza a parar uvicorn sin drenar las operaciones autorizadas. Los callbacks encolados pueden no procesarse y las siguientes escrituras de `_queue` no llegan a iniciarse; no se informa qué quedó confirmado. El timeout HTTP de 600 s agrava una salida con trabajadores pendientes.
- **Corrección:** Impedir el cierre definitivo mientras existan escrituras pendientes, informar su estado y finalizar de forma asíncrona. Detener el servidor y cerrar el cliente solo después de resolver esas operaciones y vaciar sus callbacks.

### R1-10 · Alto · «Abrir informe» escribe junto al ejecutable en lugar del directorio de datos

- **Ubicación:** `gui/pages/reports.py:46`, `gui/pages/reports.py:275`, `gui/pages/reports.py:295`; configuración correcta pero ignorada en `gui/launcher.py:13`.
- **Escenario:** En la distribución `onedir`, `parents[4]/datos/informes_locales` queda bajo la carpeta de instalación. Instalar en una ubicación sin permisos de escritura hace fallar Abrir; en instalaciones escribibles divide los datos entre esa carpeta y `%LOCALAPPDATA%/Ganttier`.
- **Corrección:** Resolver la carpeta de informes a partir de `GANTTIER_DATA_DIR` o de una utilidad común de rutas de usuario. Persistir una carpeta alternativa elegida por el usuario y mantener los recursos empaquetados como solo lectura.

## Hallazgos medios

### R1-11 · Medio · La corrección del filtro persistente no cubre todas las páginas

- **Ubicación:** `gui/pages/activities.py:181`, `gui/pages/activities.py:218`; `gui/pages/maintenance.py:210`, `gui/pages/maintenance.py:251`; `gui/pages/macro.py:344`, `gui/pages/macro.py:377`; `gui/pages/resources.py:110`.
- **Escenario:** Buscar un código exclusivo de A y abrir B conserva el texto en estas páginas; Gantt conserva además Solo críticas. Las respuestas correctas pueden quedar completamente ocultas. Control sí limpia su búsqueda (`control.py:213`): no se vuelve a reportar ese caso como pendiente.
- **Corrección:** Reiniciar filtros y selección al cambiar de pid, o conservarlos separadamente por proyecto con un indicador visible y «Limpiar filtros». Incluir los filtros booleanos y combos, además del texto.

### R1-12 · Medio · Excepciones de callbacks escapan del mecanismo central de errores

- **Ubicación:** `gui/widgets/common.py:118`, `gui/widgets/common.py:123`, `gui/widgets/common.py:594`; `gui/pages/base.py:37`.
- **Escenario:** `_Task.run` solo captura `fn`; una excepción en `on_done`, `on_error` o un slot envuelto por `button` queda fuera. Un ejemplo concreto es el GET de R1-06. Además, `Page.activate()` consume `_stale` antes de cargar: una página sin manejador propio que restaure ese estado, como Proyectos, no reintenta al regresar después de un fallo.
- **Corrección:** Proteger callbacks y slots de entrada con registro y aviso seguro, sin recursión del manejador. Ofrecer una finalización por tarea en `finally` y restaurar el estado recargable tras errores; no asumir que toda excepción Python necesariamente provoca un aborto de Qt.

### R1-13 · Medio · El comparador ordena un valor nulo como menor que sí mismo

- **Ubicación:** `gui/widgets/common.py:298`, `gui/widgets/common.py:300`.
- **Escenario:** Ordenar proyectos por Fin cuando varias filas no tienen fecha produce `lessThan(None, None) == True` en ambos sentidos. Esto incumple el orden estricto que necesita la ordenación y hace indefinido el orden relativo de los nulos. Se comprobó el resultado ejecutando únicamente el cuerpo Python extraído, con índices simulados, sin Qt.
- **Corrección:** Resolver primero ambos nulos devolviendo `False`, luego los casos de un único nulo; normalizar también valores no finitos antes de comparar. Comprobar irreflexividad y asimetría con datos vacíos y mixtos.

### R1-14 · Medio · Persisten colisiones de nombres con métodos de QWidget

- **Ubicación:** `gui/widgets/common.py:538` (`MplCanvas.style`), `gui/gantt/view.py:487` (`GanttWidget.x`); cobertura insuficiente en `backend/tests/gui/test_sombras_qt.py:34`.
- **Escenario:** `canvas.style()` devuelve `None` y aplica estilos al gráfico, aunque la interfaz heredada debe devolver `QStyle`; `gantt.x()` lanza `TypeError` porque exige una fecha. Rompen utilidades genéricas de estilo/geometría. Son métodos no virtuales: **no se afirma que reproduzcan el segfault de `metric`**. El test solo busca asignaciones con regex, no estas definiciones. [Contrato de QWidget](https://doc.qt.io/qt-6/qwidget.html#style).
- **Corrección:** Renombrar a `apply_plot_theme()` y `day_to_x()`, actualizando usos. Auditar con AST según la clase base real, incluyendo métodos y asignaciones múltiples; no marcar `self.result` de un `QWidget` como si heredase `QDialog.result()`.

### R1-15 · Medio · Consultas completas duplicadas por cada cambio de estado

- **Ubicación:** `gui/state.py:41`, `gui/pages/base.py:28`, `gui/pages/base.py:31`; `gui/pages/control.py:436` y `gui/pages/control.py:437`.
- **Escenario:** Una apertura o mutación emite `project_changed` y `schedule_changed`. La página visible ejecuta `refresh()` inmediatamente por cada señal; Control lanza por refresco proyecto, bases, 100.000 actividades, recursos, curva S y EVM. Los tickets descartan la respuesta anterior, pero no evitan descargarla ni calcularla.
- **Corrección:** Acumular invalidaciones de un mismo ciclo con un temporizador GUI y fusionar refrescos pendientes. Distinguir cambio de proyecto, cambio de programa y cambios de cabecera para recargar únicamente los datos afectados.

### R1-16 · Medio · Filtros y agregaciones masivos se ejecutan por cada tecla en GUI

- **Ubicación:** `gui/pages/control.py:197`, `gui/pages/control.py:352`; `gui/pages/maintenance.py:195`, `gui/pages/maintenance.py:245`; `gui/pages/macro.py:324`, `gui/pages/macro.py:377`.
- **Escenario:** Con 100.000 filas, cada pulsación reconstruye textos y recorre todos los datos; Control además ordena O(n log n), Mantenimiento recalcula KPI y vuelve a recorrer ocurrencias para un calendario que ni siquiera depende del texto. Paginar a 200 filas limita widgets, pero no este trabajo síncrono.
- **Corrección:** Aplicar debounce y filtrar/agregar instantáneas fuera de GUI con ticket; publicar únicamente índices o la página visible. Cachear textos normalizados y agregados, y recalcular el calendario solo cuando cambien sus entradas.

### R1-17 · Medio · Perfil por recurso crea un elemento Qt por cada serie

- **Ubicación:** `gui/gantt/profile.py:108`, `gui/gantt/profile.py:171`, `gui/gantt/profile.py:190`, `gui/gantt/profile.py:213`.
- **Escenario:** Un proyecto con muchos recursos/habilidades construye todos los `QListWidgetItem` en GUI; cada selección y cambio de tema recorre toda la lista. El coste escala con las series, no con las actividades: 100.000 actividades con cinco recursos no disparan este caso, pero una serie por recurso numeroso sí.
- **Corrección:** Sustituir la lista por `QListView` con modelo virtual y filtro; mantener las selecciones en un conjunto y actualizar solo filas afectadas. No confundir este problema con el Gantt, cuyo modelo de actividades sí es virtual.

### R1-18 · Medio · La regeneración predeterminada omite nuevas tareas periódicas

- **Ubicación:** `gui/pages/maintenance.py:416`; `backend/ganttier/solve.py:113`, `backend/ganttier/solve.py:115`, `backend/ganttier/solve.py:124`.
- **Escenario:** **Después de registrar `/solve` (R1-01)**, generar un plan, agregar otra tarea y volver a Generar con Reemplazar desmarcado envía `regenerate=False`. El motor detecta cualquier ocurrencia existente en el horizonte y omite toda generación: la tarea nueva no entra al programa. Marcar una opción anunciada como reemplazo de avances no es una solución adecuada para agregar trabajo.
- **Corrección:** Separar «completar ocurrencias faltantes» de «reemplazar»: generar y combinar por identidad de tarea/ocurrencia preservando avances y bases. Mostrar cuántas tareas quedaron sin ocurrencias antes de declarar la generación terminada.

### R1-19 · Medio · Borrar relaciones o asignaciones no pide confirmación

- **Ubicación:** `gui/gantt/dialogs.py:223`, `gui/gantt/dialogs.py:249`.
- **Escenario:** En Detalle, Eliminar ejecuta inmediatamente DELETE sobre la relación/asignación seleccionada. No hay confirmación ni deshacer; un clic equivocado modifica la lógica de precedencias o la demanda de recursos y puede disparar CPM.
- **Corrección:** Confirmar indicando actividad, relación/recurso y efecto, o proporcionar un deshacer transaccional real. Mantener la selección y bloquear el botón hasta conocer el resultado del DELETE.

### R1-20 · Medio · Navegación incompleta por teclado y etiquetas sin asociación

- **Ubicación:** `gui/app.py:111`, `gui/app.py:303`; `gui/widgets/common.py:350`; `gui/pages/calendars.py:78`.
- **Escenario:** La barra lateral usa `NoFocus`; los atajos solo cubren las primeras nueve páginas, dejando Informes e Importar/Exportar sin acceso por ese mecanismo. Los labels del formulario no se asocian a sus editores. El calendario anual anuncia clic, pero no ofrece navegación/activación equivalente por teclado.
- **Corrección:** Permitir foco y navegación de la barra, ofrecer acciones accesibles para todas las páginas y asociar labels mediante `setBuddy`/nombres accesibles. Añadir selección de fecha y alternancia de excepciones por teclado.

## Hallazgos bajos

### R1-21 · Bajo · Estados técnicos y barra gráfica quedan sin traducción

- **Ubicación:** `gui/pages/optimize.py:223`, `gui/pages/optimize.py:432`, `gui/pages/activities.py:376`, `gui/widgets/common.py:532`; arranque en `gui/app.py:333`.
- **Escenario:** Se muestran valores como `heuristico`/`optimo` sin tildes y `method_used` sin traducción; la barra estándar de Matplotlib no recibe etiquetas españolas. No se instala un traductor Qt en el arranque para diálogos estándar. La experiencia mezcla el vocabulario español del planificador con claves internas.
- **Corrección:** Centralizar traducciones de estados/métodos y textos de la barra; cargar traducciones Qt de español y mantener formato chileno de fechas/números. Conservar las claves originales únicamente en datos internos o diagnóstico.

## Comprobaciones y límites de la revisión

- No se encontró otra asignación de `self.metric`. `self.result` en Optimize/ImportExport no oculta `QDialog.result`, porque esas páginas heredan de QWidget. Las implementaciones de `data`/`flags` en modelos son overrides deliberados.
- No se encontraron `QTableWidget`, `QStandardItem` ni `setRowCount` masivos en la GUI. Gantt construye arreglos, filas e índices en trabajadores y pinta el área visible; `RowModel` y `ProfileModel` son clases Python, no objetos Qt creados indebidamente en el pool. No se demostró un bucle O(n²) en el hilo GUI.
- Fuera de las mutaciones de AppState indicadas, no se localizaron getters de widgets dentro de los cuerpos de trabajo examinados. Acceder al cliente estable mediante `self.state.client` no equivale por sí solo a modificar widgets. Emitir señales desde un trabajador es válido; importa la afinidad del receptor y el lugar donde se publica el estado.
- Optimize, Control, Performance, Reports, ImportExport y Gantt ya incluyen varias guardas pid/ticket; no se atribuye contaminación de resultados a todos sus callbacks. `run_task` descuenta el busy antes del callback, por lo que una excepción allí no basta para afirmar que el indicador global queda pegado.
- `ganttier_gui.spec:30` incluye `schema.sql`, plantillas y ejemplos; `:35` recoge módulos dinámicos de Ganttier/uvicorn; `:37` y `:54` seleccionan QtAgg/Agg; `:44` recoge OR-Tools si está instalado. No hay evidencia estática de un hiddenimport faltante: PySide6 se importa explícitamente y dispone de hooks de PyInstaller. La ausencia de OR-Tools está prevista como dependencia opcional, no es por sí sola un defecto.
- `construir_exe.bat:22` construye `onedir` y advierte distribuir `_internal`; `iniciar.bat:12` usa `pythonw`; `launcher.py:23` repara stdout/stderr y `:13` sitúa BD/datos congelados en LOCALAPPDATA. R1-10 es la excepción concreta encontrada. Arranque/errores de permisos y dependencias nativas deben verificarse en Windows sin Python instalado; aquí no se certifica el paquete.
- Validación realizada: análisis AST de los 31 módulos GUI y lectura de contratos/rutas; comparación nula de R1-13 aislada de Qt. Quedan pendientes pruebas dinámicas de afinidad, cierre con tareas, respuestas reordenadas, temas y rendimiento gráfico con 100.000 filas.

## Tabla resumen

| Prioridad | IDs | Cantidad | Impacto principal |
|---|---|---:|---|
| Crítico | R1-01 | 1 | El endpoint central de generación no está publicado |
| Alto | R1-02–R1-10 | 9 | Estado/proyecto, hilos, escrituras, bloqueos, pérdida de trabajo y rutas |
| Medio | R1-11–R1-20 | 10 | Filtros, errores, ordenación, Qt, escala, mantenimiento y accesibilidad |
| Bajo | R1-21 | 1 | Traducción y terminología |
| **Total** | **R1-01–R1-21** | **21** | **Revisión estática; R1-03 requiere confirmación dinámica de afinidad** |

## Las 10 correcciones más valiosas

1. **R1-01:** Publicar `/schedule/solve` y verificar generación desde tareas sin fechas con la API embebida real.
2. **R1-02:** Publicar proyecto/catálogos atómicamente en GUI y descartar aperturas obsoletas.
3. **R1-03:** Hacer explícito el receptor GUI de `run_task` y comprobar el hilo de todos sus callbacks.
4. **R1-04:** Bloquear filas obsoletas durante cambios de proyecto para impedir editar/eliminar datos de otro proyecto.
5. **R1-05:** Impedir POST repetidos y corridas simultáneas del mismo proyecto desde páginas distintas.
6. **R1-06:** Sacar de los slots las consultas REST de Proyectos y cubrir sus errores.
7. **R1-07:** Proteger borradores al cambiar proyecto o cerrar, conservándolos por proyecto.
8. **R1-08:** Distinguir escritura confirmada de recarga fallida para que un reintento no duplique altas.
9. **R1-09:** Coordinar cierre, cola de escrituras, callbacks, cliente HTTP y servidor.
10. **R1-10:** Unificar todas las rutas escribibles del ejecutable bajo el directorio de datos del usuario.
