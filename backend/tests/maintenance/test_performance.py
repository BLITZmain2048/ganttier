"""Criterios de aceptación medidos con asignación concreta de trabajadores."""
import importlib.util
from pathlib import Path
from time import perf_counter
from dataclasses import replace

import pytest

from ganttier.domain import ObjectiveWeights, OptimizeOptions
from ganttier.maintenance import generate_occurrences, level_periodic


def _benchmark():
    path = Path(__file__).resolve().parents[3] / "ejemplos" / "maintenance_benchmark.py"
    spec = importlib.util.spec_from_file_location("maintenance_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("count,limit", [(10000, 3.0), (100000, 30.0)])
def test_acceptance_performance(count, limit):
    tasks, resources, calendars, start, end = _benchmark().build_plan(count)
    tick = perf_counter()
    occurrences = generate_occurrences(tasks, start, end, calendars, 1)
    generation = perf_counter() - tick
    assert len(occurrences) == count
    assert generation < 3.0
    tick = perf_counter()
    result = level_periodic(occurrences, resources, calendars, ObjectiveWeights(level=1, window=0.05),
                            OptimizeOptions(time_limit_s=28))
    leveling = perf_counter() - tick
    print(f"\n{count}: generación={generation:.3f}s, nivelación={leveling:.3f}s")
    assert len(result.starts) == count, result.warnings
    assert len(result.worker_assignment) == count
    assert result.after.activities_out_of_window == 0
    assert result.after.load_std < result.before.load_std
    assert leveling < limit


def test_acceptance_100000_split_crews_with_cost():
    tasks, resources, calendars, start, end = _benchmark().build_plan(100000)
    occurrences = generate_occurrences([replace(t, crew_size=2) for t in tasks], start, end, calendars, 1)
    tick = perf_counter()
    result = level_periodic(occurrences, resources, calendars,
                            ObjectiveWeights(level=1, window=1, cost=.2),
                            OptimizeOptions(level_period="week", time_limit_s=28))
    elapsed = perf_counter() - tick
    print(f"\n100000 cuadrillas: nivelación={elapsed:.3f}s")
    assert elapsed < 30
    assert result.status == "heuristico", result.warnings
    assert len(result.starts) == len(result.crew_assignment) == 100000
    assert all(len(set(crew)) == 2 for crew in result.crew_assignment.values())
    assert result.after.activities_out_of_window == 0
    assert result.after.load_std < result.before.load_std
