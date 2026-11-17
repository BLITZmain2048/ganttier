"""Pronósticos alternativos puros, sin sustituir el EVM vigente del proyecto."""
from __future__ import annotations

from datetime import date, timedelta
from math import ceil, isfinite

from ganttier.domain import EVMResult, ForecastScenario


def forecasts(evm: EVMResult, etc_estimate: float | None,
              planned_duration_days: float | None, actual_time_days: float | None,
              *, baseline_start: date | None = None) -> list[ForecastScenario]:
    """Calcula EAC, TCPI de cada EAC y dos duraciones en días corridos.

    El inicio planificado cuenta como día 1: fecha = inicio + ceil(duración) - 1.
    Si no se entrega el inicio, se infiere del fin planificado y PD entero.
    Una duración calculable puede carecer de fecha; date_cause explica el motivo.
    """
    result: list[ForecastScenario] = []

    def scenario(name, formula, inputs, calculate, denominator=None):
        cause = None
        if any(value is None for value in inputs):
            cause = "Falta un dato requerido: " + formula + "."
        elif not all(isfinite(value) for value in inputs):
            cause = "Los datos deben ser números finitos."
        elif denominator is not None and denominator == 0:
            cause = "El denominador es cero: " + formula + "."
        value = None if cause else calculate()
        if value is not None and not isfinite(value):
            value, cause = None, "El resultado excede el rango numérico."
        item = ForecastScenario(name, formula, value,
                                "no calculable" if cause else "calculado", cause)
        result.append(item)
        return item

    bac, ev, ac, cpi, spi = evm.BAC, evm.EV, evm.AC, evm.CPI, evm.SPI
    product = cpi * spi if cpi is not None and spi is not None else None
    estimates = [
        scenario("EAC_CPI", "BAC/CPI", [bac, cpi], lambda: bac / cpi, cpi),
        scenario("EAC_presupuesto", "AC+(BAC−EV)", [ac, bac, ev], lambda: ac + bac - ev),
        scenario("EAC_combinado", "AC+(BAC−EV)/(CPI·SPI)", [ac, bac, ev, product],
                 lambda: ac + (bac - ev) / product, product),
        scenario("EAC_reestimado", "AC+ETC", [ac, etc_estimate], lambda: ac + etc_estimate),
    ]
    for estimate in estimates:
        denominator = estimate.value - ac if estimate.value is not None and ac is not None else None
        scenario("TCPI_" + estimate.name, "(BAC−EV)/(" + estimate.name + "−AC)",
                 [bac, ev, ac, estimate.value], lambda: (bac - ev) / denominator, denominator)

    pd, at, es, spi_t = planned_duration_days, actual_time_days, evm.ES_days, evm.SPI_t
    durations = [
        scenario("IEAC_t", "PD/SPI(t)", [pd, spi_t], lambda: pd / spi_t, spi_t),
        scenario("duracion_remanente", "AT+(PD−ES)", [at, pd, es], lambda: at + pd - es),
    ]
    if baseline_start is None and evm.planned_finish and pd is not None and isfinite(pd) and pd > 0:
        if float(pd).is_integer() and pd <= evm.planned_finish.toordinal():
            baseline_start = evm.planned_finish - timedelta(days=int(pd) - 1)
    for item in durations:
        if item.value is None:
            continue
        if item.value < 0 or (pd is not None and pd < 0) or (at is not None and at < 0):
            item.value, item.status, item.cause = None, "no calculable", "Los tiempos no pueden ser negativos."
        elif baseline_start is None:
            item.date_cause = "Falta el inicio planificado de la línea base o del programa vigente."
        else:
            ordinal = baseline_start.toordinal() + max(0, ceil(item.value - 1e-10) - 1)
            if ordinal > date.max.toordinal():
                item.date_cause = "La fecha pronosticada excede el rango de fechas."
            else:
                item.finish_date = date.fromordinal(ordinal)
    return result
