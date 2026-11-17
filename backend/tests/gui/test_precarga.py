"""Precarga en segundo plano del arranque (arranque en frío)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib
import sys

from ganttier.gui import arranque, precarga


def _reiniciar(monkeypatch, modo):
    monkeypatch.setenv("GANTTIER_PRECARGA", modo)
    precarga._hilos.clear()
    precarga.tiempos.clear()


def test_modo_por_defecto_y_valores_invalidos(monkeypatch):
    monkeypatch.delenv("GANTTIER_PRECARGA", raising=False)
    assert precarga.modo() == "importar"
    monkeypatch.setenv("GANTTIER_PRECARGA", "cualquier cosa")
    assert precarga.modo() == "importar"
    monkeypatch.setenv("GANTTIER_PRECARGA", "NO")
    assert precarga.modo() == "no"


def test_modo_no_no_crea_hilos(monkeypatch):
    _reiniciar(monkeypatch, "no")
    assert precarga.start() == []


def test_importar_termina_y_registra_el_tiempo(monkeypatch):
    _reiniciar(monkeypatch, "importar")
    hilos = precarga.start()
    assert len(hilos) == 1
    hilos[0].join(60)
    assert not hilos[0].is_alive() and "importar" in precarga.tiempos
    assert all(nombre in sys.modules for nombre in precarga.MODULOS)
    assert "precarga" in precarga.report()


def test_un_modulo_inexistente_no_rompe_el_hilo(monkeypatch):
    _reiniciar(monkeypatch, "importar")
    monkeypatch.setattr(precarga, "MODULOS", ("modulo_que_no_existe_ganttier",))
    precarga.start()[0].join(10)
    assert "importar" in precarga.tiempos


def test_leer_por_adelantado_recorre_la_distribucion(monkeypatch, tmp_path):
    (tmp_path / "a.pyd").write_bytes(b"x" * 3000)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.dll").write_bytes(b"y" * 10)
    (tmp_path / "c.txt").write_text("no")
    assert sorted(p.name for p in precarga.archivos_de_distribucion(tmp_path)) == ["a.pyd", "b.dll"]
    _reiniciar(monkeypatch, "archivos")
    precarga.start(tmp_path)[0].join(10)
    assert precarga.tiempos["n_archivos"] == 2.0


def test_el_informe_de_arranque_incluye_la_precarga(monkeypatch):
    _reiniciar(monkeypatch, "importar")
    modulo = importlib.reload(arranque)
    modulo.mark("fase")
    assert "[arranque] precarga" in modulo.report()
