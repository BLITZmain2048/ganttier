"""Lectura y descarga del programa base para solicitudes de desconexión."""
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from .. import repo
from .dependencies import connection

router = APIRouter(tags=["Desconexiones"])


@router.get("/projects/{pid}/outages")
def outages(pid: int, start: date | None = Query(None, alias="from"),
            end: date | None = Query(None, alias="to"),
            conn: sqlite3.Connection = Depends(connection)):
    from ..reports.outages import build_program

    return repo.json_value(build_program(conn, pid, start, end))


@router.get("/projects/{pid}/outages.xlsx")
def outages_xlsx(pid: int, start: date | None = Query(None, alias="from"),
                 end: date | None = Query(None, alias="to"),
                 conn: sqlite3.Connection = Depends(connection)):
    from ..reports.outages import build_program, xlsx_bytes

    return Response(
        content=xlsx_bytes(build_program(conn, pid, start, end)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="desconexiones-{pid}.xlsx"'},
    )
