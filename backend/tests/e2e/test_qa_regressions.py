"""Regresiones del límite de persistencia, ejecutables sin dependencias HTTP."""
from datetime import date

import pytest

from ganttier import db, domain as d, repo, services


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path / 'qa.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path)
    conn = db.connect()
    db.initialize(conn)
    p = repo.create(conn, 'projects', {'name': 'QA', 'mode': 'mantenimiento',
        'start_date': date(2026, 1, 1), 'end_date': date(2026, 3, 31), 'calendar_id': 5})
    yield conn, p
    conn.close()


def test_relation_cycle_is_rejected_at_write(store):
    conn, p = store
    a, b, c = repo.create_many(conn, 'activities', [dict(project_id=p.id, code=x, name=x) for x in 'ABC'])
    for pred, succ in ((a, b), (b, c)):
        repo.create(conn, 'relations', dict(project_id=p.id, pred_id=pred.id, succ_id=succ.id))
    with pytest.raises(ValueError, match='ciclo'):
        repo.create(conn, 'relations', dict(project_id=p.id, pred_id=c.id, succ_id=a.id))
    assert len(repo.list_entities(conn, 'relations', p.id)) == 2


@pytest.mark.parametrize('replace', [False, True])
def test_regeneration_preserves_progress_and_baseline(store, replace):
    conn, p = store
    repo.create(conn, 'periodic_tasks', dict(project_id=p.id, code='T', name='T', first_due=date(2026, 1, 5)))
    services.generate_maintenance(conn, p.id, p.start_date, p.end_date)
    a = repo.list_entities(conn, 'activities', p.id)[0]
    repo.create(conn, 'progress', dict(activity_id=a.id, date=date(2026, 1, 6), percent_complete=50, actual_start=date(2026, 1, 5)))
    baseline = services.create_baseline(conn, p.id)
    services.sync_progress(conn, p.id)
    for _ in range(2):
        services.generate_maintenance(conn, p.id, p.start_date, p.end_date, replace)
    assert len(repo.list_entities(conn, 'activities', p.id)) == 3
    assert repo.get(conn, 'activities', a.id).percent_complete == 50
    assert len(repo.list_entities(conn, 'progress', p.id)) == 1
    assert len(repo.list_entities(conn, 'baseline_activities', filters={'baseline_id': baseline.id})) == 3


def test_old_maintenance_run_cannot_move_recorded_work(store):
    conn, p = store
    a = repo.create(conn, 'activities', dict(project_id=p.id, code='A', name='A', planned_start=date(2026, 1, 5), planned_finish=date(2026, 1, 5)))
    run = services._save_run(conn, p.id, 'maintenance_level', {'starts': {str(a.id): '2026-01-10'}, 'finishes': {str(a.id): '2026-01-10'}}, status='factible')
    repo.create(conn, 'progress', dict(activity_id=a.id, date=date(2026, 1, 6), percent_complete=50, actual_start=date(2026, 1, 5)))
    with pytest.raises(ValueError, match='La programación cambió'):
        services.apply_run(conn, p.id, run)
    assert repo.get(conn, 'activities', a.id).planned_start == date(2026, 1, 5)


@pytest.mark.parametrize('fields', [
    {'percent_complete': 100},
    {'percent_complete': 50, 'actual_start': date(2026, 1, 7)},
    {'percent_complete': 50, 'actual_start': date(2026, 1, 5), 'actual_finish': date(2026, 1, 7)},
])
def test_invalid_actual_dates_rejected(store, fields):
    conn, p = store
    a = repo.create(conn, 'activities', dict(project_id=p.id, code='A', name='A'))
    with pytest.raises(ValueError):
        repo.validate_progress_entries(conn, [dict(activity_id=a.id, date=date(2026, 1, 6), **fields)])


def test_optimize_apply_keeps_full_crew(store):
    conn, p = store
    for name in ('Ana', 'Luis'):
        repo.create(conn, 'resources', dict(project_id=p.id,name=name,skills=['E'],calendar_id=5))
    repo.create(conn,'periodic_tasks',dict(project_id=p.id,code='T',name='T',frequency='A',
        first_due=date(2026,1,5),duration_days=1,work_hours=16,crew_size=2,required_skill='E',calendar_id=5))
    services.generate_maintenance(conn,p.id,p.start_date,p.end_date)
    result = services.run_optimize(conn,p.id,options=d.OptimizeOptions(method='heuristic',time_limit_s=1),apply=True)
    assert result['status'] != 'infactible'
    assignments = repo.list_entities(conn,'assignments',p.id)
    assert len(assignments) == 2
    assert len({a.resource_id for a in assignments}) == 2
    assert sum(a.work_hours for a in assignments) == 16
    assert all(a.units == 1 for a in assignments)
    second = services.run_optimize(conn,p.id,options=d.OptimizeOptions(method='heuristic',time_limit_s=1))
    assert second['status'] != 'infactible'


@pytest.mark.parametrize('period',['weekly','monthly','annual'])
@pytest.mark.parametrize('fmt',['xlsx','pdf','docx','html'])
def test_report_service_integration(store, period, fmt):
    if fmt == 'pdf': pytest.importorskip('reportlab')
    if fmt == 'docx': pytest.importorskip('docx')
    conn,p = store
    repo.update(conn,'projects',p.id,{'data_date':date(2026,1,6)})
    a = repo.create(conn,'activities',dict(project_id=p.id,code='A',name='A',duration_days=2,
        work_hours=16,cost_budget=100,planned_start=date(2026,1,5),planned_finish=date(2026,1,6)))
    services.create_baseline(conn,p.id)
    repo.create(conn,'progress',dict(activity_id=a.id,date=date(2026,1,6),percent_complete=50,
        actual_start=date(2026,1,5),actual_hours=8,actual_cost=50))
    report = services.build_report_data(conn,p.id,period,date(2026,1,6))
    assert report.scurve.planned_cum[-1] == pytest.approx(100)
    assert 0 <= report.evm.EV <= report.evm.BAC
    files = services.render_reports(conn,p.id,period,date(2026,1,6),[fmt])
    assert len(files) == 1
    assert (services.DATA_DIR/'informes'/files[0]['filename']).stat().st_size > 500


@pytest.mark.parametrize('identifier',[[],{},None,2**100])
def test_malformed_identifier_is_validation_error(store,identifier):
    conn,_ = store
    with pytest.raises(ValueError):
        repo.get(conn,'activities',identifier)


def test_integer_overflow_is_validation_error(store):
    conn,p = store
    with pytest.raises(ValueError):
        repo.create(conn,'activities',dict(project_id=p.id,code='A',name='A',duration_days=2**100))
