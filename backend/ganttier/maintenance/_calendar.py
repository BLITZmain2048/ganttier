"""Contrato estructural y lectura segura de calendarios de A1."""
from __future__ import annotations

from datetime import date
from typing import Protocol

import numpy as np


class WorkCalendar(Protocol):
    """Interfaz pública definida en SPEC §5, sin dependencia de A1."""

    def is_workday(self, d: date) -> bool: ...
    def hours(self, d: date) -> float: ...
    def next_workday(self, d: date, include: bool = True) -> date: ...
    def prev_workday(self, d: date, include: bool = True) -> date: ...
    def add_workdays(self, start: date, n: int) -> date: ...
    def workdays_between(self, a: date, b: date) -> int: ...
    def workday_index(self) -> np.ndarray: ...
    def day_hours_vector(self) -> np.ndarray: ...


def calendar_hours(cal: WorkCalendar, start: int, end: int) -> np.ndarray:
    """Lee horas inclusivas; solo usa vectores si se conoce su origen."""
    origin = getattr(cal, "horizon_start", None)
    if isinstance(origin, date):
        offset = start - origin.toordinal()
        raw = np.asarray(cal.day_hours_vector(), dtype=float)
        mask = np.asarray(cal.workday_index(), dtype=bool)
        count = end - start + 1
        if raw.ndim == mask.ndim == 1 and len(raw) == len(mask) and offset >= 0 and offset + count <= len(raw):
            result = np.where(mask[offset:offset + count], raw[offset:offset + count], 0.0)
        else:
            result = _sample(cal, start, end)
    else:
        # El contrato no expone el origen del vector: no inferirlo por su longitud.
        result = _sample(cal, start, end)
    if not np.isfinite(result).all() or np.any(result < 0):
        raise ValueError("El calendario contiene horas negativas o no finitas.")
    return result


def _sample(cal: WorkCalendar, start: int, end: int) -> np.ndarray:
    return np.fromiter((cal.hours(date.fromordinal(d)) if cal.is_workday(date.fromordinal(d)) else 0.0
                        for d in range(start, end + 1)), dtype=float, count=end - start + 1)
