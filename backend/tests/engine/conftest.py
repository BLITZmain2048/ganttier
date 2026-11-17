"""Calendario falso independiente de A1 para pruebas sin red."""
from datetime import date, timedelta

import numpy as np
import pytest

from ganttier.domain import Activity, Project, ScheduleProblem


class FakeCalendar:
    def __init__(self, start=date(2026, 1, 5), end=date(2026, 12, 31), holidays=(), weekdays=range(5), exceptions=None, bounded=False):
        self.start, self.end = start, end
        self.holidays, self.weekdays = set(holidays), set(weekdays)
        self.exceptions = exceptions or {}
        self.bounded = bounded

    def hours(self, d):
        if self.bounded and not self.start - timedelta(days=732) <= d <= self.end + timedelta(days=732):
            raise ValueError("Fecha fuera del margen del calendario.")
        return self.exceptions.get(d, 8.0 if d.weekday() in self.weekdays and d not in self.holidays else 0.0)

    def is_workday(self, d):
        return self.hours(d) > 0

    def next_workday(self, d, include=True):
        d += timedelta(days=not include)
        while not self.is_workday(d):
            d += timedelta(days=1)
        return d

    def prev_workday(self, d, include=True):
        d -= timedelta(days=not include)
        while not self.is_workday(d):
            d -= timedelta(days=1)
        return d

    def add_workdays(self, start, n):
        if not n:
            return start
        d = self.next_workday(start)
        for _ in range(n - 1):
            d = self.next_workday(d, False)
        return d

    def workdays_between(self, a, b):
        return sum(self.is_workday(a + timedelta(days=i)) for i in range((b - a).days + 1))

    def day_hours_vector(self):
        return np.array([self.hours(self.start + timedelta(days=i)) for i in range((self.end - self.start).days + 1)])

    def workday_index(self):
        return self.day_hours_vector() > 0


def activity(i, duration=1, **kwargs):
    return Activity(i, 1, chr(64 + i) if i <= 26 else f"A{i}", f"Actividad {i}", duration_days=duration, **kwargs)


def problem(acts, relations=(), resources=(), assignments=(), calendars=None, end=date(2026, 1, 30), **kwargs):
    return ScheduleProblem(Project(1, "Prueba", start_date=date(2026, 1, 5)), list(acts), list(relations), list(resources), list(assignments), calendars or {1: FakeCalendar()}, 1, date(2026, 1, 5), end, **kwargs)


@pytest.fixture
def make_problem():
    return problem
