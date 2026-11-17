"""Preparación vectorizada del historial físico y de los consumos incrementales."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from numpy.typing import NDArray

from ganttier.domain import ActivityView, ProgressEntry


def _nonnegative(values: NDArray, field: str) -> None:
    if np.any(~np.isfinite(values)) or np.any(values < 0):
        raise ValueError(f"{field} debe contener valores finitos no negativos.")


def _percent(values: NDArray) -> None:
    _nonnegative(values, "El avance")
    if np.any(values > 100):
        raise ValueError("El avance debe estar entre 0 y 100.")


def _activity_index(acts: list[ActivityView]) -> dict[int, int]:
    index = {a.id: i for i, a in enumerate(acts)}
    if len(index) != len(acts):
        raise ValueError("Los identificadores de actividad deben ser únicos.")
    return index


@dataclass
class _History:
    activity: NDArray
    days: NDArray
    percent: NDArray
    hours: NDArray
    costs: NDArray
    resources: list[int | None]
    last: NDArray
    has_records: NDArray

    def at(self, day: int, current: NDArray, cut: int) -> NDArray:
        """El estado al corte ya fue resuelto por _current_at_cut."""
        if day >= cut:
            return current.copy()
        return self.recorded_at(day)

    def recorded_at(self, day: int) -> NDArray:
        result = np.zeros(len(self.has_records), dtype=float)
        chosen = self.last[self.days[self.last] <= day]
        if chosen.size:
            # Una escritura por actividad: evita depender de índices repetidos.
            groups = self.activity[chosen]
            end = np.r_[groups[1:] != groups[:-1], True]
            result[groups[end]] = self.percent[chosen[end]]
        return result

    def earned_daily(self, weights: NDArray, start: int, size: int) -> NDArray:
        selected = self.last
        groups = self.activity[selected]
        pct = self.percent[selected]
        delta = pct.copy()
        if len(delta) > 1:
            delta[1:] -= np.where(groups[1:] == groups[:-1], pct[:-1], 0.0)
        return np.bincount(
            self.days[selected] - start,
            weights=weights[groups] * delta / 100.0, minlength=size,
        ).cumsum(dtype=float)


def _history(progress: list[ProgressEntry], index: dict[int, int], cut: int,
             milestones: NDArray | None = None) -> _History:
    # Registros ajenos al conjunto y posteriores al corte no alteran el informe.
    rows = [p for p in progress if p.activity_id in index and p.date.toordinal() <= cut]
    activity = np.fromiter((index[p.activity_id] for p in rows), dtype=np.int64, count=len(rows))
    days = np.fromiter((p.date.toordinal() for p in rows), dtype=np.int64, count=len(rows))
    ids = np.fromiter((p.id for p in rows), dtype=np.int64, count=len(rows))
    pct = np.fromiter((p.percent_complete for p in rows), dtype=float, count=len(rows))
    hours = np.fromiter((p.actual_hours for p in rows), dtype=float, count=len(rows))
    costs = np.fromiter((p.actual_cost for p in rows), dtype=float, count=len(rows))
    _percent(pct)
    if milestones is not None:
        pct = np.where(milestones[activity], np.where(pct >= 100, 100.0, 0.0), pct)
    _nonnegative(hours, "Las HH reales")
    _nonnegative(costs, "Los costos reales")
    order = np.lexsort((ids, days, activity))
    activity, days, pct = activity[order], days[order], pct[order]
    hours, costs = hours[order], costs[order]
    resources = [rows[i].resource_id for i in order]
    last = np.empty(0, dtype=np.int64)
    if len(rows):
        last = np.flatnonzero(np.r_[
            (activity[1:] != activity[:-1]) | (days[1:] != days[:-1]), True,
        ])
    has_records = np.zeros(len(index), dtype=bool)
    for p in progress:
        if p.activity_id in index:
            has_records[index[p.activity_id]] = True
    return _History(activity, days, pct, hours, costs, resources, last, has_records)


def _actual_finishes(acts: list[ActivityView], progress: list[ProgressEntry],
                     index: dict[int, int], cut: int) -> NDArray:
    """Fin explícito conocido al corte; no deduce una fecha desde un porcentaje."""
    finishes = np.fromiter((a.actual_finish.toordinal() if a.actual_finish else 0
                           for a in acts), dtype=np.int64)
    finishes[finishes > cut] = 0
    latest: dict[int, ProgressEntry] = {}
    for p in progress:
        if p.activity_id not in index or p.date.toordinal() > cut:
            continue
        key = (p.date.toordinal(), p.id)
        previous = latest.get(p.activity_id)
        if previous is None or key >= (previous.date.toordinal(), previous.id):
            latest[p.activity_id] = p
    for aid, p in latest.items():
        finish = p.actual_finish.toordinal() if p.actual_finish else 0
        if finish or p.percent_complete < 100:
            finishes[index[aid]] = finish if finish <= cut else 0
    return finishes


def _current_at_cut(acts: list[ActivityView], history: _History, cut: int,
                    finishes: NDArray) -> NDArray:
    snapshot = np.fromiter((a.percent_complete for a in acts), dtype=float)
    _percent(snapshot)
    # Un snapshot sin fecha no permite reconstruir un estado anterior a su inicio.
    future = np.fromiter((bool(a.actual_start and a.actual_start.toordinal() > cut)
                          or bool(a.actual_finish and a.actual_finish.toordinal() > cut)
                          for a in acts), dtype=bool)
    snapshot[future] = 0
    current = history.recorded_at(cut)
    current[~history.has_records] = snapshot[~history.has_records]
    current[finishes > 0] = 100
    milestones = np.fromiter((a.duration_days == 0 for a in acts), dtype=bool)
    current[milestones] = np.where(current[milestones] >= 100, 100.0, 0.0)
    return current
