"""Entrada liviana, descubrimiento y protocolo JSON de la CLI."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib
import json
import logging
import os
from pathlib import Path
import pkgutil
import sys
import time
import traceback
from datetime import date, datetime

from ._base import CLI_VERSION, CliError, Context, Result, env_flag

GANTTIER_VERSION = "1.0.0"


class ArgumentParser(argparse.ArgumentParser):
    """Convierte errores de sintaxis en errores del protocolo JSON."""

    def error(self, message: str) -> None:
        raise CliError("uso", message, hint="Consulte describir para conocer los argumentos.")


def _globals(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", help="Ruta de la base SQLite.")
    parser.add_argument("--data-dir", help="Carpeta de datos y resultados.")
    parser.add_argument("-p", "--proyecto", help="Id o nombre exacto del proyecto.")
    parser.add_argument("--escribir", action="store_true", help="Autoriza cambios en la base real.")
    parser.add_argument("--forzar-bloqueo", action="store_true", help="Ignora el bloqueo de otro escritor.")
    parser.add_argument("--sin-respaldo", action="store_true", help="Omite el respaldo previo.")
    parser.add_argument("--salida", help="Guarda también el sobre JSON en un archivo.")
    parser.add_argument("--compacto", action="store_true", help="Emite JSON en una línea.")
    parser.add_argument("--verbose", action="store_true", help="Habilita registros detallados en stderr.")
    parser.add_argument("--version", action="store_true", help="Versiones de CLI y Ganttier.")
    parser.add_argument("-h", "--help", action="store_true", help="Ayuda en formato JSON.")


def discover_commands(sub: argparse._SubParsersAction, warnings: list[str]) -> None:
    """Registra los módulos disponibles y aísla fallos de módulos opcionales."""
    from . import comandos
    for item in sorted(pkgutil.iter_modules(comandos.__path__), key=lambda m: m.name):
        if item.name.startswith("_"):
            continue
        previous = set(sub.choices)
        previous_help = list(sub._choices_actions)
        try:
            module = importlib.import_module(f"{comandos.__name__}.{item.name}")
            register = getattr(module, "register", None)
            if callable(register):
                register(sub)
        except Exception as exc:
            for name in set(sub.choices) - previous:
                del sub.choices[name]
            sub._choices_actions[:] = previous_help
            message = f"No se pudo cargar el módulo {item.name}: {exc}"
            warnings.append(message)
            print(message, file=sys.stderr)


def build_parser(warnings: list[str] | None = None) -> ArgumentParser:
    """Construye el catálogo a partir de los registros de cada agente."""
    parser = ArgumentParser(prog="ganttier-cli", description="CLI de Ganttier para agentes.", add_help=False)
    _globals(parser)
    sub = parser.add_subparsers(dest="group")
    discover_commands(sub, warnings if warnings is not None else [])
    return parser


def _bootstrap(argv: list[str]) -> argparse.Namespace:
    # Sólo globales y sólo antes del grupo; no importa ningún módulo de negocio.
    parser = ArgumentParser(add_help=False, allow_abbrev=False)
    _globals(parser)
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    # Los desconocidos los rechaza el parser completo, conservando --salida
    # y las rutas resueltas también en los sobres de errores de uso.
    return parser.parse_known_args(argv)[0]


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[3]
    db = Path(args.db or os.environ.get("GANTTIER_DB") or root / "datos" / "ganttier.db").expanduser().resolve()
    data = Path(args.data_dir or os.environ.get("GANTTIER_DATA_DIR") or
                (db.parent if args.db else root / "datos")).expanduser().resolve()
    os.environ["GANTTIER_DB"] = str(db)
    os.environ["GANTTIER_DATA_DIR"] = str(data)
    return db, data


def _json_default(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, (Path, date, datetime)):
        return str(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(f"Tipo no serializable: {type(value).__name__}")


def _translate(exc: BaseException, ctx: Context) -> CliError:
    if isinstance(exc, CliError):
        return exc
    # No cargar espacio ni pydantic por el camino feliz: reconocer sólo al fallar.
    if any(c.__name__ == "BaseOcupada" and c.__module__ == "ganttier.optimizacion.espacio"
           for c in type(exc).__mro__):
        info = getattr(exc, "info", None)
        locked = getattr(info, "por", None)
        if locked is None:
            try:
                from ..optimizacion.espacio import estado_bloqueo
                lock = estado_bloqueo(ctx.db_path)
                locked = lock.por if lock.ocupado else None
            except Exception:
                pass
        ctx.extras["locked_by"] = locked
        if ctx.mutation_info:
            ctx.mutation_info.simulated = not ctx.write
            ctx.mutation_info.locked_by = locked
        return CliError("base_ocupada", str(exc), detail={"locked_by": locked}, exit_code=3)
    code, exit_code = getattr(exc, "code", None), getattr(exc, "exit_code", None)
    if (type(exc).__module__.startswith("ganttier.optimizacion") and isinstance(code, str)
            and isinstance(exit_code, int)):  # EscenarioCambio, ResultadoInfactible, VerificacionFallida
        return CliError(code, str(exc), detail=getattr(exc, "detail", None), exit_code=exit_code)
    if isinstance(exc, (TimeoutError, KeyboardInterrupt)):
        return CliError("timeout", str(exc) or "Operación cancelada.", exit_code=4)
    if isinstance(exc, ImportError):
        return CliError("dependencia_faltante", str(exc), hint="Instale las dependencias de Ganttier.", exit_code=6)
    if isinstance(exc, ValueError) or type(exc).__name__ == "ValidationError":
        detail = {}
        if callable(getattr(exc, "errors", None)):
            detail["errors"] = [{"row": next((x for x in e.get("loc", ()) if isinstance(x, int)), None),
                                 "field": ".".join(map(str, e.get("loc", ()))),
                                 "message": e.get("msg", "Valor inválido.")}
                                for e in exc.errors()]
        return CliError("datos_invalidos", str(exc), detail=detail)
    if isinstance(exc, LookupError):
        return CliError("no_encontrado", str(exc))
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    return CliError("interno", str(exc) or type(exc).__name__, exit_code=5)


def _command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    parts = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            name = getattr(args, action.dest, None)
            if name in action.choices:
                parts.append(name)
                tail = _command(action.choices[name], args)
                if tail:
                    parts.append(tail)
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Ejecuta una invocación y emite exactamente un sobre JSON UTF-8."""
    started = time.perf_counter()
    real_stdout = sys.stdout
    for stream in (real_stdout, sys.stderr):
        if os.name == "nt" and callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    argv = list(sys.argv[1:] if argv is None else argv)
    args = argparse.Namespace(db=None, data_dir=None, salida=None, compacto=False)
    root = Path(__file__).resolve().parents[3]
    ctx = Context(root / "datos" / "ganttier.db", root / "datos")
    command, schema, emitted = "", None, False
    result, error = Result(), None

    def emit(outcome: Result, failure: CliError | None = None) -> None:
        nonlocal emitted
        if emitted:
            return
        mutation = ctx.mutation_info
        envelope = {"ok": failure is None, "command": command, "cli_version": CLI_VERSION,
                    "ganttier": {"version": GANTTIER_VERSION, "schema": schema},
                    "db": {"path": str(ctx.db_path), "data_dir": str(ctx.data_dir),
                           "mode": ("simulated" if mutation.simulated else "write") if mutation else "read",
                           "simulated": bool(mutation and mutation.simulated),
                           "backup": mutation.backup if mutation else None,
                           "locked_by": (mutation.locked_by if mutation else None) or ctx.extras.get("locked_by")},
                    "data": outcome.data, "warnings": list(dict.fromkeys(ctx.warnings + outcome.warnings)),
                    "error": ({"code": failure.code, "message": failure.message,
                               "detail": failure.detail or {}, "hint": failure.hint} if failure else None),
                    "elapsed_s": round(time.perf_counter() - started, 6)}
        indent = None if args.compacto or not real_stdout.isatty() else 2
        text = json.dumps(envelope, ensure_ascii=False, indent=indent, default=_json_default, allow_nan=False) + "\n"
        if args.salida:
            target = Path(args.salida).expanduser().resolve()
            if target == ctx.db_path:
                raise CliError("uso", "--salida no puede sobrescribir la base de datos.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        real_stdout.write(text)
        real_stdout.flush()
        emitted = True

    try:
        with contextlib.redirect_stdout(sys.stderr):
            args = _bootstrap(argv)
            db_path, data_dir = _paths(args)
            ctx = Context(db_path, data_dir, project=args.proyecto,
                          write=args.escribir or env_flag("GANTTIER_CLI_ESCRITURA"),
                          force_lock=args.forzar_bloqueo, no_backup=args.sin_respaldo, verbose=args.verbose)
            from ..migrations import LATEST
            schema = LATEST
            if args.verbose:
                logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
            if args.version:
                command = "--version"
                result = Result({"cli_version": CLI_VERSION, "ganttier_version": GANTTIER_VERSION})
            else:
                parser = build_parser(ctx.warnings)
                args = parser.parse_args(argv)
                command = _command(parser, args)
                ctx.extras.update(parser=parser, emit=emit)
                if args.help:
                    result = Result({"help": parser.format_help()})
                else:
                    handler = getattr(args, "handler", None)
                    if handler is None:
                        raise CliError("uso", "Falta indicar un comando.", hint="Use describir.")
                    if ctx.force_lock:
                        ctx.warn("Se autorizó ignorar el bloqueo de otro escritor.")
                    result = handler(args, ctx)
                    if not isinstance(result, Result):
                        result = Result(result)
    except (Exception, KeyboardInterrupt) as exc:
        error = _translate(exc, ctx)
    except SystemExit as exc:
        # argparse --help de subcomandos escribe en stderr, nunca rompe el sobre.
        result = Result({"help": "Consulte describir para la ayuda estructurada."})
        if exc.code:
            error = CliError("uso", "Argumentos inválidos.")
    if emitted:
        if error:
            print(f"{error.code}: {error.message}", file=sys.stderr)
        return error.exit_code if error else result.exit_code
    try:
        emit(result, error)
    except (Exception, KeyboardInterrupt) as exc:
        error = _translate(exc, ctx)
        args.salida = None
        emit(Result(), error)
    return error.exit_code if error else result.exit_code
