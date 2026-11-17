import type { Columnar, Relation, WBSNode } from "../../api/types";
import { isoToDay, todayDay } from "../../utils/dates";

/** Datos de actividades en arreglos tipados (índice = posición en Columnar). */
export interface ActArrays {
  n: number;
  ids: Float64Array;
  codes: string[];
  names: string[];
  start: Float64Array;   // número de día (NaN si no hay)
  finish: Float64Array;
  bs: Float64Array;
  bf: Float64Array;
  ws: Float64Array;
  we: Float64Array;
  pct: Float32Array;
  crit: Uint8Array;
  tf: Float64Array;
  dur: Float64Array;
  hh: Float64Array;
  wbs: Float64Array;     // wbs_id o NaN
  milestone: Uint8Array;
  macro: (string | null)[];
  skill: string[];
  location: string[];
  status: string[];
  outage: Uint8Array;
  windowHard: Uint8Array;
  hasHours: boolean;
  idToIndex: Map<number, number>;
  minDay: number;
  maxDay: number;
}

function dayArr(list: (string | null)[] | undefined, n: number): Float64Array {
  const a = new Float64Array(n);
  if (!list) { a.fill(NaN); return a; }
  for (let i = 0; i < n; i++) a[i] = isoToDay(list[i]);
  return a;
}

export function buildArrays(c: Columnar): ActArrays {
  if (!Array.isArray(c.ids)) throw new Error("Respuesta columnar inválida: faltan los identificadores.");
  const n = c.ids.length;
  for (const key of ["codes", "names", "starts", "finishes"] as const) {
    if (!Array.isArray(c[key]) || c[key].length !== n) throw new Error(`Respuesta columnar inválida: ${key} no coincide con los identificadores.`);
  }
  for (const [key, list] of Object.entries(c)) {
    if (Array.isArray(list) && list.length !== n) throw new Error(`Respuesta columnar inválida: longitud de ${key}.`);
  }
  const ids = Float64Array.from(c.ids);
  const start = dayArr(c.starts, n);
  const finish = dayArr(c.finishes, n);
  const bs = dayArr(c.baseline_starts, n);
  const bf = dayArr(c.baseline_finishes, n);
  const ws = dayArr(c.window_starts, n);
  const we = dayArr(c.window_ends, n);
  const pct = new Float32Array(n);
  const crit = new Uint8Array(n);
  const tf = new Float64Array(n).fill(NaN);
  const dur = new Float64Array(n).fill(NaN);
  const hh = new Float64Array(n).fill(NaN);
  const wbs = new Float64Array(n).fill(NaN);
  const milestone = new Uint8Array(n);
  const outage = new Uint8Array(n);
  const windowHard = new Uint8Array(n);
  const idToIndex = new Map<number, number>();
  let minDay = Infinity, maxDay = -Infinity;
  for (let i = 0; i < n; i++) {
    idToIndex.set(ids[i], i);
    pct[i] = c.pct ? Number(c.pct[i] ?? 0) : 0;
    crit[i] = c.critical && c.critical[i] ? 1 : 0;
    if (c.total_float && c.total_float[i] !== null && c.total_float[i] !== undefined) tf[i] = Number(c.total_float[i]);
    if (c.durations?.[i] != null) dur[i] = Number(c.durations[i]);
    if (c.work_hours?.[i] != null) hh[i] = Number(c.work_hours[i]);
    if (c.wbs_ids && c.wbs_ids[i] !== null && c.wbs_ids[i] !== undefined) wbs[i] = Number(c.wbs_ids[i]);
    const kind = c.kinds ? c.kinds[i] : "";
    milestone[i] = kind === "milestone" || kind === "hito" || dur[i] === 0 ? 1 : 0;
    outage[i] = c.outages && c.outages[i] ? 1 : 0;
    windowHard[i] = c.window_hards?.[i] ? 1 : 0;
    for (const days of [start, finish, bs, bf, ws, we]) {
      if (Number.isFinite(days[i])) {
        minDay = Math.min(minDay, days[i]); maxDay = Math.max(maxDay, days[i]);
      }
    }
  }
  if (!Number.isFinite(minDay)) { minDay = todayDay(); maxDay = minDay + 90; }
  return {
    n, ids, codes: c.codes, names: c.names, start, finish, bs, bf, ws, we, pct, crit, tf, dur, hh, wbs, milestone,
    macro: c.macro_months ?? new Array<string | null>(n).fill(null),
    skill: c.skills ?? new Array<string>(n).fill(""),
    location: c.locations ?? new Array<string>(n).fill(""),
    status: c.statuses ?? new Array<string>(n).fill(""),
    outage, windowHard, hasHours: c.work_hours?.length === n, idToIndex, minDay, maxDay,
  };
}

/** Filas visibles: >= 0 índice de actividad; < 0 encabezado WBS (-(índice en wbsRows)-1). */
export interface RowModel {
  rows: Int32Array;
  level: Uint32Array;
  wbsRows: { node: WBSNode; start: number; finish: number; count: number; pct: number }[];
  rowOfAct: Int32Array; // índice de fila por actividad (-1 si oculta)
}

export interface RowOptions {
  groupByWbs: boolean;
  collapsed: Set<number>;
  filter: Uint8Array | null; // 1 = visible
  sort: "codigo" | "inicio" | "none";
}

export function buildRows(a: ActArrays, wbs: WBSNode[], opt: RowOptions): RowModel {
  const order: number[] = [];
  for (let i = 0; i < a.n; i++) if (!opt.filter || opt.filter[i]) order.push(i);
  if (opt.sort === "inicio") {
    order.sort((x, y) => ((Number.isFinite(a.start[x]) ? a.start[x] : Infinity) - (Number.isFinite(a.start[y]) ? a.start[y] : Infinity)) || (x - y));
  } else if (opt.sort === "codigo") {
    order.sort((x, y) => (a.codes[x] < a.codes[y] ? -1 : a.codes[x] > a.codes[y] ? 1 : 0));
  }
  const rows: number[] = [];
  const level: number[] = [];
  const wbsRows: RowModel["wbsRows"] = [];
  if (opt.groupByWbs && wbs.length > 0) {
    const children = new Map<number | null, WBSNode[]>();
    const known = new Set(wbs.map((w) => w.id));
    for (const w of wbs) {
      const p = w.parent_id !== null && known.has(w.parent_id) ? w.parent_id : null;
      const l = children.get(p) ?? [];
      l.push(w);
      children.set(p, l);
    }
    const collator = new Intl.Collator("es", { numeric: true });
    for (const l of children.values()) l.sort((x, y) => (x.sort_order - y.sort_order) || collator.compare(x.code, y.code));
    const actsByWbs = new Map<number, number[]>();
    const orphans: number[] = [];
    for (const i of order) {
      const w = a.wbs[i];
      if (w === w && known.has(w)) {
        const l = actsByWbs.get(w) ?? [];
        l.push(i);
        actsByWbs.set(w, l);
      } else orphans.push(i);
    }
    // Iterative spanning forest: cycles/orphans cannot hide rows or overflow the stack.
    const visited = new Set<number>();
    const traversal: { node: WBSNode; lv: number; parent: number | null }[] = [];
    const forest = new Map<number | null, WBSNode[]>();
    const visit = (root: WBSNode) => {
      const stack = [{ node: root, lv: 0, parent: null as number | null }];
      while (stack.length) {
        const entry = stack.pop()!;
        if (visited.has(entry.node.id)) continue;
        visited.add(entry.node.id); traversal.push(entry);
        const siblings = forest.get(entry.parent) ?? [];
        siblings.push(entry.node); forest.set(entry.parent, siblings);
        const ch = children.get(entry.node.id) ?? [];
        for (let j = ch.length - 1; j >= 0; j--) stack.push({ node: ch[j], lv: entry.lv + 1, parent: entry.node.id });
      }
    };
    for (const root of children.get(null) ?? []) visit(root);
    for (const node of wbs) if (!visited.has(node.id)) visit(node);
    const agg = new Map<number, { start: number; finish: number; count: number; w: number; e: number }>();
    for (let j = traversal.length - 1; j >= 0; j--) {
      const { node } = traversal[j];
      let start = Infinity, finish = -Infinity, count = 0, w = 0, e = 0;
      for (const i of actsByWbs.get(node.id) ?? []) {
        if (Number.isFinite(a.start[i])) start = Math.min(start, a.start[i]);
        if (Number.isFinite(a.finish[i])) finish = Math.max(finish, a.finish[i]);
        count++;
        const wt = Number.isFinite(a.hh[i]) && a.hh[i] > 0 ? a.hh[i] : Math.max(1, Number.isFinite(a.dur[i]) ? a.dur[i] : 1);
        w += wt; e += wt * a.pct[i];
      }
      for (const ch of forest.get(node.id) ?? []) {
        const r = agg.get(ch.id)!;
        start = Math.min(start, r.start); finish = Math.max(finish, r.finish);
        count += r.count; w += r.w; e += r.e;
      }
      agg.set(node.id, { start, finish, count, w, e });
    }
    const stack: { node: WBSNode; lv: number; act?: number }[] =
      (forest.get(null) ?? []).map(node => ({ node, lv: 0 })).reverse();
    while (stack.length) {
      const { node, lv, act } = stack.pop()!;
      if (act !== undefined) { rows.push(act); level.push(lv); continue; }
      const r = agg.get(node.id)!;
      if (opt.filter && r.count === 0) continue;
      const wi = wbsRows.length;
      wbsRows.push({ node, ...r, pct: r.w > 0 ? r.e / r.w : 0 });
      rows.push(-wi - 1); level.push(lv);
      if (opt.collapsed.has(node.id)) continue;
      const acts = actsByWbs.get(node.id) ?? [], ch = forest.get(node.id) ?? [];
      for (let j = acts.length - 1; j >= 0; j--) stack.push({ node, lv: lv + 1, act: acts[j] });
      for (let j = ch.length - 1; j >= 0; j--) stack.push({ node: ch[j], lv: lv + 1 });
    }
    for (const i of orphans) { rows.push(i); level.push(0); }
  } else {
    for (const i of order) { rows.push(i); level.push(0); }
  }
  const rowOfAct = new Int32Array(a.n).fill(-1);
  for (let r = 0; r < rows.length; r++) if (rows[r] >= 0) rowOfAct[rows[r]] = r;
  return { rows: Int32Array.from(rows), level: Uint32Array.from(level), wbsRows, rowOfAct };
}

export interface RelArrays {
  n: number;
  pred: Int32Array; // índice de actividad
  succ: Int32Array;
  type: Uint8Array; // 0 FS 1 SS 2 FF 3 SF
  offsets: Int32Array; // outgoing edges by predecessor
  edges: Int32Array;
  ids: Float64Array;
}

export function buildRelations(rels: Relation[], a: ActArrays): RelArrays {
  const pred: number[] = [], succ: number[] = [], type: number[] = [], ids: number[] = [];
  const t = { FS: 0, SS: 1, FF: 2, SF: 3 } as const;
  for (const r of rels) {
    const p = a.idToIndex.get(r.pred_id), s = a.idToIndex.get(r.succ_id);
    if (p === undefined || s === undefined) continue;
    pred.push(p); succ.push(s); type.push(t[r.type] ?? 0); ids.push(r.id);
  }
  const offsets = new Int32Array(a.n + 1);
  for (const p of pred) offsets[p + 1]++;
  for (let i = 1; i <= a.n; i++) offsets[i] += offsets[i - 1];
  const cursor = offsets.slice(), edges = new Int32Array(pred.length);
  for (let k = 0; k < pred.length; k++) edges[cursor[pred[k]]++] = k;
  return { offsets, edges, n: pred.length, pred: Int32Array.from(pred), succ: Int32Array.from(succ), type: Uint8Array.from(type), ids: Float64Array.from(ids) };
}
