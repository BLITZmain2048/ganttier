"""Ventana principal de Ganttier (PySide6) y arranque de la aplicación de escritorio."""
from __future__ import annotations

import importlib
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEventLoop, QLibraryInfo, QSettings, QSize, Qt, QThreadPool, QTimer, QTranslator, Slot
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget, QComboBox, QDialog,
    QDialogButtonBox, QMenu, QToolButton, QTableWidget, QTableWidgetItem, QAbstractItemView,
)

from . import arranque
from .client import ApiClient
from .state import AppState, app_settings
from .theme import apply_theme, tokens
from .widgets.icons import icon
from .widgets.common import button, run_task

_MAIN: "MainWindow | None" = None
VERSION = "1.0.0"
CLOSE_TASK_WAIT_SECONDS = 30.0


def main_window() -> "MainWindow | None":
    return _MAIN


@dataclass(frozen=True)
class PageSpec:
    key: str
    label: str
    module: str          # módulo dentro de ganttier.gui.pages
    cls: str
    section: str
    needs_project: bool = True
    only_mode: str | None = None
    icon: str = "layout-dashboard"


PAGES: list[PageSpec] = [
    PageSpec("inicio", "Inicio", "home", "HomePage", "General", needs_project=False, icon="house"),
    PageSpec("proyectos", "Proyectos", "projects", "ProjectsPage", "General", needs_project=False, icon="folder-open"),
    PageSpec("gantt", "Actividades y Gantt", "activities", "ActivitiesPage", "Planificación", icon="chart-gantt"),
    PageSpec("macro", "Programación macro", "macro", "MacroPage", "Planificación", icon="calendar-range"),
    PageSpec("mantenimiento", "Plan de mantenimiento", "maintenance", "MaintenancePage", "Planificación", only_mode="mantenimiento", icon="wrench"),
    PageSpec("optimizar", "Programar / optimizar", "optimize", "OptimizePage", "Planificación", icon="wand-sparkles"),
    PageSpec("recursos", "Recursos", "resources", "ResourcesPage", "Planificación", icon="users"),
    PageSpec("calendarios", "Calendarios", "calendars", "CalendarsPage", "Planificación", needs_project=False, icon="calendar-days"),
    PageSpec("control", "Avance y curva S", "control", "ControlPage", "Control", icon="trending-up"),
    PageSpec("analisis", "Análisis y alertas", "analysis", "AnalysisPage", "Control", icon="activity"),
    PageSpec("trabajadores", "Tablero por trabajador", "tablero_trabajadores", "TableroTrabajadoresPage", "Control", icon="users"),
    PageSpec("carga", "Histograma de carga", "carga_histograma", "CargaHistogramaPage", "Control", icon="activity"),
    PageSpec("desempeno", "Desempeño", "performance", "PerformancePage", "Control", icon="gauge"),
    PageSpec("informes", "Informes", "reports", "ReportsPage", "Control", icon="file-text"),
    PageSpec("importar", "Importar / Exportar", "importexport", "ImportExportPage", "Control", needs_project=False, icon="file-spreadsheet"),
]


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    t = tokens()
    for (x, y, w, c) in ((4, 8, 34, t.accent), (20, 27, 38, t.datadate), (12, 46, 26, t.good)):
        p.setBrush(QColor(c))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(x, y, w, 11, 3, 3)
    p.end()
    return QIcon(pm)


class _Placeholder(QWidget):
    def __init__(self, text: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setProperty("muted", True)
        lay.addWidget(lbl)

    def activate(self):  # interfaz de Page
        pass


class MainWindow(QMainWindow):
    def __init__(self, client: ApiClient, theme: str = "light"):
        super().__init__()
        global _MAIN
        _MAIN = self
        self.setWindowTitle("Ganttier — Planificación y control")
        self.setWindowIcon(app_icon())
        self.settings = app_settings()
        self.state = AppState(client, self.settings)
        self.theme_name = theme
        self._busy = 0
        self._closing = False
        self._closed = False
        self._server = None
        self._pages: dict[str, QWidget] = {}
        self._project_rows = []
        self._projects_ticket = 0
        self.sidebar_collapsed = str(self.settings.value("sidebar_collapsed", False)).lower() in ("true", "1")
        self.setAcceptDrops(True)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        # barra lateral
        self.sidebar = side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(220)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 8)
        self.brand = brand = QLabel("Ganttier")
        brand.setObjectName("Brand")
        self.brand_sub = sub = QLabel("Planificación y control")
        sub.setObjectName("BrandSub")
        brand_row = QHBoxLayout()
        brand_row.addWidget(brand, 1)
        self.sidebar_btn = button("", self.toggle_sidebar, tip="Contraer barra lateral · Ctrl+B")
        self.sidebar_btn.setAccessibleName("Contraer barra lateral")
        brand_row.addWidget(self.sidebar_btn)
        sv.addLayout(brand_row)
        sv.addWidget(sub)
        self.nav = QListWidget()
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setFocusPolicy(Qt.StrongFocus)
        sv.addWidget(self.nav, 1)
        self.theme_btn = button("Tema oscuro", self.toggle_theme)
        self.shortcuts_btn = button("Atajos", self.show_shortcuts, tip="Atajos de teclado · F1")
        self.version_label = QLabel(f"v{VERSION}")
        self.version_label.setAlignment(Qt.AlignCenter)
        self.version_label.setProperty("role", "caption")
        sv.addWidget(self.theme_btn)
        sv.addWidget(self.shortcuts_btn)
        sv.addWidget(self.version_label)
        h.addWidget(side)
        # zona principal
        main = QWidget()
        mv = QVBoxLayout(main)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)
        header = QFrame()
        header.setObjectName("Header")
        header.setMinimumHeight(56)
        header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        from .widgets.responsive import HeaderFlowLayout
        hh = HeaderFlowLayout(header)
        hh.setContentsMargins(16, 0, 16, 0)
        self.title = QLabel("")
        self.title.setObjectName("Title")
        self.title.setWordWrap(True)
        self.title.setProperty("role", "title")
        self.subtitle_label = QLabel("")
        self.subtitle_label.setProperty("role", "subtitle")
        self.subtitle_label.setWordWrap(True)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle_label)
        hh.addLayout(titles, 1)
        # Conservado como atributo para integraciones antiguas; lo reemplaza el selector.
        self.proj_label = QLabel("", self)
        self.proj_label.hide()
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(260)
        self.project_combo.setMaximumWidth(380)
        self.project_combo.setMinimumContentsLength(28)
        self.project_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.project_combo.setAccessibleName("Proyecto activo")
        self.project_combo.setToolTip("Cambiar proyecto · recientes primero")
        self.project_combo.currentIndexChanged.connect(self._select_project)
        self.mode_badge = QLabel("")
        self.cut_badge = QLabel("")
        self.cut_badge.setProperty("badge", True)
        hh.setSpacing(8)
        for w in (self.project_combo, self.mode_badge, self.cut_badge):
            if w in (self.mode_badge, self.cut_badge):
                w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)   # píldora compacta, no a toda altura
            hh.addWidget(w, 0, Qt.AlignVCenter)
        self.mode_badge.setVisible(False)
        self.cut_badge.setVisible(False)
        self.search_btn = button("Buscar o ejecutar…  Ctrl+K", self.command_palette)
        hh.addWidget(self.search_btn)
        self.help_btn = QToolButton()
        self.help_btn.setToolTip("Ayuda y Acerca de")
        self.help_btn.setAccessibleName("Ayuda y Acerca de")
        help_menu = QMenu(self.help_btn)
        help_menu.addAction("Atajos de teclado · F1", self.show_shortcuts)
        help_menu.addAction("Acerca de Ganttier", self.show_about)
        self.help_btn.setMenu(help_menu)
        self.help_btn.setPopupMode(QToolButton.InstantPopup)
        hh.addWidget(self.help_btn)
        mv.addWidget(header)
        self.stack = QStackedWidget()
        mv.addWidget(self.stack, 1)
        h.addWidget(main, 1)
        self.setCentralWidget(central)
        # barra de estado
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(160)
        self.progress.setFixedHeight(3)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.busy_label = QLabel("Listo")
        self.statusBar().addPermanentWidget(self.busy_label)
        self.statusBar().addPermanentWidget(self.progress)
        self.server_label = QLabel(self)
        self.server_label.hide()
        self.statusBar().setToolTip(f"API: {client.base_url}")
        self.busy_label.setToolTip(f"API: {client.base_url}")

        self._build_nav()
        self.nav.currentRowChanged.connect(self._on_nav)
        self.state.project_changed.connect(self._on_project)
        self.state.projects_changed.connect(self.reload_projects)
        self.state.recent_projects_changed.connect(self._populate_projects)
        from . import theme as theme_module
        self._theme_signals = getattr(theme_module, "theme_signals", None)
        if self._theme_signals is not None:
            self._theme_signals.theme_changed.connect(self._on_theme_changed)
        self._apply_sidebar()
        self._recolor_icons()
        self._shortcuts()
        self.resize(1440, 880)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        window_state = self.settings.value("window_state")
        if window_state is not None:
            self.restoreState(window_state)
        last_page = str(self.settings.value("last_page", "inicio"))
        if not any(s.key == last_page for s in PAGES):
            last_page = "inicio"
        self.go("inicio")
        self.reload_projects()
        try:
            last_pid = int(self.settings.value("last_project", "") or 0)
        except (ValueError, TypeError):
            last_pid = 0
        if last_pid:
            # No desviar la navegación si el usuario ya eligió otra pantalla mientras carga.
            def restored(_):
                if self.current_key() == "inicio":
                    self.go(last_page)
            self.state.open_project_async(last_pid, restored, on_error=lambda _: None)
        else:
            self.go(last_page)

    # ----------------------------------------------------------------- navegación
    def _build_nav(self):
        self.nav.clear()
        self._rows: list[PageSpec | None] = []
        section = None
        for spec in PAGES:
            if spec.section != section:
                section = spec.section
                it = QListWidgetItem(section.upper())
                it.setFlags(Qt.NoItemFlags)
                f = it.font()
                f.setPointSizeF(7.5)
                f.setBold(True)
                it.setFont(f)
                self.nav.addItem(it)
                self._rows.append(None)
            it = QListWidgetItem(spec.label)
            it.setIcon(icon(spec.icon, tokens().sidebar_text))
            it.setSizeHint(QSize(44, 36))
            self.nav.addItem(it)
            self._rows.append(spec)
        self._refresh_nav_state()

    def _refresh_nav_state(self):
        has = self.state.project is not None
        mode = self.state.mode
        for row, spec in enumerate(self._rows):
            if spec is None:
                continue
            it = self.nav.item(row)
            hidden = bool(spec.only_mode and mode and mode != spec.only_mode) or bool(spec.only_mode and not mode)
            it.setHidden(hidden)
            enabled = has or not spec.needs_project
            it.setFlags((Qt.ItemIsEnabled | Qt.ItemIsSelectable) if enabled else Qt.NoItemFlags)
            it.setToolTip(spec.label + ("" if enabled else " · Abre un proyecto primero"))

    def page(self, key: str) -> QWidget:
        if key in self._pages:
            return self._pages[key]
        spec = next(s for s in PAGES if s.key == key)
        try:
            mod = importlib.import_module(f"ganttier.gui.pages.{spec.module}")
            widget = getattr(mod, spec.cls)(self.state)
        except Exception as exc:  # noqa: BLE001 - la app no debe caerse por una página
            traceback.print_exc()
            widget = _Placeholder(f"No se pudo cargar la pantalla «{spec.label}».\n\n{exc}")
        self._pages[key] = widget
        self.stack.addWidget(widget)
        return widget

    def go(self, key: str):
        spec = next((s for s in PAGES if s.key == key), None)
        if spec is None:
            return
        if spec.needs_project and self.state.project is None:
            key = "proyectos"
            spec = next(s for s in PAGES if s.key == key)
        elif spec.only_mode and spec.only_mode != self.state.mode:
            key = "inicio"
            spec = PAGES[0]
        row = next(i for i, s in enumerate(self._rows) if s is not None and s.key == key)
        if self.nav.currentRow() != row:
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(row)
            self.nav.blockSignals(False)
        w = self.page(key)
        self.stack.setCurrentWidget(w)
        self.title.setText(getattr(w, "title", "") or spec.label)
        self.subtitle_label.setText(getattr(w, "subtitle", ""))
        self.subtitle_label.setVisible(bool(self.subtitle_label.text()))
        self.settings.setValue("last_page", key)
        if hasattr(w, "activate"):
            w.activate()

    def current_key(self) -> str | None:
        w = self.stack.currentWidget()
        return next((k for k, v in self._pages.items() if v is w), None)

    def _on_nav(self, row: int):
        spec = self._rows[row] if 0 <= row < len(self._rows) else None
        if spec is not None:
            self.go(spec.key)

    @Slot(object)
    def _on_project(self, project):
        self._refresh_nav_state()
        self._populate_projects()
        if project:
            self.proj_label.setText(f"·  <b>{project['name']}</b>")
            mode = project.get("mode", "proyecto")
            self.mode_badge.setText("Mantenimiento" if mode == "mantenimiento" else "Proyecto")
            self.mode_badge.setProperty("badge", mode)
            self.mode_badge.setVisible(True)
            dd = project.get("data_date")
            self.cut_badge.setText(f"Corte: {dd[8:10]}-{dd[5:7]}-{dd[:4]}" if dd else "")
            self.cut_badge.setVisible(bool(dd))
        else:
            self.proj_label.setText("")
            self.mode_badge.setVisible(False)
            self.cut_badge.setVisible(False)
        self.mode_badge.style().unpolish(self.mode_badge)
        self.mode_badge.style().polish(self.mode_badge)
        self.setWindowTitle(f"Ganttier — {project['name']}" if project else "Ganttier — Planificación y control")

    def open_project(self, pid: int, page: str = "gantt"):
        self.state.open_project_async(pid, lambda _: self.go(page))

    # ----------------------------------------------------------------- estado / avisos
    def busy_start(self, text: str):
        self._busy += 1
        self.busy_label.setText(text)
        self.progress.setVisible(True)
        QApplication.setOverrideCursor(Qt.BusyCursor)

    def busy_end(self):
        self._busy = max(0, self._busy - 1)
        if self._busy == 0:
            self.busy_label.setText("Listo")
            self.progress.setVisible(False)
        QApplication.restoreOverrideCursor()

    def notify(self, text: str, kind: str = "ok"):
        from .widgets import common
        toast = getattr(common, "toast", None)
        if callable(toast):
            toast(text, kind)
            return
        self.statusBar().showMessage(text, 9000 if kind == "error" else 5000)

    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        apply_theme(QApplication.instance(), self.theme_name)
        self.settings.setValue("theme", self.theme_name)
        if self._theme_signals is None:
            self._on_theme_changed(self.theme_name)

    def _on_theme_changed(self, name):
        if self._closed:
            return
        self.theme_name = name
        self._recolor_icons()
        for w in self._pages.values():
            if hasattr(w, "on_theme"):
                w.on_theme()
            w.update()

    def toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        self.settings.setValue("sidebar_collapsed", self.sidebar_collapsed)
        self._apply_sidebar()
        self._recolor_icons()

    def _apply_sidebar(self):
        collapsed = self.sidebar_collapsed
        self.sidebar.setFixedWidth(60 if collapsed else 232)
        self.brand.setVisible(not collapsed)
        self.brand_sub.setVisible(not collapsed)
        self.version_label.setVisible(not collapsed)
        self.sidebar.setToolTip(f"Ganttier v{VERSION}")
        for row, spec in enumerate(self._rows):
            item = self.nav.item(row)
            if spec is None:
                item.setHidden(collapsed)
            else:
                item.setText("" if collapsed else spec.label)
        self.shortcuts_btn.setText("" if collapsed else "Atajos")
        text = "Expandir barra lateral" if collapsed else "Contraer barra lateral"
        self.sidebar_btn.setToolTip(text + " · Ctrl+B")
        self.sidebar_btn.setAccessibleName(text)

    def _recolor_icons(self):
        t = tokens()
        self.setWindowIcon(app_icon())
        self.nav.setStyleSheet(
            f"QListWidget {{ background: {t.sidebar}; color: {t.sidebar_text}; border: none; }}"
            "QListWidget::item { margin: 2px 6px; padding: 5px; border-radius: 8px; }"
            f"QListWidget::item:selected {{ background: {t.accent_soft}; color: {t.accent}; }}"
        )
        for row, spec in enumerate(self._rows):
            if spec:
                self.nav.item(row).setIcon(icon(spec.icon, t.sidebar_text, active_color=t.accent))
        self.sidebar_btn.setIcon(icon("panel-left-open" if self.sidebar_collapsed else "panel-left-close", t.sidebar_text))
        self.theme_btn.setIcon(icon("sun" if self.theme_name == "dark" else "moon", t.sidebar_text))
        text = "Tema claro" if self.theme_name == "dark" else "Tema oscuro"
        self.theme_btn.setText("" if self.sidebar_collapsed else text)
        self.theme_btn.setToolTip(text)
        self.theme_btn.setAccessibleName(text)
        self.shortcuts_btn.setIcon(icon("keyboard", t.sidebar_text))
        self.shortcuts_btn.setAccessibleName("Atajos de teclado")
        for btn in (self.sidebar_btn, self.theme_btn, self.shortcuts_btn):
            btn.setStyleSheet(f"QPushButton {{ color: {t.sidebar_text}; background: {t.sidebar}; border: none; padding: 8px; }}")
        self.search_btn.setIcon(icon("search"))
        self.help_btn.setIcon(icon("circle-help"))
        self.version_label.setStyleSheet(f"color: {t.sidebar_text};")
        self._populate_projects()

    def reload_projects(self):
        self._projects_ticket += 1
        ticket = self._projects_ticket

        def done(rows):
            if ticket == self._projects_ticket and not self._closed:
                self._project_rows = rows
                self._populate_projects()

        def failed(exc):
            if ticket == self._projects_ticket and not self._closed:
                self.notify(f"No se pudo actualizar la lista de proyectos: {exc}", "error")
        run_task(self.state.client.projects, done, failed)

    def _populate_projects(self):
        projects = {p["id"]: p for p in self._project_rows}
        if self.state.project:
            projects[self.state.pid] = self.state.project
        recent = [pid for pid in self.state.recent_projects() if pid in projects]
        other = sorted((pid for pid in projects if pid not in recent),
                       key=lambda pid: projects[pid].get("name", "").casefold())
        self.project_combo.blockSignals(True)
        try:
            self.project_combo.clear()
            self.project_combo.addItem("Seleccionar proyecto…", None)
            for pid in recent + other:
                if other and recent and pid == other[0]:
                    self.project_combo.insertSeparator(self.project_combo.count())
                project = projects[pid]
                mode_icon = "wrench" if project.get("mode") == "mantenimiento" else "hard-hat"
                self.project_combo.addItem(icon(mode_icon), project.get("name", str(pid)), pid)
                self.project_combo.setItemData(self.project_combo.count() - 1,
                    ("Reciente · " if pid in recent else "") + project.get("mode", "proyecto"), Qt.ToolTipRole)
            self.project_combo.setCurrentIndex(max(0, self.project_combo.findData(self.state.pid)))
        finally:
            self.project_combo.blockSignals(False)

    def _interactive(self) -> bool:
        interactive = getattr(self, "prompt_on_close", None)
        if interactive is None:
            interactive = QApplication.platformName() not in ("offscreen", "minimal")
        return bool(interactive)

    def _confirm_project_change(self) -> bool:
        """A3-05: las páginas cuyo borrador se perdería al cambiar de proyecto piden confirmación."""
        blocking = [page for page in self._pages.values() if getattr(page, "blocks_project_change", lambda: False)()]
        if not blocking or not self._interactive():
            return True
        from .widgets import common
        names = ", ".join(getattr(page, "title", page.__class__.__name__) for page in blocking)
        return common.confirm(self, "Cambios sin guardar",
                              f"Hay cambios sin guardar en {names}. Si cambias de proyecto se descartarán. ¿Continuar?")

    def _select_project(self, index):
        pid = self.project_combo.itemData(index)
        if pid is not None and pid == self.state.pid:
            # A3-07: volver a elegir el proyecto vigente cancela una apertura pendiente de otro proyecto.
            self.state.cancel_pending_open()
            return
        if pid is not None and pid != self.state.pid:
            if not self._confirm_project_change():
                self._populate_projects()
                return
            target = self.current_key() or "inicio"

            def failed(exc):
                self._populate_projects()
                self.notify(f"No se pudo abrir el proyecto: {exc}", "error")
            self.state.open_project_async(int(pid), lambda _: self.go(target), failed)
        elif pid is None:
            self._populate_projects()

    def new_project(self):
        self.go("proyectos")
        action = getattr(self.page("proyectos"), "new_project", None)
        if callable(action):
            action()

    def task_list_wizard(self):
        try:
            from .wizard import run_task_list_wizard
        except ImportError:
            self.go("proyectos")
            return
        pid = run_task_list_wizard(self.state, self, mode=self.state.mode or "proyecto")
        if pid is not None:
            self.state.projects_changed.emit()
            self.open_project(pid, "inicio")

    def import_assisted(self):
        from .importador_asistido import abrir_importador
        pid = abrir_importador(self.state, self)
        if pid is not None:
            self.state.projects_changed.emit()
            self.open_project(pid, 'inicio')

    def command_palette(self):
        from .widgets.palette import CommandPalette
        commands = [(spec.label, spec.icon, lambda key=spec.key: self.go(key), None)
                    for spec in PAGES if not spec.only_mode or spec.only_mode == self.state.mode]
        page_actions = getattr(self.stack.currentWidget(), "actions", None)
        if callable(page_actions):
            # QWidget también tiene actions(): aceptar solo el contrato Page.
            commands.extend(a for a in page_actions() if isinstance(a, (tuple, list)) and len(a) == 4)
        commands.extend([
            ("Nuevo proyecto vacío", "folder-plus", self.new_project, None),
            ("Nuevo desde lista de tareas", "list-todo", self.task_list_wizard, None),
            ("Importar desde Excel", "file-spreadsheet", lambda: self.go("importar"), None),
            ("Importar archivo de cliente con asistente", "file-spreadsheet", self.import_assisted, None),
            ("Exportar Gantt a Excel…", "file-spreadsheet", self.export_gantt_excel, None),
            ("Cambiar tema claro / oscuro", "sun" if self.theme_name == "dark" else "moon", self.toggle_theme, None),
            ("Atajos de teclado", "keyboard", self.show_shortcuts, "F1"),
            ("Respaldos de la base de datos", "history", self.show_backups, None),
            ("Buscar en todos los proyectos", "search", self.global_search, "Ctrl+Shift+F"),
        ])
        projects = {p["id"]: p for p in self._project_rows}
        if self.state.project:
            projects[self.state.pid] = self.state.project
        for pid in self.state.recent_projects():
            if pid in projects:
                p = projects[pid]
                commands.append((f"Abrir {p.get('name', pid)}", "wrench" if p.get("mode") == "mantenimiento" else "hard-hat",
                                 lambda value=pid: self.open_project(value, "inicio"), None))
        if getattr(self, "_palette", None) is not None:
            self._palette.close()
            self._palette.deleteLater()
        self._palette = CommandPalette(commands, self, fallback=("Buscar «{q}» en todos los proyectos", "search",
                                                                 self.global_search))
        self._palette.open()
        return self._palette

    def export_gantt_excel(self):
        from .gantt_export import open_gantt_export
        open_gantt_export(self.state, self)

    def global_search(self, text: str = ""):
        """P5-06: búsqueda entre proyectos; el resultado se abre en su proyecto y queda seleccionado."""
        from .search_dialog import GlobalSearchDialog
        if getattr(self, "_search_dialog", None) is not None:
            self._search_dialog.close()
        dialog = GlobalSearchDialog(self.state, self, text if isinstance(text, str) else "")
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.chosen.connect(self.open_search_result)
        dialog.destroyed.connect(lambda *_: setattr(self, "_search_dialog", None))
        self._search_dialog = dialog
        dialog.open()
        return dialog

    def open_search_result(self, row: dict):
        pid = (row.get("project") or {}).get("id")
        if pid is None:
            return
        periodic = row.get("type") == "tarea periódica"
        target = "mantenimiento" if periodic and (row.get("project") or {}).get("mode") == "mantenimiento" else "gantt"

        def focus(*_):
            page = self.page(target)
            if target == "gantt" and hasattr(page, "focus_activity"):
                page.focus_activity(int(row["id"]))
            elif hasattr(page, "focus_task"):
                page.focus_task(int(row.get("periodic_task_id") or row["id"]), row.get("code") or "")
            self.go(target)
        if pid == self.state.pid:
            focus()
            return
        if not self._confirm_project_change():
            return
        self.state.open_project_async(pid, focus)

    def show_backups(self):
        from .backups_dialog import BackupsDialog
        dialog = BackupsDialog(self.state, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.open()
        return dialog

    def refresh_page(self):
        refresh = getattr(self.stack.currentWidget(), "refresh", None)
        if callable(refresh):
            refresh()

    def show_shortcuts(self):
        if getattr(self, "_shortcuts_dialog", None) is not None:
            self._shortcuts_dialog.close()
            self._shortcuts_dialog.deleteLater()
        from .widgets.responsive import ResponsiveDialog
        dialog = ResponsiveDialog(self)
        dialog.setWindowTitle("Atajos de teclado")
        dialog.setObjectName("ShortcutsDialog")
        layout = QVBoxLayout(dialog)
        rows = []
        for i, spec in enumerate(PAGES):
            key = f"Ctrl+{(i + 1) % 10}" if i < 10 else f"Ctrl+Shift+{i - 9}"
            rows.append((key, spec.label))
        rows += [("Ctrl+K", "Buscar o ejecutar comandos"), ("Ctrl+Shift+F", "Buscar en todos los proyectos"), ("Ctrl+B", "Contraer / expandir barra lateral"),
                 ("F1", "Ayuda de atajos"), ("F9", "Calcular CPM (Actividades y Gantt)"),
                 ("F5", "Actualizar página activa"), ("Supr", "Eliminar selección (en tablas compatibles)"),
                 ("Enter", "Abrir / editar selección"), ("Ctrl+rueda", "Zoom del Gantt")]
        table = QTableWidget(len(rows), 2, dialog)
        table.setHorizontalHeaderLabels(["Atajo", "Acción"])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().hide()
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(620, 600)
        self._shortcuts_dialog = dialog
        dialog.open()
        return dialog

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle("Acerca de Ganttier")
        box.setTextFormat(Qt.PlainText)
        box.setText(f"Ganttier {VERSION}\nPlanificación y control de proyectos y mantenimiento.\n\nAPI local: {self.state.client.base_url}")
        self._about_dialog = box
        box.open()

    def open_file(self, path):
        if Path(path).suffix.lower() != ".xlsx":
            self.notify("Selecciona un libro Excel (.xlsx).", "info")
            return
        self.go("importar")
        load_file = getattr(self.page("importar"), "load_file", None)
        if callable(load_file):
            load_file(str(path))
        else:
            self.notify("Selecciona el archivo en Importar / Exportar.", "info")

    @staticmethod
    def _dropped_xlsx(event):
        return [url.toLocalFile() for url in event.mimeData().urls()
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() == ".xlsx"]

    def dragEnterEvent(self, event):
        if self._dropped_xlsx(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._dropped_xlsx(event)
        if paths:
            self.open_file(paths[0])
            event.acceptProposedAction()
        else:
            event.ignore()

    def _shortcuts(self):
        for i, spec in enumerate(PAGES):
            act = QAction(spec.label, self)
            act.setData(spec.key)
            # Primera decena: Ctrl+1..9,0 / Alt+1..9,0. Siguientes: Shift+1..9.
            digit = str((i + 1) % 10) if i < 10 else str(i - 9)
            modifier = "" if i < 10 else "Shift+"
            act.setShortcuts([QKeySequence(f"Ctrl+{modifier}{digit}"),
                              QKeySequence(f"Alt+{modifier}{digit}")])
            act.triggered.connect(lambda _=False, k=spec.key: self.go(k))
            self.addAction(act)
        for key, slot in (("Ctrl+K", self.command_palette), ("Ctrl+Shift+F", self.global_search), ("Ctrl+B", self.toggle_sidebar),
                          ("F1", self.show_shortcuts), ("F5", self.refresh_page)):
            act = QAction(self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(lambda _=False, fn=slot: fn())
            self.addAction(act)

    def closeEvent(self, event):
        from .widgets import common
        lifecycle = getattr(QApplication.instance(), "_ganttier_lifecycle", None)
        if lifecycle is not None:
            lifecycle.guard.note("closeEvent: entrada")
        if self._closed:
            event.accept()
            return
        if self._closing:
            event.ignore()
            return
        self._closing = True
        # Sin pantalla (pruebas, ejecución automatizada) nadie puede responder un diálogo modal:
        # se cierra sin preguntar (los borradores se descartan y las tareas en curso se esperan).
        interactive = self._interactive_close()
        try:
            if interactive and self._has_close_drafts():
                box = QMessageBox(self)
                box.setWindowTitle("Cambios sin guardar")
                box.setText("Hay borradores sin guardar. ¿Descartarlos y cerrar Ganttier?")
                box.setIcon(QMessageBox.Warning)
                discard = box.addButton("Descartar", QMessageBox.DestructiveRole)
                cancel = box.addButton("Cancelar", QMessageBox.RejectRole)
                box.setDefaultButton(cancel)
                box.exec()
                if box.clickedButton() is not discard:
                    if lifecycle is not None:
                        lifecycle.guard.note("Cierre cancelado: se conservan los borradores")
                    event.ignore()
                    return
            if common._active:
                wait_seconds = (min(CLOSE_TASK_WAIT_SECONDS, 2.0)
                                if lifecycle is not None and lifecycle.native_pending else CLOSE_TASK_WAIT_SECONDS)
                if interactive and not common.confirm(self, "Operaciones en curso",
                                      f"Hay tareas en curso. ¿Esperar hasta {wait_seconds:g} segundos antes de cerrar?\n"
                                      "Después se cerrará la aplicación; las operaciones incompletas pueden interrumpirse."):
                    event.ignore()
                    return
                self._arm_shutdown_guard()
                self.setEnabled(False)
                try:
                    deadline = time.monotonic() + wait_seconds
                    pool = QThreadPool.globalInstance()
                    while common._active or pool.activeThreadCount():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            logging.getLogger(__name__).warning(
                                "Cierre: se agotó la espera de %g s con tareas pendientes", wait_seconds)
                            break
                        # Esperas breves: los callbacks pueden encolar más escrituras.
                        pool.waitForDone(max(1, min(20, int(remaining * 1000))))
                        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents, 20)
                finally:
                    self.setEnabled(True)
            self._arm_shutdown_guard()
            self._save_close_state()
            self._shutdown()
            for dialog in self.findChildren(QDialog):
                dialog.reject()
            super().closeEvent(event)
            QApplication.instance().quit()  # también si la ventana estaba oculta o había un modal
        except Exception as exc:
            if self._closed:
                logging.getLogger(__name__).exception("Error durante el cierre")
                event.accept()
                QApplication.instance().quit()
            else:
                common.report_error(exc, self)
                event.ignore()
        finally:
            self._closing = False

    def _interactive_close(self):
        if getattr(self, "_session_ending", False):
            return False
        override = getattr(self, "prompt_on_close", None)
        if override is not None:
            return override
        return self.isVisible() and QApplication.platformName() not in ("offscreen", "minimal")

    def _has_close_drafts(self):
        if any(getattr(page, "has_unsaved", lambda: False)() for page in self._pages.values()):
            return True
        # Formularios modales no siempre publican has_unsaved en su página.
        # Se pregunta conservadoramente antes de rechazar un diálogo con editores.
        from PySide6.QtWidgets import QFileDialog, QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox
        for dialog in self.findChildren(QDialog):
            if dialog.isVisible() and not isinstance(dialog, (QFileDialog, QMessageBox)):
                if any(dialog.findChildren(cls) for cls in (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                    return True
        return False

    def _close_requires_consent(self):
        from .widgets import common
        return self._interactive_close() and (self._has_close_drafts() or bool(common._active))

    def _save_close_state(self):
        """Persistencia síncrona de preferencias antes de activar una salida forzada."""
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("window_state", self.saveState())
        self.settings.setValue("sidebar_collapsed", self.sidebar_collapsed)
        self.settings.setValue("last_page", self.current_key() or "inicio")
        self.settings.setValue("last_project", self.state.pid or "")
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            raise OSError("No se pudieron guardar las preferencias antes de cerrar")

    def _arm_shutdown_guard(self):
        lifecycle = getattr(QApplication.instance(), "_ganttier_lifecycle", None)
        if lifecycle is not None:
            lifecycle.guard.arm()

    def _shutdown(self):
        """Cierre idempotente, también desde aboutToQuit; no admite callbacks tardíos."""
        if self._closed:
            return
        self._arm_shutdown_guard()
        self._closed = True
        try:
            if self._server is not None:
                self._server.stop()
        finally:
            self.state.client.close()


def create_window(base_url: str | None = None, theme: str | None = None, token: str | None = None) -> tuple[MainWindow, object | None]:
    """Crea la ventana; si no se da ``base_url`` levanta el servidor API embebido.

    Con un servidor externo, ``token`` (o la variable GANTTIER_API_TOKEN) es su credencial de sesión.
    """
    from PySide6.QtCore import QLocale
    from . import accesibilidad
    accesibilidad.install()  # tablas enormes sin celdas accesibles (D03)
    # Formato chileno en controles numéricos y de fecha (coma decimal, punto de miles).
    QLocale.setDefault(QLocale(QLocale.Language.Spanish, QLocale.Country.Chile))
    server = None
    if base_url is None:
        from .server import EmbeddedServer
        server = EmbeddedServer()
        lifecycle = getattr(QApplication.instance(), "_ganttier_lifecycle", None)
        if lifecycle is not None:
            lifecycle.server = server  # conservarlo incluso si falla la construcción de MainWindow
            base_url = server.start(process_events=lifecycle.poll_startup)
        else:
            base_url = server.start()
        token = server.token
    arranque.mark("servidor API listo")
    client = None
    try:
        client = ApiClient(base_url, token=token or os.environ.get("GANTTIER_API_TOKEN"))
        settings = app_settings()
        name = theme or str(settings.value("theme", "light"))
        apply_theme(QApplication.instance(), name)
        arranque.mark("tema aplicado")
        win = MainWindow(client, name)
        win._server = server
        QApplication.instance().aboutToQuit.connect(win._shutdown)
        arranque.mark("ventana construida")
        return win, server
    except BaseException:
        # La API ya estaba arrancada: una excepción al crear la ventana no debe
        # dejar socket, bloqueo o cliente vivos, tampoco fuera de main().
        try:
            if client is not None:
                client.close()
        except Exception:
            logging.getLogger(__name__).exception("No se pudo cerrar el cliente tras fallar la ventana")
        try:
            if server is not None:
                server.stop()
        except Exception:
            logging.getLogger(__name__).exception("No se pudo detener la API tras fallar la ventana")
        raise


def _mostrar_presentacion(app):
    """Presentación mínima mientras se importa y arranca la API (3–11 s según el estado de la caché de archivos)."""
    if os.environ.get("GANTTIER_SIN_PRESENTACION"):
        return None
    try:
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
        from PySide6.QtWidgets import QSplashScreen
        pixmap = QPixmap(440, 150)
        pixmap.fill(QColor("#ffffff"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#d1d5db"))
        painter.drawRect(0, 0, pixmap.width() - 1, pixmap.height() - 1)
        font = QFont(app.font())
        font.setPointSize(22)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#111827"))
        painter.drawText(26, 66, "Ganttier")
        font.setPointSize(10)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#6b7280"))
        painter.drawText(26, 102, "Iniciando… la primera vez puede tardar unos segundos.")
        painter.end()
        splash = QSplashScreen(pixmap)
        lifecycle = getattr(app, "_ganttier_lifecycle", None)
        if lifecycle is not None:
            lifecycle.splash = splash
        splash.show()
        app.processEvents()
        return splash
    except Exception:  # noqa: BLE001 - la presentación nunca debe impedir el arranque
        return None


def _terminar_arranque(app, splash, win) -> None:
    arranque.mark("primer ciclo de eventos con la ventana visible")
    if splash is not None:
        splash.hide()  # finish() bombea eventos y puede consumir quit() durante el cierre
    try:
        from . import diagnostics
        from .. import services
        arranque.write_report(diagnostics.log_path(services.DATA_DIR))
    except Exception:  # noqa: BLE001
        pass
    from .precarga import schedule_plot_runtime
    schedule_plot_runtime(win)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True) if hasattr(Qt, "AA_EnableHighDpiScaling") else None
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Ganttier")
    app.setOrganizationName("Ganttier")
    app.setWindowIcon(app_icon())
    arranque.mark("QApplication creada")
    from .cierre import Lifecycle, StartupCancelled
    lifecycle = app._ganttier_lifecycle = Lifecycle(app)
    splash = _mostrar_presentacion(app)
    arranque.mark("presentación mostrada")
    translator = QTranslator(app)
    if translator.load("qtbase_es", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(translator)
        app._ganttier_translator = translator  # conservar el wrapper mientras viva QApplication
    arranque.mark("traducción de Qt cargada")
    base_url = os.environ.get("GANTTIER_API_URL")  # permite usar un servidor externo
    try:
        from . import diagnostics
        from .. import services
        lifecycle.guard.path = diagnostics.log_path(services.DATA_DIR)
        app._ganttier_watchdog = diagnostics.install(services.DATA_DIR)  # pila ante bloqueos y cierres nativos (D03/D04)
    except Exception:  # noqa: BLE001 - el diagnóstico nunca debe impedir el arranque
        app._ganttier_watchdog = None
    try:
        from . import recoleccion
        app._ganttier_gc = recoleccion.install(app)  # el GC cíclico sólo en el hilo de la interfaz (D04)
    except Exception:  # noqa: BLE001
        app._ganttier_gc = None
    arranque.mark("diagnóstico y recolector activos (incluye importar services)")
    try:
        lifecycle.poll_startup()
        win, server = create_window(base_url)
        lifecycle.win = win
        lifecycle.poll_startup()
        lifecycle.starting = False
    except StartupCancelled:
        lifecycle.cleanup()
        return 0
    except Exception as exc:  # noqa: BLE001
        lifecycle.starting = False  # cerrar la presentación aquí no es una solicitud del usuario
        if splash is not None:
            splash.close()
        QMessageBox.critical(None, "Ganttier", f"No se pudo iniciar Ganttier:\n{exc}")
        lifecycle.cleanup()
        return 1
    win.show()
    if lifecycle.requested:
        lifecycle.cleanup()
        return 0
    arranque.mark("ventana mostrada")
    QTimer.singleShot(0, lambda: _terminar_arranque(app, splash, win))
    try:
        return app.exec()
    finally:
        lifecycle.cleanup()
