"""Catálogo, estado, copias y acceso a la API sin interfaz gráfica."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import types
import typing

from .._base import CliError, Context, Result


def register(sub: argparse._SubParsersAction) -> None:
    """Registra los comandos del núcleo; no carga FastAPI ni motores."""
    p = sub.add_parser("describir", help="Catálogo de comandos y esquemas JSON.")
    p.add_argument("--comando", help="Filtra por grupo o por grupo y acción.")
    p.set_defaults(handler=describir)
    p = sub.add_parser("estado", help="Estado de la base, proyectos y capacidades.")
    p.set_defaults(handler=estado)
    p = sub.add_parser("base", help="Copias, respaldos e integridad de la base.")
    actions = p.add_subparsers(dest="action", required=True)
    p = actions.add_parser("copiar", help="Copia SQLite consistente a un archivo nuevo.")
    p.add_argument("--destino", required=True, help="Archivo de destino; no debe existir.")
    p.set_defaults(handler=base_copiar)
    p = actions.add_parser("respaldar", help="Crea un respaldo verificado.")
    p.add_argument("--motivo", default="manual-cli", help="Motivo del respaldo.")
    p.set_defaults(handler=base_respaldar)
    actions.add_parser("respaldos", help="Lista los respaldos disponibles.").set_defaults(handler=base_respaldos)
    actions.add_parser("verificar", help="Verifica SQLite y los respaldos.").set_defaults(handler=base_verificar)
    p = sub.add_parser("api", help="Invoca una ruta HTTP en proceso, con aislamiento de escritura.")
    p.add_argument("metodo", type=str.upper, choices=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"])
    p.add_argument("ruta", help="Ruta local, por ejemplo /api/projects.")
    body = p.add_mutually_exclusive_group()
    body.add_argument("--json", help="Cuerpo JSON.")
    body.add_argument("--archivo", help="Archivo JSON, o - para stdin.")
    p.add_argument("--consulta", nargs="+", action="extend", default=[], help="Parámetros k=v; admite claves repetidas.")
    p.add_argument("--binario-a", help="Destino de una respuesta binaria.")
    p.set_defaults(handler=api)
    p = sub.add_parser("servir", help="Sirve la API local hasta Ctrl+C; simula salvo --escribir.")
    p.add_argument("--puerto", type=int, default=0, help="Puerto preferido (0: libre asignado por el sistema).")
    p.set_defaults(handler=servir)


def _argument(action: argparse.Action) -> dict:
    converter = action.type
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        name = "bool"
    else:
        name = getattr(converter, "__name__", "str") if converter else "str"
    default = action.default if action.default != argparse.SUPPRESS else None
    return {"name": action.dest, "flags": action.option_strings, "help": action.help,
            "type": name, "default": default, "required": action.required,
            "choices": list(action.choices) if action.choices is not None else None, "nargs": action.nargs}


def _catalog(parser: argparse.ArgumentParser, parts: tuple[str, ...] = (), help_text: str = "") -> list[dict]:
    args = [_argument(a) for a in parser._actions if not isinstance(a, argparse._SubParsersAction)]
    entries = [{"command": " ".join(parts), "group": parts[0] if parts else None,
                "action": " ".join(parts[1:]) or None, "help": help_text or parser.description,
                "arguments": args, "examples": [f"ganttier-cli {' '.join(parts)} --help".strip()]}]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {a.dest: a.help for a in action._choices_actions}
            for name, child in action.choices.items():
                # Los alias comparten parser, pero argparse sólo registra la ayuda
                # del nombre canónico en _choices_actions.
                child_help = helps.get(name) or next(
                    (help_ for canonical, help_ in helps.items() if action.choices.get(canonical) is child), "")
                entries.extend(_catalog(child, (*parts, name), child_help))
    return entries


def _type_schema(annotation) -> dict:
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Literal:
        return {"enum": list(args), "type": "string" if all(isinstance(x, str) for x in args) else "number"}
    if origin in (typing.Union, types.UnionType):
        return {"anyOf": [_type_schema(x) for x in args]}
    return {"type": {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null"}.get(annotation, "object")}


def _dataclass_schema(cls) -> dict:
    hints, properties, required = typing.get_type_hints(cls), {}, []
    for field in dataclasses.fields(cls):
        definition = _type_schema(hints[field.name])
        if field.default is not dataclasses.MISSING:
            definition["default"] = field.default
        elif field.default_factory is not dataclasses.MISSING:
            definition["default"] = field.default_factory()
        else:
            required.append(field.name)
        properties[field.name] = definition
    return {"title": cls.__name__, "type": "object", "properties": properties,
            "required": required, "additionalProperties": False}


def describir(args: argparse.Namespace, ctx: Context) -> Result:
    """Deriva el catálogo del parser real y los esquemas de sus clases originales."""
    from ...domain import ObjectiveWeights, OptimizeOptions
    commands = _catalog(ctx.extras["parser"])
    globals_ = commands.pop(0)["arguments"]
    if args.comando:
        query = " ".join(args.comando.split())
        commands = [c for c in commands if c["command"] == query or c["command"].startswith(query + " ")]
        if not commands:
            raise LookupError(f"No existe el comando {query}.")
    schemas = {"weights": _dataclass_schema(ObjectiveWeights), "options": _dataclass_schema(OptimizeOptions)}
    try:
        variantes = importlib.import_module("ganttier.optimizacion.variantes")
        for key, function in (("variant", "esquema_variante"), ("plan", "esquema_plan")):
            generator = getattr(variantes, function, None)
            if callable(generator):
                schemas[key] = generator()
            else:
                ctx.warn(f"Esquema opcional no disponible: {function}.")
    except ImportError as exc:
        ctx.warn(f"Esquemas de variante y plan no disponibles: {exc}.")
    return Result({"commands": commands, "global_arguments": globals_, "schemas": schemas})


def estado(args: argparse.Namespace, ctx: Context) -> Result:
    """Consulta metadatos con SQLite de sólo lectura, sin cargar el motor."""
    from ..core import CLI_VERSION, GANTTIER_VERSION
    from ...optimizacion.espacio import estado_bloqueo
    lock = estado_bloqueo(ctx.db_path)
    ctx.extras["locked_by"] = lock.por if lock.ocupado else None
    with contextlib.closing(ctx.conn()) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        projects = [dict(r) for r in conn.execute("""SELECT p.id, p.name, p.mode,
                    (SELECT COUNT(*) FROM activities a WHERE a.project_id=p.id) AS activities
                    FROM projects p ORDER BY p.id""")]
    return Result({"version": GANTTIER_VERSION, "cli_version": CLI_VERSION,
                   "schema": int(row[0]) if row else None, "path": str(ctx.db_path),
                   "size_bytes": ctx.db_path.stat().st_size, "locked_by": ctx.extras["locked_by"],
                   "ortools": importlib.util.find_spec("ortools") is not None,
                   "cpu_count": os.cpu_count(), "python": sys.version.split()[0], "projects": projects})


def base_copiar(args: argparse.Namespace, ctx: Context) -> Result:
    """Crea una copia consistente y verifica integridad sin sobrescribir archivos."""
    from ...backups import verify_file
    destination = Path(args.destino).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(ctx.conn()) as source:
        try:
            destination.open("xb").close()
        except FileExistsError:
            raise CliError("datos_invalidos", f"El destino ya existe: {destination}") from None
        try:
            with contextlib.closing(sqlite3.connect(destination)) as target:
                source.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
            valid, message = verify_file(destination)
            if not valid:
                raise ValueError(f"La copia no pasó la verificación: {message}")
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return Result({"path": str(destination), "verified": True, "size_bytes": destination.stat().st_size})


def base_respaldar(args: argparse.Namespace, ctx: Context) -> Result:
    """Genera un respaldo mediante el proveedor compartido de espacios."""
    from ...optimizacion.espacio import respaldar
    with contextlib.closing(ctx.conn()):
        path = respaldar(ctx.db_path, ctx.data_dir, args.motivo)
    return Result({"path": str(path), "verified": True})


def base_respaldos(args: argparse.Namespace, ctx: Context) -> Result:
    """Lista los manifiestos existentes en el directorio de datos resuelto."""
    from ...backups import backup_dir, list_backups
    directory = backup_dir(ctx.db_path)
    items = [dataclasses.asdict(info) for info in list_backups(directory)]
    return Result({"directory": str(directory), "items": items, "total": len(items)})


def base_verificar(args: argparse.Namespace, ctx: Context) -> Result:
    """Comprueba integridad SQLite, referencias y manifiestos de respaldos."""
    from ... import backups
    with contextlib.closing(ctx.conn()):
        valid, message = backups.verify_file(ctx.db_path)
    checks = []
    directory = backups.backup_dir(ctx.db_path)
    for path in sorted(directory.glob("*.db")):
        try:
            backups._validate_restore(path)
            checks.append({"path": str(path), "verified": True, "message": "ok"})
        except ValueError as exc:
            checks.append({"path": str(path), "verified": False, "message": str(exc)})
    ok = valid and all(c["verified"] for c in checks)
    return Result({"verified": ok, "database": {"verified": valid, "message": message}, "backups": checks}, exit_code=0 if ok else 2)


@contextlib.contextmanager
def _api_environment(conn: sqlite3.Connection, ctx: Context):
    from ...optimizacion.espacio import redirigir_datos
    target = Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
    data = Path(os.environ.get("GANTTIER_DATA_DIR", str(ctx.data_dir)))
    old_db = os.environ.get("GANTTIER_DB")
    os.environ["GANTTIER_DB"] = str(target)
    try:
        with redirigir_datos(data):
            yield target
    finally:
        if old_db is None:
            os.environ.pop("GANTTIER_DB", None)
        else:
            os.environ["GANTTIER_DB"] = old_db


def _application(token: str, connection):
    from ...main import create_app
    from ...api.dependencies import connection as original
    application = create_app(token=token)
    application.dependency_overrides[original] = connection
    return application


def _http_error(status: int, body) -> CliError:
    code, exit_code = ("no_encontrado", 1) if status == 404 else ("datos_invalidos", 1)
    if status >= 500:
        code, exit_code = "interno", 5
    if status == 503 and any(term in str(body).lower() for term in ("dependencia", "módulo", "module")):
        code, exit_code = "dependencia_faltante", 6
    if status in (408, 504):
        code, exit_code = "timeout", 4
    return CliError(code, f"La API respondió HTTP {status}.", detail={"status_code": status, "body": body}, exit_code=exit_code)


def api(args: argparse.Namespace, ctx: Context) -> Result:
    """Invoca FastAPI en proceso sin activar el lifespan de la aplicación GUI."""
    if not args.ruta.startswith("/") or args.ruta.startswith("//"):
        raise CliError("uso", "RUTA debe ser local y comenzar por /.")
    payload = None
    if args.json is not None:
        payload = json.loads(args.json)
    elif args.archivo:
        payload = json.loads(sys.stdin.read() if args.archivo == "-" else Path(args.archivo).read_text(encoding="utf-8-sig"))
    query = []
    for item in args.consulta:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise CliError("uso", "Cada --consulta debe tener la forma clave=valor.")
        query.append((key, value))
    binary = Path(args.binario_a).expanduser().resolve() if args.binario_a else None
    if binary == ctx.db_path:
        raise CliError("uso", "--binario-a no puede sobrescribir la base de datos.")
    manager = contextlib.closing(ctx.conn()) if args.metodo == "GET" else ctx.mutation(f"api {args.metodo} {args.ruta}")
    with manager as conn, _api_environment(conn, ctx):
        from fastapi.testclient import TestClient
        token = secrets.token_urlsafe(32)

        def connection():
            yield conn

        application = _application(token, connection)
        # TestClient sólo ejecuta lifespan al usarlo como context manager.
        client = TestClient(application, base_url="http://127.0.0.1", headers={"X-Ganttier-Token": token})
        try:
            response = client.request(args.metodo, args.ruta, json=payload, params=query or None, follow_redirects=False)
            content_type = response.headers.get("content-type", "")
            if "json" in content_type and response.content:
                body = response.json()
            elif not response.content:
                body = None
            elif content_type.startswith("text/"):
                body = response.text
            else:
                body = {"bytes": len(response.content), "content_type": content_type}
                if binary is None:
                    raise CliError("uso", "La respuesta es binaria; indique --binario-a ARCHIVO.")
            if response.status_code >= 400:
                raise _http_error(response.status_code, body)
            if binary:
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(response.content)
            return Result({"status_code": response.status_code, "body": body,
                           "content_type": content_type, "binary_file": str(binary) if binary else None})
        finally:
            client.close()


def servir(args: argparse.Namespace, ctx: Context) -> Result:
    """Reserva un puerto, anuncia una sola vez y sirve hasta la interrupción."""
    if not 0 <= args.puerto <= 65535:
        raise ValueError("El puerto debe estar entre 0 y 65535.")
    import uvicorn
    from ... import db
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", args.puerto))
        except OSError:
            if not args.puerto:
                raise
            listener.bind(("127.0.0.1", 0))
        listener.listen(2048)
        port = listener.getsockname()[1]
        token = secrets.token_urlsafe(32)
        with ctx.mutation("servir API") as conn, _api_environment(conn, ctx) as target:
            def connection():
                with contextlib.closing(db.connect(target)) as request_conn:
                    yield request_conn

            application = _application(token, connection)

            class Server(uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    if self.started:
                        ctx.extras["emit"](Result({"url": f"http://127.0.0.1:{port}", "token": token}))

            config = uvicorn.Config(application, host="127.0.0.1", port=port, lifespan="off",
                                    access_log=False, log_level="info" if ctx.verbose else "warning")
            server = Server(config)
            server.run(sockets=[listener])
            return Result(exit_code=4 if server.should_exit else 0)
    finally:
        listener.close()
