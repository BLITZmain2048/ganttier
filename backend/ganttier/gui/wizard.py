"""Asistente lista de tareas → programa, exclusivamente mediante ApiClient."""
from __future__ import annotations

from .widgets.responsive import ResponsiveDialog, ResponsiveWizard

from collections import Counter, deque
from datetime import date, datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSpinBox, QStyledItemDelegate, QTableView, QVBoxLayout,
    QWizard, QWizardPage,
)

from .widgets import common
from .widgets.icons import icon
from .pages.role_options import RoleOptions
from .widgets.paste import (
    EditableTableModel, PasteDialog, parse_predecessors, paste_from_clipboard,
    setup_preview,
)

Field = common.Field
run_task = common.run_task
show_error = common.show_error


def action_button(text, slot, glyph=None, primary=False):
    """Compatible con common antes y después de la integración UX1."""
    try:
        return common.button(text, slot, primary=primary, icon=glyph)
    except TypeError:
        result = common.button(text, slot, primary=primary)
        if glyph:
            result.setIcon(icon(glyph))
        return result


def _method_text(code):
    try:
        from .widgets.common import method_text
        return method_text(code)
    except Exception:  # noqa: BLE001
        return code or "—"


def _status_text(code):
    try:
        from .widgets.common import status_text
        return status_text(code)
    except Exception:  # noqa: BLE001
        return code or "—"


def _autofit(table, model):
    """Ajusta anchos de columna al pegar o agregar filas (sin cabeceras ni celdas cortadas)."""
    try:
        from .widgets.common import fit_columns
    except ImportError:  # pragma: no cover
        return
    refit = lambda *_: fit_columns(table, max_w=260)
    model.modelReset.connect(refit)
    model.rowsInserted.connect(refit)
    refit()


def resource_fields(calendars):
    return [Field("name", "Nombre"),
            Field("kind", "Tipo", "choice", choices=[("trabajador", "Trabajador"), ("cuadrilla", "Cuadrilla")]),
            Field("skills", "Habilidades (separadas por coma)", "tags"),
            Field("capacity_hours_day", "HH/día por unidad", "float"),
            Field("max_units", "Unidades", "float"),
            Field("calendar_id", "Calendario", "choice", choices=[(c["id"], c["name"]) for c in calendars])]


def task_fields(mode):
    head = [Field("code", "Código"), Field("name", "Nombre")]
    base = [Field("duration_days", "Duración (días)", "int"), Field("work_hours", "HH por ejecución" if mode == "mantenimiento" else "HH", "float")]
    if mode == "proyecto":
        return head + base + [Field("required_skill", "Habilidad"), Field("crew_size", "Dotación", "float"),
            Field("predecessors", "Predecesoras"), Field("macro_month", "Mes macro", "month"), Field("window_hard", "Ventana dura", "bool")]
    return head + [Field("frequency", "Frecuencia", "choice", choices=[(x, x) for x in ("D", "DH", "W", "M", "Q", "S", "A", "ND", "NW", "NM", "NA")]),
        Field("interval", "Intervalo", "int", minimum=1)] + base + [Field("crew_size", "Dotación", "float"),
        Field("required_skill", "Habilidad"), Field("first_due", "Primera fecha", "date"),
        Field("location", "Instalación"), Field("requires_outage", "Requiere desconexión", "bool")]


def validate_tasks(rows, fields, skills, capacity=None):
    """Validación lineal de códigos, referencias y ciclos (sin recursión).

    ``capacity`` (habilidad → HH/día totales) permite advertir tareas imposibles: HH ÷ duración mayor
    que toda la capacidad diaria de su habilidad (el programa resultaría infactible)."""
    columns = {f.key: c for c, f in enumerate(fields)}
    errors, warnings = {}, []
    codes = Counter(row.get("code") for row in rows if row.get("code"))
    graph = {code: set() for code in codes}
    for r, row in enumerate(rows):
        code = row.get("code")
        if code and codes[code] > 1:
            errors[r, columns["code"]] = f"Código duplicado: {code}."
        skill = row.get("required_skill")
        if skill and skill not in skills:
            warnings.append(f"{code or r + 1}: habilidad «{skill}» sin recurso seleccionado.")
        try:
            hh, dur = float(row.get("work_hours") or 0), float(row.get("duration_days") or 0)
        except (TypeError, ValueError):
            hh = dur = 0
        cap = (capacity or {}).get(skill)
        if cap and dur > 0 and hh / dur > cap + 1e-6:
            warnings.append(f"{code or r + 1}: {hh / dur:.1f} HH/día supera la capacidad de «{skill}» "
                            f"({cap:.1f} HH/día); aumente la duración a {hh / cap:.0f} días o la capacidad.")
        if "predecessors" in columns:
            try:
                predecessors = parse_predecessors(row.get("predecessors", ""))
            except ValueError as exc:
                errors[r, columns["predecessors"]] = str(exc)
                continue
            seen = set()
            for pred in predecessors:
                if pred["code"] not in codes:
                    errors[r, columns["predecessors"]] = f"Predecesora inexistente: {pred['code']}."
                elif pred["code"] == code:
                    errors[r, columns["predecessors"]] = "Una tarea no puede depender de sí misma."
                elif code in graph:
                    graph[code].add(pred["code"])
                if pred["code"] in seen:
                    errors[r, columns["predecessors"]] = f"Predecesora repetida: {pred['code']}."
                seen.add(pred["code"])
    # Eliminar nodos sin predecesoras; los restantes contienen un ciclo o dependen de él.
    degree = {code: len(preds) for code, preds in graph.items()}
    children = {code: [] for code in graph}
    for code, preds in graph.items():
        for pred in preds:
            children[pred].append(code)
    ready = deque(code for code, count in degree.items() if not count)
    while ready:
        for child in children[ready.popleft()]:
            degree[child] -= 1
            if not degree[child]:
                ready.append(child)
    if "predecessors" in columns:
        for r, row in enumerate(rows):
            if degree.get(row.get("code"), 0):
                errors[r, columns["predecessors"]] = "Dependencia circular: esta tarea pertenece o está vinculada a un ciclo."
    return errors, warnings


class ChoiceDelegate(QStyledItemDelegate):
    def __init__(self, choices, parent=None, editable=False):
        super().__init__(parent)
        self._choices = choices
        self._editable = editable

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        editor.setEditable(self._editable)
        for value, label in self._choices():
            editor.addItem(label, value)
        return editor

    def setEditorData(self, editor, index):
        value = index.model().rows[index.row()].get(index.model().fields[index.column()].key, "")
        selected = editor.findData(value)
        if selected < 0:
            selected = editor.findText(str(value))
        if selected < 0 and self._editable:
            editor.setEditText(str(value))
        else:
            editor.setCurrentIndex(selected)

    def setModelData(self, editor, model, index):
        if self._editable:
            model.setData(index, editor.currentText())
        else:
            model.setData(index, editor.currentData())


class ProjectPage(QWizardPage):
    def __init__(self, state, mode):
        super().__init__()
        self.setTitle("1 · Proyecto")
        self.setSubTitle("Defina el proyecto, sus fechas y el calendario de trabajo.")
        form = QFormLayout(self)
        self.name_edit = QLineEdit()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Proyecto", "proyecto")
        self.mode_combo.addItem("Mantenimiento", "mantenimiento")
        self.mode_combo.setCurrentIndex(1 if mode == "mantenimiento" else 0)
        self.start_edit = QDateEdit(QDate.currentDate())
        self.end_edit = QDateEdit(QDate.currentDate().addYears(1))
        for editor in (self.start_edit, self.end_edit):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd-MM-yyyy")
            editor.dateChanged.connect(self._changed)
        self.end_check = QCheckBox("Definir fin / horizonte")
        self.calendar_combo = QComboBox()
        for calendar in getattr(state, "calendars", ()):
            self.calendar_combo.addItem(calendar["name"], calendar["id"])
        if not self.calendar_combo.count():
            self.calendar_combo.addItem("Chile L-V 8h (predeterminado)", None)
        preferred = self.calendar_combo.findText("Chile L-V 8h")
        if preferred >= 0:
            self.calendar_combo.setCurrentIndex(preferred)
        form.addRow("Nombre *", self.name_edit)
        form.addRow("Modo", self.mode_combo)
        form.addRow("Inicio *", self.start_edit)
        form.addRow(self.end_check, self.end_edit)
        form.addRow("Calendario", self.calendar_combo)
        self.validation_label = QLabel()
        form.addRow(self.validation_label)
        self.name_edit.textChanged.connect(self._changed)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.end_check.toggled.connect(self._changed)
        self._mode_changed()

    def _mode_changed(self, *_):
        maintenance = self.mode_combo.currentData() == "mantenimiento"
        self.end_check.setEnabled(not maintenance)
        if maintenance:
            self.end_check.setChecked(True)
        self._changed()

    def _changed(self, *_):
        self.end_edit.setEnabled(self.end_check.isChecked())
        message = ""
        if not self.name_edit.text().strip():
            message = "Escriba un nombre para el proyecto."
        elif self.end_check.isChecked() and self.end_edit.date() < self.start_edit.date():
            message = "El fin del horizonte no puede ser anterior al inicio."
        self.validation_label.setText(message)
        self.completeChanged.emit()

    def isComplete(self):
        return bool(self.name_edit.text().strip()) and (not self.end_check.isChecked() or self.end_edit.date() >= self.start_edit.date())

    def values(self):
        return dict(name=self.name_edit.text().strip(), mode=self.mode_combo.currentData(),
                    start_date=self.start_edit.date().toString("yyyy-MM-dd"),
                    end_date=self.end_edit.date().toString("yyyy-MM-dd") if self.end_check.isChecked() else None,
                    calendar_id=self.calendar_combo.currentData())


class ResourcePage(QWizardPage):
    def __init__(self, state):
        super().__init__()
        self._state = state
        self.setTitle("2 · Recursos")
        self.setSubTitle("Agregue trabajadores o cuadrillas y sus habilidades. HH/día se expresa por unidad.")
        outer = QVBoxLayout(self)
        self.model = EditableTableModel(resource_fields(getattr(state, "calendars", ())), parent=self)
        self.table = QTableView()
        self.table.setModel(self.model)
        _autofit(self.table, self.model)
        for c, field in enumerate(self.model.fields):
            if field.kind == "choice":
                self.table.setItemDelegateForColumn(c, ChoiceDelegate(lambda f=field: f.choices, self.table))
        actions = QHBoxLayout()
        for text, slot, glyph in [("Agregar fila", self.add_row, "plus"), ("Eliminar", self.remove_rows, "trash-2"),
                                  ("Pegar desde Excel", self.paste, "clipboard-paste"),
                                  ("Recursos globales existentes", self.choose_globals, "users")]:
            actions.addWidget(action_button(text, slot, glyph))
        outer.addLayout(actions)
        outer.addWidget(self.table, 1)
        self.global_resources = [dict(r) for r in getattr(state, "resources", ()) if r.get("project_id") is None and r.get("active", True)]
        self.selected_global_ids = set()
        self.summary = QLabel()
        self.no_resources_check = QCheckBox("Confirmo continuar sin recursos; la capacidad no se restringirá.")
        self.no_resources_check.toggled.connect(self._changed)
        outer.addWidget(self.summary)
        outer.addWidget(self.no_resources_check)
        if self.global_resources:
            note = QLabel("Los recursos globales son compartidos: la API los considera disponibles para todos los proyectos.\nLas casillas permiten incorporarlos a la revisión de habilidades sin duplicarlos.")
            note.setWordWrap(True)
            outer.addWidget(note)
        self.model.validated.connect(self._changed)
        self._changed()
        setup_preview(self.table)

    def add_row(self):
        cid = self.wizard().project_page.calendar_combo.currentData() if self.wizard() else None
        self.model.append_rows([dict(name="", kind="cuadrilla", skills=[], capacity_hours_day=8, max_units=1, calendar_id=cid)])

    def remove_rows(self):
        selected = {i.row() for i in self.table.selectionModel().selectedRows()}
        self.model.set_rows([row for r, row in enumerate(self.model.rows) if r not in selected])

    def paste(self):
        values = paste_from_clipboard(self, "Pegar recursos desde Excel", self.model.fields)
        if values is not None:
            self.model.append_rows(values)
            setup_preview(self.table)

    def choose_globals(self):
        dialog = ResponsiveDialog(self)
        dialog.setWindowTitle("Recursos globales existentes")
        layout = QVBoxLayout(dialog)
        choices = QListWidget()
        for resource in self.global_resources:
            item = QListWidgetItem(f"{resource['name']} · {', '.join(resource.get('skills') or [])}")
            item.setData(Qt.UserRole, resource["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if resource["id"] in self.selected_global_ids else Qt.Unchecked)
            choices.addItem(item)
        layout.addWidget(choices)
        if not self.global_resources:
            layout.addWidget(QLabel("No hay recursos globales disponibles en el catálogo."))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Aceptar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.selected_global_ids = {choices.item(i).data(Qt.UserRole) for i in range(choices.count()) if choices.item(i).checkState() == Qt.Checked}
            self._changed()

    def selected_resources(self):
        return self.model.converted + [r for r in self.global_resources if r["id"] in self.selected_global_ids]

    def skills(self):
        return sorted({s for r in self.selected_resources() for s in r.get("skills", [])})

    def capacity(self):
        """HH/día normales por habilidad (unidades × HH/día) de los recursos elegidos."""
        out = {}
        for r in self.selected_resources():
            per_day = float(r.get("capacity_hours_day") or 8) * float(r.get("max_units") or 1)
            for skill in r.get("skills", []) or []:
                out[skill] = out.get(skill, 0.0) + per_day
        return out

    def _changed(self, *_):
        count = len(self.model.rows) + len(self.selected_global_ids)
        self.summary.setText(f"{count} recursos · {len(self.model.errors)} errores")
        self.no_resources_check.setVisible(count == 0)
        if count:
            self.no_resources_check.setChecked(False)
        self.completeChanged.emit()

    def isComplete(self):
        return not self.model.errors and (bool(self.model.rows or self.selected_global_ids) or self.no_resources_check.isChecked())


class TasksPage(QWizardPage):
    def __init__(self, mode):
        super().__init__()
        self.task_mode = mode
        self.setTitle("3 · Tareas")
        self.setSubTitle("Pegue su lista y corrija los errores. Las advertencias permiten continuar.")
        outer = QVBoxLayout(self)
        actions = QHBoxLayout()
        for text, slot, glyph in [("Agregar", self.add_row, "plus"), ("Eliminar", self.remove_rows, "trash-2"),
                                 ("Pegar desde Excel", self.paste, "clipboard-paste"),
                                 ("Cargar plantilla Excel…", self.load_template, "file-spreadsheet")]:
            actions.addWidget(action_button(text, slot, glyph))
        outer.addLayout(actions)
        self.table = QTableView()
        outer.addWidget(self.table, 1)
        self.summary = QLabel()
        self.messages = QLabel()
        self.messages.setWordWrap(True)
        outer.addWidget(self.summary)
        outer.addWidget(self.messages)
        self._install_model([])

    def _install_model(self, rows):
        old = getattr(self, "model", None)
        self.model = EditableTableModel(task_fields(self.task_mode), rows, self)
        self.model.extra_validator = lambda values: validate_tasks(values, self.model.fields, self.wizard().resource_page.skills() if self.wizard() else [],
                                                                 self.wizard().resource_page.capacity() if self.wizard() else None)
        self.table.setModel(self.model)
        _autofit(self.table, self.model)
        for c, f in enumerate(self.model.fields):
            self.table.setItemDelegateForColumn(c, None)
            if f.key == "required_skill":
                delegate = ChoiceDelegate(lambda: [("", "")] + [(s, s) for s in self.wizard().resource_page.skills()], self.table, editable=True)
                self.table.setItemDelegateForColumn(c, delegate)
            elif f.kind == "choice":
                self.table.setItemDelegateForColumn(c, ChoiceDelegate(lambda field=f: field.choices, self.table))
        self.model.validated.connect(self._changed)
        self.model.revalidate()
        if old is not None:
            old.deleteLater()
        setup_preview(self.table)

    def initializePage(self):
        mode = self.wizard().project_page.mode_combo.currentData()
        if mode != self.task_mode:
            # Common fields survive a mode change and no hidden fields reach the API.
            rows = self.model.rows
            self.task_mode = mode
            self._install_model(rows)
        self.model.revalidate()

    def add_row(self):
        row = dict(code="", name="", duration_days=1, work_hours=8, crew_size=1)
        if self.task_mode == "mantenimiento":
            row.update(frequency="M", interval=1, first_due=self.wizard().project_page.values()["start_date"], requires_outage=False)
        else:
            row.update(window_hard=False)
        self.model.append_rows([row])

    def remove_rows(self):
        selected = {i.row() for i in self.table.selectionModel().selectedRows()}
        self.model.set_rows([row for r, row in enumerate(self.model.rows) if r not in selected])

    def paste(self):
        values = paste_from_clipboard(self, "Pegar tareas desde Excel", self.model.fields)
        if values is not None:
            self.model.append_rows(values)
            setup_preview(self.table)

    def load_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "Cargar plantilla Excel", "", "Libro Excel (*.xlsx *.xlsm)")
        if not path:
            return
        mode = self.task_mode
        def read():
            from openpyxl import load_workbook
            book = load_workbook(path, read_only=True, data_only=True)
            try:
                preferred = ("Plan_Maestro", "Actividades") if mode == "mantenimiento" else ("Actividades", "Plan_Maestro")
                sheet = next((book[name] for name in preferred if name in book.sheetnames), None)
                if sheet is None:
                    raise ValueError("El libro no contiene las hojas Actividades o Plan_Maestro.")
                rows = []
                for cells in sheet.iter_rows(values_only=True):
                    row = [v.date().isoformat() if isinstance(v, datetime) else v.isoformat() if isinstance(v, date) else "" if v is None else str(v) for v in cells]
                    if any(row):
                        rows.append(row)
                return rows
            finally:
                book.close()
        def done(rows):
            dialog = PasteDialog(self, "Revisar plantilla Excel", self.model.fields, rows)
            if dialog.exec() == QDialog.Accepted:
                self.model.append_rows(dialog.values())
                setup_preview(self.table)
        run_task(read, done, busy="Leyendo plantilla Excel…")

    def _changed(self):
        self.summary.setText(f"{len(self.model.rows)} tareas · {len(self.model.errors)} errores · {len(self.model.warnings)} advertencias")
        issues = [f"Fila {r + 1}: {message}" for (r, _), message in list(self.model.errors.items())[:3]]
        issues += self.model.warnings[:3]
        self.messages.setText("\n".join(issues))
        self.completeChanged.emit()

    def isComplete(self):
        return bool(self.model.rows) and not self.model.errors


class ObjectivePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("4 · Objetivo")
        self.setSubTitle("Elija la prioridad del programa y ajuste los límites de capacidad.")
        outer = QVBoxLayout(self)
        grid = QGridLayout()
        self.objective_group = QButtonGroup(self)
        self.weight_editors = {}
        cards = [("makespan", "Minimizar plazo", "Terminar lo antes posible", "timer"),
                 ("level", "Nivelar carga", "Distribuir el trabajo disponible", "chart-column"),
                 ("window", "Respetar meses/ventanas", "Acercar las tareas a su mes objetivo", "calendar-range"),
                 ("cost", "Minimizar costo/HH extra", "Reducir el uso de horas adicionales", "gauge"),
                 ("balanced", "Equilibrado", "Dar el mismo peso a los cuatro objetivos", "sliders-horizontal")]
        for i, (key, title, subtitle, glyph) in enumerate(cards):
            card = QPushButton(f"{title}\n{subtitle}")
            card.setIcon(icon(glyph))
            card.setCheckable(True)
            card.setMinimumHeight(76)
            card.setProperty("card", True)
            self.objective_group.addButton(card)
            card.clicked.connect(lambda checked=False, k=key: self.choose_objective(k))
            grid.addWidget(card, i // 2, i % 2)
            if key == "balanced":
                card.setChecked(True)
        outer.addLayout(grid)
        advanced = self.advanced_weights = QGroupBox("Pesos avanzados")
        form = QFormLayout(advanced)
        for key, label, _, _ in cards[:4]:
            editor = QDoubleSpinBox()
            editor.setRange(0, 1000)
            editor.setDecimals(2)
            editor.setValue(1)
            self.weight_editors[key] = editor
            form.addRow(label, editor)
        outer.addWidget(advanced)
        self.capacity_check = QCheckBox("Capacidad como restricción dura")
        self.capacity_check.setChecked(True)
        self.overtime_check = QCheckBox("Permitir horas extra")
        self.time_limit_spin = QSpinBox()
        self.time_limit_spin.setRange(1, 3600)
        self.time_limit_spin.setValue(30)
        self.time_limit_spin.setSuffix(" s")
        self.capacity_note = QLabel()
        self.capacity_note.setWordWrap(True)
        outer.addWidget(self.capacity_check)
        outer.addWidget(self.overtime_check)
        outer.addWidget(self.capacity_note)
        limits = QFormLayout()
        limits.addRow("Límite de tiempo", self.time_limit_spin)
        outer.addLayout(limits)
        self.role_options = RoleOptions()
        outer.addWidget(self.role_options)
        self.role_options.changed.connect(self._role_mode_changed)

    def _role_mode_changed(self, active):
        self.advanced_weights.setVisible(not active)
        if active and not hasattr(self, "_fractional_controls"):
            self._fractional_controls = (self.capacity_check.isChecked(), self.overtime_check.isChecked(), self.time_limit_spin.value())
            self.capacity_check.setChecked(True)
            self.overtime_check.setChecked(False)
        elif not active and hasattr(self, "_fractional_controls"):
            capacity, overtime, time_limit = self._fractional_controls
            del self._fractional_controls
            self.capacity_check.setChecked(capacity)
            self.overtime_check.setChecked(overtime)
            self.time_limit_spin.setMaximum(3600)
            self.time_limit_spin.setValue(time_limit)
        for editor in self.weight_editors.values():
            editor.setEnabled(not active)
        for card in self.objective_group.buttons():
            card.setEnabled(not active)
            card.setVisible(not active)
        self.capacity_check.setEnabled(not active and bool(self.wizard().resource_page.selected_resources()))
        self.overtime_check.setEnabled(not active)
        self.time_limit_spin.setMaximum(300 if active else 3600)

    def initializePage(self):
        no_resources = not self.wizard().resource_page.selected_resources()
        self.role_options.set_project_mode(self.wizard().project_page.mode_combo.currentData())
        self.capacity_check.setEnabled(not no_resources)
        note = "Sin recursos: la capacidad no se restringirá al generar." if no_resources else ""
        if no_resources and self.wizard().project_page.mode_combo.currentData() == "mantenimiento":
            note += " El motor de mantenimiento exige asignaciones concretas y puede informar infactibilidad si no dispone de trabajadores o cuadrillas."
        self.capacity_note.setText(note)
        self._role_mode_changed(self.role_options.mode.currentData() == "indivisible")

    def choose_objective(self, key):
        for name, editor in self.weight_editors.items():
            editor.setValue(1 if key == "balanced" or key == name else 0)

    def values(self):
        value = dict(weights={key: editor.value() for key, editor in self.weight_editors.items()},
                    options=dict(respect_resource_limits=self.capacity_check.isChecked() and bool(self.wizard().resource_page.selected_resources()),
                                 allow_overtime=self.overtime_check.isChecked(), time_limit_s=self.time_limit_spin.value()))
        value["options"].update(self.role_options.values())
        return value


STAGES = ("Crear proyecto", "Preparar recursos", "Crear estructura y tareas", "Crear dependencias", "Generar programa")


def create_schedule(client, payload, progress=lambda index, text: None):
    """Trabajo REST; no lee widgets ni publica estado Qt desde el trabajador.

    No reintenta POST: preserva el proyecto y cuenta cada creación confirmada.
    """
    pid, result, error = None, None, None
    created = dict(resources=0, wbs=0, activities=0, relations=0, periodic_tasks=0)
    stage = 0
    try:
        project = client.create_project(payload["project"])
        pid = project["id"]
        progress(0, f"Proyecto creado · ID {pid}")
        stage = 1
        for resource in payload["resources"]:
            client.create_resource(dict(resource, project_id=pid))
            created["resources"] += 1
        progress(1, f"{created['resources']} recursos creados · {len(payload['global_ids'])} globales reutilizados")
        stage = 2
        maintenance = payload["project"]["mode"] == "mantenimiento"
        if maintenance:
            for row in payload["tasks"]:
                client.post(f"/projects/{pid}/periodic-tasks", row)
                created["periodic_tasks"] += 1
        else:
            root = client.create_wbs(pid, dict(code="RAIZ", name=payload["project"]["name"]))
            created["wbs"] = 1
            rows = [dict({key: value for key, value in row.items() if key != "predecessors"}, wbs_id=root["id"]) for row in payload["tasks"]]
            activities = client.create_activities(pid, rows)
            created["activities"] = len(activities)
        progress(2, f"{created['periodic_tasks'] if maintenance else created['activities']} tareas creadas")
        stage = 3
        if not maintenance:
            ids = {row["code"]: row["id"] for row in activities}
            for row in payload["tasks"]:
                for pred in parse_predecessors(row.get("predecessors", "")):
                    client.create_relation(pid, dict(pred_id=ids[pred["code"]], succ_id=ids[row["code"]], type=pred["type"], lag_days=pred["lag_days"]))
                    created["relations"] += 1
        progress(3, f"{created['relations']} dependencias creadas" if not maintenance else "Recurrencias listas para generar ocurrencias")
        stage = 4
        solve = dict(payload["objective"], apply=True)
        if maintenance:
            solve.update(horizon_start=payload["project"]["start_date"], horizon_end=payload["project"]["end_date"])
        result = client.post(f"/projects/{pid}/schedule/solve", solve)
        progress(4, "Generación finalizada")
    except Exception as exc:
        error = f"{STAGES[stage]}: {exc}"
    return dict(pid=pid, result=result, error=error, stage=stage, created=created)


class GeneratePage(QWizardPage):
    stage_completed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.setTitle("5 · Generar programa")
        self.setSubTitle("Creación del proyecto y búsqueda de un programa factible.")
        self.setFinalPage(True)
        self.started = False
        self.finished_work = False
        self.is_running = False
        self.opened_project = False
        self.result_data = None
        self.creation_report = None
        outer = QVBoxLayout(self)
        self.stages_list = QListWidget()
        for name in STAGES:
            self.stages_list.addItem(QListWidgetItem(icon("clock"), name))
        outer.addWidget(self.stages_list)
        banner_type = getattr(common, "StatusBanner", None)
        self.banner = banner_type() if banner_type else QLabel()
        outer.addWidget(self.banner)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.PlainText)
        outer.addWidget(self.result_label)
        self.issues_list = QListWidget()
        outer.addWidget(self.issues_list, 1)
        self.open_anyway_button = action_button("Abrir proyecto igualmente", self.open_project, "folder-open")
        self.open_anyway_button.setVisible(False)
        self.gantt_button = action_button("Abrir en Gantt", self.open_gantt, "chart-gantt", True)
        self.gantt_button.setEnabled(False)
        outer.addWidget(self.open_anyway_button)
        outer.addWidget(self.gantt_button)
        self.stage_completed.connect(self._progress, Qt.QueuedConnection)

    def _banner(self, kind, title, text=""):
        setter = getattr(self.banner, "set", None)
        if setter:
            setter(kind, title, text)
        else:
            self.banner.setText(title + ("\n" + text if text else ""))
            self.banner.setProperty("badge", kind)

    def initializePage(self):
        if self.started:
            return
        wizard = self.wizard()
        # Snapshot validado en GUI, antes de pasar al hilo.
        try:
            payload = wizard.payload()
        except ValueError as exc:
            self._banner("critical", "Revise los datos", str(exc))
            return
        self.started = self.is_running = True
        self._banner("info", "Generando programa…")
        wizard.update_buttons()
        client = wizard.state.client
        progress = self.stage_completed.emit
        run_task(lambda: create_schedule(client, payload, progress), self._done, self._failed, busy="Generando programa…")

    def _progress(self, index, text):
        item = self.stages_list.item(index)
        item.setIcon(icon("check"))
        item.setText(text)

    def _failed(self, exc):
        # Fallos del ejecutor, fuera de la secuencia protegida de REST.
        self._done(dict(pid=None, result=None, error=str(exc), stage=0, created={}))

    def _done(self, report):
        self.is_running = False
        self.finished_work = True
        self.creation_report = report
        self.wizard().created_pid = report["pid"]
        self.result_data = report["result"]
        if report["pid"] is not None:
            self.wizard().state.projects_changed.emit()
        if report["error"]:
            self.stages_list.item(report["stage"]).setIcon(icon("circle-x"))
            created = report["created"]
            details = (f"El proyecto {report['pid']} queda creado. " if report["pid"] is not None else "No se confirmó la creación de un proyecto. ")
            details += f"Recursos: {created.get('resources', 0)} · WBS: {created.get('wbs', 0)} · Actividades: {created.get('activities', 0)} · Periódicas: {created.get('periodic_tasks', 0)} · Relaciones: {created.get('relations', 0)}."
            self._banner("critical", "No se pudo completar el programa", details)
            self.result_label.setText(report["error"])
            self.open_anyway_button.setVisible(report["pid"] is not None)
        else:
            result = report["result"] or {}
            feasible = bool(result.get("feasible"))
            if feasible:
                self._banner("good", "Programa factible")
            elif result.get("infeasibility") == "no_encontrada":
                # P1-06a: un límite de búsqueda no demuestra que no exista solución.
                self._banner("warning", "Sin solución encontrada en el tiempo dado",
                             "Aumenta el límite de tiempo o revisa capacidad y ventanas; no se demostró que sea imposible.")
            else:
                self._banner("critical", "Programa infactible", "Los datos impiden cualquier programa: revisa la causa indicada.")
            after = result.get("after") or {}
            finish = str(after.get("finish") or "—")
            try:
                finish = date.fromisoformat(finish[:10]).strftime("%d-%m-%Y")
            except ValueError:
                pass
            def number(value):
                return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") if isinstance(value, (int, float)) else str(value)
            self.result_label.setText(f"Término: {finish} · Pico: {number(after.get('peak_load', 0))} · HH extra: {number(after.get('overtime_hours', 0))}\nFuera de ventana: {after.get('activities_out_of_window', 0)} · Método: {_method_text(result.get('method_used'))} · Estado: {_status_text(result.get('status'))}")
            issues = [("Violación", v) for v in (result.get("violations") or [])] + [("Advertencia", w) for w in (result.get("warnings") or [])]
            if not self.wizard().resource_page.selected_resources():
                issues.insert(0, ("Advertencia", "Sin recursos: la capacidad no se ha restringido."))
            for kind, issue in issues[:20]:
                self.issues_list.addItem(f"{kind}: {issue}")
            if len(issues) > 20:
                self.result_label.setText(self.result_label.text() + f"\nSe muestran 20 de {len(issues)} advertencias y violaciones.")
            self.open_project()
        self.completeChanged.emit()
        self.wizard().update_buttons()

    def open_project(self):
        wizard = self.wizard()
        if wizard.created_pid is None or self.opened_project:
            return
        def opened(*_):
            self.opened_project = True
            self.open_anyway_button.setVisible(False)
            self.gantt_button.setEnabled(True)
            self.completeChanged.emit()
        opener = getattr(wizard.state, "open_project_async", None)
        if callable(opener):
            # AppState.open_project hace REST síncrono; su variante pública async
            # mantiene las publicaciones Qt en GUI y todas las peticiones en run_task.
            opener(wizard.created_pid, on_done=opened)
            self.open_anyway_button.setVisible(True)
        else:
            # Compatibilidad con estados ligeros / clientes simulados sin REST aquí.
            wizard.state.open_project(wizard.created_pid)
            opened()

    def open_gantt(self):
        if not self.opened_project:
            return
        parent = self.wizard().parentWidget()
        if callable(getattr(parent, "go", None)):
            parent.go("gantt")
        self.wizard().accept()

    def isComplete(self):
        return self.finished_work and (self.opened_project or bool(self.creation_report and self.creation_report["error"]))


class TaskListWizard(ResponsiveWizard):
    def __init__(self, state, parent=None, mode="proyecto"):
        super().__init__(parent)
        self.state = state
        self.created_pid = None
        self.setWindowTitle("Lista de tareas → programa")
        self.setWizardStyle(QWizard.ModernStyle)
        self.resize(1100, 760)
        self.setButtonText(QWizard.BackButton, "Atrás")
        self.setButtonText(QWizard.NextButton, "Siguiente")
        self.setButtonText(QWizard.FinishButton, "Cerrar")
        self.setButtonText(QWizard.CancelButton, "Cancelar")
        self.project_page = ProjectPage(state, mode)
        self.resource_page = ResourcePage(state)
        self.tasks_page = TasksPage(mode)
        self.objective_page = ObjectivePage()
        self.generate_page = GeneratePage()
        for page in (self.project_page, self.resource_page, self.tasks_page, self.objective_page, self.generate_page):
            self.addPage(page)
        self.currentIdChanged.connect(self.update_buttons)

    def update_buttons(self, *_):
        self.setButtonText(QWizard.NextButton, "Generar programa" if self.currentId() == 3 else "Siguiente")
        if self.currentId() == 4 and self.generate_page.started:
            self.button(QWizard.BackButton).setEnabled(False)
        self.button(QWizard.CancelButton).setEnabled(not self.generate_page.is_running)

    def payload(self):
        resources = self.resource_page.model.values()
        self.tasks_page.model.revalidate()
        if not self.project_page.isComplete() or not self.resource_page.isComplete() or not self.tasks_page.isComplete():
            raise ValueError("Complete los datos obligatorios antes de generar.")
        return dict(project=self.project_page.values(), resources=resources,
                    global_ids=sorted(self.resource_page.selected_global_ids),
                    tasks=self.tasks_page.model.values(), objective=self.objective_page.values())

    def reject(self):
        if not self.generate_page.is_running:
            super().reject()

    def closeEvent(self, event):
        if self.generate_page.is_running:
            event.ignore()
        else:
            super().closeEvent(event)


def build_wizard(state, parent=None, mode="proyecto") -> QWizard:
    if mode not in {"proyecto", "mantenimiento"}:
        raise ValueError("El modo debe ser proyecto o mantenimiento.")
    if not getattr(state, "calendars", None):
        # Sin proyecto abierto los catálogos aún no se cargaron: el asistente necesita los calendarios
        # (consulta local breve a la API embebida).
        # A3-11: lectura con plazo corto (la API es local); si falla se avisa en vez de seguir en silencio.
        try:
            state.calendars = state.client.calendars(timeout=5)
        except Exception as exc:  # noqa: BLE001 - el asistente sigue con el calendario del proyecto
            from .widgets.common import notify
            notify(f"No se pudieron cargar los calendarios ({exc}); se usará el calendario por defecto del proyecto.", "warning")
    return TaskListWizard(state, parent, mode)


def run_task_list_wizard(state, parent=None, mode="proyecto") -> int | None:
    wizard = build_wizard(state, parent, mode)
    wizard.exec()
    return wizard.created_pid
