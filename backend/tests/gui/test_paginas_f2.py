"""Regresiones F2: Qt offscreen, sin BD del usuario y callbacks controlados.

Ejecutar con --basetemp dentro del espacio de trabajo.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture(scope="session")
def f2_session(tmp_path_factory, request):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    data = tmp_path_factory.mktemp("f2_gui")
    with pytest.MonkeyPatch.context() as patch:
        for key, value in {"QT_QPA_PLATFORM": "offscreen", "GANTTIER_DB": data / "test.db",
                           "GANTTIER_DATA_DIR": data, "MPLCONFIGDIR": data / "matplotlib",
                           "TEMP": data, "TMP": data}.items():
            patch.setenv(key, str(value))
        yield request.getfixturevalue("qapp")


@pytest.fixture
def gui(f2_session, request, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from ganttier.gui.pages import resources, calendars, maintenance, macro

    class State(QObject):
        project_changed = Signal(object)
        schedule_changed = Signal()
        catalogs_changed = Signal()
        operations_changed = Signal()

        def __init__(self):
            super().__init__()
            self.project = dict(id=1, mode="mantenimiento", start_date="2026-01-01", end_date="2026-12-31")
            self.resources, self.calendars = [], []
            self.client = SimpleNamespace()
            self.keys = set()

        @property
        def pid(self):
            return self.project["id"] if self.project else None

        @property
        def mode(self):
            return (self.project or {}).get("mode")

        def bump_schedule(self):
            self.schedule_changed.emit()

        def try_begin(self, key):
            if key in self.keys:
                return False
            self.keys.add(key)
            self.operations_changed.emit()
            return True

        def end(self, key):
            self.keys.discard(key)
            self.operations_changed.emit()

        def is_busy(self, key):
            return key in self.keys

    state = State()
    jobs, notices, errors = [], [], []

    def run(fn, done=None, on_error=None, **kwargs):
        jobs.append((fn, done, on_error))

    def complete(value=None, error=None):
        _, done, failed = jobs.pop(0)
        if error is not None:
            assert failed is not None
            failed(error)
        elif done:
            done(value)

    modules = dict(resources=resources, calendars=calendars, maintenance=maintenance, macro=macro)
    qtbot = request.getfixturevalue("qtbot")
    for mod in modules.values():
        monkeypatch.setattr(mod, "run_task", run)
        monkeypatch.setattr(mod, "notify", lambda message, *a: notices.append(message))
        monkeypatch.setattr(mod, "show_error", lambda error, *a: errors.append(str(error)))
        if hasattr(mod, "confirm"):
            monkeypatch.setattr(mod, "confirm", lambda *a, **k: True)
    classes = dict(resources=resources.ResourcesPage, calendars=calendars.CalendarsPage,
                   maintenance=maintenance.MaintenancePage, macro=macro.MacroPage)

    def make(kind):
        page = classes[kind](state)
        qtbot.addWidget(page)
        return page

    yield SimpleNamespace(state=state, jobs=jobs, notices=notices, errors=errors,
                          complete=complete, make=make, modules=modules, qtbot=qtbot)


def loaded(gui, kind):
    page = gui.make(kind)
    gui.state.client.calendars = lambda: []
    page.refresh()
    if kind == "resources":
        gui.complete(([dict(id=11, name="A", skills=["eléctrico"], max_units=1, capacity_hours_day=8)], []))
        page.table.selectRow(0)
    elif kind == "calendars":
        gui.complete(([dict(gui.modules[kind].DEFAULT, id=11, name="A")], None))
        gui.complete([])  # días del calendario
    elif kind == "maintenance":
        from dataclasses import asdict
        from ganttier.domain import PeriodicTask
        task = asdict(PeriodicTask(id=11, project_id=1, code="A", name="A"))
        task.update(location="Instalación A", requires_outage=True)
        gui.complete(([task], dict(starts=["2026-01-01"], locations=["Instalación A"],
                                   outages=[True], kinds=["mantenimiento"]), None))
        page.table.selectRow(0)
    else:
        gui.complete(([dict(id=11, code="A", name="A", work_hours=8)],
                      [dict(key="2026-01", start="2026-01-01", end="2026-01-31", weekdays=22)], [], True))
        gui.complete([8])  # totales
        page.matrix.selected.add(11)
    return page


@pytest.mark.parametrize("kind", ["resources", "calendars", "maintenance", "macro"])
def test_project_change_clears_hidden_page_immediately(gui, kind):
    page = loaded(gui, kind)
    if hasattr(page, "search"):
        page.search.setText("A")
    if kind == "resources":
        page.skill.setCurrentIndex(1)
    elif kind == "maintenance":
        page.location.setCurrentIndex(1)
        page.outage_only.setChecked(True)
        page.replace.setChecked(True)
    elif kind == "macro":
        page.only_unassigned.setChecked(True)
        page.hard.setChecked(True)
    gui.state.project = dict(gui.state.project, id=2)
    gui.state.project_changed.emit(gui.state.project)
    if hasattr(page, "search"):
        assert page.search.text() == ""
    if kind == "resources":
        assert page.rows == [] and page.model.rowCount() == page.capacity_model.rowCount() == 0
        assert page.skill.currentData() == "" and page.selected() is None
    elif kind == "calendars":
        assert page.editing is None and page.list.count() == 0
        assert page.days == [] and page.exception_model.rowCount() == 0
    elif kind == "maintenance":
        assert page.tasks == [] and page.model.rowCount() == page.calendar_model.rowCount() == 0
        assert page.location.currentData() == "" and not page.outage_only.isChecked()
        assert not page.replace.isChecked() and page.selected() is None
    else:
        assert page.rows == page.visible == page.months == []
        assert not page.by_id and not page.matrix.selected
        assert not page.only_unassigned.isChecked() and not page.hard.isChecked()


@pytest.mark.parametrize("kind", ["resources", "calendars", "maintenance", "macro"])
def test_stale_data_pid_blocks_mutations_even_without_signal(gui, kind):
    page = loaded(gui, kind)
    gui.state.project = dict(gui.state.project, id=2)
    if kind == "resources":
        page.edit()
        page.save_resource({"name": "Obsoleto"}, 11)
        page.delete()
    elif kind == "calendars":
        page.edit_properties()
        page.save()
        page.delete()
        page.add_exception(dict(date="2026-01-01", working=False))
    elif kind == "maintenance":
        page.edit_task()
        page.save_task({"code": "X", "name": "Obsoleto"}, 11)
        page.delete_task()
        page.generate()
    else:
        page.assign_range([11], 0, 0)
        page.clear([11])
        page.save()
        page.generate()
        assert not page.pending
    assert not gui.jobs
    assert gui.notices


def test_calendar_post_survives_failed_reload_without_duplicate(gui):
    page = loaded(gui, "calendars")
    page.set_calendar(dict(gui.modules["calendars"].DEFAULT, name="Nuevo"))
    calls = []
    def create(payload):
        calls.append("POST")
        return dict(payload, id=42)
    def update(cid, payload):
        calls.append(("PUT", cid))
        return dict(payload, id=cid)
    gui.state.client.create_calendar = create
    gui.state.client.update_calendar = update
    page.save()
    gui.complete(gui.jobs[0][0]())
    assert page.editing["id"] == 42  # publicado antes de consultar catálogos/días
    while gui.jobs:
        gui.complete(error=RuntimeError("recarga caída"))
    assert "Guardado; no se pudo actualizar la vista. Usa Actualizar." in gui.notices
    page.save()
    gui.complete(gui.jobs[0][0]())
    assert calls == ["POST", ("PUT", 42)]


@pytest.mark.parametrize("kind", ["maintenance", "macro"])
def test_typing_debounces_and_does_not_rebuild_calendar(gui, monkeypatch, kind):
    page = loaded(gui, kind)
    calls, calendars = [], []
    method = "render_tasks" if kind == "maintenance" else "filter_rows"
    original = getattr(page, method)
    def render(*args):
        calls.append(page.search.text())
        return original(*args)
    monkeypatch.setattr(page, method, render)
    if kind == "maintenance":
        monkeypatch.setattr(page, "render_calendar", lambda: calendars.append(True))
    gui.qtbot.keyClicks(page.search, "ab")
    gui.qtbot.wait(140)
    gui.qtbot.keyClicks(page.search, "c")
    gui.qtbot.wait(140)
    assert not calls
    gui.qtbot.waitUntil(lambda: bool(calls), timeout=1000)
    assert calls == ["abc"]
    assert not calendars


@pytest.mark.parametrize("kind", ["maintenance", "macro"])
@pytest.mark.parametrize("failed", [False, True])
def test_solve_lock_released_on_success_and_error(gui, kind, failed):
    page = loaded(gui, kind)
    page.generate()
    assert gui.state.is_busy("solve:1")
    assert not page.generate_button.isEnabled()
    page.generate()
    assert len(gui.jobs) == 1
    if failed:
        gui.complete(error=RuntimeError("solver caído"))
    else:
        gui.complete(dict(status="factible", applied=True, created=2, kept=3))
        if kind == "maintenance":
            assert "2" in page.summary.text() and "3" in page.summary.text()
    assert not gui.state.is_busy("solve:1")
    assert page.generate_button.isEnabled()


@pytest.mark.parametrize("kind", ["resources", "calendars", "maintenance", "macro"])
def test_loading_disables_data_actions_and_ignores_old_response(gui, kind):
    page = loaded(gui, kind)
    page.refresh()
    old_job = gui.jobs.pop(0)
    if kind in ("maintenance", "macro"):
        assert not page.generate_button.isEnabled()
        page.generate()
    elif kind == "resources":
        assert not page.edit_button.isEnabled()
        page.save_resource({"name": "Bloqueado"}, 11)
    else:
        assert not page.list.isEnabled()
        page.save()
    assert not gui.jobs
    gui.state.project = dict(gui.state.project, id=2)
    gui.state.project_changed.emit(gui.state.project)
    old_job[1](None)  # un resultado viejo se descarta antes de interpretarlo
    assert page.data_pid != 1


def test_solver_exclusion_across_pages(gui):
    maintenance = loaded(gui, "maintenance")
    macro = loaded(gui, "macro")
    maintenance.generate()
    assert not macro.generate_button.isEnabled()
    macro.generate()
    assert len(gui.jobs) == 1
    gui.complete(error=RuntimeError("fallo controlado"))
    assert macro.generate_button.isEnabled()
    macro.generate()
    assert not maintenance.generate_button.isEnabled()
    assert gui.state.is_busy("solve:1")
    gui.complete(error=RuntimeError("fallo controlado"))
    assert maintenance.generate_button.isEnabled()


def test_macro_releases_solver_lock_when_saving_windows_fails(gui):
    page = loaded(gui, "macro")
    page.assign_range([11], 0, 0)
    gui.complete([8])
    page.generate()
    assert gui.state.is_busy("solve:1")
    gui.complete(error=RuntimeError("no se guardó la ventana"))
    assert not gui.jobs  # no debe arrancar solve después del error del guardado
    assert not gui.state.is_busy("solve:1")
    assert page.pending and not page.saving and not page.generating


@pytest.mark.parametrize("kind", ["maintenance", "macro"])
def test_generation_without_optional_state_lock_api(gui, monkeypatch, kind):
    page = loaded(gui, kind)
    for name in ("try_begin", "end", "is_busy"):
        monkeypatch.setattr(gui.state, name, None)
    page.generate()
    assert len(gui.jobs) == 1
    gui.complete(error=RuntimeError("fallo controlado"))
    assert page.generate_button.isEnabled()
