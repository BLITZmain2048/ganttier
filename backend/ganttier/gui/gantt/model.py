"""Columnar, Qt-independent Gantt data. Dates are inclusive epoch days.

No per-activity Qt objects and no recursive traversal: even damaged/deep WBS
forests remain inspectable. Arrays and row indexes are built in worker threads.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from threading import RLock

import numpy as np

EPOCH = date(1970, 1, 1).toordinal()


def day(value):
    return date.fromisoformat(str(value)[:10]).toordinal() - EPOCH if value else float("nan")


def day_iso(value):
    return date.fromordinal(EPOCH + int(value)).isoformat() if np.isfinite(value) else None


def date_array(values, n):
    # NumPy recorre la conversión de cada None a datetime64; las columnas de
    # línea base/ventanas ausentes son habituales y no necesitan ese recorrido.
    if values is None or (isinstance(values, list) and values.count(None) == n):
        return np.full(n, np.nan)
    if isinstance(values, list) and n >= 1024:
        unique = list(dict.fromkeys(values))
        if len(unique) <= n // 4:
            # Calendarios grandes repiten pocas fechas. Convertir una vez cada
            # valor conserva también NaT/None y evita repetir el parseo ISO.
            dates = np.asarray(unique, dtype="datetime64[D]")
            numeric = dates.astype(np.float64)
            numeric[np.isnat(dates)] = np.nan
            lookup = dict(zip(unique, numeric))
            return np.fromiter(map(lookup.__getitem__, values), dtype=float, count=n)
    dates = np.asarray(values, dtype="datetime64[D]")
    result = dates.astype(np.float64)
    result[np.isnat(dates)] = np.nan
    return result


class ActivityArrays:
    def __init__(self, columnar):
        if "ids" not in columnar:
            raise ValueError("Respuesta columnar sin identificadores.")
        self.n = n = len(columnar["ids"])
        for key in ("codes", "names", "starts", "finishes"):
            if key not in columnar or len(columnar[key]) != n:
                raise ValueError(f"Respuesta columnar inválida: {key}.")
        for key, values in columnar.items():
            if isinstance(values, (list, np.ndarray)) and len(values) != n:
                raise ValueError(f"Longitud columnar inválida: {key}.")
        self.ids = np.asarray(columnar["ids"], dtype=np.int64)
        self.id_to_index = dict(zip(self.ids.tolist(), range(n)))
        if len(self.id_to_index) != n:
            raise ValueError("Identificadores de actividad duplicados.")
        for attr, key in (("start", "starts"), ("finish", "finishes"), ("bs", "baseline_starts"),
                          ("bf", "baseline_finishes"), ("ws", "window_starts"), ("we", "window_ends")):
            setattr(self, attr, date_array(columnar.get(key), n))
        for attr, key, default in (("dur", "durations", np.nan), ("hh", "work_hours", np.nan),
                                   ("tf", "total_float", np.nan), ("wbs", "wbs_ids", np.nan),
                                   ("pct", "pct", 0)):
            setattr(self, attr, np.asarray(columnar[key], dtype=float) if key in columnar
                    else np.full(n, default))
        self.pct = np.nan_to_num(self.pct).clip(0, 100)
        for attr, key in (("crit", "critical"), ("outage", "outages"), ("window_hard", "window_hards")):
            setattr(self, attr, np.asarray(columnar[key], dtype=bool) if key in columnar else np.zeros(n, dtype=bool))
        for attr, key in (("codes", "codes"), ("names", "names"), ("macro", "macro_months"),
                          ("skill", "skills"), ("location", "locations"), ("status", "statuses")):
            setattr(self, attr, np.asarray(columnar[key], dtype=object) if key in columnar else np.full(n, "", dtype=object))
        kinds = np.asarray(columnar["kinds"], dtype=object) if "kinds" in columnar else np.full(n, "", dtype=object)
        self.milestone = (self.dur == 0) | (kinds == "hito") | (kinds == "milestone")
        finite = [v[np.isfinite(v)] for v in (self.start, self.finish, self.bs, self.bf, self.ws, self.we)]
        finite = [v for v in finite if len(v)]
        self.min_day = min(v.min() for v in finite) if finite else day(date.today())
        self.max_day = max(v.max() for v in finite) if finite else self.min_day + 90
        # Caché textual calculada en el trabajador de carga. Una cadena por fila,
        # no objetos Qt; las copias guardan referencias, no duplican las cadenas.
        self._search_sources = tuple(v.copy() for v in (self.codes, self.names, self.skill, self.location))
        self._search_text = [f"{c or ''}\0{n or ''}\0{s or ''}\0{l or ''}".casefold()
                             for c, n, s, l in zip(*self._search_sources)]
        self._search_lock = RLock()

    def filter(self, text="", critical=False, outage=False):
        mask = self.crit.copy() if critical else np.ones(self.n, dtype=bool)
        if outage:
            mask &= self.outage
        text = text.strip().casefold()
        if text:
            mask &= self._text_filter(text)
        return mask

    def _text_filter(self, text):
        # Dos reconstrucciones de filtro pueden solaparse en el pool de Qt.
        # Publicar texto y referencias juntos evita una caché incoherente.
        with self._search_lock:
            sources = (self.codes, self.names, self.skill, self.location)
            dirty = np.zeros(self.n, dtype=bool)
            for current, previous in zip(sources, self._search_sources):
                dirty |= current != previous
            for i in np.flatnonzero(dirty):
                values = tuple(v[i] for v in sources)
                c, n, s, l = values
                self._search_text[i] = f"{c or ''}\0{n or ''}\0{s or ''}\0{l or ''}".casefold()
                for previous, value in zip(self._search_sources, values):
                    previous[i] = value
            if "\0" in text:
                # Un separador en el texto buscado no debe cruzar dos campos.
                return np.fromiter((any(text in str(v or "").casefold() for v in row)
                                     for row in zip(*sources)), bool, self.n)
            return np.fromiter((text in row for row in self._search_text), bool, self.n)


class RowModel:
    def __init__(self, arrays, wbs=(), *, grouped=True, collapsed=(), text="", critical=False, sort="none", outage=False):
        self.arrays = a = arrays
        # Opciones con que se construyó: permiten reconstruir la vista tras un cambio local (A4-12).
        self.options = dict(wbs=wbs, grouped=grouped, collapsed=collapsed, text=text, critical=critical, sort=sort,
                            outage=outage)
        mask = a.filter(text, critical, outage)
        order = np.flatnonzero(mask)
        if sort in ("codigo", "nombre", "inicio"):
            values = {"codigo": a.codes, "nombre": a.names, "inicio": a.start}[sort]
            order = order[np.argsort(values[order], kind="stable")]
        self.matched_count = len(order)
        self.groups = []
        self._members, self._parents, self._children, self._aggregates = {}, {}, {}, {}
        rows, levels = [], []
        if grouped and wbs:
            nodes = {w["id"]: w for w in wbs}
            children, forest, acts = defaultdict(list), defaultdict(list), defaultdict(list)
            for w in nodes.values():
                parent = w.get("parent_id")
                children[parent if parent in nodes else None].append(w["id"])
            for siblings in children.values():
                siblings.sort(key=lambda wid: (nodes[wid].get("sort_order", 0), nodes[wid]["code"]))
            visited, traversal = set(), []
            for root in children[None] + list(nodes):
                if root in visited:
                    continue
                stack = [(root, None, 0)]
                while stack:
                    wid, parent, level = stack.pop()
                    if wid in visited:
                        continue
                    visited.add(wid)
                    traversal.append((wid, parent, level))
                    forest[parent].append(wid)
                    stack.extend((child, wid, level + 1) for child in reversed(children[wid]))
            for i in order.tolist():
                wid = a.wbs[i]
                acts[wid if wid in nodes else None].append(i)
            aggregates = {}
            self._parents = {wid: parent for wid, parent, _ in traversal}
            self._children = forest
            self._aggregates = aggregates
            for wid, parent, level in reversed(traversal):
                indices = np.asarray(acts[wid], dtype=np.int64)
                self._members[wid] = indices
                starts, finishes = a.start[indices], a.finish[indices]
                starts, finishes = starts[np.isfinite(starts)], finishes[np.isfinite(finishes)]
                weight = np.where(a.hh[indices] > 0, a.hh[indices], np.maximum(1, np.nan_to_num(a.dur[indices])))
                agg = [float(starts.min()) if len(starts) else np.inf,
                       float(finishes.max()) if len(finishes) else -np.inf,
                       len(indices), float(weight.sum()), float(np.dot(weight, a.pct[indices]))]
                for child in forest[wid]:
                    sub = aggregates[child]
                    agg = [min(agg[0], sub[0]), max(agg[1], sub[1]), agg[2] + sub[2],
                           agg[3] + sub[3], agg[4] + sub[4]]
                aggregates[wid] = agg
            collapsed = set(collapsed)
            stack = [(wid, 0, False) for wid in reversed(forest[None])]
            while stack:
                wid, level, is_activity = stack.pop()
                if is_activity:
                    rows.extend(wid)
                    levels.extend([level] * len(wid))
                    continue
                start, finish, count, weight, earned = aggregates[wid]
                if (text or critical or outage) and not count:
                    continue
                self.groups.append(dict(node=nodes[wid], start=start, finish=finish, count=count,
                                        pct=earned / weight if weight else 0, collapsed=wid in collapsed))
                rows.append(-len(self.groups))
                levels.append(level)
                if wid not in collapsed:
                    # Un lote por WBS evita 250.003 tuplas y vueltas de la pila.
                    stack.append((acts[wid], level + 1, True))
                    stack.extend((child, level + 1, False) for child in reversed(forest[wid]))
            rows.extend(acts[None])
            levels.extend([0] * len(acts[None]))
            self.rows = np.asarray(rows, dtype=np.int32)
            self.levels = np.asarray(levels, dtype=np.uint32)
        else:
            self.rows = order.astype(np.int32)
            self.levels = np.zeros(len(order), dtype=np.uint32)
        self.row_of_act = np.full(a.n, -1, dtype=np.int32)
        visible = self.rows >= 0
        self.row_of_act[self.rows[visible]] = np.flatnonzero(visible)
        self._group_by_wid = {g["node"]["id"]: i for i, g in enumerate(self.groups)}
        self._group_rows = np.flatnonzero(~visible)

    def update_dates(self, i):
        """Actualiza solo el WBS afectado y sus ancestros; conserva filas y relaciones."""
        a = self.arrays
        wid = a.wbs[i]
        changed = []
        while wid in self._parents:
            indices = self._members[wid]
            starts, finishes = a.start[indices], a.finish[indices]
            starts, finishes = starts[np.isfinite(starts)], finishes[np.isfinite(finishes)]
            weight = np.where(a.hh[indices] > 0, a.hh[indices], np.maximum(1, np.nan_to_num(a.dur[indices])))
            agg = [float(starts.min()) if len(starts) else np.inf,
                   float(finishes.max()) if len(finishes) else -np.inf,
                   len(indices), float(weight.sum()), float(np.sum(weight * a.pct[indices]))]
            for child in self._children[wid]:
                sub = self._aggregates[child]
                agg = [min(agg[0], sub[0]), max(agg[1], sub[1]), agg[2]+sub[2], agg[3]+sub[3], agg[4]+sub[4]]
            self._aggregates[wid] = agg
            group_index = self._group_by_wid.get(wid)
            if group_index is not None:
                self.groups[group_index].update(start=agg[0], finish=agg[1], count=agg[2],
                                                 pct=agg[4]/agg[3] if agg[3] else 0)
                changed.append(int(self._group_rows[group_index]))
            wid = self._parents[wid]
        return changed


class RelationIndex:
    """CSR adjacency indexed by visible predecessor row; never scan all edges to paint."""
    def __init__(self, relations, arrays, rows):
        indexed = []
        lookup = arrays.id_to_index
        for rel in relations:
            pi, si = lookup.get(rel["pred_id"]), lookup.get(rel["succ_id"])
            if pi is None or si is None:
                continue
            rp, rs = int(rows.row_of_act[pi]), int(rows.row_of_act[si])
            if rp >= 0 and rs >= 0:
                indexed.append((rp, rs, pi, si, rel.get("type", "FS"), rel.get("lag_days", 0)))
        indexed.sort(key=lambda edge: (edge[0], edge[1]))
        self.edges = indexed
        self.successor_rows = np.asarray([e[1] for e in indexed], dtype=np.int32)
        counts = np.bincount([e[0] for e in indexed], minlength=len(rows.rows))
        self.offsets = np.concatenate(([0], counts.cumsum()))

    def visible(self, first, end):
        first = min(len(self.offsets) - 1, max(0, first))
        end = min(len(self.offsets) - 1, max(first, end))
        for row in range(first, end):
            start, finish = int(self.offsets[row]), int(self.offsets[row + 1])
            successors = self.successor_rows[start:finish]
            low, high = np.searchsorted(successors, [first, end])
            yield from self.edges[start + int(low):start + int(high)]
