"""UX3: conversión, edición y asistente contra API embebida real (Qt offscreen)."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
from PySide6.QtCore import QDate, QObject, QThread, QThreadPool, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QWidget, QWizard

from ganttier.gui.widgets.common import Field
from ganttier.gui.widgets.paste import (
    EditableTableModel, PasteDialog, convert_cell, parse_date, parse_month,
    parse_number, parse_predecessors, parse_table, paste_from_clipboard,
)
from ganttier.gui.wizard import build_wizard, create_schedule, task_fields, validate_tasks


def test_parse_table_quotes_crlf_and_embedded_newlines():
    text = 'Codigo\tNombre\tHH\r\nA\t"Revisión ""principal""\r\nsegunda línea"\t"1.234,5"\r\nB\tNormal\t1234.5\r\n\r\n'
    assert parse_table(text) == [["Codigo", "Nombre", "HH"],
                               ["A", 'Revisión "principal"\r\nsegunda línea', "1.234,5"],
                               ["B", "Normal", "1234.5"]]
    assert parse_table("") == []
    assert parse_table("\ufeffA\tB\n\t\n") == [["A", "B"]]


@pytest.mark.parametrize("raw,expected", [("1.234,5", 1234.5), ("1234.5", 1234.5), ("12,25", 12.25), ("0", 0)])
def test_decimal_numbers(raw, expected):
    assert parse_number(raw) == expected
    assert convert_cell(raw, Field("work_hours", "HH", "float")) == expected


@pytest.mark.parametrize("raw", ["nan", "inf", "1,2,3", "abc", "1.2.3"])
def test_invalid_numbers(raw):
    with pytest.raises(ValueError):
        parse_number(raw)


def test_dates_months_and_noninteger_duration():
    assert {parse_date(x) for x in ("23-03-2027", "2027-03-23", "23/03/2027")} == {"2027-03-23"}
    assert parse_month("mar-2027") == parse_month("2027-03") == "2027-03"
    for raw in ("31-02-2027", "nunca"):
        with pytest.raises(ValueError):
            parse_date(raw)
    with pytest.raises(ValueError):
        parse_month("2027-13")
    for raw in ("1,5", "-1"):
        with pytest.raises(ValueError):
            convert_cell(raw, Field("duration_days", "Duración", "int"))


def test_parse_predecessors_types_lags_and_hyphenated_codes():
    assert parse_predecessors("A; B+2; C SS-1; D FF; E FS+3") == [
        dict(code="A", type="FS", lag_days=0), dict(code="B", type="FS", lag_days=2),
        dict(code="C", type="SS", lag_days=-1), dict(code="D", type="FF", lag_days=0),
        dict(code="E", type="FS", lag_days=3)]
    assert parse_predecessors("A-01, B SF-2, C -3") == [
        dict(code="A-01", type="FS", lag_days=0), dict(code="B", type="SF", lag_days=-2),
        dict(code="C", type="FS", lag_days=-3)]
    assert parse_predecessors(" ; , ") == []
    with pytest.raises(ValueError):
        parse_predecessors("A XX+2")


def test_paste_synonyms_mapping_errors_and_correction(qtbot):
    fields = task_fields("proyecto")
    rows = parse_table("Descripción\tID\tDURACIÓN_DÍAS\tHH_TOTALES\tHabilidad requerida\tMES_MACRO\nMotor\tA\t1,5\t1.234,5\tElectricidad\tmar-2027\n")
    dialog = PasteDialog(None, "Pegar", fields, rows)
    qtbot.addWidget(dialog)
    assert dialog.header_check.isChecked()
    assert [c.currentData() for c in dialog.mapping_combos] == ["name", "code", "duration_days", "work_hours", "required_skill", "macro_month"]
    assert not dialog.accept_button.isEnabled()
    assert dialog.summary.text() == "1 filas · 1 con errores"
    index = dialog.model.index(0, 2)
    assert "entero" in dialog.model.data(index, Qt.ToolTipRole)
    assert dialog.model.data(index, Qt.BackgroundRole) is not None
    dialog.model.setData(index, "2")
    assert dialog.accept_button.isEnabled()
    assert dialog.values() == [dict(code="A", name="Motor", duration_days=2, work_hours=1234.5,
                                    required_skill="Electricidad", macro_month="2027-03")]


def test_paste_order_remapping_retains_edits_and_required_mapping(qtbot):
    fields = [Field("code", "Código"), Field("name", "Nombre"), Field("duration_days", "Duración", "int")]
    dialog = PasteDialog(None, "Pegar", fields, [["A", "Revisión", "3"]])
    qtbot.addWidget(dialog)
    assert not dialog.header_check.isChecked()
    dialog.model.setData(dialog.model.index(0, 1), "Corregida")
    combo = dialog.mapping_combos[2]
    combo.setCurrentIndex(0)
    assert not dialog.accept_button.isEnabled()
    assert "Duración" in dialog.mapping_error.text()
    combo.setCurrentIndex(combo.findData("duration_days"))
    assert dialog.values()[0]["name"] == "Corregida"
    dialog.mapping_combos[1].setCurrentIndex(dialog.mapping_combos[1].findData("code"))
    assert not dialog.accept_button.isEnabled()
    assert "una sola columna" in dialog.mapping_error.text()


def test_clipboard_wrapper(qtbot, monkeypatch):
    QApplication.clipboard().setText("Codigo\tNombre\nA\tDesde Excel\n")
    monkeypatch.setattr(PasteDialog, "exec", lambda self: QDialog.Accepted)
    assert paste_from_clipboard(None, "Pegar", [Field("code", "Código"), Field("name", "Nombre")]) == [{"code": "A", "name": "Desde Excel"}]


def test_live_validation_duplicates_missing_cycles_skills(qtbot):
    wizard = build_wizard(SimpleNamespace(calendars=[], resources=[]))
    qtbot.addWidget(wizard)
    model = wizard.tasks_page.model
    model.set_rows([dict(code="A", name="A", duration_days=1, predecessors="B", required_skill="Sin recurso"),
                    dict(code="B", name="B", duration_days=1, predecessors="A")])
    assert len(model.errors) == 2
    assert any("circular" in error for error in model.errors.values())
    assert len(model.warnings) == 1
    model.set_rows([dict(code="A", name="A", duration_days="1,5", predecessors="Falta"),
                    dict(code="A", name="Duplicada", duration_days=-1)])
    assert len(model.errors) == 5
    assert "2 tareas" in wizard.tasks_page.summary.text()
    assert not wizard.tasks_page.isComplete()
    model.set_rows([dict(code="A", name="A", duration_days=0, required_skill="Sin recurso")])
    assert wizard.tasks_page.isComplete()  # Una advertencia no bloquea.


def test_long_chain_validation_is_iterative():
    rows = [dict(code=f"A{i}", predecessors=f"A{i-1}" if i else "") for i in range(2500)]
    errors, _ = validate_tasks(rows, task_fields("proyecto"), [])
    assert errors == {}


def test_qt_attributes_do_not_shadow_methods():
    import ast
    import inspect
    from pathlib import Path
    from ganttier.gui import wizard
    from ganttier.gui.widgets import paste
    for module in (wizard, paste):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            cls = getattr(module, node.name)
            base = next((b for b in inspect.getmro(cls) if b.__module__.startswith("PySide6")), None)
            if base is None:
                continue
            for attr in ast.walk(node):
                if isinstance(attr, ast.Attribute) and isinstance(attr.ctx, ast.Store) and isinstance(attr.value, ast.Name) and attr.value.id == "self":
                    assert not callable(getattr(base, attr.attr, None)), f"{node.name}.{attr.attr}"


def test_maintenance_template_loaded_without_disk_files(qtbot, monkeypatch):
    import io
    from datetime import datetime
    import openpyxl
    import ganttier.gui.wizard as module
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Plan_Maestro"
    sheet.append(["Codigo", "Nombre", "Frecuencia", "Duracion_Dias", "Primera_Fecha", "Requiere_Desconexion"])
    sheet.append(["MT1", "Inspección", "M", 1, datetime(2027, 3, 1), True])
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    original_load = openpyxl.load_workbook
    monkeypatch.setattr(openpyxl, "load_workbook", lambda path, **kw: original_load(io.BytesIO(buffer.getvalue()), **kw))
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *args: ("plantilla_en_memoria.xlsx", ""))
    monkeypatch.setattr(PasteDialog, "exec", lambda self: QDialog.Accepted)
    wizard = build_wizard(FakeState(None), mode="mantenimiento")
    qtbot.addWidget(wizard)
    wizard.tasks_page.load_template()
    qtbot.waitUntil(lambda: wizard.tasks_page.model.rowCount() == 1, timeout=10000)
    assert wizard.tasks_page.model.values() == [dict(code="MT1", name="Inspección", frequency="M",
        duration_days=1, first_due="2027-03-01", requires_outage=True)]


class FakeState(QObject):
    projects_changed = Signal()

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.calendars = []
        self.resources = []
        self.opened = []

    def open_project(self, pid):
        self.opened.append(pid)


def fill_project(wizard, mode="proyecto"):
    wizard.project_page.name_edit.setText("Prueba UX3 " + mode)
    wizard.project_page.start_edit.setDate(QDate(2027, 1, 4))
    wizard.project_page.end_check.setChecked(True)
    wizard.project_page.end_edit.setDate(QDate(2027, 12, 31))


def advance_to_generate(wizard):
    wizard.next()
    assert wizard.currentId() == 1
    wizard.next()
    assert wizard.currentId() == 2
    wizard.next()
    assert wizard.currentId() == 3
    assert wizard.button(QWizard.NextButton).text() == "Generar programa"
    wizard.next()
    assert wizard.currentId() == 4


def test_no_resources_requires_confirmation_and_disables_capacity(qtbot):
    wizard = build_wizard(FakeState(None))
    qtbot.addWidget(wizard)
    fill_project(wizard)
    wizard.show()
    assert not wizard.resource_page.isComplete()
    wizard.resource_page.no_resources_check.setChecked(True)
    wizard.tasks_page.model.set_rows([dict(code="A", name="A", duration_days=1)])
    payload = wizard.payload()
    assert payload["objective"]["options"]["respect_resource_limits"] is False
    assert payload["objective"]["options"]["allow_overtime"] is False


class FailingClient:
    def __init__(self):
        self.calls = []

    def create_project(self, row):
        self.calls.append("project")
        return dict(row, id=73)

    def create_resource(self, row):
        self.calls.append("resource")
        return dict(row, id=4)

    def create_wbs(self, pid, row):
        self.calls.append("wbs")
        return dict(row, id=5)

    def create_activities(self, pid, rows):
        raise RuntimeError("Fallo de prueba en el lote")


def test_partial_failure_preserves_project_and_does_not_repeat(qtbot):
    state = FakeState(FailingClient())
    wizard = build_wizard(state)
    qtbot.addWidget(wizard)
    fill_project(wizard)
    wizard.resource_page.model.set_rows([dict(name="Cuadrilla", kind="cuadrilla", skills=["Eléctrica"], max_units=2)])
    wizard.tasks_page.model.set_rows([dict(code="A", name="A", duration_days=1)])
    wizard.show()
    with qtbot.waitSignal(state.projects_changed, timeout=10000):
        advance_to_generate(wizard)
    qtbot.waitUntil(lambda: wizard.generate_page.finished_work)
    page = wizard.generate_page
    assert wizard.created_pid == 73
    assert page.creation_report["created"] == dict(resources=1, wbs=1, activities=0, relations=0, periodic_tasks=0)
    assert page.open_anyway_button.isVisible()
    assert state.opened == []
    page.initializePage()
    assert state.client.calls == ["project", "resource", "wbs"]
    page.open_anyway_button.click()
    assert state.opened == [73]


@pytest.fixture
def real_state(qtbot, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    from ganttier.gui.client import ApiClient
    from ganttier.gui.server import EmbeddedServer
    from ganttier.gui.state import AppState
    from ganttier import db
    import sqlite3
    import uuid
    # API real y conexiones independientes, sin crear archivos de prueba.
    uri = f"file:ux3_{uuid.uuid4().hex}?mode=memory&cache=shared"

    def connect(*args, **kwargs):
        conn = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    anchor = connect()
    monkeypatch.setattr(db, "connect", connect)
    server = EmbeddedServer(lock=False)  # base en memoria compartida: sin archivo que bloquear
    server.start()
    client = ApiClient(server.base_url, token=server.token)
    state = AppState(client)
    state.calendars = client.calendars()
    state.resources = client.resources()
    yield state
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not _active, timeout=60000)
    QThreadPool.globalInstance().waitForDone(10000)
    client.close()
    server.stop()
    anchor.close()


@pytest.mark.parametrize("mode,count", [("proyecto", 20), ("mantenimiento", 6)])
def test_full_wizard_real_server_pasted_rows_and_open_project(real_state, qtbot, monkeypatch, mode, count):
    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.destinations = []

        def go(self, name):
            self.destinations.append(name)

    host = Host()
    qtbot.addWidget(host)
    wizard = build_wizard(real_state, host, mode)
    fill_project(wizard, mode)
    cid = wizard.project_page.calendar_combo.currentData()
    resources = PasteDialog(wizard, "Recursos", wizard.resource_page.model.fields, parse_table(
        f"Nombre\tTipo\tHabilidades\tHH_dia\tUnidades\tCalendario\n"
        f"Cuadrilla 1\tcuadrilla\tElectricidad\t8\t4\t{cid}\n"
        f"Cuadrilla 2\tcuadrilla\tElectricidad\t8\t4\t{cid}\n"))
    wizard.resource_page.model.set_rows(resources.values())
    if mode == "proyecto":
        text = "Codigo\tNombre\tDuracion_Dias\tHH\tHabilidad_Requerida\tDotacion\tPredecesoras\n"
        text += "\n".join(f"T{i}\tTarea {i}\t1\t8\tElectricidad\t1\t" + (f"T{i-1} FS" if i else "") for i in range(count))
    else:
        text = "Codigo\tNombre\tFrecuencia\tIntervalo\tDuracion_Dias\tHH\tDotacion\tHabilidad\tPrimera_Fecha\tInstalacion\tRequiere_Desconexion\n"
        text += "\n".join(f"M{i}\tMantención {i}\tM\t1\t1\t8\t1\tElectricidad\t{4+i:02d}-01-2027\tS/E Prueba\tNo" for i in range(count))
    tasks = PasteDialog(wizard, "Tareas", wizard.tasks_page.model.fields, parse_table(text))
    wizard.tasks_page.model.set_rows(tasks.values())
    wizard.objective_page.choose_objective("makespan")
    requests = []
    original = real_state.client.request

    def request(method, path, **kwargs):
        # Incluye apertura de proyecto y catálogos: todo REST fuera de GUI.
        assert QThread.currentThread() != QApplication.instance().thread(), path
        requests.append((method, path, kwargs.get("json")))
        return original(method, path, **kwargs)

    monkeypatch.setattr(real_state.client, "request", request)
    wizard.show()
    with qtbot.waitSignal(real_state.projects_changed, timeout=60000):
        advance_to_generate(wizard)
    qtbot.waitUntil(lambda: wizard.generate_page.opened_project, timeout=60000)
    report = wizard.generate_page.creation_report
    assert report["error"] is None, report
    assert wizard.generate_page.result_data["feasible"] is True, wizard.generate_page.result_data
    assert real_state.pid == wizard.created_pid
    assert report["created"]["resources"] == 2
    solve_body = next(body for _, path, body in requests if path.endswith("/schedule/solve"))
    assert solve_body["apply"] is True
    assert solve_body["options"] == dict(respect_resource_limits=True, allow_overtime=False, time_limit_s=30)
    monkeypatch.setattr(real_state.client, "request", original)
    activities = real_state.client.activities(wizard.created_pid)
    if mode == "proyecto":
        assert len(activities) == 20
        assert len(real_state.client.relations(wizard.created_pid)) == 19
        assert all(a["wbs_id"] and a["planned_start"] for a in activities)
    else:
        assert len(real_state.client.periodic_tasks(wizard.created_pid)) == 6
        assert len(activities) >= 60
        assert all(a["periodic_task_id"] and a["planned_start"] for a in activities)
        assert solve_body["horizon_start"] == "2027-01-04"
        assert solve_body["horizon_end"] == "2027-12-31"
    wizard.generate_page.gantt_button.click()
    assert host.destinations == ["gantt"]
