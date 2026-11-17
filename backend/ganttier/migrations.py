"""Migraciones incrementales del esquema SQLite (P4-07).

Reglas:
- La versión 1 es ``schema.sql``. Cada paso posterior es ``N-1 → N`` y se ejecuta en su propia
  transacción ``BEGIN IMMEDIATE``; la versión se registra sólo al confirmar el paso.
- Una base nueva recorre la MISMA cadena que una existente (v1 + pasos), así ambas quedan iguales.
- Antes de migrar una base con datos se crea un respaldo verificado (``backups.create_backup``).
- Una base de una versión futura se rechaza sin ejecutar DDL.
- Nunca se baja de versión: para volver atrás se usa el ejecutable anterior con su respaldo.

Para agregar un cambio: escriba una función ``_vN(conn)`` y agréguela al final de ``STEPS``.
No modifique pasos ya liberados.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable


def _script(conn: sqlite3.Connection, sql: str) -> None:
    """Ejecuta DDL simple sentencia por sentencia DENTRO de la transacción del paso.

    (``executescript`` confirmaría la transacción y el paso dejaría de ser atómico.)
    No usar para triggers o sentencias con «;» internos.
    """
    for statement in (part.strip() for part in sql.split(";")):
        if statement:
            conn.execute(statement)


def _v2(conn: sqlite3.Connection) -> None:
    """Bitácora de operaciones sensibles y alertas de control."""
    _script(conn, """
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
          at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          action TEXT NOT NULL,
          entity TEXT NOT NULL,
          entity_id INTEGER,
          detail TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS ix_audit_project ON audit_log(project_id, at);

        CREATE TABLE IF NOT EXISTS alerts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          rule TEXT NOT NULL,
          object_key TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'advertencia',
          title TEXT NOT NULL DEFAULT '',
          detail TEXT NOT NULL DEFAULT '',
          data_date TEXT,
          state TEXT NOT NULL DEFAULT 'nueva' CHECK (state IN ('nueva','reconocida','resuelta')),
          first_seen TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          UNIQUE(project_id, rule, object_key)
        );
        CREATE INDEX IF NOT EXISTS ix_alerts_project ON alerts(project_id, state);
    """)


def _v3(conn: sqlite3.Connection) -> None:
    """Nominal role allocation, separate from the original resource demands."""
    _script(conn, """
        CREATE TABLE role_schedules (
          project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
          run_id INTEGER NOT NULL REFERENCES schedule_runs(id) ON DELETE CASCADE,
          scenario_hash TEXT NOT NULL,
          options TEXT NOT NULL,
          time_axis TEXT NOT NULL
        );
        CREATE TABLE role_allocations (
          project_id INTEGER NOT NULL REFERENCES role_schedules(project_id) ON DELETE CASCADE,
          activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
          source_assignment_id INTEGER NOT NULL,
          resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
          skill TEXT NOT NULL,
          PRIMARY KEY(activity_id, resource_id)
        );
        CREATE INDEX ix_role_allocations_project ON role_allocations(project_id);
    """)


def _v4(conn: sqlite3.Connection) -> None:
    """Equipos y lotes optativos; campos nulos conservan el comportamiento v3."""
    _script(conn, """
        ALTER TABLE resources ADD COLUMN is_work_team INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE periodic_tasks ADD COLUMN quantity_total REAL;
        ALTER TABLE periodic_tasks ADD COLUMN unit TEXT NOT NULL DEFAULT '';
        ALTER TABLE periodic_tasks ADD COLUMN batch_size REAL;
        ALTER TABLE periodic_tasks ADD COLUMN productivity_day_team REAL;
        ALTER TABLE periodic_tasks ADD COLUMN team_ids TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE periodic_tasks ADD COLUMN balance_tolerance_batches INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE activities ADD COLUMN batch_index INTEGER;
        ALTER TABLE activities ADD COLUMN quantity REAL;
        ALTER TABLE activities ADD COLUMN unit TEXT NOT NULL DEFAULT '';
        ALTER TABLE activities ADD COLUMN team_id INTEGER REFERENCES resources(id) ON DELETE RESTRICT;
        ALTER TABLE activities ADD COLUMN equity_mode TEXT NOT NULL DEFAULT 'occurrence';
        CREATE INDEX ix_activities_team_occurrence ON activities(project_id, periodic_task_id, occurrence_index, team_id);
    """)


def _v5(conn: sqlite3.Connection) -> None:
    """Lotes divisibles por defecto; continuidad estricta optativa."""
    conn.execute("ALTER TABLE periodic_tasks ADD COLUMN batch_continuous_in_shift INTEGER NOT NULL DEFAULT 0 CHECK(batch_continuous_in_shift IN (0,1))")


def _v6(conn: sqlite3.Connection) -> None:
    """Horas exactas y rutas; no mueve programas previamente guardados."""
    _script(conn, """
        ALTER TABLE periodic_tasks ADD COLUMN batch_full_day INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE periodic_tasks ADD COLUMN productivity_hour_team REAL;
        ALTER TABLE periodic_tasks ADD COLUMN productive_hours_day REAL;
        ALTER TABLE periodic_tasks ADD COLUMN route_code TEXT NOT NULL DEFAULT '';
        ALTER TABLE periodic_tasks ADD COLUMN is_route INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE periodic_tasks ADD COLUMN is_daily_round INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE activities ADD COLUMN duration_hours REAL;
        ALTER TABLE activities ADD COLUMN checklist_codes TEXT NOT NULL DEFAULT '[]';
        CREATE INDEX ix_tasks_route ON periodic_tasks(project_id,route_code);
    """)


STEPS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (2, "Bitácora de operaciones y alertas de control", _v2),
    (3, "Asignación nominal por rol de proyecto", _v3),
    (4, "Equipos de trabajo y reparto por cantidad", _v4),
    (5, "Continuidad de lote optativa", _v5),
    (6, "Lotes por horas, rutas y rondas", _v6),
]
LATEST = STEPS[-1][0] if STEPS else 1


def database_file(conn: sqlite3.Connection) -> Path | None:
    """Ruta del archivo principal o None para bases en memoria/temporales."""
    for _, name, path in conn.execute("PRAGMA database_list"):
        if name == "main":
            return Path(path) if path else None
    return None


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _has_user_data(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT EXISTS(SELECT 1 FROM projects)").fetchone()[0] == 1
    except sqlite3.OperationalError:
        return False


def migrate(conn: sqlite3.Connection, *, backup: bool = True) -> list[int]:
    """Aplica los pasos pendientes y devuelve las versiones aplicadas."""
    version = current_version(conn)
    if version > LATEST:
        raise ValueError(f"La base de datos es de una versión más reciente de Ganttier (esquema {version}; "
                         f"este programa admite hasta {LATEST}). Use la versión de Ganttier con la que se creó.")
    pending = [step for step in STEPS if step[0] > version]
    if not pending:
        return []
    path = database_file(conn)
    if backup and path is not None and version >= 1 and _has_user_data(conn):
        from . import backups
        backups.create_backup(conn, reason=f"pre-migracion-v{version}-a-v{pending[-1][0]}", kind="pre")
    applied = []
    for number, description, step in pending:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            step(conn)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(number),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                         (f"migration_{number}", f"{datetime.now().isoformat(timespec='seconds')} · {description}"))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        applied.append(number)
    return applied
