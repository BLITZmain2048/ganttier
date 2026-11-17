"""Endpoint de generación completa desde una lista de tareas."""
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..solve import solve_project
from . import schemas
from .dependencies import connection

router = APIRouter(tags=['Programación'])


def _capacity_diagnostics(conn, pid, result, role_mode):
    """Explain existing verifier findings; never infer infeasibility here."""
    if result.get('feasible') is not False:
        return result
    from .. import repo
    violations = result.get('violations') or (result.get('verification') or {}).get('violations') or []
    affected = set()
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        description = f"{violation.get('type', '')} {violation.get('detail', '')}".lower()
        if any(word in description for word in ('capacity', 'intensity', 'skill', 'capacidad', 'habilidad', 'sobrecarga')):
            affected.update(violation.get('activities') or [])
    if not affected:
        return result
    activities = {a.id: a for a in repo.list_entities(conn, 'activities', pid) if a.id in affected}
    resources = {r.id: r for r in repo.list_entities(conn, 'resources', pid)}
    rows = [a for a in repo.list_entities(conn, 'assignments', pid) if a.activity_id in activities]
    diagnostics, generic_skills = [], {}
    for row in rows:
        activity = activities[row.activity_id]
        resource = resources.get(row.resource_id)
        diagnostics.append(dict(activity_id=activity.id, activity_code=activity.code,
            assignment_id=row.id, skill=row.skill, resource_id=row.resource_id,
            resource_name=resource.name if resource else None, units=row.units, work_hours=row.work_hours))
        if row.resource_id is None and row.skill:
            generic_skills.setdefault(activity.id, set()).add(row.skill)
    assigned = {row.activity_id for row in rows}
    for activity in activities.values():
        if activity.id not in assigned:
            diagnostics.append(dict(activity_id=activity.id, activity_code=activity.code,
                assignment_id=None, skill=activity.required_skill, resource_id=None, resource_name=None,
                units=activity.crew_size, work_hours=activity.work_hours))
    result['capacity_diagnostics'] = diagnostics
    warnings = list(result.get('warnings') or [])
    for row in diagnostics[:10]:
        source = f"Asignaciones ID {row['assignment_id']}" if row['assignment_id'] else 'ficha de actividad'
        warnings.append(f"{row['activity_code']} · {source} · habilidad {row['skill'] or '(sin indicar)'} · "
                        f"{row['work_hours']:g} HH / {row['units']:g} unidades. "
                        "Revise duración, disponibilidad y dotación de esta demanda involucrada en el conflicto.")
    if len(diagnostics) > 10:
        warnings.append(f"Se muestran 10 de {len(diagnostics)} demandas; consulte capacity_diagnostics para el detalle completo.")
    if role_mode == 'fractional' and repo.get(conn, 'projects', pid).mode == 'proyecto' and any(len(skills) > 1 and any(
            r.active and r.kind == 'trabajador' and len(set(r.skills) & skills) > 1 for r in resources.values())
            for skills in generic_skills.values()):
        warnings.append("Hay roles genéricos y personas polivalentes. Si necesita personas distintas por rol y dedicación completa, "
                        "evalúe role_mode='indivisible' con calendarios de bloques equivalentes. "
                        "Cambiar el modo no garantiza capacidad ni factibilidad; revise sus requisitos.")
    result['warnings'] = warnings
    return result


@router.get('/projects/{pid}/schedule/role-assignments')
def role_assignments(pid: int, conn: sqlite3.Connection = Depends(connection)):
    from ..role_schedules import allocation_state
    from .. import repo
    state = allocation_state(conn, pid)
    state['display_rows'] = []
    if state['valid']:
        activities = {a.id: a for a in repo.list_entities(conn, 'activities', pid)}
        resources = {r.id: r for r in repo.list_entities(conn, 'resources', pid)}
        state['display_rows'] = [dict(row, activity_code=activities[row['activity_id']].code,
            activity_name=activities[row['activity_id']].name, resource_name=resources[row['resource_id']].name)
            for row in state['role_assignment']]
    return state


class SolveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    weights: schemas.ObjectiveWeights | None = None
    options: schemas.OptimizeOptions = Field(default_factory=schemas.OptimizeOptions)
    apply: bool = True
    regenerate: bool = False
    horizon_start: date | None = None
    horizon_end: date | None = None


@router.post('/projects/{pid}/schedule/solve')
def solve(pid: int, body: SolveRequest, conn: sqlite3.Connection = Depends(connection)):
    # Conservar qué opciones pidió el usuario; equipos tiene defaults propios.
    options = body.options.model_dump(exclude_unset=True)
    options.update(regenerate=body.regenerate, horizon_start=body.horizon_start, horizon_end=body.horizon_end)
    try:
        result = solve_project(conn, pid, body.weights.model_dump() if body.weights else None, options, body.apply)
        return _capacity_diagnostics(conn, pid, result, body.options.role_mode)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail='El modo bloques requiere OR-Tools.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class BlockSolveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    horizon_blocks: int = Field(ge=1, le=2000)
    block_hours: float = Field(default=4, gt=0, le=24)
    target_code: str | None = None
    time_limit_s: float = Field(default=30, gt=0, le=300)
    seed: int = Field(default=0, ge=0)


@router.post('/projects/{pid}/schedule/solve-blocks')
def solve_blocks(pid: int, body: BlockSolveRequest, conn: sqlite3.Connection = Depends(connection)):
    """Preview de roles indivisibles en bloques equivalentes, con costo normal."""
    from ..solve import solve_project_blocks
    try:
        return solve_project_blocks(conn, pid, **body.model_dump())
    except ImportError as exc:
        raise HTTPException(status_code=503, detail='El modo bloques requiere OR-Tools.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
