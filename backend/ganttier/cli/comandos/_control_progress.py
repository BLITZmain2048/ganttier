"""Adaptación de entradas CTL; las reglas de avance pertenecen a la API."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder

from ... import repo
from ...api import control, schemas
from ...api.dependencies import scoped_values
from .._base import CliError, Context, Result


def read_body(args: argparse.Namespace, *, csv_allowed: bool = False) -> Any:
    """Lee JSON o CSV UTF-8, incluido stdin, sin modificar los archivos de entrada."""
    if getattr(args, "json", None) is not None:
        return json.loads(args.json)
    filename = args.archivo
    if filename == "-":
        return json.load(sys.stdin)
    with Path(filename).open(encoding="utf-8-sig", newline="") as source:
        if csv_allowed and Path(filename).suffix.lower() == ".csv":
            return [{key: value for key, value in row.items() if value != ""}
                    for row in csv.DictReader(source)]
        return json.load(source)


def _entry(conn, pid, source, codes):
    if not isinstance(source, dict):
        raise ValueError("Cada fila debe ser un objeto.")
    aliases = {"actividad": "activity_id", "actividad_id": "activity_id", "fecha": "date",
               "porcentaje": "percent_complete", "hh_reales": "actual_hours",
               "inicio_real": "actual_start", "fin_real": "actual_finish"}
    values = {}
    for key, value in source.items():
        name = aliases.get(key, key)
        if name in values and values[name] != value:
            raise ValueError(f"Valores contradictorios para {name}.")
        values[name] = value
    code = values.pop("codigo", None)
    if code is not None:
        if code not in codes:
            rows = conn.execute("SELECT id FROM activities WHERE project_id=? AND code=?", (pid, code)).fetchall()
            if len(rows) != 1:
                raise ValueError(f"Código de actividad inexistente o ambiguo: {code}.")
            codes[code] = rows[0][0]
        if "activity_id" in values and int(values["activity_id"]) != codes[code]:
            raise ValueError("El código y el identificador señalan actividades distintas.")
        values["activity_id"] = codes[code]
    model = schemas.ProgressEntry.model_validate(values)
    scoped_values(conn, "progress", pid, model.model_dump())
    return model


def _record(conn, pid, sources, many, first_row):
    models, errors, codes = [], [], {}
    for number, row in enumerate(sources, first_row):
        try:
            models.append(_entry(conn, pid, row, codes))
        except (ValueError, LookupError, TypeError) as exc:
            errors.append({"row": number, "message": str(exc)})
    if errors:
        raise CliError("datos_invalidos", "El lote contiene errores; no se registró ninguna fila.", detail={"errors": errors})
    try:
        # La API conserva orden de entrada, infiere fechas en orden cronológico,
        # valida referencias y sincroniza solo las actividades afectadas.
        return control.create_progress(pid, models if many else models[0], conn)
    except (ValueError, LookupError, sqlite3.IntegrityError) as exc:
        # Diagnóstico solo tras un fallo: por actividad para preservar herencia
        # cronológica entre filas. Cada intento revierte su SAVEPOINT.
        groups = defaultdict(list)
        for number, model in enumerate(models, first_row):
            groups[model.activity_id].append((number, model))
        for group in groups.values():
            conn.execute("SAVEPOINT ctl_diagnosis")
            try:
                for number, model in sorted(group, key=lambda item: item[1].date):
                    try:
                        control.create_progress(pid, model, conn)
                    except (ValueError, LookupError, sqlite3.IntegrityError) as issue:
                        errors.append({"row": number, "message": str(issue)})
            finally:
                conn.execute("ROLLBACK TO ctl_diagnosis")
                conn.execute("RELEASE ctl_diagnosis")
        if not errors:
            errors = [{"row": number, "message": str(exc)} for number in range(first_row, first_row + len(models))]
        errors.sort(key=lambda error: error["row"])
        raise CliError("datos_invalidos", "El lote contiene errores; no se registró ninguna fila.", detail={"errors": errors}) from exc


def execute(args: argparse.Namespace, ctx: Context) -> Result:
    """Consulta o muta avance, corte y líneas base con el contrato HTTP."""
    operation = args._ctl
    read = operation.endswith(("/listar", "/ver"))
    with (closing(ctx.conn()) if read else ctx.mutation(operation)) as conn:
        pid = ctx.require_project(conn)
        if operation == "control/avance/listar":
            if args.desde < 0 or (args.limite is not None and args.limite < 1):
                raise ValueError("Paginación de avance inválida.")
            if args.limite is None and args.desde == 0:
                data = control.progress(pid, conn)
            else:
                data = {"rows": repo.list_entities(conn, "progress", pid, offset=args.desde, limit=args.limite or 200),
                        "total": conn.execute("SELECT COUNT(*) FROM progress WHERE activity_id IN (SELECT id FROM activities WHERE project_id=?)", (pid,)).fetchone()[0]}
        elif operation in {"control/avance/lote", "control/avance/registrar"}:
            many = operation.endswith("/lote")
            source = read_body(args, csv_allowed=many)
            if many and (not isinstance(source, list) or not source):
                raise ValueError("El lote debe ser una lista no vacía.")
            first = 2 if many and Path(args.archivo).suffix.lower() == ".csv" else 1
            with repo.transaction(conn, immediate=True):
                data = _record(conn, pid, source if many else [source], many, first)
        elif operation == "control/fecha-corte/ver":
            project = repo.get(conn, "projects", pid)
            data = {"data_date": project.data_date}
        elif operation == "control/fecha-corte/fijar":
            data = control.data_date(pid, schemas.DataDateRequest(data_date=args.fecha), conn)
        elif operation == "control/linea-base/listar":
            data = control.baselines(pid, conn)
        elif operation == "control/linea-base/crear":
            data = control.create_baseline(pid, schemas.BaselineRequest(name=args.nombre, activate=not args.sin_activar), conn)
        elif operation == "control/linea-base/activar":
            if repo.get(conn, "baselines", args.id).project_id != pid:
                raise ValueError("La línea base pertenece a otro proyecto.")
            data = control.activate_baseline(args.id, conn)
        else:
            raise CliError("uso", "Operación de control desconocida.")
        return Result(data=jsonable_encoder(data))
