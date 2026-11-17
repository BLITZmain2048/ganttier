"""La precarga no bloquea GUI, respeta las cargas y no revive una ventana cerrada."""
import threading
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget


def test_precarga_gestionada_sin_bloquear_gui(qtbot, monkeypatch):
    from ganttier.gui import precarga
    from ganttier.gui.pages import control
    from ganttier.gui.widgets import common
    entered, release = threading.Event(), threading.Event()
    threads=[];ticks=[]
    def prepare():
        threads.append(threading.get_ident());entered.set()
        assert release.wait(5)
    monkeypatch.setattr(control, '_prepare_plot_runtime', prepare)
    monkeypatch.setenv('GANTTIER_PRECARGA','importar')
    monkeypatch.delenv('GANTTIER_PRECARGA_GRAFICA',raising=False)
    win=QWidget();qtbot.addWidget(win);win.show()
    heartbeat=QTimer(win);heartbeat.timeout.connect(lambda:ticks.append(1));heartbeat.start(20)
    precarga.schedule_plot_runtime(win)
    timer=win._plot_preload_timer
    precarga.schedule_plot_runtime(win)
    assert win._plot_preload_timer is timer
    try:
        qtbot.waitUntil(entered.is_set,timeout=3000)
        n=len(ticks);qtbot.wait(150)
        assert len(ticks)>n+2 and common._active
        assert threads==[threads[0]] and threads[0]!=threading.get_ident()
    finally:
        release.set()
        qtbot.waitUntil(lambda:not common._active,timeout=5000)
    qtbot.wait(300)
    assert len(threads)==1 and not timer.isActive()


def test_precarga_espera_tareas_y_descarta_cierre(qtbot, monkeypatch):
    from ganttier.gui import precarga
    from ganttier.gui.widgets import common
    jobs=[]
    monkeypatch.setattr(common,'run_task',lambda fn,**kw:jobs.append(fn))
    monkeypatch.setattr(common,'_active',{-1})
    monkeypatch.setenv('GANTTIER_PRECARGA','importar')
    win=QWidget();qtbot.addWidget(win);win.show()
    precarga.schedule_plot_runtime(win);qtbot.wait(320)
    assert not jobs
    common._active.clear();qtbot.waitUntil(lambda:bool(jobs),timeout=1000)
    assert len(jobs)==1
    other=QWidget();qtbot.addWidget(other);other.show()
    precarga.schedule_plot_runtime(other);other._closed=True
    qtbot.wait(320)
    assert len(jobs)==1 and not other._plot_preload_timer.isActive()


def test_precarga_no_inicia_durante_modal(qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog, QApplication
    from ganttier.gui import precarga
    from ganttier.gui.widgets import common
    jobs=[]
    monkeypatch.setattr(common,'run_task',lambda fn,**kw:jobs.append(fn))
    monkeypatch.setattr(common,'_active',set())
    monkeypatch.setenv('GANTTIER_PRECARGA','importar')
    win=QWidget();qtbot.addWidget(win);win.show()
    modal=QDialog(win);modal.open()
    assert QApplication.activeModalWidget() is modal
    precarga.schedule_plot_runtime(win);qtbot.wait(350)
    assert not jobs, 'Una precarga especulativa no debe añadir tareas dentro de un cierre modal'
    modal.reject()
    qtbot.waitUntil(lambda:bool(jobs),timeout=1000)
    assert len(jobs)==1
