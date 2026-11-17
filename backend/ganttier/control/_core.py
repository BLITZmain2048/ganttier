"""Núcleo común de curvas diarias y Earned Schedule sin matrices actividad × día."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from math import ceil
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from ganttier.domain import ActivityView, BaselineActivity, ProgressEntry
from .progress import _activity_index, _history, _nonnegative, _actual_finishes, _current_at_cut, _History


class WorkCalendar(Protocol):
    """Parte de la interfaz pública de A1 consumida por control."""

    def is_workday(self, d: date) -> bool: ...
    def next_workday(self, d: date, include: bool = True) -> date: ...
    def add_workdays(self, start: date, n: int) -> date: ...


def _ratio(numerator: float, denominator: float) -> float | None:
    return float(numerator / denominator) if denominator != 0 else None


def _dates(acts: list[ActivityView] | list[BaselineActivity]) -> tuple[NDArray, NDArray]:
    starts = np.fromiter((a.start.toordinal() if a.start else 0 for a in acts), dtype=np.int64)
    finishes = np.fromiter((a.finish.toordinal() if a.finish else 0 for a in acts), dtype=np.int64)
    if np.any((starts > 0) & (finishes > 0) & (finishes < starts)):
        raise ValueError("La fecha de término no puede ser anterior al inicio.")
    return starts, finishes


def _spread(starts: NDArray, finishes: NDArray, weights: NDArray,
            milestones: NDArray, origin: int, working: NDArray) -> NDArray:
    """Distribuye pesos mediante diferencias de tasas y sumas por índice."""
    size = len(working)
    valid = weights > 0
    s, f, w = starts[valid] - origin, finishes[valid] - origin, weights[valid]
    if not len(w):
        return np.zeros(size)
    prefix = np.r_[0, working.cumsum()]
    counts = prefix[f + 1] - prefix[s]
    points = milestones[valid] | (counts == 0)
    rates = w[~points] / counts[~points]
    delta = (np.bincount(s[~points], weights=rates, minlength=size + 1)
             - np.bincount(f[~points] + 1, weights=rates, minlength=size + 1))
    daily = delta[:-1].cumsum(dtype=float) * working
    daily += np.bincount(f[points], weights=w[points], minlength=size)
    result = np.maximum(daily, 0).cumsum()
    # Evita residuos de cancelación que produzcan valores mayores que BAC.
    result = np.minimum(result, w.sum())
    result[int(f.max()):] = w.sum()
    return result


def _calendar_groups(acts, calendar, calendars=None):
    """Índices por calendario efectivo; no crea una matriz actividad por día."""
    groups = {}
    for i, activity in enumerate(acts):
        cal = (calendars or {}).get(activity.calendar_id, calendar)
        groups.setdefault(id(cal), ([], cal))[0].append(i)
    return [(np.asarray(indices, dtype=np.int64), cal) for indices, cal in groups.values()]


def _working_mask(calendar, origin: int, size: int) -> NDArray:
    if calendar is None:
        return np.ones(size, dtype=bool)
    return np.fromiter((calendar.is_workday(date.fromordinal(day))
                       for day in range(origin, origin + size)), dtype=bool, count=size)


def _spread_calendars(starts, finishes, weights, milestones, origin, size, groups):
    result = np.zeros(size)
    for indices, calendar in groups:
        result += _spread(starts[indices], finishes[indices], weights[indices],
                          milestones[indices], origin, _working_mask(calendar, origin, size))
    positive = weights > 0
    if positive.any():
        result = np.minimum(result, weights.sum())
        result[int(finishes[positive].max()) - origin:] = weights.sum()
    return result


def _calendar_fraction(starts, finishes, milestones, first, last, groups):
    """La misma distribución que PV, consultada mediante prefijos laborables."""
    result = np.zeros(len(starts))
    if first > last:
        return result
    for indices, calendar in groups:
        valid = indices[(starts[indices] > 0) & (finishes[indices] > 0)]
        if not len(valid):
            continue
        origin, end = int(starts[valid].min()), int(finishes[valid].max())
        prefix = np.r_[0, _working_mask(calendar, origin, end - origin + 1).cumsum()]
        s, f = starts[valid] - origin, finishes[valid] - origin
        counts = prefix[f + 1] - prefix[s]
        left = np.clip(np.maximum(starts[valid], first) - origin, 0, len(prefix) - 1)
        right = np.clip(np.minimum(finishes[valid], last) + 1 - origin, 0, len(prefix) - 1)
        overlap = np.maximum(0, prefix[right] - prefix[left])
        fraction = overlap / np.maximum(1, counts)
        points = milestones[valid] | (counts == 0)
        result[valid] = np.where(points, (finishes[valid] >= first) & (finishes[valid] <= last), fraction)
    return result


def _forecast_workdays(fs, ff, acts, current, active, groups):
    """Resuelve una vez cada par (inicio, duración) de cada calendario.

    La agrupación no reduce pesos ni consumos: conserva exactamente las sumas
    del cálculo original. Evita recorrer en Python cada actividad pendiente.
    La duración vigente sigue viniendo de la ficha, no de la línea base.
    """
    for group, calendar in groups:
        indices = group[active[group]]
        if not len(indices):
            continue
        durations = (acts.durations[indices] if hasattr(acts, 'durations') else
                     np.fromiter((acts[i].duration_days for i in indices), dtype=float))
        days = np.maximum(1, np.ceil(durations * (1 - current[indices] / 100)))
        starts = fs[indices]
        order = np.lexsort((days, starts))
        sorted_starts, sorted_days = starts[order], days[order]
        first = np.r_[True, (sorted_starts[1:] != sorted_starts[:-1]) |
                            (sorted_days[1:] != sorted_days[:-1])]
        inverse = np.empty(len(order), dtype=np.int64)
        inverse[order] = first.cumsum() - 1
        unique = np.flatnonzero(first)
        resolved_starts = np.empty(len(unique), dtype=np.int64)
        resolved_finishes = np.empty(len(unique), dtype=np.int64)
        for j, pos in enumerate(unique):
            ordinal = int(sorted_starts[pos])
            start = calendar.next_workday(date.fromordinal(ordinal))
            if start.toordinal() < ordinal:
                raise ValueError("El calendario no devolvió un día laborable posterior al corte.")
            finish = calendar.add_workdays(start, int(sorted_days[pos]))
            resolved_starts[j], resolved_finishes[j] = start.toordinal(), finish.toordinal()
        fs[indices] = resolved_starts[inverse]
        ff[indices] = np.maximum(ff[indices], resolved_finishes[inverse])


def _forecast_finish_workdays(ff, active, groups):
    """Ajusta fechas finales únicas; los hitos ya están excluidos por el llamador."""
    for group, calendar in groups:
        indices = group[active[group]]
        if not len(indices):
            continue
        unique, inverse = np.unique(ff[indices], return_inverse=True)
        resolved = np.fromiter((calendar.next_workday(date.fromordinal(int(day))).toordinal()
                               for day in unique), dtype=np.int64)
        ff[indices] = resolved[inverse]


@dataclass
class _Control:
    origin: int
    cut: int
    planned: NDArray
    earned: NDArray
    actual: NDArray
    forecast: NDArray
    budget: float
    ev: float
    planned_start: int | None
    planned_finish: date | None
    forecast_finish: date | None
    es: float | None
    spi_t: float | None
    evm_forecast_finish: date | None = None


def _earned_schedule(planned: NDArray, origin: int, start: int | None,
                     finish: date | None, cut: int, ev: float, bac: float
                     ) -> tuple[float | None, float | None, date | None]:
    if bac <= 0 or start is None or finish is None:
        return None, None, None
    duration = finish.toordinal() - start + 1
    elapsed = max(0, cut - start + 1)
    if ev <= 0:
        es = 0.0
    elif ev >= bac:
        es = float(duration)
    else:
        # C es el último período completo con PV(C) <= EV; omite mesetas.
        i = int(np.searchsorted(planned, ev, side="right"))
        before = float(planned[i - 1]) if i else 0.0
        increment = float(planned[i]) - before
        es = origin + i - start + (ev - before) / increment
    spi_t = _ratio(es, elapsed)
    if spi_t is None or spi_t <= 0:
        return es, spi_t, None
    # El término es el día cuyo cierre contiene el tiempo continuo pronosticado.
    days = duration / spi_t
    if not np.isfinite(days) or days > date.max.toordinal() - start + 1:
        return es, spi_t, None
    finish_ordinal = max(cut, start + ceil(days - 1e-10) - 1)
    return es, spi_t, date.fromordinal(finish_ordinal)


@dataclass
class _Prepared:
    acts: list
    index: dict
    groups: list
    effective: list
    milestones: NDArray
    history: _History
    actual_finishes: NDArray
    current: NDArray
    duration: NDArray
    weights: NDArray
    ps: NDArray
    pf: NDArray
    cs: NDArray
    cf: NDArray
    snapshot_days: NDArray
    snapshot_values: NDArray


def _prepare(acts, baseline, progress, calendar, data_date, basis, calendars=None):
    """Prepara contribuciones por actividad; no realiza sumas del proyecto."""
    if basis not in {"hours", "cost", "count"}:
        raise ValueError("La base debe ser 'hours', 'cost' o 'count'.")
    _activity_index(acts)
    # None significa sin línea base. Un diccionario (incluso vacío) es una
    # línea base activa: no incorpora automáticamente alcance no aprobado.
    if baseline is not None:
        acts = [a for a in acts if a.id in baseline]
    index = _activity_index(acts)
    groups = _calendar_groups(acts, calendar, calendars)
    effective = [(calendars or {}).get(a.calendar_id, calendar) for a in acts]
    cut = data_date.toordinal()
    milestones = np.fromiter((a.duration_days == 0 for a in acts), dtype=bool)
    history = _history(progress, index, cut, milestones)
    actual_finishes = _actual_finishes(acts, progress, index, cut)
    current = _current_at_cut(acts, history, cut, actual_finishes)
    plans = [(baseline or {}).get(a.id, a) for a in acts]
    if baseline:
        for a, p in zip(acts, plans):
            if isinstance(p, BaselineActivity) and p.activity_id != a.id:
                raise ValueError("La línea base no coincide con el identificador de actividad.")
    duration = np.fromiter((p.duration_days for p in plans), dtype=float)
    _nonnegative(duration, "La duración")
    if basis == "hours":
        weights = np.fromiter((p.work_hours for p in plans), dtype=float)
        _nonnegative(weights, "Las HH presupuestadas")
    elif basis == "cost":
        weights = np.fromiter((p.cost if isinstance(p, BaselineActivity) else p.cost_budget
                               for p in plans), dtype=float)
    else:
        weights = np.ones(len(acts))
    _nonnegative(weights, "El presupuesto")
    ps, pf = _dates(plans)
    cs, cf = _dates(acts)
    # Un registro futuro también elimina el respaldo de consumo de la ficha.
    snapshot_days = np.fromiter(((a.actual_start or data_date).toordinal()
        if not history.has_records[i] and (a.actual_start is None or a.actual_start <= data_date)
        else 0 for i, a in enumerate(acts)), dtype=np.int64)
    snapshot_values = np.fromiter(((a.actual_hours if basis == 'hours' else a.actual_cost
        if basis == 'cost' else 0.0) if snapshot_days[i] else 0.0
        for i, a in enumerate(acts)), dtype=float)
    _nonnegative(snapshot_values, "Los consumos de la ficha")
    return _Prepared(acts, index, groups, effective, milestones, history, actual_finishes,
                     current, duration, weights, ps, pf, cs, cf, snapshot_days, snapshot_values)


def update_prepared(previous, acts, progress, baseline, calendar, data_date, basis, calendars=None):
    """Reemplaza solo contribuciones editadas; conserva el orden de reducción.

    El historial se concatena y ordena estable por actividad. Dentro de cada
    actividad ya respeta (fecha, ID), incluidos duplicados y registros futuros.
    No se restan ni suman totales previamente redondeados.
    """
    part = _prepare(acts, baseline, progress, calendar, data_date, basis, calendars)
    indices = np.asarray([previous.index[a.id] for a in part.acts], dtype=np.int64)
    if not indices.size:
        return previous
    changed = {}
    for name in ('milestones','actual_finishes','current','duration','weights','ps','pf','cs','cf',
                 'snapshot_days','snapshot_values'):
        values = getattr(previous, name).copy()
        values[indices] = getattr(part, name)
        changed[name] = values
    updated_acts = previous.acts.copy()
    for i, activity in zip(indices, part.acts):
        updated_acts[i] = activity
    old, new = previous.history, part.history
    keep = ~np.isin(old.activity, indices)
    activity = np.r_[old.activity[keep], indices[new.activity]]
    order = np.argsort(activity, kind='stable')
    arrays = {name: np.r_[getattr(old,name)[keep],getattr(new,name)][order]
              for name in ('days','percent','hours','costs')}
    activity = activity[order]
    days = arrays['days']
    last = (np.flatnonzero(np.r_[(activity[1:] != activity[:-1]) | (days[1:] != days[:-1]), True])
            if len(activity) else np.empty(0,dtype=np.int64))
    recorded = old.has_records.copy()
    recorded[indices] = new.has_records
    # Recursos no intervienen en EVM/curva S; este historial es privado del panel.
    history = _History(activity, arrays['days'], arrays['percent'], arrays['hours'], arrays['costs'], [], last, recorded)
    return replace(previous, acts=updated_acts, history=history, **changed)


def _calculate(acts: list[ActivityView], baseline: dict[int, BaselineActivity] | None,
               progress: list[ProgressEntry], calendar: WorkCalendar,
               data_date: date, basis: str, *, forecast: bool = True,
               calendars: dict[int, WorkCalendar] | None = None,
               prepared: _Prepared | None = None) -> _Control:
    p = prepared if prepared is not None else _prepare(acts, baseline, progress, calendar, data_date, basis, calendars)
    acts, groups, effective = p.acts, p.groups, p.effective
    milestones, history, actual_finishes, current = p.milestones, p.history, p.actual_finishes, p.current
    duration, weights, ps, pf, cs, cf = p.duration, p.weights, p.ps, p.pf, p.cs, p.cf
    cut = data_date.toordinal()
    positive = weights > 0
    if np.any(positive & ((ps == 0) | (pf == 0))):
        raise ValueError("Las actividades con presupuesto deben tener inicio y término planificados.")
    remaining = weights * (1 - current / 100)
    if forecast and np.any((remaining > 0) & ((cs == 0) | (cf == 0))):
        raise ValueError("El remanente necesita fechas vigentes para calcular la proyección.")
    starts = [cut]
    ends = [cut]
    if positive.any():
        starts.append(int(ps[positive].min()))
        ends.append(int(pf[positive].max()))
    if history.days.size:
        starts.append(int(history.days.min()))
    snapshots = p.snapshot_days > 0
    snapshot_days, snapshot_values = p.snapshot_days[snapshots], p.snapshot_values[snapshots]
    if snapshot_days.size:
        starts.append(int(snapshot_days.min()))
    fs, ff = cs.copy(), cf.copy()
    if forecast and np.any(remaining > 0):
        if cut == date.max.toordinal():
            raise ValueError("No hay fechas disponibles después del corte para proyectar el remanente.")
        fs = np.maximum(cs, cut + 1)
        ff = np.maximum(cf, fs)
        # La duración remanente física impide comprimir tareas vencidas a un día.
        _forecast_workdays(fs, ff, acts, current, (remaining > 0) & ~milestones, groups)
        ends.append(int(ff[remaining > 0].max()))
    origin, end = min(starts), max(ends)
    size = end - origin + 1
    planned = _spread_calendars(ps, pf, weights, duration == 0, origin, size, groups)
    earned = history.earned_daily(weights, origin, size)
    budget = float(weights.sum())
    ev = float(np.dot(weights, current / 100))
    earned[cut - origin:] = ev
    consumption = history.hours if basis == "hours" else history.costs
    if basis == "count":
        consumption = np.zeros_like(history.hours)
    actual = np.bincount(history.days - origin, weights=consumption, minlength=size).cumsum(dtype=float)
    actual += np.bincount(snapshot_days - origin, weights=snapshot_values, minlength=size).cumsum()
    projection = np.full(size, ev)
    if forecast:
        projection += _spread_calendars(fs, ff, remaining, milestones, origin, size, groups)
    planned_start = int(ps[positive].min()) if positive.any() else None
    planned_finish = date.fromordinal(int(pf[positive].max())) if positive.any() else None
    es_cut = cut
    if positive.any() and np.all(current[positive] >= 100) and np.all(actual_finishes[positive] > 0):
        es_cut = int(actual_finishes[positive].max())
    es, spi_t, finish = _earned_schedule(planned, origin, planned_start, planned_finish, es_cut, ev, budget)
    evm_forecast_finish = finish
    if forecast and np.any(remaining > 0):
        # Pronóstico combinado: no comprime el remanente vigente para alcanzar
        # una fecha estadística optimista; sí lo dilata si IEAC(t) es posterior.
        last = int(ff[remaining > 0].max())
        if finish is not None and finish.toordinal() > last:
            target = finish.toordinal()
            factor = (target - cut) / (last - cut)
            active = remaining > 0
            fs[active] = cut + np.ceil((fs[active] - cut) * factor).astype(np.int64)
            ff[active] = cut + np.ceil((ff[active] - cut) * factor).astype(np.int64)
            # No mover fechas de hitos por calendario; para trabajo el fin
            # identifica el último día en el que se gana valor.
            _forecast_finish_workdays(ff, active & ~milestones, groups)
            new_end = int(ff[active].max())
            padding = max(0, new_end - end)
            if padding:
                planned = np.pad(planned, (0, padding), mode="edge")
                earned = np.pad(earned, (0, padding), mode="edge")
                actual = np.pad(actual, (0, padding), mode="edge")
            projection = ev + _spread_calendars(fs, ff, remaining, milestones, origin, size + padding, groups)
        # EVM conserva la fecha puramente estadística; SCurve informa la fecha
        # de su proyección combinada, que puede ser posterior por calendario.
        finish = date.fromordinal(int(ff[remaining > 0].max()))
        # EV + remanente puede diferir una ULP de sum(presupuestos).
        # El último acumulado debe ser exactamente el BAC compartido con PV.
        projection = np.minimum(projection, budget)
        projection[finish.toordinal() - origin:] = budget
    return _Control(origin, cut, planned, earned, actual, projection, budget, ev,
                    planned_start, planned_finish, finish, es, spi_t, evm_forecast_finish)
