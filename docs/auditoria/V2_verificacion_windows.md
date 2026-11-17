# V2 — Verificación en Windows

Fecha: 4 de octubre de 2026. Ejecución principal: 01:10–01:27, UTC−03:00.

## Resultado

Se ejecutaron las nueve carpetas backend en procesos separados y `tests/gui` completo, en una sola corrida GUI con `QT_QPA_PLATFORM=offscreen`. Resultado principal: **1.275 aprobadas, 1 fallida, 1 omitida y 12 deseleccionadas**. No hubo errores de colección ni de fixtures. Backend: 1.058 aprobadas, ninguna fallida; GUI: 217 aprobadas y 1 fallida.

La única falla es un defecto de portabilidad de una aserción de prueba: compara rutas como cadenas con separadores distintos en Windows. El diagnóstico confirmó que `app_settings()` devuelve formato INI y una ruta dentro del directorio temporal esperado. No se identificó un defecto de producción en este caso. La suite no queda completamente aprobada mientras exista esa falla.

## Entorno y método

- Plataforma informada por Python: `Windows-10-10.0.26200-SP0`; Python 3.10.6, 64 bits. Zona local de la ejecución: UTC−03:00.
- Versiones: `pytest 9.1.1`, `pytest-qt 4.5.0`, `PySide6 6.11.2`, `numpy 2.2.6`, `fastapi 0.141.1`, `httpx 0.28.1`, `matplotlib 3.10.9`.
- Directorio de ejecución: `Ganttier/backend/`. Intérprete: `..\.venv\Scripts\python.exe`.
- Variables: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONIOENCODING=utf-8`; `QT_QPA_PLATFORM=offscreen` en GUI y reproducciones.
- `TEMP` y `TMP` apuntaron a `_encargos/v2_tmp/system_temp/`; `MPLCONFIGDIR` a `_encargos/v2_tmp/matplotlib/`; los datos y la BD de respaldo del entorno a `_encargos/v2_tmp/data/`. Las fixtures conservan sus propias redirecciones temporales. Se quitó del entorno del proceso `GANTTIER_TEST_TMP`, si estaba definido, para usar `tmp_path` y el `--basetemp` indicado.
- Se respetó `backend/pytest.ini`: `addopts = -m 'not slow'`. Las 12 deseleccionadas no se ejecutaron y se contabilizan aparte de la omitida con `skip`. No se instalaron dependencias.

Plantilla del comando, ejecutada una vez por carpeta (en orden: core, e2e, engine, excel_io, maintenance, modos, reports, solver, control, gui):

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
# Solo para tests/gui:
$env:QT_QPA_PLATFORM = 'offscreen'
..\.venv\Scripts\python.exe -m pytest tests/<carpeta> -q -p no:cacheprovider --basetemp ..\_encargos\v2_tmp\<carpeta> --junitxml ..\_encargos\v2_tmp\<carpeta>.xml -ra --durations=10
```

El ejecutor utilizó las rutas absolutas equivalentes; los comandos exactos están en [results.json](../../_encargos/v2_tmp/results.json). No se combinaron carpetas en una misma invocación. No se dividió ni se interrumpió la GUI.

## Resultados por carpeta

Tiempo pytest: atributo `time` del XML JUnit, en segundos. Tiempo proceso: reloj monotónico alrededor de cada invocación, incluidos arranque y salida. Las reproducciones diagnósticas posteriores no se suman a estos conteos.

| Carpeta | Aprobadas | Fallidas | Omitidas (skip) | Deseleccionadas (slow) | Tiempo pytest (s) | Tiempo proceso (s) | Salida |
|---|---:|---:|---:|---:|---:|---:|---:|
| `tests/core` | 105 | 0 | 0 | 1 | 23.93 | 24.79 | 0 |
| `tests/e2e` | 191 | 0 | 0 | 2 | 86.71 | 87.70 | 0 |
| `tests/engine` | 134 | 0 | 0 | 0 | 118.05 | 118.83 | 0 |
| `tests/excel_io` | 70 | 0 | 0 | 3 | 21.03 | 21.72 | 0 |
| `tests/maintenance` | 105 | 0 | 0 | 0 | 13.41 | 14.16 | 0 |
| `tests/modos` | 41 | 0 | 0 | 2 | 133.34 | 134.27 | 0 |
| `tests/reports` | 49 | 0 | 1 | 3 | 16.45 | 17.41 | 0 |
| `tests/solver` | 178 | 0 | 0 | 0 | 87.72 | 88.58 | 0 |
| `tests/control` | 185 | 0 | 0 | 1 | 9.78 | 10.81 | 0 |
| `tests/gui` | 217 | 1 | 0 | 0 | 524.97 | 526.59 | 1 |
| **Total** | **1275** | **1** | **1** | **12** | **1035.39** | **1044.86** | — |

La GUI completa incluyó todos los archivos priorizados:

| Archivo GUI | Aprobadas | Fallidas | Omitidas |
|---|---:|---:|---:|
| `test_r3_gui.py` | 5 | 0 | 0 |
| `test_auditoria_a4_gui.py` | 6 | 0 | 0 |
| `test_ux2.py` | 12 | 0 | 0 |
| `test_ux3.py` | 23 | 0 | 0 |
| `test_plataforma.py` | 2 | 0 | 0 |
| `test_h1_gui.py` | 5 | 1 | 0 |
| `test_analisis.py` | 4 | 0 | 0 |

## Falla y corrección mínima propuesta (sin aplicar)

**Caso:** `tests/gui/test_h1_gui.py::test_preferencias_respetan_el_formato_por_defecto`, línea 198.

**Mensaje resumido:** `AssertionError: assert (IniFormat == IniFormat and False)`. Falla `settings.fileName().startswith(str(tmp_path))`.

**Causa confirmada:** Qt devuelve `C:/Users/.../test_preferencias_respetan_el_0/Ganttier/Ganttier.ini`; `str(tmp_path)` produce `C:\Users\...\test_preferencias_respetan_el_0`. `startswith` compara caracteres, no rutas, por lo que rechaza la misma ubicación al encontrar `/` frente a `\`. Es un defecto de la prueba específico de la representación de rutas en Windows. No es una falla de selección de formato INI ni evidencia de que se esté usando el registro; tampoco apunta a bloqueo de archivos, fuentes, DPI o zona horaria.

**Evidencia adicional:** el caso original volvió a fallar aislado en 0,40 s. El script diagnóstico, sin editar producción ni pruebas, confirmó:

| Comprobación | Resultado |
|---|---|
| `settings.format() == QSettings.IniFormat` | `True` |
| Comparación textual original `startswith` | `False` |
| `Path(settings.fileName()).resolve().is_relative_to(base.resolve())` | `True` |
| Igualdad con `(base / 'Ganttier' / 'Ganttier.ini').resolve()` | `True` |

**Corrección mínima propuesta:** importar `Path` desde `pathlib` en la prueba y reemplazar la comparación textual por una comprobación de pertenencia entre rutas normalizadas. Conservar explícita la aserción de formato:

```python
from pathlib import Path

assert settings.format() == QSettings.IniFormat
assert Path(settings.fileName()).resolve().is_relative_to(tmp_path.resolve())
```

No requiere cambiar `backend/ganttier/gui/state.py::app_settings`, que ya pasa el formato por defecto explícitamente al constructor de `QSettings`. La propuesta no se aplicó y no se presenta como una suite corregida.

Reproducción aislada, desde `backend/` y con las mismas variables de entorno:

```powershell
..\.venv\Scripts\python.exe -m pytest tests/gui/test_h1_gui.py::test_preferencias_respetan_el_formato_por_defecto -q -p no:cacheprovider --basetemp ..\_encargos\v2_tmp\gui_repro --junitxml ..\_encargos\v2_tmp\gui_repro.xml
```

Evidencia: [GUI completa](../../_encargos/v2_tmp/gui.log), [XML de reproducción](../../_encargos/v2_tmp/gui_repro.xml), [diagnóstico de rutas](../../_encargos/v2_tmp/qsettings_diagnostic.json), [script diagnóstico](../../_encargos/v2_tmp/check_qsettings.py).

## Omisiones y advertencias

- `tests/reports/test_reports.py::test_pdf_unicode_is_extractable` fue omitida por `pytest.importorskip('pypdf')`: `No module named 'pypdf'` (línea 256). Es una dependencia de verificación ausente, no una falla Windows demostrada. Queda sin comprobar la extracción de texto Unicode del PDF. Acción mínima propuesta para una siguiente corrida: disponer de `pypdf` en el entorno de pruebas y repetir el caso. No se instaló ni modificó nada en `.venv/`.
- Doce casos quedaron deseleccionados por el filtro `not slow` existente. Esta auditoría no certifica esos casos.
- Siete carpetas informaron una `StarletteDeprecationWarning` por el uso de `httpx` con `starlette.testclient` (core, e2e, maintenance, modos, reports, solver y control). No causó fallas; no es evidencia de un defecto específico de Windows.
- La ejecución GUI fue offscreen. Sus resultados no certifican la apariencia ni la interacción en un escritorio real, ni una matriz de fuentes, DPI o zonas horarias distintas. No se observó una falla atribuible a esos factores en esta corrida.

## Integridad y evidencias

Se compararon hashes SHA-256 y el inventario de **3088 archivos de `backend/`** antes de la suite y nuevamente después de la reproducción: **0 agregados, 0 eliminados y 0 modificados**. No hay metadatos Git en esta carpeta; se usó esta comparación directa. Resultado: [integrity.json](../../_encargos/v2_tmp/integrity.json).

El único entregable es este documento. Los scripts auxiliares, logs, XML, snapshots y datos de ejecución están bajo `_encargos/v2_tmp/`, fuera de `backend/`. No se modificaron código de producción ni pruebas.

Para cada carpeta se conservaron `<carpeta>.log` y `<carpeta>.xml` en [el directorio de evidencias](../../_encargos/v2_tmp/). También están [environment.json](../../_encargos/v2_tmp/environment.json), [results.json](../../_encargos/v2_tmp/results.json) y [run_verification.py](../../_encargos/v2_tmp/run_verification.py).
