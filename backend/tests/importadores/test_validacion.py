"""Casos pequeños que prueban validación y persistencia, no solo conteos."""
from datetime import date
from pathlib import Path
import json
import openpyxl
import pytest
from ganttier import db, repo, services
from ganttier.importadores import previsualizar, confirmar, detectar, AlmacenPerfiles, Perfil


def matriz(path):
    book = openpyxl.Workbook(); ws = book.active; ws.title = 'OM-WBS'
    ws.append(['Proyectos', 'Central [SOL]', None, None, 2026])
    ws.append(['Código', 'Descripción', 'Diario', 'Mensual', 'Trimestral', 'Semestral', 'Anual', 'Ene', 'Feb', 'Mar'])
    ws.append(['1', 'Familia'])
    ws.append(['1.1', 'Revisión', None, 'X', None, None, None, 'SOL', 'SOL', 'SOL'])
    book.save(path)
    return book


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path/'datos')
    conn = db.connect(tmp_path/'test.db'); db.initialize(conn)
    yield conn
    conn.close()


def test_validaciones_frecuencia_duplicado_y_meses(tmp_path):
    path = tmp_path/'mal.xlsx'; book = matriz(path); ws=book.active
    ws['D4'] = None; ws.append(['1.1','Duplicado',None,None,None,'X',None,'SOL','SOL','SOL']); book.save(path)
    draft = previsualizar(path)
    assert draft.tiene_errores
    assert {'sin_frecuencia','codigo_duplicado','frecuencia_meses'} <= {a['codigo'] for a in draft.advertencias}


def test_notas_wbs_y_calendarios_distintos(conn, tmp_path):
    path=tmp_path/'fuente.xlsx'; matriz(path)
    first = confirmar(conn,path,aceptar_supuestos=True,aceptar_advertencias=True)
    other = confirmar(conn,path,supuestos={'calendario': {'semana':'1111110','horas_dia':6,'pais':''}},
                      aceptar_supuestos=True,aceptar_advertencias=True)
    assert first['proyecto']['calendar_id'] != other['proyecto']['calendar_id']
    activities=repo.list_entities(conn,'activities',first['proyecto']['id'])
    assert len(activities)==3 and all('SUPUESTO' in a.notes and 'D4' in a.notes for a in activities)
    nodes=repo.list_entities(conn,'wbs',first['proyecto']['id'])
    assert nodes[1].parent_id==nodes[0].id
    report=services.build_report_data(conn, first['proyecto']['id'],'monthly',date(2026,1,1))
    assert 'SUPUESTOS' in report.notes and 'hh_frecuencia' in report.notes


def test_preview_equipos_lotes_sin_archivos_y_confirmacion(conn, tmp_path):
    path=tmp_path/'lotes.xlsx'; matriz(path)
    assumptions={'equipos':[{'name':'Equipo A','regime':'5x2','cycle_start':'2026-01-01','members':2,'hours_day':8}],
                 'lotes':{'1.1':{'quantity_total':10,'batch_size':5,'unit':'unidades',
                                  'productivity_day_team':5,'team_names':['Equipo A']}}}
    draft=previsualizar(path,supuestos=assumptions)
    assert draft.resumen['ocurrencias']==6
    assert not list((tmp_path/'datos').rglob('*.json')), 'Una previsualización no debe escribir corridas del solver.'
    result=confirmar(conn,path,supuestos=assumptions,aceptar_supuestos=True,aceptar_advertencias=True)
    acts=repo.list_entities(conn,'activities',result['proyecto']['id'])
    assert len(acts)==6 and all(a.team_id and a.quantity==5 for a in acts)


def test_no_ignorar_configuracion_invalida(tmp_path):
    path=tmp_path/'fuente.xlsx'; matriz(path)
    with pytest.raises(ValueError,match='desconocid|permitid|reconocid'):
        previsualizar(path,supuestos={'lotes':{'no-existe':{'quantity_total':20}}})


def test_perfil_ruta_invalida(tmp_path):
    with pytest.raises(ValueError):
        AlmacenPerfiles(tmp_path).obtener('../afuera')


def test_rutas_opt_in_reutilizan_checklist(conn, tmp_path):
    path=tmp_path/'rutas.xlsx'; matriz(path)
    assumptions={'lotes':{'1.1':{'route_code':'RUTA1'}},
        'rutas':[{'code':'RUTA1','name':'Ruta mensual','frequency':'M','first_due':'2026-01-15',
                  'work_hours':1,'duration_days':1,'crew_size':1,'horizon_end':'2026-03-31',
                  'tol_before_days':14,'tol_after_days':13,'preferred_months':'1,2,3','location':'SOL'}]}
    draft=previsualizar(path,supuestos=assumptions)
    assert draft.resumen['ocurrencias']==3
    result=confirmar(conn,path,supuestos=assumptions,aceptar_supuestos=True,aceptar_advertencias=True)
    rows=repo.list_entities(conn,'activities',result['proyecto']['id'])
    assert all(a.code.startswith('RUTA1-') and a.checklist_codes==['OM-1.1-SOL'] for a in rows)


def test_totales_erroneos_eco_sin_inventar_partidas(tmp_path):
    ECO=next((next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file() and (p / 'tools').is_dir() and (p / 'trabajos').is_dir())/'trabajos/ganttier_ejemplos_20261005/entradas').glob('Form_Eco*.xlsx'))
    book=openpyxl.load_workbook(ECO, data_only=True)
    book['E2-MP']['H12']=999
    book['E2-MP']['I23']=999
    book['E2-MP']['H192']=999
    path=tmp_path/'eco_erroneo.xlsx'; book.save(path)
    draft=previsualizar(path,supuestos={'fecha_inicio':'2026-01-01','fecha_fin':'2028-12-31'})
    assert draft.resumen['costo_total']==12824.875 and draft.resumen['ocurrencias']==1596
    assert {'total_fila','total_detalle','subtotal_no_verificable'} <= {a['codigo'] for a in draft.advertencias}


def test_calendario_sin_feriados_coincide_con_preview(conn,tmp_path):
    path=tmp_path/'diario.xlsx'; book=matriz(path); ws=book.active
    ws['D4']=None; ws['C4']='X'; ws['H2']='Jul';ws['I2']='Ago';ws['J2']='Sept';book.save(path)
    s={'calendario':{'semana':'1111100','horas_dia':8,'pais':''}}
    draft=previsualizar(path,supuestos=s)
    result=confirmar(conn,path,supuestos=s,aceptar_supuestos=True,aceptar_advertencias=True)
    rows=repo.list_entities(conn,'activities',result['proyecto']['id'])
    assert len(rows)==draft.resumen['ocurrencias']
    assert any(a.due_date==date(2026,7,16) for a in rows)


def test_error_excel_no_es_cantidad_condicional(tmp_path):
    book=openpyxl.Workbook();ws=book.active;ws.title='E2-MP'
    ws.append(['Equipo','Actividad','Precio unitario UF','Cantidad año 1','Precio total'])
    ws.append(['Equipo A','Revisión',2,'#VALUE!','#VALUE!'])
    path=tmp_path/'error.xlsx';book.save(path)
    draft=previsualizar(path)
    assert any(w['codigo']=='error_excel' and w['nivel']=='error' for w in draft.advertencias)
