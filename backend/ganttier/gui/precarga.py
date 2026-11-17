"""Precarga en segundo plano del arranque (investigación del arranque en frío, 11-10-2026).

Medición en Windows (12 corridas, ver ``trabajos/arranque_frio_20261011``): con una copia recién creada de la carpeta
de la aplicación la ventana tarda ~6,7 s (mediana) frente a ~2,9 s en caliente. El sobretiempo está en las fases que
importan ``services`` y la API (uvicorn/FastAPI/``ganttier.main``): ~0,9 s en caliente y ~3,5 s en fresco. Esas fases
leen cientos de archivos nuevos y el hilo de la interfaz espera mientras tanto.

Esta precarga importa esos módulos en un hilo secundario *mientras* el hilo principal inicializa Qt y pinta la
presentación (~1,3 s incluso en caliente). Sólo importa módulos del backend (sin Qt): no crea objetos de Qt, no abre la
base de datos ni escribe archivos. Si algo falla, el hilo principal repite la importación y muestra el error de siempre.

Modos (variable ``GANTTIER_PRECARGA``): ``importar`` (por defecto), ``archivos`` (sólo lee por adelantado los binarios
de la distribución con varios hilos para calentar la caché), ``ambos`` y ``no``.
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODULOS = ("uvicorn", "fastapi", "ganttier.db", "ganttier.services", "ganttier.main")
EXTENSIONES = {".pyd", ".dll", ".pak", ".dat"}
LIMITE_ARCHIVOS_S = 20.0

tiempos: dict[str, float] = {}
_hilos: list[threading.Thread] = []


def modo() -> str:
    valor = os.environ.get("GANTTIER_PRECARGA", "importar").strip().lower()
    return valor if valor in {"no", "importar", "archivos", "ambos"} else "importar"


def _importar() -> None:
    inicio = time.perf_counter()
    for nombre in MODULOS:
        try:
            importlib.import_module(nombre)
        except Exception:  # noqa: BLE001 - el hilo principal volverá a intentarlo y mostrará el error
            break
    tiempos["importar"] = time.perf_counter() - inicio


def _leer(ruta: Path) -> int:
    total = 0
    try:
        with ruta.open("rb") as flujo:
            while True:
                bloque = flujo.read(1 << 20)
                if not bloque:
                    return total
                total += len(bloque)
    except OSError:
        return total


def archivos_de_distribucion(raiz: Path | None = None) -> list[Path]:
    if raiz is None and getattr(sys, "frozen", False):
        raiz = Path(getattr(sys, "_MEIPASS", ""))
    if raiz is None or not Path(raiz).is_dir():
        return []
    return [p for p in Path(raiz).rglob("*") if p.suffix.lower() in EXTENSIONES and p.is_file()]


def _leer_por_adelantado(raiz: Path | None = None, hilos: int = 6) -> None:
    inicio = time.perf_counter()
    limite = inicio + LIMITE_ARCHIVOS_S
    rutas = archivos_de_distribucion(raiz)
    rutas.sort(key=lambda p: -p.stat().st_size if p.exists() else 0)
    with ThreadPoolExecutor(max_workers=hilos, thread_name_prefix="ganttier-precarga") as pool:
        for ruta in rutas:
            if time.perf_counter() > limite:
                break
            pool.submit(_leer, ruta)
    tiempos["archivos"] = time.perf_counter() - inicio
    tiempos["n_archivos"] = float(len(rutas))


def start(raiz: Path | None = None) -> list[threading.Thread]:
    """Lanza los hilos de precarga según el modo; devuelve los hilos creados (vacío con ``no``)."""
    elegido = modo()
    if elegido == "no" or _hilos:
        return []
    if elegido in {"archivos", "ambos"}:
        _hilos.append(threading.Thread(target=_leer_por_adelantado, args=(raiz,), name="ganttier-precarga-archivos", daemon=True))
    if elegido in {"importar", "ambos"}:
        _hilos.append(threading.Thread(target=_importar, name="ganttier-precarga-importar", daemon=True))
    for hilo in _hilos:
        hilo.start()
    return list(_hilos)


def report() -> str:
    if not tiempos and not _hilos:
        return f"[arranque] precarga: modo {modo()}\n"
    partes = [f"{k} {v:.2f} s" if k != "n_archivos" else f"{int(v)} archivos" for k, v in tiempos.items()]
    return f"[arranque] precarga (segundo plano, modo {modo()}): " + (", ".join(partes) or "sin terminar") + "\n"


def schedule_plot_runtime(win) -> None:
    """Prepara gráficos después del arranque visible y cuando no hay tareas.

    El temporizador pertenece a la ventana y solo coordina desde GUI. La carga
    usa el pool gestionado por el cierre de la aplicación, sin crear widgets ni
    consultar proyectos. Una carga del Gantt ya en curso tiene precedencia.
    """
    if modo() == "no" or os.environ.get("GANTTIER_PRECARGA_GRAFICA", "si").lower() == "no":
        return
    if getattr(win, "_plot_preload_timer", None) is not None:
        return
    from PySide6.QtCore import QCoreApplication, QTimer
    from PySide6.QtWidgets import QApplication
    from .widgets import common

    timer = QTimer(win)
    win._plot_preload_timer = timer
    timer.setInterval(250)

    def prepare():
        # Importar la página aquí conserva el arranque ligero. La preparación
        # de R1 tiene un lock e idempotencia compartidos con Control.
        from .pages.control import _prepare_plot_runtime
        start_time = time.perf_counter()
        try:
            _prepare_plot_runtime()
        finally:
            tiempos["graficos"] = time.perf_counter() - start_time

    def failed(exc):
        import logging
        logging.getLogger(__name__).warning("Precarga gráfica no disponible: %s", exc)

    def launch_when_idle():
        app = QCoreApplication.instance()
        lifecycle = getattr(app, "_ganttier_lifecycle", None)
        if (getattr(win, "_closed", False) or getattr(win, "_closing", False)
                or (lifecycle is not None and lifecycle.requested)):
            timer.stop()
            return
        # Un selector nativo bombea timers de Qt. Iniciar trabajo especulativo
        # dentro de ese modal alteraría el veto/consentimiento de cierre R4.
        if (not win.isVisible() or common._active
                or QApplication.activeModalWidget() is not None
                or QApplication.activePopupWidget() is not None):
            return
        timer.stop()
        common.run_task(prepare, on_error=failed)

    timer.timeout.connect(launch_when_idle)
    timer.start()
