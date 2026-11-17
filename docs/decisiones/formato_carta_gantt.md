# Carta Gantt genérica — I2

Formato `carta_gantt`, clase `AdaptadorCartaGantt`; XLSX solamente. Fuentes
ficticias, sin documento de cliente para contrastar la semántica.

## Lectura y perfil

Una columna Actividad/Tarea/Descripción/Nombre y dos o más períodos ordenados.
Reconoce ene-27, Enero 2027, Mes 1…12, S1…52 (semanas ISO), fechas Excel y
fechas texto AAAA-MM-DD, DD/MM/AAAA, DD-MM-AAAA o DD.MM.AAAA como inicio de semana.
Los años de la fila superior, incluso combinada horizontalmente, se propagan
hacia la derecha; sin año se propone 2027 como SUPUESTO editable `anio`.
No se confunden columnas de frecuencia OM-WBS con una carta: tres frecuencias
nominales en el encabezado excluyen la detección. Puntaje .9 expresa estructura,
no probabilidad. Una plantilla sin marcas puede detectarse, pero no confirmarse.

`Perfil.hoja`, `fila_encabezado` y `mapeo` se pueden editar. El mapeo usa letras
Excel para `nombre`, `codigo`, `hh`, `duracion`, `dotacion`, `habilidad`, `sitio`,
`responsable`, `categoria`; `periodos` es una lista ordenada de letras.
Código numérico jerárquico o sangría Excel producen WBS; categoría crea un padre.
Responsable se conserva como metadata, sin inventar una persona disponible.
Los sitios son valores de una columna: cualquier número de sitios en filas.
Una matriz que pone sitios en columnas debe normalizarse a filas primero.

## Interpretaciones elegibles

`interpretacion=ventana_macro`: proyecto, una actividad por tramo contiguo.
Con `discontinuas=dividir` cada tramo disjunto genera sufijos -A, -B, etc., con
advertencia. `envolvente` conserva una actividad desde primera a última marca,
incluyendo huecos, decisión explícita del usuario. HH y duración corresponden a
**cada tramo**, no se prorratean. `window_hard=false` por defecto.

`interpretacion=periodica`: mantenimiento, cada marca produce exactamente una
ocurrencia. Se materializa una maestra anual acotada al período de la marca:
ancla al primer día, tolerancia hasta el último y horizon_end igual al último.
Esto usa el generador nativo y conserva matrices irregulares sin inventar ciclos
en huecos. La frecuencia A es una representación técnica de una ejecución
acotada, no una afirmación de recurrencia anual observada. No se extrapola fuera
de la matriz ni se compactan filas en series de frecuencia inferida.

Se proponen HH=8, duración=1 día laborable, dotación=1, semana 5×2, jornada=8h,
feriados CL, moneda CLP, habilidad vacía y sitio por confirmar. Columnas presentes
prevalecen. Todos los valores y reglas se listan en faltantes como SUPUESTO y
se conservan en descripción/metadata. No se inventa capacidad de trabajadores.

## Marcas y límites

`marcas=texto_color` acepta cualquier valor literal no vacío distinto de cero
y relleno sólido directo. Alternativas `texto` o `color`. Sin lista `colores`,
acepta rellenos no blancos/negros y colores de tema/indexados. Con una lista RGB
(por ejemplo `["4472C4"]`) acepta solo RGB cuya diferencia máxima por canal sea
≤ `tolerancia_color` (0 por defecto, máximo 255). Los colores de tema no se
convierten a RGB para este filtro. No interpreta formato condicional ni macros.

Lectura openpyxl read_only en dos flujos: valores y fórmulas. Fórmula sin caché
o error Excel impiden confirmar y señalan hoja/fila/columna. Números usan el
conversor nativo, incluida coma decimal y agrupación chilena. Se conserva SHA-256
y coordenadas de marcas. Filas sin marcas permanecen como WBS y se excluyen con
motivo. Encabezados repetidos se excluyen. No se crean predecesoras artificiales.

Límites: 30 MB comprimidos, 150 MB expandidos, 10.000 entradas ZIP, 100 hojas,
200.000 filas, 200 columnas, diez años y 100.000 actividades resultantes.
La lectura es secuencial; el borrador materializa tareas y WBS en memoria.

## Ejemplos reproducibles

Ejecutar `python -B ejemplos/generar_formato_carta_gantt.py` desde app o dar su
ruta absoluta. Incluyen Instrucciones, comentarios y lista de marcas.

| Archivo | Filas actividad | Marcas | Resultado elegido |
|---|---:|---:|---|
| plantilla_carta_gantt.xlsx | 3 | 3 | 3 actividades |
| carta_anual_60.xlsx | 60 | 60 rellenos | 60 actividades |
| carta_semanal_200.xlsx | 200 | 200 X | 200 actividades |
| carta_periodica_3_sitios.xlsx | 30 | 120 X | 120 maestras/ocurrencias; 40 por sitio |

En modo proyecto, la matriz periódica también produce 120 actividades, porque
sus cuatro marcas trimestrales por fila son discontinuas.

Integración pendiente: la copia de servicio.py examinada no incluye el registro
opcional anunciado. Véase agentes/I2/CONFLICTOS.md; no se editó servicio.py.

El ejemplo adicional carta_grande_20000.xlsx contiene 20.000 actividades y 20.000
marcas. Tiene 20.003 filas físicas. Su previsualización directa se midió en
87,351 s en la máquina compartida: NO cumple la meta de 3 s. El servicio legado
puede rechazarlo antes de llegar al adaptador por su límite de 20.000 filas;
requiere integración de límites por el propietario del servicio.

La detección también cede prioridad al bloque económico E2-MP (equipo,
actividad, precio unitario, cantidades y precio total), aunque el cuerpo
contenga códigos S1/S2 que parezcan semanas. Esta exclusión se verificó con
la fuente E2-MP conservada y una reproducción mínima.
