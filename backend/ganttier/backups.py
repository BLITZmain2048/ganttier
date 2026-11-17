"""Respaldos consistentes de la base SQLite con la aplicación abierta (P4-06a).

- Copia con la API de respaldo en línea de SQLite (``Connection.backup``): consistente aunque haya
  escrituras WAL en curso; no requiere cerrar Ganttier.
- Cada copia se verifica (``integrity_check`` y ``foreign_key_check``) antes de darla por válida y se
  acompaña de un manifiesto JSON (fecha, motivo, versión de esquema, tamaño, SHA-256).
- Tipos: ``auto`` (diaria), ``pre`` (antes de migrar, importar en reemplazo o restaurar) y ``manual``.
- Retención: 7 automáticas diarias + 1 por semana durante 4 semanas; 10 «pre» y 20 manuales.
- Restaurar nunca sobrescribe la base abierta: se programa para el próximo inicio
  (``schedule_restore``) y ``apply_pending_restore`` la aplica antes de abrir conexiones, dejando un
  respaldo previo de la base reemplazada.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

RETENTION = {"pre": 10, "manual": 20, "cli": 20}
AUTO_DAILY, AUTO_WEEKLY = 7, 4
PENDING = "restauracion_pendiente.json"


@dataclass
class BackupInfo:
    file: str
    created: str
    kind: str
    reason: str
    schema_version: int
    size: int
    sha256: str
    verified: bool
    projects: int = 0
    activities: int = 0


def backup_dir(db_path: Path | None = None) -> Path:
    from . import db
    base = Path(db_path) if db_path else db.database_path()
    return base.resolve().parent / "respaldos"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path: Path) -> tuple[bool, str]:
    """Comprueba un esquema Ganttier reconocible, integridad y claves foráneas."""
    from .migrations import LATEST
    try:
        path = Path(path).resolve()
        if path.stat().st_size <= 0:
            return False, "el archivo está vacío"
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "projects" not in tables:
                return False, "falta la tabla projects del esquema Ganttier"
            row = (conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                   if "meta" in tables else None)
            version = int(row[0]) if row else conn.execute("PRAGMA user_version").fetchone()[0]
            if not 1 <= version <= LATEST:
                return False, "la versión del esquema Ganttier no es reconocida"
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                return False, f"integrity_check: {ok}"
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                return False, f"foreign_key_check: {len(bad)} referencias inválidas"
            return True, "ok"
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return False, str(exc)


def _validate_restore(path: Path, manifest: Path | None = None) -> None:
    """También contrasta el contenido con el manifiesto, si está disponible."""
    try:
        size = path.stat().st_size
        if size <= 0:
            raise ValueError("el archivo está vacío")
        manifest = manifest if manifest is not None else path.with_suffix(".json")
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("el manifiesto no es válido")
            if "size" in data and data["size"] != size:
                raise ValueError("el tamaño no coincide con el manifiesto")
            if data.get("sha256") and data["sha256"] != _sha256(path):
                raise ValueError("el SHA-256 no coincide con el manifiesto")
        ok, message = verify_file(path)
        if not ok:
            raise ValueError(message)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"El respaldo no se puede restaurar: {exc}") from exc


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in text)[:60] or "respaldo"


def create_backup(conn: sqlite3.Connection, reason: str = "manual", kind: str = "manual",
                  directory: Path | None = None) -> BackupInfo:
    """Copia consistente y verificada de la base abierta en ``conn``."""
    from .migrations import current_version, database_file
    if kind not in {"auto", "pre", "manual", "cli"}:
        raise ValueError("Tipo de respaldo inválido.")
    source = database_file(conn)
    if source is None:
        raise ValueError("La base de datos en memoria no se puede respaldar.")
    target_dir = Path(directory) if directory else backup_dir(source)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now()
    name = f"ganttier_{stamp:%Y%m%d_%H%M%S}_{kind}_{_safe(reason)}_{uuid4().hex}"
    final = target_dir / f"{name}.db"
    temporary = final.with_suffix(".db.tmp")
    # El UUID separa los nombres finales; la apertura exclusiva reserva el temporal.
    with temporary.open("xb"):
        pass
    try:
        dest = sqlite3.connect(temporary)
        try:
            conn.backup(dest)
            dest.execute("PRAGMA journal_mode=DELETE")  # la copia es un único archivo autocontenido
        finally:
            dest.close()
        ok, message = verify_file(temporary)
        if not ok:
            raise RuntimeError(f"La copia no pasó la verificación ({message}); se conserva el respaldo anterior.")
        os.replace(temporary, final)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    check = sqlite3.connect(final.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        counts = [check.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("projects", "activities")]
    except sqlite3.Error:
        counts = [0, 0]
    finally:
        check.close()
    info = BackupInfo(final.name, stamp.isoformat(timespec="seconds"), kind, reason, current_version(conn),
                      final.stat().st_size, _sha256(final), True, *counts)
    final.with_suffix(".json").write_text(json.dumps(asdict(info), ensure_ascii=False, indent=1), encoding="utf-8")
    prune(target_dir)
    return info


def list_backups(directory: Path | None = None) -> list[BackupInfo]:
    target = Path(directory) if directory else backup_dir()
    out = []
    for manifest in sorted(target.glob("ganttier_*.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data.setdefault("sha256", "")  # manifiestos anteriores sin huella
            if (target / data["file"]).exists():
                out.append(BackupInfo(**data))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return sorted(out, key=lambda b: b.created, reverse=True)


def last_backup(directory: Path | None = None, kind: str | None = None) -> BackupInfo | None:
    items = [b for b in list_backups(directory) if kind is None or b.kind == kind]
    return items[0] if items else None


def _remove(directory: Path, info: BackupInfo) -> None:
    (directory / info.file).unlink(missing_ok=True)
    (directory / info.file).with_suffix(".json").unlink(missing_ok=True)


def prune(directory: Path, today: date | None = None) -> list[str]:
    """Aplica la política de retención y devuelve los archivos eliminados."""
    items = list_backups(directory)
    keep: set[str] = set()
    for marker in Path(directory).glob("restauracion_pendiente*.json"):
        data = _read_marker(marker)
        if data is not None:
            keep.add(data["file"])
    for kind, limit in RETENTION.items():
        keep |= {b.file for b in [x for x in items if x.kind == kind][:limit]}
    autos = [b for b in items if b.kind == "auto"]
    days: dict[str, BackupInfo] = {}
    for b in autos:  # más reciente primero
        days.setdefault(b.created[:10], b)
    keep |= {b.file for b in list(days.values())[:AUTO_DAILY]}
    today = today or date.today()
    weeks: dict[tuple[int, int], BackupInfo] = {}
    for b in autos:
        created = datetime.fromisoformat(b.created).date()
        if created >= today - timedelta(weeks=AUTO_WEEKLY):
            weeks.setdefault(created.isocalendar()[:2], b)
    keep |= {b.file for b in weeks.values()}
    removed = []
    for b in items:
        if b.file not in keep:
            _remove(directory, b)
            removed.append(b.file)
    return removed


def ensure_daily(conn: sqlite3.Connection, now: datetime | None = None) -> BackupInfo | None:
    """Crea el respaldo automático si el último tiene más de 24 h (o no existe)."""
    from .migrations import database_file
    path = database_file(conn)
    if path is None:
        return None
    last = last_backup(backup_dir(path), "auto")
    now = now or datetime.now()
    if last and now - datetime.fromisoformat(last.created) < timedelta(hours=24):
        return None
    has_data = conn.execute("SELECT EXISTS(SELECT 1 FROM projects)").fetchone()[0]
    if not has_data:
        return None
    return create_backup(conn, reason="diario", kind="auto")


def export_copy(name: str, destination: Path, directory: Path | None = None) -> Path:
    """Copia un respaldo a otra ubicación (p. ej., un disco externo) para revisarlo o guardarlo."""
    source = (Path(directory) if directory else backup_dir()) / Path(name).name
    if not source.exists():
        raise LookupError("No se encontró el respaldo.")
    destination = Path(destination)
    shutil.copy2(source, destination)
    return destination


def schedule_restore(name: str, db_path: Path | None = None) -> Path:
    """Programa la restauración para el próximo inicio (no toca la base abierta)."""
    from . import db
    target = Path(db_path) if db_path else db.database_path()
    directory = backup_dir(target)
    source = directory / Path(name).name
    if not source.exists():
        raise LookupError("No se encontró el respaldo.")
    _validate_restore(source)
    content = json.dumps({"file": source.name, "target": str(target.resolve())})
    marker = _pending_marker(target)
    if marker is None:
        # Conservar el nombre histórico cuando está libre. Si pertenece a otra base,
        # usar uno propio sin sobrescribir su programación, incluso si dos bases
        # programan simultáneamente su primera restauración.
        legacy = directory / PENDING
        try:
            with legacy.open("x", encoding="utf-8") as stream:
                stream.write(content)
            return legacy
        except FileExistsError:
            marker = _scoped_marker(target)
    marker.write_text(content, encoding="utf-8")
    return marker


def _read_marker(marker: Path) -> dict | None:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if (isinstance(data, dict) and isinstance(data.get("target"), str)
                and data["target"] and isinstance(data.get("file"), str)
                and data["file"] and Path(data["file"]).name == data["file"]):
            return data
    except (OSError, ValueError):
        pass
    return None


def _scoped_marker(target: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(target.resolve())).encode("utf-8")).hexdigest()
    return backup_dir(target) / f"restauracion_pendiente_{key}.json"


def _pending_marker(db_path: Path | None = None) -> Path | None:
    from . import db
    target = (Path(db_path) if db_path else db.database_path()).resolve()
    for marker in (_scoped_marker(target), backup_dir(target) / PENDING):
        data = _read_marker(marker)
        if data is not None and Path(data["target"]).resolve() == target:
            return marker
    return None


def cancel_restore(db_path: Path | None = None) -> None:
    marker = _pending_marker(db_path)
    if marker is not None:
        marker.unlink(missing_ok=True)


def pending_restore(db_path: Path | None = None) -> str | None:
    marker = _pending_marker(db_path)
    data = _read_marker(marker) if marker is not None else None
    return data["file"] if data is not None else None


def apply_pending_restore(db_path: Path | None = None) -> str | None:
    """Llamar ANTES de abrir conexiones: respalda la base actual y la reemplaza por la copia elegida."""
    from . import db
    target = Path(db_path) if db_path else db.database_path()
    directory = backup_dir(target)
    marker = _pending_marker(target)
    name = pending_restore(target)
    if marker is None or not name:
        return None
    source = directory / name
    _validate_restore(source)
    fd, temporary_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".restaurando", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        # Inmovilizar y verificar el origen ANTES del respaldo previo y su retención.
        shutil.copy2(source, temporary)
        _validate_restore(temporary, source.with_suffix(".json"))
        if target.exists():
            current = sqlite3.connect(target)
            try:
                create_backup(current, reason="pre-restauracion", kind="pre", directory=directory)
            finally:
                current.close()
        _validate_restore(temporary, source.with_suffix(".json"))
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    return name
