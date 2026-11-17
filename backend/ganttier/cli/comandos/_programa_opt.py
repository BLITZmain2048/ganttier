"""Adaptadores de optimización: planes, archivos aplicables y progreso por stderr."""
from __future__ import annotations

from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any, Iterator
from uuid import uuid4

from .._base import CliError, Context, MutationInfo, Result


def _object(value: str, label: str) -> dict:
    try:
        data = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise CliError("datos_invalidos", f"{label}: JSON inválido ({exc}).") from exc
    if not isinstance(data, dict):
        raise CliError("datos_invalidos", f"{label} debe ser un objeto JSON.")
    return data


def _read(path: Path) -> dict:
    try:
        return _object(path.read_text(encoding="utf-8-sig"), str(path))
    except OSError as exc:
        raise CliError("no_encontrado", f"No se puede leer {path}: {exc}.") from exc


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _artifact(path: Path | None, suffix: str) -> Path:
    if path:
        return path.expanduser().resolve()
    token = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:10]
    return (Path.cwd() / "programa_resultados" / token / ("resultados" + suffix)).resolve()


def _check_artifacts(ctx: Context, outputs: list[Path], inputs: list[Path] | tuple[Path, ...] = ()) -> None:
    protected = {ctx.db_path.resolve(), *(Path(str(ctx.db_path.resolve()) + suffix)
                                        for suffix in ("-wal", "-shm", ".lock", ".cli.lock"))}
    protected.update(path.resolve() for path in inputs if path is not None)
    runs = (ctx.data_dir / "runs").resolve()
    seen = set()
    for path in outputs:
        target = path.expanduser().resolve()
        if target in protected or target == runs or runs in target.parents:
            raise CliError("uso", "Los resultados deben guardarse fuera de la base, runs/ y los archivos de entrada.")
        if target in seen:
            raise CliError("uso", "Los archivos de salida deben tener rutas distintas.")
        seen.add(target)


def _positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise CliError("datos_invalidos", f"{label} debe ser positivo y finito (segundos).")
    return float(value)


def _variants(args: Any, ctx: Context) -> tuple[int, dict, list]:
    from ...optimizacion.variantes import Variante, expandir_plan, PRESETS

    plan = _read(args.plan) if args.plan else {}
    objectives = getattr(args, "objetivos", None)
    seeds = getattr(args, "semillas", None)
    if args.plan and (objectives or seeds):
        raise CliError("uso", "Use --plan o --objetivos/--semillas, sin combinarlos.")
    if objectives and args.objetivo or seeds and args.semilla is not None:
        raise CliError("uso", "No combine un flag singular con su lista plural.")
    if not args.plan and getattr(args, "programa_action", None) == "comparar" and not (objectives or seeds):
        raise CliError("uso", "comparar requiere --plan, --objetivos o --semillas.")
    overlay: dict[str, Any] = {}
    if args.objetivo is not None:
        if args.objetivo not in PRESETS:
            raise CliError("datos_invalidos", f"Objetivo desconocido; use {', '.join(PRESETS)}.")
        overlay["objective"] = args.objetivo
    if args.pesos is not None:
        overlay["weights"] = _object(args.pesos, "--pesos")
    options = _object(args.opciones, "--opciones") if args.opciones else {}
    for flag, field in (("metodo", "method"), ("tiempo", "time_limit_s"), ("semilla", "seed"), ("modo_roles", "role_mode")):
        value = getattr(args, flag, None)
        if value is not None:
            options[field] = value
    if args.sin_horas_extra:
        options["allow_overtime"] = False
    if options:
        overlay["options"] = options
    for flag, field in (("desde", "horizon_start"), ("hasta", "horizon_end")):
        value = getattr(args, flag, None)
        if value is not None:
            overlay[field] = value.isoformat()
    if args.regenerar is not None:
        overlay["regenerate"] = args.regenerar
    if not args.plan:
        plan = {"defaults": overlay}
        if objectives or seeds:
            grid: dict[str, list] = {}
            if objectives:
                values = [s.strip() for s in objectives.split(",")]
                if not all(s in PRESETS for s in values):
                    raise CliError("datos_invalidos", f"--objetivos debe listar: {', '.join(PRESETS)}.")
                grid["objective"] = values
            if seeds:
                try:
                    grid["options.seed"] = [int(s.strip()) for s in seeds.split(",")]
                except ValueError as exc:
                    raise CliError("datos_invalidos", "--semillas requiere enteros separados por comas.") from exc
            plan["grid"] = grid
        else:
            plan["variants"] = [{}]
    variants = expandir_plan(plan)
    if args.plan and overlay:
        updated = []
        for variant in variants:
            values = variant.to_dict()
            # Cambiar parámetros cambia el id, evitando reanudar con resultados de otra configuración.
            values.pop("id", None)
            values.update({k: v for k, v in overlay.items() if k != "options"})
            values["options"] = {**values.get("options", {}), **options}
            updated.append(Variante.from_dict(values))
        variants = list({v.id: v for v in updated}.values())
    if not variants:
        raise CliError("datos_invalidos", "El plan no contiene variantes.")
    for variant in variants:
        _, effective = variant.solve_args()
        _positive(effective.get("time_limit_s", 30), "options.time_limit_s")
    with closing(ctx.conn()) as conn:
        pid = ctx.resolve_project(conn, ctx.project if ctx.project is not None else plan.get("project"))
        if plan.get("project") is not None and ctx.resolve_project(conn, plan["project"]) != pid:
            raise CliError("datos_invalidos", "El proyecto de --plan no coincide con -p.")
    if args.aplicar and not ctx.write:
        raise CliError("uso", "--aplicar requiere --escribir; use programa aplicar para una simulación.")
    return pid, plan, variants


def _externalize(result: dict, directory: Path) -> dict:
    result = dict(result)
    run = result.pop("run", None)
    legacy = result.pop("run_payload", None)
    if run is not None or legacy is not None:
        identifier = str((result.get("variant") or {}).get("id", "resultado"))
        target = directory / (sha256(identifier.encode("utf-8")).hexdigest()[:24] + ".json")
        _write(target, run if run is not None else legacy)
        result["run_payload_ref"] = str(target.resolve())
        if isinstance(run, dict):
            result["run_ref"] = {k: run.get(k) for k in ("id", "kind", "status", "scenario_hash")}
    result.setdefault("run_ref", result.get("run_payload_ref"))
    result.setdefault("violations", (result.get("verification") or {}).get("violations", []))
    if not result.get("feasible") and result.get("status") == "infactible":
        if result.get("infeasibility") not in {"demostrada", "no_encontrada"}:
            result["infeasibility"] = "no_encontrada"
    return result


def _public(result: dict) -> dict:
    # Defensa adicional incluso si otro adaptador devuelve accidentalmente un run en línea.
    return {k: v for k, v in result.items() if k not in {"run", "run_payload"}}


def _exit(result: dict) -> int:
    if result.get("status") in {"timeout", "cancelado"}:
        return 4
    if result.get("status") == "error":
        error = result.get("error") or {}
        kind = error.get("type", "")
        if kind in {"ImportError", "ModuleNotFoundError"} or error.get("code") == "dependencia_faltante":
            return 6
        return 1 if kind in {"ValueError", "LookupError", "ValidationError"} else 5
    return 0 if result.get("feasible") else 2


def solve(args: Any, ctx: Context) -> Result:
    """Ejecuta una variante en el ejecutor y publica un JSON sin carga útil."""
    from ...optimizacion.ejecutor import ejecutar_variante

    pid, plan, variants = _variants(args, ctx)
    if len(variants) != 1:
        raise CliError("uso", "resolver requiere exactamente una variante; use comparar o lote.")
    path = _artifact(args.mejor_a, ".json")
    directory = path.with_name(path.stem + "_runs")
    _check_artifacts(ctx, [path, directory], [args.plan] if args.plan else [])
    ctx.mutation_info = MutationInfo(simulated=True, db_path=str(ctx.db_path))
    result = ejecutar_variante(ctx.db_path, ctx.data_dir, pid, variants[0],
        timeout_s=plan.get("variant_timeout_s"), conservar_copia=args.conservar_copias,
        incluir_run_payload=True, directorio_runs=directory)
    result = _externalize(result, directory)
    result["result_file"] = str(path)
    _write(path, result)
    if result.get("status") == "error":
        error = result.get("error") or {}
        code = error.get("code") or ("dependencia_faltante" if _exit(result) == 6 else
                                      "datos_invalidos" if _exit(result) == 1 else "interno")
        raise CliError(code, error.get("message") or "Falló la ejecución de la variante.",
                       detail=_public(result), exit_code=_exit(result))
    if args.aplicar and result.get("feasible"):
        result["application"] = apply_result(ctx, pid, result)
    return Result(_public(result), warnings=list(result.get("warnings") or []), exit_code=_exit(result))


def _lines(path: Path, *, partial: bool = False) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise CliError("no_encontrado", f"No se puede leer {path}: {exc}.") from exc
    data = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            data.append(_object(line, f"{path}, línea {index + 1}"))
        except CliError:
            if partial and index == len(lines) - 1:
                break
            raise
    return data


def read_result(path: Path, identifier: str | None = None) -> dict:
    """Lee un resultado, sobre CLI o JSONL; sin id elige la mejor corrida conservada."""
    from ...optimizacion.ranking import clasificar

    path = path.expanduser().resolve()
    if path.suffix.lower() == ".jsonl":
        values = _lines(path)
    else:
        data = _read(path)
        data = data.get("data", data)
        if not isinstance(data, dict):
            raise CliError("datos_invalidos", "El archivo no contiene un resultado de optimización.")
        values = data.get("results", data.get("top", [data]))
    if not isinstance(values, list) or not all(isinstance(v, dict) for v in values):
        raise CliError("datos_invalidos", "El archivo no contiene resultados válidos.")
    # Una reanudación puede guardar varios intentos: el último registro por id prevalece.
    values = list({str((v.get("variant") or {}).get("id", "")): v for v in values}.values())
    if identifier is not None:
        values = [v for v in values if str((v.get("variant") or {}).get("id")) == identifier]
        if not values:
            raise CliError("no_encontrado", f"No existe la variante {identifier} en el archivo.")
    else:
        values = clasificar([v for v in values if v.get("feasible") and
                             (v.get("run") or v.get("run_payload_ref") or v.get("run_payload"))])
    if not values:
        raise CliError("datos_invalidos", "No hay un resultado factible con corrida conservada.",
                       hint="Use --conservar-top con un valor positivo al ejecutar el plan.")
    result = dict(values[0])
    if not result.get("variant") or not result.get("feasible"):
        raise CliError("datos_invalidos", "La variante seleccionada no es factible o no tiene identificador.")
    ref = result.get("run_payload_ref")
    if ref:
        target = Path(ref)
        target = target if target.is_absolute() else path.parent / target
        if not target.is_file():
            raise CliError("no_encontrado", f"No se conserva la corrida: {target}.")
        result["run_payload_ref"] = str(target.resolve())
    elif not (result.get("run") or result.get("run_payload")):
        raise CliError("datos_invalidos", "La variante no tiene una corrida aplicable conservada.")
    return result


def apply_result(ctx: Context, pid: int, result: dict, tolerance: float = 0.0) -> dict:
    """Delega aplicación y refleja simulación, métricas y respaldo en el Context."""
    from ...optimizacion.aplicar import aplicar_resultado

    application = aplicar_resultado(ctx.db_path, ctx.data_dir, pid, result, escribir=ctx.write,
        forzar_bloqueo=ctx.force_lock, respaldo=not ctx.no_backup, tolerancia=tolerance)
    data = asdict(application) if is_dataclass(application) else dict(application)
    ctx.mutation_info = MutationInfo(simulated=data["simulado"], backup=data.get("respaldo"),
                                     db_path=str(ctx.db_path))
    warnings = list(data.get("advertencias") or [])
    for warning in warnings:
        ctx.warn(warning)
    return dict(applied=data["aplicado"], simulated=data["simulado"], run_id=data.get("run_id"),
        backup=data.get("respaldo"), strategy=data.get("estrategia"),
        before=result.get("before"), expected=result.get("metrics"), after=data.get("metricas_despues"),
        variant_id=result["variant"]["id"], warnings=warnings)


@contextmanager
def _progress(cancel: threading.Event, state: dict, total: int) -> Iterator[None]:
    finished = threading.Event()
    previous = None
    installed = threading.current_thread() is threading.main_thread()

    def interrupt(signum: int, frame: Any) -> None:
        state["reason"] = "cancelado"
        cancel.set()

    def report() -> None:
        while not finished.wait(5):
            sys.stderr.write(f"programa: {state['done']}/{total} variantes terminadas; "
                             f"{time.monotonic() - state['start']:.1f} s\n")
            sys.stderr.flush()

    if installed:
        previous = signal.signal(signal.SIGINT, interrupt)
    worker = threading.Thread(target=report, name="ganttier-cli-progreso", daemon=True)
    worker.start()
    sys.stderr.write(f"programa: iniciando {total} variantes\n")
    try:
        yield
    finally:
        finished.set()
        worker.join(timeout=1)
        if installed:
            signal.signal(signal.SIGINT, previous)
        sys.stderr.write(f"programa: {state['done']}/{total} variantes terminadas\n")
        sys.stderr.flush()


def _improvement(previous: dict, current: dict, criterion: str) -> float:
    if criterion.startswith("score:"):
        old, new = previous.get("score"), current.get("score")
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            return 0.0
        return max(0.0, (old - new) / max(abs(old), 1e-12))
    fields = (["activities_out_of_window", "overtime_hours", "makespan_days", "load_std"]
              if criterion == "lexicographic" else criterion.split(","))
    for key in fields:
        old = previous.get("metrics", {}).get(key.strip())
        new = current.get("metrics", {}).get(key.strip())
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and old != new:
            return max(0.0, (old - new) / max(abs(old), 1e-12))
    return 0.0


def compare(args: Any, ctx: Context) -> Result:
    """Ejecuta el plan con journal incremental, cancelación cooperativa y top acotado."""
    from ...optimizacion.ejecutor import ejecutar_plan
    from ...optimizacion.ranking import clasificar

    pid, plan, variants = _variants(args, ctx)
    criterion = args.ranking_por or plan.get("rank_by", "lexicographic")
    clasificar([], criterion)  # Validar antes de lanzar procesos o tocar el journal.
    raw_parallel = args.paralelo or os.environ.get("GANTTIER_CLI_PARALELO") or plan.get("parallel")
    try:
        parallel = int(raw_parallel) if raw_parallel is not None else max(1, min(4, (os.cpu_count() or 1) // 2))
    except (ValueError, TypeError) as exc:
        raise CliError("datos_invalidos", "GANTTIER_CLI_PARALELO debe ser un entero positivo.") from exc
    if parallel < 1:
        raise CliError("datos_invalidos", "--paralelo debe ser positivo.")
    budget = args.presupuesto if args.presupuesto is not None else plan.get("budget_s")
    if budget is not None:
        _positive(budget, "presupuesto")
    if args.reanudar and (not args.resultados_a or not args.resultados_a.is_file()):
        raise CliError("uso", "--reanudar requiere --resultados-a apuntando a un JSONL existente.")
    path = _artifact(args.resultados_a, ".jsonl")
    if path.exists() and not args.reanudar:
        raise CliError("uso", "El archivo de resultados ya existe; use --reanudar o una ruta nueva.")
    if args.reanudar:
        from ...optimizacion.espacio import hash_escenario
        current_hash = hash_escenario(ctx.db_path, pid)
        wanted = {v.id for v in variants}
        for row in _lines(path, partial=True):
            if row.get("variant", {}).get("id") in wanted and row.get("status") in {"ok", "infactible"}:
                if row.get("scenario_hash") != current_hash:
                    raise CliError("escenario_cambio", "El escenario cambió desde el lote guardado.", exit_code=3)
    directory = path.with_name(path.stem + "_runs")
    _check_artifacts(ctx, [path, directory, *([args.mejor_a] if args.mejor_a else [])],
                     [args.plan] if args.plan else [])
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx.mutation_info = MutationInfo(simulated=True, db_path=str(ctx.db_path))
    cancel = ctx.extras.get("cancelar") or threading.Event()
    state = dict(start=time.monotonic(), done=0, reason=None)
    received: dict[str, dict] = {}
    best: list[dict] = []
    threshold = args.parar_si_mejora_menos_de

    def callback(result: dict, done: int, total: int) -> None:
        received[result["variant"]["id"]] = result
        state["done"] = done
        if threshold is not None and result.get("feasible"):
            new = clasificar([*best, result], criterion)[0]
            if best and _improvement(best[0], new, criterion) < threshold:
                state["reason"] = "mejora_insuficiente"
                cancel.set()
            best[:] = [new]

    with _progress(cancel, state, len(variants)):
        try:
            results = ejecutar_plan(ctx.db_path, ctx.data_dir, pid, variants, paralelo=parallel,
                timeout_s=plan.get("variant_timeout_s"), presupuesto_s=budget,
                reanudar_desde=path if args.reanudar else None, salida_jsonl=path,
                callback=callback, cancelar=cancel, directorio_runs=directory,
                conservar_runs=args.conservar_top)
        except KeyboardInterrupt:
            cancel.set()
            state["reason"] = "cancelado"
            results = list(received.values())
    # El journal es la fuente durable, incluso si Ctrl+C interrumpió el retorno.
    ids = {v.id for v in variants}
    merged = {r["variant"]["id"]: r for r in (_lines(path, partial=True) if path.exists() else [])
              if r.get("variant", {}).get("id") in ids}
    merged.update({r["variant"]["id"]: r for r in results})
    cleaned = [_externalize(r, directory) for r in merged.values()]
    ranked = clasificar(cleaned, criterion)
    state["done"] = len(ranked)
    counts = dict(Counter(r.get("status", "error") for r in ranked))
    feasible = sum(bool(r.get("feasible")) for r in ranked)
    counts.update(total=len(variants), completed=len(ranked), feasible=feasible,
                  pending=max(0, len(variants) - len(ranked)))
    counts["retryable"] = sum(r.get("status") not in {"ok", "infactible"} for r in ranked)
    elapsed = time.monotonic() - state["start"]
    stopped = cancel.is_set() or any(r.get("status") in {"timeout", "cancelado"} for r in ranked)
    if budget is not None and counts["pending"] and elapsed >= budget:
        stopped = True
        state["reason"] = state["reason"] or "presupuesto"
    if counts["pending"]:
        stopped = True
    if stopped and not state["reason"]:
        state["reason"] = "cancelado" if cancel.is_set() or counts.get("cancelado") else "timeout"
    warnings = []
    if parallel > 1 and any(v.options.get("method", "auto") in {"auto", "cpsat"} or
                            v.options.get("role_mode") == "indivisible" for v in variants):
        warnings.append("Los procesos CP-SAT pueden usar varios hilos; evite sobresuscribir la CPU al comparar tiempos.")
    try:
        host_load = list(os.getloadavg())
    except (OSError, AttributeError):
        host_load = None
    data = dict(summary=dict(project_id=pid, rank_by=criterion, elapsed_s=elapsed, cpu_count=os.cpu_count(),
        parallel=parallel, host_load=host_load, stop_reason=state["reason"],
        cancelled=cancel.is_set() or bool(counts.get("cancelado")),
        resumed=bool(args.reanudar)), top=[_public(r) for r in ranked[:args.top]],
        results_file=str(path) if path.exists() else None, counts=counts)
    if len(variants) <= 50 or args.completo:
        data["results"] = [_public(r) for r in ranked]
    applicable = [r for r in ranked if r.get("feasible") and r.get("run_payload_ref") and Path(r["run_payload_ref"]).is_file()]
    if args.mejor_a and feasible:
        if not applicable or applicable[0]["variant"]["id"] != ranked[0]["variant"]["id"]:
            warnings.append("La mejor variante del ranking no conserva corrida; aumente --conservar-top para exportarla.")
        else:
            target = args.mejor_a.expanduser().resolve()
            _write(target, applicable[0])
            data["best_file"] = str(target)
    if args.aplicar and feasible:
        if not applicable or applicable[0]["variant"]["id"] != ranked[0]["variant"]["id"]:
            raise CliError("datos_invalidos", "La mejor variante no conserva corrida; aumente --conservar-top.", detail=data)
        data["application"] = apply_result(ctx, pid, applicable[0])
    return Result(data, warnings=warnings, exit_code=4 if stopped else (0 if feasible else 2))
