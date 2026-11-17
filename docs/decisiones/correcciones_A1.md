# Correcciones del motor A1

Fecha: 2026-10-03. Alcance: motor, adaptador de programación, mantenimiento y calendarios.

## Decisiones

- A1-01: las asignaciones son la fuente de HH cuando existen. Cambiar la cuadrilla
  redistribuye esa demanda; no la sustituye por las HH de la actividad. Se conservan
  también las asignaciones implícitas y las combinaciones de HH y unidades.
- A1-03: el fin del remanente se recalcula desde el siguiente laborable al corte,
  conservando el inicio real; ese fin se usa para reservar capacidad y verificar.
- A1-04: el adaptador resuelve el calendario heredado de cada recurso mediante copias.
- A1-07: demanda implícita = unidades × horas de cada jornada, incluidas excepciones.
  La ruta especial admite cuadrillas y reservas sin asignación concreta; las HH
  explícitas independientes conservan el nivelador vectorizado.
- A1-09: cualquier exceso duro superior a 1e-7 HH produce estado infactible;
  se incluye el exceso de reservas aunque no haya colocaciones forzadas.
- A1-10: preparación, CP-SAT y reintento comparten el plazo solicitado. Agotado
  el presupuesto, no se concede tiempo adicional y se conserva el mejor candidato.
- A1-06: el verificador rechaza hitos no terminados con HH positivas propias o
  asignadas, nombrando su código. Se excluyen las HH históricas de trabajos terminados.
  Se conserva validate_network: la especificación de auditoría exige que devuelva
  [] en esa reproducción. optimize y solve_project quedan bloqueados por la auditoría.
- A1-08: el respaldo chileno de 2029 usa 30 y 31 de marzo para Semana Santa.
- A1-05: después de optimizar, ALAP busca hacia la última fecha factible respetando
  capacidad, calendario y sucesores colocados. Se advierte cuando falta tiempo o
  no se certifica el óptimo global con los demás trabajos fijados. Mantenimiento
  advierte expresamente que no certifica ALAP. No se fija una fecha tardía inviable.
- A1-02: las actividades relacionadas se construyen en orden topológico, con cotas
  de Model.edge_lower/edge_upper y retroceso de fechas cuando un sucesor no cabe.
  La reparación tiene presupuesto; su fracaso no demuestra infactibilidad matemática.
  Los componentes colocados se conservan durante la mejora de tareas independientes.
  La selección de cuadrilla es heurística; no se enumeran todas sus combinaciones.
  El verificador independiente decide la factibilidad final.

## Archivos

Modificados: solve.py, calendars.py, engine/verify.py, engine/optimize.py,
maintenance/leveling.py. Nuevos: tests/solver/test_auditoria_a1.py y este documento.
Las rutas especializadas son clases internas de leveling.py. Se conservaron SAVEPOINT,
el campo infeasibility y la eliminación del tope de 19 segundos.

## Verificación

- Inicial: auditoría 11 fallos y 25 aprobadas (1,35 s).
- Auditoría original: 36 aprobadas; regresiones permanentes: 77 aprobadas.
- Ambas juntas: 113 aprobadas en 3,02 s.
- Rendimiento 100 000: 3,131 → 3,376 s (+7,8 %); cuadrillas: 2,748 → 2,722 s.
- La batería completa y la prueba lenta HTTP se ejecutan desde backend con el
  Python del proyecto, sin escribir bytecode y con temporales dentro del repositorio.
- HTTP slow de mantenimiento: 44,61 → 49,80 s (+11,6 %); falla antes y después
  porque devuelve 100 003 actividades y espera 100 000. No queda certificado el
  límite del 10 % en esa prueba integral; sí se cumple en el nivelador directo.
- Los archivos de importación, servicios, control y KPI permanecen fuera del encargo.
