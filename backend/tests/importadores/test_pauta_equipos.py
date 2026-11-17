from pathlib import Path
from datetime import date, timedelta
import time
import openpyxl
import pytest
from ganttier.importadores.pauta_equipos import AdaptadorPauta, frecuencia, siguiente
from ganttier.importadores.modelos import Perfil

APP = Path(__file__).resolve().parents[3]
A = AdaptadorPauta()


def write(tmp_path, rows, headers=None):
    p = tmp_path/'pauta.xlsx'; b = openpyxl.Workbook(); w = b.active; w.title = 'Pauta'
    w.append(headers or ['Instalación', 'TAG', 'Tarea', 'Frecuencia'])
    for row in rows: w.append(row)
    b.save(p); b.close(); return p


def preview(p, **assumptions):
    return A.previsualizar(p, Perfil(**A.detectar(p)[0]['perfil']), assumptions)


@pytest.mark.parametrize('text,expected', [('Diaria', ('D',1)), ('DH',('DH',1)), ('Semanal',('W',1)), ('Mensual',('M',1)), ('Trimestral',('Q',1)), ('Semestral',('S',1)), ('Anual',('A',1)), ('bienal',('NA',2)), ('5 años',('NA',5)), ('cada 7 meses',('NM',7)), ('cada 3 días',('ND',3)), ('cada 2 semanas',('NW',2))])
def test_frecuencias(text, expected):
    assert frecuencia(text) == expected


@pytest.mark.parametrize('name,masters,count,equipment', [('pauta_1se_40.xlsx',80,2600,40), ('pauta_5se_800.xlsx',1600,52000,800), ('pauta_B_12x300.xlsx',7200,46800,3600)])
def test_ejemplos(name, masters, count, equipment):
    from ganttier.domain import PeriodicTask, Calendar
    from ganttier.calendars import WorkCalendar
    from ganttier.maintenance.generator import generate_occurrences
    from dataclasses import fields
    start = time.perf_counter(); d = preview(APP/'ejemplos/formatos'/name)
    print(name, d.resumen, 'preview_s', time.perf_counter()-start)
    assert not d.tiene_errores
    assert (d.resumen['maestras'],d.resumen['ocurrencias'],d.resumen['equipos']) == (masters,count,equipment)
    allowed = {f.name for f in fields(PeriodicTask)}
    tasks = [PeriodicTask(id=i, project_id=1, **{**{k:v for k,v in t.items() if k in allowed}, 'first_due':date.fromisoformat(t['first_due'])}) for i,t in enumerate(d.maestras,1)]
    cal = WorkCalendar(Calendar(id=1,name='Prueba'),date(2027,1,1),date(2027,12,31))
    assert len(generate_occurrences(tasks,date(2027,1,1),date(2027,12,31),{1:cal},1)) == count


def test_ancla_y_datos(tmp_path):
    p=write(tmp_path,[['S/E','T1','Ensayo','Mensual','31/01/2027','1,5',2,'SI',220]], ['Instalación','TAG','Tarea','Frecuencia','Última ejecución','HH','Cuadrilla','Requiere desconexión','Tensión'])
    d=preview(p); t=d.maestras[0]
    assert not d.tiene_errores
    assert t['first_due']=='2027-02-28' and t['day_of_month']==31
    assert t['work_hours']==1.5 and t['requires_outage'] and t['voltage_kv']==220
    assert d.resumen['ocurrencias']==11
    b=A.convertir(d); assert not b.has_errors and len(b.periodic_tasks)==1
    assert 'SUPUESTOS' in b.project['description']


@pytest.mark.parametrize('frequency', ['desconocida','cada 0 meses','=1+1'])
def test_error_localizado(tmp_path,frequency):
    d=preview(write(tmp_path,[['S/E','T1','Tarea',frequency]]))
    errors=[w for w in d.advertencias if w['nivel']=='error']
    assert d.tiene_errores and d.excluidos
    assert errors[0]['hoja']=='Pauta' and errors[0]['fila']==2 and errors[0]['columna'].startswith('D')
    with pytest.raises(ValueError): A.convertir(d)


def test_limite_antes_materializar(tmp_path):
    d=preview(write(tmp_path,[['S/E',f'T{i}','Tarea','Diaria'] for i in range(300)]))
    assert d.tiene_errores and any('100,000' in w['mensaje'] for w in d.advertencias)
    assert len(d.maestras)<300


def test_tope_inclusivo(tmp_path):
    end = date(2027,1,1)+timedelta(days=999)
    d=preview(write(tmp_path,[['S/E',f'T{i}','Tarea','Diaria'] for i in range(100)]),fecha_fin=end.isoformat())
    assert not d.tiene_errores and d.resumen['ocurrencias']==100000


def test_columnas_acotadas(tmp_path):
    p=write(tmp_path,[['x']*201],['x']*201)
    with pytest.raises(ValueError,match='200 columnas'):
        A.previsualizar(p,Perfil(id='p',nombre='P',formato=A.formato,hoja='Pauta'),{})


def test_epoch_y_opciones_no_soportadas(tmp_path):
    from openpyxl.utils.datetime import MAC_EPOCH, to_excel
    p=write(tmp_path,[['S/E','T1','Tarea','Mensual',to_excel(date(2027,1,31),MAC_EPOCH)]],['Instalación','TAG','Tarea','Frecuencia','Última ejecución'])
    b=openpyxl.load_workbook(p); b.epoch=MAC_EPOCH; b.save(p); b.close()
    assert preview(p).maestras[0]['first_due']=='2027-02-28'
    with pytest.raises(ValueError,match='no admite'):
        preview(p,lotes={'PA-000001':{'quantity_total':100}})


def test_perfil_y_personal(tmp_path):
    p=write(tmp_path,[['S/E','T1','Tarea','Anual']], ['Lugar','Activo','Trabajo','Ciclo'])
    b=openpyxl.load_workbook(p); w=b.create_sheet('Personal'); w.append(['Nombre','Habilidades']); w.append(['Ana','Eléctrica;Mantenimiento']); b.save(p); b.close()
    profile=Perfil(id='custom',nombre='Cliente',formato=A.formato,hoja='Pauta',mapeo={'columnas':dict(location='A',equipment='B',name='C',frequency='D')})
    d=A.previsualizar(p,profile,{})
    assert not d.tiene_errores and d.supuestos['recursos'][0]['skills']==['Eléctrica','Mantenimiento']
    assert 'personal' not in {f['clave'] for f in d.faltantes}


def test_b_hojas_acentos_y_personal_estable(tmp_path):
    p=tmp_path/'b.xlsx'; b=openpyxl.Workbook(); w=b.active; w.title='PAUTAS'
    w.append(['TAREA','FRECUENCIA','TIPO DE EQUÍPO']); w.append(['Ensayo','Anual','Interruptor'])
    w=b.create_sheet('EQUÍPOS'); w.append(['TAG','Tipo','Planta']); w.append(['T1','Interruptor','S/E'])
    w=b.create_sheet('PERSONAL'); w.append(['Nombre','Habilidades']); w.append(['Ana','Mantenimiento'])
    b.save(p); b.close()
    d=preview(p)
    assert not d.tiene_errores and d.resumen['ocurrencias']==1
    assert A.previsualizar(p,d.perfil,d.supuestos).to_dict()==d.to_dict()


def test_negativos_y_vacio(tmp_path):
    for headers in [['Planta','Bloque','Objeto/Tipo','Cantidad'],['ID','Task Name','Duration'],['Actividad','Enero','Febrero'],['Nombre','Habilidades'],['Diario','Mensual','Anual','ene','feb','mar']]:
        assert A.detectar(write(tmp_path,[],headers))==[]
    d=preview(write(tmp_path,[])); assert d.tiene_errores


def test_cinco_entradas_existentes(tmp_path):
    from ganttier.importadores.inventario import exportar_inventario
    root=next((p for p in Path(__file__).resolve().parents if (p/'AGENTS.md').is_file() and (p/'tools').is_dir() and (p/'trabajos').is_dir()),None)
    if root is None: pytest.skip('Referencias históricas no disponibles en esta copia.')
    entrada=root/'trabajos/ganttier_ejemplos_20261005/entradas'
    om=list(entrada.glob('OM-WBS_3PMG_*.xlsx')); eco=list(entrada.glob('Form_Eco_E1_E2_*.xlsx'))
    if not om or not eco: pytest.skip('Referencias OM/E2 no disponibles.')
    inventory=tmp_path/'inventario.xlsx'; inventory.write_bytes(exportar_inventario())
    paths=[om[0],eco[0],entrada/'caso_programacion_sin_resolver.json',root/'trabajos/ganttier_equipos_20261005/entradas/Informe_equipos_Chile_Solar.pdf',inventory]
    for path in paths:
        assert path.is_file()
        assert A.detectar(path)==[]


def test_encabezado_repetido(tmp_path):
    p=write(tmp_path,[],['Instalación','TAG','Tarea','Frecuencia','Frecuencia'])
    with pytest.raises(ValueError,match='repetido'):
        A.previsualizar(p,Perfil(id='p',nombre='P',formato=A.formato,hoja='Pauta'),{})


def test_combinadas_y_revision_estable(tmp_path):
    p=write(tmp_path,[['S/E','T1','Tarea','Mensual']])
    d=preview(p)
    assert A.previsualizar(p,d.perfil,d.supuestos).to_dict()==d.to_dict()
    b=openpyxl.load_workbook(p); b.active.merge_cells('A2:B2'); b.save(p); b.close()
    d=preview(p)
    assert any(w['codigo']=='celdas_combinadas' for w in d.advertencias)


def test_20000_medicion(tmp_path):
    p=write(tmp_path,[['S/E',f'T{i}','Tarea','Anual'] for i in range(20000)])
    profile=Perfil(id='p',nombre='P',formato=A.formato,hoja='Pauta')
    start=time.perf_counter(); d=A.previsualizar(p,profile,{})
    elapsed=time.perf_counter()-start
    print('20000 filas preview_s',elapsed)
    assert not d.tiene_errores and d.resumen['ocurrencias']==20000


def test_api_registro_explicito(tmp_path,monkeypatch):
    # La copia de ronda carece del registro opcional. No se parchean archivos.
    from ganttier import services
    from ganttier.importadores import servicio
    from ganttier.main import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setitem(servicio.LECTORES,A.formato,A)
    monkeypatch.setenv('GANTTIER_DB',str(tmp_path/'api.db'))
    monkeypatch.setattr(services,'DATA_DIR',tmp_path/'datos')
    p=write(tmp_path,[['S/E','T1','Tarea','Mensual']])
    with TestClient(create_app()) as client:
        r=client.post('/api/importadores/archivos',files={'file':(p.name,p.read_bytes())}); assert r.status_code==201,r.text
        ident=r.json()['id']
        r=client.post(f'/api/importadores/borradores/{ident}',json={}); assert r.status_code==200,r.text
        d=r.json(); assert d['resumen']['ocurrencias']==12
        body=dict(revision=d['revision'],aceptar_supuestos=True,aceptar_advertencias=True)
        r=client.post(f'/api/importadores/confirmar/{ident}',json={**body,'aceptar_supuestos':False}); assert r.status_code==422
        r=client.post(f'/api/importadores/confirmar/{ident}',json=body); assert r.status_code==201,r.text
