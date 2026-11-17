# Ganttier — GUI de escritorio (PySide6) · contrato para agentes

La interfaz web queda reemplazada por una **GUI de escritorio en Python (PySide6/Qt 6)**. La GUI consume la
**API REST local** (servidor uvicorn embebido en un hilo, puerto libre desde 8765) mediante
`ganttier/gui/client.py` (`ApiClient`). La API sigue siendo el único contrato con los motores.

Arranque: `python -m ganttier.gui` (desde `backend/`). Pruebas sin pantalla: `QT_QPA_PLATFORM=offscreen`.

## Estructura (propiedad por agente)

```
backend/ganttier/gui/
  __init__.py  __main__.py         (Claude)
  app.py        MainWindow, navegación, PAGES, busy/notify, tema   (Claude)
  client.py     ApiClient: un método por endpoint (Claude; si falta uno, agrégalo al final del bloque que corresponda)
  server.py     EmbeddedServer                                    (Claude)
  state.py      AppState (señales project_changed, schedule_changed, catalogs_changed, projects_changed) (Claude)
  theme.py      tokens claro/oscuro + QSS; tokens() y qcolor()    (Claude)
  widgets/common.py  run_task, show_error, notify, confirm, Column/DictTableModel/SortProxy,
                     Field/FormDialog, Card, KpiCard, spi_status, MplCanvas, hbox, button,
                     save_bytes_dialog, open_path, fmt_date/fmt_num/fmt_pct, iso/to_date/qdate  (Claude)
  pages/base.py      Page (title, activate(), refresh())          (Claude)
  pages/projects.py  referencia de estilo                          (Claude)
  gantt/             Q1  GanttWidget (tabla virtual + lienzo) y ResourceProfile
  pages/activities.py            Q1  Actividades y Gantt (+ detalle, nueva actividad, WBS)
  pages/macro.py, optimize.py, resources.py, calendars.py        Q2
  pages/maintenance.py                                            Q3
  pages/control.py, performance.py, reports.py                    Q4
  pages/importexport.py                                           Q5
backend/tests/gui/                                                cada agente: test_<su_página>.py (pytest-qt, offscreen)
```

## Reglas de la GUI

1. **Nunca bloquear el hilo de la GUI**: toda llamada a la API va por `run_task(fn, on_done, busy="Texto…")`.
   Los errores se muestran solos (`show_error`) salvo que pases `on_error`.
2. Textos visibles en **español neutro**. Fechas dd-mm-aaaa (`fmt_date`), números con formato chileno (`fmt_num`).
3. Colores SOLO desde `theme.tokens()` (el tema cambia en caliente; implementa `on_theme()` en la página si
   dibujas a mano o usas `MplCanvas`).
4. Cada página hereda de `Page`, fija `title`, implementa `refresh()` (se llama al mostrarse si hay cambios) y
   llama `self.state.bump_schedule()` después de modificar fechas/actividades.
5. Tablas grandes: `QTableView` + modelo (`DictTableModel` o uno propio) — nunca `QTableWidget` con 100 000 filas.
6. Formularios: `FormDialog` con `Field` para altas/ediciones (código, nombre, etc.) y validación con mensaje claro.
7. Gráficos: `MplCanvas` (matplotlib QtAgg) con colores `tokens().series`; el Gantt y los mapas de calor se dibujan
   con `QPainter` para rendimiento.
8. Cada página debe tener una prueba `backend/tests/gui/test_<pagina>.py` con pytest-qt que la abra con un proyecto
   demo (vía la API real con BD temporal) y ejercite sus acciones principales.

## Funcionalidad de referencia

El frontend web anterior (`Ganttier/frontend/src/`, React) contiene la funcionalidad completa de cada pantalla
(p. ej., `components/Gantt/GanttView.tsx`, `model.ts`, `ResourceProfile.tsx`, `pages/*.tsx`). Úsalo como
especificación funcional: la GUI Qt debe ofrecer al menos lo mismo. Endpoints: `SPEC.md §10` y `ganttier/gui/client.py`.

## Programación desde una lista de tareas (requisito del usuario)

El usuario **no entrega un programa para optimizar**: entrega actividades (duración, HH, habilidad/recursos,
dependencias, ventanas/mes macro, restricciones) o tareas periódicas, y el programa **debe encontrar un programa
factible** (y luego optimizarlo según el objetivo elegido). La acción principal en la GUI es **«Generar programa»**
(endpoint `POST /api/projects/{pid}/schedule/solve`), que en mantenimiento también genera las ocurrencias.
