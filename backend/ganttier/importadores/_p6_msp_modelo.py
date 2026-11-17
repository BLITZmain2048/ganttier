"""Tablas auxiliares y validación lineal de referencias externas."""
from __future__ import annotations
import hashlib
import json
import re
from .excel import normal
from ._p6_msp_campos import table, number, boolean, fecha

AUX = {
    'PROJWBS': {'id': 'wbs_id', 'code': 'wbs_short_name|WBS Code', 'name': 'wbs_name|WBS Name', 'parent': 'parent_wbs_id|Parent WBS'},
    'TASKPRED': {'pred': 'pred_task_id|Predecessor ID', 'succ': 'task_id|Successor ID', 'type': 'pred_type|Type', 'lag': 'lag_hr_cnt|Lag Hours'},
    'RSRC': {'id': 'rsrc_id|Resource ID', 'name': 'rsrc_name|Resource Name', 'type': 'rsrc_type|Resource Type',
             'calendar': 'clndr_id|Calendar', 'units': 'max_units|Max Units', 'capacity': 'hours_per_day|Capacity Hours',
             'skills': 'skills|Habilidades', 'cost': 'cost_per_qty|Cost Hour'},
    'TASKRSRC': {'task': 'task_id|Activity ID', 'resource': 'rsrc_id|Resource ID', 'units': 'target_qty_per_hr|Units',
                 'work': 'target_qty|Work', 'actual': 'act_reg_qty|Actual Work'},
    'CALENDAR': {'id': 'clndr_id|Calendar ID', 'name': 'clndr_name|Calendar Name', 'week': 'workweek|Semana laboral',
                 'hours': 'day_hr_cnt|Hours per Day|Horas día', 'country': 'holidays_country|País feriados',
                 'exceptions': 'exceptions|Excepciones', 'blob': 'clndr_data'},
}


def aux_rows(tables, kind, errors):
    names = {normal(n): n for n in tables.names}
    if normal(kind) not in names: return []
    aliases = {normal(alias): key for key, names in AUX[kind].items() for alias in names.split('|')}
    return list(table(tables, names[normal(kind)], errors, aliases=aliases))


def components(nodes, edges):
    """Kosaraju iterativo O(V+E); sin límite de recursión en cadenas largas."""
    adj, rev = {n: [] for n in nodes}, {n: [] for n in nodes}
    for a, b in edges:
        if a in adj and b in adj: adj[a].append(b); rev[b].append(a)
    seen, order = set(), []
    for node in adj:
        if node in seen: continue
        seen.add(node)
        stack = [(node, iter(adj[node]))]
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None: order.append(current); stack.pop()
            elif child not in seen:
                seen.add(child); stack.append((child, iter(adj[child])))
    result, sizes = {}, {}
    for node in reversed(order):
        if node in result: continue
        group = len(sizes); sizes[group] = 0; stack = [node]; result[node] = group
        while stack:
            current = stack.pop(); sizes[group] += 1
            for child in rev[current]:
                if child not in result: result[child] = group; stack.append(child)
    return result, sizes


def check_graph(nodes, edges, key):
    group, sizes = components(nodes, [(a, b) for a, b, _ in edges])
    for a, b, row in edges:
        if a not in nodes or b not in nodes:
            row.error(key, f'Referencia inexistente: {a} → {b}.', 'referencia_inexistente')
        elif group[a] == group[b] and (a == b or sizes[group[a]] > 1):
            row.error(key, f'Ciclo: {a} → {b}.', 'ciclo')


def valid_week(v):
    v = str(v).strip()
    if not re.fullmatch('[01]{7}', v) or '1' not in v: raise ValueError('Semana laboral: siete dígitos 0/1, al menos un día activo.')
    return v


def valid_hours(v):
    n = number(v)
    if not 0 < n <= 24: raise ValueError('La jornada debe ser mayor que 0 y hasta 24 horas.')
    return n


def calendars(rows, s):
    result, refs, names = [], {}, set()
    def add(label, config):
        signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
        name = label + ' · ' + signature
        if name not in names: result.append(dict(name=name, **config)); names.add(name)
        return name
    config = dict(workweek=s['calendario']['semana'], hours_per_day=s['calendario']['horas_dia'],
                  holidays_country=s['calendario']['pais'] or None, exceptions=[])
    default = add('MSP/P6 SUPUESTO', config)
    ids = set()
    for row in rows:
        ident = row.get('id', required=True)
        label = row.get('name', required=True, default=ident or 'Calendario')
        if not ident: continue
        if ident in ids: row.error('id', 'Identificador de calendario duplicado.'); continue
        ids.add(ident)
        if row.get('blob'):
            row.error('blob', 'clndr_data propietario no admitido: exporte semana, horas y excepciones simples.', 'calendario_complejo')
        config = dict(workweek=row.get('week', valid_week, s['calendario']['semana']),
                      hours_per_day=row.get('hours', valid_hours, s['calendario']['horas_dia']),
                      holidays_country=row.get('country', default=s['calendario']['pais']) or None, exceptions=[])
        raw = row.get('exceptions')
        if raw:
            try:
                values = json.loads(raw)
                if not isinstance(values, list) or len(values) > 3660: raise ValueError('Excepciones: lista de hasta 3660 fechas.')
                dates = set()
                for v in values:
                    if not isinstance(v, dict) or set(v) - {'date', 'working', 'hours', 'reason'}: raise ValueError('Excepción de calendario inválida.')
                    day = fecha(v['date'], row.epoch); working = boolean(v['working'])
                    hours = number(v.get('hours', config['hours_per_day'] if working else 0))
                    if hours > 24 or (working and hours <= 0) or (not working and hours != 0) or day in dates:
                        raise ValueError('Excepción repetida o jornada inconsistente.')
                    dates.add(day)
                    config['exceptions'].append(dict(date=day, working=working, hours=hours, reason=str(v.get('reason', ''))))
            except (ValueError, KeyError, TypeError) as exc: row.error('exceptions', str(exc))
        name = add(label, config)
        refs[ident] = name
        if label in refs and refs[label] != name: row.error('name', 'Nombre de calendario ambiguo.')
        refs[label] = name
    return result, refs, default


def wbs_rows(rows):
    wbs, refs, by_id, by_code, parents = [], {}, {}, set(), []
    for row in rows:
        ident, code, name = row.get('id', required=True), row.get('code', required=True), row.get('name', required=True)
        if not ident or not code or not name: continue
        if ident in by_id or code in by_code: row.error('id', 'ID o código WBS repetido.'); continue
        by_id[ident] = row; by_code.add(code); refs[ident] = code
        wbs.append(dict(code=code, name=name, parent_code=None, sort_order=len(wbs)))
        parents.append(row.get('parent'))
    edges = []
    for node, parent, (ident, row) in zip(wbs, parents, by_id.items()):
        if parent and parent != '0':
            node['parent_code'] = refs.get(parent)
            edges.append((parent, ident, row))
    check_graph(by_id, edges, 'parent')
    return wbs, refs
