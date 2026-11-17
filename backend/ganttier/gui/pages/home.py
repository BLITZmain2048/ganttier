"""Inicio: accesos de creación y flujo guiado, con datos obtenidos desde la API."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea, QVBoxLayout, QWidget

from ..client import ApiError
from ..theme import tokens
from ..widgets.common import KpiCard, button, fmt_date, fmt_num, fmt_pct, run_task, show_error
from ..widgets.icons import icon
from .base import Page


class HomePage(Page):
    title = "Inicio"
    subtitle = "De la lista de tareas al seguimiento de tu programa"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._ticket = 0
        self._display_pid = None
        self._recent_rows = []
        self.checklist_state = [False] * 6
        self._has_tasks = False
        self._reported_files = None
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(20)
        scroll.setWidget(body)
        self.root.addWidget(scroll)

        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Bienvenido a Ganttier")
        title.setProperty("role", "title")
        welcome_layout.addWidget(title)
        text = QLabel("Empieza con tus tareas, crea un proyecto o explora un ejemplo completo.")
        text.setWordWrap(True)
        text.setProperty("role", "subtitle")
        welcome_layout.addWidget(text)
        cards = QGridLayout()
        cards.setSpacing(16)
        self.welcome_cards = []
        choices = [
            ("Nuevo desde lista de tareas", "list-todo", "Pega tus tareas y genera un programa", self.start_wizard),
            ("Nuevo proyecto vacío", "folder-plus", "Define el proyecto paso a paso", self.new_project),
            ("Importar desde Excel", "file-spreadsheet", "Continúa desde una planilla existente", lambda: self._go("importar")),
            ("Explorar demo", "rocket", "Prueba un proyecto de construcción", self.explore_demo),
        ]
        for index, (label, name, help_text, callback) in enumerate(choices):
            card = button(label + "\n" + help_text, callback, primary=index == 0, tip=help_text)
            card.setMinimumHeight(108)
            # La QSS global limita la altura mínima de botones: la tarjeta declara la suya.
            card.setStyleSheet("QPushButton { min-height: 92px; text-align: left; padding: 14px 22px; font-size: 10.5pt; }")
            card.setIconSize(QSize(28, 28))
            card.setProperty("homeIcon", name)
            card.setAccessibleName(label)
            self.welcome_cards.append(card)
            cards.addWidget(card, index // 2, index % 2)
        welcome_layout.addLayout(cards)
        layout.addWidget(self.welcome)

        self.dashboard = QWidget()
        dashboard_layout = QVBoxLayout(self.dashboard)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setSpacing(16)
        self.project_heading = QLabel()
        self.project_heading.setTextFormat(Qt.PlainText)
        self.project_heading.setProperty("role", "section")
        dashboard_layout.addWidget(self.project_heading)
        metrics = QGridLayout()
        self.kpis = {}
        for index, (key, label) in enumerate([
            ("activities", "Actividades"), ("finish", "Término planificado"), ("progress", "Avance / planificado"),
            ("spi", "SPI"), ("hours", "HH totales"), ("resources", "Recursos"),
        ]):
            card = KpiCard(label)
            self.kpis[key] = card
            metrics.addWidget(card, index // 3, index % 3)
        dashboard_layout.addLayout(metrics)
        self.checklist = QFrame()
        self.checklist.setProperty("card", True)
        checklist_layout = QVBoxLayout(self.checklist)
        heading = QLabel("Tu siguiente paso")
        heading.setProperty("role", "section")
        checklist_layout.addWidget(heading)
        self.step_icons, self.step_statuses, self.step_buttons = [], [], []
        steps = [
            ("Recursos definidos", "Definir recursos", lambda: self._go("recursos")),
            ("Tareas cargadas", "Cargar tareas", self.open_tasks),
            ("Programa generado", "Generar programa", self.generate),
            ("Línea base guardada", "Guardar línea base", lambda: self._go("control")),
            ("Avance registrado", "Registrar avance", lambda: self._go("control")),
            ("Informe emitido", "Emitir informe", lambda: self._go("informes")),
        ]
        for index, (label, action_text, callback) in enumerate(steps):
            row = QHBoxLayout()
            indicator = QLabel()
            indicator.setFixedSize(20, 20)
            row.addWidget(indicator)
            row.addWidget(QLabel(f"{index + 1}. {label}"), 1)
            status = QLabel("Pendiente")
            row.addWidget(status)
            action = button(action_text, callback, primary=index == 2)
            action.setMinimumWidth(160)
            action.setObjectName(f"HomeStep{index + 1}")
            row.addWidget(action)
            if index == 1:
                row.addWidget(button("Importar", lambda: self._go("importar")))
            checklist_layout.addLayout(row)
            self.step_icons.append(indicator)
            self.step_statuses.append(status)
            self.step_buttons.append(action)
        self.step_statuses[5].setToolTip("Historial automático y emisiones manuales observadas en esta instalación.")
        dashboard_layout.addWidget(self.checklist)
        layout.addWidget(self.dashboard)

        self.recent_box = QFrame()
        self.recent_box.setProperty("card", True)
        recent_layout = QVBoxLayout(self.recent_box)
        label = QLabel("Proyectos recientes")
        label.setProperty("role", "section")
        recent_layout.addWidget(label)
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(240)
        self.recent_list.setAccessibleName("Proyectos recientes")
        self.recent_list.itemActivated.connect(self._open_recent)
        self.recent_list.itemClicked.connect(self._open_recent)
        recent_layout.addWidget(self.recent_list)
        self.recent_empty = QLabel("Aquí aparecerán los proyectos que abras.")
        self.recent_empty.setProperty("role", "muted")
        recent_layout.addWidget(self.recent_empty)
        layout.addWidget(self.recent_box)
        self.load_status = QLabel()
        self.load_status.setWordWrap(True)
        self.load_status.setProperty("role", "muted")
        layout.addWidget(self.load_status)
        layout.addStretch()
        self.dashboard.hide()
        self.state.catalogs_changed.connect(self._mark_stale)
        self.state.projects_changed.connect(self._mark_stale)
        self.state.recent_projects_changed.connect(self._mark_stale)
        self.state.operations_changed.connect(self._update_actions)
        self.on_theme()

    def _window(self):
        # La página puede construirse antes de incorporarla al QStackedWidget.
        from ..app import main_window
        return main_window()

    def _go(self, key):
        win = self._window()
        if win:
            win.go(key)

    def start_wizard(self):
        win = self._window()
        if win:
            win.task_list_wizard()

    def new_project(self):
        win = self._window()
        if win:
            win.new_project()

    def open_tasks(self):
        self._go("mantenimiento" if self.state.mode == "mantenimiento" else "gantt")

    def actions(self) -> list:
        if self.state.pid is not None:
            return [("Generar programa", "wand-sparkles", self.generate, None),
                    ("Actualizar resumen", "refresh-cw", self.refresh, "F5")]
        return [("Explorar demo de construcción", "rocket", self.explore_demo, None)]

    def activate(self):
        # Informes manuales no emite schedule_changed: comprobar al volver a Inicio.
        win = self._window()
        report_page = win._pages.get("informes") if win else None
        snapshot = (getattr(report_page, "_pid", None),
                    tuple(f.get("filename") for f in getattr(report_page, "files", [])))
        if snapshot != self._reported_files:
            self._reported_files = snapshot
            self._stale = True
        super().activate()

    def refresh(self):
        self._ticket += 1
        ticket, pid, client = self._ticket, self.state.pid, self.state.client
        recent_ids = self.state.recent_projects()
        self.welcome.setVisible(pid is None)
        self.dashboard.setVisible(pid is not None)
        if self._display_pid != pid:
            self._display_pid = pid
            self.checklist_state = [False] * 6
            self._has_tasks = False
            for card in self.kpis.values():
                card.set("—")
            self._paint_checklist()
        self.project_heading.setText((self.state.project or {}).get("name", ""))
        self.load_status.setText("Cargando resumen…" if pid is not None else "Cargando proyectos recientes…")
        self._update_actions()

        def work():
            projects = client.projects() if recent_ids else []
            if pid is None:
                return projects, None
            # Resumen liviano (agregados SQL): con 100 000 actividades no se descarga el programa completo.
            summary = client.get(f"/projects/{pid}/summary")
            project = summary["project"]
            count = summary["activities"]
            columns = {"ids": range(count), "finishes": [summary["finish"]] if summary["finish"] else [],
                       "work_hours": [summary["work_hours"]], "pct": [summary["max_percent"]]}
            scheduled = summary["scheduled"]
            evm = summary.get("evm") or {}
            resources = [None] * summary["resources"]
            periodic = [None] * summary["periodic_tasks"]
            baselines, progress = [None] * summary["baselines"], [None] * summary["progress"]
            log_method = getattr(client, "autoreports_log", None)
            try:
                reports = log_method(pid) if callable(log_method) else []
            except ApiError as exc:
                if exc.status not in (404, 422):
                    raise
                reports = []
            return projects, (project, columns, scheduled, evm, resources, periodic, baselines, progress, reports)

        def done(result):
            if ticket != self._ticket or pid != self.state.pid:
                return
            projects, data = result
            by_id = {p["id"]: p for p in projects}
            self._recent_rows = [by_id[value] for value in recent_ids if value in by_id]
            self._paint_recent()
            self.load_status.setText("")
            if data is None:
                return
            project, cols, scheduled, evm, resources, periodic, baselines, progress, reports = data
            n = len(cols.get("ids", []))
            self._has_tasks = bool(n or periodic)
            win = self._window()
            report_page = win._pages.get("informes") if win else None
            manual = bool(report_page and getattr(report_page, "_pid", None) == pid and getattr(report_page, "files", []))
            emitted = manual or any(row.get("files") and not row.get("error") for row in reports)
            report_key = f"home/report_emitted/{pid}"
            if emitted:
                self.state.settings.setValue(report_key, True)
            emitted = emitted or str(self.state.settings.value(report_key, False)).lower() in ("true", "1")
            self.checklist_state = [bool(resources), self._has_tasks, scheduled,
                                    bool(baselines or project.get("active_baseline_id")),
                                    bool(progress) or any(float(v or 0) > 0 for v in cols.get("pct", [])), bool(emitted)]
            finish = evm.get("planned_finish") or (max(v for v in cols.get("finishes", []) if v) if scheduled else None)
            self.kpis["activities"].set(fmt_num(n))
            self.kpis["finish"].set(fmt_date(finish) or "—")
            self.kpis["progress"].set(f"{fmt_pct(evm.get('percent_complete'))} / {fmt_pct(evm.get('percent_planned'))}", "Real / planificado")
            self.kpis["spi"].set(fmt_num(evm.get("SPI"), 2))
            self.kpis["hours"].set(fmt_num(sum(float(v or 0) for v in cols.get("work_hours", [])), 1))
            self.kpis["resources"].set(fmt_num(len(resources)))
            self._paint_checklist()
            self._update_actions()

        def failed(exc):
            if ticket == self._ticket and pid == self.state.pid:
                self._stale = True
                self.load_status.setText("No se pudo cargar el resumen. Pulsa F5 para reintentar.")
                show_error(exc, self)
        run_task(work, done, failed, busy="Cargando Inicio…")

    def _paint_recent(self):
        self.recent_list.clear()
        for project in self._recent_rows:
            name = "wrench" if project.get("mode") == "mantenimiento" else "hard-hat"
            item = QListWidgetItem(icon(name), project.get("name", str(project["id"])))
            item.setData(Qt.UserRole, project["id"])
            self.recent_list.addItem(item)
        self.recent_empty.setVisible(not self._recent_rows)
        self.recent_list.setVisible(bool(self._recent_rows))

    def _open_recent(self, item):
        win = self._window()
        if win:
            win.open_project(item.data(Qt.UserRole), "inicio")

    def _paint_checklist(self):
        t = tokens()
        for index, done in enumerate(self.checklist_state):
            indicator, status = self.step_icons[index], self.step_statuses[index]
            indicator.clear()
            indicator.setStyleSheet("" if done else f"border: 1px solid {t.text3}; border-radius: 10px;")
            if done:
                indicator.setPixmap(icon("circle-check", t.good, 20).pixmap(20, 20))
            indicator.setAccessibleName("Hecho" if done else "Pendiente")
            status.setText("Hecho" if done else "Pendiente")
            status.setProperty("badge", "good" if done else "true")
            status.style().unpolish(status)
            status.style().polish(status)
        self.step_buttons[1].setText("Plan de mantenimiento" if self.state.mode == "mantenimiento" else "Actividades")

    def _update_actions(self):
        pid = self.state.pid
        self.step_buttons[2].setEnabled(pid is not None and self._has_tasks and not self.state.is_busy(f"solve:{pid}"))
        self.welcome_cards[3].setEnabled(not self.state.is_busy("projects:create"))

    def on_theme(self):
        t = tokens()
        for index, card in enumerate(self.welcome_cards):
            color = (t.surface if t.name == "light" else t.bg) if index == 0 else t.accent
            card.setIcon(icon(card.property("homeIcon"), color, 28))
        self._paint_checklist()
        self._paint_recent()

    def generate(self):
        pid, client = self.state.pid, self.state.client
        if pid is None or not self._has_tasks:
            return
        key = f"solve:{pid}"
        if not self.state.try_begin(key):
            return

        def done(result):
            try:
                if pid == self.state.pid:
                    win = self._window()
                    if win:
                        success = result.get("applied") is True and result.get("feasible") is not False
                        win.notify("Programa generado" if success else "No se aplicó un programa. Revisa las restricciones en Programar / optimizar.", "ok" if success else "info")
                    # El estado hecho depende de volver a leer fechas, nunca del clic o del HTTP 200.
                    self.state.bump_schedule()
                    self._stale = False
                    self.refresh()
            finally:
                self.state.end(key)

        def failed(exc):
            self.state.end(key)
            show_error(exc, self)
        try:
            run_task(lambda: client.post(f"/projects/{pid}/schedule/solve", {"apply": True}),
                     done, failed, busy="Generando programa…")
        except Exception:
            self.state.end(key)
            raise

    def explore_demo(self):
        key, client = "projects:create", self.state.client
        if not self.state.try_begin(key):
            return

        def done(project):
            try:
                self.state.projects_changed.emit()
                win = self._window()
                if win:
                    win.open_project(project["id"], "inicio")
            finally:
                self.state.end(key)

        def failed(exc):
            self.state.end(key)
            show_error(exc, self)
        try:
            run_task(lambda: client.demo("proyecto"), done, failed, busy="Creando demo de construcción…")
        except Exception:
            self.state.end(key)
            raise
