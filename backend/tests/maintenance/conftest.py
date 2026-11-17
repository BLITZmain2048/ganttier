"""Calendario autónomo para probar A3 sin depender del trabajo de A1."""
from datetime import date, timedelta

import numpy as np
import pytest


class FakeCalendar:
    def __init__(self, start: date, end: date, weekdays: tuple[int, ...] = (0, 1, 2, 3, 4),
                 excluded: tuple[date, ...] = (), shift: tuple[int, int] | None = None):
        self.horizon_start, self.horizon_end = start, end
        self.weekdays, self.excluded, self.shift = weekdays, set(excluded), shift

    def is_workday(self, d: date) -> bool:
        if d < self.horizon_start or d > self.horizon_end or d in self.excluded:
            return False
        if self.shift:
            on, off = self.shift
            return (d - self.horizon_start).days % (on + off) < on
        return d.weekday() in self.weekdays

    def hours(self, d: date) -> float:
        return 8.0 if self.is_workday(d) else 0.0

    def next_workday(self, d: date, include: bool = True) -> date:
        d += timedelta(days=not include)
        while d <= self.horizon_end:
            if self.is_workday(d):
                return d
            d += timedelta(days=1)
        raise ValueError("No hay un día laborable posterior.")

    def prev_workday(self, d: date, include: bool = True) -> date:
        d -= timedelta(days=not include)
        while d >= self.horizon_start:
            if self.is_workday(d):
                return d
            d -= timedelta(days=1)
        raise ValueError("No hay un día laborable anterior.")

    def add_workdays(self, start: date, n: int) -> date:
        if n == 0:
            return start
        result = self.next_workday(start)
        for _ in range(n - 1):
            result = self.next_workday(result, False)
        return result

    def workdays_between(self, a: date, b: date) -> int:
        return sum(self.is_workday(a + timedelta(days=i)) for i in range((b - a).days + 1))

    def workday_index(self) -> np.ndarray:
        return self.day_hours_vector() > 0

    def day_hours_vector(self) -> np.ndarray:
        return np.array([self.hours(self.horizon_start + timedelta(days=i))
                         for i in range((self.horizon_end - self.horizon_start).days + 1)])


@pytest.fixture
def calendars():
    return {1: FakeCalendar(date(2024, 1, 1), date(2028, 12, 31))}
