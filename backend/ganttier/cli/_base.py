"""Contrato compartido de la CLI de Ganttier (ver trabajos/cli_codex_20261019/CLI_SPEC.md).

Este módulo NO importa el motor al cargarse (arranque rápido de `describir`); todo import pesado es perezoso.
Los módulos de ``cli/comandos/*.py`` exponen ``register(sub)`` y sus handlers reciben ``(args, ctx)``.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

CLI_VERSION = "1.0"

EXIT_OK = 0
EXIT_USO = 1          # uso o datos inválidos / no encontrado
EXIT_NEGATIVO = 2     # resultado negativo legítimo (infactible, violaciones)
EXIT_OCUPADA = 3      # base ocupada (GUI/otro escritor) o el escenario cambió
EXIT_TIEMPO = 4       # tiempo/presupuesto agotado o cancelado
EXIT_INTERNO = 5      # error interno
EXIT_DEPENDENCIA = 6  # dependencia faltante (OR-Tools u otra)

CODES = {"infactible", "no_encontrado", "datos_invalidos", "base_ocupada", "escenario_cambio",
         "dependencia_faltante", "timeout", "interno", "uso"}


class CliError(Exception):
    """Error controlado: se traduce al sobre ``error`` y al código de salida indicado."""

    def __init__(self, code: str, message: str, *, hint: str | None = None,
                 detail: Any = None, exit_code: int = EXIT_USO):
        super().__init__(message)
        self.code, self.message, self.hint, self.detail, self.exit_code = code, message, hint, detail, exit_code


@dataclass
class Result:
    """Retorno de un handler. ``exit_code`` != 0 con ``data`` presente = resultado negativo legítimo (p. ej. 2)."""
    data: Any = None
    warnings: list[str] = field(default_factory=list)
    exit_code: int = EXIT_OK


@dataclass
class MutationInfo:
    simulated: bool = True
    backup: str | None = None
    locked_by: str | None = None
    db_path: str | None = None


@dataclass
class Context:
    """Estado global de una invocación. Lo construye ``core.py`` a partir de los flags globales."""
    db_path: Path
    data_dir: Path
    project: str | None = None          # valor de -p/--proyecto (id o nombre)
    write: bool = False                 # --escribir o GANTTIER_CLI_ESCRITURA=1
    force_lock: bool = False
    no_backup: bool = False
    verbose: bool = False
    warnings: list[str] = field(default_factory=list)
    mutation_info: MutationInfo | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    # -- lectura -------------------------------------------------------------------------------
    def conn(self) -> sqlite3.Connection:
        """Conexión de SÓLO lectura a la base real; nunca migra ni crea la base."""
        if not self.db_path.exists():
            raise CliError("no_encontrado", f"No existe la base de datos: {self.db_path}",
                           hint="Indique --db o defina GANTTIER_DB.")
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- proyecto ------------------------------------------------------------------------------
    def resolve_project(self, conn: sqlite3.Connection, token: str | int | None) -> int:
        """Id de proyecto a partir de un id numérico o de un nombre exacto único."""
        if token is None or str(token).strip() == "":
            raise CliError("uso", "Falta indicar el proyecto.", hint="Use -p ID o -p \"nombre exacto\".")
        text = str(token).strip()
        if text.isdigit():
            row = conn.execute("SELECT id FROM projects WHERE id=?", (int(text),)).fetchone()
            if row:
                return int(row[0])
        rows = conn.execute("SELECT id FROM projects WHERE name=?", (text,)).fetchall()
        if len(rows) == 1:
            return int(rows[0][0])
        if len(rows) > 1:
            raise CliError("uso", f"El nombre «{text}» no es único ({len(rows)} proyectos).", hint="Use el id.")
        raise CliError("no_encontrado", f"No existe el proyecto «{text}».", hint="Liste con: proyectos listar")

    def require_project(self, conn: sqlite3.Connection) -> int:
        return self.resolve_project(conn, self.project)

    # -- escritura segura ----------------------------------------------------------------------
    @contextlib.contextmanager
    def mutation(self, reason: str) -> Iterator[sqlite3.Connection]:
        """Conexión escribible. Sin ``--escribir``: copia temporal descartable (simulación).

        Con ``--escribir``: bloqueo de escritura + respaldo verificado + conexión a la base real
        (commit al salir sin error, rollback si hay excepción). Implementado sobre ``optimizacion.espacio``.
        """
        from ..optimizacion import espacio  # import perezoso
        from .. import db
        info = MutationInfo(db_path=str(self.db_path))
        self.mutation_info = info
        if not self.write:
            with espacio.crear_copia(self.db_path, self.data_dir) as scratch, espacio.redirigir_datos(scratch.data_dir):
                info.simulated = True
                conn = db.connect(scratch.db_path)
                try:
                    db.initialize(conn)
                    yield conn
                    conn.commit()
                finally:
                    conn.close()
            return
        with espacio.bloqueo_escritura(self.db_path, forzar=self.force_lock) as lock:
            info.simulated, info.locked_by = False, (lock.por if lock and lock.ocupado else None)
            if not self.no_backup:
                info.backup = str(espacio.respaldar(self.db_path, self.data_dir, reason))
            conn = db.connect(self.db_path)
            try:
                if _schema_version(conn) != db.SCHEMA_VERSION:  # initialize() confirma INSERTs: sólo si hace falta migrar
                    db.initialize(conn)
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def scratch(self):
        """Copia temporal (con su propio data_dir) de la base real: ``with ctx.scratch() as sp: sp.db_path``."""
        from ..optimizacion import espacio
        return espacio.crear_copia(self.db_path, self.data_dir)

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)


Handler = Callable[[Any, Context], Result]


def _schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "si", "sí", "yes"}
