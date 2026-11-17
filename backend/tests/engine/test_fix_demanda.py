"""Regresiones de demanda sin capacidad y jornadas parcialmente reducidas."""
from datetime import date
from time import perf_counter

import numpy as np
import pytest

from ganttier.domain import Assignment, ObjectiveWeights, OptimizeOptions, Resource, to_dict
from ganttier.engine import cpsat, optimize
from ganttier.engine.cpm import Model, _compute
from ganttier.engine.loads import Loads
from .conftest import FakeCalendar, activity, problem


def prepared(p, **options):
    m = Model(p)
    _compute(m)
    loads = Loads(m, OptimizeOptions(method="heuristic", **options), ObjectiveWeights(1, 1, 1, 1))
    loads.rebuild(m.starts)
    return m, loads


@pytest.mark.parametrize("group_by", ["skill", "resource"])
def test_unassigned_hours_visible_without_capacity(group_by):
    p = problem([activity(1, work_hours=8), activity(2, work_hours=4), activity(3)])
    r = optimize(p, ObjectiveWeights(1, 1, 0, 1), OptimizeOptions(method="heuristic", group_by=group_by))
    assert r.status == "heuristico", r.warnings
    series = r.profile_after.series
    assert len(series) == 1
    assert series[0].key == "Sin habilidad asignada"
    assert sum(series[0].load) == 12
    assert r.after.peak_load == 12
    assert r.after.overload_hours == r.after.overtime_hours == r.after.overtime_cost == 0
    assert r.warnings == ["2 actividades con HH no tienen recurso ni habilidad: no se nivelan contra capacidad (A, B)"]
    import json
    json.dumps(to_dict(r), allow_nan=False)


@pytest.mark.parametrize("group_by", ["skill", "resource"])
def test_uncovered_skills_aggregate_warning_and_preserve_hours(group_by):
    p = problem([activity(1, work_hours=8, required_skill="Soldadura"),
                 activity(2, work_hours=4, required_skill="Soldadura"),
                 activity(3, required_skill="Pintura", crew_size=2)],
                resources=[Resource(1, "Inactivo", skills=["Soldadura"], active=False)])
    # Criterio E1: con límites de recursos activos, una habilidad sin recursos NO es ejecutable:
    # el resultado es infactible, con un único aviso agregado que nombra las habilidades faltantes.
    r = optimize(p, ObjectiveWeights(1, 1, 0, 1), OptimizeOptions(method="heuristic", group_by=group_by))
    assert r.status == "infactible", r.warnings
    assert any("Pintura" in w and "Soldadura" in w for w in r.warnings), r.warnings
    # Sin límites de recursos se programa igual y las HH se conservan en el perfil.
    r = optimize(p, ObjectiveWeights(1, 1, 0, 1), OptimizeOptions(method="heuristic", group_by=group_by,
                                                              respect_resource_limits=False))
    assert r.status != "infactible", r.warnings
    assert {s.key: sum(s.load) for s in r.profile_after.series} == {"Soldadura": 12, "Pintura": 16}


def test_unknown_capacity_does_not_hide_known_zero_capacity():
    p = problem([activity(1, work_hours=8), activity(2)],
                resources=[Resource(1, "Sin disponibilidad", capacity_hours_day=0)],
                assignments=[Assignment(1, 2, resource_id=1, work_hours=4)])
    r = optimize(p, ObjectiveWeights(), OptimizeOptions(method="heuristic"))
    assert r.status == "infactible"
    assert any("Capacidad insuficiente de Sin disponibilidad" in w for w in r.warnings)


@pytest.mark.parametrize("allow_overtime", [True, False])
def test_daily_capacity_uses_resource_hours_ceiling_units_and_overtime(allow_overtime):
    p = problem([activity(1)], resources=[Resource(1, "R", skills=["S"], calendar_id=2,
                capacity_hours_day=6, max_units=2, max_overtime_hours_day=1.5)],
                assignments=[Assignment(1, 1, resource_id=1, work_hours=4)],
                calendars={1: FakeCalendar(), 2: FakeCalendar(exceptions={date(2026, 1, 5): 4, date(2026, 1, 6): 0})})
    m, loads = prepared(p, allow_overtime=allow_overtime)
    for key in [("resource", 1), ("skill", "S")]:
        g = loads.lookup[key]
        np.testing.assert_allclose(loads.capacity[g, :3], [8, 0, 12])
        np.testing.assert_allclose(loads.limit[g, :3], [11, 0, 15] if allow_overtime else [8, 0, 12])


def test_partial_day_moves_eight_hours_but_overtime_allows_six():
    p = problem([activity(1)], resources=[Resource(1, "R", calendar_id=2, max_overtime_hours_day=2)],
                assignments=[Assignment(1, 1, resource_id=1, work_hours=8)],
                calendars={1: FakeCalendar(), 2: FakeCalendar(exceptions={date(2026, 1, 5): 4})})
    opts = OptimizeOptions(method="heuristic", group_by="resource")
    r = optimize(p, ObjectiveWeights(makespan=1, window=0), opts)
    assert r.starts[1] == date(2026, 1, 6)
    p.assignments[0].work_hours = 6
    r = optimize(p, ObjectiveWeights(makespan=1, window=0), opts)
    assert r.starts[1] == date(2026, 1, 5)
    assert r.after.overtime_hours == 2


def test_total_hours_follow_activity_calendar_for_each_candidate():
    p = problem([activity(1, duration=2)], resources=[Resource(1, "R", max_units=2)],
                assignments=[Assignment(1, 1, resource_id=1, work_hours=12)],
                calendars={1: FakeCalendar(exceptions={date(2026, 1, 5): 4})})
    m, loads = prepared(p)
    days, valid = loads.cells(0, np.array([m.idx(date(2026, 1, 5)), m.idx(date(2026, 1, 6))]))
    assert valid.all()
    g, hh, units = loads.demands[0][0]
    np.testing.assert_allclose(loads.amount(0, g, hh, units, days), [[4, 8], [6, 6]])
    assert sum(loads.profile().series[0].load) == 12


def test_rounded_hint_ignores_unknown_capacity_but_checks_partial_day():
    p = problem([activity(1, work_hours=800)], end=date(2026, 1, 5))
    m, loads = prepared(p)
    assert cpsat.hint_is_feasible(m, loads, m.starts, m.finishes)
    p.resources = [Resource(1, "R")]
    p.assignments = [Assignment(1, 1, resource_id=1, work_hours=8)]
    p.calendars = {1: FakeCalendar(exceptions={date(2026, 1, 5): 4})}
    m, loads = prepared(p)
    assert not cpsat.hint_is_feasible(m, loads, m.starts, m.finishes)


def test_explicit_skill_assignment_keeps_one_visible_uncovered_demand():
    p = problem([activity(1)], assignments=[Assignment(1, 1, skill="S", work_hours=12)])
    m, loads = prepared(p)
    assert len(loads.demands[0]) == len(loads.visible) == 1
    assert loads.profile().series[0].load[0] == 12
    # La demanda de la habilidad S sin recursos es visible y se informa como capacidad insuficiente.
    assert any("Capacidad insuficiente de S" in v for v in loads.violations())


def test_remaining_hours_follow_reduced_days_and_completed_work_has_no_load():
    p = problem([activity(1, duration=4, work_hours=24, actual_start=date(2026, 1, 5),
                          percent_complete=50, remaining_duration=2),
                 activity(2, work_hours=16, actual_start=date(2026, 1, 5),
                          actual_finish=date(2026, 1, 6), percent_complete=100)],
                calendars={1: FakeCalendar(exceptions={date(2026, 1, 8): 4})},
                data_date=date(2026, 1, 7))
    m, loads = prepared(p)
    # Corte al final del 7: el remanente (2 días, 12 HH) ocupa el 8 (4 h) y el 9 (8 h).
    np.testing.assert_allclose(loads.profile().series[0].load[:5], [0, 0, 0, 4, 8])
    assert sum(loads.profile().series[0].load) == 12


def test_out_of_horizon_profile_does_not_redistribute_clipped_hours():
    p = problem([activity(1, duration=2, work_hours=12)],
                calendars={1: FakeCalendar(exceptions={date(2026, 1, 30): 4})})
    m, loads = prepared(p)
    loads.rebuild(np.array([m.idx(date(2026, 1, 30))]))
    # Four HH inside the horizon; eight HH on the following Monday stay outside.
    assert sum(loads.profile().series[0].load) == 4


@pytest.mark.parametrize("period", ["day", "week"])
def test_incremental_scores_match_rebuild_for_proportional_and_uncovered_work(period):
    p = problem([activity(1, duration=2, work_hours=12), activity(2, duration=2)],
                resources=[Resource(1, "R", max_units=2)],
                assignments=[Assignment(1, 2, resource_id=1, work_hours=12)],
                calendars={1: FakeCalendar(exceptions={date(2026, 1, 5): 4})})
    m, loads = prepared(p, level_period=period, respect_resource_limits=False)
    candidates = np.array([m.idx(date(2026, 1, d)) for d in (5, 6, 9)])
    for i in range(2):
        loads.rebuild(m.starts)
        loads.change(i, int(m.starts[i]), -1)
        predicted, finishes = loads.candidates(i, candidates, int(m.finishes[1 - i]))
        actual = []
        for s, f in zip(candidates, finishes):
            new_s, new_f = m.starts.copy(), m.finishes.copy()
            new_s[i], new_f[i] = s, f
            loads.rebuild(new_s)
            actual.append(loads.objective(new_s, new_f))
        np.testing.assert_allclose(predicted - predicted[0], np.array(actual) - actual[0], atol=1e-8)


def test_grouped_thousand_unassigned_activities_remain_visible_and_unconstrained():
    p = problem([activity(i, work_hours=8) for i in range(1, 1001)], end=date(2026, 1, 5))
    r = optimize(p, ObjectiveWeights(1, 1, 0, 1), OptimizeOptions(method="heuristic"))
    assert r.status == "heuristico", r.warnings
    assert len(r.starts) == 1000
    assert r.profile_after.series[0].load == [8000]
    assert r.after.overload_hours == 0
    assert len(r.warnings) == 1
    assert r.warnings[0].startswith("1000 actividades con HH")


@pytest.mark.parametrize("unknown", [True, False])
def test_real_cpsat_unknown_capacity_and_partial_day(unknown):
    pytest.importorskip("ortools")
    from ortools.sat.python import cp_model
    p = problem([activity(1, work_hours=8)], end=date(2026, 1, 5))
    if not unknown:
        p.resources = [Resource(1, "R")]
        p.assignments = [Assignment(1, 1, resource_id=1, work_hours=8)]
        p.calendars = {1: FakeCalendar(exceptions={date(2026, 1, 5): 4})}
    m, loads = prepared(p)
    plan = cpsat.plan_model(m, loads, m.starts)
    model, _, _, valid = cpsat.build_model(m, loads, m.starts, m.finishes, plan, perf_counter() + 5)
    assert valid == unknown
    assert not model.validate()
    solver = cp_model.CpSolver()
    cpsat._configure(solver, loads, 3)
    state = solver.solve(model)
    if unknown:
        assert state in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    else:
        assert state == cp_model.INFEASIBLE
