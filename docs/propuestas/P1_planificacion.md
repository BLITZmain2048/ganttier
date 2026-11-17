# P1 — Mejoras de planificación y optimización

Revisión del código disponible al 29 de septiembre de 2026. Se proponen 15 mejoras para Proyecto y Mantenimiento. No se ha modificado ni ejecutado el motor: los hallazgos proceden de lectura estática; las pruebas descritas son criterios para futuras implementaciones, no resultados obtenidos.

## Base revisada y descarte de duplicados

Se revisaron [README](../../README.md), [SPEC](../../SPEC.md), [manual](../manual_usuario.md), [decisiones del solver](../decisiones/solver.md), todos los módulos Python de `backend/ganttier/engine/` y `backend/ganttier/maintenance/`, y `backend/ganttier/solve.py`. Se contrastaron además dominio, calendarios, construcción/aplicación de problemas, recursos globales, duplicación de proyectos y pruebas de optimalidad existentes.

| Ya existe | Evidencia y límite relevante |
|---|---|
| CPM, FS/SS/FF/SF y desfases; ocho tipos de restricción; holguras y corte | `engine/cpm.py`. SNLT/FNLT se tratan como plazos blandos; el avance usa Progress Override. No equivale a analizar criticidad por recursos. |
| Calendarios por actividad y recurso, feriados, excepciones, turnos NxM | `calendars.py`, `engine/loads.py`, `maintenance/leveling.py`. La unidad temporal sigue siendo el día; tener 8 horas disponibles no expresa en qué horario ocurren. |
| SGS serial/paralelo, prioridades múltiples, aleatorización y justificación adelante/atrás | `engine/multistart.py`; mejora por desplazamientos en `heuristics.py`. No se propone añadir otro multiinicio genérico. |
| CP-SAT con incumbente, admisión por tamaño/memoria y fallback | `engine/cpsat.py`, `optimize.py`. Puede restringir inicios a una malla semanal; redondea HH conservadoramente y usa un objetivo aproximado. |
| Nivelación local y control de extensión del plazo | `heuristics.py`, `search_bounds.py`. Se descarta “nivelar dentro de holgura” como propuesta genérica: ya hay desplazamientos acotados, aunque una reserva contractual de holgura sería una ampliación distinta. |
| Ocurrencias periódicas, tolerancias, asignación de trabajadores y cuadrillas, reserva de carga fija | `generator.py`, `leveling.py`, `solve._maintenance`. Las relaciones no entran en la construcción del nivelador. |
| Agrupación y reporte de desconexiones | `leveling.py` penaliza abrir otra combinación instalación/fecha de inicio; `api/outages.py` ofrece consulta/exportación. No hay un recurso de desconexión con ventanas aprobadas e incompatibilidades. |
| Verificación independiente y protección de aplicación | `engine/verify.py`, `solve.py`, `services.apply_run`. El flujo máximo diario comprueba habilidades compartidas; no hay reducción de conflictos a un conjunto irreducible. |
| Simulación, historial, comparación antes/después, líneas base y duplicación | `services.py`, `repo.duplicate_project`. Falta un escenario de entradas aislado y reproducible con comparación entre alternativas. |

El código actual prevalece cuando difiere de los documentos: `solve_project` genera siempre las ocurrencias faltantes incluso con `regenerate=False`; `apply=False` puede escribir ese inventario y guardar una corrida. Además, `level_periodic` conserva fechas provisionales si falla, pero `solve_project` las elimina del resultado aplicable. Por ello no se propone “generar lo que falta” ni “impedir aplicar resultados infactibles”, ya resueltos en la orquestación. SPEC conserva referencias a la antigua interfaz web; los puntos de integración propuestos corresponden a PySide6.

Los esfuerzos incluyen motor, persistencia/adaptadores necesarios, una integración mínima en escritorio y pruebas del alcance indicado: S ≤2 días, M ≤1 semana, L ≤1 mes, XL >1 mes, para una persona familiarizada con el repositorio. Los archivos nuevos se identifican expresamente; todos los cambios enumerados son futuros. Las metas de rendimiento se medirán en un equipo de referencia registrado, con igual versión de dependencias, datos, semillas y presupuesto; no constituyen garantías universales para 100 000 actividades heterogéneas.

## Propuestas

### P1-01 Precedencias ejecutables en mantenimiento
- Problema que resuelve: El nivelador elige fechas sin recibir las relaciones y puede ser rechazado por el auditor pese a existir solución. Ejemplo: programa el ensayo de protecciones antes de terminar el cableado del paño.
- Propuesta: Construir paquetes conectados por precedencias y propagar sus límites temporales antes de asignar personas; programarlos con SGS y reparación local.
  Mantener el camino vectorizado para ocurrencias independientes y considerar los recursos compartidos entre paquetes mediante reservas comunes.
  Cubrir FS/SS/FF/SF y desfases con la misma semántica diaria existente, preservando corte, avance y restricciones duras.
- Dónde: `backend/ganttier/solve.py::_maintenance`, `maintenance/leveling.py`; nuevo `maintenance/precedence.py`; `engine/verify.py`; pruebas en `backend/tests/solver/` y `backend/tests/maintenance/`.
- Valor para el usuario: Alto — permite convertir listas de mantenimiento con secuencias técnicas en programas aplicables.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — paquetes distintos pueden competir por la misma cuadrilla; una partición por instalación no basta.
- Criterio de aceptación: Resolver una batería con testigos factibles para los cuatro tipos de relación, desfases positivos/negativos y recursos compartidos, sin infracciones del auditor; mantener el tiempo del caso existente de 100 000 independientes dentro de +10 % frente al motor base.
- Referencias normativas o de práctica: Semántica contractual de [SPEC, motor de programación](../../SPEC.md); no se atribuye esta arquitectura a una norma externa.

### P1-02 Asignación conjunta de habilidades
- Problema que resuelve: En Proyecto, `Loads` reparte demanda genérica proporcionalmente entre candidatos. Un técnico polivalente puede quedar parcialmente ocupado por montaje aunque sea el único disponible para protecciones.
- Propuesta: Incorporar asignación explícita actividad–habilidad–persona sobre los días afectados, descontando primero las asignaciones comprometidas.
  Usar flujo de costo mínimo para HH divisibles y variables discretas para dotación o continuidad de cuadrilla; persistir quién cubre cada demanda.
  Mantener el verificador independiente: su flujo diario ya existe y no debe convertirse en el mismo código que decide las asignaciones.
- Dónde: `backend/ganttier/engine/loads.py`, `heuristics.py`, `cpsat.py`, `verify.py`; nuevo `engine/allocation.py`; `domain.py`, `schema.sql`, `repo.py`, `services.py` y pruebas de solver.
- Valor para el usuario: Alto — aprovecha personas disponibles y reduce rechazos causados por un reparto conservador.
- Esfuerzo: XL.
- Riesgo técnico: Alto — cubrir HH fraccionarias no garantiza personas simultáneas ni una cuadrilla estable durante varios días.
- Criterio de aceptación: Con dos tareas de 8 HH, una persona montaje/protecciones y otra sólo montaje, ambas de 8 HH/día, obtener ejecución en un día asignando correctamente; en un caso con continuidad obligatoria, impedir relevos no autorizados y verificar todas las asignaciones.
- Referencias normativas o de práctica: Modelado de asignación con capacidad compartida; la referencia local del límite actual es [decisiones del solver, reparto conservador](../decisiones/solver.md).

### P1-03 Desconexiones como recurso limitado
- Problema que resuelve: Agrupar por instalación y fecha no representa permisos ni incompatibilidades. Dos mantenimientos de circuitos redundantes pueden quedar juntos aunque sólo se permita retirar uno de servicio.
- Propuesta: Añadir ventanas de desconexión identificadas, activos cubiertos, estado utilizable, cupos y grupos de incompatibilidad definidos por el planificador.
  Exigir que cada trabajo dependiente quede íntegramente dentro de una ventana válida y consuma capacidad durante su indisponibilidad; separar esa ocupación de sus HH.
  Reutilizar la agrupación existente como objetivo secundario. Primera versión diaria; el detalle horario dependerá de P1-09.
- Dónde: `backend/ganttier/domain.py`, `schema.sql`, `repo.py`; nuevos `engine/outages.py` y gestión de ventanas en `api/outages.py`; `engine/cpsat.py`, `heuristics.py`, `verify.py`, `maintenance/leveling.py`, `reports/outages.py` y GUI de mantenimiento.
- Valor para el usuario: Alto — produce programas compatibles con las ventanas operativas cargadas por el ingeniero.
- Esfuerzo: XL.
- Riesgo técnico: Alto — compartir una instalación no significa compartir una desconexión; faltan identidades de activos y compatibilidades explícitas.
- Criterio de aceptación: Dos retiros declarados incompatibles nunca se superponen aunque haya personas suficientes; al revocar una ventana, ningún resultado que la utilice puede aplicarse sin recalcular y verificar.
- Referencias normativas o de práctica: Práctica operativa de coordinar indisponibilidades. No se fija un plazo normativo ni se presume validación N-1: las reglas y autorizaciones deben provenir de los antecedentes vigentes del usuario.

### P1-04 Equipos indivisibles y movilización
- Problema que resuelve: El dominio ya admite `equipo`, pero Proyecto lo contabiliza como capacidad diaria y Mantenimiento filtra trabajadores/cuadrillas. Una grúa de 80 t o un equipo de inyección debe reservarse junto con el personal.
- Propuesta: Añadir demandas simultáneas separadas de personas y equipos, ocupación indivisible y alternativas compatibles de máquina.
  Reservar la máquina por su intervalo de uso, con disponibilidad y tiempos de montaje, traslado y desmontaje; comenzar con matrices de traslado en días aportadas por el usuario.
  Extender la construcción y la auditoría de ambos modos sin convertir horas de máquina en HH ni duplicar el catálogo de recursos.
- Dónde: `backend/ganttier/domain.py`, `schema.sql`, `repo.py`; nuevo `engine/equipment.py`; `engine/loads.py`, `cpsat.py`, `heuristics.py`, `verify.py`, `maintenance/leveling.py`, `solve.py` y edición de asignaciones en GUI.
- Valor para el usuario: Alto — evita programas imposibles por reservar una misma máquina para dos frentes.
- Esfuerzo: XL.
- Riesgo técnico: Alto — los tiempos de traslado dependen del orden de uso, por lo que no basta restar capacidad diaria.
- Criterio de aceptación: Dos izajes que requieran la misma grúa quedan separados aunque cada uno declare sólo 2 HH; una secuencia entre dos S/E incorpora el traslado configurado y respeta una indisponibilidad de la máquina.
- Referencias normativas o de práctica: Modelado de recursos renovables indivisibles y preparación entre trabajos; sin requisito normativo específico atribuido.

### P1-05 Reprogramación incremental estable
- Problema que resuelve: Existen actualizaciones incrementales de perfiles dentro de una búsqueda, pero no una reparación entre corridas con estabilidad explícita. La ausencia de un técnico puede desplazar innecesariamente todo el plan semanal.
- Propuesta: Partir del programa vigente e identificar el conjunto afectado por relaciones, calendarios y competencia por recursos; reservar la carga del resto.
  Incorporar una zona congelada y minimizar cambios de fecha/persona, sujetos a factibilidad y límites de plazo; ampliar gradualmente el vecindario si la reparación falla.
  Verificar el programa completo antes de aplicar y registrar por qué se movió cada actividad; conservar la protección por huella existente.
- Dónde: Nuevo `backend/ganttier/engine/repair.py`; `engine/heuristics.py`, `loads.py`, `verify.py`, `maintenance/leveling.py`, `solve.py`, `services.py`, `domain.py`, `api/solve.py` y `gui/pages/optimize.py`.
- Valor para el usuario: Alto — permite reaccionar a contingencias conservando compromisos comunicados a las cuadrillas.
- Esfuerzo: XL.
- Riesgo técnico: Alto — un recurso compartido puede transmitir el impacto fuera de los sucesores del trabajo editado.
- Criterio de aceptación: En un caso con una componente independiente y otra afectada, mantener intactas fechas y personas de la independiente y todos los congelados; en un plan de 100 000 con perturbación local y vecindario ≤1 000, lograr mediana de reparación ≤50 % de una corrida completa, midiendo también verificación.
- Referencias normativas o de práctica: Reparación de programas y planificación con horizonte móvil; extensión específica de Ganttier, sin atribuirla a una función exacta de P6.

### P1-06 Conflictos irreducibles y estados rigurosos
- Problema que resuelve: Ya hay prechequeos y violaciones detalladas, pero `infactible` también representa búsquedas fallidas. Una entrega MFO, una precedencia y una ventana pueden contradecirse sin que el usuario identifique qué conjunto debe revisar.
- Propuesta: Separar los estados sin solución encontrada, infactibilidad demostrada y modelo inválido; conservar los diagnósticos originales antes de eliminar fechas provisionales.
  Crear un modelo diagnóstico de factibilidad con restricciones identificadas y supuestos; reducir el núcleo por eliminación, comprobando cada retiro hasta obtener irreducibilidad o agotar presupuesto.
  Informar el ámbito exacto del certificado. No certificar el problema real desde una malla semanal, un horizonte recortado al incumbente o redondeos restrictivos; usar dominios completos y precisión validada.
  Devolver cambios candidatos con magnitud comprobada, sin aplicarlos automáticamente; un núcleo suficiente no se rotula IIS mínimo.
- Dónde: Nuevo `backend/ganttier/engine/diagnostics.py`; `engine/cpsat.py`, `optimize.py`, `verify.py`, `solve.py`, `domain.py`, `api/solve.py`, `gui/pages/optimize.py` y pruebas de infactibilidad.
- Valor para el usuario: Alto — convierte un fallo en una explicación accionable y evita confundir falta de tiempo con imposibilidad.
- Esfuerzo: XL.
- Riesgo técnico: Alto — la irreducibilidad requiere resoluciones adicionales; tampoco garantiza el menor número posible de restricciones.
- Criterio de aceptación: En un conflicto sintético de tres restricciones, demostrar que el conjunto es infactible y que retirar cualquiera lo vuelve factible; ante UNKNOWN o agotamiento durante reducción, mostrar diagnóstico parcial y nunca afirmar un IIS certificado.
- Referencias normativas o de práctica: [Contrato oficial de supuestos de CP-SAT](https://github.com/google/or-tools/blob/stable/ortools/sat/cp_model.proto): el núcleo devuelto no garantiza irreducibilidad. IIS significa irreducible, no mínimo en cardinalidad; el diagnóstico se ejecutaría sin objetivo y con un trabajador del solver.

### P1-07 Escenarios aislados y comparables
- Problema que resuelve: Simular, duplicar e inspeccionar antes/después ya existe, pero no hay una rama de entradas reproducible. Probar una segunda cuadrilla o la llegada tardía de un transformador exige alterar o copiar el proyecto.
- Propuesta: Guardar una instantánea de entradas con deltas de duración, disponibilidad, ventanas y capacidades, junto con semilla, versión y opciones de resolución.
  Generar ocurrencias en memoria durante la simulación; comparar alternativas contra una misma referencia mediante fechas de hitos, HH extra, ventanas incumplidas y actividades/personas cambiadas.
  Reutilizar historial y aplicación existentes, promoviendo sólo una alternativa revalidada contra el estado vigente y preservando las líneas base.
- Dónde: Nuevo `backend/ganttier/scenarios.py`; `domain.py`, `schema.sql`, `repo.py`, `solve.py`, `services.py`, `api/planning.py`, `gui/pages/optimize.py` y pruebas de integración.
- Valor para el usuario: Alto — permite justificar con resultados la contratación de recursos o la aceptación de una nueva fecha.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — los calendarios globales y la generación de ocurrencias deben quedar aislados; una huella por sí sola no reconstruye entradas anteriores.
- Criterio de aceptación: Simular tres escenarios no altera actividades, recursos, calendarios, avance ni inventario del proyecto vigente; repetir una instantánea con presupuesto determinista reproduce sus resultados y una edición posterior bloquea su promoción sin revalidación.
- Referencias normativas o de práctica: [GAO Schedule Assessment Guide](https://www.gao.gov/products/gao-16-89g), práctica de analizar efectos de cambios sobre un programa integrado; el diseño de instantáneas es propio de esta propuesta.

### P1-08 Criticidad explicada por recursos
- Problema que resuelve: La criticidad CPM no explica esperas por disponibilidad. Una prueba de puesta en servicio puede determinar el término porque espera al único especialista, aunque tenga holgura en la red lógica.
- Propuesta: Registrar causas de espera durante la construcción y formar un grafo explicativo del programa factible con enlaces lógicos, de calendario y de asignación de recursos.
  Calcular caminos impulsores y margen de desplazamiento manteniendo el orden de recursos elegido; validar cuellos de botella mediante perturbaciones locales de duración o capacidad.
  Identificar los resultados como criticidad condicionada a ese programa, conservar las holguras CPM y representar bloqueos conjuntos cuando la capacidad sea acumulativa.
- Dónde: Nuevo `backend/ganttier/engine/resource_criticality.py`; `engine/multistart.py`, `heuristics.py`, `loads.py`, `domain.py`, `services.py` y `gui/pages/activities.py`.
- Valor para el usuario: Alto — orienta refuerzos al recurso que realmente condiciona la energización.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Alto — con varios recursos y calendarios no siempre existe una única cadena causal; fijar un orden no prueba optimalidad global.
- Criterio de aceptación: En dos trabajos sin relación y con un único especialista, explicar la espera del segundo por ese recurso; al añadir capacidad suficiente, desaparecer ese bloqueo sin modificar la red contractual ni sus holguras CPM.
- Referencias normativas o de práctica: [Opciones de programación de P6](https://docs.oracle.com/cd/E75420_01/p6help/en/6067.htm) distingue holgura total y longest path. Esa distinción sirve de referencia; no se afirma que P6 implemente el grafo de recursos aquí propuesto.

### P1-09 Horarios intradía para intervenciones
- Problema que resuelve: Los turnos NxM actuales sólo distinguen días y horas disponibles. No pueden demostrar que maniobras y ensayos de 4 horas caben en una desconexión de 02:00 a 06:00 con personal disponible en ese horario.
- Propuesta: Añadir resolución opcional de 15/30/60 minutos para paquetes seleccionados, con intervalos de turno, descanso y disponibilidad simultánea de actividad, personas y equipos.
  Usar el plan diario como marco y reservar su capacidad al refinar paquetes, conciliando después la carga diaria; conservar el camino diario para el inventario masivo.
  Guardar tiempos con zona America/Santiago y una política explícita para horas inexistentes o repetidas por cambio de hora, duración continua e interrupciones permitidas.
- Dónde: Nuevos `backend/ganttier/engine/intraday.py` y contratos temporales; `calendars.py`, `domain.py`, `schema.sql`, `engine/verify.py`, `solve.py`, `api/schemas.py` y GUI de calendarios/actividades.
- Valor para el usuario: Alto — permite planificar y comprobar ventanas de trabajo eléctrico de pocas horas.
- Esfuerzo: XL.
- Riesgo técnico: Alto — cambia la representación temporal y puede multiplicar el modelo; requiere conciliación entre resoluciones y cambios de hora.
- Criterio de aceptación: Un ensayo continuo de 4 h cabe exactamente en 02:00–06:00 y uno de 5 h se rechaza; probar una transición horaria de America/Santiago sin duplicar capacidad ni perder duración transcurrida.
- Referencias normativas o de práctica: Resolución temporal propuesta para operación; no se declara cumplimiento de un régimen laboral ni se deducen descansos legales de los patrones NxM.

### P1-10 Lógica retenida para trabajo en curso
- Problema que resuelve: CPM y auditor omiten relaciones entrantes a actividades iniciadas mediante Progress Override. Un montaje iniciado parcialmente podría continuar en el programa aunque su liberación técnica siga pendiente.
- Propuesta: Incorporar una política explícita de reprogramación: conservar el comportamiento vigente o retener lógica para el remanente.
  Separar inicio real histórico e inicio remanente; conservar avance ejecutado y suspender/reubicar sólo trabajo futuro según los eventos pendientes y los recursos.
  Aplicar la misma política en CPM, optimización y auditoría, con explicación visible de la suspensión; definir el tratamiento de SS/FF/SF sin reescribir hechos reales.
- Dónde: `backend/ganttier/domain.py`, `schema.sql`, `engine/cpm.py`, `loads.py`, `cpsat.py`, `verify.py`, `solve.py`, `services.py`, `control/progress.py` y GUI de control/optimización.
- Valor para el usuario: Alto — hace que el programa remanente respete bloqueos técnicos aun cuando se haya registrado avance fuera de secuencia.
- Esfuerzo: XL.
- Riesgo técnico: Alto — el modelo actual fija inicio y término calculado de actividades en curso; hay que representar explícitamente el remanente y su carga.
- Criterio de aceptación: En un sucesor FS ya iniciado con predecesor pendiente, lógica retenida mueve sólo el remanente después del predecesor; Progress Override reproduce el comportamiento anterior y ambas políticas conservan fechas reales, avance y HH ejecutadas.
- Referencias normativas o de práctica: [P6, Retained Logic y Progress Override](https://docs.oracle.com/cd/E75420_01/p6help/en/6067.htm), opciones documentadas para avance fuera de secuencia.

### P1-11 Riesgo de plazo con Monte Carlo
- Problema que resuelve: Las duraciones actuales son deterministas. Un plazo de montaje de línea no refleja incertidumbre de fundaciones, clima o entrega de equipos, y una proyección de avance no entrega probabilidad de energizar a tiempo.
- Propuesta: Añadir duraciones optimista/probable/pesimista, distribuciones triangular y beta-PERT y riesgos comunes por grupo; registrar supuestos, semilla y muestras.
  Simular ejecución con una política fija de prioridad y recursos, propagando retrasos sin otorgar a cada muestra una reoptimización con conocimiento del futuro.
  Entregar P50/P80/P90, probabilidad de hitos, sensibilidad y proporción de muestras sin programa encontrado; distinguir estas últimas de incumplimientos demostrados y no descartarlas silenciosamente.
- Dónde: Nuevo `backend/ganttier/engine/risk.py`; `domain.py`, `schema.sql`, `repo.py`, `engine/cpm.py`, `multistart.py`, `verify.py`, `services.py` y comparación de escenarios en GUI.
- Valor para el usuario: Alto — permite dimensionar contingencias y comunicar fechas con un nivel de confianza explícito.
- Esfuerzo: XL.
- Riesgo técnico: Alto — supuestos de independencia, política de recursos y muestras fallidas pueden sesgar mucho los percentiles.
- Criterio de aceptación: Con duraciones sin dispersión, todas las muestras reproducen el término determinista bajo la misma política; en una cadena pequeña contrastable por enumeración, 10 000 muestras estiman la probabilidad del hito con error absoluto ≤0,02 y son reproducibles con la misma semilla.
- Referencias normativas o de práctica: [GAO Schedule Assessment Guide](https://www.gao.gov/products/gao-16-89g), análisis de riesgo de cronograma. Las distribuciones y el diseño de simulación descritos son elecciones propuestas, no obligaciones normativas chilenas.

### P1-12 Búsqueda por vecindarios de conflicto
- Problema que resuelve: Multiinicio y movimientos individuales ya existen, pero pueden estancarse cuando se requiere intercambiar bloques. Dos cadenas de montaje pueden competir por una cuadrilla y necesitar desplazamientos coordinados para mejorar.
- Propuesta: Añadir destrucción/reconstrucción de vecindarios seleccionados por congestión, ventanas ajustadas y cadenas impulsoras, usando el incumbente factible como referencia.
  Resolver cada vecindario con SGS o CP-SAT según su tamaño, manteniendo reservas externas; refinar a días completos el dominio local cuando la malla semanal limite mejoras.
  Registrar tiempo hasta primera solución, mejoras, tamaño del vecindario y consumo; corregir el techo interno de 19 s del camino CP-SAT para administrar el presupuesto solicitado con márgenes explícitos.
- Dónde: Nuevo `backend/ganttier/engine/neighborhoods.py`; `engine/optimize.py`, `cpsat.py`, `multistart.py`, `heuristics.py`, `search_bounds.py`; conexión con `maintenance/leveling.py` y benchmarks en `backend/tests/solver/`.
- Valor para el usuario: Alto — mejora programas congestionados sin llevar las 100 000 actividades a un único modelo entero.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — la selección de vecindarios puede consumir tiempo sin mejorar; siempre debe conservarse el incumbente auditado.
- Criterio de aceptación: En una batería fijada antes del ajuste, lograr ≥5 % de mejora mediana en el objetivo exacto para casos deliberadamente estancados, con igual presupuesto y sin empeorar incumbentes; mantener el benchmark de independientes dentro de +10 % y publicar también casos sin mejora.
- Referencias normativas o de práctica: Búsqueda de grandes vecindarios como estrategia de optimización; no se promete un gap universal ni se sustituyen las pruebas exactas pequeñas ya existentes.

### P1-13 Objetivos coherentes y prioridades explícitas
- Problema que resuelve: El objetivo CP-SAT aproxima nivelación/costo, mientras el publicado es diario; en mantenimiento `makespan` suma desplazamientos respecto de ventanas, no minimiza directamente el fin máximo. Las mezclas pueden producir decisiones difíciles de explicar.
- Propuesta: Definir un contrato de métricas con unidades, período y normalización fija para comparar candidatos y escenarios; distinguir adelanto respecto del vencimiento y duración total.
  Añadir prioridades lexicográficas configurables, por ejemplo cumplir hitos duros, reducir costo y finalmente nivelar, con tolerancias explícitas entre etapas.
  Construir costos diarios exactos donde quepan; cuando se use un sustituto, mostrarlo, reevaluar con la métrica común y publicar cotas/gap sólo del modelo al que pertenecen.
- Dónde: `backend/ganttier/domain.py`, `engine/loads.py`, `cpsat.py`, `optimize.py`, `maintenance/leveling.py`, `services.py`, `gui/pages/optimize.py` y pruebas de objetivos.
- Valor para el usuario: Alto — hace predecible qué sacrifica el buscador para ahorrar horas extra o reducir plazo.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — cambiar significados rompe comparaciones históricas; las corridas deben guardar versión del contrato de objetivos.
- Criterio de aceptación: Un caso con exceso diario y capacidad semanal sobrante conserva su costo diario real en ambos motores; en instancias pequeñas enumerables, la modalidad lexicográfica respeta el objetivo prioritario y el estado óptimo queda limitado al dominio/objetivo efectivamente resuelto.
- Referencias normativas o de práctica: [Estados oficiales de CP-SAT](https://developers.google.com/optimization/cp/cp_solver) y límites ya reconocidos en [decisiones del solver](../decisiones/solver.md); diseño lexicográfico propuesto, sin equivalencia normativa atribuida.

### P1-14 Separación entre mantenimientos sucesivos
- Problema que resuelve: Las recurrencias ya están ancladas y no derivan al ajustar meses, pero sus ventanas se nivelan individualmente. Una inspección mensual tardía seguida de otra temprana puede quedar concentrada en pocos días.
- Propuesta: Añadir intervalos mínimo y máximo entre ejecuciones de una misma tarea/equipo, con referencia configurable inicio–inicio o término–inicio.
  Considerar la última ejecución real anterior al horizonte y la próxima obligación posterior; propagar límites entre ocurrencias y reparar cadenas sin cambiar el anclaje vigente por defecto.
  Ofrecer recurrencia desde última ejecución sólo como política explícita, distinguiendo mantenimiento vencido de una modificación de frecuencia.
- Dónde: `backend/ganttier/domain.py`, `schema.sql`, `repo.py`, `maintenance/generator.py`, `leveling.py`, `solve.py`, `engine/verify.py` y edición del plan maestro en GUI.
- Valor para el usuario: Alto — evita perder cobertura preventiva por agrupar revisiones demasiado próximas o demasiado separadas.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — las obligaciones cruzan el horizonte y dependen de históricos fiables; no deben desaparecer al regenerar.
- Criterio de aceptación: Dos ocurrencias con ventanas superpuestas respetan un mínimo configurado de 20 días aunque agruparlas mejore la carga; una ejecución previa al horizonte participa en el máximo permitido y, sin nuevas reglas, se conservan fechas objetivo e índices del generador actual.
- Referencias normativas o de práctica: Frecuencias e intervalos definidos por fabricante o plan aprobado, que deben registrarse como fuente por tarea; no se inventa una periodicidad obligatoria para equipos eléctricos.

### P1-15 Reservas de recursos entre proyectos
- Problema que resuelve: `Resource.project_id=None` permite recursos globales, pero `build_problem` carga actividades y asignaciones de un solo proyecto. La misma cuadrilla puede quedar comprometida simultáneamente en una obra de S/E y un plan de mantenimiento.
- Propuesta: Cargar compromisos vigentes de recursos globales en otros proyectos como reservas externas fechadas, con origen y prioridad visibles.
  Descontarlos durante construcción y verificación; la primera versión mueve sólo el proyecto activo y representa explícitamente demanda externa genérica cuando todavía no tiene persona asignada.
  Incluir la versión de esas reservas en la huella y comprobarlas transaccionalmente al aplicar para impedir dos compromisos simultáneos basados en la misma disponibilidad.
- Dónde: `backend/ganttier/services.py::build_problem`, `_scenario_fingerprint`, `apply_run`; `repo.py`, `domain.py`, `engine/loads.py`, `verify.py`, `solve.py`, `maintenance/leveling.py` y consulta de reservas en GUI de recursos.
- Valor para el usuario: Alto — elimina dobles compromisos entre los dos modos sin exigir un optimizador completo de cartera.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — las reservas y asignaciones genéricas cambian fuera del proyecto activo; se necesita una política de compromiso única y validación al aplicar.
- Criterio de aceptación: Una cuadrilla global reservada 8 h en Proyecto no puede aportar otras 8 h al Mantenimiento ese día; de dos corridas basadas en la misma capacidad libre, aplicar la primera invalida o fuerza revalidación de la segunda.
- Referencias normativas o de práctica: [P6, nivelación considerando asignaciones de otros proyectos](https://docs.oracle.com/cd/E68199_01/p6help/en/43145.htm), antecedente funcional. Ganttier debe utilizar los calendarios reales de las reservas externas.

## Tabla resumen

| ID | Título | Valor | Esfuerzo | Riesgo |
|---|---|---|---|---|
| P1-01 | Precedencias ejecutables en mantenimiento | Alto | L | Medio |
| P1-02 | Asignación conjunta de habilidades | Alto | XL | Alto |
| P1-03 | Desconexiones como recurso limitado | Alto | XL | Alto |
| P1-04 | Equipos indivisibles y movilización | Alto | XL | Alto |
| P1-05 | Reprogramación incremental estable | Alto | XL | Alto |
| P1-06 | Conflictos irreducibles y estados rigurosos | Alto | XL | Alto |
| P1-07 | Escenarios aislados y comparables | Alto | L | Medio |
| P1-08 | Criticidad explicada por recursos | Alto | L | Alto |
| P1-09 | Horarios intradía para intervenciones | Alto | XL | Alto |
| P1-10 | Lógica retenida para trabajo en curso | Alto | XL | Alto |
| P1-11 | Riesgo de plazo con Monte Carlo | Alto | XL | Alto |
| P1-12 | Búsqueda por vecindarios de conflicto | Alto | L | Medio |
| P1-13 | Objetivos coherentes y prioridades explícitas | Alto | L | Medio |
| P1-14 | Separación entre mantenimientos sucesivos | Alto | L | Medio |
| P1-15 | Reservas de recursos entre proyectos | Alto | L | Medio |

## Top-5 recomendado

1. **P1-01 — Precedencias ejecutables en mantenimiento.** Cierra una brecha comprobada entre construcción y auditoría con alcance acotado. Aprovecha el SGS existente y preserva la vía rápida para el volumen masivo.
2. **P1-15 — Reservas de recursos entre proyectos.** Hace efectivo el carácter compartido de los recursos globales y evita dobles compromisos entre obra y mantenimiento. Su primera versión no necesita reoptimizar una cartera completa.
3. **P1-13 — Objetivos coherentes y prioridades explícitas.** Da una base verificable a las decisiones de optimización y a las comparaciones posteriores. Conviene estabilizar este contrato antes de ajustar nuevas búsquedas o presentar escenarios como alternativas económicas.
4. **P1-07 — Escenarios aislados y comparables.** Permite al ingeniero evaluar cambios de dotación, fechas y ventanas sin alterar el plan operativo. Reutiliza el historial existente; P1-13 aporta métricas comparables entre alternativas.
5. **P1-03 — Desconexiones como recurso limitado.** Es la ampliación de mayor especificidad eléctrica: transforma agrupaciones orientativas en restricciones operativas verificables. Entregar primero ventanas diarias y exclusiones explícitas; abordar después el detalle horario de P1-09.

Las cinco tienen valor alto, pero priorizan distinta relación entre impacto y costo. P1-02 y P1-06 subirían de prioridad si se observan muchos rechazos por polivalencia o diagnósticos insuficientes; el ajuste de estados de P1-06 puede adelantarse como entrega parcial sin prometer todavía IIS. P1-05 y P1-12 comparten infraestructura de reparación, aunque una busca estabilidad ante cambios y la otra calidad de la solución. P1-09 y P1-11 se posponen por su costo y porque necesitan primero contratos temporales, de recursos y de métricas consolidados.
