"""Casos adversariales de calendarios, habilidades y restricciones compartidas."""
from datetime import date
from unittest.mock import patch

import numpy as np
import pytest

from ganttier.calendars import WorkCalendar
from ganttier.domain import Calendar, CalendarException, ObjectiveWeights, OptimizeOptions, PeriodicTask, Resource
from ganttier.maintenance import generate_occurrences, level_periodic
from .test_leveling import occurrence, run


def test_calendar_exception_limits_real_daily_hours():
    cal = WorkCalendar(Calendar(1, "Reducido", holidays_country=None,
        exceptions=[CalendarException(date(2024, 1, 3), True, 4)]), date(2024, 1, 1), date(2024, 1, 31))
    occ = [occurrence(work_hours=8, window_start=date(2024, 1, 3), window_end=date(2024, 1, 3))]
    result = run(occ, {1: cal}, [Resource(1, "Ana", skills=["protecciones"], calendar_id=1)], allow_overtime=False)
    assert result.status == "infactible"
    assert result.starts == {0: date(2024, 1, 3)}
    assert result.after.overload_hours == 4


@pytest.mark.parametrize("duration", [1, 2])
def test_fixed_worker_must_have_required_skill(calendars, duration):
    result = run([occurrence(resource_id=1, duration_days=duration)], calendars,
                 [Resource(1, "Ana", skills=["otro"], calendar_id=1)])
    assert result.status == "infactible"
    assert not result.worker_assignment


def test_multiskill_capacity_is_shared_without_public_worker_assignment(calendars):
    occ = [occurrence(i, required_skill=skill, window_start=date(2024, 1, 3),
                      window_end=date(2024, 1, 3)) for i, skill in enumerate(["electrico", "protecciones"])]
    workers = [Resource(1, "Ana", skills=["electrico", "protecciones"], calendar_id=1)]
    result = run(occ, calendars, workers, assign_workers=False, allow_overtime=False)
    assert result.status == "infactible"
    assert len(result.starts) == 2
    assert result.after.overload_hours == 8


def test_equipment_is_not_labor_capacity(calendars):
    result = run([occurrence()], calendars,
                 [Resource(1, "Equipo", kind="equipo", skills=["protecciones"])], assign_workers=False)
    assert result.status == "infactible"


def test_crew_demand_is_not_assigned_to_one_person(calendars):
    result = run([occurrence(work_hours=2, crew_size=2)], calendars,
                 [Resource(1, "Ana", skills=["protecciones"], calendar_id=1)])
    assert result.status == "infactible"


@pytest.mark.parametrize("frequency", ["D", "W", "M", "Q", "S", "A"])
def test_only_n_frequencies_apply_interval(calendars, frequency):
    def generate(interval):
        task = PeriodicTask(1, 1, "T", "T", frequency=frequency, interval=interval)
        return generate_occurrences([task], date(2024, 1, 1), date(2026, 12, 31), calendars, 1)
    assert [o.due_date for o in generate(3)] == [o.due_date for o in generate(1)]


def test_annual_leap_day_recovers_after_nonleap_years(calendars):
    task = PeriodicTask(1, 1, "T", "T", frequency="A", first_due=date(2024, 2, 29))
    result = generate_occurrences([task], date(2024, 1, 1), date(2028, 12, 31), calendars, 1)
    assert [o.due_date for o in result] == [date(y, 2, 29 if y in (2024, 2028) else 28) for y in range(2024, 2029)]


def test_weekly_multiday_variance_reduced_independently(calendars):
    occ = [occurrence(i, duration_days=3, work_hours=24, window_end=date(2024, 1, 26)) for i in range(8)]
    result = run(occ, calendars, level_period="week", seed=19)
    before = np.array(result.profile_before.series[0].load)
    after = np.array(result.profile_after.series[0].load)
    assert len(result.starts) == len(occ)
    assert after.sum() == before.sum() == 192
    assert np.var(after) < np.var(before)
    assert result.after.load_std == pytest.approx(np.std(after))
    again = run(occ, calendars, level_period="week", seed=19)
    assert result.starts == again.starts
    assert result.worker_assignment == again.worker_assignment


def test_outages_at_different_installations_are_not_grouped_together(calendars):
    occ = [occurrence(i, work_hours=1, requires_outage=True, location="A" if i < 2 else "B") for i in range(4)]
    result = run(occ, calendars)
    assert result.starts[0] == result.starts[1]
    assert result.starts[2] == result.starts[3]
    assert result.starts[0] != result.starts[2]


def test_before_metrics_measure_existing_plan(calendars):
    occ = [occurrence(i, planned_start=date(2024, 1, 3 + 7 * i),
                      planned_finish=date(2024, 1, 3 + 7 * i), window_end=date(2024, 1, 19)) for i in range(3)]
    result = run(occ, calendars, level_period="week")
    assert result.profile_before.series[0].load == [8, 8, 8]
    assert result.before.load_std == 0


def test_pooled_labor_can_share_a_large_occurrence(calendars):
    workers = [Resource(i, str(i), skills=["protecciones"], calendar_id=1) for i in (1, 2)]
    occ = [occurrence(work_hours=16, crew_size=2, window_start=date(2024, 1, 3), window_end=date(2024, 1, 3))]
    result = run(occ, calendars, workers, assign_workers=False, allow_overtime=False)
    assert result.status == "heuristico"
    assert result.after.overload_hours == 0
    assert result.worker_assignment == {}


def test_shift_and_reduced_hours_are_respected_on_every_workday():
    start, end = date(2024, 1, 1), date(2024, 1, 15)
    activity_cal = WorkCalendar(Calendar(1, "Diario", workweek="1111111", holidays_country=None), start, end)
    shift_cal = WorkCalendar(Calendar(2, "Turno", shift_pattern="2x2", shift_anchor=start,
        hours_per_day=6, holidays_country=None,
        exceptions=[CalendarException(date(2024, 1, 2), True, 3)]), start, end)
    occ = [occurrence(i, duration_days=2, work_hours=12, due_date=start, window_start=start,
                      window_end=end) for i in range(3)]
    result = run(occ, {1: activity_cal, 2: shift_cal},
                 [Resource(1, "Ana", skills=["protecciones"], calendar_id=2)], allow_overtime=False)
    assert result.status == "heuristico"
    assert set(result.starts.values()) == {date(2024, 1, i) for i in (5, 9, 13)}


@pytest.mark.parametrize("frequency, interval, expected", [
    ("NM", 2, [date(2024, 3, 31), date(2024, 5, 31)]),
    ("NA", 2, [date(2026, 2, 28), date(2028, 2, 29)]),
])
def test_n_month_and_year_phase_survives_horizon_clipping(calendars, frequency, interval, expected):
    anchor = date(2024, 1, 31) if frequency == "NM" else date(2024, 2, 29)
    start, end = (date(2024, 2, 1), date(2024, 5, 31)) if frequency == "NM" else (date(2025, 1, 1), date(2028, 3, 1))
    task = PeriodicTask(1, 1, "T", "T", frequency=frequency, interval=interval, first_due=anchor)
    result = generate_occurrences([task], start, end, calendars, 1)
    assert [o.due_date for o in result] == expected
    assert [o.occurrence_index for o in result] == [2, 3]
    assert all(start <= o.window_start <= o.window_end <= end for o in result)


def test_seed_result_does_not_depend_on_clock_speed(calendars):
    data = [(7, 2, 5), (3, 2, 11), (4, 1, 7), (5, 4, 10), (8, 1, 12),
            (1, 3, 7), (2, 3, 7), (5, 2, 6), (6, 2, 10), (6, 4, 8)]
    occ = [occurrence(i, work_hours=hh, window_start=date(2024, 1, lo),
                      window_end=date(2024, 1, hi)) for i, (hh, lo, hi) in enumerate(data)]
    workers = [Resource(i, str(i), skills=["protecciones"], calendar_id=1) for i in (1, 2)]
    fast = run(occ, calendars, workers, seed=4)
    with patch("ganttier.maintenance.leveling.perf_counter", side_effect=[0] + [100] * 1000):
        slow = run(occ, calendars, workers, seed=4)
    assert fast.starts == slow.starts
    assert fast.worker_assignment == slow.worker_assignment
