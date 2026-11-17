"""Contrato MSP/P6 y recorrido HTTP con registro público del adaptador."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import Workbook
from fastapi.testclient import TestClient
from ganttier import services, db, repo
from ganttier.importadores import servicio
from ganttier.importadores.modelos import Perfil
from ganttier.importadores.proyecto_externo import AdaptadorMSP, AdaptadorP6

APP = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('generador_msp_p6', APP/'ejemplos'/'generar_formatos_msp_p6.py')
generator = importlib.util.module_from_spec(spec); spec.loader.exec_module(generator)


def book(tmp_path, headers, rows, *, title='Tareas', header_row=1):
    wb=Workbook(); ws=wb.active; ws.title=title
    for _ in range(header_row-1): ws.append(['Título'])
    ws.append(headers)
    for row in rows: ws.append(row)
    path=tmp_path/'entrada.xlsx'; wb.save(path); wb.close(); return path


def preview(path, adapter=None, s=None, profile=None):
    adapter=adapter or AdaptadorMSP()
    if profile is None:
        found=adapter.detectar(path); assert found
        profile=Perfil.model_validate(found[0]['perfil'])
    return adapter.previsualizar(path,profile,s or {})


@pytest.fixture(scope='module')
def examples(tmp_path_factory):
    root=tmp_path_factory.mktemp('msp_p6_examples'); manifest=generator.generate(root)
    return root,manifest


@pytest.fixture
def registered(monkeypatch):
    # The round's snapshot lacks the optional loader present in the canonical app.
    # Exercise the documented public extension point, without editing servicio.py.
    monkeypatch.setattr(servicio,'LECTORES',servicio.LECTORES.copy())
    for adapter in (AdaptadorMSP(),AdaptadorP6()):
        servicio.LECTORES.pop(adapter.formato,None); servicio.registrar(adapter)


@pytest.mark.parametrize('variant',['msp_es','msp_en','p6_unica','p6_multi'])
@pytest.mark.parametrize('n',[40,2000])
def test_examples_exact_counts(examples,variant,n):
    root,manifest=examples; name=f'se220_{variant}_{n}.xlsx'; path=root/'ejemplos'/'formatos'/name
    adapter=AdaptadorMSP() if variant.startswith('msp') else AdaptadorP6()
    other=AdaptadorP6() if variant.startswith('msp') else AdaptadorMSP()
    assert other.detectar(path)==[]
    draft=preview(path,adapter)
    assert not draft.tiene_errores, draft.advertencias
    assert draft.resumen==manifest[name]
    assert not adapter.convertir(draft).has_errors
    assert all(x['estado']=='SUPUESTO' for x in draft.faltantes)
    assert all('Fuente SHA-256' in a['notes'] for a in draft.modelo['activities'])


@pytest.mark.parametrize('variant',['msp_es','msp_en','p6_unica'])
def test_csv_equals_xlsx(examples,variant):
    root,_=examples; directory=root/'ejemplos'/'formatos'
    adapter=AdaptadorMSP() if variant.startswith('msp') else AdaptadorP6()
    a=preview(directory/f'se220_{variant}_40.xlsx',adapter)
    b=preview(directory/f'se220_{variant}_40.csv',adapter)
    assert a.resumen==b.resumen
    assert not b.tiene_errores,b.advertencias
    assert a.modelo['relations']==b.modelo['relations']


@pytest.mark.parametrize('token,typ,lag',[('1FC+2d','FS',2),('1CC-1d','SS',-1),('1FF','FF',0),('1CF','SF',0),
                                        ('1 FS +8h','FS',1),('1SS+2sem','SS',10),('1SF-16h','SF',-2)])
def test_predecessors(tmp_path,token,typ,lag):
    p=book(tmp_path,['ID','Task Name','Duration','Predecessors'],[[1,'Primera','1 d',''],[2,'Segunda','8h',token]])
    d=preview(p)
    assert not d.tiene_errores,d.advertencias
    assert d.modelo['relations']==[dict(pred_code='1',succ_code='2',type=typ,lag_days=lag)]


def test_multiple_predecessors_and_decimal_lags(tmp_path):
    p=book(tmp_path,['ID','Task Name','Duration','Predecessors'],[[1,'A','1d',''],[2,'B','1d',''],[3,'C','1d','1FS+1,5d;2SS-0,5d']])
    d=preview(p); assert not d.tiene_errores,d.advertencias
    assert [r['lag_days'] for r in d.modelo['relations']]==[2,0]
    assert len([e for e in d.advertencias if e['codigo']=='redondeo'])==2
    assert preview(p,s={'redondeo_dias':'rechazar'}).tiene_errores


def test_cycles_only_mark_cycle_rows_and_missing_reference(tmp_path):
    p=book(tmp_path,['ID','Task Name','Duration','Predecessors'],[[1,'A',1,'2'],[2,'B',1,'1'],[3,'Descendiente',1,'2'],[4,'D',1,'404']])
    d=preview(p)
    assert {e['fila'] for e in d.advertencias if e['codigo']=='ciclo'}=={2,3}
    assert any(e['fila']==5 and e['columna']=='D' and e['codigo']=='referencia_inexistente' for e in d.advertencias)
    with pytest.raises(ValueError): AdaptadorMSP().convertir(d)


def test_profile_dates_numbers_constraints_progress(tmp_path):
    p=book(tmp_path,['Identificador','Nombre propio','Duracion propia','Fecha','Avance','Trabajo','Comienzo real','Fin real','Restricción','Fecha de restricción'],
           [['X','Obra','1,5 d','02-03-2027','50%','1.234,5 h','02/03/2027','','No comenzar antes del','2027-03-02']],title='Obras',header_row=4)
    profile=Perfil(id='custom',nombre='Cliente',formato='msp_excel',hoja='Obras',fila_encabezado=4,
                   mapeo={'code':'A','name':'Nombre propio','duration':'C','start':'D','percent':'E'})
    d=preview(p,profile=profile,s={'fecha_fin':'2027-12-31','fecha_corte':'2027-03-03'})
    assert not d.tiene_errores,d.advertencias
    a=d.modelo['activities'][0]
    assert (a['work_hours'],a['duration_days'],a['planned_start'],a['constraint_type'])==(1234.5,2,'2027-03-02','SNET')
    assert 'window_start' not in a and a['percent_complete']==50
    assert d.modelo['progress'][0]['date']=='2027-03-03'


@pytest.mark.parametrize('field,value,column', [('Duration','-1d','C'),('Start','31/02/2027','D'),('% Complete',101,'E'),
    ('Constraint Type','Nada conocido','F'),('Constraint Type','Must Start On','G')])
def test_invalid_cells_have_locations(tmp_path,field,value,column):
    headers=['ID','Task Name','Duration','Start','% Complete','Constraint Type','Constraint Date']
    values=['1','A',1,'2027-01-04',0,'ASAP','']; values[headers.index(field)]=value
    d=preview(book(tmp_path,headers,[values]))
    assert any(e['nivel']=='error' and e['hoja']=='Tareas' and e['fila']==2 and e['columna']==column for e in d.advertencias),d.advertencias


def test_wbs_milestone_resources(tmp_path):
    p=book(tmp_path,['ID','Task Name','Duration','Summary','Outline Level','Resource Names','Work'],
           [['S','Subestación',None,'Yes',1,'',''],['S1','Montaje',None,'Yes',2,'',''],
            ['A','Montar',1,'No',3,'Juan[50%];Ana',12],['M','Hito',0,'No',3,'',0]])
    d=preview(p); assert not d.tiene_errores,d.advertencias
    assert d.wbs[1]['parent_code']=='WBS_S'
    assert d.modelo['activities'][0]['wbs_code']=='WBS_S1'
    assert [a['work_hours'] for a in d.modelo['assignments']]==[4,8]
    assert d.resumen['hitos']==1 and len(d.excluidos)==2


def test_formula_cached_and_missing(tmp_path):
    p=book(tmp_path,['ID','Task Name','Duration'],[['A','Obra','=1+1']])
    d=preview(p); assert any(e['fila']==2 and e['columna']=='C' and e['nivel']=='error' for e in d.advertencias)
    cached=tmp_path/'cached.xlsx'
    with ZipFile(p) as src, ZipFile(cached,'w') as dst:
        for info in src.infolist():
            raw=src.read(info.filename)
            if info.filename=='xl/worksheets/sheet1.xml': raw=raw.replace(b'<f>1+1</f><v></v>',b'<f>1+1</f><v>2</v>')
            dst.writestr(info,raw)
    d=preview(cached); assert not d.tiene_errores,d.advertencias
    assert d.modelo['activities'][0]['duration_days']==2


def test_empty_repeated_headers_merged_and_zip_limits(tmp_path):
    p=book(tmp_path,['ID','Task Name','Duration'],[])
    assert preview(p).tiene_errores
    p=book(tmp_path,['ID','Task Name','Duration'],[['ID','Task Name','Duration'],['A','Actividad',1]])
    assert any(e['codigo']=='encabezado_repetido' for e in preview(p).advertencias)
    from openpyxl import load_workbook
    wb=load_workbook(p); wb.active.merge_cells('B3:C3'); wb.save(p); wb.close()
    assert any(e['codigo']=='celdas_combinadas' for e in preview(p).advertencias)
    bad=tmp_path/'bad.xlsx'
    with ZipFile(bad,'w') as z: z.writestr('../escape','x')
    profile=Perfil(id='bad',nombre='Bad',formato='msp_excel',mapeo={'code':'A','name':'B','duration':'C'})
    with pytest.raises(ValueError,match='ZIP'): preview(bad,profile=profile)


def test_auxiliary_calendar_exceptions_and_bad_references(tmp_path):
    tables=generator.tables_multi(generator.dataset())
    tables['CALENDAR'][1][0][-1]=json.dumps([dict(date='2027-02-01',working=False,hours=0,reason='Feriado propio')])
    p=tmp_path/'multi.xlsx'; generator.workbook(p,tables)
    d=preview(p,AdaptadorP6()); assert not d.tiene_errores,d.advertencias
    assert d.modelo['calendars'][1]['exceptions'][0]['reason']=='Feriado propio'
    tables['TASKPRED'][1][0][0]=99999
    tables['TASKRSRC'][1][0][1]=99999
    tables['PROJWBS'][1][1][-1]=99999
    generator.workbook(p,tables); d=preview(p,AdaptadorP6())
    assert {e['hoja'] for e in d.advertencias if e['codigo']=='referencia_inexistente'}=={'TASKPRED','TASKRSRC','PROJWBS'}


@pytest.mark.parametrize('variant',['msp_es','msp_en','p6_unica','p6_multi'])
def test_http_upload_preview_confirm_and_idempotency(tmp_path,monkeypatch,registered,examples,variant):
    from ganttier.main import create_app
    root,_=examples; p=root/'ejemplos'/'formatos'/f'se220_{variant}_40.xlsx'
    monkeypatch.setattr(services,'DATA_DIR',tmp_path/'data')
    monkeypatch.setenv('GANTTIER_DB',str(tmp_path/'api.db'))
    with TestClient(create_app()) as client:
        up=client.post('/api/importadores/archivos',files={'file':(p.name,p.read_bytes())})
        assert up.status_code==201,up.text
        assert up.json()['detecciones'][0]['formato']==('msp_excel' if variant.startswith('msp') else 'p6_excel')
        ident=up.json()['id']
        pre=client.post(f'/api/importadores/borradores/{ident}',json={})
        assert pre.status_code==200,pre.text
        body=dict(revision=pre.json()['revision'],aceptar_supuestos=True,aceptar_advertencias=True)
        rejected=client.post(f'/api/importadores/confirmar/{ident}',json={**body,'aceptar_supuestos':False})
        assert rejected.status_code==422
        created=client.post(f'/api/importadores/confirmar/{ident}',json=body)
        assert created.status_code==201,created.text
        assert created.json()['resumen']['actividades']==40
        assert client.post(f'/api/importadores/confirmar/{ident}',json=body).json()==created.json()
        pid=created.json()['proyecto']['id']
    conn=db.connect(tmp_path/'api.db')
    try:
        assert len(repo.list_entities(conn,'activities',pid))==40
        assert len(repo.list_entities(conn,'progress',pid))==4
        assert len(repo.list_entities(conn,'assignments',pid))==72
        assert repo.get(conn,'projects',pid).data_date.isoformat()=='2027-01-04'
    finally: conn.close()


def test_negative_existing_formats(tmp_path):
    # Native workbook, OM-WBS, E2-MP, inventory and unrelated matrix headers.
    headers=[['Codigo','Nombre','Duracion_Dias','HH'],['Item','Descripción','Frecuencia','ENE','FEB'],
             ['Equipo','Actividad','Precio','Cantidad','Total'],['Planta','Bloque','Objeto/Tipo','Cantidad','Unidad','Detalle','Fuente'],
             ['Actividad','Enero 2027','Febrero 2027']]
    for h in headers:
        p=book(tmp_path,h,[])
        assert AdaptadorMSP().detectar(p)==[] and AdaptadorP6().detectar(p)==[]


@pytest.mark.parametrize('encoding,delimiter',[('utf-8-sig',','),('cp1252',';'),('latin-1','\t')])
def test_csv_encoding_and_delimiter(tmp_path,encoding,delimiter):
    import csv
    p=tmp_path/'export.csv'
    with p.open('w',encoding=encoding,newline='') as f:
        writer=csv.writer(f,delimiter=delimiter)
        writer.writerow(['ID','Nombre de tarea','Duración','Predecesoras'])
        writer.writerows([['1','Revisión eléctrica','1d',''],['2','Montaje','1d','1FC']])
    d=preview(p); assert not d.tiene_errores,d.advertencias
    assert d.modelo['activities'][0]['name']=='Revisión eléctrica'
    assert d.resumen['relaciones']==1


def test_bounds_and_duplicate_columns(tmp_path,monkeypatch):
    from ganttier.importadores import _p6_msp_campos as campos
    p=book(tmp_path,['ID','Task Name','Duration'],[[1,'A',1],[2,'B',1],[3,'C',1]])
    monkeypatch.setattr(campos,'MAX_ROWS',2)
    with pytest.raises(ValueError,match='200.000'): preview(p)
    monkeypatch.undo()
    p=book(tmp_path,['ID','Task Name','Duration','ID'],[[1,'A',1,2]])
    d=preview(p); assert any(e['fila']==1 and e['columna']=='D' for e in d.advertencias)
    p=book(tmp_path,['ID','Task Name','Duration']+['']*198,[[1,'A',1]+['']*197+['fuera']])
    profile=Perfil(id='limits',nombre='Límites',formato='msp_excel',hoja='Tareas',mapeo={'code':'A','name':'B','duration':'C'})
    with pytest.raises(ValueError,match='200 columnas'): preview(p,profile=profile)


def test_milestone_and_actual_dates_errors(tmp_path):
    p=book(tmp_path,['ID','Task Name','Duration','Work','% Complete','Actual Start','Actual Finish'],
           [[1,'Hito',0,4,0,'',''],[2,'Terminada',1,8,50,'2027-01-04','2027-01-05']])
    d=preview(p,s={'fecha_fin':'2027-12-31','fecha_corte':'2027-01-04'})
    assert any(e['fila']==2 and e['columna']=='D' for e in d.advertencias)
    assert any(e['fila']==3 and e['columna']=='E' for e in d.advertencias)
    assert any(e['fila']==3 and e['columna']=='G' for e in d.advertencias)


def test_native_excel_roundtrip(tmp_path,examples):
    from ganttier.excel_io import export_project,parse_workbook
    root,_=examples
    draft=preview(root/'ejemplos'/'formatos'/'se220_p6_multi_40.xlsx',AdaptadorP6())
    source=AdaptadorP6().convertir(draft)
    path=tmp_path/'nativo.xlsx'; export_project(source,path)
    result=parse_workbook(path)
    assert not result.has_errors,result.issues
    for collection in ('activities','relations','wbs','resources','assignments','progress','calendars'):
        assert len(getattr(source,collection))==len(getattr(result,collection))
    assert {a['code']:a['planned_start'] for a in source.activities}=={a['code']:a['planned_start'].isoformat() for a in result.activities}
    assert not AdaptadorMSP().detectar(path) and not AdaptadorP6().detectar(path)


def test_native_templates_are_not_detected(tmp_path):
    from ganttier.excel_io import build_project_template,build_maintenance_template
    for builder in (build_project_template,build_maintenance_template):
        path=tmp_path/(builder.__name__+'.xlsx'); builder(path)
        assert AdaptadorMSP().detectar(path)==[] and AdaptadorP6().detectar(path)==[]


def test_p6_all_relation_types_and_wbs_cycle(tmp_path):
    tables=generator.tables_multi(generator.dataset())
    for i,typ in enumerate(('PR_FS','PR_SS','PR_FF','PR_SF')):
        tables['TASKPRED'][1][i][2]=typ; tables['TASKPRED'][1][i][3]=(-8 if i==3 else 16)
    p=tmp_path/'multi.xlsx'; generator.workbook(p,tables)
    d=preview(p,AdaptadorP6()); assert not d.tiene_errores,d.advertencias
    assert [r['type'] for r in d.modelo['relations'][:4]]==['FS','SS','FF','SF']
    assert [r['lag_days'] for r in d.modelo['relations'][:4]]==[2,2,2,-1]
    tables['PROJWBS'][1][0][-1]=10
    generator.workbook(p,tables); d=preview(p,AdaptadorP6())
    assert {e['fila'] for e in d.advertencias if e['codigo']=='ciclo' and e['hoja']=='PROJWBS'}=={2,3}


def test_p6_auxiliary_trace_and_summary_existing_wbs(tmp_path):
    tables=generator.tables_multi(generator.dataset())
    summary=list(tables['TASK'][1][0])
    summary[0:3]=[999,'SUMMARY','Resumen de ingeniería']
    summary[8]='TT_WBSsum'
    tables['TASK'][1].append(summary)
    p=tmp_path/'multi.xlsx'; generator.workbook(p,tables)
    d=preview(p,AdaptadorP6())
    assert not d.tiene_errores,d.advertencias
    assert d.resumen['wbs']==5 and d.resumen['actividades']==40
    trace=d.modelo['origen_auxiliar']['TASKPRED'][0]
    assert trace['hoja']=='TASKPRED' and trace['fila']==2 and trace['celdas']['pred']=='A2'
    assert trace['valores']['pred']=='1'


def test_excessive_wbs_depth_is_row_error(tmp_path):
    p=book(tmp_path,['Activity ID','Activity Name','Original Duration','WBS'],[['A','Obra',1,'.'.join(['W']*65)]])
    d=preview(p,AdaptadorP6())
    assert any(e['nivel']=='error' and e['fila']==2 and e['columna']=='D' for e in d.advertencias)
