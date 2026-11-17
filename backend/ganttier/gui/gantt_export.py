"""Diálogo de exportación independiente del panel de generación del programa."""

from .widgets.responsive import ResponsiveDialog
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from .widgets.common import notify, run_task, save_bytes_dialog


class GanttExportDialog(ResponsiveDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Exportar Gantt a Excel…')
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)
        self.mode = QComboBox()
        self.mode.addItem('Resumen por nivel', 'nivel')
        self.mode.addItem('Por trabajador', 'trabajador')
        self.group = QComboBox()
        for label,value in [('WBS','wbs'),('Instalación / sitio','sitio'),('Equipo','equipo'),('Habilidad','habilidad'),('Sitio y WBS','sitio/wbs'),('Equipo y WBS','equipo/wbs')]:
            self.group.addItem(label,value)
        self.level = QSpinBox()
        self.level.setRange(0,99)
        self.level.setSpecialValueText('Último nivel')
        self.scale = QComboBox()
        for label,value in [('Día','day'),('Semana','week'),('Mes','month')]:
            self.scale.addItem(label,value)
        self.scale.setCurrentIndex(1)
        self.first, self.last = QLineEdit(), QLineEdit()
        for entry in (self.first,self.last):
            entry.setPlaceholderText('DD-MM-AAAA · vacío = todo el programa')
        self.critical = QCheckBox('Resaltar grupos con actividades críticas')
        for label,widget in [('Modo',self.mode),('Agrupar por',self.group),('Nivel WBS',self.level),('Escala',self.scale),('Desde',self.first),('Hasta',self.last),('',self.critical)]:
            form.addRow(label,widget)
        info = QLabel('Se exporta el programa vigente completo dentro del rango. Máximo 520 períodos. '
                      'Más de 500 grupos se reúnen automáticamente; las personas se reparten en hojas de 500 filas. '
                      'El libro incluye leyenda y capacidad por período. Los filtros de la vista no se aplican.')
        info.setWordWrap(True)
        root.addWidget(info)
        self.error = QLabel()
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Exportar')
        buttons.button(QDialogButtonBox.Cancel).setText('Cancelar')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self._controls)
        self.group.currentIndexChanged.connect(self._controls)
        self._controls()

    def _controls(self):
        level = self.mode.currentData() == 'nivel'
        self.group.setEnabled(level)
        self.level.setEnabled(level and 'wbs' in self.group.currentData())
        self.critical.setEnabled(level)

    def parameters(self):
        from datetime import datetime
        result = dict(mode=self.mode.currentData(),group_by=self.group.currentData(),scale=self.scale.currentData(),
                      critical='true' if self.critical.isEnabled() and self.critical.isChecked() else 'false')
        if self.level.isEnabled() and self.level.value():
            result['group_by'] += f':{self.level.value()}'
        for key,entry in [('from',self.first),('to',self.last)]:
            if entry.text().strip():
                try:
                    result[key] = datetime.strptime(entry.text().strip(),'%d-%m-%Y').date().isoformat()
                except ValueError as exc:
                    raise ValueError('Escriba fechas válidas con formato DD-MM-AAAA.') from exc
        if result.get('from','') > result.get('to','9999-12-31'):
            raise ValueError('La fecha desde no puede ser posterior a hasta.')
        return result

    def accept(self):
        try:
            self.parameters()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


def open_gantt_export(state, parent=None):
    pid = state.pid
    if not pid:
        notify('Abra un proyecto para exportar su Gantt.', 'info')
        return
    dialog = GanttExportDialog(parent)
    if dialog.exec() != QDialog.Accepted:
        return
    params = dialog.parameters()
    client = state.client
    run_task(lambda: client.gantt_xlsx(pid, **params),
             lambda data: save_bytes_dialog(parent,data,f'gantt-{pid}-{params["mode"]}.xlsx'),
             busy='Exportando Gantt a Excel…')
