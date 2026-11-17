"""Virtual Qt table over the same arrays used by the time canvas."""
from datetime import date
from collections import OrderedDict
import math

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, QDate, QPoint
from PySide6.QtGui import QFont, QColor, QPainter, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QDateEdit, QStyleOptionViewItem

from ..theme import tokens
from ..widgets.common import fmt_date, fmt_num
from ..widgets.icons import icon
from .model import day_iso

COLUMNS = [("codes", "Código"), ("names", "Nombre"), ("dur", "Dur."), ("start", "Inicio"),
           ("finish", "Término"), ("pct", "% Av."), ("hh", "HH"), ("macro", "Mes macro")]
EDITABLE = {"names", "dur", "start", "pct", "hh", "macro"}
DISPLAY, EDIT, TOOLTIP = Qt.DisplayRole, Qt.EditRole, Qt.ToolTipRole
ALIGNMENT, BACKGROUND, FOREGROUND = Qt.TextAlignmentRole, Qt.BackgroundRole, Qt.ForegroundRole
FONT, DECORATION, DESCRIPTION = Qt.FontRole, Qt.DecorationRole, Qt.AccessibleDescriptionRole
DATA_ROLES = frozenset((DISPLAY, EDIT, TOOLTIP, ALIGNMENT, BACKGROUND, FOREGROUND, FONT, DECORATION, DESCRIPTION))
ALIGN_LEFT, ALIGN_RIGHT = int(Qt.AlignVCenter | Qt.AlignLeft), int(Qt.AlignVCenter | Qt.AlignRight)


class ActivityTableModel(QAbstractTableModel):
    edited = Signal(int, str, object)
    invalid = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.arrays = self.rows = None
        # El modelo plano tiene las mismas banderas base en cada índice válido.
        self._base_flags = super().flags(self.createIndex(0, 0))
        self._editable_flags = self._base_flags | Qt.ItemIsEditable
        self.columns = COLUMNS + [("tf", "Holgura")]

    def set_data(self, arrays, rows, mode="proyecto"):
        self.beginResetModel()
        self.arrays, self.rows = arrays, rows
        self.columns = COLUMNS + ([("skill", "Habilidad"), ("location", "Instalación")]
                                  if mode == "mantenimiento" else [("tf", "Holgura")])
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.rows is None else len(self.rows.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            if role in (Qt.DisplayRole, Qt.ToolTipRole):
                return self.columns[section][1]
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignVCenter | (Qt.AlignRight if self.columns[section][0] in ("dur", "pct", "hh", "tf") else Qt.AlignLeft))

    def data(self, index, role=Qt.DisplayRole):
        if role not in DATA_ROLES or not index.isValid() or self.rows is None:
            return None
        row, col = index.row(), index.column()
        i = int(self.rows.rows[row])
        key = self.columns[col][0]
        if role == DESCRIPTION:
            if i < 0:
                group = self.rows.groups[-i - 1]
                code, name, start, finish, pct = (group["node"]["code"], group["node"]["name"],
                                                  group["start"], group["finish"], group["pct"])
            else:
                a = self.arrays
                code, name, start, finish, pct = a.codes[i], a.names[i], a.start[i], a.finish[i], a.pct[i]
            # Reservar espacio para fechas y avance incluso con nombres extensos.
            return (f"Código: {str(code or '')[:256]}; Nombre: {str(name or '')[:1024]}; "
                    f"Inicio: {fmt_date(day_iso(start))}; Término: {fmt_date(day_iso(finish))}; "
                    f"Avance: {fmt_num(pct, 1)} %")
        if role == ALIGNMENT:
            return ALIGN_RIGHT if key in ("dur", "pct", "hh", "tf") else ALIGN_LEFT
        if role == BACKGROUND and row == getattr(self.parent(), "hover_row", -1):
            return QColor(tokens().surface3)
        if i < 0:
            group = self.rows.groups[-i - 1]
            if role == FONT:
                font = QFont()
                font.setBold(True)
                return font
            if role == BACKGROUND:
                return QColor(tokens().surface2)
            if role == DECORATION and key == "names":
                return icon("chevron-right" if group["collapsed"] else "chevron-down")
            if role == DISPLAY:
                if key == "codes":
                    return group["node"]["code"]
                if key == "names":
                    return "  " * min(20, int(self.rows.levels[row])) + f"{group['node']['name']} ({group['count']} act.)"
                if key in ("start", "finish"):
                    return fmt_date(day_iso(group[key]))
                if key == "pct":
                    return fmt_num(group["pct"], 1)
            if role == TOOLTIP:
                return f"{group['node']['code']} · {group['node']['name']}\n{group['count']} actividades"
            return None
        if role == FOREGROUND and key == "tf" and self.arrays.crit[i]:
            return QColor(tokens().critical)
        if role not in (DISPLAY, TOOLTIP, EDIT):
            return None
        value = getattr(self.arrays, key)[i]
        if role == EDIT:
            if key == "start":
                return QDate.fromString(day_iso(value) or "", "yyyy-MM-dd")
            return value.item() if hasattr(value, "item") else value
        if role in (DISPLAY, TOOLTIP):
            if key in ("start", "finish"):
                return fmt_date(day_iso(value))
            if key in ("dur", "pct", "hh", "tf"):
                return fmt_num(value, 1 if key in ("pct", "hh") else 0)
            if key == "names" and role == DISPLAY:
                return "  " * min(20, int(self.rows.levels[row])) + str(value or "")
            return str(value or "")

    def flags(self, index):
        if not index.isValid():
            return super().flags(index)
        if self.rows.rows[index.row()] >= 0 and self.columns[index.column()][0] in EDITABLE:
            return self._editable_flags
        return self._base_flags

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or not self.flags(index) & Qt.ItemIsEditable:
            return False
        key = self.columns[index.column()][0]
        try:
            if key in ("dur", "pct", "hh"):
                value = float(str(value).replace(",", "."))
                if not math.isfinite(value) or value < 0 or (key == "pct" and value > 100):
                    raise ValueError("Ingresa un valor no negativo; el avance debe estar entre 0 y 100.")
                if key == "dur":
                    if not value.is_integer():
                        raise ValueError("La duración debe ser un número entero de días laborables.")
                    value = int(value)
            elif key == "start":
                value = value.toString("yyyy-MM-dd") if isinstance(value, QDate) else str(value)
                try:
                    value = date.fromisoformat(value).isoformat()
                except ValueError:
                    from datetime import datetime
                    value = datetime.strptime(value, "%d-%m-%Y").date().isoformat()
            else:
                value = str(value or "").strip()
                if key == "names" and not value:
                    raise ValueError("El nombre no puede estar vacío.")
                if key == "macro" and value:
                    if len(value) != 7:
                        raise ValueError("El mes macro debe tener formato AAAA-MM.")
                    date.fromisoformat(value + "-01")
        except (TypeError, ValueError) as exc:
            self.invalid.emit(str(exc))
            return False
        # Commit only when the API succeeds; errors cannot leave phantom edits.
        i = int(self.rows.rows[index.row()])
        self.edited.emit(int(self.arrays.ids[i]), key, value)
        return True


class CachedDelegate(QStyledItemDelegate):
    """Reutiliza celdas visibles sin crear objetos por actividad.

    QStyle sigue dibujando texto, iconos, selección y foco. La clave contempla
    los valores actuales, estado visual y tema; el límite evita crecer al hacer
    scroll sobre cientos de miles de filas.
    """
    MAX_CELLS = 256
    MAX_PIXELS = 2_000_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cells = OrderedDict()
        self._pixels = 0

    def paint(self, painter, option, index):
        model = index.model()
        if not isinstance(model, ActivityTableModel) or model.rows is None:
            return super().paint(painter, option, index)
        row, col = index.row(), index.column()
        i = int(model.rows.rows[row])
        # Leer el valor evita imágenes obsoletas incluso si un consumidor cambia
        # los arrays directamente. Los roles accesibles nunca usan esta caché.
        text = model.data(index, DISPLAY)
        group = model.rows.groups[-i - 1] if i < 0 else None
        variant = (group['collapsed'] if group else bool(model.arrays.crit[i]),
                   row == getattr(model.parent(), 'hover_row', -1))
        dpr = painter.device().devicePixelRatioF()
        pixels = round(option.rect.width() * dpr) * round(option.rect.height() * dpr)
        if pixels > self.MAX_PIXELS:
            return super().paint(painter, option, index)
        key = (text, col, model.columns[col][0], i < 0, variant,
               option.state.value, option.features.value, option.viewItemPosition.value,
               option.rect.size().width(),
               option.rect.size().height(), option.font.key(), option.palette.cacheKey(),
               option.direction.value, dpr, tokens().name)
        pixmap = self._cells.pop(key, None)
        if pixmap is not None:
            self._pixels -= pixmap.width() * pixmap.height()
        if pixmap is None:
            pixmap = QPixmap(round(option.rect.width() * dpr), round(option.rect.height() * dpr))
            pixmap.setDevicePixelRatio(dpr)
            pixmap.fill(Qt.transparent)
            copy = QStyleOptionViewItem(option)
            copy.rect.moveTopLeft(QPoint(0, 0))
            cached_painter = QPainter(pixmap)
            try:
                super().paint(cached_painter, copy, index)
            finally:
                cached_painter.end()
        self._cells[key] = pixmap
        self._pixels += pixmap.width() * pixmap.height()
        while len(self._cells) > self.MAX_CELLS or self._pixels > self.MAX_PIXELS:
            _, retired = self._cells.popitem(last=False)
            self._pixels -= retired.width() * retired.height()
        painter.drawPixmap(option.rect.topLeft(), pixmap)

class DateDelegate(CachedDelegate):
    def createEditor(self, parent, option, index):
        editor = QDateEdit(parent)
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("dd-MM-yyyy")
        return editor

    def setEditorData(self, editor, index):
        value = index.data(Qt.EditRole)
        editor.setDate(value if value and value.isValid() else QDate.currentDate())

    def setModelData(self, editor, model, index):
        model.setData(index, editor.date(), Qt.EditRole)
