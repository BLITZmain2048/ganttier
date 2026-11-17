"""Perfiles NumPy y objetivo incremental, compartidos por ambos optimizadores."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

import numpy as np

from ganttier.domain import LoadProfile, LoadSeries, ObjectiveWeights, OptimizeMetrics, OptimizeOptions, remaining_work_start
from .cpm import Model


class Loads:
    """Mantiene cargas diarias y calcula movimientos sin reconstruir el programa."""

    def __init__(self, model: Model, options: OptimizeOptions, weights: ObjectiveWeights):
        self.m, self.options = model, options
        self.weights = np.array([weights.level, weights.makespan, weights.window, weights.cost], dtype=float)
        maximum = self.weights.max()
        if maximum:
            self.weights /= maximum
            self.weights /= self.weights.sum()
        else:
            self.weights[2] = 1.0
        p = model.problem
        self.begin, self.end = model.idx(p.horizon_start), model.idx(p.horizon_end)
        self.size = self.end - self.begin + 1
        dates = model.dates[self.begin:self.end + 1]
        self.period = options.level_period or "week"
        labels = [d.isoformat() if self.period == "day" else (d - timedelta(days=d.weekday())).isoformat() for d in dates]
        self.labels = list(dict.fromkeys(labels))
        mapping = {v: i for i, v in enumerate(self.labels)}
        self.period_index = np.array([mapping[v] for v in labels], dtype=np.int64)
        self.resources = {r.id: r for r in p.resources}
        self.keys: list[str] = []
        self.visible: list[int] = []
        self.lookup: dict[tuple, int] = {}
        capacities, overtimes, prices = [], [], []
        capacity_defined = []

        def group(key: tuple, name: str, members: list, visible: bool, *, constrained=True) -> int:
            if key in self.lookup:
                return self.lookup[key]
            idx = len(self.keys)
            self.lookup[key] = idx
            self.keys.append(name)
            capacity_defined.append(constrained)
            if visible:
                self.visible.append(idx)
            cap, ot, priced = np.zeros(self.size), np.zeros(self.size), np.zeros(self.size)
            for r in members:
                cal = model.calendars[r.calendar_id if r.calendar_id is not None else p.default_calendar_id]
                daily_hours = cal.hours[self.begin:self.end + 1]
                working = daily_hours > 0
                cap += np.minimum(daily_hours, r.capacity_hours_day) * r.max_units
                extra = working * r.max_overtime_hours_day * r.max_units
                ot += extra
                priced += extra * r.overtime_cost_hour
            capacities.append(cap)
            overtimes.append(ot)
            prices.append(np.divide(priced, ot, out=np.zeros_like(ot), where=ot > 0))
            return idx

        active = [r for r in p.resources if r.active]
        role_members: dict[str, dict] = defaultdict(dict)
        for r in active:
            for skill in r.skills or ["Sin habilidad"]:
                role_members[skill][r.id] = r
        activities = {a.id: a for a in model.acts}
        for assignment in p.assignments:
            r = self.resources.get(assignment.resource_id)
            if r and r.active:
                role = assignment.skill or activities[assignment.activity_id].required_skill or (r.skills[0] if r.skills else "Sin habilidad")
                role_members[role][r.id] = r
        if options.group_by == "resource":
            for r in active:
                group(("resource", r.id), r.name, [r], True)
        else:
            for skill, members in role_members.items():
                group(("skill", skill), skill, list(members.values()), True)
        by_activity: dict[int, list] = defaultdict(list)
        for a in p.assignments:
            by_activity[a.activity_id].append((a.resource_id, a.skill, a.work_hours, a.units))
        self.demands: list[list[tuple[int, float, float]]] = []
        self.dynamic_shares = defaultdict(list)
        share_cache = {}
        self.warnings: list[str] = []
        unassigned_codes = []
        uncovered_skills = set()
        for i, a in enumerate(model.acts):
            rows: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
            assignments = by_activity[a.id]
            fraction = 0.0 if model.completed[i] else (1 - a.percent_complete / 100 if model.in_progress[i] else 1.0)
            if not assignments and not a.required_skill and a.work_hours > 0:
                g = group(("unassigned",), "Sin habilidad asignada", [], True, constrained=False)
                rows[g][0] += a.work_hours * fraction
                unassigned_codes.append(a.code)
            if not assignments and a.required_skill and (a.work_hours or a.crew_size):
                assignments = [(None, a.required_skill, a.work_hours, a.crew_size)]
            for resource_id, skill, hours, units in assignments:
                resource = self.resources.get(resource_id)
                role = skill or a.required_skill or (resource.skills[0] if resource and resource.skills else "Sin habilidad")
                members = [resource] if resource and resource.active else ([r for r in active if role in r.skills] if resource_id is None else [])
                total = hours * fraction
                # HH is a total; units/crew_size only defines demand when no HH total is given.
                rate = units if not hours and fraction else 0.0
                if resource_id is None and not members:
                    # Unknown capacity is distinct from a known resource with
                    # zero availability. Keep exactly one visible demand row.
                    g = group(("uncovered_skill", role), role, [], True, constrained=options.respect_resource_limits)
                    rows[g][0] += total
                    rows[g][1] += rate
                    uncovered_skills.add(role)
                    continue
                if options.group_by == "skill":
                    eligible = list(role_members[role].values())
                    g = group(("skill", role), role, eligible, True)
                    rows[g][0] += total
                    rows[g][1] += rate
                if not members:
                    g = group(("missing", role, resource_id), role, [], options.group_by == "resource")
                    rows[g][0] += total
                    rows[g][1] += rate
                    self.warnings.append(f"{a.code}: no hay recursos activos para {role}.")
                    continue
                capacity = sum(r.capacity_hours_day * r.max_units for r in members)
                dynamic = resource_id is None and len({(r.calendar_id, r.capacity_hours_day, r.max_overtime_hours_day) for r in members}) > 1
                if dynamic:
                    signature = tuple(r.id for r in members)
                    if signature not in share_cache:
                        available = []
                        for member in members:
                            cid = member.calendar_id if member.calendar_id is not None else p.default_calendar_id
                            h = model.calendars[cid].hours
                            extra = (h > 0) * member.max_overtime_hours_day if options.allow_overtime else 0
                            available.append((np.minimum(h, member.capacity_hours_day) + extra) * member.max_units)
                        available = np.array(available)
                        denominator = available.sum(axis=0)
                        share_cache[signature] = np.divide(available, denominator, out=np.full_like(available, 1 / len(members)), where=denominator > 0)
                for r in members:
                    share = r.capacity_hours_day * r.max_units / capacity if capacity else 1 / len(members)
                    g = group(("resource", r.id), r.name, [r], options.group_by == "resource")
                    rows[g][0] += total * share
                    rows[g][1] += rate * share
                    if dynamic:
                        self.dynamic_shares[i, g].append((total, rate, share, share_cache[signature][members.index(r)]))
            self.demands.append([(g, hh, units) for g, (hh, units) in rows.items() if hh or units])
        if unassigned_codes:
            self.warnings.append(f"{len(unassigned_codes)} actividades con HH no tienen recurso ni habilidad: "
                                 f"no se nivelan contra capacidad ({', '.join(unassigned_codes)})")
        if uncovered_skills:
            self.warnings.append("Habilidades requeridas sin recursos activos con capacidad disponible "
                                 f"({', '.join(sorted(uncovered_skills))})")
        shape = (len(self.keys), self.size)
        self.capacity_defined = np.array(capacity_defined, dtype=bool)
        self.capacity = np.array(capacities).reshape(shape)
        self.overtime = np.array(overtimes).reshape(shape) if options.allow_overtime else np.zeros(shape)
        self.price = np.array(prices).reshape(shape)
        self.limit = self.capacity + self.overtime
        self.load = np.zeros(shape)
        self.period_load = np.zeros((len(self.keys), len(self.labels)))
        self.scales = np.ones(4)
        self.reference_finish = int(model.finishes.max()) if len(model.acts) else self.begin
        self.extension_price = 0.0
        self.search_finish = self.end
        self.period_slice = slice(None)
        self.level_periods = len(self.labels)
        self.cost_rows = [g for key, g in self.lookup.items() if key[0] in {"resource", "missing", "uncovered_skill"}]
        self.penalty = max(1.0, float(self.price.max(initial=0))) * 1000
        self._visible = set(self.visible)
        self._cost_rows = set(self.cost_rows)

    def cells(self, i: int, starts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Fechas laborables remanentes por candidato y validez del horizonte."""
        m, cal = self.m, self.m.cals[i]
        duration = int(m.duration[i])
        if m.completed[i] or duration == 0:
            return np.empty((len(starts), 0), dtype=np.int64), np.ones(len(starts), dtype=bool)
        if m.in_progress[i]:
            cutoff = m.problem.data_date or m.problem.project.data_date or m.problem.project.start_date
            starts = np.full(len(starts), cal.next(m.idx(remaining_work_start(cutoff, m.acts[i].actual_start))))
        ranks = cal.prefix[starts]
        days = cal.days[ranks[:, None] + np.arange(duration)] - self.begin
        valid = (days[:, 0] >= 0) & (days[:, -1] < self.size)
        return days, valid

    def amount(self, i: int, group: int, hours: float, units: float, days: np.ndarray) -> np.ndarray:
        """Distribute total HH proportionally to each candidate's calendar hours."""
        daily_hours = self.m.cals[i].hours[days + self.begin]
        total = daily_hours.sum(axis=-1, keepdims=True)
        result = np.divide(daily_hours, total, out=np.zeros_like(daily_hours), where=total > 0) * hours
        result += units * daily_hours
        for total_hh, rate, nominal, shares in self.dynamic_shares.get((i, group), []):
            base = np.divide(daily_hours, total, out=np.zeros_like(daily_hours), where=total > 0) * total_hh + rate * daily_hours
            result += (shares[days + self.begin] - nominal) * base
        return result

    def change(self, i: int, start: int, sign: float) -> None:
        if not self.demands[i]:
            return
        days, valid = self.cells(i, np.array([start]))
        if not days.size:
            return
        # Out-of-horizon schedules remain auditable, never wrap negative NumPy indices.
        mask = (days[0] >= 0) & (days[0] < self.size)
        for g, hours, units in self.demands[i]:
            # Normalize on the full duration before clipping the profile.
            amount = self.amount(i, g, hours, units, days)[0, mask] * sign
            self.load[g, days[0, mask]] += amount
            np.add.at(self.period_load[g], self.period_index[days[0, mask]], amount)

    def rebuild(self, starts: np.ndarray) -> None:
        self.load.fill(0)
        self.period_load.fill(0)
        if len(starts) >= 1000 and np.all(self.m.duration == 1) and not self.m.in_progress.any() and not self.dynamic_shares:
            # Building tiny NumPy matrices per activity dominated the 100k
            # benchmark. Accumulate scalar contributions, then reduce per row.
            by_group = defaultdict(lambda: ([], []))
            for i, start in enumerate(starts):
                if not self.demands[i]:
                    continue
                cal = self.m.cals[i]
                absolute = int(cal.days[cal.prefix[start]])
                day = absolute - self.begin
                if not 0 <= day < self.size:
                    continue
                for g, hours, units in self.demands[i]:
                    days, amounts = by_group[g]
                    days.append(day)
                    amounts.append(hours + units * float(cal.hours[absolute]))
            for g, (days, amounts) in by_group.items():
                self.load[g] = np.bincount(days, weights=amounts, minlength=self.size)
                self.period_load[g] = np.bincount(self.period_index, weights=self.load[g], minlength=len(self.labels))
            return
        for i, start in enumerate(starts):
            if self.demands[i]:
                self.change(i, int(start), 1)

    def window(self, i: int, starts: np.ndarray, finishes: np.ndarray) -> np.ndarray:
        a, m = self.m.acts[i], self.m
        value = np.zeros(len(starts))
        if a.window_start:
            value += np.maximum(0, m.idx(a.window_start) - starts)
        if a.window_end:
            value += np.maximum(0, finishes - m.idx(a.window_end))
        if a.constraint_type == "SNLT":
            value += np.maximum(0, starts - m.idx(a.constraint_date))
        elif a.constraint_type == "FNLT":
            value += np.maximum(0, finishes - m.idx(a.constraint_date))
        return value

    def candidates(self, i: int, starts: np.ndarray, other_finish: int) -> tuple[np.ndarray, np.ndarray]:
        """Evalúa en bloque el incremento exacto de carga/costo de los candidatos."""
        m = self.m
        if m.fixed[i]:
            finishes = np.full(len(starts), m.finishes[i], dtype=np.int64)
        elif m.duration[i]:
            cal = m.cals[i]
            finishes = cal.days[cal.prefix[starts] + m.duration[i] - 1]
        else:
            finishes = starts.copy()
        scores = self.weights[1] / self.scales[1] * (np.maximum(finishes, other_finish) - m.idx(m.problem.project.start_date) + 1)
        scores += self.weights[2] / self.scales[2] * self.window(i, starts, finishes)
        scores += self.extension_price * np.maximum(0, np.maximum(finishes, other_finish) - self.reference_finish)
        if not self.demands[i]:
            return scores, finishes
        days, valid = self.cells(i, starts)
        if not days.size:
            return scores, finishes
        safe = np.clip(days, 0, self.size - 1)
        periods = self.period_index[safe]
        for g, hours, units in self.demands[i]:
            amount = self.amount(i, g, hours, units, days)
            old = self.load[g, safe]
            new = old + amount
            if self.options.respect_resource_limits and self.capacity_defined[g]:
                valid &= (new <= self.limit[g, safe] + 1e-8).all(axis=1)
            excess0 = np.maximum(0, old - self.capacity[g, safe]) if self.capacity_defined[g] else np.zeros_like(old)
            excess1 = np.maximum(0, new - self.capacity[g, safe]) if self.capacity_defined[g] else np.zeros_like(new)
            if g in self._visible and self.weights[0]:
                if days.shape[1] == 1 or self.period == "day":
                    square = np.sum(2 * self.period_load[g, periods] * amount + amount**2, axis=1)
                else:
                    increments = np.zeros((len(starts), len(self.labels)))
                    np.add.at(increments, (np.arange(len(starts))[:, None], periods), amount)
                    square = np.sum(2 * self.period_load[g] * increments + increments**2, axis=1)
                # Remove change in mean: matters when calendar hours vary by start date.
                added = amount.sum(axis=1)
                total = self.period_load[g].sum()
                square -= (2 * total * added + added**2) / self.level_periods
                square += np.sum(excess1**2 - excess0**2, axis=1)
                scores += self.weights[0] / self.scales[0] * square
            if g in self._cost_rows and self.weights[3]:
                ot = self.overtime[g, safe]
                cost = (np.minimum(excess1, ot) - np.minimum(excess0, ot)) * self.price[g, safe]
                cost += (np.maximum(0, excess1 - ot) - np.maximum(0, excess0 - ot)) * self.penalty
                scores += self.weights[3] / self.scales[3] * cost.sum(axis=1)
        scores[~valid] = np.inf
        return scores, finishes

    def terms(self, starts: np.ndarray, finishes: np.ndarray) -> np.ndarray:
        visible = self.visible
        loads = self.period_load[visible, self.period_slice]
        level = float(np.sum((loads - loads.mean(axis=1, keepdims=True))**2)) if visible else 0.0
        excess = np.maximum(0, self.load - self.capacity)
        excess[~self.capacity_defined] = 0
        level += float(np.sum(excess[visible]**2))
        ot = np.minimum(excess, self.overtime)
        cost = float(np.sum((ot * self.price + np.maximum(0, excess - ot) * self.penalty)[self.cost_rows]))
        window = sum(float(self.window(i, starts[i:i+1], finishes[i:i+1])[0]) for i in range(len(starts)))
        span = max(0, int(finishes.max()) - self.m.idx(self.m.problem.project.start_date) + 1) if len(finishes) else 0
        return np.array([level, span, window, cost])

    def metrics(self, starts: np.ndarray, finishes: np.ndarray) -> OptimizeMetrics:
        loads = self.period_load[self.visible, self.period_slice]
        excess = np.maximum(0, self.load - self.capacity)[self.cost_rows]
        overtime = np.minimum(excess, self.overtime[self.cost_rows])
        outside = [self.window(i, starts[i:i+1], finishes[i:i+1])[0] for i in range(len(starts))]
        return OptimizeMetrics(
            self.m.dt(finishes.max()) if len(finishes) else None,
            float(loads.max(initial=0)), float(loads.std(axis=1).mean()) if len(loads) else 0.0,
            float(excess.sum()), float(overtime.sum()), float((overtime * self.price[self.cost_rows]).sum()),
            int(sum(outside)), int(sum(v > 0 for v in outside)),
            self.objective(starts, finishes),
        )

    def objective(self, starts, finishes):
        extension = max(0, int(finishes.max()) - self.reference_finish) if len(finishes) else 0
        return float(np.dot(self.weights, self.terms(starts, finishes) / self.scales) + self.extension_price * extension)

    def set_evaluation_span(self, finish):
        """Use one common program interval for scores and before/after profiles."""
        first = self.period_index[max(0, self.m.idx(self.m.problem.project.start_date) - self.begin)]
        last = self.period_index[min(self.size - 1, max(self.begin, finish) - self.begin)]
        self.period_slice = slice(int(first), int(last) + 1)
        self.level_periods = int(last - first + 1)

    def profile(self) -> LoadProfile:
        capacity = np.zeros_like(self.period_load)
        for g in self.visible:
            np.add.at(capacity[g], self.period_index, self.capacity[g])
        span = self.period_slice
        return LoadProfile(self.period, self.labels[span], [LoadSeries(self.keys[g], capacity[g, span].tolist(), self.period_load[g, span].tolist()) for g in self.visible])

    def violations(self) -> list[str]:
        if not self.options.respect_resource_limits:
            return []
        return [f"Capacidad insuficiente de {self.keys[g]}: {int(np.count_nonzero(self.load[g] > self.limit[g] + 1e-7))} días sobre el límite permitido."
                for g in self.cost_rows if np.any(self.load[g] > self.limit[g] + 1e-7)]
