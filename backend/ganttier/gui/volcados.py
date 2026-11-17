"""Minidumps de excepciones Windows no controladas, sin Registro ni elevación.

DbgHelp se precarga y sólo se usa desde un escritor dedicado. El filtro conserva
EXCEPTION_POINTERS vivo mientras espera como máximo ocho segundos. Es diagnóstico
de mejor esfuerzo: un intérprete corrupto, stack overflow o fast-fail puede evitar
el callback ctypes. Se conserva faulthandler como diagnóstico independiente.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import threading
import time

KEEP = 5
_installed = None


def prune(folder: Path, keep: int = KEEP) -> None:
    """Retiene únicamente los últimos volcados propios; nunca borra archivos ajenos."""
    files = sorted(Path(folder).glob("ganttier-*.dmp"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for path in files[max(0, keep):]:
        try:
            path.unlink()
        except OSError:
            pass


class _ExceptionInfo(ctypes.Structure):
    _pack_ = 4  # ABI de minidumpapiset.h, también en Windows x64
    _fields_ = [("ThreadId", wintypes.DWORD), ("ExceptionPointers", ctypes.c_void_p),
                ("ClientPointers", wintypes.BOOL)]


class DumpWriter:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        prune(self.folder)
        self.kernel = k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.dbghelp = d = ctypes.WinDLL("dbghelp", use_last_error=True)
        k.GetCurrentProcess.argtypes, k.GetCurrentProcess.restype = [], wintypes.HANDLE
        k.GetCurrentThreadId.argtypes, k.GetCurrentThreadId.restype = [], wintypes.DWORD
        k.SetUnhandledExceptionFilter.argtypes = [ctypes.c_void_p]
        k.SetUnhandledExceptionFilter.restype = ctypes.c_void_p
        d.MiniDumpWriteDump.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE,
                                       wintypes.DWORD, ctypes.POINTER(_ExceptionInfo),
                                       ctypes.c_void_p, ctypes.c_void_p]
        d.MiniDumpWriteDump.restype = wintypes.BOOL
        self._request = threading.Event()
        self._done = threading.Event()
        self._info = None
        self._entered = False
        self._thread = threading.Thread(target=self._write_requested, name="ganttier-volcados", daemon=True)
        self._thread.start()
        self._callback = ctypes.WINFUNCTYPE(wintypes.LONG, ctypes.c_void_p)(self._unhandled)
        self._previous = k.SetUnhandledExceptionFilter(self._callback)

    def _unhandled(self, pointers):
        # No continuar ejecutando un proceso que ya tiene una excepción sin manejar.
        if self._entered:
            return 1  # EXCEPTION_EXECUTE_HANDLER
        self._entered = True
        try:
            self._info = _ExceptionInfo(self.kernel.GetCurrentThreadId(), pointers, False)
            self._request.set()
            self._done.wait(8.0)
        except BaseException:
            pass
        return 1

    def _write_requested(self):
        self._request.wait()
        try:
            self._write_dump()
        except BaseException as exc:
            self._note(f"Error al escribir el volcado: {exc}")
        finally:
            self._done.set()

    def _note(self, message):
        try:
            with (self.folder / "volcados.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} {message}\n")
        except OSError:
            pass

    def _write_dump(self):
        import msvcrt
        name = f"ganttier-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.time_ns()}.dmp"
        path = self.folder / name
        with path.open("xb") as stream:
            handle = msvcrt.get_osfhandle(stream.fileno())
            # MiniDumpNormal + MiniDumpWithThreadInfo; sin volcado completo de memoria.
            ok = self.dbghelp.MiniDumpWriteDump(self.kernel.GetCurrentProcess(), os.getpid(),
                                               handle, 0x1000, ctypes.byref(self._info), None, None)
            error = ctypes.get_last_error()
        if not ok:
            path.unlink(missing_ok=True)
            raise ctypes.WinError(error)
        self._note(f"Excepción no controlada; volcado guardado: {name}")
        prune(self.folder)


def install(folder: Path) -> DumpWriter | None:
    """Instala una vez por proceso; retiene el callback hasta que termine el proceso."""
    global _installed
    if sys.platform != "win32":
        return None
    if _installed is None:
        _installed = DumpWriter(folder)
    return _installed
