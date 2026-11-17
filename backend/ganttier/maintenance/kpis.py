"""Cumplimiento del plan comprometido y backlog de ocurrencias al corte."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import fsum

from .. import repo


AGE_BANDS = ("0–7", "8–30", "31–90", ">90")


def _compliance(rows):
    due = [r for r in rows if r["due_at_cutoff"]]
    total = len(due)
    completed = sum(r["completed"] for r in due)
    unknown = sum(r["completed"] and r["actual_finish"] is None for r in due)
    timely = sum(r["on_time"] for r in due)
    return {
        "status": "calculado" if total else "sin datos",
        "population": len(rows), "due": total, "completed": completed,
        "completed_on_time": timely, "completion_date_missing": unknown,
        "on_time_status": "sin datos" if not total or unknown else "calculado",
        "on_time_pct": 100 * timely / total if total and not unknown else None,
        "total_pct": 100 * completed / total if total else None,
    }


def _backlog_totals(rows):
    return {"count": len(rows), "remaining_hours": fsum(r["remaining_hours"] for r in rows)}


def _group(rows, key, summarize):
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return [{"key": key, **summarize(members)} for key, members in sorted(groups.items())]


def kpis(conn, pid: int, start: date | None = None, end: date | None = None,
         *, as_of: date | None = None, project=None, progress=None, views=None) -> dict:
    """El período filtra due_date; end nunca sustituye la fecha de corte.

    La tolerancia es tol_after_days (días corridos). Un 100 % sin fecha real
    permite contar la terminación, pero no demostrar su puntualidad.
    Las entradas opcionales son de esta petición y del mismo corte; no se cachean.
    """
    project = repo.get(conn, "projects", pid) if project is None else project
    cutoff = as_of or project.data_date or date.today()
    start, end = start or project.start_date, end or cutoff
    if end < start:
        raise ValueError("El fin del período debe ser igual o posterior al inicio.")
    progress = repo.list_entities(conn, "progress", pid) if progress is None else progress
    recorded, latest_at_cut = set(), {}
    for entry in progress:
        recorded.add(entry.activity_id)
        if entry.date <= cutoff:
            previous = latest_at_cut.get(entry.activity_id)
            if previous is None or (entry.date, entry.id) > (previous.date, previous.id):
                latest_at_cut[entry.activity_id] = entry
    rows = []
    # Filtrar el compromiso antes de materializar objetos. La tolerancia se une
    # por proyecto, igual que el anterior diccionario de tareas del proyecto.
    occurrences = conn.execute("""SELECT a.id,a.code,a.name,a.due_date,a.location,
        a.required_skill,a.priority,a.work_hours,a.percent_complete,a.actual_finish,
        COALESCE(t.tol_after_days,0)
        FROM activities a LEFT JOIN periodic_tasks t
        ON t.id=a.periodic_task_id AND t.project_id=a.project_id
        WHERE a.project_id=? AND a.periodic_task_id IS NOT NULL
        AND a.due_date>=? AND a.due_date<=? ORDER BY a.id""",
        (pid, start.isoformat(), end.isoformat()))
    for aid, code, name, due, location, skill, priority, work, percent, actual_finish, tolerance in occurrences:
        due = date.fromisoformat(due)
        latest = latest_at_cut.get(aid)
        # No usar la fecha de registro que build_activity_views infiere como fin.
        finish = latest.actual_finish if latest else date.fromisoformat(actual_finish) if actual_finish else None
        if latest:
            percent = latest.percent_complete
        elif aid in recorded:
            finish, percent = None, 0.0
        future_finish = finish is not None and finish > cutoff
        if future_finish:
            finish = None
        if views is not None:
            percent = views[aid].percent_complete
        percent = max(0.0, min(100.0, percent))
        completed = not future_finish and (finish is not None or percent >= 100)
        tolerance = max(0, tolerance)
        age = (cutoff - due).days
        rows.append({
            "activity_id": aid, "code": code, "name": name,
            "due_date": due, "month": due.strftime("%Y-%m"),
            "location": location.strip() or "Sin instalación",
            "skill": skill.strip() or "Sin habilidad",
            "priority": "alta" if priority >= 70 else "media" if priority >= 40 else "baja",
            "percent_complete": percent, "actual_finish": finish,
            "due_at_cutoff": age >= 0, "completed": completed,
            "on_time": bool(completed and finish and (finish - due).days <= tolerance),
            "tolerance_days": tolerance, "age_days": age,
            "age_band": AGE_BANDS[0 if age <= 7 else 1 if age <= 30 else 2 if age <= 90 else 3],
            "remaining_hours": max(0.0, work) * (1 - percent / 100),
        })
    pending = [r for r in rows if r["due_at_cutoff"] and not r["completed"]]
    compliance = _compliance(rows)
    return {
        "status": compliance["status"], "project_id": pid, "data_date": cutoff,
        "start": start, "end": end, "compliance": compliance,
        "by_month": _group(rows, "month", _compliance),
        "by_location": _group(rows, "location", _compliance),
        "by_skill": _group(rows, "skill", _compliance),
        "backlog": {
            "status": compliance["status"], **_backlog_totals(pending),
            "by_age": [{"key": band, **_backlog_totals([r for r in pending if r["age_band"] == band])}
                       for band in AGE_BANDS],
            "by_priority": [{"key": level, **_backlog_totals([r for r in pending if r["priority"] == level])}
                            for level in ("alta", "media", "baja")],
            "by_location": _group(pending, "location", _backlog_totals),
            "by_age_days": _group(pending, "age_days", _backlog_totals),
            "oldest": sorted(pending, key=lambda r: (r["due_date"], r["activity_id"]))[:20],
        },
    }
