"""Página «Análisis y alertas»: alertas, pronósticos, EVM por grupo, calidad y cumplimiento preventivo."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def window(request, tmp_path_factory):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    temp = tmp_path_factory.mktemp("analisis")
    patcher = pytest.MonkeyPatch()
    patcher.setenv("GANTTIER_DB", str(temp / "ganttier.sqlite"))
    patcher.setenv("GANTTIER_DATA_DIR", str(temp / "data"))
    patcher.setenv("MPLCONFIGDIR", str(temp / "matplotlib"))
    from PySide6.QtCore import QSettings, QThreadPool
    previous = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(temp / "settings"))
    qapp = request.getfixturevalue("qapp")
    from ganttier.gui.app import create_window
    win, server = create_window(theme="light")
    win.show()
    errors = []
    from ganttier.gui.pages import analysis
    patcher.setattr(analysis, "show_error", lambda exc, parent=None: errors.append(exc))
    projects = {"proyecto": win.state.client.demo("proyecto"), "mantenimiento": win.state.client.demo("mantenimiento", 500)}
    yield win, projects, errors
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()
    win.close()
    win.state.client.close()
    server.stop()
    QSettings.setDefaultFormat(previous)
    patcher.undo()


def _idle(qtbot):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not _active, timeout=60000)


def test_analisis_proyecto_carga_todas_las_pestanas(window, qtbot):
    win, projects, errors = window
    win.state.open_project(projects["proyecto"]["id"])
    win.go("analisis")
    page = win.page("analisis")
    _idle(qtbot)
    assert page.forecast_model.rowCount() == 0          # carga perezosa: solo la pestaña visible
    for i in range(page.tabs.count()):
        page.tabs.setCurrentIndex(i)
        _idle(qtbot)
    assert not errors, errors
    assert page.forecast_model.rowCount() >= 6
    names = [r["name"] for r in page.forecast_model.rows]
    assert "EAC_CPI" in names and "EAC_combinado" in names
    assert page.group_model.rowCount() >= 2 and page.group_model.rows[-1]["name"] == "Total del proyecto"
    assert "Conciliación correcta" in page.group_note.text()
    assert page.quality_model.rowCount() >= 1
    assert not page.tabs.isTabVisible(page.tabs.indexOf(page.pm_tab))
    page.etc.setValue(5000)
    page.load_forecasts()
    _idle(qtbot)
    reest = next(r for r in page.forecast_model.rows if r["name"] == "EAC_reestimado")
    assert reest["status"] == "calculado" and reest["value"] is not None
    page.group.setCurrentIndex(page.group.findData("skill"))
    _idle(qtbot)
    assert page.group_model.rowCount() >= 2
    # Doble clic en un hallazgo lleva al Gantt filtrado.
    page._open_quality_example(page.quality_model.index(0, 0))
    _idle(qtbot)
    assert win.current_key() == "gantt" and win.page("gantt").search.text()
    win.page("gantt").search.clear()
    _idle(qtbot)


def test_alertas_reconocer_y_cumplimiento_preventivo(window, qtbot):
    win, projects, errors = window
    pid = projects["mantenimiento"]["id"]
    client = win.state.client
    # Corte avanzado: ocurrencias vencidas sin ejecutar generan backlog y alertas.
    client.put(f"/projects/{pid}/data-date", {"data_date": "2026-04-30"})
    win.state.open_project(pid)
    win.go("analisis")
    page = win.page("analisis")
    page._stale = True
    page.tabs.setCurrentIndex(0)
    page.activate()
    _idle(qtbot)
    page.tabs.setCurrentIndex(page.tabs.indexOf(page.pm_tab))
    _idle(qtbot)
    page.tabs.setCurrentIndex(0)
    _idle(qtbot)
    assert not errors, errors
    assert page.tabs.isTabVisible(page.tabs.indexOf(page.pm_tab))
    assert page.pm_kpis["due"]._value.text() not in ("", "0")
    assert page.age_model.rowCount() == 4 and page.oldest_model.rowCount() >= 1
    assert page._alerts, "debería haber alertas con backlog vencido"
    page.alert_table.selectRow(0)
    row = page._selected_alert()
    assert row["state"] == "nueva" and page.ack_button.isEnabled()
    page.acknowledge()
    _idle(qtbot)
    states = {r["id"]: r["state"] for r in page._alerts}
    assert states[row["id"]] == "reconocida"
    page.alert_state.setCurrentIndex(page.alert_state.findData("reconocida"))
    assert all(r["state"] == "reconocida" for r in page.alert_model.rows)


def test_dialogo_de_respaldos_crea_y_programa_restauracion(window, qtbot, monkeypatch):
    win, projects, errors = window
    from ganttier.gui import backups_dialog
    monkeypatch.setattr(backups_dialog, "confirm", lambda *a, **k: True)
    dialog = win.show_backups()
    _idle(qtbot)
    before = dialog.model.rowCount()
    dialog.create_backup()
    _idle(qtbot)
    assert dialog.model.rowCount() == before + 1 and dialog.model.rows[0]["verified"]
    dialog.table.selectRow(0)
    dialog.restore_selected()
    _idle(qtbot)
    assert dialog.data["pending_restore"] == dialog.model.rows[0]["file"]
    assert "Restauración programada" in dialog.status.text()
    dialog.cancel_restore()
    _idle(qtbot)
    assert dialog.data["pending_restore"] is None
    dialog.close()


def test_cadena_de_relaciones_calculadora_y_plan_semanal(window, qtbot, monkeypatch, tmp_path):
    win, projects, errors = window
    client = win.state.client
    pid = projects["proyecto"]["id"]
    win.state.open_project(pid)
    _idle(qtbot)
    client.create_activities(pid, [dict(code=f"CX{i}", name=f"Cadena {i}", duration_days=2) for i in range(4)]) \
        if hasattr(client, "create_activities") else client.post(f"/projects/{pid}/activities", [dict(code=f"CX{i}", name=f"Cadena {i}", duration_days=2) for i in range(4)])
    from ganttier.gui.tools_dialogs import ChainDialog, WorkdaysDialog, parse_groups
    assert parse_groups("A\nB, C\n\nD;E") == [["A", "B", "C"], ["D", "E"]]
    dialog = ChainDialog(win.state, win, ["CX0", "CX1", "CX2", "CX3"])
    dialog.preview()
    _idle(qtbot)
    assert dialog.table.rowCount() == 3 and dialog.table.item(0, 2).text() == "nueva"
    before = len(client.relations(pid))
    dialog.apply()
    _idle(qtbot)
    assert dialog.applied["created"] == 3 and len(client.relations(pid)) == before + 3
    # Repetir no duplica.
    again = ChainDialog(win.state, win, ["CX0", "CX1", "CX2", "CX3"])
    again.apply()
    _idle(qtbot)
    assert again.applied["created"] == 0 and again.applied["existing"] == 3
    # Calculadora: 18-09-2026 (feriado) no cuenta.
    calc = WorkdaysDialog(win.state, win)
    cal = next(c for c in win.state.calendars if "L-V" in c["name"])
    calc.calendar.setCurrentIndex(calc.calendar.findData(cal["id"]))
    from PySide6.QtCore import QDate
    calc.start.setDate(QDate(2026, 9, 14))
    calc.end.setDate(QDate(2026, 9, 20))
    calc.between()
    _idle(qtbot)
    assert "4 días laborables" in calc.result_label.text(), calc.result_label.text()   # lun–jue; el 18 es feriado
    assert any("18-09-2026" == calc.excluded.item(r, 0).text() for r in range(calc.excluded.rowCount()))
    # Plan semanal: se guarda un .xlsx legible.
    from ganttier.gui.pages import reports
    target = tmp_path / "plan.xlsx"
    monkeypatch.setattr(reports.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    win.go("informes")
    _idle(qtbot)
    win.page("informes").save_roster()
    _idle(qtbot)
    import openpyxl
    book = openpyxl.load_workbook(target)
    assert "Plan semanal" in book.sheetnames and "Detalle" in book.sheetnames
    assert not errors, errors
