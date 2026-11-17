"""Mismos umbrales del proyecto: generación <3 s; nivelación de 100.000 <30 s."""
from datetime import date
from time import perf_counter
import pytest

from ganttier import domain as d
from ganttier.calendars import WorkCalendar
from ganttier.maintenance.generator import generate_occurrences
from ganttier.maintenance.leveling import level_periodic


@pytest.mark.slow
def test_100000_feasible_team_batches():
    first, last = date(2027, 1, 1), date(2035, 4, 30)  # cien meses
    calendar = WorkCalendar(d.Calendar(1, "L-V", holidays_country=None), first, last)
    resources, tasks = [], []
    for i in range(100):
        ids = [2*i+1, 2*i+2]
        resources.extend(d.Resource(rid, f"Equipo {rid}", kind="cuadrilla", calendar_id=1,
                                     is_work_team=True, project_id=1) for rid in ids)
        tasks.append(d.PeriodicTask(i+1, 1, f"T{i:03d}", "Limpieza", first_due=first,
            quantity_total=20000, unit="paneles", batch_size=2000, batch_continuous_in_shift=True, productivity_day_team=1000, team_ids=ids))
    tick = perf_counter()
    occurrences = generate_occurrences(tasks, first, last, {1: calendar}, 1, resources=resources)
    generation = perf_counter()-tick
    assert len(occurrences) == 100000
    assert all(o.duration_days == 2 for o in occurrences)
    assert generation < 3.0
    tick = perf_counter()
    result = level_periodic(occurrences, resources, {1: calendar}, d.ObjectiveWeights(level=1, window=0),
                            d.OptimizeOptions(time_limit_s=28, allow_overtime=False), horizon_start=first, horizon_end=last)
    elapsed = perf_counter()-tick
    print(f"100000 lotes: generación={generation:.3f}s; nivelación={elapsed:.3f}s")
    assert result.status != "infactible", result.warnings
    assert len(result.starts) == len(result.worker_assignment) == 100000
    assert result.after.overload_hours == 0
    assert result.after.activities_out_of_window == 0
    assert elapsed < 30.0
