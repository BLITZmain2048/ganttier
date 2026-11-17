"""Las simulaciones conservan TODAS las filas y los archivos de runs reales."""
import json
import os
from pathlib import Path
import socket
import sqlite3

import pytest


@pytest.mark.parametrize("kind", ["modelo", "cpm", "avance", "api"])
def test_simular_ediciones_no_toca_base_ni_runs(cli, kind):
    before = cli.snapshot()
    if kind == "modelo":
        body = cli.project("actividades", "actualizar", cli.ids["activities"][0], "--json", '{"name":"Simulada"}')
    elif kind == "cpm":
        body = cli.project("programa", "cpm", code=(0, 2))
    elif kind == "avance":
        body = cli.project("control", "avance", "registrar", "--json", json.dumps(dict(codigo="T1", fecha="2027-01-05", porcentaje=25, hh_reales=4)))
    else:
        body = cli.run("api", "POST", "/api/projects", "--json", '{"name":"API simulada","start_date":"2027-01-04"}')
    assert body["db"]["mode"] == "simulated"
    assert cli.snapshot() == before, f"{kind}: simulación alteró base o runs reales"


@pytest.mark.slow
@pytest.mark.parametrize("write_flag", [False, True])
def test_resolver_sin_aplicar_no_toca_base_ni_runs(cli, write_flag):
    before = cli.snapshot()
    flags = ("--escribir",) if write_flag else ()
    body = cli.project(*flags, "programa", "resolver", "--metodo", "heuristic", "--tiempo", "1", "--semilla", "7", timeout=100)
    assert body["data"]["feasible"] is True
    assert cli.snapshot() == before, "PRG/EJE: resolver sin --aplicar dejó efectos persistentes"


@pytest.mark.parametrize("via_env", [False, True])
def test_escribir_modifica_y_respalda_con_integridad(cli, via_env):
    before = cli.snapshot()
    flags = () if via_env else ("--escribir",)
    body = cli.project(*flags, "actividades", "actualizar", cli.ids["activities"][0], "--json", '{"name":"Cambio persistente"}', env={"GANTTIER_CLI_ESCRITURA": "1" if via_env else "0"})
    assert body["db"]["mode"] == "write"
    backup = Path(body["db"]["backup"])
    assert backup.is_file() and backup.is_relative_to(cli.root)
    with sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        name = conn.execute("SELECT name FROM activities WHERE id=?", (cli.ids["activities"][0],)).fetchone()[0]
        assert name != "Cambio persistente", "ESP: el respaldo no es previo"
    assert cli.snapshot()["tables"] != before["tables"]
    with sqlite3.connect(cli.db) as conn:
        assert conn.execute("SELECT name FROM activities WHERE id=?", (cli.ids["activities"][0],)).fetchone()[0] == "Cambio persistente"


def test_bloqueo_gui_lecturas_simulacion_rechazo_y_forzado(cli):
    # PID vigente del propio pytest, sin abrir GUI ni tocar procesos ajenos.
    lock = Path(str(cli.db) + ".lock")
    lock.write_text(f"{os.getpid()}\nGanttier\n{socket.gethostname()}\n", encoding="utf-8")
    try:
        before = cli.snapshot()
        cli.run("estado")
        args = ("actividades", "actualizar", cli.ids["activities"][0], "--json", '{"name":"Con bloqueo"}')
        cli.project(*args)
        body = cli.project("--escribir", *args, code=3)
        assert body["error"]["code"] == "base_ocupada"
        assert cli.snapshot() == before
        body = cli.project("--escribir", "--forzar-bloqueo", *args)
        assert body["warnings"], "ESP/NUC: forzar bloqueo debe quedar advertido"
        assert body["db"]["backup"] and cli.snapshot() != before
    finally:
        lock.unlink(missing_ok=True)


def test_lote_atomico_revierte_todas_las_filas(cli):
    before = cli.snapshot()
    ops = cli.json_file("ops.json", [dict(op="actualizar", entidad="actividades", id=cli.ids["activities"][0], valores={"name": "No debe persistir"}), dict(op="actualizar", entidad="actividades", id=99999999, valores={"name": "Inexistente"})])
    body = cli.project("--escribir", "lote", "aplicar", "--archivo", ops, "--atomico", code=1)
    assert not body["ok"]
    after = cli.snapshot()
    changed = [table for table in before["tables"] if before["tables"][table] != after["tables"].get(table)]
    assert after == before, f"NUC/MOD: lote fallido cambió tablas {changed}; revisar inicialización de Context.mutation y transacción"
