"""D04: ningún QObject cíclico se destruye fuera del hilo de la interfaz."""
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

GUION = textwrap.dedent("""
    import os, sys, threading
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {backend!r})
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton
    from PySide6.QtCore import QTimer
    app = QApplication([])
    if {instalar!r}:
        from ganttier.gui import recoleccion
        recoleccion.install(app)
    class Dlg(QDialog):
        def __init__(self):
            super().__init__(); self.btn = QPushButton("x", self); self.yo = self   # ciclo
            self.t = QTimer(self); self.t.timeout.connect(lambda: None); self.t.start(1000)
    for _ in range(200):
        d = Dlg(); d.show(); del d
    def otro_hilo():
        for _ in range(400000):
            a = []; a.append(a)      # basura cíclica: dispara el recolector automático en este hilo
    th = threading.Thread(target=otro_hilo); th.start(); th.join()
    print("fin")
""")


def _ejecutar(instalar: bool):
    return subprocess.run([sys.executable, "-c", GUION.format(backend=str(BACKEND), instalar=instalar)],
                          capture_output=True, text=True, timeout=120)


def test_con_el_recolector_de_la_gui_no_se_destruyen_qobject_en_otros_hilos():
    r = _ejecutar(True)
    assert r.returncode == 0 and "fin" in r.stdout, r.stderr[-500:]
    assert "another thread" not in r.stderr


def test_sin_el_recolector_de_la_gui_el_problema_existe():
    """Control: documenta por qué existe el módulo (si Qt dejara de quejarse, este control se puede retirar)."""
    r = _ejecutar(False)
    assert "another thread" in r.stderr or r.returncode != 0


def test_recolector_ejecuta_y_se_detiene():
    import gc
    from PySide6.QtWidgets import QApplication
    from ganttier.gui import recoleccion
    app = QApplication.instance() or QApplication([])
    estaba = gc.isenabled()
    rec = recoleccion.install(app, intervalo_ms=20)
    try:
        assert not gc.isenabled()
        assert isinstance(rec.recolectar(), int)
    finally:
        rec.stop()
        gc.disable() if not estaba else gc.enable()
