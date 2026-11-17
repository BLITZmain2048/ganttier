"""Servidor API embebido: uvicorn en un hilo daemon sobre 127.0.0.1 y un puerto libre.

Seguridad local (P4-14) e instancia única (P4-05a):
- El socket se reserva ANTES de iniciar uvicorn y se le entrega abierto: no hay ventana entre
  «comprobar que el puerto está libre» y «enlazarlo».
- Cada inicio genera un token aleatorio de sesión (256 bits) que el cliente envía en la cabecera
  ``X-Ganttier-Token``; la API rechaza las peticiones sin él. Sólo se aceptan los Host 127.0.0.1 y
  localhost (protege contra *DNS rebinding* desde páginas web abiertas en el navegador).
- La verificación de arranque exige que /api/health devuelva el identificador de ESTA instancia.
- Un bloqueo de archivo (QLockFile) junto a la base impide abrir dos Ganttier sobre la misma base
  (dos escritores y dos programadores de informes). Un bloqueo de un proceso que ya no existe se
  recupera automáticamente.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

TOKEN_HEADER = "X-Ganttier-Token"


class InstanceLocked(RuntimeError):
    """Otra instancia de Ganttier ya usa esta base de datos."""


def free_port(preferred: int = 8765, tries: int = 20) -> int:
    """Primer puerto libre desde ``preferred``; si ninguno, uno asignado por el sistema."""
    sock = reserve_socket(preferred, tries)
    port = sock.getsockname()[1]
    sock.close()
    return port


def reserve_socket(preferred: int = 8765, tries: int = 20, host: str = "127.0.0.1") -> socket.socket:
    """Enlaza y deja en escucha el primer puerto libre; el socket se entrega abierto a uvicorn."""
    candidates = [*range(preferred, preferred + tries), 0] if preferred else [0]
    for port in candidates:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: nadie más puede compartir el puerto
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind((host, port))
            s.listen(2048)
            return s
        except OSError:
            s.close()
    raise RuntimeError("No hay puertos locales disponibles para el servidor de Ganttier.")


def _lock_for(path) -> object | None:
    """QLockFile sobre ``<base>.lock``; None si no hay Qt (p. ej., scripts)."""
    try:
        from PySide6.QtCore import QLockFile
    except ImportError:  # pragma: no cover - siempre presente en la GUI
        return None
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"No se pudo crear la carpeta de la base {path.parent}: {exc}") from exc
    lock = QLockFile(str(path) + ".lock")
    lock.setStaleLockTime(0)  # sólo es obsoleto si el proceso dueño ya no existe
    if not lock.tryLock(300):
        error = lock.error()
        if error == QLockFile.LockError.PermissionError:
            raise PermissionError(f"No hay permisos para crear o escribir el bloqueo de la base: {path}.lock")
        if error != QLockFile.LockError.LockFailedError:
            raise OSError(f"No se pudo crear el bloqueo de la base {path}.lock: {error}")
        who = ""
        try:  # PySide6 devuelve (pid, host, app) o (ok, pid, host, app) según la versión
            info = tuple(lock.getLockInfo())
            pid, host = (info[1], info[2]) if len(info) == 4 else (info[0], info[1])
            who = f" (proceso {pid}{' en ' + str(host) if host else ''})" if pid else ""
        except Exception:  # noqa: BLE001 - la información es sólo descriptiva
            pass
        raise InstanceLocked(f"Ganttier ya está abierto con esta base de datos{who}. Cierre la otra ventana "
                             f"de Ganttier o use otra base (variable GANTTIER_DB). Base: {path}")
    return lock


class EmbeddedServer:
    """Levanta la API de Ganttier dentro del proceso de la GUI."""

    def __init__(self, port: int | None = None, host: str = "127.0.0.1", *, lock: bool = True):
        self.host = host
        self._preferred = port or 8765
        self.port: int | None = port
        self.token = secrets.token_urlsafe(32)
        self.instance = uuid4().hex
        self._use_lock = lock
        self._lock = None
        self._socket: socket.socket | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._cleanup_lock = threading.Lock()
        self._stop_watcher: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _healthy(self) -> bool:
        request = Request(self.base_url + "/api/health", headers={TOKEN_HEADER: self.token})
        with urlopen(request, timeout=1) as r:
            body = json.loads(r.read() or b"{}")
            return r.status == 200 and body.get("instance") == self.instance

    def start(self, timeout: float = 30.0, *, process_events=None) -> str:
        import uvicorn
        from ganttier import db
        from ganttier.main import create_app

        with self._cleanup_lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("El servidor de Ganttier sigue activo o está terminando su cierre.")
            # Un vigilante anterior puede estar esperando este mutex. Completar su
            # limpieza antes de adquirir recursos para una nueva ejecución.
            self._release_resources()
            self._thread = None  # un vigilante anterior ya no puede limpiar el nuevo inicio
            self._stop_watcher = None
            if self._use_lock:
                self._lock = _lock_for(db.database_path())
        try:
            self._socket = reserve_socket(self._preferred, 20 if self.port is None else 1, self.host)
            self.port = self._socket.getsockname()[1]
            app = create_app(token=self.token, instance=self.instance)
            config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning", access_log=False)
            self._server = uvicorn.Server(config)
            self._server.install_signal_handlers = lambda: None  # hilo secundario: sin señales
            sockets = [self._socket]
            self._thread = threading.Thread(target=lambda: self._server.run(sockets=sockets), name="ganttier-api", daemon=True)
            self._thread.start()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process_events is not None:
                    process_events()  # sólo el llamador GUI procesa sus eventos
                try:
                    if self._healthy():
                        return self.base_url
                    raise RuntimeError(f"Otro servicio responde en {self.base_url}; no es esta instancia de Ganttier.")
                except OSError:
                    time.sleep(0.1)
            raise RuntimeError(f"El servidor de Ganttier no respondió en {self.base_url} dentro de {timeout:.0f} s.")
        except BaseException:
            self.stop()
            raise

    def stop(self, timeout: float = 5.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        with self._cleanup_lock:
            if thread is not self._thread:
                return
            if thread is not None and thread.is_alive():
                if self._stop_watcher is None or not self._stop_watcher.is_alive():
                    self._stop_watcher = threading.Thread(
                        target=self._wait_for_stop, args=(thread,), name="ganttier-api-cierre", daemon=True)
                    self._stop_watcher.start()
                return
            self._release_resources()

    def _wait_for_stop(self, thread: threading.Thread) -> None:
        thread.join()
        with self._cleanup_lock:
            if self._thread is thread:
                self._release_resources()

    def _release_resources(self) -> None:
        """Con el mutex tomado y sólo después de terminar el hilo del servidor."""
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None
