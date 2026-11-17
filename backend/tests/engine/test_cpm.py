"""Redes verificadas a mano, restricciones, avance e integración del calendario."""
from datetime import date, timedelta
from time import perf_counter

import pytest

from ganttier.domain import Relation
from ganttier.engine import compute_cpm, validate_network
from .conftest import FakeCalendar, activity, problem


def test_book_network_all_relations_and_lags():
    # A: lu 5–mi 7; B FS+1: vi 9–lu 12; C SS+1: ma 6–mi 7;
    # D FF+1 desde B: lu 12–ma 13; E SF+3 desde C: ju 8–vi 9.
    acts = [activity(1, 3), activity(2, 2), activity(3, 2), activity(4, 2), activity(5, 2)]
    rels = [Relation(1, 1, 1, 2, "FS", 1), Relation(2, 1, 1, 3, "SS", 1), Relation(3, 1, 2, 4, "FF", 1), Relation(4, 1, 3, 5, "SF", 3)]
    result = compute_cpm(problem(acts, rels))
    expected = [(5, 7, 0, 0), (9, 12, 0, 0), (6, 7, 2, 0), (12, 13, 0, 0), (8, 9, 2, 2)]
    for a, (s, f, total, free) in zip(acts, expected):
        row = result.results[a.id]
        assert row.early_start == date(2026, 1, s)
        assert row.early_finish == date(2026, 1, f)
        assert row.total_float == total
        assert row.free_float == free
    assert result.critical_path == [1, 2, 4]
    assert not result.warnings


@pytest.mark.parametrize("kind,lag,start", [("FS", -1, 7), ("SS", -1, 5), ("FF", -1, 5), ("SF", 3, 7)])
def test_negative_lags(kind, lag, start):
    p = problem([activity(1, 3), activity(2, 2)], [Relation(1, 1, 1, 2, kind, lag)])
    assert compute_cpm(p).results[2].early_start == date(2026, 1, start)


def test_holiday_and_successor_calendar():
    calendars = {1: FakeCalendar(), 2: FakeCalendar(holidays=[date(2026, 1, 8)])}
    p = problem([activity(1, 3), activity(2, 2, calendar_id=2)], [Relation(1, 1, 1, 2)], calendars=calendars)
    result = compute_cpm(p)
    assert result.results[2].early_start == date(2026, 1, 9)
    assert result.project_finish == date(2026, 1, 12)


def test_cycle_and_broken_reference():
    p = problem([activity(1), activity(2)], [Relation(1, 1, 1, 2), Relation(2, 1, 2, 1), Relation(3, 1, 2, 999)])
    errors = validate_network(p)
    assert any("Ciclo" in v and "A" in v and "B" in v for v in errors)
    assert any("inexistente" in v for v in errors)
    with pytest.raises(ValueError, match="Ciclo"):
        compute_cpm(p)


@pytest.mark.parametrize("kind,day,s,f", [("SNET", 8, 8, 9), ("FNET", 9, 8, 9), ("MSO", 8, 8, 9), ("MFO", 9, 8, 9)])
def test_date_constraints(kind, day, s, f):
    p = problem([activity(1, 2, constraint_type=kind, constraint_date=date(2026, 1, day))])
    row = compute_cpm(p).results[1]
    assert row.early_start == date(2026, 1, s)
    assert row.early_finish == date(2026, 1, f)


@pytest.mark.parametrize("kind", ["SNLT", "FNLT"])
def test_negative_float_warns(kind):
    p = problem([activity(1, 3), activity(2, 2, constraint_type=kind, constraint_date=date(2026, 1, 6))], [Relation(1, 1, 1, 2)])
    result = compute_cpm(p)
    assert result.results[2].total_float < 0
    assert any("holgura negativa" in warning for warning in result.warnings)


def test_alap_preserves_successor_early_date():
    p = problem([activity(1, 2, constraint_type="ALAP"), activity(2), activity(3, 10)], [Relation(1, 1, 1, 2, "SS", 1)])
    result = compute_cpm(p)
    assert result.results[1].early_start == date(2026, 1, 5)
    assert result.results[2].early_start == date(2026, 1, 6)
    assert not result.warnings


def test_progress_cutoff_remaining_and_milestone():
    p = problem([
        activity(1, 5, status="terminada", percent_complete=100, actual_start=date(2026, 1, 1), actual_finish=date(2026, 1, 2)),
        activity(2, 10, status="en_curso", percent_complete=50, remaining_duration=2, actual_start=date(2026, 1, 5)),
        activity(3, 0), activity(4, 2),
    ], [Relation(1, 1, 2, 3)], data_date=date(2026, 1, 12))
    r = compute_cpm(p)
    assert r.results[1].early_finish == date(2026, 1, 2)
    assert r.results[2].early_start == date(2026, 1, 5)
    # Corte al final del 12: el remanente (2 días) ocupa 13 y 14; lo no iniciado parte el 13.
    assert r.results[2].early_finish == date(2026, 1, 14)
    assert r.results[3].early_start == r.results[3].early_finish == date(2026, 1, 15)
    assert r.results[4].early_start == date(2026, 1, 13)


def test_locked_and_hard_window():
    p = problem([activity(1, 2, locked=True, planned_start=date(2026, 1, 8)), activity(2, 2, window_hard=True, window_start=date(2026, 1, 12), window_end=date(2026, 1, 13))])
    r = compute_cpm(p)
    assert r.results[1].early_start == date(2026, 1, 8)
    assert r.results[2].early_finish == date(2026, 1, 13)


def test_empty_network():
    r = compute_cpm(problem([]))
    assert r.results == {}
    assert r.critical_path == []


def test_bounded_calendar_long_chain():
    # A1 exposes only a bounded margin; asking for an invented multi-year pad fails.
    start, end = date(2026, 1, 5), date(2028, 1, 5)
    p = problem([activity(i) for i in range(1, 401)], [Relation(i, 1, i, i + 1) for i in range(1, 400)], calendars={1: FakeCalendar(start, end, bounded=True)}, end=end)
    assert len(compute_cpm(p).results) == 400


def test_cpm_10000_15000_under_five_seconds():
    n = 10000
    acts = [activity(i) for i in range(1, n + 1)]
    # 100 layers, many parallel paths, exactly 15k distinct forward edges.
    edges = [(i, i + 100) for i in range(1, n - 99)]
    edges += [(i, i + 101) for i in range(1, 5101)]
    rels = [Relation(k, 1, p, s) for k, (p, s) in enumerate(edges)]
    begin = perf_counter()
    r = compute_cpm(problem(acts, rels, end=date(2027, 1, 5)))
    elapsed = perf_counter() - begin
    assert len(r.results) == n and len(rels) == 15000
    assert elapsed < 5, f"CPM tardó {elapsed:.3f} s"


@pytest.mark.parametrize("kind", ["FS", "SS", "FF", "SF"])
@pytest.mark.parametrize("lag", [-2, 0, 2])
def test_two_calendar_cpm_matches_exhaustive_dates(kind, lag):
    first = FakeCalendar(weekdays=range(7))
    second = FakeCalendar(holidays=[date(2026, 1, 8)])
    p = problem([activity(1, 6), activity(2, 2, calendar_id=2)], [Relation(1, 1, 1, 2, kind, lag)], calendars={1: first, 2: second})
    r = compute_cpm(p)
    feasible = []
    for x in range(16):
        s1 = p.horizon_start + timedelta(days=x)
        f1 = first.add_workdays(s1, 6)
        if f1 > r.project_finish:
            continue
        event = f1 if kind[0] == "F" else s1
        target = second.next_workday(event, include=kind != "FS")
        for _ in range(abs(lag)):
            target = second.next_workday(target, False) if lag > 0 else second.prev_workday(target, False)
        for y in range(16):
            s2 = p.horizon_start + timedelta(days=y)
            if not second.is_workday(s2):
                continue
            f2 = second.add_workdays(s2, 2)
            if f2 <= r.project_finish and (f2 if kind[1] == "F" else s2) >= target:
                feasible.append((s1, s2))
    assert feasible
    assert r.results[1].late_start == max(x[0] for x in feasible)
    assert r.results[2].late_start == max(x[1] for x in feasible)
    assert r.results[1].early_start == min(x[0] for x in feasible)
    assert r.results[2].early_start == min(x[1] for x in feasible)
