"""Análisis y alertas: alertas de control (P3-14), pronósticos (P3-04), EVM por grupo (P3-11),
calidad del programa (P3-15) y cumplimiento del plan preventivo (P2-12a)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLabel, QTableView, QTabWidget, QVBoxLayout, QWidget

from ..widgets.common import (Column, DictTableModel, EmptyState, KpiCard, StatusBanner, Toolbar, fit_columns,
                              fmt_date, fmt_num, fmt_pct, hbox, notify, run_task, setup_table, show_error)
from ..widgets.icons import icon
from .base import Page
from ..widgets.responsive import scroll_layout

SEVERITY = {"crítica": "Crítica", "critica": "Crítica", "advertencia": "Advertencia", "info": "Información"}
STATE = {"nueva": "Nueva", "reconocida": "Reconocida", "resuelta": "Resuelta"}
SCENARIOS = {
    "EAC_CPI": "EAC con CPI (desempeño de costo se mantiene)",
    "EAC_presupuesto": "EAC a presupuesto (el remanente se ejecuta según lo planificado)",
    "EAC_combinado": "EAC combinado CPI·SPI (costo y plazo influyen)",
    "EAC_reestimado": "EAC con reestimación del remanente (ETC)",
    "TCPI_EAC_CPI": "TCPI para cumplir EAC con CPI",
    "TCPI_EAC_presupuesto": "TCPI para cumplir EAC a presupuesto",
    "TCPI_EAC_combinado": "TCPI para cumplir EAC combinado",
    "TCPI_EAC_reestimado": "TCPI para cumplir EAC reestimado",
    "IEAC_t": "Duración pronosticada IEAC(t) = PD/SPI(t) (el ritmo actual continúa)",
    "duracion_remanente": "Duración pronosticada AT+(PD−ES) (el remanente se ejecuta según lo planificado)",
}
RULES = {
    "sin_predecesora_ni_sucesora": "Actividades sin predecesora ni sucesora",
    "sin_predecesora": "Actividades sin predecesora", "sin_sucesora": "Actividades sin sucesora",
    "restricciones_duras": "Restricciones duras que reemplazan lógica",
    "desfases_negativos": "Desfases negativos (adelantos)", "desfases_largos": "Desfases largos",
    "duraciones_largas": "Duraciones largas", "holgura_alta": "Holgura total alta",
    "fechas_reales_futuras": "Fechas reales posteriores al corte",
    "actividades_vencidas": "Actividades vencidas sin terminar",
    "hh_sin_recurso": "HH sin habilidad ni recurso", "ciclos": "Ciclos en la red",
    "relaciones_duplicadas": "Relaciones duplicadas",
}


def _table(model, min_height=220) -> QTableView:
    view = QTableView()
    view.setModel(model)
    setup_table(view, sortable=False)
    view.setMinimumHeight(min_height)
    view.setEditTriggers(QTableView.NoEditTriggers)
    return view


def _value(row: dict) -> str:
    value = row.get("value")
    if value is None:
        return "—"
    name = row.get("name", "")
    if name.startswith("TCPI"):
        return fmt_num(value, 2)
    if "PD" in str(row.get("formula", "")):
        return f"{fmt_num(value, 0)} días corridos"
    return fmt_num(value, 1)


class AnalysisPage(Page):
    title = "Análisis y alertas"
    subtitle = "Alertas, pronósticos, EVM por grupo, calidad del programa y cumplimiento preventivo"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._pid = None
        self._request = 0
        self.loading = False
        self.toolbar = Toolbar()
        self.toolbar.add_action("Actualizar", self.refresh, icon="refresh-cw", kind="ghost")
        self.toolbar.add_stretch()
        self.root.addWidget(self.toolbar)
        self.empty = EmptyState("activity", "Abre un proyecto", "Los análisis se calculan sobre el proyecto activo.")
        self.root.addWidget(self.empty)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        self._build_alerts()
        self._build_forecasts()
        self._build_groups()
        self._build_quality()
        self._build_pm()
        # Carga perezosa: cada pestaña se calcula al mostrarse (con 100 000 actividades cada análisis
        # toma varios segundos; no se calculan todos a la vez).
        self._loaded_tabs: set = set()
        self.tabs.currentChanged.connect(self._load_current)

        self.content_scroll = scroll_layout(self.root, first=1)

    # ------------------------------------------------------------------ construcción
    def _tab(self, name, icon_name):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        self.tabs.addTab(widget, icon(icon_name), name)
        return widget, layout

    def _build_alerts(self):
        self.alerts_tab, layout = self._tab("Alertas", "triangle-alert")
        self.alert_banner = StatusBanner()
        layout.addWidget(self.alert_banner)
        self.alert_state = QComboBox()
        for key, label in ((None, "Pendientes (nuevas y reconocidas)"), ("nueva", "Nuevas"), ("reconocida", "Reconocidas"),
                           ("resuelta", "Resueltas"), ("todas", "Todas")):
            self.alert_state.addItem(label, key)
        self.alert_state.setAccessibleName("Estado de alertas")
        bar = Toolbar()
        bar.add(QLabel("Mostrar"))
        bar.add(self.alert_state)
        self.ack_button = bar.add_action("Reconocer", self.acknowledge, icon="check")
        self.reopen_button = bar.add_action("Reabrir", self.reopen, icon="undo-2", kind="ghost")
        bar.add_stretch()
        layout.addWidget(bar)
        self.alert_model = DictTableModel([
            Column("severity", "Severidad", get=lambda r: SEVERITY.get(r.get("severity"), r.get("severity", ""))),
            Column("state", "Estado", get=lambda r: STATE.get(r.get("state"), r.get("state", ""))),
            Column("title", "Alerta"), Column("detail", "Evidencia"),
            Column("first_seen", "Detectada", get=lambda r: fmt_date(r.get("first_seen"))),
        ])
        self.alert_table = _table(self.alert_model, 300)
        layout.addWidget(self.alert_table, 1)
        note = QLabel("Las alertas se recalculan al abrir esta pestaña. Reconocer una alerta la mantiene visible sin "
                      "repetirla; si la condición desaparece pasa a «Resuelta», y si reaparece vuelve a «Nueva». "
                      "Un indicador que no puede calcularse genera una alerta de datos, nunca un «verde».")
        note.setWordWrap(True)
        note.setProperty("role", "caption")
        layout.addWidget(note)
        self.alert_state.currentIndexChanged.connect(self._show_alerts)
        self.alert_table.selectionModel().selectionChanged.connect(self._alert_buttons)
        self._alerts = []
        self._alert_buttons()

    def _build_forecasts(self):
        self.forecast_tab, layout = self._tab("Pronósticos", "trending-up")
        bar = Toolbar()
        bar.add(QLabel("ETC estimado (remanente reestimado)"))
        self.etc = QDoubleSpinBox()
        self.etc.setRange(0, 1e12)
        self.etc.setDecimals(1)
        self.etc.setSpecialValueText("sin informar")
        self.etc.setAccessibleName("ETC estimado")
        bar.add(self.etc)
        self.forecast_basis = QComboBox()
        for key, label in (("hours", "HH"), ("cost", "Costo")):
            self.forecast_basis.addItem(label, key)
        bar.add(self.forecast_basis)
        bar.add_action("Calcular", self.load_forecasts, icon="play")
        bar.add_stretch()
        layout.addWidget(bar)
        self.forecast_model = DictTableModel([
            Column("label", "Método", get=lambda r: SCENARIOS.get(r.get("name"), r.get("name"))),
            Column("formula", "Fórmula"), Column("value_text", "Valor", get=_value, align_right=True),
            Column("finish_date", "Fecha", get=lambda r: fmt_date(r.get("finish_date"))),
            Column("status_text", "Estado", get=lambda r: r.get("status", "") if not r.get("cause") else
                   ("no calculable: ingresa el ETC estimado para calcularlo" if "ETC" in str(r.get("formula", "")) + str(r.get("name", ""))
                    and "Falta" in r["cause"] else f"{r['status']}: {r['cause']}")),
        ])
        self.forecast_table = _table(self.forecast_model, 320)
        layout.addWidget(self.forecast_table, 1)
        note = QLabel("Los escenarios no cambian fechas ni presupuestos. La dispersión entre métodos no es un intervalo de "
                      "confianza: elige el método según la causa de la desviación (puntual, persistente o reestimada).")
        note.setWordWrap(True)
        note.setProperty("role", "caption")
        layout.addWidget(note)

    def _build_groups(self):
        self.group_tab, layout = self._tab("EVM por grupo", "layers")
        bar = Toolbar()
        bar.add(QLabel("Agrupar por"))
        self.group = QComboBox()
        for key, label in (("wbs", "WBS"), ("location", "Instalación"), ("skill", "Habilidad")):
            self.group.addItem(label, key)
        bar.add(self.group)
        self.group_basis = QComboBox()
        for key, label in (("hours", "HH"), ("cost", "Costo"), ("count", "Cantidad")):
            self.group_basis.addItem(label, key)
        bar.add(self.group_basis)
        bar.add_stretch()
        layout.addWidget(bar)

        def ev(key):
            return lambda r: (r.get("evm") or {}).get(key)
        self.group_model = DictTableModel([
            Column("name", "Grupo"), Column("n", "Actividades", get=lambda r: len(r.get("activity_ids", [])), align_right=True),
            Column("BAC", "BAC", get=lambda r: fmt_num(ev("BAC")(r), 0), align_right=True),
            Column("PV", "PV", get=lambda r: fmt_num(ev("PV")(r), 0), align_right=True),
            Column("EV", "EV", get=lambda r: fmt_num(ev("EV")(r), 0), align_right=True),
            Column("AC", "AC", get=lambda r: fmt_num(ev("AC")(r), 0), align_right=True),
            Column("SPI", "SPI", get=lambda r: fmt_num(ev("SPI")(r), 2), align_right=True),
            Column("CPI", "CPI", get=lambda r: fmt_num(ev("CPI")(r), 2), align_right=True),
            Column("pct", "% ganado", get=lambda r: fmt_pct(ev("percent_complete")(r)), align_right=True),
        ])
        self.group_table = _table(self.group_model, 320)
        layout.addWidget(self.group_table, 1)
        self.group_note = QLabel()
        self.group_note.setWordWrap(True)
        self.group_note.setProperty("role", "caption")
        layout.addWidget(self.group_note)
        self.group.currentIndexChanged.connect(self.load_groups)
        self.group_basis.currentIndexChanged.connect(self.load_groups)

    def _build_quality(self):
        self.quality_tab, layout = self._tab("Calidad del programa", "list-checks")
        self.quality_banner = StatusBanner()
        layout.addWidget(self.quality_banner)
        self.quality_model = DictTableModel([
            Column("severity", "Severidad", get=lambda r: SEVERITY.get(r.get("severity"), r.get("severity", ""))),
            Column("rule", "Regla", get=lambda r: RULES.get(r.get("rule"), r.get("rule", ""))),
            Column("ratio", "Hallazgos", get=lambda r: f"{fmt_num(r.get('count'))} de {fmt_num(r.get('population'))}", align_right=True),
            Column("examples", "Ejemplos", get=lambda r: ", ".join(a["code"] for a in r.get("activities", [])[:8])
                   + (" …" if len(r.get("activities", [])) > 8 else "")),
            Column("action", "Acción sugerida"),
        ])
        self.quality_table = _table(self.quality_model, 320)
        self.quality_table.doubleClicked.connect(self._open_quality_example)
        layout.addWidget(self.quality_table, 1)
        note = QLabel("Diagnóstico explicable de reglas de buena práctica (no es una certificación DCMA). "
                      "Doble clic en una fila: abre el Gantt filtrado por el primer código de ejemplo.")
        note.setWordWrap(True)
        note.setProperty("role", "caption")
        layout.addWidget(note)
        self._quality = None

    def _build_pm(self):
        self.pm_tab, layout = self._tab("Cumplimiento preventivo", "wrench")
        self.pm_kpis = {key: KpiCard(label) for key, label in (
            ("on_time", "Cumplimiento a tiempo"), ("total", "Cumplimiento total"),
            ("due", "Vencidas al corte"), ("backlog", "Backlog (OT · HH)"))}
        layout.addLayout(hbox(*self.pm_kpis.values()))
        self.age_model = DictTableModel([
            Column("key", "Antigüedad"), Column("count", "OT", align_right=True),
            Column("hh", "HH remanentes", get=lambda r: fmt_num(r.get("remaining_hours"), 1), align_right=True)])
        self.location_model = DictTableModel([
            Column("key", "Instalación"), Column("due", "Vencidas", align_right=True),
            Column("on_time", "A tiempo", get=lambda r: fmt_pct(r.get("on_time_pct")), align_right=True),
            Column("total", "Total", get=lambda r: fmt_pct(r.get("total_pct")), align_right=True)])
        self.oldest_model = DictTableModel([
            Column("code", "Código"), Column("name", "Actividad"), Column("location", "Instalación"),
            Column("due", "Vencimiento", get=lambda r: fmt_date(r.get("due_date"))),
            Column("age_days", "Días de atraso", align_right=True),
            Column("hh", "HH remanentes", get=lambda r: fmt_num(r.get("remaining_hours"), 1), align_right=True)])
        self.age_table, self.location_table = _table(self.age_model, 160), _table(self.location_model, 160)
        self.oldest_table = _table(self.oldest_model, 220)
        layout.addLayout(hbox(self.age_table, self.location_table))
        layout.addWidget(QLabel("Las 20 más antiguas del backlog"))
        layout.addWidget(self.oldest_table, 1)
        note = QLabel("Denominador: el plan comprometido (ocurrencias vencidas al corte), incluidas las no ejecutadas. "
                      "Sin ocurrencias vencidas el indicador queda «sin datos», nunca 0 % ni 100 % inventados.")
        note.setWordWrap(True)
        note.setProperty("role", "caption")
        layout.addWidget(note)

    # ------------------------------------------------------------------ carga
    def refresh(self, *_):
        pid = self.state.pid
        self.empty.setVisible(pid is None)
        self.tabs.setVisible(pid is not None)
        self.tabs.setTabVisible(self.tabs.indexOf(self.pm_tab), self.state.mode == "mantenimiento")
        if pid != self._pid:
            self._pid = pid
            for model in (self.alert_model, self.forecast_model, self.group_model, self.quality_model,
                          self.age_model, self.location_model, self.oldest_model):
                model.set_rows([])
        if pid is None:
            return
        self._loaded_tabs = set()
        self._load_current()

    def _load_current(self, *_):
        if self.state.pid is None:
            return
        widget = self.tabs.currentWidget()
        if widget in self._loaded_tabs:
            return
        self._loaded_tabs.add(widget)
        loader = {self.alerts_tab: self.load_alerts, self.forecast_tab: self.load_forecasts,
                  self.group_tab: self.load_groups, self.quality_tab: self.load_quality,
                  self.pm_tab: self.load_pm}.get(widget)
        if loader is not None and (widget is not self.pm_tab or self.state.mode == "mantenimiento"):
            loader()

    def _call(self, fn, done, busy=None, channel=None):
        """A4-14: cada análisis (canal) descarta respuestas de solicitudes anteriores del mismo canal."""
        channel = channel or getattr(done, "__name__", repr(done))
        generations = self.__dict__.setdefault("_generations", {})
        generation = generations[channel] = generations.get(channel, 0) + 1
        pid = self.state.pid

        def current():
            return pid == self.state.pid and generations.get(channel) == generation

        def deliver(result):
            if current():
                done(result)

        def failed(exc):
            if current():
                show_error(exc, self)
        run_task(fn, deliver, failed, busy=busy)

    def load_alerts(self):
        client, pid = self.state.client, self.state.pid
        self._call(lambda: client.get(f"/projects/{pid}/alerts"), self._alerts_loaded, busy="Evaluando alertas…")

    def _alerts_loaded(self, rows):
        self._alerts = rows or []
        pending = [r for r in self._alerts if r.get("state") != "resuelta"]
        critical = sum(r.get("severity") in ("crítica", "critica") for r in pending)
        if not pending:
            self.alert_banner.set("good", "Sin alertas pendientes", "Las reglas evaluadas no detectan desviaciones al corte.")
        else:
            self.alert_banner.set("critical" if critical else "warning", f"{len(pending)} alertas pendientes",
                                  f"{critical} críticas · {sum(r.get('state') == 'nueva' for r in pending)} nuevas")
        self._show_alerts()

    def _show_alerts(self, *_):
        state = self.alert_state.currentData()
        rows = [r for r in self._alerts if (state == "todas") or (state is None and r.get("state") != "resuelta")
                or r.get("state") == state]
        self.alert_model.set_rows(rows)
        fit_columns(self.alert_table, max_w=520)
        self._alert_buttons()

    def _selected_alert(self):
        index = self.alert_table.currentIndex()
        return self.alert_model.rows[index.row()] if index.isValid() and index.row() < len(self.alert_model.rows) else None

    def _alert_buttons(self, *_):
        row = self._selected_alert()
        self.ack_button.setEnabled(bool(row) and row.get("state") == "nueva")
        self.reopen_button.setEnabled(bool(row) and row.get("state") == "reconocida")

    def _set_alert(self, action):
        row = self._selected_alert()
        if not row:
            return
        client = self.state.client
        self._call(lambda: client.post(f"/alerts/{row['id']}/{action}"), lambda _: self.load_alerts())

    def acknowledge(self):
        self._set_alert("acknowledge")

    def reopen(self):
        self._set_alert("reopen")

    def load_forecasts(self):
        client, pid = self.state.client, self.state.pid
        if pid is None:
            return
        params = {"basis": self.forecast_basis.currentData()}
        if self.etc.value() > 0:
            params["etc"] = str(self.etc.value())
        self._call(lambda: client.get(f"/projects/{pid}/control/forecasts", params=params), self._forecasts_loaded)

    def _forecasts_loaded(self, rows):
        self.forecast_model.set_rows(rows or [])
        fit_columns(self.forecast_table, max_w=420)

    def load_groups(self, *_):
        client, pid = self.state.client, self.state.pid
        if pid is None:
            return
        params = {"group": self.group.currentData(), "basis": self.group_basis.currentData()}
        self._call(lambda: client.get(f"/projects/{pid}/control/evm-by", params=params), self._groups_loaded)

    def _groups_loaded(self, result):
        result = result or {}
        rows = list(result.get("groups", []))
        total = result.get("total")
        if total:
            rows.append({"name": "Total del proyecto", "activity_ids": sum((g.get("activity_ids", []) for g in rows), []), "evm": total})
        self.group_model.set_rows(rows)
        fit_columns(self.group_table, max_w=320)
        rec = result.get("reconciliation") or {}
        self.group_note.setText(("Conciliación correcta: la suma de los grupos coincide con el total." if rec.get("matches")
                                 else f"Atención: la suma de grupos difiere del total ({rec.get('differences')}).")
                                + " Los índices de cada grupo se recalculan con sus PV/EV/AC (no se promedian).")

    def load_quality(self):
        client, pid = self.state.client, self.state.pid
        self._call(lambda: client.get(f"/projects/{pid}/control/quality"), self._quality_loaded, busy="Diagnosticando el programa…")

    def _quality_loaded(self, result):
        self._quality = result or {}
        findings = self._quality.get("findings", [])
        critical = sum(f.get("severity") in ("crítica", "critica") for f in findings)
        if not findings:
            self.quality_banner.set("good", "Sin hallazgos", "El programa cumple las reglas evaluadas.")
        else:
            self.quality_banner.set("critical" if critical else "warning", f"{len(findings)} reglas con hallazgos",
                                    f"{critical} críticas. Revisa antes de congelar una línea base o enviar el programa.")
        self.quality_model.set_rows(findings)
        fit_columns(self.quality_table, max_w=420)

    def _open_quality_example(self, index):
        row = self.quality_model.rows[index.row()] if index.isValid() else None
        codes = [a["code"] for a in (row or {}).get("activities", [])]
        if not codes:
            return
        window = self.window()
        if hasattr(window, "go"):
            window.go("gantt")
            page = window.page("gantt") if hasattr(window, "page") else None
            if page is not None and hasattr(page, "search"):
                page.search.setText(codes[0])
                notify(f"Gantt filtrado por {codes[0]} ({len(codes)} actividades en esta regla)", "info")

    def load_pm(self):
        client, pid = self.state.client, self.state.pid
        self._call(lambda: client.get(f"/projects/{pid}/maintenance/kpis"), self._pm_loaded)

    def _pm_loaded(self, result):
        result = result or {}
        comp, backlog = result.get("compliance") or {}, result.get("backlog") or {}
        no_data = comp.get("status") == "sin datos"
        self.pm_kpis["on_time"].set("sin datos" if comp.get("on_time_pct") is None else fmt_pct(comp["on_time_pct"]),
                                    f"{fmt_num(comp.get('completed_on_time'))} de {fmt_num(comp.get('due'))}")
        self.pm_kpis["total"].set("sin datos" if comp.get("total_pct") is None else fmt_pct(comp["total_pct"]),
                                  f"{fmt_num(comp.get('completed'))} terminadas")
        self.pm_kpis["due"].set(fmt_num(comp.get("due")), f"corte {fmt_date(result.get('data_date'))}")
        self.pm_kpis["backlog"].set("sin datos" if no_data else f"{fmt_num(backlog.get('count'))} · {fmt_num(backlog.get('remaining_hours'), 0)}",
                                    "OT vencidas sin terminar")
        self.age_model.set_rows(backlog.get("by_age", []))
        self.location_model.set_rows(result.get("by_location", []))
        self.oldest_model.set_rows(backlog.get("oldest", []))
        for table in (self.age_table, self.location_table, self.oldest_table):
            fit_columns(table, max_w=380)
