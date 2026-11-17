"""H1 de la GUI: edición masiva (P4-02), vistas guardadas (P4-03), filtro de desconexión y borrado protegido (D3)."""
from __future__ import annotations

import os

import numpy as np
import pytest

from ganttier.gui.gantt.model import ActivityArrays, RowModel

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _columnar(n=6):
    return dict(ids=list(range(1, n + 1)), codes=[f"A{i}" for i in range(n)], names=[f"Act {i}" for i in range(n)],
                starts=["2026-01-05"] * n, finishes=["2026-01-09"] * n, durations=[5] * n, work_hours=[10.0 * (i + 1) for i in range(n)],
                skills=["prot" if i % 2 else "civil" for i in range(n)], locations=["S/E Norte"] * n,
                outages=[i in (1, 4) for i in range(n)], critical=[i == 0 for i in range(n)])


def test_filtro_de_desconexion_en_el_modelo():
    arrays = ActivityArrays(_columnar())
    assert RowModel(arrays, grouped=False, outage=True).matched_count == 2
    assert RowModel(arrays, grouped=False, outage=True, text="prot").matched_count == 1  # A1 (prot, desconexión)


def test_dialogo_de_lote_calcula_parches_y_valida(qapp):
    from ganttier.gui.gantt import bulk_edit
    arrays = ActivityArrays(_columnar())
    ids = bulk_edit.filtered_ids(arrays, "prot", False)
    assert ids == [2, 4, 6]
    dialog = bulk_edit.BulkEditDialog(arrays, ids, 1, [{"id": 7, "name": "7x7"}], "proyecto")
    assert dialog.validation_error() == "Marca al menos un campo para cambiar."
    dialog.checks["work_hours"].setChecked(True)
    dialog.hh_mode.setCurrentIndex(dialog.hh_mode.findData("pct"))
    dialog.hh_value.setValue(10)
    dialog.checks["calendar_id"].setChecked(True)
    dialog.calendar.setCurrentIndex(dialog.calendar.findData(7))
    patches = dialog.patches()
    assert [p["id"] for p in patches] == [2, 4, 6]
    assert [p["work_hours"] for p in patches] == [22.0, 44.0, 66.0] and all(p["calendar_id"] == 7 for p in patches)
    assert set(patches[0]) == {"id", "work_hours", "calendar_id"}  # los campos no marcados no se envían
    dialog.scope_selected.setChecked(True)
    assert [p["id"] for p in dialog.patches()] == [1]
    dialog.hh_value.setValue(-100)
    assert "−100 %" in dialog.validation_error()
    big = bulk_edit.BulkEditDialog(arrays, list(range(1, bulk_edit.MAX_BATCH + 2)), None, [], "proyecto")
    big.checks["priority"].setChecked(True)
    assert "máximo por lote" in big.validation_error()
    assert dialog.preview.rowCount() >= 1


@pytest.fixture(scope="module")
def h1_window(request, tmp_path_factory):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    pytest.importorskip("fastapi")
    temp = tmp_path_factory.mktemp("h1_api")
    patcher = pytest.MonkeyPatch()
    patcher.setenv("GANTTIER_DB", str(temp / "ganttier.sqlite"))
    patcher.setenv("GANTTIER_DATA_DIR", str(temp / "data"))
    patcher.setenv("MPLCONFIGDIR", str(temp / "matplotlib"))
    from PySide6.QtCore import QSettings, QThreadPool
    previous = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(temp / "settings"))
    qapp = request.getfixturevalue("qapp")
    from ganttier.gui.app import create_window
    win, server = create_window(theme="light")
    win.show()
    project = win.state.client.demo("proyecto")
    yield win, project
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()
    win.close()
    win.state.client.close()
    server.stop()
    QSettings.setDefaultFormat(previous)
    patcher.undo()


def _settle(qtbot, page):
    from ganttier.gui.widgets.common import _active
    qtbot.waitUntil(lambda: not page.loading and not page.mutating and not _active, timeout=60000)
    qtbot.wait(150)
    qtbot.waitUntil(lambda: not page.loading and not page.mutating and not _active, timeout=60000)


def _page(h1_window, qtbot):
    win, project = h1_window
    win.state.open_project(project["id"])
    win.go("gantt")
    page = win.page("gantt")
    _settle(qtbot, page)
    return win, page


def test_edicion_masiva_real_cambia_solo_el_alcance_filtrado(h1_window, qtbot, monkeypatch):
    from ganttier.gui.gantt import bulk_edit
    win, page = _page(h1_window, qtbot)
    client, pid = win.state.client, win.state.pid
    before = {a["id"]: a for a in client.activities(pid, limit=5000)}
    target = next(a for a in before.values() if a.get("required_skill"))
    skill = target["required_skill"]
    page.search.setText(skill)
    _settle(qtbot, page)
    page.filter_timer.timeout.emit()
    _settle(qtbot, page)
    expected = set(bulk_edit.filtered_ids(page.arrays, skill, False))
    assert expected and len(expected) < len(before)

    def configure(dialog):
        dialog.checks["priority"].setChecked(True)
        dialog.priority.setValue(87)
        dialog.checks["work_hours"].setChecked(True)
        dialog.hh_mode.setCurrentIndex(dialog.hh_mode.findData("pct"))
        dialog.hh_value.setValue(50)
        return 1
    monkeypatch.setattr(bulk_edit.BulkEditDialog, "exec", configure)
    page.bulk_edit()
    _settle(qtbot, page)
    after = {a["id"]: a for a in client.activities(pid, limit=5000)}
    for aid, activity in after.items():
        if aid in expected:
            assert activity["priority"] == 87
            assert activity["work_hours"] == pytest.approx(round((before[aid]["work_hours"] or 0) * 1.5, 4))
        else:
            assert activity["priority"] == before[aid]["priority"] and activity["work_hours"] == before[aid]["work_hours"]
    page.search.clear()
    _settle(qtbot, page)


def test_vistas_guardadas_restauran_filtros_columnas_y_escala(h1_window, qtbot):
    win, page = _page(h1_window, qtbot)
    page.search.setText("Montaje")
    page.only_critical.setChecked(True)
    page.column_actions[7].setChecked(False)           # oculta «Mes macro»
    page.scale.setCurrentIndex(2)
    page.view_actions["baseline"].setChecked(False)
    page.save_view("Reunión semanal")
    assert "Reunión semanal" in page.saved_views()
    assert page.view_combo.currentData() == "Reunión semanal"
    # Cambiar todo y volver a aplicar la vista.
    page.search.clear()
    page.only_critical.setChecked(False)
    page.column_actions[7].setChecked(True)
    page.scale.setCurrentIndex(0)
    page.view_actions["baseline"].setChecked(True)
    page._view_selected(page.view_combo.findData("Reunión semanal"))
    _settle(qtbot, page)
    assert page.search.text() == "Montaje" and page.only_critical.isChecked()
    assert page.gantt.table.isColumnHidden(7) and page.scale.currentIndex() == 2
    assert not page.view_actions["baseline"].isChecked()
    # Una vista con columnas inexistentes (versión anterior) no rompe.
    page.apply_view({"hidden": [99, "x"], "sort": "inexistente", "scale": 99})
    _settle(qtbot, page)
    # Persistencia en la configuración del usuario.
    assert "Reunión semanal" in win.state.settings.value(page.VIEWS_KEY)
    page._populate_views(select="Reunión semanal")
    page.delete_view_action.setEnabled(True)
    from ganttier.gui.pages import activities
    activities_confirm = activities.confirm
    activities.confirm = lambda *args, **kwargs: True
    try:
        page.delete_current_view()
    finally:
        activities.confirm = activities_confirm
    assert "Reunión semanal" not in page.saved_views()
    page.apply_view({})
    _settle(qtbot, page)


def test_borrar_actividad_de_linea_base_advierte_y_fuerza(h1_window, qtbot, monkeypatch):
    from ganttier.gui.pages import activities
    win, page = _page(h1_window, qtbot)
    client, pid = win.state.client, win.state.pid
    client.create_baseline(pid, "LB H1") if hasattr(client, "create_baseline") else client.post(f"/projects/{pid}/baselines", {"name": "LB H1"})
    aid = int(page.arrays.ids[-1])
    page.gantt.select_activity(aid) if hasattr(page.gantt, "select_activity") else setattr(page.gantt, "selected_id", aid)
    page.gantt.selected_id = aid
    texts = []
    monkeypatch.setattr(activities, "confirm", lambda parent, title, text, danger=False: texts.append(text) or True)
    page.delete_selected()
    _settle(qtbot, page)
    assert texts and "línea(s) base" in texts[0] and "BAC" in texts[0]
    assert aid not in {a["id"] for a in client.activities(pid, limit=5000)}


def test_preferencias_respetan_el_formato_por_defecto(qapp, tmp_path):
    """V1: QSettings("Ganttier","Ganttier") forzaba el registro de Windows e ignoraba setDefaultFormat."""
    from PySide6.QtCore import QSettings
    from ganttier.gui.state import app_settings
    previous = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    try:
        settings = app_settings()
        # Qt entrega rutas con «/» también en Windows: comparar como rutas, no como texto (V2).
        from pathlib import Path
        assert settings.format() == QSettings.IniFormat
        assert Path(settings.fileName()).resolve().is_relative_to(Path(tmp_path).resolve())
    finally:
        QSettings.setDefaultFormat(previous)
