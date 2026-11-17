"""Edición masiva de actividades con alcance explícito y vista previa (P4-02).

El alcance se congela al abrir el diálogo (identificadores de las actividades que coinciden con el filtro
vigente, o solo la seleccionada). Cada campo se cambia únicamente si su casilla «Cambiar» está marcada.
La vista previa muestra antes → después; la escritura usa el PATCH transaccional de la API (todo o nada).
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QGridLayout, QLabel,
                               QLineEdit, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
                               QAbstractItemView)

from ..widgets.common import fmt_num
from ..widgets.responsive import ResponsiveDialog

MAX_BATCH = 5000
PREVIEW_ROWS = 300


def _hh_text(value) -> str:
    return "—" if value is None or (isinstance(value, float) and not math.isfinite(value)) else fmt_num(value, 1)


class BulkEditDialog(ResponsiveDialog):
    """``patches()`` devuelve la lista de cambios por actividad lista para ``patch_activities``."""

    def __init__(self, arrays, filtered_ids: list[int], selected_id: int | None, calendars: list[dict],
                 mode: str = "proyecto", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar actividades en lote")
        self.setModal(True)
        self.arrays = arrays
        self._filtered = [int(x) for x in filtered_ids]
        self._selected = [int(selected_id)] if selected_id is not None else []
        layout = QVBoxLayout(self)
        intro = QLabel("Elige el alcance y marca solo los campos que quieres cambiar. Los demás campos no se tocan.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.scope_filtered = QRadioButton(f"Actividades que coinciden con la búsqueda y filtros actuales ({fmt_num(len(self._filtered))})")
        self.scope_selected = QRadioButton("Solo la actividad seleccionada")
        self.scope_selected.setEnabled(bool(self._selected))
        self.scope_filtered.setChecked(True)
        layout.addWidget(self.scope_filtered)
        layout.addWidget(self.scope_selected)
        grid = QGridLayout()
        self.checks: dict[str, QCheckBox] = {}
        self.editors: dict[str, QWidget] = {}

        def row(key, label, editor):
            check = QCheckBox("Cambiar")
            check.setAccessibleName(f"Cambiar {label}")
            r = grid.rowCount()
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(editor, r, 1)
            grid.addWidget(check, r, 2)
            editor.setEnabled(False)
            check.toggled.connect(editor.setEnabled)
            check.toggled.connect(self._update_preview)
            self.checks[key], self.editors[key] = check, editor

        self.skill = QLineEdit()
        self.skill.setPlaceholderText("p. ej. protecciones")
        row("required_skill", "Habilidad requerida", self.skill)
        self.calendar = QComboBox()
        self.calendar.addItem("Del proyecto", None)
        for c in calendars:
            self.calendar.addItem(c["name"], c["id"])
        row("calendar_id", "Calendario", self.calendar)
        self.priority = QSpinBox()
        self.priority.setRange(0, 100)
        self.priority.setValue(50)
        row("priority", "Prioridad (0–100)", self.priority)
        self.hh_mode = QComboBox()
        self.hh_mode.addItem("Fijar HH en", "set")
        self.hh_mode.addItem("Variar HH en %", "pct")
        self.hh_value = QDoubleSpinBox()
        self.hh_value.setRange(-100000, 1_000_000)
        self.hh_value.setDecimals(2)
        hh_box = QWidget()
        hh_layout = QGridLayout(hh_box)
        hh_layout.setContentsMargins(0, 0, 0, 0)
        hh_layout.addWidget(self.hh_mode, 0, 0)
        hh_layout.addWidget(self.hh_value, 0, 1)
        row("work_hours", "HH presupuestadas", hh_box)
        self.crew = QDoubleSpinBox()
        self.crew.setRange(0, 1000)
        self.crew.setDecimals(2)
        self.crew.setValue(1)
        row("crew_size", "Dotación", self.crew)
        self.location = QLineEdit()
        row("location", "Instalación / ubicación", self.location)
        self.discipline = QLineEdit()
        row("discipline", "Disciplina", self.discipline)
        self.outage = QComboBox()
        self.outage.addItem("Requiere desconexión", True)
        self.outage.addItem("No requiere desconexión", False)
        row("requires_outage", "Desconexión", self.outage)
        layout.addLayout(grid)
        for widget in (self.skill, self.location, self.discipline):
            widget.textChanged.connect(self._update_preview)
        for widget in (self.calendar, self.hh_mode, self.outage):
            widget.currentIndexChanged.connect(self._update_preview)
        for widget in (self.priority, self.hh_value, self.crew):
            widget.valueChanged.connect(self._update_preview)
        self.scope_filtered.toggled.connect(self._update_preview)
        if mode == "mantenimiento":
            note = QLabel("En Mantenimiento, los cambios a ocurrencias generadas se reemplazan al volver a generar el plan "
                          "con «reemplazar»: para cambios permanentes edita la tarea periódica.")
            note.setWordWrap(True)
            note.setProperty("role", "caption")
            layout.addWidget(note)
        self.preview = QTableWidget(0, 4)
        self.preview.setHorizontalHeaderLabels(["Código", "Nombre", "Campo", "Antes → después"])
        self.preview.verticalHeader().hide()
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.preview, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Aplicar cambios")
        self.buttons.button(QDialogButtonBox.Ok).setProperty("kind", "primary")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.resize(820, 680)
        self._update_preview()

    # ------------------------------------------------------------------ lógica
    def scope_ids(self) -> list[int]:
        return list(self._filtered if self.scope_filtered.isChecked() else self._selected)

    def changes(self) -> dict:
        """Campos marcados y su valor (HH por porcentaje se resuelve por actividad en ``patches``)."""
        out = {}
        if self.checks["required_skill"].isChecked():
            out["required_skill"] = self.skill.text().strip()
        if self.checks["calendar_id"].isChecked():
            out["calendar_id"] = self.calendar.currentData()
        if self.checks["priority"].isChecked():
            out["priority"] = self.priority.value()
        if self.checks["work_hours"].isChecked():
            out["work_hours"] = (self.hh_mode.currentData(), self.hh_value.value())
        if self.checks["crew_size"].isChecked():
            out["crew_size"] = self.crew.value()
        if self.checks["location"].isChecked():
            out["location"] = self.location.text().strip()
        if self.checks["discipline"].isChecked():
            out["discipline"] = self.discipline.text().strip()
        if self.checks["requires_outage"].isChecked():
            out["requires_outage"] = bool(self.outage.currentData())
        return out

    def _current_hh(self, aid: int) -> float:
        i = self.arrays.id_to_index.get(aid)
        value = float(self.arrays.hh[i]) if i is not None else float("nan")
        return value if math.isfinite(value) else 0.0

    def patches(self) -> list[dict]:
        changes = self.changes()
        if not changes:
            return []
        result = []
        for aid in self.scope_ids():
            patch = {"id": aid}
            for key, value in changes.items():
                if key == "work_hours":
                    how, amount = value
                    current = self._current_hh(aid)
                    patch[key] = round(max(0.0, current * (1 + amount / 100) if how == "pct" else amount), 4)
                else:
                    patch[key] = value
            result.append(patch)
        return result

    def validation_error(self) -> str | None:
        ids, changes = self.scope_ids(), self.changes()
        if not ids:
            return "No hay actividades en el alcance elegido."
        if not changes:
            return "Marca al menos un campo para cambiar."
        if len(ids) > MAX_BATCH:
            return (f"El alcance tiene {fmt_num(len(ids))} actividades y el máximo por lote es {fmt_num(MAX_BATCH)}. "
                    "Acota la búsqueda o aplica el cambio en varias tandas.")
        if "work_hours" in changes:
            how, amount = changes["work_hours"]
            if how == "set" and amount < 0:
                return "Las HH no pueden ser negativas."
            if how == "pct" and amount <= -100:
                return "Una variación de −100 % o menos dejaría las HH en cero o negativas."
        return None

    def _before(self, aid: int, key: str) -> str:
        i = self.arrays.id_to_index.get(aid)
        if i is None:
            return "—"
        if key == "work_hours":
            return _hh_text(self._current_hh(aid))
        if key == "required_skill":
            return str(self.arrays.skill[i] or "—")
        if key == "location":
            return str(self.arrays.location[i] or "—")
        if key == "requires_outage":
            return "Sí" if bool(self.arrays.outage[i]) else "No"
        return "…"

    def _after(self, key: str, value) -> str:
        if key == "work_hours":
            return _hh_text(value)
        if key == "calendar_id":
            index = self.calendar.findData(value)
            return self.calendar.itemText(index) if index >= 0 else str(value)
        if key == "requires_outage":
            return "Sí" if value else "No"
        if isinstance(value, float):
            return fmt_num(value, 2)
        return str(value) if value not in (None, "") else "(vacío)"

    def _update_preview(self, *_):
        patches = self.patches()
        error = self.validation_error()
        labels = {"required_skill": "Habilidad", "calendar_id": "Calendario", "priority": "Prioridad", "work_hours": "HH",
                  "crew_size": "Dotación", "location": "Instalación", "discipline": "Disciplina", "requires_outage": "Desconexión"}
        rows = []
        for patch in patches[:PREVIEW_ROWS]:
            i = self.arrays.id_to_index.get(patch["id"])
            code = str(self.arrays.codes[i]) if i is not None else f"#{patch['id']}"
            name = str(self.arrays.names[i]) if i is not None else ""
            for key, value in patch.items():
                if key != "id":
                    rows.append((code, name, labels.get(key, key), f"{self._before(patch['id'], key)} → {self._after(key, value)}"))
        self.preview.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, text in enumerate(values):
                self.preview.setItem(r, c, QTableWidgetItem(text))
        self.preview.resizeColumnsToContents()
        count = len(self.scope_ids())
        text = f"Se modificarán {fmt_num(count)} actividades." if patches else "Sin cambios marcados."
        if len(patches) > PREVIEW_ROWS:
            text += f" La vista previa muestra las primeras {PREVIEW_ROWS}."
        if error and patches:
            text = error
        self.summary.setText(text)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(error is None)

    def _accept(self):
        if self.validation_error() is None:
            self.accept()


def filtered_ids(arrays, text: str, critical: bool, outage: bool = False) -> list[int]:
    """Identificadores que coinciden con la búsqueda y filtros vigentes de la página."""
    if arrays.n == 0:
        return []
    mask = arrays.filter(text, critical)
    if outage:
        mask &= np.asarray(arrays.outage, dtype=bool)
    return [int(x) for x in arrays.ids[mask]]
