"""Lectura especializada de control, equivalente a las vistas del servicio.

Lee columnas consumidas, resuelve el historial en una pasada en orden de ID y
omite asignaciones (EVM/curva S no las consumen). No modifica datos ni fórmulas.
"""
from datetime import date
import json
from functools import lru_cache
from collections import namedtuple
from dataclasses import fields

from .. import domain as d, repo, services
from ..calendars import WorkCalendar


# Tuplas inmutables sin diccionario por registro para el panel numérico. La
# lectura pública conserva los objetos completos del dominio por defecto.
_Activity = namedtuple('_Activity', [f.name for f in fields(d.ActivityView)])
_Progress = namedtuple('_Progress', [f.name for f in fields(d.ProgressEntry)])


def control_inputs(conn, pid, *, activity_ids=None, context=None, compact=False, basis='hours'):
    if compact and context is None:
        from .columnar import panel_inputs
        return panel_inputs(conn, pid, basis)
    project = context['project'] if context is not None else repo.get(conn, 'projects', pid)
    selected = tuple(sorted(set(activity_ids))) if activity_ids is not None else None
    predicate = '' if selected is None else ' AND id IN (SELECT value FROM json_each(?))'
    params = (pid,) if selected is None else (pid, json.dumps(selected))
    cutoff = project.data_date or date.today()
    compact = context.get('_compact', compact) if context is not None else compact
    activity_type, progress_type = (_Activity, _Progress) if compact else (d.ActivityView, d.ProgressEntry)

    @lru_cache(maxsize=8192)
    def parsed(value):
        return date.fromisoformat(value) if value else None

    progress, recorded, summary = [], set(), {}
    rows = conn.execute('''SELECT id,activity_id,date,percent_complete,actual_hours,
        actual_cost,resource_id,actual_start,actual_finish,note FROM progress
        WHERE activity_id IN (SELECT id FROM activities WHERE project_id=?''' + predicate + ') ORDER BY id', params)
    for rid, aid, day, pct, hours, cost, resource, start, finish, note in rows:
        entry = progress_type(rid, aid, parsed(day), pct, hours, cost, resource,
                                parsed(start), parsed(finish), note)
        progress.append(entry)
        recorded.add(aid)
        if entry.date > cutoff:
            continue
        state = summary.get(aid)
        if state is None:
            state = summary[aid] = [entry, 0.0, 0.0, None]
        elif (entry.date, entry.id) > (state[0].date, state[0].id):
            state[0] = entry
        # Mismo orden de sumas que list_entities(... ORDER BY id) del servicio.
        state[1] += hours
        state[2] += cost
        if entry.actual_start and (state[3] is None or entry.actual_start < state[3]):
            state[3] = entry.actual_start

    acts = []
    description = "'',''" if compact else "code,name"
    rows = conn.execute('''SELECT id,''' + description + ',' + '''COALESCE(planned_start,early_start),
        COALESCE(planned_finish,early_finish),duration_days,work_hours,cost_budget,
        percent_complete,actual_start,actual_finish,actual_hours,actual_cost,calendar_id,wbs_id
        FROM activities WHERE project_id=?''' + predicate + ' ORDER BY id', params)
    for aid, code, name, start, finish, duration, work, budget, pct, actual_start, actual_finish, hours, cost, cid, wid in rows:
        actual_start, actual_finish = parsed(actual_start), parsed(actual_finish)
        state = summary.get(aid)
        if aid in recorded and state is None:
            pct, hours, cost = 0.0, 0.0, 0.0
            actual_start = actual_finish = None
        if state is not None:
            latest, hours, cost, earliest = state
            pct = latest.percent_complete
            actual_start = earliest or actual_start
            actual_finish = latest.actual_finish or (latest.date if pct >= 100 else None)
        if actual_start and actual_start > cutoff:
            if aid not in recorded:
                pct, hours, cost = 0.0, 0.0, 0.0
                actual_finish = None
            actual_start = None
        if actual_finish and actual_finish > cutoff:
            actual_finish = None
        acts.append(activity_type(aid, code, name, parsed(start) or actual_start,
            parsed(finish) or actual_finish, duration, work, budget, pct,
            actual_start, actual_finish, hours, cost, cid or project.calendar_id, wid, () if compact else []))
    if context is not None:
        return dict(context, acts=acts, progress=progress)
    baseline = services._baseline(conn, project)
    dates = [value for a in acts for value in (a.start, a.finish, a.actual_start, a.actual_finish) if value]
    dates.extend(value for a in (baseline or {}).values() for value in (a.start, a.finish) if value)
    dates.extend(entry.date for entry in progress)
    lower = min([project.start_date, cutoff] + dates)
    upper = max([project.end_date or cutoff, cutoff] + dates)
    upper = date.fromordinal(min(date.max.toordinal(), upper.toordinal() + 3660))
    cid = project.calendar_id or repo.list_entities(conn, 'calendars')[0].id
    calendars = {key: WorkCalendar(repo.get(conn,'calendars',key), lower, upper)
                 for key in {cid, *(a.calendar_id for a in acts if a.calendar_id is not None)}}
    return dict(project=project, progress=progress, acts=acts, cutoff=cutoff,
                baseline=baseline, calendar=calendars[cid], calendars=calendars, _compact=compact)
