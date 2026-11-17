"""Diagnóstico con SQLite temporal y comprobación explícita de solo lectura."""
from datetime import date
import json
from pathlib import Path

import pytest

from ganttier import db, repo
from ganttier.optimizacion.diagnostico import diagnosticar


@pytest.fixture
def case(tmp_path):
    conn = db.connect(tmp_path / "diagnostico.sqlite")
    db.initialize(conn)
    cal = repo.create(conn, "calendars", dict(name="Continuo API", workweek="1111111", holidays_country=None))
    project = repo.create(conn, "projects", dict(name="API", start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 10), calendar_id=cal.id))
    worker = repo.create(conn, "resources", dict(name="Persona", project_id=project.id,
        skills=["E"], calendar_id=cal.id, capacity_hours_day=8))
    def activity(**kwargs):
        return repo.create(conn, "activities", dict(project_id=project.id, code=f"A{conn.total_changes}",
            name="Actividad", duration_days=1, work_hours=8, required_skill="E", **kwargs))
    yield conn, project, worker, activity
    conn.close()


def diagnose(conn, project, **kwargs):
    return diagnosticar(conn, project.id, hasta=date(2026, 1, 10), **kwargs)


@pytest.mark.parametrize("cause", ["intensity", "window", "skill", "milestone"])
def test_proven_and_read_only(case, cause):
    conn, p, worker, activity = case
    a = activity()
    changes = {"intensity": dict(work_hours=80), "window": dict(duration_days=3, window_hard=True,
        window_start=date(2026, 1, 1), window_end=date(2026, 1, 1)),
        "skill": dict(required_skill="Inexistente"), "milestone": dict(duration_days=0)}[cause]
    repo.update(conn, "activities", a.id, changes)
    before = list(conn.iterdump())
    conn.execute("PRAGMA query_only=ON")
    result = diagnose(conn, p)
    assert result["feasibility_hint"] == "demostrada_infactible"
    assert result["advice"] and result["timings"]["total_s"] > 0
    assert list(conn.iterdump()) == before


def test_cutoff_and_cycle_are_distinguished(case):
    conn, p, _, activity = case
    a, b = activity(), activity()
    repo.create(conn, "relations", dict(project_id=p.id, pred_id=a.id, succ_id=b.id))
    # Simular una base heredada corrupta; repo impide crear ciclos normalmente.
    with conn:
        conn.execute("INSERT INTO relations(project_id,pred_id,succ_id,type) VALUES (?,?,?,'FS')", (p.id, b.id, a.id))
    result = diagnose(conn, p)
    assert any("Ciclo" in e for e in result["network_errors"])
    assert result["feasibility_hint"] == "incierta"
    repo.update(conn, "projects", p.id, dict(data_date=date(2026, 1, 3)))
    repo.update(conn, "activities", a.id, dict(window_hard=True, window_start=date(2026, 1, 1), window_end=date(2026, 1, 3)))
    result = diagnose(conn, p)
    assert result["hard_window_conflicts"][0]["available_days"] == 0
    assert result["feasibility_hint"] == "demostrada_infactible"


def test_absence_partial_shift_and_periods(case):
    from ganttier.resource_absences import set_absence
    conn, p, worker, activity = case
    activity(planned_start=date(2026, 1, 1), planned_finish=date(2026, 1, 1))
    set_absence(conn, worker.id, date(2026, 1, 1), date(2026, 1, 1), 2, "Permiso parcial")
    result = diagnose(conn, p, periodo="dia")
    first = result["capacity_by_skill"][0]
    assert first["capacity_hours"] == 2 and first["demand_hours"] == 8
    assert result["feasibility_hint"] != "demostrada_infactible"
    daily_total = sum(r["capacity_hours"] for r in result["capacity_by_skill"])
    for period in ("semana", "mes"):
        assert sum(r["capacity_hours"] for r in diagnose(conn, p, periodo=period)["capacity_by_skill"]) == daily_total
    shift = repo.create(conn, "calendars", dict(name="2x2", shift_pattern="2x2",
        shift_anchor=date(2026, 1, 1), hours_per_day=12, holidays_country=None))
    repo.update(conn, "resources", worker.id, dict(calendar_id=shift.id, capacity_hours_day=12))
    assert sum(r["capacity_hours"] for r in diagnose(conn, p)["capacity_by_skill"]) == 72


def test_explicit_assignments_override_activity_and_milestones(case):
    conn, p, worker, activity = case
    a = activity()
    repo.update(conn, "activities", a.id, dict(work_hours=1000, required_skill="Ausente"))
    repo.create(conn, "assignments", dict(activity_id=a.id, resource_id=worker.id, work_hours=8, skill="E"))
    result = diagnose(conn, p)
    assert not result["intensity_violations"] and not result["unassigned_skills"]
    assert sum(r["demand_hours"] for r in result["capacity_by_skill"]) == 8
    repo.update(conn, "activities", a.id, dict(duration_days=0, work_hours=0))
    assert diagnose(conn, p)["feasibility_hint"] == "demostrada_infactible"


def test_completed_history_and_zero_work_milestone_not_infeasible(case):
    conn, p, _, activity = case
    a = activity()
    repo.update(conn, "activities", a.id, dict(duration_days=0, work_hours=0, required_skill="Inexistente"))
    b = activity()
    repo.update(conn, "activities", b.id, dict(actual_start=date(2025, 12, 1), actual_finish=date(2025, 12, 2), percent_complete=100,
                                              required_skill="Inexistente", work_hours=1000))
    assert diagnose(conn, p)["feasibility_hint"] != "demostrada_infactible"


def test_explicit_intensity_and_overtime_upper_bound(case):
    conn, p, worker, activity = case
    a = activity()
    repo.create(conn, "assignments", dict(activity_id=a.id, resource_id=worker.id, work_hours=10, skill="E"))
    assert diagnose(conn, p)["feasibility_hint"] == "demostrada_infactible"
    repo.update(conn, "resources", worker.id, dict(max_overtime_hours_day=2))
    assert diagnose(conn, p)["feasibility_hint"] != "demostrada_infactible"


def test_maintenance_implicit_demand_tracks_partial_calendar(case):
    conn, p, _, activity = case
    repo.update(conn, "projects", p.id, dict(mode="mantenimiento"))
    repo.update(conn, "calendars", p.calendar_id, dict(exceptions=[dict(date=date(2026, 1, 1), working=True, hours=2)]))
    a = activity(planned_start=date(2026, 1, 1), planned_finish=date(2026, 1, 2))
    repo.update(conn, "activities", a.id, dict(duration_days=2, work_hours=0))
    assert sum(row["demand_hours"] for row in diagnose(conn, p)["capacity_by_skill"]) == 10


def test_maintenance_generates_in_memory_and_teams(case):
    conn, p, worker, _ = case
    repo.update(conn, "projects", p.id, dict(mode="mantenimiento"))
    repo.update(conn, "calendars", p.calendar_id, dict(workweek="1111100"))
    repo.update(conn, "resources", worker.id, dict(kind="cuadrilla", is_work_team=True, max_units=2))
    repo.create(conn, "periodic_tasks", dict(project_id=p.id, code="T", name="Tarea por lote",
        frequency="M", first_due=date(2026, 1, 2), required_skill="E", quantity_total=4,
        batch_size=2, unit="equipos", productivity_day_team=2, team_ids=[worker.id], tol_before_days=1, tol_after_days=5))
    before = list(conn.iterdump())
    conn.execute("PRAGMA query_only=ON")
    result = diagnose(conn, p)
    assert result["counts"]["generated_in_memory"] > 0
    assert result["team_summary"]
    assert sum(r["demand_hours"] for r in result["capacity_by_skill"]) > 0
    assert list(conn.iterdump()) == before


def test_100000_activities_scale(tmp_path):
    """Evidencia real: 100 000 actividades, sin resolver ni abrir datos reales."""
    conn = db.connect(tmp_path / "scale.sqlite")
    db.initialize(conn)
    cal = repo.create(conn, "calendars", dict(name="Escala API", workweek="1111111", holidays_country=None))
    p = repo.create(conn, "projects", dict(name="100k", mode="mantenimiento", start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31), calendar_id=cal.id))
    repo.create(conn, "resources", dict(name="Pool", project_id=p.id, skills=["E"], max_units=1000, calendar_id=cal.id))
    with conn:
        conn.executemany("INSERT INTO activities(project_id,code,name,duration_days,work_hours,required_skill) VALUES (?,?,?,1,8,'E')",
                         ((p.id, f"A{i}", f"Actividad {i}") for i in range(100000)))
    conn.execute("PRAGMA query_only=ON")
    result = diagnosticar(conn, p.id)
    conn.close()
    assert result["counts"]["activities"] == 100000
    assert sum(r["demand_hours"] for r in result["capacity_by_skill"]) == 800000
    evidence = Path(__file__).resolve().parents[3] / "trabajos/cli_codex_20261019/agentes/API/benchmark_100000.json"
    if evidence.parent.is_dir():
        evidence.write_text(json.dumps({"counts": result["counts"], "timings": result["timings"]}, indent=2), encoding="utf-8")
