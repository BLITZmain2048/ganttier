"""Resultados reales de solve, sin depender de los módulos del agente EJE."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from ganttier import db, repo, services, solve
from ganttier.engine import verify
from ganttier.optimizacion import aplicar, espacio


def snapshot(conn):
    return "\n".join(conn.iterdump())


def resultado_en_copia(path, data, pid, *, options=None):
    origin = espacio.hash_escenario(path, pid)
    opts = {"method": "heuristic", "time_limit_s": 2, "seed": 7, **(options or {})}
    with espacio.crear_copia(path, data) as scratch, espacio.redirigir_datos(scratch.data_dir):
        with closing(db.connect(scratch.db_path)) as conn:
            # apply=True permite conservar la corrida si generó ocurrencias;
            # la única base modificada es la copia temporal descartable.
            solved = solve.solve_project(conn, pid, options=opts, apply=True)
            assert solved["feasible"], solved
            row = dict(repo.get(conn, "schedule_runs", solved["run_id"]))
            payload = json.loads(Path(row["result_path"]).read_text(encoding="utf-8"))
            return dict(variant={"id": "esp-v1", "name": "Prueba ESP", "options": opts,
                                 "regenerate": opts.get("regenerate", False)},
                        status="ok", feasible=True, scenario_hash=origin, seed=7,
                        after=solved.get("after"), metrics=solved.get("after"), generation=solved.get("generation"),
                        run={**row, "payload": payload, "scenario_hash": payload["scenario_hash"]})


@pytest.fixture
def demo_result(esp_database):
    conn, path, data = esp_database
    pid = services.demo_project(conn).id
    result = resultado_en_copia(path, data, pid)
    return conn, path, data, pid, result


def test_demo_simulacion_con_gui_y_aplicacion_real(demo_result):
    conn, path, data, pid, result = demo_result
    before = snapshot(conn)
    before_files = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    gui = Path(str(path) + ".lock")
    gui.write_text(f"{os.getpid()}\nGUI\nhost\n", encoding="utf-8")
    simulated = aplicar.aplicar_resultado(path, data, pid, result, escribir=False)
    assert simulated.simulado and not simulated.aplicado and simulated.run_id is None
    assert simulated.metricas_despues["verification"]["feasible"]
    assert snapshot(conn) == before
    assert before_files == {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    with pytest.raises(espacio.BaseOcupada):
        aplicar.aplicar_resultado(path, data, pid, result, escribir=True)
    gui.unlink()
    done = aplicar.aplicar_resultado(path, data, pid, result, escribir=True)
    assert done.aplicado and not done.simulado and done.estrategia == "corrida"
    assert done.run_id and Path(done.respaldo).is_file()
    row = repo.get(conn, "schedule_runs", done.run_id)
    assert row["applied"]
    payload = json.loads(Path(row["result_path"]).read_text(encoding="utf-8"))
    assert payload["scenario_hash"] == result["run"]["scenario_hash"]
    assert payload["cli"]["seed"] == 7 and payload["cli"]["origin"] == "cli"
    assert payload["cli"]["variant_id"] == "esp-v1"
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='cli.apply'").fetchone()[0] == 1


def test_hash_cambiado_rechaza_antes_de_respaldo(demo_result):
    conn, path, data, pid, result = demo_result
    repo.update(conn, "projects", pid, {"name": "Modificado"})
    before = snapshot(conn)
    with pytest.raises(aplicar.EscenarioCambio) as error:
        aplicar.aplicar_resultado(path, data, pid, result, escribir=True)
    assert error.value.code == "escenario_cambio" and error.value.exit_code == 3
    assert snapshot(conn) == before
    assert not (path.parent / "respaldos").exists()
    assert not Path(str(path) + ".cli.lock").exists()


@pytest.mark.parametrize("backup", [True, False])
def test_rollback_ante_verificador_independiente_fallido(demo_result, monkeypatch, backup):
    conn, path, data, pid, result = demo_result
    before = snapshot(conn)
    files = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    called = []
    original_restore = aplicar._restaurar
    def restored(*args):
        called.append(True)
        return original_restore(*args)
    monkeypatch.setattr(aplicar, "_restaurar", restored)
    monkeypatch.setattr(verify, "verify_with_options", lambda *a, **kw:
                        verify.VerificationReport(False, [{"type": "test", "detail": "fallo simulado"}]))
    with pytest.raises(aplicar.VerificacionFallida) as error:
        aplicar.aplicar_resultado(path, data, pid, result, escribir=True, respaldo=backup)
    assert error.value.exit_code == 5 and error.value.detail["restored"] == backup
    assert bool(called) == backup
    assert snapshot(conn) == before
    assert files == {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not Path(str(path) + ".cli.lock").exists()


def test_carga_archivo_lateral(demo_result, tmp_path):
    conn, path, data, pid, result = demo_result
    sidecar = tmp_path / "resultado-run.json"
    sidecar.write_text(json.dumps(result.pop("run")), encoding="utf-8")
    result["run_payload_ref"] = str(sidecar)
    done = aplicar.aplicar_resultado(path, data, pid, result, escribir=True, respaldo=False)
    assert done.aplicado and done.respaldo is None


@pytest.mark.parametrize("generate", [False, True])
def test_demo_mantenimiento_200_y_estrategia_b(esp_database, generate):
    conn, path, data = esp_database
    pid = services.demo_maintenance(200, conn).id
    if generate:
        with repo.transaction(conn):
            conn.execute("DELETE FROM activities WHERE project_id=?", (pid,))
    before = snapshot(conn)
    result = resultado_en_copia(path, data, pid)
    assert snapshot(conn) == before
    done = aplicar.aplicar_resultado(path, data, pid, result, escribir=True)
    assert done.estrategia == ("resolver_de_nuevo" if generate else "corrida")
    assert done.aplicado and done.metricas_despues["verification"]["feasible"]
    assert conn.execute("SELECT COUNT(*) FROM activities WHERE project_id=?", (pid,)).fetchone()[0] == 200


def test_estrategia_b_objetivo_peor_revierte(esp_database):
    conn, path, data = esp_database
    pid = services.demo_maintenance(200, conn).id
    with repo.transaction(conn):
        conn.execute("DELETE FROM activities WHERE project_id=?", (pid,))
    result = resultado_en_copia(path, data, pid)
    result["run"]["metrics_after"]["objective_value"] = -100
    before = snapshot(conn)
    with pytest.raises(aplicar.EscenarioCambio, match="empeoró"):
        aplicar.aplicar_resultado(path, data, pid, result, escribir=True)
    assert snapshot(conn) == before
    assert not list((data / "runs").glob("*.json"))


def test_tolerancia_porcentaje_y_no_finitos():
    original = {"metrics_after": {"objective_value": 100}, "payload": {}}
    changed = {"metrics_after": {"objective_value": 101}, "payload": {}}
    aplicar._comparar_objetivo(original, changed, 1)
    with pytest.raises(aplicar.EscenarioCambio):
        aplicar._comparar_objetivo(original, changed, 0.9)
    changed["metrics_after"]["objective_value"] = float("nan")
    with pytest.raises(ValueError):
        aplicar._comparar_objetivo(original, changed, 100)


def test_roles_indivisibles_audita_asignacion_persistida(esp_database):
    pytest.importorskip("ortools")
    conn, path, data = esp_database
    cid = repo.create(conn, "calendars", {"name": "Bloques ESP", "workweek": "1111111",
                     "hours_per_day": 4, "holidays_country": None}).id
    pid = repo.create(conn, "projects", {"name": "Roles ESP", "calendar_id": cid,
                      "start_date": "2027-01-01", "end_date": "2027-01-04", "data_date": "2027-01-01"}).id
    for name, skills in (("Ana", ["C", "I"]), ("Elena", ["I"])):
        repo.create(conn, "resources", {"project_id": pid, "name": name, "calendar_id": cid,
                    "skills": skills, "capacity_hours_day": 4, "cost_hour": 10})
    aid = repo.create(conn, "activities", {"project_id": pid, "code": "A", "name": "Dos roles",
                      "duration_days": 1, "work_hours": 8, "calendar_id": cid}).id
    for skill in ("C", "I"):
        repo.create(conn, "assignments", {"activity_id": aid, "skill": skill, "units": 1})
    result = resultado_en_copia(path, data, pid, options={"method": "cpsat", "role_mode": "indivisible",
                                "allow_overtime": False, "block_hours": 4, "horizon_blocks": 4, "target_code": "A"})
    assert result["run"]["kind"] == "role_blocks"
    done = aplicar.aplicar_resultado(path, data, pid, result, escribir=True, respaldo=False)
    assert done.aplicado and done.metricas_despues["verification"]["feasible"]
    assert conn.execute("SELECT COUNT(*) FROM role_allocations WHERE project_id=?", (pid,)).fetchone()[0] == 2


def test_equipos_con_ocurrencias_nuevas(esp_database):
    from ganttier import teams
    conn, path, data = esp_database
    cid = repo.list_entities(conn, "calendars")[0].id
    pid = repo.create(conn, "projects", {"name": "Equipos ESP", "mode": "mantenimiento", "calendar_id": cid,
                      "start_date": "2026-01-05", "end_date": "2026-01-30", "data_date": "2026-01-05"}).id
    team = teams.save_team(conn, pid, {"name": "Equipo A", "members": 2, "regime": "5x2", "hours_day": 8})
    repo.create(conn, "periodic_tasks", {"project_id": pid, "code": "L", "name": "Lotes",
                "frequency": "M", "first_due": "2026-01-05", "team_ids": [team["id"]], "unit": "u",
                "quantity_total": 4, "batch_size": 2, "productivity_day_team": 2,
                "tol_after_days": 20, "calendar_id": cid})
    result = resultado_en_copia(path, data, pid, options={"allow_overtime": False})
    assert result["run"]["kind"] == "maintenance_level"
    done = aplicar.aplicar_resultado(path, data, pid, result, escribir=True, respaldo=False)
    assert done.estrategia == "resolver_de_nuevo"
    assert done.metricas_despues["verification"]["feasible"]
    assert conn.execute("SELECT COUNT(*) FROM activities WHERE project_id=?", (pid,)).fetchone()[0] == 2


def test_error_al_guardar_no_deja_corrida(demo_result, monkeypatch):
    conn, path, data, pid, result = demo_result
    before = snapshot(conn)
    def fail(*args, **kwargs):
        raise OSError("disco simulado")
    monkeypatch.setattr(services, "apply_run", fail)
    with pytest.raises(OSError, match="disco simulado"):
        aplicar.aplicar_resultado(path, data, pid, result, escribir=True)
    assert snapshot(conn) == before
    assert not Path(str(path) + ".cli.lock").exists()


def test_mantenimiento_sin_trabajadores_nominales_se_rechaza(esp_database):
    conn, path, data = esp_database
    pid = services.demo_maintenance(5, conn).id
    result = resultado_en_copia(path, data, pid)
    result["run"]["payload"]["worker_assignment"] = {}
    result["run"]["payload"]["crew_assignment"] = {}
    before = snapshot(conn)
    with pytest.raises(aplicar.VerificacionFallida):
        aplicar.aplicar_resultado(path, data, pid, result, escribir=True, respaldo=False)
    assert snapshot(conn) == before


def test_escritor_sqlite_sin_archivo_lock_se_traduce_a_base_ocupada(demo_result, monkeypatch):
    conn, path, data, pid, result = demo_result
    original_connect = db.connect
    def quick_connect(*args, **kwargs):
        opened = original_connect(*args, **kwargs)
        opened.execute("PRAGMA busy_timeout=0")
        return opened
    monkeypatch.setattr(db, "connect", quick_connect)
    before = snapshot(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(espacio.BaseOcupada):
            aplicar.aplicar_resultado(path, data, pid, result, escribir=True)
        assert snapshot(conn) == before
        assert not Path(str(path) + ".cli.lock").exists()
    finally:
        conn.rollback()
