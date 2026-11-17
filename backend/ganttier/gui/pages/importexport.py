"""Intercambio Excel con validación previa y resultados por hoja/fila."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFileDialog, QLabel, QTableView

from ..client import ApiError
from ..widgets.common import (
    Column, DictTableModel, button, confirm, fmt_num, hbox,
    notify, run_task, save_bytes_dialog, show_error,
)
from .base import Page
from ..widgets.responsive import scroll_layout

# UX1 puede integrarse antes o después de esta página.
from .. import accesibilidad, theme
from ..widgets import common as _common
from ..widgets.icons import icon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

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



SUMMARY_LABELS = {
    "calendars": "Calendarios", "resources": "Recursos", "wbs": "WBS",
    "activities": "Actividades", "relations": "Relaciones", "assignments": "Asignaciones",
    "periodic_tasks": "Tareas periódicas", "progress": "Avance", "calendarios": "Calendarios",
    "recursos": "Recursos", "actividades": "Actividades", "relaciones": "Relaciones",
    "asignaciones": "Asignaciones", "tareas_periodicas": "Tareas periódicas", "avance": "Avance",
}


def example_files():
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[4]
    return sorted((root / "ejemplos").glob("*.xlsx"))


def validate_new_file(path):
    """La API de creación no ofrece dry_run: lector compartido, sin abrir BD."""
    from dataclasses import asdict
    from ganttier.excel_io.importer import parse_workbook
    bundle = parse_workbook(path, None)
    keys = ("calendars", "resources", "wbs", "activities", "relations", "assignments", "periodic_tasks", "progress")
    return {"summary": {key: len(getattr(bundle, key)) for key in keys},
            "errors": [asdict(issue) for issue in bundle.issues], "local_preview": True}


class ImportExportPage(Page):
    title = "Importar / Exportar"
    subtitle = "Prepara tu libro, valida los datos e importa con confianza"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.root.setSpacing(_SPACE["md"])
        self.path = None
        self.result = None
        self.working = False
        self.last_import = None
        self._validated = None
        self._context_pid = state.pid
        self.setAcceptDrops(True)
        self.assisted_button = _button('Importar archivo de cliente…', self.import_assisted, icon_name='file-spreadsheet', primary=True)
        self.root.addWidget(self.assisted_button)
        templates = Section("1 · Plantilla / ejemplo")
        hint = QLabel("Completa el libro Excel y valídalo antes de importar. Las plantillas incluyen instrucciones y ejemplos.")
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        templates.body.addWidget(hint)
        templates.body.addLayout(hbox(_button("Plantilla de proyecto…", lambda: self.download_template("proyecto"), icon_name="file-down"),
                                  _button("Plantilla de mantenimiento…", lambda: self.download_template("mantenimiento"), icon_name="file-down"), None))
        self.examples = QComboBox()
        for path in example_files():
            self.examples.addItem(path.stem.replace("_", " "), str(path))
        example_button = _button("Guardar ejemplo…", self.download_example, icon_name="download")
        example_button.setEnabled(self.examples.count() > 0)
        self.export_button = _button("Exportar proyecto abierto…", self.export_project, icon_name="file-up")
        self.gantt_export_button = _button("Exportar Gantt a Excel…", self.export_gantt, icon_name="file-spreadsheet")
        templates.body.addWidget(self.gantt_export_button)
        templates.body.addLayout(hbox(self.examples, example_button, None, self.export_button))
        self.root.addWidget(templates)
        upload = Section("2 · Archivo y validación")
        self.file_label = QLabel("Arrastra aquí un archivo .xlsx o usa «Elegir archivo…».")
        self.file_label.setTextFormat(Qt.PlainText)
        self.file_label.setWordWrap(True)
        self.pick_button = _button("Elegir archivo…", self.choose_file, icon_name="upload")
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("ux4DropZone")
        drop_layout = QHBoxLayout(self.drop_zone)
        glyph = self.drop_icon = QLabel()
        glyph.setPixmap(icon("upload").pixmap(32, 32))
        drop_layout.addWidget(glyph)
        drop_layout.addWidget(self.file_label, 1)
        drop_layout.addWidget(self.pick_button)
        self.drop_zone.setMinimumHeight(80)
        upload.body.addWidget(self.drop_zone)
        self.on_theme()
        self.target = QComboBox()
        self.target.addItem("Crear proyecto nuevo", "new")
        self.target.addItem("Importar al proyecto abierto", "current")
        self.target.setCurrentIndex(1 if state.pid else 0)
        self.mode = QComboBox()
        self.mode.addItem("Combinar (actualizar por código y agregar)", "merge")
        self.mode.addItem("Reemplazar contenido del proyecto", "replace")
        upload.body.addLayout(hbox(QLabel("Destino"), self.target, QLabel("Modo"), self.mode))
        self.validate_button = _button("Volver a validar", self.validate_file, icon_name="circle-check")
        self.import_button = _button("Importar", self.import_file, primary=True, icon_name="file-down")
        upload.body.addLayout(hbox(self.validate_button, None))
        self.root.addWidget(upload)
        results = Section("3 · Importar", "Revisa el resultado antes de confirmar la importación.")
        results.body.addWidget(_toolbar(self.import_button, None))
        banner_class = getattr(_common, "StatusBanner", None)
        self.result_banner = banner_class() if banner_class else QLabel()
        self.result_banner.setProperty("role", "caption")
        results.body.addWidget(self.result_banner)
        self._banner("info", "Selecciona un archivo para comenzar.")
        self.summary = QLabel("Selecciona un archivo para comenzar.")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setProperty("role", "caption")
        results.body.addWidget(self.summary)
        self.issue_model = DictTableModel([
            Column("severity", "Tipo", get=lambda r: "Error" if r.get("severity", "error") == "error" else "Advertencia"),
            Column("sheet", "Hoja"), Column("row", "Fila", kind="int"),
            Column("field", "Campo"), Column("message", "Mensaje"),
        ])
        self.issue_table = QTableView()
        accesibilidad.marcar(self.issue_table).setAccessibleName("Avisos de importación")
        self.issue_proxy = QSortFilterProxyModel(self)
        self.issue_proxy.setSourceModel(self.issue_model)
        self.issue_proxy.setFilterKeyColumn(0)
        self.issue_table.setModel(self.issue_proxy)
        _setup(self.issue_table)
        self.severity = QComboBox()
        self.severity.addItem("Todos los resultados", "")
        self.severity.addItem("Solo errores", "Error")
        self.severity.addItem("Solo advertencias", "Advertencia")
        self.severity.currentIndexChanged.connect(lambda *_: self.issue_proxy.setFilterFixedString(self.severity.currentData()))
        results.body.addWidget(self.severity)
        self.issue_table.verticalHeader().hide()
        self.issue_table.horizontalHeader().setStretchLastSection(True)
        self.issue_table.setColumnWidth(1, 160)
        self.issue_table.setColumnWidth(3, 170)
        self.issue_table.setWordWrap(True)
        results.body.addWidget(self.issue_table, 1)
        self.root.addWidget(results, 1)
        self.target.currentIndexChanged.connect(self.invalidate)
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.issue_table.hide()
        self.severity.hide()
        self.refresh()

        self.issue_table.setMinimumHeight(150)
        self.content_scroll = scroll_layout(self.root)
        self.root.addWidget(_toolbar(self.pick_button, self.validate_button, self.import_button, None))

    def refresh(self):
        if self.state.pid != self._context_pid:
            self._context_pid = self.state.pid
            self.invalidate()
        if not self.state.pid and self.target.currentData() == "current":
            self.target.setCurrentIndex(0)
        self.target.model().item(1).setEnabled(bool(self.state.pid))
        self.update_controls()

    def import_assisted(self):
        from ..importador_asistido import abrir_importador
        pid = abrir_importador(self.state, self)
        if pid is None:
            return
        self.state.projects_changed.emit()
        self.state.open_project_async(pid, on_done=lambda _: notify('Proyecto creado desde la planilla del cliente.'), on_error=self._failed)

    def update_controls(self):
        current = self.target.currentData() == "current"
        self.mode.setEnabled(current and not self.working)
        self.target.setEnabled(not self.working)
        self.pick_button.setEnabled(not self.working)
        self.export_button.setEnabled(bool(self.state.pid))
        self.gantt_export_button.setEnabled(bool(self.state.pid))
        ready = bool(self.path) and not self.working and (not current or bool(self.state.pid))
        self.validate_button.setEnabled(ready)
        clean = self.result is not None and not any(e.get("severity", "error") == "error" for e in self.result.get("errors", []))
        self.import_button.setEnabled(ready and clean and self._validated is not None)

    def invalidate(self, *_):
        self._validated = None
        self.result = None
        self.issue_model.set_rows([])
        self._banner("info", "Valida el archivo para continuar.")
        self.issue_table.hide()
        self.severity.hide()
        self.summary.setText("Valida el archivo para revisar errores y advertencias.")
        self.update_controls()

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Seleccionar libro Excel", "", "Libros Excel (*.xlsx)")
        if path:
            self.load_file(path)

    def pick_file(self, path):
        if self.working:
            return False
        path = Path(path)
        if path.suffix.lower() != ".xlsx" or not path.is_file():
            notify("Selecciona un archivo Excel .xlsx existente.", "error")
            return False
        self.path = path
        self.file_label.setText(path.name)
        self.file_label.setToolTip(str(path))
        self.invalidate()
        return True

    def load_file(self, path):
        """Carga y valida también al soltar un libro en la ventana principal."""
        if self.pick_file(path):
            self.validate_file()

    def on_theme(self):
        if hasattr(self, "drop_zone"):
            self.drop_icon.setPixmap(icon("upload").pixmap(32, 32))
            self.drop_zone.setStyleSheet(
                f"QFrame#ux4DropZone {{ border: 1px dashed {theme.tokens().text3}; border-radius: 8px; }}")

    def _banner(self, kind, title):
        setter = getattr(self.result_banner, "set", None)
        if setter:
            setter(kind, title)
        else:
            self.result_banner.setText(title)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.working and len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() == ".xlsx":
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and not self.working:
            self.load_file(urls[0].toLocalFile())
            event.acceptProposedAction()

    def _signature(self):
        stat = self.path.stat()
        return (str(self.path.resolve()), stat.st_mtime_ns, stat.st_size,
                self.target.currentData(), self.mode.currentData(), self.state.pid)

    def _show_result(self, result, prefix):
        self.result = result
        issues = result.get("errors") or []
        self.issue_model.set_rows(issues)
        errors = sum(i.get("severity", "error") == "error" for i in issues)
        warnings = len(issues) - errors
        self._banner("critical" if errors else "warning" if warnings else "good",
                     f"{errors} errores · {warnings} advertencias" if errors else
                     f"{warnings} advertencias" if warnings else "Sin errores")
        self.issue_table.setVisible(bool(issues))
        self.severity.setVisible(bool(issues))
        _fit(self.issue_table)
        parts = [prefix, f"{fmt_num(errors)} errores · {fmt_num(len(issues) - errors)} advertencias"]
        summary = result.get("summary") or {}
        parts += [f"{SUMMARY_LABELS.get(k, k.replace('_', ' '))}: {fmt_num(v)}" for k, v in summary.items()]
        if result.get("local_preview"):
            parts.append("Se validó el libro sin guardar. Las referencias a ocurrencias y recursos se comprueban nuevamente al crear el proyecto.")
        self.summary.setText("\n".join(parts))
        self.update_controls()

    def _failed(self, exc):
        self.working = False
        self._validated = None
        if isinstance(exc, ApiError):
            result = dict(exc.body) if isinstance(exc.body, dict) else {}
            if not result.get("errors"):
                result["errors"] = [{"severity": "error", "message": exc.message}]
            self._show_result(result, "No se completó la operación. " + exc.message)
        else:
            self._show_result({"errors": [{"severity": "error", "message": str(exc)}]}, "No se completó la operación.")

    def validate_file(self):
        if self.working or not self.path:
            return
        try:
            signature = self._signature()
        except OSError as exc:
            self._failed(exc)
            return
        pid, path, mode, target = self.state.pid, self.path, self.mode.currentData(), self.target.currentData()
        if target == "current" and not pid:
            return
        self.working = True
        self._validated = None
        self.update_controls()
        def done(result):
            self.working = False
            try:
                if signature != self._signature():
                    self.invalidate()
                    self.summary.setText("Cambió el archivo o el destino. Vuelve a validar.")
                    return
            except OSError as exc:
                self._failed(exc)
                return
            self._validated = signature
            self._show_result(result, "Validación terminada; no se guardaron cambios.")
        run_task(lambda: validate_new_file(path) if target == "new" else self.state.client.import_file(pid, path, mode, dry_run=True),
                 done, self._failed, busy="Validando libro Excel…")

    def import_file(self):
        if self.working or not self.path or self._validated is None or not self.result:
            return
        if any(e.get("severity", "error") == "error" for e in self.result.get("errors", [])):
            return
        try:
            if self._validated != self._signature():
                self.invalidate()
                self.summary.setText("Cambió el archivo o el destino. Vuelve a validar antes de importar.")
                return
        except OSError as exc:
            self._failed(exc)
            return
        target, mode, pid, path = self.target.currentData(), self.mode.currentData(), self.state.pid, self.path
        if target == "current" and mode == "replace" and not confirm(self, "Reemplazar contenido", "Se reemplazará el contenido del proyecto abierto con el libro validado. ¿Continuar?"):
            return
        self.working = True
        self.update_controls()
        client = self.state.client
        def work():
            return client.import_new_project(path) if target == "new" else client.import_file(pid, path, mode)
        def done(result):
            self._validated = None
            if result.get("errors") and any(e.get("severity", "error") == "error" for e in result["errors"]):
                self.working = False
                self._show_result(result, "El libro contiene errores; revisa las filas indicadas.")
                return
            imported_pid = result["id"] if target == "new" else pid
            # Leer en el trabajador y publicar el estado en el hilo de la GUI.
            # No cambiar el proyecto si el usuario navegó a otro mientras se importaba.
            def reload():
                return client.project(imported_pid), client.calendars(), client.resources(imported_pid)
            def loaded(data):
                project, calendars, resources = data
                if self.state.pid == pid:
                    self.state.project = project
                    self.state.calendars, self.state.resources = calendars, resources
                    self.state.catalogs_changed.emit()
                    self.state.project_changed.emit(project)
                self.state.projects_changed.emit()
                self.state.bump_schedule()
                self.working = False
                self.last_import = result
                self._show_result(result if target == "current" else {"summary": {}, "errors": []},
                                  f"Importación completada: {project['name']}.")
                notify("Importación completada")
            def reload_failed(exc):
                self.working = False
                self.last_import = result
                self._validated = None
                self.summary.setText(f"El archivo se importó, pero no se pudo actualizar la pantalla. Abre de nuevo el proyecto. {exc}")
                self.update_controls()
            run_task(reload, loaded, reload_failed, busy="Actualizando proyecto y catálogos…")
        run_task(work, done, self._failed, busy="Importando libro Excel…")

    def _save(self, data, name):
        try:
            save_bytes_dialog(self, data, name)
        except OSError as exc:
            show_error(exc, self)

    def download_template(self, kind):
        run_task(lambda: self.state.client.template(kind), lambda data: self._save(data, f"Plantilla_{kind}.xlsx"),
                 busy="Preparando plantilla…")

    def download_example(self):
        path = self.examples.currentData()
        if path:
            source = Path(path)
            run_task(source.read_bytes, lambda data: self._save(data, source.name), busy="Leyendo ejemplo…")

    def export_project(self):
        pid = self.state.pid
        if pid:
            run_task(lambda: self.state.client.export_xlsx(pid), lambda data: self._save(data, f"proyecto_{pid}.xlsx"),
                       busy="Exportando proyecto…")

    def export_gantt(self):
        from ..gantt_export import open_gantt_export
        open_gantt_export(self.state, self)
