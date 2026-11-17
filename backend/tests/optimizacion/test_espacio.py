"""Copias WAL, exclusión y restauración del contexto de datos."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import ModuleType

import pytest

from ganttier import backups, db, repo, services
from ganttier.optimizacion import espacio


def test_copia_consistente_con_escritor_wal(esp_database):
    conn, path, data = esp_database
    conn.execute("PRAGMA wal_autocheckpoint=0")
    repo.create(conn, "projects", {"name": "Confirmado", "start_date": "2026-01-05"})
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE projects SET name='Sin confirmar'")
    assert Path(str(path) + "-wal").exists()
    with espacio.crear_copia(path, data) as scratch:
        assert scratch.data_dir != data and list(scratch.data_dir.iterdir()) == []
        with closing(db.connect(scratch.db_path)) as copied:
            assert copied.execute("SELECT name FROM projects").fetchone()[0] == "Confirmado"
            assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        root = scratch.root
    assert not root.exists()
    assert conn.execute("SELECT name FROM projects").fetchone()[0] == "Sin confirmar"
    conn.rollback()


def test_migracion_solo_copia(esp_database, tmp_path):
    _, _, data = esp_database
    old = tmp_path / "vieja.db"
    with closing(sqlite3.connect(old)) as conn:
        conn.executescript(Path(db.__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT)")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version','1')")
        conn.commit()
    original = old.read_bytes()
    with espacio.crear_copia(old, data) as scratch:
        with closing(sqlite3.connect(scratch.db_path)) as copy:
            assert int(copy.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == db.SCHEMA_VERSION
    assert old.read_bytes() == original


def test_limpieza_con_excepcion_y_permissionerror(esp_database, monkeypatch):
    _, path, data = esp_database
    real = espacio.shutil.rmtree
    attempts = []
    def busy_once(root):
        attempts.append(root)
        if len(attempts) == 1:
            raise PermissionError("antivirus simulado")
        return real(root)
    monkeypatch.setattr(espacio.shutil, "rmtree", busy_once)
    with pytest.raises(ValueError):
        with espacio.crear_copia(path, data) as scratch:
            root = scratch.root
            Path(str(scratch.db_path) + "-wal").touch()
            Path(str(scratch.db_path) + "-shm").touch()
            raise ValueError("fallo")
    assert not root.exists() and len(attempts) == 2


def test_conservar_y_cleanup_idempotente(esp_database):
    _, path, data = esp_database
    with espacio.crear_copia(path, data, conservar=True) as scratch:
        root = scratch.root
    assert root.exists()
    scratch._conservar = False
    scratch.cleanup()
    scratch.cleanup()
    assert not root.exists()


def test_bloqueo_gui_vivo_y_forzado(esp_database):
    _, path, _ = esp_database
    lock = Path(str(path) + ".lock")
    content = f"{os.getpid()}\nGanttier\nhost-prueba\n"
    lock.write_text(content, encoding="utf-8")
    state = espacio.estado_bloqueo(path)
    assert state.ocupado and state.por == "gui" and state.pid == os.getpid()
    with pytest.raises(espacio.BaseOcupada):
        with espacio.bloqueo_escritura(path):
            pytest.fail("Se admitió un bloqueo vivo")
    with espacio.bloqueo_escritura(path, forzar=True) as info:
        assert "forzó" in info.detalle
        assert Path(str(path) + ".cli.lock").exists()
    assert lock.read_text(encoding="utf-8") == content
    assert not Path(str(path) + ".cli.lock").exists()


def test_pid_muerto_y_recuperacion_cli(esp_database):
    _, path, _ = esp_database
    dead = 2147483647
    assert not espacio._pid_vivo(dead)
    Path(str(path) + ".lock").write_text(f"{dead}\nGUI\nhost\n", encoding="utf-8")
    lock = Path(str(path) + ".cli.lock")
    lock.write_text(f"{dead}\nCLI\nhost\n", encoding="utf-8")
    assert not espacio.estado_bloqueo(path).ocupado
    with pytest.raises(RuntimeError, match="fallo controlado"):
        with espacio.bloqueo_escritura(path):
            assert espacio.estado_bloqueo(path).por == "cli"
            with pytest.raises(espacio.BaseOcupada):
                with espacio.bloqueo_escritura(path, forzar=True):
                    pytest.fail("Otro CLI no se puede forzar")
            raise RuntimeError("fallo controlado")
    assert not lock.exists()


def test_redireccion_todos_modulos_anidada_y_excepcion(esp_database, monkeypatch, tmp_path):
    _, _, data = esp_database
    module = ModuleType("ganttier.esp_test_copia")
    module.DATA_DIR = data / "original"
    monkeypatch.setitem(sys.modules, module.__name__, module)
    original = services.DATA_DIR
    env = os.environ.get("GANTTIER_DATA_DIR")
    first, second = tmp_path / "primero", tmp_path / "segundo"
    late = ModuleType("ganttier.esp_test_tardio")
    with pytest.raises(ValueError):
        with espacio.redirigir_datos(first):
            assert module.DATA_DIR == services.DATA_DIR == first
            late.DATA_DIR = services.DATA_DIR
            monkeypatch.setitem(sys.modules, late.__name__, late)
            with espacio.redirigir_datos(second):
                assert module.DATA_DIR == services.DATA_DIR == late.DATA_DIR == second
            assert services.DATA_DIR == first
            raise ValueError("fallo")
    assert module.DATA_DIR == data / "original"
    assert services.DATA_DIR == late.DATA_DIR == original
    assert os.environ.get("GANTTIER_DATA_DIR") == env


def test_respaldo_verificado_y_directorio(esp_database, monkeypatch):
    _, path, data = esp_database
    calls = []
    original = backups.create_backup
    def recording(*args, **kwargs):
        calls.append(kwargs["kind"])
        return original(*args, **kwargs)
    monkeypatch.setattr(backups, "create_backup", recording)
    saved = espacio.respaldar(path, data, "prueba ESP")
    assert calls[0] == "cli"
    assert saved.parent == backups.backup_dir(path)
    assert backups.verify_file(saved) == (True, "ok")
    manifest = json.loads(saved.with_suffix(".json").read_text(encoding="utf-8"))
    assert manifest["verified"]
    assert not any(name.startswith("PySide6") for name in sys.modules)


def test_hash_solo_lectura(esp_database):
    conn, path, _ = esp_database
    pid = repo.create(conn, "projects", {"name": "Huella", "start_date": "2026-01-05"}).id
    first = espacio.hash_escenario(path, pid)
    assert first == services._scenario_fingerprint(conn, pid)
    repo.update(conn, "projects", pid, {"name": "Cambió"})
    assert espacio.hash_escenario(path, pid) != first


def test_bloqueo_entre_procesos_y_recuperacion_tras_muerte(esp_database):
    _, path, _ = esp_database
    child_code = """
import os, sys
from ganttier.optimizacion.espacio import bloqueo_escritura
with bloqueo_escritura(sys.argv[1]):
    print(os.getpid(), flush=True)
    sys.stdin.readline()
    os._exit(0)
"""
    child = subprocess.Popen([sys.executable, "-c", child_code, str(path)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        # El launcher de un venv Windows puede tener otro PID que Python.
        owner_pid = int(child.stdout.readline().strip())
        assert espacio.estado_bloqueo(path).pid == owner_pid
        with pytest.raises(espacio.BaseOcupada):
            with espacio.bloqueo_escritura(path, forzar=True):
                pytest.fail("Se admitió otro escritor")
        child.communicate("terminar\n", timeout=10)
        assert Path(str(path) + ".cli.lock").exists()
        with espacio.bloqueo_escritura(path):
            assert espacio.estado_bloqueo(path).pid == os.getpid()
        assert not Path(str(path) + ".cli.lock").exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
