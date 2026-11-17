"""Motor opt-in de roles indivisibles y bloques equivalentes.

Un día sintético es un bloque, no una fecha física. Se conserva una persona
durante toda la actividad. Este contrato no acepta trabajo parcial ni avance.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from math import isfinite
from time import perf_counter

from ganttier.domain import Assignment, to_dict
from ganttier.engine.verify import verify_role_schedule


def optimize_role_blocks(problem, options, *, block_hours=4, target_code=None):
    from ortools.sat.python import cp_model
    began = perf_counter()
    if not isfinite(block_hours) or not 0 < block_hours <= 24:
        raise ValueError('block_hours debe estar entre 0 y 24, excluyendo 0.')
    if not isfinite(options.time_limit_s) or options.time_limit_s <= 0:
        raise ValueError('Se requiere un presupuesto positivo y finito.')
    if problem.project.mode != 'proyecto' or options.allow_overtime or not options.respect_resource_limits:
        raise ValueError('Bloques requiere proyecto, capacidad dura y sin horas extra.')
    origin = problem.horizon_start
    horizon = (problem.horizon_end - origin).days + 1
    if not 1 <= horizon <= 2000 or len(problem.activities) > 300:
        raise ValueError('Modelo de bloques limitado a 300 actividades y 2000 bloques.')
    if problem.data_date and problem.data_date > origin:
        raise ValueError('El modo bloques no admite fecha de corte posterior al origen.')
    acts = {a.id: a for a in problem.activities}
    resources = {r.id: r for r in problem.resources if r.active}
    if not acts:
        raise ValueError('No hay actividades.')
    target = next((a.id for a in acts.values() if a.code == target_code), None)
    if target_code is not None and target is None:
        raise ValueError('Actividad objetivo inexistente.')
    # Include the terminal boundary for zero-duration milestones at t=H.
    dates = [origin + timedelta(days=t) for t in range(horizon + 1)]

    def integer(value, label):
        if not isfinite(value) or value < 0 or value != int(value):
            raise ValueError(f'{label} debe ser un entero no negativo.')
        return int(value)

    def hours(cid):
        cal = problem.calendars[cid if cid is not None else problem.default_calendar_id]
        return [cal.hours(day) for day in dates]

    durations = {}
    for a in acts.values():
        durations[a.id] = integer(a.duration_days, 'Duración en bloques')
        if a.actual_start or a.actual_finish or a.percent_complete or a.actual_hours or a.remaining_duration is not None:
            raise ValueError('No se admite avance ni duración remanente en bloques.')
        if a.constraint_type not in {'ASAP', 'MSO', 'MFO', 'SNET', 'FNET', 'SNLT', 'FNLT'}:
            raise ValueError('Restricción no soportada en bloques.')
        if a.constraint_type != 'ASAP' and a.constraint_date is None:
            raise ValueError('Falta fecha de restricción.')
        if any(abs(h - block_hours) > 1e-8 for h in hours(a.calendar_id)):
            raise ValueError('El calendario de actividad debe representar bloques continuos; revise feriados.')

    unavailable, capacities = {}, {}
    for r in resources.values():
        if r.kind not in {'trabajador', 'equipo'} or abs(r.capacity_hours_day - block_hours) > 1e-8:
            raise ValueError('Se requieren trabajadores/equipos con capacidad de un bloque por unidad.')
        capacity = integer(r.max_units, 'Unidades del recurso')
        if capacity < 1 or (r.kind == 'trabajador' and capacity != 1):
            raise ValueError('Cada trabajador debe representar una persona; equipos admiten varias unidades.')
        hh = hours(r.calendar_id)
        if any(abs(h) > 1e-8 and abs(h - block_hours) > 1e-8 for h in hh):
            raise ValueError('La disponibilidad debe ser 0 o block_hours; no se redondean jornadas parciales.')
        unavailable[r.id] = [t for t, h in enumerate(hh) if h <= 1e-8]
        capacities[r.id] = capacity

    rows = defaultdict(list)
    for row in problem.assignments:
        if row.activity_id not in acts:
            raise ValueError('Asignación de actividad inexistente.')
        rows[row.activity_id].append(row)
    for a in acts.values():
        if not rows[a.id] and (a.required_skill or a.work_hours):
            rows[a.id] = [Assignment(-a.id, a.id, skill=a.required_skill,
                                     units=a.crew_size, work_hours=a.work_hours)]

    model = cp_model.CpModel()
    starts, ends, fixed_intervals = {}, {}, {}
    for aid, a in acts.items():
        dur = durations[aid]
        # Last civil date is inclusive, while blocks use half-open intervals.
        lo, hi = 0, horizon - dur if dur else horizon
        if a.window_hard:
            if a.window_start: lo = max(lo, (a.window_start-origin).days)
            if a.window_end: hi = min(hi, (a.window_end-origin).days - max(0, dur-1))
        if a.constraint_type in {'MSO', 'SNET'}:
            lo = max(lo, (a.constraint_date-origin).days)
        if a.constraint_type == 'MSO': hi = min(hi, (a.constraint_date-origin).days)
        if a.constraint_type in {'MFO', 'FNET'}:
            lo = max(lo, (a.constraint_date-origin).days - max(0, dur-1))
        if a.constraint_type == 'MFO': hi = min(hi, (a.constraint_date-origin).days - max(0, dur-1))
        if a.locked:
            if a.planned_start is None: raise ValueError('Actividad bloqueada sin inicio.')
            pos = (a.planned_start-origin).days
            lo, hi = max(lo, pos), min(hi, pos)
            if a.planned_finish and a.planned_finish != a.planned_start + timedelta(days=max(0,dur-1)):
                raise ValueError('Fin bloqueado incompatible con la duración.')
        if lo > hi: raise ValueError(f'{a.code}: ventana incompatible con la duración.')
        starts[aid] = model.new_int_var(lo, hi, f's{aid}')
        ends[aid] = starts[aid] + dur
        if dur: fixed_intervals[aid] = model.new_fixed_size_interval_var(starts[aid], dur, f'a{aid}')
    for rel in problem.relations:
        if rel.type != 'FS' or rel.pred_id not in acts or rel.succ_id not in acts:
            raise ValueError('Bloques admite exclusivamente relaciones FS válidas.')
        model.add(starts[rel.succ_id] >= ends[rel.pred_id] + rel.lag_days)

    person_intervals, equipment_intervals, equipment_demands = defaultdict(list), defaultdict(list), defaultdict(list)
    selected, per_activity_person, costs = [], defaultdict(list), []
    for aid, assignments in rows.items():
        dur = durations[aid]
        for k, row in enumerate(assignments):
            n = integer(row.units, 'Unidades de asignación')
            if n < 1: raise ValueError('Una asignación debe solicitar al menos una unidad.')
            expected = dur * block_hours * n
            if row.work_hours and abs(row.work_hours-expected) > 1e-8:
                raise ValueError('HH incompatible con roles de dedicación completa.')
            if dur == 0: raise ValueError('Un hito no admite demanda de recursos.')
            resource = resources.get(row.resource_id)
            if row.resource_id is not None and resource is None:
                raise ValueError('Recurso nominal inexistente o inactivo.')
            if resource and resource.kind == 'equipo':
                equipment_intervals[resource.id].append(fixed_intervals[aid])
                equipment_demands[resource.id].append(n)
                continue
            role = row.skill or acts[aid].required_skill
            if not role: raise ValueError('Una asignación de personal requiere habilidad.')
            eligible = [r for r in resources.values() if r.kind == 'trabajador' and role in r.skills
                        and (row.resource_id is None or r.id == row.resource_id)]
            variables = []
            for r in eligible:
                x = model.new_bool_var(f'x{aid}_{k}_{r.id}')
                variables.append(x)
                per_activity_person[aid, r.id].append(x)
                person_intervals[r.id].append(model.new_optional_fixed_size_interval_var(starts[aid], dur, x, f'iv{aid}_{k}_{r.id}'))
                # Cost is exact in cents; reject finer precision instead of rounding silently.
                cents = Decimal(str(r.cost_hour))*Decimal(str(block_hours))*dur*100
                if not cents.is_finite() or cents < 0 or cents != cents.to_integral_value():
                    raise ValueError('Tarifa incompatible con precisión de centavos.')
                costs.append(int(cents)*x)
                selected.append((aid, row, role, r.id, x))
            model.add(sum(variables) == n)
    for variables in per_activity_person.values(): model.add(sum(variables) <= 1)
    for r in resources.values():
        blocked = [model.new_fixed_size_interval_var(t, 1, f'off{r.id}_{t}') for t in unavailable[r.id]]
        if r.kind == 'trabajador':
            model.add_no_overlap(person_intervals[r.id] + blocked)
        else:
            model.add_cumulative(equipment_intervals[r.id] + blocked,
                                 equipment_demands[r.id] + [capacities[r.id]]*len(blocked), capacities[r.id])
    if len(selected) > 100000: raise ValueError('Demasiadas combinaciones persona/rol.')
    if target is None:
        objective = model.new_int_var(0, horizon, 'finish')
        model.add_max_equality(objective, list(ends.values()))
    else: objective = ends[target]
    model.minimize(objective)

    def solve():
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(0.001, options.time_limit_s-(perf_counter()-began))
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = options.seed
        status = solver.solve(model)
        return solver, status

    solver, status1 = solve()
    if status1 == cp_model.MODEL_INVALID: raise ValueError(model.validate())
    if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return dict(status='infactible' if status1 == cp_model.INFEASIBLE else 'sin_solucion',
                    feasible=False, applied=False, infeasibility='demostrada' if status1 == cp_model.INFEASIBLE else 'no_encontrada',
                    stage1=solver.status_name(status1), elapsed_s=perf_counter()-began)
    primary = solver.value(objective)
    bound1 = solver.best_objective_bound
    cost = sum(costs)
    status2 = None
    if status1 == cp_model.OPTIMAL and perf_counter()-began < options.time_limit_s:
        model.add(objective == primary)
        model.minimize(cost)
        second, status2 = solve()
        if status2 == cp_model.MODEL_INVALID: raise ValueError(model.validate())
        if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE): solver = second
    allocation = []
    for aid, row, role, rid, x in selected:
        if solver.value(x):
            allocation.append(dict(activity_id=aid, source_assignment_id=row.id, resource_id=rid, skill=role))
    start_dates = {aid: origin+timedelta(days=solver.value(s)) for aid, s in starts.items()}
    finish_dates = {aid: start_dates[aid]+timedelta(days=max(0,durations[aid]-1)) for aid in acts}
    audit = verify_role_schedule(problem, start_dates, finish_dates, allocation,
                                 options, block_hours=block_hours)
    if not audit.feasible:
        return dict(status='rechazado_por_verificador', feasible=False, applied=False, verification=to_dict(audit))
    return dict(status='optimo' if status1 == status2 == cp_model.OPTIMAL else 'factible', feasible=True, applied=False,
                stage1=solver.status_name(status1), stage2=solver.status_name(status2) if status2 is not None else 'NO_EJECUTADA',
                target_code=target_code, primary_finish_block=primary, primary_bound=bound1,
                personnel_cost=solver.value(cost)/100, role_assignment=allocation,
                personnel_cost_bound=second.best_objective_bound / 100 if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                starts={aid:v.isoformat() for aid,v in start_dates.items()}, finishes={aid:v.isoformat() for aid,v in finish_dates.items()},
                blocks={aid:dict(start=solver.value(starts[aid]), finish=solver.value(ends[aid])) for aid in acts},
                time_axis=dict(encoding='synthetic_day_per_block', block_hours=block_hours, origin=origin.isoformat(), horizon_blocks=horizon),
                verification=to_dict(audit), elapsed_s=perf_counter()-began)
