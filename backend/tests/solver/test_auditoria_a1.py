"""Reproducciones A1: solo objetos en memoria; no modifica datos del proyecto.

Las aserciones expresan el resultado correcto, no el comportamiento defectuoso.
Ejecutar desde backend con -B y -p no:cacheprovider.
"""
from dataclasses import replace
from datetime import date, timedelta
import importlib
import sqlite3
import sys

import pytest

from ganttier.calendars import WorkCalendar
from ganttier.domain import (Activity, Assignment, Calendar, CalendarException,
    ObjectiveWeights, Occurrence, OptimizeOptions, PeriodicTask, Project,
    Relation, Resource, ScheduleProblem)
from ganttier.engine.cpm import Model, compute_cpm
from ganttier.engine import optimize, validate_network
from ganttier.engine.verify import verify_schedule
from ganttier.maintenance import generate_occurrences, level_periodic, crew_assignment


DAY = date(2026, 1, 5)


def problem(activities, *, end=date(2026, 1, 30), mode="proyecto", resources=(), assignments=(), relations=()):
    cal = WorkCalendar(Calendar(1, "L-V", holidays_country=None), DAY, end)
    return ScheduleProblem(Project(1, "A1", mode=mode, start_date=DAY, calendar_id=1),
        activities, list(relations), list(resources), list(assignments), {1: cal}, 1, DAY, end)


def act(i=1, **kwargs):
    return Activity(i, 1, f"A{i}", f"A{i}", **kwargs)


def occ(i=1, **kwargs):
    values = dict(periodic_task_id=1, occurrence_index=i, code=f"O{i}", name=f"O{i}",
        due_date=DAY, window_start=DAY, window_end=DAY, duration_days=1,
        work_hours=8, crew_size=1, required_skill="E", resource_id=None, calendar_id=1,
        activity_id=i, window_hard=True)
    values.update(kwargs)
    return Occurrence(**values)


def options(**kwargs):
    return OptimizeOptions(method="heuristic", time_limit_s=2, allow_overtime=False, **kwargs)


def maintenance(p, weights=None):
    """Ejercita el adaptador real con lecturas SQLite vacías, sin archivos."""
    from ganttier.solve import _maintenance
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE periodic_tasks(id INTEGER, project_id INTEGER);"
        "CREATE TABLE activities(id INTEGER, project_id INTEGER);"
        "CREATE TABLE progress(activity_id INTEGER, percent_complete REAL, actual_start TEXT, actual_finish TEXT, actual_hours REAL);")
    try:
        return _maintenance(conn, 1, p, weights or ObjectiveWeights(makespan=1, window=0), options())
    finally:
        conn.close()


@pytest.mark.parametrize("method", ["heuristic", "cpsat"])
def test_control_documented_fixed_split_limitation(method):
    p = problem([act(1, work_hours=8), act(2, work_hours=8, required_skill="E")], end=DAY,
        resources=[Resource(1, "Ana", skills=["E"]), Resource(2, "Beto", skills=["E"])],
        assignments=[Assignment(1, 1, 1, work_hours=8)])
    dates = {1: DAY, 2: DAY}
    assert verify_schedule(p, dates, dates).feasible  # Ana=A1, Beto=A2.
    result = optimize(p, ObjectiveWeights(makespan=1, window=0), replace(options(), method=method))
    # docs/decisiones/engine.md documenta explícitamente esta aproximación.
    # No se incluye como defecto en el informe A1.
    assert result.status == "infactible", result.warnings


def test_alap_survives_optimization():
    p = problem([act(constraint_type="ALAP")])
    p.project.end_date = p.horizon_end
    assert compute_cpm(p).results[1].early_start == p.horizon_end
    result = optimize(p, ObjectiveWeights(makespan=1, window=0), options())
    assert result.starts[1] == p.horizon_end, result


def test_verifier_rejects_positive_work_on_zero_duration():
    p = problem([act(duration_days=0, work_hours=80, required_skill="E")])
    assert validate_network(p) == []
    result = optimize(p, ObjectiveWeights(), options())
    report = verify_schedule(p, {1: DAY}, {1: DAY})
    assert not report.feasible, (report, result.status, result.after)


def test_maintenance_crew_does_not_erase_explicit_assignment_demand():
    p = problem([act(work_hours=8, required_skill="E")], mode="mantenimiento",
        resources=[Resource(1, "Ana", skills=["E"])],
        assignments=[Assignment(1, 1, 1, work_hours=16)])
    assert not verify_schedule(p, {1: DAY}, {1: DAY}).feasible
    report = verify_schedule(p, {1: DAY}, {1: DAY}, worker_assignment={1: 1}, crews={1: [1]})
    assert not report.feasible, report


def test_maintenance_honors_fs_relation():
    p = problem([act(1, work_hours=8, required_skill="E"), act(2, work_hours=8, required_skill="E")],
        mode="mantenimiento", resources=[Resource(1, "Ana", skills=["E"]), Resource(2, "Beto", skills=["E"])],
        relations=[Relation(1, 1, 1, 2)])
    good = {1: DAY, 2: DAY + timedelta(days=1)}
    assert verify_schedule(p, good, good).feasible
    result, starts, finishes, crews = maintenance(p)
    report = verify_schedule(p, starts, finishes, worker_assignment=result.worker_assignment, crews=crews)
    assert report.feasible, (starts, report.violations)


def test_maintenance_recalculates_remaining_finish_after_cutoff():
    a = act(duration_days=4, work_hours=32, required_skill="E", actual_start=DAY,
        percent_complete=50, remaining_duration=2, status="en_curso",
        planned_start=DAY, planned_finish=date(2026, 1, 8))
    p = problem([a], mode="mantenimiento", resources=[Resource(1, "Ana", skills=["E"])],
        assignments=[Assignment(1, 1, 1, work_hours=32)])
    p.data_date = date(2026, 1, 9)
    assert verify_schedule(p, {1: DAY}, {1: date(2026, 1, 13)}).feasible
    result, starts, finishes, crews = maintenance(p)
    assert finishes[1] == date(2026, 1, 13), (finishes, result)


def test_maintenance_resource_inherits_project_calendar():
    saturday = date(2026, 1, 10)
    p = problem([act(work_hours=8, required_skill="E", window_start=saturday,
        window_end=saturday, window_hard=True)], mode="mantenimiento", resources=[Resource(1, "Ana", skills=["E"])])
    p.calendars[2] = WorkCalendar(Calendar(2, "Todos", workweek="1111111", holidays_country=None), DAY, p.horizon_end)
    p.default_calendar_id = p.project.calendar_id = 2
    assert verify_schedule(p, {1: saturday}, {1: saturday}, worker_assignment={1: 1}).feasible
    result, starts, finishes, crews = maintenance(p)
    assert result.status != "infactible", result


def test_maintenance_reports_small_capacity_violation():
    p = problem([], end=DAY)
    result = level_periodic([occ(work_hours=8.25)], [Resource(1, "Ana", skills=["E"])],
        p.calendars, ObjectiveWeights(), options())
    assert result.after.overload_hours == .25
    assert result.status == "infactible", result


def test_maintenance_zero_explicit_hours_uses_crew_demand():
    p = problem([act(work_hours=0, required_skill="E")], mode="mantenimiento",
        resources=[Resource(1, "Ana", skills=["E"])])
    assert verify_schedule(p, {1: DAY}, {1: DAY}, worker_assignment={1: 1}).feasible
    result, starts, finishes, crews = maintenance(p)
    report = verify_schedule(p, starts, finishes, worker_assignment=result.worker_assignment, crews=crews)
    assert report.feasible, (result, report.violations)


def test_cpm_backward_pass_matches_cross_calendar_forward_semantics():
    # Control: el fin global limita la holgura aunque el desfase permita un día más.
    p = problem([act(1, calendar_id=2), act(2, constraint_type="MSO", constraint_date=date(2026, 1, 12))],
        relations=[Relation(1, 1, 1, 2, "FF", -1)])
    p.calendars[2] = WorkCalendar(Calendar(2, "Todos", workweek="1111111", holidays_country=None), DAY, p.horizon_end)
    result = compute_cpm(p)
    good_s = {1: date(2026, 1, 13), 2: date(2026, 1, 12)}
    assert verify_schedule(p, good_s, good_s).feasible
    assert result.results[1].late_start == date(2026, 1, 12)


def test_control_documented_model_margin_for_large_lag():
    p = problem([act(1), act(2)], relations=[Relation(1, 1, 1, 2, "FS", -300)])
    assert verify_schedule(p, {1: DAY, 2: DAY}, {1: DAY, 2: DAY}).feasible
    # Límite explícito de 366 días: se conserva como control, no como hallazgo.
    with pytest.raises(ValueError, match="calendario disponible"):
        compute_cpm(p)


def test_retry_does_not_get_a_new_time_budget(monkeypatch):
    module = importlib.import_module("ganttier.engine.optimize")
    cp = importlib.import_module("ganttier.engine.cpsat")
    deadlines = []
    real_prepare = module.prepare

    def recording_prepare(m, loads, late, deadline):
        deadlines.append(deadline)
        return real_prepare(m, loads, late, deadline)

    # Reloj estable: revela el presupuesto concedido sin una prueba lenta o frágil.
    monkeypatch.setattr(module, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(module, "prepare", recording_prepare)
    monkeypatch.setattr(cp, "solve", lambda *args: None)
    p = problem([act(work_hours=16, required_skill="E")], end=DAY,
        resources=[Resource(1, "Ana", skills=["E"])])
    module.optimize(p, ObjectiveWeights(makespan=1, window=0),
        OptimizeOptions(method="cpsat", time_limit_s=.1, allow_overtime=False))
    assert deadlines and max(deadlines) <= 100.1, deadlines


def test_fallback_holidays_good_friday_2029(monkeypatch):
    from ganttier.calendars import _holiday_dates
    # 2029: Pascua = 1 de abril; Viernes Santo = 30 de marzo.
    _holiday_dates.cache_clear()
    monkeypatch.setitem(sys.modules, "holidays", None)
    try:
        cal = WorkCalendar(Calendar(1, "CL", holidays_country="CL"),
            date(2027, 1, 1), date(2028, 12, 31))
        assert not cal.is_workday(date(2029, 3, 30))
        assert cal.is_workday(date(2029, 4, 6))
    finally:
        _holiday_dates.cache_clear()


def test_solve_project_does_not_reduce_assignment_hours(monkeypatch):
    from ganttier import db, repo, services
    from ganttier.solve import solve_project
    conn = db.connect(":memory:")
    try:
        db.initialize(conn)
        cid = repo.create(conn, "calendars", dict(name="A1 sin feriados", holidays_country=None)).id
        p = repo.create(conn, "projects", dict(name="A1", mode="mantenimiento", start_date=DAY, end_date=DAY, calendar_id=cid))
        r = repo.create(conn, "resources", dict(name="Ana", project_id=p.id, skills=["E"], calendar_id=cid))
        a = repo.create(conn, "activities", dict(project_id=p.id, code="A", name="A", work_hours=8, required_skill="E"))
        repo.create(conn, "assignments", dict(activity_id=a.id, resource_id=r.id, work_hours=16))
        payloads = []
        # Solo se intercepta la persistencia del resultado para no generar JSON.
        monkeypatch.setattr(services, "_save_run", lambda conn, pid, kind, payload, **kwargs: payloads.append(payload) or 1)
        result = solve_project(conn, p.id, options=options(), apply=False)
        assert payloads[0]["verification"]["feasible"] == result["feasible"]
        assert not result["feasible"], result
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["FS", "SS", "FF", "SF"])
@pytest.mark.parametrize("lag", [-2, 0, 2])
def test_control_relations_and_holiday(kind, lag):
    p = problem([act(1, duration_days=3), act(2, duration_days=2)],
        relations=[Relation(1, 1, 1, 2, kind, lag)])
    p.calendars[1] = WorkCalendar(Calendar(1, "Excepción", holidays_country=None,
        exceptions=[CalendarException(date(2026, 1, 8), False)]), DAY, p.horizon_end)
    cpm = compute_cpm(p)
    starts = {i: a.early_start for i, a in cpm.results.items()}
    finishes = {i: a.early_finish for i, a in cpm.results.items()}
    assert verify_schedule(p, starts, finishes).feasible


def test_control_month_end_leap_year_and_clipped_horizon():
    first, last = date(2024, 1, 1), date(2028, 12, 31)
    calendars = {1: WorkCalendar(Calendar(1, "L-V", holidays_country=None), first, last)}
    task = PeriodicTask(1, 1, "M", "M", frequency="M", first_due=date(2024, 1, 31))
    generated = generate_occurrences([task], first, date(2024, 4, 30), calendars, 1)
    assert [o.due_date for o in generated] == [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30)]
    assert all(first <= o.window_start <= o.window_end <= date(2024, 4, 30) for o in generated)
    yearly = replace(task, frequency="A", first_due=date(2024, 2, 29))
    generated = generate_occurrences([yearly], date(2025, 1, 1), last, calendars, 1)
    assert [o.due_date for o in generated] == [date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29)]
    assert [o.occurrence_index for o in generated] == [2, 3, 4, 5]


def test_control_shift_anchor_and_cutoff_next_workday():
    p = problem([act(duration_days=4, actual_start=DAY, percent_complete=50, remaining_duration=2)])
    p.data_date = date(2026, 1, 8)
    p.calendars[1] = WorkCalendar(Calendar(1, "4x3", shift_pattern="4x3", shift_anchor=DAY,
        hours_per_day=10, holidays_country=None), DAY, p.horizon_end)
    cpm = compute_cpm(p)
    assert cpm.results[1].early_finish == date(2026, 1, 13)
    assert verify_schedule(p, {1: DAY}, {1: date(2026, 1, 13)}).feasible


@pytest.mark.parametrize("kind,expected", [
    ("SNET", (date(2026, 1, 9), date(2026, 1, 12))),
    ("FNET", (date(2026, 1, 8), date(2026, 1, 9))),
    ("MSO", (date(2026, 1, 9), date(2026, 1, 12))),
    ("MFO", (date(2026, 1, 8), date(2026, 1, 9))),
])
def test_control_hard_date_constraints(kind, expected):
    p = problem([act(duration_days=2, constraint_type=kind, constraint_date=date(2026, 1, 9))])
    row = compute_cpm(p).results[1]
    assert (row.early_start, row.early_finish) == expected
    assert verify_schedule(p, {1: row.early_start}, {1: row.early_finish}).feasible


@pytest.mark.parametrize("kind", ["SNLT", "FNLT"])
def test_control_late_deadlines_warn_with_negative_float(kind):
    p = problem([act(1, duration_days=3), act(2, duration_days=2, constraint_type=kind, constraint_date=DAY)],
        relations=[Relation(1, 1, 1, 2)])
    cpm = compute_cpm(p)
    assert cpm.results[2].total_float < 0
    report = verify_schedule(p, {i: a.early_start for i, a in cpm.results.items()},
        {i: a.early_finish for i, a in cpm.results.items()})
    assert report.feasible
    assert any(w["type"] == kind for w in report.warnings)


def test_control_maintenance_is_reproducible_for_same_seed():
    p = problem([])
    tasks = [occ(i, window_end=p.horizon_end, window_hard=False) for i in range(1, 5)]
    resources = [Resource(i, str(i), skills=["E"]) for i in (1, 2)]
    first = level_periodic(tasks, resources, p.calendars, ObjectiveWeights(level=1, window=0), replace(options(), seed=19))
    second = level_periodic(tasks, resources, p.calendars, ObjectiveWeights(level=1, window=0), replace(options(), seed=19))
    assert first.starts == second.starts
    assert crew_assignment(first) == crew_assignment(second)


@pytest.mark.parametrize('kind', ['FS', 'SS', 'FF', 'SF'])
@pytest.mark.parametrize('lag', [-2, 0, 2])
@pytest.mark.parametrize('cross_calendar', [False, True])
def test_mantenimiento_relaciones_con_desfases(kind, lag, cross_calendar):
    p = problem([act(1, duration_days=3, work_hours=24, required_skill='E',
                     constraint_type='SNET', constraint_date=date(2026, 1, 12)),
                 act(2, duration_days=2, work_hours=16, required_skill='E',
                     calendar_id=2 if cross_calendar else 1)],
        mode='mantenimiento', resources=[Resource(1, 'Ana', skills=['E']),
            Resource(2, 'Beto', skills=['E'], calendar_id=2 if cross_calendar else 1)],
        relations=[Relation(1, 1, 1, 2, kind, lag)])
    if cross_calendar:
        p.calendars[2] = WorkCalendar(Calendar(2, 'Todos', workweek='1111111', holidays_country=None,
            exceptions=[CalendarException(date(2026, 1, 13), False)]), DAY, p.horizon_end)
    result, starts, finishes, crews = maintenance(p)
    report = verify_schedule(p, starts, finishes, worker_assignment=result.worker_assignment, crews=crews)
    assert result.status != 'infactible', result.warnings
    assert report.feasible, report.violations


def test_mantenimiento_repara_predecesor_si_sucesor_no_tiene_capacidad():
    tuesday, wednesday = date(2026, 1, 6), date(2026, 1, 7)
    p = problem([act(1, work_hours=8, required_skill='E', due_date=tuesday),
                 act(2, work_hours=8, required_skill='E', due_date=wednesday),
                 act(3, work_hours=8, required_skill='E', locked=True,
                     planned_start=wednesday, planned_finish=wednesday)],
        mode='mantenimiento', end=wednesday, resources=[Resource(1, 'Ana', skills=['E'])],
        assignments=[Assignment(1, 3, 1, work_hours=8)], relations=[Relation(1, 1, 1, 2)])
    result, starts, finishes, crews = maintenance(p, ObjectiveWeights(window=1, makespan=0, level=0))
    assert starts == {1: DAY, 2: tuesday, 3: wednesday}
    assert verify_schedule(p, starts, finishes, worker_assignment=result.worker_assignment, crews=crews).feasible


def test_mantenimiento_relacion_imposible_no_publica_fechas():
    p = problem([act(1, work_hours=8, required_skill='E'), act(2, work_hours=8, required_skill='E')],
        mode='mantenimiento', end=DAY, resources=[Resource(1, 'Ana', skills=['E'])],
        relations=[Relation(1, 1, 1, 2)])
    result, starts, finishes, crews = maintenance(p)
    assert result.status == 'infactible'
    assert not starts and not finishes


@pytest.mark.parametrize('source', ['actividad', 'asignacion'])
def test_hito_con_hh_se_rechaza_con_codigo(case, source):
    p = case(n=1, duration=0, hours=80 if source == 'actividad' else 0)
    if source == 'asignacion':
        p.assignments = [Assignment(1, 1, 1, work_hours=80)]
    report = verify_schedule(p, {1: DAY}, {1: DAY})
    assert not report.feasible
    assert any(v['type'] == 'milestone_work' and 'A1' in v['detail'] for v in report.violations)
    assert optimize(p, ObjectiveWeights(), options()).status == 'infactible'


def test_hito_terminado_con_hh_historicas_no_se_rechaza(case):
    p = case(n=1, duration=0, hours=80)
    p.activities[0] = replace(p.activities[0], actual_start=DAY, actual_finish=DAY, percent_complete=100)
    p.assignments = [Assignment(1, 1, 1, work_hours=80)]
    assert verify_schedule(p, {1: DAY}, {1: DAY}).feasible


@pytest.mark.parametrize('mixed', [False, True])
def test_demanda_implicita_respeta_horas_de_cada_jornada(mixed):
    p = problem([act(work_hours=0, duration_days=2, required_skill='E')],
        mode='mantenimiento', end=date(2026, 1, 6),
        resources=[Resource(1, 'Ana', skills=['E'], max_units=2)])
    p.calendars[1] = WorkCalendar(Calendar(1, 'Variable', holidays_country=None,
        exceptions=[CalendarException(DAY, True, hours=4)]), DAY, p.horizon_end)
    if mixed:
        p.assignments = [Assignment(1, 1, 1, units=1), Assignment(2, 1, 1, work_hours=8)]
    result, starts, finishes, crews = maintenance(p)
    assert result.status != 'infactible', result.warnings
    assert verify_schedule(p, starts, finishes, worker_assignment=result.worker_assignment, crews=crews).feasible
    assert sum(result.profile_after.series[0].load) == pytest.approx(20 if mixed else 12)


def test_hh_asignadas_se_conservan_al_cambiar_cuadrilla():
    p = problem([act(work_hours=8, required_skill='E')], mode='mantenimiento',
        resources=[Resource(1, 'Ana', skills=['E']), Resource(2, 'Beto', skills=['E'])],
        assignments=[Assignment(1, 1, 1, work_hours=16)])
    assert verify_schedule(p, {1: DAY}, {1: DAY}, worker_assignment={1: 1}, crews={1: [1, 2]}).feasible
    assert not verify_schedule(p, {1: DAY}, {1: DAY}, worker_assignment={1: 2}, crews={1: [2]}).feasible


def test_remanente_reserva_capacidad_antes_de_colocar_pendiente():
    p = problem([act(1, duration_days=4, work_hours=32, required_skill='E', actual_start=DAY,
                     percent_complete=50, remaining_duration=2, planned_start=DAY, planned_finish=date(2026, 1, 8)),
                 act(2, work_hours=8, required_skill='E')], mode='mantenimiento',
        resources=[Resource(1, 'Ana', skills=['E'])], assignments=[Assignment(1, 1, 1, work_hours=32)])
    p.data_date = date(2026, 1, 9)
    result, starts, finishes, crews = maintenance(p)
    assert finishes[1] == date(2026, 1, 13)
    assert starts[2] == date(2026, 1, 14)
    assert verify_schedule(p, starts, finishes, worker_assignment=result.worker_assignment, crews=crews).feasible


@pytest.mark.parametrize('method', ['heuristic', 'cpsat'])
def test_alap_con_capacidad_ocupada_al_final(method):
    last = date(2026, 1, 30)
    p = problem([act(1, work_hours=8, required_skill='E', constraint_type='ALAP'),
                 act(2, work_hours=8, required_skill='E', locked=True, planned_start=last, planned_finish=last)],
        resources=[Resource(1, 'Ana', skills=['E'])])
    p.project.end_date = last
    result = optimize(p, ObjectiveWeights(makespan=1, window=0), replace(options(), method=method))
    assert result.starts[1] == date(2026, 1, 29), result.warnings
    assert verify_schedule(p, result.starts, result.finishes).feasible


def test_presupuesto_agotado_no_reintenta_despues_de_cpsat(monkeypatch):
    module = importlib.import_module('ganttier.engine.optimize')
    cp = importlib.import_module('ganttier.engine.cpsat')
    clock, deadlines = [100.0], []
    real_prepare = module.prepare
    def prepare(m, loads, late, deadline):
        deadlines.append(deadline)
        return real_prepare(m, loads, late, deadline)
    def exhausted(*args):
        clock[0] = 101.0
        return None
    monkeypatch.setattr(module, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(module, 'prepare', prepare)
    monkeypatch.setattr(cp, 'solve', exhausted)
    p = problem([act(work_hours=16, required_skill='E')], end=DAY,
        resources=[Resource(1, 'Ana', skills=['E'])])
    result = optimize(p, ObjectiveWeights(), OptimizeOptions(method='cpsat', time_limit_s=.1, allow_overtime=False))
    assert len(deadlines) == 1
    assert result.status == 'infactible'


def test_demanda_implicita_sin_asignacion_concreta_conserva_reserva():
    p = problem([], end=DAY)
    result = level_periodic([occ(work_hours=0)], [Resource(i, str(i), skills=['E'],
        capacity_hours_day=4) for i in (1, 2)], p.calendars, ObjectiveWeights(),
        replace(options(), assign_workers=False))
    assert result.status != 'infactible', result.warnings
    assert not result.worker_assignment
    assert sum(result.profile_after.series[0].load) == 8
    assert result.after.overload_hours == 0


def test_reserva_sobrecargada_se_declara_infactible_aunque_no_fuerce_pendientes():
    p = problem([])
    result = level_periodic([occ(window_end=p.horizon_end)], [Resource(1, 'Ana', skills=['E'])],
        p.calendars, ObjectiveWeights(), options(), reserved_load={1: {DAY: 8.25}})
    assert result.status == 'infactible'


def test_mantenimiento_advierte_alap_no_certificado():
    p = problem([act(work_hours=8, required_skill='E', constraint_type='ALAP')],
        mode='mantenimiento', resources=[Resource(1, 'Ana', skills=['E'])])
    result, *_ = maintenance(p)
    assert any('ALAP' in warning for warning in result.warnings)


def test_demanda_implicita_elige_personas_si_cuadrilla_agregada_no_cabe():
    p = problem([], end=DAY)
    resources = [Resource(1, 'Grupo', skills=['E'], max_units=2, capacity_hours_day=2),
                 Resource(2, 'Ana', skills=['E']), Resource(3, 'Beto', skills=['E'])]
    result = level_periodic([occ(work_hours=0, crew_size=2)], resources, p.calendars,
                            ObjectiveWeights(), options())
    assert result.status != 'infactible', result.warnings
    assert set(crew_assignment(result)[1]) == {2, 3}


def test_cpsat_conserva_factible_aunque_incumbente_invalido_tenga_mejor_objetivo(monkeypatch):
    module = importlib.import_module('ganttier.engine.optimize')
    cp = importlib.import_module('ganttier.engine.cpsat')
    clock = [100.0]
    real_prepare = module.prepare
    def bad_prepare(m, loads, late, deadline):
        _, _, warnings = real_prepare(m, loads, late, deadline)
        return m.starts.copy(), m.finishes.copy(), warnings
    def completed(m, loads, starts, finishes, deadline):
        starts, finishes = starts.copy(), finishes.copy()
        starts[1] = finishes[1] = m.idx(date(2026, 1, 6))
        clock[0] = 103.0
        return starts, finishes, 'factible', []
    monkeypatch.setattr(module, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(module, 'prepare', bad_prepare)
    monkeypatch.setattr(module, 'heuristic_solve', lambda m, loads, late, deadline, **kw:
        (m.starts.copy(), m.finishes.copy(), []))
    monkeypatch.setattr(cp, 'solve', completed)
    p = problem([act(1, work_hours=8, required_skill='E'), act(2, work_hours=8, required_skill='E')],
        end=date(2026, 1, 6), resources=[Resource(1, 'Ana', skills=['E'])])
    result = optimize(p, ObjectiveWeights(makespan=1, window=0), replace(options(), method='cpsat'))
    assert result.status != 'infactible', result.warnings
    assert verify_schedule(p, result.starts, result.finishes).feasible
