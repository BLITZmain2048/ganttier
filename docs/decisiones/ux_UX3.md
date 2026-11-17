# UX3 · Pegar desde Excel y lista de tareas → programa

Fecha: 28-09-2026. Contratos: GUI_UX_SPEC.md y GUI_SPEC.md.

## Alcance
- Solo se crearon `gui/widgets/paste.py`, `gui/wizard.py`,
  `tests/gui/test_ux3.py` (bajo backend) y este documento.
- `parse_table` usa CSV con tabulador: admite comillas escapadas, CRLF,
  saltos dentro de celdas y líneas vacías finales.
- `PasteDialog` permite corregir celdas, detectar/desactivar encabezados,
  mapear columnas e ignorarlas. Conserva las correcciones al reasignar columnas.
- Sinónimos normalizados sin tildes, mayúsculas, espacios ni guiones bajos.
- Números finitos, fechas chilenas/ISO, meses y booleanos se convierten antes del POST.
- Se bloquea Aceptar ante cualquier celda inválida, campo obligatorio ausente
  o mapeo duplicado; nunca se descarta silenciosamente un dato inválido.
- Código, nombre y duración son obligatorios; también se respeta Field.required.
- Predecesoras FS/SS/FF/SF, separadas por coma o punto y coma, con desfases.
  Se preservan códigos como A-01: desfases negativos se escriben `A FS-2` o `A -2`.

## Asistente
- API pública: `build_wizard(state, parent=None, mode="proyecto")` y
  `run_task_list_wizard(...)`, que ejecuta exec() y devuelve el ID creado o None.
- Cinco páginas ModernStyle, sin imágenes: proyecto, recursos, tareas,
  objetivo y generación. Tablas editables con modelos públicos `.model`.
- Validación de duplicados, referencias, ciclos sin recursión y duraciones;
  las habilidades sin recurso son advertencias. Excel se lee con openpyxl
  en segundo plano desde Actividades o Plan_Maestro, usando el mismo pegado.
- Toda creación y resolución se ejecuta en run_task con una instantánea de datos.
  El progreso vuelve por señales Qt; los POST no se reintentan automáticamente.
- Fallos parciales conservan el ID y los conteos de creaciones confirmadas,
  notifican projects_changed y permiten abrir el proyecto sin repetir la generación.
- Al resolver se abre el proyecto con AppState.open_project_async para evitar
  el REST síncrono de open_project; estados simulados conservan el fallback público.
- Botón Abrir en Gantt llama parent.go("gantt"). Cancelar/cerrar se bloquea
  mientras se crean datos; no se puede retroceder y duplicar un proyecto ya creado.
- Uso defensivo de button(icon=...), StatusBanner, setup_table y fit_columns.
  Colores de errores desde tokens().critical e íconos desde widgets.icons.icon.

## Límites del contrato actual de la API
- Los recursos globales seleccionados se reutilizan sin copiarlos ni modificarlos.
  La API incluye TODOS los globales en el solver y no admite excluirlos por proyecto;
  las casillas sirven para la revisión de recursos/habilidades. La interfaz lo explica.
- Sin recursos se requiere confirmación y se envía respect_resource_limits=False.
  En mantenimiento el solver fuerza asignación concreta y puede declarar infactibilidad;
  se avisa en Objetivo. Resolverlo requiere un cambio de motor fuera del alcance UX3.

## Verificación
- El Python principal no tiene Qt, pero `.venv/Scripts/python.exe` sí lo tiene.
- 23 pruebas pytest-qt offscreen aprobadas, con caché y bytecode desactivados.
- API embebida real con SQLite compartido en memoria: 20 tareas con 19 relaciones
  y dos cuadrillas; seis tareas periódicas con ocurrencias y fechas programadas.
  Ambos programas factibles y abiertos; todas las peticiones fuera del hilo GUI.
- También: fallo parcial, plantilla en memoria, mapeo, conversiones, validación,
  cadena de 2500 tareas y ausencia de atributos que oculten métodos Qt.
- py_compile de los tres archivos Python, sin escribir archivos .pyc.
