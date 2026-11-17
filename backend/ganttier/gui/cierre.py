"""Cierre común de GUI y arranque; límite de salida incluso durante finalización."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QEventLoop, QObject, QTimer
from PySide6.QtWidgets import QApplication

SHUTDOWN_SECONDS = 45.0
NATIVE_SHUTDOWN_SECONDS = 8.0  # margen hasta el límite solicitado de 10 s
WM_CLOSE, WM_QUERYENDSESSION, WM_ENDSESSION = 0x0010, 0x0011, 0x0016


class StartupCancelled(Exception):
    """El usuario cerró la presentación mientras se iniciaba la API."""


class ExitGuard:
    """Se arma después de aceptar el cierre y no se desarma antes de salir.

    En Windows un supervisor PowerShell oculto conserva un handle del proceso y
    verifica su fecha de creación; puede actuar aunque Python retenga el GIL o Qt
    quede bloqueado durante sus destructores. Un hilo daemon es el respaldo.
    """
    def __init__(self, path: Path, timeout=SHUTDOWN_SECONDS):
        self.path = Path(path)
        self.timeout = timeout
        self.armed = False
        self.supervisor = None
        self.deadline = None

    def note(self, text):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(f"[{time.strftime('%H:%M:%S')}] pid={os.getpid()} "
                             f"t={time.monotonic():.3f} hilo={threading.get_ident()} {text}\n")
        except OSError:
            pass

    def arm(self):
        if self.armed:
            return
        self.armed = True
        self.deadline = time.monotonic() + self.timeout
        self.note(f"Cierre solicitado; límite de salida {self.timeout:g} s")
        # Arrancar antes del supervisor: su creación no consume el plazo del daemon.
        threading.Thread(target=self._fallback, name="ganttier-limite-cierre", daemon=True).start()
        if sys.platform == "win32":
            try:
                self._start_supervisor()
            except Exception as exc:
                self.note(f"Supervisor externo no disponible; queda vigilante daemon: {exc}")

    def _fallback(self):
        time.sleep(max(0, self.deadline - time.monotonic()))
        self.note("Salida forzada: el cierre ordenado superó el límite")
        os._exit(3)

    def _start_supervisor(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        created, exited, used_kernel, used_user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(used_kernel), ctypes.byref(used_user)):
            raise ctypes.WinError(ctypes.get_last_error())
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        quoted_path = str(self.path.resolve()).replace("'", "''")
        # Plazo absoluto: iniciar PowerShell no agrega segundos al presupuesto.
        deadline_ticks = int((time.time() + max(0, self.deadline - time.monotonic())
                              + 11644473600) * 10_000_000)
        script = f"""
$ErrorActionPreference = 'Stop'
try {{
    $target = [System.Diagnostics.Process]::GetProcessById({os.getpid()})
    $handle = $target.Handle
    if ($target.StartTime.ToUniversalTime().ToFileTimeUtc() -ne {ticks}) {{ exit }}
    $remaining = [Math]::Max(0, [Math]::Min(2147483647, ({deadline_ticks} - [DateTime]::UtcNow.ToFileTimeUtc()) / 10000))
    if (-not $target.WaitForExit([int]$remaining)) {{
        try {{ [System.IO.File]::AppendAllText('{quoted_path}', "`nSalida forzada: supervisor externo, pid={os.getpid()}`n") }} catch {{}}
        $target.Kill()
        $target.WaitForExit(3000) | Out-Null
    }}
    $target.Dispose()
}} catch {{ exit }}
"""
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.supervisor = subprocess.Popen([str(exe), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)


class _NativeDialogs:
    """Cancela sólo diálogos Win32 del proceso y de la cadena de propietarios.

    El trabajador nunca accede a widgets ni espera SendMessage. Reenumera porque
    cerrar un aviso puede habilitar al selector que antes ignoraba WM_CLOSE.
    """
    def __init__(self, owner, note):
        self.owner, self.note = owner, note
        self.stop = threading.Event()
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback_type, wintypes.LPARAM]
        self.user.EnumWindows.restype = wintypes.BOOL
        self.user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user.PostMessageW.restype = wintypes.BOOL

    def owned_dialogs(self):
        found = []
        @self.callback_type
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != os.getpid():
                return True
            name = ctypes.create_unicode_buffer(64)
            self.user.GetClassNameW(hwnd, name, len(name))
            if name.value != "#32770":
                return True
            owner, seen = hwnd, set()
            while owner and owner not in seen:
                if owner == self.owner:
                    found.append((len(seen), hwnd))
                    break
                seen.add(owner)
                owner = self.user.GetWindow(owner, 4)  # GW_OWNER
            return True
        self.user.EnumWindows(visit, 0)
        return [hwnd for _, hwnd in sorted(found, reverse=True)]

    def start(self):
        threading.Thread(target=self._run, name="ganttier-cancelar-dialogos", daemon=True).start()

    def _run(self):
        deadline, logged = time.monotonic() + NATIVE_SHUTDOWN_SECONDS, set()
        while not self.stop.is_set() and time.monotonic() < deadline:
            for hwnd in self.owned_dialogs():
                ok = self.user.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                if hwnd not in logged:
                    self.note(f"Cancelar diálogo nativo hwnd={hwnd}; WM_CLOSE enviado={bool(ok)}")
                    logged.add(hwnd)
            if self.stop.wait(.05):
                break


class _NativeClose(QAbstractNativeEventFilter):
    """Recibe la petición antes de depender de callbacks o temporizadores de Qt."""
    def __init__(self, lifecycle):
        super().__init__()
        self.lifecycle = lifecycle

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            win = self.lifecycle.win
            if (msg.message in (WM_CLOSE, WM_QUERYENDSESSION, WM_ENDSESSION)
                    and win is not None and int(win.winId()) == msg.hWnd):
                return self.lifecycle.native_request(msg.message, bool(msg.wParam))
        return False, 0


class Lifecycle(QObject):
    def __init__(self, app):
        super().__init__(app)
        from .diagnostics import log_path
        self.app = app
        self.guard = ExitGuard(log_path())
        self.splash = self.win = self.server = None
        self.requested = False
        self.starting = True
        self._cleaned = False
        self.native_pending = False
        self._dialogs = None
        self._native_previous_timeout = self.guard.timeout
        self._native_close = _NativeClose(self)
        app.installNativeEventFilter(self._native_close)
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.cleanup)

    def native_request(self, message, ending=False):
        win = self.win
        self.guard.note(f"Petición nativa mensaje=0x{message:04x}; ending={ending}")
        if message == WM_ENDSESSION and not ending:
            self.guard.note("Fin de sesión cancelado por Windows; no se inicia el cierre")
            return True, 0
        if message == WM_QUERYENDSESSION:
            # Consultar no autoriza salir: otra aplicación aún puede vetar el apagado.
            try:
                from .widgets import common
                safe = not (win._has_close_drafts() or common._active)
            except Exception as exc:
                safe = False
                self.guard.note(f"No se pudo comprobar el estado al consultar fin de sesión: {exc}")
            self.guard.note(f"WM_QUERYENDSESSION: permitido={safe}; sin iniciar limpieza")
            return True, int(safe)
        if self.native_pending or getattr(win, "_closing", False):
            return True, 0
        self.native_pending = True
        self._native_previous_timeout = self.guard.timeout
        try:
            win._save_close_state()
            consent = win._close_requires_consent()
            if message == WM_ENDSESSION:
                # Windows ya confirmó el fin; WM_QUERYENDSESSION es el punto de veto.
                win._session_ending = True
                if consent:
                    self.guard.note("Windows confirmó el fin de sesión con cambios pendientes; "
                                    "no existe veto en WM_ENDSESSION")
                consent = False
            self.guard.timeout = min(self.guard.timeout, NATIVE_SHUTDOWN_SECONDS)
            if consent:
                self.guard.note("Cierre pendiente de consentimiento; vigilante NO armado")
            else:
                self.guard.arm()
            self._dialogs = _NativeDialogs(int(win.winId()), self.guard.note)
            self._dialogs.start()
            if message == WM_ENDSESSION:
                # Tras retornar, Windows puede terminar el proceso inmediatamente.
                # Guardado y limpieza no pueden quedar pendientes de un QTimer.
                self._close_after_native(session_ending=True)
            else:
                QTimer.singleShot(0, self._close_after_native)
        except Exception as exc:
            self.native_pending = False
            if not self.guard.armed:
                self.guard.timeout = self._native_previous_timeout
            self.guard.note(f"No se pudo preparar el cierre nativo: {exc}; se conserva la ventana")
        return True, 0

    def _close_after_native(self, session_ending=False):
        if not session_ending and self._dialogs is not None and self._dialogs.owned_dialogs():
            QTimer.singleShot(25, self._close_after_native)
            return
        if self._dialogs is not None:
            self._dialogs.stop.set()  # no cancelar una confirmación posterior del usuario
        try:
            self.win.close()
        finally:
            # Cancelar/volver desde una confirmación no deja un cierre diferido activo.
            self.native_pending = False
            if not self.guard.armed:
                self.guard.timeout = self._native_previous_timeout
            if self._dialogs is not None:
                self._dialogs.stop.set()

    def eventFilter(self, watched, event):
        if self.starting and watched is self.splash and event.type() == QEvent.Close:
            self.requested = True
            self.guard.arm()
        return False

    def poll_startup(self):
        self.app.processEvents(QEventLoop.AllEvents, 20)
        if self.requested:
            raise StartupCancelled()

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self.requested = True
        self.guard.arm()
        try:
            if self.win is not None:
                self.win._shutdown()
            elif self.server is not None:
                self.server.stop()
        except Exception as exc:
            self.guard.note(f"Error durante el cierre: {exc}")
        finally:
            watchdog = getattr(self.app, "_ganttier_watchdog", None)
            if watchdog is not None:
                watchdog.stop()
            collector = getattr(self.app, "_ganttier_gc", None)
            if collector is not None:
                collector.stop(restore=False)
            self.guard.note("Limpieza de GUI terminada; esperando salida del proceso")
