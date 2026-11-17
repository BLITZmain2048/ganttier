"""Casos de uso; los motores se cargan solamente cuando se necesitan."""
from __future__ import annotations

import calendar as month_calendar
import hashlib
import json
import os
import zlib
import sqlite3
from collections import defaultdict
from dataclasses import fields, replace as dataclass_replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable


from . import db, domain as d, repo
from .calendars import WorkCalendar

DATA_DIR = Path(os.environ.get("GANTTIER_DATA_DIR", str(db.ROOT / "datos")))


class ModuleUnavailable(RuntimeError):
    """Un módulo opcional no se pudo cargar; la API lo traduce a HTTP 503."""


def _engine():
    try:
        from ganttier.engine import compute_cpm, optimize, validate_network
    except Exception as exc:
        raise ModuleUnavailable(f"El módulo de programación no está disponible: {exc}") from exc
    return compute_cpm, optimize, validate_network


def _maintenance():
    try:
        from ganttier.maintenance import generate_occurrences, level_periodic
    except Exception as exc:
        raise ModuleUnavailable(f"El módulo de mantenimiento no está disponible: {exc}") from exc
    return generate_occurrences, level_periodic


def _control():
    try:
        from ganttier.control import scurve, evm, worker_performance
    except Exception as exc:
        raise ModuleUnavailable(f"El módulo de control no está disponible: {exc}") from exc
    return scurve, evm, worker_performance


def excel_functions() -> tuple[Callable, Callable, Callable, Callable]:
    """Carga la interfaz pública Excel o informa indisponibilidad."""
    try:
        from ganttier.excel_io import build_project_template, build_maintenance_template, parse_workbook, export_project
    except Exception as exc:
        raise ModuleUnavailable(f"El módulo Excel no está disponible: {exc}") from exc
    return build_project_template, build_maintenance_template, parse_workbook, export_project


def build_activity_views(conn: sqlite3.Connection, pid: int, *, progress=None, assignments=None,
                         as_of: date | None = None, activity_ids=None) -> list[d.ActivityView]:
    """Fechas vigentes y avance acumulado a la fecha de corte (o a ``as_of``), sin consultas por fila."""
    project = repo.get(conn, "projects", pid)
    cutoff = as_of or project.data_date or date.today()
    selection = ""
    params = [pid]
    if activity_ids is not None:
        import json
        selected_json = json.dumps(list(activity_ids))
        selection = " AND id IN (SELECT value FROM json_each(?))"
        params.append(selected_json)
        if progress is None:
            progress = [repo._decode('progress', row) for row in conn.execute(
                'SELECT * FROM progress WHERE activity_id IN (SELECT value FROM json_each(?)) ORDER BY id',
                (selected_json,))]
    ledger = defaultdict(list)
    recorded = set()
    for entry in (repo.list_entities(conn, "progress", pid) if progress is None else progress):
        recorded.add(entry.activity_id)
        if entry.date <= cutoff:
            ledger[entry.activity_id].append(entry)
    assigned = defaultdict(list)
    if assignments is None:
        for aid, rid in conn.execute("SELECT activity_id,resource_id FROM assignments WHERE resource_id IS NOT NULL AND activity_id IN (SELECT id FROM activities WHERE project_id=?" + selection + ")", params):
            assigned[aid].append(rid)
    else:
        for assignment in assignments:
            if assignment.resource_id is not None:
                assigned[assignment.activity_id].append(assignment.resource_id)
    views = []
    rows = conn.execute("""SELECT id,code,name,COALESCE(planned_start,early_start),
        COALESCE(planned_finish,early_finish),duration_days,work_hours,cost_budget,
        percent_complete,actual_start,actual_finish,actual_hours,actual_cost,calendar_id,wbs_id
        FROM activities WHERE project_id=?""" + selection + " ORDER BY id", params)
    for row in rows:
        aid, code, name, start, finish, duration, work, budget, percent, actual_start, actual_finish, hours, cost, cid, wid = row
        actual_start = date.fromisoformat(actual_start) if actual_start else None
        actual_finish = date.fromisoformat(actual_finish) if actual_finish else None
        entries = ledger.get(aid)
        if aid in recorded and not entries:
            percent, hours, cost = 0.0, 0.0, 0.0
            actual_start, actual_finish = None, None
        if entries:
            latest = max(entries, key=lambda e: (e.date, e.id))
            percent = latest.percent_complete
            hours, cost = sum(e.actual_hours for e in entries), sum(e.actual_cost for e in entries)
            actual_start = min((e.actual_start for e in entries if e.actual_start), default=actual_start)
            actual_finish = latest.actual_finish or (latest.date if percent >= 100 else None)
        if actual_start and actual_start > cutoff:
            if aid not in recorded:
                # A4-05: la ficha sin registro de avance es un estado posterior al corte; su avance tampoco existía.
                percent, hours, cost = 0.0, 0.0, 0.0
                actual_finish = None
            actual_start = None
        if actual_finish and actual_finish > cutoff:
            actual_finish = None
        views.append(d.ActivityView(aid, code, name,
            date.fromisoformat(start) if start else actual_start,
            date.fromisoformat(finish) if finish else actual_finish,
            duration, work, budget, percent,
            actual_start, actual_finish, hours, cost, cid or project.calendar_id,
            wid, sorted(set(assigned[aid]))))
    return views


def sync_progress(conn: sqlite3.Connection, pid: int, activity_ids: list[int] | None = None) -> None:
    """Actualiza el resumen de avance visible en el Gantt, según el registro al corte."""
    if activity_ids is None:
        selected = {row[0] for row in conn.execute("SELECT DISTINCT p.activity_id FROM progress p JOIN activities a ON a.id=p.activity_id WHERE a.project_id=?", (pid,))}
    else:
        selected = set(activity_ids)
    if not selected:
        return
    views = build_activity_views(conn, pid, activity_ids=selected, assignments=[])
    import json
    has_entries = {row[0] for row in conn.execute(
        'SELECT DISTINCT activity_id FROM progress WHERE activity_id IN (SELECT value FROM json_each(?))',
        (json.dumps(list(selected)),))}
    updates = []
    for view in views:
        if view.id not in selected:
            continue
        if view.id not in has_entries:
            view.percent_complete, view.actual_hours, view.actual_cost = 0.0, 0.0, 0.0
            view.actual_start, view.actual_finish = None, None
        status = "terminada" if view.percent_complete >= 100 else "en_curso" if view.percent_complete > 0 or view.actual_start else "no_iniciada"
        updates.append((view.percent_complete, view.actual_hours, view.actual_cost,
            view.actual_start.isoformat() if view.actual_start else None,
            view.actual_finish.isoformat() if view.actual_finish else None, status, view.id, pid))
    with repo.transaction(conn):
        conn.executemany("UPDATE activities SET percent_complete=?,actual_hours=?,actual_cost=?,actual_start=?,actual_finish=?,status=? WHERE id=? AND project_id=?", updates)


def build_problem(conn: sqlite3.Connection, pid: int) -> d.ScheduleProblem:
    """Construye el contrato de programación y calendarios relativos a su horizonte."""
    project = repo.get(conn, "projects", pid)
    activities = repo.list_entities(conn, "activities", pid)
    explicit_dates = [value for a in activities for value in (a.window_end, a.planned_finish, a.actual_finish, a.constraint_date) if value]
    if project.mode == "mantenimiento" and project.end_date:
        horizon_end = project.end_date
    else:
        # En proyectos, end_date es un plazo (holgura negativa si no se cumple), no un límite del horizonte de cálculo.
        # cota segura: programa totalmente en serie (suma de duraciones en días corridos aprox.) + margen
        serial_days = int(sum(max(0, a.duration_days) for a in activities) * 7 / 5) + 90
        span = project.start_date + timedelta(days=max(366, serial_days))
        horizon_end = max([span] + ([project.end_date + timedelta(days=366)] if project.end_date else []) + explicit_dates)
    calendars = {c.id: WorkCalendar(c, project.start_date, horizon_end) for c in repo.list_entities(conn, "calendars")}
    default = project.calendar_id or next((cid for cid, cal in calendars.items() if cal.cal.name == "Chile L-V 8h"), next(iter(calendars), None))
    if default is None or default not in calendars:
        raise ValueError("El proyecto necesita un calendario válido.")
    views = {v.id: v for v in build_activity_views(conn, pid)}
    for activity in activities:
        view = views[activity.id]
        activity.percent_complete = view.percent_complete
        activity.actual_start, activity.actual_finish = view.actual_start, view.actual_finish
        activity.actual_hours, activity.actual_cost = view.actual_hours, view.actual_cost
        activity.status = "terminada" if view.percent_complete >= 100 else "en_curso" if view.percent_complete > 0 or view.actual_start else "no_iniciada"
    return d.ScheduleProblem(project, activities, repo.list_entities(conn, "relations", pid), repo.list_entities(conn, "resources", pid), repo.list_entities(conn, "assignments", pid), calendars, default, project.start_date, horizon_end, project.data_date, repo.list_entities(conn, "periodic_tasks", pid))


def _scenario_fingerprint(conn: sqlite3.Connection, pid: int) -> str:
    """SHA-256 determinista del estado persistido; no incluye historial de corridas."""
    digest = hashlib.sha256()
    project = repo.get(conn, "projects", pid)
    digest.update(str(project.data_date or date.today()).encode())
    queries = {
        "projects": ("SELECT * FROM projects WHERE id=?", (pid,)),
        "activities": ("SELECT * FROM activities WHERE project_id=? ORDER BY id", (pid,)),
        "relations": ("SELECT * FROM relations WHERE project_id=? ORDER BY id", (pid,)),
        "periodic_tasks": ("SELECT * FROM periodic_tasks WHERE project_id=? ORDER BY id", (pid,)),
        "resources": ("SELECT * FROM resources WHERE project_id=? OR project_id IS NULL ORDER BY id", (pid,)),
        "calendars": ("SELECT * FROM calendars ORDER BY id", ()),
        "calendar_exceptions": ("SELECT * FROM calendar_exceptions ORDER BY calendar_id,date,id", ()),
    }
    for table in ("assignments", "progress"):
        queries[table] = (f"SELECT * FROM {table} WHERE activity_id IN (SELECT id FROM activities WHERE project_id=?) ORDER BY id", (pid,))
    for table, (query, params) in queries.items():
        digest.update(table.encode())
        for row in conn.execute(query, params):
            digest.update(json.dumps(dict(row), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _save_run(conn: sqlite3.Connection, pid: int, kind: str, payload: dict, *, scenario_hash: str | None = None, **metadata: Any) -> int:
    payload = dict(payload, scenario_hash=scenario_hash or _scenario_fingerprint(conn, pid))
    directory = DATA_DIR / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    path = None
    try:
        with repo.transaction(conn):
            row = repo.create(conn, "schedule_runs", {"project_id": pid, "kind": kind, **metadata})
            run_id = row["id"]
            path = directory / f"{run_id}.json"
            temporary = path.with_suffix(".json.tmp")
            try:
                temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, default=lambda obj: obj.isoformat()), encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            repo.update(conn, "schedule_runs", run_id, {"result_path": str(path.resolve())})
            return run_id
    except Exception:
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def run_cpm(conn: sqlite3.Connection, pid: int) -> dict:
    """Calcula CPM, conserva una corrida y aplica sus fechas y holguras."""
    compute_cpm, _, validate_network = _engine()
    scenario_hash = _scenario_fingerprint(conn, pid)
    problem = build_problem(conn, pid)
    errors = validate_network(problem)
    if errors:
        raise ValueError("La red no es válida: " + "; ".join(errors))
    result = compute_cpm(problem)
    payload = repo.json_value(result)
    payload["starts"] = {str(aid): item.early_start.isoformat() for aid, item in result.results.items()}
    payload["finishes"] = {str(aid): item.early_finish.isoformat() for aid, item in result.results.items()}
    run_id = _save_run(conn, pid, "cpm", payload, scenario_hash=scenario_hash, status="calculado")
    apply_run(conn, pid, run_id)
    return {"run_id": run_id, "finish": result.project_finish, "critical_count": sum(bool(row.critical) for row in result.results.values()), "warnings": result.warnings}


def _occurrences(problem: d.ScheduleProblem, task_resources: dict[int, int | None] | None = None) -> list[d.Occurrence]:
    resources = {assignment.activity_id: assignment.resource_id for assignment in problem.assignments if assignment.resource_id is not None}
    result = []
    continuity = {t.id: t.batch_continuous_in_shift for t in problem.periodic_tasks}
    for a in problem.activities:
        if a.periodic_task_id is None or a.locked or a.percent_complete > 0 or a.actual_start or a.actual_finish or a.actual_hours > 0:
            continue
        result.append(d.Occurrence(a.periodic_task_id, a.occurrence_index or 0, a.code, a.name,
            a.due_date or a.planned_start or problem.horizon_start,
            a.window_start or problem.horizon_start, a.window_end or problem.horizon_end,
            a.duration_days, a.work_hours, a.crew_size, a.required_skill,
            a.team_id if a.team_id is not None else (task_resources.get(a.periodic_task_id) if task_resources is not None else resources.get(a.id)),
            a.calendar_id or problem.default_calendar_id, a.planned_start, a.planned_finish,
            a.window_hard, a.location, a.equipment, a.requires_outage, a.priority, a.id,
            a.batch_index, a.quantity, a.unit, a.team_id, a.equity_mode,
            duration_hours=a.duration_hours, checklist_codes=a.checklist_codes,
            batch_continuous_in_shift=continuity.get(a.periodic_task_id, False)))
    return result


def run_optimize(conn: sqlite3.Connection, pid: int, weights: d.ObjectiveWeights | None = None, options: d.OptimizeOptions | None = None, apply: bool = False) -> dict:
    """Optimiza según el modo; los mapas grandes permanecen en el archivo de corrida."""
    team_plan = conn.execute("SELECT 1 FROM periodic_tasks WHERE project_id=? AND (quantity_total IS NOT NULL OR is_daily_round=1) LIMIT 1", (pid,)).fetchone() is not None
    weights = weights or d.ObjectiveWeights()
    options = options or d.OptimizeOptions(allow_overtime=not team_plan)
    if team_plan:
        from .solve import solve_project
        return solve_project(conn, pid, weights, dataclass_replace(options, respect_resource_limits=True), apply)
    if options.role_mode == 'indivisible':
        from .solve import solve_project
        return solve_project(conn, pid, weights, options, apply)
    scenario_hash = _scenario_fingerprint(conn, pid)
    problem = build_problem(conn, pid)
    if problem.project.mode == "mantenimiento":
        _, level_periodic = _maintenance()
        task_resources = {t.id: t.resource_id for t in repo.list_entities(conn, "periodic_tasks", pid)}
        # Protege también el trabajo registrado después del corte actual.
        recorded = {r[0] for r in conn.execute("""SELECT DISTINCT p.activity_id FROM progress p
            JOIN activities a ON a.id=p.activity_id WHERE a.project_id=?
            AND (p.percent_complete>0 OR p.actual_start IS NOT NULL OR p.actual_finish IS NOT NULL OR p.actual_hours>0)""", (pid,))}
        occurrences = [o for o in _occurrences(problem, task_resources) if o.activity_id not in recorded]
        if not occurrences:
            raise ValueError("No hay ocurrencias pendientes para nivelar; las actividades con avance, bloqueadas o no periódicas permanecen fijas.")
        result = level_periodic(occurrences, problem.resources, problem.calendars, weights, options)
        fixed_count = len(problem.activities) - len(occurrences)
        if fixed_count:
            result.warnings.append(f"{fixed_count} actividades fijas por avance/bloqueo o no periódicas no se movieron")
            result.warnings.append("La carga de las actividades fijas no se descuenta de la capacidad: el nivelador no admite carga reservada.")
        kind = "maintenance_level"
    else:
        _, optimize, validate_network = _engine()
        errors = validate_network(problem)
        if errors:
            raise ValueError("La red no es válida: " + "; ".join(errors))
        result = optimize(problem, weights, options)
        kind = "optimize"
    payload = repo.json_value(result)
    if kind == "maintenance_level":
        # La cuadrilla completa es una extensión pública de A3, no un campo
        # de OptimizeResult; asdict por sí solo conserva únicamente al líder.
        from ganttier.maintenance import crew_assignment
        payload["crew_assignment"] = repo.json_value(crew_assignment(result))
    run_id = _save_run(conn, pid, kind, payload, scenario_hash=scenario_hash, weights=d.to_dict(weights), options=d.to_dict(options), metrics_before=payload["before"], metrics_after=payload["after"], status=result.status, elapsed_s=result.elapsed_s)
    if apply and result.status != "infactible":
        apply_run(conn, pid, run_id)
    summary = dict(payload, run_id=run_id)
    if len(result.starts) > 5000:
        for key in ("starts", "finishes", "worker_assignment", "crew_assignment"):
            summary[key] = {}
        summary["stored_activity_count"] = len(result.starts)
    return summary


def apply_run(conn: sqlite3.Connection, pid: int, run_id: int) -> dict:
    """Aplica fechas, holguras y asignaciones de una corrida del mismo proyecto."""
    with repo.transaction(conn):
        return _apply_run(conn, pid, run_id)


def _apply_run(conn: sqlite3.Connection, pid: int, run_id: int) -> dict:
    run = repo.get(conn, "schedule_runs", run_id)
    if run["project_id"] != pid:
        raise LookupError("La corrida no pertenece al proyecto.")
    if run["status"] == "infactible":
        raise ValueError("No se puede aplicar una corrida infactible.")
    path = Path(run["result_path"] or "").resolve()
    if path.parent != (DATA_DIR / "runs").resolve() or path.suffix != ".json":
        raise ValueError("La ruta de resultados no es válida.")
    if not path.is_file():
        raise LookupError("No se encontró el archivo de la corrida.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    starts, finishes = payload.get("starts", {}), payload.get("finishes", {})
    if set(starts) != set(finishes):
        raise ValueError("La corrida tiene fechas incompletas.")
    current_hash = _scenario_fingerprint(conn, pid)
    applied_hash = conn.execute("SELECT value FROM meta WHERE key=?", (f"run_applied_hash:{run_id}",)).fetchone()
    expected_hash = applied_hash[0] if run["applied"] and applied_hash else payload.get("scenario_hash")
    if expected_hash != current_hash:
        raise ValueError("La programación cambió desde que se simuló esta corrida; vuelve a simular.")
    if run["applied"]:
        return {"run_id": run_id, "applied": True, "activities": len(starts)}
    existing = {a.id: a for a in repo.list_entities(conn, "activities", pid)}
    if run["kind"] == "maintenance_level":
        recorded = {r[0] for r in conn.execute("""SELECT DISTINCT p.activity_id FROM progress p JOIN activities a ON a.id=p.activity_id
            WHERE a.project_id=? AND (p.percent_complete>0 OR p.actual_start IS NOT NULL OR p.actual_finish IS NOT NULL OR p.actual_hours>0)""", (pid,))}
        if any(int(aid) in recorded or (int(aid) in existing and (existing[int(aid)].percent_complete > 0 or existing[int(aid)].actual_start)) for aid in starts):
            raise ValueError("La corrida de mantenimiento tiene actividades con avance; vuelva a calcular un plan pendiente.")
    if any(int(aid) not in existing for aid in starts):
        raise ValueError("La corrida referencia actividades eliminadas o de otro proyecto; vuelva a calcular.")
    if run["kind"] == "role_blocks":
        from .role_schedules import audit_payload
        audit_payload(conn, pid, payload)
    if any(a.team_id is not None for a in existing.values()):
        from .engine.verify import verify_with_options
        problem = build_problem(conn, pid)
        all_starts = {a.id: a.actual_start or a.planned_start for a in problem.activities}
        all_finishes = {a.id: a.actual_finish or a.planned_finish for a in problem.activities}
        all_starts.update({int(aid): date.fromisoformat(value) for aid, value in starts.items()})
        all_finishes.update({int(aid): date.fromisoformat(value) for aid, value in finishes.items()})
        proposed_workers = {int(aid): rid for aid, rid in payload.get("worker_assignment", {}).items()}
        proposed_crews = {int(aid): members for aid, members in payload.get("crew_assignment", {}).items()}
        # repo.get decodifica JSON; también admite corridas recibidas como texto.
        run_options = run["options"] or {}
        if isinstance(run_options, str):
            run_options = json.loads(run_options)
        checked = verify_with_options(problem, all_starts, all_finishes, d.OptimizeOptions(**run_options),
                                  worker_assignment=proposed_workers or None, crews=proposed_crews)
        if not checked.feasible:
            raise ValueError("Lotes no verificables: " + "; ".join(v["detail"] for v in checked.violations[:10]))
    updates = []
    for aid, start in starts.items():
        end = finishes[aid]
        if date.fromisoformat(end) < date.fromisoformat(start):
            raise ValueError("La corrida contiene un intervalo de fechas inválido.")
        activity = existing[int(aid)]
        if activity.locked and (str(activity.planned_start) != start or str(activity.planned_finish) != end):
            raise ValueError("La corrida intenta modificar una actividad bloqueada.")
        updates.append((start, end, int(aid), pid))
    with repo.transaction(conn):
        conn.executemany("UPDATE activities SET planned_start=?,planned_finish=? WHERE id=? AND project_id=?", updates)
        cpm_rows = []
        for aid, item in payload.get("results", {}).items():
            cpm_rows.append(tuple(item[k] for k in ("early_start", "early_finish", "late_start", "late_finish", "total_float", "free_float", "critical")) + (int(aid), pid))
        conn.executemany("UPDATE activities SET early_start=?,early_finish=?,late_start=?,late_finish=?,total_float=?,free_float=?,critical=? WHERE id=? AND project_id=?", cpm_rows)
        workers = payload.get("worker_assignment", {})
        crews = payload.get("crew_assignment", {})
        worker_rows = []
        for aid, rid in workers.items():
            members = crews.get(aid, [rid])
            if not members or len(set(members)) != len(members):
                raise ValueError("La corrida contiene una cuadrilla inválida.")
            aid = int(aid)
            if aid not in existing:
                raise ValueError("La asignación referencia una actividad ajena al proyecto.")
            activity = existing[aid]
            for member in members:
                worker_rows.append({"activity_id": aid, "resource_id": member, "skill": activity.required_skill, "units": activity.crew_size / len(members), "work_hours": activity.work_hours / len(members)})
        if worker_rows:
            conn.executemany("DELETE FROM assignments WHERE activity_id=?", [(r["activity_id"],) for r in worker_rows])
            repo.create_many(conn, "assignments", worker_rows)
        from .role_schedules import persist_allocation
        persist_allocation(conn, pid, run_id, payload if run["kind"] == "role_blocks" else None)
        repo.update(conn, "schedule_runs", run_id, {"applied": True})
        # El propio resultado modifica fechas/asignaciones: admite reaplicación
        # sin aceptar ninguna edición posterior del escenario.
        repo.set_meta(conn, f"run_applied_hash:{run_id}", _scenario_fingerprint(conn, pid))
    return {"run_id": run_id, "applied": True, "activities": len(updates)}


def generate_maintenance(conn: sqlite3.Connection, pid: int, horizon_start: date, horizon_end: date, replace: bool = True) -> dict:
    """Genera a través de A3 y persiste actividades y asignaciones en bloque."""
    generate_occurrences, _ = _maintenance()
    project = repo.get(conn, "projects", pid)
    if project.mode != "mantenimiento" or horizon_end < horizon_start:
        raise ValueError("La generación requiere un proyecto de mantenimiento y un horizonte válido.")
    calendars = {c.id: WorkCalendar(c, horizon_start, horizon_end) for c in repo.list_entities(conn, "calendars")}
    default = project.calendar_id or next(iter(calendars))
    tasks = repo.list_entities(conn, "periodic_tasks", pid)
    if any(t.quantity_total is not None or t.is_daily_round for t in tasks):
        from .solve import solve_project
        return solve_project(conn, pid, weights=d.ObjectiveWeights(level=1, window=0),
            options=dict(horizon_start=horizon_start, horizon_end=horizon_end, regenerate=replace,
                         allow_overtime=False, respect_resource_limits=True), apply=True)
    occurrences = generate_occurrences(tasks, horizon_start, horizon_end, calendars, default)
    return {"created": repo.save_occurrences(conn, pid, occurrences, replace, return_entities=False)}


def create_baseline(conn: sqlite3.Connection, pid: int, name: str = "Línea base", activate: bool = True) -> d.Baseline:
    """Captura el programa vigente con INSERT SELECT atómico."""
    repo.get(conn, "projects", pid)
    with repo.transaction(conn):
        baseline = repo.create(conn, "baselines", {"project_id": pid, "name": name})
        conn.execute("""INSERT INTO baseline_activities(baseline_id,activity_id,start,finish,work_hours,cost,duration_days)
            SELECT ?,id,COALESCE(planned_start,early_start),COALESCE(planned_finish,early_finish),work_hours,cost_budget,duration_days
            FROM activities WHERE project_id=?""", (baseline.id, pid))
        if activate:
            repo.update(conn, "projects", pid, {"active_baseline_id": baseline.id})
        return baseline


def _baseline(conn: sqlite3.Connection, project: d.Project) -> dict[int, d.BaselineActivity] | None:
    if project.active_baseline_id is None:
        return None
    return {entry.activity_id: entry for entry in repo.list_entities(conn, "baseline_activities", filters={"baseline_id": project.active_baseline_id})}


def project_summary(conn: sqlite3.Connection, pid: int) -> dict:
    """Resumen liviano para la página Inicio (agregados SQL; no descarga actividades ni avances).

    Con 100 000 actividades evita transferir el programa completo solo para mostrar indicadores.
    """
    project = repo.get(conn, "projects", pid)
    n, hours, finish, unscheduled, max_pct = conn.execute("""SELECT COUNT(*), COALESCE(SUM(work_hours),0),
        MAX(COALESCE(planned_finish, early_finish)),
        SUM(CASE WHEN COALESCE(planned_start, early_start) IS NULL OR COALESCE(planned_finish, early_finish) IS NULL THEN 1 ELSE 0 END),
        MAX(percent_complete) FROM activities WHERE project_id=?""", (pid,)).fetchone()
    scalar = lambda sql: conn.execute(sql, (pid,)).fetchone()[0]
    scheduled = bool(n) and not unscheduled
    evm = None
    # A4-11: con línea base activa Control calcula EVM aunque haya actividades nuevas sin fechas fuera de ella.
    if scheduled or (n and project.active_baseline_id is not None):
        try:
            evm = d.to_dict(control_result(conn, pid, "evm"))
        except ValueError:
            evm = None
    return {
        "project": d.to_dict(project), "activities": n, "work_hours": float(hours or 0), "finish": finish,
        "scheduled": scheduled, "unscheduled": int(unscheduled or 0), "max_percent": float(max_pct or 0),
        "resources": scalar("SELECT COUNT(*) FROM resources WHERE project_id=? OR project_id IS NULL"),
        "periodic_tasks": scalar("SELECT COUNT(*) FROM periodic_tasks WHERE project_id=?"),
        "baselines": scalar("SELECT COUNT(*) FROM baselines WHERE project_id=?"),
        "progress": scalar("SELECT COUNT(*) FROM progress WHERE activity_id IN (SELECT id FROM activities WHERE project_id=?)"),
        "evm": evm,
    }


def control_inputs(conn: sqlite3.Connection, pid: int, *, as_of: date | None = None, assignments=None) -> dict:
    """Entradas comunes de control (vistas, línea base, avance, calendarios efectivos y corte).

    Única fuente para curva S, EVM, desempeño y los análisis (pronósticos, EVM por grupo), de modo que
    todos usen exactamente los mismos calendarios por actividad y la misma fecha de corte.
    """
    project = repo.get(conn, "projects", pid)
    progress = repo.list_entities(conn, "progress", pid)
    acts = build_activity_views(conn, pid, progress=progress, assignments=assignments, as_of=as_of)
    cutoff = as_of or project.data_date or date.today()
    baseline = _baseline(conn, project)
    dates = [value for a in acts for value in (a.start, a.finish, a.actual_start, a.actual_finish) if value]
    dates.extend(value for a in (baseline or {}).values() for value in (a.start, a.finish) if value)
    dates.extend(entry.date for entry in progress)
    lower = min([project.start_date, cutoff] + dates)
    upper = max([project.end_date or cutoff, cutoff] + dates)
    # El pronóstico reprograma el remanente desde el corte, incluso años tarde.
    upper = date.fromordinal(min(date.max.toordinal(), upper.toordinal() + 3660))
    cid = project.calendar_id or repo.list_entities(conn, "calendars")[0].id
    calendars = {key: WorkCalendar(repo.get(conn, "calendars", key), lower, upper)
                 for key in {cid, *(a.calendar_id for a in acts if a.calendar_id is not None)}}
    return dict(project=project, progress=progress, acts=acts, cutoff=cutoff, baseline=baseline,
                calendar=calendars[cid], calendars=calendars)


def control_result(conn: sqlite3.Connection, pid: int, kind: str, period: str = "week", basis: str = "hours", start: date | None = None, end: date | None = None,
                   as_of: date | None = None) -> Any:
    """Despacha curva S, EVM o desempeño con la misma fecha de corte (``as_of`` la reemplaza)."""
    scurve, evm, worker_performance = _control()
    if period not in {"day", "week", "month"} or basis not in {"hours", "cost", "count"}:
        raise ValueError("Período o base de control inválidos.")
    assignments = repo.list_entities(conn, "assignments", pid) if kind == "performance" else None
    data = control_inputs(conn, pid, as_of=as_of, assignments=assignments)
    project, progress, acts, cutoff = data["project"], data["progress"], data["acts"], data["cutoff"]
    baseline, cal, calendars = data["baseline"], data["calendar"], data["calendars"]
    if kind == "performance":
        return worker_performance(acts, assignments, progress, repo.list_entities(conn, "resources", pid),
                                  start or project.start_date, end or cutoff, cutoff,
                                  calendar=cal, calendars=calendars)
    args = (acts, baseline, progress, cal, cutoff)
    if kind == "scurve":
        return scurve(*args, period=period, basis=basis, calendars=calendars)
    if kind == "evm":
        return evm(*args, basis=basis, calendars=calendars)
    raise ValueError("Consulta de control desconocida.")


def load_profile(conn: sqlite3.Connection, pid: int, group: str = "resource", period: str = "day", from_date: date | None = None, to_date: date | None = None) -> d.LoadProfile:
    """Perfil de HH por diferencias vectorizadas; el rango recorta sin redistribuir HH."""
    import numpy as np
    if group not in {"resource", "skill"} or period not in {"day", "week", "month"}:
        raise ValueError("Agrupación o período de carga inválidos.")
    project = repo.get(conn, "projects", pid)
    activities = {a.id: a for a in repo.list_entities(conn, "activities", pid)}
    resources = {r.id: r for r in repo.list_entities(conn, "resources", pid)}
    assignments = repo.list_entities(conn, "assignments", pid)
    starts = [a.planned_start or a.early_start for a in activities.values() if a.planned_start or a.early_start]
    ends = [a.planned_finish or a.early_finish for a in activities.values() if a.planned_finish or a.early_finish]
    first = from_date or min(starts, default=project.start_date)
    last = to_date or max(ends, default=project.end_date or first)
    if last < first:
        raise ValueError("El fin del perfil debe ser posterior al inicio.")
    lower, upper = min([first] + starts), max([last] + ends)
    count = (last - first).days + 1
    calendars = {c.id: WorkCalendar(c, lower, upper) for c in repo.list_entities(conn, "calendars")}
    default = project.calendar_id or next(iter(calendars))
    offset = (first - lower).days
    vectors = {cid: c.day_hours_vector()[offset:offset + count] for cid, c in calendars.items()}
    demands, capacities = defaultdict(lambda: np.zeros(count)), defaultdict(lambda: np.zeros(count))
    for resource in resources.values():
        if not resource.active:
            continue
        key_list = [resource.name] if group == "resource" else (resource.skills or ["Sin habilidad"])
        vector = vectors[resource.calendar_id or default]
        capacity = (vector > 0) * resource.capacity_hours_day * resource.max_units
        for key in key_list:
            capacities[key] += capacity
    grouped_events = defaultdict(list)
    assigned_ids = set()
    for assignment in assignments:
        activity = activities[assignment.activity_id]
        assigned_ids.add(activity.id)
        resource = resources.get(assignment.resource_id)
        key = (resource.name if resource else assignment.skill or activity.required_skill or "Sin asignar") if group == "resource" else assignment.skill or activity.required_skill or (resource.skills[0] if resource and resource.skills else "Sin habilidad")
        grouped_events[(key, activity.calendar_id or default)].append((activity, assignment.work_hours, assignment.units))
    for activity in activities.values():
        if activity.id not in assigned_ids:
            grouped_events[(activity.required_skill or "Sin asignar", activity.calendar_id or default)].append((activity, activity.work_hours, activity.crew_size))
    for (key, cid), rows in grouped_events.items():
        cal = calendars[cid]
        diff = np.zeros(count + 1)
        lefts, rights, weights = [], [], []
        for activity, work, units in rows:
            start = activity.planned_start or activity.early_start
            finish = activity.planned_finish or activity.early_finish
            if not start or not finish or finish < first or start > last or activity.duration_days == 0:
                continue
            days = cal.workdays_between(start, finish)
            if days == 0:
                continue
            # HH explícitas uniformes por día laborable; si faltan se usa la dotación.
            daily = work / days if work else units * cal.cal.hours_per_day
            lefts.append(max(0, (start - first).days))
            rights.append(min(count, (finish - first).days + 1))
            weights.append(daily)
        np.add.at(diff, np.asarray(lefts, dtype=int), weights)
        np.add.at(diff, np.asarray(rights, dtype=int), -np.asarray(weights))
        demands[key] += np.cumsum(diff[:-1]) * (vectors[cid] > 0)
    labels = []
    for index in range(count):
        day = first + timedelta(days=index)
        if period == "week":
            day -= timedelta(days=day.weekday())
        elif period == "month":
            day = day.replace(day=1)
        labels.append(day.isoformat())
    unique, inverse = np.unique(labels, return_inverse=True)
    series = [d.LoadSeries(key, np.bincount(inverse, weights=capacities[key], minlength=len(unique)).tolist(), np.maximum(0, np.bincount(inverse, weights=demands[key], minlength=len(unique))).tolist()) for key in sorted(capacities.keys() | demands.keys())]
    return d.LoadProfile(period, unique.tolist(), series)


def _period_bounds(kind: str, start: date) -> tuple[date, date]:
    if kind == "weekly":
        start -= timedelta(days=start.weekday())
        end = start + timedelta(days=6)
    elif kind == "monthly":
        start = start.replace(day=1)
        end = start.replace(day=month_calendar.monthrange(start.year, start.month)[1])
    elif kind == "annual":
        start = start.replace(month=1, day=1)
        end = start.replace(month=12, day=31)
    else:
        raise ValueError("El período debe ser weekly, monthly o annual.")
    return start, end


def build_report_data(conn: sqlite3.Connection, pid: int, period_type: str, period_start: date) -> d.ReportData:
    """Arma todas las secciones del informe usando períodos civiles completos."""
    start, end = _period_bounds(period_type, period_start)
    next_start, next_end = _period_bounds(period_type, end + timedelta(days=1))
    project = repo.get(conn, "projects", pid)
    current_cutoff = project.data_date or date.today()
    # D4: un informe nunca usa avances posteriores al fin de su período. Si el período ya cerró antes
    # del corte vigente, se evalúa al fin del período con el historial de avance registrado.
    cutoff = min(current_cutoff, end)
    as_of = cutoff if cutoff != current_cutoff else None
    baseline = _baseline(conn, project) or {}
    resources = {r.id: r.name for r in repo.list_entities(conn, "resources", pid)}
    originals = {a.id: a for a in repo.list_entities(conn, "activities", pid)}
    scheduled, completed, delayed, lookahead, milestones, critical = [], [], [], [], [], []
    for activity in build_activity_views(conn, pid, as_of=as_of):
        reference = baseline.get(activity.id)
        baseline_finish = reference.finish if reference else None
        target = baseline_finish or activity.finish
        is_done = activity.percent_complete >= 100 or activity.actual_finish is not None
        status = "terminada" if is_done else "atrasada" if target and target < cutoff else "en_curso" if activity.percent_complete > 0 else "no_iniciada"
        actual_or_cutoff = activity.actual_finish or cutoff
        delay = max(0, (actual_or_cutoff - target).days) if target else None
        row = d.ReportRow(activity.code, activity.name, activity.start, activity.finish, activity.percent_complete, status, ", ".join(resources[rid] for rid in activity.resource_ids if rid in resources), baseline_finish, delay, originals[activity.id].location)
        if activity.start and activity.finish and activity.start <= end and activity.finish >= start:
            scheduled.append(row)
        if activity.actual_finish and start <= activity.actual_finish <= end:
            completed.append(row)
        if status == "atrasada":
            delayed.append(row)
        if not is_done and activity.start and activity.finish and activity.start <= next_end and activity.finish >= next_start:
            lookahead.append(row)
        if activity.duration_days == 0:
            milestones.append(row)
        if originals[activity.id].critical:
            critical.append(row)
    period = "month" if period_type == "annual" else "week" if period_type == "monthly" else "day"
    # La curva S cubre todo el proyecto: semanal en informes semanales/mensuales y mensual en el anual.
    scurve_period = "month" if period_type == "annual" else "week"
    notes = ("La programación y la carga corresponden al período solicitado; el próximo período se muestra en la previsión. "
             "Los hitos conservan el alcance global del proyecto, incluidos los próximos hitos. "
             "Las actividades atrasadas, EVM y curva S se evalúan a la fecha de corte del informe.")
    if project.description.startswith('IMPORTACIÓN ASISTIDA.'):
        notes += '\n\n' + project.description
    if as_of is not None:
        notes = (f"Informe al cierre del período: corte {as_of:%d-%m-%Y}. El proyecto tiene un corte vigente posterior "
                 f"({current_cutoff:%d-%m-%Y}); los avances registrados después del período no se incluyen. " + notes)
    if cutoff < start:
        notes = ("Período posterior a la fecha de corte: se presenta la programación, sin datos de desempeño del período. "
                 "EVM y curva S conservan el estado conocido al corte. " + notes)
    report_project = dataclass_replace(project, data_date=cutoff)
    from .routes import checklist_report
    team_data = dict(rows=[], warnings=[], violations=[])
    if conn.execute("SELECT 1 FROM periodic_tasks WHERE project_id=? AND quantity_total IS NOT NULL LIMIT 1", (pid,)).fetchone():
        from .teams import summary
        team_data = summary(conn, pid)
        team_data["rows"] = [r for r in team_data["rows"] if start <= r["due_date"] <= end]
    return d.ReportData(report_project, period_type, start, end, datetime.now(),
        control_result(conn, pid, "evm", as_of=as_of), control_result(conn, pid, "scurve", period=scurve_period, as_of=as_of),
        load_profile(conn, pid, "resource", period, start, end),
        control_result(conn, pid, "performance", start=start, end=cutoff, as_of=as_of) if cutoff >= start else [],
        scheduled, completed, delayed, lookahead, milestones,
        {"Actividades programadas en el período": len(scheduled), "Actividades terminadas en el período": len(completed),
         "Actividades atrasadas a la fecha de corte": len(delayed), "Actividades del próximo período": len(lookahead)},
        notes,
          team_distribution=team_data["rows"],
          route_checklist=[r for r in checklist_report(conn,pid) if r['finish'] and str(start)<=r['finish']<=str(end)],
          critical=critical,
          skill_load=load_profile(conn, pid, 'skill', 'week', start, end),
          team_alerts=[*team_data["warnings"], *(v["detail"] for v in team_data["violations"])])


def render_reports(conn: sqlite3.Connection, pid: int, period_type: str, period_start: date, formats: list[str]) -> list[dict]:
    """Genera informes descargables con nombres únicos controlados por el servidor."""
    from uuid import uuid4
    if not formats or any(fmt not in {"xlsx", "pdf", "docx", "html"} for fmt in formats):
        raise ValueError("Formato de informe inválido.")
    try:
        from ganttier.reports import render
    except Exception as exc:
        raise ModuleUnavailable(f"El módulo de informes no está disponible: {exc}") from exc
    report = build_report_data(conn, pid, period_type, period_start)
    directory = DATA_DIR / "informes"
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    paths = []
    try:
        for fmt in dict.fromkeys(formats):
            filename = f"proyecto-{pid}-{period_type}-{report.period_start}-{uuid4().hex[:12]}.{fmt}"
            path = directory / filename
            paths.append(path)
            render(report, fmt, path)
            if not path.is_file():
                raise ValueError("El generador no creó el archivo del informe.")
            results.append({"format": fmt, "filename": filename, "url": f"/api/reports/files/{filename}"})
    except BaseException:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    return results


def create_project_from_bundle(conn: sqlite3.Connection, bundle: d.ImportBundle, save: Callable | None = None) -> d.Project:
    """Crea un proyecto nuevo a partir de un libro Excel validado (plantilla o exportación)."""
    if save is None:
        from .api.files import _save_bundle as save
    from .api import schemas
    with repo.transaction(conn):
        values = dict(bundle.project)
        calendar_name = values.pop("calendar_name", None)
        values.pop("id", None)
        values.pop("active_baseline_id", None)
        values["mode"] = bundle.mode
        # Calendarios antes del proyecto permiten resolver su calendario por nombre.
        calendars = {c.name: c.id for c in repo.list_entities(conn, "calendars")}
        for source in bundle.calendars:
            repo.import_calendar(conn, schemas.Calendar.model_validate(source).model_dump(), calendars)
        if calendar_name:
            if calendar_name not in calendars:
                raise ValueError("No se encontró el calendario del proyecto en la plantilla.")
            values["calendar_id"] = calendars[calendar_name]
        elif not values.get("calendar_id"):
            values["calendar_id"] = next(iter(calendars.values()))
        project = repo.create(conn, "projects", schemas.Project.model_validate(values).model_dump())
        save(conn, project.id, bundle)
        sync_progress(conn, project.id)
        return project


def demo_project(conn: sqlite3.Connection | None = None) -> d.Project:
    """Crea una subestación de 80 actividades, seis WBS y doce recursos."""
    if conn is None:
        connection = db.connect()
        try:
            db.initialize(connection)
            return demo_project(connection)
        finally:
            connection.close()
    example = db.ROOT / "ejemplos" / "Ejemplo_Proyecto_SE_220kV.xlsx"
    if example.exists():
        try:
            _, _, parse_workbook, _ = excel_functions()
            bundle = parse_workbook(example, "proyecto")
            if not bundle.has_errors:
                bundle.project["end_date"] = None  # demo: sin plazo impuesto, la ruta crítica queda con holgura 0
                bundle.project["name"] = "Demo · " + str(bundle.project.get("name") or "Proyecto S/E")
                project = create_project_from_bundle(conn, bundle)
                try:
                    run_cpm(conn, project.id)
                except Exception:  # la demo no debe fallar por una advertencia de red
                    pass
                return repo.get(conn, "projects", project.id)
        except ModuleUnavailable:
            pass
    with repo.transaction(conn):
        cid = repo.list_entities(conn, "calendars")[0].id
        start = date(2026, 1, 5)
        project = repo.create(conn, "projects", {"name": "Construcción S/E 220/23 kV", "start_date": start, "end_date": None, "data_date": start, "calendar_id": cid, "description": "Ampliación de subestación: dos transformadores de 60 MVA, patio de 220 kV y sala de control."})
        phases = [("Ingeniería", ["Levantamiento topográfico", "Estudio de cortocircuito", "Coordinación de protecciones", "Malla de puesta a tierra", "Planos de fundaciones", "Ingeniería de control", "Revisión de planos"]), ("Adquisiciones", ["Especificación de equipos", "Licitación de transformadores", "Evaluación técnica", "Orden de compra", "Fabricación", "Pruebas FAT", "Transporte a obra"]), ("Obras civiles", ["Instalación de faenas", "Movimiento de tierras", "Fundaciones de equipos", "Canalizaciones", "Edificio de control", "Caminos interiores", "Cierre perimetral"]), ("Montaje electromecánico", ["Estructuras metálicas", "Montaje de interruptores", "Montaje de seccionadores", "Montaje de transformadores", "Tendido de barras", "Cableado de potencia", "Cableado de control"]), ("Pruebas", ["Pruebas de aislación", "Inyección secundaria", "Pruebas de enclavamientos", "Verificación SCADA", "Pruebas de disparo", "Medición de malla", "Pruebas integradas"]), ("Puesta en servicio", ["Revisión de protocolos", "Coordinación de desconexión", "Energización de barras", "Energización de transformadores", "Prueba con carga", "Entrega documental", "Recepción provisoria"])]
        wbs = repo.create_many(conn, "wbs", [{"project_id": project.id, "code": str(i + 1), "name": phase[0], "sort_order": i} for i, phase in enumerate(phases)])
        skills = ["Ingeniería", "Adquisiciones", "Civil", "SSEE", "Protecciones", "Control"]
        resources = repo.create_many(conn, "resources", [{"project_id": project.id, "name": f"Cuadrilla de {role} {i // 6 + 1}", "kind": "cuadrilla", "max_units": 2, "skills": [role], "calendar_id": cid, "cost_hour": 16000 + i * 1500, "overtime_cost_hour": (16000 + i * 1500) * 1.5, "max_overtime_hours_day": 2} for i, role in enumerate(skills * 2)])
        cal = WorkCalendar(repo.get(conn, "calendars", cid), start, date(2027, 12, 31))
        rows = []
        for index in range(80):
            phase = min(index // 14, 5)
            duration = 0 if index == 79 else 2 + index % 8
            planned = cal.next_workday(start + timedelta(days=index * 3))
            row = {"project_id": project.id, "code": f"SE-{index + 1:03d}", "name": "Hito: recepción de la subestación" if duration == 0 else phases[phase][1][index % 7] + f" — frente {index % 2 + 1}", "wbs_id": wbs[phase].id, "duration_days": duration, "work_hours": duration * 16, "cost_budget": duration * 16 * resources[phase].cost_hour, "calendar_id": cid, "planned_start": planned, "planned_finish": cal.add_workdays(planned, duration), "macro_month": planned.strftime("%Y-%m") if index % 9 == 0 else None, "required_skill": skills[phase], "crew_size": 2}
            rows.append(row)
        activities = repo.create_many(conn, "activities", rows)
        repo.create_many(conn, "relations", [{"project_id": project.id, "pred_id": activities[i - 1].id, "succ_id": activities[i].id, "type": "SS" if i % 4 == 0 else "FS", "lag_days": 2 if i % 4 == 0 else 0} for i in range(1, 80)])
        repo.create_many(conn, "assignments", [{"activity_id": activity.id, "resource_id": resources[min(i // 14, 5) + (i % 2) * 6].id, "skill": activity.required_skill, "units": 2, "work_hours": activity.work_hours} for i, activity in enumerate(activities) if activity.duration_days])
        return project


_DEMO_MT_TYPES = [
    # (nombre, frecuencia, duración días, HH, habilidad, requiere desconexión, instalación S/E o línea, meses preferidos)
    ("Inspección visual de patio y equipos", "M", 1, 4, "SSEE", False, "SE", ""),
    ("Termografía de conexiones y barras", "Q", 1, 6, "SSEE", False, "SE", ""),
    ("Revisión de banco de baterías y cargador", "M", 1, 3, "Control", False, "SE", ""),
    ("Verificación de SCADA / RTU y alarmas", "Q", 1, 6, "Control", False, "SE", ""),
    ("Análisis de aceite (DGA) transformador de poder", "S", 1, 4, "Transformadores", False, "SE", "3,9"),
    ("Mantenimiento de cambiador de tomas bajo carga", "A", 2, 24, "Transformadores", True, "SE", ""),
    ("Pruebas de relés de protección (inyección secundaria)", "A", 2, 16, "Protecciones", True, "SE", ""),
    ("Mantenimiento de interruptor de poder", "A", 2, 24, "SSEE", True, "SE", ""),
    ("Inspección y ajuste de desconectadores", "S", 1, 8, "SSEE", True, "SE", ""),
    ("Medición de resistencia de malla de puesta a tierra", "A", 1, 8, "SSEE", False, "SE", ""),
    ("Pruebas de transformadores de medida (TT/CC)", "A", 1, 12, "Protecciones", True, "SE", ""),
    ("Inspección terrestre de línea y franja de servidumbre", "Q", 2, 16, "Líneas", False, "LT", ""),
    ("Lavado de aislación en vivo / desenergizado", "S", 1, 12, "Líneas", True, "LT", "4,10"),
    ("Inspección termográfica de línea", "S", 1, 8, "Líneas", False, "LT", ""),
    ("Poda y control de vegetación en franja", "A", 3, 32, "Líneas", False, "LT", ""),
]
_FREQ_PER_YEAR = {"W": 52, "M": 12, "Q": 4, "S": 2, "A": 1}


def demo_maintenance(n: int = 10000, conn: sqlite3.Connection | None = None) -> d.Project:
    """Plan de mantenimiento realista: tipos de tarea por S/E y línea, escalado hasta n ocurrencias en 2026."""
    if not 1 <= n <= 100000:
        raise ValueError("La demo admite entre 1 y 100 000 ocurrencias.")
    generate_occurrences, _ = _maintenance()
    if conn is None:
        connection = db.connect()
        try:
            db.initialize(connection)
            return demo_maintenance(n, connection)
        finally:
            connection.close()
    with repo.transaction(conn):
        all_calendars = repo.list_entities(conn, "calendars")
        cid = next((c.id for c in all_calendars if c.name == "Chile L-V 8h"), all_calendars[0].id)
        shift = next((c.id for c in all_calendars if c.shift_pattern == "7x7"), cid)
        start, end = date(2026, 1, 1), date(2026, 12, 31)
        project = repo.create(conn, "projects", {"name": f"Plan de mantenimiento S/E y líneas 2026 — {n:,} actividades".replace(",", "."), "mode": "mantenimiento", "start_date": start, "end_date": end, "data_date": start, "calendar_id": cid, "description": "Demo: mantenimiento preventivo de subestaciones y líneas de transmisión (datos ficticios)."})
        se_types = [t for t in _DEMO_MT_TYPES if t[6] == "SE"]
        lt_types = [t for t in _DEMO_MT_TYPES if t[6] == "LT"]
        per_se = sum(_FREQ_PER_YEAR[t[1]] for t in se_types)
        per_lt = sum(_FREQ_PER_YEAR[t[1]] for t in lt_types)
        # proporción 3 S/E por cada línea
        units = max(1, -(-int(n * 1.3) // (3 * per_se + per_lt)))  # margen: primeras fechas desfasadas generan menos ocurrencias
        n_se, n_lt = 3 * units, units
        voltages = [220, 154, 110, 66]
        rows, hh_by_skill = [], {}
        def add(t, loc, kv, k):
            name, freq, dur, hh, skill, outage, _, months = t
            rows.append({"project_id": project.id, "code": f"MT-{len(rows) + 1:05d}", "name": name, "frequency": freq,
                         "first_due": start + timedelta(days=zlib.crc32(f"{loc}|{name}".encode()) % {"W": 7, "M": 28, "Q": 90, "S": 180, "A": 350}[freq]), "duration_days": dur, "work_hours": float(hh),
                         "crew_size": 2 if hh >= 12 else 1, "required_skill": skill, "calendar_id": cid, "location": loc,
                         "equipment": "", "voltage_kv": kv, "requires_outage": outage, "tol_before_days": 10 if freq in "SA" else 5,
                         "tol_after_days": 20 if freq in "SA" else 7, "preferred_months": months, "discipline": "Mantenimiento",
                         "cost_per_occurrence": hh * 18000})
            hh_by_skill[skill] = hh_by_skill.get(skill, 0) + hh * _FREQ_PER_YEAR[freq]
        for i in range(n_se):
            for k, t in enumerate(se_types):
                add(t, f"S/E {i + 1:04d}", voltages[i % 4], i + k)
        for i in range(n_lt):
            for k, t in enumerate(lt_types):
                add(t, f"Línea {voltages[i % 4]} kV Tramo {i + 1:04d}", voltages[i % 4], i + k)
        tasks = repo.create_many(conn, "periodic_tasks", rows)
        calendars = {c.id: WorkCalendar(c, start, end) for c in all_calendars}
        occurrences = generate_occurrences(tasks, start, end, calendars, cid)
        # exactamente n: se conservan planes completos por instalación y se recorta solo la última
        occurrences.sort(key=lambda o: o.code)
        trimmed_tasks = {o.periodic_task_id for o in occurrences[n:]}
        occurrences = occurrences[:n]
        kept = {o.periodic_task_id for o in occurrences}
        # La regeneración debe respetar el recorte de la demo, también al resolver.
        for tid in kept & trimmed_tasks:
            last_due = max(o.due_date for o in occurrences if o.periodic_task_id == tid)
            repo.update(conn, "periodic_tasks", tid, {"horizon_end": last_due})
        unused = [(t.id,) for t in tasks if t.id not in kept]
        if unused:
            conn.executemany("DELETE FROM periodic_tasks WHERE id=?", unused)
        # dotación según la demanda real conservada: ~1 800 HH/año por técnico, utilización objetivo 85 %
        hh_by_skill = {}
        for o in occurrences:
            hh_by_skill[o.required_skill] = hh_by_skill.get(o.required_skill, 0) + o.work_hours
        resources, idx = [], 0
        for skill, hh in sorted(hh_by_skill.items()):
            for j in range(max(2, -(-int(hh) // 1300))):
                on_shift = j % 4 == 3
                resources.append({"project_id": project.id, "name": f"Técnico {skill} {j + 1:02d}", "kind": "trabajador", "skills": [skill],
                                  "calendar_id": shift if on_shift else cid, "capacity_hours_day": 12 if on_shift else 8,
                                  "cost_hour": 15000 + (idx % 5) * 2000, "overtime_cost_hour": 24000 + (idx % 5) * 3000, "max_overtime_hours_day": 2})
                idx += 1
        repo.create_many(conn, "resources", resources)
        repo.save_occurrences(conn, project.id, occurrences, return_entities=False)
        return project
