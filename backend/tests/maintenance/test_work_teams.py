"""Contratos de equipos, lotes, equidad por ciclo y aplicación factible."""
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, timedelta

import pytest

from ganttier import db, domain as d, repo, services, teams
from ganttier.calendars import WorkCalendar
from ganttier.maintenance.generator import generate_occurrences
from ganttier.maintenance.leveling import level_periodic
from ganttier.engine.verify import verify_schedule

START, END = date(2027, 1, 1), date(2027, 12, 31)


def plan(regime="7x7", count=2, quantity=28000, rate=1000, batch=2000):
    calendars, resources = {}, []
    for i in range(count):
        cal = d.Calendar(i+1, f"Turno {i}", hours_per_day=8, holidays_country="CL",
                         shift_pattern=None if regime == "5x2" else regime,
                         shift_anchor=START + timedelta(days=i*int(regime.split("x")[0])))
        calendars[i+1] = WorkCalendar(cal, START, END)
        resources.append(d.Resource(i+1, f"Equipo {i+1}", kind="cuadrilla", max_units=3,
                                    calendar_id=i+1, is_work_team=True, project_id=1))
    task = d.PeriodicTask(1, 1, "PANEL", "Limpieza", first_due=START,
                          quantity_total=quantity, unit="paneles", batch_size=batch,
                          batch_continuous_in_shift=True, productivity_day_team=rate, team_ids=[r.id for r in resources])
    return task, resources, calendars


def expand(task, resources, calendars, first=START, last=END):
    warnings = []
    rows = generate_occurrences([task], first, last, calendars, 1,
                                resources=resources, warnings=warnings)
    return rows, warnings


@pytest.mark.parametrize("regime", ["7x7", "14x14", "5x2"])
def test_monthly_exact_demand_and_calendar(regime):
    task, resources, calendars = plan(regime)
    rows, warnings = expand(task, resources, calendars)
    assert len(rows) == 168
    for month in range(1, 13):
        subset = [o for o in rows if o.due_date.month == month]
        assert Counter(o.team_id for o in subset) == {1: 7, 2: 7}
        for rid in (1, 2):
            assigned = [o for o in subset if o.team_id == rid]
            assert sum(o.quantity for o in assigned) == 14000
            assert all(o.duration_days == 2 and o.work_hours == 48 for o in assigned)
            assert all(o.window_start.day == 1 and o.window_start.month == month
                       and o.window_end.month == month and o.window_hard for o in assigned)
    result = level_periodic(rows, resources, calendars, d.ObjectiveWeights(level=1, window=1),
                            d.OptimizeOptions(allow_overtime=False), horizon_start=START, horizon_end=END)
    if regime == "5x2":
        assert result.status != "infactible", result.warnings
        for i, o in enumerate(rows):
            s, f = result.starts[i], result.finishes[i]
            assert (f-s).days + 1 == o.duration_days
            for n in range(s.toordinal(), f.toordinal()+1):
                day = date.fromordinal(n)
                assert day.weekday() < 5 and day not in calendars[o.calendar_id].holidays
    else:
        # El contrato 7/7 es demanda: turnos alternados no garantizan capacidad mensual.
        assert result.status == "infactible"


@pytest.mark.parametrize("count,quantity", [(2, 28500), (3, 28500), (3, 28000), (4, 28500)])
def test_rotation_all_prefixes_idempotence_and_partial_horizon(count, quantity):
    task, resources, calendars = plan("5x2", count, quantity)
    rows, _ = expand(task, resources, calendars)
    again, _ = expand(task, resources, calendars)
    assert rows == again
    accumulated = Counter({r.id: 0 for r in resources})
    for month in range(1, 13):
        part = [o for o in rows if o.due_date.month == month]
        counts = Counter({r.id: 0 for r in resources})
        counts.update(o.team_id for o in part)
        assert max(counts.values()) - min(counts.values()) <= 1
        assert sum(o.quantity for o in part) == quantity
        accumulated.update(counts)
        assert max(accumulated.values()) - min(accumulated.values()) <= 1
    july, _ = expand(task, resources, calendars, date(2027, 7, 1))
    assert [(o.code, o.team_id) for o in july] == [(o.code, o.team_id) for o in rows if o.due_date.month >= 7]
    if count == 2:
        assert Counter(o.team_id for o in rows) == {1: 90, 2: 90}
        assert [next(o.team_id for o in rows if o.occurrence_index == m and o.batch_index == 15)
                for m in (1, 2)] == [1, 2]


def test_unequal_workdays_warning_has_evidence_without_reallocation():
    task, resources, calendars = plan()
    rows, warnings = expand(task, resources, calendars)
    assert warnings
    assert any("Equipo 1" in w and "Equipo 2" in w and "2027-01" in w for w in warnings)
    assert Counter(o.team_id for o in rows if o.due_date.month == 1) == {1: 7, 2: 7}


def test_batch_longer_than_shift_rejected():
    task, resources, calendars = plan(batch=9000)
    with pytest.raises(ValueError, match="bloque.*7|7.*bloque"):
        expand(task, resources, calendars)


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setattr(services, "DATA_DIR", tmp_path / "datos")
    conn = db.connect(tmp_path / "teams.db")
    db.initialize(conn)
    project = repo.create(conn, "projects", dict(name="Paneles", mode="mantenimiento",
        start_date=START, end_date=END, data_date=START, calendar_id=1))
    yield conn, project.id
    conn.close()


def populate(conn, pid, regime="5x2", quantity=28000, rate=1000):
    rs = [teams.save_team(conn, pid, dict(name=f"Equipo {letter}", regime=regime,
              cycle_start=START + timedelta(days=i*7), members=3, hours_day=8))
          for i, letter in enumerate("AB")]
    task = repo.create(conn, "periodic_tasks", dict(project_id=pid, code="PANEL", name="Limpieza",
        first_due=START, quantity_total=quantity, unit="paneles", batch_size=2000,
        batch_continuous_in_shift=True, productivity_day_team=rate, team_ids=[r["id"] for r in rs]))
    return task, rs


def test_infeasible_atomic_and_feasible_idempotent(saved):
    conn, pid = saved
    task, resources = populate(conn, pid, "7x7")
    original = repo.create(conn, "activities", dict(project_id=pid, code="EXISTE", name="Preexistente",
        planned_start=date(2027, 1, 4), planned_finish=date(2027, 1, 4), locked=True))
    result = services.generate_maintenance(conn, pid, START, END)
    assert result["status"] == "infactible" and not result["applied"]
    assert repo.list_entities(conn, "activities", pid) == [original]
    assert any("requeridos" in v["detail"] and "disponibles" in v["detail"] for v in result["violations"])
    for r in resources:
        teams.save_team(conn, pid, dict(regime="5x2"), r["id"])
    result = services.generate_maintenance(conn, pid, START, END)
    assert result["applied"] and result["created"] == 168, result
    before = [(a.id, a.code, a.team_id, a.planned_start) for a in repo.list_entities(conn, "activities", pid)]
    result = services.generate_maintenance(conn, pid, START, END)
    assert result["created"] == 0 and result["applied"], result
    assert before == [(a.id, a.code, a.team_id, a.planned_start) for a in repo.list_entities(conn, "activities", pid)]


def test_independent_verifier_detects_quantity_team_and_rest_tampering(saved):
    conn, pid = saved
    populate(conn, pid)
    assert services.generate_maintenance(conn, pid, START, END)["applied"]
    p = services.build_problem(conn, pid)
    starts = {a.id: a.planned_start for a in p.activities}
    finishes = {a.id: a.planned_finish for a in p.activities}
    assert verify_schedule(p, starts, finishes).feasible
    a = p.activities[0]
    a.quantity += 1
    assert any(v["type"] == "batch_quantity" for v in verify_schedule(p, starts, finishes).violations)
    a.quantity -= 1
    starts[a.id], finishes[a.id] = date(2027, 1, 8), date(2027, 1, 11)
    assert any(v["type"] == "batch_rest" for v in verify_schedule(p, starts, finishes).violations)
    a.team_id = p.activities[-1].team_id
    assert not verify_schedule(p, starts, finishes).feasible


def test_verifier_detects_entire_missing_month_and_enlarged_window(saved):
    conn, pid = saved
    populate(conn, pid)
    assert services.generate_maintenance(conn, pid, START, END)["applied"]
    p = services.build_problem(conn, pid)
    p.activities = [a for a in p.activities if a.occurrence_index != 2]
    retained = {a.id for a in p.activities}
    p.assignments = [s for s in p.assignments if s.activity_id in retained]
    starts = {a.id: a.planned_start for a in p.activities}
    finishes = {a.id: a.planned_finish for a in p.activities}
    assert any(v["type"] == "batch_missing_occurrence" for v in verify_schedule(p, starts, finishes).violations)
    p = services.build_problem(conn, pid)
    a = p.activities[0]
    a.window_end = date(2027, 2, 28)
    assert any(v["type"] == "batch_window" for v in verify_schedule(p, starts, finishes).violations)


def test_excel_roundtrip(saved, tmp_path):
    from ganttier.excel_io.exporter import export_project
    from ganttier.excel_io.importer import parse_workbook
    conn, pid = saved
    populate(conn, pid)
    assert services.generate_maintenance(conn, pid, START, END)["applied"]
    bundle = repo.export_bundle(conn, pid)
    path = export_project(bundle, tmp_path / "paneles.xlsx")
    loaded = parse_workbook(path)
    assert not loaded.has_errors, loaded.issues
    assert repo.json_value(loaded.teams) == bundle.teams
    other = services.create_project_from_bundle(conn, loaded)
    again = repo.export_bundle(conn, other.id)
    assert again.periodic_tasks == bundle.periodic_tasks
    assert again.activities == bundle.activities
    assert again.teams == bundle.teams


def test_weekly_14x14_balance_by_complete_cycle_without_false_warning():
    from ganttier.teams import summarize_occurrences
    task, resources, calendars = plan("14x14", quantity=2000, batch=1000)
    task = replace(task, frequency="W")
    end = START + timedelta(days=27)
    calendars = {cid: WorkCalendar(cal.cal, START, end) for cid, cal in calendars.items()}
    # Segunda cuadrilla 14 días después (plan ya aplica este desplazamiento).
    rows, warnings = expand(task, resources, calendars, last=end)
    assert len(rows) == 8
    assert [Counter(o.team_id for o in rows if o.occurrence_index == i) for i in range(1, 5)] == [
        {1: 2}, {1: 2}, {2: 2}, {2: 2}]
    assert Counter(o.team_id for o in rows) == {1: 4, 2: 4}
    assert all(o.equity_mode == "cycle" for o in rows)
    assert not warnings
    summary, warnings, errors = summarize_occurrences(rows, resources, calendars, [task])
    assert not warnings and not errors
    result = level_periodic(rows, resources, calendars, d.ObjectiveWeights(level=1, window=0),
                            d.OptimizeOptions(allow_overtime=False), horizon_start=START, horizon_end=end)
    assert result.status != "infactible", result.warnings
    for i, o in enumerate(rows):
        assert calendars[o.calendar_id].is_workday(result.starts[i])


def test_cycle_month_disagreement_is_visible_and_tolerance_configurable():
    from ganttier.teams import summarize_occurrences
    task, resources, calendars = plan("14x14", quantity=2000, batch=1000)
    task = replace(task, frequency="W")
    end = date(2027, 1, 31)
    calendars = {cid: WorkCalendar(cal.cal, START, end) for cid, cal in calendars.items()}
    rows, _ = expand(task, resources, calendars, last=end)
    _, warnings, _ = summarize_occurrences(rows, resources, calendars, [task])
    assert any("por mes" in w for w in warnings)
    assert not any("por ciclo" in w for w in warnings)
    _, warnings, _ = summarize_occurrences(rows, resources, calendars, [replace(task, balance_tolerance_batches=2)])
    assert not warnings


def test_shared_team_three_plants_cannot_exceed_capacity(saved):
    conn, pid = saved
    task, _ = populate(conn, pid, quantity=28000)
    for i in range(2):
        values = d.to_dict(task)
        values.pop("id")
        values.update(code=f"PLANTA{i}", location=f"Planta FV {i+2}")
        repo.create(conn, "periodic_tasks", values)
    result = services.generate_maintenance(conn, pid, START, END)
    assert result["status"] == "infactible" and not result["applied"]
    assert repo.list_entities(conn, "activities", pid) == []


def test_team_crud_defaults_scope_and_delete_protection(saved):
    conn, pid = saved
    team = teams.save_team(conn, pid, dict(name="Cuadrilla", regime="7x7"))
    assert team["holidays_work"] is True
    team = teams.save_team(conn, pid, dict(regime="5x2"), team["id"])
    assert team["holidays_work"] is False
    cal = WorkCalendar(repo.get(conn, "calendars", team["calendar_id"]), START, END)
    assert not cal.is_workday(START)  # Año Nuevo
    assert not cal.is_workday(date(2027, 1, 2))
    assert not cal.is_workday(date(2027, 1, 3))
    repo.create(conn, "periodic_tasks", dict(project_id=pid, code="T", name="T", first_due=START,
        quantity_total=1, batch_size=1, productivity_day_team=1, unit="paneles", team_ids=[team["id"]]))
    with pytest.raises(ValueError, match="tareas maestras"):
        repo.delete(conn, "resources", team["id"])
    other = repo.create(conn, "projects", dict(name="Otro", mode="mantenimiento", start_date=START, end_date=END))
    with pytest.raises(ValueError, match="mismo plan"):
        repo.create(conn, "periodic_tasks", dict(project_id=other.id, code="T", name="T", first_due=START,
            quantity_total=1, batch_size=1, productivity_day_team=1, unit="paneles", team_ids=[team["id"]]))


def test_api_crud_generation_alerts_and_summary(saved):
    from fastapi.testclient import TestClient
    from ganttier.main import app
    from ganttier.api.dependencies import connection
    conn, pid = saved
    app.dependency_overrides[connection] = lambda: conn
    try:
        client = TestClient(app)
        created = client.post(f"/api/projects/{pid}/teams", json=dict(name="Equipo API", regime="5x2"))
        assert created.status_code == 201, created.text
        rid = created.json()["id"]
        changed = client.put(f"/api/projects/{pid}/teams/{rid}", json=dict(members=4))
        assert changed.status_code == 200 and changed.json()["members"] == 4
        assert client.delete(f"/api/projects/{pid}/teams/{rid}").status_code == 204
        populate(conn, pid, "7x7")
        response = client.post(f"/api/projects/{pid}/maintenance/generate", json=dict(horizon_start=str(START), horizon_end=str(END)))
        assert response.status_code == 200 and response.json()["applied"] is False, response.text
        summary = client.get(f"/api/projects/{pid}/maintenance/distribution")
        assert summary.status_code == 200 and summary.json()["warnings"]
        from ganttier.control.alerts import evaluate
        assert any(a["rule"] == "equipos_reparto" for a in evaluate(conn, pid))
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("regime", ["7x7", "14x14"])
def test_feasible_shift_batches_never_cross_rest(saved, regime):
    conn, pid = saved
    task, rs = populate(conn, pid, regime, quantity=24000)
    if regime == "14x14":
        teams.save_team(conn, pid, dict(cycle_start=date(2027, 1, 15)), rs[1]["id"])
    result = services.generate_maintenance(conn, pid, START, END)
    assert result["applied"], result
    problem = services.build_problem(conn, pid)
    for a in problem.activities:
        assert (a.planned_finish-a.planned_start).days == 1
        assert all(problem.calendars[a.calendar_id].is_workday(a.planned_start+timedelta(days=i)) for i in (0,1))
    assert verify_schedule(problem, {a.id:a.planned_start for a in problem.activities},
                           {a.id:a.planned_finish for a in problem.activities}).feasible


def test_apply_rejects_tampered_team_assignment(saved):
    conn, pid = saved
    _, rs = populate(conn, pid)
    assert services.generate_maintenance(conn, pid, START, END)["applied"]
    before = repo.list_entities(conn, "assignments", pid)
    acts = repo.list_entities(conn, "activities", pid)
    target = acts[0]
    wrong = next(r["id"] for r in rs if r["id"] != target.team_id)
    payload = dict(starts={str(a.id): str(a.planned_start) for a in acts},
                   finishes={str(a.id): str(a.planned_finish) for a in acts},
                   worker_assignment={str(target.id): wrong})
    run = services._save_run(conn, pid, "maintenance_level", payload, status="factible")
    with pytest.raises(ValueError, match="asignación.*equipo"):
        services.apply_run(conn, pid, run)
    assert repo.list_entities(conn, "assignments", pid) == before


def test_short_frequency_chooses_team_with_a_continuous_slot():
    task, resources, calendars = plan("14x14", quantity=6000, batch=6000)
    task = replace(task, frequency="W", first_due=date(2027,1,14))
    rows, _ = expand(task, resources, calendars, first=date(2027,1,14), last=date(2027,1,20))
    assert len(rows) == 1 and rows[0].team_id == 2  # A tiene un día; B tiene seis continuos.


def test_task_start_inside_cycle_does_not_raise_partial_period_warning():
    task, resources, calendars = plan("14x14", quantity=2000, batch=1000)
    task = replace(task, frequency="W", first_due=date(2027,1,8))
    end = date(2027,1,28)
    calendars = {cid: WorkCalendar(cal.cal, START, end) for cid, cal in calendars.items()}
    rows, _ = expand(task, resources, calendars, last=end)
    _, warnings, _ = teams.summarize_occurrences(rows, resources, calendars, [task])
    assert not warnings


def test_leveling_reserves_other_activity_of_the_same_team(saved):
    conn, pid = saved
    _, resources = populate(conn, pid, quantity=4000)
    r = resources[0]
    fixed = repo.create(conn, "activities", dict(project_id=pid, code="OTRA", name="Otra planta",
        calendar_id=r["calendar_id"], duration_days=10, work_hours=240, crew_size=3,
        planned_start=date(2027,1,11), planned_finish=date(2027,1,22), locked=True))
    repo.create(conn, "assignments", dict(activity_id=fixed.id, resource_id=r["id"], units=3, work_hours=240))
    result = services.generate_maintenance(conn, pid, START, END)
    assert result["applied"], result
    for a in repo.list_entities(conn, "activities", pid):
        if a.batch_index is not None and a.team_id == r["id"]:
            assert a.planned_finish < fixed.planned_start or a.planned_start > fixed.planned_finish
    assert repo.get(conn, "activities", fixed.id) == fixed


def test_feasible_simulation_does_not_create_inventory(saved):
    from ganttier.solve import solve_project
    conn, pid = saved
    populate(conn, pid)
    result = solve_project(conn, pid, apply=False)
    assert result["feasible"] and result["simulated"] and not result["applied"], result
    assert not repo.list_entities(conn, "activities", pid)
    assert not repo.list_entities(conn, "schedule_runs", pid)


def test_simulation_of_existing_batches_remains_reviewable_and_applicable(saved):
    conn, pid = saved
    populate(conn, pid)
    assert services.generate_maintenance(conn, pid, START, END)["applied"]
    before = services._scenario_fingerprint(conn, pid)
    result = services.run_optimize(conn, pid, apply=False)
    assert result["feasible"] and result["run_id"] is not None and not result["applied"]
    assert services._scenario_fingerprint(conn, pid) == before
    assert services.apply_run(conn, pid, result["run_id"])["applied"]


@pytest.mark.parametrize("weekly", [True, False])
def test_14x14_generation_and_independent_audit_by_cycle_or_occurrence(saved, weekly):
    conn, pid = saved
    end = date(2027,1,28 if weekly else 31)
    repo.update(conn, "projects", pid, {"end_date":end})
    task, rs = populate(conn, pid, "14x14", quantity=4000 if weekly else 28000)
    teams.save_team(conn, pid, dict(cycle_start=date(2027,1,15)), rs[1]["id"])
    if weekly:
        repo.update(conn, "periodic_tasks", task.id, {"frequency":"W"})
    result = services.generate_maintenance(conn, pid, START, end)
    assert result["applied"], result
    acts = repo.list_entities(conn, "activities", pid)
    assert Counter(a.team_id for a in acts) == {r["id"]:4 if weekly else 7 for r in rs}
    if weekly:
        assert not any("acumulado por" in w or "días hábiles distintos" in w for w in result["warnings"])
        assert all(len({a.team_id for a in acts if a.occurrence_index == i}) == 1 for i in range(1,5))
    problem = services.build_problem(conn, pid)
    assert verify_schedule(problem, {a.id:a.planned_start for a in acts}, {a.id:a.planned_finish for a in acts}).feasible
