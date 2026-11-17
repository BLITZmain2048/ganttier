"""UX4: Qt offscreen y API real con datos aislados.

Ejecutar desde Ganttier con PYTHONPATH=backend, QT_QPA_PLATFORM=offscreen
y --basetemp dentro de la carpeta de trabajo.
"""
from types import SimpleNamespace

import pytest


@pytest.fixture
def gui(request, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "ux4.sqlite"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    qapp = request.getfixturevalue("qapp")
    qtbot = request.getfixturevalue("qtbot")
    from PySide6.QtCore import QSettings, QThreadPool
    from ganttier import services
    from ganttier.gui import app
    from ganttier.gui.client import ApiClient
    from ganttier.gui.server import EmbeddedServer
    from ganttier.gui.state import AppState
    from ganttier.gui.pages import projects, resources, calendars, importexport, reports
    from ganttier.gui.widgets import common

    monkeypatch.setattr(services, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "main_window", lambda: None)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(reports, "QSettings", lambda *args: settings)
    errors = []
    for module in (common, projects, resources, calendars, importexport, reports):
        monkeypatch.setattr(module, "show_error", lambda exc, *args: errors.append(str(exc)))
        monkeypatch.setattr(module, "notify", lambda *args, **kwargs: None)
    server = EmbeddedServer()
    client = ApiClient(server.start(), token=server.token)
    state = AppState(client, settings=settings)
    pages = []

    def wait():
        qtbot.waitUntil(lambda: not common._active, timeout=120000)

    def page(cls):
        widget = cls(state)
        qtbot.addWidget(widget)
        pages.append(widget)
        widget.resize(1400, 1000)
        widget.show()
        widget.refresh()
        wait()
        return widget

    def project():
        state.project = client.create_project(dict(name="UX4", mode="proyecto", start_date="2026-01-05"))
        return state.project

    try:
        yield SimpleNamespace(state=state, client=client, page=page, project=project,
                              wait=wait, qtbot=qtbot, errors=errors)
    finally:
        wait()
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        for widget in pages:
            widget.close()
        client.close()
        server.stop()
    assert not errors, errors


def test_empty_projects_offers_creation(gui):
    from PySide6.QtWidgets import QPushButton
    from ganttier.gui.pages.projects import ProjectsPage
    page = gui.page(ProjectsPage)
    assert page.empty.isVisible()
    assert page.table.isHidden()
    assert any("lista de tareas" in b.text() for b in page.empty.findChildren(QPushButton))


def test_search_filters_projects_and_clears(gui):
    from ganttier.gui.pages.projects import ProjectsPage
    for name in ("Subestación Norte", "Mantenimiento Sur"):
        gui.client.create_project(dict(name=name, mode="proyecto", start_date="2026-01-05"))
    page = gui.page(ProjectsPage)
    page.search.setText("norte")
    gui.qtbot.waitUntil(lambda: page.proxy.rowCount() == 1)
    assert page.proxy.index(0, 0).data() == "Subestación Norte"
    page.search.setText("sin coincidencias")
    assert page.no_matches.isVisible() and not page.empty.isVisible()
    page.search.clear()
    assert page.proxy.rowCount() == 2 and page.table.isVisible()


def test_paste_creates_all_resources(gui, monkeypatch):
    from ganttier.gui.pages import resources
    project = gui.project()
    page = gui.page(resources.ResourcesPage)
    before = len(gui.client.resources(project["id"]))
    pasted = [dict(name=f"Electricista {i}", skills=["electricidad"], max_units=1, capacity_hours_day=8)
              for i in range(3)]
    monkeypatch.setattr(resources, "paste_from_clipboard", lambda parent, title, fields: pasted)
    page.paste_resources()
    gui.wait()
    rows = gui.client.resources(project["id"])
    assert len(rows) == before + 3
    assert {r["name"] for r in rows} >= {r["name"] for r in pasted}
    assert not page.empty.isVisible()
    capacity = next(r for r in page.capacity_model.rows if r["skill"] == "electricidad")
    assert capacity["hours"] == 24


def test_load_file_validates_template(gui, tmp_path):
    from ganttier.gui.pages.importexport import ImportExportPage
    path = tmp_path / "Plantilla_proyecto.xlsx"
    path.write_bytes(gui.client.template("proyecto"))
    page = gui.page(ImportExportPage)
    page.load_file(path)
    gui.wait()
    assert page.path == path and page.result is not None
    assert page._validated is not None
    assert not any(e.get("severity", "error") == "error" for e in page.result.get("errors", []))
    assert page.import_button.isEnabled()
    assert page.file_label.text() == path.name
    assert str(path) == page.file_label.toolTip()


def test_validation_severity_filter(gui):
    from ganttier.gui.pages.importexport import ImportExportPage
    page = gui.page(ImportExportPage)
    page._show_result({"errors": [dict(severity="error", message="Falta nombre"),
                                   dict(severity="warning", message="Revisar fecha")]}, "Validado")
    page.severity.setCurrentIndex(page.severity.findData("Advertencia"))
    assert page.issue_proxy.rowCount() == 1
    assert page.issue_proxy.index(0, 4).data() == "Revisar fecha"
    assert page.issue_model.rowCount() == 2


def test_reports_empty_then_generated_files(gui):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton
    from ganttier.gui.pages.reports import ReportsPage
    gui.state.project = gui.client.demo("proyecto")
    page = gui.page(ReportsPage)
    assert page.files_empty.isVisible() and page.log_empty.isVisible()
    for key, check in page.formats.items():
        check.setChecked(key == "html")
    page.period_buttons[1].click()
    assert page.period.currentData() == "monthly"
    page.generate()
    gui.wait()
    assert page.files and page.file_table.isVisible() and page.files_empty.isHidden()
    actions = page.file_table.indexWidget(page.file_model.index(0, 2))
    # La barra puede compactar botones secundarios (solo ícono) según el ancho: se valida el nombre accesible.
    assert {b.text() or b.accessibleName() for b in actions.findChildren(QPushButton)} == {"Abrir", "Guardar como…", "Vista previa"}
    assert page.directory_label.toolTip() == str(page.download_directory)
    assert str(page.download_directory) not in page.directory_label.text()
    page.resize(1500, 1000)
    gui.qtbot.waitUntil(lambda: page.report_splitter.orientation() == Qt.Horizontal)
    page.resize(1000, 1000)
    gui.qtbot.waitUntil(lambda: page.report_splitter.orientation() == Qt.Vertical)


def test_calendars_keep_public_models_and_show_summary(gui):
    from ganttier.gui.pages.calendars import CalendarsPage
    gui.client.create_calendar(dict(name="Jornada UX4", hours_per_day=8, workweek="1111100", holidays_country="CL"))
    page = gui.page(CalendarsPage)
    labels = [page.list.item(i).text() for i in range(page.list.count())]
    assert any("Jornada UX4" in text and "8 h/día · L-V · Feriados CL" in text for text in labels)
    assert isinstance(page.days, list) and page.exception_model.rowCount() == 0
    assert page.exceptions_empty.isVisible()
