"""Q2: integración con ventana/servidor reales y BD temporal.

Desde backend:
python -B -m pytest -p no:cacheprovider tests/gui/test_planning_pages.py \
    --basetemp=../../trabajos/pytest_gui_q2

Las pruebas de funciones puras se ejecutan incluso sin Qt; las de integración
se omiten explícitamente cuando faltan sus dependencias.
"""
from __future__ import annotations

import ast
from bisect import bisect_right
import calendar
from collections import Counter
from datetime import date, timedelta
import importlib.util
import json
import os
from pathlib import Path
import re
import threading

import pytest


REQUIRED = ("PySide6", "pytestqt", "fastapi", "uvicorn", "httpx", "matplotlib", "numpy",
            "openpyxl", "holidays", "multipart")
MISSING = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
gui_test = pytest.mark.skipif(bool(MISSING), reason="Faltan dependencias GUI/API: " + ", ".join(MISSING))


def pure_functions(module, names, extra=None):
    """Carga el cuerpo real de funciones puras sin importar la capa Qt opcional."""
    source = Path(__file__).resolve().parents[2] / "ganttier" / "gui" / "pages" / f"{module}.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    namespace = dict(date=date, timedelta=timedelta, calendar=calendar, bisect_right=bisect_right,
                     Counter=Counter, re=re, json=json)
    namespace.update(extra or {})
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def test_monthly_hours_preserves_hours_and_partial_months():
    f = pure_functions("macro", ["month_bounds", "window_of", "build_months", "monthly_hours"])
    rows = [dict(id=1, window_start="2024-01-15", window_end="2024-03-05", work_hours=51),
            dict(id=2, macro_month="2024-02", work_hours=29),
            dict(id=3, planned_start="2024-03-31", planned_finish="2024-03-31", work_hours=8),
            dict(id=4, work_hours=100)]
    months = f["build_months"]({"start_date": "2024-01-01", "end_date": "2024-03-31"}, rows)
    assert f["monthly_hours"](rows, {}, months) == pytest.approx([17, 58, 13])
    patches = {2: dict(id=2, window_start="2024-03-01", window_end="2024-03-31", macro_month="2024-03")}
    assert f["monthly_hours"](rows, patches, months) == pytest.approx([17, 29, 42])
    assert len(months) == 3


def test_macro_columnar_and_long_horizon():
    f = pure_functions("macro", ["decode_columns", "month_bounds", "window_of", "build_months", "monthly_hours"])
    rows = f["decode_columns"]({"ids": [1], "codes": ["A"], "names": ["Sin programa"],
                                "work_hours": [8], "macro_months": ["2027-02"], "window_hards": [True]})
    assert rows[0]["window_hard"] is True
    months = f["build_months"]({"start_date": "2026-01-01", "end_date": "2047-01-01"}, rows)
    assert len(months) == 253  # No truncar silenciosamente horizontes de más de veinte años.
    totals = f["monthly_hours"](rows, {}, months)
    assert sum(totals) == pytest.approx(8)
    assert totals[13] == pytest.approx(8)


def test_verification_and_warning_groups():
    f = pure_functions("optimize", ["verification_report", "applicable", "grouped_warnings", "parse_object"],
                       {"FEASIBLE": {"factible", "heuristico"}})
    assert f["applicable"]({"status": "factible", "verification": {"violations": []}})
    assert not f["applicable"]({"status": "factible", "verification": {"violations": ["Sobrecarga"]}})
    assert not f["applicable"]({"status": "infactible"})
    assert not f["applicable"]({"status": "factible", "feasible": False, "violations": []})
    assert f["grouped_warnings"](["SE-001: Fuera de ventana", "SE-002: Fuera de ventana"]) == ["Fuera de ventana (2 veces)"]
    assert f["parse_object"]('{"level": 1}') == {"level": 1}
    assert f["parse_object"]("inválido") == {}


@pytest.mark.parametrize("status", [404, 405, 422, 503])
def test_solve_fallback_only_when_endpoint_is_missing(status):
    class ApiError(Exception):
        def __init__(self, status):
            self.status = status

    class Client:
        calls = []

        def post(self, path, payload):
            self.calls.append(("solve", path, payload))
            raise ApiError(status)

        def optimize(self, pid, weights, options, apply):
            self.calls.append(("optimize", pid, apply))
            return {"status": "factible", "warnings": []}

    client = Client()
    f = pure_functions("optimize", ["solve_program"], {"ApiError": ApiError})
    if status in (404, 405):
        result = f["solve_program"](client, 12, {"window": 1}, {}, {"mode": "proyecto"})
        assert result["compatibility_fallback"]
        assert client.calls[-1] == ("optimize", 12, True)
    else:
        with pytest.raises(ApiError):
            f["solve_program"](client, 12, {"window": 1}, {}, {"mode": "proyecto"})
        assert len(client.calls) == 1


def test_solve_fallback_generates_missing_occurrences():
    class ApiError(Exception):
        status = 404

    class Client:
        calls = []

        def post(self, path, payload):
            raise ApiError()

        def columnar(self, pid):
            return {"ids": []}

        def generate_occurrences(self, pid, start, end, replace):
            self.calls.append((pid, start, end, replace))

        def optimize(self, *args, **kwargs):
            return {"status": "factible"}

    client = Client()
    f = pure_functions("optimize", ["solve_program"], {"ApiError": ApiError})
    f["solve_program"](client, 9, {}, {}, {"mode": "mantenimiento", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert client.calls == [(9, "2026-01-01", "2026-12-31", False)]


@pytest.fixture(scope="session")
def planning_application(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("gui_q2")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
        patch.setenv("GANTTIER_DB", str(root / "ganttier.db"))
        patch.setenv("GANTTIER_DATA_DIR", str(root / "datos"))
        patch.setenv("MPLCONFIGDIR", str(root / "matplotlib"))
        from PySide6.QtCore import QSettings, QThreadPool
        from ganttier import services
        from ganttier.gui.app import create_window
        from ganttier.gui.widgets import common

        patch.setattr(services, "DATA_DIR", root / "datos")
        app = request.getfixturevalue("qapp")
        previous_format = QSettings.defaultFormat()
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
        QSettings.setDefaultFormat(previous_format)


def wait_idle(qtbot):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not _active, timeout=120000)


@pytest.fixture
def gui(planning_application, qtbot, monkeypatch):
    from ganttier.gui.pages import macro, optimize, resources, calendars
    win, root, errors = planning_application
    wait_idle(qtbot)
    errors.clear()
    for module in (macro, optimize, resources, calendars):
        monkeypatch.setattr(module, "show_error", lambda exc, parent=None: errors.append(exc))
    win.go("proyectos")
    wait_idle(qtbot)
    project = win.state.client.demo("proyecto")
    win.state.open_project(project["id"])
    wait_idle(qtbot)
    yield win, project, root, errors
    wait_idle(qtbot)
    win.go("proyectos")
    assert not errors, [str(exc) for exc in errors]


def open_page(win, key, qtbot):
    from ganttier.gui.pages.base import Page
    win.go(key)
    page = win.page(key)
    assert isinstance(page, Page), "La aplicación mostró un placeholder en lugar de la página."
    wait_idle(qtbot)
    assert not page.loading
    return page


@gui_test
def test_macro_assign_drag_clear_save_discard(gui, qtbot):
    from PySide6.QtCore import QPoint, Qt
    win, project, _, _ = gui
    page = open_page(win, "macro", qtbot)
    ids = [r["id"] for r in page.rows[:3]]
    page.hard.setChecked(True)
    viewport = page.matrix.viewport()
    qtbot.mouseClick(viewport, Qt.LeftButton, pos=QPoint(20, page.matrix.HEADER + 10))
    qtbot.mouseClick(viewport, Qt.LeftButton, Qt.ShiftModifier,
                     pos=QPoint(20, page.matrix.HEADER + page.matrix.ROW + 10))
    assert page.matrix.selected == set(ids[:2])
    qtbot.mouseClick(viewport, Qt.LeftButton, Qt.ControlModifier,
                     pos=QPoint(20, page.matrix.HEADER + 2 * page.matrix.ROW + 10))
    assert page.matrix.selected == set(ids)
    qtbot.mouseClick(viewport, Qt.LeftButton, Qt.ControlModifier,
                     pos=QPoint(20, page.matrix.HEADER + 2 * page.matrix.ROW + 10))
    assert page.matrix.selected == set(ids[:2])
    start = QPoint(page.matrix.LEFT + 10, page.matrix.HEADER + 10)
    end = QPoint(page.matrix.LEFT + page.matrix.CELL + 10, page.matrix.HEADER + 10)
    qtbot.mousePress(viewport, Qt.LeftButton, pos=start)
    qtbot.mouseMove(viewport, end)
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=end)
    assert set(page.pending) == set(ids[:2])
    assert all(p["window_hard"] and p["macro_month"] is None for p in page.pending.values())
    page.save()
    wait_idle(qtbot)
    for aid in ids[:2]:
        activity = win.state.client.activity(aid)
        assert activity["window_start"] == page.months[0]["start"]
        assert activity["window_end"] == page.months[1]["end"]
        assert activity["window_hard"]
    page.assign_range([ids[2]], 0, 0)
    assert page.pending[ids[2]]["macro_month"] == page.months[0]["key"]
    page.discard()
    assert not page.pending
    qtbot.mouseDClick(viewport, Qt.LeftButton, pos=start)
    assert all(page.pending[aid]["window_start"] is None for aid in ids[:2])
    page.save()
    wait_idle(qtbot)
    assert win.state.client.activity(ids[0])["window_start"] is None
    page.search.setText(page.rows[0]["code"])
    page.only_unassigned.setChecked(True)
    assert page.visible
    page.search.clear()
    page.only_unassigned.setChecked(False)


@gui_test
def test_macro_generate_saves_before_solve(gui, qtbot, monkeypatch):
    win, project, _, _ = gui
    page = open_page(win, "macro", qtbot)
    page.hard.setChecked(False)
    aid = page.rows[0]["id"]
    page.assign_range([aid], 0, 1)
    post = win.state.client.post
    captured = []

    def record(path, json=None, **kwargs):
        if path.endswith("/schedule/solve"):
            captured.append(win.state.client.activity(aid))
            assert json["weights"]["window"] > .5
        return post(path, json, **kwargs)
    monkeypatch.setattr(win.state.client, "post", record)
    page.generate()
    wait_idle(qtbot)
    assert captured and captured[0]["window_end"] == page.months[1]["end"]
    assert not page.pending
    assert hasattr(page, "last_result")
    assert win.state.client.runs(project["id"])


@gui_test
def test_optimize_generate_without_previous_dates_then_simulate_apply(gui, qtbot):
    from ganttier.gui.pages.optimize import applicable
    win, project, _, _ = gui
    pid = project["id"]
    columns = win.state.client.columnar(pid)
    fields = ("planned_start", "planned_finish", "early_start", "early_finish", "late_start", "late_finish")
    win.state.client.patch_activities(pid, [dict(id=aid, **{k: None for k in fields}) for aid in columns["ids"]])
    # Las actividades ya iniciadas conservan su inicio real; el resto queda sin fechas (lista de tareas).
    acts = win.state.client.activities(pid, limit=10000)
    assert all(a["planned_start"] is None for a in acts if not a.get("actual_start"))
    assert sum(1 for a in acts if a["planned_start"] is None) > len(acts) // 2
    page = open_page(win, "optimizar", qtbot)
    page.method.setCurrentIndex(page.method.findData("heuristic"))
    page.time_limit.setValue(1)
    page.set_preset("Minimizar plazo")
    page.generate()
    wait_idle(qtbot)
    assert page.result and applicable(page.result)
    assert page.result["applied"]
    assert all(win.state.client.columnar(pid)["starts"])
    original = win.state.client.columnar(pid)["starts"]
    page.set_preset("Equilibrado")
    assert sum(page.weights().values()) == pytest.approx(1)
    page.execute(False)
    wait_idle(qtbot)
    assert applicable(page.result) and not page.result["applied"]
    assert original == win.state.client.columnar(pid)["starts"]
    assert page.apply_button.isEnabled()
    page.apply_last()
    wait_idle(qtbot)
    assert page.result["applied"]
    assert any(r["id"] == page.result["run_id"] and r["applied"] for r in page.runs_model.rows)
    assert page.before_plot.ax.patches and page.after_plot.ax.lines


@gui_test
def test_optimize_history_apply_and_verification(gui, qtbot):
    win, _, _, _ = gui
    page = open_page(win, "optimizar", qtbot)
    page.method.setCurrentIndex(page.method.findData("heuristic"))
    page.time_limit.setValue(1)
    page.set_preset("Minimizar plazo")
    page.execute(False)
    wait_idle(qtbot)
    run_id = page.result["run_id"]
    index = next(i for i, r in enumerate(page.runs_model.rows) if r["id"] == run_id)
    page.runs_table.selectRow(index)
    assert page.history_apply.isEnabled()
    page.apply_selected()
    wait_idle(qtbot)
    assert any(r["id"] == run_id and r["applied"] for r in page.runs_model.rows)
    page.result["verification"] = {"violations": []}
    page._render()
    assert "Programa factible: 0 violaciones" in page.messages.toPlainText()
    page.result.update(applied=False, verification={"violations": ["Capacidad excedida"]})
    page._render()
    page._buttons()
    assert "Capacidad excedida" in page.messages.toPlainText()
    assert not page.apply_button.isEnabled()


@gui_test
def test_resources_crud_form_filters_capacity(gui, qtbot, monkeypatch):
    from ganttier.gui.pages import resources
    win, project, _, _ = gui
    page = open_page(win, "recursos", qtbot)
    created = dict(name="Recurso Q2", kind="cuadrilla", skills=["Q2", "Protecciones"],
                   capacity_hours_day=6, max_units=3, calendar_id=None, cost_hour=100,
                   overtime_cost_hour=150, max_overtime_hours_day=2, active=True, email="")

    class Dialog:
        def __init__(self, title, fields, values, *args, **kwargs):
            assert values["project_id"] == project["id"]
            assert {f.key for f in fields} >= set(created)
        def exec(self):
            return True
        def values(self):
            return dict(created, project_id=project["id"])

    monkeypatch.setattr(resources, "FormDialog", Dialog)
    page.new_resource()
    wait_idle(qtbot)
    row = next(r for r in page.rows if r["name"] == "Recurso Q2")
    assert row["project_id"] == project["id"]
    cap = next(r for r in page.capacity_model.rows if r["skill"] == "Q2")
    assert cap["resources"] == 3 and cap["hours"] == 18
    page.search.setText("Q2")
    page.skill.setCurrentIndex(page.skill.findData("Q2"))
    assert page.model.rowCount() == 1
    page.save_resource(dict(created, name="Recurso global Q2", active=False, project_id=None), row["id"])
    wait_idle(qtbot)
    assert not any(r["skill"] == "Q2" for r in page.capacity_model.rows)
    page.table.selectRow(0)
    assert page.selected()["project_id"] is None
    monkeypatch.setattr(resources, "confirm", lambda *args, **kwargs: True)
    page.delete()
    wait_idle(qtbot)
    assert all(r["id"] != row["id"] for r in win.state.client.resources())
    assert all(r["id"] != row["id"] for r in win.state.resources)
    page.search.clear()
    page.skill.setCurrentIndex(0)


@gui_test
def test_calendar_crud_exceptions_and_year_without_project(gui, qtbot, monkeypatch):
    from ganttier.gui.pages import calendars
    win, _, _, _ = gui
    win.state.open_project(None)
    page = open_page(win, "calendarios", qtbot)

    class Dialog:
        def __init__(self, *args, **kwargs):
            pass
        def exec(self):
            return True
        def values(self):
            return dict(calendars.DEFAULT, name="Calendario Q2", holidays_country=None, exceptions=[])

    monkeypatch.setattr(calendars, "FormDialog", Dialog)
    page.new_calendar()
    page.save()
    wait_idle(qtbot)
    cid = page.editing["id"]
    page.year.setValue(2026)
    wait_idle(qtbot)
    assert len(page.days) == 365
    assert sum(d["hours"] for d in page.days) == 261 * 8
    page.toggle_day("2026-01-05")
    assert page.dirty
    page.save()
    wait_idle(qtbot)
    assert not next(d for d in page.days if d["date"] == "2026-01-05")["working"]
    assert sum(d["hours"] for d in page.days) == 260 * 8
    page.add_exception(dict(date="2026-01-10", working=True, hours=4, reason="Inspección especial"))
    page.save()
    wait_idle(qtbot)
    assert next(d for d in page.days if d["date"] == "2026-01-10")["hours"] == 4
    page.remove_exception("2026-01-05")
    page.save()
    wait_idle(qtbot)
    assert next(d for d in page.days if d["date"] == "2026-01-05")["hours"] == 8
    page.editing.update(name="Turno Q2", shift_pattern="7x7", shift_anchor="2026-01-01",
                        holidays_country="CL", holidays_apply_to_shift=True)
    page.dirty = True
    page.save()
    wait_idle(qtbot)
    assert win.state.client.calendar(cid)["shift_pattern"] == "7x7"
    assert not next(d for d in page.days if d["date"] == "2026-01-01")["working"]
    monkeypatch.setattr(calendars, "confirm", lambda *args, **kwargs: True)
    page.delete()
    wait_idle(qtbot)
    assert all(c["id"] != cid for c in win.state.client.calendars())


@gui_test
def test_maintenance_demo_pages_and_large_virtual_matrix(gui, qtbot):
    win, _, _, _ = gui
    win.go("proyectos")
    project = win.state.client.demo("mantenimiento", 2000)
    win.state.open_project(project["id"])
    macro = open_page(win, "macro", qtbot)
    assert len(macro.rows) == 2000
    page = open_page(win, "optimizar", qtbot)
    assert page.checks["assign_workers"].isVisible()
    assert page.checks["group_outages"].isVisible()
    page.method.setCurrentIndex(page.method.findData("heuristic"))
    page.time_limit.setValue(1)
    page.execute(False)
    wait_idle(qtbot)
    assert page.result and page.result.get("run_id")
    assert page.runs_model.rows
    win.go("macro")
    wait_idle(qtbot)
    # Amplía solo los datos de presentación; no crea 100 000 widgets ni modifica la BD.
    seed = dict(macro.rows[0])
    macro.rows = [dict(seed, id=i + 1000000, code=f"V-{i:06d}") for i in range(100000)]
    macro.by_id = {r["id"]: r for r in macro.rows}
    macro.filter_rows()
    macro.matrix.verticalScrollBar().setValue(macro.matrix.verticalScrollBar().maximum())
    pixmap = macro.matrix.viewport().grab()
    assert not pixmap.isNull()
    assert macro.matrix.last_painted_rows < 100
    assert len(macro.visible) == 100000
    macro.refresh()
    wait_idle(qtbot)


@gui_test
def test_macro_partial_batch_failure_keeps_unsaved_changes(gui, qtbot, monkeypatch):
    from ganttier.gui.client import ApiError
    win, project, _, errors = gui
    page = open_page(win, "macro", qtbot)
    pid = project["id"]
    win.state.client.create_activities(pid, [dict(code=f"B-{i}", name=f"Lote {i}", duration_days=1) for i in range(5001)])
    page.refresh()
    wait_idle(qtbot)
    ids = [r["id"] for r in page.rows if r["code"].startswith("B-")]
    page.assign_range(ids, 0, 0)
    original = win.state.client.patch_activities
    sizes = []

    def fail_second(pid, patches):
        sizes.append(len(patches))
        if len(sizes) == 2:
            raise ApiError(503, "Fallo de prueba en el segundo lote")
        return original(pid, patches)
    monkeypatch.setattr(win.state.client, "patch_activities", fail_second)
    page.save()
    wait_idle(qtbot)
    assert sizes == [5000, 1]
    assert len(page.pending) == 1
    assert errors and "segundo lote" in str(errors.pop())
    monkeypatch.setattr(win.state.client, "patch_activities", original)
    page.save()
    wait_idle(qtbot)
    assert not page.pending
    assert win.state.client.activity(ids[-1])["macro_month"] == page.months[0]["key"]


@gui_test
def test_old_macro_response_cannot_replace_new_project(gui, qtbot, monkeypatch):
    win, project, _, _ = gui
    page = open_page(win, "macro", qtbot)
    other = win.state.client.demo("proyecto")
    original = win.state.client.columnar
    entered, release = threading.Event(), threading.Event()

    def delayed(pid):
        if pid == project["id"]:
            entered.set()
            assert release.wait(10), "La prueba no liberó la respuesta demorada"
        return original(pid)
    monkeypatch.setattr(win.state.client, "columnar", delayed)
    page.refresh()
    qtbot.waitUntil(entered.is_set)
    try:
        win.state.open_project(other["id"])
        win.go("macro")
        qtbot.waitUntil(lambda: page.data_pid == other["id"] and not page.loading, timeout=15000)
    finally:
        release.set()
    wait_idle(qtbot)
    assert page.data_pid == other["id"]
    expected = set(original(other["id"])["ids"])
    assert set(page.by_id) == expected


@gui_test
def test_theme_change_repaints_all_planning_pages(gui, qtbot):
    win, _, root, _ = gui
    for key in ("macro", "optimizar", "recursos", "calendarios"):
        page = open_page(win, key, qtbot)
        for _ in range(2):
            win.toggle_theme()
            qtbot.wait(30)
            image = page.grab()
            assert not image.isNull()
            assert image.save(str(root / f"{key}_{win.theme_name}.png"))
