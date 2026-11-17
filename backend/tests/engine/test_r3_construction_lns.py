"""S2: capacidad exacta, presupuestos, diagnóstico y mejoras coordinadas reales."""
from datetime import date, timedelta
from time import perf_counter

import numpy as np
import pytest

from ganttier.domain import ObjectiveWeights, OptimizeOptions, Relation, Resource
from ganttier.engine_r3 import optimize, cpsat
from ganttier.engine_r3.construction import first_position
from ganttier.engine_r3.cpm import Model, _compute
from ganttier.engine_r3.loads import Loads
from ganttier.engine_r3.lns import improve, neighborhood_plan
from ganttier.engine_r3.verify import verify_with_options
from .conftest import FakeCalendar, activity, problem
from .test_optimize import case


def prepared(p, weights=None):
    m = Model(p)
    _compute(m)
    loads = Loads(m, OptimizeOptions(allow_overtime=False, seed=17),
                  weights or ObjectiveWeights(makespan=1, window=0))
    return m, loads


@pytest.mark.parametrize('variable', [False, True])
@pytest.mark.parametrize('duration', [1, 3, 12])
def test_first_position_matches_full_exact_scan(variable, duration):
    p = case(3, duration=duration, hours=duration * 6, capacity=8)
    p.horizon_end = date(2026, 4, 30)
    if variable:
        p.calendars[1] = FakeCalendar(exceptions={date(2026, 1, 7): 4., date(2026, 1, 12): 3.})
    m, loads = prepared(p)
    loads.change(0, int(m.starts[0]), 1)
    cal = m.cals[1]
    days = cal.days[cal.rank(int(m.lower[1])):cal.rank(int(m.upper[1]) + 1)]
    scores, finishes = loads.candidates(1, days, loads.begin)
    valid = np.flatnonzero(np.isfinite(scores))
    found = first_position(m, loads, 1, int(m.lower[1]), int(m.upper[1]), perf_counter() + 5)
    assert found == (int(days[valid[0]]), int(finishes[valid[0]]))


def test_first_position_dynamic_skill_shares():
    p = problem([activity(1, 2, required_skill='civil', work_hours=12),
                 activity(2, 2, required_skill='civil', work_hours=12)],
                resources=[Resource(1, 'A', skills=['civil'], calendar_id=1),
                           Resource(2, 'B', skills=['civil'], calendar_id=2)],
                calendars={1: FakeCalendar(), 2: FakeCalendar(weekdays=[0, 2, 4])})
    m, loads = prepared(p)
    assert loads.dynamic_shares
    loads.change(0, int(m.starts[0]), 1)
    cal = m.cals[1]
    days = cal.days[cal.rank(int(m.lower[1])):cal.rank(int(m.upper[1]) + 1)]
    scores, finishes = loads.candidates(1, days, loads.begin)
    k = int(np.flatnonzero(np.isfinite(scores))[0])
    assert first_position(m, loads, 1, int(m.lower[1]), int(m.upper[1]), perf_counter() + 3) == (int(days[k]), int(finishes[k]))


def test_expired_construction_and_lns_leave_incumbent_untouched(monkeypatch):
    m, loads = prepared(case(2, capacity=16))
    s, f = m.starts.copy(), m.finishes.copy()
    deadline = perf_counter() - 1
    assert first_position(m, loads, 0, int(m.lower[0]), int(m.upper[0]), deadline) is None
    monkeypatch.setattr(cpsat, 'solve', lambda *a, **kw: pytest.fail('Solver después del plazo'))
    actual_s, actual_f, _ = improve(m, loads, s, f, deadline)
    np.testing.assert_array_equal(actual_s, s)
    np.testing.assert_array_equal(actual_f, f)


def test_hard_temporal_contradiction_is_proved_but_search_failure_is_not():
    p = case(1, duration=3)
    p.activities[0].window_hard = True
    p.activities[0].window_end = p.horizon_start
    result = optimize(p, ObjectiveWeights(makespan=1), OptimizeOptions(method='heuristic', time_limit_s=.2))
    assert result.method_used == 'precheck'
    assert result.status == 'infactible' and not result.starts
    assert any('ventana' in w for w in result.warnings)
    p = case(1, hours=20, capacity=8)
    result = optimize(p, ObjectiveWeights(makespan=1), OptimizeOptions(method='heuristic', time_limit_s=.2))
    assert result.method_used != 'precheck'
    assert result.status == 'infactible' and not result.starts
    assert any('no demuestra infactibilidad' in w for w in result.warnings)


def test_soft_deadline_never_becomes_proof_of_impossibility():
    p = case(1, duration=3)
    p.activities[0].constraint_type = 'FNLT'
    p.activities[0].constraint_date = p.horizon_start
    result = optimize(p, ObjectiveWeights(makespan=1), OptimizeOptions(method='heuristic', time_limit_s=.5))
    assert result.status != 'infactible'
    assert verify_with_options(p, result.starts, result.finishes, OptimizeOptions()).feasible


def test_temporal_precheck_identifies_mfo_even_when_model_fixed_its_finish():
    p = case(1, duration=10)
    p.activities[0].constraint_type = 'MFO'
    p.activities[0].constraint_date = p.horizon_start
    result = optimize(p, ObjectiveWeights(makespan=1), OptimizeOptions(time_limit_s=.5))
    assert result.method_used == 'precheck' and not result.starts
    assert any('MFO' in w and p.horizon_start.isoformat() in w for w in result.warnings)


def swap_case():
    p = problem([activity(1, 2, work_hours=16, required_skill='civil'),
                 activity(2, 1, work_hours=8, required_skill='civil'), activity(3, 5),
                 activity(4, 1, locked=True, planned_start=date(2026, 1, 15))],
                relations=[Relation(1, 1, 2, 3)],
                resources=[Resource(1, 'Civil', skills=['civil'])])
    # La tarea fija queda antes del término inicial y no oculta la mejora.
    p.activities[3].planned_start = p.horizon_start
    m, loads = prepared(p)
    s = m.starts.copy()
    s[1] = m.cals[1].day(m.cals[1].rank(int(s[0])) + 2)
    s[2] = m.cals[2].day(m.cals[2].rank(int(s[0])) + 3)
    f = np.array([m.finish(i, int(start)) for i, start in enumerate(s)])
    loads.rebuild(s)
    loads.reference_finish = loads.search_finish = int(f.max())
    loads.set_evaluation_span(loads.search_finish)
    loads.scales = np.maximum(1, loads.terms(s, f))
    return p, m, loads, s, f


def test_neighborhood_keeps_exterior_and_original_bounds():
    _, m, loads, s, f = swap_case()
    lower, upper = m.lower.copy(), m.upper.copy()
    plan = neighborhood_plan(m, loads, s, f, [0, 1])
    assert plan is not None and plan.accepted
    assert list(plan.domains[2]) == [s[2]]
    assert list(plan.domains[3]) == [s[3]]
    assert all(s[i] in days for i, days in enumerate(plan.domains))
    np.testing.assert_array_equal(m.lower, lower)
    np.testing.assert_array_equal(m.upper, upper)


@pytest.mark.parametrize('invalid_hint', [False, True])
def test_lns_constant_loads_do_not_create_period_variables(invalid_hint):
    cp = pytest.importorskip('ortools.sat.python.cp_model')
    _, m, loads, s, f = swap_case()
    loads.weights = np.array([1., 0., 0., 1.])
    loads.options.respect_resource_limits = False
    loads.capacity *= .5
    # Se libera sólo la actividad sin demanda: toda la carga queda constante.
    plan = neighborhood_plan(m, loads, s, f, [2])
    hint = s.copy()
    if invalid_hint:
        hint[0] += 1
    model, _, _, valid = cpsat.build_model(m, loads, hint, f, plan, perf_counter() + 5)
    assert valid is not invalid_hint
    assert not model.validate()
    assert not any(v.name.startswith(('load', 'over', 'under', 'peak', 'ot', 'spill')) for v in model.proto.variables)
    solver = cp.CpSolver()
    cpsat._configure(solver, loads, 2)
    assert solver.solve(model) in (cp.OPTIMAL, cp.FEASIBLE)


def test_real_lns_crosses_single_move_local_optimum(monkeypatch):
    pytest.importorskip('ortools')
    monkeypatch.setenv('GANTTIER_CPSAT_WORKERS', '1')
    p, m, loads, s, f = swap_case()
    from ganttier.engine_r3.heuristics import solve
    _, late, _ = _compute(m)
    local_s, local_f, _ = solve(m, loads, late, perf_counter() + 2, initial=(s, f))
    assert int(local_f.max()) == int(f.max())
    best_s, best_f, _ = improve(m, loads, local_s, local_f, perf_counter() + 5)
    assert int(best_f.max()) < int(f.max())
    assert best_s[3] == s[3]
    assert m.lns_stats['accepted'] >= 1
    audit = verify_with_options(p, {a.id: m.dt(best_s[i]) for i, a in enumerate(m.acts)},
                                {a.id: m.dt(best_f[i]) for i, a in enumerate(m.acts)}, loads.options)
    assert audit.feasible, audit.violations
    again_s, again_f, _ = improve(m, loads, s, f, perf_counter() + 5)
    np.testing.assert_array_equal(best_s, again_s)
    np.testing.assert_array_equal(best_f, again_f)


@pytest.mark.parametrize('relation', ['FS', 'SS', 'FF', 'SF'])
def test_seed_reproducibility_with_lags_skills_and_hard_windows(relation):
    p = case(8, duration=2, hours=8, capacity=16)
    p.relations = [Relation(i, 1, i, i + 1, relation, -1 if i % 2 else 1) for i in range(1, 8)]
    p.activities[-1].window_hard = True
    p.activities[-1].window_end = p.horizon_end
    opts = OptimizeOptions(method='heuristic', seed=17, time_limit_s=3, allow_overtime=False)
    a = optimize(p, ObjectiveWeights(makespan=1, window=0), opts)
    b = optimize(p, ObjectiveWeights(makespan=1, window=0), opts)
    assert a.starts == b.starts and a.finishes == b.finishes
    assert verify_with_options(p, a.starts, a.finishes, opts).feasible


def test_worker_budget_matches_configuration(monkeypatch):
    m, loads = prepared(case(4))
    monkeypatch.setenv('GANTTIER_CPSAT_WORKERS', '1')
    one = cpsat.plan_model(m, loads, m.starts)
    monkeypatch.setenv('GANTTIER_CPSAT_WORKERS', '4')
    four = cpsat.plan_model(m, loads, m.starts)
    assert four.estimate['estimated_bytes'] > one.estimate['estimated_bytes']
    cp = pytest.importorskip('ortools.sat.python.cp_model')
    solver = cp.CpSolver()
    cpsat._configure(solver, loads, .5)
    assert solver.parameters.num_search_workers == 4
    monkeypatch.delenv('GANTTIER_CPSAT_WORKERS')
    cpsat._configure(solver, loads, .5)
    assert solver.parameters.num_search_workers == 1


@pytest.mark.parametrize('value', ['0', '-1', 'nulo'])
def test_invalid_workers_are_diagnosed_before_build(monkeypatch, value):
    monkeypatch.setenv('GANTTIER_CPSAT_WORKERS', value)
    m, loads = prepared(case(2))
    with pytest.raises(ValueError, match='entero positivo'):
        cpsat.plan_model(m, loads, m.starts)
