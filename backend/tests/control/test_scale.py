"""Criterio A4: 100.000 actividades por llamada en menos de cinco segundos."""
from datetime import date, timedelta
from time import perf_counter

import pytest

from ganttier.control import scurve, worker_performance
from ganttier.domain import Assignment, ProgressEntry, Resource


@pytest.fixture(scope="module")
def large_case():
    from ganttier.domain import ActivityView
    origin = date(2026, 1, 5)
    acts, assignments, progress = [], [], []
    resources = [Resource(i, f"Trabajador {i}") for i in range(100)]
    for i in range(100_000):
        start = origin + timedelta(days=i % 365)
        finish = start + timedelta(days=4)
        acts.append(ActivityView(i, str(i), "Actividad", start, finish, 5, 40, 400, 50,
                                 None, None, 0, 0, 1))
        assignments.append(Assignment(i, i, i % 100, work_hours=40))
        progress.append(ProgressEntry(i, i, start, 50, 20, 200, i % 100))
    return acts, assignments, progress, resources


def test_scurve_100000(calendar, large_case):
    acts, _, progress, _ = large_case
    started = perf_counter()
    curve = scurve(acts, None, progress, calendar, date(2026, 7, 1), "week")
    elapsed = perf_counter() - started
    print(f"scurve 100000: {elapsed:.3f} s")
    assert curve.budget_total == 4_000_000
    assert curve.forecast_abs[-1] == pytest.approx(4_000_000)
    assert curve.planned_cum[-1] == 100
    assert elapsed < 5, f"scurve tardó {elapsed:.3f} s; máximo 5 s."


def test_worker_performance_100000(large_case):
    acts, assignments, progress, resources = large_case
    started = perf_counter()
    results = worker_performance(acts, assignments, progress, resources,
                                 date(2026, 1, 1), date(2026, 12, 31), date(2026, 12, 31))
    elapsed = perf_counter() - started
    print(f"worker_performance 100000: {elapsed:.3f} s")
    assert len(results) == 100
    assert sum(r.assigned_activities for r in results) == 100_000
    # El conjunto incluye registros de enero de 2027: no pertenecen al corte.
    expected = sum(20 for p in progress if p.date <= date(2026, 12, 31))
    assert sum(r.earned_hours for r in results) == expected
    assert elapsed < 5, f"worker_performance tardó {elapsed:.3f} s; máximo 5 s."
