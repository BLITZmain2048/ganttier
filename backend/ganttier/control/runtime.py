"""QoS temporal del hilo que responde a una operación interactiva de Control.

Windows puede trasladar trabajo sin ventana propia a procesadores eficientes
aunque haya poca carga total. Se solicita HighQoS solo durante la respuesta y
se restaura la política exacta del hilo. No cambia prioridad, afinidad ni plan
de energía del proceso/equipo. En otras plataformas o API no disponible es no-op.
"""
from contextlib import contextmanager
from functools import lru_cache
import sys


@lru_cache(maxsize=1)
def _native():
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes
    class State(ctypes.Structure):
        _fields_ = [('version', wintypes.ULONG), ('control', wintypes.ULONG), ('state', wintypes.ULONG)]
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentThread.restype = wintypes.HANDLE
        for name in ('GetThreadInformation', 'SetThreadInformation'):
            function = getattr(kernel, name)
            function.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            function.restype = wintypes.BOOL
        return kernel, State, ctypes
    except (AttributeError, OSError):
        return None


@contextmanager
def interactive_calculation():
    """Preserva la política previa, también con excepciones o llamadas anidadas."""
    native = _native()
    if native is None:
        yield False
        return
    kernel, State, ctypes = native
    thread = kernel.GetCurrentThread()
    previous = State(1, 0, 0)
    # ThreadPowerThrottling = 3; EXECUTION_SPEED = 1. Si no podemos leer
    # la política, tampoco la cambiamos: es obligatorio poder restaurarla.
    if not kernel.GetThreadInformation(thread, 3, ctypes.byref(previous), ctypes.sizeof(previous)):
        yield False
        return
    requested = State(previous.version, previous.control | 1, previous.state & ~1)
    applied = bool(kernel.SetThreadInformation(thread, 3, ctypes.byref(requested), ctypes.sizeof(requested)))
    try:
        yield applied
    finally:
        if applied:
            kernel.SetThreadInformation(thread, 3, ctypes.byref(previous), ctypes.sizeof(previous))
