# Formato personal y avance (I4)

API aditiva para proyectos existentes. GET `/templates/personal` y `/templates/avance` descargan libros comentados. POST `/projects/{pid}/import/personal` y `/projects/{pid}/import/avance` reciben multipart `file`; `dry_run=true` por defecto. Personal admite `modo=merge|replace`; avance admite `allow_regressions=false`. Estos parámetros son query parameters. Las rutas usan la conexión y autenticación del servidor existente.

## Contrato

Encabezados en primera fila, reordenables e insensibles a tildes/mayúsculas. Referencias por nombre/código exacto. Fechas Excel o formatos chilenos; números decimales con coma. Fórmulas y errores Excel se rechazan. Límites: 200 000 filas, 200 columnas, 512 MiB ZIP descomprimido. La respuesta incluye `created`, `updated`, `ignored`, `deactivated`, `errors`, `warnings`, `source_sha256`, `elapsed_s`, `backup`, `applied`. Errores con `sheet`, `row`, `column`, `message`.

Personal:

| Columna | Semántica |
|---|---|
| Nombre | Identidad local única, obligatoria |
| Rol / Habilidades | Rol se incorpora a la lista de habilidades; habilidades separadas por coma |
| Calendario | Calendario existente o nombre definido en feriados propios |
| Turno | 5x2 (lunes a viernes), 7x7, 14x14 o personalizado |
| Patron / Ancla | NxM para personalizado y fecha inicial del bloque; ancla obligatoria para turnos móviles |
| Horas_Dia | Capacidad por persona, >0 y <=24; nueva persona: 8 |
| Costo_HH / Costo_HH_Extra | Costos no negativos en moneda del proyecto; nuevos: 0 |
| Max_HH_Extra_Dia | Tope diario no negativo; nuevo: 0 |
| Unidades | Capacidad agregada; nuevo: 1; ausencias requieren 1 |

Ausencias: Persona, Desde, Hasta, Tipo (vacaciones/licencia/permiso/capacitación), Horas_Disponibles. Esta última expresa disponibilidad restante por día: 0=ausencia completa, 4=cuatro horas disponibles, no cuatro horas a descontar. Se usa `resource_absences.set_absence`, preservando excepciones y aislando calendarios compartidos. Los días de descanso no se habilitan.

Calendarios: Calendario, Fecha, Motivo. Define feriados propios para las personas que referencian ese nombre en Personal; crea copias privadas, sin alterar calendarios globales. No acepta feriados sin una referencia en Personal. Feriados nacionales: CL por defecto del dominio.

Equipos opcional: Equipo, Regimen, Inicio_Ciclo, Integrantes, Horas_Dia, Feriados_Trabajan, Calendario. Usa `teams.save_team`, incluidos NxM y rotación mediante anclas. Solo planes de mantenimiento. No introduce membresía nominal porque el dominio modela integrantes como cantidad.

Merge conserva personas omitidas y campos omitidos de recursos existentes. Replace desactiva trabajadores/cuadrillas locales omitidos; conserva identidades, asignaciones e historia. No borra actividades ni calendarios históricos. Personal activa las personas presentes. Los campos omitidos en recursos nuevos usan los defaults del dominio descritos arriba.

## Avance

Avance: Actividad, Fecha, Porcentaje (0..100), HH_Reales, Costo_Real, Trabajador opcional, Inicio_Real, Fin_Real, Nota, Modo_Porcentaje (acumulado por defecto o periodo). Las HH/costos corresponden al registro, no al acumulado. El porcentaje del período se suma al físico anterior al día. El corte se interpreta inclusivo al fin de jornada; registros posteriores se rechazan. Se heredan inicio real y fin al 100% con las mismas reglas de `api.control.create_progress`; la aplicación llama ese servicio y `sync_progress`.

Avance_Trabajador: Actividad, Fecha, Trabajador, HH_Reales, Nota. Complementa el físico con consumos individuales y hereda el porcentaje vigente. Para una actividad/día con detalle de trabajadores, la fila agregada debe indicar HH cero. No repita claves entre hojas. Todos los porcentajes explícitos de una actividad/día deben coincidir. No se suman porcentajes entre trabajadores.

La clave `(actividad, fecha, trabajador)` identifica un registro: repetición exacta se ignora y corrección actualiza el registro mediante la API de control dentro de una transacción. Un historial existente con varias filas para la misma clave se rechaza como ambiguo. Los retrocesos se rechazan salvo `allow_regressions=true`, que los conserva con advertencia.

## Atomicidad y respaldo

Se valida antes de aplicar. Personal simula mediante SAVEPOINT revertido para reutilizar exactamente las reglas de ausencias y equipos; avance valida en memoria sin escribir. Si hay errores, no se aplica ninguna fila. Aplicar requiere una conexión sin transacción externa y un respaldo SQLite verificado con `backups.create_backup`; un fallo de respaldo bloquea la escritura (también bases en memoria). Se revalida bajo reserva de escritura y se aplica en una transacción exterior. Un fallo posterior revierte recursos, calendarios, avances y sincronización. La cache de control conserva su mecanismo nativo de invalidación.

## Reproducción

Desde app/backend, con el intérprete indicado por LEEME_AGENTES y `PYTHONPATH=.`:

`python -B ../ejemplos/generar_formato_personal_avance.py`

Genera dos plantillas y cuatro ejemplos ficticios (4 y 5 000 personas/actividades). Avance grande contiene 5 000 físicos y 5 000 consumos, requiere códigos SE-00001..SE-05000 y trabajadores Trabajador 00001..05000 ya existentes. Los ejemplos no crean proyectos ni actividades. Los resultados de pruebas y rendimiento reales están en agentes/I4/INFORME.md.

La integración da prioridad a las dos rutas estáticas de plantilla antes del comodín histórico, desde `importacion_extra.install`. La única modificación de `api/__init__.py` es la línea include_router autorizada. No incluye CLI ni GUI.
