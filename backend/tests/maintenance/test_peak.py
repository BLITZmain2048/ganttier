"""Pico opt-in: contrato, deltas exactos, transacciones y auditor independiente."""
from dataclasses import fields, replace
from datetime import date
from time import perf_counter

import numpy as np
import pytest

from ganttier import domain as d
from ganttier.api import schemas
from ganttier.calendars import WorkCalendar
from ganttier.engine.verify import verify_schedule
from ganttier.maintenance import crew_assignment, generate_occurrences, level_periodic
from ganttier.maintenance.leveling import _Leveler
from ganttier.maintenance._peak import _attempt, improve
from ganttier.optimizacion.variantes import Variante


FIRST, LAST = date(2027, 1, 4), date(2027, 1, 15)


def case(hours=(5, 4, 3, 2), end=LAST):
    calendars = {1: WorkCalendar(d.Calendar(1, "L-V", holidays_country=None), FIRST, LAST)}
    resources = [d.Resource(1, "Ana", skills=["electrico"], calendar_id=1)]
    rows = [d.Occurrence(1, i + 1, f"T{i}", "Inspección", FIRST, FIRST, end,
                        1, hh, 1, "electrico", None, 1, activity_id=i + 1, window_hard=True)
            for i, hh in enumerate(hours)]
    return rows, resources, calendars


def audit(rows, resources, calendars, result, tasks=None):
    shared = {f.name for f in fields(d.Activity)} & {f.name for f in fields(d.Occurrence)}
    activities = [d.Activity(id=o.activity_id, project_id=1,
                            **{key: getattr(o, key) for key in shared}) for o in rows]
    problem = d.ScheduleProblem(d.Project(1, "S1", mode="mantenimiento", start_date=FIRST,
                               end_date=LAST, data_date=FIRST, calendar_id=1),
                               activities, [], resources, [], calendars, 1, FIRST, LAST,
                               periodic_tasks=tasks or [])
    report = verify_schedule(problem, result.starts, result.finishes,
                             worker_assignment=result.worker_assignment, crews=crew_assignment(result))
    assert report.feasible, report.violations


def test_peak_contract_and_preset():
    assert d.ObjectiveWeights().peak == schemas.ObjectiveWeights().peak == 0
    assert d.ObjectiveWeights(peak=3, window=1).normalized().peak == .75
    assert d.ObjectiveWeights(0, 0, 0, 0).normalized().window == 1
    assert schemas.ObjectiveWeights.model_json_schema()["properties"]["peak"]["default"] == 0
    assert Variante(objective="pico").solve_args() == Variante(objective="peak").solve_args()
    assert Variante(objective="pico").solve_args()[0]["peak"] == 1


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_peak_rejected_at_every_boundary(value):
    with pytest.raises(ValueError):
        schemas.ObjectiveWeights(peak=value)
    with pytest.raises(ValueError):
        Variante(weights={"peak": value})
    with pytest.raises(ValueError):
        level_periodic(*case(), d.ObjectiveWeights(peak=value), d.OptimizeOptions())


def test_peak_zero_preserves_dates_assignment_and_metrics():
    args = case()
    a = level_periodic(*args, d.ObjectiveWeights(level=1), d.OptimizeOptions())
    b = level_periodic(*args, d.ObjectiveWeights(level=1, peak=0), d.OptimizeOptions())
    assert (a.starts, a.worker_assignment, a.after) == (b.starts, b.worker_assignment, b.after)


@pytest.mark.parametrize("period", ["day", "week"])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_peak_balances_fixed_resources_by_skill_and_repeats(period, seed):
    rows, resources, calendars = case((8, 8, 8, 8))
    resources = [replace(resources[0], id=i) for i in range(1, 5)]
    rows = [replace(o, resource_id=i) for i, o in enumerate(rows, 1)]
    opts = d.OptimizeOptions(level_period=period, allow_overtime=False, seed=seed)
    weights = d.ObjectiveWeights(peak=1, level=.1, window=0)
    a = level_periodic(rows, resources, calendars, weights, opts)
    b = level_periodic(rows, resources, calendars, weights, opts)
    assert (a.starts, a.worker_assignment, a.after) == (b.starts, b.worker_assignment, b.after)
    audit(rows, resources, calendars, a)
    if period == "day":
        assert len(set(a.starts.values())) == 4
    else:
        assert sum(day <= date(2027, 1, 8) for day in a.starts.values()) == 2


@pytest.mark.parametrize("period", ["day", "week"])
def test_multiday_peak_delta_matches_recomputed_objective(period):
    rows, resources, calendars = case((8, 12))
    rows[1] = replace(rows[1], duration_days=3)
    lv = _Leveler(rows, resources, calendars, d.ObjectiveWeights(peak=1, window=0),
                  d.OptimizeOptions(level_period=period), perf_counter())
    lv._update(0, 0, 0, 1)
    candidate = lv.candidates[1]
    predicted = lv.peak_profile.score(1, candidate, lv.amount[1])
    old = lv.peak_profile.value()
    for choice in range(len(candidate.starts)):
        lv._update(1, choice, 0, 1)
        assert lv.peak_profile.value() - old == pytest.approx(predicted[choice])
        lv._update(1, choice, 0, -1)


def packed():
    rows, resources, calendars = case(end=date(2027, 1, 5))
    lv = _Leveler(rows, resources, calendars, d.ObjectiveWeights(peak=1, level=1, window=0),
                  d.OptimizeOptions(time_limit_s=2, allow_overtime=False), perf_counter())
    # 5+3 / 4+2. No move fits capacity; swapping 3 and 2 yields 7 / 7.
    for i, choice in enumerate([0, 1, 0, 1]):
        lv.selected[i], lv.workers[i] = choice, 0
        lv._update(i, choice, 0, 1)
    return lv


def test_swap_escapes_move_local_minimum_without_overload():
    lv = packed()
    assert not _attempt(lv, {2: 1}, float("inf"), perf_counter)
    improve(lv, list(range(4)), perf_counter() + 2, perf_counter)
    assert lv.load[0].tolist() == [7, 7]
    assert lv.worker_remaining.min() >= 0
    assert lv.peak_search_stats["swaps"] >= 1


def test_expired_budget_does_no_work_and_late_trial_rolls_back():
    lv = packed()
    arrays = [lv.load, lv.week_load, lv.worker_load, lv.worker_remaining,
              lv.worker_total, lv.selected, lv.peak_profile.day, lv.peak_profile.week]
    before = [a.copy() for a in arrays]
    improve(lv, list(range(4)), 10, lambda: 10)
    ticks = iter([0, 11])
    assert not _attempt(lv, {2: 1, 3: 0}, 10, lambda: next(ticks))
    for expected, actual in zip(before, arrays):
        np.testing.assert_array_equal(expected, actual)


def test_peak_respects_worker_calendar_reserved_capacity_outages_and_hard_dates():
    rows, resources, calendars = case((4,) * 8)
    calendars[2] = WorkCalendar(d.Calendar(2, "Ausencia", holidays_country=None,
        exceptions=[d.CalendarException(date(2027, 1, 5), 0)]), FIRST, LAST)
    resources[0] = replace(resources[0], calendar_id=2)
    rows = [replace(o, requires_outage=True, location="S/E") for o in rows]
    result = level_periodic(rows, resources, calendars, d.ObjectiveWeights(peak=1, level=1),
                            d.OptimizeOptions(allow_overtime=False),
                            date_constraints={1: ("MSO", FIRST)},
                            reserved_load={1: {date(2027, 1, 6): 8}})
    assert result.starts[1] == FIRST
    assert date(2027, 1, 5) not in result.starts.values()
    assert date(2027, 1, 6) not in result.starts.values()
    audit(rows, resources, calendars, result)


def test_peak_team_batches_independent_verifier():
    _, resources, calendars = case()
    resources = [replace(resources[0], kind="cuadrilla", is_work_team=True, project_id=1)]
    tasks = [d.PeriodicTask(1, 1, "P", "Lotes", first_due=FIRST, frequency="M",
                           required_skill="electrico", calendar_id=1, team_ids=[1],
                           quantity_total=4, batch_size=1, productivity_day_team=1, unit="u")]
    rows = generate_occurrences(tasks, FIRST, LAST, calendars, 1, resources=resources)
    for i, row in enumerate(rows, 1):
        row.activity_id = i
    result = level_periodic(rows, resources, calendars, d.ObjectiveWeights(peak=1, level=1),
                           d.OptimizeOptions(allow_overtime=False))
    audit(rows, resources, calendars, result, tasks)


@pytest.mark.parametrize("period", ["day", "week"])
def test_implicit_hours_in_peak_reference_and_solution(period):
    rows, resources, calendars = case((0, 0))
    result = level_periodic(rows, resources, calendars, d.ObjectiveWeights(peak=1, window=0),
                           d.OptimizeOptions(level_period=period, allow_overtime=False))
    assert result.before.objective_value == 16 + 10000 * (8 + 8**2)
    assert result.after.objective_value == 8
    audit(rows, resources, calendars, result)


@pytest.mark.parametrize("seed", range(6))
def test_opt_in_never_worsens_reference_metrics(seed):
    rng = np.random.default_rng(seed)
    rows, resources, calendars = case(tuple(float(h) for h in rng.integers(1, 8, size=16)))
    resources = [replace(resources[0], id=i) for i in range(1, 4)]
    options = d.OptimizeOptions(seed=seed, allow_overtime=False)
    weights = d.ObjectiveWeights(level=1, window=.05)
    before = level_periodic(rows, resources, calendars, weights, options)
    after = level_periodic(rows, resources, calendars, replace(weights, peak=1), options)
    for field in ("load_std", "peak_load", "overload_hours", "days_out_of_window"):
        assert getattr(after.after, field) <= getattr(before.after, field) + 1e-8
    audit(rows, resources, calendars, after)


@pytest.mark.parametrize("seed", range(6))
def test_seeded_mixed_skills_split_crews_and_multiday_audit(seed):
    rng = np.random.default_rng(seed)
    rows, resources, calendars = case(tuple(float(h) for h in rng.integers(1, 8, size=24)))
    resources = [replace(resources[0], id=i, skills=["electrico", "mecanico"])
                 for i in range(1, 7)]
    rows = [replace(o, duration_days=1 + i % 3, crew_size=2 if i % 3 else 1,
                    required_skill="mecanico" if i % 2 else "electrico")
            for i, o in enumerate(rows)]
    result = level_periodic(rows, resources, calendars,
                           d.ObjectiveWeights(peak=1, level=1, window=.05),
                           d.OptimizeOptions(level_period="week" if seed % 2 else "day",
                                             seed=seed, allow_overtime=False))
    audit(rows, resources, calendars, result)
