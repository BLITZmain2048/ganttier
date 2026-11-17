# A5 — Informes e intercambio Excel

## Contrato e implementación

Se respetan `SPEC.md`, `domain.py` y `schema.sql`, sin modificar archivos de otros
módulos. Se implementan las siguientes funciones públicas:

```python
ganttier.reports.render(report: ReportData, fmt: str, out_path: Path) -> Path
ganttier.excel_io.build_project_template(path: str | Path) -> Path
ganttier.excel_io.build_maintenance_template(path: str | Path) -> Path
ganttier.excel_io.parse_workbook(path: str | Path, mode: str | None = None) -> ImportBundle
ganttier.excel_io.export_project(bundle: ImportBundle, path: str | Path) -> Path
```

También están disponibles en `reports.builder`, `excel_io.templates`,
`excel_io.importer` y `excel_io.exporter`, según SPEC. El comando
`python -m ganttier.excel_io.templates` genera ambos libros en `Ganttier/plantillas`.

No se requiere ninguna dependencia de producción adicional a las ya incluidas
en `requirements.txt`: openpyxl ≥ 3.1, reportlab ≥ 4.2, python-docx ≥ 1.1,
matplotlib ≥ 3.8 y numpy ≥ 1.24. No se usa pandas. `pypdf` es opcional para
inspección semántica del PDF en pruebas; no se necesita para generar informes.

## Decisiones de intercambio

- Fechas Python `date`; avance físico de 0 a 100. Se diferencia el formato
  porcentual de Excel (`0.0%`, valor 0,5) del porcentaje decorativo (`0.0" %"`,
  valor 50). Booleanos Python. Habilidades como `list[str]`.
- Referencias sin resolver: `project.calendar_name`, `activity.calendar_name`,
  `activity.wbs_code`, `wbs.parent_code`, `relation.pred_code/succ_code`,
  `assignment.activity_code/resource_name`, `progress.activity_code/resource_name`,
  `resource.calendar_name`, `periodic_task.calendar_name/resource_name/wbs_code`.
  Para actividades de mantenimiento se admite `periodic_task_code`. Estos nombres
  complementan los `*_code`/`*_name` documentados en ImportBundle. Los nombres
  son sensibles a mayúsculas; la normalización se aplica solo a encabezados,
  nombres de hoja y valores de categorías.
- Las excepciones se anidan como `calendar['exceptions']`, con campos de
  `CalendarException`. No se agrega una lista a ImportBundle ni se modifica el
  dominio. Se exportan a Excepciones_Calendario en ambos modos.
- En Asignaciones se separan **Recurso** y **Habilidad**, evitando una referencia
  ambigua en una sola columna. Basta una de las dos; ambas se conservan.
- Los defaults de las dataclasses se incluyen para campos omitidos. Los errores
  se devuelven como ImportIssue y el consumidor debe comprobar `has_errors` antes
  de persistir. Nunca se escribe en BD desde este módulo.
- En mantenimiento se valida la pertenencia sintáctica de `TAREA-0001` a una
  tarea maestra. La ocurrencia efectiva y su horizonte deben verificarse al
  aplicar los datos, pues el lector no puede generarlos ni consultar la BD.
- No se materializa Mes_Macro: el backend es responsable de fijar su ventana,
  según SPEC. Se preservan los campos originales para la ida y vuelta.
- La exportación conserva todos los campos conocidos del dominio mediante
  columnas adicionales. El importador advierte columnas desconocidas; el
  exportador rechaza claves desconocidas para evitar pérdida silenciosa.
  Los IDs que vengan en el bundle se conservan como datos auxiliares, pero el
  servicio debe resolver referencias por códigos/nombres.
- Las fórmulas no se evalúan: `read_only=True`, `data_only=True`, `keep_links=False`.
  La hoja Instrucciones pide recalcular y guardar en Excel antes de importar.
  El texto exportado siempre permanece texto, aunque comience por `=`.
- Lectura y validación de referencias O(n), índices por código/nombre y detección
  iterativa de ciclos WBS. Se usa `values_only=True` donde no se necesita el
  formato porcentual; conversiones repetidas de calendario/categoría tienen una
  caché local acotada a 2.048 entradas. Sin estado global mutable de importación.

## Decisiones de informes

- La entrada ReportData pertenece a A1. Se presentan sus indicadores, sin
  recalcular planificación, EVM ni desempeño; no se confunden HH con moneda.
- Semáforo: SPI ≥ 0,95 verde; 0,85 ≤ SPI < 0,95 amarillo; SPI < 0,85 rojo.
  Valores ausentes: “Sin datos”. Se muestran palabra y color simultáneamente.
- Una definición compartida de secciones asegura orden y contenido equivalentes.
  La tabla de trabajadores se divide en indicadores y conteos para caber en A4.
- PDF/DOCX/HTML: hasta 500 filas por tabla, con aviso explícito y total original.
  XLSX: detalle completo, sujeto al límite físico de filas/columnas de Excel.
  Mini-Gantt y barras estáticas: primeras 20 filas con aviso para preservar
  legibilidad. El XLSX usa todas las filas en su gráfico nativo de desempeño.
- Excel usa LineChart, BarChart apilado + línea de capacidad y barras de
  desempeño. El corte tiene dos puntos de igual fecha en un DateAxis para
  representar una línea vertical exacta. Solo el mini-Gantt se inserta como PNG.
- La capacidad del histograma apilado es la suma de las capacidades incluidas;
  la tabla mantiene valores individuales, pues el total puede ocultar un
  desbalance entre recursos. Numpy calcula los totales de series.
- Matplotlib Agg usa Figure sin pyplot, acceso protegido por RLock y PNG a
  150 dpi. Los datos ausentes se mantienen como huecos, no se interpolan a cero.
- PDF: A4, Platypus LongTable, cabecera repetida, división de filas altas,
  encabezado/pie y páginas. Word: A4, Arial, títulos nativos, cabeceras repetidas,
  tablas dimensionadas y campo PAGE. HTML: textos escapados, CSS y PNG base64
  autocontenidos, reglas de impresión.
- Escritura atómica de informes: un fallo no reemplaza el informe anterior y
  elimina el temporal creado en la misma carpeta de salida. Los formatos y las
  dimensiones de las series se validan antes de escribir.

## Verificación y límites del entorno

Las pruebas crean libros reales, alteran celdas, reimportan exportaciones y
generan informes sintéticos con datos eléctricos plausibles. Cubren formatos,
referencias, ciclos WBS, fechas imposibles, números, booleanos, restricciones,
archivos ZIP/XML dañados, variantes de encabezado, porcentajes y texto que
parece fórmula. Las pruebas de informes cubren tablas largas, contenido vacío,
semáforos en los límites, títulos por período y fallos de escritura.

El entorno local dispone de openpyxl, numpy, matplotlib y pytest. No dispone de
reportlab ni python-docx. Se intentó instalar esas dependencias exclusivamente
en `backend/tests/reports/_runtime/deps`, con temporales dentro de la misma
carpeta, pero la red está bloqueada (WinError 10013). Tampoco había ruedas en la
caché local. Por ello las pruebas PDF/DOCX se omiten explícitamente cuando falta
la biblioteca. No se afirma validación de render ni revisión visual de esos
formatos en este entorno. Deben ejecutarse en el entorno completo de A1/Claude.

Se inspeccionó visualmente el PNG de mini-Gantt y se corrigieron el espesor de la
barra para una sola actividad y la repetición de etiquetas de día. Los gráficos
Excel se reabren con openpyxl y se inspeccionan sus series y formato numérico.

Resultados de la ejecución final de Excel (26-09-2026): **29 pruebas correctas**
en 132,75 s, incluyendo la creación del banco masivo. La importación medida fue
**55,761 s para 100.000 actividades**, con cero incidencias, bajo el umbral de
60 s. Una medición aislada previa dio 50,045 s tras optimizar; la versión inicial
tardaba 64,437 s. Los tiempos dependen de la carga de esta máquina compartida.

Informes: **14 pruebas correctas y 4 omitidas** en 7,63 s (render y tablas largas
de PDF/DOCX omitidos por las dependencias ausentes). Ambas plantillas definitivas
se regeneraron por CLI y se reimportaron con **cero incidencias**. La sintaxis de
los 16 archivos Python entregados se validó con `ast.parse`.

Archivos entregados:

- `backend/ganttier/reports/`: `__init__.py`, `builder.py`, `common.py`,
  `charts.py`, `xlsx.py`, `pdf.py`, `docx.py`, `html.py`.
- `backend/ganttier/excel_io/`: `__init__.py`, `schema.py`, `templates.py`,
  `importer.py`, `exporter.py`.
- `backend/tests/reports/test_reports.py`.
- `backend/tests/excel_io/test_templates.py`, `test_performance.py`.
- `plantillas/Plantilla_Proyecto.xlsx`, `Plantilla_Mantenimiento.xlsx`.
- `docs/plantillas.md` y este documento.

No quedan funciones sin implementar. Quedan pendientes en el entorno completo
la ejecución de las cuatro pruebas PDF/DOCX omitidas y su revisión visual. La
generación efectiva de ocurrencias y la resolución de IDs son responsabilidades
de A1/A3, no de A5.

Los temporales de pruebas y revisión visual permanecen dentro de
`backend/tests/excel_io/_runtime/` y `backend/tests/reports/_runtime/`: la revisión
automática rechazó su limpieza recursiva por política de ejecución. No son
archivos de producción y no se necesita distribuirlos con Ganttier.
