# Integración de las correcciones del 12-10-2026

Se conservan las contribuciones A (arranque/empaquetado), B (interfaz/destino), D (estabilidad), H1 (Avance), H1b (indicadores) y H2 (Gantt/accesibilidad). La auditoría SHA-256, conflictos y resultados están en `trabajos/correcciones_20261012/INT_integracion/`.

## Contratos conservados

- Avance solicita páginas de 200 actividades. Los borradores sobreviven a filtros y cambios de página; un guardado confirmado no se vuelve a enviar si falla la recarga posterior.
- El panel comparte cálculo de EVM y curva S. Los cambios de avance recalculan las contribuciones afectadas; escrituras externas invalidan la caché. El muestreo del gráfico conserva extremos y corte, mientras la API sin `max_points` devuelve la serie completa.
- El precálculo especulativo al abrir un proyecto se limita a 150.000 actividades, la cota de retención de la caché. Para proyectos mayores, Control calcula al solicitarse: evita cargar Matplotlib y competir con el primer Gantt de 250.003 actividades. Se mantienen el precálculo de 100.000 y los cálculos completos bajo demanda.
- El Gantt y la tabla de avisos exponen una celda con foco y un resumen de fila, como máximo dos hijos accesibles propios. Se recorre cualquier fila mediante teclado. No se ofrece exploración de millones de celdas mediante patrones de tabla ni se declara validación con voz de todos los lectores de pantalla.
- El lector PDF congelado usa QtPdf en un proceso auxiliar offscreen cuando no hay un lector independiente. Se conservan los límites de tamaño/páginas y la revisión del inventario antes de importar.
- La captura de minidumps corresponde a excepciones nativas no controladas. Una traza de primera oportunidad `0x8001010d` no demuestra por sí sola que el proceso terminó. D04 continúa sin causa nativa atribuida; se requiere el volcado del incidente para investigarlo.
- El cierre espera tareas y servidor, con vigilancia del proceso ante bloqueos. Una salida forzada queda registrada y no equivale a cierre limpio. No se intervienen instancias ajenas a la prueba.
- La espera aceptada de tareas es de 30 s y la del servidor de 5 s, con vigilancia global de 45 s. Al agotarse la espera no se vuelve a dejar la ventana abierta. Los callbacks tardíos se descartan; una operación pendiente puede interrumpirse. Cancelar antes de aceptar el cierre sigue disponible.

## Autoprueba reproducible del mismo ejecutable

La integración incorpora un despacho explícito y diferido en `gui/launcher.py`:

```text
Ganttier.exe --ganttier-autoprueba CARPETA_NUEVA RUTA_INVENTARIO_PDF
```

La carpeta de salida debe no existir y el PDF debe ser una muestra de inventario reconocible por el importador. Un argumento inválido devuelve 2. El modo redirige SQLite, datos, preferencias INI, temporales y caché de Matplotlib a esa carpeta antes de cargar la aplicación. Usa Qt offscreen y el arranque habitual, incluida precarga, servidor, diagnóstico y cierre. No expone la credencial de sesión ni añade endpoints de prueba.

Comprueba servidor listo, imports pesados diferidos, programa pequeño generado por API, exportación Excel, previsualización del inventario PDF y generación desde la página Informes de PDF/XLSX en un destino con espacios y acentos. Verifica contenido de los archivos, parada del servidor y liberación del lock. Escribe `resultado.json`; devuelve 0 solo si todas esas comprobaciones pasan y la GUI sale con código 0. El proceso exterior debe comprobar también su código de salida.

Este modo no se activa en el arranque habitual. No reemplaza la aceptación manual de apariencia/clics ni las pruebas de carga. No provoca fallos nativos deliberados; los ensayos destructivos de minidump permanecen en procesos de prueba propios.

## Rendimiento y límites

Los umbrales de los agentes se mantienen. La aceptación de rendimiento depende de mediciones completas de la integración, no de extrapolar conteos de suites seleccionadas. Consultar el informe final para resultados de 100.000 y 250.003 actividades, pruebas heredadas y cualquier suite que no haya terminado. La distribución se reconstruye con `ganttier_gui.spec` de A y se entrega con toda su carpeta `_internal`.
