"""Supervisor de procesos: copias aisladas, límites duros y resultados durables.

La interfaz exige devolver list[dict]: se retienen O(N) resúmenes, nunca N cargas
útiles. Sólo existen `paralelo` procesos y K+1 archivos de corrida durante la
poda (K al terminar). Los JSONL son diarios incrementales; al cerrar se concilian
las referencias podadas mediante reescritura en streaming y reemplazo atómico.
GANTTIER_CLI_TEMP_DIR permite elegir la raíz de copias; por defecto se usa una
subcarpeta del directorio actual, no el temporal global del sistema.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator
from uuid import uuid4

from .ranking import _lex_key
from .trabajador import _empty_result, _write_json
from .variantes import Variante


@dataclass
class _Job:
    variant: Variante
    scratch: Any
    process: subprocess.Popen
    output: Path
    log: Path
    started: float
    limit: float
    scenario_hash: str
    schema: int


def _positive(value: float | None, name: str) -> float | None:
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} debe ser positivo y finito.")
    return value


def _protect_destination(path: Path, db_path: Path, data_dir: Path | None) -> None:
    target, source = path.resolve(), db_path.resolve()
    protected = {source, *(Path(str(source) + suffix) for suffix in ("-wal", "-shm", ".lock", ".cli.lock"))}
    if target in protected or (target.is_file() and source.is_file() and target.samefile(source)):
        raise ValueError("La salida no puede sobrescribir la base de origen ni sus archivos auxiliares.")
    real_runs = ((data_dir or source.parent) / "runs").resolve()
    if target == real_runs or target.is_relative_to(real_runs):
        raise ValueError("La salida no puede escribir dentro de runs/ del origen; elija otra carpeta.")


def _host(parallel: int, variant: Variante) -> dict:
    cores = os.cpu_count() or 1
    options = variant.options
    workers = (1 if options.get("role_mode") == "indivisible" else min(8, cores)) if options.get("method", "auto") != "heuristic" else 0
    try:
        load = list(os.getloadavg())
    except (AttributeError, OSError):
        load = None
    return {"cpu_count": cores, "parallel": parallel, "cpsat_workers": workers, "host_load": load}


def _annotate(row: dict, parallel: int, variant: Variante) -> None:
    host = _host(parallel, variant)
    row.update(host)
    if parallel * host["cpsat_workers"] > host["cpu_count"]:
        row["warnings"].append(f"Posible sobresuscripción: {parallel} procesos × {host['cpsat_workers']} hilos CP-SAT exceden {host['cpu_count']} núcleos; los tiempos dependen de la carga del host.")


def _failure(variant: Variante, status: str, message: str, *, code: str | None = None,
             elapsed: float = 0.0, error_type: str | None = None) -> dict:
    result = _empty_result(variant)
    result.update(status=status, elapsed_s=elapsed, error={"type": error_type or status,
                  "code": code or ("timeout" if status in {"timeout", "cancelado"} else "interno"), "message": message})
    return result


def _terminate(process: subprocess.Popen) -> None:
    if os.name == "nt":
        if process.poll() is None:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15, creationflags=flags, check=False)
            finally:
                if process.poll() is None:
                    process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=15)


def _start(db_path: Path, data_dir: Path | None, pid: int, variant: Variante,
           timeout: float | None, keep: bool) -> _Job:
    from .espacio import crear_copia  # ESP puede cargarse después que este módulo.
    from .. import services
    timeout = _positive(timeout, "timeout_s")
    weights, options = variant.solve_args()
    limit = timeout if timeout is not None else options.get("time_limit_s", 30.0) * 1.5 + 30
    root = Path(os.environ.get("GANTTIER_CLI_TEMP_DIR", str(Path.cwd() / ".ganttier-cli-temporales"))).resolve()
    _protect_destination(root, db_path, data_dir)
    root.mkdir(parents=True, exist_ok=True)
    scratch = crear_copia(db_path, data_dir, raiz=root, conservar=keep)
    process = None
    try:
        with closing(sqlite3.connect(scratch.db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            scenario = services._scenario_fingerprint(conn, pid)
            schema = int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        request, output, log = scratch.root / "variante.json", scratch.root / "resultado.json", scratch.root / "trabajador.log"
        _write_json(request, variant.to_dict())
        env = os.environ.copy()
        env.update(GANTTIER_DB=str(scratch.db_path.resolve()), GANTTIER_DATA_DIR=str(scratch.data_dir.resolve()),
                   PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", TMP=str(scratch.root), TEMP=str(scratch.root), TMPDIR=str(scratch.root))
        # El mismo paquete que importó el supervisor, también al usar API fuera de backend/.
        backend = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join([backend, *filter(None, [env.get("PYTHONPATH")])])
        command = [sys.executable, "-m", "ganttier.optimizacion.trabajador", "--db", str(scratch.db_path.resolve()),
                   "--data-dir", str(scratch.data_dir.resolve()), "--pid", str(pid),
                   "--variante-json", str(request.resolve()), "--salida", str(output.resolve())]
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
        started = time.monotonic()
        with log.open("wb") as handle:
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=handle, stderr=handle, **flags)
        return _Job(variant, scratch, process, output, log, started, limit, scenario, schema)
    except BaseException:
        if process is not None:
            _terminate(process)
        scratch.cleanup()
        raise


def _finish(job: _Job, parallel: int, status: str | None = None, message: str = "",
            keep: bool = False) -> dict:
    try:
        if status:
            _terminate(job.process)
            row = _failure(job.variant, status, message, elapsed=time.monotonic() - job.started)
        else:
            job.process.wait()
            try:
                row = json.loads(job.output.read_text(encoding="utf-8"))
                if not isinstance(row, dict) or row.get("status") not in {"ok", "infactible", "error"}:
                    raise ValueError("Salida del trabajador inválida.")
            except (OSError, ValueError) as exc:
                with job.log.open("rb") as handle:
                    handle.seek(max(0, job.log.stat().st_size - 4000))
                    detail = handle.read().decode("utf-8", errors="replace")
                row = _failure(job.variant, "error", f"El trabajador terminó con código {job.process.returncode}: {exc}. {detail}")
        row["elapsed_s"] = time.monotonic() - job.started
        row["scenario_hash"] = job.scenario_hash
        row["database_schema"] = job.schema
        row["worker_pid"] = job.process.pid
        _annotate(row, parallel, job.variant)
        if keep:
            row["scratch_root"] = str(job.scratch.root)
        return row
    finally:
        if job.process.poll() is None:
            _terminate(job.process)
        job.scratch.cleanup()


def _externalize(row: dict, directory: Path | None, include: bool = True) -> None:
    if not include or row.get("status") != "ok" or row.get("feasible") is not True:
        row.pop("run", None)
        row["run_payload_ref"] = None
    elif directory is not None and row.get("run"):
        directory.mkdir(parents=True, exist_ok=True)
        identity = sha256(row["variant"]["id"].encode("utf-8")).hexdigest()[:16]
        path = directory / f"ganttier-run-{identity}-{uuid4().hex}.json"
        _write_json(path, row["run"])
        row.pop("run")
        row["run_payload_ref"] = str(path.resolve())


def ejecutar_variante(db_path: str | Path, data_dir: str | Path | None, pid: int, variante: Variante, *,
                      timeout_s: float | None = None, conservar_copia: bool = False,
                      incluir_run_payload: bool = True, directorio_runs: Path | None = None) -> dict:
    """Resuelve en un hijo y su copia; Ctrl+C termina su árbol y devuelve cancelado.

    Con directorio_runs escribe la envoltura de §8.2 en un lateral. Sin él, una
    resolución individual puede incluir run en memoria (el emisor CLI lo oculta).
    """
    _positive(timeout_s, "timeout_s")
    if directorio_runs is not None:
        _protect_destination(Path(directorio_runs), Path(db_path), Path(data_dir) if data_dir else None)
    job = None
    try:
        job = _start(Path(db_path).resolve(), Path(data_dir).resolve() if data_dir else None, pid, variante, timeout_s, conservar_copia)
        while job.process.poll() is None:
            if time.monotonic() - job.started >= job.limit:
                return _finish(job, 1, "timeout", f"Se agotó el límite duro de {job.limit:g} segundos.", conservar_copia)
            time.sleep(.03)
        row = _finish(job, 1, keep=conservar_copia)
        _externalize(row, Path(directorio_runs).resolve() if directorio_runs else None, incluir_run_payload)
        return row
    except KeyboardInterrupt:
        return (_finish(job, 1, "cancelado", "Resolución cancelada por Ctrl+C.", conservar_copia) if job else
                _failure(variante, "cancelado", "Resolución cancelada por Ctrl+C."))
    except Exception as exc:
        if job is not None and job.process.poll() is None:
            _terminate(job.process)
            job.scratch.cleanup()
        code = "dependencia_faltante" if isinstance(exc, ImportError) else "datos_invalidos" if isinstance(exc, ValueError) else "interno"
        row = _failure(variante, "error", str(exc), code=code, error_type=type(exc).__name__)
        _annotate(row, 1, variante)
        return row


def _read_jsonl(path: Path) -> Iterator[dict]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        number = 0
        while raw := handle.readline():
            number += 1
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
                if not isinstance(row, dict) or not isinstance(row.get("variant"), dict) or not row["variant"].get("id"):
                    raise ValueError("Falta variant.id.")
            except (ValueError, UnicodeError) as exc:
                if handle.tell() == size and not raw.endswith(b"\n"):
                    break  # sólo se tolera la última línea truncada por una caída.
                raise ValueError(f"JSONL inválido en {path}, línea {number}: {exc}") from exc
            yield row


def _append(handle: Any, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _reconcile(path: Path, discarded: set[str]) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for row in _read_jsonl(path):
                row.pop("run", None)
                reference = row.get("run_payload_ref")
                if reference and (reference in discarded or not Path(reference).is_file()):
                    row["run_payload_ref"] = None
                    row["run_retained"] = False
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def ejecutar_plan(db_path: str | Path, data_dir: str | Path | None, pid: int, variantes: list[Variante], *,
                  paralelo: int | None = None, timeout_s: float | None = None,
                  presupuesto_s: float | None = None, reanudar_desde: Path | None = None,
                  salida_jsonl: Path | None = None, callback: Callable[[dict, int, int], None] | None = None,
                  cancelar: threading.Event | None = None, directorio_runs: Path | None = None,
                  conservar_runs: int | None = 3) -> list[dict]:
    """Ejecuta una cola acotada y devuelve resúmenes, nunca run.payload.

    Reanuda sólo ok/infactible, reintenta errores y tolera una última línea
    truncada. La poda usa el ranking lexicográfico del contrato. Cancelación,
    presupuesto y Ctrl+C devuelven parciales; las variantes aún no iniciadas
    quedan pendientes para reanudar. Si la detención ocurre entre arranques,
    se marca la siguiente variante cancelada/timeout para comunicarla a la CLI.
    """
    _positive(timeout_s, "timeout_s")
    _positive(presupuesto_s, "presupuesto_s")
    if paralelo is None:
        value = os.environ.get("GANTTIER_CLI_PARALELO")
        try:
            paralelo = int(value) if value is not None else max(1, min(4, (os.cpu_count() or 1) // 2))
        except ValueError as exc:
            raise ValueError("GANTTIER_CLI_PARALELO debe ser entero positivo.") from exc
    if type(paralelo) is not int or paralelo < 1:
        raise ValueError("paralelo debe ser entero positivo.")
    if conservar_runs is not None and (type(conservar_runs) is not int or conservar_runs < 0):
        raise ValueError("conservar_runs debe ser entero no negativo o None.")
    if len({variant.id for variant in variantes}) != len(variantes):
        raise ValueError("El plan contiene ids de variante repetidos.")
    db_path = Path(db_path).resolve()
    data_dir = Path(data_dir).resolve() if data_dir else None
    output = Path(salida_jsonl).resolve() if salida_jsonl else None
    resume = Path(reanudar_desde).resolve() if reanudar_desde else None
    directory = (Path(directorio_runs).resolve() if directorio_runs else
                 output.with_suffix(".runs") if output else Path.cwd() / ".ganttier-cli-runs" / uuid4().hex)
    if output:
        _protect_destination(output, db_path, data_dir)
    _protect_destination(directory, db_path, data_dir)
    results: list[dict] = []
    completed: dict[str, dict] = {}
    retained: list[dict] = []
    discarded: set[str] = set()
    wanted = {v.id: v for v in variantes}
    started = time.monotonic()

    def discard(row: dict) -> None:
        row.pop("run", None)
        reference = row.get("run_payload_ref")
        if reference:
            path = Path(reference).resolve()
            # Un JSONL externo nunca autoriza borrar archivos ajenos o fuera del directorio.
            if path.parent == directory.resolve() and path.name.startswith("ganttier-run-"):
                path.unlink(missing_ok=True)
            discarded.add(reference)
        row["run_payload_ref"] = None
        row["run_retained"] = False

    def retain(row: dict) -> None:
        if not (row.get("status") == "ok" and row.get("feasible") is True) or conservar_runs == 0:
            discard(row)
            return
        if conservar_runs is not None and len(retained) >= conservar_runs:
            worst = max(retained, key=_lex_key)
            if _lex_key(row) >= _lex_key(worst):
                discard(row)
                return
            discard(worst)
            retained.remove(worst)
        _externalize(row, directory)
        reference = row.get("run_payload_ref")
        row["run_retained"] = bool(reference and Path(reference).is_file())
        if not row["run_retained"]:
            row["run_payload_ref"] = None
        elif conservar_runs is not None:
            retained.append(row)

    if resume:
        for row in _read_jsonl(resume):
            identity = row["variant"]["id"]
            if identity in wanted and row.get("status") in {"ok", "infactible"}:
                previous = Variante.from_dict(row["variant"])
                if previous._fingerprint() != wanted[identity]._fingerprint():
                    raise ValueError(f"El id {identity} tiene otros parámetros en el JSONL de reanudación.")
                row.pop("run", None)
                completed[identity] = row
        for row in completed.values():
            row["resumed"] = True
            retain(row)
            results.append(row)
    queue = iter(v for v in variantes if v.id not in completed)
    effective = min(paralelo, max(1, len(variantes) - len(completed)))
    active: list[_Job] = []
    exhausted, stopped = False, None
    handle = None

    def record(row: dict) -> None:
        retain(row)
        results.append(row)
        if handle is not None:
            _append(handle, row)
        if callback is not None:
            # El callback recibe una instantánea de metadatos, nunca el payload.
            callback(json.loads(json.dumps(row, ensure_ascii=False)), len(results), len(variantes))

    def stop_reason() -> tuple[str, str] | None:
        if cancelar is not None and cancelar.is_set():
            return "cancelado", "Plan cancelado por el solicitante."
        if presupuesto_s is not None and time.monotonic() - started >= presupuesto_s:
            return "timeout", f"Se agotó el presupuesto global de {presupuesto_s:g} segundos."
        return None

    try:
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            same = resume == output
            if same:
                _reconcile(output, discarded)
            handle = output.open("a" if same else "w", encoding="utf-8", newline="\n")
            if not same:
                for row in results:
                    _append(handle, row)
        while not exhausted or active:
            stopped = stop_reason()
            if stopped:
                break
            while len(active) < effective and not exhausted:
                if stop_reason():
                    break
                variant = next(queue, None)
                if variant is None:
                    exhausted = True
                    break
                try:
                    active.append(_start(db_path, data_dir, pid, variant, timeout_s, False))
                except Exception as exc:
                    row = _failure(variant, "error", str(exc), code="dependencia_faltante" if isinstance(exc, ImportError) else
                                   "datos_invalidos" if isinstance(exc, ValueError) else "interno", error_type=type(exc).__name__)
                    _annotate(row, effective, variant)
                    record(row)
            stopped = stop_reason()
            if stopped:
                break
            for job in active[:]:
                if job.process.poll() is not None:
                    row = _finish(job, effective)
                elif time.monotonic() - job.started >= job.limit:
                    row = _finish(job, effective, "timeout", f"Se agotó el límite duro de {job.limit:g} segundos.")
                else:
                    continue
                active.remove(job)
                record(row)
            if active:
                time.sleep(.03)
    except KeyboardInterrupt:
        stopped = ("cancelado", "Plan cancelado por Ctrl+C.")
    finally:
        try:
            notifications = []
            for job in active[:]:
                row = _finish(job, effective, *(stopped or ("cancelado", "El supervisor interrumpió la ejecución.")))
                active.remove(job)
                # Un fallo en callback nunca debe impedir limpiar los demás hijos.
                retain(row)
                results.append(row)
                if handle is not None:
                    _append(handle, row)
                notifications.append((row, len(results)))
            if stopped and not notifications:
                pending = next(queue, None)
                if pending is not None:
                    row = _failure(pending, *stopped)
                    _annotate(row, effective, pending)
                    record(row)
            # Notificar recién cuando TODOS los hijos activos están terminados:
            # una excepción del callback no debe dejar procesos vivos.
            if callback is not None:
                for row, done in notifications:
                    callback(json.loads(json.dumps(row, ensure_ascii=False)), done, len(variantes))
        finally:
            # Segunda barrera ante errores de escritura durante la limpieza.
            for job in active:
                try:
                    _terminate(job.process)
                finally:
                    job.scratch.cleanup()
            if handle is not None:
                handle.close()
            if output and output.exists():
                _reconcile(output, discarded)
    return results
