"""Regresiones F3; ejecutar con QT_QPA_PLATFORM=offscreen y --basetemp local."""
import os
from pathlib import Path
from threading import Event

import pytest


@pytest.fixture
def gui(request, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    pytest.importorskip("fastapi")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "f3.sqlite"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    from PySide6.QtCore import QSettings, QThreadPool
    from ganttier import services
    monkeypatch.setattr(services, "DATA_DIR", tmp_path / "datos")
    old_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    qapp = request.getfixturevalue("qapp")
    qtbot = request.getfixturevalue("qtbot")
    from ganttier.gui.app import create_window
    from ganttier.gui.pages import activities, control, optimize, reports
    from ganttier.gui.widgets import common
    errors = []
    for module in (common, activities, control, optimize, reports):
        monkeypatch.setattr(module, "show_error", lambda exc, parent=None: errors.append(exc))
    win, server = create_window(theme="light")
    win.show()
    yield win, qtbot, errors
    wait_idle(qtbot)
    # Los borradores de las pruebas no deben abrir el diálogo de cierre.
    for page in win._pages.values():
        if hasattr(page, "drafts_by_project"):
            page.drafts.clear()
            page.drafts_by_project.clear()
    QThreadPool.globalInstance().waitForDone(30000)
    qapp.processEvents()
    win.close()
    win.state.client.close()
    if server:
        server.stop()
    QSettings.setDefaultFormat(old_format)
    assert not errors, [str(exc) for exc in errors]


def wait_idle(qtbot):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not _active, timeout=120000)
    qtbot.wait(250)  # Temporizadores de filtros/calendario.
    qtbot.waitUntil(lambda: not _active, timeout=120000)


def task_list(win, name="Lista sin fechas"):
    client = win.state.client
    calendar = client.post("/calendars", {"name": name, "holidays_country": None})
    project = client.create_project(dict(name=name, mode="proyecto", start_date="2026-01-05",
                                         data_date="2026-01-05", calendar_id=calendar["id"]))
    client.post("/resources", dict(project_id=project["id"], name="Ana", skills=["electrico"],
                                   calendar_id=calendar["id"]))
    rows = client.create_activities(project["id"], [
        dict(code=f"F3-{i}", name=f"Actividad {i}", duration_days=2, work_hours=16,
             required_skill="electrico") for i in range(3)])
    client.create_relation(project["id"], dict(pred_id=rows[0]["id"], succ_id=rows[1]["id"]))
    assert all(a["planned_start"] is None for a in rows)
    return project, rows


def open_page(win, qtbot, pid, key="gantt"):
    win.state.open_project(pid)
    wait_idle(qtbot)
    win.go(key)
    wait_idle(qtbot)
    page = win.page(key)
    from ganttier.gui.pages.base import Page
    assert isinstance(page, Page)
    return page


@pytest.mark.parametrize("key", ["gantt", "optimizar"])
def test_solve_desde_lista_sin_fechas_muestra_factibilidad_y_aplica(gui, key):
    win, qtbot, _ = gui
    project, _ = task_list(win)
    page = open_page(win, qtbot, project["id"], key)
    page.generate()
    wait_idle(qtbot)
    result = page.last_summary if key == "gantt" else page.result
    assert result["feasible"] is True and result["applied"] is True
    assert result["violations"] == [] and "warnings" in result
    label = page.summary if key == "gantt" else page.status
    assert "Factible: Sí" in label.text() and "Violaciones: 0" in label.text()
    assert "Advertencias:" in label.text()
    assert all(a["planned_start"] for a in win.state.client.activities(project["id"]))


def test_doble_clic_generar_un_post_y_exclusion_entre_paginas(gui, monkeypatch):
    from PySide6.QtCore import Qt
    win, qtbot, _ = gui
    project, _ = task_list(win)
    page = open_page(win, qtbot, project["id"])
    win.go("optimizar")
    wait_idle(qtbot)
    optimizer = win.page("optimizar")
    win.go("gantt")
    wait_idle(qtbot)
    original = win.state.client.post
    release, entered = Event(), Event()
    calls = []

    def post(path, *args, **kwargs):
        if path.endswith("/schedule/solve"):
            calls.append(path)
            entered.set()
            assert release.wait(15), "La prueba no liberó el trabajador"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(win.state.client, "post", post)
    try:
        qtbot.mouseClick(page.generate_button, Qt.LeftButton)
        qtbot.waitUntil(entered.is_set)
        qtbot.mouseClick(page.generate_button, Qt.LeftButton)
        page.generate()  # También rechaza entradas directas/atajos.
        optimizer.generate()
        page.baseline()
        assert len(calls) == 1
        assert not page.generate_button.isEnabled()
        assert not page.baseline_button.isEnabled()
        assert not optimizer.solve_button.isEnabled()
    finally:
        release.set()
    wait_idle(qtbot)
    assert len(calls) == 1 and page.generate_button.isEnabled()
    assert optimizer.solve_button.isEnabled()


def test_borradores_control_por_proyecto_y_filtros_gantt(gui):
    win, qtbot, _ = gui
    first, rows = task_list(win, "Primero")
    second, _ = task_list(win, "Segundo")
    gantt = open_page(win, qtbot, first["id"])
    gantt.search.setText("exclusivo-del-primero")
    gantt.only_critical.setChecked(True)
    page = open_page(win, qtbot, first["id"], "control")
    page.scope.setCurrentIndex(page.scope.findData("all"))
    wait_idle(qtbot)  # H1: cambiar el filtro ahora solicita una página SQL.
    aid = rows[0]["id"]
    row = next(i for i, a in enumerate(page.model.rows) if a["id"] == aid)
    col = next(i for i, c in enumerate(page.model.columns) if c.key == "new_pct")
    assert page.model.setData(page.model.index(row, col), 15)
    assert page.has_unsaved()
    win.state.open_project(second["id"])
    wait_idle(qtbot)
    assert not page.drafts and aid not in page.arrays["ids"]
    assert page.drafts_by_project[first["id"]][aid]["new_pct"] == 15
    assert page.has_unsaved()
    win.go("gantt")
    wait_idle(qtbot)
    assert not gantt.search.text() and not gantt.only_critical.isChecked()
    # Restauración incluso si Control estaba oculto al cambiar de proyecto.
    win.state.open_project(first["id"])
    wait_idle(qtbot)
    assert page.drafts[aid]["new_pct"] == 15
    win.go("control")
    wait_idle(qtbot)
    assert next(a for a in page.model.rows if a["id"] == aid)["new_pct"] == 15
    page.discard()
    assert not page.has_unsaved()


def test_ruta_informes_data_dir_y_persistencia(gui, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QFileDialog
    from ganttier.gui.pages.reports import ReportsPage
    win, qtbot, _ = gui
    __import__("ganttier.gui.state", fromlist=["app_settings"]).app_settings().remove("reports/download_directory")
    page = ReportsPage(win.state)
    qtbot.addWidget(page)
    assert page.download_directory == Path(os.environ["GANTTIER_DATA_DIR"]) / "informes"
    chosen = tmp_path / "informes_elegidos"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(chosen))
    page.choose_directory()
    restored = ReportsPage(win.state)
    qtbot.addWidget(restored)
    assert restored.download_directory == chosen


@pytest.mark.parametrize("kind", ["relation", "assignment"])
def test_confirmacion_eliminar_relacion_y_asignacion(gui, monkeypatch, kind):
    from ganttier.gui.gantt import dialogs
    win, qtbot, _ = gui
    project, rows = task_list(win)
    page = open_page(win, qtbot, project["id"])
    client, pid, aid = win.state.client, project["id"], rows[0]["id"]
    client.create_assignment(pid, dict(activity_id=aid, skill="electrico", units=1, work_hours=16))
    dialog = dialogs.ActivityDetail(page, client.activity(aid), client.relations(pid), client.assignments(pid, aid))
    qtbot.addWidget(dialog)
    table, model, remove, button_key = ((dialog.rel_table, dialog.rel_model, dialog.delete_relation, "Relaciones")
                                      if kind == "relation" else
                                      (dialog.asg_table, dialog.asg_model, dialog.delete_assignment, "Asignaciones"))
    table.selectRow(0)
    row_id = model.rows[0]["id"]
    calls, prompts = [], []
    original = getattr(client, "delete_" + kind)
    def delete(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(client, "delete_" + kind, delete)
    def confirm(*args, **kwargs):
        prompts.append(args[2])
        assert not dialog.remove_buttons[button_key].isEnabled()
        assert rows[0]["code"] in args[2] and kwargs["danger"]
        return len(prompts) > 1
    monkeypatch.setattr(dialogs, "confirm", confirm)
    remove()
    assert not calls and table.currentIndex().row() == 0
    remove()
    remove()  # Bloqueado durante el DELETE.
    wait_idle(qtbot)
    assert calls == [row_id] and len(prompts) == 2
    assert model.rowCount() == 0 and dialog.remove_buttons[button_key].isEnabled()


@pytest.mark.parametrize("key", ["gantt", "optimizar"])
def test_no_factible_muestra_20_violaciones_y_no_afirma_aplicacion(gui, monkeypatch, key):
    from PySide6.QtWidgets import QMessageBox
    from ganttier.gui.pages import activities, optimize
    win, qtbot, _ = gui
    project, _ = task_list(win)
    page = open_page(win, qtbot, project["id"], key)
    warnings, notifications = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    for module in (activities, optimize):
        monkeypatch.setattr(module, "notify", lambda message, *a: notifications.append(message))
    original = win.state.client.post
    def post(path, *args, **kwargs):
        if path.endswith("/schedule/solve"):
            return dict(status="infactible", feasible=False, applied=False,
                        violations=[{"detail": f"Violación {i:02}"} for i in range(25)], warnings=["Revisar capacidad"])
        return original(path, *args, **kwargs)
    monkeypatch.setattr(win.state.client, "post", post)
    page.generate()
    wait_idle(qtbot)
    label = page.summary if key == "gantt" else page.status
    assert "Factible: No" in label.text() and "Sin aplicar" in label.text()
    assert "Violaciones: 25" in label.text() and "Advertencias: 1" in label.text()
    assert len(warnings) == 1 and "Violación 19" in warnings[0] and "Violación 20" not in warnings[0]
    assert not any("aplicado" in text.lower() or "guardados" in text.lower() for text in notifications)
    assert not page._gate.is_busy(f"solve:{project['id']}")


def test_perfil_virtual_filtra_sin_perder_seleccion(gui):
    from PySide6.QtCore import Qt, QSortFilterProxyModel
    from PySide6.QtWidgets import QListView
    from ganttier.gui.gantt.profile import SeriesListModel
    from ganttier.gui.gantt.view import GanttWidget
    _, qtbot, _ = gui
    gantt = GanttWidget()
    qtbot.addWidget(gantt)
    assert isinstance(gantt.x(), int)  # Conserva el contrato de QWidget.
    assert isinstance(gantt.day_to_x(0), (int, float))
    view = QListView()
    qtbot.addWidget(view)
    model = SeriesListModel(view)
    model.replace([dict(key=f"Recurso {i}", total=i, color=i) for i in range(10000)], {"Recurso 0"})
    proxy = QSortFilterProxyModel(view)
    proxy.setSourceModel(model)
    proxy.setFilterRole(Qt.UserRole)
    view.setModel(proxy)
    proxy.setFilterFixedString("Recurso 9999")
    assert proxy.rowCount() == 1
    assert proxy.setData(proxy.index(0, 0), Qt.Checked, Qt.CheckStateRole)
    assert model.selected_keys == {"Recurso 0", "Recurso 9999"}
    proxy.setFilterFixedString("")
    assert proxy.rowCount() == 10000


def test_cola_no_repite_clave_y_conserva_operaciones_distintas(gui, monkeypatch):
    from ganttier.gui.pages import activities
    win, qtbot, _ = gui
    project, _ = task_list(win)
    page = open_page(win, qtbot, project["id"])
    pending, writes = [], []
    monkeypatch.setattr(activities, "run_task", lambda fn, done, *a, **k: pending.append((fn, done)))
    monkeypatch.setattr(win.state, "bump_schedule", lambda: None)
    assert page.mutate(lambda: writes.append("A"), operation_key="A", recalc=False)
    assert not page.mutate(lambda: writes.append("A"), operation_key="A", recalc=False)
    assert page.mutate(lambda: writes.append("B"), operation_key="B", recalc=False)
    assert not page.mutate(lambda: writes.append("B"), operation_key="B", recalc=False)
    while pending:
        work, done = pending.pop(0)
        done(work())
    assert writes == ["A", "B"] and not page.mutating and not page._operation_keys


@pytest.mark.parametrize("key", ["gantt", "optimizar"])
def test_error_solve_libera_exclusion_sin_activar_metodo_antiguo(gui, monkeypatch, key):
    from ganttier.gui.client import ApiError
    win, qtbot, errors = gui
    project, _ = task_list(win)
    page = open_page(win, qtbot, project["id"], key)
    original = win.state.client.post
    calls = []
    def post(path, *args, **kwargs):
        calls.append(path)
        if path.endswith("/schedule/solve"):
            raise ApiError(503, "Servidor ocupado")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(win.state.client, "post", post)
    page.generate()
    wait_idle(qtbot)
    assert len(errors) == 1 and isinstance(errors[0], ApiError)
    errors.clear()
    assert calls == [f"/projects/{project['id']}/schedule/solve"]
    assert not page._gate.is_busy(f"solve:{project['id']}")
    control = page.generate_button if key == "gantt" else page.solve_button
    assert control.isEnabled()
