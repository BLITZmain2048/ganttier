"""Estado compartido de la GUI: cliente API, proyecto activo y catálogos, con señales Qt."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSettings, QThread, Qt, Signal, Slot

from .client import ApiClient


@dataclass(frozen=True)
class _ProjectBundle:
    project: dict | None
    calendars: list[dict]
    resources: list[dict]


def app_settings() -> QSettings:
    """Preferencias del usuario con el formato por defecto vigente.

    QSettings("Ganttier", "Ganttier") fuerza el formato nativo (registro de Windows) e ignora
    QSettings.setDefaultFormat; así, las pruebas y un eventual modo portable no podían redirigirlo a INI.
    """
    # Una ejecución aislada del binario puede conservar también las preferencias
    # en su carpeta, sin escribir ni restaurar el registro del usuario.
    directory = os.environ.get("GANTTIER_SETTINGS_DIR")
    if directory:
        return QSettings(str(Path(directory) / "Ganttier.ini"), QSettings.IniFormat)
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, "Ganttier", "Ganttier")


class AppState(QObject):
    """Una sola instancia por ventana. Las páginas se suscriben a sus señales.

    - ``project_changed(dict|None)``: se abrió/cerró/actualizó el proyecto activo.
    - ``schedule_changed()``: cambiaron fechas/actividades (tras CPM, optimizar, editar, importar).
    - ``catalogs_changed()``: cambiaron recursos o calendarios.
    - ``projects_changed()``: cambió la lista de proyectos.
    - ``operations_changed()``: cambió la exclusión de operaciones en GUI.

    Claves recomendadas: ``f"solve:{pid}"``, ``f"optimize:{pid}"``,
    ``f"import:{pid}"``. Adquirir/liberar desde GUI, siempre con ``end`` al terminar.
    """

    project_changed = Signal(object)
    schedule_changed = Signal()
    catalogs_changed = Signal()
    projects_changed = Signal()
    operations_changed = Signal()
    recent_projects_changed = Signal()
    _catalogs_ready = Signal(object)

    def __init__(self, client: ApiClient, settings=None):
        super().__init__()
        self.client = client
        self.settings = settings if settings is not None else app_settings()
        self.project: dict | None = None
        self.calendars: list[dict] = []
        self.resources: list[dict] = []
        self._generation = 0
        self._operations: set[str] = set()
        self._catalogs_ready.connect(self._publish_catalogs, Qt.QueuedConnection)

    def recent_projects(self) -> list[int]:
        values = self.settings.value("recent_projects", []) or []
        if not isinstance(values, (list, tuple)):
            values = [values]
        result = []
        for value in values:
            try:
                pid = int(value)
            except (TypeError, ValueError):
                continue
            if pid > 0 and pid not in result:
                result.append(pid)
        return result[:8]

    def touch_recent(self, pid: int) -> None:
        pid = int(pid)
        if pid <= 0:
            return
        recent = [pid] + [p for p in self.recent_projects() if p != pid]
        self.settings.setValue("recent_projects", recent[:8])
        self.recent_projects_changed.emit()

    @property
    def pid(self) -> int | None:
        return self.project["id"] if self.project else None

    @property
    def mode(self) -> str | None:
        return self.project.get("mode") if self.project else None

    def open_project(self, pid: int | None) -> None:
        """Apertura síncrona compatible con pruebas; propaga fallos sin mutar estado.

        Invocar en GUI. Para interacción de usuario usar ``open_project_async``.
        Cerrar (pid=None) invalida aperturas pendientes y no realiza peticiones REST.
        """
        self._generation += 1
        generation = self._generation
        bundle = self._load_project(self.client, pid)
        self._publish_project(generation, bundle)

    @staticmethod
    def _load_project(client: ApiClient, pid: int | None) -> _ProjectBundle:
        if pid is None:
            return _ProjectBundle(None, [], [])
        project = client.project(pid)
        calendars = client.calendars()
        resources = client.resources(pid)
        return _ProjectBundle(project, calendars, resources)

    def _publish_project(self, generation: int, bundle: _ProjectBundle) -> bool:
        if generation != self._generation:
            return False
        self.project, self.calendars, self.resources = bundle.project, bundle.calendars, bundle.resources
        self.settings.setValue("last_project", self.pid or "")
        if self.pid is not None:
            self.touch_recent(self.pid)
            from .pages.control import prefetch_dashboard
            prefetch_dashboard(self.client, self.pid)
        self.project_changed.emit(self.project)
        self.catalogs_changed.emit()
        self.schedule_changed.emit()
        return True

    def cancel_pending_open(self) -> None:
        """A3-07: invalida una apertura en curso (p. ej., el usuario volvió a elegir el proyecto vigente)."""
        self._generation += 1

    def open_project_async(self, pid: int | None, on_done: Callable | None = None,
                           on_error: Callable | None = None) -> None:
        """Carga el paquete completo fuera de GUI y publica solo la última apertura."""
        from .widgets.common import report_error, run_task
        self._generation += 1
        generation, client = self._generation, self.client

        def done(bundle):
            if self._publish_project(generation, bundle) and on_done:
                on_done(bundle.project)

        def failed(exc):
            if generation == self._generation:
                (on_error or report_error)(exc)

        if pid is None:
            try:
                done(_ProjectBundle(None, [], []))
            except Exception as exc:
                report_error(exc)
        else:
            run_task(lambda: self._load_project(client, pid), done, failed, busy="Abriendo proyecto…")

    def reload_project(self) -> None:
        pid, generation = self.pid, self._generation
        if pid is not None:
            project = self.client.project(pid)
            if generation != self._generation or pid != self.pid:
                return
            self.project = project
            self.project_changed.emit(self.project)

    def reload_catalogs(self, emit: bool = True) -> None:
        """Lee con un pid capturado; publica ambos catálogos juntos en GUI.

        Compatible con llamadas existentes desde trabajadores: en ese caso la
        publicación se encola, y se descarta si entretanto cambió la apertura.
        """
        pid, generation, client = self.pid, self._generation, self.client
        calendars = client.calendars()
        resources = client.resources(pid) if pid is not None else client.resources()
        payload = (generation, pid, calendars, resources, emit)
        if QThread.currentThread() == self.thread():
            self._publish_catalogs(payload)
        else:
            self._catalogs_ready.emit(payload)

    @Slot(object)
    def _publish_catalogs(self, payload) -> None:
        generation, pid, calendars, resources, emit = payload
        if generation != self._generation or pid != self.pid:
            return
        self.calendars, self.resources = calendars, resources
        if emit:
            self.catalogs_changed.emit()

    def try_begin(self, key: str) -> bool:
        """Reserva una operación en GUI; False si otra ya usa exactamente la clave."""
        if key in self._operations:
            return False
        self._operations.add(key)
        self.operations_changed.emit()
        return True

    def end(self, key: str) -> None:
        if key in self._operations:
            self._operations.remove(key)
            self.operations_changed.emit()

    def is_busy(self, key: str) -> bool:
        return key in self._operations

    def skills(self) -> list[str]:
        return sorted({s for r in self.resources for s in (r.get("skills") or [])})

    def bump_schedule(self) -> None:
        self.schedule_changed.emit()
