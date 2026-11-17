"""Pantalla Proyectos: lista, creación, demos, duplicar, eliminar y crear desde Excel."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFileDialog, QHeaderView, QLabel, QTableView, QMenu, QToolButton

from ..widgets.common import (
    Card, Column, DictTableModel, Field, FormDialog, SortProxy, button, confirm, notify, run_task, show_error,
)
from .base import Page
from ..widgets.responsive import scroll_layout

# UX1 puede integrarse antes o después de esta página.
from .. import theme
from ..widgets import common as _common
from ..widgets.icons import icon
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget, QLineEdit

_SPACE = getattr(theme, "SPACE", dict(sm=8, md=12, lg=16))


def _button(text, slot=None, *, icon_name=None, primary=False, danger=False, kind=None):
    import inspect
    if "icon" in inspect.signature(button).parameters:
        return button(text, slot, primary=primary, danger=danger, icon=icon_name, kind=kind)
    result = button(text, slot, primary=primary, danger=danger)
    result.setProperty("kind", kind or ("primary" if primary else "danger" if danger else "secondary"))
    if icon_name:
        result.setIcon(icon(icon_name))
    return result


def _toolbar(*widgets):
    cls = getattr(_common, "Toolbar", None)
    bar = cls() if cls else QWidget()
    if not cls:
        bar.setProperty("toolbar", True)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SPACE["sm"])
    for widget in widgets:
        if cls:
            bar.add(widget) if widget is not None else bar.add_stretch()
        else:
            layout.addWidget(widget) if widget is not None else layout.addStretch()
    return bar


def _search(placeholder):
    widget = getattr(_common, "SearchBox", QLineEdit)()
    widget.setPlaceholderText(placeholder)
    widget.setClearButtonEnabled(True)
    widget.setAccessibleName(placeholder)
    return widget


def _empty(name, title, text="", actions=()):
    cls = getattr(_common, "EmptyState", None)
    if cls:
        return cls(name, title, text, actions=actions)
    widget = QWidget()
    widget.setProperty("emptystate", True)
    layout = QVBoxLayout(widget)
    label = QLabel(title + "\n" + text)
    label.setWordWrap(True)
    layout.addWidget(label)
    for caption, callback, primary in actions:
        layout.addWidget(_button(caption, callback, primary=primary))
    return widget


def _setup(view):
    setup = getattr(_common, "setup_table", None)
    if setup:
        setup(view, sortable=False)
    else:
        view.verticalHeader().hide()
        view.verticalHeader().setDefaultSectionSize(30)
        view.setAlternatingRowColors(True)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.horizontalHeader().setStretchLastSection(True)


def _fit(view):
    fit = getattr(_common, "fit_columns", None)
    if fit:
        fit(view)
    else:
        view.resizeColumnsToContents()
        for column in range(view.model().columnCount()):
            view.setColumnWidth(column, max(90, min(360, view.columnWidth(column) + 16)))


class _ProjectModel(DictTableModel):
    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and index.column() == 1 and role == Qt.DecorationRole:
            return icon("wrench" if self.rows[index.row()].get("mode") == "mantenimiento" else "hard-hat")
        return super().data(index, role)


class ProjectsPage(Page):
    title = "Proyectos"
    subtitle = "Crea un programa desde tus tareas o retoma un proyecto"
    _creation_key = "projects:create"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.root.setSpacing(_SPACE["md"])
        self._refresh_ticket = 0
        self._wizard_running = False
        self.demo_n = QComboBox()
        for n in (2000, 10000, 20000, 50000, 100000):
            self.demo_n.addItem(f"{n:,} actividades".replace(",", "."), n)
        self.demo_n.setCurrentIndex(2)
        self.wizard_btn = _button("Nuevo desde lista de tareas", self.from_task_list, primary=True, icon_name="wand-sparkles")
        self.new_btn = _button("Nuevo proyecto", self.new_project, icon_name="plus")
        self.excel_btn = _button("Crear desde Excel…", self.from_excel, icon_name="file-spreadsheet")
        self.demo_project_btn = _button("Construcción S/E", lambda: self.demo("proyecto"), icon_name="hard-hat")
        self.demo_maintenance_btn = _button("Mantenimiento", lambda: self.demo("mantenimiento"), icon_name="wrench")
        # Se conservan los botones públicos; las demos se presentan en un menú compacto.
        self.demo_project_btn.setParent(self)
        self.demo_maintenance_btn.setParent(self)
        self.demo_project_btn.hide()
        self.demo_maintenance_btn.hide()
        self.demo_menu = QToolButton()
        self.demo_menu.setText("Demos")
        self.demo_menu.setIcon(icon("rocket"))
        self.demo_menu.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.demo_menu)
        menu.addAction(icon("hard-hat"), "Construcción S/E", lambda: self.demo("proyecto"))
        menu.addAction(icon("wrench"), "Mantenimiento", lambda: self.demo("mantenimiento"))
        self.demo_menu.setMenu(menu)
        self.duplicate_btn = _button("Duplicar", self.duplicate, icon_name="copy")
        self._creation_buttons = (self.wizard_btn, self.new_btn, self.excel_btn, self.demo_menu,
                                  self.demo_project_btn, self.demo_maintenance_btn, self.duplicate_btn)
        self.backups_btn = _button("Respaldos…", self.show_backups, icon_name="history", kind="ghost")
        self.root.addWidget(_toolbar(self.wizard_btn, self.new_btn, self.excel_btn, None, self.backups_btn))
        self.search = _search("Buscar proyectos…")
        self.root.addWidget(_toolbar(self.search, None, self.demo_menu, self.demo_n))
        self.model = _ProjectModel([
            Column("name", "Nombre", width=380),
            Column("mode", "Modo", kind="choice", choices=[("proyecto", "Proyecto"), ("mantenimiento", "Mantenimiento")]),
            Column("start_date", "Inicio", kind="date"),
            Column("end_date", "Fin / horizonte", kind="date"),
            Column("data_date", "Fecha de corte", kind="date"),
            Column("activities", "Actividades", kind="int", get=lambda r: (r.get("counts") or {}).get("activities")),
            Column("description", "Descripción"),
        ])
        self.proxy = SortProxy()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        _setup(self.table)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 380)
        self.table.doubleClicked.connect(lambda *_: self.open_selected())
        card = Card("Proyectos")
        card.header.addWidget(_button("Abrir", self.open_selected, icon_name="folder-open"))
        card.header.addWidget(self.duplicate_btn)
        card.header.addWidget(_button("Editar", self.edit, icon_name="pencil"))
        card.header.addWidget(_button("Eliminar", self.delete, danger=True, icon_name="trash-2"))
        card.add(self.table, stretch=1)
        self.empty = _empty("folder-plus", "Aún no hay proyectos", "Comienza con una lista de tareas o crea un proyecto.",
                            [("Nuevo desde lista de tareas", self.from_task_list, True),
                             ("Nuevo proyecto", self.new_project, False), ("Crear desde Excel…", self.from_excel, False)])
        card.add(self.empty)
        self.no_matches = QLabel("No hay proyectos que coincidan con la búsqueda.")
        self.no_matches.setProperty("role", "caption")
        card.add(self.no_matches)
        self.search.textChanged.connect(self.filter_rows)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.filter_rows()
        self.root.addWidget(card, 1)
        state.projects_changed.connect(self._mark_stale)
        state.operations_changed.connect(self._update_actions)
        self._update_actions()

        self.content_scroll = scroll_layout(self.root)
        self.root.addWidget(self.wizard_btn)

    def from_task_list(self):
        if self._wizard_running or self.state.is_busy(self._creation_key):
            return
        try:
            from ..wizard import run_task_list_wizard
        except ImportError:
            self.new_project()
            return
        self._wizard_running = True
        self._update_actions()
        try:
            pid = run_task_list_wizard(self.state, self, "proyecto")
            if pid and self.state.pid != pid:
                from ..app import main_window
                window = main_window()
                if window is not None:
                    window.open_project(pid)
            self.refresh()
        finally:
            self._wizard_running = False
            self._update_actions()

    def filter_rows(self, *_):
        self.proxy.setFilterFixedString(self.search.text().strip())
        self.empty.setVisible(self.model.rowCount() == 0)
        self.table.setVisible(self.proxy.rowCount() > 0)
        self.no_matches.setVisible(self.model.rowCount() > 0 and self.proxy.rowCount() == 0)

    def context_menu(self, position):
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        self.table.setCurrentIndex(index)
        menu = QMenu(self)
        for text, callback, name in (("Abrir", self.open_selected, "folder-open"),
                                     ("Duplicar", self.duplicate, "copy"), ("Editar", self.edit, "pencil"),
                                     ("Eliminar", self.delete, "trash-2")):
            menu.addAction(icon(name), text, callback)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def on_theme(self):
        self.demo_menu.setIcon(icon("rocket"))
        for action, name in zip(self.demo_menu.menu().actions(), ("hard-hat", "wrench")):
            action.setIcon(icon(name))
        self.table.viewport().update()

    # ------------------------------------------------------------------ datos
    def refresh(self):
        self._refresh_ticket += 1
        ticket = self._refresh_ticket

        def done(rows):
            if ticket != self._refresh_ticket:
                return
            self.model.set_rows(rows)
            self.filter_rows()
            _fit(self.table)
            self.table.setColumnWidth(1, self.table.columnWidth(1) + 24)

        def failed(exc):
            if ticket == self._refresh_ticket:
                self._stale = True
                show_error(exc, self)
        run_task(self.state.client.projects, done, failed)

    @Slot()
    def _update_actions(self):
        enabled = not self._wizard_running and not self.state.is_busy(self._creation_key)
        for btn in self._creation_buttons:
            btn.setEnabled(enabled)

    def _creation_failed(self, exc):
        self.state.end(self._creation_key)
        show_error(exc, self)

    def _create_task(self, fn, on_done, busy):
        """La guarda ya está adquirida, incluso durante los diálogos previos."""
        def done(result):
            try:
                on_done(result)
            finally:
                self.state.end(self._creation_key)
        try:
            run_task(fn, done, self._creation_failed, busy=busy)
        except Exception:
            self.state.end(self._creation_key)
            raise

    def selected(self) -> dict | None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        return self.model.row_dict(self.proxy.mapToSource(idx).row())

    # ------------------------------------------------------------------ acciones
    def _fields(self, calendars=None):
        cals = [(None, "(por defecto: Chile L-V 8h)")] + [(c["id"], c["name"]) for c in
                (self.state.calendars if calendars is None else calendars)]
        return [
            Field("name", "Nombre", span=True),
            Field("mode", "Modo", "choice", [("proyecto", "Proyecto (CPM, dependencias, recursos)"), ("mantenimiento", "Mantenimiento (actividades periódicas)")]),
            Field("calendar_id", "Calendario", "choice", cals),
            Field("start_date", "Fecha de inicio", "date"),
            Field("end_date", "Término / fin del horizonte (obligatorio en mantenimiento)", "date"),
            Field("currency", "Moneda"),
            Field("description", "Descripción", "multiline", span=True),
        ]

    @staticmethod
    def _validate(v: dict) -> str | None:
        if not v.get("name"):
            return "Ingresa un nombre para el proyecto."
        if not v.get("start_date"):
            return "Indica la fecha de inicio."
        if v.get("mode") == "mantenimiento" and not v.get("end_date"):
            return "En modo mantenimiento debes indicar el fin del horizonte."
        if v.get("end_date") and v["end_date"] < v["start_date"]:
            return "El término no puede ser anterior al inicio."
        return None

    def new_project(self):
        if not self.state.try_begin(self._creation_key):
            return

        def ready(calendars):
            submitted = False
            try:
                dlg = FormDialog("Nuevo proyecto", self._fields(calendars),
                                 {"mode": "proyecto", "start_date": date.today().isoformat(), "currency": "CLP"},
                                 self, validate=self._validate)
                if dlg.exec():
                    v = dlg.values()
                    self._create_task(lambda: self.state.client.create_project(v), self._created, "Creando proyecto…")
                    submitted = True
            finally:
                if not submitted:
                    self.state.end(self._creation_key)
        run_task(self.state.client.calendars, ready, self._creation_failed)

    def show_backups(self):
        from ..backups_dialog import BackupsDialog
        dialog = BackupsDialog(self.state, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.open()
        return dialog

    def edit(self):
        p = self.selected()
        if not p:
            return
        # A3-04: cargar el catálogo de calendarios antes de editar (igual que en «Nuevo proyecto»); si la
        # lectura falla se edita con lo que haya en memoria y el valor guardado se conserva igual.
        run_task(self.state.client.calendars, lambda cals: self._edit_with(p, cals),
                 lambda _exc: self._edit_with(p, None))

    def _edit_with(self, p: dict, calendars):
        dlg = FormDialog(f"Editar {p['name']}", self._fields(calendars), p, self, validate=self._validate)
        if dlg.exec():
            v = dlg.values()
            payload = {k: v[k] for k in ("name", "mode", "calendar_id", "start_date", "end_date", "currency", "description")}

            def done(_):
                notify("Proyecto actualizado")
                self.refresh()
                if self.state.pid == p["id"]:
                    # La escritura ya fue confirmada; un fallo aquí es de lectura.
                    self.state.open_project_async(p["id"])
            run_task(lambda: self.state.client.update_project(p["id"], payload), done)

    def _created(self, p: dict):
        notify(f"Proyecto creado: {p['name']}")
        self.state.projects_changed.emit()
        from ..app import main_window
        main_window().open_project(p["id"])

    def demo(self, kind: str):
        if not self.state.try_begin(self._creation_key):
            return
        n = self.demo_n.currentData() if kind == "mantenimiento" else None
        label = f"Generando plan de mantenimiento de {n:,} actividades…".replace(",", ".") if n else "Creando proyecto de demostración…"
        self._create_task(lambda: self.state.client.demo(kind, n), self._created, label)

    def open_selected(self):
        p = self.selected()
        if p:
            from ..app import main_window
            main_window().open_project(p["id"])

    def duplicate(self):
        p = self.selected()
        if p and self.state.try_begin(self._creation_key):
            self._create_task(lambda: self.state.client.duplicate_project(p["id"]),
                              lambda np: (notify(f"Copia creada: {np['name']}"), self.refresh()), "Duplicando…")

    def delete(self):
        p = self.selected()
        if not p:
            return
        if not confirm(self, "Eliminar proyecto", f"Se eliminará «{p['name']}» con todas sus actividades, relaciones, líneas base y avances.\nEsta acción no se puede deshacer.", danger=True):
            return

        def done(_):
            notify("Proyecto eliminado")
            if self.state.pid == p["id"]:
                self.state.open_project(None)
            self.refresh()
        run_task(lambda: self.state.client.delete_project(p["id"]), done)

    def from_excel(self):
        if not self.state.try_begin(self._creation_key):
            return
        submitted = False
        try:
            path, _ = QFileDialog.getOpenFileName(self, "Crear proyecto desde Excel", "", "Libros Excel (*.xlsx)")
            if path:
                self._create_task(lambda: self.state.client.import_new_project(path), self._created, "Importando libro…")
                submitted = True
        finally:
            if not submitted:
                self.state.end(self._creation_key)
