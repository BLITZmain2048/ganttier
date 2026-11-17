"""Contratos HTTP nuevos y regresión del OpenAPI guardado antes de integrar."""
from datetime import date
import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from ganttier import db, repo, services
from ganttier.api import optimizacion as api
from ganttier.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "api.sqlite"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(services, "DATA_DIR", tmp_path)
    with TestClient(create_app(token="api-test"), headers={"X-Ganttier-Token": "api-test"}) as client:
        yield client
    registry = api._registry()
    for event in registry.events.values():
        event.set()
    for thread in registry.threads.values():
        thread.join(timeout=10)


def project(client):
    response = client.post("/api/projects", json=dict(name="API comparación", start_date="2026-01-01", end_date="2026-01-10", calendar_id=5))
    assert response.status_code == 201, response.text
    pid = response.json()["id"]
    assert client.post("/api/resources", json=dict(name="Pool", project_id=pid, skills=["E"], calendar_id=5)).status_code in (200, 201)
    assert client.post(f"/api/projects/{pid}/activities", json=[dict(code="A", name="A", duration_days=1, work_hours=8, required_skill="E")]).status_code in (200, 201)
    return pid


def plan():
    return dict(variants=[dict(id=f"v{i}", options=dict(seed=i, method="heuristic", time_limit_s=0.1)) for i in range(2)], parallel=1)


def result(variant, i):
    return dict(variant=variant.to_dict(), status="ok", feasible=True,
                metrics=dict(makespan_days=i+1, activities_out_of_window=0, overtime_hours=0, load_std=0),
                run=dict(id=i, kind="optimize", payload={"secret_payload": "never-return"}),
                elapsed_s=.01)


def inject(monkeypatch, execute):
    from ganttier.optimizacion.variantes import expandir_plan
    from ganttier.optimizacion.ranking import clasificar
    monkeypatch.setattr(api, "_components", lambda: (execute, expandir_plan, clasificar))


def test_capabilities_security_openapi_unchanged(client):
    assert client.get("/api/capabilities", headers={"X-Ganttier-Token": "wrong"}).status_code == 401
    cap = client.get("/api/capabilities").json()
    assert cap["limits"]["max_variants"] == 5000 and "plazo" in cap["presets"]
    schema = client.get("/openapi.json").json()
    evidence = Path(__file__).resolve().parents[3] / "trabajos/cli_codex_20261019/agentes/API"
    if not (evidence / "openapi_previo.json").is_file() or not (evidence / "openapi_previo.json").read_text(encoding="utf-8").strip():
        pytest.skip("Falta la evidencia openapi_previo.json del encargo original.")
    previous = json.loads((evidence / "openapi_previo.json").read_text(encoding="utf-8"))
    routes = json.loads((evidence / "rutas_previas.json").read_text(encoding="utf-8"))
    for path, methods in routes.items():
        assert sorted(schema["paths"][path]) == methods
        assert schema["paths"][path] == previous["paths"][path]
    for name, definition in previous["components"]["schemas"].items():
        assert schema["components"]["schemas"][name] == definition
    assert len(schema["paths"]) == len(routes) + 6  # GET y POST comparten /jobs.
    for path in set(schema["paths"]) - set(routes):
        for operation in schema["paths"][path].values():
            assert operation["tags"] == ["Optimización y trabajos"] and operation["description"]


def test_diagnostic_route(client):
    pid = project(client)
    response = client.get(f"/api/projects/{pid}/schedule/diagnostics", params=dict(hasta="2026-01-10"))
    assert response.status_code == 200, response.text
    assert response.json()["capacity_by_skill"]
    assert client.get(f"/api/projects/{pid}/schedule/diagnostics", params=dict(periodo="bad")).status_code == 422
    assert client.get("/api/projects/99999/schedule/diagnostics").status_code == 404


def test_compare_two_variants_without_payload_or_mutation(client, monkeypatch):
    pid = project(client)
    seen = {}
    def execute(path, data_dir, project_id, variants, **kwargs):
        assert Path(path).is_relative_to(services.DATA_DIR / "jobs")
        assert project_id == pid
        seen["source"] = Path(path)
        rows = [result(v, i) for i, v in enumerate(variants)]
        for i, row in enumerate(rows, 1):
            kwargs["callback"](row, i, len(rows))
        return rows
    inject(monkeypatch, execute)
    before = client.get(f"/api/projects/{pid}/activities").json()
    response = client.post(f"/api/projects/{pid}/schedule/compare", json=dict(plan=plan()))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completado" and data["progress"] == dict(done=2, total=2)
    assert data["top"][0]["variant"]["id"] == "v0" and not data["applied"]
    assert "secret_payload" not in response.text and "payload" not in json.dumps(data["results"])
    assert before == client.get(f"/api/projects/{pid}/activities").json()
    assert not seen["source"].exists()


def test_missing_executor_503_and_strict_bounds(client, monkeypatch):
    pid = project(client)
    original = api.importlib.import_module
    def unavailable(name, *args, **kwargs):
        if name == "ganttier.optimizacion.ejecutor":
            raise ImportError("ejecutor")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(api.importlib, "import_module", unavailable)
    endpoint = f"/api/projects/{pid}/schedule/compare"
    response = client.post(endpoint, json=dict(plan=plan()))
    assert response.status_code == 503 and "EJE" in response.text
    bad = [dict(plan=plan(), output_path="C:/escape"), dict(variants=[dict(options={"unknown": 1})]),
           dict(plan={"grid": {"options.seed": list(range(100)), "weights.level": list(range(100))}}),
           dict(plan={"random": {"n": 5001, "space": {}}}), dict(plan={"parallel": 100}),
           dict(plan={"reanudar_desde": "../../file"}), dict(plan={}, variants=[])]
    for body in bad:
        assert client.post(endpoint, json=body).status_code == 422, body
    assert client.get("/api/jobs/not-a-path").status_code == 404


def test_async_progress_cancel_persistence_and_cleanup(client, monkeypatch):
    pid = project(client)
    started = threading.Event()
    def execute(path, data_dir, project_id, variants, **kwargs):
        row = result(variants[0], 0)
        kwargs["callback"](row, 1, len(variants))
        started.set()
        assert kwargs["cancelar"].wait(10)
        return [row]
    inject(monkeypatch, execute)
    response = client.post("/api/jobs", json=dict(kind="batch", project_id=pid, plan=plan()))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert started.wait(5)
    info = client.get(f"/api/jobs/{job_id}").json()
    assert info["progress"] == dict(done=1, total=2)
    assert "secret_payload" not in json.dumps(client.get("/api/jobs").json())
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    api._registry().threads[job_id].join(10)
    info = client.get(f"/api/jobs/{job_id}").json()
    assert info["status"] == "cancelado"
    root = services.DATA_DIR / "jobs"
    assert not (root / job_id / "source.sqlite").exists()
    journal = (root / f"{job_id}.jsonl").read_text(encoding="utf-8")
    assert "secret_payload" not in journal
    recovered = api.JobRegistry(root)
    assert recovered.view(job_id)["status"] == "cancelado"
    assert recovered.view(job_id)["total_results"] == 1
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelado"


def test_recovery_marks_running_interrupted(client):
    registry = api._registry()
    job_id = "a" * 32
    directory = registry.root / job_id
    directory.mkdir()
    (directory / "source.sqlite").touch()
    registry._append(job_id, "state", dict(job_id=job_id, status="ejecutando", progress=dict(done=0, total=1), rank_by="lexicographic"))
    recovered = api.JobRegistry(registry.root)
    assert recovered.view(job_id)["status"] == "interrumpido"
    assert not (directory / "source.sqlite").exists()


def test_real_executor_two_variants(client):
    pytest.importorskip("ganttier.optimizacion.ejecutor")
    pid = project(client)
    response = client.post(f"/api/projects/{pid}/schedule/compare", json=dict(plan=plan()))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completado", data
    assert data["counts"] == {"ok": 2}, data
    assert not data["applied"] and data["total_results"] == 2
    assert all(r["feasible"] for r in data["results"])
    assert all(r["cpu_count"] > 0 and r["parallel"] == 1 for r in data["results"])
    assert not client.get(f"/api/projects/{pid}/activities").json()[0]["planned_start"]
    root = services.DATA_DIR / "jobs" / data["job_id"]
    assert all(Path(r["run_payload_ref"]).is_relative_to(root) for r in data["results"])
    assert not list((root / "temporales").iterdir())


def test_real_apply_best_and_verified_backup(client):
    pytest.importorskip("ganttier.optimizacion.ejecutor")
    pytest.importorskip("ganttier.optimizacion.aplicar")
    pid = project(client)
    response = client.post(f"/api/projects/{pid}/schedule/compare", json=dict(plan=plan(), apply_best=True))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["applied"], result
    assert Path(result["application"]["respaldo"]).is_file()
    assert client.get(f"/api/projects/{pid}/activities").json()[0]["planned_start"]


def test_async_completion_survives_client_closing(client, monkeypatch):
    pid = project(client)
    release = threading.Event()
    def execute(path, data_dir, project_id, variants, **kwargs):
        assert release.wait(5)
        return [result(v, i) for i, v in enumerate(variants)]
    inject(monkeypatch, execute)
    # La aceptación no depende del contexto de este segundo cliente.
    with TestClient(create_app(token="api-test"), headers={"X-Ganttier-Token": "api-test"}) as temporary:
        response = temporary.post("/api/jobs", json=dict(kind="compare", project_id=pid, plan=plan()))
        assert response.status_code == 202
        job_id = response.json()["job_id"]
    release.set()
    api._registry().threads[job_id].join(10)
    info = client.get(f"/api/jobs/{job_id}").json()
    assert info["status"] == "completado" and info["progress"] == dict(done=2, total=2)
    assert len(info["top"]) == 2 and "payload" not in json.dumps(info)


def test_apply_conflict_returns_409(client, monkeypatch):
    from ganttier.optimizacion.aplicar import EscenarioCambio
    pid = project(client)
    inject(monkeypatch, lambda p, d, pid, variants, **kwargs: [result(v, i) for i, v in enumerate(variants)])
    def apply(*args, **kwargs):
        raise EscenarioCambio("El escenario cambió; vuelva a resolver.")
    monkeypatch.setattr(api, "_application", lambda: apply)
    response = client.post(f"/api/projects/{pid}/schedule/compare", json=dict(plan=plan(), apply_best=True))
    assert response.status_code == 409 and "escenario" in response.text


def test_real_async_cancel_cleans_children(client):
    pytest.importorskip("ganttier.optimizacion.ejecutor")
    pid = project(client)
    response = client.post("/api/jobs", json=dict(kind="batch", project_id=pid,
        plan=dict(grid={"options.seed": list(range(20))}, parallel=1)))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    api._registry().threads[job_id].join(30)
    info = client.get(f"/api/jobs/{job_id}").json()
    assert info["status"] == "cancelado", info
    directory = services.DATA_DIR / "jobs" / job_id
    assert not (directory / "source.sqlite").exists()
    temporary = directory / "temporales"
    assert not temporary.exists() or not list(temporary.iterdir())


def test_executor_exception_becomes_durable_error(client, monkeypatch):
    pid = project(client)
    def execute(*args, **kwargs):
        raise RuntimeError("Fallo de prueba acotado")
    inject(monkeypatch, execute)
    response = client.post("/api/jobs", json=dict(kind="compare", project_id=pid, plan=plan()))
    job_id = response.json()["job_id"]
    api._registry().threads[job_id].join(10)
    result = client.get(f"/api/jobs/{job_id}").json()
    assert result["status"] == "error" and "Fallo de prueba" in result["error"]
    assert not (services.DATA_DIR / "jobs" / job_id / "source.sqlite").exists()


def test_compaction_preserves_audit_counts():
    row = dict(warnings=["Aviso"] * 30, verification=dict(feasible=False, violations=[{}, {}]),
               run=dict(id=1, kind="optimize", payload={"large": "hidden"}))
    compact = api._compact(api._compact(row))
    assert compact["warnings_total"] == 30 and len(compact["warnings"]) == 20
    assert compact["verification"] == dict(feasible=False, violations_count=2)
    assert "payload" not in json.dumps(compact)
