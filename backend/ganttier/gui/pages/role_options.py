"""Shared opt-in controls for equivalent-block role scheduling."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit, QLabel


class RoleOptions(QWidget):
    changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        form = self.form = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItem("HH fraccionarias (habitual)", "fractional")
        self.mode.addItem("Roles indivisibles por actividad", "indivisible")
        self.hours = QDoubleSpinBox()
        self.hours.setRange(0.1, 24)
        self.hours.setValue(4)
        self.horizon = QSpinBox()
        self.horizon.setRange(0, 2000)
        self.horizon.setSpecialValueText("Usar fin del proyecto")
        self.target = QLineEdit()
        self.target.setPlaceholderText("Vacío: terminar todas las actividades")
        self.help = QLabel(
            "Use roles indivisibles cuando cada rol requiere una persona distinta, constante durante toda la actividad. "
            "Solo proyectos sin avance, relaciones FS y dedicación completa. Requiere OR-Tools y calendarios continuos "
            "con las horas del bloque, sin feriados salvo ausencias explícitas. Cada fecha representa un bloque equivalente; "
            "no es una jornada civil ni un turno intradía. Primero minimiza el término del objetivo y luego el costo del personal. "
            "Los pesos de nivelación no intervienen. El programa valida estas condiciones antes de resolver.")
        self.help.setWordWrap(True)
        self.mode.setToolTip(self.help.text())
        form.addRow("Asignación", self.mode)
        form.addRow("Horas por bloque", self.hours)
        form.addRow("Horizonte (bloques)", self.horizon)
        form.addRow("Código de actividad objetivo", self.target)
        form.addRow(self.help)
        self.mode.currentIndexChanged.connect(self._changed)
        self._changed()

    def _changed(self, *_):
        active = self.mode.currentData() == "indivisible"
        for widget in (self.hours, self.horizon, self.target, self.help):
            widget.setEnabled(active)
            self.form.setRowVisible(widget, active)
        self.changed.emit(active)

    def set_project_mode(self, mode):
        available = mode == "proyecto"
        if not available:
            self.mode.setCurrentIndex(0)
        self.setVisible(available)

    def values(self):
        if self.mode.currentData() != "indivisible":
            # Preserve the existing fractional payload; the API supplies its default.
            return {}
        return dict(role_mode="indivisible", block_hours=self.hours.value(),
                    horizon_blocks=self.horizon.value() or None, target_code=self.target.text().strip() or None,
                    method="auto", respect_resource_limits=True, allow_overtime=False)
