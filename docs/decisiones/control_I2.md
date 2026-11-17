# Control I2: cumplimiento preventivo y alertas

- Implementación exclusiva de backend; sin cambios de esquema ni GUI.
- `maintenance.kpis.kpis(conn, pid, start=None, end=None, as_of=None)` devuelve dict.
- Período inclusivo por `due_date` original; por defecto inicio del proyecto–corte.
- `end` filtra vencimientos, no modifica el corte; vencidas incluye el día del corte.
- Población: actividades con `periodic_task_id` y `due_date`, incluso tareas inactivas.
- Denominador: todas las ocurrencias del período vencidas al corte, ejecutadas o no.
- Tolerancia real del esquema: `periodic_tasks.tol_after_days`, en días corridos.
- Se admite `tolerance_days` como alternativa; sin campo/tarea se utiliza cero.
- El avance se obtiene con `services.build_activity_views`, respetando registros al corte.
- Terminación: avance 100 % o fecha real de término no posterior al corte.
- Un término real futuro no demuestra terminación al corte, aunque figure 100 %.
- Para puntualidad se exige fecha real explícita; no se inventa con la fecha del registro.
- Si hay cierres sin fecha real: `on_time_pct=null`, `on_time_status="sin datos"`.
- `completion_date_missing` informa esa carencia; el cumplimiento total sigue disponible.
- Sin vencidas: `status="sin datos"` y porcentajes `null`; los conteos vacíos son cero.
- `compliance`: population, due, completed, completed_on_time, on_time_pct, total_pct.
- `by_month`, `by_location`, `by_skill`: listas con `key` y las mismas métricas.
- Habilidad: `required_skill`, igual que aggregation.py; vacíos quedan identificados.
- Backlog: vencidas no terminadas; HH = work_hours × (1 − percent_complete/100).
- `backlog`: count, remaining_hours, by_age, by_priority, by_location y oldest (20).
- Antigüedad: 0–7, 8–30, 31–90, >90 días corridos; prioridad alta ≥70, media ≥40.
- `by_age_days` permite aplicar umbrales configurables sin truncar al top 20.
- No se calculan MTBF ni disponibilidad.

- `control.alerts.evaluate` evalúa y sincroniza en una transacción composable.
- Clave única existente: (project_id, rule, object_key); no se agrega almacenamiento.
- Las vigentes conservan reconocimiento; desaparecidas se resuelven; reapariciones son nuevas.
- Evaluación sin cambios no modifica id, first_seen, updated_at ni estado.
- `today` es inyectable; umbrales por argumento: delay_days=5, stale_days=14,
  min_spi=0.9, backlog_days=30. Comparaciones estrictas (> días, < SPI).
- Término proyectado: fin vigente planned_finish/early_finish, o fin real al corte.
- Se compara con la línea base activa solo para hitos (duración 0) y actividades críticas.
- No se reparte el pronóstico global de forecasts.py entre actividades individuales.
- Vencidas de programación: término planificado < corte y avance <100 %.
- Antigüedad del corte se compara con hoy; registros de avance, contra el corte.
- En curso sin registros al corte produce alerta; registros futuros no la eliminan.
- SPI usa `services.control_result(..., 'evm')`: alerta si algún SPI disponible < umbral.
- Si ninguno es calculable o EVM rechaza datos incompletos, alerta de datos insuficientes.
- Backlog antiguo se resume sobre toda la cartera, incluso anterior al inicio del proyecto.
- Calidad reutiliza `schedule_quality`; una alerta por regla con hallazgo crítico.
- Sincronizar alertas no modifica actividades, avances, programación ni línea base.
- `acknowledge` pone reconocida; `reopen` pone nueva. La siguiente evaluación verifica vigencia.
- Orden: nueva, reconocida, resuelta; dentro de cada estado: crítica, advertencia, info.
- Resumen: nueva y reconocida cuentan esos estados; critica cuenta críticas no resueltas.

## API
- GET `/api/projects/{pid}/maintenance/kpis?start=YYYY-MM-DD&end=YYYY-MM-DD`
- GET `/api/projects/{pid}/alerts?state=nueva|reconocida|resuelta`
- GET `/api/projects/{pid}/alerts/summary` → `{nueva, reconocida, critica}`
- POST `/api/alerts/{id}/acknowledge`
- POST `/api/alerts/{id}/reopen`
- Ambos GET de alertas evalúan antes de responder; recursos ausentes: 404; filtros inválidos: 422.

## Verificación
- Pruebas con SQLite real mediante repo y TestClient(create_app()); temporales fuera de backend.
- Caso 10 vencidas: 60 % a tiempo, 80 % total, backlog 2 / 22 HH; límites y cortes históricos.
- Diez evaluaciones idénticas: una alerta; reconocimiento, resolución, reaparición y API.
- Ejecución: nuevas 13/13; suites 272 aprobadas, 16 fallidas (auditoria_a2), 2 excluidas.
