"""D03: la tabla del Gantt no publica millones de celdas a la accesibilidad."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QTableView
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ganttier.gui import accesibilidad


class Grande(QAbstractTableModel):
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 100_000

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 11

    def data(self, index, role=Qt.DisplayRole):
        return f"{index.row()},{index.column()}" if role == Qt.DisplayRole else None


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    accesibilidad.install()
    QAccessible.setActive(True)
    yield app
    QAccessible.setActive(False)


def test_tabla_marcada_no_publica_celdas(app):
    tabla = accesibilidad.marcar(QTableView())
    tabla.setModel(Grande())
    tabla.setAccessibleName("Tabla de actividades")
    interfaz = QAccessible.queryAccessibleInterface(tabla)
    assert interfaz.role() == QAccessible.Role.Table
    assert interfaz.childCount() == 0
    assert interfaz.text(QAccessible.Text.Name) == "Tabla de actividades"
    assert "100.000 filas" in interfaz.text(QAccessible.Text.Description)


def test_tabla_sin_marcar_conserva_la_accesibilidad_completa(app):
    tabla = QTableView()
    tabla.setModel(Grande())
    interfaz = QAccessible.queryAccessibleInterface(tabla)
    assert interfaz.childCount() > 1_000_000


def test_gantt_y_avisos_de_importacion_estan_marcados(app):
    from ganttier.gui.gantt.view import GanttWidget
    widget = GanttWidget()
    assert widget.table.property(accesibilidad.PROPIEDAD) is True
    assert QAccessible.queryAccessibleInterface(widget.table).childCount() == 0
