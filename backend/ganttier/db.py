"""Conexiones SQLite locales y migraciones versionadas e idempotentes."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from .migrations import LATEST as SCHEMA_VERSION  # noqa: E402  (última versión de la cadena)


def database_path() -> Path:
    """Ruta configurable de la base de datos (por defecto Ganttier/datos)."""
    return Path(os.environ.get("GANTTIER_DB", str(ROOT / "datos" / "ganttier.db"))).resolve()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Abre una conexión por petición; WAL, claves foráneas y caché de 64 MiB."""
    target = str(path if path is not None else database_path())
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Aplica el esquema compartido y registra la versión sin degradar bases futuras."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    version = int(row[0]) if row else 0
    if version > SCHEMA_VERSION:
        raise ValueError(f"La base de datos pertenece a una versión más reciente de Ganttier (esquema {version}; "
                         f"este programa admite hasta {SCHEMA_VERSION}).")
    if version < 1:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        try:
            conn.executescript("BEGIN IMMEDIATE;\n" + schema + "\nINSERT OR REPLACE INTO meta VALUES ('schema_version','1');\nCOMMIT;")
        except Exception:
            conn.rollback()
            raise
    from .migrations import migrate
    migrate(conn)  # pasos 1 → N (respaldo previo si la base tiene datos)
    from .calendars import ensure_default_calendars
    ensure_default_calendars(conn)
    from .control.queries import install_indexes
    install_indexes(conn)
