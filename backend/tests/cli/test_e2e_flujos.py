"""Flujos completos, persistencia de resultados y límites de ejecución."""
import json
from pathlib import Path
import sqlite3

import pytest

pytestmark = pytest.mark.slow

METRICS = {"finish", "makespan_days", "peak_load", "load_std", "overload_hours",
           "overtime_hours", "overtime_cost", "days_out_of_window", "activities_out_of_window", "personnel_cost"}


def make_plan(cli, count=3):
    return cli.json_file("plan.json", {"project": cli.ids["project"], "parallel": 2,
        "defaults": {"options": {"method": "heuristic", "time_limit_s": 1, "allow_overtime": False}},
        "variants": [{"id": f"v{i}", "name": f"Variante {i}", "objective": objective, "options": {"seed": i}}
                     for i, objective in enumerate(("plazo", "nivelar", "equilibrado")[:count])]})


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_result(result):
    assert {"variant", "status", "feasible", "infeasibility", "metrics", "before", "after",
            "verification", "warnings", "elapsed_s", "scenario_hash", "seed", "error"} <= result.keys()
    assert result["status"] in {"ok", "infactible", "error", "timeout", "cancelado"}
    assert result["infeasibility"] in {None, "demostrada", "no_encontrada"}
    assert METRICS <= result["metrics"].keys()
    assert isinstance(result["seed"], int)
    if result["feasible"]:
        assert result["scenario_hash"] and result["verification"]["feasible"]
        assert not result["verification"]["violations"]
    assert not (result.get("run") or {}).get("payload"), "EJE/PRG: stdout no debe incluir run.payload"


def test_proyecto_diagnosticar_comparar_lote_reanudar_aplicar_control_informe(cli):
    initial = cli.snapshot()
    diag = cli.project("programa", "diagnosticar")["data"]
    assert {"capacity_by_skill", "intensity_violations", "hard_window_conflicts", "feasibility_hint"} <= diag.keys()
    plan = make_plan(cli)
    best, results = cli.root / "mejor.json", cli.root / "comparacion.jsonl"
    comparison = cli.project("programa", "comparar", "--plan", plan, "--paralelo", "2", "--mejor-a", best, "--resultados-a", results, timeout=150)["data"]
    assert {"summary", "top", "results_file", "counts"} <= comparison.keys()
    assert len(comparison["results"]) >= 3
    for result in comparison["results"]:
        check_result(result)
    assert best.is_file() and len(read_lines(results)) == 3
    assert cli.snapshot() == initial
    batch = cli.root / "lote.jsonl"
    cli.project("programa", "lote", "--plan", plan, "--paralelo", "2", "--presupuesto", "120", "--resultados-a", batch, timeout=150)
    first_bytes = batch.read_bytes()
    first = read_lines(batch)
    assert len(first) == 3 and len({r["variant"]["id"] for r in first}) == 3
    cli.project("programa", "lote", "--plan", plan, "--paralelo", "2", "--presupuesto", "120", "--resultados-a", batch, "--reanudar", timeout=150)
    assert batch.read_bytes() == first_bytes, "EJE: reanudar duplicó o reescribió resultados terminados"
    assert cli.snapshot() == initial
    simulated = cli.project("programa", "aplicar", "--resultado", best)
    assert simulated["db"]["mode"] == "simulated"
    assert cli.snapshot() == initial
    applied = cli.project("--escribir", "programa", "aplicar", "--resultado", best, timeout=100)
    assert applied["db"]["mode"] == "write" and Path(applied["db"]["backup"]).is_file()
    assert cli.snapshot()["tables"] != initial["tables"]
    verification = cli.project("programa", "verificar")["data"]
    assert verification["feasible"] and not verification["violations"]
    cli.project("--escribir", "control", "linea-base", "crear")
    cli.project("--escribir", "control", "fecha-corte", "fijar", "2027-01-05")
    cli.project("--escribir", "control", "avance", "registrar", "--json", json.dumps(dict(codigo="T1", fecha="2027-01-05", porcentaje=50, hh_reales=8)))
    with sqlite3.connect(cli.db) as conn:
        assert conn.execute("SELECT percent_complete FROM progress WHERE activity_id=?", (cli.ids["activities"][0],)).fetchone()[0] == 50
    curve = cli.project("control", "curva-s", "--base", "horas", "--periodo", "semana")["data"]
    assert curve, "CTL: curva S vacía tras línea base y avance"
    directory = cli.root / "informe_mensual"
    report = cli.project("--escribir", "informes", "generar", "--tipo", "mensual", "--inicio", "2027-01-01", "--formatos", "html", "--a", directory, timeout=100)
    assert report["data"]
    html = list(directory.glob("*.html"))
    assert html and all(p.stat().st_size > 100 for p in html)


def test_mantenimiento_resolver_con_equipos(cli):
    before = cli.snapshot()
    teams = cli.project("equipos", "listar", maintenance=True)["data"]
    assert teams
    result = cli.project("programa", "resolver", "--objetivo", "nivelar", "--metodo", "heuristic",
                         "--tiempo", "2", "--semilla", "7", "--desde", "2027-01-01", "--hasta", "2027-01-31",
                         "--sin-horas-extra", maintenance=True, timeout=100)["data"]
    check_result(result)
    assert result["feasible"] is True, result
    assert cli.snapshot() == before


def test_escenario_modificado_rechaza_aplicar_exit_tres(cli):
    best = cli.root / "mejor.json"
    cli.project("programa", "comparar", "--plan", make_plan(cli, 1), "--paralelo", "1", "--mejor-a", best, timeout=100)
    cli.project("--escribir", "actividades", "actualizar", cli.ids["activities"][0], "--json", '{"work_hours":8}')
    before = cli.snapshot()
    body = cli.project("--escribir", "programa", "aplicar", "--resultado", best, code=3)
    assert body["error"]["code"] == "escenario_cambio"
    assert cli.snapshot() == before


def test_presupuesto_agotado_exit_cuatro_y_reanudar_parciales(cli):
    plan = make_plan(cli)
    target = cli.root / "parciales.jsonl"
    body = cli.project("programa", "lote", "--plan", plan, "--paralelo", "1", "--presupuesto", "0.05", "--resultados-a", target,
                       env={"GANTTIER_TRABAJADOR_RETARDO_S": "3"}, code=4, timeout=30)
    assert body["error"] is None or body["error"]["code"] == "timeout"
    if target.exists():
        partial = read_lines(target)
        assert all(r["status"] in {"ok", "infactible", "timeout", "cancelado", "error"} for r in partial)
    else:
        partial = []
    cli.project("programa", "lote", "--plan", plan, "--paralelo", "2", "--presupuesto", "120", "--resultados-a", target, "--reanudar", timeout=150)
    complete = [r for r in read_lines(target) if r["status"] in {"ok", "infactible"}]
    assert {r["variant"]["id"] for r in complete} == {"v0", "v1", "v2"}
    assert len(complete) == 3
    for row in partial:
        if row["status"] in {"ok", "infactible"}:
            assert row in complete


def test_dependencia_ortools_ausente_exit_seis(cli):
    hook = cli.root / "sin_ortools"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text("import sys\nclass NoOrtools:\n def find_spec(self, fullname, *args):\n  if fullname.startswith('ortools'): raise ModuleNotFoundError('TST: OR-Tools no instalado', name='ortools')\nsys.meta_path.insert(0, NoOrtools())\n", encoding="utf-8")
    body = cli.project("programa", "resolver", "--metodo", "cpsat", "--tiempo", "1", env={"PYTHONPATH": str(hook)}, code=6, timeout=100)
    assert not body["ok"] and body["error"] is not None, "PRG: exit 6 debe llevar error en el sobre; no basta data.error con ok=true"
    assert body["error"]["code"] == "dependencia_faltante"


def test_infactibilidad_demostrada_exit_dos(cli):
    # Una sola persona no puede aportar 1000 HH en dos jornadas sin horas extra.
    cli.project("--escribir", "actividades", "actualizar", cli.ids["activities"][0], "--json", '{"work_hours":1000}')
    # El prechequeo del motor demuestra intensidad sobre demanda por habilidad.
    # Retirar la asignación nominal hace que la demanda proceda de la actividad.
    cli.project("--escribir", "asignaciones", "eliminar", cli.ids["assignments"][0])
    result = cli.project("programa", "resolver", "--metodo", "heuristic", "--tiempo", "1", "--sin-horas-extra", code=2, timeout=100)
    assert result["ok"] and result["error"] is None
    assert result["data"]["feasible"] is False
    assert result["data"]["infeasibility"] == "demostrada"
