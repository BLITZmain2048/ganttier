# Arranque y empaquetado — decisión del 12-10-2026

El ejecutable `trabajos/exe_20261012a/dist/Ganttier/Ganttier.exe` reduce el arranque observado con copia fresca de 6,12 a 5,62 s de mediana y el paquete de 3.331 a 2.571 archivos. La medición tiene cinco pares; no demuestra una mejora garantizada en otros equipos. Se debe distribuir **toda la carpeta `dist/Ganttier`**, incluido `_internal`.

## Causa y cambio

La API importa el registro de importadores al iniciar. Los módulos `construccion`, `excel`, `e2_mp`, `om_wbs` e `inventario` importaban openpyxl en ese momento, aunque todavía no se había solicitado ninguna operación Excel. Sus imports se trasladaron a las funciones que usan cada símbolo. El import de NumPy en `services.py` se trasladó a `load_profile`.

La regresión offscreen crea QApplication, ventana principal y servidor API reales, llama también a `create_app` y comprueba `sys.modules`. Falló antes por openpyxl y pasa después. Vigila OR-Tools, matplotlib, reportlab, openpyxl, pandas, scipy, pypdf y pdfplumber. Los módulos distintos de openpyxl ya estaban diferidos en la ruta comprobada; no se atribuye su diferimiento a este cambio.

**NumPy sigue cargándose** por `repo.py` y `calendars.py`. El repositorio reconoce sus escalares/arrays en serialización y validación; los calendarios son vectorizados. Eliminar ese import requiere una revisión transversal y pruebas del núcleo, fuera de la propiedad de A. La prueba no oculta esta excepción.

## Paquete

- Sustituir `collect_all` por recopilación de datos y análisis de imports evita copiar fuentes `.py` como datos y recoger familias completas no usadas.
- OR-Tools se limita a las entradas CP-SAT y utilidades de intervalos. Se conservan sus DLL compartidas y pandas: `ortools.sat.python.cp_model` 9.15 importa pandas obligatoriamente.
- Se excluyen pruebas, familias Qt WebEngine/3D/Multimedia/QML/Quick no usadas por Ganttier y otras herramientas no utilizadas. Se preservan QtSvg, QtPdf y las dependencias de los lienzos QtAgg.
- Matplotlib conserva Agg, QtAgg, iconos, estilos y fuentes TTF. Solo se eliminan muestras y fuentes AFM/PostScript/PDF propias de backends que Ganttier no utiliza; sus informes PDF usan ReportLab y las TTF de matplotlib.
- `GANTTIER_BUILD_ASSETS` permite mantener el icono generado dentro de la carpeta de cada compilación.
- `construir_exe.bat` verifica que `PySide6.QtPdf` esté disponible antes de construir.

No se excluyen pandas, fuentes TTF ni las DLL de OR-Tools solo por su tamaño. La verificación congelada comprobó CP-SAT y los formatos generados con el mismo código empaquetado y bibliotecas del entregable.

## Lectura PDF congelada

PySide6.QtPdf se incluye explícitamente. El código anterior rechazaba el respaldo cuando `sys.frozen` era verdadero, porque `sys.executable -B archivo.py` no ejecuta Python dentro de una distribución windowed.

El lanzador reconoce ahora `--ganttier-extraer-pdf entrada.pdf salida.json` antes de iniciar la GUI. Ejecuta QtPdf en un proceso offscreen, limita la entrada a 30 MB y 100 páginas y escribe JSON. El importador aplica un timeout de 45 s y usa un directorio temporal. El archivo de resultado evita depender de stdout, que puede ser `None` en un ejecutable sin consola. No se crean objetos Qt en el hilo HTTP del proceso principal.

Se intentó instalar pypdf con pip y con índice explícito. La conexión a PyPI fue rechazada con `WinError 10013`; no se instaló pypdf ni pdfplumber. pypdf queda declarado como opcional y el spec incluye ambos lectores si están disponibles al construir. El respaldo QtPdf funciona sin ellos.

## Evidencia y límites

| Medida | exe_20261011b | exe_20261012a |
|---|---:|---:|
| Archivos | 3.331 | 2.571 |
| MB decimales | 316,93 | 299,68 |
| MiB | 302,25 | 285,79 |
| Principal, mediana de 5 frescas (s) | 6,12 | 5,62 |
| Principal, mínimo–máximo (s) | 5,85–7,07 | 5,25–5,84 |
| Services/diagnóstico, mediana de fase (s) | 1,87 | 1,64 |
| API lista, mediana de fase (s) | 0,90 | 0,59 |

Se reutilizó el procedimiento de `arranque_precarga_20261011`: copia nueva por lanzamiento, precarga `importar`, splash activo, sondeo de ventana cada 100 ms, servidor y datos aislados, monitor de CPU/health, cierre normal y restauración de preferencias. Se alternó el orden de las versiones entre rondas. Las diez corridas cerraron con código 0, sin terminación forzada.

Las copias no vacían la caché de Windows. La diferencia entre los extremos más próximos de los rangos es de solo 5 ms, inferior al intervalo de sondeo; cinco pares no permiten afirmar significancia o una mejora robusta. Hay trabajo concurrente de otros agentes. El registro `cambios_durante_build.json` identifica 20 archivos ajenos que cambiaron durante la construcción: este ejecutable no certifica la integración final de las cinco tareas.

La ventana del entregable y su lector PDF se comprobaron directamente. Un verificador auxiliar reutilizó **el mismo PYZ**, comprobado por SHA-256, y **2.570 archivos `_internal` idénticos**: ventana offscreen, programa de tres actividades con CP-SAT, exportación Excel, informes PDF/Excel/Word y previsualización de 126 filas del inventario PDF pasaron. Su entrada de prueba es diferente del lanzador normal; no se presenta como aceptación manual de los botones.

La aceptación visual quedó pendiente: `computer-use` no pudo acceder a la sesión donde se lanzó la prueba. La revisión automática rechazó abrir el lanzador visual con «Computer Use was not approved to use verificarganttiervisual». No se eludió el rechazo. El integrador debe repetir los flujos por la interfaz en una sesión habilitada y reconstruir desde una instantánea estable de las fuentes.

[Informe, pruebas, manifiestos y comandos reproducibles](../../trabajos/correcciones_20261012/A_arranque/INFORME.md).
