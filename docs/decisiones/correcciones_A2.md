# Correcciones de control y persistencia A2

Fecha: 3 de octubre de 2026.

- A2-01: solo regresión del HTTP 409 al eliminar una tarea periódica con avance;
  se conserva íntegra la protección, eliminación forzada y auditoría anteriores.
- A2-06: el último registro por fecha e ID resuelve la reapertura; un avance
  menor de 100 sin fin real invalida el cierre anterior.
- A2-02: Excel conserva la diferencia entre ausencia y valor explícito.
  Los valores iniciales se aplican al crear, no al preparar un merge;
  también se conserva el padre WBS cuando su referencia está ausente.
- A2-05: el parser rechaza NULL incompatible con el dominio; el repositorio
  rechaza números obligatorios nulos y valores no finitos antes de persistir.
- A2-03/A2-11: la clave actividad/fecha/trabajador actualiza sin cambiar ID.
  Varias filas con esa clave se rechazan. Sin identidad estable, un consumo
  idéntico que pasa de sin trabajador a identificado, o viceversa, se rechaza
  como ambiguo: debe corregirse el registro existente, sin sumar otro consumo.
- A2-07: solo sin ningún registro de avance, AC usa actual_hours/actual_cost
  de la ficha, fechados en actual_start o, si falta, en el corte consultado.
  Antes del inicio no hay consumo; cualquier historial, incluso futuro,
  desactiva este respaldo. El consumo sin trabajador usa las cuotas de personal.
- A2-04: BEGIN IMMEDIATE precede la lectura y validación de actualizaciones.
  Dentro de una transacción se reserva escritura sin modificar datos y se usa
  SAVEPOINT; una operación anidada nunca confirma la transacción del llamador.
- A2-08/A2-13: PV, proyección y desempeño agrupan por calendario efectivo,
  usan máscaras laborables y suman; el corte incluye toda su jornada.
  Hitos e intervalos sin jornadas concentran el peso al fin; BAC queda exacto.
  Las líneas base usan sus fechas/presupuestos y el calendario efectivo actual:
  el esquema existente no conserva versiones históricas de calendarios.
- A2-12: equipos/materiales no participan del reparto de HH entre personal;
  la demanda genérica mantiene su cuota sin adjudicarse a trabajadores.
- A2-09: mismo nombre exige igual definición y excepciones, o se rechaza.
  El calendario de plantilla se llama «Chile 5x2 · ventana de ejemplo» porque
  incluye una excepción y difiere del «Chile 5x2» de los libros de ejemplo.
- A2-10: desde la última emisión se recuperan períodos cerrados, con máximo
  de 12 por tipo/proyecto/ciclo; se reintentan formatos antiguos incompletos.
  La primera ejecución emite solo el último período elegible de cada tipo.
- A2-14: rollback de metadatos retira los archivos nuevos; el render fallido
  también limpia sus archivos parciales. Una terminación abrupta del proceso
  o un fallo del sistema de archivos al limpiar siguen requiriendo revisión.
- A2-15: reagrupar la curva Excel divide el grupo al corte y conserva su ancla.

No se modifica el esquema ni se requiere migración para estas correcciones.
Endurecimiento futuro opcional: sanear NULL heredados y reconstruir las tablas
con NOT NULL en números obligatorios; no se aplicó ninguna migración.
Se preservan as_of, el cierre de período D4 y el respaldo previo al reemplazo.
La verificación de escala exigió dos ajustes auxiliares: JSONResponse directo
para columnas ya serializables y horizon_end en la última tarea recortada de
la demo, para no recrear ocurrencias descartadas. No se modificó el motor.

Regresiones: backend/tests/control/test_auditoria_a2.py.
Pruebas anteriores ajustadas: fixture sin consumo (999 → 0), reapertura (EV=20),
doble EVM con calendarios, gráfico con corte y referencia al calendario de plantilla.
Verificación: seis carpetas solicitadas, con Python del proyecto, importlib,
sin caché y con temporales en _encargos/a2_correcciones, fuera de backend.
Resultado final: pendiente de la ejecución completa.
