"""Auditor independiente: sólo dominio, calendario público y aritmética civil.

No importa CPM, Loads, SGS ni CP-SAT. Las HH de proyecto se distribuyen por
horas laborables; mantenimiento usa HH uniformes por jornada (contrato A3).
La demanda genérica se comprueba mediante flujo por día, sin duplicar recursos
polivalentes. ``crews`` es activity_id -> [resource_id, ...], líder primero.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from ganttier.domain import remaining_work_start, schedule_floor
from math import ceil


@dataclass
class VerificationReport:
    feasible: bool
    violations: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)


def _flow(demands, capacities, eligible):
    """Flujo máximo fraccionario bipartito, con aristas residuales explícitas."""
    graph = defaultdict(dict)

    def edge(a, b, amount):
        graph[a][b] = amount
        graph[b].setdefault(a, 0.0)

    for skill, amount in demands.items():
        node = ('skill', skill)
        edge('source', node, amount)
        for rid in eligible.get(skill, []):
            edge(node, ('resource', rid), amount)
    for rid, amount in capacities.items():
        edge(('resource', rid), 'sink', max(0.0, amount))
    total = 0.0
    while True:
        parent = {'source': None}
        queue = deque(['source'])
        while queue and 'sink' not in parent:
            a = queue.popleft()
            for b, amount in graph[a].items():
                if amount > 1e-8 and b not in parent:
                    parent[b] = a
                    queue.append(b)
        if 'sink' not in parent:
            return total
        amount, b = float('inf'), 'sink'
        while parent[b] is not None:
            a = parent[b]
            amount = min(amount, graph[a][b])
            b = a
        b = 'sink'
        while parent[b] is not None:
            a = parent[b]
            graph[a][b] -= amount
            graph[b][a] += amount
            b = a
        total += amount


def verify_schedule(problem, starts, finishes, *, worker_assignment=None, crews=None) -> VerificationReport:
    report = VerificationReport(True)
    acts = {a.id: a for a in problem.activities}
    resources = {r.id: r for r in problem.resources if r.active}
    starts, finishes = dict(starts), dict(finishes)
    crews = crews or {}
    workers = worker_assignment or {}
    cutoff = problem.data_date or problem.project.data_date or problem.project.start_date
    maintenance = problem.project.mode == 'mantenimiento'
    by_activity = defaultdict(list)
    for row in problem.assignments:
        by_activity[row.activity_id].append(row)
    assigned = defaultdict(lambda: defaultdict(float))
    generic = defaultdict(lambda: defaultdict(float))
    contributors = defaultdict(set)
    hours_cache = {}

    def hours(cid, day):
        key = (cid, day)
        if key not in hours_cache:
            cal = problem.calendars[cid]
            hours_cache[key] = max(0.0, cal.hours(day)) if cal.is_workday(day) else 0.0
        return hours_cache[key]

    def issue(kind, ids, day, detail, warning=False):
        item = dict(type=kind, activities=list(ids), date=day.isoformat() if day else None, detail=detail)
        (report.warnings if warning else report.violations).append(item)

    def seek(cid, day, direction=1):
        # Bounded even for an empty/broken calendar; supports leads before horizon.
        for _ in range(36600):
            if hours(cid, day) > 0:
                return day
            day += timedelta(days=direction)
        raise ValueError('El calendario no tiene días laborables disponibles.')

    for aid in set(starts) | set(finishes):
        if aid not in acts:
            issue('unknown_activity', [aid], None, 'Las fechas contienen una actividad inexistente.')
    for a in problem.activities:
        s, f = starts.get(a.id), finishes.get(a.id)
        if not a.actual_finish and a.duration_days == 0 and (a.work_hours > 0 or any(
                row.work_hours > 0 for row in by_activity[a.id])):
            issue('milestone_work', [a.id], None,
                  f'{a.code}: un hito de duración cero no puede consumir HH, incluidas sus asignaciones.')
        if not isinstance(s, date) or not isinstance(f, date):
            issue('missing_dates', [a.id], None, f'{a.code}: falta inicio o fin de la actividad.')
            continue
        cid = a.calendar_id if a.calendar_id is not None else problem.default_calendar_id
        if cid not in problem.calendars:
            issue('calendar', [a.id], s, f'{a.code}: calendario inexistente.')
            continue
        completed = bool(a.actual_finish)
        if completed:
            if s != a.actual_start or f != a.actual_finish:
                issue('actual_dates', [a.id], s, f'{a.code}: se modificaron las fechas reales.')
            if a.constraint_date:
                c, d = a.constraint_type, a.constraint_date
                bad = {'MSO': s != d, 'MFO': f != d, 'SNET': s < d, 'FNET': f < d, 'SNLT': s > d, 'FNLT': f > d}.get(c, False)
                if bad:
                    issue(c, [a.id], f, f'{a.code}: las fechas reales incumplen {c} ({d}).', c in {'SNLT', 'FNLT'})
            if (a.window_start and s < a.window_start) or (a.window_end and f > a.window_end):
                issue('hard_window' if a.window_hard else 'soft_window', [a.id], s,
                      f'{a.code}: fechas reales fuera de la ventana.', not a.window_hard)
            continue
        remaining = ceil(a.remaining_duration if a.remaining_duration is not None else a.duration_days * (1 - a.percent_complete / 100)) if a.actual_start else a.duration_days
        work_start = remaining_work_start(cutoff, a.actual_start) if a.actual_start else s
        if a.actual_start and s != a.actual_start:
            issue('actual_dates', [a.id], s, f'{a.code}: se modificó el inicio real.')
        # Historical starts are immutable. A zero-duration mandatory milestone
        # remains a civil-date point, as established by the shared CPM contract.
        civil_milestone = remaining == 0 and a.constraint_type in {'MSO', 'MFO'}
        if not a.actual_start and not civil_milestone and (hours(cid, s) <= 0 or hours(cid, f) <= 0):
            issue('calendar', [a.id], s, f'{a.code}: inicio o fin en día no laborable.')
        days = [date.fromordinal(n) for n in range(work_start.toordinal(), f.toordinal() + 1)
                if hours(cid, date.fromordinal(n)) > 0]
        valid_duration = (s == f if remaining == 0 and not a.actual_start else len(days) == remaining and bool(days) and days[-1] == f)
        if a.actual_start and remaining == 0:
            valid_duration = f == seek(cid, work_start)
        if f < s or not valid_duration:
            issue('duration', [a.id], f, f'{a.code}: duración esperada {remaining} días laborables; intervalo incompatible.')
        if not a.actual_start and s < max(schedule_floor(cutoff, problem.project.start_date), problem.project.start_date, problem.horizon_start):
            issue('data_date', [a.id], s, f'{a.code}: una actividad no iniciada comienza antes de la fecha de corte o del horizonte.')
        if f > problem.horizon_end:
            issue('horizon', [a.id], f, f'{a.code}: fin fuera del horizonte.')
        if a.locked and (s != a.planned_start or (a.planned_finish and f != a.planned_finish)):
            issue('locked', [a.id], s, f'{a.code}: se modificaron fechas bloqueadas.')
        c, d = a.constraint_type, a.constraint_date
        if c not in {'ASAP', 'ALAP'} and d is None:
            issue('constraint_date', [a.id], s, f'{a.code}: falta fecha para {c}.')
        if d is not None:
            bad = {'MSO': s != d, 'MFO': f != d, 'SNET': s < d, 'FNET': f < d, 'SNLT': s > d, 'FNLT': f > d}.get(c, False)
            if bad:
                issue(c, [a.id], s if c in {'MSO', 'SNET', 'SNLT'} else f,
                      f'{a.code}: incumple {c} ({d.isoformat()}).', c in {'SNLT', 'FNLT'})
        if (a.window_start and s < a.window_start) or (a.window_end and f > a.window_end):
            issue('hard_window' if a.window_hard else 'soft_window', [a.id], s,
                  f'{a.code}: inicio o fin fuera de la ventana.', not a.window_hard)
        if not remaining or not days:
            continue
        fraction = 1 - a.percent_complete / 100 if a.actual_start else 1.0
        rows = by_activity[a.id]
        crew = list(crews.get(a.id, [workers[a.id]] if a.id in workers else []))
        if crew:
            if len(set(crew)) != len(crew) or (a.id in workers and crew[0] != workers[a.id]):
                issue('crew', [a.id], s, f'{a.code}: cuadrilla duplicada o líder incompatible.')
            members = [resources[rid] for rid in set(crew) if rid in resources]
            if sum(r.max_units for r in members) + 1e-8 < a.crew_size:
                issue('crew', [a.id], s, f'{a.code}: dotación insuficiente para la cuadrilla de {a.crew_size}.')
            # La cuadrilla puede cambiar; la demanda de cada asignación se conserva.
            source = [(r.skill or a.required_skill, r.work_hours, r.units) for r in rows]
            if not source:
                source = [(a.required_skill, a.work_hours, a.crew_size)]
            demands = [(rid, skill, work / len(crew), units / len(crew))
                       for rid in crew for skill, work, units in source]
        else:
            demands = [(r.resource_id, r.skill or a.required_skill, r.work_hours, r.units) for r in rows]
            if not demands and (a.required_skill or a.work_hours):
                demands = [(None, a.required_skill, a.work_hours, a.crew_size)]
            if maintenance and worker_assignment is not None and demands and not all(rid is not None for rid, *_ in demands):
                issue('worker_missing', [a.id], s, f'{a.code}: falta asignación de trabajador o cuadrilla.')
        total_hours = sum(hours(cid, day) for day in days)
        for rid, skill, work, units in demands:
            resource = resources.get(rid)
            if rid is not None and resource is None:
                issue('worker', [a.id], s, f'{a.code}: recurso {rid} inexistente o inactivo.')
            if maintenance and resource and resource.kind not in {'trabajador', 'cuadrilla'}:
                issue('worker', [a.id], s, f'{a.code}: el recurso asignado no es un trabajador ni una cuadrilla.')
            if maintenance and resource and skill and skill not in resource.skills:
                issue('skill', [a.id], s, f'{a.code}: {resource.name} no tiene la habilidad {skill}.')
            if not skill and rid is None:
                issue('unassigned', [a.id], s, f'{a.code}: HH sin recurso ni habilidad; capacidad no verificable.', True)
                continue
            for day in days:
                amount = work * fraction * (1 / len(days) if maintenance else hours(cid, day) / total_hours) if work else units * hours(cid, day)
                if amount <= 1e-8:
                    continue
                contributors[day].add(a.id)
                if rid is None:
                    generic[day][skill] += amount
                else:
                    assigned[day][rid] += amount
                    if resource and hours(resource.calendar_id if resource.calendar_id is not None else problem.default_calendar_id, day) <= 0:
                        issue('worker_calendar', [a.id], day, f'{a.code}: {resource.name} no está disponible.')

    for rel in problem.relations:
        if rel.pred_id not in acts or rel.succ_id not in acts:
            issue('relation', [rel.pred_id, rel.succ_id], None, 'Relación con actividad inexistente.')
            continue
        a, pred = acts[rel.succ_id], acts[rel.pred_id]
        if a.actual_start:
            issue('progress_override', [pred.id, a.id], a.actual_start, 'Se omite la relación entrante al avance real (Progress Override).', True)
            continue
        if rel.type not in {'FS', 'SS', 'FF', 'SF'}:
            issue('relation', [pred.id, a.id], None, 'Tipo de relación desconocido.')
            continue
        event = (finishes if rel.type[0] == 'F' else starts).get(pred.id)
        target = (finishes if rel.type[1] == 'F' else starts).get(a.id)
        if not isinstance(event, date) or not isinstance(target, date):
            continue
        cid = a.calendar_id if a.calendar_id is not None else problem.default_calendar_id
        if cid not in problem.calendars:
            continue
        try:
            required = seek(cid, event + timedelta(days=int(rel.type == 'FS' and pred.duration_days > 0)))
            direction = 1 if rel.lag_days >= 0 else -1
            for _ in range(abs(rel.lag_days)):
                required = seek(cid, required + timedelta(days=direction), direction)
            if target < required:
                issue('precedence', [pred.id, a.id], target,
                      f'{pred.code} → {a.code}: {rel.type}{rel.lag_days:+d} requiere {required.isoformat()} o posterior.')
        except (ValueError, OverflowError) as exc:
            issue('calendar', [pred.id, a.id], target, str(exc))

    eligible = defaultdict(list)
    for r in resources.values():
        for skill in r.skills:
            eligible[skill].append(r.id)
    checked_days = set(assigned) | set(generic)
    for day in sorted(checked_days):
        capacities = {}
        for r in resources.values():
            h = hours(r.calendar_id if r.calendar_id is not None else problem.default_calendar_id, day)
            capacities[r.id] = (min(h, r.capacity_hours_day) + (r.max_overtime_hours_day if h else 0)) * r.max_units
        for rid, amount in assigned[day].items():
            cap = capacities.get(rid, 0.0)
            if amount > cap + 1e-7:
                issue('resource_capacity', sorted(contributors[day]), day,
                      f'Capacidad insuficiente del recurso {rid}: {amount:.6g} HH > {cap:.6g} HH permitidas.')
            capacities[rid] = max(0.0, cap - amount)
        demand = sum(generic[day].values())
        if demand and _flow(generic[day], capacities, eligible) < demand - 1e-7:
            issue('skill_capacity', sorted(contributors[day]), day,
                  f'Capacidad insuficiente para habilidades {", ".join(sorted(generic[day]))}: no hay recursos disponibles que cubran {demand:.6g} HH sin duplicar personas.')
    _verify_batches(problem, starts, finishes, workers, crews, by_activity, issue)
    _verify_routes(problem, starts, finishes, issue)
    report.feasible = not report.violations
    report.stats = dict(activities=len(acts), scheduled=sum(isinstance(starts.get(a), date) and isinstance(finishes.get(a), date) for a in acts),
                        resource_days=len(checked_days), violations=len(report.violations), warnings=len(report.warnings))
    return report


def verify_with_options(problem, starts, finishes, options, **kwargs):
    """Explicit policy adapter; the independent checker always checks capacity."""
    if not options.allow_overtime:
        problem = replace(problem, resources=[replace(r, max_overtime_hours_day=0) for r in problem.resources])
    report = verify_schedule(problem, starts, finishes, **kwargs)
    if not options.respect_resource_limits and not any(a.batch_index is not None for a in problem.activities):
        capacity = {'resource_capacity', 'skill_capacity'}
        report.warnings.extend(v for v in report.violations if v['type'] in capacity)
        report.violations = [v for v in report.violations if v['type'] not in capacity]
        report.feasible = not report.violations
        report.stats.update(violations=len(report.violations), warnings=len(report.warnings), capacity_policy='blanda')
    return report


def verify_role_schedule(problem, starts, finishes, allocation, options, *, block_hours):
    """Independent full-person audit against ORIGINAL demands, then dates/capacity.

    A nominal row refers to one source demand and covers its entire activity.
    No optimizer, candidate selection or CP-SAT objects are used here.
    """
    from ganttier.domain import Assignment
    from math import isfinite
    report = VerificationReport(True)

    def issue(kind, aid, detail):
        report.violations.append(dict(type=kind, activities=[aid] if aid is not None else [],
                                      date=None, detail=detail))

    if (not isfinite(block_hours) or not 0 < block_hours <= 24 or options.allow_overtime
            or not options.respect_resource_limits or problem.project.mode != 'proyecto'):
        issue('role_policy', None, 'Roles requiere proyecto, bloques válidos y capacidad dura sin horas extra.')
        report.feasible = False
        return report
    acts = {a.id: a for a in problem.activities}
    resources = {r.id: r for r in problem.resources if r.active}
    demands = list(problem.assignments)
    explicit = {row.activity_id for row in demands}
    for a in acts.values():
        if a.id not in explicit and (a.required_skill or a.work_hours):
            demands.append(Assignment(-a.id, a.id, skill=a.required_skill,
                                      units=a.crew_size, work_hours=a.work_hours))
    source = {}
    concrete = []
    people = defaultdict(set)
    counts = defaultdict(int)
    for row in demands:
        key = (row.activity_id, row.id)
        source[key] = row
        a = acts.get(row.activity_id)
        if a is None:
            issue('role_demand', row.activity_id, 'Demanda de una actividad inexistente.')
            continue
        if (row.units < 1 or row.units != int(row.units) or a.duration_days == 0
                or (row.work_hours and abs(row.work_hours - a.duration_days * block_hours * row.units) > 1e-7)):
            issue('role_demand', a.id, 'Demanda incompatible con roles enteros de dedicación completa.')
        resource = resources.get(row.resource_id)
        if resource and resource.kind == 'equipo':
            concrete.append(row)
    for entry in allocation:
        aid, rid = entry.get('activity_id'), entry.get('resource_id')
        key = (aid, entry.get('source_assignment_id'))
        row, resource = source.get(key), resources.get(rid)
        if row is None or aid not in acts:
            issue('role_source', aid, 'La asignación nominal no corresponde a una demanda original.')
            continue
        role = row.skill or acts[aid].required_skill
        if (resource is None or resource.kind != 'trabajador' or resource.max_units != 1
                or abs(resource.capacity_hours_day - block_hours) > 1e-7
                or role not in resource.skills or entry.get('skill') != role
                or (row.resource_id is not None and row.resource_id != rid)):
            issue('role_skill', aid, 'Persona, habilidad o recurso nominal incompatible con la demanda.')
            continue
        if rid in people[aid]:
            issue('role_distinct', aid, 'Una persona ocupa más de un rol en la misma actividad.')
        people[aid].add(rid)
        counts[key] += 1
        concrete.append(replace(row, resource_id=rid, skill=role, units=1,
                                work_hours=acts[aid].duration_days * block_hours))
    for key, row in source.items():
        resource = resources.get(row.resource_id)
        if not (resource and resource.kind == 'equipo') and counts[key] != row.units:
            issue('role_count', row.activity_id, 'La cantidad de personas no coincide con la demanda original.')
    for a in acts.values():
        s, f = starts.get(a.id), finishes.get(a.id)
        if isinstance(s, date) and isinstance(f, date):
            if (f-s).days != max(0, a.duration_days-1):
                issue('role_duration', a.id, 'El intervalo no coincide con la duración en bloques continuos.')
            limit = problem.horizon_end + timedelta(days=int(a.duration_days == 0))
            if s < problem.horizon_start or f > limit:
                issue('role_horizon', a.id, 'El intervalo está fuera del horizonte de bloques.')
    checked = verify_with_options(replace(problem, assignments=concrete,
        horizon_end=problem.horizon_end + timedelta(days=1)), starts, finishes, options)
    report.violations.extend(checked.violations)
    report.warnings = checked.warnings
    report.stats = dict(checked.stats, roles=len(allocation), violations=len(report.violations))
    report.feasible = not report.violations
    return report


def _verify_batches(problem, starts, finishes, workers, crews, by_activity, issue):
    """Audita lotes desde maestras y recursos originales, sin importar el generador/nivelador."""
    from collections import Counter
    from decimal import Decimal, ROUND_CEILING
    from calendar import monthrange
    from math import isclose, lcm
    tasks = {t.id: t for t in problem.periodic_tasks if t.quantity_total is not None and not t.route_code}
    resources = {r.id: r for r in problem.resources}
    grouped = defaultdict(list)
    for a in problem.activities:
        if a.batch_index is not None or a.periodic_task_id in tasks:
            grouped[(a.periodic_task_id, a.occurrence_index)].append(a)
    expected_dates = {}
    for tid, task in tasks.items():
        expected_dates[tid] = _batch_due_contract(task, problem) if task.active else {}
        for index, due in expected_dates[tid].items():
            if (tid, index) not in grouped:
                issue('batch_missing_occurrence', [], due,
                      f'{task.code}: falta la ocurrencia completa {index} ({due}).')
    accumulated = {}
    cycles, months = defaultdict(Counter), defaultdict(Counter)
    policies = {}
    for (tid, index), rows in sorted(grouped.items(), key=lambda item: (item[0][0] or 0, item[0][1] or 0)):
        task = tasks.get(tid)
        if task is None:
            issue('batch_task', [a.id for a in rows], None, 'Lote sin tarea maestra de cantidad verificable.')
            continue
        ids = [a.id for a in rows]
        total, batch = (Decimal(str(v)) for v in (task.quantity_total, task.batch_size))
        expected = int((total / batch).to_integral_value(rounding=ROUND_CEILING))
        if len(rows) != expected or {a.batch_index for a in rows} != set(range(1, expected+1)):
            issue('batch_count', ids, None, f'{task.code}, ocurrencia {index}: faltan lotes o hay índices duplicados.')
        if not isclose(sum(a.quantity or 0 for a in rows), float(total), rel_tol=1e-10, abs_tol=1e-8):
            issue('batch_quantity', ids, None, f'{task.code}, ocurrencia {index}: suma de cantidades distinta de {total}.')
        counts = Counter({rid: 0 for rid in task.team_ids})
        definitions = [problem.calendars[resources[rid].calendar_id].cal for rid in task.team_ids if rid in resources]
        cycle_length = max((sum(map(int, c.shift_pattern.split('x'))) if c.shift_pattern else 7 for c in definitions), default=7)
        period_length = {'D': 1, 'DH': 1, 'ND': task.interval, 'W': 7, 'NW': 7*task.interval,
                         'M': 28, 'NM': 28*task.interval, 'Q': 84, 'S': 168, 'A': 365, 'NA': 365*task.interval}[task.frequency]
        for a in rows:
            due = expected_dates[tid].get(index)
            if due is not None:
                if task.frequency in {'M', 'NM', 'Q', 'S', 'A', 'NA'}:
                    span = {'M': 1, 'NM': task.interval, 'Q': 3, 'S': 6, 'A': 12, 'NA': 12*task.interval}[task.frequency]
                    year, month = divmod(due.year*12+due.month-1+span, 12)
                    lower, upper = due.replace(day=1), date(year, month+1, 1)-timedelta(days=1)
                elif period_length < cycle_length:
                    lower, upper = due, due+timedelta(days=period_length-1)
                else:
                    lower, upper = due-timedelta(days=task.tol_before_days), due+timedelta(days=task.tol_after_days)
                lower = max(lower, problem.horizon_start)
                upper = min(upper, problem.horizon_end, task.horizon_end or problem.horizon_end)
                if a.due_date != due or a.window_start != lower or a.window_end != upper or not a.window_hard:
                    issue('batch_window', [a.id], due, f'{a.code}: ventana o vencimiento distintos de la ocurrencia contractual.')
            elif task.active:
                issue('batch_occurrence', [a.id], a.due_date, f'{a.code}: ocurrencia ajena al horizonte de la maestra.')
            rid = a.team_id
            r = resources.get(rid)
            if rid not in task.team_ids or r is None or not r.is_work_team:
                issue('batch_team', [a.id], None, f'{a.code}: equipo distinto de los seleccionados en la maestra.')
                continue
            counts[rid] += 1
            assigned = crews.get(a.id) or ([workers[a.id]] if a.id in workers else [row.resource_id for row in by_activity[a.id]])
            if assigned != [rid] or a.calendar_id != r.calendar_id:
                issue('batch_team', [a.id], None, f'{a.code}: asignación o calendario distinto del equipo del lote.')
            if a.batch_index is None or not 1 <= a.batch_index <= expected:
                continue
            amount = min(batch, total-(a.batch_index-1)*batch)
            nominal = problem.calendars[r.calendar_id].cal.hours_per_day
            hourly_rate = task.productivity_hour_team or task.productivity_day_team / (task.productive_hours_day or nominal)
            expected_hours = float(amount) / hourly_rate
            duration = max(1, ceil(expected_hours / nominal - 1e-12))
            if task.batch_full_day:
                expected_hours = duration * nominal
            if not isclose(a.quantity or 0, float(amount), rel_tol=1e-10, abs_tol=1e-8):
                issue('batch_quantity', [a.id], None, f'{a.code}: tamaño de lote alterado.')
            cal = problem.calendars[r.calendar_id]
            if a.duration_days != duration or not isclose(a.work_hours, expected_hours*r.max_units) or (a.duration_hours is not None and not isclose(a.duration_hours, expected_hours)):
                issue('batch_effort', [a.id], None, f'{a.code}: duración o HH no corresponden al rendimiento y dotación del equipo.')
            s, f = starts.get(a.id), finishes.get(a.id)
            if isinstance(s, date) and isinstance(f, date):
                effective = [date.fromordinal(n) for n in range(s.toordinal(), f.toordinal()+1)
                             if cal.is_workday(date.fromordinal(n))]
                if (len(effective) != duration or not effective or effective[0] != s or effective[-1] != f
                        or (not cal.cal.shift_pattern and any(day.weekday() >= 5 for day in effective))
                        or (task.batch_continuous_in_shift and (f-s).days+1 != duration)):
                    issue('batch_rest', [a.id], s, f'{a.code}: días efectivos distintos de la duración, extremo en descanso o cruce prohibido por modo estricto.')
                if not a.window_start or not a.window_end or s < a.window_start or f > a.window_end:
                    issue('batch_window', [a.id], s, f'{a.code}: lote fuera de la ventana de la ocurrencia.')
        if tid not in policies:
            definitions = [problem.calendars[resources[rid].calendar_id].cal for rid in task.team_ids if rid in resources]
            lengths = [sum(map(int, c.shift_pattern.split('x'))) if c.shift_pattern else 7 for c in definitions]
            cycle = lcm(*lengths) if lengths else 7
            step = {'D': 1, 'DH': 1, 'ND': task.interval, 'W': 7, 'NW': 7*task.interval,
                    'M': 28, 'NM': 28*task.interval, 'Q': 84, 'S': 168, 'A': 365, 'NA': 365*task.interval}[task.frequency]
            anchor = min((c.shift_anchor if c.shift_pattern else task.first_due-timedelta(days=task.first_due.weekday())
                          for c in definitions), default=task.first_due)
            policies[tid] = (step < max(lengths, default=7), cycle, anchor)
        short, cycle, anchor = policies[tid]
        if not short:
            if max(counts.values(), default=0)-min(counts.values(), default=0) > 1:
                issue('batch_equity', ids, None, f'{task.code}, ocurrencia {index}: reparto con diferencia mayor de un lote.')
            accumulated.setdefault(tid, Counter({rid: 0 for rid in task.team_ids})).update(counts)
            totals = accumulated[tid]
            if max(totals.values(), default=0)-min(totals.values(), default=0) > 1:
                issue('batch_accumulated', ids, None, f'{task.code}: acumulado desequilibrado; revise la rotación.')
        else:
            due = rows[0].due_date
            if due:
                cycle_start = anchor + timedelta(days=((due-anchor).days//cycle)*cycle)
                for bucket, key in ((cycles, (tid, cycle_start)), (months, (tid, due.replace(day=1)))):
                    if key not in bucket:
                        bucket[key] = Counter({rid: 0 for rid in task.team_ids})
                    bucket[key].update(counts)
    for label, buckets in (('ciclo', cycles), ('mes', months)):
        for (tid, first), counts in buckets.items():
            task = tasks[tid]
            last = first+timedelta(days=policies[tid][1]-1) if label == 'ciclo' else first.replace(day=monthrange(first.year, first.month)[1])
            if first < max(problem.horizon_start, task.first_due) or last > min(problem.horizon_end, task.horizon_end or date.max):
                continue
            if max(counts.values())-min(counts.values()) > task.balance_tolerance_batches:
                detail = '; '.join(f'{resources[rid].name}: {value} lotes' for rid, value in counts.items())
                issue('batch_cycle_balance', [], first,
                      f'{task.code}: acumulado por {label} {first} a {last} desequilibrado ({detail}); '
                      f'tolerancia {task.balance_tolerance_batches} lotes. Revisar manualmente.', True)


def _batch_due_contract(task, problem):
    """Recurrencias de auditoría por aritmética civil, independiente de generator._dates."""
    from calendar import monthrange
    first, stop = problem.horizon_start, min(problem.horizon_end, task.horizon_end or problem.horizon_end)
    anchor = task.first_due
    if anchor is None:
        return {}
    allowed = {int(m.strip()) for m in task.preferred_months.split(',') if m.strip()}
    result = {}
    daily = task.frequency in {'D', 'DH', 'ND', 'W', 'NW'}
    if daily:
        step = {'D': 1, 'DH': 1, 'ND': task.interval, 'W': 7, 'NW': 7*task.interval}[task.frequency]
        base = anchor
        if task.frequency in {'W', 'NW'} and task.preferred_weekday is not None:
            base += timedelta(days=(task.preferred_weekday-base.weekday()) % 7)
        k = max(0, ((first-base).days+step-1)//step)
        due = base+timedelta(days=k*step)
        while due <= stop:
            valid = (not allowed or due.month in allowed)
            valid &= task.preferred_weekday is None or due.weekday() == task.preferred_weekday
            valid &= task.day_of_month is None or due.day == min(task.day_of_month, monthrange(due.year, due.month)[1])
            if task.frequency == 'DH':
                valid &= problem.calendars[task.calendar_id or problem.default_calendar_id].is_workday(due)
            if valid:
                result[k+1] = due
            k += 1
            due += timedelta(days=step)
    else:
        step = {'M': 1, 'NM': task.interval, 'Q': 3, 'S': 6, 'A': 12, 'NA': 12*task.interval}[task.frequency]
        origin = anchor.year*12+anchor.month-1
        end = stop.year*12+stop.month-1
        for k in range(max(0, (first.year*12+first.month-1-origin)//step), max(0, (end-origin)//step+1)):
            slot = origin+k*step
            if allowed and step > 1:
                options = [m for m in range(slot, slot+step) if m%12+1 in allowed]
                if not options:
                    continue
                slot = options[0]
            year, month0 = divmod(slot, 12)
            month = month0+1
            if allowed and month not in allowed:
                continue
            limit = monthrange(year, month)[1]
            day = min(task.day_of_month or anchor.day, limit)
            due = date(year, month, day)
            if task.preferred_weekday is not None:
                offset = (task.preferred_weekday-due.weekday()) % 7
                day += offset if offset <= 3 else offset-7
                day = day+7 if day < 1 else day-7 if day > limit else day
                due = date(year, month, day)
            if max(first, anchor) <= due <= stop:
                result[k+1] = due
    return result


def _verify_routes(problem, starts, finishes, issue):
    """Reconstruye checklist, HH y cobertura diaria sin llamar al generador."""
    from math import isclose
    routes={t.id:t for t in problem.periodic_tasks if t.is_route and t.active}
    by_code={t.code:t for t in problem.periodic_tasks}
    by_id={t.id:t for t in problem.periodic_tasks}
    children=defaultdict(list)
    for t in problem.periodic_tasks:
        if t.active and t.route_code:
            parent=by_code.get(t.route_code)
            if parent is None or not parent.is_route or not parent.active:
                issue('route_reference',[],None,f'{t.code}: ruta inexistente o inactiva.')
            else:
                children[parent.id].append(t)
    grouped=defaultdict(list)
    resources={r.id:r for r in problem.resources}
    for a in problem.activities:
        if a.periodic_task_id in routes:
            grouped[a.periodic_task_id].append(a)
            expected=sorted(t.code for t in children[a.periodic_task_id])
            if sorted(a.checklist_codes)!=expected or len(set(a.checklist_codes))!=len(a.checklist_codes):
                issue('route_checklist',[a.id],a.due_date,f'{a.code}: checklist incompleto o duplicado.')
        elif a.periodic_task_id in by_id:
            t=by_id[a.periodic_task_id]
            if t.route_code:
                issue('route_duplicate',[a.id],a.due_date,f'{a.code}: el ítem no debe duplicar su ruta.')
    for tid,r in routes.items():
        if not children[tid]:
            issue('route_empty',[],None,f'{r.code}: ruta sin ítems.')
        for t in children[tid]:
            attrs=('location','frequency','interval','first_due','horizon_end','day_of_month','preferred_weekday')
            task_months = {int(m) for m in t.preferred_months.split(',') if m.strip()}
            route_months = {int(m) for m in r.preferred_months.split(',') if m.strip()}
            if (any(getattr(t,k)!=getattr(r,k) for k in attrs) or task_months != route_months
                or (not r.is_daily_round and t.equipment!=r.equipment)):
                issue('route_scope',[],None,f'{t.code}: alcance distinto de su ruta.')
        if not r.is_daily_round:
            continue
        expected=_batch_due_contract(r,problem)
        rows=grouped[tid]
        if len(rows)!=len(expected) or {a.occurrence_index for a in rows}!=set(expected):
            issue('round_missing',[],None,f'{r.code}: faltan rondas diarias o hay duplicados.')
        hh=sum(t.work_hours for t in children[tid])
        for a in rows:
            day=expected.get(a.occurrence_index)
            member=resources.get(a.team_id)
            if (a.batch_index is not None or a.due_date!=day or starts.get(a.id)!=day or finishes.get(a.id)!=day
                or not isclose(a.work_hours,hh) or not isclose(r.work_hours,hh)
                or member is None or a.team_id not in r.team_ids or a.calendar_id!=member.calendar_id
                or not isclose(a.duration_hours or 0,hh/member.max_units)):
                issue('round_contract',[a.id],a.due_date,f'{a.code}: fecha, HH, equipo o duración de ronda alterados.')
