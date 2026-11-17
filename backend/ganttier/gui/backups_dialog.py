"""Diálogo de respaldos de la base de datos (P4-06a)."""
from __future__ import annotations

from .widgets.responsive import ResponsiveDialog

from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QTableView, QVBoxLayout

from .widgets.common import (Column, DictTableModel, Toolbar, confirm, fit_columns, fmt_num, notify, run_task,
                             setup_table, show_error)

KIND = {"auto": "Automático diario", "pre": "Previo a una operación", "manual": "Manual"}


def _created(row):
    text = str(row.get("created", ""))
    return f"{text[8:10]}-{text[5:7]}-{text[:4]} {text[11:16]}" if len(text) >= 16 else text


class BackupsDialog(ResponsiveDialog):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Respaldos de la base de datos")
        self.resize(860, 520)
        layout = QVBoxLayout(self)
        intro = QLabel("Ganttier crea un respaldo verificado cada día mientras está abierto, y antes de migrar la base o "
                       "importar en modo reemplazo. Restaurar se aplica al reiniciar y guarda antes la base actual.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        bar = Toolbar()
        self.create_button = bar.add_action("Crear respaldo ahora", self.create_backup, icon="save", primary=True)
        self.export_button = bar.add_action("Guardar copia en…", self.export_selected, icon="download")
        self.restore_button = bar.add_action("Restaurar al reiniciar", self.restore_selected, icon="history", danger=True)
        self.cancel_button = bar.add_action("Cancelar restauración", self.cancel_restore, icon="x", kind="ghost")
        bar.add_stretch()
        layout.addWidget(bar)
        self.model = DictTableModel([
            Column("created", "Fecha", get=_created), Column("kind", "Tipo", get=lambda r: KIND.get(r.get("kind"), r.get("kind"))),
            Column("reason", "Motivo"), Column("projects", "Proyectos", align_right=True),
            Column("activities", "Actividades", get=lambda r: fmt_num(r.get("activities")), align_right=True),
            Column("size", "Tamaño", get=lambda r: f"{fmt_num((r.get('size') or 0) / 1048576, 1)} MB", align_right=True),
            Column("verified", "Verificado", get=lambda r: "Sí" if r.get("verified") else "No"),
        ])
        self.table = QTableView()
        self.table.setModel(self.model)
        setup_table(self.table, sortable=False)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "caption")
        layout.addWidget(self.status)
        self.table.selectionModel().selectionChanged.connect(self._buttons)
        self.data = {}
        self.refresh()

    def _selected(self):
        index = self.table.currentIndex()
        return self.model.rows[index.row()] if index.isValid() and index.row() < len(self.model.rows) else None

    def _buttons(self, *_):
        row = self._selected()
        self.export_button.setEnabled(bool(row))
        self.restore_button.setEnabled(bool(row))
        self.cancel_button.setEnabled(bool(self.data.get("pending_restore")))

    def refresh(self):
        client = self.state.client
        run_task(lambda: client.get("/backups"), self._loaded, lambda exc: show_error(exc, self))

    def _loaded(self, data):
        self.data = data or {}
        self.model.set_rows(self.data.get("items", []))
        fit_columns(self.table, max_w=320)
        pending = self.data.get("pending_restore")
        last = self.data.get("items", [{}])[0] if self.data.get("items") else None
        text = f"Carpeta: {self.data.get('directory', '')}. "
        text += f"Última copia verificada: {_created(last)}." if last else "Aún no hay respaldos."
        if pending:
            text += f" Restauración programada al reiniciar: {pending}."
        self.status.setText(text)
        self._buttons()

    def create_backup(self):
        client = self.state.client
        self.create_button.setEnabled(False)

        def done(info):
            self.create_button.setEnabled(True)
            notify(f"Respaldo creado y verificado: {info.get('file')}")
            self.refresh()

        def failed(exc):
            self.create_button.setEnabled(True)
            show_error(exc, self)
        run_task(lambda: client.post("/backups", {"reason": "manual"}), done, failed, busy="Creando respaldo…")

    def export_selected(self):
        row = self._selected()
        if not row:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Guardar copia del respaldo", row["file"], "Base SQLite (*.db)")
        if not path:
            return
        client = self.state.client

        def save(content):
            with open(path, "wb") as f:
                f.write(content)
            notify("Copia guardada")
        run_task(lambda: client.request("GET", f"/backups/{row['file']}/download", raw=True), save,
                 lambda exc: show_error(exc, self), busy="Descargando respaldo…")

    def restore_selected(self):
        row = self._selected()
        if not row:
            return
        if not confirm(self, "Restaurar respaldo",
                       f"Al reiniciar Ganttier, la base actual se reemplazará por el respaldo del {_created(row)} "
                       f"({row.get('reason')}). Antes se guardará un respaldo de la base actual. ¿Programar la restauración?"):
            return
        client = self.state.client
        run_task(lambda: client.post(f"/backups/{row['file']}/restore"),
                 lambda result: (notify(result.get("message", "Restauración programada")), self.refresh()),
                 lambda exc: show_error(exc, self))

    def cancel_restore(self):
        client = self.state.client
        run_task(lambda: client.delete("/backups/pending"), lambda _: (notify("Restauración cancelada"), self.refresh()),
                 lambda exc: show_error(exc, self))
