"""Aceptación funcional de objetivos, perfiles y restricciones duras."""
from copy import deepcopy
from datetime import date

import numpy as np
import pytest

from ganttier.domain import Assignment, ObjectiveWeights, OptimizeOptions, Relation, Resource
from ganttier.engine import optimize
from .conftest import FakeCalendar, activity, problem


def case(n=8, duration=1, hours=8, capacity=8, overtime=0):
    acts = [activity(i, duration) for i in range(1, n + 1)]
    # La capacidad diaria por unidad no supera la jornada del calendario (8 h): capacidades mayores se modelan como unidades.
    resources = [Resource(1, "Electricista", skills=["eléctrico"], capacity_hours_day=min(capacity, 8), max_units=max(1.0, capacity / 8), max_overtime_hours_day=overtime / max(1.0, capacity / 8), overtime_cost_hour=20)]
    assignments = [Assignment(i, i, resource_id=1, work_hours=hours) for i in range(1, n + 1)]
    return problem(acts, resources=resources, assignments=assignments)


def options(**kwargs):
    return OptimizeOptions(method="heuristic", time_limit_s=10, **kwargs)


def test_level_reduces_std_and_preserves_hours():
    p = case()
    saved = deepcopy(p.activities)
    r = optimize(p, ObjectiveWeights(level=1, window=0), options(respect_resource_limits=False))
    assert r.status == "heuristico"
    assert r.after.load_std < r.before.load_std
    assert sum(r.profile_before.series[0].load) == sum(r.profile_after.series[0].load) == 64
    assert p.activities == saved


def test_makespan_respects_capacity_and_weekly_profile():
    r = optimize(case(), ObjectiveWeights(makespan=1, window=0), options(level_period="week"))
    assert r.status == "heuristico"
    assert r.after.finish == date(2026, 1, 14)
    assert r.after.overload_hours == 0
    assert r.profile_after.period == "week"
    assert r.profile_after.labels[0] == "2026-01-05"


def test_hard_windows_and_fixed_successor():
    p = case(2, duration=2, hours=16)
    p.activities[0].window_hard = True
    p.activities[0].window_start = date(2026, 1, 8)
    p.activities[0].window_end = date(2026, 1, 9)
    p.activities[1].locked = True
    p.activities[1].planned_start = date(2026, 1, 12)
    p.relations = [Relation(1, 1, 1, 2)]
    r = optimize(p, ObjectiveWeights(level=1, window=0), options())
    assert r.status == "heuristico"
    assert r.starts[1] == date(2026, 1, 8)
    assert r.finishes[1] == date(2026, 1, 9)
    assert r.starts[2] == date(2026, 1, 12)


def test_cost_reduces_overtime():
    r = optimize(case(6, hours=6, capacity=8, overtime=8), ObjectiveWeights(cost=1, window=0), options(respect_resource_limits=False))
    assert r.after.overtime_cost < r.before.overtime_cost
    assert r.after.overtime_hours == 0
    assert r.after.overload_hours == 0


def test_soft_window_objective():
    p = case(1)
    p.activities[0].window_start = date(2026, 1, 19)
    p.activities[0].window_end = date(2026, 1, 20)
    r = optimize(p, ObjectiveWeights(window=1), options())
    assert r.after.days_out_of_window == 0
    assert date(2026, 1, 19) <= r.starts[1] <= date(2026, 1, 20)


def test_infeasible_capacity_is_explicit():
    p = case(1, hours=20)
    r = optimize(p, ObjectiveWeights(makespan=1, window=0), options())
    assert r.status == "infactible"
    assert any("Capacidad insuficiente" in s for s in r.warnings)


def test_assignment_units_variable_hours_and_resource_calendar():
    p = case(1, duration=2, hours=0, capacity=16)
    p.calendars = {1: FakeCalendar(exceptions={date(2026, 1, 5): 4}), 2: FakeCalendar(holidays=[date(2026, 1, 5)])}
    p.resources[0].calendar_id = 2
    p.assignments[0].units = 2
    r = optimize(p, ObjectiveWeights(makespan=1, window=0), options(group_by="resource"))
    assert r.starts[1] == date(2026, 1, 6)
    assert sum(r.profile_after.series[0].load) == 32


@pytest.mark.parametrize("group", ["skill", "resource"])
def test_implicit_skill_total_hh_and_crew(group):
    p = case(2, hours=16)
    p.assignments = []
    for a in p.activities:
        a.work_hours, a.required_skill, a.crew_size = 8, "eléctrico", 3
    r = optimize(p, ObjectiveWeights(makespan=1, window=0), options(group_by=group))
    assert r.status == "heuristico"
    assert sum(r.profile_after.series[0].load) == 16


def test_multiple_skills_do_not_duplicate_worker_capacity():
    p = case(2)
    p.resources[0].skills = ["A", "B"]
    p.assignments = [Assignment(1, 1, skill="A", work_hours=8), Assignment(2, 2, skill="B", work_hours=8)]
    r = optimize(p, ObjectiveWeights(makespan=1, window=0), options())
    assert r.status == "heuristico"
    assert r.starts[1] != r.starts[2]


def test_in_progress_only_remaining_load():
    p = case(1, duration=10, hours=80)
    p.activities[0].actual_start = date(2026, 1, 5)
    p.activities[0].percent_complete = 50
    p.data_date = date(2026, 1, 12)
    r = optimize(p, ObjectiveWeights(), options())
    assert r.starts[1] == date(2026, 1, 5)
    assert r.finishes[1] == date(2026, 1, 19)   # corte al final del 12: 5 días desde el 13
    assert sum(r.profile_after.series[0].load) == 40


def test_empty_and_invalid_options():
    r = optimize(problem([]), ObjectiveWeights(), options())
    assert r.starts == {} and r.after.finish is None
    with pytest.raises(ValueError, match="pesos"):
        optimize(problem([]), ObjectiveWeights(level=-1), options())


def test_incremental_scores_match_full_objective():
    from ganttier.engine.cpm import Model, _compute
    from ganttier.engine.loads import Loads
    p = case(3, duration=3, hours=18, overtime=8)
    for a in p.activities:
        a.window_start, a.window_end = date(2026, 1, 8), date(2026, 1, 15)
    m = Model(p)
    _compute(m)
    opts = options(level_period="week", respect_resource_limits=False)
    loads = Loads(m, opts, ObjectiveWeights(1, 1, 1, 1))
    loads.rebuild(m.starts)
    loads.scales = np.maximum(1, loads.terms(m.starts, m.finishes))
    starts = np.array([m.idx(date(2026, 1, d)) for d in (5, 8, 12)])
    loads.change(0, int(m.starts[0]), -1)
    predicted, finishes = loads.candidates(0, starts, int(m.finishes[1:].max()))
    actual = []
    for s, f in zip(starts, finishes):
        new_s, new_f = m.starts.copy(), m.finishes.copy()
        new_s[0], new_f[0] = s, f
        loads.rebuild(new_s)
        actual.append(float(np.dot(loads.weights, loads.terms(new_s, new_f) / loads.scales)))
    assert np.allclose(predicted - predicted[0], np.array(actual) - actual[0])


@pytest.mark.parametrize("weights", [ObjectiveWeights(level=1, window=0), ObjectiveWeights(makespan=1, window=0), ObjectiveWeights(cost=1, window=0), ObjectiveWeights(1, 1, 1, 1)])
def test_grouped_sgs_matches_individual_objective(weights):
    from time import perf_counter
    from ganttier.engine.cpm import Model, _compute
    from ganttier.engine.loads import Loads
    from ganttier.engine.heuristics import _independent_days, solve
    p = case(40, capacity=32, overtime=8)
    m = Model(p)
    _, late, _ = _compute(m)
    loads = Loads(m, options(), weights)
    loads.rebuild(m.starts)
    loads.scales = np.maximum(1, loads.terms(m.starts, m.finishes))
    single_s, single_f, _ = solve(m, loads, late, perf_counter() + 10)
    single_value = loads.metrics(single_s, single_f).objective_value
    group_s, group_f, _ = _independent_days(m, loads, late, perf_counter() + 10)
    assert not m.violations(group_s, group_f)
    assert not loads.violations()
    assert loads.metrics(group_s, group_f).objective_value <= single_value + 1e-8


def test_thousand_independent_tasks_with_windows():
    p = case(1000, capacity=800)
    for a in p.activities[:500]:
        a.window_start, a.window_end, a.window_hard = date(2026, 1, 12), date(2026, 1, 16), True
    r = optimize(p, ObjectiveWeights(1, 1, 1, 1), options())
    assert r.status == "heuristico", r.warnings
    assert r.after.overload_hours == 0
    assert r.after.days_out_of_window == 0
    assert len(r.starts) == 1000


def test_mixed_explicit_hours_and_units_are_added():
    p = case(1, capacity=16)
    p.assignments.append(Assignment(2, 1, resource_id=1, units=1, work_hours=0))
    r = optimize(p, ObjectiveWeights(makespan=1, window=0), options())
    assert sum(r.profile_after.series[0].load) == 16


def test_resources_without_skill_share_full_capacity():
    p = case(2)
    p.resources[0].skills = []
    p.resources.append(Resource(2, "Segundo", skills=[]))
    p.assignments[1].resource_id = 2
    r = optimize(p, ObjectiveWeights(makespan=1, window=0), options())
    assert r.starts[1] == r.starts[2] == date(2026, 1, 5)
    assert r.profile_after.series[0].capacity[0] == 16
