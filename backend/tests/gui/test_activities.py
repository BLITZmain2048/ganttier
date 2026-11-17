"""Q1 regression tests. Pure models also run on machines without Qt.

Qt tests use create_window() and the real embedded API, never a mock server.
Run with QT_QPA_PLATFORM=offscreen; use --basetemp inside the workspace.
"""
from __future__ import annotations

import copy
import os
import time

import numpy as np
import pytest

from ganttier.gui.gantt.model import ActivityArrays, RowModel, RelationIndex, day, day_iso
from ganttier.gui.gantt.profile_model import ProfileModel
from ganttier.gui.gantt.editing import detail_patch, validate_activity, scheduled_dates, warning_groups


def columnar(n=6):
    return dict(ids=list(range(1, n + 1)), codes=[f"A{i:06}" for i in range(n)],
                names=[f"Actividad {i}" for i in range(n)], starts=["2026-01-05"] * n,
                finishes=["2026-01-09"] * n, durations=[5] * n, work_hours=[40] * n,
                wbs_ids=[1 + i % 10 for i in range(n)], pct=[25] * n, critical=[i % 2 == 0 for i in range(n)])


def test_epoch_missing_inclusive_dates_and_validation():
    values = columnar(3)
    values.update(starts=["1970-01-01", None, "2026-02-02"],
                  finishes=["1970-01-01", None, "2026-02-02"], durations=[1, 3, 0])
    arrays = ActivityArrays(values)
    assert arrays.start[0] == arrays.finish[0] == 0
    assert np.isnan(arrays.start[1]) and np.isnan(arrays.finish[1])
    assert day_iso(arrays.finish[2]) == "2026-02-02"
    assert arrays.milestone.tolist() == [False, False, True]
    assert day("1970-01-01") == 0
    assert day_iso(float("nan")) is None
    with pytest.raises(ValueError, match="Longitud"):
        ActivityArrays(dict(values, work_hours=[1]))
    with pytest.raises(ValueError, match="duplicados"):
        ActivityArrays(dict(values, ids=[1, 1, 2]))


def test_wbs_deep_cycle_orphans_and_collapse():
    depth = 1500
    wbs = [dict(id=i, parent_id=i - 1 if i else None, code=str(i), name=f"Nodo {i}") for i in range(depth)]
    values = columnar(3)
    values["wbs_ids"] = [depth - 1, 99999, 1]
    arrays = ActivityArrays(values)
    rows = RowModel(arrays, wbs)
    assert np.count_nonzero(rows.rows >= 0) == 3
    assert rows.levels.max() == depth
    assert rows.groups[0]["count"] == 2
    collapsed = RowModel(arrays, wbs, collapsed={0})
    assert collapsed.rows.tolist() == [-1, 1]
    assert collapsed.row_of_act.tolist() == [-1, 1, -1]
    wbs[0]["parent_id"] = depth - 1
    cycle = RowModel(arrays, wbs)
    assert len(cycle.groups) == depth
    assert np.count_nonzero(cycle.rows >= 0) == 3


def test_filters_sort_weighted_groups_and_relation_index():
    values = columnar(4)
    values.update(wbs_ids=[1, 2, 2, None], work_hours=[10, 30, 0, 0], pct=[0, 100, 50, 25],
                  starts=["2026-02-01", "2026-01-01", None, "2026-01-10"])
    arrays = ActivityArrays(values)
    wbs = [dict(id=1, code="1", name="Raíz", parent_id=None), dict(id=2, code="1.1", name="Hija", parent_id=1)]
    rows = RowModel(arrays, wbs)
    assert rows.groups[0]["count"] == 3
    assert rows.groups[0]["pct"] == pytest.approx((30 * 100 + 5 * 50) / 45)
    assert RowModel(arrays, wbs, text="Actividad 1").matched_count == 1
    assert RowModel(arrays, wbs, critical=True).matched_count == 2
    ordered = RowModel(arrays, grouped=False, sort="inicio")
    assert ordered.rows.tolist() == [1, 3, 0, 2]
    rels = [dict(pred_id=1, succ_id=2, type="FS"), dict(pred_id=2, succ_id=4, type="SS"),
            dict(pred_id=999, succ_id=2, type="FF")]
    index = RelationIndex(rels, arrays, ordered)
    assert len(index.edges) == 2
    assert len(list(index.visible(0, 2))) == 1
    hidden = RowModel(arrays, grouped=False, critical=True)
    assert len(RelationIndex(rels, arrays, hidden).edges) == 0
    assert list(index.visible(1000, 2000)) == []


def test_relations_high_degree_predecessor_only_visible_successors():
    arrays = ActivityArrays(columnar(10000))
    rows = RowModel(arrays, grouped=False)
    index = RelationIndex([dict(pred_id=1, succ_id=i) for i in range(2, 10001)], arrays, rows)
    assert len(list(index.visible(0, 20))) == 19
    assert list(index.visible(5000, 5020)) == []


def test_build_100000_under_one_second():
    values = columnar(100000)
    wbs = [dict(id=i, code=str(i), name="Fase", parent_id=None) for i in range(1, 11)]
    relations = [dict(pred_id=i, succ_id=i + 1, type="FS") for i in range(1, 100000)]
    before = time.perf_counter()
    arrays = ActivityArrays(values)
    rows = RowModel(arrays, wbs)
    index = RelationIndex(relations, arrays, rows)
    elapsed = time.perf_counter() - before
    assert len(rows.rows) == 100010 and len(index.edges) == 99999
    assert elapsed < 1.0, f"Modelo 100k, WBS y 99.999 relaciones: {elapsed:.3f} s"


def test_macro_and_window_edit_precedence():
    old = dict(code="A", name="Prueba", macro_month="2026-01", window_start="2026-01-01", window_end="2026-01-31")
    assert detail_patch(old, old) == {}
    values = dict(old, macro_month="2026-02", window_start="2026-02-02")
    assert detail_patch(old, values) == {"macro_month": "2026-02"}
    values = dict(old, window_start="2026-01-09")
    assert detail_patch(old, values) == {"macro_month": None, "window_start": "2026-01-09"}
    assert validate_activity(dict(old, macro_month="2026-13"))
    assert validate_activity(dict(old, remaining_duration="1.5"))
    assert validate_activity(dict(old, constraint_type="SNET"))
    assert validate_activity(dict(old, voltage_kv="nan"))
    assert validate_activity(dict(old, percent_complete=101))


def test_activity_validation_rejects_fractional_duration_and_priority():
    values = dict(code="A", name="Prueba", duration_days=1, priority=50)
    assert validate_activity(values) is None
    assert validate_activity(dict(values, duration_days=1.5))
    assert validate_activity(dict(values, priority=101))


def test_working_day_moves_holidays_and_horizon():
    days = [dict(date=d, working=w) for d, w in (("2026-01-09", True), ("2026-01-10", False),
             ("2026-01-11", False), ("2026-01-12", False), ("2026-01-13", True), ("2026-01-14", True))]
    assert scheduled_dates(days, "2026-01-09", 2) == dict(planned_start="2026-01-09", planned_finish="2026-01-13")
    assert scheduled_dates(days, "2026-01-10", 1)["planned_start"] == "2026-01-13"
    assert scheduled_dates([], "2026-01-10", 0)["planned_finish"] == "2026-01-10"
    with pytest.raises(ValueError, match="suficientes"):
        scheduled_dates(days, "2026-01-09", 3, "2026-01-13")
    with pytest.raises(ValueError, match="horizonte"):
        scheduled_dates([], "2026-01-20", 0, "2026-01-19")


def test_profile_validation_and_visible_range():
    response = dict(period="week", labels=["2026-01-05", "2026-01-12", "2026-01-26"],
                    series=[dict(key="Electricidad", load=[40, 100, 80], capacity=[80, 80, 80])])
    profile = ProfileModel(response, "week")
    assert profile.visible(day("2026-01-10"), day("2026-01-15")) == (0, 2)
    assert profile.visible(day("2026-01-21"), day("2026-01-22")) == (2, 2)
    assert profile.visible(day("2026-02-10"), day("2026-02-11")) == (3, 3)
    assert profile.series[0]["total"] == 220
    with pytest.raises(ValueError, match="período"):
        ProfileModel(response, "day")
    invalid = copy.deepcopy(response)
    invalid["labels"][1] = "2026-01-08"
    with pytest.raises(ValueError, match="superpuestos"):
        ProfileModel(invalid, "week")
    invalid = copy.deepcopy(response)
    invalid["series"][0]["load"][1] = float("nan")
    with pytest.raises(ValueError, match="valores"):
        ProfileModel(invalid, "week")
    invalid = copy.deepcopy(response)
    invalid["series"] *= 2
    with pytest.raises(ValueError, match="duplicadas"):
        ProfileModel(invalid, "week")


def test_warning_grouping_retains_examples():
    groups = warning_groups(["Actividad A-01 fuera de ventana", "Actividad A-02 fuera de ventana", "Otra advertencia"])
    assert len(groups) == 2
    assert groups[0][1] == 2 and "A-02" in groups[0][2][1]


@pytest.fixture(scope="session")
def qt_window(request, tmp_path_factory):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    pytest.importorskip("fastapi")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    temp = tmp_path_factory.mktemp("q1_api")
    patcher = pytest.MonkeyPatch()
    patcher.setenv("GANTTIER_DB", str(temp / "ganttier.sqlite"))
    patcher.setenv("GANTTIER_DATA_DIR", str(temp / "data"))
    patcher.setenv("MPLCONFIGDIR", str(temp / "matplotlib"))
    from PySide6.QtCore import QSettings, QThreadPool
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(temp / "settings"))
    qapp = request.getfixturevalue("qapp")
    from ganttier.gui.app import create_window
    win, server = create_window(theme="light")
    win.show()
    projects = {"proyecto": win.state.client.demo("proyecto"),
                "mantenimiento": win.state.client.demo("mantenimiento", 20000)}
    yield win, projects
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()
    win.close()
    win.state.client.close()
    server.stop()
    patcher.undo()


def settle(qtbot, page, timeout=60000):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not page.loading and not page.mutating and not _active, timeout=timeout)
    qtbot.wait(220)  # Debounced calendar/profile fetches are part of the operation.
    qtbot.waitUntil(lambda: not page.loading and not page.mutating and not _active, timeout=timeout)


def open_page(qt_window, qtbot, mode="proyecto"):
    win, projects = qt_window
    win.state.open_project(projects[mode]["id"])
    win.go("gantt")
    page = win.page("gantt")
    from ganttier.gui.pages.activities import ActivitiesPage
    assert isinstance(page, ActivitiesPage)
    settle(qtbot, page)
    return win, page


def test_real_project_cpm_inline_name_duration_progress(qt_window, qtbot):
    from PySide6.QtCore import Qt
    win, page = open_page(qt_window, qtbot)
    assert page.arrays.n >= 42 and page.model.rowCount() > page.arrays.n
    page.cpm()
    settle(qtbot, page)
    assert page.last_summary["critical_count"] > 0
    aid = int(page.arrays.ids[0])
    def edit(key, value):
        row = int(page.gantt.rows.row_of_act[page.arrays.id_to_index[aid]])
        col = next(i for i, (name, _) in enumerate(page.model.columns) if name == key)
        assert page.model.setData(page.model.index(row, col), value, Qt.EditRole)
        settle(qtbot, page)
    edit("names", "Actividad editada Q1")
    assert win.state.client.activity(aid)["name"] == "Actividad editada Q1"
    edit("dur", 7)
    assert win.state.client.activity(aid)["duration_days"] == 7
    edit("pct", 35)
    entries = win.state.client.progress(win.state.pid)
    assert any(e["activity_id"] == aid and e["percent_complete"] == 35 and e["date"] == win.state.project["data_date"] for e in entries)
    assert page.arrays.pct[page.arrays.id_to_index[aid]] == 35


def test_real_crud_relations_wbs_detail_baseline(qt_window, qtbot, monkeypatch):
    from ganttier.gui.gantt.dialogs import WbsDialog, ActivityDetail
    from ganttier.gui import theme
    import ganttier.gui.pages.activities as module
    win, page = open_page(qt_window, qtbot)
    client, pid, before = win.state.client, win.state.pid, page.arrays.n
    page.create_activity(dict(code="Q1-A", name="Creada A", duration_days=2, work_hours=16))
    page.create_activity(dict(code="Q1-B", name="Creada B", duration_days=3, work_hours=24))
    settle(qtbot, page)
    assert page.arrays.n == before + 2
    aid = int(page.arrays.ids[np.flatnonzero(page.arrays.codes == "Q1-A")[0]])
    bid = int(page.arrays.ids[np.flatnonzero(page.arrays.codes == "Q1-B")[0]])
    page.create_relation(aid, bid)
    settle(qtbot, page)
    relation = next(r for r in client.relations(pid) if r["pred_id"] == aid and r["succ_id"] == bid)
    assert relation["type"] == "FS"
    page.save_relation(dict(pred_id=aid, succ_id=bid, type="SS", lag_days=2), relation)
    settle(qtbot, page)
    assert any(r["type"] == "SS" and r["lag_days"] == 2 for r in client.relations(pid) if r["pred_id"] == aid)
    dialog = WbsDialog(page)
    qtbot.addWidget(dialog)
    dialog.save_node(dict(code="Q1", name="Fase Q1", parent_id=None, sort_order=90))
    settle(qtbot, page)
    node = next(w for w in client.wbs(pid) if w["code"] == "Q1")
    parent = next(w for w in client.wbs(pid) if w["id"] != node["id"])
    dialog.save_node(dict(code="Q1", name="Fase movida", parent_id=parent["id"], sort_order=4), node["id"])
    settle(qtbot, page)
    assert next(w for w in client.wbs(pid) if w["id"] == node["id"])["parent_id"] == parent["id"]
    original = client.activity(aid)
    page.save_detail(original, dict(original, wbs_id=node["id"], macro_month="2026-02"))
    settle(qtbot, page)
    original = client.activity(aid)
    assert original["window_start"] == "2026-02-01" and original["window_end"] == "2026-02-28"
    page.save_detail(original, dict(original, window_start="2026-02-05"))
    settle(qtbot, page)
    assert client.activity(aid)["macro_month"] is None
    page.gantt.select_id(aid)
    page.open_detail()
    qtbot.waitUntil(lambda: hasattr(page, "detail_dialog") and page.detail_dialog.isVisible())
    assert isinstance(page.detail_dialog, ActivityDetail)
    assert page.detail_dialog.rel_model.rowCount() == 1
    resource = win.state.resources[0]
    page.mutate(lambda: client.create_assignment(pid, dict(activity_id=aid, resource_id=resource["id"], units=1, work_hours=8)),
                page.detail_dialog.reload_related, recalc=False)
    settle(qtbot, page)
    assert page.detail_dialog.asg_model.rowCount() == 1
    assignment = client.assignments(pid, aid)[0]
    page.mutate(lambda: client.update_assignment(assignment["id"], {"units": 2, "work_hours": 16}), recalc=False)
    settle(qtbot, page)
    assert client.assignments(pid, aid)[0]["units"] == 2
    page.detail_dialog._editors["name"].setText("Guardada desde detalle")
    page.detail_dialog._accept()
    settle(qtbot, page)
    assert client.activity(aid)["name"] == "Guardada desde detalle"
    class BaselineForm:
        def __init__(self, *args, **kwargs): pass
        def exec(self): return True
        def values(self): return {"name": "Prueba Q1"}
    monkeypatch.setattr(module, "FormDialog", BaselineForm)
    page.baseline()
    settle(qtbot, page)
    assert client.project(pid)["active_baseline_id"]
    assert np.isfinite(page.arrays.bs).any()
    win.toggle_theme()
    assert theme.tokens().name == "dark"
    win.toggle_theme()
    monkeypatch.setattr(module, "confirm", lambda *args, **kwargs: True)
    page.gantt.select_id(bid)
    page.delete_selected()
    settle(qtbot, page)
    assert bid not in page.arrays.id_to_index
    import ganttier.gui.gantt.dialogs as dialogs_module
    monkeypatch.setattr(dialogs_module, "confirm", lambda *args, **kwargs: True)
    row = next(i for i, w in enumerate(dialog.model.rows) if w["id"] == node["id"])
    dialog.table.setCurrentIndex(dialog.model.index(row, 0))
    dialog.delete_node()
    settle(qtbot, page)
    assert not any(w["id"] == node["id"] for w in client.wbs(pid))
    assert client.activity(aid)["wbs_id"] is None


def test_real_maintenance_20000_filters_profile_and_zoom(qt_window, qtbot):
    win, page = open_page(qt_window, qtbot, "mantenimiento")
    assert page.arrays.n == 20000
    assert page.model.columns[-2:] == [("skill", "Habilidad"), ("location", "Instalación")]
    code = str(page.arrays.codes[100])
    page.search.setText(code)
    qtbot.wait(200)
    settle(qtbot, page)
    assert page.gantt.rows.matched_count >= 1
    page.search.clear()
    qtbot.wait(200)
    settle(qtbot, page)
    page.gantt.vertical.setValue(page.gantt.vertical.maximum() // 2)
    assert page.gantt.table.verticalScrollBar().value() == page.gantt.vertical.value()
    g = page.gantt
    g.horizontal.setValue(g.horizontal.maximum() // 2)
    anchor = min(150, g.canvas.width() / 2)
    at = g.day_at(anchor)
    g.set_day_width(26, anchor)
    assert abs(g.day_at(anchor) - at) <= 1 / g.day_width
    page.view_actions["profile"].setChecked(True)
    settle(qtbot, page)
    qtbot.waitUntil(lambda: page.profile.model is not None, timeout=60000)
    assert page.profile.period == "day" and page.profile.model.span == 1
    assert page.profile.canvas.width() == g.canvas.width()
    assert page.profile.canvas.mapToGlobal(page.profile.canvas.rect().topLeft()).x() == g.canvas.mapToGlobal(g.canvas.rect().topLeft()).x()
    g.set_day_width(2.4)
    qtbot.waitUntil(lambda: page.profile.model is not None and page.profile.model.span == 7, timeout=60000)
    if page.profile.series.count():
        from PySide6.QtCore import Qt
        item = page.profile.series.item(0)
        item.setCheckState(Qt.Unchecked)
        assert item.data(Qt.UserRole) not in {s["key"] for s in page.profile.selected}
    page.profile.group.setCurrentIndex(1)
    settle(qtbot, page)
    assert page.profile._key[1] == "resource"
    page.view_actions["profile"].setChecked(False)


def test_maintenance_calendar_edits_locked_and_resize(qt_window, qtbot, monkeypatch):
    import ganttier.gui.pages.activities as module
    win, page = open_page(qt_window, qtbot, "mantenimiento")
    client, pid = win.state.client, win.state.pid
    page.create_activity(dict(code="Q1-MOVE", name="Movimiento por calendario", duration_days=2,
                              planned_start="2026-01-05", planned_finish="2026-01-06"))
    settle(qtbot, page)
    aid = int(page.arrays.ids[np.flatnonzero(page.arrays.codes == "Q1-MOVE")[0]])
    calendar = win.state.project["calendar_id"]
    page.move_activity(aid, "2026-01-09")
    settle(qtbot, page)
    days = client.calendar_days(calendar, "2026-01-09", "2026-02-28")
    work = [d["date"] for d in days if d["working"]]
    activity = client.activity(aid)
    assert activity["planned_start"] == work[0] and activity["planned_finish"] == work[1]
    page.edit_cell(aid, "dur", 3)
    settle(qtbot, page)
    assert client.activity(aid)["planned_finish"] == work[2]
    page.resize_activity(aid, work[3])
    settle(qtbot, page)
    assert client.activity(aid)["duration_days"] == 4
    client.update_activity(aid, {"locked": True})
    errors = []
    monkeypatch.setattr(module, "show_error", lambda exc, *args: errors.append(str(exc)))
    page.move_activity(aid, "2026-02-02")
    settle(qtbot, page)
    assert errors and "bloqueada" in errors[-1]
    assert client.activity(aid)["planned_start"] == work[0]
    client.delete_activity(aid)
    page.refresh()
    settle(qtbot, page)


def test_canvas_100000_virtualized_frame_and_drag(qt_window, qtbot):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QImage, QWheelEvent
    from PySide6.QtWidgets import QApplication
    from ganttier.gui.gantt.view import GanttWidget, HEADER_HEIGHT, ROW_HEIGHT
    widget = GanttWidget()
    qtbot.addWidget(widget)
    widget.resize(1400, 650)
    values = columnar(100000)
    arrays = ActivityArrays(values)
    rows = RowModel(arrays, grouped=False)
    widget.set_data(arrays, rows, RelationIndex([dict(pred_id=1, succ_id=2, type="FS")], arrays, rows), dict(mode="proyecto"), reset=True)
    widget.show()
    qtbot.waitExposed(widget)
    widget.set_day_width(26)
    widget.jump_to_day(arrays.start[0])
    image = QImage(widget.size(), QImage.Format_ARGB32_Premultiplied)
    widget.render(image)  # Warm up glyphs; the measured frame includes the table.
    start = time.perf_counter()
    widget.render(image)
    elapsed = time.perf_counter() - start
    assert elapsed < .05, f"Cuadro de Gantt: {elapsed * 1000:.1f} ms"
    assert widget.canvas.last_painted_rows < 30
    assert widget.vertical.maximum() > 2_000_000
    center = QPoint(round(widget.day_to_x(arrays.start[0]) + 30), HEADER_HEIGHT + ROW_HEIGHT // 2)
    with qtbot.waitSignal(widget.move_requested, timeout=1000) as moved:
        qtbot.mousePress(widget.canvas, Qt.LeftButton, pos=center)
        qtbot.mouseMove(widget.canvas, center + QPoint(26, 0))
        qtbot.mouseRelease(widget.canvas, Qt.LeftButton, pos=center + QPoint(26, 0))
    assert moved.args == [1, "2026-01-06"]
    finish = QPoint(round(widget.day_to_x(arrays.finish[0] + 1)) - 2, center.y())
    with qtbot.waitSignal(widget.resize_requested, timeout=1000) as resized:
        qtbot.mousePress(widget.canvas, Qt.LeftButton, pos=finish)
        qtbot.mouseRelease(widget.canvas, Qt.LeftButton, pos=finish + QPoint(26, 0))
    assert resized.args == [1, "2026-01-10"]
    handle = QPoint(round(widget.day_to_x(arrays.finish[0] + 1)) + 7, center.y())
    target = QPoint(center.x(), center.y() + ROW_HEIGHT)
    with qtbot.waitSignal(widget.link_requested, timeout=1000) as linked:
        qtbot.mousePress(widget.canvas, Qt.LeftButton, pos=handle)
        qtbot.mouseMove(widget.canvas, target)
        qtbot.mouseRelease(widget.canvas, Qt.LeftButton, pos=target)
    assert linked.args == [1, 2]
    anchor = QPointF(100, 100)
    before = widget.day_at(anchor.x())
    wheel = QWheelEvent(anchor, QPointF(widget.canvas.mapToGlobal(anchor.toPoint())), QPoint(), QPoint(0, 120),
                        Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget.canvas, wheel)
    assert widget.day_width == pytest.approx(31.2)
    assert abs(widget.day_at(anchor.x()) - before) <= 1 / widget.day_width


def test_generate_fallback_and_failure_preserves_data(qt_window, qtbot, monkeypatch):
    from ganttier.gui.client import ApiError
    import ganttier.gui.pages.activities as module
    win, page = open_page(qt_window, qtbot)
    client = win.state.client
    original_post = client.post
    calls = []
    def post(path, *args, **kwargs):
        calls.append(path)
        if path.endswith("/schedule/solve"):
            raise ApiError(404, "No existe")
        return original_post(path, *args, **kwargs)
    monkeypatch.setattr(client, "post", post)
    page.generate()
    settle(qtbot, page)
    assert any(p.endswith("/schedule/cpm") for p in calls)
    assert any("CPM" in str(w) for w in page.last_summary["warnings"])
    errors = []
    monkeypatch.setattr(module, "show_error", lambda exc, *args: errors.append(exc))
    def failed_post(path, *args, **kwargs):
        if path.endswith("/schedule/solve"):
            raise ApiError(422, "Programa inviable")
        return original_post(path, *args, **kwargs)
    monkeypatch.setattr(client, "post", failed_post)
    before = len(client.runs(win.state.pid))
    page.generate()
    settle(qtbot, page)
    assert errors and errors[-1].status == 422
    assert len(client.runs(win.state.pid)) == before
    relation = client.relations(win.state.pid)[0]
    page.save_relation(dict(pred_id=relation["pred_id"], succ_id=relation["pred_id"], type="FS", lag_days=0), relation)
    settle(qtbot, page)
    assert any(r["pred_id"] == relation["pred_id"] and r["succ_id"] == relation["succ_id"]
               and r["type"] == relation["type"] for r in client.relations(win.state.pid))


def test_stale_project_load_is_ignored(qt_window, qtbot, monkeypatch):
    import threading
    win, projects = qt_window
    win, page = open_page(qt_window, qtbot)
    client, original = win.state.client, win.state.client.columnar
    started, release = threading.Event(), threading.Event()
    first_pid = projects["proyecto"]["id"]
    def delayed(pid):
        result = original(pid)
        if pid == first_pid:
            started.set()
            release.wait(10)
        return result
    monkeypatch.setattr(client, "columnar", delayed)
    page.refresh()
    qtbot.waitUntil(started.is_set)
    win.state.open_project(projects["mantenimiento"]["id"])
    qtbot.waitUntil(lambda: page._loaded_pid == projects["mantenimiento"]["id"] and page.arrays.n == 20000, timeout=60000)
    release.set()
    settle(qtbot, page)
    assert page.arrays.n == 20000 and page.model.columns[-1][0] == "location"
