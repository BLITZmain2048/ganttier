"""Regresiones H1: paginación real, caché coherente y guardado acotado."""
from datetime import date
from time import perf_counter
import os

import pytest
from fastapi.testclient import TestClient

from ganttier import db, domain, repo, services
from ganttier.main import create_app

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def make_page(client, project, tmp_path, qtbot, monkeypatch):
    from PySide6.QtCore import QSettings
    from ganttier.gui.client import ApiClient
    from ganttier.gui.state import AppState
    from ganttier.gui.pages import control
    errors = []
    monkeypatch.setattr(control, 'show_error', lambda exc, parent=None: errors.append(str(exc)))
    monkeypatch.setattr(control, 'notify', lambda *args: None)
    api = ApiClient('http://testserver')
    api._http.close()
    api._http = client
    state = AppState(api, QSettings(str(tmp_path/'settings.ini'), QSettings.IniFormat))
    state.project = domain.to_dict(project)
    page = control.ControlPage(state)
    qtbot.addWidget(page)
    page.resize(1200,900)
    page.show()
    return page, errors


def table_ready(qtbot, page):
    qtbot.waitUntil(lambda: not page.loading and not page._search_timer.isActive(), timeout=30000)


def idle(qtbot):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not _active, timeout=120000)


def painted(qtbot, page):
    """Espera también el draw_idle; terminar HTTP no equivale a terminar pintura."""
    idle(qtbot)
    qtbot.waitUntil(lambda: page.chart is None or not page.chart.canvas._draw_pending, timeout=30000)
    qtbot.wait(1)


@pytest.fixture
def control_db(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path / 'control.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR', str(tmp_path / 'datos'))
    with TestClient(create_app()) as client:
        conn = db.connect()
        p = repo.create(conn, 'projects', dict(name='H1', start_date=date(2026,1,1), data_date=date(2026,1,9)))
        acts = repo.create_many(conn, 'activities', [dict(project_id=p.id, code=f'A{i:03}',
            name='Instalación Ñandú Straße' if i == 1 else 'Actividad', planned_start=date(2026,1,5),
            planned_finish=date(2026,1,9), duration_days=5, work_hours=40, cost_budget=400) for i in range(230)])
        yield conn, client, p, acts
        conn.close()


def test_pagina_servidor_y_busqueda_unicode(control_db):
    conn, client, p, acts = control_db
    path = f'/api/projects/{p.id}/control/activities'
    first = client.get(path, params=dict(scope='all', limit=200)).json()
    assert first['total'] == 230
    assert len(first['rows']) == 200
    second = client.get(path, params=dict(scope='all', limit=200, offset=200)).json()
    assert len(second['rows']) == 30
    assert not {r['id'] for r in first['rows']} & {r['id'] for r in second['rows']}
    result = client.get(path, params=dict(q='ÑANDÚ STRASSE')).json()
    assert [r['id'] for r in result['rows']] == [acts[1].id]
    assert client.get(path, params={'limit':100001}).status_code == 422
    assert client.get(path, params={'scope':'invalido'}).status_code == 422


@pytest.mark.parametrize('basis', ['hours','cost','count'])
@pytest.mark.parametrize('period', ['day','week','month'])
def test_panel_equivale_y_cache_invalida(control_db, basis, period):
    conn, client, p, acts = control_db
    path = f'/api/projects/{p.id}/control/dashboard'
    def check():
        r = client.get(path, params=dict(basis=basis, period=period))
        assert r.status_code == 200, r.text
        value = r.json()
        assert value['evm'] == domain.to_dict(services.control_result(conn,p.id,'evm',period,basis))
        assert value['scurve'] == domain.to_dict(services.control_result(conn,p.id,'scurve',period,basis))
        return value
    before = check()
    assert check() == before
    result = client.post(f'/api/projects/{p.id}/progress', json=dict(activity_id=acts[0].id,
        date='2026-01-09', percent_complete=50, actual_hours=3, actual_cost=30))
    assert result.status_code == 201, result.text
    assert check()['evm']['EV'] > before['evm']['EV']
    # Escritura en otra conexión: no depende de que el endpoint avise al caché.
    other = db.connect()
    with repo.transaction(other):
        other.execute('UPDATE activities SET work_hours=80,cost_budget=800 WHERE id=?', (acts[1].id,))
    other.close()
    assert check()['evm']['BAC'] >= before['evm']['BAC']


def test_guardar_no_lee_todo_el_proyecto(control_db, monkeypatch):
    conn, client, p, acts = control_db
    original = services.build_activity_views
    calls = []
    def watched(*args, **kwargs):
        calls.append(kwargs.get('activity_ids'))
        return original(*args, **kwargs)
    monkeypatch.setattr(services, 'build_activity_views', watched)
    r = client.post(f'/api/projects/{p.id}/progress', json=dict(activity_id=acts[0].id,
        date='2026-01-09', percent_complete=50, actual_hours=3))
    assert r.status_code == 201, r.text
    assert calls and all(ids is not None and set(ids) == {acts[0].id} for ids in calls)
    assert repo.get(conn,'activities',acts[0].id).actual_hours == 3


def test_cache_remuestreo_transaccion_y_calendario(control_db,monkeypatch):
    from ganttier.control import cache
    conn,client,p,acts=control_db
    cache.clear()
    calls=[]
    compute=cache._compute
    def watched(*args):
        calls.append(1)
        return compute(*args)
    monkeypatch.setattr(cache,'_compute',watched)
    before=cache.dashboard(conn,p.id)
    monthly=cache.dashboard(conn,p.id,period='month')
    assert monthly['scurve'].period=='month' and len(calls)==1
    before['scurve'].planned_abs[0]=-999
    assert cache.dashboard(conn,p.id)['scurve'].planned_abs[0]>=0
    # Los cambios sin confirmar se leen para su transacción, jamás se cachean.
    conn.execute('BEGIN')
    conn.execute('UPDATE activities SET work_hours=800 WHERE id=?',(acts[0].id,))
    assert cache.dashboard(conn,p.id)['evm'].BAC>monthly['evm'].BAC
    conn.rollback()
    assert cache.dashboard(conn,p.id)['evm']==monthly['evm']
    assert len(calls)==2
    cid=repo.list_entities(conn,'calendars')[0].id
    repo.update(conn,'calendars',cid,dict(name='Calendario cambiado'))
    cache.dashboard(conn,p.id)
    assert len(calls)==3
    repo.update(conn,'projects',p.id,dict(data_date=date(2026,1,7)))
    assert cache.dashboard(conn,p.id)['evm'].data_date==date(2026,1,7)
    assert len(calls)==4
    cache.clear()


def test_filtro_maxima_fecha_no_incluye_sin_inicio(control_db):
    conn,client,p,acts=control_db
    repo.update(conn,'projects',p.id,dict(data_date=date.max))
    repo.update(conn,'activities',acts[0].id,dict(planned_start=None))
    result=client.get(f'/api/projects/{p.id}/control/activities',params=dict(scope='open',limit=500)).json()
    assert acts[0].id not in {row['id'] for row in result['rows']}


@pytest.mark.parametrize('cut', ['2025-12-01','2026-01-09','2026-02-01'])
@pytest.mark.parametrize('baseline', ['none','empty','partial','full'])
def test_lectura_control_equivalente(control_db,cut,baseline):
    from ganttier.control.inputs import control_inputs
    conn,client,p,acts=control_db
    repo.create_many(conn,'progress',[
        dict(activity_id=acts[0].id,date=date(2026,1,6),percent_complete=20,actual_hours=.1,actual_start=date(2026,1,5)),
        dict(activity_id=acts[0].id,date=date(2026,1,5),percent_complete=10,actual_hours=.2),
        dict(activity_id=acts[0].id,date=date(2026,1,6),percent_complete=40,actual_hours=.3),
        dict(activity_id=acts[1].id,date=date(2026,1,9),percent_complete=100,actual_start=date(2026,1,5)),
        dict(activity_id=acts[2].id,date=date(2026,2,1),percent_complete=50),
    ])
    repo.update(conn,'activities',acts[3].id,dict(percent_complete=45,actual_start=date(2026,1,8),actual_hours=4))
    if baseline!='none':
        b=services.create_baseline(conn,p.id,'Base H1',True)
        with repo.transaction(conn):
            if baseline=='empty':
                conn.execute('DELETE FROM baseline_activities WHERE baseline_id=?',(b.id,))
            elif baseline=='partial':
                conn.execute('DELETE FROM baseline_activities WHERE baseline_id=? AND activity_id>?',(b.id,acts[2].id))
    repo.update(conn,'projects',p.id,dict(data_date=date.fromisoformat(cut)))
    reference=services.control_inputs(conn,p.id,assignments=[])
    actual=control_inputs(conn,p.id)
    for key in ('project','progress','acts','cutoff','baseline'):
        assert actual[key]==reference[key], key
    for basis in ('hours','cost','count'):
        from ganttier.control.cache import dashboard
        result=dashboard(conn,p.id,basis=basis)
        for kind in ('evm','scurve'):
            assert domain.to_dict(result[kind])==domain.to_dict(services.control_result(conn,p.id,kind,basis=basis))


def test_gui_borrador_paginado_y_guardado(control_db, tmp_path, qtbot, monkeypatch):
    conn, client, p, acts = control_db
    page, errors = make_page(client,p,tmp_path,qtbot,monkeypatch)
    page.refresh()
    table_ready(qtbot,page)
    aid=page.model.rows[0]['id']
    assert page.model.setData(page.model.index(0,6),45)
    page.change_page(1)
    table_ready(qtbot,page)
    assert page.model.rowCount()==30 and page.model.rows[0]['id']!=aid
    page.search.setText('ninguna coincidencia')
    table_ready(qtbot,page)
    assert page.model.rowCount()==0 and aid in page.drafts
    page.save_progress()
    idle(qtbot)
    assert not page.drafts
    assert repo.get(conn,'activities',aid).percent_complete==45
    page.search.clear()
    table_ready(qtbot,page)
    assert len(page.arrays['ids'])<=200
    idle(qtbot)
    assert not errors


def test_gui_post_confirmado_fallo_recarga(control_db,tmp_path,qtbot,monkeypatch):
    conn,client,p,acts=control_db
    page,errors=make_page(client,p,tmp_path,qtbot,monkeypatch)
    page.refresh()
    idle(qtbot)
    assert page.model.setData(page.model.index(0,6),40)
    assert page.model.setData(page.model.index(0,7),3)
    def failed(pid):
        raise RuntimeError('Fallo de recarga')
    monkeypatch.setattr(page.state.client,'project',failed)
    page.save_progress()
    idle(qtbot)
    assert not page.drafts and not page.save_button.isEnabled()
    assert 'se guardó' in page.status.text()
    assert len(errors)==1
    page.save_progress()
    assert conn.execute('SELECT COUNT(*) FROM progress').fetchone()[0]==1


@pytest.mark.slow
def test_avance_100k_umbral_real(tmp_path,monkeypatch,qtbot):
    """≤2 s tabla operativa (incluye construir página); ≤0,3 s guardar y recargar tabla.

    Los indicadores se calculan en segundo plano; su latencia fría se informa
    separadamente. El refresco completo con caché también debe cumplir ≤2 s.
    """
    monkeypatch.setenv('GANTTIER_DB',str(tmp_path/'escala.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR',str(tmp_path/'datos'))
    from ganttier.control import cache
    with TestClient(create_app()) as client:
        conn=db.connect()
        p=services.demo_maintenance(100000,conn)
        with repo.transaction(conn):
            conn.execute("UPDATE projects SET data_date='2026-07-01' WHERE id=?",(p.id,))
            conn.execute("""INSERT INTO progress(activity_id,date,percent_complete,actual_hours,actual_start)
                SELECT id,'2026-07-01',25,2,MIN(planned_start,'2026-07-01') FROM activities WHERE project_id=?""",(p.id,))
        services.sync_progress(conn,p.id)
        p=repo.get(conn,'projects',p.id)
        cache.clear()
        start=perf_counter()
        page,errors=make_page(client,p,tmp_path,qtbot,monkeypatch)
        page.refresh()
        table_ready(qtbot,page)
        opening=perf_counter()-start
        painted(qtbot,page)
        start=perf_counter()
        page.refresh()
        painted(qtbot,page)
        refresh=perf_counter()-start
        aid=page.model.rows[0]['id']
        start=perf_counter()
        assert page.model.setData(page.model.index(0,6),50)
        edit=perf_counter()-start
        start=perf_counter()
        page.save_progress()
        qtbot.waitUntil(lambda:not page.saving and not page.loading,timeout=30000)
        save=perf_counter()-start
        painted(qtbot,page)
        assert not errors
        assert repo.get(conn,'activities',aid).percent_complete==50
        assert page.model.rowCount()<=200 and len(page.arrays['ids'])<=200
        print(dict(apertura=opening,refresco=refresh,edicion=edit,guardado=save))
        cache.clear()
        conn.close()
        assert opening<=2.0
        assert refresh<=2.0
        assert edit<=.3
        assert save<=.3
