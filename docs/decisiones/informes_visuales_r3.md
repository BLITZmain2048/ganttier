# Informes visuales R3

Los cuatro formatos conservan la misma fuente ReportData producida por services.build_report_data. No se recalculan EVM, avances ni fechas. Portada con versión editorial R3 / 1, proyecto, período, fecha de corte y generación. Semáforo de costo CPI añadido al resumen y portada; umbrales existentes 0,95 y 0,85, nulo = sin datos. El semáforo de plazo mantiene SPI(t) preferente.

PDF, Word y HTML mantienen el contrato de hasta 500 filas por tabla. Los atrasos se seleccionan con heapq.nlargest(500) antes de convertirlos a celdas, memoria O(500); el aviso conserva el número total. Excel recibe todos los atrasos ordenados y todo el detalle mediante escritura secuencial. Los indicadores agregados son iguales entre formatos; igualdad no significa que PDF contenga todo el detalle de Excel.

HTML incorpora alternativas textuales descriptivas y scope de encabezados. Word incorpora descripción OOXML de cada gráfico. Se conserva tipografía, paleta de alto contraste, fuentes Unicode incrustadas en PDF y paginación existente. El Gantt Excel añade encabezado/pie y gráfico nativo carga-capacidad en la hoja de totales por período del modo trabajador. Sus fórmulas necesitan recálculo al abrir Excel, como los totales existentes.

La tabla de críticas y el gráfico semanal por habilidad consumen dos campos aditivos propuestos en CONFLICTOS.md: critical y skill_load. Sin integración no se inventan datos: el informe declara su ausencia. Cada habilidad tiene su propia comparación de carga y capacidad; el gráfico muestra las primeras seis y la tabla conserva el detalle según el límite editorial. La capacidad multihabilidad no representa personas distintas.

No cambia autoreports: reutiliza services.render_reports. No se modifica dominio, servicios, base real ni archivos de otros agentes. El tiempo de exportación masiva excluye la construcción de ReportData; el servicio todavía materializa sus listas de actividades, fuera de propiedad G1. Las mediciones y resultados exactos se publican en INFORME.md y tiempos.json del agente.

La aplicación original no se pudo escribir por el enlace fuera del área autorizada. El parche y los archivos propuestos están en agentes/G1; la validación se ejecutó sobre una copia local dentro del proyecto. Integración pendiente.
