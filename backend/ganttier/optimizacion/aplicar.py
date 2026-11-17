"""Aplicación transaccional y auditada de resultados transportables de la CLI."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date
import json
from math import isfinite
from pathlib import Path
import sqlite3
from typing import Literal

from . import espacio


class EscenarioCambio(RuntimeError):
    """La corrida ya no corresponde al escenario o no reproduce su objetivo."""

    code = "escenario_cambio"
    exit_code = 3


class VerificacionFallida(RuntimeError):
    """La auditoría posterior rechazó el programa; se revierte la aplicación."""

    code = "interno"
    exit_code = 5

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


class ResultadoInfactible(RuntimeError):
    """La nueva resolución no encontró una solución aplicable."""

    code = "infactible"
    exit_code = 2


@dataclass
class ResultadoAplicacion:
    """Resultado de una aplicación confirmada o de su simulación aislada."""

    aplicado: bool
    simulado: bool
    run_id: int | None
    respaldo: str | None
    estrategia: Literal["corrida", "resolver_de_nuevo"]
    metricas_despues: dict
    advertencias: list[str]


def _cargar_corrida(resultado: dict) -> dict:
    run = resultado.get("run") or resultado.get("run_payload")
    if run is None and resultado.get("run_payload_ref"):
        run = json.loads(Path(resultado["run_payload_ref"]).read_text(encoding="utf-8-sig"))
    if not isinstance(run, dict):
        raise ValueError("El resultado no conserva su corrida: falta run o run_payload_ref.")
    run = deepcopy(run)
    if "payload" not in run:
        # Compatibilidad con §5.3, anterior al envoltorio de §8.2.
        run = dict(payload=run, kind=resultado.get("kind") or run.get("kind"),
                   options=resultado.get("variant", {}).get("options", {}),
                   weights=resultado.get("variant", {}).get("weights"),
                   scenario_hash=run.get("scenario_hash") or resultado.get("scenario_hash"),
                   metrics_after=resultado.get("after"), status=run.get("status", "factible"))
    if run.get("kind") not in {"optimize", "maintenance_level", "role_blocks", "cpm"}:
        raise ValueError(f"Tipo de corrida no admitido: {run.get('kind')!r}.")
    payload = run.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("starts"), dict):
        raise ValueError("La corrida no contiene un mapa de fechas aplicable.")
    if not isinstance(payload.get("finishes"), dict) or set(payload["starts"]) != set(payload["finishes"]):
        raise ValueError("La corrida tiene fechas incompletas.")
    if not isinstance(resultado.get("scenario_hash"), str) or not resultado["scenario_hash"]:
        raise ValueError("Falta el hash del escenario de origen del resultado.")
    if resultado.get("feasible") is not True or resultado.get("status") != "ok":
        raise ValueError("Sólo se pueden aplicar resultados factibles con status='ok'.")
    if run.get("status") == "infactible" or payload.get("verification", {}).get("feasible") is False:
        raise ValueError("La corrida conservada es infactible.")
    run_hash = run.get("scenario_hash") or payload.get("scenario_hash")
    if not run_hash or (payload.get("scenario_hash") and payload["scenario_hash"] != run_hash):
        raise ValueError("Los hashes internos de la corrida son inconsistentes.")
    run["scenario_hash"] = run_hash
    return run


def _opciones(resultado: dict, run: dict) -> dict:
    from ..domain import OptimizeOptions

    variant = resultado.get("variant") or {}
    supplied = dict(variant.get("options") or {})
    recorded = dict(run.get("options") or run["payload"].get("options") or {})
    # Las opciones guardadas describen las decisiones efectivas del motor.
    # No aceptar un resultado editado que cambie la semilla de la reproducción.
    seed = resultado.get("seed", recorded.get("seed", supplied.get("seed", 0)))
    for values in (recorded, supplied):
        if "seed" in values and values["seed"] != seed:
            raise ValueError("La semilla del resultado no coincide con la corrida.")
    values = {**supplied, **recorded, "seed": seed}
    for key in ("horizon_start", "horizon_end", "regenerate"):
        if variant.get(key) is not None:
            values[key] = variant[key]
    OptimizeOptions(**{k: v for k, v in values.items()
                       if k not in {"horizon_start", "horizon_end", "regenerate"}})
    return values


def _leer_run(conn: sqlite3.Connection, run_id: int) -> dict:
    from .. import repo

    row = dict(repo.get(conn, "schedule_runs", run_id))
    row["payload"] = json.loads(Path(row["result_path"]).read_text(encoding="utf-8"))
    row["scenario_hash"] = row["payload"]["scenario_hash"]
    return row


def _comparar_objetivo(expected: dict, actual: dict, tolerancia: float) -> None:
    before = expected.get("metrics_after") or expected["payload"].get("after") or {}
    after = actual.get("metrics_after") or actual["payload"].get("after") or {}
    key = "objective_value"
    if key not in before or key not in after:
        raise EscenarioCambio("No se puede comparar el objetivo de la regeneración: falta objective_value.")
    old, new = float(before[key]), float(after[key])
    if not isfinite(old) or not isfinite(new):
        raise ValueError("El objetivo de la corrida debe ser finito.")
    limit = old + abs(old) * tolerancia / 100.0
    if new > limit + 1e-9 * max(1.0, abs(old)):
        raise EscenarioCambio(f"La regeneración empeoró el objetivo de {old:.12g} a {new:.12g}; "
                              f"tolerancia permitida: {tolerancia:g} %. Se revierte todo.")


def _verificar(conn: sqlite3.Connection, pid: int, run: dict, options: dict) -> dict:
    from .. import domain as d, repo, services
    from ..calendars import WorkCalendar
    from ..engine import verify

    opts = d.OptimizeOptions(**{k: v for k, v in options.items()
                               if k not in {"horizon_start", "horizon_end", "regenerate"}})
    if run["kind"] == "role_blocks":
        from .. import role_schedules
        state = role_schedules.allocation_state(conn, pid)
        if not state["valid"]:
            raise VerificacionFallida("La asignación de roles persistida quedó inválida.")
        axis = state["time_axis"]
        opts = replace(opts, horizon_blocks=axis["horizon_blocks"])
        problem = role_schedules._problem(conn, pid, opts, axis["origin"])
    else:
        problem = services.build_problem(conn, pid)
        first = date.fromisoformat(options["horizon_start"]) if options.get("horizon_start") else problem.horizon_start
        last = date.fromisoformat(options["horizon_end"]) if options.get("horizon_end") else problem.horizon_end
        problem = replace(problem, horizon_start=first, horizon_end=last,
                          calendars={cid: WorkCalendar(cal.cal, first, last)
                                     for cid, cal in problem.calendars.items()})
    starts = {a.id: a.actual_start or a.planned_start for a in problem.activities}
    finishes = {a.id: a.actual_finish or a.planned_finish for a in problem.activities}
    if run["kind"] == "role_blocks":
        # verify_role_schedule reconstruye la demanda nominal y llama a
        # verify_with_options; además comprueba indivisibilidad y cantidades.
        report = verify.verify_role_schedule(problem, starts, finishes, state["role_assignment"],
                                             opts, block_hours=opts.block_hours)
    else:
        # Auditoría sobre las asignaciones efectivamente persistidas, no sólo
        # las propuestas en el JSON recibido.
        kwargs = {}
        if problem.project.mode == "mantenimiento" and opts.assign_workers:
            members = {}
            pending = {int(aid) for aid in run["payload"]["starts"]}
            for row in problem.assignments:
                if row.activity_id in pending and row.resource_id is not None:
                    members.setdefault(row.activity_id, []).append(row.resource_id)
            # Pasar {} (no None) activa además la comprobación de trabajador
            # ausente, aunque el JSON haya omitido todo el mapa de cuadrillas.
            kwargs = {"worker_assignment": {aid: rows[0] for aid, rows in members.items()},
                      "crews": members}
        report = verify.verify_with_options(problem, starts, finishes, opts, **kwargs)
    result = repo.json_value(report)
    if not report.feasible:
        raise VerificacionFallida("La verificación independiente posterior rechazó el programa.", result)
    return result


def _restaurar(conn: sqlite3.Connection, backup: Path) -> None:
    from .. import backups

    ok, detail = backups.verify_file(backup)
    if not ok:
        raise RuntimeError(f"No se puede restaurar el respaldo: {detail}")
    # Nunca reemplazar archivos SQLite ni borrar WAL de una conexión abierta.
    with closing(espacio._lectura(backup)) as source:
        source.backup(conn)
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("La base restaurada no pasó integrity_check.")


def _estado(conn: sqlite3.Connection, pid: int) -> dict:
    return {row["id"]: dict(row) for row in conn.execute(
        "SELECT id,code,planned_start,planned_finish FROM activities WHERE project_id=?", (pid,))}


def _asignaciones(conn: sqlite3.Connection, pid: int) -> dict:
    result = {}
    for row in conn.execute("""SELECT activity_id,resource_id,skill,units,work_hours FROM assignments
        WHERE activity_id IN (SELECT id FROM activities WHERE project_id=?)
        ORDER BY activity_id,resource_id,skill,units,work_hours""", (pid,)):
        result.setdefault(row["activity_id"], []).append(tuple(row))
    for row in conn.execute("""SELECT activity_id,source_assignment_id,resource_id,skill
        FROM role_allocations WHERE project_id=? ORDER BY activity_id,resource_id""", (pid,)):
        result.setdefault(row["activity_id"], []).append(tuple(row))
    return result


def _aplicar(db_path: Path, data_dir: Path, pid: int, resultado: dict, run: dict, *,
             backup_enabled: bool, tolerancia: float, warnings: list[str]) -> ResultadoAplicacion:
    from .. import db, repo, services, solve

    backup = None
    strategy = "corrida"
    with espacio.redirigir_datos(data_dir), closing(db.connect(db_path)) as conn:
        # Impide cambios SQLite entre la comprobación del hash, el respaldo y
        # la aplicación, incluso si el otro escritor no usa archivos de lock.
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise espacio.BaseOcupada("Otro escritor mantiene una transacción SQLite abierta.") from exc
            raise
        files = set()
        previous_runs = {r[0] for r in conn.execute("SELECT id FROM schedule_runs")}
        changed = False
        try:
            current_hash = services._scenario_fingerprint(conn, pid)
            if current_hash != resultado["scenario_hash"]:
                raise EscenarioCambio("El escenario cambió desde la optimización; vuelva a resolver antes de aplicar.")
            before = _estado(conn, pid)
            assignments_before = _asignaciones(conn, pid)
            options = _opciones(resultado, run)
            missing = {int(aid) for aid in run["payload"]["starts"]} - set(before)
            regenerated = bool((resultado.get("generation") or {}).get("created") or
                               (resultado.get("generation") or {}).get("regenerated"))
            needs_solve = bool(missing or run["scenario_hash"] != current_hash or regenerated)
            if needs_solve and repo.get(conn, "projects", pid).mode != "mantenimiento":
                raise EscenarioCambio("La corrida no corresponde a las actividades del escenario de origen.")
            if backup_enabled:
                backup = espacio.respaldar(db_path, data_dir, f"aplicar variante {resultado.get('variant', {}).get('id', '')}")
                manifest = json.loads(backup.with_suffix(".json").read_text(encoding="utf-8"))
                if manifest.get("kind") != "cli":
                    warnings.append("Respaldo conservado como manual: backups.py aún no admite kind='cli'.")
            if needs_solve:
                strategy = "resolver_de_nuevo"
                changed = True
                # El wrapper público descarta el inventario nuevo con apply=False.
                # Esta transacción externa conserva la generación provisional hasta
                # comparar el objetivo y auditarla; cualquier rechazo revierte todo.
                solved = solve._solve_project(conn, pid, weights=run.get("weights"), options=options, apply=False)
                if not solved.get("feasible") or solved.get("run_id") is None:
                    raise ResultadoInfactible("La regeneración no encontró un programa factible; se revierte todo.")
                generated = _leer_run(conn, solved["run_id"])
                files.add(Path(generated["result_path"]))
                _comparar_objetivo(run, generated, tolerancia)
                conn.execute("DELETE FROM schedule_runs WHERE id=?", (solved["run_id"],))
                espacio._retry(lambda: Path(generated["result_path"]).unlink(missing_ok=True))
                run = generated
                warnings.append("Se regeneraron las ocurrencias y se resolvió con las mismas opciones y semilla.")
            payload = deepcopy(run["payload"])
            payload["cli"] = {"origin": "cli", "variant_id": resultado.get("variant", {}).get("id"),
                              "seed": options.get("seed", 0), "source_scenario_hash": resultado["scenario_hash"],
                              "strategy": strategy}
            changed = True
            run_id = services._save_run(conn, pid, run["kind"], payload, scenario_hash=run["scenario_hash"],
                weights=run.get("weights"), options=run.get("options") or {
                    k: v for k, v in options.items() if k not in {"horizon_start", "horizon_end", "regenerate"}},
                metrics_before=run.get("metrics_before"), metrics_after=run.get("metrics_after"),
                status=run.get("status") or payload.get("status", "factible"), elapsed_s=run.get("elapsed_s", 0))
            files.add(data_dir / "runs" / f"{run_id}.json")
            services.apply_run(conn, pid, run_id)
            report = _verificar(conn, pid, run, options)
            after = _estado(conn, pid)
            assignments_after = _asignaciones(conn, pid)
            changes = [{"activity_id": aid, "code": item["code"], "before": before.get(aid), "after": item}
                       for aid, item in after.items() if before.get(aid) != item]
            metrics = dict(run.get("metrics_after") or payload.get("after") or {})
            metrics.update(verification=report, changed_activities=len(changes), changes=changes,
                           changed_assignment_activities=sorted(aid for aid in assignments_before.keys() | assignments_after.keys()
                               if assignments_before.get(aid) != assignments_after.get(aid)),
                           removed_activity_ids=sorted(set(before) - set(after)))
            repo.set_meta(conn, f"cli_run:{run_id}", json.dumps(payload["cli"], ensure_ascii=False))
            conn.execute("INSERT INTO audit_log(project_id,action,entity,entity_id,detail) VALUES (?,?,?,?,?)",
                         (pid, "cli.apply", "schedule_runs", run_id, json.dumps(payload["cli"], ensure_ascii=False)))
            conn.commit()
            return ResultadoAplicacion(True, False, run_id, str(backup) if backup else None,
                                       strategy, metrics, warnings)
        except BaseException as exc:
            # Incluir corridas producidas por solve antes de que éste fallara.
            for row in conn.execute("SELECT id,result_path FROM schedule_runs"):
                if row["id"] not in previous_runs and row["result_path"]:
                    files.add(Path(row["result_path"]))
            conn.rollback()
            try:
                if changed and backup is not None:
                    _restaurar(conn, backup)
            except BaseException as restore_error:
                raise VerificacionFallida(f"Falló la aplicación y también la restauración; respaldo: {backup}",
                                         {"application_error": str(exc), "restore_error": str(restore_error)}) from restore_error
            finally:
                for path in files:
                    if path.resolve().parent == (data_dir / "runs").resolve():
                        espacio._retry(lambda p=path: p.unlink(missing_ok=True))
            if isinstance(exc, VerificacionFallida):
                exc.detail.update(backup=str(backup) if backup else None,
                                  restored=bool(backup), transaction_rolled_back=True)
            raise


def aplicar_resultado(db_path: str | Path, data_dir: str | Path, pid: int, resultado: dict, *,
                      escribir: bool, forzar_bloqueo: bool = False, respaldo: bool = True,
                      tolerancia: float = 0.0) -> ResultadoAplicacion:
    """Aplica o simula una corrida con hash, exclusión, respaldo y auditoría.

    ``tolerancia`` es el porcentaje máximo de empeoramiento del objetivo al
    regenerar mantenimiento. Sin respaldo duradero sigue habiendo rollback
    transaccional. Una simulación no deja corridas ni archivos en la base real.
    """
    if not isfinite(tolerancia) or tolerancia < 0:
        raise ValueError("La tolerancia debe ser un porcentaje no negativo y finito.")
    target, data = Path(db_path).resolve(), Path(data_dir).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {target}")
    run = _cargar_corrida(resultado)
    if not escribir:
        with espacio.crear_copia(target, data) as scratch:
            result = _aplicar(scratch.db_path, scratch.data_dir, pid, resultado, run,
                              backup_enabled=False, tolerancia=tolerancia, warnings=[])
            result.aplicado, result.simulado, result.run_id = False, True, None
            result.advertencias.append("Simulación en copia temporal: la base real permanece intacta.")
            return result
    with espacio.bloqueo_escritura(target, forzar=forzar_bloqueo) as lock:
        return _aplicar(target, data, pid, resultado, run, backup_enabled=respaldo,
                        tolerancia=tolerancia, warnings=[lock.detalle] if forzar_bloqueo else [])
