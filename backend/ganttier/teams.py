"""Equipos de trabajo: cuadrillas identificadas, calendarios y evidencia de reparto."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from math import isfinite, lcm
import json
import re

from . import domain as d, repo
from .calendars import WorkCalendar


def equity_policy(task, resources, calendars):
    """Frecuencia menor que el ciclo: equidad acumulada, con ventana natural del período."""
    definitions = [calendars[r.calendar_id].cal for r in resources]
    lengths = [sum(map(int, c.shift_pattern.split('x'))) if c.shift_pattern else 7 for c in definitions]
    cycle = lcm(*lengths) if lengths else 7
    step = {"D": 1, "DH": 1, "ND": task.interval, "W": 7, "NW": 7*task.interval,
            "M": 28, "NM": 28*task.interval, "Q": 84, "S": 168, "A": 365, "NA": 365*task.interval}[task.frequency]
    anchor = min((c.shift_anchor if c.shift_pattern else task.first_due-timedelta(days=task.first_due.weekday())
                  for c in definitions), default=task.first_due)
    return step < max(lengths, default=7), cycle, anchor, step


def validate_batch_fields(values):
    """Opt-in completo; nunca interpreta un formulario parcial como tarea tradicional."""
    for key in ("productivity_hour_team", "productive_hours_day"):
        v = values.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int,float)) or not isfinite(v) or v <= 0):
            raise ValueError(f"{key} debe ser positivo y finito.")
    if values.get("productive_hours_day", 0) and values["productive_hours_day"] > 24:
        raise ValueError("Las horas productivas no pueden superar 24.")
    if values.get("is_daily_round"):
        if values.get("frequency") != "D" or not values.get("is_route") or not values.get("team_ids") or not values.get("first_due"):
            raise ValueError("La ronda diaria requiere ruta, frecuencia D, equipos y primera fecha.")
        if any(values.get(k) is not None for k in ("quantity_total", "batch_size", "productivity_day_team", "productivity_hour_team")):
            raise ValueError("La ronda diaria usa HH fijas; no admite lotes ni rendimientos.")
        return False
    fields = ("quantity_total", "batch_size")
    rate_field = "productivity_hour_team" if values.get("productivity_hour_team") is not None else "productivity_day_team"
    fields += (rate_field,)
    enabled = any(values.get(k) is not None for k in fields) or bool(values.get("team_ids")) or bool(values.get("unit"))
    if not enabled:
        return False
    for field in fields:
        value = values.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
            raise ValueError(f"Reparto por cantidad: {field} debe ser positivo y finito.")
    if not str(values.get("unit", "")).strip():
        raise ValueError("Reparto por cantidad: indique la unidad.")
    ids = values.get("team_ids")
    if not isinstance(ids, list) or not ids or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Reparto por cantidad: seleccione equipos distintos y válidos.")
    if not values.get("first_due"):
        raise ValueError("Reparto por cantidad: Primera_Fecha es obligatoria para mantener estable la rotación.")
    if values.get("resource_id") is not None:
        raise ValueError("El reparto por equipos no admite simultáneamente un trabajador fijo.")
    return True


def team_view(resource, calendar):
    return dict(id=resource.id, project_id=resource.project_id, name=resource.name,
                regime=calendar.shift_pattern or "5x2", cycle_start=calendar.shift_anchor,
                members=int(resource.max_units), hours_day=calendar.hours_per_day,
                holidays_work=not bool(calendar.holidays_country) if not calendar.shift_pattern
                else not calendar.holidays_apply_to_shift, calendar_name=calendar.name,
                calendar_id=calendar.id, capacity_hours_day=resource.capacity_hours_day * resource.max_units)


def list_teams(conn, pid):
    repo.get(conn, "projects", pid)
    calendars = {c.id: c for c in repo.list_entities(conn, "calendars")}
    return [team_view(r, calendars[r.calendar_id]) for r in repo.list_entities(conn, "resources", pid)
            if r.is_work_team and r.project_id == pid]


def save_team(conn, pid, values, team_id=None):
    """Alta/edición atómica del recurso y su calendario. 5x2 siempre significa lunes a viernes."""
    project = repo.get(conn, "projects", pid)
    if project.mode != "mantenimiento":
        raise ValueError("Los equipos de trabajo pertenecen a planes de mantenimiento.")
    with repo.transaction(conn, immediate=True):
        previous = None
        current = {}
        if team_id is not None:
            previous = repo.get(conn, "resources", team_id)
            if previous.project_id != pid or not previous.is_work_team:
                raise ValueError("El equipo no pertenece al plan.")
            current = team_view(previous, repo.get(conn, "calendars", previous.calendar_id))
        allowed = {"name", "regime", "cycle_start", "members", "hours_day", "holidays_work", "calendar_name"}
        if set(values) - allowed:
            raise ValueError("Campos de equipo desconocidos: " + ", ".join(sorted(set(values)-allowed)))
        source = dict(name="", regime="5x2", cycle_start=project.start_date, members=1, hours_day=8)
        source.update(current)
        source.update(values)
        regime = str(source["regime"]).lower().strip()
        if not re.fullmatch(r"[1-9]\d*x[1-9]\d*", regime):
            raise ValueError("Régimen inválido: use NxM con enteros positivos, por ejemplo 7x7.")
        name = str(source["name"]).strip()
        if not name or "," in name:
            raise ValueError("El nombre del equipo es obligatorio y no puede contener comas (separador de Equipos).")
        members, hours = source["members"], source["hours_day"]
        if isinstance(members, bool) or not isinstance(members, (int, float)) or not isfinite(members) or members < 1 or members != int(members):
            raise ValueError("Integrantes debe ser un entero positivo.")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not isfinite(hours) or not 0 < hours <= 24:
            raise ValueError("Horas_Dia debe ser mayor que cero y no superar 24.")
        anchor = source["cycle_start"]
        anchor = date.fromisoformat(anchor) if isinstance(anchor, str) else anchor
        if not isinstance(anchor, date):
            raise ValueError("Indique una fecha de inicio de ciclo válida.")
        works = source.get("holidays_work")
        if works is None or (current and regime != current["regime"] and "holidays_work" not in values):
            works = regime != "5x2"
        if not isinstance(works, bool):
            raise ValueError("Feriados_Trabajan debe ser SI o NO.")
        if conn.execute("SELECT 1 FROM resources WHERE (project_id=? OR project_id IS NULL) AND name=? AND id<>?", (pid, name, team_id or 0)).fetchone():
            raise ValueError("Ya existe un recurso o equipo con ese nombre en el plan.")
        definition = dict(workweek="1111100" if regime == "5x2" else "1111111",
                          hours_per_day=hours, holidays_country=None if works and regime == "5x2" else "CL",
                          shift_pattern=None if regime == "5x2" else regime, shift_anchor=anchor,
                          holidays_apply_to_shift=not works)
        cid = None
        if previous:
            old = repo.get(conn, "calendars", previous.calendar_id)
            if all(getattr(old, k) == v for k, v in definition.items()):
                cid = old.id
        calendar_name = values.get("calendar_name")
        if calendar_name:
            matches = repo.list_entities(conn, "calendars", filters={"name": calendar_name})
            if not matches or any(getattr(matches[0], k) != v for k, v in definition.items()):
                raise ValueError(f"El calendario {calendar_name} no coincide con la definición del equipo.")
            cid = matches[0].id
        if cid is None:
            suffix = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM calendars").fetchone()[0]
            cid = repo.create(conn, "calendars", dict(name=f"Equipo {pid} · {name} · {suffix}", **definition)).id
        row = dict(name=name, kind="cuadrilla", project_id=pid, is_work_team=True,
                   max_units=members, capacity_hours_day=hours, calendar_id=cid, active=True)
        resource = repo.update(conn, "resources", team_id, row) if previous else repo.create(conn, "resources", row)
        return team_view(resource, repo.get(conn, "calendars", cid))


def _blocks(cal, first, last):
    blocks, length = [], 0
    for n in range(first.toordinal(), last.toordinal()+1):
        if cal.is_workday(date.fromordinal(n)):
            length += 1
        elif length:
            blocks.append(length)
            length = 0
    if length:
        blocks.append(length)
    return blocks


def summarize_occurrences(occurrences, resources, calendars, tasks=(), *, allow_overtime=False):
    """Resumen de demanda y prueba necesaria de capacidad, sin alterar la equidad."""
    resource_map = {r.id: r for r in resources}
    groups = defaultdict(list)
    occurrence_teams = defaultdict(list)
    result, warnings, violations = [], [], []
    blocks_cache = {}
    for o in occurrences:
        if o.team_id is not None:
            groups[(o.periodic_task_id, o.occurrence_index, o.team_id)].append(o)
    for (tid, index, rid), rows in groups.items():
        first = rows[0]
        resource = resource_map[rid]
        key = (first.calendar_id, first.window_start, first.window_end)
        if key not in blocks_cache:
            blocks_cache[key] = _blocks(calendars[first.calendar_id], first.window_start, first.window_end)
        blocks = blocks_cache[key]
        available = sum(blocks)
        daily_hours = calendars[first.calendar_id].cal.hours_per_day
        required = sum(o.work_hours for o in rows) / (resource.max_units * daily_hours)
        durations = sorted((o.duration_days for o in rows), reverse=True)
        full = durations[0]
        n_full = sum(n == full for n in durations)
        small = durations[n_full:]
        slots = sum(n // full for n in blocks)
        fits = n_full <= slots and required <= available
        # Una ocurrencia tiene a lo sumo un lote parcial. Probarlo en cada bloque evita
        # confundir días totales con capacidad de empaquetado continuo.
        if fits and small:
            tail = small[0]
            fits = any(n >= tail and slots - n//full + (n-tail)//full >= n_full for n in blocks)
        hourly = any(o.duration_hours is not None and abs(o.duration_hours-o.duration_days*daily_hours)>1e-8 for o in rows)
        if not first.batch_continuous_in_shift or hourly or allow_overtime:
            capacity = sum(calendars[first.calendar_id].hours(date.fromordinal(n)) for n in range(first.window_start.toordinal(),first.window_end.toordinal()+1) if calendars[first.calendar_id].is_workday(date.fromordinal(n)))
            if allow_overtime:
                capacity += available * resource.max_overtime_hours_day
            fits = sum(o.work_hours for o in rows) <= capacity * resource.max_units + 1e-8
            if first.batch_continuous_in_shift:
                fits = fits and all(o.duration_days <= max(blocks, default=0) for o in rows)
        item = dict(task_id=tid, occurrence_index=index, occurrence=first.code.rsplit("-L", 1)[0],
                    month=first.due_date.strftime("%Y-%m"), due_date=first.due_date,
                    team_id=rid, team=resource.name, quantity=sum(o.quantity or 0 for o in rows),
                    unit=first.unit, batches=sum(o.batch_index is not None for o in rows),
                    rounds=sum(o.batch_index is None for o in rows), required_days=required, available_days=available,
                    required_hours=sum(o.work_hours for o in rows)/resource.max_units,
                    available_hours=sum(calendars[first.calendar_id].hours(date.fromordinal(n)) for n in range(first.window_start.toordinal(),first.window_end.toordinal()+1) if calendars[first.calendar_id].is_workday(date.fromordinal(n))),
                    work_hours=sum(o.work_hours for o in rows), blocks=blocks, feasible_capacity=fits,
                    window_start=first.window_start, window_end=first.window_end, equity_mode=first.equity_mode)
        result.append(item)
        occurrence_teams[(tid, index)].append(item)
        if not fits:
            violations.append(dict(type="team_capacity", activities=[], date=first.due_date.isoformat(),
                detail=f"{item['occurrence']} ({item['month']}), {resource.name}: {required} días requeridos vs {available} disponibles; "
                       f"bloques de turno {blocks}; {len(rows)} lotes no caben (continuidad estricta={first.batch_continuous_in_shift}). No se aplica el programa."))
    task_map = {t.id: t for t in tasks}
    for (tid, _), rows in occurrence_teams.items():
        task = task_map.get(tid)
        if task:
            present = {row["team_id"] for row in rows}
            for rid in task.team_ids:
                if rid in present:
                    continue
                base = rows[0]
                resource = resource_map[rid]
                calendar = calendars[resource.calendar_id]
                blocks = _blocks(calendar, base["window_start"], base["window_end"])
                item = dict(base, team_id=rid, team=resource.name, quantity=0, batches=0, rounds=0, required_days=0, required_hours=0,
                            available_days=sum(blocks),
                            available_hours=sum(calendar.hours(date.fromordinal(n)) for n in range(base["window_start"].toordinal(), base["window_end"].toordinal()+1)
                                                if calendar.is_workday(date.fromordinal(n))),
                            work_hours=0, blocks=blocks, feasible_capacity=True)
                rows.append(item)
                result.append(item)
        if rows[0]["equity_mode"] != "cycle" and len({r["available_days"] for r in rows}) > 1:
            warnings.append(f"{rows[0]['occurrence']} ({rows[0]['month']}): días hábiles distintos: "
                            + "; ".join(f"{r['team']}: {r['available_days']} días" for r in rows)
                            + ". Se conserva el reparto equitativo por lotes; el planificador debe resolver la diferencia.")
    combined = defaultdict(list)
    for row in result:
        combined[(row["team_id"], row["window_start"], row["window_end"])].append(row)
    for (rid, lower, upper), rows in combined.items():
        needed = sum(r["required_days"] for r in rows)
        available = rows[0]["available_days"] * (1 + (resource_map[rid].max_overtime_hours_day / calendars[resource_map[rid].calendar_id].cal.hours_per_day if allow_overtime else 0))
        if len(rows) > 1 and needed > available:
            violations.append(dict(type="team_shared_capacity", activities=[], date=lower.isoformat(),
                detail=f"{resource_map[rid].name}, ventana {lower} a {upper}: {needed} días requeridos vs {available} disponibles "
                       f"al sumar {len(rows)} ocurrencias de las tareas/plantas compartidas. No se aplica el programa."))
    warnings.extend(balance_alerts(result, tasks, resources, calendars))
    return sorted(result, key=lambda r: (r["task_id"], r["occurrence_index"], r["team_id"])), warnings, violations


def balance_alerts(rows, tasks, resources, calendars):
    """Alertas por ciclos/meses civiles completos del horizonte; períodos parciales quedan pendientes."""
    from calendar import monthrange
    from collections import Counter
    names = {r.id: r.name for r in resources}
    result = []
    for task in tasks:
        subset = [r for r in rows if r["task_id"] == task.id and r["equity_mode"] == "cycle"]
        if not subset:
            continue
        members = [r for r in resources if r.id in task.team_ids]
        _, cycle, anchor, _ = equity_policy(task, members, calendars)
        first = max(task.first_due, min(calendars[r.calendar_id].horizon_start for r in members))
        last = min(task.horizon_end or date.max, max(calendars[r.calendar_id].horizon_end for r in members))
        buckets = {}
        for row in subset:
            due = row["due_date"]
            c_start = anchor+timedelta(days=((due-anchor).days//cycle)*cycle)
            m_start = due.replace(day=1)
            periods = [("ciclo", c_start, c_start+timedelta(days=cycle-1)),
                       ("mes", m_start, m_start.replace(day=monthrange(due.year, due.month)[1]))]
            for kind, lower, upper in periods:
                if lower < first or upper > last:
                    continue
                counts = buckets.setdefault((kind, lower, upper), Counter({rid: 0 for rid in task.team_ids}))
                counts[row["team_id"]] += row["batches"]
        for (kind, lower, upper), counts in buckets.items():
            if max(counts.values())-min(counts.values()) > task.balance_tolerance_batches:
                result.append(f"{task.code}: acumulado por {kind} {lower} a {upper} desequilibrado ("
                              + "; ".join(f"{names[rid]}: {count} lotes" for rid, count in counts.items())
                              + f"); tolerancia {task.balance_tolerance_batches} lotes. Revisar manualmente.")
    return result


def summary(conn, pid, first=None, last=None):
    with repo.transaction(conn):
        return _summary(conn, pid, first, last)


def _summary(conn, pid, first=None, last=None):
    """Demanda actual por ocurrencia, contrastada con los lotes guardados y sus fechas."""
    from .maintenance.generator import generate_occurrences
    project = repo.get(conn, "projects", pid)
    tasks = repo.list_entities(conn, "periodic_tasks", pid)
    if not tasks:
        return dict(rows=[], warnings=[], violations=[], status="sin_reparto")
    first, last = first or project.start_date, last or project.end_date
    calendars = {c.id: WorkCalendar(c, first, last) for c in repo.list_entities(conn, "calendars")}
    resources = repo.list_entities(conn, "resources", pid)
    occurrences = generate_occurrences(tasks, first, last, calendars, project.calendar_id or min(calendars), resources=resources)
    rows, warnings, violations = summarize_occurrences(occurrences, resources, calendars, tasks)
    evidence = conn.execute("SELECT value FROM meta WHERE key=?", (f"team_generation:{pid}",)).fetchone()
    if evidence:
        from .services import _scenario_fingerprint
        stored = json.loads(evidence[0])
        if stored.get("scenario_hash") == _scenario_fingerprint(conn, pid):
            warnings = list(dict.fromkeys([*warnings, *(stored.get("warnings") or [])]))
            known = {v["detail"] for v in violations}
            violations += [v for v in stored.get("violations") or [] if v["detail"] not in known]
    saved = defaultdict(list)
    for a in repo.list_entities(conn, "activities", pid):
        if a.team_id is not None:
            saved[(a.periodic_task_id, a.occurrence_index, a.team_id)].append(a)
    for row in rows:
        acts = saved[(row["task_id"], row["occurrence_index"], row["team_id"])]
        scheduled = [a for a in acts if a.planned_start and a.planned_finish]
        row.update(scheduled_batches=sum(a.batch_index is not None for a in scheduled), scheduled_rounds=sum(a.batch_index is None for a in scheduled), scheduled_quantity=sum(a.quantity or 0 for a in scheduled),
                   scheduled_hours=sum(a.work_hours for a in scheduled))
    return dict(rows=rows, warnings=warnings, violations=violations,
                status="infactible" if violations else "demanda")


class _NotApplied(Exception):
    def __init__(self, result):
        self.result = result


def solve_teams(conn, pid, weights, options, apply):
    """Unidad atómica para planes con lotes: si no hay verificación factible, se revierte todo."""
    from .solve import _solve_project
    from . import services
    # El valor histórico de los otros motores no cambia. En planes de equipos
    # las horas extra requieren una opción explícita.
    if not isinstance(options, d.OptimizeOptions):
        options = {"allow_overtime": False, **(options or {})}
    try:
        with repo.transaction(conn, immediate=True):
            previous_ids = {r[0] for r in conn.execute("SELECT id FROM activities WHERE project_id=?", (pid,))}
            previous_hash = services._scenario_fingerprint(conn, pid) if not apply else None
            result = _solve_project(conn, pid, weights, options, apply)
            if not apply:
                result["simulated"] = True
            changed_inventory = not apply and previous_hash != services._scenario_fingerprint(conn, pid)
            if not result.get("feasible") or changed_inventory:
                run_id = result.get("run_id")
                if run_id is not None:
                    (services.DATA_DIR / "runs" / f"{run_id}.json").unlink(missing_ok=True)
                result.update(run_id=None, applied=False)
                raise _NotApplied(result)
    except _NotApplied as exc:
        result = exc.result
    generation = result.get("generation") or {}
    result["created"] = sum(r[0] not in previous_ids for r in conn.execute(
        "SELECT id FROM activities WHERE project_id=?", (pid,))) if result.get("applied") else 0
    generation["created"] = result["created"]
    if apply:
        evidence = {k: result.get(k) for k in ("status", "warnings", "violations", "team_summary")}
        evidence["scenario_hash"] = services._scenario_fingerprint(conn, pid)
        repo.set_meta(conn, f"team_generation:{pid}", json.dumps(repo.json_value(evidence), ensure_ascii=False))
    return result
