"""Actividades, programación, mantenimiento y perfiles de carga."""
import sqlite3
import gzip
from datetime import date

from fastapi import APIRouter, Body, Depends, Query, Response, Request
from fastapi.responses import JSONResponse

from .. import domain, repo, services
from . import schemas
from .dependencies import connection, scoped_values, validated_update

router = APIRouter(tags=["Programación"])


@router.get("/projects/{pid}/activities")
def activities(pid: int, offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=100000), q: str | None = None, wbs_id: int | None = None, status: str | None = None, from_date: date | None = Query(None, alias="from"), to_date: date | None = Query(None, alias="to"), resource_id: int | None = None, conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, "projects", pid)
    return repo.list_entities(conn, "activities", pid, offset=offset, limit=limit, filters={"q": q, "wbs_id": wbs_id, "status": status, "from": from_date, "to": to_date, "resource_id": resource_id})


@router.get("/projects/{pid}/activities/columnar")
def columnar(pid: int, conn: sqlite3.Connection = Depends(connection), request: Request = None):
    repo.get(conn, "projects", pid)
    # El repositorio ya entrega columnas de tipos JSON simples (fechas ISO).
    response = Response(content=repo.activities_columnar(conn, pid, encoded=True),
                        media_type="application/json", headers={"Vary": "Accept-Encoding"})
    # H2: un Gantt de 250.003 filas supera 50 MB de JSON. Solo comprimir si el
    # cliente anuncia gzip sin restricciones; q=0 conserva la respuesta original.
    encodings = {v.strip().lower() for v in request.headers.get("accept-encoding", "").split(",")} if request else set()
    if len(response.body) >= 262144 and "gzip" in encodings:
        return Response(gzip.compress(response.body, compresslevel=1, mtime=0), media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return response


@router.post("/projects/{pid}/activities", status_code=201)
def create_activities(pid: int, body: schemas.Activity | list[schemas.Activity], conn: sqlite3.Connection = Depends(connection)):
    many = isinstance(body, list)
    objects = body if many else [body]
    repo.get(conn, "projects", pid)
    values = []
    for obj in objects:
        row = obj.model_dump()
        if row["project_id"] not in (0, pid):
            raise ValueError("La actividad pertenece a otro proyecto.")
        row["project_id"] = pid
        values.append(row)
    result = repo.create_many(conn, "activities", values)
    return result if many else result[0]


@router.patch("/projects/{pid}/activities")
def patch_activities(pid: int, body: list[dict] = Body(...), conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, "projects", pid)
    with repo.transaction(conn):
        for row in body:
            if "id" not in row or repo.get(conn, "activities", row["id"]).project_id != pid:
                raise ValueError("La actividad no pertenece al proyecto.")
        return [validated_update(conn, "activities", row["id"], row) for row in body]


@router.get("/activities/{aid}")
def get_activity(aid: int, conn: sqlite3.Connection = Depends(connection)):
    return repo.get(conn, "activities", aid)


@router.put("/activities/{aid}")
def update_activity(aid: int, body: dict = Body(...), conn: sqlite3.Connection = Depends(connection)):
    return validated_update(conn, "activities", aid, body)


@router.get("/activities/{aid}/history")
def activity_history(aid: int, conn: sqlite3.Connection = Depends(connection)):
    """Historia de control que se perdería al borrar la actividad (líneas base y avances)."""
    repo.get(conn, "activities", aid)
    return repo.activity_history(conn, [aid])


@router.get("/periodic-tasks/{tid}/history")
def periodic_task_history(tid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, "periodic_tasks", tid)
    ids = [r[0] for r in conn.execute("SELECT id FROM activities WHERE periodic_task_id=?", (tid,))]
    return repo.activity_history(conn, ids)


@router.delete("/activities/{aid}", status_code=204)
def delete_activity(aid: int, force: bool = False, conn: sqlite3.Connection = Depends(connection)):
    repo.delete(conn, "activities", aid, force=force)
    return Response(status_code=204)


@router.post("/projects/{pid}/maintenance/generate")
def generate(pid: int, body: schemas.GenerateRequest, conn: sqlite3.Connection = Depends(connection)):
    return services.generate_maintenance(conn, pid, body.horizon_start, body.horizon_end, body.replace)


@router.post("/projects/{pid}/schedule/cpm")
def cpm(pid: int, conn: sqlite3.Connection = Depends(connection)):
    return services.run_cpm(conn, pid)


@router.post("/projects/{pid}/schedule/optimize")
def optimize(pid: int, body: schemas.OptimizeRequest, conn: sqlite3.Connection = Depends(connection)):
    options = body.options.model_dump()
    if 'allow_overtime' not in body.options.model_fields_set and conn.execute(
            'SELECT 1 FROM periodic_tasks WHERE project_id=? AND (quantity_total IS NOT NULL OR is_daily_round=1) LIMIT 1', (pid,)).fetchone():
        options['allow_overtime'] = False
    return services.run_optimize(conn, pid, domain.ObjectiveWeights(**body.weights.model_dump()), domain.OptimizeOptions(**options), body.apply)


@router.get("/projects/{pid}/schedule/runs")
def runs(pid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, "projects", pid)
    return repo.list_entities(conn, "schedule_runs", pid)


@router.post("/projects/{pid}/schedule/runs/{run_id}/apply")
def apply_run(pid: int, run_id: int, conn: sqlite3.Connection = Depends(connection)):
    return services.apply_run(conn, pid, run_id)


@router.get("/projects/{pid}/load")
def load(pid: int, group: str = "resource", period: str = "day", from_date: date | None = Query(None, alias="from"), to_date: date | None = Query(None, alias="to"), conn: sqlite3.Connection = Depends(connection)):
    return services.load_profile(conn, pid, group, period, from_date, to_date)


@router.post("/demo/{kind}", status_code=201)
def demo(kind: str, n: int = Query(10000, ge=1, le=100000), conn: sqlite3.Connection = Depends(connection)):
    if kind == "proyecto":
        return services.demo_project(conn)
    if kind == "mantenimiento":
        return services.demo_maintenance(n, conn)
    raise ValueError("El tipo de demo debe ser proyecto o mantenimiento.")


@router.get("/projects/{pid}/routes")
def routes(pid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, "projects", pid)
    return [t for t in repo.list_entities(conn, "periodic_tasks", pid) if t.is_route]


@router.post("/projects/{pid}/routes/propose")
def propose_routes(pid: int, body: dict = Body(default={}), conn: sqlite3.Connection = Depends(connection)):
    from ..routes import propose_routes as propose
    repo.get(conn, "projects", pid)
    return propose(conn, pid, apply=bool(body.get("apply", False)), team_ids=body.get("team_ids"))


@router.get("/projects/{pid}/routes/checklist")
def route_checklist(pid: int, conn: sqlite3.Connection = Depends(connection)):
    from ..routes import checklist_report
    repo.get(conn, "projects", pid)
    return checklist_report(conn, pid)


@router.post("/projects/{pid}/routes", status_code=201)
def create_route(pid: int, body: schemas.PeriodicTask, conn: sqlite3.Connection = Depends(connection)):
    values=body.model_dump()
    if values.get("project_id") not in (0,pid):
        raise ValueError("La ruta pertenece a otro proyecto.")
    values.update(project_id=pid,is_route=True,route_code="")
    return repo.create(conn,"periodic_tasks",values)


@router.put("/projects/{pid}/routes/{tid}")
def update_route(pid: int, tid: int, body: dict = Body(...), conn: sqlite3.Connection = Depends(connection)):
    current=repo.get(conn,"periodic_tasks",tid)
    if current.project_id!=pid or not current.is_route:
        raise ValueError("Ruta ajena al proyecto.")
    return validated_update(conn,"periodic_tasks",tid,dict(body,is_route=True,route_code=""))
