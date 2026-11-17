from types import SimpleNamespace
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('pytestqt')


def state():
    from ganttier.gui.state import AppState
    result = AppState(SimpleNamespace(), settings={})
    result.project = dict(id=1, mode='proyecto', name='Prueba', start_date='2027-01-01', end_date='2027-02-10')
    return result


def test_selector_and_nominal_stale_response_guard(qtbot, qapp, monkeypatch, tmp_path):
    # The Windows offscreen platform does not discover installed fonts here.
    from PySide6.QtGui import QFontDatabase
    from matplotlib import get_data_path
    from pathlib import Path
    QFontDatabase.addApplicationFont(str(Path(get_data_path())/'fonts/ttf/DejaVuSans.ttf'))
    from ganttier.gui.theme import apply_theme
    apply_theme(qapp, 'light')
    from ganttier.gui.pages import optimize
    s = state()
    page = optimize.OptimizePage(s)
    qtbot.addWidget(page)
    page.resize(1200, 900)
    page.show()
    assert 'role_mode' not in page.options()
    page.role_options.mode.setCurrentIndex(1)
    page.role_options.horizon.setValue(60)
    page.role_options.target.setText('A27')
    assert page.options()['horizon_blocks'] == 60
    assert page.options()['target_code'] == 'A27'
    assert page.options()['allow_overtime'] is False
    assert page.options()['respect_resource_limits'] is True
    assert not page.objective_cards['Nivelar carga'].isEnabled()
    callbacks = []
    monkeypatch.setattr(optimize, 'run_task', lambda work, done, failed=None, **kw: callbacks.append(done))
    page.load_nominal()
    old = callbacks[-1]
    s.project = dict(s.project, id=2)
    page.load_nominal()
    old(dict(applied=True, valid=True, display_rows=[{'activity_code': 'OLD'}]))
    assert page.nominal_model.rowCount() == 0
    callbacks[-1](dict(applied=True, valid=True, run_id=9, display_rows=[dict(activity_code='A27', activity_name='Prueba', skill='I', resource_name='Elena', source_assignment_id=4)]))
    assert page.nominal_model.rowCount() == 1
    assert page.nominal_model.row_dict(0)['resource_name'] == 'Elena'
    qapp.processEvents()
    page.grab().save(str(tmp_path/'roles_gui.png'))
    page.load_nominal()
    callbacks[-1](dict(applied=True, valid=False, run_id=9))
    assert page.nominal_model.rowCount() == 0
    assert 'obsoleta' in page.nominal_status.text()
    page.role_options.set_project_mode('mantenimiento')
    assert 'role_mode' not in page.options()


def test_wizard_exposes_same_role_contract(qtbot):
    from ganttier.gui.wizard import TaskListWizard, task_fields
    s = state()
    wizard = TaskListWizard(s)
    qtbot.addWidget(wizard)
    page = wizard.objective_page
    page.role_options.mode.setCurrentIndex(1)
    page.role_options.hours.setValue(8)
    page.role_options.horizon.setValue(30)
    options = page.values()['options']
    assert options['role_mode'] == 'indivisible'
    assert options['block_hours'] == 8
    assert options['respect_resource_limits'] is True
    assert options['allow_overtime'] is False
    wizard.project_page.mode_combo.setCurrentIndex(1)
    page.initializePage()
    assert 'role_mode' not in page.values()['options']
    frequency = next(f for f in task_fields('mantenimiento') if f.key == 'frequency')
    assert ('DH', 'DH') in frequency.choices
