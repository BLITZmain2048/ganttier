"""Disponibilidad personal por intervalo, sin reprogramar asignaciones."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, replace
from datetime import date, timedelta

from . import repo
from .calendars import WorkCalendar
from .domain import CalendarException


def _resource(conn, rid):
    resource = repo.get(conn, "resources", rid)
    if resource.project_id is None:
        raise ValueError("Las ausencias personales no admiten recursos globales: afectan varios proyectos.")
    if resource.kind != "trabajador" or resource.max_units != 1:
        raise ValueError("Las ausencias personales requieren un trabajador local con max_units=1; "
                         "no se admiten cuadrillas ni recursos agregados.")
    return resource


def _default_calendar(conn):
    row = conn.execute("SELECT id FROM calendars ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise ValueError("No hay un calendario disponible para el recurso.")
    return row[0]


def _exclusive(conn, rid, cid):
    """Incluye referencias heredadas del proyecto y del calendario por defecto."""
    default = _default_calendar(conn)
    if conn.execute("SELECT 1 FROM projects WHERE COALESCE(calendar_id,?)=? LIMIT 1",
                    (default, cid)).fetchone():
        return False
    for table in ("resources", "activities", "periodic_tasks"):
        condition = " AND t.id<>?" if table == "resources" else ""
        params = (default, cid, rid) if condition else (default, cid)
        if conn.execute(f"""SELECT 1 FROM {table} t LEFT JOIN projects p ON p.id=t.project_id
            WHERE COALESCE(t.calendar_id,p.calendar_id,?)=?{condition} LIMIT 1""", params).fetchone():
            return False
    return True


def _copy_name(conn, calendar, resource):
    base = f"{calendar.name} · {resource.name}"
    name, number = base, 1
    while conn.execute("SELECT 1 FROM calendars WHERE name=?", (name,)).fetchone():
        name = f"{base} ({resource.id}-{number})"
        number += 1
    return name


def set_absence(conn: sqlite3.Connection, rid: int, start: date, end: date,
                hours: float, reason: str, *, dry_run: bool = False) -> dict:
    """Sustituye las excepciones coincidentes; conserva todas las demás."""
    if end < start:
        raise ValueError("El fin de la ausencia no puede ser anterior al inicio.")
    if (end - start).days > 36600:
        raise ValueError("El intervalo admite hasta cien años.")
    if not math.isfinite(hours) or not 0 <= hours <= 24:
        raise ValueError("La disponibilidad diaria debe estar entre 0 y 24 horas y ser finita.")
    reason = reason.strip()
    if not reason or len(reason) > 200:
        raise ValueError("El motivo debe contener entre 1 y 200 caracteres.")
    # Lectura coherente en vista previa; reserva de escritura antes de comprobar referencias al aplicar.
    with repo.transaction(conn, immediate=not dry_run):
        resource = _resource(conn, rid)
        project = repo.get(conn, "projects", resource.project_id)
        cid = resource.calendar_id or project.calendar_id or _default_calendar(conn)
        source = repo.get(conn, "calendars", cid)
        copy = not _exclusive(conn, rid, cid)
        name = _copy_name(conn, source, resource) if copy else source.name
        calendar = WorkCalendar(source, start, end)
        # Jornada base del día (patrón semanal, turno y feriados legales) sin las excepciones del intervalo: una
        # ausencia editada puede volver a dar horas, pero nunca más que la jornada base.
        base_calendar = WorkCalendar(replace(source, exceptions=[ex for ex in source.exceptions
                                                                 if not start <= ex.date <= end]), start, end)
        exceptions = {ex.date: ex for ex in source.exceptions}
        days = []
        for i in range((end - start).days + 1):
            day = start + timedelta(days=i)
            previous = exceptions.get(day)
            before, base = calendar.hours(day), base_calendar.hours(day)
            # Una ausencia solo reduce disponibilidad: un día no laborable por base (fin de semana, feriado legal,
            # descanso del turno) no se vuelve laborable con una disponibilidad parcial y conserva su excepción.
            after = min(hours, base) if base > 0 else before
            days.append({"date": day, "hours_before": before, "hours_after": after,
                         "previous_exception": asdict(previous) if previous else None,
                         "reason": reason if base > 0 else "día no laborable: sin cambio"})
            if base > 0:
                exceptions[day] = CalendarException(day, after > 0, after, reason)
        assignments = [dict(row) for row in conn.execute("""
            SELECT s.id AS assignment_id, s.activity_id, s.resource_id, s.units, s.work_hours,
                   a.code, a.name, a.project_id,
                   COALESCE(a.planned_start,a.early_start) AS planned_start,
                   COALESCE(a.planned_finish,a.early_finish) AS planned_finish
            FROM assignments s JOIN activities a ON a.id=s.activity_id
            WHERE s.resource_id=? AND COALESCE(a.planned_start,a.early_start)<=?
              AND COALESCE(a.planned_finish,a.early_finish)>=?
            ORDER BY planned_start,a.id,s.id""", (rid, end.isoformat(), start.isoformat()))]
        result = {"resource_id": rid, "dry_run": dry_run,
                  "source_calendar": {"id": source.id, "name": source.name},
                  "will_create_copy": copy, "calendar": {"id": None if copy else cid, "name": name},
                  "days": days, "hours_before": sum(d["hours_before"] for d in days),
                  "hours_after": sum(d["hours_after"] for d in days), "assignments": assignments,
                  "warning": "La copia personal es independiente: no heredará futuras ediciones del calendario de origen."
                  if copy else ""}
        if not dry_run:
            if copy:
                values = asdict(source)
                values.pop("id")
                values["name"] = name
                # Copia completa antes de aplicar, bajo el mismo SAVEPOINT exterior.
                cid = repo.create(conn, "calendars", values).id
            repo.update(conn, "calendars", cid, {"exceptions": [exceptions[d] for d in sorted(exceptions)]})
            repo.update(conn, "resources", rid, {"calendar_id": cid})
            result["calendar"]["id"] = cid
        return result


def list_absences(conn: sqlite3.Connection, rid: int) -> list[dict]:
    """Excepciones del calendario exclusivo actual, incluidas las heredadas al copiar."""
    with repo.transaction(conn):
        resource = _resource(conn, rid)
        if resource.calendar_id is None or not _exclusive(conn, rid, resource.calendar_id):
            return []
        calendar = repo.get(conn, "calendars", resource.calendar_id)
        return [asdict(ex) for ex in calendar.exceptions]
