"""Programación y optimización segura; registro liviano sin importar el motor."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

from .._base import CliError, Context, Result


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Debe ser un número positivo y finito.")
    return number


def _nonnegative(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("Debe ser un número no negativo y finito.")
    return number


def _integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Debe ser un entero positivo.")
    return number


def _zero_integer(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Debe ser un entero no negativo.")
    return number


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError(value)
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use una fecha válida YYYY-MM-DD.") from exc


def _dates(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--desde", type=_date, help="Inicio del horizonte YYYY-MM-DD.")
    parser.add_argument("--hasta", type=_date, help="Fin del horizonte YYYY-MM-DD.")


def _variant_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", type=Path, help="Plan JSON de variantes.")
    parser.add_argument("--objetivo", help="plazo, nivelar, ventanas, costo o equilibrado.")
    parser.add_argument("--pesos", help="Objeto JSON de pesos explícitos; prevalece sobre objetivo.")
    parser.add_argument("--metodo", choices=("auto", "heuristic", "cpsat"))
    parser.add_argument("--tiempo", type=_positive, help="Presupuesto del motor por variante, en segundos.")
    parser.add_argument("--semilla", type=_zero_integer)
    parser.add_argument("--modo-roles", choices=("fractional", "indivisible"))
    _dates(parser)
    parser.add_argument("--regenerar", action="store_true", default=None)
    parser.add_argument("--sin-horas-extra", action="store_true", default=None)
    parser.add_argument("--opciones", help="Objeto JSON de OptimizeOptions; los flags explícitos prevalecen.")
    parser.add_argument("--aplicar", action="store_true", help="Aplica la mejor factible; requiere --escribir.")


def register(sub: argparse._SubParsersAction) -> None:
    """Registra el grupo programa y sus handlers para el descubrimiento de la CLI."""
    group = sub.add_parser("programa", help="Verificar, programar y comparar alternativas aisladas.")
    actions = group.add_subparsers(dest="programa_action", required=True)
    check = actions.add_parser("verificar", help="Audita la red y las fechas vigentes sin escribir.")
    check.add_argument("--fechas", choices=("actuales",), default="actuales")
    check.add_argument("--max-violaciones", type=_zero_integer, default=100)
    check.set_defaults(handler=verificar)
    actions.add_parser("cpm", help="Calcula CPM; simula salvo --escribir.").set_defaults(handler=cpm)
    diagnostic = actions.add_parser("diagnosticar", help="Analiza demanda, capacidad y prechequeos.")
    _dates(diagnostic)
    diagnostic.add_argument("--periodo", choices=("dia", "semana", "mes"), default="mes")
    diagnostic.set_defaults(handler=diagnosticar)
    load = actions.add_parser("carga", help="Perfil de HH, totales y pico por período.")
    _dates(load)
    load.add_argument("--agrupar", choices=("recurso", "habilidad"), default="recurso")
    load.add_argument("--periodo", choices=("dia", "semana", "mes"), default="dia")
    load.set_defaults(handler=carga)
    solve = actions.add_parser("resolver", help="Resuelve una variante en un proceso aislado.")
    _variant_flags(solve)
    solve.add_argument("--mejor-a", type=Path, help="JSON aplicable de la solución.")
    solve.add_argument("--conservar-copias", action="store_true")
    solve.set_defaults(handler=resolver)
    for name, handler in (("comparar", comparar), ("lote", lote)):
        parser = actions.add_parser(name, help="Ejecuta variantes, conserva JSONL y clasifica resultados.")
        _variant_flags(parser)
        parser.add_argument("--objetivos", help="Objetivos separados por comas; producto con semillas.")
        parser.add_argument("--semillas", help="Semillas separadas por comas; producto con objetivos.")
        parser.add_argument("--paralelo", type=_integer, help="Procesos; def. GANTTIER_CLI_PARALELO o CPU/2 (máx. 4).")
        parser.add_argument("--ranking-por", help="lexicographic, lista de métricas o score:pesos.")
        parser.add_argument("--mejor-a", type=Path)
        parser.add_argument("--resultados-a", type=Path, help="Archivo JSONL incremental.")
        parser.add_argument("--top", type=_zero_integer, default=5)
        parser.add_argument("--conservar-top", type=_zero_integer, default=3, help="Corridas factibles conservadas en disco.")
        parser.add_argument("--completo", action="store_true", help="Incluye toda la tabla sin payloads en stdout.")
        parser.add_argument("--presupuesto", type=_positive, help="Presupuesto global en segundos.")
        parser.add_argument("--reanudar", action="store_true", help="Requiere --resultados-a existente.")
        parser.add_argument("--parar-si-mejora-menos-de", type=_nonnegative,
                            help="Detiene al mejorar menos de esta fracción relativa entre resultados factibles.")
        parser.set_defaults(handler=handler)
    apply = actions.add_parser("aplicar", help="Aplica un resultado conservado; sin --escribir simula.")
    apply.add_argument("--resultado", required=True, type=Path)
    apply.add_argument("--variante", help="Identificador exacto en JSON/JSONL.")
    apply.add_argument("--tolerancia", type=_nonnegative, default=0.0)
    apply.set_defaults(handler=aplicar)
    history = actions.add_parser("corridas", help="Historial persistido del proyecto.")
    history_actions = history.add_subparsers(dest="corridas_action", required=True)
    listing = history_actions.add_parser("listar", help="Lista corridas con métricas y estado de aplicación.")
    listing.add_argument("--limite", type=_integer, default=100)
    listing.add_argument("--desde", type=_zero_integer, default=0)
    listing.set_defaults(handler=corridas)
    for name in ("ver", "aplicar"):
        parser = history_actions.add_parser(name, help="Consulta o aplica una corrida del proyecto.")
        parser.add_argument("id", type=_integer)
        parser.set_defaults(handler=corridas)


def _check_dates(args: Any) -> None:
    if args.desde and args.hasta and args.hasta < args.desde:
        raise CliError("datos_invalidos", "El fin del horizonte es anterior al inicio.")


def _verification(conn: Any, pid: int) -> dict:
    from ... import domain, services, role_schedules
    from ...engine.cpm import validate_network
    from ...engine.verify import verify_with_options

    problem = services.build_problem(conn, pid)
    network = validate_network(problem)
    starts = {a.id: a.actual_start or a.planned_start for a in problem.activities}
    finishes = {a.id: a.actual_finish or a.planned_finish for a in problem.activities}
    # Conserva la política explícita de la última corrida aplicada que aún coincide.
    options = domain.OptimizeOptions()
    for row in conn.execute("SELECT id, options FROM schedule_runs WHERE project_id=? AND applied=1 ORDER BY id DESC", (pid,)):
        current = conn.execute("SELECT value FROM meta WHERE key=?", (f"run_applied_hash:{row['id']}",)).fetchone()
        if current and current[0] == services._scenario_fingerprint(conn, pid):
            options = domain.OptimizeOptions(**json.loads(row["options"] or "{}"))
            break
    state = role_schedules.allocation_state(conn, pid)
    if state["applied"] and state["valid"]:
        stored = conn.execute("SELECT options FROM role_schedules WHERE project_id=?", (pid,)).fetchone()
        report = role_schedules.audit_payload(conn, pid, dict(feasible=True,
            options=json.loads(stored[0]), time_axis=state["time_axis"],
            starts={str(k): v.isoformat() for k, v in starts.items() if v},
            finishes={str(k): v.isoformat() for k, v in finishes.items() if v},
            role_assignment=state["role_assignment"]))
    else:
        report = verify_with_options(problem, starts, finishes, options)
    violations = [dict(type="network", activities=[], date=None, detail=e) for e in network]
    violations.extend(report.violations)
    if state["applied"] and not state["valid"]:
        violations.append(dict(type="role_schedule_stale", activities=[], date=None,
                               detail="La asignación de roles guardada quedó obsoleta."))
    return dict(feasible=not violations, violations=violations, warnings=report.warnings,
                stats=report.stats, network_errors=network)


def verificar(args: Any, ctx: Context) -> Result:
    """Verifica las fechas vigentes con red y auditor independiente; acota el detalle."""
    with closing(ctx.conn()) as conn:
        result = _verification(conn, ctx.require_project(conn))
    result["violation_count"] = len(result["violations"])
    result["network_error_count"] = len(result["network_errors"])
    result["network_errors"] = result["network_errors"][:args.max_violaciones]
    result["violations"] = result["violations"][:args.max_violaciones]
    result["truncated"] = result["violation_count"] > len(result["violations"])
    return Result(result, exit_code=0 if result["feasible"] else 2)


def cpm(args: Any, ctx: Context) -> Result:
    """Calcula CPM en la conexión segura y devuelve fechas y holguras sin el payload."""
    from ... import services, repo
    from ...optimizacion.espacio import redirigir_datos

    with ctx.mutation("programa cpm") as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        # mutation ya redirige la simulación; en escritura la ruta explícita también manda.
        directory = ctx.data_dir if ctx.write else services.DATA_DIR
        with redirigir_datos(directory):
            pid = ctx.require_project(conn)
            previous_runs = {r[0] for r in conn.execute("SELECT id FROM schedule_runs WHERE project_id=?", (pid,))}
            try:
                result = services.run_cpm(conn, pid)
            except BaseException:
                # La transacción revierte SQLite, pero el archivo de _save_run también requiere limpieza.
                for run in conn.execute("SELECT id,result_path FROM schedule_runs WHERE project_id=?", (pid,)):
                    path = Path(run["result_path"] or "").resolve()
                    if run["id"] not in previous_runs and path.parent == (directory / "runs").resolve():
                        path.unlink(missing_ok=True)
                raise
            result["verification"] = _verification(conn, pid)
            result["activities"] = [dict(id=a.id, code=a.code, early_start=a.early_start,
                early_finish=a.early_finish, late_start=a.late_start, late_finish=a.late_finish,
                total_float=a.total_float, free_float=a.free_float, critical=a.critical)
                for a in repo.list_entities(conn, "activities", pid)]
            result["critical_path"] = [a["id"] for a in result["activities"] if a["critical"]]
            result["simulated"] = not ctx.write
            if ctx.write and not result["verification"]["feasible"]:
                # Los SAVEPOINT internos no deben confirmar fechas que rechace la auditoría.
                conn.rollback()
                (directory / "runs" / f"{result['run_id']}.json").unlink(missing_ok=True)
                result.update(applied=False, run_id=None)
    return Result(result, warnings=list(result.get("warnings") or []),
                  exit_code=0 if result["verification"]["feasible"] else 2)


def diagnosticar(args: Any, ctx: Context) -> Result:
    """Combina diagnóstico compartido con advertencias de validación de la red."""
    from ...optimizacion.diagnostico import diagnosticar as diagnose
    from ...engine.cpm import validate_network
    from ... import services

    _check_dates(args)
    with closing(ctx.conn()) as conn:
        pid = ctx.require_project(conn)
        data = diagnose(conn, pid, desde=args.desde, hasta=args.hasta, periodo=args.periodo)
        errors = validate_network(services.build_problem(conn, pid))
    data["precheck_warnings"] = errors
    return Result(data, warnings=errors)


def carga(args: Any, ctx: Context) -> Result:
    """Entrega el perfil compartido con totales de HH y pico del período elegido."""
    from ... import services, repo

    _check_dates(args)
    with closing(ctx.conn()) as conn:
        profile = repo.json_value(services.load_profile(conn, ctx.require_project(conn),
            {"recurso": "resource", "habilidad": "skill"}[args.agrupar],
            {"dia": "day", "semana": "week", "mes": "month"}[args.periodo], args.desde, args.hasta))
    totals = [sum(s["load"][i] for s in profile["series"]) for i in range(len(profile["labels"]))]
    capacities = [sum(s["capacity"][i] for s in profile["series"]) for i in range(len(totals))]
    peak_index = max(range(len(totals)), key=totals.__getitem__) if totals else None
    profile.update(group={"recurso": "resource", "habilidad": "skill"}[args.agrupar],
        totals=dict(load_hours=sum(totals), capacity_hours=sum(capacities)),
        peak=dict(load_hours=totals[peak_index] if peak_index is not None else 0,
                  period=profile["labels"][peak_index] if peak_index is not None else None),
        totals_by_series=[dict(key=s["key"], load_hours=sum(s["load"]), capacity_hours=sum(s["capacity"]),
                              peak_load_hours=max(s["load"], default=0)) for s in profile["series"]])
    warnings = (["La capacidad por habilidad cuenta a las personas polivalentes en cada habilidad; "
                 "la suma no representa dotación física independiente."] if args.agrupar == "habilidad" else [])
    return Result(profile, warnings=warnings)


def resolver(args: Any, ctx: Context) -> Result:
    """Resuelve una variante aislada y conserva un resultado aplicable."""
    from ._programa_opt import solve
    return solve(args, ctx)


def comparar(args: Any, ctx: Context) -> Result:
    """Compara el producto de variantes y devuelve ranking sin cargas de corrida."""
    from ._programa_opt import compare
    return compare(args, ctx)


def lote(args: Any, ctx: Context) -> Result:
    """Ejecuta un plan incremental con presupuesto, cancelación y reanudación."""
    from ._programa_opt import compare
    if not args.plan:
        raise CliError("uso", "programa lote requiere --plan.")
    return compare(args, ctx)


def aplicar(args: Any, ctx: Context) -> Result:
    """Aplica o simula el resultado seleccionado mediante el módulo de garantías."""
    from ._programa_opt import apply_result, read_result
    with closing(ctx.conn()) as conn:
        pid = ctx.require_project(conn)
    result = read_result(args.resultado, args.variante)
    return Result(apply_result(ctx, pid, result, args.tolerancia))


def corridas(args: Any, ctx: Context) -> Result:
    """Consulta historial persistido o aplica una corrida dentro de mutation."""
    from ... import repo, services
    if args.corridas_action == "aplicar":
        from ...optimizacion.espacio import redirigir_datos
        with ctx.mutation("programa corridas aplicar") as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN")
            pid = ctx.require_project(conn)
            run = repo.get(conn, "schedule_runs", args.id)
            if run["project_id"] != pid:
                raise CliError("no_encontrado", "La corrida no pertenece al proyecto.")
            directory = ctx.data_dir if ctx.write else services.DATA_DIR
            with redirigir_datos(directory):
                if not ctx.write:
                    # crear_copia deja runs vacío: copiar sólo la corrida solicitada y remapear su ruta.
                    original = Path(run["result_path"] or "").resolve()
                    if original.parent != (ctx.data_dir / "runs").resolve() or original.suffix != ".json":
                        raise CliError("datos_invalidos", "La ruta de la corrida no es válida.")
                    target = directory / "runs" / f"{args.id}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(original.read_bytes())
                    conn.execute("UPDATE schedule_runs SET result_path=? WHERE id=?", (str(target.resolve()), args.id))
                try:
                    result = services.apply_run(conn, pid, args.id)
                except ValueError as exc:
                    if "cambió desde" in str(exc):
                        raise CliError("escenario_cambio", str(exc), exit_code=3) from exc
                    raise
                verification = _verification(conn, pid)
                if not verification["feasible"]:
                    raise CliError("infactible", "La corrida no supera la verificación independiente.",
                                   detail=verification, exit_code=2)
                result.update(simulated=not ctx.write, verification=verification,
                              before=run["metrics_before"], after=run["metrics_after"])
        result["backup"] = ctx.mutation_info.backup
        return Result(result)
    with closing(ctx.conn()) as conn:
        pid = ctx.require_project(conn)
        if args.corridas_action == "ver":
            data = repo.get(conn, "schedule_runs", args.id)
            if data["project_id"] != pid:
                raise CliError("no_encontrado", "La corrida no pertenece al proyecto.")
        else:
            total = conn.execute("SELECT COUNT(*) FROM schedule_runs WHERE project_id=?", (pid,)).fetchone()[0]
            rows = conn.execute("SELECT id FROM schedule_runs WHERE project_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                                (pid, args.limite, args.desde)).fetchall()
            data = dict(total=total, runs=[repo.get(conn, "schedule_runs", row[0]) for row in rows])
    return Result(data)
