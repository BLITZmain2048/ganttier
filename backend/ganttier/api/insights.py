"""Indicadores preventivos y alertas persistentes de control."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends

from ..control import alerts
from ..maintenance.kpis import kpis
from .dependencies import connection

router = APIRouter(tags=["Indicadores y alertas"])


@router.get("/projects/{pid}/maintenance/kpis")
def maintenance_kpis(pid: int, start: date | None = None, end: date | None = None,
                     conn: sqlite3.Connection = Depends(connection)):
    return kpis(conn, pid, start, end)


@router.get("/projects/{pid}/alerts")
def project_alerts(pid: int, state: Literal["nueva", "reconocida", "resuelta"] | None = None,
                   conn: sqlite3.Connection = Depends(connection)):
    rows = alerts.evaluate(conn, pid)
    return [row for row in rows if state is None or row["state"] == state]


@router.get("/projects/{pid}/alerts/summary")
def alerts_summary(pid: int, conn: sqlite3.Connection = Depends(connection)):
    rows = alerts.evaluate(conn, pid)
    return {"nueva": sum(row["state"] == "nueva" for row in rows),
            "reconocida": sum(row["state"] == "reconocida" for row in rows),
            "critica": sum(row["severity"] == "crítica" and row["state"] != "resuelta" for row in rows)}


@router.post("/alerts/{id}/acknowledge")
def acknowledge(id: int, conn: sqlite3.Connection = Depends(connection)):
    return alerts.set_state(conn, id, "reconocida")


@router.post("/alerts/{id}/reopen")
def reopen(id: int, conn: sqlite3.Connection = Depends(connection)):
    return alerts.set_state(conn, id, "nueva")
