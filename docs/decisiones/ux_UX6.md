# UX6 — Gantt, Avance y curva S, Desempeño

Contrato: `GUI_UX_SPEC.md`, `GUI_SPEC.md` y API común de UX1.

- Gantt: filas de 26 px, barras centradas de 12 px con radio 3 px y avance interior
  más oscuro derivado del color de la barra; críticas con `bar_crit` en ambos temas.
- Línea base de 3 px, hitos de 10 px y resúmenes WBS delgados con picos en los extremos.
- Etiquetas de actividad en `text2`, 8,5 pt y elisión; meses semibold y nivel inferior
  en `text3`; días no laborables desde `weekend`/`holiday`.
- Corte con pastilla «Corte dd-mm» en la cabecera; hoy con línea verde discontinua.
- Tabla con `setup_table`, cabecera común, fechas chilenas, alineación numérica,
  sangría WBS y chevrons Lucide. Alternancia, hover bidireccional y selección
  sincronizados con el lienzo, incluidos los grupos.
- Se conserva `setBrush(Qt.NoBrush)` inmediatamente antes de pintar relaciones,
  las transformaciones temporales, las señales públicas y la virtualización.
- Perfil: colores asignados por clave para independizarlos del orden de carga;
  apilado en orden fijo de color, capacidad escalonada discontinua `text2`,
  exceso `critical` y leyenda textual existente.
- Detalle Gantt: pestañas con íconos `list-checks`, `git-branch` y `users`;
  confirmaciones y guardado permanecen intactos.
- Control: Toolbar para corte, Aplicar, Hoy, Actualizar y menú de líneas base;
  selector de línea base preservado. KPI en 5 columnas, 3 bajo 1100 px.
- Semáforos de KPI con ícono Lucide y texto, renovados al cambiar el tema.
- Curva S con líneas de 2 px azul/naranja/verde, proyección discontinua,
  corte rotulado, grilla tenue y leyenda fuera del área de datos.
- Avance con `setup_table`/`fit_columns`, borradores en `warning` con alfa 0,16
  y contador «N cambios sin guardar» junto a Guardar avance, primario.
- Sin programa se muestra EmptyState «Primero genera el programa», cuya acción
  llama a `window().go("gantt")` si existe. Se conserva la tabla de actividades
  para no alterar el flujo de borradores ni las pruebas de tareas sin fechas.
- Desempeño: período segmentado y QComboBox público oculto, sincronizados.
  Gráfico con espacios de 26 px y barras de 18 px, altura acotada incluso con
  dos trabajadores, rótulos finales y metas con `axvline` (90 % / 1,0).
  Las metas no añaden parches. EmptyState sustituye las tablas/gráficas vacías.
- Componentes pintados y páginas conectados a `theme_signals.theme_changed`.
  No se modificó `pages/activities.py`, el tema ni los widgets compartidos.

## Verificación

- `py_compile` correcto para los siete módulos modificados y `test_ux6.py`.
- Ejecución desde `backend`:
  `python -B -m pytest -p no:cacheprovider tests/gui/test_ux6.py tests/gui/test_activities.py tests/gui/test_control_pages.py tests/gui/test_paginas_f3.py tests/gui/test_ux5.py tests/gui/test_sombras_qt.py --basetemp=../.ux6-test-tmp --durations=3`
- Resultado local: 12 passed, 47 skipped. La prueba nueva de construcción de
  100.000 actividades, 10 WBS y 99.999 relaciones pasó el límite de 1 segundo
  (duración total de la llamada de prueba: 0,24 s).
- PySide6 y pytest-qt no están instalados: no se afirma validación visual ni
  ejecución de las pruebas Qt. Pendiente del integrador con PySide6 6.11.
- Pruebas Qt nuevas: grab a QImage y color crítico en claro/oscuro con el mismo
  widget vivo; virtualización/hover/fechas; EmptyState sin programa y navegación;
  grilla KPI y curva S; sincronización de período, barras acotadas y límite de
  40 parches en Desempeño. El benchmark y la estabilidad de colores corren sin Qt.
