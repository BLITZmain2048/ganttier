"""Proceso GUI real y aislado para certificar cierre/reinicio en Windows.

Se invoca desde test_process_lifecycle.py, nunca sobre una base del usuario.
Los fallos quedan en JSON y stderr antes de terminar el proceso.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback


def main() -> int:
    from PySide6.QtCore import QSettings, QThreadPool, QTimer
    from PySide6.QtWidgets import QApplication
    from ganttier.gui.app import create_window
    from ganttier.gui.server import InstanceLocked
    from ganttier.gui.widgets import common

    mode, root_text, label = sys.argv[1:]
    root = Path(root_text)
    root.mkdir(parents=True, exist_ok=True)
    result_path = root / f"{label}.json"
    records: list[dict] = []
    started = time.monotonic()

    def record(stage: str, **values) -> None:
        records.append({"stage": stage, "elapsed_seconds": round(time.monotonic() - started, 4), **values})
        temporary = result_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(result_path)
        if stage == "ready_for_termination":
            (root / f"{label}.ready").touch()

    app = QApplication([sys.argv[0]])
    app.setOrganizationName("Ganttier")
    app.setApplicationName("GanttierCertificacion")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root / "settings"))
    errors = []
    common.show_error = lambda error, parent=None: errors.append(str(error))
    try:
        win, server = create_window(theme="light")
    except InstanceLocked as error:
        record("instance_locked", message=str(error))
        return 0 if mode == "duplicate" else 2
    if mode == "duplicate":
        win._shutdown()
        record("failure", message="La segunda instancia obtuvo una base ya bloqueada")
        return 3
    win.show()
    record("started", pid=os.getpid(), port=server.port, platform=QApplication.platformName())
    failures = []

    def close_window() -> None:
        record("close_requested", active_tasks=len(common._active))
        accepted = win.close()
        record("close_returned", accepted=accepted, active_tasks=len(common._active),
               active_threads=QThreadPool.globalInstance().activeThreadCount(),
               window_visible=win.isVisible(), window_enabled=win.isEnabled(),
               server_alive=server._thread.is_alive(), lock_held=server._lock is not None,
               client_closed=win.state.client._http.is_closed)

    def create(name: str):
        return win.state.client.create_project({"name": name, "mode": "proyecto", "start_date": "2026-10-06"})

    def begin() -> None:
        if common._active:
            QTimer.singleShot(20, begin)
            return
        try:
            if mode == "overdue":
                # Sonda separada: verifica si la espera documentada de 30 segundos
                # también acota la vida del proceso con un trabajo que tarda 45 s.
                def overdue_done(_):
                    record("overdue_callback")
                    if not win._closed:
                        QTimer.singleShot(0, close_window)

                common.run_task(lambda: time.sleep(45), overdue_done)
                QTimer.singleShot(0, close_window)
            elif mode == "pending":
                def work():
                    time.sleep(0.15)
                    return create(f"{label}:pending")

                def first_done(project):
                    record("first_write_committed", project_id=project["id"])
                    common.run_task(lambda: create(f"{label}:followup"),
                                    lambda value: record("followup_write_committed", project_id=value["id"]))

                common.run_task(work, first_done)
                QTimer.singleShot(0, close_window)
            elif mode == "hold":
                project = create(f"{label}:committed")
                # Inyección de una transacción SQLite interrumpida: simula una escritura
                # no confirmada al terminar el proceso, sin modificar código de producción.
                win._certification_transaction = sqlite3.connect(os.environ["GANTTIER_DB"])
                win._certification_transaction.execute("BEGIN IMMEDIATE")
                win._certification_transaction.execute("UPDATE projects SET name=? WHERE id=?",
                                                       (f"{label}:UNCOMMITTED", project["id"]))
                record("ready_for_termination", project_id=project["id"],
                       wal_present=Path(os.environ["GANTTIER_DB"] + "-wal").exists())
            else:
                project = create(f"{label}:committed")
                record("write_committed", project_id=project["id"])
                QTimer.singleShot(0, close_window)
        except BaseException:
            failures.append(traceback.format_exc())
            app.exit(4)

    QTimer.singleShot(0, begin)
    code = app.exec()
    win._shutdown()  # mismo cierre idempotente que usa main()
    record("finished", exit_code=code, errors=errors, failures=failures, closed=win._closed,
           server_alive=server._thread.is_alive(), socket_released=server._socket is None,
           lock_released=server._lock is None, client_closed=win.state.client._http.is_closed)
    return code if code else int(bool(errors or failures))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
