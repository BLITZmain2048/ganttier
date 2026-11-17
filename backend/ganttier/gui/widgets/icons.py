"""Íconos vectoriales (Lucide, licencia ISC; ver gui/icons/LICENSE-lucide.txt).

``icon("calendar")`` devuelve un QIcon coloreado con el tema activo; ``pixmap(...)`` un QPixmap nítido
en pantallas con escalado (125-200 %). Los SVG usan ``currentColor``: se reemplaza por el color pedido.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sys

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..theme import tokens


def _icons_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "ganttier" / "gui" / "icons"
    return Path(__file__).resolve().parents[1] / "icons"


ICONS_DIR = _icons_dir()


def available() -> list[str]:
    return sorted(p.stem for p in ICONS_DIR.glob("*.svg"))


@lru_cache(maxsize=None)
def _svg(name: str) -> str:
    path = ICONS_DIR / f"{name}.svg"
    if not path.exists():
        raise KeyError(f"Ícono inexistente: {name}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=2048)
def _render(name: str, color: str, size: int, stroke: float, ratio: float) -> QPixmap:
    data = _svg(name).replace("currentColor", color).replace('stroke-width="2"', f'stroke-width="{stroke:g}"')
    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
    px = max(1, round(size * ratio))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def _ratio() -> float:
    screen = QGuiApplication.primaryScreen() if QGuiApplication.instance() else None
    return float(screen.devicePixelRatio()) if screen else 1.0


def pixmap(name: str, color: str | None = None, size: int = 18, stroke: float = 2.0) -> QPixmap:
    """QPixmap del ícono; ``color`` por defecto = texto secundario del tema activo."""
    return _render(name, color or tokens().text2, size, stroke, _ratio())


def icon(name: str, color: str | None = None, size: int = 18, *, disabled_color: str | None = None,
         active_color: str | None = None, stroke: float = 2.0) -> QIcon:
    """QIcon con estados normal/desactivado/activo. Usar en botones, acciones y listas."""
    t = tokens()
    result = QIcon()
    result.addPixmap(pixmap(name, color or t.text2, size, stroke), QIcon.Normal)
    result.addPixmap(pixmap(name, disabled_color or t.text3, size, stroke), QIcon.Disabled)
    if active_color:
        result.addPixmap(pixmap(name, active_color, size, stroke), QIcon.Active)
        result.addPixmap(pixmap(name, active_color, size, stroke), QIcon.Selected)
    return result


def clear_cache() -> None:
    """Llamar al cambiar de tema (los colores cambian)."""
    _render.cache_clear()


ICON_SIZE = QSize(18, 18)
