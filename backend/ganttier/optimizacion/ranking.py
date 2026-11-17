"""Clasificación estable, sin convertir ausencia de métricas en buen desempeño."""
from __future__ import annotations

import math

from .metricas import ALIASES, FIELDS, _terms, normalizar, puntaje

_DEFAULT = ("activities_out_of_window", "overtime_hours", "makespan_days", "load_std")


def _number(value: object) -> float:
    return float(value) if type(value) in (int, float) and math.isfinite(value) else math.inf


def _lex_key(row: dict) -> tuple:
    return (not (row.get("feasible") and row.get("status") == "ok"),
            *(_number((row.get("metrics") or {}).get(key)) for key in _DEFAULT),
            str((row.get("variant") or {}).get("id", "")))


def clasificar(resultados: list[dict], criterio: str = "lexicographic") -> list[dict]:
    """Añade rank y rank_reason en copias superficiales; factibles primero siempre.

    Admite campos separados por comas (ascendentes), o score:expresión. Los
    empates se resuelven por id. El score se escala respecto al before individual;
    por tanto compara mejora relativa, no costos absolutos de distintos escenarios.
    """
    if not isinstance(criterio, str) or not criterio.strip():
        raise ValueError("El criterio de ranking debe ser una cadena no vacía.")
    criterio = criterio.strip()
    expression = criterio[6:] if criterio.startswith("score:") else None
    if expression is not None:
        _terms(expression)
        keys = ()
    else:
        keys = _DEFAULT if criterio == "lexicographic" else tuple(ALIASES.get(k.strip(), k.strip()) for k in criterio.split(","))
        if any(key not in FIELDS[1:] and key != "feasible" for key in keys):
            raise ValueError(f"Campos de ranking desconocidos en {criterio!r}.")
    rows = []
    for source in resultados:
        row = dict(source)
        feasible = row.get("status") == "ok" and row.get("feasible") is True
        metrics = dict(row.get("metrics") or {})
        reason = None
        if not feasible:
            reason = ((row.get("error") or {}).get("message") or row.get("infeasibility")
                      or f"Resultado {row.get('status', 'sin estado')}; sin solución factible.")
        if expression is not None and feasible:
            if "_before" not in metrics:
                metrics["_before"] = normalizar({**(row.get("before") or {}),
                                                "horizon_start": row.get("horizon_start") or
                                                (row.get("variant") or {}).get("horizon_start")})
            try:
                row["score"] = puntaje(metrics, expression)
            except ValueError as exc:
                row["score"] = None
                reason = str(exc)
        row["rank_reason"] = reason
        rows.append(row)

    def key(row: dict) -> tuple:
        valid = row.get("status") == "ok" and row.get("feasible") is True
        values = ((_number(row.get("score")),) if expression is not None else
                  tuple((not row.get("feasible")) if field == "feasible" else
                        _number((row.get("metrics") or {}).get(field)) for field in keys))
        return (not valid, *values, str((row.get("variant") or {}).get("id", "")))

    rows.sort(key=key)
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows
