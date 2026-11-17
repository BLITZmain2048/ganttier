"""D03/D04: el diagnóstico deja la pila del hilo de la interfaz cuando se bloquea y registra fallos nativos."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _pump(qapp, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_vigilante_registra_la_pila_del_bloqueo_y_la_recuperacion(qapp, tmp_path):
    from PySide6.QtCore import QTimer
    from ganttier.gui.diagnostics import UiWatchdog

    log = tmp_path / "logs" / "diagnostico.log"
    log.parent.mkdir()
    dog = UiWatchdog(log, threshold=0.3, repeat=0.2).start()
    try:
        _pump(qapp, 0.5)
        assert not log.exists() or "no responde" not in log.read_text(encoding="utf-8")   # sin bloqueo no escribe
        QTimer.singleShot(0, lambda: time.sleep(1.2))   # bloquea el hilo de la interfaz
        _pump(qapp, 2.0)
        text = log.read_text(encoding="utf-8")
        assert "la interfaz no responde" in text and "time.sleep" in text or "sleep" in text
        assert "interfaz recuperada tras" in text
    finally:
        dog.stop()


def test_registro_de_fallos_nativos_apunta_al_archivo_de_diagnostico(tmp_path):
    import faulthandler
    from ganttier.gui import diagnostics

    path = diagnostics.enable_fault_log(tmp_path)
    try:
        assert path == tmp_path / "logs" / "diagnostico.log" and path.exists()
        assert faulthandler.is_enabled()
        assert "=== inicio" in path.read_text(encoding="utf-8")
    finally:
        faulthandler.disable()
        diagnostics._fault_file.close()
