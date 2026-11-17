"""API aislada, períodos civiles, archivos reales y ciclo de vida del scheduler."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import pytest
from ganttier import autoreports as ar, db, repo, services


@pytest.fixture
def setup(monkeypatch):
    # También al ejecutar pytest sin --basetemp, todo temporal queda en el workspace.
    with tempfile.TemporaryDirectory(prefix="autoreports-", ignore_cleanup_errors=True) as folder:
        root = Path(folder)
        monkeypatch.setenv("GANTTIER_DB", str(root / "test.db"))
        monkeypatch.setenv("GANTTIER_DATA_DIR", str(root))
        monkeypatch.setenv("MPLCONFIGDIR", str(root / "mpl"))
        monkeypatch.setattr(services, "DATA_DIR", root)
        conn = db.connect()
        db.initialize(conn)
        pid = repo.create(conn, "projects", {
            "name": "Demo automática", "start_date": date(2024, 1, 1),
            "data_date": date(2025, 1, 5),
            "calendar_id": repo.list_entities(conn, "calendars")[0].id,
        }).id
        repo.create(conn, "activities", {
            "project_id": pid, "code": "DEMO-01", "name": "Inspección",
            "planned_start": date(2024, 12, 30), "planned_finish": date(2025, 1, 3),
            "duration_days": 4, "work_hours": 32, "cost_budget": 320,
        })
        try:
            yield conn, pid, None, root
        finally:
            ar.stop_scheduler()
            conn.close()


@pytest.fixture
def client(setup):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ganttier.api.autoreports import router
    from ganttier.api.files import router as files_router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(files_router, prefix="/api")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def renderer(monkeypatch):
    calls = []

    def render(conn, pid, kind, start, formats):
        calls.append((pid, kind, start, list(formats)))
        directory = services.DATA_DIR / "informes"
        directory.mkdir(exist_ok=True)
        result = []
        for fmt in formats:
            filename = f"proyecto-{pid}-{kind}-{start}-{len(calls)}.{fmt}"
            (directory / filename).write_text("Informe de prueba", encoding="utf-8")
            result.append({"format": fmt, "filename": filename,
                           "url": f"/api/reports/files/{filename}"})
        return result

    monkeypatch.setattr(services, "render_reports", render)
    return calls


def enable(conn, pid, **kwargs):
    return ar.set_config(conn, pid, {"enabled": True, "formats": ["html"], **kwargs})


def test_config_defaults_validation_and_persistence_without_http(setup):
    conn, pid, _, _ = setup
    assert ar.get_config(conn, pid) == {
        "enabled": False, "weekly": True, "monthly": True, "annual": True,
        "formats": ["pdf", "xlsx"], "weekday": 0, "keep_last": 12,
    }
    config = enable(conn, pid, formats=["html", "html"], weekday=6, keep_last=2)
    assert config["formats"] == ["html"]
    other = db.connect()
    try:
        assert ar.get_config(other, pid) == config
    finally:
        other.close()
    for invalid in ({"weekday": -1}, {"weekday": 7}, {"weekday": True},
                    {"keep_last": 0}, {"keep_last": True}, {"formats": []},
                    {"formats": ["csv"]}, {"enabled": "true"}, {"unknown": 1}):
        with pytest.raises(ValueError):
            ar.set_config(conn, pid, invalid)
        assert ar.get_config(conn, pid) == config


def test_caller_transaction_is_preserved(setup, renderer):
    conn, pid, _, _ = setup
    enable(conn, pid)
    conn.execute("BEGIN")
    conn.execute("UPDATE projects SET name='Pendiente' WHERE id=?", (pid,))
    with pytest.raises(ValueError, match="transacción"):
        ar.run_due_reports(conn, datetime(2025, 1, 6))
    assert conn.in_transaction
    conn.rollback()
    assert repo.get(conn, "projects", pid).name == "Demo automática"
    assert not renderer


def test_config_roundtrip_and_project_scope(setup, client, renderer, monkeypatch):
    conn, pid, _, _ = setup
    url = f"/api/projects/{pid}/autoreports"
    defaults = client.get(url).json()
    assert defaults == {"enabled": False, "weekly": True, "monthly": True,
                        "annual": True, "formats": ["pdf", "xlsx"], "weekday": 0, "keep_last": 12}
    assert client.post(url + "/run").json() == []
    assert client.get(url + "/log").json() == []
    response = client.put(url, json={**defaults, "enabled": True, "formats": ["html", "html"]})
    assert response.status_code == 200
    assert response.json()["formats"] == ["html"]
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key=?",
                                 (f"autoreports:{pid}",)).fetchone()[0]) == response.json()
    assert client.get(url).json() == response.json()
    second = repo.create(conn, "projects", {"name": "Otro", "start_date": date(2024, 1, 1)}).id
    enable(conn, second)
    original = ar.run_project_reports
    monkeypatch.setattr(ar, "run_project_reports", lambda c, p: original(c, p, datetime(2025, 1, 6)))
    emitted = client.post(url + "/run")
    assert emitted.status_code == 200
    assert len(emitted.json()) == 3
    assert {call[0] for call in renderer} == {pid}
    assert client.post(url + "/run").json() == []
    assert len(client.get(url + "/log").json()) == 3
    for entry in emitted.json():
        for file in entry["files"]:
            assert file["url"] == f"/api/reports/files/{file['filename']}"
            assert client.get(file["url"]).status_code == 200


@pytest.mark.parametrize("body", [
    {"weekday": -1}, {"weekday": 7}, {"weekday": True}, {"weekday": 1.5},
    {"keep_last": 0}, {"keep_last": -1}, {"keep_last": True},
    {"formats": []}, {"formats": ["csv"]}, {"enabled": "true"}, {"unknown": 1},
])
def test_invalid_config(setup, client, body):
    _, pid, _, _ = setup
    assert client.put(f"/api/projects/{pid}/autoreports", json=body).status_code == 422


@pytest.mark.parametrize("method,suffix,body", [
    ("get", "", None), ("put", "", {}), ("post", "/run", None), ("get", "/log", None),
])
def test_missing_project(setup, client, method, suffix, body):
    assert client.request(method, f"/api/projects/99999/autoreports{suffix}", json=body).status_code == 404


@pytest.mark.parametrize("now,expected", [
    (datetime(2025, 1, 6), [("2024-12-30", "2025-01-05"), ("2024-12-01", "2024-12-31"), ("2024-01-01", "2024-12-31")]),
    (datetime(2024, 3, 1), [("2024-02-19", "2024-02-25"), ("2024-02-01", "2024-02-29"), ("2023-01-01", "2023-12-31")]),
    (datetime(2025, 1, 1), [("2024-12-23", "2024-12-29"), ("2024-12-01", "2024-12-31"), ("2024-01-01", "2024-12-31")]),
    (datetime(2025, 1, 5, 23, 59), [("2024-12-23", "2024-12-29"), ("2024-12-01", "2024-12-31"), ("2024-01-01", "2024-12-31")]),
])
def test_closed_periods_and_persistent_idempotency(setup, renderer, now, expected):
    conn, pid, _, _ = setup
    enable(conn, pid)
    results = ar.run_due_reports(conn, now)
    assert [r["period_type"] for r in results] == ["weekly", "monthly", "annual"]
    assert [(r["period_start"], r["period_end"]) for r in results] == expected
    assert all(date.fromisoformat(r["period_end"]) < now.date() for r in results)
    other = db.connect()
    try:
        assert ar.run_due_reports(other, now) == []
    finally:
        other.close()
    assert len(renderer) == 3


def test_weekday_catchup_and_disabled_frequencies(setup, renderer):
    conn, pid, _, _ = setup
    enable(conn, pid, weekday=2, monthly=False, annual=False)
    assert ar.run_due_reports(conn, datetime(2025, 1, 6)) == []
    assert ar.run_due_reports(conn, datetime(2025, 1, 7)) == []
    assert len(ar.run_due_reports(conn, datetime(2025, 1, 9))) == 1
    assert ar.run_due_reports(conn, datetime(2025, 1, 12)) == []
    assert ar.run_due_reports(conn, datetime(2025, 1, 13)) == []
    assert len(ar.run_due_reports(conn, datetime(2025, 1, 15))) == 1
    enable(conn, pid, enabled=False)
    assert ar.run_due_reports(conn, datetime(2025, 2, 3)) == []


def test_partial_failure_retries_only_missing_format_and_continues(setup, renderer, monkeypatch):
    conn, pid, _, _ = setup
    second = repo.create(conn, "projects", {"name": "Segundo", "start_date": date(2024, 1, 1)}).id
    for project in (pid, second):
        enable(conn, project, formats=["pdf", "xlsx"], monthly=False, annual=False)
    render = services.render_reports

    def failing(c, p, kind, start, formats):
        if p == pid and formats == ["xlsx"]:
            raise RuntimeError("Fallo de Excel")
        return render(c, p, kind, start, formats)

    monkeypatch.setattr(services, "render_reports", failing)
    results = ar.run_due_reports(conn, datetime(2025, 1, 6))
    assert {r["project_id"] for r in results} == {pid, second}
    assert ar.get_log(conn, pid)[0]["status"] == "error"
    assert "Fallo de Excel" in ar.get_log(conn, pid)[0]["error"]
    assert ar.run_due_reports(conn, datetime(2025, 1, 6)) == []
    monkeypatch.setattr(services, "render_reports", render)
    retried = ar.run_due_reports(conn, datetime(2025, 1, 6))
    assert len(retried) == 1
    assert [f["format"] for f in retried[0]["files"]] == ["xlsx"]
    assert ar.get_log(conn, pid)[0]["status"] == "ok"
    assert len(ar.get_log(conn, pid)[0]["files"]) == 2
    assert ar.run_due_reports(conn, datetime(2025, 1, 6)) == []


def test_corrupt_config_does_not_block_next_project(setup, renderer):
    conn, pid, _, _ = setup
    second = repo.create(conn, "projects", {"name": "Segundo", "start_date": date(2024, 1, 1)}).id
    enable(conn, pid)
    enable(conn, second)
    conn.execute("UPDATE meta SET value=? WHERE key=?", ("{broken", f"autoreports:{pid}"))
    conn.commit()
    results = ar.run_due_reports(conn, datetime(2025, 1, 6))
    assert {r["project_id"] for r in results} == {second}
    assert ar.get_log(conn, pid)[0]["status"] == "error"


def test_retention_preserves_deduplication_and_manual_files(setup, renderer):
    conn, pid, _, root = setup
    enable(conn, pid, keep_last=1)
    first = ar.run_due_reports(conn, datetime(2025, 1, 6))
    manual = root / "informes" / "manual.html"
    manual.write_text("Conservar", encoding="utf-8")
    ar.run_due_reports(conn, datetime(2025, 2, 3))
    history = ar.get_log(conn, pid)
    assert len(history) == 3
    assert {entry["period_type"] for entry in history} == {"weekly", "monthly", "annual"}
    for entry in first:
        path = root / "informes" / entry["files"][0]["filename"]
        assert path.exists() == (entry["period_type"] == "annual")
    assert ar.run_due_reports(conn, datetime(2025, 1, 6)) == []
    assert manual.read_text(encoding="utf-8") == "Conservar"


def test_two_connections_emit_only_once(setup, renderer):
    conn, pid, _, _ = setup
    enable(conn, pid)
    barrier = threading.Barrier(2)

    def run():
        connection = db.connect()
        try:
            barrier.wait(timeout=5)
            return ar.run_due_reports(connection, datetime(2025, 1, 6))
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run) for _ in range(2)]
        assert sorted(len(job.result(timeout=15)) for job in jobs) == [0, 3]
    assert len(renderer) == 3


@pytest.mark.parametrize("fmt,module", [("html", None), ("xlsx", "openpyxl"),
                                       ("pdf", "reportlab"), ("docx", "docx")])
def test_real_rendering(setup, fmt, module):
    if module:
        pytest.importorskip(module)
    conn, pid, _, root = setup
    enable(conn, pid, formats=[fmt])
    results = ar.run_due_reports(conn, datetime(2025, 1, 6))
    assert len(results) == 3
    for entry in results:
        assert entry["status"] == "ok"
        for file in entry["files"]:
            content = (root / "informes" / file["filename"]).read_bytes()
            assert len(content) > 100
            if file["format"] == "pdf":
                assert content.startswith(b"%PDF")
            elif file["format"] in {"xlsx", "docx"}:
                assert content.startswith(b"PK")


def test_real_download(setup, client):
    conn, pid, _, root = setup
    enable(conn, pid, formats=["html"])
    for entry in ar.run_due_reports(conn, datetime(2025, 1, 6)):
        for file in entry["files"]:
            response = client.get(file["url"])
            assert response.status_code == 200
            assert response.content == (root / "informes" / file["filename"]).read_bytes()


def test_scheduler_retries_owns_connections_and_stops(setup, monkeypatch):
    _, _, _, root = setup
    attempts, connections, worker_ids, closed = [], [], [], []
    ran = threading.Event()

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            super().close()
            # Comprobar el cierre desde el hilo propietario, sin violar afinidad.
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                self.execute("SELECT 1")
            closed.append(self)

    def connect():
        worker_ids.append(threading.get_ident())
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("Fallo transitorio")
        conn = sqlite3.connect(root / "scheduler.db", factory=TrackedConnection)
        connections.append(conn)
        return conn

    def run(conn):
        conn.execute("SELECT 1")
        ran.set()
        return []

    monkeypatch.setattr(ar, "run_due_reports", run)
    thread = ar.start_scheduler(connect, interval_s=1)
    assert thread.daemon
    assert ar.start_scheduler(connect, interval_s=1) is thread
    assert ran.wait(5)
    ar.stop_scheduler()
    assert not thread.is_alive()
    assert len(attempts) >= 2
    assert all(identity != threading.get_ident() for identity in worker_ids)
    assert closed == connections
    ran.clear()
    with pytest.raises(RuntimeError, match="lifespan"):
        with ar.scheduler(connect, interval_s=3600) as restarted:
            assert restarted is not thread
            assert ran.wait(5)
            raise RuntimeError("lifespan")
    assert not restarted.is_alive()
    assert closed == connections


@pytest.mark.parametrize("interval", [0, -1, True, 0.5])
def test_scheduler_invalid_interval(interval):
    with pytest.raises(ValueError):
        ar.start_scheduler(db.connect, interval)
