"""Diagnóstico de solo lectura; las sobrecargas de un perfil no prueban infactibilidad."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, fields, replace
from datetime import date, timedelta
from math import ceil
import sqlite3
from time import perf_counter

import numpy as np

from .. import domain as d, services
from ..calendars import WorkCalendar
from ..engine import validate_network
from ..solve import _intensity_violations


def diagnosticar(conn: sqlite3.Connection, pid: int, *, desde: date | None = None,
                 hasta: date | None = None, periodo: str = "mes") -> dict:
    """Explica capacidad normal, demanda remanente y pruebas necesarias sin escribir.

    Las tareas sin fechas se ubican provisionalmente desde su ventana/corte; esa
    carga es orientativa. La capacidad por habilidad es compartida si hay personas
    polivalentes. Se permiten horas extra para las pruebas de imposibilidad.
    Coste del perfil: O(asignaciones + actividades + calendarios×días + recursos×días),
    sin matriz ni bucle de actividades×días. Incluye los tiempos de cada fase.
    """
    started = perf_counter()
    periods = {"dia": "day", "semana": "week", "mes": "month"}
    if periodo not in periods:
        raise ValueError("El período debe ser dia, semana o mes.")
    problem = services.build_problem(conn, pid)
    built = perf_counter()
    first, last = desde or problem.horizon_start, hasta or problem.horizon_end
    if last < first:
        raise ValueError("El fin del horizonte no puede ser anterior al inicio.")
    problem = replace(problem, horizon_start=first, horizon_end=last,
                      calendars={cid: WorkCalendar(cal.cal, first, last)
                                 for cid, cal in problem.calendars.items()})
    warnings, team_summary, team_violations = [], [], []
    generated_count = 0
    if problem.project.mode == "mantenimiento" and problem.periodic_tasks:
        from ..maintenance import generate_occurrences
        from ..teams import summarize_occurrences
        generated = generate_occurrences(problem.periodic_tasks, first, last, problem.calendars,
                                         problem.default_calendar_id, resources=problem.resources,
                                         warnings=warnings)
        # La conversión es exclusivamente en memoria; conserva toda ocurrencia existente.
        codes = {a.code for a in problem.activities}
        names = {f.name for f in fields(d.Activity)} - {"id", "project_id"}
        for index, occurrence in enumerate(generated, 1):
            if occurrence.code in codes:
                continue
            values = {k: v for k, v in asdict(occurrence).items() if k in names}
            values.update(planned_start=None, planned_finish=None)
            activity = d.Activity(-index, pid, **values)
            problem.activities.append(activity)
            rid = occurrence.team_id or occurrence.resource_id
            if rid is not None:
                problem.assignments.append(d.Assignment(-index, activity.id, rid,
                    occurrence.required_skill, occurrence.crew_size, occurrence.work_hours))
            generated_count += 1
        # Usar ocurrencias efectivas: una ficha persistida puede diferir de su plantilla.
        occurrences = services._occurrences(problem, {t.id: t.resource_id for t in problem.periodic_tasks})
        if any(o.team_id is not None for o in occurrences):
            try:
                team_summary, extra, team_violations = summarize_occurrences(
                    occurrences, problem.resources, problem.calendars,
                    problem.periodic_tasks, allow_overtime=True)
                warnings.extend(extra)
            except (ValueError, KeyError, ZeroDivisionError) as exc:
                warnings.append(f"No se pudo evaluar el reparto por equipos: {exc}")
    network_errors = validate_network(problem)
    intensity = _intensity_violations(problem, d.OptimizeOptions(allow_overtime=True))
    checked = perf_counter()
    count = (last - first).days + 1
    labels = []
    for n in range(count):
        day = first + timedelta(days=n)
        if periodo == "semana":
            day -= timedelta(days=day.weekday())
        elif periodo == "mes":
            day = day.replace(day=1)
        labels.append(day.isoformat())
    labels, inverse = np.unique(labels, return_inverse=True)
    vectors = {cid: cal.day_hours_vector() for cid, cal in problem.calendars.items()}
    capacity = defaultdict(lambda: np.zeros(count))
    members = defaultdict(set)
    resources = {r.id: r for r in problem.resources}
    acts = {a.id: a for a in problem.activities}
    rows = defaultdict(list)
    for row in problem.assignments:
        rows[row.activity_id].append(row)
    # Misma regla de capacidad que engine.loads y el verificador independiente.
    # Las ausencias parciales, feriados y turnos ya están en el vector del calendario.
    resource_capacity = {}
    for resource in problem.resources:
        cid = resource.calendar_id or problem.default_calendar_id
        if not resource.active or cid not in vectors:
            continue
        resource_capacity[resource.id] = np.minimum(vectors[cid], resource.capacity_hours_day) * resource.max_units
        for skill in resource.skills or ["Sin habilidad"]:
            members[skill].add(resource.id)
    for row in problem.assignments:
        resource, activity = resources.get(row.resource_id), acts.get(row.activity_id)
        if resource and resource.active and activity:
            skill = row.skill or activity.required_skill or next(iter(resource.skills), "Sin habilidad")
            members[skill].add(resource.id)
    for skill, ids in members.items():
        for rid in ids:
            if rid in resource_capacity:
                capacity[skill] += resource_capacity[rid]
    positive_resources = {r.id for r in problem.resources if r.active and r.max_units > 0
                          and r.capacity_hours_day + r.max_overtime_hours_day > 0}
    eligible_by_skill = {skill: tuple(resources[rid] for rid in ids if rid in positive_resources)
                         for skill, ids in members.items()}
    grouped = defaultdict(list)
    hard, missing, issues, demand_without_dates = [], defaultdict(set), [], 0
    proofs = []
    cutoff = problem.data_date or problem.project.start_date
    floor = max(first, d.schedule_floor(cutoff, problem.project.start_date))
    for activity in problem.activities:
        if activity.actual_finish or activity.percent_complete >= 100:
            continue
        cid = activity.calendar_id or problem.default_calendar_id
        if cid not in problem.calendars:
            continue
        cal = problem.calendars[cid]
        duration = (ceil(activity.remaining_duration if activity.remaining_duration is not None
                         else activity.duration_days * (1 - activity.percent_complete / 100))
                    if activity.actual_start else activity.duration_days)
        fraction = 1 - activity.percent_complete / 100 if activity.actual_start else 1.0
        sources = rows[activity.id] or [d.Assignment(0, activity.id, activity.team_id,
                                                   activity.required_skill, activity.crew_size, activity.work_hours)]
        has_work = any(row.work_hours > 0 for row in sources)
        if duration == 0 and has_work:
            issue = dict(activity_id=activity.id, code=activity.code, type="milestone_work",
                         detail="Hay HH remanentes con duración cero; aumente la duración o quite las HH del hito.")
            issues.append(issue)
            proofs.append(issue)
        elif not has_work and duration > 0:
            issues.append(dict(activity_id=activity.id, code=activity.code, type="implicit_work",
                               detail="Sin HH explícitas: se estima demanda con dotación × horas laborables; confirme HH o dotación."))
        lower = max(first, d.remaining_work_start(cutoff, activity.actual_start)) if activity.actual_start else floor
        upper = last
        if activity.window_hard:
            lower = max(lower, activity.window_start or lower)
            upper = min(upper, activity.window_end or upper)
        if activity.constraint_date:
            if activity.constraint_type in {"MSO", "SNET"} and not activity.actual_start:
                lower = max(lower, activity.constraint_date)
            if activity.constraint_type == "MFO":
                upper = min(upper, activity.constraint_date)
        # Solo límites duros; una fecha fin de Proyecto no es por sí sola un límite.
        available = cal.workdays_between(lower, upper) if first <= lower <= upper <= last else 0
        if lower > upper or (duration > 0 and available < duration):
            conflict = dict(activity_id=activity.id, code=activity.code,
                            start=lower.isoformat(), end=upper.isoformat(), required_days=duration,
                            available_days=available, type="hard_window" if activity.window_hard else "horizon",
                            detail=f"{activity.code}: requiere {duration} días y dispone de {available}; amplíe ventana/horizonte o reduzca duración después del corte.")
            hard.append(conflict)
        start = activity.planned_start or activity.early_start
        finish = activity.planned_finish or activity.early_finish
        if start and start < lower and not activity.locked:
            start = finish = None
        if activity.actual_start:
            start, finish = lower, None
        if not start or not finish:
            demand_without_dates += 1
            preferred = activity.window_start or lower
            start = max(lower, preferred if preferred <= upper else lower)
            try:
                start = cal.next_workday(start)
                finish = cal.add_workdays(start, max(0, duration))
            except ValueError:
                start = finish = None
        explicit_demands = defaultdict(float)
        explicit_caps = {}
        for row in sources:
            resource = resources.get(row.resource_id)
            skill = row.skill or activity.required_skill or (next(iter(resource.skills), "Sin habilidad") if resource else "Sin asignar")
            work, units = row.work_hours * fraction, row.units if not row.work_hours else 0
            if duration <= 0 or not (work or units):
                continue
            if row.resource_id is not None:
                eligible = [resource] if resource and resource.id in positive_resources else []
            else:
                eligible = eligible_by_skill.get(skill, ())
            if not eligible:
                missing[skill].add(activity.id)
                if skill != "Sin asignar":
                    proofs.append(dict(type="missing_resource", activity_id=activity.id, skill=skill))
            if rows[activity.id] and work > 0 and duration > 0 and eligible:
                key = (row.resource_id, skill)
                explicit_demands[key] += work
                explicit_caps[key] = sum((r.capacity_hours_day + r.max_overtime_hours_day) * r.max_units for r in eligible)
            if start and finish and finish >= first and start <= last:
                grouped[(skill, cid)].append((start, finish, work, units))
        for key, work in explicit_demands.items():
            cap = explicit_caps[key]
            if work > cap * duration + 1e-6:
                intensity.append(dict(type="intensity", activities=[activity.id], date=None,
                    detail=f"{activity.code}: la asignación {key[1]} requiere {work:g} HH en {duration} días, "
                           f"superando {cap:g} HH/día incluso con horas extra; aumente duración o dotación."))
    demand = defaultdict(lambda: np.zeros(count))
    # Diferencias por calendario/habilidad: O(actividades), acumulación NumPy por día.
    for (skill, cid), entries in grouped.items():
        cal = problem.calendars[cid]
        diff = np.zeros(count + 1)
        diff_units = np.zeros(count + 1)
        lefts, rights, amounts, unit_amounts = [], [], [], []
        # Proyecto distribuye por horas; mantenimiento, por jornadas (contrato motor).
        base = (vectors[cid] > 0).astype(float) if problem.project.mode == "mantenimiento" else vectors[cid]
        full = cal._hours if problem.project.mode == "proyecto" else cal._working
        prefix = np.r_[0.0, np.cumsum(full)]
        for start, finish, work, units in entries:
            try:
                total = prefix[cal._index(finish) + 1] - prefix[cal._index(start)]
            except ValueError:
                warnings.append("Hay fechas fuera del calendario; revise el horizonte del programa vigente.")
                continue
            if total <= 0:
                continue
            lefts.append(max(0, (start - first).days))
            rights.append(min(count, (finish - first).days + 1))
            amounts.append(work / total if work else 0)
            unit_amounts.append(units if not work else 0)
        np.add.at(diff, np.asarray(lefts, dtype=int), amounts)
        np.add.at(diff, np.asarray(rights, dtype=int), -np.asarray(amounts))
        np.add.at(diff_units, np.asarray(lefts, dtype=int), unit_amounts)
        np.add.at(diff_units, np.asarray(rights, dtype=int), -np.asarray(unit_amounts))
        demand[skill] += np.cumsum(diff[:-1]) * base + np.cumsum(diff_units[:-1]) * vectors[cid]
    table = []
    for skill in sorted(capacity.keys() | demand.keys()):
        caps = np.bincount(inverse, weights=capacity[skill], minlength=len(labels))
        loads = np.maximum(0, np.bincount(inverse, weights=demand[skill], minlength=len(labels)))
        table.extend(dict(skill=skill, period=str(label), capacity_hours=float(cap), demand_hours=float(work),
                          utilization=float(work / cap) if cap else None)
                     for label, cap, work in zip(labels, caps, loads))
    profiled = perf_counter()
    # Exponer también el perfil vigente común, distinguiéndolo de la estimación remanente.
    has_dates = any((a.planned_start or a.early_start) and (a.planned_finish or a.early_finish)
                    for a in problem.activities if a.id > 0)
    current = services.load_profile(conn, pid, "skill", periods[periodo], first, last) if has_dates and not network_errors else None
    advice = [v["detail"] for v in intensity + hard] + [i["detail"] for i in issues[:20]]
    if missing:
        advice.append("Asigne recursos activos a las habilidades sin cobertura o corrija la habilidad/recurso de la demanda: " + ", ".join(sorted(missing)))
    if network_errors:
        advice.append("Corrija las relaciones, ciclos y datos inválidos antes de optimizar: " + "; ".join(network_errors[:10]))
    overloaded = any(r["demand_hours"] > r["capacity_hours"] + 1e-6 for r in table)
    if overloaded:
        advice.append("Redistribuya fechas o aumente dotación/horizonte en períodos sobrecargados; esta carga provisional no demuestra infactibilidad.")
    if demand_without_dates:
        warnings.append(f"{demand_without_dates} actividades usan fechas provisionales para el perfil; no es una programación verificada.")
    warnings.append("La capacidad de personas polivalentes figura en cada habilidad y no se puede sumar entre habilidades.")
    if team_summary:
        advice.append("Revise el reparto por equipos, cantidad por lote, productividad y ventanas en team_summary; ampliar dotación sin revisar equidad puede no bastar.")
    proven = bool(intensity or hard or proofs or team_violations)
    ended = perf_counter()
    return dict(project=d.to_dict(problem.project), capacity_by_skill=table,
                intensity_violations=intensity, hard_window_conflicts=hard,
                unassigned_skills=[dict(skill=k, activities=sorted(v)) for k, v in sorted(missing.items())],
                data_issues=issues, network_errors=network_errors, team_summary=team_summary,
                team_violations=team_violations, proofs=proofs, advice=list(dict.fromkeys(advice)),
                warnings=list(dict.fromkeys(warnings)),
                feasibility_hint="demostrada_infactible" if proven else "incierta" if network_errors or missing or issues or demand_without_dates or overloaded else "probable",
                assumptions=dict(allow_overtime_for_proofs=True, capacity="normal", demand="remanente con fechas provisionales si faltan",
                                 horizon_start=first.isoformat(), horizon_end=last.isoformat()),
                current_load_profile=asdict(current) if current else None,
                counts=dict(activities=len(problem.activities), generated_in_memory=generated_count,
                            assignments=len(problem.assignments), resources=len(resources)),
                timings=dict(build_problem_s=built-started, checks_s=checked-built,
                             profile_s=profiled-checked, current_profile_s=ended-profiled, total_s=ended-started))
