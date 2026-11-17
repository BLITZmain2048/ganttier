"""Unsolved case imported through the public API; no oracle dates or staff."""
from datetime import date, timedelta
import json
from pathlib import Path

DATA = Path(__file__).with_name('data')
ORIGIN = date(2027, 1, 1)


def ok(response):
    assert response.status_code in (200, 201, 204), response.text
    return response.json() if response.content else None


def import_case(client):
    data = json.loads((DATA / 'caso_programacion_sin_resolver.json').read_text(encoding='utf-8'))
    response = ok(client.post('/api/import/new-project', params={'mode': 'proyecto'}, files={
        'file': ('Proyecto_caso_Ganttier.xlsx', (DATA / 'Proyecto_caso_Ganttier.xlsx').read_bytes(),
                 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}))
    pid = response.get('id') or response['project']['id']
    project = ok(client.get(f'/api/projects/{pid}'))
    cid = project['calendar_id']
    # An empty Excel country cell inherits CL. Explicit null means abstract blocks.
    calendar = ok(client.get(f'/api/calendars/{cid}'))
    calendar['holidays_country'] = None
    ok(client.put(f'/api/calendars/{cid}', json=calendar))
    resources = {r['name']: r for r in ok(client.get('/api/resources', params={'project_id': pid}))}
    horizon = data['time_model']['horizon_slots']
    for person in data['personnel']:
        intervals = list(person['unavailable_intervals'])
        lo, hi = person['availability_window']
        intervals += [(0, lo), (hi, horizon)]
        for start, end in intervals:
            if end > start:
                ok(client.post(f"/api/resources/{resources[person['name']]['id']}/absences", json={
                    'start': (ORIGIN + timedelta(days=start)).isoformat(),
                    'end': (ORIGIN + timedelta(days=end-1)).isoformat(), 'hours': 0,
                    'reason': 'Caso de roles: indisponibilidad original'}))
    return pid, data


def request_body(apply=True):
    return dict(apply=apply, options=dict(role_mode='indivisible', block_hours=4,
        horizon_blocks=60, target_code='A27', allow_overtime=False,
        respect_resource_limits=True, time_limit_s=120, seed=0))


def audit_original(client, pid, data, result):
    """Recompute staffing, capacity, windows, precedence and cost from original JSON."""
    rows = ok(client.get(f'/api/projects/{pid}/activities', params={'limit': 1000}))
    resources = {r['id']: r for r in ok(client.get('/api/resources', params={'project_id': pid}))}
    people = {p['name']: p for p in data['personnel']}
    specs = {a['id']: dict(duration=a['duration_slots'], lo=a['earliest_start_slot'],
        hi=a['latest_finish_slot'], personnel=a['personnel_requirements'], equipment=a['resource_requirements'],
        preds=[(p['activity_id'], p['lag_slots']) for p in a['predecessors']]) for a in data['activities']}
    for series in data['periodic_series']:
        for k in range(1, series['occurrence_count']+1):
            lo = series['first_window_start_slot'] + series['period_slots']*(k-1)
            code = series['occurrence_id_format'].format(series_id=series['id'], occurrence=k)
            specs[code] = dict(duration=series['duration_slots'], lo=lo, hi=lo+series['window_length_slots'],
                personnel=series['personnel_requirements'], equipment=series['resource_requirements'],
                preds=[(f"{p['series_id']}_{k+p['occurrence_offset']:02d}", p['lag_slots']) for p in series['predecessors']])
    schedule = {}
    for a in rows:
        aid = str(a['id'])
        block = result['blocks'][aid]
        staff = {}
        for entry in result['role_assignment']:
            if entry['activity_id'] == a['id']:
                staff.setdefault(entry['skill'], []).append(resources[entry['resource_id']]['name'])
        schedule[a['code']] = dict(**block, staff=staff)
        if result['applied']:
            assert a['planned_start'] == result['starts'][aid]
            assert a['planned_finish'] == result['finishes'][aid]
    cost = 0
    errors = []
    for code, a in specs.items():
        s = schedule[code]
        if not (a['lo'] <= s['start'] and s['finish'] <= a['hi'] and s['finish']-s['start'] == a['duration']):
            errors.append((code, 'time'))
        if any(s['start'] < schedule[p]['finish']+lag for p, lag in a['preds']):
            errors.append((code, 'precedence'))
        used = []
        if {r: len(v) for r, v in s['staff'].items()} != a['personnel']:
            errors.append((code, 'roles'))
        for role, members in s['staff'].items():
            for name in members:
                p = people[name]
                used.append(name)
                cost += a['duration']*p['cost_per_slot']
                if role not in p['skills'] or not (p['availability_window'][0] <= s['start'] and s['finish'] <= p['availability_window'][1]):
                    errors.append((code, 'skill/availability'))
                if any(s['start'] < end and start < s['finish'] for start, end in p['unavailable_intervals']):
                    errors.append((code, 'absence'))
        if len(used) != len(set(used)):
            errors.append((code, 'distinct'))
    for t in range(data['time_model']['horizon_slots']):
        active = [code for code, s in schedule.items() if s['start'] <= t < s['finish']]
        busy = [name for code in active for members in schedule[code]['staff'].values() for name in members]
        if len(busy) != len(set(busy)):
            errors.append((t, 'overlap'))
        for equipment in data['resources']:
            if sum(specs[code]['equipment'].get(equipment['id'], 0) for code in active) > equipment['capacity']:
                errors.append((t, equipment['id']))
    return dict(errors=errors, personnel_cost=cost, A27=schedule['A27']['finish'], schedule=schedule)
