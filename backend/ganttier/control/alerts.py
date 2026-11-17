"""Reglas explicables y ciclo de vida persistente de alertas, sin duplicados."""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import isfinite

from .. import repo, services
from ..maintenance.kpis import kpis
from .evm import evm
from .schedule_quality import schedule_quality, _load_activities, _assigned_activity_ids

DELAY_DAYS = 5
STALE_DAYS = 14
MIN_SPI = 0.9
BACKLOG_DAYS = 30
MAX_ITEM_ALERTS = 15   # sobre este número, una sola alerta resumen por regla


def _d(value) -> str:
    """Fecha en formato chileno dd-mm-aaaa para los textos de evidencia."""
    return value.strftime("%d-%m-%Y") if value else "—"
STATES = ("nueva", "reconocida", "resuelta")


def list_alerts(conn, pid: int, state: str | None = None) -> list[dict]:
    repo.get(conn, "projects", pid)
    if state is not None and state not in STATES:
        raise ValueError("Estado de alerta inválido.")
    query = "SELECT * FROM alerts WHERE project_id=?"
    args = [pid]
    if state is not None:
        query += " AND state=?"
        args.append(state)
    query += """ ORDER BY CASE state WHEN 'nueva' THEN 0 WHEN 'reconocida' THEN 1 ELSE 2 END,
        CASE severity WHEN 'crítica' THEN 0 WHEN 'advertencia' THEN 1 ELSE 2 END, id"""
    return [dict(row) for row in conn.execute(query, args)]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def set_state(conn, alert_id: int, state: str) -> dict:
    if state not in STATES:
        raise ValueError("Estado de alerta inválido.")
    with repo.transaction(conn):
        conn.execute("UPDATE alerts SET state=?, updated_at=? WHERE id=? AND state<>?",
                     (state, _now(), alert_id, state))
        row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if row is None:
            raise LookupError("No se encontró la alerta solicitada.")
        return dict(row)


def evaluate(conn, pid: int, *, today: date | None = None,
             delay_days: int = DELAY_DAYS, stale_days: int = STALE_DAYS,
             min_spi: float = MIN_SPI, backlog_days: int = BACKLOG_DAYS) -> list[dict]:
    """Evalúa al corte; conserva reconocimiento, resuelve y reactiva por clave.

    El término proyectado es el fin del programa vigente (planned/early_finish),
    o el fin real al corte. No se reparte el pronóstico global entre actividades.
    """
    if any(not isfinite(value) or value < 0 for value in (delay_days, stale_days, min_spi, backlog_days)):
        raise ValueError("Los umbrales deben ser finitos y no negativos.")
    today = today or date.today()
    with repo.transaction(conn):
        # Tomar el bloqueo de escritura ANTES de leer: en WAL, una transacción que primero lee y luego
        # intenta escribir falla de inmediato («database is locked») si otra conexión escribió entre
        # medio (p. ej., dos evaluaciones simultáneas). Una escritura vacía espera con busy_timeout.
        conn.execute("UPDATE alerts SET state=state WHERE 0")
        project = repo.get(conn, "projects", pid)
        cutoff = project.data_date or today
        cause = None
        try:
            data = services.control_inputs(conn, pid, as_of=cutoff)
        except ValueError as exc:
            # También los errores de preparación de EVM eran una alerta, no un
            # impedimento para evaluar calidad, vencimientos y mantenimiento.
            cause = str(exc)
            progress = repo.list_entities(conn, "progress", pid)
            data = dict(progress=progress, acts=services.build_activity_views(
                conn, pid, progress=progress, as_of=cutoff), baseline={
                    b.activity_id: b for b in repo.list_entities(conn, "baseline_activities",
                        filters={"baseline_id": project.active_baseline_id})
                } if project.active_baseline_id is not None else None)
        progress = data["progress"]
        views = {v.id: v for v in data["acts"]}
        baseline = data["baseline"] or {}
        activities = _load_activities(conn, pid)
        latest = {}
        for entry in progress:
            if entry.date <= cutoff:
                latest[entry.activity_id] = max(latest.get(entry.activity_id, entry.date), entry.date)
        active = {}

        def add(rule, key, severity, title, detail):
            active[(rule, key)] = (severity, title, detail, cutoff.isoformat())

        if conn.execute("SELECT 1 FROM periodic_tasks WHERE project_id=? AND quantity_total IS NOT NULL LIMIT 1", (pid,)).fetchone():
            from ..teams import summary
            import hashlib
            try:
                distribution = summary(conn, pid)
                for detail in distribution["warnings"]:
                    key = hashlib.sha256(detail.encode()).hexdigest()[:20]
                    add("equipos_reparto", key, "advertencia", "Revisar equidad de equipos", detail)
                for violation in distribution["violations"]:
                    detail = violation["detail"]
                    key = hashlib.sha256(detail.encode()).hexdigest()[:20]
                    add("equipos_capacidad", key, "crítica", "Reparto de equipos infactible", detail)
            except ValueError as exc:
                add("equipos_configuracion", "plan", "crítica", "Configuración de equipos inválida", str(exc))
        overdue, stale = [], []   # se emiten por actividad solo si son pocas (evita cientos de alertas)

        for activity in activities:
            view = views[activity.id]
            key = f"act:{activity.id}"
            plan = baseline.get(activity.id)
            projected = view.actual_finish or activity.planned_finish or activity.early_finish
            if (activity.critical or activity.duration_days == 0) and plan and plan.finish and projected:
                delay = (projected - plan.finish).days
                if delay > delay_days:
                    add("retraso_linea_base", key, "crítica", "Retraso respecto de línea base",
                        f"{activity.code}: término vigente {_d(projected)}; línea base {_d(plan.finish)}; "
                        f"retraso {delay} días (umbral > {delay_days}).")
            finish = activity.planned_finish or activity.early_finish
            # A4-09: término real informado al corte = terminada, igual que en EVM.
            if finish and finish < cutoff and view.percent_complete < 100 and not view.actual_finish:
                overdue.append((key, activity.code, (cutoff - finish).days,
                                f"{activity.code}: término planificado {_d(finish)}; corte {_d(cutoff)}; "
                                f"{(cutoff - finish).days} días vencida; avance {view.percent_complete:g} %."))
            in_progress = view.percent_complete < 100 and not view.actual_finish and (
                view.percent_complete > 0 or view.actual_start is not None or activity.status == "en_curso")
            last = latest.get(activity.id)
            age = (cutoff - last).days if last else None
            if in_progress and (last is None or age > stale_days):
                evidence = (f"último registro {_d(last)}, hace {age} días" if last else "0 registros al corte")
                stale.append((key, activity.code, age if age is not None else 10**6,
                              f"{activity.code}: avance {view.percent_complete:g} %; {evidence}; "
                              f"corte {_d(cutoff)}; ventana {stale_days} días."))
        # Mantenimiento: las ocurrencias vencidas se resumen siempre (el backlog ya las detalla por antigüedad).
        limit = 0 if project.mode == "mantenimiento" else MAX_ITEM_ALERTS
        for rule, items, title, plural in (
                ("actividad_vencida", overdue, "Actividad vencida", "actividades vencidas sin terminar"),
                ("avance_desactualizado", stale, "Avance sin actualizar", "actividades en curso sin avance reciente")):
            if not items:
                continue
            if len(items) <= limit:
                for key, _code, _days, detail in items:
                    add(rule, key, "advertencia", title, detail)
            else:
                worst = sorted(items, key=lambda item: -item[2])[:10]
                add(rule, "resumen", "advertencia", f"{len(items)} {plural}",
                    f"{len(items)} {plural} al corte {_d(cutoff)}. Las de mayor antigüedad: "
                    + ", ".join(f"{code} ({days} días)" if days < 10**6 else f"{code} (sin registros)"
                                for _key, code, days, _detail in worst) + ".")
        if project.data_date is None:
            add("corte_desactualizado", "proyecto", "advertencia", "Fecha de corte sin definir",
                f"0 fechas de corte informadas; evaluación provisional al {_d(cutoff)}.")
        elif (today - cutoff).days > stale_days:
            add("corte_desactualizado", "proyecto", "advertencia", "Fecha de corte desactualizada",
                f"Corte {_d(cutoff)}; hoy {_d(today)}; antigüedad {(today - cutoff).days} días (umbral > {stale_days}).")
        metrics = None
        try:
            if cause is None:
                metrics = evm(data["acts"], data["baseline"], progress,
                              data["calendar"], cutoff, calendars=data["calendars"])
        except ValueError as exc:
            # EVM valida, entre otros datos, las fechas del presupuesto positivo.
            metrics, cause = None, str(exc)
        indicators = [] if metrics is None else [(name, value) for name, value in (("SPI(t)", metrics.SPI_t), ("SPI", metrics.SPI))
                      if value is not None and isfinite(value)]
        if not indicators:
            evidence = cause or f"BAC={metrics.BAC:g}, PV={metrics.PV:g}, EV={metrics.EV:g}."
            add("spi_datos_insuficientes", "spi", "advertencia", "Datos insuficientes para SPI",
                f"SPI(t) y SPI no calculables al {_d(cutoff)}; {evidence} Revisar plan y avance.")
        elif any(value < min_spi for _, value in indicators):
            evidence = "; ".join(f"{name}={value:.4f}" for name, value in indicators)
            add("spi_bajo", "spi", "advertencia", "Desempeño de plazo bajo",
                f"{evidence}; umbral < {min_spi:g}; corte {_d(cutoff)}.")
        if project.mode == "mantenimiento":
            # Toda la cartera vencida, incluso compromisos anteriores al inicio.
            backlog = kpis(conn, pid, start=date.min, end=cutoff, as_of=cutoff,
                           project=project, progress=progress, views=views)["backlog"]
            aged = [r for r in backlog["by_age_days"] if r["key"] > backlog_days]
            if aged:
                add("backlog_antiguo", "proyecto", "advertencia", "Backlog de mantenimiento antiguo",
                    f"{sum(r['count'] for r in aged)} ocurrencias con antigüedad > {backlog_days} días; "
                    f"máxima {max(r['key'] for r in aged)} días; "
                    f"HH remanentes {sum(r['remaining_hours'] for r in aged):g}.")
        quality = schedule_quality(
            activities, repo.list_entities(conn, "relations", pid), [], cutoff,
            profile=project.mode, progress=progress, views=views,
            assigned_ids=_assigned_activity_ids(conn, pid))
        for finding in quality.findings:
            if finding.severity == "crítica":
                add("calidad_critica", finding.rule, "crítica", "Hallazgo crítico de calidad",
                    f"{finding.rule}: {finding.count} de {finding.population} {finding.population_unit}. "
                    f"{finding.description} {finding.action}")
        now = _now()
        for (rule, key), values in active.items():
            conn.execute("""INSERT INTO alerts
                (project_id,rule,object_key,severity,title,detail,data_date,state,first_seen,updated_at)
                VALUES (?,?,?,?,?,?,?,'nueva',?,?)
                ON CONFLICT(project_id,rule,object_key) DO UPDATE SET
                severity=excluded.severity,title=excluded.title,detail=excluded.detail,
                data_date=excluded.data_date,
                state=CASE WHEN alerts.state='resuelta' THEN 'nueva' ELSE alerts.state END,
                updated_at=excluded.updated_at
                WHERE alerts.state='resuelta' OR alerts.severity<>excluded.severity
                OR alerts.title<>excluded.title OR alerts.detail<>excluded.detail
                OR alerts.data_date IS NOT excluded.data_date""", (pid, rule, key, *values, now, now))
        for row in conn.execute("SELECT id,rule,object_key FROM alerts WHERE project_id=? AND state<>'resuelta'", (pid,)).fetchall():
            if (row["rule"], row["object_key"]) not in active:
                conn.execute("UPDATE alerts SET state='resuelta',data_date=?,updated_at=? WHERE id=?",
                             (cutoff.isoformat(), now, row["id"]))
        return list_alerts(conn, pid)
