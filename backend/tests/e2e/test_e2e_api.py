"""Prueba de integración de punta a punta sobre la API (Claude, integración).

Recorre el flujo real de ambos modos: demo -> CPM -> optimización -> línea base -> avance ->
curva S / EVM / desempeño -> informes en 4 formatos -> plantillas -> importación -> exportación.
"""
from __future__ import annotations

import importlib
import os
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    os.environ["GANTTIER_DB"] = str(tmp / "e2e.db")
    os.environ["GANTTIER_DATA_DIR"] = str(tmp)
    from fastapi.testclient import TestClient
    import ganttier.main as main
    importlib.reload(main)
    app = getattr(main, "app", None) or main.create_app()
    with TestClient(app) as c:
        yield c


def ok(r, code=200):
    assert r.status_code in (code, 200, 201), f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:800]}"
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r


def test_health_and_calendars(client):
    ok(client.get("/api/health"))
    cals = ok(client.get("/api/calendars"))
    assert len(cals) >= 5
    cl = next(c for c in cals if c["holidays_country"] == "CL" and c["workweek"] == "1111100")
    days = ok(client.get(f"/api/calendars/{cl['id']}/days", params={"start": "2026-09-14", "end": "2026-09-20"}))
    by = {d["date"]: d for d in days}
    assert by["2026-09-18"]["working"] is False and by["2026-09-19"]["working"] is False
    assert by["2026-09-17"]["working"] is True


def test_project_mode_flow(client):
    p = ok(client.post("/api/demo/proyecto"))
    pid = p["id"]
    col = ok(client.get(f"/api/projects/{pid}/activities/columnar"))
    n = len(col["ids"])
    assert n >= 40
    for k in ("codes", "names", "starts", "finishes", "critical", "pct", "durations", "work_hours", "macro_months"):
        assert k in col and len(col[k]) == n, k
    rels = ok(client.get(f"/api/projects/{pid}/relations"))
    assert len(rels) > 0
    cpm = ok(client.post(f"/api/projects/{pid}/schedule/cpm"))
    assert cpm["critical_count"] > 0 and cpm["finish"]
    a0 = ok(client.get(f"/api/activities/{col['ids'][0]}"))
    assert a0["code"] == col["codes"][0]
    # macro_month -> ventana
    ok(client.patch(f"/api/projects/{pid}/activities", json=[{"id": col["ids"][3], "macro_month": "2027-02"}]))
    a3 = ok(client.get(f"/api/activities/{col['ids'][3]}"))
    assert a3["window_start"] == "2027-02-01" and a3["window_end"] == "2027-02-28"
    # optimización con mezcla de objetivos
    for w in ({"level": 1, "makespan": 0, "window": 0, "cost": 0}, {"level": 0, "makespan": 1, "window": 0, "cost": 0},
              {"level": 1, "makespan": 1, "window": 1, "cost": 1}):
        r = ok(client.post(f"/api/projects/{pid}/schedule/optimize", json={
            "weights": w, "options": {"method": "auto", "time_limit_s": 5}, "apply": False}))
        assert r["after"] is not None and r["profile_after"] is not None, r
    r = ok(client.post(f"/api/projects/{pid}/schedule/optimize", json={
        "weights": {"level": 1, "makespan": 0.3, "window": 1, "cost": 0.2}, "options": {"method": "heuristic", "time_limit_s": 10}, "apply": True}))
    runs = ok(client.get(f"/api/projects/{pid}/schedule/runs"))
    assert len(runs) >= 1
    load = ok(client.get(f"/api/projects/{pid}/load", params={"group": "skill", "period": "week"}))
    assert load["labels"] and load["series"]
    # línea base y avance
    b = ok(client.post(f"/api/projects/{pid}/baselines", json={"name": "LB0"}))
    ok(client.post(f"/api/baselines/{b['id']}/activate"))
    col = ok(client.get(f"/api/projects/{pid}/activities/columnar"))
    starts = sorted(s for s in col["starts"] if s)
    dd = starts[len(starts) // 3]
    ok(client.put(f"/api/projects/{pid}/data-date", json={"data_date": dd}))
    res = ok(client.get("/api/resources", params={"project_id": pid}))
    wk = [x for x in res if x["kind"] == "trabajador"] or res
    prog = []
    for i, (aid, s) in enumerate(zip(col["ids"], col["starts"])):
        if s and s <= dd:
            prog.append({"activity_id": aid, "date": dd, "percent_complete": 100 if i % 3 else 60,
                         "actual_hours": 10 + i, "resource_id": wk[i % len(wk)]["id"], "actual_start": s})
    ok(client.post(f"/api/projects/{pid}/progress", json=prog))
    ok(client.post(f"/api/projects/{pid}/schedule/cpm"))
    sc = ok(client.get(f"/api/projects/{pid}/control/scurve", params={"period": "week", "basis": "hours"}))
    assert len(sc["labels"]) == len(sc["planned_cum"]) > 3
    assert abs(sc["planned_cum"][-1] - 100) < 1.0
    ev = ok(client.get(f"/api/projects/{pid}/control/evm"))
    assert ev["BAC"] > 0 and ev["EV"] > 0
    perf = ok(client.get(f"/api/projects/{pid}/control/performance", params={"start": starts[0], "end": dd}))
    assert len(perf) > 0
    # informes
    for period in ("weekly", "monthly", "annual"):
        files = ok(client.post(f"/api/projects/{pid}/reports", json={"period_type": period, "period_start": dd,
                                                                     "formats": ["pdf", "xlsx", "docx", "html"]}))
        assert {f["format"] for f in files} == {"pdf", "xlsx", "docx", "html"}
        for f in files:
            r = client.get(f["url"])
            assert r.status_code == 200 and len(r.content) > 1000, f
    # exportación e importación ida y vuelta
    x = client.get(f"/api/projects/{pid}/export.xlsx")
    assert x.status_code == 200 and len(x.content) > 5000
    imp = client.post("/api/import/new-project", files={"file": ("p.xlsx", x.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    newp = ok(imp)
    col2 = ok(client.get(f"/api/projects/{newp['id']}/activities/columnar"))
    assert len(col2["ids"]) == len(col["ids"])


def test_templates_import(client):
    for kind in ("proyecto", "mantenimiento"):
        t = client.get(f"/api/templates/{kind}")
        assert t.status_code == 200 and len(t.content) > 5000
        imp = client.post("/api/import/new-project", files={"file": (f"{kind}.xlsx", t.content, "application/octet-stream")})
        p = ok(imp)
        assert p["mode"] == kind


def test_maintenance_mode_flow(client):
    t0 = time.time()
    p = ok(client.post("/api/demo/mantenimiento", params={"n": 20000}))
    pid = p["id"]
    col = ok(client.get(f"/api/projects/{pid}/activities/columnar"))
    n = len(col["ids"])
    assert 15000 <= n <= 25000, n
    tasks = ok(client.get(f"/api/projects/{pid}/periodic-tasks"))
    assert len(tasks) > 10
    r = ok(client.post(f"/api/projects/{pid}/schedule/optimize", json={
        "weights": {"level": 1, "makespan": 0, "window": 1, "cost": 0.2},
        "options": {"method": "heuristic", "time_limit_s": 20, "level_period": "week", "assign_workers": True}, "apply": True}))
    assert r["after"]["load_std"] <= r["before"]["load_std"] + 1e-6
    col = ok(client.get(f"/api/projects/{pid}/activities/columnar"))
    assert all(col["starts"]), "todas las ocurrencias deben quedar programadas"
    print(f"mantenimiento 20k: {time.time() - t0:.1f} s")
