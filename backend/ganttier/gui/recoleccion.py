"""Recolección de basura cíclica sólo en el hilo de la interfaz (D04 de la verificación del .exe, 08-10-2026).

El recolector cíclico de Python se dispara en el hilo que esté asignando memoria. Si ese hilo es el del servidor API,
el de informes automáticos o un trabajador, y el ciclo contiene un QObject o un QWidget (un diálogo cerrado cuyo
envoltorio quedó en un ciclo, por ejemplo), Qt destruye el objeto C++ fuera de su hilo: «Timers cannot be stopped from
another thread» y cierre inmediato en Qt6Core.dll, sin traza de Python. Con el recolector automático desactivado y
ejecutado desde un temporizador del hilo de la interfaz, esas destrucciones ocurren siempre en el hilo propietario.

Los objetos sin ciclos se liberan por conteo de referencias, como siempre.
"""
from __future__ import annotations

import gc

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer

INTERVALO_MS = 1000
COMPLETA_CADA = 30  # recolección completa cada 30 pasadas (30 s); el resto, sólo generaciones jóvenes


def _require_ui_thread():
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise RuntimeError("La recolección debe ejecutarse en el hilo de la interfaz")


class Recolector(QObject):
    def __init__(self, parent: QObject | None = None, intervalo_ms: int = INTERVALO_MS):
        _require_ui_thread()
        super().__init__(parent)
        self._was_enabled = None
        self._pasadas = 0
        self.timer = QTimer(self)
        self.timer.setInterval(intervalo_ms)
        self.timer.timeout.connect(self.recolectar)

    def start(self) -> "Recolector":
        _require_ui_thread()
        if self._was_enabled is None:
            self._was_enabled = gc.isenabled()
        gc.disable()
        self.timer.start()
        return self

    def stop(self, *, restore: bool = True) -> None:
        _require_ui_thread()
        self.timer.stop()
        if restore and self._was_enabled is not None:
            gc.enable() if self._was_enabled else gc.disable()
            self._was_enabled = None
        # Al salir no se reactiva el GC mientras existan trabajadores y widgets.

    def recolectar(self) -> int:
        _require_ui_thread()
        self._pasadas += 1
        return gc.collect() if self._pasadas % COMPLETA_CADA == 0 else gc.collect(1)


def install(app: QObject, intervalo_ms: int = INTERVALO_MS) -> Recolector:
    """Desactiva el recolector automático y lo ejecuta periódicamente desde el hilo de la interfaz."""
    return Recolector(app, intervalo_ms).start()
