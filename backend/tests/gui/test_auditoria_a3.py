"""Regresiones de la auditoría A3 de la GUI (defectos corregidos el 03-10-2026).

Patrones: backend/tests/gui/test_ux2.py (AST y Qt opcional), test_paginas_f2.py
(run_task controlado) y test_ux6.py (geometría Gantt). Sin servidor ni BD real.
Ejecutar desde Ganttier; consultar docs/auditoria/A3_gui.md.
"""
from __future__ import annotations

import ast
import math
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTEST_QT_API", "pyside6")


class MemorySettings:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value


@pytest.fixture
def gui(request, monkeypatch, tmp_path):
    # Pedir qtbot después de importorskip evita un error de fixture sin pytest-qt.
    for package in ("PySide6", "pytestqt", "numpy", "matplotlib", "httpx"):
        pytest.importorskip(package)
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "ganttier.db"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    app = request.getfixturevalue("qapp")
    bot = request.getfixturevalue("qtbot")
    from ganttier.gui.state import AppState

    state = AppState(SimpleNamespace(), settings=MemorySettings())
    state.project = dict(id=1, name="A", mode="proyecto", start_date="2026-01-05",
                         end_date="2026-12-31", data_date="2026-01-05")
    return SimpleNamespace(app=app, bot=bot, state=state)


def controlled_tasks(monkeypatch, module):
    jobs = []

    def enqueue(fn, on_done=None, on_error=None, **kwargs):
        jobs.append((fn, on_done, on_error))

    monkeypatch.setattr(module, "run_task", enqueue)
    return jobs


@pytest.mark.parametrize("text,expected", [("1.234", 1234.0), ("1.234.567", 1234567.0)])
def test_a3_02_chilean_grouped_integers(text, expected):
    # Ejecuta la función real sin importar PySide6 ni copiar su implementación.
    path = ROOT / "backend/ganttier/gui/widgets/paste.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parse_number")
    namespace = {"math": math, "re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace["parse_number"](text) == expected


def test_a3_01_activity_page_keeps_table_and_canvas_row_height_equal(gui, monkeypatch):
    from ganttier.gui.pages import activities
    from ganttier.gui.gantt import profile
    from ganttier.gui.gantt.model import ActivityArrays, RowModel, RelationIndex
    from ganttier.gui.gantt.view import ROW_HEIGHT

    controlled_tasks(monkeypatch, activities)
    controlled_tasks(monkeypatch, profile)
    page = activities.ActivitiesPage(gui.state)
    gui.bot.addWidget(page)
    n = 12
    arrays = ActivityArrays(dict(ids=list(range(1, n + 1)), codes=[f"A{i}" for i in range(n)],
        names=[f"Actividad {i}" for i in range(n)], starts=["2026-01-05"] * n,
        finishes=["2026-01-09"] * n, durations=[5] * n, work_hours=[40] * n,
        wbs_ids=[None] * n, pct=[0] * n, critical=[False] * n, locations=[""] * n, skills=[""] * n))
    rows = RowModel(arrays, grouped=False)
    page.gantt.set_data(arrays, rows, RelationIndex([], arrays, rows), {}, reset=True)
    assert all(page.gantt.table.rowHeight(i) == ROW_HEIGHT for i in range(n))


def test_a3_03_editing_name_does_not_round_untouched_hours(gui):
    from ganttier.gui.widgets.common import Field, FormDialog
    from ganttier.gui.gantt.editing import detail_patch

    original = dict(name="Original", work_hours=1.234)
    dlg = FormDialog("Editar", [Field("name", "Nombre"), Field("work_hours", "HH", "float")], original)
    gui.bot.addWidget(dlg)
    dlg._editors["name"].setText("Renombrada")
    patch = detail_patch(original, dlg.values())
    assert patch.get("name") == "Renombrada"
    assert "work_hours" not in patch


def test_a3_04_project_calendar_survives_edit_without_open_project(gui, monkeypatch):
    from ganttier.gui.pages import projects

    gui.state.project = None
    original = dict(id=7, name="Existente", mode="proyecto", calendar_id=17,
                    start_date="2026-01-05", end_date=None, currency="CLP", description="")
    sent = []
    gui.state.client.update_project = lambda pid, payload: sent.append((pid, payload))
    gui.state.client.calendars = lambda: [dict(id=17, name="Calendario especial")]
    jobs = controlled_tasks(monkeypatch, projects)
    page = projects.ProjectsPage(gui.state)
    gui.bot.addWidget(page)
    monkeypatch.setattr(page, "selected", lambda: original)

    def accept_name_change(dlg):
        dlg._editors["name"].setText("Renombrado")
        return 1

    monkeypatch.setattr(projects.FormDialog, "exec", accept_name_change)
    page.edit()
    while jobs and not sent:
        fn, done, _ = jobs.pop(0)
        result = fn()
        if not sent and done:
            done(result)
    assert sent
    assert sent[0][1]["name"] == "Renombrado"
    assert sent[0][1]["calendar_id"] == 17


def test_a3_07_selecting_current_project_cancels_pending_other_project(gui, monkeypatch):
    from PySide6.QtWidgets import QComboBox
    from ganttier.gui.app import MainWindow
    from ganttier.gui.widgets import common

    jobs = controlled_tasks(monkeypatch, common)
    gui.state.client.project = lambda pid: dict(id=pid, name=str(pid), mode="proyecto")
    gui.state.client.calendars = lambda: []
    gui.state.client.resources = lambda pid: []
    combo = QComboBox()
    gui.bot.addWidget(combo)
    combo.addItem("A", 1)
    combo.addItem("B", 2)
    host = SimpleNamespace(state=gui.state, project_combo=combo, current_key=lambda: "inicio",
                           go=lambda key: None, _populate_projects=lambda: None,
                           notify=lambda *args: None, _confirm_project_change=lambda: True)
    # El método real de MainWindow y la generación real de AppState, sin arrancar uvicorn.
    MainWindow._select_project(host, 1)
    MainWindow._select_project(host, 0)
    while jobs:
        fn, done, _ = jobs.pop(0)
        done(fn())
    assert gui.state.pid == 1


def test_a3_09_model_reset_publishes_valid_checkbox_data(gui):
    from PySide6.QtCore import Qt
    from ganttier.gui.widgets.common import Field
    from ganttier.gui.widgets.paste import EditableTableModel

    model = EditableTableModel([Field("requires_outage", "Desconexión", "bool")],
                               [{"requires_outage": False}])
    observations, errors = [], []

    def observe_reset():
        try:
            index = model.index(model.rowCount() - 1, 0)
            observations.append(model.data(index, Qt.CheckStateRole))
        except Exception as exc:
            errors.append(exc)

    model.modelReset.connect(observe_reset)
    model.append_rows([{"requires_outage": True}])
    assert not errors, f"modelReset entregó datos incoherentes: {errors}"
    assert observations == [Qt.Checked]


def test_a3_10_closed_activity_details_are_destroyed(gui, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QWidget
    from ganttier.gui.pages import activities
    from ganttier.gui.gantt.dialogs import ActivityDetail

    jobs = controlled_tasks(monkeypatch, activities)
    activity = dict(id=11, project_id=1, code="A1", name="Actividad", duration_days=1,
                    work_hours=8, crew_size=1, constraint_type="ASAP", status="no_iniciada")
    gui.state.client.activity = lambda aid: dict(activity)
    gui.state.client.relations = lambda pid: []
    gui.state.client.assignments = lambda pid, aid: []
    # Padre QWidget real, con los datos que open_detail/ActivityDetail necesitan.
    page = QWidget()
    page.state, page.wbs = gui.state, []
    page.arrays = SimpleNamespace(id_to_index={}, codes=[])
    page.gantt = SimpleNamespace(selected_id=11)
    gui.bot.addWidget(page)
    for _ in range(3):
        activities.ActivitiesPage.open_detail(page)
        fn, done, _ = jobs.pop(0)
        done(fn())
        page.detail_dialog.reject()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        gui.app.processEvents()
    assert not page.findChildren(ActivityDetail)
