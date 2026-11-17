"""Conexión por petición y validación de pertenencia a un proyecto."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Iterator

from .. import db, repo
from . import schemas


def connection() -> Iterator[sqlite3.Connection]:
    """Cierra la conexión incluso cuando una ruta genera una excepción."""
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def scoped_values(conn: sqlite3.Connection, table: str, pid: int, values: dict) -> dict:
    """Normaliza el id de la ruta y rechaza referencias fuera del proyecto."""
    repo.get(conn, "projects", pid)
    if table in {"assignments", "progress"}:
        if repo.get(conn, "activities", values["activity_id"]).project_id != pid:
            raise ValueError("La actividad no pertenece al proyecto solicitado.")
    else:
        if values.get("project_id") not in (None, 0, pid):
            raise ValueError("El identificador del proyecto no coincide con la ruta.")
        values["project_id"] = pid
    return values


def validated_update(conn: sqlite3.Connection, table: str, entity_id: int, values: dict):
    """Valida el objeto completo conservando campos omitidos en una actualización."""
    with repo.transaction(conn, immediate=True):
        return _validated_update_locked(conn, table, entity_id, values)


def _validated_update_locked(conn: sqlite3.Connection, table: str, entity_id: int, values: dict):
    current = repo.get(conn, table, entity_id)
    current_values = {field: getattr(current, field) for field in current.__dataclass_fields__}
    current_values.update(values)
    model = getattr(schemas, repo.ENTITIES[table].__name__)
    validated = model.model_validate(current_values).model_dump()
    changes = {key: validated[key] for key in values}
    if table == "activities" and "duration_days" in values and validated["planned_start"]:
        project = repo.get(conn, "projects", current.project_id)
        if project.mode == "mantenimiento":
            from ..calendars import WorkCalendar
            start = validated["planned_start"]
            end = date.fromordinal(min(date.max.toordinal(), start.toordinal() + max(3660, validated["duration_days"] * 14)))
            cid = validated["calendar_id"] or project.calendar_id or repo.list_entities(conn, "calendars")[0].id
            cal = WorkCalendar(repo.get(conn, "calendars", cid), start, end)
            changes["planned_finish"] = cal.add_workdays(start, validated["duration_days"])
    return repo.update(conn, table, entity_id, changes)
