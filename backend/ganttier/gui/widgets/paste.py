"""Pegado tabular de Excel y conversiones compartidas con el asistente UX3."""
from __future__ import annotations

from .responsive import ResponsiveDialog

import csv
import io
import math
import re
import unicodedata
from datetime import date, datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QScrollArea, QTableView, QVBoxLayout, QWidget,
)

from ..theme import tokens
from . import common


def normalize_header(value) -> str:
    text = unicodedata.normalize("NFKD", str(value).casefold())
    return "".join(c for c in text if c.isalnum() and not unicodedata.combining(c))


_GROUPS = {
    "code": "codigo code id", "name": "nombre actividad tarea descripcion name",
    "duration_days": "duracion duracion_dias dias dur duration_days",
    "work_hours": "hh horas work_hours hh_totales hh_por_ejecucion",
    "required_skill": "habilidad habilidad_requerida especialidad skill required_skill",
    "crew_size": "dotacion cuadrilla personas crew crew_size",
    "predecessors": "predecesoras predecesor dependencias predecessors",
    "macro_month": "mes mes_macro macro_month", "frequency": "frecuencia frequency",
    "interval": "intervalo interval", "first_due": "primera_fecha first_due",
    "location": "instalacion location", "equipment": "equipo equipment",
    "requires_outage": "requiere_desconexion desconexion requires_outage",
    "voltage_kv": "tension kv voltage_kv", "max_units": "unidades max_units",
    "capacity_hours_day": "capacidad hh_dia capacity_hours_day",
    "kind": "tipo kind", "cost_hour": "costo_hh cost_hour",
    "skills": "habilidades skills", "calendar_id": "calendario calendar_id calendar",
    "window_hard": "ventana_dura window_hard",
}
ALIASES = {normalize_header(word): key for key, words in _GROUPS.items() for word in words.split()}
_NUMERIC = {"work_hours", "crew_size", "voltage_kv", "max_units", "capacity_hours_day", "cost_hour"}
_INTEGER = {"duration_days", "interval"}


def canonical_key(value) -> str:
    key = normalize_header(value)
    return ALIASES.get(key, key)


def parse_table(text: str) -> list[list[str]]:
    """TSV RFC-style: dobles comillas escapadas y nuevas líneas dentro de celdas."""
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"), newline=""), delimiter="\t"))
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    return rows


def parse_number(value) -> float:
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if "," in text:
        # Excel chileno usa punto de miles y coma decimal.
        if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:\.\d{3})+),\d+", text):
            raise ValueError("Número inválido; use 1.234,5 o 1234.5.")
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[+-]?[1-9]\d{0,2}(?:\.\d{3})+", text):
        # A3-02: entero chileno con punto de miles (1.234 = mil doscientos treinta y cuatro). Con el formato
        # chileno declarado, un punto seguido de grupos de exactamente tres cifras se interpreta como miles.
        text = text.replace(".", "")
    try:
        result = float(text)
    except (ValueError, TypeError):
        raise ValueError("Número inválido; use 1.234,5 o 1234.5.") from None
    if not math.isfinite(result):
        raise ValueError("El número debe ser finito.")
    return result


def parse_date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Fecha inválida; use dd-mm-aaaa, aaaa-mm-dd o dd/mm/aaaa.")


def parse_month(value) -> str:
    text = str(value).strip().lower()
    months = "ene feb mar abr may jun jul ago sep oct nov dic".split()
    match = re.fullmatch(r"([a-z]{3})-(\d{4})", text)
    if match and match[1] in months:
        text = f"{match[2]}-{months.index(match[1]) + 1:02d}"
    try:
        if not re.fullmatch(r"\d{4}-\d{2}", text):
            raise ValueError
        date.fromisoformat(text + "-01")
    except ValueError:
        raise ValueError("Mes inválido; use 2027-03 o mar-2027.") from None
    return text


def parse_predecessors(text: str) -> list[dict]:
    """Tipos explícitos separados del código por espacio; FS es el valor por defecto.

    +N sin espacio es un desfase. Los códigos con guion (A-01) se preservan;
    un desfase negativo sin tipo requiere un espacio: B -2, o bien B FS-2.
    """
    result = []
    for item in re.split(r"[;,]", text or ""):
        item = item.strip()
        if not item:
            continue
        explicit = re.fullmatch(r"(.+?)\s+(FS|SS|FF|SF)\s*([+-]\s*\d+)?", item, re.I)
        if explicit:
            code, kind, lag = explicit.groups()
        else:
            implicit = re.fullmatch(r"([^\s+;,]+?)(?:\s*([+]\s*\d+)|\s+([-]\s*\d+))?", item)
            if not implicit:
                raise ValueError(f"Predecesora inválida: {item}. Use A FS+2 o B SS-1.")
            code, positive, negative = implicit.groups()
            lag = positive or negative
            kind = "FS"
        code = code.strip()
        if not code or re.search(r"[\s;,]", code):
            raise ValueError(f"Código de predecesora inválido: {code}.")
        result.append(dict(code=code, type=kind.upper(), lag_days=int((lag or "0").replace(" ", ""))))
    return result


def required_field(field) -> bool:
    return bool(getattr(field, "required", False) or canonical_key(field.key) in {"code", "name", "duration_days"})


def convert_cell(value, field):
    """Convierte sin perder información: las entradas inválidas nunca pasan a cero."""
    key, kind = canonical_key(field.key), getattr(field, "kind", "text")
    if value is None or (isinstance(value, str) and not value.strip()):
        if required_field(field):
            raise ValueError("Campo obligatorio.")
        return None
    text = str(value).strip()
    choices = getattr(field, "choices", ())
    if key == "calendar_id" and (not choices or normalize_header(text) in {
            "none", "nulo", "delproyecto", "proyecto", "predeterminado", ""}):
        return None   # sin calendario explícito el recurso usa el del proyecto
    if kind == "choice" and choices:
        for stored, label in choices:
            if normalize_header(text) in {normalize_header(stored), normalize_header(label)}:
                return stored
        raise ValueError("Elija uno de los valores de la lista.")
    if kind in {"float", "int"} or key in _NUMERIC | _INTEGER:
        number = parse_number(value)
        if (kind == "int" or key in _INTEGER) and not number.is_integer():
            raise ValueError("Debe ser un número entero.")
        minimum = max(1 if key == "interval" else 0, getattr(field, "minimum", 0))
        if not minimum <= number <= getattr(field, "maximum", 1e12):
            raise ValueError(f"Valor fuera del rango permitido (mínimo {minimum:g}).")
        return int(number) if kind == "int" or key in _INTEGER else number
    if kind == "date" or key == "first_due":
        return parse_date(value)
    if kind == "month" or key == "macro_month":
        return parse_month(value)
    if kind == "bool" or key in {"requires_outage", "window_hard"}:
        normalized = normalize_header(text)
        if normalized in {"1", "si", "true", "verdadero", "x"}:
            return True
        if normalized in {"0", "no", "false", "falso"}:
            return False
        raise ValueError("Use Sí/No, verdadero/falso o 1/0.")
    if kind == "tags" or key == "skills":
        return [str(s).strip() for s in (value if isinstance(value, list) else re.split(r"[,;]", text)) if str(s).strip()]
    if key == "predecessors":
        parse_predecessors(text)
    return text


class EditableTableModel(QAbstractTableModel):
    """Modelo reutilizable: filas crudas editables, valores tipados y errores por celda."""

    validated = Signal()

    def __init__(self, fields, rows=None, parent=None):
        super().__init__(parent)
        self.fields = list(fields)
        self.rows = [dict(row) for row in (rows or [])]
        self.errors = {}
        self.warnings = []
        self.converted = []
        self.extra_validator = None
        self.revalidate()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.fields)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(self.fields):
            if role in (Qt.DisplayRole, Qt.ToolTipRole):
                f = self.fields[section]
                return f.label + (" *" if required_field(f) else "")
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return section + 1
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        field = self.fields[index.column()]
        value = self.rows[index.row()].get(field.key, "")
        error = self.errors.get((index.row(), index.column()))
        if role == Qt.ToolTipRole:
            return error or getattr(field, "help", "")
        if role == Qt.BackgroundRole and error:
            color = QColor(tokens().critical)
            color.setAlpha(45)
            return color
        if role == Qt.ForegroundRole and error:
            return QColor(tokens().critical)
        if role == Qt.CheckStateRole and field.kind == "bool":
            return Qt.Checked if self.converted[index.row()].get(field.key) else Qt.Unchecked
        if role == Qt.EditRole:
            return ", ".join(value) if isinstance(value, list) else ("" if value is None else str(value))
        if role == Qt.DisplayRole:
            if field.kind == "bool":
                return "" if not error else str(value)
            for stored, label in getattr(field, "choices", ()):
                if value == stored:
                    return label
            if value is None and canonical_key(field.key) == "calendar_id":
                return "Del proyecto"
            if isinstance(value, float) and not error:
                # Formato chileno: coma decimal, punto de miles.
                text = f"{value:,.1f}" if value != int(value) or field.kind == "float" else f"{int(value):,}"
                return text.replace(",", "_").replace(".", ",").replace("_", ".")
            return ", ".join(value) if isinstance(value, list) else ("" if value is None else str(value))
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        return base | (Qt.ItemIsUserCheckable if self.fields[index.column()].kind == "bool" else Qt.ItemIsEditable)

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role not in (Qt.EditRole, Qt.CheckStateRole):
            return False
        if role == Qt.CheckStateRole:
            value = value == Qt.Checked or value == Qt.Checked.value
        self.rows[index.row()][self.fields[index.column()].key] = value
        self.revalidate()
        return True

    def set_rows(self, rows):
        # A3-09: filas, conversiones y errores se reconstruyen DENTRO del reset; las notificaciones van después.
        self.beginResetModel()
        self.rows = [dict(row) for row in rows]
        self._recompute()
        self.endResetModel()
        self.validated.emit()

    def append_rows(self, rows):
        self.set_rows([*self.rows, *rows])

    def revalidate(self):
        self._recompute()
        if self.rows and self.fields:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(self.fields) - 1))
        self.validated.emit()

    def _recompute(self):
        self.errors, self.converted, self.warnings = {}, [], []
        for r, row in enumerate(self.rows):
            converted = {}
            for c, field in enumerate(self.fields):
                try:
                    value = convert_cell(row.get(field.key), field)
                    if value is not None:
                        converted[field.key] = value
                except (TypeError, ValueError) as exc:
                    self.errors[r, c] = str(exc)
            self.converted.append(converted)
        if self.extra_validator:
            extra, self.warnings = self.extra_validator(self.converted)
            for cell, message in extra.items():
                self.errors.setdefault(cell, message)

    def values(self):
        self.revalidate()
        if self.errors:
            raise ValueError("Corrija las celdas marcadas antes de continuar.")
        return [dict(row) for row in self.converted]


def setup_preview(view):
    setup = getattr(common, "setup_table", None)
    if setup:
        setup(view, sortable=False)
    else:
        view.setAlternatingRowColors(True)
        view.setSelectionBehavior(QTableView.SelectRows)
        view.verticalHeader().setDefaultSectionSize(30)
    fit = getattr(common, "fit_columns", None)
    if fit:
        fit(view)
    else:
        view.resizeColumnsToContents()


class PasteDialog(ResponsiveDialog):
    def __init__(self, parent, title, fields, rows=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 590)
        self.fields = list(fields)
        self.source_rows = [list(row) for row in (rows if rows is not None else parse_table(QApplication.clipboard().text()))]
        self.mapping_combos = []
        self._raw_model = None
        outer = QVBoxLayout(self)
        hint = QLabel("Revise los encabezados, asigne las columnas y corrija las celdas marcadas.")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self.header_check = QCheckBox("La primera fila contiene encabezados")
        lookup = {}
        for f in self.fields:
            for label in (f.key, f.label):
                lookup[canonical_key(label)] = f.key
        if "skills" in {f.key for f in self.fields} and "required_skill" not in {f.key for f in self.fields}:
            lookup["required_skill"] = "skills"
        first = self.source_rows[0] if self.source_rows else []
        recognized = [lookup.get(canonical_key(cell)) for cell in first]
        self.header_check.setChecked(any(recognized))
        outer.addWidget(self.header_check)
        mapping_scroll = QScrollArea()
        mapping_scroll.setWidgetResizable(True)
        mapping_scroll.setMaximumHeight(120)
        mapping_body = QWidget()
        mapping_layout = QHBoxLayout(mapping_body)
        width = max((len(row) for row in self.source_rows), default=len(fields))
        for col in range(width):
            box = QVBoxLayout()
            box.addWidget(QLabel(f"Columna {col + 1}"))
            combo = QComboBox()
            combo.addItem("Ignorar", None)
            for f in fields:
                combo.addItem(f.label, f.key)
            key = (recognized[col] if col < len(recognized) else None) if any(recognized) else (fields[col].key if col < len(fields) else None)
            combo.setCurrentIndex(max(0, combo.findData(key)))
            combo.currentIndexChanged.connect(self._remap)
            self.mapping_combos.append(combo)
            box.addWidget(combo)
            mapping_layout.addLayout(box)
        mapping_scroll.setWidget(mapping_body)
        outer.addWidget(mapping_scroll)
        self.table = QTableView()
        outer.addWidget(self.table, 1)
        self.summary = QLabel()
        self.mapping_error = QLabel()
        self.mapping_error.setWordWrap(True)
        outer.addWidget(self.summary)
        outer.addWidget(self.mapping_error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.accept_button = self.buttons.button(QDialogButtonBox.Ok)
        self.accept_button.setText("Aceptar")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.header_check.toggled.connect(self._remap)
        self._remap()

    def _remap(self, *_):
        # Preserve corrections in source coordinates when changing the mapping.
        if self._raw_model is not None:
            offset = int(self._previous_header)
            for r, row in enumerate(self._raw_model.rows):
                self.source_rows[r + offset] = [row.get(str(c), "") for c in range(len(self.mapping_combos))]
        self._previous_header = self.header_check.isChecked()
        by_key = {f.key: f for f in self.fields}
        columns = []
        from dataclasses import replace
        for c, combo in enumerate(self.mapping_combos):
            field = by_key.get(combo.currentData())
            # Keep source columns visible, including ignored ones.
            columns.append(replace(field, key=str(c)) if field else common.Field(str(c), "Ignorar"))
        data = [{str(c): cell for c, cell in enumerate(row)} for row in self.source_rows[int(self._previous_header):]]
        old = self._raw_model
        self.model = EditableTableModel(columns, data, self)
        self._raw_model = self.model
        # Conversion uses the original field to preserve required/inferred kinds.
        def validate(rows):
            errors = {}
            for r, raw in enumerate(self.model.rows):
                for c, combo in enumerate(self.mapping_combos):
                    field = by_key.get(combo.currentData())
                    if field:
                        try:
                            convert_cell(raw.get(str(c)), field)
                        except (TypeError, ValueError) as exc:
                            errors[r, c] = str(exc)
            return errors, []
        self.model.extra_validator = validate
        self.model.validated.connect(self._summary)
        self.table.setModel(self.model)
        if old is not None:
            old.deleteLater()
        self.model.revalidate()
        setup_preview(self.table)

    def _summary(self):
        selected = [c.currentData() for c in self.mapping_combos if c.currentData()]
        missing = [f.label for f in self.fields if required_field(f) and f.key not in selected]
        duplicate = len(selected) != len(set(selected))
        errors = len({r for r, _ in self.model.errors})
        self.summary.setText(f"{self.model.rowCount()} filas · {errors} con errores")
        problems = (["Faltan columnas obligatorias: " + ", ".join(missing)] if missing else [])
        if duplicate:
            problems.append("Cada campo debe asignarse a una sola columna.")
        self.mapping_error.setText(" · ".join(problems))
        self.accept_button.setEnabled(bool(self.model.rows) and not errors and not problems)

    def values(self):
        self.model.revalidate()
        if not self.accept_button.isEnabled():
            raise ValueError("Corrija los errores y el mapeo de columnas.")
        by_key = {f.key: f for f in self.fields}
        result = []
        for row in self.model.rows:
            values = {}
            for c, combo in enumerate(self.mapping_combos):
                key = combo.currentData()
                if key:
                    value = convert_cell(row.get(str(c)), by_key[key])
                    if value is not None:
                        values[key] = value
            result.append(values)
        return result

    def accept(self):
        self.model.revalidate()
        if self.accept_button.isEnabled():
            super().accept()


def paste_from_clipboard(parent, title, fields) -> list[dict] | None:
    dialog = PasteDialog(parent, title, fields)
    return dialog.values() if dialog.exec() == QDialog.Accepted else None
