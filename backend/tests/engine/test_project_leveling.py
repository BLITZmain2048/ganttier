"""Regresiones del proyecto real; SQLite en memoria, sin API ni archivos de corrida."""
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from ganttier.domain import ObjectiveWeights, OptimizeOptions
from ganttier.engine import compute_cpm, optimize
from .conftest import activity
from .test_optimize import case


def demo_problem():
    from ganttier import db, repo, services
    from ganttier.excel_io.importer import parse_workbook
    path = Path(__file__).resolve().parents[3] / 'ejemplos/Ejemplo_Proyecto_SE_220kV.xlsx'
    bundle = parse_workbook(path, 'proyecto')
    bundle.project['end_date'] = None
    conn = db.connect(':memory:')
    try:
        db.initialize(conn)
        project = repo.create(conn, 'projects', {'name': 'Regresión demo', 'start_date': bundle.project['start_date']})
        # The workbook intentionally contains fictional progress in 2027.
        class DemoDate(date):
            @classmethod
            def today(cls):
                return date(2028, 1, 1)
        with patch.object(repo, 'date', DemoDate):
            repo.save_import_bundle(conn, project.id, bundle)
        services.sync_progress(conn, project.id)
        p = services.build_problem(conn, project.id)
    finally:
        conn.close()
    cpm = compute_cpm(p)
    for a in p.activities:
        a.planned_start = cpm.results[a.id].early_start
        a.planned_finish = cpm.results[a.id].early_finish
    return p


@pytest.mark.parametrize('limits', [True, False])
def test_real_demo_does_not_expand_for_leveling(limits):
    p = demo_problem()
    assert (len(p.activities), len(p.relations), len(p.resources)) == (120, 133, 18)
    cpm = compute_cpm(p)
    r = optimize(p, ObjectiveWeights(level=1, window=1, cost=.2),
                 OptimizeOptions(method='heuristic', respect_resource_limits=limits, level_period='week'))
    assert r.after is not None, r.warnings
    assert (r.after.finish - p.project.start_date).days <= 1.15 * (cpm.project_finish - p.project.start_date).days
    assert r.after.days_out_of_window <= r.before.days_out_of_window
    assert r.after.load_std < r.before.load_std


def test_leveling_uses_float_without_extending_project():
    p = case(8, capacity=64)
    p.activities.append(activity(9, 15))  # independent critical path, no demand
    p.horizon_end = date(2035, 1, 1)
    r = optimize(p, ObjectiveWeights(level=1, window=0), OptimizeOptions(method='heuristic'))
    assert r.after.finish <= compute_cpm(p).project_finish
    assert r.after.load_std < r.before.load_std
    assert sum(r.profile_after.series[0].load) == 64


def test_resource_limit_switch_allows_penalized_overload():
    p = case(2)
    for a in p.activities:
        a.window_start = a.window_end = p.project.start_date
    w = ObjectiveWeights(level=.001, makespan=1, window=10)
    hard = optimize(p, w, OptimizeOptions(method='heuristic', respect_resource_limits=True))
    soft = optimize(p, w, OptimizeOptions(method='heuristic', respect_resource_limits=False))
    assert hard.after.overload_hours == 0
    assert soft.after.overload_hours > 0
    assert hard.starts != soft.starts


def test_late_soft_window_keeps_room_for_successor():
    from ganttier.domain import Relation
    p = case(2, duration=2, hours=2, capacity=32)
    p.activities[0].window_start = date(2026, 1, 19)
    p.relations = [Relation(1, 1, 1, 2)]
    r = optimize(p, ObjectiveWeights(window=1), OptimizeOptions(method='heuristic'))
    assert r.after is not None
    assert r.after.days_out_of_window == 0
    assert r.starts[2] > r.finishes[1]


def test_tiny_demands_overlap_without_consuming_whole_resource():
    p = case(4, duration=180, hours=90)
    p.horizon_end = date(2028, 1, 1)
    r = optimize(p, ObjectiveWeights(level=1, window=0), OptimizeOptions(method='heuristic'))
    assert r.after is not None
    assert r.after.finish <= compute_cpm(p).project_finish
    assert sum(r.profile_after.series[0].load) == pytest.approx(360)
    assert r.after.overload_hours == 0


def test_real_demo_cpsat_is_formulated(monkeypatch):
    cp = pytest.importorskip('ortools.sat.python.cp_model')
    calls = []
    real_solve = cp.CpSolver.solve
    def checked_solve(solver, model, *args, **kwargs):
        assert not model.validate()
        calls.append(True)
        return real_solve(solver, model, *args, **kwargs)
    monkeypatch.setattr(cp.CpSolver, 'solve', checked_solve)
    r = optimize(demo_problem(), ObjectiveWeights(level=1, window=1, cost=.2),
                 OptimizeOptions(method='cpsat', time_limit_s=30, level_period='week'))
    assert r.after is not None, r.warnings
    assert calls, 'El modelo debe llegar al solver real, no solo producir un fallback.'
    assert not any('presupuesto' in w or 'rechazó' in w for w in r.warnings), r.warnings
    assert r.method_used == 'cpsat' or any('CP-SAT alcanzó' in w for w in r.warnings)


def prepared(p):
    from time import perf_counter
    from ganttier.engine.cpm import Model, _compute
    from ganttier.engine.loads import Loads
    from ganttier.engine.search_bounds import prepare
    m = Model(p)
    _, late, _ = _compute(m)
    loads = Loads(m, OptimizeOptions(), ObjectiveWeights(1, 0, 1, .2))
    s, f, _ = prepare(m, loads, late, perf_counter() + 10)
    return m, loads, s, f


def test_demo_cpsat_budget_uses_useful_domains():
    from ganttier.engine.cpsat import candidate_domains, MAX_CELLS
    m, loads, s, f = prepared(demo_problem())
    domains, weekly, estimate = candidate_domains(m, loads, s)
    assert not weekly
    assert estimate['cells'] + estimate['tables'] < MAX_CELLS
    assert estimate['choices'] < 15000
    assert all(int(s[i]) in days for i, days in enumerate(domains))
    assert max(m.finish(i, int(days[-1])) for i, days in enumerate(domains)) <= loads.search_finish


def test_weekly_domain_fallback_preserves_incumbent_and_calendars(monkeypatch):
    from ganttier.engine import cpsat
    m, loads, s, f = prepared(demo_problem())
    daily, _, original = cpsat.candidate_domains(m, loads, s)
    monkeypatch.setattr(cpsat, 'MAX_CELLS', 100000)
    domains, weekly, estimate = cpsat.candidate_domains(m, loads, s)
    assert weekly
    assert estimate['cells'] + estimate['tables'] < 100000
    assert estimate['cells'] < original['cells'] / 3
    for i, days in enumerate(domains):
        assert int(s[i]) in days
        assert set(days) <= set(daily[i])
        assert days[0] == daily[i][0] and days[-1] == daily[i][-1]


def test_padding_horizon_does_not_change_schedule_or_metrics():
    p = case(8, capacity=64)
    p.activities.append(activity(9, 15))
    w, opts = ObjectiveWeights(level=1, window=0), OptimizeOptions(method='heuristic')
    short = optimize(p, w, opts)
    p.horizon_end = date(2035, 1, 1)
    long = optimize(p, w, opts)
    assert long.starts == short.starts
    assert long.after.load_std == pytest.approx(short.after.load_std)
    assert long.after.objective_value == pytest.approx(short.after.objective_value)
    assert long.profile_after.labels == short.profile_after.labels


def test_extension_policy_is_configurable_and_validated(monkeypatch):
    monkeypatch.setenv('GANTTIER_LEVEL_EXTENSION_RATIO', '0.15')
    p = case(4, duration=180, hours=90)
    p.horizon_end = date(2035, 1, 1)
    m, loads, s, f = prepared(p)
    cpm_finish = compute_cpm(p).project_finish
    assert 0 < (m.dt(loads.search_finish) - cpm_finish).days <= .15 * ((cpm_finish - p.project.start_date).days + 1)
    monkeypatch.setenv('GANTTIER_LEVEL_EXTENSION_RATIO', 'nan')
    with pytest.raises(ValueError, match='EXTENSION_RATIO'):
        prepared(p)


def test_cpsat_budget_3000_uses_float_instead_of_global_horizon():
    from ganttier.engine.cpsat import candidate_domains, MAX_CELLS
    p = case(3000, capacity=2400)
    p.horizon_end = date(2035, 1, 1)
    m, loads, s, f = prepared(p)
    domains, weekly, estimate = candidate_domains(m, loads, s)
    assert len(domains) == 3000
    assert estimate['cells'] + estimate['tables'] < MAX_CELLS
    assert estimate['choices'] <= 30000
