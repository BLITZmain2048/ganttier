"""Contenido desplazable y diálogos contenidos en el área útil del monitor."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QHeaderView, QDialog, QDialogButtonBox, QFrame, QLayout, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QWidgetItem, QWizard)


def constrain_actions(content):
    """Un botón conserva su ancho de lectura aunque el contenido tenga tablas anchas."""
    for button in content.findChildren(QAbstractButton):
        if not isinstance(button.parentWidget(), QHeaderView):
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


def scroll_layout(layout, *, first=0, last=None):
    """Envuelve una parte de un QVBoxLayout; las acciones externas permanecen visibles.

    Conserva los widgets, señales, orden y factores de estiramiento existentes.
    El mínimo del contenido se convierte en desplazamiento, no en mínimo de ventana.
    """
    end = layout.count() if last is None else last
    content = QWidget()
    body = QVBoxLayout(content)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(layout.spacing())
    for _ in range(end - first):
        stretch = layout.stretch(first)
        item = layout.takeAt(first)
        if item.widget() is not None:
            body.addWidget(item.widget(), stretch, item.alignment())
        elif item.layout() is not None:
            nested = item.layout()
            nested.setParent(None)
            body.addLayout(nested, stretch)
        else:
            body.addItem(item)
            body.setStretch(body.count() - 1, stretch)
    body.setSizeConstraint(QLayout.SetMinAndMaxSize)
    scroll = QScrollArea()
    scroll.setObjectName("contenidoDesplazable")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setMinimumSize(0, 0)
    scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    scroll.setWidget(content)
    constrain_actions(content)
    for view in content.findChildren(QAbstractItemView):
        if not isinstance(view, QHeaderView):
            view.setMinimumHeight(max(view.minimumHeight(), 100))
    layout.insertWidget(first, scroll, 1)
    return scroll


def available_geometry(widget):
    """Qt entrega coordenadas lógicas; ya contemplan el escalado de Windows."""
    return widget.screen().availableGeometry()


def fit_dialog(dialog):
    area = available_geometry(dialog)
    # Espacio para marco y título, incluso antes del primer show nativo.
    size = QSize(max(1, area.width() - 16), max(1, area.height() - 48))
    dialog.setMinimumSize(0, 0)
    dialog.setMaximumSize(size)
    dialog.resize(dialog.size().boundedTo(size))
    frame = dialog.frameGeometry()
    dialog.move(max(area.left(), min(frame.left(), area.right() - frame.width() + 1)),
                max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1)))


class ResponsiveDialog(QDialog):
    """Desplaza el cuerpo y conserva los botones finales fuera del desplazamiento."""

    def showEvent(self, event):
        if not getattr(self, "_responsive_ready", False):
            layout = self.layout()
            if isinstance(layout, QVBoxLayout) and layout.count():
                last = layout.count()
                tail = layout.itemAt(last - 1)
                if isinstance(tail.widget(), (QDialogButtonBox, QPushButton)) or tail.layout():
                    last -= 1
                # FormDialog ya tiene su propio cuerpo desplazable y pie de acciones.
                if last and not isinstance(layout.itemAt(0).widget(), QScrollArea):
                    self.content_scroll = scroll_layout(layout, last=last)
                layout.setSizeConstraint(QLayout.SetNoConstraint)
            self._responsive_ready = True
        super().showEvent(event)
        fit_dialog(self)


class ResponsiveWizard(QWizard):
    """Cada paso se desplaza; la navegación del asistente sigue siempre a la vista."""

    def showEvent(self, event):
        if not getattr(self, "_responsive_ready", False):
            for page_id in self.pageIds():
                page = self.page(page_id)
                layout = page.layout()
                if isinstance(layout, QVBoxLayout):
                    scroll_layout(layout)
                elif layout is not None:
                    # Las páginas con formulario/grilla conservan su diseño original.
                    holder = QWidget()
                    holder.setLayout(layout)
                    root = QVBoxLayout(page)
                    root.addWidget(holder)
                    scroll_layout(root)
            self._responsive_ready = True
        super().showEvent(event)
        fit_dialog(self)


class HeaderFlowLayout(QLayout):
    """Encabezado que conserva todos los controles y pasa a más filas si no caben."""

    def __init__(self, parent=None, *, minimum_height=56):
        super().__init__(parent)
        self._items = []
        self._minimum_height = minimum_height

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def addWidget(self, widget, stretch=0, alignment=Qt.Alignment()):
        self.addChildWidget(widget)
        item = QWidgetItem(widget)
        item.setAlignment(alignment)
        self.addItem(item)

    def addLayout(self, layout, stretch=0):
        self.addChildLayout(layout)
        self.addItem(layout)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), measure=True)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        height = self._arrange(rect)
        parent = self.parentWidget()
        if parent is not None and parent.minimumHeight() != max(self._minimum_height, height):
            parent.setMinimumHeight(max(self._minimum_height, height))
            parent.updateGeometry()

    def _arrange(self, rect, measure=False):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        spacing = max(0, self.spacing())
        for item in self._items:
            if item.isEmpty():
                continue
            width = min(item.sizeHint().width(), area.width())
            height = item.heightForWidth(width) if item.hasHeightForWidth() else item.sizeHint().height()
            if x > area.left() and x + width > area.right() + 1:
                x, y, row_height = area.left(), y + row_height + spacing, 0
            if not measure:
                item.setGeometry(QRect(x, y, max(0, width), max(0, height)))
            x += width + spacing
            row_height = max(row_height, height)
        return y + row_height - rect.y() + margins.bottom()
