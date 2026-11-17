"""Formulario y diálogo reales, sin servidores ni bases del usuario."""
from types import SimpleNamespace
from PySide6.QtCore import QObject, Signal, Qt
from ganttier.gui.widgets.common import Field, FormDialog
from ganttier.gui import teams_dialog


def test_master_form_continuity_defaults_and_roundtrip(qtbot, tmp_path):
    from ganttier.gui.pages.maintenance import MaintenancePage
    from PySide6.QtGui import QFontDatabase, QFont
    from PySide6.QtWidgets import QApplication
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    if font_id >= 0:
        QApplication.instance().setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
    fake = SimpleNamespace(state=SimpleNamespace(resources=[], calendars=[]))
    fields = MaintenancePage._fields(fake)
    field = next(f for f in fields if f.key == "batch_continuous_in_shift")
    dialog = FormDialog("Continuidad", [field], {})
    qtbot.addWidget(dialog)
    assert dialog.values()["batch_continuous_in_shift"] is False
    dialog._editors["batch_continuous_in_shift"].setChecked(True)
    assert dialog.values()["batch_continuous_in_shift"] is True
    dialog.show()
    qtbot.wait(30)
    assert dialog.grab().save(str(tmp_path / 'continuidad.png'))


def test_multiple_teams_are_ids_and_optional_quantity_stays_empty(qtbot):
    dialog = FormDialog("Reparto", [Field("team_ids", "Equipos", "multi_choice", [(7,"A"),(9,"B")]),
                                   Field("quantity_total", "Cantidad")], {"team_ids":[9]})
    qtbot.addWidget(dialog)
    assert dialog.values()["team_ids"] == [9]
    dialog._editors["team_ids"].item(0).setCheckState(Qt.Checked)
    assert dialog.values()["team_ids"] == [9,7]
    assert dialog.values()["quantity_total"] in (None, "")


def test_dialog_displays_months_demand_and_alerts(qtbot, monkeypatch, tmp_path):
    from pathlib import Path
    from PySide6.QtGui import QFontDatabase
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))  # Qt offscreen no enumera las fuentes del escritorio.
    class State(QObject):
        project_changed = Signal()
        pid = 1
        project = {"start_date":"2027-01-01"}
        def reload_catalogs(self):
            pass
    state = State()
    rows = [dict(month=month, occurrence=f"PANEL-{i:04d}", team=team, quantity=14000, unit="paneles",
                 batches=7, scheduled_batches=0, required_days=14, available_days=days, work_hours=112,
                 scheduled_hours=0) for i,month in enumerate(("2027-01","2027-02"),1)
             for team, days in (("Equipo A",17),("Equipo B",14))]
    summary = dict(rows=rows, warnings=["PANEL-0001: días hábiles distintos: Equipo A 17; Equipo B 14."],
                   violations=[{"detail":"Equipo B: 14 días requeridos, bloques [7, 7]; lotes indivisibles no caben."}])
    team_rows = [dict(name=f"Equipo {letter}", regime="7x7", cycle_start=f"2027-01-0{day}",
                      members=1, hours_day=8, holidays_work=True) for letter,day in (("A",1),("B",8))]
    state.client = SimpleNamespace(get=lambda route: summary if route.endswith("distribution") else team_rows)
    monkeypatch.setattr(teams_dialog, "run_task", lambda work, done, fail, **kwargs: done(work()))
    dialog = teams_dialog.TeamsDialog(state)
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == 2 and dialog.distribution.rowCount() == 4
    dialog.month.setCurrentIndex(1)
    assert dialog.distribution.rowCount() == 2
    assert "no caben" in dialog.alerts.toPlainText()
    from ganttier.gui.theme import apply_theme
    from PySide6.QtWidgets import QApplication, QTabWidget
    apply_theme(QApplication.instance(), "light")
    dialog.findChild(QTabWidget).setCurrentIndex(1)
    dialog.show()
    qtbot.wait(100)
    assert dialog.grab().save(str(tmp_path / "equipos_reparto.png"))
