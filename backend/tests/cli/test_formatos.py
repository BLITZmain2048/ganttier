"""Pruebas CLI con libros mínimos y SQLite aislado por tmp_path."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from openpyxl import Workbook

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def sandbox(tmp_path):
    env = dict(os.environ, GANTTIER_DB=str(tmp_path / 'test.db'),
               GANTTIER_DATA_DIR=str(tmp_path / 'data'), GANTTIER_CLI_ESCRITURA='0',
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               TEMP=str(tmp_path), TMP=str(tmp_path), TMPDIR=str(tmp_path))
    seed = '''
import os
from ganttier import db, repo
conn = db.connect(os.environ['GANTTIER_DB'])
db.initialize(conn)
c = repo.create(conn, 'calendars', dict(name='Continuo', workweek='1111111', hours_per_day=8, holidays_country=None))
p = repo.create(conn, 'projects', dict(name='G3', start_date='2027-01-01', data_date='2027-01-31', calendar_id=c.id))
repo.create(conn, 'activities', dict(project_id=p.id, code='A1', name='Prueba', duration_days=2, work_hours=16, calendar_id=c.id))
repo.create(conn, 'resources', dict(project_id=p.id, name='Existente', kind='trabajador', calendar_id=c.id))
conn.commit()
conn.close()
'''
    proc = subprocess.run([sys.executable, '-B', '-c', seed], cwd=BACKEND, env=env, capture_output=True, text=True, encoding='utf-8')
    assert proc.returncode == 0, proc.stderr
    return tmp_path, env


def run(sandbox, *args, code=0):
    root, env = sandbox
    proc = subprocess.run([sys.executable, '-B', '-m', 'ganttier.cli', *map(str, args)],
                          cwd=BACKEND, env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    body = json.loads(proc.stdout)
    assert proc.returncode == code, (proc.stderr, body)
    assert {'ok', 'command', 'cli_version', 'ganttier', 'db', 'data', 'warnings', 'error', 'elapsed_s'} <= body.keys()
    return body


def snapshot(sandbox):
    path = Path(sandbox[1]['GANTTIER_DB'])
    with sqlite3.connect(path) as conn:
        counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('resources', 'progress', 'calendars', 'activities')}
        logical = '\n'.join(conn.iterdump())
    return hashlib.sha256(path.read_bytes()).hexdigest(), counts, hashlib.sha256(logical.encode()).hexdigest()


def workbook(root, kind, invalid=False):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Personal' if kind == 'personal' else 'Avance'
    if kind == 'personal':
        sheet.append(['Nombre', 'Horas_Dia'])
        sheet.append(['Nueva', 8])
        if invalid:
            sheet.append(['Error', -1])
    else:
        sheet.append(['Actividad', 'Fecha', 'Porcentaje', 'HH_Reales'])
        sheet.append(['A1', '2027-01-02', 25, 4])
        if invalid:
            sheet.append(['A1', '2027-01-03', 150, 4])
    path = root / f'{kind}.xlsx'
    book.save(path)
    book.close()
    return path


def test_listar_y_describir(sandbox):
    body = run(sandbox, 'formatos', 'listar')
    rows = {r['format']: r for r in body['data']['formats']}
    for name in ('msp_excel', 'p6_excel', 'carta_gantt', 'pauta_equipos', 'personal', 'avance'):
        assert rows[name]['description'] and '.xlsx' in rows[name]['extensions']
        assert rows[name]['templates'] and rows[name]['examples']
        for relative in rows[name]['templates'] + rows[name]['examples']:
            assert (Path(body['data']['asset_root']) / relative).is_file()
    for command in ('formatos listar', 'formatos plantilla', 'importar personal', 'importar avance'):
        description = run(sandbox, 'describir', '--comando', command)
        assert description['ok'] and command in json.dumps(description['data'])


@pytest.mark.parametrize('kind', ['personal', 'avance', 'msp_excel', 'p6_excel', 'carta_gantt', 'pauta_equipos'])
def test_plantilla_no_sobrescribe(sandbox, kind):
    path = sandbox[0] / 'copia.xlsx'
    body = run(sandbox, 'formatos', 'plantilla', kind, '--destino', path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert body['data']['sha256'] == digest
    assert path.read_bytes() == Path(body['data']['source']).read_bytes()
    assert not run(sandbox, 'formatos', 'plantilla', kind, '--destino', path, code=1)['ok']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('kind', ['personal', 'avance'])
def test_simular_y_escribir(sandbox, kind):
    path = workbook(sandbox[0], kind)
    before = snapshot(sandbox)
    simulated = run(sandbox, '-p', 'G3', 'importar', kind, path)
    assert simulated['db']['mode'] == 'simulated'
    assert simulated['data']['created'] == 1
    assert simulated['data']['dry_run'] and not simulated['data']['applied']
    assert simulated['data']['backup'] is None
    assert snapshot(sandbox) == before
    applied = run(sandbox, '-p', 'G3', '--escribir', 'importar', kind, path)
    assert applied['db']['mode'] == 'write' and Path(applied['db']['backup']).exists()
    assert applied['data']['applied'] and not applied['data']['errors']
    after = snapshot(sandbox)
    table = 'resources' if kind == 'personal' else 'progress'
    assert after[1][table] == before[1][table] + 1
    assert after[2] != before[2]
    repeated = run(sandbox, '-p', 'G3', '--escribir', 'importar', kind, path)
    assert repeated['data']['created'] == 0
    assert snapshot(sandbox)[1] == after[1]


@pytest.mark.parametrize('kind', ['personal', 'avance'])
@pytest.mark.parametrize('write', [False, True])
def test_errores_fila_atomicos(sandbox, kind, write):
    path = workbook(sandbox[0], kind, invalid=True)
    before = snapshot(sandbox)
    flags = ['--escribir'] if write else []
    result = run(sandbox, '-p', 'G3', *flags, 'importar', kind, path, code=2)
    assert result['ok'] and result['error'] is None
    assert not result['data']['applied']
    issue = result['data']['errors'][0]
    assert issue['row'] == 3 and issue['sheet'] == ('Personal' if kind == 'personal' else 'Avance')
    assert issue['column'] and issue['message']
    assert snapshot(sandbox) == before


def test_replace_y_proyecto_obligatorio(sandbox):
    path = workbook(sandbox[0], 'personal')
    before = snapshot(sandbox)
    assert not run(sandbox, 'importar', 'personal', path, code=1)['ok']
    assert not run(sandbox, '-p', 'Ausente', 'importar', 'personal', path, code=1)['ok']
    run(sandbox, '-p', 'G3', 'importar', 'personal', path, '--modo', 'replace')
    assert snapshot(sandbox) == before
    result = run(sandbox, '-p', 'G3', '--escribir', 'importar', 'personal', path, '--modo', 'replace')
    assert result['data']['deactivated'] == 1
    with sqlite3.connect(sandbox[1]['GANTTIER_DB']) as conn:
        assert conn.execute("SELECT active FROM resources WHERE name='Existente'").fetchone()[0] == 0

@pytest.mark.parametrize('failure', ['after_import', 'backup'])
def test_context_rollback_y_respaldo_obligatorio(sandbox, failure):
    path = workbook(sandbox[0], 'personal')
    before = snapshot(sandbox)
    script = '''
import os, sys
from argparse import Namespace
from pathlib import Path
from ganttier.cli._base import Context
from ganttier.cli.comandos._formatos import import_extra_handler
from ganttier.excel_io import personal_avance
from ganttier.optimizacion import espacio
original = personal_avance.import_workbook

def fail_after(*args, **kwargs):
    result = original(*args, **kwargs)
    assert result['applied']
    assert args[0].in_transaction, 'La confirmación debe pertenecer al Context'
    raise RuntimeError('Fallo inyectado después de aplicar')

def fail_backup(*args, **kwargs):
    raise RuntimeError('Fallo inyectado de respaldo')

if sys.argv[2] == 'after_import':
    personal_avance.import_workbook = fail_after
else:
    espacio.respaldar = fail_backup
ctx = Context(Path(os.environ['GANTTIER_DB']), Path(os.environ['GANTTIER_DATA_DIR']), project='G3', write=True)
try:
    import_extra_handler(Namespace(source=sys.argv[1], kind='personal', modo='merge'), ctx)
except RuntimeError as exc:
    assert 'Fallo inyectado' in str(exc)
else:
    raise AssertionError('Debió propagarse el fallo')
'''
    proc = subprocess.run([sys.executable, '-B', '-c', script, str(path), failure], cwd=BACKEND,
                          env=sandbox[1], capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert proc.returncode == 0, proc.stderr
    # SQLite puede cambiar su cabecera tras BEGIN/ROLLBACK; filas y conteos deben ser idénticos.
    assert snapshot(sandbox)[1:] == before[1:]
