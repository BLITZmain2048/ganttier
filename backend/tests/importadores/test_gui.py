"""Recorrido Qt offscreen con la API real; sin diálogos nativos de archivos."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
import openpyxl
from PySide6.QtWidgets import QApplication
from fastapi.testclient import TestClient
from ganttier.gui.importador_asistido import ImportadorAsistido
from ganttier.gui.client import ApiClient
from ganttier.main import create_app
from ganttier import services


def test_asistente_recorrido_completo(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path/'gui.db'))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path/'datos')
    def sync(work, done, fail=None, **kwargs):
        try: result = work()
        except Exception as exc:
            if fail: fail(exc)
            else: raise
        else: done(result)
    monkeypatch.setattr('ganttier.gui.importador_asistido.run_task', sync)
    book = openpyxl.Workbook(); ws = book.active; ws.title = 'OM-WBS'
    ws.append(['Proyectos', 'Planta [SOL]', None, None, 2026])
    ws.append(['Código', 'Descripción', 'Diario', 'Mensual', 'Trimestral', 'Semestral', 'Anual', 'Ene', 'Feb', 'Mar'])
    ws.append(['1', 'Revisión', None, 'X', None, None, None, 'SOL', 'SOL', 'SOL'])
    path = tmp_path/'cliente.xlsx'; book.save(path)
    with TestClient(create_app()) as http:
        api = ApiClient('http://testserver'); api._http.close(); api._http = http
        wizard = ImportadorAsistido(api)
        wizard.set_source(path); wizard.avanzar()
        assert wizard.paso == 1, wizard.estado.text()
        assert wizard.mapeo.rowCount() > 0 and wizard.vista.rowCount() > 0
        wizard.avanzar()
        assert wizard.paso == 2, wizard.estado.text()
        assert wizard.faltantes.rowCount() > 5
        wizard.avanzar()
        assert wizard.paso == 3, wizard.estado.text()
        assert not wizard.siguiente.isEnabled()
        wizard.acepta_supuestos.setChecked(True); wizard.acepta_advertencias.setChecked(True)
        wizard.avanzar()
        assert wizard.resultado and wizard.resultado['proyecto']['id'] > 0, wizard.estado.text()
        assert wizard.resultado['resumen']['ocurrencias'] == 3
        wizard.close(); app.processEvents()
