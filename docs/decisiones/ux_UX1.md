# UX1 — Tema y componentes comunes

Contrato: `GUI_UX_SPEC.md`, secciones «Tema» y «Widgets comunes»; arquitectura de `GUI_SPEC.md`.

- Se conservan todos los tokens y componentes anteriores, incluido `MplCanvas.apply_plot_theme`.
- Claro: fondo `#f5f6f8`, tarjetas blancas, borde `#e3e6ea`, radio de tarjeta 10 px.
- Oscuro conserva su paleta; estados, textos y superficies usan tokens en ambos temas.
- Se exponen `SPACE`, `RADIUS`, `RADIUS_LG`, `FONT_SIZES`, `ROW_HEIGHT` y `ui_font()`.
- Fuente de interfaz 9,5 pt: Segoe UI en Windows; Inter, Noto Sans y DejaVu Sans como respaldo.
- QSS incorpora botones por `kind`, estados de foco, controles, tablas, pestañas subrayadas,
  menús, indicadores, scrollbars delgadas, badges, sidebar, header y barra de estado.
- `apply_theme` aplica fuente y paleta, limpia la caché de íconos y emite `theme_changed`.
  La guarda `_style_set` sigue impidiendo reinstalar Fusion con ventanas vivas.
- `button` conserva sus cinco argumentos posicionales y agrega `icon`, `kind`, `shortcut`.
  El atajo aparece en el tooltip; `QShortcut` se crea al recibir padre, incluso mediante layout.
  Los callbacks mantienen la protección con `report_error`.
- `Toolbar` agrega widgets, botones, búsqueda, separadores, espacios y menús con íconos.
- `SearchBox` usa QAction a la izquierda, limpieza nativa y un QTimer reiniciable single-shot.
- `EmptyState`, `StatusBanner` y `Section` añaden composición reutilizable y nombres accesibles.
  `Section.actions` es la propiedad Toolbar exigida por contrato, respaldada por `_actions`;
  sustituye intencionadamente el acceso Python a `QWidget.actions()` en esta clase.
- Los íconos nuevos se obtienen del módulo existente y se refrescan con `theme_changed`.
- `toast` crea avisos hijos de `app.main_window()`, apilados abajo a la derecha, sobre la barra
  de estado. Se reposicionan al redimensionar, admiten acción/cierre y expiran con QTimer.
  Incluyen nombre del estado e ícono; sin ventana registran el aviso en logging. `notify` los usa.
- `setup_table` aplica filas de 30 px, selección por fila, alternancia, ausencia de rejilla,
  cabecera izquierda y elisión central. Los modelos declaran la alineación de cada cabecera.
- `fit_columns` mide cabecera semibold y DisplayRole con las métricas correspondientes,
  muestreando hasta 200 filas repartidas por el modelo. `max_w` limita contenido;
  una cabecera más ancha tiene prioridad para evitar truncamiento. Respeta columnas ocultas.
- `DictTableModel.headerData` añade tooltip completo y alineación numérica; respeta `align_right`.
- `Field.help` ya existía al final y conserva su posición. `required` es opcional y solo por nombre,
  sin desplazar argumentos posicionales; añade asterisco, descripción accesible y validación vacía.
- `FormDialog` usa una columna hasta ocho campos y dos a partir de nueve; respeta `columns=`
  y `span`. Muestra ayuda bajo el editor, errores por clave y resumen general dentro del diálogo.
  Los validadores que devuelven texto conservan QMessageBox; los dict generan errores en línea.

## Verificación

- `python -m py_compile backend/ganttier/gui/theme.py backend/ganttier/gui/widgets/common.py backend/tests/gui/test_ux1.py`: correcto.
- Pruebas creadas en `backend/tests/gui/test_ux1.py`: debounce y limpieza, Toolbar/menú/atajos,
  EmptyState/Section, StatusBanner, toast/acción/expiración/fallback, tablas y cabeceras,
  formularios/ayuda/validadores compatibles y dos aplicaciones de tema con ventanas vivas.
- PySide6 y pytest-qt no están instalados en este entorno; estas pruebas y la revisión visual
  quedan pendientes de ejecución offscreen por el integrador, sin afirmar que hayan pasado.
- Ejecutar desde `backend`: `python -m pytest -p no:cacheprovider tests/gui/test_ux1.py`.
- No se modificaron `widgets/icons.py`, páginas ni archivos de otros encargos.
