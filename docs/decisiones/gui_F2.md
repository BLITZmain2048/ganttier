# GUI F2 — páginas de planificación

Fecha: 26-09-2026. Alcance: Recursos, Calendarios, Mantenimiento y Macro.
Revisión de `revision_gui_R1.md` y `GUI_SPEC.md` aplicada sin editar componentes compartidos.

- **R1-04:** el cambio de proyecto invalida tickets y vacía datos, selección y modelos,
  incluso con la página oculta. `data_pid` identifica los datos publicados.
  Editar, guardar, eliminar y generar verifican su propietario y el estado de carga.
  Los diálogos vuelven a comprobar el proyecto antes de aceptar una mutación.
  Macro detiene los lotes restantes si cambia el proyecto y conserva los pendientes.
- **R1-08:** Recursos y Calendarios separan escritura y consultas de recarga.
  El callback publica `last_saved`; Calendarios publica además `editing` con el ID
  confirmado antes de consultar catálogos o días. Un segundo Guardar usa PUT.
  Mantenimiento también conserva el resultado antes de recargar tras guardar.
  Si falla esa recarga se avisa: «Guardado; no se pudo actualizar la vista. Usa Actualizar.»
  Los catálogos de AppState se publican desde callbacks GUI, sin `reload_catalogs`
  dentro del trabajador de escritura. Las páginas tienen acción Actualizar.
- **R1-11:** búsquedas, habilidad, instalación, desconexión, reemplazo, solo sin mes
  y nuevas ventanas duras se reinician con señales bloqueadas al cambiar de proyecto.
- **R1-16:** Mantenimiento y Macro usan QTimer reiniciable, singleShot, de 250 ms
  para escritura del usuario. `setText()` sigue filtrando inmediatamente para
  conservar el contrato de las pruebas existentes; Qt reinicia `isModified` allí.
  Los textos con `casefold` se calculan una vez por carga. Buscar en Mantenimiento
  solo actualiza tareas/KPI; el calendario instalación×mes conserva su agregado.
- **R1-18 GUI:** permanece la casilla de reemplazo. Su ayuda describe la operación
  de completar faltantes cuando está desmarcada. El resumen usa `.get` para
  mostrar `created` y `kept` cuando están presentes, también en compatibilidad.
- **R1-05 páginas:** ambos generadores reservan `solve:{pid}` con `try_begin` y
  liberan con `end` en éxito/error, incluido el fallo del guardado previo de Macro.
  `is_busy` y `operations_changed` mantienen los botones sincronizados entre páginas.
  Todos estos accesos son defensivos; la ausencia de la API opcional no bloquea.

## Verificación

- `python -m py_compile` sobre las cuatro páginas y `test_paginas_f2.py`: correcto.
- Nueva suite: 23 casos parametrizados, Qt offscreen y callbacks controlados;
  cubre cambio de proyecto, cargas tardías, propiedad, POST/PUT, debounce y exclusión.
- Pytest sobre las tres suites, con `-k 'not launcher' -p no:cacheprovider`:
  **10 passed, 44 skipped, 2 deselected**. PySide6 y pytest-qt no están instalados.
- Primera ejecución: los dos tests de launcher fallaron en setup por una ruta
  temporal inexistente; se excluyeron de la comprobación de páginas.
- Pendiente para Claude: ejecutar las pruebas GUI reales con PySide6/pytest-qt.
  Usar `--basetemp` bajo una carpeta existente del espacio compartido.
- API Qt 6 contrastada con documentación oficial de
  [QTimer](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QTimer.html),
  [QLineEdit](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QLineEdit.html) y
  [QObject.blockSignals](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QObject.html#PySide6.QtCore.QObject.blockSignals).
