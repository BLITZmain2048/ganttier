"""Aceptación de búsqueda transversal, normalización y tiempo HTTP con 100 000 filas."""
import json
import platform
import sys
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from ganttier import db, repo, services
from ganttier.main import create_app
from ganttier.search import normalize, search


@pytest.fixture
def client(local_dir, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(local_dir / "search.db"))
    monkeypatch.setattr(services, "DATA_DIR", local_dir)
    with TestClient(create_app()) as client:
        yield client


def test_search_projects_master_occurrence_fields_and_order(conn, project):
    projects = [project] + [repo.create(conn, "projects", {
        "name": f"Proyecto {i}", "mode": "mantenimiento", "start_date": "2026-01-01",
        "end_date": "2026-12-31"}) for i in range(2)]
    task = repo.create(conn, "periodic_tasks", {"project_id": projects[1].id, "code": "MAESTRA",
        "name": "Inspección", "equipment": "52-J1", "required_skill": "Mecánica"})
    ids = []
    for i, p in enumerate(projects):
        a = repo.create(conn, "activities", {"project_id": p.id, "code": f"A{i}",
            "name": "Inspección", "equipment": "52-J1", "location": "Subestación Ñuble",
            "required_skill": "Eléctrica", "planned_start": "2026-10-05",
            "planned_finish": "2026-10-09", "percent_complete": 25,
            "periodic_task_id": task.id if i == 1 else None})
        ids.append(a.id)
    rows = search(conn, "52-j1")
    assert len(rows) == 4
    assert {r["project"]["id"] for r in rows} == {p.id for p in projects}
    acts = [r for r in rows if r["type"] == "actividad"]
    assert {r["id"] for r in acts} == set(ids)
    assert acts[1]["periodic_task_id"] == task.id
    assert all(r["planned_start"] == "2026-10-05" and r["percent_complete"] == 25 for r in acts)
    master = next(r for r in rows if r["type"] == "tarea periódica")
    assert master["id"] == task.id and master["project"]["mode"] == "mantenimiento"
    assert master["planned_start"] is None and master["percent_complete"] is None
    for term, count in (("INSPECCION", 4), ("subestacio\u0301n nuble", 3), ("electrica", 3), ("MECANICA", 1)):
        assert len(search(conn, term)) == count
    for code in ("XX-CÓD", "CÓDIGO", "CÓD"):
        repo.create(conn, "activities", {"project_id": project.id, "code": code, "name": "Orden"})
    assert [r["code"] for r in search(conn, "cod")] == ["CÓD", "CÓDIGO", "XX-CÓD"]
    assert search(conn, "cod", 1, 1)[0]["code"] == "CÓDIGO"


def test_literal_wildcards_and_no_bulk_entities(conn, project, monkeypatch):
    for code in ("A_%", "AB1", "O'Hara"):
        repo.create(conn, "activities", {"project_id": project.id, "code": code, "name": "Dato"})
    def forbidden(*args, **kwargs):
        pytest.fail("La búsqueda no debe cargar entidades masivas.")
    monkeypatch.setattr(repo, "list_entities", forbidden)
    assert [r["code"] for r in search(conn, "_%")] == ["A_%"]
    assert search(conn, "' OR 1=1 --") == []
    assert search(conn, "o'hara")[0]["code"] == "O'Hara"


def test_http_small_validation_and_limit(client):
    pid = client.post("/api/projects", json={"name": "Pequeño", "start_date": "2026-01-01"}).json()["id"]
    response = client.post(f"/api/projects/{pid}/activities", json=[
        {"code": f"AA-{i:03}", "name": "Revisión"} for i in range(205)])
    assert response.status_code == 201
    start = perf_counter()
    response = client.get("/api/search", params={"q": "revision"})
    assert perf_counter() - start < 1
    assert response.status_code == 200 and len(response.json()) == 50
    assert len(client.get("/api/search?q=revision&limit=200").json()) == 200
    assert len(client.get("/api/search?q=revision&offset=200").json()) == 5
    for params in ({"q": "a"}, {"q": "  "}, {"q": " a "}, {"q": "aa", "limit": 201},
                   {"q": "aa", "limit": 0}, {"q": "aa", "offset": -1}, {}):
        assert client.get("/api/search", params=params).status_code == 422


@pytest.mark.slow
def test_http_search_100000_twenty_queries(client, local_dir):
    response = client.post("/api/demo/mantenimiento?n=100000")
    assert response.status_code == 201, response.text[:1000]
    # Distribuir la demo en diez proyectos, conservando maestros, ocurrencias y recursos coherentes.
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 100000
        original = response.json()["id"]
        pids = [original] + [repo.create(conn, "projects", {"name": f"Cartera {i}",
            "mode": "mantenimiento", "start_date": "2026-01-01", "end_date": "2026-12-31",
            "calendar_id": response.json()["calendar_id"]}).id for i in range(1, 10)]
        with repo.transaction(conn):
            for remainder, pid in enumerate(pids):
                conn.execute("UPDATE periodic_tasks SET project_id=? WHERE id%10=?", (pid, remainder))
            conn.execute("UPDATE activities SET project_id=(SELECT project_id FROM periodic_tasks t "
                         "WHERE t.id=activities.periodic_task_id)")
        assert conn.execute("SELECT COUNT(DISTINCT project_id) FROM activities").fetchone()[0] == 10
        exact = conn.execute("SELECT code FROM activities ORDER BY id LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    queries = [exact, "MT-", "inspeccion", "S/E 0001", "linea", "electrica", "protecciones",
               "transformador", "interruptor", "ZZ-no-existe-991", "SE", "mecanica", "revision",
               "MT-00001", "termografia", "aislacion", "S/E", "Tramo", "control", "52-J1"]
    times = []
    for q in queries:
        normalize.cache_clear()  # Medir también normalización sin resultados anteriores.
        start = perf_counter()
        response = client.get("/api/search", params={"q": q})
        elapsed = perf_counter() - start
        assert response.status_code == 200, response.text[:1000]
        assert len(response.json()) <= 50
        times.append({"q": q, "seconds": elapsed, "results": len(response.json())})
    evidence = {"platform": platform.platform(), "processor": platform.processor(),
                "python": sys.version, "activities": 100000, "projects": 10, "queries": times}
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    # tmp/local_dir se redirige fuera de backend al ejecutar estas pruebas.
    (local_dir / "search-times.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    assert max(t["seconds"] for t in times) < 1, evidence
