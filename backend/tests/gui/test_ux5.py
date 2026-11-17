"""UX5: API real embebida y Qt offscreen; --basetemp debe estar en el proyecto."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def wait_idle(qtbot):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not _active, timeout=60000)
    qtbot.wait(300)  # Incluye los temporizadores del Gantt y de los buscadores.
    qtbot.waitUntil(lambda: not _active, timeout=60000)


@pytest.fixture(scope="session")
def ux5_window(tmp_path_factory, request):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    pytest.importorskip("fastapi")
    root = tmp_path_factory.mktemp("ux5")
    with pytest.MonkeyPatch.context() as patch:
        for key, value in {"QT_QPA_PLATFORM": "offscreen", "GANTTIER_DB": root / "ux5.sqlite",
                           "GANTTIER_DATA_DIR": root / "data", "MPLCONFIGDIR": root / "matplotlib",
                           "TEMP": root, "TMP": root}.items():
            patch.setenv(key, str(value))
        from PySide6.QtCore import QSettings, QThreadPool
        from ganttier import services
        from ganttier.gui.widgets import common

        patch.setattr(services, "DATA_DIR", root / "data")
        errors = []
        patch.setattr(common, "show_error", lambda exc, parent=None: errors.append(exc))
        old_format = QSettings.defaultFormat()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root / "settings"))
        app = request.getfixturevalue("qapp")
        from ganttier.gui.app import create_window
        win, server = create_window(theme="light")
        win.resize(1440, 1000)
        win.show()
        yield win, errors
        QThreadPool.globalInstance().waitForDone(30000)
        app.processEvents()
        win.close()
        win.state.client.close()
        if server:
            server.stop()
        QSettings.setDefaultFormat(old_format)


@pytest.fixture
def ux5_gui(ux5_window, qtbot, monkeypatch):
    from ganttier.gui.pages import activities, maintenance, optimize, macro
    win, errors = ux5_window
    wait_idle(qtbot)
    errors.clear()
    for module in (activities, maintenance, optimize, macro):
        monkeypatch.setattr(module, "show_error", lambda exc, parent=None: errors.append(exc))

    def open_page(key="gantt", mode="proyecto"):
        win.go("proyectos")
        wait_idle(qtbot)
        project = win.state.client.create_project(dict(name="UX5", mode=mode,
            start_date="2026-01-05", end_date="2026-12-31", data_date="2026-01-05"))
        win.state.open_project(project["id"])
        wait_idle(qtbot)
        win.go(key)
        wait_idle(qtbot)
        page = win.page(key)
        assert not page.loading
        return page, project

    yield SimpleNamespace(win=win, errors=errors, open_page=open_page)
    wait_idle(qtbot)
    win.go("proyectos")
    wait_idle(qtbot)
    assert not errors, [str(error) for error in errors]


def accept_clipboard(monkeypatch, text):
    """Solo evita el clic modal: mapeo, conversión y validación son reales."""
    from PySide6.QtWidgets import QApplication, QDialog
    from ganttier.gui.widgets.paste import PasteDialog
    QApplication.clipboard().setText(text)

    def accept(dialog):
        assert dialog.accept_button.isEnabled(), dialog.mapping_error.text()
        assert dialog.values()
        return QDialog.Accepted
    monkeypatch.setattr(PasteDialog, "exec", accept)


def test_empty_activity_project_guides_next_action(ux5_gui, qtbot):
    from PySide6.QtWidgets import QLabel, QPushButton, QComboBox, QCheckBox
    page, _ = ux5_gui.open_page()
    assert page.empty_state.isVisible()
    assert not page.vertical_splitter.isVisible()
    assert "Aún no hay tareas" in [label.text() for label in page.empty_state.findChildren(QLabel)]
    labels = {button.text() for button in page.empty_state.findChildren(QPushButton)}
    assert labels >= {"Asistente lista de tareas", "Pegar desde Excel", "Agregar actividad", "Importar plantilla"}
    assert isinstance(page.scale, QComboBox) and isinstance(page.only_critical, QCheckBox)
    page.scale.setCurrentText("Mes")
    assert page.scale_buttons.checkedId() == page.scale.currentIndex()
    assert page.gantt.day_width == page.scale.currentData()
    assert any(action[3] == "Ctrl+G" for action in page.actions())


def test_paste_ten_activities_and_predecessor_relations(ux5_gui, qtbot, monkeypatch):
    page, project = ux5_gui.open_page()
    rows = ["Codigo\tNombre\tDuracion_Dias\tHH\tPredecesoras"]
    for i in range(10):
        pred = "" if i == 0 else f"A{i - 1:02} SS+2" if i == 2 else f"A{i - 1:02}"
        rows.append(f"A{i:02}\tActividad {i}\t2\t8,5\t{pred}")
    accept_clipboard(monkeypatch, "\n".join(rows))
    page.paste_activities()
    wait_idle(qtbot)
    assert page.last_paste_result == dict(activities=10, relations=9)
    assert page.arrays.n == 10 and not page.empty_state.isVisible()
    client = ux5_gui.win.state.client
    columns = client.columnar(project["id"])
    ids = dict(zip(columns["codes"], columns["ids"]))
    relations = client.relations(project["id"])
    assert len(relations) == 9
    assert any(r["pred_id"] == ids["A01"] and r["succ_id"] == ids["A02"]
               and r["type"] == "SS" and r["lag_days"] == 2 for r in relations)
    assert columns["work_hours"] == [8.5] * 10


@pytest.mark.parametrize("rows, message", [
    ([dict(code="A", name="A", predecessors="UNKNOWN")], "desconocida"),
    ([dict(code="A", name="A"), dict(code="A", name="Duplicada")], "duplicado"),
    ([dict(code="A", name="A", predecessors="B"), dict(code="B", name="B", predecessors="A")], "ciclo"),
])
def test_invalid_paste_does_not_write_any_activity(ux5_gui, qtbot, rows, message):
    page, project = ux5_gui.open_page()
    page.paste_rows([dict(duration_days=1, **row) for row in rows])
    wait_idle(qtbot)
    assert message in str(ux5_gui.errors.pop())
    assert not ux5_gui.win.state.client.columnar(project["id"])["ids"]


def test_objective_card_changes_normalized_weights(ux5_gui, qtbot):
    from PySide6.QtCore import Qt
    page, _ = ux5_gui.open_page("optimizar")
    assert len(page.objective_cards) == 5
    qtbot.mouseClick(page.objective_cards["Minimizar plazo"], Qt.LeftButton)
    assert page.weights() == dict(level=0, makespan=1, window=0, cost=0)
    assert page.objective_cards["Minimizar plazo"].isChecked()
    page.set_preset("Equilibrado")
    assert all(weight == pytest.approx(.25) for weight in page.weights().values())
    assert page.objective_cards["Equilibrado"].isChecked()
    page.advanced_toggle.setChecked(True)
    assert not page.advanced_panel.isHidden()


def test_status_banner_after_real_generation(ux5_gui, qtbot):
    from PySide6.QtWidgets import QLabel
    page, project = ux5_gui.open_page("optimizar")
    ux5_gui.win.state.client.create_activities(project["id"], [
        dict(code="GEN", name="Generación UX5", duration_days=2, work_hours=0)])
    page.method.setCurrentIndex(page.method.findData("heuristic"))
    page.time_limit.setValue(1)
    page.generate()
    wait_idle(qtbot)
    assert page.result and page.result.get("applied")
    assert not page.result_banner.isHidden()
    assert page.result_banner.property("result_kind") == "good"
    assert "Programa factible" in " ".join(label.text() for label in page.result_banner.findChildren(QLabel))
    page.result.update(feasible=False, violations=[{"detail": "Capacidad insuficiente"}])
    page._render()
    assert page.result_banner.property("result_kind") == "critical"
    assert "Capacidad insuficiente" in " ".join(label.text() for label in page.result_banner.findChildren(QLabel))


def test_paste_periodic_tasks_with_frequency_and_dates(ux5_gui, qtbot, monkeypatch):
    page, project = ux5_gui.open_page("mantenimiento", "mantenimiento")
    assert page.empty_state.isVisible()
    accept_clipboard(monkeypatch, "Codigo\tNombre\tFrecuencia\tDuracion_Dias\tHH\tPrimera_Fecha\n"
        "PM-01\tInspección\tMensual\t1\t4,5\t05-01-2026\n"
        "PM-02\tRevisión\tSemanal\t2\t8\t12-01-2026")
    page.paste_tasks()
    wait_idle(qtbot)
    tasks = ux5_gui.win.state.client.periodic_tasks(project["id"])
    assert len(tasks) == 2 and len(page.last_paste_result) == 2
    assert tasks[0]["frequency"] == "M" and tasks[0]["work_hours"] == 4.5
    assert tasks[1]["frequency"] == "W" and tasks[1]["first_due"] == "2026-01-12"
    assert all(task["active"] for task in tasks)
    assert page.model.rowCount() == 2 and not page.empty_state.isVisible()
