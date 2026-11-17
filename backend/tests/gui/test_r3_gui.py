"""Ronda 3 de la GUI: búsqueda global entre proyectos (P5-06) y ausencias personales (P5-10)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_paleta_ofrece_buscar_en_todos_los_proyectos(qapp):
    from ganttier.gui.widgets.palette import CommandPalette
    calls = []
    palette = CommandPalette([("Inicio", "house", lambda: calls.append("inicio"), None)], None,
                             fallback=("Buscar «{q}» en todos los proyectos", "search", calls.append))
    palette.search.setText("i")                      # < 2 caracteres: sin búsqueda global
    assert palette.results.count() == 1
    palette.search.setText("IB01")
    texts = [palette.results.item(i).text() for i in range(palette.results.count())]
    assert texts == ["Buscar «IB01» en todos los proyectos"]
    assert not palette.empty_label.isVisibleTo(palette)
    palette.execute_current()
    assert calls == ["IB01"]


def test_ausencia_elegible_solo_trabajador_local():
    from ganttier.gui.absence_dialog import eligible
    assert eligible(None)
    assert "globales" in eligible({"project_id": None, "kind": "trabajador", "max_units": 1})
    assert "cuadrillas" in eligible({"project_id": 3, "kind": "cuadrilla", "max_units": 4})
    assert eligible({"project_id": 3, "kind": "trabajador", "max_units": 1.0}) is None


@pytest.fixture(scope="module")
def r3_window(request, tmp_path_factory):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    pytest.importorskip("fastapi")
    temp = tmp_path_factory.mktemp("r3_api")
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
    project = win.state.client.demo("proyecto")
    plan = win.state.client.demo("mantenimiento")
    yield win, project, plan
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()
    win.close()
    win.state.client.close()
    server.stop()
    QSettings.setDefaultFormat(previous)
    patcher.undo()


def _idle(qtbot, *pages):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not _active and all(not getattr(p, "loading", False) for p in pages), timeout=60000)
    qtbot.wait(150)
    qtbot.waitUntil(lambda: not _active and all(not getattr(p, "loading", False) for p in pages), timeout=60000)


def test_busqueda_global_abre_la_actividad_en_otro_proyecto(r3_window, qtbot):
    win, project, plan = r3_window
    client = win.state.client
    target = client.activities(project["id"], limit=5000)[-1]
    win.state.open_project(plan["id"])
    win.go("inicio")
    _idle(qtbot)
    dialog = win.global_search(target["code"])
    qtbot.waitUntil(lambda: dialog.model.rowCount() > 0, timeout=20000)
    row = next(r for r in range(dialog.model.rowCount())
               if dialog.model.row_dict(r)["id"] == target["id"] and dialog.model.row_dict(r)["type"] == "actividad")
    assert dialog.model.row_dict(row)["project"]["id"] == project["id"]
    dialog.choose(row)
    page = win.page("gantt")
    qtbot.waitUntil(lambda: win.state.pid == project["id"] and page.gantt.selected_id == target["id"], timeout=60000)
    assert win.current_key() == "gantt"
    # Con un filtro que la oculta, enfocar limpia los filtros y la selecciona igual.
    _idle(qtbot, page)
    page.gantt.select_id(None)
    page.search.setText("zzz-no-existe")
    page.filter_timer.timeout.emit()
    _idle(qtbot, page)
    page.focus_activity(target["id"])
    qtbot.waitUntil(lambda: page.gantt.selected_id == target["id"], timeout=60000)
    assert page.search.text() == ""


def test_busqueda_global_requiere_dos_caracteres_y_abre_tarea_periodica(r3_window, qtbot):
    win, project, plan = r3_window
    dialog = win.global_search("a")
    assert dialog.model.rowCount() == 0 and "dos caracteres" in dialog.status.text()
    task = win.state.client.periodic_tasks(plan["id"])[0]
    dialog.query.setText(task["code"])
    dialog.timer.timeout.emit()
    qtbot.waitUntil(lambda: any(dialog.model.row_dict(r)["type"] == "tarea periódica"
                                for r in range(dialog.model.rowCount())), timeout=20000)
    row = next(r for r in range(dialog.model.rowCount()) if dialog.model.row_dict(r)["type"] == "tarea periódica"
               and dialog.model.row_dict(r)["id"] == task["id"])
    dialog.choose(row)
    page = win.page("mantenimiento")
    qtbot.waitUntil(lambda: win.state.pid == plan["id"] and page.selected() is not None
                    and page.selected()["id"] == task["id"], timeout=60000)
    assert win.current_key() == "mantenimiento"


def test_ausencia_desde_recursos_con_vista_previa(r3_window, qtbot, monkeypatch):
    from ganttier.gui import absence_dialog
    from ganttier.gui.pages import resources as resources_page
    win, project, _plan = r3_window
    client = win.state.client
    worker = client.create_resource({"project_id": project["id"], "name": "Técnico R3", "kind": "trabajador",
                                     "skills": ["prot"], "max_units": 1})
    win.state.open_project(project["id"])
    win.go("recursos")
    page = win.page("recursos")
    page.refresh()
    _idle(qtbot, page)
    row = next(r for r in range(page.model.rowCount()) if page.model.row_dict(r)["id"] == worker["id"])
    page.table.selectRow(page.proxy.mapFromSource(page.model.index(row, 0)).row())
    seen = {}

    def run(dialog):
        from PySide6.QtCore import QDate
        dialog.start.setDate(QDate(2026, 10, 5))
        dialog.end.setDate(QDate(2026, 10, 11))      # lunes a domingo
        dialog.hours.setValue(4)
        assert dialog.validation_error() == "Indica el motivo de la ausencia."
        dialog.reason.setText("Capacitación SEC")
        dialog.preview()
        qtbot.waitUntil(lambda: dialog.preview_result is not None, timeout=20000)
        seen["days"] = {d["date"]: (d["hours_before"], d["hours_after"]) for d in dialog.preview_result["days"]}
        assert dialog.ok.isEnabled() and dialog.days.rowCount() == 7
        dialog.hours.setValue(3)                     # cambiar un dato invalida la vista previa
        assert not dialog.ok.isEnabled()
        dialog.hours.setValue(4)
        dialog.apply()                               # sin vista previa vigente: primero la calcula
        qtbot.waitUntil(lambda: dialog.preview_result is not None, timeout=20000)
        dialog.apply()
        qtbot.waitUntil(lambda: dialog.applied is not None, timeout=20000)
        return 1
    monkeypatch.setattr(absence_dialog.AbsenceDialog, "exec", run)
    page.absence()
    _idle(qtbot, page)
    # Un día no laborable no gana horas y ningún día supera su jornada.
    assert all(after == (min(4, before) if before > 0 else 0) for before, after in seen["days"].values())
    working = {day for day, (before, _after) in seen["days"].items() if before > 0}
    assert working and len(working) < 7
    listed = client.get(f"/resources/{worker['id']}/absences")
    assert {r["date"] for r in listed if r["reason"] == "Capacitación SEC"} == working
    # Un recurso global o una cuadrilla no abre el diálogo.
    notes = []
    monkeypatch.setattr(resources_page, "notify", lambda text, kind="ok": notes.append(text))
    page.table.clearSelection()
    page.table.setCurrentIndex(page.proxy.index(-1, -1))
    assert page.absence() is None and notes
