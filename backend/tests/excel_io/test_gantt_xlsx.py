"""Contrato visual/numerico del Gantt nativo y comparación con la API pública."""
from datetime import date
from io import BytesIO
from time import perf_counter
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from ganttier import db, repo, services
from ganttier.excel_io.gantt_xlsx import Options, export_gantt, parse_options


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB',str(tmp_path/'test.db'))
    monkeypatch.setattr(services,'DATA_DIR',tmp_path)
    conn = db.connect(tmp_path/'test.db')
    db.initialize(conn)
    cal = repo.create(conn,'calendars',dict(name='Especial',holidays_country=None,
        exceptions=[dict(date='2026-10-07',working=False,hours=0,reason='Feriado local')]))
    project = repo.create(conn,'projects',dict(name='Prueba = segura',start_date='2026-10-05',end_date='2026-10-16',data_date='2026-10-08',calendar_id=cal.id))
    root = repo.create(conn,'wbs',dict(project_id=project.id,code='1',name='Obra'))
    leaf = repo.create(conn,'wbs',dict(project_id=project.id,code='1.1',name='Montaje',parent_id=root.id))
    worker = repo.create(conn,'resources',dict(project_id=project.id,name='Ana',calendar_id=cal.id,capacity_hours_day=8))
    activity = repo.create(conn,'activities',dict(project_id=project.id,code='A',name='Trabajo',wbs_id=leaf.id,
        planned_start='2026-10-05',planned_finish='2026-10-09',duration_days=4,work_hours=48,percent_complete=50,critical=True,location='Sur'))
    repo.create(conn,'assignments',dict(activity_id=activity.id,resource_id=worker.id,work_hours=48))
    repo.create(conn,'activities',dict(project_id=project.id,code='H',name='Hito',wbs_id=leaf.id,
        duration_days=0,planned_start='2026-10-07',planned_finish='2026-10-07',location='Sur'))
    conn.commit()
    yield conn,project,worker,activity
    conn.close()


def test_summary_dates_calendar_milestones_critical_and_clipping(sample,tmp_path):
    conn,p,worker,a=sample
    target=export_gantt(conn,p.id,tmp_path/'nivel.xlsx',Options(group_by='sitio/wbs:1',scale='day',from_date=date(2026,10,6),to_date=date(2026,10,10),critical=True))
    book=load_workbook(target)
    sheet=book['Resumen por nivel']
    assert sheet['A6'].value=='Sur / 1 · Obra'
    assert sheet['B6'].value==2
    assert sheet['C6'].value.date()==a.planned_start
    assert sheet['D6'].value.date()==a.planned_finish
    assert sheet['C6'].number_format=='dd-mm-yyyy'
    assert sheet['I6'].value=='◆ 1'  # hito en feriado
    assert '◀' in sheet['H6'].value
    assert sheet['I3'].value==0
    assert sheet['L4'].fill.fgColor.rgb.endswith('E7E6E6')  # sábado
    assert sheet['J6'].border.left.style=='medium'  # corte 8 octubre
    assert sheet.freeze_panes=='H6'
    rules=list(sheet.conditional_formatting._cf_rules.values())[0]
    assert len(rules)==2 and '$F6="Sí"' in rules[0].formula[0]
    assert all(not ws._images for ws in book)
    book.close()
    with ZipFile(target) as archive:
        assert not any('/media/' in name for name in archive.namelist())


@pytest.mark.parametrize('scale',['day','week','month'])
def test_worker_load_matches_api(sample,tmp_path,monkeypatch,scale):
    from fastapi.testclient import TestClient
    from ganttier.main import create_app
    from ganttier.api.dependencies import connection
    conn,p,worker,a=sample
    monkeypatch.setattr(services,'DATA_DIR',tmp_path)
    app=create_app()
    app.dependency_overrides[connection]=lambda: conn
    client=TestClient(app)
    params={'mode':'trabajador','scale':scale,'from':'2026-10-06','to':'2026-10-08'}
    response=client.get(f'/api/projects/{p.id}/gantt.xlsx',params=params)
    assert response.status_code==200,response.text
    profile=client.get(f'/api/projects/{p.id}/load',params={'group':'resource','period':scale,'from':'2026-10-06','to':'2026-10-08'})
    assert profile.status_code==200,profile.text
    series=next(row for row in profile.json()['series'] if row['key']=='Ana')
    book=load_workbook(BytesIO(response.content))
    sheet=book['Por trabajador']
    assert sheet['A6'].value=='Ana' and sheet['B6'].value==worker.id
    assert [c.value for c in sheet[6]][5:]==pytest.approx(series['load'])
    assert [c.value for c in book['Capacidad'][6]][5:]==pytest.approx(series['capacity'])
    assert sheet['C6'].value.startswith('=SUM(')
    assert 'MATCH($B6' in sheet['D6'].value
    assert sheet['F7'].value=='=SUM(F6:F6)'
    assert len(list(sheet.conditional_formatting._cf_rules.values())[0])==3
    assert 'MATCH($B6' in list(sheet.conditional_formatting._cf_rules.values())[0][0].formula[0]
    assert not list((tmp_path/'tmp').glob('*.xlsx'))  # BackgroundTasks elimina el temporal
    book.close()
    client.close()


def test_dates_match_activity_api(sample,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from ganttier.main import create_app
    from ganttier.api.dependencies import connection
    conn,p,_,_=sample
    monkeypatch.setattr(services,'DATA_DIR',tmp_path)
    app=create_app(); app.dependency_overrides[connection]=lambda:conn
    with TestClient(app) as client:
        acts=client.get(f'/api/projects/{p.id}/activities').json()
        response=client.get(f'/api/projects/{p.id}/gantt.xlsx',params={'group_by':'wbs:1'})
    book=load_workbook(BytesIO(response.content))
    sheet=book['Resumen por nivel']
    assert sheet['C6'].value.date().isoformat()==min(a['planned_start'] for a in acts)
    assert sheet['D6'].value.date().isoformat()==max(a['planned_finish'] for a in acts)
    book.close()


@pytest.mark.parametrize('params,message',[
    ({'mode':'otro'},'modo'),({'scale':'year'},'escala'),({'group_by':'hack'},'Agrupación'),
    ({'critical':'1'},'crítica'),({'from':'2026-99-01'},'fecha'),({'from':'05-10-2026'},'formato'),
    ({'from':'2026-10-10','to':'2026-10-01'},'posterior'),
    ({'from':'2020-01-01','to':'2026-01-01','scale':'day'},'columnas'),
    ({'from':'0001-01-01'},'1900')])
def test_api_invalid_parameters_are_spanish_4xx(sample,tmp_path,monkeypatch,params,message):
    from fastapi.testclient import TestClient
    from ganttier.main import create_app
    from ganttier.api.dependencies import connection
    conn,p,_,_=sample
    monkeypatch.setattr(services,'DATA_DIR',tmp_path)
    app=create_app(); app.dependency_overrides[connection]=lambda:conn
    with TestClient(app) as client:
        response=client.get(f'/api/projects/{p.id}/gantt.xlsx',params=params)
        assert response.status_code==422,response.text
        assert message in response.json()['detail']
        assert client.get('/api/projects/999999/gantt.xlsx').status_code==404


def test_capacity_partial_exception_and_duplicate_names(sample,tmp_path):
    conn,p,worker,_=sample
    cal=repo.create(conn,'calendars',dict(name='Personal',holidays_country=None,
        exceptions=[dict(date='2026-10-06',working=True,hours=4)]))
    repo.update(conn,'resources',worker.id,dict(calendar_id=cal.id))
    other=repo.create(conn,'resources',dict(project_id=p.id,name='Ana',calendar_id=cal.id))
    path=export_gantt(conn,p.id,tmp_path/'workers.xlsx',Options(mode='trabajador',scale='day',from_date=date(2026,10,6),to_date=date(2026,10,6)))
    book=load_workbook(path)
    assert book['Capacidad']['F6'].value==4
    assert {book['Por trabajador']['B6'].value,book['Por trabajador']['B7'].value}=={worker.id,other.id}
    book.close()


def test_atomic_destination_and_literal_text(sample,tmp_path):
    conn,p,_,_=sample
    target=tmp_path/'keep.xlsx'; target.write_bytes(b'original')
    with pytest.raises(ValueError):
        export_gantt(conn,p.id,target,Options(scale='day',from_date=date(2020,1,1),to_date=date(2026,1,1)))
    assert target.read_bytes()==b'original'
    conn.execute("UPDATE activities SET location='=1+1'")
    export_gantt(conn,p.id,target,Options(group_by='sitio'))
    book=load_workbook(target)
    assert book['Resumen por nivel']['A6'].data_type=='s'
    assert book['Resumen por nivel']['A6'].value=='=1+1'
    book.close()
    assert not list(tmp_path.glob('.gantt*'))


def test_persons_paginate_without_merging_and_global_totals(sample,tmp_path,monkeypatch):
    from ganttier.excel_io import gantt_xlsx
    conn,p,_,_=sample
    monkeypatch.setattr(gantt_xlsx,'MAX_ROWS',2)
    for name in ('Bea','Caro'):
        repo.create(conn,'resources',dict(project_id=p.id,name=name,calendar_id=p.calendar_id))
    book=load_workbook(export_gantt(conn,p.id,tmp_path/'pages.xlsx',Options(mode='trabajador')))
    assert 'Por trabajador 2' in book.sheetnames and 'Capacidad 2' in book.sheetnames
    assert book['Por trabajador']['A6'].value=='Ana'
    assert book['Por trabajador 2']['A6'].value=='Caro'
    assert "'Por trabajador 2'!" in book['Totales por período']['C2'].value
    book.close()


def test_missing_progress_and_read_transaction(sample,tmp_path):
    conn,p,_,_=sample
    conn.execute('UPDATE activities SET percent_complete=NULL')
    conn.commit()
    assert not conn.in_transaction
    book=load_workbook(export_gantt(conn,p.id,tmp_path/'null.xlsx'))
    assert book['Resumen por nivel']['E6'].value is None
    assert not conn.in_transaction
    book.close()
    conn.execute("UPDATE projects SET description='pendiente'")
    export_gantt(conn,p.id,tmp_path/'pending.xlsx')
    assert conn.in_transaction
    assert conn.execute('SELECT description FROM projects WHERE id=?',(p.id,)).fetchone()[0]=='pendiente'
    conn.rollback()


@pytest.mark.slow
def test_100000_activities_aggregate_before_writing(sample,tmp_path):
    conn,p,worker,_=sample
    memory = db.connect(':memory:')
    conn.backup(memory)
    conn = memory
    conn.execute('DELETE FROM activities')
    conn.executemany('''INSERT INTO activities(project_id,code,name,location,planned_start,planned_finish,duration_days,work_hours)
        VALUES (?,?,?,?,?,?,?,?)''',((p.id,f'A{i}',f'Tarea {i}',f'Sitio {i%1200:04}', '2026-10-05','2026-10-09',4,32) for i in range(100000)))
    conn.execute('INSERT INTO assignments(activity_id,resource_id,work_hours) SELECT id,?,work_hours FROM activities',(worker.id,))
    conn.commit()
    start=perf_counter()
    path=export_gantt(conn,p.id,tmp_path/'100k.xlsx',Options(group_by='sitio',scale='day'))
    worker_path=export_gantt(conn,p.id,tmp_path/'100k_workers.xlsx',Options(mode='trabajador',scale='day'))
    elapsed=perf_counter()-start
    assert elapsed<60,elapsed
    book=load_workbook(path)
    rows=list(book['Resumen por nivel'].values)[5:]
    assert len(rows)<=500
    assert sum(r[1] for r in rows)==100000
    assert any(r[0]=='Agrupamiento automático' for r in book['Leyenda'].values)
    book.close()
    book=load_workbook(worker_path)
    assert sum(c.value for c in book['Por trabajador'][6][5:])==pytest.approx(3200000)
    book.close()
    (tmp_path/'benchmark.txt').write_text(f'100000 actividades y asignaciones; dos modos: {elapsed:.3f} s',encoding='utf-8')
    conn.close()
