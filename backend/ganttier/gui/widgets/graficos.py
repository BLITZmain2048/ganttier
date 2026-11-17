"""Gráficos Qt sin dependencias adicionales. Datos ya agregados, nunca actividades."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget, QSizePolicy, QToolTip

from ..theme import tokens


class CurvaSWidget(QWidget):
    """Series acumuladas; ratón o flechas seleccionan un periodo, Enter lo abre.

    ``set_data(labels, planned, actual)`` admite None como dato ausente.
    En modo histograma la segunda serie representa capacidad.
    """
    period_selected = Signal(int)
    period_activated = Signal(int)

    def __init__(self, parent=None, *, histogram=False):
        super().__init__(parent)
        self.histogram = histogram
        self.labels, self.planned, self.actual = [], [], []
        self.selected = -1
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Histograma de carga" if histogram else "Curva S acumulada")

    def set_data(self, labels, planned, actual):
        if not len(labels) == len(planned) == len(actual):
            raise ValueError("Las series deben tener la misma longitud")
        self.labels, self.planned, self.actual = list(labels), list(planned), list(actual)
        self.selected = 0 if labels else -1
        self.setAccessibleDescription("Use flechas para recorrer periodos y Enter para abrir el detalle.")
        self.update()

    def plot_rect(self):
        fm = self.fontMetrics()
        left = max(58, fm.horizontalAdvance("999999 HH") + 8)
        top = fm.height() * 2.5
        bottom = max(64, fm.height() * 4.5)
        return QRectF(left, top, max(1, self.width()-left-18),
                      max(1, self.height()-top-bottom))

    def summary(self, index):
        def fmt(value):
            return "sin datos" if value is None else f"{value:,.1f} HH"
        second = "capacidad" if self.histogram else "real"
        return f"{self.labels[index]}: plan {fmt(self.planned[index])}; {second} {fmt(self.actual[index])}"

    def select(self, index, activate=False):
        if not self.labels:
            return
        self.selected = max(0, min(index, len(self.labels)-1))
        self.setAccessibleDescription(self.summary(self.selected))
        self.period_selected.emit(self.selected)
        if activate:
            self.period_activated.emit(self.selected)
        self.update()

    def _at(self, x):
        r = self.plot_rect()
        return max(0, min(len(self.labels)-1, int((x-r.left()) / r.width()*len(self.labels))))

    def mouseMoveEvent(self, event):
        if self.labels and self.plot_rect().contains(event.position()):
            self.select(self._at(event.position().x()))
            QToolTip.showText(event.globalPosition().toPoint(), self.summary(self.selected), self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.labels and self.plot_rect().contains(event.position()):
            self.select(self._at(event.position().x()), True)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            self.select(self.selected + (1 if event.key() == Qt.Key_Right else -1))
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.select(self.selected, True)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r, t = self.plot_rect(), tokens()
        foreground = self.palette().windowText().color()
        p.setPen(foreground)
        title = "Carga (barras) · Capacidad (línea) · Exceso (trama)" if self.histogram else "Planificada (continua) · Real (discontinua)"
        p.drawText(QRectF(8, 4, self.width()-16, self.fontMetrics().height()*2), Qt.AlignCenter, title)
        if not self.labels:
            p.drawText(r, Qt.AlignCenter, "Sin datos para el periodo")
            return
        maximum = max([float(v) for v in self.planned+self.actual if v is not None]+[1.0]) * 1.1
        step = r.width()/len(self.labels)
        def point(i, v):
            return QPointF(r.left()+(i+.5)*step, r.bottom()-float(v)/maximum*r.height())
        for n in range(5):
            y = r.bottom()-r.height()*n/4
            p.setPen(QPen(self.palette().mid().color(), 1, Qt.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(foreground)
            p.drawText(QRectF(0, y-10, r.left()-6, 20), Qt.AlignRight | Qt.AlignVCenter, f"{maximum*n/4:.0f}")
        for values, color, dashed in ((self.planned, t.accent, False), (self.actual, t.good, True)):
            if self.histogram and values is self.planned:
                for i, value in enumerate(values):
                    if value is None:
                        continue
                    pos = point(i, value)
                    bar = QRectF(pos.x()-step*.35, pos.y(), max(1, step*.7), r.bottom()-pos.y())
                    p.fillRect(bar, QColor(color))
                    cap = self.actual[i]
                    if cap is not None and value > cap:
                        p.fillRect(QRectF(bar.left(), bar.top(), bar.width(), point(i, cap).y()-bar.top()),
                                   self.palette().windowText())
                        from PySide6.QtGui import QBrush
                        p.fillRect(bar, QBrush(QColor(t.accent), Qt.BDiagPattern))
                continue
            path, active = QPainterPath(), False
            for i, value in enumerate(values):
                if value is None:
                    active = False
                    continue
                pos = point(i, value)
                if active:
                    path.lineTo(pos)
                else:
                    path.moveTo(pos)
                active = True
            p.setPen(QPen(QColor(color), 2.5, Qt.DashLine if dashed and not self.histogram else Qt.SolidLine))
            p.drawPath(path)
            for i, value in enumerate(values):
                if value is not None:
                    p.drawEllipse(point(i, value), 2, 2)
        p.setPen(foreground)
        stride = max(1, int(100/max(step, 1)))
        for i in range(0, len(self.labels), stride):
            p.drawText(QRectF(point(i, 0).x()-50, r.bottom()+4, 100, 24), Qt.AlignCenter, str(self.labels[i]))
        if self.selected >= 0:
            x = point(self.selected, 0).x()
            p.setPen(QPen(foreground, 1, Qt.DotLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawText(QRectF(8, self.height()-26, self.width()-16, 24), Qt.AlignCenter,
                       self.fontMetrics().elidedText(self.summary(self.selected), Qt.ElideRight, self.width()-16))
