"""Calendarios vectorizados; las operaciones de fecha usan prefijos y búsqueda binaria."""
from __future__ import annotations

import re
import math
import sqlite3
import warnings
from datetime import date, timedelta
from functools import lru_cache

import numpy as np

from .domain import Calendar, CalendarException

# Feriados nacionales recurrentes y fechas móviles, respaldo sin dependencia externa.
_FIXED = "01-01 05-01 05-21 07-16 08-15 09-18 09-19 11-01 12-08 12-25".split()
_MOVABLE = {
    2024: "03-29 03-30 06-20 06-29 09-20 10-12 10-31",
    2025: "04-18 04-19 06-20 06-29 10-12 10-31",
    2026: "04-03 04-04 06-21 06-29 10-12 10-31",
    2027: "03-26 03-27 06-21 06-28 09-17 10-11 10-31",
    2028: "04-14 04-15 06-20 06-26 09-18 10-09 10-27",
    2029: "03-30 03-31 06-20 07-02 09-17 10-15 11-02",
    2030: "04-19 04-20 06-21 06-29 09-20 10-12 10-31",
}


@lru_cache(maxsize=128)
def _holiday_dates(country: str, first: int, last: int) -> dict[date, str]:
    try:
        import holidays
    except ImportError:
        if country != "CL":
            raise ValueError("Instale holidays para utilizar feriados de otros países.")
        if first < 2024 or last > 2030:
            warnings.warn("El respaldo de feriados solo cubre 2024–2030; instale holidays para otros años.", RuntimeWarning, stacklevel=2)
        return {date.fromisoformat(f"{year}-{md}"): "Feriado nacional (respaldo)"
                for year in range(max(2024, first), min(2030, last) + 1)
                for md in _FIXED + _MOVABLE[year].split()}
    try:
        return dict(holidays.country_holidays(country, years=range(first, last + 1)))
    except (NotImplementedError, KeyError) as exc:
        raise ValueError(f"El país de feriados {country} no está soportado.") from exc


class WorkCalendar:
    """Índices públicos relativos al horizonte; margen interno de dos años por lado."""

    def __init__(self, cal: Calendar, horizon_start: date, horizon_end: date):
        if horizon_end < horizon_start:
            raise ValueError("El fin del horizonte debe ser posterior al inicio.")
        if not re.fullmatch("[01]{7}", cal.workweek) or cal.hours_per_day < 0 or not math.isfinite(cal.hours_per_day):
            raise ValueError("Calendario inválido: semana de siete dígitos y horas no negativas.")
        self.cal = cal
        self.horizon_start, self.horizon_end = horizon_start, horizon_end
        def year_margin(day: date, offset: int) -> date:
            year = min(9999, max(1, day.year + offset))
            try:
                return day.replace(year=year)
            except ValueError:  # 29 de febrero al desplazar a un año no bisiesto.
                return day.replace(year=year, day=28)
        self._start = year_margin(horizon_start, -2)
        self._end = year_margin(horizon_end, 2)
        ordinals = np.arange(self._start.toordinal(), self._end.toordinal() + 1)
        if cal.shift_pattern:
            match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", cal.shift_pattern)
            if not match or cal.shift_anchor is None:
                raise ValueError("El turno requiere patrón NxM positivo y fecha de ancla.")
            on, off = map(int, match.groups())
            working = (ordinals - cal.shift_anchor.toordinal()) % (on + off) < on
        else:
            working = np.array([c == "1" for c in cal.workweek])[(ordinals - 1) % 7]
        self.holidays = {}
        if cal.holidays_country and (not cal.shift_pattern or cal.holidays_apply_to_shift):
            self.holidays = _holiday_dates(cal.holidays_country, self._start.year, self._end.year)
            indices = [d.toordinal() - self._start.toordinal() for d in self.holidays if self._start <= d <= self._end]
            working[np.asarray(indices, dtype=int)] = False
        self._hours = working.astype(float) * cal.hours_per_day
        self.exceptions = {e.date: e for e in cal.exceptions}
        for d, ex in self.exceptions.items():
            if ex.hours < 0 or not math.isfinite(ex.hours):
                raise ValueError("Las horas de una excepción no pueden ser negativas.")
            if self._start <= d <= self._end:
                self._hours[(d - self._start).days] = ex.hours if ex.working else 0
        self._working = self._hours > 0
        self._prefix = np.r_[0, np.cumsum(self._working, dtype=np.int64)]
        self._indices = np.flatnonzero(self._working)
        self._slice = slice((horizon_start - self._start).days, (horizon_end - self._start).days + 1)

    def _index(self, d: date) -> int:
        if not self._start <= d <= self._end:
            raise ValueError(f"Fecha {d.isoformat()} fuera del horizonte del calendario y su margen.")
        return (d - self._start).days

    def _date_at(self, pos: int) -> date:
        if pos < 0 or pos >= len(self._indices):
            raise ValueError("No hay suficientes días laborables dentro del horizonte del calendario.")
        return self._start + timedelta(days=int(self._indices[pos]))

    def is_workday(self, d: date) -> bool:
        """Indica si el día tiene horas disponibles."""
        return bool(self._working[self._index(d)])

    def hours(self, d: date) -> float:
        """Devuelve las horas del día, incluidas las excepciones."""
        return float(self._hours[self._index(d)])

    def next_workday(self, d: date, include: bool = True) -> date:
        """Primer día laborable desde d (o después de d)."""
        return self._date_at(int(np.searchsorted(self._indices, self._index(d), side="left" if include else "right")))

    def prev_workday(self, d: date, include: bool = True) -> date:
        """Último día laborable hasta d (o antes de d)."""
        return self._date_at(int(np.searchsorted(self._indices, self._index(d), side="right" if include else "left")) - 1)

    def add_workdays(self, start: date, n: int) -> date:
        """Fin inclusivo de n días; cero conserva la fecha del hito."""
        if not isinstance(n, (int, np.integer)) or n < 0:
            raise ValueError("La duración debe ser un entero no negativo.")
        if n == 0:
            return start
        pos = int(np.searchsorted(self._indices, self._index(start)))
        return self._date_at(pos + n - 1)

    def workdays_between(self, a: date, b: date) -> int:
        """Cuenta días en [a,b]; un intervalo invertido está vacío."""
        if b < a:
            return 0
        return int(self._prefix[self._index(b) + 1] - self._prefix[self._index(a)])

    def workday_index(self) -> np.ndarray:
        """Vector booleano del horizonte solicitado, sin el margen interno."""
        result = self._working[self._slice].view()
        result.flags.writeable = False
        return result

    def day_hours_vector(self) -> np.ndarray:
        """Vector de horas del horizonte solicitado, sin el margen interno."""
        result = self._hours[self._slice].view()
        result.flags.writeable = False
        return result


def ensure_default_calendars(conn: sqlite3.Connection) -> None:
    """Crea los cinco calendarios iniciales sin sobrescribir cambios del usuario."""
    with conn:
        conn.executemany("""INSERT OR IGNORE INTO calendars
            (name,workweek,hours_per_day,holidays_country,shift_pattern,shift_anchor)
            VALUES (?,?,?,?,?,?)""", [
            ("Chile L-V 8h", "1111100", 8, "CL", None, None),
            ("Chile L-S 8h", "1111110", 8, "CL", None, None),
            ("Turno 7x7 12h", "1111111", 12, "CL", "7x7", "2026-01-01"),
            ("Turno 4x3 10h", "1111111", 10, "CL", "4x3", "2026-01-01"),
            ("Corrido 7 días", "1111111", 8, None, None, None),
        ])


def load_calendars(conn: sqlite3.Connection) -> dict[int, WorkCalendar]:
    """Carga calendarios y excepciones en dos SELECT y cubre los proyectos existentes."""
    from . import repo
    projects = repo.list_entities(conn, "projects")
    start = min((p.start_date for p in projects), default=date.today())
    end = max((p.end_date or p.start_date + timedelta(days=5 * 366) for p in projects), default=start + timedelta(days=366))
    return {cal.id: WorkCalendar(cal, start, end) for cal in repo.list_entities(conn, "calendars")}
