"""Clase base de las páginas de la ventana principal."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..state import AppState


class Page(QWidget):
    """Página de la barra lateral.

    Contrato:
    - ``title``: título mostrado en el encabezado.
    - ``activate()``: se llama cada vez que la página se muestra (recargar datos si están obsoletos).
    - Usa ``self.state.client`` para la API y ``widgets.common.run_task`` para llamadas lentas.
    - Tras modificar fechas o actividades llama ``self.state.bump_schedule()``.
    """

    title = ""
    subtitle = ""

    def actions(self) -> list:
        """Acciones para la paleta: (texto, ícono, función, atajo opcional)."""
        return []

    def __init__(self, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(14, 12, 14, 12)
        self.root.setSpacing(10)
        self._stale = True
        self._refresh_pending = False
        state.schedule_changed.connect(self._mark_stale)
        state.project_changed.connect(self._project_invalidated)

    @Slot(object)
    def _project_invalidated(self, _project) -> None:
        # Cambio de proyecto: la página visible se refresca de inmediato (descarta en el acto las
        # respuestas del proyecto anterior). La señal schedule_changed que AppState emite a
        # continuación en el mismo ciclo no provoca una segunda carga.
        self._suppress_next = False
        self._mark_stale()          # las páginas limpian aquí sus datos del proyecto anterior
        if self.isVisible():
            self.activate()
            self._suppress_next = True
            QTimer.singleShot(0, self._end_suppression)

    @Slot()
    def _end_suppression(self) -> None:
        self._suppress_next = False

    @Slot()
    def _mark_stale(self) -> None:
        self._stale = True
        if getattr(self, "_suppress_next", False):
            self._stale = False
            return
        if self.isVisible() and not self._refresh_pending:
            self._refresh_pending = True
            # El refresco diferido cuenta como trabajo pendiente: quien espere a que la GUI quede
            # inactiva (cierre, pruebas) no debe verla «libre» entre la escritura y la recarga.
            from ..widgets import common
            self._pending_token = -id(self)
            common._active.add(self._pending_token)
            QTimer.singleShot(0, self._refresh_if_visible)

    @Slot()
    def _refresh_if_visible(self) -> None:
        self._refresh_pending = False
        try:
            if self.isVisible():
                self.activate()
        finally:
            from ..widgets import common
            common._active.discard(getattr(self, "_pending_token", None))

    def activate(self) -> None:
        if self._stale:
            self._stale = False
            try:
                self.refresh()
            except Exception as exc:
                from ..widgets.common import report_error
                self._stale = True
                report_error(exc, self)

    def refresh(self) -> None:
        """Recarga los datos de la página (sobrescribir)."""
