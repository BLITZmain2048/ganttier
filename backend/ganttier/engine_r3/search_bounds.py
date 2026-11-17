"""Plazo operativo de nivelación independiente del horizonte de calendarios."""
from dataclasses import replace
from math import floor, isfinite
import os

import numpy as np

from .heuristics import solve
from .multistart import improve


def prepare(m, loads, late, deadline):
    """SGS temprano como incumbente; cota por capacidad y holgura hacia atrás.

    La cota es una política de búsqueda, no una prueba de plazo RCPSP mínimo.
    La extensión voluntaria se configura sin modificar el contrato de dominio.
    """
    ratio = float(os.environ.get('GANTTIER_LEVEL_EXTENSION_RATIO', '0'))
    if not isfinite(ratio) or ratio < 0:
        raise ValueError('GANTTIER_LEVEL_EXTENSION_RATIO debe ser finito y no negativo.')
    cpm_finish = int(m.finishes.max()) if len(m.acts) else loads.begin
    loads.reference_finish = cpm_finish
    # Propagate true earliest bounds (not soft LS deadlines) through the network.
    early_s, early_f = m.starts.copy(), m.finishes.copy()
    for i in m.order:
        if not m.fixed[i]:
            m.lower[i] = max([int(m.lower[i])] + [m.edge_lower(i, p, r, early_s, early_f) for p, r in m.incoming[i]])
            early_s[i] = m.lower[i]
            early_f[i] = m.finish(i, int(early_s[i]))
    m.tighten_hard_bounds()
    weights, opts, limit = loads.weights.copy(), loads.options, loads.limit
    # Even soft capacity may need room to remove overload. Use the user's
    # permitted normal + overtime capacity to construct the reference.
    loads.weights = np.array([0., 1., 0., 0.])
    loads.options = replace(opts, respect_resource_limits=True)
    seed_s, seed_f, warnings = solve(m, loads, late, deadline, construction_only=True)
    seed_s, seed_f, notices = improve(m, loads, late, deadline, (seed_s, seed_f))
    warnings.extend(notices)
    # A cost-only request can use the normal-capacity reference to avoid OT.
    if weights[3] and not weights[0] and not weights[2]:
        actual_s, actual_f = seed_s, seed_f
        loads.limit = loads.capacity
        seed_s, seed_f, warnings = solve(m, loads, late, deadline, construction_only=True)
        if m.violations(seed_s, seed_f) or loads.violations():
            seed_s, seed_f = actual_s, actual_f
    loads.weights, loads.options, loads.limit = weights, opts, limit
    reference = max(cpm_finish, int(seed_f.max())) if len(seed_f) else cpm_finish
    # Respecting a deliberately later soft window must remain possible.
    if weights[2]:
        preferred_s, preferred_f = early_s.copy(), early_f.copy()
        for i in m.order:
            if m.fixed[i]:
                continue
            a = m.acts[i]
            start = max([int(m.lower[i])] + [m.edge_lower(i, p, r, preferred_s, preferred_f) for p, r in m.incoming[i]])
            if a.window_start:
                start = max(start, m.cals[i].next(m.idx(a.window_start)))
            # Hard bounds remain authoritative when a soft window is impossible.
            preferred_s[i] = min(start, int(m.upper[i]))
            preferred_f[i] = m.finish(i, int(preferred_s[i]))
        reference = max(reference, int(preferred_f.max()) if len(preferred_f) else reference)
    loads.rebuild(seed_s)
    seed_feasible = not m.violations(seed_s, seed_f) and not loads.violations()
    cap = min(loads.end, reference + floor(max(1, cpm_finish - loads.begin + 1) * ratio)) if seed_feasible else loads.end
    loads.reference_finish = reference
    loads.extension_price = 1 / max(1, reference - m.idx(m.problem.project.start_date) + 1)
    for i in m.order:
        if not m.fixed[i]:
            m.upper[i] = min(m.upper[i], m.cals[i].start_by(cap, int(m.duration[i])))
    m.tighten_hard_bounds()
    loads.search_finish = cap
    # A complete window-aligned incumbent permits coordinated chain shifts;
    # single-task local moves cannot cross successors that are still early.
    if weights[2]:
        loads.rebuild(preferred_s)
        if not m.violations(preferred_s, preferred_f) and not loads.violations():
            preferred_value = loads.objective(preferred_s, preferred_f)
            loads.rebuild(seed_s)
            if m.violations(seed_s, seed_f) or loads.violations() or preferred_value < loads.objective(seed_s, seed_f):
                seed_s, seed_f = preferred_s, preferred_f
    loads.rebuild(seed_s)
    return seed_s, seed_f, warnings
