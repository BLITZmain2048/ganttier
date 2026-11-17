from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta

import pytest
import numpy as np

from ganttier.domain import ObjectiveWeights, Occurrence, OptimizeOptions, Resource
from ganttier.maintenance import level_periodic
from .conftest import FakeCalendar


def occurrence(index=0, **kwargs):
    base = Occurrence(1, index + 1, f"P-{index}", "Revisión de protecciones", date(2024, 1, 3),
                      date(2024, 1, 1), date(2024, 1, 5), 1, 8, 1, "protecciones", None, 1,
                      window_hard=True)
    return replace(base, **kwargs)


def resources(count=5):
    return [Resource(i + 1, f"Técnico {i}", skills=["protecciones"], calendar_id=1) for i in range(count)]


def run(occ, calendars, res=None, weights=None, **options):
    return level_periodic(occ, resources() if res is None else res, calendars,
                          weights or ObjectiveWeights(level=1, window=0), OptimizeOptions(**options))


def test_leveling_reduces_std_and_conserves_hours(calendars):
    occ = [occurrence(i) for i in range(20)]
    original = deepcopy(occ)
    result = run(occ, calendars)
    assert result.status == "heuristico"
    assert len(result.starts) == 20
    assert result.after.load_std < result.before.load_std
    assert result.after.overload_hours == 0
    assert sum(result.profile_before.series[0].load) == sum(result.profile_after.series[0].load) == 160
    assert result.profile_after.period == "week"
    assert occ == original


def test_assignment_capacity_calendar_skill_and_multiskill(calendars):
    calendars[2] = FakeCalendar(date(2024, 1, 1), date(2024, 1, 31), shift=(2, 2))
    workers = [Resource(1, "Ana", skills=["protecciones", "electrico"], calendar_id=1),
               Resource(2, "Luis", skills=["protecciones"], calendar_id=2),
               Resource(3, "Otro", skills=["lineas"], calendar_id=1),
               Resource(4, "Inactivo", skills=["protecciones"], active=False, calendar_id=1)]
    occ = [occurrence(i, work_hours=4, required_skill="electrico" if i % 4 == 0 else "protecciones") for i in range(12)]
    result = run(occ, calendars, workers)
    assert len(result.starts) == len(occ)
    loads = defaultdict(float)
    for i, start in result.starts.items():
        worker = workers[result.worker_assignment[i] - 1]
        assert occ[i].required_skill in worker.skills
        assert worker.active
        assert calendars[worker.calendar_id].is_workday(start)
        loads[worker.id, start] += occ[i].work_hours
    assert max(loads.values()) <= 8


def test_hard_window_includes_multiday_finish(calendars):
    occ = [occurrence(0, duration_days=3, work_hours=24, window_start=date(2024, 1, 4),
                      window_end=date(2024, 1, 9))]
    result = run(occ, calendars)
    assert result.starts[0] >= date(2024, 1, 4)
    assert result.finishes[0] <= date(2024, 1, 9)
    assert calendars[1].workdays_between(result.starts[0], result.finishes[0]) == 3
    assert sum(result.profile_after.series[0].load) == 24


def test_infeasible_preserves_provisional_dates_and_reports_overload(calendars):
    occ = [occurrence(i, window_start=date(2024, 1, 3), window_end=date(2024, 1, 3)) for i in range(2)]
    result = run(occ, calendars, resources(1))
    assert result.status == "infactible"
    assert len(result.starts) == 2
    assert result.after.overload_hours == 8
    assert result.warnings


def test_outage_grouping(calendars):
    occ = [occurrence(i, work_hours=1, location="S/E Alto Jahuel", requires_outage=True) for i in range(3)]
    grouped = run(occ, calendars, group_outages=True)
    separate = run(occ, calendars, group_outages=False)
    assert len(set(grouped.starts.values())) == 1
    assert len(set(separate.starts.values())) == 3


def test_keys_collision_empty_and_fixed_resource(calendars):
    result = run([occurrence(activity_id=47, resource_id=2)], calendars)
    assert set(result.starts) == {47}
    assert result.worker_assignment == {47: 2}
    with pytest.raises(ValueError, match="superponen"):
        run([occurrence(activity_id=1), occurrence(1)], calendars)
    empty = run([], {})
    assert empty.starts == {} and empty.after.finish is None


def test_fixed_resource_reserved_even_without_worker_assignment(calendars):
    occ = [occurrence(i, resource_id=1) for i in range(5)]
    result = run(occ, calendars, resources(1), assign_workers=False)
    assert len(result.starts) == 5
    assert set(result.worker_assignment.values()) == {1}
    assert len(set(result.starts.values())) == 5


def test_overtime_limits_and_cost(calendars):
    occ = [occurrence(0, work_hours=10, window_start=date(2024, 1, 3), window_end=date(2024, 1, 3))]
    worker = Resource(1, "Ana", skills=["protecciones"], calendar_id=1,
                      max_overtime_hours_day=2, overtime_cost_hour=5000)
    result = run(occ, calendars, [worker], weights=ObjectiveWeights(cost=1, window=0))
    assert result.after.overtime_hours == 2
    assert result.after.overtime_cost == 10000
    assert run(occ, calendars, [worker], allow_overtime=False).status == "infactible"


def test_soft_window_fallback_and_hard_window(calendars):
    occ = [occurrence(0, window_start=date(2024, 1, 3), window_end=date(2024, 1, 3)),
           occurrence(1, window_start=date(2024, 1, 3), window_end=date(2024, 1, 3), window_hard=False),
           occurrence(2, work_hours=0, due_date=date(2024, 1, 5))]
    result = run(occ, calendars, resources(1), weights=ObjectiveWeights(window=1))
    assert len(result.starts) == 3
    assert result.starts[0] == date(2024, 1, 3)          # la ventana dura se respeta
    # La capacidad es dura y la ventana 1 es blanda: se mueve fuera de su ventana (con advertencia)
    # en vez de sobrecargar al único recurso (criterio E1).
    assert result.starts[1] != date(2024, 1, 3)
    assert result.after.activities_out_of_window == 1
    assert result.after.overload_hours == 0
    assert result.status != "infactible"


def test_weekly_objective_balances_weeks(calendars):
    occ = [occurrence(i, window_end=date(2024, 1, 19)) for i in range(6)]
    result = run(occ, calendars, level_period="week")
    assert result.profile_after.series[0].load == [16, 16, 16]


def test_early_date_objective(calendars):
    result = run([occurrence()], calendars, weights=ObjectiveWeights(makespan=1, window=0))
    assert result.starts[0] == date(2024, 1, 1)


def test_worker_balance(calendars):
    result = run([occurrence(i, work_hours=2) for i in range(12)], calendars, resources(3))
    assert [list(result.worker_assignment.values()).count(i) for i in range(1, 4)] == [4, 4, 4]


def test_unsatisfied_skill_and_milestone(calendars):
    assert run([occurrence(required_skill="inexistente")], calendars).status == "infactible"
    result = run([occurrence(duration_days=0, work_hours=0)], calendars, [])
    assert result.starts[0] == result.finishes[0]
    assert result.after.peak_load == 0


def test_determinism(calendars):
    occ = [occurrence(i) for i in range(15)]
    a, b = run(occ, calendars), run(occ, calendars)
    assert a.starts == b.starts and a.worker_assignment == b.worker_assignment


def test_cost_selects_cheaper_overtime_worker(calendars):
    workers = [Resource(1, "Costoso", skills=["protecciones"], calendar_id=1,
                        max_overtime_hours_day=2, overtime_cost_hour=100),
               Resource(2, "Económico", skills=["protecciones"], calendar_id=1,
                        max_overtime_hours_day=2, overtime_cost_hour=10)]
    occ = [occurrence(work_hours=10, window_start=date(2024, 1, 3), window_end=date(2024, 1, 3))]
    result = run(occ, calendars, workers, weights=ObjectiveWeights(cost=1, window=0))
    assert result.worker_assignment[0] == 2
    assert result.after.overtime_cost == 20
    assert result.after.overload_hours == 2


def test_baseline_hours_not_lost_on_closed_calendar():
    calendars = {1: FakeCalendar(date(2024, 1, 1), date(2024, 1, 31), weekdays=())}
    result = run([occurrence()], calendars)
    assert result.status == "infactible"
    assert sum(result.profile_before.series[0].load) == 8


def test_baseline_multiday_finish_after_last_window(calendars):
    result = run([occurrence(duration_days=3, work_hours=24, due_date=date(2024, 1, 5))], calendars)
    assert result.before.finish == date(2024, 1, 9)
    assert result.after.finish <= date(2024, 1, 5)
    assert sum(result.profile_before.series[0].load) == 24


def test_two_day_worker_assignment_checks_every_day(calendars):
    occ = [occurrence(i, duration_days=2, work_hours=16) for i in range(2)]
    result = run(occ, calendars, resources(1))
    assert result.status == "heuristico"
    busy = defaultdict(float)
    for i, start in result.starts.items():
        for offset in range((result.finishes[i] - start).days + 1):
            day = start + timedelta(days=offset)
            if calendars[1].is_workday(day):
                busy[day] += 8
    assert max(busy.values()) == 8


@pytest.mark.parametrize("seed", range(10))
def test_randomized_capacity_and_conservation(seed):
    rng = np.random.default_rng(seed)
    calendars = {1: FakeCalendar(date(2024, 1, 1), date(2024, 2, 29)),
                 2: FakeCalendar(date(2024, 1, 1), date(2024, 2, 29), shift=(4, 3))}
    workers = [Resource(i, f"Técnico {i}", skills=["protecciones", "electrico"],
                        calendar_id=1 + i % 2, max_overtime_hours_day=2,
                        overtime_cost_hour=100 + i) for i in range(1, 5)]
    occ = [occurrence(i, duration_days=int(rng.integers(1, 4)), work_hours=float(rng.integers(1, 9)),
                      required_skill="electrico" if i % 2 else "protecciones",
                      due_date=date(2024, 1, int(rng.integers(1, 20))), window_end=date(2024, 1, 19),
                      calendar_id=1 + i % 2) for i in range(30)]
    result = run(occ, calendars, workers, weights=ObjectiveWeights(level=1, window=1, cost=seed % 2),
                 level_period="week" if seed % 2 else "day")
    daily = defaultdict(float)
    for i, start in result.starts.items():
        o, worker = occ[i], workers[result.worker_assignment[i] - 1]
        days = [start + timedelta(days=j) for j in range((result.finishes[i] - start).days + 1)
                if calendars[o.calendar_id].is_workday(start + timedelta(days=j))]
        assert len(days) == o.duration_days
        assert o.window_start <= start <= result.finishes[i] <= o.window_end
        for d in days:
            assert calendars[worker.calendar_id].is_workday(d)
            daily[worker.id, d] += o.work_hours / o.duration_days
    assert max(daily.values(), default=0) <= 10 + 1e-8
    assert sum(sum(series.load) for series in result.profile_after.series) == pytest.approx(
        sum(occ[i].work_hours for i in result.starts))
    assert sum(sum(series.load) for series in result.profile_before.series) == pytest.approx(
        sum(o.work_hours for o in occ))


def test_skill_name_cannot_collide_with_fixed_resource(calendars):
    workers = [Resource(1, "Ana", skills=["resource:2"], calendar_id=1),
               Resource(2, "Luis", skills=["otro"], calendar_id=1)]
    occ = [occurrence(0, required_skill="resource:2"), occurrence(1, resource_id=2, required_skill="otro")]
    result = run(occ, calendars, workers)
    assert result.worker_assignment == {0: 1, 1: 2}
    assert len(result.profile_after.series) == 2
