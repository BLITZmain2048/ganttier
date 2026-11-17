# P4 — Usabilidad y plataforma Windows

Revisión: 29-09-2026. Se proponen 14 mejoras a partir de lectura estática del repositorio. Se revisaron `README.md`, `GUI_UX_SPEC.md`, `GUI_SPEC.md`, `docs/manual_usuario.md`, P1–P3, la GUI PySide6, el arranque y cliente local, la API FastAPI, la persistencia y el empaquetado. No se ejecutó la aplicación ni se midió su rendimiento: los criterios siguientes son pruebas futuras, no resultados obtenidos.

Las rutas completas son relativas a `Ganttier/`; las abreviadas `gui/`, `api/` y los módulos como `db.py`, `repo.py` o `services.py` parten de `backend/ganttier/`. «Nuevo» identifica un archivo o módulo propuesto. Las referencias externas se consultaron en sus sitios oficiales; la compatibilidad que dependa de una versión o política de TI se marca «a verificar». Las metas numéricas son decisiones de producto propuestas, no requisitos impuestos por esas fuentes.

Esfuerzo para una persona familiarizada con el repositorio, incluida integración y validación: **S ≤1 día, M ≤1 semana, L ≤1 mes, XL >1 mes**. Se usa el límite S de este encargo, aunque P1–P3 empleen otro. Las estimaciones suponen disponibles las dependencias indicadas y no incluyen compras de certificados ni aprobaciones de TI.

## Capacidades existentes y límites del encargo

| Tema | Evidencia y decisión |
|---|---|
| Edición y carga masiva | `api/planning.py::patch_activities` ya ofrece PATCH transaccional; `gui/pages/macro.py::MacroPage.save` guarda ventanas por lotes y el pegado Excel ya valida columnas. P4-02 propone selección y transformación de actividades existentes, no otro importador. |
| Descartar y simular | Control, Macro y Calendarios permiten descartar borradores; existen historial de corridas, líneas base y duplicación. Ninguno constituye una pila general para revertir ediciones ya confirmadas. |
| Persistencia de preferencias | `gui/app.py::MainWindow.closeEvent` guarda geometría, estado de ventana, página y proyecto; también se conserva el tema. No hay catálogo de vistas del Gantt con nombre. |
| Escala y accesibilidad | El Gantt usa arrays, `QAbstractTableModel`, pintado de filas visibles y trabajo en segundo plano; ya existen pruebas de modelo y pintado con 100 000 filas. Hay atajos, nombres accesibles, formularios con ayuda y temas claro/oscuro. P4 amplía cobertura y comportamiento verificable. |
| Base de datos y errores | `db.initialize` ya registra versión 1, usa transacción y rechaza versiones futuras; `db.connect` habilita WAL y espera de bloqueo. `report_error` ya registra fallos y el lanzador crea `ganttier.log` sin consola. Se proponen ampliaciones, no reemplazos por supuesta ausencia. |
| Historia de negocio | Escenarios aislados pertenecen a P1-07; cierres y bases a P3-01/P3-02; bitácora a P3-06. P4-06 trata recuperación técnica y P4-13 diagnóstico de software. No añaden otra historia contractual. |
| Informes y terreno | Plantillas por destinatario y filtros de control pertenecen a P3-10/P3-11; partes y registro sin conexión a P3-08/P2-13. El plano Gantt de P4-04 es una salida distinta de esos informes. |

## Propuestas

### P4-01 Deshacer y rehacer ediciones confirmadas

- **Problema**: `backend/ganttier/gui/pages/activities.py::ActivitiesPage.edit_cell`, `move_activity` y `resize_activity` escriben inmediatamente mediante `mutate`; `_next_mutation` puede ejecutar CPM después. No guardan un comando inverso. Un arrastre accidental obliga a reconstruir la restricción anterior. `save_relation` compensa fallos al reemplazar una relación, pero no ofrece deshacer al usuario.
- **Propuesta**: Incorporar Ctrl+Z/Ctrl+Y con descripción del cambio y pila por proyecto durante la sesión. Primera entrega: nombre, HH, duración, restricciones, ventanas, relaciones y lotes P4-02; capturar valores originales y efectos de programación, con revisión de origen. Ejecutar cada comando y su inverso mediante un servicio transaccional; confirmar la posición de la pila solo tras respuesta de la API, sin hacer REST síncrono desde `QUndoCommand`. Agrupar un lote en un paso y eliminar la rama de rehacer al editar de nuevo. Invalidar comandos incompatibles tras importar, cambiar calendarios o aplicar una corrida. Avances, bases y borrados históricos quedan fuera de esta primera entrega, con alcance visible; sus correcciones seguirán sus flujos específicos.
- **Dónde**: Nuevos `backend/ganttier/gui/commands.py` y `backend/ganttier/edit_commands.py`; `gui/pages/activities.py`, `gui/app.py`, `gui/client.py`, `api/planning.py`, `repo.py`, `services.py`.
- **Valor**: Alto — permite corregir errores de preparación de campañas y restricciones sin reconstruir manualmente el programa de una subestación.
- **Esfuerzo**: L (≤1 mes).
- **Riesgo**: Alto — un inverso incompleto puede dejar fechas derivadas o dependencias inconsistentes; un fallo HTTP no debe adelantar la pila ni repetir escrituras.
- **Criterio de aceptación**: Editar nombre, mover una actividad y cambiar una relación; tres deshacer recuperan las entradas y fechas anteriores, y tres rehacer reproducen el resultado. Un fallo de API conserva la posición de la pila. Una revisión incompatible bloquea la reversión y explica el conflicto; no sobrescribe cambios posteriores.
- **Referencias**: [Qt/PySide6, QUndoStack](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QUndoStack.html), macros y estados de deshacer; [SQLite, transacciones](https://www.sqlite.org/lang_transaction.html). La coordinación asíncrona con REST es responsabilidad de Ganttier.

### P4-02 Cambios masivos con alcance y vista previa

- **Problema**: `backend/ganttier/gui/gantt/view.py::GanttWidget.__init__` configura `SingleSelection`; `ActivitiesPage.edit_cell` modifica una actividad. Aunque `backend/ganttier/api/planning.py::patch_activities` ya valida y guarda una lista atómicamente, no hay un flujo general para cambiar la habilidad o prioridad de una selección existente.
- **Propuesta**: Permitir selección múltiple y «Editar selección», con asignación de nombre, habilidad, calendario, prioridad y HH, más incremento porcentual de HH. Separar «seleccionadas» de «todas las coincidencias» y congelar sus IDs al abrir la vista previa. Mostrar valor anterior/nuevo, filas excluidas y errores; la API debe revalidar revisiones y bloqueos antes de confirmar todo o nada. Reutilizar el PATCH y sus validadores, con un único refresco. Las fechas, avance y regeneración del plan conservan sus flujos especializados. Inicialmente limitar el lote a 1 000 actividades con explicación; ampliarlo tras medir, sin fraccionar silenciosamente su atomicidad.
- **Dónde**: Nuevo `backend/ganttier/gui/gantt/bulk_edit.py`; `gui/gantt/view.py`, `gui/pages/activities.py`, `gui/client.py`, `api/planning.py`, `api/dependencies.py`, `repo.py`.
- **Valor**: Alto — reduce horas de edición repetitiva al preparar campañas de protecciones, termografía o inspección de líneas.
- **Esfuerzo**: M (≤1 semana), limitado a los campos y tamaño indicados; integración con deshacer cuando P4-01 esté disponible.
- **Riesgo**: Medio — una selección oculta o un filtro cambiado puede ampliar el alcance; las validaciones deben estar también en el servidor.
- **Criterio de aceptación**: Sobre 1 000 actividades filtradas, cambiar habilidad afecta exactamente los IDs confirmados y conserva las demás. Una fila inválida o modificada desde la vista previa produce cero escrituras. Un lote mayor informa el límite antes de enviar; el usuario puede cancelar la vista previa sin cambios.
- **Referencias**: [Qt, QAbstractItemModel](https://doc.qt.io/qt-6/qabstractitemmodel.html), modelo y notificaciones; [SQLite, transacciones](https://www.sqlite.org/lang_transaction.html). Base funcional existente: `api/planning.py::patch_activities`.

### P4-03 Vistas guardadas del Gantt por tarea de trabajo

- **Problema**: `backend/ganttier/gui/pages/activities.py::ActivitiesPage.options` reúne búsqueda, criticidad, agrupación WBS, colapso y orden solo en memoria; `refresh` limpia filtros al cambiar de proyecto. Las columnas se ocultan directamente desde el menú y `rebuild_rows` vuelve a ajustar anchos. `MainWindow.closeEvent` no persiste ese conjunto como una vista reutilizable.
- **Propuesta**: Añadir vistas con nombre, duplicación, restablecimiento y estado «modificada»: filtros combinables por instalación, habilidad, desconexión y criticidad; columnas visibles, orden y ancho; agrupación única por WBS, instalación o habilidad; capas y escala temporal. Guardar definiciones versionadas por usuario y proyecto, identificando columnas por clave estable y tratando «sin clasificar» explícitamente. Respetar anchos manuales; ofrecer ajuste automático como acción. Compartir después la definición de filtros con P3-11, sin incorporar cálculos EVM ni plantillas de informe en esta propuesta.
- **Dónde**: Nuevo `backend/ganttier/gui/views.py`; `gui/pages/activities.py`, `gui/gantt/model.py`, `gui/gantt/table.py`, `gui/state.py`; almacenamiento con `QSettings`.
- **Valor**: Alto — permite alternar entre reunión semanal, revisión por instalación y trabajos con desconexión sin reconstruir la pantalla.
- **Esfuerzo**: M (≤1 semana), con agrupación de un nivel salvo la jerarquía WBS existente.
- **Riesgo**: Medio — columnas distintas entre modos y IDs WBS retirados pueden invalidar preferencias; aplicar valores de respaldo sin ocultar datos silenciosamente.
- **Criterio de aceptación**: Guardar tres vistas, cerrar, reabrir y recuperar filtros, columnas, anchos, agrupación y escala. En datos sin cambios se obtienen los mismos IDs. Una vista con una columna retirada se abre con aviso y alternativa válida; cambiar de vista no modifica ninguna actividad.
- **Referencias**: [Qt/PySide6, QSettings](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QSettings.html), persistencia de preferencias; el esquema versionado de vistas es una decisión propia.

### P4-04 Plano Gantt vectorial en A3 y A1

- **Problema**: `backend/ganttier/gui/gantt/view.py::GanttCanvas.paint` dibuja solo las filas y fechas del área visible. `backend/ganttier/reports/pdf.py::write` genera informes A4 mediante tablas y gráficos, sin un plano del Gantt con paginación temporal, cajetín y leyenda. Capturar la pantalla dejaría fuera parte del programa.
- **Propuesta**: Crear «Imprimir/exportar Gantt» con vista previa, A3/A1, orientación, intervalo de fechas, selección/filtro y escala. Componer tabla y barras vectoriales desde una instantánea consistente, independiente del desplazamiento de pantalla. Repetir cabeceras y códigos al dividir horizontalmente, mantener filas completas y señalar relaciones que continúan en otra hoja. Incluir cajetín con proyecto, instalación/alcance, revisión declarada, corte, base, autor y página X/Y, además de leyenda de criticidad, avance, base y ventanas. Proponer paginación antes que reducir a texto ilegible; configurar mínimo de 8 pt. P3-10 podrá aportar identidad visual, pero no es requisito para entregar este plano.
- **Dónde**: Nuevos `backend/ganttier/gui/gantt/printing.py` y `backend/ganttier/gui/gantt/print_dialog.py`; `gui/gantt/model.py`, `gui/pages/activities.py`, `gui/client.py`, `api/planning.py`, `services.py`.
- **Valor**: Alto — entrega planos legibles para reuniones de coordinación y preparación de desconexiones en sala de control.
- **Esfuerzo**: L (≤1 mes).
- **Riesgo**: Medio — desalineación tabla/barras, cortes de vínculos y diferencias de fuentes; la instantánea no debe mezclar revisiones durante una exportación larga.
- **Criterio de aceptación**: Exportar 250 actividades y 18 meses en A3 y A1: dimensiones ISO correctas, cajetín y leyenda en cada hoja, texto seleccionable, cero filas omitidas y cobertura completa del intervalo. Al cambiar el scroll, el contenido exportado no cambia. La previsualización y el PDF tienen igual número de páginas y tipografía ≥8 pt.
- **Referencias**: [Qt/PySide6, QPdfWriter](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QPdfWriter.html) y [Qt, QPageSize](https://doc.qt.io/qt-6/qpagesize.html), tamaños y composición PDF.

### P4-05 Concurrencia explícita y protección frente a escrituras obsoletas

- **Problema**: `backend/ganttier/db.py::connect` usa WAL y `busy_timeout=30000`, pero `repo.py::update` actualiza por ID sin revisión esperada. `gui/state.py::AppState` conserva exclusiones en su propia instancia; dos procesos no las comparten. `gui/app.py::create_window` inicia otro servidor si no recibe URL externa. WAL evita ciertos bloqueos físicos, pero no evita que un formulario antiguo sobrescriba una edición reciente.
- **Propuesta**: Definir como primera modalidad soportada un único proceso propietario por ruta canónica de base local, con bloqueo adquirido antes de migrar o iniciar el scheduler. Un segundo arranque muestra quién ocupa la base y no abre otro escritor. Añadir revisión esperada en operaciones de edición y lotes, comprobada dentro de la transacción, con respuesta 409 y comparación visible ante conflicto; incluir los catálogos que afecten la operación. Distinguir espera por SQLite de conflicto de versión y evitar reintentos ciegos de POST. Para colaboración en red, la decisión futura será una API central autenticada, con la base en disco del servidor; no compartir el archivo WAL por SMB. No se incluye todavía construir el servidor multiusuario.
- **Dónde**: Nuevo `backend/ganttier/instance_lock.py`; `gui/launcher.py`, `gui/server.py`, `main.py`, `db.py`, `schema.sql`, `repo.py`, `api/dependencies.py`, `api/planning.py`, `gui/client.py`, `gui/state.py`.
- **Valor**: Alto — evita pérdida silenciosa de revisiones del plan y dobles procesos sobre los mismos compromisos de mantenimiento.
- **Esfuerzo**: L (≤1 mes); requiere P4-07 para agregar revisiones persistentes.
- **Riesgo**: Alto — todos los puntos de escritura deben participar; un bloqueo cooperativo no protege frente a programas externos que lo ignoren ni sustituye las transacciones.
- **Criterio de aceptación**: Dos procesos dirigidos a la misma base producen un solo propietario y un solo scheduler. Tras cierre anormal se recupera el bloqueo sin forzar uno vivo. Dos peticiones con revisión N: la primera confirma N+1; la segunda obtiene 409 y deja intacto el cambio. Una base ocupada devuelve un error distinguible y conserva el borrador.
- **Referencias**: [SQLite, WAL](https://www.sqlite.org/wal.html), un escritor y limitación de uso sobre sistemas de archivos de red; [Qt, QLockFile](https://doc.qt.io/qt-6/qlockfile.html), bloqueo cooperativo y detección de bloqueos obsoletos. Adaptación para el arranque sin GUI: a verificar.

### P4-06 Respaldos automáticos y puntos de recuperación verificables

- **Problema**: El manual exige copiar la base con Ganttier cerrado. `backend/ganttier/db.py::connect` trabaja en WAL, sin servicio de respaldo. `repo.py::duplicate_project` conserva bases pero excluye historial de corridas; `services.py::_save_run` guarda resultados fuera de SQLite. Duplicar un proyecto o copiar únicamente el `.db` abierto no proporciona una recuperación integral.
- **Propuesta**: Añadir copias mediante la API de respaldo SQLite, programadas mientras la aplicación esté abierta, al recuperar un período vencido y antes de importar en reemplazo, migrar o actualizar. Cada punto tendrá fecha, motivo, versiones de aplicación/esquema, manifiesto, huellas y estado de verificación; incluir los archivos de corridas e informes referenciados. Coordinar snapshot y retención de archivos bajo una barrera breve de escrituras y verificar referencias al terminar. Retener, por ejemplo, siete copias diarias y cuatro semanales con cuota configurable. Restaurar primero a una ubicación de revisión; el reemplazo de la base activa requiere cerrar conexiones, detener tareas y conservar un punto previo. Primera entrega: respaldo del espacio completo, rotulado como tal; no restauración selectiva que altere calendarios globales inadvertidamente.
- **Dónde**: Nuevo `backend/ganttier/backups.py` y diálogo `gui/backup_dialog.py`; `db.py`, `services.py`, `autoreports.py`, `gui/launcher.py`, `gui/app.py`.
- **Valor**: Alto — permite recuperar semanas de planificación y avance tras un error de importación, daño de disco o actualización fallida; una copia en el mismo disco solo cubre parte de esos casos.
- **Esfuerzo**: L (≤1 mes).
- **Riesgo**: Alto — inconsistencias entre base y archivos, espacio insuficiente y restauración sobre conexiones abiertas. Ofrecer exportar el respaldo completado a otro soporte.
- **Criterio de aceptación**: Con escrituras WAL activas, obtener una copia que pase `integrity_check` y `foreign_key_check`, abra sin errores y resuelva todas sus referencias a corridas e informes. Restaurarla recupera IDs, actividades, avances y bases del snapshot. Simular disco lleno no daña el original ni elimina el último respaldo válido; mostrar fecha de la última copia verificada.
- **Referencias**: [SQLite, Online Backup API](https://www.sqlite.org/backup.html), copia consistente de una base activa; [SQLite, WAL](https://www.sqlite.org/wal.html), relación entre base y archivos auxiliares. El protocolo de archivos externos es adicional a SQLite.

### P4-07 Migraciones incrementales con ensayo de recuperación

- **Problema**: `backend/ganttier/db.py::initialize` ya aplica el esquema inicial dentro de `BEGIN IMMEDIATE` y rechaza una versión superior a `SCHEMA_VERSION=1`. Sin embargo, solo existe el salto a versión 1; una base ya marcada como 1 no recibe cambios posteriores por editar `schema.sql`. Las extensiones persistentes de P1–P4 necesitan una cadena de evolución comprobable.
- **Propuesta**: Mantener la protección actual y agregar pasos ordenados N→N+1, registro de versión y huella del paso, validaciones previas/posteriores y actualización de versión solo al confirmar. Construir las bases nuevas por la misma cadena o comprobar equivalencia con el esquema consolidado. No usar `CREATE TABLE IF NOT EXISTS` como sustituto de cambios de columnas. Ejecutar con propietario exclusivo, respaldo previo P4-06 y mensaje de progreso; si falta ese componente, exigir un respaldo técnico equivalente antes de habilitar una migración real. No intentar bajar el esquema automáticamente: volver al binario anterior con su copia compatible. Mantener fixtures de cada versión liberada.
- **Dónde**: Nuevo `backend/ganttier/migrations/`; `db.py`, `schema.sql`, `main.py::create_app`, `gui/launcher.py`, `ganttier_gui.spec`; pruebas futuras de persistencia en `backend/tests/`.
- **Valor**: Alto — protege los planes existentes al incorporar activos, revisiones y otras mejoras sin pedir al usuario recrear su base.
- **Esfuerzo**: M (≤1 semana) para infraestructura y una migración representativa; futuras transformaciones de negocio se estiman aparte.
- **Riesgo**: Alto — reconstrucciones de tablas, claves foráneas y límites transaccionales de `executescript`; un esquema parcialmente actualizado puede impedir abrir la aplicación.
- **Criterio de aceptación**: Bases vacía y v1 llegan al mismo esquema objetivo sin perder registros ni relaciones; reiniciar no repite transformaciones. Un fallo inducido a mitad del paso deja versión y contenido anteriores recuperables. Una base de versión futura se rechaza sin aplicar DDL ni cambiar sus datos.
- **Referencias**: [SQLite, ALTER TABLE y procedimiento de cambios generales](https://www.sqlite.org/lang_altertable.html); conservar las garantías ya presentes en `db.initialize`.

### P4-08 Actualizaciones verificadas y aplazables

- **Problema**: `backend/ganttier/gui/app.py::MainWindow.show_about` informa versión y URL, pero no ofrece actualización; `gui/launcher.py::main` solo configura y arranca. `construir_exe.bat` entrega una carpeta onedir que se reemplaza manualmente. Es fácil mezclar un ejecutable nuevo con `_internal` antiguo o actualizar durante una corrida.
- **Propuesta**: Añadir comprobación automática opcional de versiones y descarga en segundo plano, con canal estable, notas de cambio, tamaño y compatibilidad de esquema. Instalar únicamente cuando el usuario lo solicite y terminen las operaciones; permitir aplazar indefinidamente y usar un paquete sin conexión. Validar manifiesto firmado, huella, editor esperado y firma del paquete P4-09; una huella descargada del mismo origen no basta por sí sola. Reemplazar la distribución completa mediante el instalador, conservando la anterior y el respaldo P4-06. Si el esquema cambió, la recuperación combina binario y datos compatibles; no abre la base nueva con el ejecutable viejo.
- **Dónde**: Nuevo `backend/ganttier/gui/updates.py`; `gui/app.py`, `gui/launcher.py`, `construir_exe.bat`; nuevo manifiesto de publicación y configuración del instalador.
- **Valor**: Medio — simplifica mantener varios equipos al día, pero depende de contar con publicaciones, firmas y recuperación confiables.
- **Esfuerzo**: L (≤1 mes), después de P4-06/P4-07/P4-09.
- **Riesgo**: Alto — actualización interrumpida, paquetes incompatibles y cadena de distribución; nunca reemplazar archivos en uso ni perder borradores.
- **Criterio de aceptación**: Firma incorrecta, manifiesto alterado o descarga truncada impiden instalar sin cambiar la versión activa. Durante una importación la instalación queda pendiente. Una actualización válida conserva proyectos y preferencias; una interrupción recupera la distribución anterior y su base compatible. Sin red, todas las funciones locales siguen disponibles.
- **Referencias**: [Microsoft, SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool) y [rollback de Windows Installer](https://learn.microsoft.com/en-us/windows/win32/msi/rollback-installation). [App Installer/MSIX](https://learn.microsoft.com/en-us/windows/msix/app-installer/auto-update-and-repair--overview) es una alternativa si se elige MSIX, no una capacidad automática del onedir/MSI; políticas corporativas y versión de Windows: a verificar.

### P4-09 Distribución Windows firmada e instalable

- **Problema**: `ganttier_gui.spec`, en los bloques `EXE` y `COLLECT`, produce una distribución onedir sin etapa de firma ni instalador; `construir_exe.bat` termina pidiendo distribuir toda la carpeta. `backend/ganttier/gui/launcher.py::configure_runtime` ya separa datos en `%LOCALAPPDATA%`, pero copiar solo el `.exe` puede romper el arranque y no hay reparación/desinstalación gestionada.
- **Propuesta**: Empaquetar el onedir completo en un MSI versionado con accesos directos, reparación, actualización mayor y desinstalación que conserve datos de usuario por defecto. Firmar el ejecutable propio y el MSI con Authenticode y sello de tiempo, verificar la firma durante publicación y custodiar las claves fuera del repositorio. Elegir una sola vía inicial: MSI para el alcance propuesto; evaluar MSIX solo si TI necesita su administración y se valida el servidor local, las rutas y la actualización. Mantener disponible el ZIP onedir para evaluación sin instalación. Registrar un identificador de compilación y dependencias resueltas para soporte.
- **Dónde**: `ganttier_gui.spec`, `construir_exe.bat`, `backend/ganttier/gui/launcher.py`, `gui/app.py::MainWindow.show_about`; nuevos archivos de empaquetado en `packaging/windows/` y automatización de publicación.
- **Valor**: Medio — facilita el despliegue confiable en estaciones de planificación corporativas y evita instalaciones incompletas.
- **Esfuerzo**: L (≤1 mes), con certificado y política de instalación disponibles.
- **Riesgo**: Medio — permisos, antivirus, rutas y upgrades; una firma válida no garantiza por sí sola ausencia de avisos de reputación o aprobación corporativa.
- **Criterio de aceptación**: En una VM Windows limpia sin Python, instalar, iniciar, reparar, actualizar y desinstalar satisfactoriamente. La verificación Authenticode identifica al editor esperado y detecta un binario manipulado. La base y los respaldos permanecen tras desinstalar; reinstalar permite abrirlos. No se exige escritura en el directorio de binarios durante uso normal.
- **Referencias**: [Microsoft, SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool) y [opciones estándar de Windows Installer](https://learn.microsoft.com/en-us/windows/win32/msi/standard-installer-command-line-options). Certificado, confianza, instalación por usuario/equipo y políticas de TI: a verificar en el entorno destino.

### P4-10 Actualización incremental y presupuesto de respuesta con 100 000 actividades

- **Problema**: La virtualización ya existe en `backend/ganttier/gui/gantt/view.py::GanttCanvas.paint`, y `backend/tests/gui/test_activities.py` incluye `test_build_100000_under_one_second` y `test_canvas_100000_virtualized_frame_and_drag`. La brecha está en el recorrido completo: `ActivitiesPage._next_mutation` publica `bump_schedule`; `refresh` descarga todo el columnar, WBS y relaciones, y `rebuild_rows` reconstruye índices. `ControlPage.refresh` también descarga el columnar completo. Una edición simple puede generar trabajo proporcional a todo el plan.
- **Propuesta**: Medir por separado SQLite, serialización/transferencia, conversión, modelo y primer pintado. Para cambios locales, responder con revisión y filas afectadas y actualizar solo sus datos mediante señales del modelo; usar invalidación completa tras cambios globales de programa. Compartir snapshots inmutables entre páginas y descartar resultados antiguos; agrupar solicitudes de filtro para evitar colas de reconstrucciones. Mantener GUI→API y `run_task`, sin acceso directo a SQLite desde widgets. Incorporar un benchmark del `.exe` real con carga, búsqueda, edición, cambio de proyecto y memoria; optimizar según esa evidencia, conservando el pintado existente.
- **Dónde**: `backend/ganttier/gui/pages/activities.py`, `gui/pages/control.py`, `gui/state.py`, `gui/gantt/model.py`, `gui/gantt/table.py`, `gui/client.py`, `api/planning.py`, `repo.py`; ampliar `backend/tests/gui/test_activities.py` y añadir benchmark de distribución.
- **Valor**: Alto — mantiene usable el plan anual masivo durante la revisión y corrección diaria, más allá de desplazar barras con fluidez.
- **Esfuerzo**: L (≤1 mes).
- **Riesgo**: Alto — cachés obsoletas, selección perdida y cambios globales mal clasificados; las actualizaciones incrementales deben conservar coherencia de WBS y relaciones.
- **Criterio de aceptación**: En Windows con SSD, 16 GB RAM y CPU de al menos cuatro núcleos, registrar modelo exacto, versiones, resolución, DPI y fixture de 100 000 actividades/100 000 relaciones. En 20 repeticiones: carga inicial ≤10 s, búsqueda p95 ≤500 ms, edición de nombre hasta confirmación visible p95 ≤1 s y pintado p95 ≤50 ms; proceso ≤1,5 GB de memoria privada. Editar un nombre no vuelve a descargar todo el columnar. Publicar medición base y final; estos objetivos quedan pendientes de comprobar.
- **Referencias**: [Qt, QAbstractItemModel](https://doc.qt.io/qt-6/qabstractitemmodel.html), notificaciones incrementales y afinidad de hilo; pruebas locales citadas como punto de partida, no como mediciones realizadas en esta revisión.

### P4-11 Accesibilidad verificable de tablas y gráficos

- **Problema**: `backend/ganttier/gui/gantt/view.py::GanttCanvas.__init__` asigna un nombre accesible y `keyPressEvent` permite navegar filas, pero `paint` representa criticidad, ventanas y relaciones en dibujo personalizado. `gui/gantt/table.py::ActivityTableModel.data` expone textos y colores, sin una descripción conjunta de esos estados. `gui/theme.py::apply_theme` ofrece dos paletas y `ui_font` fija el tamaño base; esto no acredita contraste alto ni lectura del contenido gráfico.
- **Propuesta**: Completar el recorrido por teclado y ofrecer en tabla/detalle una alternativa textual equivalente del Gantt y perfiles: fechas, criticidad, base, ventanas, predecesoras y sobrecarga. Añadir descripciones accesibles y avisos de cambios/errores; evitar un objeto accesible por cada actividad si la tabla virtual puede exponerla bajo demanda. Incorporar preferencia de fuente y alto contraste, foco perceptible y símbolos/texto además de color. Probar con Narrador de Windows y NVDA, en los modos claro, oscuro y alto contraste; conservar los atajos y nombres ya implementados.
- **Dónde**: `backend/ganttier/gui/gantt/view.py`, `gui/gantt/table.py`, `gui/gantt/profile.py`, `gui/theme.py`, `gui/widgets/common.py`, `gui/gantt/dialogs.py`, `gui/app.py`.
- **Valor**: Alto — permite jornadas prolongadas, uso con baja visión y revisión de criticidad sin depender del color o del ratón.
- **Esfuerzo**: L (≤1 mes).
- **Riesgo**: Medio — diferencias entre lectores y versiones Qt/Windows, y regresiones de densidad y alineación al ampliar fuentes.
- **Criterio de aceptación**: Crear, seleccionar, editar, relacionar y consultar una actividad sin ratón ni trampas de foco. Narrador y NVDA anuncian código, fechas, criticidad y errores en una muestra de 20 casos. A 200 % de escala no se ocultan acciones esenciales; contraste de texto normal ≥4,5:1 y controles/estados gráficos esenciales ≥3:1 en los perfiles evaluados, con criterio aplicable y excepciones documentados.
- **Referencias**: [Qt, accesibilidad](https://doc.qt.io/qt-6/accessible.html); [WCAG 2.2](https://www.w3.org/TR/WCAG22/), criterios 1.4.1, 1.4.3, 1.4.11, 2.1.1 y 2.4.7 como guía medible. No se declara certificación WCAG de la aplicación nativa; compatibilidad de lectores con la versión empaquetada: a verificar.

### P4-12 Ayuda contextual disponible sin conexión

- **Problema**: `backend/ganttier/gui/app.py::MainWindow.show_shortcuts` usa F1 para una tabla de atajos y `show_about` muestra versión/API. `gui/widgets/common.py::FormDialog.__init__` ya admite `Field.help`, pero no integra temas del manual por pantalla; `ganttier_gui.spec` empaqueta plantillas y ejemplos, no `docs/manual_usuario.md`. El usuario del `.exe` puede no tener la explicación de corte, ventanas o diferencias entre los modos.
- **Propuesta**: Incluir una ayuda local con índice, búsqueda y enlaces desde cada página y campo de interpretación delicada. F1 abre el tema activo y mantiene «Atajos» como acción visible; Shift+F1 o «¿Qué significa?» explica conceptos breves. Empezar por diez temas: modos, corte, duración laboral, HH/dotación, ventanas, restricciones, bases, simulación/aplicación, importación y recuperación. Derivar contenido de la misma fuente del manual y mostrar la versión; cada error conocido enlaza su corrección, sin inventar diagnósticos de factibilidad reservados a P1-06.
- **Dónde**: Nuevo `backend/ganttier/gui/help.py` y recursos locales de ayuda; `gui/app.py`, `gui/pages/base.py`, `gui/widgets/common.py`, `ganttier_gui.spec`, `docs/manual_usuario.md` en una futura implementación.
- **Valor**: Medio — reduce interpretaciones erróneas y consultas de soporte al incorporar nuevos planificadores eléctricos.
- **Esfuerzo**: M (≤1 semana).
- **Riesgo**: Bajo — contenido desactualizado o enlaces rotos; mantener una sola fuente y comprobar destinos al empaquetar.
- **Criterio de aceptación**: En una instalación sin red, F1 abre el tema correspondiente en diez pantallas/controles acordados; buscar «fecha de corte» encuentra su regla y ejemplo. Todos los enlaces internos resuelven y la versión coincide con la aplicación. El usuario puede abrir la lista de atajos en dos acciones como máximo.
- **Referencias**: [Qt, QWhatsThis](https://doc.qt.io/qt-6/qwhatsthis.html), ayuda contextual; [manual de Ganttier](../manual_usuario.md), fuente funcional existente.

### P4-13 Diagnóstico local con rotación y paquete de soporte

- **Problema**: `backend/ganttier/gui/launcher.py::configure_runtime` abre `ganttier.log` en modo append cuando faltan stdout/stderr, sin rotación. `gui/widgets/common.py::report_error` registra excepciones y `show_error` muestra detalles, mientras `gui/client.py::ApiClient.request` transforma fallos HTTP en `ApiError` sin identificador compartido con el servidor. No hay un paquete reproducible para investigar un fallo del `.exe` sin copiar datos de planificación.
- **Propuesta**: Configurar logging una sola vez, con rotación y registros estructurados de hora, compilación, versión de esquema, operación, duración e ID de correlación GUI/API. Capturar excepciones no controladas de Python y mensajes Qt evitando bucles del manejador. Añadir «Diagnóstico» con ubicación del registro, estado del servidor, último error y exportación local de soporte. Excluir tokens, cuerpos de peticiones, nombres de trabajadores y archivos de proyectos por defecto; permitir revisar lo exportado. Medir solo fallos y tiempos locales, sin transmisión automática. La bitácora de modificaciones del programa sigue en P3-06.
- **Dónde**: Nuevo `backend/ganttier/diagnostics.py`; `gui/launcher.py`, `gui/app.py`, `gui/widgets/common.py`, `gui/client.py`, `main.py`, `gui/server.py`.
- **Valor**: Medio — acorta la recuperación ante errores durante el cierre semanal y permite soporte sin entregar el programa completo.
- **Esfuerzo**: M (≤1 semana).
- **Riesgo**: Medio — filtración accidental de datos en excepciones, recursión del logger y crecimiento del disco; los dumps nativos quedan fuera de la primera entrega.
- **Criterio de aceptación**: Provocar un error de API y uno de callback: cada uno aparece con ID, versión y traza útil, sin cerrar la GUI. Generar más de 50 MB mantiene un máximo de cinco archivos de 10 MB, incluido el activo. El paquete de soporte no incluye base, cuerpos ni token de prueba; su generación no realiza conexiones externas.
- **Referencias**: [Python, logging.handlers y RotatingFileHandler](https://docs.python.org/3/library/logging.handlers.html), rotación; política de datos y correlación propuesta para Ganttier.

### P4-14 Autenticar la API local y reservar el puerto de arranque

- **Problema**: `backend/ganttier/main.py::create_app` configura CORS para el antiguo frontend y monta el router sin autenticación; `gui/client.py::ApiClient.__init__` no envía credencial. `gui/server.py::free_port` cierra el socket tras comprobar disponibilidad y `EmbeddedServer.start` acepta cualquier HTTP 200 en `/api/health`: hay una ventana entre comprobación y enlace, y la salud no verifica la identidad de esa instancia. El arranque alternativo `main.py::main` ya conserva un listener, solución aprovechable.
- **Propuesta**: Generar un token aleatorio de sesión de al menos 256 bits, pasarlo en memoria al servidor y cliente y exigirlo en lecturas y escrituras de datos. Compararlo de forma segura y enviarlo en cabecera, nunca en URL, logs, argumentos o preferencias. Reservar el socket hasta entregarlo a uvicorn, escuchar exclusivamente en loopback y comprobar salud autenticada con ID de instancia. Deshabilitar o proteger docs/OpenAPI en distribución; configurar Host/Origin permitidos para el modo local, sin tratar CORS como autenticación. La URL externa requiere una configuración diferenciada con TLS, identidad y autorización de servidor; el token efímero local no habilita uso multiusuario. No se pretende proteger frente a un administrador o proceso con acceso completo a la memoria del usuario.
- **Dónde**: Nuevo `backend/ganttier/api/security.py`; `gui/server.py`, `gui/client.py`, `gui/app.py::create_window`, `main.py::create_app`, `api/__init__.py` y rutas de salud/archivos.
- **Valor**: Alto — protege programas, HH y datos de trabajadores frente a solicitudes locales sin autorización y evita conectar la GUI a un servicio equivocado.
- **Esfuerzo**: M (≤1 semana), para el modo embebido; la autenticación corporativa remota se estima aparte.
- **Riesgo**: Medio — romper clientes/pruebas o el chequeo de arranque; todas las rutas de datos y descargas deben quedar cubiertas sin exponer el token para depuración.
- **Criterio de aceptación**: GET, POST y descarga de datos sin token o con token de otra sesión devuelven 401/403 sin efecto. La GUI funciona con la credencial correcta y un reinicio invalida la anterior. Un servicio ajeno que responda 200 no supera el chequeo. Con el puerto preferido ocupado, se usa un socket propio alternativo; no aparece escucha en `0.0.0.0` ni el token en registros, URLs o línea de comandos.
- **Referencias**: [FastAPI, herramientas de seguridad HTTPBearer/APIKeyHeader](https://fastapi.tiangolo.com/reference/security/) y [Python, secrets](https://docs.python.org/3/library/secrets.html). La validación del secreto debe implementarse además de extraer la cabecera; entrega del socket a la versión empaquetada de uvicorn: a verificar, con antecedente local en `main.py::main`.

## Descartes y dependencias

**Modo portable estricto: pospuesto.** `gui/launcher.py::configure_runtime` ya admite `GANTTIER_DATA_DIR` y `GANTTIER_DB`, y el onedir funciona sin instalación. Eso no equivale a portabilidad sin rastros: `MainWindow` y `AppState` usan `QSettings` nativo y `theme._check_image` escribe en el directorio temporal. Un modo explícito necesitaría preferencias INI locales, rutas relativas coherentes, gestión de temporales y prueba de soporte extraíble. Su valor inicial es bajo frente a recuperación y concurrencia; no se propone como capacidad inexistente ni como un simple cambio de ruta. Tampoco se recomienda trabajar sobre un `.db` activo en una carpeta sincronizada; trasladar copias completadas es un flujo distinto.

**Servidor multiusuario completo: pospuesto.** `GANTTIER_API_URL` resuelve conectividad, no usuarios, permisos, conflictos ni operación del servicio. Primero P4-05 y P4-14; si existe demanda real, estimar un proyecto XL para servidor administrado, autenticación, autorización por proyecto, copias centrales y evaluación de carga. Mantener SQLite local al servidor puede bastar para concurrencia reducida; decidir PostgreSQL con mediciones, no por el número de actividades solamente. No se propone abrir el puerto embebido a la red ni compartir WAL por SMB.

**No repetir funciones existentes.** No se agrega otro asistente, pegado Excel, paleta de comandos, tema oscuro, motor de informes ni virtualización desde cero. Tampoco se incluyen nueva gestión de activos, cierres, escenarios o captura en terreno: sus alcances están en P1–P3. Las «versiones» de P4-06 son puntos técnicos de recuperación del espacio completo, no una comparación contractual de versiones del proyecto.

P4-06/P4-07 se preparan conjuntamente antes de liberar una migración real. P4-05 aporta exclusión y revisiones reutilizables por P4-01/P4-02; una primera entrega de edición puede usar verificaciones de estado equivalentes hasta disponer de ese contrato. P4-10 debe integrar las invalidaciones de P4-03 y P4-05. P4-08 se habilita después de firma, copias y migración; P4-13 permite diagnosticar el proceso. Los esfuerzos no se suman sin descontar infraestructura compartida.

## Tabla resumen

Orden: **valor alto antes de medio; dentro de cada valor, esfuerzo M antes de L; empate por ID**. No se fabrica una razón numérica entre categorías ordinales. El top-5 siguiente añade urgencia y dependencias a este orden de costo/valor.

| ID | Título | Valor | Esfuerzo | Riesgo |
|---|---|---|---|---|
| P4-02 | Cambios masivos con alcance y vista previa | Alto | M | Medio |
| P4-03 | Vistas guardadas del Gantt por tarea de trabajo | Alto | M | Medio |
| P4-07 | Migraciones incrementales con ensayo de recuperación | Alto | M | Alto |
| P4-14 | Autenticar la API local y reservar el puerto de arranque | Alto | M | Medio |
| P4-01 | Deshacer y rehacer ediciones confirmadas | Alto | L | Alto |
| P4-04 | Plano Gantt vectorial en A3 y A1 | Alto | L | Medio |
| P4-05 | Concurrencia explícita y protección frente a escrituras obsoletas | Alto | L | Alto |
| P4-06 | Respaldos automáticos y puntos de recuperación verificables | Alto | L | Alto |
| P4-10 | Actualización incremental y presupuesto de respuesta con 100 000 actividades | Alto | L | Alto |
| P4-11 | Accesibilidad verificable de tablas y gráficos | Alto | L | Medio |
| P4-12 | Ayuda contextual disponible sin conexión | Medio | M | Bajo |
| P4-13 | Diagnóstico local con rotación y paquete de soporte | Medio | M | Medio |
| P4-08 | Actualizaciones verificadas y aplazables | Medio | L | Alto |
| P4-09 | Distribución Windows firmada e instalable | Medio | L | Medio |

## Top-5 justificado

1. **P4-06 — Respaldos y recuperación.** Protege el activo principal del planificador: inventario, fechas y avance. Es previo a migraciones y distribución continua; entregar primero copia consistente y restauración ensayada, antes de ampliar retenciones.
2. **P4-14 — API local autenticada.** Cierra una exposición concreta con esfuerzo M y reutiliza el patrón de socket ya presente en el arranque alternativo. Debe preceder a distribuir el ejecutable a más puestos o considerar acceso remoto.
3. **P4-07 — Migraciones incrementales.** Permite incorporar las mejoras persistentes de las cuatro propuestas sin abandonar bases existentes. Preparar el runner junto con P4-06 y no liberar transformaciones sin recuperación comprobada.
4. **P4-02 — Edición masiva revisable.** Aporta ahorro inmediato al preparar campañas y aprovecha un endpoint transaccional existente. Su alcance acotado evita convertir la primera entrega en un editor de fórmulas o un nuevo importador.
5. **P4-10 — Respuesta de extremo a extremo.** La promesa de 100 000 actividades exige medir carga y correcciones reales en el `.exe`, no solo pintado. Adelantar la medición y aplicar las optimizaciones donde se confirme el costo.

Si ya se abren varias instancias sobre la misma base, **P4-05 sustituye a P4-10 en este primer bloque** por riesgo de pérdida de cambios. P4-03 puede adelantarse como mejora rápida de uso diario; P4-04 sube de prioridad si se exige entregar planos A1/A3 al mandante. El instalador y las actualizaciones quedan detrás de protección y recuperación de datos.
