# Revisión adversarial de informes e intercambio Excel

Fecha: 26-09-2026. Contrato revisado: SPEC.md §9 y domain.py.
Alcance: reports/, excel_io/, sus pruebas y documentación de plantillas.
No se modificaron dominio, servicios, API, motores ni dependencias del proyecto.

## Hallazgos y correcciones

| Prioridad | Defecto reproducido | Corrección y regresión |
|---|---|---|
| P1 | `0` numérico rechazado; `-1`, `s.i` y `n/o` aceptados al eliminar signos de booleanos | Conversión exacta de SI/Sí/NO, true/false y 1/0. Pruebas parametrizadas de aceptación y rechazo. |
| P1 | `1,234.56` se convertía en 1,23456; miles mal agrupados podían cambiar de valor silenciosamente | Decimal determinado por el último separador cuando hay ambos y validación de grupos de tres cifras. Pruebas de formatos chileno/inglés y agrupación inválida. |
| P1 | Fórmula sin caché convertida silenciosamente en ausencia y luego en HH=0 | Lectura con fórmulas visibles y rechazo con hoja/fila/columna. Los errores Excel también se señalan. Los textos literales con `=` siguen siendo textos. |
| P1 | Relaciones A→B→C→A aceptadas | DFS iterativa O(V+E), error sobre una arista que cierra el ciclo. Pruebas de ciclo con sucesora externa y cadena de 2.000 nodos. |
| P1 | Exportación cambiaba None por un valor por defecto, recortaba espacios, confundía ausencia/cadena vacía y dividía habilidades con comas internas | Intercambio versionado con escapes explícitos en las celdas visibles. Prueba de igualdad de ImportBundle completo, incluidas excepciones, campos dispersos y extensiones del dominio. |
| P1 | Dimensiones XML A1:A1 podían ocultar actividades existentes | El lector procesa filas físicas, sin confiar en las dimensiones declaradas. Prueba con XML alterado. |
| P1 | Importación de 100.000 filas excedía 60 s en este equipo | Lectura XML por bloques, sin construir objetos de texto por celda ni recorrer anticipadamente toda la hoja cuando falta `<dimension>`. Se conservan metadatos y conversiones de openpyxl. Comparación contra su API pública para fechas 1900/1904/ISO, texto enriquecido, shared strings, porcentajes, errores y fórmulas. |
| P1 | Semáforo verde al completar tarde: SPI vuelve a 1 | Se prefiere SPI(t), con SPI como respaldo documentado. Prueba con SPI=1 y SPI(t)=0,7. |
| P2 | Resumen sin conclusiones accionables | Atraso/adelanto pronosticado en días corridos, SPI(t), cinco actividades más atrasadas y acciones sugeridas. No inventa atraso cero sin fechas. |
| P2 | La capacidad total ocultaba sobrecarga de un recurso | Conclusión calcula sobrecarga individual antes de sumar. Prueba con carga total dentro de capacidad pero un trabajador con 4 HH extra. |
| P2 | `None` en capacidad provocaba límites NaN y caída de Matplotlib; KPI libre dict rompía XLSX | Huecos explícitos, totales incompletos sin convertirlos en cero y serialización textual de KPI compuestos. Pruebas para cuatro formatos. |
| P2 | XLSX creaba cada celda y sus estilos en memoria | Workbook(write_only=True), estilos compartidos y vistas de filas. Tablas, filtros, paneles, encabezados de impresión, pies y gráficos nativos se conservan. Regresión con 100.000 filas, conteo y último código. |
| P2 | Fechas de carga en ISO, enteros sin miles y leyendas ilegibles con muchos recursos | Fechas dd-mm-aaaa, enteros chilenos en texto, formatos numéricos nativos en Excel. Hasta ocho recursos más Otros, sin perder carga/capacidad; desempeño gráfico limitado a 20 con aviso y tabla completa. |
| P2 | Fuentes PDF estándar no garantizaban símbolos eléctricos y Unicode | Se embebe DejaVu Sans distribuida con matplotlib. Prueba de extracción Ω, ≥, Ñ, tildes; pendiente de ejecutar con ReportLab/pypdf. |
| P2 | Error de Fecha_Fin de Proyecto apuntaba a fila 1 | Se conserva y reporta la fila real del campo. |
| P2 | Escritura fallida de exportación podía dañar el archivo previo; textos muy largos se cortaban en Excel | Exportación atómica. Rechazo explícito de textos sobre el límite nativo de 32.767 caracteres. Pruebas de preservación del archivo y limpieza de temporales. |
| P3 | Ejemplos eléctricos poco variados, Proyecto sin lista de calendario, listas con un millón de blancos | Ejemplos de S/E, paños, transformadores, interruptores, relés, líneas 66/110/154/220 kV, termografía, aceite y lavado. Lista de calendario y nombres basados en tablas Excel. |
| P3 | COUNTIF sobre un millón de filas en cada celda ralentizaba la edición | Aviso visual de duplicados acotado a 5.000 filas; importación valida la totalidad. Límite explicado en Instrucciones. |

## Decisiones de compatibilidad

- No se recalcula EVM ni se construye una línea base en reports. El atraso es
  la diferencia entre forecast_finish y planned_finish entregados por EVM.
- Los umbrales del semáforo permanecen en 0,95 y 0,85, aplicados primero a SPI(t).
- PDF, DOCX y HTML mantienen el límite editorial de 500 filas por tabla, con
  aviso. XLSX conserva las 100.000 filas del ensayo, sin ese límite editorial.
- Las fórmulas, incluso recalculadas, deben pegarse como valores al importar.
  Es una restricción deliberada para evitar resultados ausentes u obsoletos;
  no se pretende evaluar Excel con Python.
- Los libros comunes siguen usando valores por defecto. Los exportados tienen
  una hoja técnica de versión `_Ganttier` y marcadores visibles `\A`, `\N`, `\J`
  para preservar ausencia, nulidad y listas. No hay copia oculta de las entidades.
  Se validan los valores editados y todas sus referencias al reimportar.
- `excel_io/reader.py` concentra la integración interna con ExcelReader y
  ReadOnlyWorksheet (`read_only=True`). Se omite el escaneo anticipado de
  dimensiones, innecesario para una lectura secuencial. Expat procesa bloques
  de 64 KiB, excluye anotaciones fonéticas y prohíbe DTD/entidades externas.
  Las fechas, estilos, shared strings y metadatos del libro usan openpyxl;
  las fórmulas se detectan y rechazan, sin evaluarlas. Al actualizar openpyxl
  deben ejecutarse las pruebas de equivalencia y el benchmark.
- Los números de XLSX son numéricos: Excel presenta sus separadores regionales.
  HTML/PDF/DOCX y las etiquetas de gráficos usan notación chilena directamente.

## Evidencia de verificación

- Base previa, sin benchmark: 42 pruebas aprobadas y 4 omitidas por dependencias.
- Primera batería adversarial: 14 fallos reproducidos antes de corregirlos.
- Perfil del lector original: 5.001 filas, 3.566.390 llamadas; la construcción
  de objetos de texto enriquecido domina el costo evitable.
- Primera importación de 100.000 filas: 111,11 s (con ensayo XLSX concurrente).
- Una optimización intermedia midió 32,13 s aislada, pero volvió a 73,75 s en
  la batería completa y se descartó como resultado de cierre. El lector final
  evita además la pasada inicial por todas las filas para buscar dimensiones.
- Comprobación de compatibilidad antes del cierre: 69 aprobadas, 6 omitidas;
  dos ensayos masivos separados de esa ejecución.

### Resultado de cierre

- **83 casos aprobados y 7 omitidos por dependencias**, sobre 90 casos únicos,
  verificados mediante ejecuciones complementarias: suite general (76 aprobados,
  7 omitidos, 1 benchmark XLSX excluido), suite Excel final (59 aprobados,
  1 benchmark excluido; incorpora los casos adicionales del lector y locales)
  y benchmark XLSX final (1 aprobado). No se suman dos veces los casos repetidos.
- **Importación: 100.000 filas en 37,397 s**, cero incidencias, en la suite de
  cierre con el lector final. El ensayo aislado previo del mismo lector fue
  49,344 s; ambos cumplen 60 s. El costo de crear el archivo de prueba queda
  fuera de la medición de importación.
- **Informe XLSX: 100.000 filas en 67,233 s**, comprobación posterior de las
  100.000 filas y del último código A099999. Escritura secuencial sin lxml en
  este Python. Una ejecución anterior bajo otra carga del equipo dio 137,063 s;
  estos tiempos no son una garantía independiente del hardware o su carga.
- Las dos plantillas regeneradas se reimportaron sin incidencias y conservaron
  igualdad exacta al exportar/reimportar. Proyecto: tres actividades; mantenimiento:
  seis tareas maestras.
- Sintaxis verificada en los 20 archivos Python de ambos módulos y sus pruebas.
- Revisión visual de los PNG de curva S y carga apilada: fechas y leyendas
  legibles, corte visible, escala y capacidad conservadas. Ejemplo HTML y PNG
  en `backend/tests/reports/_runtime/revision_visual/`.

Evidencia: `backend/tests/reports/_runtime/verified.log`,
`verified/test_import_100000_rows0/benchmark.json` y
`xlsx_final/test_xlsx_100000_rows_streamed0/benchmark.json`. Los directorios de
ejecuciones anteriores conservan también la evidencia de fallos reproducidos.

## Límites de esta verificación

Este Python de Windows no tiene reportlab, python-docx ni pypdf. La instalación
local de prueba, dirigida al área autorizada, falló porque la red bloquea
sockets. Las pruebas PDF/DOCX se conservan con skips explícitos; **no se afirma
que las nuevas salidas PDF/DOCX hayan sido ejecutadas ni revisadas visualmente**.
Claude debe correr esas regresiones y revisar paginación con textos muy largos.

La regeneración usa `python -m ganttier.excel_io.templates --output-dir
ganttier/excel_io/generated` desde backend. Los dos binarios quedan dentro del
alcance autorizado. Los archivos de `Ganttier/plantillas/` deben publicarse desde
el entorno que tenga autorización para esa ruta.
