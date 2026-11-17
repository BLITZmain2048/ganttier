"""Ausencias personales por intervalo (P5-10): licencias, capacitaciones o media jornada de un trabajador."""
from __future__ import annotations

from .widgets.responsive import ResponsiveDialog

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QAbstractItemView, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout)

from .widgets.common import Toolbar, fmt_date, fmt_num, notify, run_task, show_error


def eligible(resource: dict | None) -> str | None:
    """None si admite ausencias; si no, el motivo (lo mismo que valida el backend)."""
    if not resource:
        return "Selecciona un trabajador."
    if resource.get("project_id") is None:
        return "Las ausencias personales no admiten recursos globales: afectan a varios proyectos."
    if resource.get("kind") != "trabajador" or float(resource.get("max_units") or 0) != 1:
        return "Las ausencias son para un trabajador individual (tipo trabajador, 1 unidad); no para cuadrillas."
    return None


def _date_edit(value: QDate) -> QDateEdit:
    editor = QDateEdit(value)
    editor.setCalendarPopup(True)
    editor.setDisplayFormat("dd-MM-yyyy")
    return editor


def _fill(table: QTableWidget, rows: list[tuple]) -> None:
    table.setRowCount(len(rows))
    for r, values in enumerate(rows):
        for c, text in enumerate(values):
            table.setItem(r, c, QTableWidgetItem(str(text)))
    table.resizeColumnsToContents()


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class AbsenceDialog(ResponsiveDialog):
    """Vista previa obligatoria antes de aplicar; no reprograma: muestra las asignaciones afectadas."""

    def __init__(self, state, resource: dict, parent=None):
        super().__init__(parent)
        self.state, self.resource = state, resource
        self.preview_result: dict | None = None
        self.applied: dict | None = None
        self.setWindowTitle(f"Ausencia de {resource.get('name', '')}")
        self.resize(760, 600)
        layout = QVBoxLayout(self)
        intro = QLabel("Registra una licencia, capacitación o disponibilidad parcial. Solo reduce horas: los fines de semana, "
                       "feriados y descansos del turno no cambian. Si el calendario es compartido se crea una copia personal "
                       "para este trabajador. El programa no se reprograma solo: revisa las asignaciones afectadas y "
                       "vuelve a generar o nivelar si corresponde.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        today = QDate.currentDate()
        self.start, self.end = _date_edit(today), _date_edit(today)
        self.hours = QDoubleSpinBox()
        self.hours.setRange(0, 24)
        self.hours.setDecimals(1)
        self.hours.setSuffix(" h/día disponibles")
        self.hours.setToolTip("0 = ausencia completa; por ejemplo 4 = media jornada.")
        self.reason = QLineEdit()
        self.reason.setMaxLength(200)
        self.reason.setPlaceholderText("Licencia médica, capacitación, vacaciones…")
        form.addRow("Desde", self.start)
        form.addRow("Hasta", self.end)
        form.addRow("Disponibilidad", self.hours)
        form.addRow("Motivo", self.reason)
        layout.addLayout(form)
        bar = Toolbar()
        bar.add_action("Vista previa", self.preview, icon="eye")
        bar.add_stretch()
        layout.addWidget(bar)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        self.days = _table(["Día", "Horas antes", "Horas después", "Motivo"])
        self.assignments = _table(["Código", "Actividad", "Inicio", "Término", "HH asignadas"])
        self.current = _table(["Día", "Laborable", "Horas", "Motivo"])
        self.tabs.addTab(self.days, "Días")
        self.tabs.addTab(self.assignments, "Asignaciones afectadas")
        self.tabs.addTab(self.current, "Excepciones vigentes")
        layout.addWidget(self.tabs, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("Registrar ausencia")
        self.ok.setProperty("kind", "primary")
        self.ok.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cerrar")
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for widget in (self.start, self.end):
            widget.dateChanged.connect(self._invalidate)
        self.hours.valueChanged.connect(self._invalidate)
        self.reason.textChanged.connect(self._invalidate)
        self.load_current()

    def body(self) -> dict:
        return {"start": self.start.date().toPython().isoformat(), "end": self.end.date().toPython().isoformat(),
                "hours": self.hours.value(), "reason": self.reason.text().strip()}

    def validation_error(self) -> str | None:
        body = self.body()
        if body["end"] < body["start"]:
            return "La fecha «Hasta» no puede ser anterior a «Desde»."
        if not body["reason"]:
            return "Indica el motivo de la ausencia."
        return None

    def _invalidate(self, *_):
        self.preview_result = None
        self.ok.setEnabled(False)

    def load_current(self):
        client, rid = self.state.client, self.resource["id"]

        def done(rows):
            _fill(self.current, [(fmt_date(r.get("date")), "Sí" if r.get("working") else "No",
                                  fmt_num(r.get("hours"), 1), r.get("reason", "")) for r in rows])
        run_task(lambda: client.get(f"/resources/{rid}/absences"), done, lambda exc: None)

    def _post(self, dry_run: bool, done):
        error = self.validation_error()
        if error:
            self.summary.setText(error)
            return
        client, rid, body = self.state.client, self.resource["id"], self.body()
        run_task(lambda: client.post(f"/resources/{rid}/absences", body, params={"dry_run": "true" if dry_run else "false"}),
                 done, lambda exc: (self.summary.setText(str(getattr(exc, "message", exc))), show_error(exc, self)))

    def show_preview(self, result: dict):
        self.preview_result = result
        _fill(self.days, [(fmt_date(d["date"]), fmt_num(d["hours_before"], 1), fmt_num(d["hours_after"], 1), d["reason"])
                          for d in result.get("days", [])])
        _fill(self.assignments, [(a.get("code", ""), a.get("name", ""), fmt_date(a.get("planned_start")),
                                  fmt_date(a.get("planned_finish")), fmt_num(a.get("work_hours"), 1))
                                 for a in result.get("assignments", [])])
        lost = result.get("hours_before", 0) - result.get("hours_after", 0)
        text = (f"Disponibilidad: {fmt_num(result.get('hours_before'), 1)} h → {fmt_num(result.get('hours_after'), 1)} h "
                f"(−{fmt_num(lost, 1)} h). Asignaciones afectadas: {len(result.get('assignments', []))}.")
        if result.get("will_create_copy"):
            text += f" Se creará el calendario personal «{result['calendar']['name']}». {result.get('warning', '')}"
        self.summary.setText(text)
        self.ok.setEnabled(True)

    def preview(self):
        self._post(True, self.show_preview)

    def apply(self):
        if self.preview_result is None:
            self.preview()
            return

        def done(result):
            self.applied = result
            notify(f"Ausencia registrada para {self.resource.get('name', '')}"
                   + (f" · {len(result.get('assignments', []))} asignación(es) a revisar" if result.get("assignments") else ""))
            self.accept()
        self._post(False, done)
