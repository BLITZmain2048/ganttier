"""Flujo HTTP completo: carga, revisión vinculante, confirmación y perfiles."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from ganttier import db, services
from ganttier.main import create_app

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file() and (p / 'tools').is_dir() and (p / 'trabajos').is_dir())
SOURCE = next((ROOT/'trabajos/ganttier_ejemplos_20261005/entradas').glob('Form_Eco*.xlsx'))

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path/'api.db'))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path/'datos')
    with TestClient(create_app()) as c:
        yield c

def test_api_completa_y_confirmacion_idempotente(client):
    response = client.post('/api/importadores/archivos', files={'file': (SOURCE.name, SOURCE.read_bytes())})
    assert response.status_code == 201, response.text
    upload = response.json(); ident = upload['id']
    assert upload['detecciones'][0]['formato'] == 'e2_mp'
    request = {'perfil': upload['detecciones'][0]['perfil'],
               'supuestos': {'fecha_inicio': '2026-01-01', 'fecha_fin': '2028-12-31'}}
    response = client.post(f'/api/importadores/borradores/{ident}', json=request)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft['resumen']['ocurrencias'] == 1596
    assert draft['faltantes']
    body = {'revision': draft['revision'], 'nombre': 'Eco asistido', 'aceptar_supuestos': True, 'aceptar_advertencias': True}
    rejected = client.post(f'/api/importadores/confirmar/{ident}', json={**body, 'aceptar_supuestos': False})
    assert rejected.status_code == 422
    response = client.post(f'/api/importadores/confirmar/{ident}', json=body)
    assert response.status_code == 201, response.text
    result = response.json(); pid = result['proyecto']['id']
    assert 'SUPUESTOS' in result['proyecto']['description']
    repeated = client.post(f'/api/importadores/confirmar/{ident}', json=body)
    assert repeated.status_code == 201 and repeated.json()['proyecto']['id'] == pid
    assert client.get(f'/api/projects/{pid}/export.xlsx').status_code == 200
    metadata = client.get(f'/api/projects/{pid}/importacion-asistida').json()
    assert metadata['fuente']['sha256'] and metadata['maestras'][0]['origen']['fila']

def test_revision_y_perfiles(client):
    upload = client.post('/api/importadores/archivos', files={'file': (SOURCE.name, SOURCE.read_bytes())}).json()
    ident = upload['id']; profile = upload['detecciones'][0]['perfil']
    saved = client.post('/api/importadores/perfiles', json={**profile, 'id': 'cliente', 'nombre': 'Cliente'} )
    assert saved.status_code == 201, saved.text
    v1 = saved.json()
    assert client.post('/api/importadores/perfiles', json=v1).json()['version'] == 2
    assert client.post('/api/importadores/perfiles', json=v1).status_code == 422
    assert any(p['id']=='cliente' for p in client.get('/api/importadores/perfiles').json())
    assert client.delete('/api/importadores/perfiles/cliente').status_code == 204
    assert client.delete('/api/importadores/perfiles/matriz-om-wbs').status_code == 422
    assert client.post(f'/api/importadores/confirmar/{ident}', json={'revision': 'invalida', 'aceptar_supuestos': True}).status_code == 409
    assert client.delete(f'/api/importadores/archivos/{ident}').status_code == 204
    assert client.post(f'/api/importadores/borradores/{ident}', json={}).status_code == 404


def test_revision_obsoleta_y_archivo_alterado(client):
    upload=client.post('/api/importadores/archivos',files={'file':(SOURCE.name,SOURCE.read_bytes())}).json()
    ident=upload['id']
    before=client.post(f'/api/importadores/borradores/{ident}',json={}).json()
    after=client.post(f'/api/importadores/borradores/{ident}',json={'supuestos':{'dotacion':2}}).json()
    assert before['revision']!=after['revision']
    def commit(revision):
        return client.post(f'/api/importadores/confirmar/{ident}',json={'revision':revision,'aceptar_supuestos':True,'aceptar_advertencias':True})
    assert commit(before['revision']).status_code==409
    path=services.DATA_DIR/'importaciones_asistidas'/f'{ident}.xlsx'
    path.write_bytes(path.read_bytes()+b'alterado')
    assert commit(after['revision']).status_code==409
    assert client.get('/api/projects').json()==[]


def test_mapeo_invalido_no_es_error_interno(client):
    upload=client.post('/api/importadores/archivos',files={'file':(SOURCE.name,SOURCE.read_bytes())}).json()
    profile=upload['detecciones'][0]['perfil']; profile['mapeo']['cantidades']=True
    response=client.post(f"/api/importadores/borradores/{upload['id']}",json={'perfil':profile})
    assert response.status_code==422 and 'Mapeo' in response.json()['detail']
