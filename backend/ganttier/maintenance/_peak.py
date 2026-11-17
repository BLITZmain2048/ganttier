"""Picos por habilidad y búsqueda transaccional, activados sólo con peak > 0."""
from __future__ import annotations

import numpy as np


class RefinementExpired(Exception):
    """Interrumpe únicamente una alternativa; nunca descarta la referencia."""


class PeakProfile:
    """Incluye en una misma habilidad la demanda fija de distintos equipos."""

    def __init__(self, leveler):
        skills = list(dict.fromkeys(o.required_skill for o in leveler.occ))
        ids = {skill: j for j, skill in enumerate(skills)}
        self.role = np.array([ids[o.required_skill] for o in leveler.occ])
        self.day = np.zeros((len(skills), leveler.size))
        self.week = np.zeros((len(skills), leveler.nweeks))
        self.week_ids = leveler.week
        self.weekly = leveler.options.level_period == "week"

    @property
    def measured(self):
        return self.week if self.weekly else self.day

    def update(self, i, days, amount):
        role = self.role[i]
        if len(days) == 1 and np.ndim(amount) == 0:
            day = days[0]
            self.day[role, day] += amount
            self.week[role, self.week_ids[day]] += amount
            return
        self.day[role, days] += amount
        np.add.at(self.week[role], self.week_ids[days], amount)

    def score(self, i, candidates, amount):
        row = self.measured[self.role[i]]
        if self.weekly:
            affected = row[candidates.weeks] + amount * candidates.week_counts
        else:
            affected = row[candidates.days] + amount
        current = row.max(initial=0)
        return np.maximum(current, affected.max(axis=1)) - current

    def value(self):
        return float(self.measured.max(axis=1, initial=0).sum())


def construction_order(leveler):
    """Primero menos fechas laborables y mayor demanda; semilla sólo en empates."""
    tie = np.random.default_rng(leveler.options.seed).permutation(len(leveler.occ))
    return sorted(range(len(leveler.occ)), key=lambda i: (
        len(leveler.candidates[i].starts), -leveler.occ[i].work_hours,
        -leveler.occ[i].priority, int(tie[i])))


def construct_and_improve(lv, order, clock):
    """Incumbente compatible, nueva semilla con filtro Pareto y búsqueda local."""
    from dataclasses import replace

    requested = lv.weights
    reserved = lv.worker_load.copy()
    original_candidates = list(lv.candidates)
    # Start with the historical objective, so opting in cannot silently worsen
    # load dispersion, daily peak, overtime or hard/soft window violations.
    lv.weights = replace(lv.requested_weights, peak=0).normalized()
    lv._construct(order)
    lv._legacy_improve(lv._improvement_order(order))
    lv.weights = requested

    def clear_scores():
        for c in lv.candidates:
            c.static_scores.clear()

    clear_scores()
    elapsed = clock() - lv.started
    deadline = lv.started + lv.options.time_limit_s - max(.01, elapsed * .1)
    if clock() >= deadline:
        lv.peak_search_stats = {"moves": 0, "swaps": 0, "deadline_reached": True,
                                "alternative_accepted": False}
        return

    def criteria():
        starts = np.array([c.starts[j] for c, j in zip(lv.candidates, lv.selected)])
        finishes = np.array([c.finishes[j] for c, j in zip(lv.candidates, lv.selected)])
        metric = lv._metrics(lv.load, starts, finishes, lv.worker_load)
        return np.array([metric.load_std, metric.peak_load, metric.overload_hours,
                         metric.days_out_of_window, metric.overtime_cost,
                         lv.peak_profile.value(),
                         sum(np.count_nonzero(v) for v in lv.outages.values())]), metric.objective_value

    accepted = False
    # Do not duplicate an expensive construction without enough remaining time.
    # Related components have additional CPM state and remain indivisible here.
    if deadline - clock() > elapsed and not lv.forced.any() and not getattr(lv, "related", set()):
        before, objective = criteria()
        names = ("load", "week_load", "worker_load", "worker_remaining", "worker_total",
                 "selected", "forced", "amount")
        saved = {name: getattr(lv, name).copy() for name in names}
        saved.update(workers=list(lv.workers), candidates=list(lv.candidates),
                     outages={key: value.copy() for key, value in lv.outages.items()},
                     pool_reservations=dict(lv.pool_reservations),
                     invalid_calendar=set(lv.invalid_calendar), warnings=list(lv.warnings))
        peak_day, peak_week = lv.peak_profile.day.copy(), lv.peak_profile.week.copy()
        try:
            lv.load.fill(0)
            lv.week_load.fill(0)
            lv.peak_profile.day.fill(0)
            lv.peak_profile.week.fill(0)
            lv.worker_load = reserved.copy()
            lv.worker_remaining = lv.worker_limit - reserved
            lv.worker_total = reserved.sum(axis=1)
            lv.selected.fill(-1)
            lv.workers = [-1] * len(lv.occ)
            lv.forced.fill(False)
            lv.pool_reservations = {}
            lv.candidates = original_candidates
            for value in lv.outages.values():
                value.fill(0)
            clear_scores()
            lv._construction_deadline = deadline
            lv._construct(construction_order(lv))
            if clock() < deadline:
                after, new_objective = criteria()
                accepted = (not lv.forced.any() and np.all(after <= before + 1e-8)
                            and new_objective < objective - 1e-8)
        except RefinementExpired:
            pass
        finally:
            lv._construction_deadline = None
            if not accepted:
                for name, value in saved.items():
                    setattr(lv, name, value)
                lv.peak_profile.day, lv.peak_profile.week = peak_day, peak_week
            clear_scores()
    improve(lv, order, deadline, clock)
    lv.peak_search_stats["alternative_accepted"] = bool(accepted)


def _quality(lv, indices):
    """Métricas afectadas; ningún escalar de factibilidad se canjea por el pico.

    La comparación por rol es más conservadora que comparar sólo el promedio
    global. Usa las mismas unidades y períodos que OptimizeMetrics.
    """
    roles = sorted({int(lv.role[i]) for i in indices})
    skills = sorted({int(lv.peak_profile.role[i]) for i in indices})
    members = set()
    outside, drift, early = 0, 0, 0
    locations = set()
    for i in indices:
        worker = lv.workers[i]
        members.update(worker if isinstance(worker, tuple) else (() if worker < 0 else (worker,)))
        c, choice = lv.candidates[i], lv.selected[i]
        start, finish = c.starts[choice], c.finishes[choice]
        outside += max(lv.lower[i] - start, 0) + max(finish - lv.upper[i], 0)
        drift += abs(start - lv.due[i])
        early += start - lv.lower[i]
        if lv.occ[i].requires_outage and lv.occ[i].location in lv.outages:
            locations.add(lv.occ[i].location)
    members = sorted(members)
    # Worker assignment stays fixed in this neighborhood, so these sets match.
    excess = np.maximum(lv.worker_load[members] - lv.worker_normal[members], 0)
    cost = float((np.minimum(excess, lv.worker_extra[members]) * lv.rates[members, None]).sum())
    measured = lv.week_load[roles] if lv.options.level_period == "week" else lv.load[roles]
    variance = np.var(measured, axis=1)
    peaks = lv.peak_profile.measured[skills].max(axis=1, initial=0)
    outages = sum(np.count_nonzero(lv.outages[loc]) for loc in locations)
    w = lv.weights
    objective = (w.level * variance.sum() + w.peak * peaks.sum()
                 + w.window * (drift + 1000 * outside) + w.makespan * early
                 + w.cost * cost + 8.0 * outages)
    return np.r_[variance, lv.load[roles].max(axis=1, initial=0), peaks,
                 excess.sum(), cost, outside, outages], float(objective)


def _feasible(lv, i, choice, worker):
    days, amount = lv.candidates[i].days[choice], lv.amount[i]
    if np.any(lv.load[lv.role[i], days] + amount > lv.limit[lv.role[i], days] + 1e-8):
        return False
    crew = worker if isinstance(worker, tuple) else (() if worker < 0 else (worker,))
    return (not amount or bool(crew)) and all(
        np.all(lv.worker_remaining[member, days] + 1e-8 >= amount / len(crew))
        for member in crew)


def _attempt(lv, changes, deadline, clock):
    """Deltas sólo en las celdas afectadas; el plan no se toca para rechazar.

    El esfuerzo por rol es constante: Δvarianza = Δsum(carga²)/N. Evaluar el
    histograma entero para cada pareja hacía cuadrático el costo en horizonte.
    """
    from collections import defaultdict

    if clock() >= deadline:
        return False
    roles, skills, workers, outages = (defaultdict(lambda: defaultdict(float)) for _ in range(4))
    outside, drift, early = 0, 0, 0
    previous = {i: int(lv.selected[i]) for i in changes}
    for i, choice in changes.items():
        c, o, hh, worker = lv.candidates[i], lv.occ[i], lv.amount[i], lv.workers[i]
        crew = worker if isinstance(worker, tuple) else (() if worker < 0 else (worker,))
        if hh and not crew:
            return False
        for placement, sign in ((previous[i], -1), (choice, 1)):
            start, finish = c.starts[placement], c.finishes[placement]
            outside += sign * (max(lv.lower[i] - start, 0) + max(finish - lv.upper[i], 0))
            drift += sign * abs(start - lv.due[i])
            early += sign * start
            if o.requires_outage and o.location in lv.outages:
                outages[o.location][start] += sign
            for day in c.days[placement]:
                roles[lv.role[i]][day] += sign * hh
                skills[lv.peak_profile.role[i]][day] += sign * hh
                for member in crew:
                    workers[member][day] += sign * hh / len(crew)
    if outside > 0:
        return False

    def vectors(entries, weekly=False):
        if weekly:
            grouped = defaultdict(float)
            for day, amount in entries.items():
                grouped[lv.week[day]] += amount
            entries = grouped
        return np.fromiter(entries, dtype=int), np.fromiter(entries.values(), dtype=float)

    w, objective = lv.weights, 0.0
    weekly = lv.options.level_period == "week"
    for role, entries in roles.items():
        days, delta = vectors(entries)
        new = lv.load[role, days] + delta
        if (np.any(new > lv.limit[role, days] + 1e-8)
                or new.max(initial=0) > lv.load[role].max(initial=0) + 1e-8):
            return False
        periods, amount = vectors(entries, weekly)
        row = lv.week_load[role] if weekly else lv.load[role]
        variance_delta = float(np.sum(2 * row[periods] * amount + amount * amount)) / len(row)
        if variance_delta > 1e-8:
            return False
        objective += w.level * variance_delta
    for skill, entries in skills.items():
        periods, amount = vectors(entries, weekly)
        row = lv.peak_profile.measured[skill]
        candidate = row.copy()
        candidate[periods] += amount
        peak_delta = candidate.max(initial=0) - row.max(initial=0)
        if peak_delta > 1e-8:
            return False
        objective += w.peak * peak_delta
    overload, cost = 0.0, 0.0
    for member, entries in workers.items():
        days, delta = vectors(entries)
        old = lv.worker_load[member, days]
        new = old + delta
        if np.any(new > lv.worker_limit[member, days] + 1e-8):
            return False
        before = np.maximum(old - lv.worker_normal[member, days], 0)
        after = np.maximum(new - lv.worker_normal[member, days], 0)
        overload += float((after - before).sum())
        cost += float((np.minimum(after, lv.worker_extra[member, days])
                       - np.minimum(before, lv.worker_extra[member, days])).sum()) * lv.rates[member]
    if overload > 1e-8 or cost > 1e-8:
        return False
    outage_delta = 0
    for location, entries in outages.items():
        days, delta = vectors(entries)
        old = lv.outages[location][days]
        outage_delta += int(np.count_nonzero(old + delta) - np.count_nonzero(old))
    if outage_delta > 0:
        return False
    objective += w.window * (drift + 1000 * outside) + w.makespan * early + w.cost * cost + 8 * outage_delta
    if objective >= -1e-8 or clock() >= deadline:
        return False
    # All constraints evaluated on the combined delta, including shared workers.
    for i, old in previous.items():
        lv._update(i, old, lv.workers[i], -1)
    for i, choice in changes.items():
        lv.selected[i] = choice
        lv._update(i, choice, lv.workers[i], 1)
    return True


def improve(leveler, order, deadline, clock):
    """Desplazamientos y swaps acotados, sin cambiar equipos ni cuadrillas.

    Los componentes con precedencias, lotes múltiples y demanda implícita no se
    separan. La construcción común sigue encargada de esos contratos.
    """
    from collections import Counter, defaultdict

    lv = leveler
    stats = lv.peak_search_stats = {"moves": 0, "swaps": 0, "deadline_reached": False}
    if clock() >= deadline:
        stats["deadline_reached"] = True
        return
    groups = Counter((o.periodic_task_id, o.occurrence_index, o.team_id)
                     for o in lv.occ if o.batch_index is not None)
    eligible = [i for i in order if not lv.forced[i] and i not in getattr(lv, "implicit", {})
                and i not in getattr(lv, "related", set())
                and (lv.occ[i].batch_index is None or groups[(lv.occ[i].periodic_task_id,
                     lv.occ[i].occurrence_index, lv.occ[i].team_id)] == 1)
                and (isinstance(lv.workers[i], tuple) or lv.workers[i] >= 0)]
    # Fixed work cap avoids spending every second on tiny or already balanced plans.
    visits = min(len(eligible) * 2, max(1, int(lv.options.time_limit_s * 20)))
    moved, swapped = 0, 0
    for _ in range(2):
        changed = 0
        # Bounded buckets: no quadratic enumeration of pairs in massive plans.
        buckets = defaultdict(list)
        for i in eligible:
            key = (int(lv.role[i]), int(lv.candidates[i].starts[lv.selected[i]]))
            if len(buckets[key]) < 4:
                buckets[key].append(i)
        for i in eligible:
            if visits <= 0 or clock() >= deadline:
                stats["deadline_reached"] = clock() >= deadline
                return
            visits -= 1
            c, old, worker = lv.candidates[i], int(lv.selected[i]), lv.workers[i]
            lv._update(i, old, worker, -1)
            try:
                scores = lv._score(i, c)
                # Check just the best few starts. Swaps can free occupied capacity.
                targets = np.argsort(scores, kind="stable")[:4]
            finally:
                lv._update(i, old, worker, 1)
            for target in targets:
                target = int(target)
                if target == old:
                    continue
                if clock() >= deadline:
                    stats["deadline_reached"] = True
                    return
                if _attempt(lv, {i: target}, deadline, clock):
                    moved += 1
                    stats["moves"] = moved
                    changed += 1
                    break
                for j in buckets.get((int(lv.role[i]), int(c.starts[target])), []):
                    if i == j or clock() >= deadline:
                        continue
                    other = lv.candidates[j]
                    match = int(np.searchsorted(other.starts, c.starts[old]))
                    if match >= len(other.starts) or other.starts[match] != c.starts[old]:
                        continue
                    if _attempt(lv, {i: target, j: match}, deadline, clock):
                        swapped += 1
                        stats["swaps"] = swapped
                        changed += 1
                        break
                else:
                    continue
                break
        if not changed:
            break
