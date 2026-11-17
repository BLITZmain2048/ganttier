"""Búsqueda global entre proyectos (P5-06): código, nombre, instalación, TAG o habilidad."""
from __future__ import annotations

from .widgets.responsive import ResponsiveDialog

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QTableView, QVBoxLayout

from .widgets.common import Column, DictTableModel, fit_columns, fmt_date, fmt_pct, run_task, setup_table

MIN_CHARS = 2
LIMIT = 200


def _project(row):
    project = row.get("project") or {}
    return f"{project.get('name', '')}{' (mant.)' if project.get('mode') == 'mantenimiento' else ''}"


class GlobalSearchDialog(ResponsiveDialog):
    """Consulta con espera de 250 ms; doble clic o Enter abre el resultado en su proyecto."""
    chosen = Signal(dict)

    def __init__(self, state, parent=None, text: str = ""):
        super().__init__(parent)
        self.state = state
        self.selected_row: dict | None = None
        self._request = 0
        self.setWindowTitle("Buscar en todos los proyectos")
        self.resize(980, 560)
        layout = QVBoxLayout(self)
        self.query = QLineEdit(text)
        self.query.setPlaceholderText("Código, nombre, instalación, TAG o habilidad (mínimo 2 caracteres)")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("Texto a buscar en todos los proyectos")
        layout.addWidget(self.query)
        self.status = QLabel()
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)
        self.model = DictTableModel([
            Column("type", "Tipo"), Column("code", "Código"), Column("name", "Nombre"),
            Column("project", "Proyecto", get=_project), Column("location", "Instalación"),
            Column("equipment", "TAG"), Column("required_skill", "Habilidad"),
            Column("planned_start", "Inicio", get=lambda r: fmt_date(r.get("planned_start"))),
            Column("planned_finish", "Término", get=lambda r: fmt_date(r.get("planned_finish"))),
            Column("percent_complete", "Avance", align_right=True,
                   get=lambda r: "" if r.get("percent_complete") is None else fmt_pct(r.get("percent_complete"))),
        ])
        self.table = QTableView()
        self.table.setModel(self.model)
        setup_table(self.table, sortable=False)
        self.table.setAccessibleName("Resultados de la búsqueda")
        self.table.doubleClicked.connect(lambda index: self.choose(index.row()))
        layout.addWidget(self.table, 1)
        hint = QLabel("Enter o doble clic: abrir en su proyecto    Esc: cerrar. Orden: código exacto, TAG exacto, "
                      "prefijo y otras coincidencias.")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.timer = QTimer(self, singleShot=True, interval=250)
        self.timer.timeout.connect(self.run_query)
        self.query.textChanged.connect(lambda *_: self.timer.start())
        self.query.returnPressed.connect(self._enter)
        self.run_query()

    def _enter(self):
        if self.timer.isActive():
            self.timer.stop()
            self.run_query(then_choose=True)
        elif self.model.rowCount():
            index = self.table.currentIndex()
            self.choose(index.row() if index.isValid() else 0)

    def run_query(self, then_choose: bool = False):
        text = self.query.text().strip()
        self._request += 1
        request = self._request
        if len(text) < MIN_CHARS:
            self.model.set_rows([])
            self.status.setText("Escribe al menos dos caracteres.")
            return
        self.status.setText("Buscando…")
        client = self.state.client

        def done(rows):
            if request != self._request:
                return
            self.model.set_rows(rows)
            projects = len({(r.get("project") or {}).get("id") for r in rows})
            more = " (se muestran los primeros 200; precisa la búsqueda)" if len(rows) >= LIMIT else ""
            self.status.setText(f"{len(rows)} resultado(s) en {projects} proyecto(s){more}" if rows else "Sin resultados.")
            if rows:
                self.table.selectRow(0)
                fit_columns(self.table, max_w=320)
                if then_choose:
                    self.choose(0)

        def failed(exc):
            if request == self._request:
                self.status.setText(f"No se pudo buscar: {getattr(exc, 'message', exc)}")
        run_task(lambda: client.get("/search", params={"q": text, "limit": LIMIT}), done, failed)

    def choose(self, row: int):
        if 0 <= row < self.model.rowCount():
            self.selected_row = self.model.row_dict(row)
            self.chosen.emit(self.selected_row)
            self.accept()
