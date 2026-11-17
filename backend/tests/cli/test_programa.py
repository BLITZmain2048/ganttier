"""Pruebas PRG en proceso; datos y procesos acotados a trabajos/_temporales/.../PRG."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import signal
import sys
import threading
from uuid import uuid4

import pytest

from ganttier.cli._base import CliError, Context
from ganttier.cli.comandos import programa


ROOT = Path(__file__).resolve().parents[3] / "trabajos" / "_temporales" / "cli_codex_20261019" / "PRG"
INTEGRATED = all(importlib.util.find_spec("ganttier.optimizacion." + name) is not None
                 for name in ("ejecutor", "variantes", "ranking", "espacio", "aplicar"))
integration = pytest.mark.skipif(not INTEGRATED, reason="Faltan los módulos EJE/ESP del contrato §7.")


def invoke(ctx, *argv):
    parser = argparse.ArgumentParser()
    programa.register(parser.add_subparsers(dest="group", required=True))
    args = parser.parse_args(["programa", *map(str, argv)])
    return args.handler(args, ctx)


def snapshot(ctx, *, ignore_sequence=False):
    with closing(ctx.conn()) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {t: conn.execute('SELECT COUNT(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0] for t in tables}
        content = list(conn.iterdump())
        if ignore_sequence:
            # db.initialize (propiedad NUC/existente) confirma INSERT OR IGNORE de calendarios
            # antes del handler y adelanta su secuencia. Evidencia y parche: CONFLICTOS PRG.
            content = [line for line in content if '"sqlite_sequence"' not in line]
    runs = {str(p.relative_to(ctx.data_dir)): sha256(p.read_bytes()).hexdigest()
            for p in sorted((ctx.data_dir / "runs").rglob("*")) if p.is_file()}
    return sha256(json.dumps([counts, content, runs], sort_keys=True).encode()).hexdigest(), counts, runs


@pytest.fixture
def case(monkeypatch):
    from ganttier import db, services

    folder = ROOT / ("case_" + uuid4().hex[:12])
    folder.mkdir(parents=True)
    data_dir = folder / "datos"
    data_dir.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setenv("GANTTIER_DB", str(folder / "test.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GANTTIER_CLI_TEMP_DIR", str(folder / "scratch"))
    monkeypatch.setenv("GANTTIER_CLI_PARALELO", "1")
    monkeypatch.setattr(services, "DATA_DIR", data_dir)
    # Elegir la demo incorporada de 80 tareas de modo reproducible, sin un Excel opcional.
    monkeypatch.setattr(db, "ROOT", folder)
    with closing(db.connect(folder / "test.db")) as conn:
        db.initialize(conn)
        demo = services.demo_project(conn)
        maintenance = services.demo_maintenance(6, conn)
        conn.commit()
    ctx = Context(folder / "test.db", data_dir, project=str(demo.id))
    return ctx, maintenance.id, folder


def plan_file(folder, count=4, project=None):
    plan = {"defaults": {"objective": "plazo", "options": {"method": "heuristic", "time_limit_s": 3}},
            "grid": {"options.seed": list(range(count))}, "parallel": 1}
    if project is not None:
        plan["project"] = project
    path = folder / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


@integration
@pytest.mark.parametrize("mode", ["proyecto", "mantenimiento"])
def test_resolver_no_modifica_base_ni_runs(case, mode):
    ctx, maintenance, folder = case
    if mode == "mantenimiento":
        ctx.project = str(maintenance)
    before = snapshot(ctx)
    result = invoke(ctx, "resolver", "--objetivo", "plazo", "--metodo", "heuristic", "--tiempo", "4",
                    "--mejor-a", folder / "solucion.json")
    assert result.exit_code == 0, result.data
    assert result.data["feasible"] is True
    assert result.data["verification"]["feasible"] is True
    assert ctx.mutation_info.simulated
    assert "run" not in result.data and "run_payload" not in result.data
    assert Path(result.data["run_payload_ref"]).is_file()
    assert json.loads((folder / "solucion.json").read_text(encoding="utf-8"))["variant"] == result.data["variant"]
    assert snapshot(ctx) == before
    assert not list((folder / "scratch").glob(".ganttier-cli-*"))


@integration
def test_comparar_tres_variantes_ranking_y_producto(case, capsys):
    ctx, _, folder = case
    before = snapshot(ctx)
    result = invoke(ctx, "comparar", "--objetivos", "plazo,nivelar,ventanas", "--semillas", "0,1",
                    "--metodo", "heuristic", "--tiempo", "3", "--paralelo", "2", "--top", "3",
                    "--conservar-top", "2", "--resultados-a", folder / "compare.jsonl", "--mejor-a", folder / "mejor.json")
    assert result.exit_code == 0, result.data
    assert result.data["counts"]["completed"] == 6
    assert len(result.data["top"]) == 3
    assert [r["rank"] for r in result.data["results"]] == list(range(1, 7))
    assert result.data["top"][0]["feasible"]
    assert {r["variant"]["options"]["seed"] for r in result.data["results"]} == {0, 1}
    keys = [(not r["feasible"], *(r["metrics"][k] for k in
             ("activities_out_of_window", "overtime_hours", "makespan_days", "load_std"))) for r in result.data["results"]]
    assert keys == sorted(keys)
    assert len(list((folder / "compare_runs").glob("*.json"))) == 2
    journal = [json.loads(s) for s in (folder / "compare.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(journal) == 6 and all("run" not in r for r in journal)
    assert all("run" not in r for r in result.data["results"])
    assert snapshot(ctx) == before
    captured = capsys.readouterr()
    assert captured.out == "" and "variantes" in captured.err


@integration
def test_lote_cancelacion_y_reanudacion_reales(case, monkeypatch):
    from ganttier.optimizacion import ejecutor

    ctx, maintenance, folder = case
    ctx.project = str(maintenance)
    before = snapshot(ctx)
    plan = plan_file(folder)
    journal = folder / "lote.jsonl"
    original = ejecutor.ejecutar_plan
    observed = []

    def interrupted(*args, **kwargs):
        callback = kwargs["callback"]

        def stop_at_two(row, done, total):
            # El diario ya está durable antes de invocar callback.
            assert len(journal.read_text(encoding="utf-8").splitlines()) == done
            if row["status"] in {"ok", "infactible"}:
                observed.append(row["variant"]["id"])
            callback(row, done, total)
            if done == 2:
                kwargs["cancelar"].set()

        kwargs["callback"] = stop_at_two
        return original(*args, **kwargs)

    monkeypatch.setattr(ejecutor, "ejecutar_plan", interrupted)
    first = invoke(ctx, "lote", "--plan", plan, "--paralelo", "1", "--resultados-a", journal)
    assert first.exit_code == 4 and first.data["counts"]["feasible"] == 2
    assert first.data["counts"]["pending"] + first.data["counts"]["retryable"] == 2
    monkeypatch.setattr(ejecutor, "ejecutar_plan", original)
    second = invoke(ctx, "lote", "--plan", plan, "--paralelo", "1", "--resultados-a", journal, "--reanudar")
    assert second.exit_code == 0, second.data
    assert second.data["counts"]["completed"] == 4
    assert {r["variant"]["id"] for r in second.data["results"] if r.get("resumed")} == set(observed)
    rows = [json.loads(s) for s in journal.read_text(encoding="utf-8").splitlines()]
    terminal = [r for r in rows if r["status"] in {"ok", "infactible"}]
    assert len(terminal) == len({r["variant"]["id"] for r in terminal}) == 4
    assert len(rows) - len(terminal) <= 1  # el ejecutor puede dejar una marca cancelado reintentable
    assert snapshot(ctx) == before


@integration
@pytest.mark.parametrize("mode", ["proyecto", "mantenimiento"])
def test_aplicar_simulado_y_real_verificacion_posterior(case, mode):
    ctx, maintenance, folder = case
    if mode == "mantenimiento":
        ctx.project = str(maintenance)
    path = folder / "solucion.json"
    solve = invoke(ctx, "resolver", "--objetivo", "plazo", "--metodo", "heuristic", "--tiempo", "4", "--mejor-a", path)
    assert solve.exit_code == 0, solve.data
    before = snapshot(ctx)
    simulation = invoke(ctx, "aplicar", "--resultado", path)
    assert simulation.data["simulated"]
    assert snapshot(ctx) == before
    writing = replace(ctx, write=True)
    applied = invoke(writing, "aplicar", "--resultado", path)
    assert applied.exit_code == 0 and applied.data["applied"] and not applied.data["simulated"]
    assert Path(applied.data["backup"]).is_file()
    assert writing.mutation_info.backup == applied.data["backup"]
    checked = invoke(ctx, "verificar")
    assert checked.exit_code == 0, checked.data
    assert checked.data["violation_count"] == 0
    history = invoke(ctx, "corridas", "listar")
    assert history.data["runs"][0]["applied"]
    run_id = history.data["runs"][0]["id"]
    assert invoke(ctx, "corridas", "ver", run_id).data["id"] == run_id
    # Reaplicar una corrida guardada también simula y no pierde su archivo.
    after = snapshot(ctx)
    assert invoke(ctx, "corridas", "aplicar", run_id).data["simulated"]
    assert snapshot(ctx) == after


@integration
def test_cpm_carga_diagnostico_y_verificar_acotado(case):
    ctx, _, _ = case
    before = snapshot(ctx)
    initial = invoke(ctx, "verificar", "--max-violaciones", "2")
    assert initial.exit_code == 2
    assert len(initial.data["violations"]) <= 2
    assert initial.data["violation_count"] > 2 and initial.data["truncated"]
    computed = invoke(ctx, "cpm")
    assert computed.data["simulated"]
    assert computed.data["activities"] and computed.data["critical_path"]
    profile = invoke(ctx, "carga", "--agrupar", "habilidad", "--periodo", "semana")
    assert profile.data["totals"]["load_hours"] > 0
    assert profile.data["peak"]["load_hours"] > 0
    assert sum(r["load_hours"] for r in profile.data["totals_by_series"]) == pytest.approx(profile.data["totals"]["load_hours"])
    diagnostic = invoke(ctx, "diagnosticar", "--periodo", "mes")
    assert "capacity_by_skill" in diagnostic.data
    assert snapshot(ctx) == before


@integration
def test_resolver_infactible_demostrado_y_timeout(case):
    from ganttier import db

    ctx, maintenance, folder = case
    ctx.project = str(maintenance)
    with closing(db.connect(ctx.db_path)) as conn:
        conn.execute("UPDATE resources SET capacity_hours_day=0,max_overtime_hours_day=0 WHERE project_id=?", (maintenance,))
        conn.commit()
    negative = invoke(ctx, "resolver", "--objetivo", "plazo", "--metodo", "heuristic", "--tiempo", "1")
    assert negative.exit_code == 2, negative.data
    assert negative.data["feasible"] is False
    assert negative.data["infeasibility"] in {"demostrada", "no_encontrada"}
    assert negative.data["violations"]
    compared = invoke(ctx, "comparar", "--semillas", "0,1,2", "--metodo", "heuristic", "--tiempo", "1")
    assert compared.exit_code == 2 and compared.data["counts"]["feasible"] == 0
    path = folder / "timeout.json"
    path.write_text(json.dumps({"variants": [{"options": {"method": "heuristic", "time_limit_s": 1}}],
                                "variant_timeout_s": 0.001}), encoding="utf-8")
    timeout = invoke(ctx, "resolver", "--plan", path)
    assert timeout.exit_code == 4 and timeout.data["status"] == "timeout"


@integration
def test_presupuesto_y_reanudar_escenario_cambiado(case):
    from ganttier import db

    ctx, _, folder = case
    plan = plan_file(folder, count=3)
    path = folder / "budget.jsonl"
    budget = invoke(ctx, "lote", "--plan", plan, "--presupuesto", "0.001", "--resultados-a", path)
    assert budget.exit_code == 4 and path.exists()
    completed = invoke(ctx, "lote", "--plan", plan, "--reanudar", "--resultados-a", path)
    assert completed.exit_code == 0
    with closing(db.connect(ctx.db_path)) as conn:
        conn.execute("UPDATE activities SET work_hours=work_hours+1 WHERE project_id=? AND duration_days>0", (int(ctx.project),))
        conn.commit()
    with pytest.raises(CliError) as error:
        invoke(ctx, "lote", "--plan", plan, "--reanudar", "--resultados-a", path)
    assert error.value.exit_code == 3


@integration
def test_flags_y_plan_validacion_antes_de_ejecutar(case, monkeypatch):
    from ganttier.optimizacion import ejecutor

    ctx, _, folder = case
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("No debe ejecutar entradas inválidas.")

    monkeypatch.setattr(ejecutor, "ejecutar_variante", forbidden)
    for value in ("0", "-1", "nan", "inf"):
        with pytest.raises(SystemExit):
            invoke(ctx, "resolver", "--tiempo", value)
    for options in ('{"time_limit_s":0}', '{"unknown":true}'):
        with pytest.raises((CliError, ValueError)):
            invoke(ctx, "resolver", "--opciones", options)
    with pytest.raises(CliError):
        invoke(ctx, "resolver", "--aplicar")
    with pytest.raises(CliError):
        invoke(ctx, "resolver", "--plan", plan_file(folder, count=3))
    with pytest.raises(CliError):
        invoke(ctx, "comparar", "--objetivos", "plazo", "--plan", folder / "plan.json")
    assert not calls


@integration
def test_jsonl_por_id_y_sobre_json(case):
    from ganttier.cli.comandos._programa_opt import read_result

    ctx, maintenance, folder = case
    ctx.project = str(maintenance)
    result = invoke(ctx, "comparar", "--semillas", "0,1,2", "--metodo", "heuristic", "--tiempo", "3",
                    "--conservar-top", "3", "--resultados-a", folder / "results.jsonl")
    identifier = result.data["top"][1]["variant"]["id"]
    selected = read_result(folder / "results.jsonl", identifier)
    assert selected["variant"]["id"] == identifier
    simulated = invoke(ctx, "aplicar", "--resultado", folder / "results.jsonl", "--variante", identifier)
    assert simulated.data["simulated"]
    (folder / "envelope.json").write_text(json.dumps({"ok": True, "data": selected}), encoding="utf-8")
    assert read_result(folder / "envelope.json")["variant"]["id"] == identifier
    with pytest.raises(CliError):
        read_result(folder / "results.jsonl", "inexistente")


def test_ctrl_c_instala_evento_y_restaura_handler(capsys):
    from ganttier.cli.comandos._programa_opt import _progress
    import time

    old = signal.getsignal(signal.SIGINT)
    event = threading.Event()
    state = {"start": time.monotonic(), "done": 1, "reason": None}
    with _progress(event, state, 3):
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert event.is_set() and state["reason"] == "cancelado"
    assert signal.getsignal(signal.SIGINT) == old
    assert capsys.readouterr().out == ""


@integration
def test_cpm_real_rechazado_no_deja_fechas_ni_corridas(case):
    from ganttier import db

    ctx, _, _ = case
    with closing(db.connect(ctx.db_path)) as conn:
        conn.execute("UPDATE resources SET capacity_hours_day=0,max_overtime_hours_day=0 WHERE project_id=?", (int(ctx.project),))
        conn.commit()
    before = snapshot(ctx, ignore_sequence=True)
    result = invoke(replace(ctx, write=True, no_backup=True), "cpm")
    assert result.exit_code == 2
    assert result.data["applied"] is False
    assert snapshot(ctx, ignore_sequence=True) == before


@integration
def test_corridas_verificacion_fallida_revierte_datos_del_programa(case):
    from ganttier import db, services

    ctx, _, _ = case
    with closing(db.connect(ctx.db_path)) as conn:
        activity = conn.execute("SELECT id FROM activities WHERE project_id=? ORDER BY id LIMIT 1", (int(ctx.project),)).fetchone()[0]
        run = services._save_run(conn, int(ctx.project), "optimize", dict(
            starts={str(activity): "2026-01-05"}, finishes={str(activity): "2026-01-05"}), status="factible")
        conn.commit()
    before = snapshot(ctx, ignore_sequence=True)
    with pytest.raises(CliError) as error:
        invoke(replace(ctx, write=True, no_backup=True), "corridas", "aplicar", run)
    assert error.value.exit_code == 2
    assert snapshot(ctx, ignore_sequence=True) == before


@integration
def test_parar_por_mejora_y_plan_desde_proyecto(case):
    ctx, maintenance, folder = case
    plan = plan_file(folder, count=4, project=maintenance)
    ctx.project = None
    result = invoke(ctx, "lote", "--plan", plan, "--parar-si-mejora-menos-de", "2", "--top", "0")
    assert result.exit_code == 4
    assert result.data["summary"]["stop_reason"] == "mejora_insuficiente"
    assert result.data["counts"]["feasible"] == 2
    assert result.data["counts"]["pending"] + result.data["counts"]["retryable"] == 2
    assert result.data["top"] == []


@integration
def test_salida_masiva_acotada_y_flags_efectivos(case, monkeypatch):
    from ganttier.cli.comandos._programa_opt import _variants
    from ganttier.optimizacion import ejecutor, espacio

    ctx, _, folder = case
    parser = argparse.ArgumentParser()
    programa.register(parser.add_subparsers())
    args = parser.parse_args(["programa", "resolver", "--objetivo", "costo", "--pesos", '{"level":2}',
        "--opciones", '{"method":"auto","time_limit_s":5}', "--metodo", "heuristic", "--tiempo", "2.5",
        "--semilla", "7", "--modo-roles", "fractional", "--desde", "2026-01-05", "--hasta", "2026-12-31",
        "--regenerar", "--sin-horas-extra"])
    _, _, variants = _variants(args, ctx)
    weights, options = variants[0].solve_args()
    assert weights["level"] == 2
    assert options["method"] == "heuristic" and options["time_limit_s"] == 2.5
    assert options["seed"] == 7 and options["allow_overtime"] is False
    assert options["regenerate"] is True and options["horizon_end"] == "2026-12-31"
    seen = []

    def finished(db_path, data_dir, pid, variants, **kwargs):
        # Doble sólo para verificar tamaño de salida, sin lanzar 51 procesos.
        seen.append(kwargs["conservar_runs"])
        rows = [dict(variant=v.to_dict(), status="infactible", feasible=False, metrics={},
                     scenario_hash=espacio.hash_escenario(db_path, pid)) for v in variants]
        kwargs["salida_jsonl"].write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return rows

    monkeypatch.setattr(ejecutor, "ejecutar_plan", finished)
    plan = plan_file(folder, count=51)
    result = invoke(ctx, "lote", "--plan", plan, "--top", "2", "--conservar-top", "0")
    assert result.exit_code == 2 and result.data["counts"]["completed"] == 51
    assert "results" not in result.data and len(result.data["top"]) == 2
    assert seen == [0]


@integration
def test_destinos_protegidos_no_sobrescriben_entradas(case):
    ctx, _, folder = case
    before = snapshot(ctx)
    for path in (ctx.db_path, ctx.data_dir / "runs" / "resultado.json"):
        with pytest.raises(CliError):
            invoke(ctx, "resolver", "--mejor-a", path)
    plan = plan_file(folder, count=1)
    original = plan.read_bytes()
    with pytest.raises(CliError):
        invoke(ctx, "resolver", "--plan", plan, "--mejor-a", plan)
    with pytest.raises(CliError):
        invoke(ctx, "comparar", "--semillas", "0,1", "--resultados-a", folder / "igual.jsonl",
               "--mejor-a", folder / "igual.jsonl")
    assert plan.read_bytes() == original and snapshot(ctx) == before


@integration
def test_core_sobre_unico_y_dependencia_faltante(case, capsys, monkeypatch):
    from ganttier.cli.core import main
    from ganttier.optimizacion import ejecutor

    ctx, _, folder = case
    args = ["--db", str(ctx.db_path), "--data-dir", str(ctx.data_dir), "-p", ctx.project]
    qt_before = {name for name in sys.modules if name.startswith("PySide6")}
    code = main([*args, "--salida", str(folder / "sobre.json"), "programa", "carga"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert code == 0 and parsed["ok"] and parsed["db"]["mode"] == "read"
    assert json.loads((folder / "sobre.json").read_text(encoding="utf-8")) == parsed
    # pytest-qt puede cargar Qt antes de ejecutar ningún test; la CLI no debe añadirlo.
    assert {name for name in sys.modules if name.startswith("PySide6")} == qt_before

    def missing(*args, **kwargs):
        return dict(variant=args[3].to_dict(), status="error", feasible=False, warnings=[],
                    error=dict(type="ImportError", code="dependencia_faltante", message="OR-Tools no disponible"))

    monkeypatch.setattr(ejecutor, "ejecutar_variante", missing)
    code = main([*args, "programa", "resolver", "--metodo", "cpsat"])
    parsed = json.loads(capsys.readouterr().out)
    assert code == 6 and not parsed["ok"] and parsed["error"]["code"] == "dependencia_faltante"


@integration
def test_cpm_excepcion_limpia_archivo_antes_de_rollback(case, monkeypatch):
    from ganttier import services

    ctx, _, _ = case
    before = snapshot(ctx, ignore_sequence=True)

    def failed(conn, pid):
        services._save_run(conn, pid, "cpm", {"starts": {}, "finishes": {}}, status="calculado")
        raise ValueError("Fallo tras guardar la corrida")

    monkeypatch.setattr(services, "run_cpm", failed)
    with pytest.raises(ValueError, match="Fallo tras"):
        invoke(replace(ctx, write=True, no_backup=True), "cpm")
    assert snapshot(ctx, ignore_sequence=True) == before
