"""Contrato NUC: protocolo, descubrimiento, aislamiento y rutas de sistema."""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types

import pytest

from ganttier.cli import core
from ganttier.cli._base import CliError, MutationInfo, Result
from ganttier.cli.comandos import sistema

ROOT = Path(__file__).resolve().parents[3]
TEMP = ROOT / "trabajos" / "_temporales" / "cli_codex_20261019" / "NUC"


@pytest.fixture
def nuc_space(monkeypatch):
    TEMP.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="case-", dir=TEMP))
    monkeypatch.setenv("GANTTIER_DB", str(folder / "test.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(folder / "datos"))
    monkeypatch.delenv("GANTTIER_CLI_ESCRITURA", raising=False)
    from ganttier import db
    with contextlib.closing(db.connect(folder / "test.db")) as conn:
        db.initialize(conn)
        conn.execute("INSERT INTO projects(name,mode,start_date) VALUES ('Núcleo ñ', 'proyecto', '2026-01-05')")
        conn.commit()
    yield folder
    shutil.rmtree(folder)


@pytest.fixture
def system_only(monkeypatch):
    monkeypatch.setattr(core, "discover_commands", lambda sub, warnings: sistema.register(sub))


def invoke(argv):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = core.main(argv)
    return code, json.loads(output.getvalue()), errors.getvalue(), output.getvalue()


def install_handler(monkeypatch, handler):
    def discover(sub, warnings):
        sub.add_parser("prueba").set_defaults(handler=handler)
    monkeypatch.setattr(core, "discover_commands", discover)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_projects(path):
    with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        return conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]


def test_prints_y_mutacion_en_sobre(monkeypatch, nuc_space):
    def handler(args, ctx):
        print("impresión perdida áéí")
        ctx.mutation_info = MutationInfo(simulated=False, backup="copia.db", locked_by="gui")
        ctx.warn("aviso")
        return Result({"saludo": "año"}, ["aviso"], 2)
    install_handler(monkeypatch, handler)
    code, body, stderr, raw = invoke(["prueba"])
    assert code == 2 and body["ok"] and body["error"] is None
    assert body["db"]["mode"] == "write" and body["db"]["backup"] == "copia.db"
    assert body["db"]["locked_by"] == "gui" and body["warnings"] == ["aviso"]
    assert "impresión perdida" in stderr and "impresión perdida" not in raw


@pytest.mark.parametrize("exception,exit_code,error_code", [
    (ValueError("dato"), 1, "datos_invalidos"), (LookupError("ausente"), 1, "no_encontrado"),
    (ImportError("opcional"), 6, "dependencia_faltante"), (TimeoutError("tiempo"), 4, "timeout"),
    (KeyboardInterrupt(), 4, "timeout"), (RuntimeError("defecto"), 5, "interno"),
    (CliError("escenario_cambio", "cambió", exit_code=3), 3, "escenario_cambio"),
])
def test_excepciones(monkeypatch, nuc_space, exception, exit_code, error_code):
    def handler(args, ctx):
        raise exception
    install_handler(monkeypatch, handler)
    code, body, stderr, _ = invoke(["prueba"])
    assert code == exit_code and body["error"]["code"] == error_code and not body["ok"]
    if exit_code == 5:
        assert "Traceback" in stderr


def test_validacion_fila_campo(monkeypatch, nuc_space):
    from pydantic import BaseModel
    class Row(BaseModel):
        value: int
    class Body(BaseModel):
        rows: list[Row]
    def handler(args, ctx):
        Body.model_validate({"rows": [{"value": "no"}]})
    install_handler(monkeypatch, handler)
    code, body, _, _ = invoke(["prueba"])
    assert code == 1 and body["error"]["detail"]["errors"][0]["row"] == 0
    assert body["error"]["detail"]["errors"][0]["field"] == "rows.0.value"


@pytest.mark.parametrize("argv", [[], ["desconocido"], ["base", "copiar"], ["--db"], ["api", "BAD", "/api/health"]])
def test_uso_json(nuc_space, system_only, argv):
    code, body, _, _ = invoke(argv)
    assert code == 1 and body["error"]["code"] == "uso"


def test_salida_describir_estado(nuc_space, system_only):
    path = nuc_space / "salida.json"
    code, body, _, raw = invoke(["--salida", str(path), "describir", "--comando", "base copiar"])
    assert code == 0 and path.read_text(encoding="utf-8") == raw
    assert body["data"]["commands"][0]["command"] == "base copiar"
    dest = next(a for a in body["data"]["commands"][0]["arguments"] if a["name"] == "destino")
    assert dest["required"] and dest["type"] == "str"
    assert body["data"]["schemas"]["options"]["properties"]["method"]["enum"] == ["auto", "heuristic", "cpsat"]
    code, body, _, _ = invoke(["estado"])
    assert code == 0 and body["db"]["mode"] == "read"
    assert body["data"]["projects"][0]["name"] == "Núcleo ñ"
    code, body, _, raw = invoke(["--salida", str(path), "--desconocido"])
    assert code == 1 and body["error"]["code"] == "uso" and path.read_text(encoding="utf-8") == raw


def test_api_get_y_post_simulado(nuc_space, system_only):
    db = nuc_space / "test.db"
    before, count = fingerprint(db), count_projects(db)
    code, body, _, _ = invoke(["api", "GET", "/api/projects"])
    assert code == 0 and len(body["data"]["body"]) == count
    code, body, _, _ = invoke(["api", "POST", "/api/projects", "--json", '{"name":"Simulado","start_date":"2026-01-05"}'])
    assert code == 0, body
    assert body["db"]["mode"] == "simulated" and body["data"]["status_code"] == 201
    assert fingerprint(db) == before and count_projects(db) == count
    assert not list(nuc_space.glob(".ganttier-cli-*"))


def test_api_post_real_respaldo_y_bloqueo(nuc_space, system_only):
    db = nuc_space / "test.db"
    lock = Path(str(db) + ".lock")
    lock.write_text(f"{os.getpid()}\nGanttier\nlocalhost\n", encoding="utf-8")
    request = ["--escribir", "api", "POST", "/api/projects", "--json", '{"name":"Real","start_date":"2026-01-05"}']
    code, body, _, _ = invoke(request)
    assert code == 3 and body["error"]["code"] == "base_ocupada"
    assert body["db"]["locked_by"] == "gui"
    lock.unlink()
    code, body, _, _ = invoke(request)
    assert code == 0, body
    assert body["db"]["mode"] == "write" and Path(body["db"]["backup"]).is_file()
    assert count_projects(db) == 2


def test_copiar_respaldar_verificar(nuc_space, system_only):
    destination = nuc_space / "copia.db"
    code, body, _, _ = invoke(["base", "copiar", "--destino", str(destination)])
    assert code == 0 and body["data"]["verified"] and count_projects(destination) == 1
    before = fingerprint(destination)
    assert invoke(["base", "copiar", "--destino", str(destination)])[0] == 1
    assert fingerprint(destination) == before
    assert invoke(["base", "respaldar"])[0] == 0
    code, body, _, _ = invoke(["base", "respaldos"])
    assert code == 0 and body["data"]["total"] == 1
    code, body, _, _ = invoke(["base", "verificar"])
    assert code == 0 and body["data"]["verified"] and len(body["data"]["backups"]) == 1


def test_api_binario_y_respaldo_simulado(nuc_space, system_only):
    assert invoke(["base", "respaldar"])[0] == 0
    _, body, _, _ = invoke(["base", "respaldos"])
    name = body["data"]["items"][0]["file"]
    target = nuc_space / "descarga.db"
    code, body, _, _ = invoke(["api", "GET", f"/api/backups/{name}/download", "--binario-a", str(target)])
    assert code == 0 and body["data"]["binary_file"] == str(target)
    assert count_projects(target) == 1
    before = sorted((nuc_space / "respaldos").iterdir())
    code, body, _, _ = invoke(["api", "POST", "/api/backups", "--json", '{"reason":"simular"}'])
    assert code == 0 and body["db"]["mode"] == "simulated"
    assert sorted((nuc_space / "respaldos").iterdir()) == before


def test_api_errores_y_salida_protegida(nuc_space, system_only):
    code, body, _, _ = invoke(["api", "GET", "/api/projects/99999"])
    assert code == 1 and body["error"]["code"] == "no_encontrado"
    code, body, _, _ = invoke(["api", "POST", "/api/projects", "--json", '{"name":""}'])
    assert code == 1 and body["error"]["code"] == "datos_invalidos"
    db = nuc_space / "test.db"
    before = fingerprint(db)
    code, body, _, _ = invoke(["--salida", str(db), "estado"])
    assert code == 1 and body["error"]["code"] == "uso" and fingerprint(db) == before


def test_servidor_real_ciclo_breve(nuc_space):
    # El servidor real anuncia después de enlazar. El cliente local confirma token,
    # rechazo de acceso anónimo y escritura en copia, luego solicita cierre normal.
    source = '''
import asyncio, json, threading, urllib.request, urllib.error
import uvicorn
from ganttier.cli.core import main
original_loop = uvicorn.Server.main_loop
async def brief_loop(self):
    port = self.servers[0].sockets[0].getsockname()[1]
    url = 'http://127.0.0.1:' + str(port)
    def checks():
        with urllib.request.urlopen(url + '/api/health') as response:
            assert json.load(response)['app'] == 'Ganttier'
        try:
            urllib.request.urlopen(url + '/api/projects')
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError('La API no exigió token')
    await asyncio.to_thread(checks)
    self.should_exit = True
uvicorn.Server.main_loop = brief_loop
raise SystemExit(main(['servir']))
'''
    db = nuc_space / "test.db"
    before = fingerprint(db)
    proc = subprocess.run([sys.executable, "-B", "-c", source], cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 4, proc.stderr
    body = json.loads(proc.stdout)
    assert body["ok"] and body["db"]["mode"] == "simulated"
    assert body["data"]["url"].startswith("http://127.0.0.1:") and len(body["data"]["token"]) > 30
    assert fingerprint(db) == before and not list(nuc_space.glob(".ganttier-cli-*"))


def test_descubrimiento_aisla_importacion(monkeypatch, nuc_space):
    original = importlib.import_module
    def importer(name, *args, **kwargs):
        if name.endswith(".nuc_roto"):
            print("ruido al importar")
            raise ImportError("dependencia simulada")
        if name.endswith(".nuc_prueba"):
            return types.SimpleNamespace(register=lambda sub: sub.add_parser("nuc").set_defaults(handler=lambda a, c: Result({"ok": 1})))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(core.pkgutil, "iter_modules", lambda path: [types.SimpleNamespace(name="nuc_roto"), types.SimpleNamespace(name="nuc_prueba")])
    monkeypatch.setattr(core.importlib, "import_module", importer)
    code, body, stderr, raw = invoke(["nuc"])
    assert code == 0 and body["warnings"] and "nuc_roto" in stderr
    assert "ruido al importar" in stderr and "ruido al importar" not in raw


def test_catalogo_conserva_ayuda_alias(monkeypatch, nuc_space):
    def discover(sub, warnings):
        sistema.register(sub)
        group = sub.add_parser("grupo", aliases=["alias"], help="Ayuda del grupo.")
        actions = group.add_subparsers(dest="action")
        actions.add_parser("crear", aliases=["agregar"], help="Ayuda de acción.")
    monkeypatch.setattr(core, "discover_commands", discover)
    code, body, _, _ = invoke(["describir", "--comando", "alias"])
    assert code == 0
    entries = {c["command"]: c for c in body["data"]["commands"]}
    assert entries["alias"]["help"] == "Ayuda del grupo."
    assert entries["alias agregar"]["help"] == "Ayuda de acción."


def test_entorno_antes_del_descubrimiento(monkeypatch, nuc_space):
    monkeypatch.delenv("GANTTIER_DATA_DIR")
    def discover(sub, warnings):
        assert Path(os.environ["GANTTIER_DB"]) == nuc_space / "test.db"
        assert Path(os.environ["GANTTIER_DATA_DIR"]) == nuc_space
        sub.add_parser("env").set_defaults(handler=lambda a, c: Result({"write": c.write}))
    monkeypatch.setattr(core, "discover_commands", discover)
    monkeypatch.setenv("GANTTIER_CLI_ESCRITURA", "1")
    code, body, _, _ = invoke(["--db", str(nuc_space / "test.db"), "env"])
    assert code == 0 and body["data"]["write"]


@pytest.mark.parametrize("command", ["--version", "describir", "estado"])
def test_arranque_subproceso_sin_motor(nuc_space, command):
    source = "from ganttier.cli.core import main; import sys,json; code=main(sys.argv[1:]); print(json.dumps([m for m in sys.modules if m.split('.')[0] in ('numpy','ortools','reportlab','matplotlib','PySide6')]),file=sys.stderr); raise SystemExit(code)"
    proc = subprocess.run([sys.executable, "-B", "-c", source, command], cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"]
    assert json.loads(proc.stderr.splitlines()[-1]) == []
