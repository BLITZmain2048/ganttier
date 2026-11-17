import WbsManager from "../components/WbsManager";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { Activity, Assignment, ConstraintType, Relation, RelType, WBSNode, CalendarDay } from "../api/types";
import { useAction, useStore } from "../state/store";
import GanttView, { type ColumnDef } from "../components/Gantt/GanttView";
import ResourceProfile from "../components/Gantt/ResourceProfile";
import { createViewportChannel } from "../components/Gantt/viewport";
import { buildArrays, buildRelations, buildRows, type ActArrays } from "../components/Gantt/model";
import { dayToIso, fmtDay, isoToDay, todayDay, todayIso } from "../utils/dates";
import { summarizeWarnings } from "../utils/warnings";
import { scheduledDates } from "../utils/activityDates";
import { Field, Modal, NumInput } from "../components/ui";

const SCALES: { key: string; label: string; w: number }[] = [
  { key: "dia", label: "Día", w: 26 },
  { key: "semana", label: "Semana", w: 7 },
  { key: "mes", label: "Mes", w: 2.4 },
  { key: "trimestre", label: "Trimestre", w: 1.0 },
  { key: "anio", label: "Año", w: 0.35 },
];

const CONSTRAINTS: { v: ConstraintType; l: string }[] = [
  { v: "ASAP", l: "Lo antes posible" }, { v: "ALAP", l: "Lo más tarde posible" },
  { v: "SNET", l: "Comenzar no antes de" }, { v: "SNLT", l: "Comenzar no después de" },
  { v: "FNET", l: "Terminar no antes de" }, { v: "FNLT", l: "Terminar no después de" },
  { v: "MSO", l: "Comenzar el" }, { v: "MFO", l: "Terminar el" },
];

function num(a: Float64Array, i: number, dec = 0): string {
  const v = a[i];
  return v === v ? v.toLocaleString("es-CL", { maximumFractionDigits: dec }) : "";
}

export default function GanttPage() {
  const { projectId, project, toast, fail, bumpSchedule, scheduleVersion, go, resources, reloadProject } = useStore();
  const run = useAction();
  const [arrays, setArrays] = useState<ActArrays | null>(null);
  const [relList, setRelList] = useState<Relation[]>([]);
  const [wbs, setWbs] = useState<WBSNode[]>([]);
  const [days, setDays] = useState<Map<number, 0 | 1 | 2>>(new Map());
  const [dayWidth, setDayWidth] = useState(7);
  const [tableWidth, setTableWidth] = useState(560);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState(-1);
  const [q, setQ] = useState("");
  const [onlyCrit, setOnlyCrit] = useState(false);
  const [groupWbs, setGroupWbs] = useState(true);
  const [showBase, setShowBase] = useState(true);
  const [showWin, setShowWin] = useState(true);
  const [showRel, setShowRel] = useState(true);
  const [showProfile, setShowProfile] = useState(false);
  const viewport = useMemo(createViewportChannel, []);
  const [autoCpm, setAutoCpm] = useState(true);
  const [sort, setSort] = useState<"none" | "codigo" | "inicio">("none");
  const [jump, setJump] = useState<{ day: number; nonce: number } | null>(null);
  const [detail, setDetail] = useState<Activity | null>(null);
  const [newAct, setNewAct] = useState(false);
  const [wbsOpen, setWbsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const loadRequest = useRef(0);
  const [loadError, setLoadError] = useState(false);
  const isProj = project?.mode !== "mantenimiento";

  const load = useCallback(async () => {
    if (!projectId) return;
    const request = ++loadRequest.current;
    setLoading(true); setLoadError(false);
    try {
      const [c, r, w] = await Promise.all([api.columnar(projectId), api.relations(projectId), api.wbs(projectId)]);
      if (request !== loadRequest.current) return;
      const arr = buildArrays(c);
      setSelected(-1);
      setArrays(arr);
      setRelList(r);
      setWbs(w);
    } catch (e) { if (request === loadRequest.current) { setLoadError(true); fail(e); } } finally { if (request === loadRequest.current) setLoading(false); }
  }, [projectId, fail]);

  useEffect(() => { void load(); return () => { loadRequest.current++; }; }, [load, scheduleVersion]);

  // calendario del proyecto para sombreado
  useEffect(() => {
    if (!project || !arrays) return;
    let active = true;
    const calId = project.calendar_id;
    if (!calId) { setDays(new Map()); return; }
    const s = dayToIso(arrays.minDay - 60), e = dayToIso(Math.min(arrays.maxDay + 60, arrays.minDay + 3700));
    api.calendarDays(calId, s, e).then((list: CalendarDay[]) => {
      const m = new Map<number, 0 | 1 | 2>();
      for (const d of list) m.set(isoToDay(d.date), d.working ? 0 : d.reason && !/fin de semana|descanso/i.test(d.reason) ? 2 : 1);
      if (active) setDays(m);
    }).catch(e => { if (active) { setDays(new Map()); fail(e); } });
    return () => { active = false; };
  }, [project, arrays, fail]);

  const isNonWorking = useCallback((d: number): 0 | 1 | 2 => {
    if (days.has(d)) return days.get(d)!;
    if (project?.calendar_id) return 0;
    const wd = ((d % 7) + 7 + 3) % 7; // 1970-01-01 fue jueves -> 0 = lunes
    return wd >= 5 ? 1 : 0;
  }, [days, project?.calendar_id]);

  const filter = useMemo(() => {
    if (!arrays) return null;
    const t = q.trim().toLowerCase();
    if (!t && !onlyCrit) return null;
    const f = new Uint8Array(arrays.n);
    for (let i = 0; i < arrays.n; i++) {
      let ok = true;
      if (onlyCrit && !arrays.crit[i]) ok = false;
      if (ok && t) ok = arrays.codes[i].toLowerCase().includes(t) || arrays.names[i].toLowerCase().includes(t)
        || arrays.location[i].toLowerCase().includes(t) || arrays.skill[i].toLowerCase().includes(t);
      f[i] = ok ? 1 : 0;
    }
    return f;
  }, [arrays, q, onlyCrit]);

  const rows = useMemo(() => arrays ? buildRows(arrays, wbs, { groupByWbs: groupWbs, collapsed, filter, sort }) : null,
    [arrays, wbs, groupWbs, collapsed, filter, sort]);
  const rels = useMemo(() => arrays ? buildRelations(relList, arrays) : null, [arrays, relList]);

  const columns = useMemo<ColumnDef[]>(() => {
    const cols: ColumnDef[] = [
      { key: "code", label: "Código", width: 92, get: (a, i) => a.codes[i] },
      { key: "name", label: "Nombre", width: 220, get: (a, i) => a.names[i], edit: "text" },
      { key: "dur", label: "Dur.", width: 46, align: "right", get: (a, i) => num(a.dur, i), edit: "number" },
      { key: "start", label: "Inicio", width: 84, get: (a, i) => fmtDay(a.start[i]), edit: "date", editValue: (a, i) => (a.start[i] === a.start[i] ? dayToIso(a.start[i]) : "") },
      { key: "finish", label: "Término", width: 84, get: (a, i) => fmtDay(a.finish[i]) },
      { key: "pct", label: "% Av.", width: 50, align: "right", get: (a, i) => a.pct[i].toFixed(0), edit: "number" },
      { key: "hh", label: "HH", width: 58, align: "right", get: (a, i) => num(a.hh, i, 1), edit: "number" },
      { key: "macro", label: "Mes macro", width: 76, get: (a, i) => a.macro[i] ?? "", edit: "month" },
    ];
    if (isProj) cols.push({ key: "tf", label: "Holgura", width: 60, align: "right", get: (a, i) => num(a.tf, i) });
    else {
      cols.push({ key: "skill", label: "Habilidad", width: 90, get: (a, i) => a.skill[i] });
      cols.push({ key: "loc", label: "Instalación", width: 120, get: (a, i) => a.location[i] });
    }
    return cols;
  }, [isProj]);

  const recalc = useCallback(async () => {
    if (!projectId) return;
    const r = await run("Calculando ruta crítica…", () => api.cpm(projectId));
    if (r) {
      if (r.warnings?.length) toast(summarizeWarnings(r.warnings).slice(0, 2).join(" · "), "info");
    }
    bumpSchedule();
  }, [projectId, run, toast, bumpSchedule]);

  const patch = useCallback(async (list: Partial<Activity>[] & { id: number }[], recalcAfter: boolean) => {
    if (!projectId) return;
    try {
      await api.patchActivities(projectId, list as (Partial<Activity> & { id: number })[]);
      if (recalcAfter && isProj && autoCpm) await recalc();
      else bumpSchedule();
    } catch (e) { fail(e); }
  }, [projectId, isProj, autoCpm, recalc, bumpSchedule, fail]);

  const onEditCell = (i: number, key: string, value: string) => {
    if (!arrays) return;
    const id = arrays.ids[i];
    const v = value.trim();
    if (["dur", "hh", "pct"].includes(key) && !v) return;
    switch (key) {
      case "name": if (v && v !== arrays.names[i]) void patch([{ id, name: v }], false); break;
      case "dur": {
        const n = Math.max(0, Math.round(Number(v.replace(",", "."))));
        if (Number.isFinite(n) && n !== arrays.dur[i]) void patch([{ id, duration_days: n }], true);
        break;
      }
      case "hh": {
        const n = Number(v.replace(",", "."));
        if (Number.isFinite(n) && n >= 0) void patch([{ id, work_hours: n }], false);
        break;
      }
      case "pct": {
        // El avance se registra como entrada histórica a la fecha de corte (misma fuente que Control).
        const n = Math.max(0, Math.min(100, Number(v.replace(",", "."))));
        if (!Number.isFinite(n) || !projectId) break;
        const date = project?.data_date ?? todayIso();
        const startIso = arrays.start[i] === arrays.start[i] ? dayToIso(Math.min(arrays.start[i], isoToDay(date))) : date;
        void api.addProgress(projectId, [{ activity_id: id, date, percent_complete: n, actual_hours: 0, actual_cost: 0, resource_id: null,
          actual_start: n > 0 ? startIso : null, actual_finish: n >= 100 ? date : null, note: "Editado en Gantt" }])
          .then(() => { toast(`Avance ${n} % registrado a la fecha de corte ${date.split("-").reverse().join("-")}`, "info"); if (isProj && autoCpm) void recalc(); else bumpSchedule(); })
          .catch(fail);
        break;
      }
      case "macro": void patch([v ? { id, macro_month: v } : { id, macro_month: null, window_start: null, window_end: null }], false); break;
      case "start":
        if (!v) return;
        if (isProj) void patch([{ id, constraint_type: "SNET", constraint_date: v }], true);
        else void moveMaintenance(i, v);
        break;
    }
  };

  const moveMaintenance = async (i: number, requested: string) => {
    if (!arrays || !projectId) return;
    try {
      const activity = await api.activity(projectId, arrays.ids[i], arrays.codes[i]);
      const calId = activity.calendar_id ?? project?.calendar_id;
      if (activity.locked) throw new Error("La actividad está bloqueada.");
      if (!project?.end_date || requested > project.end_date) throw new Error("El inicio queda fuera del horizonte del proyecto.");
      if (calId == null && activity.duration_days > 0) throw new Error("Asigna un calendario explícito para mover la actividad.");
      const days = activity.duration_days === 0 ? [] : await api.calendarDays(calId!, requested, project.end_date);
      await patch([{ id: activity.id, ...scheduledDates(days, requested, activity.duration_days) }], false);
    } catch (e) { fail(e); }
  };

  const onMove = (i: number, dd: number) => {
    if (!arrays) return;
    const ns = arrays.start[i] + dd;
    if (!Number.isFinite(ns)) return;
    if (isProj) void patch([{ id: arrays.ids[i], constraint_type: "SNET", constraint_date: dayToIso(ns) }], true);
    else void moveMaintenance(i, dayToIso(ns));
  };

  const onResize = async (i: number, newFinish: number) => {
    if (!arrays || !projectId || !Number.isFinite(newFinish)) return;
    try {
      const activity = await api.activity(projectId, arrays.ids[i], arrays.codes[i]);
      if (activity.locked) throw new Error("La actividad está bloqueada.");
      const calId = activity.calendar_id ?? project?.calendar_id;
      if (calId == null) throw new Error("Asigna un calendario al proyecto o a la actividad antes de cambiar su duración.");
      const start = arrays.start[i];
      const days = await api.calendarDays(calId, dayToIso(start), dayToIso(newFinish));
      const working = days.filter(d => d.working && isoToDay(d.date) >= start && isoToDay(d.date) <= newFinish);
      if (!working.length) throw new Error("El intervalo no contiene días laborables.");
      const finish = working.reduce((last, d) => d.date > last ? d.date : last, working[0].date);
      await patch([{ id: activity.id, duration_days: working.length, ...(isProj ? {} : { planned_finish: finish }) }], true);
    } catch (e) { fail(e); }
  };

  const onLink = async (pi: number, si: number) => {
    if (!arrays || !projectId) return;
    try {
      await api.createRelation(projectId, { pred_id: arrays.ids[pi], succ_id: arrays.ids[si], type: "FS", lag_days: 0 });
      toast(`Relación FS creada: ${arrays.codes[pi]} → ${arrays.codes[si]}`);
      if (autoCpm && isProj) await recalc(); else bumpSchedule();
    } catch (e) { fail(e); }
  };

  const openDetail = async () => {
    if (!arrays || selected < 0) return;
    try { setDetail(await api.activity(projectId!, arrays.ids[selected], arrays.codes[selected])); } catch (e) { fail(e); }
  };

  const baseline = async () => {
    if (!projectId) return;
    const name = `Línea base ${new Date().toLocaleDateString("es-CL")}`;
    const b = await run("Guardando línea base…", () => api.createBaseline(projectId, name));
    if (b) {
      const activated = await run("Activando línea base…", () => api.activateBaseline(b.id));
      if (!activated) return;
      toast(`${name} creada y activada`);
      await reloadProject();
      bumpSchedule();
    }
  };

  const delSelected = async () => {
    if (!arrays || selected < 0) return;
    if (!window.confirm(`¿Eliminar la actividad ${arrays.codes[selected]}?`)) return;
    try { await api.deleteActivity(arrays.ids[selected]); setSelected(-1); toast("Actividad eliminada"); bumpSchedule(); } catch (e) { fail(e); }
  };

  const dataDay = project?.data_date ? isoToDay(project.data_date) : NaN;
  const scaleKey = SCALES.reduce((best, s) => (Math.abs(Math.log(s.w / dayWidth)) < Math.abs(Math.log(best.w / dayWidth)) ? s : best), SCALES[0]).key;

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select, button, [contenteditable], [role=dialog]")) return;
      if (e.key === "Enter" && selected >= 0) void openDetail();
      if (e.key === "Delete" && selected >= 0) void delSelected();
      if (e.key === "F9") { e.preventDefault(); void recalc(); }
      if (!rows || !arrays) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const r = selected >= 0 ? rows.rowOfAct[selected] : -1;
        let k = r + (e.key === "ArrowDown" ? 1 : -1);
        while (k >= 0 && k < rows.rows.length && rows.rows[k] < 0) k += e.key === "ArrowDown" ? 1 : -1;
        if (k >= 0 && k < rows.rows.length) setSelected(rows.rows[k]);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  return (
    <>
      {loadError && <div role="alert">No se pudo actualizar el Gantt. <button className="btn" onClick={load}>Reintentar</button></div>}
      {loading && arrays && <div role="status">Actualizando actividades…</div>}
      <div className="toolbar">
        {isProj && <button className="btn primary" onClick={recalc} title="Recalcular CPM (F9)">▶ Programar (CPM)</button>}
        <button className="btn" onClick={() => go("optimizar")}>⚙ Optimizar</button>
        <button className="btn" onClick={baseline}>⧉ Línea base</button>
        <span className="sep" />
        <button className="btn" onClick={() => setNewAct(true)}>＋ Actividad</button>
        <button className="btn" onClick={() => setWbsOpen(true)} title="Crear y organizar la estructura de desglose del trabajo">WBS</button>
        <button className="btn" disabled={selected < 0} onClick={openDetail}>✎ Detalle</button>
        <button className="btn" disabled={selected < 0} onClick={delSelected}>🗑</button>
        <span className="sep" />
        <input className="inp" placeholder="Buscar código, nombre, instalación…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 220 }} />
        {isProj && <label className="chk"><input type="checkbox" checked={onlyCrit} onChange={(e) => setOnlyCrit(e.target.checked)} />Solo críticas</label>}
        <span className="sep" />
        <div className="btn-group">
          {SCALES.map((s) => <button key={s.key} className={`btn small${scaleKey === s.key ? " on" : ""}`} onClick={() => setDayWidth(s.w)}>{s.label}</button>)}
        </div>
        <button className="btn small" onClick={() => setJump({ day: todayDay(), nonce: Date.now() })}>Hoy</button>
        {selected >= 0 && arrays && <button className="btn small" onClick={() => setJump({ day: arrays.start[selected], nonce: Date.now() })}>Ir a selección</button>}
        <span className="sep" />
        <details style={{ position: "relative" }}>
          <summary className="btn small" style={{ listStyle: "none" }}>Vista ▾</summary>
          <div className="card" style={{ position: "absolute", zIndex: 20, padding: 10, display: "flex", flexDirection: "column", gap: 6, minWidth: 220, right: 0 }}>
            <label className="chk"><input type="checkbox" checked={groupWbs} onChange={(e) => setGroupWbs(e.target.checked)} />Agrupar por WBS</label>
            <label className="chk"><input type="checkbox" checked={showBase} onChange={(e) => setShowBase(e.target.checked)} />Mostrar línea base</label>
            <label className="chk"><input type="checkbox" checked={showWin} onChange={(e) => setShowWin(e.target.checked)} />Mostrar ventanas macro</label>
            <label className="chk"><input type="checkbox" checked={showRel} onChange={(e) => setShowRel(e.target.checked)} />Mostrar relaciones</label>
            {isProj && <label className="chk"><input type="checkbox" checked={autoCpm} onChange={(e) => setAutoCpm(e.target.checked)} />Recalcular CPM al editar</label>}
            <label className="fld">Orden
              <select className="inp" value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
                <option value="none">Original</option><option value="codigo">Código</option><option value="inicio">Fecha de inicio</option>
              </select>
            </label>
            {groupWbs && wbs.length > 0 && <div className="row">
              <button className="btn small" onClick={() => setCollapsed(new Set(wbs.map((w) => w.id)))}>Contraer todo</button>
              <button className="btn small" onClick={() => setCollapsed(new Set())}>Expandir todo</button>
            </div>}
          </div>
        </details>
        <button className={`btn small${showProfile ? " primary" : ""}`} aria-pressed={showProfile}
          aria-controls="gantt-resource-profile" onClick={() => setShowProfile(value => !value)}>Perfil de recursos</button>
      </div>
      {arrays && rows && rels ? (
        arrays.n === 0 ? (
          <div className="empty">
            Este proyecto no tiene actividades. Agrégalas con <b>＋ Actividad</b>, importa una plantilla Excel
            {project?.mode === "mantenimiento" ? <> o genera ocurrencias desde el <a href={`#/p/${projectId}/mantenimiento`}>plan de mantenimiento</a></> : null}.
          </div>
        ) : (
          <div className="gantt-workspace">
          <GanttView readOnly={loading || loadError} a={arrays} rows={rows} rels={rels} columns={columns} dayWidth={dayWidth} onDayWidth={setDayWidth}
            dataDay={dataDay} isNonWorking={isNonWorking} selected={selected} onSelect={setSelected}
            onToggleWbs={(id) => setCollapsed((c) => { const n = new Set(c); if (n.has(id)) n.delete(id); else n.add(id); return n; })}
            collapsed={collapsed} onEditCell={onEditCell} onMove={onMove} onResize={onResize} onLink={isProj ? onLink : undefined}
            jump={jump} tableWidth={tableWidth} onTableWidth={setTableWidth}
            showBaseline={showBase} showWindows={showWin} showRelations={showRel} onViewport={viewport.publish} />
          {projectId != null && <ResourceProfile key={projectId} visible={showProfile} projectId={projectId}
            scheduleVersion={scheduleVersion} period={dayWidth >= 4 ? "day" : "week"}
            tableWidth={tableWidth} viewport={viewport} />}
          </div>
        )
      ) : <div className="empty">{loading ? "Cargando actividades…" : ""}</div>}
      <div className="statusbar">
        <span>{arrays ? `${arrays.n.toLocaleString("es-CL")} actividades` : ""}</span>
        {rows && filter && <span>{rows.rows.length.toLocaleString("es-CL")} visibles</span>}
        <span>{relList.length.toLocaleString("es-CL")} relaciones</span>
        {arrays && <span>{fmtDay(arrays.minDay)} → {fmtDay(arrays.maxDay)}</span>}
        <span className="muted">Doble clic para editar · arrastra barras para mover · extremo derecho para cambiar duración · círculo para vincular · Ctrl+rueda para zoom · Enter: detalle · F9: CPM</span>
      </div>
      {detail && <ActivityDetail act={detail} onClose={() => { setDetail(null); bumpSchedule(); }} onSaved={async () => { setDetail(null); if (isProj && autoCpm) await recalc(); else bumpSchedule(); }}
        wbs={wbs} arrays={arrays} relList={relList} />}
      {wbsOpen && projectId && <WbsManager pid={projectId} wbs={wbs} onClose={() => setWbsOpen(false)} onChanged={() => bumpSchedule()} />}
      {newAct && projectId && <NewActivity pid={projectId} wbs={wbs} onClose={() => setNewAct(false)}
        onCreated={async () => { setNewAct(false); if (isProj && autoCpm) await recalc(); else bumpSchedule(); }} skills={Array.from(new Set(resources.flatMap((r) => r.skills)))} />}
    </>
  );
}

function NewActivity({ pid, wbs, onClose, onCreated, skills }: { pid: number; wbs: WBSNode[]; onClose: () => void; onCreated: () => void; skills: string[] }) {
  const { fail } = useStore();
  const [f, setF] = useState<Partial<Activity>>({ code: "", name: "", duration_days: 5, work_hours: 0, wbs_id: null, constraint_type: "ASAP", priority: 50 });
  const save = async () => {
    if (!f.code || !f.name) { fail(new Error("Código y nombre son obligatorios")); return; }
    try { await api.createActivities(pid, f); onCreated(); } catch (e) { fail(e); }
  };
  return (
    <Modal title="Nueva actividad" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancelar</button><button className="btn primary" onClick={save}>Crear</button></>}>
      <div className="form-grid">
        <Field label="Código"><input className="inp" autoFocus value={f.code ?? ""} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Nombre" style={{ gridColumn: "span 2" }}><input className="inp" value={f.name ?? ""} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="WBS">
          <select className="inp" value={f.wbs_id ?? ""} onChange={(e) => setF({ ...f, wbs_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">(sin WBS)</option>
            {wbs.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
          </select>
        </Field>
        <Field label="Duración (días laborables)"><NumInput value={f.duration_days} onChange={(v) => setF({ ...f, duration_days: Math.round(v) })} min={0} /></Field>
        <Field label="HH presupuestadas"><NumInput value={f.work_hours} onChange={(v) => setF({ ...f, work_hours: v })} min={0} /></Field>
        <Field label="Mes macro"><input className="inp" type="month" value={f.macro_month ?? ""} onChange={(e) => setF({ ...f, macro_month: e.target.value || null })} /></Field>
        <Field label="Habilidad requerida">
          <input className="inp" list="skills-dl" value={f.required_skill ?? ""} onChange={(e) => setF({ ...f, required_skill: e.target.value })} />
          <datalist id="skills-dl">{skills.map((s) => <option key={s} value={s} />)}</datalist>
        </Field>
      </div>
    </Modal>
  );
}

function ActivityDetail({ act, onClose, onSaved, wbs, arrays, relList }: {
  act: Activity; onClose: () => void; onSaved: () => void; wbs: WBSNode[]; arrays: ActArrays | null; relList: Relation[];
}) {
  const { fail, toast, resources, calendars, projectId } = useStore();
  const [f, setF] = useState<Activity>(act);
  const [asg, setAsg] = useState<Assignment[]>([]);
  const dirtyAssignments = useRef(new Set<number>());
  const [saving, setSaving] = useState(false);
  const [newRel, setNewRel] = useState<{ code: string; type: RelType; lag: number; dir: "pred" | "succ" }>({ code: "", type: "FS", lag: 0, dir: "pred" });
  const [rels, setRels] = useState<Relation[]>(relList.filter((r) => r.pred_id === act.id || r.succ_id === act.id));
  const skills = Array.from(new Set(resources.flatMap((r) => r.skills)));

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    api.assignments(projectId, act.id).then(l => { if (active) setAsg(l.filter(x => x.activity_id === act.id)); }).catch(e => { if (active) fail(e); });
    return () => { active = false; };
  }, [projectId, act.id, fail]);

  const code = (id: number) => { const i = arrays?.idToIndex.get(id); return i !== undefined && arrays ? `${arrays.codes[i]} · ${arrays.names[i]}` : `#${id}`; };

  const save = async () => {
    if (saving) return;
    if (!f.code.trim() || !f.name.trim() || !Number.isInteger(f.duration_days) || f.duration_days < 0 || f.work_hours < 0) { fail(new Error("Revisa código, nombre, duración y HH.")); return; }
    setSaving(true);
    try {
      for (const assignment of asg) if (dirtyAssignments.current.has(assignment.id)) {
        if (!Number.isFinite(assignment.units) || assignment.units < 0 || !Number.isFinite(assignment.work_hours) || assignment.work_hours < 0) throw new Error("Las unidades y HH deben ser no negativas.");
        await api.updateAssignment(assignment.id, assignment);
        dirtyAssignments.current.delete(assignment.id);
      }
      // Coherencia mes macro / ventana: si cambió el mes, el backend deriva la ventana;
      // si cambiaron solo las fechas de la ventana, se limpia el mes macro para no sobrescribirlas.
      const payload: Partial<Activity> = { ...f };
      const macroChanged = (f.macro_month ?? null) !== (act.macro_month ?? null);
      const windowChanged = (f.window_start ?? null) !== (act.window_start ?? null) || (f.window_end ?? null) !== (act.window_end ?? null);
      if (macroChanged && f.macro_month) { delete payload.window_start; delete payload.window_end; }
      else if (windowChanged) payload.macro_month = null;
      await api.updateActivity(act.id, payload); toast("Actividad guardada"); onSaved();
    } catch (e) { fail(e); } finally { setSaving(false); }
  };

  const addRel = async () => {
    if (!projectId || !arrays) return;
    const i = arrays.codes.findIndex((c) => c === newRel.code.trim());
    if (i < 0) { fail(new Error(`No existe la actividad con código ${newRel.code}`)); return; }
    const other = arrays.ids[i];
    if (other === act.id) { fail(new Error("Una actividad no puede depender de sí misma.")); return; }
    try {
      const r = await api.createRelation(projectId, newRel.dir === "pred"
        ? { pred_id: other, succ_id: act.id, type: newRel.type, lag_days: newRel.lag }
        : { pred_id: act.id, succ_id: other, type: newRel.type, lag_days: newRel.lag });
      setRels(current => [...current, r]);
      setNewRel({ ...newRel, code: "" });
    } catch (e) { fail(e); }
  };

  const delRel = async (r: Relation) => {
    try { await api.deleteRelation(r.id); setRels(current => current.filter(x => x.id !== r.id)); } catch (e) { fail(e); }
  };

  const addAsg = async () => {
    if (!projectId) return;
    try {
      const a = await api.createAssignment(projectId, { activity_id: act.id, resource_id: null, skill: skills[0] ?? "", units: 1, work_hours: 0 });
      setAsg(current => [...current, a]);
    } catch (e) { fail(e); }
  };
  const updAsg = (a: Assignment) => {
    dirtyAssignments.current.add(a.id);
    setAsg(current => current.map(x => x.id === a.id ? a : x));
  };
  const delAsg = async (a: Assignment) => {
    try { await api.deleteAssignment(a.id); setAsg(current => current.filter(x => x.id !== a.id)); dirtyAssignments.current.delete(a.id); } catch (e) { fail(e); }
  };

  const set = <K extends keyof Activity>(k: K, v: Activity[K]) => setF({ ...f, [k]: v });

  return (
    <Modal title={`${act.code} · ${act.name}`} onClose={onClose} width={920}
      footer={<><button className="btn" onClick={onClose}>Cerrar</button><button className="btn primary" disabled={saving} onClick={save}>{saving ? "Guardando…" : "Guardar"}</button></>}>
      <div className="stack">
        <div className="form-grid">
          <Field label="Código"><input className="inp" value={f.code} onChange={(e) => set("code", e.target.value)} /></Field>
          <Field label="Nombre" style={{ gridColumn: "span 2" }}><input className="inp" value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="WBS">
            <select className="inp" value={f.wbs_id ?? ""} onChange={(e) => set("wbs_id", e.target.value ? Number(e.target.value) : null)}>
              <option value="">(sin WBS)</option>
              {wbs.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
            </select>
          </Field>
          <Field label="Duración (días lab.)"><NumInput value={f.duration_days} onChange={(v) => set("duration_days", Math.round(v))} min={0} /></Field>
          <Field label="HH presupuestadas"><NumInput value={f.work_hours} onChange={(v) => set("work_hours", v)} min={0} /></Field>
          <Field label="Costo presupuestado"><NumInput value={f.cost_budget} onChange={(v) => set("cost_budget", v)} min={0} /></Field>
          <Field label="Prioridad (0-100)"><NumInput value={f.priority} onChange={(v) => set("priority", Math.round(v))} min={0} /></Field>
          <Field label="Restricción">
            <select className="inp" value={f.constraint_type} onChange={(e) => set("constraint_type", e.target.value as ConstraintType)}>
              {CONSTRAINTS.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}
            </select>
          </Field>
          <Field label="Fecha de restricción"><input className="inp" type="date" value={f.constraint_date ?? ""} onChange={(e) => set("constraint_date", e.target.value || null)} /></Field>
          <Field label="Mes macro"><input className="inp" type="month" value={f.macro_month ?? ""} onChange={(e) => set("macro_month", e.target.value || null)} /></Field>
          <Field label="Ventana desde"><input className="inp" type="date" value={f.window_start ?? ""} onChange={(e) => set("window_start", e.target.value || null)} /></Field>
          <Field label="Ventana hasta"><input className="inp" type="date" value={f.window_end ?? ""} onChange={(e) => set("window_end", e.target.value || null)} /></Field>
          <label className="chk" style={{ alignSelf: "end" }}><input type="checkbox" checked={f.window_hard} onChange={(e) => set("window_hard", e.target.checked)} />Ventana dura</label>
          <Field label="Calendario">
            <select className="inp" value={f.calendar_id ?? ""} onChange={(e) => set("calendar_id", e.target.value ? Number(e.target.value) : null)}>
              <option value="">(del proyecto)</option>
              {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <Field label="Habilidad requerida">
            <input className="inp" list="skills-dl2" value={f.required_skill} onChange={(e) => set("required_skill", e.target.value)} />
            <datalist id="skills-dl2">{skills.map((s) => <option key={s} value={s} />)}</datalist>
          </Field>
          <Field label="Dotación"><NumInput value={f.crew_size} onChange={(v) => set("crew_size", v)} min={0} /></Field>
          <Field label="Instalación / ubicación"><input className="inp" value={f.location} onChange={(e) => set("location", e.target.value)} /></Field>
          <Field label="Equipo / TAG"><input className="inp" value={f.equipment} onChange={(e) => set("equipment", e.target.value)} /></Field>
          <Field label="Tensión (kV)"><NumInput value={f.voltage_kv} onChange={(v) => set("voltage_kv", v)} min={0} /></Field>
          <label className="chk" style={{ alignSelf: "end" }}><input type="checkbox" checked={f.requires_outage} onChange={(e) => set("requires_outage", e.target.checked)} />Requiere desconexión</label>
          <label className="chk" style={{ alignSelf: "end" }}><input type="checkbox" checked={f.locked} onChange={(e) => set("locked", e.target.checked)} />Bloqueada (no optimizar)</label>
          <Field label="Notas" style={{ gridColumn: "1 / -1" }}><textarea className="inp" rows={2} value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
        </div>
        <div className="muted small">
          Programada: {f.planned_start ?? "—"} → {f.planned_finish ?? "—"} · Temprano: {f.early_start ?? "—"} → {f.early_finish ?? "—"} ·
          Tardío: {f.late_start ?? "—"} → {f.late_finish ?? "—"} · Holgura total: {f.total_float ?? "—"} · Libre: {f.free_float ?? "—"}
        </div>
        <div className="grid2">
          <div>
            <div className="card-h" style={{ padding: "6px 0" }}>Relaciones</div>
            <table className="tbl"><thead><tr><th>Dirección</th><th>Actividad</th><th>Tipo</th><th className="num">Desfase</th><th /></tr></thead>
              <tbody>
                {rels.map((r) => (
                  <tr key={r.id}>
                    <td>{r.succ_id === act.id ? "Predecesora" : "Sucesora"}</td>
                    <td>{code(r.succ_id === act.id ? r.pred_id : r.succ_id)}</td>
                    <td>{r.type}</td><td className="num">{r.lag_days}</td>
                    <td><button className="btn small ghost" onClick={() => delRel(r)}>✕</button></td>
                  </tr>
                ))}
                <tr>
                  <td><select className="cell" value={newRel.dir} onChange={(e) => setNewRel({ ...newRel, dir: e.target.value as "pred" | "succ" })}><option value="pred">Predecesora</option><option value="succ">Sucesora</option></select></td>
                  <td><input className="cell" placeholder="código" value={newRel.code} onChange={(e) => setNewRel({ ...newRel, code: e.target.value })} /></td>
                  <td><select className="cell" value={newRel.type} onChange={(e) => setNewRel({ ...newRel, type: e.target.value as RelType })}>{["FS", "SS", "FF", "SF"].map((t) => <option key={t}>{t}</option>)}</select></td>
                  <td><input className="cell" type="number" value={newRel.lag} onChange={(e) => setNewRel({ ...newRel, lag: Number(e.target.value) })} /></td>
                  <td><button className="btn small" onClick={addRel}>＋</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div>
            <div className="card-h" style={{ padding: "6px 0" }}>Asignaciones (se guardan con Guardar) <span className="spacer" /><button className="btn small" onClick={addAsg}>＋</button></div>
            <table className="tbl"><thead><tr><th>Recurso</th><th>o habilidad</th><th className="num">Unid.</th><th className="num">HH</th><th /></tr></thead>
              <tbody>
                {asg.map((a) => (
                  <tr key={a.id}>
                    <td><select className="cell" value={a.resource_id ?? ""} onChange={(e) => updAsg({ ...a, resource_id: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">(genérico)</option>{resources.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></td>
                    <td><input className="cell" list="skills-dl2" value={a.skill} onChange={(e) => updAsg({ ...a, skill: e.target.value })} /></td>
                    <td><input className="cell right" type="number" value={a.units} onChange={(e) => updAsg({ ...a, units: Number(e.target.value) })} /></td>
                    <td><input className="cell right" type="number" value={a.work_hours} onChange={(e) => updAsg({ ...a, work_hours: Number(e.target.value) })} /></td>
                    <td><button className="btn small ghost" onClick={() => delAsg(a)}>✕</button></td>
                  </tr>
                ))}
                {asg.length === 0 && <tr><td colSpan={5} className="muted">Sin asignaciones. Si no asignas, se usa la habilidad requerida y la dotación.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Modal>
  );
}
