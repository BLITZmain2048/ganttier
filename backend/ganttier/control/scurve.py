"""Muestreo diario, semanal o mensual de las curvas acumuladas."""
from __future__ import annotations

import calendar as month_calendar
from datetime import date

import numpy as np
from numpy.typing import NDArray

from ganttier.domain import ActivityView, BaselineActivity, ProgressEntry, SCurve
from ._core import WorkCalendar, _calculate


def _period_ends(start: int, end: int, period: str) -> NDArray:
    if period == "day":
        return np.arange(start, end + 1, dtype=np.int64)
    labels: list[int] = []
    cursor = start
    while cursor <= end:
        day = date.fromordinal(cursor)
        if period == "week":
            last = cursor + 6 - day.weekday()
        else:
            last = date(day.year, day.month, month_calendar.monthrange(day.year, day.month)[1]).toordinal()
        if last > date.max.toordinal():
            raise ValueError("El fin de período excede el rango de fechas permitido.")
        labels.append(last)
        cursor = last + 1
    return np.asarray(labels, dtype=np.int64)


def scurve(acts: list[ActivityView], baseline: dict[int, BaselineActivity] | None,
           progress: list[ProgressEntry], calendar: WorkCalendar, data_date: date,
           period: str = "week", basis: str = "hours", *,
           calendars: dict[int, WorkCalendar] | None = None) -> SCurve:
    """Devuelve acumulados al fin de cada período y proyección del programa vigente."""
    if period not in {"day", "week", "month"}:
        raise ValueError("El período debe ser 'day', 'week' o 'month'.")
    c = _calculate(acts, baseline, progress, calendar, data_date, basis, calendars=calendars)
    return from_calculation(c, bool(acts), data_date, period, basis)


def from_calculation(c, has_activities, data_date, period='week', basis='hours', *, max_points=None) -> SCurve:
    """Muestrea un cálculo diario ya resuelto, sin volver a recorrer actividades."""
    labels = _period_ends(c.origin, c.origin + len(c.planned) - 1, period) if has_activities else np.empty(0, dtype=np.int64)
    if labels.size:
        labels = np.union1d(labels, [c.cut])
    if max_points is not None and len(labels) > max_points:
        # Extremos de bloques de períodos, sin promediar acumulados. Conserva
        # inicio, término y corte; cada valor visible pertenece a la serie exacta.
        keep = np.linspace(0, len(labels) - 1, max_points - 1, dtype=np.int64)
        keep = np.union1d(keep, np.flatnonzero(labels == c.cut))
        labels = labels[keep]
    samples = np.minimum(labels - c.origin, len(c.planned) - 1)
    past, future = labels <= c.cut, labels >= c.cut
    scale = 100 / c.budget if c.budget else 0.0

    def series(values: NDArray, mask: NDArray, factor: float = 1.0) -> list[float | None]:
        return [float(v * factor) if ok else None for v, ok in zip(values[samples], mask)]

    return SCurve(
        period=period, basis=basis, labels=[date.fromordinal(int(d)).isoformat() for d in labels],
        planned_cum=(c.planned[samples] * scale).tolist(),
        earned_cum=series(c.earned, past, scale), actual_cum=series(c.actual, past, scale),
        forecast_cum=series(c.forecast, future, scale), planned_abs=c.planned[samples].tolist(),
        earned_abs=series(c.earned, past), actual_abs=series(c.actual, past),
        forecast_abs=series(c.forecast, future), budget_total=c.budget, data_date=data_date,
        forecast_finish=c.forecast_finish, planned_finish=c.planned_finish,
    )
