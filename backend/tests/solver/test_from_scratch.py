from datetime import timedelta
import random

import pytest

from ganttier.domain import Activity, ObjectiveWeights, OptimizeOptions, Relation, Resource
from ganttier.engine import optimize
from ganttier.engine.verify import verify_schedule
from ganttier.maintenance import level_periodic, crew_assignment
from ganttier.domain import Occurrence
from ganttier.calendars import WorkCalendar
from ganttier.domain import Calendar


@pytest.mark.parametrize('seed', range(30))
def test_random_pure_task_lists(case, seed):
    rng = random.Random(seed)
    n = 10 if seed == 0 else 200 if seed == 29 else rng.randint(10, 200)
    p = case(n=0, horizon=1500)
    k = 1 + seed % 5
    p.resources = [Resource(i + 1, f'Equipo {i}', skills=[f'skill{i}'], max_units=2, calendar_id=2) for i in range(k)]
    # A serial witness defines feasible hard windows; it is discarded and is
    # never passed to the solver. The solver receives no dates or assignments.
    witness_finish = p.horizon_start
    for i in range(1, n + 1):
        cid, duration = rng.choice([1, 2]), rng.randint(1, 3)
        cal = p.calendars[cid]
        earliest = cal.next_workday(p.horizon_start + timedelta(days=rng.randint(0, 5)))
        witness_start = cal.next_workday(max(earliest, witness_finish + timedelta(days=1)))
        witness_finish = cal.add_workdays(witness_start, duration)
        a = Activity(i, 1, f'A{i}', f'Tarea {i}', duration_days=duration, work_hours=duration * rng.choice([4, 8, 12]),
                     required_skill=f'skill{rng.randrange(k)}', calendar_id=cid,
                     window_start=earliest, window_end=witness_finish + timedelta(days=rng.randint(10, 25)), window_hard=i % 4 == 0)
        p.activities.append(a)
        if i > 1:
            parents = rng.sample(range(1, i), min(i - 1, rng.randint(0, 3)))
            for pred in parents:
                p.relations.append(Relation(len(p.relations) + 1, 1, pred, i, rng.choice(['FS', 'SS', 'FF', 'SF']), rng.choice([0, 0, -1])))
    result = optimize(p, ObjectiveWeights(makespan=1, window=0), OptimizeOptions(method='heuristic', time_limit_s=1, seed=seed, allow_overtime=False))
    assert result.status != 'infactible', result.warnings
    report = verify_schedule(p, result.starts, result.finishes)
    assert report.feasible, report.violations[:5]
    assert all(a.planned_start is None and a.planned_finish is None for a in p.activities)


def test_maintenance_without_prior_dates(case):
    p = case(n=12, mode='mantenimiento', hours=8)
    occ = [Occurrence(1, i, a.code, a.name, p.horizon_start, p.horizon_start, p.horizon_end,
                      1, 8, 1, 'electrico', None, 1, activity_id=a.id) for i, a in enumerate(p.activities)]
    result = level_periodic(occ, p.resources, p.calendars, ObjectiveWeights(makespan=1, window=0), OptimizeOptions())
    assert result.status != 'infactible', result.warnings
    assert verify_schedule(p, result.starts, result.finishes, worker_assignment=result.worker_assignment, crews=crew_assignment(result)).feasible


def test_generic_skill_with_complementary_worker_calendars(case):
    p = case(n=2)
    for cid, week in [(3, '1010100'), (4, '0101000')]:
        p.calendars[cid] = WorkCalendar(Calendar(cid, str(cid), workweek=week, holidays_country=None), p.horizon_start, p.horizon_end)
    p.resources = [Resource(1, 'L-M-V', skills=['electrico'], calendar_id=3), Resource(2, 'M-J', skills=['electrico'], calendar_id=4)]
    p.relations = [Relation(1, 1, 1, 2)]
    result = optimize(p, ObjectiveWeights(makespan=1, window=0), OptimizeOptions(method='heuristic', time_limit_s=1))
    assert result.status != 'infactible', result.warnings
    assert verify_schedule(p, result.starts, result.finishes).feasible
    assert result.finishes[2] == p.horizon_start + timedelta(days=1)
