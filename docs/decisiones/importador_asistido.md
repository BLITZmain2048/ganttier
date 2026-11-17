# Importador asistido con perfiles — contrato v1, fases A y B

Ganttier acepta directamente los Excel del cliente **Matriz OM-WBS** y
**Formulario E-2-MP**, el **Caso JSON de proyecto** y el **Inventario de equipos**
(Excel o PDF con revisión). La entrada se revisa antes de crear un proyecto nuevo:
archivo → detección y mapeo → datos faltantes → resumen y confirmación.
La plantilla propia mantiene su flujo anterior.

## Uso y decisiones

Abra **Importar / Exportar → Importar archivo de cliente…**, o **Ctrl+K →
Importar archivo de cliente con asistente**. El asistente usa la API y ejecuta
las operaciones en un trabajador Qt; la interfaz no lee directamente la base.

1. Seleccione el archivo original. Se conserva intacto; la API guarda una copia
   de trabajo identificada por UUID y SHA-256.
2. Revise el perfil, la hoja, la fila de encabezado y el mapeo editable. La vista
   muestra filas físicas y columnas Excel. **Actualizar vista previa** aplica los
   cambios. Doble clic en listas/objetos abre un editor de estructura.
3. Revise el horizonte, las HH, la dotación y el calendario. Las excepciones por
   actividad usan el código de origen. Recursos nominales, equipos, rutas y lotes
   están desactivados por defecto. Ningún valor propuesto se considera observado.
4. Revise conteos, costos, exclusiones y advertencias. Marque las dos aceptaciones
   y cree el proyecto. Cambiar un dato obliga a volver a previsualizar.

El resultado es un **programa inicial sin nivelación**. Las fechas generadas no
certifican capacidad, habilitaciones, maniobras, desconexiones ni ejecución real.
Use las funciones existentes de Ganttier para asignar y nivelar después de
validar los supuestos.

## Separación de responsabilidades

| Archivo | Responsabilidad |
|---|---|
| `backend/ganttier/importadores/modelos.py` | `Perfil`, `Lectura`, `Borrador`, protocolos de extensión |
| `perfiles.py` | Perfiles incorporados y JSON de usuario con versiones |
| `excel.py` | Apertura acotada, normalización y referencias de origen |
| `om_wbs.py`, `e2_mp.py` | Detección estructural y lectura de cada formato |
| `caso_json.py` | Expansión y validación de bloques, roles, equipos y disponibilidad |
| `inventario.py`, `pdf_texto.py` | Inventario Excel/PDF, revisión, derivados y vínculos a lotes/rutas |
| `construccion.py` | Supuestos explícitos y generación de plantilla interna en memoria |
| `servicio.py` | Registro, previsualización y creación transaccional |
| `backend/ganttier/api/importadores.py` | Cargas, revisión vinculante, perfiles y metadatos |
| `backend/ganttier/gui/importador_asistido.py` | Cuatro pasos del asistente |

La conversión pasa por `excel_io.importer.parse_workbook`,
`services.create_project_from_bundle`, `repo.save_import_bundle`,
`maintenance.generator.generate_occurrences` y `repo.save_occurrences`.
No hay otro importador de la plantilla interna ni otro generador de recurrencias.
La previsualización normal llama al generador puro; las opciones de equipos y
rutas resuelven sus referencias en una base SQLite **en memoria**, sin solver ni
archivos de corridas. La confirmación reutiliza el mismo generador.

No se cambia el esquema general ni el contrato de `domain.ImportBundle`.
Dos tablas auxiliares se crean de forma idempotente en la primera confirmación:
`importaciones_asistidas(project_id, esquema, documento)` conserva el borrador;
`importaciones_confirmadas(carga, revision, project_id, resultado)` hace idempotente
la confirmación HTTP. Ambas referencian el proyecto con eliminación en cascada.
La descripción del proyecto y las notas de las ocurrencias conservan los
**SUPUESTOS** y la trazabilidad también en el Excel exportado. Los informes
nativos agregan la descripción de importación a sus notas.

## Perfil y detección

El perfil tiene `id`, `nombre`, `formato`, `version`, `esquema=1`, `hoja`,
`fila_encabezado`, `mapeo` y `supuestos`. Las coordenadas físicas son letras Excel
y filas desde 1. Se normalizan mayúsculas, espacios y acentos para detectar
encabezados, sin reescribir descripciones ni códigos del cliente.

OM-WBS puntúa la combinación de códigos jerárquicos, frecuencias y meses. E2-MP
busca Equipo/Actividad/Precio unitario/cantidades anuales/total en una misma fila.
El nombre de la hoja ayuda a la presentación, pero no es requisito de detección.
Un Excel/JSON que no coincide con ningún perfil produce una lista vacía. El PDF
se ofrece como candidato de inventario con confianza limitada y se valida al
extraer sus filas; nunca se confirma de forma automática. El puntaje expresa
reglas cumplidas, no una probabilidad estadística.

Mapeo OM: `codigo`, `descripcion`, `descripciones_wbs`, `frecuencias`, `meses`
(`columna`, `mes`, `anio`), `sitios` (`codigo`, `nombre`, `celda`),
`filas_ignorar`, `palabras_ignorar`, `columnas_auxiliares`.
Mapeo E2-MP: `equipo`, `descripcion`, `precio`, `cantidades` (columnas en orden de
año contractual), `total`, `subtotales`, `codigo` opcional, `detener_en_total`,
`filas_ignorar`, `palabras_ignorar`, `columnas_auxiliares`.
Solo se importan las columnas de datos mapeadas; una columna auxiliar nunca
se interpreta como una segunda tabla.

Los perfiles incorporados son de solo lectura. **Guardar perfil como…** crea una
copia; **Actualizar perfil** crea su siguiente versión y exige que el cliente
haya leído la versión vigente. Se guardan en
`GANTTIER_DATA_DIR/perfiles_importacion/<id>.json`, con revisiones anteriores en
`historial/<id>/<version>.json`. El borrado retira el perfil activo y conserva el
historial. La escritura usa un archivo temporal y reemplazo atómico. Los perfiles
solo contienen datos; no ejecutan código. Un perfil guardado conserva coordenadas
físicas: si cambia el diseño de la fuente, vuelva a detectar o ajuste el mapeo y
guarde otra versión. El detector recalcula posiciones en cada archivo nuevo.

## Reglas de las fuentes y aceptación

### OM-WBS

- Una maestra por pareja ítem/sitio con al menos una marca. Las plantas se leen
  de `[SIGLA]` en los encabezados; las marcas pueden agregar un sitio ausente del
  encabezado, pero se advierte. No se presupone que siempre haya tres plantas.
- Los códigos originales forman una WBS con `parent_code`. Cada maestra conserva
  la celda de código, descripción, frecuencia y todos los meses marcados.
- **Diario** se adapta a `DH`, frecuencia diaria hábil nativa. Las demás frecuencias
  usan `M` y meses preferidos para reproducir las marcas. La frecuencia nominal
  original se conserva en el borrador y en notas.
- Una frecuencia semestral con doce marcas genera advertencia; solo se importa
  tras aceptar la política `respetar_marcas`. No se elimina ninguna marca para
  forzar una cadencia nominal.
- El año fuente se propone, pero el usuario confirma el horizonte. El mes inicial
  debe coincidir con el primero de la matriz; admite otros meses iniciales
  presentes en la estructura. La matriz comprende hasta doce meses distintos.

Con inicio 01-11-2026, fin 31-10-2027, calendario CL lunes–viernes de ocho horas:
**175 ítems → 523 maestras → 28.717 ocurrencias**. Con las excepciones HH de la
conversión de referencia: **7.153,50 HH**. El ejemplo anterior usaba diarias
inactivas y ocurrencias explícitas; aquí `DH` permanece activo y regenerable.
Las maestras no diarias se anclan al día 15 con tolerancias -14/+13 días. Estas
ventanas caben en cada mes, pero son más estrechas que algunas ventanas del
ejemplo manual. Las fechas iniciales y las asignaciones no reproducen su solver.

### Formulario E-2-MP

- Lee el bloque Equipo/Actividad/Precio/Cantidad por año/Total y se detiene en el
  total del servicio. No importa firmas, nombres de empresas ni la copia lateral.
- Una maestra por fila/año con cantidad positiva. Cantidades admitidas: 0, 1, 2,
  4, 6, 12. Códigos ausentes se sustituyen por `MP<fila>` como identificadores de
  trazabilidad, sin deduplicar actividades iguales en distintas filas.
- Las dos partidas condicionales no numéricas se conservan en `excluidos` y en
  `faltantes`, sin programarlas. Se debe completar su criterio en la fuente para
  programarlas en esta versión.
- Se compara precio × suma de cantidades con el total almacenado de cada fila,
  con los subtotales y el total del detalle. Los subtotales `SUM` se verifican por
  el rango real de la fórmula, que puede abarcar varias familias de equipos.
  Se comparan además los resúmenes E1/E2 identificados por su descripción.
- Nunca se ejecutan fórmulas. Se leen sus valores calculados y su texto de
  referencia. Si faltan cachés, se calcula el precio × cantidades y se advierte
  que los totales/subtotales sin caché no quedan certificados; no se considera
  que un valor ausente sea cero. No se recalcula ni se guarda el libro original.
- Las cantidades «año 1/2/3» requieren inicio confirmado. Actualmente se exige
  el número correspondiente de años civiles completos desde el 1 de enero.
- Precio por ejecución conserva UF. HH provisionales:
  `round(max(0.5, precio_UF / uf_por_hh), 6)`, con 0,75 UF/HH editable. Es una
  conversión de presupuesto a esfuerzo, no salario observado. La política
  `referencia_eco` reproduce la regla de dotación 1/2/3 de la conversión previa;
  `precio_uf` usa la dotación explícita general. Ambas admiten excepciones.

Con 2026–2028: **330 maestras, 1.596 ocurrencias** (498 / 500 / 598),
**12.824,875 UF** y **17.099,833113 HH** con el coeficiente de referencia.
E1/E2 declaran 13.544,875 UF: faltan **720 UF** en las partidas detalladas,
correspondientes a SGIIE. Se informa la diferencia; no se inventan actividades.

## Datos faltantes y opciones avanzadas

Todos los valores de `supuestos` son decisiones de planificación confirmadas,
incluso si coinciden con una sugerencia del archivo. El campo `faltantes` informa
`clave`, `etiqueta`, `motivo`, `valor`, `estado` (`SUPUESTO` o `PENDIENTE`) y origen
cuando corresponde. La descripción persistida identifica las decisiones y el
SHA-256. Las filas de origen, advertencias y exclusiones completas se consultan
en el endpoint de metadatos.

Opciones `equipos`, `recursos`, `rutas` y `lotes` usan nombres de campo del
contrato de plantilla interna; el editor avanzado del asistente permite introducir
estas estructuras. Ejemplo opt-in válido:

```json
{
  "equipos": [{"name": "Equipo A", "regime": "5x2", "cycle_start": "2026-01-01", "members": 2, "hours_day": 8}],
  "lotes": {
    "1.1": {"quantity_total": 10, "unit": "unidades", "batch_size": 5,
            "productivity_day_team": 5, "team_names": ["Equipo A"]}
  }
}
```

La clave de `lotes` es el código de origen (aplica a sus maestras) o el código
exacto de maestra (tiene precedencia). Campos adicionales admitidos:
`productivity_hour_team`, `productive_hours_day`, `balance_tolerance_batches`,
`batch_continuous_in_shift`, `batch_full_day`, `route_code`. Los códigos o campos
desconocidos se rechazan, no se omiten. Para una ruta, agregue su definición en
`rutas` y `route_code` en las maestras componentes; se reutiliza el checklist
nativo. No se agrupan ítems ni se convierten precios en cantidades físicas de
forma automática. La demanda generada puede requerir nivelación posterior.

## API

Todas las rutas usan el prefijo `/api` y la protección local/token existente.

| Método y ruta | Entrada / resultado |
|---|---|
| `GET /importadores/formatos` | Formatos instalados y extensiones registradas |
| `POST /importadores/archivos` | Multipart `file`; devuelve `id`, SHA-256 y detecciones |
| `POST /importadores/borradores/{id}` | `perfil` completo o `perfil_id`, `supuestos`; devuelve borrador y `revision` |
| `POST /importadores/confirmar/{id}` | `revision`, `nombre` opcional, `aceptar_supuestos`, `aceptar_advertencias`; crea proyecto y devuelve resumen |
| `DELETE /importadores/archivos/{id}` | Elimina copia de trabajo y previsualización; conserva el proyecto creado |
| `GET /importadores/perfiles` | Perfiles incorporados y de usuario |
| `POST /importadores/perfiles` | Guarda perfil; incremento de versión con control de concurrencia |
| `DELETE /importadores/perfiles/{id}` | Borra perfil de usuario activo |
| `GET /projects/{pid}/importacion-asistida` | Documento completo de trazabilidad y supuestos confirmados |

`revision` es el hash del borrador revisado. Confirmar una revisión obsoleta, un
archivo alterado o un resultado que cambia produce 409 y revierte la creación.
Una confirmación repetida de la misma carga/revisión devuelve el mismo proyecto.
Errores de mapeo, datos o aceptación: 422. Carga/perfil/proyecto inexistente: 404.
Desconocido en detección: 201 con lista de detecciones vacía; la previsualización
sin perfil compatible devuelve 422.

Límites de entrada: 30 MB comprimidos, 150 MB descomprimidos, 100 hojas,
20.000 filas y 512 columnas por hoja; horizonte máximo de diez años,
100.000 ocurrencias. Las estimaciones de demanda excesivas se rechazan antes de
materializar. Las cargas conservadas se pueden retirar por DELETE; cancelar el
asistente retira su carga. No hay limpieza programada por antigüedad en fase A.

## Interfaz para fase B

Importe desde `ganttier.importadores`; `registrar(instancia)` incorpora código
confiable instalado al iniciar el programa. No registre desde un perfil de usuario.
Hay dos caminos:

1. **Lector tabular de mantenimiento:** implemente `formato`, `extensiones`,
   `detectar(path)`, `leer(path, perfil) -> Lectura` y
   `construir(lectura, supuestos) -> list[dict]` de maestras. Aprovecha resolución
   de supuestos, plantilla interna, generador y persistencia comunes.
2. **Adaptador completo**, apropiado para JSON de proyecto o inventario:
   implemente `detectar`, `previsualizar(path, perfil, supuestos) -> Borrador` y
   `convertir(borrador, nombre) -> domain.ImportBundle`. Es responsable de
   normalizar y validar su modelo, declarar todos sus supuestos y conservar
   trazabilidad. `convertir` no escribe en la base ni en archivos. El servicio
   común realiza la creación transaccional. Puede devolver `mode='proyecto'`,
   actividades, relaciones, recursos, WBS y calendarios existentes en el contrato.

Ejemplo de firma, deliberadamente sin el lector JSON real de fase B:

```python
class ProyectoJSON:
    formato = "proyecto_json"
    extensiones = {".json"}
    def detectar(self, path): ...  # lista de formato/puntaje/motivos/perfil
    def previsualizar(self, path, perfil, supuestos): ...  # Borrador v1
    def convertir(self, borrador, nombre): ...  # ImportBundle(mode="proyecto", ...)

registrar(ProyectoJSON())
```

La API filtra por extensiones registradas y la GUI consulta esa lista; no requieren
modificaciones para aceptar un nuevo sufijo. El adaptador debe ser determinista:
la confirmación vuelve a leer y comparar el borrador completo con la revisión
(incluido el modelo y la trazabilidad, no solo el resumen). El esquema
v1 de `Borrador` incluye `modo`, `perfil`, `fuente`, `maestras`, `sitios`, `wbs`,
`advertencias`, `faltantes`, `supuestos`, `resumen`, `excluidos`, `vista_previa` y
`modelo` (objeto JSON reservado al modelo intermedio del adaptador).
Un adaptador de proyecto conserva allí sus actividades, relaciones y referencias;
`resumen` permanece dedicado a los conteos y resultados de validación.
`test_extension.py` demuestra el flujo HTTP completo
con un adaptador JSON mínimo registrado solo durante la prueba.

## Validación y límites pendientes

Las pruebas nuevas están en `backend/tests/importadores`: libros reales,
variantes, perfiles, validaciones, persistencia, equipos/lotes/rutas, API y Qt
offscreen. No dependen del JSON `om_items.json`: OM se lee directamente del Excel.
Las variantes se generan en temporales; deben desplazar también las combinaciones
de celdas. `openpyxl.insert_cols` por sí solo no corrige rangos combinados ni
fórmulas; eso puede destruir una copia de prueba al guardarla.

Los perfiles de fase B se describen a continuación. Ningún perfil infiere
rendimientos, potencias, distancias, habilitaciones ni procedimientos eléctricos.
No se resuelven años contractuales parciales de E2-MP, matrices OM de
más de doce meses distintos, ni fórmulas sin caché como si fueran resultados
verificados. Las advertencias y diferencias se conservan para revisión.

## Caso JSON de proyecto (fase B)

Identificador `caso-json-proyecto`, formato `caso_json`, extensión `.json`.
La detección requiere `time_model`, `activities`, `personnel` y `resources`.
El adaptador completo entrega un `ImportBundle(mode='proyecto')`; no genera
plantillas intermedias ni modifica los motores. La conversión reutiliza las reglas
de `trabajos/ganttier_ejemplos_20261005/proyecto/caso_ganttier.py` y corrige de forma
explícita el calendario abstracto que exigía `APLICACION_PARCHE.md`.

- `horizon_slots`: entero de 1 a 3660; `slot_hours`: horas positivas, hasta 24.
- `duration_slots`, ventanas `[earliest_start_slot, latest_finish_slot)` y
  relaciones FS con `lag_slots`. Los hitos duran cero y pueden ocupar la frontera
  terminal del horizonte. Se rechazan referencias inexistentes, ciclos e IDs
  duplicados.
- `periodic_series` expande `occurrence_count` desde
  `occurrence_index_starts_at` (1 por defecto). Cada predecesora usa el
  `occurrence_id_format` de la **serie referenciada**, más `occurrence_offset`.
  Solo admite `series_id` y `occurrence`, sin atributos ni conversiones de Python;
  el formato entero puede indicar ancho de hasta 99 caracteres.
- `expected_expanded_counts` verifica los cuatro conteos declarados. Una
  contradicción se presenta como error bloqueante; si faltan, se advierte que no
  hay control externo. Máximo 100.000 actividades expandidas.
- Los roles son demandas por habilidad y unidades enteras. El personal mantiene
  `skills`, costo horario `cost_per_slot / slot_hours` y una unidad por persona.
  Los recursos renovables son `equipo`, con `capacity` como unidades.
- Las ventanas de disponibilidad y `unavailable_intervals` usan fin excluido.
  Se conservan como excepciones de cero horas en calendarios de recursos,
  incluyendo equipos con disponibilidad acotada.

Supuestos editables: `fecha_base` (propuesta 2027-01-01), `moneda` (CLP) y
`role_mode`. `equivalencia_bloque_dia='synthetic_day_per_block'` declara el único
modelo temporal admitido: un bloque de 4 h de la referencia equivale a un día de
calendario continuo `1111111`, sin país de feriados ni turnos. El número de horas
proviene de la fuente. Cambiar la fecha base traslada el eje completo; no cambia
las cantidades ni los bloques de la solución. Otra equivalencia temporal no se
acepta silenciosamente: requiere otro adaptador.

Con personas nominales y demandas por rol se propone `indivisible`, que queda en
`resumen.solicitud_solver_sugerida`. La importación conserva demandas y no ejecuta
ni configura globalmente el solver. Para resolver la referencia se usa:

```json
{"apply": true, "options": {"role_mode": "indivisible", "block_hours": 4,
 "horizon_blocks": 60, "target_code": "A27", "allow_overtime": false,
 "respect_resource_limits": true, "time_limit_s": 120, "seed": 0}}
```

Aceptación API: **51 actividades, 24 ocurrencias de series, A27=36, personal=10.420,
cero violaciones**. El verificador independiente contrasta ventanas, dependencias,
roles distintos, habilidades, ausencias, solapes, capacidades y costo con el JSON
original. Evidencia en `faseB/demo/json/` del trabajo compartido.

## Inventario de equipos (fase B)

Identificador `inventario-equipos`, formato `inventario_equipos`, extensiones
`.xlsx` y `.pdf`. El perfil crea un proyecto de mantenimiento nuevo. El inventario
se conserva dentro de `Borrador.modelo`, en la trazabilidad SQLite existente; no
se agrega un esquema general de inventarios ni se transforma inventario físico
en capacidad de recursos de personal.

### Excel y revisión de PDF

El Excel exige las siete columnas **Planta, Bloque, Objeto/Tipo, Cantidad, Unidad,
Detalle, Fuente**, con mapeo editable. Se normalizan encabezados y nombres
conocidos, no cantidades. Una combinación planta/bloque/objeto no se repite.
Cantidades enteras no negativas; errores Excel o fórmulas sin caché bloquean.
Las filas de total se excluyen y se conservan como evidencia. Una fila `PLANTA`
no puede coexistir con el desglose por bloques del mismo objeto.

La plantilla descargable contiene una hoja Inventario vacía y un ejemplo
separado en Instrucciones. La exportación de inventario materializa cantidades y
fuentes sin fórmulas; las cadenas se guardan como texto, nunca como fórmulas.

El PDF se lee con `pdfplumber`, si está instalado, o `pypdf`. En el entorno de
aceptación no estaban disponibles y la red impidió instalarlos: se utilizó
**QtPdf/PDFium**, ya incluido con PySide6, en un proceso aislado con timeout de
45 segundos. Se reconstruyen las filas tabulares del texto y se comprueban
cantidades por bloque, número de bloques y resúmenes. Límites: 30 MB y 100 páginas.
No hay OCR. La variante respaldada por pruebas es el informe Chile Solar con sus
tablas de tres plantas; otros diseños pueden requerir Excel. Un PDF arbitrario
se presenta como candidato de confianza limitada, no como extracción certificada.
El respaldo QtPdf no se lanza desde un ejecutable congelado: sin lector Python
incluido se ofrece Excel, evitando abrir nuevas instancias de la aplicación.

`modelo.inventario_base` conserva las filas observadas/corregidas y
`modelo.inventario` incluye los derivados. `vista_previa` muestra las tablas de
origen; `excluidos` conserva los totales de control. La tabla de revisión GUI
permite corregir filas, agregar o retirar registros y conserva esos cambios en
`supuestos.filas_revisadas`. Una corrección no elimina la obligación de revisar.

Para PDF, tanto el servicio `confirmar` como la API exigen
`aceptar_revision_inventario=true`, además de las aceptaciones generales. La GUI
tiene una casilla específica, inicialmente desmarcada. La revisión sigue vinculada
al hash del borrador; los cambios obligan a previsualizar de nuevo. Se persiste
`revision_confirmada=true` en los metadatos. No se importa automáticamente.

En Chile Solar, los cuadros resumen controlan el detalle y no se suman como filas
adicionales. El total **57/57/57, general 171** del Cuadro 2 se comprueba excluyendo
módulos; la suma completa **28.071/28.071/24.939, general 81.081** se conserva para
auditoría. Los ceros de Meteo Station en AND/SDN se leen del resumen explícito.

### Derivados y reglas de cantidad

`strings_por_SCB` empieza en `null`: no se inventan strings. El texto de la fuente
que dice 4 se conserva en Detalle y se advierte. La aceptación confirma **24**.
`hileras_por_tracker` se propone únicamente cuando el detalle extraído contiene un
valor inequívoco (27 en el informe). Ambos parámetros son editables. Los derivados
incluyen `SUPUESTO editable:` en Detalle y pueden recalcularse al reimportar su
Excel. Una cantidad observada explícitamente de Strings/Hileras conserva prioridad.

Cada elemento de `supuestos.vinculos` identifica `codigo`, `destino` (`actividad`
o `ruta`), `planta`, `objeto`, `bloque` opcional y una regla:

| Regla | Cantidad_Total |
|---|---|
| `total` | Suma del 100 % de los bloques seleccionados |
| `porcentaje_por_bloque` | `Σ ceil(cantidad_bloque × porcentaje / 100)` |
| `fija` | `cantidad`, entero confirmado por el usuario |

El porcentaje se calcula con aritmética decimal. No se aplica sobre el total
agregado ni sobre filas sin desglose. El vínculo alimenta
`supuestos.lotes[codigo].quantity_total` o `supuestos.rutas[].quantity_total`.
No sustituye tamaño de lote, rendimiento ni equipos. Cantidad cero no crea
actividades ficticias: hay que retirar la tarea de esa planta o corregir el vínculo.
El generador y la validación de rutas son los nativos; el checklist conserva los
códigos de componentes. Una ruta transmite objeto/planta a componentes que no
tengan valores explícitos; las contradicciones siguen bloqueando.

Ejemplo completo de `supuestos` para una tarea anual de muestra:

```json
{
  "strings_por_SCB": 24,
  "fecha_inicio": "2027-01-01", "fecha_fin": "2027-12-31",
  "maestras": [{"code": "REV", "name": "Revisar módulos",
                "frequency": "A", "first_due": "2027-01-01",
                "work_hours": 8, "duration_days": 1, "crew_size": 1}],
  "equipos": [{"name": "Equipo", "regime": "5x2", "cycle_start": "2027-01-01",
               "members": 1, "hours_day": 8}],
  "lotes": {"REV": {"batch_size": 500, "productivity_day_team": 500,
                    "team_names": ["Equipo"]}},
  "vinculos": [{"codigo": "REV", "destino": "actividad", "planta": "DST",
                "objeto": "Modulos", "regla": "porcentaje_por_bloque", "porcentaje": 15}]
}
```

Resultado: `quantity_total=4203`. Calendario, HH, dotación, frecuencia, lote y
rendimiento del ejemplo son SUPUESTOS; el inventario no certifica productividad.
La GUI proporciona un formulario de selección de destino/planta/objeto/regla;
las definiciones de maestras, equipos y rutas usan el editor de estructuras.
Con listas de maestras/rutas vacías se guarda solo inventario, con cero actividades.

Aceptación (columnas DST / AND / SDN):

| Objeto | 100 % | 15 % por bloque |
|---|---|---|
| Módulos | 28.014 / 28.014 / 24.882 | 4.203 / 4.203 / 3.735 |
| Strings (24/SCB) | 1.152 / 1.152 / 1.152 | 174 / 174 / 174 |
| Hileras (27/tracker) | 324 / 324 / 243 | 51 / 51 / 39 |
| SCB | 48 / 48 / 48 | — |
| Trackers | 12 / 12 / 9 | — |
| SKID | 3 / 3 / 3 | — |
| Meteo Station | 3 / 0 / 0 | — |

### API adicional

| Método y ruta (prefijo `/api`) | Resultado |
|---|---|
| `GET /importadores/plantillas/inventario.xlsx` | Plantilla vacía con instrucciones |
| `GET /importadores/borradores/{id}/inventario.xlsx` | Filas y derivados para revisar antes de importar |
| `GET /projects/{pid}/inventario.xlsx` | Inventario persistido, separado del Excel de tareas |

El flujo de cargas y borradores de fase A se conserva. Una confirmación PDF usa:

```json
{"revision": "hash_del_borrador", "aceptar_supuestos": true,
 "aceptar_advertencias": true, "aceptar_revision_inventario": true}
```

Omitir la revisión de PDF devuelve **422**, revisión obsoleta **409**. La carga
repetida confirmada es idempotente. Las pruebas y demos de fase B usan la API real,
SQLite aislado y Qt offscreen; no reemplazan una aceptación de operación en planta.
