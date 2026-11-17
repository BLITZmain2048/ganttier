"""Tablero nominal de lectura; cálculos y exportación fuera del hilo de GUI."""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel
from PySide6.QtWidgets import QTableView, QLabel, QFileDialog, QGridLayout, QPushButton
from shiboken6 import isValid

from .base import Page
from .performance import date_editor, choices
from ..widgets.common import run_task


COLUMNS = [("name", "Trabajador"), ("skills_text", "Habilidades"),
           ("planned_hours", "HH plan"), ("actual_hours", "HH reales"),
           ("capacity", "HH capacidad"), ("utilization", "Utilización %"),
           ("absence_days", "Días con ausencia"), ("hh_efficiency", "Eficiencia HH")]


class SummaryModel(QAbstractTableModel):
    def __init__(self, columns=COLUMNS, parent=None):
        super().__init__(parent)
        self.columns, self.rows = columns, []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section][1]

    def summary(self, row):
        return "; ".join(f"{title}: {self.display(row.get(key))}" for key, title in self.columns)

    @staticmethod
    def display(value):
        return "Sin datos" if value is None else f"{value:,.2f}" if isinstance(value, float) else str(value)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        value = row.get(self.columns[index.column()][0])
        if role == Qt.UserRole:
            return value
        if role in (Qt.AccessibleTextRole, Qt.ToolTipRole):
            return self.summary(row)
        if role == Qt.DisplayRole:
            return self.display(value)


def export_rows(path, rows, columns=COLUMNS):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Trabajadores")
    ws.append([title for _, title in columns])
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            cell = WriteOnlyCell(ws, value=value)
            if isinstance(value, str):
                cell.data_type = "s"  # nombres de usuario no se interpretan como fórmulas
            cells.append(cell)
        ws.append(cells)
    wb.save(path)
    return str(path)


def load_board(client, pid, start, end):
    resources = client.resources(pid)
    perf = client.performance(pid, start, end)
    profile = client.load(pid, group="resource", period="month", date_from=start, date_to=end)
    capacities = {s["key"]: sum(s["capacity"]) for s in profile["series"]}
    names = Counter(r["name"] for r in resources)
    by_id = {r["id"]: r for r in resources}
    rows = []
    for kpi in perf:
        resource = by_id.get(kpi["resource_id"], {})
        if resource.get("kind") != "trabajador":
            continue
        row = dict(kpi)
        row["skills"] = resource.get("skills") or []
        row["skills_text"] = ", ".join(row["skills"])
        row["capacity"] = capacities.get(row["name"]) if names[row["name"]] == 1 else None
        cap = row["capacity"]
        row["utilization"] = 100*row["planned_hours"]/cap if cap else None
        if resource.get("project_id") is not None and resource.get("max_units", 1) == 1:
            absences = client.get(f"/resources/{row['resource_id']}/absences")
            row["absence_days"] = len({a["date"] for a in absences if start <= a["date"] <= end})
        else:
            row["absence_days"] = None
        rows.append(row)
    return rows


class AsyncPage(Page):
    """Una petición activa por página; último filtro pendiente sustituye al anterior."""
    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._generation = 0
        self._running = False
        self._queued = None
        self.status = QLabel()
        self.status.setWordWrap(True)

    def _project_invalidated(self, project):
        self.invalidate()
        super()._project_invalidated(project)

    def invalidate(self, *_):
        self._generation += 1
        self._queued = None
        self.clear_data()

    def clear_data(self):
        pass

    def submit(self, fn, done):
        self._generation += 1
        ticket, pid = self._generation, self.state.pid
        self._queued = (ticket, pid, fn, done)
        self.status.setText("Cargando…")
        self._dispatch()

    def _dispatch(self):
        if self._running or self._queued is None:
            return
        ticket, pid, fn, done = self._queued
        self._queued = None
        self._running = True
        def deliver(result, error=False):
            if not isValid(self):
                return
            self._running = False
            if ticket == self._generation and pid == self.state.pid:
                if error:
                    self.status.setText(f"No se pudo cargar: {result}. Usa Actualizar para reintentar.")
                else:
                    done(result)
            self._dispatch()
        run_task(fn, deliver, lambda error: deliver(error, True))


class TableroTrabajadoresPage(AsyncPage):
    title = "Tablero por trabajador"
    subtitle = "Plan, ejecución y capacidad del periodo"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.rows = []
        today = (state.project or {}).get("data_date") or date.today()
        self.start, self.end = date_editor(today, "Desde"), date_editor(today, "Hasta")
        self.skill = choices([(None, "Todas las habilidades")], "Habilidad")
        controls = QGridLayout()
        for col, (label, widget) in enumerate((("Desde", self.start), ("Hasta", self.end), ("Habilidad", self.skill))):
            controls.addWidget(QLabel(label), 0, col)
            controls.addWidget(widget, 1, col)
        refresh = QPushButton("Actualizar")
        refresh.clicked.connect(self.refresh)
        self.export = QPushButton("Exportar a Excel…")
        self.export.clicked.connect(self.export_excel)
        controls.addWidget(refresh, 2, 0)
        controls.addWidget(self.export, 2, 1)
        self.root.addLayout(controls)
        self.root.addWidget(self.status)
        self.model = SummaryModel()
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.UserRole)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.setAccessibleName("Resumen por trabajador")
        self.table.horizontalHeader().setDefaultSectionSize(150)
        self.table.setColumnWidth(0, 220)
        self.root.addWidget(self.table, 1)
        self.summary = QLabel("Selecciona una fila para leer su resumen.")
        self.summary.setWordWrap(True)
        self.root.addWidget(self.summary)
        note = QLabel("Utilización = HH plan / capacidad. Eficiencia = HH ganadas / reales. "
                      "Capacidad según perfil del servidor; nombres duplicados: sin datos. Ausencias: días registrados, incluidos parciales.")
        note.setWordWrap(True)
        self.root.addWidget(note)
        self.table.selectionModel().currentRowChanged.connect(self._selected)
        self.skill.currentIndexChanged.connect(self.filter_rows)
        self.start.dateChanged.connect(self.invalidate)
        self.end.dateChanged.connect(self.invalidate)
        self.clear_data()

    def clear_data(self):
        if hasattr(self, "model"):
            self.rows = []
            self.model.set_rows([])
            self.export.setEnabled(False)
            self.summary.setText("")

    def refresh(self, *_):
        self.invalidate()
        pid, client = self.state.pid, self.state.client
        start, end = self.start.date().toString(Qt.ISODate), self.end.date().toString(Qt.ISODate)
        if pid is None or start > end:
            self.status.setText("Abre un proyecto." if pid is None else "Desde debe ser anterior o igual a Hasta.")
            return
        self.submit(lambda: load_board(client, pid, start, end), self.loaded)

    def loaded(self, rows):
        self.rows = rows
        current = self.skill.currentData()
        self.skill.blockSignals(True)
        self.skill.clear()
        self.skill.addItem("Todas las habilidades", None)
        for skill in sorted({s for row in rows for s in row["skills"]}):
            self.skill.addItem(skill, skill)
        self.skill.setCurrentIndex(max(0, self.skill.findData(current)))
        self.skill.blockSignals(False)
        self.filter_rows()

    def filter_rows(self, *_):
        skill = self.skill.currentData()
        rows = [r for r in self.rows if skill is None or skill in r["skills"]]
        self.model.set_rows(rows)
        self.export.setEnabled(bool(rows))
        self.status.setText(f"{len(rows)} trabajadores" if rows else "Sin trabajadores para estos filtros.")

    def _selected(self, index, _previous):
        if index.isValid():
            self.summary.setText(self.model.summary(self.model.rows[self.proxy.mapToSource(index).row()]))

    def export_excel(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar tablero", "trabajadores.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        rows = [dict(self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()])
                for i in range(self.proxy.rowCount())]
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        self.submit(lambda: export_rows(Path(path), rows), lambda _: self.status.setText("Excel guardado."))
