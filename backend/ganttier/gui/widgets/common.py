"""Componentes comunes de la GUI: tareas en segundo plano, modelos de tabla, formularios, KPI y gráficos."""
from __future__ import annotations

from .responsive import ResponsiveDialog

import logging
import math
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable

from PySide6.QtCore import QAbstractTableModel, QCoreApplication, QDate, QEvent, QModelIndex, QObject, QRunnable, QSortFilterProxyModel, Qt, QThread, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QTableView, QToolButton, QVBoxLayout, QWidget,
)

from ..client import ApiError
from ..theme import ROW_HEIGHT, SPACE, theme_signals, tokens
from . import icons

_log = logging.getLogger(__name__)
_reporting_error = False

# ---------------------------------------------------------------------------- fechas y números

def iso(d: date | datetime | QDate | str | None) -> str | None:
    if d is None or d == "":
        return None
    if isinstance(d, QDate):
        return d.toString("yyyy-MM-dd") if d.isValid() else None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10]


def to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def qdate(v: Any) -> QDate:
    d = to_date(v)
    return QDate(d.year, d.month, d.day) if d else QDate()


def fmt_date(v: Any) -> str:
    d = to_date(v)
    return d.strftime("%d-%m-%Y") if d else ""


def fmt_num(v: Any, dec: int = 0) -> str:
    if v is None or v == "":
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x != x:
        return "—"
    s = f"{x:,.{dec}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")  # formato chileno 1.234,5


def fmt_pct(v: Any, dec: int = 1) -> str:
    return "—" if v is None else f"{fmt_num(v, dec)} %"


def status_text(code: str | None) -> str:
    """Etiqueta visible; conserva códigos desconocidos para no ocultar información."""
    return {
        "heuristico": "Heurístico", "optimo": "Óptimo", "factible": "Factible",
        "infactible": "Infactible", "optimal": "Óptimo", "feasible": "Factible",
        "infeasible": "Infactible", "unknown": "Desconocido", "desconocido": "Desconocido",
        "model_invalid": "Modelo inválido", "error": "Error", "ok": "Correcto",
        "pending": "Pendiente", "running": "En curso", "cancelled": "Cancelado",
        "timeout": "Tiempo agotado", "sin_solucion": "Sin solución",
    }.get(str(code).lower(), str(code) if code is not None else "—")


def method_text(code: str | None) -> str:
    """Traduce los identificadores de métodos usados por la API."""
    return {
        "cpsat": "CP-SAT", "cp_sat": "CP-SAT", "cp-sat": "CP-SAT",
        "heuristic": "Heurística", "heuristico": "Heurística",
        "maintenance_numpy": "Nivelación de mantenimiento", "precheck": "Validación previa",
        "cpm": "Ruta crítica (CPM)", "manual": "Manual", "none": "Sin método",
    }.get(str(code).lower(), str(code) if code is not None else "—")


# ---------------------------------------------------------------------------- tareas en segundo plano

class _Bridge(QObject):
    """Único receptor, creado y vivo en el hilo GUI durante toda la aplicación.

    Los trabajadores emiten ``finished`` (conexión encolada) y el slot ejecuta los callbacks en
    el hilo GUI. No se crean ni destruyen QObject por tarea: así se evitan fallas nativas por
    objetos Qt destruidos (deleteLater) mientras su envoltorio Python se recolecta.
    """

    finished = Signal(int, bool, object)

    def __init__(self):
        super().__init__()
        self.finished.connect(self._on_finished, Qt.QueuedConnection)
        self._retired = []
        self._release_timer = QTimer(self, interval=50)
        self._release_timer.timeout.connect(self._release_finished)

    @Slot()
    def _release_finished(self):
        # La señal puede llegar antes de que run() retorne al pool. Sólo liberar
        # las funciones (pueden capturar widgets) en GUI y con el pool inactivo.
        if not QThreadPool.globalInstance().waitForDone(0):
            return
        from shiboken6 import delete
        retired, self._retired = self._retired, []
        self._release_timer.stop()
        for task in retired:
            task.fn = None
            task.bridge = None
            delete(task)  # setAutoDelete(False): la propiedad sigue siendo nuestra

    @Slot(int, bool, object)
    def _on_finished(self, tid, ok, value):
        entry = _pending.pop(tid, None)
        try:
            if entry is None:
                return
            _task, on_done, on_error, win, busy = entry
            self._retired.append(_task)
            self._release_timer.start()
            if win is not None and getattr(win, "_closed", False):
                return  # aboutToQuit: no volver a abrir diálogos ni escribir widgets
            try:
                if busy and win is not None:
                    win.busy_end()
            except Exception as exc:  # noqa: BLE001
                report_error(exc)
            callback = on_done if ok else (on_error or report_error)
            try:
                if callback:
                    callback(value)
            except Exception as exc:  # noqa: BLE001 - un error en un callback no debe abortar Qt
                report_error(exc)
        finally:
            _active.discard(tid)


class _Task(QRunnable):
    def __init__(self, tid: int, fn: Callable[[], Any], bridge: _Bridge):
        super().__init__()
        self.tid, self.fn, self.bridge = tid, fn, bridge
        self.setAutoDelete(False)   # la referencia Python vive en _pending hasta la entrega

    def run(self) -> None:  # hilo de trabajo
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001 - se informa en la GUI
            exc.__gt_trace__ = traceback.format_exc()  # type: ignore[attr-defined]
            self.bridge.finished.emit(self.tid, False, exc)
        else:
            self.bridge.finished.emit(self.tid, True, result)


def report_error(exc: Exception, parent: QWidget | None = None) -> None:
    """Registra y muestra un fallo; nunca reentra si falla el propio aviso."""
    global _reporting_error
    _log.error("Error en la GUI: %s", exc, exc_info=(type(exc), exc, exc.__traceback__))
    if _reporting_error:
        return
    _reporting_error = True
    try:
        show_error(exc, parent)
    except Exception:
        _log.exception("No se pudo mostrar el error de la GUI")
    finally:
        _reporting_error = False



_active: set[int] = set()          # tareas cuya entrega (incluidos callbacks) aún no termina
_pending: dict[int, tuple] = {}
_bridge: _Bridge | None = None
_next_id = 0


def run_task(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
             on_error: Callable[[Exception], None] | None = None, busy: str | None = None) -> None:
    """Ejecuta ``fn`` en el pool de hilos y entrega el resultado en el hilo de la GUI.

    Debe invocarse en GUI. ``_active`` incluye la entrega y los callbacks, incluso
    si estos inician otra tarea. ``busy`` mantiene el indicador hasta terminar.
    """
    global _bridge, _next_id
    from .. import app as _app  # import tardío para evitar ciclo
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise RuntimeError("run_task debe invocarse desde el hilo de la GUI")
    if _bridge is None:
        _bridge = _Bridge()
    win = _app.main_window()
    if win is not None and getattr(win, "_closed", False):
        return
    if busy and win is not None:
        win.busy_start(busy)
    _next_id += 1
    tid = _next_id
    task = _Task(tid, fn, _bridge)
    _pending[tid] = (task, on_done, on_error, win, busy)
    _active.add(tid)
    try:
        QThreadPool.globalInstance().start(task)
    except Exception as exc:  # noqa: BLE001
        _bridge._on_finished(tid, False, exc)


def show_error(exc: Exception, parent: QWidget | None = None) -> None:
    from .. import app as _app
    if isinstance(exc, ApiError):
        text = exc.detail_text()
    else:
        text = str(exc) or exc.__class__.__name__
    win = _app.main_window()
    if win is not None and parent is None:
        win.notify(text.splitlines()[0], "error")
    box = QMessageBox(parent or win)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Ganttier")
    box.setText(text)
    trace = getattr(exc, "__gt_trace__", None)
    if trace and not isinstance(exc, ApiError):
        box.setDetailedText(trace)
    box.exec()


def notify(text: str, kind: str = "ok") -> None:
    toast(text, kind)


def history_warning(history: dict | None) -> str:
    """Texto de advertencia cuando un borrado alteraría líneas base o avances (D3); '' si no aplica."""
    if not history or not (history.get("baseline_rows") or history.get("progress")):
        return ""
    parts = []
    if history.get("baseline_rows"):
        parts.append(f"figura en {history.get('baselines', 1)} línea(s) base")
    if history.get("progress"):
        parts.append(f"tiene {history['progress']} registro(s) de avance "
                     f"({fmt_num(history.get('actual_hours', 0), 1)} HH reales)")
    return ("Atención: " + " y ".join(parts) + ". Eliminarla cambiará hacia atrás el presupuesto (BAC), el valor "
            "planificado y las HH reales de los informes ya emitidos. La eliminación quedará registrada en la bitácora.")


def confirm(parent: QWidget, title: str, text: str, danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    yes = box.addButton("Eliminar" if danger else "Aceptar", QMessageBox.AcceptRole)
    box.addButton("Cancelar", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is yes


# ---------------------------------------------------------------------------- modelo de tabla genérico

@dataclass
class Column:
    key: str
    title: str
    kind: str = "text"            # text | int | float | date | bool | choice | pct
    editable: bool = False
    width: int = 0
    dec: int = 0
    choices: list[tuple[Any, str]] = field(default_factory=list)   # (valor, etiqueta)
    get: Callable[[dict], Any] | None = None                        # valor calculado
    align_right: bool | None = None


class DictTableModel(QAbstractTableModel):
    """Tabla sobre una lista de dicts, con columnas declarativas, edición y marca de filas modificadas."""

    edited = Signal(int, str, object)  # fila, clave, valor nuevo

    def __init__(self, columns: list[Column], rows: list[dict] | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = rows or []
        self.dirty: set[int] = set()

    # datos
    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.dirty.clear()
        self.endResetModel()

    def row_dict(self, row: int) -> dict:
        return self.rows[row]

    def dirty_rows(self) -> list[dict]:
        return [self.rows[i] for i in sorted(self.dirty)]

    # Qt
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            column = self.columns[section]
            if role in (Qt.DisplayRole, Qt.ToolTipRole):
                return column.title
            if role == Qt.TextAlignmentRole:
                right = column.align_right if column.align_right is not None else column.kind in ("int", "float", "pct")
                return int((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
        return None

    def _raw(self, r: int, c: Column):
        row = self.rows[r]
        return c.get(row) if c.get else row.get(c.key)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        c = self.columns[index.column()]
        v = self._raw(index.row(), c)
        if role == Qt.DisplayRole:
            if c.kind == "bool":
                return None
            if c.kind == "date":
                return fmt_date(v)
            if c.kind in ("int", "float"):
                return "" if v is None else fmt_num(v, c.dec)
            if c.kind == "pct":
                return "" if v is None else fmt_pct(v, c.dec)
            if c.kind == "choice":
                return next((lbl for val, lbl in c.choices if val == v), "" if v is None else str(v))
            if isinstance(v, (list, tuple)):
                return ", ".join(str(x) for x in v)
            return "" if v is None else str(v)
        if role == Qt.EditRole:
            if c.kind == "date":
                return qdate(v)
            return v
        if role == Qt.CheckStateRole and c.kind == "bool":
            return Qt.Checked if v else Qt.Unchecked
        if role == Qt.TextAlignmentRole:
            right = c.align_right if c.align_right is not None else c.kind in ("int", "float", "pct")
            return int((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
        if role == Qt.BackgroundRole and index.row() in self.dirty:
            return QColor(tokens().accent_soft)
        if role == Qt.UserRole:
            return v
        return None

    def flags(self, index):
        f = super().flags(index)
        c = self.columns[index.column()]
        if c.editable and not c.get:
            f |= Qt.ItemIsUserCheckable if c.kind == "bool" else Qt.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
        c = self.columns[index.column()]
        if c.kind == "bool" and role == Qt.CheckStateRole:
            value = value in (Qt.Checked, 2, True)
        elif role != Qt.EditRole:
            return False
        if c.kind == "date":
            value = iso(value) if isinstance(value, QDate) else (value or None)
        elif c.kind == "int":
            value = None if value in ("", None) else int(float(str(value).replace(",", ".")))
        elif c.kind in ("float", "pct"):
            value = None if value in ("", None) else float(str(value).replace(",", "."))
        self.rows[index.row()][c.key] = value
        self.dirty.add(index.row())
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(self.columns) - 1))
        self.edited.emit(index.row(), c.key, value)
        return True


class SortProxy(QSortFilterProxyModel):
    """Proxy con orden numérico/fecha correcto y filtro de texto en todas las columnas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.setSortRole(Qt.UserRole)

    def lessThan(self, a, b):
        va, vb = a.data(Qt.UserRole), b.data(Qt.UserRole)
        va = None if isinstance(va, float) and not math.isfinite(va) else va
        vb = None if isinstance(vb, float) and not math.isfinite(vb) else vb
        if va is None and vb is None:
            return False
        # Qt invierte los operandos al ordenar descendente: los nulos siguen al final.
        if va is None or vb is None:
            return (va is None) if self.sortOrder() == Qt.DescendingOrder else (vb is None)
        try:
            return va < vb
        except TypeError:
            return str(va) < str(vb)


# ---------------------------------------------------------------------------- formulario declarativo

@dataclass
class Field:
    key: str
    label: str
    kind: str = "text"      # text | multiline | int | float | date | bool | choice | month | tags
    choices: list[tuple[Any, str]] = field(default_factory=list)
    minimum: float = 0
    maximum: float = 1e12
    dec: int = 2
    span: bool = False
    required: bool = field(default=False, kw_only=True)
    help: str = ""


class FormDialog(ResponsiveDialog):
    """Diálogo de formulario construido a partir de ``Field``; ``values()`` devuelve el dict editado."""

    def __init__(self, title: str, fields: list[Field], values: dict | None = None, parent=None, columns: int | None = None,
                 validate: Callable[[dict], str | dict[str, str] | None] | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._fields = fields
        self._validate = validate
        self._values = dict(values or {})
        self._editors: dict[str, QWidget] = {}
        self._errors: dict[str, QLabel] = {}
        self._help_labels: dict[str, QLabel] = {}
        columns = max(1, columns) if columns is not None else (2 if len(fields) > 8 else 1)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        grid = QGridLayout(body)
        self._field_grid = grid
        grid.setHorizontalSpacing(SPACE["xl"])
        grid.setVerticalSpacing(SPACE["md"])
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        r = c = 0
        for f in fields:
            ed = self._editor(f, self._values.get(f.key))
            self._editors[f.key] = ed
            box = QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(SPACE["xs"])
            lbl = QLabel(f.label + (" *" if f.required else ""))
            lbl.setBuddy(ed)
            ed.setAccessibleName(f.label)
            lbl.setProperty("muted", True)
            if f.help:
                lbl.setToolTip(f.help)
                ed.setToolTip(f.help)
            if f.kind != "bool":
                box.addWidget(lbl)
            box.addWidget(ed)
            if f.required:
                ed.setAccessibleDescription("Obligatorio. " + f.help)
                if isinstance(ed, QCheckBox):
                    ed.setText(f.label + " *")
            if f.help:
                help_label = QLabel(f.help)
                help_label.setTextFormat(Qt.TextFormat.PlainText)
                help_label.setProperty("role", "caption")
                help_label.setWordWrap(True)
                box.addWidget(help_label)
                self._help_labels[f.key] = help_label
            error_label = QLabel()
            error_label.setTextFormat(Qt.TextFormat.PlainText)
            error_label.setProperty("role", "error")
            error_label.setWordWrap(True)
            error_label.hide()
            box.addWidget(error_label)
            self._errors[f.key] = error_label
            box.addStretch()
            w = QWidget()
            w.setLayout(box)
            if f.span:
                if c:
                    r, c = r + 1, 0
                grid.addWidget(w, r, 0, 1, columns)
                r += 1
                continue
            grid.addWidget(w, r, c)
            c += 1
            if c >= columns:
                r, c = r + 1, 0
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self._error_summary = QLabel()
        self._error_summary.setProperty("role", "error")
        self._error_summary.setWordWrap(True)
        self._error_summary.setTextFormat(Qt.TextFormat.PlainText)
        self._error_summary.hide()
        outer.addWidget(self._error_summary)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Guardar")
        bb.button(QDialogButtonBox.Ok).setProperty("kind", "primary")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)
        self.resize(760 if columns > 1 else 460, min(720, 120 + 70 * ((len(fields) + columns - 1) // columns)))

    def _editor(self, f: Field, v: Any) -> QWidget:
        if f.kind == "multiline":
            e = QPlainTextEdit(str(v or ""))
            e.setFixedHeight(64)
            return e
        if f.kind == "int":
            e = QSpinBox()
            e.setRange(int(f.minimum), int(min(f.maximum, 2_000_000_000)))
            e.setValue(int(v or 0))
            return e
        if f.kind == "float":
            e = QDoubleSpinBox()
            e.setDecimals(f.dec)
            e.setRange(f.minimum, f.maximum)
            e.setValue(float(v or 0))
            # A3-03: el editor redondea a f.dec; si el usuario no toca el campo se devuelve el valor original exacto.
            e.setProperty("_loaded", e.value())
            e.setProperty("_original", v)
            return e
        if f.kind == "date":
            e = QDateEdit()
            e.setCalendarPopup(True)
            e.setDisplayFormat("dd-MM-yyyy")
            e.setSpecialValueText("—")
            e.setMinimumDate(QDate(1900, 1, 1))
            e.setDate(qdate(v) if v else e.minimumDate())
            return e
        if f.kind == "bool":
            e = QCheckBox(f.label)
            e.setChecked(bool(v))
            return e
        if f.kind == "choice":
            e = QComboBox()
            for val, lbl in f.choices:
                e.addItem(lbl, val)
            idx = next((i for i, (val, _) in enumerate(f.choices) if val == v), None)
            if idx is None and v is not None:
                # A3-04: nunca reemplazar en silencio un valor guardado que no está en la lista cargada.
                e.addItem(f"Valor actual ({v}) — no disponible en la lista", v)
                idx = e.count() - 1
            e.setCurrentIndex(idx or 0)
            return e
        if f.kind == "multi_choice":
            from PySide6.QtWidgets import QListWidget, QListWidgetItem
            e = QListWidget()
            e.setMaximumHeight(140)
            selected = set(v or [])
            for key, label in f.choices:
                item = QListWidgetItem(label, e)
                item.setData(Qt.UserRole, key)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if key in selected else Qt.Unchecked)
            return e
        if f.kind == "tags":
            e = QLineEdit(", ".join(v or []))
            e.setPlaceholderText("separadas por coma")
            return e
        e = QLineEdit("" if v is None else str(v))
        if f.kind == "month":
            e.setPlaceholderText("AAAA-MM")
            e.setInputMask("")
        return e

    def values(self) -> dict:
        out = dict(self._values)
        for f in self._fields:
            e = self._editors[f.key]
            if f.kind == "multiline":
                out[f.key] = e.toPlainText()
            elif f.kind == "int":
                out[f.key] = e.value()
            elif f.kind == "float":
                original = e.property("_original")
                untouched = original is not None and e.value() == e.property("_loaded")
                out[f.key] = float(original) if untouched else e.value()
            elif f.kind == "date":
                out[f.key] = None if e.date() == e.minimumDate() else iso(e.date())
            elif f.kind == "bool":
                out[f.key] = e.isChecked()
            elif f.kind == "choice":
                out[f.key] = e.currentData()
            elif f.kind == "multi_choice":
                selected = [e.item(i).data(Qt.UserRole) for i in range(e.count()) if e.item(i).checkState() == Qt.Checked]
                # El orden de equipos define la rotación: editar otro campo no debe cambiarlo.
                previous = [key for key in self._values.get(f.key, []) if key in selected]
                out[f.key] = previous + [key for key in selected if key not in previous]
            elif f.kind == "tags":
                out[f.key] = [x.strip() for x in e.text().split(",") if x.strip()]
            else:
                t = e.text().strip()
                out[f.key] = t if t or f.kind == "text" else None
                if f.kind == "month" and not t:
                    out[f.key] = None
        return out

    def _accept(self):
        for key, label in self._errors.items():
            label.clear()
            label.hide()
            self._mark_invalid(key, False)
        self._error_summary.hide()
        values = self.values()
        errors = {f.key: "Este campo es obligatorio." for f in self._fields
                  if f.required and (values.get(f.key) is None or values.get(f.key) == "" or values.get(f.key) == [])}
        if self._validate:
            msg = self._validate(values)
            if isinstance(msg, dict):
                errors.update({key: str(value) for key, value in msg.items() if value})
            elif msg:
                QMessageBox.warning(self, "Revisa los datos", msg)
                return
        if errors:
            first = None
            for key, message in errors.items():
                if key in self._errors:
                    self._errors[key].setText(message)
                    self._errors[key].show()
                    self._mark_invalid(key, True)
                    first = first or self._editors[key]
            self._error_summary.setText("Revisa los datos: " + "; ".join(errors.values()))
            self._error_summary.show()
            if first is not None:
                first.setFocus()
            return
        self.accept()

    def _mark_invalid(self, key: str, invalid: bool):
        editor = self._editors[key]
        editor.setProperty("invalid", invalid)
        editor.style().unpolish(editor)
        editor.style().polish(editor)
        editor.update()


# ---------------------------------------------------------------------------- tarjetas y paneles

class Card(QFrame):
    def __init__(self, title: str | None = None, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 10, 14, 12)
        self.header = QHBoxLayout()
        if title is not None:
            lbl = QLabel(title)
            lbl.setProperty("cardTitle", True)
            self.header.addWidget(lbl)
        self.header.addStretch(1)
        self.body.addLayout(self.header)

    def add(self, w: QWidget | None = None, layout=None, stretch: int = 0):
        if w is not None:
            self.body.addWidget(w, stretch)
        if layout is not None:
            self.body.addLayout(layout, stretch)
        return self


class KpiCard(QFrame):
    """Tarjeta de indicador con semáforo opcional (``good``/``warning``/``critical``)."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        self._label = QLabel(label.upper())
        self._label.setProperty("kpiLabel", True)
        self._value = QLabel("—")
        self._value.setProperty("kpiValue", True)
        self._sub = QLabel("")
        self._sub.setProperty("muted", True)
        self._sub.setWordWrap(True)
        for w in (self._label, self._value, self._sub):
            lay.addWidget(w)

    def set(self, value: str, sub: str = "", status: str | None = None):
        self._value.setText(value)
        t = tokens()
        if status:
            color = {"good": t.good, "warning": t.warning, "critical": t.critical}.get(status, t.text3)
            label = {"good": "En plan", "warning": "Atención", "critical": "Crítico"}.get(status, "")
            sub = f'<span style="color:{color}">●</span> {label}' + (f" · {sub}" if sub else "")
        self._sub.setText(sub)


def spi_status(v: float | None) -> str | None:
    if v is None:
        return None
    return "good" if v >= 0.95 else "warning" if v >= 0.85 else "critical"


# ---------------------------------------------------------------------------- gráficos (matplotlib)

class MplCanvas(QWidget):
    """Lienzo matplotlib integrado y con colores del tema; usa ``self.fig`` / ``self.ax``."""

    def __init__(self, parent=None, height: float = 3.2):
        super().__init__(parent)
        import matplotlib
        matplotlib.use("QtAgg")
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(6, height), dpi=100, layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.ax = self.fig.add_subplot(111)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(self.toolbar.iconSize() * 0.8)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.canvas, 1)
        self.apply_plot_theme()

    def apply_plot_theme(self):
        t = tokens()
        self.fig.set_facecolor(t.surface)
        for ax in self.fig.axes:
            ax.set_facecolor(t.surface)
            ax.tick_params(colors=t.text2, labelsize=8)
            for s in ax.spines.values():
                s.set_color(t.border)
            ax.yaxis.label.set_color(t.text2)
            ax.xaxis.label.set_color(t.text2)
            ax.title.set_color(t.text)
            ax.grid(True, color=t.grid, linewidth=0.6)
            ax.set_axisbelow(True)

    def clear(self):
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.apply_plot_theme()

    def draw(self):
        self.apply_plot_theme()
        leg = self.ax.get_legend()
        if leg:
            t = tokens()
            leg.get_frame().set_facecolor(t.surface)
            leg.get_frame().set_edgecolor(t.border)
            for txt in leg.get_texts():
                txt.set_color(t.text2)
        self.canvas.draw_idle()


def hbox(*widgets, stretch_last: bool = False, spacing: int = 8) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if stretch_last:
        lay.addStretch(1)
    return lay


def _invoke_action(slot: Callable, parent: QWidget):
    try:
        slot()
    except Exception as exc:
        report_error(exc, parent.window())


class _ActionButton(QPushButton):
    """Atajo diferido: el helper público crea botones sin padre."""

    def __init__(self, text, slot, icon_name, kind, shortcut):
        super().__init__(text)
        self._callback = slot
        self._icon_name = icon_name
        self._shortcut_text = shortcut
        self._key_shortcut = None
        self.setProperty("kind", kind)
        self.setIconSize(icons.ICON_SIZE)
        self._refresh_icon()
        theme_signals.theme_changed.connect(self._refresh_icon)
        self.clicked.connect(self._invoke)

    @Slot()
    def _invoke(self):
        if self._callback:
            _invoke_action(self._callback, self)

    @Slot(str)
    def _refresh_icon(self, _name=None):
        if self._icon_name:
            t = tokens()
            color = (t.surface if t.name == "light" else t.bg) if self.property("kind") == "primary" else (
                t.critical if self.property("kind") == "danger" else t.text2)
            self.setIcon(icons.icon(self._icon_name, color=color))

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.ParentChange and getattr(self, "_shortcut_text", None):
            if self.parentWidget() is not None and self._key_shortcut is None:
                self._key_shortcut = QShortcut(QKeySequence(self._shortcut_text), self)
                self._key_shortcut.activated.connect(self.click)
            elif self.parentWidget() is None and self._key_shortcut is not None:
                self._key_shortcut.setEnabled(False)
                self._key_shortcut.deleteLater()
                self._key_shortcut = None
        return result


def button(text: str, slot: Callable | None = None, primary: bool = False, danger: bool = False, tip: str = "",
           *, icon=None, kind=None, shortcut=None):
    resolved_kind = kind or ("danger" if danger else "primary" if primary else "secondary")
    b = _ActionButton(text, slot, icon, resolved_kind, shortcut)
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    tooltip = tip or text
    if shortcut:
        key_text = QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)
        tooltip += f" ({key_text})"
    if tip or shortcut:
        b.setToolTip(tooltip)
    b.setAccessibleName(text or tip)
    return b


class SearchBox(QLineEdit):
    searched = Signal(str)

    def __init__(self, placeholder="Buscar…", delay_ms=250, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder)
        self.setClearButtonEnabled(True)
        self._search_action = self.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(max(0, delay_ms))
        self._debounce.timeout.connect(self._emit_search)
        self.textChanged.connect(self._restart_search)
        theme_signals.theme_changed.connect(self._refresh_icon)

    @Slot(str)
    def _restart_search(self, _text):
        self._debounce.start()

    @Slot()
    def _emit_search(self):
        self.searched.emit(self.text())

    @Slot(str)
    def _refresh_icon(self, _name):
        self._search_action.setIcon(icons.icon("search"))


class Toolbar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("toolbar", True)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(SPACE["sm"])
        self._row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._menu_icons = []
        theme_signals.theme_changed.connect(self._refresh_icons)

    def add(self, widget):
        if isinstance(widget, (QPushButton, QToolButton)):
            # Los botones conservan su ancho natural: la barra no los estira a todo el ancho.
            widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._row.addWidget(widget)
        return widget

    # --- modo compacto: si la fila no cabe, los botones secundarios con ícono muestran sólo el ícono
    # (el texto pasa al tooltip) y vuelven a mostrarlo cuando hay espacio. Evita barras que desbordan.
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_compact()

    def _compactable(self):
        out = []
        for i in range(self._row.count()):
            w = self._row.itemAt(i).widget()
            if isinstance(w, QPushButton) and not w.icon().isNull() and w.property("kind") != "primary" \
                    and not w.property("primary"):
                out.append(w)
        return out

    def _fit_compact(self):
        buttons = self._compactable()
        if not buttons:
            return
        for b in buttons:                       # restaurar para medir el ancho completo
            full = b.property("_full_text")
            if full is not None and b.text() == "":
                b.setText(full)
        needed = self._row.sizeHint().width()
        if needed <= self.width():
            return
        for b in reversed(buttons):             # compacta desde la derecha (acciones menos usadas)
            if not b.text():
                continue
            b.setProperty("_full_text", b.text())
            if not b.toolTip():
                b.setToolTip(b.text())
            b.setText("")
            if self._row.sizeHint().width() <= self.width():
                break

    def add_action(self, text, slot, *, icon=None, primary=False, danger=False, kind=None, tip="", shortcut=None) -> QPushButton:
        return self.add(button(text, slot, primary, danger, tip, icon=icon, kind=kind, shortcut=shortcut))

    def add_separator(self):
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFixedHeight(20)
        return self.add(separator)

    def add_stretch(self):
        self._row.addStretch(1)

    def add_search(self, placeholder="Buscar…", delay_ms=250) -> SearchBox:
        box = SearchBox(placeholder, delay_ms, self)
        box.setMinimumWidth(200)
        return self.add(box)

    def add_menu(self, text, actions: list[tuple[str, Callable, str | None]], icon="ellipsis") -> QToolButton:
        tool = QToolButton(self)
        tool.setText(text)
        tool.setToolTip(text)
        tool.setAccessibleName(text)
        tool.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tool.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tool.setIconSize(icons.ICON_SIZE)
        menu = QMenu(tool)
        for label, callback, icon_name in actions:
            act = menu.addAction(label)
            act.triggered.connect(lambda checked=False, fn=callback: _invoke_action(fn, self))
            if icon_name:
                self._menu_icons.append((act, icon_name))
        tool.setMenu(menu)
        if icon:
            self._menu_icons.append((tool, icon))
        self._refresh_icons()
        return self.add(tool)

    @Slot(str)
    def _refresh_icons(self, _name=None):
        for item, name in self._menu_icons:
            item.setIcon(icons.icon(name))


class EmptyState(QWidget):
    def __init__(self, icon, title, text="", actions: list[tuple[str, Callable, bool]] = (), parent=None):
        super().__init__(parent)
        self.setProperty("emptystate", True)
        self._icon_name = icon
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        lay.setSpacing(SPACE["sm"])
        lay.addStretch()
        self._illustration = QLabel()
        self._title_label = QLabel()
        self._title_label.setProperty("role", "section")
        self._text_label = QLabel()
        self._text_label.setProperty("role", "muted")
        for label in (self._illustration, self._title_label, self._text_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            lay.addWidget(label)
        action_row = QHBoxLayout()
        action_row.addStretch()
        for label, callback, primary in actions:
            action_row.addWidget(button(label, callback, primary=primary))
        action_row.addStretch()
        lay.addLayout(action_row)
        lay.addStretch()
        self.set_text(title, text)
        self._refresh_icon()
        theme_signals.theme_changed.connect(self._refresh_icon)

    def set_text(self, title, text):
        self._title_label.setText(title)
        self._text_label.setText(text)
        self._text_label.setVisible(bool(text))
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)

    @Slot(str)
    def _refresh_icon(self, _name=None):
        self._illustration.setPixmap(icons.pixmap(self._icon_name, tokens().text3, size=40))


_STATUS = {
    "good": ("circle-check", "Correcto", "good"),
    "warning": ("triangle-alert", "Atención", "warning"),
    "critical": ("circle-x", "Error", "critical"),
    "info": ("info", "Información", "accent"),
}


class StatusBanner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self._kind = "info"
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        self._status_icon = QLabel()
        row.addWidget(self._status_icon, 0, Qt.AlignmentFlag.AlignTop)
        labels = QVBoxLayout()
        labels.setSpacing(SPACE["xs"])
        self._title_label = QLabel()
        self._title_label.setProperty("role", "section")
        self._text_label = QLabel()
        self._text_label.setProperty("role", "subtitle")
        for label in (self._title_label, self._text_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            labels.addWidget(label)
        row.addLayout(labels, 1)
        self.set("info", "")
        theme_signals.theme_changed.connect(self._refresh_icon)

    def set(self, kind, title, text=""):
        self._kind = kind if kind in _STATUS else "info"
        self._title_label.setText(title)
        self._text_label.setText(text)
        self._text_label.setVisible(bool(text))
        self.setAccessibleName(f"{_STATUS[self._kind][1]}: {title}")
        self.setAccessibleDescription(text)
        self._refresh_icon()

    @Slot(str)
    def _refresh_icon(self, _name=None):
        icon_name, label, token = _STATUS[self._kind]
        self._status_icon.setPixmap(icons.pixmap(icon_name, getattr(tokens(), token), size=22))
        self._status_icon.setToolTip(label)
        self._status_icon.setAccessibleName(label)


class Section(QFrame):
    def __init__(self, title, subtitle=None, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["lg"])
        outer.setSpacing(SPACE["md"])
        header = QHBoxLayout()
        labels = QVBoxLayout()
        labels.setSpacing(SPACE["xs"])
        label = QLabel(title)
        label.setProperty("role", "section")
        labels.addWidget(label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setProperty("role", "caption")
            sub.setWordWrap(True)
            labels.addWidget(sub)
        header.addLayout(labels, 1)
        self._actions = Toolbar(self)
        header.addWidget(self._actions)
        outer.addLayout(header)
        self.body = QVBoxLayout()
        self.body.setSpacing(SPACE["sm"])
        outer.addLayout(self.body)

    @property
    def actions(self) -> Toolbar:
        """API del contrato; el almacenamiento evita asignar sobre QWidget.actions()."""
        return self._actions


class _Toast(QFrame):
    def __init__(self, stack, text, kind, action_text, action, timeout_ms):
        super().__init__(stack.parent())
        self.setObjectName("Toast")
        self.setProperty("card", True)
        self._stack = stack
        self._kind = {"ok": "good", "error": "critical", "warn": "warning"}.get(kind, kind)
        if self._kind not in _STATUS:
            self._kind = "info"
        self._dismissed = False
        self._callback = action
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        row.setSpacing(SPACE["sm"])
        self._notice_icon = QLabel()
        row.addWidget(self._notice_icon)
        status = _STATUS[self._kind][1]
        label = QLabel(f"{status}: {text}")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(label, 1)
        self.setAccessibleName(f"{status}: {text}")
        if action_text and action:
            row.addWidget(button(action_text, self._activate, kind="ghost"))
        row.addWidget(button("", self.dismiss, icon="x", kind="ghost", tip="Cerrar aviso"))
        self._refresh_icon()
        theme_signals.theme_changed.connect(self._refresh_icon)
        self._expiry = QTimer(self)
        self._expiry.setSingleShot(True)
        self._expiry.timeout.connect(self.dismiss)
        self._expiry.start(max(0, int(timeout_ms)))

    @Slot(str)
    def _refresh_icon(self, _name=None):
        name, label, token = _STATUS[self._kind]
        self._notice_icon.setPixmap(icons.pixmap(name, getattr(tokens(), token)))
        self._notice_icon.setAccessibleName(label)
        self._notice_icon.setToolTip(label)

    @Slot()
    def _activate(self):
        if self._callback:
            _invoke_action(self._callback, self)
        self.dismiss()

    @Slot()
    def dismiss(self):
        if self._dismissed:
            return
        self._dismissed = True
        self._expiry.stop()
        self.hide()
        self._stack.remove(self)
        self.deleteLater()


class _ToastStack(QObject):
    def __init__(self, window):
        super().__init__(window)
        self._notices = []
        window.installEventFilter(self)

    def add(self, notice):
        self._notices.append(notice)
        notice.show()
        self._arrange()

    def remove(self, notice):
        if notice in self._notices:
            self._notices.remove(notice)
        self._arrange()

    def _arrange(self):
        window = self.parent()
        width = max(1, min(420, window.width() - 2 * SPACE["lg"]))
        bottom = window.height() - SPACE["lg"]
        # Evita cubrir la barra de estado, sin crear una si la ventana no tiene.
        from PySide6.QtWidgets import QStatusBar
        status_bar = window.findChild(QStatusBar)
        if status_bar is not None and status_bar.isVisible():
            bottom -= status_bar.height()
        for notice in reversed(self._notices):
            notice.setFixedWidth(width)
            notice.ensurePolished()
            height = notice.layout().totalHeightForWidth(width)
            notice.setFixedHeight(max(52, height if height >= 0 else notice.sizeHint().height()))
            bottom -= notice.height()
            notice.move(max(0, window.width() - width - SPACE["lg"]), bottom)
            notice.raise_()
            bottom -= SPACE["sm"]

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._arrange()
        return super().eventFilter(watched, event)


def toast(text, kind="ok", *, action_text=None, action=None, timeout_ms=4000):
    from .. import app as _app
    window = _app.main_window()
    if window is None:
        _log.info("%s: %s", kind, text)
        return None
    stack = getattr(window, "_ux_toast_stack", None)
    if stack is None:
        stack = _ToastStack(window)
        window._ux_toast_stack = stack
    notice = _Toast(stack, text, kind, action_text, action, timeout_ms)
    stack.add(notice)
    return notice


def setup_table(view: QTableView, *, sortable=True, stretch_last=True, row_height: int | None = None):
    """``row_height`` permite conservar la altura de una vista sincronizada (p. ej., tabla y lienzo del Gantt)."""
    height = row_height or ROW_HEIGHT
    view.setShowGrid(False)
    view.verticalHeader().hide()
    view.verticalHeader().setMinimumSectionSize(min(height, view.verticalHeader().minimumSectionSize()))
    view.verticalHeader().setDefaultSectionSize(height)
    view.verticalHeader().setMinimumSectionSize(height)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setAlternatingRowColors(True)
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.setMouseTracking(True)
    header = view.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    font = QFont(header.font())
    font.setWeight(QFont.Weight.DemiBold)
    header.setFont(font)
    header.setStretchLastSection(stretch_last)
    view.setSortingEnabled(sortable)


def fit_columns(view: QTableView, *, min_w=56, max_w=360, sample=200):
    model = view.model()
    if model is None:
        return
    view.ensurePolished()
    header = view.horizontalHeader()
    header.ensurePolished()
    header_font = QFont(header.font())
    header_font.setWeight(QFont.Weight.DemiBold)
    header_metrics = QFontMetrics(header_font)
    cell_metrics = QFontMetrics(view.font())
    count = min(model.rowCount(), max(0, min(200, int(sample))))
    # Repartir la muestra también observa códigos largos al final de la tabla.
    rows = [round(i * (model.rowCount() - 1) / max(1, count - 1)) for i in range(count)]
    for column in range(model.columnCount()):
        if view.isColumnHidden(column):
            continue
        title = model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        title_width = max((header_metrics.horizontalAdvance(line) for line in str(title or "").splitlines()), default=0)
        content_width = 0
        for row in rows:
            index = model.index(row, column)
            value = model.data(index, Qt.ItemDataRole.DisplayRole)
            font = model.data(index, Qt.ItemDataRole.FontRole)   # filas en negrita (p. ej. WBS) son más anchas
            metrics = QFontMetrics(font) if isinstance(font, QFont) else cell_metrics
            content_width = max(content_width, max((metrics.horizontalAdvance(line)
                                for line in str(value if value is not None else "").splitlines()), default=0))
        # max_w limita contenido; la cabecera completa siempre tiene prioridad.
        width = max(min_w, title_width + 20 + (18 if header.isSortIndicatorShown() else 0), min(max_w, content_width + 20))
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        view.setColumnWidth(column, width)


def save_bytes_dialog(parent: QWidget, data: bytes, suggested: str, filt: str = "Excel (*.xlsx)") -> str | None:
    """Pide ruta y guarda ``data``; devuelve la ruta o None si se canceló."""
    from PySide6.QtWidgets import QFileDialog
    path, _ = QFileDialog.getSaveFileName(parent, "Guardar archivo", suggested, filt)
    if not path:
        return None
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def open_path(path: str) -> None:
    """Abre un archivo con la aplicación predeterminada del sistema."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def iter_chunks(items: list, size: int) -> Iterable[list]:
    for k in range(0, len(items), size):
        yield items[k:k + size]
