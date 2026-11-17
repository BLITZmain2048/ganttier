"""Tiempos de arranque por fase (investigación del arranque en frío, 11-10-2026).

En el .exe recién construido algunos arranques dejaron la ventana sin aparecer durante 8–11 s y otros tardaron 2–4 s.
Sin consola no hay forma de saber qué fase absorbe la diferencia (carga de DLL de Qt, importación de la API, tema,
construcción de la ventana). Este módulo marca cada fase con ``perf_counter`` y escribe el resumen en
``<datos>/logs/diagnostico.log`` al aparecer la ventana. En Windows incluye el tiempo desde la creación del proceso
(cargador de PyInstaller, inicio del intérprete) hasta el primer código de Ganttier.

No lee ni escribe datos del proyecto y no cambia el comportamiento de la aplicación.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_T0 = time.perf_counter()
_marks: list[tuple[str, float]] = []


def process_age() -> float | None:
    """Segundos desde la creación del proceso (sólo Windows); None si no se puede determinar."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetCurrentProcess.argtypes = []
        # Sin argtypes, ctypes convierte el seudo-identificador de 64 bits a int de 32 bits y lanza OverflowError
        # (medido en el .exe del 11-10-2026: process_age() devolvía siempre None).
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return max(0.0, time.time() - (ticks / 1e7 - 11644473600))
    except Exception:  # noqa: BLE001 - la medición nunca debe impedir el arranque
        return None


_AGE0 = process_age()


def mark(name: str) -> None:
    _marks.append((name, time.perf_counter()))


def report() -> str:
    """Resumen legible: fase, duración de la fase y tiempo acumulado desde el primer código de Ganttier."""
    lines = [f"[arranque] {time.strftime('%H:%M:%S')} frozen={bool(getattr(sys, 'frozen', False))}"]
    if _AGE0 is not None:
        lines.append(f"[arranque] el proceso llevaba {_AGE0:.2f} s creado al ejecutarse el primer código de Ganttier")
    previous = _T0
    for name, stamp in _marks:
        lines.append(f"[arranque] {name}: +{stamp - previous:.2f} s (acumulado {stamp - _T0:.2f} s)")
        previous = stamp
    if _marks:
        total = _marks[-1][1] - _T0 + (_AGE0 or 0.0)
        lines.append(f"[arranque] total desde la creación del proceso hasta «{_marks[-1][0]}»: {total:.2f} s"
                     if _AGE0 is not None else f"[arranque] total hasta «{_marks[-1][0]}»: {total:.2f} s")
    try:
        from . import precarga
        extra = precarga.report().rstrip("\n")
    except Exception:  # noqa: BLE001
        extra = ""
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def write_report(path: str | Path) -> bool:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(report())
        return True
    except OSError:
        return False


def ejecutar_lector_pdf(argv: list[str]) -> int:
    """Extrae texto con QtPdf en el proceso auxiliar, sin ventana ni servidor API.

    El resultado se escribe en JSON: un ejecutable windowed no garantiza stdout.
    Este modo sólo se invoca desde el importador con rutas locales temporales.
    """
    if len(argv) != 3 or argv[0] != "--ganttier-extraer-pdf":
        return 2
    import json
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtPdf import QPdfDocument
    origen, destino = map(Path, argv[1:])
    if not origen.is_file() or origen.stat().st_size > 30 * 1024 * 1024:
        return 2
    app = QGuiApplication.instance() or QGuiApplication([])
    documento = QPdfDocument()
    try:
        if documento.load(str(origen)) != QPdfDocument.Error.None_ or not 0 < documento.pageCount() <= 100:
            return 2
        paginas = [documento.getAllText(i).text() for i in range(documento.pageCount())]
        destino.write_text(json.dumps(paginas, ensure_ascii=False), encoding="utf-8")
        return 0
    finally:
        documento.close()
