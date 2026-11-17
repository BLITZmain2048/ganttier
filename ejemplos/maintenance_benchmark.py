"""Plan sintético reproducible de subestaciones, sin red ni dependencias de A1.

Desde Ganttier: python -B ejemplos/maintenance_benchmark.py --count 100000
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ganttier.domain import ObjectiveWeights, OptimizeOptions, PeriodicTask, Resource
from ganttier.maintenance import generate_occurrences, level_periodic


class BenchmarkCalendar:
    """Lunes a viernes sin feriados para mediciones comparables."""

    def __init__(self, start: date, end: date):
        self.horizon_start, self.horizon_end = start, end
        self._hours = np.where((np.arange((end - start).days + 1) + start.weekday()) % 7 < 5, 8.0, 0.0)

    def hours(self, d: date) -> float:
        index = (d - self.horizon_start).days
        return float(self._hours[index]) if 0 <= index < len(self._hours) else 0.0

    def is_workday(self, d: date) -> bool:
        return self.hours(d) > 0

    def workday_index(self) -> np.ndarray:
        return self._hours > 0

    def day_hours_vector(self) -> np.ndarray:
        return self._hours.copy()

    def next_workday(self, d: date, include: bool = True) -> date:
        index = max(0, (d - self.horizon_start).days + int(not include))
        found = np.flatnonzero(self._hours[index:] > 0)
        if not len(found):
            raise ValueError("No hay un día laborable posterior en el horizonte.")
        return self.horizon_start + timedelta(days=index + int(found[0]))

    def prev_workday(self, d: date, include: bool = True) -> date:
        index = min(len(self._hours) - 1, (d - self.horizon_start).days - int(not include))
        found = np.flatnonzero(self._hours[:max(0, index + 1)] > 0)
        if not len(found):
            raise ValueError("No hay un día laborable anterior en el horizonte.")
        return self.horizon_start + timedelta(days=int(found[-1]))

    def add_workdays(self, start: date, n: int) -> date:
        if n == 0:
            return start
        index = max(0, (start - self.horizon_start).days)
        found = np.flatnonzero(self._hours[index:] > 0)
        if len(found) < n:
            raise ValueError("La duración excede el horizonte laboral.")
        return self.horizon_start + timedelta(days=index + int(found[n - 1]))

    def workdays_between(self, a: date, b: date) -> int:
        lo, hi = max(0, (a - self.horizon_start).days), min(len(self._hours), (b - self.horizon_start).days + 1)
        return int(np.count_nonzero(self._hours[lo:max(lo, hi)]))


def build_plan(count: int = 100000):
    """Devuelve tareas, recursos, calendarios e intervalo para count ocurrencias."""
    if count < 1:
        raise ValueError("La cantidad de ocurrencias debe ser positiva.")
    start, end = date(2024, 1, 1), date(2026, 12, 31)
    skills = ("protecciones", "transformadores", "lineas", "servicios_auxiliares")
    names = ("Prueba de relés", "Inspección de transformador", "Inspección de paño", "Revisión de SS/AA")
    locations = ["S/E Alto Jahuel"] + [f"S/E Valle {i:02d}" for i in range(1, 80)]
    tasks = []
    full, remainder = divmod(count, 36)
    for i in range(full + bool(remainder)):
        due = date(2024, 1, 1 + i % 28)
        stop = end
        if i == full:
            year, month = divmod(remainder - 1, 12)
            stop = date(2024 + year, month + 1, 28)
        tasks.append(PeriodicTask(
            i + 1, 1, f"MP-{i + 1:05d}", names[i % 4], frequency="M", first_due=due,
            duration_days=1, work_hours=float(1 + i % 4), required_skill=skills[i % 4],
            calendar_id=1, tol_before_days=7, tol_after_days=7, window_hard=True,
            location=locations[i % len(locations)], equipment=f"PAÑO-{i // 80 + 1:03d}",
            voltage_kv=220, requires_outage=i % 5 == 0, horizon_end=stop))
    # Enough workers for the largest role; individual daily capacity is still checked.
    per_skill = max(2, int(np.ceil(count / 100000 * 24)))
    resources = [Resource(i * per_skill + j + 1, f"Técnico {skill} {j + 1}", skills=[skill],
                          calendar_id=1, cost_hour=12000, overtime_cost_hour=18000,
                          max_overtime_hours_day=2)
                 for i, skill in enumerate(skills) for j in range(per_skill)]
    return tasks, resources, {1: BenchmarkCalendar(start, end)}, start, end


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100000)
    parser.add_argument("--no-workers", action="store_true")
    args = parser.parse_args()
    tasks, resources, calendars, start, end = build_plan(args.count)
    tick = perf_counter()
    occurrences = generate_occurrences(tasks, start, end, calendars, 1)
    generation = perf_counter() - tick
    tick = perf_counter()
    result = level_periodic(occurrences, resources, calendars, ObjectiveWeights(level=1, window=0.05),
                            OptimizeOptions(assign_workers=not args.no_workers, time_limit_s=28))
    leveling = perf_counter() - tick
    print(json.dumps({"occurrences": len(occurrences), "scheduled": len(result.starts),
                      "workers_assigned": len(result.worker_assignment), "generation_s": round(generation, 4),
                      "leveling_s": round(leveling, 4), "status": result.status,
                      "std_before": result.before.load_std, "std_after": result.after.load_std,
                      "outside_windows": result.after.activities_out_of_window,
                      "warnings": result.warnings}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
