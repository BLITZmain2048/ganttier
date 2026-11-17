"""Líneas base, avance, fecha de corte y resultados de control."""
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Response, Query
from typing import Literal

from .. import repo, services
from . import schemas
from .dependencies import connection, scoped_values

router = APIRouter(tags=["Control"])


@router.get("/projects/{pid}/control/activities")
def activities_page(pid: int, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
                    scope: Literal['all', 'open', 'late'] = 'open', q: str = '',
                    conn: sqlite3.Connection = Depends(connection)):
    from ..control.queries import activities_page as page
    return page(conn, pid, offset=offset, limit=limit, scope=scope, q=q)


@router.get("/projects/{pid}/control/dashboard")
def dashboard(pid: int, period: str = 'week', basis: str = 'hours',
              max_points: int | None = Query(None, ge=4, le=4096), conn: sqlite3.Connection = Depends(connection)):
    from ..control.runtime import interactive_calculation
    with interactive_calculation():
        from ..control.cache import dashboard as result
        return result(conn, pid, period, basis, max_points=max_points)


@router.get("/projects/{pid}/baselines")
def baselines(pid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, "projects", pid)
    return repo.list_entities(conn, "baselines", pid)


@router.post("/projects/{pid}/baselines", status_code=201)
def create_baseline(pid: int, body: schemas.BaselineRequest, conn: sqlite3.Connection = Depends(connection)):
    return services.create_baseline(conn, pid, body.name, body.activate)


@router.delete("/baselines/{bid}", status_code=204)
def delete_baseline(bid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.delete(conn, "baselines", bid)
    return Response(status_code=204)


@router.post("/baselines/{bid}/activate")
def activate_baseline(bid: int, conn: sqlite3.Connection = Depends(connection)):
    baseline = repo.get(conn, "baselines", bid)
    return repo.update(conn, "projects", baseline.project_id, {"active_baseline_id": bid})


@router.get("/projects/{pid}/progress")
def progress(pid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, "projects", pid)
    return repo.list_entities(conn, "progress", pid)


@router.post("/projects/{pid}/progress", status_code=201)
def create_progress(pid: int, body: schemas.ProgressEntry | list[schemas.ProgressEntry], conn: sqlite3.Connection = Depends(connection)):
    many = isinstance(body, list)
    rows = [scoped_values(conn, "progress", pid, obj.model_dump()) for obj in (body if many else [body])]
    from ..control.cache import progress_write
    with progress_write(conn, pid, [row['activity_id'] for row in rows]):
        # Orden cronológico para que un lote sin ordenar herede el inicio real.
        starts = {}
        for row in sorted(rows, key=lambda r: r["date"]):
            aid, recorded_date = row["activity_id"], row["date"]
            if row["percent_complete"] > 0 and not row.get("actual_start"):
                activity = repo.get(conn, "activities", aid)
                previous = conn.execute("SELECT MIN(actual_start) FROM progress WHERE activity_id=? AND date<=?",
                    (aid, recorded_date.isoformat())).fetchone()[0]
                candidates = [value for value in (starts.get(aid), activity.actual_start,
                    date.fromisoformat(previous) if previous else None) if value and value <= recorded_date]
                row["actual_start"] = min(candidates) if candidates else min(activity.planned_start or recorded_date, recorded_date)
            if row["percent_complete"] >= 100 and not row.get("actual_finish"):
                row["actual_finish"] = recorded_date
            if row.get("actual_start"):
                starts[aid] = min(starts.get(aid, row["actual_start"]), row["actual_start"])
        repo.validate_progress_entries(conn, rows)
        result = repo.create_many(conn, "progress", rows)
        services.sync_progress(conn, pid, [row["activity_id"] for row in rows])
    return result if many else result[0]


@router.delete("/progress/{entry_id}", status_code=204)
def delete_progress(entry_id: int, conn: sqlite3.Connection = Depends(connection)):
    entry = repo.get(conn, "progress", entry_id)
    activity = repo.get(conn, "activities", entry.activity_id)
    from ..control.cache import progress_write
    with progress_write(conn, activity.project_id, [activity.id]):
        repo.delete(conn, "progress", entry_id)
        services.sync_progress(conn, activity.project_id, [activity.id])
    return Response(status_code=204)


@router.put("/projects/{pid}/data-date")
def data_date(pid: int, body: schemas.DataDateRequest, conn: sqlite3.Connection = Depends(connection)):
    with repo.transaction(conn):
        project = repo.update(conn, "projects", pid, {"data_date": body.data_date})
        services.sync_progress(conn, pid)
        return project


@router.get("/projects/{pid}/control/scurve")
def scurve(pid: int, period: str = "week", basis: str = "hours", conn: sqlite3.Connection = Depends(connection)):
    return services.control_result(conn, pid, "scurve", period, basis)


@router.get("/projects/{pid}/control/evm")
def evm(pid: int, period: str = "week", basis: str = "hours", conn: sqlite3.Connection = Depends(connection)):
    return services.control_result(conn, pid, "evm", period, basis)


@router.get("/projects/{pid}/control/performance")
def performance(pid: int, start: date | None = None, end: date | None = None, conn: sqlite3.Connection = Depends(connection)):
    if start and end and end < start:
        raise ValueError("El fin del período no puede ser anterior al inicio.")
    return services.control_result(conn, pid, "performance", start=start, end=end)
