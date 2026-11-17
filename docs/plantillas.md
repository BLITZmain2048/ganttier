# Plantillas e informes de Ganttier

Las plantillas se encuentran en `plantillas/Plantilla_Proyecto.xlsx` y
`plantillas/Plantilla_Mantenimiento.xlsx`. Contienen ejemplos ficticios de una
subestación y líneas chilenas de 66, 110, 154 y 220 kV: termografía de
transformadores de poder, pruebas de relés e interruptores, análisis de aceite
y lavado de aislación. Reemplace o elimine esos ejemplos antes
de cargar datos reales. La hoja **Instrucciones** y los comentarios de cada
encabezado explican el formato sin necesidad de abrir este manual.

## Flujo de trabajo

1. Elija la plantilla del modo correspondiente.
2. Complete **Proyecto**: nombre, modo, fecha inicial, fecha final, calendario,
   moneda y fecha de corte. En mantenimiento, la fecha final del horizonte es
   obligatoria.
3. Defina **Calendarios** y **Recursos** antes de ingresar referencias a ellos.
4. En proyecto, complete WBS, Actividades, Relaciones y Asignaciones. En
   mantenimiento, complete Plan_Maestro.
5. Registre el avance físico y las HH/costos reales en **Avance**.
6. Guarde como `.xlsx`, importe y revise los errores por hoja, fila y columna.
   Un libro con errores no debe aplicarse a la base de datos.

Los textos azules identifican entradas editables. Los desplegables restringen
categorías; el formato condicional marca valores fuera de rango y duplicados
en las primeras 5.000 filas. El importador revisa duplicados en **todo** el libro.
Los paneles inmovilizados mantienen visibles códigos y encabezados. Las tablas
permiten filtrar. La hoja oculta **Listas** alimenta las validaciones; no es
necesario modificarla.

## Reglas de formato

- La primera fila contiene encabezados. No agregue títulos ni filas de unidades
  sobre ella; las unidades y explicaciones están en los comentarios.
- Puede reordenar columnas y usar mayúsculas o tildes en los encabezados. También
  se aceptan los nombres de campo de `domain.py`.
- Los códigos y nombres de referencia distinguen mayúsculas. Copie exactamente
  el nombre del calendario/recurso o el código WBS/actividad.
- Fechas: fecha de Excel, `DD-MM-AAAA`, `DD/MM/AAAA`, `DD.MM.AAAA`,
  `AAAA/MM/DD` o `AAAA-MM-DD`.
- Números: se admiten números de Excel, `16,5`, `1.234,56` y `1,234.56`
  como texto. Con ambos separadores, el último es el decimal y los grupos de
  miles deben tener tres cifras. Un único punto se interpreta como decimal.
- Booleanos: `SI`, `Sí`, `NO`, `1`, `0` o booleanos de Excel.
- Avance físico: **0 a 100**, por ejemplo `50` para 50 %. El lector también
  reconoce una celda porcentual de Excel que contiene `0,5` y muestra `50 %`.
- Ingrese valores: copie las fórmulas y use **Pegado especial → Valores** antes
  de importar. El lector rechaza fórmulas y errores de Excel con hoja, fila y
  columna, para evitar importar un cero por defecto o un resultado guardado
  obsoleto. Un texto literal que comienza con `=` sigue siendo texto.
- Las filas completamente vacías se omiten. Una fila parcialmente llena debe
  contener todos sus campos obligatorios. Las columnas desconocidas generan una
  advertencia; los campos conocidos no se descartan silenciosamente al exportar.

## Calendarios y recursos

`Semana_Laboral` contiene siete dígitos de lunes a domingo: `1111100` representa
lunes a viernes. Conserve los ceros iniciales usando texto si corresponde.
`Horas_Dia` y las capacidades son HH por día. `Pais_Feriados=CL` selecciona Chile.
`Turno=7x7` exige `Ancla_Turno`; `Feriados_En_Turno` indica si los feriados también
aplican al turno. **Excepciones_Calendario** permite indicar calendario, fecha,
laborable SI/NO, HH y motivo. Se entrega como `calendar.exceptions` al backend.

Tipos de recurso: trabajador, cuadrilla, equipo o material. Las habilidades se
separan por comas. `Capacidad_HH_Dia` es por unidad; `Unidades` indica el máximo de
unidades disponibles. Los costos y HH extra se ingresan por separado. Se pueden
usar nombres de roles en **Habilidad** cuando no se conoce aún al trabajador.

## Proyecto, WBS y programación

Los códigos WBS son únicos; `Codigo_Padre` debe existir y no puede crear ciclos.
Las actividades se vinculan mediante `Codigo_WBS`. `Duracion_Dias=0` representa un
hito. `HH` y `Costo` son los presupuestos totales de la actividad. `Dotacion` es
la cantidad de personas/unidades requeridas. `Prioridad` va de 0 a 100.

### Mes y ventana macro

`Mes_Macro=2026-10` es un atajo que el backend convierte en la ventana del mes.
Para una ventana explícita use `Ventana_Inicio` y `Ventana_Fin`.
`Ventana_Dura=SI` exige respetarla; `NO` permite penalizar desviaciones. El
importador conserva los datos originales; la interpretación temporal pertenece
al servicio de programación. Evite indicar un mes y una ventana contradictorios.

### Relaciones y restricciones

| Código | Relación |
|---|---|
| FS | Fin de predecesora a inicio de sucesora |
| SS | Inicio a inicio |
| FF | Fin a fin |
| SF | Inicio de predecesora a fin de sucesora |

`Desfase_Dias` usa días laborables del calendario de la sucesora. Un número
negativo permite un adelanto. Predecesora y sucesora deben existir y ser distintas.
No se permiten ciclos, incluidos A→B→C→A. El error señala una relación que
cierra el ciclo; las sucesoras externas al ciclo no se marcan como culpables.

`ASAP` programa lo antes posible y `ALAP` lo más tarde posible. `SNET`/`SNLT`
limitan el inicio a no antes/no después de una fecha; `FNET`/`FNLT` hacen lo mismo
con el fin. `MSO` fija el inicio y `MFO` el fin. Todas salvo ASAP/ALAP requieren
`Fecha_Restriccion`.

En **Asignaciones**, las columnas **Recurso** y **Habilidad** son independientes:
se requiere al menos una. Así se evita confundir un recurso con una habilidad
que tenga el mismo nombre. Si se indican ambas, se conservan ambas. El resto es
Unidades, HH presupuestadas y HH_Reales.

## Mantenimiento

| Frecuencia | Repetición |
|---|---|
| D / W / M | Diaria / semanal / mensual |
| Q / S / A | Trimestral / semestral / anual |
| ND / NW / NM / NA | Cada N días / semanas / meses / años |

`Intervalo` debe ser entero ≥ 1 y representa N para ND/NW/NM/NA.
`Primera_Fecha` es la fecha objetivo inicial. `Meses_Preferidos` usa números
separados por comas, por ejemplo `1,4,7,10`; `Dia_Semana_Preferido` usa 0=lunes a
6=domingo; `Dia_del_Mes` usa 1 a 31. La interpretación de meses cortos corresponde
al generador de mantenimiento.

Las tolerancias antes/después son días corridos alrededor de la fecha objetivo.
La ventana dura determina si se puede salir de ella. La instalación, equipo,
tensión, disciplina y necesidad de desconexión apoyan la coordinación eléctrica.
`Trabajador_Fijo` referencia un recurso; puede omitirse para asignar por habilidad.
`Fin_Horizonte` permite acotar una tarea individual y `Activo` habilitarla.

El avance referencia la **ocurrencia**, por ejemplo `PM001-0001`, no `PM001`.
El importador acepta códigos de actividades presentes en el libro o el patrón
`CODIGO_TAREA-índice` de una tarea maestra existente, con índice positivo. Como no
genera ocurrencias ni consulta la BD, la existencia concreta en el horizonte
debe verificarse en el servicio que aplica la importación.

## Avance y exportación

`Porcentaje` es acumulado a la fecha del registro. `HH_Reales` y `Costo_Real`
corresponden al registro, **no son acumulados**. `Trabajador` es opcional y debe
existir en Recursos si se indica. `Inicio_Real`/`Fin_Real` admiten fechas reales.

La exportación usa las mismas hojas. Agrega columnas con nombres de dominio para
conservar campos como fechas programadas, holguras, avance, estado y bloqueo.
Esas columnas también se pueden importar. Los identificadores de BD son datos
auxiliares; las referencias de intercambio se expresan por código/nombre.
Los errores de validación del objeto original no se exportan como datos.

Los libros exportados incorporan una hoja técnica `_Ganttier` con la versión
del formato. Permite conservar exactamente campos ausentes (`\A`), valores
nulos (`\N`), cadenas vacías, espacios y listas (`\J` seguido de JSON).
No elimine esa hoja. Para completar un campo, reemplace el marcador por el
valor que corresponda; para vaciarlo explícitamente use `\N`. En Habilidades,
también puede reemplazar la lista por habilidades separadas por comas.
Las plantillas de ingreso no requieren conocer estos marcadores.
Las excepciones de calendario se editan en **Excepciones_Calendario**;
la columna técnica `exceptions` sólo conserva su presencia o nulidad.
Una edición en la hoja visible es la fuente de verdad: no se guarda una copia
oculta de los datos. La igualdad exacta se aplica a bundles válidos con campos
del dominio, fechas `date` y sin incidencias pendientes.

Excel permite hasta 32.767 caracteres por celda. El exportador informa un error
si se excede ese límite, en lugar de cortar el texto silenciosamente. Tanto la
exportación como los informes conservan el archivo anterior si falla la escritura.

## Informes

Los formatos xlsx, pdf, docx y html comparten portada, resumen, curva S, EVM,
actividades terminadas/programadas/atrasadas, lookahead, hitos, carga, desempeño y
notas. Los valores provienen de `ReportData`; el generador no recalcula EVM ni
la planificación.

El semáforo usa **SPI(t)**, con **SPI** como respaldo si falta SPI(t):
**índice ≥ 0,95 verde**, **0,85 ≤ índice < 0,95 amarillo** y
**índice < 0,85 rojo**. Sin un índice finito muestra “Sin datos”. La palabra y el
color se presentan juntos. Es un criterio de presentación del producto, no una
regla normativa del sistema eléctrico.

El resumen explica en texto el atraso o adelanto **pronosticado**, medido en
días corridos entre el término base y el pronosticado entregados por EVM;
no lo confunde con un atraso real observado. Incluye SPI(t), hasta cinco
actividades más atrasadas y acciones sugeridas. Si falta alguna fecha lo indica,
sin inventar un atraso cero. La sobrecarga suma `máximo(carga − capacidad, 0)`
por recurso y período; la capacidad libre de otro trabajador no la compensa.
Los datos faltantes se señalan y no se convierten en capacidad cero.

PDF, Word y HTML muestran como máximo 500 filas por tabla y advierten el total
original. El Excel conserva todas las filas y usa escritura secuencial
`write_only`, sin crear una celda persistente en memoria para cada dato.
El mini-Gantt y las barras en los
formatos estáticos muestran las primeras 20 actividades/trabajadores con aviso;
las tablas contienen el detalle. En Excel la curva S, carga y desempeño usan
gráficos nativos. El gráfico de carga muestra hasta ocho recursos y agrupa el
resto en “Otros recursos”, conservando su carga y la capacidad total. Las tablas
mantienen el detalle y la capacidad individual. El gráfico de desempeño limita
su vista a veinte trabajadores, también en Excel; su tabla conserva todos.
Fechas visibles: `dd-mm-aaaa`; texto numérico: `1.234,56`. Excel conserva tipos
numéricos y usa los separadores regionales de la aplicación.

La curva S usa porcentajes del presupuesto y mantiene vacíos los datos ausentes.
“Ganada” representa el avance; “Real” el consumo real de HH/costo normalizado al
presupuesto. Una fecha de corte vertical separa observación y proyección. Los
indicadores monetarios solo se presentan en moneda cuando `basis=cost`; con
`basis=hours` se indican HH.

Para regenerar las plantillas desde `Ganttier/backend`:

```powershell
python -m ganttier.excel_io.templates
```

Para generar en una carpeta explícita:

```powershell
python -m ganttier.excel_io.templates --output-dir ganttier/excel_io/generated
```

La revisión adversarial dejó las plantillas regeneradas en esa carpeta autorizada.
No reemplazó `Ganttier/plantillas/`, fuera del alcance de escritura de esta revisión.

Pruebas sin red:

```powershell
python -m pytest tests/excel_io -q
python -m pytest tests/reports -q
```
