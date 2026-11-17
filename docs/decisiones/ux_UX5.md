# UX5 — Actividades, macro, optimización y mantenimiento

Fecha: 28-09-2026. Contratos: `GUI_UX_SPEC.md` y `GUI_SPEC.md`.

## Alcance
- Cambios únicamente en `activities.py`, `macro.py`, `optimize.py`, `maintenance.py`,
  `backend/tests/gui/test_ux5.py` y este documento. No se editó `gui/gantt/*`.
- Se conservaron los métodos y atributos públicos usados por las pruebas existentes,
  los bloqueos de operaciones, la cola de mutaciones y las comprobaciones de proyecto.
- REST continúa ejecutándose exclusivamente mediante `run_task` o la cola `mutate`.

## Presentación y flujo
- Actividades: una Toolbar, grupos de programación/edición, menú Más (WBS, línea base,
  vista), SearchBox, filtro de críticas con icono y apariencia de botón conmutable.
- `only_critical` conserva su tipo QCheckBox. `scale` conserva su QComboBox oculto,
  sincronizado con D/S/M/T/A y con el zoom del Gantt. Atajos Ctrl+G, F9 y Enter.
- La barra mantiene una fila; permite desplazamiento horizontal en ventanas estrechas.
- Estado vacío con asistente, pegado, alta e importación; toast con «Ver detalle»;
  acciones de paleta y pastillas de actividades, críticas, relaciones y avance ponderado.
- Avance ponderado: HH por actividad; donde no hay HH, duración con mínimo de un día,
  consistente con los agregados actuales de WBS.
- Macro: Toolbar, acción principal de generación, leyenda con tokens y pie de capacidad
  normal hasta 85 %, atención hasta 100 %, sobrecarga por encima; sin capacidad se alerta.
- Optimización: cinco tarjetas seleccionables, descripciones caption, pesos avanzados
  plegables, StatusBanner, KPI antes → después y delta (menor es mejor en estas métricas),
  advertencias con iconos, historial en Section plegable y estados vacíos.
- Mantenimiento: fechas alineadas, reemplazo con ayuda de advertencia, Toolbar del maestro,
  pegado en lote, KPI en fila, pestañas con iconos y estado vacío con asistente de mantenimiento.
- Subtítulos, ayudas caption, `setup_table` y `fit_columns`; colores desde tokens.

## Integración y guardado
- Los adaptadores defensivos de UX1 residen en `activities.py` y se reutilizan en UX5.
  Prefieren los widgets comunes disponibles y conservan respaldos locales pequeños.
- PasteDialog se usa mediante `paste_from_clipboard`; `parse_predecessors` y el asistente
  se resuelven al invocarlos. Si UX3 aún falta, se informa sin impedir abrir las páginas.
- Actividades: un POST para el lote y relaciones con IDs devueltos por la API.
  Antes de escribir se validan campos, códigos repetidos/existentes, referencias desconocidas,
  dependencias consigo misma, predecesoras repetidas y ciclos dentro del lote.
- Maestros periódicos: la API recibe una tarea por POST; se valida el lote completo primero.
- No existe transacción REST conjunta para actividades/relaciones ni maestros periódicos.
  Un fallo intermedio informa el número guardado y recarga el estado real; no borra datos.

## Verificación
- `py_compile` de las cuatro páginas y `test_ux5.py`: correcto, con escritura de bytecode
  desactivada mediante mock para no crear archivos adicionales.
- Selección compatible con el entorno: 20 pruebas aprobadas, 60 omitidas y 2 excluidas
  (`launcher`). PySide6 y FastAPI no están instalados en el Python disponible.
- Ocho casos nuevos Qt/API: estado vacío, 10 actividades + 9 relaciones, tres rechazos
  sin escrituras, selección de objetivo, banner factible/infactible y pegado periódico.
- Comprobación adicional en memoria de la lógica real de pegado: cadena de 10/9,
  referencia desconocida, ciclo y duplicado; correcta. No sustituye ejecutar Qt.
- El intento de suite completa encontró fixtures que usan TEMP externo; no se cambiaron.
  Quedan pendientes ejecución Qt 6.11, pruebas F3 y capturas con ambos temas.
- Para esa validación, desde `backend`, usar `QT_QPA_PLATFORM=offscreen` y ejecutar:
  `python -m pytest tests/gui/test_ux5.py tests/gui/test_activities.py tests/gui/test_planning_pages.py tests/gui/test_maintenance_importexport.py tests/gui/test_paginas_f2.py tests/gui/test_paginas_f3.py tests/gui/test_sombras_qt.py --basetemp=.tmp-ux5-qt -p no:cacheprovider`
