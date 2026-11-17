"""Paleta de comandos: búsqueda local, sin llamadas REST ni módulos de páginas."""
from __future__ import annotations

from .responsive import ResponsiveDialog

import re
import unicodedata

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..theme import tokens
from .icons import icon


def _normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKD", str(text).casefold())
                   if not unicodedata.combining(c))


def matches(query: str, text: str) -> bool:
    """Subcadenas (palabras en cualquier orden) o iniciales, sin tildes."""
    query, text = _normalized(query).strip(), _normalized(text)
    initials = "".join(word[0] for word in re.findall(r"\w+", text))
    return not query or all(part in text for part in query.split()) or query.replace(" ", "") in initials


class CommandPalette(ResponsiveDialog):
    """``commands``: secuencia de (texto, nombre Lucide, función, atajo)."""

    def __init__(self, commands, parent=None, fallback=None):
        """``fallback``: (plantilla con {q}, ícono, función(texto)) siempre al final si hay ≥ 2 caracteres."""
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.fallback = fallback
        self.setObjectName("CommandPalette")
        self.setWindowTitle("Buscar o ejecutar")
        self.setModal(True)
        self.commands = list(commands)
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar páginas, acciones o proyectos…")
        self.search.setAccessibleName("Buscar comandos")
        self.results = QListWidget()
        self.results.setAccessibleName("Comandos disponibles")
        self.empty_label = QLabel("Sin resultados. Prueba con otra palabra.")
        self.empty_label.setProperty("role", "muted")
        hint = QLabel("↑ ↓ Seleccionar    Enter Ejecutar    Esc Cerrar")
        hint.setProperty("role", "caption")
        for widget in (self.search, self.results, self.empty_label, hint):
            layout.addWidget(widget)
        self.search.textChanged.connect(self.filter_commands)
        self.results.itemActivated.connect(lambda *_: self.execute_current())
        self.results.itemClicked.connect(lambda *_: self.execute_current())
        self.search.installEventFilter(self)
        self.results.installEventFilter(self)
        self.on_theme()
        self.filter_commands("")

    def on_theme(self):
        t = tokens()
        self.setStyleSheet(f"QDialog#CommandPalette {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}")

    def filter_commands(self, query):
        self.results.clear()
        for index, command in enumerate(self.commands):
            text, name, _callback, shortcut = command
            if matches(query, text):
                item = QListWidgetItem(icon(name or "command"), text + (f"    {shortcut}" if shortcut else ""))
                item.setData(Qt.UserRole, index)
                self.results.addItem(item)
        query = query.strip()
        if self.fallback and len(query) >= 2:
            template, name, _callback = self.fallback
            item = QListWidgetItem(icon(name or "search"), template.format(q=query))
            item.setData(Qt.UserRole, -1)
            self.results.addItem(item)
        self.empty_label.setVisible(self.results.count() == 0)
        if self.results.count():
            self.results.setCurrentRow(0)

    def execute_current(self):
        item = self.results.currentItem()
        if item is None:
            return
        index = item.data(Qt.UserRole)
        if index == -1:
            text = self.search.text().strip()
            callback = lambda: self.fallback[2](text)
        else:
            callback = self.commands[index][2]
        self.accept()
        try:
            callback()
        except Exception as exc:
            from .common import report_error
            report_error(exc, self.parentWidget())

    def eventFilter(self, watched, event):
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Up, Qt.Key_Down):
                count = self.results.count()
                if count:
                    delta = 1 if key == Qt.Key_Down else -1
                    self.results.setCurrentRow((self.results.currentRow() + delta) % count)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.execute_current()
                return True
            if key == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.parentWidget():
            self.move(self.parentWidget().frameGeometry().center() - self.rect().center())
        self.search.setFocus(Qt.OtherFocusReason)
