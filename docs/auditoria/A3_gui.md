# A3 — Auditoría de defectos de la GUI

Fecha: 2026-10-03. Revisión estática de `backend/ganttier/gui/`, contrastada con `GUI_SPEC.md`, `GUI_UX_SPEC.md` y los patrones de `backend/tests/gui/`. Las rutas y líneas corresponden a la copia examinada del repositorio Ganttier. No se modificaron fuentes ni pruebas existentes.

Se documentan **11 defectos: 6 de severidad alta y 5 de severidad media**. Alta significa pérdida silenciosa de trabajo o alteración de datos; media, selección incorrecta de contexto, incumplimiento del contrato del modelo, bloqueo o acumulación de objetos. No se incluyen los defectos conocidos D1–D4 del backend.

No está instalado PySide6 en el intérprete disponible. Los comportamientos Qt indicados se sustentan en código y documentación oficial de Qt 6; no se presentan como reproducciones visuales ejecutadas. Se añadieron candidatas en [`test_a3_regressions.py`](../../_encargos/auditoria_tests/test_a3_regressions.py): seis casos Qt pendientes de ejecución y dos casos parametrizados del conversor numérico ejecutados sin Qt. Estos últimos fallan contra la conducta esperada y confirman A3-02. Las pruebas afirman la conducta corregida, no llevan `xfail` y no constituyen una suite aprobada.

### A3-01 La tabla y las barras del Gantt usan alturas de fila distintas

**Severidad:** Alta.

**Archivo:línea:** `backend/ganttier/gui/pages/activities.py:357`; `widgets/common.py:1157`; `theme.py:73`; `gantt/view.py:20`, `:74`, `:260`, `:422` (estas últimas rutas también bajo `backend/ganttier/gui/`).

**Pasos para reproducir en la GUI:** Abrir Actividades y Gantt con doce o más actividades programadas. Desactivar la agrupación para facilitar la comparación. Comparar el nombre de una fila situada hacia la mitad inferior con su barra; seleccionar esa fila y luego pulsar o arrastrar la barra situada horizontalmente frente a ella. Repetir después de desplazar verticalmente.

**Obtenido vs esperado:** `GanttWidget` configura filas de 26 px. Después, el constructor de `ActivitiesPage` llama a `setup_table`, que impone tamaño predeterminado y mínimo de 30 px. El lienzo sigue dibujando filas y resolviendo clics con 26 px. La separación entre el inicio de la fila de índice `r` y su barra acumula `4*r` píxeles, incluso antes del desplazamiento. Las etiquetas, selección y barras dejan de corresponder horizontalmente: una barra junto a otro nombre puede seleccionar o editar la actividad de esa barra. Se espera una misma geometría para tabla, lienzo, selección y desplazamiento. No es un fallo de `mapToSource`: el error está en la correspondencia visual que guía la edición.

**Sustento Qt 6:** `defaultSectionSize` establece el tamaño de las secciones en modo `Fixed`; dicho modo no impide que la aplicación cambie sus tamaños. Por tanto, el ajuste posterior a 30 px sí afecta a las filas. [QHeaderView: tamaños y modos](https://doc.qt.io/qt-6/qheaderview.html#defaultSectionSize-prop).

**Estado:** Análisis estático; prueba candidata `test_a3_01_activity_page_keeps_table_and_canvas_row_height_equal`. Usa la página real, porque probar solo `GanttWidget` omite el ajuste que provoca el defecto.

**Corrección sugerida mínima:** Permitir que `setup_table` conserve la altura del Gantt, o parametrizarla y pasar la misma constante usada por el lienzo. Mantener idénticos tamaño mínimo, predeterminado, cálculo de coordenadas y desplazamiento.

### A3-02 El pegado interpreta incorrectamente los enteros chilenos con separadores de miles

**Severidad:** Alta.

**Archivo:línea:** `backend/ganttier/gui/widgets/paste.py:62`, `:155`; `backend/ganttier/gui/pages/activities.py:508`.

**Pasos para reproducir en la GUI:** Copiar desde una planilla chilena una actividad válida con HH mostradas como `1.234`, sin decimales. Abrir Pegar desde Excel, revisar y confirmar. Repetir con HH `1.234.567`.

**Obtenido vs esperado:** La función elimina los puntos de miles únicamente si encuentra una coma. En el primer caso acepta `1.234` como el número decimal 1,234, mil veces menor que 1.234 HH, y ese valor puede llegar al payload de creación. En el segundo caso rechaza un entero chileno válido. Se espera interpretar la agrupación conforme al formato chileno declarado por `GUI_UX_SPEC.md:6`, o resolver explícitamente la ambigüedad antes de aceptar el dato. Con `1.234,5` el código sí realiza la conversión correcta.

**Estado:** Confirmado sin Qt ejecutando por AST la función original: `parse_number("1.234") == 1.234`; `parse_number("1.234.567")` lanza `ValueError`. Prueba candidata `test_a3_02_chilean_grouped_integers`: **2 fallos esperados contra el código actual**.

**Corrección sugerida mínima:** Reconocer la agrupación chilena completa también sin parte decimal. Para conservar la admisión de punto decimal, definir una regla explícita para entradas ambiguas; no aceptar silenciosamente una interpretación incompatible con la planilla chilena. Las candidatas asumen prioridad del formato chileno.

### A3-03 Editar un texto redondea y vuelve a guardar números que el usuario no modificó

**Severidad:** Alta.

**Archivo:línea:** `backend/ganttier/gui/widgets/common.py:396`, `:503`, `:546`; `backend/ganttier/gui/gantt/dialogs.py:26`, `:30`, `:188`; `backend/ganttier/gui/gantt/editing.py:56`; `backend/ganttier/gui/pages/activities.py:925`.

**Pasos para reproducir en la GUI:** Crear mediante pegado una actividad con HH `1,234` —valor decimal válido de tres cifras—. Abrir su detalle, cambiar únicamente el nombre o las notas y guardar. Volver a consultar las HH.

**Obtenido vs esperado:** Los campos flotantes usan `Field.dec=2`. Al cargar 1,234 en el editor, su valor pasa a 1,23; `values()` devuelve ese valor para todos los campos y `detail_patch` lo interpreta como un cambio. Se envía `work_hours=1.23` aunque el usuario no haya editado las HH. También están expuestos otros flotantes de estos formularios, como costo y dotación. Se espera conservar exactamente los valores no editados, separando precisión de presentación y modificación efectiva.

**Sustento Qt 6:** `QDoubleSpinBox` redondea el valor efectivo a `decimals`; no se limita a recortar el texto visible. Su getter devuelve el valor redondeado. [QDoubleSpinBox: value](https://doc.qt.io/qt-6/qdoublespinbox.html#value-prop).

**Estado:** Análisis estático; prueba candidata `test_a3_03_editing_name_does_not_round_untouched_hours` sobre `FormDialog` y `detail_patch` reales.

**Corrección sugerida mínima:** Conservar el valor original de cada campo numérico que el usuario no haya modificado, o ajustar la precisión del editor a la precisión admitida por los datos sin redondear al cargar. No basta con comparar el valor ya redondeado contra el original.

### A3-04 Editar un proyecto sin catálogo cargado borra su calendario asignado

**Severidad:** Alta.

**Archivo:línea:** `backend/ganttier/gui/pages/projects.py:281`, `:325`; `backend/ganttier/gui/widgets/common.py:521`; `backend/ganttier/gui/state.py:44`; trazabilidad del guardado: `backend/ganttier/api/resources.py:50`.

**Pasos para reproducir en la GUI:** Abrir una base que contenga un proyecto con calendario especial, en una sesión sin proyecto activo ni catálogos precargados —por ejemplo, preferencias de primera ejecución con una base existente—. Ir a Proyectos, seleccionar su fila y pulsar Editar sin abrirlo. Cambiar solo su nombre y guardar.

**Obtenido vs esperado:** `state.calendars` empieza vacío. A diferencia de Nuevo proyecto, Editar no consulta los calendarios. La lista contiene únicamente la opción con valor `None`; al no encontrar el ID original, `FormDialog` selecciona la primera opción. El payload incluye incondicionalmente `calendar_id=None` y la actualización lo persiste. Se espera conservar el calendario existente o cargar las opciones antes de editar. El reemplazo por calendario predeterminado que existe en la creación del backend no se ejecuta en esta actualización.

**Estado:** Análisis estático; prueba candidata `test_a3_04_project_calendar_survives_edit_without_open_project`, que inspecciona el payload de `ProjectsPage.edit` con formulario real.

**Corrección sugerida mínima:** Cargar el catálogo antes de abrir Editar, igual que en Nuevo proyecto. Si un ID existente falta en las opciones, conservarlo y avisar o impedir guardar ese campo; no sustituirlo implícitamente por la primera opción.

### A3-05 Cambiar de proyecto elimina el borrador de Calendarios sin confirmación

**Severidad:** Alta.

**Archivo:línea:** `backend/ganttier/gui/pages/calendars.py:282`, `:291`, `:300`, `:454`; `backend/ganttier/gui/pages/base.py:36`, `:44`.

**Pasos para reproducir en la GUI:** Con el proyecto A activo, entrar a Calendarios y cambiar un día de la semana o agregar una excepción. Comprobar el mensaje de cambios pendientes y no guardar. Seleccionar el proyecto B en el encabezado; volver a A y al calendario editado. También ocurre si se deja Calendarios oculta antes del cambio de proyecto.

**Obtenido vs esperado:** La señal `project_changed` alcanza incluso a la página oculta. `_sync_project()` borra `editing`, `days` y `calendars`, y pone `dirty=False`, sin consultar ni conservar el borrador. Al volver solo se recuperan los datos persistidos. Se espera pedir una decisión o mantener el borrador recuperable, como ya se hace con otros datos en edición. Los controles contra respuestas de otro proyecto no requieren destruir el trabajo pendiente.

**Estado:** Análisis estático. La asignación destructiva ocurre directamente en la ruta de la señal, sin depender de latencia ni del resultado de una petición.

**Corrección sugerida mínima:** Separar el borrador de calendario del contenido remoto de la página y conservarlo al invalidar el proyecto, o comprobar cambios pendientes antes de autorizar el cambio. No poner `dirty=False` hasta guardar o descartar explícitamente.

### A3-06 El cierre ignora los borradores de Programación macro y Calendarios

**Severidad:** Alta.

**Archivo:línea:** `backend/ganttier/gui/app.py:657`; `backend/ganttier/gui/pages/macro.py:309`, `:376`, `:482`; `backend/ganttier/gui/pages/calendars.py:181`, `:454`; contraste: `backend/ganttier/gui/pages/control.py:255`.

**Pasos para reproducir en la GUI:** En una ejecución interactiva normal, asignar una ventana o mes a actividades en Programación macro sin guardar, o modificar un calendario sin guardarlo. Dejar terminar las tareas de lectura y cerrar la ventana principal. Para aislar el caso, no mantener borradores en Control.

**Obtenido vs esperado:** El cierre solo pregunta si alguna página implementa `has_unsaved()` y devuelve verdadero. Control lo implementa; Macro y Calendarios no. Por eso no se muestra la advertencia y se pierde el trabajo, incluidos borradores macro de proyectos anteriormente visitados que están en `_pending`. Se espera ofrecer cancelar el cierre o descartar explícitamente todos los borradores existentes. Este caso es distinto de A3-05: ocurre sin cambiar de proyecto y afecta también a Macro.

**Estado:** Análisis estático de todas las definiciones de `has_unsaved` y del cierre. No se basa en el cierre automático `offscreen`, que deliberadamente omite preguntas. Una prueba de ventana debe establecer `prompt_on_close=True`.

**Corrección sugerida mínima:** Implementar el contrato en ambas páginas: `dirty` en Calendarios y presencia de entradas en cualquiera de los borradores por proyecto de Macro. Hacer que todas las páginas con guardado diferido participen del mismo contrato.

### A3-07 Volver a elegir A no invalida la apertura pendiente de B

**Severidad:** Media.

**Archivo:línea:** `backend/ganttier/gui/app.py:488`; `backend/ganttier/gui/state.py:101`, `:117`, `:134`.

**Pasos para reproducir en la GUI:** Con A abierto, seleccionar B en el selector del encabezado. Mientras se cargan proyecto, calendarios o recursos de B, volver a seleccionar A. Para una reproducción determinista, retrasar la respuesta de cualquiera de esas lecturas de B. Esperar su finalización.

**Obtenido vs esperado:** Durante la carga `state.pid` sigue siendo A. La segunda selección se omite porque coincide con ese ID: no cambia la generación ni cancela la intención pendiente. Cuando termina B, su generación sigue vigente y se publica B, pese a que A fue la última selección del usuario. Se espera que prevalezca A. El guardado de datos en un proyecto ajeno no está demostrado por este caso; el defecto confirmado es la sustitución del contexto activo por una intención ya obsoleta.

**Estado:** Análisis estático; prueba candidata `test_a3_07_selecting_current_project_cancels_pending_other_project`, con el método real del selector, `AppState` real y entrega controlada de respuestas.

**Corrección sugerida mínima:** Invalidar la solicitud pendiente también cuando el usuario selecciona el proyecto que continúa publicado. Mantener explícitamente el ID solicitado, o incluir esta cancelación en el protocolo de generaciones.

### A3-08 Actualizar el historial de informes sobrescribe la configuración aún no guardada

**Severidad:** Media.

**Archivo:línea:** `backend/ganttier/gui/pages/reports.py:215`, `:329`, `:346`, `:358`.

**Pasos para reproducir en la GUI:** En Informes, esperar la carga inicial. Modificar Activar, formatos, día semanal o cantidad a conservar sin pulsar Guardar configuración. Pulsar Actualizar historial o F5. Otra variante: modificar cualquiera de esos controles mientras está pendiente la respuesta de carga.

**Obtenido vs esperado:** Actualizar historial llama a `refresh()`, cuyo callback repone todos los controles desde la configuración persistida. No existe comprobación de edición pendiente. Durante la petición solo se deshabilitan Guardar y Emitir; los campos siguen editables, por lo que también se pisan cambios posteriores al inicio de la lectura. Se espera refrescar el historial preservando el borrador, o pedir confirmación antes de reemplazarlo.

**Estado:** Análisis estático. Las comprobaciones de PID y número de petición evitan resultados de otro proyecto, pero no evitan sobrescribir cambios locales del mismo proyecto.

**Corrección sugerida mínima:** Separar actualización del historial y carga del formulario. Marcar la configuración como modificada y no hidratarla desde lecturas en curso si hubo edición local; restablecer esa marca solo después de guardar o descartar.

### A3-09 El modelo editable emite modelReset antes de reconstruir los valores de sus casillas

**Severidad:** Media.

**Archivo:línea:** `backend/ganttier/gui/widgets/paste.py:225`, `:259`, `:269`; uso del modelo en `backend/ganttier/gui/wizard.py`.

**Pasos para reproducir en la GUI:** En la tabla de tareas del asistente, agregar o pegar filas que incluyan campos booleanos, como Requiere desconexión. Para observar el defecto de forma determinista, conectar un observador de `modelReset` que lea el `CheckStateRole` del índice de la última fila nueva; la prueba candidata incluye ese observador.

**Obtenido vs esperado:** `set_rows()` reemplaza `rows` y emite `endResetModel()` antes de actualizar `converted`. Durante `modelReset`, `rowCount()` ya anuncia las filas nuevas, pero `data()` intenta acceder al arreglo anterior. Al aumentar el número de filas, consultar la última casilla lanza `IndexError`; con igual tamaño puede devolver el estado de la fila anterior. Se espera que todo índice nuevo válido sea consultable y coherente cuando se anuncia el fin del reset. No se afirma que toda pulsación de Agregar provoque un fallo visible: eso depende de cuándo consulte la vista; la inconsistencia observable del contrato sí es determinista.

**Sustento Qt 6:** `endResetModel()` debe llamarse después de restablecer las estructuras internas, y emite `modelReset`. `converted` forma parte de esas estructuras porque `data()` depende de ella. [QAbstractItemModel: endResetModel](https://doc.qt.io/qt-6/qabstractitemmodel.html#endResetModel).

**Estado:** Análisis estático; prueba candidata `test_a3_09_model_reset_publishes_valid_checkbox_data`. Captura la excepción del observador para que el fallo se informe como aserción y no escape de un callback Qt.

**Corrección sugerida mínima:** Reconstruir y validar filas, conversiones y errores dentro del par de reset, antes de `endResetModel()`. Separar el cálculo de validación de sus emisiones para no emitir `dataChanged` con el reset abierto; emitir la notificación auxiliar al terminar.

### A3-10 Cada apertura del detalle deja un diálogo cerrado y sus conexiones vivos

**Severidad:** Media.

**Archivo:línea:** `backend/ganttier/gui/pages/activities.py:916`; `backend/ganttier/gui/gantt/dialogs.py:135`, `:163`; `backend/ganttier/gui/widgets/common.py:407`.

**Pasos para reproducir en la GUI:** Abrir el detalle de una actividad, cancelarlo y repetir muchas veces sin cerrar la ventana principal. Inspeccionar los hijos `ActivityDetail` de la página; cambiar el tema permite observar que los diálogos antiguos siguen suscritos a su actualización.

**Obtenido vs esperado:** Cada apertura crea un nuevo diálogo con la página como padre. Se sustituye `self.detail_dialog`, pero el anterior sigue siendo hijo Qt; al rechazarlo solo se oculta. No se configura eliminación al cerrar ni se llama a `deleteLater`. Por cada ciclo quedan retenidos formulario, editores, tablas y conexión a `theme_changed`. Se espera destruir los detalles finalizados o reutilizar una única instancia. La retención dura hasta destruir la página/ventana; no se afirma una fuga posterior al cierre de la aplicación.

**Sustento Qt 6:** `reject()` oculta el diálogo; la eliminación al finalizar depende de `WA_DeleteOnClose`. Los objetos con padre permanecen en el árbol de propiedad hasta su destrucción explícita o la del padre. [QDialog: reject/done](https://doc.qt.io/qt-6/qdialog.html#reject), [QObject: propiedad de hijos](https://doc.qt.io/qt-6/qobject.html#details).

**Estado:** Análisis estático; prueba candidata `test_a3_10_closed_activity_details_are_destroyed`, usando `open_detail` y `ActivityDetail` reales y procesando `DeferredDelete` antes de contar los hijos.

**Corrección sugerida mínima:** Dar al detalle una política explícita de destrucción al finalizar y limpiar la referencia de la página. Coordinarla con callbacks aún pendientes. Aplicar la solución en este diálogo asíncrono; no activar indiscriminadamente eliminación automática en formularios cuyo llamador lee `values()` después de `exec()`.

### A3-11 El primer asistente consulta calendarios sincrónicamente y bloquea la ventana

**Severidad:** Media.

**Archivo:línea:** `backend/ganttier/gui/wizard.py:763`, `:770`, `:776`; `backend/ganttier/gui/client.py:44`; requisito en `GUI_SPEC.md:35`.

**Pasos para reproducir en la GUI:** Iniciar una sesión sin proyecto abierto ni calendarios en caché. Abrir el asistente de lista de tareas mientras la respuesta de calendarios de la API local está retrasada o bloqueada. Intentar mover, repintar o cerrar la ventana mientras espera.

**Obtenido vs esperado:** `run_task_list_wizard()` llama a `build_wizard()` desde la acción de GUI, y este ejecuta directamente `state.client.calendars()` antes de construir/abrir el diálogo. La petición síncrona impide que el hilo de GUI procese eventos. El cliente tiene un timeout configurado de 600 segundos; no hay una cota corta específica para esta lectura. Si falla, la excepción se suprime y el asistente continúa sin explicar la falta del catálogo. Se espera cargarlo mediante `run_task`, mantener la ventana operable y presentar la causa si la carga falla.

**Estado:** Análisis estático de la cadena de llamadas. No depende de una medición de rendimiento normal: basta una respuesta demorada para retener el hilo de GUI. No se afirma que cada apertura tarde 600 segundos.

**Corrección sugerida mínima:** Mover esa consulta a una carga asíncrona previa o a una página inicial del asistente, con indicación de carga y error recuperable; construir o habilitar las opciones cuando termine.

## Verificación y límites de cobertura

Se inspeccionaron el puente compartido, tareas y callbacks, cambios de proyecto, guardado diferido, operaciones de escritura, modelos y proxies, edición y pegado, conexiones, cierre y validaciones. No se añadió un hallazgo por categoría cuando faltaba evidencia. En particular, el puente entrega callbacks en GUI y captura sus excepciones; las rutas de selección revisadas de Proyectos y Recursos traducen los índices proxy; varias páginas ya comprueban PID y generación. No se atribuyen defectos a esas protecciones ni se interpretan las conversiones ISO de la API como una falta de admisión de fechas chilenas del usuario.

Ejecución realizada desde Ganttier, sin crear bytecode ni caché pytest:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m pytest -q -p no:cacheprovider _encargos/auditoria_tests/test_a3_regressions.py -k chilean_grouped_integers
```

Resultado: **2 failed, 6 deselected**. Los fallos son el valor numérico incorrecto y el `ValueError` descritos en A3-02. La recolección independiente con `--collect-only` reconoce los ocho casos. Se verificaron la estructura del informe, sus referencias a pruebas y la sintaxis del archivo candidato; los hashes de los 52 archivos revisados de GUI, pruebas existentes y especificaciones permanecen iguales. Las seis candidatas Qt requieren ejecución posterior con las dependencias GUI del proyecto. Para ejecutarlas, manteniendo temporales dentro del repositorio:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTEST_QT_API = 'pyside6'
python -B -m pytest -q -p no:cacheprovider --basetemp=_encargos/auditoria_tests/.tmp_a3 _encargos/auditoria_tests/test_a3_regressions.py
```

El directorio `.tmp_a3` debe reservarse exclusivamente a los temporales de esta ejecución. Las candidatas usan API simulada, ajustes en memoria y los patrones `qtbot`/tareas controladas de las pruebas existentes; no levantan el servidor ni utilizan una base de usuario.

## Tabla resumen

| ID | Severidad | Defecto | Evidencia / prueba candidata |
|---|---|---|---|
| A3-01 | Alta | Tabla Gantt a 30 px y lienzo a 26 px | Estático + `test_a3_01_activity_page_keeps_table_and_canvas_row_height_equal` |
| A3-02 | Alta | Miles chilenos convertidos como decimales o rechazados | Dos casos ejecutados de `test_a3_02_chilean_grouped_integers` |
| A3-03 | Alta | Guardar un texto redondea números intactos | Estático + `test_a3_03_editing_name_does_not_round_untouched_hours` |
| A3-04 | Alta | Edición de proyecto elimina calendario no cargado | Estático + `test_a3_04_project_calendar_survives_edit_without_open_project` |
| A3-05 | Alta | Cambio de proyecto destruye borrador de calendario | Estático |
| A3-06 | Alta | Cierre omite borradores macro y de calendarios | Estático |
| A3-07 | Media | Respuesta pendiente de B prevalece tras volver a elegir A | Estático + `test_a3_07_selecting_current_project_cancels_pending_other_project` |
| A3-08 | Media | Actualizar historial reemplaza configuración editada | Estático |
| A3-09 | Media | `modelReset` expone casillas con datos sin reconstruir | Estático + `test_a3_09_model_reset_publishes_valid_checkbox_data` |
| A3-10 | Media | Detalles cerrados y conexiones se acumulan | Estático + `test_a3_10_closed_activity_details_are_destroyed` |
| A3-11 | Media | Primera carga de calendarios del asistente bloquea GUI | Estático |
