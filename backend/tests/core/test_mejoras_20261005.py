from datetime import date
import pytest
from ganttier import repo, services


@pytest.mark.parametrize('period, start', [('weekly', date(2027, 1, 4)), ('monthly', date(2027, 1, 1)), ('annual', date(2027, 1, 1))])
def test_future_report_keeps_schedule_without_performance(conn, project, period, start):
    repo.create(conn, 'activities', dict(project_id=project.id, code='FUT', name='Futura',
        duration_days=1, work_hours=8, planned_start='2027-01-05', planned_finish='2027-01-05'))
    report = services.build_report_data(conn, project.id, period, start)
    assert [row.code for row in report.scheduled_in_period] == ['FUT']
    assert report.performance == []
    assert report.completed_in_period == []
    assert 'posterior' in report.notes.lower()
    assert repo.get(conn, 'projects', project.id).data_date == project.data_date


@pytest.mark.parametrize('window_start, window_end, expected', [
    ('2026-11-02', '2026-11-02', ('2026-11-02', '2026-11-02')),
    ('2026-10-20', '2026-11-10', ('2026-11-01', '2026-11-10')),
    (None, '2026-11-15', ('2026-11-01', '2026-11-15')),
])
def test_macro_intersects_explicit_window(conn, project, window_start, window_end, expected):
    row = repo.create(conn, 'activities', dict(project_id=project.id, code='D', name='Diaria',
        macro_month='2026-11', window_start=window_start, window_end=window_end, window_hard=True))
    assert (str(row.window_start), str(row.window_end)) == expected


def test_macro_disjoint_window_rejected_without_write(conn, project):
    with pytest.raises(ValueError, match='ventana|window'):
        repo.create(conn, 'activities', dict(project_id=project.id, code='X', name='Fuera',
            macro_month='2026-11', window_start='2026-12-01', window_end='2026-12-02'))
    assert repo.list_entities(conn, 'activities', project.id) == []


def test_macro_patch_preserves_narrow_window_and_allows_moving_macro_only(conn, project):
    row = repo.create(conn, 'activities', dict(project_id=project.id, code='X', name='X',
        window_start='2026-11-02', window_end='2026-11-02'))
    updated = repo.update(conn, 'activities', row.id, {'macro_month': '2026-11'})
    assert updated.window_start == updated.window_end == date(2026, 11, 2)
    row2 = repo.create(conn, 'activities', dict(project_id=project.id, code='Y', name='Y', macro_month='2026-11'))
    updated = repo.update(conn, 'activities', row2.id, {'macro_month': '2026-12'})
    assert updated.window_start == date(2026, 12, 1)
    assert updated.window_end == date(2026, 12, 31)
    # Existing explicit-window edit behavior still removes the macro shortcut.
    updated = repo.update(conn, 'activities', row.id, {'window_start': '2027-01-01', 'window_end': '2027-01-02'})
    assert updated.macro_month is None


def test_report_explains_global_milestone_scope(conn, project):
    for code, when in [('JAN', '2026-01-10'), ('FEB', '2026-02-10')]:
        repo.create(conn, 'activities', dict(project_id=project.id, code=code, name=code,
            duration_days=0, work_hours=0, planned_start=when, planned_finish=when))
    report = services.build_report_data(conn, project.id, 'monthly', date(2026, 1, 1))
    assert [row.code for row in report.milestones] == ['JAN', 'FEB']
    assert 'hitos conservan el alcance global' in report.notes


def test_batch_validation_error_identifies_row_and_code_and_rolls_back(conn, project):
    rows = [dict(project_id=project.id, code=f'A{i}', name='A') for i in range(2002)]
    rows[-1]['duration_days'] = -1
    with pytest.raises(ValueError, match='fila 2002.*A2001.*duration_days'):
        repo.create_many(conn, 'activities', rows)
    assert repo.list_entities(conn, 'activities', project.id) == []


def test_solve_capacity_diagnostic_identifies_demand_and_qualifies_role_hint(conn, project, monkeypatch):
    from ganttier.api import solve as api
    a = repo.create(conn, 'activities', dict(project_id=project.id, code='A', name='Dos roles'))
    repo.create(conn, 'resources', dict(project_id=project.id, name='Ana', skills=['C', 'I']))
    demands = [repo.create(conn, 'assignments', dict(activity_id=a.id, skill=skill, units=1, work_hours=8)) for skill in ['C', 'I']]
    original = dict(feasible=False, warnings=[], verification={'feasible': False, 'violations': [dict(type='capacity', activities=[a.id], detail='Capacidad por habilidad')]})
    monkeypatch.setattr(api, 'solve_project', lambda *args: dict(original))
    response = api.solve(project.id, api.SolveRequest(), conn)
    assert response['feasible'] is False
    diagnostics = response['capacity_diagnostics']
    assert {d['assignment_id'] for d in diagnostics} == {d.id for d in demands}
    assert all(d['activity_code'] == 'A' for d in diagnostics)
    assert any('role_mode' in w and 'bloques' in w for w in response['warnings'])
    assert original['warnings'] == []


def test_native_dh_api_contract_persistence_and_regeneration(conn):
    from ganttier.api.schemas import PeriodicTask
    cid = repo.create(conn, 'calendars', dict(name='CL diario', workweek='1111100', hours_per_day=8, holidays_country='CL')).id
    p = repo.create(conn, 'projects', dict(name='O&M', mode='mantenimiento', calendar_id=cid,
        start_date='2026-11-01', end_date='2027-10-31', data_date='2026-11-01'))
    task = PeriodicTask(project_id=p.id, code='R', name='Ronda', frequency='DH', first_due='2026-11-01',
                        tol_before_days=0, tol_after_days=0, duration_days=1)
    repo.create(conn, 'periodic_tasks', task.model_dump())
    assert services.generate_maintenance(conn, p.id, date(2026, 11, 1), date(2026, 11, 30))['created'] == 21
    rows = repo.list_entities(conn, 'activities', p.id)
    assert all(a.planned_start == a.planned_finish and a.planned_start is not None for a in rows)
    original = {a.code: a.planned_start for a in rows}
    assert services.generate_maintenance(conn, p.id, date(2026, 11, 16), date(2026, 11, 30), replace=False)['created'] == 0
    assert {a.code: a.planned_start for a in repo.list_entities(conn, 'activities', p.id)} == original


@pytest.mark.parametrize('fmt', ['html', 'xlsx', 'pdf'])
def test_future_report_renders_formats(conn, project, monkeypatch, local_dir, fmt):
    monkeypatch.setattr(services, 'DATA_DIR', local_dir)
    repo.create(conn, 'activities', dict(project_id=project.id, code='F', name='Futura',
        planned_start='2027-01-05', planned_finish='2027-01-05'))
    files = services.render_reports(conn, project.id, 'annual', date(2027, 1, 1), [fmt])
    assert len(files) == 1
    assert list((local_dir/'informes').glob(f'*.{fmt}'))
