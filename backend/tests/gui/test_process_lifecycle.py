"""Certificación de procesos GUI con base, preferencias y archivos aislados.

No simula closeEvent ni el servidor: crea QApplication/ventana/API en procesos
independientes, observa su salida y vuelve a abrir la misma base SQLite.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import pytest


DRIVER = Path(__file__).with_name("lifecycle_driver.py")
BACKEND = Path(__file__).resolve().parents[2]


def _launch(root: Path, mode: str, label: str):
    root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"QT_QPA_PLATFORM": "offscreen", "GANTTIER_DB": str(root / "ganttier.db"),
                "GANTTIER_DATA_DIR": str(root / "data"), "MPLCONFIGDIR": str(root / "matplotlib"),
                "PYTHONPATH": str(BACKEND), "PYTHONUNBUFFERED": "1"})
    env.pop("GANTTIER_API_URL", None)
    env.pop("GANTTIER_API_TOKEN", None)
    stdout = (root / f"{label}.stdout.log").open("w", encoding="utf-8")
    stderr = (root / f"{label}.stderr.log").open("w", encoding="utf-8")
    try:
        process = subprocess.Popen([sys.executable, "-B", str(DRIVER), mode, str(root), label],
                                   env=env, cwd=BACKEND, stdout=stdout, stderr=stderr,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        stdout.close()
        stderr.close()
    return process


def _events(root: Path, label: str) -> list[dict]:
    return json.loads((root / f"{label}.json").read_text(encoding="utf-8"))


def _wait_success(process, root: Path, label: str, timeout=60):
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        pytest.fail(f"{label}: proceso no terminó en {timeout}s; ver {root}")
    assert code == 0, (root / f"{label}.stderr.log").read_text(encoding="utf-8")
    return _events(root, label)


def _assert_closed(events):
    finished = events[-1]
    assert finished["stage"] == "finished" and finished["exit_code"] == 0
    assert not finished["errors"] and not finished["failures"]
    assert finished["closed"] and finished["client_closed"]
    assert not finished["server_alive"]
    assert finished["socket_released"] and finished["lock_released"]
    closed = next(event for event in events if event["stage"] == "close_returned")
    assert closed["accepted"] and closed["active_tasks"] == closed["active_threads"] == 0
    port = events[0]["port"]
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def _assert_database(root: Path, expected_names: list[str]):
    with sqlite3.connect(root / "ganttier.db") as conn:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        names = [row[0] for row in conn.execute("SELECT name FROM projects ORDER BY id")]
    assert names == expected_names
    assert not (root / "ganttier.db.lock").exists()


def test_three_real_gui_processes_close_and_reopen_same_database(tmp_path):
    names = []
    for cycle in range(1, 4):
        label = f"clean_{cycle}"
        events = _wait_success(_launch(tmp_path, "clean", label), tmp_path, label)
        _assert_closed(events)
        names.append(f"{label}:committed")
        _assert_database(tmp_path, names)


def test_closing_waits_for_committed_api_writes_and_chained_gui_callback(tmp_path):
    label = "pending"
    events = _wait_success(_launch(tmp_path, label, label), tmp_path, label)
    _assert_closed(events)
    stages = [event["stage"] for event in events]
    assert stages.index("close_requested") < stages.index("first_write_committed")
    assert stages.index("first_write_committed") < stages.index("followup_write_committed") < stages.index("close_returned")
    assert next(e for e in events if e["stage"] == "close_requested")["active_tasks"] > 0
    _assert_database(tmp_path, ["pending:pending", "pending:followup"])


def test_second_process_is_rejected_and_abrupt_termination_recovers_lock_and_transaction(tmp_path):
    holder = _launch(tmp_path, "hold", "crash")
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert holder.poll() is None, (tmp_path / "crash.stderr.log").read_text(encoding="utf-8")
            if (tmp_path / "crash.ready").exists():
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"El proceso no quedó listo: {tmp_path}")
        assert _events(tmp_path, "crash")[-1]["wal_present"]
        duplicate = _wait_success(_launch(tmp_path, "duplicate", "duplicate"), tmp_path, "duplicate")
        assert duplicate[-1]["stage"] == "instance_locked"
        assert "ya está abierto" in duplicate[-1]["message"]
        assert holder.poll() is None
        holder.kill()  # TerminateProcess en Windows: no finally, closeEvent ni shutdown.
        holder.wait(timeout=10)
        assert holder.returncode != 0
        assert (tmp_path / "ganttier.db.lock").exists(), "La terminación abrupta debe dejar un lock obsoleto"
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
    events = _wait_success(_launch(tmp_path, "clean", "recovered"), tmp_path, "recovered")
    _assert_closed(events)
    _assert_database(tmp_path, ["crash:committed", "recovered:committed"])
