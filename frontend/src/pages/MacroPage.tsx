import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { ActivityPatch } from "../api/types";
import { useAction, useStore } from "../state/store";
import { buildArrays, type ActArrays } from "../components/Gantt/model";
import { dayParts, dayToIso, isoToDay, monthKey, monthKeyLabel, monthRange, monthStartDay, todayDay } from "../utils/dates";

import { monthOffset, monthlyHours } from "../utils/macro";

const ROW_H = 26;
const COL_W = 64;
const LEFT_W = 330;

interface Pending { ws: number; we: number; hard: boolean; macro: string | null }

export default function MacroPage() {
  const { projectId, project, resources, fail, toast, bumpSchedule, scheduleVersion } = useStore();
  const run = useAction();
  const [error, setError] = useState(false);
  const dragCleanup = useRef<(() => void) | null>(null);
  useEffect(() => () => dragCleanup.current?.(), []);
  const [a, setA] = useState<ActArrays | null>(null);
  const [pending, setPending] = useState<Map<number, Pending>>(new Map());
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number>(-1);
  const [hard, setHard] = useState(false);
  const [q, setQ] = useState("");
  const [onlyUnassigned, setOnlyUnassigned] = useState(false);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(600);
  const scRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ idx: number; m0: number; m1: number } | null>(null);
  const [dragView, setDragView] = useState<{ idx: number; m0: number; m1: number } | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    setError(false); dragCleanup.current?.(); setDragView(null);
    api.columnar(projectId).then((c) => { if (active) { setA(buildArrays(c)); setSel(new Set()); setAnchor(-1); } }).catch(e => { if (active) { setError(true); fail(e); } });
    return () => { active = false; dragCleanup.current?.(); };

  }, [projectId, scheduleVersion, fail]);

  useEffect(() => {
    const el = scRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setViewH(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [a]);

  const months = useMemo(() => {
    if (!a || !project) return [] as { key: string; s: number; e: number; wd: number }[];
    const s0 = project.start_date ? isoToDay(project.start_date) : a.minDay;
    const e0 = project.end_date ? isoToDay(project.end_date) : Math.max(a.maxDay, s0 + 365);
    const start = Math.min(s0, a.minDay), end = Math.max(e0, a.maxDay);
    const out: { key: string; s: number; e: number; wd: number }[] = [];
    const p = dayParts(start);
    let k = 0;
    while (true) {
      const s = monthStartDay(p.y, p.m + k);
      if (s > end || k >= 240) break;
      const e = monthStartDay(p.y, p.m + k + 1) - 1;
      let wd = 0;
      for (let d = s; d <= e; d++) if (dayParts(d).wd < 5) wd++;
      out.push({ key: monthKey(s), s, e, wd });
      k++;
    }
    return out;
  }, [a, project]);

  const visible = useMemo(() => {
    if (!a) return [] as number[];
    const t = q.trim().toLowerCase();
    const out: number[] = [];
    for (let i = 0; i < a.n; i++) {
      if (t && !a.codes[i].toLowerCase().includes(t) && !a.names[i].toLowerCase().includes(t) && !a.location[i].toLowerCase().includes(t)) continue;
      if (onlyUnassigned) {
        const pd = pending.get(a!.ids[i]);
        const has = pd ? pd.ws === pd.ws : a.ws[i] === a.ws[i];
        if (has) continue;
      }
      out.push(i);
    }
    return out;
  }, [a, q, onlyUnassigned, pending]);

  const winOf = useCallback((i: number): { ws: number; we: number; hard: boolean } => {
    const pd = pending.get(a!.ids[i]);
    if (pd) return pd;
    return { ws: a ? a.ws[i] : NaN, we: a ? a.we[i] : NaN, hard: !!a?.windowHard[i] };
  }, [pending, a]);

  const monthIdx = (day: number) => monthOffset(day, months[0]?.s ?? NaN);
  const positions = useMemo(() => new Map(visible.map((i, pos) => [i, pos])), [visible]);
  useEffect(() => { if (scRef.current) scRef.current.scrollTop = 0; setScrollTop(0); }, [q, onlyUnassigned]);

  const setRange = (targets: number[], m0: number, m1: number) => {
    const lo = Math.min(m0, m1), hi = Math.max(m0, m1);
    const ws = months[lo].s, we = months[hi].e;
    setPending((p) => {
      const n = new Map(p);
      for (const i of targets) n.set(a!.ids[i], { ws, we, hard, macro: lo === hi ? months[lo].key : null });
      return n;
    });
  };

  const clear = (targets: number[]) => {
    setPending((p) => {
      const n = new Map(p);
      for (const i of targets) n.set(a!.ids[i], { ws: NaN, we: NaN, hard: false, macro: null });
      return n;
    });
  };

  const onCellDown = (i: number, m: number, e: React.MouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    dragCleanup.current?.();
    dragRef.current = { idx: i, m0: m, m1: m };
    setDragView({ idx: i, m0: m, m1: m });
    const up = () => {
      window.removeEventListener("mouseup", up);
      const d = dragRef.current;
      dragRef.current = null;
      setDragView(null);
      if (!d) return;
      const targets = sel.has(d.idx) && sel.size > 1 ? Array.from(sel) : [d.idx];
      setRange(targets, d.m0, d.m1);
    };
    dragCleanup.current = () => { window.removeEventListener("mouseup", up); dragRef.current = null; };
    window.addEventListener("mouseup", up);
  };
  const onCellEnter = (i: number, m: number) => {
    const d = dragRef.current;
    if (d && d.idx === i) { d.m1 = m; setDragView({ ...d }); }
  };

  const onRowClick = (i: number, e: React.MouseEvent) => {
    const pos = positions.get(i)!;
    if (e.shiftKey && positions.has(anchor)) {
      const pa = positions.get(anchor)!;
      const [lo, hi] = pa < pos ? [pa, pos] : [pos, pa];
      setSel(new Set(visible.slice(lo, hi + 1)));
    } else if (e.ctrlKey || e.metaKey) {
      const n = new Set(sel);
      if (n.has(i)) n.delete(i); else n.add(i);
      setSel(n); setAnchor(i);
    } else { setSel(new Set([i])); setAnchor(i); }
  };

  // HH por mes: con ventana (reparto uniforme en la ventana) vs capacidad
  const monthTotals = useMemo(() => monthlyHours(months, a?.n ?? 0, i => {
    const w = winOf(i);
    const start = Number.isFinite(w.ws) ? w.ws : a!.start[i];
    const finish = Number.isFinite(w.ws) ? w.we : (Number.isFinite(a!.finish[i]) ? a!.finish[i] : start);
    return { start, finish, hours: a!.hh[i] };
  }), [a, months, winOf]);

  const capPerDay = useMemo(() => resources.filter((r) => r.active && r.kind !== "material" && r.kind !== "equipo")
    .reduce((s, r) => s + r.capacity_hours_day * r.max_units, 0), [resources]);

  const save = async (): Promise<boolean> => {
    if (!projectId || !a) return false;
    if (pending.size === 0) return true;
    const list: ActivityPatch[] = [];
    for (const [id, p] of pending) {
      list.push({
        id,
        macro_month: p.macro,
        window_start: p.ws === p.ws ? dayToIso(p.ws) : null,
        window_end: p.we === p.we ? dayToIso(p.we) : null,
        window_hard: p.hard,
      });
    }
    const ok = await run(`Guardando ${list.length.toLocaleString("es-CL")} ventanas…`, async () => {
      for (let k = 0; k < list.length; k += 5000) {
        const batch = list.slice(k, k + 5000);
        await api.patchActivities(projectId, batch);
        setPending(current => { const next = new Map(current); for (const item of batch) if (current.get(item.id) === pending.get(item.id)) next.delete(item.id); return next; });
      }
      return true;
    });
    if (ok) toast(`${list.length.toLocaleString("es-CL")} actividades actualizadas`);
    bumpSchedule(); // Also refresh batches persisted before a later batch failed.
    return !!ok;
  };

  const schedule = async () => {
    if (!projectId) return;
    if (!(await save())) return;
    const r = await run("Programando dentro de las ventanas macro…", () => api.optimize(projectId,
      { level: 0.35, makespan: 0.05, window: 0.6, cost: 0 },
      { method: "auto", time_limit_s: 30, level_period: "week", group_by: "skill", respect_resource_limits: false, allow_overtime: true, assign_workers: true, cpsat_max_activities: 3000, seed: 0, group_outages: true },
      true));
    if (r) {
      if (!["optimo", "factible", "heuristico"].includes(r.status) || r.applied === false) { toast(`No se aplicó una programación (${r.status}).`, "error"); return; }
      toast(`Programación lista (${r.method_used}, ${r.elapsed_s.toFixed(1)} s). Fuera de ventana: ${r.after?.activities_out_of_window ?? 0}`);
      bumpSchedule();
    }
  };

  if (!a) return <div className="empty" role={error ? "alert" : "status"}>{error ? "No se pudo cargar la matriz. Vuelve a abrir esta vista para reintentar." : "Cargando…"}</div>;

  const r0 = Math.max(0, Math.floor(scrollTop / ROW_H) - 2);
  const r1 = Math.min(visible.length - 1, Math.ceil((scrollTop + viewH) / ROW_H) + 2);
  const gridW = LEFT_W + months.length * COL_W;
  const todayK = monthKey(todayDay());
  const cells: JSX.Element[] = [];
  for (let r = r0; r <= r1; r++) {
    const i = visible[r];
    const w = winOf(i);
    const m0 = monthIdx(w.ws), m1 = monthIdx(w.we);
    const ps = monthIdx(a.start[i]), pf = monthIdx(a.finish[i] === a.finish[i] ? a.finish[i] : a.start[i]);
    const isSel = sel.has(i);
    const dv = dragView && dragView.idx === i ? dragView : null;
    cells.push(
      <div key={i} style={{ position: "absolute", top: r * ROW_H + 30, left: 0, height: ROW_H, width: gridW, display: "flex", borderBottom: "1px solid var(--grid)", background: isSel ? "var(--accent-soft)" : pending.has(a.ids[i]) ? "var(--surface-2)" : "var(--surface)" }}>
        <div onClick={(e) => onRowClick(i, e)} style={{ position: "sticky", left: 0, width: LEFT_W, flex: "none", display: "flex", alignItems: "center", gap: 8, padding: "0 8px", background: "inherit", borderRight: "1px solid var(--border)", cursor: "pointer", zIndex: 1, fontSize: 12 }}>
          <span className="mono" style={{ width: 90, flex: "none", overflow: "hidden", textOverflow: "ellipsis" }}>{a.codes[i]}</span>
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={a.names[i]}>{a.names[i]}</span>
          <span className="muted" style={{ width: 44, textAlign: "right" }}>{a.hh[i] === a.hh[i] ? a.hh[i].toFixed(0) : ""}</span>
          {pending.has(a.ids[i]) && <span title="Cambio sin guardar" style={{ color: "var(--datadate)" }}>●</span>}
        </div>
        {months.map((m, k) => {
          const inWin = Number.isFinite(m0) && k >= m0 && k <= m1;
          const inDrag = dv && k >= Math.min(dv.m0, dv.m1) && k <= Math.max(dv.m0, dv.m1);
          const planned = Number.isFinite(ps) && k >= ps && k <= pf;
          return (
            <div key={m.key} className="macro-cell" role="button" tabIndex={0} aria-label={`${a.codes[i]}: asignar ${monthKeyLabel(m.key)}`} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setRange(sel.has(i) ? Array.from(sel) : [i], k, k); } }} onMouseDown={(e) => onCellDown(i, k, e)} onMouseEnter={() => onCellEnter(i, k)}
              onDoubleClick={() => clear(sel.has(i) ? Array.from(sel) : [i])}
              style={{ width: COL_W, flex: "none", borderRight: "1px solid var(--grid)", position: "relative", background: m.key === todayK ? "rgba(27,175,122,.06)" : undefined }}>
              {(inWin || inDrag) && <div style={{ position: "absolute", inset: "4px 0", background: inDrag ? "var(--accent-2)" : w.hard ? "var(--bar-window-hard)" : "var(--bar-window)", opacity: inDrag ? .45 : 1, borderRadius: k === (inDrag ? Math.min(dv!.m0, dv!.m1) : m0) ? "4px 0 0 4px" : k === (inDrag ? Math.max(dv!.m0, dv!.m1) : m1) ? "0 4px 4px 0" : 0 }} />}
              {planned && <div style={{ position: "absolute", left: 6, right: 6, top: 11, height: 4, borderRadius: 2, background: a.crit[i] ? "var(--bar-crit)" : "var(--bar)" }} />}
            </div>
          );
        })}
      </div>,
    );
  }

  return (
    <>
      {!a.hasHours && <div role="status" className="muted small">HH no disponibles en la respuesta columnar; los totales no se pueden calcular.</div>}
      {error && <div role="alert">No se pudo actualizar la matriz. Vuelve a abrir esta vista para reintentar.</div>}
      <div className="toolbar">
        <input className="inp" placeholder="Buscar…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 200 }} />
        <label className="chk"><input type="checkbox" checked={onlyUnassigned} onChange={(e) => setOnlyUnassigned(e.target.checked)} />Solo sin mes asignado</label>
        <span className="sep" />
        <label className="chk" title="Una ventana dura no puede violarse; una blanda solo se penaliza"><input type="checkbox" checked={hard} onChange={(e) => setHard(e.target.checked)} />Nuevas ventanas duras</label>
        <button className="btn small" disabled={sel.size === 0} onClick={() => clear(Array.from(sel))}>Quitar mes de selección</button>
        <span className="muted small">{sel.size ? `${sel.size} seleccionadas` : "Clic en la celda de un mes para asignarlo; arrastra para un rango; Shift/Ctrl para selección múltiple; doble clic borra"}</span>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn" disabled={pending.size === 0} onClick={() => setPending(new Map())}>Descartar</button>
        <button className="btn" disabled={pending.size === 0} onClick={save}>Guardar ({pending.size})</button>
        <button className="btn primary" onClick={schedule}>▶ Programar dentro de los meses</button>
      </div>
      <div ref={scRef} style={{ flex: 1, overflow: "auto", position: "relative", background: "var(--surface)" }}
        onScroll={(e) => { const top = Math.floor(e.currentTarget.scrollTop / ROW_H) * ROW_H; setScrollTop(old => old === top ? old : top); }}>
        <div style={{ height: visible.length * ROW_H + 30 + 40, width: gridW, position: "relative" }}>
          <div style={{ position: "sticky", top: 0, zIndex: 3, display: "flex", height: 30, background: "var(--surface-2)", borderBottom: "1px solid var(--border)", width: gridW }}>
            <div style={{ position: "sticky", left: 0, width: LEFT_W, flex: "none", display: "flex", alignItems: "center", padding: "0 8px", gap: 8, fontWeight: 600, fontSize: 12, background: "var(--surface-2)", borderRight: "1px solid var(--border)", zIndex: 4 }}>
              <span style={{ width: 90 }}>Código</span><span style={{ flex: 1 }}>Actividad</span><span style={{ width: 44, textAlign: "right" }}>HH</span><span style={{ width: 10 }} />
            </div>
            {months.map((m) => <div key={m.key} style={{ width: COL_W, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11.5, fontWeight: 600, color: "var(--text-2)", borderRight: "1px solid var(--grid)" }}>{monthKeyLabel(m.key)}</div>)}
          </div>
          {cells}
          <div style={{ position: "sticky", bottom: 0, top: "auto", zIndex: 3, display: "flex", height: 40, background: "var(--surface-2)", borderTop: "1px solid var(--border)", width: gridW, marginTop: visible.length * ROW_H }}>
            <div style={{ position: "sticky", left: 0, width: LEFT_W, flex: "none", padding: "4px 8px", fontSize: 11.5, background: "var(--surface-2)", borderRight: "1px solid var(--border)", zIndex: 4 }}>
              <b>HH estimadas por mes</b> (reparto por días corridos)<br />
              <span className="muted">Capacidad estimada L–V, sin feriados ni turnos: {capPerDay.toFixed(0)} HH/día</span>
            </div>
            {months.map((m, k) => {
              const cap = capPerDay * m.wd;
              const ratio = cap > 0 ? monthTotals[k] / cap : 0;
              const color = !a.hasHours || cap <= 0 ? "var(--text-2)" : ratio > 1 ? "var(--critical)" : ratio > 0.85 ? "#b07800" : "var(--good)";
              return (
                <div key={m.key} title={!a.hasHours ? "HH no disponibles" : `${monthKeyLabel(m.key)}: ${monthTotals[k].toFixed(0)} HH${cap > 0 ? ` de ${cap.toFixed(0)} disponibles (${(ratio * 100).toFixed(0)} %)` : ""}`}
                  style={{ width: COL_W, flex: "none", fontSize: 11, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", borderRight: "1px solid var(--grid)", color, fontWeight: 600 }}>
                  {!a.hasHours ? "—" : monthTotals[k] >= 1000 ? `${(monthTotals[k] / 1000).toFixed(1)}k` : monthTotals[k].toFixed(0)}
                  {a.hasHours && cap > 0 && <span style={{ fontWeight: 400 }}>{(ratio * 100).toFixed(0)}%</span>}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}

export { monthRange };
