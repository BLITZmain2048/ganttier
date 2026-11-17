"""Q3: GUI real + API embebida; ninguna prueba utiliza la BD del usuario.

QT_QPA_PLATFORM=offscreen PYTHONPATH=backend python -m pytest <este archivo>
Use --basetemp dentro del espacio de trabajo para mantener allí los temporales.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture(scope="session")
def gui_session(tmp_path_factory, request):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    data = tmp_path_factory.mktemp("q3_gui")
    patch = pytest.MonkeyPatch()
    patch.setenv("GANTTIER_DB", str(data / "test.db"))
    patch.setenv("GANTTIER_DATA_DIR", str(data))
    patch.setenv("MPLCONFIGDIR", str(data / "matplotlib"))
    patch.setenv("TEMP", str(data))
    patch.setenv("TMP", str(data))
    app = request.getfixturevalue("qapp")
    from PySide6.QtCore import QSettings, QThreadPool
    from ganttier import services
    from ganttier.gui import app as gui_app
    # Otras suites pueden haber importado services antes de fijar el entorno.
    patch.setattr(services, "DATA_DIR", data)
    # Aislar la fábrica de preferencias sin sustituir la clase: el cierre R4
    # consulta QSettings.NoError al comprobar que se guardaron los ajustes.
    patch.setattr(gui_app, "app_settings", lambda: QSettings(str(data / "settings.ini"), QSettings.IniFormat))
    win, server = gui_app.create_window(theme="light")
    win.show()
    try:
        project = win.state.client.demo("proyecto")
        maintenance = win.state.client.demo("mantenimiento", 2000)
        yield SimpleNamespace(win=win, server=server, project=project, maintenance=maintenance, app=app)
    finally:
        QThreadPool.globalInstance().waitForDone(60000)
        app.processEvents()
        win.close()
        server.stop()
        win.state.client.close()
        patch.undo()


@pytest.fixture
def gui(gui_session, request, monkeypatch):
    qtbot = request.getfixturevalue("qtbot")
    from ganttier.gui.widgets import common
    from ganttier.gui.pages import maintenance, importexport
    errors = []
    for module in (common, maintenance, importexport):
        monkeypatch.setattr(module, "show_error", lambda exc, *args: errors.append(str(exc)))
    session = gui_session
    def wait():
        qtbot.waitUntil(lambda: not common._active, timeout=120000)
    def open_page(pid, key):
        session.win.state.open_project(pid)
        session.win.go(key)
        wait()
        return session.win.page(key)
    yield SimpleNamespace(**vars(session), qtbot=qtbot, errors=errors, wait=wait, open=open_page)
    wait()
    assert not errors, errors


def test_master_crud_all_fields_and_filters(gui, monkeypatch):
    from dataclasses import fields
    from ganttier.domain import PeriodicTask
    from ganttier.gui.pages import maintenance as mod
    page = gui.open(gui.maintenance["id"], "mantenimiento")
    assert isinstance(page, mod.MaintenancePage)
    assert len(page.columnar["ids"]) == 2000
    assert not page.replace.isChecked()
    assert {f.key for f in page._fields()} == {f.name for f in fields(PeriodicTask)} - {"id", "project_id"}
    initial = len(page.tasks)
    values = {"code": "Q3-CRUD", "name": "Inspección Q3", "frequency": "NM", "interval": 2,
              "first_due": "2026-02-02", "horizon_end": "2026-12-31", "duration_days": 2,
              "work_hours": 16, "crew_size": 2, "preferred_months": "2,4,6,8,10,12",
              "preferred_weekday": 0, "day_of_month": 2, "tol_before_days": 3,
              "tol_after_days": 4, "window_hard": True, "location": "S/E Q3", "equipment": "52-Q3",
              "voltage_kv": "220,5", "requires_outage": True, "required_skill": "eléctrico",
              "discipline": "Pruebas", "calendar_id": gui.win.state.calendars[0]["id"],
              "resource_id": None, "wbs_code": "", "priority": 75, "cost_per_occurrence": 1000, "active": True}
    # Ejercita el diálogo real, incluidos sus editores y la aceptación.
    def accept_new():
        from PySide6.QtWidgets import QApplication
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, mod.FormDialog)
        dialog._editors["code"].setText("Q3-CRUD")
        dialog._editors["name"].setText("Inspección Q3")
        dialog._accept()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, accept_new)
    page.new_task()
    gui.wait()
    assert len(page.tasks) == initial + 1
    task = next(t for t in page.tasks if t["code"] == "Q3-CRUD")
    page.save_task(values, task["id"])
    gui.wait()
    page.search.setText("q3-crud")
    assert page.model.rowCount() == 1
    assert page.model.rows[0]["voltage_kv"] == 220.5
    assert page.model.rows[0]["preferred_weekday"] == 0
    assert page.model.rows[0]["horizon_end"] == "2026-12-31"
    page.location.setCurrentIndex(page.location.findData("S/E Q3"))
    page.outage_only.setChecked(True)
    assert page.model.rowCount() == 1
    assert page.kpis["hours"]._value.text() == "96"
    page.table.selectRow(0)
    monkeypatch.setattr(mod, "confirm", lambda *a, **k: True)
    page.delete_task()
    gui.wait()
    assert len(page.tasks) == initial
    assert not any(t["id"] == task["id"] for t in gui.win.state.client.periodic_tasks(gui.maintenance["id"]))
    page.search.clear()
    page.location.setCurrentIndex(0)
    page.outage_only.setChecked(False)
    page.PAGE_SIZE = 10
    page.render_tasks()
    first = page.model.rows[0]["id"]
    page.change_page(1)
    assert page.task_page == 1 and page.model.rows[0]["id"] != first
    page.PAGE_SIZE = 200


@pytest.mark.parametrize("fallback", [False, True])
def test_generate_real_schedule_calendar_heatmap_outages(gui, tmp_path, monkeypatch, fallback):
    from PySide6.QtWidgets import QFileDialog
    from ganttier.gui.pages.maintenance import WeeklyHeatmap
    from ganttier.gui.theme import tokens
    # Caso pequeño factible; la demo de 2000 se usa en las pruebas de lectura/CRUD.
    client = gui.win.state.client
    if fallback:
        from ganttier.gui.client import ApiError
        original = client.post
        def post(path, *args, **kwargs):
            if path.endswith("/schedule/solve"):
                raise ApiError(404, "Ruta no disponible")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(client, "post", post)
    project = client.create_project({"name": "Q3 programación", "mode": "mantenimiento",
                                     "start_date": "2026-03-02", "end_date": "2026-03-31"})
    pid = project["id"]
    client.create_resource({"name": "Técnico Q3", "project_id": pid, "skills": ["q3"], "capacity_hours_day": 8})
    client.create_periodic_task(pid, {"code": "Q3-PLAN", "name": "Revisión", "frequency": "W",
        "duration_days": 1, "work_hours": 4, "crew_size": 1, "required_skill": "q3", "location": "S/E Plan",
        "requires_outage": True, "first_due": "2026-03-02", "tol_before_days": 0, "tol_after_days": 2})
    page = gui.open(pid, "mantenimiento")
    page.generate()
    gui.wait()
    assert page.last_result and page.last_result["status"] != "infactible"
    activities = client.activities(pid)
    assert activities and all(a["planned_start"] for a in activities)
    assert any(a["resource_id"] for a in client.assignments(pid))
    assert page.calendar_model.rowCount() == 1
    assert "⚡" in page.calendar_model.rows[0]["2026-03"]
    assert page.heatmap.profile["series"]
    assert "HH" in page.heatmap.cell_tooltip(0, 0)
    assert WeeklyHeatmap.cell_color(10, 5).name() == tokens().critical
    assert WeeklyHeatmap.cell_color(1, 0).name() == tokens().critical
    assert WeeklyHeatmap.cell_color(0, 0).name() == tokens().surface2
    assert WeeklyHeatmap.cell_color(1, 10) != WeeklyHeatmap.cell_color(9, 10)
    page.heatmap.show()
    assert not page.heatmap.grab().isNull()
    gui.win.toggle_theme()
    assert not page.heatmap.grab().isNull()
    gui.win.toggle_theme()
    destination = tmp_path / "desconexiones.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), ""))
    page.download_outages()
    gui.qtbot.waitUntil(destination.exists, timeout=30000)
    from openpyxl import load_workbook
    book = load_workbook(destination, read_only=True)
    assert book.sheetnames
    book.close()


def test_solver_fallback_only_for_missing_endpoint(gui, monkeypatch):
    from ganttier.gui.client import ApiError
    page = gui.open(gui.maintenance["id"], "mantenimiento")
    client = gui.win.state.client
    original = client.post
    calls = []
    def post(path, *args, **kwargs):
        if path.endswith("/schedule/solve"):
            calls.append(path)
            raise ApiError(422, "Ventana inválida")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(client, "post", post)
    monkeypatch.setattr(client, "generate_occurrences", lambda *a, **k: pytest.fail("No debe ejecutar el fallback"))
    page.generate()
    gui.wait()
    assert len(calls) == 1
    assert gui.errors == ["Ventana inválida"]
    gui.errors.clear()
    assert not page.generating and page.generate_button.isEnabled()


def test_maintenance_disabled_in_project_mode(gui):
    page = gui.open(gui.project["id"], "mantenimiento")
    assert not page.isEnabled()
    assert page.model.rowCount() == 0


@pytest.mark.parametrize("kind", ["proyecto", "mantenimiento"])
def test_templates_validate_import_new_and_export(gui, tmp_path, monkeypatch, kind):
    from PySide6.QtWidgets import QFileDialog
    from ganttier.gui.pages.importexport import ImportExportPage
    page = gui.open(None, "importar")
    assert isinstance(page, ImportExportPage)
    assert not page.export_button.isEnabled()
    path = tmp_path / f"{kind}.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    page.download_template(kind)
    gui.qtbot.waitUntil(path.exists, timeout=30000)
    gui.wait()
    projects_before = len(gui.win.state.client.projects())
    page.pick_file(path)
    page.validate_file()
    gui.wait()
    assert page.result and not [e for e in page.result["errors"] if e["severity"] == "error"]
    assert len(gui.win.state.client.projects()) == projects_before
    page.last_import = None
    page.import_file()
    gui.wait()
    assert page.last_import and gui.win.state.mode == kind
    assert len(gui.win.state.client.projects()) == projects_before + 1
    assert gui.win.state.calendars
    out = tmp_path / "exportado.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    page.export_project()
    gui.qtbot.waitUntil(out.exists, timeout=30000)
    gui.wait()
    page.target.setCurrentIndex(1)
    page.pick_file(out)
    before = gui.win.state.client.project(gui.win.state.pid)["counts"]
    for mode in ("merge", "replace"):
        page.mode.setCurrentIndex(page.mode.findData(mode))
        page.validate_file()
        gui.wait()
        assert not [e for e in page.result["errors"] if e["severity"] == "error"]
        assert gui.win.state.client.project(gui.win.state.pid)["counts"] == before
        import ganttier.gui.pages.importexport as module
        monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
        page.last_import = None
        page.import_file()
        gui.wait()
        assert page.last_import is not None


@pytest.mark.parametrize("filename", ["Ejemplo_Proyecto_SE_220kV.xlsx", "Ejemplo_Plan_Mantenimiento.xlsx"])
def test_examples_download_validate_import(gui, tmp_path, monkeypatch, filename):
    from PySide6.QtWidgets import QFileDialog
    source = ROOT / "ejemplos" / filename
    if not source.exists():
        pytest.skip("El ejemplo no está incluido en esta copia")
    page = gui.open(None, "importar")
    index = page.examples.findData(str(source))
    assert index >= 0
    page.examples.setCurrentIndex(index)
    downloaded = tmp_path / filename
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(downloaded), ""))
    page.download_example()
    gui.qtbot.waitUntil(downloaded.exists, timeout=30000)
    assert downloaded.read_bytes() == source.read_bytes()
    page.pick_file(downloaded)
    page.validate_file()
    gui.wait()
    assert page.import_button.isEnabled(), page.summary.text()
    page.last_import = None
    page.import_file()
    gui.wait()
    assert page.last_import and gui.win.state.pid == page.last_import["id"]


def test_invalid_workbook_and_validation_invalidation(gui, tmp_path):
    page = gui.open(None, "importar")
    invalid = tmp_path / "invalido.xlsx"
    invalid.write_bytes(b"no es un libro")
    page.pick_file(invalid)
    page.validate_file()
    gui.wait()
    assert page.issue_model.rowCount() > 0
    assert not page.import_button.isEnabled()
    valid = tmp_path / "valido.xlsx"
    valid.write_bytes(gui.win.state.client.template("proyecto"))
    page.pick_file(valid)
    page.validate_file()
    gui.wait()
    assert page.import_button.isEnabled()
    before = len(gui.win.state.client.projects())
    valid.write_bytes(valid.read_bytes() + b" ")
    page.import_file()
    assert not page.import_button.isEnabled()
    assert len(gui.win.state.client.projects()) == before
    assert "Vuelve a validar" in page.summary.text()


def test_drop_excel(gui, tmp_path):
    from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl, Qt
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    page = gui.open(None, "importar")
    path = tmp_path / "drop.xlsx"
    path.write_bytes(gui.win.state.client.template("proyecto"))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    page.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    page.dropEvent(drop)
    assert page.path == path and drop.isAccepted()


def test_launcher_uses_localappdata_and_respects_overrides(tmp_path, monkeypatch):
    from ganttier.gui.launcher import configure_runtime
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for key in ("GANTTIER_DB", "GANTTIER_DATA_DIR", "MPLCONFIGDIR"):
        monkeypatch.delenv(key, raising=False)
    expected = tmp_path / "local" / "Ganttier"
    assert configure_runtime() == expected
    assert Path(os.environ["GANTTIER_DB"]) == expected / "ganttier.db"
    assert Path(os.environ["MPLCONFIGDIR"]).is_relative_to(expected)
    override = tmp_path / "portable"
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(override))
    monkeypatch.setenv("GANTTIER_DB", str(override / "custom.db"))
    assert configure_runtime() == override
    assert Path(os.environ["GANTTIER_DB"]) == override / "custom.db"


def test_launcher_restores_windowless_streams(tmp_path, monkeypatch):
    from ganttier.gui.launcher import configure_runtime
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    streams = []
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", None)
        patch.setattr(sys, "stderr", None)
        configure_runtime()
        streams = [sys.stdout, sys.stderr]
        assert not sys.stderr.isatty()
        sys.stderr.write("diagnóstico Q3\n")
    for stream in streams:
        stream.close()
    assert "diagnóstico Q3" in (tmp_path / "ganttier.log").read_text(encoding="utf-8")


def test_batch_files_crlf_and_requirements_preserved():
    for name in ("iniciar.bat", "iniciar_consola.bat", "instalar.bat", "construir_exe.bat"):
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
        assert b"pause" in raw
    install = (ROOT / "instalar.bat").read_text(encoding="utf-8")
    assert "requirements-opcional.txt" in install and "heuristico" in install
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for package in ("fastapi", "pydantic", "uvicorn", "python-multipart", "numpy", "openpyxl", "reportlab",
                    "python-docx", "matplotlib", "holidays", "pytest", "httpx", "PySide6>=6.6", "pytest-qt"):
        assert package in requirements


def test_form_and_solver_fields_match_backend_contracts():
    """Comprobación de integración disponible también sin instalar Qt/Pydantic."""
    import ast
    from dataclasses import fields
    from ganttier.domain import PeriodicTask
    tree = ast.parse((ROOT / "backend/ganttier/gui/pages/maintenance.py").read_text(encoding="utf-8"))
    form = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_fields")
    form_keys = {n.args[0].value for n in ast.walk(form) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "Field"}
    assert form_keys == {f.name for f in fields(PeriodicTask)} - {"id", "project_id"}
    solve_path = ROOT / "backend/ganttier/api/solve.py"
    if solve_path.exists():
        schema = ast.parse(solve_path.read_text(encoding="utf-8"))
        cls = next(n for n in schema.body if isinstance(n, ast.ClassDef) and n.name == "SolveRequest")
        fields_ = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)}
        generate = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "generate")
        body = next(n.value for n in ast.walk(generate) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "body" for t in n.targets))
        assert {k.value for k in body.keys} <= fields_
