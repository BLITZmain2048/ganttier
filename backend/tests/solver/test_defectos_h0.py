"""Regresiones de los defectos D1 y D2 y de los estados de solución (P1-06a)."""
from datetime import date

import pytest

from ganttier import db, repo, services
from ganttier.domain import OptimizeOptions, ObjectiveWeights
from ganttier.solve import solve_project


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('GANTTIER_DB', str(tmp_path / 'h0.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(services, 'DATA_DIR', tmp_path)
    conn = db.connect()
    db.initialize(conn)
    yield conn
    conn.close()


def _project(conn, mode='proyecto'):
    cid = repo.create(conn, 'calendars', {'name': 'Sin feriados H0', 'holidays_country': None}).id
    return repo.create(conn, 'projects', dict(name='H0', mode=mode, start_date=date(2026, 1, 5), data_date=date(2026, 1, 5),
                                              end_date=date(2026, 6, 30) if mode == 'mantenimiento' else None, calendar_id=cid))


def test_d1_cpsat_respeta_el_limite_pedido(case, monkeypatch):
    """Antes CP-SAT recibía como máximo 19 s aunque el usuario pidiera más."""
    import importlib
    from ganttier.engine import cpsat
    module = importlib.import_module("ganttier.engine.optimize")
    from time import perf_counter
    seen = {}

    def fake_solve(m, loads, starts, finishes, deadline):
        seen['budget'] = deadline - perf_counter()
        return None
    from ganttier.engine_r3 import cpsat as cpsat_r3  # ruta R3 (<=500 actividades y >=20 s)
    monkeypatch.setattr(cpsat, 'solve', fake_solve)
    monkeypatch.setattr(cpsat_r3, 'solve', fake_solve)
    module.optimize(case(3), ObjectiveWeights(makespan=1), OptimizeOptions(method='cpsat', time_limit_s=40))
    assert seen['budget'] > 30, seen  # 40 s menos el margen de auditoría (≤ 1 s) y la fase heurística


def test_d2_simular_mantenimiento_no_modifica_la_base(database):
    p = _project(database, 'mantenimiento')
    repo.create(database, 'resources', dict(project_id=p.id, name='Ana', skills=['electrico'], calendar_id=p.calendar_id))
    repo.create(database, 'periodic_tasks', dict(project_id=p.id, code='M', name='Mensual', frequency='M', first_due=p.start_date,
                                                 work_hours=8, required_skill='electrico', calendar_id=p.calendar_id))
    counts = lambda: tuple(database.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                           for t in ('activities', 'assignments', 'schedule_runs'))
    before = counts()
    result = solve_project(database, p.id, options={'method': 'heuristic', 'time_limit_s': 1}, apply=False)
    assert result['feasible'] and result['simulated'] and result['run_id'] is None
    assert counts() == before
    assert not list((services.DATA_DIR / 'runs').glob('*.json'))
    # Aplicar sí deja el inventario y la corrida.
    applied = solve_project(database, p.id, options={'method': 'heuristic', 'time_limit_s': 1}, apply=True)
    assert applied['applied'] and counts()[0] == 6 and counts()[2] == 1
    # Simular sin cambios de inventario conserva la corrida (puede aplicarse después).
    again = solve_project(database, p.id, options={'method': 'heuristic', 'time_limit_s': 1}, apply=False)
    assert again['run_id'] is not None and not again.get('simulated')


def test_p1_06a_distingue_infactibilidad_demostrada(database):
    p = _project(database)
    repo.create(database, 'resources', dict(project_id=p.id, name='Ana', skills=['prot'], capacity_hours_day=8, calendar_id=p.calendar_id))
    repo.create(database, 'activities', dict(project_id=p.id, code='X', name='Imposible', duration_days=1, work_hours=24, required_skill='prot'))
    result = solve_project(database, p.id, options={'method': 'heuristic', 'time_limit_s': 1})
    assert not result['feasible'] and result['infeasibility'] == 'demostrada'
    ok = solve_project(database, p.id, options={'method': 'heuristic', 'time_limit_s': 1}, apply=False)
    assert ok['infeasibility'] == 'demostrada'
    repo.update(database, 'activities', repo.list_entities(database, 'activities', p.id)[0].id, {'duration_days': 3})
    fine = solve_project(database, p.id, options={'method': 'heuristic', 'time_limit_s': 1})
    assert fine['feasible'] and fine['infeasibility'] is None
