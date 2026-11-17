# GUI F1 — Correcciones del núcleo

Fecha: 26-09-2026. Alcance: revisión R1 y GUI_SPEC.md.

## Cambios

- R1-02: `open_project(pid)` conserva su contrato síncrono para pruebas.
  Lee proyecto, calendarios y recursos en variables locales y publica al final.
  Si falla, conserva los tres valores anteriores y propaga la excepción.
  `open_project_async(pid, on_done=None)` carga en un trabajador; solo publica
  la generación vigente en GUI, emite después las señales y muestra los fallos.
  MainWindow/Proyectos usan esta ruta. Cerrar con `pid=None` no consulta REST.
  `reload_catalogs` captura cliente/pid/generación y publica ambos catálogos
  juntos; desde trabajadores usa un slot encolado y descarta resultados obsoletos.
- R1-03: `run_task` exige invocación en GUI y crea allí un despachador QObject,
  con receptores `@Slot(object)` conectados mediante `Qt.QueuedConnection`.
  Conserva firma pública y `_active`; retiene tareas hasta finalizar callbacks,
  incluidas las tareas nuevas que estos lancen, y libera referencias en `finally`.
- R1-12: callbacks y botones registran excepciones y muestran un aviso protegido
  contra recursión. `Page.activate` restaura `_stale` si falla `refresh`.
- R1-13: nulos equivalentes nunca comparan como menores; NaN e infinitos se
  normalizan a nulo. Los nulos quedan al final en ambos sentidos de ordenación.
- R1-14: `MplCanvas.apply_plot_theme` sustituye al método `style` y sus tres
  llamadas internas. No se encontraron llamadas al método anterior en páginas.
- R1-05 común: `try_begin`, `end`, `is_busy`, `operations_changed` y claves
  recomendadas documentadas. Proyectos bloquea Nuevo/Demo/Duplicar/Excel con
  una guarda compartida, desde el diálogo hasta el resultado o error.
- R1-06: catálogos del alta se leen en trabajador; edición recarga el activo
  mediante apertura asíncrona y eliminación lo cierra sin REST síncrono.
  Proyectos conserva posibilidad de reintento y descarta listas obsoletas.
- R1-09: cierre consulta `has_unsaved()` opcional y ofrece Descartar/Cancelar.
  Si hay tareas pide confirmación, procesa eventos y espera el pool en intervalos
  breves hasta 30 s, incluyendo callbacks encadenados. Después detiene el servidor
  y cierra el cliente. El cierre de recursos es idempotente.
  El diálogo advierte que al vencer el plazo se cerrará la conexión; se registra
  ese caso. No se garantiza que una escritura pendiente termine tras ese límite.
- R1-15: invalidaciones consecutivas se fusionan con un único singleShot(0).
- R1-20: barra lateral con StrongFocus, Ctrl+1..9 y Ctrl+0 (Informes);
  Ctrl+Shift+1 (Importar/Exportar), con alternativas equivalentes usando Alt.
  Las etiquetas de FormDialog tienen buddy y los editores nombre accesible.
- R1-21: `status_text`/`method_text` traducen estados y métodos; `main` instala
  y retiene `qtbase_es` si existe en la ruta de traducciones de QLibraryInfo.

## Pruebas y límites

- Nuevo `backend/tests/gui/test_nucleo.py`, pytest-qt/offscreen y servidor real
  con BD, datos, configuración Qt y Matplotlib aislados en tmp.
- Cubre nulos/NaN, hilo GUI y excepciones de callbacks, tareas encadenadas,
  exclusión, apertura atómica/fallida/obsoleta, captura de pid, refrescos,
  botones/buddies, guardas de Proyectos, REST fuera de GUI y cierre/cancelación.
  Verifica acciones y atajos registrados para las once páginas.
- Validación local: `python -m py_compile` en los seis archivos Python.
  AST correcto; 128 comparaciones puras y traducciones verificadas sin importar Qt.
  No se ejecutó PySide6 ni pytest-qt; ejecución dinámica pendiente para Claude.
- Desde `backend/`: `python -B -m pytest -p no:cacheprovider
  tests/gui/test_nucleo.py --basetemp=../datos/pytest_gui_f1` (una sola línea).
- Solo se editaron common.py, state.py, app.py, pages/base.py, pages/projects.py;
  se crearon la prueba y este resumen. No se modificaron otras páginas/backend.
- El aviso de borradores depende de que cada página implemente `has_unsaved`.
  No se implementa en F1 la protección de borradores al cambiar de proyecto.
- API Qt contrastada con documentación oficial:
  [afinidad de slots](https://doc.qt.io/qtforpython-6/tutorials/basictutorial/signals_and_slots.html#thread-affinity),
  [QThreadPool](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThreadPool.html).
