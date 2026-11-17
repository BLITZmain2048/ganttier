"""Resource-budget regressions; integration tests require the real OR-Tools."""
from time import perf_counter
import sys

import numpy as np
import pytest

from ganttier.domain import ObjectiveWeights, OptimizeOptions
from ganttier.engine import cpsat, optimize
from .test_project_leveling import demo_problem, prepared
from .test_optimize import case


def test_demo_compact_plan_before_ortools():
    from ganttier.engine.loads import Loads
    m, daily, s, _ = prepared(demo_problem())
    loads = Loads(m, OptimizeOptions(level_period='week'), ObjectiveWeights(1, 0, 1, .2))
    loads.search_finish = daily.search_finish
    plan = cpsat.plan_model(m, loads, s)
    assert plan.accepted, plan.estimate
    assert plan.weekly
    assert plan.estimate['choices'] < 4000
    assert plan.estimate['cells'] < 100000
    assert all(int(s[i]) in days for i, days in enumerate(plan.domains))


def test_integer_objective_budget_preserves_positive_terms():
    coefficients, bound = cpsat.objective_coefficients([.001, 1000, .000001], [100, 1000, 10000])
    assert all(c > 0 for c in coefficients)
    assert bound == sum(c * b for c, b in zip(coefficients, [100, 1000, 10000]))
    assert bound <= cpsat.MAX_INTEGER


def test_integer_objective_rejects_unrepresentable_data():
    with pytest.raises(ValueError, match='escala entera'):
        cpsat.objective_coefficients([.00001], [cpsat.MAX_INTEGER + 1])


def test_compact_plan_for_3000_activities():
    m, loads, s, _ = prepared(case(3000, capacity=2400))
    plan = cpsat.plan_model(m, loads, s)
    assert plan.accepted, plan.estimate
    assert plan.estimate['choices'] <= cpsat.MODEL_MAX_CHOICES
    assert plan.estimate['estimated_bytes'] <= cpsat.MODEL_MAX_BYTES


def test_auto_rejects_budget_before_solver(monkeypatch):
    monkeypatch.setattr(cpsat, 'MODEL_MAX_CHOICES', 1)
    def forbidden(*args, **kwargs):
        pytest.fail('An over-budget auto model must never be constructed or solved')
    monkeypatch.setattr(cpsat, 'solve', forbidden)
    p = case(8, capacity=16)
    r = optimize(p, ObjectiveWeights(level=1), OptimizeOptions(method='auto', time_limit_s=2))
    assert r.method_used == 'heuristic'
    assert r.after is not None
    assert any('presupuesto' in w for w in r.warnings)


def test_real_demo_feasible_under_25_seconds(monkeypatch):
    pytest.importorskip('ortools')
    from ortools.sat.python import cp_model
    calls = []
    real_solve = cp_model.CpSolver.solve
    def checked(solver, model, *args, **kwargs):
        assert not model.validate()
        proto = model.proto
        assert len(proto.variables) < 20000
        assert all(max(abs(v) for v in var.domain) <= cpsat.MAX_INTEGER for var in proto.variables)
        assert not any(var.name.startswith(('excess', 'day_load', 'work')) for var in proto.variables)
        assert not solver.parameters.repair_hint  # desactivado: abortos nativos en OR-Tools 9.15
        assert solver.parameters.max_presolve_iterations == 1
        assert solver.parameters.cp_model_presolve
        assert len(proto.solution_hint.vars) == len(proto.variables)
        result = real_solve(solver, model, *args, **kwargs)
        calls.append(result)
        return result
    monkeypatch.setattr(cp_model.CpSolver, 'solve', checked)
    p = demo_problem()
    started = perf_counter()
    r = optimize(p, ObjectiveWeights(level=1, window=1, cost=.2),
                 OptimizeOptions(method='cpsat', time_limit_s=20, level_period='week'))
    assert perf_counter() - started < 25
    assert r.status in {'optimo', 'factible'}, r.warnings
    assert r.method_used == 'cpsat'
    assert any(v in (cp_model.OPTIMAL, cp_model.FEASIBLE) for v in calls)
    assert len(r.starts) == 120
    assert r.after.objective_value <= r.before.objective_value + 1e-8
    if sys.platform != 'win32':
        # Run this test alone for an isolated process high-water mark.
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_bytes = peak if sys.platform == 'darwin' else peak * 1024
        assert peak_bytes < 400_000_000, f'Process peak RSS: {peak_bytes / 1e6:.1f} MB'
    m, loads, _, _ = prepared(p)
    s = np.array([m.idx(r.starts[a.id]) for a in m.acts])
    f = np.array([m.idx(r.finishes[a.id]) for a in m.acts])
    loads.rebuild(s)
    assert not m.violations(s, f)
    assert not loads.violations()


def test_real_weekly_loads_and_complete_hint():
    pytest.importorskip('ortools')
    from ortools.sat.python import cp_model
    from ganttier.engine.loads import Loads
    p = case(4, duration=8, hours=12, capacity=100)
    m, daily, s, f = prepared(p)
    loads = Loads(m, OptimizeOptions(level_period='week'), ObjectiveWeights(1, 0, 0, .2))
    loads.search_finish = daily.search_finish
    loads.set_evaluation_span(loads.search_finish)
    plan = cpsat.plan_model(m, loads, s)
    model, _, _, valid = cpsat.build_model(m, loads, s, f, plan, perf_counter() + 5)
    assert valid
    assert not model.validate()
    assert len(model.proto.solution_hint.vars) == len(model.proto.variables)
    assert not any('lin_max' in str(c) for c in model.proto.constraints)
    solver = cp_model.CpSolver()
    cpsat._configure(solver, loads, 3)
    solver.parameters.fix_variables_to_their_hinted_value = True
    # Con varios hilos, OR-Tools 9.15 aborta ("heuristics.fixed_search != nullptr") al fijar el hint.
    solver.parameters.num_search_workers = 1
    assert solver.solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    # 1.5 HH/workday crosses a weekend and two weeks, without rounding loss.
    loads.rebuild(s)
    for var in model.proto.variables:
        if var.name.startswith('load'):
            g, period = map(int, var.name[4:].split('_'))
            hint_index = next(i for i, v in enumerate(model.proto.variables) if v.name == var.name)
            assert solver.response_proto.solution[hint_index] == round(loads.period_load[g, period] * cpsat.SCALE)
