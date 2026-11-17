"""UX6: render offscreen, geometría y contratos públicos de Gantt/Control/Desempeño."""
from __future__ import annotations

import importlib.util
import os
from time import perf_counter

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTEST_QT_API", "pyside6")
GUI_MISSING = [name for name in ("PySide6", "pytestqt", "matplotlib")
               if importlib.util.find_spec(name) is None]
gui = pytest.mark.skipif(bool(GUI_MISSING), reason="Faltan: " + ", ".join(GUI_MISSING))


def columnar(n):
    return dict(ids=list(range(1, n + 1)), codes=[f"A{i}" for i in range(n)],
                names=[f"Actividad {i}" for i in range(n)], starts=["2026-01-05"] * n,
                finishes=["2026-01-09"] * n, durations=[5] * n, work_hours=[40] * n,
                wbs_ids=[1 + i % 10 for i in range(n)], pct=[25] * n,
                critical=[i % 2 == 0 for i in range(n)], locations=[""] * n, skills=[""] * n)


def test_construction_100000_under_one_second():
    from ganttier.gui.gantt.model import ActivityArrays, RowModel, RelationIndex

    data = columnar(100_000)
    wbs = [dict(id=i, code=str(i), name="Fase", parent_id=None) for i in range(1, 11)]
    relations = [dict(pred_id=i, succ_id=i + 1, type="FS") for i in range(1, 100_000)]
    start = perf_counter()
    arrays = ActivityArrays(data)
    rows = RowModel(arrays, wbs)
    links = RelationIndex(relations, arrays, rows)
    elapsed = perf_counter() - start
    assert len(rows.rows) == 100_010 and len(links.edges) == 99_999
    assert elapsed < 1, f"Construcción 100k: {elapsed:.3f} s"


def test_profile_colors_do_not_follow_load_ranking():
    from ganttier.gui.gantt.profile_model import ProfileModel

    series = [dict(key="B", load=[80], capacity=[40]), dict(key="A", load=[20], capacity=[40])]
    first = ProfileModel(dict(period="day", labels=["2026-01-05"], series=series), "day")
    second = ProfileModel(dict(period="day", labels=["2026-01-05"], series=list(reversed(series))), "day")
    assert {s["key"]: s["color"] for s in first.series} == {s["key"]: s["color"] for s in second.series} == {"A": 0, "B": 1}


@gui
def test_gantt_grab_critical_both_themes_and_hover(qtbot, qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QImage
    from ganttier.gui.gantt.model import ActivityArrays, RowModel, RelationIndex
    from ganttier.gui.gantt.view import GanttWidget, HEADER_HEIGHT, ROW_HEIGHT
    from ganttier.gui.theme import apply_theme, tokens

    gantt = GanttWidget()
    qtbot.addWidget(gantt)
    gantt.resize(1400, 550)
    arrays = ActivityArrays(columnar(100_000))
    rows = RowModel(arrays, grouped=False)
    gantt.set_data(arrays, rows, RelationIndex([], arrays, rows), {}, reset=True)
    gantt.show()
    gantt.set_day_width(26)
    gantt.jump_to_day(arrays.start[0])
    try:
        for theme in ("light", "dark"):
            apply_theme(qapp, theme)  # Same live widget: exercise theme_changed.
            qapp.processEvents()
            image = gantt.canvas.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
            assert not image.isNull()
            pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
            pixels = pixels[:, :image.width() * 4].reshape(image.height(), image.width(), 4)
            color = QColor(tokens().bar_crit)
            assert np.any(np.all(pixels[:, :, :3] == [color.red(), color.green(), color.blue()], axis=2))
            assert gantt.canvas.last_painted_rows <= gantt.canvas.height() // ROW_HEIGHT + 1
            assert gantt.canvas.last_painted_rows < 100
            assert gantt.table.rowHeight(0) == ROW_HEIGHT
            assert gantt.model.index(0, 3).data() == "05-01-2026"
        point = QPoint(8, HEADER_HEIGHT + ROW_HEIGHT + ROW_HEIGHT // 2)

        def move(widget, pos):
            # Evento directo: qtbot.mouseMove depende de la ventana activa y falla con otras abiertas.
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QMouseEvent
            from PySide6.QtWidgets import QApplication
            event = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(pos), QPointF(widget.mapToGlobal(pos)),
                                Qt.NoButton, Qt.NoButton, Qt.NoModifier)
            QApplication.sendEvent(widget, event)

        move(gantt.canvas, point)
        assert gantt.hover_row == 1
        assert gantt.model.index(1, 1).data(Qt.BackgroundRole) == QColor(tokens().surface3)
        move(gantt.table.viewport(), gantt.table.visualRect(gantt.model.index(2, 0)).center())
        assert gantt.hover_row == 2 and gantt.canvas.hover == 2
    finally:
        apply_theme(qapp, "light")


@pytest.fixture
def state():
    from PySide6.QtCore import QObject, Signal

    class State(QObject):
        schedule_changed = Signal()
        project_changed = Signal(object)
        catalogs_changed = Signal()

        def __init__(self):
            super().__init__()
            self.pid = None
            self.project = None
            self.client = None

    return State()


@gui
def test_control_without_program_empty_state_and_navigation(qtbot, monkeypatch, state):
    from PySide6.QtWidgets import QLabel, QPushButton
    from ganttier.gui.pages import control
    from ganttier.gui.widgets.common import EmptyState

    class Client:
        def get(self, path, params=None):
            if path.endswith('/control/dashboard'):
                raise ValueError("Se requieren inicio y término planificados")
            assert path.endswith('/control/activities')
            rows = [dict(id=i, code=f'A{i}', name='Sin programa', start=None,
                         finish=None, work_hours=8, pct=0) for i in (1, 2)]
            if (params or {}).get('scope') != 'all':
                rows = []
            return dict(rows=rows, total=len(rows), offset=0, data_date='2026-01-05')

        def project(self, *_):
            return state.project

        def baselines(self, *_):
            return []

        def columnar(self, *_):
            return dict(columnar(2), starts=[None, None], finishes=[None, None])

        def resources(self, *_):
            return []

    def run_now(work, done, failed=None, **_):
        try:
            result = work()
        except Exception as exc:
            if failed is None:
                raise
            failed(exc)
        else:
            done(result)

    monkeypatch.setattr(control, "run_task", run_now)
    state.pid, state.project, state.client = 1, dict(id=1, data_date="2026-01-05"), Client()
    page = control.ControlPage(state)
    qtbot.addWidget(page)
    page.resize(1000, 800)
    page.show()
    page.refresh()
    assert isinstance(page.empty_state, EmptyState) and page.empty_state.isVisible()
    assert any(label.text() == "Primero genera el programa" for label in page.empty_state.findChildren(QLabel))
    assert page.chart is None
    visited = []
    page.go = visited.append
    page.empty_state.findChildren(QPushButton)[0].click()
    assert visited == ["gantt"]
    # Unscheduled tasks remain editable, preserving existing progress/draft workflows.
    page.scope.setCurrentIndex(page.scope.findData("all"))
    assert page.model.setData(page.model.index(0, 6), 25)
    assert page.draft_count.text() == "1 cambios sin guardar"
    assert page.has_unsaved()
    page.discard()
    assert page.draft_count.text() == "0 cambios sin guardar"


@gui
def test_control_kpi_reflow_and_scurve_contract(qtbot, qapp, state):
    from ganttier.gui.pages.control import ControlPage
    from ganttier.gui.theme import tokens

    page = ControlPage(state)
    qtbot.addWidget(page)
    page.resize(1400, 900)
    page.show()
    qapp.processEvents()
    assert page._kpi_columns == 5
    page.resize(950, 900)
    qapp.processEvents()
    assert page._kpi_columns == 3
    page.scurve = dict(labels=["2026-01-05", "2026-01-12"], data_date="2026-01-08", basis="hours",
                       **{key + "_cum": [0, 100] for key in ("planned", "earned", "actual", "forecast")})
    page.draw_chart()
    assert len(page.chart.ax.lines) == 5
    assert [line.get_color() for line in page.chart.ax.lines[:3]] == list(tokens().series[:3])
    assert all(line.get_linewidth() == 2 for line in page.chart.ax.lines[:4])
    assert page.chart.ax.lines[3].get_linestyle() == "--"
    assert any(text.get_text() == "Corte 08-01" for text in page.chart.ax.texts)


@gui
@pytest.mark.parametrize("workers", [2, 45])
def test_performance_fixed_bar_height_and_public_period(qtbot, qapp, monkeypatch, state, workers):
    from ganttier.gui.pages import performance

    monkeypatch.setattr(performance, "run_task", lambda *args, **kwargs: None)
    page = performance.PerformancePage(state)
    qtbot.addWidget(page)
    page.resize(1200, 900)
    page.show()
    page.period.setCurrentIndex(page.period.findData("weekly"))
    assert page.period_buttons.checkedId() == page.period.currentIndex()
    page.period_buttons.button(page.period.findData("custom")).click()
    assert page.period.currentData() == "custom"
    page.rows = [dict(name=f"Trabajador {i}", compliance_pct=92, hh_efficiency=1.1,
                      avg_delay_days=2, SPI=.95) for i in range(workers)]
    for index in range(page.metric_combo.count()):
        page.metric_combo.setCurrentIndex(index)
        page.draw_chart()
        qapp.processEvents()
        page.chart.canvas.draw()
        assert len(page.chart.ax.patches) == min(40, workers)
        ratio = page.chart.canvas.devicePixelRatioF()
        assert all(15 <= patch.get_window_extent().height / ratio <= 21 for patch in page.chart.ax.patches)
        if workers == 2:
            assert page.chart.height() < 230
        key = page.metric_combo.currentData()
        targets = page.chart.ax.lines
        assert len(targets) == (0 if key == "avg_delay_days" else 1)
        if targets:
            assert targets[0].get_linestyle() == "--"
            assert targets[0].get_xdata()[0] == (90 if key == "compliance_pct" else 1)
    page.rows = []
    page.draw_chart()
    assert page.empty_state.isVisible()
