"""Contrato del diálogo y descarga del Gantt."""
import httpx


def test_dialog_options_and_validation(qtbot):
    from ganttier.gui.gantt_export import GanttExportDialog
    dialog = GanttExportDialog()
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == 'Exportar Gantt a Excel…'
    dialog.group.setCurrentIndex(dialog.group.findData('sitio/wbs'))
    dialog.level.setValue(2)
    dialog.first.setText('05-10-2026')
    dialog.last.setText('31-10-2026')
    assert dialog.parameters()['group_by'] == 'sitio/wbs:2'
    assert dialog.parameters()['from'] == '2026-10-05'
    dialog.mode.setCurrentIndex(1)
    assert not dialog.group.isEnabled() and not dialog.level.isEnabled()
    dialog.scale.setCurrentIndex(dialog.scale.findData('month'))
    assert dialog.parameters()['scale'] == 'month'
    dialog.last.setText('01-10-2026')
    dialog.accept()
    assert 'posterior' in dialog.error.text()
    assert dialog.result() == 0


def test_client_download_parameters():
    from ganttier.gui.client import ApiClient
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b'xlsx')
    client = ApiClient('http://testserver', transport=httpx.MockTransport(handler))
    assert client.gantt_xlsx(7, mode='trabajador', scale='month', **{'from':'2026-10-05'}) == b'xlsx'
    assert seen[0].url.path == '/api/projects/7/gantt.xlsx'
    assert seen[0].url.params['from'] == '2026-10-05'
    client.close()


def test_palette_contains_export(monkeypatch):
    from types import SimpleNamespace
    from ganttier.gui.app import MainWindow
    from ganttier.gui.widgets import palette
    captured = []
    class FakePalette:
        def __init__(self, commands, *args, **kwargs): captured.extend(commands)
        def open(self): pass
    monkeypatch.setattr(palette, 'CommandPalette', FakePalette)
    window = SimpleNamespace(state=SimpleNamespace(mode='proyecto', project=None, recent_projects=lambda: []),
        stack=SimpleNamespace(currentWidget=lambda: None), _project_rows=[], theme_name='light',
        go=lambda *a: None, new_project=lambda: None, task_list_wizard=lambda: None,
        import_assisted=lambda: None,
        export_gantt_excel=lambda: None, toggle_theme=lambda: None, show_shortcuts=lambda: None,
        show_backups=lambda: None, global_search=lambda: None)
    MainWindow.command_palette(window)
    assert any(row[0] == 'Exportar Gantt a Excel…' for row in captured)
    assert any(row[0] == 'Importar archivo de cliente con asistente' for row in captured)
