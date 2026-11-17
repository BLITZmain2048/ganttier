"""Informes CTL: renderizado compartido y manifiestos de archivos generados."""
from __future__ import annotations

import argparse
import hashlib
import shutil
from contextlib import closing, nullcontext
from pathlib import Path

from ... import autoreports, services
from ...api import tools as api_tools
from .._base import CliError, Context, Result
from ._control_progress import read_body


def file_metadata(path: Path, simulated: bool) -> dict:
    """Calcula tamaño y SHA-256 antes de retirar una copia simulada."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "sha256": digest.hexdigest(),
            "simulated": simulated, "ephemeral": simulated}


def _manifest(files, simulated, directory=None):
    for entry in files:
        path = services.DATA_DIR / "informes" / entry["filename"]
        if directory is not None and path.resolve().parent != directory:
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / path.name
            shutil.move(str(path), str(destination))
            path = destination
        entry.update(file_metadata(path, simulated))
    return files


def execute(args: argparse.Namespace, ctx: Context) -> Result:
    """Genera informes manuales o automáticos dentro de la mutación segura."""
    operation = args._ctl
    if operation == "informes/automaticos/ver":
        with closing(ctx.conn()) as conn:
            return Result(data=autoreports.get_config(conn, ctx.require_project(conn)))
    from ...optimizacion.espacio import redirigir_datos
    warnings = []
    with ctx.mutation(operation) as conn:
        pid = ctx.require_project(conn)
        simulated = ctx.mutation_info.simulated
        # Context ya redirige la simulación; en escritura también se respeta
        # data_dir de un Context creado en proceso tras importar services.
        with (nullcontext() if simulated else redirigir_datos(ctx.data_dir)):
            requested = getattr(args, "a", None)
            if simulated:
                warnings.append("Simulación: los archivos se generan y verifican en la copia temporal; se retiran al terminar.")
            try:
                if operation == "informes/generar":
                    formats = [fmt.strip().lower() for fmt in args.formatos.split(",")]
                    data = services.render_reports(conn, pid, {"semanal": "weekly", "mensual": "monthly", "anual": "annual"}[args.tipo], args.inicio, formats)
                    directory = Path(requested).expanduser().resolve() if requested and not simulated else None
                    data = _manifest(data, simulated, directory)
                elif operation == "informes/plantel-semanal":
                    target = Path(requested).expanduser().resolve()
                    if target.suffix.lower() != ".xlsx":
                        raise ValueError("El archivo de plantel debe tener extensión .xlsx.")
                    if simulated:
                        target = services.DATA_DIR / "informes" / target.name
                    response = api_tools.weekly_roster(pid, args.inicio, conn)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(response.body)
                    data = {"format": "xlsx", "filename": target.name, **file_metadata(target, simulated)}
                elif operation == "informes/automaticos/configurar":
                    data = autoreports.set_config(conn, pid, read_body(args))
                elif operation == "informes/automaticos/ejecutar":
                    data = autoreports.run_project_reports(conn, pid)
                    for entry in data:
                        _manifest(entry["files"], simulated)
                else:
                    raise ValueError("Operación de informes desconocida.")
            except (ImportError, RuntimeError) as exc:
                cause, seen = exc, set()
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause))
                    if isinstance(cause, (ImportError, services.ModuleUnavailable)):
                        raise CliError("dependencia_faltante", f"Falta una dependencia para generar informes: {cause}", exit_code=6) from exc
                    cause = cause.__cause__ or cause.__context__
                raise
            return Result(data=data, warnings=warnings)
