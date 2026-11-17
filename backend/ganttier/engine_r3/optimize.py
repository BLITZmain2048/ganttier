"""Entrada pública de optimización, selección de método y auditoría final."""
from __future__ import annotations

from math import isfinite
from time import perf_counter

import numpy as np

from ganttier.domain import ObjectiveWeights, OptimizeOptions, OptimizeResult, ScheduleProblem
from .cpm import Model, _compute
from .loads import Loads
from .heuristics import solve as heuristic_solve
from .search_bounds import prepare
from .verify import verify_with_options
from .construction import temporal_conflicts


def optimize(problem: ScheduleProblem, weights: ObjectiveWeights, options: OptimizeOptions) -> OptimizeResult:
    """Optimiza sin mutar entradas; devuelve fechas, métricas y conflictos auditados."""
    started = perf_counter()
    if options.method not in {"auto", "heuristic", "cpsat"}:
        raise ValueError("Método de optimización desconocido.")
    if options.group_by not in {"skill", "resource"} or options.level_period not in {"day", "week", None}:
        raise ValueError("Agrupación o período de nivelación inválido.")
    if not isfinite(options.time_limit_s) or options.time_limit_s <= 0:
        raise ValueError("El límite de tiempo debe ser positivo y finito.")
    if any(not isfinite(v) or v < 0 for v in vars(weights).values()):
        raise ValueError("Los pesos deben ser no negativos y finitos.")
    m = Model(problem)
    cpm, late, _ = _compute(m)
    conflicts = temporal_conflicts(m)
    if conflicts:
        return OptimizeResult({}, {}, status='infactible', method_used='precheck',
                              elapsed_s=perf_counter() - started,
                              warnings=['Restricciones temporales incompatibles incluso sin límites de recursos.', *conflicts])
    loads = Loads(m, options, weights)
    if options.respect_resource_limits:
        missing = [loads.keys[g] for g in loads.cost_rows if loads.capacity_defined[g]
                   and not np.any(loads.limit[g] > 0)
                   and any(m.duration[i] > 0 and any(row[0] == g for row in demand) for i, demand in enumerate(loads.demands))]
        if missing:
            return OptimizeResult({}, {}, status='infactible', method_used='precheck', elapsed_s=perf_counter() - started,
                                  warnings=['Capacidad insuficiente de ' + ', '.join(missing) + ': no hay recursos activos con capacidad disponible.'])
    before_s, before_f = m.starts.copy(), m.finishes.copy()
    for i, a in enumerate(m.acts):
        if not m.fixed[i] and a.planned_start:
            before_s[i] = m.idx(a.planned_start)
            before_f[i] = m.idx(a.planned_finish) if a.planned_finish else m.finish(i, int(before_s[i]))
    loads.rebuild(before_s)
    loads.scales = np.maximum(1, loads.terms(before_s, before_f))
    use_cp = options.method == "cpsat" or (options.method == "auto" and len(m.acts) <= options.cpsat_max_activities)
    use_lns = options.method in {'auto', 'cpsat'}
    requested_deadline = started + options.time_limit_s
    # Se respeta el límite pedido por el usuario (antes había un tope fijo de 19 s con CP-SAT):
    # sólo se reserva un margen para construir perfiles y ejecutar las auditorías finales.
    margin = min(1.0, 0.1 * options.time_limit_s)
    deadline = requested_deadline - margin if use_cp else requested_deadline
    # Primero un programa completo: no gastar el primer cuarto en reinicios
    # antes de saber si habrá un incumbente para el resto de la búsqueda.
    construction_deadline = min(deadline, perf_counter() + max(0., deadline - perf_counter()) * .8)
    original_upper = m.upper.copy()
    m._seed_only = True
    try:
        seed_s, seed_f, warnings = prepare(m, loads, late, construction_deadline)
    finally:
        m._seed_only = False
    heuristic_deadline = (min(deadline, perf_counter() + max(0., deadline - perf_counter()) * .35)
                          if use_lns else deadline)
    # El plazo de búsqueda se acota al incumbente compacto; si el programa VIGENTE termina después y es
    # completo, se admite su término para que la optimización nunca empeore el programa actual (p. ej.
    # «Nivelar carga» sobre un programa ya nivelado que termina más tarde que el SGS temprano).
    complete_before = all(m.fixed[i] or a.planned_start for i, a in enumerate(m.acts))
    if len(m.acts) and complete_before and int(before_f.max()) > loads.search_finish:
        current_cap = int(before_f.max())
        for i in m.order:
            if not m.fixed[i]:
                m.upper[i] = min(original_upper[i], m.cals[i].start_by(current_cap, int(m.duration[i])))
        m.tighten_hard_bounds()
        loads.search_finish = current_cap
    loads.set_evaluation_span(max(loads.search_finish, int(before_f.max()) if len(before_f) else loads.begin))
    loads.rebuild(before_s)
    loads.scales = np.maximum(1, loads.terms(before_s, before_f))
    before = loads.metrics(before_s, before_f)
    profile_before = loads.profile()
    if use_cp and options.method == "auto":
        from .cpsat import plan_model
        admission = plan_model(m, loads, seed_s)
        if not admission.accepted:
            use_cp = False
            heuristic_deadline = min(deadline, perf_counter() + max(0., deadline - perf_counter()) * .85)
            warnings.append("El modelo CP-SAT completo excede el presupuesto estimado; se usa la heurística y se intentan vecindades acotadas.")
            # Un primer intento incompleto puede recuperarse usando el remanente
            # de construcción; nunca se reinicia el reloj del presupuesto.
            loads.rebuild(seed_s)
            if m.violations(seed_s, seed_f) or loads.violations():
                seed_s, seed_f, rebuild_warnings = prepare(m, loads, late, heuristic_deadline)
                warnings = [w for w in warnings if not w.startswith("Se agotó el tiempo durante el SGS")] + rebuild_warnings
                loads.set_evaluation_span(max(loads.search_finish, int(before_f.max()) if len(before_f) else loads.begin))
                loads.rebuild(before_s)
                loads.scales = np.maximum(1, loads.terms(before_s, before_f))
                before = loads.metrics(before_s, before_f)
                profile_before = loads.profile()
    from .multistart import improve as multistart_improve
    multi_share = .15 if len(m.acts) > 500 else .5
    multi_deadline = min(heuristic_deadline, perf_counter() + max(0., heuristic_deadline - perf_counter()) * multi_share)
    seed_s, seed_f, multi_warnings = multistart_improve(m, loads, late, multi_deadline, (seed_s, seed_f))
    warnings.extend(multi_warnings)
    # Improve a complete early schedule. Moving predecessors greedily before
    # placing their successors ignores downstream soft-window costs.
    if len(m.acts) >= 1000 and not problem.relations and np.all(m.duration == 1):
        starts, finishes, local_warnings = heuristic_solve(m, loads, late, heuristic_deadline)
    else:
        starts, finishes, local_warnings = heuristic_solve(m, loads, late, heuristic_deadline, initial=(seed_s, seed_f))
    warnings.extend(local_warnings)
    # Never replace a feasible incumbent with a worse or infeasible candidate.
    for incumbent_s, incumbent_f in ((seed_s, seed_f), (before_s, before_f)):
        loads.rebuild(incumbent_s)
        if m.violations(incumbent_s, incumbent_f) or loads.violations():
            continue
        value = loads.objective(incumbent_s, incumbent_f)
        loads.rebuild(starts)
        if m.violations(starts, finishes) or loads.violations() or value < loads.objective(starts, finishes) - 1e-9:
            starts, finishes = incumbent_s.copy(), incumbent_f.copy()
    loads.rebuild(starts)
    warnings = list(dict.fromkeys([*cpm.warnings, *loads.warnings, *warnings]))
    method, status = "heuristic", "heuristico"
    if use_cp and len(m.acts) and perf_counter() < deadline:
        from .cpsat import solve as cpsat_solve
        cp_deadline = (min(deadline, perf_counter() + max(0., deadline - perf_counter()) * .75)
                       if len(m.acts) > 50 else deadline)
        candidate = cpsat_solve(m, loads, starts, finishes, cp_deadline)
        if candidate is not None:
            cp_s, cp_f, cp_status, cp_warnings = candidate
            loads.rebuild(starts)
            incumbent_value = loads.objective(starts, finishes)
            incumbent_ok = not m.violations(starts, finishes) and not loads.violations()
            loads.rebuild(cp_s)
            cp_ok = not m.violations(cp_s, cp_f) and not loads.violations()
            # CP-SAT trabaja en una grilla (semanal) y puede no mejorar el objetivo diario exacto:
            # sólo reemplaza al incumbente si es factible y no peor.
            if cp_ok and (not incumbent_ok or loads.objective(cp_s, cp_f) <= incumbent_value + 1e-9):
                starts, finishes, status = cp_s, cp_f, cp_status
                method = "heuristic" if status == "heuristico" else "cpsat"
            else:
                warnings.append("CP-SAT no mejoró el mejor programa heurístico; se conserva este último.")
            warnings.extend(cp_warnings)
        else:
            # Un modelo que no llegó a devolver candidato no debe provocar otra
            # construcción CP-SAT inmediatamente. Aprovechar el remanente con
            # movimientos locales conserva el contrato del fallback público.
            use_lns = False
            warnings.append("CP-SAT no devolvió un candidato; se usa el tiempo restante en mejora local.")
            if perf_counter() < deadline:
                starts, finishes, more = heuristic_solve(m, loads, late, deadline, initial=(starts, finishes))
                warnings.extend(more)
    elif use_cp and len(m.acts):
        warnings.append("El presupuesto de tiempo se agotó antes de iniciar CP-SAT; se conserva la heurística.")
    if use_lns and len(m.acts) and perf_counter() < deadline:
        from .lns import improve as lns_improve
        candidate_s, candidate_f, lns_warnings = lns_improve(m, loads, starts, finishes, deadline)
        if not np.array_equal(candidate_s, starts):
            starts, finishes, status, method = candidate_s, candidate_f, 'factible', 'cpsat'
        warnings.extend(lns_warnings)
    loads.rebuild(starts)
    errors = m.violations(starts, finishes) + loads.violations()
    if errors and use_cp and perf_counter() < requested_deadline:
        # El reintento comparte el único presupuesto solicitado.
        extra = requested_deadline
        retry_s, retry_f, retry_warnings = prepare(m, loads, late, extra)
        retry_s, retry_f, more = heuristic_solve(m, loads, late, extra, initial=(retry_s, retry_f))
        warnings.extend([*retry_warnings, *more])
        loads.rebuild(retry_s)
        if not m.violations(retry_s, retry_f) and not loads.violations():
            starts, finishes, status, method = retry_s, retry_f, "heuristico", "heuristic"
            warnings.append("CP-SAT no completó un programa factible a tiempo; se entrega la construcción heurística completa.")
        loads.rebuild(starts)
        errors = m.violations(starts, finishes) + loads.violations()
    # ALAP se aplica al resultado: última fecha factible con los demás trabajos
    # colocados. No se convierte la fecha tardía sin recursos en una fecha fija.
    if not errors:
        for i in reversed(m.order):
            if m.acts[i].constraint_type != 'ALAP' or m.fixed[i]:
                continue
            if perf_counter() >= requested_deadline:
                warnings.append(f'{m.acts[i].code}: no se pudo certificar la última fecha ALAP dentro del presupuesto.')
                continue
            upper = min(int(original_upper[i]), int(late[i]))
            for j, r in m.outgoing[i]:
                upper = min(upper, m.edge_upper(i, j, r, starts, finishes))
            cal = m.cals[i]
            candidates = cal.days[cal.rank(int(starts[i])):cal.rank(upper + 1)]
            if not len(candidates):
                warnings.append(f'{m.acts[i].code}: no se pudo conservar la fecha ALAP calculada por CPM; el programa factible requiere una fecha posterior.')
                continue
            old = int(starts[i])
            loads.change(i, old, -1)
            scores, ends = loads.candidates(i, candidates, int(finishes.max()))
            feasible = np.flatnonzero(np.isfinite(scores))
            if len(feasible):
                chosen = feasible[-1]
                starts[i], finishes[i] = candidates[chosen], ends[chosen]
                m.upper[i] = max(m.upper[i], starts[i])
                if starts[i] != old:
                    status, method = 'heuristico', 'heuristic'
            loads.change(i, int(starts[i]), 1)
            if starts[i] < late[i] or m.incoming[i] or m.outgoing[i]:
                warnings.append(f'{m.acts[i].code}: ALAP se ubicó en la última fecha admisible con la capacidad y las otras actividades fijadas; no se certifica un óptimo global de la red.')
        errors = m.violations(starts, finishes) + loads.violations()
    independent = verify_with_options(problem, {a.id: m.dt(starts[i]) for i, a in enumerate(m.acts)},
                                      {a.id: m.dt(finishes[i]) for i, a in enumerate(m.acts)}, options)
    errors.extend(v['detail'] for v in independent.violations)
    warnings.extend(v['detail'] for v in independent.warnings if v['type'] != 'unassigned')
    if errors:
        status = "infactible"
        warnings.extend(errors)
        warnings.append("No se encontró un programa que cumpla todas las restricciones dentro de la búsqueda disponible; no se devuelven fechas aplicables. Este resultado no demuestra infactibilidad del proyecto.")
    for i, a in enumerate(m.acts):
        if a.constraint_type in {"SNLT", "FNLT"}:
            event = starts[i] if a.constraint_type == "SNLT" else finishes[i]
            if event > m.idx(a.constraint_date):
                warnings.append(f"{a.code}: el programa optimizado excede el plazo {a.constraint_type} del {a.constraint_date.isoformat()}.")
    return OptimizeResult(
        {a.id: m.dt(starts[i]) for i, a in enumerate(m.acts)} if status != "infactible" else {},
        {a.id: m.dt(finishes[i]) for i, a in enumerate(m.acts)} if status != "infactible" else {},
        before=before, after=loads.metrics(starts, finishes) if status != "infactible" else None,
        profile_before=profile_before, profile_after=loads.profile() if status != "infactible" else None, method_used=method,
        status=status, elapsed_s=perf_counter() - started, warnings=list(dict.fromkeys(warnings)),
    )
