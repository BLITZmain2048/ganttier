"""Caché acotada y coherente, con actualización selectiva de avances.

Las escrituras externas invalidan mediante data_version. Solo los POST/DELETE
bajo progress_write conservan las entradas no editadas. Las reducciones se
repiten en el orden original: sumar/restar totales cambiaría sus últimos bits.
"""
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
import sqlite3
from threading import RLock, Event

import numpy as np

from .. import repo
from .inputs import control_inputs
from ._core import _calculate, _prepare, update_prepared
from .evm import from_calculation as evm
from .scurve import from_calculation as scurve

_lock = RLock()
_databases = OrderedDict()
_pending = {}
MAX_DATABASES = 4
MAX_RESULTS = 6
# Solo una entrada por base conserva fichas, máximo 150k actividades / 500k avances.
MAX_ACTIVITIES = 150_000
MAX_PROGRESS = 500_000


@dataclass
class _Value:
    calculation: object
    has_activities: bool
    cut: date
    data: dict | None = None
    dirty: frozenset = frozenset()
    prepared: object = None


def clear():
    with _lock:
        for observer, _, _ in _databases.values():
            observer.close()
        _databases.clear()


def _identity(conn):
    filename = conn.execute('PRAGMA database_list').fetchone()[2]
    if not filename:
        return None
    stat = Path(filename).stat()
    return (filename, stat.st_dev, stat.st_ino, stat.st_ctime_ns)


def _value(data, basis, prepared=None):
    args = (data['acts'], data['baseline'], data['progress'], data['calendar'], data['cutoff'])
    if prepared is None:
        prepared = data.get('_prepared')
    if '_prepared' in data:
        data = {k:v for k,v in data.items() if k != '_prepared'}
    if prepared is None:
        prepared = _prepare(*args, basis=basis, calendars=data['calendars'])
    c = _calculate(*args, basis=basis, calendars=data['calendars'], prepared=prepared)
    retained = data if len(data['acts']) <= MAX_ACTIVITIES and len(data['progress']) <= MAX_PROGRESS else None
    return _Value(c, bool(data['acts']), data['cutoff'], retained, prepared=prepared if retained is not None else None)


def _compute(conn, pid, period, basis):
    return _value(control_inputs(conn, pid, compact=True, basis=basis), basis)


def _refresh(conn, pid, basis, previous):
    old = previous.data
    updates = control_inputs(conn, pid, activity_ids=previous.dirty, context=old)
    by_id = {a.id: a for a in updates['acts']}
    acts = (old['acts'].updated(updates['acts']) if hasattr(old['acts'], 'updated')
            else [by_id.get(a.id, a) for a in old['acts']])
    if old.get('_columnar'):
        from .columnar import PROGRESS_IDS
        keep = ~np.isin(old['progress']['activity_id'],list(previous.dirty))
        changed = np.asarray([(p.id,p.activity_id) for p in updates['progress']],dtype=PROGRESS_IDS)
        progress = np.r_[old['progress'][keep],changed]
        progress = progress[np.argsort(progress['id'],kind='stable')]
    else:
        progress = [p for p in old['progress'] if p.activity_id not in previous.dirty]
        progress.extend(updates['progress'])
        progress.sort(key=lambda p: p.id)
    # Fechas muy alejadas pueden ampliar el horizonte de calendario. La ruta
    # completa conserva su semántica y sus errores en esos casos excepcionales.
    dates = [v for a in updates['acts'] for v in (a.start,a.finish,a.actual_start,a.actual_finish) if v]
    dates.extend(p.date for p in updates['progress'])
    cal = old['calendar']
    if dates and (min(dates) < cal.horizon_start or max(dates) > cal.horizon_end):
        return _compute(conn, pid, 'week', basis)
    prepared = update_prepared(previous.prepared, updates['acts'], updates['progress'],
        old['baseline'], old['calendar'], old['cutoff'], basis, old['calendars'])
    return _value(dict(old, acts=acts, progress=progress), basis, prepared)


def _render(value, period, basis, max_points=None):
    c, cut = value.calculation, value.cut
    return dict(scurve=scurve(c, value.has_activities, cut, period, basis, max_points=max_points), evm=evm(c, cut, basis))


@contextmanager
def progress_write(conn, pid, activity_ids):
    """Reserva SQLite antes de observar y publica solo un commit propio aislado.

    El observador y el escritor tienen contadores independientes. Comprobar el
    del escritor a ambos lados de la lectura del observador detecta cualquier
    commit externo entre el nuestro y la publicación. Las transacciones del
    llamador conservan la invalidación normal (incluido rollback).
    """
    nested = conn.in_transaction
    identity = _identity(conn)
    with repo.transaction(conn, immediate=True):
        with _lock:
            entry = _databases.get(identity)
            token = None
            if entry is not None and not nested:
                observer, version, results = entry
                current = observer.execute('PRAGMA data_version').fetchone()[0]
                if current == version:
                    token = (observer, version, results, conn.execute('PRAGMA data_version').fetchone()[0])
            yield
    if token is None:
        return
    with _lock:
        observer, version, results, own_version = token
        entry = _databases.get(identity)
        if entry is None or entry[0] is not observer or entry[1] != version:
            return
        current = observer.execute('PRAGMA data_version').fetchone()[0]
        if conn.execute('PRAGMA data_version').fetchone()[0] != own_version:
            results.clear()
        else:
            changed = frozenset(activity_ids)
            for key, value in list(results.items()):
                if key[0] == pid:
                    if value.data is None:
                        del results[key]
                    else:
                        results[key] = replace(value, dirty=value.dirty | changed)
        _databases[identity] = (observer, current, results)


def dashboard(conn, pid, period='week', basis='hours', *, max_points=None):
    if period not in {'day','week','month'} or basis not in {'hours','cost','count'}:
        raise ValueError('Período o base de control inválidos.')
    if max_points is not None and not 4 <= max_points <= 4096:
        raise ValueError('El límite visible debe estar entre 4 y 4096 puntos.')
    identity = _identity(conn)
    if identity is None or conn.in_transaction:
        return _render(_compute(conn, pid, period, basis), period, basis, max_points)
    key = (pid, basis, date.today())
    with _lock:
        if identity not in _databases:
            observer = sqlite3.connect(Path(identity[0]).as_uri() + '?mode=ro', uri=True, check_same_thread=False)
            _databases[identity] = (observer, None, OrderedDict())
            while len(_databases) > MAX_DATABASES:
                _, (old, _, _) = _databases.popitem(last=False)
                old.close()
        _databases.move_to_end(identity)
        observer, version, results = _databases[identity]
        current = observer.execute('PRAGMA data_version').fetchone()[0]
        if version != current:
            results.clear()
            _databases[identity] = (observer, current, results)
        previous = results.get(key)
        if previous is not None and not previous.dirty:
            results.move_to_end(key)
            return _render(previous, period, basis, max_points)
        pending_key = (identity, current, key)
        waiting = _pending.get(pending_key)
        if waiting is None:
            _pending[pending_key] = Event()
    if waiting is not None:
        waiting.wait()
        return dashboard(conn, pid, period, basis, max_points=max_points)
    try:
        with repo.transaction(conn):
            result = (_refresh(conn,pid,basis,previous) if previous is not None and previous.data is not None
                      else _compute(conn,pid,period,basis))
        with _lock:
            if identity in _databases and _databases[identity][0] is observer:
                latest = observer.execute('PRAGMA data_version').fetchone()[0]
                if current == latest:
                    for other, value in list(results.items()):
                        if other != key and value.data is not None:
                            if value.dirty:
                                del results[other]
                            else:
                                results[other] = replace(value, data=None, prepared=None)
                    # _render crea listas independientes; no copiar fichas ni arrays.
                    results[key] = result
                    results.move_to_end(key)
                    while len(results) > MAX_RESULTS:
                        results.popitem(last=False)
    finally:
        with _lock:
            _pending.pop(pending_key).set()
    return _render(result, period, basis, max_points)
