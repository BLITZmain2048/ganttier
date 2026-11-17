"""Catálogo de recursos y capacidad normal por habilidad."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QAbstractItemView, QLabel, QLineEdit, QComboBox, QTableView

from ..widgets.common import (Column, DictTableModel, Field, FormDialog, SortProxy,
                              button, confirm, notify, run_task, show_error)
from .base import Page
from ..widgets.responsive import scroll_layout

# UX1 puede integrarse antes o después de esta página.
from .. import theme
from ..widgets import common as _common
from ..widgets.icons import icon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget, QLineEdit

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


class _FallbackSection(QFrame):
    def __init__(self, title, subtitle=None):
        super().__init__()
        self.setProperty("card", True)
        self.body = QVBoxLayout(self)
        self.body.setSpacing(_SPACE["sm"])
        label = QLabel(title)
        label.setProperty("role", "section")
        self.body.addWidget(label)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setProperty("role", "caption")
            hint.setWordWrap(True)
            self.body.addWidget(hint)


Section = getattr(_common, "Section", _FallbackSection)



KINDS = [("trabajador", "Trabajador"), ("cuadrilla", "Cuadrilla"),
         ("equipo", "Equipo"), ("material", "Material")]


try:
    from ..widgets.paste import paste_from_clipboard
except ImportError:
    def paste_from_clipboard(parent, title, fields):
        """Respaldo mínimo: una fila por recurso, nombre y habilidades separados por tab."""
        import csv
        import io
        from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPlainTextEdit
        dialog = QDialog(parent)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Pega nombre y habilidades (separadas por coma), sin encabezados."))
        editor = QPlainTextEdit(QApplication.clipboard().text())
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if not dialog.exec():
            return None
        return [dict(name=row[0].strip(), skills=[v.strip() for v in row[1].split(",") if v.strip()] if len(row) > 1 else [])
                for row in csv.reader(io.StringIO(editor.toPlainText()), delimiter="\t") if row and row[0].strip()]


class _ScopeDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        t = theme.tokens()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        text = str(index.data() or "")
        rect = QRectF(option.rect.adjusted(5, 5, -5, -5))
        rect.setWidth(min(rect.width(), option.fontMetrics.horizontalAdvance(text) + 20))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t.accent_soft if text == "Proyecto" else t.surface2))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor(t.text))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.restore()


class _CapacityDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        t = theme.tokens()
        model = index.model()
        maximum = max((float(row.get("hours", 0)) for row in model.rows), default=1) or 1
        value = float(model.rows[index.row()].get("hours", 0))
        rect = QRectF(option.rect.adjusted(4, 6, -4, -6))
        painter.fillRect(rect, QColor(t.surface2))
        rect.setWidth(rect.width() * value / maximum)
        painter.fillRect(rect, QColor(t.accent_soft))
        painter.setPen(QColor(t.text))
        painter.drawText(option.rect.adjusted(8, 0, -8, 0), Qt.AlignRight | Qt.AlignVCenter, str(index.data()))
        painter.restore()


class ResourcesPage(Page):
    title = "Recursos"
    subtitle = "Recursos del proyecto y globales, capacidad por habilidad"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.root.setSpacing(_SPACE["md"])
        self.rows = []
        self.calendars = []
        self.loading = True
        self.saving = False
        self._ticket = 0
        self._pid = state.pid
        self.data_pid = None
        self._publishing = False
        self.search = _search("Buscar recursos…")
        self.search.setPlaceholderText("Buscar recursos…")
        self.skill = QComboBox()
        self.skill.addItem("Todas las habilidades", "")
        self.new_button = _button("Nuevo", self.new_resource, primary=True, icon_name="plus")
        self.edit_button = _button("Editar", self.edit, icon_name="pencil")
        self.delete_button = _button("Eliminar", self.delete, danger=True, icon_name="trash-2")
        self.paste_button = _button("Pegar desde Excel", self.paste_resources, icon_name="clipboard-paste")
        self.absence_button = _button("Ausencia…", self.absence, icon_name="calendar-x")
        self.absence_button.setToolTip("Licencia, capacitación o media jornada de un trabajador (P5-10)")
        self.root.addWidget(_toolbar(self.new_button, self.edit_button, self.delete_button, self.paste_button,
                                     self.absence_button, None,
                                     _button("Actualizar", self.refresh, icon_name="refresh-cw", kind="ghost")))
        self.root.addWidget(_toolbar(self.search, self.skill))
        self.model = DictTableModel([
            Column("name", "Nombre"), Column("kind", "Tipo", kind="choice", choices=KINDS),
            Column("skills", "Habilidades"), Column("capacity_hours_day", "HH/día", kind="float", dec=1),
            Column("max_units", "Unidades", kind="float", dec=1),
            Column("calendar_id", "Calendario", get=self._calendar_name),
            Column("cost_hour", "Costo HH", kind="float"),
            Column("overtime_cost_hour", "Costo HH extra", kind="float"),
            Column("max_overtime_hours_day", "Máx. extra/día", kind="float", dec=1),
            Column("project_id", "Ámbito", get=lambda r: "Global" if r.get("project_id") is None else "Proyecto"),
            Column("active", "Activo", kind="bool"), Column("email", "Correo"),
        ])
        self.proxy = SortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        _setup(self.table)
        self.table.setItemDelegateForColumn(9, _ScopeDelegate(self.table))
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setColumnWidth(0, 220)
        self.table.doubleClicked.connect(lambda *_: self.edit())
        self.root.addWidget(self.table, 3)
        self.empty = _empty("users", "Aún no hay recursos", "Agrega recursos o pega una tabla desde Excel.",
                            [("Nuevo recurso", self.new_resource, True), ("Pegar desde Excel", self.paste_resources, False)])
        self.root.addWidget(self.empty)
        self.no_matches = QLabel("No hay recursos para estos filtros.")
        self.no_matches.setProperty("role", "caption")
        self.no_matches.hide()
        self.root.addWidget(self.no_matches)
        card = Section("Capacidad por habilidad")
        self.capacity_model = DictTableModel([
            Column("skill", "Habilidad"), Column("resources", "Recursos (unidades)", kind="float", dec=1),
            Column("hours", "HH/día", kind="float", dec=1)])
        capacity = self.capacity_table = QTableView()
        capacity.setModel(self.capacity_model)
        _setup(capacity)
        capacity.setItemDelegateForColumn(2, _CapacityDelegate(capacity))
        capacity.setMaximumHeight(160)
        card.body.addWidget(capacity)
        self.capacity_empty = QLabel("Sin capacidad por habilidad. Asigna habilidades a tus recursos activos.")
        self.capacity_empty.setWordWrap(True)
        self.capacity_empty.setProperty("role", "caption")
        card.body.addWidget(self.capacity_empty)
        capacity.hide()
        hint = QLabel("HH/día de recursos activos. Un recurso con varias habilidades participa en cada una; no sumes esas capacidades.")
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        card.body.addWidget(hint)
        self.root.addWidget(card, 1)
        self.status = QLabel()
        self.status.setProperty("role", "caption")
        self.root.addWidget(self.status)
        self.search.textChanged.connect(self.filter_rows)
        self.skill.currentIndexChanged.connect(self.filter_rows)
        state.catalogs_changed.connect(self._mark_stale)
        self.model.data_pid = self.capacity_model.data_pid = None
        self.filter_rows()
        self._actions()

        self.content_scroll = scroll_layout(self.root)
        self.root.addWidget(_toolbar(self.new_button, self.edit_button, None))

    def _actions(self):
        ready = self.data_pid == self.state.pid and not self.loading and not self.saving
        for widget in (self.new_button, self.edit_button, self.delete_button, self.paste_button, self.absence_button,
                       self.table):
            widget.setEnabled(ready)
        self.empty.setEnabled(ready)

    def on_theme(self):
        self.table.viewport().update()
        self.capacity_table.viewport().update()

    def _can_mutate(self):
        if self.data_pid != self.state.pid or self.model.data_pid != self.state.pid:
            notify("Los datos pertenecen a otro proyecto. Usa Actualizar.", "info")
            return False
        return not self.loading and not self.saving

    def _sync_project(self):
        if self._pid == self.state.pid:
            return
        self._pid = self.state.pid
        self._ticket += 1
        self.data_pid = self.model.data_pid = self.capacity_model.data_pid = None
        self.loading = True
        self.rows, self.calendars = [], []
        self.table.clearSelection()
        self.model.set_rows([])
        self.capacity_model.set_rows([])
        self.capacity_table.hide()
        self.capacity_empty.show()
        self.filter_rows()
        for widget in (self.search, self.skill):
            previous = widget.blockSignals(True)
            widget.clear()
            if widget is self.skill:
                widget.addItem("Todas las habilidades", "")
            widget.blockSignals(previous)
        self.status.clear()
        self._actions()

    def _mark_stale(self):
        self._sync_project()
        if not self._publishing:
            super()._mark_stale()

    def _calendar_name(self, row):
        return next((c["name"] for c in self.calendars if c["id"] == row.get("calendar_id")), "Del proyecto")

    def refresh(self, *, after_write=False):
        self._sync_project()
        pid = self.state.pid
        self._ticket += 1
        ticket = self._ticket
        self.loading = True
        self._actions()
        client = self.state.client

        def done(data):
            if ticket != self._ticket or pid != self.state.pid:
                return
            self.loading = False
            self.rows, self.calendars = data
            self.data_pid = self.model.data_pid = self.capacity_model.data_pid = pid
            selected = self.skill.currentData()
            self.skill.blockSignals(True)
            self.skill.clear()
            self.skill.addItem("Todas las habilidades", "")
            for skill in sorted({s for r in self.rows for s in r.get("skills", [])}):
                self.skill.addItem(skill, skill)
            self.skill.setCurrentIndex(max(0, self.skill.findData(selected)))
            self.skill.blockSignals(False)
            self.filter_rows()
            totals = {}
            for r in self.rows:
                if r.get("active", True):
                    for skill in set(r.get("skills", [])):
                        v = totals.setdefault(skill, {"skill": skill, "resources": 0, "hours": 0})
                        units = r.get("max_units", 1)
                        v["resources"] += units
                        v["hours"] += units * r.get("capacity_hours_day", 0)
            self.capacity_model.set_rows(sorted(totals.values(), key=lambda r: -r["hours"]))
            self.capacity_table.setVisible(bool(totals))
            self.capacity_empty.setVisible(not totals)
            _fit(self.capacity_table)
            _fit(self.table)
            self._actions()
            if after_write:
                self.state.resources, self.state.calendars = self.rows, self.calendars
                self._publishing = True
                try:
                    self.state.catalogs_changed.emit()
                finally:
                    self._publishing = False

        def failed(exc):
            if ticket == self._ticket and pid == self.state.pid:
                self.loading = False
                self._stale = True
                self._actions()
                if after_write:
                    notify("Guardado; no se pudo actualizar la vista. Usa Actualizar.", "info")
                else:
                    show_error(exc, self)
        run_task(lambda: (client.resources(pid), client.calendars()), done,
                 failed, busy="Cargando recursos…")

    def filter_rows(self, *_):
        q, skill = self.search.text().strip().casefold(), self.skill.currentData()
        rows = [r for r in self.rows if (not skill or skill in r.get("skills", [])) and
                (not q or q in " ".join(str(r.get(k, "")) for k in ("name", "email", "skills", "kind")).casefold())]
        self.model.set_rows(rows)
        self.empty.setVisible(not self.rows)
        self.table.setVisible(bool(rows))
        self.no_matches.setVisible(bool(self.rows) and not rows)
        self.status.setText(f"{len(rows)} recursos · Los recursos globales se comparten entre proyectos.")

    def paste_resources(self):
        if not self._can_mutate():
            return
        pid, client = self.state.pid, self.state.client
        values = paste_from_clipboard(self, "Pegar recursos desde Excel", self._fields())
        if not values or pid != self.state.pid or not self._can_mutate():
            return
        payloads = []
        keys = {f.key for f in self._fields()}
        for row in values:
            payload = dict(kind="trabajador", skills=[], capacity_hours_day=8, max_units=1,
                           max_overtime_hours_day=2, active=True, project_id=pid)
            payload.update({key: value for key, value in row.items() if key in keys})
            error = self._validate(payload)
            if error:
                show_error(ValueError(error), self)
                return
            payloads.append(payload)
        self.saving = True
        self._actions()

        def work():
            created = []
            for payload in payloads:
                try:
                    created.append(client.create_resource(payload))
                except Exception as exc:
                    return created, exc
            return created, None

        def done(result):
            created, error = result
            self.saving = False
            self._actions()
            notify(f"{len(created)} de {len(payloads)} recursos creados")
            if pid == self.state.pid:
                self.refresh(after_write=True)
            if error:
                show_error(error, self)
        run_task(work, done, self._failed, busy="Creando recursos…")

    def selected(self):
        index = self.table.currentIndex()
        return self.model.row_dict(self.proxy.mapToSource(index).row()) if index.isValid() else None

    def _fields(self):
        scope = [(self.state.pid, "Solo este proyecto"), (None, "Global")]
        return [Field("name", "Nombre"), Field("kind", "Tipo", "choice", KINDS),
                Field("skills", "Habilidades (separadas por coma)", "tags", span=True),
                Field("email", "Correo"),
                Field("calendar_id", "Calendario", "choice", [(None, "Del proyecto")] + [(c["id"], c["name"]) for c in self.calendars]),
                Field("capacity_hours_day", "HH/día por unidad", "float"),
                Field("max_units", "Unidades disponibles", "float"),
                Field("cost_hour", "Costo HH normal", "float"),
                Field("overtime_cost_hour", "Costo HH extra", "float"),
                Field("max_overtime_hours_day", "Máx. HH extra/día", "float"),
                Field("project_id", "Ámbito", "choice", scope), Field("active", "Activo", "bool")]

    @staticmethod
    def _validate(values):
        if not values.get("name", "").strip():
            return "Ingresa un nombre para el recurso."
        return None

    def new_resource(self):
        if not self._can_mutate():
            return
        self._dialog({"name": "", "kind": "trabajador", "skills": [], "capacity_hours_day": 8,
                      "max_units": 1, "max_overtime_hours_day": 2, "active": True,
                      "project_id": self.state.pid})

    def absence(self):
        """P5-10: ausencia personal por intervalo, con vista previa y asignaciones afectadas."""
        from ..absence_dialog import AbsenceDialog, eligible
        if not self._can_mutate():
            return
        row = self.selected()
        reason = eligible(row)
        if reason:
            notify(reason, "info")
            return None
        dialog = AbsenceDialog(self.state, row, self)
        if dialog.exec() and dialog.applied is not None:
            self.refresh(after_write=True)
        return dialog

    def edit(self):
        if not self._can_mutate():
            return
        row = self.selected()
        if row and not self.saving:
            self._dialog(row)

    def _dialog(self, values):
        pid = self.state.pid
        dlg = FormDialog("Editar recurso" if values.get("id") else "Nuevo recurso",
                         self._fields(), values, self, validate=self._validate)
        if dlg.exec():
            if pid != self.state.pid:
                notify("El proyecto cambió; no se guardó el recurso.", "info")
                return
            self.save_resource(dlg.values(), values.get("id"))

    def save_resource(self, values, rid=None):
        if not self._can_mutate():
            return
        error = self._validate(values)
        if error:
            show_error(ValueError(error), self)
            return
        payload = {f.key: values[f.key] for f in self._fields() if f.key in values}
        payload.setdefault("project_id", self.state.pid)
        self.saving = True
        self._actions()
        pid, client = self.state.pid, self.state.client

        def work():
            return client.update_resource(rid, payload) if rid else client.create_resource(payload)
        run_task(work, lambda result: self._saved(result, pid), self._failed, busy="Guardando recurso…")

    def _saved(self, result, pid):
        self.saving = False
        self.last_saved = result
        self._actions()
        notify("Recurso guardado")
        if pid == self.state.pid:
            self.refresh(after_write=True)

    def _failed(self, exc):
        self.saving = False
        self._actions()
        show_error(exc, self)

    def delete(self):
        if not self._can_mutate():
            return
        pid = self.state.pid
        row = self.selected()
        if not row or self.saving or not confirm(self, "Eliminar recurso", f"¿Eliminar «{row['name']}»?", danger=True):
            return
        if pid != self.state.pid or not self._can_mutate():
            notify("El proyecto cambió; no se eliminó el recurso.", "info")
            return
        self.saving = True
        self._actions()
        pid, client = self.state.pid, self.state.client

        def work():
            client.delete_resource(row["id"])

        def done(_):
            self.saving = False
            self._actions()
            notify("Recurso eliminado")
            if pid == self.state.pid:
                self.refresh(after_write=True)
        run_task(work, done, self._failed, busy="Eliminando recurso…")
