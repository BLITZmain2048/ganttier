from datetime import date

import pytest

from ganttier.domain import PeriodicTask
from ganttier.maintenance import generate_occurrences
from .conftest import FakeCalendar


def task(**kwargs):
    return PeriodicTask(1, 1, "TR-01", "Inspección de transformador", **kwargs)


@pytest.mark.parametrize("frequency,interval,expected", [
    ("D", 1, date(2024, 1, 2)), ("ND", 3, date(2024, 1, 4)),
    ("W", 1, date(2024, 1, 8)), ("NW", 2, date(2024, 1, 15)),
    ("M", 1, date(2024, 2, 1)), ("NM", 2, date(2024, 3, 1)),
    ("Q", 1, date(2024, 4, 1)), ("S", 1, date(2024, 7, 1)),
    ("A", 1, date(2025, 1, 1)), ("NA", 2, date(2026, 1, 1)),
])
def test_all_frequencies(calendars, frequency, interval, expected):
    result = generate_occurrences([task(frequency=frequency, interval=interval)],
                                  date(2024, 1, 1), date(2026, 12, 31), calendars, 1)
    assert result[1].due_date == expected
    assert result[0].code == "TR-01-0001"


def test_month_end_no_drift_and_leap_year(calendars):
    result = generate_occurrences([task(first_due=date(2024, 1, 31))],
                                  date(2024, 1, 1), date(2024, 4, 30), calendars, 1)
    assert [o.due_date.day for o in result] == [31, 29, 31, 30]
    assert result[2].due_date == date(2024, 3, 31)
    assert result[2].planned_start == date(2024, 4, 1)


def test_preferred_month_weekday_and_day(calendars):
    result = generate_occurrences([task(preferred_months="1,4,7,10", day_of_month=31,
                                        preferred_weekday=4)],
                                  date(2024, 1, 1), date(2024, 12, 31), calendars, 1)
    assert [o.due_date.month for o in result] == [1, 4, 7, 10]
    assert all(o.due_date.weekday() == 4 for o in result)
    assert result[1].due_date == date(2024, 4, 26)


def test_old_anchor_preserves_phase_indices(calendars):
    result = generate_occurrences([task(first_due=date(2000, 1, 31))],
                                  date(2024, 2, 1), date(2024, 3, 31), calendars, 1)
    assert result[0].due_date == date(2024, 2, 29)
    assert result[0].occurrence_index == 290


def test_weekly_weekday_and_task_end(calendars):
    result = generate_occurrences([task(frequency="W", preferred_weekday=2,
                                        horizon_end=date(2024, 1, 10))],
                                  date(2024, 1, 1), date(2024, 2, 1), calendars, 1)
    assert [o.due_date for o in result] == [date(2024, 1, 3), date(2024, 1, 10)]
    assert result[-1].window_end == date(2024, 1, 10)


def test_nearest_workday_tie_prefers_earlier_and_keeps_due():
    cal = FakeCalendar(date(2024, 1, 1), date(2024, 1, 31), excluded=(date(2024, 1, 10),))
    result = generate_occurrences([task(first_due=date(2024, 1, 10))],
                                  date(2024, 1, 1), date(2024, 1, 31), {1: cal}, 1)
    assert result[0].due_date == date(2024, 1, 10)
    assert result[0].planned_start == date(2024, 1, 9)


def test_closed_window_is_preserved_unplanned(calendars):
    result = generate_occurrences([task(first_due=date(2024, 1, 6), tol_before_days=0,
                                        tol_after_days=0, window_hard=True)],
                                  date(2024, 1, 1), date(2024, 1, 31), calendars, 1)
    assert result[0].planned_start is None
    assert result[0].planned_finish is None


def test_disabled_and_clipped_horizon(calendars):
    assert generate_occurrences([task(active=False)], date(2024, 1, 1), date(2024, 1, 31), calendars, 1) == []
    result = generate_occurrences([task()], date(2024, 1, 1), date(2024, 1, 1), calendars, 1)
    assert result[0].window_start == result[0].window_end == date(2024, 1, 1)


@pytest.mark.parametrize("kwargs", [{"interval": 0}, {"frequency": "X"}, {"preferred_months": "13"},
                                    {"preferred_months": "enero"}, {"preferred_weekday": 7},
                                    {"day_of_month": 32}, {"work_hours": -1},
                                    {"duration_days": 0}, {"tol_after_days": -1}])
def test_invalid_tasks(calendars, kwargs):
    with pytest.raises(ValueError):
        generate_occurrences([task(**kwargs)], date(2024, 1, 1), date(2024, 1, 31), calendars, 1)


def test_calendar_without_vector_origin_uses_public_interface():
    class NoOrigin:
        def __init__(self):
            self.cal = FakeCalendar(date(2020, 1, 1), date(2025, 1, 1))

        def __getattr__(self, name):
            if name == "horizon_start":
                raise AttributeError(name)
            return getattr(self.cal, name)

    result = generate_occurrences([task(first_due=date(2024, 1, 6))],
                                  date(2024, 1, 1), date(2024, 1, 31), {1: NoOrigin()}, 1)
    assert result[0].planned_start == date(2024, 1, 5)
