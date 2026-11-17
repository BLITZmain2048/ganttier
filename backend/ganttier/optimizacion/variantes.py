"""Variantes validadas y planes reproducibles, sin importar API ni motor.

Los rangos aleatorios numéricos de dos elementos son intervalos inclusivos
(uniformes para reales, randint para enteros); las demás listas son categorías.
Los presets completan con cero los pesos omitidos. Un weights explícito usa los
defaults de ObjectiveWeights y prevalece sobre objective, incluso si es {}.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from hashlib import sha256
from itertools import product
import json
import math
import random
import types
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from ..domain import ObjectiveWeights, OptimizeOptions

PRESETS: dict[str, dict[str, float]] = {
    "plazo": {"makespan": 1, "window": .2},
    "nivelar": {"level": 1, "window": 1},
    "ventanas": {"window": 1},
    "costo": {"cost": 1, "window": .5},
    "equilibrado": {"level": .5, "makespan": .3, "window": 1, "cost": .2},
    "pico": {"peak": 1, "level": .1, "window": .05},
    "peak": {"peak": 1, "level": .1, "window": .05},
}


def _object(value: Any, context: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{context} debe ser un objeto JSON.")
    return value


def _unknown(values: dict, allowed: set[str], context: str) -> None:
    extra = set(values) - allowed
    if extra:
        raise ValueError(f"Claves desconocidas en {context}: {', '.join(sorted(map(str, extra)))}.")


def _matches(value: Any, hint: Any) -> bool:
    origin, args = get_origin(hint), get_args(hint)
    if origin in (Union, types.UnionType):
        return any(_matches(value, item) for item in args)
    if origin is Literal:
        return value in args and type(value) in {type(item) for item in args}
    if hint is float:
        return type(value) in (int, float) and math.isfinite(value)
    if hint in (int, bool, str, type(None)):
        return type(value) is hint
    return True


def _domain(values: dict, cls: type, context: str) -> dict:
    values = deepcopy(_object(values, context))
    hints = get_type_hints(cls)
    _unknown(values, set(hints), context)
    for key, value in values.items():
        if not _matches(value, hints[key]):
            raise ValueError(f"{context}.{key}: valor o tipo inválido ({value!r}).")
    full = asdict(cls(**values))
    if cls is ObjectiveWeights:
        if any(v < 0 for v in full.values()):
            raise ValueError("Los pesos deben ser finitos y no negativos.")
        return {k: float(v) for k, v in full.items()}
    if full["time_limit_s"] <= 0 or full["cpsat_max_activities"] < 1:
        raise ValueError("options.time_limit_s y cpsat_max_activities deben ser positivos.")
    if not 0 <= full["seed"] <= 2147483647:
        raise ValueError("options.seed debe estar entre 0 y 2147483647.")
    if not 0 < full["block_hours"] <= 24:
        raise ValueError("options.block_hours debe estar entre 0 (exclusivo) y 24.")
    if full["horizon_blocks"] is not None and not 1 <= full["horizon_blocks"] <= 2000:
        raise ValueError("options.horizon_blocks debe estar entre 1 y 2000.")
    # No materializar defaults: solve_teams tiene allow_overtime=False propio.
    for key in ("time_limit_s", "block_hours"):
        if key in values:
            values[key] = float(values[key])
    return values


def _iso(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} debe ser una fecha YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser una fecha YYYY-MM-DD válida.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} debe ser una fecha YYYY-MM-DD.")
    return value


@dataclass
class Variante:
    """Parámetros de una resolución; el id automático excluye nombre y etiquetas."""

    id: str = ""
    name: str = ""
    objective: str | None = None
    weights: dict | None = None
    options: dict = field(default_factory=dict)
    horizon_start: str | None = None
    horizon_end: str | None = None
    regenerate: bool = False
    tags: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not isinstance(self.name, str):
            raise ValueError("id y name deben ser cadenas.")
        if self.objective is not None and (not isinstance(self.objective, str) or self.objective not in PRESETS):
            raise ValueError(f"Objetivo desconocido: {self.objective!r}; use {', '.join(PRESETS)}.")
        self.options = _domain(self.options, OptimizeOptions, "options")
        if self.weights is not None:
            self.weights = _domain(self.weights, ObjectiveWeights, "weights")
        self.horizon_start = _iso(self.horizon_start, "horizon_start")
        self.horizon_end = _iso(self.horizon_end, "horizon_end")
        if self.horizon_start and self.horizon_end and self.horizon_end < self.horizon_start:
            raise ValueError("horizon_end no puede ser anterior a horizon_start.")
        if type(self.regenerate) is not bool:
            raise ValueError("regenerate debe ser booleano.")
        self.tags = deepcopy(_object(self.tags, "tags"))
        try:
            json.dumps(self.tags, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("tags debe contener valores JSON finitos.") from exc
        if not self.id:
            self.id = self._fingerprint()
        if not self.name:
            self.name = self.objective or self.id

    def _fingerprint(self) -> str:
        weights, options = self.solve_args()
        raw = json.dumps({"weights": weights, "options": options}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Devuelve una copia JSON de la variante."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Variante:
        """Construye y valida estrictamente una variante del contrato CLI."""
        _object(d, "variante")
        _unknown(d, {f.name for f in fields(cls)}, "variante")
        return cls(**deepcopy(d))

    def solve_args(self) -> tuple[dict | None, dict]:
        """Entrega pesos efectivos y opciones con horizonte, como api/solve.py."""
        options = _domain(self.options, OptimizeOptions, "options")
        options.update(horizon_start=self.horizon_start, horizon_end=self.horizon_end,
                       regenerate=self.regenerate)
        weights = self.weights
        if weights is None and self.objective is not None:
            weights = {key: 0.0 for key in get_type_hints(ObjectiveWeights)}
            weights.update(PRESETS[self.objective])
        return (_domain(weights, ObjectiveWeights, "weights") if weights is not None else None, options)


def _merge(defaults: dict, value: dict) -> dict:
    result = deepcopy(defaults)
    for key, item in value.items():
        if key in {"options", "weights", "tags"} and isinstance(item, dict):
            result[key] = {**(result.get(key) or {}), **deepcopy(item)}
        else:
            result[key] = deepcopy(item)
    return result


def _paths(space: dict, name: str) -> list[str]:
    _object(space, name)
    allowed = {f"{prefix}.{key}" for prefix, cls in (("weights", ObjectiveWeights), ("options", OptimizeOptions))
               for key in get_type_hints(cls)} | {"objective", "horizon_start", "horizon_end", "regenerate"}
    _unknown(space, allowed, name)
    for key, values in space.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"{name}.{key} debe ser una lista no vacía.")
    return sorted(space)


def _assign(values: dict, key: str, value: Any) -> None:
    if "." in key:
        parent, child = key.split(".")
        if values.get(parent) is None:
            values[parent] = {}
        values.setdefault(parent, {})[child] = value
    else:
        values[key] = value


def expandir_plan(plan: dict) -> list[Variante]:
    """Expande variants + producto grid + random local; elimina parámetros duplicados.

    Orden estable: variantes explícitas, grid con claves ordenadas, muestras. No
    modifica el plan ni el generador aleatorio global. Los ids explícitos que
    colisionen con parámetros distintos se rechazan.
    """
    _object(plan, "plan")
    _unknown(plan, {"project", "defaults", "variants", "grid", "random", "rank_by", "parallel",
                    "variant_timeout_s", "budget_s"}, "plan")
    if "project" in plan and (type(plan["project"]) not in (int, str) or not str(plan["project"]).strip()):
        raise ValueError("plan.project debe ser un id o nombre de proyecto.")
    for key in ("parallel", "variant_timeout_s", "budget_s"):
        if key in plan and (type(plan[key]) not in (int, float) or not math.isfinite(plan[key]) or plan[key] <= 0):
            raise ValueError(f"plan.{key} debe ser positivo y finito.")
    if "parallel" in plan and type(plan["parallel"]) is not int:
        raise ValueError("plan.parallel debe ser entero.")
    if "rank_by" in plan:
        from .ranking import clasificar
        clasificar([], plan["rank_by"])
    defaults = _object(plan.get("defaults", {}), "defaults")
    _unknown(defaults, {f.name for f in fields(Variante)} - {"id", "name"}, "defaults")
    Variante.from_dict(defaults)
    variants = plan.get("variants", [])
    if not isinstance(variants, list):
        raise ValueError("variants debe ser una lista.")
    out, seen, ids = [], set(), {}

    def add(value: dict) -> None:
        variant = Variante.from_dict(_merge(defaults, _object(value, "variante")))
        fingerprint = variant._fingerprint()
        if variant.id in ids and ids[variant.id] != fingerprint:
            raise ValueError(f"Id de variante repetido con parámetros distintos: {variant.id}.")
        ids[variant.id] = fingerprint
        if fingerprint not in seen:
            seen.add(fingerprint)
            out.append(variant)

    for value in variants:
        add(value)
    if "grid" in plan:
        grid = plan["grid"]
        keys = _paths(grid, "grid")
        for combination in product(*(grid[key] for key in keys)):
            value: dict = {}
            for key, item in zip(keys, combination):
                _assign(value, key, item)
            add(value)
    if "random" in plan:
        random_plan = _object(plan["random"], "random")
        _unknown(random_plan, {"n", "seed", "space"}, "random")
        n, seed = random_plan.get("n"), random_plan.get("seed", 0)
        if type(n) is not int or n < 1 or type(seed) is not int or seed < 0:
            raise ValueError("random.n debe ser entero positivo y random.seed entero no negativo.")
        space = random_plan.get("space", {})
        keys = _paths(space, "random.space")
        # Validar también categorías que la semilla podría no seleccionar.
        for key in keys:
            for candidate in space[key]:
                if "." in key:
                    prefix, child = key.split(".")
                    _domain({child: candidate}, ObjectiveWeights if prefix == "weights" else OptimizeOptions, prefix)
                else:
                    Variante.from_dict({key: candidate})
        rng = random.Random(seed)
        integer_keys = {f"options.{key}" for key, hint in get_type_hints(OptimizeOptions).items()
                        if hint is int or int in get_args(hint)}
        for _ in range(n):
            value = {"tags": {"random_seed": seed}}
            for key in keys:
                choices = space[key]
                if len(choices) == 2 and all(type(x) in (int, float) for x in choices):
                    low, high = choices
                    if not all(math.isfinite(x) for x in choices) or low > high:
                        raise ValueError(f"random.space.{key}: intervalo inválido.")
                    if key in integer_keys:
                        if any(type(x) is not int for x in choices):
                            raise ValueError(f"random.space.{key} requiere límites enteros.")
                        item = rng.randint(low, high)
                    else:
                        item = rng.uniform(low, high)
                else:
                    item = rng.choice(choices)
                _assign(value, key, item)
            add(value)
    if not any(key in plan for key in ("variants", "grid", "random")):
        add({})
    return out


def _type_schema(hint: Any) -> dict:
    origin, args = get_origin(hint), get_args(hint)
    if origin in (Union, types.UnionType):
        return {"anyOf": [_type_schema(item) for item in args]}
    if origin is Literal:
        return {"type": "string", "enum": list(args)}
    return {"type": {float: "number", int: "integer", str: "string", bool: "boolean", type(None): "null"}[hint]}


def _domain_schema(cls: type) -> dict:
    props = {}
    for key, hint in get_type_hints(cls).items():
        props[key] = {**_type_schema(hint), "default": getattr(cls(), key)}
        if cls is ObjectiveWeights or key == "seed":
            props[key]["minimum"] = 0
        if key in {"time_limit_s", "block_hours"}:
            props[key]["exclusiveMinimum"] = 0
        if key == "cpsat_max_activities":
            props[key]["minimum"] = 1
        if key == "block_hours":
            props[key]["maximum"] = 24
        if key == "seed":
            props[key]["maximum"] = 2147483647
        if key == "horizon_blocks":
            props[key].update(minimum=1, maximum=2000)
    return {"type": "object", "additionalProperties": False, "properties": props}


def esquema_variante() -> dict:
    """JSON Schema de Variante, derivado de los campos del dominio."""
    nullable_date = {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]}
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Variante",
            "type": "object", "additionalProperties": False, "properties": {
                "id": {"type": "string"}, "name": {"type": "string"},
                "objective": {"enum": [*PRESETS, None]},
                "weights": {"anyOf": [_domain_schema(ObjectiveWeights), {"type": "null"}]},
                "options": _domain_schema(OptimizeOptions), "horizon_start": deepcopy(nullable_date),
                "horizon_end": deepcopy(nullable_date), "regenerate": {"type": "boolean", "default": False},
                "tags": {"type": "object"}}}


def esquema_plan() -> dict:
    """JSON Schema de planes, incluidos espacios cartesianos y aleatorios."""
    variant = esquema_variante()
    defaults = deepcopy(variant)
    for key in ("id", "name"):
        defaults["properties"].pop(key)
    space_props = {}
    for prefix, cls in (("weights", ObjectiveWeights), ("options", OptimizeOptions)):
        for key, schema in _domain_schema(cls)["properties"].items():
            space_props[f"{prefix}.{key}"] = {"type": "array", "minItems": 1, "items": schema}
    for key in ("objective", "horizon_start", "horizon_end", "regenerate"):
        space_props[key] = {"type": "array", "minItems": 1, "items": variant["properties"][key]}
    space = {"type": "object", "additionalProperties": False, "properties": space_props}
    return {"$schema": variant["$schema"], "title": "Plan", "type": "object", "additionalProperties": False,
            "properties": {"project": {"type": ["integer", "string"]}, "defaults": defaults,
                           "variants": {"type": "array", "items": variant}, "grid": space,
                           "random": {"type": "object", "additionalProperties": False, "required": ["n"],
                                      "properties": {"n": {"type": "integer", "minimum": 1},
                                                     "seed": {"type": "integer", "minimum": 0, "default": 0}, "space": space}},
                           "rank_by": {"type": "string", "default": "lexicographic"},
                           "parallel": {"type": "integer", "minimum": 1},
                           "variant_timeout_s": {"type": "number", "exclusiveMinimum": 0},
                           "budget_s": {"type": "number", "exclusiveMinimum": 0}}}
