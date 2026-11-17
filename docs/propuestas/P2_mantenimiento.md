# P2 — Mantenimiento eléctrico de transmisión y distribución en Chile

Revisión: 29-09-2026. Documento de propuestas; no implementa cambios. Los ejemplos de instalaciones y los valores de las pruebas son ficticios. Los plazos de esfuerzo son estimaciones de desarrollo por una persona familiarizada con Ganttier, incluidas migración, interfaz y validación del alcance descrito, con sus dependencias disponibles; no son plazos regulatorios.

## Punto de partida comprobado

Se revisaron `README.md`, `docs/manual_usuario.md`, el conjunto `docs/decisiones/*.md`, `docs/plantillas.md`, `backend/ganttier/maintenance/`, `backend/ganttier/reports/outages.py`, el dominio, la integración de programación y las plantillas y ejemplos Excel. Cuando una decisión histórica describe una limitación ya corregida, prevalece el código actual: `level_periodic` ya recibe reservas de carga fija y expone los integrantes de las cuadrillas; `solve.py` también admite actividades ordinarias en modo Mantenimiento.

- **Ya existe planificación periódica:** frecuencias, anclas, tolerancias, ventanas duras, meses preferidos, horizonte, prioridad 0–100, habilidades, dotación, turnos y calendarios chilenos. La generación conserva identificadores de ocurrencia; la integración protege avance, bloqueos y líneas base. No se propone volver a construir estas funciones.
- **Ya existen atributos eléctricos y recursos:** `PeriodicTask` y `Activity` tienen instalación, equipo, tensión y necesidad de desconexión. Son atributos de tareas, no un catálogo de activos con identidad y ciclo de vida. `Resource.kind` admite equipo y material, pero el nivelador de mantenimiento filtra capacidad laboral a trabajador/cuadrilla: falta reservar medios técnicos y existencias conjuntamente con las personas.
- **Ya existe agrupación y exportación:** el nivelador favorece coincidencias por instalación; `outages.build_program` agrupa exactamente por `(location, fecha_inicio)`. El número del grupo es un correlativo de la consulta, la tensión resumida es la máxima y la duración son días corridos inclusivos. El Excel contiene Resumen, Programa y Detalle, responsables asignados y métricas de agrupación. No identifica una desconexión física persistente ni acredita factibilidad operacional.
- **Ya existe control:** avances fechados, HH/costos reales, líneas base, EVM, curvas S, cumplimiento por trabajador e informes. La brecha es historial técnico por activo, cierre de órdenes y confiabilidad; no otro tablero genérico de avance.
- **Excel inspeccionado:** `plantillas/Plantilla_Mantenimiento.xlsx` y su copia en `backend/ganttier/excel_io/generated/` incluyen `Plan_Maestro` con 27 columnas y `Avance`. También se revisaron las plantillas de Proyecto y `ejemplos/Ejemplo_Plan_Mantenimiento.xlsx`: ya contienen trabajos de termografía, DGA, protecciones y líneas. La novedad propuesta es gestionar sus resultados y restricciones, no agregar nombres de tareas de ejemplo.

## Criterio normativo y de alcance

Las referencias enlazadas respaldan la existencia y el alcance general indicado. Los artículos, versiones consolidadas, umbrales, plazos y aplicabilidad no confirmados se identifican como **«a verificar»**. No se deduce una frecuencia obligatoria a partir de una frecuencia de ejemplo ni se atribuye a una norma una fórmula propia del producto.

Para transmisión y distribución corresponde revisar los pliegos **RPTD** de SEC; los **RIC** regulan instalaciones de consumo y no se trasladan automáticamente a la red. RPTD N°15 aborda operación y mantenimiento; la LGSE y el DS N°109 son parte de su marco legal y reglamentario. La aplicabilidad concreta debe validarse por instalación. [SEC, RPTD N°15](https://www.sec.cl/sitio-web/wp-content/uploads/2020/09/Pliego-T%C3%A9cnico-Normativo-RPTD-N%C2%B015-Operaci%C3%B3n-y-mantenimiento.pdf), [SEC, ámbito de los pliegos RIC](https://www.sec.cl/reglamento-de-seguridad-de-las-instalaciones-de-consumo-de-energia-electrica-decreto-08/).

Una intervención de distribución no requiere por defecto el mismo trámite CEN que una instalación coordinada: se registrará el ámbito y la contraparte competente. Las categorías y estados propuestos abajo son **internos de Ganttier**, pendientes de correspondencia con el procedimiento externo. El portal del CEN distingue Neomante y el Programa de Mantenimiento Preventivo Mayor; registrar uno no acredita completar el otro. [CEN, plataformas para coordinados](https://www.coordinador.cl/ingreso-coordinados/).

Las rutas de «Dónde» son relativas a `Ganttier/`. Los archivos señalados como nuevos son propuestas futuras. Toda extensión persistente deberá integrar `domain.py`, `schema.sql`, migraciones en `db.py` y `repo.py` según corresponda, sin romper importaciones antiguas. La aceptación de una nueva restricción deberá pasar por la verificación independiente existente, además de la heurística. Para 100 000 ocurrencias se trabajará con relaciones indexadas, consultas por lotes y carga del detalle bajo demanda; no con adjuntos o series históricas replicados en cada ocurrencia.

### P2-01 Catálogo de activos eléctricos
- Problema que resuelve: Dos tareas nombran al mismo transformador como «TR1» y «TR-1», mientras un equipo de reemplazo hereda por error su historial en S/E Cordillera. El texto libre no distingue posición eléctrica de equipo físico.
- Propuesta: Crear activos con ID estable, tipo, TAG, propietario, fabricante, modelo, serie, puesta en servicio y retiro; jerarquías S/E–nivel de tensión–paño–equipo y línea–circuito–tramo–estructura.
  Separar posición funcional de equipo instalado, con fechas de montaje/retiro, y admitir varias tensiones nominales para transformadores.
  Vincular tareas y ocurrencias mediante `asset_id`; migrar textos a candidatos con revisión de ambigüedades e incorporar hoja Activos al intercambio Excel.
- Dónde: `backend/ganttier/domain.py`, `schema.sql`, `db.py`, `repo.py`; nuevos `maintenance/assets.py` y `api/assets.py`; `gui/pages/maintenance.py`; `excel_io/schema.py`, `importer.py`, `exporter.py`, `templates.py`; `plantillas/Plantilla_Mantenimiento.xlsx`.
- Valor para el usuario: Alto — evita duplicaciones y atribuciones erróneas y permite acumular condición, fallas y costos del activo correcto.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — conciliación de nombres antiguos, referencias compartidas y separación entre posición y número de serie.
- Criterio de aceptación: Importar dos alias del mismo activo solo los unifica tras resolución explícita, sin perder tareas; sustituir TR-1 conserva dos historiales de equipos y una cronología consultable de la posición.
- Referencias normativas o técnicas: [ISO 55000:2024](https://www.iso.org/standard/83053.html), vocabulario y principios de gestión de activos; no prescribe este esquema de tablas ni constituye certificación.

### P2-02 Prioridad por riesgo trazable
- Problema que resuelve: Una inspección menor y un interruptor con fallas repetidas que alimenta una carga crítica pueden tener la misma prioridad manual en S/E Valle. Nivelar HH no distingue las consecuencias de postergar cada trabajo.
- Propuesta: Incorporar evaluaciones versionadas por activo: consecuencia en seguridad, servicio, ambiente y costo; probabilidad de falla con horizonte, evidencia y confianza.
  Cuando solo haya clases ordinales, mostrar una matriz cualitativa; calcular riesgo esperado como probabilidad × consecuencia únicamente con magnitudes compatibles. Un índice de salud no se convertirá automáticamente en probabilidad.
  Ofrecer un orden de atención y costo de diferimiento explicables, con revisión del ingeniero, reutilizando `priority`; no relajar vencimientos obligatorios ni restricciones operacionales. Depende de P2-01 y puede comenzar con evaluación manual.
- Dónde: nuevo `backend/ganttier/maintenance/risk.py`; `domain.py`, `repo.py`; `maintenance/leveling.py`, `solve.py`; `gui/pages/maintenance.py` y `optimize.py`.
- Valor para el usuario: Alto — hace defendible qué mantenimiento se adelanta o posterga cuando no alcanza la capacidad.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — falsa precisión si se multiplican escalas ordinales o se usan probabilidades sin horizonte y evidencia.
- Criterio de aceptación: Con capacidades y ventanas idénticas, dos evaluaciones de riesgo distintas producen el orden aprobado y explican sus factores; un activo sin evaluación aparece «sin evaluar» y nunca recibe riesgo cero por defecto.
- Referencias normativas o técnicas: [ISO 55000:2024](https://www.iso.org/standard/83053.html), marco de gestión de activos y riesgo. Matriz, ponderaciones y transformación a prioridad son políticas de la empresa, no una fórmula exigida por ISO; calibración local a verificar.

### P2-03 Condición y salud de transformadores
- Problema que resuelve: El plan ya programa muestreos DGA, pero no relaciona el aumento de gases del TR-1 de 220/66 kV con la siguiente inspección. Un avance de 100 % registra que se tomó la muestra, no que el equipo esté sano.
- Propuesta: Guardar muestras y ensayos por activo: DGA por gas y compartimiento, humedad, resultados del aceite y factor de potencia/tan δ de aislamiento, con unidades, fecha, laboratorio, método, temperatura y documento de origen.
  Calcular tendencias entre mediciones comparables y un índice de salud explicable por componentes, con cobertura de datos, versión y reglas aprobadas; distinguir aceite, aislamiento del equipo y cambiador bajo carga.
  Proponer inspección extraordinaria o cambio de intervalo para revisión humana; conservar resultados originales y anclas históricas. Primer alcance: transformadores con aceite mineral, sin diagnóstico automático concluyente.
- Dónde: nuevo `backend/ganttier/maintenance/condition.py`; `domain.py`, `repo.py`, `services.py`; `maintenance/generator.py`; `gui/pages/maintenance.py`; `excel_io/schema.py`, `importer.py`, `exporter.py`, `templates.py` para una hoja de mediciones.
- Valor para el usuario: Alto — permite actuar ante deterioro real y justificar campañas de ensayo con datos, no solo con periodicidad.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Alto — comparabilidad de muestras, unidades y condiciones de ensayo; un índice aparentemente preciso puede ocultar datos faltantes.
- Criterio de aceptación: Dos muestras del mismo compartimiento calculan la tasa por tiempo transcurrido y conservan sus unidades; omitir un ensayo obligatorio del perfil deja el índice incompleto y no declara al activo saludable ni reprograma sin aprobación.
- Referencias normativas o técnicas: [IEC 60599:2022](https://webstore.iec.ch/en/publication/76192), interpretación de gases disueltos/libres en equipos con aceite mineral y aislamiento celulósico; [IEC 60422:2024](https://webstore.iec.ch/en/publication/66421), supervisión y mantenimiento del aceite, que excluye el diagnóstico del equipo mediante DGA. Umbrales, correcciones de factor de potencia/tan δ y perfil de salud: a verificar con método de ensayo, fabricante y edición aplicable; ninguna de estas referencias define por sí sola el índice compuesto propuesto.

### P2-04 Desconexión con alcance eléctrico
- Problema que resuelve: Dos trabajos que empiezan el mismo día en S/E Cordillera se agrupan hoy aunque uno afecte una barra de 220 kV y otro un alimentador de 13,8 kV. A la inversa, trabajos en extremos distintos de una línea pueden compartir una sola indisponibilidad.
- Propuesta: Crear un paquete de intervención con ID persistente, revisión, activos afectados, frontera de aislamiento documentada, empresas y trabajos asociados; usar la agrupación actual solo para sugerir candidatos.
  Permitir varios trabajos por paquete y varias intervenciones relacionadas cuando una tarea necesite aislamientos distintos. Un revisor deberá confirmar compatibilidad eléctrica y de ejecución antes de consolidar.
  Diferenciar desconexión, intervención sin desconexión e indisponibilidad de protección/control; incluir estas últimas en la coordinación aunque `requires_outage=False`. Depende de P2-01 y se integra con horarios de P2-05.
- Dónde: nuevos `backend/ganttier/maintenance/outage_packages.py` y entidades en `domain.py`/`schema.sql`; `repo.py`; `maintenance/leveling.py`; `api/outages.py`, `reports/outages.py`; `gui/pages/maintenance.py`.
- Valor para el usuario: Alto — reduce solicitudes duplicadas y evita presentar coincidencias geográficas como una desconexión técnicamente común.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Alto — cardinalidad trabajo–paquete y ambigüedad entre fronteras físicas, nombres de instalaciones y equipos afectados.
- Criterio de aceptación: Dos paños incompatibles de una S/E no se consolidan automáticamente aunque coincidan instalación y fecha; un paquete confirmado con trabajos en ambos extremos de una línea conserva ID, vínculos y revisión al filtrar o regenerar el Excel.
- Referencias normativas o técnicas: [CEN, manual Neomante publicado](https://www.coordinador.cl/wp-content/uploads/2023/11/B43-DIN-GUA12-Guia-Usuario-Plataforma-Neomante.pdf), que identifica módulos de desconexión/intervención y fallas. El documento enlazado desarrolla el módulo de fallas: reglas vigentes para consolidar solicitudes y coordinar intervenciones, a verificar con el CEN y los propietarios; no se deducen de este manual.

### P2-05 Ventanas horarias y devolución al servicio
- Problema que resuelve: Una ventana autorizada de 08:00 a 14:00 para la LT Valle de 110 kV no equivale a un día disponible para trabajar. Las maniobras y ensayos de retorno pueden consumir parte sustancial de esa ventana.
- Propuesta: Agregar intervalos horarios solicitados, autorizados y reales al paquete: preparación/maniobras, entrega, ejecución, pruebas y devolución, con zona horaria de la instalación y almacenamiento UTC.
  Desarrollar programación y verificación intradía para los trabajos del paquete, con ocupación simultánea de cuadrillas y equipos; conservar el motor diario para el horizonte general y reservar en él la capacidad correspondiente sin doble conteo.
  Admitir desconexión continua o ventanas diarias separadas y prever tiempo de devolución anticipada. El retorno real al servicio será un evento operacional distinto del fin de la OT.
- Dónde: nuevo `backend/ganttier/maintenance/outage_windows.py`; `domain.py`, `repo.py`; `maintenance/leveling.py`, `solve.py`, `engine/verify.py`; `reports/outages.py`, `api/outages.py`; `gui/pages/maintenance.py`.
- Valor para el usuario: Alto — evita programas imposibles dentro de la ventana concedida y mide horas reales de indisponibilidad.
- Esfuerzo: XL.
- Riesgo técnico: Alto — convivencia de calendarios diarios e intervalos horarios, cambios de hora y reservas de múltiples recursos.
- Criterio de aceptación: Una ventana de seis horas con dos horas de maniobras admite como máximo cuatro horas de ejecución secuencial; dos ventanas diarias con reposición nocturna no se contabilizan como una desconexión continua y su ida/vuelta Excel conserva instantes y zona.
- Referencias normativas o técnicas: [CEN, EAF 223/2025](https://www.coordinador.cl/wp-content/uploads/2025/06/EAF-223-2025.pdf) contiene registros operacionales con trabajos diarios y desconexiones continuas; es evidencia técnica de la distinción, no una regla de plazos. Condiciones de devolución y procedimiento vigente de coordinación: a verificar para cada instalación.

### P2-06 Expediente y estados de solicitudes
- Problema que resuelve: El Excel de S/E Cordillera puede seguir mostrando una fecha que el CEN observó o rechazó. El campo de responsables asignados tampoco identifica necesariamente al solicitante ni al interlocutor del centro de control.
- Propuesta: Incorporar expediente enlazado al paquete con folio externo, contraparte, responsable formal, adjuntos/referencias, historial de revisiones y estados internos: borrador, presentado, observado, autorizado, rechazado, cancelado y cerrado.
  Separar aprobación interna, respuesta externa y habilitación operacional para iniciar. Guardar fechas propuestas/autorizadas y congelar la revisión presentada; cualquier cambio de alcance o horario exige reconciliar su autorización.
  Añadir hitos y alertas configurables con fuente, versión, calendario y regla de cómputo; distinguir compromisos del PMPM de solicitudes específicas. Primera entrega con carga manual e intercambio Excel, sin envío automático ni plazos oficiales precargados sin validar.
- Dónde: nuevos `backend/ganttier/maintenance/requests.py` y `api/maintenance_requests.py`; `domain.py`, `repo.py`; `api/outages.py`, `reports/outages.py`; `gui/pages/maintenance.py` y `reports.py`.
- Valor para el usuario: Alto — permite saber qué trabajos están coordinados, qué falta y qué cambió después de presentar una solicitud.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — estados externos cambiantes, revisiones concurrentes y confusión entre un registro local y una autorización real.
- Criterio de aceptación: Exportar o asignar personal no cambia un expediente a autorizado; modificar el horario de una revisión autorizada crea una nueva revisión pendiente y mantiene intactos folio, respuesta y fechas anteriores.
- Referencias normativas o técnicas: [CEN, plataformas Neomante y PMPM](https://www.coordinador.cl/ingreso-coordinados/). Procedimiento aplicable, catálogo externo de estados, plazos, excepciones y versión: a verificar antes de habilitar reglas regulatorias. No se presupone que todas las redes de distribución tramiten sus trabajos por Neomante.

### P2-07 Incompatibilidades operacionales y revisión N-1
- Problema que resuelve: Hay cuadrillas suficientes para intervenir ambos circuitos de una LT de 220 kV, pero su salida conjunta puede dejar sin respaldo a la zona. También puede ser incompatible probar simultáneamente protecciones redundantes sin abrir ningún interruptor.
- Propuesta: Crear reglas aprobadas por operación para pares o conjuntos de activos: exclusión simultánea, mínimo de circuitos/protecciones disponibles y restricciones temporales por escenario, incluidas indisponibilidades externas conocidas.
  Evaluar paquetes durante la búsqueda y mediante un verificador independiente; explicar activo, regla y período conflictivo. Adjuntar estudio N-1, escenario de red, vigencia y aprobador cuando corresponda.
  Presentar «sin conflicto en las reglas cargadas» o «evaluación incompleta»; comprobar exclusiones no acredita N-1, que puede requerir flujo de carga, estabilidad y evaluación del Coordinador. Primer alcance sin motor eléctrico propio.
- Dónde: nuevo `backend/ganttier/maintenance/operational_constraints.py`; `domain.py`, `repo.py`; `maintenance/leveling.py`, `solve.py`, `engine/verify.py`; `gui/pages/maintenance.py` y `optimize.py`.
- Valor para el usuario: Alto — detecta incompatibilidades que la nivelación de personas y las ventanas por tarea no pueden representar.
- Esfuerzo: XL.
- Riesgo técnico: Alto — reglas incompletas, cambios del estado de red y escala combinatoria; riesgo de confundir una revisión de agenda con un estudio eléctrico.
- Criterio de aceptación: Con una regla que exige un circuito disponible, dos paquetes solapados sobre C1 y C2 se rechazan aunque sobre personal; un estudio vencido o de otro escenario deja la evaluación pendiente, sin mostrar «cumple N-1».
- Referencias normativas o técnicas: [CNE, NTSyCS publicada en septiembre de 2020](https://www.cne.cl/wp-content/uploads/2020/09/NTSyCS-Sept20.pdf) y [catálogo oficial de normas y modificaciones](https://www.cne.cl/normativas/electrica/normas-tecnicas/). Criterio N-1, excepciones, condiciones operacionales y versión consolidada vigente: a verificar; no se extiende automáticamente la misma exigencia a todo alimentador radial de distribución.

### P2-08 Consignaciones y cierre operacional
- Problema que resuelve: Terminar las pruebas de un interruptor en S/E Valle no prueba que se retiraron todas las tierras temporales ni que todos los equipos de trabajo devolvieron su zona. Marcar 100 % no debe confundirse con disponibilidad eléctrica.
- Propuesta: Registrar consignaciones asociadas a paquetes, con procedimiento y revisión, puntos de aislamiento identificados, responsables de operación/trabajo, entrega de zona y registro de las verificaciones exigidas por el procedimiento aprobado.
  Mantener permisos separados por frente, evidencias de devolución y cierre conjunto antes de registrar la liberación operacional; añadir requisitos de habilitación por rol y vigencia, más específicos que una habilidad genérica.
  Implementar trazabilidad documental y controles de transición, sin generar maniobras ni accionar equipos. La interfaz distinguirá siempre trabajo terminado, zona devuelta y equipo repuesto al servicio.
- Dónde: nuevos `backend/ganttier/maintenance/permits.py` y `api/permits.py`; `domain.py`, `repo.py`; `gui/pages/maintenance.py`, `resources.py`, `control.py`; integración con `maintenance/outage_packages.py` propuesto.
- Valor para el usuario: Alto — hace visibles los frentes pendientes y las condiciones documentales para entregar y recuperar una instalación.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Alto — integridad de estados, identificación de responsables y posibilidad de que el usuario interprete una casilla como verificación física.
- Criterio de aceptación: Con dos frentes abiertos, la devolución de uno no permite cerrar la consignación; una habilitación vencida impide asignar el rol exigido y el cierre técnico de la OT no registra por sí solo la reposición eléctrica.
- Referencias normativas o técnicas: [SEC, RPTD N°15](https://www.sec.cl/sitio-web/wp-content/uploads/2020/09/Pliego-T%C3%A9cnico-Normativo-RPTD-N%C2%B015-Operaci%C3%B3n-y-mantenimiento.pdf), seguridad en operación y mantenimiento. Secuencia de consignación, perfiles de habilitación, evidencias y requisitos concretos: a verificar con el procedimiento vigente del propietario y la coordinación operacional aplicable.

### P2-09 Fallas y órdenes correctivas
- Problema que resuelve: Tres reparaciones por fuga en el mismo interruptor de S/E Cordillera aparecen como actividades aisladas. No se puede distinguir repetición de una falla, nueva avería o reparación provisional pendiente de solución definitiva.
- Propuesta: Incorporar eventos de falla por activo con detección, pérdida de función, recuperación, modo, causa presunta/confirmada, consecuencias y vínculo opcional al informe externo.
  Crear OT correctivas vinculadas al evento y actividades existentes, con diagnóstico, acciones, repuestos, pruebas de salida y validación del cierre; un evento puede tener varias OT sin multiplicar el número de fallas.
  Para urgencias, comparar un escenario de reprogramación y mostrar preventivos desplazados, riesgo y vencimientos; reutilizar reservas de carga fija y actividades ordinarias ya soportadas. No reiniciar automáticamente la recurrencia preventiva al cerrar una reparación.
- Dónde: nuevos `backend/ganttier/maintenance/failures.py`, `work_orders.py` y `api/work_orders.py`; `domain.py`, `repo.py`, `services.py`, `solve.py`; `gui/pages/maintenance.py` y `control.py`.
- Valor para el usuario: Alto — permite controlar reparaciones pendientes, detectar reincidencias y cerrar técnicamente el mantenimiento.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — relación muchos-a-muchos entre evento, OT y actividad; cierre administrativo distinto de recuperación funcional.
- Criterio de aceptación: Una falla con tres OT cuenta como un evento y permite distinguir reparación provisional de definitiva; introducir una OT urgente conserva avances y trabajos bloqueados y enumera los preventivos desplazados sin borrar sus vencimientos.
- Referencias normativas o técnicas: [CEN, manual Neomante — módulo Informe de Fallas](https://www.coordinador.cl/wp-content/uploads/2023/11/B43-DIN-GUA12-Guia-Usuario-Plataforma-Neomante.pdf), que contempla fallas de S/E y líneas y causas presuntas/definitivas. Campos obligatorios, códigos y plazos de reporte vigentes: a verificar; la OT local no sustituye el informe al CEN.

### P2-10 Repuestos y medios de prueba reservables
- Problema que resuelve: Dos S/E tienen cuadrillas disponibles, pero ambas necesitan el único equipo de inyección primaria. Otro trabajo llega a su desconexión sin el kit compatible con el modelo de interruptor.
- Propuesta: Extender los recursos ya existentes con reservas de equipos reutilizables por intervalo, traslado/preparación y certificado de calibración o inspección cuando corresponda: camión grúa, inyección, microhmímetro y tratamiento de aceite.
  Añadir existencias de repuestos por código, unidad y ubicación, compatibilidad aprobada con activos, reservas, consumo real y fecha de reposición; distinguir disponibilidad actual de una compra pendiente.
  Exigir conjuntamente cuadrilla, equipo apto y repuestos para declarar una OT lista, sin convertir equipos o materiales en HH laborales. Alcance acotado a reservas y movimientos, no compras ni contabilidad de un ERP.
- Dónde: nuevo `backend/ganttier/maintenance/logistics.py`; `domain.py`, `schema.sql`, `repo.py`; `maintenance/leveling.py`, `solve.py`, `engine/verify.py`; `gui/pages/resources.py` y `maintenance.py`; `excel_io/schema.py` y `templates.py`.
- Valor para el usuario: Alto — evita perder ventanas de desconexión por recursos físicos que hoy no se comprueban junto con el personal.
- Esfuerzo: XL.
- Riesgo técnico: Alto — recursos renovables frente a existencias consumibles, unidades y reservas compartidas entre proyectos.
- Criterio de aceptación: Dos OT de proyectos distintos no reservan el mismo equipo en intervalos incompatibles, incluido traslado; consumir un kit libera su reserva y descuenta una sola unidad, mientras cancelar otra reserva no descuenta stock.
- Referencias normativas o técnicas: [ISO 55000:2024](https://www.iso.org/standard/83053.html), gestión del ciclo de vida como marco general. Compatibilidad de repuestos, aptitud del camión grúa y vigencia/metrología de equipos de prueba: documentación del fabricante y procedimientos de la empresa, a verificar por equipo; no se propone un intervalo universal de calibración.

### P2-11 Obligaciones y vencimientos acreditables
- Problema que resuelve: Una tolerancia de programación puede permitir mover una prueba de S/E Valle más allá de una fecha exigible. El sistema no distingue un requisito normativo de una recomendación del fabricante o una meta interna.
- Propuesta: Crear un registro de obligaciones con fuente exacta, edición/resolución, cláusula verificada, ámbito, responsable de validación, vigencia, regla de vencimiento y evidencia requerida; asociarlo a activos y tareas.
  Separar fecha objetivo, tolerancia de planificación y fecha límite exigible. Solo una obligación validada alimentará una restricción dura y un indicador de cumplimiento; lo pendiente quedará «a verificar».
  Versionar cambios y excepciones documentadas sin sobrescribir el historial ni permitir que el optimizador conceda prórrogas. Preparar una vista de obligaciones aplicables para SEC/SGIIE y compromisos CEN, sin certificar conformidad global.
- Dónde: nuevo `backend/ganttier/maintenance/compliance.py`; `domain.py`, `repo.py`; `maintenance/generator.py`, `solve.py`, `engine/verify.py`; `gui/pages/maintenance.py`; `reports/builder.py` y `reports/xlsx.py`.
- Valor para el usuario: Alto — permite demostrar por qué vence una tarea y evita confundir una reprogramación factible con cumplimiento de una obligación.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — aplicabilidad y vigencia normativa, cálculo de fechas y conservación de la evidencia de cada versión.
- Criterio de aceptación: En un caso ficticio con límite validado al día 10, una tolerancia que llega al día 15 no permite aplicar ejecución al día 12; cambiar la versión de una obligación conserva los vencimientos y evidencias históricos y recalcula solo el alcance afectado.
- Referencias normativas o técnicas: [SEC, RPTD N°15](https://www.sec.cl/sitio-web/wp-content/uploads/2020/09/Pliego-T%C3%A9cnico-Normativo-RPTD-N%C2%B015-Operaci%C3%B3n-y-mantenimiento.pdf), [RPTD N°17 y anexo de indicadores SGIIE](https://www.sec.cl/sitio-web/wp-content/uploads/2023/03/RE-16380-Diario-OFICIAL.pdf), [SEC, información sobre reportabilidad SGIIE](https://www.sec.cl/capacitacion-sgiie/?view_full_site=true). Versiones, resoluciones posteriores, cláusulas, plazos y aplicabilidad por segmento: a verificar; no se fija aquí ningún plazo de mantenimiento obligatorio.

### P2-12 Indicadores de mantenimiento y confiabilidad
- Problema que resuelve: Un SPI favorable no demuestra que se atendieron los activos críticos ni que bajaron las fallas. Además, tres OT bajo la misma desconexión de S/E Cordillera no representan tres veces la indisponibilidad del activo.
- Propuesta: Añadir cumplimiento por vencimiento original y por límite exigible, backlog en OT y HH remanentes por antigüedad/criticidad y causas de diferimiento, manteniendo el denominador del plan comprometido.
  Con P2-09 y horas operacionales, calcular MTBF observado como horas de operación conocidas/fallas funcionales y disponibilidad del activo como tiempo disponible/tiempo observable; separar indisponibilidad programada y forzada y unir intervalos solapados.
  Mostrar cobertura y «sin datos suficientes» si faltan horas/eventos; cero fallas no implica MTBF infinito. Tratar estos KPI como internos hasta validar su correspondencia con indicadores regulatorios; la disponibilidad de un equipo no equivale a continuidad de suministro a clientes.
- Dónde: nuevo `backend/ganttier/control/maintenance_kpis.py`; `services.py`, `api/control.py`; `reports/builder.py`, `reports/xlsx.py`; `gui/pages/control.py` y `maintenance.py`.
- Valor para el usuario: Alto — mide deuda de mantenimiento y confiabilidad con una base auditable, complementando EVM y desempeño laboral existentes.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — sesgo por historial incompleto, denominadores cambiantes y doble conteo de intervalos o eventos.
- Criterio de aceptación: Con 1 000 horas de operación observadas y dos fallas funcionales, MTBF = 500 h, aunque existan cinco OT; en 100 horas observables, indisponibilidades de 10 h y 5 h con 2 h de solapamiento producen 13 h indisponibles y 87 % de disponibilidad.
- Referencias normativas o técnicas: [SEC, anexo técnico de indicadores del RPTD N°17, febrero de 2023](https://www.sec.cl/sitio-web/wp-content/uploads/2023/03/Anexo-Tecnico-RPTD-17-Indicadores-final-V-23-02-2023-1.pdf). Versión vigente, fórmulas exigidas, denominadores y segmentación de reportes: a verificar; las fórmulas de aceptación anteriores son definiciones internas explícitas, no una transcripción del anexo.

### P2-13 Registro técnico en terreno sin conexión
- Problema que resuelve: Una brigada inspecciona estructuras de la LT Valle sin conectividad y transcribe después fotos y mediciones en notas libres. Es fácil asociar el resultado al vano equivocado o duplicar HH al reingresar la información.
- Propuesta: Preparar paquetes de OT para un cliente de terreno sin conexión, con QR/ID del activo, pauta versionada, mediciones con unidades, fotos, hallazgos y tiempos reales; primer alcance para dispositivos Windows compatibles con PySide6.
  Intercambiar paquetes exportables con identificadores únicos, revisión base, autor y fecha; importar idempotentemente y resolver conflictos de forma visible, manteniendo adjuntos por referencia y huella, no en cada ocurrencia.
  Separar trabajo ejecutado de aceptación técnica: las mediciones incompletas generan pendiente de revisión. Reutilizar `ProgressEntry` para avance y HH, sin convertir la autoría local en firma electrónica certificada.
- Dónde: nuevos `backend/ganttier/maintenance/field_io.py` y `gui/pages/field.py`; `domain.py`, `repo.py`; `api/files.py`, `api/control.py`, `control/progress.py`; `gui/pages/maintenance.py`.
- Valor para el usuario: Medio — reduce transcripción y mejora evidencia, pero rinde plenamente después de disponer de activos y OT estables.
- Esfuerzo: XL.
- Riesgo técnico: Alto — resolución de conflictos sin conexión, identidad local, volumen de fotografías y compatibilidad del dispositivo de terreno.
- Criterio de aceptación: Reimportar dos veces el mismo paquete no duplica HH, mediciones ni fotos; dos revisiones incompatibles sobre la misma OT conservan ambos aportes y exigen resolución, sin sobrescribir el aceptado.
- Referencias normativas o técnicas: Procedimientos de inspección y aceptación del propietario y fabricante, a verificar para cada pauta. Diseño técnico de intercambio idempotente y control de versiones; no se atribuye una exigencia de QR, aplicación móvil o firma certificada a la normativa chilena.

### P2-14 Hallazgos de líneas y campañas focalizadas
- Problema que resuelve: El lavado periódico de toda la LT Cordillera trata igual una sección costera contaminada y otra interior. Un hallazgo de vegetación, erosión o aislador dañado puede perderse al quedar anotado solo bajo el nombre general de la línea.
- Propuesta: Registrar hallazgos por circuito, vano/estructura, ubicación y condición observada, con severidad revisada, fotos y seguimiento; aprovechar la jerarquía P2-01 y las OT P2-09.
  Construir campañas por tramos afectados y accesos compartidos, con restricciones de acceso, temporada, permisos prediales y condiciones de ejecución aprobadas para lavado o despeje.
  Mostrar qué hallazgos cubre cada campaña y cuáles quedan fuera; distinguir tareas con/sin desconexión y coordinar circuitos vecinos cuando aplique. Primer alcance tabular con coordenadas y agrupación, sin GIS completo ni umbrales meteorológicos universales.
- Dónde: nuevo `backend/ganttier/maintenance/line_campaigns.py`; `domain.py`, `repo.py`; `maintenance/leveling.py`, `solve.py`; `gui/pages/maintenance.py`; `excel_io/schema.py`, `importer.py`, `exporter.py` para hallazgos.
- Valor para el usuario: Alto — concentra recursos en tramos deteriorados y deja trazabilidad de riesgos frecuentes de líneas de transmisión y distribución.
- Esfuerzo: L (≤1 mes).
- Riesgo técnico: Medio — localización y deduplicación de hallazgos, compatibilidad de campañas y calidad de restricciones cargadas.
- Criterio de aceptación: Una campaña seleccionada para estructuras 20–30 no cierra hallazgos de la estructura 31 ni del circuito vecino; un acceso restringido impide declarar lista la OT afectada y conserva los hallazgos pendientes.
- Referencias normativas o técnicas: [SEC, DS N°109 y listado de pliegos RPTD](https://www.sec.cl/transparencia/docs2018/decreto_109.pdf), marco de seguridad de instalaciones de producción, transporte y distribución. Disposiciones de franja/vegetación, distancias, permisos y procedimientos de lavado/despeje: a verificar según tensión, emplazamiento y versión aplicable; no se fijan distancias ni frecuencias universales.

## Qué se descarta de esta etapa

Se descartan un nuevo Gantt o dashboard de EVM, otra agrupación por instalación/fecha y otra plantilla genérica de desconexiones: duplican capacidades existentes. También se excluyen un ERP de compras, integración SCADA en tiempo real, un GIS corporativo y un simulador de red propio: amplían excesivamente el producto antes de resolver identidad, expedientes y calidad del dato. La predicción de fallas con IA carece de base defendible sin historial suficiente; primero deben existir P2-03 y P2-09. El envío automático al CEN se pospone hasta validar interfaz, permisos, procedimiento y conciliación de respuestas; exportar un Excel no equivale a ingresar una solicitud.

Los estándares internacionales son referencias técnicas según su ámbito y adopción aplicable, no obligaciones chilenas universales por aparecer en esta propuesta. No se incorpora una cita genérica a «CIGRÉ» ni un plazo supuestamente oficial sin documento identificable.

## Tabla resumen

| ID | Título | Valor | Esfuerzo | Riesgo |
|---|---|---|---|---|
| P2-01 | Catálogo de activos eléctricos | Alto | L | Medio |
| P2-02 | Prioridad por riesgo trazable | Alto | L | Medio |
| P2-03 | Condición y salud de transformadores | Alto | L | Alto |
| P2-04 | Desconexión con alcance eléctrico | Alto | L | Alto |
| P2-05 | Ventanas horarias y devolución al servicio | Alto | XL | Alto |
| P2-06 | Expediente y estados de solicitudes | Alto | L | Medio |
| P2-07 | Incompatibilidades operacionales y revisión N-1 | Alto | XL | Alto |
| P2-08 | Consignaciones y cierre operacional | Alto | L | Alto |
| P2-09 | Fallas y órdenes correctivas | Alto | L | Medio |
| P2-10 | Repuestos y medios de prueba reservables | Alto | XL | Alto |
| P2-11 | Obligaciones y vencimientos acreditables | Alto | L | Medio |
| P2-12 | Indicadores de mantenimiento y confiabilidad | Alto | L | Medio |
| P2-13 | Registro técnico en terreno sin conexión | Medio | XL | Alto |
| P2-14 | Hallazgos de líneas y campañas focalizadas | Alto | L | Medio |

S = ≤2 días; M = ≤1 semana; L = ≤1 mes; XL = más de un mes para el alcance descrito. No se ofrecen mejoras S/M artificiales: las seleccionadas requieren trazabilidad persistente e integración. Las estimaciones no son aditivas sin ajustar dependencias y componentes compartidos.

## Top-5 recomendado

1. **P2-01 — Catálogo de activos eléctricos.** Primera inversión: evita que condición, fallas y desconexiones se construyan sobre nombres ambiguos. Empezar por una S/E y una línea reales, reconciliadas por su responsable, antes de migrar el resto.
2. **P2-04 — Desconexión con alcance eléctrico.** Corrige la limitación más importante del programa de desconexiones actual: compartir instalación y fecha no basta para compartir aislamiento. Entrega valor inmediato a la preparación de trabajos y establece la entidad común para horarios, solicitudes y consignaciones.
3. **P2-06 — Expediente y estados de solicitudes.** Convierte el Excel en una salida trazable de un proceso controlado. Puede comenzar con fechas conocidas y carga manual; necesita P2-04 y se amplía con P2-05 cuando se aborde verificación intradía.
4. **P2-09 — Fallas y órdenes correctivas.** Cierra el ciclo entre planificación y resultados técnicos, y permite gestionar urgencias con sus efectos visibles. Es la fuente necesaria para backlog confiable, reincidencia y MTBF, aprovechando programación y avance ya implementados.
5. **P2-03 — Condición y salud de transformadores.** Piloto acotado con resultados de laboratorio existentes: alta utilidad sobre activos costosos y críticos sin esperar un historial estadístico extenso. Alimenta decisiones de inspección y posteriormente P2-02; exige revisión técnica del perfil y umbrales.

Este orden prioriza valor y dependencias para un planificador local. Los paquetes seguirán requiriendo revisión operacional humana: P2-04/P2-06 no habilitan por sí solos la ejecución. P2-05, P2-07 y P2-08 deben acompañar cualquier ampliación que pretenda validar horarios, compatibilidad de red o cierre operacional dentro del producto. P2-11 se adelanta si el usuario ya tiene una matriz de obligaciones verificada o una auditoría próxima; P2-14 sube de prioridad si predominan líneas sobre subestaciones en su cartera.
