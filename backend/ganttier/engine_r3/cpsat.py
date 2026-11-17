"""CP-SAT compacto: cargas por período y límites diarios sobre inicios discretos."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import floor
import os
from time import perf_counter

import numpy as np

from .cpm import Model
from .loads import Loads


SCALE = 10  # Conservative tenths of HH, shared by hints and capacity constraints.
MAX_CELLS = 3_000_000
MAX_CHOICES = 800_000


def _workers():
    """Un hilo por defecto; la admisión y el solver respetan la misma opción."""
    try:
        value = int(os.environ.get('GANTTIER_CPSAT_WORKERS', '1'))
    except ValueError as exc:
        raise ValueError('GANTTIER_CPSAT_WORKERS debe ser un entero positivo.') from exc
    if value < 1:
        raise ValueError('GANTTIER_CPSAT_WORKERS debe ser un entero positivo.')
    return value


def _domain_estimate(m, loads, domains):
    choices = sum(len(days) for i, days in enumerate(domains) if loads.demands[i])
    cells = sum(len(days) * int(m.duration[i]) * len(loads.demands[i]) for i, days in enumerate(domains))
    tables = 2 * sum(map(len, domains))
    for i in range(len(m.acts)):
        for p, r in m.incoming[i]:
            if len(domains[p]) and (m.cals[p] is not m.cals[i] or m.fixed[p] or m.fixed[i]):
                lo, hi = int(domains[p][0]), int(domains[p][-1])
                if r.type[0] == 'F':
                    lo, hi = m.finish(p, lo), m.finish(p, hi)
                tables += hi - lo + 1
    return {'choices': choices, 'cells': cells, 'tables': tables}


def candidate_domains(m, loads, hint_s, *, force_weekly=False):
    """Budget actual ES..LS domains; retry on a weekly start grid if needed.

    The weekly grid preserves bounds, fixed dates and incumbent starts. Work and
    capacity remain daily and calendar-exact; optimality is only on that grid.
    """
    domains = []
    for i, cal in enumerate(m.cals):
        if m.fixed[i]:
            days = np.array([int(m.starts[i])])
        else:
            days = cal.days[cal.rank(int(m.lower[i])):cal.rank(int(m.upper[i]) + 1)] if m.upper[i] >= m.lower[i] else np.array([], dtype=np.int64)
        domains.append(days)

    estimate = _domain_estimate(m, loads, domains)
    weekly = force_weekly or estimate['choices'] > MAX_CHOICES or estimate['tables'] + estimate['cells'] > MAX_CELLS
    if weekly:
        for i, days in enumerate(domains):
            if len(days) <= 1:
                continue
            weeks = (days + m.origin.toordinal() - 1) // 7
            keep = set(np.r_[0, np.flatnonzero(np.diff(weeks)) + 1, len(days) - 1].tolist())
            for day in (int(hint_s[i]),):
                k = int(np.searchsorted(days, day))
                if k < len(days) and days[k] == day:
                    keep.add(k)
            # Include soft-window boundary choices in the restricted grid.
            a = m.acts[i]
            for day in (m.idx(a.window_start) if a.window_start else None,
                        m.cals[i].start_by(m.idx(a.window_end), int(m.duration[i])) if a.window_end else None):
                if day is not None:
                    k = int(np.searchsorted(days, day))
                    if k < len(days):
                        keep.add(k)
                    if k:
                        keep.add(k - 1)
            domains[i] = days[sorted(keep)]
        estimate = _domain_estimate(m, loads, domains)
    return domains, weekly, estimate


def relation_thresholds(m, pred, succ, relation, lo, hi):
    """Only tabulate reachable predecessor events, never the global horizon."""
    cal = m.cals[succ]
    ranks = cal.prefix[np.arange(lo, hi + 1) + m.fs_offset(pred, relation)] + relation.lag_days
    if np.any(ranks < 0) or np.any(ranks >= len(cal.days)):
        raise ValueError("El desfase excede el calendario disponible para CP-SAT.")
    return cal.days[ranks].tolist()


def hint_is_feasible(m, loads, starts, finishes):
    """Check both date logic and CP-SAT's conservative integer HH rounding."""
    if m.violations(starts, finishes):
        return False
    rounded = np.zeros(loads.capacity.shape, dtype=np.int64)
    for i, start in enumerate(starts):
        if finishes[i] != m.finish(i, int(start)):
            return False
        if not loads.demands[i]:
            continue
        days, valid = loads.cells(i, np.array([start]))
        if not valid.all():
            return False
        if not days.size:
            continue
        for g, hours, units in loads.demands[i]:
            amounts = loads.amount(i, g, hours, units, days)[0]
            rounded[g, days[0]] += np.ceil(amounts * SCALE - 1e-8).astype(np.int64)
    return not loads.options.respect_resource_limits or bool(
        np.all(rounded[loads.capacity_defined] <= np.floor(loads.limit[loads.capacity_defined] * SCALE + 1e-8)))



MAX_INTEGER = 10_000_000
MODEL_MAX_CHOICES = 30_000
MODEL_MAX_CELLS = 250_000
MODEL_MAX_BYTES = 320 * 1024**2  # Admission estimate, not an OS memory limit.


@dataclass
class ModelPlan:
    domains: list
    weekly: bool
    estimate: dict
    accepted: bool


def plan_model(m, loads, hint_s):
    """No OR-Tools objects or candidate-by-workday matrices allocated here."""
    domains, weekly, estimate = candidate_domains(m, loads, hint_s)
    if not weekly and (sum(map(len, domains)) > 4000 or estimate['cells'] > 100_000):
        domains, weekly, estimate = candidate_domains(m, loads, hint_s, force_weekly=True)
    return _plan_from_domains(m, loads, domains, weekly)


def _plan_from_domains(m, loads, domains, weekly=False):
    """Admite también vecindades con el resto de actividades fijadas."""
    estimate = _domain_estimate(m, loads, domains)
    choices = sum(len(d) for d in domains if len(d) > 1)
    active_days = max(1, min(loads.size, loads.search_finish - loads.begin + 1))
    periods = int(loads.period_index[active_days - 1] - loads.period_index[0] + 1)
    groups = len(loads.keys)
    variables = choices + 3 * len(m.acts) + 6 * groups * periods
    # Worst-case support of sparse hard rows, plus weekly objective rows.
    rows = groups * (active_days if loads.options.respect_resource_limits else 0) + variables
    terms = 2 * estimate['cells'] + 6 * choices + estimate['tables']
    workers = _workers()
    memory = 64 * 1024**2 + (workers + 1) * (48 * terms + 160 * variables + 96 * rows)
    estimate = dict(estimate, choices=choices, periods=periods, variables=variables,
                    linear_terms=terms, estimated_bytes=memory)
    accepted = (choices <= MODEL_MAX_CHOICES and estimate['cells'] <= MODEL_MAX_CELLS
                and memory <= MODEL_MAX_BYTES and all(len(d) for d in domains))
    return ModelPlan(domains, weekly, estimate, accepted)


def objective_coefficients(coefficients, bounds):
    """Integer objective with an explicit <=1e7 domain and positive weights.

    Reserve one unit per active coefficient, then distribute the remaining
    precision proportionally. Tiny relative weights consequently lose precision.
    Reject data too large to represent even at that minimum precision.
    """
    base = sum(b for c, b in zip(coefficients, bounds) if c > 0)
    if base > MAX_INTEGER:
        raise ValueError('El objetivo excede la escala entera compacta.')
    weighted = sum(c * b for c, b in zip(coefficients, bounds))
    factor = (MAX_INTEGER - base) / max(weighted, 1e-30)
    result = [max(1, int(floor(c * factor))) if c > 0 else 0 for c in coefficients]
    bound = sum(c * b for c, b in zip(result, bounds))
    return result, bound


class _BuildTimeout(Exception):
    pass


def build_model(m, loads, hint_s, hint_f, plan, deadline):
    """Sparse start-choice formulation. Return model, dates and hint validity.

    Daily hard limits are linear rows over choices, never daily load variables.
    Weekly load/cost rows aggregate candidate HH directly. No LinMax, Element,
    per-workday intervals, quadratic domains or floating-point objective.
    """
    from ortools.sat.python import cp_model
    if loads.limit[loads.capacity_defined].max(initial=0) * SCALE > MAX_INTEGER:
        raise ValueError('La capacidad excede la escala entera compacta.')
    model = cp_model.CpModel()
    domains = plan.domains
    valid_hint = hint_is_feasible(m, loads, hint_s, hint_f) and all(
        int(hint_s[i]) in d for i, d in enumerate(domains))
    objective = []

    def add(constraint):
        # Avoid implicit constant Boolean variables; the hint covers every var.
        if isinstance(constraint, (bool, np.bool_)):
            if not constraint:
                model.add_bool_or([])
        else:
            model.add(constraint)

    def check_time():
        if perf_counter() >= deadline:
            raise _BuildTimeout

    def var(lo, hi, name, hint):
        lo, hi, hint = int(lo), int(hi), int(hint)
        if max(abs(lo), abs(hi)) > MAX_INTEGER:
            raise ValueError('Una carga o fecha excede la escala entera compacta.')
        if lo == hi:
            return lo
        v = model.new_int_var(lo, hi, name)
        if valid_hint:
            model.add_hint(v, hint)
        return v

    def cost(v, coefficient, bound, hint):
        if coefficient > 0 and not isinstance(v, int):
            objective.append((v, float(coefficient), int(bound), int(hint)))

    def selected(values, selectors):
        # Subtract the common offset to reduce linear coefficients.
        base = int(min(values))
        return base + sum((int(v) - base) * x for v, x in zip(values, selectors) if int(v) != base)

    starts, finishes, choices = [], [], []
    end_values = []
    day_terms, period_terms = defaultdict(list), defaultdict(list)
    period_bounds, period_hint = defaultdict(int), defaultdict(int)
    wanted = set(loads.visible if loads.weights[0] else []) | set(loads.cost_rows if loads.weights[3] else [])
    for i, a in enumerate(m.acts):
        check_time()
        candidates = domains[i]
        ends = [m.finish(i, int(d)) for d in candidates]
        end_values.append(ends)
        if len(candidates) == 1:
            selectors = [1]
        else:
            selectors = [var(0, 1, f'x{i}_{k}', int(d == hint_s[i])) for k, d in enumerate(candidates)]
            model.add_exactly_one(selectors)
        choices.append(selectors)
        s = var(candidates[0], candidates[-1], f's{i}', hint_s[i])
        f = var(min(ends), max(ends), f'f{i}', hint_f[i])
        add(s == selected(candidates, selectors))
        add(f == selected(ends, selectors))
        starts.append(s)
        finishes.append(f)
        if m.fixed[i] and not m.completed[i] and not m.in_progress[i]:
            add(s >= int(m.lower[i]))
            add(s <= int(m.upper[i]))
        if a.window_hard:
            if a.window_start:
                add(s >= m.idx(a.window_start))
            if a.window_end:
                add(f <= m.idx(a.window_end))
        if a.constraint_type == 'MSO':
            add(s == m.idx(a.constraint_date))
        elif a.constraint_type == 'MFO':
            add(f == m.idx(a.constraint_date))
        if loads.weights[2]:
            # Window penalties are constants per start choice, no max auxiliaries.
            values = loads.window(i, candidates, np.array(ends)).astype(np.int64)
            hint = int(loads.window(i, hint_s[i:i+1], hint_f[i:i+1])[0])
            win = var(int(values.min()), int(values.max()), f'window{i}', hint)
            add(win == selected(values, selectors))
            cost(win, loads.weights[2] / loads.scales[2], int(values.max()), hint)
        if not loads.demands[i] or not m.duration[i]:
            continue
        days, valid = loads.cells(i, candidates)
        if not valid.all():
            raise ValueError(f'{a.code}: trabajo remanente fuera del horizonte.')
        for g, hours, units in loads.demands[i]:
            check_time()
            if g not in wanted and not loads.options.respect_resource_limits:
                continue
            amounts = np.ceil(loads.amount(i, g, hours, units, days) * SCALE - 1e-8).astype(np.int64)
            activity_bounds = defaultdict(int)
            for k, selector in enumerate(selectors):
                if k % 128 == 0:
                    check_time()
                aggregate = defaultdict(int)
                for day, demand in zip(days[k], amounts[k]):
                    if demand:
                        if loads.options.respect_resource_limits and loads.capacity_defined[g]:
                            day_terms[g, int(day)].append(int(demand) * selector)
                        if g in wanted:
                            aggregate[int(loads.period_index[day])] += int(demand)
                for period, amount in aggregate.items():
                    key = g, period
                    period_terms[key].append(amount * selector)
                    activity_bounds[period] = max(activity_bounds[period], amount)
                    if candidates[k] == hint_s[i]:
                        period_hint[key] += amount
            for period, amount in activity_bounds.items():
                period_bounds[g, period] += amount
    for i in range(len(m.acts)):
        check_time()
        for p, relation in m.incoming[i]:
            # Only reachable predecessor events; the successor calendar handles
            # FS/SS/FF/SF, leads, milestones and fixed historical dates exactly.
            events = end_values[p] if relation.type[0] == 'F' else domains[p]
            cal = m.cals[i]
            ranks = cal.prefix[np.asarray(events) + m.fs_offset(p, relation)] + relation.lag_days
            if relation.type[1] == 'F' and not m.fixed[i]:
                ranks = ranks - max(int(m.duration[i]) - 1, 0)
            if np.any(ranks < 0) or np.any(ranks >= len(cal.days)):
                raise ValueError('El desfase excede el calendario disponible para CP-SAT.')
            required = selected(cal.days[ranks], choices[p])
            target = finishes[i] if m.fixed[i] and relation.type[1] == 'F' else starts[i]
            add(target >= required)
    for (g, day), terms in day_terms.items():
        check_time()
        add(sum(terms) <= int(floor(loads.limit[g, day] * SCALE + 1e-8)))
    # Release the Python expression lists before native solver copies are made.
    day_terms.clear()
    span_hint = int(max(hint_f))
    span_lo = max(min(v) for v in end_values)
    span_hi = max(max(v) for v in end_values)
    span = var(span_lo, span_hi, 'project_finish', span_hint)
    for f in finishes:
        add(span >= f)
    cost(span, loads.weights[1] / loads.scales[1], span_hi, span_hint)
    extension_hint = max(0, span_hint - loads.reference_finish)
    extension_hi = max(0, span_hi - loads.reference_finish)
    if loads.extension_price and extension_hi:
        extension = var(0, extension_hi, 'extension', extension_hint)
        add(extension >= span - loads.reference_finish)
        cost(extension, loads.extension_price, extension_hi, extension_hint)
    periods = list(range(len(loads.labels))[loads.period_slice])
    # build_model is also used directly: keep the default evaluation range useful.
    if loads.period_slice == slice(None):
        last = int(loads.period_index[min(loads.size - 1, loads.search_finish - loads.begin)])
        first = int(loads.period_index[max(0, m.idx(m.problem.project.start_date) - loads.begin)])
        periods = list(range(first, last + 1))
    level_reference = 0.0
    if loads.weights[0]:
        for g in loads.visible:
            values = [period_hint[g, t] for t in periods]
            mean = sum(values) / max(1, len(values))
            level_reference += max(values, default=0) + sum(abs(v - mean) for v in values)
    level_coefficient = loads.weights[0] / max(1, level_reference)
    for g in sorted(wanted):
        check_time()
        group_periods = sorted(set(periods) | {p for group, p in period_terms if group == g})
        target = int(round(sum(period_hint[g, t] for t in periods) / max(1, len(periods))))
        level_values = []
        for period in group_periods:
            check_time()
            key = g, period
            bound, hint = period_bounds[key], period_hint[key]
            total = sum(period_terms.get(key, []))
            # En LNS la mayor parte del proyecto está fijada: una carga constante
            # no necesita variable, ecuación ni auxiliares de desviación.
            value = total if isinstance(total, int) else var(0, bound, f'load{g}_{period}', hint)
            add(value == total)
            if loads.weights[0] and g in loads._visible and period in periods:
                over = (max(0, value - target) if isinstance(value, int) else
                        var(0, max(0, bound - target), f'over{g}_{period}', max(0, hint - target)))
                under = (max(0, target - value) if isinstance(value, int) else
                         var(max(0, target - bound), target, f'under{g}_{period}', max(0, target - hint)))
                add(value - target == over - under)
                cost(over, level_coefficient, max(0, bound - target), max(0, hint - target))
                cost(under, level_coefficient, target, max(0, target - hint))
                level_values.append((value, bound, hint))
            if loads.weights[3] and g in loads._cost_rows and bound:
                mask = loads.period_index == period
                capacity = int(floor(loads.capacity[g, mask].sum() * SCALE + 1e-8))
                extra = min(bound, int(floor(loads.overtime[g, mask].sum() * SCALE + 1e-8)))
                excess_hint = max(0, (value if isinstance(value, int) else hint) - capacity)
                ot_hint = min(extra, excess_hint)
                ot = ot_hint if isinstance(value, int) else var(0, extra, f'ot{g}_{period}', ot_hint)
                spill_bound = max(0, bound - capacity)
                spill = (excess_hint - ot_hint if isinstance(value, int) else
                         var(0, spill_bound, f'spill{g}_{period}', excess_hint - ot_hint))
                add(value <= capacity + ot + spill)
                available = loads.overtime[g, mask]
                price = float(np.dot(available, loads.price[g, mask]) / max(1e-30, available.sum()))
                cost(ot, loads.weights[3] * price / loads.scales[3] / SCALE, extra, ot_hint)
                cost(spill, loads.weights[3] * loads.penalty / loads.scales[3] / SCALE,
                     spill_bound, excess_hint - ot_hint)
        if level_values:
            peak_bound = max(b for _, b, _ in level_values)
            peak_hint = max(h for _, _, h in level_values)
            peak = (max(v for v, _, _ in level_values) if all(isinstance(v, int) for v, _, _ in level_values)
                    else var(0, peak_bound, f'peak{g}', peak_hint))
            for value, _, _ in level_values:
                add(peak >= value)
            cost(peak, level_coefficient, peak_bound, peak_hint)
    coefficients, objective_bound = objective_coefficients(
        [c for _, c, _, _ in objective], [b for _, _, b, _ in objective])
    expression = sum(c * row[0] for c, row in zip(coefficients, objective))
    hint_objective = sum(c * row[3] for c, row in zip(coefficients, objective))
    score = var(0, objective_bound, 'objective_score', hint_objective)
    add(score == expression)
    if valid_hint:
        add(score <= hint_objective)
    model.minimize(score)
    check_time()
    return model, starts, finishes, valid_hint


def _configure(solver, loads, seconds):
    solver.parameters.max_time_in_seconds = max(.001, seconds)
    solver.parameters.num_search_workers = _workers()
    solver.parameters.random_seed = loads.options.seed
    # repair_hint provoca abortos nativos en OR-Tools 9.15 con varios hilos
    # ("Check failed: heuristics.fixed_search != nullptr"); el hint ya es factible.
    solver.parameters.cp_model_presolve = True
    solver.parameters.max_presolve_iterations = 1
    solver.parameters.cp_model_probing_level = 0
    solver.parameters.symmetry_level = 0


def solve(m: Model, loads: Loads, hint_s: np.ndarray, hint_f: np.ndarray, deadline: float,
          *, plan=None) -> tuple | None:
    """Certify an incumbent first, then optimize within the remaining budget."""
    plan = plan or plan_model(m, loads, hint_s)
    if not plan.accepted:
        return hint_s, hint_f, 'heuristico', [f'CP-SAT excede el presupuesto antes de construir el modelo: {plan.estimate}.']
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return hint_s, hint_f, 'heuristico', ['CP-SAT no está disponible: falta OR-Tools; se conserva la heurística.']
    notices = (['CP-SAT usa una malla semanal de inicios; calendarios y capacidad siguen siendo diarios. La optimalidad se limita a esa malla.']
               if plan.weekly else [])
    if loads.weights[3] and loads.period == 'week':
        notices.append('CP-SAT aproxima el costo por semana; la auditoría y las métricas de costo siguen siendo diarias.')
    try:
        model, starts, finishes, valid_hint = build_model(m, loads, hint_s, hint_f, plan, deadline)
    except _BuildTimeout:
        return None
    except ValueError as exc:
        return hint_s, hint_f, 'heuristico', notices + [f'CP-SAT: {exc} Se conserva la heurística.']
    loads.rebuild(hint_s)
    incumbent_feasible = not m.violations(hint_s, hint_f) and not loads.violations()
    incumbent_value = loads.objective(hint_s, hint_f)
    best = None
    # Fixing a COMPLETE hint makes this a cheap feasibility certification. It is
    # never reported as optimal for the free model. Do not disable presolve.
    # La certificación con fix_variables_to_their_hinted_value provoca un abort nativo en
    # OR-Tools 9.15 ("Check failed: heuristics.fixed_search != nullptr") con varios hilos.
    # El hint ya se verificó en Python (hint_is_feasible + auditoría diaria): se usa directamente.
    if valid_hint and incumbent_feasible:
        best = (hint_s.copy(), hint_f.copy(), 'factible')
    remaining = deadline - perf_counter()
    if remaining > .05:
        solver = cp_model.CpSolver()
        _configure(solver, loads, remaining)
        result = solver.solve(model)
        if result in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            s = np.array([solver.value(v) if not isinstance(v, int) else v for v in starts])
            f = np.array([solver.value(v) if not isinstance(v, int) else v for v in finishes])
            # A weekly surrogate improvement need not improve the daily public
            # objective. Retain the certified seed in that case.
            loads.rebuild(s)
            candidate_value = loads.objective(s, f)
            errors = m.violations(s, f) + loads.violations()
            loads.rebuild(hint_s)
            if not errors and (not incumbent_feasible or candidate_value <= incumbent_value + 1e-9):
                best = (s, f, 'optimo' if result == cp_model.OPTIMAL else 'factible')
            elif best is not None:
                notices.append('Se conserva la semilla certificada: el objetivo diario no mejora con el sustituto semanal.')
            elif not errors and incumbent_feasible:
                return hint_s, hint_f, 'heuristico', notices + ['Se conserva la heurística: CP-SAT no mejoró el objetivo diario y no certificó la semilla.']
        elif result == cp_model.MODEL_INVALID:
            notices.append(f'CP-SAT rechazó el modelo: {model.validate()}.')
        elif result == cp_model.INFEASIBLE and best is None:
            notices.append('La malla de búsqueda CP-SAT no tiene solución con su redondeo conservador; esto no demuestra infactibilidad del proyecto original.')
    if best is not None:
        if best[2] == 'optimo':
            notices.append('La optimalidad corresponde al objetivo lineal entero de CP-SAT, no a la métrica cuadrática publicada.')
        return *best, notices
    return hint_s, hint_f, 'heuristico', notices + ['CP-SAT alcanzó el límite sin una solución; se conserva el programa heurístico.']
