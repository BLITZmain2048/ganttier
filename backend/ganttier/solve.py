"""Generar un programa desde tareas, verificarlo y recién entonces aplicarlo."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from math import ceil
from time import perf_counter
from collections import defaultdict

from . import domain as d, repo, services
from .calendars import WorkCalendar
from .engine import optimize, validate_network
from .engine.verify import VerificationReport, verify_with_options


def _date(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Las fechas del horizonte deben tener formato YYYY-MM-DD.') from exc


def _maintenance(conn, pid, problem, weights, options):
    from .maintenance import level_periodic, crew_assignment
    tasks = {t.id: t.resource_id for t in repo.list_entities(conn, 'periodic_tasks', pid)}
    occurrences = services._occurrences(problem, tasks)
    recorded = {row[0] for row in conn.execute('''SELECT DISTINCT p.activity_id FROM progress p
        JOIN activities a ON a.id=p.activity_id WHERE a.project_id=?
        AND (p.percent_complete>0 OR p.actual_start IS NOT NULL OR p.actual_finish IS NOT NULL OR p.actual_hours>0)''', (pid,))}
    occurrences = [o for o in occurrences if o.activity_id not in recorded]
    ids = {o.activity_id for o in occurrences}
    data_date = problem.data_date or problem.project.data_date or problem.project.start_date
    cutoff = max(problem.horizon_start, d.schedule_floor(data_date, problem.project.start_date))
    demand_rows = defaultdict(list)
    for row in problem.assignments:
        demand_rows[row.activity_id].append(row)
    assignments = {x.activity_id: x.resource_id for x in problem.assignments if x.resource_id is not None}
    for a in problem.activities:
        if a.id not in ids and a.periodic_task_id is None and not (a.locked or a.actual_start or a.percent_complete or a.id in recorded):
            occurrences.append(d.Occurrence(0, 0, a.code, a.name, a.due_date or cutoff,
                a.window_start or cutoff, a.window_end or problem.horizon_end, a.duration_days,
                a.work_hours, a.crew_size, a.required_skill, assignments.get(a.id),
                a.calendar_id or problem.default_calendar_id, a.planned_start, a.planned_finish,
                a.window_hard, a.location, a.equipment, a.requires_outage, a.priority, a.id))
    # Restrict physical bounds without erasing soft-window penalties in the
    # independent audit (which uses the original activities).
    adjusted = []
    demand_units = {}
    acts = {a.id: a for a in problem.activities}
    for o in occurrences:
        a = acts[o.activity_id]
        lower, upper = max(cutoff, o.window_start), min(problem.horizon_end, o.window_end)
        if a.constraint_type in {'SNET', 'MSO'}:
            lower = max(lower, a.constraint_date)
        if a.constraint_type == 'MFO':
            upper = min(upper, a.constraint_date)
        if lower > upper:
            if o.window_hard:
                raise ValueError(f'{a.code}: la ventana dura no cabe después de la fecha de corte.')
            lower, upper = cutoff, problem.horizon_end
        rows = demand_rows[a.id]
        # Las HH explícitas son la fuente de demanda, también al reasignar cuadrillas.
        work = sum(row.work_hours for row in rows) if rows else o.work_hours
        units = sum(row.units for row in rows if not row.work_hours) if rows else o.crew_size
        if rows and (units or not work):
            demand_units[a.id] = units
        adjusted.append(replace(o, window_start=lower, window_end=upper, work_hours=work,
                                crew_size=max(o.crew_size, units)))
    pending_ids = {o.activity_id for o in occurrences}
    fixed = {a.id: a for a in problem.activities if a.id not in pending_ids and not a.actual_finish}
    fixed_finishes = {}
    for a in fixed.values():
        if a.actual_start:
            cal = problem.calendars[a.calendar_id or problem.default_calendar_id]
            first = cal.next_workday(d.remaining_work_start(data_date, a.actual_start))
            remaining = ceil(a.remaining_duration if a.remaining_duration is not None
                             else a.duration_days * (1 - a.percent_complete / 100))
            fixed_finishes[a.id] = cal.add_workdays(first, remaining)
        else:
            fixed_finishes[a.id] = a.planned_finish
    reserved = defaultdict(lambda: defaultdict(float))
    for row in problem.assignments:
        a = fixed.get(row.activity_id)
        if a is None or row.resource_id is None:
            continue
        first = d.remaining_work_start(data_date, a.actual_start) if a.actual_start else a.planned_start
        last = fixed_finishes[a.id]
        if first is None or last is None:
            continue
        cal = problem.calendars[a.calendar_id or problem.default_calendar_id]
        days = [date.fromordinal(n) for n in range(first.toordinal(), last.toordinal() + 1) if cal.is_workday(date.fromordinal(n))]
        fraction = 1 - a.percent_complete / 100 if a.actual_start else 1
        for day in days:
            reserved[row.resource_id][day] += row.work_hours * fraction / len(days) if row.work_hours else row.units * cal.hours(day)
    resources = [replace(r, calendar_id=problem.default_calendar_id) if r.calendar_id is None else r
                 for r in problem.resources]
    result = level_periodic(adjusted, resources, problem.calendars, weights, options,
        date_constraints={a.id: (a.constraint_type, a.constraint_date) for a in problem.activities},
        horizon_start=cutoff, horizon_end=problem.horizon_end, reserved_load=reserved,
        demand_units=demand_units, relation_problem=problem if problem.relations else None)
    crews = crew_assignment(result)
    # Include all fixed work in the independent resource audit. Keep the run
    # itself restricted to pending tasks because apply_run protects actuals.
    starts, finishes = dict(result.starts), dict(result.finishes)
    for a in problem.activities:
        if a.id not in pending_ids:
            starts[a.id] = a.actual_start or a.planned_start
            finishes[a.id] = a.actual_finish or fixed_finishes.get(a.id, a.planned_finish)
    return result, starts, finishes, crews



def _intensity_violations(problem, opts) -> list[dict]:
    """Tareas cuya intensidad (HH/día = HH ÷ duración) supera la capacidad diaria TOTAL de su habilidad.

    Ningún orden de ejecución puede cumplirlas: se informa la causa exacta en vez de un programa vacío.
    """
    if not getattr(opts, "respect_resource_limits", True):
        return []
    capacity = defaultdict(float)
    for r in problem.resources:
        if not r.active:
            continue
        per_day = r.capacity_hours_day * r.max_units
        if getattr(opts, "allow_overtime", False):
            per_day += r.max_overtime_hours_day * r.max_units
        for skill in r.skills:
            capacity[skill] += per_day
    # A4-06: con asignaciones explícitas (nominales o genéricas) la demanda la definen ellas, no la ficha (A1-01).
    explicit = {a.activity_id for a in problem.assignments}
    out = []
    for a in problem.activities:
        if (a.actual_start or a.actual_finish or a.id in explicit or not a.required_skill
                or a.required_skill not in capacity or a.duration_days <= 0 or a.work_hours <= 0):
            continue
        daily = a.work_hours / a.duration_days
        cap = capacity[a.required_skill]
        if daily > cap + 1e-6:
            need = a.work_hours / cap if cap > 0 else float("inf")
            out.append(dict(type="intensity", activities=[a.id], date=None, detail=(
                f"{a.code}: requiere {daily:.1f} HH/día ({a.work_hours:g} HH en {a.duration_days} días) y la "
                f"capacidad total de «{a.required_skill}» es {cap:.1f} HH/día. Aumente la duración a al menos "
                f"{need:.0f} días o la capacidad/dotación de esa habilidad.").replace(".0 HH", " HH")))
    return out


def solve_project(conn, pid, weights=None, options=None, apply=True) -> dict:
    """Genera un programa factible; con ``apply=False`` en Mantenimiento no deja efectos en la base.

    La simulación de Mantenimiento necesita persistir temporalmente el inventario de ocurrencias para
    nivelar y auditar. Si la simulación creó o reemplazó ocurrencias, todo se revierte (inventario y
    corrida) para que «simular» nunca modifique el proyecto; la corrida no queda en el historial porque
    sus actividades ya no existen. Si el inventario no cambió, la corrida se conserva y puede aplicarse.
    """
    project = repo.get(conn, 'projects', pid)
    if project.mode == 'mantenimiento' and any(t.quantity_total is not None or t.is_daily_round for t in repo.list_entities(conn, 'periodic_tasks', pid)):
        from .teams import solve_teams
        return solve_teams(conn, pid, weights, options, apply)
    if apply or project.mode != 'mantenimiento':
        return _solve_project(conn, pid, weights, options, apply)
    from uuid import uuid4
    name = 'sim_' + uuid4().hex
    conn.execute(f'SAVEPOINT {name}')
    try:
        result = _solve_project(conn, pid, weights, options, apply)
    except BaseException:
        conn.execute(f'ROLLBACK TO SAVEPOINT {name}')
        conn.execute(f'RELEASE SAVEPOINT {name}')
        raise
    generation = result.get('generation') or {}
    if generation.get('created') or generation.get('regenerated'):
        run_id = result.get('run_id')
        # A4-04: borrar el archivo mientras el SAVEPOINT aún excluye a otros escritores; después del
        # rollback, SQLite puede reutilizar ese ID para una corrida confirmada por otra conexión.
        if run_id is not None:
            (services.DATA_DIR / 'runs' / f'{run_id}.json').unlink(missing_ok=True)
        conn.execute(f'ROLLBACK TO SAVEPOINT {name}')
        conn.execute(f'RELEASE SAVEPOINT {name}')
        result.update(run_id=None, applied=False, simulated=True)
        result['warnings'] = [*result.get('warnings', []), 'Simulación: las ocurrencias se generaron solo para evaluar el '
                              'programa; no se guardó ningún cambio. Use «Generar programa» para aplicarlo.']
    else:
        conn.execute(f'RELEASE SAVEPOINT {name}')
    return result


def _solve_project(conn, pid, weights=None, options=None, apply=True) -> dict:
    """Options accepts OptimizeOptions or a dict, plus generation/horizon keys.

    No task dates are required. Data errors raise ValueError; an unsuccessful
    search returns feasible=False and a run containing no applicable dates.
    """
    started = perf_counter()
    values = dict(options) if isinstance(options, dict) else {}
    regenerate = values.pop('regenerate', False)
    first_arg, last_arg = values.pop('horizon_start', None), values.pop('horizon_end', None)
    try:
        opts = options if isinstance(options, d.OptimizeOptions) else d.OptimizeOptions(**values)
        weights = d.ObjectiveWeights(**weights) if isinstance(weights, dict) else weights or d.ObjectiveWeights(makespan=1, window=0)
    except TypeError as exc:
        raise ValueError(f'Opciones o pesos de programación inválidos: {exc}') from exc
    if opts.method not in {'auto', 'heuristic', 'cpsat'}:
        raise ValueError('Método de optimización desconocido.')
    project = repo.get(conn, 'projects', pid)
    if opts.role_mode not in {'fractional', 'indivisible'}:
        raise ValueError('Modo de roles desconocido.')
    if opts.role_mode == 'indivisible':
        from .role_schedules import solve_roles
        return solve_roles(conn, pid, opts, apply=apply, first=first_arg, last=last_arg)
    generation = None
    team_summary, team_warnings = [], []
    if project.mode == 'mantenimiento':
        from .maintenance import generate_occurrences
        first, last = _date(first_arg, project.start_date), _date(last_arg, project.end_date)
        if last is None or last < first:
            raise ValueError('Mantenimiento requiere un horizonte válido con fecha de fin.')
        # Siempre se generan las ocurrencias del horizonte: con regenerate=False sólo se AGREGAN las que
        # faltan (p. ej. tareas nuevas) y las existentes quedan intactas; con regenerate=True se reemplazan
        # (respetando avances, bloqueos y líneas base).
        before_count = conn.execute('SELECT COUNT(*) FROM activities WHERE project_id=? AND periodic_task_id IS NOT NULL', (pid,)).fetchone()[0]
        calendars = {c.id: WorkCalendar(c, first, last) for c in repo.list_entities(conn, 'calendars')}
        default = project.calendar_id or next(iter(calendars))
        resources = repo.list_entities(conn, 'resources', pid)
        tasks = repo.list_entities(conn, 'periodic_tasks', pid)
        generated = generate_occurrences(tasks, first, last, calendars, default,
                                         resources=resources, warnings=team_warnings)
        if any(o.batch_index is not None for o in generated):
            from .teams import summarize_occurrences
            opts = replace(opts, respect_resource_limits=True)
            team_summary, extra_warnings, capacity_errors = summarize_occurrences(generated, resources, calendars, tasks, allow_overtime=opts.allow_overtime)
            team_warnings = list(dict.fromkeys([*team_warnings, *extra_warnings]))
            if capacity_errors:
                return dict(status='infactible', feasible=False, applied=False, created=0, run_id=None,
                            violations=capacity_errors, warnings=team_warnings, team_summary=team_summary,
                            generation=dict(generated=len(generated), created=0, kept=before_count),
                            infeasibility='demostrada', elapsed_s=perf_counter()-started)

        # Generator dates are provisional; persisting them would bypass the
        # audit when apply=False or when resource leveling is infeasible.
        previous = {a.code: a for a in repo.list_entities(conn, 'activities', pid)}
        generated = [replace(o, planned_start=previous[o.code].planned_start if o.code in previous else None,
                               planned_finish=previous[o.code].planned_finish if o.code in previous else None) for o in generated]
        repo.save_occurrences(conn, pid, generated, replace=bool(regenerate), return_entities=False)
        after_count = conn.execute('SELECT COUNT(*) FROM activities WHERE project_id=? AND periodic_task_id IS NOT NULL', (pid,)).fetchone()[0]
        generation = dict(generated=len(generated), created=max(0, after_count - before_count) if not regenerate else len(generated),
                          kept=before_count if not regenerate else 0, regenerated=bool(regenerate))
    problem = services.build_problem(conn, pid)
    if first_arg is not None or last_arg is not None:
        first, last = _date(first_arg, problem.horizon_start), _date(last_arg, problem.horizon_end)
        if last < first:
            raise ValueError('El fin del horizonte es anterior a su inicio.')
        problem = replace(problem, horizon_start=first, horizon_end=last,
                          calendars={cid: WorkCalendar(cal.cal, first, last) for cid, cal in problem.calendars.items()})
    errors = validate_network(problem)
    if errors:
        raise ValueError('La red no es válida: ' + '; '.join(errors))
    scenario_hash = services._scenario_fingerprint(conn, pid)
    crews = {}
    if project.mode == 'mantenimiento':
        # Generar programa always assigns people/crews, as requested by E1.
        opts = replace(opts, assign_workers=True)
        result, starts, finishes, crews = _maintenance(conn, pid, problem, weights, opts)
        report = verify_with_options(problem, starts, finishes, opts, worker_assignment=result.worker_assignment, crews=crews)
        kind = 'maintenance_level'
    else:
        pre = _intensity_violations(problem, opts)
        if pre:
            result = d.OptimizeResult({}, {}, status='infactible', method_used='precheck',
                warnings=['Hay tareas que ninguna secuencia puede cumplir con la capacidad disponible.'])
            report = VerificationReport(feasible=False, violations=pre)
        else:
            result = optimize(problem, weights, opts)
            report = verify_with_options(problem, result.starts, result.finishes, opts)
        kind = 'optimize'
    feasible = report.feasible and result.status != 'infactible'
    warnings = list(dict.fromkeys([*team_warnings, *result.warnings, *(v['detail'] for v in report.warnings)]))
    if not feasible:
        result.status = 'infactible'
        result.starts, result.finishes, result.worker_assignment = {}, {}, {}
        result.after, result.profile_after, crews = None, None, {}
        if not report.violations:
            report.violations.append(dict(type='search', activities=[], date=None,
                detail='No se encontró una solución factible; revise capacidad, cuadrillas, ventanas y horizonte.'))
        if result.method_used == 'precheck':
            warnings.append('No se aplica ningún programa: la infactibilidad está demostrada por los datos (ver violaciones).')
        else:
            warnings.append('No se aplica ningún programa sin verificación factible. Un límite de búsqueda no demuestra infactibilidad matemática.')
    result.elapsed_s = perf_counter() - started
    payload = repo.json_value(result)
    payload.update(crew_assignment=repo.json_value(crews), verification=repo.json_value(report))
    run_id = services._save_run(conn, pid, kind, payload, scenario_hash=scenario_hash,
        weights=d.to_dict(weights), options=d.to_dict(opts), metrics_before=payload['before'], metrics_after=payload['after'],
        status=result.status, elapsed_s=result.elapsed_s)
    applied = False
    if apply and feasible:
        services.apply_run(conn, pid, run_id)
        applied = True
    return dict(status=result.status, feasible=feasible, violations=report.violations, warnings=warnings,
                before=payload['before'], after=payload['after'], metrics={'before': payload['before'], 'after': payload['after']},
                method_used=result.method_used, elapsed_s=perf_counter() - started, run_id=run_id, applied=applied,
                generation=generation, team_summary=team_summary,
                # P1-06a: distingue una infactibilidad demostrada por los datos (prechequeo de capacidad o
                # intensidad) de una búsqueda que no encontró solución dentro del límite de tiempo.
                infeasibility=None if feasible else ('demostrada' if result.method_used == 'precheck' else 'no_encontrada'))


def solve_project_blocks(conn, pid, *, horizon_blocks, block_hours=4,
                         target_code=None, time_limit_s=30, seed=0):
    """Compatibility preview; the normal solve endpoint also supports apply."""
    return solve_project(conn, pid, options=d.OptimizeOptions(
        role_mode='indivisible', horizon_blocks=horizon_blocks, block_hours=block_hours,
        target_code=target_code, time_limit_s=time_limit_s, seed=seed,
        allow_overtime=False), apply=False)
