# GUI_UX_SPEC — Ciclo de mejora visual y de flujo de trabajo (28-09-2026)

Objetivo pedido por el usuario (ingeniero eléctrico, planificador): **aspecto visual moderno y limpio** (barra lateral,
tarjetas, íconos, espaciado cómodo, pero tablas y Gantt densos) y **mejor flujo de trabajo** (menos clics, asistente
«lista de tareas → programa», pegar desde Excel, cambio rápido de proyecto, paleta de comandos, estados vacíos que guían).
Idioma: español neutro. Formato chileno de fechas (dd-mm-aaaa) y números (1.234,5). Referencia estética: Linear / Notion /
Microsoft Planner moderno; NO recargado; densidad de tabla ~30 px por fila.

Lee también `GUI_SPEC.md` (arquitectura: GUI → ApiClient → API local; `run_task` obligatorio para REST; `Page`).

## Reglas para todos los agentes

1. Solo modificas los archivos de tu encargo. Si necesitas algo de otro archivo, usa la API de este contrato (con `getattr`
   defensivo si la implementa otro agente en paralelo) y anótalo en tu resumen.
2. **No rompas las pruebas existentes** en `backend/tests/gui/`: no renombres atributos o métodos públicos que esas pruebas usan
   (búscalos con grep antes de cambiar nada: p. ej. `page.search`, `page.model`, `page.rows`, `page.generate()`,
   `page.metric_combo`, `page.data_date`, `page.save_progress()`, etc.). Puedes agregar, no quitar.
3. Nunca asignes atributos con nombre de método Qt (`self.metric`, `self.style`, `self.update`, `self.x`, `self.sort` en
   modelos, etc.): existe una prueba que lo detecta (`tests/gui/test_sombras_qt.py`).
4. Todo color sale de `theme.tokens()`; todo ícono de `widgets.icons.icon(nombre)`; nada de colores fijos en código de páginas.
5. No hay PySide6 en tu entorno: verifica con `python -m py_compile` y revisa con cuidado la API de PySide6 6.x.
   Claude ejecuta las pruebas pytest-qt (offscreen) y toma capturas.
6. Agrega pruebas pytest-qt en el archivo nuevo indicado en tu encargo y resume tus cambios en `docs/decisiones/ux_<ID>.md`
   (≤ 60 líneas).

## API compartida (contrato)

### Íconos — `gui/widgets/icons.py` (YA EXISTE, lo escribió Claude)
- `icon(name, color=None, size=18, *, disabled_color=None, active_color=None) -> QIcon`
- `pixmap(name, color=None, size=18) -> QPixmap`; `available() -> list[str]`; `clear_cache()`; `ICON_SIZE = QSize(18, 18)`.
- Nombres disponibles (Lucide): house layout-dashboard folder-open folder-plus file-spreadsheet chart-gantt calendar
  calendar-range calendar-days calendar-clock users user hard-hat wrench zap activity trending-up chart-line chart-column
  chart-no-axes-combined file-text file-down file-up upload download share-2 play wand-sparkles sparkles sliders-horizontal
  settings search plus pencil trash-2 copy save refresh-cw x check circle-check triangle-alert info circle-help sun moon
  panel-left-close panel-left-open chevron-right chevron-down command keyboard clipboard-paste list-checks layers git-branch
  link flag target gauge history undo-2 filter eye printer table clock timer rocket list-todo network briefcase building-2
  cable award circle-x circle-alert arrow-right arrow-left external-link grip-vertical ellipsis

### Tema — `gui/theme.py` (encargo UX1)
- Se conservan `Tokens`, `LIGHT`, `DARK`, `tokens()`, `qcolor()`, `apply_theme(app, name)`, `stylesheet(t)`.
- Nuevas constantes: `SPACE = dict(xs=4, sm=8, md=12, lg=16, xl=24)`, `RADIUS = 8`, `RADIUS_LG = 12`,
  `FONT_SIZES = dict(caption=8.5, body=9.5, title=15, h2=11.5, kpi=20)` (pt), `ROW_HEIGHT = 30`.
- `ui_font() -> QFont`: «Segoe UI» en Windows, «Inter»/«Noto Sans»/«DejaVu Sans» como respaldo; 9,5 pt.
- `apply_theme` llama `icons.clear_cache()` y emite `theme_changed` de un objeto global `theme_signals` (QObject con
  `Signal(str)`), para que páginas y widgets repinten íconos.
- Propiedades QSS reconocidas (setProperty): botones `kind` = `primary` | `secondary` | `ghost` | `danger`;
  QLabel `role` = `title` | `subtitle` | `caption` | `muted` | `section`; QFrame `card=true`; QLabel `badge` = `true` |
  `proyecto` | `mantenimiento` | `good` | `warning` | `critical`; QWidget `toolbar=true`; `emptystate=true`.
  (Se mantienen las propiedades antiguas `primary`/`danger`/`card`/`cardTitle`/`badge` por compatibilidad.)

### Widgets comunes — `gui/widgets/common.py` (encargo UX1)
- `button(text, slot=None, primary=False, danger=False, tip="", *, icon=None, kind=None, shortcut=None)`: agrega `icon`
  (nombre Lucide), `kind` («ghost» para acciones secundarias sin borde) y `shortcut` (QKeySequence en texto; lo muestra en
  el tooltip). Compatible con las llamadas actuales.
- `class Toolbar(QWidget)`: fila de acciones con espaciado uniforme.
  `add(widget)`, `add_action(text, slot, *, icon=None, primary=False, danger=False, kind=None, tip="", shortcut=None) -> QPushButton`,
  `add_separator()`, `add_stretch()`, `add_search(placeholder="Buscar…", delay_ms=250) -> SearchBox`,
  `add_menu(text, actions: list[tuple[str, callable, str|None]], icon="ellipsis") -> QToolButton` (acciones poco usadas).
- `class SearchBox(QLineEdit)`: ícono de lupa, botón limpiar, señal `searched(str)` con retardo (debounce).
- `class EmptyState(QWidget)`: `EmptyState(icon, title, text="", actions: list[tuple[str, callable, bool primary]] = ())`,
  `.set_text(title, text)`. Centrado, ícono 40 px en `text3`.
- `class StatusBanner(QFrame)`: banda de resultado `set(kind, title, text="")` con `kind` ∈ good/warning/critical/info
  (ícono + color de tokens; nunca solo color).
- `toast(text, kind="ok", *, action_text=None, action=None, timeout_ms=4000)`: aviso flotante no modal sobre la ventana
  principal (esquina inferior derecha, se apilan). `notify()` pasa a usar `toast`.
- `setup_table(view, *, sortable=True, stretch_last=True)`: filas 30 px, sin cabecera vertical, selección por fila,
  colores alternos, cabecera alineada a la izquierda (números a la derecha), elisión de texto al medio para códigos.
- `fit_columns(view, *, min_w=56, max_w=360, sample=200)`: ancho = max(texto de cabecera, contenido de muestra) → elimina
  cabeceras cortadas («in / horizont», «echa de cort»).
- `DictTableModel.headerData` devuelve alineación según `Column.kind` y tooltip con el nombre completo.
- `FormDialog`: grilla de 2 columnas cuando hay > 8 campos, etiquetas alineadas, texto de ayuda gris bajo el campo
  (`Field(..., help="...")`), marcador de obligatorio y errores en línea bajo el campo (además del mensaje general).
- `class Section(QFrame)` (tarjeta con título, subtítulo opcional y barra de acciones a la derecha): `Section(title,
  subtitle=None)`, `.actions` (Toolbar), `.body` (QVBoxLayout). `Card` se mantiene.

### Pegar desde Excel — `gui/widgets/paste.py` (encargo UX3)
- `parse_table(text) -> list[list[str]]` (TSV del portapapeles de Excel, respeta comillas y saltos de línea en celdas).
- `class PasteDialog(QDialog)`: `PasteDialog(parent, title, fields: list[Field], rows: list[list[str]] | None = None)`,
  vista previa editable, mapeo automático por encabezado (acepta los nombres de las plantillas Excel: Codigo, Nombre,
  Duracion_Dias, HH, Habilidad_Requerida, Dotacion, Predecesoras, Mes_Macro, Frecuencia, …, y sinónimos sin tildes),
  conversión de números con coma decimal y fechas dd-mm-aaaa, errores por fila/celda; `values() -> list[dict]`.
- `paste_from_clipboard(parent, title, fields) -> list[dict] | None` (atajo que abre el diálogo con el portapapeles).
- `parse_predecessors(text) -> list[dict]`: «A; B+2; C SS-1; D FF» → `[{code, type, lag_days}]`.

### Asistente — `gui/wizard.py` (encargo UX3)
- `run_task_list_wizard(state, parent=None, mode="proyecto") -> int | None` (id del proyecto creado o None).

### Ventana y navegación (encargo UX2)
- `PageSpec` gana `icon: str`. Nueva página `inicio` (`pages/home.py`, `HomePage`), primera de la barra lateral.
- `Page` gana (opcional) `subtitle: str` y `def actions(self) -> list[tuple[str, str, callable, str | None]]`
  (texto, ícono, función, atajo) que la paleta de comandos lista cuando la página está activa.
- `MainWindow.open_file(path)`: arrastrar un .xlsx a la ventana → página Importar/Exportar con el archivo cargado
  (`ImportExportPage.load_file(path)` lo implementa UX4).
- `MainWindow.command_palette()` (Ctrl+K), `MainWindow.show_shortcuts()` (F1), `MainWindow.toggle_sidebar()` (Ctrl+B).
