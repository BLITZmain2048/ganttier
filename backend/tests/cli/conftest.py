"""E2E por procesos reales; datos y evidencia confinados al espacio de TST."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
TEMP_ROOT = ROOT / "trabajos/_temporales/cli_codex_20261019/TST"

# Preparación independiente de la CLI: una avería en demo crear no oculta los
# demás comandos. El proceso recibe las rutas ANTES de importar services.
SEED = r'''
import json, os
from datetime import date
from pathlib import Path
from ganttier import db, repo, services, teams
conn = db.connect(os.environ["GANTTIER_DB"])
db.initialize(conn)
demo = services.demo_project(conn)
small_demo = services.demo_maintenance(8, conn)
cal = repo.create(conn, "calendars", dict(name="TST continuo", workweek="1111111", hours_per_day=8, holidays_country=None))
project = repo.create(conn, "projects", dict(name="TST proyecto", mode="proyecto", start_date=date(2027,1,4), data_date=date(2027,1,4), calendar_id=cal.id))
resource = repo.create(conn, "resources", dict(project_id=project.id, name="TST electricista", kind="trabajador", skills=["electrico"], calendar_id=cal.id, capacity_hours_day=8, max_units=1))
acts, assignments = [], []
for i in range(3):
    a = repo.create(conn, "activities", dict(project_id=project.id, code=f"T{i+1}", name=f"Tarea {i+1}", duration_days=2, work_hours=16, required_skill="electrico", crew_size=1, calendar_id=cal.id))
    assignments.append(repo.create(conn, "assignments", dict(activity_id=a.id, resource_id=resource.id, skill="electrico", units=1, work_hours=16)).id)
    if acts:
        repo.create(conn, "relations", dict(project_id=project.id, pred_id=acts[-1], succ_id=a.id, type="FS"))
    acts.append(a.id)
mt = repo.create(conn, "projects", dict(name="TST equipos", mode="mantenimiento", start_date=date(2027,1,1), end_date=date(2027,1,31), data_date=date(2027,1,1), calendar_id=cal.id))
team_ids = [teams.save_team(conn, mt.id, dict(name=f"Equipo {letter}", regime="5x2", cycle_start=date(2027,1,1), members=2, hours_day=8))["id"] for letter in "AB"]
repo.create(conn, "periodic_tasks", dict(project_id=mt.id, code="PANEL", name="Limpieza", first_due=date(2027,1,4), frequency="M", quantity_total=400, unit="paneles", batch_size=100, productivity_day_team=100, team_ids=team_ids, batch_continuous_in_shift=False, tol_after_days=25))
conn.commit()
conn.close()
Path(os.environ["GANTTIER_DATA_DIR"], "runs").mkdir(parents=True, exist_ok=True)
Path(os.environ["GANTTIER_DATA_DIR"], "runs", "tst_sentinel.txt").write_text("No modificar: áéíóú", encoding="utf-8")
print(json.dumps(dict(project=project.id, demo=demo.id, maintenance=mt.id, maintenance_demo=small_demo.id, activities=acts, assignments=assignments, teams=team_ids, entities=list(repo.ENTITIES))))
'''


def logical_snapshot(path: Path) -> dict:
    """Conteos y checksum de filas ordenadas, incluidos meta y sqlite_sequence."""
    out = {}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = [json.dumps(list(row), ensure_ascii=False, separators=(",", ":"),
                               default=lambda x: {"blob": x.hex()})
                    for row in conn.execute(f"SELECT * FROM {quoted}")]
            out[name] = {"count": len(rows), "sha256": hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()}
    return out


def file_snapshot(directory: Path) -> dict:
    """Incluye nombres relativos y contenido, sin depender de mtimes de Windows."""
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


class CliSandbox:
    def __init__(self, root, env, ids):
        self.root, self.env, self.ids = root, env, ids
        self.db = Path(env["GANTTIER_DB"])
        self.data_dir = Path(env["GANTTIER_DATA_DIR"])
        self.calls = []

    def json_file(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def snapshot(self):
        return {"tables": logical_snapshot(self.db), "runs": file_snapshot(self.data_dir / "runs")}

    def run(self, *args, code=0, timeout=90, stdin=None, env=None, implemented=True):
        # La implementación publica --mejor-a también en resolver. Fijarlo evita
        # el destino predeterminado backend/programa_resultados fuera del sandbox.
        if "resolver" in args and "programa" in args and "--mejor-a" not in args:
            args = (*args, "--mejor-a", self.root / f"resolver_{len(self.calls)}.json")
        if "comparar" in args and "programa" in args and "--resultados-a" not in args:
            args = (*args, "--resultados-a", self.root / f"comparar_{len(self.calls)}.jsonl")
        command = [sys.executable, "-m", "ganttier.cli", *map(str, args)]
        started = time.perf_counter()
        try:
            proc = subprocess.run(command, cwd=BACKEND, env={**self.env, **(env or {})},
                                  input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired:
            pytest.fail(f"{args}: excedió el límite externo de {timeout}s; revisar NUC/PRG/EJE", pytrace=False)
        elapsed = time.perf_counter() - started
        record = dict(argv=command, cwd=str(BACKEND), returncode=proc.returncode,
                      wall_s=elapsed, stdout=proc.stdout, stderr=proc.stderr)
        self.calls.append(record)
        with (self.root / "commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        combined = proc.stdout + proc.stderr
        if implemented and any(s in combined for s in ("No module named 'ganttier.cli", "No module named 'ganttier.optimizacion", "invalid choice:", "elección no válida", "no implementado")):
            pytest.fail(f"comando no implementado todavía: {' '.join(map(str, args))}\n{combined[:1500]}", pytrace=False)
        try:
            body = json.loads(proc.stdout, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
        except (ValueError, UnicodeError):
            pytest.fail(f"NUC: stdout debe ser un único JSON UTF-8 sin BOM: {args}\nstdout={proc.stdout[:1200]!r}\nstderr={proc.stderr[:1200]}", pytrace=False)
        assert isinstance(body, dict), "NUC: el sobre debe ser un objeto JSON"
        required = {"ok", "command", "cli_version", "ganttier", "db", "data", "warnings", "error", "elapsed_s"}
        assert required <= body.keys(), f"NUC: faltan claves del sobre: {required - body.keys()}"
        assert isinstance(body["ok"], bool) and isinstance(body["command"], str)
        assert body["cli_version"] == "1.0"
        assert {"version", "schema"} <= body["ganttier"].keys()
        assert {"path", "data_dir", "mode", "backup", "locked_by"} <= body["db"].keys()
        assert body["db"]["mode"] in {"read", "write", "simulated"}
        assert isinstance(body["warnings"], list)
        assert isinstance(body["elapsed_s"], (int, float)) and body["elapsed_s"] >= 0
        assert body["ok"] == (body["error"] is None)
        if body["error"] is not None:
            assert {"code", "message", "detail", "hint"} <= body["error"].keys()
        assert proc.returncode in range(7), f"NUC: código fuera del contrato: {proc.returncode}"
        expected = (code,) if isinstance(code, int) else tuple(code)
        assert proc.returncode in expected, f"{' '.join(map(str, args))}: esperado exit {expected}, llegó {proc.returncode}: {json.dumps(body, ensure_ascii=False)[:2500]}\nstderr={proc.stderr[:1200]}"
        return body

    def project(self, *args, maintenance=False, **kwargs):
        return self.run("-p", self.ids["maintenance" if maintenance else "project"], *args, **kwargs)


@pytest.fixture(scope="session")
def cli_seed():
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="session_", dir=TEMP_ROOT))
    env = os.environ.copy()
    env.update(GANTTIER_DB=str(root / "seed.db"), GANTTIER_DATA_DIR=str(root / "seed_data"),
               GANTTIER_CLI_ESCRITURA="0", GANTTIER_CLI_PARALELO="2", PYTHONUTF8="1",
               PYTHONDONTWRITEBYTECODE="1", TEMP=str(root), TMP=str(root), TMPDIR=str(root),
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    env.pop("GANTTIER_TRABAJADOR_RETARDO_S", None)
    proc = subprocess.run([sys.executable, "-c", SEED], cwd=BACKEND, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=90)
    if proc.returncode != 0:
        pytest.fail(f"Preparación TST falló (no es un fallo de comando): {proc.stderr}", pytrace=False)
    ids = json.loads(proc.stdout)
    (root / "seed_ids.json").write_text(proc.stdout, encoding="utf-8")
    return root, env, ids


@pytest.fixture
def cli(cli_seed, request):
    import shutil
    root, source_env, ids = cli_seed
    case = Path(tempfile.mkdtemp(prefix=request.node.name[:55] + "_", dir=root))
    env = {**source_env, "GANTTIER_DB": str(case / "test.db"), "GANTTIER_DATA_DIR": str(case / "data")}
    with sqlite3.connect(source_env["GANTTIER_DB"]) as source, sqlite3.connect(env["GANTTIER_DB"]) as target:
        source.backup(target)
    shutil.copytree(source_env["GANTTIER_DATA_DIR"], env["GANTTIER_DATA_DIR"])
    sandbox = CliSandbox(case, env, ids)
    yield sandbox
    (case / "final_snapshot.json").write_text(json.dumps(sandbox.snapshot(), indent=2), encoding="utf-8")
