"""Verificación numérica y casos no calculables de pronósticos alternativos."""
from dataclasses import replace
from datetime import date

import pytest

from ganttier.control.forecasts import forecasts
from ganttier.domain import EVMResult


@pytest.fixture
def metrics():
    return EVMResult(data_date=date(2026, 1, 10), basis="hours", BAC=100, PV=50,
                     EV=40, AC=50, SV=-10, CV=-10, SPI=.8, CPI=.8, ES_days=8,
                     SPI_t=.8, EAC=125, ETC=75, VAC=-25, TCPI=1.2,
                     planned_finish=date(2026, 1, 20), forecast_finish=None,
                     percent_planned=50, percent_complete=40)


def test_numeric_scenarios_and_inclusive_dates(metrics):
    original = replace(metrics)
    rows = {row.name: row for row in forecasts(metrics, 65, 20, 10)}
    expected = {"EAC_CPI": 125, "EAC_presupuesto": 110, "EAC_combinado": 143.75,
                "EAC_reestimado": 115, "IEAC_t": 25, "duracion_remanente": 22}
    for name, value in expected.items():
        assert rows[name].value == pytest.approx(value)
        assert rows[name].status == "calculado"
        assert rows[name].formula
        if name.startswith("EAC"):
            assert rows["TCPI_" + name].value == pytest.approx(60 / (value - 50))
    assert len(rows) == 10
    assert rows["IEAC_t"].finish_date == date(2026, 1, 25)
    assert rows["duracion_remanente"].finish_date == date(2026, 1, 22)
    assert metrics == original


@pytest.mark.parametrize("field,value,names", [
    ("CPI", 0, ["EAC_CPI", "EAC_combinado"]),
    ("CPI", None, ["EAC_CPI", "EAC_combinado"]),
    ("SPI", 0, ["EAC_combinado"]),
    ("SPI_t", 0, ["IEAC_t"]),
    ("SPI_t", None, ["IEAC_t"]),
    ("ES_days", None, ["duracion_remanente"]),
])
def test_unavailable_inputs(metrics, field, value, names):
    rows = {row.name: row for row in forecasts(replace(metrics, **{field: value}), None, 20, 10)}
    for name in names + ["EAC_reestimado", "TCPI_EAC_reestimado"]:
        assert rows[name].value is None
        assert rows[name].status == "no calculable"
        assert rows[name].cause


def test_tcpi_zero_remaining_budget_and_missing_duration(metrics):
    rows = {row.name: row for row in forecasts(metrics, 0, None, None)}
    assert rows["TCPI_EAC_reestimado"].value is None
    assert "cero" in rows["TCPI_EAC_reestimado"].cause
    assert rows["IEAC_t"].status == "no calculable"
    assert rows["duracion_remanente"].status == "no calculable"


def test_missing_origin_fractional_duration_and_date_overflow(metrics):
    result = replace(metrics, planned_finish=None, SPI_t=.9)
    rows = {r.name: r for r in forecasts(result, 65, 20, 10)}
    assert rows["IEAC_t"].value == pytest.approx(20 / .9)
    assert rows["IEAC_t"].finish_date is None
    assert rows["IEAC_t"].date_cause
    rows = {r.name: r for r in forecasts(result, 65, 20, 10, baseline_start=date(2026, 1, 1))}
    assert rows["IEAC_t"].finish_date == date(2026, 1, 23)
    rows = {r.name: r for r in forecasts(result, 65, 20, 10, baseline_start=date.max)}
    assert rows["IEAC_t"].finish_date is None
    assert rows["IEAC_t"].date_cause


def test_nonfinite_input_does_not_escape_to_json(metrics):
    rows = forecasts(replace(metrics, CPI=float("nan")), float("inf"), 20, 10)
    assert next(r for r in rows if r.name == "EAC_CPI").status == "no calculable"
    assert next(r for r in rows if r.name == "EAC_reestimado").status == "no calculable"
