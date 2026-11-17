"""Pure validation and edit semantics shared by dialogs, page and tests."""
from datetime import date
import math
import re

READONLY = {"id", "project_id", "early_start", "early_finish", "late_start", "late_finish",
            "total_float", "free_float", "critical", "periodic_task_id", "occurrence_index",
            "actual_hours", "actual_cost", "status"}


def normalize_activity(values):
    result = {k: v for k, v in values.items() if k not in READONLY}
    for key in ("voltage_kv", "remaining_duration"):
        if key in result:
            value = result[key]
            if value in (None, ""):
                result[key] = None
            else:
                number = float(str(value).replace(",", "."))
                if not math.isfinite(number) or number < 0 or (key == "remaining_duration" and not number.is_integer()):
                    raise ValueError("La tensión y la duración restante deben ser no negativas; la duración debe ser entera.")
                result[key] = int(number) if key == "remaining_duration" else number
    return result


def validate_activity(values):
    if not values.get("code", "").strip() or not values.get("name", "").strip():
        return "Código y nombre son obligatorios."
    try:
        normalized = normalize_activity(values)
        month = values.get("macro_month")
        if month:
            if len(month) != 7:
                return "El mes macro debe tener formato AAAA-MM."
            date.fromisoformat(month + "-01")
        for key in ("duration_days", "work_hours", "cost_budget", "crew_size"):
            if key in normalized and (not math.isfinite(float(normalized[key])) or normalized[key] < 0):
                return "La duración, HH, costo y dotación deben ser no negativos."
        if not float(normalized.get("duration_days", 0)).is_integer():
            return "La duración debe ser un número entero de días laborables."
        priority = float(normalized.get("priority", 50))
        if not priority.is_integer() or not 0 <= priority <= 100:
            return "La prioridad debe ser un número entero entre 0 y 100."
        if not 0 <= normalized.get("percent_complete", 0) <= 100:
            return "El avance debe estar entre 0 y 100."
        for start, finish in (("window_start", "window_end"), ("planned_start", "planned_finish"), ("actual_start", "actual_finish")):
            if values.get(start) and values.get(finish) and values[finish] < values[start]:
                return "El término no puede ser anterior al inicio."
        if values.get("constraint_type", "ASAP") not in ("ASAP", "ALAP") and not values.get("constraint_date"):
            return "Indica una fecha para la restricción."
    except (TypeError, ValueError) as exc:
        return f"Revisa los valores y las fechas: {exc}"
    return None


def detail_patch(original, values):
    """Only send changed editable fields; preserve macro/window precedence."""
    normalized = normalize_activity(values)
    patch = {k: v for k, v in normalized.items() if v != original.get(k)}
    macro_changed = (normalized.get("macro_month") or None) != (original.get("macro_month") or None)
    window_changed = any(normalized.get(k) != original.get(k) for k in ("window_start", "window_end"))
    if macro_changed and normalized.get("macro_month"):
        patch.pop("window_start", None)
        patch.pop("window_end", None)
    elif window_changed:
        patch["macro_month"] = None
    return patch


def scheduled_dates(days, requested, duration, horizon=None):
    """Inclusive finish on actual working days (including shifts and exceptions)."""
    if horizon and requested > horizon:
        raise ValueError("El inicio queda fuera del horizonte del proyecto.")
    if duration == 0:
        return dict(planned_start=requested, planned_finish=requested)
    working = sorted(d["date"] for d in days if d["working"] and d["date"] >= requested
                     and (not horizon or d["date"] <= horizon))
    if len(working) < duration:
        raise ValueError("No hay suficientes días laborables dentro del horizonte.")
    return dict(planned_start=working[0], planned_finish=working[duration - 1])


def warning_groups(warnings):
    groups = {}
    for warning in warnings:
        text = str(warning)
        key = re.sub(r"(?i)(actividad\s+)[\w.-]+", r"\1…", text)
        groups.setdefault(key, []).append(text)
    return [(key, len(examples), examples) for key, examples in groups.items()]

