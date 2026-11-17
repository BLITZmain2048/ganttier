"""P4-05a (instancia única) y P4-14 (token y socket reservado) del servidor embebido."""
import importlib.util
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
MISSING = [n for n in ("PySide6", "uvicorn", "httpx", "fastapi") if importlib.util.find_spec(n) is None]
pytestmark = pytest.mark.skipif(bool(MISSING), reason="Faltan dependencias: " + ", ".join(MISSING))


def test_segunda_instancia_sobre_la_misma_base_se_rechaza(tmp_path, monkeypatch):
    import httpx
    from ganttier.gui.server import EmbeddedServer, InstanceLocked
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "unica.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    first = EmbeddedServer()
    first.start()
    try:
        assert httpx.get(first.base_url + "/api/projects").status_code == 401
        ok = httpx.get(first.base_url + "/api/projects", headers={"X-Ganttier-Token": first.token})
        assert ok.status_code == 200
        second = EmbeddedServer()
        with pytest.raises(InstanceLocked, match="ya está abierto"):
            second.start()
        assert second._socket is None  # no dejó un puerto tomado
    finally:
        first.stop()
    third = EmbeddedServer()  # al cerrar la primera, el bloqueo se libera
    third.start()
    third.stop()


def test_puerto_preferido_ocupado_usa_otro_socket(tmp_path, monkeypatch):
    from ganttier.gui.server import EmbeddedServer, reserve_socket
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "puerto.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    busy = reserve_socket(8765, 30)
    try:
        server = EmbeddedServer(lock=False)
        server._preferred = busy.getsockname()[1]
        server.start()
        assert server.port != busy.getsockname()[1]
        server.stop()
    finally:
        busy.close()
