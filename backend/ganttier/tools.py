"""Herramientas de planificación sin dependencia de la interfaz gráfica."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from itertools import chain

from . import repo
from .calendars import WorkCalendar
from .domain import to_dict

INCLUSIVE = "Conteo inclusivo: el inicio laborable cuenta como día 1; cero conserva la fecha."


class _PreviewRelations:
    """Lectura de aristas para el validador de repo, con el lote aún no persistido.

    Implementa únicamente el SELECT utilizado por _validate_relation_cycles.
    No duplica el algoritmo ni ejecuta escrituras, incluso dentro de un SAVEPOINT.
    """

    def __init__(self, conn, pending):
        self.conn, self.pending = conn, pending

    def execute(self, sql, params):
        if sql != "SELECT pred_id,succ_id FROM relations WHERE project_id=?":
            raise RuntimeError("Cambió el contrato de lectura del validador de relaciones.")
        return chain(self.conn.execute(sql, params),
                     ((r["pred_id"], r["succ_id"]) for r in self.pending
                      if r["project_id"] == params[0]))


def relation_chain(conn: sqlite3.Connection, pid: int, groups: list[list[str]],
                   type: str = "FS", lag_days: int = 0, *, dry_run: bool = False) -> dict:
    """Crea pares consecutivos de cada grupo atómicamente; la vista previa solo lee."""
    if type not in {"FS", "SS", "FF", "SF"}:
        raise ValueError("El tipo debe ser FS, SS, FF o SF.")
    if isinstance(lag_days, bool) or not isinstance(lag_days, int):
        raise ValueError("El desfase debe ser un número entero de días.")
    if not groups or any(not isinstance(g, list) or len(g) < 2 for g in groups):
        raise ValueError("Indique grupos con al menos dos códigos de actividad.")
    codes = [code for group in groups for code in group]
    if len(codes) > 250:
        raise ValueError("La cadena admite como máximo 250 actividades por solicitud.")
    if any(not isinstance(code, str) or not code.strip() for code in codes):
        raise ValueError("Los códigos de actividad no pueden estar vacíos.")
    with repo.transaction(conn, immediate=not dry_run):
        if repo.get(conn, "projects", pid).mode != "proyecto":
            raise ValueError("Las cadenas de relaciones solo están disponibles en modo proyecto.")
        activities = {a.code: a.id for a in repo.list_entities(conn, "activities", pid)}
        missing = sorted(set(codes) - activities.keys())
        if missing:
            raise ValueError("Códigos inexistentes en el proyecto: " + ", ".join(missing))
        stored = {}
        for relation in repo.list_entities(conn, "relations", pid):
            stored.setdefault((relation.pred_id, relation.succ_id), []).append(relation)
        pending, ordered, seen = [], [], set()
        existing = 0
        for group in groups:
            for pred, succ in zip(group, group[1:]):
                pair = activities[pred], activities[succ]
                if pair in seen:
                    continue
                seen.add(pair)
                if pair[0] == pair[1]:
                    raise ValueError(f"{pred} → {succ}: una actividad no puede depender de sí misma.")
                matches = stored.get(pair, [])
                if any(r.type != type or r.lag_days != lag_days for r in matches):
                    description = ", ".join(f"{r.type}/{r.lag_days}" for r in matches)
                    raise ValueError(f"{pred} → {succ}: ya existe otra relación ({description}).")
                if matches:
                    existing += 1
                    ordered.append(to_dict(matches[0]))
                else:
                    row = dict(project_id=pid, pred_id=pair[0], succ_id=pair[1],
                               type=type, lag_days=lag_days)
                    pending.append(row)
                    ordered.append(row)
        if dry_run:
            repo._validate_relation_cycles(_PreviewRelations(conn, pending), {pid})
            relations = [dict(id=None, **row) if "id" not in row else row for row in ordered]
        else:
            created = repo.create_many(conn, "relations", pending)
            by_pair = {(r.pred_id, r.succ_id): to_dict(r) for r in created}
            relations = [by_pair.get((r["pred_id"], r["succ_id"]), r) for r in ordered]
        return {"created": len(pending), "existing": existing, "relations": relations}


def _excluded(cal: WorkCalendar, start: date, end: date) -> list[dict]:
    result = []
    for ordinal in range(start.toordinal(), end.toordinal() + 1):
        day = date.fromordinal(ordinal)
        if cal.is_workday(day):
            continue
        if day in cal.exceptions:
            kind, reason = "exception", cal.exceptions[day].reason or "Excepción sin horas disponibles"
        elif day in cal.holidays:
            kind, reason = "holiday", cal.holidays[day]
        elif cal.cal.hours_per_day == 0:
            kind, reason = "calendar", "Calendario sin horas disponibles"
        elif cal.cal.shift_pattern:
            kind, reason = "shift", f"Descanso de turno {cal.cal.shift_pattern}"
        else:
            kind, reason = "week", "Descanso semanal"
        result.append({"date": day.isoformat(), "reason": reason, "kind": kind})
    return result


def workdays_between(conn: sqlite3.Connection, cid: int, start: date, end: date) -> dict:
    """Días y horas inclusivos, con todos los días excluidos y sus motivos."""
    if end < start:
        raise ValueError("La fecha final no puede ser anterior a la inicial.")
    cal = WorkCalendar(repo.get(conn, "calendars", cid), start, end)
    return {"calendar_id": cid, "calendar_name": cal.cal.name,
            "start": start.isoformat(), "end": end.isoformat(), "inclusive": True,
            "convention": INCLUSIVE, "workdays": cal.workdays_between(start, end),
            "hours": float(cal.day_hours_vector().sum()), "excluded": _excluded(cal, start, end)}


def workdays(conn: sqlite3.Connection, cid: int, start: date, days: int) -> dict:
    """Término inclusivo; N negativo recorre el calendario hacia atrás."""
    if isinstance(days, bool) or not isinstance(days, int):
        raise ValueError("La cantidad de días debe ser un número entero.")
    calendar = repo.get(conn, "calendars", cid)
    direction = 1 if days >= 0 else -1
    available = (date.max - start).days if direction > 0 else (start - date.min).days
    span = min(available, max(366, abs(days) * 2))
    while True:
        bound = start + timedelta(days=direction * span)
        lo, hi = sorted((start, bound))
        cal = WorkCalendar(calendar, lo, hi)
        if cal.workdays_between(lo, hi) >= abs(days):
            break
        if span == available:
            raise ValueError("No hay suficientes días laborables dentro del rango de fechas admitido.")
        span = min(available, max(1, span * 2))
    if days >= 0:
        result = cal.add_workdays(start, days)
    else:
        # Búsqueda binaria con el contador público; no reproduce reglas de calendario.
        left, right = lo.toordinal(), start.toordinal()
        while left < right:
            middle = (left + right + 1) // 2
            if cal.workdays_between(date.fromordinal(middle), start) >= -days:
                left = middle
            else:
                right = middle - 1
        result = date.fromordinal(left)
    first, last = sorted((start, result))
    return {"calendar_id": cid, "calendar_name": calendar.name,
            "start": start.isoformat(), "result": result.isoformat(), "workdays": days,
            "inclusive": True, "convention": INCLUSIVE,
            "hours": sum(cal.hours(date.fromordinal(o))
                         for o in range(first.toordinal(), last.toordinal() + 1)) if days else 0.0,
            "excluded": _excluded(cal, first, last)}
