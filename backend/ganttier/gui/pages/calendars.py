"""Calendarios independientes del proyecto y almanaque anual pintado con Qt."""
from __future__ import annotations

import calendar
from copy import deepcopy
from datetime import date
import re

from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QLabel, QListWidget,
                              QListWidgetItem, QSplitter, QPushButton, QScrollArea, QSpinBox, QTableView, QWidget)

from ..theme import tokens
from ..widgets.common import (Column, DictTableModel, Field, FormDialog, button, confirm,
                              fmt_date, fmt_num, hbox, notify, run_task, show_error)
from .base import Page
from ..widgets.responsive import scroll_layout

# UX1 puede integrarse antes o después de esta página.
from .. import theme
from ..widgets import common as _common
from ..widgets.icons import icon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

_SPACE = getattr(theme, "SPACE", dict(sm=8, md=12, lg=16))


def _button(text, slot=None, *, icon_name=None, primary=False, danger=False, kind=None):
    import inspect
    if "icon" in inspect.signature(button).parameters:
        return button(text, slot, primary=primary, danger=danger, icon=icon_name, kind=kind)
    result = button(text, slot, primary=primary, danger=danger)
    result.setProperty("kind", kind or ("primary" if primary else "danger" if danger else "secondary"))
    if icon_name:
        result.setIcon(icon(icon_name))
    return result


def _toolbar(*widgets):
    cls = getattr(_common, "Toolbar", None)
    bar = cls() if cls else QWidget()
    if not cls:
        bar.setProperty("toolbar", True)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SPACE["sm"])
    for widget in widgets:
        if cls:
            bar.add(widget) if widget is not None else bar.add_stretch()
        else:
            layout.addWidget(widget) if widget is not None else layout.addStretch()
    return bar


def _setup(view):
    setup = getattr(_common, "setup_table", None)
    if setup:
        setup(view, sortable=False)
    else:
        view.verticalHeader().hide()
        view.verticalHeader().setDefaultSectionSize(30)
        view.setAlternatingRowColors(True)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.horizontalHeader().setStretchLastSection(True)


def _fit(view):
    fit = getattr(_common, "fit_columns", None)
    if fit:
        fit(view)
    else:
        view.resizeColumnsToContents()
        for column in range(view.model().columnCount()):
            view.setColumnWidth(column, max(90, min(360, view.columnWidth(column) + 16)))


class _FallbackSection(QFrame):
    def __init__(self, title, subtitle=None):
        super().__init__()
        self.setProperty("card", True)
        self.body = QVBoxLayout(self)
        self.body.setSpacing(_SPACE["sm"])
        label = QLabel(title)
        label.setProperty("role", "section")
        self.body.addWidget(label)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setProperty("role", "caption")
            hint.setWordWrap(True)
            self.body.addWidget(hint)


Section = getattr(_common, "Section", _FallbackSection)



MONTHS = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
          "Septiembre", "Octubre", "Noviembre", "Diciembre")
WEEKDAYS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
DEFAULT = dict(name="", workweek="1111100", hours_per_day=8, holidays_country="CL",
               shift_pattern=None, shift_anchor=None, holidays_apply_to_shift=False, exceptions=[])


class YearCanvas(QWidget):
    day_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.year = date.today().year
        self.days = {}
        self.exceptions = {}
        self.hit_cells = []
        self.setMinimumSize(720, 790)
        self.setMouseTracking(True)
        self.setAccessibleName("Calendario anual: clic en un día para alternar una excepción")

    def set_days(self, year, days, exceptions):
        self.year = year
        self.days = {d["date"]: d for d in days}
        self.exceptions = {e["date"]: e for e in exceptions}
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = tokens()
        p.fillRect(self.rect(), QColor(t.surface))
        self.hit_cells = []
        width = self.width() // 3
        for month in range(1, 13):
            x0, y0 = ((month - 1) % 3) * width + 8, ((month - 1) // 3) * 195 + 8
            cw = (width - 20) // 7
            p.setPen(QColor(t.text))
            p.drawText(QRect(x0, y0, width - 20, 24), Qt.AlignLeft | Qt.AlignVCenter, MONTHS[month - 1])
            for k, label in enumerate(WEEKDAYS):
                p.drawText(QRect(x0 + k * cw, y0 + 24, cw, 20), Qt.AlignCenter, label)
            offset, count = calendar.monthrange(self.year, month)
            for day in range(1, count + 1):
                pos = offset + day - 1
                rect = QRect(x0 + pos % 7 * cw, y0 + 46 + pos // 7 * 23, cw - 2, 21)
                iso = date(self.year, month, day).isoformat()
                info = self.exceptions.get(iso) or self.days.get(iso)
                working = info and info.get("working") and info.get("hours", 0) > 0
                reason = (info or {}).get("reason", "")
                holiday = info and not working and reason and not re.search("descanso|fin de semana|no laborable", reason, re.I)
                bg = t.surface2 if info is None else t.holiday if holiday else t.accent_soft if working else t.weekend
                p.setBrush(QColor(bg))
                p.setPen(QPen(QColor(t.datadate), 1.5) if iso in self.exceptions else Qt.NoPen)
                p.drawRoundedRect(rect, 3, 3)
                p.setPen(QColor(t.text if working else t.text3))
                p.drawText(rect, Qt.AlignCenter, str(day))
                self.hit_cells.append((rect, iso))
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            for rect, iso in self.hit_cells:
                if rect.contains(event.position().toPoint()):
                    self.day_clicked.emit(iso)
                    break

    def mouseMoveEvent(self, event):
        for rect, iso in self.hit_cells:
            if rect.contains(event.position().toPoint()):
                info = self.exceptions.get(iso) or self.days.get(iso, {})
                self.setToolTip(f"{fmt_date(iso)} · {fmt_num(info.get('hours'))} h · {info.get('reason', 'Sin datos guardados')}")
                return
        self.setToolTip("")


class CalendarsPage(Page):
    title = "Calendarios"
    subtitle = "Jornadas, feriados y excepciones de trabajo"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.root.setSpacing(_SPACE["md"])
        self.calendars = []
        self.editing = None
        self.days = []
        self.dirty = False
        self.loading = True
        self.saving = False
        self._ticket = 0
        self._days_ticket = 0
        self._pid = state.pid
        self.data_pid = None
        self._publishing = False
        self.toolbar = _toolbar(
            _button("Nuevo", self.new_calendar, icon_name="plus"),
            _button("Editar", self.edit_properties, icon_name="pencil"),
            _button("Guardar", self.save, primary=True, icon_name="save"),
            _button("Descartar", self.discard, kind="ghost", icon_name="undo-2"),
            _button("Eliminar", self.delete, danger=True, icon_name="trash-2"), None,
            _button("Calculadora de días…", self.open_calculator, kind="ghost", icon_name="calendar-clock"))
        self.root.addWidget(self.toolbar)
        panels = QSplitter(Qt.Horizontal)
        panels.setChildrenCollapsible(False)
        self.list = QListWidget()
        self.list.setMinimumWidth(210)
        self.list.setMaximumWidth(330)
        self.list.setSpacing(_SPACE["sm"])
        self.list.setWordWrap(True)
        self.list.setAccessibleName("Calendarios disponibles")
        self.list.currentItemChanged.connect(self._selected)
        panels.addWidget(self.list)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(_SPACE["md"])
        panels.addWidget(detail)
        panels.setStretchFactor(1, 1)
        self.root.addWidget(panels, 1)
        properties = Section("Propiedades")
        detail_layout.addWidget(properties)
        self.summary = QLabel("Selecciona o crea un calendario.")
        self.summary.setWordWrap(True)
        properties.body.addWidget(self.summary)
        self.weekdays = [QCheckBox(w) for w in WEEKDAYS]
        properties.body.addLayout(hbox(QLabel("Días laborables:"), *self.weekdays, None))
        for check in self.weekdays:
            check.toggled.connect(self._week_changed)
        self.exception_model = DictTableModel([
            Column("date", "Fecha", kind="date"), Column("working", "Laborable", kind="bool"),
            Column("hours", "Horas", kind="float", dec=1), Column("reason", "Motivo")])
        self.exceptions_table = QTableView()
        self.exceptions_table.setModel(self.exception_model)
        self.exceptions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.exceptions_table.setMaximumHeight(125)
        self.exceptions_table.horizontalHeader().setStretchLastSection(True)
        _setup(self.exceptions_table)
        exceptions = Section("Excepciones")
        exceptions.body.addWidget(_toolbar(_button("Agregar", self.add_exception, icon_name="plus"),
                                           _button("Quitar", self.remove_exception, icon_name="trash-2", kind="ghost"), None))
        exceptions.body.addWidget(self.exceptions_table)
        self.exceptions_empty = QLabel("Sin excepciones. Agrega una fecha especial o selecciona un día en la vista anual.")
        self.exceptions_empty.setProperty("role", "caption")
        self.exceptions_empty.setWordWrap(True)
        exceptions.body.addWidget(self.exceptions_empty)
        self.exceptions_table.hide()
        detail_layout.addWidget(exceptions)
        annual = Section("Vista anual")
        detail_layout.addWidget(annual, 1)
        self.year = QSpinBox()
        self.year.setRange(1901, 9998)
        self.year.setValue(date.today().year)
        self.year.valueChanged.connect(self.load_days)
        self.totals = QLabel()
        annual.body.addLayout(hbox(QLabel("Año"), self.year, self.totals, None))
        self.canvas = YearCanvas()
        self.canvas.day_clicked.connect(self.toggle_day)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        annual.body.addWidget(scroll, 1)
        self.legend = QLabel()
        annual.body.addWidget(self.legend)
        self.status = QLabel("Los cambios y excepciones se confirman con Guardar.")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "caption")
        self.root.addWidget(self.status)
        self.on_theme()
        state.catalogs_changed.connect(self._catalog_changed)
        self.exception_model.data_pid = None
        self.list.model().data_pid = None
        self._data_widgets = self.findChildren(QPushButton) + [self.list, self.canvas, *self.weekdays]
        self.root.addWidget(_toolbar(_button("Actualizar", self.refresh, icon_name="refresh-cw", kind="ghost"), None))
        self._actions()

        self.content_scroll = scroll_layout(self.root)
        self.root.addWidget(_toolbar(_button("Guardar calendario", self.save, primary=True, icon_name="save"),
                                     _button("Descartar", self.discard, kind="ghost", icon_name="undo-2"), None))

    def _actions(self):
        ready = self.data_pid == self.state.pid and not self.loading and not self.saving
        for widget in self._data_widgets:
            widget.setEnabled(ready)
        for widget in self.weekdays:
            widget.setEnabled(ready and self.editing is not None and not self.editing.get("shift_pattern"))

    def _can_mutate(self):
        if self.data_pid != self.state.pid or self.exception_model.data_pid != self.state.pid:
            notify("Los datos pertenecen a otro proyecto. Usa Actualizar.", "info")
            return False
        return not self.loading and not self.saving

    def open_calculator(self):
        """P5-08: término y conteo de días laborables con feriados, turnos y excepciones."""
        from ..tools_dialogs import WorkdaysDialog
        current = self.editing.get("id") if isinstance(getattr(self, "editing", None), dict) else None
        if not self.state.calendars and self.calendars:
            self.state.calendars = list(self.calendars)
        dialog = WorkdaysDialog(self.state, self, current)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.open()
        return dialog

    def has_unsaved(self) -> bool:
        """A3-06: el calendario en edición tiene cambios sin guardar."""
        return bool(self.dirty)

    def blocks_project_change(self) -> bool:
        """A3-05: cambiar de proyecto descarta el borrador; la ventana pide confirmación antes."""
        return bool(self.dirty)

    def _sync_project(self):
        if self._pid == self.state.pid:
            return
        self._pid = self.state.pid
        self._ticket += 1
        self._days_ticket += 1
        self.data_pid = self.exception_model.data_pid = self.list.model().data_pid = None
        self.loading = True
        self.calendars, self.days = [], []
        self.editing, self.dirty = None, False
        previous = self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(previous)
        self.exceptions_table.clearSelection()
        self._display()
        self.totals.clear()
        self._actions()

    def _mark_stale(self):
        self._sync_project()
        if not self._publishing:
            super()._mark_stale()

    def _catalog_changed(self):
        if not self.dirty and not self.saving and not self._publishing:
            self._mark_stale()

    def refresh(self, *, after_write=False):
        self._sync_project()
        pid, client = self.state.pid, self.state.client
        self._ticket += 1
        ticket = self._ticket
        self.loading = True
        self._actions()

        def done(data):
            if ticket != self._ticket or pid != self.state.pid:
                return
            rows, resources = data
            self.loading = False
            self.data_pid = self.exception_model.data_pid = self.list.model().data_pid = pid
            self.calendars = rows
            cid = (self.editing or {}).get("id")
            self.list.blockSignals(True)
            self.list.clear()
            for c in rows:
                week = c.get("workweek", "1111100")
                pattern = "L-V" if week == "1111100" else " · ".join(w for w, active in zip(WEEKDAYS, week) if active == "1")
                pattern = f"Turno {c['shift_pattern']}" if c.get("shift_pattern") else pattern
                holidays = f"Feriados {c['holidays_country']}" if c.get("holidays_country") else "Sin feriados"
                summary = f"{fmt_num(c.get('hours_per_day', 8), 0 if float(c.get('hours_per_day', 8)).is_integer() else 1)} h/día · {pattern} · {holidays}"
                item = QListWidgetItem(icon("calendar"), c["name"] + "\n" + summary)
                item.setToolTip(c["name"] + "\n" + summary)
                item.setData(Qt.UserRole, c["id"])
                self.list.addItem(item)
            index = next((i for i, c in enumerate(rows) if c["id"] == cid), 0)
            if rows:
                self.list.setCurrentRow(index)
            self.list.blockSignals(False)
            if not self.dirty:
                self.set_calendar(rows[index] if rows else None, after_write=after_write)
            self._actions()
            if after_write:
                self.state.calendars, self.state.resources = rows, resources
                self._publishing = True
                try:
                    self.state.catalogs_changed.emit()
                    self.state.bump_schedule()
                finally:
                    self._publishing = False

        def failed(exc):
            if ticket == self._ticket and pid == self.state.pid:
                self.loading = False
                self._stale = True
                self._actions()
                if after_write:
                    notify("Guardado; no se pudo actualizar la vista. Usa Actualizar.", "info")
                else:
                    show_error(exc, self)
        run_task(lambda: (client.calendars(), client.resources(pid) if after_write else None),
                 done, failed, busy="Cargando calendarios…")

    def _selected(self, item, previous):
        if item is None or not self._can_mutate():
            return
        pid = self.state.pid
        cid = item.data(Qt.UserRole)
        discard = not self.dirty or confirm(self, "Cambios sin guardar", "¿Descartar los cambios del calendario actual?")
        if pid != self.state.pid or not self._can_mutate():
            return
        if not discard:
            self.list.blockSignals(True)
            self.list.setCurrentItem(previous)
            self.list.blockSignals(False)
            return
        self.set_calendar(next(c for c in self.calendars if c["id"] == cid))

    def set_calendar(self, cal, *, after_write=False):
        self.editing = deepcopy(cal)
        self.dirty = False
        self.days = []
        self._display()
        self.load_days(after_write=after_write)

    def _display(self):
        v = self.editing or DEFAULT
        for k, check in enumerate(self.weekdays):
            check.blockSignals(True)
            check.setChecked(v.get("workweek", "1111100")[k] == "1")
            check.setEnabled(bool(self.editing) and not v.get("shift_pattern")
                             and not self.loading and not self.saving and self.data_pid == self.state.pid)
            check.blockSignals(False)
        self.summary.setText(f"{v.get('name') or 'Nuevo calendario'} · {fmt_num(v.get('hours_per_day'), 1)} h/día · "
                             f"{('Turno ' + v['shift_pattern']) if v.get('shift_pattern') else 'Semana laboral'} · "
                             f"{('Feriados ' + v['holidays_country']) if v.get('holidays_country') else 'Sin feriados'}")
        self.exception_model.set_rows(v.get("exceptions", []))
        _fit(self.exceptions_table)
        self.exceptions_table.setVisible(self.exception_model.rowCount() > 0)
        self.exceptions_empty.setVisible(self.exception_model.rowCount() == 0)
        self.canvas.set_days(self.year.value(), self.days, v.get("exceptions", []))
        self.status.setText("Cambios pendientes. El calendario anual muestra los días guardados y las excepciones en edición; los totales se actualizan al guardar."
                            if self.dirty else "Clic en un día: alternar excepción. Los cambios se confirman con Guardar.")

    @staticmethod
    def _fields():
        return [Field("name", "Nombre", span=True), Field("hours_per_day", "Horas/día", "float"),
                Field("holidays_country", "Feriados de país", "choice", [("CL", "Chile"), (None, "Ninguno")]),
                Field("shift_pattern", "Turno NxM (vacío = semana laboral)"),
                Field("shift_anchor", "Ancla del turno", "date"),
                Field("holidays_apply_to_shift", "Aplicar feriados al turno", "bool")]

    @staticmethod
    def _validate(v):
        if not v.get("name", "").strip():
            return "Ingresa un nombre para el calendario."
        if v.get("shift_pattern") and (not re.fullmatch(r"[1-9]\d*x[1-9]\d*", v["shift_pattern"]) or not v.get("shift_anchor")):
            return "El turno necesita un patrón NxM positivo (por ejemplo, 7x7) y una fecha de ancla."
        if not re.fullmatch("[01]{7}", v.get("workweek", "1111100")):
            return "Selecciona una semana laboral válida."
        return None

    def new_calendar(self):
        if not self._can_mutate():
            return
        pid = self.state.pid
        if self.dirty and not confirm(self, "Cambios sin guardar", "¿Descartar los cambios del calendario actual?"):
            return
        dlg = FormDialog("Nuevo calendario", self._fields(), deepcopy(DEFAULT), self, validate=self._validate)
        if dlg.exec():
            if pid != self.state.pid or not self._can_mutate():
                notify("El proyecto cambió; no se guardó el calendario.", "info")
                return
            self.set_calendar(dlg.values())
            self.editing["shift_pattern"] = self.editing.get("shift_pattern") or None
            self.dirty = True
            self._display()

    def edit_properties(self):
        if not self._can_mutate() or not self.editing:
            return
        pid = self.state.pid
        dlg = FormDialog("Propiedades del calendario", self._fields(), self.editing, self, validate=self._validate)
        if dlg.exec():
            if pid != self.state.pid or not self._can_mutate():
                notify("El proyecto cambió; no se guardó el calendario.", "info")
                return
            self.editing.update(dlg.values())
            self.editing["shift_pattern"] = self.editing.get("shift_pattern") or None
            self.dirty = True
            self._display()

    def _week_changed(self, *_):
        if self._can_mutate() and self.editing:
            self.editing["workweek"] = "".join("1" if w.isChecked() else "0" for w in self.weekdays)
            self.dirty = True
            self._display()

    def add_exception(self, values=None):
        if not self._can_mutate() or not self.editing:
            return
        pid = self.state.pid
        if values is None:
            fields = [Field("date", "Fecha", "date"), Field("working", "Laborable", "bool"),
                      Field("hours", "Horas (si es laborable)", "float"), Field("reason", "Motivo")]
            dlg = FormDialog("Agregar excepción", fields, {"date": date.today().isoformat(), "working": False,
                             "hours": self.editing["hours_per_day"], "reason": ""}, self,
                             validate=lambda v: None if v.get("date") else "Indica una fecha.")
            if not dlg.exec():
                return
            values = dlg.values()
        if pid != self.state.pid or not self._can_mutate():
            notify("El proyecto cambió; no se modificó la excepción.", "info")
            return
        values = dict(values)
        if not values["working"]:
            values["hours"] = 0
        rows = [e for e in self.editing.get("exceptions", []) if e["date"] != values["date"]]
        self.editing["exceptions"] = sorted(rows + [values], key=lambda e: e["date"])
        self.dirty = True
        self._display()

    def remove_exception(self, day=None):
        if not self._can_mutate() or not self.editing:
            return
        if day is None:
            idx = self.exceptions_table.currentIndex()
            if not idx.isValid():
                return
            day = self.exception_model.row_dict(idx.row())["date"]
        self.editing["exceptions"] = [e for e in self.editing.get("exceptions", []) if e["date"] != day]
        self.dirty = True
        self._display()

    def toggle_day(self, day):
        if not self._can_mutate() or not self.editing:
            return
        if any(e["date"] == day for e in self.editing.get("exceptions", [])):
            self.remove_exception(day)
            return
        info = next((d for d in self.days if d["date"] == day), None)
        if info is None:
            notify("Guarda el calendario para consultar sus días antes de alternar excepciones.", "info")
            return
        working = not info["working"]
        self.add_exception(dict(date=day, working=working, hours=self.editing["hours_per_day"] if working else 0,
                                reason="Día laborable especial" if working else "Día no laborable"))

    def load_days(self, *_, after_write=False):
        self._days_ticket += 1
        ticket = self._days_ticket
        pid = self.data_pid
        cid = (self.editing or {}).get("id")
        year = self.year.value()
        self.days = []
        self._display()
        self.totals.setText("Consultando días…" if cid else "Guarda el calendario para consultar los días del año.")
        if not cid:
            return

        def done(days):
            if ticket != self._days_ticket or pid != self.state.pid or cid != (self.editing or {}).get("id"):
                return
            self.days = days
            self.totals.setText(f"Guardado: {sum(bool(d['working']) for d in days)} días laborables · "
                                f"{fmt_num(sum(d['hours'] for d in days), 1)} h")
            self._display()

        def failed(exc):
            if ticket == self._days_ticket and pid == self.state.pid:
                self.totals.setText("No se pudieron consultar los días.")
                if after_write:
                    notify("Guardado; no se pudo actualizar la vista. Usa Actualizar.", "info")
                else:
                    show_error(exc, self)
        run_task(lambda: self.state.client.calendar_days(cid, f"{year}-01-01", f"{year}-12-31"),
                 done, failed, busy="Consultando calendario anual…")

    def save(self):
        if not self._can_mutate() or not self.editing:
            return
        error = self._validate(self.editing)
        if error:
            show_error(ValueError(error), self)
            return
        cid = self.editing.get("id")
        payload = {k: deepcopy(self.editing.get(k, v)) for k, v in DEFAULT.items()}
        payload["shift_pattern"] = payload["shift_pattern"] or None
        self.saving = True
        self._actions()
        pid, client = self.state.pid, self.state.client

        def work():
            return client.update_calendar(cid, payload) if cid else client.create_calendar(payload)

        def done(cal):
            self.saving = False
            self.last_saved = cal
            self._actions()
            if pid == self.state.pid:
                # Publicar el ID confirmado antes de cualquier GET: reintentar es PUT.
                self.editing = deepcopy(cal)
                self.dirty = False
                self._display()
                self.refresh(after_write=True)
            notify("Calendario guardado")
        run_task(work, done, self._failed, busy="Guardando calendario…")

    def _failed(self, exc):
        self.saving = False
        self._actions()
        show_error(exc, self)

    def discard(self):
        if self._can_mutate():
            self.dirty = False
            self.refresh()

    def delete(self):
        if not self._can_mutate():
            return
        pid = self.state.pid
        v = self.editing or {}
        if not v.get("id") or self.saving or not confirm(self, "Eliminar calendario", f"¿Eliminar «{v['name']}»?", danger=True):
            return
        if pid != self.state.pid or not self._can_mutate():
            notify("El proyecto cambió; no se eliminó el calendario.", "info")
            return
        cid = v["id"]
        self.saving = True
        self._actions()
        pid, client = self.state.pid, self.state.client

        def work():
            client.delete_calendar(cid)

        def done(_):
            self.saving = False
            self._actions()
            if pid == self.state.pid:
                self.set_calendar(None)
                self.refresh(after_write=True)
            notify("Calendario eliminado")
        run_task(work, done, self._failed, busy="Eliminando calendario…")

    def on_theme(self):
        t = tokens()
        for index in range(self.list.count()):
            self.list.item(index).setIcon(icon("calendar"))
        self.legend.setText(f'<span style="background:{t.accent_soft};color:{t.text}"> Laborable </span> · '
                            f'<span style="background:{t.weekend};color:{t.text}"> No laborable </span> · '
                            f'<span style="background:{t.holiday};color:{t.text}"> Feriado </span> · '
                            f'<span style="color:{t.datadate}">Contorno: excepción</span>')
        self.canvas.update()
