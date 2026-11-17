# Ganttier — Manual de usuario

## Importar un archivo de cliente con perfiles

Use **Importar / Exportar → Importar archivo de cliente…**, o **Ctrl+K →
Importar archivo de cliente con asistente**. No es necesario convertir antes el
archivo a la plantilla propia de Ganttier.

1. **Archivo:** seleccione el original. Se admiten Matriz OM-WBS, Formulario
   E-2-MP, Caso JSON de proyecto e Inventario de equipos (Excel o PDF).
   El original no se modifica. Puede descargar la plantilla Excel de inventario.
2. **Formato y mapeo:** revise hoja, fila de encabezado y columnas. Puede editar
   las celdas del mapeo y pulsar **Actualizar vista previa**. Doble clic en listas
   como meses o sitios abre su editor. **Guardar perfil como…** conserva el mapeo
   para otros archivos; **Actualizar perfil** crea una versión nueva del perfil
   de usuario. Los perfiles incorporados no se sobrescriben.
3. **Datos faltantes:** confirme fechas, HH, dotación y calendario. Puede definir
   HH/dotación/duración por código de actividad. Los equipos, rutas y lotes son
   opcionales; déjelos vacíos si no conoce cantidades y rendimientos. Todos los
   valores propuestos quedan identificados como **SUPUESTO**.
4. **Resumen:** revise maestras, ocurrencias, costos, advertencias y exclusiones.
   Acepte expresamente los supuestos y las advertencias para crear el proyecto.
   El programa inicial debe revisarse y nivelarse antes de usarlo en operación.

OM-WBS conserva las marcas de meses por sitio: una semestral con doce marcas
produce una advertencia y requiere aceptación; no se borran marcas de forma
automática. Diario significa días hábiles del calendario confirmado. En E2-MP se
programan las cantidades numéricas por año y se informa cualquier diferencia
entre detalle y resumen. Una partida condicional sin cantidades se conserva como
pendiente, sin inventar ejecuciones.

Los supuestos se ven en la descripción del proyecto, las notas de sus actividades
y los informes exportados. La importación de la plantilla propia sigue disponible
en la misma página. Consulte [reglas, API y opciones avanzadas](decisiones/importador_asistido.md)
para equipos, rutas, lotes y nuevos formatos.

### Caso JSON de proyecto

El perfil reconoce `time_model`, `activities`, `periodic_series`, `personnel` y
`resources`. Revise la **fecha base SUPUESTA**: de forma predeterminada se propone
2027-01-01. Cada bloque de la fuente se representa como **un día abstracto de
calendario continuo**, con las horas del bloque, sin feriados ni descansos. No se
convierte a horario civil intradía; `slots_per_workday` se conserva en la fuente,
pero no cambia esta equivalencia.

Las series se expanden con sus formatos de identificador y predecesoras FS. Los
conteos se comparan con `expected_expanded_counts`; una diferencia bloquea la
creación. Las habilidades, unidades, costos por bloque y equipos vienen del JSON.
Los intervalos de ausencia y el exterior de la ventana de disponibilidad se
convierten a excepciones del calendario del recurso.

Cuando hay personas nominales y requerimientos por rol, el resumen propone
**Roles indivisibles**. Después de importar, elija ese modo en **Generar programa**,
con las horas y el horizonte de bloques indicados. La importación no ejecuta el
solver. El caso de referencia, con la solicitud de aceptación, termina A27 en el
bloque 36 y cuesta 10.420 de personal.

### Inventario de equipos y PDF revisado

La plantilla tiene siete columnas: **Planta, Bloque, Objeto/Tipo, Cantidad,
Unidad, Detalle, Fuente**. Ingrese una fila por objeto y bloque, con cantidad entera
no negativa. `PLANTA` identifica una cantidad sin desglose; no permite aplicar
porcentaje por bloque. Los ejemplos están en Instrucciones y no se importan.

Al abrir un PDF se intenta reconocer sus tablas. En **Formato y mapeo** se
presentan las filas extraídas y los totales. En **Datos faltantes** use
**Revisar / corregir inventario…** para contrastar y corregir las filas base.
**Descargar inventario para revisión…** permite revisarlo en Excel y volver a
abrirlo. Una extracción no confiable informa el problema y ofrece la plantilla;
no se aplica OCR ni se importan imágenes como datos.

Para Chile Solar, el total del Cuadro 2 excluye módulos. El asistente verifica
ese alcance y excluye las filas de total del inventario. `strings_por_SCB` empieza
sin confirmar; el 4 escrito en el PDF se conserva en Detalle. Para reproducir la
referencia confirmada use **24**. Las **27 hileras por tracker** se proponen desde
el detalle del PDF y también se pueden editar. Los derivados se recalculan al
preparar el resumen. Meteo Station está ausente explícitamente en AND y SDN.

Para programar cantidades:

1. Defina **Tareas maestras**, **Rutas**, **Equipos de trabajo** y **Lotes y
   rendimientos** mediante sus editores de estructura. Los campos siguen el
   contrato de la plantilla de mantenimiento; hay un ejemplo completo en la
   [decisión del importador](decisiones/importador_asistido.md#inventario-de-equipos-fase-b).
2. Pulse **Vincular cantidad a tarea o ruta…**. Seleccione destino, planta,
   objeto, bloque y una regla: **100 %**, **porcentaje por bloque con redondeo
   hacia arriba** o **cantidad fija**. Cada vínculo reemplaza `Cantidad_Total`
   del destino; el tamaño de lote, rendimiento y equipos siguen siendo editables.
3. Prepare el resumen y revise cantidades, ocurrencias, exclusiones y advertencias.
   Para PDF marque además **He contrastado las filas y los totales…**. Sin esa
   confirmación específica no se crea el proyecto.

Un inventario sin maestras guarda cantidades y trazabilidad en un proyecto nuevo,
con cero actividades. No inventa trabajo, rendimientos ni dotación. La exportación
`/api/projects/{id}/inventario.xlsx` conserva el inventario; la exportación habitual
del proyecto contiene sus tareas y lotes. No se modifica un proyecto previo.

## Equipos de trabajo y reparto por cantidad

En un plan de mantenimiento, abra **Plan de mantenimiento → Equipos y reparto**. Cree cada equipo con nombre, integrantes, horas por día, régimen e inicio de ciclo. Puede usar 7x7, 14x14, 5x2 u otro NxM. Para equipos alternados 14x14, desplace catorce días el inicio del segundo. 5x2 siempre significa lunes a viernes: el inicio de ciclo no habilita sábados ni domingos. Los feriados de Chile son libres por defecto en 5x2 y laborables por defecto en los otros regímenes; el selector permite cambiarlo.

Edite la tarea maestra y complete **Cantidad total por ocurrencia**, **Unidad**, **Tamaño de lote**, **Rendimiento por equipo** y los **Equipos de trabajo**. Complete también Primera fecha. El rendimiento es del equipo completo, no por persona. Para conservar el comportamiento tradicional, deje vacíos todos los campos de reparto. La duración y las HH de cada lote se calculan a partir de cantidad, rendimiento y calendario del equipo.

Ejemplo: 28.000 paneles mensuales, lotes de 2.000 y rendimiento de 1.000 paneles/día producen catorce lotes de dos días; dos equipos reciben siete lotes y 14.000 paneles cada uno. Con tres integrantes y ocho horas por día, cada lote demanda `2 × 8 × 3 = 48 HH`. La última fracción de un día se redondea hacia arriba.

El campo **Lote continuo en turno** está desmarcado por defecto: el lote puede continuar en el siguiente bloque del mismo equipo, usando solo sus días de turno dentro de la ventana. Excel lo intercambia como `Lote_Continuo_En_Turno` (SI/NO) y la API como `batch_continuous_in_shift` (true/false). Inicio/fin pueden incluir descansos intermedios; duración, HH y carga cuentan solo días efectivos. Marque SI para exigir un bloque consecutivo. Pulse **Generar programa** y revise capacidad y auditoría: el ejemplo de 28.000 paneles con dos equipos 7x7 alternados se aplica con NO; con SI puede no caber. Un resultado infactible conserva el programa anterior.

Para frecuencias iguales o mayores que el ciclo se reparten lotes por igual antes de programar. Si sobra un lote, rota al equipo siguiente entre ocurrencias. Por ejemplo, 28.500 paneles crea quince lotes y alterna 8/7 y 7/8. Si los días hábiles de los equipos son distintos, aparece una alerta con el detalle; no se compensa la cuota automáticamente.

**Frecuencias cortas y equidad por ciclo:** con actividad semanal y dos equipos 14x14 alternados, la ocurrencia completa va al equipo que tiene turno en su ventana. Cuatro semanas alineadas se asignan A, A, B, B. La igualdad se revisa por ciclo de 28 días y por mes civil, no por semana. Ajuste **Tolerancia de equidad por ciclo/mes (lotes)** si su criterio permite una diferencia distinta de uno. Un mes puede quedar desequilibrado por sus bordes aunque el ciclo esté equilibrado; esa advertencia requiere revisión. Períodos incompletos quedan pendientes de comparación.

En **Equipos y reparto → Reparto y alertas**, filtre por mes y compare demanda, lotes programados, días y HH. Desplace horizontalmente la tabla para ver todas las columnas. Cero lotes programados con demanda positiva significa que esa demanda aún no está aplicada. Las advertencias también aparecen en **Análisis y alertas** y en los informes mensuales/anuales.

En **Importar / Exportar**, genere una plantilla de mantenimiento actualizada. Complete la hoja **Equipos** y use sus nombres, separados por coma, en **Equipos** del Plan_Maestro. No utilice comas dentro del nombre de un equipo. Las nuevas columnas y asignaciones se conservan al exportar e importar. Los libros antiguos sin reparto siguen siendo válidos.

Para exportar el resultado, elija **Gantt a Excel → Por nivel → Equipo**. Los lotes se agrupan por cuadrilla; las actividades tradicionales mantienen su equipo/TAG físico. El libro incorpora reparto, carga y alertas. Las barras muestran el intervalo del grupo y no implican trabajo durante todos sus días intermedios.

Si dos equipos atienden tres plantas FV, cree ambos equipos una sola vez en el mismo plan y selecciónelos en las maestras de las tres plantas. Use Instalación para distinguirlas. Así las tareas comparten capacidad. No duplique las cuadrillas por planta. Con `strings_por_SCB=24`, convierta la cantidad y el rendimiento a una misma unidad antes de cargarlos; Ganttier no convierte SCB, strings ni paneles automáticamente.

Consulte [diseño, reglas y límites](decisiones/equipos_reparto.md) para ventanas, calendarios genéricos, auditoría y criterios de equidad.

Ganttier es un programa de escritorio (ventana propia, sin navegador) de planificación y control tipo Primavera P6 con dos modos:

- **Proyecto**: obras con WBS, dependencias FS/SS/FF/SF con desfase, restricciones de fecha, ruta crítica (CPM), recursos y optimización.
- **Mantenimiento**: planes de mantenimiento periódico (subestaciones, líneas, transformadores, protecciones) con generación masiva de ocurrencias (hasta 100 000) y nivelación de la carga por habilidad y trabajador.

## Exportar Gantt a Excel

Abra el proyecto y use **Gantt → Más → Exportar Gantt a Excel…**, el botón de
**Importar / Exportar**, o busque esa acción en **Ctrl+K**.

1. Elija **Resumen por nivel** (WBS, instalación/sitio, equipo o habilidad) o
   **Por trabajador** (HH frente a capacidad personal).
2. En resumen, elija la agrupación y el nivel WBS: «Último nivel» conserva el nodo
   asignado; 1 reúne por raíces. Sitio y WBS / Equipo y WBS permiten combinar ambos.
3. Seleccione **día, semana o mes** y, si lo necesita, fechas **DD-MM-AAAA**.
   Vacío significa todo el programa. Los filtros de la vista Gantt no se aplican.
4. Active el resaltado crítico si corresponde y pulse **Exportar**. Al terminar,
   elija dónde guardar el XLSX. La generación se ejecuta en segundo plano.

La hoja **Leyenda** explica los colores y los datos. ◆ señala hitos, ◀/▶ una barra
que continúa fuera del rango y la línea naranja el período de la fecha de corte.
**Calendario** detalla feriados y descansos. En trabajadores, rojo significa carga
superior a capacidad, naranja desde 80 % y verde carga positiva menor. **Capacidad**
permite revisar las HH disponibles y **Totales por período** reúne las personas.
Las fórmulas se recalculan al abrir con Excel/LibreOffice.

Más de **500 grupos** se reúnen automáticamente en intervalos alfabéticos declarados
en Leyenda, sin perder conteos ni extremos. Más de 500 personas se reparte en hojas,
sin fusionarlas. Máximo **520 períodos**: si se excede, acote fechas o use semana/mes.
Las barras resumen el programa vigente; no implican ocupación continua. La demanda
sin trabajador nominal no se asigna ficticiamente. Editar el XLSX no modifica el
programa ni permite reimportar las barras. [Detalles y límites](decisiones/gantt_excel.md).

## 1. Instalación y arranque (Windows)

1. Requisito: Python 3.10 o superior (`py --version`).
2. Doble clic en `instalar.bat` (solo la primera vez): crea el entorno `.venv`, instala las dependencias y genera las plantillas Excel.
3. Doble clic en `iniciar.bat`: abre la ventana de Ganttier. Si no abre, `iniciar_consola.bat` muestra el error.
4. Los datos quedan en `datos/ganttier.db` (respáldalo copiando el archivo con el programa cerrado). En **Informes**, elige la carpeta local de salida; si no eliges otra, se utiliza la subcarpeta `informes` del directorio de datos.
5. Versión ejecutable: `construir_exe.bat` genera `dist\Ganttier\Ganttier.exe` (distribuye la carpeta completa). El ejecutable guarda los datos en `%LOCALAPPDATA%\Ganttier`.

La barra lateral agrupa las páginas (General, Planificación, Control) y se pliega a solo íconos con **Ctrl+B**. **Ctrl+1…9** y **Ctrl+0** saltan a cada página; **Ctrl+K** abre la paleta de comandos (buscar páginas, acciones y proyectos); **F1** muestra todos los atajos; **F5** actualiza la página. En el encabezado, el selector de proyecto cambia de proyecto sin pasar por la lista (los recientes aparecen primero). Arrastra un archivo .xlsx sobre la ventana para importarlo. Ganttier recuerda el tamaño de la ventana, la última página y el último proyecto.

En pantallas pequeñas o con escala alta, el encabezado ocupa varias filas y las páginas largas permiten desplazamiento. Los diálogos de WBS y edición masiva se ajustan al área disponible del monitor.

**Inicio** es la página de partida: sin proyecto ofrece crear uno desde una lista de tareas, vacío, desde Excel o explorar la demo; con proyecto muestra un resumen (actividades, término, avance, SPI, HH, recursos) y la lista guiada de pasos (recursos → tareas → generar programa → línea base → avance → informe) con el botón de cada paso.

**Asistente «Nuevo desde lista de tareas»** (Inicio o Proyectos): 1) datos del proyecto, 2) recursos, 3) tareas, 4) objetivo, 5) generar. En los pasos 2 y 3 puedes **pegar desde Excel** (copia el rango con encabezados: Codigo, Nombre, Duracion_Dias, HH, Habilidad_Requerida, Dotacion, Predecesoras, Mes_Macro…; se aceptan números con coma decimal y fechas dd-mm-aaaa). Las predecesoras se escriben como «A; B+2; C SS-1; D FF». El asistente valida en vivo (códigos duplicados, predecesoras inexistentes, ciclos, habilidades sin recurso y tareas cuya intensidad HH/día supera la capacidad de su habilidad) y al final muestra si el programa es factible y lo abre en el Gantt. «Pegar desde Excel» también está en Actividades, Recursos y Plan de mantenimiento.

## 2. Flujo de trabajo en modo Proyecto

### 2.1 Desde una lista de tareas (sin fechas) — «Generar programa»

No es necesario planificar a mano. Carga la lista de tareas y Ganttier encuentra un programa factible:

1. Crea el proyecto y los **recursos** (cuadrillas o trabajadores con sus habilidades, HH/día y unidades).
2. Ingresa las tareas en *Actividades y Gantt* (＋ Actividad) o con la plantilla Excel: código, nombre, duración, HH, **habilidad requerida**, dotación y, si aplica, **dependencias** (FS/SS/FF/SF con desfase), **mes macro** o ventana (blanda o dura) y restricciones de fecha. No se ingresan fechas de inicio.
3. Pulsa **Generar programa** (en *Actividades y Gantt* o en *Programar / optimizar*, donde además eliges el objetivo). El programa respeta calendarios y feriados, precedencias, capacidad por habilidad (sin horas extra salvo que las permitas) y ventanas duras.
4. Antes de aplicar, un **verificador independiente** revisa el resultado. Si es factible se aplica y verás término, pico de carga, HH extra y actividades fuera de ventana. Si no existe solución factible, no se modifica nada y se listan las causas (por ejemplo: «habilidad *Protecciones* sin recursos», «ventana dura imposible», «MFO incompatible con las precedencias»).

### 2.2 Edición detallada

1. **Proyectos → Nuevo proyecto** (modo *Proyecto*), o **Demo: construcción S/E** para explorar.
2. **Recursos**: crea trabajadores, cuadrillas y equipos con sus **habilidades** (p. ej., *Montaje*, *Protecciones*, *Obras civiles*), capacidad HH/día, costos normales y de hora extra y máximo de horas extra.
3. **Actividades y Gantt**: agrega actividades (＋ Actividad) o importa la plantilla. En la tabla, doble clic edita nombre, duración, inicio, % avance, HH y mes macro.
   - Arrastra una barra para moverla (se aplica la restricción *Comenzar no antes de*).
   - Arrastra el extremo derecho para cambiar la duración.
   - Arrastra desde el círculo a la derecha de una barra hasta otra barra para crear una relación FS.
   - **Enter** abre el detalle (restricciones, ventana, calendario, relaciones con tipo y desfase, asignaciones de recursos). **F9** recalcula el CPM.
   - Ctrl + rueda: zoom; Shift + rueda: desplazamiento horizontal. Selector *Escala*: Día/Semana/Mes/Trimestre/Año; *Hoy* e *Ir a selección* centran la vista; *Vista* muestra u oculta capas y columnas.
   - **Perfil de recursos**: panel inferior alineado con el Gantt que muestra la carga diaria o semanal por habilidad o recurso, la capacidad y el exceso (rojo).
   - Rojo = ruta crítica; gris delgado = línea base; banda translúcida = ventana macro; línea naranja = fecha de corte.
   - **Editar en lote** (Ctrl+Shift+E): aplica un cambio a todas las actividades que coinciden con la búsqueda y los filtros (o solo a la seleccionada). Marca únicamente los campos a cambiar (habilidad, calendario, prioridad, HH fijas o ±%, dotación, instalación, disciplina, desconexión); la vista previa muestra antes → después y la escritura es de todo o nada (máximo 5.000 por lote).
   - **Vistas guardadas**: el selector *Vistas…* recuerda búsqueda, filtros (*Solo críticas*, *Desconexión*), columnas visibles, orden, capas y escala. *Más → Guardar vista actual…* crea una (p. ej., «Reunión semanal»).
   - **Crear cadena de relaciones** (*Más*): pega los códigos en orden de ejecución (una línea en blanco separa cadenas) y Ganttier crea A→B→C… con vista previa; si un par ya tiene otra relación o se formaría un ciclo, no crea ninguna.
   - **Eliminar** una actividad que figura en una línea base o tiene avance registrado pide una confirmación explícita, porque borrarla cambiaría hacia atrás el BAC, el valor planificado y las HH reales; la eliminación queda en la bitácora. Lo mismo ocurre al eliminar una tarea periódica con ocurrencias ejecutadas.
4. **Programación macro**: matriz actividades × meses. Haz clic en el mes en que debe ejecutarse cada actividad (o arrastra para un rango). Con Shift/Ctrl seleccionas varias y las asignas de una vez. El pie muestra las HH por mes contra la capacidad. Luego **Programar dentro de los meses**: el optimizador ubica cada actividad día a día dentro de su mes, nivelando la carga.
5. **Optimizar**: elige el objetivo con los controles deslizantes o un preajuste:
   - *Nivelar carga laboral*: reparte uniformemente las HH por día o semana y por habilidad o recurso.
   - *Minimizar plazo*: adelanta el término respetando dependencias y capacidad.
   - *Respetar ventanas macro*: minimiza los días fuera del mes o ventana indicada (la ventana *dura* no se viola).
   - *Minimizar costo/HH extra*: reduce las horas sobre la capacidad normal, valorizadas al costo de la hora extra.
   - Cualquier mezcla ponderada. **Simular** muestra KPI e histograma antes/después sin modificar nada; **Aplicar** actualiza las fechas. El historial permite aplicar corridas anteriores.
   - Método *Automático*: CP-SAT (óptimo o casi óptimo) hasta ~3000 actividades; heurística para volúmenes mayores.

### 2.3 Búsqueda en todos los proyectos

*Ctrl+Shift+F* (o escribe en la paleta *Ctrl+K* y elige «Buscar … en todos los proyectos») busca por código, nombre, instalación, TAG o habilidad en todos los proyectos y planes de mantenimiento, sin distinguir mayúsculas ni tildes. El orden es: código exacto, TAG exacto, prefijo y otras coincidencias (máximo 200 resultados). Enter o doble clic abre el resultado en su proyecto: una actividad queda seleccionada en el Gantt (si un filtro la ocultaba, se quitan los filtros) y una tarea periódica en el plan de mantenimiento.

## 3. Flujo de trabajo en modo Mantenimiento

1. Crea el proyecto en modo *Mantenimiento* con el **fin del horizonte** (por ejemplo, 1 a 3 años), o usa **Demo: mantenimiento** eligiendo la cantidad de actividades.
2. **Plan de mantenimiento**: define las tareas periódicas (frecuencia D/W/M/Q/S/A o cada N días/semanas/meses/años, HH por ejecución, dotación, habilidad, instalación, equipo, tensión, si requiere desconexión, tolerancia antes/después, meses preferidos). También se cargan con la *Plantilla_Mantenimiento.xlsx*.
3. **Generar programa del plan**: crea las ocurrencias del horizonte y las programa en un solo paso. Si vuelves a generar después de agregar tareas, sólo se **agregan** las ocurrencias que faltan; las existentes y su avance no se tocan. Marca *Reemplazar ocurrencias* únicamente si quieres rehacer el plan (el avance registrado, las actividades bloqueadas y las incluidas en líneas base se conservan).
4. El programa (o **Optimizar** con el preajuste *Ventanas + nivelación*) nivela la carga semanal por habilidad, asigna un trabajador concreto a cada ocurrencia respetando su calendario o turno y agrupa por instalación los trabajos que requieren desconexión. Esto facilita coordinar las indisponibilidades con el Coordinador Eléctrico Nacional.
5. Revisa el **mapa de calor** (utilización semanal por habilidad; rojo = sobre capacidad) y el **calendario por instalación** (⚡ = trabajos con desconexión).
6. **⚡ Programa de desconexiones** (en el plan de mantenimiento o en Informes): descarga un Excel con los trabajos que requieren desconexión agrupados por instalación y fecha (equipos, tensión, HH y responsables). Úsalo como base para preparar las solicitudes ante el Coordinador Eléctrico Nacional; el formato oficial y los plazos de solicitud se deben verificar en la normativa y los procedimientos vigentes.

## 4. Control (ambos modos)

1. Guarda una **línea base** (Gantt → Línea base, o Control → Líneas base) y actívala.
2. Define la **fecha de corte** en *Avance y curva S*.
3. Registra el avance en la grilla: % físico acumulado, HH reales del período, trabajador que ejecutó y fechas reales. La tabla se muestra por páginas; cambiar de página o filtrar conserva los borradores. **Guardar avance** envía todos los cambios pendientes y crea registros históricos a la fecha de corte. Si el guardado se confirma pero falla la recarga, el aviso distingue ambas situaciones para evitar enviar el mismo avance dos veces.
4. La **curva S** muestra la curva planificada (línea base), la ganada (avance físico), la real (HH o costo incurrido) y la proyección desde la fecha de corte. Puedes verla por día, semana o mes y con base en HH, costo o cantidad de actividades.
5. Las tarjetas **EVM** muestran PV, EV, AC, SPI, CPI, SPI(t) (*Earned Schedule*), EAC, VAC, TCPI y el término pronosticado, con semáforo (verde ≥ 0,95; amarillo 0,85–0,95; rojo < 0,85).
6. En modo Proyecto, después de registrar avance ejecuta **Programar (CPM)**: el remanente se reprograma desde el día hábil siguiente a la fecha de corte.
7. **Fecha de corte**: se entiende al final de la jornada. El avance registrado «al corte» incluye ese día (así lo cuentan también el valor planificado y la curva S), y el trabajo pendiente se programa a partir del día hábil siguiente. Un corte igual al inicio del proyecto significa que la obra aún no comienza.

8. **Análisis y alertas** (página propia, cada pestaña se calcula al abrirla):
   - *Alertas*: hitos o actividades críticas que se atrasan respecto de la línea base, actividades vencidas, avance desactualizado, corte antiguo, SPI bajo, backlog preventivo antiguo y hallazgos críticos de calidad. Se pueden *reconocer*; si la condición desaparece pasan a *resueltas* y, si reaparece, vuelven a *nuevas*. Cuando hay muchas actividades vencidas se muestra una sola alerta resumen. Un indicador que no puede calcularse genera una alerta de datos, nunca un «verde».
   - *Pronósticos*: EAC con CPI, a presupuesto, combinado CPI·SPI y con reestimación del remanente (ETC), TCPI para cada uno y duración pronosticada PD/SPI(t) y AT+(PD−ES). No modifican fechas ni presupuestos.
   - *EVM por grupo*: PV, EV, AC, SPI y CPI por WBS, instalación o habilidad, recalculados con los valores del grupo (no promediando índices) y conciliados con el total.
   - *Calidad del programa*: reglas de buena práctica (actividades sin predecesora o sucesora, restricciones duras, adelantos, desfases y duraciones largas, holgura alta, fechas reales futuras, vencidas, HH sin recurso). Doble clic abre el Gantt filtrado. No es una certificación DCMA.
   - *Cumplimiento preventivo* (modo Mantenimiento): cumplimiento a tiempo y total contra el plan comprometido, backlog por antigüedad, prioridad e instalación y las 20 órdenes más antiguas.
9. Los **informes de un período ya cerrado** se calculan al fin de ese período con el historial de avance: regenerar el informe de agosto en septiembre entrega las mismas cifras. El informe indica el corte usado.
10. El **PV** se reparte con el calendario efectivo de cada actividad (turnos 7x7, L-V, corrido); el desempeño por trabajador usa la misma distribución.

## 5. Desempeño por trabajador

Elige semana, mes, año o un período personalizado. Por trabajador se muestra:

- **Cumplimiento**: actividades terminadas a tiempo / actividades que vencían en el período.
- **Eficiencia HH**: HH ganadas (presupuesto × avance) / HH reales.
- **Atraso promedio**: en días respecto del término planificado.
- **SPI** y **CPI** de las actividades asignadas, además de HH planificadas, ganadas y reales.

Para que el cálculo sea representativo, registra en el avance *quién ejecutó* o asigna recursos a las actividades.

## 6. Informes automáticos

**Generar informe** descarga los formatos seleccionados a la carpeta local elegida y muestra sus rutas completas. La elección se recuerda; **Abrir carpeta** lleva a ese destino. Si la carpeta elegida fue eliminada o no permite escribir, se informa el error. **Emitir pendientes ahora** también guarda las nuevas emisiones allí. Las emisiones automáticas de fondo se conservan en el historial interno del programa.

En **Informes** elige *Semanal*, *Mensual* o *Anual* y una fecha dentro del período, y marca los formatos que quieras (PDF, Excel, Word, HTML). Cada informe incluye resumen ejecutivo con KPI y semáforo, curva S, tabla EVM, actividades terminadas, programadas, atrasadas y próximas (lookahead), hitos, histograma de carga y desempeño por trabajador. El HTML se puede ver dentro de la aplicación.

**Plan semanal por trabajador**: en *Informes*, elige una fecha de la semana y descarga un Excel con lo que hace cada trabajador cada día (según las asignaciones explícitas), más los bloques *Cuadrillas* y *Sin asignación nominal* y una hoja *Detalle* con una fila por asignación y día.

## 7. Plantillas Excel

En **Importar / Exportar** descarga *Plantilla_Proyecto.xlsx* o *Plantilla_Mantenimiento.xlsx*. Traen hojas de instrucciones, listas desplegables, validaciones y ejemplos. Sube el archivo y usa **Validar** para ver los errores por hoja, fila y campo antes de importar (*Combinar* actualiza por código; *Reemplazar* borra y vuelve a cargar). También puedes crear un proyecto nuevo directamente desde un archivo. **Exportar** genera el mismo formato para editar en Excel y volver a importar. El detalle de cada columna está en `docs/plantillas.md`.

## 8. Calendarios

*Calendarios* trae precargados: Chile L-V 8 h (con los feriados legales de Chile, incluidos los móviles), Chile L-S, Turno 7x7 12 h, Turno 4x3 10 h y Corrido 7 días. Puedes crear otros calendarios (semana laboral, horas por día o turno NxM con fecha de inicio del ciclo) y agregar excepciones haciendo clic en los días de la vista anual. Cada recurso y cada actividad pueden tener su propio calendario.

**Calculadora de días laborables** (*Calendarios → Calculadora de días…*): calcula el término sumando o restando días laborables a una fecha, o cuenta los días laborables entre dos fechas, mostrando los feriados, descansos de turno y excepciones del intervalo. Los días son inclusivos (una tarea de 1 día empieza y termina el mismo día).

**Ausencias personales** (*Recursos → Ausencia…*): registra licencias, capacitaciones o media jornada de un trabajador entre dos fechas, indicando las horas disponibles por día (0 = ausencia completa). La vista previa muestra, día a día, las horas antes y después y las asignaciones del trabajador en ese intervalo. Una ausencia solo reduce horas: los fines de semana, feriados y descansos del turno no cambian, y ningún día supera su jornada base. Si el calendario del trabajador es compartido, se crea una copia personal (no hereda cambios futuros del calendario original). El programa no se reprograma solo: después, vuelve a generar o nivelar si corresponde. Solo aplica a trabajadores individuales de un proyecto (no a cuadrillas ni a recursos globales).

## 9. Respaldos y seguridad

- Ganttier crea un **respaldo verificado cada día** mientras está abierto (con la base en uso, sin cerrarla), y además antes de actualizar el esquema de la base y antes de importar en modo *Reemplazar*. Se conservan 7 diarios, 1 por semana durante 4 semanas, 10 previos a operaciones y 20 manuales, en la carpeta `respaldos` junto a la base.
- *Proyectos → Respaldos…* (o Ctrl+K → «Respaldos») permite crear un respaldo, guardar una copia en otra ubicación (p. ej., un disco externo) y **programar una restauración**: se aplica al reiniciar Ganttier y antes se guarda la base actual.
- Solo puede haber **una ventana de Ganttier por base de datos**; una segunda instancia avisa en lugar de abrir otro escritor.
- La API local exige una credencial de sesión y solo acepta conexiones a 127.0.0.1/localhost, lo que impide que una página web abierta en el navegador lea o modifique los datos.
- El registro `ganttier.log` rota a los 10 MB (máximo 5 archivos).


## Mejoras de programación y mantenimiento (05-10-2026)

En **Generar programa**, seleccione **Roles indivisibles por actividad** cuando cada rol requiera una persona distinta que permanezca durante toda la tarea. Complete horas por bloque, horizonte en bloques (0 usa el fin del proyecto) y, opcionalmente, el código de la actividad objetivo. La misma selección está en el paso **Objetivo** de **Nuevo desde lista de tareas**. Se minimiza primero el término y después el costo de personal; los pesos habituales se ocultan. Se exige capacidad dura y no se permiten horas extra.

Este modo requiere OR-Tools, proyectos sin avance, relaciones FS, dedicación completa y calendarios continuos con las horas del bloque. Cada fecha representa un bloque equivalente, no un horario civil: revise el calendario antes de generar. No se convierten feriados ni turnos automáticamente. El modo habitual **HH fraccionarias** sigue siendo el predeterminado; cambiar el modo no garantiza una solución.

Tras aplicar, la tabla **Asignación nominal aplicada** muestra actividad, rol, persona e ID de demanda. Si edita el escenario, se oculta el personal obsoleto y se pide volver a generar. Los perfiles de carga e informes generales siguen leyendo las demandas. Para consultar el personal elegido use esta tabla o la exportación nominal existente.

En el plan maestro, **DH — diaria hábil** crea un objetivo por día con horas positivas del calendario de la tarea, respetando feriados, turnos y excepciones. **D** conserva días corridos. Excel admite `DH` en `Frecuencia`, y el asistente también la ofrece. Los códigos pueden saltar números para conservar su identidad al regenerar subhorizontes. Una duración que no cabe en su ventana sigue pendiente; amplíe tolerancia o corrija duración y calendario. Las HH son totales por ejecución, repartidas entre personas y días; no se multiplican por dotación. La estimación anual de HH de DH depende del calendario: genere las ocurrencias para conocerla.

**Mes_Macro** se interseca con **Ventana_Inicio/Fin**. Por ejemplo, noviembre más una ventana del 2 de noviembre conserva solo ese día. Si no hay intersección, la operación se rechaza. Las ventanas antiguas ya ensanchadas no se recuperan automáticamente; vuelva a cargar sus datos originales. La edición explícita de ventanas sin Mes_Macro retira el atajo del mes, como antes.

Puede generar informes semanales, mensuales y anuales posteriores al corte. Conservan la programación y muestran desempeño vacío para ese período. EVM y curva S reflejan el estado conocido al corte; las notas distinguen estos alcances. Los hitos conservan el alcance global del proyecto, incluidos los próximos compromisos.

Si falla la capacidad, revise el código de actividad y el ID de Asignaciones indicado. El diagnóstico muestra habilidad, HH y unidades; compruebe duración, recursos y disponibilidad. Una sugerencia de roles indivisibles es condicional y no sustituye corregir una falta real de personal. Los errores al guardar lotes indican tabla, fila del lote y código; la fila física del libro sigue apareciendo en la validación Excel.

Las plantillas de mantenimiento generadas incluyen un bloque **Supuestos** en Instrucciones. Revise HH, duración, dotación, tarifas, traslados, vacaciones y medios de prueba. Los datos de ejemplo no acreditan rendimientos reales y la marca de desconexión no equivale a un permiso.
