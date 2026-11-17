"""Router autónomo; el agregador de API agrega el prefijo /api."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import autoreports
from .dependencies import connection

router = APIRouter(tags=["Informes automáticos"])


def _exists(conn, pid):
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
        raise HTTPException(status_code=404, detail="No se encontró el proyecto solicitado.")


@router.get("/projects/{pid}/autoreports")
def get_config(pid: int, conn: sqlite3.Connection = Depends(connection)):
    _exists(conn, pid)
    return autoreports.get_config(conn, pid)


@router.put("/projects/{pid}/autoreports")
def put_config(pid: int, config: autoreports.AutoReportConfig,
               conn: sqlite3.Connection = Depends(connection)):
    _exists(conn, pid)
    return autoreports.set_config(conn, pid, config.model_dump())


@router.post("/projects/{pid}/autoreports/run")
def run_reports(pid: int, conn: sqlite3.Connection = Depends(connection)):
    _exists(conn, pid)
    return autoreports.run_project_reports(conn, pid)


@router.get("/projects/{pid}/autoreports/log")
def get_log(pid: int, conn: sqlite3.Connection = Depends(connection)):
    _exists(conn, pid)
    return autoreports.get_log(conn, pid)
