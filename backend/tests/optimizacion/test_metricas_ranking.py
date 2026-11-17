"""Plazos corridos y scores sin mezclar unidades físicas."""
import math

import pytest

from ganttier.optimizacion.metricas import normalizar, puntaje
from ganttier.optimizacion.ranking import clasificar


def test_calendar_makespan_and_missing_values():
    row = normalizar({"finish": "2026-01-12", "horizon_start": "2026-01-05", "load_std": 2})
    assert row["makespan_days"] == 7  # lunes a lunes, incluidos días no laborables.
    assert row["personnel_cost"] is None
    assert normalizar(None)["makespan_days"] is None
    assert normalizar({"finish": "2026-01-05", "horizon_start": "2026-01-05"})["makespan_days"] == 0


def test_unit_invariant_score_and_zero_baseline():
    def score(factor):
        metrics = normalizar({"before": {"load_std": 10 * factor, "makespan_days": 20},
                              "after": {"load_std": 5 * factor, "makespan_days": 10}})
        return puntaje(metrics, "level=1,makespan=.2")
    assert score(1) == pytest.approx(.4)
    assert score(1) == pytest.approx(score(1000))
    assert puntaje({"load_std": 0, "_before": {"load_std": 0}}, "level=1") == 0
    assert puntaje({"load_std": 1000, "_before": {"load_std": 0}}, "level=1") == 1
    with pytest.raises(ValueError, match="before"):
        puntaje({"load_std": 10}, "level=1")


@pytest.mark.parametrize("expression", ["", "unknown=1", "level=nan", "level=-1", "level=0", "level=1,level=2"])
def test_invalid_score(expression):
    with pytest.raises(ValueError):
        puntaje({}, expression)


def _row(identity, **metrics):
    return {"variant": {"id": identity}, "status": "ok", "feasible": True,
            "metrics": {"activities_out_of_window": 0, "overtime_hours": 0, "makespan_days": 7, "load_std": 1, **metrics}}


def test_default_and_custom_ranking_keep_failures_last():
    rows = [_row("late", makespan_days=20), _row("overtime", overtime_hours=2), _row("best"),
            {**_row("timeout", makespan_days=0), "status": "timeout", "feasible": False,
             "error": {"message": "Presupuesto agotado"}}]
    assert [r["variant"]["id"] for r in clasificar(rows)] == ["best", "late", "overtime", "timeout"]
    classified = clasificar(rows, "makespan_days,load_std")
    assert classified[-1]["rank_reason"] == "Presupuesto agotado"
    assert [r["rank"] for r in classified] == [1, 2, 3, 4]
    assert all("rank" not in r for r in rows)
    with pytest.raises(ValueError, match="desconocidos"):
        clasificar([], "invented")


def test_score_ranking_and_unmeasured_metrics():
    a, b, absent = _row("a", load_std=5), _row("b", load_std=9), _row("missing", load_std=None)
    for row in (a, b, absent):
        row["metrics"]["_before"] = {"load_std": 10}
    ranked = clasificar([absent, b, a], "score:level=1")
    assert [r["variant"]["id"] for r in ranked] == ["a", "b", "missing"]
    assert ranked[-1]["score"] is None and ranked[-1]["rank_reason"]
    assert all(math.isfinite(r["score"]) for r in ranked[:-1])
