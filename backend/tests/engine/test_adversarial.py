"""Regresiones de la revisión adversarial; oráculos independientes del motor."""
from datetime import date

import numpy as np
import pytest

from ganttier.domain import Assignment, ObjectiveWeights, OptimizeOptions, Relation, Resource
from ganttier.engine import compute_cpm, optimize
from .conftest import FakeCalendar, activity, problem
from .test_optimize import case


def run(p, weights=None, **kwargs):
    return optimize(p, weights or ObjectiveWeights(), OptimizeOptions(method="heuristic", **kwargs))


def test_alap_consumes_free_float_without_pushing_successor():
    p = problem([activity(1, 2, constraint_type="ALAP"), activity(2), activity(3, 10)],
                [Relation(1, 1, 1, 2, "SS", 1)])
    r = compute_cpm(p)
    assert r.results[1].early_start == date(2026, 1, 5)
    assert r.results[2].early_start == date(2026, 1, 6)
    assert r.results[1].late_start == date(2026, 1, 15)


def test_alap_uses_available_free_float():
    p = problem([activity(1, 2, constraint_type="ALAP"),
                 activity(2, constraint_type="SNET", constraint_date=date(2026, 1, 12)),
                 activity(3, 10)], [Relation(1, 1, 1, 2)])
    r = compute_cpm(p)
    assert r.results[1].early_start == date(2026, 1, 8)
    assert r.results[1].free_float == 0
    assert r.results[2].early_start == date(2026, 1, 12)


def test_negative_total_float_does_not_make_free_float_negative():
    p = problem([activity(1, constraint_type="SNLT", constraint_date=date(2026, 1, 2)),
                 activity(2, 10)])
    r = compute_cpm(p).results[1]
    assert r.total_float == -1
    assert r.free_float == 9


def test_start_milestone_does_not_add_a_workday():
    p = problem([activity(1, 0), activity(2)], [Relation(1, 1, 1, 2)])
    r = compute_cpm(p)
    assert r.results[2].early_start == date(2026, 1, 5)
    assert r.results[1].total_float == 0


def test_progress_override_is_consistent_with_optimizer():
    p = problem([activity(1, 10), activity(2, 10, actual_start=date(2026, 1, 5),
                status="en_curso", remaining_duration=2, percent_complete=50)],
                [Relation(1, 1, 1, 2)], data_date=date(2026, 1, 12))
    r = run(p)
    assert r.status == "heuristico", r.warnings
    assert r.starts[2] == date(2026, 1, 5)
    assert r.finishes[2] == date(2026, 1, 14)   # corte al final del 12: remanente 13-14
    assert any("Progress Override" in w for w in r.warnings)


def test_mfo_conflict_exposes_negative_float_on_predecessor():
    p = problem([activity(1, 5), activity(2, 2, constraint_type="MFO",
                constraint_date=date(2026, 1, 6))], [Relation(1, 1, 1, 2)])
    r = compute_cpm(p)
    assert r.results[2].early_finish == date(2026, 1, 6)
    assert r.results[1].total_float == -5
    assert r.results[2].total_float == 0  # mandatory dates are fixed in both passes
    assert any("precedencia" in w for w in r.warnings)


def test_before_is_current_plan_and_makespan_reduces_finish():
    p = case(2)
    for a in p.activities:
        a.planned_start = a.planned_finish = date(2026, 1, 20)
    r = run(p, ObjectiveWeights(makespan=1, window=0))
    assert r.before.finish == date(2026, 1, 20)
    assert r.after.finish < r.before.finish
    assert r.profile_before.series[0].load[15] == 16


def test_hard_successor_window_propagates_to_predecessor():
    p = problem([activity(1, window_start=date(2026, 1, 20)),
                 activity(2, window_hard=True, window_end=date(2026, 1, 7))],
                [Relation(1, 1, 1, 2)])
    r = run(p)
    assert r.status == "heuristico", r.warnings
    assert r.finishes[2] <= date(2026, 1, 7)


def test_infeasible_result_cannot_be_applied_as_schedule():
    p = case(1, hours=40, capacity=8, overtime=2)
    r = run(p)
    assert r.status == "infactible"
    assert not r.starts and not r.finishes
    assert r.after is None and r.profile_after is None


def test_locked_nonworking_day_is_not_accepted():
    p = problem([activity(1, locked=True, planned_start=date(2026, 1, 10))])
    r = run(p)
    assert r.status == "infactible"
    assert any("laborable" in w for w in r.warnings)


def test_locked_finish_is_preserved_or_rejected():
    p = problem([activity(1, 2, locked=True, planned_start=date(2026, 1, 5),
                          planned_finish=date(2026, 1, 9))])
    r = run(p)
    assert r.status == "infactible" or r.finishes[1] == date(2026, 1, 9)


def test_weight_normalization_does_not_overflow():
    p = case(2)
    a = run(p, ObjectiveWeights(1, 1, 1, 1))
    b = run(p, ObjectiveWeights(1e308, 1e308, 1e308, 1e308))
    assert b.after.objective_value == pytest.approx(a.after.objective_value)
    assert b.starts == a.starts


def test_units_only_implicit_crew_has_load():
    p = problem([activity(1, 2, required_skill="A", crew_size=2)],
                resources=[Resource(1, "R", skills=["A"], max_units=2)])
    r = run(p)
    assert sum(r.profile_after.series[0].load) == 32


@pytest.mark.parametrize("complete", [False, True])
def test_actual_duration_does_not_expand_remaining_horizon(complete):
    a = activity(1, 10000, actual_start=date(2026, 1, 5), remaining_duration=2,
                 percent_complete=100 if complete else 50,
                 actual_finish=date(2026, 1, 6) if complete else None)
    r = compute_cpm(problem([a], data_date=date(2026, 1, 12)))
    assert r.results[1].early_finish == date(2026, 1, 6 if complete else 14)


def test_free_float_does_not_delay_project_finish_on_negative_ss():
    p = problem([activity(1, 10), activity(2)], [Relation(1, 1, 1, 2, "SS", -5)])
    assert compute_cpm(p).results[1].free_float == 0


def test_alap_terminal_project_finish_is_actual_maximum():
    p = problem([activity(1, constraint_type="ALAP")])
    p.project.end_date = date(2026, 1, 20)
    r = compute_cpm(p)
    assert r.project_finish == max(a.early_finish for a in r.results.values())


def test_zero_duration_mfo_has_identical_start_and_finish():
    r = compute_cpm(problem([activity(1, 0, constraint_type="MFO", constraint_date=date(2026, 1, 11))]))
    assert r.results[1].early_start == r.results[1].early_finish == date(2026, 1, 11)


def test_metrics_are_json_serializable_domain_values():
    import json
    from ganttier.domain import to_dict
    json.dumps(to_dict(run(case(2))))


def test_bulk_profile_matches_individual_accumulation():
    from ganttier.engine.cpm import Model, _compute
    from ganttier.engine.loads import Loads
    p = case(1200, capacity=10000)
    p.calendars[2] = FakeCalendar(weekdays=range(7), exceptions={date(2026, 1, 6): 4})
    for i, a in enumerate(p.activities):
        a.calendar_id = 2 if i % 2 else 1
        a.window_start = date(2026, 1, 8)
        a.window_end = date(2026, 1, 9)
        p.assignments[i].work_hours = 0 if i % 3 else 8
        p.assignments[i].units = 0.5
    m = Model(p)
    _compute(m)
    loads = Loads(m, OptimizeOptions(level_period="week"), ObjectiveWeights(1, 1, 1, 1))
    starts = m.starts + np.arange(1200) % 14
    for i, start in enumerate(starts):
        loads.change(i, int(start), 1)
    expected, periods = loads.load.copy(), loads.period_load.copy()
    loads.rebuild(starts)
    assert np.allclose(loads.load, expected)
    assert np.allclose(loads.period_load, periods)
    finishes = np.array([m.finish(i, int(s)) for i, s in enumerate(starts)])
    expected_window = sum(loads.window(i, starts[i:i+1], finishes[i:i+1])[0] for i in range(1200))
    assert loads.terms(starts, finishes)[2] == expected_window
    assert loads.metrics(starts, finishes).days_out_of_window == expected_window


def test_four_objectives_and_weighted_tradeoff_change_solution():
    p = case(4, hours=6, capacity=8, overtime=4)
    for a in p.activities:
        a.window_start = a.window_end = date(2026, 1, 12)
        a.planned_start = a.planned_finish = date(2026, 1, 20)
    results = [run(p, w, respect_resource_limits=False) for w in (
        ObjectiveWeights(1, 0, 0, 0), ObjectiveWeights(0, 1, 0, 0),
        ObjectiveWeights(0, 0, 1, 0), ObjectiveWeights(0, 0, 0, 1),
        ObjectiveWeights(0, 100, 1, 0), ObjectiveWeights(0, 1, 100, 0))]
    level, span, window, cost, fast_mix, window_mix = results
    assert level.after.load_std < level.before.load_std
    assert span.after.finish < span.before.finish
    assert window.after.days_out_of_window < window.before.days_out_of_window
    assert cost.after.overtime_hours < cost.before.overtime_hours
    assert fast_mix.after.finish < window_mix.after.finish
    assert window_mix.after.days_out_of_window < fast_mix.after.days_out_of_window
    for r in results:
        values = np.asarray(r.profile_after.series[0].load)
        assert values.sum() == 24
        assert r.after.peak_load == values.max()
        assert r.after.load_std == pytest.approx(values.std())
        assert r.after.overtime_hours <= 4 * np.count_nonzero(values)


@pytest.mark.parametrize("overtime", [False, True])
@pytest.mark.parametrize("weights", [ObjectiveWeights(1, 0, 0, 0), ObjectiveWeights(0, 1, 0, 0),
                                    ObjectiveWeights(0, 0, 1, 0), ObjectiveWeights(0, 0, 0, 1),
                                    ObjectiveWeights(1, 2, 3, 4)])
def test_hard_capacity_precedences_and_window_for_every_objective(weights, overtime):
    p = case(5, duration=2, hours=12, capacity=8, overtime=4)
    p.relations = [Relation(1, 1, 1, 2), Relation(2, 1, 2, 3, "SS", -1),
                   Relation(3, 1, 3, 4, "FF", 1), Relation(4, 1, 4, 5, "SF", 2)]
    p.activities[-1].window_hard = True
    p.activities[-1].window_end = date(2026, 1, 23)
    r = run(p, weights, allow_overtime=overtime)
    assert r.status == "heuristico", r.warnings
    assert r.finishes[5] <= date(2026, 1, 23)
    calendar = p.calendars[1]
    for rel in p.relations:
        event = r.finishes[rel.pred_id] if rel.type[0] == "F" else r.starts[rel.pred_id]
        target = calendar.next_workday(event, include=rel.type != "FS")
        for _ in range(abs(rel.lag_days)):
            target = calendar.next_workday(target, False) if rel.lag_days > 0 else calendar.prev_workday(target, False)
        assert (r.finishes[rel.succ_id] if rel.type[1] == "F" else r.starts[rel.succ_id]) >= target
    series = r.profile_after.series[0]
    assert all(load <= capacity + (4 if overtime and capacity else 0) for load, capacity in zip(series.load, series.capacity))
    if not overtime:
        assert r.after.overtime_hours == 0
