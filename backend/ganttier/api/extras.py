"""Búsqueda transversal y ausencias personales (P5-06 y P5-10)."""
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import resource_absences, search
from .dependencies import connection

router = APIRouter(tags=["Búsqueda y ausencias"])


class Absence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: date
    end: date
    hours: float = Field(default=0, ge=0, le=24)
    reason: str = Field(min_length=1, max_length=200)


@router.get("/search")
def search_all(q: str = Query(..., min_length=2), limit: int = Query(50, ge=1, le=200),
               offset: int = Query(0, ge=0), conn: sqlite3.Connection = Depends(connection)):
    return search.search(conn, q, limit, offset)


@router.post("/resources/{rid}/absences")
def add_absence(rid: int, body: Absence, dry_run: bool = False,
                conn: sqlite3.Connection = Depends(connection)):
    return resource_absences.set_absence(conn, rid, **body.model_dump(), dry_run=dry_run)


@router.get("/resources/{rid}/absences")
def absences(rid: int, conn: sqlite3.Connection = Depends(connection)):
    return resource_absences.list_absences(conn, rid)
