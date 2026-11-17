"""Fecha de corte, líneas base, valor ganado y registro de avance paginado."""
from __future__ import annotations

from datetime import date
from math import isfinite
from threading import Lock

from PySide6.QtCore import QCoreApplication, QDate, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDoubleSpinBox, QGridLayout, QLabel,
    QLineEdit, QMessageBox, QStyledItemDelegate, QTableView, QWidget, QVBoxLayout,
)

from ..theme import tokens, theme_signals, SPACE
from ..widgets.common import (
    Card, Column, DictTableModel, Field, FormDialog, KpiCard, MplCanvas, button,
    confirm, fmt_date, fmt_num, fmt_pct, hbox, iso, notify, qdate, run_task, show_error, to_date,
    Toolbar, EmptyState, setup_table, fit_columns,
)
from .base import Page
from .performance import choices, date_editor, metric_status, scroll_body, set_indicator


PAGE_SIZE = 200
_plot_runtime_lock = Lock()
_plot_runtime_ready = False


def _prepare_plot_runtime():
    """Carga módulos y fuentes en un trabajador; no crea objetos de Qt."""
    global _plot_runtime_ready
    with _plot_runtime_lock:
        if not _plot_runtime_ready:
            from ...control.runtime import interactive_calculation
            with interactive_calculation():
                import matplotlib
                matplotlib.use("QtAgg")
                import matplotlib.dates  # noqa: F401
                from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: F401
                from matplotlib.figure import Figure  # noqa: F401
                _plot_runtime_ready = True


def prefetch_dashboard(client, pid):
    """Precálculo acotado al tamaño retenido por la caché de indicadores."""
    if QCoreApplication.instance() is None:
        return
    # Un proyecto sin programa puede rechazar el cálculo; la página explica ese
    # estado cuando se abre. No cambia el éxito de la apertura del proyecto.
    def work():
        # No competir con la primera carga de un Gantt masivo. Por encima de
        # MAX_ACTIVITIES de control/cache.py no se retienen las fichas para
        # actualización incremental: el cálculo se solicita al abrir Control.
        page = client.get(f"/projects/{pid}/control/activities", params=dict(limit=1, scope="all"))
        if page['total'] > 150_000:
            return None
        try:
            return client.get(f"/projects/{pid}/control/dashboard", params=dict(max_points=128))
        finally:
            _prepare_plot_runtime()
    run_task(work, on_error=lambda _exc: None)


class ProgressModel(DictTableModel):
    def __init__(self, page):
        self.page = page
        super().__init__([
            Column("code", "Código"), Column("name", "Actividad", width=280),
            Column("start", "Inicio planificado", kind="date"), Column("finish", "Término planificado", kind="date"),
            Column("work_hours", "HH", kind="float", dec=1), Column("pct", "% actual", kind="pct", dec=1),
            Column("new_pct", "% nuevo", kind="float", editable=True, dec=1),
            Column("period_hours", "HH reales del período", kind="float", editable=True, dec=2),
            Column("period_cost", "Costo real del período", kind="float", editable=True, dec=2),
            Column("resource_id", "Trabajador", kind="choice", editable=True),
            Column("actual_start", "Inicio real", kind="date", editable=True),
            Column("actual_finish", "Fin real", kind="date", editable=True),
            Column("note", "Nota", editable=True),
        ], parent=page)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid():
            row = self.rows[index.row()]
            if role == Qt.BackgroundRole and row["id"] in self.page.drafts:
                color = QColor(tokens().warning)
                color.setAlphaF(.16)
                return color
            if role == Qt.ForegroundRole and self.columns[index.column()].key == "finish":
                if row.get("finish") and row["finish"] < self.page.cut_date and row["pct"] < 100:
                    return QColor(tokens().critical)
        return super().data(index, role)

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or self.page.saving or not self.columns[index.column()].editable:
            return False
        try:
            if self.columns[index.column()].kind == "float" and value not in (None, ""):
                number = float(str(value).replace(",", "."))
                if not isfinite(number) or number < 0 or (index.column() == 6 and number > 100):
                    return False
            return super().setData(index, value, role)
        except (TypeError, ValueError):
            return False


class ProgressDelegate(QStyledItemDelegate):
    """Editores tipados sin crear widgets para cada celda de la tabla."""

    def createEditor(self, parent, option, index):
        col = index.model().columns[index.column()]
        if col.kind == "choice":
            editor = QComboBox(parent)
            for value, label in col.choices:
                editor.addItem(label, value)
            return editor
        if col.kind == "float":
            editor = QDoubleSpinBox(parent)
            editor.setDecimals(2)
            editor.setRange(-1, 100 if col.key == "new_pct" else 1e12)
            editor.setSpecialValueText("—")
            return editor
        if col.kind == "date":
            editor = QDateEdit(parent)
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd-MM-yyyy")
            editor.setMinimumDate(QDate(1900, 1, 1))
            editor.setSpecialValueText("—")
            return editor
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        value = index.data(Qt.UserRole)
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(max(0, editor.findData(value)))
        elif isinstance(editor, QDoubleSpinBox):
            editor.setValue(-1 if value is None else value)
        elif isinstance(editor, QDateEdit):
            editor.setDate(qdate(value) if value else editor.minimumDate())
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentData())
        elif isinstance(editor, QDoubleSpinBox):
            model.setData(index, None if editor.value() < 0 else editor.value())
        elif isinstance(editor, QDateEdit):
            model.setData(index, None if editor.date() == editor.minimumDate() else iso(editor.date()))
        else:
            super().setModelData(editor, model, index)


class ControlPage(Page):
    title = "Avance y curva S"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._pid = None
        self._request = 0
        self._chart_request = 0
        self._table_request = 0
        self._page_rows = []
        self.loading = False
        self.chart_loading = False
        self.saving = False
        self.drafts_by_project = {}
        self.drafts = {}
        self.arrays = {}
        self.indices = []
        self.page_number = 0
        self.cut_date = date.today().isoformat()
        self.scurve = None
        self.evm = None
        body = scroll_body(self)
        self.data_date = date_editor(label="Fecha de corte")
        self.toolbar = Toolbar()
        self.toolbar.add(QLabel("Fecha de corte"))
        self.toolbar.add(self.data_date)
        self.toolbar.add_action("Aplicar", self.save_data_date, primary=True, icon="check")
        self.toolbar.add_action("Hoy", lambda: self.data_date.setDate(QDate.currentDate()), icon="calendar-days", kind="ghost")
        self.toolbar.add_stretch()
        self.toolbar.add_action("Actualizar", self.refresh, icon="refresh-cw", kind="ghost")
        self.toolbar.add_menu("Líneas base", [("Nueva…", self.new_baseline, "plus"),
                                             ("Activar seleccionada", self.activate_baseline, "check"),
                                             ("Eliminar seleccionada", self.delete_baseline, "trash-2")], icon="layers")
        body.addWidget(self.toolbar)
        self.cut_label = QLabel()
        self.cut_label.setWordWrap(True)
        self.cut_label.setProperty("role", "caption")
        body.addWidget(self.cut_label)
        self.baselines = choices([], "Línea base")
        self.baselines.setMinimumWidth(240)
        self.baselines.setPlaceholderText("Sin línea base activa")
        body.addLayout(hbox(QLabel("Línea base"), self.baselines, None))
        self.kpis = {}
        self.kpi_panel = QWidget()
        self.kpi_grid = grid = QGridLayout(self.kpi_panel)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SPACE["sm"])
        self._kpi_columns = 5
        for n, (key, label) in enumerate((
            ("percent_planned", "% planificado"), ("percent_complete", "% real (ganado)"),
            ("SPI", "SPI"), ("SPI_t", "SPI(t): cronograma ganado"), ("CPI", "CPI"),
            ("planned_finish", "Término línea base"), ("forecast_finish", "Término pronosticado"),
            ("EAC", "EAC / presupuesto"), ("values", "PV / EV / AC"), ("TCPI", "TCPI"),
        )):
            self.kpis[key] = KpiCard(label)
            self.kpis[key]._label.setWordWrap(True)
            grid.addWidget(self.kpis[key], n // 5, n % 5)
        body.addWidget(self.kpi_panel)
        chart_card = Card("Curva S")
        self.period = choices([("day", "Día"), ("week", "Semana"), ("month", "Mes")], "Período curva S")
        self.period.setCurrentIndex(1)
        self.basis = choices([("hours", "HH"), ("cost", "Costo"), ("count", "Cantidad")], "Base de cálculo")
        self.scale = choices([(False, "%"), (True, "Absoluto")], "Escala curva S")
        for w in (self.period, self.basis, self.scale):
            chart_card.header.addWidget(w)
        # Construir el lienzo solo cuando llegan indicadores; la tabla no espera
        # la inicialización gráfica. No modifica el módulo común ni sus imports.
        self.chart = None
        self.chart_container = QWidget()
        self.chart_container.setMinimumHeight(370)
        self.chart_layout = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_state = EmptyState("chart-gantt", "Primero genera el programa",
                                      "Programa las actividades para calcular la curva S y los indicadores de avance.",
                                      actions=[("Ir a Actividades y Gantt", self._go_gantt, True)])
        self.empty_state.hide()
        chart_card.add(self.empty_state)
        chart_card.add(self.chart_container)
        self.chart_note = QLabel()
        self.chart_note.setWordWrap(True)
        chart_card.add(self.chart_note)
        body.addWidget(chart_card)
        progress = Card("Registro de avance")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar código, actividad, instalación o habilidad…")
        self.search.setClearButtonEnabled(True)
        self.scope = choices([("open", "Iniciadas o por iniciar ≤ corte"), ("late", "Atrasadas"),
                              ("all", "Todas")], "Filtro de actividades")
        progress.add(layout=hbox(self.search, self.scope))
        self.model = ProgressModel(self)
        self.model.edited.connect(self._edited)
        self.table = QTableView()
        self.table.setModel(self.model)
        setup_table(self.table, sortable=False, stretch_last=False)
        self.table.setItemDelegate(ProgressDelegate(self.table))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(380)
        for i, col in enumerate(self.model.columns):
            self.table.setColumnWidth(i, col.width or (160 if i >= 7 else 125))
        progress.add(self.table)
        self.previous = button("Anterior", lambda: self.change_page(-1))
        self.next = button("Siguiente", lambda: self.change_page(1))
        self.page_label = QLabel()
        self.save_button = button("Guardar avance", self.save_progress, primary=True, icon="save")
        self.discard_button = button("Descartar", self.discard, icon="undo-2", kind="ghost")
        self.draft_count = QLabel("0 cambios sin guardar")
        self.draft_count.setProperty("role", "caption")
        progress.add(layout=hbox(self.page_label, self.previous, self.next, None, self.discard_button, self.draft_count, self.save_button))
        self.status = QLabel()
        self.status.setWordWrap(True)
        progress.add(self.status)
        body.addWidget(progress)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self.filter_rows)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.scope.currentIndexChanged.connect(self.filter_rows)
        self.period.currentIndexChanged.connect(self.load_chart)
        self.basis.currentIndexChanged.connect(self.load_chart)
        self.scale.currentIndexChanged.connect(self.draw_chart)
        state.catalogs_changed.connect(self.refresh)
        state.project_changed.connect(self._project_drafts)
        self._update_actions()
        theme_signals.theme_changed.connect(self.on_theme)

    def _go_gantt(self):
        go = getattr(self.window(), "go", None)
        if callable(go):
            go("gantt")

    def _show_no_program(self, visible):
        self.empty_state.setVisible(visible)
        self.chart_container.setVisible(not visible)
        self.kpi_panel.setVisible(not visible)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "kpi_grid"):
            return
        columns = 3 if event.size().width() < 1100 else 5
        if columns != self._kpi_columns:
            while self.kpi_grid.count():
                self.kpi_grid.takeAt(0)
            for index, card in enumerate(self.kpis.values()):
                self.kpi_grid.addWidget(card, index // columns, index % columns)
            for index in range(5):
                self.kpi_grid.setColumnStretch(index, 1 if index < columns else 0)
            self._kpi_columns = columns

    def has_unsaved(self) -> bool:
        return bool(self.drafts) or any(self.drafts_by_project.values())

    def _project_drafts(self, *_):
        # This also runs while hidden: drafts always belongs to the active pid.
        if self.state.pid != self._pid:
            self.refresh()

    def _project_invalidated(self, project):
        # La lectura actual ya incluye estos metadatos. La señal se conserva
        # para las otras páginas, sin reiniciar nuestras peticiones en curso.
        if getattr(self, '_publishing_project', False) and self.state.pid == self._pid:
            return
        super()._project_invalidated(project)

    def refresh(self, *_):
        pid = self.state.pid
        if getattr(self, '_progress_refresh', False) and pid == self._pid:
            # Un avance no cambia recursos ni líneas base: recargar solo la ventana.
            self._request += 1
            self._load_page()
            self.load_chart()
            return
        if pid != self._pid:
            if self._pid is not None:
                self.drafts_by_project[self._pid] = self.drafts
            self._pid = pid
            self.drafts = self.drafts_by_project.setdefault(pid, {}) if pid is not None else {}
            self.arrays = {}
            self.indices = []
            self._page_rows = []
            self.scurve = self.evm = None
            # Un filtro de búsqueda de otro proyecto ocultaría todas las actividades del nuevo.
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self.model.set_rows([])
            self.baselines.clear()
            self.page_number = 0
        self._request += 1
        request = self._request
        project = self.state.project or {}
        self.cut_date = project.get("data_date") or date.today().isoformat()
        self.data_date.setDate(qdate(self.cut_date))
        self.cut_label.setText(f"Corte aplicado: {fmt_date(self.cut_date)}. Las HH y el costo ingresados corresponden al período, no al acumulado.")
        self.loading = bool(pid)
        self.table.setEnabled(False)
        self._update_actions()
        self._chart_request += 1  # Descarta indicadores de una lectura anterior.
        if not pid:
            self.chart_loading = False
            self.model.set_rows([])
            self._show_no_program(True)
            return
        self.status.setText("Cargando actividades y líneas base…")
        client = self.state.client

        def done(result):
            if pid != self.state.pid or request != self._request:
                return
            project, baselines, payload, resources = result
            changed = project != self.state.project
            self.state.project = project
            self.cut_date = project.get("data_date") or date.today().isoformat()
            self.data_date.setDate(qdate(self.cut_date))
            self.cut_label.setText(f"Corte aplicado: {fmt_date(self.cut_date)}. Las HH y el costo ingresados corresponden al período, no al acumulado.")
            self.baselines.clear()
            active = (self.state.project or {}).get("active_baseline_id")
            for b in baselines:
                self.baselines.addItem(f"{b['name']} · {fmt_date(b.get('created_at'))}" + (" · Activa" if b["id"] == active else ""), b["id"])
            index = self.baselines.findData(active)
            if index >= 0:
                self.baselines.setCurrentIndex(index)
            self.model.columns[9].choices = [(None, "—")] + [(r["id"], r["name"]) for r in resources
                                                             if r.get("kind") in ("trabajador", "cuadrilla")]
            if table_request == self._table_request:
                self.loading = False
                self.table.setEnabled(True)
                self.status.setText("")
                self._accept_page(payload)
            # Los indicadores se iniciaron en paralelo con esta lectura.
            if changed:
                self._publishing_project = True
                try:
                    self.state.project_changed.emit(project)
                finally:
                    self._publishing_project = False

        def failed(exc):
            if pid == self.state.pid and request == self._request:
                self.loading = False
                self.arrays = {}
                self.model.set_rows([])
                self._update_actions()
                self.save_button.setEnabled(False)
                self.status.setText("No se pudieron cargar las actividades. Usa Actualizar para reintentar; el borrador se conserva.")
                show_error(exc, self)

        self.load_chart()
        params = self._page_params()
        self._table_request += 1
        table_request = self._table_request
        run_task(lambda: (client.project(pid), client.baselines(pid),
                         client.get(f"/projects/{pid}/control/activities", params=params), client.resources(pid)), done, failed,
                 busy="Cargando control…")

    def load_chart(self, *_):
        self._chart_request += 1
        request, pid = self._chart_request, self.state.pid
        period, basis = self.period.currentData(), self.basis.currentData()
        self.chart_loading = bool(pid)
        self._show_no_program(False)
        if getattr(self, '_chart_pid', None) != pid:
            self.scurve = self.evm = None
            self._plot_dates = None
            self.render_kpis()
            self.chart_container.hide()
        if not pid:
            self.draw_chart()
        if not pid:
            self._show_no_program(True)
            return
        self.chart_note.setText("Calculando curva S y valor ganado…")
        client = self.state.client

        def done(result):
            if pid == self.state.pid and request == self._chart_request:
                self.scurve, self.evm = result['scurve'], result['evm']
                self._plot_dates = result.get('_plot_dates')
                self._plot_labels = tuple(self.scurve['labels'])
                self._chart_pid = pid
                self.chart_loading = False
                self._show_no_program(not bool(self.scurve.get("labels")))
                self.render_kpis()
                self.draw_chart()
                self.chart_note.setText(f"Presupuesto total: {fmt_num(self.scurve.get('budget_total'))} · "
                                       f"Término planificado: {fmt_date(self.scurve.get('planned_finish'))} · "
                                       f"Término proyectado: {fmt_date(self.scurve.get('forecast_finish'))}")

        def failed(exc):
            if pid == self.state.pid and request == self._chart_request:
                self.chart_loading = False
                text = str(getattr(exc, "message", "") or exc)
                if "inicio y término planificados" in text or "fechas vigentes" in text:
                    self._show_no_program(True)
                    # Lista de tareas aún sin programa: no es un error, falta «Generar programa».
                    self.chart_note.setText("El proyecto aún no tiene programa: usa «Generar programa» en "
                                            "Actividades y Gantt (o en Programar / optimizar) para calcular la curva S y el EVM.")
                    return
                self.chart_note.setText("No se pudieron calcular los indicadores. Usa Actualizar para reintentar.")
                show_error(exc, self)

        # Un punto por cada dos píxeles horizontales, con extremos y corte.
        # El endpoint sin max_points mantiene la serie completa para exportación.
        max_points = max(128, min(2048, self.chart_container.width() // 2))
        def work():
            result = client.get(f"/projects/{pid}/control/dashboard",
                                params=dict(period=period, basis=basis, max_points=max_points))
            # Cargar después del SQL evita competir por el GIL en cada fila.
            # Ambos pasos siguen fuera del hilo de interfaz.
            _prepare_plot_runtime()
            import matplotlib.dates as mdates
            result['_plot_dates'] = mdates.date2num([date.fromisoformat(d) for d in result['scurve']['labels']])
            return result
        run_task(work, done, failed)

    def render_kpis(self):
        e = self.evm or {}
        for key, card in self.kpis.items():
            value, sub = e.get(key), ""
            if key.startswith("percent_"):
                text = fmt_pct(value)
            elif key.endswith("finish"):
                text = fmt_date(value) or "—"
                if key == "forecast_finish" and value and e.get("planned_finish"):
                    sub = f"{(to_date(value) - to_date(e['planned_finish'])).days:+d} días vs. línea base"
            elif key == "values":
                text = " / ".join(fmt_num(e.get(k)) for k in ("PV", "EV", "AC"))
                sub = f"SV {fmt_num(e.get('SV'))} · CV {fmt_num(e.get('CV'))}"
            else:
                text = fmt_num(value, 2 if key in ("SPI", "SPI_t", "CPI", "TCPI") else 0)
                if key == "EAC":
                    unit = {"hours": "HH", "count": "actividades", "cost": (self.state.project or {}).get("currency", "CLP")}.get(e.get("basis"), "")
                    sub = f"{unit} · BAC {fmt_num(e.get('BAC'))} · VAC {fmt_num(e.get('VAC'))}"
                elif key == "SPI_t":
                    sub = "Cronograma ganado"
                elif key == "TCPI":
                    sub = "Índice para completar"
            set_indicator(card, text, sub, metric_status(key, value))

    def draw_chart(self, *_):
        if self.chart is None and not self.scurve:
            return
        from ...control.runtime import interactive_calculation
        with interactive_calculation():
            self._draw_chart()

    def _draw_chart(self):
        import matplotlib.dates as mdates
        if self.chart is None:
            if not self.scurve:
                return
            self.chart = MplCanvas()
            # Márgenes estables: evita resolver constrained_layout en cada pintura.
            self.chart.fig.set_layout_engine(None)
            self.chart.fig.subplots_adjust(left=.10, right=.98, bottom=.20, top=.80)
            self.chart_layout.addWidget(self.chart)
            ax = self.chart.ax
            self._curve_lines = {}
            for key, label, color, style in (("planned", "Planificada", 0, "-"), ("earned", "Ganada (avance)", 1, "-"),
                                            ("actual", "Real (HH/costo)", 2, "-"), ("forecast", "Proyección", 6, "--")):
                self._curve_lines[key] = (ax.plot([], [], label=label, color=tokens().series[color],
                                                 linestyle=style, linewidth=2)[0], color)
            self._cut_line = ax.axvline(0, linewidth=1.2)
            self._cut_text = ax.text(0, .98, "", transform=ax.get_xaxis_transform(), va="top", ha="left", fontsize=8)
            self._empty_text = ax.text(.5, .5, "Sin datos para la curva S", ha="center", transform=ax.transAxes)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m-%Y"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=8))
            ax.legend(fontsize=8, ncol=2, loc="lower center", bbox_to_anchor=(.5, 1.04), frameon=False)
        sc, ax, theme = self.scurve, self.chart.ax, tokens()
        has_data = bool(sc and sc.get("labels"))
        absolute = self.scale.currentData()
        dates = getattr(self, '_plot_dates', None)
        if has_data and (dates is None or getattr(self, '_plot_labels', ()) != tuple(sc['labels'])):
            # También admite llamadas directas de pruebas/temas sin una petición.
            dates = mdates.date2num([date.fromisoformat(d) for d in sc['labels']])
        for key, (line, color) in self._curve_lines.items():
            values = [float("nan") if v is None else v for v in sc[key + ("_abs" if absolute else "_cum")]] if has_data else []
            line.set_data(dates if has_data else [], values)
            line.set_color(theme.series[color])
        self._empty_text.set_visible(not has_data)
        self._empty_text.set_color(theme.text3)
        has_cut = has_data and bool(sc.get('data_date'))
        self._cut_line.set_visible(has_cut)
        self._cut_text.set_visible(has_cut)
        if has_cut:
            cut = date.fromisoformat(sc['data_date'])
            ordinal = mdates.date2num(cut)
            self._cut_line.set_xdata([ordinal, ordinal])
            self._cut_line.set_color(theme.datadate)
            self._cut_text.set_x(ordinal)
            self._cut_text.set_text(f"Corte {cut:%d-%m}")
            self._cut_text.set_color(theme.text2)
            self._cut_text.set_bbox(dict(facecolor=theme.surface, edgecolor=theme.border, boxstyle="round,pad=.3"))
        if has_data:
            ax.set_ylabel({"hours": "HH", "cost": "Costo", "count": "Cantidad"}[sc["basis"]] if absolute else "%")
            ax.relim(visible_only=True)
            ax.autoscale(enable=True, axis='both')
            ax.set_ylim(bottom=0, top=ax.get_ylim()[1] if absolute else max(100, ax.get_ylim()[1]))
        self.chart.toolbar.update()
        self.chart.draw()
        ax.grid(True, color=theme.grid, linewidth=.5, alpha=.55)
        for edge in ("top", "right"):
            ax.spines[edge].set_visible(False)

    def filter_rows(self, *_, reset=True):
        self._search_timer.stop()
        if reset:
            self.page_number = 0
        self._load_page()

    def _page_params(self):
        return dict(offset=self.page_number * PAGE_SIZE, limit=PAGE_SIZE,
                    q=self.search.text().strip(), scope=self.scope.currentData())

    def _accept_page(self, payload):
        self._page_rows = payload['rows']
        self.indices = range(payload['total'])  # Solo conteo; nunca materializa 100k índices.
        self.page_number = payload['offset'] // PAGE_SIZE
        self.cut_date = payload['data_date']
        fields = dict(ids='id', codes='code', names='name', starts='start', finishes='finish', work_hours='work_hours', pct='pct')
        self.arrays = {key: [row[source] for row in self._page_rows] for key, source in fields.items()}
        self.show_page()

    def _load_page(self):
        pid = self.state.pid
        if not pid:
            return
        self._table_request += 1
        request, params, client = self._table_request, self._page_params(), self.state.client
        self.loading = True
        self.table.setEnabled(False)
        self._update_actions()
        def done(payload):
            if pid != self.state.pid or request != self._table_request:
                return
            self.loading = False
            self.table.setEnabled(True)
            self._accept_page(payload)
        def failed(exc):
            if pid == self.state.pid and request == self._table_request:
                self.loading = False
                self.arrays = {}
                self.indices = []
                self._page_rows = []
                self.model.set_rows([])
                self.status.setText('No se pudo cargar la página. Usa Actualizar para reintentar; el borrador se conserva.')
                self._update_actions()
                show_error(exc, self)
        run_task(lambda: client.get(f'/projects/{pid}/control/activities', params=params), done, failed)

    def change_page(self, step):
        self.page_number = max(0, min(self.page_number + step, max(0, (len(self.indices) - 1) // PAGE_SIZE)))
        self._load_page()

    def show_page(self):
        rows = []
        for source in self._page_rows:
            row = dict(source)
            row.update(self.drafts.get(row["id"], {}))
            rows.append(row)
        self.model.set_rows(rows)
        # Los anchos ya se fijan al construir: no medir todas las celdas al paginar.
        pages = max(1, (len(self.indices) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_label.setText(f"{fmt_num(len(self.indices))} actividades · Página {self.page_number + 1} de {pages}")
        self.previous.setEnabled(self.page_number > 0)
        self.next.setEnabled(self.page_number + 1 < pages)
        self._update_actions()

    def _edited(self, row, key, value):
        aid = self.model.rows[row]["id"]
        self.drafts.setdefault(aid, {'_pct': self.model.rows[row]['pct']})[key] = value
        self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, self.model.columnCount() - 1), [Qt.BackgroundRole])
        self._update_actions()

    def _update_actions(self):
        self.draft_count.setText(f"{len(self.drafts)} cambios sin guardar")
        self.save_button.setEnabled(bool(self.drafts) and not self.loading and not self.saving and bool(self.arrays))
        self.discard_button.setEnabled(bool(self.drafts) and not self.saving)
        if self.loading or self.saving:
            self.previous.setEnabled(False)
            self.next.setEnabled(False)

    def discard(self):
        if not self.saving:
            self.drafts.clear()
            self.show_page()

    def _mutate(self, operation, message, clear_drafts=False):
        pid, client = self.state.pid, self.state.client
        if not pid or self.saving:
            return
        self.saving = True
        saved_drafts = self.drafts
        self.setEnabled(False)

        def work():
            operation(client, pid)
            # El POST ya está confirmado. Un fallo posterior de lectura no debe
            # presentarse como fallo del guardado ni permitir reenviar sus HH.
            try:
                return client.project(pid), None
            except Exception as exc:
                return None, exc

        def done(result):
            self.saving = False
            self.setEnabled(True)
            if clear_drafts:
                saved_drafts.clear()
            if pid != self.state.pid:
                self._update_actions()
                return
            project, reload_error = result
            if reload_error is not None:
                self.arrays = {}
                self.indices = []
                self.model.set_rows([])
                self._stale = True
                self._update_actions()
                self.status.setText("La operación se guardó, pero no se pudo recargar el proyecto. Usa Actualizar; no vuelvas a registrar el mismo avance.")
                show_error(RuntimeError(f"{self.status.text()}\n{reload_error}"), self)
                return
            self.state.project = project
            self._progress_refresh = clear_drafts
            try:
                self.state.project_changed.emit(project)
                self.state.bump_schedule()
                if not self.isVisible():
                    self.refresh()
            finally:
                self._progress_refresh = False
            notify(message)

        def failed(exc):
            self.saving = False
            self.setEnabled(True)
            self._update_actions()
            show_error(exc, self)

        run_task(work, done, failed, busy=message + "…")

    def save_data_date(self):
        if self.drafts:
            QMessageBox.warning(self, "Avance pendiente", "Guarda o descarta el borrador antes de cambiar la fecha de corte.")
            return
        cut = iso(self.data_date.date())
        self._mutate(lambda c, p: c.set_data_date(p, cut), "Fecha de corte actualizada")

    def new_baseline(self):
        dlg = FormDialog("Nueva línea base", [Field("name", "Nombre")],
                         {"name": f"Línea base {fmt_date(date.today())}"}, self, columns=1,
                         validate=lambda v: None if v.get("name", "").strip() else "Ingresa un nombre.")
        if dlg.exec():
            self.create_baseline(dlg.values()["name"])

    def create_baseline(self, name):
        name = name.strip()
        if name:
            self._mutate(lambda c, p: c.create_baseline(p, name), "Línea base creada")

    def activate_baseline(self):
        bid = self.baselines.currentData()
        if bid is not None:
            self._mutate(lambda c, p: c.activate_baseline(bid), "Línea base activada")

    def delete_baseline(self):
        bid = self.baselines.currentData()
        if bid is not None and confirm(self, "Eliminar línea base", f"¿Eliminar «{self.baselines.currentText()}»?", danger=True):
            self._mutate(lambda c, p: c.delete_baseline(bid), "Línea base eliminada")

    def save_progress(self):
        if not self.drafts or self.loading or self.saving or not self.arrays:
            return
        cut = self.cut_date
        if iso(self.data_date.date()) != cut:
            QMessageBox.warning(self, "Fecha de corte", "La fecha editada aún no está aplicada. Restaura el corte aplicado para guardar este borrador.")
            return
        entries = []
        try:
            for aid, draft in self.drafts.items():
                pct = draft.get("new_pct")
                pct = draft['_pct'] if pct is None else pct
                entry = dict(activity_id=aid, date=cut, percent_complete=pct,
                             actual_hours=draft.get("period_hours") or 0, actual_cost=draft.get("period_cost") or 0,
                             resource_id=draft.get("resource_id"), actual_start=draft.get("actual_start"),
                             actual_finish=draft.get("actual_finish") or (cut if pct >= 100 else None), note=draft.get("note") or "")
                self._validate_entry(entry)
                entries.append(entry)
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Revisa el avance", str(exc))
            return

        def save(client, pid):
            # El endpoint ya resuelve el inicio real con el historial al corte.
            client.add_progress(pid, entries)

        self._mutate(save, "Avance registrado", clear_drafts=True)

    @staticmethod
    def _validate_entry(entry):
        pct = entry["percent_complete"]
        if not isfinite(pct) or not 0 <= pct <= 100:
            raise ValueError("El porcentaje debe estar entre 0 y 100.")
        if any(not isfinite(entry[k]) or entry[k] < 0 for k in ("actual_hours", "actual_cost")):
            raise ValueError("Las HH y el costo deben ser números no negativos.")
        start, finish, cut = entry.get("actual_start"), entry.get("actual_finish"), entry["date"]
        if any(value and (not to_date(value) or value > cut) for value in (start, finish)):
            raise ValueError("Las fechas reales deben ser válidas y no posteriores al corte.")
        if finish and (pct < 100 or (start and finish < start)):
            raise ValueError("El fin real requiere 100 % de avance y no puede ser anterior al inicio real.")

    def on_theme(self):
        self.table.viewport().update()
        self.render_kpis()
        self.draw_chart()
