"""MODO CONTROL — prueba de todas sus funciones a través de la API.

Cubre: líneas base (crear, activar, eliminar), fecha de corte, registro de avance (alta masiva, inicio
real automático, término al 100 %, eliminación, rechazo de datos inválidos), reprogramación del
remanente por CPM desde la fecha de corte, curva S (3 períodos × 3 bases, monotonía, 100 % final,
ganado ≤ presupuesto, proyección desde el corte), EVM verificado con cálculo manual, desempeño por
trabajador verificado a mano, informes semanal/mensual/anual en PDF, Excel, Word y HTML con contenido,
descarga segura, y emisión automática (configuración, ejecución idempotente, historial).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from .conftest import calendar_id, err, ok


@pytest.fixture(scope="module")
def ctl(client):
    """Proyecto de control con 2 actividades en serie de 10 días × 80 HH y 2 trabajadores."""
    cid = calendar_id(client)
    p = ok(client.post("/api/projects", json={"name": "Control manual", "mode": "proyecto", "start_date": "2027-03-01", "calendar_id": cid}))
    pid = p["id"]
    w1 = ok(client.post("/api/resources", json={"name": "Ana", "kind": "trabajador", "skills": ["electrico"], "capacity_hours_day": 8, "project_id": pid}))
    w2 = ok(client.post("/api/resources", json={"name": "Beto", "kind": "trabajador", "skills": ["electrico"], "capacity_hours_day": 8, "project_id": pid}))
    a, b = ok(client.post(f"/api/projects/{pid}/activities", json=[
        {"code": "A", "name": "Montaje", "duration_days": 10, "work_hours": 80, "cost_budget": 800000, "required_skill": "electrico"},
        {"code": "B", "name": "Pruebas", "duration_days": 10, "work_hours": 80, "cost_budget": 800000, "required_skill": "electrico"}]))
    ok(client.post(f"/api/projects/{pid}/relations", json={"pred_id": a["id"], "succ_id": b["id"], "type": "FS"}))
    ok(client.post(f"/api/projects/{pid}/assignments", json={"activity_id": a["id"], "resource_id": w1["id"], "units": 1, "work_hours": 80}))
    ok(client.post(f"/api/projects/{pid}/assignments", json={"activity_id": b["id"], "resource_id": w2["id"], "units": 1, "work_hours": 80}))
    ok(client.post(f"/api/projects/{pid}/schedule/solve", json={"weights": {"makespan": 1}, "apply": True}))
    return {"pid": pid, "a": a["id"], "b": b["id"], "w1": w1["id"], "w2": w2["id"], "cid": cid}


def test_lineas_base(client, ctl):
    pid = ctl["pid"]
    b1 = ok(client.post(f"/api/projects/{pid}/baselines", json={"name": "LB0"}))
    b2 = ok(client.post(f"/api/projects/{pid}/baselines", json={"name": "LB-temporal"}))
    ok(client.post(f"/api/baselines/{b1['id']}/activate"))
    assert ok(client.get(f"/api/projects/{pid}"))["active_baseline_id"] == b1["id"]
    ok(client.delete(f"/api/baselines/{b2['id']}"))
    assert [x["name"] for x in ok(client.get(f"/api/projects/{pid}/baselines"))] == ["LB0"]
    col = ok(client.get(f"/api/projects/{pid}/activities/columnar"))
    assert col["baseline_starts"] == ["2027-03-01", "2027-03-15"]


def test_avance_curva_s_evm_y_reprogramacion(client, ctl):
    pid, a, b = ctl["pid"], ctl["a"], ctl["b"]
    # corte al viernes 2027-03-05 (5 de 10 días de A): avance real 40 % con 36 HH
    ok(client.put(f"/api/projects/{pid}/data-date", json={"data_date": "2027-03-05"}))
    ok(client.post(f"/api/projects/{pid}/progress", json=[{"activity_id": a, "date": "2027-03-05", "percent_complete": 40,
                                                            "actual_hours": 36, "actual_cost": 400000, "resource_id": ctl["w1"]}]))
    act = ok(client.get(f"/api/activities/{a}"))
    assert act["actual_start"] == "2027-03-01" and act["status"] == "en_curso"   # inicio real automático
    ev = ok(client.get(f"/api/projects/{pid}/control/evm", params={"basis": "hours"}))
    assert ev["BAC"] == pytest.approx(160)
    assert ev["PV"] == pytest.approx(40, abs=0.5)      # 5/10 de A (80 HH)
    assert ev["EV"] == pytest.approx(32, abs=0.5)      # 40 % de 80
    assert ev["AC"] == pytest.approx(36, abs=0.5)
    assert ev["SPI"] == pytest.approx(0.8, abs=0.02) and ev["CPI"] == pytest.approx(32 / 36, abs=0.02)
    assert ev["forecast_finish"] > ev["planned_finish"]
    for period in ("day", "week", "month"):
        for basis in ("hours", "cost", "count"):
            sc = ok(client.get(f"/api/projects/{pid}/control/scurve", params={"period": period, "basis": basis}))
            pl = sc["planned_cum"]
            assert pl == sorted(pl) and abs(pl[-1] - 100) < 0.6
            earned = [v for v in sc["earned_cum"] if v is not None]
            assert earned and max(earned) <= 100
            fc = [v for v in sc["forecast_cum"] if v is not None]
            assert fc and abs(fc[-1] - 100) < 0.6
    # CPM reprograma el remanente desde la fecha de corte
    ok(client.post(f"/api/projects/{pid}/schedule/cpm"))
    act = ok(client.get(f"/api/activities/{a}"))
    assert act["planned_finish"] > "2027-03-12"         # 60 % de 10 días restantes desde el lunes 8
    bb = ok(client.get(f"/api/activities/{b}"))
    assert bb["planned_start"] > act["planned_finish"]
    # 100 % completa el término real
    ok(client.put(f"/api/projects/{pid}/data-date", json={"data_date": "2027-03-19"}))
    ok(client.post(f"/api/projects/{pid}/progress", json=[{"activity_id": a, "date": "2027-03-19", "percent_complete": 100, "actual_hours": 50, "resource_id": ctl["w1"]}]))
    act = ok(client.get(f"/api/activities/{a}"))
    assert act["status"] == "terminada" and act["actual_finish"] == "2027-03-19"
    entries = ok(client.get(f"/api/projects/{pid}/progress"))
    assert len(entries) == 2
    err(client.post(f"/api/projects/{pid}/progress", json=[{"activity_id": a, "date": "2027-03-19", "percent_complete": 140}]))


def test_desempeno_por_trabajador(client, ctl):
    pid = ctl["pid"]
    perf = {k["name"]: k for k in ok(client.get(f"/api/projects/{pid}/control/performance", params={"start": "2027-03-01", "end": "2027-03-31"}))}
    ana = perf["Ana"]
    assert ana["completed_activities"] == 1 and ana["actual_hours"] == pytest.approx(86)
    assert ana["hh_efficiency"] == pytest.approx(80 / 86, abs=0.02)
    assert ana["avg_delay_days"] is not None and ana["avg_delay_days"] >= 0
    assert "Beto" in perf and perf["Beto"]["completed_activities"] == 0


def test_informes_4_formatos_x_3_periodos_con_contenido(client, ctl):
    pid = ctl["pid"]
    for period in ("weekly", "monthly", "annual"):
        files = ok(client.post(f"/api/projects/{pid}/reports", json={"period_type": period, "period_start": "2027-03-10", "formats": ["pdf", "xlsx", "docx", "html"]}))
        got = {f["format"]: client.get(f["url"]) for f in files}
        assert set(got) == {"pdf", "xlsx", "docx", "html"}
        assert got["pdf"].content[:5] == b"%PDF-" and len(got["pdf"].content) > 5000
        z = zipfile.ZipFile(io.BytesIO(got["xlsx"].content))
        assert any(n.startswith("xl/worksheets/") for n in z.namelist())
        z = zipfile.ZipFile(io.BytesIO(got["docx"].content))
        assert "word/document.xml" in z.namelist()
        html = got["html"].text
        assert "Control manual" in html and ("Curva S" in html or "curva S" in html)
        titles = {"weekly": "semanal", "monthly": "mensual", "annual": "anual"}
        assert titles[period] in html.lower()
    err(client.get("/api/reports/files/..%2F..%2Fganttier.db"), 400, 404, 422)


def test_emision_automatica(client, ctl):
    pid = ctl["pid"]
    cfg = ok(client.get(f"/api/projects/{pid}/autoreports"))
    assert cfg["enabled"] is False
    ok(client.put(f"/api/projects/{pid}/autoreports", json={**cfg, "enabled": True, "weekly": True, "monthly": True, "annual": False, "formats": ["html"]}))
    first = ok(client.post(f"/api/projects/{pid}/autoreports/run"))
    second = ok(client.post(f"/api/projects/{pid}/autoreports/run"))
    assert len(first) >= 1 and all(e.get("status") == "ok" for e in first)
    assert len(second) == 0  # idempotente: no repite períodos ya emitidos
    log = ok(client.get(f"/api/projects/{pid}/autoreports/log"))
    entries = log if isinstance(log, list) else (log.get("items") or log.get("log") or log.get("entries") or [])
    assert len(entries) >= len(first)
