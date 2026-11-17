"""Objetos de prueba independientes de la implementación de calendarios de A1."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from ganttier.domain import ActivityView


class FakeCalendar:
    """Calendario con la interfaz completa de SPEC §5, sin red ni feriados externos."""

    def __init__(self, horizon_start=date(2026, 1, 1), horizon_end=date(2027, 12, 31),
                 holidays=(), all_days=False):
        self.horizon_start = horizon_start
        self.horizon_end = horizon_end
        self.holidays = set(holidays)
        self.all_days = all_days

    def is_workday(self, d: date) -> bool:
        return (self.all_days or d.weekday() < 5) and d not in self.holidays

    def hours(self, d: date) -> float:
        return 8.0 if self.is_workday(d) else 0.0

    def next_workday(self, d: date, include: bool = True) -> date:
        candidate = d if include else d + timedelta(days=1)
        while not self.is_workday(candidate):
            candidate += timedelta(days=1)
        return candidate

    def prev_workday(self, d: date, include: bool = True) -> date:
        candidate = d if include else d - timedelta(days=1)
        while not self.is_workday(candidate):
            candidate -= timedelta(days=1)
        return candidate

    def add_workdays(self, start: date, n: int) -> date:
        if n == 0:
            return start
        current = self.next_workday(start)
        for _ in range(n - 1):
            current = self.next_workday(current, False)
        return current

    def workdays_between(self, a: date, b: date) -> int:
        return sum(self.is_workday(a + timedelta(days=i)) for i in range((b - a).days + 1))

    def workday_index(self):
        return np.array([self.is_workday(self.horizon_start + timedelta(days=i))
                         for i in range((self.horizon_end - self.horizon_start).days + 1)])

    def day_hours_vector(self):
        return self.workday_index() * 8.0


@pytest.fixture
def calendar():
    return FakeCalendar()


@pytest.fixture
def make_activity():
    def make(**kwargs):
        values = dict(id=1, code="A1", name="Actividad", start=date(2026, 1, 5),
                      finish=date(2026, 1, 9), duration_days=5, work_hours=40,
                      cost_budget=400, percent_complete=50, actual_start=None,
                      actual_finish=None, actual_hours=0, actual_cost=0,
                      calendar_id=1)
        values.update(kwargs)
        return ActivityView(**values)
    return make
