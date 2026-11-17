"""Auditoría de hints y pruebas contra OR-Tools real (sin emular el solver)."""
from datetime import date
from time import perf_counter

import numpy as np
import pytest

from ganttier.domain import ObjectiveWeights, OptimizeOptions, Relation
from ganttier.engine import optimize
from ganttier.engine.cpm import Model, _compute
from ganttier.engine.loads import Loads
from ganttier.engine import cpsat
from .conftest import FakeCalendar, activity, problem
from .test_optimize import case


def test_hint_must_satisfy_rounded_capacity():
    p = case(2, hours=0.3334, capacity=0.667)
    m = Model(p)
    _compute(m)
    loads = Loads(m, OptimizeOptions(), ObjectiveWeights())
    loads.rebuild(m.starts)
    assert not loads.violations()  # feasible as floats, infeasible at 0.001 HH
    assert not cpsat.hint_is_feasible(m, loads, m.starts, m.finishes)


def test_hint_must_satisfy_precedence_and_hard_windows():
    p = case(2, capacity=100)
    p.relations = [Relation(1, 1, 1, 2)]
    m = Model(p)
    _compute(m)
    loads = Loads(m, OptimizeOptions(), ObjectiveWeights())
    assert cpsat.hint_is_feasible(m, loads, m.starts, m.finishes)
    bad_s, bad_f = m.starts.copy(), m.finishes.copy()
    bad_s[1] = bad_f[1] = bad_s[0]
    assert not cpsat.hint_is_feasible(m, loads, bad_s, bad_f)


def test_relation_table_does_not_evaluate_impossible_predecessor_dates():
    p = problem([activity(1, constraint_type="SNET", constraint_date=date(2028, 1, 3)),
                 activity(2, calendar_id=2)], [Relation(1, 1, 1, 2, "SS", -400)],
                calendars={1: FakeCalendar(), 2: FakeCalendar(weekdays=range(7))}, end=date(2028, 1, 31))
    m = Model(p)
    _compute(m)
    lo, hi = m.idx(date(2028, 1, 3)), m.idx(date(2028, 1, 31))
    table = cpsat.relation_thresholds(m, 0, 1, p.relations[0], lo, hi)
    assert len(table) == hi - lo + 1
    assert m.dt(table[0]) == date(2026, 11, 29)


@pytest.mark.parametrize("weights", [ObjectiveWeights(1, 0, 0, 0), ObjectiveWeights(0, 1, 0, 0),
                                    ObjectiveWeights(0, 0, 1, 0), ObjectiveWeights(0, 0, 0, 1),
                                    ObjectiveWeights(1, 2, 3, 4)])
def test_real_cpsat_objectives_and_profiles(weights):
    pytest.importorskip("ortools.sat.python.cp_model")
    p = case(4, hours=6, capacity=8, overtime=4)
    for a in p.activities:
        a.planned_start = a.planned_finish = date(2026, 1, 20)
        a.window_start = a.window_end = date(2026, 1, 12)
    r = optimize(p, weights, OptimizeOptions(method="cpsat", time_limit_s=10, respect_resource_limits=False))
    assert r.method_used == "cpsat", r.warnings
    assert r.status in {"optimo", "factible"}
    assert sum(r.profile_after.series[0].load) == 24
    if weights.level and not weights.window:
        assert r.after.load_std < r.before.load_std
    if weights.makespan and not weights.window:
        assert r.after.finish < r.before.finish
    if weights.window and not weights.level:
        assert r.after.days_out_of_window < r.before.days_out_of_window
    if weights.cost and not weights.window:
        assert r.after.overtime_hours < r.before.overtime_hours


def test_real_cpsat_milestone_and_weekend_resource_capacity():
    pytest.importorskip("ortools.sat.python.cp_model")
    p = case(2, duration=2, hours=16)
    p.activities.insert(0, activity(3, 0))
    p.relations = [Relation(1, 1, 3, 1), Relation(2, 1, 1, 2, "FS", -1)]
    p.activities[1].calendar_id = 2
    p.calendars[2] = FakeCalendar(weekdays=range(7))
    r = optimize(p, ObjectiveWeights(0, 1, 0, 0), OptimizeOptions(method="cpsat", time_limit_s=10))
    assert r.method_used == "cpsat", r.warnings
    assert r.starts[3] <= r.starts[1]
    assert r.after.overload_hours == 0
    assert r.after.finish == date(2026, 1, 8)


def test_real_cpsat_3000_with_time_limit():
    pytest.importorskip("ortools.sat.python.cp_model")
    p = case(3000, capacity=2400)
    p.horizon_end = date(2026, 1, 30)
    started = perf_counter()
    r = optimize(p, ObjectiveWeights(0, 1, 0, 0), OptimizeOptions(method="cpsat", time_limit_s=10))
    assert perf_counter() - started < 15
    assert r.status in {"optimo", "factible", "heuristico"}, r.warnings
    assert len(r.starts) == 3000
    assert r.after.overload_hours == 0
