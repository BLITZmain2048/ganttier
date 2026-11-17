"""Entrada columnar del panel: SQLite conserva sus float64, sin JSON intermedio.

Reproduce las reglas de control_inputs/_prepare. No agrega en SQL consumos ni
pesos: las sumas siguen el mismo orden NumPy de la implementación de referencia.
"""
from collections import namedtuple
from datetime import date
from functools import lru_cache
import sqlite3
import struct

import numpy as np

from .. import repo, services
from ..calendars import WorkCalendar
from ._core import _Prepared
from .progress import _History, _nonnegative, _percent

_Activity = namedtuple('_Activity', 'id duration_days')


class _Activities:
    """Identificadores y duraciones sin crear una ficha Python por actividad.

    El acceso escalar conserva el contrato privado anterior. Las rutas masivas
    usan las columnas; copy/updated no modifican una entrada de caché anterior.
    """
    def __init__(self, ids, durations):
        self.ids, self.durations = ids, durations

    def __len__(self):
        return len(self.ids)

    def __iter__(self):
        return (_Activity(int(aid), duration) for aid, duration in zip(self.ids, self.durations))

    def __getitem__(self, index):
        if np.isscalar(index):
            return _Activity(int(self.ids[index]), self.durations[index])
        return _Activities(self.ids[index], self.durations[index])

    def __setitem__(self, index, activity):
        self.ids[index], self.durations[index] = activity.id, activity.duration_days

    def copy(self):
        return _Activities(self.ids.copy(), self.durations.copy())

    def updated(self, activities):
        result = self.copy()
        for activity in activities:
            index = int(np.searchsorted(result.ids, activity.id))
            if index < len(result) and result.ids[index] == activity.id:
                result[index] = activity
        return result
_ACTIVITY_DTYPE = [('id','i8'),('start','M8[D]'),('finish','M8[D]'),('duration','f8'),
    ('work','f8'),('budget','f8'),('pct','f8'),('actual_start','M8[D]'),('actual_finish','M8[D]'),
    ('hours','f8'),('cost','f8'),('calendar','i8')]
_PROGRESS_DTYPE = [('id','i8'),('activity_id','i8'),('date','M8[D]'),('pct','f8'),
    ('hours','f8'),('cost','f8'),('start','M8[D]'),('finish','M8[D]')]
PROGRESS_IDS = np.dtype([('id','i8'),('activity_id','i8')])
# Límite del bloque temporal, independiente del límite de retención de caché.
MAX_READ_BYTES = 16 * 1024 * 1024


def _ordinals(values):
    # Datetime64[D] convierte ISO en C. El cero es el mismo centinela de _dates.
    return np.where(np.isnat(values), 0, values.view('i8') + 719163)


def _read(conn, sql, params, dtype):
    cursor = conn.cursor()
    cursor.row_factory = None
    try:
        # Un bloque binario evita la conversión estructurada de NumPy por fila.
        # struct conserva cada float64 de SQLite, incluso sus últimos bits; no
        # intervienen JSON, printf ni redondeos decimales. Las fechas se memoizan.
        dtype = np.dtype(dtype)
        pack = struct.Struct('=' + ''.join('d' if dtype[name].kind == 'f' else 'q'
                                          for name in dtype.names)).pack
        date_indices = [i for i, name in enumerate(dtype.names) if dtype[name].kind == 'M']
        @lru_cache(maxsize=8192)
        def day(value):
            return date.fromisoformat(value).toordinal() - 719163 if value else -(2**63)

        used = 0
        fallback = False
        def binary_row(*values):
            nonlocal used, fallback
            used += dtype.itemsize
            try:
                if used > MAX_READ_BYTES:
                    raise OverflowError('Bloque de lectura completo; usar cursor incremental.')
                values = list(values)
                for index in date_indices:
                    values[index] = day(values[index])
                return pack(*values)
            except (TypeError, ValueError, OverflowError, struct.error):
                # Conservar también los errores/conversiones de la ruta previa.
                fallback = True
                raise

        columns = cursor.execute('SELECT * FROM (' + sql + ') LIMIT 0', params).description
        names = ','.join('"' + column[0].replace('"', '""') + '"' for column in columns)
        function = '_ganttier_control_binary_row'
        conn.create_function(function, len(dtype.names), binary_row)
        try:
            try:
                raw = cursor.execute("SELECT CAST(group_concat(" + function + '(' + names +
                    "), '') AS BLOB) FROM (" + sql + ')', params).fetchone()[0]
                # Vista inmutable: la preparación copia las columnas que modifica.
                return np.frombuffer(raw or b'', dtype=dtype)
            except sqlite3.OperationalError:
                if not fallback:
                    raise
        finally:
            conn.create_function(function, len(dtype.names), None)
        return np.fromiter(cursor.execute(sql, params), dtype=dtype)
    finally:
        cursor.close()


def panel_inputs(conn, pid, basis):
    project = repo.get(conn, 'projects', pid)
    cutoff = project.data_date or date.today()
    cut = cutoff.toordinal()
    cid = project.calendar_id or repo.list_entities(conn,'calendars')[0].id
    rows = _read(conn, '''SELECT id,COALESCE(planned_start,early_start),
        COALESCE(planned_finish,early_finish),duration_days,work_hours,cost_budget,
        percent_complete,actual_start,actual_finish,actual_hours,actual_cost,COALESCE(calendar_id,?)
        FROM activities WHERE project_id=? ORDER BY id''', (cid,pid), _ACTIVITY_DTYPE)
    # El historial se ordena abajo por (actividad, fecha, ID). Ordenarlo antes
    # por ID en SQLite creaba además un B-tree temporal de todos los avances.
    # No cambia el orden de ninguna suma: todas usan `known` ya ordenado.
    progress = _read(conn, '''SELECT id,activity_id,date,percent_complete,actual_hours,
        actual_cost,actual_start,actual_finish FROM progress
        WHERE activity_id IN (SELECT id FROM activities WHERE project_id=?)''', (pid,), _PROGRESS_DTYPE)
    n = len(rows)
    ids = rows['id']
    days, pstart, pfinish = (_ordinals(progress[key]) for key in ('date','start','finish'))
    activity = np.searchsorted(ids, progress['activity_id'])
    recorded = np.zeros(n,dtype=bool)
    recorded[activity] = True
    past = days <= cut
    known = np.flatnonzero(past)
    order = np.lexsort((progress['id'][known],days[known],activity[known]))
    known = known[order]
    last = (known[np.r_[activity[known][1:] != activity[known][:-1],True]]
            if len(known) else np.empty(0,dtype=np.int64))
    current = rows['pct'].copy()
    actual_start = _ordinals(rows['actual_start'])
    actual_finish = _ordinals(rows['actual_finish'])
    future_snapshot = actual_start > cut
    current[recorded | future_snapshot] = 0
    actual_start[recorded] = 0
    actual_finish[recorded | future_snapshot] = 0
    # El inicio más antiguo explícito del historial prevalece sobre la ficha.
    earliest = np.full(n,date.max.toordinal()+1,dtype=np.int64)
    has_start = known[pstart[known] > 0]
    np.minimum.at(earliest, activity[has_start], pstart[has_start])
    if len(last):
        targets = activity[last]
        current[targets] = progress['pct'][last]
        raw_start = _ordinals(rows['actual_start'])
        actual_start[targets] = np.where(earliest[targets] <= date.max.toordinal(),earliest[targets],raw_start[targets])
        actual_finish[targets] = np.where(pfinish[last] > 0,pfinish[last],
            np.where(progress['pct'][last] >= 100,days[last],0))
    actual_start[actual_start > cut] = 0
    actual_finish[actual_finish > cut] = 0
    cs, cf = _ordinals(rows['start']), _ordinals(rows['finish'])
    cs = np.where(cs > 0,cs,actual_start)
    cf = np.where(cf > 0,cf,actual_finish)
    baseline = services._baseline(conn,project)
    # El calendario se construye con TODO el alcance, como la lectura pública.
    bounds = [cs[cs>0],cf[cf>0],actual_start[actual_start>0],actual_finish[actual_finish>0],days]
    dates = [v.toordinal() for a in (baseline or {}).values() for v in (a.start,a.finish) if v]
    lower = min([project.start_date.toordinal(),cut] + dates + [int(a.min()) for a in bounds if a.size])
    upper = max([(project.end_date or cutoff).toordinal(),cut] + dates + [int(a.max()) for a in bounds if a.size])
    upper = min(date.max.toordinal(),upper+3660)
    calendars = {int(key): WorkCalendar(repo.get(conn,'calendars',int(key)),date.fromordinal(lower),date.fromordinal(upper))
                 for key in {cid, *rows['calendar']}}
    calendar = calendars[cid]
    all_acts = _Activities(ids.copy(), rows['duration'].copy())
    progress_ids = np.empty(len(progress),dtype=PROGRESS_IDS)
    progress_ids['id'],progress_ids['activity_id'] = progress['id'],progress['activity_id']
    selected = np.ones(n,dtype=bool) if baseline is None else np.isin(ids,list(baseline))
    selected_indices = np.flatnonzero(selected)
    mapping = np.full(n,-1,dtype=np.int64)
    mapping[selected] = np.arange(len(selected_indices))
    active_rows = known[selected[activity[known]]]
    activity_at_cut = mapping[activity[active_rows]]
    history_days = days[active_rows]
    pct = progress['pct'][active_rows]
    hours, costs = progress['hours'][active_rows], progress['cost'][active_rows]
    _percent(pct)
    _nonnegative(hours,'Las HH reales')
    _nonnegative(costs,'Los costos reales')
    milestones = rows['duration'][selected] == 0
    pct = np.where(milestones[activity_at_cut],np.where(pct >= 100,100.0,0.0),pct)
    last_daily = (np.flatnonzero(np.r_[(activity_at_cut[1:] != activity_at_cut[:-1]) |
                    (history_days[1:] != history_days[:-1]),True])
                  if len(active_rows) else np.empty(0,dtype=np.int64))
    history = _History(activity_at_cut,history_days,pct,hours,costs,[],last_daily,recorded[selected])
    current = current[selected]
    _percent(current)
    current[actual_finish[selected] > 0] = 100
    current[milestones] = np.where(current[milestones] >= 100,100.0,0.0)
    acts = all_acts if baseline is None else all_acts[selected_indices]
    selected_ids = ids[selected]
    cs, cf = cs[selected], cf[selected]
    if np.any((cs > 0) & (cf > 0) & (cf < cs)):
        raise ValueError('La fecha de término no puede ser anterior al inicio.')
    if baseline is None:
        ps,pf,duration = cs,cf,rows['duration'][selected]
        weights = rows['work'][selected] if basis=='hours' else rows['budget'][selected] if basis=='cost' else np.ones(len(acts))
    else:
        plans = [baseline[int(aid)] for aid in selected_ids]
        ps = np.fromiter((p.start.toordinal() if p.start else 0 for p in plans),dtype=np.int64)
        pf = np.fromiter((p.finish.toordinal() if p.finish else 0 for p in plans),dtype=np.int64)
        duration = np.fromiter((p.duration_days for p in plans),dtype=float)
        weights = np.fromiter((p.work_hours if basis=='hours' else p.cost if basis=='cost' else 1.0 for p in plans),dtype=float)
    if np.any((ps > 0) & (pf > 0) & (pf < ps)):
        raise ValueError('La fecha de término no puede ser anterior al inicio.')
    _nonnegative(duration,'La duración')
    _nonnegative(weights,'Las HH presupuestadas' if basis=='hours' else 'El presupuesto')
    snapshots = ~recorded[selected]
    snapshot_days = np.where(snapshots,np.where(actual_start[selected]>0,actual_start[selected],cut),0)
    consumption = rows['hours'][selected] if basis=='hours' else rows['cost'][selected] if basis=='cost' else np.zeros(len(acts))
    snapshot_values = np.where(snapshots & ~future_snapshot[selected],consumption,0.0)
    _nonnegative(snapshot_values,'Los consumos de la ficha')
    selected_calendars = rows['calendar'][selected]
    unique, first = np.unique(selected_calendars,return_index=True)
    groups = [(np.flatnonzero(selected_calendars == key),calendars[int(key)]) for key in unique[np.argsort(first)]]
    effective = [calendars[int(key)] for key in selected_calendars]
    prepared = _Prepared(acts,{int(aid):i for i,aid in enumerate(acts.ids)},groups,effective,milestones,
        history,actual_finish[selected],current,duration,weights,ps,pf,cs,cf,snapshot_days,snapshot_values)
    return dict(project=project,progress=progress_ids,acts=all_acts,cutoff=cutoff,baseline=baseline,
                calendar=calendar,calendars=calendars,_compact=True,_columnar=True,_prepared=prepared)
