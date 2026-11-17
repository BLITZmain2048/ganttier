"""SGS serial y mejora local con perfiles actualizados incrementalmente."""
from __future__ import annotations

import heapq
from time import perf_counter

import numpy as np

from .cpm import Model
from .loads import Loads


def _independent_days(m: Model, loads: Loads, late: np.ndarray, deadline: float, construction_only=False) -> tuple | None:
    """Agrupa tareas independientes de un día sin perder la demanda individual.

    Cada extracción del heap añade una tarea al menor costo marginal. Las claves
    obsoletas son cotas inferiores: carga, costo y término solo crecen en el SGS.
    La mejora local mueve unidades entre fechas, luego expande los grupos a IDs.
    """
    if m.problem.relations or m.fixed.any() or not np.all(m.duration == 1) or loads.dynamic_shares:
        return None
    groups: dict[tuple, list[int]] = {}
    for i, a in enumerate(m.acts):
        if m.upper[i] < m.lower[i]:
            return None
        cal = m.cals[i]
        days = cal.days[cal.rank(int(m.lower[i])):cal.rank(int(m.upper[i]) + 1)]
        if not len(days):
            return None
        if any(units for _, _, units in loads.demands[i]) and np.ptp(cal.hours[days]) > 1e-9:
            return None
        signature = (id(cal), int(m.lower[i]), int(m.upper[i]), a.window_start, a.window_end,
                     a.constraint_type, a.constraint_date, a.priority, int(late[i] - m.starts[i]), tuple(loads.demands[i]))
        groups.setdefault(signature, []).append(i)
    loads.load.fill(0)
    loads.period_load.fill(0)
    starts, finishes = m.starts.copy(), m.finishes.copy()
    finish_counts = np.zeros(loads.size, dtype=np.int64)
    max_finish = m.idx(m.problem.project.start_date)
    allocations = []
    # Same ordering as the general serial SGS, except exchangeable IDs are grouped.
    ordered = sorted(groups.values(), key=lambda ids: (m.cals[ids[0]].rank(int(late[ids[0]])) - m.cals[ids[0]].rank(int(m.starts[ids[0]])), -m.acts[ids[0]].priority, m.idx(m.acts[ids[0]].window_start) if m.acts[ids[0]].window_start else m.lower[ids[0]], ids[0]))
    w = loads.weights / loads.scales
    for ids in ordered:
        i = ids[0]
        cal = m.cals[i]
        candidates = cal.days[cal.rank(int(m.lower[i])):cal.rank(int(m.upper[i]) + 1)]
        demands = [(g, hours + units * float(cal.hours[candidates[0]])) for g, hours, units in loads.demands[i]]
        window = loads.window(i, candidates, candidates) * w[2]
        allocated = np.zeros(len(candidates), dtype=np.int64)

        def marginal(k: int) -> float:
            day = int(candidates[k]) - loads.begin
            period = loads.period_index[day]
            value = float(window[k]) + w[1] * max(int(candidates[k]), max_finish)
            value += loads.extension_price * max(0, max(int(candidates[k]), max_finish) - loads.reference_finish)
            for g, amount in demands:
                old = float(loads.load[g, day])
                new = old + amount
                if loads.options.respect_resource_limits and loads.capacity_defined[g] and new > loads.limit[g, day] + 1e-8:
                    return float("inf")
                excess0 = max(0.0, old - loads.capacity[g, day]) if loads.capacity_defined[g] else 0.0
                excess1 = max(0.0, new - loads.capacity[g, day]) if loads.capacity_defined[g] else 0.0
                if w[0] and g in loads._visible:
                    value += w[0] * (2 * loads.period_load[g, period] * amount + amount * amount + excess1**2 - excess0**2)
                if w[3] and g in loads._cost_rows:
                    ot = loads.overtime[g, day]
                    value += w[3] * ((min(excess1, ot) - min(excess0, ot)) * loads.price[g, day] + (max(0.0, excess1 - ot) - max(0.0, excess0 - ot)) * loads.penalty)
            return float(value)

        heap = [(marginal(k), k) for k in range(len(candidates))]
        heapq.heapify(heap)
        for _ in ids:
            while True:
                value, k = heapq.heappop(heap)
                updated = marginal(k)
                if updated > value + 1e-10:
                    heapq.heappush(heap, (updated, k))
                else:
                    break
            # If all dates overflow, retain explicit dates and let the final audit
            # report the infeasibility; no capacity violation is silently accepted.
            allocated[k] += 1
            day = int(candidates[k]) - loads.begin
            finish_counts[day] += 1
            for g, amount in demands:
                loads.load[g, day] += amount
                loads.period_load[g, loads.period_index[day]] += amount
            max_finish = max(max_finish, int(candidates[k]))
            heapq.heappush(heap, (marginal(k), k))
        allocations.append((ids, candidates, allocated))
    # One move represents one real activity. Repeated dates are evaluated once,
    # not once per each of the potentially 100,000 identical activities.
    while not construction_only:
        changed = False
        for ids, candidates, allocated in allocations:
            i = ids[0]
            for source in np.flatnonzero(allocated):
                if perf_counter() >= deadline:
                    break
                old = int(candidates[source])
                loads.change(i, old, -1)
                finish_counts[old - loads.begin] -= 1
                occupied = np.flatnonzero(finish_counts)
                other = int(occupied[-1]) + loads.begin if len(occupied) else m.idx(m.problem.project.start_date)
                scores, _ = loads.candidates(i, candidates, other)
                best = int(np.argmin(scores))
                if scores[best] < scores[source] - 1e-9:
                    allocated[source] -= 1
                    allocated[best] += 1
                    changed = True
                    target = int(candidates[best])
                else:
                    target = old
                loads.change(i, target, 1)
                finish_counts[target - loads.begin] += 1
        if not changed or perf_counter() >= deadline:
            break
    for ids, candidates, allocated in allocations:
        dates = np.repeat(candidates, allocated)
        starts[ids], finishes[ids] = dates, dates
    warnings = ["Se agotó el tiempo de búsqueda; se conserva el programa completo construido por grupos."] if perf_counter() >= deadline else []
    return starts, finishes, warnings


def solve(model: Model, loads: Loads, late: np.ndarray, deadline: float, *, construction_only=False, initial=None) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Genera una solución completa; audita luego los conflictos si vence el tiempo."""
    m = model
    n = len(m.acts)
    if n >= 1000 and initial is None:
        grouped = _independent_days(m, loads, late, deadline, construction_only)
        if grouped is not None:
            return grouped
    starts, finishes = m.starts.copy(), m.finishes.copy()
    loads.load.fill(0)
    loads.period_load.fill(0)
    warnings: list[str] = []
    placed = m.fixed.copy()
    for i in np.flatnonzero(m.fixed):
        loads.change(int(i), int(starts[i]), 1)
    degree = np.array([len(x) for x in m.incoming], dtype=np.int64)

    def priority(i: int) -> tuple:
        a = m.acts[i]
        slack = m.cals[i].rank(int(late[i])) - m.cals[i].rank(int(m.starts[i]))
        return (slack, -a.priority, m.idx(a.window_start) if a.window_start else m.lower[i], i)

    queue = [priority(i) for i in range(n) if degree[i] == 0]
    heapq.heapify(queue)
    max_finish = int(finishes[m.fixed].max()) if m.fixed.any() else m.idx(m.problem.project.start_date)
    timed_out = False
    # En la construcción temprana, sin costos de carga, ventana u horas extra,
    # el puntaje no decrece al atrasar el inicio. La primera fecha factible es
    # el mismo mínimo que elegiría el barrido completo (incluidos los empates).
    # Evaluar por bloques evita gastar el presupuesto en el resto del horizonte.
    early_only = (construction_only and not np.any(loads.weights[[0, 2, 3]])
                  and loads.weights[1] >= 0 and loads.extension_price >= 0)
    if initial is not None:
        starts, finishes = initial[0].copy(), initial[1].copy()
        loads.rebuild(starts)
        queue = []
    while queue:
        i = heapq.heappop(queue)[-1]
        if not m.fixed[i]:
            lower = max([int(m.lower[i])] + [m.edge_lower(i, p, r, starts, finishes) for p, r in m.incoming[i]])
            upper = int(m.upper[i])
            for j, r in m.outgoing[i]:
                if placed[j]:
                    upper = min(upper, m.edge_upper(i, j, r, starts, finishes))
            cal = m.cals[i]
            if perf_counter() >= deadline:
                timed_out = True
            if upper >= lower and not timed_out:
                candidates = cal.days[cal.rank(lower):cal.rank(upper + 1)]
                if len(candidates):
                    step = 128 if early_only else len(candidates)
                    chosen = None
                    for offset in range(0, len(candidates), step):
                        if offset and perf_counter() >= deadline:
                            timed_out = True
                            break
                        batch = candidates[offset:offset + step]
                        scores, candidate_finishes = loads.candidates(i, batch, max_finish)
                        best = int(np.argmin(scores))
                        if np.isfinite(scores[best]):
                            chosen = batch[best], candidate_finishes[best]
                            break
                    if chosen is not None:
                        starts[i], finishes[i] = chosen
                    else:
                        starts[i], finishes[i] = lower, m.finish(i, lower)
                else:
                    starts[i], finishes[i] = lower, m.finish(i, lower)
            else:
                starts[i], finishes[i] = lower, m.finish(i, lower)
            loads.change(i, int(starts[i]), 1)
            placed[i] = True
        max_finish = max(max_finish, int(finishes[i]))
        for j, _ in m.outgoing[i]:
            degree[j] -= 1
            if degree[j] == 0:
                heapq.heappush(queue, priority(j))
    if timed_out:
        warnings.append("Se agotó el tiempo durante el SGS; se completaron las fechas por precedencias y se auditaron los conflictos.")
    if construction_only:
        return starts, finishes, warnings
    # Count finish dates so removing a task does not scan all activities.
    counts = np.bincount(finishes, minlength=len(m.dates)) if n else np.zeros(len(m.dates), dtype=int)
    while True:
        changed = False
        for i in m.order:
            if perf_counter() >= deadline:
                return starts, finishes, warnings
            if m.fixed[i]:
                continue
            lower = max([int(m.lower[i])] + [m.edge_lower(i, p, r, starts, finishes) for p, r in m.incoming[i]])
            project_finish = int(np.flatnonzero(counts)[-1])
            upper = int(m.upper[i])
            for j, r in m.outgoing[i]:
                upper = min(upper, m.edge_upper(i, j, r, starts, finishes))
            if upper < lower:
                continue
            cal = m.cals[i]
            candidates = cal.days[cal.rank(lower):cal.rank(upper + 1)]
            if not len(candidates):
                continue
            old_start, old_finish = int(starts[i]), int(finishes[i])
            loads.change(i, old_start, -1)
            counts[old_finish] -= 1
            occupied = np.flatnonzero(counts)
            other = int(occupied[-1]) if len(occupied) else m.idx(m.problem.project.start_date)
            scores, candidate_finishes = loads.candidates(i, candidates, other)
            old_score = loads.candidates(i, np.array([old_start]), other)[0][0]
            best = int(np.argmin(scores))
            if scores[best] < old_score - 1e-9:
                starts[i], finishes[i] = candidates[best], candidate_finishes[best]
                changed = True
            loads.change(i, int(starts[i]), 1)
            counts[finishes[i]] += 1
        if not changed:
            break
    return starts, finishes, warnings
