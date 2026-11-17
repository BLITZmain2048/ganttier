"""Infraestructura común de las pruebas por modo (proyecto, periódico, control, importar/exportar).

Todas las pruebas usan la API REST real (FastAPI TestClient con ciclo de vida completo) sobre una base
SQLite temporal, igual que la GUI de escritorio. Los datos se crean como LISTAS DE TAREAS, sin fechas
planificadas: el programa debe encontrar un programa factible por sí mismo.
"""
from __future__ import annotations

import importlib
import os
import random
from datetime import date, timedelta

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("modo")
    old = {k: os.environ.get(k) for k in ("GANTTIER_DB", "GANTTIER_DATA_DIR")}
    os.environ["GANTTIER_DB"] = str(tmp / "ganttier.db")
    os.environ["GANTTIER_DATA_DIR"] = str(tmp / "datos")
    from fastapi.testclient import TestClient
    import ganttier.services as services
    importlib.reload(services)
    import ganttier.main as main
    importlib.reload(main)
    with TestClient(main.create_app()) as c:
        yield c
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def ok(r, *codes):
    codes = codes or (200, 201, 204)
    assert r.status_code in codes, f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:1200]}"
    if r.status_code == 204 or not r.content:
        return None
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content


def err(r, *codes):
    """La API debe rechazar con 4xx y mensaje en español (nunca 500)."""
    codes = codes or (400, 404, 409, 422)
    assert r.status_code in codes, f"se esperaba {codes}, llegó {r.status_code}: {r.text[:600]}"
    body = r.json()
    assert isinstance(body.get("detail"), (str, list)) and body["detail"], body
    return body


def calendar_id(client, name="Chile L-V 8h") -> int:
    cals = ok(client.get("/api/calendars"))
    return next(c["id"] for c in cals if c["name"] == name)


class WorkdayIndex:
    """Días laborables de un calendario según la API (para verificar programas de forma independiente)."""

    def __init__(self, client, cid: int, start: date, end: date):
        days = ok(client.get(f"/api/calendars/{cid}/days", params={"start": start.isoformat(), "end": end.isoformat()}))
        self.hours = {date.fromisoformat(d["date"]): float(d["hours"]) for d in days}
        self.work = sorted(d for d, h in self.hours.items() if h > 0)
        self.rank = {d: i for i, d in enumerate(self.work)}

    def is_work(self, d: date) -> bool:
        return self.hours.get(d, 0) > 0

    def span(self, s: date, f: date) -> int:
        return sum(1 for d in self.work if s <= d <= f)


def make_task_list_project(client, *, n=40, skills=("montaje", "civil", "protecciones"), units=2, seed=7,
                           name="Lista de tareas", start="2027-03-01", windows=True, hard_windows=False):
    """Crea un proyecto SOLO con la lista de tareas: recursos, WBS, actividades, relaciones. Sin fechas."""
    rnd = random.Random(seed)
    cid = calendar_id(client)
    p = ok(client.post("/api/projects", json={"name": name, "mode": "proyecto", "start_date": start, "calendar_id": cid}))
    pid = p["id"]
    for s in skills:
        ok(client.post("/api/resources", json={"name": f"Cuadrilla {s}", "kind": "cuadrilla", "skills": [s], "capacity_hours_day": 8,
                                               "max_units": units, "calendar_id": cid, "project_id": pid, "cost_hour": 20000,
                                               "overtime_cost_hour": 30000, "max_overtime_hours_day": 0}))
    root = ok(client.post(f"/api/projects/{pid}/wbs", json={"project_id": pid, "code": "P", "name": "Proyecto"}))
    acts = []
    base = date.fromisoformat(start)
    for i in range(n):
        dur = rnd.randint(1, 8)
        crew = rnd.choice([1, 1, 2])
        a = {"code": f"T{i:03d}", "name": f"Tarea {i}", "wbs_id": root["id"], "duration_days": dur, "work_hours": dur * 8 * crew,
             "required_skill": rnd.choice(skills), "crew_size": crew}
        if windows and i % 5 == 0:
            m = base + timedelta(days=30 * (i // 10))
            a["macro_month"] = f"{m.year}-{m.month:02d}"
            a["window_hard"] = hard_windows
        acts.append(a)
    created = ok(client.post(f"/api/projects/{pid}/activities", json=acts))
    ids = [a["id"] for a in created]
    rels = []
    for i in range(1, n):
        for j in rnd.sample(range(max(0, i - 8), i), k=min(i - max(0, i - 8), rnd.randint(0, 2))):
            t = rnd.choice(["FS", "FS", "FS", "SS", "FF"])
            rels.append((ids[j], ids[i], t, rnd.choice([0, 0, 1, 2])))
    for pr, su, t, lag in rels:
        ok(client.post(f"/api/projects/{pid}/relations", json={"pred_id": pr, "succ_id": su, "type": t, "lag_days": lag}))
    return pid, cid


def check_project_schedule(client, pid, cid, *, capacity_per_skill: dict[str, float] | None = None, hard_windows=False):
    """Verificación INDEPENDIENTE del programa aplicado (no usa el código del motor)."""
    acts = {a["id"]: a for a in ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 100000}))}
    assert acts, "sin actividades"
    starts = [date.fromisoformat(a["planned_start"]) for a in acts.values() if a["planned_start"]]
    finishes = [date.fromisoformat(a["planned_finish"]) for a in acts.values() if a["planned_finish"]]
    assert len(starts) == len(acts), "hay actividades sin fecha de inicio"
    wd = WorkdayIndex(client, cid, min(starts) - timedelta(days=10), max(finishes) + timedelta(days=10))
    problems = []
    for a in acts.values():
        s, f = date.fromisoformat(a["planned_start"]), date.fromisoformat(a["planned_finish"])
        if a["duration_days"] > 0:
            if not wd.is_work(s) or not wd.is_work(f):
                problems.append(f"{a['code']}: inicio/fin en día no laborable")
            if wd.span(s, f) != a["duration_days"]:
                problems.append(f"{a['code']}: duración {wd.span(s, f)} ≠ {a['duration_days']}")
        if hard_windows and a.get("window_hard") and a.get("window_start"):
            if s < date.fromisoformat(a["window_start"]) or f > date.fromisoformat(a["window_end"]):
                problems.append(f"{a['code']}: fuera de ventana dura")
    for r in ok(client.get(f"/api/projects/{pid}/relations")):
        p, q = acts[r["pred_id"]], acts[r["succ_id"]]
        ps, pf = wd.rank[date.fromisoformat(p["planned_start"])], wd.rank[date.fromisoformat(p["planned_finish"])]
        qs, qf = wd.rank[date.fromisoformat(q["planned_start"])], wd.rank[date.fromisoformat(q["planned_finish"])]
        lag = r["lag_days"]
        need = {"FS": qs >= pf + 1 + lag, "SS": qs >= ps + lag, "FF": qf >= pf + lag, "SF": qf >= ps + lag - 1}[r["type"]]
        if not need:
            problems.append(f"relación {r['type']} {p['code']}→{q['code']} (desfase {lag}) no se cumple")
    if capacity_per_skill:
        load: dict[tuple[str, date], float] = {}
        for a in acts.values():
            if not a["work_hours"] or a["duration_days"] <= 0:
                continue
            s, f = date.fromisoformat(a["planned_start"]), date.fromisoformat(a["planned_finish"])
            days = [d for d in wd.work if s <= d <= f]
            for d in days:
                key = (a["required_skill"], d)
                load[key] = load.get(key, 0) + a["work_hours"] / len(days)
        for (sk, d), v in load.items():
            if v > capacity_per_skill[sk] + 1e-6:
                problems.append(f"capacidad {sk} excedida el {d}: {v:.1f} > {capacity_per_skill[sk]}")
    return problems
