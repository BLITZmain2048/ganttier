"""Proceso aislado de resolución; nunca escribe en la base de origen.

Gancho de prueba: GANTTIER_TRABAJADOR_RETARDO_S introduce una espera antes del
solver (sólo en este hijo); el supervisor puede forzar así el timeout duro.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import sys
import time

from .metricas import normalizar
from .variantes import Variante


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _empty_result(variant: Variante) -> dict:
    return {"variant": variant.to_dict(), "status": "error", "feasible": False, "infeasibility": None,
            "metrics": normalizar(None), "before": None, "after": None,
            "verification": {"feasible": False, "violations": []}, "violations": [], "warnings": [],
            "elapsed_s": 0.0, "scenario_hash": None, "seed": variant.options.get("seed", 0),
            "schema_version": "1.0", "run_payload_ref": None, "error": None}


def _solve(db_path: Path, data_dir: Path, pid: int, variant: Variante) -> dict:
    # Es crucial fijar el entorno ANTES de importar cualquier módulo de servicios.
    os.environ["GANTTIER_DB"] = str(db_path.resolve())
    os.environ["GANTTIER_DATA_DIR"] = str(data_dir.resolve())
    started = time.monotonic()
    result = _empty_result(variant)
    conn = None
    try:
        from .. import db, repo, services, solve
        conn = db.connect(db_path)
        db.initialize(conn)
        result["database_schema"] = db.SCHEMA_VERSION
        result["scenario_hash"] = services._scenario_fingerprint(conn, pid)
        project = repo.get(conn, "projects", pid)
        origin = variant.horizon_start or project.start_date.isoformat()
        result["horizon_start"] = origin
        weights, options = variant.solve_args()
        delay = float(os.environ.get("GANTTIER_TRABAJADOR_RETARDO_S", "0"))
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("GANTTIER_TRABAJADOR_RETARDO_S debe ser finito y no negativo.")
        if delay:
            time.sleep(delay)
        # El motor fraccional puede degradar silenciosamente a heurística si falta
        # OR-Tools. Una petición explícita de CP-SAT debe informar dependencia.
        if options.get("method") == "cpsat" or options.get("role_mode") == "indivisible":
            from ortools.sat.python import cp_model  # noqa: F401

        captured: dict = {}
        original_save = services._save_run

        def capture(connection, project_id, kind, payload, *, scenario_hash=None, **metadata):
            run_id = original_save(connection, project_id, kind, payload,
                                   scenario_hash=scenario_hash, **metadata)
            # Leer el JSON real inmediatamente: solve_project(apply=False) elimina
            # ese archivo al revertir ocurrencias nuevas de mantenimiento.
            persisted = json.loads((services.DATA_DIR / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            captured.clear()
            captured.update(id=run_id, kind=kind, payload=persisted,
                            scenario_hash=persisted.get("scenario_hash"),
                            weights=metadata.get("weights") or weights or {},
                            options=metadata.get("options") or options,
                            metrics_before=metadata.get("metrics_before"),
                            metrics_after=metadata.get("metrics_after"),
                            status=metadata.get("status", ""), elapsed_s=metadata.get("elapsed_s", 0.0))
            return run_id

        # Sólo este intérprete aislado observa la envoltura; el guardado original
        # y las transacciones del solver se ejecutan sin cambiar su comportamiento.
        services._save_run = capture
        try:
            solved = solve.solve_project(conn, pid, weights, options, apply=False)
        finally:
            services._save_run = original_save
        payload = captured.get("payload") or {}
        feasible = solved.get("feasible") is True
        verification = solved.get("verification") or payload.get("verification") or {
            "feasible": feasible, "violations": solved.get("violations") or []}
        before, after = solved.get("before"), solved.get("after")
        metrics_after = dict(after or {})
        # El motor de roles publica costos y bloques; finish se obtiene del mismo
        # programa auditado, nunca de primary_finish_block (puede ser sólo un target).
        if feasible and not metrics_after.get("finish"):
            finishes = payload.get("finishes") or solved.get("finishes") or {}
            valid = [value for value in finishes.values() if value]
            if valid:
                metrics_after["finish"] = max(valid)
        result.update(status="ok" if feasible else "infactible", feasible=feasible,
                      infeasibility=None if feasible else solved.get("infeasibility", "no_encontrada"),
                      before=before, after=after,
                      metrics=normalizar({"before": before, "after": metrics_after if feasible else after,
                                          "horizon_start": origin}),
                      verification=verification, violations=verification.get("violations") or [],
                      warnings=list(solved.get("warnings") or []),
                      solver_elapsed_s=solved.get("elapsed_s", 0.0),
                      generation=solved.get("generation"), method_used=solved.get("method_used"))
        if captured:
            result["run"] = captured
            result["run_scenario_hash"] = captured["scenario_hash"]
        if captured and captured["scenario_hash"] != result["scenario_hash"]:
            result["warnings"].append("La corrida contiene ocurrencias generadas en la copia; aplicarla requiere regenerar y verificar el escenario de origen.")
        conn.commit()
    except Exception as exc:
        code = "dependencia_faltante" if isinstance(exc, ImportError) else "datos_invalidos" if isinstance(exc, ValueError) else "interno"
        result.update(status="error", feasible=False,
                      error={"type": type(exc).__name__, "code": code, "message": str(exc)})
        result.pop("run", None)
    finally:
        if conn is not None:
            conn.close()
        result["elapsed_s"] = time.monotonic() - started
    return result


def main(argv: list[str] | None = None) -> int:
    """Resuelve una copia y escribe ResultadoVariante; una infactibilidad sale 0."""
    parser = argparse.ArgumentParser(description="Trabajador aislado de Ganttier")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--variante-json", required=True, type=Path)
    parser.add_argument("--salida", required=True, type=Path)
    args = parser.parse_args(argv)
    with contextlib.redirect_stdout(sys.stderr):
        try:
            variant = Variante.from_dict(json.loads(args.variante_json.read_text(encoding="utf-8")))
            result = _solve(args.db, args.data_dir, args.pid, variant)
        except Exception as exc:
            result = _empty_result(Variante())
            result["error"] = {"type": type(exc).__name__, "code": "datos_invalidos" if isinstance(exc, ValueError) else "interno",
                               "message": str(exc)}
        _write_json(args.salida, result)
    if result["status"] != "error":
        return 0
    return {"dependencia_faltante": 6, "datos_invalidos": 1}.get(result["error"]["code"], 5)


if __name__ == "__main__":
    raise SystemExit(main())
