"""Regresiones HTTP de aceptación; toda persistencia queda en el tmp de pytest."""
from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("python_multipart")
from fastapi.testclient import TestClient

from ganttier import db, repo, services
from ganttier.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "aceptacion.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setattr(services, "DATA_DIR", tmp_path)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def project(client, mode="proyecto", **changes):
    return ok(client.post("/api/projects", json=dict(
        name="Aceptación", mode=mode, start_date="2026-01-05", end_date="2026-03-31",
        data_date="2026-01-06", calendar_id=5, **changes)), 201)["id"]


def activity(client, pid, code="A", **changes):
    values = dict(code=code, name=code, duration_days=2, work_hours=16,
                  planned_start="2026-01-05", planned_finish="2026-01-06")
    values.update(changes)
    return ok(client.post(f"/api/projects/{pid}/activities", json=values), 201)


def simulate(client, pid):
    return ok(client.post(f"/api/projects/{pid}/schedule/optimize", json={
        "options": {"method": "heuristic", "time_limit_s": 0.1}}))


def maintenance_book():
    from ganttier.domain import ImportBundle
    return ImportBundle(mode="mantenimiento", project=dict(name="Importado",
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), data_date=date(2026, 1, 20)),
        periodic_tasks=[dict(code="T", name="Tarea", frequency="M", first_due=date(2026, 1, 5))],
        progress=[dict(activity_code="T-0001", date=date(2026, 1, 10), percent_complete=10,
                       actual_start=date(2026, 1, 5)),
                  dict(activity_code="T-9999", date=date(2026, 1, 10), percent_complete=10,
                       actual_start=date(2026, 1, 5))])


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("mode", ["merge", "replace"])
def test_import_missing_occurrence_reports_actual_row_and_rolls_back(client, tmp_path, dry_run, mode):
    from ganttier.excel_io import export_project
    from openpyxl import load_workbook
    path = tmp_path / "import.xlsx"
    export_project(maintenance_book(), path)
    book = load_workbook(path)
    book["Avance"].insert_rows(3, 2)  # La fila física no es el índice de la lista.
    book.save(path)
    book.close()
    pid = project(client, "mantenimiento")
    original = activity(client, pid, "EXISTENTE")
    response = client.post(f"/api/projects/{pid}/import",
        params={"dry_run": dry_run, "mode": mode}, files={"file": ("import.xlsx", path.read_bytes())})
    result = ok(response, 200 if dry_run else 422)
    errors = [e for e in result["errors"] if "T-9999" in e["message"]]
    assert len(errors) == 1
    assert errors[0]["sheet"] == "Avance" and errors[0]["row"] == 5
    assert errors[0]["severity"] == "error"
    assert errors[0]["message"] == "La ocurrencia T-9999 no existe en el horizonte del proyecto"
    assert set(result["summary"]) == {"calendarios", "recursos", "wbs", "actividades",
        "relaciones", "asignaciones", "tareas_periodicas", "avance"}
    assert ok(client.get(f"/api/projects/{pid}/progress")) == []
    assert [a["id"] for a in ok(client.get(f"/api/projects/{pid}/activities"))] == [original["id"]]


def test_new_project_import_rejects_unknown_occurrence(client, tmp_path):
    from ganttier.excel_io import export_project
    path = tmp_path / "new.xlsx"
    export_project(maintenance_book(), path)
    result = ok(client.post("/api/import/new-project", files={"file": ("new.xlsx", path.read_bytes())}), 422)
    assert any("T-9999" in e["message"] for e in result["errors"])
    assert ok(client.get("/api/projects")) == []


def test_valid_maintenance_import_keeps_progress(client, tmp_path):
    from ganttier.excel_io import export_project
    bundle = maintenance_book()
    bundle.progress = bundle.progress[:1]
    path = tmp_path / "valid.xlsx"
    export_project(bundle, path)
    pid = project(client, "mantenimiento")
    result = ok(client.post(f"/api/projects/{pid}/import", files={"file": ("valid.xlsx", path.read_bytes())}))
    assert result["errors"] == [] and result["summary"]["avance"] == 1
    assert ok(client.get(f"/api/projects/{pid}/progress"))[0]["percent_complete"] == 10


@pytest.mark.parametrize("change", ["duration", "relation", "assignment", "resource", "calendar",
    "exception", "constraint", "window", "cutoff", "progress"])
def test_changed_scenario_cannot_apply_optimization(client, change):
    pid = project(client)
    a, b = activity(client, pid), activity(client, pid, "B")
    resource = ok(client.post("/api/resources", json={"project_id": pid, "name": "Ana", "calendar_id": 5}), 201)
    run = simulate(client, pid)
    if change == "duration":
        ok(client.put(f'/api/activities/{a["id"]}', json={"duration_days": 4}))
    elif change == "relation":
        ok(client.post(f"/api/projects/{pid}/relations", json={"pred_id": a["id"], "succ_id": b["id"]}), 201)
    elif change == "assignment":
        ok(client.post(f"/api/projects/{pid}/assignments", json={"activity_id": a["id"], "resource_id": resource["id"]}), 201)
    elif change == "resource":
        ok(client.put(f'/api/resources/{resource["id"]}', json={"capacity_hours_day": 4}))
    elif change in {"calendar", "exception"}:
        values = {"workweek": "1111100"} if change == "calendar" else {
            "exceptions": [{"date": "2026-01-07", "working": False}]}
        ok(client.put("/api/calendars/5", json=values))
    elif change == "constraint":
        ok(client.put(f'/api/activities/{a["id"]}', json={"constraint_type": "SNET", "constraint_date": "2026-01-10"}))
    elif change == "window":
        ok(client.put(f'/api/activities/{a["id"]}', json={"window_start": "2026-01-10", "window_hard": True}))
    elif change == "cutoff":
        ok(client.put(f"/api/projects/{pid}/data-date", json={"data_date": "2026-01-07"}))
    else:
        ok(client.post(f"/api/projects/{pid}/progress", json={"activity_id": a["id"], "date": "2026-01-06",
            "percent_complete": 20, "actual_start": "2026-01-05"}), 201)
    before = ok(client.get(f'/api/activities/{a["id"]}'))
    result = ok(client.post(f'/api/projects/{pid}/schedule/runs/{run["run_id"]}/apply'), 422)
    assert result["detail"] == "La programación cambió desde que se simuló esta corrida; vuelve a simular."
    assert ok(client.get(f'/api/activities/{a["id"]}')) == before


def test_unchanged_run_can_apply_and_cpm_also_detects_changes(client):
    pid = project(client)
    a = activity(client, pid)
    run = simulate(client, pid)
    url = f'/api/projects/{pid}/schedule/runs/{run["run_id"]}/apply'
    ok(client.post(url))
    ok(client.post(url))  # Reaplicar la misma corrida es idempotente.
    cpm = ok(client.post(f"/api/projects/{pid}/schedule/cpm"))
    ok(client.put(f'/api/activities/{a["id"]}', json={"duration_days": 5}))
    result = ok(client.post(f'/api/projects/{pid}/schedule/runs/{cpm["run_id"]}/apply'), 422)
    assert "vuelve a simular" in result["detail"]


def test_maintenance_levels_only_pending_and_preserves_fixed(client):
    pid = project(client, "mantenimiento")
    resource = ok(client.post("/api/resources", json={"project_id": pid, "name": "Ana", "calendar_id": 5}), 201)
    task = ok(client.post(f"/api/projects/{pid}/periodic-tasks", json={"code": "T", "name": "T", "resource_id": resource["id"]}), 201)
    fixed = [activity(client, pid, "LOCK", periodic_task_id=task["id"], locked=True),
             activity(client, pid, "START", periodic_task_id=task["id"], actual_start="2026-01-05"),
             activity(client, pid, "ADV", periodic_task_id=task["id"], percent_complete=25),
             activity(client, pid, "MANUAL")]
    pending = activity(client, pid, "PEND", periodic_task_id=task["id"])
    run = simulate(client, pid)
    assert set(run["starts"]) == {str(pending["id"])}
    assert "4 actividades fijas por avance/bloqueo o no periódicas no se movieron" in run["warnings"]
    ok(client.post(f'/api/projects/{pid}/schedule/runs/{run["run_id"]}/apply'))
    for original in fixed:
        assert ok(client.get(f'/api/activities/{original["id"]}')) == original
    ok(client.put(f'/api/activities/{pending["id"]}', json={"locked": True}))
    response = client.post(f"/api/projects/{pid}/schedule/optimize", json={})
    assert "No hay ocurrencias pendientes" in ok(response, 422)["detail"]


@pytest.mark.parametrize("endpoint", ["scurve", "evm"])
def test_control_accepts_distant_forecast(client, endpoint):
    pid = project(client)
    activity(client, pid, duration_days=2200)
    result = ok(client.get(f"/api/projects/{pid}/control/{endpoint}"))
    assert result["planned_finish"] == "2026-01-06"
    if endpoint == "scurve":
        assert result["forecast_finish"] > "2031-01-01"


@pytest.mark.parametrize("percent,start,expected", [(30, "2026-01-05", "2026-01-05"),
    (30, "2026-02-01", "2026-01-06"), (100, "2026-01-05", "2026-01-05")])
def test_progress_infers_actual_dates_and_cpm_works(client, percent, start, expected):
    pid = project(client)
    a = activity(client, pid, planned_start=start, planned_finish=max(start, "2026-01-06"))
    entry = ok(client.post(f"/api/projects/{pid}/progress", json={"activity_id": a["id"],
        "date": "2026-01-06", "percent_complete": percent}), 201)
    assert entry["actual_start"] == expected
    assert entry["actual_finish"] == ("2026-01-06" if percent == 100 else None)
    ok(client.post(f"/api/projects/{pid}/schedule/cpm"))


def test_progress_keeps_previous_start_in_unsorted_batch(client):
    pid = project(client)
    a = activity(client, pid)
    rows = ok(client.post(f"/api/projects/{pid}/progress", json=[
        {"activity_id": a["id"], "date": "2026-01-06", "percent_complete": 100},
        {"activity_id": a["id"], "date": "2026-01-05", "percent_complete": 20, "actual_start": "2026-01-04"}]), 201)
    assert rows[0]["actual_start"] == "2026-01-04"
    assert rows[0]["actual_finish"] == "2026-01-06"


def test_columnar_window_hards_are_booleans(client):
    pid = project(client)
    activity(client, pid, window_hard=True)
    activity(client, pid, "B")
    result = ok(client.get(f"/api/projects/{pid}/activities/columnar"))
    assert result["window_hards"] == [True, False]
    assert all(type(v) is bool for v in result["window_hards"])
    assert len({len(v) for v in result.values()}) == 1


@pytest.mark.parametrize("method", ["put", "patch"])
def test_maintenance_duration_recomputes_finish_with_activity_calendar(client, method):
    pid = project(client, "mantenimiento")
    cal = ok(client.post("/api/calendars", json={"name": "L-V", "workweek": "1111100",
        "holidays_country": None, "exceptions": [{"date": "2026-01-12", "working": False}]}), 201)
    a = activity(client, pid, duration_days=1, calendar_id=cal["id"],
        planned_start="2026-01-09", planned_finish="2026-01-09")
    if method == "put":
        result = ok(client.put(f'/api/activities/{a["id"]}', json={"duration_days": 3}))
    else:
        result = ok(client.patch(f"/api/projects/{pid}/activities", json=[{"id": a["id"], "duration_days": 3}]))[0]
    assert result["planned_finish"] == "2026-01-14"


def test_assignments_filter_pagination_and_legacy_list(client):
    pid = project(client)
    a, b = activity(client, pid), activity(client, pid, "B")
    for aid in (a["id"], b["id"], a["id"]):
        ok(client.post(f"/api/projects/{pid}/assignments", json={"activity_id": aid, "skill": "E"}), 201)
    url = f"/api/projects/{pid}/assignments"
    assert len(ok(client.get(url))) == 3
    assert len(ok(client.get(url, params={"activity_id": a["id"]}))) == 2
    result = ok(client.get(url, params={"activity_id": a["id"], "offset": 1, "limit": 1}))
    assert result["total"] == 2 and result["offset"] == 1 and result["limit"] == 1
    assert len(result["items"]) == 1 and result["items"][0]["activity_id"] == a["id"]
    assert ok(client.get(url, params={"offset": 10, "limit": 1}))["items"] == []


def test_assignments_without_pagination_do_not_truncate(client):
    pid = project(client)
    a = activity(client, pid)
    conn = db.connect()
    try:
        with conn:
            conn.executemany("INSERT INTO assignments(activity_id,skill) VALUES (?,?)", [(a["id"], "E")] * 100001)
    finally:
        conn.close()
    assert len(ok(client.get(f"/api/projects/{pid}/assignments"))) == 100001


@pytest.mark.parametrize("failure,status", [("import", 503), ("wrapped", 503), ("save", 500)])
def test_report_failures_have_spanish_detail(client, monkeypatch, failure, status):
    import ganttier.reports
    pid = project(client)
    monkeypatch.setattr(services, "build_report_data", lambda *a: SimpleNamespace(period_start=date(2026, 1, 5)))
    def fail(*args):
        if failure == "save":
            raise OSError("disco lleno")
        try:
            raise ImportError("dependencia ausente", name="reportlab")
        except ImportError as exc:
            if failure == "wrapped":
                raise RuntimeError("No se pudo cargar PDF") from exc
            raise
    monkeypatch.setattr(ganttier.reports, "render", fail)
    result = ok(client.post(f"/api/projects/{pid}/reports", json={"period_type": "weekly",
        "period_start": "2026-01-05", "formats": ["pdf"]}), status)
    assert ("dependencia" if status == 503 else "guardar") in result["detail"]


def test_launcher_tries_next_port_and_prints_real_url(monkeypatch, capsys):
    import ganttier.main as main
    import uvicorn
    attempted, closed, served, opened = [], [], [], []
    class Socket:
        def setsockopt(self, *args): pass
        def bind(self, address):
            attempted.append(address[1])
            if address[1] < 8767:
                raise OSError("ocupado")
        def close(self): closed.append(self)
        def listen(self, *args): pass
        def setblocking(self, *args): pass
    class Thread:
        def __init__(self, target, args=(), **kwargs): self.target, self.args = target, args
        def start(self): self.target(*self.args)
    monkeypatch.setattr(main.socket, "socket", lambda *a: Socket())
    monkeypatch.setattr(main, "threading", SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(main, "_open_when_ready", lambda url: opened.append(url))
    monkeypatch.setattr(uvicorn, "Server", lambda config: SimpleNamespace(run=lambda **kw: served.append(kw)))
    main.main()
    assert attempted == [8765, 8766, 8767] and len(closed) == 3
    assert opened == ["http://127.0.0.1:8767"] and served[0]["sockets"]
    assert "http://127.0.0.1:8767" in capsys.readouterr().out


def test_launcher_exhausts_ports_without_opening_browser(monkeypatch):
    import ganttier.main as main
    attempted, closed, opened = [], [], []
    class Socket:
        def setsockopt(self, *args): pass
        def bind(self, address):
            attempted.append(address[1])
            raise OSError("ocupado")
        def close(self): closed.append(self)
    monkeypatch.setattr(main.socket, "socket", lambda *a: Socket())
    monkeypatch.setattr(main.webbrowser, "open", lambda url: opened.append(url))
    with pytest.raises(RuntimeError, match="No hay puertos disponibles"):
        main.main()
    assert attempted == list(range(8765, 8776)) and len(closed) == 11
    assert opened == []


@pytest.mark.parametrize("healthy", [True, False])
def test_browser_waits_for_health_and_times_out(monkeypatch, healthy):
    import ganttier.main as main
    elapsed, attempts, opened = [0.0], [], []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"status":"ok","app":"Ganttier"}'
    def get(url, **kwargs):
        attempts.append(url)
        if not healthy or len(attempts) < 3:
            raise OSError("todavía no")
        return Response()
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: elapsed[0],
        sleep=lambda n: elapsed.__setitem__(0, elapsed[0] + n)))
    monkeypatch.setattr(main, "urlopen", get)
    monkeypatch.setattr(main.webbrowser, "open", lambda url: opened.append(url))
    main._open_when_ready("http://127.0.0.1:8767")
    assert all(url.endswith("/api/health") for url in attempts)
    assert opened == (["http://127.0.0.1:8767"] if healthy else [])
    assert elapsed[0] <= 20
