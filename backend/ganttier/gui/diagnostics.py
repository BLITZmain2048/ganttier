"""Diagnóstico de bloqueos y cierres nativos (D03/D04 de la verificación del .exe, 08-10-2026).

Sin consola no queda rastro de por qué una ventana «no responde» o de un cierre inesperado en Qt. Este módulo escribe
en ``<datos>/logs/diagnostico.log``:

- las referencias a ``excepciones_nativas.log`` (faulthandler) y ``volcados/`` (fallos no controlados);
- la pila del hilo de la interfaz cuando el bucle de eventos no responde durante ``threshold`` segundos, y la
  duración total del bloqueo cuando se recupera.

No lee ni escribe datos del proyecto; el archivo es local, se rota al superar 2 MB y puede borrarse sin efectos.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

LOG_NAME = "diagnostico.log"
FAULT_LOG_NAME = "excepciones_nativas.log"
MAX_BYTES = 2 * 1024 * 1024
_fault_file = None


def log_path(data_dir: str | Path | None = None) -> Path:
    base = Path(data_dir or os.environ.get("GANTTIER_DATA_DIR") or Path.cwd())
    return base / "logs" / LOG_NAME


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size >= MAX_BYTES:
        os.replace(path, path.with_name(path.name + ".1"))
    return path.open("a", encoding="utf-8", buffering=1)


def enable_fault_log(data_dir: str | Path | None = None) -> Path | None:
    """Separa las SEH de primera oportunidad de los bloqueos confirmados de la interfaz."""
    global _fault_file
    path = log_path(data_dir)
    stream = None
    try:
        if _fault_file is not None and not _fault_file.closed:
            faulthandler.disable()
            _fault_file.close()
        header = f"\n=== inicio {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} ===\n"
        note = ("Windows: faulthandler también registra excepciones SEH de primera oportunidad; "
                "el texto 'fatal' no demuestra que el proceso haya terminado. Correlacionar con "
                "la salida del proceso y logs/volcados. Trazas: excepciones_nativas.log.\n")
        with _open_log(path) as log:
            log.write(header + note)
        stream = _open_log(path.with_name(FAULT_LOG_NAME))
        stream.write(header + note)
        faulthandler.enable(file=stream, all_threads=True)
        _fault_file = stream
    except (OSError, RuntimeError, ValueError):
        if stream is not None:
            stream.close()
        return None
    return path


def _format_stack(frame) -> str:
    return "".join(traceback.format_stack(frame)[-14:])


class UiWatchdog(QObject):
    """Detecta bloqueos del hilo de la interfaz y deja su pila en el archivo de diagnóstico."""

    def __init__(self, path: Path, threshold: float = 5.0, repeat: float = 30.0, parent=None):
        super().__init__(parent)
        self.path, self.threshold, self.repeat = Path(path), float(threshold), float(repeat)
        self.main_id = threading.get_ident()
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._stalled_since = None
        self._last_dump = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(max(20, int(self.threshold * 1000 / 10)))
        self.timer.timeout.connect(self._tick)
        self._vigilante = threading.Thread(target=self._watch, name="ganttier-watchdog", daemon=True)

    def start(self) -> "UiWatchdog":
        self._beat = time.monotonic()
        self.timer.start()
        self._vigilante.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.timer.stop()
        if self._vigilante.is_alive() and self._vigilante is not threading.current_thread():
            self._vigilante.join(timeout=2)

    def _tick(self) -> None:
        now = time.monotonic()
        if self._stalled_since is not None:
            self._write(f"[{time.strftime('%H:%M:%S')}] interfaz recuperada tras {now - self._stalled_since:.1f} s\n")
            self._stalled_since = None
        self._beat = now

    def _write(self, text: str) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(text)
        except OSError:
            pass

    def _watch(self) -> None:
        interval = max(0.05, self.threshold / 5)
        while not self._stop.wait(interval):
            now = time.monotonic()
            silent = now - self._beat
            if silent < self.threshold:
                continue
            if self._stalled_since is None:
                self._stalled_since = self._beat
            if now - self._last_dump < self.repeat:
                continue
            self._last_dump = now
            frames = sys._current_frames()
            lines = [f"[{time.strftime('%H:%M:%S')}] la interfaz no responde hace {silent:.1f} s; hilo principal:\n"]
            main = frames.get(self.main_id)
            lines.append(_format_stack(main) if main is not None else "  (sin pila)\n")
            others = [(t.name, frames.get(t.ident)) for t in threading.enumerate()
                      if t.ident not in (self.main_id, threading.get_ident()) and frames.get(t.ident) is not None]
            for name, frame in others[:8]:
                top = traceback.format_stack(frame)[-2:]
                lines.append(f"  hilo {name}:\n{''.join(top)}")
            self._write(''.join(lines))


def install(data_dir: str | Path | None = None, threshold: float = 5.0) -> UiWatchdog | None:
    """Activa el registro de fallos nativos y el vigilante; no hace nada si no se puede escribir el archivo."""
    path = enable_fault_log(data_dir)
    if path is None:
        return None
    try:
        from . import volcados
        volcados.install(path.parent / "volcados")
    except Exception as exc:  # el diagnóstico nunca debe impedir el arranque
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"No se pudo activar la captura de volcados: {exc}\n")
    return UiWatchdog(path, threshold).start()
