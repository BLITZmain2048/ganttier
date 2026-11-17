"""Integración EJE con ESP real y procesos hijos; máximo dos simultáneos."""
from contextlib import closing
from datetime import date
from hashlib import sha256
import builtins
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import tracemalloc
from types import SimpleNamespace

import pytest

from ganttier import db, repo, services
from ganttier.optimizacion import ejecutor, trabajador
from ganttier.optimizacion.espacio import _pid_vivo
from ganttier.optimizacion.variantes import Variante


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    allowed = Path(__file__).resolve().parents[3] / "trabajos/_temporales/cli_codex_20261019/EJE"
    if os.environ.get("GANTTIER_TESTS_TMP_ESTRICTO") == "1":  # opcional: aislamiento exigido por el encargo original
        assert tmp_path.resolve().is_relative_to(allowed.resolve()), "Use --basetemp dentro de temporales/EJE."
    monkeypatch.setenv("GANTTIER_CLI_TEMP_DIR", str(tmp_path / "copies"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "source-data"))
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "source.db"))
    monkeypatch.setattr(services, "DATA_DIR", tmp_path / "source-data")


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "source.db"
    data = tmp_path / "source-data"
    with closing(db.connect(path)) as conn:
        db.initialize(conn)
        demo = services.demo_project(conn)
        cid = repo.create(conn, "calendars", {"name": "Sin feriados EJE", "holidays_country": None}).id
        maintenance = repo.create(conn, "projects", {"name": "Mantenimiento EJE", "mode": "mantenimiento",
            "start_date": date(2026, 1, 5), "end_date": date(2026, 3, 31), "data_date": date(2026, 1, 5), "calendar_id": cid})
        repo.create(conn, "resources", {"name": "Técnica EJE", "project_id": maintenance.id, "skills": ["electrico"], "calendar_id": cid})
        repo.create(conn, "periodic_tasks", {"project_id": maintenance.id, "code": "M", "name": "Inspección", "frequency": "M",
            "first_due": date(2026, 1, 5), "duration_days": 1, "work_hours": 8, "required_skill": "electrico", "calendar_id": cid})
        conn.commit()
    (data / "runs").mkdir(parents=True, exist_ok=True)
    (data / "runs" / "testigo.json").write_text('{"original":true}', encoding="utf-8")
    return path, data, demo.id, maintenance.id


def _snapshot(path, data):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {name: conn.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
    return {"db_sha256": sha256(path.read_bytes()).hexdigest(), "counts": counts,
            "runs": {str(f.relative_to(data)): sha256(f.read_bytes()).hexdigest() for f in data.rglob("*") if f.is_file()}}


def _variants(n=3):
    return [Variante(objective="plazo", options={"method": "heuristic", "time_limit_s": 3, "seed": i}) for i in range(n)]


def _assert_clean(tmp_path, rows):
    assert not list((tmp_path / "copies").glob(".ganttier-cli-*"))
    assert all(not _pid_vivo(row["worker_pid"]) for row in rows if "worker_pid" in row)


@pytest.mark.parametrize("parallel", [1, 2])
def test_demo_three_variants_isolated(database, tmp_path, parallel):
    path, data, pid, _ = database
    before = _snapshot(path, data)
    output, directory = tmp_path / "results.jsonl", tmp_path / "kept"
    progress = []
    rows = ejecutor.ejecutar_plan(path, data, pid, _variants(), paralelo=parallel, salida_jsonl=output,
                                 directorio_runs=directory, conservar_runs=2,
                                 callback=lambda row, done, total: progress.append((done, total)))
    assert len(rows) == 3 and all(row["status"] == "ok" for row in rows), rows
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert len(list(directory.glob("*.json"))) == 2
    assert all("run" not in row for row in rows)
    assert all(row["parallel"] == parallel and len(row["scenario_hash"]) == 64 for row in rows)
    persisted = list(ejecutor._read_jsonl(output))
    assert len(persisted) == 3
    assert all(not r["run_payload_ref"] or Path(r["run_payload_ref"]).is_file() for r in persisted)
    assert _snapshot(path, data) == before
    _assert_clean(tmp_path, rows)


@pytest.mark.parametrize("parallel", [1, 2])
def test_maintenance_generation_captures_deleted_run(database, tmp_path, parallel):
    path, data, _, pid = database
    before = _snapshot(path, data)
    rows = ejecutor.ejecutar_plan(path, data, pid, _variants(), paralelo=parallel, directorio_runs=tmp_path / "kept")
    assert len(rows) == 3 and all(row["feasible"] for row in rows), rows
    for row in rows:
        run = json.loads(Path(row["run_payload_ref"]).read_text(encoding="utf-8"))
        assert len(run["payload"]["starts"]) == 3
        assert run["payload"]["scenario_hash"] == run["scenario_hash"] == row["run_scenario_hash"]
        assert run["scenario_hash"] != row["scenario_hash"]
        assert row["generation"]["created"] == 3
    assert _snapshot(path, data) == before
    _assert_clean(tmp_path, rows)


def test_hard_timeout_kills_child_and_cleans(database, tmp_path, monkeypatch):
    path, data, pid, _ = database
    before = _snapshot(path, data)
    monkeypatch.setenv("GANTTIER_TRABAJADOR_RETARDO_S", "30")
    started = time.monotonic()
    row = ejecutor.ejecutar_variante(path, data, pid, _variants()[0], timeout_s=.5)
    assert row["status"] == "timeout", row
    assert time.monotonic() - started < 15
    assert _snapshot(path, data) == before
    _assert_clean(tmp_path, [row])


@pytest.mark.parametrize("mode", ["event", "budget", "keyboard"])
def test_cancel_and_budget_cleanup(database, tmp_path, monkeypatch, mode):
    path, data, pid, _ = database
    before = _snapshot(path, data)
    monkeypatch.setenv("GANTTIER_TRABAJADOR_RETARDO_S", "30")
    event = threading.Event()
    kwargs = {"presupuesto_s": .6} if mode == "budget" else {"cancelar": event}
    timer = None
    if mode == "event":
        timer = threading.Timer(.6, event.set)
        timer.start()
    elif mode == "keyboard":
        real_sleep = ejecutor.time.sleep
        interrupted = False
        def interrupt(seconds):
            nonlocal interrupted
            if not interrupted and seconds == .03:
                interrupted = True
                raise KeyboardInterrupt
            real_sleep(seconds)
        monkeypatch.setattr(ejecutor.time, "sleep", interrupt)
    try:
        rows = ejecutor.ejecutar_plan(path, data, pid, _variants(), paralelo=2, salida_jsonl=tmp_path / "partial.jsonl", **kwargs)
    finally:
        if timer:
            timer.join()
    assert rows and all(row["status"] == ("timeout" if mode == "budget" else "cancelado") for row in rows), rows
    assert _snapshot(path, data) == before
    _assert_clean(tmp_path, rows)


def test_resume_skips_finished_and_repairs_partial_tail(database, tmp_path, monkeypatch):
    path, data, _, pid = database
    output = tmp_path / "resume.jsonl"
    initial = ejecutor.ejecutar_plan(path, data, pid, _variants(), paralelo=2, salida_jsonl=output)
    assert all(row["status"] == "ok" for row in initial), initial
    with output.open("ab") as handle:
        handle.write(b'{"variant":')
    def never_start(*args, **kwargs):
        raise AssertionError("La reanudación no debe iniciar hijos terminados.")
    monkeypatch.setattr(ejecutor, "_start", never_start)
    resumed = ejecutor.ejecutar_plan(path, data, pid, _variants(), reanudar_desde=output, salida_jsonl=output)
    assert len(resumed) == 3 and all(row["resumed"] for row in resumed)
    assert len(list(ejecutor._read_jsonl(output))) == 3


def test_resume_retries_timeout(database, tmp_path, monkeypatch):
    path, data, _, pid = database
    output = tmp_path / "retry.jsonl"
    monkeypatch.setenv("GANTTIER_TRABAJADOR_RETARDO_S", "30")
    first = ejecutor.ejecutar_plan(path, data, pid, _variants(1), paralelo=1, timeout_s=.3, salida_jsonl=output)
    assert first[0]["status"] == "timeout"
    monkeypatch.delenv("GANTTIER_TRABAJADOR_RETARDO_S")
    second = ejecutor.ejecutar_plan(path, data, pid, _variants(1), paralelo=1, reanudar_desde=output, salida_jsonl=output)
    assert second[0]["status"] == "ok", second
    assert len(list(ejecutor._read_jsonl(output))) == 2


def test_worker_classifies_valueerror_and_missing_ortools(database, monkeypatch):
    path, data, pid, _ = database
    from ganttier import solve
    def bad(*args, **kwargs):
        raise ValueError("Datos inválidos de prueba")
    monkeypatch.setattr(solve, "solve_project", bad)
    result = trabajador._solve(path, data, pid, _variants()[0])
    assert result["error"]["code"] == "datos_invalidos"
    original_import = builtins.__import__
    def no_ortools(name, *args, **kwargs):
        if name.startswith("ortools"):
            raise ImportError("Falta OR-Tools de prueba")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_ortools)
    result = trabajador._solve(path, data, pid, Variante(options={"method": "cpsat"}))
    assert result["error"]["code"] == "dependencia_faltante"


def test_source_data_error_is_returned(database, tmp_path):
    path, data, pid, _ = database
    before = _snapshot(path, data)
    row = ejecutor.ejecutar_variante(path, data, pid, Variante(options={"method": "heuristic", "role_mode": "indivisible"}))
    assert row["status"] == "error" and row["error"]["code"] == "datos_invalidos", row
    assert _snapshot(path, data) == before
    _assert_clean(tmp_path, [row])


def test_ten_thousand_summaries_bounded_payloads_and_fsync(tmp_path, monkeypatch):
    variants = [Variante(id=f"v{i:05d}", options={"seed": i}) for i in range(10000)]
    live, maximum, synced = 0, 0, 0
    def fake_start(db_path, data_dir, pid, variant, timeout, keep):
        nonlocal live, maximum
        live += 1
        maximum = max(maximum, live)
        return SimpleNamespace(variant=variant, process=SimpleNamespace(poll=lambda: 0))
    def fake_finish(job, parallel, *args, **kwargs):
        nonlocal live
        live -= 1
        row = trabajador._empty_result(job.variant)
        row.update(status="ok", feasible=True, metrics={"makespan_days": int(job.variant.options["seed"])},
                   run={"payload": {"large": "x" * 100000}})
        return row
    def fsync(fd):
        nonlocal synced
        synced += 1
    monkeypatch.setattr(ejecutor, "_start", fake_start)
    monkeypatch.setattr(ejecutor, "_finish", fake_finish)
    monkeypatch.setattr(os, "fsync", fsync)  # Prueba de escala lógica, no benchmark del disco.
    tracemalloc.start()
    rows = ejecutor.ejecutar_plan(tmp_path / "unused.db", tmp_path, 1, variants, paralelo=2,
                                 salida_jsonl=tmp_path / "large.jsonl", directorio_runs=tmp_path / "kept")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(rows) == 10000 and maximum == 2 and live == 0
    assert all("run" not in row for row in rows)
    assert len(list((tmp_path / "kept").glob("*.json"))) == 3
    assert synced >= 10000
    assert peak < 80 * 1024 * 1024, peak
    (tmp_path / "scale.json").write_text(json.dumps({"variants": len(rows), "peak_bytes": peak,
                                                   "max_active": maximum, "fsync_calls": synced}), encoding="utf-8")


def test_output_cannot_overwrite_source(tmp_path):
    source = tmp_path / "source.db"
    source.write_bytes(b"base protegida")
    with pytest.raises(ValueError, match="origen"):
        ejecutor.ejecutar_plan(source, tmp_path / "data", 1, [], salida_jsonl=source)
    assert source.read_bytes() == b"base protegida"


def test_output_cannot_enter_real_runs(tmp_path):
    with pytest.raises(ValueError, match="runs"):
        ejecutor.ejecutar_plan(tmp_path / "source.db", tmp_path / "data", 1, [],
                              directorio_runs=tmp_path / "data" / "runs")


@pytest.mark.parametrize("maintenance", [False, True])
def test_retained_payload_is_applicable_via_esp_simulation(database, tmp_path, maintenance):
    from ganttier.optimizacion.aplicar import aplicar_resultado
    path, data, project_pid, maintenance_pid = database
    pid = maintenance_pid if maintenance else project_pid
    before = _snapshot(path, data)
    result = ejecutor.ejecutar_variante(path, data, pid, _variants()[0], directorio_runs=tmp_path / "kept")
    assert result["status"] == "ok", result
    assert "run" not in result and Path(result["run_payload_ref"]).is_file()
    applied = aplicar_resultado(path, data, pid, result, escribir=False)
    assert applied.simulado
    assert applied.estrategia == ("resolver_de_nuevo" if maintenance else "corrida")
    assert _snapshot(path, data) == before
    _assert_clean(tmp_path, [result])


def test_host_oversubscription_and_environment_parallel(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    row = trabajador._empty_result(Variante(options={"method": "cpsat"}))
    ejecutor._annotate(row, 2, Variante(options={"method": "cpsat"}))
    assert row["cpsat_workers"] == 2 and row["cpu_count"] == 2
    assert any("sobresuscripción" in warning for warning in row["warnings"])
    monkeypatch.setenv("GANTTIER_CLI_PARALELO", "incorrecto")
    with pytest.raises(ValueError, match="GANTTIER_CLI_PARALELO"):
        ejecutor.ejecutar_plan(tmp_path / "unused.db", tmp_path, 1, [])


def test_resume_infeasible_without_launch(tmp_path, monkeypatch):
    variant = _variants()[0]
    row = trabajador._empty_result(variant)
    row.update(status="infactible", infeasibility="demostrada")
    output = tmp_path / "infeasible.jsonl"
    output.write_text(json.dumps(row) + "\n", encoding="utf-8")
    def forbidden(*args, **kwargs):
        pytest.fail("No debe resolver de nuevo un resultado infactible terminado.")
    monkeypatch.setattr(ejecutor, "_start", forbidden)
    result = ejecutor.ejecutar_plan(tmp_path / "unused.db", tmp_path, 1, [variant], reanudar_desde=output)
    assert result[0]["resumed"] and result[0]["status"] == "infactible"


@pytest.mark.parametrize("during", [True, False])
def test_cancel_progress_and_between_variants(tmp_path, monkeypatch, during):
    event = threading.Event()
    notifications = []
    def start(db_path, data_dir, pid, variant, timeout, keep):
        if during:
            event.set()
        return SimpleNamespace(variant=variant, process=SimpleNamespace(poll=lambda: 0))
    def finish(job, parallel, status=None, message=""):
        row = trabajador._empty_result(job.variant)
        row.update(status=status or "ok", feasible=status is None)
        return row
    def callback(row, done, total):
        notifications.append((row["status"], done, total))
        event.set()
    monkeypatch.setattr(ejecutor, "_start", start)
    monkeypatch.setattr(ejecutor, "_finish", finish)
    rows = ejecutor.ejecutar_plan(tmp_path / "unused.db", tmp_path, 1, _variants(), paralelo=1,
                                 cancelar=event, callback=callback, directorio_runs=tmp_path / "kept")
    assert rows[-1]["status"] == "cancelado"
    assert len(notifications) == len(rows)


def test_infeasible_payload_is_not_retained(tmp_path):
    row = trabajador._empty_result(_variants()[0])
    row.update(status="infactible", run={"payload": {"starts": {}}})
    ejecutor._externalize(row, tmp_path / "kept")
    assert "run" not in row and row["run_payload_ref"] is None
    assert not (tmp_path / "kept").exists()
