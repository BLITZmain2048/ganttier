"""Matriz macro virtual: solo se pintan las filas y meses visibles."""
from __future__ import annotations

from bisect import bisect_right
import calendar
from datetime import date, timedelta

from PySide6.QtCore import Qt, QRect, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QAbstractScrollArea, QCheckBox, QLabel, QLineEdit

from ..theme import qcolor, tokens
from ..widgets.common import button, fmt_num, hbox, notify, run_task, show_error
from .base import Page
from ..widgets.responsive import scroll_layout
from .activities import Toolbar, SearchBox, EmptyState, ux_button, import_template
from ..widgets.icons import icon
from .optimize import applicable, solve_program


def month_bounds(key):
    year, month = map(int, key.split("-"))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def decode_columns(columns):
    names = {"ids": "id", "codes": "code", "names": "name", "locations": "location",
             "starts": "planned_start", "finishes": "planned_finish", "critical": "critical",
             "window_starts": "window_start", "window_ends": "window_end",
             "window_hards": "window_hard", "macro_months": "macro_month", "work_hours": "work_hours"}
    arrays = [(names[key], value) for key, value in columns.items() if key in names]
    rows = [{name: values[i] for name, values in arrays} for i in range(len(columns.get("ids", [])))]
    return rows


def window_of(row):
    start, end = row.get("window_start"), row.get("window_end")
    if not start and not end and row.get("macro_month"):
        a, b = month_bounds(row["macro_month"])
        return a.isoformat(), b.isoformat()
    return start or end, end or start


def build_months(project, rows):
    start = date.fromisoformat(project["start_date"])
    finish = date.fromisoformat(project["end_date"]) if project.get("end_date") else start + timedelta(days=365)
    for row in rows:
        ws, we = window_of(row)
        for value in (row.get("planned_start"), row.get("planned_finish"), ws, we):
            if value:
                d = date.fromisoformat(value)
                start, finish = min(start, d), max(finish, d)
    result = []
    year, month = start.year, start.month
    while (year, month) <= (finish.year, finish.month):
        key = f"{year:04d}-{month:02d}"
        a, b = month_bounds(key)
        weekdays = sum(date(year, month, d).weekday() < 5 for d in range(1, b.day + 1))
        result.append(dict(key=key, start=a.isoformat(), end=b.isoformat(), days=b.day, weekdays=weekdays))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def monthly_hours(rows, pending, months):
    """Reparto por días corridos O(n log m + m), sin recorrer los meses por actividad."""
    if not months:
        return []
    starts = [date.fromisoformat(m["start"]).toordinal() for m in months]
    ends = [date.fromisoformat(m["end"]).toordinal() for m in months]
    totals, difference = [0.0] * len(months), [0.0] * (len(months) + 1)
    for row in rows:
        effective = pending.get(row["id"], row)
        a, b = window_of(effective)
        a = a or row.get("planned_start")
        b = b or row.get("planned_finish") or a
        if not a or not b or not row.get("work_hours"):
            continue
        a, b = date.fromisoformat(a).toordinal(), date.fromisoformat(b).toordinal()
        if b < a or b < starts[0] or a > ends[-1]:
            continue
        per_day = row["work_hours"] / (b - a + 1)
        a, b = max(a, starts[0]), min(b, ends[-1])
        lo, hi = bisect_right(starts, a) - 1, bisect_right(starts, b) - 1
        if lo == hi:
            totals[lo] += (b - a + 1) * per_day
        else:
            totals[lo] += (ends[lo] - a + 1) * per_day
            totals[hi] += (b - starts[hi] + 1) * per_day
            difference[lo + 1] += per_day
            difference[hi] -= per_day
    running = 0.0
    for i, month in enumerate(months):
        running += difference[i]
        totals[i] += running * month["days"]
    return totals


class MacroMatrix(QAbstractScrollArea):
    range_assigned = Signal(object, int, int)
    cleared = Signal(object)
    selection_changed = Signal()
    ROW, LEFT, CELL, HEADER, FOOTER = 28, 330, 92, 30, 52

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.selected = set()
        self.anchor = None
        self.drag = None
        self.focus_cell = (0, 0)
        self.last_painted_rows = 0
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Matriz de programación macro")
        self.setMinimumHeight(200)
        self.verticalScrollBar().valueChanged.connect(lambda *_: self.viewport().update())
        self.horizontalScrollBar().valueChanged.connect(lambda *_: self.viewport().update())

    def update_ranges(self):
        self.verticalScrollBar().setRange(0, max(0, len(self.page.visible) * self.ROW -
                                               (self.viewport().height() - self.HEADER - self.FOOTER)))
        self.verticalScrollBar().setPageStep(max(self.ROW, self.viewport().height() - self.HEADER - self.FOOTER))
        self.verticalScrollBar().setSingleStep(self.ROW)
        self.horizontalScrollBar().setRange(0, max(0, len(self.page.months) * self.CELL -
                                                 (self.viewport().width() - self.LEFT)))
        self.horizontalScrollBar().setSingleStep(self.CELL)
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_ranges()

    def scrollContentsBy(self, dx, dy):
        self.viewport().update()

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        t, page = tokens(), self.page
        w, h = self.viewport().width(), self.viewport().height()
        p.fillRect(self.viewport().rect(), QColor(t.surface))
        sy, sx = self.verticalScrollBar().value(), self.horizontalScrollBar().value()
        r0 = sy // self.ROW
        r1 = min(len(page.visible), (sy + h - self.HEADER - self.FOOTER) // self.ROW + 1)
        m0 = sx // self.CELL
        m1 = min(len(page.months), (sx + max(0, w - self.LEFT)) // self.CELL + 1)
        self.last_painted_rows = max(0, r1 - r0)
        p.save()
        p.setClipRect(QRect(0, self.HEADER, w, max(0, h - self.HEADER - self.FOOTER)))
        for pos in range(r0, r1):
            row = page.rows[page.visible[pos]]
            aid, y = row["id"], self.HEADER + pos * self.ROW - sy
            selected = aid in self.selected
            p.fillRect(QRect(0, y, w, self.ROW), QColor(t.accent_soft if selected else t.surface2 if aid in page.pending else t.surface))
            effective = page.pending.get(aid, row)
            ws, we = window_of(effective)
            ps, pf = row.get("planned_start"), row.get("planned_finish") or row.get("planned_start")
            p.save()
            p.setClipRect(QRect(self.LEFT, y, max(0, w - self.LEFT), self.ROW), Qt.IntersectClip)
            for k in range(m0, m1):
                month = page.months[k]
                x = self.LEFT + k * self.CELL - sx
                if ws and we and ws <= month["end"] and we >= month["start"]:
                    p.fillRect(QRect(x, y + 4, self.CELL, self.ROW - 8), qcolor(t.bar_window_hard if effective.get("window_hard") else t.bar_window))
                if self.drag and aid in self.drag[3] and min(self.drag[1:3]) <= k <= max(self.drag[1:3]):
                    p.fillRect(QRect(x, y + 4, self.CELL, self.ROW - 8), QColor(t.accent_soft))
                if ps and pf and ps <= month["end"] and pf >= month["start"]:
                    p.fillRect(QRect(x + 5, y + 12, self.CELL - 10, 4), QColor(t.bar_crit if row.get("critical") else t.bar))
                p.setPen(QColor(t.grid))
                p.drawRect(QRect(x, y, self.CELL, self.ROW))
            p.restore()
            p.setPen(QColor(t.text))
            label = f"{row.get('code', '')}  {row.get('name', '')}"
            label = p.fontMetrics().elidedText(label, Qt.ElideRight, self.LEFT - 85)
            p.drawText(QRect(8, y, self.LEFT - 80, self.ROW), Qt.AlignVCenter, label)
            p.drawText(QRect(self.LEFT - 73, y, 58, self.ROW), Qt.AlignRight | Qt.AlignVCenter, fmt_num(row.get("work_hours")))
            if aid in page.pending:
                p.setPen(QColor(t.datadate))
                p.drawText(QRect(self.LEFT - 13, y, 12, self.ROW), Qt.AlignCenter, "●")
        p.restore()
        for y, height in ((0, self.HEADER), (h - self.FOOTER, self.FOOTER)):
            p.fillRect(QRect(0, y, w, height), QColor(t.surface2))
        p.save()
        p.setClipRect(QRect(self.LEFT, 0, max(0, w - self.LEFT), h))
        for k in range(m0, m1):
            month = page.months[k]
            x = self.LEFT + k * self.CELL - sx
            p.setPen(QColor(t.text2))
            year, number = month["key"].split("-")
            p.drawText(QRect(x, 0, self.CELL, self.HEADER), Qt.AlignCenter, f"{number}/{year}")
            hours = page.totals[k] if k < len(page.totals) else None
            capacity = page.cap_per_day * month["weekdays"]
            ratio = hours / capacity if hours is not None and capacity > 0 else None
            color = (t.critical if hours and capacity <= 0 else t.text2 if ratio is None
                     else t.critical if ratio > 1 else t.warning if ratio > .85 else t.good)
            p.setPen(QColor(color))
            # Tres líneas cortas: HH, utilización y capacidad (no caben en una sola celda de 92 px).
            text = (f"{fmt_num(hours)} HH\n{fmt_num(ratio * 100)} %" if ratio is not None
                    else f"{fmt_num(hours)} HH\n—" if not hours else f"{fmt_num(hours)} HH\nsin cap.") + f"\n{fmt_num(capacity)} cap."
            small = QFont(p.font())
            small.setPointSizeF(max(7.0, p.font().pointSizeF() - 1))
            p.save()
            p.setFont(small)
            p.drawText(QRect(x, h - self.FOOTER, self.CELL, self.FOOTER), Qt.AlignCenter, text)
            p.restore()
        p.restore()
        p.setPen(QColor(t.text2))
        p.drawText(QRect(8, 0, self.LEFT - 16, self.HEADER), Qt.AlignVCenter, "Código · Actividad                                      HH")
        p.drawText(QRect(8, h - self.FOOTER, self.LEFT - 16, self.FOOTER), Qt.AlignVCenter,
                   "HH estimadas por días corridos\nCapacidad L–V, sin feriados ni turnos")
        if not page.visible:
            p.drawText(QRect(0, self.HEADER, w, max(0, h - self.HEADER - self.FOOTER)), Qt.AlignCenter,
                       "Cargando actividades…" if page.loading else "No hay actividades para este filtro.")
        p.end()

    def _hit(self, point):
        if not self.HEADER <= point.y() < self.viewport().height() - self.FOOTER:
            return None
        pos = (point.y() - self.HEADER + self.verticalScrollBar().value()) // self.ROW
        if not 0 <= pos < len(self.page.visible):
            return None
        month = (point.x() - self.LEFT + self.horizontalScrollBar().value()) // self.CELL if point.x() >= self.LEFT else -1
        return pos, month

    def _select(self, pos, modifiers):
        aid = self.page.rows[self.page.visible[pos]]["id"]
        if modifiers & Qt.ShiftModifier and self.anchor in self.page.positions:
            a = self.page.positions[self.anchor]
            self.selected = {self.page.rows[self.page.visible[k]]["id"] for k in range(min(a, pos), max(a, pos) + 1)}
        elif modifiers & Qt.ControlModifier:
            self.selected.symmetric_difference_update({aid})
            self.anchor = aid
        else:
            self.selected = {aid}
            self.anchor = aid
        self.selection_changed.emit()
        self.viewport().update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.page.saving or self.page.loading:
            return
        hit = self._hit(event.position().toPoint())
        if hit is None:
            return
        pos, month = hit
        aid = self.page.rows[self.page.visible[pos]]["id"]
        if month < 0 or event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
            self._select(pos, event.modifiers())
        if 0 <= month < len(self.page.months):
            if aid not in self.selected:
                self._select(pos, Qt.NoModifier)
            self.focus_cell = pos, month
            self.drag = [aid, month, month, list(self.selected)]
            self.viewport().update()

    def mouseMoveEvent(self, event):
        if self.drag:
            x = event.position().x()
            if x > self.viewport().width() - 12:
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + self.CELL // 3)
            elif x < self.LEFT + 12:
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - self.CELL // 3)
            month = int((x - self.LEFT + self.horizontalScrollBar().value()) // self.CELL)
            self.drag[2] = min(len(self.page.months) - 1, max(0, month))
            self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag:
            _, start, end, targets = self.drag
            self.drag = None
            self.range_assigned.emit(targets, start, end)

    def mouseDoubleClickEvent(self, event):
        hit = self._hit(event.position().toPoint())
        self.drag = None
        if event.button() == Qt.LeftButton and hit and hit[1] >= 0:
            aid = self.page.rows[self.page.visible[hit[0]]]["id"]
            self.cleared.emit(list(self.selected) if aid in self.selected else [aid])

    def keyPressEvent(self, event):
        if not self.page.visible or not self.page.months:
            return
        pos, month = self.focus_cell
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.cleared.emit(list(self.selected))
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.range_assigned.emit(list(self.selected), month, month)
        elif event.key() == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.selected = {self.page.rows[i]["id"] for i in self.page.visible}
        elif event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            pos = min(len(self.page.visible) - 1, max(0, pos + (event.key() == Qt.Key_Down) - (event.key() == Qt.Key_Up)))
            month = min(len(self.page.months) - 1, max(0, month + (event.key() == Qt.Key_Right) - (event.key() == Qt.Key_Left)))
            self.focus_cell = pos, month
            self._select(pos, event.modifiers())
            self.verticalScrollBar().setValue(pos * self.ROW)
            self.horizontalScrollBar().setValue(max(0, month * self.CELL - self.viewport().width() + self.LEFT + self.CELL))
        else:
            super().keyPressEvent(event)
        self.selection_changed.emit()
        self.viewport().update()


class MacroPage(Page):
    title = "Programación macro"
    subtitle = "Asigna meses y ventanas para generar un programa dentro de tus compromisos."

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.rows, self.visible, self.months, self.totals = [], [], [], []
        self.positions, self.by_id = {}, {}
        self.cap_per_day = 0
        self._pending = {}
        self.data_pid = None
        self.loading = False
        self.saving = False
        self.generating = False
        self._pid = state.pid
        self._search_source = None
        self._search_texts = []
        self._ticket = self._totals_ticket = 0
        self.has_hours = True
        self.search = SearchBox()
        self.search.setPlaceholderText("Buscar código, actividad o instalación…")
        self.only_unassigned = QCheckBox("Solo sin mes")
        self.hard = QCheckBox("Nuevas ventanas duras")
        self.toolbar = Toolbar()
        self.toolbar.add(self.search)
        self.toolbar.add(self.only_unassigned)
        self.toolbar.add(self.hard)
        self.toolbar.add_separator()
        self.save_button = ux_button("Guardar", self.save, icon="save")
        self.discard_button = ux_button("Descartar", self.discard, icon="undo-2", kind="ghost")
        self.generate_button = ux_button("Generar programa dentro de los meses", self.generate, primary=True, icon="wand-sparkles")
        self.clear_button = ux_button("Quitar mes", lambda: self.clear(list(self.matrix.selected)), icon="x", tip="Quitar mes de la selección")
        for control in (self.clear_button, self.discard_button, self.save_button):
            self.toolbar.add(control)
        self.toolbar.add_action("Actualizar", self.refresh, icon="refresh-cw", kind="ghost")
        self.root.addWidget(self.toolbar)
        self.root.addWidget(self.generate_button, alignment=Qt.AlignLeft)
        self.help = QLabel("Clic: asignar mes · Arrastre: ventana · Shift/Ctrl en filas: selección múltiple · Doble clic: borrar")
        self.help.setWordWrap(True)
        self.help.setProperty("role", "caption")
        self.root.addWidget(self.help)
        self.matrix = MacroMatrix(self)
        self.matrix.range_assigned.connect(self.assign_range)
        self.matrix.cleared.connect(self.clear)
        self.matrix.selection_changed.connect(self._status)
        self.legend = QLabel()
        self.legend.setProperty("role", "caption")
        self.root.addWidget(self.legend)
        self.root.addWidget(self.matrix, 1)
        self.empty_state = EmptyState("calendar-range", "Sin actividades para programar",
            "Agrega una lista de tareas para asignar meses y revisar la capacidad.", actions=[
                ("Ir a actividades", self.open_activities, True), ("Importar plantilla", import_template, False)])
        self.empty_state.hide()
        self.root.addWidget(self.empty_state, 1)
        self.status = QLabel()
        self.status.setProperty("role", "caption")
        self.root.addWidget(self.status)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(lambda: self.filter_rows())
        self.search.textChanged.connect(self._search_changed)
        self.only_unassigned.toggled.connect(self.filter_rows)
        state.catalogs_changed.connect(self._mark_stale)
        self.matrix.data_pid = None
        operations_changed = getattr(state, "operations_changed", None)
        if operations_changed is not None:
            operations_changed.connect(self._status)
        self._status()
        self.on_theme()

        self.content_scroll = scroll_layout(self.root)
        self.root.addLayout(hbox(self.save_button, self.generate_button, None))

    def open_activities(self):
        from ..app import main_window
        if main_window():
            main_window().go("gantt")

    @property
    def pending(self):
        return self._pending.setdefault(self.data_pid, {})

    def _mark_stale(self):
        self._sync_project()
        self._stale = True
        if self.isVisible() and not self.saving and not self.generating:
            self.activate()

    def _sync_project(self):
        if self._pid == self.state.pid:
            return
        self._pid = self.state.pid
        self._ticket += 1
        self._totals_ticket += 1
        self.search_timer.stop()
        self.data_pid = self.matrix.data_pid = None
        self.loading = True
        self.rows, self.visible, self.months, self.totals = [], [], [], []
        self.positions, self.by_id = {}, {}
        self._search_source, self._search_texts = None, []
        self.cap_per_day = 0
        self.matrix.selected.clear()
        self.matrix.anchor, self.matrix.drag = None, None
        self.matrix.focus_cell = (0, 0)
        for widget in (self.search, self.only_unassigned, self.hard):
            previous = widget.blockSignals(True)
            if widget is self.search:
                widget.clear()
            else:
                widget.setChecked(False)
            widget.blockSignals(previous)
        self.matrix.update_ranges()
        self._status()

    def _can_mutate(self, *, allow_generating=False):
        if self.data_pid != self.state.pid or self.matrix.data_pid != self.state.pid:
            notify("Los datos pertenecen a otro proyecto. Usa Actualizar.", "info")
            return False
        return (bool(self.state.pid) and not self.saving and not self.loading
                and (allow_generating or not self.generating))

    def _search_changed(self, *_):
        if self.search.isModified():
            self.search_timer.start()
        else:
            self.filter_rows()

    def refresh(self):
        self._sync_project()
        pid, project = self.state.pid, dict(self.state.project or {})
        self._ticket += 1
        ticket = self._ticket
        if not pid:
            self.loading = False
            self._status()
            return
        self.loading = True
        self._status()

        def work():
            cols = self.state.client.columnar(pid)
            rows = decode_columns(cols)
            resources = self.state.client.resources(pid)
            return rows, build_months(project, rows), resources, "work_hours" in cols

        def done(data):
            if ticket != self._ticket or pid != self.state.pid:
                return
            self.loading = False
            self.data_pid = self.matrix.data_pid = pid
            self.rows, self.months, resources, self.has_hours = data
            self.by_id = {row["id"]: row for row in self.rows}
            self.matrix.selected.intersection_update(self.by_id)
            self.cap_per_day = sum(r["capacity_hours_day"] * r["max_units"] for r in resources
                                   if r.get("active") and r.get("kind") not in ("material", "equipo"))
            self.filter_rows()
            self.update_totals()

        def failed(exc):
            if ticket == self._ticket and pid == self.state.pid:
                self.loading = False
                self._stale = True
                self._status()
                show_error(exc, self)
        run_task(work, done, failed, busy="Cargando matriz macro…")

    def filter_rows(self, *_):
        self.search_timer.stop()
        if self._search_source is not self.rows:
            self._search_source = self.rows
            self._search_texts = [f"{row.get('code', '')} {row.get('name', '')} {row.get('location', '')}".casefold()
                                  for row in self.rows]
        q = self.search.text().strip().casefold()
        only = self.only_unassigned.isChecked()
        pending = self.pending
        self.visible = [i for i, row in enumerate(self.rows)
                        if (not q or q in self._search_texts[i])
                        and (not only or not any(window_of(pending.get(row["id"], row))))]
        self.positions = {self.rows[i]["id"]: p for p, i in enumerate(self.visible)}
        self.matrix.focus_cell = (0, 0)
        self.matrix.drag = None
        self.matrix.update_ranges()
        self._status()

    def assign_range(self, targets, start, end):
        if not self._can_mutate():
            return
        lo, hi = sorted((start, end))
        if not (0 <= lo <= hi < len(self.months)):
            return
        patch = dict(window_start=self.months[lo]["start"], window_end=self.months[hi]["end"],
                     window_hard=self.hard.isChecked(), macro_month=self.months[lo]["key"] if lo == hi else None)
        for aid in targets:
            if aid in self.by_id:
                self.pending[aid] = dict(id=aid, **patch)
        self.filter_rows()
        self.update_totals()

    def clear(self, targets):
        if not self._can_mutate():
            return
        for aid in targets:
            if aid in self.by_id:
                self.pending[aid] = dict(id=aid, window_start=None, window_end=None, macro_month=None, window_hard=False)
        self.filter_rows()
        self.update_totals()

    def update_totals(self):
        self._totals_ticket += 1
        ticket, pid = self._totals_ticket, self.data_pid
        rows, pending, months = self.rows, dict(self.pending), self.months

        def done(totals):
            if ticket == self._totals_ticket and pid == self.data_pid and pid == self.state.pid:
                self.totals = totals if self.has_hours else []
                self.matrix.viewport().update()
        run_task(lambda: monthly_hours(rows, pending, months), done)

    def _status(self):
        empty = bool(self.state.pid) and not self.loading and not self.rows
        self.empty_state.setVisible(empty)
        self.matrix.setVisible(not empty)
        self.status.setText(f"{fmt_num(len(self.visible))} actividades · {fmt_num(len(self.matrix.selected))} seleccionadas · "
                            f"{fmt_num(len(self.pending))} cambios pendientes" + (" · HH no disponibles" if not self.has_hours else ""))
        self.save_button.setText(f"Guardar ({fmt_num(len(self.pending))})")
        ready = (self.data_pid == self.state.pid and bool(self.state.pid)
                 and not self.saving and not self.loading and not self.generating)
        self.save_button.setEnabled(bool(self.pending) and ready)
        self.discard_button.setEnabled(bool(self.pending) and ready)
        self.clear_button.setEnabled(ready)
        self.matrix.setEnabled(ready)
        busy = getattr(self.state, "is_busy", None)
        self.generate_button.setEnabled(ready and not (callable(busy) and busy(f"solve:{self.state.pid}")))

    def has_unsaved(self) -> bool:
        """A3-06: borradores macro (de cualquier proyecto visitado) sin guardar."""
        return any(bool(drafts) for drafts in self._pending.values())

    def discard(self):
        if self._can_mutate():
            self.pending.clear()
            self.filter_rows()
            self.update_totals()

    def save(self, on_saved=None, *, on_error=None):
        if not self._can_mutate(allow_generating=callable(on_saved)):
            if callable(on_error):
                on_error()
            return
        pid = self.data_pid
        patches = list(self.pending.values())
        if not patches:
            if callable(on_saved):
                on_saved()
            return
        self.saving = True
        self.matrix.setEnabled(False)
        self._status()
        committed = 0

        def finish():
            self.saving = False
            self.matrix.setEnabled(True)
            self._status()
            if pid == self.state.pid and not callable(on_saved):
                self.state.bump_schedule()
            elif pid != self.state.pid and not self.generating and self.isVisible():
                self.activate()

        def batch_at(offset):
            nonlocal committed
            if pid != self.state.pid or self.data_pid != pid:
                finish()
                if callable(on_error):
                    on_error()
                notify(f"El proyecto cambió; se guardaron {fmt_num(committed)} ventanas. El resto sigue pendiente.", "info")
                return
            batch = patches[offset:offset + 5000]
            if not batch:
                finish()
                notify(f"{fmt_num(committed)} ventanas guardadas")
                if callable(on_saved):
                    on_saved()
                return

            def done(_):
                nonlocal committed
                committed += len(batch)
                pending = self._pending.setdefault(pid, {})
                for patch in batch:
                    if pending.get(patch["id"]) == patch:
                        pending.pop(patch["id"], None)
                    if self.data_pid == pid and patch["id"] in self.by_id:
                        self.by_id[patch["id"]].update(patch)
                self._status()
                batch_at(offset + len(batch))

            def failed(exc):
                finish()
                if callable(on_error):
                    on_error()
                notify(f"Se guardaron {fmt_num(committed)} ventanas; los cambios restantes siguen pendientes.", "error")
                show_error(exc, self)
            run_task(lambda: self.state.client.patch_activities(pid, batch), done, failed, busy="Guardando ventanas macro…")
        batch_at(0)

    def generate(self):
        if not self._can_mutate():
            return
        pid, project = self.state.pid, dict(self.state.project)
        key = f"solve:{pid}"
        begin = getattr(self.state, "try_begin", None)
        if callable(begin) and not begin(key):
            notify("Ya se está generando un programa para este proyecto.", "info")
            return
        self.generating = True
        self._status()

        def finish():
            self.saving = self.generating = False
            end_operation = getattr(self.state, "end", None)
            if callable(begin) and callable(end_operation):
                end_operation(key)
            self._status()
            if pid == self.state.pid:
                self.state.bump_schedule()
            elif self.isVisible():
                self.activate()

        def after_save():
            if pid != self.state.pid:
                finish()
                return
            self.saving = True
            self.matrix.setEnabled(False)
            self._status()
            weights = dict(level=.25, makespan=.05, window=.70, cost=0)
            options = dict(method="auto", time_limit_s=30, level_period="week", group_by="skill",
                           respect_resource_limits=True, allow_overtime=True, assign_workers=True, group_outages=True)

            def done(result):
                finish()
                if pid != self.state.pid:
                    return
                self.last_result = result
                if applicable(result) and result.get("applied") is not False:
                    notify(f"Programa aplicado · Fuera de ventana: {fmt_num((result.get('after') or {}).get('activities_out_of_window'))}")
                else:
                    notify("No se aplicó un programa factible. Revisa las corridas en Programar / optimizar.", "error")

            def failed(exc):
                finish()
                show_error(exc, self)
            run_task(lambda: solve_program(self.state.client, pid, weights, options, project), done, failed,
                     busy="Generando programa dentro de los meses…")
        self.save(after_save, on_error=finish)

    def on_theme(self):
        t = tokens()
        self.legend.setText(f'<span style="color:{t.bar_crit}">━</span> Crítica · '
                            f'<span style="color:{t.bar}">━</span> Normal · '
                            f'<span style="color:{qcolor(t.bar_window_hard).name()}">■</span> Ventana dura · '
                            'Capacidad: hasta 85 % normal, 85–100 % atención, más de 100 % sobrecarga')
        self.matrix.viewport().update()
