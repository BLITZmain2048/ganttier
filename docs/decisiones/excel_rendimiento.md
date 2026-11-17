# Rendimiento de exportación Excel

Fecha: 26-09-2026. Alcance: `excel_io/`, `reports/xlsx.py` y sus pruebas.

## Banco y mediciones

Windows, Python 3.10.6, openpyxl 3.1.5, sin lxml. El banco reproducible de
exportación contiene **100.000 actividades de mantenimiento y 100.000
asignaciones**, con campos del dominio, fechas, maestros, excepciones y avance.
El banco del informe conserva todas sus secciones, gráficos e imagen Gantt y
contiene 100.000 filas en «Programadas en el período», igual que la prueba de
`revision_reports.md`.

El reloj comienza con el ImportBundle/ReportData ya construido y termina después
de guardar el ZIP, limpiar temporales y reemplazar atómicamente el destino.
La lectura posterior y la construcción del fixture quedan fuera del tiempo.
Los tiempos corresponden a las funciones Python; no incluyen HTTP ni SQL.

| Operación | Antes, medido aquí | Después, pasada final | Límite |
|---|---:|---:|---:|
| `export_project`, 100k actividades + 100k asignaciones | Interrumpido a los 65 s | 14,952 s | <20 s |
| Informe XLSX, 100k filas | 67,477 s | 10,278 s | <25 s |

La primera pasada completa de la solución con temporales comprimidos dio
14,987 s y 13,889 s, respectivamente; también cumplió ambos límites. La pasada
intermedia lenta dio 10,177 s y 12,960 s. La verificación final conjunta terminó
con **97 aprobadas y 7 omitidas por dependencias ausentes, en 236,65 s**; incluye
las tres pruebas lentas y la igualdad de ImportBundle completo después de exportar.
Una batería rápida anterior que incluía también `tests/reports/test_outages.py`
dio 97 aprobadas, 16 omitidas y 5 deseleccionadas. Son selecciones distintas;
sus conteos no se suman.

Se amplió el caso de aceptación con **22.339 tareas maestras**, además de las
100k actividades y asignaciones: **15,134 s**, límite <20 s. Sobre la base real
de QA, abierta con SQLite `mode=ro&immutable=1`, la llamada pública final a
`export_project` midió **11,246 s**, con esas mismas cardinalidades.
La reconstrucción previa del bundle SQL midió 43,983 y 33,177 s en dos pasadas;
queda fuera del límite de escritura y no se modificó el repositorio. Por ello
estos resultados no equivalen a un endpoint HTTP de menos de 20 segundos.

El límite de 65 s de la medición inicial abarcó también el arranque del proceso
y la creación del fixture: es una exportación interrumpida, no un tiempo de
terminación. Coincide con el bloqueo documentado previamente en `qa.md`.
La medición inicial del informe coincidió parcialmente con un perfil diagnóstico;
no se presenta como un ensayo con carga del equipo controlada.

Evidencia bajo `backend/tests/excel_io/_runtime/`:

- `performance-before/export.json`: interrupción del exportador original.
- `performance-spool/test_export_100000_activities0/benchmark.json`: exportación
  seguida de igualdad completa `parse_workbook(path) == bundle` sin incidencias.
- `performance-spool/test_xlsx_100000_rows_streamed0/benchmark.json`: informe,
  recuento de 100.000 filas y último código A099999 con el lector público.
- `final-slow/test_export_100000_activities0/benchmark.json` y
  `final-slow/test_xlsx_100000_rows_streamed0/benchmark.json`: repetición intermedia.
- `verified-final/test_export_100000_activities0/benchmark.json`,
  `verified-final/test_export_100000_with_22339_0/benchmark.json` y
  `verified-final/test_xlsx_100000_rows_streamed0/benchmark.json`: tres aceptaciones finales.
- `final-qa/verified-benchmark.json`: llamada pública final sobre el bundle de QA.
- La medición original del informe está en
  `backend/tests/reports/_runtime/performance-before/test_xlsx_100000_rows_streamed0/benchmark.json`.

Se conservan las mediciones intermedias: exportación 28,783 y 26,632 s; informe
19,390 s. Una variante con XML temporal sin comprimir dio 98,921 s de exportación
y 13,774 s de informe. No se descartó esa muestra: motivó reducir el tráfico de
disco del temporal, además del costo de CPU. El perfil de 100k de esa ruta registró
8,886 s sólo en eliminación de temporales y 7,728 s en compresión ZIP.
La primera prueba de la base real con 22.339 maestros dio 20,450 s y, después
de añadir buffers al gzip, 20,208 s. Esos incumplimientos se conservan en
`final-qa/benchmark.json` y `final-qa/buffered-benchmark.json`. La caché acotada
de codificación de valores y la eliminación de una segunda pasada por cada
fila completaron la mejora; no se modificaron ni relajaron los límites.

## Implementación

- Libro `Workbook(write_only=True)`. Las tablas grandes reciben filas de valores
  crudos; sólo sus encabezados usan `WriteOnlyCell`, con comentarios y estilos.
  Proyecto, Instrucciones, Listas y metadatos son hojas normales pequeñas dentro
  del mismo libro secuencial. Se preservan orden, nombres y columnas.
- Fuentes, alineaciones y formatos se registran una vez por columna/tipo. Los
  datos llevan el identificador del estilo compartido, sin construir estilos ni
  celdas por fila. Las fechas nativas y las fechas ISO procedentes del repositorio
  se muestran como `dd-mm-yyyy` (dd-mm-aaaa en Excel español); se conservan los
  formatos numéricos y las dos escalas de porcentaje: intercambio 0..100,
  informe 0..1.
- Los estilos de columna solos no formatean de forma fiable las celdas existentes.
  Por eso se escribe también el identificador de estilo en cada celda del XML.
  Referencia: [estilos de openpyxl](https://openpyxl.readthedocs.io/en/3.0/styles.html).
- Validaciones numéricas y listas: un rango por columna, desde fila 2 hasta
  1.048.576, nunca llamadas por celda. Se conservan nombres definidos, tablas,
  autofiltro explícito, paneles inmovilizados, anchos, impresión y formato
  condicional. En Avance de mantenimiento se conserva también la lista de
  trabajadores; sólo se omite la lista de ocurrencias aún no materializadas.
- `streaming.py` concentra el adaptador de openpyxl. Sin lxml, `et_xmlfile` domina
  incluso al pasar valores crudos: el perfil diagnóstico de 440.000 celdas mostró
  21,434 s en `etree_write_cell`, de 28,394 s totales bajo perfilado. El adaptador
  serializa una fila por vez, con caché limitada a 4.096 fragmentos de valor/estilo.
  Los valores repetidos no vuelven a crear objetos XML. Las coordenadas de celda
  consecutivas son implícitas (permitido por OOXML); los huecos son explícitos.
- El XML temporal se comprime con gzip nivel 1 mientras se escribe; al guardar
  se descomprime hacia el ZIP XLSX estándar, también a nivel 1, en bloques de
  256 KiB. No se carga la hoja completa en memoria. Los temporales quedan junto
  al destino, sin modificar `tempfile.tempdir` global. La escritura atómica y la
  limpieza se mantienen ante fallos de datos, construcción o ZIP.
- Se conservan escapes `\A`, `\N`, `\J`, texto literal que empieza por `=`,
  Unicode, espacios, CR/LF, ausencia frente a nulo y excepciones editables. Los
  textos demasiado largos se rechazan; no se truncan. No se evalúan fórmulas.
- El informe recorre el detalle una sola vez; calcula alturas durante esa pasada
  y libera las dimensiones de fila después de escribirlas. No se aplica el límite
  editorial de HTML/PDF/DOCX al XLSX.
- Los gráficos nativos usan series temporales agregadas. Las series diarias se
  agrupan por semana y las largas por mes en hojas auxiliares ocultas, sin cambiar
  las tablas de detalle. Curva S conserva el último acumulado del período; carga
  y capacidad suman HH, manteniendo huecos cuando faltan datos. Desempeño conserva
  el máximo de 20 trabajadores y el Gantt la imagen limitada existente. Para
  horizontes extremos se combinan semanas/meses consecutivos hasta un máximo
  de 520 puntos, sin descartar períodos.

## Pruebas y reproducción

Se agregaron pruebas rápidas de igualdad completa, columnas y formatos,
validaciones por rango, tablas vacías, fechas 1900/1904 contra el lector público
de openpyxl, fechas ISO del repositorio, texto literal, XML especial, limpieza y
preservación del destino ante fallos. Los gráficos diarios se verifican contra
valores agregados conocidos y sus referencias XML.

Las tres aceptaciones usan `@pytest.mark.slow`, 100.000 filas y límites estrictos
de 20/25 segundos. La prueba previa de importación masiva también queda marcada
`slow`, de modo que no se ejecuta accidentalmente en la batería rápida.

Desde `Ganttier/backend`, PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$PWD/../../tools/.venv/Lib/site-packages"
$env:TEMP = "$PWD/tests/excel_io/_runtime/performance-temp"
$env:TMP = $env:TEMP
$env:MPLCONFIGDIR = "$env:TEMP/matplotlib"
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
python -m pytest tests/excel_io tests/reports -q -p no:cacheprovider --import-mode=importlib --basetemp=tests/excel_io/_runtime/final-fast
python -m pytest -m slow -s tests/excel_io/test_export_performance.py tests/reports/test_reports.py::test_xlsx_100000_rows_streamed -q -p no:cacheprovider --import-mode=importlib --basetemp=tests/excel_io/_runtime/final-slow
# Selección conjunta ejecutada al cierre (97 aprobadas, 7 omitidas):
python -m pytest tests/excel_io tests/reports/test_reports.py --ignore=tests/excel_io/test_performance.py -o addopts= -s -q -p no:cacheprovider --import-mode=importlib --basetemp=tests/excel_io/_runtime/verified-final
```

El adaptador usa interfaces internas de openpyxl 3.1.x, aisladas en un archivo;
al actualizar la dependencia deben ejecutarse la comparación con su lector
público y las aceptaciones. No se instalaron dependencias ni se cambiaron los
modelos, repositorio, servicios o endpoints. No se certifica latencia HTTP ni
visualización en Excel de escritorio en este entorno.
