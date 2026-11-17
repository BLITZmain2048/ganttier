"""MODO PERIÓDICO (mantenimiento) — prueba de todas sus funciones a través de la API.

Cubre: proyecto de mantenimiento (horizonte obligatorio), CRUD del plan maestro con todas las
frecuencias (D, W, M, Q, S, A, ND, NW, NM, NA), generación de ocurrencias (conteos, fin de mes, meses y
día preferidos, tolerancias y ventanas, recorte al horizonte), regeneración con/sin reemplazo,
GENERACIÓN DEL PROGRAMA desde la lista de tareas periódicas (sin fechas) con asignación de trabajadores y
cuadrillas verificada de forma independiente (habilidad, calendario/turno, capacidad diaria), ventanas
duras, agrupación de desconexiones, programa de desconexiones (JSON y Excel), perfil de carga y escala
(20 000 en la suite normal; 100 000 en la prueba lenta).
"""
from __future__ import annotations

import io
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

import pytest

from .conftest import WorkdayIndex, calendar_id, err, ok

FREQS = [("D", 1), ("W", 1), ("M", 1), ("Q", 1), ("S", 1), ("A", 1), ("ND", 10), ("NW", 2), ("NM", 2), ("NA", 1)]


def _mproject(client, name="Mant", start="2026-01-01", end="2026-12-31"):
    return ok(client.post("/api/projects", json={"name": name, "mode": "mantenimiento", "start_date": start, "end_date": end,
                                                 "calendar_id": calendar_id(client)}))


def _workers(client, pid, cid, spec: dict[str, int], shift_cid=None):
    out = []
    for skill, n in spec.items():
        for k in range(n):
            cal = shift_cid if (shift_cid and k % 3 == 2) else cid
            out.append(ok(client.post("/api/resources", json={
                "name": f"Técnico {skill} {k + 1}", "kind": "trabajador", "skills": [skill], "capacity_hours_day": 8 if cal == cid else 12,
                "max_units": 1, "calendar_id": cal, "project_id": pid, "max_overtime_hours_day": 0})))
    return out


def test_proyecto_mantenimiento_requiere_horizonte(client):
    err(client.post("/api/projects", json={"name": "Sin fin", "mode": "mantenimiento", "start_date": "2026-01-01"}))


def test_plan_maestro_crud_todas_las_frecuencias(client):
    p = _mproject(client, "CRUD plan")
    pid = p["id"]
    tasks = []
    for f, n in FREQS:
        tasks.append(ok(client.post(f"/api/projects/{pid}/periodic-tasks", json={
            "code": f"T-{f}", "name": f"Tarea {f}", "frequency": f, "interval": n, "duration_days": 1, "work_hours": 4,
            "required_skill": "SSEE", "first_due": "2026-01-05", "tol_before_days": 2, "tol_after_days": 3})))
    got = ok(client.get(f"/api/projects/{pid}/periodic-tasks"))
    assert {t["frequency"] for t in got} == {f for f, _ in FREQS}
    t = tasks[2]
    ok(client.put(f"/api/periodic-tasks/{t['id']}", json={**t, "name": "Mensual editada", "work_hours": 6}))
    assert next(x for x in ok(client.get(f"/api/projects/{pid}/periodic-tasks")) if x["id"] == t["id"])["work_hours"] == 6
    err(client.post(f"/api/projects/{pid}/periodic-tasks", json={"code": "T-M", "name": "duplicado", "frequency": "M"}))
    err(client.post(f"/api/projects/{pid}/periodic-tasks", json={"code": "BAD", "name": "frecuencia", "frequency": "X"}))
    ok(client.delete(f"/api/periodic-tasks/{tasks[-1]['id']}"))


def test_generacion_de_ocurrencias_reglas(client):
    p = _mproject(client, "Reglas")
    pid = p["id"]
    mk = lambda **kw: ok(client.post(f"/api/projects/{pid}/periodic-tasks", json={"duration_days": 1, "work_hours": 2, "required_skill": "SSEE", **kw}))
    mk(code="M", name="Mensual", frequency="M", first_due="2026-01-15")
    mk(code="Q", name="Trimestral", frequency="Q", first_due="2026-02-10")
    mk(code="A", name="Anual meses", frequency="A", first_due="2026-01-10", preferred_months="10")
    mk(code="EOM", name="Fin de mes", frequency="M", first_due="2026-01-31", day_of_month=31)
    mk(code="W", name="Semanal lunes", frequency="W", first_due="2026-01-05", preferred_weekday=0)
    mk(code="N10", name="Cada 10 días", frequency="ND", interval=10, first_due="2026-01-02")
    mk(code="TOL", name="Tolerancias", frequency="M", first_due="2026-03-15", tol_before_days=4, tol_after_days=6)
    r = ok(client.post(f"/api/projects/{pid}/maintenance/generate", json={"horizon_start": "2026-01-01", "horizon_end": "2026-12-31", "replace": True}))
    acts = ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 100000}))
    by = defaultdict(list)
    for a in acts:
        by[a["code"].rsplit("-", 1)[0]].append(a)
    assert r["created"] == len(acts)
    assert len(by["M"]) == 12 and len(by["Q"]) == 4 and len(by["A"]) == 1
    assert date.fromisoformat(by["A"][0]["due_date"]).month == 10
    for a in by["EOM"]:  # último día laborable cercano al fin de cada mes
        d = date.fromisoformat(a["due_date"])
        assert (d + timedelta(days=7)).month != d.month or d.day >= 24
    assert all(date.fromisoformat(a["due_date"]).weekday() == 0 for a in by["W"]) and 50 <= len(by["W"]) <= 53
    assert 33 <= len(by["N10"]) <= 37
    for a in by["TOL"]:
        due, ws, we = (date.fromisoformat(a[k]) for k in ("due_date", "window_start", "window_end"))
        assert ws <= due <= we and (we - ws).days <= 10
    for a in acts:
        assert "2026-01-01" <= a["window_start"] <= a["window_end"] <= "2026-12-31"


def test_regenerar_con_y_sin_reemplazo(client):
    p = _mproject(client, "Regenerar")
    pid = p["id"]
    ok(client.post(f"/api/projects/{pid}/periodic-tasks", json={"code": "M", "name": "Mensual", "frequency": "M", "first_due": "2026-01-12", "work_hours": 4, "required_skill": "SSEE"}))
    ok(client.post(f"/api/projects/{pid}/maintenance/generate", json={"horizon_start": "2026-01-01", "horizon_end": "2026-12-31", "replace": True}))
    n1 = len(ok(client.get(f"/api/projects/{pid}/activities")))
    ok(client.post(f"/api/projects/{pid}/maintenance/generate", json={"horizon_start": "2026-01-01", "horizon_end": "2026-12-31", "replace": False}))
    assert len(ok(client.get(f"/api/projects/{pid}/activities"))) == n1  # sin duplicados
    ok(client.post(f"/api/projects/{pid}/maintenance/generate", json={"horizon_start": "2026-01-01", "horizon_end": "2026-12-31", "replace": True}))
    assert len(ok(client.get(f"/api/projects/{pid}/activities"))) == n1


def _independent_check(client, pid, cid, shift_cid):
    """Verifica sin usar el motor: fechas en ventana (duras), trabajador con la habilidad, laborable en su
    calendario, y HH diarias por trabajador ≤ su capacidad."""
    acts = ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 100000}))
    res = {r["id"]: r for r in ok(client.get("/api/resources", params={"project_id": pid}))}
    asg = ok(client.get(f"/api/projects/{pid}/assignments"))
    by_act = defaultdict(list)
    for a in asg:
        by_act[a["activity_id"]].append(a)
    lo = min(date.fromisoformat(a["planned_start"]) for a in acts) - timedelta(days=3)
    hi = max(date.fromisoformat(a["planned_finish"]) for a in acts) + timedelta(days=3)
    cals = {cid: WorkdayIndex(client, cid, lo, hi)}
    if shift_cid:
        cals[shift_cid] = WorkdayIndex(client, shift_cid, lo, hi)
    load = defaultdict(float)
    problems = []
    for a in acts:
        s, f = date.fromisoformat(a["planned_start"]), date.fromisoformat(a["planned_finish"])
        if a["window_hard"] and (s < date.fromisoformat(a["window_start"]) or f > date.fromisoformat(a["window_end"])):
            problems.append(f"{a['code']} fuera de ventana dura")
        members = [x for x in by_act.get(a["id"], []) if x["resource_id"]]
        if not members:
            problems.append(f"{a['code']} sin trabajador asignado")
            continue
        if len(members) < int(round(a["crew_size"] or 1)):
            problems.append(f"{a['code']}: cuadrilla incompleta ({len(members)} de {a['crew_size']})")
        for m in members:
            w = res[m["resource_id"]]
            if a["required_skill"] and a["required_skill"] not in w["skills"]:
                problems.append(f"{a['code']}: {w['name']} no tiene la habilidad {a['required_skill']}")
            wd = cals[w["calendar_id"]]
            days = [d for d in wd.work if s <= d <= f]
            if not days:
                problems.append(f"{a['code']}: {w['name']} no trabaja en {s}..{f}")
                continue
            for d in days:
                load[(w["id"], d)] += (m["work_hours"] or a["work_hours"] / len(members)) / len(days)
    for (rid, d), v in load.items():
        cap = min(res[rid]["capacity_hours_day"], cals[res[rid]["calendar_id"]].hours.get(d, 0)) * res[rid]["max_units"] + res[rid]["max_overtime_hours_day"]
        if v > cap + 1e-6:
            problems.append(f"{res[rid]['name']} sobrecargado el {d}: {v:.1f} > {cap}")
    return problems


def test_generar_programa_desde_lista_de_tareas_periodicas(client):
    p = _mproject(client, "Desde lista")
    pid = p["id"]
    cid = calendar_id(client)
    shift = calendar_id(client, "Turno 7x7 12h")
    _workers(client, pid, cid, {"SSEE": 6, "Protecciones": 3, "Líneas": 4}, shift_cid=shift)
    specs = [("Inspección visual", "M", 1, 4, "SSEE", 1, False), ("Termografía", "Q", 1, 6, "SSEE", 1, False),
             ("Pruebas de relés", "A", 2, 16, "Protecciones", 2, True), ("Mantenimiento interruptor", "S", 2, 24, "SSEE", 2, True),
             ("Inspección de franja", "Q", 2, 16, "Líneas", 2, False), ("Lavado de aislación", "S", 1, 12, "Líneas", 2, True)]
    k = 0
    for se in range(8):
        for name, f, dur, hh, skill, crew, outage in specs:
            k += 1
            ok(client.post(f"/api/projects/{pid}/periodic-tasks", json={
                "code": f"MT{k:04d}", "name": name, "frequency": f, "duration_days": dur, "work_hours": hh, "crew_size": crew,
                "required_skill": skill, "first_due": (date(2026, 1, 5) + timedelta(days=(k * 11) % 60)).isoformat(),
                "location": f"S/E {se + 1:02d}", "requires_outage": outage, "tol_before_days": 7, "tol_after_days": 10,
                "window_hard": k % 4 == 0}))
    assert ok(client.get(f"/api/projects/{pid}/activities")) == []  # solo lista de tareas: sin actividades ni fechas
    res = ok(client.post(f"/api/projects/{pid}/schedule/solve", json={
        "weights": {"level": 1, "window": 1, "cost": 0.2}, "options": {"level_period": "week", "assign_workers": True, "group_outages": True},
        "horizon_start": "2026-01-01", "horizon_end": "2026-12-31", "apply": True}))
    assert res["feasible"] is True, res.get("violations", [])[:5]
    acts = ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 100000}))
    assert len(acts) >= 180 and all(a["planned_start"] for a in acts)   # 8 S/E × ~25 ejecuciones/año
    problems = _independent_check(client, pid, cid, shift)
    assert problems == [], problems[:10]
    # agrupación de desconexiones: trabajos con desconexión de la misma S/E comparten fechas más que al azar
    outs = ok(client.get(f"/api/projects/{pid}/outages"))
    assert outs["groups"] and outs["summary"]
    grouped = sum(1 for g in outs["groups"] if len(g.get("activities") or g.get("jobs") or []) >= 2)
    assert grouped >= 1


def test_generar_de_nuevo_agrega_tareas_nuevas_sin_tocar_las_existentes(client):
    """Revisión R1-18: con regenerate=False se completan las ocurrencias faltantes (tareas nuevas)."""
    p = _mproject(client, "Incremental")
    pid = p["id"]
    cid = calendar_id(client)
    _workers(client, pid, cid, {"SSEE": 3})
    mk = lambda code, f: ok(client.post(f"/api/projects/{pid}/periodic-tasks", json={
        "code": code, "name": code, "frequency": f, "duration_days": 1, "work_hours": 4, "required_skill": "SSEE",
        "first_due": "2026-01-12"}))
    mk("MEN", "M")
    first = ok(client.post(f"/api/projects/{pid}/schedule/solve", json={"apply": True}))
    assert first["feasible"] and first["generation"]["created"] == 12
    before = {a["code"]: (a["id"], a["planned_start"]) for a in ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 1000}))}
    mk("TRI", "Q")
    second = ok(client.post(f"/api/projects/{pid}/schedule/solve", json={"apply": True, "regenerate": False}))
    assert second["feasible"], second["violations"][:5]
    assert second["generation"]["created"] == 4 and second["generation"]["kept"] == 12
    acts = {a["code"]: a for a in ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 1000}))}
    assert sum(1 for c in acts if c.startswith("TRI-")) == 4
    assert all(acts[c]["id"] == before[c][0] for c in before)          # mismas actividades (no se recrearon)
    assert all(a["planned_start"] for a in acts.values())


def test_programa_de_desconexiones_excel(client):
    p = ok(client.post("/api/demo/mantenimiento", params={"n": 2000}))
    x = client.get(f"/api/projects/{p['id']}/outages.xlsx", params={"from": "2026-01-01", "to": "2026-06-30"})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(x.content), read_only=True)
    assert {"Resumen", "Programa", "Detalle"} <= set(wb.sheetnames)


def test_perfil_de_carga_mantenimiento(client):
    p = ok(client.post("/api/demo/mantenimiento", params={"n": 2000}))
    ok(client.post(f"/api/projects/{p['id']}/schedule/solve", json={"weights": {"level": 1, "window": 1}, "apply": True}))
    prof = ok(client.get(f"/api/projects/{p['id']}/load", params={"group": "skill", "period": "week"}))
    assert len(prof["series"]) >= 3 and all(len(s["load"]) == len(prof["labels"]) for s in prof["series"])


def test_escala_20000_ocurrencias(client):
    t = time.perf_counter()
    p = ok(client.post("/api/demo/mantenimiento", params={"n": 20000}))
    res = ok(client.post(f"/api/projects/{p['id']}/schedule/solve", json={"weights": {"level": 1, "window": 1, "cost": 0.2}, "apply": True}))
    col = ok(client.get(f"/api/projects/{p['id']}/activities/columnar"))
    assert len(col["ids"]) == 20000 and all(col["starts"])
    assert res["feasible"] is True
    assert time.perf_counter() - t < 120


@pytest.mark.slow
def test_escala_100000_ocurrencias(client):
    t = time.perf_counter()
    p = ok(client.post("/api/demo/mantenimiento", params={"n": 100000}))
    res = ok(client.post(f"/api/projects/{p['id']}/schedule/solve", json={"weights": {"level": 1, "window": 1}, "apply": True}))
    col = ok(client.get(f"/api/projects/{p['id']}/activities/columnar"))
    assert len(col["ids"]) == 100000 and all(col["starts"])
    assert res["feasible"] is True
    assert time.perf_counter() - t < 300
