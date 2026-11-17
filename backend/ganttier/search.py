"""Búsqueda local entre proyectos, con filtrado y límite en SQLite."""
from __future__ import annotations

import sqlite3
import unicodedata
from functools import lru_cache


@lru_cache(maxsize=4096)
def normalize(value: str | None) -> str:
    """Ignora mayúsculas y diacríticos; la caché es acotada, no una cartera."""
    value = (value or "").casefold()
    if value.isascii():
        return value
    return "".join(c for c in unicodedata.normalize("NFD", value)
                   if not unicodedata.combining(c))


def search(conn: sqlite3.Connection, q: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Código exacto, TAG exacto, prefijo de código/TAG, otras coincidencias."""
    term = normalize(q.strip())
    if len(term) < 2:
        raise ValueError("La búsqueda requiere al menos dos caracteres.")
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("El límite debe estar entre 1 y 200 y el desplazamiento no puede ser negativo.")
    # Los comodines escritos por el usuario son texto literal.
    literal = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn.create_function("p5_normalize", 1, normalize, deterministic=True)
    fields = ("code", "name", "location", "equipment", "required_skill")
    predicate = " OR ".join(f"p5_normalize(t.{f}) LIKE :contains ESCAPE '\\'" for f in fields)
    branches = []
    for table, kind, dates in (
        ("activities", "actividad", "COALESCE(t.planned_start,t.early_start), "
         "COALESCE(t.planned_finish,t.early_finish), t.percent_complete, t.periodic_task_id"),
        ("periodic_tasks", "tarea periódica", "NULL, NULL, NULL, t.id"),
    ):
        branches.append(f"""SELECT '{kind}' AS type, t.id, t.code, t.name,
            t.project_id, p.name AS project_name, p.mode AS project_mode,
            t.location, t.equipment, t.required_skill, {dates},
            CASE WHEN p5_normalize(t.code)=:term THEN 0
                 WHEN p5_normalize(t.equipment)=:term THEN 1
                 WHEN p5_normalize(t.code) LIKE :prefix ESCAPE '\\'
                   OR p5_normalize(t.equipment) LIKE :prefix ESCAPE '\\' THEN 2
                 ELSE 3 END AS relevance
            FROM {table} t JOIN projects p ON p.id=t.project_id
            WHERE {predicate}""")
    # El CTE da nombres uniformes a campos que no existen en la tarea maestra.
    sql = """WITH hits(type,id,code,name,project_id,project_name,project_mode,
        location,equipment,required_skill,planned_start,planned_finish,
        percent_complete,periodic_task_id,relevance) AS (""" + " UNION ALL ".join(branches) + """
        ) SELECT * FROM hits ORDER BY relevance, code COLLATE NOCASE, project_id, type, id
        LIMIT :limit OFFSET :offset"""
    rows = conn.execute(sql, {"term": term, "prefix": literal + "%", "contains": "%" + literal + "%",
                              "limit": limit, "offset": offset})
    result = []
    for row in rows:
        item = dict(row)
        item.pop("relevance")
        item["project"] = {"id": item.pop("project_id"), "name": item.pop("project_name"),
                           "mode": item.pop("project_mode")}
        result.append(item)
    return result
