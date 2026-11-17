"""Métricas comparables; los valores no medidos se representan por None.

makespan_days = (finish - horizon_start).days: días corridos transcurridos,
sin sumar uno (un hito en el origen tiene plazo cero). No son jornadas hábiles.
Para conservar contexto se admite {before, after, horizon_start}; el trabajador
usa esta forma. Un OptimizeMetrics aislado necesita horizon_start para el plazo.

El score es la suma de pesos por x/(abs(before)+abs(x)); a igualdad del valor
inicial vale 0.5. Es adimensional e invariante a cambios de unidad. Si before y
x son cero vale 0; si sólo before es cero, un deterioro positivo vale 1.
Sin valor before medido no se inventa una escala: puntaje lanza ValueError.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date
import math

FIELDS = ("finish", "makespan_days", "peak_load", "load_std", "overload_hours", "overtime_hours",
          "overtime_cost", "days_out_of_window", "activities_out_of_window", "personnel_cost")
ALIASES = {"level": "load_std", "makespan": "makespan_days", "window": "days_out_of_window", "cost": "overtime_cost"}


def _metrics(value: dict | None, origin: str | None) -> dict:
    if is_dataclass(value):
        value = asdict(value)
    value = value or {}
    if not isinstance(value, dict):
        raise ValueError("Las métricas deben ser un objeto o null.")
    result = {key: value.get(key) for key in FIELDS}
    finish = result["finish"]
    if isinstance(finish, date):
        finish = finish.isoformat()
    result["finish"] = finish
    origin = value.get("horizon_start") or origin
    if origin and finish:
        first = origin if isinstance(origin, date) else date.fromisoformat(origin)
        result["makespan_days"] = (date.fromisoformat(finish) - first).days
    for key in FIELDS[1:]:
        item = result[key]
        if item is not None and (type(item) not in (int, float) or not math.isfinite(item)):
            raise ValueError(f"Métrica {key} debe ser numérica y finita, o null.")
    return result


def normalizar(metrics: dict | None) -> dict:
    """Mapea OptimizeMetrics o {before, after, horizon_start} al contrato §5.3."""
    if is_dataclass(metrics):
        metrics = asdict(metrics)
    metrics = metrics or {}
    origin = metrics.get("horizon_start")
    if "after" in metrics or "before" in metrics:
        result = _metrics(metrics.get("after"), origin)
        result["_before"] = _metrics(metrics.get("before"), origin)
    else:
        result = _metrics(metrics, origin)
        if "_before" in metrics:
            result["_before"] = _metrics(metrics["_before"], origin)
    result["_score_scale"] = "x/(abs(before)+abs(x)); 0/0=0"
    return result


def _terms(expresion: str) -> list[tuple[str, float]]:
    if not isinstance(expresion, str) or not expresion.strip():
        raise ValueError("El puntaje requiere una expresión como level=1,makespan=.2.")
    out, seen = [], set()
    for term in expresion.split(","):
        key, sep, raw = term.strip().partition("=")
        key = ALIASES.get(key.strip(), key.strip())
        if not sep or key not in FIELDS[1:] or key in seen:
            raise ValueError(f"Término de puntaje desconocido o repetido: {term!r}.")
        try:
            weight = float(raw)
        except ValueError as exc:
            raise ValueError(f"Peso de puntaje inválido: {raw!r}.") from exc
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("Los pesos del puntaje deben ser finitos y no negativos.")
        seen.add(key)
        out.append((key, weight))
    if not any(weight > 0 for _, weight in out):
        raise ValueError("El puntaje requiere al menos un peso positivo.")
    return out


def puntaje(metrics: dict, expresion: str) -> float:
    """Puntaje adimensional (menor es mejor), relativo a _before; véase el módulo."""
    before = metrics.get("_before") or metrics.get("before") or {}
    score = 0.0
    for key, weight in _terms(expresion):
        if not weight:
            continue
        value, baseline = metrics.get(key), before.get(key)
        if value is None or baseline is None:
            raise ValueError(f"Falta {key} o su valor before para escalar el puntaje.")
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (value, baseline)):
            raise ValueError(f"Métrica {key} no finita.")
        denominator = abs(baseline) + abs(value)
        score += weight * (value / denominator if denominator else 0.0)
    return score
