import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')
pytest.importorskip('multipart')
from fastapi.testclient import TestClient
from ganttier import services
from ganttier.api.solve import router as solve_router
from ganttier.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path / 'api.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path)
    app = create_app()
    # E1 cannot edit api/__init__.py. Exercise the real router by registering
    # it here until the integrator adds the documented production registration.
    if not any(getattr(route, 'path', '') == '/api/projects/{pid}/schedule/solve' for route in app.routes):
        app.include_router(solve_router, prefix='/api')
    with TestClient(app) as value:
        yield value


def post(client, path, body):
    response = client.post(path, json=body)
    assert response.status_code in {200, 201}, response.text
    return response.json()


@pytest.mark.parametrize('mode', ['proyecto', 'mantenimiento'])
@pytest.mark.parametrize('apply', [True, False])
def test_task_list_to_schedule_http(client, mode, apply):
    cal = post(client, '/api/calendars', {'name': 'Pruebas sin feriados', 'holidays_country': None})
    p = post(client, '/api/projects', dict(name='Lista API', mode=mode, start_date='2026-01-05', data_date='2026-01-05', calendar_id=cal['id'], **({'end_date': '2026-03-31'} if mode == 'mantenimiento' else {})))
    base = f"/api/projects/{p['id']}"
    post(client, '/api/resources', dict(project_id=p['id'], name='Ana', skills=['electrico'], calendar_id=cal['id']))
    if mode == 'proyecto':
        acts = post(client, base + '/activities', [dict(code=f'A{i}', name=f'A{i}', duration_days=2, work_hours=16, required_skill='electrico') for i in range(3)])
        assert all(a['planned_start'] is None for a in acts)
        post(client, base + '/relations', {'pred_id': acts[0]['id'], 'succ_id': acts[1]['id']})
    else:
        post(client, base + '/periodic-tasks', dict(code='M', name='Mantención', frequency='M', first_due='2026-01-05', work_hours=8, required_skill='electrico'))
        assert client.get(base + '/activities').json() == []
    result = post(client, base + '/schedule/solve', {'apply': apply, 'options': {'method': 'heuristic', 'time_limit_s': 1}})
    assert result['feasible'] and result['applied'] == apply and not result['violations'], result
    assert result['metrics']['after']['finish']
    acts = client.get(base + '/activities').json()
    if mode == 'mantenimiento' and not apply:
        # D2: simular no deja ocurrencias ni corridas en la base.
        assert result['run_id'] is None and result['simulated'] and acts == []
    else:
        assert result['run_id']
        assert len(acts) == 3 and all(bool(a['planned_start']) == apply for a in acts)


def test_invalid_network_is_422(client):
    p = post(client, '/api/projects', dict(name='Ciclo', start_date='2026-01-05'))
    base = f"/api/projects/{p['id']}"
    acts = post(client, base + '/activities', [dict(code=x, name=x) for x in ['A', 'B']])
    post(client, base + '/relations', dict(pred_id=acts[0]['id'], succ_id=acts[1]['id']))
    # La API rechaza el ciclo ya al crear la relación (nunca llega al solver).
    response = client.post(base + '/relations', json=dict(pred_id=acts[1]['id'], succ_id=acts[0]['id']))
    assert response.status_code == 422 and 'ciclo' in response.json()['detail'].lower()
    # El solver con la red válida sí entrega un programa factible.
    response = client.post(base + '/schedule/solve', json={})
    assert response.status_code == 200 and response.json()['feasible'] is True


def test_intensity_precheck_explains_impossible_task(client):
    """Una tarea de 48 HH en 2 días con una habilidad de 16 HH/día es imposible en cualquier orden."""
    p = post(client, '/api/projects', dict(name='Intensidad', start_date='2026-01-05'))
    base = f"/api/projects/{p['id']}"
    post(client, '/api/resources', dict(name='Cuadrilla', kind='cuadrilla', skills=['prot'], capacity_hours_day=8,
                                        max_units=2, project_id=p['id']))
    post(client, base + '/activities', [dict(code='OK', name='ok', duration_days=3, work_hours=24, required_skill='prot'),
                                        dict(code='X', name='x', duration_days=2, work_hours=48, required_skill='prot')])
    body = client.post(base + '/schedule/solve', json={}).json()
    assert body['feasible'] is False and body['method_used'] == 'precheck'
    assert [v['type'] for v in body['violations']] == ['intensity']
    detail = body['violations'][0]['detail']
    assert detail.startswith('X:') and '24' in detail and '16' in detail and '3 días' in detail
