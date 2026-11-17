"""Aplicación de lotes por horas y compatibilidad de opciones persistidas."""
import json
from datetime import date, timedelta

import pytest

from ganttier import db, repo, services, teams


def test_occurrence_keeps_legacy_positional_continuity():
    from ganttier.domain import Occurrence
    day = date(2027, 1, 1)
    occurrence = Occurrence(1, 1, 'O', 'O', day, day, day, 1, 3, 3, '', 1, 1,
                            None, None, True, '', '', False, 50, None,
                            1, 10, 'u', 1, 'occurrence', True)
    assert occurrence.batch_continuous_in_shift is True
    assert occurrence.duration_hours is None
    assert occurrence.checklist_codes == []


@pytest.fixture
def hourly_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path / 'datos')
    conn = db.connect(tmp_path / 'plan.db')
    db.initialize(conn)
    day = date(2027, 1, 1)
    project = repo.create(conn, 'projects', dict(
        name='Lotes pequeños', mode='mantenimiento', start_date=day,
        end_date=day, calendar_id=1))
    team = teams.save_team(conn, project.id, dict(
        name='Equipo A', regime='7x7', cycle_start=day,
        members=3, hours_day=8, holidays_work=True))
    repo.create(conn, 'periodic_tasks', dict(
        project_id=project.id, code='LOTE', name='Inspección', frequency='D',
        first_due=day, horizon_end=day, quantity_total=80, batch_size=10,
        unit='tableros', productivity_hour_team=10, team_ids=[team['id']]))
    yield conn, project, team
    conn.close()


@pytest.mark.parametrize('representation', ['dict', 'str'])
def test_apply_run_accepts_options_dict_or_json(hourly_plan, monkeypatch, representation):
    conn, project, team = hourly_plan
    original_get = repo.get
    seen = []

    def get_with_options(conn, table, entity_id):
        row = original_get(conn, table, entity_id)
        if table == 'schedule_runs':
            assert isinstance(row['options'], dict)
            row = dict(row)
            if representation == 'str':
                row['options'] = json.dumps(row['options'])
            seen.append(type(row['options']))
        return row

    monkeypatch.setattr(repo, 'get', get_with_options)
    result = services.generate_maintenance(conn, project.id, project.start_date, project.end_date)
    assert result['feasible'] and result['applied'], result
    assert seen
    activities = repo.list_entities(conn, 'activities', project.id)
    assert len(activities) == 8
    assert {a.planned_start for a in activities} == {project.start_date}
    assert {a.planned_finish for a in activities} == {project.start_date}
    assert all(a.duration_hours == 1 and a.work_hours == 3 for a in activities)
    assert sum(a.work_hours for a in activities) == 3 * 8
    assert all(a.team_id == team['id'] for a in activities)


def test_small_batches_over_daily_capacity_are_not_applied(hourly_plan):
    conn, project, team = hourly_plan
    task = repo.list_entities(conn, 'periodic_tasks', project.id)[0]
    repo.update(conn, 'periodic_tasks', task.id, {'quantity_total': 90})
    result = services.generate_maintenance(conn, project.id, project.start_date, project.end_date)
    assert not result['feasible'] and not result['applied'], result
    assert not repo.list_entities(conn, 'activities', project.id)


@pytest.mark.parametrize('method', ['solve', 'optimize'])
@pytest.mark.parametrize('allow_extra', [False, True])
def test_team_overtime_requires_explicit_service_option(hourly_plan, method, allow_extra):
    from ganttier.domain import OptimizeOptions
    from ganttier.solve import solve_project

    conn, project, team = hourly_plan
    task = repo.list_entities(conn, 'periodic_tasks', project.id)[0]
    repo.update(conn, 'periodic_tasks', task.id, {'quantity_total': 90})
    repo.update(conn, 'resources', team['id'], {'max_overtime_hours_day': 1})
    if method == 'solve':
        options = {'allow_overtime': True} if allow_extra else None
        result = solve_project(conn, project.id, options=options, apply=True)
    else:
        options = OptimizeOptions(allow_overtime=True) if allow_extra else None
        result = services.run_optimize(conn, project.id, options=options, apply=True)
    assert result['feasible'] is allow_extra, result
    assert result['applied'] is allow_extra, result
    assert len(repo.list_entities(conn, 'activities', project.id)) == (9 if allow_extra else 0)


@pytest.mark.parametrize('endpoint', ['solve', 'optimize'])
@pytest.mark.parametrize('allow_extra', [False, True])
def test_team_overtime_requires_explicit_api_option(hourly_plan, endpoint, allow_extra):
    from fastapi.testclient import TestClient
    from ganttier.main import create_app
    from ganttier.api.dependencies import connection

    conn, project, team = hourly_plan
    task = repo.list_entities(conn, 'periodic_tasks', project.id)[0]
    repo.update(conn, 'periodic_tasks', task.id, {'quantity_total': 90})
    repo.update(conn, 'resources', team['id'], {'max_overtime_hours_day': 1})
    conn.commit()
    filename = conn.execute('PRAGMA database_list').fetchone()[2]

    def isolated_connection():
        child = db.connect(filename)
        try:
            yield child
        finally:
            child.close()

    app = create_app()
    app.dependency_overrides[connection] = isolated_connection
    with TestClient(app) as client:
        body = {'apply': True}
        if allow_extra:
            body['options'] = {'allow_overtime': True}
        response = client.post(f'/api/projects/{project.id}/schedule/{endpoint}', json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['feasible'] is allow_extra, result
    assert result['applied'] is allow_extra, result


def test_route_verifier_accepts_equivalent_month_lists(hourly_plan):
    from dataclasses import asdict
    from ganttier.routes import propose_routes

    conn, project, team = hourly_plan
    task = repo.list_entities(conn, 'periodic_tasks', project.id)[0]
    first = repo.update(conn, 'periodic_tasks', task.id,
                        {'frequency': 'M', 'preferred_months': '1,4'})
    second = asdict(first)
    second.pop('id')
    second.update(code='LOTE-2', preferred_months='4,1')
    repo.create(conn, 'periodic_tasks', second)
    routes = propose_routes(conn, project.id, apply=True)
    assert len(routes) == 1
    result = services.generate_maintenance(conn, project.id, project.start_date, project.end_date)
    assert result['feasible'] and result['applied'], result
    assert len(repo.list_entities(conn, 'activities', project.id)) == 8


@pytest.mark.parametrize('hours', [.15, 27])
def test_round_only_plan_uses_team_pipeline_and_hard_capacity(hourly_plan, hours):
    from ganttier.domain import OptimizeOptions
    from ganttier.routes import propose_routes

    conn, project, team = hourly_plan
    task = repo.list_entities(conn, 'periodic_tasks', project.id)[0]
    repo.update(conn, 'periodic_tasks', task.id, dict(
        quantity_total=None, batch_size=None, productivity_hour_team=None,
        team_ids=[], unit='', work_hours=hours))
    propose_routes(conn, project.id, apply=True, team_ids=[team['id']])
    result = services.run_optimize(conn, project.id,
                                  options=OptimizeOptions(respect_resource_limits=False, allow_overtime=False),
                                  apply=True)
    assert result['feasible'] is (hours <= 24), result
    assert result['applied'] is (hours <= 24), result
    activities = repo.list_entities(conn, 'activities', project.id)
    assert len(activities) == (1 if hours <= 24 else 0)
    if activities:
        assert activities[0].batch_index is None
        assert activities[0].checklist_codes == ['LOTE']
        assert activities[0].work_hours == hours


def test_round_distribution_reports_zero_hours_for_team_on_rest(hourly_plan):
    from ganttier.routes import propose_routes

    conn, project, team = hourly_plan
    other = teams.save_team(conn, project.id, dict(
        name='Equipo B', regime='7x7', cycle_start=project.start_date+timedelta(days=7),
        members=3, hours_day=8, holidays_work=True))
    task = repo.list_entities(conn, 'periodic_tasks', project.id)[0]
    repo.update(conn, 'periodic_tasks', task.id, dict(
        quantity_total=None, batch_size=None, productivity_hour_team=None,
        team_ids=[], unit='', work_hours=.15))
    propose_routes(conn, project.id, apply=True, team_ids=[team['id'], other['id']])
    result = services.generate_maintenance(conn, project.id, project.start_date, project.end_date)
    assert result['applied'], result
    rows = teams.summary(conn, project.id)['rows']
    active = next(r for r in rows if r['team_id'] == team['id'])
    resting = next(r for r in rows if r['team_id'] == other['id'])
    assert active['available_days'] == 1 and active['available_hours'] == 8
    assert resting['available_days'] == 0 and resting['available_hours'] == 0
    assert resting['required_hours'] == resting['scheduled_hours'] == 0
