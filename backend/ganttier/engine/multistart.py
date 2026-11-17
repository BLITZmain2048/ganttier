"""SGS serial/paralelo multi-regla y doble justificación con semilla local."""
from time import perf_counter
import heapq

import numpy as np


def _priorities(m, late, rng):
    n = len(m.acts)
    if n > 500:
        # Avoid quadratic transitive closure for large DAGs. The critical-path
        # positional surrogate and deadline/slack priorities remain O(n + e).
        path = m.duration.astype(float).copy()
        for i in reversed(m.order):
            path[i] += max((path[j] for j, _ in m.outgoing[i]), default=0)
        return [m.upper, late, late - m.starts, -path,
                -np.log(np.maximum(rng.random(n), 1e-12)) / np.maximum(1, path)]
    descendants = [0] * n
    positional = m.duration.astype(float).copy()
    # Python integer bitsets count unique transitive successors (not paths).
    for i in reversed(m.order):
        for j, _ in m.outgoing[i]:
            descendants[i] |= (1 << j) | descendants[j]
        bits = descendants[i]
        while bits:
            bit = bits & -bits
            positional[i] += m.duration[bit.bit_length() - 1]
            bits -= bit
    slack = late - m.starts
    lft = [m.cals[i].finish(int(late[i]), int(m.duration[i])) if not m.fixed[i] else m.finishes[i] for i in range(n)]
    rules = [m.upper, np.argsort(m.order), lft, slack, [-x.bit_count() for x in descendants], -positional]
    # Biased randomization of GRPW via exponential races, reproducible per seed.
    rules.extend(-np.log(np.maximum(rng.random(n), 1e-12)) / np.maximum(1, positional) for _ in range(4))
    return rules


def construct(m, loads, priorities, deadline, parallel=False):
    """Serial picks a priority-eligible task; parallel picks earliest feasible time.

    Generalized precedences use scheduled predecessor events, so SS/FF/SF and
    negative lags do not incorrectly wait for predecessor completion.
    """
    starts, finishes = m.starts.copy(), m.finishes.copy()
    loads.load.fill(0)
    loads.period_load.fill(0)
    for i in np.flatnonzero(m.fixed):
        loads.change(int(i), int(starts[i]), 1)
    degree = [len(edges) for edges in m.incoming]
    def key(i):
        return (float(priorities[i]), -m.acts[i].priority, i)
    queue = [key(i) for i, d in enumerate(degree) if not d]
    heapq.heapify(queue)
    placed = m.fixed.copy()
    while queue:
        if perf_counter() >= deadline:
            return None
        eligible = [row[-1] for row in queue] if parallel else [queue[0][-1]]
        candidates = []
        for i in eligible:
            if m.fixed[i]:
                candidates.append((int(starts[i]), key(i), int(finishes[i])))
                continue
            lo = max([int(m.lower[i])] + [m.edge_lower(i, p, r, starts, finishes) for p, r in m.incoming[i]])
            hi = min([int(m.upper[i])] + [m.edge_upper(i, j, r, starts, finishes) for j, r in m.outgoing[i] if placed[j]])
            if hi < lo:
                return None
            cal = m.cals[i]
            days = cal.days[cal.rank(lo):cal.rank(hi + 1)]
            if not len(days):
                return None
            scores, ends = loads.candidates(i, days, loads.begin)
            valid = np.flatnonzero(np.isfinite(scores))
            if not len(valid):
                return None
            first = int(valid[0])
            candidates.append((int(days[first]), key(i), int(ends[first])))
        start, priority, finish = min(candidates) if parallel else candidates[0]
        i = priority[-1]
        if parallel:
            queue.remove(priority)
            heapq.heapify(queue)
        else:
            heapq.heappop(queue)
        if not m.fixed[i]:
            starts[i], finishes[i] = start, finish
            loads.change(i, start, 1)
        placed[i] = True
        for j, _ in m.outgoing[i]:
            degree[j] -= 1
            if not degree[j]:
                heapq.heappush(queue, key(j))
    return starts, finishes


def justify(m, loads, schedule, deadline):
    """Right shift without lengthening, then reconstruct left by new start order."""
    starts, finishes = schedule[0].copy(), schedule[1].copy()
    loads.rebuild(starts)
    end = int(finishes.max()) if len(finishes) else loads.begin
    for i in reversed(m.order):
        if perf_counter() >= deadline:
            return schedule
        if m.fixed[i]:
            continue
        lo = max([int(m.lower[i])] + [m.edge_lower(i, p, r, starts, finishes) for p, r in m.incoming[i]])
        hi = min([int(m.upper[i]), m.cals[i].start_by(end, int(m.duration[i]))] +
                 [m.edge_upper(i, j, r, starts, finishes) for j, r in m.outgoing[i]])
        cal = m.cals[i]
        days = cal.days[cal.rank(lo):cal.rank(hi + 1)] if hi >= lo else np.array([], dtype=int)
        if not len(days):
            continue
        loads.change(i, int(starts[i]), -1)
        scores, ends = loads.candidates(i, days, end)
        valid = np.flatnonzero(np.isfinite(scores))
        if len(valid):
            k = int(valid[-1])
            starts[i], finishes[i] = days[k], ends[k]
        loads.change(i, int(starts[i]), 1)
    return construct(m, loads, starts, deadline) or schedule


def improve(m, loads, late, deadline, initial):
    """Retain only audited complete incumbents; never interpret timeout as proof."""
    best = initial
    best_value = float('inf')
    trials = 0

    def consider(candidate):
        nonlocal best, best_value
        if candidate is None:
            return False
        s, f = candidate
        loads.rebuild(s)
        if m.violations(s, f) or loads.violations():
            return False
        value = loads.objective(s, f)
        if value < best_value - 1e-9 or (abs(value - best_value) <= 1e-9 and tuple(s) < tuple(best[0])):
            best, best_value = (s.copy(), f.copy()), value
        return True

    consider(initial)
    # Keep the grouped large-instance SGS; transitive bitsets are for <= 500.
    if (len(m.acts) >= 1000 and not m.problem.relations and np.all(m.duration == 1)) or not m.acts or perf_counter() >= deadline:
        loads.rebuild(best[0])
        return *best, []
    rules = _priorities(m, late, np.random.default_rng(loads.options.seed))
    for parallel in ((False, True) if len(m.acts) <= 500 else (False,)):
        for rule in rules:
            if perf_counter() >= deadline:
                break
            candidate = construct(m, loads, rule, deadline, parallel)
            trials += 1
            if consider(candidate):
                for _ in range(2):
                    candidate = justify(m, loads, candidate, deadline)
                    consider(candidate)
        if perf_counter() >= deadline:
            break
    loads.rebuild(best[0])
    m.search_stats = {'constructions': trials, 'seed': loads.options.seed}
    return *best, []
