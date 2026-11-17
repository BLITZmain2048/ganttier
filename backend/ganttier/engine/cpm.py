"""CPM con fechas inclusivas, calendarios múltiples y avance real."""
from __future__ import annotations

from collections import deque
from datetime import date, timedelta
from math import ceil, isfinite
from typing import Protocol

import numpy as np

from ganttier.domain import CPMActivityResult, CPMResult, Relation, ScheduleProblem, remaining_work_start, schedule_floor


class CalendarHours(Protocol):
    """Parte de la interfaz WorkCalendar que requiere el índice interno."""

    def hours(self, day: date) -> float: ...


def _network(problem: ScheduleProblem) -> tuple[list[str], list[int], list[list], list[list]]:
    acts = problem.activities
    ids = {a.id: i for i, a in enumerate(acts)}
    errors: list[str] = []
    if len(ids) != len(acts):
        errors.append("Hay identificadores de actividad duplicados.")
    if len({a.code for a in acts}) != len(acts):
        errors.append("Hay códigos de actividad duplicados.")
    incoming: list[list] = [[] for _ in acts]
    outgoing: list[list] = [[] for _ in acts]
    degree = [0] * len(acts)
    for r in problem.relations:
        if r.pred_id not in ids or r.succ_id not in ids:
            errors.append(f"Relación {r.id}: referencia a una actividad inexistente.")
            continue
        if r.type not in {"FS", "SS", "FF", "SF"} or not isinstance(r.lag_days, int):
            errors.append(f"Relación {r.id}: tipo o desfase inválido.")
            continue
        p, s = ids[r.pred_id], ids[r.succ_id]
        incoming[s].append((p, r))
        outgoing[p].append((s, r))
        degree[s] += 1
    ready = deque(i for i, d in enumerate(degree) if not d)
    order = []
    while ready:
        i = ready.popleft()
        order.append(i)
        for j, _ in outgoing[i]:
            degree[j] -= 1
            if not degree[j]:
                ready.append(j)
    if len(order) != len(acts):
        codes = ", ".join(a.code for i, a in enumerate(acts) if degree[i])
        errors.append(f"Ciclo en la red; actividades involucradas o bloqueadas: {codes}.")
    return errors, order, incoming, outgoing


def validate_network(problem: ScheduleProblem) -> list[str]:
    """Devuelve errores de datos, referencias y ciclos sin modificar el problema."""
    errors, _, _, _ = _network(problem)
    if problem.horizon_end < problem.horizon_start:
        errors.append("El fin del horizonte es anterior a su inicio.")
    if problem.default_calendar_id not in problem.calendars:
        errors.append("No existe el calendario predeterminado.")
    resources = {r.id: r for r in problem.resources}
    if len(resources) != len(problem.resources):
        errors.append("Hay identificadores de recurso duplicados.")
    ids = {a.id for a in problem.activities}
    for a in problem.activities:
        if (a.calendar_id if a.calendar_id is not None else problem.default_calendar_id) not in problem.calendars:
            errors.append(f"{a.code}: calendario inexistente.")
        if not isinstance(a.duration_days, int) or a.duration_days < 0:
            errors.append(f"{a.code}: duración inválida.")
        if not isfinite(a.percent_complete) or not 0 <= a.percent_complete <= 100:
            errors.append(f"{a.code}: avance fuera del intervalo 0–100.")
        if a.remaining_duration is not None and (not isfinite(a.remaining_duration) or a.remaining_duration < 0):
            errors.append(f"{a.code}: duración remanente inválida.")
        if a.constraint_type not in {"ASAP", "ALAP", "SNET", "SNLT", "FNET", "FNLT", "MSO", "MFO"}:
            errors.append(f"{a.code}: restricción desconocida.")
        elif a.constraint_type not in {"ASAP", "ALAP"} and a.constraint_date is None:
            errors.append(f"{a.code}: falta la fecha de restricción.")
        if a.window_start and a.window_end and a.window_start > a.window_end:
            errors.append(f"{a.code}: ventana invertida.")
        if a.locked and not a.planned_start:
            errors.append(f"{a.code}: actividad bloqueada sin inicio planificado.")
        if a.status == "terminada" or a.percent_complete == 100 or a.actual_finish:
            if not a.actual_start or not a.actual_finish:
                errors.append(f"{a.code}: faltan fechas reales de la actividad terminada.")
        elif a.status == "en_curso" or a.percent_complete > 0:
            if not a.actual_start:
                errors.append(f"{a.code}: falta el inicio real de la actividad en curso.")
        if a.actual_start and a.actual_finish and a.actual_start > a.actual_finish:
            errors.append(f"{a.code}: fechas reales invertidas.")
        if not isfinite(a.work_hours) or a.work_hours < 0 or not isfinite(a.crew_size) or a.crew_size < 0:
            errors.append(f"{a.code}: trabajo o tamaño de cuadrilla inválido.")
    for r in problem.resources:
        if (r.calendar_id if r.calendar_id is not None else problem.default_calendar_id) not in problem.calendars:
            errors.append(f"{r.name}: calendario inexistente.")
        if any(not isfinite(v) or v < 0 for v in (r.capacity_hours_day, r.max_units, r.max_overtime_hours_day, r.cost_hour, r.overtime_cost_hour)):
            errors.append(f"{r.name}: capacidad o costo inválido.")
    for a in problem.assignments:
        if a.activity_id not in ids or (a.resource_id is not None and a.resource_id not in resources):
            errors.append(f"Asignación {a.id}: referencia inexistente.")
        if a.resource_id is None and not a.skill:
            errors.append(f"Asignación {a.id}: falta recurso o habilidad.")
        if any(not isfinite(v) or v < 0 for v in (a.units, a.work_hours)):
            errors.append(f"Asignación {a.id}: horas o unidades inválidas.")
    return errors


class CalendarIndex:
    """Índices y prefijos de calendario; operaciones de fecha O(1)."""

    def __init__(self, calendar: CalendarHours, dates: list[date]):
        self.hours = np.fromiter((calendar.hours(d) for d in dates), dtype=float, count=len(dates))
        if not np.isfinite(self.hours).all() or (self.hours < 0).any():
            raise ValueError("El calendario contiene horas inválidas.")
        self.days = np.flatnonzero(self.hours > 0)
        self.prefix = np.concatenate(([0], np.cumsum(self.hours > 0)))
        if not len(self.days):
            raise ValueError("El calendario no tiene días laborables en el horizonte ampliado.")

    def day(self, rank: int) -> int:
        if rank < 0 or rank >= len(self.days):
            raise ValueError("La programación excede el calendario disponible; amplíe el horizonte.")
        return int(self.days[rank])

    def rank(self, day: int) -> int:
        if day < 0 or day >= len(self.hours):
            raise ValueError("La fecha está fuera del calendario disponible; amplíe el horizonte.")
        return int(self.prefix[day])

    def next(self, day: int) -> int:
        return self.day(self.rank(day))

    def prev(self, day: int) -> int:
        return self.day(self.rank(day + 1) - 1)

    def finish(self, start: int, duration: int) -> int:
        return self.day(self.rank(start) + duration - 1) if duration else start

    def start_by(self, finish: int, duration: int) -> int:
        return self.day(self.rank(finish + 1) - max(duration, 1))

    def start_for(self, finish: int, duration: int) -> int:
        return self.day(self.rank(finish) - max(duration, 1) + 1)


class Model:
    """Representación interna compartida por CPM y optimización."""

    def __init__(self, problem: ScheduleProblem):
        errors = validate_network(problem)
        if errors:
            raise ValueError("\n".join(errors))
        self.problem = problem
        self.acts = problem.activities
        _, self.order, self.incoming, self.outgoing = _network(problem)
        n = len(self.acts)
        # The supplied calendars are finite. Network length must not trigger
        # calendar queries decades outside the requested project horizon.
        pad = 366
        dates = [problem.horizon_start, problem.horizon_end, problem.project.start_date]
        dates.extend(d for a in self.acts for d in (a.constraint_date, a.actual_start, a.actual_finish, a.planned_start, a.planned_finish, a.window_start, a.window_end) if d)
        dates.extend(d for d in (problem.data_date, problem.project.data_date, problem.project.end_date) if d)
        lo = max(1, min(d.toordinal() for d in dates) - pad)
        hi = min(date.max.toordinal() - 1, max(d.toordinal() for d in dates) + pad)
        self.origin = date.fromordinal(lo)
        self.dates = [date.fromordinal(d) for d in range(lo, hi + 1)]
        used = {problem.default_calendar_id}
        used.update(a.calendar_id for a in self.acts if a.calendar_id is not None)
        used.update(r.calendar_id for r in problem.resources if r.calendar_id is not None)
        self.calendars = {k: CalendarIndex(problem.calendars[k], self.dates) for k in used}
        self.cals = [self.calendars[a.calendar_id if a.calendar_id is not None else problem.default_calendar_id] for a in self.acts]
        self.duration = np.array([a.duration_days for a in self.acts], dtype=np.int64)
        self.fixed = np.zeros(n, dtype=bool)
        self.completed = np.zeros(n, dtype=bool)
        self.in_progress = np.zeros(n, dtype=bool)
        self.lower = np.zeros(n, dtype=np.int64)
        self.upper = np.full(n, self.idx(problem.horizon_end), dtype=np.int64)
        self.starts = np.zeros(n, dtype=np.int64)
        self.finishes = np.zeros(n, dtype=np.int64)
        self.warnings: list[str] = []
        cutoff = problem.data_date or problem.project.data_date or problem.project.start_date
        for i, a in enumerate(self.acts):
            cal = self.cals[i]
            # Bounds refer to work still to schedule, never the original budget.
            if a.actual_finish:
                self.duration[i] = 0
            elif a.actual_start:
                self.duration[i] = ceil(a.remaining_duration if a.remaining_duration is not None else a.duration_days * (1 - a.percent_complete / 100))
            self.lower[i] = cal.next(self.idx(max(problem.project.start_date, problem.horizon_start, schedule_floor(cutoff, problem.project.start_date))))
            self.upper[i] = cal.start_by(self.idx(problem.horizon_end), int(self.duration[i]))
            if a.window_hard:
                if a.window_start:
                    self.lower[i] = max(self.lower[i], cal.next(self.idx(a.window_start)))
                if a.window_end:
                    self.upper[i] = min(self.upper[i], cal.start_by(self.idx(a.window_end), int(self.duration[i])))
            d = self.idx(a.constraint_date) if a.constraint_date else 0
            if a.constraint_type == "SNET":
                self.lower[i] = max(self.lower[i], cal.next(d))
            elif a.constraint_type == "FNET":
                self.lower[i] = max(self.lower[i], cal.start_for(d, int(self.duration[i])))
            if a.actual_finish:
                self.completed[i] = self.fixed[i] = True
                self.starts[i], self.finishes[i] = self.idx(a.actual_start), self.idx(a.actual_finish)
                self.duration[i] = 0
            elif a.actual_start:
                self.in_progress[i] = self.fixed[i] = True
                self.starts[i] = self.idx(a.actual_start)
                remaining_start = cal.next(self.idx(remaining_work_start(cutoff, a.actual_start)))
                self.finishes[i] = cal.finish(remaining_start, int(self.duration[i]))
            elif a.locked or a.constraint_type in {"MSO", "MFO"}:
                self.fixed[i] = True
                if a.locked:
                    self.starts[i] = self.idx(a.planned_start)
                    self.finishes[i] = cal.finish(int(self.starts[i]), int(self.duration[i]))
                elif a.constraint_type == "MSO":
                    self.starts[i] = d
                    self.finishes[i] = cal.finish(d, int(self.duration[i]))
                else:
                    self.finishes[i] = d
                    self.starts[i] = cal.start_by(d, int(self.duration[i])) if self.duration[i] else d

        # Progress Override: actual events are immutable; incoming logic does
        # not suspend the remaining work. Remove edges consistently for every
        # pass/solver, while retaining the original network validation above.
        for i in range(n):
            if (self.completed[i] or self.in_progress[i]) and self.incoming[i]:
                self.warnings.append(f"{self.acts[i].code}: Progress Override; se omiten las relaciones entrantes al avance real.")
                self.incoming[i] = []
        for i in range(n):
            self.outgoing[i] = [(j, r) for j, r in self.outgoing[i]
                                if not (self.completed[j] or self.in_progress[j])]

    def idx(self, value: date) -> int:
        return (value - self.origin).days

    def dt(self, value: int) -> date:
        return self.origin + timedelta(days=int(value))

    def finish(self, i: int, start: int) -> int:
        return int(self.finishes[i]) if self.fixed[i] else self.cals[i].finish(start, int(self.duration[i]))

    def fs_offset(self, pred: int, relation: Relation) -> int:
        # The domain has no start/finish milestone type: zero-duration points
        # default to start-of-day. A completed ordinary task is still end-of-day.
        return int(relation.type == "FS" and self.acts[pred].duration_days > 0)

    def edge_lower(self, i: int, pred: int, relation: Relation, starts: np.ndarray, finishes: np.ndarray) -> int:
        cal = self.cals[i]
        event = int(finishes[pred] if relation.type[0] == "F" else starts[pred])
        rank = cal.rank(event + self.fs_offset(pred, relation)) + relation.lag_days
        if relation.type[1] == "F" and not self.fixed[i]:
            rank -= max(int(self.duration[i]) - 1, 0)
        return cal.day(rank)

    def edge_upper(self, i: int, succ: int, relation: Relation, starts: np.ndarray, finishes: np.ndarray) -> int:
        cal = self.cals[succ]
        target = int(finishes[succ] if relation.type[1] == "F" else starts[succ])
        rank = cal.rank(target + 1) - 1 - relation.lag_days
        # A large lead can make this upper bound nonbinding beyond the indexed
        # horizon. No date outside the model is needed to apply that bound.
        if rank >= len(cal.days):
            return int(self.cals[i].days[-1])
        event = cal.day(rank) - self.fs_offset(i, relation)
        return self.cals[i].start_by(event, int(self.duration[i])) if relation.type[0] == "F" else self.cals[i].prev(event)

    def tighten_hard_bounds(self) -> None:
        """Propagate hard successor bounds before any resource-based placement."""
        latest_s, latest_f = self.starts.copy(), self.finishes.copy()
        for i in reversed(self.order):
            if self.fixed[i]:
                continue
            upper = int(self.upper[i])
            for j, r in self.outgoing[i]:
                upper = min(upper, self.edge_upper(i, j, r, latest_s, latest_f))
            self.upper[i] = latest_s[i] = upper
            latest_f[i] = self.finish(i, upper)

    def violations(self, starts: np.ndarray, finishes: np.ndarray) -> list[str]:
        """Audita restricciones duras sin confundir plazos blandos con infactibilidad."""
        errors = []
        for i, a in enumerate(self.acts):
            s, f = int(starts[i]), int(finishes[i])
            if self.fixed[i] and (s != self.starts[i] or f != self.finishes[i]):
                errors.append(f"{a.code}: se modificaron fechas fijas.")
            if not self.completed[i] and not self.in_progress[i]:
                if self.duration[i] and (not self.cals[i].hours[s] or not self.cals[i].hours[f]):
                    errors.append(f"{a.code}: inicio o término en fecha no laborable.")
                if a.locked and a.planned_finish and f != self.idx(a.planned_finish):
                    errors.append(f"{a.code}: el término bloqueado no coincide con la duración y el calendario.")
            if not self.completed[i] and f > self.idx(self.problem.horizon_end):
                errors.append(f"{a.code}: el término excede el horizonte disponible.")
            if not self.completed[i] and not self.in_progress[i] and (s < self.lower[i] or s > self.upper[i]):
                errors.append(f"{a.code}: no cabe en el horizonte o la ventana dura.")
            if a.window_hard and ((a.window_start and s < self.idx(a.window_start)) or (a.window_end and f > self.idx(a.window_end))):
                errors.append(f"{a.code}: incumple su ventana dura.")
            if a.constraint_type == "MSO" and s != self.idx(a.constraint_date):
                errors.append(f"{a.code}: incumple la fecha de inicio obligatoria.")
            if a.constraint_type == "MFO" and f != self.idx(a.constraint_date):
                errors.append(f"{a.code}: incumple la fecha de término obligatoria.")
            for p, r in self.incoming[i]:
                required = self.edge_lower(i, p, r, starts, finishes)
                event = f if self.fixed[i] and r.type[1] == "F" else s
                if event < required:
                    errors.append(f"{a.code}: precedencia {self.acts[p].code}–{a.code} incompatible con sus fechas.")
        return errors


def _compute(model: Model) -> tuple[CPMResult, np.ndarray, np.ndarray]:
    m = model
    starts, finishes = m.starts.copy(), m.finishes.copy()
    for i in m.order:
        if not m.fixed[i]:
            starts[i] = max([int(m.lower[i])] + [m.edge_lower(i, p, r, starts, finishes) for p, r in m.incoming[i]])
            finishes[i] = m.finish(i, int(starts[i]))
    project_finish = int(finishes.max()) if len(finishes) else m.idx(m.problem.project.start_date)
    late_s, late_f = starts.copy(), finishes.copy()
    deadline = m.idx(m.problem.project.end_date) if m.problem.project.end_date else project_finish
    for i in reversed(m.order):
        a, cal = m.acts[i], m.cals[i]
        if m.fixed[i]:
            continue
        latest = min(cal.start_by(deadline, int(m.duration[i])), int(m.upper[i]))
        if a.constraint_type in {"SNLT", "FNLT"}:
            d = m.idx(a.constraint_date)
            latest = min(latest, cal.prev(d) if a.constraint_type == "SNLT" else cal.start_by(d, int(m.duration[i])))
        for j, r in m.outgoing[i]:
            latest = min(latest, m.edge_upper(i, j, r, late_s, late_f))
        late_s[i] = latest
        late_f[i] = m.finish(i, latest)
    for i in reversed(m.order):
        if m.acts[i].constraint_type == "ALAP" and not m.fixed[i] and late_s[i] >= starts[i]:
            latest = min([int(late_s[i])] + [m.edge_upper(i, j, r, starts, finishes) for j, r in m.outgoing[i]])
            starts[i] = max(int(starts[i]), latest)
            finishes[i] = m.finish(i, int(starts[i]))
    project_finish = int(finishes.max()) if len(finishes) else project_finish
    warnings = m.warnings + m.violations(starts, finishes)
    results = {}
    critical = []
    for i in m.order:
        a, cal = m.acts[i], m.cals[i]
        total = cal.rank(int(late_s[i])) - cal.rank(int(starts[i]))
        free_end = cal.start_by(project_finish, int(m.duration[i])) if not m.fixed[i] else int(starts[i])
        if m.outgoing[i] and not m.fixed[i]:
            free_end = min(free_end, min(m.edge_upper(i, j, r, starts, finishes) for j, r in m.outgoing[i]))
        free = max(0, cal.rank(free_end) - cal.rank(int(starts[i])))
        if total < 0:
            warnings.append(f"{a.code}: holgura negativa de {total} días laborables; revise los plazos y restricciones.")
        if total <= 0:
            critical.append(a.id)
        results[a.id] = CPMActivityResult(a.id, m.dt(starts[i]), m.dt(finishes[i]), m.dt(late_s[i]), m.dt(late_f[i]), total, free, total <= 0)
    m.starts, m.finishes = starts, finishes
    return CPMResult(results, m.dt(project_finish), critical, warnings), late_s, late_f


def compute_cpm(problem: ScheduleProblem) -> CPMResult:
    """Calcula CPM; datos inválidos/ciclos lanzan ValueError con detalle en español."""
    return _compute(Model(problem))[0]
