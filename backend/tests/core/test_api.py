"""Pruebas HTTP sin red. Las dependencias opcionales se sustituyen por contratos públicos."""
from datetime import date
from pathlib import Path
import sys
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient

from ganttier import domain as d, services
from ganttier.main import create_app


@pytest.fixture
def client(local_dir, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(local_dir / "api.db"))
    monkeypatch.setattr(services, "DATA_DIR", local_dir)
    with TestClient(create_app()) as value:
        yield value


def make_project(client, **overrides):
    response = client.post("/api/projects", json={"name": "API", "start_date": "2026-01-01", **overrides})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_crud_columnar_macro_and_validation(client):
    assert client.get("/api/health").json()["status"] == "ok"
    pid = make_project(client)
    response = client.post(f"/api/projects/{pid}/activities", json=[{"code": "A", "name": "Actividad", "macro_month": "2028-02"}, {"code": "B", "name": "Hito", "duration_days": 0}])
    assert response.status_code == 201, response.text
    aid = response.json()[0]["id"]
    assert response.json()[0]["window_end"] == "2028-02-29"
    assert client.get(f"/api/projects/{pid}").json()["counts"]["activities"] == 2
    columnar = client.get(f"/api/projects/{pid}/activities/columnar").json()
    assert columnar["ids"][0] == aid
    assert columnar["kinds"] == ["actividad", "hito"]
    assert client.patch(f"/api/projects/{pid}/activities", json=[{"id": aid, "macro_month": "2026-06"}]).status_code == 200
    assert client.get(f"/api/projects/{pid}/activities").json()[0]["window_end"] == "2026-06-30"
    assert client.put(f"/api/activities/{aid}", json={"name": "Cambio"}).json()["code"] == "A"
    invalid = client.put(f"/api/activities/{aid}", json={"duration_days": -3})
    assert invalid.status_code == 422 and "errors" in invalid.json()
    assert client.get(f"/api/projects/{pid}/activities", params={"limit": -1}).status_code == 422
    assert client.delete(f"/api/activities/{aid}").status_code == 204
    assert client.post(f"/api/projects/{pid}/duplicate").status_code == 201
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_master_endpoints_baseline_progress_and_calendar(client):
    pid = make_project(client)
    cal = client.post("/api/calendars", json={"name": "Especial", "holidays_country": None, "exceptions": [{"date": "2026-01-03", "working": True, "hours": 4}]}).json()
    days = client.get(f"/api/calendars/{cal['id']}/days", params={"start": "2026-01-03", "end": "2026-01-03"})
    assert days.json()[0]["hours"] == 4
    assert client.put(f"/api/calendars/{cal['id']}", json={"name": "Revisado"}).status_code == 200
    wbs = client.post(f"/api/projects/{pid}/wbs", json={"code": "1", "name": "Ingeniería"}).json()
    resource = client.post("/api/resources", json={"project_id": pid, "name": "Ana", "skills": ["SSEE"]}).json()
    activity = client.post(f"/api/projects/{pid}/activities", json={"code": "A", "name": "Prueba", "wbs_id": wbs["id"]}).json()
    assignment = client.post(f"/api/projects/{pid}/assignments", json={"activity_id": activity["id"], "resource_id": resource["id"], "work_hours": 8})
    assert assignment.status_code == 201
    assert client.put(f"/api/assignments/{assignment.json()['id']}", json={"work_hours": 16}).status_code == 200
    baseline = client.post(f"/api/projects/{pid}/baselines", json={"name": "BL"})
    assert baseline.status_code == 201
    assert client.post(f"/api/baselines/{baseline.json()['id']}/activate").status_code == 200
    progress = client.post(f"/api/projects/{pid}/progress", json=[{"activity_id": activity["id"], "date": "2026-01-05", "percent_complete": 30, "actual_hours": 3}])
    assert progress.status_code == 201
    assert client.put(f"/api/projects/{pid}/data-date", json={"data_date": "2026-01-10"}).status_code == 200
    assert client.delete(f"/api/progress/{progress.json()[0]['id']}").status_code == 204
    assert client.delete(f"/api/baselines/{baseline.json()['id']}").status_code == 204
    assert client.get(f"/api/projects/{pid}/load").status_code == 200


def test_cross_project_atomicity_and_missing_modules(client, monkeypatch):
    first, second = make_project(client), make_project(client)
    a = client.post(f"/api/projects/{first}/activities", json={"code": "A", "name": "A"}).json()
    b = client.post(f"/api/projects/{second}/activities", json={"code": "B", "name": "B"}).json()
    assert client.post(f"/api/projects/{first}/relations", json={"pred_id": a["id"], "succ_id": b["id"]}).status_code == 422
    assert client.post(f"/api/projects/{first}/progress", json={"activity_id": b["id"], "date": "2026-01-01", "percent_complete": 10}).status_code == 422
    assert client.patch(f"/api/projects/{first}/activities", json=[{"id": a["id"], "name": "Cambiada"}, {"id": b["id"], "name": "Error"}]).status_code == 422
    assert client.get(f"/api/projects/{first}/activities").json()[0]["name"] == "A"
    monkeypatch.setitem(sys.modules, "ganttier.engine", None)
    response = client.post(f"/api/projects/{first}/schedule/cpm")
    assert response.status_code == 503 and "programación" in response.json()["detail"]
    assert client.get("/api/health").status_code == 200


def test_import_export_and_templates_with_stubs(client, monkeypatch):
    module = types.ModuleType("ganttier.excel_io")
    bundle = d.ImportBundle("proyecto", {"name": "Importado", "start_date": "2026-01-01"}, activities=[{"code": "EX", "name": "Excel"}])
    module.parse_workbook = lambda path, mode: bundle
    def export(book, path):
        assert book.activities[0]["code"] == "EX"
        Path(path).write_bytes(b"excel-stub")
    module.export_project = export
    module.build_project_template = lambda path: Path(path).write_bytes(b"template-project")
    module.build_maintenance_template = lambda path: Path(path).write_bytes(b"template-maintenance")
    monkeypatch.setitem(sys.modules, "ganttier.excel_io", module)
    pid = make_project(client)
    upload = {"file": ("datos.xlsx", b"stub", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    assert client.post(f"/api/projects/{pid}/import?dry_run=true", files=upload).status_code == 200
    assert client.get(f"/api/projects/{pid}/activities").json() == []
    assert client.post(f"/api/projects/{pid}/import", files=upload).json()["summary"]["actividades"] == 1
    assert client.get(f"/api/projects/{pid}/export.xlsx").content == b"excel-stub"
    assert client.get("/api/templates/proyecto").content == b"template-project"
    assert client.get("/api/templates/mantenimiento").content == b"template-maintenance"
    assert client.post("/api/import/new-project", files=upload).status_code == 201
    bundle.issues = [d.ImportIssue("Actividades", 2, "code", "Código duplicado.")]
    response = client.post(f"/api/projects/{pid}/import", files=upload)
    assert response.status_code == 422
    assert response.json()["errors"][0]["row"] == 2


def test_reports_download_traversal_cors_and_openapi(client, local_dir, monkeypatch):
    pid = make_project(client)
    monkeypatch.setattr(services, "build_report_data", lambda *args: types.SimpleNamespace(period_start=date(2026, 1, 1)))
    module = types.ModuleType("ganttier.reports")
    def render(report, fmt, path):
        Path(path).write_bytes(b"informe")
        return path
    module.render = render
    monkeypatch.setitem(sys.modules, "ganttier.reports", module)
    response = client.post(f"/api/projects/{pid}/reports", json={"period_type": "monthly", "period_start": "2026-01-01", "formats": ["pdf", "xlsx"]})
    assert response.status_code == 200, response.text
    assert client.get(response.json()[0]["url"]).content == b"informe"
    assert client.get("/api/reports/files/..%5Capi.db").status_code in {404, 422}
    assert client.get("/api/no-existe").status_code == 404
    assert client.get("/control").status_code == 200
    # P4-14: la interfaz web antigua ya no se usa; ningún origen del navegador recibe permiso CORS.
    response = client.options("/api/projects", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in response.headers
    # Protección contra DNS rebinding: un Host ajeno se rechaza.
    assert client.get("/api/projects", headers={"Host": "atacante.example"}).status_code == 400
    assert client.get("/openapi.json").status_code == 200
