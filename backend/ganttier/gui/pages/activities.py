"""Activities and Gantt page: asynchronous API workflows and virtual presentation."""
from __future__ import annotations

from collections import deque
from datetime import date
import json
import math
import re

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QLabel, QLineEdit, QMenu, QToolButton,
                               QSplitter, QMessageBox, QAbstractItemView, QApplication,
                               QWidget, QFrame, QVBoxLayout, QHBoxLayout, QButtonGroup, QScrollArea)

from .base import Page
from ..state import app_settings
from ..client import ApiError
from ..widgets.common import Field, FormDialog, button, confirm, hbox, notify, run_task, show_error, fmt_date, fmt_num, history_warning
from ..gantt.model import ActivityArrays, RowModel, RelationIndex, day, day_iso
from ..gantt.view import GanttWidget, SCALES
from ..gantt.loading import cargar
from ..gantt.profile import ResourceProfile
from ..gantt.dialogs import ActivityDetail, WbsDialog, activity_fields
from ..gantt.editing import validate_activity, detail_patch, scheduled_dates, warning_groups
from ..gantt.dialogs import operation_gate, status_text, method_text, solve_report, show_infeasible
from ..theme import tokens
from ..widgets.icons import icon
from ..widgets.responsive import HeaderFlowLayout, scroll_layout
from ..widgets import common as _common


# Adaptadores transitorios del contrato UX1. Las otras páginas UX5 los reutilizan
# para no duplicar respaldos; no se modifica ningún widget compartido.
def ux_button(text, slot=None, *, icon=None, kind=None, shortcut=None, **kwargs):
    import inspect
    if "icon" in inspect.signature(_common.button).parameters:
        return _common.button(text, slot, icon=icon, kind=kind, shortcut=shortcut, **kwargs)
    widget = _common.button(text, slot, **kwargs)
    if icon:
        from ..widgets.icons import icon as make_icon
        widget.setIcon(make_icon(icon))
    if kind:
        widget.setProperty("kind", kind)
    if shortcut:
        action = QAction(widget)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        action.triggered.connect(widget.click)
        widget.addAction(action)
    return widget


class _Toolbar(QWidget):
    def __init__(self):
        super().__init__()
        self.setProperty("toolbar", True)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(4)

    def add(self, widget):
        self.row.addWidget(widget)

    def add_stretch(self):
        self.row.addStretch()

    def add_separator(self):
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        self.add(line)

    def add_action(self, text, slot, **kwargs):
        widget = ux_button(text, slot, **kwargs)
        self.add(widget)
        return widget


class _EmptyState(QWidget):
    def __init__(self, icon_name, title, text="", actions=()):
        super().__init__()
        self.setProperty("emptystate", True)
        layout = QVBoxLayout(self)
        layout.addStretch()
        picture = QLabel()
        picture.setPixmap(icon(icon_name).pixmap(40, 40))
        picture.setAlignment(Qt.AlignCenter)
        layout.addWidget(picture)
        self.heading, self.description = QLabel(), QLabel()
        self.heading.setTextFormat(Qt.PlainText)
        self.description.setTextFormat(Qt.PlainText)
        self.heading.setProperty("role", "section")
        self.description.setProperty("role", "caption")
        for label in (self.heading, self.description):
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label)
        self.set_text(title, text)
        for label, callback, primary in actions:
            layout.addWidget(ux_button(label, callback, primary=primary), alignment=Qt.AlignHCenter)
        layout.addStretch()

    def set_text(self, title, text=""):
        self.heading.setText(title)
        self.description.setText(text)
        self.setAccessibleName(title)


class _StatusBanner(QFrame):
    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        self.symbol, self.message = QLabel(), QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        row.addWidget(self.symbol)
        row.addWidget(self.message, 1)

    def set(self, kind, title, text=""):
        self.setProperty("kind", kind)
        color = getattr(tokens(), kind, tokens().accent)
        self.symbol.setPixmap(icon({"good": "circle-check", "critical": "circle-x",
                                   "warning": "triangle-alert"}.get(kind, "info"), color).pixmap(18, 18))
        self.message.setText(title + (" · " + text if text else ""))
        self.setAccessibleName(title)
        self.setStyleSheet(f"QFrame {{border: 1px solid {color}; border-radius: 8px;}} QLabel {{border: none;}}")


class _Section(QFrame):
    def __init__(self, title, subtitle=None):
        super().__init__()
        self.setProperty("card", True)
        self.body = QVBoxLayout(self)
        self._actions = _Toolbar()
        self._actions.add(QLabel(title))
        self._actions.add_stretch()
        self.body.addWidget(self._actions)

    @property
    def actions(self):
        return self._actions


Toolbar = getattr(_common, "Toolbar", _Toolbar)
SearchBox = getattr(_common, "SearchBox", QLineEdit)
EmptyState = getattr(_common, "EmptyState", _EmptyState)
StatusBanner = getattr(_common, "StatusBanner", _StatusBanner)
Section = getattr(_common, "Section", _Section)


def setup_table(view, **kwargs):
    if hasattr(_common, "setup_table"):
        return _common.setup_table(view, **kwargs)
    view.verticalHeader().hide()
    view.verticalHeader().setDefaultSectionSize(30)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setAlternatingRowColors(True)


def fit_columns(view, **kwargs):
    if hasattr(_common, "fit_columns"):
        return _common.fit_columns(view, **kwargs)
    view.resizeColumnsToContents()
    for col in range(view.model().columnCount()):
        view.setColumnWidth(col, min(360, max(56, view.columnWidth(col))))


def toast(text, kind="ok", **kwargs):
    if hasattr(_common, "toast"):
        return _common.toast(text, kind, **kwargs)
    notify(text, kind)


def paste_from_clipboard(parent, title, fields):
    try:
        from ..widgets.paste import paste_from_clipboard as paste
    except ImportError:
        notify("El pegado desde Excel aún no está disponible. Puedes agregar tareas o importar una plantilla.", "info")
        return None
    return paste(parent, title, fields)


def parse_predecessors(text):
    try:
        from ..widgets.paste import parse_predecessors as parse
    except ImportError:
        result = []
        for part in re.split(r"[;,\n]+", text or ""):
            if not part.strip():
                continue
            match = re.fullmatch(r"(.+?)\s+(FS|SS|FF|SF)\s*([+-]\s*\d+)?", part.strip(), re.I)
            if match:
                code, relation, lag = match.groups()
            else:
                match = re.fullmatch(r"([^\s+;,]+?)(?:\s*([+]\s*\d+)|\s+([-]\s*\d+))?", part.strip())
                if not match:
                    raise ValueError(f"Predecesora inválida: {part}")
                code, positive, negative = match.groups()
                relation, lag = "FS", positive or negative
            lag = (lag or "0").replace(" ", "")
            result.append(dict(code=code, type=(relation or "FS").upper(), lag_days=int(lag or 0)))
        return result
    return parse(text)


def run_task_list_wizard(state, parent=None, mode="proyecto"):
    try:
        from ..wizard import run_task_list_wizard as wizard
    except ImportError:
        notify("El asistente aún no está disponible. Puedes agregar tareas o importar una plantilla.", "info")
        return None
    return wizard(state, parent=parent, mode=mode)


def import_template():
    from ..app import main_window
    if main_window():
        main_window().go("importar")




class ActivitiesPage(Page):
    title = "Actividades y Gantt"
    subtitle = "Define tareas y dependencias, genera el programa y revisa su ruta crítica."

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.arrays = ActivityArrays(dict(ids=[], codes=[], names=[], starts=[], finishes=[]))
        self.wbs, self.rel_list = [], []
        self.collapsed = set()
        self.group_wbs, self.auto_cpm, self.sort = True, True, "none"
        self._request = self._row_request = self._calendar_request = 0
        self._loaded_pid = None
        self._filter_pid = None
        self.loading = False
        self._queue = deque()
        self._operation_keys = set()
        self._gate = operation_gate(state)
        self.mutating = False
        self.last_summary = None
        self._calendar_pending = None
        self._calendar_extent = None
        self.gantt = GanttWidget()
        self.model = self.gantt.model
        self.profile = ResourceProfile(state, self.gantt)
        self.profile.hide()
        self.search = SearchBox()
        self.search.setPlaceholderText("Buscar código, nombre, habilidad o instalación…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(180)
        self.only_critical = QCheckBox("Solo críticas")
        self.only_critical.setIcon(icon("flag"))
        self.only_critical.setProperty("kind", "ghost")
        self.only_outage = QCheckBox("Desconexión")
        self.only_outage.setIcon(icon("zap"))
        self.only_outage.setProperty("kind", "ghost")
        self.only_outage.setToolTip("Mostrar solo actividades que requieren desconexión")
        # P4-03: vistas guardadas del Gantt (filtros, columnas, orden, capas y escala).
        self.view_combo = QComboBox()
        self.view_combo.setToolTip("Vistas guardadas: filtros, columnas, orden, capas y escala")
        self.view_combo.setAccessibleName("Vista guardada")
        self.view_combo.setMinimumContentsLength(9)
        self.view_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.scale = QComboBox(self)
        self.scale.hide()
        for label, width in SCALES.items():
            self.scale.addItem(label, width)
        self.scale.setCurrentText("Semana")
        self.toolbar = Toolbar()
        self.generate_button = self.toolbar.add_action("Generar programa", self.generate, icon="wand-sparkles", primary=True)
        self.cpm_button = self.toolbar.add_action("Programar (CPM)", self.cpm, icon="play", tip="F9")
        self.optimize_button = self.toolbar.add_action("Optimizar", self.optimize, icon="sliders-horizontal")
        self.toolbar.add_separator()
        self.new_button = self.toolbar.add_action("Actividad", self.new_activity, icon="plus")
        self.paste_button = self.toolbar.add_action("Pegar desde Excel", self.paste_activities, icon="clipboard-paste")
        self.detail_button = self.toolbar.add_action("Detalle", self.open_detail, icon="pencil", tip="Enter")
        self.bulk_button = self.toolbar.add_action("Editar en lote", self.bulk_edit, icon="list-checks",
                                                   tip="Cambiar campos de todas las actividades filtradas · Ctrl+Shift+E")
        self.delete_button = self.toolbar.add_action("Eliminar", self.delete_selected, icon="trash-2", kind="ghost", danger=True)
        self.delete_button.setProperty("danger", True)
        self.detail_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        # Se conserva el QPushButton público que usan las pruebas; la acción vive en Más.
        self.baseline_button = ux_button("Línea base", self.baseline, icon="flag")
        self.baseline_button.setParent(self)
        self.baseline_button.hide()
        self._gate.changed.connect(self._operation_buttons)
        state.project_changed.connect(self._operation_buttons)
        self._operation_buttons()
        self.view_menu = QMenu(self)
        self.view_button = QToolButton(self)
        self.view_button.hide()
        self.view_button.setText("Vista")
        self.view_button.setMenu(self.view_menu)
        self.view_button.setPopupMode(QToolButton.InstantPopup)
        self.view_actions = {}
        options = [("group", "Agrupar por WBS", True, self._set_group),
                   ("baseline", "Línea base", True, lambda v: self._set_layer("show_baseline", v)),
                   ("windows", "Ventanas macro", True, lambda v: self._set_layer("show_windows", v)),
                   ("relations", "Relaciones", True, lambda v: self._set_layer("show_relations", v)),
                   ("profile", "Perfil de recursos", False, self.profile.setVisible),
                   ("cpm", "Recalcular CPM al editar", True, lambda v: setattr(self, "auto_cpm", v))]
        for key, label, checked, slot in options:
            action = self.view_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(checked)
            action.toggled.connect(slot)
            self.view_actions[key] = action
        self.order_actions = {}
        order = self.view_menu.addMenu("Orden")
        order_group = QActionGroup(self)
        order_group.setExclusive(True)
        for key, label in (("none", "Original"), ("codigo", "Código"), ("nombre", "Nombre"), ("inicio", "Inicio")):
            action = order.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self.sort)
            order_group.addAction(action)
            action.triggered.connect(lambda checked=False, value=key: self.set_sort(value))
            self.order_actions[key] = action
        self.view_menu.addAction("Contraer todo", self.collapse_all)
        self.view_menu.addAction("Expandir todo", self.expand_all)
        self.column_actions = []
        columns = self.view_menu.addMenu("Columnas")
        for i, label in enumerate(("Código", "Nombre", "Duración", "Inicio", "Término", "Avance", "HH", "Mes macro", "Holgura / habilidad", "Instalación")):
            action = columns.addAction(label)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(lambda visible, col=i: self.gantt.table.setColumnHidden(col, not visible))
            self.column_actions.append(action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(icon("calendar-days"), "Hoy", lambda: self.gantt.jump_to_day(day(date.today())))
        self.view_menu.addAction(icon("target"), "Ir a selección", self.gantt.jump_to_selection)
        self.view_menu.addAction(icon("refresh-cw"), "Actualizar", self.refresh)
        self.more_button = QToolButton()
        self.more_button.setText("Más")
        self.more_button.setIcon(icon("ellipsis"))
        self.more_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        more = QMenu(self.more_button)
        more.addAction(icon("file-spreadsheet"), "Exportar Gantt a Excel…", self.export_gantt)
        more.addAction(icon("layers"), "WBS", self.manage_wbs)
        self.chain_action = more.addAction(icon("link"), "Crear cadena de relaciones…", self.relation_chain)
        self.baseline_action = more.addAction(icon("flag"), "Línea base", self.baseline)
        more.addMenu(self.view_menu).setText("Vista")
        more.addSeparator()
        more.addAction(icon("save"), "Guardar vista actual…", self.save_view_dialog)
        self.delete_view_action = more.addAction(icon("trash-2"), "Eliminar vista seleccionada", self.delete_current_view)
        self.more_button.setMenu(more)
        self.toolbar.add(self.more_button)
        self.toolbar.add(self.view_combo)
        self.toolbar.add(self.search)
        self.toolbar.add(self.only_critical)
        self.toolbar.add(self.only_outage)
        self.scale_buttons = QButtonGroup(self)
        self.scale_buttons.setExclusive(True)
        for i, (letter, label) in enumerate(zip("DSMTA", SCALES)):
            segment = ux_button(letter, kind="ghost", tip=label)
            segment.setCheckable(True)
            segment.setFixedWidth(28)
            segment.setAccessibleName("Escala " + label)
            self.scale_buttons.addButton(segment, i)
            self.toolbar.add(segment)
        self.scale_buttons.idClicked.connect(self.scale.setCurrentIndex)
        self.scale.currentIndexChanged.connect(self._sync_scale)
        self._sync_scale()
        toolbar_scroll = QScrollArea()
        toolbar_scroll.setFrameShape(QFrame.NoFrame)
        toolbar_scroll.setWidgetResizable(True)
        toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        toolbar_scroll.setFixedHeight(58)
        toolbar_scroll.setWidget(self.toolbar)
        self.root.addWidget(toolbar_scroll)
        self.empty_state = EmptyState("list-todo", "Aún no hay tareas",
            "Empieza con una lista, pega tu planilla o agrega la primera actividad.", actions=[
                ("Asistente lista de tareas", self.task_wizard, True),
                ("Pegar desde Excel", self.paste_activities, False),
                ("Agregar actividad", self.new_activity, False),
                ("Importar plantilla", import_template, False)])
        self.empty_state.hide()
        self.root.addWidget(self.empty_state, 1)
        # A3-01: la tabla debe conservar la misma altura de fila que el lienzo de barras.
        from ..gantt.view import ROW_HEIGHT as GANTT_ROW_HEIGHT
        setup_table(self.gantt.table, sortable=False, row_height=GANTT_ROW_HEIGHT)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.hide()
        self.summary_button = button("Ver resumen y advertencias", self.show_summary)
        self.summary_button.hide()
        self.root.addLayout(hbox(self.summary, self.summary_button))
        self.vertical_splitter = QSplitter(Qt.Vertical)
        self.vertical_splitter.setChildrenCollapsible(False)
        self.vertical_splitter.addWidget(self.gantt)
        self.vertical_splitter.addWidget(self.profile)
        self.vertical_splitter.setSizes([540, 190])
        self.root.addWidget(self.vertical_splitter, 1)
        self.status = QLabel("Sin actividades")
        self.status.setProperty("role", "caption")
        self.status_badges = {}
        footer_widget = QWidget()
        footer = HeaderFlowLayout(footer_widget, minimum_height=0)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(6)
        for key in ("activities", "critical", "relations", "progress"):
            badge = QLabel()
            badge.setProperty("badge", True)
            self.status_badges[key] = badge
            footer.addWidget(badge)
        footer.addWidget(self.status)
        self.root.addWidget(footer_widget)
        # El pie se distribuye en filas y los paneles se desplazan cuando la
        # altura disponible es pequeña (DPI alto o perfil de recursos abierto).
        # La barra de acciones conserva su desplazamiento horizontal propio.
        self.content_scroll = scroll_layout(self.root, first=1)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(self.rebuild_rows)
        self.search.textChanged.connect(lambda *_: self.filter_timer.start())
        self.only_critical.toggled.connect(lambda *_: self.rebuild_rows())
        self.only_outage.toggled.connect(lambda *_: self.rebuild_rows())
        self._populate_views()
        self.view_combo.activated.connect(self._view_selected)
        self.scale.currentIndexChanged.connect(lambda *_: self.gantt.set_day_width(self.scale.currentData()))
        self.gantt.edit_requested.connect(self.edit_cell)
        self.gantt.move_requested.connect(self.move_activity)
        self.gantt.resize_requested.connect(self.resize_activity)
        self.gantt.link_requested.connect(self.create_relation)
        self.gantt.toggle_wbs.connect(self.toggle_wbs)
        self.gantt.selection_changed.connect(self._selection)
        self.gantt.invalid_edit.connect(lambda text: notify(text, "error"))
        self.gantt.viewport_changed.connect(self._viewport)
        self.calendar_timer = QTimer(self)
        self.calendar_timer.setSingleShot(True)
        self.calendar_timer.setInterval(150)
        self.calendar_timer.timeout.connect(self.load_calendar)
        for key, slot in (("Ctrl+G", self.generate), ("F9", self.cpm), ("Return", self.open_detail), ("Delete", self.delete_selected),
                          ("Ctrl+Shift+E", self.bulk_edit)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            action.triggered.connect(lambda checked=False, fn=slot: self._shortcut(fn))
            self.addAction(action)
        self.generate_button.setToolTip("Generar programa · Ctrl+G")
        state.catalogs_changed.connect(self._catalogs_changed)
        self._page_theme()

    def _shortcut(self, fn):
        if QApplication.activeModalWidget() is not None:
            return
        if self.gantt.table.state() == QAbstractItemView.EditingState:
            return
        focus = QApplication.focusWidget()
        if focus in (self.gantt.table, self.gantt.canvas, self.gantt.table.viewport()) or fn in (self.cpm, self.generate):
            index = self.gantt.table.currentIndex()
            if fn == self.open_detail and index.isValid():
                i = int(self.gantt.rows.rows[index.row()])
                if i < 0:
                    self.toggle_wbs(self.gantt.rows.groups[-i - 1]["node"]["id"])
                    return
            fn()

    def _sync_scale(self, *_):
        selected = self.scale_buttons.button(self.scale.currentIndex())
        if selected:
            selected.setChecked(True)

    def actions(self):
        return [("Generar programa", "wand-sparkles", self.generate, "Ctrl+G"),
                ("Programar (CPM)", "play", self.cpm, "F9"),
                ("Optimizar", "sliders-horizontal", self.optimize, None),
                ("Agregar actividad", "plus", self.new_activity, None),
                ("Pegar desde Excel", "clipboard-paste", self.paste_activities, None),
                ("Detalle", "pencil", self.open_detail, "Enter"),
                ("Eliminar actividad", "trash-2", self.delete_selected, None),
                ("Editar actividades en lote", "list-checks", self.bulk_edit, "Ctrl+Shift+E"),
                ("Guardar vista actual del Gantt", "save", self.save_view_dialog, None),
                ("Crear cadena de relaciones", "link", self.relation_chain, None)]

    def export_gantt(self):
        from ..gantt_export import open_gantt_export
        open_gantt_export(self.state, self)

    def relation_chain(self):
        """P5-09: relaciones consecutivas desde una lista ordenada de códigos (solo modo Proyecto)."""
        if self.state.pid is None:
            return
        if self.state.mode != "proyecto":
            notify("Las cadenas de relaciones están disponibles en modo Proyecto.", "info")
            return
        from ..tools_dialogs import ChainDialog
        options = self.options()
        initial = []
        if options["text"] or options["critical"] or options["outage"]:
            from ..gantt.bulk_edit import filtered_ids
            ids = set(filtered_ids(self.arrays, options["text"], options["critical"], options["outage"]))
            initial = [str(self.arrays.codes[i]) for i in np.argsort(np.nan_to_num(self.arrays.start, nan=np.inf))
                       if int(self.arrays.ids[i]) in ids][:250]
        dialog = ChainDialog(self.state, self, initial)
        if dialog.exec() and dialog.applied and dialog.applied.get("created"):
            pid = self.state.pid
            self.mutate(lambda: None, pid=pid, notify_saved=False, operation_key=("chain", id(dialog)))

    # ------------------------------------------------------------------ P4-03 vistas guardadas
    VIEWS_KEY = "gantt/views"

    def _settings(self):
        settings = getattr(self.state, "settings", None)
        if settings is None:
            from PySide6.QtCore import QSettings
            settings = app_settings()
        return settings

    def saved_views(self) -> dict:
        raw = self._settings().value(self.VIEWS_KEY, "")
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _store_views(self, views: dict) -> None:
        self._settings().setValue(self.VIEWS_KEY, json.dumps(views, ensure_ascii=False))

    def current_view(self) -> dict:
        return dict(search=self.search.text(), critical=self.only_critical.isChecked(),
                    outage=self.only_outage.isChecked(), group=self.group_wbs, sort=self.sort,
                    hidden=[i for i, action in enumerate(self.column_actions) if not action.isChecked()],
                    scale=self.scale.currentIndex(),
                    layers={key: self.view_actions[key].isChecked() for key in ("baseline", "windows", "relations", "profile")})

    def apply_view(self, view: dict) -> None:
        """Aplica una vista; claves ausentes o columnas inexistentes se ignoran (vistas de versiones anteriores)."""
        for key, checked in (view.get("layers") or {}).items():
            if key in self.view_actions:
                self.view_actions[key].setChecked(bool(checked))
        self.view_actions["group"].setChecked(bool(view.get("group", True)))
        hidden = {int(i) for i in view.get("hidden", []) if str(i).isdigit()}
        for i, action in enumerate(self.column_actions):
            action.setChecked(i not in hidden)
        sort = view.get("sort", "none")
        if sort in self.order_actions:
            self.order_actions[sort].setChecked(True)
            self.set_sort(sort)
        scale = view.get("scale")
        if isinstance(scale, int) and 0 <= scale < self.scale.count():
            self.scale.setCurrentIndex(scale)
        for widget, value in ((self.only_critical, view.get("critical", False)), (self.only_outage, view.get("outage", False))):
            blocked = widget.blockSignals(True)
            widget.setChecked(bool(value))
            widget.blockSignals(blocked)
        self.search.setText(str(view.get("search", "")))
        self.rebuild_rows()

    def _populate_views(self, select: str | None = None) -> None:
        blocked = self.view_combo.blockSignals(True)
        try:
            self.view_combo.clear()
            self.view_combo.addItem("Vistas…", None)
            for name in sorted(self.saved_views(), key=str.casefold):
                self.view_combo.addItem(name, name)
            index = self.view_combo.findData(select) if select else 0
            self.view_combo.setCurrentIndex(max(0, index))
        finally:
            self.view_combo.blockSignals(blocked)
        if hasattr(self, "delete_view_action"):
            self.delete_view_action.setEnabled(self.view_combo.currentData() is not None)

    def _view_selected(self, index: int) -> None:
        name = self.view_combo.itemData(index)
        if hasattr(self, "delete_view_action"):
            self.delete_view_action.setEnabled(name is not None)
        view = self.saved_views().get(name) if name else None
        if view:
            self.apply_view(view)

    def save_view(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Escribe un nombre para la vista.")
        views = self.saved_views()
        views[name] = self.current_view()
        self._store_views(views)
        self._populate_views(select=name)

    def save_view_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        current = self.view_combo.currentData() or ""
        name, ok = QInputDialog.getText(self, "Guardar vista", "Nombre de la vista (p. ej. «Reunión semanal»):", text=current)
        if ok and name.strip():
            exists = name.strip() in self.saved_views() and name.strip() != current
            if exists and not confirm(self, "Reemplazar vista", f"Ya existe la vista «{name.strip()}». ¿Reemplazarla?"):
                return
            self.save_view(name)
            notify(f"Vista guardada: {name.strip()}")

    def delete_current_view(self) -> None:
        name = self.view_combo.currentData()
        if not name:
            return
        if confirm(self, "Eliminar vista", f"Se eliminará la vista «{name}». No cambia ninguna actividad.", danger=True):
            views = self.saved_views()
            views.pop(name, None)
            self._store_views(views)
            self._populate_views()

    # ------------------------------------------------------------------ P4-02 edición masiva
    def bulk_edit(self):
        pid = self.state.pid
        if pid is None or self.arrays.n == 0:
            notify("No hay actividades para editar.", "info")
            return
        from ..gantt.bulk_edit import BulkEditDialog, filtered_ids
        options = self.options()
        ids = filtered_ids(self.arrays, options["text"], options["critical"], options["outage"])
        dialog = BulkEditDialog(self.arrays, ids, self.gantt.selected_id, list(self.state.calendars or []),
                                self.state.mode or "proyecto", self)
        if not dialog.exec():
            return
        patches = dialog.patches()
        if not patches or pid != self.state.pid:
            return
        client = self.state.client
        count = len(patches)

        def done(_result):
            notify(f"Se actualizaron {fmt_num(count)} actividades")
        self.mutate(lambda: client.patch_activities(pid, patches), done, pid=pid,
                    operation_key=("bulk", json.dumps(patches[:3], sort_keys=True, default=str), count))

    def task_wizard(self):
        return run_task_list_wizard(self.state, parent=self, mode=self.state.mode)

    def paste_activities(self):
        pid = self.state.pid
        if not pid or self.loading or self.mutating:
            return
        fields = activity_fields(self.state, self.wbs, full=False)
        fields += [Field("predecessors", "Predecesoras", help="Códigos separados por ;. Ejemplo: A; B+2; C SS-1")]
        rows = paste_from_clipboard(self, "Pegar actividades desde Excel", fields)
        if rows and pid == self.state.pid:
            self.paste_rows(rows, pid=pid)

    def paste_rows(self, rows, *, pid=None):
        """Valida el lote completo antes de escribir; las relaciones usan IDs reales."""
        pid = self.state.pid if pid is None else pid
        if not pid or pid != self.state.pid:
            return
        client = self.state.client
        values = [dict(row) for row in rows]

        def work():
            existing = client.columnar(pid)
            ids = dict(zip(existing["codes"], existing["ids"]))
            payloads, dependencies, codes = [], {}, set()
            for number, values_row in enumerate(values, 1):
                row = dict(values_row)
                predecessors = row.pop("predecessors", row.pop("Predecesoras", "")) or ""
                row["code"] = str(row.get("code") or "").strip()
                row["name"] = str(row.get("name") or "").strip()
                message = validate_activity(row)
                if message:
                    raise ValueError(f"Fila {number}: {message}")
                code = row["code"]
                if code in ids or code in codes:
                    raise ValueError(f"Fila {number}: código duplicado «{code}».")
                codes.add(code)
                dependencies[code] = parse_predecessors(predecessors)
                payloads.append(row)
            known = codes | ids.keys()
            incoming = {code: 0 for code in codes}
            successors = {code: [] for code in codes}
            for code, predecessors in dependencies.items():
                seen = set()
                for predecessor in predecessors:
                    pred = predecessor["code"]
                    if pred not in known:
                        raise ValueError(f"{code}: predecesora desconocida «{pred}».")
                    if pred == code:
                        raise ValueError(f"{code}: una actividad no puede depender de sí misma.")
                    if pred in seen:
                        raise ValueError(f"{code}: predecesora repetida «{pred}».")
                    seen.add(pred)
                    if pred in codes:
                        incoming[code] += 1
                        successors[pred].append(code)
            ready = deque(code for code, count in incoming.items() if count == 0)
            visited = 0
            while ready:
                visited += 1
                for successor in successors[ready.popleft()]:
                    incoming[successor] -= 1
                    if incoming[successor] == 0:
                        ready.append(successor)
            if visited != len(codes):
                raise ValueError("Las predecesoras del lote forman un ciclo.")
            if not payloads:
                return dict(activities=0, relations=0)
            created = client.create_activities(pid, payloads)
            ids.update({row["code"]: row["id"] for row in created})
            count = 0
            try:
                for code, predecessors in dependencies.items():
                    for predecessor in predecessors:
                        client.create_relation(pid, dict(pred_id=ids[predecessor["code"]], succ_id=ids[code],
                            type=predecessor["type"], lag_days=predecessor["lag_days"]))
                        count += 1
            except Exception as exc:
                raise RuntimeError(f"Se crearon {len(created)} actividades y {count} relaciones. "
                                   f"El resto de las relaciones no se guardó: {exc}") from exc
            return dict(activities=len(created), relations=count)

        def done(result):
            self.last_paste_result = result
            toast(f"{fmt_num(result['activities'])} actividades y {fmt_num(result['relations'])} relaciones creadas")
        return self.mutate(work, done, recalc=False, pid=pid, notify_saved=False,
                           operation_key=("paste", json.dumps(values, sort_keys=True, default=str)))

    def _selection(self, aid):
        self.detail_button.setEnabled(aid is not None)
        self.delete_button.setEnabled(aid is not None)

    def options(self):
        return dict(grouped=self.group_wbs, collapsed=set(self.collapsed), text=self.search.text(),
                    critical=self.only_critical.isChecked(), sort=self.sort, outage=self.only_outage.isChecked())

    def refresh(self):
        self._request += 1
        request, pid, client = self._request, self.state.pid, self.state.client
        self._stale = False
        self._row_request += 1
        self._calendar_request += 1
        self._calendar_pending = self._calendar_extent = None
        self.gantt.calendar_days = {}
        changed = pid != self._loaded_pid
        if pid != self._filter_pid:
            self._filter_pid = pid
            self.filter_timer.stop()
            for widget in (self.search, self.only_critical, self.only_outage):
                blocked = widget.blockSignals(True)
                if widget is self.search:
                    widget.clear()
                else:
                    widget.setChecked(False)
                widget.blockSignals(blocked)
        if changed:
            self.collapsed.clear()
            self.gantt.selected_id = None
            self.arrays = ActivityArrays(dict(ids=[], codes=[], names=[], starts=[], finishes=[]))
            self.wbs, self.rel_list = [], []
            rows = RowModel(self.arrays)
            self.gantt.set_data(self.arrays, rows, RelationIndex([], self.arrays, rows), self.state.project or {}, reset=True)
            self.summary.hide()
            self.summary_button.hide()
            self.last_summary = None
        if pid is None:
            self._loaded_pid = None
            self.loading = False
            self.status.setText("Selecciona un proyecto")
            self.empty_state.hide()
            self.vertical_splitter.show()
            for badge in self.status_badges.values():
                badge.clear()
            return
        self.loading = True
        self.empty_state.hide()
        self.vertical_splitter.show()
        self.status.setText("Cargando actividades…")

        def work():
            return cargar(client, pid)

        def done(result):
            if request != self._request or pid != self.state.pid:
                return
            self.arrays, self.wbs, self.rel_list = result
            self._loaded_pid = pid
            self.rebuild_rows(reset=changed)
            self.profile.refresh()

        def failed(exc):
            if request == self._request and pid == self.state.pid:
                self.loading = False
                self.status.setText("No se pudo cargar el Gantt. Pulsa Actualizar para reintentar.")
                show_error(exc, self)
        run_task(work, done, failed, busy="Cargando Gantt…")

    def rebuild_rows(self, reset=False):
        self._row_request += 1
        version, pid = self._row_request, self.state.pid
        if pid is None:
            return
        a, wbs, rels, options = self.arrays, self.wbs, self.rel_list, self.options()
        project = dict(self.state.project)
        self.loading = True
        def work():
            rows = RowModel(a, wbs, **options)
            return rows, RelationIndex(rels, a, rows)
        def done(result):
            if version != self._row_request or pid != self.state.pid or a is not self.arrays:
                return
            rows, relations = result
            self.gantt.set_data(a, rows, relations, project, reset=reset)
            self.loading = False
            self.empty_state.setVisible(a.n == 0)
            self.vertical_splitter.setVisible(a.n > 0)
            weight = np.where(a.hh > 0, a.hh, np.maximum(1, np.nan_to_num(a.dur)))
            progress = float(np.sum(weight * a.pct) / weight.sum()) if a.n else 0
            for key, text in (("activities", f"{fmt_num(a.n)} actividades"),
                              ("critical", f"{fmt_num(np.count_nonzero(a.crit))} críticas"),
                              ("relations", f"{fmt_num(len(rels))} relaciones"),
                              ("progress", f"{fmt_num(progress, 1)} % avance ponderado")):
                self.status_badges[key].setText(text)
            self.status_badges["progress"].setToolTip("Ponderado por HH; sin HH, duración (mínimo 1 día).")
            self.status.setText(f"{fmt_num(rows.matched_count)} coinciden")
            if reset or getattr(self, "_columns_arrays", None) is not a:
                fit_columns(self.gantt.table, max_w=300, sample=40)
                self._columns_arrays = a
            self.calendar_timer.start()
            self._apply_focus()
        def failed(exc):
            if version == self._row_request:
                self.loading = False
                self.status.setText("No se pudo construir la vista. Pulsa Actualizar.")
                show_error(exc, self)
        run_task(work, done, failed)

    def focus_activity(self, aid: int) -> None:
        """P5-06: selecciona una actividad (búsqueda global) cuando el programa termine de cargar."""
        self._pending_focus, self._focus_cleared = int(aid), False
        if self._loaded_pid == self.state.pid and not self.loading:
            if self.arrays.id_to_index.get(int(aid)) is None:
                self.refresh()
            else:
                self._apply_focus()

    def _apply_focus(self) -> None:
        aid = getattr(self, "_pending_focus", None)
        if aid is None or self._loaded_pid != self.state.pid:
            return
        i = self.arrays.id_to_index.get(aid)
        if i is None:
            self._pending_focus = None
            notify("La actividad ya no existe en este proyecto.", "info")
            return
        if int(self.gantt.rows.row_of_act[i]) < 0 and not self._focus_cleared:
            # Oculta por un filtro o un grupo contraído: se muestran todas y se vuelve a intentar.
            self._focus_cleared = True
            for widget in (self.search, self.only_critical, self.only_outage):
                blocked = widget.blockSignals(True)
                widget.clear() if widget is self.search else widget.setChecked(False)
                widget.blockSignals(blocked)
            self.filter_timer.stop()
            self.collapsed.clear()
            self.rebuild_rows()
            return
        self._pending_focus = None
        self.gantt.select_id(aid)

    def _set_group(self, value):
        self.group_wbs = value
        self.rebuild_rows()

    def _set_layer(self, key, value):
        setattr(self.gantt, key, value)
        self.gantt.canvas.update()

    def set_sort(self, value):
        self.sort = value
        self.rebuild_rows()

    def toggle_wbs(self, wid):
        self.collapsed.symmetric_difference_update({wid})
        self.rebuild_rows()

    def collapse_all(self):
        self.collapsed = {w["id"] for w in self.wbs}
        self.rebuild_rows()

    def expand_all(self):
        self.collapsed.clear()
        self.rebuild_rows()

    def _viewport(self):
        self.calendar_timer.start()
        nearest = min(range(self.scale.count()), key=lambda i: abs(math.log(self.scale.itemData(i) / self.gantt.day_width)))
        self.scale.blockSignals(True)
        self.scale.setCurrentIndex(nearest)
        self.scale.blockSignals(False)
        self._sync_scale()

    def _catalogs_changed(self):
        self._calendar_extent = self._calendar_pending = None
        self._calendar_request += 1
        self.gantt.calendar_days.clear()
        self.calendar_timer.start()
        self.profile.refresh()

    def load_calendar(self):
        project = self.state.project or {}
        cid, pid = project.get("calendar_id"), self.state.pid
        if not cid or not self.isVisible():
            return
        first = math.floor(self.gantt.day_at(0))
        last = math.ceil(self.gantt.day_at(self.gantt.canvas.width()))
        if self._calendar_extent and self._calendar_extent[0] <= first and self._calendar_extent[1] >= last:
            return
        bounds = first - 30, last + 30
        key = pid, cid, bounds
        if self._calendar_pending == key:
            return
        self._calendar_pending = key
        self._calendar_request += 1
        request = self._calendar_request
        def done(days):
            if request != self._calendar_request or pid != self.state.pid:
                return
            self._calendar_pending = None
            self._calendar_extent = bounds
            self.gantt.calendar_days = {int(day(d["date"])): 0 if d["working"] else
                                       1 if not d.get("reason") or re.search("descanso|fin de semana", d["reason"], re.I) else 2
                                       for d in days}
            self.gantt.canvas.update()
        def failed(exc):
            if request == self._calendar_request:
                self._calendar_pending = None
                notify(f"No se pudo cargar el calendario: {exc}", "error")
        run_task(lambda: self.state.client.calendar_days(cid, day_iso(bounds[0]), day_iso(bounds[1])), done, failed)

    def _operation_buttons(self, *_):
        enabled = bool(self.state.pid) and not self._gate.is_busy(f"solve:{self.state.pid}")
        for widget in (self.generate_button, self.cpm_button, self.optimize_button, self.baseline_button):
            widget.setEnabled(enabled)
        if hasattr(self, "baseline_action"):
            self.baseline_action.setEnabled(enabled)

    def mutate(self, work, done=None, *, recalc=True, pid=None, finished=None, operation_key=None,
               notify_saved=True, local_update=None):
        """Serialize writes; capture project identity and report post-save CPM failures."""
        pid = self.state.pid if pid is None else pid
        if pid is None:
            return False
        key = (pid, operation_key) if operation_key is not None else None
        if key is not None:
            if key in self._operation_keys:
                return False
            self._operation_keys.add(key)
        do_cpm = recalc and self.auto_cpm and self.state.mode == "proyecto"
        # P4-10: una edición que no cambia fechas (sin CPM) puede reflejarse localmente sin volver a
        # descargar todo el programa; si falla, se recarga el estado canónico del servidor.
        light = local_update if (local_update is not None and not do_cpm) else None
        self._queue.append((pid, work, done, finished, do_cpm, key, notify_saved, light))
        if not self.mutating:
            self._next_mutation()
        return True

    def _next_mutation(self):
        if not self._queue:
            self.mutating = False
            return
        self.mutating = True
        pid, work, callback, finished, do_cpm, key, notify_saved, light = self._queue.popleft()
        client = self.state.client
        applied_locally = []
        def execute():
            result = work()
            summary, cpm_error = None, None
            if do_cpm:
                try:
                    summary = client.cpm(pid)
                except Exception as exc:
                    cpm_error = exc
            return result, summary, cpm_error
        def cleanup():
            self._operation_keys.discard(key)
            try:
                if finished:
                    finished()
            finally:
                try:
                    if pid == self.state.pid:
                        if applied_locally and self.isVisible() and not self._queue:
                            # Las demás páginas quedan obsoletas; esta ya muestra el cambio.
                            self._suppress_next = True
                            QTimer.singleShot(0, self._end_suppression)
                        self.state.bump_schedule()
                        if not self.isVisible():
                            self._stale = True
                finally:
                    self._next_mutation()
        def done(result):
            value, summary, cpm_error = result
            try:
                if callback and pid == self.state.pid:
                    callback(value)
                if light is not None and pid == self.state.pid:
                    try:
                        # A4-13: solo un reflejo local exitoso suprime la recarga canónica.
                        if light():
                            applied_locally.append(True)
                    except Exception:  # noqa: BLE001 - ante cualquier duda se recarga todo
                        applied_locally.clear()
                if pid == self.state.pid:
                    if summary:
                        self.set_summary(summary)
                    if cpm_error:
                        notify(f"Cambios guardados; CPM no pudo recalcularse: {cpm_error}", "error")
                        self.set_summary({"warnings": [f"Cambios guardados; CPM no pudo recalcularse: {cpm_error}"]})
                    elif notify_saved and not (isinstance(value, dict) and solve_report(value)[0] is False):
                        notify("Cambios guardados")
            finally:
                cleanup()
        def failed(exc):
            try:
                show_error(exc, self)
            finally:
                cleanup()  # Partial writes must still reload the canonical server state.
        try:
            run_task(execute, done, failed, busy="Guardando cambios…")
        except Exception:
            cleanup()
            raise

    def set_summary(self, result):
        self.last_summary = result
        warnings = result.get("warnings") or []
        self.warning_summary = warning_groups(warnings)
        parts = []
        if result.get("finish") or result.get("project_finish"):
            parts.append(f"Término: {fmt_date(result.get('finish') or result.get('project_finish'))}")
        if "critical_count" in result:
            parts.append(f"Críticas: {fmt_num(result['critical_count'])}")
        if result.get("status"):
            parts.append(f"Estado: {status_text(result['status'])}")
        if result.get("method_used"):
            parts.append(f"Método: {method_text(result['method_used'])}")
        if "created" in result:
            parts.append(f"Actividades generadas: {fmt_num(result['created'])}")
        feasible, violations = solve_report(result)
        parts.append("Factible: " + ("Sí" if feasible is True else "No" if feasible is False else "Sin verificar"))
        parts.append(f"Violaciones: {len(violations)}")
        parts.append(f"Advertencias: {len(warnings)}")
        if "applied" in result or feasible is False:
            parts.append("Aplicado" if result.get("applied") and feasible is not False else "Sin aplicar")
        if result.get("compatibility_fallback"):
            parts.append("El servidor no tiene /schedule/solve; se usó el método antiguo (CPM sin nivelación de recursos)")
        self.summary.setText(" · ".join(parts))
        self.summary.show()
        self.summary_button.show()

    def _solve_done(self, result):
        self.set_summary(result)
        feasible, _ = solve_report(result)
        if feasible is None and result.get("status") in ("infactible", "infeasible"):
            feasible = False
        finish = result.get("finish") or result.get("project_finish") or (result.get("after") or {}).get("finish")
        label = "Factible" if feasible is True else "Infactible" if feasible is False else "Sin verificar"
        toast(f"{label} · Término: {fmt_date(finish) or 'sin fecha'}",
              "error" if feasible is False else "ok", action_text="Ver detalle", action=self.show_summary)
        show_infeasible(self, result)

    def _schedule_mutation(self, pid, work, done=None, operation_key="solve"):
        key = f"solve:{pid}"
        if not self._gate.try_begin(key):
            return
        try:
            accepted = self.mutate(work, done, recalc=False, pid=pid, operation_key=operation_key,
                                   finished=lambda: self._gate.end(key), notify_saved=False)
            if not accepted:
                self._gate.end(key)
        except Exception:
            self._gate.end(key)
            raise

    def show_summary(self):
        if self.last_summary is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Resultado de programación")
        box.setText(self.summary.text())
        details = [f"{count} × {key}\n  " + "\n  ".join(examples) for key, count, examples in self.warning_summary]
        box.setDetailedText("\n\n".join(details) + "\n\n" + json.dumps(self.last_summary, ensure_ascii=False, indent=2))
        box.exec()

    def cpm(self):
        pid, client = self.state.pid, self.state.client
        if pid:
            self._schedule_mutation(pid, lambda: client.cpm(pid), self._solve_done, "cpm")

    def generate(self):
        pid, client = self.state.pid, self.state.client
        if not pid:
            return
        def work():
            try:
                return client.post(f"/projects/{pid}/schedule/solve")
            except ApiError as exc:
                if exc.status not in (404, 405):
                    raise
                result = dict(client.cpm(pid))
                result["warnings"] = list(result.get("warnings") or []) + [
                    "El servidor no tiene /schedule/solve; se usó el método antiguo (CPM sin nivelación de recursos)"]
                result["compatibility_fallback"] = True
                return result
        self._schedule_mutation(pid, work, self._solve_done)

    def optimize(self):
        if self._gate.is_busy(f"solve:{self.state.pid}"):
            return
        from ..app import main_window
        if main_window():
            main_window().go("optimizar")

    def baseline(self):
        pid, client = self.state.pid, self.state.client
        if not pid or self._gate.is_busy(f"solve:{pid}"):
            return
        dlg = FormDialog("Guardar línea base", [Field("name", "Nombre")],
                         {"name": f"Línea base {date.today():%d-%m-%Y}"}, self,
                         validate=lambda v: None if v["name"] else "Ingresa un nombre.")
        if dlg.exec():
            name = dlg.values()["name"]
            def work():
                baseline = client.create_baseline(pid, name)
                client.activate_baseline(baseline["id"])
                return client.project(pid)
            def done(project):
                if pid == self.state.pid:
                    self.state.project = project
                    notify("Línea base guardada")
            self._schedule_mutation(pid, work, done, "baseline")

    def new_activity(self):
        pid = self.state.pid
        if not pid:
            return
        dlg = FormDialog("Nueva actividad", activity_fields(self.state, self.wbs, full=False),
                         {"duration_days": 5, "work_hours": 0, "crew_size": 1}, self, validate=validate_activity)
        if dlg.exec():
            self.create_activity(dlg.values(), pid=pid)

    def create_activity(self, values, *, pid=None):
        pid = self.state.pid if pid is None else pid
        self.mutate(lambda: self.state.client.create_activities(pid, values), recalc=False, pid=pid,
                    operation_key=("create_activity", json.dumps(values, sort_keys=True, default=str)))

    def manage_wbs(self):
        if self.state.pid:
            dialog = WbsDialog(self)
            dialog.exec()

    def open_detail(self):
        aid, pid, client = self.gantt.selected_id, self.state.pid, self.state.client
        if aid is None or pid is None:
            return
        def done(result):
            if pid == self.state.pid:
                self.detail_dialog = ActivityDetail(self, *result)
                self.detail_dialog.open()
        run_task(lambda: (client.activity(aid), client.relations(pid), client.assignments(pid, aid)), done, busy="Cargando detalle…")

    def save_detail(self, original, values, done=None, finished=None, *, pid=None):
        pid = self.state.pid if pid is None else pid
        project, client, aid = dict(self.state.project), self.state.client, original["id"]
        patch = detail_patch(original, values)
        percent = patch.pop("percent_complete", None)
        def work():
            if patch:
                client.update_activity(aid, patch)
            if percent is not None:
                self._progress(client, pid, project, aid, percent)
        accepted = self.mutate(work, lambda _: done() if done else None, pid=pid, finished=finished,
                               operation_key=("detail", aid, json.dumps(patch, sort_keys=True, default=str), percent))
        if not accepted and finished:
            finished()

    @staticmethod
    def _progress(client, pid, project, aid, value):
        cut = project.get("data_date") or date.today().isoformat()
        activity = client.activity(aid)
        start = activity.get("actual_start") or activity.get("planned_start") or activity.get("early_start") or cut
        client.add_progress(pid, [dict(activity_id=aid, date=cut, percent_complete=value, actual_hours=0,
                                       actual_cost=0, resource_id=None, actual_start=min(start, cut) if value > 0 else None,
                                       actual_finish=cut if value >= 100 else None, note="Editado en Gantt")])

    def edit_cell(self, aid, key, value):
        pid, client, project = self.state.pid, self.state.client, dict(self.state.project)
        if key == "start":
            self.move_activity(aid, value)
            return
        if key == "pct":
            self.mutate(lambda: self._progress(client, pid, project, aid, value), pid=pid,
                        operation_key=("progress", aid, project.get("data_date"), value))
            return
        patch = {dict(names="name", dur="duration_days", hh="work_hours", macro="macro_month")[key]: value or None if key == "macro" else value}
        if key == "macro" and not value:
            patch.update(window_start=None, window_end=None)
        def work():
            if key == "dur":
                activity = client.activity(aid)
                if activity.get("locked"):
                    raise ValueError("La actividad está bloqueada.")
                if project["mode"] == "mantenimiento":
                    start = activity.get("planned_start") or activity.get("early_start")
                    if start:
                        patch.update(self._maintenance_dates(client, project, activity, start, int(value)))
            return client.update_activity(aid, patch)
        local = (lambda: self._apply_local(aid, key, value)) if key in ("names", "hh") else None
        self.mutate(work, recalc=key == "dur", pid=pid, local_update=local)

    def _apply_local(self, aid, key, value) -> bool:
        """Refleja en memoria un cambio de nombre o HH ya confirmado por la API (sin recargar 100 000 filas)."""
        i = self.arrays.id_to_index.get(aid)
        if i is None:
            return False
        if key == "names":
            self.arrays.names[i] = value
        elif key == "hh":
            self.arrays.hh[i] = float(value) if value not in (None, "") else float("nan")
        else:
            return False
        rows = self.gantt.rows
        options = getattr(rows, "options", None)
        if rows is None or options is None:
            return False
        if (key == "names" and (options["text"].strip() or options["sort"] == "nombre")) or (
                key == "hh" and options["grouped"] and options["wbs"]):
            # A4-12: el filtro, el orden o los agregados WBS dependen del valor editado: se reconstruye la vista
            # local (≈ 0,05 s con 100 000 actividades) sin volver a descargar el programa.
            new_rows = RowModel(self.arrays, **options)
            relations = RelationIndex(self.rel_list, self.arrays, new_rows)
            selected = self.gantt.selected_id
            self.gantt.set_data(self.arrays, new_rows, relations, self.state.project or {}, reset=False)
            if selected is not None:
                self.gantt.selected_id = selected
            self.status.setText(f"{fmt_num(new_rows.matched_count)} coinciden")
            return True
        row = int(rows.row_of_act[i])
        if row >= 0:
            self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, self.model.columnCount() - 1))
        self.gantt.canvas.update()
        return True

    @staticmethod
    def _maintenance_dates(client, project, activity, requested, duration):
        cid = activity.get("calendar_id") or project.get("calendar_id")
        end = project.get("end_date")
        if not end:
            raise ValueError("El mantenimiento requiere un horizonte de término.")
        if duration and not cid:
            raise ValueError("Asigna un calendario antes de mover la actividad.")
        if requested > end:
            raise ValueError("El inicio queda fuera del horizonte del proyecto.")
        days = client.calendar_days(cid, requested, end) if duration else []
        return scheduled_dates(days, requested, duration, end)

    def _apply_local_dates(self, activity, project):
        if not self.gantt.apply_activity_dates(activity, project, self.rel_list):
            return False
        a = self.arrays
        weight = np.where(a.hh > 0, a.hh, np.maximum(1, np.nan_to_num(a.dur)))
        progress = float(np.sum(weight * a.pct) / weight.sum()) if a.n else 0
        self.status_badges["progress"].setText(f"{fmt_num(progress, 1)} % avance ponderado")
        return True

    def move_activity(self, aid, requested):
        pid, project, client = self.state.pid, dict(self.state.project), self.state.client
        saved = []
        def work():
            activity = client.activity(aid)
            if activity.get("locked"):
                raise ValueError("La actividad está bloqueada.")
            if project["mode"] == "proyecto":
                patch = dict(constraint_type="SNET", constraint_date=requested)
            else:
                patch = self._maintenance_dates(client, project, activity, requested, activity["duration_days"])
            updated = client.update_activity(aid, patch)
            saved.append(updated)
            return updated
        local = (lambda: self._apply_local_dates(saved[0], project)) if project["mode"] == "mantenimiento" else None
        self.mutate(work, pid=pid, local_update=local)

    def resize_activity(self, aid, requested_finish):
        pid, project, client = self.state.pid, dict(self.state.project), self.state.client
        saved = []
        def work():
            activity = client.activity(aid)
            if activity.get("locked"):
                raise ValueError("La actividad está bloqueada.")
            start = activity.get("planned_start") or activity.get("early_start") or activity.get("actual_start")
            cid = activity.get("calendar_id") or project.get("calendar_id")
            if not start or not cid:
                raise ValueError("La actividad necesita fecha de inicio y calendario.")
            if requested_finish < start:
                raise ValueError("El término no puede ser anterior al inicio.")
            if project["mode"] == "mantenimiento" and project.get("end_date") and requested_finish > project["end_date"]:
                raise ValueError("El término queda fuera del horizonte del proyecto.")
            days = client.calendar_days(cid, start, requested_finish)
            working = [d["date"] for d in days if d["working"]]
            if not working:
                raise ValueError("El intervalo no contiene días laborables.")
            patch = dict(duration_days=len(working))
            if project["mode"] == "mantenimiento":
                patch["planned_finish"] = max(working)
            updated = client.update_activity(aid, patch)
            saved.append(updated)
            return updated
        local = (lambda: self._apply_local_dates(saved[0], project)) if project["mode"] == "mantenimiento" else None
        self.mutate(work, pid=pid, local_update=local)

    def create_relation(self, pred_id, succ_id):
        if pred_id == succ_id:
            notify("Una actividad no puede depender de sí misma.", "error")
            return
        self.save_relation(dict(pred_id=pred_id, succ_id=succ_id, type="FS", lag_days=0))

    def save_relation(self, payload, old=None, done=None, *, pid=None):
        pid = self.state.pid if pid is None else pid
        client = self.state.client
        def work():
            if old is None:
                return client.create_relation(pid, payload)
            # The API has no relation PUT. Compensate a failed replacement so the
            # original dependency is not silently lost. Reload after either outcome.
            restore = {k: old[k] for k in ("pred_id", "succ_id", "type", "lag_days")}
            if restore == payload:
                return old
            client.delete_relation(old["id"])
            try:
                return client.create_relation(pid, payload)
            except Exception as original_error:
                try:
                    client.create_relation(pid, restore)
                except Exception as rollback_error:
                    raise RuntimeError(f"No se pudo reemplazar ni restaurar la relación. Datos originales: {restore}. "
                                       f"Error: {original_error}; restauración: {rollback_error}") from original_error
                raise
        self.mutate(work, pid=pid, finished=lambda: done(None) if done else None,
                    operation_key=("relation", old["id"] if old else None, json.dumps(payload, sort_keys=True)))

    def delete_selected(self):
        aid, pid = self.gantt.selected_id, self.state.pid
        if aid is None:
            return
        i = self.arrays.id_to_index[aid]
        label = f"{self.arrays.codes[i]} · {self.arrays.names[i]}"
        client = self.state.client

        def ask(history):
            if pid != self.state.pid:
                return
            warning = history_warning(history)
            text = f"Se eliminará {label} y sus relaciones." + (f"\n\n{warning}" if warning else "")
            if confirm(self, "Eliminar actividad", text, danger=True):
                self.mutate(lambda: client.delete_activity(aid, force=bool(warning)), pid=pid,
                            operation_key=("delete_activity", aid))
        # D3: antes de confirmar se consulta si la actividad tiene líneas base o avances.
        run_task(lambda: client.activity_history(aid), ask, lambda _exc: ask(None))

    def on_theme(self):
        self.gantt.on_theme()
        self.profile.on_theme()
        self._page_theme()

    def _page_theme(self):
        t = tokens()
        self.only_critical.setIcon(icon("flag"))
        self.more_button.setIcon(icon("ellipsis"))
        # Conserva QCheckBox y su API pública, con aspecto de botón conmutable.
        self.only_critical.setStyleSheet(
            f"QCheckBox {{padding: 6px; border: 1px solid {t.border}; border-radius: 8px;}}"
            "QCheckBox::indicator {width: 0px; height: 0px;}"
            f"QCheckBox:checked {{background: {t.accent_soft}; color: {t.accent};}}"
            f"QCheckBox:focus {{border-color: {t.accent};}}")
        self.delete_button.setStyleSheet(
            f"QPushButton {{color: {t.critical}; border-color: transparent;}}"
            f"QPushButton:hover {{background: {t.surface3};}}")
        self.toolbar.layout().setSpacing(4)
        for widget in (self.generate_button, self.cpm_button, self.optimize_button,
                       self.new_button, self.paste_button, self.detail_button):
            widget.setStyleSheet("QPushButton {padding: 6px;}")
        for segment in self.scale_buttons.buttons():
            segment.setStyleSheet(
                "QPushButton {padding: 4px; border-radius: 4px;}"
                f"QPushButton:checked {{background: {t.accent_soft}; color: {t.accent};}}")
