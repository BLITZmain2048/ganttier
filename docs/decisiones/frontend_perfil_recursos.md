# Perfil de recursos bajo el Gantt

Implementado el 26 de septiembre de 2026, tras revisar SPEC §§10–11 y `frontend_revision.md`. Sin dependencias nuevas ni cambios al backend.

## Integración y geometría

`GanttPage.tsx` añade el conmutador «Perfil de recursos» y un contenedor vertical para Gantt y perfil. El perfil empieza oculto y con altura de 180 px. Su borde superior admite arrastre con captura de puntero y teclado (arriba/abajo, Inicio/Fin); reserva 100 px al Gantt cuando hay espacio. Ocultarlo conserva altura, agrupación y selección durante el montaje de la página, libera observadores de dibujo y no solicita datos. Al mostrarlo vuelve a cargar.

`GanttView.tsx` publica `onViewport({ originDay, dayWidth, scrollX, width })` desde su RAF. `viewport.ts` mantiene ese valor fuera del estado de React y notifica únicamente si cambia. El perfil se dibuja en ese mismo RAF durante desplazamiento/zoom; sus cambios propios (datos, selección, tamaño y tema) se agrupan en un RAF cancelable. El scroll vertical no vuelve a dibujar el perfil. No se actualiza el estado de GanttPage por scroll.

Ambos gráficos usan exactamente `x = (day - originDay) * dayWidth - scrollX`. El perfil replica `tableWidth`, el separador de 5 px y la reserva derecha de 14 px del scrollbar. No introduce margen izquierdo para un eje Y. Las fechas provienen de `labels`, convertidas a días UTC; cada barra ocupa un día o siete días desde esa fecha. No se distribuyen los períodos uniformemente por índice: se conservan los huecos temporales. Se contempla el devicePixelRatio.

## Datos, selección y significado

- Solicitud: `api.load(projectId, group, period)`, sin descargar actividades ni asignaciones adicionales. `group` es `skill` o `resource`; `period` es `day` con `dayWidth >= 4`, y `week` por debajo. El scroll no hace peticiones. El cambio de escala dentro del mismo período tampoco.
- Se recarga al cambiar `scheduleVersion`, agrupación, período, proyecto, o al reabrir/reintentar. Se descartan respuestas obsoletas y se vincula cada modelo a la clave de su solicitud para impedir que se dibuje bajo otro contexto. El transporte existente no permite abortar; descartar no cancela el trabajo del servidor.
- Las cinco series de mayor suma de carga en el horizonte completo se seleccionan inicialmente. Se conserva la selección por agrupación al recargar, intersectándola con las claves todavía existentes; una selección vacía es válida. La lista muestra el total HH por serie.
- La línea discontinua representa la **suma de capacidad de las series seleccionadas**, comparable con su carga apilada. El tramo de carga por encima de esa suma se pinta con `--critical`. No se suman sobrecargas individuales: un déficit de una serie puede compensarse visualmente con capacidad de otra, como corresponde al total mostrado.
- Tooltip: inicio y fin inclusivo del período, HH de cada serie seleccionada, carga total, capacidad y utilización. Con capacidad cero: `∞ (sin capacidad)` si hay carga, y `— (sin capacidad)` si no la hay.
- El perfil corresponde a **todo el proyecto**, no al filtro de búsqueda, WBS ni «Solo críticas». La interfaz lo indica. El endpoint no ofrece ese filtro.
- Colores `--s1`…`--s8`, exceso `--critical`, fondo, texto y grilla con variables existentes. Se escucha `data-theme` y el cambio de preferencia del sistema.

## Coste del dibujo

`resourceProfileModel.ts` valida fechas ordenadas y no superpuestas, períodos, claves únicas, longitudes y valores finitos no negativos. Convierte las fechas y calcula el ranking una vez por respuesta. Los totales seleccionados y el máximo vertical se calculan al cambiar datos/selección y se reutilizan durante scroll; la escala Y es estable durante desplazamiento horizontal.

La búsqueda de períodos visibles es binaria: O(log P); dibujar cuesta O(V × S), con P períodos del perfil, V períodos visibles y S series seleccionadas. No hay recorrido de las 100 000 actividades en el perfil. Solo el canvas visible ocupa memoria gráfica. La respuesta HTTP y los cálculos al seleccionar abarcan el horizonte completo para poder elegir las cinco series de mayor carga global.

## Verificación realizada

Prueba nueva, dentro del alcance autorizado:

```text
node Ganttier/frontend/src/components/Gantt/resource-profile.test.cjs [ruta/typescript/lib/typescript.js]
```

Se ejecutó con Electron local en modo Node y TypeScript 5.9.3, sin instalar paquetes. Salida 0: ranking, capacidad seleccionada, exceso, selección vacía, límites diarios/semanales, huecos, respuestas inválidas, viewport exacto con zoom/scroll fraccionario, deduplicación y desuscripción. Tipado strict del modelo del perfil y del canal: cero diagnósticos. 10 000 consultas de rango visible sobre 100 000 períodos: 6 ms en la corrida observada; no es una medición de FPS.

La regresión existente `frontend/tests/regression.cjs` también terminó con salida 0: ocho grupos, incluidos 100 000 actividades, WBS y relaciones (411 ms en esa corrida).

`npx tsc --noEmit -p Ganttier/frontend` no pudo ejecutarse porque `npx` no está disponible. El comprobador local `frontend/tests/check.cjs --full` pudo analizar la sintaxis de 28 archivos TS/TSX sin diagnósticos y el núcleo/API strict sin diagnósticos. El chequeo completo queda bloqueado por `node_modules` ausente (`react`, tipos React/JSX, `react-dom`, `echarts` y errores derivados); **no se certifica el tipado completo ni el build**.

## Revisión pendiente con el entorno completo

1. Ejecutar `npx tsc --noEmit -p Ganttier/frontend` con las dependencias originales disponibles. Verificar en React 18: abrir/cerrar, arrastrar borde, teclado, modificar ancho de tabla, barra horizontal, Ctrl+rueda cruzando 4 px/día, Hoy/Ir a selección y temas claro/oscuro.
2. Medir scroll con 100 000 actividades con y sin perfil. El trabajo añadido no depende de las actividades, pero no se midieron FPS ni tiempos reales de paint en navegador.
3. Probar CPM/optimización/ediciones, solicitudes lentas, cambios rápidos de período/proyecto, errores y reintento, capacidad cero, series vacías y selección vacía.
4. Confirmar la semántica del backend por **habilidad**: `services.load_profile` suma la capacidad de un trabajador en cada habilidad que posee. Por tanto, sumar varias habilidades puede contar varias veces a un trabajador polivalente. Se representa fielmente el contrato; para capacidad de personas sin esa duplicación, usar agrupación por recurso. El backend también identifica recursos por nombre, no por ID, y las semanas de borde pueden contener solo los días del horizonte calculado.

## Archivos

- Modificados: `frontend/src/components/Gantt/GanttView.tsx`, `frontend/src/pages/GanttPage.tsx`, `frontend/src/styles.css`.
- Nuevos: `frontend/src/components/Gantt/ResourceProfile.tsx`, `viewport.ts`, `resourceProfileModel.ts`, `resource-profile.test.cjs` y este documento.
- `model.ts` no necesitó cambios.
