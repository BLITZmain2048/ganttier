"""A4: pruebas pytest-qt con widgets reales y entrega controlada de respuestas.

No crean una ventana principal ni escriben preferencias en el registro de Windows.
"""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QSettings
from ganttier.gui.state import AppState
from ganttier.gui.gantt.model import ActivityArrays, RowModel, RelationIndex


@pytest.fixture
def state(tmp_path, qapp):
    settings = QSettings(str(tmp_path / "a4.ini"), QSettings.IniFormat)
    state = AppState(SimpleNamespace(), settings=settings)
    state.project = {"id": 1, "name": "A4", "mode": "proyecto", "start_date": "2026-01-05"}
    return state


@pytest.fixture
def page(state, qtbot, monkeypatch):
    from ganttier.gui.pages import activities
    page = activities.ActivitiesPage(state)
    qtbot.addWidget(page)
    monkeypatch.setattr(page, "load_calendar", lambda: None)
    yield page
    page.filter_timer.stop()
    page.calendar_timer.stop()


def install_rows(page, *, text="", sort="none", grouped=False):
    arrays = ActivityArrays({"ids": [1, 2], "codes": ["A", "B"], "names": ["Alfa", "Beta"],
        "starts": ["2026-01-05"] * 2, "finishes": ["2026-01-09"] * 2,
        "durations": [5, 5], "work_hours": [10, 10], "pct": [100, 0], "wbs_ids": [1, 1]})
    wbs = [{"id": 1, "code": "1", "name": "Grupo", "parent_id": None}]
    rows = RowModel(arrays, wbs, text=text, sort=sort, grouped=grouped)
    page.arrays, page.wbs = arrays, wbs
    page.gantt.set_data(arrays, rows, RelationIndex([], arrays, rows), page.state.project)
    return arrays, rows, wbs


def test_apply_local_recomputes_wbs_weighted_progress(page):
    arrays, rows, wbs = install_rows(page, grouped=True)
    assert rows.groups[0]["pct"] == 50
    assert page._apply_local(1, "hh", 90)
    expected = RowModel(arrays, wbs).groups[0]["pct"]
    assert expected == 90
    assert page.gantt.rows.groups[0]["pct"] == expected


def test_apply_local_rebuilds_active_name_filter(page):
    arrays, rows, _ = install_rows(page, text="Alfa")
    assert rows.matched_count == 1
    assert page._apply_local(1, "names", "Otro nombre")
    assert not arrays.filter("Alfa").any()
    assert page.gantt.rows.matched_count == 0


def test_apply_local_rebuilds_name_sort(page):
    arrays, _, _ = install_rows(page, sort="nombre")
    assert page._apply_local(1, "names", "Zeta")
    expected = RowModel(arrays, grouped=False, sort="nombre").rows.tolist()
    assert expected == [1, 0]
    assert page.gantt.rows.rows.tolist() == expected


def test_false_local_update_requests_canonical_refresh(page, qtbot, monkeypatch):
    from ganttier.gui.pages import activities
    refreshed = []
    monkeypatch.setattr(page, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(activities, "run_task", lambda work, done, failed, **kw: done(work()))
    monkeypatch.setattr(activities, "notify", lambda *a, **kw: None)
    page.show()
    qtbot.wait(5)
    refreshed.clear()
    # _apply_local devuelve False si la fila ya no está en el array cargado.
    page.mutate(lambda: {"id": 42, "name": "Guardado"}, recalc=False, notify_saved=False,
                local_update=lambda: page._apply_local(42, "names", "Guardado"))
    qtbot.wait(20)
    assert refreshed, "Una lista [False] se trata como actualización local exitosa y suprime la recarga"


def test_analysis_discards_older_response_for_same_project(state, qtbot, monkeypatch):
    from ganttier.gui.pages import analysis
    pending = []
    monkeypatch.setattr(analysis, "run_task", lambda fn, done, failed, **kw: pending.append((fn, done)))
    state.client = SimpleNamespace(get=lambda path, params: {
        "groups": [{"name": params["group"], "activity_ids": [], "evm": {}}],
        "reconciliation": {"matches": True}})
    page = analysis.AnalysisPage(state)
    qtbot.addWidget(page)
    page.load_groups()  # solicitud lenta: WBS
    page.group.setCurrentIndex(page.group.findData("skill"))  # solicitud nueva: habilidad
    assert len(pending) == 2
    for fn, done in reversed(pending):
        done(fn())  # termina primero la segunda consulta
    assert page.group.currentData() == "skill"
    assert page.group_model.rows[0]["name"] == "skill"


def test_bulk_dialog_freezes_scope_and_changes_only_checked_fields(qtbot):
    from ganttier.gui.gantt.bulk_edit import BulkEditDialog
    arrays = ActivityArrays({"ids": [1, 2], "codes": ["A", "B"], "names": ["A", "B"],
        "starts": [None, None], "finishes": [None, None], "work_hours": [10, 20]})
    scope = [1]
    dialog = BulkEditDialog(arrays, scope, 2, [])
    qtbot.addWidget(dialog)
    scope.append(2)
    dialog.checks["work_hours"].setChecked(True)
    dialog.hh_mode.setCurrentIndex(dialog.hh_mode.findData("pct"))
    dialog.hh_value.setValue(50)
    assert dialog.patches() == [{"id": 1, "work_hours": 15}]
