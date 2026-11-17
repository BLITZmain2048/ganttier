"""Desempeño por trabajador con agregaciones vectorizadas de asignaciones y avance."""
from __future__ import annotations

from datetime import date

import numpy as np
from numpy.typing import NDArray

from ganttier.domain import ActivityView, Assignment, ProgressEntry, Resource, WorkerKPI
from ._core import _dates, _ratio, _calendar_groups, _calendar_fraction, WorkCalendar
from .progress import _activity_index, _history, _nonnegative, _percent, _actual_finishes, _current_at_cut


def worker_performance(acts: list[ActivityView], assignments: list[Assignment],
                       progress: list[ProgressEntry], resources: list[Resource],
                       period_start: date, period_end: date, data_date: date, *,
                       calendar: WorkCalendar | None = None,
                       calendars: dict[int, WorkCalendar] | None = None) -> list[WorkerKPI]:
    """Calcula HH del período y estado al corte; devuelve todos los recursos trabajadores."""
    if period_end < period_start:
        raise ValueError("El término del período no puede ser anterior al inicio.")
    index = _activity_index(acts)
    resource_map = {r.id: r for r in resources}
    if len(resource_map) != len(resources):
        raise ValueError("Los identificadores de recurso deben ser únicos.")
    workers = [r for r in resources if r.kind in ("trabajador", "cuadrilla")]
    worker_index = {r.id: i for i, r in enumerate(workers)}
    n, m = len(acts), len(workers)
    current = np.fromiter((a.percent_complete for a in acts), dtype=float)
    budgets = np.fromiter((a.work_hours for a in acts), dtype=float)
    durations = np.fromiter((a.duration_days for a in acts), dtype=float)
    _percent(current)
    _nonnegative(budgets, "Las HH presupuestadas")
    _nonnegative(durations, "La duración")
    starts, finishes = _dates(acts)
    first, last, cut = period_start.toordinal(), period_end.toordinal(), data_date.toordinal()
    history = _history(progress, index, cut, durations == 0)
    finish_actual = _actual_finishes(acts, progress, index, cut)
    current = _current_at_cut(acts, history, cut, finish_actual)

    rows = [a for a in assignments if a.activity_id in index
            and (a.resource_id is None or a.resource_id in worker_index)]
    ai = np.fromiter((index[a.activity_id] for a in rows), dtype=np.int64)
    ri = np.fromiter((worker_index.get(a.resource_id, -1) for a in rows), dtype=np.int64)
    hours = np.fromiter((a.work_hours for a in rows), dtype=float)
    units = np.fromiter((a.units for a in rows), dtype=float)
    capacities = np.fromiter((resource_map[a.resource_id].capacity_hours_day
                              if a.resource_id in resource_map else 8.0 for a in rows), dtype=float)
    _nonnegative(hours, "Las HH asignadas")
    _nonnegative(units, "Las unidades asignadas")
    _nonnegative(capacities, "La capacidad diaria")
    hours = np.where(hours > 0, hours, units * durations[ai] * capacities)
    total = np.bincount(ai, weights=hours, minlength=n)
    total_units = np.bincount(ai, weights=units, minlength=n)
    assignment_count = np.bincount(ai, minlength=n)
    shares = np.divide(hours, total[ai], out=np.zeros(len(ai)), where=total[ai] > 0)
    no_hours = total[ai] == 0
    shares[no_hours] = np.divide(units[no_hours], total_units[ai[no_hours]],
                                 out=np.zeros(int(no_hours.sum())), where=total_units[ai[no_hours]] > 0)
    # Codifica el par trabajador/actividad para fusionar asignaciones duplicadas.
    valid = ri >= 0
    keys = ri[valid] * max(1, n) + ai[valid]
    unique, inverse = np.unique(keys, return_inverse=True)
    pair_hours = np.bincount(inverse, weights=hours[valid], minlength=len(unique))
    pair_shares = np.bincount(inverse, weights=shares[valid], minlength=len(unique))

    hr = np.fromiter((worker_index.get(rid, -1) for rid in history.resources), dtype=np.int64)
    reported = np.unique(hr[hr >= 0] * max(1, n) + history.activity[hr >= 0])
    extra = np.setdiff1d(reported, unique, assume_unique=True)
    if extra.size:
        extra_ai = extra % n
        # Sin asignaciones, reparte entre trabajadores que registraron ejecución.
        reporter_count = np.bincount(reported % n, minlength=n)
        extra_shares = np.where(assignment_count[extra_ai] == 0, 1.0 / reporter_count[extra_ai], 0.0)
        unique = np.r_[unique, extra]
        pair_hours = np.r_[pair_hours, np.zeros(len(extra))]
        pair_shares = np.r_[pair_shares, extra_shares]
    pa = unique % max(1, n)
    pr = unique // max(1, n)

    groups = _calendar_groups(acts, calendar, calendars)
    full_fraction = _calendar_fraction(starts, finishes, durations == 0, first, last, groups)
    elapsed_fraction = _calendar_fraction(starts, finishes, durations == 0, first, min(last, cut), groups)
    if first > cut:
        delta_pct = np.zeros(n)
    else:
        delta_pct = (history.at(min(last, cut), current, cut)
                     - history.at(first - 1, current, cut)) / 100.0
    known_finish = (finish_actual > 0) & (finish_actual <= cut)
    complete = (current >= 100) | known_finish
    due = (finishes >= first) & (finishes <= last) & (finishes > 0)
    on_time = due & known_finish & (finish_actual <= finishes)
    expired = due & (finishes <= cut)
    delay = np.maximum(0, np.where(known_finish, finish_actual, cut) - finishes)

    def aggregate(values: NDArray) -> NDArray:
        return np.bincount(pr, weights=values, minlength=m)

    assigned = np.bincount(pr, minlength=m)
    completed = aggregate(complete[pa])
    dues = aggregate(due[pa])
    timely = aggregate(on_time[pa])
    planned = aggregate(pair_hours * full_fraction[pa])
    planned_elapsed = aggregate(budgets[pa] * pair_shares * elapsed_fraction[pa])
    earned = aggregate(budgets[pa] * pair_shares * delta_pct[pa])
    delayed_count = aggregate(expired[pa])
    delay_total = aggregate(delay[pa] * expired[pa])
    real_mask = (hr >= 0) & (history.days >= first) & (history.days <= min(last, cut))
    actual = np.bincount(hr[real_mask], weights=history.hours[real_mask], minlength=m)
    # Consumos sin trabajador se reparten por las mismas participaciones del EV.
    # Consumos con resource_id desconocido/equipo no se reasignan a personas.
    untagged = np.fromiter((rid is None for rid in history.resources), dtype=bool)
    untagged &= (history.days >= first) & (history.days <= min(last, cut))
    untagged_hours = np.bincount(history.activity[untagged], weights=history.hours[untagged], minlength=n)
    for i, activity in enumerate(acts):
        if not history.has_records[i] and first <= (activity.actual_start or data_date).toordinal() <= min(last, cut):
            untagged_hours[i] += activity.actual_hours
    actual = actual + aggregate(untagged_hours[pa] * pair_shares)
    result: list[WorkerKPI] = []
    for i, worker in enumerate(workers):
        efficiency = _ratio(earned[i], actual[i])
        result.append(WorkerKPI(
            resource_id=worker.id, name=worker.name, assigned_activities=int(assigned[i]),
            completed_activities=int(completed[i]), completed_on_time=int(timely[i]),
            due_in_period=int(dues[i]), compliance_pct=_ratio(100 * timely[i], dues[i]),
            planned_hours=float(planned[i]), earned_hours=float(earned[i]), actual_hours=float(actual[i]),
            hh_efficiency=efficiency, avg_delay_days=_ratio(delay_total[i], delayed_count[i]),
            SPI=_ratio(earned[i], planned_elapsed[i]), CPI=efficiency,
            pending_activities=int(assigned[i] - completed[i]),
        ))
    return result
