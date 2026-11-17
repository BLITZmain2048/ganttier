"""Nivelador determinista: perfiles por rol y evaluación vectorial de ventanas."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
from time import perf_counter

import numpy as np

# Costo (en días equivalentes de desviación) de abrir una desconexión adicional en una instalación.
OUTAGE_PENALTY = 8.0

from ganttier.domain import (LoadProfile, LoadSeries, ObjectiveWeights, Occurrence,
                            OptimizeMetrics, OptimizeOptions, OptimizeResult, Resource)
from ._calendar import WorkCalendar, calendar_hours


@dataclass
class _Candidates:
    days: np.ndarray
    starts: np.ndarray
    finishes: np.ndarray
    weeks: np.ndarray
    week_counts: np.ndarray
    static_scores: dict[tuple[int, int, int], np.ndarray] = field(default_factory=dict)


def _role(o: Occurrence) -> tuple[str, int | str]:
    return ("resource", o.resource_id) if o.resource_id is not None else ("skill", o.required_skill)


def crew_assignment(result: OptimizeResult) -> dict[int, list[int]]:
    """Cuadrilla completa por clave de ocurrencia (líder primero).

    El mapa también está en ``result.crew_assignment``. No es un campo de la
    dataclass: los integradores deben serializarlo explícitamente antes de
    persistir. Las HH se dividen por igual entre los miembros; un recurso
    agregado conserva todas las HH en una única asignación.
    """
    return {key: list(members) for key, members in getattr(
        result, "crew_assignment", {k: [v] for k, v in result.worker_assignment.items()}).items()}


class _Leveler:
    def __init__(self, occurrences: list[Occurrence], resources: list[Resource],
                 calendars: dict[int, WorkCalendar], weights: ObjectiveWeights,
                 options: OptimizeOptions, started: float, *, date_constraints=None,
                 horizon_start=None, horizon_end=None, reserved_load=None) -> None:
        self.occ = occurrences
        self.requested_weights = weights
        self._construction_deadline = None
        self.options, self.weights, self.started = options, weights.normalized(), started
        self.date_constraints = date_constraints or {}
        self.warnings: list[str] = []
        self.keys = [o.activity_id if o.activity_id is not None else i for i, o in enumerate(occurrences)]
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("Las claves de actividad y los índices de ocurrencia se superponen.")
        if not calendars:
            raise ValueError("Se necesita al menos un calendario laboral.")
        self.default_cid = min(calendars)
        self.origin = min(min(o.window_start, o.due_date).toordinal() for o in occurrences)
        self.end = max(max(o.window_end, o.due_date).toordinal() for o in occurrences)
        self.schedule_start = self.origin
        self.schedule_end = self.end
        self.clamp_start, self.clamp_end = horizon_start is not None, horizon_end is not None
        if horizon_start is not None:
            self.schedule_start = horizon_start.toordinal()
            self.origin = min(self.origin, self.schedule_start)
        if horizon_end is not None:
            self.schedule_end = horizon_end.toordinal()
            self.end = max(self.end, self.schedule_end)
        self.origin = min(self.origin, min((o.planned_start or o.due_date).toordinal() for o in occurrences))
        self.end = max(self.end, max((o.planned_finish or o.planned_start or o.due_date).toordinal() for o in occurrences))
        # Reference effort may finish after its last due date/window. Extend the
        # display horizon, without expanding the scheduling horizon itself.
        reference_runs = {(self._cid(o.calendar_id), o.planned_start or o.due_date, o.duration_days)
                          for o in occurrences if o.duration_days > 1}
        reference_warning = False
        for cid, due, duration in reference_runs:
            if cid not in calendars:
                raise ValueError(f"No existe el calendario {cid}.")
            try:
                second = calendars[cid].next_workday(due, include=False)
                finish = calendars[cid].add_workdays(second, duration - 1)
                self.end = max(self.end, finish.toordinal())
            except (ValueError, OverflowError):
                if not reference_warning:
                    self.warnings.append("El calendario no cubre el fin de una referencia de varios días; las HH restantes se muestran en la fecha objetivo.")
                    reference_warning = True
        # A window can contain only holidays, or be shorter than the duration.
        # Include the nearest real intervals for the provisional-date fallback.
        windows = {(self._cid(o.calendar_id), o.window_start, o.window_end, max(1, o.duration_days))
                   for o in occurrences}
        for cid, lower, upper, duration in windows:
            if cid not in calendars:
                raise ValueError(f"No existe el calendario {cid}.")
            calendar = calendars[cid]
            try:
                first = calendar.next_workday(lower)
                last = calendar.add_workdays(first, duration)
                if last <= upper:
                    continue
                self.end = max(self.end, last.toordinal())
            except (ValueError, OverflowError):
                pass
            try:
                first = calendar.prev_workday(upper)
                for _ in range(duration - 1):
                    first = calendar.prev_workday(first, include=False)
                self.origin = min(self.origin, first.toordinal())
            except (ValueError, OverflowError):
                pass
        self.size = self.end - self.origin + 1
        self.dates = [date.fromordinal(self.origin + i) for i in range(self.size)]
        self.week = (np.arange(self.size) + self.dates[0].weekday()) // 7
        self.nweeks = int(self.week[-1]) + 1
        self.week_labels = [date.fromordinal(self.origin - self.dates[0].weekday() + 7 * i).isoformat()
                            for i in range(self.nweeks)]
        self.hours: dict[int, np.ndarray] = {}
        self.workdays: dict[int, np.ndarray] = {}
        used = {self._cid(o.calendar_id) for o in occurrences}
        used.update(self._cid(r.calendar_id) for r in resources if r.active)
        for cid in used:
            if cid not in calendars:
                raise ValueError(f"No existe el calendario {cid}.")
            hours = calendar_hours(calendars[cid], self.origin, self.end)
            self.hours[cid] = hours
            self.workdays[cid] = np.flatnonzero(hours > 0)
        self.roles = list(dict.fromkeys(_role(o) for o in occurrences))
        role_ids = {role: i for i, role in enumerate(self.roles)}
        self.role = np.array([role_ids[_role(o)] for o in occurrences])
        self.resources = [r for r in resources if r.active]
        ids = [r.id for r in resources]
        if len(set(ids)) != len(ids):
            raise ValueError("Los identificadores de los recursos deben ser únicos.")
        self.resource_ids = {r.id: i for i, r in enumerate(self.resources)}
        nr = len(self.resources)
        self.worker_normal = np.zeros((nr, self.size))
        self.worker_extra = np.zeros_like(self.worker_normal)
        self.rates = np.zeros(nr)
        for ri, resource in enumerate(self.resources):
            vals = (resource.capacity_hours_day, resource.max_units, resource.max_overtime_hours_day,
                    resource.cost_hour, resource.overtime_cost_hour)
            if any(not math.isfinite(v) or v < 0 for v in vals):
                raise ValueError(f"{resource.name}: capacidad o costo inválido.")
            day_hours = self.hours[self._cid(resource.calendar_id)]
            working = day_hours > 0
            self.worker_normal[ri] = np.minimum(day_hours, resource.capacity_hours_day) * resource.max_units
            if options.allow_overtime:
                self.worker_extra[ri] = working * resource.max_overtime_hours_day * resource.max_units
            self.rates[ri] = resource.overtime_cost_hour
        self.worker_limit = self.worker_normal + self.worker_extra
        self.worker_remaining = self.worker_limit.copy()
        self.worker_load = np.zeros_like(self.worker_normal)
        self.worker_total = np.zeros(nr)
        for rid, entries in (reserved_load or {}).items():
            if rid not in self.resource_ids:
                continue
            ri = self.resource_ids[rid]
            for day, amount in entries.items():
                index = day.toordinal() - self.origin
                if 0 <= index < self.size:
                    self.worker_remaining[ri, index] -= amount
                    self.worker_load[ri, index] += amount
                    self.worker_total[ri] += amount
        self.member_cache: dict[tuple, np.ndarray] = {}
        self.pool_reservations: dict[int, list[tuple[int, int, float]]] = {}
        self.members: list[np.ndarray] = []
        for kind, value in self.roles:
            if kind == "resource":
                rid = int(value)
                members = [self.resource_ids[rid]] if rid in self.resource_ids else []
            else:
                members = [i for i, r in enumerate(self.resources) if not value or value in r.skills]
            members = [i for i in members if self.resources[i].kind in {"trabajador", "cuadrilla"}]
            self.members.append(np.array(members, dtype=int))
        self.eligible = [m if role[0] == "resource" else
                         np.array([i for i in m if self.resources[i].kind in {"trabajador", "cuadrilla"}], dtype=int)
                         for role, m in zip(self.roles, self.members)]
        self.normal = np.array([self.worker_normal[m].sum(axis=0) for m in self.members])
        self.extra = np.array([self.worker_extra[m].sum(axis=0) for m in self.members])
        self.limit = self.normal + self.extra
        self.rate = np.zeros_like(self.normal)
        for ri, m in enumerate(self.members):
            weighted = (self.worker_extra[m] * self.rates[m, None]).sum(axis=0)
            np.divide(weighted, self.extra[ri], out=self.rate[ri], where=self.extra[ri] > 0)
        self.load = np.zeros_like(self.normal)
        self.week_load = np.zeros((len(self.roles), self.nweeks))
        self.outages: dict[str, np.ndarray] = {}
        self.cache: dict[tuple, _Candidates] = {}
        self.candidates: list[_Candidates] = []
        self.due = np.array([o.due_date.toordinal() - self.origin for o in occurrences])
        self.lower = np.array([o.window_start.toordinal() - self.origin for o in occurrences])
        self.upper = np.array([o.window_end.toordinal() - self.origin for o in occurrences])
        self.amount = np.array([o.work_hours / max(1, o.duration_days) for o in occurrences])
        self.selected = np.full(len(occurrences), -1, dtype=int)
        # A single resource keeps the fast integer path. Split crews use tuples.
        self.workers: list[int | tuple[int, ...]] = [-1] * len(occurrences)
        self.forced = np.zeros(len(occurrences), dtype=bool)
        self.invalid_calendar: set[int] = set()
        self.peak_profile = None
        if self.weights.peak:
            from ._peak import PeakProfile
            self.peak_profile = PeakProfile(self)
        for i, o in enumerate(occurrences):
            self.candidates.append(self._candidates(i, False))
            if options.group_outages and o.requires_outage and o.location:
                self.outages.setdefault(o.location, np.zeros(self.size, dtype=int))

    def _cid(self, cid: int | None) -> int:
        return self.default_cid if cid is None else cid

    def _members(self, i: int) -> np.ndarray:
        """Una asignación fija tampoco exime de habilidad ni tamaño de cuadrilla."""
        o = self.occ[i]
        concrete = self.options.assign_workers or o.resource_id is not None
        key = (int(self.role[i]), o.required_skill, o.crew_size, concrete)
        if key not in self.member_cache:
            source = self.eligible[self.role[i]] if concrete else self.members[self.role[i]]
            self.member_cache[key] = np.array([
                j for j in source
                if (not o.required_skill or o.required_skill in self.resources[j].skills)
                and (not concrete or o.crew_size <= self.resources[j].max_units)
            ], dtype=int)
        return self.member_cache[key]

    def _split_members(self, i: int) -> tuple[np.ndarray, int]:
        o = self.occ[i]
        key = (int(self.role[i]), o.required_skill, "split")
        if key not in self.member_cache:
            self.member_cache[key] = np.array([
                j for j in self.eligible[self.role[i]]
                if self.resources[j].max_units >= 1
                and (not o.required_skill or o.required_skill in self.resources[j].skills)
            ], dtype=int)
        return self.member_cache[key], max(1, math.ceil(o.crew_size))

    def _pool_feasible(self, i: int, days: np.ndarray) -> np.ndarray:
        members = self._members(i)
        remaining = self.worker_remaining[members[:, None, None], days[None, :, :]]
        units = np.array([self.resources[j].max_units for j in members])
        available_units = ((remaining > 1e-8) * units[:, None, None]).sum(axis=0)
        return np.all((remaining.sum(axis=0) + 1e-8 >= self.amount[i])
                      & (available_units + 1e-8 >= self.occ[i].crew_size), axis=1)

    def _reserve_pool(self, i: int, days: np.ndarray, sign: int) -> None:
        if sign < 0:
            for worker, day, hh in self.pool_reservations.pop(i):
                self.worker_load[worker, day] -= hh
                self.worker_remaining[worker, day] += hh
                self.worker_total[worker] -= hh
            return
        reservations = []
        members = self._members(i)
        for day in days:
            left = self.amount[i]
            for worker in members[np.argsort(self.worker_total[members], kind="stable")]:
                hh = min(left, self.worker_remaining[worker, day])
                if hh > 0:
                    self.worker_load[worker, day] += hh
                    self.worker_remaining[worker, day] -= hh
                    self.worker_total[worker] += hh
                    reservations.append((int(worker), int(day), float(hh)))
                    left -= hh
                if left <= 1e-8:
                    break
            # Provisional overload is explicit, never silently discarded.
            available = members[self.worker_limit[members, day] > 0]
            if left > 1e-8 and len(available):
                worker = int(available[np.argmin(self.worker_load[available, day])])
                self.worker_load[worker, day] += left
                self.worker_remaining[worker, day] -= left
                self.worker_total[worker] += left
                reservations.append((worker, int(day), float(left)))
        self.pool_reservations[i] = reservations

    def _candidates(self, i: int, expanded: bool) -> _Candidates:
        o = self.occ[i]
        lower, upper = (0, self.size - 1) if expanded else (int(self.lower[i]), int(self.upper[i]))
        # Sólo un horizonte explícito (fecha de corte / horizonte de «Generar programa») recorta los
        # candidatos; sin él, una ventana blanda vacía (p. ej. fin de semana) puede usar el laborable
        # más cercano aunque quede antes de la primera ventana.
        if self.clamp_start:
            lower = max(lower, self.schedule_start - self.origin)
        if self.clamp_end:
            upper = min(upper, self.schedule_end - self.origin)
        cid, duration = self._cid(o.calendar_id), max(1, o.duration_days)
        constraint = self.date_constraints.get(self.keys[i], ('ASAP', None))
        key = (cid, duration, lower, upper, constraint, o.batch_continuous_in_shift)
        if key in self.cache:
            return self.cache[key]
        workdays = self.workdays[cid]
        lo = int(np.searchsorted(workdays, lower))
        hi = int(np.searchsorted(workdays, upper, side="right")) - duration + 1
        positions = np.arange(lo, max(lo, hi))
        days = workdays[positions[:, None] + np.arange(duration)]
        if o.batch_index is not None and o.batch_continuous_in_shift and len(days):
            continuous = days[:, -1] - days[:, 0] == duration - 1
            days, positions = days[continuous], positions[continuous]
        kind, when = constraint
        if when is not None and len(days):
            target = when.toordinal() - self.origin
            mask = {'MSO': days[:, 0] == target, 'MFO': days[:, -1] == target,
                    'SNET': days[:, 0] >= target, 'FNET': days[:, -1] >= target}.get(kind)
            if mask is not None:
                days, positions = days[mask], positions[mask]
        weeks = self.week[days]
        # Exact delta of weekly squared loads, including multiple days in one week.
        work_weeks = self.week[workdays]
        week_lo = np.searchsorted(work_weeks, weeks, side="left")
        week_hi = np.searchsorted(work_weeks, weeks, side="right")
        counts = np.minimum(positions[:, None] + duration, week_hi) - np.maximum(positions[:, None], week_lo)
        candidates = _Candidates(days, days[:, 0], days[:, -1], weeks, counts)
        # Keep recurring templates only; bound cache memory on heterogeneous imports.
        if len(self.cache) < 20000:
            self.cache[key] = candidates
        return candidates

    def _cost(self, load: np.ndarray, role: int, days: np.ndarray) -> np.ndarray:
        overtime = np.minimum(np.maximum(load - self.normal[role, days], 0), self.extra[role, days])
        return overtime * self.rate[role, days]

    def _score(self, i: int, candidates: _Candidates, pooled_cost: bool = True) -> np.ndarray:
        role, hh = self.role[i], self.amount[i]
        w = self.weights
        days = candidates.days
        old = self.load[role, days]
        score = np.zeros(len(days))
        if w.level:
            if self.options.level_period == "week":
                score += w.level * (2 * hh * self.week_load[role, candidates.weeks]
                                    + hh * hh * candidates.week_counts).sum(axis=1) / self.nweeks
            else:
                score += w.level * (2 * hh * old + hh * hh).sum(axis=1) / self.size
        if w.peak:
            score += w.peak * self.peak_profile.score(i, candidates, hh)
        outside = np.maximum(self.lower[i] - candidates.starts, 0) + np.maximum(candidates.finishes - self.upper[i], 0)
        if w.window:
            score += w.window * (np.abs(candidates.starts - self.due[i]) + 1000 * outside)
        if w.makespan:
            score += w.makespan * (candidates.starts - self.lower[i])
        if w.cost and pooled_cost:
            score += w.cost * (self._cost(old + hh, role, days) - self._cost(old, role, days)).sum(axis=1)
        # Capacity violations remain expensive even when cost has zero weight.
        if not self.options.respect_resource_limits:
            excess = np.maximum(old + hh - self.limit[role, days], 0)
            previous = np.maximum(old - self.limit[role, days], 0)
            score += 10000 * (excess + excess * excess - previous - previous * previous).sum(axis=1)
        o = self.occ[i]
        if o.requires_outage and o.location in self.outages:
            # Abrir una desconexión adicional cuesta OUTAGE_PENALTY (≈ días de desviación): en transmisión
            # cada desconexión exige solicitud al Coordinador, maniobras y pérdida de disponibilidad, por
            # lo que dentro de la tolerancia conviene agrupar trabajos. Un grupo no recibe bonos sin límite.
            score += OUTAGE_PENALTY * (self.outages[o.location][candidates.starts] == 0).astype(float)
        if self.options.respect_resource_limits:
            score[np.any(old + hh > self.limit[role, days] + 1e-8, axis=1)] = np.inf
        return score

    def _choose(self, i: int, candidates: _Candidates) -> tuple[int, int | tuple[int, ...]]:
        if not len(candidates.starts):
            return -1, -1
        o = self.occ[i]
        if (self.options.assign_workers and o.resource_id is None
                and self.amount[i] > 0 and not len(self._members(i))):
            return self._choose_crew(i, candidates)
        if self.occ[i].duration_days <= 1 and not self.weights.cost:
            choice = self._choose_single(i, candidates)
            if choice[0] < 0 and self.options.assign_workers and o.resource_id is None and o.crew_size > 1:
                return self._choose_crew(i, candidates)
            return choice
        o, hh = self.occ[i], self.amount[i]
        assign = self.options.assign_workers or o.resource_id is not None
        score = self._score(i, candidates, pooled_cost=not (assign and hh > 0))
        if assign and hh > 0:
            members = self._members(i)
            if not len(members):
                return -1, -1
            # All candidate starts and all eligible workers in one NumPy operation.
            days = candidates.days
            available = self.worker_limit[members[:, None, None], days[None, :, :]]
            assigned = self.worker_load[members[:, None, None], days[None, :, :]]
            feasible = np.all(assigned + hh <= available + 1e-8, axis=2)
            worker_cost = None
            if self.weights.cost:
                normal = self.worker_normal[members[:, None, None], days[None, :, :]]
                worker_cost = ((np.maximum(assigned + hh - normal, 0) - np.maximum(assigned - normal, 0))
                               * self.rates[members, None, None]).sum(axis=2)
                worker_cost[~feasible] = np.inf
                score += self.weights.cost * worker_cost.min(axis=0)
            score[~np.any(feasible, axis=0)] = np.inf
            best = int(np.argmin(score))
            if not np.isfinite(score[best]):
                if self.options.assign_workers and o.resource_id is None and o.crew_size > 1:
                    return self._choose_crew(i, candidates)
                return -1, -1
            valid = members[feasible[:, best]]
            if worker_cost is not None:
                valid = members[feasible[:, best] & (worker_cost[:, best] == worker_cost[:, best].min())]
            worker = int(valid[np.argmin(self.worker_total[valid])])
            return best, worker
        if hh > 0 and self.options.respect_resource_limits:
            score[~self._pool_feasible(i, candidates.days)] = np.inf
        best = int(np.argmin(score))
        return (best, -1) if np.isfinite(score[best]) else (-1, -1)

    def _choose_crew(self, i: int, c: _Candidates) -> tuple[int, int | tuple[int, ...]]:
        members, count = self._split_members(i)
        if len(members) < count:
            return -1, -1
        share = self.amount[i] / count
        single = self.occ[i].duration_days <= 1
        if single:
            score = self._single_score(i, c)
            best = int(score.argmin())
            if np.isfinite(score[best]):
                day = c.starts[best]
                ordered = members[np.argsort(self.worker_total[members], kind="stable")]
                crew = []
                for member in ordered:
                    left = self.worker_remaining[member, day]
                    if left + 1e-8 < share:
                        continue
                    if (self.weights.cost and self.rates[member]
                            and left - self.worker_extra[member, day] + 1e-8 < share):
                        continue
                    crew.append(int(member))
                    if len(crew) == count:
                        # All overtime increments are nonnegative. A zero-cost
                        # crew at the minimum base score is already optimal.
                        # Usually only `count` workers need scalar inspection.
                        return best, tuple(crew)
            remaining = self.worker_remaining[members[:, None], c.starts]
            feasible = remaining + 1e-8 >= share
        else:
            remaining = self.worker_remaining[members[:, None, None], c.days[None, :, :]]
            feasible = np.all(remaining + 1e-8 >= share, axis=2)
            score = self._score(i, c, pooled_cost=False)
        costs = np.zeros(feasible.shape)
        if self.weights.cost:
            if single:
                normal_remaining = remaining - self.worker_extra[members[:, None], c.starts]
                costs = (np.maximum(share - normal_remaining, 0) - np.maximum(-normal_remaining, 0)) * self.rates[members, None]
            else:
                normal_remaining = remaining - self.worker_extra[members[:, None, None], c.days[None, :, :]]
                costs = ((np.maximum(share - normal_remaining, 0) - np.maximum(-normal_remaining, 0))
                         * self.rates[members, None, None]).sum(axis=2)
        costs[~feasible] = np.inf
        if self.weights.cost:
            cheapest = np.partition(costs, count - 1, axis=0)[:count].sum(axis=0)
            score += self.weights.cost * cheapest
        score[feasible.sum(axis=0) < count] = np.inf
        best = int(score.argmin())
        if not np.isfinite(score[best]):
            return -1, -1
        rank = np.lexsort((members, self.worker_total[members], costs[:, best]))
        return best, tuple(int(j) for j in members[rank[:count]])

    def _single_score(self, i: int, c: _Candidates) -> np.ndarray:
        """Caso masivo de un día: vectores 1D y costos estáticos reutilizados."""
        role, hh, w = self.role[i], self.amount[i], self.weights
        key = (int(self.due[i]), int(self.lower[i]), int(self.upper[i]))
        static = c.static_scores.get(key)
        if static is None:
            outside = np.maximum(self.lower[i] - c.starts, 0) + np.maximum(c.finishes - self.upper[i], 0)
            static = (w.window * (np.abs(c.starts - self.due[i]) + 1000 * outside)
                      + w.makespan * (c.starts - self.lower[i]))
            c.static_scores[key] = static
        old = self.load[role, c.starts]
        if self.options.level_period == "week":
            score = static + (2 * hh * w.level / self.nweeks) * self.week_load[role, c.weeks[:, 0]]
        else:
            score = static + (2 * hh * w.level / self.size) * old
        if w.peak:
            score += w.peak * self.peak_profile.score(i, c, hh)
        if self.options.respect_resource_limits:
            score[old + hh > self.limit[role, c.starts] + 1e-8] = np.inf
        else:
            excess = np.maximum(old + hh - self.limit[role, c.starts], 0)
            previous = np.maximum(old - self.limit[role, c.starts], 0)
            score += 10000 * (excess + excess * excess - previous - previous * previous)
        o = self.occ[i]
        if o.requires_outage and o.location in self.outages:
            score += OUTAGE_PENALTY * (self.outages[o.location][c.starts] == 0)
        return score

    def _choose_single(self, i: int, c: _Candidates) -> tuple[int, int]:
        score = self._single_score(i, c)
        hh, o = self.amount[i], self.occ[i]
        if hh > 0 and (self.options.assign_workers or o.resource_id is not None):
            members = self._members(i)
            if not len(members):
                return -1, -1
            # Most windows have a free worker at the best date. Avoid allocating
            # workers × dates unless that date is actually full; ties unchanged.
            best = int(score.argmin())
            if math.isfinite(float(score[best])):
                valid = members[self.worker_remaining[members, c.starts[best]] + 1e-8 >= hh]
                if len(valid):
                    return best, int(valid[self.worker_total[valid].argmin()])
            feasible = self.worker_remaining[members[:, None], c.starts] + 1e-8 >= hh
            score[~feasible.any(axis=0)] = np.inf
            best = int(score.argmin())
            if not math.isfinite(float(score[best])):
                return -1, -1
            valid = members[feasible[:, best]]
            return best, int(valid[self.worker_total[valid].argmin()])
        if hh > 0 and self.options.respect_resource_limits:
            score[~self._pool_feasible(i, c.days)] = np.inf
        best = int(score.argmin())
        return (best, -1) if math.isfinite(float(score[best])) else (-1, -1)

    def _fallback(self, i: int) -> tuple[int, int | tuple[int, ...]]:
        """Fecha provisional: ventana, mínima sobrecarga, cercanía al objetivo.

        Solo se amplía si el calendario no ofrece un intervalo en ventana.
        Una cuadrilla incompleta o no disponible jamás se publica como válida.
        """
        c = self.candidates[i]
        if not len(c.starts):
            c = self._candidates(i, True)
            if len(c.starts):
                # Keep improvements in the nearest window tier as well. A
                # monetary overtime tariff cannot buy days of window drift.
                outside = np.maximum(self.lower[i] - c.starts, 0) + np.maximum(c.finishes - self.upper[i], 0)
                keep = outside == outside.min()
                c = _Candidates(c.days[keep], c.starts[keep], c.finishes[keep],
                                c.weeks[keep], c.week_counts[keep])
            else:
                # No laborable interval exists in the scheduling horizon. Keep
                # all effort visible at a provisional date, without inventing
                # worker availability or a valid workday duration.
                day = int(np.clip(self.due[i], self.lower[i], self.upper[i]))
                days = np.array([[day]])
                c = _Candidates(days, days[:, 0], days[:, 0], self.week[days], np.ones_like(days))
                self.amount[i] = self.occ[i].work_hours
                self.invalid_calendar.add(i)
            self.candidates[i] = c
        days, hh = c.days, self.amount[i]
        old = self.load[self.role[i], days]
        excess = np.maximum(old + hh - self.limit[self.role[i], days], 0)
        previous = np.maximum(old - self.limit[self.role[i], days], 0)
        score = (excess + excess ** 2 - previous - previous ** 2).sum(axis=1)
        o = self.occ[i]
        crew_by_day = None
        members = self._members(i)
        count = 1
        if hh > 0 and (self.options.assign_workers or o.resource_id is not None):
            if not len(members) and o.resource_id is None:
                members, count = self._split_members(i)
            if len(members) >= count:
                remaining = self.worker_remaining[members[:, None, None], days[None, :, :]]
                available = self.worker_normal[members[:, None, None], days[None, :, :]] > 0
                after = np.maximum(hh / count - remaining, 0)
                before = np.maximum(-remaining, 0)
                penalty = (after + after ** 2 - before - before ** 2).sum(axis=2)
                penalty[~available.all(axis=2)] = np.inf
                ranks = np.argsort(penalty, axis=0, kind="stable")[:count]
                worker_score = np.take_along_axis(penalty, ranks, axis=0).sum(axis=0)
                if np.isfinite(worker_score).any():
                    score = worker_score
                    crew_by_day = members[ranks]
        outside = np.maximum(self.lower[i] - c.starts, 0) + np.maximum(c.finishes - self.upper[i], 0)
        valid = np.isfinite(score)
        outside = np.where(valid, outside, np.iinfo(np.int64).max)
        best = int(np.lexsort((c.starts, np.abs(c.starts - self.due[i]), score, outside))[0])
        if crew_by_day is not None:
            crew = tuple(int(j) for j in crew_by_day[:, best])
            return best, crew[0] if count == 1 else crew
        return best, -1

    def _update(self, i: int, choice: int, worker: int | tuple[int, ...], sign: int) -> None:
        candidates = self.candidates[i]
        days = candidates.days[choice]
        hh = self.amount[i] * sign
        if self.peak_profile is not None:
            self.peak_profile.update(i, days, hh)
        crew = worker if isinstance(worker, tuple) else (() if worker < 0 else (worker,))
        if (not crew and self.amount[i] > 0 and self.options.respect_resource_limits
                and not (self.options.assign_workers or self.occ[i].resource_id is not None)):
            self._reserve_pool(i, days, sign)
        if isinstance(worker, tuple) and self.occ[i].duration_days > 1:
            share = hh / len(worker)
            for member in worker:
                self.worker_load[member, days] += share
                self.worker_remaining[member, days] -= share
                self.worker_total[member] += share * len(days)
            worker = -1
        if self.occ[i].duration_days <= 1:
            day = candidates.starts[choice]
            self.load[self.role[i], day] += hh
            self.week_load[self.role[i], self.week[day]] += hh
            if crew:
                share = hh / len(crew)
                for member in crew:
                    self.worker_load[member, day] += share
                    self.worker_remaining[member, day] -= share
                    self.worker_total[member] += share
            o = self.occ[i]
            if o.requires_outage and o.location in self.outages:
                self.outages[o.location][day] += sign
            return
        self.load[self.role[i], days] += hh
        np.add.at(self.week_load[self.role[i]], candidates.weeks[choice], hh)
        if worker >= 0:
            self.worker_load[worker, days] += hh
            self.worker_remaining[worker, days] -= hh
            self.worker_total[worker] += self.occ[i].work_hours * sign
        o = self.occ[i]
        if o.requires_outage and o.location in self.outages:
            self.outages[o.location][candidates.starts[choice]] += sign

    def _baseline(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        load = np.zeros_like(self.load)
        reference = np.array([(o.planned_start or o.due_date).toordinal() - self.origin for o in self.occ])
        starts, finishes = reference.copy(), reference.copy()
        # Grouped scatter avoids a NumPy dispatch per occurrence in the baseline.
        grouped: dict[tuple, list[int]] = {}
        self.reference_peak = np.zeros_like(self.peak_profile.day) if self.peak_profile is not None else None
        for i, o in enumerate(self.occ):
            skill = int(self.peak_profile.role[i]) if self.peak_profile is not None else None
            grouped.setdefault((int(self.role[i]), self._cid(o.calendar_id), max(1, o.duration_days), skill), []).append(i)
        for (role, cid, duration, skill), indices in grouped.items():
            ix = np.asarray(indices)
            days = self.workdays[cid]
            if not len(days):
                np.add.at(load[role], reference[ix], self.amount[ix] * duration)
                if skill is not None:
                    np.add.at(self.reference_peak[skill], reference[ix], self.amount[ix] * duration)
                continue
            # Compara con la programación recibida; due_date solo si no la hay.
            positions = np.searchsorted(days, reference[ix], side="right")
            for offset in range(duration):
                if offset == 0:
                    target = reference[ix]
                else:
                    valid = positions + offset - 1 < len(days)
                    target = np.where(valid, days[np.minimum(positions + offset - 1, len(days) - 1)], reference[ix])
                np.add.at(load[role], target, self.amount[ix])
                if skill is not None:
                    np.add.at(self.reference_peak[skill], target, self.amount[ix])
                finishes[ix] = np.maximum(finishes[ix], target)
        return load, starts, finishes

    def _forced_without_valid_worker(self) -> bool:
        """Forzadas sin trabajador válido o con calendario inválido (no basta con rozar el límite de capacidad)."""
        if not self.forced.any():
            return False
        invalid = set(getattr(self, "invalid_calendar", []) or [])
        for i in np.flatnonzero(self.forced):
            if int(i) in invalid:
                return True
            worker = self.workers[i] if i < len(self.workers) else -1
            if not self.options.assign_workers:
                return True
            if not isinstance(worker, tuple) and worker < 0:
                return True
        return False

    def _metrics(self, load: np.ndarray, starts: np.ndarray, finishes: np.ndarray,
                 worker_load: np.ndarray | None = None) -> OptimizeMetrics:
        selected = starts >= 0
        weekly = np.array([np.bincount(self.week, weights=row, minlength=self.nweeks) for row in load])
        measured = weekly if self.options.level_period == "week" else load
        overload = np.maximum(load - self.normal, 0)
        overload_hours = float(overload.sum())
        overtime = np.minimum(overload, self.extra)
        excess = np.maximum(load - self.limit, 0)
        cost = float((overtime * self.rate).sum())
        overtime_hours = float(overtime.sum())
        if worker_load is not None and (self.options.assign_workers or self.options.respect_resource_limits):
            # Concrete allocation gives exact overtime instead of pooled rate estimates.
            actual = np.minimum(np.maximum(worker_load - self.worker_normal, 0), self.worker_extra)
            cost, overtime_hours = float((actual * self.rates[:, None]).sum()), float(actual.sum())
            overload_hours = float(np.maximum(worker_load - self.worker_normal, 0).sum())
            # Missing skills/calendars still contribute demand to the profile.
            overload_hours += max(0.0, float(load.sum() - worker_load.sum()))
        outside = np.maximum(self.lower[selected] - starts[selected], 0) + np.maximum(finishes[selected] - self.upper[selected], 0)
        w = self.weights
        obj = w.level * float(np.var(measured, axis=1).sum())
        if w.peak:
            if load is self.load:
                obj += w.peak * self.peak_profile.value()
            else:
                # Reference demand grouped by skill, including fixed resources.
                reference = self.reference_peak
                if self.options.level_period == "week":
                    reference = np.array([np.bincount(self.week, weights=row, minlength=self.nweeks)
                                          for row in reference])
                obj += w.peak * float(reference.max(axis=1, initial=0).sum())
        obj += w.window * float((np.abs(starts[selected] - self.due[selected]) + 1000 * outside).sum())
        obj += w.makespan * float((starts[selected] - self.lower[selected]).sum())
        penalty = float(10000 * (excess + excess * excess).sum())
        obj += w.cost * cost + penalty
        if self.options.group_outages:
            obj += OUTAGE_PENALTY * len({(o.location, int(starts[i])) for i, o in enumerate(self.occ)
                        if selected[i] and o.requires_outage and o.location})
        return OptimizeMetrics(
            finish=self.dates[int(finishes[selected].max())] if np.any(selected) else None,
            peak_load=float(load.max(initial=0)), load_std=float(np.std(measured, axis=1).mean()),
            overload_hours=overload_hours, overtime_hours=overtime_hours, overtime_cost=cost,
            days_out_of_window=int(outside.sum()), activities_out_of_window=int(np.count_nonzero(outside)),
            objective_value=obj)

    def _profile(self, load: np.ndarray) -> LoadProfile:
        return LoadProfile("week", self.week_labels, [LoadSeries(
            f"resource:{role[1]}" if role[0] == "resource" else f"skill:{role[1] or 'Sin habilidad'}",
            np.bincount(self.week, weights=self.normal[i], minlength=self.nweeks).tolist(),
            np.bincount(self.week, weights=load[i], minlength=self.nweeks).tolist())
            for i, role in enumerate(self.roles)])

    def _improvement_order(self, order):
        return order

    def _construct(self, order):
        """Construye todas las fechas antes de invertir presupuesto en mejoras."""
        for i in order:
            if self._construction_deadline is not None and perf_counter() >= self._construction_deadline:
                from ._peak import RefinementExpired
                raise RefinementExpired
            choice, worker = self._choose(i, self.candidates[i])
            if choice < 0 and not self.occ[i].window_hard:
                expanded = self._candidates(i, True)
                other_choice, other_worker = self._choose(i, expanded)
                if other_choice >= 0:
                    self.candidates[i] = expanded
                    choice, worker = other_choice, other_worker
            if choice < 0:
                choice, worker = self._fallback(i)
                self.forced[i] = True
            self.selected[i], self.workers[i] = choice, worker
            self._update(i, choice, worker, 1)

    def _legacy_improve(self, order):
        # Split crews already perform an extra worker-selection search during
        # construction. Charge that deterministic work to the improvement
        # budget; a massive crew plan must not spend the whole budget twice.
        split_work = sum(isinstance(worker, tuple) for worker in self.workers)
        visits_left = min(2 * len(order), max(0, int(self.options.time_limit_s * 2000) - split_work))
        for _ in range(2):
            moved = 0
            for i in order:
                if visits_left <= 0:
                    self.warnings.append("Se agotó el presupuesto determinista de mejora local; el tiempo es un objetivo blando.")
                    break
                visits_left -= 1
                old, worker = int(self.selected[i]), self.workers[i]
                self._update(i, old, worker, -1)
                choice, new_worker = self._choose(i, self.candidates[i])
                # Deterministic tie breaking makes repeated calls reproducible.
                self.forced[i] = choice < 0
                if choice < 0:
                    choice, new_worker = self._fallback(i)
                self.selected[i], self.workers[i] = choice, new_worker
                self._update(i, choice, new_worker, 1)
                moved += choice != old
            else:
                if moved:
                    continue
                break
            break

    def run(self) -> OptimizeResult:
        before_load, before_starts, before_finishes = self._baseline()
        before = self._metrics(before_load, before_starts, before_finishes)
        order = sorted(range(len(self.occ)), key=lambda i: (
            self.upper[i] - self.lower[i], -self.occ[i].work_hours, -self.occ[i].priority, i))
        if self.weights.peak:
            from ._peak import construct_and_improve
            construct_and_improve(self, order, perf_counter)
        else:
            self._construct(order)
            self._legacy_improve(self._improvement_order(order))
        starts = np.full(len(self.occ), -1, dtype=int)
        finishes = starts.copy()
        result_starts: dict[int, date] = {}
        result_finishes: dict[int, date] = {}
        assignment: dict[int, int] = {}
        crews: dict[int, list[int]] = {}
        for i, o in enumerate(self.occ):
            choice = self.selected[i]
            c = self.candidates[i]
            starts[i], finishes[i] = c.starts[choice], c.finishes[choice]
            key = self.keys[i]
            result_starts[key], result_finishes[key] = self.dates[starts[i]], self.dates[finishes[i]]
            worker = self.workers[i]
            if isinstance(worker, tuple):
                crews[key] = [self.resources[j].id for j in worker]
                assignment[key] = crews[key][0]
            elif worker >= 0:
                assignment[key] = self.resources[worker].id
                crews[key] = [assignment[key]]
        for i in self.invalid_calendar:
            self.forced[i] = True
        forced = int(self.forced.sum())
        outside = np.maximum(self.lower - starts, 0) + np.maximum(finishes - self.upper, 0)
        hard_outside = any(o.window_hard and outside[i] for i, o in enumerate(self.occ))
        excess_hours = float(np.maximum(self.worker_load - self.worker_limit, 0).sum())
        if forced:
            self.warnings.append(f"Se conservaron todas las fechas; {forced} ocurrencias requieren revisar capacidad, dotación o calendario. "
                                 "Se usó la menor sobrecarga disponible dentro de la ventana o la fecha laborable más cercana; "
                                 f"las asignaciones que exceden capacidad son provisionales ({excess_hours:.2f} HH sobre el límite permitido).")
        if np.any(outside):
            self.warnings.append(f"{np.count_nonzero(outside)} ocurrencias tienen fechas fuera de ventana por falta de un intervalo laborable completo dentro de ella.")
        if not self.options.assign_workers and not self.options.respect_resource_limits and any(len(r.skills) > 1 for r in self.resources):
            self.warnings.append("Los perfiles por habilidad comparten recursos polivalentes; active la asignación de trabajadores para verificar su capacidad conjunta.")
        if self.options.method == "cpsat":
            self.warnings.append("Mantenimiento utiliza la heurística vectorizada; no ejecuta CP-SAT.")
        if perf_counter() - self.started > self.options.time_limit_s:
            self.warnings.append("La ejecución excedió el objetivo de tiempo; se conservó un resultado reproducible.")
        result = OptimizeResult(
            starts=result_starts, finishes=result_finishes, worker_assignment=assignment,
            before=before, after=self._metrics(self.load, starts, finishes, self.worker_load),
            profile_before=self._profile(before_load), profile_after=self._profile(self.load),
            method_used="maintenance_numpy", status="infactible" if hard_outside or excess_hours > 1e-7 or self._forced_without_valid_worker() else "heuristico",
            elapsed_s=perf_counter() - self.started, warnings=self.warnings)
        result.crew_assignment = crews
        if self.peak_profile is not None:
            result.peak_search = getattr(self, "peak_search_stats", {})
            result.peak_by_skill = {
                "period": self.options.level_period,
                "maximum": float(self.peak_profile.measured.max(initial=0)),
                "sum_of_maxima": self.peak_profile.value(),
            }
        if result.status == 'infactible':
            # Se conservan fechas PROVISIONALES (mejor esfuerzo) para diagnosticar dónde falta capacidad;
            # la orquestación (solve.py/apply_run) impide aplicarlas como programa factible.
            result.warnings.append('No se encontró un programa factible: capacidad, habilidad, dotación o ventana dura insuficiente; las fechas son provisionales y no se aplican.')
        return result


class _DemandLeveler(_Leveler):
    def __init__(self, *args, demand_units=None, **kwargs):
        super().__init__(*args, **kwargs)
        units = demand_units or {}
        self.implicit = {i: units.get(self.keys[i], o.crew_size if not o.work_hours else 0)
                         for i, o in enumerate(self.occ) if o.duration_days > 0}
        self.implicit = {i: value for i, value in self.implicit.items() if value}

    def _daily(self, i, days):
        return self.amount[i] + self.implicit[i] * self.hours[self._cid(self.occ[i].calendar_id)][days]

    def _choose(self, i, candidates):
        if i not in self.implicit:
            return super()._choose(i, candidates)
        return self._choose_daily(i, candidates)

    def _choose_daily(self, i, candidates, provisional=False, split=False):
        o, w, role = self.occ[i], self.weights, self.role[i]
        concrete = self.options.assign_workers or o.resource_id is not None
        members, count = self._members(i), 1
        if (split or not len(members)) and o.resource_id is None:
            members, count = self._split_members(i)
        best, answer = float('inf'), (-1, -1)
        for choice, days in enumerate(candidates.days):
            hh = self._daily(i, days)
            old = self.load[role, days]
            outside = max(self.lower[i] - candidates.starts[choice], 0) + max(candidates.finishes[choice] - self.upper[i], 0)
            score = w.window * (abs(candidates.starts[choice] - self.due[i]) + 1000 * outside)
            score += w.makespan * (candidates.starts[choice] - self.lower[i])
            if self.options.level_period == 'week':
                delta = np.bincount(self.week[days], weights=hh, minlength=self.nweeks)
                score += w.level * np.sum(2 * self.week_load[role] * delta + delta ** 2) / self.nweeks
            else:
                score += w.level * np.sum(2 * old * hh + hh ** 2) / self.size
            if w.peak:
                row = self.peak_profile.measured[self.peak_profile.role[i]]
                if self.options.level_period == 'week':
                    after = row + np.bincount(self.week[days], weights=hh, minlength=self.nweeks)
                else:
                    after = row.copy()
                    after[days] += hh
                score += w.peak * (after.max(initial=0) - row.max(initial=0))
            if o.requires_outage and o.location in self.outages:
                score += OUTAGE_PENALTY * (self.outages[o.location][candidates.starts[choice]] == 0)
            if not concrete:
                remaining = self.worker_remaining[members[:, None], days]
                excess = np.maximum(hh - np.maximum(remaining, 0).sum(axis=0), 0)
                units = np.array([self.resources[j].max_units for j in members])
                available_units = ((remaining > 1e-8) * units[:, None]).sum(axis=0)
                if self.options.respect_resource_limits and not provisional:
                    if np.any(excess > 1e-8) or np.any(available_units + 1e-8 < o.crew_size):
                        continue
                score += w.cost * (self._cost(old + hh, role, days) - self._cost(old, role, days)).sum()
                score += 1e6 * excess.sum()
                if score < best:
                    best, answer = score, (choice, -1)
                continue
            if len(members) < count:
                continue
            share = hh / count
            remaining = self.worker_remaining[members[:, None], days]
            available = (self.worker_normal[members[:, None], days] > 0).all(axis=1)
            excess = np.maximum(share - remaining, 0).sum(axis=1)
            if not provisional:
                available &= excess <= 1e-8
            normal = remaining - self.worker_extra[members[:, None], days]
            costs = ((np.maximum(share - normal, 0) - np.maximum(-normal, 0)) * self.rates[members, None]).sum(axis=1)
            penalty = w.cost * costs + (1e6 * excess if provisional else 0)
            penalty[~available] = np.inf
            ranks = np.lexsort((members, self.worker_total[members], penalty))[:count]
            score += penalty[ranks].sum()
            if score < best:
                crew = tuple(int(j) for j in members[ranks])
                best, answer = score, (choice, crew[0] if count == 1 else crew)
        if answer[0] < 0 and concrete and not split and o.resource_id is None and o.crew_size > 1 and count == 1:
            return self._choose_daily(i, candidates, provisional=provisional, split=True)
        return answer

    def _fallback(self, i):
        if i not in self.implicit:
            return super()._fallback(i)
        if not len(self.candidates[i].starts):
            # El respaldo base conserva una fecha diagnóstica para calendarios vacíos.
            super()._fallback(i)
        choice, worker = self._choose_daily(i, self.candidates[i], provisional=True)
        return (choice, worker) if choice >= 0 else (0, -1)

    def _update(self, i, choice, worker, sign):
        if i not in self.implicit:
            return super()._update(i, choice, worker, sign)
        c = self.candidates[i]
        days = c.days[choice]
        hh = self._daily(i, days) * sign
        if self.peak_profile is not None:
            self.peak_profile.update(i, days, hh)
        self.load[self.role[i], days] += hh
        np.add.at(self.week_load[self.role[i]], self.week[days], hh)
        crew = worker if isinstance(worker, tuple) else (() if worker < 0 else (worker,))
        if not crew and not self.options.assign_workers and self.occ[i].resource_id is None:
            if sign < 0:
                for member, day, share in self.pool_reservations.pop(i, []):
                    self.worker_load[member, day] -= share
                    self.worker_remaining[member, day] += share
                    self.worker_total[member] -= share
            else:
                reservations = []
                members = self._members(i)
                for day, amount in zip(days, hh):
                    for member in members[np.argsort(self.worker_total[members], kind='stable')]:
                        share = min(amount, max(0, self.worker_remaining[member, day]))
                        self.worker_load[member, day] += share
                        self.worker_remaining[member, day] -= share
                        self.worker_total[member] += share
                        reservations.append((int(member), int(day), float(share)))
                        amount -= share
                self.pool_reservations[i] = reservations
        for member in crew:
            self.worker_load[member, days] += hh / len(crew)
            self.worker_remaining[member, days] -= hh / len(crew)
            self.worker_total[member] += hh.sum() / len(crew)
        o = self.occ[i]
        if o.requires_outage and o.location in self.outages:
            self.outages[o.location][c.starts[choice]] += sign

    def _baseline(self):
        load, starts, finishes = super()._baseline()
        for i, units in self.implicit.items():
            cid = self._cid(self.occ[i].calendar_id)
            days = self.workdays[cid]
            days = days[(days >= starts[i]) & (days <= finishes[i])]
            load[self.role[i], days] += units * self.hours[cid][days]
            if self.reference_peak is not None:
                self.reference_peak[self.peak_profile.role[i], days] += units * self.hours[cid][days]
        return load, starts, finishes


class _NoSchedule(Exception):
    pass


def _subset(candidates, indices):
    return _Candidates(candidates.days[indices], candidates.starts[indices],
                       candidates.finishes[indices], candidates.weeks[indices],
                       candidates.week_counts[indices])




class _TeamLeveler(_DemandLeveler):
    """Empaquetado ordenado de lotes mediante DP sobre los candidatos del nivelador.

    Reutiliza calendarios, perfiles, costos y reservas de la implementación común.
    La búsqueda por grupo es exacta para su orden de lotes y carga ya reservada;
    no certifica factibilidad global entre grupos o precedencias.
    """
    def _improvement_order(self, order):
        return [i for i in super()._improvement_order(order) if self.occ[i].batch_index is None]

    def _batch_score(self, i, candidates, member, cache):
        """La carga no cambia hasta ubicar todo el grupo: reutilizar puntajes idénticos."""
        o = self.occ[i]
        key = (id(candidates), self.role[i], self.amount[i], self.due[i], self.lower[i],
               self.upper[i], o.requires_outage, o.location)
        if key not in cache:
            scores = self._score(i, candidates).copy()
            scores -= self.weights.window * np.abs(candidates.starts-self.due[i])
            scores -= self.weights.makespan * (candidates.starts-self.lower[i])
            available = self.worker_limit[member, candidates.days]-self.worker_load[member, candidates.days]
            scores[np.any(available+1e-8 < self.amount[i], axis=1)] = np.inf
            cache[key] = scores
        return cache[key].copy()

    def _construct(self, order):
        from collections import defaultdict
        ordinary, groups = [], defaultdict(list)
        for i in order:
            o = self.occ[i]
            if o.batch_index is None:
                ordinary.append(i)
            else:
                groups[(o.periodic_task_id, o.occurrence_index, o.team_id)].append(i)
        super()._construct(ordinary)
        for indices in groups.values():
            if self._construction_deadline is not None and perf_counter() >= self._construction_deadline:
                from ._peak import RefinementExpired
                raise RefinementExpired
            indices.sort(key=lambda i: self.occ[i].batch_index)
            first = self.occ[indices[0]]
            workdays = self.workdays[self._cid(first.calendar_id)]
            window_days = workdays[(workdays >= self.lower[indices[0]]) & (workdays <= self.upper[indices[0]])]
            fractional = any(self.occ[i].duration_hours is not None and
                abs(self.occ[i].duration_hours-self.occ[i].duration_days*calendars_hours)>1e-8
                for i in indices for calendars_hours in [float(self.hours[self._cid(self.occ[i].calendar_id)].max())])
            if fractional or self.options.allow_overtime:
                member = self.resource_ids.get(first.team_id)
                for rank, i in enumerate(indices):
                    c = self.candidates[i]
                    if member is None or not len(c.starts):
                        super()._construct([i])
                        continue
                    scores = self._batch_score(i, c, member, {})
                    target = (rank+.5)*len(window_days)/len(indices)-max(1,self.occ[i].duration_days)/2
                    scores += np.abs(np.searchsorted(window_days,c.starts)-target)
                    choice = int(scores.argmin())
                    if not np.isfinite(scores[choice]):
                        super()._construct([i])
                    else:
                        self.selected[i], self.workers[i] = choice, member
                        self._update(i, choice, member, 1)
                continue
            predecessors, costs, previous = [], None, None
            score_cache = {}
            valid = bool(len(window_days))
            member = self.resource_ids.get(first.team_id)
            valid &= member is not None
            for rank, i in enumerate(indices):
                if not valid:
                    break
                c = self.candidates[i]
                if not len(c.starts):
                    valid = False
                    break
                duration = max(1, self.occ[i].duration_days)
                # Centros distribuidos en el índice de días hábiles DEL equipo.
                target = max(0, min(len(window_days)-duration,
                                   round((rank+.5)*len(window_days)/len(indices)-duration/2)))
                scores = self._batch_score(i, c, member, score_cache)
                # En lotes la fecha objetivo es la posición uniforme en el turno;
                # due_date conserva el vencimiento contractual para control.
                scores += np.abs(np.searchsorted(window_days, c.starts)-target)
                if costs is None:
                    back = np.full(len(scores), -1, dtype=int)
                else:
                    prefix = np.minimum.accumulate(costs)
                    best = np.maximum.accumulate(np.where(costs == prefix, np.arange(len(costs)), -1))
                    pos = np.searchsorted(previous.finishes, c.starts, side="left")-1
                    back = np.where(pos >= 0, best[np.maximum(pos, 0)], -1)
                    scores += np.where(pos >= 0, prefix[np.maximum(pos, 0)], np.inf)
                predecessors.append(back)
                costs, previous = scores, c
                valid = np.isfinite(costs).any()
            if not valid:
                super()._construct(indices)
                self.forced[indices] = True
                self.warnings.append(f"{first.code.rsplit('-L', 1)[0]}, equipo {first.team_id}: "
                                     "no se encontró espacio en días de turno con la carga de las otras actividades.")
                continue
            chosen = int(costs.argmin())
            placements = []
            for index in range(len(indices)-1, -1, -1):
                placements.append((indices[index], chosen))
                chosen = int(predecessors[index][chosen])
            for i, choice in reversed(placements):
                self.selected[i], self.workers[i] = choice, member
                self._update(i, choice, member, 1)


class _PrecedenceLeveler(_TeamLeveler):
    def __init__(self, *args, relation_problem, **kwargs):
        super().__init__(*args, **kwargs)
        from ganttier.engine.cpm import Model
        self.model = m = Model(relation_problem)
        m.tighten_hard_bounds()
        by_id = {a.id: i for i, a in enumerate(m.acts)}
        self.model_indices = {i: by_id[key] for i, key in enumerate(self.keys)}
        self.related = {i for i, j in self.model_indices.items() if m.incoming[j] or m.outgoing[j]}
        self.starts, self.finishes = m.starts.copy(), m.finishes.copy()
        # Ambos motores indexan días civiles, con orígenes distintos.
        self.offset = self.origin - m.origin.toordinal()

    def _improvement_order(self, order):
        # Una mejora aislada no puede desplazar un componente ya factible.
        return [i for i in super()._improvement_order(order) if i not in self.related]

    def _construct(self, order):
        m = self.model
        inverse = {j: i for i, j in self.model_indices.items() if i in self.related}
        linked = [inverse[j] for j in m.order if j in inverse]
        original = {}
        for i in linked:
            original[i] = self.candidates[i] if self.occ[i].window_hard else self._candidates(i, True)
        # Pila explícita: las cadenas largas no dependen del límite de recursión.
        remaining, depth, visits = {}, 0, 0
        budget = max(1000, int(self.options.time_limit_s * 2000))
        while depth < len(linked):
            if visits >= budget or perf_counter() >= self.started + self.options.time_limit_s:
                raise _NoSchedule('Se agotó el presupuesto de reparación de precedencias; no se encontró un programa factible.')
            visits += 1
            i = linked[depth]
            j = self.model_indices[i]
            source = original[i]
            if depth not in remaining:
                lower = max([int(m.lower[j])] + [m.edge_lower(j, p, r, self.starts, self.finishes)
                                                 for p, r in m.incoming[j]])
                mask = ((source.starts + self.offset >= lower)
                        & (source.starts + self.offset <= m.upper[j]))
                if m.fixed[j]:
                    mask &= source.starts + self.offset == m.starts[j]
                remaining[depth] = np.flatnonzero(mask)
            indices = remaining[depth]
            candidates = _subset(source, indices)
            choice, worker = self._choose(i, candidates)
            if choice >= 0:
                # Al retroceder se prueba otra fecha del predecesor.
                remaining[depth] = np.delete(indices, choice)
                self.candidates[i] = candidates
                self.selected[i], self.workers[i] = choice, worker
                self._update(i, choice, worker, 1)
                self.starts[j] = candidates.starts[choice] + self.offset
                self.finishes[j] = candidates.finishes[choice] + self.offset
                depth += 1
                remaining.pop(depth, None)
            else:
                remaining.pop(depth, None)
                depth -= 1
                if depth < 0:
                    raise _NoSchedule('No se encontró una combinación de fechas, precedencias y capacidad factible.')
                previous = linked[depth]
                self._update(previous, int(self.selected[previous]), self.workers[previous], -1)
        super()._construct([i for i in order if i not in self.related])

    def run(self):
        try:
            result = super().run()
        except _NoSchedule as exc:
            return OptimizeResult({}, {}, status='infactible', method_used='maintenance_numpy',
                                  elapsed_s=perf_counter() - self.started, warnings=[str(exc)])
        # La búsqueda nunca certifica su propia factibilidad.
        m = self.model
        starts = {a.id: m.dt(m.starts[j]) for j, a in enumerate(m.acts) if m.fixed[j]}
        finishes = {a.id: m.dt(m.finishes[j]) for j, a in enumerate(m.acts) if m.fixed[j]}
        starts.update(result.starts)
        finishes.update(result.finishes)
        from ganttier.engine.verify import verify_with_options
        report = verify_with_options(m.problem, starts, finishes, self.options,
                                     worker_assignment=result.worker_assignment,
                                     crews=getattr(result, 'crew_assignment', {}))
        if not report.feasible:
            result.status = 'infactible'
            result.warnings.extend(v['detail'] for v in report.violations)
        return result


def level_periodic(occurrences: list[Occurrence], resources: list[Resource],
                   calendars: dict[int, WorkCalendar], weights: ObjectiveWeights,
                   options: OptimizeOptions, *, date_constraints=None,
                   horizon_start=None, horizon_end=None, reserved_load=None,
                   demand_units=None, relation_problem=None) -> OptimizeResult:
    """Nivela HH en días laborables y devuelve fechas sin mutar las entradas.

    Claves: ``activity_id`` si existe; en otro caso índice de base cero en la
    lista recibida. Una colisión se rechaza para evitar pérdida de resultados.
    Las ventanas incluyen inicio y fin. Si no existe solución factible se
    conservan fechas provisionales, con advertencia agregada y status infactible
    (la orquestación no las aplica como programa factible).
    ``crew_assignment(result)`` expone todos los miembros, con HH iguales.
    Las extensiones keyword-only permiten reservar HH de trabajo fijo y fijar
    restricciones de fechas y horizonte desde la orquestación, sin cambiar el
    contrato posicional existente.
    """
    started = perf_counter()
    if any(not math.isfinite(v) or v < 0 for v in (weights.level, weights.window, weights.makespan, weights.cost, weights.peak)):
        raise ValueError("Los pesos deben ser números finitos no negativos.")
    if options.level_period not in {"day", "week"}:
        raise ValueError("El período de nivelación debe ser day o week.")
    if not math.isfinite(options.time_limit_s) or options.time_limit_s <= 0:
        raise ValueError("El límite de tiempo debe ser positivo y finito.")
    for o in occurrences:
        if not math.isfinite(o.crew_size) or o.crew_size < 0:
            raise ValueError(f"{o.code}: tamaño de cuadrilla inválido.")
        if o.window_start > o.window_end:
            raise ValueError(f"{o.code}: ventana de fechas invertida.")
        if not isinstance(o.duration_days, int) or o.duration_days < 0 or not math.isfinite(o.work_hours) or o.work_hours < 0:
            raise ValueError(f"{o.code}: duración u horas de trabajo inválidas.")
        if o.duration_days == 0 and o.work_hours:
            raise ValueError(f"{o.code}: un hito no puede consumir horas de trabajo.")
    if not occurrences:
        empty = OptimizeMetrics(None, 0, 0, 0, 0, 0, 0, 0, 0)
        result = OptimizeResult({}, {}, before=empty, after=OptimizeMetrics(None, 0, 0, 0, 0, 0, 0, 0, 0),
                              profile_before=LoadProfile("week", [], []), profile_after=LoadProfile("week", [], []),
                              method_used="maintenance_numpy", status="heuristico", elapsed_s=perf_counter() - started)
        result.crew_assignment = {}
        return result
    leveler, extra = _Leveler, {}
    if demand_units or any(o.duration_days > 0 and not o.work_hours for o in occurrences):
        leveler, extra = _DemandLeveler, {'demand_units': demand_units}
    if any(o.batch_index is not None for o in occurrences):
        leveler, extra = _TeamLeveler, {"demand_units": demand_units}
    if relation_problem is not None and relation_problem.relations:
        leveler = _PrecedenceLeveler
        extra = {'demand_units': demand_units, 'relation_problem': relation_problem}
    result = leveler(occurrences, resources, calendars, weights, options, started,
                    date_constraints=date_constraints, horizon_start=horizon_start, horizon_end=horizon_end,
                    reserved_load=reserved_load, **extra).run()
    for key, (kind, _) in (date_constraints or {}).items():
        if kind == 'ALAP' and key in result.starts:
            result.warnings.append(f'Actividad {key}: mantenimiento no certifica la última fecha admisible ALAP; revise esta preferencia antes de aplicar el programa.')
    return result
