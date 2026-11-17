"""Tiempos de arranque por fase y pantalla de presentación (arranque en frío)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib

from PySide6.QtWidgets import QApplication

from ganttier.gui import arranque


def test_informe_lista_las_fases_con_duracion_y_acumulado(tmp_path, monkeypatch):
    modulo = importlib.reload(arranque)
    modulo.mark("fase uno")
    modulo.mark("fase dos")
    texto = modulo.report()
    assert "fase uno: +" in texto and "fase dos: +" in texto and "acumulado" in texto
    destino = tmp_path / "logs" / "diagnostico.log"
    assert modulo.write_report(destino) is True
    assert "[arranque]" in destino.read_text(encoding="utf-8")


def test_edad_del_proceso_solo_en_windows():
    if os.name != "nt":
        assert arranque.process_age() is None
    else:
        assert arranque.process_age() >= 0.0


def test_presentacion_se_muestra_y_se_puede_desactivar(monkeypatch):
    from ganttier.gui.app import _mostrar_presentacion
    app = QApplication.instance() or QApplication([])
    monkeypatch.delenv("GANTTIER_SIN_PRESENTACION", raising=False)
    splash = _mostrar_presentacion(app)
    assert splash is not None and splash.isVisible()
    splash.close()
    monkeypatch.setenv("GANTTIER_SIN_PRESENTACION", "1")
    assert _mostrar_presentacion(app) is None
