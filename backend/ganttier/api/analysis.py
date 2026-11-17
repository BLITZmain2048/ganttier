"""Consultas de análisis de control de solo lectura."""
from __future__ import annotations

import sqlite3
from datetime import date
from math import isfinite
from typing import Literal

from fastapi import APIRouter, Depends, Query

from .. import repo, services
from ..calendars import WorkCalendar
from ..control.aggregation import aggregate_evm
from ..control.forecasts import forecasts
from ..control.schedule_quality import schedule_quality, _load_activities, _assigned_activity_ids
from .dependencies import connection

router = APIRouter(tags=["Análisis de control"])
Basis = Literal["hours", "cost", "count"]


def _inputs(conn, pid):
    """Mismas entradas que services.control_result (calendarios efectivos por actividad incluidos)."""
    data = services.control_inputs(conn, pid)
    return data["acts"], data["baseline"], data["progress"], data["calendar"], data["cutoff"], data["calendars"]


@router.get("/projects/{pid}/control/forecasts")
def project_forecasts(pid: int, basis: Basis = "hours", etc: str | None = None,
                      conn: sqlite3.Connection = Depends(connection)):
    """Entrega escenarios; ETC vacío equivale a una reestimación no informada."""
    estimate = None
    if etc is not None and etc.strip():
        try:
            estimate = float(etc)
        except ValueError:
            raise ValueError("ETC debe ser un número finito no negativo.") from None
        if not isfinite(estimate) or estimate < 0:
            raise ValueError("ETC debe ser un número finito no negativo.")
    result = services.control_result(conn, pid, "evm", basis=basis)
    acts, baseline, _, _, cutoff, _ = _inputs(conn, pid)
    plans = [baseline[a.id] if baseline is not None else a for a in acts
             if baseline is None or a.id in baseline]
    def weight(plan):
        if basis == "count":
            return 1
        if basis == "hours":
            return plan.work_hours
        return plan.cost if baseline is not None else plan.cost_budget
    starts = [a.start for a in plans if weight(a) > 0 and a.start is not None]
    start = min(starts) if starts else None
    duration = (result.planned_finish - start).days + 1 if start and result.planned_finish else None
    elapsed = max(0, (cutoff - start).days + 1) if start else None
    # A4-10: alcance presupuestado terminado → el tiempo real se detiene en el último término real (como ES).
    scope = [a for a in acts if (baseline is None or a.id in baseline)
             and weight(baseline[a.id] if baseline is not None else a) > 0]
    if start and scope and result.percent_complete >= 100 - 1e-9 and all(a.actual_finish for a in scope):
        elapsed = max(0, (max(a.actual_finish for a in scope) - start).days + 1)
    return forecasts(result, estimate, duration, elapsed, baseline_start=start)


@router.get("/projects/{pid}/control/quality")
def project_quality(pid: int, profile: Literal["proyecto", "mantenimiento"] | None = None,
                    exclude_terminals: bool = True, start_ids: list[int] | None = Query(None),
                    end_ids: list[int] | None = Query(None),
                    long_lag_days: int = Query(10, ge=0), long_duration_days: int = Query(44, ge=0),
                    high_float_days: int = Query(44, ge=0),
                    conn: sqlite3.Connection = Depends(connection)):
    """Diagnostica los datos guardados sin ejecutar CPM ni sincronizar el avance."""
    project = repo.get(conn, "projects", pid)
    activities = _load_activities(conn, pid)
    progress = repo.list_entities(conn, "progress", pid)
    views = {a.id: a for a in services.build_activity_views(conn, pid, progress=progress)}
    known_ids = {a.id for a in activities}
    if (set(start_ids or []) | set(end_ids or [])) - known_ids:
        raise ValueError("Los extremos excluidos deben pertenecer al proyecto.")
    return schedule_quality(
        activities, repo.list_entities(conn, "relations", pid), [],
        project.data_date or date.today(), profile=profile or project.mode,
        exclude_terminals=exclude_terminals,
        start_ids=None if start_ids is None else set(start_ids),
        end_ids=None if end_ids is None else set(end_ids),
        long_lag_days=long_lag_days, long_duration_days=long_duration_days,
        high_float_days=high_float_days, progress=progress, views=views,
        assigned_ids=_assigned_activity_ids(conn, pid))


@router.get("/projects/{pid}/control/evm-by")
def project_evm_by(pid: int, group: Literal["wbs", "location", "skill"] = "wbs",
                   basis: Basis = "hours", conn: sqlite3.Connection = Depends(connection)):
    """Agrupa por WBS directo, instalación o habilidad requerida y concilia totales."""
    acts, baseline, progress, calendar, cutoff, calendars = _inputs(conn, pid)
    labels = None
    if group in {"location", "skill"}:
        column = "location" if group == "location" else "required_skill"
        labels = dict(conn.execute(f"SELECT id,{column} FROM activities WHERE project_id=?", (pid,)))
    return aggregate_evm(acts, baseline, progress, calendar, cutoff, calendars=calendars,
                         labels=labels,
                         wbs=repo.list_entities(conn, "wbs", pid) if group == "wbs" else [],
                         group=group, basis=basis)
