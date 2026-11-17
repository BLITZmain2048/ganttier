"""Bases de ESP bajo el --basetemp explícito del espacio compartido."""
from contextlib import closing
import os
from pathlib import Path

import pytest

from ganttier import db
from ganttier.optimizacion.espacio import redirigir_datos


@pytest.fixture
def esp_database(tmp_path, monkeypatch):
    allowed = Path(__file__).resolve().parents[3] / "trabajos" / "_temporales" / "cli_codex_20261019" / "ESP"
    if os.environ.get("GANTTIER_TESTS_TMP_ESTRICTO") == "1":  # opcional: aislamiento exigido por el encargo original
        assert tmp_path.resolve().is_relative_to(allowed.resolve()), "Ejecute con --basetemp dentro del temporal ESP."
    path = tmp_path / "prueba.db"
    data = tmp_path / "datos"
    monkeypatch.setenv("GANTTIER_DB", str(path))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(data))
    with redirigir_datos(data), closing(db.connect(path)) as conn:
        db.initialize(conn)
        conn.commit()
        yield conn, path, data
