"""Aceptación de los perfiles reales de fase B, sin datos precalculados en el lector."""
import copy
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from ganttier import services
from ganttier.main import create_app
from ganttier.importadores import detectar, previsualizar

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file() and (p / 'tools').is_dir() and (p / 'trabajos').is_dir())
JSON = ROOT/'trabajos/ganttier_ejemplos_20261005/entradas/caso_programacion_sin_resolver.json'
PDF = ROOT/'trabajos/ganttier_equipos_20261005/entradas/Informe_equipos_Chile_Solar.pdf'

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path/'api.db'))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path/'datos')
    with TestClient(create_app()) as c: yield c

def ok(response):
    assert response.status_code in (200,201),response.text
    return response.json()

def upload(client, path):
    return ok(client.post('/api/importadores/archivos',files={'file':(path.name,path.read_bytes())}))['id']

def test_json_deteccion_borrador_faltantes():
    assert detectar(JSON)[0]['formato']=='caso_json'
    d=previsualizar(JSON)
    assert d.modo=='proyecto' and d.resumen['actividades']==51
    assert d.resumen['conteos_expandidos']==dict(one_off_work_activities=26,milestones=1,periodic_occurrences=24,total_activities_including_milestones=51)
    assert d.supuestos['role_mode']=='indivisible'
    assert {'fecha_base','equivalencia_bloque_dia'} <= {x['clave'] for x in d.faltantes if x['estado']=='SUPUESTO'}
    assert not d.tiene_errores

def test_json_conteos_invalidos_y_formato_ocurrencia(tmp_path):
    data=json.loads(JSON.read_text(encoding='utf-8'))
    data['expected_expanded_counts']['periodic_occurrences']=25
    path=tmp_path/'caso.json'; path.write_text(json.dumps(data),encoding='utf-8')
    assert previsualizar(path).tiene_errores
    data['expected_expanded_counts']['periodic_occurrences']=24
    data['periodic_series'][1]['occurrence_id_format']='{series_id}-ciclo-{occurrence:03d}'
    path.write_text(json.dumps(data),encoding='utf-8')
    d=previsualizar(path)
    assert any(r['pred_code']=='P02-ciclo-001' for r in d.modelo['relaciones'])

def test_json_api_aceptacion(client):
    ident=upload(client,JSON)
    d=ok(client.post(f'/api/importadores/borradores/{ident}',json={}))
    req=dict(revision=d['revision'],aceptar_supuestos=True,aceptar_advertencias=True)
    pid=ok(client.post(f'/api/importadores/confirmar/{ident}',json=req))['proyecto']['id']
    body=json.loads((JSON.parent.parent/'proyecto/salida_parche/solicitud_api.json').read_text())
    result=ok(client.post(f'/api/projects/{pid}/schedule/solve',json=body))
    assert result['feasible'] and result['applied'] and result['violations']==[]
    assert result['primary_finish_block']==36 and result['personnel_cost']==10420
    import sys
    monkey_path=str(ROOT/'Ganttier/backend/tests/solver')
    sys.path.insert(0,monkey_path)
    try:
        from role_case import audit_original
        audit=audit_original(client,pid,json.loads(JSON.read_text(encoding='utf-8')),result)
    finally: sys.path.remove(monkey_path)
    assert audit['errors']==[] and audit['A27']==36 and audit['personnel_cost']==10420

def test_inventario_pdf_requiere_revision_y_cantidades(client):
    from ganttier.importadores.inventario import calcular_cantidad
    assert detectar(PDF)[0]['formato']=='inventario_equipos'
    ident=upload(client,PDF)
    d=ok(client.post(f'/api/importadores/borradores/{ident}',json={'supuestos':{'strings_por_SCB':24}}))
    assert d['modelo']['requiere_revision']
    assert any('excluye módulos' in a['mensaje'] for a in d['advertencias'])
    body=dict(revision=d['revision'],aceptar_supuestos=True,aceptar_advertencias=True)
    assert client.post(f'/api/importadores/confirmar/{ident}',json=body).status_code==422
    rows=d['modelo']['inventario']
    expected={'Modulos':[28014,28014,24882],'Strings':[1152]*3,'Hileras':[324,324,243],'SCB':[48]*3,'Trackers':[12,12,9],'SKID':[3]*3,'Meteo Station':[3,0,0]}
    for obj, totals in expected.items():
        for plant,total in zip(['DST','AND','SDN'],totals):
            assert calcular_cantidad(rows,dict(planta=plant,objeto=obj,regla='total'))==total
    for obj,totals in {'Modulos':[4203,4203,3735],'Strings':[174]*3,'Hileras':[51,51,39]}.items():
        for plant,total in zip(['DST','AND','SDN'],totals):
            assert calcular_cantidad(rows,dict(planta=plant,objeto=obj,regla='porcentaje_por_bloque',porcentaje=15))==total
    body['aceptar_revision_inventario']=True
    assert client.post(f'/api/importadores/confirmar/{ident}',json=body).status_code==201

def test_inventario_excel_ida_vuelta_y_plantilla(client,tmp_path):
    from ganttier.importadores.inventario import exportar_inventario
    d=previsualizar(PDF,supuestos={'strings_por_SCB':24})
    path=tmp_path/'inventario.xlsx'; path.write_bytes(exportar_inventario(d.modelo['inventario']))
    assert detectar(path)[0]['formato']=='inventario_equipos'
    other=previsualizar(path)
    keys=('planta','bloque','objeto','cantidad','unidad','detalle','fuente')
    assert sorted(tuple(str(r[k]) for k in keys) for r in d.modelo['inventario'])==sorted(tuple(str(r[k]) for k in keys) for r in other.modelo['inventario'])
    r=client.get('/api/importadores/plantillas/inventario.xlsx')
    assert r.status_code==200 and r.content.startswith(b'PK')

def test_inventario_vinculos_alimentan_lotes(tmp_path):
    d=previsualizar(PDF,supuestos={'strings_por_SCB':24,'vinculos':[dict(codigo='REV',planta='DST',objeto='Modulos',regla='porcentaje_por_bloque',porcentaje=15)],
        'maestras':[dict(code='REV',name='Revisión módulos',frequency='A',duration_days=1,work_hours=8,crew_size=1,first_due='2027-01-01')],
        'lotes':{'REV':dict(batch_size=100,productivity_day_team=100,team_names=['Equipo'])},
        'equipos':[dict(name='Equipo',regime='5x2',cycle_start='2027-01-01',members=1,hours_day=8)]})
    assert d.supuestos['lotes']['REV']['quantity_total']==4203
    assert d.resumen['ocurrencias']>0

def test_cambio_modelo_sin_cambio_conteos_invalida_revision(client,monkeypatch):
    from ganttier.importadores.caso_json import CasoJSON
    ident=upload(client,JSON)
    draft=ok(client.post(f'/api/importadores/borradores/{ident}',json={}))
    original=CasoJSON.previsualizar
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        result.modelo['actividades'][0]['name']='Lectura alterada después de la revisión'
        return result
    monkeypatch.setattr(CasoJSON,'previsualizar',changed)
    response=client.post(f'/api/importadores/confirmar/{ident}',json=dict(revision=draft['revision'],aceptar_supuestos=True,aceptar_advertencias=True))
    assert response.status_code==409,response.text
    assert client.get('/api/projects').json()==[]
