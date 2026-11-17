"""Resource load profile, sharing the exact Gantt time transform."""
from __future__ import annotations

from html import escape
import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, QAbstractListModel, QModelIndex, QSortFilterProxyModel
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QComboBox,
                               QListView, QLineEdit, QToolTip)

from ..theme import tokens, qcolor, theme_signals
from ..widgets.common import run_task, fmt_num, fmt_date, button
from .model import day_iso


from .profile_model import ProfileModel


class SeriesListModel(QAbstractListModel):
    """Checkable virtual rows; only the selected keys are stored separately."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.selected_keys = set()

    def replace(self, rows, keys):
        self.beginResetModel()
        self.rows, self.selected_keys = rows, set(keys)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.DisplayRole:
            return f"{row['key'] or 'Sin nombre'} · {fmt_num(row['total'], 1)} HH"
        if role == Qt.UserRole:
            return row['key']
        if role == Qt.CheckStateRole:
            return Qt.Checked if row['key'] in self.selected_keys else Qt.Unchecked
        if role == Qt.ForegroundRole:
            colors = tokens().series
            return qcolor(colors[row['color'] % len(colors)])

    def flags(self, index):
        return super().flags(index) | Qt.ItemIsUserCheckable if index.isValid() else Qt.NoItemFlags

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.CheckStateRole or not index.isValid() or not 0 <= index.row() < len(self.rows):
            return False
        key = self.rows[index.row()]['key']
        checked = value == Qt.Checked or value == Qt.Checked.value
        if checked == (key in self.selected_keys):
            return True
        if checked:
            self.selected_keys.add(key)
        else:
            self.selected_keys.discard(key)
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        return True


class _SeriesRow:
    """Lazy compatibility handle for existing callers; never a Qt item per row."""
    def __init__(self, view, row):
        self.view, self.row = view, row

    def data(self, role):
        return self.view.model().index(self.row, 0).data(role)

    def setCheckState(self, value):
        self.view.model().setData(self.view.model().index(self.row, 0), value, Qt.CheckStateRole)


class SeriesListView(QListView):
    def count(self):
        return self.model().rowCount()

    def item(self, row):
        return _SeriesRow(self, row) if 0 <= row < self.count() else None


class ProfileCanvas(QWidget):
    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self.setMouseTracking(True)
        self.setMinimumHeight(130)
        self.setAccessibleName("Carga apilada, capacidad discontinua y exceso en rojo")

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            self.paint(p)
        finally:
            p.end()

    def paint(self, p):
        panel, t = self.profile, tokens()
        g, model = panel.gantt, panel.model
        p.fillRect(self.rect(), qcolor(t.surface))
        if model is None or not panel.selected:
            p.setPen(qcolor(t.text2))
            p.drawText(self.rect(), Qt.AlignCenter, "Selecciona una serie." if model else "Perfil de recursos")
            return
        top, bottom = 23, self.height() - 18
        maximum = panel.maximum
        y = lambda value: bottom - value / (maximum * 1.1) * (bottom - top)
        first, end = model.visible(g.day_at(0), g.day_at(self.width()))
        p.setPen(qcolor(t.grid))
        for tick in range(5):
            p.drawLine(QPointF(0, y(maximum * tick / 4)), QPointF(self.width(), y(maximum * tick / 4)))
        for i in range(first, end):
            x1, x2 = g.day_to_x(model.days[i]), g.day_to_x(model.days[i] + model.span)
            width = max(.5, x2 - x1 - 1)
            total = 0
            for series in panel.selected:
                value = series["load"][i]
                p.fillRect(QRectF(x1, y(total + value), width, y(total) - y(total + value)),
                           qcolor(t.series[series["color"] % len(t.series)]))
                total += value
            if total > panel.capacity[i]:
                p.fillRect(QRectF(x1, y(total), width, y(panel.capacity[i]) - y(total)), qcolor(t.critical))
        p.setPen(QPen(qcolor(t.text2), 1.5, Qt.DashLine))
        for i in range(first, end):
            x1, x2, cy = g.day_to_x(model.days[i]), g.day_to_x(model.days[i] + model.span), y(panel.capacity[i])
            p.drawLine(QPointF(x1, cy), QPointF(x2, cy))
            if i > first and model.days[i - 1] + model.span == model.days[i]:
                p.drawLine(QPointF(x1, y(panel.capacity[i - 1])), QPointF(x1, cy))
        p.fillRect(QRectF(0, 0, min(self.width(), 260), 20), qcolor(t.surface))
        p.setPen(qcolor(t.text2))
        p.drawText(QPointF(5, 15), f"Máx. {fmt_num(maximum, 1)} HH · {'día' if model.span == 1 else 'semana'}")
        p.drawText(QPointF(5, self.height() - 3), "0 HH")

    def mouseMoveEvent(self, event):
        panel = self.profile
        if panel.model is None or not panel.selected:
            return
        model = panel.model
        d = panel.gantt.day_at(event.position().x())
        i = int(np.searchsorted(model.days, d, side="right")) - 1
        if i < 0 or i >= len(model.days) or d >= model.days[i] + model.span:
            QToolTip.hideText()
            return
        lines = [f"<b>{fmt_date(day_iso(model.days[i]))} → {fmt_date(day_iso(model.days[i] + model.span - 1))}</b>"]
        lines.extend(f"{escape(s['key'])}: {fmt_num(s['load'][i], 1)} HH" for s in panel.selected)
        lines.extend([f"Carga: {fmt_num(panel.total[i], 1)} HH", f"Capacidad: {fmt_num(panel.capacity[i], 1)} HH",
                      f"Exceso: {fmt_num(max(0, panel.total[i] - panel.capacity[i]), 1)} HH"])
        QToolTip.showText(event.globalPosition().toPoint(), "<br>".join(lines), self)

    def leaveEvent(self, event):
        QToolTip.hideText()

    def wheelEvent(self, event):
        self.profile.gantt.canvas.wheelEvent(event)


class ResourceProfile(QWidget):
    def __init__(self, state, gantt, parent=None):
        super().__init__(parent)
        self.state, self.gantt = state, gantt
        self.model = None
        self.selected = []
        self.selected_keys = set()
        self._series_by_key = {}
        self.total = self.capacity = np.zeros(0)
        self.maximum = 1.0
        self._request = 0
        self._key = None
        self._choices = {}
        self.group = QComboBox()
        self.group.addItem("Habilidad", "skill")
        self.group.addItem("Recurso", "resource")
        self.series = SeriesListView()
        self.series.setUniformItemSizes(True)
        self.series_model = SeriesListModel(self)
        self.series_proxy = QSortFilterProxyModel(self)
        self.series_proxy.setSourceModel(self.series_model)
        self.series_proxy.setFilterRole(Qt.UserRole)
        self.series_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.series.setModel(self.series_proxy)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar recursos o habilidades…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.series_proxy.setFilterFixedString)
        self.message = QLabel("Perfil de recursos")
        self.message.setWordWrap(True)
        self.sidebar = QWidget()
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(5, 0, 5, 0)
        side.addWidget(self.message)
        side.addWidget(self.group)
        side.addWidget(self.search)
        side.addWidget(self.series, 1)
        side.addWidget(QLabel("Capacidad: línea discontinua · Exceso: rojo"))
        self.retry = button("Reintentar", self.refresh)
        self.retry.hide()
        side.addWidget(self.retry)
        self.canvas = ProfileCanvas(self)
        self.spacer = QWidget()
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.sidebar)
        root.addWidget(self.canvas, 1)
        root.addWidget(self.spacer)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(160)
        self.timer.timeout.connect(self.refresh)
        self.group.currentIndexChanged.connect(lambda *_: self.refresh())
        self.series_model.dataChanged.connect(self._select)
        gantt.viewport_changed.connect(self._viewport)
        theme_signals.theme_changed.connect(self.on_theme)

    @property
    def period(self):
        return "day" if self.gantt.day_width >= 4 else "week"

    def _viewport(self):
        self.sidebar.setFixedWidth(self.gantt.table.width() + self.gantt.splitter.handleWidth())
        self.spacer.setFixedWidth(self.gantt.vertical.width())
        if self.isVisible() and self._key != (self.state.pid, self.group.currentData(), self.period):
            self._request += 1
            self.model = None
            self.timer.start()
        self.canvas.update()

    def refresh(self):
        self._request += 1
        request = self._request
        if not self.isVisible() or not self.state.pid:
            self._key = None
            return
        pid, group, period = self.state.pid, self.group.currentData(), self.period
        self._key = pid, group, period
        self.model = None
        self.message.setText("Cargando perfil de recursos…")
        self.retry.hide()
        self.canvas.update()

        def done(model):
            if request != self._request or self._key != (self.state.pid, self.group.currentData(), self.period):
                return
            self.model = model
            self._series_by_key = {s["key"]: s for s in model.series}
            self.message.setText("Perfil de recursos · todo el proyecto")
            keys = self._choices.get((pid, group), {s["key"] for s in model.series[:5]})
            self.series_model.replace(model.series, keys & self._series_by_key.keys())
            self._select()

        def failed(exc):
            if request == self._request:
                self.message.setText(f"No se pudo cargar el perfil: {exc}")
                self.retry.show()

        run_task(lambda: ProfileModel(self.state.client.load(pid, group, period), period), done, failed)

    def _select(self, *_):
        if self.model is None:
            return
        self.selected_keys = self.series_model.selected_keys
        self._choices[(self.state.pid, self.group.currentData())] = set(self.selected_keys)
        self.selected = sorted((self._series_by_key[key] for key in self.selected_keys),
                               key=lambda s: s['color'])
        self.total = np.zeros(len(self.model.days))
        self.capacity = np.zeros(len(self.model.days))
        for series in self.selected:
            self.total += series["load"]
            self.capacity += series["capacity"]
        self.maximum = max(1, float(self.total.max(initial=0)), float(self.capacity.max(initial=0)))
        self.on_theme()

    def showEvent(self, event):
        super().showEvent(event)
        self._viewport()
        self.timer.start()

    def hideEvent(self, event):
        self._request += 1
        self._key = None
        self.timer.stop()
        super().hideEvent(event)

    def on_theme(self):
        self.series.viewport().update()
        self.canvas.update()
