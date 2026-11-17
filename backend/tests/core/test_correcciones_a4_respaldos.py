"""Regresiones A4-01/02/03/07/08/15; sólo bases desechables en tmp_path."""
import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier, Event

import pytest

# Las pruebas del servidor embebido crean QCoreApplication/QLockFile: sin pantalla en CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ganttier import backups, db, repo


def database(path, name="Original"):
    conn = db.connect(path)
    db.initialize(conn)
    repo.create(conn, "projects", {"name": name, "start_date": "2026-01-05"})
    return conn


def names(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM projects ORDER BY id")]
    finally:
        conn.close()


@pytest.fixture
def saved(tmp_path):
    target = tmp_path / "live.db"
    conn = database(target)
    try:
        info = backups.create_backup(conn)
        repo.update(conn, "projects", 1, {"name": "Vigente"})
    finally:
        conn.close()
    return target, backups.backup_dir(target) / info.file


def test_a4_01_foreign_marker_is_neither_applied_nor_cancelled(saved):
    target, source = saved
    other = target.with_name("other.db")
    database(other, "Otra base").close()
    marker = backups.schedule_restore(source.name, target)
    original = marker.read_bytes()
    assert backups.pending_restore(other) is None
    assert backups.apply_pending_restore(other) is None
    backups.cancel_restore(other)
    assert marker.read_bytes() == original
    assert names(other) == ["Otra base"]
    assert backups.pending_restore(target) == source.name


def test_a4_01_two_databases_keep_independent_pending_restores(saved):
    target, source = saved
    other = target.with_name("other.db")
    conn = database(other, "Otra base")
    try:
        second = backups.create_backup(conn)
    finally:
        conn.close()
    first_marker = backups.schedule_restore(source.name, target)
    second_marker = backups.schedule_restore(second.file, other)
    assert first_marker != second_marker
    assert backups.pending_restore(target) == source.name
    assert backups.pending_restore(other) == second.file
    backups.cancel_restore(other)
    assert first_marker.exists()
    assert not second_marker.exists()
    assert backups.apply_pending_restore(target) == source.name
    assert names(target) == ["Original"]
    assert names(other) == ["Otra base"]


def test_a4_01_canonical_target_and_legacy_marker(saved):
    target, source = saved
    marker = backups.backup_dir(target) / backups.PENDING
    alias = target.parent / "unused" / ".." / target.name
    marker.write_text(json.dumps({"file": source.name, "target": str(alias)}), encoding="utf-8")
    assert backups.pending_restore(target) == source.name
    backups.cancel_restore(target)
    assert not marker.exists()


def test_a4_02_manifest_records_size_and_sha256(saved):
    _, source = saved
    data = json.loads(source.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["size"] == source.stat().st_size > 0
    assert data["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize("when", ["schedule", "apply"])
@pytest.mark.parametrize("damage", ["empty", "size", "sha256"])
def test_a4_02_rejects_changed_content_without_touching_live_database(saved, when, damage):
    target, source = saved
    if when == "apply":
        backups.schedule_restore(source.name, target)
    if damage == "empty":
        source.write_bytes(b"")
    elif damage == "size":
        with source.open("ab") as stream:
            stream.write(b"changed")
    else:
        size = source.stat().st_size
        conn = sqlite3.connect(source)
        conn.execute("UPDATE projects SET name='Alterada'")
        conn.commit()
        conn.close()
        assert source.stat().st_size == size
        assert backups.verify_file(source)[0]  # SQLite válido, contenido distinto
    with pytest.raises(ValueError, match="vacío|tamaño|SHA-256"):
        if when == "apply":
            backups.apply_pending_restore(target)
        else:
            backups.schedule_restore(source.name, target)
    assert names(target) == ["Vigente"]
    assert not list(target.parent.glob("*.restaurando"))


@pytest.mark.parametrize("damage", ["schema", "version", "future", "foreign_keys", "integrity"])
def test_a4_02_rejects_invalid_legacy_backups(saved, damage):
    target, source = saved
    source.with_suffix(".json").unlink()
    if damage == "integrity":
        source.write_bytes(b"Esto no es una base SQLite" * 500)
    else:
        conn = sqlite3.connect(source)
        if damage == "schema":
            conn.execute("ALTER TABLE projects RENAME TO unrelated")
        elif damage == "version":
            conn.execute("DELETE FROM meta WHERE key='schema_version'")
        elif damage == "future":
            conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        else:
            conn.execute("CREATE TABLE orphan (parent INTEGER REFERENCES projects(id))")
            conn.execute("INSERT INTO orphan VALUES (999)")
        conn.commit()
        conn.close()
    with pytest.raises(ValueError, match="El respaldo no se puede restaurar"):
        backups.schedule_restore(source.name, target)
    assert names(target) == ["Vigente"]


@pytest.mark.parametrize("version_source", ["schema_version", "user_version"])
def test_a4_02_accepts_old_manifest_without_hash(saved, version_source):
    target, source = saved
    if version_source == "user_version":
        conn = sqlite3.connect(source)
        conn.execute("DELETE FROM meta WHERE key='schema_version'")
        conn.execute("PRAGMA user_version=1")
        conn.commit()
        conn.close()
    manifest = source.with_suffix(".json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["sha256"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert backups.list_backups(source.parent)[0].file == source.name
    backups.schedule_restore(source.name, target)
    assert backups.apply_pending_restore(target) == source.name
    assert names(target) == ["Original"]


def test_a4_02_verifies_the_staged_copy_before_pre_backup(saved, monkeypatch):
    target, source = saved
    backups.schedule_restore(source.name, target)
    copy = backups.shutil.copy2

    def corrupt(src, dst):
        copy(src, dst)
        Path(dst).write_bytes(b"")

    monkeypatch.setattr(backups.shutil, "copy2", corrupt)
    with pytest.raises(ValueError, match="vacío"):
        backups.apply_pending_restore(target)
    assert backups.last_backup(source.parent, "pre") is None
    assert names(target) == ["Vigente"]
    assert not list(target.parent.glob("*.restaurando"))


@pytest.mark.parametrize("kind,limit", [("pre", 10), ("manual", 20), ("auto", 40)])
def test_a4_03_retention_preserves_pending_backup(tmp_path, monkeypatch, kind, limit):
    class Clock(datetime):
        tick = datetime.now() - timedelta(days=90)

        @classmethod
        def now(cls):
            cls.tick += timedelta(days=1)
            return cls.tick

    monkeypatch.setattr(backups, "datetime", Clock)
    target = tmp_path / "live.db"
    conn = database(target)
    try:
        selected = backups.create_backup(conn, "primero", kind)
        backups.schedule_restore(selected.file, target)
        for i in range(limit):
            backups.create_backup(conn, str(i), kind)
    finally:
        conn.close()
    assert (backups.backup_dir(target) / selected.file).exists()
    assert backups.apply_pending_restore(target) == selected.file
    assert names(target) == ["Original"]


def test_a4_03_stages_source_before_pre_restore_backup(saved, monkeypatch):
    target, source = saved
    backups.schedule_restore(source.name, target)
    create = backups.create_backup

    def pre_backup(*args, **kwargs):
        staged = list(target.parent.glob("*.restaurando"))
        assert len(staged) == 1
        assert names(staged[0]) == ["Original"]
        result = create(*args, **kwargs)
        source.unlink()  # la restauración ya no depende del origen tras la retención
        return result

    monkeypatch.setattr(backups, "create_backup", pre_backup)
    assert backups.apply_pending_restore(target) == source.name
    assert names(target) == ["Original"]
    assert not list(target.parent.glob("*.restaurando"))


def test_a4_08_concurrent_backups_have_exclusive_names(saved, monkeypatch):
    target, _ = saved
    connect = backups.sqlite3.connect
    gate = Barrier(2)
    destinations = []

    class Clock(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 1, 5, 12)

    def synchronized_connect(destination, *args, **kwargs):
        result = connect(destination, *args, **kwargs)
        if str(destination).endswith(".db.tmp"):
            destinations.append(str(destination))
            gate.wait(timeout=5)
        return result

    monkeypatch.setattr(backups, "datetime", Clock)
    monkeypatch.setattr(backups.sqlite3, "connect", synchronized_connect)

    def create(_):
        conn = db.connect(target)
        try:
            return backups.create_backup(conn)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        copies = list(pool.map(create, range(2)))
    assert len(set(destinations)) == len({b.file for b in copies}) == 2
    for info in copies:
        source = backups.backup_dir(target) / info.file
        assert backups.verify_file(source)[0]
        assert names(source) == ["Vigente"]
        assert info.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.fixture
def qt_server(request):
    pytest.importorskip("PySide6")
    request.getfixturevalue("qapp")
    from ganttier.gui import server
    return server


def test_a4_07_lock_survives_timeout_and_is_released_automatically(tmp_path, monkeypatch, qt_server):
    import httpx
    from fastapi import FastAPI
    import ganttier.main as main

    target = tmp_path / "live.db"
    monkeypatch.setenv("GANTTIER_DB", str(target))
    entered, release = Event(), Event()

    def factory(token=None, instance=None):
        app = FastAPI()

        @app.get("/api/health")
        def health():
            return {"instance": instance}

        @app.get("/slow")
        def slow():
            entered.set()
            assert release.wait(10)
            return {"done": True}

        return app

    monkeypatch.setattr(main, "create_app", factory)
    server = qt_server.EmbeddedServer()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            server.start(timeout=5)
            response = pool.submit(httpx.get, server.base_url + "/slow", timeout=10)
            assert entered.wait(3)
            server.stop(timeout=0.01)
            assert server._thread.is_alive()
            assert server._socket is not None
            server.stop(timeout=0.01)  # cierre repetido tampoco libera el bloqueo
            with pytest.raises(qt_server.InstanceLocked):
                qt_server._lock_for(target)
            with pytest.raises(RuntimeError, match="sigue activo"):
                server.start()
            release.set()
            assert response.result(timeout=5).status_code == 200
            server._stop_watcher.join(5)
            assert not server._thread.is_alive()
            assert server._lock is None and server._socket is None
            acquired = qt_server._lock_for(target)
            acquired.unlock()
            server.start(timeout=5)  # el mismo objeto puede volver a iniciar
        finally:
            release.set()
            server.stop()


def test_a4_15_lock_creates_missing_parent(tmp_path, qt_server):
    target = tmp_path / "nueva" / "carpeta" / "live.db"
    lock = qt_server._lock_for(target)
    try:
        assert target.parent.is_dir()
        with pytest.raises(qt_server.InstanceLocked):
            qt_server._lock_for(target)
    finally:
        lock.unlock()


def test_a4_15_parent_creation_reports_real_error(tmp_path, monkeypatch, qt_server):
    def denied(*args, **kwargs):
        raise PermissionError("acceso denegado de prueba")

    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(OSError, match="acceso denegado de prueba") as caught:
        qt_server._lock_for(tmp_path / "nueva" / "live.db")
    assert not isinstance(caught.value, qt_server.InstanceLocked)


@pytest.mark.parametrize("error_name,exception", [("PermissionError", PermissionError), ("UnknownError", OSError)])
def test_a4_15_lock_errors_are_not_another_instance(tmp_path, monkeypatch, qt_server, error_name, exception):
    from PySide6 import QtCore
    errors = QtCore.QLockFile.LockError

    class FailedLock:
        LockError = errors

        def __init__(self, path):
            pass

        def setStaleLockTime(self, value):
            pass

        def tryLock(self, timeout):
            return False

        def error(self):
            return getattr(errors, error_name)

    monkeypatch.setattr(QtCore, "QLockFile", FailedLock)
    with pytest.raises(exception, match="permisos|UnknownError") as caught:
        qt_server._lock_for(tmp_path / "live.db")
    assert not isinstance(caught.value, qt_server.InstanceLocked)
