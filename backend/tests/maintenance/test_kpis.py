"""Plan comprometido, fechas de corte, tolerancia y backlog preventivo."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from ganttier import db, repo
from ganttier.main import create_app
from ganttier.maintenance.kpis import kpis


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "kpis.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    with TestClient(create_app()) as client:
        conn = db.connect()
        project = repo.create(conn, "projects", {
            "name": "Preventivo", "mode": "mantenimiento", "start_date": date(2026, 1, 1),
            "end_date": date(2026, 12, 31), "data_date": date(2026, 5, 1)})
        task = repo.create(conn, "periodic_tasks", {
            "project_id": project.id, "code": "P", "name": "Inspección", "tol_after_days": 2})
        try:
            yield conn, client, project, task
        finally:
            conn.close()


def occurrence(data, code, due, **fields):
    conn, _, project, task = data
    return repo.create(conn, "activities", {
        "project_id": project.id, "periodic_task_id": task.id,
        "code": code, "name": code, "due_date": due,
        "work_hours": 10, "location": "Norte", "required_skill": "Eléctrica", **fields})


def test_ten_due_sixty_eighty_and_committed_denominator(data):
    conn, client, project, _ = data
    for i in range(6):
        occurrence(data, f"T{i}", date(2026, 4, 1), percent_complete=100,
                   actual_finish=date(2026, 4, 3))  # tolerancia inclusiva
    for i in range(2):
        occurrence(data, f"L{i}", date(2026, 4, 1), percent_complete=100,
                   actual_finish=date(2026, 4, 4))
    oldest = occurrence(data, "B1", date(2026, 1, 1), priority=70, work_hours=16)
    occurrence(data, "B2", date(2026, 4, 25), priority=39, work_hours=8,
               percent_complete=25, location="Sur", required_skill="Mecánica")
    result = client.get(f"/api/projects/{project.id}/maintenance/kpis")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["compliance"]["due"] == 10
    assert body["compliance"]["on_time_pct"] == 60
    assert body["compliance"]["total_pct"] == 80
    backlog = body["backlog"]
    assert backlog["count"] == 2
    assert backlog["remaining_hours"] == 22
    assert backlog["oldest"][0]["activity_id"] == oldest.id
    assert {r["key"]: r["count"] for r in backlog["by_age"]} == {
        "0–7": 1, "8–30": 0, "31–90": 0, ">90": 1}
    assert {r["key"]: r["count"] for r in backlog["by_priority"]} == {"alta": 1, "media": 0, "baja": 1}
    assert sum(r["due"] for r in body["by_location"]) == 10
    assert sum(r["due"] for r in body["by_skill"]) == 10
    assert sum(r["due"] for r in body["by_month"]) == 10
    filtered = kpis(conn, project.id, date(2026, 4, 1), date(2026, 4, 1))
    assert filtered["compliance"]["due"] == 8
    assert filtered["compliance"]["total_pct"] == 100


def test_no_due_is_no_data_and_future_population_is_not_denominator(data):
    conn, _, project, _ = data
    occurrence(data, "F", date(2026, 6, 1))
    result = kpis(conn, project.id, end=date(2026, 12, 31))
    assert result["status"] == "sin datos"
    assert result["compliance"]["population"] == 1
    assert result["compliance"]["on_time_pct"] is None
    assert result["compliance"]["total_pct"] is None
    assert result["backlog"]["status"] == "sin datos"


def test_original_due_future_finish_and_no_invented_finish(data):
    conn, _, project, _ = data
    repo.update(conn, "projects", project.id, {"data_date": date(2026, 6, 1)})
    occurrence(data, "F", date(2026, 4, 1), planned_finish=date(2026, 6, 1),
               actual_finish=date(2026, 6, 1), percent_complete=100)
    repo.update(conn, "projects", project.id, {"data_date": project.data_date})
    occurrence(data, "U", date(2026, 4, 1), percent_complete=100)
    result = kpis(conn, project.id)
    assert result["compliance"]["due"] == 2
    assert result["compliance"]["total_pct"] == 50
    assert result["compliance"]["on_time_pct"] is None
    assert result["compliance"]["completion_date_missing"] == 1
    assert result["backlog"]["count"] == 1


def test_ledger_uses_cutoff_and_explicit_actual_finish(data):
    conn, _, project, _ = data
    a = occurrence(data, "L", date(2026, 4, 1), percent_complete=100)
    repo.create_many(conn, "progress", [
        {"activity_id": a.id, "date": date(2026, 4, 2), "percent_complete": 50,
         "actual_start": date(2026, 4, 1)},
        {"activity_id": a.id, "date": date(2026, 6, 1), "percent_complete": 100,
         "actual_finish": date(2026, 6, 1)}])
    result = kpis(conn, project.id)
    assert result["compliance"]["total_pct"] == 0
    assert result["backlog"]["remaining_hours"] == 5


def test_age_boundaries_top_twenty_and_priority_boundaries(data):
    conn, _, project, _ = data
    for i, age in enumerate([0, 7, 8, 30, 31, 90, 91] + [100] * 20):
        occurrence(data, str(i), project.data_date - timedelta(days=age), priority=[39, 40, 69, 70][i % 4])
    result = kpis(conn, project.id)["backlog"]
    assert result["count"] == 27
    assert [r["count"] for r in result["by_age"]] == [2, 2, 2, 21]
    assert len(result["oldest"]) == 20
    assert all(r["age_days"] == 100 for r in result["oldest"])


def test_invalid_range_and_missing_project(data):
    _, client, project, _ = data
    url = f"/api/projects/{project.id}/maintenance/kpis"
    assert client.get(url, params={"start": "2026-06-01", "end": "2026-01-01"}).status_code == 422
    assert client.get(url, params={"start": "ayer"}).status_code == 422
    assert client.get("/api/projects/99999/maintenance/kpis").status_code == 404
