"""Integración real CP-SAT, omitida únicamente si OR-Tools no está instalado."""
from datetime import date

import pytest

from ganttier.domain import ObjectiveWeights, OptimizeOptions, Relation
from ganttier.engine import optimize
from .test_optimize import case


@pytest.mark.parametrize("weights", [ObjectiveWeights(makespan=1, window=0), ObjectiveWeights(level=1, window=0), ObjectiveWeights(cost=1, window=0), ObjectiveWeights(window=1)])
def test_cpsat_real(weights):
    pytest.importorskip("ortools.sat.python.cp_model")
    p = case(4, duration=2, hours=16, overtime=8)
    p.relations = [Relation(1, 1, 1, 2, "SS", 1), Relation(2, 1, 2, 3, "FF", 1), Relation(3, 1, 3, 4, "FS", -1)]
    p.activities[3].window_start = date(2026, 1, 12)
    r = optimize(p, weights, OptimizeOptions(method="cpsat", time_limit_s=10, allow_overtime=False))
    assert r.method_used == "cpsat"
    assert r.status in {"optimo", "factible"}, r.warnings
    assert r.after.overload_hours == 0


def test_missing_cpsat_fallback(monkeypatch):
    import sys
    # Con OR-Tools instalado, el paquete padre ya está importado: se bloquea también el padre.
    monkeypatch.setitem(sys.modules, "ortools.sat.python.cp_model", None)
    monkeypatch.setitem(sys.modules, "ortools.sat.python", None)
    r = optimize(case(2), ObjectiveWeights(), OptimizeOptions(method="cpsat", time_limit_s=2))
    assert r.status == "heuristico"
    assert r.method_used == "heuristic"
    assert any("no está disponible" in v for v in r.warnings)
