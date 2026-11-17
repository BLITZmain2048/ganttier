"""Recurrencias ancladas, sin deriva al ajustar fines de mes o feriados."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
import math

import numpy as np

from ganttier.domain import Occurrence, PeriodicTask
from ._calendar import WorkCalendar, calendar_hours


def _validate_task(task: PeriodicTask) -> set[int]:
    if task.frequency not in {"D", "DH", "W", "M", "Q", "S", "A", "ND", "NW", "NM", "NA"}:
        raise ValueError(f"{task.code}: frecuencia desconocida.")
    if not isinstance(task.interval, int) or task.interval < 1:
        raise ValueError(f"{task.code}: el intervalo debe ser un entero positivo.")
    if not isinstance(task.duration_days, int) or task.duration_days < 0 or not math.isfinite(task.work_hours) or task.work_hours < 0:
        raise ValueError(f"{task.code}: duración u horas de trabajo inválidas.")
    if task.duration_days == 0 and task.work_hours:
        raise ValueError(f"{task.code}: un hito no puede consumir horas de trabajo.")
    if any(not isinstance(x, int) or x < 0 for x in (task.tol_before_days, task.tol_after_days)):
        raise ValueError(f"{task.code}: las tolerancias deben ser enteros no negativos.")
    if task.preferred_weekday is not None and task.preferred_weekday not in range(7):
        raise ValueError(f"{task.code}: el día de semana debe estar entre 0 y 6.")
    if task.day_of_month is not None and task.day_of_month not in range(1, 32):
        raise ValueError(f"{task.code}: el día del mes debe estar entre 1 y 31.")
    try:
        months = {int(x.strip()) for x in task.preferred_months.split(",") if x.strip()}
    except ValueError as exc:
        raise ValueError(f"{task.code}: meses preferidos inválidos.") from exc
    if not months.issubset(set(range(1, 13))):
        raise ValueError(f"{task.code}: los meses deben estar entre 1 y 12.")
    return months


def _dates(task: PeriodicTask, start: date, end: date, months: set[int]) -> list[tuple[int, int]]:
    anchor = task.first_due or start
    daily = task.frequency in {"D", "DH", "ND", "W", "NW"}
    result: list[tuple[int, int]] = []
    interval = task.interval if task.frequency.startswith("N") else 1
    if daily:
        step = interval * (7 if task.frequency in {"W", "NW"} else 1)
        base = anchor.toordinal()
        if task.frequency in {"W", "NW"} and task.preferred_weekday is not None:
            base += (task.preferred_weekday - anchor.weekday()) % 7
        first = max(0, (start.toordinal() - base + step - 1) // step)
        for k in range(first, max(first, (end.toordinal() - base) // step + 1)):
            ordinal = base + k * step
            d = date.fromordinal(ordinal)
            if months and d.month not in months:
                continue
            if task.preferred_weekday is not None and d.weekday() != task.preferred_weekday:
                continue
            if task.day_of_month is not None and d.day != min(task.day_of_month, monthrange(d.year, d.month)[1]):
                continue
            result.append((k + 1, ordinal))
    else:
        step = interval * {"M": 1, "NM": 1, "Q": 3, "S": 6, "A": 12, "NA": 12}[task.frequency]
        base = (anchor.year - 1) * 12 + anchor.month - 1
        first = max(0, (((start.year - 1) * 12 + start.month - 1) - base) // step)
        last = ((end.year - 1) * 12 + end.month - 1 - base) // step
        for k in range(first, last + 1):
            slot = base + k * step
            if months and step > 1:
                # Frecuencias de varios meses (Q, S, A, NM>1...): el mes preferido se elige DENTRO de
                # cada período [slot, slot + step). Así "anual en octubre" ejecuta una vez por año en
                # octubre aunque la primera fecha sea en enero.
                picks = [m for m in range(slot, slot + step) if (m % 12) + 1 in months]
                if not picks:
                    continue
                slot = picks[0]
            year, month = divmod(slot, 12)
            year, month = year + 1, month + 1
            if months and month not in months:
                continue
            days = monthrange(year, month)[1]
            day = min(task.day_of_month or anchor.day, days)
            d = date(year, month, day)
            if task.preferred_weekday is not None:
                delta = (task.preferred_weekday - d.weekday()) % 7
                if delta > 3:
                    delta -= 7
                day += delta
                if day < 1:
                    day += 7
                if day > days:
                    day -= 7
                d = date(year, month, day)
            if max(start, anchor) <= d <= end:
                result.append((k + 1, d.toordinal()))
    return result


def generate_occurrences(tasks: list[PeriodicTask], horizon_start: date, horizon_end: date,
                         calendars: dict[int, WorkCalendar], default_calendar_id: int, *, resources=None, warnings=None) -> list[Occurrence]:
    """Genera objetivos y ventanas inclusivas; ajusta el inicio al laborable más cercano.

    Conserva ``due_date`` contractual. En empates prefiere el día anterior. Si no
    existe un inicio y fin dentro de la ventana, deja ambos planificados en None.
    """
    if horizon_end < horizon_start:
        raise ValueError("El fin del horizonte debe ser igual o posterior al inicio.")
    if default_calendar_id not in calendars:
        raise ValueError("No existe el calendario predeterminado.")
    start, end = horizon_start.toordinal(), horizon_end.toordinal()
    workdays: dict[int, np.ndarray] = {}
    templates: dict[tuple, list[tuple[int, date, date, date, date | None, date | None]]] = {}
    occurrences: list[Occurrence] = []
    resource_map = {r.id: r for r in (resources or [])}
    batch_cache = {}
    from ganttier.routes import validate_routes, route_checklists
    validate_routes(tasks)
    checklists = route_checklists(tasks)
    for task in tasks:
        if task.route_code:
            continue
        if not task.active:
            continue
        if task.is_daily_round:
            occurrences.extend(_daily_rounds(task, horizon_start, horizon_end, calendars, resource_map, checklists.get(task.code, [])))
            continue
        if task.quantity_total is not None or task.batch_size is not None or task.productivity_day_team is not None or task.team_ids or task.unit:
            occurrences.extend(_batch_occurrences(task, horizon_start, horizon_end, calendars, resource_map, batch_cache, warnings))
            continue
        months = _validate_task(task)
        cid = task.calendar_id if task.calendar_id is not None else default_calendar_id
        if cid not in calendars:
            raise ValueError(f"{task.code}: no existe el calendario {cid}.")
        stop = min(horizon_end, task.horizon_end or horizon_end)
        if stop < horizon_start:
            continue
        if cid not in workdays:
            workdays[cid] = np.flatnonzero(calendar_hours(calendars[cid], start, end) > 0) + start
        key = (task.frequency, task.interval, task.first_due, tuple(sorted(months)),
               task.preferred_weekday, task.day_of_month, task.tol_before_days,
               task.tol_after_days, task.duration_days, stop, cid)
        if key not in templates:
            rows = []
            days = workdays[cid]
            duration = max(1, task.duration_days)
            for index, due in _dates(task, horizon_start, stop, months):
                if task.frequency == "DH":
                    pos = int(np.searchsorted(days, due))
                    if pos == len(days) or days[pos] != due:
                        continue
                lower = max(start, due - task.tol_before_days)
                upper = min(stop.toordinal(), due + task.tol_after_days)
                lo = int(np.searchsorted(days, lower))
                hi = int(np.searchsorted(days, upper, side="right")) - duration
                planned = finish = None
                if lo <= hi:
                    pos = min(hi, max(lo, int(np.searchsorted(days, due))))
                    if pos > lo and abs(int(days[pos - 1]) - due) <= abs(int(days[pos]) - due):
                        pos -= 1
                    planned = date.fromordinal(int(days[pos]))
                    finish = date.fromordinal(int(days[pos + duration - 1]))
                rows.append((index, date.fromordinal(due), date.fromordinal(lower),
                             date.fromordinal(upper), planned, finish))
            templates[key] = rows
        for index, due, lower_date, upper_date, planned, finish in templates[key]:
            occurrences.append(Occurrence(
                periodic_task_id=task.id, occurrence_index=index, code=f"{task.code}-{index:04d}",
                name=task.name, due_date=due, window_start=lower_date, window_end=upper_date,
                duration_days=task.duration_days, work_hours=task.work_hours, crew_size=task.crew_size,
                required_skill=task.required_skill, resource_id=task.resource_id, calendar_id=cid,
                planned_start=planned, planned_finish=finish, window_hard=task.window_hard,
                location=task.location, equipment=task.equipment, requires_outage=task.requires_outage,
                priority=task.priority))
    by_id = {t.id: t for t in tasks}
    for o in occurrences:
        o.checklist_codes = list(checklists.get(by_id[o.periodic_task_id].code, []))
    return occurrences


def _batch_occurrences(task, horizon_start, horizon_end, calendars, resources, cache, warnings):
    """Expande demanda íntegra, sin compensar días de turno ni certificar fechas."""
    from dataclasses import asdict
    from decimal import Decimal, ROUND_CEILING
    from ganttier.teams import validate_batch_fields, equity_policy, _blocks
    validate_batch_fields(asdict(task))
    months = _validate_task(task)
    stop = min(horizon_end, task.horizon_end or horizon_end)
    total, batch = (Decimal(str(v)) for v in (task.quantity_total, task.batch_size))
    def effort(amount, cal):
        rate = task.productivity_hour_team or task.productivity_day_team / (task.productive_hours_day or cal.hours_per_day)
        hours = float(amount) / rate
        days = max(1, math.ceil(hours / cal.hours_per_day - 1e-12))
        return days, days * cal.hours_per_day if task.batch_full_day else hours
    count = int((total / batch).to_integral_value(rounding=ROUND_CEILING))
    members = []
    for rid in task.team_ids:
        resource = resources.get(rid)
        if resource is None or not resource.is_work_team or not resource.active or resource.project_id != task.project_id:
            raise ValueError(f"{task.code}: equipo {rid} inexistente, inactivo o ajeno al proyecto.")
        if resource.calendar_id not in calendars:
            raise ValueError(f"{task.code}: falta el calendario del equipo {resource.name}.")
        cal = calendars[resource.calendar_id].cal
        if not cal.shift_pattern and (cal.workweek != "1111100" or any(
                e.working and e.hours > 0 and e.date.weekday() >= 5 for e in cal.exceptions)):
            raise ValueError(f"{task.code}, {resource.name}: 5x2 requiere lunes a viernes y no admite turnos en sábado o domingo.")
        duration, _ = effort(min(total, batch), cal)
        block = int(cal.shift_pattern.split('x')[0]) if cal.shift_pattern else 5
        if task.batch_continuous_in_shift and duration > block:
            raise ValueError(f"{task.code}, {resource.name}: lote de {duration} días supera el bloque de turno de {block} días.")
        members.append(resource)
    n = len(members)
    short, cycle, cycle_anchor, period_days = equity_policy(task, members, calendars)
    from collections import Counter
    cycle_totals, month_totals = {}, {}
    quotient, remainder = divmod(count, n)
    key = (task.frequency, task.interval, task.first_due, tuple(sorted(months)),
           task.preferred_weekday, task.day_of_month, stop)
    if key not in cache:
        cache[key] = _dates(task, task.first_due, stop, months)
    result = []
    for ordinal, (index, due_ordinal) in enumerate(cache[key]):
        due = date.fromordinal(due_ordinal)
        if due > stop or due < date.fromordinal(max(1, horizon_start.toordinal()-max(cycle, 31))):
            continue
        if task.frequency == "DH" and not calendars[task.calendar_id or min(calendars)].is_workday(due):
            continue
        if task.frequency in {"M", "NM", "Q", "S", "A", "NA"}:
            step = {"M": 1, "NM": task.interval, "Q": 3, "S": 6, "A": 12, "NA": 12*task.interval}[task.frequency]
            slot = due.year*12 + due.month-1 + step
            year, month = divmod(slot, 12)
            lower = max(horizon_start, due.replace(day=1))
            upper = min(stop, date.fromordinal(date(year, month+1, 1).toordinal()-1))
        elif short:
            lower = due
            upper = min(stop, date.fromordinal(due_ordinal+period_days-1))
        else:
            lower = date.fromordinal(max(horizon_start.toordinal(), due_ordinal-task.tol_before_days))
            upper = date.fromordinal(min(stop.toordinal(), due_ordinal+task.tol_after_days))
        availability = [(r.name, calendars[r.calendar_id].workdays_between(lower, upper)) for r in members]
        if not short and due >= horizon_start and warnings is not None and len({days for _, days in availability}) > 1:
            warnings.append(f"{task.code}-{index:04d} ({due:%Y-%m}): días hábiles distintos: "
                            + "; ".join(f"{name}: {days} días" for name, days in availability)
                            + ". Se conserva el reparto equitativo por lotes; el planificador debe resolver la diferencia.")
        cursor = ordinal * remainder % n
        if short:
            cycle_key = (due-cycle_anchor).days//cycle
            month_key = (due.year, due.month)
            counts = cycle_totals.setdefault(cycle_key, Counter({r.id: 0 for r in members}))
            monthly = month_totals.setdefault(month_key, Counter({r.id: 0 for r in members}))
            active = [r for r, (_, days) in zip(members, availability) if days > 0]
            if not active:
                active = members  # demanda explícita; la prueba de capacidad rechazará la ocurrencia
            capacity_blocks = {r.id: _blocks(calendars[r.calendar_id], lower, upper) for r in active}
            if not task.batch_continuous_in_shift:
                capacity_blocks = {rid: [sum(blocks)] for rid, blocks in capacity_blocks.items()}
        for j in range(count):
            amount = min(batch, total-j*batch)
            duration, hours = effort(amount, calendars[members[0].calendar_id].cal)
            if short:
                fitting = [r for r in active if any(length >= duration for length in capacity_blocks[r.id])]
                member = min(fitting or active, key=lambda r: (counts[r.id], monthly[r.id], task.team_ids.index(r.id)))
                blocks = capacity_blocks[member.id]
                slots = [i for i, length in enumerate(blocks) if length >= duration]
                if slots:
                    best = min(slots, key=lambda i: blocks[i])
                    blocks[best] -= duration
                counts[member.id] += 1
                monthly[member.id] += 1
            else:
                member = members[j % n if j < quotient*n else (cursor+j-quotient*n) % n]
            if due < horizon_start:
                continue
            cal = calendars[member.calendar_id].cal
            duration, hours = effort(amount, cal)
            result.append(Occurrence(task.id, index, f"{task.code}-{index:04d}-L{j+1:02d}",
                f"{task.name} · lote {j+1}/{count}", due, lower, upper, duration,
                hours * member.max_units, member.max_units, "", member.id,
                member.calendar_id, window_hard=True, location=task.location, equipment=task.equipment,
                requires_outage=task.requires_outage, priority=task.priority,
                batch_index=j+1, quantity=float(amount), unit=task.unit, team_id=member.id,
                equity_mode="cycle" if short else "occurrence",
                batch_continuous_in_shift=task.batch_continuous_in_shift, duration_hours=hours))
    return result


def _daily_rounds(task, first, last, calendars, resources, checklist):
    """Una ronda por planta y fecha; HH fijas, sin partición en lotes."""
    from ganttier.teams import validate_batch_fields
    from dataclasses import asdict
    validate_batch_fields(asdict(task))
    result=[]
    counts={rid:0 for rid in task.team_ids}
    for index, ordinal in _dates(task, first, min(last,task.horizon_end or last), _validate_task(task)):
        day=date.fromordinal(ordinal)
        eligible=[resources[rid] for rid in task.team_ids if rid in resources and resources[rid].active
                  and resources[rid].is_work_team and resources[rid].project_id==task.project_id
                  and calendars[resources[rid].calendar_id].is_workday(day)]
        if not eligible:
            raise ValueError(f"{task.code}: no hay equipo en turno para la ronda del {day}.")
        member=min(eligible,key=lambda r:(counts[r.id],task.team_ids.index(r.id)))
        counts[member.id]+=1
        result.append(Occurrence(task.id,index,f"{task.code}-{index:04d}",task.name,
            day,day,day,1,task.work_hours,member.max_units,"",member.id,member.calendar_id,
            window_hard=True,location=task.location,equipment=task.equipment,
            team_id=member.id,equity_mode="cycle",duration_hours=task.work_hours/member.max_units,
            checklist_codes=list(checklist)))
    return result
