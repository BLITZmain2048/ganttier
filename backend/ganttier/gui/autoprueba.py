"""Prueba explícita del mismo ejecutable distribuido, sin escritorio interactivo.

Uso: Ganttier.exe --ganttier-autoprueba CARPETA_NUEVA INVENTARIO_PDF
Nunca se ejecuta en el arranque habitual. Rechaza una carpeta ya existente y
redirige bases, datos, preferencias y temporales antes de importar la aplicación.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys
import time
import traceback


def ejecutar(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    output, pdf = (Path(value).resolve() for value in argv)
    if output.exists() or not pdf.is_file():
        return 2
    output.mkdir(parents=True)
    for key, folder in {'TEMP': 'temp', 'TMP': 'temp', 'APPDATA': 'appdata',
                        'LOCALAPPDATA': 'localappdata', 'MPLCONFIGDIR': 'matplotlib',
                        'GANTTIER_DATA_DIR': 'datos'}.items():
        target = output / folder
        target.mkdir(exist_ok=True)
        os.environ[key] = str(target)
    os.environ.update(GANTTIER_DB=str(output / 'datos/ganttier.db'),
                      QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1',
                      GANTTIER_PRECARGA='importar')
    for key in ('GANTTIER_API_URL', 'GANTTIER_API_TOKEN', 'GANTTIER_SIN_PRESENTACION'):
        os.environ.pop(key, None)
    from .launcher import configure_runtime
    configure_runtime()
    from PySide6.QtCore import QDate, QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(output / 'ajustes'))
    app = QApplication([])
    result = dict(frozen=bool(getattr(sys, 'frozen', False)), ejecutable=sys.executable,
                  pasos={}, ok=False)
    state = {'fase': 'arranque', 'ventana': None, 'pagina': None}
    started = time.perf_counter()

    def finish(error=None):
        if state['fase'] == 'cerrando':
            return
        state['fase'] = 'cerrando'
        timer.stop()
        if error is not None:
            result['error'] = str(error)
        else:
            result['ok'] = True
        win = state['ventana']
        if win is not None:
            win.prompt_on_close = False
            win.close()
        else:
            app.quit()

    def api_work(client):
        steps = {}
        health = client.get('/health')
        assert health['status'] == 'ok'
        steps['servidor'] = health
        calendar = client.post('/calendars', {'name': 'Calendario de autoprueba', 'holidays_country': None})
        project = client.create_project(dict(name='Autoprueba integrada', start_date='2026-10-12',
                                             data_date='2026-10-12', calendar_id=calendar['id']))
        pid = project['id']
        activities = client.create_activities(pid, [dict(code=f'INT-{i}', name=f'Actividad {i}',
            duration_days=1, work_hours=8, cost_budget=800) for i in range(3)])
        for before, after in zip(activities, activities[1:]):
            client.post(f'/projects/{pid}/relations', dict(pred_id=before['id'], succ_id=after['id']))
        solved = client.post(f'/projects/{pid}/schedule/solve', {})
        assert solved['feasible'] and solved.get('applied') is not False
        assert all(row['planned_start'] for row in client.activities(pid))
        steps['programa_api'] = solved
        excel = output / 'proyecto.xlsx'
        excel.write_bytes(client.export_xlsx(pid))
        from openpyxl import load_workbook
        book = load_workbook(excel, read_only=True)
        try:
            assert len(book.sheetnames) > 1
            steps['excel'] = dict(ruta=str(excel), bytes=excel.stat().st_size, hojas=book.sheetnames)
        finally:
            book.close()
        from ganttier.importadores import detectar, previsualizar
        detected = detectar(pdf)
        preview = previsualizar(pdf, supuestos={'strings_por_SCB': 24})
        assert detected[0]['formato'] == 'inventario_equipos'
        inventory = preview.modelo['inventario']
        assert inventory
        steps['importador_pdf'] = dict(formato=detected[0]['formato'], filas=len(inventory))
        return pid, steps

    def api_done(payload):
        try:
            pid, steps = payload
            result['pasos'].update(steps)
            win = state['ventana']
            state['pid'] = pid
            win.state.open_project(pid)
            win.go('informes')
            state['pagina'] = win.page('informes')
            state['fase'] = 'preparar_informes'
        except Exception:
            finish(traceback.format_exc())

    def poll():
        try:
            if time.perf_counter() - started > 150:
                finish('La autoprueba superó 150 segundos.')
                return
            if state['fase'] == 'arranque':
                from .app import main_window
                win = main_window()
                if win is None or not win.isVisible():
                    return
                state['ventana'] = win
                state['fase'] = 'api'
                result['arranque_s'] = time.perf_counter() - started
                result['pesados_al_arrancar'] = [name for name in
                    ('openpyxl', 'matplotlib', 'reportlab', 'ortools', 'pandas', 'scipy') if name in sys.modules]
                assert not result['pesados_al_arrancar']
                from .widgets.common import run_task
                run_task(lambda: api_work(win.state.client), api_done, finish)
            elif state['fase'] == 'preparar_informes':
                page = state['pagina']
                if (state['ventana'].state.pid != state['pid'] or page._pid != state['pid']
                        or page.loading or page.working):
                    return
                destination = output / 'Destino elegido con acentos á'
                destination.mkdir()
                page.download_directory = destination
                page.period.setCurrentIndex(page.period.findData('weekly'))
                page.reference.setDate(QDate(2026, 10, 12))
                for name, check in page.formats.items():
                    check.setChecked(name in ('pdf', 'xlsx'))
                state['fase'] = 'informes'
                page.generate()
            elif state['fase'] == 'informes':
                page = state['pagina']
                if page.working:
                    return
                assert {f['format'] for f in page.files} == {'pdf', 'xlsx'}, page.saved_label.text()
                from PySide6.QtPdf import QPdfDocument
                from openpyxl import load_workbook
                files = []
                for item in page.files:
                    path = Path(item['local_path'])
                    assert path.parent == page.download_directory and path.stat().st_size > 0
                    details = dict(ruta=str(path), formato=item['format'], bytes=path.stat().st_size)
                    if item['format'] == 'pdf':
                        document = QPdfDocument()
                        assert document.load(str(path)) == QPdfDocument.Error.None_
                        details['paginas'] = document.pageCount()
                        assert details['paginas'] > 0
                        document.close()
                    else:
                        book = load_workbook(path, read_only=True)
                        details['hojas'] = book.sheetnames
                        book.close()
                    files.append(details)
                result['pasos']['informes_destino_gui'] = files
                finish()
        except Exception:
            finish(traceback.format_exc())

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    code = 1
    try:
        runpy.run_module('ganttier.gui', run_name='__main__')
    except SystemExit as exc:
        code = exc.code or 0
    except BaseException:
        result['error'] = traceback.format_exc()
        result['ok'] = False
    finally:
        win = state['ventana']
        server = getattr(win, '_server', None)
        result['cierre'] = dict(codigo_gui=code, lock_liberado=not (output / 'datos/ganttier.db.lock').exists(),
                               servidor_detenido=server is not None and not server._thread.is_alive())
        result['ok'] = result['ok'] and code == 0 and all(result['cierre'][key] for key in ('lock_liberado', 'servidor_detenido'))
        (output / 'resultado.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return 0 if result['ok'] else 1
