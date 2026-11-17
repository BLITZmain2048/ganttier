"""Aceptación de P5-08/P5-09: calendario real y escritura atómica."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from ganttier import repo, tools
from ganttier.api.dependencies import connection
from ganttier.main import create_app


def client_for(conn):
    # Sin lifespan: se usa exclusivamente la base aislada del fixture.
    app = create_app()
    app.dependency_overrides[connection] = lambda: conn
    return TestClient(app)


def activities(conn, pid, codes):
    return repo.create_many(conn, "activities", [dict(project_id=pid, code=c, name=c) for c in codes])


def test_twenty_activities_and_repeat(conn, project):
    codes = [f"A{i:02d}" for i in range(20)]
    acts = activities(conn, project.id, codes)
    client = client_for(conn)
    url = f"/api/projects/{project.id}/relations/chain"
    body = {"groups": [codes], "type": "FS", "lag_days": 0}
    before = conn.total_changes
    preview = client.post(url + "?dry_run=true", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["created"] == 19
    assert all(r["id"] is None for r in preview.json()["relations"])
    assert conn.total_changes == before
    assert repo.list_entities(conn, "relations", project.id) == []
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 19
    assert response.json()["existing"] == 0
    assert [(r["pred_id"], r["succ_id"]) for r in response.json()["relations"]] == [
        (a.id, b.id) for a, b in zip(acts, acts[1:])]
    repeat = client.post(url, json=body).json()
    assert repeat["created"] == 0 and repeat["existing"] == 19
    assert len(repo.list_entities(conn, "relations", project.id)) == 19


def test_groups_do_not_connect_and_duplicate_pairs_count_once(conn, project):
    activities(conn, project.id, "ABCDE")
    result = tools.relation_chain(conn, project.id, [["A", "B", "C"], ["D", "E"], ["A", "B"]])
    assert result["created"] == 3 and result["existing"] == 0
    assert len(result["relations"]) == 3
    repeat = tools.relation_chain(conn, project.id, [["A", "B", "C"], ["D", "E"]], dry_run=True)
    assert repeat["created"] == 0 and repeat["existing"] == 3


@pytest.mark.parametrize("dry_run", [False, True])
def test_cycle_rejects_all_groups(conn, project, dry_run):
    a, b, c, d, e = activities(conn, project.id, "ABCDE")
    repo.create_many(conn, "relations", [dict(project_id=project.id, pred_id=c.id, succ_id=a.id)])
    before = repo.list_entities(conn, "relations", project.id)
    client = client_for(conn)
    response = client.post(f"/api/projects/{project.id}/relations/chain", params={"dry_run": dry_run},
                           json={"groups": [["D", "E"], ["A", "B", "C"]]})
    assert response.status_code == 422
    assert "ciclo" in response.json()["detail"]
    assert repo.list_entities(conn, "relations", project.id) == before


@pytest.mark.parametrize("relation_type,lag", [("SS", 0), ("FS", 2)])
def test_conflicting_relation_rejects_whole_batch(conn, project, relation_type, lag):
    a, b, c, d = activities(conn, project.id, "ABCD")
    repo.create_many(conn, "relations", [dict(project_id=project.id, pred_id=a.id, succ_id=b.id,
                                             type=relation_type, lag_days=lag)])
    response = client_for(conn).post(f"/api/projects/{project.id}/relations/chain",
                                     json={"groups": [["C", "D"], ["A", "B"]]})
    assert response.status_code == 422 and "A → B" in response.json()["detail"]
    assert len(repo.list_entities(conn, "relations", project.id)) == 1


def test_codes_are_scoped_and_missing_codes_are_listed(conn, project):
    activities(conn, project.id, ["A"])
    other = repo.create_many(conn, "projects", [dict(name="Otro", start_date="2026-01-01")])[0]
    activities(conn, other.id, ["B"])
    response = client_for(conn).post(f"/api/projects/{project.id}/relations/chain",
                                     json={"groups": [["A", "B", "C"]]})
    assert response.status_code == 422
    assert "B, C" in response.json()["detail"]
    assert repo.list_entities(conn, "relations") == []


def test_chain_limits_mode_and_types(conn, project):
    activities(conn, project.id, ["A", "B"])
    client = client_for(conn)
    url = f"/api/projects/{project.id}/relations/chain"
    for body in ({"groups": []}, {"groups": [["A"]]}, {"groups": [["A", "A"]]},
                 {"groups": [["A", "B"]], "type": "XX"},
                 {"groups": [["A", "B"]], "lag_days": 1.5},
                 {"groups": [["A"] * 251]}):
        assert client.post(url, json=body).status_code == 422
    maintenance = repo.create_many(conn, "projects", [dict(name="Mantención", mode="mantenimiento",
                                                          start_date="2026-01-01", end_date="2026-12-31")])[0]
    activities(conn, maintenance.id, ["A", "B"])
    assert client.post(f"/api/projects/{maintenance.id}/relations/chain",
                       json={"groups": [["A", "B"]]}).status_code == 422
    result = client.post(url, json={"groups": [["A", "B"]], "type": "SS", "lag_days": -2})
    assert result.status_code == 200
    assert result.json()["relations"][0]["type"] == "SS"
    assert result.json()["relations"][0]["lag_days"] == -2


def test_preview_reuses_validator_inside_existing_transaction(conn, project, monkeypatch):
    activities(conn, project.id, "ABC")
    original = repo._validate_relation_cycles
    calls = []

    def validate(source, pids):
        calls.append(pids)
        return original(source, pids)

    monkeypatch.setattr(repo, "_validate_relation_cycles", validate)
    with repo.transaction(conn):
        before = conn.total_changes
        result = tools.relation_chain(conn, project.id, [["A", "B", "C"]], dry_run=True)
        assert result["created"] == 2
        assert conn.total_changes == before
        assert conn.in_transaction
    assert calls == [{project.id}]


def calendar(conn, **values):
    return repo.create_many(conn, "calendars", [{"name": "Prueba", "holidays_country": None, **values}])[0]


def test_inclusive_acceptance_and_exception_hours(conn):
    plain = calendar(conn, name="Calendario simple")
    special = calendar(conn, exceptions=[dict(date="2026-10-07", working=False, hours=0, reason="Capacitación")])
    client = client_for(conn)
    before = conn.total_changes
    def add(cid, n, start="2026-10-05"):
        response = client.get(f"/api/calendars/{cid}/workdays", params={"start": start, "days": n})
        assert response.status_code == 200, response.text
        return response.json()
    assert add(plain.id, 5)["result"] == "2026-10-09"
    assert add(special.id, 5)["result"] == "2026-10-12"
    assert add(special.id, 0, "2026-10-07")["result"] == "2026-10-07"
    assert add(plain.id, -5, "2026-10-09")["result"] == "2026-10-05"
    assert add(plain.id, -1, "2026-10-11")["result"] == "2026-10-09"
    assert add(plain.id, 1, "2026-10-11")["result"] == "2026-10-12"
    result = client.get(f"/api/calendars/{special.id}/workdays/between",
                        params={"start": "2026-10-05", "end": "2026-10-09"}).json()
    assert result["workdays"] == 4 and result["hours"] == 32
    assert result["inclusive"] is True and result["calendar_name"] == "Prueba"
    assert result["excluded"] == [{"date": "2026-10-07", "reason": "Capacitación", "kind": "exception"}]
    assert conn.total_changes == before


def test_chilean_holiday_and_working_exception_override(conn):
    chile = repo.create_many(conn, "calendars", [dict(name="Chile", holidays_country="CL")])[0]
    result = tools.workdays(conn, chile.id, date(2026, 9, 17), 2)
    assert result["result"] == "2026-09-21"
    excluded = {d["date"]: d for d in result["excluded"]}
    assert excluded["2026-09-18"]["kind"] == "holiday"
    assert excluded["2026-09-18"]["reason"]
    override = repo.create_many(conn, "calendars", [dict(name="Excepción", holidays_country="CL",
        exceptions=[dict(date="2026-09-18", working=True, hours=4, reason="Jornada parcial")])])[0]
    result = tools.workdays_between(conn, override.id, date(2026, 9, 17), date(2026, 9, 18))
    assert result["workdays"] == 2 and result["hours"] == 12
    assert result["excluded"] == []


def test_seven_on_seven_off_in_both_directions(conn):
    shift = calendar(conn, shift_pattern="7x7", shift_anchor="2026-10-05", hours_per_day=12)
    assert tools.workdays(conn, shift.id, date(2026, 10, 5), 8)["result"] == "2026-10-19"
    assert tools.workdays(conn, shift.id, date(2026, 10, 19), -8)["result"] == "2026-10-05"
    interval = tools.workdays_between(conn, shift.id, date(2026, 10, 5), date(2026, 10, 18))
    assert interval["workdays"] == 7 and interval["hours"] == 84
    assert len(interval["excluded"]) == 7
    assert all(d["kind"] == "shift" and "7x7" in d["reason"] for d in interval["excluded"])


def test_invalid_calendar_queries(conn):
    cal = calendar(conn)
    client = client_for(conn)
    url = f"/api/calendars/{cal.id}/workdays"
    assert client.get(url, params={"start": "incorrecta", "days": 2}).status_code == 422
    assert client.get(url, params={"start": "2026-10-05", "days": "1.5"}).status_code == 422
    assert client.get(url + "/between", params={"start": "2026-10-06", "end": "2026-10-05"}).status_code == 422
    assert client.get("/api/calendars/999999/workdays", params={"start": "2026-10-05", "days": 1}).status_code == 404
