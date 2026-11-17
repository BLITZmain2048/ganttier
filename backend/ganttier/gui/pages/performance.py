"""Desempeño por trabajador y utilidades de presentación de las páginas Q4."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QButtonGroup, QComboBox, QDateEdit, QLabel, QScrollArea, QTableView, QVBoxLayout, QWidget

from ..theme import tokens, theme_signals, SPACE
from ..widgets.icons import icon
from ..widgets.common import (
    Card, Column, DictTableModel, KpiCard, MplCanvas, SortProxy, button, fmt_date,
    fmt_num, fmt_pct, hbox, iso, qdate, run_task, show_error, EmptyState, Toolbar, setup_table, fit_columns,
)
from .base import Page


def period_bounds(kind: str, reference: date) -> tuple[date, date]:
    if kind == "weekly":
        start = reference - timedelta(days=reference.weekday())
        return start, start + timedelta(days=6)
    if kind == "monthly":
        return reference.replace(day=1), reference.replace(day=monthrange(reference.year, reference.month)[1])
    return date(reference.year, 1, 1), date(reference.year, 12, 31)


def date_editor(value=None, label="Fecha"):
    editor = QDateEdit()
    editor.setCalendarPopup(True)
    editor.setDisplayFormat("dd-MM-yyyy")
    editor.setDate(qdate(value or date.today()))
    editor.setAccessibleName(label)
    return editor


def choices(items, label=""):
    combo = QComboBox()
    for key, text in items:
        combo.addItem(text, key)
    combo.setAccessibleName(label)
    return combo


def scroll_body(page):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.setSpacing(10)
    scroll.setWidget(body)
    page.root.addWidget(scroll)
    return layout


def metric_status(key, value):
    if value is None:
        return None
    if key == "compliance_pct":
        return "good" if value >= 90 else "warning" if value >= 75 else "critical"
    if key == "avg_delay_days":
        return "good" if value <= 0 else "warning" if value <= 3 else "critical"
    if key in ("hh_efficiency", "SPI", "SPI_t", "CPI"):
        return "good" if value >= .95 else "warning" if value >= .85 else "critical"
    return None


def set_indicator(card, value, sub="", status=None):
    if not hasattr(card, "_status_icon"):
        card._status_icon = QLabel()
        card._status_text = QLabel()
        card._status_text.setWordWrap(True)
        card.layout().addLayout(hbox(card._status_icon, card._status_text, None, spacing=SPACE["xs"]))
    card._status_icon.setVisible(bool(status))
    card._status_text.setVisible(bool(status))
    if status:
        label, symbol = {"good": ("En plan", "circle-check"), "warning": ("Atención", "triangle-alert"),
                         "critical": ("Crítico", "circle-alert")}[status]
        color = getattr(tokens(), status)
        card._status_icon.setPixmap(icon(symbol, color=color, size=16).pixmap(16, 16))
        card._status_icon.setAccessibleName(label)
        card._status_text.setText(label)
        card._status_text.setStyleSheet(f"color: {color};")
    card.set(value, sub)
    card._value.setStyleSheet(f"color: {getattr(tokens(), status) if status else tokens().text};")


class PerformanceModel(DictTableModel):
    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.ForegroundRole:
            key = self.columns[index.column()].key
            status = metric_status(key, self.rows[index.row()].get(key))
            if status:
                return QColor(getattr(tokens(), status))
        return super().data(index, role)


class PerformancePage(Page):
    title = "Desempeño"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._pid = None
        self._request = 0
        self.loading = False
        self.rows = []
        body = scroll_body(self)
        self.period = choices([("weekly", "Semana"), ("monthly", "Mes"), ("annual", "Año"),
                               ("custom", "Personalizado")], "Período")
        self.period.setCurrentIndex(1)
        self.period.setParent(self)
        self.period.hide()  # Public compatibility combo, synchronized in both directions.
        self.period_buttons = QButtonGroup(self)
        period_bar = Toolbar()
        period_bar.layout().setSpacing(0)
        for index in range(self.period.count()):
            segment = period_bar.add_action(self.period.itemText(index),
                                            lambda checked=False, i=index: self.period.setCurrentIndex(i))
            segment.setCheckable(True)
            self.period_buttons.addButton(segment, index)
        period_bar.add_stretch()
        self.period_buttons.button(self.period.currentIndex()).setChecked(True)
        self.period.currentIndexChanged.connect(self._sync_period)
        body.addWidget(period_bar)
        self.reference = date_editor(label="Fecha de referencia")
        self.start = date_editor(label="Desde")
        self.end = date_editor(label="Hasta")
        self.bounds_label = QLabel()
        body.addLayout(hbox(QLabel("Referencia"), self.reference,
                            QLabel("Desde"), self.start, QLabel("Hasta"), self.end))
        body.addLayout(hbox(self.bounds_label, None, button("Actualizar", self.refresh, icon="refresh-cw")))
        self.status = QLabel()
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        self.empty_state = EmptyState("users", "Sin datos de desempeño",
                                      "Asigna trabajadores o registra avance indicando quién ejecutó las actividades.")
        body.addWidget(self.empty_state)
        self.kpis = {key: KpiCard(label) for key, label in (
            ("workers", "Trabajadores evaluados"), ("compliance", "Cumplimiento global"),
            ("efficiency", "Eficiencia HH global"))}
        body.addLayout(hbox(*self.kpis.values()))
        columns = [Column("name", "Trabajador", width=240)]
        for key, label, kind, dec in (
            ("compliance_pct", "Cumplimiento", "pct", 1),
            ("hh_efficiency", "Eficiencia HH", "float", 2),
            ("avg_delay_days", "Atraso promedio (d)", "float", 1),
            ("SPI", "SPI", "float", 2), ("CPI", "CPI", "float", 2),
            ("due_in_period", "Vencen", "int", 0),
            ("completed_on_time", "Terminadas a tiempo", "int", 0),
            ("completed_activities", "Terminadas", "int", 0),
            ("pending_activities", "Pendientes", "int", 0),
            ("planned_hours", "HH planificadas", "float", 1),
            ("earned_hours", "HH ganadas", "float", 1),
            ("actual_hours", "HH reales", "float", 1),
        ):
            columns.append(Column(key, label, kind=kind, dec=dec))
        self.model = PerformanceModel(columns)
        self.proxy = SortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        setup_table(self.table)
        self.table.sortByColumn(1, Qt.DescendingOrder)
        self.table.setColumnWidth(0, 240)
        self.table.setMinimumHeight(290)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self._table_card = Card("Indicadores por trabajador").add(self.table)
        body.addWidget(self._table_card)
        self._chart_card = card = Card("Comparativo: hasta 40 trabajadores con mayor valor")
        self.metric_combo = choices([("compliance_pct", "Cumplimiento (%)"), ("hh_efficiency", "Eficiencia HH"),
                               ("avg_delay_days", "Atraso promedio (días)"), ("SPI", "SPI")], "Indicador comparativo")
        card.header.addWidget(self.metric_combo)
        self.chart = MplCanvas()
        card.add(self.chart)
        note = QLabel("Cumplimiento = terminadas a tiempo / actividades que vencían en el período. "
                      "Eficiencia HH = HH ganadas / HH reales. SPI y CPI usan las actividades asignadas a cada trabajador.")
        note.setWordWrap(True)
        card.add(note)
        body.addWidget(card)
        self.period.currentIndexChanged.connect(self.refresh)
        for editor in (self.reference, self.start, self.end):
            editor.dateChanged.connect(self.refresh)
        self.metric_combo.currentIndexChanged.connect(self.draw_chart)
        self._update_bounds()
        theme_signals.theme_changed.connect(self.on_theme)

    def _sync_period(self, index):
        segment = self.period_buttons.button(index)
        if segment is not None:
            segment.setChecked(True)

    def _update_bounds(self):
        custom = self.period.currentData() == "custom"
        self.reference.setEnabled(not custom)
        self.start.setEnabled(custom)
        self.end.setEnabled(custom)
        if custom:
            start, end = self.start.date().toPython(), self.end.date().toPython()
        else:
            start, end = period_bounds(self.period.currentData(), self.reference.date().toPython())
        self.bounds_label.setText(f"{fmt_date(start)} → {fmt_date(end)}")
        return start, end

    def refresh(self, *_):
        pid = self.state.pid
        if pid != self._pid:
            self._pid = pid
            project = self.state.project or {}
            for editor in (self.reference, self.start, self.end):
                editor.blockSignals(True)
                editor.setDate(qdate(project.get("data_date") or date.today()))
                editor.blockSignals(False)
        self._request += 1
        request = self._request
        start, end = self._update_bounds()
        self.rows = []
        self.model.set_rows([])
        self.loading = False
        for card in self.kpis.values():
            set_indicator(card, "—")
        self.draw_chart()
        self.empty_state.hide()
        if not pid or start > end:
            self.empty_state.setVisible(not pid)
            self.status.setText("El inicio debe ser anterior o igual al fin." if pid else "Abre un proyecto.")
            return
        self.loading = True
        self.status.setText("Calculando indicadores…")

        def done(rows):
            if pid != self.state.pid or request != self._request:
                return
            self.loading = False
            self.rows = rows
            self.model.set_rows(rows)
            fit_columns(self.table)
            self.table.setColumnWidth(0, max(self.table.columnWidth(0), 240))
            self.status.setText("" if rows else "Sin datos. Asigna trabajadores o registra avance indicando quién ejecutó las actividades.")
            self.render_kpis()
            self.draw_chart()

        def failed(exc):
            if pid == self.state.pid and request == self._request:
                self.loading = False
                self.status.setText("No se pudo cargar el desempeño. Usa Actualizar para reintentar.")
                show_error(exc, self)

        client = self.state.client
        run_task(lambda: client.performance(pid, iso(start), iso(end)), done, failed, busy="Calculando desempeño…")

    def render_kpis(self):
        total = lambda key: sum(row.get(key, 0) or 0 for row in self.rows)
        due, completed = total("due_in_period"), total("completed_on_time")
        earned, actual = total("earned_hours"), total("actual_hours")
        compliance = 100 * completed / due if due else None
        efficiency = earned / actual if actual else None
        set_indicator(self.kpis["workers"], fmt_num(len(self.rows)))
        set_indicator(self.kpis["compliance"], fmt_pct(compliance), status=metric_status("compliance_pct", compliance))
        set_indicator(self.kpis["efficiency"], fmt_num(efficiency, 2),
                      f"{fmt_num(earned)} HH ganadas / {fmt_num(actual)} reales", metric_status("hh_efficiency", efficiency))

    def draw_chart(self, *_):
        self.chart.clear()
        key = self.metric_combo.currentData()
        rows = sorted((r for r in self.rows if r.get(key) is not None), key=lambda r: r[key], reverse=True)[:40]
        self.empty_state.setVisible(not self.rows and not self.loading)
        self._table_card.setVisible(bool(self.rows))
        self._chart_card.setVisible(bool(self.rows))
        # One 26 px slot per worker, 18 px bar, plus fixed margins for labels.
        # Explicit axes geometry avoids constrained-layout stretching small datasets.
        plot_height = max(1, len(rows)) * 26
        figure_height = plot_height + 72
        self.chart.fig.set_layout_engine(None)
        self.chart.canvas.setFixedHeight(figure_height)
        self.chart.setFixedHeight(figure_height + self.chart.toolbar.sizeHint().height() + max(0, self.chart.layout().spacing()))
        ratio = self.chart.canvas.devicePixelRatioF()
        self.chart.fig.set_size_inches(self.chart.fig.get_figwidth(), figure_height * ratio / self.chart.fig.dpi, forward=False)
        self.chart.ax.set_position([.28, 48 / figure_height, .66, plot_height / figure_height])
        if rows:
            values = [r[key] for r in rows]
            self.chart.ax.barh(range(len(rows)), values, height=18 / 26, color=tokens().series[0])
            self.chart.ax.set_yticks(range(len(rows)), [r["name"] for r in rows])
            self.chart.ax.set_ylim(len(rows) - .5, -.5)
            target = 90 if key == "compliance_pct" else 1.0 if key in ("hh_efficiency", "SPI") else None
            if target is not None:
                self.chart.ax.axvline(target, color=tokens().text2, linestyle="--", linewidth=1.2)
                self.chart.ax.text(target, 1.04, f"Meta {fmt_num(target, 1 if target == 1 else 0)}{' %' if key == 'compliance_pct' else ''}",
                                   transform=self.chart.ax.get_xaxis_transform(), fontsize=8, ha="center", color=tokens().text2)
            low, high = min(0, *values), max(0, target or 0, *values)
            span = max(1, high - low)
            self.chart.ax.set_xlim(low - span * .03, high + span * .2)
            for position, value in enumerate(values):
                self.chart.ax.text(value + span * .02, position, fmt_num(value, 1 if key == "compliance_pct" else 2),
                                   va="center", fontsize=8.5, color=tokens().text2)
            self.chart.ax.set_xlabel(self.metric_combo.currentText())
        else:
            self.chart.ax.text(.5, .5, "Sin datos para el comparativo", ha="center", transform=self.chart.ax.transAxes,
                               color=tokens().text3)
        self.chart.draw()
        self.chart.ax.yaxis.grid(False)
        self.chart.ax.xaxis.grid(True, color=tokens().grid, linewidth=.5, alpha=.6)

    def on_theme(self):
        self.table.viewport().update()
        self.render_kpis()
        self.draw_chart()
