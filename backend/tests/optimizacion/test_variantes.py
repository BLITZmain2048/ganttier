"""Contrato EJE: validación estricta, composición e identidad reproducible."""
from copy import deepcopy
import json
import random

import pytest

from ganttier.optimizacion.variantes import PRESETS, Variante, expandir_plan, esquema_plan, esquema_variante


def test_presets_and_explicit_weights():
    for name, expected in PRESETS.items():
        weights, options = Variante(objective=name).solve_args()
        assert weights == {**dict.fromkeys(("level", "makespan", "window", "cost", "peak"), 0), **expected}
        assert options["regenerate"] is False
    v = Variante.from_dict({"objective": "plazo", "weights": {"level": 2}, "options": {"seed": 4}})
    assert v.solve_args()[0] == {"level": 2, "makespan": 0, "window": 1, "cost": 0, "peak": 0}
    assert Variante(weights={}).solve_args()[0]["window"] == 1
    assert "allow_overtime" not in Variante().solve_args()[1]


def test_stable_identity_and_roundtrip():
    a = Variante(options={"seed": 7, "time_limit_s": 2}, horizon_start="2026-01-01")
    b = Variante(options={"time_limit_s": 2.0, "seed": 7}, horizon_start="2026-01-01", tags={"arbitrary": "label"}, name="Otra")
    assert a.id == b.id
    assert Variante.from_dict(a.to_dict()).to_dict() == a.to_dict()
    assert Variante(options=a.options, horizon_start="2026-01-02").id != a.id
    assert Variante(options=a.options, regenerate=True).id != a.id
    assert len(a.id) == 16


@pytest.mark.parametrize("data,match", [
    ({"unknown": 1}, "desconocidas"), ({"options": {"workers": 2}}, "workers"),
    ({"weights": {"typo": 1}}, "typo"), ({"options": {"seed": True}}, "seed"),
    ({"options": {"allow_overtime": "false"}}, "allow_overtime"),
    ({"options": {"method": "foo"}}, "method"), ({"options": {"time_limit_s": 0}}, "positivos"),
    ({"weights": {"level": float("nan")}}, "level"), ({"weights": {"cost": -1}}, "negativos"),
    ({"regenerate": 1}, "booleano"), ({"horizon_start": "20260101"}, "YYYY"),
    ({"horizon_start": "2026-02-01", "horizon_end": "2026-01-01"}, "anterior"),
    ({"objective": "unknown"}, "desconocido"), ({"options": {"horizon_blocks": 2001}}, "2000"),
])
def test_reject_invalid(data, match):
    with pytest.raises(ValueError, match=match):
        Variante.from_dict(data)


def test_grid_random_defaults_dedup_no_mutation():
    plan = {"defaults": {"options": {"method": "heuristic", "time_limit_s": 2}},
            "variants": [{"options": {"seed": 0}}, {"options": {"seed": 0}, "name": "Duplicada"}],
            "grid": {"options.seed": [0, 1, 2]},
            "random": {"n": 5, "seed": 7, "space": {"weights.level": [0, 1], "options.seed": [10, 100]}}}
    original = deepcopy(plan)
    state = random.getstate()
    variants = expandir_plan(plan)
    assert len(variants) == 8
    assert plan == original and random.getstate() == state
    assert [v.to_dict() for v in variants] == [v.to_dict() for v in expandir_plan(plan)]
    assert all(v.options["method"] == "heuristic" for v in variants)
    assert len({v.id for v in variants}) == 8
    assert all(0 <= v.weights["level"] <= 1 for v in variants[3:])


@pytest.mark.parametrize("plan,match", [
    ({"typo": []}, "typo"), ({"grid": {"options.typo": [1]}}, "typo"),
    ({"grid": {"weights.level": []}}, "no vacía"), ({"parallel": 1.5}, "entero"),
    ({"random": {"n": 2, "space": {"options.seed": [2, 1]}}}, "intervalo"),
    ({"variants": [{"id": "same", "options": {"seed": 1}}, {"id": "same", "options": {"seed": 2}}]}, "Id"),
    ({"rank_by": "foo"}, "ranking"),
])
def test_invalid_plans(plan, match):
    with pytest.raises(ValueError, match=match):
        expandir_plan(plan)


def test_schemas_are_serializable_and_forbid_unknown_keys():
    for schema in (esquema_plan(), esquema_variante()):
        json.dumps(schema, allow_nan=False)
        assert schema["additionalProperties"] is False
    assert "seed" in esquema_variante()["properties"]["options"]["properties"]
    assert len(expandir_plan({"grid": {"options.seed": list(range(10000))}})) == 10000


def test_random_rejects_invalid_unsampled_category():
    with pytest.raises(ValueError, match="method"):
        expandir_plan({"random": {"n": 1, "seed": 1, "space": {"options.method": ["heuristic", "typo"]}}})
