"""Aceptación HTTP y diagnóstico directo cuando FastAPI no está instalado.

Ejecutar explícitamente: pytest -m slow -s tests/e2e/test_qa_performance.py
"""
import json
import subprocess
import sys
from time import perf_counter

import pytest

from ganttier import db, domain as d, repo, services


def measured(timings, label, call):
    start = perf_counter()
    try:
        return call()
    finally:
        timings[label] = perf_counter() - start
        print(f'QA 100k {label}: {timings[label]:.3f} s', flush=True)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path/'qa.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path)
    return tmp_path


@pytest.mark.slow
def test_http_100k(storage):
    pytest.importorskip('fastapi')
    pytest.importorskip('httpx')
    from fastapi.testclient import TestClient
    from ganttier.main import create_app
    timings = {}
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        response = measured(timings,'demo',lambda:c.post('/api/demo/mantenimiento?n=100000'))
        assert response.status_code == 201, response.text[:1000]
        url = f'/api/projects/{response.json()["id"]}'
        response = measured(timings,'columnar',lambda:c.get(url+'/activities/columnar'))
        assert response.status_code == 200 and len(response.json()['ids']) == 100000
        response = measured(timings,'optimize',lambda:c.post(url+'/schedule/optimize',json={'options':{'method':'heuristic','time_limit_s':30}}))
        assert response.status_code == 200, response.text[:1000]
        assert response.json()['status'] != 'infactible'
        assert c.post(url+'/baselines',json={'name':'QA100k'}).status_code == 201
        for operation in ('scurve','performance'):
            response = measured(timings,operation,lambda:c.get(url+'/control/'+operation))
            assert response.status_code == 200, response.text[:1000]
            if operation == 'scurve': assert response.json()['planned_cum'][-1] == pytest.approx(100)
        response = measured(timings,'export',lambda:c.get(url+'/export.xlsx'))
        assert response.status_code == 200 and response.content.startswith(b'PK')
    (storage/'http-times.json').write_text(json.dumps(timings,indent=2),encoding='utf-8')
    limits = dict(demo=20,columnar=3,optimize=60,scurve=5,performance=5,export=60)
    assert not {k:round(v,3) for k,v in timings.items() if v >= limits[k]}


@pytest.mark.slow
def test_services_100k_diagnostic(storage):
    """No sustituye la aceptación HTTP: incluye SQL/motores, excluye transporte."""
    timings = {}
    conn = db.connect()
    try:
        db.initialize(conn)
        p = measured(timings,'demo',lambda:services.demo_maintenance(100000,conn))
        col = measured(timings,'columnar',lambda:repo.activities_columnar(conn,p.id))
        assert len(col['ids']) == 100000
        result = measured(timings,'optimize',lambda:services.run_optimize(conn,p.id,
            options=d.OptimizeOptions(method='heuristic',time_limit_s=30)))
        (storage/'optimization-summary.json').write_text(json.dumps(
            {key:result[key] for key in ('status','elapsed_s','warnings')},indent=2),encoding='utf-8')
        services.create_baseline(conn,p.id)
        for operation in ('scurve','performance'):
            measured(timings,operation,lambda:services.control_result(conn,p.id,operation))
        def export():
            # Aísla el exportador para que un incumplimiento no bloquee pytest.
            code = '''import faulthandler,sys
from ganttier import db,repo,services
faulthandler.dump_traceback_later(55)
c=db.connect(sys.argv[1])
services.excel_functions()[3](repo.export_bundle(c,int(sys.argv[2])),sys.argv[3])
c.close()
'''
            try:
                subprocess.run([sys.executable,'-c',code,str(storage/'qa.db'),str(p.id),str(storage/'export.xlsx')],
                    timeout=65,check=True,capture_output=True)
            except subprocess.TimeoutExpired as exc:
                (storage/'export-timeout.txt').write_bytes(exc.stderr or b'')
                pytest.fail('Exportación excedió 65 s; traza en export-timeout.txt.')
        measured(timings,'export',export)
        limits = dict(demo=20,columnar=3,optimize=60,scurve=5,performance=5,export=60)
        assert result['status'] != 'infactible', result['warnings']
        assert not {k:round(v,3) for k,v in timings.items() if v >= limits[k]}
    finally:
        conn.close()
        (storage/'service-times.json').write_text(json.dumps(timings,indent=2),encoding='utf-8')
