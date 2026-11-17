"""Rutas aditivas de diagnóstico, comparación y trabajos persistidos en JSONL."""
from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager, closing
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import db, repo, services
from . import schemas
from .dependencies import connection

MAX_VARIANTS = 5000
MAX_PARALLEL = 4
MAX_ACTIVE = 4
TERMINAL = {"completado", "cancelado", "error", "interrumpido", "timeout"}


class StrictModel(BaseModel):
    """Contrato cerrado: se rechazan campos desconocidos y números no finitos."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class VariantRequest(StrictModel):
    """Parámetros de una variante; no acepta rutas ni cargas de corridas."""
    id: str = Field(default="", max_length=150)
    name: str = Field(default="", max_length=300)
    objective: Literal["plazo", "nivelar", "ventanas", "costo", "equilibrado"] | None = None
    weights: schemas.ObjectiveWeights | None = None
    options: schemas.OptimizeOptions = Field(default_factory=schemas.OptimizeOptions)
    horizon_start: date | None = None
    horizon_end: date | None = None
    regenerate: bool = False
    tags: dict[str, str | float | bool | None] = Field(default_factory=dict)


class RandomRequest(StrictModel):
    """Muestreo acotado del espacio de variantes."""
    n: int = Field(ge=1, le=MAX_VARIANTS)
    seed: int = Field(default=0, ge=0)
    space: dict[str, list[Any]]


class PlanRequest(StrictModel):
    """Plan HTTP con límite previo a la expansión cartesiana."""
    project: int | str | None = None
    defaults: VariantRequest | None = None
    variants: list[VariantRequest] | None = Field(default=None, max_length=MAX_VARIANTS)
    grid: dict[str, list[Any]] | None = None
    random: RandomRequest | None = None
    rank_by: str = Field(default="lexicographic", max_length=500)
    parallel: int = Field(default=2, ge=1, le=MAX_PARALLEL)
    variant_timeout_s: float | None = Field(default=None, gt=0, le=86400)
    budget_s: float = Field(default=3600, gt=0, le=86400)

    @model_validator(mode="after")
    def bounded(self) -> "PlanRequest":
        """Rechaza expansión excesiva antes de materializar sus elementos."""
        n = len(self.variants or []) + (self.random.n if self.random else 0)
        if self.grid is not None:
            product = 1
            for values in self.grid.values():
                product *= len(values)
                if product > MAX_VARIANTS:
                    raise ValueError(f"El plan HTTP admite hasta {MAX_VARIANTS} variantes; use la CLI para más.")
            n += product
        if n > MAX_VARIANTS:
            raise ValueError(f"El plan HTTP admite hasta {MAX_VARIANTS} variantes; use la CLI para más.")
        if self.defaults and ({"id", "name"} & self.defaults.model_fields_set):
            raise ValueError("defaults no admite id ni name.")
        return self


class CompareRequest(StrictModel):
    """Comparación aislada; aplicar la mejor es una opción explícita."""
    variants: list[VariantRequest] | None = Field(default=None, max_length=MAX_VARIANTS)
    plan: PlanRequest | None = None
    parallel: int | None = Field(default=None, ge=1, le=MAX_PARALLEL)
    rank_by: str | None = Field(default=None, max_length=500)
    apply_best: bool = False

    @model_validator(mode="after")
    def one_source(self) -> "CompareRequest":
        """Exige una única fuente para las variantes."""
        if (self.variants is None) == (self.plan is None):
            raise ValueError("Indique variants o plan, exactamente uno.")
        return self


class JobRequest(StrictModel):
    """Trabajo independiente de la conexión HTTP que lo crea."""
    kind: Literal["compare", "batch"]
    project_id: int = Field(gt=0)
    plan: PlanRequest


def _components() -> tuple[Any, Any, Any]:
    try:
        executor = importlib.import_module("ganttier.optimizacion.ejecutor")
        variants = importlib.import_module("ganttier.optimizacion.variantes")
        ranking = importlib.import_module("ganttier.optimizacion.ranking")
        getattr(executor, "ejecutar_plan")
        return _execute_http_plan, variants.expandir_plan, ranking.clasificar
    except (ImportError, AttributeError) as exc:
        raise HTTPException(503, "El ejecutor de optimización aún no está disponible; instale o complete los módulos EJE.") from exc


def _application() -> Any:
    try:
        return importlib.import_module("ganttier.optimizacion.aplicar").aplicar_resultado
    except (ImportError, AttributeError) as exc:
        raise HTTPException(503, "La aplicación segura de resultados aún no está disponible; falta el módulo ESP.") from exc


def _plan(body: PlanRequest, pid: int, conn: sqlite3.Connection) -> tuple[dict, list, Any, Any]:
    project = repo.get(conn, "projects", pid)
    if body.project is not None and str(body.project) not in {str(pid), project.name}:
        raise ValueError("El proyecto del plan no coincide con el solicitado.")
    values = body.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    # Materializar límites del servidor aun si el cliente los omitió.
    values.update(parallel=body.parallel, budget_s=body.budget_s, rank_by=body.rank_by)
    execute, expand, rank = _components()
    variants = expand(values)
    if not 1 <= len(variants) <= MAX_VARIANTS:
        raise ValueError(f"El plan debe producir entre 1 y {MAX_VARIANTS} variantes distintas.")
    for variant in variants:
        options = variant.to_dict().get("options", {})
        if options.get("time_limit_s", 30) > 3600:
            raise ValueError("Cada variante HTTP admite hasta 3600 segundos de búsqueda.")
    rank([], body.rank_by)
    return values, variants, execute, rank


def _compact(row: dict) -> dict:
    # Lista positiva: ni run.payload ni estructuras de fechas por actividad llegan a listados.
    keys = ("variant", "status", "feasible", "infeasibility", "metrics", "elapsed_s",
            "scenario_hash", "seed", "run_payload_ref", "error", "rank", "rank_reason", "score",
            "cpu_count", "parallel", "cpsat_workers", "host_load", "run_retained")
    value = {key: row[key] for key in keys if key in row}
    if "warnings" in row:
        value["warnings"] = (row["warnings"] or [])[:20]
        value["warnings_total"] = row.get("warnings_total", len(row["warnings"] or []))
    if row.get("verification"):
        value["verification"] = dict(feasible=row["verification"].get("feasible"),
                                     violations_count=row["verification"].get("violations_count",
                                         len(row["verification"].get("violations") or [])))
    if row.get("run"):
        value["run_ref"] = {k: row["run"].get(k) for k in ("id", "kind", "scenario_hash")}
    return jsonable_encoder(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execute_http_plan(db_path: Path, data_dir: Path, pid: int, variants: list, **kwargs: Any) -> list[dict]:
    """Aísla variables de entorno de EJE en un coordinador hijo por trabajo.

    El hilo HTTP conserva el registro y el Event. El hijo recibe su propio TEMP,
    GANTTIER_CLI_TEMP_DIR y DATA_DIR, todos bajo jobs/<id>, sin mutar os.environ
    del servidor. Cerrar stdin solicita cancelación, incluso si muere el servidor.
    """
    directory = Path(data_dir).resolve()
    request = directory / "request.json"
    values = {k: v for k, v in kwargs.items() if k not in {"callback", "cancelar"}}
    request.write_text(json.dumps(jsonable_encoder(dict(db_path=str(db_path), data_dir=str(directory),
        pid=pid, variants=[v.to_dict() for v in variants], kwargs=values)), ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    env.update(GANTTIER_DB=str(db_path), GANTTIER_DATA_DIR=str(directory),
               GANTTIER_CLI_TEMP_DIR=str(directory / "temporales"), TEMP=str(directory), TMP=str(directory),
               PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    backend = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = backend + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    events_path, final_path = directory / "events.jsonl", directory / "result.json"
    events_path.touch()
    process = None
    try:
        with (directory / "worker.log").open("w", encoding="utf-8") as log, events_path.open(encoding="utf-8") as events:
            process = subprocess.Popen([sys.executable, "-c",
                "from ganttier.api.optimizacion import _http_worker; _http_worker()", str(request)],
                cwd=directory, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            while True:
                position = events.tell()
                line = events.readline()
                if line:
                    if not line.endswith("\n"):
                        events.seek(position)
                    else:
                        value = json.loads(line)
                        kwargs["callback"](value["result"], value["done"], value["total"])
                        continue
                if kwargs["cancelar"].is_set() and process.stdin and not process.stdin.closed:
                    process.stdin.close()
                if process.poll() is not None:
                    break
                try:
                    process.wait(timeout=.05)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode != 0 or not final_path.exists():
                raise RuntimeError("El coordinador de optimización terminó con error; consulte worker.log del trabajo.")
            value = json.loads(final_path.read_text(encoding="utf-8"))
            if value.get("error"):
                raise RuntimeError(value["error"])
            return value["results"]
    finally:
        if process:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _http_worker() -> None:
    """Entrada interna del coordinador, sin rutas provenientes del cliente HTTP."""
    from ..optimizacion.ejecutor import ejecutar_plan
    from ..optimizacion.variantes import Variante
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    directory = Path(request["data_dir"])
    event = threading.Event()

    def disconnected() -> None:
        os.read(sys.stdin.fileno(), 1)
        event.set()

    threading.Thread(target=disconnected, daemon=True).start()

    def callback(result: dict, done: int, total: int) -> None:
        with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(result=_compact(result), done=done, total=total), ensure_ascii=False) + "\n")
            stream.flush()

    try:
        results = ejecutar_plan(request["db_path"], directory, request["pid"],
            [Variante.from_dict(v) for v in request["variants"]], callback=callback,
            cancelar=event, **request["kwargs"])
        final = dict(results=results)
    except Exception as exc:
        final = dict(error=f"No se pudo ejecutar el plan: {exc}")
    finally:
        # También limpia si el servidor desapareció y la tubería activó cancelación.
        for suffix in ("", "-wal", "-shm"):
            Path(str(request["db_path"]) + suffix).unlink(missing_ok=True)
    (directory / "result.json").write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")


class JobRegistry:
    """Registro por carpeta de datos, con diario durable y recuperación tras reinicio."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, threading.Event] = {}
        self.threads: dict[str, threading.Thread] = {}
        self._recover()

    def _append(self, job_id: str, event: str, data: dict) -> None:
        with (self.root / f"{job_id}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(event=event, data=jsonable_encoder(data)), ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _state(self, job: dict) -> None:
        self._append(job["job_id"], "state", {k: v for k, v in job.items() if k != "results"})

    def _recover(self) -> None:
        for path in self.root.glob("*.jsonl"):
            if len(path.stem) != 32 or any(c not in "0123456789abcdef" for c in path.stem):
                continue
            state, results = None, []
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                        if event["event"] == "state":
                            state = event["data"]
                        elif event["event"] == "result":
                            results.append(event["data"])
                        elif event["event"] == "results_final":
                            results = event["data"]["results"]
                    except (ValueError, KeyError, TypeError):
                        continue  # Una última línea truncada no pierde las anteriores.
            if not state:
                continue
            state.update(job_id=path.stem, results=results)
            if state["status"] not in TERMINAL:
                state.update(status="interrumpido", finished_at=_now(),
                             error="El servidor se reinició antes de completar el trabajo.")
                self._state(state)
                self._cleanup_source(path.stem, recovering=True)
            self.jobs[path.stem] = state

    def _cleanup_source(self, job_id: str, *, recovering: bool = False) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                (self.root / job_id / ("source.sqlite" + suffix)).unlink(missing_ok=True)
            except PermissionError:
                # El coordinador anterior podría seguir cerrando SQLite tras EOF.
                if not recovering:
                    raise

    def create(self, pid: int, kind: str, plan: dict, variants: list, execute: Any,
               rank: Any, conn: sqlite3.Connection, *, asynchronous: bool) -> dict:
        """Reserva cupo y copia SQLite consistente; solo el hilo posee su ejecución."""
        with self.lock:
            if sum(j["status"] not in TERMINAL for j in self.jobs.values()) >= MAX_ACTIVE:
                raise HTTPException(429, "Hay demasiados trabajos activos; espere o cancele uno.")
            job_id = uuid4().hex
            directory = self.root / job_id
            directory.mkdir()
            with closing(sqlite3.connect(directory / "source.sqlite")) as target:
                conn.backup(target)
            job = dict(job_id=job_id, kind=kind, project_id=pid, status="pendiente",
                       progress=dict(done=0, total=len(variants)), created_at=_now(),
                       rank_by=plan["rank_by"], results=[], error=None,
                       results_file=str(directory / "results.jsonl"))
            self.jobs[job_id] = job
            self.events[job_id] = threading.Event()
            self._state(job)
        if asynchronous:
            thread = threading.Thread(target=self._run, args=(job_id, plan, variants, execute, rank),
                                      name=f"ganttier-job-{job_id}", daemon=True)
            self.threads[job_id] = thread
            thread.start()
        return job

    def _run(self, job_id: str, plan: dict, variants: list, execute: Any, rank: Any) -> list[dict]:
        directory = self.root / job_id
        job = self.jobs[job_id]
        event = self.events[job_id]
        try:
            with self.lock:
                job.update(status="ejecutando", started_at=_now())
                self._state(job)

            def callback(result: dict, done: int, total: int) -> None:
                compact = _compact(result)
                with self.lock:
                    job["results"].append(compact)
                    job["progress"] = dict(done=done, total=total)
                    self._append(job_id, "result", compact)
                    self._state(job)

            results = execute(directory / "source.sqlite", directory, job["project_id"], variants,
                              paralelo=plan["parallel"], timeout_s=plan.get("variant_timeout_s"),
                              presupuesto_s=plan["budget_s"], salida_jsonl=directory / "results.jsonl",
                              callback=callback, cancelar=event,
                              directorio_runs=directory / "result_runs",
                              conservar_runs=3 if plan["rank_by"] == "lexicographic" else None)
            ordered = rank(results, plan["rank_by"])
            with self.lock:
                # Algunos ejecutores pueden devolver resultados sin invocar callback.
                seen = {(r.get("variant") or {}).get("id") for r in job["results"]}
                for result in ordered:
                    if (result.get("variant") or {}).get("id") not in seen:
                        compact = _compact(result)
                        job["results"].append(compact)
                        self._append(job_id, "result", compact)
                job["results"] = [_compact(row) for row in ordered]
                self._append(job_id, "results_final", dict(results=job["results"]))
                job["progress"]["done"] = len(job["results"])
                job["status"] = ("cancelado" if event.is_set() else
                                 "timeout" if len(results) < len(variants) else "completado")
            return ordered
        except Exception as exc:
            with self.lock:
                job.update(status="cancelado" if event.is_set() else "error",
                           error=f"No se pudo completar el trabajo: {exc}")
            return []
        finally:
            self._cleanup_source(job_id)
            with self.lock:
                job["finished_at"] = _now()
                self._state(job)

    def view(self, job_id: str, *, offset: int = 0, limit: int = 50, top: int = 10) -> dict:
        """Entrega resultados resumidos y paginados; nunca cargas completas de corrida."""
        with self.lock:
            if job_id not in self.jobs:
                raise LookupError("No existe el trabajo solicitado.")
            job = self.jobs[job_id]
            value = {k: v for k, v in job.items() if k != "results"}
            rows = list(job["results"])
            try:
                rank = importlib.import_module("ganttier.optimizacion.ranking").clasificar
                ordered = rank(rows, job["rank_by"])
            except ImportError:
                ordered = rows
            value.update(results=rows[offset:offset+limit], top=ordered[:top],
                         total_results=len(rows), counts=dict(Counter(r.get("status", "error") for r in rows)))
            return jsonable_encoder(value)


_registries: dict[Path, JobRegistry] = {}
_registry_lock = threading.Lock()


def _registry() -> JobRegistry:
    root = (Path(services.DATA_DIR) / "jobs").resolve()
    with _registry_lock:
        if root not in _registries:
            _registries[root] = JobRegistry(root)
        return _registries[root]


@asynccontextmanager
async def _lifespan(app: Any):
    _registry()
    yield


router = APIRouter(tags=["Optimización y trabajos"], lifespan=_lifespan)


@router.get("/capabilities", summary="Consultar capacidades de optimización",
            description="Versiones, objetivos disponibles y límites HTTP; usa la misma credencial de sesión.")
def capabilities() -> dict:
    """Describe capacidades sin importar el ejecutor ni iniciar un cálculo."""
    try:
        presets = importlib.import_module("ganttier.optimizacion.variantes").PRESETS
    except ImportError:
        presets = {}
    return dict(version="1.0.0", cli_version="1.0", schema=db.SCHEMA_VERSION,
                ortools=importlib.util.find_spec("ortools") is not None, cpu_count=os.cpu_count(),
                limits=dict(max_variants=MAX_VARIANTS, max_parallel=MAX_PARALLEL,
                            max_active_jobs=MAX_ACTIVE, max_budget_s=86400),
                objectives=list(presets), presets=presets)


@router.get("/projects/{pid}/schedule/diagnostics", summary="Diagnosticar capacidad y restricciones",
            description="Diagnóstico de solo lectura, con calendarios, ausencias, intensidades y ventanas duras.")
def diagnostics(pid: int, desde: date | None = None, hasta: date | None = None,
                periodo: Literal["dia", "semana", "mes"] = "mes",
                conn: sqlite3.Connection = Depends(connection)) -> dict:
    """Consulta el diagnóstico compartido con la CLI sin persistir cambios."""
    from ..optimizacion.diagnostico import diagnosticar
    return diagnosticar(conn, pid, desde=desde, hasta=hasta, periodo=periodo)


@router.post("/projects/{pid}/schedule/compare", summary="Comparar variantes aisladas",
             description="Ejecuta variantes en copias; apply_best solicita aplicar la mejor con respaldo y verificación.")
def compare(pid: int, body: CompareRequest, conn: sqlite3.Connection = Depends(connection)) -> dict:
    """Compara sin modificar la base salvo aplicación explícita y verificada."""
    model = body.plan or PlanRequest(variants=body.variants)
    values = model.model_dump(exclude_unset=True, exclude_none=True)
    if body.parallel is not None:
        values["parallel"] = body.parallel
    if body.rank_by is not None:
        values["rank_by"] = body.rank_by
    plan, variants, execute, rank = _plan(PlanRequest.model_validate(values), pid, conn)
    apply = _application() if body.apply_best else None
    registry = _registry()
    job = registry.create(pid, "compare", plan, variants, execute, rank, conn, asynchronous=False)
    results = registry._run(job["job_id"], plan, variants, execute, rank)
    result = registry.view(job["job_id"])
    result["applied"] = False
    if apply and results and results[0].get("feasible") and results[0].get("status") == "ok":
        path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
        if not path:
            raise ValueError("La aplicación segura requiere una base SQLite en disco.")
        try:
            applied = apply(Path(path), services.DATA_DIR, pid, results[0], escribir=True)
        except RuntimeError as exc:
            # ESP expresa conflictos de escenario/bloqueo con el código compartido.
            code = getattr(exc, "exit_code", None)
            if code not in {2, 3, 5}:
                raise
            raise HTTPException({2: 422, 3: 409, 5: 500}[code], str(exc)) from exc
        result["application"] = jsonable_encoder(asdict(applied) if is_dataclass(applied) else applied)
        result["applied"] = result["application"].get("aplicado", False)
    return result


@router.post("/jobs", status_code=202, summary="Crear trabajo asíncrono",
             description="Crea un hilo independiente del cliente y un diario JSONL dentro de DATA_DIR/jobs.")
def create_job(body: JobRequest, conn: sqlite3.Connection = Depends(connection)) -> dict:
    """Acepta un plan validado y devuelve su identificador generado por el servidor."""
    plan, variants, execute, rank = _plan(body.plan, body.project_id, conn)
    job = _registry().create(body.project_id, body.kind, plan, variants, execute, rank, conn, asynchronous=True)
    return dict(job_id=job["job_id"])


@router.get("/jobs", summary="Listar trabajos", description="Lista estados y contadores paginados, sin cargas de corrida.")
def list_jobs(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict:
    """Lista únicamente metadatos y métricas resumidas de los trabajos."""
    registry = _registry()
    with registry.lock:
        ids = list(reversed(registry.jobs))
        jobs = [registry.view(i, limit=0, top=1) for i in ids[offset:offset+limit]]
    return dict(total=len(ids), jobs=jobs)


@router.get("/jobs/{job_id}", summary="Consultar progreso y resultados",
            description="Estado, progreso done/total, resultados parciales paginados y mejores variantes.")
def get_job(job_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
            top: int = Query(10, ge=1, le=50)) -> dict:
    """Consulta resultados compactos sin aceptar rutas del cliente."""
    return _registry().view(job_id, offset=offset, limit=limit, top=top)


@router.post("/jobs/{job_id}/cancel", summary="Cancelar un trabajo",
             description="Activa la cancelación cooperativa del ejecutor y conserva resultados parciales.")
def cancel_job(job_id: str) -> dict:
    """Solicita cancelación idempotente; el ejecutor limpia sus procesos y copias."""
    registry = _registry()
    with registry.lock:
        if job_id not in registry.jobs:
            raise LookupError("No existe el trabajo solicitado.")
        job = registry.jobs[job_id]
        if job["status"] not in TERMINAL:
            registry.events[job_id].set()
            job["status"] = "cancelando"
            registry._state(job)
        return dict(job_id=job_id, status=job["status"])
