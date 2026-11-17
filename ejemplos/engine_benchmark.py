"""Redes sintéticas reproducibles: python ejemplos/engine_benchmark.py --suite.

No requiere BD, red, OR-Tools ni calendarios de A1. No escribe archivos.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys
from time import perf_counter

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np

from ganttier.domain import Activity, Assignment, ObjectiveWeights, OptimizeOptions, Project, Relation, Resource, ScheduleProblem
from ganttier.engine import compute_cpm, optimize


class BenchmarkCalendar:
    """Calendario L–V simple para aislar el costo del motor."""

    def hours(self, day: date) -> float:
        return 8.0 if day.weekday() < 5 else 0.0


def synthetic(n: int, relationships: int, horizon_days: int = 365) -> ScheduleProblem:
    """Genera un DAG por capas con exactamente la cantidad solicitada de aristas."""
    start = date(2026, 1, 5)
    width = max(100, n // 100)
    activities = [Activity(i, 1, f"A{i:06}", f"Actividad {i}", duration_days=1) for i in range(n)]
    edges = []
    offset = width
    while len(edges) < relationships and offset < n:
        take = min(relationships - len(edges), n - offset)
        edges.extend(Relation(len(edges) + j, 1, j, j + offset) for j in range(take))
        offset += 1
    if len(edges) != relationships:
        raise ValueError("Demasiadas relaciones para esta red sintética.")
    resources = [Resource(1, "Cuadrilla eléctrica", skills=["eléctrico"], max_units=max(1, n / 100))]
    assignments = [Assignment(i, i, resource_id=1, work_hours=8) for i in range(n)]
    return ScheduleProblem(Project(1, "Benchmark A2", start_date=start), activities, edges, resources, assignments, {1: BenchmarkCalendar()}, 1, start, start + timedelta(days=horizon_days - 1))


def run(n: int, relationships: int, method: str, time_limit: float = 50) -> dict:
    """Ejecuta y devuelve tiempos y estado para comparar equipos/versiones."""
    p = synthetic(n, relationships)
    begin = perf_counter()
    cpm = compute_cpm(p)
    cpm_s = perf_counter() - begin
    result = None
    if method != "cpm":
        result = optimize(p, ObjectiveWeights(level=0.5, makespan=0.5, window=0), OptimizeOptions(method=method, time_limit_s=time_limit))
    summary = {"activities": n, "relations": relationships, "cpm_s": round(cpm_s, 4), "finish_cpm": cpm.project_finish.isoformat(), "optimize_s": round(result.elapsed_s, 4) if result else None, "status": result.status if result else "CPM", "warnings": result.warnings[:3] if result else cpm.warnings[:3]}
    print(summary, flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="store_true")
    parser.add_argument("--activities", type=int, default=10000)
    parser.add_argument("--relations", type=int, default=15000)
    parser.add_argument("--method", choices=["cpm", "heuristic", "auto", "cpsat"], default="cpm")
    parser.add_argument("--time-limit", type=float, default=50)
    args = parser.parse_args()
    if args.suite:
        run(10000, 15000, "heuristic", args.time_limit)
        run(100000, 0, "heuristic", args.time_limit)
    else:
        run(args.activities, args.relations, args.method, args.time_limit)
