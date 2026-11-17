"""Indicadores de valor ganado al cierre de la fecha de corte."""
from __future__ import annotations

from datetime import date

from ganttier.domain import ActivityView, BaselineActivity, EVMResult, ProgressEntry
from ._core import WorkCalendar, _calculate, _ratio


def evm(acts: list[ActivityView], baseline: dict[int, BaselineActivity] | None,
        progress: list[ProgressEntry], calendar: WorkCalendar, data_date: date,
        basis: str = "hours", *, calendars: dict[int, WorkCalendar] | None = None) -> EVMResult:
    """Calcula EVM y Earned Schedule usando el avance vigente y consumos registrados."""
    c = _calculate(acts, baseline, progress, calendar, data_date, basis, forecast=False, calendars=calendars)
    return from_calculation(c, data_date, basis)


def from_calculation(c, data_date, basis='hours') -> EVMResult:
    """EVM del cálculo común, conservando el pronóstico estadístico original."""
    i = c.cut - c.origin
    pv, ac, ev, bac = float(c.planned[i]), float(c.actual[i]), c.ev, c.budget
    cpi = _ratio(ev, ac)
    if ev >= bac:
        eac = ac
    elif cpi is not None and cpi > 0:
        eac = bac / cpi
    elif ev == ac == 0:
        eac = bac  # Estimación inicial, aún sin evidencia de desempeño.
    else:
        eac = None  # CPI cero/indefinido no justifica cambiar a PF=1.
    return EVMResult(
        data_date=data_date, basis=basis, BAC=bac, PV=pv, EV=ev, AC=ac,
        SV=ev - pv, CV=ev - ac, SPI=_ratio(ev, pv), CPI=cpi,
        ES_days=c.es, SPI_t=c.spi_t, EAC=eac,
        ETC=eac - ac if eac is not None else None,
        VAC=bac - eac if eac is not None else None,
        TCPI=_ratio(bac - ev, bac - ac) if bac > ac else None, planned_finish=c.planned_finish,
        forecast_finish=c.evm_forecast_finish,
        percent_planned=100 * pv / bac if bac else 0.0,
        percent_complete=100 * ev / bac if bac else 0.0,
    )
