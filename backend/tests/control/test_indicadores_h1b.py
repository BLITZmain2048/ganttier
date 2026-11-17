"""Regresiones H1b: actualización selectiva exacta y artistas persistentes."""
from datetime import date
import pytest
from ganttier import domain, repo, services
from ganttier.control import cache
from test_avance_h1 import control_db, make_page, painted


def test_guardado_reutiliza_entradas_no_editadas(control_db, monkeypatch):
    conn, client, p, acts = control_db
    cache.clear()
    cache.dashboard(conn, p.id)
    original = cache.control_inputs
    selected = []
    def watched(conn, pid, *args, **kwargs):
        selected.append(kwargs.get('activity_ids'))
        return original(conn, pid, *args, **kwargs)
    monkeypatch.setattr(cache, 'control_inputs', watched)
    response = client.post(f'/api/projects/{p.id}/progress', json=dict(activity_id=acts[0].id,
        date='2026-01-09', percent_complete=33.3, actual_hours=.1, actual_cost=.3))
    assert response.status_code == 201, response.text
    result = cache.dashboard(conn, p.id)
    for kind in ('evm', 'scurve'):
        assert domain.to_dict(result[kind]) == domain.to_dict(services.control_result(conn,p.id,kind))
    assert selected and all(ids is not None and set(ids)=={acts[0].id} for ids in selected)
    cache.clear()


def test_lienzo_conserva_ejes_y_series(control_db, tmp_path, qtbot, monkeypatch):
    conn, client, p, acts = control_db
    page, errors = make_page(client,p,tmp_path,qtbot,monkeypatch)
    page.refresh(); painted(qtbot,page)
    axes=page.chart.ax
    lines=tuple(axes.lines)
    page.scale.setCurrentIndex(1); painted(qtbot,page)
    assert page.chart.ax is axes
    assert tuple(axes.lines) == lines
    page.model.setData(page.model.index(0,6),50)
    page.save_progress(); painted(qtbot,page)
    assert page.chart.ax is axes and tuple(axes.lines)==lines
    assert not errors
    cache.clear()


def test_muestreo_visible_preserva_corte_y_extremos(control_db):
    conn, client, p, acts=control_db
    path=f'/api/projects/{p.id}/control/dashboard'
    full=client.get(path,params=dict(period='day')).json()
    small=client.get(path,params=dict(period='day',max_points=4)).json()
    a,b=full['scurve'],small['scurve']
    assert len(b['labels'])<=4
    assert a['labels'][0]==b['labels'][0] and a['labels'][-1]==b['labels'][-1]
    assert a['data_date'] in b['labels']
    assert small['evm']==full['evm']
    for i,label in enumerate(b['labels']):
        j=a['labels'].index(label)
        for key in ('planned_cum','earned_cum','actual_cum','forecast_cum','planned_abs','earned_abs','actual_abs','forecast_abs'):
            assert b[key][i]==a[key][j]
    cache.clear()


def test_guardado_prepara_historial_solo_de_editadas(control_db, monkeypatch):
    from ganttier.control import _core
    conn, client, p, acts=control_db
    repo.create_many(conn,'progress',[dict(activity_id=a.id,date=date(2026,1,6),percent_complete=20,actual_hours=.1) for a in acts])
    cache.clear(); cache.dashboard(conn,p.id)
    seen=[]
    original=_core._history
    def watched(progress,index,*args,**kwargs):
        seen.append(set(index))
        return original(progress,index,*args,**kwargs)
    monkeypatch.setattr(_core,'_history',watched)
    r=client.post(f'/api/projects/{p.id}/progress',json=dict(activity_id=acts[2].id,date='2026-01-09',percent_complete=51.7,actual_hours=.2))
    assert r.status_code==201,r.text
    actual=cache.dashboard(conn,p.id)
    assert seen and all(ids=={acts[2].id} for ids in seen)
    monkeypatch.setattr(_core,'_history',original)
    for kind in ('evm','scurve'):
        assert domain.to_dict(actual[kind])==domain.to_dict(services.control_result(conn,p.id,kind))
    cache.clear()

@pytest.mark.parametrize('basis', ['hours','cost','count'])
@pytest.mark.parametrize('baseline', ['none','empty','partial','full'])
def test_incremental_exacto_lotes_historia_y_borrado(control_db,basis,baseline):
    conn,client,p,acts=control_db
    # Dos calendarios, un hito, decimales no representables y magnitudes dispares.
    cals=repo.list_entities(conn,'calendars')
    for i,a in enumerate(acts[:6]):
        repo.update(conn,'activities',a.id,dict(work_hours=[.1,1e8,.3,3.7,40,1e-8][i],
            cost_budget=[.3,1e10,.1,17.3,400,1e-9][i],calendar_id=cals[i%2].id))
    repo.update(conn,'activities',acts[4].id,dict(duration_days=0))
    repo.create_many(conn,'progress',[
        dict(activity_id=acts[0].id,date=date(2026,1,6),percent_complete=30,actual_hours=.1),
        dict(activity_id=acts[0].id,date=date(2026,1,5),percent_complete=10,actual_hours=.2),
        dict(activity_id=acts[0].id,date=date(2026,1,6),percent_complete=40,actual_hours=.3),
        dict(activity_id=acts[1].id,date=date(2026,2,1),percent_complete=70,actual_cost=.1),
    ])
    if baseline!='none':
        b=services.create_baseline(conn,p.id,'H1b',True)
        with repo.transaction(conn):
            if baseline=='empty': conn.execute('DELETE FROM baseline_activities WHERE baseline_id=?',(b.id,))
            elif baseline=='partial': conn.execute('DELETE FROM baseline_activities WHERE baseline_id=? AND activity_id>?',(b.id,acts[2].id))
    cache.clear()
    def check():
        for period in ('day','week','month'):
            actual=cache.dashboard(conn,p.id,period,basis)
            for kind in ('evm','scurve'):
                assert domain.to_dict(actual[kind])==domain.to_dict(services.control_result(conn,p.id,kind,period,basis))
    check()
    batches=[
        [dict(activity_id=acts[0].id,date='2026-01-09',percent_complete=51.7,actual_hours=.1),
         dict(activity_id=acts[0].id,date='2026-01-07',percent_complete=42.3,actual_hours=.2),
         dict(activity_id=acts[3].id,date='2026-01-09',percent_complete=100,actual_cost=.3),
         dict(activity_id=acts[4].id,date='2026-01-09',percent_complete=99)],
        [dict(activity_id=acts[1].id,date='2026-02-01',percent_complete=75.3,actual_hours=.7),
         dict(activity_id=acts[0].id,date='2026-01-09',percent_complete=51.8,actual_hours=.3)],
        [dict(activity_id=acts[1].id,date='2026-01-09',percent_complete=100,actual_cost=.7),
         dict(activity_id=acts[4].id,date='2026-01-09',percent_complete=100)],
    ]
    created=[]
    for batch in batches:
        r=client.post(f'/api/projects/{p.id}/progress',json=batch)
        assert r.status_code==201,r.text
        created.extend(row['id'] for row in r.json())
        check()
    for rid in reversed(created):
        assert client.delete(f'/api/progress/{rid}').status_code==204
        check()
    cache.clear()


def test_incremental_dos_guardados_antes_de_consultar_y_rollback(control_db):
    conn,client,p,acts=control_db
    cache.clear(); before=cache.dashboard(conn,p.id)
    for a in acts[:2]:
        assert client.post(f'/api/projects/{p.id}/progress',json=dict(activity_id=a.id,
            date='2026-01-09',percent_complete=33.3,actual_hours=.1)).status_code==201
    actual=cache.dashboard(conn,p.id)
    assert actual['evm'].EV>before['evm'].EV
    for kind in ('evm','scurve'):
        assert domain.to_dict(actual[kind])==domain.to_dict(services.control_result(conn,p.id,kind))
    with pytest.raises(RuntimeError):
        with cache.progress_write(conn,p.id,[acts[0].id]):
            repo.create(conn,'progress',dict(activity_id=acts[0].id,date=date(2026,1,9),percent_complete=66.6))
            raise RuntimeError('rollback deliberado')
    assert cache.dashboard(conn,p.id)==actual
    conn.execute('BEGIN')
    with cache.progress_write(conn,p.id,[acts[0].id]):
        repo.create(conn,'progress',dict(activity_id=acts[0].id,date=date(2026,1,9),percent_complete=66.6))
    assert cache.dashboard(conn,p.id)['evm'].EV>actual['evm'].EV
    conn.rollback()
    assert cache.dashboard(conn,p.id)==actual
    cache.clear()


def test_escritura_externa_entre_guardado_y_lectura(control_db,monkeypatch):
    from ganttier import db
    conn,client,p,acts=control_db
    cache.clear(); cache.dashboard(conn,p.id)
    assert client.post(f'/api/projects/{p.id}/progress',json=dict(activity_id=acts[0].id,
        date='2026-01-09',percent_complete=50)).status_code==201
    other=db.connect()
    repo.update(other,'activities',acts[1].id,dict(work_hours=123.4))
    other.close()
    reads=[]; original=cache.control_inputs
    def watched(*args,**kwargs):
        reads.append(kwargs.get('activity_ids'))
        return original(*args,**kwargs)
    monkeypatch.setattr(cache,'control_inputs',watched)
    actual=cache.dashboard(conn,p.id)
    assert reads==[None]
    assert domain.to_dict(actual['evm'])==domain.to_dict(services.control_result(conn,p.id,'evm'))
    cache.clear()


def test_precalculo_al_abrir_proyecto_con_pagina_oculta(control_db,tmp_path,qtbot,monkeypatch):
    from test_avance_h1 import idle
    conn,client,p,acts=control_db
    page,errors=make_page(client,p,tmp_path,qtbot,monkeypatch)
    page.hide()
    page.state.project=None
    page._pid=None
    page.state.open_project(p.id)
    idle(qtbot)
    assert not page.isVisible() and page.evm and page.evm['BAC']>0
    assert not errors
    cache.clear()


@pytest.mark.slow
def test_indicadores_100k_umbral_real(tmp_path,monkeypatch,qtbot):
    """Incluye construcción, HTTP, cálculo, Matplotlib y pintura visible offscreen."""
    import json
    from pathlib import Path
    from time import perf_counter
    from fastapi.testclient import TestClient
    from ganttier import db
    from ganttier.main import create_app
    from ganttier.gui.widgets import common
    monkeypatch.setenv('GANTTIER_DB',str(tmp_path/'escala.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR',str(tmp_path/'datos'))
    with TestClient(create_app()) as client:
        conn=db.connect()
        p=services.demo_maintenance(100000,conn)
        with repo.transaction(conn):
            conn.execute("UPDATE projects SET data_date='2026-07-01' WHERE id=?",(p.id,))
            conn.execute("""INSERT INTO progress(activity_id,date,percent_complete,actual_hours,actual_cost,actual_start)
                SELECT id,'2026-07-01',CASE WHEN planned_finish<'2026-07-01' THEN 100 ELSE 25 END,
                2,30000,MIN(planned_start,'2026-07-01') FROM activities WHERE project_id=?""",(p.id,))
        services.sync_progress(conn,p.id)
        p=repo.get(conn,'projects',p.id)
        cache.clear()
        start=perf_counter()
        page,errors=make_page(client,p,tmp_path,qtbot,monkeypatch)
        page.refresh(); painted(qtbot,page)
        opening=perf_counter()-start
        assert page.evm and page.scurve and page.chart.isVisible() and not page.chart_loading
        # Valores originales H1, sin tolerancias; se comprueban después de cronometrar.
        reference=Path(__file__).resolve().parents[3]/'trabajos/correcciones_20261012/H1_avance'
        for kind in ('evm','scurve'):
            assert getattr(page,kind)==json.loads((reference/f'antes_{kind}.json').read_text(encoding='utf-8'))
        axes=page.chart.ax; lines=tuple(axes.lines)
        cold=[opening]
        for _ in range(2):
            cache.clear(); start=perf_counter()
            page.refresh(); painted(qtbot,page)
            cold.append(perf_counter()-start)
        saves=[]
        for pct in (50,61.3,100):
            aid=page.model.rows[0]['id']
            assert page.model.setData(page.model.index(0,6),pct)
            start=perf_counter(); page.save_progress(); painted(qtbot,page)
            saves.append(perf_counter()-start)
            assert repo.get(conn,'activities',aid).percent_complete==pct
            assert not page.chart_loading and not page.saving and not page.loading
            assert page.chart.ax is axes and tuple(axes.lines)==lines
            for kind in ('evm','scurve'):
                expected=domain.to_dict(services.control_result(conn,p.id,kind))
                assert getattr(page,kind)==expected
        assert not errors
        evidence=dict(actividades=100000,avances_iniciales=100000,frio=cold,guardar=saves,
                      limite_frio=2.0,limite_guardar=.5,puntos=len(page.scurve['labels']))
        (tmp_path/'aceptacion.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        print(json.dumps(evidence),flush=True)
        page.close(); cache.clear(); conn.close()
        assert all(t<=2.0 for t in cold),evidence
        assert all(t<=.5 for t in saves),evidence


def test_importacion_grafica_fuera_del_hilo_gui(control_db,tmp_path,qtbot,monkeypatch):
    import builtins
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    conn,client,p,acts=control_db
    from ganttier.gui.pages import control
    monkeypatch.setattr(control,'_plot_runtime_ready',False)
    calls=[]; original=builtins.__import__
    def watched(name,*args,**kwargs):
        if name.startswith('matplotlib'):
            calls.append(QThread.currentThread()!=QApplication.instance().thread())
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',watched)
    page,errors=make_page(client,p,tmp_path,qtbot,monkeypatch)
    page.refresh(); painted(qtbot,page)
    assert calls and calls[0]
    assert not errors
    cache.clear()


def test_commit_externo_durante_publicacion(control_db,monkeypatch):
    from contextlib import contextmanager
    from ganttier import db
    conn,client,p,acts=control_db
    cache.clear(); cache.dashboard(conn,p.id)
    original=repo.transaction
    injected=[]
    @contextmanager
    def transaction(connection,**kwargs):
        with original(connection,**kwargs):
            yield
        if kwargs.get('immediate') and not injected:
            injected.append(True)
            other=db.connect()
            other.execute('UPDATE activities SET work_hours=123.4 WHERE id=?',(acts[1].id,))
            other.commit(); other.close()
    monkeypatch.setattr(repo,'transaction',transaction)
    r=client.post(f'/api/projects/{p.id}/progress',json=dict(activity_id=acts[0].id,
        date='2026-01-09',percent_complete=51.3))
    assert r.status_code==201 and injected,r.text
    actual=cache.dashboard(conn,p.id)
    assert domain.to_dict(actual['evm'])==domain.to_dict(services.control_result(conn,p.id,'evm'))
    cache.clear()


def test_precalculo_sin_construir_pagina(control_db,tmp_path,qtbot,monkeypatch):
    from PySide6.QtCore import QSettings
    from ganttier.gui.client import ApiClient
    from ganttier.gui.state import AppState
    from test_avance_h1 import idle
    conn,client,p,acts=control_db
    api=ApiClient('http://testserver'); api._http.close(); api._http=client
    state=AppState(api,QSettings(str(tmp_path/'pre.ini'),QSettings.IniFormat))
    cache.clear(); calls=[]; original=cache._compute
    def watched(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(cache,'_compute',watched)
    state.open_project(p.id); idle(qtbot)
    assert calls==[1]
    cache.dashboard(conn,p.id)
    assert calls==[1]
    cache.clear()


def test_peticion_compartida_e_invalidacion_en_calculo(control_db,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from ganttier import db
    conn,client,p,acts=control_db
    cache.clear(); entered=Event(); release=Event(); calls=[]; original=cache._compute
    def watched(*args):
        calls.append(1)
        value=original(*args)
        if len(calls)==1:
            entered.set()
            assert release.wait(5)
        return value
    monkeypatch.setattr(cache,'_compute',watched)
    def request():
        connection=db.connect()
        try: return cache.dashboard(connection,p.id)
        finally: connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(request)
        assert entered.wait(5)
        second=pool.submit(request)
        repo.update(conn,'activities',acts[0].id,dict(work_hours=99.7))
        release.set()
        old=first.result(5); new=second.result(5)
    current=cache.dashboard(conn,p.id)
    assert current['evm'].BAC!=old['evm'].BAC
    assert current==new
    assert len(calls)==2
    cache.clear()


@pytest.mark.parametrize('seed',range(5))
@pytest.mark.parametrize('basis',['hours','cost','count'])
def test_columnar_snapshots_y_hitos_exactos(control_db,seed,basis):
    import random
    conn,client,p,acts=control_db
    rng=random.Random(seed)
    repo.update(conn,'projects',p.id,dict(data_date=date(2026,2,5)))
    for i,a in enumerate(acts[:12]):
        pct=rng.uniform(0,100)
        values=dict(work_hours=rng.uniform(.001,1e5),cost_budget=rng.uniform(.001,1e8),percent_complete=pct,
            actual_hours=rng.uniform(0,100),actual_cost=rng.uniform(0,1000),duration_days=0 if i%4==0 else 5)
        if i%3==0: values.update(actual_start=date(2026,2,1),actual_finish=date(2026,2,3),percent_complete=100)
        elif i%3==1: values.update(actual_start=date(2026,1,5),actual_finish=date(2026,1,8),percent_complete=100)
        repo.update(conn,'activities',a.id,values)
    repo.update(conn,'projects',p.id,dict(data_date=date(2026,1,9)))
    repo.create_many(conn,'progress',[
        dict(activity_id=acts[7].id,date=date(2026,1,6),percent_complete=100),
        dict(activity_id=acts[8].id,date=date(2026,2,1),percent_complete=25),
        dict(activity_id=acts[9].id,date=date(2026,1,6),percent_complete=99,actual_hours=.1),
        dict(activity_id=acts[9].id,date=date(2026,1,5),percent_complete=1.3,actual_hours=.2),
        dict(activity_id=acts[9].id,date=date(2026,1,6),percent_complete=42.7,actual_hours=.3),
    ])
    cache.clear()
    for period in ('day','week','month'):
        actual=cache.dashboard(conn,p.id,period,basis)
        for kind in ('evm','scurve'):
            assert domain.to_dict(actual[kind])==domain.to_dict(services.control_result(conn,p.id,kind,period,basis))
    cache.clear()


@pytest.mark.parametrize('period',['day','week','month'])
def test_horizonte_largo_remuestreo_exacto(control_db,period):
    conn,client,p,acts=control_db
    repo.update(conn,'projects',p.id,dict(start_date=date(2000,1,1),data_date=date(2026,1,9)))
    with repo.transaction(conn):
        conn.execute("UPDATE activities SET planned_start='2000-01-01',planned_finish='2040-01-01' WHERE project_id=?",(p.id,))
    cache.clear()
    full=domain.to_dict(cache.dashboard(conn,p.id,period)['scurve'])
    small=client.get(f'/api/projects/{p.id}/control/dashboard',params=dict(period=period,max_points=64)).json()['scurve']
    assert len(full['labels'])>64 and len(small['labels'])<=64
    assert small['labels'][0]==full['labels'][0] and small['labels'][-1]==full['labels'][-1]
    assert full['data_date'] in small['labels']
    for key,value in full.items():
        if isinstance(value,list):
            assert small[key]==[value[full['labels'].index(label)] for label in small['labels']]
        else:
            assert small[key]==value
    cache.clear()
