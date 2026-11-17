# Q3 — Mantenimiento, intercambio Excel y distribución de escritorio

Fecha: 26-09-2026.

## Archivos y alcance

- `backend/ganttier/gui/pages/maintenance.py`: `MaintenancePage`, plan maestro,
  formulario de todos los campos editables de `PeriodicTask`, paginación de 200
  tareas, búsqueda, filtro por instalación y desconexión, cinco KPI, generación,
  mapa de calor con `QPainter`, calendario instalación × mes y descarga de desconexiones.
- `backend/ganttier/gui/pages/importexport.py`: `ImportExportPage`, plantillas y
  ejemplos, exportación, selección/arrastre de Excel, validación, importación nueva
  o al proyecto abierto, combinar/reemplazar, tabla de errores y advertencias.
- `backend/ganttier/gui/launcher.py`: preparación del entorno y de los flujos de
  salida antes de ejecutar el punto de entrada de la GUI.
- `ganttier_gui.spec`, `construir_exe.bat`, `iniciar.bat`,
  `iniciar_consola.bat`, `instalar.bat`, `requirements.txt`.
- `backend/tests/gui/test_maintenance_importexport.py`.

No se modifican `app.py`, `client.py`, `state.py`, `theme.py`, `widgets/common.py`
ni los motores o rutas de API. Los archivos se mantienen dentro de Ganttier.
La alternativa `iniciar_consola.bat` y `launcher.py` son las ampliaciones
expresamente autorizadas en el punto 3 del encargo.

## Interacción y trabajo en segundo plano

Las llamadas API, lectura de ejemplos y validación de libros se ejecutan mediante
`run_task`. Las modificaciones de widgets y publicación de catálogos tras importar
se hacen en los callbacks de la GUI. La carga de mantenimiento lleva un número de
petición y el identificador del proyecto para descartar respuestas antiguas.
Los botones de importación/programación impiden repetir una operación en curso.

El plan se edita con «Editar» o doble clic en una fila. Los campos de fecha opcionales
admiten vacío; lunes conserva el valor 0; la tensión vacía se envía como `null` y
admite coma decimal. Se incluyen también `wbs_code` y `horizon_end`.
La estimación anual de HH usa solamente las tareas activas del filtro actual.
El calendario cuenta ocurrencias periódicas con fecha, respeta instalación y
desconexión, incluye meses intermedios sin actividad y permite desplazamiento
horizontal en horizontes largos. Las tablas usan modelos, no celdas QWidget.
El mapa pinta solamente la región expuesta y obtiene sus colores de `tokens()`;
carga positiva con capacidad cero se pinta en rojo. El tooltip muestra HH y utilización.

### Generación y contrato actual

Se llama primero a `POST /api/projects/{pid}/schedule/solve` con:

```json
{
  "horizon_start": "2026-01-01",
  "horizon_end": "2026-12-31",
  "regenerate": false,
  "weights": {"level": 1, "window": 1},
  "options": {"assign_workers": true, "respect_resource_limits": true},
  "apply": true
}
```

El endpoint se incorporó al backend durante este encargo. Su campo es
`regenerate`, no `replace`. Solamente ante HTTP 404/405 se utiliza la alternativa
`maintenance/generate` con `replace` y luego `schedule/optimize` con `apply=true`.
Errores de validación, conexión o servidor no activan esa alternativa.
Si falla la segunda operación de la alternativa, se informa que las ocurrencias
ya fueron generadas y se recarga el plan. No se presenta una respuesta infactible
como programa aplicado; se muestran estado, métricas, advertencias y violaciones.

Hay dos diferencias del backend que requieren revisión de su propietario:

1. `repo.save_occurrences` protege ocurrencias con avances/bloqueos y referencias
   de línea base. La casilla mantiene el texto pedido en el encargo, está desmarcada
   inicialmente y tiene un tooltip sobre esta protección. La GUI no elimina
   avances por su cuenta ni evita esa protección del servidor.
2. La versión revisada de `solve_project` genera cuando `regenerate=true` o no
   existe ninguna ocurrencia en el horizonte. Con `regenerate=false`, añadir una
   tarea a un horizonte que ya tiene ocurrencias puede no expandir esa tarea nueva.
   Debe revisarse la semántica de generación incremental en el backend. La GUI
   transmite la elección del usuario sin convertirla silenciosamente en reemplazo.

### Validación e importación

Para el proyecto abierto se usa `/projects/{pid}/import?dry_run=true`. Para un
proyecto nuevo, la API `/import/new-project` no acepta `dry_run`: la vista previa
usa `excel_io.importer.parse_workbook` en segundo plano, sin crear proyecto ni
abrir BD. La pantalla avisa que la creación volverá a comprobar las referencias
a ocurrencias y recursos. No se simula una validación creando y borrando proyectos.

Se exige validar antes de importar en ambos destinos. Archivo, fecha de modificación,
tamaño, destino, modo e identificador de proyecto forman la firma de validación.
Si cambian, se pide validar nuevamente. Un 422 con `errors` se presenta en la tabla,
incluyendo hoja, fila, campo, severidad y mensaje. Reemplazar un proyecto requiere
confirmar en el propio diálogo de la aplicación. Después de importar se recuperan
proyecto, calendarios y recursos; una navegación concurrente no cambia de vuelta
el proyecto elegido por el usuario. Si falla esta recarga se informa que la
importación sí ocurrió, para evitar repetirla por error.

## Arranque e instalación

Requiere Windows y Python 3.10 o superior con distribución PySide6 compatible.

1. Ejecutar `instalar.bat`. Conserva la instalación opcional de OR-Tools mediante
   `requirements-opcional.txt` y el funcionamiento heurístico cuando no está disponible.
2. Ejecutar `iniciar.bat` para abrir la GUI sin mantener una consola.
3. Usar `iniciar_consola.bat` para diagnosticar: ejecuta directamente
   `.venv\Scripts\python.exe -m ganttier.gui` con `PYTHONPATH=backend`.

`iniciar.bat` ejecuta `pythonw -m ganttier.gui.launcher`. El launcher prepara
`stdout/stderr` cuando son `None` y ejecuta `ganttier.gui` mediante `runpy`, equivalente
al punto de entrada `python -m ganttier.gui`. Este paso evita que uvicorn u otra
biblioteca fallen al consultar/escribir los flujos en modo sin consola, sin editar
el `__main__.py` compartido. El lanzador comprueba dependencias antes de abrir la
ventana. Si falla el arranque sin consola, consultar `datos/ganttier.log` y ejecutar
la alternativa de diagnóstico. Los cuatro `.bat` usan CRLF y pausa en errores.

## Construcción del ejecutable

En una máquina Windows con acceso a los paquetes Python:

```bat
cd /d RUTA\Ganttier
instalar.bat
construir_exe.bat
```

La construcción instala `pyinstaller>=6.6,<7` en `.venv`, comprueba dependencias y
ejecuta `python -m PyInstaller --noconfirm ganttier_gui.spec`. El resultado esperado
es `dist\Ganttier\Ganttier.exe` (onedir, sin consola). Distribuir **toda** la carpeta
`dist\Ganttier`, incluido `_internal`; copiar solo el `.exe` no es suficiente.
Cerrar una copia anterior antes de reconstruir. El comando reemplaza el resultado
anterior de compilación; no contiene la BD del usuario.

El spec incluye `ganttier/schema.sql`, los Excel de `plantillas/` y `ejemplos/`,
datos y módulos de `holidays`, páginas de importación dinámica, módulos de uvicorn,
openpyxl, reportlab y python-docx. Recoge OR-Tools y sus binarios solo si está
instalado. Matplotlib incluye los backends `QtAgg` y `Agg`; se excluyen otros
bindings Qt para evitar mezclas. Si existe `ganttier.ico` se usa; si no, se genera
`build/gui_assets/ganttier.ico` con Pillow y colores del tema.

Referencias de construcción: [opciones de hooks de PyInstaller](https://pyinstaller.org/en/stable/hooks-config.html)
y [utilidades de recolección de datos y módulos](https://pyinstaller.org/en/latest/hooks.html).

### Datos del ejecutable

Antes de importar la aplicación, el launcher configura por defecto:

- `GANTTIER_DATA_DIR=%LOCALAPPDATA%\Ganttier`.
- `GANTTIER_DB=%LOCALAPPDATA%\Ganttier\ganttier.db`.
- Informes en `GANTTIER_DATA_DIR\informes`, corridas en `runs`, temporales en `tmp`.
- Caché Matplotlib en `GANTTIER_DATA_DIR\matplotlib` y salida sin consola en
  `GANTTIER_DATA_DIR\ganttier.log`.

Las variables explícitas se respetan. En ejecución desde fuentes se conserva la
ubicación habitual `Ganttier/datos` si no se configuran variables. El `.exe` no
copia ni migra automáticamente bases existentes.

Para probar sin escribir fuera de esta carpeta de trabajo:

```bat
set "GANTTIER_DATA_DIR=%CD%\build\prueba_exe\datos"
set "GANTTIER_DB=%CD%\build\prueba_exe\datos\ganttier.db"
set "LOCALAPPDATA=%CD%\build\prueba_exe\localappdata"
set "APPDATA=%CD%\build\prueba_exe\appdata"
set "TEMP=%CD%\build\prueba_exe\tmp"
set "TMP=%TEMP%"
mkdir "%TEMP%"
dist\Ganttier\Ganttier.exe
```

Comprobar creación de proyectos, abrir ambas pantallas, guardar una tarea, generar
un programa pequeño factible, cambiar de tema, descargar plantilla/desconexiones,
importar y exportar. Verificar también informes PDF/DOCX/Excel para cubrir las
dependencias empaquetadas, y repetir sin OR-Tools en un entorno independiente.
La verificación visual y del ejecutable necesita una máquina con Qt instalado.

## Pruebas y verificación realizada

```bat
cd /d RUTA\Ganttier
set "QT_QPA_PLATFORM=offscreen"
set "PYTHONPATH=%CD%\backend"
set "PYTHONDONTWRITEBYTECODE=1"
.venv\Scripts\python.exe -m pytest backend\tests\gui\test_maintenance_importexport.py -q --basetemp=.venv\tmp\pytest-q3 -p no:cacheprovider
```

La fixture crea la ventana real con `create_window()`, API embebida, BD y datos
temporales, configuración Qt en un archivo temporal y demos `proyecto` y
`mantenimiento` de 2000 ocurrencias. Las acciones se esperan con `qtbot.waitUntil`.
Se ejercitan CRUD con FormDialog real, campos completos, filtros/paginación,
programación normal y alternativa, asignación, calendario, pintura/tema y descarga
de desconexiones, validación/importación de ambas plantillas y ejemplos,
combinar/reemplazar, exportación, archivos inválidos, invalidación de la validación,
arrastrar/soltar y rechazo de fallback ante error 422.

Las pruebas que no requieren Qt comprueban rutas de datos y overrides, restauración
de flujos sin consola, CRLF/dependencias y correspondencia de campos con el backend.
La sesión local no dispone de PySide6, pytest-qt ni PyInstaller. El intento de
instalación en `.venv` falló por bloqueo de conexión de Windows (`WinError 10013`).
No se afirma que las pruebas gráficas pasen ni que el ejecutable haya sido construido.
Resultado local: **4 pruebas aprobadas y 11 omitidas por falta de PySide6**.
También se compiló la sintaxis de ambos módulos de página, launcher, pruebas y
spec, y se verificó CRLF en los cuatro archivos batch. Quedan pendientes la
ejecución offscreen con Qt, la inspección visual y la construcción/prueba del `.exe`.
