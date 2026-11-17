"""Contrato UX1. Ejecutar con QT_QPA_PLATFORM=offscreen y pytest-qt."""
from __future__ import annotations

import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTEST_QT_API"] = "pyside6"
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QShortcut
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QMessageBox, QPushButton, QTableView, QWidget

from ganttier.gui import theme
from ganttier.gui.widgets import common, icons


@pytest.fixture(autouse=True)
def light_theme(qapp):
    original = theme.tokens().name
    theme.apply_theme(qapp, "light")
    yield
    theme.apply_theme(qapp, original)


def test_search_debounce_restarts_and_clear(qapp, qtbot):
    search = common.SearchBox(delay_ms=120)
    qtbot.addWidget(search)
    emitted = []
    search.searched.connect(emitted.append)
    search.show()
    qtbot.keyClicks(search, "a")
    qtbot.wait(70)
    qtbot.keyClicks(search, "b")
    qtbot.wait(70)
    assert emitted == []  # el plazo del primer carácter ya venció
    qtbot.waitUntil(lambda: emitted == ["ab"])
    qtbot.wait(160)
    assert emitted == ["ab"]
    assert search.isClearButtonEnabled()
    assert not search._search_action.icon().isNull()
    with qtbot.waitSignal(search.searched) as signal:
        search.clear()
    assert signal.args == [""]


def test_toolbar_actions_menu_and_shortcut(qapp, qtbot):
    toolbar = common.Toolbar()
    qtbot.addWidget(toolbar)
    calls = []
    save = toolbar.add_action("Guardar", lambda: calls.append("save"), icon="save", primary=True, shortcut="Ctrl+G")
    label = QLabel("Proyecto")
    assert toolbar.add(label) is label
    toolbar.add_separator()
    toolbar.add_stretch()
    search = toolbar.add_search("Filtrar", delay_ms=10)
    more = toolbar.add_menu("Más", [("Exportar", lambda: calls.append("export"), "download")])
    toolbar.show()
    assert save.property("kind") == "primary"
    assert not save.icon().isNull()
    assert save.toolTip() == "Guardar (Ctrl+G)"
    assert len(save.findChildren(QShortcut)) == 1
    save.click()
    more.menu().actions()[0].trigger()
    assert calls == ["save", "export"]
    assert search.placeholderText() == "Filtrar"
    assert label.parentWidget() is toolbar
    assert toolbar.findChildren(QFrame)


def test_button_legacy_arguments_and_parentless_shortcut(qapp, qtbot):
    calls = []
    legacy = common.button("Borrar", lambda: calls.append(True), False, True, "Eliminar fila")
    qtbot.addWidget(legacy)
    assert legacy.property("danger") is True
    assert legacy.toolTip() == "Eliminar fila"
    legacy.click()
    assert calls == [True]
    loose = common.button("Guardar", shortcut="Ctrl+G")
    qtbot.addWidget(loose)
    assert loose.toolTip() == "Guardar (Ctrl+G)"
    assert loose.findChildren(QShortcut) == []
    parent = QWidget()
    qtbot.addWidget(parent)
    loose.setParent(parent)
    assert len(loose.findChildren(QShortcut)) == 1
    loose.setParent(None)
    qapp.sendPostedEvents()
    assert loose._key_shortcut is None


def test_empty_state_with_action_and_section(qapp, qtbot):
    calls = []
    empty = common.EmptyState("folder-open", "Sin proyectos", "Crea el primero.",
                              [("Crear", lambda: calls.append(True), True)])
    qtbot.addWidget(empty)
    empty.show()
    action = empty.findChild(QPushButton)
    action.click()
    assert calls == [True]
    assert action.property("primary") is True
    empty.set_text("Sin resultados", "Prueba otra búsqueda.")
    assert empty.accessibleName() == "Sin resultados"
    assert empty._text_label.text() == "Prueba otra búsqueda."
    section = common.Section("Actividades", "Lista del proyecto")
    qtbot.addWidget(section)
    section.actions.add_action("Nueva", lambda: None)
    section.body.addWidget(QLabel("Contenido"))
    assert isinstance(section.actions, common.Toolbar)
    assert section.body.count() == 1


def test_status_banner_has_text_and_icon(qapp, qtbot):
    banner = common.StatusBanner()
    qtbot.addWidget(banner)
    for kind, expected in (("good", "Correcto"), ("warning", "Atención"), ("critical", "Error"), ("info", "Información")):
        banner.set(kind, "Resultado", "Detalle")
        assert expected in banner.accessibleName()
        assert not banner._status_icon.pixmap().isNull()
        assert banner._text_label.text() == "Detalle"


def test_toast_stack_expires_and_action(qapp, qtbot, monkeypatch):
    from ganttier.gui import app
    window = QWidget()
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    monkeypatch.setattr(app, "main_window", lambda: window)
    calls = []
    first = common.toast("Guardado", timeout_ms=1000)
    second = common.toast("Programa creado", action_text="Abrir", action=lambda: calls.append(True), timeout_ms=5000)
    assert first.parentWidget() is window
    assert first.isVisible() and second.isVisible()
    assert first.geometry().bottom() < second.geometry().top()
    assert "Correcto" in first.accessibleName()
    window.resize(1000, 700)
    qtbot.waitUntil(lambda: second.geometry().right() <= window.width())
    action = next(b for b in second.findChildren(QPushButton) if b.text() == "Abrir")
    action.click()
    assert calls == [True]
    assert len(window._ux_toast_stack._notices) == 1
    qtbot.waitUntil(lambda: not window._ux_toast_stack._notices, timeout=2500)
    qtbot.waitUntil(lambda: not window.findChildren(QFrame, "Toast"))


def test_notify_uses_toast_and_fallback_logs(qapp, monkeypatch, caplog):
    from ganttier.gui import app
    monkeypatch.setattr(app, "main_window", lambda: None)
    with caplog.at_level("INFO", logger=common.__name__):
        assert common.toast("Aviso sin ventana") is None
    assert "Aviso sin ventana" in caplog.text
    calls = []
    monkeypatch.setattr(common, "toast", lambda text, kind: calls.append((text, kind)))
    common.notify("Listo", "ok")
    assert calls == [("Listo", "ok")]


@pytest.mark.parametrize("kind", ["int", "float", "pct"])
def test_header_alignment_and_tooltip(qapp, kind):
    model = common.DictTableModel([common.Column("value", "Nombre completo", kind), common.Column("code", "Código")])
    numeric = model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole)
    textual = model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole)
    assert numeric & int(Qt.AlignmentFlag.AlignRight)
    assert textual & int(Qt.AlignmentFlag.AlignLeft)
    assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole) == "Nombre completo"
    assert model.headerData(-1, Qt.Orientation.Horizontal) is None
    assert model.headerData(2, Qt.Orientation.Horizontal) is None


def test_fit_columns_measures_headers_display_values_and_caps_sample(qapp, qtbot):
    class CountingModel(common.DictTableModel):
        def data(self, index, role=Qt.ItemDataRole.DisplayRole):
            if role == Qt.ItemDataRole.DisplayRole:
                self.display_reads += 1
            return super().data(index, role)

    columns = [common.Column("horizon", "Fin / horizonte de planificación"), common.Column("date", "Fecha de corte", "date")]
    model = CountingModel(columns, [{"horizon": "Programa " + "W" * 20, "date": "2026-09-28"}] * 500)
    model.display_reads = 0
    table = QTableView()
    qtbot.addWidget(table)
    table.setModel(model)
    common.setup_table(table, sortable=False, stretch_last=False)
    table.resize(1000, 400)
    table.ensurePolished()
    table.horizontalHeader().ensurePolished()
    model.display_reads = 0
    common.fit_columns(table, min_w=56, max_w=80, sample=999)
    assert model.display_reads == 200 * len(columns)
    font = QFont(table.horizontalHeader().font())
    font.setWeight(QFont.Weight.DemiBold)
    metrics = QFontMetrics(font)
    for index, column in enumerate(columns):
        assert table.columnWidth(index) >= metrics.horizontalAdvance(column.title) + 20
    common.fit_columns(table, max_w=360)
    assert table.columnWidth(0) >= QFontMetrics(table.font()).horizontalAdvance("Programa " + "W" * 20)
    assert table.verticalHeader().isHidden()
    assert table.verticalHeader().defaultSectionSize() == 30
    assert not table.showGrid()
    assert table.alternatingRowColors()
    assert table.textElideMode() == Qt.TextElideMode.ElideRight


def test_form_two_columns_help_inline_errors_and_correction(qapp, qtbot):
    # help mantiene exactamente su índice posicional anterior.
    positional = common.Field("f0", "Campo 0", "text", [], 0, 1e12, 2, False, "Ayuda visible")
    fields = [positional] + [common.Field(f"f{i}", f"Campo {i}") for i in range(1, 9)]
    dialog = common.FormDialog("Editar", fields, validate=lambda values: {"f0": "Falta el nombre"} if not values["f0"] else None)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog._field_grid.columnCount() == 2
    assert dialog._field_grid.itemAtPosition(0, 1) is not None
    assert dialog._help_labels["f0"].isVisible()
    assert dialog._help_labels["f0"].text() == "Ayuda visible"
    assert dialog._help_labels["f0"].property("role") == "caption"
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog._errors["f0"].isVisible()
    assert dialog._errors["f0"].text() == "Falta el nombre"
    assert dialog._error_summary.isVisible()
    assert dialog._editors["f0"].property("invalid") is True
    dialog._editors["f0"].setText("Proyecto")
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog._errors["f0"].isHidden()
    assert dialog._editors["f0"].property("invalid") is False


def test_form_required_small_grid_explicit_columns_and_legacy_validation(qapp, qtbot, monkeypatch):
    required = common.FormDialog("Crear", [common.Field("name", "Nombre", required=True)])
    qtbot.addWidget(required)
    assert required._field_grid.columnCount() == 1
    assert any(label.text() == "Nombre *" for label in required.findChildren(QLabel))
    required._accept()
    assert required._errors["name"].text() == "Este campo es obligatorio."
    dialog = common.FormDialog("Editar", [common.Field("name", "Nombre")], columns=2, validate=lambda _: "Error general")
    qtbot.addWidget(dialog)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: messages.append(text))
    dialog._accept()
    assert messages == ["Error general"]
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog._field_grid.columnCount() == 2


def test_apply_theme_twice_emits_clears_icons_and_preserves_style(qapp, qtbot, monkeypatch):
    calls, emitted = [], []
    original_style_setter = qapp.setStyle
    original_clear_cache = icons.clear_cache

    def set_style(style):
        calls.append(style)
        return original_style_setter(style)

    cleared = []

    def clear_cache():
        cleared.append(True)
        original_clear_cache()

    monkeypatch.setattr(qapp, "setStyle", set_style)
    monkeypatch.setattr(icons, "clear_cache", clear_cache)
    window = common.Toolbar()
    qtbot.addWidget(window)
    window.add_action("Guardar", lambda: None, icon="save")
    window.add_search()
    window.show()
    theme.theme_signals.theme_changed.connect(emitted.append)
    try:
        first = theme.apply_theme(qapp, "dark")
        second = theme.apply_theme(qapp, "light")
    finally:
        theme.theme_signals.theme_changed.disconnect(emitted.append)
    assert [first.name, second.name] == ["dark", "light"]
    assert emitted == ["dark", "light"]
    assert cleared == [True, True]
    assert calls == []  # Fusion ya fue fijado por la fixture; nunca se reinstala.
    assert qapp.font().pointSizeF() == 9.5
    assert theme.LIGHT.bg == "#f5f6f8"
    assert theme.LIGHT.border == "#e3e6ea"
    assert callable(common.MplCanvas.apply_plot_theme)
