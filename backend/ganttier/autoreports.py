"""Emisión de períodos cerrados, persistencia en meta y scheduler local."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import services

logger = logging.getLogger(__name__)


class AutoReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    weekly: bool = True
    monthly: bool = True
    annual: bool = True
    formats: list[Literal["pdf", "xlsx", "docx", "html"]] = Field(
        default_factory=lambda: ["pdf", "xlsx"], min_length=1)
    weekday: int = Field(default=0, ge=0, le=6)
    keep_last: int = Field(default=12, ge=1)

    @field_validator("formats")
    @classmethod
    def unique_formats(cls, value):
        return list(dict.fromkeys(value))


def _require_project(conn, pid):
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
        raise LookupError("No se encontró el proyecto solicitado.")


def _read(conn, key, default):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _write(conn, key, value):
    conn.execute("INSERT INTO meta(key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value, ensure_ascii=False)))


@contextmanager
def _exclusive(conn):
    # Reservar antes de leer evita duplicados entre POST, hilo y otros procesos.
    # No confirmar ni revertir accidentalmente una transacción del llamador.
    if conn.in_transaction:
        raise ValueError("Los informes automáticos requieren una conexión sin transacción activa.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def get_config(conn: sqlite3.Connection, pid: int) -> dict:
    _require_project(conn, pid)
    return AutoReportConfig.model_validate(_read(conn, f"autoreports:{pid}", {})).model_dump()


def set_config(conn: sqlite3.Connection, pid: int, config: dict) -> dict:
    """Reemplaza la configuración; los campos omitidos toman sus valores iniciales."""
    value = AutoReportConfig.model_validate(config).model_dump()
    with _exclusive(conn):
        _require_project(conn, pid)
        _write(conn, f"autoreports:{pid}", value)
    return value


def get_log(conn: sqlite3.Connection, pid: int) -> list[dict]:
    _require_project(conn, pid)
    return _read(conn, f"autoreports_log:{pid}", {"history": []})["history"]


def _periods(today: date, config: dict):
    monday = today - timedelta(days=today.weekday())
    if config["weekly"] and today.weekday() >= config["weekday"]:
        yield "weekly", monday - timedelta(days=7), monday - timedelta(days=1)
    if config["monthly"]:
        end = today.replace(day=1) - timedelta(days=1)
        yield "monthly", end.replace(day=1), end
    if config["annual"]:
        yield "annual", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)


def _pending_periods(today: date, config: dict, state: dict):
    """Hasta 12 períodos por tipo y ciclo, incluidos reintentos de formatos faltantes."""
    for kind, latest, latest_end in _periods(today, config):
        known = {date.fromisoformat(key.split(":", 1)[1]): formats
                 for key, formats in state["emitted"].items() if key.startswith(kind + ":")}
        pending = sorted(start for start, formats in known.items()
                         if start <= latest and set(config["formats"]) - set(formats))
        used = 0
        for start in pending[:12]:
            yield kind, *services._period_bounds(kind, start)
            used += 1
        cursor = (services._period_bounds(kind, max(known))[1] + timedelta(days=1)) if known else latest
        while cursor <= latest and used < 12:
            start, end = services._period_bounds(kind, cursor)
            yield kind, start, end
            used += 1
            cursor = end + timedelta(days=1)


@contextmanager
def _cleanup_unconfirmed(files, pid):
    """Compensa archivos nuevos si falla la confirmación de sus metadatos."""
    try:
        yield
    except BaseException:
        directory = (services.DATA_DIR / "informes").resolve()
        for file in files:
            filename = file["filename"]
            path = directory / filename
            if Path(filename).name == filename and path.resolve().parent == directory and filename.startswith(f"proyecto-{pid}-"):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("No se pudo retirar el informe no confirmado %s", filename)
        raise


def _trim(state, keep_last):
    retained, removed, counts = [], [], {}
    for entry in reversed(state["history"]):
        bucket = entry["period_type"] if entry["status"] == "ok" else "errors"
        counts[bucket] = counts.get(bucket, 0) + 1
        (retained if counts[bucket] <= keep_last else removed).append(entry)
    state["history"] = list(reversed(retained))
    # El índice emitted no se poda: tampoco se reemite tras retroceder el reloj.
    state.setdefault("pending_delete", []).extend(
        file["filename"] for entry in removed for file in entry["files"])


def _delete_expired(conn, pid):
    with _exclusive(conn):
        state = _read(conn, f"autoreports_log:{pid}", {})
        pending = state.get("pending_delete", [])
        if not pending:
            return
        directory = (services.DATA_DIR / "informes").resolve()
        failed = []
        for filename in pending:
            path = directory / filename
            if (Path(filename).name != filename or path.resolve().parent != directory
                    or not filename.startswith(f"proyecto-{pid}-")):
                logger.error("Ruta de informe automático rechazada: %s", filename)
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                failed.append(filename)
                logger.exception("No se pudo retirar el informe %s", filename)
        state["pending_delete"] = failed
        _write(conn, f"autoreports_log:{pid}", state)


def run_project_reports(conn: sqlite3.Connection, pid: int,
                        now: datetime | None = None) -> list[dict]:
    """Emite lo vencido del proyecto; devuelve sólo archivos nuevos (también parciales)."""
    now = now or datetime.now().astimezone()
    emitted = []
    generated = []
    with _cleanup_unconfirmed(generated, pid), _exclusive(conn):
        _require_project(conn, pid)
        state = _read(conn, f"autoreports_log:{pid}", {"emitted": {}, "history": []})
        try:
            config = get_config(conn, pid)
        except (ValueError, TypeError) as exc:
            state["history"].append({"project_id": pid, "period_type": "config",
                "status": "error", "error": str(exc), "files": [], "emitted_at": now.isoformat()})
            _trim(state, 12)
            _write(conn, f"autoreports_log:{pid}", state)
            return []
        if not config["enabled"]:
            return []
        for kind, start, end in _pending_periods(now.date(), config, state):
            key = f"{kind}:{start.isoformat()}"
            done = state["emitted"].setdefault(key, [])
            missing = [fmt for fmt in config["formats"] if fmt not in done]
            if not missing:
                continue
            previous = next((item for item in state["history"]
                             if item.get("key") == key), None)
            entry = dict(previous) if previous else {
                "key": key, "project_id": pid, "period_type": kind,
                "period_start": start.isoformat(), "period_end": end.isoformat(), "files": []}
            entry["files"] = list(entry["files"])
            new_files, errors = [], []
            for fmt in missing:
                try:
                    files = services.render_reports(conn, pid, kind, start, [fmt])
                    generated.extend(files)
                    if not files:
                        raise ValueError("El generador no devolvió archivos.")
                except Exception as exc:
                    logger.exception("Error en informe automático %s / %s / %s", pid, key, fmt)
                    errors.append(f"{fmt}: {exc}")
                    continue
                done.append(fmt)
                new_files.extend(files)
            entry.update(emitted_at=now.isoformat(), status="error" if errors else "ok",
                         error="; ".join(errors) if errors else None)
            entry["files"].extend(new_files)
            if previous is not None:
                state["history"].remove(previous)
            state["history"].append(entry)
            if new_files:
                emitted.append(dict(entry, files=new_files))
        _trim(state, config["keep_last"])
        _write(conn, f"autoreports_log:{pid}", state)
    _delete_expired(conn, pid)
    return emitted


def run_due_reports(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """Procesa proyectos configurados, aislando los errores de cada uno."""
    now = now or datetime.now().astimezone()
    if conn.in_transaction:
        raise ValueError("Los informes automáticos requieren una conexión sin transacción activa.")
    pids = [row[0] for row in conn.execute(
        "SELECT p.id FROM projects p JOIN meta m ON m.key='autoreports:' || p.id ORDER BY p.id")]
    emitted = []
    for pid in pids:
        try:
            emitted.extend(run_project_reports(conn, pid, now))
        except Exception:
            # Si SQLite o el propio JSON del historial están dañados, conservarlos
            # intactos y registrar el fallo en logging; seguir con los demás.
            logger.exception("No se pudo emitir el informe del proyecto %s", pid)
    return emitted


_scheduler_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _daily_backup(conn) -> None:
    """P4-06a: respaldo automático diario mientras la aplicación está abierta (cada ciclo revisa 24 h)."""
    try:
        from . import backups
        backups.ensure_daily(conn)
    except Exception:
        logger.exception("No se pudo crear el respaldo automático diario")


def start_scheduler(db_connect: Callable, interval_s: int = 3600) -> threading.Thread:
    """Inicia una sola vez por proceso; cada ciclo abre/cierra su propia conexión."""
    if isinstance(interval_s, bool) or not isinstance(interval_s, int) or interval_s <= 0:
        raise ValueError("El intervalo debe ser un entero positivo de segundos.")
    global _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return _scheduler_thread
        _stop_event.clear()

        def loop():
            while not _stop_event.is_set():
                conn = None
                try:
                    conn = db_connect()
                    run_due_reports(conn)
                    _daily_backup(conn)
                except Exception:
                    logger.exception("Error del scheduler de informes automáticos")
                finally:
                    if conn is not None:
                        conn.close()
                if _stop_event.wait(interval_s):
                    break

        _scheduler_thread = threading.Thread(target=loop, name="ganttier-autoreports", daemon=True)
        _scheduler_thread.start()
        return _scheduler_thread


def stop_scheduler() -> None:
    """Despierta el hilo y espera el cierre; permite terminar la emisión en curso."""
    with _scheduler_lock:
        _stop_event.set()
        thread = _scheduler_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join()


@contextmanager
def scheduler(db_connect: Callable, interval_s: int = 3600):
    """Integración con lifespan: garantiza stop incluso si yield lanza una excepción."""
    thread = start_scheduler(db_connect, interval_s)
    try:
        yield thread
    finally:
        stop_scheduler()
