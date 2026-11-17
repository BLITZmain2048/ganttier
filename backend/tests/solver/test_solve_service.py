"""Run orchestration against real SQLite even without FastAPI installed."""
from datetime import date
import json

import pytest

from ganttier import db, repo, services
from ganttier.solve import solve_project


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path / 'solve.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path)
    conn = db.connect()
    db.initialize(conn)
    yield conn
    conn.close()


def project(conn, mode='proyecto'):
    cid = repo.create(conn, 'calendars', {'name': 'Sin feriados', 'holidays_country': None}).id
    return repo.create(conn, 'projects', dict(name='Lista', mode=mode, start_date=date(2026, 1, 5),
        data_date=date(2026, 1, 5), end_date=date(2026, 3, 31) if mode == 'mantenimiento' else None, calendar_id=cid))


@pytest.mark.parametrize('apply', [True, False])
def test_project_service(database, apply):
    p = project(database)
    repo.create(database, 'resources', dict(project_id=p.id, name='Ana', skills=['electrico'], calendar_id=p.calendar_id))
    acts = repo.create_many(database, 'activities', [dict(project_id=p.id, code=f'A{i}', name=f'Tarea {i}', work_hours=8, required_skill='electrico') for i in range(3)])
    repo.create(database, 'relations', dict(project_id=p.id, pred_id=acts[0].id, succ_id=acts[1].id))
    result = solve_project(database, p.id, options={'method': 'heuristic', 'time_limit_s': 1}, apply=apply)
    assert result['feasible'] and result['applied'] == apply, result
    assert all(bool(a.planned_start) == apply for a in repo.list_entities(database, 'activities', p.id))
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('apply', [True, False])
def test_generate_level_verify_apply_maintenance(database, apply):
    p = project(database, 'mantenimiento')
    for name in ['Ana', 'Beto']:
        repo.create(database, 'resources', dict(project_id=p.id, name=name, skills=['electrico'], calendar_id=p.calendar_id))
    repo.create(database, 'periodic_tasks', dict(project_id=p.id, code='M', name='Revisión', frequency='M',
        first_due=p.start_date, duration_days=1, work_hours=16, crew_size=2, required_skill='electrico', calendar_id=p.calendar_id))
    assert not repo.list_entities(database, 'activities', p.id)
    result = solve_project(database, p.id, options={'method': 'heuristic', 'time_limit_s': 1}, apply=apply)
    assert result['feasible'] and result['applied'] == apply, result
    acts = repo.list_entities(database, 'activities', p.id)
    if not apply:
        # D2: la simulación evalúa las 3 ocurrencias pero no deja inventario ni corrida.
        assert acts == [] and result['run_id'] is None and result['generation']['generated'] == 3
        assert not repo.list_entities(database, 'schedule_runs', p.id)
        return
    assert len(acts) == 3 and all(a.planned_start for a in acts)
    assert len(repo.list_entities(database, 'assignments', p.id)) == 6
    rerun = solve_project(database, p.id, options={'regenerate': True, 'method': 'heuristic'}, apply=apply)
    assert rerun['feasible'], rerun
    assert len(repo.list_entities(database, 'activities', p.id)) == 3


def test_bad_result_cannot_be_applied_later(database):
    p = project(database)
    repo.create(database, 'activities', dict(project_id=p.id, code='A', name='A', work_hours=8, required_skill='sin_personal'))
    result = solve_project(database, p.id)
    assert not result['feasible'] and not result['applied']
    with pytest.raises(ValueError, match='infactible'):
        services.apply_run(database, p.id, result['run_id'])


def test_maintenance_cutoff(database):
    p = project(database, 'mantenimiento')
    repo.update(database, 'projects', p.id, {'data_date': date(2026, 2, 2)})
    repo.create(database, 'resources', dict(project_id=p.id, name='Ana', skills=['electrico']))
    repo.create(database, 'periodic_tasks', dict(project_id=p.id, code='M', name='M', frequency='M', first_due=p.start_date, work_hours=8, required_skill='electrico'))
    result = solve_project(database, p.id)
    assert result['feasible'], result
    assert all(a.planned_start >= date(2026, 2, 2) for a in repo.list_entities(database, 'activities', p.id))


@pytest.mark.parametrize('constraint,expected', [('MSO', date(2026, 1, 9)), ('MFO', date(2026, 1, 8)), ('FNET', date(2026, 1, 8))])
def test_maintenance_hard_date_constraints(database, constraint, expected):
    p = project(database, 'mantenimiento')
    repo.create(database, 'resources', dict(project_id=p.id, name='Ana', skills=['electrico'], calendar_id=p.calendar_id))
    repo.create(database, 'activities', dict(project_id=p.id, code='A', name='A', duration_days=2,
        work_hours=16, required_skill='electrico', constraint_type=constraint, constraint_date=date(2026, 1, 9)))
    result = solve_project(database, p.id)
    assert result['feasible'], result
    a = repo.list_entities(database, 'activities', p.id)[0]
    assert a.planned_start == expected


def test_regenerate_preview_preserves_existing_schedule(database):
    p = project(database, 'mantenimiento')
    repo.create(database, 'resources', dict(project_id=p.id, name='Ana', skills=['electrico']))
    repo.create(database, 'periodic_tasks', dict(project_id=p.id, code='M', name='M', frequency='M', first_due=p.start_date, required_skill='electrico'))
    assert solve_project(database, p.id)['feasible']
    dates = {a.id: (a.planned_start, a.planned_finish) for a in repo.list_entities(database, 'activities', p.id)}
    assert solve_project(database, p.id, options={'regenerate': True}, apply=False)['feasible']
    assert dates == {a.id: (a.planned_start, a.planned_finish) for a in repo.list_entities(database, 'activities', p.id)}


def test_fixed_maintenance_load_is_included_in_verification(database):
    p = project(database, 'mantenimiento')
    r = repo.create(database, 'resources', dict(project_id=p.id, name='Ana', skills=['electrico']))
    for i in range(2):
        a = repo.create(database, 'activities', dict(project_id=p.id, code=f'A{i}', name='A', work_hours=8,
            required_skill='electrico', window_start=p.start_date, window_end=p.start_date, window_hard=True,
            locked=i == 0, planned_start=p.start_date if i == 0 else None, planned_finish=p.start_date if i == 0 else None))
        repo.create(database, 'assignments', dict(activity_id=a.id, resource_id=r.id, work_hours=8))
    result = solve_project(database, p.id)
    assert not result['feasible'] and not result['applied']
    assert any(v['type'] in {'resource_capacity', 'missing_dates'} for v in result['violations'])


def test_fixed_maintenance_reserves_worker_before_search(database):
    p = project(database, 'mantenimiento')
    r = repo.create(database, 'resources', dict(project_id=p.id, name='Ana', skills=['electrico']))
    fixed = repo.create(database, 'activities', dict(project_id=p.id, code='F', name='Fija', work_hours=8,
        required_skill='electrico', locked=True, planned_start=p.start_date, planned_finish=p.start_date))
    repo.create(database, 'assignments', dict(activity_id=fixed.id, resource_id=r.id, work_hours=8))
    pending = repo.create(database, 'activities', dict(project_id=p.id, code='A', name='Pendiente', work_hours=8, required_skill='electrico'))
    result = solve_project(database, p.id)
    assert result['feasible'], result
    assert repo.get(database, 'activities', pending.id).planned_start > p.start_date
