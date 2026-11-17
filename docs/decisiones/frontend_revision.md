# Revisión del frontend — 26 de septiembre de 2026

Alcance: SPEC §§10–12 y `backend/ganttier/domain.py`, contrastados con las rutas y el repositorio del backend disponibles durante la revisión. Solo se modificaron archivos de `frontend/` y este documento. No se agregaron dependencias ni se modificaron endpoints del backend.

## Hallazgos y correcciones, por prioridad

### P0 — Integridad de los datos

- **Avance asociado a la fila equivocada.** `ControlPage.tsx` indexaba borradores por posición en la respuesta columnar. Ahora usa `activity_id`, resuelve su posición al guardar y rechaza actividades que dejaron de existir. Valida porcentaje, HH y fechas reales. Ya no inventa el inicio real a partir del inicio planificado. Las HH siguen siendo las del período, no acumuladas.
- **Respuesta anterior sobre un proyecto/período nuevo.** `store.tsx`, `App.tsx`, Gantt, Macro, Control, Desempeño y calendarios descartan respuestas obsoletas o separan el estado de cada proyecto mediante una clave de montaje. Los recursos se exponen solo para el proyecto al que pertenece su carga. El proyecto se carga antes de montar sus páginas.
- **Regeneración destructiva presentada como conservación del avance.** `MaintenancePage.tsx` enviaba siempre `replace: true` y prometía conservar los avances. El repositorio inspeccionado elimina las ocurrencias anteriores. Ahora el valor predeterminado es `false`; reemplazar requiere marcar una opción cuyo texto explica la eliminación. No se ejecutó ninguna generación real durante la revisión.
- **Actualizaciones concurrentes de asignaciones.** El detalle de Gantt enviaba un PUT del objeto entero por pulsación, permitiendo que una respuesta/escritura antigua ganara. Las ediciones quedan locales y se guardan una vez por asignación modificada con Guardar. Los cambios a listas usan actualizadores funcionales.

### P1 — Fechas, Gantt y modelo

- `dates.ts`: validación estricta de fecha ISO, días UTC desde 1970, rechazo de fechas inexistentes y períodos vacíos; tratamiento de años 0–99 al construir meses. La fecha «Hoy» se toma del calendario local y se convierte al mismo número de día UTC, también en Gantt y Macro.
- `model.ts`: IDs en `Float64Array`, evitando truncarlos a 32 bits; duración nula conserva «desconocida» y no se transforma en hito; los seis extremos de programa/base/ventana participan simétricamente en el horizonte. Una ventana parcial ya no produce `maxDay = -Infinity`. Se validan longitudes columnares antes de construir el modelo.
- `GanttView.tsx`: barras y ventanas conservan el fin inclusivo (`finish + 1` es el borde derecho). Relaciones y vínculo provisional de un hito llegan a su fecha, sin agregarle un día. Una barra sin fin puede estirarse tomando su inicio como extremo inicial.
- `GanttPage.tsx`: estirar una actividad cuenta días laborables del calendario efectivo explícito, en vez de multiplicar duración por una proporción de días corridos. En mantenimiento, mover o editar el inicio conserva la duración laborable y usa el último día de trabajo como fin inclusivo. Se rechazan movimientos sin calendario explícito o sin suficientes días dentro del horizonte. Falta formalizar el calendario predeterminado resuelto por el servidor (ver pendientes).
- El sombreado registra también los días laborables devueltos: un calendario sin descansos no cae por error al supuesto lunes–viernes. Fuera de la cobertura de un calendario explícito no se inventan feriados ni descansos.
- Se eliminó el GET de detalle que el SPEC no define: `api.activity(pid, id, code)` consulta el endpoint existente de actividades, con búsqueda, paginación y verificación del ID. El ID es la identidad, aunque la búsqueda devuelva varios códigos similares.

### P1 — React y rendimiento masivo

- `GanttView.tsx`: planificador de dibujo estable con referencia al último callback, como máximo un RAF pendiente y cancelación al desmontar. Los observadores de tema ya no dibujan los datos del primer render. Se limpian los listeners de arrastre, columnas y separador al desmontar/cambiar modelo.
- Scroll horizontal: solo actualiza la referencia y el canvas. Scroll vertical: desplaza la capa de tabla mediante `transform`; actualiza el estado de React cuando cambia la primera fila, no por cada píxel dentro de la misma fila. Solo se crean las filas visibles y una pequeña cobertura adicional. Los modelos/filtros siguen memorizados fuera del componente visual.
- Relaciones: índice de adyacencia compacto construido en O(n + relaciones); el dibujo recorre las relaciones salientes de las filas visibles y exige ambos extremos visibles. Se retiró el apagado total a partir de 60 000 relaciones. El costo por frame depende de las filas visibles y su grado, no de un barrido de todas las relaciones.
- WBS: agregación y recorrido iterativos O(n + WBS), salvo ordenación; tolerancia a ciclos y huérfanos sin perder actividades; sin desbordamiento de pila por profundidad. Se reutiliza `Intl.Collator`. Se permite agrupar también con 100 000 actividades; antes se desactivaba sobre 30 000.
- `MacroPage.tsx` y `utils/macro.ts`: mapa actividad→posición para selección de rangos, sin `indexOf`; localización de meses O(1); totales O(n + meses) por diferencias acumuladas. Los extremos son inclusivos, los meses parciales reciben la proporción de días corridos correspondiente y las ventanas fuera del horizonte no se acumulan falsamente en su primer/último mes. El scroll actualiza React por fila. Los borradores usan IDs y los lotes ya guardados se retiran individualmente, incluso si un lote posterior falla.
- Control: cambiar período/base de la curva ya no vuelve a descargar las 100 000 actividades. Se eliminó la descarga de todo el historial de avance solo para mostrar su longitud. Se limita la grilla a 200 filas y se corrige la página cuando cambia el filtro o disminuye la cantidad de filas.
- Desempeño: respuestas obsoletas descartadas, fechas vacías/invertidas rechazadas, comparación de dos valores nulos igual a cero, tabla paginada a 100 trabajadores. El gráfico informa que muestra hasta 40 valores mayores. El plan maestro de mantenimiento se pagina a 200 filas: antes las tareas posteriores a la 2000 eran inaccesibles.

### P2 — Contrato, estados y accesibilidad

- `client.ts`: respuestas 204 de guardar avance, corte y aplicar corrida se distinguen de errores capturados por `useAction`. JSON inválido y HTML servido por error en una ruta API generan mensajes útiles. No se cambiaron los nombres de los endpoints.
- `types.ts`: se incorporaron `starts`, `finishes` y `worker_assignment` de `OptimizeResult`; extensiones columnares documentadas como opcionales; duraciones/HH nulas no implican cero.
- Optimizar y Macro ya no anuncian que una solución infactible fue aplicada. La simulación sin `run_id` explica que no puede aplicarse desde ese resultado. EVM etiqueta las unidades usando `evm.basis`, no suponiendo que el servidor aceptó el parámetro pedido.
- Cargas y errores visibles en las páginas principales; navegación sin proyecto hacia Importar reparada; hash de ruta validado. Edición en línea: Enter confirma una sola vez y Escape descarta, sin guardar por un blur posterior. Atajos del Gantt respetan formularios y diálogos.
- `ui.tsx`: foco inicial/restauración, Escape y ciclo de Tab en modales; `aria-modal`. Gantt/WBS y celdas macro tienen operación por teclado; nombres accesibles en la grilla de avance y selectores de período; foco visible; anuncios de estado/error. Las columnas de la tabla del Gantt admiten desplazamiento horizontal propio.
- Tema: se actualizan las variables del DOM antes de recalcular las opciones ECharts. Curva S no recorta costos/HH reales superiores al 100 % del presupuesto.
- Calendarios: Nuevo ya no queda reemplazado inmediatamente por el primer calendario existente. Importar al proyecto exige una validación sin errores del archivo y modo actuales; cambiar modo invalida la previsualización. Reemplazar requiere confirmación en la aplicación. Informes no genera con fecha vacía y la vista HTML queda en un iframe sin permisos de scripts.

## Pendientes concretos para Claude/backend

1. **Completar y formalizar las extensiones del endpoint columnar.** Añadir arreglos alineados con `ids`, todos con idéntica longitud:

   | Columna | Campo del dominio | Uso |
   |---|---|---|
   | `durations` | `duration_days` | Grilla, tooltip y ponderación WBS |
   | `work_hours` | `work_hours` | Grilla, control y estimación macro |
   | `macro_months` | `macro_month` | Edición del mes |
   | `skills` | `required_skill` | Búsqueda y mantenimiento |
   | `locations` | `location` | Búsqueda y calendario por instalación |
   | `statuses` | `status` | Metadatos del modelo |
   | `outages` | `requires_outage` | Indicadores y filtro de desconexión |
   | `window_hards` | `window_hard` | Distinguir ventana dura/blanda |

   No están en SPEC §10 ni en la consulta columnar inspeccionada. Sin ellas la navegación básica funciona; faltan valores/dimensiones. Macro muestra HH no disponibles, no cero. La dureza de ventanas existentes y la agrupación por instalación/desconexión requieren estas columnas. No se implementó una segunda descarga de 100 000 objetos completos para suplirlas.

2. **Formalizar optimización/historial.** `run_id` y `applied` no son campos de `domain.OptimizeResult`, pero habilitan Aplicar y su estado; describirlos explícitamente en el contrato HTTP. Especificar el esquema de `schedule/runs` (`id`, `created_at`, `kind`, `weights`, `metrics_before`, `metrics_after`, `applied`, `status`, `elapsed_s`). El frontend admite métricas/pesos como objeto o JSON serializado. No inventa un ID de corrida si falta.

3. **Parámetro `basis` en EVM.** El cliente lo envía; la ruta de backend inspeccionada lo acepta, pero SPEC §10 solo lo enumera para curva S. Documentar `hours|cost|count` y la unidad de respuesta. La pantalla ya usa la base devuelta para rotular EAC.

4. **Calendario efectivo por defecto.** El dominio permite `project.calendar_id = null`; distintas funciones de servicios eligen un calendario por defecto. Exponer/documentar una regla única o devolver el calendario resuelto. Para cambiar duración/mover mantenimiento la interfaz solicita un calendario explícito, evitando adivinarlo. El sombreado único del Gantt es del calendario de proyecto, no de los calendarios particulares de cada actividad.

5. **Detalle y asignaciones eficientes.** El detalle ya funciona con las rutas del SPEC. Opcionalmente añadir y documentar GET individual para evitar paginar búsquedas ambiguas. `GET assignments` descarga todas las asignaciones del proyecto para editar una actividad: conviene un filtro `activity_id` documentado y paginación consistente. La pantalla no depende de ese filtro inexistente.

6. **Regeneración de mantenimiento y avance.** Si el producto quiere preservar avance al reemplazar, implementarlo transaccionalmente en backend y fijar si `replace` afecta todo el proyecto o solo el horizonte. La UI actual no promete esa preservación y usa generación sin reemplazo por defecto.

7. **Atomicidad/concurrencia.** El PATCH macro se envía en lotes de 5000, que pueden tener éxito parcial; la UI conserva solo los pendientes. Documentar la atomicidad de cada petición. El guardado de una actividad y sus asignaciones son peticiones distintas; no hay transacción conjunta ni versión para detectar modificaciones concurrentes de otro cliente. Conviene mantener las validaciones de integridad y calendarios en servidor.

## Límites y seguimiento frontend

- Capacidad macro es una **estimación lunes–viernes**, sin feriados ni turnos; HH se reparten por días corridos. Se rotula explícitamente. Para carga operativa real, usar los perfiles de carga del backend. No equivale a una nueva ejecución del motor de calendarios.
- Macro limita la vista a 240 meses; calendario de mantenimiento a 60 meses. La tabla principal de actividades sí conserva las 100 000 filas. El mapa de calor de carga sigue usando un canvas completo: para muchísimos roles o horizontes extensos conviene virtualizarlo también.
- SPEC §11 menciona columnas configurables y edición de WBS; no hay un selector completo de columnas ni CRUD de WBS en la UI revisada. No se confundieron estas funcionalidades faltantes con bugs corregidos.
- Se ha reducido el trabajo por scroll y eliminado algoritmos problemáticos, pero no se midieron FPS, memoria del navegador ni carga HTTP completa de 100 000 actividades. Los benchmarks del modelo no certifican por sí solos el umbral de tres segundos de SPEC §12.
- Las peticiones obsoletas se descartan en las vistas corregidas, pero no se cancelan en el transporte; agregar `AbortSignal` puede ahorrar red/trabajo. Las pantallas de catálogos restantes aún pueden beneficiarse de una política unificada de carga/cancelación.

## Verificación reproducible

Pruebas sin dependencias adicionales en `frontend/tests/`:

```text
node tests/regression.cjs [ruta/typescript/lib/typescript.js]
node tests/api.cjs [ruta/typescript/lib/typescript.js]
node tests/check.cjs [ruta/typescript/lib/typescript.js]
node tests/check.cjs [ruta/typescript/lib/typescript.js] --full
```

Si se omite la ruta, se usa el TypeScript instalado en el proyecto. En esta sesión se utilizó el ejecutable Electron local en modo Node y el compilador TypeScript local, sin instalar paquetes. Se reprodujeron primero cinco fallos del modelo/fechas y luego pasaron con las correcciones. Las pruebas cubren UTC/bisiestos/entradas inválidas, IDs mayores que 32 bits, extremos parciales, WBS cíclicas y de profundidad 100 000, relaciones, distribución macro inclusiva y errores/paginación/204 del cliente API. También comprueban movimientos sobre fin de semana y duración cero.

Resultados observados con TypeScript **5.9.3** local (el proyecto declara `^5.5.4`):

- 8 grupos de regresión: pasan; 100 000 actividades + WBS de profundidad 100 000 + 99 999 relaciones: **374 ms** en esa corrida.
- Distribución de HH de 100 000 ventanas de un año bisiesto: **48 ms**.
- 3 grupos de cliente API con `fetch` simulado: pasan (204, búsqueda paginada/ID, 404, HTML inesperado y 422).
- Sintaxis de 24 archivos TypeScript/TSX: sin diagnósticos. Tipado `strict` del núcleo de fechas, macro, programación por calendario, modelo y cliente/tipos API: sin diagnósticos.
- Chequeo completo: **bloqueado por dependencias ausentes**, con errores de resolución de `react`, `react/jsx-runtime`, `react-dom` y `echarts`, y diagnósticos derivados. Registro en `frontend/tests/verification-results.json`.

**No se ejecutó npm**: el acceso a `registry.npmjs.org` devolvió `WinError 10013`, y este checkout no incluye `node_modules`. No se declara que el build completo ni una prueba visual en navegador hayan pasado. Con las dependencias originales disponibles, queda ejecutar `npx tsc --noEmit -p Ganttier/frontend` y verificar visualmente scroll, zoom, arrastre y cambios rápidos de proyecto/período en React 18.
