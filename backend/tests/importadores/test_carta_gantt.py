from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import time
import pytest
from openpyxl import Workbook, load_workbook
from ganttier.importadores.carta_gantt import AdaptadorCartaGantt, periodo
from ganttier.importadores.modelos import Perfil

APP=Path(__file__).resolve().parents[3]
A=AdaptadorCartaGantt()
spec=importlib.util.spec_from_file_location('generar_carta',APP/'ejemplos/generar_formato_carta_gantt.py')
gen=importlib.util.module_from_spec(spec); spec.loader.exec_module(gen)


def draft(path, **supuestos):
    profile=Perfil.model_validate(A.detectar(path)[0]['perfil'])
    return A.previsualizar(path,profile,supuestos)


@pytest.mark.parametrize('count,weekly,sites,color,expected',[(60,False,1,True,60),(200,True,1,False,200),(30,False,3,False,120)])
def test_ejemplos(tmp_path,count,weekly,sites,color,expected):
    p=tmp_path/'carta.xlsx'; gen.generar(p,count,weekly,sites,color)
    d=draft(p,interpretacion='periodica' if sites==3 else 'ventana_macro')
    assert not d.tiene_errores
    assert d.resumen['actividades']==expected
    assert not A.convertir(d).has_errors
    assert len(d.fuente['sha256'])==64


def test_discontinuas_color_formula_numeros(tmp_path):
    p=tmp_path/'carta.xlsx'; gen.generar(p,1,color=True)
    b=load_workbook(p); w=b['Carta']; w['K4']='✓'; w['E4']='1.234,5'; b.save(p); b.close()
    d=draft(p); assert len(d.maestras)==2 and d.maestras[0]['work_hours']==1234.5
    assert len(draft(p,discontinuas='envolvente').maestras)==1
    assert len(draft(p,colores=['4472C5'],tolerancia_color=1).maestras)==2
    b=load_workbook(p); b['Carta']['E4']='=2+2'; b.save(p); b.close()
    d=draft(p); assert d.tiene_errores
    assert any(a.get('columna')=='E' and a['fila']==4 and a['hoja']=='Carta' for a in d.advertencias)


def test_negativos(tmp_path):
    for headers in [['Código','Actividad','Diario','Mensual','Anual','Enero','Febrero','Marzo'],
                    ['Activity ID','Activity Name','Start','Finish'],['Equipo','Tarea','Frecuencia','HH'],
                    ['Planta','Bloque','Objeto/Tipo','Cantidad'],['Ítem','Descripción','Cantidad','Precio'],['Nombre']]:
        p=tmp_path/'otro.xlsx'; b=Workbook(); b.active.append(headers); b.active.append(['1','A']); b.save(p); b.close()
        assert A.detectar(p)==[]
    b=Workbook(); w=b.active
    w.append(['Equipo','Actividad','Precio unitario','Cantidad 1','Precio total'])
    w.append(['Equipo A','Inspección','S1','S2'])
    b.save(p); b.close()
    assert A.detectar(p)==[]


def test_fechas_perfil_y_jerarquia(tmp_path):
    assert periodo('ene-27',2026)[0].isoformat()=='2027-01-01'
    assert periodo('Enero 2027',2026)[0].year==2027
    assert periodo('04/01/2027',2026)[1].isoformat()=='2027-01-10'
    p=tmp_path/'carta.xlsx'; gen.generar(p,2)
    b=load_workbook(p); w=b['Carta']; w['A4']='1'; w['A5']='1.1'; w['E4']=-1; b.save(p); b.close()
    d=draft(p); assert d.tiene_errores and d.wbs[1]['parent_code']=='1'


@pytest.mark.parametrize('periodic',[False,True])
def test_api_cli(tmp_path,monkeypatch,periodic):
    from fastapi.testclient import TestClient
    from ganttier.main import create_app
    from ganttier import services
    from ganttier.importadores import servicio
    monkeypatch.setitem(servicio.LECTORES, A.formato, A)
    p=tmp_path/'carta.xlsx'; gen.generar(p,3)
    database=tmp_path/'api.db'
    monkeypatch.setenv('GANTTIER_DB',str(database)); monkeypatch.setenv('GANTTIER_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setattr(services,'DATA_DIR',tmp_path/'data')
    with TestClient(create_app()) as c:
        r=c.post('/api/importadores/archivos',files={'file':('carta.xlsx',p.read_bytes())}); assert r.status_code==201,r.text
        u=r.json(); assert u['detecciones'][0]['formato']=='carta_gantt'
        r=c.post('/api/importadores/borradores/'+u['id'],json={'supuestos':{'interpretacion':'periodica' if periodic else 'ventana_macro'}}); assert r.status_code==200,r.text
        d=r.json()
        r=c.post('/api/importadores/confirmar/'+u['id'],json={'revision':d['revision'],'aceptar_supuestos':True,'aceptar_advertencias':True}); assert r.status_code==201,r.text
        pid=r.json()['proyecto']['id']
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',GANTTIER_CLI_ESCRITURA='0',TEMP=str(tmp_path),TMP=str(tmp_path))
    base=[sys.executable,'-B','-m','ganttier.cli','--db',str(database),'--data-dir',str(tmp_path/'data'),'-p',str(pid)]
    for args in [['describir','--comando','programa cpm'],['programa','cpm']]:
        run=subprocess.run(base+args,cwd=APP/'backend',env=env,capture_output=True,text=True,encoding='utf-8')
        assert run.returncode==0,(run.stdout,run.stderr)
        assert json.loads(run.stdout)['ok']
    # CPM es lectura: verificar informa legítimamente tareas sin programación en proyecto.
    run=subprocess.run(base+['programa','verificar'],cwd=APP/'backend',env=env,capture_output=True,text=True,encoding='utf-8')
    assert run.returncode in (0,2),(run.stdout,run.stderr)
    assert json.loads(run.stdout)['ok']
    print('CLI',periodic,'verificar exit',run.returncode,run.stdout)


def test_rendimiento_20000(tmp_path):
    p=tmp_path/'grande.xlsx'; gen.generar(p,20000)
    profile=Perfil.model_validate(A.detectar(p)[0]['perfil'])
    start=time.perf_counter(); d=A.previsualizar(p,profile,{})
    elapsed=time.perf_counter()-start
    assert len(d.maestras)==20000
    print(f'PREVISUALIZACION_20000={elapsed:.3f}s')


def test_multinivel_limites_y_ocurrencias(tmp_path):
    from ganttier import db, services
    from ganttier.importadores.servicio import _guardar_modelo
    p=tmp_path/'carta.xlsx'; gen.generar(p,30,sitios=3)
    b=load_workbook(p); w=b['Carta']; w['B2']='Actividad'; w['B3']=None; w.merge_cells('B2:B3'); b.save(p); b.close()
    d=draft(p,interpretacion='periodica')
    conn=db.connect(':memory:')
    try:
        db.initialize(conn)
        project=services.create_project_from_bundle(conn,A.convertir(d),_guardar_modelo)
        assert conn.execute('select count(*) from activities where project_id=?',(project.id,)).fetchone()[0]==120
    finally: conn.close()
    b=load_workbook(p); b['Carta']['GS1']='fuera de límite'; b.save(p); b.close()
    with pytest.raises(ValueError,match='200 columnas'): A.detectar(p)


def test_vacio_repetidos_y_perfil(tmp_path):
    p=tmp_path/'carta.xlsx'; b=Workbook(); b.save(p); b.close(); assert not A.detectar(p)
    gen.generar(p,3)
    b=load_workbook(p); w=b['Carta']; w['B5']='Actividad'; w['I5']='X'; w['J3']='Mes 1'; b.save(p); b.close()
    with pytest.raises(ValueError,match='repetidos'): draft(p)
    b=load_workbook(p); b['Carta']['J3']='Mes 2'; b.save(p); b.close()
    d=draft(p); assert any(e['motivo']=='Encabezado repetido' for e in d.excluidos)
    profile=d.perfil.model_copy(deep=True); profile.id='personalizado'; profile.supuestos={'hh':12,'anio':2027}
    assert A.previsualizar(p,profile,{}).perfil.id=='personalizado'
