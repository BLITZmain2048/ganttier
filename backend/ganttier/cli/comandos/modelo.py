"""CLI de modelo, maestros e intercambio; registro descubierto por core.py.

Todos los handlers aceptan (args, ctx). Las mutaciones de base se ejecutan sólo
mediante Context.mutation; exportaciones y plantillas son artefactos explícitos.
"""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from dataclasses import fields
from typing import Any

from .._base import CliError, Context, Result
from ._modelo_crud import (ALIASES, batch_handler, entity_handler, model_error,
                          operate, read_json)
from ._modelo_io import export_handler, import_handler, template_handler


def project_handler(args: Any, ctx: Context) -> Result:
    """Resuelve el proyecto por id o nombre y reutiliza resumen y duplicación."""
    from ... import repo, services
    try:
        if args.action == "listar" or args.action in {"crear", "agregar"}:
            return entity_handler(args, ctx)
        token = getattr(args, "id", None) or ctx.project
        if args.action in {"ver", "resumen"}:
            with closing(ctx.conn()) as conn:
                pid = ctx.resolve_project(conn, token)
                if args.action == "resumen":
                    return Result(repo.json_value(services.project_summary(conn, pid)))
                project = repo.json_value(repo.get(conn, "projects", pid))
                project["counts"] = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE project_id=?", (pid,)).fetchone()[0]
                                     for t in ("activities", "resources", "wbs", "relations", "periodic_tasks", "baselines", "schedule_runs")}
                return Result(project)
        values = read_json(args) if args.action == "actualizar" else None
        with ctx.mutation(f"CLI proyectos {args.action}") as conn, repo.transaction(conn):
            pid = ctx.resolve_project(conn, token)
            if args.action == "duplicar":
                result = repo.duplicate_project(conn, pid)
            else:
                result = operate(conn, ctx, "projects", args.action, pid, values, getattr(args, "confirmar_nombre", None))
        return Result(repo.json_value(result))
    except (ValueError, LookupError, sqlite3.IntegrityError, CliError) as exc:
        raise model_error(exc) from exc


def chain_handler(args: Any, ctx: Context) -> Result:
    """Crea una cadena de códigos o grupos usando RelationChain y el servicio API."""
    from ... import repo, tools
    from ...api.tools import RelationChain
    try:
        values = read_json(args)
        if not isinstance(values, dict):
            raise ValueError("La cadena debe ser un objeto JSON.")
        values = dict(values)
        for spanish, english in (("codigos", "groups"), ("tipo", "type"), ("desfase", "lag_days")):
            if spanish in values:
                if english in values:
                    raise ValueError(f"Use sólo {spanish} o {english}.")
                value = values.pop(spanish)
                if spanish == "codigos":
                    if not isinstance(value, list) or not all(isinstance(code, str) for code in value):
                        raise ValueError("codigos debe ser una lista de códigos de actividad.")
                    value = [value]
                values[english] = value
        request = RelationChain.model_validate(values)
        with ctx.mutation("CLI relaciones cadena") as conn, repo.transaction(conn):
            result = tools.relation_chain(conn, ctx.require_project(conn), request.groups, request.type, request.lag_days)
        return Result(repo.json_value(result))
    except (ValueError, LookupError, sqlite3.IntegrityError, CliError) as exc:
        raise model_error(exc) from exc


def demo_handler(args: Any, ctx: Context) -> Result:
    """Crea los ejemplos oficiales de proyecto o mantenimiento en modo seguro."""
    from ... import repo, services
    if not 1 <= args.n <= 100000:
        raise CliError("datos_invalidos", "--n debe estar entre 1 y 100000.")
    with ctx.mutation(f"CLI demo {args.kind}") as conn, repo.transaction(conn):
        result = services.demo_project(conn) if args.kind == "proyecto" else services.demo_maintenance(args.n, conn)
    return Result(repo.json_value(result))


def _json_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Objeto JSON con nombres de campos del dominio.")
    group.add_argument("--archivo", help="Archivo JSON UTF-8; '-' lee stdin.")


def _listing(parser: argparse.ArgumentParser, names: set[str]) -> None:
    parser.add_argument("--limite", type=int, default=1000, help="Máximo de filas (0 sólo cuenta).")
    parser.add_argument("--desde", type=int, default=0, help="Desplazamiento base cero.")
    parser.add_argument("--campos", help="Proyección: campos separados por coma.")
    for name in sorted(names):
        flags = ["--" + name]
        if "_" in name:
            flags.append("--" + name.replace("_", "-"))
        parser.add_argument(*flags, dest="filter_" + name, help=f"Filtrar por {name} (igualdad).")


def register(sub: argparse._SubParsersAction) -> None:
    """Registra todos los maestros de repo.ENTITIES y las operaciones de §4.2."""
    from ... import domain as d  # sin numpy: `describir` y `estado` no deben cargar el motor
    entities = {"projects": d.Project, "calendars": d.Calendar, "wbs": d.WBSNode, "activities": d.Activity,
                "relations": d.Relation, "resources": d.Resource, "assignments": d.Assignment,
                "periodic_tasks": d.PeriodicTask, "baselines": d.Baseline,
                "baseline_activities": d.BaselineActivity, "progress": d.ProgressEntry}  # = repo.ENTITIES (ver tests/cli/test_integracion.py)
    for table, cls in entities.items():
        name = ALIASES.get(table, table)
        aliases = [] if table == name else [table]
        if table == "baselines":
            aliases.extend(["líneas-base", "lineas_base"])
        group = sub.add_parser(name, aliases=aliases, help=f"Consultar y editar {table}.")
        actions = group.add_subparsers(dest="action", required=True)
        names = {f.name for f in fields(cls)} - {"exceptions"}
        if table == "activities":
            names |= {"q", "from", "to", "resource_id"}
        handler = project_handler if table == "projects" else entity_handler
        for action in ("listar", "ver", "agregar", "actualizar", "eliminar"):
            p = actions.add_parser(action, aliases=["crear"] if action == "agregar" else [], help=f"{action.capitalize()} {name}.")
            p.set_defaults(handler=handler, entity=table, action=action)
            if action == "listar":
                _listing(p, names)
            elif action in {"ver", "actualizar", "eliminar"}:
                p.add_argument("id", nargs="?" if table == "projects" else None,
                               help="Id o nombre exacto (-p por defecto)." if table == "projects" else
                               "BASELINE_ID:ACTIVITY_ID" if table == "baseline_activities" else "Id numérico.")
            if action in {"agregar", "actualizar"}:
                _json_arguments(p)
            if action == "eliminar" and table == "projects":
                p.add_argument("--confirmar-nombre", required=True, help="Nombre exacto del proyecto.")
        if table == "projects":
            for action in ("resumen", "duplicar"):
                p = actions.add_parser(action, help=f"{action.capitalize()} el proyecto seleccionado.")
                p.add_argument("id", nargs="?", help="Id o nombre exacto; por defecto -p.")
                p.set_defaults(handler=project_handler, action=action, entity=table)
        if table == "relations":
            p = actions.add_parser("cadena", help="Encadenar códigos o grupos con FS/SS/FF/SF.")
            _json_arguments(p)
            p.set_defaults(handler=chain_handler)
    for name, alias in (("equipos", "teams"), ("rutas", "routes"), ("ausencias", "absences")):
        group = sub.add_parser(name, aliases=[alias], help=f"Administrar {name} mediante sus servicios de dominio.")
        actions = group.add_subparsers(dest="action", required=True)
        for action in ("listar", "ver", "agregar", "actualizar", "eliminar"):
            p = actions.add_parser(action, aliases=["crear"] if action == "agregar" else [], help=f"{action.capitalize()} {name}.")
            p.set_defaults(handler=entity_handler, entity=name, action=action)
            if action == "listar":
                names = {"id", "name", "regime", "members", "hours_day", "project_id", "calendar_id", "calendar_name", "cycle_start", "holidays_work", "capacity_hours_day"} if name == "equipos" else {"resource_id", "id", "date", "working", "hours", "reason"} if name == "ausencias" else {f.name for f in fields(d.PeriodicTask)}
                _listing(p, names)
            elif action in {"ver", "actualizar", "eliminar"}:
                p.add_argument("id", help="RECURSO_ID:AAAA-MM-DD" if name == "ausencias" else "Id numérico.")
            if action in {"agregar", "actualizar"}:
                _json_arguments(p)
    group = sub.add_parser("lote", help="Edición masiva de entidades en una transacción.")
    p = group.add_subparsers(dest="action", required=True).add_parser("aplicar", help="Aplicar operaciones; cualquier error revierte todo.")
    _json_arguments(p)
    p.add_argument("--atomico", action="store_true", help="Siempre activo; se acepta por compatibilidad.")
    p.set_defaults(handler=batch_handler)
    group = sub.add_parser("importar", help="Importar Excel, volcado JSON o formatos asistidos.")
    actions = group.add_subparsers(dest="kind", required=True)
    __import__(__package__ + '._formatos', fromlist=['register']).register(sub, actions)
    for kind in ("excel", "json", "inventario", "detectar", "asistido"):
        p = actions.add_parser(kind, help=f"Importar {kind}." if kind != "detectar" else "Detectar formato sin modificar la base.")
        p.add_argument("source", metavar="ARCHIVO", help="Fuente de importación; se informa su SHA-256.")
        p.set_defaults(handler=import_handler)
        if kind == "excel":
            dest = p.add_mutually_exclusive_group()
            dest.add_argument("--nuevo-proyecto", action="store_true", help="Crear un proyecto (predeterminado).")
            dest.add_argument("--en-proyecto", action="store_true", help="Importar en el proyecto de -p.")
            p.add_argument("--modo", choices=["proyecto", "mantenimiento"], help="Modo del libro nuevo; por defecto autodetectado.")
            p.add_argument("--mode", choices=["merge", "replace"], default="merge", help="Política para --en-proyecto.")
        elif kind != "detectar":
            p.add_argument("--perfil", help="Archivo JSON con Perfil del importador asistido.")
            s = p.add_mutually_exclusive_group()
            s.add_argument("--supuestos", help="Objeto JSON de supuestos.")
            s.add_argument("--supuestos-archivo", help="Archivo JSON de supuestos.")
            p.add_argument("--nombre", help="Nombre del nuevo proyecto asistido.")
            p.add_argument("--previsualizar", action="store_true", help="Devolver borrador sin crear el proyecto.")
            for flag in ("aceptar-supuestos", "aceptar-advertencias", "aceptar-revision-inventario"):
                p.add_argument("--" + flag, action="store_true", help="Aceptación explícita equivalente a la API.")
    group = sub.add_parser("exportar", help="Exportar el proyecto sin modificar la base.")
    actions = group.add_subparsers(dest="kind", required=True)
    for kind in ("xlsx", "gantt", "json"):
        p = actions.add_parser(kind, help=f"Exportar {kind}." + (" Volcado ganttier.project v1 reimportable." if kind == "json" else ""))
        p.add_argument("--a", required=True, help="Ruta del archivo de destino.")
        p.set_defaults(handler=export_handler)
        if kind == "xlsx":
            p.add_argument("--nominal", action="store_true", help="Exportar asignaciones nominales.")
        elif kind == "gantt":
            p.add_argument("--modo", "--mode", default="nivel", choices=["nivel", "trabajador"])
            p.add_argument("--agrupar", "--group-by", "--group_by", default="wbs")
            p.add_argument("--escala", "--scale", default="week", choices=["day", "week", "month"])
            p.add_argument("--desde", "--from")
            p.add_argument("--hasta", "--to")
            p.add_argument("--critica", "--critical", nargs="?", const="true", default="false", choices=["true", "false"])
    group = sub.add_parser("plantilla", help="Crear una plantilla oficial de ingreso.")
    group.add_argument("kind", choices=["proyecto", "mantenimiento", "inventario"])
    group.add_argument("--a", required=True, help="Ruta del archivo Excel de destino.")
    group.set_defaults(handler=template_handler)
    group = sub.add_parser("demo", help="Crear ejemplos oficiales.")
    p = group.add_subparsers(dest="action", required=True).add_parser("crear", help="Crear una demostración en modo seguro.")
    p.add_argument("kind", choices=["proyecto", "mantenimiento"])
    p.add_argument("--n", type=int, default=10000, help="Número de actividades de mantenimiento (1..100000).")
    p.set_defaults(handler=demo_handler)
