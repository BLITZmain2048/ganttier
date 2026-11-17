"""Operaciones de modelo compartidas por los handlers individuales y los lotes."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

from .._base import CliError, Context, Result

ALIASES = {
    "projects": "proyectos", "activities": "actividades", "resources": "recursos",
    "relations": "relaciones", "assignments": "asignaciones", "calendars": "calendarios",
    "periodic_tasks": "periodicas", "baselines": "lineas-base",
    "baseline_activities": "actividades-base", "progress": "avances", "wbs": "wbs",
}
SPECIAL = {"equipos": "resources", "rutas": "periodic_tasks", "ausencias": "calendars"}


def read_json(args: Any) -> Any:
    """Lee JSON literal, archivo UTF-8 o stdin, conservando errores de ubicación."""
    try:
        source = getattr(args, "json", None)
        if source is None:
            path = getattr(args, "archivo", None)
            if path is None:
                raise CliError("uso", "Indique --json o --archivo.")
            source = sys.stdin.read() if str(path) == "-" else Path(path).read_text(encoding="utf-8-sig")
        return json.loads(source)
    except json.JSONDecodeError as exc:
        raise CliError("datos_invalidos", "JSON inválido.", detail={"errors": [
            {"field": "$", "line": exc.lineno, "column": exc.colno, "message": exc.msg}]}) from exc
    except (OSError, UnicodeError) as exc:
        raise CliError("datos_invalidos", f"No se pudo leer el JSON: {exc}") from exc


def model_error(exc: Exception, *, index: int | None = None) -> CliError:
    """Normaliza validación Pydantic, referencias e historia protegida por campo."""
    from pydantic import ValidationError
    from ... import repo
    if isinstance(exc, CliError):
        if index is None:
            return exc
        errors = (exc.detail or {}).get("errors", [{"field": "$", "message": exc.message}])
    elif isinstance(exc, ValidationError):
        errors = [{"field": ".".join(map(str, e["loc"])) or _message_field(e["msg"]),
                   "message": e["msg"], "type": e["type"]}
                  for e in exc.errors(include_url=False, include_context=False, include_input=False)]
    else:
        errors = [{"field": _message_field(str(exc)), "message": str(exc)}]
    errors = [dict(e, **({"index": index} if index is not None else {})) for e in errors]
    detail = {"errors": errors}
    if isinstance(exc, repo.ProtectedDeleteError):
        detail["history"] = exc.history
        for error in errors:
            error["history"] = exc.history
    return CliError("no_encontrado" if isinstance(exc, LookupError) else "datos_invalidos",
                    str(exc), detail=detail)


def _message_field(message: str) -> str:
    match = re.search(r"(?:campo |en |referencia )([a-z][a-z_]+)", message)
    if match:
        return match.group(1)
    for word, field in (("avance", "percent_complete"), ("prioridad", "priority"),
                        ("intervalo", "interval"), ("inicio real", "actual_start"),
                        ("fin de horizonte", "end_date"), ("proyecto", "project_id")):
        if word in message.lower():
            return field
    return "$"


def entity_name(name: str) -> tuple[str, str]:
    """Resuelve alias castellanos e ingleses contra el registro del repositorio."""
    from ... import repo
    name = {"teams": "equipos", "routes": "rutas", "absences": "ausencias",
            "líneas-base": "lineas-base", "lineas_base": "lineas-base"}.get(name, name)
    table = SPECIAL.get(name) or next((k for k, v in ALIASES.items() if v == name), name)
    if table not in repo.ENTITIES:
        raise CliError("datos_invalidos", f"Entidad desconocida: {name}.", detail={
            "errors": [{"field": "entidad", "message": "Use describir para consultar las entidades."}]})
    return table, name if name in SPECIAL else table


def _schema(table: str) -> Any:
    from ... import repo
    from ...api import schemas
    return getattr(schemas, repo.ENTITIES[table].__name__)


def _pid(conn: sqlite3.Connection, ctx: Context, table: str) -> int | None:
    if table in {"projects", "calendars"}:
        return None
    if table == "resources" and ctx.project is None:
        return None
    return ctx.require_project(conn)


def _scope(conn: sqlite3.Connection, ctx: Context, table: str, current: Any) -> None:
    from ... import repo
    if ctx.project is None or table in {"projects", "calendars"}:
        return
    pid = ctx.require_project(conn)
    owner = getattr(current, "project_id", None)
    if table in {"assignments", "progress", "baseline_activities"}:
        owner = repo.get(conn, "activities", current.activity_id).project_id
    if owner != pid and not (table == "resources" and owner is None):
        raise ValueError("El registro no pertenece al proyecto solicitado.")


def _baseline_key(value: Any) -> tuple[int, int]:
    try:
        a, b = str(value).split(":")
        return int(a), int(b)
    except (ValueError, TypeError) as exc:
        raise ValueError("La clave debe ser BASELINE_ID:ACTIVITY_ID.") from exc


def _get(conn: sqlite3.Connection, table: str, ident: Any) -> Any:
    from ... import repo
    if table != "baseline_activities":
        if isinstance(ident, (bool, float)):
            raise ValueError("El id debe ser entero.")
        try:
            ident = int(ident)
        except (TypeError, ValueError) as exc:
            raise ValueError("El id debe ser entero.") from exc
        return repo.get(conn, table, ident)
    bid, aid = _baseline_key(ident)
    rows = repo.list_entities(conn, table, filters={"baseline_id": bid, "activity_id": aid})
    if not rows:
        raise LookupError("No existe la actividad en esa línea base.")
    return rows[0]


def operate(conn: sqlite3.Connection, ctx: Context, entity: str, action: str,
            ident: Any = None, values: Any = None, confirm_name: str | None = None) -> Any:
    """Ejecuta una operación validada dentro de la transacción del llamador."""
    from ... import repo, domain, services
    from ...api.dependencies import scoped_values, validated_update
    table, special = entity_name(entity)
    if action in {"crear", "agregar", "actualizar"}:
        if not isinstance(values, dict):
            raise ValueError("Los valores deben ser un objeto JSON.")
        values = dict(values)
    if special == "ausencias":
        return _absence(conn, ctx, action, ident, values)
    if special == "equipos":
        from ... import teams
        from ...api.schemas import WorkTeam
        pid = ctx.require_project(conn)
        if action in {"crear", "agregar", "actualizar"}:
            if action != "actualizar":
                values = WorkTeam.model_validate(values).model_dump(exclude_unset=True)
            return teams.save_team(conn, pid, values, int(ident) if action == "actualizar" else None)
        current = _get(conn, table, ident)
        if current.project_id != pid or not current.is_work_team:
            raise ValueError("El equipo no pertenece al plan.")
        if action == "ver":
            return teams.team_view(current, repo.get(conn, "calendars", current.calendar_id))
    if special == "rutas" and action in {"crear", "agregar", "actualizar"}:
        values.update(is_route=True, route_code="")
    if action in {"crear", "agregar"}:
        if table == "baselines":
            values.setdefault("created_at", datetime.now().isoformat())
        validated = _schema(table).model_validate(values).model_dump()
        pid = _pid(conn, ctx, table)
        if pid is not None and table != "baseline_activities":
            validated = scoped_values(conn, table, pid, validated)
        if table == "projects" and validated["calendar_id"] is None:
            calendars = repo.list_entities(conn, "calendars")
            validated["calendar_id"] = next((c.id for c in calendars if c.name == "Chile L-V 8h"), calendars[0].id)
        if table == "baseline_activities":
            current = domain.BaselineActivity(**validated)
            _scope(conn, ctx, table, current)
            baseline = repo.get(conn, "baselines", current.baseline_id)
            if baseline.project_id != repo.get(conn, "activities", current.activity_id).project_id:
                raise ValueError("La línea base y la actividad pertenecen a proyectos distintos.")
            if conn.execute("SELECT 1 FROM baseline_activities WHERE baseline_id=? AND activity_id=?",
                            (current.baseline_id, current.activity_id)).fetchone():
                raise ValueError("La actividad ya existe en esa línea base; use actualizar.")
            repo.save_baseline_activities(conn, [current])
            return current
        return repo.create(conn, table, validated)
    current = _get(conn, table, ident)
    _scope(conn, ctx, table, current)
    if special == "rutas" and not current.is_route:
        raise ValueError("La tarea no es una ruta.")
    if action == "ver":
        return current
    if action == "actualizar":
        if table == "baseline_activities":
            for field in ("baseline_id", "activity_id"):
                if field in values and values[field] != getattr(current, field):
                    raise ValueError(f"No se puede cambiar el campo {field}.")
            validated = _schema(table).model_validate(dict(repo.json_value(current), **values)).model_dump()
            current = domain.BaselineActivity(**validated)
            repo.save_baseline_activities(conn, [current])
            return current
        updated = validated_update(conn, table, current.id, values)
        if table == "projects" and "data_date" in values:
            services.sync_progress(conn, current.id)
        return updated
    if action == "eliminar":
        if table == "projects" and confirm_name != current.name:
            raise CliError("uso", "Para eliminar el proyecto indique --confirmar-nombre con su nombre exacto.")
        if table == "baseline_activities":
            repo.delete_baseline_activity(conn, current.baseline_id, current.activity_id)
        else:
            repo.delete(conn, table, current.id)
        return {"deleted": True, "id": ident}
    raise ValueError(f"Operación desconocida: {action}.")


def _absence(conn: sqlite3.Connection, ctx: Context, action: str, ident: Any, values: Any) -> Any:
    from ... import repo, resource_absences
    from ...api.extras import Absence
    from ...api.dependencies import validated_update
    # No hay id de ausencia estable: la clave pública es RECURSO_ID:AAAA-MM-DD.
    if action in {"crear", "agregar"}:
        values = dict(values)
        if "resource_id" not in values:
            raise CliError("datos_invalidos", "Falta resource_id.", detail={"errors": [{"field": "resource_id", "message": "Campo obligatorio."}]})
        rid = int(values.pop("resource_id"))
    else:
        try:
            rid, day = str(ident).split(":", 1)
            rid = int(rid)
        except (ValueError, TypeError) as exc:
            raise ValueError("La clave de ausencia debe ser RECURSO_ID:AAAA-MM-DD.") from exc
    resource = repo.get(conn, "resources", rid)
    _scope(conn, ctx, "resources", resource)
    if action in {"crear", "agregar"}:
        return resource_absences.set_absence(conn, rid, **Absence.model_validate(values).model_dump())
    rows = resource_absences.list_absences(conn, rid)
    current = next((r for r in rows if str(r["date"]) == day), None)
    if current is None:
        raise LookupError("No existe esa ausencia en el calendario personal.")
    if action == "ver":
        return dict(current, resource_id=rid, id=ident)
    if action == "eliminar":
        calendar = repo.get(conn, "calendars", resource.calendar_id)
        validated_update(conn, "calendars", calendar.id, {"exceptions": [
            repo.json_value(ex) for ex in calendar.exceptions if str(ex.date) != day]})
        return {"deleted": True, "id": ident}
    merged = dict(start=day, end=day, hours=current["hours"], reason=current["reason"])
    merged.update(values)
    validated = Absence.model_validate(merged).model_dump()
    if str(validated["start"]) != day or str(validated["end"]) != day:
        raise ValueError("Actualizar una ausencia conserva su fecha; agregue otro intervalo para ampliarla.")
    return resource_absences.set_absence(conn, rid, **validated)


def list_rows(conn: sqlite3.Connection, ctx: Context, args: Any) -> dict:
    """Lista mediante SQL proyectado; sólo materializa la página solicitada."""
    from ... import repo
    from pydantic import TypeAdapter
    table, special = entity_name(args.entity)
    limit, offset = args.limite, args.desde
    if limit < 0 or offset < 0:
        raise ValueError("La paginación no admite valores negativos.")
    filters = {key[7:]: value for key, value in vars(args).items() if key.startswith("filter_") and value is not None}
    wanted = [s.strip() for s in args.campos.split(",")] if args.campos else None
    if special in {"equipos", "ausencias"}:
        if special == "equipos":
            from ...teams import list_teams
            rows = repo.json_value(list_teams(conn, ctx.require_project(conn)))
            allowed = {"id", "project_id", "name", "regime", "cycle_start", "members", "hours_day", "holidays_work", "calendar_name", "calendar_id", "capacity_hours_day"}
        else:
            from ...resource_absences import list_absences
            rid = filters.pop("resource_id", None)
            if rid is None:
                raise ValueError("Indique --resource_id para listar ausencias.")
            resource = repo.get(conn, "resources", int(rid))
            _scope(conn, ctx, "resources", resource)
            rows = [dict(repo.json_value(r), resource_id=int(rid), id=f"{rid}:{r['date']}") for r in list_absences(conn, int(rid))]
            allowed = {"resource_id", "id", "date", "working", "hours", "reason"}
        if (set(filters) | set(wanted or [])) - allowed:
            raise ValueError("Filtro o campo de proyección desconocido.")
        rows = [r for r in rows if all(str(r.get(k)).lower() == str(v).lower() for k, v in filters.items())]
        total = len(rows)
        rows = rows[offset:offset + limit]
        return {"items": [{k: r[k] for k in wanted} for r in rows] if wanted else rows,
                "total": total, "offset": offset, "limit": limit}
    columns = {f.name for f in fields(repo.ENTITIES[table])} - {"exceptions"}
    if wanted and set(wanted) - columns:
        raise ValueError("Campos desconocidos: " + ", ".join(sorted(set(wanted) - columns)))
    clauses, params = [], []
    pid = _pid(conn, ctx, table)
    if pid is not None:
        if table in {"assignments", "progress", "baseline_activities"}:
            clauses.append("activity_id IN (SELECT id FROM activities WHERE project_id=?)")
        elif table == "resources":
            clauses.append("(project_id=? OR project_id IS NULL)")
        else:
            clauses.append("project_id=?")
        params.append(pid)
    if special == "rutas":
        clauses.append("is_route=1")
    for key, value in filters.items():
        if key == "q" and table == "activities":
            clauses.append("(code LIKE ? OR name LIKE ?)")
            params.extend([f"%{value}%"] * 2)
        elif key in {"from", "to"} and table == "activities":
            from datetime import date
            date.fromisoformat(value)
            clauses.append("COALESCE(planned_finish,early_finish,actual_finish,due_date)>=?" if key == "from" else
                           "COALESCE(planned_start,early_start,actual_start,due_date)<=?")
            params.append(value)
        elif key == "resource_id" and table == "activities":
            clauses.append("id IN (SELECT activity_id FROM assignments WHERE resource_id=?)")
            params.append(int(value))
        elif key in columns:
            model_field = _schema(table).model_fields[key]
            if key in repo._JSON:
                value = json.loads(value)
            value = TypeAdapter(model_field.annotation).validate_python(value)
            clauses.append(f'"{key}" IS ?')
            params.append(repo._sql_value(value))
        else:
            raise ValueError(f"Filtro desconocido: {key}.")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    selected = ",".join(f'"{c}"' for c in wanted) if wanted else "*"
    order = "baseline_id,activity_id" if table == "baseline_activities" else "id"
    total = conn.execute(f"SELECT COUNT(*) FROM {table}{where}", params).fetchone()[0]
    rows = [dict(r) for r in conn.execute(f"SELECT {selected} FROM {table}{where} ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, offset])]
    converters = repo._converters(table)
    for row in rows:
        for key in row.keys() & converters.keys():
            if row[key] is not None:
                row[key] = repo.json_value(converters[key](row[key]))
    if table == "calendars" and wanted is None and rows:
        by_id = {r["id"]: r for r in rows}
        for row in rows:
            row["exceptions"] = []
        for ex in conn.execute("SELECT calendar_id,date,working,hours,reason FROM calendar_exceptions"):
            if ex["calendar_id"] in by_id:
                by_id[ex["calendar_id"]]["exceptions"].append({"date": ex["date"], "working": bool(ex["working"]), "hours": ex["hours"], "reason": ex["reason"] or ""})
    return {"items": rows, "total": total, "offset": offset, "limit": limit}


def entity_handler(args: Any, ctx: Context) -> Result:
    """Handler CRUD genérico con conexiones administradas por Context."""
    from ... import repo
    try:
        if args.action in {"listar", "ver"}:
            with closing(ctx.conn()) as conn:
                data = list_rows(conn, ctx, args) if args.action == "listar" else operate(conn, ctx, args.entity, "ver", args.id)
        else:
            values = read_json(args) if args.action in {"crear", "agregar", "actualizar"} else None
            with ctx.mutation(f"CLI {args.entity} {args.action}") as conn, repo.transaction(conn):
                data = operate(conn, ctx, args.entity, args.action, getattr(args, "id", None), values,
                               getattr(args, "confirmar_nombre", None))
        return Result(repo.json_value(data))
    except (ValueError, LookupError, sqlite3.IntegrityError, CliError) as exc:
        raise model_error(exc) from exc


def batch_handler(args: Any, ctx: Context) -> Result:
    """Aplica todas las filas o revierte el lote completo; índices base cero."""
    from ... import repo
    rows = read_json(args)
    if not isinstance(rows, list):
        raise CliError("datos_invalidos", "El lote debe contener una lista de operaciones.")
    errors, results = [], []
    with ctx.mutation("CLI lote aplicar") as conn, repo.transaction(conn):
        for index, row in enumerate(rows):
            try:
                if not isinstance(row, dict):
                    raise ValueError("La fila debe ser un objeto.")
                unknown = set(row) - {"op", "entidad", "id", "valores", "confirmar_nombre"}
                if unknown:
                    raise ValueError("Campos desconocidos: " + ", ".join(sorted(unknown)))
                if row.get("op") not in {"crear", "actualizar", "eliminar"}:
                    raise CliError("datos_invalidos", "Operación inválida.", detail={"errors": [{"field": "op", "message": "Use crear, actualizar o eliminar."}]})
                with repo.transaction(conn):
                    data = operate(conn, ctx, row.get("entidad", ""), row["op"], row.get("id"), row.get("valores", {}), row.get("confirmar_nombre"))
                results.append({"index": index, "data": repo.json_value(data)})
            except (ValueError, LookupError, sqlite3.IntegrityError, CliError, KeyError, TypeError) as exc:
                errors.extend(model_error(exc, index=index).detail["errors"])
        if errors:
            raise CliError("datos_invalidos", "Lote revertido: ninguna operación fue aplicada.", detail={"atomic": True, "errors": errors, "index_base": 0})
    return Result({"atomic": True, "results": results, "created_ids": [r["data"].get("id") for r, row in zip(results, rows) if row["op"] == "crear"], "errors": []})
