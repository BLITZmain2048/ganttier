"""Modelos Pydantic v2 derivados del contrato; campos idénticos a domain.py."""
from dataclasses import MISSING, fields, is_dataclass
from datetime import date
from typing import Any, get_type_hints
import math
import re

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from .. import domain


class DomainModel(BaseModel):
    """Valida restricciones comunes sin duplicar las declaraciones del dominio."""

    model_config = ConfigDict(from_attributes=True, extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_values(self):
        values = self.model_dump()
        for name in ("name", "code"):
            if name in values and (not isinstance(values[name], str) or not values[name].strip()):
                raise ValueError(f"El campo {name} no puede estar vacío.")
        for name in ("duration_days", "remaining_duration", "work_hours", "actual_hours", "actual_cost", "cost_budget", "hours_per_day", "hours", "capacity_hours_day", "max_units", "units", "crew_size", "cost_hour", "overtime_cost_hour", "max_overtime_hours_day", "tol_before_days", "tol_after_days", "cost_per_occurrence", "level", "makespan", "window", "cost", "peak"):
            value = values.get(name)
            if isinstance(value, (int, float)) and (value < 0 or not math.isfinite(value)):
                raise ValueError(f"El campo {name} debe ser finito y no negativo.")
        if values.get("percent_complete") is not None and not 0 <= values["percent_complete"] <= 100:
            raise ValueError("El avance debe estar entre 0 y 100.")
        if values.get("priority") is not None and not 0 <= values["priority"] <= 100:
            raise ValueError("La prioridad debe estar entre 0 y 100.")
        if values.get("interval", 1) < 1:
            raise ValueError("El intervalo debe ser positivo.")
        if values.get("time_limit_s", 1) <= 0 or values.get("cpsat_max_activities", 1) < 1:
            raise ValueError("El tiempo y el límite de actividades deben ser positivos.")
        for start, end in (("start_date", "end_date"), ("window_start", "window_end"), ("planned_start", "planned_finish"), ("actual_start", "actual_finish"), ("first_due", "horizon_end")):
            if values.get(start) and values.get(end) and values[end] < values[start]:
                raise ValueError(f"El campo {end} no puede ser anterior a {start}.")
        if self.__class__.__name__ == "Project" and values.get("mode") == "mantenimiento" and not values.get("end_date"):
            raise ValueError("El modo mantenimiento requiere una fecha de fin de horizonte.")
        if values.get("workweek") is not None and not re.fullmatch(r"[01]{7}", values["workweek"]):
            raise ValueError("La semana debe contener siete dígitos 0 o 1.")
        if values.get("shift_pattern") and (not re.fullmatch(r"[1-9]\d*x[1-9]\d*", values["shift_pattern"]) or not values.get("shift_anchor")):
            raise ValueError("El turno requiere un patrón NxM y un ancla.")
        month = values.get("macro_month")
        if month:
            if not re.fullmatch(r"\d{4}-\d{2}", month):
                raise ValueError("El mes macro debe tener formato YYYY-MM.")
            try:
                date.fromisoformat(month + "-01")
            except ValueError:
                raise ValueError("El mes macro debe indicar un año y mes válidos.") from None
        if values.get("preferred_weekday") is not None and not 0 <= values["preferred_weekday"] <= 6:
            raise ValueError("El día de semana debe estar entre 0 y 6.")
        if values.get("day_of_month") is not None and not 1 <= values["day_of_month"] <= 31:
            raise ValueError("El día del mes debe estar entre 1 y 31.")
        if values.get("preferred_months"):
            months = values["preferred_months"].split(",")
            if any(not m.strip().isdigit() or not 1 <= int(m.strip()) <= 12 for m in months):
                raise ValueError("Los meses preferidos deben ser números entre 1 y 12 separados por comas.")
        if self.__class__.__name__ == "Activity":
            if (values.get("percent_complete", 0) >= 100 or values.get("actual_finish") or values.get("status") == "terminada") and not values.get("actual_start"):
                raise ValueError("Una actividad terminada requiere un inicio real.")
        for exception in values.get("exceptions", []):
            if exception["hours"] < 0:
                raise ValueError("Las horas de una excepción no pueden ser negativas.")
        return self


def _model(name: str, cls: type) -> type[BaseModel]:
    hints = get_type_hints(cls)
    specs = {}
    for item in fields(cls):
        default: Any = ...
        if item.name in {"id", "project_id"} and item.default is MISSING:
            default = 0  # Los POST obtienen ambos ids desde SQLite y la ruta.
        elif name == "Project" and item.name == "start_date":
            default = Field(default_factory=date.today)
        elif item.default_factory is not MISSING:
            default = Field(default_factory=item.default_factory)
        elif item.default is not MISSING:
            default = item.default
        specs[item.name] = (hints[item.name], default)
    return create_model(name, __base__=DomainModel, __module__=__name__, **specs)


for _name, _type in vars(domain).copy().items():
    if isinstance(_type, type) and is_dataclass(_type):
        globals()[_name] = _model(_name, _type)


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    options: OptimizeOptions = Field(default_factory=OptimizeOptions)
    apply: bool = False


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horizon_start: date
    horizon_end: date
    replace: bool = True


class BaselineRequest(DomainModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Línea base", min_length=1)
    activate: bool = True


class DataDateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_date: date


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period_type: str
    period_start: date
    formats: list[str] = Field(default_factory=lambda: ["pdf"])
