import builtins
from datetime import date, timedelta

import numpy as np
import pytest

from ganttier import calendars, domain as d, repo
from ganttier.calendars import WorkCalendar


def test_chile_holidays_confirmed_by_package():
    holidays = pytest.importorskip("holidays")
    known = "01-01 04-03 04-04 05-01 05-21 06-29 07-16 08-15 09-18 09-19 10-12 10-31 11-01 12-08 12-25".split()
    official = holidays.country_holidays("CL", years=2026)
    cal = WorkCalendar(d.Calendar(1, "Chile", workweek="1111111"), date(2026, 1, 1), date(2026, 12, 31))
    for text in known:
        day = date.fromisoformat("2026-" + text)
        if day in official:
            assert not cal.is_workday(day)
            assert cal.hours(day) == 0


def test_fallback_without_holidays(monkeypatch):
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "holidays":
            raise ImportError("Sin dependencia")
        return original(name, *args, **kwargs)
    calendars._holiday_dates.cache_clear()
    monkeypatch.setattr(builtins, "__import__", missing)
    cal = WorkCalendar(d.Calendar(1, "Chile", workweek="1111111"), date(2026, 1, 1), date(2026, 12, 31))
    assert not cal.is_workday(date(2026, 4, 3))
    assert not cal.is_workday(date(2026, 6, 21))
    assert not cal.is_workday(date(2026, 9, 18))
    assert cal.is_workday(date(2026, 4, 6))
    calendars._holiday_dates.cache_clear()


def test_shift_anchor_exceptions_and_vectors():
    start = date(2026, 1, 1)
    definition = d.Calendar(1, "Turno", hours_per_day=12, shift_pattern="7x7", shift_anchor=start,
        exceptions=[d.CalendarException(start + timedelta(days=8), True, 4, "Trabajo especial"), d.CalendarException(start + timedelta(days=1), False)])
    cal = WorkCalendar(definition, start, start + timedelta(days=27))
    assert cal.hours(start) == 12  # Feriado ignorado en turno.
    assert not cal.is_workday(start + timedelta(days=1))
    assert cal.hours(start + timedelta(days=8)) == 4
    assert not cal.is_workday(start + timedelta(days=7))
    assert cal.is_workday(start + timedelta(days=14))
    assert cal.is_workday(start - timedelta(days=8))
    assert len(cal.workday_index()) == 28
    assert cal.workday_index().dtype == np.bool_
    assert cal.day_hours_vector().sum() == 13 * 12 + 4
    with pytest.raises(ValueError):
        cal.day_hours_vector()[0] = 0


@pytest.mark.parametrize("pattern,on,off", [("4x3", 4, 3), ("7x7", 7, 7)])
def test_shifts_against_naive(pattern, on, off):
    anchor = date(2026, 1, 5)
    cal = WorkCalendar(d.Calendar(1, "Turno", holidays_country=None, shift_pattern=pattern, shift_anchor=anchor), anchor, date(2026, 12, 31))
    for delta in range(-100, 366):
        assert cal.is_workday(anchor + timedelta(days=delta)) == (delta % (on + off) < on)


def test_inclusive_arithmetic_and_internal_margin():
    cal = WorkCalendar(d.Calendar(1, "Semana", holidays_country=None), date(2026, 1, 1), date(2026, 1, 31))
    friday, saturday, monday = date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 5)
    assert cal.add_workdays(friday, 1) == friday
    assert cal.add_workdays(friday, 2) == monday
    assert cal.add_workdays(saturday, 1) == monday
    assert cal.add_workdays(saturday, 0) == saturday
    assert cal.next_workday(friday, include=False) == monday
    assert cal.prev_workday(monday, include=False) == friday
    assert cal.workdays_between(friday, monday) == 2
    assert cal.workdays_between(monday, friday) == 0
    assert cal.add_workdays(date(2026, 1, 30), 2) == date(2026, 2, 2)
    with pytest.raises(ValueError):
        cal.add_workdays(friday, -1)
    with pytest.raises(ValueError):
        cal.hours(date(2040, 1, 1))


def test_arithmetic_matches_naive_with_holidays_and_exceptions():
    cal = WorkCalendar(d.Calendar(1, "Chile", exceptions=[d.CalendarException(date(2026, 3, 7), True, 5)]), date(2026, 1, 1), date(2026, 12, 31))
    start = date(2026, 1, 1)
    for offset in range(0, 330, 11):
        day = start + timedelta(days=offset)
        expected = day
        remaining = 20
        while remaining:
            remaining -= cal.is_workday(expected)
            if remaining:
                expected += timedelta(days=1)
        assert cal.add_workdays(day, 20) == expected
        assert cal.workdays_between(day, expected) == 20


def test_empty_calendar_and_default_idempotency(conn):
    before = repo.list_entities(conn, "calendars")
    calendars.ensure_default_calendars(conn)
    assert len(repo.list_entities(conn, "calendars")) == len(before) == 5
    cal = WorkCalendar(d.Calendar(8, "Cerrado", workweek="0000000", holidays_country=None), date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(ValueError, match="suficientes"):
        cal.next_workday(date(2026, 1, 1))


def test_load_calendars(conn, project):
    loaded = calendars.load_calendars(conn)
    assert loaded[project.calendar_id].horizon_start == project.start_date
    assert loaded[project.calendar_id].horizon_end == project.end_date
