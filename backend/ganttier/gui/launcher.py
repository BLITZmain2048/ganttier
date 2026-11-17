"""Arranque sin consola para la distribución PyInstaller y pythonw."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys


LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 4  # ganttier.log + 4 anteriores = máximo 5 archivos (P4-13a)


class RotatingStream:
    """Flujo de texto para stdout/stderr sin consola que rota ganttier.log al superar el tamaño máximo."""

    def __init__(self, path: Path, max_bytes: int = LOG_MAX_BYTES, backups: int = LOG_BACKUPS):
        self.path, self.max_bytes, self.backups = Path(path), max_bytes, backups
        self._file = None
        self._open()

    def _open(self):
        if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
            self._rotate_files()
        self._file = self.path.open("a", encoding="utf-8", buffering=1)

    def _rotate_files(self):
        for i in range(self.backups - 1, 0, -1):
            src, dst = self.path.with_name(f"{self.path.name}.{i}"), self.path.with_name(f"{self.path.name}.{i + 1}")
            if src.exists():
                os.replace(src, dst)
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    def write(self, text):
        try:
            if self._file.tell() >= self.max_bytes:
                self._file.close()
                self._rotate_files()
                self._file = self.path.open("a", encoding="utf-8", buffering=1)
            return self._file.write(text)
        except (OSError, ValueError):
            return 0

    def flush(self):
        try:
            self._file.flush()
        except (OSError, ValueError):
            pass

    def isatty(self):
        return False

    def close(self):
        try:
            self._file.close()
        except (OSError, ValueError):
            pass

    @property
    def closed(self):
        return self._file.closed

    def fileno(self):
        return self._file.fileno()


def configure_runtime():
    """Configurar antes de importar services (captura DATA_DIR al importarse)."""
    if getattr(sys, "frozen", False):
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Ganttier"
        os.environ.setdefault("GANTTIER_DATA_DIR", str(local))
        os.environ.setdefault("GANTTIER_DB", str(Path(os.environ["GANTTIER_DATA_DIR"]) / "ganttier.db"))
    data = Path(os.environ.get("GANTTIER_DATA_DIR", str(Path(__file__).resolve().parents[3] / "datos")))
    data.mkdir(parents=True, exist_ok=True)
    Path(os.environ.get("GANTTIER_DB", str(data / "ganttier.db"))).parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(data / "matplotlib"))
    os.environ.setdefault("QT_API", "pyside6")
    # uvicorn y otras bibliotecas llaman a isatty/write incluso sin consola.
    # pythonw/PyInstaller pueden proporcionar None para estos flujos.
    stream = None
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            stream = stream or RotatingStream(data / "ganttier.log")
            setattr(sys, name, stream)
    return data


def main():
    if sys.argv[1:2] == ["--ganttier-autoprueba"]:
        from ganttier.gui.autoprueba import ejecutar
        raise SystemExit(ejecutar(sys.argv[2:]))
    if sys.argv[1:2] == ["--ganttier-extraer-pdf"]:
        from ganttier.gui.arranque import ejecutar_lector_pdf
        raise SystemExit(ejecutar_lector_pdf(sys.argv[1:]))
    configure_runtime()
    # Mismo punto de entrada que python -m ganttier.gui, tras preparar los flujos.
    runpy.run_module("ganttier.gui", run_name="__main__")


if __name__ == "__main__":
    main()
