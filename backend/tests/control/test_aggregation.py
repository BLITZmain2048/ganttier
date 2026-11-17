"""Conciliación EVM y contratos HTTP de los tres análisis de solo lectura."""
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from ganttier import db, repo, services
from ganttier.control.aggregation import aggregate_evm
from ganttier.domain import Activity, BaselineActivity, ProgressEntry, WBSNode


@pytest.mark.parametrize("group", ["wbs", "location", "skill"])
def test_weighted_indices_and_exact_reconciliation(make_activity, calendar, group):
    acts = [make_activity(id=1, wbs_id=1, work_hours=20, percent_complete=50),
            make_activity(id=2, wbs_id=2, work_hours=100, percent_complete=90)]
    metadata = [Activity(1, 1, "A1", "Uno", location="Norte", required_skill="Montaje"),
                Activity(2, 1, "A2", "Dos", location="Sur", required_skill="Pruebas")]
    wbs = [WBSNode(1, 1, "1", "Raíz"), WBSNode(2, 1, "1.1", "Hijo", parent_id=1)]
    progress = [ProgressEntry(1, 1, date(2026, 1, 9), 50, actual_hours=20),
                ProgressEntry(2, 2, date(2026, 1, 9), 90, actual_hours=100)]
    result = aggregate_evm(acts, None, progress, calendar, date(2026, 1, 9),
                           activities=metadata, wbs=wbs, group=group)
    populated = [g for g in result.groups if g.activity_ids]
    assert sorted(g.evm.SPI for g in populated) == [.5, .9]
    assert result.total.SPI == pytest.approx(100 / 120)
    assert result.total.SPI != pytest.approx(.70)
    assert result.reconciliation.SPI == pytest.approx(.8333333333)
    assert result.reconciliation.CPI == pytest.approx(100 / 120)
    assert result.reconciliation.matches
    assert result.reconciliation.differences == dict(BAC=0, PV=0, EV=0, AC=0)
    assert result.reconciliation.BAC == result.total.BAC == 120
    assert next(g for g in result.groups if g.name == "Sin clasificar").evm.BAC == 0


@pytest.mark.parametrize("basis", ["hours", "cost", "count"])
@pytest.mark.parametrize("baseline_kind", ["none", "partial", "empty"])
def test_scope_baseline_history_and_unclassified(make_activity, calendar, basis, baseline_kind):
    acts = [make_activity(id=1), make_activity(id=2, work_hours=90), make_activity(id=3)]
    baseline = None if baseline_kind == "none" else {} if baseline_kind == "empty" else {
        1: BaselineActivity(1, 1, date(2026, 1, 5), date(2026, 1, 9), 10, 80, 5)}
    progress = [ProgressEntry(1, 1, date(2026, 1, 7), 20, actual_hours=2, actual_cost=10),
                ProgressEntry(2, 1, date(2026, 1, 9), 80, actual_hours=90, actual_cost=900)]
    metadata = [Activity(1, 1, "A1", "Uno", location=" "), Activity(2, 1, "A2", "Dos", location="Sur")]
    result = aggregate_evm(acts, baseline, progress, calendar, date(2026, 1, 7),
                           activities=metadata, group="location", basis=basis)
    assert result.reconciliation.matches
    for field in ("BAC", "PV", "EV", "AC"):
        assert sum(getattr(g.evm, field) for g in result.groups) == pytest.approx(getattr(result.total, field), abs=1e-6)
    unknown = next(g for g in result.groups if g.name == "Sin clasificar")
    expected_ids = [1, 3] if baseline_kind == "none" else [1] if baseline_kind == "partial" else []
    assert unknown.activity_ids == expected_ids
    if basis == "hours" and baseline_kind == "partial":
        assert result.total.BAC == 10
        assert result.total.EV == 2
        assert result.total.AC == 2


@pytest.fixture
def analysis_api():
    """Inicializa SQLite en memoria; todas las peticiones usan esa misma conexión."""
    from ganttier.api.dependencies import connection
    from ganttier.main import create_app
    conn = db.connect(":memory:")
    db.initialize(conn)
    app = create_app()
    app.dependency_overrides[connection] = lambda: conn
    # La base ya está inicializada; no se inicia el planificador de informes.
    client = TestClient(app)
    try:
        yield client, conn
    finally:
        client.close()
        conn.close()


def seed(conn):
    cal = repo.create(conn, "calendars", {"name": "Todos los días", "workweek": "1111111", "holidays_country": None})
    project = repo.create(conn, "projects", {"name": "Análisis", "start_date": "2026-01-01",
                          "data_date": "2026-01-10", "calendar_id": cal.id})
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "A1", "name": "Trabajo",
                           "planned_start": "2026-01-01", "planned_finish": "2026-01-20", "duration_days": 20,
                           "work_hours": 100, "cost_budget": 200, "location": "Norte", "required_skill": "Montaje"})
    services.create_baseline(conn, project.id)
    repo.create(conn, "progress", {"activity_id": activity.id, "date": "2026-01-10", "percent_complete": 40,
                                  "actual_hours": 50, "actual_cost": 100, "actual_start": "2026-01-01"})
    return project.id, activity.id


def test_forecast_endpoint_numeric_contract_blank_etc_and_baseline_dates(analysis_api):
    client, conn = analysis_api
    pid, aid = seed(conn)
    # El programa vigente cambia; PD y el origen deben seguir siendo los de la base.
    repo.update(conn, "activities", aid, {"planned_start": "2026-02-01", "planned_finish": "2026-03-01"})
    response = client.get(f"/api/projects/{pid}/control/forecasts", params={"basis": "hours", "etc": 65})
    assert response.status_code == 200, response.text
    rows = {r["name"]: r for r in response.json()}
    for key, value in {"EAC_CPI": 125, "EAC_presupuesto": 110, "EAC_combinado": 143.75,
                       "EAC_reestimado": 115, "IEAC_t": 25, "duracion_remanente": 22}.items():
        assert rows[key]["value"] == pytest.approx(value)
    assert rows["IEAC_t"]["finish_date"] == "2026-01-25"
    assert rows["duracion_remanente"]["finish_date"] == "2026-01-22"
    response = client.get(f"/api/projects/{pid}/control/forecasts?etc=")
    assert response.status_code == 200
    assert next(r for r in response.json() if r["name"] == "EAC_reestimado")["status"] == "no calculable"


@pytest.mark.parametrize("group", ["wbs", "location", "skill"])
@pytest.mark.parametrize("basis", ["hours", "cost", "count"])
def test_aggregation_api_matches_existing_service(analysis_api, group, basis):
    client, conn = analysis_api
    pid, _ = seed(conn)
    # Alcance posterior a la línea base no debe filtrarse hacia la agregación.
    repo.create(conn, "activities", {"project_id": pid, "code": "NUEVA", "name": "Fuera de base", "work_hours": 400})
    response = client.get(f"/api/projects/{pid}/control/evm-by", params={"group": group, "basis": basis})
    assert response.status_code == 200, response.text
    result = response.json()
    existing = repo.json_value(services.control_result(conn, pid, "evm", basis=basis))
    assert result["total"] == existing
    assert result["reconciliation"]["matches"]
    assert max(abs(v) for v in result["reconciliation"]["differences"].values()) <= 1e-6


def test_quality_endpoint_does_not_modify_counts_dates_or_any_rows(analysis_api):
    client, conn = analysis_api
    pid, aid = seed(conn)
    # Simula un dato heredado inválido para verificar que el diagnóstico no lo oculta.
    conn.execute("UPDATE activities SET actual_finish='2026-02-01' WHERE id=?", (aid,))
    before = list(conn.iterdump())
    changes = conn.total_changes
    response = client.get(f"/api/projects/{pid}/control/quality")
    assert response.status_code == 200, response.text
    future = next(r for r in response.json()["findings"] if r["rule"] == "fechas_reales_futuras")
    assert future["activities"] == [{"id": aid, "code": "A1"}]
    assert conn.total_changes == changes
    assert list(conn.iterdump()) == before


def test_quality_api_reads_progress_at_cut_and_default_maintenance_profile(analysis_api):
    client, conn = analysis_api
    pid, aid = seed(conn)
    repo.update(conn, "projects", pid, {"mode": "mantenimiento", "end_date": "2026-12-31"})
    repo.update(conn, "activities", aid, {"planned_finish": "2026-01-09", "percent_complete": 100})
    response = client.get(f"/api/projects/{pid}/control/quality")
    assert response.status_code == 200, response.text
    rows = {r["rule"]: r for r in response.json()["rules"]}
    assert rows["sin_predecesora"]["status"] == "no aplica"
    assert rows["actividades_vencidas"]["count"] == 1


@pytest.mark.parametrize("endpoint,query", [
    ("forecasts", "etc=nan"), ("forecasts", "etc=inf"), ("forecasts", "etc=-1"),
    ("forecasts", "etc=texto"), ("forecasts", "basis=otro"), ("evm-by", "group=otro"),
    ("evm-by", "basis=otro"), ("quality", "long_lag_days=-1"),
    ("quality", "profile=otro"), ("quality", "start_ids=9999"),
])
def test_invalid_analysis_queries(analysis_api, endpoint, query):
    client, conn = analysis_api
    pid, _ = seed(conn)
    response = client.get(f"/api/projects/{pid}/control/{endpoint}?{query}")
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("endpoint", ["forecasts", "quality", "evm-by"])
def test_unknown_project(analysis_api, endpoint):
    client, _ = analysis_api
    assert client.get(f"/api/projects/9999/control/{endpoint}").status_code == 404
