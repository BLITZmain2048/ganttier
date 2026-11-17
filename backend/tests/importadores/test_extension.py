"""Un lector futuro de proyecto JSON se conecta sin cambiar API ni GUI."""
from pathlib import Path
import json
from fastapi.testclient import TestClient
from ganttier import services, domain
from ganttier.main import create_app
from ganttier.importadores import registrar, Perfil, Borrador
from ganttier.importadores.servicio import LECTORES


def test_extension_proyecto_json(tmp_path, monkeypatch):
    class LectorProyectoPrueba:
        formato='proyecto_prueba'
        extensiones={'.json'}
        def detectar(self,path):
            profile=Perfil(id='proyecto-prueba',nombre='Proyecto de prueba',formato=self.formato,mapeo={'nombre':'nombre'})
            return [{'formato':self.formato,'puntaje':1,'motivos':['Contrato de prueba'],'perfil':profile.model_dump()}]
        def previsualizar(self,path,perfil,supuestos):
            import hashlib
            data=json.loads(path.read_text(encoding='utf-8'))
            return Borrador(perfil=perfil,fuente={'nombre':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
                maestras=[],sitios=[],wbs=[],advertencias=[],faltantes=[],supuestos={},resumen={'items':1},
                modo='proyecto',modelo={'nombre':data['nombre']})
        def convertir(self,draft,nombre):
            return domain.ImportBundle(mode='proyecto',project={'name':nombre or draft.modelo['nombre'],'start_date':'2026-01-01',
                                      'description':'Proyecto del lector de prueba'})
    monkeypatch.setattr(services,'DATA_DIR',tmp_path/'datos')
    monkeypatch.setenv('GANTTIER_DB',str(tmp_path/'test.db'))
    registrar(LectorProyectoPrueba())
    try:
        with TestClient(create_app()) as client:
            uploaded=client.post('/api/importadores/archivos',files={'file':('proyecto.json',b'{"nombre":"Proyecto B"}')})
            assert uploaded.status_code==201,uploaded.text
            ident=uploaded.json()['id']
            preview=client.post(f'/api/importadores/borradores/{ident}',json={})
            assert preview.status_code==200,preview.text
            assert preview.json()['modo']=='proyecto' and preview.json()['modelo']['nombre']=='Proyecto B'
            created=client.post(f'/api/importadores/confirmar/{ident}',json={'revision':preview.json()['revision'],'aceptar_supuestos':True})
            assert created.status_code==201,created.text
            assert created.json()['proyecto']['mode']=='proyecto'
    finally:LECTORES.pop('proyecto_prueba')
