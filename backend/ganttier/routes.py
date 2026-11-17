"""Rutas como maestras programables; los ítems conservan identidad y código OM-WBS.

La propuesta agrupa por objeto, planta, recurrencia y meses. Es editable antes
de generar. Las rondas suman HH fijas de ítems diarios, sin multiplicar inventario.
"""
from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
from . import repo


def route_checklists(tasks):
    result = defaultdict(list)
    for t in tasks:
        if t.active and t.route_code:
            result[t.route_code].append(t.code)
    return {k: sorted(v) for k, v in result.items()}


def signature(t):
    return (t.location, t.equipment, t.frequency, t.interval,
            tuple(sorted({int(m) for m in t.preferred_months.split(',') if m.strip()})),
            t.first_due, t.horizon_end, t.day_of_month, t.preferred_weekday)


def validate_routes(tasks):
    routes = {t.code: t for t in tasks if t.is_route}
    children = defaultdict(list)
    for t in tasks:
        if not t.route_code:
            continue
        r = routes.get(t.route_code)
        if r is None or r.project_id != t.project_id or t.is_route:
            raise ValueError(f'{t.code}: ruta inexistente, ajena o anidada.')
        if t.active and not r.active:
            raise ValueError(f'{t.code}: ítem activo en ruta inactiva.')
        if r.is_daily_round:
            if t.frequency != 'D' or t.location != r.location or signature(t)[2:] != signature(r)[2:]:
                raise ValueError(f'{t.code}: ronda con planta o calendario de recurrencia incompatibles.')
        elif signature(t) != signature(r):
            raise ValueError(f'{t.code}: objeto, planta, frecuencia, meses o fechas no coinciden con la ruta.')
        if t.active:
            children[r.code].append(t)
    for r in routes.values():
        if r.active and not children[r.code]:
            raise ValueError(f'{r.code}: ruta activa sin checklist activo.')
        if r.is_daily_round and abs(r.work_hours-sum(t.work_hours for t in children[r.code])) > 1e-8:
            raise ValueError(f'{r.code}: HH de ronda distintas de la suma de sus ítems.')


def propose_routes(conn, pid, *, apply=False, team_ids=None):
    """Propuesta determinista, sin cambios salvo apply=True; no altera rutas existentes.

    Para rutas cuantificadas usa el rendimiento menor y lote menor del grupo;
    son parámetros iniciales SUPUESTOS, visibles y editables. Sin cantidad
    común, conserva suma de HH previas. No inventa cantidades faltantes.
    """
    tasks = repo.list_entities(conn, 'periodic_tasks', pid)
    groups = defaultdict(list)
    for t in tasks:
        if not t.active or t.is_route or t.route_code:
            continue
        key = signature(t)
        if t.frequency == 'D':
            key = (t.location, 'RONDA DIARIA', *key[2:])
        else:
            # Población completa y muestra del 15 % no son el mismo alcance.
            key = (*key, t.quantity_total, t.unit, tuple(t.team_ids))
        groups[key].append(t)
    proposals = []
    ids = team_ids or [r.id for r in repo.list_entities(conn,'resources',pid) if r.is_work_team and r.active]
    for key, items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        first = items[0]
        daily = first.frequency == 'D'
        code = ('RONDA-' if daily else 'RUTA-') + sha256(repr(key).encode()).hexdigest()[:12].upper()
        values = asdict(first)
        values.pop('id')
        values.update(code=code, name=('Ronda diaria' if daily else 'Ruta '+first.equipment)+' · '+first.location,
                      is_route=True, is_daily_round=daily, route_code='', resource_id=None,
                      cost_per_occurrence=sum(t.cost_per_occurrence for t in items),
                      work_hours=sum(t.work_hours for t in items), required_skill='')
        if daily:
            values.update(equipment='RONDA DIARIA', quantity_total=None, batch_size=None,
                          productivity_day_team=None, productivity_hour_team=None, unit='',team_ids=ids,
                          duration_days=1, window_hard=True, tol_before_days=0, tol_after_days=0)
        elif (all(t.quantity_total is not None for t in items)
              and len({(t.quantity_total,t.unit,tuple(t.team_ids)) for t in items})==1):
            values['batch_size'] = min(t.batch_size for t in items)
            # Tasas heterogéneas requieren edición explícita, sin sumar recorridos.
            if all(t.productivity_hour_team is None for t in items):
                values['productivity_day_team'] = min(t.productivity_day_team for t in items)
        else:
            values.update(quantity_total=None,batch_size=None,productivity_day_team=None,
                          productivity_hour_team=None,unit='',team_ids=[])
        proposals.append(dict(values, checklist_codes=sorted(t.code for t in items),
                              assumption='SUPUESTO: recorrido agrupado; confirmar alcance y rendimiento.'))
    if apply:
        with repo.transaction(conn):
            for proposed in proposals:
                values={k:v for k,v in proposed.items() if k not in {'checklist_codes','assumption'}}
                repo.create(conn,'periodic_tasks',values)
                for code in proposed['checklist_codes']:
                    t=next(t for t in tasks if t.code==code)
                    repo.update(conn,'periodic_tasks',t.id,dict(route_code=proposed['code']))
            validate_routes(repo.list_entities(conn,'periodic_tasks',pid))
    return repo.json_value(proposals)


def checklist_report(conn, pid):
    """Cumplimiento programado, distinto del avance real registrado."""
    tasks={t.id:t for t in repo.list_entities(conn,'periodic_tasks',pid)}
    groups=defaultdict(list)
    for a in repo.list_entities(conn,'activities',pid):
        if a.checklist_codes:
            groups[(a.periodic_task_id,a.occurrence_index)].append(a)
    rows=[]
    for (tid,index), acts in groups.items():
        programmed=all(a.planned_start and a.planned_finish for a in acts)
        completed=all(a.actual_finish or a.percent_complete>=100 for a in acts)
        for code in acts[0].checklist_codes:
            rows.append(dict(route=tasks[tid].code,item=code,occurrence=index,
                             scheduled=programmed,completed=completed,
                             finish=max((a.planned_finish for a in acts if a.planned_finish),default=None)))
    return repo.json_value(rows)
