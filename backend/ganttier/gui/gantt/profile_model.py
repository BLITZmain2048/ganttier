"""Validated numeric load profile; independent of Qt."""
import numpy as np
from .model import date_array


class ProfileModel:
    def __init__(self, response, period):
        if response.get("period") != period:
            raise ValueError("El perfil recibido tiene un período distinto al solicitado.")
        self.days = date_array(response["labels"], len(response["labels"]))
        self.span = 1 if period == "day" else 7
        if not np.isfinite(self.days).all() or (np.diff(self.days) < self.span).any():
            raise ValueError("El perfil contiene fechas inválidas o períodos superpuestos.")
        self.series = []
        keys = set()
        # Color identity does not change when the API orders series by current load.
        for color, source in enumerate(sorted(response["series"], key=lambda s: str(s["key"] or ""))):
            load, capacity = np.asarray(source["load"], float), np.asarray(source["capacity"], float)
            key = source["key"]
            if (key in keys or load.shape != self.days.shape or capacity.shape != self.days.shape
                    or not np.isfinite(load).all() or not np.isfinite(capacity).all()
                    or (load < 0).any() or (capacity < 0).any()):
                raise ValueError("El perfil contiene series duplicadas o valores inválidos.")
            keys.add(key)
            self.series.append(dict(key=key, load=load, capacity=capacity, color=color, total=float(load.sum())))
        self.series.sort(key=lambda s: (-s["total"], s["key"]))

    def visible(self, start, finish):
        first = int(np.searchsorted(self.days, start))
        if first and self.days[first - 1] + self.span > start:
            first -= 1
        return first, int(np.searchsorted(self.days, finish))

