"""Asistente Qt con API real: revisión PDF obligatoria, JSON y descarga Excel."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QComboBox, QDoubleSpinBox, QDialogButtonBox, QTableWidget, QLabel
from fastapi.testclient import TestClient
from ganttier.main import create_app
from ganttier import services
from ganttier.gui.client import ApiClient
from ganttier.gui.importador_asistido import ImportadorAsistido

ROOT=next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file() and (p / 'tools').is_dir() and (p / 'trabajos').is_dir())

@pytest.mark.parametrize('relative,fmt',[
    ('trabajos/ganttier_ejemplos_20261005/entradas/caso_programacion_sin_resolver.json','caso_json'),
    ('trabajos/ganttier_equipos_20261005/entradas/Informe_equipos_Chile_Solar.pdf','inventario_equipos')])
def test_gui_fase_b(tmp_path,monkeypatch,relative,fmt):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setenv('GANTTIER_DB',str(tmp_path/'api.db'))
    monkeypatch.setattr(services,'DATA_DIR',tmp_path/'datos')
    def sync(work,done,fail=None,**kw):
        try: value=work()
        except Exception as exc:
            if fail: fail(exc)
            else: raise
        else: done(value)
    monkeypatch.setattr('ganttier.gui.importador_asistido.run_task',sync)
    with TestClient(create_app()) as http:
        api=ApiClient('http://testserver'); api._http.close(); api._http=http
        w=ImportadorAsistido(api); w.set_source(ROOT/relative); w.avanzar()
        assert w.paso==1 and w.borrador['perfil']['formato']==fmt,w.estado.text()
        w.avanzar(); assert w.paso==2,w.estado.text()
        if fmt=='inventario_equipos':
            assert w.revisar_inventario_btn.isEnabled()
            assert w.vincular_inventario_btn.isEnabled()
            output=tmp_path/'Plantilla.xlsx'
            monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(output),'Excel'))
            w.descargar_plantilla_inventario()
            assert output.read_bytes().startswith(b'PK')
            w._set_supuesto('strings_por_SCB',24)
            w._set_supuesto('maestras',[dict(code='REV',name='Muestra',frequency='A',first_due='2027-01-01',work_hours=8,crew_size=1)])
            w._set_supuesto('equipos',[dict(name='Equipo',regime='5x2',cycle_start='2027-01-01',members=1,hours_day=8)])
            w._set_supuesto('lotes',{'REV':dict(batch_size=500,productivity_day_team=500,team_names=['Equipo'])})
            edited=[]
            def edit_link():
                dialog=app.activeModalWidget()
                try:
                    dialog.findChild(QComboBox,'vinculo_planta').setCurrentText('DST')
                    dialog.findChild(QComboBox,'vinculo_objeto').setCurrentText('Modulos')
                    rule=dialog.findChild(QComboBox,'vinculo_regla'); rule.setCurrentIndex(rule.findData('porcentaje_por_bloque'))
                    dialog.findChild(QDoubleSpinBox,'vinculo_porcentaje').setValue(15)
                    edited.append(True)
                    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
                finally: dialog.reject()
            QTimer.singleShot(0,edit_link); w.vincular_inventario()
            assert edited and w._leer_tabla(w.faltantes,w._missing_types)['vinculos'][0]['porcentaje']==15
            def review_rows():
                dialog=app.activeModalWidget()
                try:
                    table=dialog.findChild(QTableWidget)
                    assert table.rowCount()>100
                    table.item(0,5).setText('Detalle revisado en la GUI')
                    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
                finally: dialog.reject()
            QTimer.singleShot(0,review_rows); w.revisar_inventario()
            assert w._leer_tabla(w.faltantes,w._missing_types)['filas_revisadas'][0]['detalle']=='Detalle revisado en la GUI'
            refused=[]
            def clear_inventory():
                dialog=app.activeModalWidget()
                try:
                    dialog.findChild(QTableWidget).setRowCount(0)
                    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
                    refused.append(any('no puede quedar vacío' in label.text() for label in dialog.findChildren(QLabel)))
                finally: dialog.reject()
            QTimer.singleShot(0,clear_inventory); w.revisar_inventario()
            assert refused==[True]
            assert len(w._leer_tabla(w.faltantes,w._missing_types)['filas_revisadas'])>100
        w.avanzar(); assert w.paso==3,w.estado.text()
        if fmt=='inventario_equipos': assert w.borrador['supuestos']['lotes']['REV']['quantity_total']==4203
        w.acepta_supuestos.setChecked(True); w.acepta_advertencias.setChecked(True)
        if fmt=='inventario_equipos':
            assert not w.siguiente.isEnabled()
            w.acepta_inventario.setChecked(True)
        assert w.siguiente.isEnabled()
        w.avanzar(); assert w.resultado,w.estado.text()
        w.close(); app.processEvents()
