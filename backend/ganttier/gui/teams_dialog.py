"""Gestión de cuadrillas y resumen de demanda/programación por equipo y mes."""
from __future__ import annotations

from .widgets.responsive import ResponsiveDialog

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTabWidget, QWidget, QPlainTextEdit, QComboBox

from .widgets.common import Card, Column, DictTableModel, Field, FormDialog, button, hbox, run_task, show_error, confirm
from .pages.maintenance import table_for


class TeamsDialog(ResponsiveDialog):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state, self.pid = state, state.pid
        self.setWindowTitle("Equipos de trabajo y reparto")
        self.resize(1120, 700)
        self.busy = False
        self.rows = []
        root = QVBoxLayout(self)
        hint = QLabel("Los equipos se comparten entre todas las tareas y plantas de este plan. La demanda asignada solo se aplica cuando el programa supera la verificación de factibilidad.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        tabs = QTabWidget()
        root.addWidget(tabs)
        teams = Card("Equipos de trabajo")
        self.new = button("Nuevo equipo", lambda: self.edit())
        self.change = button("Editar", self.edit_selected)
        self.remove = button("Eliminar", self.delete)
        teams.add(layout=hbox(self.new, self.change, self.remove, button("Actualizar", self.refresh), None))
        self.model = DictTableModel([Column("name", "Equipo"), Column("regime", "Régimen"),
            Column("cycle_start", "Inicio de ciclo"), Column("members", "Integrantes", kind="int"),
            Column("hours_day", "Horas/día", kind="float"), Column("holidays_work", "Trabaja feriados", kind="bool")])
        self.table = table_for(self.model)
        self.table.doubleClicked.connect(self.edit_selected)
        teams.add(self.table, stretch=1)
        tabs.addTab(teams, "Equipos")
        summary = Card("Demanda y programa por equipo y mes")
        self.month = QComboBox()
        self.month.addItem("Todos los meses", "")
        self.month.currentIndexChanged.connect(self.filter)
        summary.add(layout=hbox(QLabel("Mes"), self.month, None))
        self.distribution = DictTableModel([Column("month", "Mes", width=95), Column("occurrence", "Ocurrencia", width=155),
            Column("team", "Equipo", width=150), Column("quantity", "Cantidad", kind="float", width=110), Column("unit", "Unidad", width=95),
            Column("batches", "Lotes demanda", kind="int", width=125), Column("scheduled_batches", "Programados", kind="int", width=120),
            Column("required_days", "Días requeridos", kind="int", width=130), Column("available_days", "Días de turno", kind="int", width=120),
            Column("work_hours", "HH demanda", kind="float", width=120), Column("scheduled_hours", "HH programadas", kind="float", width=140)])
        self.distribution_table = table_for(self.distribution)
        for i, column in enumerate(self.distribution.columns):
            self.distribution_table.setColumnWidth(i, column.width)
        summary.add(self.distribution_table, stretch=1)
        self.alerts = QPlainTextEdit()
        self.alerts.setReadOnly(True)
        self.alerts.setMaximumHeight(180)
        summary.add(self.alerts)
        tabs.addTab(summary, "Reparto y alertas")
        root.addWidget(button("Cerrar", self.accept))
        state.project_changed.connect(lambda *_: self.reject() if self.pid != state.pid else None)
        self.refresh()

    def _ready(self):
        return self.pid == self.state.pid and not self.busy

    def _busy(self, value):
        self.busy = value
        for widget in (self.new, self.change, self.remove, self.table):
            widget.setEnabled(not value)

    def refresh(self):
        if not self._ready():
            return
        self._busy(True)
        client, pid = self.state.client, self.pid
        def work():
            teams = client.get(f"/projects/{pid}/teams")
            summary = client.get(f"/projects/{pid}/maintenance/distribution")
            self.state.reload_catalogs()
            return teams, summary
        def done(data):
            self._busy(False)
            if self.state.pid != pid:
                return
            teams, summary = data
            self.model.set_rows(teams)
            self.rows = summary["rows"]
            selected = self.month.currentData()
            self.month.blockSignals(True)
            self.month.clear()
            self.month.addItem("Todos los meses", "")
            for month in sorted({r["month"] for r in self.rows}):
                self.month.addItem(month, month)
            self.month.setCurrentIndex(max(0, self.month.findData(selected)))
            self.month.blockSignals(False)
            self.filter()
            messages = [*summary["warnings"], *(v["detail"] for v in summary["violations"])]
            self.alerts.setPlainText("\n".join(messages) or "Sin alertas de reparto. Los períodos incompletos quedan pendientes de evaluación.")
        run_task(work, done, self.failed, busy="Cargando equipos y reparto…")

    def failed(self, exc):
        self._busy(False)
        show_error(exc, self)

    def filter(self):
        month = self.month.currentData()
        self.distribution.set_rows([r for r in self.rows if not month or r["month"] == month])

    def selected(self):
        index = self.table.currentIndex()
        return self.model.row_dict(index.row()) if index.isValid() else None

    def edit_selected(self, *_):
        if self.selected():
            self.edit(self.selected())

    def edit(self, team=None):
        if not self._ready():
            return
        fields = [Field("name", "Equipo", required=True),
            Field("regime", "Régimen NxM", required=True, help="Presets: 7x7, 14x14, 5x2. También acepta otros NxM. 5x2 siempre es lunes a viernes."),
            Field("cycle_start", "Inicio del ciclo", "date", required=True),
            Field("members", "Integrantes", "int", minimum=1), Field("hours_day", "Horas por día de turno", "float", minimum=.01, maximum=24),
            Field("holidays_choice", "Feriados", "choice", [(None, "Predeterminado del régimen"), (True, "Se trabajan"), (False, "No se trabajan (Chile)")])]
        values = dict(name="", regime="7x7", cycle_start=self.state.project["start_date"], members=1, hours_day=8, holidays_choice=None)
        if team:
            values.update(team)
            values["holidays_choice"] = team["holidays_work"]
        dialog = FormDialog("Editar equipo" if team else "Nuevo equipo", fields, values, self)
        if dialog.exec() and self._ready():
            payload = {f.key: dialog.values()[f.key] for f in fields}
            payload["holidays_work"] = payload.pop("holidays_choice")
            route = f"/projects/{self.pid}/teams" + (f"/{team['id']}" if team else "")
            self._busy(True)
            def done(_):
                self._busy(False)
                self.state.bump_schedule()
                self.refresh()
            run_task(lambda: self.state.client.request("PUT" if team else "POST", route, json=payload), done, self.failed,
                     busy="Guardando equipo…")

    def delete(self):
        team = self.selected()
        if not team or not self._ready():
            return
        if not confirm(self, "Eliminar equipo", f"Eliminar {team['name']}. Solo se permite si no tiene tareas ni lotes asociados."):
            return
        if not self._ready():
            return
        self._busy(True)
        def done(_):
            self._busy(False)
            self.state.bump_schedule()
            self.refresh()
        run_task(lambda: self.state.client.request("DELETE", f"/projects/{self.pid}/teams/{team['id']}"), done, self.failed)
