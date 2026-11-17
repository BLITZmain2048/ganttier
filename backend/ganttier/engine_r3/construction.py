"""Búsqueda de capacidad sin calcular objetivos durante el SGS temprano."""
from __future__ import annotations

from time import perf_counter

import numpy as np


def first_position(m, loads, i: int, lower: int, upper: int, deadline: float) -> tuple[int, int] | None:
    """Devuelve la primera posición válida; None no demuestra infactibilidad.

    Con jornadas uniformes, un prefijo de días saturados reemplaza la matriz
    inicio × duración. Calendarios variables y repartos dinámicos conservan el
    cálculo exacto de HH. Ambos caminos consultan bloques acotados de inicios.
    """
    if upper < lower or perf_counter() >= deadline:
        return None
    cal = m.cals[i]
    begin, end = cal.rank(lower), cal.rank(upper + 1)
    duration = int(m.duration[i])
    if end <= begin:
        return None
    if not duration or not loads.demands[i] or not loads.options.respect_resource_limits:
        s = int(cal.days[begin])
        return s, m.finish(i, s)
    cache = getattr(m, '_uniform_construction_hours', None)
    if cache is None:
        cache = m._uniform_construction_hours = {}
    key = id(cal)
    if key not in cache:
        h = cal.hours[cal.days]
        cache[key] = float(h[0]) if np.all(h == h[0]) else None
    uniform = cache[key]
    constant = uniform is not None and not any((i, g) in loads.dynamic_shares for g, _, _ in loads.demands[i])
    # A bounded matrix on the general path, even for very long activities.
    step = max(1, min(128, 16384 // max(1, duration)))
    for offset in range(begin, end, step):
        if perf_counter() >= deadline:
            return None
        stop = min(end, offset + step)
        days = cal.days[offset:stop]
        if constant:
            occupied = cal.days[offset:stop + duration - 1] - loads.begin
            if len(occupied) != len(days) + duration - 1:
                return None
            bad = (occupied < 0) | (occupied >= loads.size)
            safe = np.clip(occupied, 0, loads.size - 1)
            for g, hours, units in loads.demands[i]:
                if loads.capacity_defined[g]:
                    amount = hours / duration + units * uniform
                    bad |= loads.load[g, safe] + amount > loads.limit[g, safe] + 1e-8
            prefix = np.r_[0, np.cumsum(bad)]
            valid = prefix[duration:] == prefix[:-duration]
        else:
            cells, valid = loads.cells(i, days)
            safe = np.clip(cells, 0, loads.size - 1)
            for g, hours, units in loads.demands[i]:
                if loads.capacity_defined[g]:
                    amount = loads.amount(i, g, hours, units, cells)
                    valid &= (loads.load[g, safe] + amount <= loads.limit[g, safe] + 1e-8).all(axis=1)
        positions = np.flatnonzero(valid)
        if len(positions):
            start = int(days[positions[0]])
            return start, m.finish(i, start)
    return None


def temporal_conflicts(m) -> list[str]:
    """Prueba únicamente contradicciones de fechas, sin inferir asignaciones."""
    starts, finishes = m.starts.copy(), m.finishes.copy()
    for i in m.order:
        if not m.fixed[i]:
            starts[i] = max([int(m.lower[i])] + [m.edge_lower(i, p, r, starts, finishes)
                                               for p, r in m.incoming[i]])
            finishes[i] = m.finish(i, int(starts[i]))
    errors = m.violations(starts, finishes)
    # Una fecha obligatoria puede fijar ya el inicio/fin en Model. La
    # contradicción aparece entonces como horizonte o precedencia, pero el
    # diagnóstico debe identificar también la restricción que fijó esa fecha.
    for a in m.acts:
        if (a.constraint_type in {'MSO', 'MFO'} and a.constraint_date
                and any(e.startswith(f'{a.code}:') for e in errors)):
            event = 'iniciar' if a.constraint_type == 'MSO' else 'terminar'
            errors.append(f'{a.code}: la restricción {a.constraint_type} exige {event} el '
                          f'{a.constraint_date.isoformat()}; es incompatible con las fechas y precedencias indicadas.')
    return errors
