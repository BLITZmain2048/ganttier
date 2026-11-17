"""Casos de valor ganado y curvas S con resultados calculados a mano."""
from datetime import date

import numpy as np
import pytest

from ganttier.control import evm, scurve
from ganttier.domain import BaselineActivity, ProgressEntry, to_dict


def d(day):
    return date(2026, 1, day)


def test_single_activity_evm_and_daily_curve(calendar, make_activity):
    acts = [make_activity()]
    progress = [ProgressEntry(1, 1, d(5), 20, 4, 40),
                ProgressEntry(2, 1, d(6), 25, 6, 60),
                ProgressEntry(3, 1, d(7), 40, 15, 150),
                ProgressEntry(4, 1, d(8), 99, 1000, 1000)]
    result = evm(acts, None, progress, calendar, d(7))
    # El último registro al corte es 40%; el snapshot (50%) no lo sustituye.
    assert (result.BAC, result.PV, result.EV, result.AC) == (40, 24, 16, 25)
    assert (result.SV, result.CV, result.EAC, result.ETC, result.VAC) == (-8, -9, 62.5, 37.5, -22.5)
    assert result.SPI == pytest.approx(2 / 3)
    assert result.CPI == 0.64
    assert result.TCPI == pytest.approx(24 / 15)
    assert result.ES_days == 2
    assert result.SPI_t == pytest.approx(2 / 3)
    assert result.forecast_finish == d(12)
    curve = scurve(acts, None, progress, calendar, d(7), "day")
    assert curve.labels == [d(i).isoformat() for i in range(5, 13)]
    assert curve.planned_abs == [8, 16, 24, 32, 40, 40, 40, 40]
    assert curve.earned_abs == [8, 10, 16, None, None, None, None, None]
    assert curve.actual_abs == [4, 10, 25, None, None, None, None, None]
    assert curve.forecast_abs == [None, None, 16, 24, 32, 32, 32, 40]
    assert curve.forecast_cum == [None, None, 40, 60, 80, 80, 80, 100]
    assert curve.forecast_finish == result.forecast_finish
    assert to_dict(result)["forecast_finish"] == "2026-01-12"


def test_baseline_two_activities_and_replanned_forecast(calendar, make_activity):
    acts = [make_activity(start=d(8), finish=d(12), work_hours=100),
            make_activity(id=2, code="A2", start=d(12), finish=d(13), duration_days=2, percent_complete=0)]
    baseline = {1: BaselineActivity(1, 1, d(5), d(6), 20, 200, 2),
                2: BaselineActivity(1, 2, d(7), d(8), 60, 600, 2)}
    result = evm(acts, baseline, [], calendar, d(6))
    assert (result.BAC, result.PV, result.EV, result.AC) == (80, 20, 10, 0)
    assert result.planned_finish == d(8)
    assert result.ES_days == 1
    assert result.SPI_t == 0.5
    assert result.forecast_finish == d(12)
    curve = scurve(acts, baseline, [], calendar, d(6), "day")
    by_date = dict(zip(curve.labels, curve.forecast_abs))
    assert by_date[d(6).isoformat()] == 10
    assert by_date[d(8).isoformat()] == pytest.approx(10 + 10 / 3)
    assert by_date[d(12).isoformat()] == pytest.approx(50)
    assert by_date[d(13).isoformat()] == 80


@pytest.mark.parametrize("pct,cut,es,spi,finish", [
    (80, 6, 4, 2, 7), (40, 7, 2, 2 / 3, 12), (50, 14, 2.5, 0.25, 24),
])
def test_earned_schedule_ahead_behind_and_after_plan(calendar, make_activity, pct, cut, es, spi, finish):
    result = evm([make_activity(percent_complete=pct)], None, [], calendar, d(cut))
    assert result.ES_days == es
    assert result.SPI_t == pytest.approx(spi)
    assert result.forecast_finish == d(finish)


def test_zero_progress_no_consumption_and_overdue_remainder(calendar, make_activity):
    activity = make_activity(percent_complete=0)
    result = evm([activity], None, [], calendar, d(9))
    assert result.CPI is None
    assert result.SPI_t == 0
    assert result.forecast_finish is None
    assert result.EAC == result.ETC == result.BAC == 40
    curve = scurve([activity], None, [], calendar, d(9), "day")
    assert curve.labels[-1] == d(16).isoformat()
    assert curve.forecast_abs[-5:] == [8, 16, 24, 32, 40]


def test_complete_and_pre_start_cases(calendar, make_activity):
    result = evm([make_activity(percent_complete=100)], None, [], calendar, d(12))
    assert result.ES_days == 5
    assert result.forecast_finish == d(12)
    assert result.EAC == result.ETC == 0
    before = evm([make_activity(percent_complete=0)], None, [], calendar, d(4))
    assert before.SPI is None and before.SPI_t is None
    assert before.PV == 0 and before.ES_days == 0


def test_cost_count_and_zero_hours(calendar, make_activity):
    a = make_activity(work_hours=0)
    p = [ProgressEntry(1, 1, d(7), 50, 3, 250)]
    hours = evm([a], None, p, calendar, d(7))
    costs = evm([a], None, p, calendar, d(7), "cost")
    count = evm([a], None, p, calendar, d(7), "count")
    assert (hours.BAC, hours.PV, hours.EV, hours.AC) == (0, 0, 0, 3)
    assert (costs.BAC, costs.PV, costs.EV, costs.AC) == (400, 240, 200, 250)
    assert count.BAC == 1 and count.EV == 0.5 and count.AC == 0
    assert count.CPI is None


def test_milestone_on_weekend(calendar, make_activity):
    a = make_activity(start=d(10), finish=d(10), duration_days=0, work_hours=0, percent_complete=0)
    curve = scurve([a], None, [], calendar, d(9), "day", "count")
    assert curve.planned_abs == [0, 1]
    assert curve.forecast_abs == [0, 1]
    zero = evm([a], None, [], calendar, d(10))
    assert zero.BAC == zero.EV == zero.percent_complete == 0
    assert zero.SPI is zero.CPI is zero.TCPI is zero.forecast_finish is None


def test_holiday_and_nonworking_interval(calendar, make_activity):
    calendar.holidays.add(d(7))
    curve = scurve([make_activity(percent_complete=0, duration_days=4)], None, [], calendar, d(4), "day")
    assert curve.planned_abs == [0, 10, 20, 20, 30, 40]
    a = make_activity(start=d(10), finish=d(11), duration_days=1, percent_complete=0)
    weekend = scurve([a], None, [], calendar, d(9), "day")
    assert weekend.planned_abs[:3] == [0, 0, 40]
    assert weekend.forecast_finish == d(12)


def test_duplicate_snapshots_and_downward_corrections(calendar, make_activity):
    a = make_activity(percent_complete=40)
    records = [ProgressEntry(4, 1, d(6), 25, 2), ProgressEntry(2, 1, d(5), 50, 3),
               ProgressEntry(1, 1, d(5), 20, 5), ProgressEntry(3, 1, d(6), 30, 1),
               ProgressEntry(99, 999, d(1), 100, 999)]
    curve = scurve([a], None, records, calendar, d(7), "day")
    assert curve.earned_abs[:3] == [20, 10, 10]
    assert curve.actual_abs[:3] == [8, 11, 11]
    assert curve.labels[0] == d(5).isoformat()


def test_week_and_month_labels_and_strict_cutoff(calendar, make_activity):
    a = make_activity(start=d(1), finish=date(2026, 2, 6), duration_days=27)
    week = scurve([a], None, [], calendar, d(7), "week")
    assert all(date.fromisoformat(label).weekday() == 6 or label == d(7).isoformat() for label in week.labels)
    assert week.earned_abs[2] is None and week.actual_abs[2] is None
    assert week.forecast_abs[0] is None
    month = scurve([a], None, [], calendar, d(7), "month")
    assert month.labels == ["2026-01-07", "2026-01-31", "2026-02-28"]
    assert month.earned_abs == [20, None, None]
    assert month.forecast_cum[-1] == 100
    exact = scurve([a], None, [], calendar, d(4), "week")
    assert exact.earned_abs[0] == exact.forecast_abs[0] == 20


def test_partial_baseline_keeps_approved_scope(calendar, make_activity):
    acts = [make_activity(), make_activity(id=2)]
    baseline = {1: BaselineActivity(1, 1, d(5), d(9), 20, 200, 5)}
    assert evm(acts, baseline, [], calendar, d(7)).BAC == 20


def test_empty_inputs(calendar):
    curve = scurve([], None, [], calendar, d(7))
    assert curve.labels == curve.planned_abs == []
    assert curve.budget_total == 0
    result = evm([], None, [], calendar, d(7))
    assert result.PV == result.EV == result.AC == result.BAC == 0
    assert result.forecast_finish is None


def test_fractional_current_progress_without_history(calendar, make_activity):
    curve = scurve([make_activity(work_hours=5, percent_complete=50)], None, [], calendar, d(7), "day")
    assert curve.earned_abs[2] == 2.5
    assert curve.earned_cum[2] == 50
    assert curve.forecast_abs[2] == curve.earned_abs[2]


def test_earned_schedule_inverts_weekend_plateau(calendar, make_activity):
    a = make_activity(start=d(9), finish=d(12), work_hours=16)
    result = evm([a], None, [], calendar, d(11))
    assert result.PV == result.EV == 8
    assert result.ES_days == 3  # Último período con PV <= EV, antes del siguiente incremento.
    assert result.SPI_t == 1
    assert result.forecast_finish == d(12)


def test_tiny_spi_unrepresentable_finish_is_none(calendar, make_activity):
    result = evm([make_activity(percent_complete=1e-12)], None, [], calendar, d(9))
    assert result.SPI_t > 0 and result.forecast_finish is None


def test_missing_current_dates_with_valid_baseline(calendar, make_activity):
    a = make_activity(start=None, finish=None)
    baseline = {1: BaselineActivity(1, 1, d(5), d(9), 40, 400, 5)}
    assert evm([a], baseline, [], calendar, d(7)).PV == 24
    with pytest.raises(ValueError, match="remanente"):
        scurve([a], baseline, [], calendar, d(7))


@pytest.mark.parametrize("change", [dict(work_hours=-1), dict(percent_complete=101),
                                       dict(percent_complete=float("nan")), dict(start=d(10)),
                                       dict(finish=None), dict(cost_budget=float("inf"))])
def test_invalid_activity_inputs(calendar, make_activity, change):
    with pytest.raises(ValueError):
        scurve([make_activity(**change)], None, [], calendar, d(7), basis="cost" if "cost_budget" in change else "hours")


def test_invalid_options_ids_and_progress(calendar, make_activity):
    a = make_activity()
    with pytest.raises(ValueError, match="período"):
        scurve([a], None, [], calendar, d(7), period="year")
    with pytest.raises(ValueError, match="base"):
        evm([a], None, [], calendar, d(7), basis="foo")
    with pytest.raises(ValueError, match="únicos"):
        evm([a, a], None, [], calendar, d(7))
    with pytest.raises(ValueError, match="reales"):
        evm([a], None, [ProgressEntry(1, 1, d(7), 50, -1)], calendar, d(7))
    with pytest.raises(ValueError, match="línea base"):
        evm([a], {1: BaselineActivity(1, 2, d(5), d(9), 40, 400, 5)}, [], calendar, d(7))


def test_cpi_zero_and_tcpi_zero_denominator(calendar, make_activity):
    result = evm([make_activity(percent_complete=0)], None,
                 [ProgressEntry(1, 1, d(7), 0, 40)], calendar, d(7))
    assert result.CPI == 0 and result.TCPI is None
    assert result.EAC is result.ETC is result.VAC is None


def test_random_plan_against_simple_reference(calendar, make_activity):
    rng = np.random.default_rng(140)
    acts = []
    for i in range(100):
        start = int(rng.integers(1, 25))
        finish = int(rng.integers(start, 29))
        acts.append(make_activity(id=i, start=d(start), finish=d(finish),
                                  work_hours=float(rng.integers(1, 100)), percent_complete=0,
                                  duration_days=int(rng.integers(0, 4))))
    curve = scurve(acts, None, [], calendar, d(1), "day")
    daily = np.zeros(28)
    for a in acts:
        days = [i for i in range(a.start.day, a.finish.day + 1) if calendar.is_workday(d(i))]
        if a.duration_days == 0 or not days:
            daily[a.finish.day - 1] += a.work_hours
        else:
            daily[np.array(days) - 1] += a.work_hours / len(days)
    assert np.allclose(curve.planned_abs, daily.cumsum())
