"""Virtualized QPainter Gantt with one visible vertical scrollbar."""
from __future__ import annotations

from datetime import date, timedelta
import math
from html import escape

import numpy as np
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QTimer, QEvent, QModelIndex
from PySide6.QtGui import QPainter, QPen, QPolygonF, QPainterPath, QFont, QPixmap
from PySide6.QtWidgets import (QWidget, QSplitter, QTableView, QScrollBar, QVBoxLayout,
                               QGridLayout, QAbstractItemView, QHeaderView,
                               QFrame, QToolTip)

from .. import accesibilidad
from ..theme import tokens, qcolor, theme_signals, ui_font
from ..widgets.common import fmt_date, fmt_num, setup_table
from .model import ActivityArrays, RowModel, RelationIndex, day, day_iso
from .table import ActivityTableModel, DateDelegate, CachedDelegate

ROW_HEIGHT = 26
HEADER_HEIGHT = 44
SCALES = {"Día": 26.0, "Semana": 7.0, "Mes": 2.4, "Trimestre": 1.0, "Año": 0.35}
MONTHS = ("", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def tiers(first, last, width):
    """Calendar-aligned ticks; only generate intervals intersecting the viewport."""
    start = date.fromisoformat(day_iso(first))
    stop = date.fromisoformat(day_iso(last))

    def months(step):
        d = start.replace(day=1, month=((start.month - 1) // step) * step + 1)
        result = []
        while d <= stop:
            m = d.month - 1 + step
            following = date(d.year + m // 12, m % 12 + 1, 1)
            label = (str(d.year) if step == 12 else f"T{(d.month - 1) // 3 + 1}"
                     if step == 3 else MONTHS[d.month])
            result.append((day(d), day(following), label))
            d = following
        return result

    if width >= 16:
        upper = [(s, f, f"{MONTHS[date.fromisoformat(day_iso(s)).month]} {date.fromisoformat(day_iso(s)).year}")
                 for s, f, _ in months(1)]
        lower = [(d, d + 1, str(date.fromisoformat(day_iso(d)).day))
                 for d in range(int(first), int(last) + 1)]
    elif width >= 4:
        upper = [(s, f, f"{MONTHS[date.fromisoformat(day_iso(s)).month]} {date.fromisoformat(day_iso(s)).year}")
                 for s, f, _ in months(1)]
        d = start - timedelta(days=start.weekday())
        lower = []
        while d <= stop:
            lower.append((day(d), day(d) + 7, f"S{d.isocalendar().week}"))
            d += timedelta(days=7)
    else:
        upper = months(12)
        lower = months(1 if width >= 1.6 else 3)
    return upper, lower


class GanttCanvas(QWidget):
    def __init__(self, gantt):
        super().__init__()
        self.gantt = gantt
        self.setMinimumWidth(120)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Cronograma de actividades")
        self.drag = None
        self.hover = -1
        self.last_painted_rows = 0
        self._frame = None

    def row_y(self, row):
        return HEADER_HEIGHT + row * ROW_HEIGHT - self.gantt.vertical.value()

    def bar(self, i, preview=True):
        g, a = self.gantt, self.gantt.arrays
        start, finish = a.start[i], a.finish[i]
        if not np.isfinite(start):
            return None
        if not np.isfinite(finish):
            finish = start
        if preview and self.drag and self.drag["i"] == i:
            delta = round((self.drag["point"].x() - self.drag["x"]) / g.day_width)
            if self.drag["mode"] == "move":
                start, finish = start + delta, finish + delta
            elif self.drag["mode"] == "resize":
                finish = max(start, finish + delta)
        return g.day_to_x(start), g.day_to_x(start if a.milestone[i] else finish + 1)

    def _frame_key(self):
        """Firma solo las filas y días visibles; detecta también edición directa."""
        g, a, rows = self.gantt, self.gantt.arrays, self.gantt.rows
        first = max(0, g.vertical.value() // ROW_HEIGHT)
        end = min(len(rows.rows), math.ceil((g.vertical.value() + self.height() - HEADER_HEIGHT) / ROW_HEIGHT))
        visible = rows.rows[first:max(first, end)]
        activities = visible[visible >= 0]
        values = tuple(getattr(a, name)[activities].tobytes() for name in
                       ('ids', 'start', 'finish', 'bs', 'bf', 'ws', 'we', 'milestone',
                        'pct', 'outage', 'window_hard', 'crit'))
        groups = tuple((rows.groups[-int(i)-1]['start'], rows.groups[-int(i)-1]['finish'])
                       for i in visible if i < 0)
        first_day, last_day = math.floor(g.day_at(0)), math.ceil(g.day_at(self.width()))
        calendar = tuple(g.calendar_days.get(d) for d in range(first_day, last_day + 1))
        selection = tuple((r.top(), r.bottom(), r.left(), r.right())
                          for r in g.table.selectionModel().selection())
        drag = None if not self.drag else (self.drag['i'], self.drag['mode'], self.drag['x'],
                                          self.drag['point'].x(), self.drag['point'].y())
        return (self.width(), self.height(), self.devicePixelRatioF(), tokens(), self.font().key(),
                g.vertical.value(), g.horizontal.value(), g.day_width, g.origin_day,
                g.selected_id, g.hover_row, self.hover, selection, drag, g.calendar_id, calendar,
                g.show_windows, g.show_baseline, g.show_relations, g.relations,
                float(g.data_day).hex(), date.today(), visible.tobytes(), values,
                tuple(a.names[activities]), groups)

    def paintEvent(self, event):
        key = self._frame_key()
        if self._frame is None or self._frame[0] != key:
            dpr = self.devicePixelRatioF()
            pixmap = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
            pixmap.setDevicePixelRatio(dpr)
            painter = QPainter(pixmap)
            try:
                self.paint(painter)
            finally:
                painter.end()
            # Una sola imagen del viewport, nunca una imagen por actividad.
            self._frame = (key, pixmap)
        painter = QPainter(self)
        try:
            painter.drawPixmap(0, 0, self._frame[1])
        finally:
            painter.end()

    def paint(self, p):
        g, t = self.gantt, tokens()
        a, rows = g.arrays, g.rows
        width, height = self.width(), self.height()
        p.fillRect(self.rect(), qcolor(t.surface))
        p.setRenderHint(QPainter.Antialiasing)
        label_font = ui_font()
        label_font.setPointSizeF(8.5)
        p.setFont(label_font)
        first = min(len(rows.rows), max(0, g.vertical.value() // ROW_HEIGHT))
        end = max(first, min(len(rows.rows), math.ceil((g.vertical.value() + height - HEADER_HEIGHT) / ROW_HEIGHT)))
        # Same row parity and hover state as the table; only visit visible rows.
        for row in range(first, end):
            color = t.surface2 if rows.rows[row] < 0 or row % 2 else t.surface
            p.fillRect(QRectF(0, self.row_y(row), width, ROW_HEIGHT), qcolor(color))
        first_day, last_day = math.floor(g.day_at(0)), math.ceil(g.day_at(width))
        # Non-working days remain actual days (not a guessed 5/7 pattern when a calendar exists).
        for d in range(first_day, last_day + 1):
            kind = g.calendar_days.get(d, 0 if g.calendar_id else (1 if (d + 3) % 7 >= 5 else 0))
            if kind:
                p.fillRect(QRectF(g.day_to_x(d), HEADER_HEIGHT, max(.5, g.day_width), height - HEADER_HEIGHT),
                           qcolor(t.holiday if kind == 2 else t.weekend))
        upper, lower = tiers(first_day, last_day, g.day_width)
        p.fillRect(QRectF(0, 0, width, HEADER_HEIGHT), qcolor(t.surface2))
        for tier, ticks in enumerate((upper, lower)):
            header_font = QFont(label_font)
            header_font.setWeight(QFont.DemiBold if tier == 0 else QFont.Normal)
            p.setFont(header_font)
            for start, finish, label in ticks:
                x1, x2 = g.day_to_x(start), g.day_to_x(finish)
                p.setPen(qcolor(t.grid))
                p.drawLine(QPointF(x1, tier * 22), QPointF(x1, height if tier else 22))
                left, right = max(0, x1), min(width, x2)
                if right - left > p.fontMetrics().horizontalAdvance(label) + 6:
                    p.setPen(qcolor(t.text2 if tier == 0 else t.text3))
                    p.drawText(QRectF(left, tier * 22, right - left, 22), Qt.AlignCenter, label)
        p.setPen(qcolor(t.border))
        p.drawLine(0, 22, width, 22)
        p.drawLine(0, HEADER_HEIGHT - 1, width, HEADER_HEIGHT - 1)
        p.save()
        p.setFont(label_font)
        p.setClipRect(QRectF(0, HEADER_HEIGHT, width, max(0, height - HEADER_HEIGHT)))
        self.last_painted_rows = max(0, end - first)
        for row in range(first, end):
            y = self.row_y(row)
            p.setPen(qcolor(t.grid))
            p.drawLine(QPointF(0, y + ROW_HEIGHT), QPointF(width, y + ROW_HEIGHT))
            i = int(rows.rows[row])
            selected = g.table.selectionModel().isRowSelected(row, QModelIndex())
            if selected or row == g.hover_row:
                color = t.accent_soft if selected else t.surface3
                p.fillRect(QRectF(0, y, width, ROW_HEIGHT), qcolor(color))
            if i < 0:
                group = rows.groups[-i - 1]
                if np.isfinite(group["start"]) and np.isfinite(group["finish"]):
                    left, right = max(-10, g.day_to_x(group["start"])), min(width + 10, g.day_to_x(group["finish"] + 1))
                    if right >= left:
                        p.setPen(Qt.NoPen)
                        p.setBrush(qcolor(t.text))
                        p.drawRect(QRectF(left, y + 10, max(2, right - left), 4))
                        peak = min(5, max(1, (right - left) / 2))
                        for edge, inward in ((left, peak), (right, -peak)):
                            p.drawPolygon(QPolygonF([QPointF(edge, y + 10),
                                                     QPointF(edge + inward, y + 14), QPointF(edge, y + 20)]))
                continue
            if g.selected_id == int(a.ids[i]):
                p.fillRect(QRectF(0, y, width, ROW_HEIGHT), qcolor(t.accent_soft))
            if g.show_windows and np.isfinite(a.ws[i]) and np.isfinite(a.we[i]):
                left, right = max(-10, g.day_to_x(a.ws[i])), min(width + 10, g.day_to_x(a.we[i] + 1))
                if right > left:
                    p.fillRect(QRectF(left, y + 2, right - left, ROW_HEIGHT - 4),
                               qcolor(t.bar_window_hard if a.window_hard[i] else t.bar_window))
            if g.show_baseline and np.isfinite(a.bs[i]):
                left = max(-10, g.day_to_x(a.bs[i]))
                right = min(width + 10, g.day_to_x((a.bf[i] if np.isfinite(a.bf[i]) else a.bs[i]) + 1))
                if right > left:
                    p.fillRect(QRectF(left, y + 21, max(2, right - left), 3), qcolor(t.bar_base))
            bar = self.bar(i)
            if not bar:
                continue
            xa, xb = bar
            if xb < -10 or xa > width + 10:
                continue
            p.setPen(Qt.NoPen)
            bar_color = qcolor(t.bar_crit if a.crit[i] else t.milestone if a.milestone[i] else t.bar)
            p.setBrush(bar_color)
            if a.milestone[i]:
                p.drawPolygon(QPolygonF([QPointF(xa, y + 8), QPointF(xa + 5, y + 13),
                                        QPointF(xa, y + 18), QPointF(xa - 5, y + 13)]))
            else:
                left, right = max(-10, xa), min(width + 10, max(xa + 2, xb))
                p.drawRoundedRect(QRectF(left, y + 7, right - left, 12), 3, 3)
                progress_left = max(left, xa + 1)
                progress_right = min(right - 1, xa + (xb - xa - 2) * a.pct[i] / 100 + 1)
                if progress_right > progress_left:
                    p.setBrush(bar_color.darker(135))
                    p.drawRoundedRect(QRectF(progress_left, y + 8, progress_right - progress_left, 10), 2, 2)
            if a.outage[i]:
                p.fillRect(QRectF(max(0, xa), y + 3, 6, 3), qcolor(t.datadate))
            if g.day_width >= 1.2 and 0 <= xb + 12 < width:
                p.setPen(qcolor(t.text2))
                p.drawText(QRectF(xb + 12, y, width - xb - 12, ROW_HEIGHT), Qt.AlignVCenter,
                           p.fontMetrics().elidedText(str(a.names[i]), Qt.ElideRight, int(width - xb - 12)))
            if self.hover == i or g.selected_id == int(a.ids[i]):
                p.setPen(QPen(qcolor(t.accent), 1.5))
                p.setBrush(qcolor(t.surface))
                p.drawEllipse(QPointF(xb + 7, y + 13), 3.5, 3.5)
        if g.show_relations:
            p.setPen(QPen(qcolor(t.text3), 1))
            p.setBrush(qcolor(t.text3))
            for rp, rs, pi, si, kind, lag in g.relations.visible(first, end):
                pred, succ = self.bar(pi), self.bar(si)
                if pred is None or succ is None:
                    continue
                from_end, to_end = kind in ("FS", "FF"), kind in ("FF", "SF")
                x1, x2 = pred[int(from_end)], succ[int(to_end)]
                if max(x1, x2) < 0 or min(x1, x2) > width:
                    continue
                x1, x2 = max(-20, min(width + 20, x1)), max(-20, min(width + 20, x2))
                y1, y2 = self.row_y(rp) + 13, self.row_y(rs) + 13
                ex, ax = x1 + (7 if from_end else -7), x2 + (7 if to_end else -7)
                path = QPainterPath(QPointF(x1, y1))
                path.lineTo(ex, y1)
                ym = y2 - 12 if y2 > y1 else y2 + 12
                path.lineTo(ex, ym)
                path.lineTo(ax, ym)
                path.lineTo(ax, y2)
                path.lineTo(x2, y2)
                # Sin relleno: un QPainterPath abierto con pincel se rellena y dibuja bandas grises.
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                p.setBrush(qcolor(t.text3))
                tail = x2 + (5 if to_end else -5)
                p.drawPolygon(QPolygonF([QPointF(x2, y2), QPointF(tail, y2 - 3), QPointF(tail, y2 + 3)]))
        if self.drag and self.drag["mode"] == "link":
            i = self.drag["i"]
            bar = self.bar(i)
            p.setPen(QPen(qcolor(t.accent), 2, Qt.DashLine))
            p.drawLine(QPointF(bar[1] + 7, self.row_y(int(rows.row_of_act[i])) + 13), self.drag["point"])
        for d, color, label in ((day(date.today()), t.today, "Hoy"), (g.data_day, t.datadate, "Corte")):
            if np.isfinite(d) and 0 <= g.day_to_x(d) <= width:
                x = g.day_to_x(d)
                p.setPen(QPen(qcolor(color), 1.5, Qt.DashLine if label == "Hoy" else Qt.SolidLine))
                p.drawLine(QPointF(x, HEADER_HEIGHT), QPointF(x, height))
        p.restore()
        if np.isfinite(g.data_day) and 0 <= g.day_to_x(g.data_day) <= width:
            label = f"Corte {date.fromisoformat(day_iso(g.data_day)):%d-%m}"
            p.setFont(label_font)
            pill_width = p.fontMetrics().horizontalAdvance(label) + 12
            pill = QRectF(max(0, min(g.day_to_x(g.data_day) + 4, width - pill_width)), 2, pill_width, 19)
            p.setPen(QPen(qcolor(t.datadate), 1))
            p.setBrush(qcolor(t.surface))
            p.drawRoundedRect(pill, 6, 6)
            p.setPen(qcolor(t.text2))
            p.drawText(pill, Qt.AlignCenter, label)
        if not len(rows.rows):
            p.setPen(qcolor(t.text2))
            p.drawText(QRectF(12, HEADER_HEIGHT + 20, width - 24, 80), Qt.AlignCenter | Qt.TextWordWrap,
                       "No hay actividades para mostrar. Agrega una actividad o ajusta los filtros.")

    def hit(self, point):
        row = int((point.y() - HEADER_HEIGHT + self.gantt.vertical.value()) // ROW_HEIGHT)
        if point.y() < HEADER_HEIGHT or not 0 <= row < len(self.gantt.rows.rows):
            return -1, -1, "none"
        i = int(self.gantt.rows.rows[row])
        if i < 0:
            return row, i, "wbs"
        bar = self.bar(i, preview=False)
        if bar is None or abs(point.y() - (self.row_y(row) + 13)) > 10:
            return row, i, "none"
        xa, xb = bar
        x = point.x()
        if xb + 2 <= x <= xb + 12:
            return row, i, "link"
        if self.gantt.arrays.milestone[i]:
            return row, i, "move" if abs(x - xa) <= 7 else "none"
        if xb - 5 <= x <= xb + 1:
            return row, i, "resize"
        return row, i, "move" if xa <= x <= xb else "none"

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.setFocus()
        row, i, zone = self.hit(event.position())
        if zone == "wbs":
            self.gantt.toggle_wbs.emit(self.gantt.rows.groups[-i - 1]["node"]["id"])
        elif i >= 0:
            self.gantt.select_id(int(self.gantt.arrays.ids[i]))
            if zone != "none":
                self.drag = dict(i=i, mode=zone, x=event.position().x(), point=event.position())
                self.grabMouse()
        QToolTip.hideText()

    def mouseMoveEvent(self, event):
        if self.drag:
            self.drag["point"] = event.position()
            self.update()
            return
        row, i, zone = self.hit(event.position())
        self.hover = i if i >= 0 else -1
        self.gantt.set_hover_row(row)
        self.setCursor({"move": Qt.OpenHandCursor, "resize": Qt.SizeHorCursor, "link": Qt.CrossCursor}.get(zone, Qt.ArrowCursor))
        if i >= 0 and zone != "none":
            a = self.gantt.arrays
            tip = (f"<b>{escape(str(a.codes[i]))} · {escape(str(a.names[i]))}</b><br>"
                   f"{fmt_date(day_iso(a.start[i]))} → {fmt_date(day_iso(a.finish[i]))}<br>"
                   f"Duración: {fmt_num(a.dur[i])} días laborables · Avance: {fmt_num(a.pct[i], 1)} %<br>"
                   f"HH: {fmt_num(a.hh[i], 1)} · Holgura: {fmt_num(a.tf[i])}<br>"
                   f"Mes macro: {escape(str(a.macro[i] or '—'))}<br>"
                   f"Ventana: {fmt_date(day_iso(a.ws[i]))} → {fmt_date(day_iso(a.we[i]))}<br>"
                   f"Línea base: {fmt_date(day_iso(a.bs[i]))} → {fmt_date(day_iso(a.bf[i]))}<br>"
                   f"{escape(str(a.skill[i] or ''))} · {escape(str(a.location[i] or ''))}"
                   + ("<br>Requiere desconexión" if a.outage[i] else ""))
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()
        self.update()

    def mouseReleaseEvent(self, event):
        if not self.drag:
            return
        drag, self.drag = self.drag, None
        self.releaseMouse()
        i, a = drag["i"], self.gantt.arrays
        delta = round((event.position().x() - drag["x"]) / self.gantt.day_width)
        aid = int(a.ids[i])
        if drag["mode"] == "link":
            _, target, zone = self.hit(event.position())
            if target >= 0 and target != i and zone not in ("none", "wbs"):
                self.gantt.link_requested.emit(aid, int(a.ids[target]))
        elif delta and drag["mode"] == "move":
            self.gantt.move_requested.emit(aid, day_iso(a.start[i] + delta))
        elif delta and drag["mode"] == "resize":
            finish = a.finish[i] if np.isfinite(a.finish[i]) else a.start[i]
            self.gantt.resize_requested.emit(aid, day_iso(max(a.start[i], finish + delta)))
        self.update()

    def cancel_drag(self):
        if self.drag:
            self.drag = None
            self.releaseMouse()
            self.update()

    def hideEvent(self, event):
        self.cancel_drag()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancel_drag()
        elif event.key() in (Qt.Key_Up, Qt.Key_Down):
            g = self.gantt
            i = g.arrays.id_to_index.get(g.selected_id)
            row = int(g.rows.row_of_act[i]) if i is not None else -1
            step = 1 if event.key() == Qt.Key_Down else -1
            row += step
            while 0 <= row < len(g.rows.rows) and g.rows.rows[row] < 0:
                row += step
            if 0 <= row < len(g.rows.rows):
                g.select_id(int(g.arrays.ids[g.rows.rows[row]]))
        else:
            super().keyPressEvent(event)

    def leaveEvent(self, event):
        self.hover = -1
        self.gantt.set_hover_row(-1)
        QToolTip.hideText()
        self.update()

    def wheelEvent(self, event):
        g = self.gantt
        if event.modifiers() & Qt.ControlModifier:
            g.set_day_width(g.day_width * (1.2 if event.angleDelta().y() > 0 else 1 / 1.2), event.position().x())
        elif event.modifiers() & Qt.ShiftModifier or abs(event.angleDelta().x()) > abs(event.angleDelta().y()):
            delta = event.angleDelta().x() or event.angleDelta().y()
            g.horizontal.setValue(g.horizontal.value() - delta)
        else:
            delta = event.pixelDelta().y() or event.angleDelta().y() / 120 * ROW_HEIGHT * 3
            g.vertical.setValue(g.vertical.value() - int(delta))
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.gantt.update_ranges)


class GanttWidget(QWidget):
    edit_requested = Signal(int, str, object)
    move_requested = Signal(int, str)
    resize_requested = Signal(int, str)
    link_requested = Signal(int, int)
    toggle_wbs = Signal(int)
    selection_changed = Signal(object)
    viewport_changed = Signal()
    invalid_edit = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.arrays = ActivityArrays(dict(ids=[], codes=[], names=[], starts=[], finishes=[]))
        self.rows = RowModel(self.arrays)
        self.relations = RelationIndex([], self.arrays, self.rows)
        self.selected_id = None
        self.hover_row = -1
        self.day_width = 7.0
        self.origin_day = self.arrays.min_day - 45
        self.end_day = self.arrays.max_day + 45
        self.data_day = np.nan
        self.show_baseline = self.show_windows = self.show_relations = True
        self.calendar_days = {}
        self.calendar_id = None
        self.model = ActivityTableModel(self)
        self.table = QTableView()
        accesibilidad.marcar(self.table).setAccessibleName("Tabla de actividades")
        self.table.setModel(self.model)
        setup_table(self.table, sortable=False, stretch_last=False)
        self.table.viewport().installEventFilter(self)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setMinimumSectionSize(ROW_HEIGHT)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.horizontalHeader().setFixedHeight(HEADER_HEIGHT)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setItemDelegate(CachedDelegate(self.table))
        self.table.setItemDelegateForColumn(3, DateDelegate(self.table))
        self.vertical = QScrollBar(Qt.Vertical)
        self.horizontal = QScrollBar(Qt.Horizontal)
        self.canvas = GanttCanvas(self)
        right = QWidget()
        grid = QGridLayout(right)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.canvas, 0, 0, 2, 1)
        grid.setRowMinimumHeight(0, HEADER_HEIGHT)
        grid.setRowStretch(1, 1)
        grid.addWidget(self.vertical, 1, 1)
        grid.addWidget(self.horizontal, 2, 0)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(right)
        self.splitter.setSizes([680, 600])   # código, nombre, duración, inicio y término visibles
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)
        self.model.edited.connect(self.edit_requested.emit)
        self.model.invalid.connect(self.invalid_edit.emit)
        self.table.selectionModel().currentRowChanged.connect(self._selection)
        self.table.clicked.connect(self._click)
        scrollbar = self.table.verticalScrollBar()
        scrollbar.rangeChanged.connect(self._vertical_range)
        scrollbar.valueChanged.connect(self.vertical.setValue)
        self.vertical.valueChanged.connect(scrollbar.setValue)
        self.vertical.valueChanged.connect(lambda *_: self.canvas.update())
        self.horizontal.valueChanged.connect(lambda *_: self._viewport())
        self.splitter.splitterMoved.connect(lambda *_: self.update_ranges())
        theme_signals.theme_changed.connect(self.on_theme)

    def eventFilter(self, watched, event):
        if watched is self.table.viewport():
            if event.type() == QEvent.MouseMove:
                self.set_hover_row(self.table.indexAt(event.position().toPoint()).row())
            elif event.type() == QEvent.Leave:
                self.set_hover_row(-1)
        return super().eventFilter(watched, event)

    def set_hover_row(self, row):
        if self.hover_row != row:
            self.hover_row = row
            i = int(self.rows.rows[row]) if 0 <= row < len(self.rows.rows) else -1
            self.canvas.hover = i if i >= 0 else -1
            self.table.viewport().update()
            self.canvas.update()

    def _vertical_range(self, low, high):
        self.vertical.setRange(low, high)
        self.vertical.setPageStep(self.table.verticalScrollBar().pageStep())
        self.vertical.setSingleStep(ROW_HEIGHT)

    def _viewport(self):
        self.canvas.update()
        self.viewport_changed.emit()

    def update_ranges(self):
        self.horizontal.setRange(0, max(0, math.ceil((self.end_day - self.origin_day) * self.day_width - self.canvas.width())))
        self.horizontal.setPageStep(self.canvas.width())
        self.horizontal.setSingleStep(max(1, int(self.day_width)))
        self._viewport()

    def set_data(self, arrays, rows, relations, project, reset=False):
        self.canvas.cancel_drag()
        self.set_hover_row(-1)
        selected, left, top = self.selected_id, self.day_at(0), self.vertical.value()
        self.arrays, self.rows, self.relations = arrays, rows, relations
        self.calendar_id = project.get("calendar_id")
        self.data_day = day(project.get("data_date"))
        self.origin_day = min(arrays.min_day - 45, day(date.today()) - 45)
        self.end_day = max(arrays.max_day + 45, day(date.today()) + 45)
        self.model.set_data(arrays, rows, project.get("mode", "proyecto"))
        # Anchos según la fuente real (Segoe UI en Windows es más angosta que DejaVu en Linux):
        # las fechas dd-mm-aaaa nunca deben quedar cortadas.
        fm = self.table.fontMetrics()
        date_w = fm.horizontalAdvance("00-00-0000") + 26
        widths = (max(92, fm.horizontalAdvance("SE.ING.IB00") + 34), 230, max(56, fm.horizontalAdvance("Dur.") + 26),
                  date_w, date_w, 70, 85, 95, 110, 130)
        for col, width in enumerate(widths):
            if col < self.model.columnCount() and (reset or self.table.columnWidth(col) in (100, 0)):
                self.table.setColumnWidth(col, width)
        self.update_ranges()
        if reset:
            self.vertical.setValue(0)
            self.jump_to_day(arrays.min_day)
        else:
            self.horizontal.setValue(round((left - self.origin_day) * self.day_width))
        self.select_id(selected, ensure_visible=False)
        if not reset:
            self.vertical.setValue(top)

    def _click(self, index):
        value = int(self.rows.rows[index.row()])
        if value < 0:
            self.toggle_wbs.emit(self.rows.groups[-value - 1]["node"]["id"])

    def apply_activity_dates(self, activity, project, relations):
        """Refleja fechas confirmadas por la API sin descargar todas las actividades.

        El llamador conserva la recarga canónica si hubo CPM o falló la escritura.
        Los agregados WBS y el orden por inicio se reconstruyen con los arrays locales.
        """
        a = self.arrays
        i = a.id_to_index.get(activity.get("id"))
        if i is None:
            return False
        a.start[i] = day(activity.get("planned_start") or activity.get("early_start") or activity.get("actual_start"))
        a.finish[i] = day(activity.get("planned_finish") or activity.get("early_finish") or activity.get("actual_finish"))
        a.dur[i] = activity["duration_days"]
        a.milestone[i] = a.dur[i] == 0
        finite = [v for v in (a.start[i], a.finish[i]) if np.isfinite(v)]
        if finite:
            # Conservar el rango anterior evita saltos al acortar una barra.
            a.min_day = min(a.min_day, min(finite))
            a.max_day = max(a.max_day, max(finite))
        options = self.rows.options
        if options["sort"] == "inicio":
            rows = RowModel(a, **options)
            self.set_data(a, rows, RelationIndex(relations, a, rows), project)
        else:
            row = int(self.rows.row_of_act[i])
            changed = self.rows.update_dates(i)
            if row >= 0:
                changed.append(row)
            for changed_row in changed:
                self.model.dataChanged.emit(self.model.index(changed_row, 0), self.model.index(changed_row, self.model.columnCount()-1))
            left = self.day_at(0)
            self.origin_day = min(self.origin_day, a.min_day - 45)
            self.end_day = max(self.end_day, a.max_day + 45)
            self.update_ranges()
            self.horizontal.setValue(round((left - self.origin_day) * self.day_width))
            self.canvas.update()
        self.canvas.setAccessibleDescription(accesibilidad.resumen_fila(self.table))
        return True

    def _selection(self, index, previous):
        i = int(self.rows.rows[index.row()]) if index.isValid() else -1
        self.selected_id = int(self.arrays.ids[i]) if i >= 0 else None
        self.canvas.setAccessibleDescription(accesibilidad.resumen_fila(self.table))
        self.selection_changed.emit(self.selected_id)
        self.canvas.update()

    def select_id(self, aid, ensure_visible=True):
        i = self.arrays.id_to_index.get(aid)
        row = int(self.rows.row_of_act[i]) if i is not None else -1
        if row < 0:
            self.selected_id = None
            self.table.clearSelection()
            from PySide6.QtCore import QModelIndex
            self.table.setCurrentIndex(QModelIndex())
        else:
            self.selected_id = aid
            index = self.model.index(row, 0)
            self.table.setCurrentIndex(index)
            if ensure_visible:
                self.table.scrollTo(index, QAbstractItemView.EnsureVisible)
        self.selection_changed.emit(self.selected_id)
        self.canvas.update()

    def day_to_x(self, epoch_day):
        return (epoch_day - self.origin_day) * self.day_width - self.horizontal.value()

    def day_at(self, x):
        return self.origin_day + (self.horizontal.value() + x) / self.day_width

    def set_day_width(self, width, anchor=None):
        anchor = self.canvas.width() / 2 if anchor is None else anchor
        at = self.day_at(anchor)
        self.canvas.cancel_drag()
        self.day_width = max(.15, min(60, width))
        self.update_ranges()
        self.horizontal.setValue(round((at - self.origin_day) * self.day_width - anchor))
        self._viewport()

    def jump_to_day(self, value):
        if np.isfinite(value):
            self.horizontal.setValue(round((value - self.origin_day) * self.day_width - self.canvas.width() / 3))

    def jump_to_selection(self):
        i = self.arrays.id_to_index.get(self.selected_id)
        if i is not None:
            self.jump_to_day(self.arrays.start[i])

    def on_theme(self):
        self.table.viewport().update()
        self.canvas.update()
