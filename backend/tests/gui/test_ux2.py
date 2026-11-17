"""UX2: pytest-qt offscreen, API real y QSettings aislado por prueba."""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import re
from types import SimpleNamespace
import unicodedata

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTEST_QT_API"] = "pyside6"
REQUIRED = ("PySide6", "pytestqt", "fastapi", "uvicorn", "httpx", "matplotlib", "numpy",
            "holidays", "openpyxl", "docx", "reportlab", "multipart")
MISSING = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
gui_test = pytest.mark.skipif(bool(MISSING), reason="Faltan dependencias: " + ", ".join(MISSING))


@pytest.fixture
def gui(tmp_path, request, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "ganttier.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    from PySide6.QtCore import QSettings
    from ganttier import services
    from ganttier.gui import app as gui_app
    from ganttier.gui.pages import home
    from ganttier.gui.widgets import common

    app = request.getfixturevalue("qapp")
    qtbot = request.getfixturevalue("qtbot")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    # Las fábricas aíslan preferencias; conservar la clase y sus enumeraciones
    # permite verificar el cierre R4 con la misma API que en producción.
    monkeypatch.setattr(gui_app, "app_settings", lambda: settings)
    from ganttier.gui import state as gui_state
    monkeypatch.setattr(gui_state, "app_settings", lambda: settings)
    monkeypatch.setattr(services, "DATA_DIR", tmp_path / "datos")
    errors = []
    monkeypatch.setattr(common, "show_error", lambda exc, parent=None: errors.append(exc))
    monkeypatch.setattr(home, "show_error", lambda exc, parent=None: errors.append(exc))
    previous_main = gui_app.main_window()
    win, server = gui_app.create_window(theme="light")
    win.show()

    def idle():
        qtbot.waitUntil(lambda: not common._active, timeout=60000)
        app.processEvents()
        qtbot.waitUntil(lambda: not common._active, timeout=60000)

    idle()
    try:
        yield SimpleNamespace(win=win, app=app, errors=errors, server=server, idle=idle, settings=settings)
    finally:
        idle()
        win.close()
        if server:
            server.stop()
        gui_app._MAIN = previous_main


def _shortcut(win, text):
    return next(action for action in win.actions() if text in [key.toString() for key in action.shortcuts()])


def _project(gui, name="Proyecto UX2"):
    return gui.win.state.client.create_project({"name": name, "mode": "proyecto",
                                               "start_date": "2026-01-05", "end_date": "2026-02-28"})


def test_palette_matching_without_qt():
    path = Path(__file__).resolve().parents[2] / "ganttier/gui/widgets/palette.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    namespace = {"re": re, "unicodedata": unicodedata}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    matches = namespace["matches"]
    assert matches("", "Inicio")
    assert matches("programacion", "Programación macro")
    assert matches("macro pro", "Programación macro")
    assert matches("ayg", "Actividades y Gantt")
    assert not matches("zzzzz", "Actividades y Gantt")


def test_recent_projects_normalizes_settings_and_limits_history_without_qt():
    path = Path(__file__).resolve().parents[2] / "ganttier/gui/state.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AppState")
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef)
               and node.name in ("recent_projects", "touch_recent")]
    namespace = {}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
    values = {"recent_projects": ["3", "incorrecto", 3, -1, "2"]}
    state = SimpleNamespace(settings=SimpleNamespace(value=lambda key, default: values.get(key, default),
                           setValue=lambda key, value: values.__setitem__(key, value)),
                           recent_projects_changed=SimpleNamespace(emit=lambda: None))
    state.recent_projects = lambda: namespace["recent_projects"](state)
    assert state.recent_projects() == [3, 2]
    for pid in range(10, 20):
        namespace["touch_recent"](state, pid)
    namespace["touch_recent"](state, 15)
    assert state.recent_projects() == [15, 19, 18, 17, 16, 14, 13, 12]


@gui_test
def test_sidebar_collapses_expands_and_retains_tooltips(gui):
    win = gui.win
    width = win.sidebar.width()
    _shortcut(win, "Ctrl+B").trigger()
    assert win.sidebar_collapsed and win.sidebar.width() < width
    for row, spec in enumerate(win._rows):
        item = win.nav.item(row)
        if spec:
            assert item.text() == "" and spec.label in item.toolTip()
            assert not item.icon().isNull()
        else:
            assert item.isHidden()
    win.sidebar_btn.click()
    assert not win.sidebar_collapsed and win.sidebar.width() == width
    _shortcut(win, "Ctrl+1").trigger()
    assert win.current_key() == "inicio"


@gui_test
def test_selector_opens_project_and_orders_recents(gui, qtbot):
    win = gui.win
    first, second = _project(gui, "Zeta"), _project(gui, "Alfa")
    win.state.touch_recent(first["id"])
    win.state.projects_changed.emit()
    gui.idle()
    assert win.project_combo.itemData(1) == first["id"]
    win.project_combo.setCurrentIndex(win.project_combo.findData(second["id"]))
    qtbot.waitUntil(lambda: win.state.pid == second["id"], timeout=15000)
    gui.idle()
    assert win.project_combo.currentData() == second["id"]
    assert win.state.recent_projects()[:2] == [second["id"], first["id"]]
    assert win.mode_badge.text() == "Proyecto"
    assert not gui.errors


@gui_test
def test_palette_filters_page_actions_and_executes_with_keyboard(gui, qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    win, called = gui.win, []
    home = win.page("inicio")
    monkeypatch.setattr(home, "actions", lambda: [("Acción única", "play", lambda: called.append(True), None)])
    palette = win.command_palette()
    palette.search.setText("accion unica")
    # P5-06: la última fila siempre ofrece buscar el texto en todos los proyectos.
    assert palette.results.count() == 2 and palette.results.item(1).text().startswith("Buscar «accion unica»")
    qtbot.keyClick(palette.search, Qt.Key_Return)
    assert called == [True] and not palette.isVisible()
    palette = win.command_palette()
    palette.search.setText("proyectos")
    qtbot.keyClick(palette.search, Qt.Key_Down)
    qtbot.keyClick(palette.search, Qt.Key_Up)
    qtbot.keyClick(palette.search, Qt.Key_Return)
    assert win.current_key() == "proyectos"
    palette = win.command_palette()
    palette.search.setText("sin coincidencias xyz")
    assert palette.results.count() == 1 and palette.results.item(0).text().startswith("Buscar «")
    qtbot.keyClick(palette.search, Qt.Key_Escape)
    assert not palette.isVisible()


@gui_test
def test_f1_help_and_f5_refresh(gui, monkeypatch):
    from PySide6.QtWidgets import QTableWidget
    _shortcut(gui.win, "F1").trigger()
    dialog = gui.win._shortcuts_dialog
    assert dialog.isVisible() and dialog.windowTitle() == "Atajos de teclado"
    table = dialog.findChild(QTableWidget)
    keys = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert {"Ctrl+1", "Ctrl+0", "Ctrl+K", "Ctrl+B", "F9", "F5", "Supr", "Enter", "Ctrl+rueda"} <= set(keys)
    dialog.close()
    calls = []
    monkeypatch.setattr(gui.win.page("inicio"), "refresh", lambda: calls.append(True))
    _shortcut(gui.win, "F5").trigger()
    assert calls == [True]


@gui_test
def test_open_file_navigates_and_delegates(gui, monkeypatch, tmp_path):
    page = gui.win.page("importar")
    loaded = []
    monkeypatch.setattr(page, "load_file", loaded.append, raising=False)
    path = tmp_path / "tareas.XLSX"
    gui.win.open_file(path)
    assert gui.win.current_key() == "importar"
    assert loaded == [str(path)]
    gui.win.open_file(tmp_path / "ignorado.txt")
    assert loaded == [str(path)]


@gui_test
def test_home_welcome_and_real_schedule_checklist(gui, qtbot):
    home, client = gui.win.page("inicio"), gui.win.state.client
    assert home.welcome.isVisible() and not home.dashboard.isVisible()
    assert len(home.welcome_cards) == 4
    project = _project(gui)
    pid = project["id"]
    client.create_resource({"name": "Cuadrilla UX2", "project_id": pid, "skills": ["UX2"]})
    client.create_activities(pid, [{"code": "A", "name": "Montaje", "duration_days": 1,
                                    "work_hours": 8, "required_skill": "UX2"}])
    gui.win.state.open_project(pid)
    gui.idle()
    assert home.dashboard.isVisible() and not home.welcome.isVisible()
    assert home.checklist.isVisible() and len(home.step_buttons) == 6
    assert home.checklist_state[:3] == [True, True, False]
    home.step_buttons[2].click()
    qtbot.waitUntil(lambda: home.checklist_state[2], timeout=60000)
    gui.idle()
    assert home.step_statuses[2].text() == "Hecho"
    assert not gui.win.state.is_busy(f"solve:{pid}")
    assert not gui.errors


@gui_test
def test_home_empty_project_and_evm_422_are_tolerated(gui, monkeypatch):
    from ganttier.gui.client import ApiError
    home = gui.win.page("inicio")
    project = _project(gui)
    gui.win.state.open_project(project["id"])
    gui.idle()
    assert not home.checklist_state[2]
    assert not home.step_buttons[2].isEnabled()
    # Inicio usa el resumen liviano del servidor (/projects/{pid}/summary), no descarga el programa.
    client = gui.win.state.client
    client.post(f"/projects/{project['id']}/activities", [{"code": "A", "name": "A", "work_hours": 8,
                "planned_start": "2026-01-05", "planned_finish": "2026-01-05"}])
    summary = client.get(f"/projects/{project['id']}/summary")
    assert summary["scheduled"] and summary["activities"] == 1 and summary["work_hours"] == 8
    home.refresh()
    gui.idle()
    assert home.checklist_state[2]
    assert not gui.errors


@gui_test
def test_solve_guard_failure_and_project_switch(gui, monkeypatch):
    from ganttier.gui.pages import home as home_module
    home = gui.win.page("inicio")
    first, second = _project(gui, "Primero"), _project(gui, "Segundo")
    gui.win.state.open_project(first["id"])
    gui.idle()
    home._has_tasks = True
    pending = []
    monkeypatch.setattr(home_module, "run_task", lambda fn, done=None, on_error=None, **kw: pending.append((fn, done, on_error)))
    home.generate()
    home.generate()
    assert len(pending) == 1 and gui.win.state.is_busy(f"solve:{first['id']}")
    pending[0][2](RuntimeError("Fallo simulado"))
    assert not gui.win.state.is_busy(f"solve:{first['id']}")
    assert str(gui.errors[-1]) == "Fallo simulado"
    pending.clear()
    home.generate()
    complete = pending[0][1]
    gui.win.state.open_project(second["id"])
    complete({"applied": True, "feasible": True})
    assert not gui.win.state.is_busy(f"solve:{first['id']}")
    assert not home.checklist_state[2]


@gui_test
def test_settings_restore_recent_limit_and_missing_project(gui, qtbot):
    from ganttier.gui.app import create_window
    from ganttier.gui import app as gui_app
    win = gui.win
    project = _project(gui)
    win.state.open_project(project["id"])
    gui.idle()
    for pid in range(100, 112):
        win.state.touch_recent(pid)
    win.state.touch_recent(project["id"])
    assert len(win.state.recent_projects()) == 8
    assert len(set(win.state.recent_projects())) == 8
    win.toggle_sidebar()
    win.go("proyectos")
    gui.idle()
    # Conservar el servidor al cerrar solo la ventana original.
    win._server = None
    win.close()
    restored, _ = create_window(base_url=gui.server.base_url, theme="light", token=gui.server.token)
    try:
        restored.show()
        qtbot.waitUntil(lambda: restored.state.pid == project["id"], timeout=15000)
        gui.idle()
        assert restored.sidebar_collapsed and restored.current_key() == "proyectos"
        assert restored.project_combo.currentData() == project["id"]
        assert gui.settings.value("geometry") is not None
        assert gui.settings.value("window_state") is not None
    finally:
        restored.close()
    gui.settings.setValue("last_project", 9999999)
    missing, _ = create_window(base_url=gui.server.base_url, theme="light", token=gui.server.token)
    try:
        missing.show()
        gui.idle()
        assert missing.state.pid is None
        assert not gui.errors
    finally:
        missing.close()
        gui_app._MAIN = win


@gui_test
def test_theme_recolors_shell_and_toast_is_used(gui, monkeypatch):
    from ganttier.gui.widgets import common
    calls = []
    monkeypatch.setattr(common, "toast", lambda text, kind="ok": calls.append((text, kind)), raising=False)
    before = gui.win.theme_btn.icon().cacheKey()
    gui.win.toggle_theme()
    assert gui.win.theme_name == "dark"
    assert gui.win.theme_btn.icon().cacheKey() != before
    assert gui.win.theme_btn.toolTip() == "Tema claro"
    gui.win.notify("Guardado", "ok")
    assert calls == [("Guardado", "ok")]
    gui.win.busy_start("Operación de prueba")
    assert gui.win.busy_label.text() == "Operación de prueba"
    gui.win.busy_end()
    assert gui.win.busy_label.text() == "Listo"
