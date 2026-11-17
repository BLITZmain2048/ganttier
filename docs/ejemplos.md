# Ejemplos eléctricos importables

Los libros de [ejemplos](../ejemplos/) contienen datos ficticios completos y usan
las mismas hojas, columnas, listas desplegables y formatos de las plantillas
oficiales. No necesitan fórmulas ni macros. Los importes están expresados en CLP.

## Importar en Ganttier

1. Abra **Importar/Exportar → Crear proyecto desde archivo**.
2. Seleccione uno de estos libros:
   - [Ejemplo_Proyecto_SE_220kV.xlsx](../ejemplos/Ejemplo_Proyecto_SE_220kV.xlsx).
   - [Ejemplo_Plan_Mantenimiento.xlsx](../ejemplos/Ejemplo_Plan_Mantenimiento.xlsx).
3. Revise la vista previa y confirme la creación. La hoja **Proyecto** identifica
   automáticamente el modo y el horizonte; no importe los dos libros en el mismo proyecto.
4. En el ejemplo de obra, abra **Actividades + Gantt** y ejecute la programación
   CPM; revise luego **Recursos y calendarios**, **Programación macro** y **Control**.
5. En el ejemplo de mantenimiento, abra **Mantenimiento** y genere las ocurrencias
   entre el **01-01-2027 y el 31-12-2027**. Después ejecute la nivelación por
   habilidad/recurso y revise las ventanas de desconexión.

Para conservar cambios realizados en la aplicación, use **Importar/Exportar**
y exporte el proyecto a Excel. Esa exportación puede incorporar columnas de
programación y control adicionales, admitidas por el importador.

## Proyecto: ampliación S/E 220/66 kV

**Ampliación S/E 220/66 kV – paño de transformador 150 MVA**, en la S/E ficticia
Alto Maitén. Horizonte: 04-01-2027 a 30-06-2028; corte de avance: 22-01-2027.

| Contenido | Cantidad |
|---|---:|
| Actividades, incluidos los hitos | 120 |
| Hitos de duración cero | 6 |
| Nodos WBS | 30 |
| Relaciones | 133 |
| Recursos | 18 |
| Asignaciones | 118 |
| Registros de avance | 9 |
| Calendarios | 1 |

La WBS tiene exactamente tres niveles: proyecto, disciplina y paquete. Sus
20 paquetes cubren ingeniería básica/detalle, permisos, adquisiciones del
transformador, interruptores, desconectadores, TT/CC y pararrayos; fundaciones,
malla de tierra y canaletas; montaje, conexiones, SS/AA, protecciones, SCADA,
FAT/SAT, energización y puesta en servicio.

La red contiene relaciones FS, SS y FF con desfases positivos. Hay restricciones
SNET para la obra civil y la conexión final, y FNLT para la puesta en servicio.
Parte de las actividades tiene **Mes_Macro**, con ventanas blandas orientativas.
El programa definitivo se calcula en Ganttier; el libro no contiene una línea
base ni asegura factibilidad después de nivelar recursos.

Las duraciones son días laborables. Para trabajo directo se estiman 8 HH por
persona y día; fabricación y curado usan 0,5 HH por persona y día para
seguimiento, mientras la duración representa el plazo transcurrido en días
laborables. El transformador considera 180 días laborables de fabricación.
Los recursos de tipo cuadrilla representan grupos disponibles: capacidad de
8 HH por persona, unidades máximas iguales a su dotación y costo por HH de
persona. Las asignaciones usan esa misma convención. Se incorporan además
apoyos de inspección, prevención e izaje en actividades específicas.

Los costos presupuestados representan **mano de obra**, no el presupuesto total
de inversión: excluyen suministro de equipos, IVA y gastos generales. Son
supuestos ilustrativos, no cotizaciones. La suma de HH de las asignaciones
coincide con las HH de cada actividad.

**Avance** contiene registros de las primeras tres semanas para arranque,
levantamiento, estudios y permisos. El porcentaje es acumulado a cada corte;
las HH y el costo real son incrementales por registro, no acumulados. Las
HH_Reales de Asignaciones resumen esos registros por actividad/recurso.

## Plan anual de mantenimiento

**Plan anual 2027 — Red ficticia Valle Austral**, con horizonte del 01-01-2027
al 31-12-2027. Contiene **72 tareas maestras**, **30 trabajadores** y **3 calendarios**.
Las actividades todavía no están materializadas: una tarea mensual origina
varias ocurrencias durante el año. La hoja Avance se entrega vacía para evitar
referencias a ocurrencias aún no generadas.

Las 12 subestaciones son Alto Maitén, Las Brumas, Valle Claro, Piedra Azul,
Los Arrayanes, Loma Serena, Río Seco, Monte Canelo, Quebrada Verde, Laguna Norte,
Portal del Sur y Cerro Lucero. Las seis líneas ficticias unen esos nombres por
pares consecutivos. Cada subestación tiene cinco tareas; cada línea, dos.

| Alcance | Frecuencia y criterio ilustrativo |
|---|---|
| Inspección visual de las 12 S/E | Mensual |
| Termografía de las 12 S/E | Trimestral: enero, abril, julio y octubre |
| Muestreo de aceite para DGA de las 12 S/E | Semestral: marzo y septiembre |
| Pruebas de relés de las 12 S/E | Anual, mayo; requiere desconexión |
| Interruptores de seis S/E | Cada seis meses, abril y octubre; requiere desconexión |
| Malla de tierra de las otras seis S/E | Anual, febrero; se supone desconexión por el método de ensayo |
| Lavado de aislación de las seis líneas | Semestral, marzo y septiembre; línea desenergizada |
| Inspección de franja de las seis líneas | Mensual |

Cada tarea incluye duración, HH por ejecución, dotación, habilidad, tolerancias
en días corridos, meses y día preferidos, instalación y equipo. Las tareas con
**Requiere_Desconexion=SI** usan ventanas duras; la etiqueta expresa una necesidad
de planificación y no una autorización de maniobra. Las frecuencias, métodos
y tolerancias son ejemplos, no exigencias normativas ni instrucciones de terreno.

Los 30 trabajadores tienen nombres ficticios, habilidades y costos por HH.
Dieciocho usan **Chile 5x2**; seis usan **Brigada 7x7 A** y seis **Brigada 7x7 B**,
con anclas 01-01-2027 y 08-01-2027 respectivamente. Los turnos tienen jornadas
de 8 horas y no suspenden su ciclo por feriados; el calendario 5x2 usa feriados
de Chile. Algunas inspecciones fijan un trabajador y su calendario; las demás
permiten nivelar por habilidad. En tareas de varias personas, Trabajador_Fijo
identifica al responsable, no sustituye la dotación requerida.

## Regenerar y validar

Desde `Ganttier/backend`, en PowerShell:

```powershell
$env:PYTHONPATH = "."
python -B ../ejemplos/generar_ejemplos.py
```

También puede ejecutarse el script directamente desde otra carpeta: resuelve
su backend y su destino respecto de su propia ubicación. Requiere Python 3.10+
y `openpyxl`, incluidos en las dependencias del proyecto.

El script reemplaza únicamente los dos XLSX de `Ganttier/ejemplos/`. Desactiva la
escritura de bytecode, crea cada libro con el constructor oficial de plantillas
y sustituye sus filas de ejemplo conservando sus hojas y columnas. Después
ejecuta `parse_workbook` para cada modo y falla ante cualquier error o advertencia.
También verifica conteos, profundidad WBS, tipos de relación, correspondencia
de HH, habilidades, unidades y cobertura de instalaciones. Imprime el resumen
de cada archivo. No consulta ni modifica la base de datos.

Validación ejecutada: **ambos libros con 0 errores y 0 advertencias**. Esta
comprobación acredita el contrato de importación; no ejecuta la interfaz ni
la optimización del programa.


## Diario hábil y ventana explícita

Para reproducir una ronda cada día hábil de noviembre de 2026, use `Frecuencia=DH`, `Primera_Fecha=2026-11-01`, duración 1 y tolerancias 0, con calendario Chile lunes–viernes. Se generan **21 ocurrencias con fecha**. D sigue produciendo 30 objetivos, de los cuales 9 no caben con tolerancia cero. No sustituya los maestros D desactivados del caso O&M entregado sin revisar sus ocurrencias explícitas existentes; ensaye la regeneración en una copia.

Una actividad con `Mes_Macro=2026-11` y ventana explícita `2026-11-02` a `2026-11-02` conserva ese único día. Un informe de enero de 2027 con corte noviembre de 2026 conserva el programa y no inventa desempeño futuro.

El caso de roles del 05-10-2026 se puede configurar desde **Generar programa → Roles indivisibles** con 4 horas por bloque, horizonte 60 y objetivo A27, sobre su calendario abstracto corregido. Consulte las [restricciones del modo](decisiones/roles_indivisibles.md); las fechas representan bloques equivalentes.
