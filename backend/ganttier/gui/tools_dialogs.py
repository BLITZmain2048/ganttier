"""Herramientas P5: cadenas de relaciones (P5-09) y calculadora de días laborables (P5-08)."""
from __future__ import annotations

from .widgets.responsive import ResponsiveDialog

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QLabel, QPlainTextEdit,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QAbstractItemView)

from .widgets.common import fmt_date, fmt_num, notify, run_task, show_error, Toolbar


def parse_groups(text: str) -> list[list[str]]:
    """Una línea por código (o separados por coma/tabulación); una línea en blanco separa cadenas."""
    groups, current = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                groups.append(current)
                current = []
            continue
        for part in line.replace("\t", ",").replace(";", ",").split(","):
            code = part.strip()
            if code:
                current.append(code)
    if current:
        groups.append(current)
    return groups


class ChainDialog(ResponsiveDialog):
    """Crea relaciones consecutivas desde una lista ordenada de códigos, con vista previa."""

    def __init__(self, state, parent=None, initial_codes: list[str] | None = None):
        super().__init__(parent)
        self.state = state
        self.applied = None
        self.setWindowTitle("Crear cadena de relaciones")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        help_text = QLabel("Pega los códigos en el orden de ejecución (uno por línea, o separados por coma). "
                           "Una línea en blanco empieza otra cadena independiente. Se crea A→B, B→C, … "
                           "Si un par ya tiene otra relación o se formaría un ciclo, no se crea ninguna.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.codes = QPlainTextEdit("\n".join(initial_codes or []))
        self.codes.setPlaceholderText("IB01\nIB02\nIB03\n\nPE01\nPE02")
        layout.addWidget(self.codes, 1)
        form = QFormLayout()
        self.type = QComboBox()
        for key, label in (("FS", "FS · fin a comienzo"), ("SS", "SS · comienzo a comienzo"),
                           ("FF", "FF · fin a fin"), ("SF", "SF · comienzo a fin")):
            self.type.addItem(label, key)
        self.lag = QSpinBox()
        self.lag.setRange(-365, 365)
        self.lag.setSuffix(" días")
        form.addRow("Tipo de relación", self.type)
        form.addRow("Desfase", self.lag)
        layout.addLayout(form)
        bar = Toolbar()
        bar.add_action("Vista previa", self.preview, icon="eye")
        bar.add_stretch()
        layout.addWidget(bar)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Predecesora", "Sucesora", "Estado"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Crear relaciones")
        self.buttons.button(QDialogButtonBox.Ok).setProperty("kind", "primary")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def body(self) -> dict:
        return {"groups": parse_groups(self.codes.toPlainText()), "type": self.type.currentData(), "lag_days": self.lag.value()}

    def _request(self, dry_run: bool, done):
        body = self.body()
        if not body["groups"] or all(len(g) < 2 for g in body["groups"]):
            self.summary.setText("Ingresa al menos dos códigos por cadena.")
            return
        client, pid = self.state.client, self.state.pid
        run_task(lambda: client.post(f"/projects/{pid}/relations/chain", body, params={"dry_run": "true"} if dry_run else None),
                 done, lambda exc: (self.summary.setText(str(getattr(exc, "message", exc))), show_error(exc, self)))

    def _pairs(self) -> list[tuple[str, str]]:
        pairs, seen = [], set()
        for group in parse_groups(self.codes.toPlainText()):
            for pair in zip(group, group[1:]):
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)
        return pairs

    def _show(self, result):
        rows = result.get("relations", [])
        pairs = self._pairs()
        self.table.setRowCount(len(rows))
        for r, rel in enumerate(rows):
            pred, succ = pairs[r] if r < len(pairs) else (rel.get("pred_id"), rel.get("succ_id"))
            status = "nueva" if rel.get("id") is None else "ya existe"
            for c, text in enumerate((str(pred), str(succ), status)):
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.summary.setText(f"Se crearán {fmt_num(result.get('created', 0))} relaciones; "
                             f"{fmt_num(result.get('existing', 0))} ya existían.")

    def preview(self):
        self._request(True, self._show)

    def apply(self):
        def done(result):
            self.applied = result
            notify(f"Relaciones creadas: {result.get('created', 0)} (ya existían {result.get('existing', 0)})")
            self.accept()
        self._request(False, done)


class WorkdaysDialog(ResponsiveDialog):
    """Calculadora de días laborables sobre un calendario (feriados de Chile, turnos y excepciones)."""

    def __init__(self, state, parent=None, calendar_id: int | None = None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Calculadora de días laborables")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.calendar = QComboBox()
        for cal in state.calendars or []:
            self.calendar.addItem(cal["name"], cal["id"])
        if calendar_id is not None and self.calendar.findData(calendar_id) >= 0:
            self.calendar.setCurrentIndex(self.calendar.findData(calendar_id))
        self.start = QDateEdit(QDate.currentDate())
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("dd-MM-yyyy")
        self.days = QSpinBox()
        self.days.setRange(-3650, 3650)
        self.days.setValue(10)
        self.days.setSuffix(" días laborables")
        self.end = QDateEdit(QDate.currentDate().addDays(30))
        self.end.setCalendarPopup(True)
        self.end.setDisplayFormat("dd-MM-yyyy")
        form.addRow("Calendario", self.calendar)
        form.addRow("Desde", self.start)
        form.addRow("Sumar (negativo: restar)", self.days)
        form.addRow("Hasta (para contar)", self.end)
        layout.addLayout(form)
        bar = Toolbar()
        bar.add_action("Calcular término", self.add_days, icon="calendar-clock", primary=True)
        bar.add_action("Contar días entre fechas", self.between, icon="calendar-range")
        bar.add_stretch()
        layout.addWidget(bar)
        self.result_label = QLabel("Convención: días inclusivos (una tarea de 1 día empieza y termina el mismo día).")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        self.excluded = QTableWidget(0, 2)
        self.excluded.setHorizontalHeaderLabels(["Día no laborable", "Motivo"])
        self.excluded.verticalHeader().hide()
        self.excluded.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.excluded.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.excluded, 1)

    def _iso(self, editor) -> str:
        return editor.date().toPython().isoformat()

    def _show_excluded(self, items):
        self.excluded.setRowCount(len(items))
        for r, item in enumerate(items):
            self.excluded.setItem(r, 0, QTableWidgetItem(fmt_date(item.get("date"))))
            self.excluded.setItem(r, 1, QTableWidgetItem(str(item.get("reason", ""))))
        self.excluded.resizeColumnsToContents()

    def add_days(self):
        cid, client = self.calendar.currentData(), self.state.client
        if cid is None:
            return
        params = {"start": self._iso(self.start), "days": self.days.value()}

        def done(result):
            self.result_label.setText(f"Término: {fmt_date(result.get('result'))} · {fmt_num(result.get('workdays'))} días laborables "
                                f"({fmt_num(result.get('hours'), 1)} h) en «{result.get('calendar_name')}». Días inclusivos.")
            self._show_excluded(result.get("excluded", []))
        run_task(lambda: client.get(f"/calendars/{cid}/workdays", params=params), done, lambda exc: show_error(exc, self))

    def between(self):
        cid, client = self.calendar.currentData(), self.state.client
        if cid is None:
            return
        params = {"start": self._iso(self.start), "end": self._iso(self.end)}

        def done(result):
            self.result_label.setText(f"Entre {fmt_date(result.get('start'))} y {fmt_date(result.get('end'))} hay "
                                f"{fmt_num(result.get('workdays'))} días laborables ({fmt_num(result.get('hours'), 1)} h), "
                                f"ambos extremos incluidos.")
            self._show_excluded(result.get("excluded", []))
        run_task(lambda: client.get(f"/calendars/{cid}/workdays/between", params=params), done, lambda exc: show_error(exc, self))
