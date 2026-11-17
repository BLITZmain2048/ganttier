"""Las pruebas de GUI recolectan basura cíclica sólo en el hilo principal (ver ganttier/gui/recoleccion.py).

Con el recolector automático activo, un ciclo con un QObject/QWidget de una prueba anterior se destruye en el hilo del
servidor API y aborta el proceso («Timers cannot be stopped from another thread»), de forma intermitente.
"""
import gc

import pytest


@pytest.fixture(autouse=True, scope="session")
def _gc_solo_en_el_hilo_principal():
    gc.disable()
    yield
    gc.collect()
    gc.enable()


@pytest.fixture(autouse=True)
def _recolectar_al_terminar():
    yield
    gc.collect()
