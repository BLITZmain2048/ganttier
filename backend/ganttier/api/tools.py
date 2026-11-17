"""Plan semanal, cadenas de relaciones y consultas de días laborables."""
import sqlite3
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .. import tools
from .dependencies import connection

router = APIRouter(tags=["Herramientas de planificación"])


class RelationChain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[list[str]] = Field(min_length=1, max_length=125)
    type: Literal["FS", "SS", "FF", "SF"] = "FS"
    lag_days: StrictInt = 0


@router.post("/projects/{pid}/relations/chain")
def relation_chain(pid: int, body: RelationChain, dry_run: bool = False,
                   conn: sqlite3.Connection = Depends(connection)):
    return tools.relation_chain(conn, pid, body.groups, body.type, body.lag_days, dry_run=dry_run)


@router.get("/calendars/{cid}/workdays")
def workdays(cid: int, start: date, days: int, conn: sqlite3.Connection = Depends(connection)):
    return tools.workdays(conn, cid, start, days)


@router.get("/calendars/{cid}/workdays/between")
def workdays_between(cid: int, start: date, end: date, conn: sqlite3.Connection = Depends(connection)):
    return tools.workdays_between(conn, cid, start, end)


@router.get("/projects/{pid}/reports/weekly-roster")
def weekly_roster(pid: int, week_start: date, conn: sqlite3.Connection = Depends(connection)):
    from ..reports.weekly_roster import monday, weekly_roster as render

    data = render(conn, pid, week_start)
    filename = f"plan_semanal_{monday(week_start).isoformat()}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
