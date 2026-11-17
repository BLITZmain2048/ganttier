# GUI F3 — correcciones de páginas y Gantt

Fecha: 26-09-2026. Alcance: R1-01 (GUI), 05, 07 (Control), 10, 11, 14, 17, 19 y 21.

- Actividades y Optimizar intentan primero `/schedule/solve`.
  Solo 404/405 habilitan CPM/optimizador anterior, con el aviso literal:
  «El servidor no tiene /schedule/solve; se usó el método antiguo».
- Ambos muestran factibilidad, violaciones y advertencias. Una respuesta
  no factible abre un diálogo con hasta 20 violaciones, conserva el informe
  completo y nunca comunica que se aplicó o guardó el programa.
- Generar, CPM, Optimizar/aplicar y Línea base comparten `solve:{pid}`.
  El adaptador en `gantt/dialogs.py` usa defensivamente `try_begin/end/is_busy`
  y `operations_changed`; sin esos miembros mantiene exclusión entre páginas.
  Libera la reserva en éxito/error/callback obsoleto y deshabilita botones.
- `mutate(operation_key=...)` deduplica claves pendientes y en ejecución.
  Altas de actividad, WBS, relaciones/asignaciones, avances y líneas base
  tienen claves; operaciones distintas conservan su orden en la cola.
- Control conserva `drafts_by_project[pid]`; `drafts` siempre pertenece al
  proyecto activo, incluso oculto. Cambiar proyecto conserva los borradores
  sin descartarlos ni exigir confirmación. Un guardado confirmado limpia
  exclusivamente el borrador de origen aunque entretanto cambie el proyecto.
  `has_unsaved()` incluye todos los proyectos para la consulta de cierre.
- Informes usa `GANTTIER_DATA_DIR/informes_locales` o
  `QStandardPaths.AppDataLocation/informes_locales`. Una elección explícita
  se conserva en `QSettings("Ganttier", "Ganttier")`, clave
  `reports/download_directory`, con prioridad sobre el valor predeterminado.
- Actividades reinicia búsqueda y Solo críticas con señales bloqueadas.
- `GanttWidget.day_to_x()` sustituye la colisión con `QWidget.x()`;
  las únicas ediciones de `test_activities.py` son los tres usos renombrados.
- Perfil: `QListView` + `QAbstractListModel` + filtro proxy, selección en un
  conjunto y colores consultados al pintar. Los cambios de selección no
  recorren todas las filas. Adaptador `count/item` perezoso para pruebas previas.
- Eliminar relación/asignación confirma actividad, elemento y efecto;
  bloquea el botón desde la pregunta hasta finalizar el DELETE y recálculo.
- Estados/métodos usan las traducciones de `common` mediante `getattr`,
  con diccionarios de respaldo. No se editaron common.py, state.py ni app.py.

## Verificación

- `python -m py_compile` de los módulos cambiados y las dos pruebas: correcto.
- pytest de `test_activities.py`, `test_planning_pages.py` y
  `test_paginas_f3.py`: **18 passed, 30 skipped** (sin PySide6/pytest-qt).
- F3 aporta 13 casos: solve real desde lista, doble envío/exclusión,
  borradores/filtros, rutas/persistencia, cancelación y eliminación confirmada,
  rechazo no factible, perfil virtual, cola y desbloqueo tras error.
- Pendiente: ejecutar Qt offscreen con API real; no se certifica ejecución Qt.
  Desde backend: `python -B -m pytest -p no:cacheprovider tests/gui/test_paginas_f3.py --basetemp=tests/gui/_tmp_f3_qt`.
  Los fixtures de cierre deben resolver/descartar también borradores de otros
  proyectos, ahora detectados por `has_unsaved()`.
- API Qt contrastada con documentación oficial de
  [QAbstractListModel](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QAbstractListModel.html),
  [QSortFilterProxyModel](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QSortFilterProxyModel.html) y
  [QStandardPaths](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QStandardPaths.html).
