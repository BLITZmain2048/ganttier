"""Exact start enumeration, independent of every production solver/helper."""
from datetime import timedelta
from math import ceil
import random

import pytest

from ganttier.domain import Assignment, ObjectiveWeights, OptimizeOptions, Relation, Resource
from ganttier.engine import compute_cpm, optimize
from ganttier.engine.verify import verify_schedule


def small_instances(case):
    result = []
    for seed in range(12):
        rng = random.Random(seed + 1701)
        n = 4 + seed % 3
        p = case(n=n, horizon=30)
        nr = 1 + seed % 2
        p.resources = [Resource(r + 1, f'R{r}', skills=['electrico'], max_units=1 + seed % 2) for r in range(nr)]
        for a in p.activities:
            a.duration_days = rng.randint(1, 2)
            a.work_hours = 8 * a.duration_days
            p.assignments.append(Assignment(a.id, a.id, rng.randrange(nr) + 1, work_hours=a.work_hours))
        for i in range(2, n + 1):
            if rng.random() < .6:
                p.relations.append(Relation(len(p.relations) + 1, 1, rng.randrange(1, i), i))
        result.append(p)
    chain = case(n=8, horizon=30)
    chain.relations = [Relation(i, 1, i, i + 1) for i in range(1, 8)]
    chain.assignments = [Assignment(a.id, a.id, 1, work_hours=8) for a in chain.activities]
    result.append(chain)
    return result


def brute_force(problem):
    """Enumerate all integer workday starts below the best known serial bound.

    Only prune partial capacity/FS violations and spans >= incumbent. No CPM,
    verifier, optimizer, priority heuristic, or production bounds are called.
    The fixtures have a common L-V calendar, integer HH and only FS0 arcs.
    """
    n = len(problem.activities)
    duration = [a.duration_days for a in problem.activities]
    best = sum(duration)
    capacity = {r.id: min(8, r.capacity_hours_day) * r.max_units for r in problem.resources}
    resource = {a.activity_id: a.resource_id for a in problem.assignments}
    rows = {rid: [0] * best for rid in capacity}
    starts, finishes = [0] * n, [0] * n
    preds = [[r.pred_id - 1 for r in problem.relations if r.succ_id == a.id] for a in problem.activities]
    examined = 0

    def visit(i, span):
        nonlocal best, examined
        if i == n:
            examined += 1
            best = min(best, span)
            return
        a = problem.activities[i]
        rid = resource[a.id]
        demand = a.work_hours / duration[i]
        lower = max((finishes[j] for j in preds[i]), default=0)
        for start in range(lower, best - duration[i] + 1):
            end = start + duration[i]
            if max(span, end) >= best:
                continue
            if any(rows[rid][day] + demand > capacity[rid] for day in range(start, end)):
                continue
            starts[i], finishes[i] = start, end
            for day in range(start, end):
                rows[rid][day] += demand
            visit(i + 1, max(span, end))
            for day in range(start, end):
                rows[rid][day] -= demand
    visit(0, 0)
    return best


def span(problem, result):
    finish = max(result.finishes.values())
    return sum((problem.horizon_start + timedelta(days=k)).weekday() < 5
               for k in range((finish - problem.horizon_start).days + 1))


def test_heuristic_gap_and_lower_bounds(case):
    gaps = []
    for p in small_instances(case):
        optimum = brute_force(p)
        heuristic = optimize(p, ObjectiveWeights(makespan=1, window=0), OptimizeOptions(method='heuristic', time_limit_s=2))
        assert verify_schedule(p, heuristic.starts, heuristic.finishes).feasible, heuristic.warnings
        gap = (span(p, heuristic) / optimum - 1) * 100
        gaps.append(gap)
        cpm = compute_cpm(p)
        cp_bound = p.calendars[1].workdays_between(p.horizon_start, cpm.project_finish)
        load_bound = max(ceil(sum(a.work_hours for a in p.assignments if a.resource_id == r.id) /
                              (min(8, r.capacity_hours_day) * r.max_units)) for r in p.resources)
        assert max(cp_bound, load_bound) <= optimum <= span(p, heuristic)
        assert gap <= 10 + 1e-8
    print(f'Instancias: {len(gaps)}; gap heurístico promedio={sum(gaps)/len(gaps):.3f}%; máximo={max(gaps):.3f}%')


def test_cpsat_matches_independent_brute_force(case):
    pytest.importorskip('ortools')
    for p in small_instances(case):
        optimum = brute_force(p)
        result = optimize(p, ObjectiveWeights(makespan=1, window=0), OptimizeOptions(method='cpsat', time_limit_s=10))
        assert result.method_used == 'cpsat', result.warnings
        assert result.status == 'optimo', result.warnings
        assert verify_schedule(p, result.starts, result.finishes).feasible
        assert span(p, result) == optimum
