# UX2 — Ventana, navegación e Inicio

Fecha: 28-09-2026. Contratos: GUI_UX_SPEC.md y GUI_SPEC.md.

- Alcance: app.py, state.py, pages/base.py; nuevos pages/home.py,
  widgets/palette.py y tests/gui/test_ux2.py. No se editaron fuentes de otros encargos.
- Inicio ocupa el primer lugar: Ctrl+1 = Inicio, Ctrl+2 = Proyectos.
  Se conservan Ctrl/Alt+1…9/0 y Ctrl/Alt+Shift+1/2 para las páginas restantes.
  test_nucleo.py calcula los atajos desde PAGES y no necesitó modificaciones.
- Sidebar con secciones, Lucide, selección en pastilla y colapso Ctrl+B;
  los nombres siguen en tooltips y accesibilidad. Tema, Atajos y versión al pie.
- Header de 56 px con título/subtítulo, selector MRU + restantes alfabéticos,
  ícono de modo, pastillas de modo/corte, Ctrl+K y menú Acerca de.
- Paleta modal sin marco: subcadenas e iniciales sin tildes, flechas, Enter y Esc.
  Incluye páginas, Page.actions(), acciones globales y proyectos recientes.
- F1 muestra la tabla; F5 llama refresh() de la página activa.
  F9 continúa bajo el contexto existente de Actividades y Gantt, sin duplicarlo.
- open_file y drop de .xlsx navegan a importar y llaman load_file si está disponible.
- notify usa common.toast si existe y, como respaldo, la barra de estado.
  La barra muestra Listo/operación y progreso delgado; la URL queda en tooltip/Acerca de.
- Se pasa el QSettings de la ventana a AppState para respetar las pruebas que lo parchean.
  Persisten geometría, saveState(), sidebar, última página/proyecto y hasta 8 recientes.
  La reapertura es asíncrona y silenciosa si el proyecto dejó de existir.
- Inicio vacío ofrece cuatro tarjetas y recientes. La demo usa projects:create.
  Asistente UX3 y new_project se resuelven de forma defensiva.
- Inicio con proyecto carga por run_task proyecto, columnas, EVM (cuando hay fechas),
  recursos, tareas periódicas, líneas base, avances e historial de informes.
  EVM 422 deja indicadores sin dato; no impide mostrar el checklist.
- El paso 3 requiere actividades y todas sus fechas iniciales/finales presentes.
  Generar usa POST schedule/solve con apply=true y exclusión solve:{pid};
  vuelve a leer las fechas, incluso si la respuesta HTTP 200 no aplicó un programa.
  Tickets y pid evitan publicar respuestas de otra selección; las guardas se liberan al fallar.
- Limitación del contrato REST: no hay listado persistente de informes manuales.
  El paso 6 usa historial automático y archivos de ReportsPage observados en sesión;
  guarda esa evidencia en QSettings. Informes manuales externos no son detectables.
- Colores desde tokens; se recolorean íconos vía theme_signals o fallback en toggle_theme.
  El círculo pendiente es un borde redondo: no existe un SVG Lucide `circle` en el catálogo.
- Page solo agrega subtitle y actions; su lógica de invalidación/supresión permanece intacta.

Validación local: py_compile de los seis archivos Python, sin errores.
pytest: dos pruebas puras (búsqueda y MRU) y diez pruebas GUI preparadas para pytest-qt.
No hay PySide6 en este entorno: las pruebas GUI y la revisión visual quedan pendientes.
Ejecutar desde Ganttier:
`python -B -m pytest -p no:cacheprovider backend/tests/gui/test_ux2.py --basetemp=trabajos/pytest_ux2`
