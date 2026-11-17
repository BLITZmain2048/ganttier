"""Persistencia, reglas, límites y API de alertas con SQLite real."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ganttier import db, repo
from ganttier.control.alerts import evaluate, list_alerts, set_state
from ganttier.main import create_app


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "alerts.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    with TestClient(create_app()) as client:
        conn = db.connect()
        project = repo.create(conn, "projects", {
            "name": "Alertas", "start_date": date(2026, 1, 1), "data_date": date(2026, 5, 1)})
        try:
            yield conn, client, project
        finally:
            conn.close()


def activity(data, code="A", **fields):
    conn, _, project = data
    return repo.create(conn, "activities", {
        "project_id": project.id, "code": code, "name": code, **fields})


def test_ten_evaluations_one_alert_ack_resolve_and_reappear(data):
    conn, _, project = data
    first = evaluate(conn, project.id, today=project.data_date)
    assert len(first) == 1
    assert first[0]["rule"] == "spi_datos_insuficientes"
    for _ in range(9):
        assert evaluate(conn, project.id, today=project.data_date) == first
    alert = first[0]
    recognized = set_state(conn, alert["id"], "reconocida")
    assert evaluate(conn, project.id, today=project.data_date) == [recognized]
    a = activity(data, work_hours=10, planned_start=date(2026, 1, 1), planned_finish=date(2026, 1, 2),
                 percent_complete=100, actual_finish=date(2026, 1, 2))
    resolved = evaluate(conn, project.id, today=project.data_date)
    assert len(resolved) == 1
    assert resolved[0]["state"] == "resuelta"
    assert resolved[0]["first_seen"] == alert["first_seen"]
    repo.update(conn, "activities", a.id, {"work_hours": 0})
    again = evaluate(conn, project.id, today=project.data_date)
    assert len(again) == 1
    assert again[0]["id"] == alert["id"]
    assert again[0]["state"] == "nueva"
    assert "cumple" not in again[0]["title"].lower()


def test_overdue_correction_and_project_isolation(data):
    conn, _, project = data
    a = activity(data, planned_finish=date(2026, 4, 30), percent_complete=0)
    other = repo.create(conn, "projects", {"name": "Otro", "start_date": project.start_date,
                                          "data_date": project.data_date})
    other_rows = evaluate(conn, other.id, today=project.data_date)
    rows = evaluate(conn, project.id, today=project.data_date)
    overdue = next(r for r in rows if r["rule"] == "actividad_vencida")
    set_state(conn, overdue["id"], "reconocida")
    repo.update(conn, "activities", a.id, {"percent_complete": 100})
    resolved = evaluate(conn, project.id, today=project.data_date)
    assert next(r for r in resolved if r["id"] == overdue["id"])["state"] == "resuelta"
    assert list_alerts(conn, other.id) == other_rows


def test_baseline_critical_and_milestone_strict_delay(data):
    conn, _, project = data
    baseline = repo.create(conn, "baselines", {"project_id": project.id, "name": "Base",
                                               "created_at": datetime(2026, 1, 1)})
    repo.update(conn, "projects", project.id, {"active_baseline_id": baseline.id})
    ids = []
    for i, (delay, critical, duration) in enumerate([(5, True, 2), (6, True, 2), (6, False, 0), (6, False, 2)]):
        a = activity(data, str(i), planned_finish=date(2026, 6, 1) + timedelta(days=delay),
                     critical=critical, duration_days=duration)
        ids.append(a.id)
        repo.save_baseline_activities(conn, [{"baseline_id": baseline.id, "activity_id": a.id,
            "start": date(2026, 6, 1), "finish": date(2026, 6, 1), "work_hours": 0, "cost": 0,
            "duration_days": duration}])
    rows = evaluate(conn, project.id, today=project.data_date)
    assert {r["object_key"] for r in rows if r["rule"] == "retraso_linea_base"} == {
        f"act:{ids[1]}", f"act:{ids[2]}"}
    assert all(r["state"] == "resuelta" for r in evaluate(conn, project.id, today=project.data_date,
        delay_days=6) if r["rule"] == "retraso_linea_base")


def test_stale_cutoff_progress_and_future_records(data):
    conn, _, project = data
    a = activity(data, percent_complete=30, actual_start=date(2026, 4, 1))
    repo.create_many(conn, "progress", [
        {"activity_id": a.id, "date": date(2026, 4, 16), "percent_complete": 30,
         "actual_start": date(2026, 4, 1)},
        {"activity_id": a.id, "date": date(2026, 6, 1), "percent_complete": 60}])
    rows = evaluate(conn, project.id, today=project.data_date + timedelta(days=15))
    assert {"corte_desactualizado", "avance_desactualizado"} <= {r["rule"] for r in rows}
    repo.create(conn, "progress", {"activity_id": a.id, "date": date(2026, 4, 17), "percent_complete": 40})
    rows = evaluate(conn, project.id, today=project.data_date + timedelta(days=14))
    assert all(r["state"] == "resuelta" for r in rows if r["rule"] in {
        "corte_desactualizado", "avance_desactualizado"})


def test_low_spi_and_critical_quality(data):
    conn, _, project = data
    repo.update(conn, "projects", project.id, {"data_date": date(2026, 6, 1)})
    # A4-05: un inicio real posterior al corte no gana valor; el inicio real queda dentro del corte.
    activity(data, work_hours=10, planned_start=date(2026, 1, 1), planned_finish=date(2026, 1, 2),
             actual_start=date(2026, 1, 1), percent_complete=20)
    # Fecha real futura (calidad crítica) en una actividad aún no planificada al corte: no aporta PV ni EV.
    activity(data, code="F", work_hours=10, planned_start=date(2026, 6, 1), planned_finish=date(2026, 6, 2),
             actual_start=date(2026, 6, 1))
    repo.update(conn, "projects", project.id, {"data_date": project.data_date})
    rows = evaluate(conn, project.id, today=project.data_date)
    assert any(r["rule"] == "spi_bajo" and "0.2000" in r["detail"] for r in rows)
    assert any(r["rule"] == "calidad_critica" and r["severity"] == "crítica" for r in rows)
    assert not any(r["rule"] == "spi_datos_insuficientes" for r in rows)


def test_maintenance_alert_counts_more_than_oldest_twenty(data):
    conn, _, project = data
    repo.update(conn, "projects", project.id, {"mode": "mantenimiento", "end_date": date(2026, 12, 31)})
    task = repo.create(conn, "periodic_tasks", {"project_id": project.id, "code": "P", "name": "Plan"})
    for i in range(25):
        activity(data, str(i), periodic_task_id=task.id, due_date=date(2026, 1, 1), work_hours=8)
    activity(data, "límite", periodic_task_id=task.id, due_date=date(2026, 4, 1), work_hours=8)
    rows = evaluate(conn, project.id, today=project.data_date)
    alert = next(r for r in rows if r["rule"] == "backlog_antiguo")
    assert "25 ocurrencias" in alert["detail"]
    assert "200" in alert["detail"]
    rows = evaluate(conn, project.id, today=project.data_date, backlog_days=29)
    assert "26 ocurrencias" in next(r for r in rows if r["rule"] == "backlog_antiguo")["detail"]


def test_api_states_summary_validation_and_persistence(data):
    conn, client, project = data
    repo.update(conn, "projects", project.id, {"data_date": date.today() + timedelta(days=1)})
    activity(data, actual_start=date.today() + timedelta(days=1))
    repo.update(conn, "projects", project.id, {"data_date": date.today()})
    url = f"/api/projects/{project.id}/alerts"
    response = client.get(url)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows[0]["severity"] == "crítica"
    aid = rows[0]["id"]
    ack = client.post(f"/api/alerts/{aid}/acknowledge")
    assert ack.status_code == 200 and ack.json()["state"] == "reconocida"
    filtered = client.get(url, params={"state": "reconocida"}).json()
    assert [r["id"] for r in filtered] == [aid]
    summary = client.get(url + "/summary").json()
    assert summary == {"nueva": 1, "reconocida": 1, "critica": 1}
    reopened = client.post(f"/api/alerts/{aid}/reopen")
    assert reopened.status_code == 200 and reopened.json()["state"] == "nueva"
    assert client.get(url).json()[0]["id"] == aid
    assert client.get(url, params={"state": "cumple"}).status_code == 422
    assert client.post("/api/alerts/99999/acknowledge").status_code == 404
    assert client.post("/api/alerts/99999/reopen").status_code == 404
    assert client.get("/api/projects/99999/alerts").status_code == 404
    assert client.get("/api/projects/99999/alerts/summary").status_code == 404
    with pytest.raises(ValueError):
        evaluate(conn, project.id, min_spi=float("nan"))


def test_muchas_actividades_vencidas_se_resumen_en_una_alerta(data):
    """Sobre MAX_ITEM_ALERTS, una sola alerta resumen por regla (evita cientos de alertas)."""
    conn, _, project = data
    for i in range(20):
        activity(data, code=f"V{i:02d}", planned_start=date(2026, 3, 1), planned_finish=date(2026, 3, 1) + timedelta(days=i))
    rows = [r for r in evaluate(conn, project.id, today=project.data_date) if r["rule"] == "actividad_vencida"]
    assert len(rows) == 1 and rows[0]["object_key"] == "resumen"
    assert rows[0]["title"].startswith("20 actividades vencidas") and "V00 (61 días)" in rows[0]["detail"]
    assert "01-05-2026" in rows[0]["detail"]   # fechas en formato dd-mm-aaaa
