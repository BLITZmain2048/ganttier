"""Tema visual (claro/oscuro) basado en el estilo Fusion de Qt y una hoja QSS con tokens."""
from __future__ import annotations

from dataclasses import dataclass
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Tokens:
    name: str
    bg: str
    surface: str
    surface2: str
    surface3: str
    border: str
    border_strong: str
    text: str
    text2: str
    text3: str
    accent: str
    accent2: str
    accent_soft: str
    sidebar: str
    sidebar_text: str
    grid: str
    weekend: str
    holiday: str
    bar: str
    bar_crit: str
    bar_base: str
    bar_window: str
    bar_window_hard: str
    bar_progress: str
    milestone: str
    datadate: str
    today: str
    good: str
    warning: str
    critical: str
    series: tuple[str, ...]


LIGHT = Tokens(
    "light", bg="#f5f6f8", surface="#ffffff", surface2="#f8f9fb", surface3="#eef1f5", border="#e3e6ea",
    border_strong="#b9c0ca", text="#16191d", text2="#4b5360", text3="#7a828e", accent="#1f5fae", accent2="#2a78d6",
    accent_soft="#dbe8f8", sidebar="#17263a", sidebar_text="#c9d4e3", grid="#e7eaee", weekend="#f1f3f6",
    holiday="#fde9e3", bar="#2a78d6", bar_crit="#d03b3b", bar_base="#9aa3ae", bar_window="#2a78d621",
    bar_window_hard="#eb683429", bar_progress="#104281", milestone="#16191d", datadate="#eb6834", today="#1baf7a",
    good="#0ca30c", warning="#fab219", critical="#d03b3b",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"),
)

DARK = Tokens(
    "dark", bg="#111315", surface="#1a1c1f", surface2="#202327", surface3="#272b30", border="#33383f",
    border_strong="#4a515b", text="#eceef1", text2="#b6bcc6", text3="#858c97", accent="#5b9bea", accent2="#3987e5",
    accent_soft="#1d3350", sidebar="#0c1522", sidebar_text="#aebbd0", grid="#2a2e34", weekend="#1f2226",
    holiday="#3a2621", bar="#3987e5", bar_crit="#e66767", bar_base="#6b7480", bar_window="#3987e52e",
    bar_window_hard="#d9592638", bar_progress="#9ec5f4", milestone="#eceef1", datadate="#d95926", today="#199e70",
    good="#0ca30c", warning="#fab219", critical="#d03b3b",
    series=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"),
)

_current: Tokens = LIGHT

SPACE = dict(xs=4, sm=8, md=12, lg=16, xl=24)
RADIUS = 8
RADIUS_LG = 12
FONT_SIZES = dict(caption=8.5, body=9.5, title=15, h2=11.5, kpi=20)
ROW_HEIGHT = 30


class _ThemeSignals(QObject):
    theme_changed = Signal(str)


theme_signals = _ThemeSignals()


def ui_font() -> QFont:
    families = (["Segoe UI"] if sys.platform == "win32" else []) + ["Inter", "Noto Sans", "DejaVu Sans"]
    available = set(QFontDatabase.families()) if QApplication.instance() else set()
    font = QFont(next((name for name in families if name in available), families[0]))
    font.setFamilies(families)
    font.setPointSizeF(FONT_SIZES["body"])
    return font


def tokens() -> Tokens:
    """Tokens del tema activo (úsalos al dibujar Gantt y gráficos)."""
    return _current


def qcolor(hex_or_rgba: str) -> QColor:
    """Acepta #rrggbb o #rrggbbaa (alfa al final, como en CSS)."""
    s = hex_or_rgba.lstrip("#")
    if len(s) == 8:
        c = QColor("#" + s[:6])
        c.setAlpha(int(s[6:], 16))
        return c
    return QColor("#" + s)


_style_set = False


def apply_theme(app: QApplication, name: str = "light") -> Tokens:
    global _current
    t = DARK if name == "dark" else LIGHT
    _current = t
    # Cambiar el estilo con ventanas vivas puede provocar fallas nativas: se fija una sola vez.
    global _style_set
    if not _style_set:
        app.setStyle("Fusion")
        _style_set = True
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(t.bg))
    pal.setColor(QPalette.WindowText, QColor(t.text))
    pal.setColor(QPalette.Base, QColor(t.surface))
    pal.setColor(QPalette.AlternateBase, QColor(t.surface2))
    pal.setColor(QPalette.ToolTipBase, QColor(t.surface))
    pal.setColor(QPalette.ToolTipText, QColor(t.text))
    pal.setColor(QPalette.Text, QColor(t.text))
    pal.setColor(QPalette.PlaceholderText, QColor(t.text3))
    pal.setColor(QPalette.Button, QColor(t.surface))
    pal.setColor(QPalette.ButtonText, QColor(t.text))
    pal.setColor(QPalette.Highlight, QColor(t.accent_soft))
    pal.setColor(QPalette.HighlightedText, QColor(t.text))
    pal.setColor(QPalette.Link, QColor(t.accent))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(t.text3))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t.text3))
    app.setPalette(pal)
    app.setFont(ui_font())
    app.setStyleSheet(stylesheet(t))
    from .widgets import icons
    icons.clear_cache()
    theme_signals.theme_changed.emit(t.name)
    return t



def _check_image(color: str) -> str:
    """SVG de la marca de verificación (Qt QSS sólo acepta archivos): se escribe una vez en temp."""
    import tempfile
    from pathlib import Path
    folder = Path(tempfile.gettempdir()) / "ganttier_qss"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"check_{color.lstrip('#')}.svg"
    if not path.exists():
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                        f'stroke="{color}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">'
                        '<path d="M20 6 9 17l-5-5"/></svg>', encoding="utf-8")
    return path.as_posix()


def stylesheet(t: Tokens) -> str:
    # Qt usa el modelo de caja CSS: 18 + 2*6 + 2*1 = 32 px en botones.
    on_accent = LIGHT.surface if t.name == "light" else DARK.bg
    return f"""
    QWidget {{ font-size: {FONT_SIZES['body']}pt; color: {t.text}; }}
    QMainWindow, QDialog {{ background: {t.bg}; }}
    QLabel {{ background: transparent; }}
    QLabel[role="title"] {{ font-size: 15pt; font-weight: 600; }}
    QLabel[role="section"], QLabel[cardTitle="true"] {{ font-size: 11.5pt; font-weight: 600; }}
    QLabel[role="subtitle"] {{ color: {t.text2}; }}
    QLabel[role="caption"] {{ font-size: 8.5pt; color: {t.text3}; }}
    QLabel[role="muted"], QLabel[muted="true"] {{ color: {t.text3}; }}
    QLabel[role="error"] {{ color: {t.critical}; font-size: 8.5pt; }}
    QFrame[card="true"] {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}
    QWidget[toolbar="true"], QWidget[emptystate="true"] {{ background: transparent; }}
    QLabel[kpiLabel="true"] {{ color: {t.text3}; font-size: 8.5pt; text-transform: uppercase; }}
    QLabel[kpiValue="true"] {{ font-size: 20pt; font-weight: 600; }}
    QLabel[badge] {{ background: {t.surface3}; color: {t.text2}; border-radius: 10px; padding: 3px 8px; font-size: 8.5pt; }}
    QLabel[badge="proyecto"] {{ background: {t.accent_soft}; color: {t.accent}; }}
    QLabel[badge="mantenimiento"], QLabel[badge="good"] {{ color: {t.good}; }}
    QLabel[badge="warning"] {{ color: {t.warning}; }}
    QLabel[badge="critical"] {{ color: {t.critical}; }}

    QPushButton, QToolButton {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 8px; min-height: 18px; padding: 6px 12px; }}
    QToolButton {{ padding: 6px; }}
    QPushButton[kind="secondary"] {{ background: {t.surface}; border-color: {t.border}; }}
    QPushButton:hover, QToolButton:hover {{ background: {t.surface2}; border-color: {t.border_strong}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {t.surface3}; }}
    QPushButton:checked, QToolButton:checked {{ background: {t.accent_soft}; color: {t.accent}; }}
    QPushButton[primary="true"], QPushButton[kind="primary"] {{ background: {t.accent}; color: {on_accent}; border-color: {t.accent}; }}
    QPushButton[primary="true"]:hover, QPushButton[kind="primary"]:hover {{ background: {t.accent2}; border-color: {t.accent2}; }}
    QPushButton[primary="true"]:pressed, QPushButton[kind="primary"]:pressed {{ background: {t.accent}; border-color: {t.text2}; }}
    QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; }}
    QPushButton[kind="ghost"]:hover {{ background: {t.surface3}; }}
    QPushButton[kind="ghost"]:pressed {{ background: {t.accent_soft}; }}
    QPushButton[danger="true"], QPushButton[kind="danger"] {{ color: {t.critical}; border-color: {t.critical}; }}
    QPushButton[danger="true"]:hover, QPushButton[kind="danger"]:hover {{ background: {t.surface3}; }}
    QPushButton[danger="true"]:pressed, QPushButton[kind="danger"]:pressed {{ background: {t.critical}; color: {LIGHT.surface}; }}
    QPushButton:focus, QPushButton[kind]:focus, QPushButton[primary="true"]:focus,
    QPushButton[danger="true"]:focus, QToolButton:focus {{ border: 2px solid {t.accent2}; padding: 5px 11px; }}
    QToolButton:focus {{ padding: 5px; }}
    QPushButton:disabled, QPushButton[kind]:disabled, QPushButton[primary="true"]:disabled,
    QPushButton[danger="true"]:disabled, QToolButton:disabled {{ background: {t.surface2}; color: {t.text3}; border-color: {t.border}; }}
    QToolButton::menu-indicator {{ subcontrol-position: bottom right; }}
    QToolBar {{ background: {t.surface}; border: 0; border-bottom: 1px solid {t.border}; spacing: 8px; padding: 4px 8px; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; min-height: 22px; padding: 4px 8px;
        selection-background-color: {t.accent_soft}; selection-color: {t.text}; }}
    QPlainTextEdit, QTextEdit {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px; }}
    QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover {{ border-color: {t.border_strong}; }}
    QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {t.accent}; }}
    QLineEdit[invalid="true"], QComboBox[invalid="true"], QAbstractSpinBox[invalid="true"], QPlainTextEdit[invalid="true"] {{ border-color: {t.critical}; }}
    QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{ background: {t.surface2}; color: {t.text3}; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        selection-background-color: {t.accent_soft}; selection-color: {t.text}; padding: 4px; }}

    QTableView {{ background: {t.surface}; alternate-background-color: {t.surface2}; gridline-color: transparent;
        border: 1px solid {t.border}; selection-background-color: {t.accent_soft}; selection-color: {t.text}; }}
    QTableView::item {{ padding: 3px 6px; border: 0; }}
    QTableView::item:hover {{ background: {t.surface3}; }}
    QTableView::item:selected, QTableView::item:selected:hover {{ background: {t.accent_soft}; color: {t.text}; }}
    QHeaderView {{ background: {t.surface2}; }}
    QHeaderView::section {{ background: {t.surface2}; color: {t.text2}; border: 0; border-bottom: 1px solid {t.border};
        padding: 6px 8px; font-weight: 600; }}
    QTableCornerButton::section {{ background: {t.surface2}; border: 0; }}
    QTabWidget::pane {{ border: 0; border-top: 1px solid {t.border}; background: {t.surface}; }}
    QTabBar::tab {{ background: transparent; color: {t.text2}; padding: 8px 14px; border-bottom: 2px solid transparent; }}
    QTabBar::tab:hover {{ background: {t.surface2}; color: {t.text}; }}
    QTabBar::tab:selected {{ color: {t.accent}; border-bottom-color: {t.accent}; }}
    QTabBar::tab:disabled {{ color: {t.text3}; }}

    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
    QScrollBar::handle {{ background: {t.border_strong}; border-radius: 4px; }}
    QScrollBar::handle:vertical {{ min-height: 24px; }}
    QScrollBar::handle:horizontal {{ min-width: 24px; }}
    QScrollBar::handle:hover {{ background: {t.text3}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px; padding: 6px 8px; }}
    QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px; }}
    QMenu::item {{ padding: 6px 24px 6px 10px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
    QMenu::item:disabled {{ color: {t.text3}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}
    QCheckBox, QRadioButton {{ spacing: 7px; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {t.border_strong}; background: {t.surface}; }}
    QCheckBox::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border: 1px solid {t.accent}; image: url({_check_image("#ffffff")}); }}
    QTableView::indicator:checked {{ background: {t.accent}; border: 1px solid {t.accent}; border-radius: 4px; image: url({_check_image("#ffffff")}); }}
    QTableView::indicator {{ width: 14px; height: 14px; border: 1px solid {t.border_strong}; border-radius: 4px; background: {t.surface}; }}
    QRadioButton::indicator:checked {{ background: {t.accent}; border: 3px solid {t.surface}; }}
    QCheckBox::indicator:indeterminate {{ background: {t.text3}; border: 4px solid {t.surface}; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover,
    QCheckBox::indicator:focus, QRadioButton::indicator:focus {{ border-color: {t.accent2}; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {t.text3}; }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {t.surface3}; border-color: {t.border}; }}
    QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{ background: {t.text3}; }}
    QProgressBar {{ background: {t.surface3}; color: {t.text2}; border: 0; border-radius: 3px; min-height: 6px; max-height: 8px; text-align: center; }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
    QSplitter::handle {{ background: {t.border}; width: 1px; height: 1px; }}
    QSplitter::handle:hover {{ background: {t.accent}; }}
    QGroupBox {{ border: 1px solid {t.border}; border-radius: 8px; margin-top: 14px; padding-top: 6px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.text2}; font-weight: 600; }}

    #Sidebar {{ background: {t.sidebar}; }}
    #Sidebar QLabel#Brand {{ color: {LIGHT.surface}; font-size: 15pt; font-weight: 600; padding: 12px 14px 2px; }}
    #Sidebar QLabel#BrandSub {{ color: {t.sidebar_text}; font-size: 8.5pt; padding: 0 14px 8px; }}
    #Sidebar QLabel[role="section"], #Sidebar QLabel[section="true"] {{ color: {t.sidebar_text}; font-size: 8.5pt;
        font-weight: 600; text-transform: uppercase; padding: 12px 14px 4px; }}
    #Sidebar QListWidget {{ background: transparent; border: 0; color: {t.sidebar_text}; outline: 0; padding: 4px; }}
    #Sidebar QListWidget::item {{ padding: 7px 12px; margin: 2px 4px; border-radius: 8px; }}
    #Sidebar QListWidget::item:hover {{ background: {t.accent_soft}; color: {t.text}; }}
    #Sidebar QListWidget::item:selected {{ background: {t.accent}; color: {on_accent}; }}
    #Sidebar QListWidget::item:disabled {{ color: {t.sidebar_text}; font-size: 8.5pt; text-transform: uppercase; }}
    #Header {{ background: {t.surface}; min-height: 56px; border-bottom: 1px solid {t.border}; }}
    #Header QLabel#Title {{ font-size: 15pt; font-weight: 600; }}
    QStatusBar {{ background: {t.surface2}; color: {t.text2}; border-top: 1px solid {t.border}; font-size: 8.5pt; }}
    QStatusBar::item {{ border: 0; }}
    """
