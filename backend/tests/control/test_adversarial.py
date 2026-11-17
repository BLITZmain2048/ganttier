"""Regresiones de la revisión cruzada: oráculos independientes del motor."""
from datetime import date

import pytest

from ganttier.control import evm, scurve, worker_performance
from ganttier.domain import Assignment, BaselineActivity, ProgressEntry, Resource


def d(day):
    return date(2026, 1, day)


def test_zero_hours_are_not_duration_units(calendar, make_activity):
    result = evm([make_activity(work_hours=0)], None, [], calendar, d(7))
    assert result.BAC == result.PV == result.EV == 0
    assert result.SPI_t is None


@pytest.mark.parametrize("records, expected", [
    ([ProgressEntry(1, 1, d(9), 100, 40)], 0),
    ([ProgressEntry(1, 1, d(6), 25, 10), ProgressEntry(2, 1, d(9), 100, 30)], 10),
])
def test_future_snapshot_cannot_leak_into_ev(calendar, make_activity, records, expected):
    result = evm([make_activity(percent_complete=100)], None, records, calendar, d(7))
    assert result.EV == expected
    assert result.AC == expected


def test_partial_baseline_does_not_invent_approved_budget(calendar, make_activity):
    acts = [make_activity(), make_activity(id=2, work_hours=1000)]
    baseline = {1: BaselineActivity(1, 1, d(5), d(9), 40, 400, 5)}
    result = evm(acts, baseline, [ProgressEntry(1, 2, d(7), 50, 50)], calendar, d(7))
    assert (result.BAC, result.PV, result.EV, result.AC) == (40, 24, 20, 0)


def test_empty_active_baseline_is_not_current_schedule(calendar, make_activity):
    assert evm([make_activity()], {}, [], calendar, d(7)).BAC == 0


def test_es_plateau_uses_last_complete_period(calendar, make_activity):
    a = make_activity(start=d(9), finish=d(12), work_hours=16)
    result = evm([a], None, [], calendar, d(11))
    # PV = [8, 8, 8, 16], C=3, I=0 at EV=8.
    assert result.ES_days == 3
    assert result.SPI_t == 1
    assert result.forecast_finish == d(12)


def test_completed_project_does_not_keep_aging(calendar, make_activity):
    a = make_activity(percent_complete=100, actual_finish=d(8))
    result = evm([a], None, [ProgressEntry(1, 1, d(8), 100, 40)], calendar, d(20))
    assert result.ES_days == 5
    assert result.SPI_t == pytest.approx(5 / 4)
    assert result.forecast_finish == d(8)


def test_exhausted_budget_has_no_negative_tcpi(calendar, make_activity):
    result = evm([make_activity()], None, [ProgressEntry(1, 1, d(7), 50, 60)], calendar, d(7))
    assert result.TCPI is None


def test_zero_cpi_does_not_silently_switch_forecast_model(calendar, make_activity):
    result = evm([make_activity(percent_complete=0)], None,
                 [ProgressEntry(1, 1, d(7), 0, 10)], calendar, d(7))
    assert result.CPI == 0
    assert result.EAC is result.ETC is result.VAC is None


@pytest.mark.parametrize("period", ["day", "week", "month"])
def test_forecast_contains_exact_cutoff_anchor(calendar, make_activity, period):
    curve = scurve([make_activity()], None, [], calendar, d(7), period)
    i = curve.labels.index(d(7).isoformat())
    assert curve.forecast_abs[i] == curve.earned_abs[i] == 20
    assert curve.forecast_cum[-1] == 100


def test_overdue_remainder_keeps_working_duration(calendar, make_activity):
    curve = scurve([make_activity(percent_complete=0)], None, [], calendar, d(9), "day")
    values = dict(zip(curve.labels, curve.forecast_abs))
    assert values[d(12).isoformat()] == 8
    assert values[d(16).isoformat()] == 40


def test_worker_future_progress_is_pending(make_activity):
    result = worker_performance([make_activity(percent_complete=100, actual_finish=d(9))],
        [Assignment(1, 1, 1, work_hours=40)], [ProgressEntry(1, 1, d(9), 100, 40, resource_id=1)],
        [Resource(1, "Ana")], d(5), d(9), d(7))[0]
    assert result.completed_activities == result.earned_hours == result.actual_hours == 0
    assert result.pending_activities == 1


def test_untagged_hours_are_shared_once_and_not_dropped(make_activity):
    assignments = [Assignment(1, 1, 1, work_hours=10), Assignment(2, 1, 1, work_hours=10),
                   Assignment(3, 1, 2, work_hours=20)]
    progress = [ProgressEntry(1, 1, d(6), 25, 8), ProgressEntry(2, 1, d(7), 50, 12),
                ProgressEntry(3, 1, d(7), 50, 3, resource_id=1)]
    results = worker_performance([make_activity()], assignments, progress,
                                [Resource(1, "Ana"), Resource(2, "Luis")], d(5), d(9), d(7))
    assert [r.actual_hours for r in results] == [13, 10]
    assert sum(r.earned_hours for r in results) == 20
    assert [r.assigned_activities for r in results] == [1, 1]


def test_average_delay_cannot_cancel_lateness_with_early_completion(make_activity):
    acts = [make_activity(id=1, percent_complete=100, actual_finish=d(5)),
            make_activity(id=2, percent_complete=0)]
    result = worker_performance(acts, [Assignment(1, 1, 1), Assignment(2, 2, 1)], [],
                                [Resource(1, "Ana")], d(5), d(9), d(12))[0]
    assert result.avg_delay_days == 1.5  # (0 + 3) / 2


def test_history_actual_finish_supplies_compliance(make_activity):
    result = worker_performance([make_activity(percent_complete=100)], [Assignment(1, 1, 1)],
        [ProgressEntry(1, 1, d(8), 100, 40, resource_id=1, actual_finish=d(8))],
        [Resource(1, "Ana")], d(5), d(9), d(12))[0]
    assert result.completed_on_time == 1


def test_milestone_is_zero_or_one_hundred(calendar, make_activity):
    a = make_activity(start=d(7), finish=d(7), duration_days=0, percent_complete=50)
    assert evm([a], None, [], calendar, d(7), "count").EV == 0


def test_worker_spi_compares_the_same_budget_as_ev(make_activity):
    result = worker_performance([make_activity(percent_complete=100)],
        [Assignment(1, 1, 1, work_hours=10)], [], [Resource(1, "Ana")], d(5), d(9), d(9))[0]
    assert result.planned_hours == 10
    assert result.earned_hours == 40
    assert result.SPI == 1


def test_forecast_respects_slower_earned_schedule(calendar, make_activity):
    acts = [make_activity(percent_complete=50)]
    result = evm(acts, None, [], calendar, d(14))
    curve = scurve(acts, None, [], calendar, d(14), "day")
    assert curve.forecast_finish >= result.forecast_finish
    i = curve.labels.index(curve.forecast_finish.isoformat())
    assert curve.forecast_cum[i] == 100
    assert curve.forecast_cum[i - 1] < 100


def test_explicit_finish_is_consistent_between_evm_and_worker(make_activity, calendar):
    acts = [make_activity(percent_complete=50, actual_finish=d(8))]
    records = [ProgressEntry(1, 1, d(8), 50, 40, resource_id=1)]
    result = worker_performance(acts, [Assignment(1, 1, 1, work_hours=40)], records,
                                [Resource(1, "Ana")], d(5), d(9), d(9))[0]
    # El último registro reabre el trabajo; el cierre de la ficha queda obsoleto.
    assert result.earned_hours == evm(acts, None, records, calendar, d(9)).EV == 20
