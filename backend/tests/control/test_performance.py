"""Casos manuales de atribución de HH y cumplimiento por trabajador."""
from datetime import date

import pytest

from ganttier.control import worker_performance
from ganttier.domain import Assignment, ProgressEntry, Resource


def d(day):
    return date(2026, 1, day)


def test_worker_shares_period_earned_hours_and_early_finish(make_activity):
    acts = [make_activity(percent_complete=100, actual_finish=d(8))]
    assignments = [Assignment(1, 1, 1, work_hours=24), Assignment(2, 1, 2, work_hours=16)]
    progress = [ProgressEntry(1, 1, d(4), 20, 9, resource_id=1),
                ProgressEntry(2, 1, d(8), 100, 20, resource_id=1),
                ProgressEntry(3, 1, d(8), 100, 10, resource_id=2)]
    results = worker_performance(acts, assignments, progress, [Resource(1, "Ana"), Resource(2, "Luis")], d(5), d(9), d(9))
    for r, planned, earned, actual in zip(results, [24, 16], [19.2, 12.8], [20, 10]):
        assert r.assigned_activities == r.completed_activities == r.completed_on_time == r.due_in_period == 1
        assert r.compliance_pct == 100 and r.pending_activities == 0
        assert r.planned_hours == planned and r.earned_hours == pytest.approx(earned)
        assert r.actual_hours == actual and r.avg_delay_days == 0
        assert r.SPI == pytest.approx(0.8)
        assert r.CPI == r.hh_efficiency == pytest.approx(earned / actual)


def test_due_denominator_pending_and_average_includes_early_finish(make_activity):
    acts = [make_activity(id=1, percent_complete=100, actual_finish=d(8)),
            make_activity(id=2, percent_complete=50),
            make_activity(id=3, finish=d(15), percent_complete=100, actual_finish=d(8))]
    assignments = [Assignment(i, i, 1, work_hours=40) for i in range(1, 4)]
    result = worker_performance(acts, assignments, [], [Resource(1, "Ana")], d(5), d(9), d(12))[0]
    assert result.assigned_activities == 3 and result.completed_activities == 2
    assert result.due_in_period == 2 and result.completed_on_time == 1
    assert result.compliance_pct == 50 and result.pending_activities == 1
    assert result.avg_delay_days == 1.5  # (0 + 3) / 2, adelanto no cancela atraso.


def test_duplicates_unassigned_reporter_and_resource_filters(make_activity):
    acts = [make_activity(percent_complete=100, actual_finish=d(9)),
            make_activity(id=2, percent_complete=50)]
    assignments = [Assignment(1, 1, 1, work_hours=8), Assignment(2, 1, 1, work_hours=12),
                   Assignment(3, 1, 2, work_hours=20)]
    progress = [ProgressEntry(1, 1, d(9), 100, 10, resource_id=3),
                ProgressEntry(2, 2, d(9), 50, 5, resource_id=1),
                ProgressEntry(3, 2, d(9), 50, 6, resource_id=3)]
    resources = [Resource(1, "Ana"), Resource(2, "Luis"), Resource(3, "Eva"),
                 Resource(4, "Inactivo", active=False), Resource(5, "Equipo", kind="equipo")]
    a, b, c, idle = worker_performance(acts, assignments, progress, resources, d(5), d(9), d(9))
    assert a.assigned_activities == 2 and a.planned_hours == 20 and a.earned_hours == 30
    assert b.assigned_activities == 1 and b.earned_hours == 20
    assert c.assigned_activities == 2 and c.planned_hours == 0 and c.earned_hours == 10
    assert c.actual_hours == 16  # Reportero adicional no duplica el EV ya asignado.
    assert idle.assigned_activities == 0 and idle.SPI is idle.CPI is idle.compliance_pct is None
    assert sum(r.earned_hours for r in [a, b, c]) == 60


def test_default_assignment_hours_and_partial_period(make_activity):
    a = make_activity(percent_complete=50)
    assignments = [Assignment(1, 1, 1, units=0.5)]
    result = worker_performance([a], assignments, [], [Resource(1, "Ana", capacity_hours_day=6)],
                                d(6), d(9), d(7))[0]
    assert result.planned_hours == 12  # .5 * 5 * 6 * 4/5
    assert result.earned_hours == 20
    assert result.SPI == pytest.approx(20 / 16)  # PV del mismo presupuesto que EV: 2/5 de 40 HH.


def test_historical_period_uses_steps_and_no_future_consumption(make_activity):
    records = [ProgressEntry(1, 1, d(5), 20, 2, resource_id=1),
               ProgressEntry(2, 1, d(6), 40, 3, resource_id=1),
               ProgressEntry(3, 1, d(7), 60, 5, resource_id=1),
               ProgressEntry(4, 1, d(9), 90, 100, resource_id=1),
               ProgressEntry(5, 1, d(12), 100, 1000, resource_id=1)]
    args = ([make_activity(percent_complete=80)], [Assignment(1, 1, 1, work_hours=40)],
            records, [Resource(1, "Ana")])
    historical = worker_performance(*args, d(6), d(7), d(9))[0]
    assert historical.earned_hours == 16 and historical.actual_hours == 8
    assert historical.planned_hours == 16 and historical.SPI == 1
    future = worker_performance(*args, d(10), d(13), d(9))[0]
    assert future.earned_hours == 0 and future.actual_hours == 0


def test_generic_assignment_keeps_its_share(make_activity):
    assignments = [Assignment(1, 1, 1, work_hours=20), Assignment(2, 1, None, work_hours=20)]
    result = worker_performance([make_activity()], assignments, [], [Resource(1, "Ana")], d(5), d(9), d(9))[0]
    assert result.earned_hours == 10 and result.planned_hours == 20


def test_unknown_references_empty_and_no_calendar_dates(make_activity):
    resources = [Resource(1, "Ana")]
    assert worker_performance([], [], [], [], d(5), d(9), d(9)) == []
    empty = worker_performance([], [Assignment(1, 99, 1)], [ProgressEntry(1, 99, d(9), 50)], resources, d(5), d(9), d(9))[0]
    assert empty.assigned_activities == empty.earned_hours == empty.actual_hours == 0
    a = make_activity(start=None, finish=None)
    unscheduled = worker_performance([a], [Assignment(1, 1, 1, work_hours=40)], [], resources, d(5), d(9), d(9))[0]
    assert unscheduled.planned_hours == 0 and unscheduled.due_in_period == 0
    assert unscheduled.earned_hours == 20 and unscheduled.pending_activities == 1


def test_missing_actual_finish_never_fabricates_on_time_completion(make_activity):
    result = worker_performance([make_activity(percent_complete=100)], [Assignment(1, 1, 1)], [],
                                [Resource(1, "Ana")], d(5), d(9), d(12))[0]
    assert result.completed_activities == 1 and result.completed_on_time == 0
    assert result.avg_delay_days == 3


def test_invalid_worker_inputs(make_activity):
    with pytest.raises(ValueError, match="período"):
        worker_performance([], [], [], [], d(9), d(5), d(9))
    with pytest.raises(ValueError, match="únicos"):
        worker_performance([], [], [], [Resource(1, "Ana"), Resource(1, "Luis")], d(5), d(9), d(9))
    with pytest.raises(ValueError, match="asignadas"):
        worker_performance([make_activity()], [Assignment(1, 1, 1, work_hours=-1)], [], [Resource(1, "Ana")], d(5), d(9), d(9))
