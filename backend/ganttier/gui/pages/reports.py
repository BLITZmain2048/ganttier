"""Informes manuales y automáticos, descargas y programa de desconexiones."""
from __future__ import annotations

from ..widgets.responsive import ResponsiveDialog

from datetime import date, datetime, timedelta
from pathlib import Path
import os
from uuid import uuid4

from PySide6.QtCore import Qt, QSettings, QStandardPaths
from PySide6.QtWidgets import (
    QButtonGroup, QSplitter, QAbstractItemView, QCheckBox, QDialog, QFileDialog, QLabel, QMessageBox,
    QSpinBox, QTableView, QTextBrowser, QVBoxLayout, QWidget,
)

from ..widgets.common import (
    Card, Column, DictTableModel, button, fmt_date, hbox, iso, notify, open_path,
    qdate, run_task, show_error,
)
from .base import Page
from ..state import app_settings
from .performance import choices, date_editor, period_bounds, scroll_body

# UX1 puede integrarse antes o después de esta página.
from .. import theme
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


def _empty(name, title, text="", actions=()):
    cls = getattr(_common, "EmptyState", None)
    if cls:
        return cls(name, title, text, actions=actions)
    widget = QWidget()
    widget.setProperty("emptystate", True)
    layout = QVBoxLayout(widget)
    label = QLabel(title + "\n" + text)
    label.setWordWrap(True)
    layout.addWidget(label)
    for caption, callback, primary in actions:
        layout.addWidget(_button(caption, callback, primary=primary))
    return widget


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



FORMATS = (("pdf", "PDF"), ("xlsx", "Excel"), ("docx", "Word"), ("html", "HTML"))
PERIODS = {"weekly": "Semanal", "monthly": "Mensual", "annual": "Anual"}


def emission_date(row):
    value = row.get("emitted_at") or row.get("generated_at")
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return str(value)


class ReportsPage(Page):
    title = "Informes"
    subtitle = "Genera, consulta y programa los informes del proyecto"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.root.setSpacing(_SPACE["md"])
        self._pid = None
        self._request = 0
        self._config_dirty = False   # A3-08: cambios de configuración automática aún no guardados
        self._hydrating = False
        self.loading = False
        self.working = False
        self.files = []
        self.log = []
        self.last_download = None
        self.settings = app_settings()
        data_dir = os.environ.get("GANTTIER_DATA_DIR") or QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        default_directory = self.default_directory = Path(data_dir) / "informes"
        self.download_directory = Path(self.settings.value("reports/download_directory", "") or default_directory)
        self.last_directory = None
        body = scroll_body(self)
        self.report_splitter = QSplitter(Qt.Vertical)
        self.report_splitter.setChildrenCollapsible(False)
        body.addWidget(self.report_splitter)
        left, right = QWidget(), QWidget()
        left_body, right_body = QVBoxLayout(left), QVBoxLayout(right)
        for layout in (left_body, right_body):
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(_SPACE["md"])
        self.report_splitter.addWidget(left)
        self.report_splitter.addWidget(right)
        generate = Section("Generar informe")
        self.period = choices(list(PERIODS.items()), "Tipo de informe")
        self.reference = date_editor(label="Fecha de referencia del informe")
        self.bounds_label = QLabel()
        self.period.setParent(self)
        self.period.hide()  # API pública conservada para pruebas y comandos existentes.
        self.period_group = QButtonGroup(self)
        self.period_buttons = []
        for index, (key, label) in enumerate(PERIODS.items()):
            control = _button(label, lambda checked=False, i=index: self.period.setCurrentIndex(i))
            control.setCheckable(True)
            self.period_group.addButton(control, index)
            self.period_buttons.append(control)
        self.period_buttons[self.period.currentIndex()].setChecked(True)
        self.period.currentIndexChanged.connect(lambda index: self.period_buttons[index].setChecked(True))
        generate.body.addWidget(_toolbar(*self.period_buttons, None))
        generate.body.addLayout(hbox(QLabel("Fecha de referencia"), self.reference, None))
        generate.body.addWidget(self.bounds_label)
        self.formats = self._format_checks({"pdf", "xlsx", "html"})
        generate.body.addLayout(hbox(*self.formats.values(), None))
        self.generate_button = _button("Generar informe", self.generate, primary=True, icon_name="file-text")
        self.root.addWidget(_toolbar(self.generate_button, _button("Abrir carpeta", self.open_directory, icon_name="folder-open"), None))
        self.cut_label = QLabel()
        self.cut_label.setWordWrap(True)
        generate.body.addWidget(self.cut_label)
        left_body.addWidget(generate)
        files = Section("Archivos generados")
        self.file_model = DictTableModel([
            Column("format", "Formato", get=lambda r: dict(FORMATS).get(r.get("format"), "")),
            Column("filename", "Archivo", get=lambda r: Path(r.get("filename", "")).name),
            Column("actions", "Acciones"),
        ])
        self.file_table = self._table(self.file_model, 160)
        files.body.addWidget(self.file_table)
        self.files_empty = _empty("file-text", "Aún no hay informes", "Elige un período y genera tu primer informe.")
        files.body.addWidget(self.files_empty)
        self.directory_label = QLabel()
        self.directory_label.setProperty("role", "caption")
        self.directory_label.setWordWrap(True)
        self.directory_label.setTextFormat(Qt.PlainText)
        self.saved_label = QLabel()
        self.saved_label.setTextFormat(Qt.PlainText)
        self.saved_label.setWordWrap(True)
        files.body.addWidget(self.saved_label)
        self._directory_caption()
        files.body.addWidget(self.directory_label)
        files.body.addWidget(_toolbar(_button("Abrir carpeta", self.open_directory, icon_name="folder-open"),
                                      _button("Cambiar carpeta…", self.choose_directory, kind="ghost", icon_name="pencil"), None))
        left_body.addWidget(files)
        left_body.addStretch()
        automatic = Section("Emisión automática")
        self.enabled = QCheckBox("Activar")
        self.auto_periods = {key: QCheckBox(label) for key, label in PERIODS.items()}
        self.auto_formats = self._format_checks({"pdf", "xlsx"})
        automatic.body.addLayout(hbox(self.enabled, *self.auto_periods.values(), None))
        automatic.body.addLayout(hbox(QLabel("Formatos"), *self.auto_formats.values(), None))
        self.weekday = choices(list(enumerate(("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"))), "Día de emisión semanal")
        self.keep_last = QSpinBox()
        self.keep_last.setRange(1, 10000)
        self.keep_last.setValue(12)
        self.keep_last.setAccessibleName("Informes conservados por tipo")
        automatic.body.addLayout(hbox(QLabel("Emisión semanal"), self.weekday, QLabel("Conservar por tipo"), self.keep_last, None))
        self.auto_save = _button("Guardar configuración", self.save_automatic, primary=True, icon_name="save")
        self.auto_run = _button("Emitir pendientes ahora", self.run_automatic, icon_name="play")
        automatic.body.addWidget(_toolbar(self.auto_save, self.auto_run, None))
        automatic.body.addWidget(_toolbar(_button("Actualizar historial", self.refresh, icon_name="refresh-cw", kind="ghost"), None))
        explanation = QLabel("Ganttier revisa los períodos cerrados al iniciar y cada hora mientras está abierto. "
                             "Las emisiones quedan en el historial interno. Emitir pendientes ahora guarda las nuevas "
                             "también en la carpeta local elegida.")
        explanation.setWordWrap(True)
        explanation.setProperty("role", "caption")
        automatic.body.addWidget(explanation)
        self.auto_status = QLabel()
        self.auto_status.setWordWrap(True)
        automatic.body.addWidget(self.auto_status)
        self.log_model = DictTableModel([
            Column("emitted", "Emitido", get=emission_date),
            Column("period_type", "Tipo", get=lambda r: PERIODS.get(r.get("period_type"), r.get("period_type", ""))),
            Column("period", "Período", get=lambda r: f"{fmt_date(r.get('period_start'))} → {fmt_date(r.get('period_end'))}"),
            Column("filename", "Archivo", get=lambda r: Path(r.get("filename", "")).name), Column("actions", "Acciones"), Column("error", "Error"),
        ])
        self.log_table = self._table(self.log_model, 210)
        automatic.body.addWidget(self.log_table)
        for check in (self.enabled, *self.auto_periods.values(), *self.auto_formats.values()):
            check.toggled.connect(self._mark_config_dirty)
        self.weekday.currentIndexChanged.connect(self._mark_config_dirty)
        self.keep_last.valueChanged.connect(self._mark_config_dirty)
        self.log_empty = _empty("history", "Aún no hay emisiones", "Activa la emisión automática para crear informes al cerrar cada período.")
        automatic.body.addWidget(self.log_empty)
        automatic.body.addLayout(hbox(_button("Abrir", lambda: self.open_selected(history=True), icon_name="folder-open", kind="ghost"),
                                  _button("Guardar como…", lambda: self.save_selected(history=True), icon_name="save", kind="ghost"), None))
        right_body.addWidget(automatic)
        outages = Card("Programa de trabajos con desconexión (base para solicitudes)")
        note = QLabel("Agrupa por instalación y fecha los trabajos que requieren desconexión, con equipos, HH y responsables. "
                      "Verifica el formato oficial y los plazos de solicitud ante el Coordinador Eléctrico Nacional "
                      "en la normativa y los procedimientos vigentes.")
        note.setWordWrap(True)
        note.setProperty("role", "caption")
        outages.add(note)
        self.outage_start = date_editor(label="Desconexiones desde")
        self.outage_end = date_editor(label="Desconexiones hasta")
        outages.add(layout=hbox(QLabel("Desde"), self.outage_start, QLabel("Hasta"), self.outage_end,
                                _button("Guardar Excel…", self.save_outages, icon_name="file-spreadsheet"), None))
        right_body.addWidget(outages)
        roster = Card("Plan semanal por trabajador (Excel)")
        roster_note = QLabel("Qué hace cada trabajador cada día de la semana, según las asignaciones explícitas. Las cuadrillas "
                             "y las tareas sin persona asignada van en bloques aparte; no se asignan personas por habilidad.")
        roster_note.setWordWrap(True)
        roster_note.setProperty("role", "caption")
        roster.add(roster_note)
        self.roster_week = date_editor(label="Semana del plan")
        roster.add(layout=hbox(QLabel("Semana que incluye"), self.roster_week,
                               _button("Guardar Excel…", self.save_roster, icon_name="users"), None))
        right_body.addWidget(roster)
        right_body.addStretch()
        self.period.currentIndexChanged.connect(self.update_bounds)
        self.reference.dateChanged.connect(self.update_bounds)
        self.file_table.doubleClicked.connect(lambda *_: self.open_selected())
        self.log_table.doubleClicked.connect(lambda *_: self.open_selected(history=True))
        self.update_bounds()
        self._empty_states()
        self.cut_label.setProperty("role", "caption")
        self.bounds_label.setProperty("role", "caption")
        self.auto_status.setProperty("role", "caption")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "report_splitter"):
            orientation = Qt.Horizontal if self.window().width() >= 1300 else Qt.Vertical
            if self.report_splitter.orientation() != orientation:
                self.report_splitter.setOrientation(orientation)
                self.report_splitter.setSizes([1, 1])

    def on_theme(self):
        for checks in (self.formats, self.auto_formats):
            for key, check in checks.items():
                check.setIcon(icon("file-spreadsheet" if key == "xlsx" else "file-text"))

    def _directory_caption(self):
        self.directory_label.setText(f"Carpeta local: …/{self.download_directory.name}")
        self.directory_label.setToolTip(str(self.download_directory))

    def open_directory(self):
        try:
            directory = self.last_directory or self.download_directory
            if not directory.is_dir():
                raise OSError(f"La carpeta de informes no existe: {directory}")
            open_path(str(directory))
        except OSError as exc:
            show_error(exc, self)

    def _empty_states(self):
        self.files_empty.setVisible(not self.files)
        self.file_table.setVisible(bool(self.files))
        self.log_empty.setVisible(self.log_model.rowCount() == 0)
        self.log_table.setVisible(self.log_model.rowCount() > 0)

    def _file_actions(self):
        _fit(self.file_table)
        for index, file in enumerate(self.files):
            preview = _button("Vista previa", lambda checked=False, f=file: self.preview_file(f), icon_name="eye", kind="ghost")
            preview.setEnabled(file.get("format") == "html")
            preview.setToolTip("Vista previa disponible para HTML")
            actions = _toolbar(_button("Abrir", lambda checked=False, f=file: self.open_file(f), icon_name="folder-open", kind="ghost"),
                               _button("Guardar como…", lambda checked=False, f=file: self.save_file(f), icon_name="save", kind="ghost"), preview)
            self.file_table.setIndexWidget(self.file_model.index(index, 2), actions)
            self.file_table.setRowHeight(index, max(38, actions.sizeHint().height()))
        self.file_table.setColumnWidth(2, max(390, actions.sizeHint().width()) if self.files else 390)

    @staticmethod
    def _format_checks(selected):
        result = {}
        for key, label in FORMATS:
            check = QCheckBox(label)
            check.setIcon(icon("file-spreadsheet" if key == "xlsx" else "file-text"))
            check.setChecked(key in selected)
            result[key] = check
        return result

    @staticmethod
    def _table(model, height):
        table = QTableView()
        table.setModel(model)
        _setup(table)
        _fit(table)
        table.setMinimumHeight(height)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().hide()
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _mark_config_dirty(self, *_):
        if not self._hydrating:
            self._config_dirty = True

    def has_unsaved(self) -> bool:
        return bool(self._config_dirty)

    def update_bounds(self, *_):
        start, end = period_bounds(self.period.currentData(), self.reference.date().toPython())
        self.bounds_label.setText(f"{fmt_date(start)} → {fmt_date(end)}")
        return start, end

    def refresh(self, *_):
        pid, project = self.state.pid, self.state.project or {}
        if pid != self._pid:
            self._pid = pid
            self.files, self.log = [], []
            self.file_model.set_rows([])
            self.log_model.set_rows([])
            self._empty_states()
            self.reference.setDate(qdate(project.get("data_date") or date.today()))
            self.outage_start.setDate(qdate(project.get("start_date") or date.today()))
            self.outage_end.setDate(qdate(project.get("end_date") or project.get("data_date") or project.get("start_date") or date.today()))
            self._hydrating = True
            try:
                self.enabled.setChecked(False)
            finally:
                self._hydrating = False
            self._config_dirty = False
        self.cut_label.setText(f"Incluye resumen ejecutivo, curva S, valor ganado, actividades, hitos, carga y desempeño. "
                               f"Fecha de corte del proyecto: {fmt_date(project.get('data_date')) or 'sin definir'}.")
        self._request += 1
        request = self._request
        self.loading = bool(pid)
        self.auto_save.setEnabled(False)
        self.auto_run.setEnabled(False)
        if not pid:
            return
        self.auto_status.setText("Cargando configuración e historial…")
        client = self.state.client

        def done(result):
            if pid != self.state.pid or request != self._request:
                return
            config, self.log = result
            self.loading = False
            if self._config_dirty:
                # A3-08: actualizar el historial no pisa la configuración editada y aún no guardada.
                self.auto_status.setText("Historial actualizado. Tus cambios de configuración siguen sin guardar.")
            else:
                self._hydrating = True
                try:
                    self.enabled.setChecked(config.get("enabled", False))
                    for key, check in self.auto_periods.items():
                        check.setChecked(config.get(key, True))
                    for key, check in self.auto_formats.items():
                        check.setChecked(key in config.get("formats", ["pdf", "xlsx"]))
                    self.weekday.setCurrentIndex(config.get("weekday", 0))
                    self.keep_last.setValue(config.get("keep_last", 12))
                finally:
                    self._hydrating = False
                self.auto_status.setText("Configuración cargada. Guarda los cambios antes de emitir pendientes.")
            self.auto_save.setEnabled(True)
            self.auto_run.setEnabled(True)
            rows = []
            for entry in list(reversed(self.log))[:50]:
                for file in entry.get("files") or [None]:
                    rows.append(dict(entry, file=file, filename=file["filename"] if file else "Sin archivo"))
            self.log_model.set_rows(rows)
            self._empty_states()
            _fit(self.log_table)
            for index, row in enumerate(rows):
                if row.get("file"):
                    file = row["file"]
                    actions = QWidget()
                    layout = hbox(_button("Abrir", lambda checked=False, f=file: self.open_file(f), icon_name="folder-open", kind="ghost"),
                                  _button("Guardar…", lambda checked=False, f=file: self.save_file(f), icon_name="save", kind="ghost"))
                    layout.setContentsMargins(2, 0, 2, 0)
                    actions.setLayout(layout)
                    self.log_table.setIndexWidget(self.log_model.index(index, 4), actions)
                    self.log_table.setRowHeight(index, 38)
            if len(self.log) > 50:
                self.auto_status.setText("Se muestran las 50 emisiones más recientes. Guarda la configuración antes de emitir pendientes.")
            self.log_table.setColumnWidth(0, 155)
            self.log_table.setColumnWidth(2, 210)
            self.log_table.setColumnWidth(3, 360)
            self.log_table.setColumnWidth(4, 210)

        def failed(exc):
            if pid == self.state.pid and request == self._request:
                self.loading = False
                self.auto_status.setText("No se pudo cargar la emisión automática. Usa Actualizar historial para reintentar.")
                show_error(exc, self)

        run_task(lambda: (client.autoreports(pid), client.autoreports_log(pid)), done, failed, busy="Cargando informes…")

    def _task(self, operation, done, message):
        if self.working or not self.state.pid:
            return
        pid = self.state.pid
        self.working = True
        self.setEnabled(False)

        def completed(result):
            self.working = False
            self.setEnabled(True)
            if pid == self.state.pid:
                done(result)

        def failed(exc):
            self.working = False
            self.setEnabled(True)
            show_error(exc, self)

        run_task(operation, completed, failed, busy=message)

    def generate(self):
        formats = [k for k, check in self.formats.items() if check.isChecked()]
        if not formats:
            QMessageBox.warning(self, "Formatos", "Selecciona al menos un formato de informe.")
            return
        start, _ = self.update_bounds()
        pid, client, kind = self.state.pid, self.state.client, self.period.currentData()
        directory = self.download_directory.resolve()
        create_default = self._can_create_default_directory(directory)

        def done(files):
            names = {f["filename"] for f in files}
            self.files = files + [f for f in self.files if f["filename"] not in names]
            self.file_model.set_rows(self.files)
            self._file_actions()
            self._empty_states()
            if files:
                self.file_table.selectRow(0)
            if files:
                self.last_download = files[0]["local_path"]
                self.last_directory = directory
                message = "Informes guardados:\n" + "\n".join(f["local_path"] for f in files)
                self.saved_label.setText(message)
                self.saved_label.setToolTip(message)
                notify(message)

        def work():
            self._validate_directory(directory, create=create_default)
            files = client.reports(pid, kind, iso(start), formats)
            saved = []
            for file in files:
                target = directory / Path(file["filename"]).name
                path = self._write_report(target, client.download(file["url"]))
                saved.append(dict(file, local_path=path))
            return saved

        self._task(work, done, "Generando y guardando informe…")

    def selected_file(self, history=False):
        table, model = (self.log_table, self.log_model) if history else (self.file_table, self.file_model)
        index = table.currentIndex()
        if not index.isValid():
            return None
        row = model.rows[index.row()]
        return row.get("file") if history else row

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Carpeta local de informes", str(self.download_directory))
        if directory:
            self.download_directory = Path(directory).resolve()
            self.last_directory = None
            self.settings.setValue("reports/download_directory", str(self.download_directory))
            self._directory_caption()

    def open_selected(self, history=False):
        file = self.selected_file(history)
        if file:
            self.open_file(file)

    def open_file(self, file):
        # Las rutas de la API nunca se interpretan como rutas locales.
        local = file.get("local_path")
        if local and Path(local).is_file():
            open_path(local)
            return
        target = self.download_directory / Path(file["filename"]).name
        self.download_file(file, target, opening=True)

    def save_selected(self, history=False):
        file = self.selected_file(history)
        if file:
            self.save_file(file)

    def save_file(self, file):
        extension = file["format"]
        path, _ = QFileDialog.getSaveFileName(self, "Guardar informe", str(self.download_directory / Path(file["filename"]).name),
                                             f"{dict(FORMATS).get(extension, extension)} (*.{extension})")
        if path:
            self.download_file(file, Path(path))

    def download_file(self, file, target, opening=False):
        client, url, path = self.state.client, file["url"], Path(target)
        create_default = self._can_create_default_directory(path.parent.resolve())

        def work():
            data = client.download(url)
            self._validate_directory(path.parent, create=create_default)
            return self._write_report(path, data)

        def done(saved):
            self.last_download = saved
            self.last_directory = Path(saved).parent
            self.saved_label.setText(f"Informe guardado: {saved}")
            notify(f"Informe guardado: {saved}")
            if opening:
                open_path(saved)

        self._task(work, done, "Descargando informe…")

    def _can_create_default_directory(self, directory):
        return directory == self.default_directory.resolve() and not self.settings.value("reports/download_directory", "")

    @staticmethod
    def _validate_directory(directory, *, create=False):
        try:
            if create:
                directory.mkdir(parents=True, exist_ok=True)
            if not directory.is_dir():
                raise OSError(f"La carpeta de informes no existe o no es una carpeta: {directory}")
            # Comprobación real: os.access no refleja siempre los permisos ACL de Windows.
            probe = directory / f".ganttier-{uuid4().hex}.tmp"
            with probe.open("xb"):
                pass
            probe.unlink()
        except PermissionError as exc:
            raise OSError(f"No hay permiso para guardar informes en: {directory}") from exc

    @staticmethod
    def _write_report(path, data):
        temporary = path.with_name(f".ganttier-{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
            temporary.replace(path)
        except PermissionError as exc:
            raise OSError(f"No hay permiso para guardar el informe: {path}") from exc
        except OSError as exc:
            raise OSError(f"No se pudo guardar el informe en {path}: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return str(path.resolve())

    def preview_selected(self):
        self.preview_file(self.selected_file())

    def preview_file(self, file):
        if not file or file.get("format") != "html":
            QMessageBox.information(self, "Vista previa", "Selecciona un informe HTML.")
            return
        client, url = self.state.client, file["url"]

        def done(data):
            dialog = ResponsiveDialog(self)
            dialog.setWindowTitle("Vista previa HTML")
            layout = QVBoxLayout(dialog)
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setOpenLinks(False)
            browser.setHtml(data.decode("utf-8"))
            layout.addWidget(browser)
            label = QLabel("Vista de lectura. Usa Abrir para ver el diseño completo en el navegador.")
            layout.addWidget(label)
            layout.addWidget(button("Cerrar", dialog.accept))
            dialog.resize(1000, 750)
            dialog.exec()

        self._task(lambda: client.download(url), done, "Cargando vista previa…")

    def save_automatic(self):
        if self.loading:
            return
        formats = [k for k, check in self.auto_formats.items() if check.isChecked()]
        if not formats:
            QMessageBox.warning(self, "Formatos", "Selecciona al menos un formato para la emisión automática.")
            return
        config = dict(enabled=self.enabled.isChecked(), formats=formats, weekday=self.weekday.currentData(),
                      keep_last=self.keep_last.value(), **{k: c.isChecked() for k, c in self.auto_periods.items()})
        pid, client = self.state.pid, self.state.client
        def saved(_):
            self._config_dirty = False
            notify("Configuración guardada")
            self.refresh()
        self._task(lambda: client.save_autoreports(pid, config), saved, "Guardando configuración…")

    def run_automatic(self):
        if self.loading:
            return
        pid, client = self.state.pid, self.state.client
        directory = self.download_directory.resolve()
        create_default = self._can_create_default_directory(directory)

        def work():
            self._validate_directory(directory, create=create_default)
            entries = client.run_autoreports(pid)
            return [self._write_report(directory / Path(file["filename"]).name, client.download(file["url"]))
                    for entry in entries for file in entry.get("files", [])]

        def done(paths):
            if paths:
                self.last_directory = directory
                self.last_download = paths[0]
                message = "Informes guardados:\n" + "\n".join(paths)
                self.saved_label.setText(message)
                notify(message)
            else:
                notify("No hay informes pendientes nuevos.")
            self.refresh()

        self._task(work, done, "Emitiendo y guardando informes pendientes…")

    def save_roster(self):
        """P5-01: plan semanal nominal por trabajador."""
        week = self.roster_week.date().toPython()
        monday = week - timedelta(days=week.weekday())
        path, _ = QFileDialog.getSaveFileName(self, "Guardar plan semanal", str(self.download_directory / f"plan_semanal_{monday.isoformat()}.xlsx"),
                                              "Excel (*.xlsx)")
        if not path:
            return
        pid, client = self.state.pid, self.state.client

        def work():
            data = client.request("GET", f"/projects/{pid}/reports/weekly-roster", params={"week_start": monday.isoformat()}, raw=True)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return str(target.resolve())

        def done(saved):
            self.last_download = saved
            notify("Plan semanal guardado")

        self._task(work, done, "Generando plan semanal…")

    def save_outages(self):
        start, end = iso(self.outage_start.date()), iso(self.outage_end.date())
        if start > end:
            QMessageBox.warning(self, "Período", "La fecha desde no puede ser posterior a la fecha hasta.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Guardar programa de desconexiones",
                                             str(self.download_directory / "programa_desconexiones.xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        pid, client = self.state.pid, self.state.client

        def work():
            data = client.outages_xlsx(pid, start, end)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return str(target.resolve())

        def done(saved):
            self.last_download = saved
            notify("Programa de desconexiones guardado")

        self._task(work, done, "Descargando programa de desconexiones…")
