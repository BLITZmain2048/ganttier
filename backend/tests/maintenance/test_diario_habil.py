from datetime import date
from ganttier.domain import PeriodicTask
from ganttier.maintenance import generate_occurrences
from .conftest import FakeCalendar


def test_daily_workdays_skips_closed_days_and_preserves_calendar_indices():
    start, end = date(2026, 11, 1), date(2026, 11, 30)
    closed = date(2026, 11, 4)
    cal = FakeCalendar(start, end, excluded=(closed,))
    task = PeriodicTask(1, 1, 'D', 'Ronda', frequency='DH', first_due=start,
                        duration_days=1, tol_before_days=0, tol_after_days=0)
    rows = generate_occurrences([task], start, end, {1: cal}, 1)
    assert len(rows) == 20
    assert all(r.due_date.weekday() < 5 and r.due_date != closed for r in rows)
    assert all(r.planned_start == r.planned_finish == r.due_date for r in rows)
    sub = generate_occurrences([task], date(2026, 11, 16), end, {1: cal}, 1)
    assert [(r.code, r.due_date) for r in sub] == [(r.code, r.due_date) for r in rows if r.due_date >= date(2026, 11, 16)]
    task.frequency = 'D'
    legacy = generate_occurrences([task], start, end, {1: cal}, 1)
    assert len(legacy) == 30
    assert sum(r.planned_start is None for r in legacy) == 10


def test_daily_workdays_uses_task_calendar_and_keeps_impossible_duration():
    start, end = date(2026, 11, 2), date(2026, 11, 8)
    task = PeriodicTask(1, 1, 'D', 'Ronda', frequency='DH', calendar_id=2,
                        duration_days=2, tol_before_days=0, tol_after_days=0)
    calendars = {1: FakeCalendar(start, end), 2: FakeCalendar(start, end, excluded=(start,))}
    rows = generate_occurrences([task], start, end, calendars, 1)
    assert len(rows) == 4
    assert all(r.planned_start is None for r in rows)
