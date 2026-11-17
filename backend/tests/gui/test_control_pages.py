"""Integración Q4 con la ventana y API reales; ejecutar con QT_QPA_PLATFORM=offscreen.

Desde backend, con las dependencias instaladas:
python -B -m pytest -p no:cacheprovider tests/gui/test_control_pages.py \
    --basetemp=../../trabajos/pytest_gui_q4
"""
from __future__ import annotations

import importlib.util
import os
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTEST_QT_API"] = "pyside6"


REQUIRED = ("PySide6", "pytestqt", "fastapi", "uvicorn", "httpx", "matplotlib", "holidays",
            "openpyxl", "docx", "reportlab", "multipart")
MISSING = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
pytestmark = pytest.mark.skipif(bool(MISSING), reason="Faltan dependencias GUI/API: " + ", ".join(MISSING))


@pytest.fixture(scope="session")
def q4_application(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("q4")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
        patch.setenv("GANTTIER_DB", str(root / "ganttier.db"))
        patch.setenv("GANTTIER_DATA_DIR", str(root / "datos"))
        patch.setenv("MPLCONFIGDIR", str(root / "matplotlib"))
        from PySide6.QtCore import QSettings, QThreadPool
        from ganttier import services
        from ganttier.gui.app import create_window
        from ganttier.gui.widgets import common

        # También aislar servicios si otra prueba importó el módulo antes de esta fixture.
        patch.setattr(services, "DATA_DIR", root / "datos")
        app = request.getfixturevalue("qapp")
        old_format = QSettings.defaultFormat()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root / "settings"))
        errors = []
        patch.setattr(common, "show_error", lambda exc, parent=None: errors.append(exc))
        win, server = create_window(theme="light")
        win.show()
        yield win, root, errors
        QThreadPool.globalInstance().waitForDone(30000)
        app.processEvents()
        win.close()
        win.state.client.close()
        if server:
            server.stop()
        QSettings.setDefaultFormat(old_format)


@pytest.fixture
def gui(q4_application, qtbot, monkeypatch):
    from ganttier.gui.pages import control, performance, reports
    from ganttier.gui.widgets import common

    win, root, errors = q4_application
    qtbot.waitUntil(lambda: not common._active, timeout=60000)
    errors.clear()
    for module in (control, performance, reports):
        monkeypatch.setattr(module, "show_error", lambda exc, parent=None: errors.append(exc))
    project = win.state.client.demo("proyecto")
    win.go("proyectos")
    win.state.open_project(project["id"])
    yield win, project, root, errors
    qtbot.waitUntil(lambda: not common._active, timeout=120000)
    win.go("proyectos")
    assert not errors, [str(e) for e in errors]


def wait_idle(qtbot):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not _active, timeout=120000)


def open_page(win, key, qtbot):
    from ganttier.gui.pages.base import Page
    win.go(key)
    page = win.page(key)
    assert isinstance(page, Page), "La aplicación mostró un placeholder por un error de importación/construcción"
    wait_idle(qtbot)
    assert not page.loading
    return page


def edit(page, row, key, value):
    column = next(i for i, c in enumerate(page.model.columns) if c.key == key)
    assert page.model.setData(page.model.index(row, column), value)


def wait_table(qtbot, page):
    """La paginación y la búsqueda de Avance consultan ahora una ventana SQL."""
    qtbot.waitUntil(lambda: not page.loading and not page._search_timer.isActive(), timeout=30000)


def validate_file(path, kind):
    data = Path(path).read_bytes()
    assert len(data) > 100
    if kind == "pdf":
        assert data.startswith(b"%PDF")
    elif kind in ("xlsx", "docx"):
        with ZipFile(BytesIO(data)) as archive:
            assert archive.testzip() is None
            assert ("xl/workbook.xml" if kind == "xlsx" else "word/document.xml") in archive.namelist()
    else:
        assert "<html" in data.decode("utf-8").lower()


def test_cutoff_baselines_progress_and_evm(gui, qtbot, monkeypatch):
    from ganttier.gui.pages import control
    from ganttier.gui.widgets.common import qdate

    win, project, _, _ = gui
    page = open_page(win, "control", qtbot)
    page.scope.setCurrentIndex(page.scope.findData("all"))
    wait_table(qtbot, page)
    activities = win.state.client.activities(project["id"], limit=1000)
    activity = next(a for a in activities if a["work_hours"] > 0 and a["percent_complete"] < 100 and a["planned_start"])
    cut = max(date.fromisoformat(project.get("data_date") or project["start_date"]), date.fromisoformat(activity["planned_start"])) + timedelta(days=2)
    page.data_date.setDate(qdate(cut))
    page.save_data_date()
    wait_idle(qtbot)
    assert win.state.client.project(project["id"])["data_date"] == cut.isoformat()
    assert page.scurve["data_date"] == cut.isoformat()
    assert page.evm["data_date"] == cut.isoformat()

    page.create_baseline("Base Q4")
    wait_idle(qtbot)
    base = next(b for b in win.state.client.baselines(project["id"]) if b["name"] == "Base Q4")
    page.baselines.setCurrentIndex(page.baselines.findData(base["id"]))
    page.activate_baseline()
    wait_idle(qtbot)
    assert win.state.client.project(project["id"])["active_baseline_id"] == base["id"]

    page.search.setText(activity["code"])
    wait_table(qtbot, page)
    row = next(i for i, a in enumerate(page.model.rows) if a["id"] == activity["id"])
    before = page.evm["EV"]
    worker = next(r for r in win.state.client.resources(project["id"]) if r["kind"] == "trabajador")
    edit(page, row, "new_pct", 100)
    edit(page, row, "period_hours", 8)
    edit(page, row, "period_cost", 32000)
    edit(page, row, "resource_id", worker["id"])
    edit(page, row, "note", "Prueba integración Q4")
    page.save_progress()
    page.save_progress()  # Un doble clic no debe duplicar HH/costo.
    wait_idle(qtbot)
    entries = [p for p in win.state.client.progress(project["id"]) if p["note"] == "Prueba integración Q4"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["actual_start"] and entry["actual_start"] <= cut.isoformat()
    assert entry["actual_finish"] == entry["date"] == cut.isoformat()
    assert entry["actual_hours"] == 8 and entry["actual_cost"] == 32000
    assert entry["resource_id"] == worker["id"]
    assert not page.drafts
    assert page.evm["EV"] > before
    assert any(v and v > 0 for v in page.scurve["earned_abs"])
    assert len(page.chart.ax.lines) == 5
    assert page.chart.ax.lines[3].get_linestyle() == "--"

    monkeypatch.setattr(control, "confirm", lambda *a, **k: True)
    page.baselines.setCurrentIndex(page.baselines.findData(base["id"]))
    page.delete_baseline()
    wait_idle(qtbot)
    assert base["id"] not in [b["id"] for b in win.state.client.baselines(project["id"])]


def test_pagination_drafts_and_project_switch(gui, qtbot):
    win, project, _, _ = gui
    maintenance = win.state.client.demo("mantenimiento", 2000)
    win.state.open_project(maintenance["id"])
    page = open_page(win, "control", qtbot)
    page.scope.setCurrentIndex(page.scope.findData("all"))
    wait_table(qtbot, page)
    assert len(page.indices) == 2000
    assert page.model.rowCount() == 200
    aid = page.model.rows[0]["id"]
    edit(page, 0, "new_pct", 15)
    edit(page, 0, "note", "Borrador paginado")
    page.change_page(1)
    wait_table(qtbot, page)
    assert page.page_number == 1
    assert page.model.rows[0]["id"] != aid
    page.change_page(-1)
    wait_table(qtbot, page)
    assert page.model.rows[0]["new_pct"] == 15
    page.search.setText("no-coincide-con-ninguna-actividad-q4")
    wait_table(qtbot, page)
    assert page.model.rowCount() == 0 and aid in page.drafts
    page.search.clear()
    wait_table(qtbot, page)
    page.discard()
    assert not page.drafts and page.model.rows[0].get("new_pct") is None
    edit(page, 0, "new_pct", 10)
    win.state.open_project(project["id"])
    wait_idle(qtbot)
    assert not page.drafts
    assert aid not in page.arrays["ids"]


def test_progress_validation_and_filters(gui, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    win, project, _, _ = gui
    page = open_page(win, "control", qtbot)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    page.scope.setCurrentIndex(page.scope.findData("all"))
    wait_table(qtbot, page)
    edit(page, 0, "new_pct", 25)
    edit(page, 0, "actual_finish", page.cut_date)
    before = len(win.state.client.progress(project["id"]))
    page.save_progress()
    assert warnings and page.drafts
    assert len(win.state.client.progress(project["id"])) == before
    page.discard()
    page.scope.setCurrentIndex(page.scope.findData("late"))
    wait_table(qtbot, page)
    assert all(r["pct"] < 100 and (r["finish"] or r["start"]) <= page.cut_date for r in page.model.rows)
    page.scope.setCurrentIndex(page.scope.findData("open"))
    wait_table(qtbot, page)
    assert all(r["pct"] < 100 and r["start"] <= page.cut_date for r in page.model.rows)


def test_confirmed_progress_not_resent_after_reload_failure(gui, qtbot, monkeypatch):
    win, project, _, errors = gui
    page = open_page(win, "control", qtbot)
    page.scope.setCurrentIndex(page.scope.findData("all"))
    wait_table(qtbot, page)
    edit(page, 0, "new_pct", 10)
    edit(page, 0, "period_hours", 3)
    edit(page, 0, "note", "POST confirmado aunque falle GET")
    with monkeypatch.context() as patch:
        def failed_read(pid):
            raise RuntimeError("Fallo de lectura simulado")
        patch.setattr(win.state.client, "project", failed_read)
        page.save_progress()
        wait_idle(qtbot)
        assert not page.drafts
        assert not page.save_button.isEnabled()
        assert "se guardó" in page.status.text()
        assert len(errors) == 1
        errors.clear()
        page.save_progress()
    entries = [e for e in win.state.client.progress(project["id"]) if e["note"] == "POST confirmado aunque falle GET"]
    assert len(entries) == 1 and entries[0]["actual_hours"] == 3
    page.refresh()
    wait_idle(qtbot)
    assert page.model.rowCount() > 0


def test_failed_progress_keeps_draft_for_retry(gui, qtbot, monkeypatch):
    win, project, _, errors = gui
    page = open_page(win, "control", qtbot)
    page.scope.setCurrentIndex(page.scope.findData("all"))
    wait_table(qtbot, page)
    edit(page, 0, "new_pct", 10)
    edit(page, 0, "note", "Borrador recuperable")
    before = len(win.state.client.progress(project["id"]))
    with monkeypatch.context() as patch:
        def failed_post(pid, entries):
            raise RuntimeError("Rechazo de avance simulado")
        patch.setattr(win.state.client, "add_progress", failed_post)
        page.save_progress()
        wait_idle(qtbot)
        assert page.drafts and page.save_button.isEnabled()
        assert len(errors) == 1
        errors.clear()
    assert len(win.state.client.progress(project["id"])) == before
    page.save_progress()
    wait_idle(qtbot)
    assert not page.drafts


@pytest.mark.parametrize("basis", ["hours", "cost", "count"])
def test_curve_basis_period_scale_and_theme(gui, qtbot, basis):
    win, _, _, _ = gui
    page = open_page(win, "control", qtbot)
    page.basis.setCurrentIndex(page.basis.findData(basis))
    for period in ("day", "week", "month"):
        page.period.setCurrentIndex(page.period.findData(period))
        wait_idle(qtbot)
        assert page.scurve["basis"] == page.evm["basis"] == basis
        assert page.scurve["period"] == period
    page.scale.setCurrentIndex(1)
    assert list(page.chart.ax.lines[0].get_ydata()) == page.scurve["planned_abs"]
    win.toggle_theme()
    assert len(page.chart.ax.lines) == 5
    win.toggle_theme()


def test_performance_month_custom_sort_and_chart(gui, qtbot):
    from PySide6.QtCore import Qt
    from ganttier.gui.widgets.common import qdate
    from ganttier.gui.pages.performance import period_bounds

    win, project, _, _ = gui
    page = open_page(win, "desempeno", qtbot)
    page.reference.setDate(qdate("2026-02-15"))
    page.period.setCurrentIndex(page.period.findData("monthly"))
    wait_idle(qtbot)
    expected = win.state.client.performance(project["id"], "2026-02-01", "2026-02-28")
    assert page.rows == expected
    assert "01-02-2026" in page.bounds_label.text() and "28-02-2026" in page.bounds_label.text()
    assert page.model.rowCount() == len(expected)
    from ganttier.gui.widgets.common import fmt_num, fmt_pct
    due = sum(r["due_in_period"] for r in expected)
    on_time = sum(r["completed_on_time"] for r in expected)
    earned = sum(r["earned_hours"] for r in expected)
    actual = sum(r["actual_hours"] for r in expected)
    assert page.kpis["workers"]._value.text() == fmt_num(len(expected))
    assert page.kpis["compliance"]._value.text() == fmt_pct(100 * on_time / due if due else None)
    assert page.kpis["efficiency"]._value.text() == fmt_num(earned / actual if actual else None, 2)
    assert period_bounds("monthly", date(2024, 2, 15))[1] == date(2024, 2, 29)
    assert period_bounds("weekly", date(2026, 1, 1)) == (date(2025, 12, 29), date(2026, 1, 4))
    assert period_bounds("annual", date(2026, 7, 1)) == (date(2026, 1, 1), date(2026, 12, 31))
    for metric in ("compliance_pct", "hh_efficiency", "avg_delay_days", "SPI"):
        page.metric_combo.setCurrentIndex(page.metric_combo.findData(metric))
        assert len(page.chart.ax.patches) == min(40, sum(r.get(metric) is not None for r in expected))
    page.table.sortByColumn(0, Qt.AscendingOrder)
    names = [page.proxy.index(i, 0).data() for i in range(page.proxy.rowCount())]
    assert names == sorted(names)
    page.period.setCurrentIndex(page.period.findData("custom"))
    page.start.setDate(qdate("2026-03-10"))
    page.end.setDate(qdate("2026-03-01"))
    wait_idle(qtbot)
    assert page.model.rowCount() == 0
    assert "anterior" in page.status.text()
    page.end.setDate(qdate("2026-03-31"))
    wait_idle(qtbot)
    assert page.rows == win.state.client.performance(project["id"], "2026-03-10", "2026-03-31")


def test_reports_all_formats_open_save_preview(gui, qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QDialog
    from ganttier.gui.pages import reports

    win, _, _, _ = gui
    page = open_page(win, "informes", qtbot)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    page.choose_directory()
    for check in page.formats.values():
        check.setChecked(True)
    page.generate()
    wait_idle(qtbot)
    assert {f["format"] for f in page.files} == {"pdf", "xlsx", "docx", "html"}
    assert all(Path(f["local_path"]).is_relative_to(tmp_path) for f in page.files)
    assert all(Path(f["local_path"]).is_file() for f in page.files)
    opened = []
    monkeypatch.setattr(reports, "open_path", opened.append)
    for row, file in enumerate(page.files):
        page.file_table.selectRow(row)
        page.open_selected()
        wait_idle(qtbot)
        assert Path(opened[-1]).is_relative_to(tmp_path)
        validate_file(opened[-1], file["format"])
        target = tmp_path / ("copia." + file["format"])
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, p=target, **k: (str(p), ""))
        page.save_selected()
        wait_idle(qtbot)
        validate_file(target, file["format"])
        if file["format"] == "html":
            previews = []
            monkeypatch.setattr(QDialog, "exec", lambda d: previews.append(d.windowTitle()) or 0)
            page.preview_selected()
            wait_idle(qtbot)
            assert previews == ["Vista previa HTML"]


def test_automatic_reports_configuration_run_history(gui, qtbot, monkeypatch, tmp_path):
    from ganttier.gui.pages import reports
    win, project, _, _ = gui
    page = open_page(win, "informes", qtbot)
    page.enabled.setChecked(True)
    for check in page.auto_periods.values():
        check.setChecked(True)
    for kind, check in page.auto_formats.items():
        check.setChecked(kind == "html")
    page.weekday.setCurrentIndex(0)
    page.keep_last.setValue(4)
    page.save_automatic()
    wait_idle(qtbot)
    config = win.state.client.autoreports(project["id"])
    assert config == {"enabled": True, "weekly": True, "monthly": True, "annual": True,
                      "formats": ["html"], "weekday": 0, "keep_last": 4}
    page.run_automatic()
    wait_idle(qtbot)
    assert {e["period_type"] for e in page.log} == {"weekly", "monthly", "annual"}
    assert all(not e.get("error") and e.get("emitted_at") for e in page.log)
    count = page.log_model.rowCount()
    assert count == 3
    assert page.log_model.index(0, 0).data()
    page.download_directory = tmp_path
    opened = []
    monkeypatch.setattr(reports, "open_path", opened.append)
    page.log_table.selectRow(0)
    page.open_selected(history=True)
    wait_idle(qtbot)
    validate_file(opened[0], "html")
    page.run_automatic()
    wait_idle(qtbot)
    assert page.log_model.rowCount() == count


def test_outage_excel_cancel_and_date_validation(gui, qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from ganttier.gui.widgets.common import qdate
    win, _, _, _ = gui
    page = open_page(win, "informes", qtbot)
    path = tmp_path / "desconexiones.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    page.save_outages()
    wait_idle(qtbot)
    validate_file(path, "xlsx")
    assert page.last_download == str(path.resolve())
    from openpyxl import load_workbook
    workbook = load_workbook(path, read_only=True)
    assert workbook.sheetnames
    workbook.close()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    page.save_outages()
    assert not page.working
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    page.outage_start.setDate(qdate("2026-12-31"))
    page.outage_end.setDate(qdate("2026-01-01"))
    page.save_outages()
    assert warnings and not page.working


def test_obsolete_responses_do_not_replace_current_project(gui, qtbot, monkeypatch):
    from ganttier.gui.pages import control, performance, reports
    win, project, _, _ = gui
    other = win.state.client.demo("proyecto")
    for key, module in (("control", control), ("desempeno", performance), ("informes", reports)):
        win.state.open_project(project["id"])
        page = open_page(win, key, qtbot)
        pending = []
        with monkeypatch.context() as patch:
            patch.setattr(module, "run_task", lambda fn, done=None, *a, **k: pending.append((fn, done)))
            page.refresh()
            old = [(fn(), done) for fn, done in pending]
            win.state.open_project(other["id"])
            for result, done in old:
                done(result)
            assert page._pid == other["id"]
            if key == "control":
                assert page.scurve is None and page.model.rowCount() == 0
            elif key == "desempeno":
                assert not page.rows
            else:
                assert not page.files and not page.log_model.rows
        page.refresh()
        wait_idle(qtbot)
