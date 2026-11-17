"""Aceptación de aislamiento, vista previa, copias completas y atomicidad."""
from dataclasses import asdict
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from ganttier import repo
from ganttier.api.dependencies import connection
from ganttier.calendars import WorkCalendar
from ganttier.main import create_app
from ganttier.resource_absences import list_absences, set_absence

START, END = date(2026, 10, 5), date(2026, 10, 9)


@pytest.fixture
def workers(conn, project):
    cal = repo.create(conn, "calendars", {"name": "Semana", "holidays_country": None})
    repo.update(conn, "projects", project.id, {"calendar_id": cal.id})
    workers = [repo.create(conn, "resources", {"project_id": project.id, "name": name,
        "calendar_id": cal.id}) for name in ("Ana", "Luis")]
    return cal, workers


def total(conn, cid):
    cal = WorkCalendar(repo.get(conn, "calendars", cid), START, END)
    return sum(cal.hours(START + timedelta(days=i)) for i in range(5))


def test_shared_workers_preview_apply_reuse_partial_and_assignments(conn, project, workers):
    source, (ana, luis) = workers
    a = repo.create(conn, "activities", {"project_id": project.id, "code": "A", "name": "Desde antes",
        "planned_start": "2026-10-01", "planned_finish": "2026-10-06"})
    b = repo.create(conn, "activities", {"project_id": project.id, "code": "B", "name": "Después",
        "planned_start": "2026-10-10", "planned_finish": "2026-10-12"})
    assignment = repo.create(conn, "assignments", {"activity_id": a.id, "resource_id": ana.id})
    repo.create(conn, "assignments", {"activity_id": b.id, "resource_id": ana.id})
    repo.create(conn, "assignments", {"activity_id": a.id, "skill": "Eléctrica"})
    before = conn.total_changes
    preview = set_absence(conn, ana.id, START, END, 0, "Capacitación", dry_run=True)
    assert conn.total_changes == before
    assert preview["source_calendar"] == {"id": source.id, "name": "Semana"}
    assert preview["will_create_copy"] and preview["calendar"]["name"] == "Semana · Ana"
    assert preview["hours_before"] == 40 and preview["hours_after"] == 0
    assert [r["assignment_id"] for r in preview["assignments"]] == [assignment.id]
    assert list_absences(conn, ana.id) == []
    result = set_absence(conn, ana.id, START, END, 0, "Capacitación")
    cid = result["calendar"]["id"]
    assert cid != source.id and repo.get(conn, "resources", ana.id).calendar_id == cid
    assert total(conn, cid) == 0
    assert total(conn, source.id) == total(conn, repo.get(conn, "resources", luis.id).calendar_id) == 40
    assert repo.get(conn, "activities", a.id) == a
    assert repo.get(conn, "assignments", assignment.id) == assignment
    partial = set_absence(conn, ana.id, START, START, 4, "Parcial", dry_run=True)
    assert not partial["will_create_copy"]
    assert partial["days"][0]["previous_exception"]["reason"] == "Capacitación"
    assert partial["hours_before"] == 0 and partial["hours_after"] == 4
    applied = set_absence(conn, ana.id, START, START, 4, "Parcial")
    assert applied["calendar"]["id"] == cid and total(conn, cid) == 4
    rows = list_absences(conn, ana.id)
    assert len(rows) == 5 and rows[0]["hours"] == 4 and rows[0]["reason"] == "Parcial"
    assert all(r["hours"] == 0 for r in rows[1:])
    assert WorkCalendar(repo.get(conn, "calendars", cid), START, END).hours(date(2026, 10, 2)) == 8


def test_inherited_shift_holidays_and_all_exceptions_copied(conn, project, workers):
    source, (ana, _) = workers
    source = repo.update(conn, "calendars", source.id, {"workweek": "1111110", "hours_per_day": 12,
        "shift_pattern": "7x7", "shift_anchor": "2026-01-01", "holidays_country": "CL",
        "holidays_apply_to_shift": True, "exceptions": [
            {"date": "2026-01-01", "working": True, "hours": 3, "reason": "Anterior"},
            {"date": "2026-10-05", "working": True, "hours": 6, "reason": "Coincidente"},
            {"date": "2027-01-01", "working": False, "hours": 0, "reason": "Futura"}]})
    repo.update(conn, "resources", ana.id, {"calendar_id": None})
    result = set_absence(conn, ana.id, START, START, 4, "Parcial")
    copied = repo.get(conn, "calendars", result["calendar"]["id"])
    assert result["days"][0]["hours_before"] == 6
    for key in ("workweek", "hours_per_day", "shift_pattern", "shift_anchor", "holidays_country", "holidays_apply_to_shift"):
        assert getattr(copied, key) == getattr(source, key)
    assert copied.exceptions[0] == source.exceptions[0] and copied.exceptions[2] == source.exceptions[2]
    assert repo.get(conn, "calendars", source.id) == source


@pytest.mark.parametrize("table", ["resources", "projects", "activities", "periodic_tasks", None])
def test_exclusivity_checks_every_reference(conn, project, workers, table):
    _, (ana, _) = workers
    personal = repo.create(conn, "calendars", {"name": "Personal", "holidays_country": None})
    repo.update(conn, "resources", ana.id, {"calendar_id": personal.id})
    if table:
        values = {"name": "Uso", "calendar_id": personal.id}
        if table == "projects":
            values["start_date"] = "2026-01-01"
        else:
            values["project_id"] = project.id
        if table in ("activities", "periodic_tasks"):
            values["code"] = "USO"
        repo.create(conn, table, values)
    result = set_absence(conn, ana.id, START, END, 0, "Curso")
    assert result["will_create_copy"] == (table is not None)
    assert (result["calendar"]["id"] == personal.id) == (table is None)


@pytest.mark.parametrize("stage", ["exceptions", "resource"])
def test_failure_rolls_back_calendar_exceptions_and_resource(conn, workers, monkeypatch, stage):
    source, (ana, _) = workers
    before = [tuple(r) for r in conn.execute("SELECT * FROM calendars")]
    update = repo.update
    def fail(conn, table, entity_id, changes):
        value = update(conn, table, entity_id, changes)
        if (stage == "exceptions" and table == "calendars") or (stage == "resource" and table == "resources"):
            raise RuntimeError("Fallo inducido antes del commit")
        return value
    monkeypatch.setattr(repo, "update", fail)
    with pytest.raises(RuntimeError, match="Fallo inducido"):
        set_absence(conn, ana.id, START, END, 0, "Curso")
    assert [tuple(r) for r in conn.execute("SELECT * FROM calendars")] == before
    assert repo.get(conn, "resources", ana.id) == ana
    assert repo.get(conn, "calendars", source.id) == source
    assert conn.execute("SELECT COUNT(*) FROM calendar_exceptions").fetchone()[0] == 0


def test_http_errors_and_listing(conn, workers):
    _, (ana, _) = workers
    app = create_app()
    app.dependency_overrides[connection] = lambda: conn
    client = TestClient(app)
    try:
        url = f"/api/resources/{ana.id}/absences"
        body = {"start": START.isoformat(), "end": END.isoformat(), "hours": 4, "reason": "Curso"}
        assert client.post(url + "?dry_run=true", json=body).json()["hours_after"] == 20
        assert client.get(url).json() == []
        assert client.post(url, json=body).status_code == 200
        assert len(client.get(url).json()) == 5
        for changes in ({"hours": -1}, {"hours": 25}, {"hours": "NaN"}, {"reason": "  "},
                        {"reason": "x" * 201}, {"end": "2026-10-04"}, {"start": "inválida"}, {"extra": 1}):
            assert client.post(url, json={**body, **changes}).status_code == 422
        for changes, text in (({"project_id": None}, "globales"), ({"kind": "cuadrilla"}, "cuadrillas"),
                              ({"max_units": 2}, "max_units=1")):
            resource = repo.create(conn, "resources", {**asdict(ana), **changes})
            response = client.post(f"/api/resources/{resource.id}/absences", json=body)
            assert response.status_code == 422 and text in response.json()["detail"]
        assert client.get("/api/resources/999999/absences").status_code == 404
        assert client.post("/api/resources/999999/absences", json=body).status_code == 404
    finally:
        client.close()


def test_default_inheritance_and_name_collision(conn, project, workers):
    _, (ana, _) = workers
    repo.update(conn, "projects", project.id, {"calendar_id": None})
    repo.update(conn, "resources", ana.id, {"calendar_id": None})
    default = repo.list_entities(conn, "calendars")[0]
    repo.create(conn, "calendars", {"name": f"{default.name} · {ana.name}"})
    result = set_absence(conn, ana.id, START, END, 0, "Curso")
    assert result["source_calendar"]["id"] == default.id and result["will_create_copy"]
    assert result["calendar"]["name"].startswith(f"{default.name} · {ana.name} (")


def test_rechecks_sharing_after_preview_and_lists_early_dates(conn, project, workers):
    _, (ana, luis) = workers
    cid = set_absence(conn, ana.id, START, START, 4, "Parcial")["calendar"]["id"]
    preview = set_absence(conn, ana.id, START, END, 0, "Curso", dry_run=True)
    assert not preview["will_create_copy"]
    # Otro proceso comparte el calendario después de abrir la vista previa.
    repo.update(conn, "resources", luis.id, {"calendar_id": cid})
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "CPM",
        "name": "Calculada", "early_start": "2026-10-05", "early_finish": "2026-10-09"})
    repo.create(conn, "assignments", {"activity_id": activity.id, "resource_id": ana.id})
    result = set_absence(conn, ana.id, START, END, 0, "Curso")
    assert result["will_create_copy"] and result["calendar"]["id"] != cid
    assert result["assignments"][0]["activity_id"] == activity.id
    assert result["assignments"][0]["planned_start"] == "2026-10-05"
    assert total(conn, cid) == 36
    assert repo.get(conn, "activities", activity.id) == activity


def test_reused_calendar_rollback(conn, workers, monkeypatch):
    _, (ana, _) = workers
    cid = set_absence(conn, ana.id, START, END, 4, "Parcial")["calendar"]["id"]
    before = repo.get(conn, "calendars", cid)
    update = repo.update
    def fail(conn, table, entity_id, changes):
        if table == "resources":
            raise RuntimeError("Fallo en recurso")
        return update(conn, table, entity_id, changes)
    monkeypatch.setattr(repo, "update", fail)
    with pytest.raises(RuntimeError):
        set_absence(conn, ana.id, START, END, 0, "Curso")
    assert repo.get(conn, "calendars", cid) == before


def test_ausencia_no_agrega_horas_en_dias_no_laborables(conn, project, workers):
    """A5: una disponibilidad parcial no convierte un fin de semana en laborable ni supera la jornada base."""
    source, (ana, _luis) = workers
    saturday, sunday = date(2026, 10, 10), date(2026, 10, 11)
    base = WorkCalendar(repo.get(conn, "calendars", source.id), START, sunday)
    assert base.hours(saturday) == 0 and base.hours(sunday) == 0
    result = set_absence(conn, ana.id, START, sunday, 4, "Media jornada")
    cid = result["calendar"]["id"]
    cal = WorkCalendar(repo.get(conn, "calendars", cid), START, sunday)
    assert cal.hours(saturday) == 0 and cal.hours(sunday) == 0
    assert all(cal.hours(START + timedelta(days=i)) == 4 for i in range(5))
    assert result["hours_after"] == 20
    assert {e.date for e in repo.get(conn, "calendars", cid).exceptions} == {START + timedelta(days=i) for i in range(5)}
    over = set_absence(conn, ana.id, START, START, 12, "Error de digitación", dry_run=True)
    assert over["days"][0]["hours_after"] == base.hours(START)  # nunca más que la jornada base
