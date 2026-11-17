# V1 — Verificación Windows

Fecha: 3 de octubre de 2026, zona America/Santiago. Ejecución principal: 22:12:37–22:31:19, aproximadamente 18 min 42 s.

Repositorio comprobado: `C:\Users\<usuario>\Claude\Codex-Compartido\Ganttier` (subcarpeta del directorio inicial). Trabajo de verificación únicamente: no se modificó código ni pruebas, ni se instalaron o actualizaron dependencias.

## Resultado

Las nueve carpetas backend finalizaron con **990 aprobadas, 0 fallidas, 1 omitida y 10 excluidas**. La carpeta GUI completa finalizó con **203 aprobadas y 3 fallidas**. Total de la ejecución principal: **1193 aprobadas, 3 fallidas, 1 omitida y 10 excluidas**; 1197 casos seleccionados y 1207 contando los excluidos. No hubo errores de colección ni de preparación/desmontaje registrados en los XML.

Los tres fallos GUI se reprodujeron juntos en una segunda ejecución: **3 fallidas en 17,83 s**, código de salida 1. Esta repetición no se suma al total anterior.

## Entorno

- Python: **3.10.6**, MSC v.1932, 64 bits AMD64.
- Ejecutable: `.venv\Scripts\python.exe` del repositorio.
- Plataforma informada por Python: `Windows-10-10.0.26200-SP0`.
- GUI: `QT_QPA_PLATFORM=offscreen`.

| Biblioteca | Versión |
|---|---|
| fastapi | 0.141.1 |
| pydantic | 2.13.5 |
| ortools | 9.15.6755 |
| numpy | 2.2.6 |
| PySide6 | 6.11.2 |
| pytest | 9.1.1 |
| pytest-qt | 4.5.0 |
| starlette | 1.7.0 |
| httpx | 0.28.1 |
| openpyxl | 3.1.5 |
| matplotlib | 3.10.9 |
| reportlab | 4.5.1 |
| python-docx | 1.2.0 |

## Método y conteos

Se ejecutaron las carpetas secuencialmente, cada una en un proceso nuevo y desde `backend`, con este comando (sustituyendo `<carpeta>`):

```powershell
..\.venv\Scripts\python.exe -m pytest tests\<carpeta> -q -p no:cacheprovider --basetemp=..\trabajos\pytest_v1_20261003_01\<carpeta> --junitxml=..\trabajos\pytest_v1_20261003_01\<carpeta>.xml
```

Antes de GUI se estableció `$env:QT_QPA_PLATFORM='offscreen'`. Para todos los procesos, `TEMP` y `TMP` apuntaron a `Ganttier\trabajos\pytest_v1_20261003_01\tmp`, y `PYTHONDONTWRITEBYTECODE=1`. Se conservó `backend/pytest.ini`, cuyo `addopts = -m 'not slow'` excluye las pruebas lentas. No se sustituyó esa selección ni se ejecutaron los casos excluidos.

| Carpeta | Aprobadas | Fallidas | Omitidas | Excluidas (`slow`) | Tiempo pytest (s) | Salida |
|---|---:|---:|---:|---:|---:|---:|
| core | 60 | 0 | 0 | 0 | 12.370 | 0 |
| e2e | 191 | 0 | 0 | 2 | 89.298 | 0 |
| engine | 134 | 0 | 0 | 0 | 125.537 | 0 |
| excel_io | 70 | 0 | 0 | 3 | 19.992 | 0 |
| maintenance | 105 | 0 | 0 | 0 | 13.709 | 0 |
| modos | 41 | 0 | 0 | 2 | 130.247 | 0 |
| reports | 49 | 0 | 1 | 3 | 16.347 | 0 |
| solver | 175 | 0 | 0 | 0 | 90.410 | 0 |
| control | 165 | 0 | 0 | 0 | 8.987 | 0 |
| gui | 203 | 3 | 0 | 0 | 604.815 | 1 |

El tiempo de la tabla procede de JUnit; los tiempos completos de cada proceso se conservan en `exit_codes.csv`. GUI tardó 604,82 s según el resumen pytest (606,91 s incluyendo arranque y cierre del proceso). Se ejecutó toda la carpeta, sin recurrir al subconjunto mínimo. Los cuatro archivos solicitados están incluidos:

| Archivo GUI solicitado como mínimo | Aprobadas | Fallidas |
|---|---:|---:|
| test_plataforma.py | 2 | 0 |
| test_h1_gui.py | 4 | 1 |
| test_analisis.py | 4 | 0 |
| test_auditoria_a3.py | 8 | 0 |

## Fallos y diagnóstico

### 1. Vistas guardadas

- Prueba: `tests/gui/test_h1_gui.py::test_vistas_guardadas_restauran_filtros_columnas_y_escala`.
- Ubicación de la aserción: `backend/tests/gui/test_h1_gui.py:141`.
- Error: tras `page.save_view("Reunión semanal")`, se esperaba esa clave en `page.saved_views()`, pero se obtuvo `{}`.
- Diagnóstico: **diferencia de plataforma y permisos del entorno, junto con un defecto de aislamiento de la prueba**. La fixture configura `QSettings.setDefaultFormat(IniFormat)` y una ruta INI temporal, pero la aplicación construye `QSettings("Ganttier", "Ganttier")`. En este Windows/PySide6 ese constructor sigue usando `NativeFormat`, con destino `HKEY_CURRENT_USER\Software\Ganttier\Ganttier`. La comprobación de solo lectura `isWritable()` devolvió `False`. Un constructor con formato INI explícito pudo escribir y leer correctamente dentro de `trabajos`.
- Evidencia del código: `gui/state.py:43`, `gui/pages/activities.py:494–510` y fixture `h1_window`. Las vistas se almacenan como JSON mediante `setValue`; no se comprueba el estado de persistencia.
- Límite: no se probó una sesión Windows con acceso al registro ni otra versión de PySide6. No se demuestra una regresión entre versiones ni un fallo general en Windows sin estas restricciones. La falta de comprobación de errores de persistencia también deja un fallo silencioso de la aplicación en este entorno.

### 2. Carpeta local de informes

- Prueba: `tests/gui/test_paginas_f3.py::test_ruta_informes_data_dir_y_persistencia`.
- Ubicación de la aserción: `backend/tests/gui/test_paginas_f3.py:187`.
- Error: una nueva `ReportsPage` recupera `datos/informes_locales` en vez del directorio elegido `informes_elegidos`.
- Diagnóstico: **la misma diferencia de plataforma/permisos y defecto de aislamiento QSettings del caso anterior**. `ReportsPage` crea un `QSettings("Ganttier", "Ganttier")` propio (`gui/pages/reports.py:144`); `choose_directory()` escribe `reports/download_directory` (`:484–488`) y la siguiente página vuelve a leer el valor por defecto. La fixture tampoco fuerza el formato en ese constructor.
- Límite: reproducido en esta sesión restringida; no se atribuye a un fallo del backend ni a una regresión de versión demostrada. No se alteró el registro para diagnosticarlo: la sonda nativa solo consultó formato, destino y capacidad de escritura.

### 3. Texto de la acción Vista previa

- Prueba: `tests/gui/test_ux4.py::test_reports_empty_then_generated_files`.
- Ubicación de la aserción: `backend/tests/gui/test_ux4.py:158`.
- Error: se esperaba `{'Abrir', 'Guardar como…', 'Vista previa'}` y se obtuvo `{'Abrir', 'Guardar como…', ''}`.
- Diagnóstico: **defecto de la expectativa de la prueba frente al diseño adaptable**, sensible al ancho y a las métricas de presentación del entorno. `gui/pages/reports.py:308–312` crea el botón con texto y lo coloca en `Toolbar`; `gui/widgets/common.py:865–898` compacta los botones secundarios con icono cuando no caben y llama deliberadamente a `setText("")`, empezando por la derecha.
- Comprobación directa con los componentes existentes: con una barra estrecha, `text()==''`, `_full_text=='Vista previa'` y `accessibleName()=='Vista previa'`; al ampliar la barra, `text()=='Vista previa'`. La acción no desaparece. La aserción supone que siempre hay texto visible, aunque el componente admite modo compacto. No hay evidencia de un defecto de generación de informes: la prueba llegó a comprobar que se generaron archivos y se mostró la tabla.
- Límite: no se compararon métricas con otro sistema operativo o versión Qt; no se afirma que sea exclusivo de Windows o de PySide6 6.11.2.

## Omisiones y advertencias

- `tests/reports/test_reports.py::test_pdf_unicode_is_extractable`: omitida porque `pypdf` no está instalado (`No module named 'pypdf'`). Esa propiedad del PDF no quedó verificada. No se instalaron dependencias.
- Diez pruebas excluidas por la selección `not slow`: e2e 2, excel_io 3, modos 2, reports 3.
- Siete advertencias en backend: Starlette indica que el uso de `httpx` con `TestClient` está deprecado y menciona `httpx2`. No ocasionó fallos.
- En las trazas GUI aparece `This plugin does not support propagateSizeHints()`, propio del plugin Qt empleado; no basta por sí solo para explicar los fallos.

## Integridad y evidencias

Se compararon hashes SHA-256 antes y después: **197 archivos Python y `pytest.ini` de backend sin cambios**, excluyendo dependencias vendorizadas `.deps` y cachés `__pycache__`. No se aplicaron correcciones.

Evidencia en [trabajos/pytest_v1_20261003_01](../../trabajos/pytest_v1_20261003_01/):

- Un `<carpeta>.log` y `<carpeta>.xml` por ejecución principal; códigos de salida y duración de proceso en `exit_codes.csv`. Los nueve procesos backend devolvieron 0; GUI devolvió 1.
- [gui.log](../../trabajos/pytest_v1_20261003_01/gui.log) y [gui.xml](../../trabajos/pytest_v1_20261003_01/gui.xml): trazas completas y conteos GUI.
- [gui_repro.log](../../trabajos/pytest_v1_20261003_01/gui_repro.log) y `gui_repro.xml`: repetición de los tres nodos fallidos, con los mismos argumentos pytest y temporales separados en `gui_repro`.
- [diagnostico.txt](../../trabajos/pytest_v1_20261003_01/diagnostico.txt): sondas QSettings y compactación de la barra; la única escritura de la sonda de persistencia fue un INI explícito dentro de esta carpeta.
- `entorno.json`, `hashes_antes.csv` y `hashes_despues.csv`.

Todos los registros, XML, temporales dirigidos por esta ejecución y este informe permanecen dentro de la carpeta de trabajo inicial. La redirección INI pretendida por las fixtures no alcanza a los constructores QSettings nativos señalados: esa limitación se observó y se documentó, sin cambiar las pruebas.

