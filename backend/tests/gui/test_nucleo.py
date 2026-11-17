"""Regresiones F1. Ejecutar desde backend con QT_QPA_PLATFORM=offscreen.

python -B -m pytest -p no:cacheprovider tests/gui/test_nucleo.py \
    --basetemp=../datos/pytest_gui_f1
"""
from __future__ import annotations

import importlib.util
import os
from threading import Event
from types import SimpleNamespace

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTEST_QT_API"] = "pyside6"
REQUIRED = ("PySide6", "pytestqt", "fastapi", "uvicorn", "httpx", "matplotlib",
            "holidays", "openpyxl", "docx", "reportlab", "multipart")
MISSING = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
pytestmark = pytest.mark.skipif(bool(MISSING), reason="Faltan dependencias: " + ", ".join(MISSING))


@pytest.fixture
def gui(tmp_path, request, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "ganttier.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    from PySide6.QtCore import QSettings
    from ganttier import services
    from ganttier.gui import app as gui_app
    from ganttier.gui.widgets import common

    monkeypatch.setattr(services, "DATA_DIR", tmp_path / "datos")
    app = request.getfixturevalue("qapp")
    qtbot = request.getfixturevalue("qtbot")
    old_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    errors = []
    monkeypatch.setattr(common, "show_error", lambda exc, parent=None: errors.append(exc))
    previous_main = gui_app.main_window()
    win, server = gui_app.create_window(theme="light")
    from ganttier.gui.pages import projects
    monkeypatch.setattr(projects, "show_error", lambda exc, parent=None: errors.append(exc))
    win.show()
    qtbot.waitUntil(lambda: not common._active, timeout=60000)
    try:
        yield SimpleNamespace(win=win, app=app, errors=errors, server=server)
    finally:
        qtbot.waitUntil(lambda: not common._active, timeout=60000)
        win.close()
        win.state.client.close()
        if server:
            server.stop()
        gui_app._MAIN = previous_main
        QSettings.setDefaultFormat(old_format)


def test_less_than_nulls_and_nan(qapp):
    from PySide6.QtCore import Qt
    from ganttier.gui.widgets.common import Column, DictTableModel, SortProxy
    model = DictTableModel([Column("v", "Valor")], [{"v": x} for x in (None, None, float("nan"), 2, 1)])
    proxy = SortProxy()
    proxy.setSourceModel(model)
    indices = [model.index(i, 0) for i in range(5)]
    for i in range(3):
        for j in range(3):
            assert not proxy.lessThan(indices[i], indices[j])
        assert not proxy.lessThan(indices[i], indices[3])
        assert proxy.lessThan(indices[3], indices[i])
    for order in (Qt.AscendingOrder, Qt.DescendingOrder):
        proxy.sort(0, order)
        rows = [proxy.mapToSource(proxy.index(i, 0)).row() for i in range(5)]
        assert set(rows[-3:]) == {0, 1, 2}
        assert rows[:2] == ([4, 3] if order == Qt.AscendingOrder else [3, 4])


def test_run_task_gui_thread_callback_errors_and_chaining(gui, qtbot):
    from PySide6.QtCore import QThread
    from ganttier.gui.widgets import common
    threads, active_during_callback = [], []

    def nested(value):
        threads.append(QThread.currentThread())
        active_during_callback.append(bool(common._active))
        assert value == 43

    def done(value):
        threads.append(QThread.currentThread())
        active_during_callback.append(bool(common._active))
        common.run_task(lambda: value + 1, nested)
        raise ValueError("fallo del callback")

    common.run_task(lambda: 42, done, busy="Prueba")
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    assert threads == [gui.app.thread(), gui.app.thread()]
    assert all(active_during_callback)
    assert len(gui.errors) == 1 and str(gui.errors[0]) == "fallo del callback"
    assert gui.win._busy == 0

    def failed(exc):
        threads.append(QThread.currentThread())
        assert common._active
        assert isinstance(exc, ValueError)
        raise RuntimeError("fallo del manejador")

    def work():
        raise ValueError("fallo de trabajo")

    common.run_task(work, on_error=failed)
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    assert threads[-1] == gui.app.thread()
    assert str(gui.errors[-1]) == "fallo del manejador"


def test_operations_are_exclusive(gui):
    state = gui.win.state
    changes = []
    state.operations_changed.connect(lambda: changes.append(True))
    assert state.try_begin("solve:1")
    assert not state.try_begin("solve:1")
    assert state.is_busy("solve:1")
    assert state.try_begin("solve:2")
    state.end("solve:1")
    state.end("solve:1")
    assert not state.is_busy("solve:1") and state.is_busy("solve:2")
    state.end("solve:2")
    assert len(changes) == 4


def test_open_project_sync_atomic_failure_and_signals(gui, monkeypatch):
    from ganttier.gui.state import AppState
    client = gui.win.state.client
    first = client.create_project({"name": "A", "mode": "proyecto", "start_date": "2026-01-01"})
    second = client.create_project({"name": "B", "mode": "proyecto", "start_date": "2026-02-01"})
    state = AppState(client)
    state.open_project(first["id"])
    before = (state.project, state.calendars, state.resources)
    snapshots = []
    state.project_changed.connect(lambda _: snapshots.append((state.project, state.calendars, state.resources)))
    state.catalogs_changed.connect(lambda: snapshots.append((state.project, state.calendars, state.resources)))
    state.schedule_changed.connect(lambda: snapshots.append((state.project, state.calendars, state.resources)))

    def broken_resources(pid=None):
        assert state.project is before[0] and state.calendars is before[1]
        raise RuntimeError("catálogo no disponible")

    with monkeypatch.context() as patch:
        patch.setattr(client, "resources", broken_resources)
        with pytest.raises(RuntimeError, match="catálogo"):
            state.open_project(second["id"])
    assert all(current is old for current, old in zip((state.project, state.calendars, state.resources), before))
    assert not snapshots
    state.open_project(second["id"])
    assert state.pid == second["id"]
    assert len(snapshots) == 3
    assert all(s == (state.project, state.calendars, state.resources) for s in snapshots)


def test_async_open_generation_and_failure(gui, qtbot):
    from PySide6.QtCore import QThread
    from ganttier.gui.state import AppState
    from ganttier.gui.widgets import common
    release, started = Event(), Event()
    worker_threads, published, done = [], [], []

    def project(pid):
        worker_threads.append(QThread.currentThread())
        if pid == 1:
            started.set()
            if not release.wait(10):
                raise TimeoutError("prueba sin liberar")
        return {"id": pid}

    def resources(pid=None):
        if pid == 3:
            raise RuntimeError("sin recursos")
        return [{"project_id": pid}]

    state = AppState(SimpleNamespace(project=project, calendars=lambda: [{"id": 7}], resources=resources))
    state.project_changed.connect(lambda p: published.append((p["id"], QThread.currentThread(), state.resources)))
    state.open_project_async(1, lambda p: done.append(p["id"]))
    try:
        qtbot.waitUntil(started.is_set, timeout=5000)
        state.open_project_async(2, lambda p: done.append(p["id"]))
        qtbot.waitUntil(lambda: state.pid == 2, timeout=5000)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    assert published == [(2, gui.app.thread(), [{"project_id": 2}])]
    assert done == [2]
    assert all(t != gui.app.thread() for t in worker_threads)
    before = (state.project, state.calendars, state.resources)
    state.open_project_async(3)
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    assert (state.project, state.calendars, state.resources) == before
    assert str(gui.errors[-1]) == "sin recursos"


def test_shortcuts_all_pages_and_sidebar(gui, monkeypatch):
    from PySide6.QtCore import Qt
    from ganttier.gui.app import PAGES
    win = gui.win
    visited = []
    monkeypatch.setattr(win, "go", visited.append)
    actions = {action.data(): action for action in win.actions() if action.data()}
    assert set(actions) == {spec.key for spec in PAGES}
    for spec in PAGES:
        assert actions[spec.key].shortcuts()
        actions[spec.key].trigger()
    assert visited == [spec.key for spec in PAGES]
    assert "Ctrl+0" in [s.toString() for s in actions[PAGES[9].key].shortcuts()]
    assert "Ctrl+Shift+1" in [s.toString() for s in actions[PAGES[10].key].shortcuts()]
    assert win.nav.focusPolicy() == Qt.StrongFocus


def test_page_coalesces_and_can_retry(gui, qtbot):
    from ganttier.gui.pages.base import Page

    class CounterPage(Page):
        calls = 0
        fail = False

        def refresh(self):
            self.calls += 1
            if self.fail:
                raise RuntimeError("refresh falló")

    page = CounterPage(gui.win.state)
    qtbot.addWidget(page)
    page.show()
    page.activate()
    gui.win.state.project_changed.emit(None)
    gui.win.state.schedule_changed.emit()
    qtbot.waitUntil(lambda: page.calls == 2)
    gui.app.processEvents()
    assert page.calls == 2
    page.fail = True
    page._stale = True
    page.activate()
    assert page._stale and str(gui.errors[-1]) == "refresh falló"
    page.fail = False
    page.activate()
    assert not page._stale


def test_button_exceptions_and_form_buddies(gui, qtbot):
    from PySide6.QtWidgets import QLabel
    from ganttier.gui.widgets.common import Field, FormDialog, button

    def broken():
        raise RuntimeError("botón falló")

    btn = button("Prueba", broken)
    qtbot.addWidget(btn)
    btn.click()
    assert str(gui.errors[-1]) == "botón falló"
    dlg = FormDialog("Prueba", [Field("name", "Nombre")])
    qtbot.addWidget(dlg)
    label = next(x for x in dlg.findChildren(QLabel) if x.text() == "Nombre")
    assert label.buddy() is dlg._editors["name"]


def test_error_dialog_failure_is_not_recursive(gui, qtbot, monkeypatch, caplog):
    from ganttier.gui.widgets import common
    calls = []

    def broken_dialog(exc, parent=None):
        calls.append(exc)
        raise RuntimeError("aviso roto")

    def broken_callback(_):
        raise ValueError("callback roto")

    monkeypatch.setattr(common, "show_error", broken_dialog)
    common.run_task(lambda: None, broken_callback)
    qtbot.waitUntil(lambda: not common._active, timeout=5000)
    assert len(calls) == 1
    assert "aviso roto" in caplog.text


def test_catalogs_capture_pid_and_publish_in_gui(gui, qtbot):
    from PySide6.QtCore import QThread
    from ganttier.gui.state import AppState
    from ganttier.gui.widgets import common
    started, release = Event(), Event()
    requested, published = [], []

    def calendars():
        started.set()
        if not release.wait(10):
            raise TimeoutError("prueba sin liberar")
        return [{"id": 8}]

    def resources(pid=None):
        requested.append(pid)
        return [{"project_id": pid}]

    state = AppState(SimpleNamespace(calendars=calendars, resources=resources))
    state.project = {"id": 1}
    state.catalogs_changed.connect(lambda: published.append(QThread.currentThread()))
    common.run_task(state.reload_catalogs)
    try:
        qtbot.waitUntil(started.is_set, timeout=5000)
        state.open_project(None)  # Debe cerrar sin consultar la API ni esperar al trabajador.
    finally:
        release.set()
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    assert requested == [1]
    assert state.project is None and state.calendars == [] and state.resources == []
    assert published == [gui.app.thread()]
    common.run_task(state.reload_catalogs)
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    assert requested == [1, None]
    assert state.calendars == [{"id": 8}]
    assert published == [gui.app.thread(), gui.app.thread()]


@pytest.mark.parametrize("action", ["demo", "duplicate", "new_project"])
def test_project_creation_guards_unlock_on_failure(gui, monkeypatch, action):
    from ganttier.gui.pages import projects
    page = gui.win.page("proyectos")
    pending = []
    monkeypatch.setattr(projects, "run_task", lambda fn, done=None, on_error=None, **kw:
                        pending.append((fn, done, on_error)))
    monkeypatch.setattr(page, "selected", lambda: {"id": 1})
    invoke = (lambda: page.demo("proyecto")) if action == "demo" else getattr(page, action)
    invoke()
    invoke()
    assert len(pending) == 1
    assert all(not btn.isEnabled() for btn in page._creation_buttons)
    pending[0][2](RuntimeError("fallo esperado"))
    assert all(btn.isEnabled() for btn in page._creation_buttons)
    assert not gui.win.state.is_busy(page._creation_key)
    assert str(gui.errors[-1]) == "fallo esperado"


def test_new_project_catalogs_and_post_are_off_gui(gui, qtbot, monkeypatch):
    from PySide6.QtCore import QThread
    from ganttier.gui.pages import projects
    from ganttier.gui.widgets import common
    page = gui.win.page("proyectos")
    client = gui.win.state.client
    threads, created = [], []
    original_calendars, original_create = client.calendars, client.create_project

    def calendars():
        threads.append(QThread.currentThread())
        return original_calendars()

    def create(values):
        threads.append(QThread.currentThread())
        return original_create(values)

    monkeypatch.setattr(client, "calendars", calendars)
    monkeypatch.setattr(client, "create_project", create)
    monkeypatch.setattr(projects.FormDialog, "exec", lambda self: 1)
    monkeypatch.setattr(projects.FormDialog, "values", lambda self:
                        {"name": "Nuevo F1", "mode": "proyecto", "start_date": "2026-01-01"})
    monkeypatch.setattr(page, "_created", created.append)
    page.new_project()
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    assert len(threads) == 2 and all(t != gui.app.thread() for t in threads)
    assert len(created) == 1 and created[0]["name"] == "Nuevo F1"
    assert all(btn.isEnabled() for btn in page._creation_buttons)
    assert not gui.errors


def test_close_waits_for_callbacks_and_followup_tasks(gui, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from ganttier.gui.widgets import common
    order = []
    stop, close = gui.server.stop, gui.win.state.client.close
    monkeypatch.setattr(common, "confirm", lambda *a, **kw: True)

    def stop_server():
        assert not common._active
        order.append("server")
        stop()

    def close_client():
        order.append("client")
        close()

    def done(_):
        order.append("callback")
        common.run_task(lambda: 2, lambda _: order.append("followup"))

    monkeypatch.setattr(gui.server, "stop", stop_server)
    monkeypatch.setattr(gui.win.state.client, "close", close_client)
    common.run_task(lambda: 1, done)
    event = QCloseEvent()
    gui.win.closeEvent(event)
    assert event.isAccepted()
    assert order == ["callback", "followup", "server", "client"]


def test_close_can_cancel_for_tasks_and_drafts(gui, qtbot, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox
    from ganttier.gui.widgets import common
    monkeypatch.setattr(common, "confirm", lambda *a, **kw: False)
    monkeypatch.setattr(gui.win, "prompt_on_close", True, raising=False)   # offscreen no pregunta por omisión
    common.run_task(lambda: 1)
    event = QCloseEvent()
    gui.win.closeEvent(event)
    assert not event.isAccepted() and not gui.win._closed
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    page = gui.win.page("proyectos")
    with monkeypatch.context() as patch:
        patch.setattr(page, "has_unsaved", lambda: True, raising=False)
        patch.setattr(QMessageBox, "exec", lambda self: 0)
        gui.win.closeEvent(event)
        assert not event.isAccepted() and not gui.win._closed


def test_close_timeout_cierra_y_descarta_callbacks_tardios(gui, qtbot, monkeypatch):
    """D: el vencimiento aceptado cierra; no vuelve a dejar la ventana abierta.

    Los borradores aún permiten cancelar antes de aceptar. Una tarea que excede
    el límite no debe publicar resultados sobre la ventana ya cerrada.
    """
    from PySide6.QtGui import QCloseEvent
    from ganttier.gui import app as gui_app
    from ganttier.gui.server import _lock_for
    from ganttier.gui.widgets import common
    from ganttier import db
    started, release = Event(), Event()
    callbacks = []
    monkeypatch.setattr(gui_app, "CLOSE_TASK_WAIT_SECONDS", 0.03)

    def delayed_work():
        started.set()
        assert release.wait(10)
        return "resultado tardío"

    common.run_task(delayed_work, callbacks.append)
    try:
        qtbot.waitUntil(started.is_set, timeout=5000)
        event = QCloseEvent()
        gui.win.closeEvent(event)
        assert event.isAccepted() and gui.win._closed
        assert not gui.server._thread.is_alive()
        assert gui.win.state.client._http.is_closed
        lock = _lock_for(db.database_path())
        lock.unlock()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not common._active, timeout=10000)
    assert callbacks == []
