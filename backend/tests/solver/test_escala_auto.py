"""Regresión: con método «auto» y listas grandes el presupuesto repartido entre heurística y CP-SAT
no debe dejar un programa incompleto. Si CP-SAT se descarta por tamaño, la construcción se repite
con todo el tiempo disponible y el resultado debe ser factible (verificado de forma independiente)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modos.conftest import check_project_schedule, make_task_list_project  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("escala")
    os.environ["GANTTIER_DB"] = str(tmp / "g.db")
    os.environ["GANTTIER_DATA_DIR"] = str(tmp)
    import importlib
    from fastapi.testclient import TestClient
    import ganttier.services as services
    importlib.reload(services)
    import ganttier.main as main
    importlib.reload(main)
    with TestClient(main.create_app()) as c:
        yield c


def test_auto_con_1500_tareas_entrega_programa_factible(client):
    pid, cid = make_task_list_project(client, n=1500, seed=6, name="Escala auto", units=2, windows=False)
    r = client.post(f"/api/projects/{pid}/schedule/solve", json={
        "weights": {"makespan": 1}, "options": {"method": "auto", "time_limit_s": 20}, "apply": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feasible"] is True, (body["violations"][:3], body["warnings"][:5])
    problems = check_project_schedule(client, pid, cid, capacity_per_skill={s: 16.0 for s in ("montaje", "civil", "protecciones")})
    assert problems == [], problems[:5]
