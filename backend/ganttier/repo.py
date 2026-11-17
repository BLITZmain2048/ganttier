"""Repositorio tipado, operaciones masivas y transferencias atómicas entre proyectos."""
from __future__ import annotations

import calendar
import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime
from functools import lru_cache
from math import isfinite
from typing import Any, Iterator, Mapping, get_type_hints
from uuid import uuid4

import numpy as np

from . import domain as d

ENTITIES = {
    "projects": d.Project, "calendars": d.Calendar, "wbs": d.WBSNode,
    "activities": d.Activity, "relations": d.Relation, "resources": d.Resource,
    "assignments": d.Assignment, "periodic_tasks": d.PeriodicTask,
    "baselines": d.Baseline, "baseline_activities": d.BaselineActivity, "progress": d.ProgressEntry,
}
_EXTRA_COLUMNS = {
    "calendar_exceptions": {"id", "calendar_id", "date", "working", "hours", "reason"},
    "schedule_runs": {"id", "project_id", "created_at", "kind", "weights", "options", "metrics_before", "metrics_after", "result_path", "applied", "status", "elapsed_s"},
    "meta": {"key", "value"},
}
_JSON = {"checklist_codes", "team_ids", "skills", "weights", "options", "metrics_before", "metrics_after"}


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = False) -> Iterator[None]:
    """SAVEPOINT composable: una operación fallida nunca deja altas parciales."""
    name = "sp_" + uuid4().hex
    owned = immediate and not conn.in_transaction
    if owned:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(f"SAVEPOINT {name}")
    try:
        if immediate and not owned:
            # Reserva de escritura sin cambiar datos ni confirmar al llamador.
            conn.execute("UPDATE meta SET value=value WHERE 0")
        yield
        conn.execute(f"RELEASE SAVEPOINT {name}")
        if owned:
            conn.commit()
    except BaseException:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        conn.execute(f"RELEASE SAVEPOINT {name}")
        if owned:
            conn.rollback()
        raise


@lru_cache(maxsize=None)
def _columns(table: str) -> frozenset[str]:
    if table in ENTITIES:
        return frozenset(f.name for f in fields(ENTITIES[table]) if f.name != "exceptions")
    if table in _EXTRA_COLUMNS:
        return frozenset(_EXTRA_COLUMNS[table])
    raise ValueError("Tabla de datos desconocida.")


def _mapping(value: Any) -> dict:
    if is_dataclass(value):
        return {f.name: getattr(value, f.name) for f in fields(value)}
    return dict(value)


def json_value(value: Any) -> Any:
    """Normaliza dominio, fechas y escalares NumPy para JSON y respuestas HTTP."""
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, dict):
        return {json_value(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def _sql_value(value: Any) -> Any:
    if isinstance(value, (int, np.integer)) and not -(2**63) <= value < 2**63:
        raise ValueError("El número entero está fuera del rango admitido por la base de datos.")
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, dict)):
        return json.dumps(json_value(value), ensure_ascii=False, allow_nan=False)
    return value


@lru_cache(maxsize=None)
def _types(table: str) -> dict:
    return get_type_hints(ENTITIES[table])


@lru_cache(maxsize=None)
def _converters(table: str) -> dict:
    result = {}
    for key, typ in (_types(table).items() if table in ENTITIES else []):
        if typ is datetime:
            result[key] = datetime.fromisoformat
        elif typ is date or "datetime.date" in str(typ):
            result[key] = date.fromisoformat
        elif typ is bool:
            result[key] = bool
    for key in _JSON & _columns(table):
        result[key] = json.loads
    for key in {"working", "applied"} & _columns(table):
        result[key] = bool
    return result


def _decode(table: str, row: sqlite3.Row) -> Any:
    values = dict(zip(row.keys(), row))
    for key in values.keys() - _columns(table):
        del values[key]
    for key, convert in _converters(table).items():
        if values.get(key) is not None:
            values[key] = convert(values[key])
    return ENTITIES[table](**values) if table in ENTITIES else values


def _prepare(table: str, obj: Any) -> dict:
    values = _mapping(obj)
    unknown = set(values) - _columns(table) - ({"exceptions"} if table == "calendars" else set())
    if unknown:
        raise ValueError("Campos desconocidos: " + ", ".join(sorted(unknown)))
    values.pop("id", None)
    for key, value in values.items():
        typ = _types(table).get(key) if table in ENTITIES else None
        if typ in (int, float, bool) and value is None:
            raise ValueError(f"El campo {key} no admite NULL.")
        if isinstance(value, (float, np.floating)) and not isfinite(value):
            raise ValueError(f"El campo {key} debe ser finito.")
    if table == "activities" and values.get("macro_month"):
        month = values["macro_month"]
        try:
            if len(month) != 7:
                raise ValueError
            start = date.fromisoformat(month + "-01")
        except (ValueError, TypeError):
            raise ValueError("El mes macro debe tener formato YYYY-MM.") from None
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        explicit_start = values.get("window_start")
        explicit_end = values.get("window_end")
        values["window_start"] = max(start, date.fromisoformat(str(explicit_start))) if explicit_start else start
        values["window_end"] = min(end, date.fromisoformat(str(explicit_end))) if explicit_end else end
        if values["window_end"] < values["window_start"]:
            raise ValueError(f"Mes macro {month} sin intersección con la ventana explícita; revise mes e inicio/fin de ventana.")
    for key in ("duration_days", "remaining_duration", "work_hours", "actual_hours", "cost_budget", "actual_cost", "hours_per_day", "capacity_hours_day", "max_units", "crew_size", "units", "tol_before_days", "tol_after_days", "cost_hour", "overtime_cost_hour", "max_overtime_hours_day"):
        if values.get(key) is not None and values[key] < 0:
            raise ValueError(f"El campo {key} no puede ser negativo.")
    if "percent_complete" in values and not 0 <= values["percent_complete"] <= 100:
        raise ValueError("El avance debe estar entre 0 y 100.")
    if table == "periodic_tasks":
        from .teams import validate_batch_fields
        validate_batch_fields(values)
        for key in ("batch_full_day", "is_route", "is_daily_round"):
            if type(values.get(key, False)) is not bool:
                raise ValueError(f"{key} debe ser booleano.")
        if values.get("is_route") and values.get("route_code"):
            raise ValueError("Una ruta no puede pertenecer a otra ruta.")
        if type(values.get("batch_continuous_in_shift", False)) is not bool:
            raise ValueError("Lote_Continuo_En_Turno debe ser booleano (SI/NO).")
        tolerance = values.get("balance_tolerance_batches", 1)
        if type(tolerance) is not int or tolerance < 0:
            raise ValueError("La tolerancia de equidad debe ser un número entero no negativo de lotes.")
    if table == "periodic_tasks" and values.get("interval", 1) < 1:
        raise ValueError("El intervalo debe ser positivo.")
    for a, b in (("start_date", "end_date"), ("window_start", "window_end"), ("planned_start", "planned_finish"), ("actual_start", "actual_finish")):
        if values.get(a) and values.get(b) and str(values[a]) > str(values[b]):
            raise ValueError(f"El campo {b} no puede ser anterior a {a}.")
    return values


def _validate_references(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    """Valida referencias en bloque, incluidas pertenencia y recursos globales."""
    refs = {"wbs_id": "wbs", "parent_id": "wbs", "pred_id": "activities", "succ_id": "activities", "activity_id": "activities", "resource_id": "resources", "team_id": "resources", "periodic_task_id": "periodic_tasks", "calendar_id": "calendars", "project_id": "projects", "baseline_id": "baselines", "active_baseline_id": "baselines"}
    maps = {}
    for field, target in refs.items():
        ids = {row[field] for row in rows if row.get(field) is not None}
        if not ids:
            continue
        mapping = {}
        ids = list(ids)
        for start in range(0, len(ids), 900):
            part = ids[start:start + 900]
            selected = "id,project_id" if "project_id" in _columns(target) else "id"
            mapping.update({r[0]: r[1] if len(r) > 1 else None for r in conn.execute(f"SELECT {selected} FROM {target} WHERE id IN ({','.join('?' for _ in part)})", [_sql_value(value) for value in part])})
        if len(mapping) != len(ids):
            raise ValueError(f"Referencia inexistente en {field}.")
        maps[field] = mapping
    for row in rows:
        pid = row.get("project_id")
        if pid is None and row.get("activity_id") is not None:
            pid = maps["activity_id"][row["activity_id"]]
        for field, mapping in maps.items():
            value = row.get(field)
            if value is None or field in {"project_id", "calendar_id"}:
                continue
            owner = mapping[value]
            if pid is not None and owner != pid and not (field == "resource_id" and owner is None):
                raise ValueError(f"La referencia {field} pertenece a otro proyecto.")
        if table == "relations" and row.get("pred_id") == row.get("succ_id"):
            raise ValueError("Una actividad no puede depender de sí misma.")
    if table == "periodic_tasks" and any(row.get("team_ids") for row in rows):
        teams = {r["id"]: r for r in conn.execute("SELECT id,project_id,is_work_team,active FROM resources")}
        for row in rows:
            for rid in row.get("team_ids", []):
                team = teams.get(rid)
                if not team or not team["is_work_team"] or not team["active"] or team["project_id"] != row["project_id"]:
                    raise ValueError("Los equipos deben estar activos y pertenecer al mismo plan.")
    if table == "progress":
        validate_progress_entries(conn, rows, require_start=False)
    if table == "activities":
        cutoffs = {}
        for row in rows:
            if not row.get("actual_start") and not row.get("actual_finish"):
                continue
            pid = row["project_id"]
            if pid not in cutoffs:
                cutoffs[pid] = str(get(conn, "projects", pid).data_date or date.today())
            if any(row.get(field) and str(row[field]) > cutoffs[pid] for field in ("actual_start", "actual_finish")):
                raise ValueError("Las fechas reales no pueden ser posteriores a la fecha de corte.")


def validate_progress_entries(conn: sqlite3.Connection, rows: list[dict], *, require_start: bool = True) -> None:
    """Valida avance entrante; el repositorio admite snapshots históricos sin inicio."""
    if rows:
        starts = {}
        for row in sorted(rows, key=lambda r: str(r["date"])):
            aid = row["activity_id"]
            cutoff = str(row["date"])
            for field in ("actual_start", "actual_finish"):
                if row.get(field) and str(row[field]) > cutoff:
                    raise ValueError("Las fechas reales no pueden ser posteriores a la fecha del registro.")
            start = row.get("actual_start") or starts.get(aid)
            if not start:
                start = conn.execute("SELECT MIN(actual_start) FROM progress WHERE activity_id=? AND date<=?", (aid, cutoff)).fetchone()[0]
            if not start:
                start = conn.execute("SELECT actual_start FROM activities WHERE id=?", (aid,)).fetchone()[0]
            if require_start and (row.get("percent_complete", 0) >= 100 or row.get("actual_finish")) and not start:
                raise ValueError("Una actividad terminada requiere un inicio real.")
            if row.get("actual_finish") and start and str(row["actual_finish"]) < str(start):
                raise ValueError("El fin real no puede ser anterior al inicio real.")
            if start:
                starts[aid] = start


def _validate_relation_cycles(conn: sqlite3.Connection, pids: set[int]) -> None:
    """Kahn iterativo O(V+E), también para lotes importados."""
    for pid in pids:
        edges, degrees = defaultdict(list), defaultdict(int)
        for pred, succ in conn.execute("SELECT pred_id,succ_id FROM relations WHERE project_id=?", (pid,)):
            edges[pred].append(succ)
            degrees.setdefault(pred, 0)
            degrees[succ] += 1
        ready = [aid for aid, degree in degrees.items() if degree == 0]
        visited = 0
        while ready:
            visited += 1
            for succ in edges[ready.pop()]:
                degrees[succ] -= 1
                if degrees[succ] == 0:
                    ready.append(succ)
        if visited != len(degrees):
            raise ValueError("La red de relaciones contiene un ciclo.")


def get(conn: sqlite3.Connection, table: str, entity_id: int) -> Any:
    """Obtiene una entidad o lanza LookupError si no existe."""
    _columns(table)
    if not isinstance(entity_id, (int, np.integer)):
        raise ValueError("El identificador debe ser un número entero.")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (_sql_value(entity_id),)).fetchone()
    if row is None:
        raise LookupError("No se encontró el registro solicitado.")
    result = _decode(table, row)
    if table == "calendars":
        result.exceptions = [d.CalendarException(date.fromisoformat(r["date"]), bool(r["working"]), r["hours"], r["reason"] or "") for r in conn.execute("SELECT * FROM calendar_exceptions WHERE calendar_id=? ORDER BY date", (entity_id,))]
    return result


def list_entities(conn: sqlite3.Connection, table: str, project_id: int | None = None, *, offset: int = 0, limit: int | None = None, filters: Mapping[str, Any] | None = None) -> list:
    """Lectura masiva sin N+1; recursos del proyecto incluyen los globales."""
    columns = _columns(table)
    clauses, params = [], []
    if project_id is not None:
        if table in {"assignments", "progress"}:
            clauses.append("activity_id IN (SELECT id FROM activities WHERE project_id=?)")
        elif table == "resources":
            clauses.append("(project_id=? OR project_id IS NULL)")
        elif "project_id" in columns:
            clauses.append("project_id=?")
        else:
            raise ValueError("Esta tabla no pertenece directamente a un proyecto.")
        params.append(project_id)
    for key, value in (filters or {}).items():
        if value is None:
            continue
        if key == "q" and table == "activities":
            clauses.append("(code LIKE ? OR name LIKE ?)")
            params.extend([f"%{value}%"] * 2)
        elif key == "from" and table == "activities":
            clauses.append("COALESCE(planned_finish,early_finish,actual_finish,due_date)>=?")
            params.append(_sql_value(value))
        elif key == "to" and table == "activities":
            clauses.append("COALESCE(planned_start,early_start,actual_start,due_date)<=?")
            params.append(_sql_value(value))
        elif key == "resource_id" and table == "activities":
            clauses.append("id IN (SELECT activity_id FROM assignments WHERE resource_id=?)")
            params.append(_sql_value(value))
        elif key in columns:
            clauses.append(f"{key}=?")
            params.append(_sql_value(value))
        else:
            raise ValueError("Filtro desconocido.")
    order = "baseline_id,activity_id" if table == "baseline_activities" else "key" if table == "meta" else "id"
    query = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(clauses) if clauses else "") + f" ORDER BY {order}"
    if offset < 0 or (limit is not None and limit < 0):
        raise ValueError("La paginación no admite valores negativos.")
    query += " LIMIT ? OFFSET ?"
    params.extend([-1 if limit is None else _sql_value(limit), _sql_value(offset)])
    results = [_decode(table, row) for row in conn.execute(query, params)]
    if table == "calendars" and results:
        by_id = {cal.id: cal for cal in results}
        for row in conn.execute("SELECT * FROM calendar_exceptions ORDER BY date"):
            if row["calendar_id"] in by_id:
                by_id[row["calendar_id"]].exceptions.append(d.CalendarException(date.fromisoformat(row["date"]), bool(row["working"]), row["hours"], row["reason"] or ""))
    return results


def create_many(conn: sqlite3.Connection, table: str, objects: list[Any], *, return_entities: bool = True, _row_offset: int = 0) -> list:
    """Inserta lotes con executemany en una única transacción y devuelve las entidades."""
    # Mantiene acotadas las copias de diccionarios/tuplas sin fraccionar el COMMIT.
    if len(objects) > 2000:
        with transaction(conn):
            result = []
            for offset in range(0, len(objects), 2000):
                result.extend(create_many(conn, table, objects[offset:offset + 2000], return_entities=return_entities, _row_offset=_row_offset + offset))
            return result
    defaults = {}
    if table in ENTITIES:
        for item in fields(ENTITIES[table]):
            if item.name in {"id", "exceptions"}:
                continue
            if item.default is not MISSING:
                defaults[item.name] = item.default
            elif item.default_factory is not MISSING:
                defaults[item.name] = item.default_factory()
    rows = []
    for index, obj in enumerate(objects, _row_offset + 1):
        try:
            rows.append(_prepare(table, obj))
        except ValueError as exc:
            identity = _mapping(obj)
            label = identity.get("code") or identity.get("name") or identity.get("activity_id") or "sin código"
            raise ValueError(f"{table}, fila {index} del lote ({label}): {exc}") from exc
    if not rows:
        return []
    if table == "projects" and any(row.get("active_baseline_id") is not None for row in rows):
        raise ValueError("Cree el proyecto antes de asociar una línea base propia.")
    # Solo rellenar diferencias de forma: SQLite aplica sus defaults sin enviar 45 campos nulos.
    union = set().union(*(row.keys() for row in rows))
    if any(row.keys() != union for row in rows):
        for row in rows:
            for key in union - row.keys():
                if key in defaults:
                    row[key] = defaults[key]
    with transaction(conn):
        _validate_references(conn, table, rows)
        before = conn.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0] if return_entities or table == "calendars" else 0
        groups = defaultdict(list)
        for row in rows:
            exceptions = row.pop("exceptions", None)
            if exceptions is not None:
                if len(rows) != 1:
                    raise ValueError("Guarde cada calendario con sus excepciones por separado.")
            keys = tuple(sorted(row))
            groups[keys].append(tuple(_sql_value(row[k]) for k in keys))
        # Una forma de fila por lote mantiene el orden y la correlación de ids.
        if len(groups) > 1:
            return [create(conn, table, obj) for obj in objects]
        for keys, values in groups.items():
            conn.executemany(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", values)
        if table == "relations":
            _validate_relation_cycles(conn, {row["project_id"] for row in rows})
        if not return_entities and table != "calendars":
            return []
        results = [_decode(table, r) for r in conn.execute(f"SELECT * FROM {table} WHERE id>? ORDER BY id", (before,))]
        if table == "calendars":
            for obj, cal in zip(objects, results):
                _save_exceptions(conn, cal.id, _mapping(obj).get("exceptions", []))
                cal.exceptions = get(conn, table, cal.id).exceptions
        return results


def create(conn: sqlite3.Connection, table: str, obj: Any) -> Any:
    """Inserta una entidad, ignorando cualquier id proporcionado."""
    return create_many(conn, table, [obj])[0]


def _save_exceptions(conn: sqlite3.Connection, cid: int, exceptions: list) -> None:
    conn.execute("DELETE FROM calendar_exceptions WHERE calendar_id=?", (cid,))
    values = [_mapping(ex) for ex in exceptions]
    if any(ex.get("hours", 0) < 0 for ex in values):
        raise ValueError("Las horas de las excepciones no pueden ser negativas.")
    conn.executemany("INSERT INTO calendar_exceptions(calendar_id,date,working,hours,reason) VALUES (?,?,?,?,?)", [(cid, _sql_value(ex["date"]), ex["working"], ex.get("hours", 0), ex.get("reason", "")) for ex in values])


def update(conn: sqlite3.Connection, table: str, entity_id: int, changes: Mapping[str, Any]) -> Any:
    """Actualiza campos presentes y valida el estado combinado."""
    with transaction(conn, immediate=True):
        return _update_locked(conn, table, entity_id, changes)


def _update_locked(conn: sqlite3.Connection, table: str, entity_id: int, changes: Mapping[str, Any]) -> Any:
    current = _mapping(get(conn, table, entity_id))
    values = dict(changes)
    if "id" in values and values["id"] != entity_id:
        raise ValueError("No se puede cambiar el identificador.")
    if "project_id" in values and values["project_id"] != current.get("project_id"):
        if table != "resources":
            raise ValueError("No se puede trasladar un registro a otro proyecto.")
        # Un recurso puede pasar de un proyecto a ser global (sigue válido en su proyecto) o de global a
        # un proyecto, siempre que no quede referenciado desde actividades de otros proyectos.
        target = values["project_id"]
        if target is not None:
            if current.get("project_id") is not None:
                raise ValueError("No se puede trasladar un recurso de un proyecto a otro; hágalo global primero.")
            used = conn.execute(
                "SELECT (SELECT COUNT(*) FROM activities a WHERE a.project_id<>? AND (a.id IN "
                "(SELECT activity_id FROM assignments WHERE resource_id=?) OR a.id IN "
                "(SELECT activity_id FROM progress WHERE resource_id=?))) + "
                "(SELECT COUNT(*) FROM periodic_tasks WHERE project_id<>? AND resource_id=?)",
                (target, entity_id, entity_id, target, entity_id)).fetchone()[0]
            if used:
                raise ValueError("El recurso global está usado en otros proyectos; no puede restringirse a uno solo.")
    if table in {"assignments", "progress"} and values.get("activity_id", current["activity_id"]) != current["activity_id"]:
        if get(conn, "activities", values["activity_id"]).project_id != get(conn, "activities", current["activity_id"]).project_id:
            raise ValueError("No se puede trasladar un registro a otro proyecto.")
    values.pop("id", None)
    if table == "activities":
        if values.get("macro_month") and values["macro_month"] != current.get("macro_month") and current.get("macro_month"):
            old_start = date.fromisoformat(current["macro_month"] + "-01")
            old_end = old_start.replace(day=calendar.monthrange(old_start.year, old_start.month)[1])
            # A whole-month window is the shortcut itself. A narrower explicit
            # window remains a constraint when the month changes.
            if str(current.get("window_start")) == str(old_start) and str(current.get("window_end")) == str(old_end):
                for field in ("window_start", "window_end"):
                    if field not in values:
                        current[field] = None
        if "macro_month" not in values and {"window_start", "window_end"} & values.keys():
            values["macro_month"] = None
    current.update(values)
    if table == "projects" and current.get("active_baseline_id") is not None:
        if get(conn, "baselines", current["active_baseline_id"]).project_id != entity_id:
            raise ValueError("La línea base pertenece a otro proyecto.")
    merged = _prepare(table, current)
    if table == "activities" and values.get("macro_month"):
        values.update(window_start=merged["window_start"], window_end=merged["window_end"])
    # La validación de reparto necesita el objeto completo, ya validado arriba.
    # Validar el parche aislado rechaza, por ejemplo, editar solo el rendimiento.
    values = ({k: merged[k] for k in values if k != "id"} if table == "periodic_tasks"
              else _prepare(table, values))
    with transaction(conn):
        _validate_references(conn, table, [merged])
        if table == "wbs" and merged.get("parent_id"):
            seen, parent = {entity_id}, merged["parent_id"]
            while parent:
                if parent in seen:
                    raise ValueError("La jerarquía WBS contiene un ciclo.")
                seen.add(parent)
                parent = get(conn, "wbs", parent).parent_id
        exceptions = values.pop("exceptions", None)
        if values:
            conn.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in values)} WHERE id=?", [_sql_value(v) for v in values.values()] + [entity_id])
        if table == "relations":
            _validate_relation_cycles(conn, {merged["project_id"]})
        if exceptions is not None:
            _save_exceptions(conn, entity_id, exceptions)
        return get(conn, table, entity_id)


class ProtectedDeleteError(ValueError):
    """Borrar la entidad alteraría la historia de control (líneas base o avances registrados)."""

    def __init__(self, message: str, history: dict):
        super().__init__(message)
        self.history = history


def activity_history(conn: sqlite3.Connection, activity_ids: list[int]) -> dict:
    """Cuántas líneas base y registros de avance perderían las actividades si se borraran."""
    if not activity_ids:
        return {"activities": 0, "baselines": 0, "baseline_rows": 0, "progress": 0, "actual_hours": 0.0}
    marks = ",".join("?" * len(activity_ids))
    rows, baselines = conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT baseline_id) FROM baseline_activities WHERE activity_id IN ({marks})", activity_ids).fetchone()
    progress, hours = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(actual_hours),0) FROM progress WHERE activity_id IN ({marks})", activity_ids).fetchone()
    affected = conn.execute(f"""SELECT COUNT(*) FROM activities WHERE id IN ({marks}) AND (
        id IN (SELECT activity_id FROM baseline_activities) OR id IN (SELECT activity_id FROM progress))""", activity_ids).fetchone()[0]
    return {"activities": affected, "baselines": baselines, "baseline_rows": rows, "progress": progress, "actual_hours": float(hours)}


def audit(conn: sqlite3.Connection, project_id: int | None, action: str, entity: str, entity_id: int | None, detail: dict) -> None:
    """Registra una operación sensible en la bitácora (si la tabla existe)."""
    try:
        conn.execute("INSERT INTO audit_log(project_id, action, entity, entity_id, detail) VALUES (?,?,?,?,?)",
                     (project_id, action, entity, entity_id, json.dumps(detail, ensure_ascii=False, default=str)))
    except sqlite3.OperationalError:
        pass


def delete(conn: sqlite3.Connection, table: str, entity_id: int, *, force: bool = False) -> None:
    """Elimina una entidad; las dependencias siguen las reglas FK del esquema.

    D3: borrar una actividad (o una tarea periódica con ocurrencias) que figura en una línea base o
    que tiene avances registrados eliminaría también esa historia (BAC, PV y AC cambiarían hacia
    atrás). Sin ``force`` se rechaza con ``ProtectedDeleteError``; con ``force`` se borra y se deja
    constancia en la bitácora.
    """
    entity = get(conn, table, entity_id)
    with transaction(conn):
        if table in {"activities", "periodic_tasks"}:
            ids = [entity_id] if table == "activities" else [r[0] for r in conn.execute(
                "SELECT id FROM activities WHERE periodic_task_id=?", (entity_id,))]
            history = activity_history(conn, ids)
            if history["baseline_rows"] or history["progress"]:
                what = "la actividad" if table == "activities" else f"la tarea periódica ({history['activities']} ocurrencias con historia)"
                parts = []
                if history["baseline_rows"]:
                    parts.append(f"figura en {history['baselines']} línea(s) base")
                if history["progress"]:
                    parts.append(f"tiene {history['progress']} registro(s) de avance ({history['actual_hours']:.1f} HH reales)")
                message = (f"No se eliminó {what}: {' y '.join(parts)}. Borrarla cambiaría hacia atrás el presupuesto (BAC), "
                           "el valor planificado y las HH reales de los informes ya emitidos. Si de todos modos quiere "
                           "eliminarla, confirme la eliminación forzada.")
                if not force:
                    raise ProtectedDeleteError(message, history)
                audit(conn, entity.project_id, "eliminacion_forzada", table, entity_id,
                      {"codigo": getattr(entity, "code", ""), "nombre": getattr(entity, "name", ""), **history})
        if table == "resources" and entity.is_work_team:
            if conn.execute("SELECT 1 FROM periodic_tasks, json_each(periodic_tasks.team_ids) t WHERE t.value=?", (entity_id,)).fetchone():
                raise ValueError("El equipo está seleccionado en tareas maestras; retire esas referencias antes de eliminarlo.")
        if table == "baselines":
            conn.execute("UPDATE projects SET active_baseline_id=NULL WHERE active_baseline_id=?", (entity_id,))
        conn.execute(f"DELETE FROM {table} WHERE id=?", (entity_id,))


def patch_activities(conn: sqlite3.Connection, pid: int, patches: list[dict]) -> list[d.Activity]:
    """Edición masiva atómica, sin permitir registros de otros proyectos."""
    with transaction(conn):
        results = []
        for patch in patches:
            aid = patch.get("id")
            if aid is None or get(conn, "activities", aid).project_id != pid:
                raise ValueError("La actividad no pertenece al proyecto.")
            results.append(update(conn, "activities", aid, patch))
        return results


def activities_columnar(conn: sqlite3.Connection, pid: int, *, encoded: bool = False) -> dict[str, list] | bytes:
    """Gantt con un SELECT; JSON directo evita materializar millones de valores.

    La salida predeterminada conserva las listas para los consumidores internos.
    """
    names = "ids codes names starts finishes es ef ls lf total_float critical pct wbs_ids baseline_starts baseline_finishes window_starts window_ends kinds durations work_hours macro_months skills locations statuses outages window_hards".split()
    result = {name: [] for name in names}
    sql = """SELECT a.id,a.code,a.name,COALESCE(a.planned_start,a.early_start,a.actual_start),
        COALESCE(a.planned_finish,a.early_finish,a.actual_finish),a.early_start,a.early_finish,
        a.late_start,a.late_finish,a.total_float,a.critical,a.percent_complete,a.wbs_id,
        b.start,b.finish,a.window_start,a.window_end,
        CASE WHEN a.duration_days=0 THEN 'hito' WHEN a.periodic_task_id IS NOT NULL THEN 'mantenimiento' ELSE 'actividad' END,
        a.duration_days,a.work_hours,a.macro_month,COALESCE(a.required_skill,''),COALESCE(a.location,''),a.status,a.requires_outage,a.window_hard
        FROM activities a JOIN projects p ON p.id=a.project_id
        LEFT JOIN baseline_activities b ON b.activity_id=a.id AND b.baseline_id=p.active_baseline_id
        WHERE a.project_id=? ORDER BY a.id"""
    if encoded:
        # Los identificadores proceden exclusivamente de la lista fija anterior.
        # La CTE conserva orden, nulos, texto Unicode y fechas ISO del contrato.
        columns = []
        for name in names:
            value = ("json(CASE WHEN window_hards THEN 'true' ELSE 'false' END)"
                     if name == "window_hards" else name)
            if name in ("total_float", "pct", "durations", "work_hours"):
                # JSON1 redondea los REAL a 15 cifras. Python conserva el
                # round-trip; los enteros exactos y NULL no necesitan callback.
                value = (f"CASE WHEN typeof({name})='real' AND "
                         f"({name} != CAST({name} AS INTEGER) OR abs({name})>=1000000000000000) "
                         f"THEN json(_ganttier_json_real({name})) ELSE {name} END")
            columns.append(f"'{name}',json_group_array({value})")
        query = ("WITH rows (" + ",".join(names) + ") AS (" + sql + ") "
                 "SELECT json_object(" + ",".join(columns) + ") FROM rows")
        conn.create_function("_ganttier_json_real", 1, repr)
        try:
            return conn.execute(query, (pid,)).fetchone()[0].encode("utf-8")
        finally:
            conn.create_function("_ganttier_json_real", 1, None)
    cursor = conn.execute(sql, (pid,))
    arrays = list(result.values())
    while batch := cursor.fetchmany(4000):
        for array, column in zip(arrays, zip(*batch)):
            array.extend(column)
    result["window_hards"] = [bool(value) for value in result["window_hards"]]
    return result


def save_occurrences(conn: sqlite3.Connection, pid: int, occurrences: list[d.Occurrence], replace: bool = True, *, return_entities: bool = True) -> list[d.Activity] | int:
    """Persiste ocurrencias y asignaciones fijas sin recalcular su generación."""
    get(conn, "projects", pid)
    with transaction(conn):
        tasks = {t.id: t for t in list_entities(conn, "periodic_tasks", pid)}
        wbs = {w.code: w.id for w in list_entities(conn, "wbs", pid)}
        existing = {a.code: a for a in list_entities(conn, "activities", pid)}
        recorded = {r[0] for r in conn.execute("SELECT DISTINCT activity_id FROM progress WHERE activity_id IN (SELECT id FROM activities WHERE project_id=?)", (pid,))}
        protected = recorded | {a.id for a in existing.values() if a.locked or a.percent_complete or a.actual_start or a.actual_finish}
        generated_codes = {o.code for o in occurrences}
        if replace:
            baseline_ids = {r[0] for r in conn.execute("SELECT activity_id FROM baseline_activities WHERE baseline_id IN (SELECT id FROM baselines WHERE project_id=?)", (pid,))}
            stale = [a.id for a in existing.values() if a.periodic_task_id is not None and a.code not in generated_codes and a.id not in protected | baseline_ids]
            conn.executemany("DELETE FROM activities WHERE id=?", [(aid,) for aid in stale])
        values, fresh = [], []
        for occurrence in occurrences:
            task = tasks.get(occurrence.periodic_task_id)
            if task is None:
                raise ValueError("La tarea periódica no pertenece al proyecto.")
            row = {k: v for k, v in _mapping(occurrence).items() if k in _columns("activities")}
            cost = task.cost_per_occurrence * occurrence.quantity / task.quantity_total if occurrence.batch_index is not None else task.cost_per_occurrence
            row.update(project_id=pid, cost_budget=cost, voltage_kv=task.voltage_kv, discipline=task.discipline, wbs_id=wbs.get(task.wbs_code))
            previous = existing.get(occurrence.code)
            if previous:
                if previous.periodic_task_id != occurrence.periodic_task_id:
                    raise ValueError("El código de ocurrencia ya pertenece a otra actividad.")
                if replace and previous.id not in protected:
                    update(conn, "activities", previous.id, row)
                    conn.execute("DELETE FROM assignments WHERE activity_id=?", (previous.id,))
                    if occurrence.resource_id is not None or occurrence.required_skill:
                        create(conn, "assignments", dict(activity_id=previous.id, resource_id=occurrence.resource_id, skill=occurrence.required_skill, units=occurrence.crew_size, work_hours=occurrence.work_hours))
                continue
            values.append(row)
            fresh.append(occurrence)
        activities = create_many(conn, "activities", values, return_entities=return_entities)
        if return_entities:
            ids = [a.id for a in activities]
        else:
            by_code = dict(conn.execute("SELECT code,id FROM activities WHERE project_id=? AND periodic_task_id IS NOT NULL", (pid,)))
            ids = [by_code[o.code] for o in fresh]
        assignments = [{"activity_id": aid, "resource_id": o.resource_id, "skill": o.required_skill, "units": o.crew_size, "work_hours": o.work_hours} for aid, o in zip(ids, fresh) if o.resource_id is not None or o.required_skill]
        create_many(conn, "assignments", assignments, return_entities=False)
        return activities if return_entities else len(fresh)


def _clean(table: str, row: dict) -> dict:
    return {k: v for k, v in row.items() if k in _columns(table) and k not in {"id", "project_id", "active_baseline_id", "created_at"}}


def import_calendar(conn: sqlite3.Connection, source: dict, calendars: dict[str, int]) -> int:
    """Reutiliza un nombre compartido solo si la definición completa coincide."""
    row = _clean("calendars", source)
    row["exceptions"] = source.get("exceptions", [])
    name = row["name"]
    if name not in calendars:
        calendars[name] = create(conn, "calendars", row).id
    else:
        existing = get(conn, "calendars", calendars[name])
        incoming = d.Calendar(id=0, **row)
        def definition(cal):
            values = _mapping(cal)
            values.pop("id")
            values["exceptions"] = sorted(
                [json_value(_mapping(d.CalendarException(**_mapping(ex)))) for ex in cal.exceptions],
                key=lambda ex: ex["date"])
            return json_value(values)
        if definition(existing) != definition(incoming):
            raise ValueError(f"El calendario '{name}' del libro difiere del existente. Cambie el nombre o iguale su definición y excepciones antes de importar.")
    return calendars[name]


def save_import_bundle(conn: sqlite3.Connection, pid: int, bundle: d.ImportBundle, mode: str = "merge") -> dict[str, int]:
    """Resuelve nombres/códigos y guarda todo el libro en una transacción."""
    project = get(conn, "projects", pid)
    if bundle.has_errors:
        raise ValueError("El libro contiene errores; corrija las filas antes de importar.")
    if mode not in {"merge", "replace"} or bundle.mode != project.mode:
        raise ValueError("Modo de importación incompatible con el proyecto.")
    def ref(mapping: dict, value: Any, field: str) -> int | None:
        if value in (None, ""):
            return None
        if value not in mapping:
            raise ValueError(f"No se encontró {field}: {value}.")
        return mapping[value]
    with transaction(conn, immediate=True):
        if mode == "replace":
            conn.execute("UPDATE projects SET active_baseline_id=NULL WHERE id=?", (pid,))
            for table in ("baselines", "schedule_runs", "activities", "periodic_tasks", "wbs", "resources"):
                conn.execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
        calendars = {c.name: c.id for c in list_entities(conn, "calendars")}
        for source in bundle.calendars:
            import_calendar(conn, source, calendars)
        project_values = _clean("projects", bundle.project)
        if bundle.project.get("calendar_name"):
            project_values["calendar_id"] = ref(calendars, bundle.project["calendar_name"], "calendario del proyecto")
        if project_values:
            update(conn, "projects", pid, project_values)
        available_resources = list_entities(conn, "resources", pid)
        resources = {r.name: r.id for r in available_resources}
        if len(resources) != len(available_resources):
            raise ValueError("Hay recursos con nombres repetidos; asigne nombres inequívocos antes de importar.")
        for source in bundle.resources:
            row = _clean("resources", source)
            row["project_id"] = pid
            if "calendar_name" in source:
                row["calendar_id"] = ref(calendars, source["calendar_name"], "calendario")
            if row["name"] in resources:
                rid = resources[row["name"]]
                if get(conn, "resources", rid).project_id == pid:
                    update(conn, "resources", rid, row)
            else:
                resources[row["name"]] = create(conn, "resources", row).id
        from .teams import save_team
        for source in bundle.teams:
            team = save_team(conn, pid, source, resources.get(source["name"]))
            resources[team["name"]] = team["id"]
        wbs = {w.code: w.id for w in list_entities(conn, "wbs", pid)}
        for source in bundle.wbs:
            row = _clean("wbs", source)
            row.update(project_id=pid)
            if row["code"] in wbs:
                update(conn, "wbs", wbs[row["code"]], row)
            else:
                wbs[row["code"]] = create(conn, "wbs", row).id
        for source in bundle.wbs:
            if "parent_code" in source:
                update(conn, "wbs", wbs[source["code"]], {"parent_id": ref(wbs, source["parent_code"], "WBS padre")})
        for table in ("periodic_tasks", "activities"):
            existing = {obj.code: obj.id for obj in list_entities(conn, table, pid)}
            new = []
            tasks = {t.code: t.id for t in list_entities(conn, "periodic_tasks", pid)}
            for source in getattr(bundle, table):
                row = _clean(table, source)
                row["project_id"] = pid
                for field, mapping, label in (("calendar", calendars, "calendario"), ("resource", resources, "recurso")):
                    if field + "_name" in source and field + "_id" in _columns(table):
                        row[field + "_id"] = ref(mapping, source[field + "_name"], label)
                if table == "periodic_tasks" and "team_names" in source:
                    row["team_ids"] = [ref(resources, name, "equipo de trabajo") for name in source["team_names"]]
                if table == "activities":
                    if "team_name" in source:
                        row["team_id"] = ref(resources, source["team_name"], "equipo de trabajo")
                    if "wbs_code" in source:
                        row["wbs_id"] = ref(wbs, source["wbs_code"], "WBS")
                    if "periodic_task_code" in source:
                        row["periodic_task_id"] = ref(tasks, source["periodic_task_code"], "tarea periódica")
                if row["code"] in existing:
                    update(conn, table, existing[row["code"]], row)
                else:
                    new.append(row)
            create_many(conn, table, new)
        acts = {a.code: a.id for a in list_entities(conn, "activities", pid)}
        existing_relations = {(r.pred_id, r.succ_id): r.id for r in list_entities(conn, "relations", pid)}
        rels = []
        for source in bundle.relations:
            row = _clean("relations", source)
            row.update(project_id=pid, pred_id=ref(acts, source.get("pred_code"), "predecesora"), succ_id=ref(acts, source.get("succ_code"), "sucesora"))
            key = (row["pred_id"], row["succ_id"])
            if key in existing_relations:
                update(conn, "relations", existing_relations[key], row)
            else:
                rels.append(row)
        create_many(conn, "relations", rels)
        for table in ("assignments", "progress"):
            values = []
            for source in getattr(bundle, table):
                row = _clean(table, source)
                row["activity_id"] = ref(acts, source.get("activity_code"), "actividad")
                row["resource_id"] = ref(resources, source.get("resource_name"), "recurso")
                values.append(row)
            # Reimportación idempotente por actividad para asignaciones; por fecha/recurso para avance.
            if table == "assignments":
                conn.executemany("DELETE FROM assignments WHERE activity_id=?", [(aid,) for aid in {v["activity_id"] for v in values}])
            else:
                seen = set()
                for value in values:
                    key = (value["activity_id"], _sql_value(value["date"]), value["resource_id"])
                    matches = list(conn.execute("SELECT * FROM progress WHERE activity_id=? AND date=? AND resource_id IS ?", key))
                    if key in seen or len(matches) > 1:
                        raise ValueError("Avance ambiguo: hay varias filas para la misma actividad, fecha y trabajador. Corrija el historial antes de importar.")
                    seen.add(key)
                    if matches:
                        update(conn, "progress", matches[0]["id"], value)
                        continue
                    # Sin identidad estable, completar/quitar trabajador puede duplicar el consumo.
                    candidates = conn.execute("SELECT * FROM progress WHERE activity_id=? AND date=?", key[:2])
                    if any((other["resource_id"] is None or key[2] is None)
                           and all(other[field] == value.get(field, 0) for field in ("percent_complete", "actual_hours", "actual_cost"))
                           for other in candidates):
                        raise ValueError("Avance ambiguo: el consumo ya existe con otra atribución de trabajador. Corrija el registro existente antes de importar.")
                    # Insertar ahora permite detectar también colisiones dentro del lote.
                    create(conn, "progress", value)
                continue
            create_many(conn, table, values)
        return {table: len(getattr(bundle, table)) for table in ("calendars", "resources", "wbs", "activities", "relations", "assignments", "periodic_tasks", "progress")}


def export_bundle(conn: sqlite3.Connection, pid: int, *, nominal: bool = False) -> d.ImportBundle:
    """Reconstruye referencias legibles para un libro reimportable."""
    project = get(conn, "projects", pid)
    calendars = list_entities(conn, "calendars")
    resources = list_entities(conn, "resources", pid)
    if len({r.name for r in resources}) != len(resources):
        raise ValueError("Hay recursos con nombres repetidos; asigne nombres inequívocos antes de exportar.")
    wbs = list_entities(conn, "wbs", pid)
    acts = list_entities(conn, "activities", pid)
    tasks = list_entities(conn, "periodic_tasks", pid)
    names = {"calendar_id": {c.id: c.name for c in calendars}, "resource_id": {r.id: r.name for r in resources}, "wbs_id": {w.id: w.code for w in wbs}, "parent_id": {w.id: w.code for w in wbs}, "activity_id": {a.id: a.code for a in acts}, "pred_id": {a.id: a.code for a in acts}, "succ_id": {a.id: a.code for a in acts}, "periodic_task_id": {t.id: t.code for t in tasks}}
    aliases = {"calendar_id": "calendar_name", "resource_id": "resource_name", "wbs_id": "wbs_code", "parent_id": "parent_code", "activity_id": "activity_code", "pred_id": "pred_code", "succ_id": "succ_code", "periodic_task_id": "periodic_task_code"}
    def convert(obj: Any) -> dict:
        row = d.to_dict(obj)
        if "team_ids" in row:
            row["team_names"] = [names["resource_id"][rid] for rid in row.pop("team_ids")]
        if "team_id" in row:
            row["team_name"] = names["resource_id"].get(row.pop("team_id"), "")
        for field, mapping in names.items():
            if field in row:
                row[aliases[field]] = mapping.get(row.pop(field), "")
        for field in ("id", "project_id", "active_baseline_id"):
            row.pop(field, None)
        return row
    assignments = list_entities(conn, 'assignments', pid)
    if nominal:
        from .role_schedules import nominal_assignments
        assignments = nominal_assignments(conn, pid)
    from .teams import list_teams
    team_fields = {f.name for f in fields(d.WorkTeam)}
    teams = [{k: v for k, v in json_value(t).items() if k in team_fields} for t in list_teams(conn, pid)]
    return d.ImportBundle(mode=project.mode, project=convert(project), teams=teams, calendars=[convert(c) for c in calendars], resources=[convert(r) for r in resources], wbs=[convert(w) for w in wbs], activities=[convert(a) for a in acts], periodic_tasks=[convert(t) for t in tasks], assignments=[convert(a) for a in assignments], **{table: [convert(obj) for obj in list_entities(conn, table, pid)] for table in ("relations", "progress")})


def duplicate_project(conn: sqlite3.Connection, pid: int) -> d.Project:
    """Duplica estructura, recursos y avance con referencias nuevas; sin historial de corridas."""
    bundle = export_bundle(conn, pid)
    with transaction(conn):
        values = _clean("projects", bundle.project)
        values["name"] = values["name"] + " (copia)"
        values["calendar_id"] = get(conn, "projects", pid).calendar_id
        project = create(conn, "projects", values)
        bundle.project["name"] = project.name
        save_import_bundle(conn, project.id, bundle)
        # Conserva las líneas base remapeando sus actividades por código.
        old_activities = {a.id: a.code for a in list_entities(conn, "activities", pid)}
        new_activities = {a.code: a.id for a in list_entities(conn, "activities", project.id)}
        original = get(conn, "projects", pid)
        for baseline in list_entities(conn, "baselines", pid):
            new_baseline = create(conn, "baselines", {"project_id": project.id, "name": baseline.name})
            entries = list_entities(conn, "baseline_activities", filters={"baseline_id": baseline.id})
            save_baseline_activities(conn, [d.BaselineActivity(new_baseline.id, new_activities[old_activities[e.activity_id]], e.start, e.finish, e.work_hours, e.cost, e.duration_days) for e in entries])
            if baseline.id == original.active_baseline_id:
                project = update(conn, "projects", project.id, {"active_baseline_id": new_baseline.id})
        return project


def save_baseline_activities(conn: sqlite3.Connection, entries: list[d.BaselineActivity]) -> None:
    """Alta/actualización masiva de las filas con clave compuesta de una línea base."""
    rows = [_mapping(entry) for entry in entries]
    with transaction(conn):
        _validate_references(conn, "baseline_activities", rows)
        conn.executemany("""INSERT INTO baseline_activities(baseline_id,activity_id,start,finish,work_hours,cost,duration_days)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(baseline_id,activity_id) DO UPDATE SET
            start=excluded.start,finish=excluded.finish,work_hours=excluded.work_hours,cost=excluded.cost,duration_days=excluded.duration_days""", [tuple(_sql_value(row[k]) for k in ("baseline_id", "activity_id", "start", "finish", "work_hours", "cost", "duration_days")) for row in rows])


def delete_baseline_activity(conn: sqlite3.Connection, baseline_id: int, activity_id: int) -> None:
    """Elimina una fila de línea base por su clave compuesta."""
    with transaction(conn):
        conn.execute("DELETE FROM baseline_activities WHERE baseline_id=? AND activity_id=?", (baseline_id, activity_id))


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    """Guarda un metadato; None elimina la clave."""
    with transaction(conn):
        if value is None:
            conn.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            conn.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
