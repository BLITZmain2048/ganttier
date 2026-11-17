"""Plan maestro, generación y lectura de carga de mantenimiento."""
from __future__ import annotations

from dataclasses import asdict
from math import ceil, isfinite

from PySide6.QtCore import Qt, QRect, QDate, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QScrollArea, QTableView, QTabWidget, QWidget,
)

from ganttier.domain import PeriodicTask
from ..client import ApiError
from ..theme import tokens
from ..widgets.common import (
    Card, Column, DictTableModel, Field, FormDialog, KpiCard, button, confirm,
    fmt_date, fmt_num, hbox, iso, notify, qdate, run_task, save_bytes_dialog, show_error, history_warning,
)
from .base import Page
from ..widgets.responsive import scroll_layout
from .activities import (Toolbar, SearchBox, EmptyState, ux_button, setup_table, fit_columns,
                         paste_from_clipboard, run_task_list_wizard, import_template, toast)
from ..widgets.icons import icon


FREQUENCIES = [("D", "Diaria (días corridos)"), ("DH", "Diaria hábil"), ("W", "Semanal"), ("M", "Mensual"),
               ("Q", "Trimestral"), ("S", "Semestral"), ("A", "Anual"),
               ("ND", "Cada N días"), ("NW", "Cada N semanas"),
               ("NM", "Cada N meses"), ("NA", "Cada N años")]
MONTHS = "Ene Feb Mar Abr May Jun Jul Ago Sep Oct Nov Dic".split()


def quantity_values(values):
    """Los campos vacíos desactivan el reparto; cero nunca se convierte en ausencia."""
    result = dict(values)
    for key in ("quantity_total", "batch_size", "productivity_day_team", "productivity_hour_team", "productive_hours_day"):
        value = result.get(key)
        result[key] = None if value in (None, "") else float(str(value).replace(",", "."))
    return result


def annual_occurrences(task):
    frequency = task.get("frequency", "M")
    if frequency == "DH":
        return None  # Depends on actual calendar, exceptions and horizon.
    count = {"D": 365, "W": 52, "M": 12, "Q": 4, "S": 2, "A": 1}.get(frequency.lstrip("N"), 0)
    return count / max(1, task.get("interval", 1)) if frequency.startswith("N") else count


class WeeklyHeatmap(QWidget):
    """Lienzo desplazable; pinta solamente las celdas del rectángulo expuesto."""
    LEFT, TOP, CELL, ROW = 170, 30, 26, 28

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = {"labels": [], "series": []}
        self.setMouseTracking(True)
        self.set_profile(self.profile)

    def set_profile(self, profile):
        self.profile = profile or {"labels": [], "series": []}
        self.setMinimumSize(max(500, self.LEFT + len(self.profile["labels"]) * self.CELL),
                            max(100, self.TOP + len(self.profile["series"]) * self.ROW))
        self.update()

    @staticmethod
    def cell_color(load, capacity):
        t = tokens()
        if load <= 0:
            return QColor(t.surface2)
        if capacity <= 0 or load / capacity > 1.0001:
            return QColor(t.critical)
        low, high = QColor(t.accent_soft), QColor(t.accent)
        u = max(0, min(1, load / capacity))
        return QColor(*(round(a + (b - a) * u) for a, b in zip(low.getRgb()[:3], high.getRgb()[:3])))

    def cell_tooltip(self, row, col):
        s = self.profile["series"][row]
        load, cap = s["load"][col], s["capacity"][col]
        use = f"{fmt_num(load / cap * 100)} %" if cap > 0 else "sin capacidad disponible"
        return (f"{s['key']} · semana del {fmt_date(self.profile['labels'][col])}: "
                f"{fmt_num(load, 1)} HH de {fmt_num(cap, 1)} disponibles ({use})")

    def mouseMoveEvent(self, event):
        p = event.position()
        col, row = int((p.x() - self.LEFT) // self.CELL), int((p.y() - self.TOP) // self.ROW)
        self.setToolTip(self.cell_tooltip(row, col) if
                        0 <= col < len(self.profile["labels"]) and 0 <= row < len(self.profile["series"]) else "")
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        t = tokens()
        p = QPainter(self)
        p.fillRect(event.rect(), QColor(t.surface))
        p.setPen(QColor(t.text2))
        labels, series = self.profile["labels"], self.profile["series"]
        if not labels or not series:
            p.drawText(self.rect(), Qt.AlignCenter, "Sin carga. Genera el programa del plan.")
            p.end()
            return
        first = max(0, (event.rect().left() - self.LEFT) // self.CELL)
        last = min(len(labels), (event.rect().right() - self.LEFT) // self.CELL + 1)
        for c in range(first, last):
            if c == 0 or labels[c][:7] != labels[c - 1][:7]:
                d = labels[c]
                p.drawText(QRect(self.LEFT + c * self.CELL, 0, 100, self.TOP),
                           Qt.AlignVCenter, f"{MONTHS[int(d[5:7]) - 1]} {d[:4]}")
        for r in range(max(0, (event.rect().top() - self.TOP) // self.ROW),
                       min(len(series), (event.rect().bottom() - self.TOP) // self.ROW + 1)):
            s = series[r]
            y = self.TOP + r * self.ROW
            label = p.fontMetrics().elidedText(s["key"] or "Sin habilidad", Qt.ElideRight, self.LEFT - 10)
            p.drawText(QRect(4, y, self.LEFT - 8, self.ROW), Qt.AlignVCenter, label)
            for c in range(first, last):
                p.fillRect(QRect(self.LEFT + c * self.CELL + 1, y + 2, self.CELL - 2, self.ROW - 4),
                           self.cell_color(s["load"][c], s["capacity"][c]))
        p.end()


def table_for(model):
    table = QTableView()
    table.setModel(model)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    setup_table(table, sortable=False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    fit_columns(table)
    return table


class MaintenancePage(Page):
    title = "Plan de mantenimiento"
    subtitle = "Organiza tareas periódicas y genera sus ocurrencias dentro del horizonte del plan."
    PAGE_SIZE = 200

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.tasks, self.columnar = [], {}
        self.loading = self.saving = self.generating = False
        self._request = 0
        self._pid = None
        self.data_pid = None
        self._search_texts = []
        self._publishing = False
        self.task_page = 0
        self.last_result = None
        self.kpis = {key: KpiCard(label) for key, label in (
            ("tasks", "Tareas"), ("active", "Activas"), ("occurrences", "Ocurrencias"),
            ("hours", "HH anuales estimadas"), ("outages", "Con desconexión"))}
        self.root.addLayout(hbox(*self.kpis.values()))
        generation = Card("Generar plan")
        self.start, self.end = QDateEdit(), QDateEdit()
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd-MM-yyyy")
            edit.setDate(QDate.currentDate())
            edit.setFixedWidth(132)
        self.replace = QCheckBox("Reemplazar ocurrencias y sus avances")
        self.replace.setChecked(False)
        self.replace.setToolTip("Sin marcar: completa ocurrencias faltantes y conserva las existentes. Marcada: solicita reemplazar ocurrencias y avances.")
        self.generate_button = ux_button("Generar programa del plan", self.generate, primary=True, icon="wand-sparkles")
        generation.add(layout=hbox(QLabel("Desde"), self.start, QLabel("Hasta"), self.end, None))
        self.replace_hint = QLabel("Sin marcar, conserva las ocurrencias y sus avances. Al marcar, se reemplazan ambos.")
        self.replace_hint.setProperty("role", "caption")
        self.replace_hint.setWordWrap(True)
        generation.add(layout=hbox(self.replace, self.replace_hint, None))
        generation.add(layout=hbox(self.generate_button, ux_button("Programa de desconexiones…", self.download_outages, icon="file-down"), None))
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        generation.add(self.summary)
        self.root.addWidget(generation)
        tabs = self.tabs = QTabWidget()
        self.root.addWidget(tabs, 1)
        master = Card("Tareas periódicas")
        self.search = SearchBox()
        self.search.setPlaceholderText("Buscar código, nombre, equipo o habilidad…")
        self.location = QComboBox()
        self.location.addItem("Todas las instalaciones", "")
        self.outage_only = QCheckBox("Solo con desconexión")
        self.toolbar = Toolbar()
        self.new_button = self.toolbar.add_action("Tarea", self.new_task, icon="plus", primary=True)
        self.edit_button = self.toolbar.add_action("Editar", self.edit_task, icon="pencil")
        self.delete_button = self.toolbar.add_action("Eliminar", self.delete_task, icon="trash-2", danger=True, kind="ghost")
        self.paste_button = self.toolbar.add_action("Pegar desde Excel", self.paste_tasks, icon="clipboard-paste")
        self.teams_button = self.toolbar.add_action("Equipos y reparto", self.manage_teams, icon="users")
        self.routes_button = self.toolbar.add_action("Proponer rutas", self.propose_routes, icon="list-todo")
        self.toolbar.add_action("Actualizar", self.refresh, icon="refresh-cw", kind="ghost")
        master.add(self.toolbar)
        master.add(layout=hbox(self.search, self.location, self.outage_only))
        self.empty_state = EmptyState("list-todo", "Aún no hay tareas periódicas",
            "Crea el plan maestro antes de generar sus ocurrencias.", actions=[
                ("Asistente lista de tareas", self.task_wizard, True),
                ("Pegar desde Excel", self.paste_tasks, False),
                ("Importar plantilla", import_template, False)])
        self.empty_state.hide()
        master.add(self.empty_state, stretch=1)
        self.model = DictTableModel([
            Column("code", "Código"), Column("name", "Nombre"),
            Column("frequency", "Frecuencia", get=lambda t: dict(FREQUENCIES).get(t["frequency"], "").replace("N", str(t["interval"]))),
            Column("duration_days", "Días", kind="int"), Column("work_hours", "HH", kind="float", dec=1),
            Column("crew_size", "Dotación", kind="float", dec=1), Column("required_skill", "Habilidad"),
            Column("location", "Instalación"), Column("equipment", "Equipo"), Column("voltage_kv", "kV", kind="float", dec=1),
            Column("requires_outage", "Desconexión", kind="bool"),
            Column("tolerance", "Tolerancia", get=lambda t: f"−{t['tol_before_days']}/+{t['tol_after_days']} d" + (" (dura)" if t['window_hard'] else "")),
            Column("preferred_months", "Meses preferidos"), Column("annual", "Ocurr./año", kind="float", dec=1, get=annual_occurrences),
            Column("active", "Activa", kind="bool"),
        ])
        self.table = table_for(self.model)
        self.table.setColumnWidth(1, 280)
        self.table.doubleClicked.connect(lambda *_: self.edit_task())
        master.add(self.table, stretch=1)
        self.previous = button("Anterior", lambda: self.change_page(-1))
        self.next = button("Siguiente", lambda: self.change_page(1))
        self.page_label = QLabel()
        master.add(layout=hbox(None, self.previous, self.page_label, self.next))
        tabs.addTab(master, icon("list-todo"), "Plan maestro")
        heat = Card("Carga semanal por habilidad · utilización = carga / capacidad")
        self.heatmap = WeeklyHeatmap()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.heatmap)
        heat.add(scroll, stretch=1)
        heat_hint = QLabel("Intensidad: utilización de 0–100 % · Alerta: sobre capacidad o carga sin capacidad")
        heat_hint.setProperty("role", "caption")
        heat.add(heat_hint)
        tabs.addTab(heat, icon("layers"), "Mapa de calor")
        calendar = Card("Ocurrencias por instalación y mes · ⚡ con desconexión")
        self.calendar_model = DictTableModel([])
        self.calendar_table = table_for(self.calendar_model)
        calendar.add(self.calendar_table, stretch=1)
        tabs.addTab(calendar, icon("calendar-days"), "Calendario")
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self._filter_search)
        self.search.textChanged.connect(self._search_changed)
        self.location.currentIndexChanged.connect(self.filter_changed)
        self.outage_only.toggled.connect(self.filter_changed)
        self.model.data_pid = self.calendar_model.data_pid = None
        operations_changed = getattr(state, "operations_changed", None)
        if operations_changed is not None:
            operations_changed.connect(self._actions)
        self._actions()
        self.on_theme()

        self.content_scroll = scroll_layout(self.root)
        self.root.addLayout(hbox(self.generate_button, self.new_button, None))

    def _actions(self):
        ready = (self.data_pid == self.state.pid and bool(self.state.pid)
                 and not self.loading and not self.saving and not self.generating)
        for widget in (self.new_button, self.edit_button, self.delete_button, self.paste_button, self.teams_button, self.routes_button, self.table):
            widget.setEnabled(ready)
        busy = getattr(self.state, "is_busy", None)
        self.generate_button.setEnabled(ready and not (callable(busy) and busy(f"solve:{self.state.pid}")))

    def _can_mutate(self):
        if self.data_pid != self.state.pid or self.model.data_pid != self.state.pid:
            notify("Los datos pertenecen a otro proyecto. Usa Actualizar.", "info")
            return False
        return bool(self.state.pid) and not self.loading and not self.saving and not self.generating

    def _sync_project(self):
        if self._pid == self.state.pid:
            return
        self._pid = self.state.pid
        self._request += 1
        self.search_timer.stop()
        self.data_pid = self.model.data_pid = self.calendar_model.data_pid = None
        self.tasks, self.columnar, self._search_texts = [], {}, []
        self.loading = True
        self.task_page = 0
        self.table.clearSelection()
        self.calendar_table.clearSelection()
        self.model.set_rows([])
        self.calendar_model.set_rows([])
        for widget in (self.search, self.location, self.outage_only, self.replace):
            previous = widget.blockSignals(True)
            if widget is self.search:
                widget.clear()
            elif widget is self.location:
                widget.clear()
                widget.addItem("Todas las instalaciones", "")
            else:
                widget.setChecked(False)
            widget.blockSignals(previous)
        project = self.state.project or {}
        self.start.setDate(qdate(project.get("start_date")))
        self.end.setDate(qdate(project.get("end_date")))
        self.summary.clear()
        self.last_result = None
        self.heatmap.set_profile(None)
        self.render_tasks()
        self._actions()

    def _mark_stale(self):
        self._sync_project()
        if not self._publishing:
            super()._mark_stale()

    def _search_changed(self, *_):
        # setText() reinicia isModified; los filtros programáticos son inmediatos.
        if self.search.isModified():
            self.search_timer.start()
        else:
            self._filter_search()

    def _filter_search(self):
        self.search_timer.stop()
        self.task_page = 0
        self.render_tasks()

    def refresh(self, *, after_write=False):
        self._sync_project()
        self._request += 1
        request, pid = self._request, self.state.pid
        if not pid or self.state.mode != "mantenimiento":
            self.tasks, self.columnar = [], {}
            self.loading = False
            self.data_pid = self.model.data_pid = self.calendar_model.data_pid = None
            self.setEnabled(False)
            self.filter_changed()
            self.heatmap.set_profile(None)
            return
        self.setEnabled(True)
        self.loading = True
        self._actions()
        client = self.state.client

        def work():
            return client.periodic_tasks(pid), client.columnar(pid), client.load(pid, "skill", "week")

        def done(data):
            if request != self._request or pid != self.state.pid:
                return
            self.loading = False
            self.tasks, self.columnar, profile = data
            self.data_pid = self.model.data_pid = self.calendar_model.data_pid = pid
            self._search_texts = [" ".join(str(t.get(k) or "") for k in
                                  ("code", "name", "location", "equipment", "required_skill")).casefold()
                                  for t in self.tasks]
            current = self.location.currentData()
            self.location.blockSignals(True)
            self.location.clear()
            self.location.addItem("Todas las instalaciones", "")
            for loc in sorted({t["location"] for t in self.tasks if t.get("location")}):
                self.location.addItem(loc, loc)
            self.location.setCurrentIndex(max(0, self.location.findData(current)))
            self.location.blockSignals(False)
            self.filter_changed()
            self.heatmap.set_profile(profile)
            self._actions()
            self._apply_focus()

        def failed(exc):
            if request == self._request and pid == self.state.pid:
                self.loading = False
                self._stale = True
                self._actions()
                if after_write:
                    notify("Guardado; no se pudo actualizar la vista. Usa Actualizar.", "info")
                else:
                    show_error(exc, self)
        run_task(work, done, failed, busy="Cargando plan de mantenimiento…")

    def focus_task(self, tid: int, code: str = "") -> None:
        """P5-06: muestra y selecciona una tarea periódica elegida en la búsqueda global."""
        self._pending_focus = (int(tid), code)
        if self.data_pid == self.state.pid and not self.loading:
            self._apply_focus()

    def _apply_focus(self) -> None:
        pending = getattr(self, "_pending_focus", None)
        if pending is None or self.data_pid != self.state.pid:
            return
        self._pending_focus = None
        tid, code = pending
        blocked = self.outage_only.blockSignals(True)
        self.outage_only.setChecked(False)
        self.outage_only.blockSignals(blocked)
        self.location.blockSignals(True)
        self.location.setCurrentIndex(0)
        self.location.blockSignals(False)
        blocked = self.search.blockSignals(True)
        self.search.setText(code)
        self.search.blockSignals(blocked)
        self.filter_changed()
        row = next((r for r, task in enumerate(self.model.rows) if task.get("id") == tid), None)
        if row is None:
            notify("La tarea ya no existe en este plan.", "info")
            return
        self.table.selectRow(row)
        self.table.scrollTo(self.model.index(row, 0))

    def filter_changed(self, *_):
        self.search_timer.stop()
        self.task_page = 0
        self.render_tasks()
        self.render_calendar()

    def render_tasks(self):
        query, location = self.search.text().casefold().strip(), self.location.currentData()
        rows = [t for t, text in zip(self.tasks, self._search_texts) if
                (not query or query in text)
                and (not location or t.get("location") == location)
                and (not self.outage_only.isChecked() or t.get("requires_outage"))]
        pages = max(1, ceil(len(rows) / self.PAGE_SIZE))
        self.task_page = max(0, min(self.task_page, pages - 1))
        self.model.set_rows(rows[self.task_page * self.PAGE_SIZE:(self.task_page + 1) * self.PAGE_SIZE])
        empty = not self.tasks and not self.loading and self.data_pid == self.state.pid
        self.empty_state.setVisible(empty)
        self.table.setVisible(not empty)
        fit_columns(self.table)
        self.page_label.setText(f"Página {self.task_page + 1} de {pages} · {fmt_num(len(rows))} tareas")
        self.previous.setEnabled(self.task_page > 0)
        self.next.setEnabled(self.task_page < pages - 1)
        calendar_dependent = any(t["active"] and t.get("frequency") == "DH" for t in rows)
        for key, value in (("tasks", len(self.tasks)), ("active", sum(bool(t["active"]) for t in self.tasks)),
                           ("occurrences", sum(k == "mantenimiento" for k in self.columnar.get("kinds", []))),
                           ("hours", None if calendar_dependent else sum(annual_occurrences(t) * t["work_hours"] for t in rows if t["active"])),
                           ("outages", sum(bool(t["requires_outage"]) for t in self.tasks))):
            self.kpis[key].set(fmt_num(value), ("DH: genere ocurrencias para obtener HH del calendario" if calendar_dependent else "Según filtro") if key == "hours" else "")

    def change_page(self, delta):
        self.task_page += delta
        self.render_tasks()

    def render_calendar(self):
        data = self.columnar
        counts, outages, months = {}, {}, set()
        loc_filter = self.location.currentData()
        for start, loc, outage, kind in zip(data.get("starts", []), data.get("locations", []),
                                          data.get("outages", []), data.get("kinds", [])):
            if not start or kind != "mantenimiento" or (loc_filter and loc != loc_filter) or (self.outage_only.isChecked() and not outage):
                continue
            month, loc = start[:7], loc or "(sin instalación)"
            months.add(month)
            counts.setdefault(loc, {})[month] = counts.get(loc, {}).get(month, 0) + 1
            outages.setdefault(loc, {})[month] = outages.get(loc, {}).get(month, 0) + bool(outage)
        ordered = []
        if months:
            current, end = min(months), max(months)
            while current <= end:
                ordered.append(current)
                year, month = map(int, current.split("-"))
                current = f"{year + (month == 12):04d}-{month % 12 + 1:02d}"
        cols = [Column("location", "Instalación")]
        cols += [Column(m, f"{MONTHS[int(m[5:7]) - 1]} {m[:4]}") for m in ordered]
        cols += [Column("total", "Total", kind="int")]
        rows = []
        for loc in sorted(counts):
            row = {"location": loc, "total": sum(counts[loc].values())}
            for m in ordered:
                n, o = counts[loc].get(m, 0), outages[loc].get(m, 0)
                row[m] = (fmt_num(n) + (f" ⚡{fmt_num(o)}" if o else "")) if n else ""
            rows.append(row)
        self.calendar_model.beginResetModel()
        self.calendar_model.columns, self.calendar_model.rows = cols, rows
        self.calendar_model.endResetModel()
        fit_columns(self.calendar_table)

    def manage_teams(self):
        if self._can_mutate():
            from ..teams_dialog import TeamsDialog
            TeamsDialog(self.state, self).exec()

    def propose_routes(self):
        if not self._can_mutate():
            return
        pid = self.state.pid
        def preview(rows):
            if pid != self.state.pid or not self._can_mutate():
                return
            detail = '\n'.join(f"{r['code']}: {r['name']} ({len(r['checklist_codes'])} ítems)" for r in rows[:30])
            if not rows:
                notify("No hay ítems activos sin ruta.", "info")
                return
            if confirm(self, "Propuesta de rutas", f"{len(rows)} rutas propuestas. Podrá editar sus parámetros en Plan maestro o en la hoja Rutas de Excel antes de generar.\n\n{detail}\n\n¿Crear estas rutas?"):
                run_task(lambda: self.state.client.post(f'/projects/{pid}/routes/propose', {'apply':True}),
                         lambda _: self._reload_written(), busy="Creando rutas…")
        run_task(lambda: self.state.client.post(f'/projects/{pid}/routes/propose', {'apply':False}),
                 preview, busy="Preparando propuesta de rutas…")

    def _fields(self):
        return [
            Field("code", "Código"), Field("name", "Nombre"),
            Field("frequency", "Frecuencia", "choice", FREQUENCIES), Field("interval", "Intervalo N", "int", minimum=1),
            Field("duration_days", "Duración (días)", "int", minimum=1), Field("work_hours", "HH por ejecución", "float"),
            Field("crew_size", "Dotación", "float"), Field("required_skill", "Habilidad requerida"),
            Field("resource_id", "Trabajador fijo", "choice", [(None, "Asignación automática")] + [(r["id"], r["name"]) for r in self.state.resources]),
            Field("first_due", "Primera fecha", "date"), Field("horizon_end", "No generar después de", "date"),
            Field("preferred_months", "Meses preferidos (1–12 separados por coma)"),
            Field("preferred_weekday", "Día de semana preferido", "choice", [(None, "Cualquiera")] + list(enumerate(["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]))),
            Field("day_of_month", "Día del mes", "choice", [(None, "Cualquiera")] + [(i, str(i)) for i in range(1, 32)]),
            Field("tol_before_days", "Tolerancia antes (días)", "int"), Field("tol_after_days", "Tolerancia después (días)", "int"),
            Field("window_hard", "Ventana dura", "bool"), Field("location", "Instalación"), Field("equipment", "Equipo / TAG"),
            Field("voltage_kv", "Tensión (kV; vacío = sin especificar)"), Field("requires_outage", "Requiere desconexión", "bool"),
            Field("discipline", "Disciplina"), Field("calendar_id", "Calendario", "choice", [(None, "Del proyecto")] + [(c["id"], c["name"]) for c in self.state.calendars]),
            Field("wbs_code", "Código WBS"), Field("priority", "Prioridad", "int", maximum=100),
            Field("cost_per_occurrence", "Costo por ejecución", "float"), Field("active", "Activa", "bool"),
            Field("quantity_total", "Cantidad total por ocurrencia (opcional)", help="Deje cantidad, lote, rendimiento y equipos vacíos para conservar el modo tradicional."),
            Field("unit", "Unidad (paneles, strings, SCB…)"),
            Field("batch_size", "Tamaño de lote / orden de trabajo"),
            Field("productivity_day_team", "Rendimiento por equipo (cantidad/día)"),
            Field("team_ids", "Equipos de trabajo", "multi_choice", [(r["id"], r["name"]) for r in self.state.resources if r.get("is_work_team")], span=True,
                  help="Primero cree los equipos en Equipos y reparto. Cada equipo utiliza su propio calendario. HH y duración se calculan por lote."),
            Field("productivity_hour_team", "Rendimiento por hora de equipo (opcional)"),
            Field("productive_hours_day", "Horas productivas de la tasa diaria (opcional)"),
            Field("batch_full_day", "Lote de día completo", "bool", help="NO: horas exactas y capacidad compartida. SI: redondea a jornadas completas."),
            Field("route_code", "Ruta del ítem (código)", help="El ítem queda como checklist: solo se programa su ruta."),
            Field("is_route", "Es una ruta o paquete", "bool"),
            Field("is_daily_round", "Ronda diaria por planta (HH fijas)", "bool"),
            Field("batch_continuous_in_shift", "Lote continuo en turno", "bool",
                  help="NO por defecto: continúa en el siguiente bloque del mismo equipo, sin trabajar descansos. SI exige días consecutivos."),
            Field("balance_tolerance_batches", "Tolerancia de equidad por ciclo/mes (lotes)", "int", minimum=0,
                  help="Para frecuencias menores que el ciclo NxM. Por defecto 1. En otras frecuencias se exige diferencia máxima de 1 por ocurrencia."),
        ]

    @staticmethod
    def _validate(values):
        if not values.get("code", "").strip() or not values.get("name", "").strip():
            return "Código y nombre son obligatorios."
        try:
            if values.get("preferred_months") and any(not 1 <= int(m.strip()) <= 12 for m in values["preferred_months"].split(",")):
                return "Los meses preferidos deben estar entre 1 y 12."
        except ValueError:
            return "Ingresa meses enteros separados por coma."
        try:
            voltage = values.get("voltage_kv")
            if voltage not in (None, "") and (not isfinite(float(str(voltage).replace(",", "."))) or float(str(voltage).replace(",", ".")) < 0):
                return "La tensión debe ser un número finito no negativo."
        except ValueError:
            return "Ingresa una tensión numérica o deja el campo vacío."
        if values.get("first_due") and values.get("horizon_end") and values["first_due"] > values["horizon_end"]:
            return "El fin del horizonte no puede ser anterior a la primera fecha."
        try:
            from ganttier.teams import validate_batch_fields
            validate_batch_fields(quantity_values(values))
        except (ValueError, TypeError) as exc:
            return str(exc)
        return None

    def selected(self):
        index = self.table.currentIndex()
        return self.model.row_dict(index.row()) if index.isValid() else None

    def task_wizard(self):
        return run_task_list_wizard(self.state, parent=self, mode="mantenimiento")

    def paste_tasks(self):
        if not self._can_mutate():
            return
        pid = self.state.pid
        rows = paste_from_clipboard(self, "Pegar tareas periódicas desde Excel", self._fields())
        if rows and pid == self.state.pid:
            self.paste_rows(rows)

    def paste_rows(self, rows):
        if not self._can_mutate() or self.state.mode != "mantenimiento":
            return
        pid, client = self.state.pid, self.state.client
        allowed = {field.key for field in self._fields()}
        defaults = asdict(PeriodicTask(id=0, project_id=pid, code="", name=""))
        payloads = [{key: value for key, value in dict(defaults, **row).items() if key in allowed}
                    for row in rows]
        if not payloads:
            return
        self.saving = True
        self._actions()

        def work():
            codes = {task["code"] for task in client.periodic_tasks(pid)}
            for number, row in enumerate(payloads, 1):
                row.update(quantity_values(row))
                row["code"] = str(row.get("code") or "").strip()
                row["name"] = str(row.get("name") or "").strip()
                message = self._validate(row)
                if message:
                    raise ValueError(f"Fila {number}: {message}")
                if row["code"] in codes:
                    raise ValueError(f"Fila {number}: código duplicado «{row['code']}».")
                codes.add(row["code"])
                if row.get("frequency") not in dict(FREQUENCIES):
                    raise ValueError(f"Fila {number}: frecuencia inválida.")
                for key in ("interval", "duration_days", "work_hours", "crew_size",
                            "tol_before_days", "tol_after_days", "priority", "cost_per_occurrence"):
                    number_value = float(row[key])
                    minimum = 1 if key in ("interval", "duration_days") else 0
                    if not isfinite(number_value) or number_value < minimum:
                        raise ValueError(f"Fila {number}: {key} debe ser finito y al menos {minimum}.")
                    if key in ("interval", "duration_days", "tol_before_days", "tol_after_days", "priority"):
                        if not number_value.is_integer():
                            raise ValueError(f"Fila {number}: {key} debe ser entero.")
                        row[key] = int(number_value)
                    else:
                        row[key] = number_value
                if row["priority"] > 100:
                    raise ValueError(f"Fila {number}: prioridad fuera del rango 0–100.")
                voltage = row.get("voltage_kv")
                row["voltage_kv"] = None if voltage in (None, "") else float(str(voltage).replace(",", "."))
            created = []
            try:
                # La API de maestros acepta una tarea por petición.
                for row in payloads:
                    created.append(client.create_periodic_task(pid, row))
            except Exception as exc:
                raise RuntimeError(f"Se guardaron {len(created)} de {len(payloads)} tareas. "
                                   f"Las restantes no se guardaron: {exc}") from exc
            return created

        def finish():
            self.saving = False
            self._actions()
            if pid == self.state.pid:
                self._reload_written()

        def done(created):
            finish()
            if pid == self.state.pid:
                self.last_paste_result = created
                toast(f"{fmt_num(len(created))} tareas periódicas creadas")

        def failed(exc):
            finish()  # También recarga si hubo escrituras parciales.
            show_error(exc, self)
        run_task(work, done, failed, busy="Guardando tareas periódicas…")

    def new_task(self):
        if not self._can_mutate():
            return
        self._edit(asdict(PeriodicTask(id=0, project_id=self.state.pid, code="", name="")))

    def edit_task(self):
        if not self._can_mutate():
            return
        task = self.selected()
        if task and not self.saving:
            self._edit(task)

    def _edit(self, task):
        pid = self.state.pid
        dlg = FormDialog("Editar tarea periódica" if task.get("id") else "Nueva tarea periódica",
                         self._fields(), task, self, validate=self._validate)
        if dlg.exec():
            if pid != self.state.pid:
                notify("El proyecto cambió; no se guardó la tarea.", "info")
                return
            self.save_task(dlg.values(), task.get("id"))

    def save_task(self, values, tid=None):
        if not self._can_mutate() or self.state.mode != "mantenimiento":
            return
        message = self._validate(values)
        if message:
            QMessageBox.warning(self, "Revisa los datos", message)
            return
        values = quantity_values(values)
        payload = {f.key: values[f.key] for f in self._fields() if f.key in values}
        voltage = payload.get("voltage_kv")
        payload["voltage_kv"] = None if voltage in (None, "") else float(str(voltage).replace(",", "."))
        pid, client = self.state.pid, self.state.client
        self.saving = True
        self._actions()

        def done(result):
            self.saving = False
            self.last_saved = result
            self._actions()
            notify("Tarea guardada")
            if pid == self.state.pid:
                self._reload_written()
        def failed(exc):
            self.saving = False
            self._actions()
            show_error(exc, self)
        run_task(lambda: client.update_periodic_task(tid, payload) if tid else client.create_periodic_task(pid, payload),
                 done, failed, busy="Guardando tarea…")

    def delete_task(self):
        if not self._can_mutate():
            return
        pid = self.state.pid
        task = self.selected()
        if not task or self.saving:
            return
        client = self.state.client
        run_task(lambda: client.periodic_task_history(task["id"]), lambda h: self._confirm_delete_task(pid, task, h),
                 lambda _exc: self._confirm_delete_task(pid, task, None))

    def _confirm_delete_task(self, pid, task, history):
        if pid != self.state.pid or not self._can_mutate() or self.saving:
            return
        warning = history_warning(history)
        text = f"Se eliminará {task['code']} y sus ocurrencias generadas." + (f"\n\n{warning}" if warning else "")
        if not confirm(self, "Eliminar tarea", text, danger=True):
            return
        force = bool(warning)
        if pid != self.state.pid or not self._can_mutate():
            notify("El proyecto cambió; no se eliminó la tarea.", "info")
            return
        pid, client = self.state.pid, self.state.client
        self.saving = True
        self._actions()
        def done(_):
            self.saving = False
            self._actions()
            if pid == self.state.pid:
                self._reload_written()
        def failed(exc):
            self.saving = False
            self._actions()
            show_error(exc, self)
        run_task(lambda: client.delete_periodic_task(task["id"], force=force), done, failed, busy="Eliminando tarea…")

    def _reload_written(self):
        self._publishing = True
        try:
            self.state.bump_schedule()
        finally:
            self._publishing = False
        self.refresh(after_write=True)

    def generate(self):
        if not self._can_mutate() or self.state.mode != "mantenimiento":
            return
        pid = self.state.pid
        start, end = iso(self.start.date()), iso(self.end.date())
        if not start or not end or start > end:
            QMessageBox.warning(self, "Revisa el horizonte", "El inicio debe ser anterior o igual al término.")
            return
        replace = self.replace.isChecked()
        if replace and not confirm(self, "Reemplazar ocurrencias", "Se solicitó reemplazar las ocurrencias y sus avances. ¿Continuar?"):
            return
        if pid != self.state.pid or not self._can_mutate():
            notify("El proyecto cambió; no se generó el programa.", "info")
            return
        pid, client = self.state.pid, self.state.client
        key = f"solve:{pid}"
        begin = getattr(self.state, "try_begin", None)
        if callable(begin) and not begin(key):
            notify("Ya se está generando un programa para este proyecto.", "info")
            return
        self.generating = True
        self._actions()
        generated = None
        body = {"horizon_start": start, "horizon_end": end, "regenerate": replace,
                "weights": {"level": 1, "window": 1},
                "options": {"assign_workers": True, "respect_resource_limits": True}, "apply": True}
        def work():
            nonlocal generated
            try:
                return client.post(f"/projects/{pid}/schedule/solve", body)
            except ApiError as exc:
                if exc.status not in (404, 405):
                    raise
            generated = client.generate_occurrences(pid, start, end, replace)
            result = client.optimize(pid, body["weights"], body["options"], apply=True)
            return dict(result, created=generated.get("created"), kept=generated.get("kept"), fallback=True)
        def finish():
            self.generating = False
            end_operation = getattr(self.state, "end", None)
            if callable(begin) and callable(end_operation):
                end_operation(key)
            self._actions()
        def done(result):
            finish()
            if pid == self.state.pid:
                self.last_result = result
                status = result.get("status", "")
                labels = {"optimo": "Óptimo", "factible": "Factible", "heuristico": "Heurístico", "infactible": "Sin programa factible"}
                text = labels.get(status, status or "Programación finalizada")
                if result.get("created") is not None:
                    text += f" · {fmt_num(result.get('created'))} ocurrencias nuevas"
                if result.get("kept") is not None:
                    text += f" · {fmt_num(result.get('kept'))} ocurrencias conservadas"
                metrics = result.get("after") or {}
                if metrics.get("finish"):
                    text += f" · Término: {fmt_date(metrics['finish'])}"
                if metrics.get("overload_hours") is not None:
                    text += f" · Sobrecarga: {fmt_num(metrics['overload_hours'], 1)} HH"
                if status == "infactible":
                    text += ". No se aplicaron las fechas de la optimización."
                if result.get("applied") is True:
                    text += " · Programa aplicado"
                elif result.get("applied") is False:
                    text += " · Programa no aplicado"
                warnings = list(result.get("warnings") or [])
                warnings += [v.get("detail", str(v)) if isinstance(v, dict) else str(v)
                             for v in result.get("violations", [])]
                self.summary.setText(text + ("\n" + "\n".join(dict.fromkeys(warnings)) if warnings else ""))
            if pid == self.state.pid:
                self.state.bump_schedule()
        def failed(exc):
            finish()
            if generated is not None:
                if pid == self.state.pid:
                    self.summary.setText(f"Se generaron {fmt_num(generated.get('created', 0))} ocurrencias, pero no se completó la programación. {exc}")
                self.state.bump_schedule()
            show_error(exc, self)
        run_task(work, done, failed, busy="Generando y nivelando el programa del plan…")

    def download_outages(self):
        pid, start, end = self.state.pid, iso(self.start.date()), iso(self.end.date())
        if pid:
            run_task(lambda: self.state.client.outages_xlsx(pid, start, end),
                     lambda data: self._save_download(data), busy="Preparando desconexiones…")

    def _save_download(self, data):
        try:
            save_bytes_dialog(self, data, "programa_desconexiones.xlsx")
        except OSError as exc:
            show_error(exc, self)

    def on_theme(self):
        t = tokens()
        self.delete_button.setStyleSheet(
            f"QPushButton {{color: {t.critical}; border-color: transparent;}}"
            f"QPushButton:hover {{background: {t.surface3};}}")
        self.replace.setStyleSheet(f"QCheckBox {{color: {t.warning};}}")
        self.replace_hint.setStyleSheet(f"color: {t.warning};")
        for index, name in enumerate(("list-todo", "layers", "calendar-days")):
            self.tabs.setTabIcon(index, icon(name))
        self.heatmap.update()
