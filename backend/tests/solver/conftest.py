"""Fixtures confined to this suite; invoke pytest with --basetemp in solver/."""
from datetime import date, timedelta

import pytest

from ganttier.calendars import WorkCalendar
from ganttier.domain import Activity, Calendar, Project, Resource, ScheduleProblem


@pytest.fixture
def case():
    def make(n=2, *, mode='proyecto', duration=1, hours=8, units=1, horizon=90):
        start = date(2026, 1, 5)
        end = start + timedelta(days=horizon)
        calendars = {1: WorkCalendar(Calendar(1, 'L-V', holidays_country=None), start, end),
                     2: WorkCalendar(Calendar(2, 'L-S', workweek='1111110', holidays_country=None), start, end)}
        acts = [Activity(i, 1, f'A{i}', f'Actividad {i}', duration_days=duration, work_hours=hours, required_skill='electrico') for i in range(1, n + 1)]
        return ScheduleProblem(Project(1, 'Prueba', mode=mode, start_date=start, calendar_id=1), acts, [],
                               [Resource(1, 'Ana', skills=['electrico'], max_units=units, calendar_id=2)], [], calendars, 1, start, end)
    return make
