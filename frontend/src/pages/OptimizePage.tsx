import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ObjectiveWeights, OptimizeMetrics, OptimizeOptions, OptimizeResponse, ScheduleRun } from "../api/types";
import { useAction, useStore } from "../state/store";
import { Card, Field, Kpi } from "../components/ui";
import EChart from "../components/Charts/EChart";
import { loadOption } from "../components/Charts/charts";
import { fmtDate, fmtNum } from "../utils/dates";
import { summarizeWarnings } from "../utils/warnings";

const OBJ: { key: keyof ObjectiveWeights; label: string; help: string }[] = [
  { key: "level", label: "Nivelar carga laboral", help: "Distribuye uniformemente las HH por día/semana y por rol o recurso." },
  { key: "makespan", label: "Minimizar plazo", help: "Termina lo antes posible respetando dependencias y capacidad." },
  { key: "window", label: "Respetar ventanas macro", help: "Ubica cada actividad dentro del mes o ventana indicada." },
  { key: "cost", label: "Minimizar costo / HH extra", help: "Reduce horas extra y sobreasignación, valorizadas al costo de HH extra." },
];

const PRESETS: { name: string; w: ObjectiveWeights }[] = [
  { name: "Nivelar carga", w: { level: 1, makespan: 0, window: 0, cost: 0 } },
  { name: "Minimizar plazo", w: { level: 0, makespan: 1, window: 0, cost: 0 } },
  { name: "Respetar ventanas", w: { level: 0, makespan: 0, window: 1, cost: 0 } },
  { name: "Minimizar costo", w: { level: 0, makespan: 0, window: 0, cost: 1 } },
  { name: "Equilibrado", w: { level: 1, makespan: 1, window: 1, cost: 1 } },
  { name: "Plazo + nivelación", w: { level: 0.5, makespan: 1, window: 0, cost: 0.2 } },
  { name: "Ventanas + nivelación", w: { level: 1, makespan: 0, window: 1, cost: 0.2 } },
];

const DEFAULT_OPT: OptimizeOptions = {
  method: "auto", time_limit_s: 30, level_period: "week", group_by: "skill", respect_resource_limits: true,
  allow_overtime: true, assign_workers: true, cpsat_max_activities: 3000, seed: 0, group_outages: true,
};

function parseJson<T>(v: T | string | null): T | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") { try { return JSON.parse(v) as T; } catch { return null; } }
  return v;
}

function delta(a: number | null | undefined, b: number | null | undefined, lowerIsBetter = true, dec = 0): JSX.Element | null {
  if (a === null || a === undefined || b === null || b === undefined || !Number.isFinite(a) || !Number.isFinite(b)) return null;
  const d = b - a;
  if (Math.abs(d) < 1e-9) return <span className="muted">sin cambio</span>;
  const good = lowerIsBetter ? d < 0 : d > 0;
  const pct = a !== 0 ? ` (${d > 0 ? "+" : ""}${((d / Math.abs(a)) * 100).toFixed(0)} %)` : "";
  return <span style={{ color: good ? "var(--good)" : "var(--critical)" }}>{good ? "▼" : "▲"} {fmtNum(Math.abs(d), dec)}{pct}</span>;
}

export default function OptimizePage() {
  const { projectId, project, bumpSchedule, toast, theme, fail } = useStore();
  const run = useAction();
  const [w, setW] = useState<ObjectiveWeights>({ level: 1, makespan: 0, window: 1, cost: 0.2 });
  const [opt, setOpt] = useState<OptimizeOptions>(DEFAULT_OPT);
  const [res, setRes] = useState<OptimizeResponse | null>(null);
  const [runs, setRuns] = useState<ScheduleRun[]>([]);
  const isMaint = project?.mode === "mantenimiento";

  const loadRuns = () => { if (projectId) api.runs(projectId).then(setRuns).catch(() => setRuns([])); };
  useEffect(loadRuns, [projectId]);

  const total = w.level + w.makespan + w.window + w.cost;
  const norm = (k: keyof ObjectiveWeights) => (total > 0 ? (w[k] / total) * 100 : 0);

  const execute = async (apply: boolean) => {
    if (!projectId) return;
    if (total <= 0) { fail(new Error("Asigna peso a al menos un objetivo")); return; }
    if (!Number.isFinite(opt.time_limit_s) || opt.time_limit_s <= 0) { fail(new Error("Indica un límite de tiempo positivo.")); return; }
    const r = await run(apply ? "Optimizando y aplicando…" : "Optimizando…", () => api.optimize(projectId, w, opt, apply));
    if (r) {
      const feasible = ["optimo", "factible", "heuristico"].includes(r.status);
      setRes({ ...r, applied: apply && feasible && r.applied !== false });
      loadRuns();
      if (r.warnings?.length) toast(summarizeWarnings(r.warnings).slice(0, 2).join(" · "), "info");
      if (!feasible) { toast(`No hay una solución aplicable (${r.status}). Revisa las advertencias.`, "error"); return; }
      if (apply && r.applied === false) { toast("El servidor no aplicó el resultado.", "error"); return; }
      if (apply) { toast("Programación optimizada aplicada"); bumpSchedule(); }
      else toast(`Optimización lista en ${r.elapsed_s.toFixed(1)} s (${r.method_used}, ${r.status}). Revisa y aplica si te conviene.`);
    }
  };

  const applyLast = async () => {
    if (!projectId || !res?.run_id) return;
    const ok = await run("Aplicando…", () => api.applyRun(projectId, res.run_id!));
    if (ok !== undefined) { toast("Programación aplicada"); setRes({ ...res, applied: true }); bumpSchedule(); loadRuns(); }
  };

  const applyRun = async (id: number) => {
    if (!projectId) return;
    const ok = await run("Aplicando corrida…", () => api.applyRun(projectId, id));
    if (ok !== undefined) { toast(`Corrida #${id} aplicada`); bumpSchedule(); loadRuns(); }
  };

  const before = res?.before ?? null, after = res?.after ?? null;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const optBefore = useMemo(() => loadOption(res?.profile_before ?? null, "Carga ANTES de optimizar"), [res, theme]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const optAfter = useMemo(() => loadOption(res?.profile_after ?? null, "Carga DESPUÉS de optimizar"), [res, theme]);

  const K = (label: string, k: keyof OptimizeMetrics, lower = true, dec = 0, fmt?: (v: number | string | null) => string) => {
    const a = before?.[k] as number | null | undefined, b = after?.[k] as number | null | undefined;
    return (
      <Kpi label={label} value={fmt ? fmt(b ?? null) : fmtNum(b as number, dec)}
        sub={<>antes {fmt ? fmt(a ?? null) : fmtNum(a as number, dec)} · {typeof a === "number" ? delta(a, b, lower, dec) : null}</>} />
    );
  };

  return (
    <div className="stack">
      <div className="grid2" style={{ gridTemplateColumns: "minmax(420px, 1fr) minmax(360px, 1fr)" }}>
        <Card title="Objetivos (elige uno o mezcla)">
          <div className="row" style={{ marginBottom: 10 }}>
            {PRESETS.map((p) => (
              <button key={p.name} className="btn small" onClick={() => setW(p.w)}>{p.name}</button>
            ))}
          </div>
          {OBJ.map((o) => (
            <div key={o.key}>
              <div className="slider-row">
                <span title={o.help}><b>{o.label}</b></span>
                <input type="range" min={0} max={1} step={0.05} value={w[o.key]} onChange={(e) => setW({ ...w, [o.key]: Number(e.target.value) })} aria-label={o.label} />
                <span className="right mono">{norm(o.key).toFixed(0)}%</span>
              </div>
              <div className="muted small" style={{ marginTop: -6, marginBottom: 6 }}>{o.help}</div>
            </div>
          ))}
          <div className="muted small">Los pesos se normalizan: el porcentaje indica la importancia relativa de cada objetivo en la función a minimizar.</div>
        </Card>
        <Card title="Opciones">
          <div className="form-grid">
            <Field label="Método">
              <select className="inp" value={opt.method} onChange={(e) => setOpt({ ...opt, method: e.target.value as OptimizeOptions["method"] })}>
                <option value="auto">Automático</option>
                <option value="heuristic">Heurístico (rápido, masivo)</option>
                <option value="cpsat" disabled={isMaint}>CP-SAT exacto (≤ {opt.cpsat_max_activities} act.)</option>
              </select>
            </Field>
            <Field label="Límite de tiempo (s)">
              <input className="inp" type="number" min={5} max={600} value={opt.time_limit_s} onChange={(e) => setOpt({ ...opt, time_limit_s: Number(e.target.value) })} />
            </Field>
            <Field label="Nivelar por">
              <select className="inp" value={opt.group_by} onChange={(e) => setOpt({ ...opt, group_by: e.target.value as OptimizeOptions["group_by"] })}>
                <option value="skill">Habilidad / rol</option><option value="resource">Recurso individual</option>
              </select>
            </Field>
            <Field label="Período de nivelación">
              <select className="inp" value={opt.level_period} onChange={(e) => setOpt({ ...opt, level_period: e.target.value as OptimizeOptions["level_period"] })}>
                <option value="day">Diario</option><option value="week">Semanal</option>
              </select>
            </Field>
          </div>
          <div className="stack" style={{ gap: 6, marginTop: 12 }}>
            <label className="chk"><input type="checkbox" checked={opt.respect_resource_limits} onChange={(e) => setOpt({ ...opt, respect_resource_limits: e.target.checked })} />Capacidad de recursos como restricción dura</label>
            <label className="chk"><input type="checkbox" checked={opt.allow_overtime} onChange={(e) => setOpt({ ...opt, allow_overtime: e.target.checked })} />Permitir horas extra (hasta el máximo de cada recurso)</label>
            {isMaint && <label className="chk"><input type="checkbox" checked={opt.assign_workers} onChange={(e) => setOpt({ ...opt, assign_workers: e.target.checked })} />Asignar trabajador concreto por habilidad</label>}
            {isMaint && <label className="chk"><input type="checkbox" checked={opt.group_outages} onChange={(e) => setOpt({ ...opt, group_outages: e.target.checked })} />Agrupar trabajos con desconexión por instalación</label>}
          </div>
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn" onClick={() => execute(false)}>Simular</button>
            <button className="btn primary" onClick={() => execute(true)}>Optimizar y aplicar</button>
            {res && res.run_id && ["optimo", "factible", "heuristico"].includes(res.status) && !res.applied && <button className="btn" onClick={applyLast}>Aplicar resultado simulado</button>}
          </div>
        </Card>
      </div>

      {res && !res.applied && !res.run_id && <div role="status">El servidor no devolvió un identificador de corrida. No se puede aplicar esta simulación desde aquí.</div>}
      {res && (
        <>
          <Card title={<>Resultado · {res.method_used} · {res.status} · {res.elapsed_s.toFixed(1)} s {res.applied && <span className="badge" style={{ marginLeft: 8 }}>Aplicado</span>}</>}>
            <div className="kpis">
              {K("Término", "finish", true, 0, (v) => (typeof v === "string" ? fmtDate(v) : "—"))}
              {K("Pico de carga (HH)", "peak_load", true, 0)}
              {K("Desv. estándar carga", "load_std", true, 1)}
              {K("HH sobre capacidad", "overload_hours", true, 0)}
              {K("HH extra", "overtime_hours", true, 0)}
              {K(`Costo HH extra (${project?.currency ?? ""})`, "overtime_cost", true, 0)}
              {K("Actividades fuera de ventana", "activities_out_of_window", true, 0)}
              {K("Días fuera de ventana", "days_out_of_window", true, 0)}
            </div>
            {res.warnings?.length > 0 && <details className="small" style={{ color: "var(--text-2)", marginTop: 10 }} open={summarizeWarnings(res.warnings).length <= 4}><summary>Advertencias ({summarizeWarnings(res.warnings).length})</summary><ul>{summarizeWarnings(res.warnings).map((x, i) => <li key={i}>{x}</li>)}</ul></details>}
          </Card>
          <div className="grid2">
            <Card><EChart option={optBefore} height={340} /></Card>
            <Card><EChart option={optAfter} height={340} /></Card>
          </div>
        </>
      )}

      <Card title="Historial de corridas" bodyStyle={{ padding: 0 }}>
        {runs.length === 0 ? <div className="empty">Sin corridas todavía.</div> : (
          <div className="tbl-wrap" style={{ maxHeight: 320 }}>
            <table className="tbl"><thead><tr><th>#</th><th>Fecha</th><th>Tipo</th><th>Pesos</th><th>Término</th><th className="num">Pico</th><th className="num">Fuera de ventana</th><th>Estado</th><th className="num">Tiempo</th><th /></tr></thead>
              <tbody>{runs.map((r) => {
                const ww = parseJson<ObjectiveWeights>(r.weights);
                const ma = parseJson<OptimizeMetrics>(r.metrics_after);
                return (
                  <tr key={r.id}>
                    <td>{r.id}</td><td>{r.created_at}</td><td>{({ cpm: "CPM", optimize: "Optimización", maintenance_level: "Nivelación mantenimiento" } as Record<string, string>)[r.kind] ?? r.kind}</td>
                    <td className="small">{ww ? `N ${ww.level} · P ${ww.makespan} · V ${ww.window} · C ${ww.cost}` : "—"}</td>
                    <td>{fmtDate(ma?.finish ?? null)}</td><td className="num">{fmtNum(ma?.peak_load)}</td><td className="num">{fmtNum(ma?.activities_out_of_window)}</td>
                    <td>{r.status}{r.applied ? " · aplicado" : ""}</td><td className="num">{r.elapsed_s ? `${r.elapsed_s.toFixed(1)} s` : ""}</td>
                    <td>{r.kind !== "cpm" && r.status !== "infactible" && !r.applied && <button className="btn small" onClick={() => applyRun(r.id)}>Aplicar</button>}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
