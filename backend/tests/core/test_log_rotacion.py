"""P4-13a: ganttier.log rota y nunca supera 5 archivos."""
from ganttier.gui.launcher import RotatingStream


def test_rotacion_limita_archivos_y_tamano(tmp_path):
    log = tmp_path / "ganttier.log"
    stream = RotatingStream(log, max_bytes=1000, backups=4)
    for i in range(400):
        stream.write(f"linea {i:05d} " + "x" * 40 + "\n")
    stream.flush()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["ganttier.log", "ganttier.log.1", "ganttier.log.2", "ganttier.log.3", "ganttier.log.4"]
    assert all(p.stat().st_size <= 1100 for p in tmp_path.iterdir())
    assert "linea 00399" in log.read_text(encoding="utf-8")
    assert not stream.isatty()
