from copy import deepcopy
from datetime import date
import json

import pytest
from fastapi.testclient import TestClient
from ganttier import db, domain as d, repo, services
from ganttier.main import create_app
from ganttier.engine.verify import verify_role_schedule
from role_case import import_case, request_body, audit_original, ok
from test_role_blocks import problem, solve


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path/'api.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path)
    with TestClient(create_app()) as client:
        yield client


def test_unsolved_case_api_optimal_apply_persist_export(client, tmp_path):
    pid, data = import_case(client)
    path = f'/api/projects/{pid}'
    demands = ok(client.get(path+'/assignments'))
    assert any(row['resource_id'] is None for row in demands)
    result = ok(client.post(path+'/schedule/solve', json=request_body(apply=False)))
    assert result['feasible'] and not result['applied']
    assert result['stage1'] == result['stage2'] == 'OPTIMAL'
    assert result['primary_finish_block'] == result['primary_bound'] == 36
    assert result['personnel_cost'] == result['personnel_cost_bound'] == 10420
    assert not result['violations'] and not result['verification']['violations']
    assert not ok(client.get(path+'/schedule/role-assignments'))['applied']
    assert all(a['planned_start'] is None for a in ok(client.get(path+'/activities', params={'limit': 1000})))
    audit = audit_original(client, pid, data, result)
    assert audit['errors'] == [] and audit['A27'] == 36 and audit['personnel_cost'] == 10420
    applied = ok(client.post(path+f"/schedule/runs/{result['run_id']}/apply"))
    assert applied['applied']
    assert ok(client.post(path+f"/schedule/runs/{result['run_id']}/apply")) == applied
    nominal = ok(client.get(path+'/schedule/role-assignments'))
    assert nominal['valid'] and nominal['role_assignment']
    assert ok(client.get(path+'/assignments')) == demands
    with db.connect() as conn:
        assert repo.get(conn, 'activities', next(a.id for a in repo.list_entities(conn, 'activities', pid) if a.code == 'A27')).planned_finish == date(2027, 2, 6)
        assert conn.execute('SELECT COUNT(*) FROM role_allocations').fetchone()[0] == len(nominal['role_assignment'])
    response = client.get(path+'/export.xlsx', params={'nominal': 'true'})
    assert response.status_code == 200
    exported = tmp_path/'nominal.xlsx'
    exported.write_bytes(response.content)
    from ganttier.excel_io import parse_workbook
    bundle = parse_workbook(exported, 'proyecto')
    assert not bundle.has_errors
    assert all(a.get('resource_name') for a in bundle.assignments)
    imported = ok(client.post('/api/import/new-project', params={'mode': 'proyecto'}, files={
        'file': ('nominal.xlsx', response.content, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}))
    imported_pid = imported.get('id') or imported['project']['id']
    again = ok(client.post(f'/api/projects/{imported_pid}/schedule/solve', json=request_body(apply=False)))
    assert again['feasible'] and again['primary_finish_block'] == 36 and again['personnel_cost'] == 10420
    # Editing a source demand invalidates the nominal export rather than silently using stale staff.
    row = demands[0]
    ok(client.put(f"/api/assignments/{row['id']}", json={**row, 'units': row['units']+1}))
    assert not ok(client.get(path+'/schedule/role-assignments'))['valid']
    assert client.get(path+'/export.xlsx', params={'nominal': 'true'}).status_code == 422


@pytest.mark.parametrize('defect', ['duplicate', 'missing', 'skill', 'source', 'dates'])
def test_independent_verifier_rejects_corrupted_staff(defect):
    p = problem()
    r = solve(p)
    allocation = deepcopy(r['role_assignment'])
    starts = {k: date.fromisoformat(v) for k, v in r['starts'].items()}
    finishes = {k: date.fromisoformat(v) for k, v in r['finishes'].items()}
    if defect == 'duplicate': allocation[1]['resource_id'] = allocation[0]['resource_id']
    if defect == 'missing': allocation.pop()
    if defect == 'skill': allocation[0]['skill'] = 'unknown'
    if defect == 'source': allocation[0]['source_assignment_id'] = 999
    if defect == 'dates': finishes[1] = date(2027, 1, 5)
    report = verify_role_schedule(p, starts, finishes, allocation, d.OptimizeOptions(allow_overtime=False), block_hours=4)
    assert not report.feasible and report.violations


def test_default_contract_is_fractional():
    from ganttier.api.solve import SolveRequest
    assert SolveRequest().options.role_mode == d.OptimizeOptions().role_mode == 'fractional'


def test_default_and_explicit_fractional_produce_same_schedule(client):
    path, _ = mini_case(client)
    options = dict(method='heuristic', allow_overtime=False, time_limit_s=2, seed=0)
    default = ok(client.post(path+'/schedule/solve', json=dict(apply=False, options=options)))
    explicit = ok(client.post(path+'/schedule/solve', json=dict(
        apply=False, options=dict(options, role_mode='fractional'))))
    assert default['feasible'] and explicit['feasible']
    for field in ('feasible', 'status', 'method_used', 'violations'):
        assert default[field] == explicit[field]
    payloads = [json.loads((services.DATA_DIR/'runs'/f"{r['run_id']}.json").read_text(encoding='utf-8'))
                for r in (default, explicit)]
    for field in ('starts', 'finishes'):
        assert payloads[0][field] == payloads[1][field]
    assert not ok(client.get(path+'/schedule/role-assignments'))['applied']


def mini_case(client):
    cal = ok(client.post('/api/calendars', json=dict(name='Bloques', workweek='1111111', hours_per_day=4, holidays_country=None)))
    p = ok(client.post('/api/projects', json=dict(name='Mini', start_date='2027-01-01', end_date='2027-01-04', calendar_id=cal['id'])))
    path = f"/api/projects/{p['id']}"
    ok(client.post('/api/resources', json=dict(project_id=p['id'], name='Ana', skills=['C'], calendar_id=cal['id'], capacity_hours_day=4)))
    ok(client.post(path+'/activities', json=[dict(code='A', name='A', duration_days=1, work_hours=4, required_skill='C')]))
    body = dict(apply=False, options=dict(role_mode='indivisible', allow_overtime=False, target_code='A', horizon_blocks=4))
    return path, body


def test_tampered_run_is_audited_and_rolled_back(client):
    path, body = mini_case(client)
    result = ok(client.post(path+'/schedule/solve', json=body))
    run_path = services.DATA_DIR/'runs'/f"{result['run_id']}.json"
    payload = json.loads(run_path.read_text(encoding='utf-8'))
    payload['role_assignment'] = []
    run_path.write_text(json.dumps(payload), encoding='utf-8')
    assert client.post(path+f"/schedule/runs/{result['run_id']}/apply").status_code == 422
    assert not ok(client.get(path+'/schedule/role-assignments'))['applied']
    assert all(a['planned_start'] is None for a in ok(client.get(path+'/activities')))


def test_apply_true_and_infeasible_do_not_overwrite_valid_schedule(client):
    path, body = mini_case(client)
    body['apply'] = True
    result = ok(client.post(path+'/schedule/solve', json=body))
    assert result['feasible'] and result['applied']
    dates = ok(client.get(path+'/activities'))
    roles = ok(client.get(path+'/schedule/role-assignments'))
    # Force an impossible window without altering the persisted project.
    body['horizon_start'] = '2026-12-01'
    body['options']['horizon_blocks'] = 1
    result = ok(client.post(path+'/schedule/solve', json=body))
    assert not result['feasible'] and not result['applied']
    assert ok(client.get(path+'/activities')) == dates
    assert ok(client.get(path+'/schedule/role-assignments')) == roles


@pytest.mark.parametrize('change', [dict(allow_overtime=True), dict(respect_resource_limits=False), dict(horizon_blocks=0), dict(block_hours=0), dict(target_code='missing')])
def test_invalid_options_rejected(client, change):
    path, body = mini_case(client)
    body['options'].update(change)
    assert client.post(path+'/schedule/solve', json=body).status_code == 422
    assert all(a['planned_start'] is None for a in ok(client.get(path+'/activities')))
