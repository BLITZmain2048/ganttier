"""Exploración del contrato HTTP con motores reales y almacenamiento aislado."""
from pathlib import Path
import sqlite3

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')
pytest.importorskip('python_multipart')
from fastapi.testclient import TestClient
from ganttier import services
from ganttier.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path / 'qa.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def ok(response, status=200):
    assert response.status_code == status, response.text[:2000]
    return response.json()


def bad(response):
    assert 400 <= response.status_code < 500, response.text[:2000]
    detail = response.json()['detail']
    assert isinstance(detail, str) and detail.strip()
    assert detail not in ('Not Found', 'Method Not Allowed', 'Internal Server Error')


def project(client, mode='proyecto'):
    return ok(client.post('/api/projects', json=dict(name='QA', mode=mode,
        start_date='2026-01-05', end_date='2026-03-31', calendar_id=5)), 201)['id']


def activity(client, pid, code='A', **fields):
    return ok(client.post(f'/api/projects/{pid}/activities', json=dict(code=code,
        name=code, duration_days=2, work_hours=16, cost_budget=100, **fields)), 201)


@pytest.mark.parametrize('path,payload', [
    ('projects', {'name':'X','start_date':'2026-02-30'}),
    ('projects', {'name':' '}), ('projects', {'name':'X','mode':'otro'}),
    ('projects', {'name':'X','calendar_id':999999}),
    ('calendars', {'name':'X','workweek':'11112'}),
    ('calendars', {'name':'X','shift_pattern':'0x0','shift_anchor':'2026-01-01'}),
    ('calendars', {'name':'X','exceptions':[{'date':'2026-02-30','working':True}]}),
    ('resources', {'name':'X','capacity_hours_day':-1}),
    ('resources', {'name':'X','project_id':999999}),
])
def test_invalid_global_resources(client, path, payload):
    bad(client.post('/api/'+path, json=payload))


@pytest.mark.parametrize('fields', [
    {'duration_days':-1}, {'macro_month':'2026-2'}, {'macro_month':'2026-13'},
    {'macro_month':'0000-01'}, {'planned_start':'2026-02-30'},
    {'percent_complete':101}, {'work_hours':-1}, {'calendar_id':999999},
    {'window_start':'2026-02-01','window_end':'2026-01-01'},
])
def test_invalid_activity_create_update_batch(client, fields):
    pid = project(client)
    a = activity(client, pid)
    bad(client.post(f'/api/projects/{pid}/activities', json=dict(code='B', name='B', **fields)))
    bad(client.put(f'/api/activities/{a["id"]}', json=fields))
    bad(client.patch(f'/api/projects/{pid}/activities', json=[{'id':a['id'],'name':'CHANGED'}, {'id':a['id'],**fields}]))
    assert ok(client.get(f'/api/activities/{a["id"]}'))['name'] == 'A'


def test_crud_scoping_duplicates_cycles_and_atomicity(client):
    p, other = project(client), project(client)
    a, b = activity(client, p), activity(client, p, 'B')
    foreign = activity(client, other)
    base = f'/api/projects/{p}'
    bad(client.post(base+'/activities', json=[{'code':'NEW','name':'N'}, {'code':'A','name':'duplicate'}]))
    assert len(ok(client.get(base+'/activities'))) == 2
    w = ok(client.post(base+'/wbs', json={'code':'W','name':'W'}),201)
    w2 = ok(client.post(base+'/wbs', json={'code':'W2','name':'W2','parent_id':w['id']}),201)
    bad(client.put(f'/api/wbs/{w["id"]}',json={'parent_id':w2['id']}))
    bad(client.post(base+'/wbs',json={'code':'W','name':'duplicate'}))
    bad(client.put(f'/api/activities/{foreign["id"]}',json={'wbs_id':w['id']}))
    rel = ok(client.post(base+'/relations',json={'pred_id':a['id'],'succ_id':b['id']}),201)
    for pred,succ in ((a,a),(b,a),(a,foreign)):
        bad(client.post(base+'/relations',json={'pred_id':pred['id'],'succ_id':succ['id']}))
    r = ok(client.post('/api/resources',json={'name':'R','project_id':p}),201)
    rf = ok(client.post('/api/resources',json={'name':'F','project_id':other}),201)
    bad(client.post(base+'/assignments',json={'activity_id':a['id'],'resource_id':rf['id']}))
    ass = ok(client.post(base+'/assignments',json={'activity_id':a['id'],'resource_id':r['id']}),201)
    bad(client.put(f'/api/assignments/{ass["id"]}',json={'units':-1}))
    bad(client.put(f'/api/assignments/{ass["id"]}',json={'activity_id':foreign['id']}))
    ok(client.put(f'/api/assignments/{ass["id"]}',json={'units':2}))
    task = ok(client.post(base+'/periodic-tasks',json={'code':'T','name':'T'}),201)
    bad(client.post(base+'/periodic-tasks',json={'code':'T','name':'duplicate'}))
    for fields in ({'interval':0},{'duration_days':-1},{'resource_id':rf['id']},{'preferred_months':'0,13'}):
        bad(client.put(f'/api/periodic-tasks/{task["id"]}',json=fields))
    ok(client.put(f'/api/periodic-tasks/{task["id"]}',json={'name':'updated'}))
    ok(client.put(f'/api/wbs/{w["id"]}',json={'name':'updated'}))
    ok(client.put(f'/api/resources/{r["id"]}',json={'name':'updated'}))
    for path,entity in [('assignments',ass),('relations',rel),('periodic-tasks',task),('wbs',w2),('wbs',w),('resources',r),('activities',a),('activities',b)]:
        assert client.delete(f'/api/{path}/{entity["id"]}').status_code == 204
        bad(client.delete(f'/api/{path}/{entity["id"]}'))
    ok(client.put(base,json={'name':'edited'}))
    dup = ok(client.post(base+'/duplicate'),201)
    assert dup['id'] != p
    assert client.delete(base).status_code == 204
    bad(client.get(base))


def test_calendar_crud_and_boundaries(client):
    cal = ok(client.post('/api/calendars',json={'name':'QA','holidays_country':None}),201)
    url = f'/api/calendars/{cal["id"]}'
    assert ok(client.get(url))['name'] == 'QA'
    bad(client.post('/api/calendars',json={'name':'QA'}))
    bad(client.put(url,json={'workweek':'bad'}))
    ok(client.put(url,json={'workweek':'1111111','exceptions':[{'date':'2026-01-06','working':False}]}))
    days = ok(client.get(url+'/days',params={'start':'2026-01-05','end':'2026-01-06'}))
    assert [day['working'] for day in days] == [True,False]
    bad(client.get(url+'/days',params={'start':'2026-02-01','end':'2026-01-01'}))
    assert client.delete(url).status_code == 204


def test_put_uses_coerced_values(client):
    pid = project(client)
    a = activity(client,pid)
    updated = ok(client.put(f'/api/activities/{a["id"]}',json={'duration_days':'3','calendar_id':'5'}))
    assert updated['duration_days'] == 3 and updated['calendar_id'] == 5


def controlled(client):
    pid = project(client)
    a = activity(client,pid)
    r = ok(client.post('/api/resources',json={'name':'Ana','project_id':pid}),201)
    ok(client.post(f'/api/projects/{pid}/assignments',json={'activity_id':a['id'],'resource_id':r['id'],'work_hours':16}),201)
    ok(client.post(f'/api/projects/{pid}/schedule/cpm'))
    baseline = ok(client.post(f'/api/projects/{pid}/baselines',json={'name':'LB'}),201)
    ok(client.put(f'/api/projects/{pid}/data-date',json={'data_date':'2026-01-06'}))
    return pid,a,r,baseline


def test_control_flow_and_invalid_progress(client):
    pid,a,r,baseline = controlled(client)
    url = f'/api/projects/{pid}'
    for fields in ({'percent_complete':101},
        {'percent_complete':50,'actual_start':'2026-01-07'},
        {'percent_complete':50,'actual_start':'2026-01-05','actual_finish':'2026-01-07'}):
        bad(client.post(url+'/progress',json={'activity_id':a['id'],'date':'2026-01-06',**fields}))
    assert ok(client.get(url+'/progress')) == []
    progress = ok(client.post(url+'/progress',json={'activity_id':a['id'],'date':'2026-01-06',
        'percent_complete':100,'actual_start':'2026-01-05','actual_finish':'2026-01-06',
        'actual_hours':18,'actual_cost':120,'resource_id':r['id']}),201)
    ok(client.post(url+'/schedule/cpm'))
    for basis in ('hours','cost','count'):
        for period in ('day','week','month'):
            sc = ok(client.get(url+'/control/scurve',params={'basis':basis,'period':period}))
            assert sc['planned_cum'][-1] == pytest.approx(100)
        ev = ok(client.get(url+'/control/evm',params={'basis':basis}))
        assert 0 <= ev['EV'] <= ev['BAC']
        assert ev['percent_complete'] == pytest.approx(100)
    perf = ok(client.get(url+'/control/performance',params={'start':'2026-01-01','end':'2026-01-31'}))
    assert perf[0]['completed_activities'] == 1
    for group in ('resource','skill'):
        for period in ('day','week','month'):
            assert ok(client.get(url+'/load',params={'group':group,'period':period}))['labels']
    ok(client.post(f'/api/baselines/{baseline["id"]}/activate'))
    assert len(ok(client.get(url+'/baselines'))) == 1
    assert client.delete(f'/api/progress/{progress["id"]}').status_code == 204
    assert ok(client.get(f'/api/activities/{a["id"]}'))['percent_complete'] == 0
    assert client.delete(f'/api/baselines/{baseline["id"]}').status_code == 204
    assert ok(client.get(url))['active_baseline_id'] is None


def test_maintenance_generation_runs_and_old_run(client):
    pid = project(client,'mantenimiento'); url = f'/api/projects/{pid}'
    resource = ok(client.post('/api/resources',json={'project_id':pid,'name':'Técnico','calendar_id':5}),201)
    ok(client.post(url+'/periodic-tasks',json={'code':'T','name':'T','first_due':'2026-01-05','resource_id':resource['id']}),201)
    body = {'horizon_start':'2026-01-01','horizon_end':'2026-03-31'}
    for replace in (True,False,True,False):
        ok(client.post(url+'/maintenance/generate',json={**body,'replace':replace}))
        assert len(ok(client.get(url+'/activities'))) == 3
    run = ok(client.post(url+'/schedule/optimize',json={'options':{'method':'heuristic'}}))
    ok(client.post(url+f'/schedule/runs/{run["run_id"]}/apply'))
    assert ok(client.get(url+'/schedule/runs'))[0]['applied']
    a = ok(client.get(url+'/activities'))[0]
    ok(client.post(url+'/progress',json={'activity_id':a['id'],'date':'2026-01-06','percent_complete':50,'actual_start':'2026-01-05'}),201)
    for replace in (True,False):
        ok(client.post(url+'/maintenance/generate',json={**body,'replace':replace}))
        assert ok(client.get(f'/api/activities/{a["id"]}'))['percent_complete'] == 50
    bad(client.post(url+f'/schedule/runs/{run["run_id"]}/apply'))
    pending = ok(client.post(url+'/schedule/optimize',json={'options':{'method':'heuristic'}}))
    assert str(a['id']) not in pending['starts']
    assert '1 actividades fijas por avance/bloqueo o no periódicas no se movieron' in pending['warnings']
    other = project(client)
    bad(client.post(f'/api/projects/{other}/schedule/runs/{run["run_id"]}/apply'))


@pytest.mark.parametrize('period',['weekly','monthly','annual'])
@pytest.mark.parametrize('fmt',['xlsx','pdf','docx','html'])
def test_reports_all_formats_periods(client,period,fmt):
    if fmt == 'pdf': pytest.importorskip('reportlab')
    if fmt == 'docx': pytest.importorskip('docx')
    pid,*_ = controlled(client)
    files = ok(client.post(f'/api/projects/{pid}/reports',json={'period_type':period,'period_start':'2026-01-06','formats':[fmt]}))
    assert len(files) == 1 and files[0]['format'] == fmt
    response = client.get(files[0]['url'])
    assert response.status_code == 200 and len(response.content) > 500
    if fmt in ('xlsx','docx'): assert response.content.startswith(b'PK')
    if fmt == 'pdf': assert response.content.startswith(b'%PDF')


@pytest.mark.parametrize('name',['..%5Cqa.db','..%2Fqa.db','%2E%2E%2Fqa.db','C:%5Cqa.db','missing.pdf','qa.db'])
def test_download_traversal(client,name):
    bad(client.get('/api/reports/files/'+name))


@pytest.mark.parametrize('kind',['proyecto','mantenimiento'])
def test_templates_new_project_dry_run_merge_replace(client,kind,tmp_path):
    content = client.get('/api/templates/'+kind)
    assert content.status_code == 200 and content.content.startswith(b'PK')
    files = {'file':('template.xlsx',content.content,'application/octet-stream')}
    p = ok(client.post('/api/import/new-project',files=files),201)
    assert p['mode'] == kind
    url = f'/api/projects/{p["id"]}'
    extra = activity(client,p['id'],'EXTRA')
    with sqlite3.connect(tmp_path/'qa.db') as conn:
        before = list(conn.iterdump())
    for mode in ('merge','replace'):
        ok(client.post(url+'/import',params={'dry_run':True,'mode':mode},files=files))
        with sqlite3.connect(tmp_path/'qa.db') as conn:
            assert list(conn.iterdump()) == before
    ok(client.post(url+'/import',params={'mode':'merge'},files=files))
    assert ok(client.get(f'/api/activities/{extra["id"]}'))['code'] == 'EXTRA'
    count = len(ok(client.get(url+'/activities')))
    ok(client.post(url+'/import',params={'mode':'merge'},files=files))
    assert len(ok(client.get(url+'/activities'))) == count
    ok(client.post(url+'/import',params={'mode':'replace'},files=files))
    assert 'EXTRA' not in ok(client.get(url+'/activities/columnar'))['codes']
    exported = client.get(url+'/export.xlsx')
    assert exported.status_code == 200 and exported.content.startswith(b'PK')
    restored = ok(client.post('/api/import/new-project',files={'file':('roundtrip.xlsx',exported.content)}),201)
    assert ok(client.get(f'/api/projects/{restored["id"]}/activities/columnar'))['codes'] == ok(client.get(url+'/activities/columnar'))['codes']
    assert not list((tmp_path/'tmp').glob('*'))


@pytest.mark.parametrize('path',sorted((Path(__file__).resolve().parents[3]/'ejemplos').glob('*.xlsx')),ids=lambda p:p.name)
def test_example_workbooks(client,path):
    p = ok(client.post('/api/import/new-project',files={'file':(path.name,path.read_bytes())}),201)
    assert ok(client.get(f'/api/projects/{p["id"]}/activities/columnar'))['ids']


@pytest.mark.parametrize('path,payload',[
    ('schedule/optimize',{'options':{'method':'invalid'}}),
    ('data-date',{'data_date':'2026-02-30'}), ('baselines',{'name':' '}),
    ('reports',{'period_type':'bad','period_start':'2026-01-01','formats':['html']}),
    ('reports',{'period_type':'monthly','period_start':'2026-01-01','formats':['exe']}),
    ('maintenance/generate',{'horizon_start':'2026-03-01','horizon_end':'2026-01-01'}),
])
def test_invalid_operations(client,path,payload):
    pid = project(client)
    method = client.put if path == 'data-date' else client.post
    bad(method(f'/api/projects/{pid}/'+path,json=payload))


@pytest.mark.parametrize('path', ['activities?offset=-1','activities?limit=100001',
    'control/scurve?period=bad','control/evm?basis=bad','control/performance?start=2026-02-01&end=2026-01-01',
    'load?group=bad','load?from=2026-02-01&to=2026-01-01'])
def test_invalid_queries(client,path):
    bad(client.get(f'/api/projects/{project(client)}/'+path))


def test_invalid_excel_is_4xx(client):
    pid = project(client)
    for path in ('/api/import/new-project',f'/api/projects/{pid}/import'):
        for name,content in [('x.txt',b'bad'),('x.xlsx',b'not a zip')]:
            bad(client.post(path,files={'file':(name,content)}))


@pytest.mark.parametrize('suffix',['','/activities','/activities/columnar','/wbs','/relations','/assignments',
    '/periodic-tasks','/baselines','/progress','/schedule/runs','/control/scurve','/control/evm',
    '/control/performance','/load','/export.xlsx'])
def test_missing_project(client,suffix):
    bad(client.get('/api/projects/999999'+suffix))


def test_empty_project_and_health(client):
    assert ok(client.get('/api/health'))['status'] == 'ok'
    pid = project(client)
    assert len(ok(client.get('/api/projects'))) == 1
    assert ok(client.get(f'/api/projects/{pid}/activities/columnar'))['ids'] == []
    for endpoint in ('scurve','evm','performance'):
        ok(client.get(f'/api/projects/{pid}/control/{endpoint}'))


def test_valid_project_run_can_be_reapplied(client):
    pid = project(client)
    a = activity(client,pid)
    url = f'/api/projects/{pid}'
    first = ok(client.post(url+'/schedule/optimize',json={'options':{'method':'heuristic','time_limit_s':1}}))
    second = ok(client.post(url+'/schedule/optimize',json={'options':{'method':'heuristic','time_limit_s':1}}))
    assert first['run_id'] != second['run_id']
    ok(client.post(url+f'/schedule/runs/{first["run_id"]}/apply'))
    ok(client.put(f'/api/activities/{a["id"]}',json={'locked':True,'planned_start':'2026-02-02','planned_finish':'2026-02-03'}))
    bad(client.post(url+f'/schedule/runs/{first["run_id"]}/apply'))
    assert client.delete(f'/api/activities/{a["id"]}').status_code == 204
    bad(client.post(url+f'/schedule/runs/{first["run_id"]}/apply'))


@pytest.mark.parametrize('identifier',[[],{},None,2**100])
def test_malformed_patch_identifier(client,identifier):
    pid = project(client)
    bad(client.patch(f'/api/projects/{pid}/activities',json=[{'id':identifier,'name':'X'}]))
