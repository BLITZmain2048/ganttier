# UX4 — Catálogos, intercambio e informes

Fecha: 28-09-2026. Contrato: GUI_UX_SPEC.md y GUI_SPEC.md.

- Se modificaron únicamente las cinco páginas asignadas y se agregaron este
  documento y `backend/tests/gui/test_ux4.py`.
- Proyectos: acción primaria desde lista de tareas, demos agrupadas en menú,
  cantidad conservada, búsqueda, doble clic, menú contextual e íconos por modo.
  El estado vacío ofrece las tres vías de creación; una búsqueda sin resultados
  muestra un mensaje distinto del catálogo vacío.
- Recursos: barra de acciones, búsqueda y habilidad, pegado de múltiples filas,
  ámbito dibujado como pastilla y barras de HH/día por habilidad.
  Los POST se ejecutan en un trabajador, secuencialmente; un fallo parcial
  informa cuántos recursos se crearon y refresca los datos confirmados.
- Calendarios: lista lateral con ícono y resumen, detalle separado en Propiedades,
  Excepciones y Vista anual, acciones compactas y Guardar primario.
  Se conserva la leyenda coloreada desde tokens y la edición de excepciones.
- Importar/Exportar: tres secciones numeradas, zona de arrastre, banner de
  resultado y filtro de severidad sobre un proxy; el modelo original conserva
  todas las incidencias. `load_file(path)` selecciona y valida automáticamente.
  `pick_file(path)` conserva la selección sin validación de las pruebas previas.
- Informes: dos columnas desde 1300 px de ventana; disposición vertical debajo.
  Períodos segmentados sincronizados con el QComboBox público `period`.
  Estados vacíos en archivos e historial, acciones por archivo y carpeta
  abreviada con tooltip completo y acceso para abrirla.
  Vista previa habilitada para HTML; otros formatos conservan Abrir/Guardar.
- Todas las páginas tienen `subtitle`; ayudas usan `role="caption"` y el
  espaciado usa `theme.SPACE` con valores de respaldo. Tablas ajustadas mediante
  `setup_table`/`fit_columns`; dibujos usan exclusivamente tokens del tema.
- UX1: componentes obtenidos defensivamente de `widgets.common`; fallbacks
  locales para barras, búsqueda, estados vacíos, secciones, banner y tablas.
  El botón usa la API ampliada si está presente, preservando el cambio de tema.
- UX3: asistente importado al ejecutar la acción, con respaldo `new_project`.
  Pegado importado defensivamente; el respaldo ofrece edición TSV sencilla
  (nombre y habilidades, sin encabezados). El mapeo completo corresponde a UX3.
- Se conservan métodos, modelos, controles y guardas de concurrencia existentes.

Validación: `py_compile` pasó para las cinco páginas y el archivo de pruebas.
`pytest backend/tests/gui/test_ux4.py` recolectó siete pruebas: todas omitidas
porque PySide6 no está instalado. No se afirma validación visual ni de ejecución.
Pendiente de integración: ejecutar las suites GUI existentes y UX4 con pytest-qt
offscreen, y revisar capturas en ambos temas y tamaños de ventana.
