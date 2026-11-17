"""A4-04 y A4-06: limpieza de simulaciones y prechequeo con asignaciones genéricas."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier

import pytest

from ganttier import backups, db, domain as d, repo, services
from ganttier.control import alerts
from ganttier.solve import solve_project


@pytest.fixture
def data(tmp_path, monkeypatch):
    path = tmp_path / "a4.db"
    monkeypatch.setattr(services, "DATA_DIR", tmp_path / "data")
    conn = db.connect(path)
    db.initialize(conn)
    cid = repo.create(conn, "calendars", {"name": "A4", "holidays_country": None}).id
    project = repo.create(conn, "projects", {"name": "A4", "mode": "mantenimiento",
        "start_date": "2026-01-05", "end_date": "2026-01-31", "data_date": "2026-01-05",
        "calendar_id": cid})
    repo.create(conn, "resources", {"project_id": project.id, "name": "Ana", "skills": ["electrico"],
        "calendar_id": cid})
    repo.create(conn, "periodic_tasks", {"project_id": project.id, "code": "M", "name": "Mensual",
        "frequency": "M", "first_due": "2026-01-05", "work_hours": 8,
        "required_skill": "electrico", "calendar_id": cid})
    yield conn, project, path
    conn.close()


def test_simulation_cleanup_runs_while_writers_are_still_excluded(data, monkeypatch):
    """A4-04: el JSON de la simulación se borra antes de liberar el SAVEPOINT; otro escritor no puede
    confirmar entretanto una corrida que reutilice ese ID (y su archivo)."""
    import sqlite3
    conn, project, path = data
    other = db.connect(path)
    other.execute("PRAGMA busy_timeout=0")
    original_unlink = Path.unlink
    observed = []

    def check_at_unlink(file, *args, **kwargs):
        if file.parent == services.DATA_DIR / "runs" and file.suffix == ".json":
            observed.append(conn.in_transaction)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                services._save_run(other, project.id, "optimize", {"owner": "otra peticion"})
        return original_unlink(file, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", check_at_unlink)
    try:
        result = solve_project(conn, project.id, options={"method": "heuristic", "time_limit_s": 1}, apply=False)
        assert result["simulated"] and result["run_id"] is None
        assert observed == [True]
        monkeypatch.setattr(Path, "unlink", original_unlink)
        run_id = services._save_run(other, project.id, "optimize", {"owner": "otra peticion"})
        assert Path(repo.get(other, "schedule_runs", run_id)["result_path"]).is_file()
    finally:
        other.close()


def test_simulation_preserves_callers_pending_transaction(data):
    conn, p, _ = data
    conn.execute("BEGIN")
    conn.execute("UPDATE projects SET description='sin confirmar' WHERE id=?", (p.id,))
    result = solve_project(conn, p.id, options={"method": "heuristic", "time_limit_s": 1}, apply=False)
    assert result["simulated"]
    assert conn.in_transaction
    assert repo.get(conn, "projects", p.id).description == "sin confirmar"
    assert not repo.list_entities(conn, "activities", p.id)
    assert not repo.list_entities(conn, "schedule_runs", p.id)
    conn.rollback()
    assert repo.get(conn, "projects", p.id).description == ""


def test_precheck_uses_generic_assignment_demand_instead_of_activity_budget(data):
    from ganttier.engine import optimize
    from ganttier.engine.verify import verify_with_options

    conn, p, _ = data
    repo.update(conn, "projects", p.id, {"mode": "proyecto"})
    a = repo.create(conn, "activities", {"project_id": p.id, "code": "A", "name": "A",
        "duration_days": 1, "work_hours": 100, "required_skill": "electrico"})
    repo.create(conn, "assignments", {"activity_id": a.id, "resource_id": None,
        "skill": "electrico", "work_hours": 8})
    problem = services.build_problem(conn, p.id)
    opts = d.OptimizeOptions(method="heuristic", time_limit_s=1)
    candidate = optimize(problem, d.ObjectiveWeights(), opts)
    proof = verify_with_options(problem, candidate.starts, candidate.finishes, opts)
    assert proof.feasible, proof.violations  # existe un programa validado por el auditor independiente
    result = solve_project(conn, p.id, options={"method": "heuristic", "time_limit_s": 1}, apply=False)
    assert result["feasible"], result


