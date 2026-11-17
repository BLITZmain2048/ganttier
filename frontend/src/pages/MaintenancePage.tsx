import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { Frequency, LoadProfile, PeriodicTask } from "../api/types";
import { useAction, useStore } from "../state/store";
import { Card, Empty, Field, Modal, NumInput } from "../components/ui";
import { buildArrays, type ActArrays } from "../components/Gantt/model";
import { chartTokens } from "../components/Charts/EChart";
import { dayParts, fmtDate, MONTHS } from "../utils/dates";

const FREQS: { v: Frequency; l: string }[] = [
  { v: "D", l: "Diaria" }, { v: "W", l: "Semanal" }, { v: "M", l: "Mensual" }, { v: "Q", l: "Trimestral" },
  { v: "S", l: "Semestral" }, { v: "A", l: "Anual" }, { v: "ND", l: "Cada N días" }, { v: "NW", l: "Cada N semanas" },
  { v: "NM", l: "Cada N meses" }, { v: "NA", l: "Cada N años" },
];
const freqLabel = (t: PeriodicTask) => {
  const f = FREQS.find((x) => x.v === t.frequency)?.l ?? t.frequency;
  return t.frequency.startsWith("N") ? f.replace("N", String(t.interval)) : f;
};

const EMPTY: Partial<PeriodicTask> = {
  code: "", name: "", frequency: "M", interval: 1, duration_days: 1, work_hours: 8, crew_size: 2, required_skill: "",
  resource_id: null, first_due: null, preferred_months: "", preferred_weekday: null, day_of_month: null, tol_before_days: 7,
  tol_after_days: 7, window_hard: false, location: "", equipment: "", voltage_kv: null, requires_outage: false, discipline: "",
  calendar_id: null, wbs_code: "", priority: 50, cost_per_occurrence: 0, active: true, horizon_end: null,
};

function Heatmap({ profile }: { profile: LoadProfile | null }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [tip, setTip] = useState<string>("");
  const { theme } = useStore();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [wrapW, setWrapW] = useState(900);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWrapW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [profile]);
  const RH = 22, LW = 150, TH = 26;
  const CW = Math.max(12, Math.floor((wrapW - LW - 14) / Math.max(1, profile?.labels.length ?? 1)));
  useEffect(() => {
    const cv = ref.current;
    if (!cv || !profile) return;
    const t = chartTokens();
    const rows = profile.series.length, cols = profile.labels.length;
    const W = LW + cols * CW + 10, H = TH + rows * RH + 4;
    const dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = `${W}px`; cv.style.height = `${H}px`;
    const ctx = cv.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = t.surface; ctx.fillRect(0, 0, W, H);
    ctx.font = "11px Segoe UI, system-ui, sans-serif"; ctx.textBaseline = "middle";
    // escala secuencial azul (100 -> 700) según utilización carga/capacidad; rojo si > 100 %
    const ramp = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
    let lastMonth = -1;
    for (let c = 0; c < cols; c++) {
      const lbl = profile.labels[c];
      const m = Number(lbl.slice(5, 7));
      if (m !== lastMonth && (c > 0 || Number(lbl.slice(8, 10)) <= 7)) {
        ctx.fillStyle = t.text2;
        ctx.fillText(`${MONTHS[m - 1]} ${lbl.slice(2, 4)}`, LW + c * CW, 12);
        ctx.strokeStyle = t.grid; ctx.beginPath(); ctx.moveTo(LW + c * CW + 0.5, TH - 6); ctx.lineTo(LW + c * CW + 0.5, H); ctx.stroke();
        lastMonth = m;
      }
    }
    profile.series.forEach((s, r) => {
      ctx.fillStyle = t.text; ctx.fillText(s.key.length > 22 ? `${s.key.slice(0, 21)}…` : s.key, 6, TH + r * RH + RH / 2);
      for (let c = 0; c < cols; c++) {
        const cap = s.capacity[c] ?? 0, ld = s.load[c] ?? 0;
        if (ld <= 0) continue;
        const u = cap > 0 ? ld / cap : 2;
        ctx.fillStyle = u > 1.0001 ? t.critical : ramp[Math.min(ramp.length - 1, Math.floor(u * ramp.length))];
        ctx.fillRect(LW + c * CW + 1, TH + r * RH + 2, CW - 2, RH - 4);
      }
    });
  }, [profile, theme, CW]);
  if (!profile || profile.series.length === 0) return <Empty>Sin carga. Genera las ocurrencias y optimiza.</Empty>;
  return (
    <div>
      <div className="heat-wrap" ref={wrapRef}>
        <canvas ref={ref} onMouseMove={(e) => {
          const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
          const x = e.clientX - rect.left, y = e.clientY - rect.top;
          const c = Math.floor((x - LW) / CW), r = Math.floor((y - TH) / RH);
          if (c < 0 || r < 0 || r >= profile.series.length || c >= profile.labels.length) { setTip(""); return; }
          const s = profile.series[r];
          const cap = s.capacity[c], ld = s.load[c];
          setTip(`${s.key} · semana del ${fmtDate(profile.labels[c])}: ${ld.toFixed(0)} HH de ${cap.toFixed(0)} disponibles (${cap > 0 ? ((ld / cap) * 100).toFixed(0) : "—"} %)`);
        }} onMouseLeave={() => setTip("")} />
      </div>
      <div className="row small" style={{ marginTop: 6, minHeight: 18 }}>
        <span className="legend"><span><i style={{ background: "#cde2fb" }} />baja</span><span><i style={{ background: "#3987e5" }} />media</span><span><i style={{ background: "#0d366b" }} />alta</span><span><i style={{ background: "var(--critical)" }} />sobre capacidad</span></span>
        <span className="muted">{tip}</span>
      </div>
    </div>
  );
}

export default function MaintenancePage() {
  const { projectId, project, resources, calendars, fail, toast, bumpSchedule, scheduleVersion } = useStore();
  const run = useAction();
  const [tasks, setTasks] = useState<PeriodicTask[]>([]);
  const [edit, setEdit] = useState<Partial<PeriodicTask> | null>(null);
  const [q, setQ] = useState("");
  const [profile, setProfile] = useState<LoadProfile | null>(null);
  const [acts, setActs] = useState<ActArrays | null>(null);
  const [hs, setHs] = useState(project?.start_date ?? "");
  const [he, setHe] = useState(project?.end_date ?? "");
  const [replace, setReplace] = useState(false);
  const [taskPage, setTaskPage] = useState(0);
  const [locF, setLocF] = useState("");
  const [outageOnly, setOutageOnly] = useState(false);

  useEffect(() => { setHs(project?.start_date ?? ""); setHe(project?.end_date ?? ""); }, [project]);

  const loadTasks = () => { if (projectId) api.periodicTasks(projectId).then(setTasks).catch(fail); };
  useEffect(loadTasks, [projectId]);
  useEffect(() => {
    if (!projectId) return;
    let active = true;
    api.load(projectId, "skill", "week").then(p => { if (active) setProfile(p); }).catch(e => { if (active) { setProfile(null); fail(e); } });
    api.columnar(projectId).then(c => { if (active) setActs(buildArrays(c)); }).catch(e => { if (active) fail(e); });
    return () => { active = false; };
  }, [projectId, scheduleVersion, fail]);

  const list = tasks.filter((t) => (!q || `${t.code} ${t.name} ${t.location} ${t.equipment} ${t.required_skill}`.toLowerCase().includes(q.toLowerCase()))
    && (!locF || t.location === locF) && (!outageOnly || t.requires_outage));
  useEffect(() => setTaskPage(0), [q, locF, outageOnly]);
  useEffect(() => setTaskPage(p => Math.min(p, Math.max(0, Math.ceil(list.length / 200) - 1))), [list.length]);
  const locations = useMemo(() => Array.from(new Set(tasks.map((t) => t.location).filter(Boolean))).sort(), [tasks]);
  const skills = useMemo(() => Array.from(new Set(resources.flatMap((r) => r.skills))).sort(), [resources]);

  const estPerYear = (t: PeriodicTask) => {
    const n = Math.max(1, t.interval || 1);
    switch (t.frequency) {
      case "D": return 365; case "W": return 52; case "M": return 12; case "Q": return 4; case "S": return 2; case "A": return 1;
      case "ND": return 365 / n; case "NW": return 52 / n; case "NM": return 12 / n; case "NA": return 1 / n;
    }
    return 0;
  };
  const annualHH = list.reduce((s, t) => s + (t.active ? estPerYear(t) * t.work_hours : 0), 0);

  const save = async () => {
    if (!edit || !projectId) return;
    if (!edit.code?.trim() || !edit.name?.trim()) { fail(new Error("Código y nombre son obligatorios")); return; }
    try {
      if (edit.id) await api.updatePeriodicTask(edit.id, edit); else await api.createPeriodicTask(projectId, edit);
      setEdit(null); loadTasks(); toast("Tarea guardada");
    } catch (e) { fail(e); }
  };

  const remove = async (t: PeriodicTask) => {
    if (!window.confirm(`¿Eliminar la tarea ${t.code}? También se eliminarán sus ocurrencias generadas.`)) return;
    try { await api.deletePeriodicTask(t.id); loadTasks(); bumpSchedule(); } catch (e) { fail(e); }
  };

  const generate = async () => {
    if (!projectId || !hs || !he) { fail(new Error("Indica el horizonte de generación")); return; }
    if (hs > he) { fail(new Error("El inicio debe ser anterior o igual al fin del horizonte.")); return; }
    const r = await run("Generando ocurrencias…", () => api.generateOccurrences(projectId, { horizon_start: hs, horizon_end: he, replace }));
    if (r) { toast(`${r.created.toLocaleString("es-CL")} ocurrencias generadas. Ahora optimiza para nivelar la carga.`); bumpSchedule(); }
  };

  // calendario anual por instalación (conteo de ocurrencias por mes)
  const annual = useMemo(() => {
    if (!acts) return null;
    const months: string[] = [];
    const idx = new Map<string, number>();
    const locs = new Map<string, number[]>();
    const outs = new Map<string, number[]>();
    let min = Infinity, max = -Infinity;
    for (let i = 0; i < acts.n; i++) if (acts.start[i] === acts.start[i]) { min = Math.min(min, acts.start[i]); max = Math.max(max, acts.start[i]); }
    if (!Number.isFinite(min)) return null;
    const p0 = dayParts(min), p1 = dayParts(max);
    for (let y = p0.y, m = p0.m; y < p1.y || (y === p1.y && m <= p1.m); m === 11 ? (y++, m = 0) : m++) {
      const k = `${y}-${String(m + 1).padStart(2, "0")}`;
      idx.set(k, months.length); months.push(k);
      if (months.length >= 60) break;
    }
    for (let i = 0; i < acts.n; i++) {
      const s = acts.start[i];
      if (s !== s) continue;
      if (outageOnly && !acts.outage[i]) continue;
      const loc = acts.location[i] || "(sin instalación)";
      if (locF && loc !== locF) continue;
      const pp = dayParts(s);
      const k = idx.get(`${pp.y}-${String(pp.m + 1).padStart(2, "0")}`);
      if (k === undefined) continue;
      if (!locs.has(loc)) { locs.set(loc, new Array(months.length).fill(0)); outs.set(loc, new Array(months.length).fill(0)); }
      locs.get(loc)![k]++;
      if (acts.outage[i]) outs.get(loc)![k]++;
    }
    const rows = Array.from(locs.entries()).sort((a, b) => a[0].localeCompare(b[0], "es"));
    const maxV = rows.reduce((m, [, v]) => Math.max(m, ...v), 1);
    return { months, rows, outs, maxV };
  }, [acts, locF, outageOnly]);

  return (
    <div className="stack">
      <div className="kpis">
        <div className="kpi"><div className="l">Tareas del plan maestro</div><div className="v">{tasks.length}</div><div className="s">{tasks.filter((t) => t.active).length} activas</div></div>
        <div className="kpi"><div className="l">Ocurrencias generadas</div><div className="v">{acts ? acts.n.toLocaleString("es-CL") : "—"}</div></div>
        <div className="kpi"><div className="l">HH anuales estimadas</div><div className="v">{Math.round(annualHH).toLocaleString("es-CL")}</div><div className="s">según filtro</div></div>
        <div className="kpi"><div className="l">Con desconexión</div><div className="v">{tasks.filter((t) => t.requires_outage).length}</div><div className="s">tareas que requieren indisponibilidad</div></div>
      </div>

      <Card title="Generar ocurrencias" actions={<span className="muted small">Sin reemplazo se conservan las ocurrencias existentes. Reemplazar elimina todas las ocurrencias y sus avances.</span>}>
        <div className="row">
          <Field label="Desde"><input className="inp" type="date" value={hs} onChange={(e) => setHs(e.target.value)} /></Field>
          <Field label="Hasta"><input className="inp" type="date" value={he} onChange={(e) => setHe(e.target.value)} /></Field>
          <label className="chk"><input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} />Reemplazar ocurrencias y eliminar sus avances</label>
          <button className="btn primary" style={{ alignSelf: "end" }} onClick={generate}>↻ Generar</button>
          <a className="btn" style={{ alignSelf: "end", textDecoration: "none" }} href={`#/p/${projectId}/optimizar`}>⚙ Nivelar / optimizar</a>
          {projectId && <a className="btn" style={{ alignSelf: "end", textDecoration: "none" }} href={api.outagesUrl(projectId, hs || undefined, he || undefined)} download="programa_desconexiones.xlsx" title="Trabajos con desconexión agrupados por instalación y fecha">⚡ Programa de desconexiones</a>}
        </div>
      </Card>

      <Card title={`Plan maestro (${list.length})`} actions={<>
        <input className="inp" placeholder="Buscar…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="inp" value={locF} onChange={(e) => setLocF(e.target.value)}><option value="">Todas las instalaciones</option>{locations.map((l) => <option key={l}>{l}</option>)}</select>
        <label className="chk small"><input type="checkbox" checked={outageOnly} onChange={(e) => setOutageOnly(e.target.checked)} />Solo con desconexión</label>
        <button className="btn small primary" onClick={() => setEdit({ ...EMPTY })}>＋ Tarea</button>
      </>} bodyStyle={{ padding: 0 }}>
        {list.length === 0 ? <Empty>Sin tareas periódicas. Agrégalas aquí o impórtalas con la plantilla de mantenimiento.</Empty> : (
          <div className="tbl-wrap" style={{ maxHeight: 420 }}>
            <table className="tbl"><thead><tr><th>Código</th><th>Nombre</th><th>Frecuencia</th><th className="num">Dur.</th><th className="num">HH</th><th className="num">Dot.</th><th>Habilidad</th><th>Instalación</th><th>Equipo</th><th className="num">kV</th><th>Desc.</th><th>Tolerancia</th><th>Meses pref.</th><th className="num">Ocurr./año</th><th /></tr></thead>
              <tbody>{list.slice(taskPage * 200, (taskPage + 1) * 200).map((t) => (
                <tr key={t.id} style={{ opacity: t.active ? 1 : .5 }}>
                  <td className="mono">{t.code}</td><td>{t.name}</td><td>{freqLabel(t)}</td><td className="num">{t.duration_days}</td><td className="num">{t.work_hours}</td>
                  <td className="num">{t.crew_size}</td><td>{t.required_skill}</td><td>{t.location}</td><td>{t.equipment}</td><td className="num">{t.voltage_kv ?? ""}</td>
                  <td>{t.requires_outage ? "Sí" : ""}</td><td>−{t.tol_before_days}/+{t.tol_after_days} d{t.window_hard ? " (dura)" : ""}</td><td>{t.preferred_months}</td>
                  <td className="num">{estPerYear(t).toFixed(1)}</td>
                  <td className="right"><button className="btn small" onClick={() => setEdit({ ...t })}>Editar</button> <button className="btn small ghost" onClick={() => remove(t)}>✕</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>

      {list.length > 200 && <div className="row"><button className="btn" disabled={taskPage === 0} onClick={() => setTaskPage(p => p - 1)}>Anterior</button><span>Página {taskPage + 1} de {Math.ceil(list.length / 200)}</span><button className="btn" disabled={(taskPage + 1) * 200 >= list.length} onClick={() => setTaskPage(p => p + 1)}>Siguiente</button></div>}
      <Card title="Mapa de calor de carga semanal por habilidad (utilización = HH programadas / capacidad)">
        <Heatmap profile={profile} />
      </Card>

      <Card title="Calendario de ocurrencias por instalación (hasta 60 meses)" bodyStyle={{ padding: 0 }}>
        {!annual || annual.rows.length === 0 ? <Empty>Sin ocurrencias programadas.</Empty> : (
          <div className="tbl-wrap" style={{ maxHeight: 460 }}>
            <table className="tbl"><thead><tr><th>Instalación</th>{annual.months.map((m) => <th key={m} className="num">{MONTHS[Number(m.slice(5, 7)) - 1]} {m.slice(2, 4)}</th>)}<th className="num">Total</th></tr></thead>
              <tbody>{annual.rows.map(([loc, v]) => (
                <tr key={loc}><td>{loc}</td>{v.map((c, k) => {
                  const o = annual.outs.get(loc)![k];
                  const alpha = c / annual.maxV;
                  return <td key={k} className="num" title={`${c} ocurrencias${o ? `, ${o} con desconexión` : ""}`}
                    style={{ background: c ? `rgba(42,120,214,${0.08 + alpha * 0.55})` : undefined, color: alpha > 0.6 ? "#fff" : undefined }}>
                    {c || ""}{o ? <sup style={{ color: alpha > 0.6 ? "#ffd2c0" : "var(--datadate)" }}> ⚡{o}</sup> : null}</td>;
                })}<td className="num"><b>{v.reduce((a, b) => a + b, 0)}</b></td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>

      {edit && (
        <Modal title={edit.id ? `Editar ${edit.code}` : "Nueva tarea periódica"} onClose={() => setEdit(null)} width={900}
          footer={<><button className="btn" onClick={() => setEdit(null)}>Cancelar</button><button className="btn primary" onClick={save}>Guardar</button></>}>
          <div className="form-grid">
            <Field label="Código"><input className="inp" autoFocus value={edit.code ?? ""} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></Field>
            <Field label="Nombre" style={{ gridColumn: "span 2" }}><input className="inp" value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Frecuencia">
              <select className="inp" value={edit.frequency} onChange={(e) => setEdit({ ...edit, frequency: e.target.value as Frequency })}>{FREQS.map((f) => <option key={f.v} value={f.v}>{f.l}</option>)}</select>
            </Field>
            <Field label="Intervalo N"><NumInput value={edit.interval} onChange={(v) => setEdit({ ...edit, interval: Math.max(1, Math.round(v)) })} min={1} /></Field>
            <Field label="Primera fecha"><input className="inp" type="date" value={edit.first_due ?? ""} onChange={(e) => setEdit({ ...edit, first_due: e.target.value || null })} /></Field>
            <Field label="Duración (días)"><NumInput value={edit.duration_days} onChange={(v) => setEdit({ ...edit, duration_days: Math.max(1, Math.round(v)) })} min={1} /></Field>
            <Field label="HH por ejecución"><NumInput value={edit.work_hours} onChange={(v) => setEdit({ ...edit, work_hours: v })} min={0} /></Field>
            <Field label="Dotación"><NumInput value={edit.crew_size} onChange={(v) => setEdit({ ...edit, crew_size: v })} min={0} /></Field>
            <Field label="Habilidad requerida">
              <input className="inp" list="mt-skills" value={edit.required_skill ?? ""} onChange={(e) => setEdit({ ...edit, required_skill: e.target.value })} />
              <datalist id="mt-skills">{skills.map((s) => <option key={s} value={s} />)}</datalist>
            </Field>
            <Field label="Trabajador fijo (opcional)">
              <select className="inp" value={edit.resource_id ?? ""} onChange={(e) => setEdit({ ...edit, resource_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">(asignación automática)</option>{resources.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </Field>
            <Field label="Meses preferidos (1-12, coma)"><input className="inp" placeholder="3,9" value={edit.preferred_months ?? ""} onChange={(e) => setEdit({ ...edit, preferred_months: e.target.value })} /></Field>
            <Field label="Día de semana preferido">
              <select className="inp" value={edit.preferred_weekday ?? ""} onChange={(e) => setEdit({ ...edit, preferred_weekday: e.target.value === "" ? null : Number(e.target.value) })}>
                <option value="">(cualquiera)</option>{["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"].map((d, k) => <option key={d} value={k}>{d}</option>)}
              </select>
            </Field>
            <Field label="Día del mes"><NumInput value={edit.day_of_month} onChange={(v) => setEdit({ ...edit, day_of_month: v ? Math.min(31, Math.max(1, Math.round(v))) : null })} min={1} /></Field>
            <Field label="Tolerancia antes (días)"><NumInput value={edit.tol_before_days} onChange={(v) => setEdit({ ...edit, tol_before_days: Math.round(v) })} min={0} /></Field>
            <Field label="Tolerancia después (días)"><NumInput value={edit.tol_after_days} onChange={(v) => setEdit({ ...edit, tol_after_days: Math.round(v) })} min={0} /></Field>
            <label className="chk" style={{ alignSelf: "end" }}><input type="checkbox" checked={edit.window_hard ?? false} onChange={(e) => setEdit({ ...edit, window_hard: e.target.checked })} />Ventana dura</label>
            <Field label="Instalación"><input className="inp" list="mt-locs" value={edit.location ?? ""} onChange={(e) => setEdit({ ...edit, location: e.target.value })} /><datalist id="mt-locs">{locations.map((l) => <option key={l} value={l} />)}</datalist></Field>
            <Field label="Equipo / TAG"><input className="inp" value={edit.equipment ?? ""} onChange={(e) => setEdit({ ...edit, equipment: e.target.value })} /></Field>
            <Field label="Tensión (kV)"><NumInput value={edit.voltage_kv} onChange={(v) => setEdit({ ...edit, voltage_kv: v || null })} min={0} /></Field>
            <label className="chk" style={{ alignSelf: "end" }}><input type="checkbox" checked={edit.requires_outage ?? false} onChange={(e) => setEdit({ ...edit, requires_outage: e.target.checked })} />Requiere desconexión</label>
            <Field label="Disciplina"><input className="inp" value={edit.discipline ?? ""} onChange={(e) => setEdit({ ...edit, discipline: e.target.value })} /></Field>
            <Field label="Calendario">
              <select className="inp" value={edit.calendar_id ?? ""} onChange={(e) => setEdit({ ...edit, calendar_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">(del proyecto)</option>{calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field>
            <Field label="Prioridad"><NumInput value={edit.priority} onChange={(v) => setEdit({ ...edit, priority: Math.round(v) })} min={0} /></Field>
            <Field label="Costo por ejecución"><NumInput value={edit.cost_per_occurrence} onChange={(v) => setEdit({ ...edit, cost_per_occurrence: v })} min={0} /></Field>
            <label className="chk" style={{ alignSelf: "end" }}><input type="checkbox" checked={edit.active ?? true} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />Activa</label>
          </div>
        </Modal>
      )}
    </div>
  );
}
