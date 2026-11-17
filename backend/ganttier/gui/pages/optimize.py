"""Generación y optimización de programas, comparación e historial de corridas."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from html import escape
import json
import re

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox,
                              QGridLayout, QLabel, QScrollArea, QSlider, QTableView,
                              QTextBrowser, QVBoxLayout, QWidget, QStyledItemDelegate, QButtonGroup,
                              QPushButton)

from ..client import ApiError
from ..theme import tokens
from ..widgets.common import (Card, Column, DictTableModel, KpiCard, MplCanvas,
                              button, fmt_date, fmt_num, hbox, notify, run_task, show_error)
from .base import Page
from ..widgets.responsive import constrain_actions
from .role_options import RoleOptions
from .activities import (Toolbar, EmptyState, StatusBanner, Section, ux_button,
                         setup_table, fit_columns)
from ..widgets.icons import icon
from ..gantt.dialogs import operation_gate, status_text, method_text, solve_report, show_infeasible


OBJECTIVES = [("level", "Nivelar carga"), ("makespan", "Minimizar plazo"),
              ("window", "Respetar ventanas macro"), ("cost", "Minimizar costo/HH extra")]
PRESETS = {"Nivelar carga": (100, 0, 0, 0), "Minimizar plazo": (0, 100, 0, 0),
           "Respetar ventanas": (0, 0, 100, 0), "Minimizar costo": (0, 0, 0, 100),
           "Equilibrado": (100, 100, 100, 100), "Plazo + nivelación": (50, 100, 0, 20),
           "Ventanas + nivelación": (100, 0, 100, 20)}
METRICS = [("finish", "Término"), ("peak_load", "Pico de carga (HH)"),
           ("load_std", "Desviación de carga"), ("overload_hours", "HH sobre capacidad"),
           ("overtime_hours", "HH extra"), ("overtime_cost", "Costo extra"),
           ("activities_out_of_window", "Actividades fuera de ventana"),
           ("days_out_of_window", "Días fuera de ventana")]
FEASIBLE = {"optimo", "factible", "heuristico", "optimal", "feasible"}


def solve_program(client, pid, weights, options, project):
    """Compatibilidad con servidores anteriores; ejecutar exclusivamente en run_task."""
    try:
        return client.post(f"/projects/{pid}/schedule/solve", {"weights": weights, "options": options})
    except ApiError as exc:
        if exc.status not in (404, 405):
            raise
    if project.get("mode") == "mantenimiento":
        activities = client.columnar(pid)
        if not activities.get("ids"):
            client.generate_occurrences(pid, project["start_date"], project["end_date"], replace=False)
    result = client.optimize(pid, weights, options, apply=True)
    result = dict(result)
    result["warnings"] = list(result.get("warnings") or []) + [
        "El servidor no tiene /schedule/solve; se usó el método antiguo"]
    result["compatibility_fallback"] = True
    return result


def parse_object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def verification_report(result):
    for key in ("verification", "feasibility", "feasibility_report", "verification_report", "validation"):
        value = result.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"violations": value}
    if "violations" in result or "feasible" in result:
        return {"violations": result.get("violations", []), "feasible": result.get("feasible")}
    return None


def applicable(result):
    if result.get("feasible") is False or result.get("violations"):
        return False
    report = verification_report(result)
    if report and (report.get("violations") or report.get("feasible") is False or
                   report.get("is_feasible") is False or report.get("violation_count", 0)):
        return False
    return str(result.get("status", "")).lower() in FEASIBLE


def grouped_warnings(warnings):
    """Agrupa mensajes con el prefijo de actividad usado por el motor."""
    counts = Counter()
    for warning in warnings:
        message = warning.get("message", str(warning)) if isinstance(warning, dict) else str(warning)
        message = re.sub(r"^(?:Actividad\s+)?(?:#?\d+|[A-Z][A-Z0-9_.-]*\d[A-Z0-9_.-]*):\s*", "", message)
        counts[message] += 1
    return [f"{message} ({count} veces)" if count > 1 else message for message, count in counts.items()]


class ObjectiveCard(QPushButton):
    """Toda la tarjeta es pulsable; el texto de ayuda conserva su jerarquía."""

    def __init__(self, title, description, symbol, parent=None):
        super().__init__(parent)
        self._symbol_name = symbol
        self.setCheckable(True)
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)
        self.setToolTip(description)
        self.setMinimumHeight(76)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        self._symbol = QLabel()
        heading = QLabel(title)
        heading.setProperty("role", "section")
        caption = QLabel(description)
        caption.setProperty("role", "caption")
        layout.addLayout(hbox(self._symbol, heading, None))
        layout.addWidget(caption)
        for label in (self._symbol, heading, caption):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.on_theme()

    def on_theme(self):
        self._symbol.setPixmap(icon(self._symbol_name).pixmap(18, 18))
        t = tokens()
        # La QSS global fija min-height 18 px para botones: la tarjeta necesita su propio mínimo.
        self.setStyleSheet(f"QPushButton {{min-height: 58px; padding: 0;}}"
                           f"QPushButton:checked {{background: {t.accent_soft}; border-color: {t.accent};}}"
                           "QLabel {background: transparent; border: none;}")


class RunApplyDelegate(QStyledItemDelegate):
    """Acción por corrida sin insertar un widget por fila en la tabla."""

    def __init__(self, page):
        super().__init__(page.runs_table)
        self.page = page

    def paint(self, painter, option, index):
        if not index.data():
            super().paint(painter, option, index)
            return
        painter.save()
        t = tokens()
        rect = option.rect.adjusted(4, 3, -4, -3)
        painter.setBrush(QColor(t.surface2 if self.page.running else t.accent_soft))
        painter.setPen(QColor(t.border))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor(t.text3 if self.page.running else t.accent))
        painter.drawText(rect, Qt.AlignCenter, "Aplicar")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        mouse = event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton
        keyboard = event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Space)
        if (mouse or keyboard) and index.data() and not self.page.running:
            self.page.apply_run(model.row_dict(index.row())["id"])
            return True
        return super().editorEvent(event, model, option, index)


class OptimizePage(Page):
    title = "Programar / optimizar"
    subtitle = "Elige un objetivo, compara resultados y aplica un programa factible."

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.result = None
        self.result_pid = None
        self.loading = False
        self.running = False
        self._gate = operation_gate(state)
        self._running_key = None
        self._ticket = 0
        self._pid = None
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.root.addWidget(scroll)
        top = QGridLayout()
        objectives = self.objectives_card = Card("Objetivos")
        self.preset = QComboBox(self)
        self.preset.addItems(list(PRESETS))
        self.preset.hide()
        self.objective_cards = {}
        self.objective_group = QButtonGroup(self)
        self.objective_group.setExclusive(True)
        cards = QGridLayout()
        choices = [
            ("Equilibrado", "Equilibrado", "gauge", "Combina los cuatro objetivos."),
            ("Minimizar plazo", "Minimizar plazo", "timer", "Busca terminar lo antes posible."),
            ("Nivelar carga", "Nivelar carga", "chart-column", "Distribuye el trabajo en el tiempo."),
            ("Respetar ventanas", "Respetar ventanas", "calendar-range", "Prioriza los meses comprometidos."),
            ("Minimizar costo", "Minimizar costo/HH extra", "hard-hat", "Reduce el costo de horas extra.")]
        for i, (name, title, symbol, description) in enumerate(choices):
            card = ObjectiveCard(title, description, symbol)
            card.clicked.connect(lambda checked=False, value=name: self.set_preset(value))
            self.objective_group.addButton(card)
            self.objective_cards[name] = card
            cards.addWidget(card, i // 3, i % 3)
        objectives.add(layout=cards)
        self.advanced_toggle = ux_button("Pesos avanzados", icon="chevron-right", kind="ghost")
        self.advanced_toggle.setCheckable(True)
        objectives.add(self.advanced_toggle)
        self.advanced_panel = QWidget()
        advanced = QVBoxLayout(self.advanced_panel)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.advanced_panel.hide()
        self.advanced_toggle.toggled.connect(self.advanced_panel.setVisible)
        self.advanced_toggle.toggled.connect(lambda opened: self.advanced_toggle.setIcon(icon("chevron-down" if opened else "chevron-right")))
        self.sliders, self.percentages = {}, {}
        for key, label in OBJECTIVES:
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setAccessibleName(label)
            self.sliders[key] = slider
            self.percentages[key] = QLabel()
            advanced.addLayout(hbox(QLabel(label), slider, self.percentages[key]))
            slider.valueChanged.connect(self._weights_changed)
        hint = QLabel("Los porcentajes representan la importancia relativa de cada objetivo.")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        advanced.addWidget(hint)
        objectives.add(self.advanced_panel)
        self.preset.currentTextChanged.connect(self.set_preset)
        self.set_preset("Equilibrado")
        top.addWidget(objectives, 0, 0, 1, 2)
        options = Card("Opciones")
        self.method = QComboBox()
        for key, label in (("auto", "Automático"), ("heuristic", "Heurístico"), ("cpsat", "CP-SAT")):
            self.method.addItem(label, key)
        self.time_limit = QDoubleSpinBox()
        self.time_limit.setRange(0.1, 600)
        self.time_limit.setValue(30)
        self.time_limit.setSuffix(" s")
        self.group = QComboBox()
        self.group.addItem("Habilidad", "skill")
        self.group.addItem("Recurso", "resource")
        self.period = QComboBox()
        self.period.addItem("Semanal", "week")
        self.period.addItem("Diario", "day")
        options.add(layout=hbox(QLabel("Método"), self.method, QLabel("Límite"), self.time_limit))
        options.add(layout=hbox(QLabel("Nivelar por"), self.group, QLabel("Período"), self.period))
        self.checks = {}
        for key, label in (("respect_resource_limits", "Capacidad como restricción dura"),
                           ("allow_overtime", "Permitir horas extra"),
                           ("assign_workers", "Asignar trabajadores por habilidad"),
                           ("group_outages", "Agrupar desconexiones por instalación")):
            check = QCheckBox(label)
            check.setChecked(True)
            self.checks[key] = check
            options.add(check)
        top.addWidget(options, 1, 0, 1, 2)
        layout.addLayout(top)
        self.role_options = RoleOptions()
        layout.addWidget(self.role_options)
        self.role_options.changed.connect(self._role_mode_changed)
        self.nominal_status = QLabel()
        self.nominal_status.setWordWrap(True)
        self.nominal_model = DictTableModel([
            Column("activity_code", "Actividad"), Column("activity_name", "Nombre"),
            Column("skill", "Rol / habilidad"), Column("resource_name", "Persona"),
            Column("source_assignment_id", "ID demanda", kind="int")])
        self.nominal_table = QTableView()
        self.nominal_table.setModel(self.nominal_model)
        self.nominal_table.setMinimumHeight(160)
        setup_table(self.nominal_table)
        self.nominal_status.hide()
        self.nominal_table.hide()
        self._nominal_ticket = 0
        self.solve_button = ux_button("Generar programa factible", self.generate, primary=True, icon="wand-sparkles")
        self.simulate_button = ux_button("Simular", lambda: self.execute(False), icon="play")
        self.optimize_button = ux_button("Optimizar y aplicar", lambda: self.execute(True), icon="sliders-horizontal")
        self.apply_button = ux_button("Aplicar resultado simulado", self.apply_last, icon="check")
        self.apply_button.setEnabled(False)
        self.toolbar = Toolbar()
        for control in (self.solve_button, self.simulate_button, self.optimize_button, self.apply_button):
            self.toolbar.add(control)
        self.toolbar.add_stretch()
        layout.addWidget(self.toolbar)
        self.status = QLabel("Genera un programa a partir de actividades o tareas; no necesita fechas previas.")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "caption")
        layout.addWidget(self.status)
        self.result_banner = StatusBanner()
        self.result_banner.hide()
        layout.addWidget(self.result_banner)
        self.empty_state = EmptyState("wand-sparkles", "Aún no hay resultados",
            "Elige un objetivo y genera el programa de tus actividades o tareas periódicas.",
            actions=[("Generar programa", self.generate, True)])
        layout.addWidget(self.empty_state)
        layout.addWidget(self.nominal_status)
        layout.addWidget(self.nominal_table)
        self.kpis = {}
        grid = QGridLayout()
        for i, (key, label) in enumerate(METRICS):
            card = KpiCard(label)
            self.kpis[key] = card
            grid.addWidget(card, i // 4, i % 4)
        layout.addLayout(grid)
        self.messages = QTextBrowser()
        self.messages.setMaximumHeight(165)
        layout.addWidget(self.messages)
        self.warning_list = QWidget()
        self.warning_layout = QVBoxLayout(self.warning_list)
        self.warning_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.warning_list)
        self.before_plot, self.after_plot = MplCanvas(), MplCanvas()
        self.before_plot.setMinimumHeight(330)
        self.after_plot.setMinimumHeight(330)
        layout.addLayout(hbox(self.before_plot, self.after_plot))
        history = Section("Historial de corridas")
        self.history_toggle = ux_button("Mostrar historial", icon="chevron-right", kind="ghost")
        self.history_toggle.setCheckable(True)
        history.actions.add(self.history_toggle)
        self.history_panel = QWidget()
        history_body = QVBoxLayout(self.history_panel)
        history_body.setContentsMargins(0, 0, 0, 0)
        self.history_panel.hide()
        self.history_toggle.toggled.connect(self.history_panel.setVisible)
        self.history_toggle.toggled.connect(lambda opened: self.history_toggle.setIcon(icon("chevron-down" if opened else "chevron-right")))
        self.history_apply = button("Aplicar corrida seleccionada", self.apply_selected)
        history_body.addWidget(self.history_apply)
        self.runs_model = DictTableModel([
            Column("id", "#", kind="int"), Column("created_at", "Fecha", get=self._run_date),
            Column("kind", "Tipo", get=lambda r: {"cpm": "CPM", "optimize": "Optimización", "solve": "Generación", "maintenance_level": "Nivelación mantenimiento"}.get(r.get("kind"), r.get("kind", ""))),
            Column("weights", "Pesos", get=lambda r: " · ".join(f"{label}: {fmt_num(parse_object(r.get('weights')).get(key), 2)}" for key, label in OBJECTIVES)),
            Column("finish", "Término", kind="date", get=lambda r: parse_object(r.get("metrics_after")).get("finish")),
            Column("peak", "Pico", kind="float", get=lambda r: parse_object(r.get("metrics_after")).get("peak_load")),
            Column("outside", "Fuera de ventana", kind="int", get=lambda r: parse_object(r.get("metrics_after")).get("activities_out_of_window")),
            Column("status", "Estado", get=lambda r: status_text(r.get("status"))), Column("elapsed_s", "Tiempo (s)", kind="float", dec=1),
            Column("applied", "Aplicado", kind="bool"),
            Column("action", "Acción", get=lambda r: "Aplicar" if r.get("kind") != "cpm" and applicable(r) and not r.get("applied") else "")])
        self.runs_table = QTableView()
        self.runs_table.setModel(self.runs_model)
        self.runs_table.setItemDelegateForColumn(len(self.runs_model.columns) - 1, RunApplyDelegate(self))
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.runs_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.runs_table.setMinimumHeight(170)
        self.runs_table.selectionModel().selectionChanged.connect(self._buttons)
        setup_table(self.runs_table, sortable=False)
        history_body.addWidget(self.runs_table)
        self.history_empty = EmptyState("history", "Aún no hay corridas", "Genera o simula un programa para comparar sus resultados.")
        history_body.addWidget(self.history_empty)
        history.body.addWidget(self.history_panel)
        layout.addWidget(history)
        self._gate.changed.connect(self._buttons)
        state.project_changed.connect(self._buttons)
        self._buttons()
        self.on_theme()

        constrain_actions(body)
        self.root.addLayout(hbox(self.solve_button, self.simulate_button, None))
        self.root.addLayout(hbox(self.optimize_button, self.apply_button, None))

    @staticmethod
    def _run_date(row):
        try:
            return datetime.fromisoformat(row.get("created_at", "")).strftime("%d-%m-%Y %H:%M:%S")
        except (TypeError, ValueError):
            return str(row.get("created_at", ""))

    def set_preset(self, name):
        name = "Minimizar costo" if name == "Minimizar costo/HH extra" else name
        if name not in PRESETS:
            return
        blocked = self.preset.blockSignals(True)
        self.preset.setCurrentText(name)
        self.preset.blockSignals(blocked)
        for (key, _), value in zip(OBJECTIVES, PRESETS[name]):
            self.sliders[key].setValue(value)
        self._weights_changed()

    def weights(self):
        total = sum(s.value() for s in self.sliders.values())
        return {key: slider.value() / total if total else 0 for key, slider in self.sliders.items()}

    def _weights_changed(self, *_):
        weights = self.weights()
        for key, value in weights.items():
            self.percentages[key].setText(f"{fmt_num(100 * value)} %")
        # Un ajuste manual no deja seleccionada una tarjeta cuyos pesos ya cambiaron.
        self.objective_group.setExclusive(False)
        for name, card in self.objective_cards.items():
            total = sum(PRESETS[name])
            expected = [value / total for value in PRESETS[name]]
            card.setChecked(all(abs(weights[key] - value) < 1e-9 for (key, _), value in zip(OBJECTIVES, expected)))
        self.objective_group.setExclusive(True)

    def options(self):
        options = dict(method=self.method.currentData(), time_limit_s=self.time_limit.value(),
                    level_period=self.period.currentData(), group_by=self.group.currentData(),
                    cpsat_max_activities=3000, seed=0,
                    **{key: w.isChecked() for key, w in self.checks.items()})
        options.update(self.role_options.values())
        return options

    def _role_mode_changed(self, active):
        self.objectives_card.setVisible(not active)
        if active and not hasattr(self, "_fractional_controls"):
            self._fractional_controls = (self.method.currentIndex(), self.checks["allow_overtime"].isChecked(),
                                         self.checks["respect_resource_limits"].isChecked(), self.time_limit.value())
            self.method.setCurrentIndex(0)
            self.checks["allow_overtime"].setChecked(False)
            self.checks["respect_resource_limits"].setChecked(True)
        elif not active and hasattr(self, "_fractional_controls"):
            method, overtime, limits, time_limit = self._fractional_controls
            del self._fractional_controls
            self.method.setCurrentIndex(method)
            self.checks["allow_overtime"].setChecked(overtime)
            self.checks["respect_resource_limits"].setChecked(limits)
            self.time_limit.setMaximum(600)
            self.time_limit.setValue(time_limit)
        for card in self.objective_cards.values():
            card.setEnabled(not active)
        self.advanced_toggle.setEnabled(not active)
        self.advanced_panel.setEnabled(not active)
        self.method.setEnabled(not active)
        self.group.setEnabled(not active)
        self.period.setEnabled(not active)
        for key in ("assign_workers", "group_outages"):
            self.checks[key].setVisible(not active and self.state.mode == "mantenimiento")
        for key in ("allow_overtime", "respect_resource_limits"):
            self.checks[key].setEnabled(not active)
        self.time_limit.setMaximum(300 if active else 600)

    def load_nominal(self):
        self._nominal_ticket += 1
        ticket, pid = self._nominal_ticket, self.state.pid
        self.nominal_model.set_rows([])
        self.nominal_status.hide()
        self.nominal_table.hide()
        if not pid or self.state.mode != "proyecto":
            return

        def done(data):
            if ticket != self._nominal_ticket or pid != self.state.pid:
                return
            if not data.get("applied"):
                return
            valid = data.get("valid", False)
            self.nominal_status.setText(
                f"Asignación nominal aplicada · corrida #{data.get('run_id')} · bloques equivalentes"
                if valid else "Asignación nominal obsoleta: el escenario cambió. Vuelva a generar el programa.")
            self.nominal_status.show()
            # Never present stale people as the current assignment.
            self.nominal_model.set_rows(data.get("display_rows", []) if valid else [])
            self.nominal_table.setVisible(valid)
            fit_columns(self.nominal_table)

        def failed(exc):
            if ticket == self._nominal_ticket and pid == self.state.pid:
                if isinstance(exc, ApiError) and exc.status == 404:
                    return
                self.nominal_status.setText(f"No se pudo consultar la asignación nominal: {exc}")
                self.nominal_status.show()
        run_task(lambda: self.state.client.get(f"/projects/{pid}/schedule/role-assignments"), done, failed)

    def refresh(self):
        pid = self.state.pid
        if pid != self._pid:
            self._pid = pid
            self.result = None
            self.result_pid = None
            self._render()
        maintenance = self.state.mode == "mantenimiento"
        self.role_options.set_project_mode(self.state.mode)
        self.checks["assign_workers"].setVisible(maintenance)
        self.checks["group_outages"].setVisible(maintenance)
        self.method.model().item(2).setEnabled(not maintenance)
        if maintenance and self.method.currentData() == "cpsat":
            self.method.setCurrentIndex(0)
        self.load_runs()

    def load_runs(self):
        self.load_nominal()
        pid = self.state.pid
        self._ticket += 1
        ticket = self._ticket
        if not pid:
            self.runs_model.set_rows([])
            self.history_empty.show()
            self.runs_table.hide()
            self._buttons()
            return
        self.loading = True

        def done(rows):
            if ticket == self._ticket and pid == self.state.pid:
                self.loading = False
                self.runs_model.set_rows(rows)
                self.history_empty.setVisible(not rows)
                self.runs_table.setVisible(bool(rows))
                fit_columns(self.runs_table)
                self._buttons()

        def failed(exc):
            if ticket == self._ticket:
                self.loading = False
                self._stale = True
                show_error(exc, self)
        run_task(lambda: self.state.client.runs(pid), done, failed, busy="Cargando corridas…")

    def generate(self):
        self._execute("solve", True)

    def execute(self, apply=False):
        self._execute("optimize", apply)

    def _execute(self, kind, apply):
        if self.running or not self.state.pid:
            return
        weights, options = self.weights(), self.options()
        if options.get("role_mode") != "indivisible" and not any(weights.values()):
            show_error(ValueError("Asigna peso a al menos un objetivo."), self)
            return
        pid, project = self.state.pid, dict(self.state.project)
        if not self._begin(pid):
            return

        def work():
            if kind == "solve":
                # /solve puede devolver solo métricas y verificación, sin perfiles.
                # Capturar el estado anterior antes de cualquier aplicación.
                client = self.state.client
                profile_before = None
                profile_warning = None
                try:
                    profile_before = client.load(pid, group=options["group_by"], period=options["level_period"])
                except ApiError as exc:
                    profile_warning = f"No se pudo consultar la carga anterior: {exc}"
                result = dict(solve_program(client, pid, weights, options, project))
                if not result.get("profile_before"):
                    result["profile_before"] = profile_before
                if not result.get("profile_after") and applicable(result) and result.get("applied") is not False:
                    try:
                        result["profile_after"] = client.load(pid, group=options["group_by"], period=options["level_period"])
                    except ApiError as exc:
                        result["warnings"] = list(result.get("warnings") or []) + [f"No se pudo consultar la carga posterior: {exc}"]
                if profile_warning:
                    result["warnings"] = list(result.get("warnings") or []) + [profile_warning]
                return result
            return self.state.client.optimize(pid, weights, options, apply)

        def done(result):
            if pid != self.state.pid:
                return
            self.result = dict(result)
            self.result_pid = pid
            self.result["applied"] = bool(apply and applicable(result) and result.get("applied") is not False)
            self._render()
            show_infeasible(self, result)
            self.load_runs()
            if self.result["applied"]:
                self.state.bump_schedule()
                notify("Programa aplicado")
            elif not applicable(result):
                notify("No hay un programa aplicable. Revisa el informe y las advertencias.", "error")
            elif apply:
                notify("El servidor no aplicó el programa.", "error")
            self._buttons()
        self._run_operation(work, done, "Generando programa…" if kind == "solve" else "Optimizando programa…")

    def _begin(self, pid):
        key = f"solve:{pid}"
        if self.running or not self._gate.try_begin(key):
            return False
        self._running_key = key
        self.running = True
        self._buttons()
        return True

    def _finish(self):
        key, self._running_key = self._running_key, None
        self.running = False
        if key is not None:
            self._gate.end(key)
        self._buttons()

    def _run_operation(self, work, callback, busy):
        def done(result):
            try:
                callback(result)
            finally:
                self._finish()
        try:
            run_task(work, done, self._failed, busy=busy)
        except Exception:
            self._finish()
            raise

    def _failed(self, exc):
        try:
            show_error(exc, self)
        finally:
            self._finish()

    def _selected_run(self):
        idx = self.runs_table.currentIndex()
        return self.runs_model.row_dict(idx.row()) if idx.isValid() else None

    def _buttons(self, *_):
        self.runs_table.viewport().update()
        busy = self.running or self._gate.is_busy(f"solve:{self.state.pid}")
        for widget in (self.solve_button, self.simulate_button, self.optimize_button):
            widget.setEnabled(not busy and bool(self.state.pid))
        result = self.result or {}
        self.apply_button.setEnabled(not busy and self.result_pid == self.state.pid and
                                     bool(result.get("run_id")) and applicable(result) and not result.get("applied"))
        row = self._selected_run() or {}
        self.history_apply.setEnabled(not busy and row.get("kind") != "cpm" and
                                      applicable(row) and not row.get("applied"))

    def apply_last(self):
        result = self.result or {}
        if self.result_pid == self.state.pid and applicable(result) and not result.get("applied") and result.get("run_id"):
            self.apply_run(result["run_id"])

    def apply_selected(self):
        row = self._selected_run()
        if row and row.get("kind") != "cpm" and applicable(row) and not row.get("applied"):
            self.apply_run(row["id"])

    def apply_run(self, run_id):
        if self.running or not self.state.pid:
            return
        pid = self.state.pid
        if not self._begin(pid):
            return

        def done(response):
            if pid == self.state.pid:
                if isinstance(response, dict) and (response.get("applied") is False or solve_report(response)[0] is False):
                    self.result, self.result_pid = dict(response, applied=False), pid
                    self._render()
                    show_infeasible(self, response)
                    notify("El servidor no aplicó la corrida.", "error")
                else:
                    if self.result and self.result.get("run_id") == run_id:
                        self.result["applied"] = True
                        self._render()
                    self.state.bump_schedule()
                    notify(f"Corrida #{run_id} aplicada")
                self.load_runs()
            self._buttons()
        self._run_operation(lambda: self.state.client.apply_run(pid, run_id), done, "Aplicando corrida…")

    def _render(self):
        result = self.result or {}
        before, after = result.get("before") or {}, result.get("after") or {}
        t = tokens()
        for key, _ in METRICS:
            a, b = before.get(key), after.get(key)
            formatter = fmt_date if key == "finish" else lambda v: fmt_num(v, 1 if key == "load_std" else 0)
            delta = ""
            if a is not None and b is not None:
                try:
                    d = (date.fromisoformat(b) - date.fromisoformat(a)).days if key == "finish" else b - a
                    color = t.good if d < 0 else t.critical if d > 0 else t.text3
                    delta = f' · <span style="color:{color}">{"+" if d > 0 else ""}{fmt_num(d, 1)}{" días" if key == "finish" else ""}</span>'
                except (TypeError, ValueError):
                    pass
            self.kpis[key].set(formatter(b) or "—", f"{formatter(a) or '—'} → {formatter(b) or '—'}{delta}")
        self.result_banner.setVisible(bool(result))
        self.empty_state.setVisible(not result)
        for card in self.kpis.values():
            card.setVisible(bool(result))
        self.before_plot.setVisible(bool(result))
        self.after_plot.setVisible(bool(result))
        if result:
            feasible, violations = solve_report(result)
            if feasible is None and result.get("status") in ("infactible", "infeasible"):
                feasible = False
            first = violations[0] if violations else ""
            if isinstance(first, dict):
                first = first.get("detail") or first.get("message") or str(first)
            banner_kind = "good" if feasible is True else "critical" if feasible is False else "info"
            self.result_banner.setProperty("result_kind", banner_kind)
            self.result_banner.set(banner_kind,
                "Programa factible" if feasible is True else "Programa infactible" if feasible is False else "Factibilidad sin verificar",
                str(first) if first else "Sin violaciones" if feasible is True else
                str((result.get("warnings") or ["Revisa el informe de programación."])[0]))
            self.status.setText(f"Resultado: {status_text(result.get('status'))} · {method_text(result.get('method_used'))} · "
                                f"{fmt_num(result.get('elapsed_s'), 1)} s · "
                                f"{'Aplicado' if result.get('applied') and feasible is not False else 'Sin aplicar'} · "
                                f"Factible: {'Sí' if feasible is True else 'No' if feasible is False else 'Sin verificar'} · "
                                f"Violaciones: {len(violations)} · Advertencias: {len(result.get('warnings') or [])}")
        else:
            self.status.setText("Genera un programa a partir de actividades o tareas; no necesita fechas previas.")
        while self.warning_layout.count():
            item = self.warning_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        warnings = grouped_warnings(result.get("warnings") or [])
        self.warning_list.setVisible(bool(warnings))
        for warning in warnings:
            row = QWidget()
            symbol, message = QLabel(), QLabel(warning)
            symbol.setPixmap(icon("triangle-alert", tokens().warning).pixmap(18, 18))
            message.setTextFormat(Qt.PlainText)
            message.setWordWrap(True)
            message.setProperty("role", "caption")
            row.setLayout(hbox(symbol, message))
            self.warning_layout.addWidget(row)
        lines = []
        report = verification_report(result)
        if report is not None:
            violations = report.get("violations") or []
            explicit = ("violations" in report or isinstance(report.get("feasible"), bool)
                        or isinstance(report.get("is_feasible"), bool))
            feasible = explicit and applicable(result)
            lines.append("<b>Programa factible: 0 violaciones</b>" if feasible else
                         "<b>Verificación: programa no factible</b>" if explicit else "Verificación sin un resultado concluyente.")
            for violation in violations:
                if isinstance(violation, dict):
                    message = str(violation.get("detail") or violation.get("message") or violation)
                    if violation.get("activities"):
                        message += " · Actividades: " + ", ".join(map(str, violation["activities"]))
                    if violation.get("date"):
                        message += " · " + fmt_date(violation["date"])
                else:
                    message = str(violation)
                lines.append(f"<p>{escape(message)}</p>")
        elif result:
            lines.append("El servidor no entregó un informe de verificación de factibilidad.")
        if result and not result.get("run_id") and not result.get("applied"):
            lines.append("No se recibió un identificador de corrida para aplicar este resultado.")
        self.messages.setHtml("".join(lines))
        self.messages.setVisible(bool(lines))
        self._plot(self.before_plot, result.get("profile_before"), "Carga antes de programar")
        self._plot(self.after_plot, result.get("profile_after"), "Carga después de programar")

    def _plot(self, canvas, profile, title):
        canvas.clear()
        ax, t = canvas.ax, tokens()
        labels = (profile or {}).get("labels", [])
        ax.set_title(title)
        if not labels:
            ax.text(0.5, 0.5, "Sin datos de carga", ha="center", va="center", transform=ax.transAxes, color=t.text3)
            canvas.draw()
            return
        n = len(labels)
        series = sorted(profile.get("series", []), key=lambda s: -sum(s["load"]))
        keep = sorted(series[:7], key=lambda s: s["key"])
        if len(series) > 7:
            keep.append({"key": "Otros", "load": [sum(s["load"][i] for s in series[7:]) for i in range(n)]})
        bottom = [0.0] * n
        for k, s in enumerate(keep):
            color = t.text3 if s["key"] == "Otros" else t.series[k % len(t.series)]
            ax.bar(range(n), s["load"], bottom=bottom, label=s["key"], color=color, width=0.85)
            bottom = [a + b for a, b in zip(bottom, s["load"])]
        capacity = [sum(s["capacity"][i] for s in series) for i in range(n)]
        ax.step(range(n), capacity, where="mid", label="Capacidad", color=t.text, linestyle="--")
        ticks = list(range(0, n, max(1, n // 8)))
        ax.set_xticks(ticks, [fmt_date(labels[i]) for i in ticks], rotation=30, ha="right")
        ax.set_ylabel("HH")
        ax.legend(fontsize=7, ncol=2)
        canvas.draw()

    def on_theme(self):
        for card in self.objective_cards.values():
            card.on_theme()
        self._render()
