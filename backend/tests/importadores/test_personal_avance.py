from datetime import date
from pathlib import Path
import pytest
from openpyxl import Workbook, load_workbook

from ganttier import db, repo, services
from ganttier.excel_io.personal_avance import import_workbook, build_template


@pytest.fixture
def setup(tmp_path):
    conn = db.connect(tmp_path / 'test.db')
    db.initialize(conn)
    cal = repo.create(conn, 'calendars', dict(name='Prueba', holidays_country=None))
    project = repo.create(conn, 'projects', dict(name='I4', start_date=date(2027, 1, 4), data_date=date(2027, 1, 31), calendar_id=cal.id))
    worker = repo.create(conn, 'resources', dict(name='Ana', project_id=project.id, calendar_id=cal.id))
    activity = repo.create(conn, 'activities', dict(project_id=project.id, code='A1', name='Prueba', work_hours=16,
        planned_start=date(2027, 1, 4), planned_finish=date(2027, 1, 5), calendar_id=cal.id))
    conn.commit()
    yield conn, project.id, worker.id, activity.id
    conn.close()


def book(tmp_path, sheets):
    wb = Workbook(write_only=True)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    path = tmp_path / 'input.xlsx'
    wb.save(path)
    wb.close()
    return path


def test_progress_equivalence_idempotence_performance(setup, tmp_path):
    from ganttier.api.control import create_progress
    from ganttier.api.schemas import ProgressEntry
    conn, pid, rid, aid = setup
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje', 'HH_Reales', 'Trabajador'], ['A1', '05/01/2027', 50, '8,5', 'Ana']]})
    preview = import_workbook(conn, pid, path, 'avance')
    assert not preview['errors'], preview
    assert preview['created'] == 1 and not repo.list_entities(conn, 'progress', pid)
    result = import_workbook(conn, pid, path, 'avance', dry_run=False)
    assert result['applied'] and result['backup']
    imported = repo.list_entities(conn, 'progress', pid)[0]
    assert repo.get(conn, 'activities', aid).actual_hours == 8.5
    kpis = services.control_result(conn, pid, 'performance', start=date(2027, 1, 1), end=date(2027, 1, 31))
    assert kpis[0].actual_hours == 8.5
    again = import_workbook(conn, pid, path, 'avance', dry_run=False)
    assert again['ignored'] == 1 and len(repo.list_entities(conn, 'progress', pid)) == 1
    repo.delete(conn, 'progress', imported.id)
    created = create_progress(pid, ProgressEntry(activity_id=aid, date=date(2027, 1, 5), percent_complete=50, actual_hours=8.5, resource_id=rid), conn)
    assert {k: v for k, v in vars(imported).items() if k != 'id'} == {k: v for k, v in vars(created).items() if k != 'id'}


def test_invalid_atomic_and_cutoff(setup, tmp_path):
    conn, pid, _, _ = setup
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje'], ['A1', '2027-01-05', 50], ['A1', '2027-02-01', 100]]})
    result = import_workbook(conn, pid, path, 'avance', dry_run=False)
    assert result['errors'][0]['row'] == 3
    assert result['errors'][0]['column'] == 'Fecha'
    assert not result['backup'] and not repo.list_entities(conn, 'progress', pid)


def test_personal_absences_replace(setup, tmp_path):
    conn, pid, rid, _ = setup
    path = book(tmp_path, {
        'Personal': [['Nombre', 'Rol', 'Turno', 'Ancla', 'Horas_Dia', 'Calendario'], ['Beto', 'eléctrico', '7x7', '2027-01-04', 8, 'Propio']],
        'Calendarios': [['Calendario', 'Fecha', 'Motivo'], ['Propio', '2027-01-06', 'Local']],
        'Ausencias': [['Persona', 'Desde', 'Hasta', 'Tipo', 'Horas_Disponibles'], ['Beto', '2027-01-05', '2027-01-05', 'permiso', 4]]})
    result = import_workbook(conn, pid, path, 'personal', modo='replace')
    assert not result['errors'], result
    assert len(repo.list_entities(conn, 'resources', pid)) == 1
    result = import_workbook(conn, pid, path, 'personal', dry_run=False, modo='replace')
    assert not result['errors'], result
    assert result['deactivated'] == 1
    assert not repo.get(conn, 'resources', rid).active
    beto = next(r for r in repo.list_entities(conn, 'resources', pid) if r.name == 'Beto')
    cal = repo.get(conn, 'calendars', beto.calendar_id)
    assert beto.skills == ['eléctrico'] and cal.shift_pattern == '7x7'
    assert {e.date: e.hours for e in cal.exceptions} == {date(2027, 1, 5): 4, date(2027, 1, 6): 0}


@pytest.mark.parametrize('kind', ['personal', 'avance'])
def test_templates_http(kind, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ganttier.api import router
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get('/templates/' + kind)
    assert response.status_code == 200
    path = tmp_path / (kind + '.xlsx')
    path.write_bytes(response.content)
    wb = load_workbook(path)
    assert 'Instrucciones' in wb.sheetnames
    assert wb['Personal' if kind == 'personal' else 'Avance']['A1'].comment
    wb.close()


def test_worker_sheet_and_regression(setup, tmp_path):
    conn, pid, rid, aid = setup
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje'], ['A1', '2027-01-05', 50]],
        'Avance_Trabajador': [['Actividad', 'Fecha', 'Trabajador', 'HH_Reales'], ['A1', '2027-01-05', 'Ana', 8]]})
    result = import_workbook(conn, pid, path, 'avance', dry_run=False)
    assert not result['errors'], result
    assert repo.get(conn, 'activities', aid).actual_hours == 8
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje'], ['A1', '2027-01-06', 20]]})
    assert import_workbook(conn, pid, path, 'avance')['errors']
    assert import_workbook(conn, pid, path, 'avance', allow_regressions=True)['warnings']


def test_formula_location_and_rollback(setup, tmp_path):
    conn, pid, _, _ = setup
    path = book(tmp_path, {'Personal': [['Nombre', 'Horas_Dia'], ['Beto', 8], ['Error', '=1+1']]})
    result = import_workbook(conn, pid, path, 'personal', dry_run=False)
    assert result['errors'][0]['row'] == 3 and result['errors'][0]['column'] == 'Horas_Dia'
    assert len(repo.list_entities(conn, 'resources', pid)) == 1


def test_100000_dry_run(setup, tmp_path):
    conn, pid, _, aid = setup
    # 100 000 claves válidas: trabajadores distintos, misma actividad y fecha.
    repo.create_many(conn, 'resources', [dict(name=f'T{i}', project_id=pid) for i in range(100000)])
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje', 'Trabajador', 'HH_Reales']] +
        [['A1', '2027-01-05', 50, f'T{i}', 1] for i in range(100000)]})
    result = import_workbook(conn, pid, path, 'avance')
    print(f'I4 dry_run 100000: {result["elapsed_s"]:.3f} s')
    assert not result['errors'], result['errors'][:3]
    assert result['created'] == 100000
    assert result['elapsed_s'] <= 10


def test_http_default_preview(setup, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ganttier.api import router
    from ganttier.api.dependencies import connection
    conn, pid, _, _ = setup
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[connection] = lambda: conn
    path = book(tmp_path, {'Personal': [['Nombre'], ['Beto']]})
    response = TestClient(app).post(f'/projects/{pid}/import/personal', files={'file': ('personal.xlsx', path.read_bytes())})
    assert response.status_code == 200
    assert response.json()['dry_run'] and not response.json()['applied']
    assert len(repo.list_entities(conn, 'resources', pid)) == 1


def test_transaction_failure_restores_progress(setup, tmp_path, monkeypatch):
    conn, pid, _, _ = setup
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje'], ['A1', '2027-01-05', 50]]})
    import_workbook(conn, pid, path, 'avance', dry_run=False)
    original = repo.json_value(repo.list_entities(conn, 'progress', pid))
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje'], ['A1', '2027-01-05', 60]]})
    def fail(*args, **kwargs):
        raise RuntimeError('Fallo inyectado después de escribir')
    monkeypatch.setattr(services, 'sync_progress', fail)
    with pytest.raises(RuntimeError):
        import_workbook(conn, pid, path, 'avance', dry_run=False)
    assert repo.json_value(repo.list_entities(conn, 'progress', pid)) == original


def test_period_idempotence_and_duplicate_hours(setup, tmp_path):
    conn, pid, _, aid = setup
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje', 'Modo_Porcentaje'],
        ['A1', '2027-01-05', 20, 'periodo'], ['A1', '2027-01-06', 30, 'periodo']]})
    assert import_workbook(conn, pid, path, 'avance', dry_run=False)['applied']
    assert repo.get(conn, 'activities', aid).percent_complete == 50
    assert import_workbook(conn, pid, path, 'avance')['ignored'] == 2
    path = book(tmp_path, {'Avance': [['Actividad', 'Fecha', 'Porcentaje', 'HH_Reales'], ['A1', '2027-01-07', 60, 8]],
        'Avance_Trabajador': [['Actividad', 'Fecha', 'Trabajador', 'HH_Reales'], ['A1', '2027-01-07', 'Ana', 8]]})
    assert import_workbook(conn, pid, path, 'avance')['errors']


def test_team_and_custom_shift(setup, tmp_path):
    conn, pid, _, _ = setup
    repo.update(conn, 'projects', pid, dict(mode='mantenimiento', end_date=date(2027, 12, 31)))
    path = book(tmp_path, {'Personal': [['Nombre', 'Turno', 'Patron', 'Ancla'], ['Beto', 'personalizado', '4x3', '2027-01-04']],
        'Equipos': [['Equipo', 'Regimen', 'Inicio_Ciclo', 'Integrantes', 'Horas_Dia'], ['Cuadrilla', '14x14', '2027-01-04', 3, 8]]})
    result = import_workbook(conn, pid, path, 'personal', dry_run=False)
    assert not result['errors'], result
    assert result['created'] == 2
