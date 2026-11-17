"""Copias SQLite, exclusión de escritores y aislamiento de archivos sin Qt."""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
import warnings
from dataclasses import dataclass, field
from typing import Iterator


class BaseOcupada(RuntimeError):
    """La base tiene un escritor vivo o no se puede adquirir su exclusión."""

    code = "base_ocupada"
    exit_code = 3


@dataclass
class BloqueoInfo:
    """Estado observable de los archivos de bloqueo de GUI y CLI."""

    ocupado: bool
    por: str | None
    pid: int | None
    detalle: str


def _retry(operation):
    for attempt in range(8):
        try:
            return operation()
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(0.05 * 2 ** attempt, 0.5))


@dataclass
class ScratchSpace:
    """Copia con directorio propio; cerrar las conexiones antes de salir."""

    db_path: Path
    data_dir: Path
    root: Path
    _conservar: bool = field(default=False, repr=False)

    def cleanup(self) -> None:
        """Elimina toda la copia, incluidos WAL/SHM, con reintentos en Windows."""
        if not self._conservar and self.root.exists():
            _retry(lambda: shutil.rmtree(self.root))

    def __enter__(self) -> ScratchSpace:
        """Devuelve la copia temporal."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Libera archivos incluso al salir por excepción."""
        self.cleanup()


def _lectura(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def crear_copia(db_path: str | Path, data_dir: str | Path | None = None, *,
                raiz: Path | None = None, conservar: bool = False) -> ScratchSpace:
    """Copia la instantánea confirmada de SQLite y migra únicamente la copia.

    Por defecto el temporal queda junto a la base; nunca usa el TEMP global.
    Los archivos de ``data_dir`` no se copian: cada corrida tiene datos propios.
    """
    from .. import db

    source = Path(db_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {source}")
    parent = Path(raiz).resolve() if raiz is not None else source.parent
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=".ganttier-cli-", dir=parent))
    scratch = ScratchSpace(root / source.name, root / "datos", root)
    try:
        scratch.data_dir.mkdir()
        with contextlib.closing(_lectura(source)) as origin:
            with contextlib.closing(sqlite3.connect(scratch.db_path)) as dest:
                origin.backup(dest)
        with contextlib.closing(db.connect(scratch.db_path)) as dest:
            db.initialize(dest)
            dest.commit()
        scratch._conservar = conservar
        return scratch
    except BaseException:
        scratch.cleanup()
        raise


def _pid_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # acceso denegado: conservar exclusión
        try:
            status = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(status)) or status.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _leer_bloqueo(path: Path, por: str) -> BloqueoInfo:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except FileNotFoundError:
        return BloqueoInfo(False, None, None, "Sin bloqueo.")
    except PermissionError:
        return BloqueoInfo(True, por, None, f"No se puede leer el bloqueo {path}.")
    try:
        pid = int(lines[0])
    except (ValueError, IndexError):
        # Un archivo CLI recién reservado puede estar entre O_EXCL y write().
        return BloqueoInfo(por == "cli", por, None, f"Bloqueo {por} incompleto: {path}.")
    alive = _pid_vivo(pid)
    app = lines[1] if len(lines) > 1 else ""
    host = lines[2] if len(lines) > 2 else ""
    return BloqueoInfo(alive, por, pid,
                       f"Bloqueo {por} {'vigente' if alive else 'obsoleto'}: PID {pid}, {app}, {host}.")


def estado_bloqueo(db_path: str | Path) -> BloqueoInfo:
    """Interpreta QLockFile y el bloqueo CLI comprobando sus procesos dueños."""
    target = Path(db_path).resolve()
    states = [_leer_bloqueo(Path(str(target) + suffix), owner)
              for suffix, owner in ((".lock", "gui"), (".cli.lock", "cli"))]
    return next((state for state in states if state.ocupado),
                BloqueoInfo(False, None, None, " ".join(s.detalle for s in states)))


@contextlib.contextmanager
def _exclusion_sistema(path: Path) -> Iterator[None]:
    """Serializa también la recuperación de archivos obsoletos sin residuos."""
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        key = hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()
        handle = kernel.CreateMutexW(None, False, "Local\\GanttierCLI_" + key)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
            if not acquired:
                raise BaseOcupada("Otro escritor CLI tiene la base.")
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        import fcntl
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BaseOcupada("Otro escritor CLI tiene la base.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def bloqueo_escritura(db_path: str | Path, *, forzar: bool = False,
                       quien: str = "cli") -> Iterator[BloqueoInfo]:
    """Reserva atómicamente el escritor CLI; forzar sólo omite el bloqueo GUI."""
    target = Path(db_path).resolve()
    lock = Path(str(target) + ".cli.lock")
    token = uuid.uuid4().hex
    content = f"{os.getpid()}\n{quien}\n{socket.gethostname()}\n{token}\n"
    with _exclusion_sistema(target):
        cli = _leer_bloqueo(lock, "cli")
        if cli.ocupado and cli.pid is not None:
            raise BaseOcupada(cli.detalle)
        # El mutex/flock excluye la creación concurrente por este protocolo.
        if lock.exists():
            if cli.pid is None and time.time() - lock.stat().st_mtime < 30:
                raise BaseOcupada(cli.detalle)
            _retry(lambda: lock.unlink(missing_ok=True))
        info = _leer_bloqueo(Path(str(target) + ".lock"), "gui")
        if info.ocupado and not forzar:
            raise BaseOcupada(info.detalle)
        fd = None
        owned = False
        try:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                raise BaseOcupada("Otro escritor reservó el bloqueo CLI.") from exc
            owned = True
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                fd = None
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            info = _leer_bloqueo(Path(str(target) + ".lock"), "gui")
            if info.ocupado and not forzar:
                raise BaseOcupada(info.detalle)
            if forzar:
                info.detalle += " Se forzó el bloqueo por solicitud explícita."
            yield info
        finally:
            if fd is not None:
                os.close(fd)
            if owned:
                _retry(lambda: lock.unlink(missing_ok=True))


def respaldar(db_path: str | Path, data_dir: str | Path, motivo: str) -> Path:
    """Crea y verifica el respaldo en backup_dir(db_path), nunca en otra base.

    Compatibilidad con versiones que aún no admiten ``cli``: respaldo manual
    retenido con motivo CLI y advertencia explícita, sin alterar backups.py.
    """
    from .. import backups

    directory = backups.backup_dir(Path(db_path))
    with contextlib.closing(_lectura(db_path)) as conn:
        try:
            info = backups.create_backup(conn, reason=motivo, kind="cli", directory=directory)
        except ValueError as exc:
            if str(exc) != "Tipo de respaldo inválido.":
                raise
            warnings.warn("backups.create_backup todavía no admite kind='cli'; "
                          "se conserva un respaldo manual con motivo CLI.", RuntimeWarning, stacklevel=2)
            info = backups.create_backup(conn, reason="cli: " + motivo, kind="manual", directory=directory)
    path = directory / info.file
    ok, detail = backups.verify_file(path)
    if not ok:
        raise RuntimeError(f"El respaldo no pasó la verificación: {detail}")
    return path


def hash_escenario(db_path: str | Path, pid: int) -> str:
    """Calcula la huella en una transacción de sólo lectura, sin migraciones."""
    from .. import services

    with contextlib.closing(_lectura(db_path)) as conn:
        conn.execute("BEGIN")
        return services._scenario_fingerprint(conn, pid)


_DATA_LOCK = threading.RLock()


@contextlib.contextmanager
def redirigir_datos(data_dir: str | Path) -> Iterator[None]:
    """Redirige DATA_DIR y restaura módulos y entorno aun si el bloque falla.

    Serializa contextos entre hilos; para paralelismo real usar procesos hijos.
    También restaura módulos importados dentro del bloque que heredaron la ruta.
    """
    from .. import services

    target = Path(data_dir).resolve()
    with _DATA_LOCK:
        original = services.DATA_DIR
        previous_env = os.environ.get("GANTTIER_DATA_DIR")
        previous = {}
        for name, module in list(sys.modules.items()):
            if (name == "ganttier" or name.startswith("ganttier.")) and isinstance(getattr(module, "DATA_DIR", None), Path):
                previous[name] = (module, module.DATA_DIR)
                module.DATA_DIR = target
        os.environ["GANTTIER_DATA_DIR"] = str(target)
        try:
            yield
        finally:
            for name, module in list(sys.modules.items()):
                if name not in previous and (name == "ganttier" or name.startswith("ganttier.")):
                    if getattr(module, "DATA_DIR", None) == target:
                        module.DATA_DIR = original
            for module, value in previous.values():
                module.DATA_DIR = value
            if previous_env is None:
                os.environ.pop("GANTTIER_DATA_DIR", None)
            else:
                os.environ["GANTTIER_DATA_DIR"] = previous_env
