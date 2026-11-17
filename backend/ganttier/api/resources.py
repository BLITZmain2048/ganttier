"""CRUD de proyectos, calendarios y maestros, con validación Pydantic."""
import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from .. import repo, services
from ..calendars import WorkCalendar
from . import schemas
from .dependencies import connection, scoped_values, validated_update

router = APIRouter(tags=["Maestros"])


@router.get("/health")
def health(request: Request):
    """Comprobación de vida sin cargar motores opcionales; incluye el identificador de la instancia."""
    return {"status": "ok", "app": "Ganttier", "instance": getattr(request.app.state, "instance", None)}


@router.get("/projects/{pid}/summary")
def project_summary(pid: int, conn: sqlite3.Connection = Depends(connection)):
    """Indicadores livianos de un proyecto (página Inicio)."""
    from .. import services
    return services.project_summary(conn, pid)


@router.get("/projects")
def projects(conn: sqlite3.Connection = Depends(connection)):
    from ..domain import to_dict
    counts = dict(conn.execute("SELECT project_id, COUNT(*) FROM activities GROUP BY project_id").fetchall())
    result = []
    for p in repo.list_entities(conn, "projects"):
        row = to_dict(p)
        row["counts"] = {"activities": counts.get(p.id, 0)}
        result.append(row)
    return result


@router.post("/projects", status_code=201)
def create_project(body: schemas.Project, conn: sqlite3.Connection = Depends(connection)):
    values = body.model_dump()
    if values["calendar_id"] is None:
        calendars = repo.list_entities(conn, "calendars")
        values["calendar_id"] = next((c.id for c in calendars if c.name == "Chile L-V 8h"), calendars[0].id)
    return repo.create(conn, "projects", values)


@router.get("/projects/{pid}")
def project(pid: int, conn: sqlite3.Connection = Depends(connection)):
    from ..domain import to_dict
    result = to_dict(repo.get(conn, "projects", pid))
    result["counts"] = {table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (pid,)).fetchone()[0] for table in ("activities", "wbs", "relations", "resources", "periodic_tasks", "baselines", "schedule_runs")}
    return result


@router.put("/projects/{pid}")
def update_project(pid: int, body: dict = Body(...), conn: sqlite3.Connection = Depends(connection)):
    with repo.transaction(conn):
        project = validated_update(conn, "projects", pid, body)
        if "data_date" in body:
            services.sync_progress(conn, pid)
        return project


@router.delete("/projects/{pid}", status_code=204)
def delete_project(pid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.delete(conn, "projects", pid)
    return Response(status_code=204)


@router.post("/projects/{pid}/duplicate", status_code=201)
def duplicate(pid: int, conn: sqlite3.Connection = Depends(connection)):
    return repo.duplicate_project(conn, pid)


@router.get("/calendars")
def calendars(conn: sqlite3.Connection = Depends(connection)):
    return repo.list_entities(conn, "calendars")


@router.post("/calendars", status_code=201)
def create_calendar(body: schemas.Calendar, conn: sqlite3.Connection = Depends(connection)):
    return repo.create(conn, "calendars", body.model_dump())


@router.get("/calendars/{cid}")
def calendar_detail(cid: int, conn: sqlite3.Connection = Depends(connection)):
    return repo.get(conn, "calendars", cid)


@router.put("/calendars/{cid}")
def update_calendar(cid: int, body: dict = Body(...), conn: sqlite3.Connection = Depends(connection)):
    return validated_update(conn, "calendars", cid, body)


@router.delete("/calendars/{cid}", status_code=204)
def delete_calendar(cid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.delete(conn, "calendars", cid)
    return Response(status_code=204)


@router.get("/calendars/{cid}/days")
def calendar_days(cid: int, start: date, end: date, conn: sqlite3.Connection = Depends(connection)):
    cal = WorkCalendar(repo.get(conn, "calendars", cid), start, end)
    if (end - start).days > 36600:
        raise ValueError("La consulta de calendario admite hasta cien años.")
    result = []
    for index in range((end - start).days + 1):
        day = start + timedelta(days=index)
        reason = cal.exceptions[day].reason if day in cal.exceptions else cal.holidays.get(day, "Laborable" if cal.is_workday(day) else "Descanso")
        result.append({"date": day, "hours": cal.hours(day), "working": cal.is_workday(day), "reason": reason})
    return result


@router.get("/resources")
def resources(project_id: int | None = None, conn: sqlite3.Connection = Depends(connection)):
    if project_id is not None:
        repo.get(conn, "projects", project_id)
    return repo.list_entities(conn, "resources", project_id)


@router.post("/resources", status_code=201)
def create_resource(body: schemas.Resource, conn: sqlite3.Connection = Depends(connection)):
    return repo.create(conn, "resources", body.model_dump())


def _register_master(table: str, path: str, model: type, collection: bool = True, update_allowed: bool = True):
    """Registra rutas equivalentes manteniendo esquemas explícitos en OpenAPI."""
    if collection:
        def read(pid: int, offset: int = Query(0, ge=0), limit: int = Query(100000, ge=1, le=100000), conn: sqlite3.Connection = Depends(connection)):
            repo.get(conn, "projects", pid)
            return repo.list_entities(conn, table, pid, offset=offset, limit=limit)

        def insert(pid: int, body: model, conn: sqlite3.Connection = Depends(connection)):
            return repo.create(conn, table, scoped_values(conn, table, pid, body.model_dump()))

        if table != "assignments":
            router.add_api_route(f"/projects/{{pid}}/{path}", read, methods=["GET"], name=f"list_{table}")
        router.add_api_route(f"/projects/{{pid}}/{path}", insert, methods=["POST"], status_code=201, name=f"create_{table}")

    def change(entity_id: int, body: dict = Body(...), conn: sqlite3.Connection = Depends(connection)):
        return validated_update(conn, table, entity_id, body)

    def remove(entity_id: int, force: bool = False, conn: sqlite3.Connection = Depends(connection)):
        repo.delete(conn, table, entity_id, force=force)
        return Response(status_code=204)

    if update_allowed:
        router.add_api_route(f"/{path}/{{entity_id}}", change, methods=["PUT"], name=f"update_{table}")
    router.add_api_route(f"/{path}/{{entity_id}}", remove, methods=["DELETE"], status_code=204, name=f"delete_{table}")


@router.get("/projects/{pid}/assignments")
def assignments(pid: int, activity_id: int | None = None,
                offset: int | None = Query(None, ge=0), limit: int | None = Query(None, ge=1, le=100000),
                conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, "projects", pid)
    filters = {"activity_id": activity_id}
    if offset is None and limit is None:
        return repo.list_entities(conn, "assignments", pid, filters=filters)
    offset, limit = offset or 0, limit or 100000
    query = "SELECT COUNT(*) FROM assignments s JOIN activities a ON a.id=s.activity_id WHERE a.project_id=?"
    params = [pid]
    if activity_id is not None:
        query += " AND s.activity_id=?"
        params.append(activity_id)
    total = conn.execute(query, params).fetchone()[0]
    return {"items": repo.list_entities(conn, "assignments", pid, filters=filters, offset=offset, limit=limit),
            "offset": offset, "limit": limit, "total": total}


_register_master("wbs", "wbs", schemas.WBSNode)
_register_master("relations", "relations", schemas.Relation, update_allowed=False)
_register_master("resources", "resources", schemas.Resource, collection=False)
_register_master("assignments", "assignments", schemas.Assignment)
_register_master("periodic_tasks", "periodic-tasks", schemas.PeriodicTask)


@router.get("/projects/{pid}/teams")
def work_teams(pid: int, conn: sqlite3.Connection = Depends(connection)):
    from ..teams import list_teams
    return list_teams(conn, pid)


@router.post("/projects/{pid}/teams", status_code=201)
def create_work_team(pid: int, body: schemas.WorkTeam, conn: sqlite3.Connection = Depends(connection)):
    from ..teams import save_team
    return save_team(conn, pid, body.model_dump(exclude_unset=True))


@router.put("/projects/{pid}/teams/{team_id}")
def update_work_team(pid: int, team_id: int, body: dict = Body(...), conn: sqlite3.Connection = Depends(connection)):
    from ..teams import save_team
    return save_team(conn, pid, body, team_id)


@router.delete("/projects/{pid}/teams/{team_id}", status_code=204)
def delete_work_team(pid: int, team_id: int, conn: sqlite3.Connection = Depends(connection)):
    team = repo.get(conn, "resources", team_id)
    if team.project_id != pid or not team.is_work_team:
        raise ValueError("El equipo no pertenece al plan.")
    repo.delete(conn, "resources", team_id)
    return Response(status_code=204)


@router.get("/projects/{pid}/maintenance/distribution")
def work_team_distribution(pid: int, start: date | None = None, end: date | None = None,
                           conn: sqlite3.Connection = Depends(connection)):
    from ..teams import summary
    return summary(conn, pid, start, end)
