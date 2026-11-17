# Q4 — Control, desempeño e informes de escritorio

Implementación en `backend/ganttier/gui/pages/control.py`, `performance.py` y
`reports.py`. No se modifican la ventana, el cliente, el estado compartido, el tema,
los widgets comunes ni la API. `performance.py` contiene las pequeñas utilidades de
presentación y períodos que comparten estas tres páginas.

## Funciones implementadas

- Control: corte aplicado, selector de fecha y Hoy, creación/activación/eliminación
  de líneas base, diez tarjetas con todos los campos EVM solicitados, diferencia en
  días del término pronosticado y semáforos con texto y colores del tema.
- Curva S: día/semana/mes, HH/costo/cantidad, porcentaje/absoluto; cuatro series,
  proyección discontinua y línea de corte en su fecha exacta. Los valores ausentes
  se dibujan como interrupciones de serie, no como ceros.
- Avance: QTableView con 200 filas, filtros por corte/atraso/todas, búsqueda por
  código/nombre/instalación/habilidad, y editores de porcentaje, HH y costo del
  período, trabajador/cuadrilla, fechas reales y nota. Los borradores se conservan
  al paginar, filtrar y actualizar el mismo proyecto; se eliminan al cambiar de
  proyecto. La carga usa el endpoint columnar. Solo las actividades modificadas
  que necesitan completar el inicio real requieren una consulta individual.
- El avance se registra al corte aplicado. Si hay un borrador, se pide guardarlo o
  descartarlo antes de cambiar ese corte. El inicio faltante conserva el inicio
  real de la actividad o usa el planificado, limitado al corte. Al llegar a 100 %,
  el fin real faltante toma el corte. Se validan porcentaje, HH/costo finitos y no
  negativos, orden de fechas y fin real compatible con 100 %.
- Desempeño: semana/mes/año/período personalizado, KPIs agregados por cociente de
  totales, tabla virtual ordenable con los trece campos y semáforos, barras de los
  40 trabajadores con mayor valor no nulo para el indicador seleccionado.
- Informes: período calculado visible, PDF/Excel/Word/HTML, lista de resultados,
  descarga, Abrir y Guardar como, carpeta local elegible y vista de lectura HTML.
- Emisión automática: configuración completa, conservación por tipo, ejecución
  inmediata, historial con errores y botones por archivo. Se muestran hasta las
  50 emisiones más recientes, como en la referencia web. Se lee `emitted_at`
  (campo real del servidor), con compatibilidad para `generated_at`.
- Desconexiones: fechas desde/hasta, descarga Excel y nota sobre verificar formato
  oficial y plazos ante el Coordinador Eléctrico Nacional.

## Decisiones de integración

Todas las operaciones de API y descargas pasan por `run_task`; los valores de los
controles Qt se capturan antes de entrar al trabajador. Los callbacks comprueban
el proyecto y un número de solicitud para descartar respuestas antiguas. Las
mutaciones deshabilitan temporalmente la página para impedir el doble envío.

Una escritura confirmada y su recarga posterior son resultados distintos. Si el
POST de avance resulta correcto pero falla la lectura del proyecto, se consume el
borrador, se informa que el guardado ocurrió y se exige actualizar antes de seguir
registrando. Así no se suman otra vez las HH/costos del mismo borrador. Un rechazo
del POST conserva el borrador para corregirlo o reintentar.

La carpeta inicial para Abrir es `Ganttier/datos/informes_locales/proyecto-{id}`;
se crea solo al descargar. Guardar como respeta la ruta elegida. Los nombres
recibidos de la API se reducen a su componente de archivo al construir rutas
locales. Ningún informe ni base de datos de usuario fue creado durante este encargo.

La vista interna usa QTextBrowser: permite lectura, pero no reproduce todo el CSS
de un navegador. El botón Abrir permite ver el HTML completo con la aplicación
predeterminada. Los enlaces de la vista interna están desactivados.

## Verificación y límites del entorno

`backend/tests/gui/test_control_pages.py` contiene 13 casos de pytest (incluida la
parametrización), con `create_window()`, servidor embebido, proyectos demo por API,
demo de mantenimiento de 2000 actividades y `qtbot.waitUntil`. BD, datos, ajustes
Qt y caché Matplotlib quedan en el directorio temporal de pytest. Los diálogos de
archivo y la apertura externa se parchean en las pruebas.

Cobertura: corte, líneas base, registro, EVM y curva S actualizados, doble envío,
validación, recuperación tras errores de POST/GET, paginación y borradores,
cambio de proyecto, respuestas obsoletas, las tres bases y períodos de curva S,
tema, mes y rango personalizado de desempeño, orden y KPI globales, los cuatro
formatos con comprobación de firmas/contenido ZIP, vista HTML, configuración y
ejecución automática sin duplicados, historial y Excel de desconexiones.

Comando desde `Ganttier/backend` (todos los artefactos permanecen en el espacio de
trabajo; usar un `--basetemp` exclusivo para esta ejecución):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:QT_QPA_PLATFORM='offscreen'
python -B -m pytest -p no:cacheprovider tests/gui/test_control_pages.py --basetemp=../../trabajos/pytest_gui_q4
```

En este entorno faltan PySide6, pytest-qt, FastAPI, uvicorn, httpx, holidays,
python-docx, reportlab y python-multipart. La suite se recoge, pero se omite por
esas ausencias. No se instalaron dependencias ni se modificaron archivos ajenos
al encargo. Se verificó la sintaxis mediante compilación en memoria y se ejecutaron
de forma aislada las funciones reales de períodos (incluidos año cruzado y año
bisiesto), semáforos, fechas de emisión, validación y filtrado de avance, además del
caso POST confirmado/GET fallido.

Pendiente de verificación en un entorno equipado: ejecutar las 13 pruebas reales,
inspeccionar visualmente las tres pantallas en tema claro/oscuro y comprobar la
convivencia de los lienzos Qt con la generación de gráficos de informes (el módulo
de informes existente selecciona el backend Agg global de Matplotlib). No se ha
afirmado que la integración Qt ni los archivos generados hayan pasado en este
entorno sin sus dependencias.
