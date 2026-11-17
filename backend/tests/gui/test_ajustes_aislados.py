"""El arranque nativo de certificación conserva las preferencias en su carpeta."""
def test_preferencias_aisladas(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from ganttier.gui.state import app_settings
    monkeypatch.setenv('GANTTIER_SETTINGS_DIR',str(tmp_path/'ajustes'))
    settings=app_settings()
    settings.setValue('last_page','gantt');settings.sync()
    assert settings.status()==QSettings.NoError
    assert (tmp_path/'ajustes/Ganttier.ini').is_file()
    assert app_settings().value('last_page')=='gantt'
