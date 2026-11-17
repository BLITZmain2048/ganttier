"""MODO PROYECTO — prueba de todas sus funciones a través de la API.

Cubre: proyectos (CRUD, duplicar), calendarios y feriados de Chile, turnos, WBS, actividades (alta
individual y masiva, edición, PATCH masivo, mes macro → ventana, validaciones), relaciones (4 tipos,
desfases, rechazo de auto-relación y ciclos), recursos y asignaciones, CPM de libro verificado a mano,
restricciones de fecha y holgura negativa, GENERACIÓN DE PROGRAMA FACTIBLE desde una lista de tareas sin
fechas (verificada de forma independiente), los 4 objetivos de optimización y sus mezclas, ventanas duras,
simular/aplicar/historial, corridas obsoletas, diagnóstico de infactibilidad, perfil de carga y demo.
"""
from __future__ import annotations

from datetime import date

import pytest

from .conftest import (WorkdayIndex, calendar_id, check_project_schedule, err, make_task_list_project, ok)


def _project(client, name="P", start="2027-03-01", **kw):
    return ok(client.post("/api/projects", json={"name": name, "mode": "proyecto", "start_date": start,
                                                 "calendar_id": calendar_id(client), **kw}))


# ------------------------------------------------------------------ proyectos, calendarios, WBS
def test_proyecto_crud_y_duplicar(client):
    p = _project(client, "Obra A", description="prueba")
    got = ok(client.get(f"/api/projects/{p['id']}"))
    assert got["mode"] == "proyecto" and "counts" in got
    editable = {k: v for k, v in got.items() if k not in ("id", "counts", "created_at", "updated_at")}
    upd = ok(client.put(f"/api/projects/{p['id']}", json={**editable, "name": "Obra A2"}))
    assert upd["name"] == "Obra A2"
    ok(client.post(f"/api/projects/{p['id']}/activities", json={"code": "X1", "name": "x", "duration_days": 2}))
    dup = ok(client.post(f"/api/projects/{p['id']}/duplicate"))
    assert dup["id"] != p["id"]
    assert len(ok(client.get(f"/api/projects/{dup['id']}/activities"))) == 1
    ok(client.delete(f"/api/projects/{dup['id']}"))
    err(client.get(f"/api/projects/{dup['id']}"), 404)
    assert any(x["id"] == p["id"] for x in ok(client.get("/api/projects")))


def test_calendarios_feriados_chile_y_turnos(client):
    cals = {c["name"]: c for c in ok(client.get("/api/calendars"))}
    assert {"Chile L-V 8h", "Chile L-S 8h", "Turno 7x7 12h", "Turno 4x3 10h", "Corrido 7 días"} <= set(cals)
    days = {d["date"]: d for d in ok(client.get(f"/api/calendars/{cals['Chile L-V 8h']['id']}/days",
                                                params={"start": "2026-09-14", "end": "2026-09-20"}))}
    assert days["2026-09-17"]["working"] and days["2026-09-17"]["hours"] == 8
    assert not days["2026-09-18"]["working"] and not days["2026-09-19"]["working"]  # Fiestas Patrias
    assert not days["2026-09-20"]["working"]  # domingo
    # turno 7x7: 7 días de trabajo seguidos de 7 de descanso desde el ancla
    c = ok(client.post("/api/calendars", json={"name": "Turno prueba 7x7", "workweek": "1111111", "hours_per_day": 12,
                                               "holidays_country": None, "shift_pattern": "7x7", "shift_anchor": "2026-01-05"}))
    d = [x["working"] for x in ok(client.get(f"/api/calendars/{c['id']}/days", params={"start": "2026-01-05", "end": "2026-01-18"}))]
    assert d == [True] * 7 + [False] * 7
    # excepción puntual y eliminación
    c2 = ok(client.put(f"/api/calendars/{c['id']}", json={**c, "exceptions": [{"date": "2026-01-06", "working": False, "hours": 0, "reason": "Mantención"}]}))
    d2 = {x["date"]: x for x in ok(client.get(f"/api/calendars/{c2['id']}/days", params={"start": "2026-01-06", "end": "2026-01-06"}))}
    assert not d2["2026-01-06"]["working"]
    err(client.post("/api/calendars", json={"name": "malo", "workweek": "12", "hours_per_day": 8}))
    ok(client.delete(f"/api/calendars/{c['id']}"))


def test_wbs_crud(client):
    p = _project(client, "WBS")
    a = ok(client.post(f"/api/projects/{p['id']}/wbs", json={"project_id": p["id"], "code": "1", "name": "Ingeniería"}))
    b = ok(client.post(f"/api/projects/{p['id']}/wbs", json={"project_id": p["id"], "code": "1.1", "name": "Básica", "parent_id": a["id"]}))
    ok(client.put(f"/api/wbs/{b['id']}", json={**b, "name": "Ingeniería básica"}))
    assert {w["name"] for w in ok(client.get(f"/api/projects/{p['id']}/wbs"))} == {"Ingeniería", "Ingeniería básica"}
    err(client.put(f"/api/wbs/{a['id']}", json={**a, "parent_id": b["id"]}))  # ciclo en la jerarquía
    ok(client.delete(f"/api/wbs/{b['id']}"))


# ------------------------------------------------------------------ actividades y relaciones
def test_actividades_crud_masivo_macro_y_validaciones(client):
    p = _project(client, "Act")
    pid = p["id"]
    one = ok(client.post(f"/api/projects/{pid}/activities", json={"code": "A1", "name": "Uno", "duration_days": 3, "work_hours": 24}))
    many = ok(client.post(f"/api/projects/{pid}/activities", json=[{"code": f"B{i}", "name": f"b{i}", "duration_days": 1} for i in range(50)]))
    assert len(many) == 50
    got = ok(client.get(f"/api/activities/{one['id']}"))
    assert got["code"] == "A1" and got["planned_start"] is None
    ok(client.put(f"/api/activities/{one['id']}", json={**got, "name": "Uno editada"}))
    ok(client.patch(f"/api/projects/{pid}/activities", json=[{"id": one["id"], "macro_month": "2027-02"}]))
    got = ok(client.get(f"/api/activities/{one['id']}"))
    assert (got["window_start"], got["window_end"], got["name"]) == ("2027-02-01", "2027-02-28", "Uno editada")
    ok(client.patch(f"/api/projects/{pid}/activities", json=[{"id": m["id"], "priority": 80} for m in many[:10]]))
    assert sum(1 for a in ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 1000})) if a["priority"] == 80) == 10
    col = ok(client.get(f"/api/projects/{pid}/activities/columnar"))
    for k in ("ids", "codes", "names", "starts", "finishes", "durations", "work_hours", "macro_months", "window_starts", "window_hards", "critical"):
        assert len(col[k]) == 51, k
    err(client.post(f"/api/projects/{pid}/activities", json={"code": "A1", "name": "duplicada", "duration_days": 1}))
    err(client.post(f"/api/projects/{pid}/activities", json={"code": "NEG", "name": "negativa", "duration_days": -1}))
    err(client.patch(f"/api/projects/{pid}/activities", json=[{"id": one["id"], "macro_month": "2027-13"}]))
    ok(client.delete(f"/api/activities/{many[0]['id']}"))
    err(client.get(f"/api/activities/{many[0]['id']}"), 404)


def test_relaciones_tipos_autorrelacion_y_ciclo(client):
    p = _project(client, "Rel")
    pid = p["id"]
    a, b, c = ok(client.post(f"/api/projects/{pid}/activities", json=[{"code": x, "name": x, "duration_days": 2} for x in "ABC"]))
    for t in ("SS", "FF", "SF", "FS"):   # los 4 tipos se aceptan; se elimina y se recrea
        r = ok(client.post(f"/api/projects/{pid}/relations", json={"pred_id": a["id"], "succ_id": b["id"], "type": t, "lag_days": 1}))
        assert r["type"] == t and r["lag_days"] == 1
        if t != "FS":
            ok(client.delete(f"/api/relations/{r['id']}"))
    err(client.post(f"/api/projects/{pid}/relations", json={"pred_id": a["id"], "succ_id": b["id"], "type": "SS"}))  # par duplicado
    err(client.post(f"/api/projects/{pid}/relations", json={"pred_id": a["id"], "succ_id": a["id"], "type": "FS"}))
    ok(client.post(f"/api/projects/{pid}/relations", json={"pred_id": b["id"], "succ_id": c["id"], "type": "FS"}))
    bad = client.post(f"/api/projects/{pid}/relations", json={"pred_id": c["id"], "succ_id": a["id"], "type": "FS"})
    if bad.status_code < 400:  # si la API acepta el ciclo, el CPM debe rechazarlo con mensaje
        body = err(client.post(f"/api/projects/{pid}/schedule/cpm"))
        assert "ciclo" in str(body["detail"]).lower()
    else:
        assert "ciclo" in bad.text.lower()


def test_recursos_y_asignaciones_crud(client):
    p = _project(client, "Rec")
    pid = p["id"]
    r = ok(client.post("/api/resources", json={"name": "Cuadrilla X", "kind": "cuadrilla", "skills": ["montaje", "electrico"],
                                               "capacity_hours_day": 8, "max_units": 3, "project_id": pid}))
    assert r["skills"] == ["montaje", "electrico"]
    ok(client.put(f"/api/resources/{r['id']}", json={**r, "max_units": 4}))
    act = ok(client.post(f"/api/projects/{pid}/activities", json={"code": "M1", "name": "Montaje", "duration_days": 4, "work_hours": 64}))
    asg = ok(client.post(f"/api/projects/{pid}/assignments", json={"activity_id": act["id"], "resource_id": r["id"], "units": 2, "work_hours": 64}))
    only = ok(client.get(f"/api/projects/{pid}/assignments", params={"activity_id": act["id"]}))
    assert [x["id"] for x in only] == [asg["id"]]
    ok(client.put(f"/api/assignments/{asg['id']}", json={**asg, "units": 1}))
    ok(client.delete(f"/api/assignments/{asg['id']}"))
    ok(client.delete(f"/api/resources/{r['id']}"))


# ------------------------------------------------------------------ CPM
def test_cpm_red_de_libro_verificada_a_mano(client):
    """Red A(3)→B(2) FS; A→C(4) SS+1; B→D(1) FS; C→D FF+0. Calendario L-V, inicio lunes 2027-03-01."""
    p = _project(client, "CPM libro")
    pid = p["id"]
    A, B, C, D = ok(client.post(f"/api/projects/{pid}/activities", json=[
        {"code": "A", "name": "A", "duration_days": 3}, {"code": "B", "name": "B", "duration_days": 2},
        {"code": "C", "name": "C", "duration_days": 4}, {"code": "D", "name": "D", "duration_days": 1}]))
    for pr, su, t, lag in ((A, B, "FS", 0), (A, C, "SS", 1), (B, D, "FS", 0), (C, D, "FF", 0)):
        ok(client.post(f"/api/projects/{pid}/relations", json={"pred_id": pr["id"], "succ_id": su["id"], "type": t, "lag_days": lag}))
    r = ok(client.post(f"/api/projects/{pid}/schedule/cpm"))
    acts = {a["code"]: a for a in ok(client.get(f"/api/projects/{pid}/activities"))}
    # A: lu1-mi3; B: ju4-vi5; C: ma2-vi5 (SS+1); D: FS de B -> lu8; FF de C -> fin ≥ vi5 -> D = lu8
    assert (acts["A"]["early_start"], acts["A"]["early_finish"]) == ("2027-03-01", "2027-03-03")
    assert (acts["B"]["early_start"], acts["B"]["early_finish"]) == ("2027-03-04", "2027-03-05")
    assert (acts["C"]["early_start"], acts["C"]["early_finish"]) == ("2027-03-02", "2027-03-05")
    assert (acts["D"]["early_start"], acts["D"]["early_finish"]) == ("2027-03-08", "2027-03-08")
    assert r["finish"] == "2027-03-08"
    assert acts["A"]["critical"] and acts["B"]["critical"] and acts["D"]["critical"]
    assert acts["C"]["total_float"] == 1 and not acts["C"]["critical"]


def test_restricciones_y_holgura_negativa(client):
    p = _project(client, "Restricciones")
    pid = p["id"]
    a, b = ok(client.post(f"/api/projects/{pid}/activities", json=[
        {"code": "A", "name": "A", "duration_days": 5, "constraint_type": "SNET", "constraint_date": "2027-03-10"},
        {"code": "B", "name": "B", "duration_days": 5, "constraint_type": "FNLT", "constraint_date": "2027-03-12"}]))
    ok(client.post(f"/api/projects/{pid}/relations", json={"pred_id": a["id"], "succ_id": b["id"], "type": "FS"}))
    r = ok(client.post(f"/api/projects/{pid}/schedule/cpm"))
    acts = {x["code"]: x for x in ok(client.get(f"/api/projects/{pid}/activities"))}
    assert acts["A"]["early_start"] == "2027-03-10"
    assert acts["B"]["total_float"] < 0 and acts["B"]["critical"]
    assert any("holgura negativa" in w.lower() for w in r["warnings"])
    m = ok(client.post(f"/api/projects/{pid}/activities", json={"code": "M", "name": "M", "duration_days": 2, "constraint_type": "MSO", "constraint_date": "2027-04-05"}))
    ok(client.post(f"/api/projects/{pid}/schedule/cpm"))
    assert ok(client.get(f"/api/activities/{m['id']}"))["early_start"] == "2027-04-05"


# ------------------------------------------------------------------ programa factible desde lista de tareas
@pytest.mark.parametrize("seed,n", [(1, 25), (2, 60), (3, 120)])
def test_generar_programa_factible_desde_lista_de_tareas(client, seed, n):
    pid, cid = make_task_list_project(client, n=n, seed=seed, name=f"Lista {seed}")
    assert all(a["planned_start"] is None for a in ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 1000})))
    res = ok(client.post(f"/api/projects/{pid}/schedule/solve", json={
        "weights": {"level": 0.3, "makespan": 1, "window": 0.5, "cost": 0},
        "options": {"method": "auto", "time_limit_s": 20, "respect_resource_limits": True, "allow_overtime": False}, "apply": True}))
    assert res["feasible"] is True, res
    assert res["violations"] == []
    problems = check_project_schedule(client, pid, cid, capacity_per_skill={s: 16.0 for s in ("montaje", "civil", "protecciones")})
    assert problems == [], problems[:10]


def test_objetivos_y_mezclas(client):
    pid, cid = make_task_list_project(client, n=45, seed=11, name="Objetivos", units=3)
    base = ok(client.post(f"/api/projects/{pid}/schedule/solve", json={"weights": {"makespan": 1}, "options": {"time_limit_s": 15}, "apply": True}))
    assert base["feasible"]
    def opt(w, **o):
        return ok(client.post(f"/api/projects/{pid}/schedule/optimize", json={
            "weights": w, "options": {"method": "auto", "time_limit_s": 15, "level_period": "week", **o}, "apply": False}))
    lvl = opt({"level": 1, "makespan": 0, "window": 0, "cost": 0})
    assert lvl["after"]["load_std"] <= lvl["before"]["load_std"] + 1e-6
    mk = opt({"level": 0, "makespan": 1, "window": 0, "cost": 0})
    assert mk["after"]["finish"] <= lvl["after"]["finish"]
    win = opt({"level": 0, "makespan": 0, "window": 1, "cost": 0})
    assert win["after"]["days_out_of_window"] <= win["before"]["days_out_of_window"]
    cost = opt({"level": 0, "makespan": 0, "window": 0, "cost": 1}, respect_resource_limits=False, allow_overtime=True)
    assert cost["after"]["overtime_cost"] <= cost["before"]["overtime_cost"] + 1e-6
    mix = opt({"level": 1, "makespan": 1, "window": 1, "cost": 1})
    assert mix["after"] is not None and mix["profile_after"]["series"]
    for r in (lvl, mk, win, mix):
        assert r["method_used"] and r["status"] in {"optimo", "factible", "heuristico"}, r["status"]


def test_ventanas_duras_se_respetan(client):
    pid, cid = make_task_list_project(client, n=30, seed=5, name="Ventanas duras", hard_windows=True, units=3)
    res = ok(client.post(f"/api/projects/{pid}/schedule/solve", json={"weights": {"window": 1, "makespan": 0.2}, "options": {"time_limit_s": 15}, "apply": True}))
    if res["feasible"]:
        assert check_project_schedule(client, pid, cid, hard_windows=True) == []
    else:  # si es imposible, debe explicar por qué
        assert res["violations"] or res["warnings"]


def test_simular_aplicar_historial_y_corrida_obsoleta(client):
    pid, cid = make_task_list_project(client, n=20, seed=9, name="Corridas")
    ok(client.post(f"/api/projects/{pid}/schedule/solve", json={"apply": True}))
    sim = ok(client.post(f"/api/projects/{pid}/schedule/optimize", json={"weights": {"level": 1}, "options": {"time_limit_s": 10}, "apply": False}))
    runs = ok(client.get(f"/api/projects/{pid}/schedule/runs"))
    assert any(r["id"] == sim["run_id"] for r in runs)
    ok(client.post(f"/api/projects/{pid}/schedule/runs/{sim['run_id']}/apply"))
    sim2 = ok(client.post(f"/api/projects/{pid}/schedule/optimize", json={"weights": {"makespan": 1}, "options": {"time_limit_s": 10}, "apply": False}))
    a = ok(client.get(f"/api/projects/{pid}/activities"))[0]
    ok(client.put(f"/api/activities/{a['id']}", json={**a, "duration_days": a["duration_days"] + 3}))
    body = err(client.post(f"/api/projects/{pid}/schedule/runs/{sim2['run_id']}/apply"))
    assert "cambió" in str(body["detail"]).lower()


def test_diagnostico_de_infactibilidad(client):
    p = _project(client, "Infactible")
    pid = p["id"]
    ok(client.post("/api/resources", json={"name": "Solo civil", "skills": ["civil"], "capacity_hours_day": 8, "max_units": 1, "project_id": pid}))
    a, b = ok(client.post(f"/api/projects/{pid}/activities", json=[
        {"code": "X", "name": "Requiere SCADA", "duration_days": 2, "work_hours": 16, "required_skill": "scada"},
        {"code": "Y", "name": "Tarde", "duration_days": 10, "constraint_type": "MFO", "constraint_date": "2027-03-03"}]))
    ok(client.post(f"/api/projects/{pid}/relations", json={"pred_id": a["id"], "succ_id": b["id"], "type": "FS"}))
    r = client.post(f"/api/projects/{pid}/schedule/solve", json={"options": {"respect_resource_limits": True}, "apply": True})
    text = r.text.lower()
    if r.status_code < 400:
        assert r.json()["feasible"] is False or r.json()["warnings"]
    assert "scada" in text or "habilidad" in text or "mfo" in text or "terminar el" in text


def test_perfil_de_carga(client):
    pid, cid = make_task_list_project(client, n=20, seed=4, name="Carga")
    ok(client.post(f"/api/projects/{pid}/schedule/solve", json={"apply": True}))
    for group in ("skill", "resource"):
        for period in ("day", "week", "month"):
            prof = ok(client.get(f"/api/projects/{pid}/load", params={"group": group, "period": period}))
            assert prof["labels"] and prof["series"]
            for s in prof["series"]:
                assert len(s["load"]) == len(s["capacity"]) == len(prof["labels"])
    total = sum(sum(s["load"]) for s in ok(client.get(f"/api/projects/{pid}/load", params={"group": "skill", "period": "week"}))["series"])
    hh = sum(a["work_hours"] for a in ok(client.get(f"/api/projects/{pid}/activities")))
    assert abs(total - hh) < 1e-3 * max(1, hh)


def test_demo_proyecto(client):
    p = ok(client.post("/api/demo/proyecto"))
    col = ok(client.get(f"/api/projects/{p['id']}/activities/columnar"))
    assert len(col["ids"]) >= 100 and all(col["starts"])
    assert len(ok(client.get(f"/api/projects/{p['id']}/relations"))) > 50
