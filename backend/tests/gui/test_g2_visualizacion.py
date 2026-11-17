import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QSettings, QTimer
from ganttier.gui.state import AppState
from ganttier.gui.pages.tablero_trabajadores import TableroTrabajadoresPage, load_board, export_rows
from ganttier.gui.pages.carga_histograma import CargaHistogramaPage, period_activities, period_end
from ganttier.gui.widgets.graficos import CurvaSWidget
from ganttier.gui.theme import apply_theme


class Client:
    def resources(self, pid):
        return [dict(id=1, name="Ana Pérez", skills=["Eléctrica"], kind="trabajador", project_id=pid, max_units=1)]

    def performance(self, pid, start, end):
        return [dict(resource_id=1, name="Ana Pérez", planned_hours=48., actual_hours=30., hh_efficiency=.8)]

    def load(self, pid, **kwargs):
        return dict(labels=["2027-01-04", "2027-01-11", "2027-01-18"], period="week",
                    series=[dict(key="Ana Pérez", load=[48., 32., 16.], capacity=[40., 40., 24.])])

    def get(self, path):
        return [dict(date="2027-01-05", hours=0, reason="Permiso")]

    def assignments(self, pid):
        return [dict(activity_id=1, resource_id=1, skill="Eléctrica")]

    def activities(self, pid, **params):
        return [dict(id=1, code="SE-01", name="Montaje de protecciones", planned_start="2027-01-04",
                     planned_finish="2027-01-08", required_skill="Eléctrica")]


@pytest.fixture
def state(tmp_path):
    state = AppState(Client(), QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat))
    state.project = dict(id=1, data_date="2027-01-04", mode="proyecto")
    return state


@pytest.fixture(autouse=True)
def offscreen_font(qapp):
    from PySide6.QtGui import QFontDatabase
    if not QFontDatabase.families():
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
        assert font.exists(), "Qt offscreen no encuentra fuentes para verificar capturas"
        assert QFontDatabase.addApplicationFont(str(font)) >= 0


@pytest.fixture(autouse=True)
def sin_ventana_previa(monkeypatch):
    """Otras pruebas dejan una ventana principal cerrada; run_task no ejecuta tareas con ella."""
    from ganttier.gui import app as gui_app
    monkeypatch.setattr(gui_app, "_MAIN", None)


def idle(qtbot, page):
    qtbot.waitUntil(lambda: not page._running, timeout=10000)


def test_board_numbers_and_missing():
    row = load_board(Client(), 1, "2027-01-01", "2027-01-31")[0]
    assert row["capacity"] == 104
    assert row["utilization"] == pytest.approx(100*48/104)
    assert row["absence_days"] == 1
    client = Client()
    original = client.resources(1)
    client.resources = lambda pid: original + [dict(original[0], id=2)]
    assert load_board(client, 1, "2027-01-01", "2027-01-31")[0]["capacity"] is None


def test_excel_preserves_numbers_and_literal_names(tmp_path):
    from openpyxl import load_workbook
    target = tmp_path / "salida.xlsx"
    export_rows(target, [dict(name="=1+1", planned_hours=48., actual_hours=None)])
    wb = load_workbook(target)
    ws = wb.active
    assert ws["A2"].value == "=1+1" and ws["A2"].data_type == "s"
    assert ws["C2"].value == 48 and ws["D2"].value is None
    wb.close()


def test_periods_and_detail():
    assert period_end("2028-02-01", "month") == "2028-02-29"
    assert period_end("2027-01-04", "week") == "2027-01-10"
    assert len(period_activities(Client(), 1, "skill", "Eléctrica", "2027-01-04", "2027-01-10")) == 1
    assert period_activities(Client(), 1, "resource", "Otro", "2027-01-04", "2027-01-10") == []


def test_curve_keyboard_and_null(qtbot):
    widget = CurvaSWidget()
    qtbot.addWidget(widget)
    widget.set_data(["A", "B"], [10, 20], [5, None])
    with qtbot.waitSignal(widget.period_activated) as signal:
        qtbot.keyClick(widget, Qt.Key_Right)
        qtbot.keyClick(widget, Qt.Key_Return)
    assert signal.args == [1]
    assert "sin datos" in widget.accessibleDescription()
    with pytest.raises(ValueError):
        widget.set_data(["A"], [], [])


def test_async_stale_and_error(qtbot, state):
    page = TableroTrabajadoresPage(state)
    qtbot.addWidget(page)
    gate = threading.Event()
    delivered = []
    page.submit(lambda: gate.wait(2), delivered.append)
    page.invalidate()
    page.submit(lambda: "vigente", delivered.append)
    gate.set()
    idle(qtbot, page)
    assert delivered == ["vigente"]
    page.submit(lambda: 1/0, delivered.append)
    idle(qtbot, page)
    assert "No se pudo cargar" in page.status.text()


def test_board_filters_and_invalid_range(qtbot, state):
    page = TableroTrabajadoresPage(state)
    qtbot.addWidget(page)
    page.refresh()
    idle(qtbot, page)
    assert page.model.rowCount() == 1
    page.skill.addItem("Mecánica", "Mecánica")
    page.skill.setCurrentIndex(2)
    assert page.model.rowCount() == 0
    page.end.setDate(page.start.date().addDays(-1))
    page.refresh()
    assert "Desde" in page.status.text() and not page.export.isEnabled()


def test_histogram_click_opens_details(qtbot, state):
    page = CargaHistogramaPage(state)
    qtbot.addWidget(page)
    page.resize(1000, 720)
    page.show()
    page.refresh()
    idle(qtbot, page)
    point = page.chart.plot_rect().center().toPoint()
    qtbot.mouseClick(page.chart, Qt.LeftButton, pos=point)
    idle(qtbot, page)
    assert page.detail_model.rowCount() == 1


def test_100000_activities_worker(qtbot, state):
    class Large(Client):
        def assignments(self, pid):
            return [dict(activity_id=i, resource_id=1, skill="Eléctrica") for i in range(100000)]

        def activities(self, pid, **params):
            first = params["offset"]
            return [dict(id=i, code=str(i), name="Inspección", required_skill="Eléctrica")
                    for i in range(first, min(first+params["limit"], 100000))]
    page = CargaHistogramaPage(state)
    qtbot.addWidget(page)
    beats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    main_thread = threading.get_ident()
    threads = []
    def work():
        threads.append(threading.get_ident())
        return period_activities(Large(), 1, "resource", "Ana Pérez", "2027-01-01", "2027-01-31")
    start = time.perf_counter()
    page.submit(work, page.details_loaded)
    idle(qtbot, page)
    timer.stop()
    assert threads[0] != main_thread
    assert len(page.details) == 100000 and page.detail_model.rowCount() == 200
    assert beats
    print(f"G2 synthetic_100000 elapsed_s={time.perf_counter()-start:.4f} heartbeat_count={len(beats)}")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_capture(qtbot, qapp, state, tmp_path, theme):
    apply_theme(qapp, theme)
    target = Path(os.environ.get("G2_EVIDENCE", str(tmp_path)))
    target.mkdir(parents=True, exist_ok=True)
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    for cls in (TableroTrabajadoresPage, CargaHistogramaPage):
        page = cls(state)
        qtbot.addWidget(page)
        page.start.setDate(page.start.date().addDays(-3))
        page.end.setDate(page.end.date().addDays(27))
        page.resize(1100, 760)
        page.show()
        page.refresh()
        idle(qtbot, page)
        assert page.grab().save(str(target / f"{cls.__name__}_{theme}_{scale}.png"))
        page.hide()
    chart = CurvaSWidget()
    qtbot.addWidget(chart)
    chart.resize(950, 340)
    chart.set_data(["04 ene", "11 ene", "18 ene", "25 ene"], [10, 40, 90, 120], [5, 30, 65, None])
    chart.show()
    assert chart.grab().save(str(target / f"curva_s_{theme}_{scale}.png"))


def test_project_switch_refreshes_visible_page(qtbot, state):
    page = TableroTrabajadoresPage(state)
    qtbot.addWidget(page)
    page.show()
    page.refresh()
    idle(qtbot, page)
    state.project = dict(id=2, data_date='2027-01-04', mode='proyecto')
    state.project_changed.emit(state.project)
    qtbot.wait(30)
    idle(qtbot, page)
    assert page.model.rowCount() == 1
