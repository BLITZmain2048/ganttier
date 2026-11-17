"""Persistence and application of opt-in project role schedules."""
from dataclasses import replace
from datetime import date, timedelta
import json

from . import domain as d, repo, services
from .calendars import WorkCalendar
from .engine import validate_network
from .engine.verify import verify_role_schedule


def _problem(conn, pid, options, first=None, last=None):
    if options.role_mode != 'indivisible':
        raise ValueError('Se requiere role_mode=indivisible.')
    if options.method not in {'auto', 'cpsat'}:
        raise ValueError('Roles indivisibles requiere CP-SAT (method auto o cpsat).')
    problem = services.build_problem(conn, pid)
    if problem.project.mode != 'proyecto':
        raise ValueError('Los roles indivisibles requieren modo proyecto.')
    origin = date.fromisoformat(first) if isinstance(first, str) else first or problem.project.start_date
    end = date.fromisoformat(last) if isinstance(last, str) else last
    if options.horizon_blocks is not None:
        if type(options.horizon_blocks) is not int or not 1 <= options.horizon_blocks <= 2000:
            raise ValueError('horizon_blocks debe ser entero entre 1 y 2000.')
        limit = origin + timedelta(days=options.horizon_blocks - 1)
        if end is not None and end != limit:
            raise ValueError('horizon_end y horizon_blocks son incompatibles.')
        end = limit
    end = end or problem.project.end_date
    if end is None or end < origin:
        raise ValueError('Roles indivisibles requiere un horizonte explícito válido.')
    return replace(problem, horizon_start=origin, horizon_end=end,
                   calendars={cid: WorkCalendar(cal.cal, origin, end + timedelta(days=1))
                              for cid, cal in problem.calendars.items()})


def solve_roles(conn, pid, options, *, apply, first=None, last=None):
    from .engine.role_blocks import optimize_role_blocks
    problem = _problem(conn, pid, options, first, last)
    errors = validate_network(problem)
    if errors:
        raise ValueError('La red no es válida: ' + '; '.join(errors))
    scenario_hash = services._scenario_fingerprint(conn, pid)
    result = optimize_role_blocks(problem, options, block_hours=options.block_hours,
                                  target_code=options.target_code)
    result.setdefault('starts', {})
    result.setdefault('finishes', {})
    result.setdefault('role_assignment', [])
    result.setdefault('verification', dict(feasible=False, violations=[dict(
        type='search', activities=[], date=None, detail='No se encontró un programa factible.')], warnings=[]))
    result['violations'] = result['verification']['violations']
    result['warnings'] = [v['detail'] for v in result['verification'].get('warnings', [])]
    result['method_used'] = 'cpsat_role_blocks'
    result['options'] = d.to_dict(options)
    result['infeasibility'] = None if result['feasible'] else result.get('infeasibility', 'no_encontrada')
    metrics = dict(primary_finish_block=result.get('primary_finish_block'),
                   personnel_cost=result.get('personnel_cost')) if result['feasible'] else None
    result.update(before=None, after=metrics, metrics=dict(before=None, after=metrics))
    run_id = services._save_run(conn, pid, 'role_blocks', result, scenario_hash=scenario_hash,
        options=d.to_dict(options), status=result['status'], elapsed_s=result.get('elapsed_s', 0),
        metrics_after=metrics)
    result['run_id'] = run_id
    if apply and result['feasible']:
        services.apply_run(conn, pid, run_id)
        result['applied'] = True
    return result


def audit_payload(conn, pid, payload):
    """Recheck the actual allocation and dates; never trust a stored feasible flag."""
    if not payload.get('feasible') or not payload.get('time_axis'):
        raise ValueError('No se puede aplicar una corrida de roles sin solución verificada.')
    options = d.OptimizeOptions(**payload['options'])
    axis = payload['time_axis']
    options = replace(options, horizon_blocks=axis['horizon_blocks'])
    problem = _problem(conn, pid, options, axis['origin'])
    starts = {int(k): date.fromisoformat(v) for k, v in payload['starts'].items()}
    finishes = {int(k): date.fromisoformat(v) for k, v in payload['finishes'].items()}
    report = verify_role_schedule(problem, starts, finishes, payload['role_assignment'],
                                  options, block_hours=options.block_hours)
    if not report.feasible:
        raise ValueError('El verificador rechazó la corrida de roles: ' +
                         '; '.join(v['detail'] for v in report.violations))
    return report


def persist_allocation(conn, pid, run_id, payload):
    """Called inside the same transaction that applies dates."""
    conn.execute('DELETE FROM role_schedules WHERE project_id=?', (pid,))
    if payload is None:
        return
    conn.execute('INSERT INTO role_schedules VALUES (?,?,?,?,?)', (
        pid, run_id, services._scenario_fingerprint(conn, pid),
        json.dumps(payload['options']), json.dumps(payload['time_axis'])))
    conn.executemany('INSERT INTO role_allocations VALUES (?,?,?,?,?)', [
        (pid, row['activity_id'], row['source_assignment_id'], row['resource_id'], row['skill'])
        for row in payload['role_assignment']])


def allocation_state(conn, pid):
    repo.get(conn, 'projects', pid)
    schedule = conn.execute('SELECT * FROM role_schedules WHERE project_id=?', (pid,)).fetchone()
    if schedule is None:
        return dict(applied=False, valid=False, run_id=None, role_assignment=[])
    allocation = [dict(row) for row in conn.execute('''SELECT activity_id, source_assignment_id,
        resource_id, skill FROM role_allocations WHERE project_id=? ORDER BY activity_id, resource_id''', (pid,))]
    return dict(applied=True, valid=schedule['scenario_hash'] == services._scenario_fingerprint(conn, pid),
                run_id=schedule['run_id'], role_assignment=allocation,
                time_axis=json.loads(schedule['time_axis']))


def nominal_assignments(conn, pid):
    """An explicit export of the applied schedule, preserving source demands in DB."""
    state = allocation_state(conn, pid)
    if not state['valid']:
        raise ValueError('La asignación nominal no existe o quedó obsoleta; vuelva a resolver.')
    resources = {r.id: r for r in repo.list_entities(conn, 'resources', pid)}
    acts = {a.id: a for a in repo.list_entities(conn, 'activities', pid)}
    rows = [r for r in repo.list_entities(conn, 'assignments', pid)
            if r.resource_id in resources and resources[r.resource_id].kind == 'equipo']
    for i, row in enumerate(state['role_assignment'], 1):
        rows.append(d.Assignment(-i, row['activity_id'], resource_id=row['resource_id'],
            skill=row['skill'], units=1,
            work_hours=acts[row['activity_id']].duration_days * state['time_axis']['block_hours']))
    return rows
