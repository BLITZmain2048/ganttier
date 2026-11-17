"""Vecindades CP-SAT acotadas por fechas y recursos, con incumbente protegido."""
from __future__ import annotations

from time import perf_counter

import numpy as np

from . import cpsat


def neighborhood_plan(m, loads, starts, finishes, movable, radius=14):
    """Fija el exterior y conserva TODAS las relaciones y cargas del modelo.

    El radio limita días civiles alrededor de la ventana seleccionada; los
    inicios actuales permanecen en el dominio. Nunca altera cotas del Model.
    """
    selected = {int(i) for i in movable if not m.fixed[i]}
    if not selected:
        return None
    lower = min(int(starts[i]) for i in selected) - radius
    upper = max(int(finishes[i]) for i in selected) + radius
    domains = []
    for i, cal in enumerate(m.cals):
        if i not in selected:
            domains.append(np.array([int(starts[i])], dtype=np.int64))
            continue
        lo, hi = max(int(m.lower[i]), lower), min(int(m.upper[i]), upper)
        days = cal.days[cal.rank(lo):cal.rank(hi + 1)] if hi >= lo else np.array([], dtype=np.int64)
        # No se introducen fechas fuera de cotas para conservar un hint inválido.
        if not len(days) or not np.any(days == starts[i]):
            return None
        domains.append(days)
    if not any(len(d) > 1 for d in domains):
        return None
    return cpsat._plan_from_domains(m, loads, domains)


def improve(m, loads, starts, finishes, deadline):
    """Mejora ventanas diarias/por recurso, aceptando sólo el objetivo exacto.

    Orden y desempates dependen de la semilla, no de conjuntos sin ordenar.
    Con cortes por reloj puede cambiar cuántas vecindades llegan a completarse.
    No se presenta optimalidad de una vecindad como optimalidad del proyecto.
    """
    best_s, best_f = starts.copy(), finishes.copy()
    if not len(starts) or perf_counter() >= deadline:
        return best_s, best_f, []
    loads.rebuild(best_s)
    if m.violations(best_s, best_f) or loads.violations():
        return best_s, best_f, []
    try:
        import ortools.sat.python.cp_model  # noqa: F401; importación diferida
    except ImportError:
        return best_s, best_f, ['LNS no está disponible: falta OR-Tools; se conserva el programa factible.']
    value = loads.objective(best_s, best_f)
    rng = np.random.default_rng(loads.options.seed)
    movable = np.flatnonzero(~m.fixed)
    if not len(movable):
        return best_s, best_f, []
    # Un máximo de doce vecindades evita iterar sin límite en casos pequeños.
    order = sorted(movable, key=lambda i: (-int(best_f[i]), int(i)))
    anchors = [order[0], *rng.permutation(movable).tolist()]
    attempts = accepted = 0
    for turn, anchor in enumerate(anchors[:12]):
        remaining = deadline - perf_counter()
        if remaining <= .1:
            break
        groups = {g for g, _, _ in loads.demands[anchor]}
        pool = movable
        if turn % 2 and groups:
            pool = [i for i in movable if any(g in groups for g, _, _ in loads.demands[i])]
        selected = sorted(pool, key=lambda i: (abs(int(best_s[i]) - int(best_s[anchor])), int(i)))[:32]
        plan = neighborhood_plan(m, loads, best_s, best_f, selected)
        if plan is None or not plan.accepted:
            continue
        attempts += 1
        # Construcción y búsqueda comparten el mismo límite absoluto.
        result = cpsat.solve(m, loads, best_s, best_f, min(deadline, perf_counter() + min(2., remaining)), plan=plan)
        if result is None:
            continue
        s, f, _, _ = result
        loads.rebuild(s)
        if not m.violations(s, f) and not loads.violations():
            candidate = loads.objective(s, f)
            if candidate < value - 1e-9:
                best_s, best_f, value = s.copy(), f.copy(), candidate
                accepted += 1
        loads.rebuild(best_s)
    loads.rebuild(best_s)
    m.lns_stats = {'attempts': attempts, 'accepted': accepted, 'seed': loads.options.seed}
    notices = [f'LNS CP-SAT: {attempts} vecindades, {accepted} mejoras del objetivo diario; se conserva el mejor programa factible.'] if attempts else []
    return best_s, best_f, notices
