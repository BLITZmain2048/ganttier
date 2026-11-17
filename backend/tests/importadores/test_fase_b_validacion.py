"""Límites, datos incorrectos y semántica que no deben perderse al importar."""
import json
from pathlib import Path
from datetime import date
import pytest
import openpyxl
from ganttier.importadores import previsualizar, confirmar
from ganttier.importadores.caso_json import CasoJSON
from ganttier.importadores.inventario import calcular_cantidad, exportar_inventario
from ganttier import db,repo

ROOT=next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file() and (p / 'tools').is_dir() and (p / 'trabajos').is_dir())
JSON=ROOT/'trabajos/ganttier_ejemplos_20261005/entradas/caso_programacion_sin_resolver.json'
PDF=ROOT/'trabajos/ganttier_equipos_20261005/entradas/Informe_equipos_Chile_Solar.pdf'

@pytest.mark.parametrize('change',[
    lambda d:d['activities'][0].update(duration_slots=-1),
    lambda d:d['activities'][0].update(predecessors=[dict(activity_id='NO',type='FS',lag_slots=0)]),
    lambda d:d['activities'][0].update(resource_requirements={'NO':1}),
    lambda d:d['activities'][0].update(personnel_requirements={'C':0.5}),
    lambda d:d['activities'][0].update(latest_finish_slot=0),
    lambda d:d['activities'][0].update(predecessors=[dict(activity_id='A01',type='FS',lag_slots=0)]),
    lambda d:d['personnel'][0].update(unavailable_intervals=[[10,5]]),
    lambda d:d['personnel'][0].update(cost_per_slot=float('nan')),
    lambda d:d['periodic_series'][0].update(occurrence_id_format='{occurrence:9999999d}'),
])
def test_json_rechaza_datos_invalidos(tmp_path,change):
    data=json.loads(JSON.read_text(encoding='utf-8')); change(data)
    path=tmp_path/'bad.json'; path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError): previsualizar(path)

def test_json_fecha_base_ausencias_equipos_y_hito(tmp_path):
    data=json.loads(JSON.read_text(encoding='utf-8'))
    data['personnel'][0]['availability_window']=[3,55]
    data['resources'][0]['availability_window']=[2,58]
    data['activities'][-1]['latest_finish_slot']=40
    path=tmp_path/'caso.json'; path.write_text(json.dumps(data),encoding='utf-8')
    d=previsualizar(path,supuestos={'fecha_base':'2028-02-01'})
    b=CasoJSON().convertir(d)
    assert b.project['start_date']=='2028-02-01'
    assert all(c['holidays_country'] is None and c['workweek']=='1111111' for c in b.calendars)
    ana=next(r for r in b.resources if r['name']=='Ana')
    cal=next(c for c in b.calendars if c['name']==ana['calendar_name'])
    assert len(cal['exceptions'])==8
    hm=next(r for r in b.resources if r['name']=='HM')
    assert len(next(c for c in b.calendars if c['name']==hm['calendar_name'])['exceptions'])==4
    assert next(a for a in b.activities if a['code']=='A27')['window_end']=='2028-03-12'

def test_inventario_reglas_invalidas_y_redondeo_decimal():
    rows=[dict(planta='A',bloque=str(i),objeto='SCB',cantidad=10,unidad='unidades') for i in range(3)]
    assert calcular_cantidad(rows,dict(planta='A',objeto='SCB',regla='porcentaje_por_bloque',porcentaje=10))==3
    for link in [dict(regla='porcentaje_por_bloque',porcentaje=-1),dict(regla='fija',cantidad=None),dict(regla='fija',cantidad=1.2),dict(regla='error')]:
        with pytest.raises(ValueError): calcular_cantidad(rows,dict(planta='A',objeto='SCB',**link))
    with pytest.raises(ValueError,match='desglose'): calcular_cantidad([dict(rows[0],bloque='PLANTA')],dict(planta='A',objeto='SCB',regla='porcentaje_por_bloque',porcentaje=15))

def test_excel_totales_vacios_duplicados_y_formulas(tmp_path):
    path=tmp_path/'entrada.xlsx'; path.write_bytes(exportar_inventario())
    assert previsualizar(path).tiene_errores
    book=openpyxl.load_workbook(path); ws=book['Inventario']
    ws.append(['A','1','SCB',16,'unidades','equipo','fuente'])
    ws.append(['A','PLANTA','Total general',16,'unidades','control','fuente']); book.save(path)
    d=previsualizar(path)
    assert d.resumen['items']==1 and len(d.excluidos)==1
    ws.append(['A','1','SCB',16,'unidades','duplicado','fuente']); book.save(path)
    with pytest.raises(ValueError,match='duplicado'): previsualizar(path)
    ws.delete_rows(4); ws['D2']='=8+8'; book.save(path)
    with pytest.raises(ValueError,match='sin valor calculado'): previsualizar(path)

def test_pdf_no_reconocido_ofrece_plantilla(tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    path=tmp_path/'no_tablas.pdf'; canvas=Canvas(str(path)); canvas.drawString(20,700,'Documento sin inventario'); canvas.save()
    d=previsualizar(path)
    assert d.tiene_errores and 'plantilla' in d.advertencias[0]['mensaje'].lower()

def test_pdf_parametros_derivados_y_control_total():
    d=previsualizar(PDF,supuestos={'strings_por_SCB':24})
    assert len(d.excluidos)==2
    control=d.excluidos[1]
    assert control['declarado']==control['suma_sin_modulos']==[57,57,57,171]
    assert control['suma_detalle']==[28071,28071,24939,81081]
    other=previsualizar(PDF,supuestos={'strings_por_SCB':20,'hileras_por_tracker':30,'filas_revisadas':d.modelo['inventario_base']})
    assert other.resumen['inventario_por_planta']['DST']['Strings']==960
    assert other.resumen['inventario_por_planta']['SDN']['Hileras']==270
    assert all('La planta muestra' not in r['detalle'] for r in d.modelo['inventario'])
    assert sum(r['celdas'].get('Tratamiento','').startswith('TOTAL') for r in d.vista_previa)==2

def test_excel_derivados_editables(tmp_path):
    d=previsualizar(PDF,supuestos={'strings_por_SCB':24})
    path=tmp_path/'inventario.xlsx'; path.write_bytes(exportar_inventario(d.modelo['inventario']))
    other=previsualizar(path,supuestos={'strings_por_SCB':20,'hileras_por_tracker':30})
    assert other.resumen['inventario_por_planta']['DST']['Strings']==960
    assert other.resumen['inventario_por_planta']['SDN']['Hileras']==270

def test_inventario_ruta_lotes_confirmacion_y_checklist(tmp_path):
    assumptions=dict(strings_por_SCB=24,maestras=[dict(code='REV',name='Revisión SCB',frequency='A',first_due='2027-01-01',work_hours=8,crew_size=1)],
        lotes={'REV':{'route_code':'RUTA'}},
        rutas=[dict(code='RUTA',name='Ruta SCB',frequency='A',first_due='2027-01-01',work_hours=8,duration_days=1,crew_size=1,
                    batch_size=10,productivity_day_team=10,team_names=['Equipo'])],
        equipos=[dict(name='Equipo',regime='5x2',cycle_start='2027-01-01',members=1,hours_day=8)],
        vinculos=[dict(codigo='RUTA',destino='ruta',planta='DST',objeto='SCB',regla='total')])
    d=previsualizar(PDF,supuestos=assumptions)
    assert d.supuestos['rutas'][0]['quantity_total']==48 and d.resumen['ocurrencias']==5
    conn=db.connect(':memory:'); db.initialize(conn)
    try:
        with pytest.raises(ValueError,match='revisión'): confirmar(conn,PDF,supuestos=assumptions,aceptar_supuestos=True,aceptar_advertencias=True)
        assert not repo.list_entities(conn,'projects')
        result=confirmar(conn,PDF,supuestos=assumptions,aceptar_supuestos=True,aceptar_advertencias=True,aceptar_revision_inventario=True)
        acts=repo.list_entities(conn,'activities',result['proyecto']['id'])
        assert sum(a.quantity for a in acts)==48 and all(a.checklist_codes==['REV'] for a in acts)
    finally: conn.close()
