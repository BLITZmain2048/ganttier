"""Accesibilidad acotada para tablas enormes (D03 de la verificación del .exe, 08-10-2026).

Qt publica cada celda de un ``QTableView`` como un elemento accesible: con 100.000 actividades y 11 columnas son
1,1 millones. Un cliente de Automatización de UI de Windows (lector de pantalla, herramienta de pruebas) que recorra el
árbol obliga al hilo de la interfaz a crear esos elementos uno a uno: la ventana «no responde», con un núcleo al 100 % y
~1 GB de memoria que crece mientras dure el recorrido.

Las tablas marcadas publican como máximo dos hijos estables: la celda actual y un
resumen de su fila. Las flechas del teclado permiten recorrer todo el modelo sin
materializar sus celdas en UIA. Las demás tablas conservan la interfaz de Qt.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QAccessible, QAccessibleObject, QAccessibleEvent
from PySide6.QtWidgets import QAccessibleWidget, QAbstractItemView

PROPIEDAD = "ganttier_accesibilidad_ligera"
_instalada = False
MAX_HIJOS = 2
MAX_COLUMNAS_RESUMEN = 32
MAX_TEXTO = 4096


def _texto(value):
    return str(value if value is not None else "")[:MAX_TEXTO]


def resumen_fila(view):
    """Resumen de la fila actual, incluso si está fuera de la zona visible."""
    index = view.currentIndex()
    if not index.isValid():
        return ""
    model = view.model()
    custom = model.data(index, Qt.AccessibleDescriptionRole)
    if custom:
        return _texto(custom)
    parts = []
    for col in range(min(MAX_COLUMNAS_RESUMEN, model.columnCount(index.parent()))):
        value = model.data(model.index(index.row(), col, index.parent()), Qt.DisplayRole)
        label = model.headerData(col, Qt.Horizontal, Qt.DisplayRole)
        if value not in (None, ""):
            parts.append(f"{_texto(label)}: {_texto(value)}")
    return _texto("; ".join(parts))


class _Nodo(QObject):
    """Identidad QObject estable: dos nodos durante toda la vida de la vista."""
    def __init__(self, view, resumen=False):
        super().__init__(view)
        self.view = view
        self.resumen = resumen


class CeldaActual(QAccessibleObject):
    def parent(self):
        return QAccessible.queryAccessibleInterface(self.object().view)

    def childCount(self):
        return 0

    def child(self, index):
        return None

    def childAt(self, x, y):
        return None

    def indexOfChild(self, child):
        return -1

    def role(self):
        return QAccessible.Role.StaticText if self.object().resumen else QAccessible.Role.Cell

    def rect(self):
        view = self.object().view
        index = view.currentIndex()
        if not index.isValid() or not view.isVisible():
            return QRect()
        rect = view.visualRect(index).intersected(view.viewport().rect())
        if rect.isEmpty():
            return QRect()
        return QRect(view.viewport().mapToGlobal(rect.topLeft()), rect.size())

    def state(self):
        view = self.object().view
        index = view.currentIndex()
        state = QAccessible.State()
        state.invalid = not index.isValid()
        state.invisible = not view.isVisible()
        state.offscreen = self.rect().isEmpty()
        state.focusable = not self.object().resumen
        state.focused = state.focusable and view.hasFocus() and index.isValid()
        state.selectable = state.focusable
        state.selected = index.isValid() and view.selectionModel().isSelected(index)
        state.readOnly = not bool(index.flags() & Qt.ItemIsEditable)
        return state

    def text(self, kind):
        node = self.object()
        view, index = node.view, node.view.currentIndex()
        if not index.isValid():
            return ""
        if kind == QAccessible.Text.Description or (node.resumen and kind == QAccessible.Text.Name):
            return resumen_fila(view)
        if kind in (QAccessible.Text.Name, QAccessible.Text.Value):
            value = index.data(Qt.AccessibleTextRole)
            if value is None:
                value = index.data(Qt.DisplayRole)
            label = view.model().headerData(index.column(), Qt.Horizontal, Qt.DisplayRole)
            return _texto(f"{label or 'Celda'}: {_texto(value)}") if kind == QAccessible.Text.Name else _texto(value)
        return ""

    def setText(self, kind, text):
        # Las escrituras se realizan con el delegado normal y la API de la aplicación.
        pass


class _Seguimiento(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.nodes = (_Nodo(view), _Nodo(view, resumen=True))
        self.selection = None
        self.model = None
        view.installEventFilter(self)
        QTimer.singleShot(0, self.conectar)

    def conectar(self):
        selection, model = self.view.selectionModel(), self.view.model()
        if selection is not self.selection:
            if self.selection is not None:
                try:
                    self.selection.currentChanged.disconnect(self.notificar)
                except (RuntimeError, TypeError):
                    pass
            self.selection = selection
            if selection is not None:
                selection.currentChanged.connect(self.notificar)
        if model is not self.model:
            if self.model is not None:
                try:
                    self.model.dataChanged.disconnect(self.notificar)
                    self.model.modelReset.disconnect(self.notificar)
                except (RuntimeError, TypeError):
                    pass
            self.model = model
            if model is not None:
                model.dataChanged.connect(self.notificar)
                model.modelReset.connect(self.notificar)

    def notificar(self, *_):
        if not QAccessible.isActive():
            return
        QAccessible.updateAccessibility(QAccessibleEvent(self.view, QAccessible.Event.ObjectReorder))
        if self.view.currentIndex().isValid():
            for node in self.nodes:
                QAccessible.updateAccessibility(QAccessibleEvent(node, QAccessible.Event.NameChanged))
            if self.view.hasFocus():
                QAccessible.updateAccessibility(QAccessibleEvent(self.nodes[0], QAccessible.Event.Focus))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Show, QEvent.FocusIn, QEvent.ChildAdded):
            self.conectar()
            if event.type() == QEvent.FocusIn:
                self.notificar()
        return False


class TablaLigera(QAccessibleWidget):
    """Interfaz acotada sin TableInterface: nunca expone filas × columnas a UIA."""

    def childCount(self) -> int:
        self.object()._accesibilidad_acotada.conectar()
        return MAX_HIJOS if self.object().currentIndex().isValid() else 0

    def child(self, index: int):
        if 0 <= index < self.childCount():
            return QAccessible.queryAccessibleInterface(self.object()._accesibilidad_acotada.nodes[index])
        return None

    def indexOfChild(self, child) -> int:
        for i in range(self.childCount()):
            if child is not None and child.object() is self.object()._accesibilidad_acotada.nodes[i]:
                return i
        return -1

    def focusChild(self):
        return self.child(0)

    def childAt(self, x, y):
        child = self.child(0)
        return child if child is not None and child.rect().contains(QPoint(x, y)) else None

    def text(self, kind):
        base = super().text(kind)
        if kind == QAccessible.Text.Description and not base:
            widget = self.object()
            model = widget.model() if isinstance(widget, QAbstractItemView) else None
            if model is not None:
                count = f"{model.rowCount():,}".replace(",", ".")
                return (f"Tabla de {count} filas. Use las flechas para leer cada celda y el resumen de su fila. "
                        "Se exponen como máximo dos elementos a la vez.")
        return base


def marcar(view: QAbstractItemView) -> QAbstractItemView:
    """Activa lectura acotada; llamar antes de la primera consulta accesible."""
    install()
    if not hasattr(view, "_accesibilidad_acotada"):
        view._accesibilidad_acotada = _Seguimiento(view)
    view.setProperty(PROPIEDAD, True)
    return view


def _fabrica(clase: str, objeto):
    if isinstance(objeto, _Nodo):
        return CeldaActual(objeto)
    if clase in ("QTableView", "QTreeView") and objeto is not None and objeto.property(PROPIEDAD):
        return TablaLigera(objeto, QAccessible.Role.Table)
    return None


def install() -> None:
    """Registra la fábrica una sola vez; sin efecto si ya estaba instalada."""
    global _instalada
    if not _instalada:
        QAccessible.installFactory(_fabrica)
        _instalada = True
