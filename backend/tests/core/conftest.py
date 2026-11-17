"""Pruebas aisladas en el directorio temporal de pytest (o en GANTTIER_TEST_TMP si se define)."""
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from ganttier import db, repo


@pytest.fixture
def local_dir(tmp_path):
    base = os.environ.get("GANTTIER_TEST_TMP")
    if not base:
        yield tmp_path
        return
    path = Path(base) / uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def conn(local_dir):
    connection = db.connect(local_dir / "test.db")
    db.initialize(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def project(conn):
    cid = repo.list_entities(conn, "calendars")[4].id
    return repo.create(conn, "projects", {"name": "Proyecto de prueba", "start_date": "2026-01-01", "end_date": "2026-12-31", "data_date": "2026-01-15", "calendar_id": cid})
