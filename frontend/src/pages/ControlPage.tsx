import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Baseline, EVMResult, ProgressEntry, SCurve } from "../api/types";
import { useAction, useStore } from "../state/store";
import { Card, Kpi, spiStatus } from "../components/ui";
import EChart from "../components/Charts/EChart";
import { scurveOption } from "../components/Charts/charts";
import { buildArrays, type ActArrays } from "../components/Gantt/model";
import { dayToIso, fmtDate, fmtDay, fmtNum, fmtPct, isoToDay, todayIso } from "../utils/dates";

interface Draft { pct?: number; hh?: number; cost?: number; res?: number | null; as?: string; af?: string; note?: string }

const PAGE = 200;

export default function ControlPage() {
  const { projectId, project, reloadProject, resources, fail, toast, theme, scheduleVersion, bumpSchedule } = useStore();
  const run = useAction();
  const [period, setPeriod] = useState<"day" | "week" | "month">("week");
  const [basis, setBasis] = useState<"hours" | "cost" | "count">("hours");
  const [abs, setAbs] = useState(false);
  const [sc, setSc] = useState<SCurve | null>(null);
  const [evm, setEvm] = useState<EVMResult | null>(null);
  const [bls, setBls] = useState<Baseline[]>([]);
  const [dd, setDd] = useState(project?.data_date ?? todayIso());
  const [acts, setActs] = useState<ActArrays | null>(null);
  const [drafts, setDrafts] = useState<Map<number, Draft>>(new Map());
  const [scope, setScope] = useState<"abiertas" | "vencen" | "todas">("abiertas");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState(false);
  const loadAll = () => setRefresh(v => v + 1);

  useEffect(() => { setDd(project?.data_date ?? todayIso()); }, [project?.data_date]);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    setLoading(true); setError(false);
    Promise.all([api.baselines(projectId), api.columnar(projectId)]).then(([baselines, c]) => {
      if (active) { setBls(baselines); setActs(buildArrays(c)); }
    }).catch(e => { if (active) { setError(true); fail(e); } }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId, scheduleVersion, refresh, fail]);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    setChartLoading(true); setChartError(false); setSc(null); setEvm(null);
    Promise.all([api.scurve(projectId, period, basis), api.evm(projectId, basis)]).then(([curve, value]) => {
      if (active) { setSc(curve); setEvm(value); }
    }).catch(e => { if (active) { setChartError(true); fail(e); } }).finally(() => { if (active) setChartLoading(false); });
    return () => { active = false; };
  }, [projectId, period, basis, scheduleVersion, refresh, project?.data_date, project?.active_baseline_id, fail]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const option = useMemo(() => scurveOption(sc, abs), [sc, abs, theme]);

  const saveDataDate = async () => {
    if (!projectId || !dd) return;
    const ok = await run("Actualizando fecha de corte…", () => api.setDataDate(projectId, dd));
    if (ok !== undefined) { toast(`Fecha de corte: ${fmtDate(dd)}`); await reloadProject(); bumpSchedule(); }
  };

  const newBaseline = async () => {
    if (!projectId) return;
    const name = window.prompt("Nombre de la línea base", `Línea base ${new Date().toLocaleDateString("es-CL")}`);
    if (!name) return;
    const b = await run("Guardando línea base…", () => api.createBaseline(projectId, name));
    if (b) { toast("Línea base creada"); loadAll(); }
  };
  const activate = async (b: Baseline) => {
    await run("Activando…", () => api.activateBaseline(b.id));
    await reloadProject(); loadAll(); bumpSchedule();
  };
  const delBl = async (b: Baseline) => {
    if (!window.confirm(`¿Eliminar ${b.name}?`)) return;
    await run("Eliminando…", () => api.deleteBaseline(b.id));
    await reloadProject(); loadAll();
  };

  const dataDay = isoToDay(project?.data_date ?? dd);
  const rows = useMemo(() => {
    if (!acts) return [] as number[];
    const t = q.trim().toLowerCase();
    const out: number[] = [];
    for (let i = 0; i < acts.n; i++) {
      if (t && !`${acts.codes[i]} ${acts.names[i]} ${acts.location[i]} ${acts.skill[i]}`.toLowerCase().includes(t)) continue;
      const s = acts.start[i], f = acts.finish[i] === acts.finish[i] ? acts.finish[i] : s;
      if (scope === "abiertas" && !(acts.pct[i] < 100 && s <= dataDay)) continue;
      if (scope === "vencen" && !(acts.pct[i] < 100 && f <= dataDay)) continue;
      out.push(i);
    }
    out.sort((x, y) => acts.start[x] - acts.start[y]);
    return out;
  }, [acts, q, scope, dataDay]);

  useEffect(() => setPage(0), [q, scope, dataDay]);
  useEffect(() => setPage(p => Math.min(p, Math.max(0, Math.ceil(rows.length / PAGE) - 1))), [rows.length]);

  const setDraft = (i: number, d: Draft) => setDrafts((m) => { const n = new Map(m); const id = acts!.ids[i]; n.set(id, { ...(m.get(id) ?? {}), ...d }); return n; });

  const saveProgress = async () => {
    if (!projectId || !acts || drafts.size === 0) return;
    const date = project?.data_date ?? dd;
    if (!Number.isFinite(isoToDay(date))) { fail(new Error("Indica una fecha de corte válida.")); return; }
    const list: Partial<ProgressEntry>[] = [];
    for (const [id, d] of drafts) {
      const i = acts.idToIndex.get(id);
      if (i === undefined) { fail(new Error("Una actividad del borrador ya no existe. Descarta el borrador.")); return; }
      const pct = d.pct ?? acts.pct[i];
      const start = d.as || null, finish = d.af || (pct >= 100 ? date : null);
      if (!Number.isFinite(pct) || pct < 0 || pct > 100 || !Number.isFinite(d.hh ?? 0) || (d.hh ?? 0) < 0 ||
          (start && (!Number.isFinite(isoToDay(start)) || start > date)) ||
          (finish && (!Number.isFinite(isoToDay(finish)) || finish > date || pct < 100 || (start && finish < start)))) {
        fail(new Error("Revisa el porcentaje (0–100), HH no negativas y fechas reales hasta la fecha de corte.")); return;
      }
      list.push({
        activity_id: acts.ids[i], date, percent_complete: pct, actual_hours: d.hh ?? 0, actual_cost: d.cost ?? 0,
        resource_id: d.res ?? null, actual_start: start,
        actual_finish: finish, note: d.note ?? "",
      });
    }
    const ok = await run(`Registrando avance de ${list.length} actividades…`, () => api.addProgress(projectId, list));
    if (ok !== undefined) { toast("Avance registrado"); setDrafts(new Map()); bumpSchedule(); }
  };

  const workers = resources.filter((r) => r.kind === "trabajador" || r.kind === "cuadrilla");
  const pageRows = rows.slice(page * PAGE, (page + 1) * PAGE);


  return (
    <div className="stack">
      {loading && <div role="status">Cargando actividades y líneas base…</div>}
      {error && <div role="alert">No se pudo cargar el control. <button className="btn" onClick={loadAll}>Reintentar</button></div>}
      <div className="grid2" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <Card title="Fecha de corte">
          <div className="row">
            <input className="inp" type="date" aria-label="Fecha de corte" value={dd} onChange={(e) => setDd(e.target.value)} />
            <button className="btn primary" onClick={saveDataDate}>Aplicar</button>
            <button className="btn small" onClick={() => setDd(todayIso())}>Hoy</button>
            <span className="muted small">El avance se registra a esta fecha; el CPM reprograma el remanente desde ella.</span>
          </div>
        </Card>
        <Card title="Líneas base" actions={<button className="btn small" onClick={newBaseline}>＋ Nueva</button>} bodyStyle={{ padding: 0 }}>
          {bls.length === 0 ? <div className="empty" style={{ padding: 16 }}>Sin líneas base. Crea una para comparar planificado vs. real.</div> : (
            <table className="tbl"><tbody>{bls.map((b) => (
              <tr key={b.id}><td><b>{b.name}</b> {project?.active_baseline_id === b.id && <span className="badge proyecto">Activa</span>}</td><td className="muted small">{b.created_at}</td>
                <td className="right">{project?.active_baseline_id !== b.id && <button className="btn small" onClick={() => activate(b)}>Activar</button>} <button className="btn small ghost" onClick={() => delBl(b)}>✕</button></td></tr>
            ))}</tbody></table>
          )}
        </Card>
      </div>

      {evm && (
        <div className="kpis">
          <Kpi label="% planificado" value={fmtPct(evm.percent_planned)} sub={`a ${fmtDate(evm.data_date)}`} />
          <Kpi label="% real (ganado)" value={fmtPct(evm.percent_complete)} sub={<>desvío {fmtPct(evm.percent_complete - evm.percent_planned)}</>} />
          <Kpi label="SPI" value={fmtNum(evm.SPI, 2)} status={spiStatus(evm.SPI)} />
          <Kpi label="SPI(t)" value={fmtNum(evm.SPI_t, 2)} status={spiStatus(evm.SPI_t)} sub="Earned Schedule (cronograma ganado)" />
          <Kpi label="CPI" value={fmtNum(evm.CPI, 2)} status={spiStatus(evm.CPI)} />
          <Kpi label="Término línea base" value={fmtDate(evm.planned_finish)} />
          <Kpi label="Término pronosticado" value={fmtDate(evm.forecast_finish)}
            sub={evm.forecast_finish && evm.planned_finish ? `${isoToDay(evm.forecast_finish) - isoToDay(evm.planned_finish)} días vs. línea base` : undefined} />
          <Kpi label={`EAC (${evm.basis === "hours" ? "HH" : evm.basis === "cost" ? project?.currency ?? "" : "N°"})`} value={fmtNum(evm.EAC)} sub={`BAC ${fmtNum(evm.BAC)} · VAC ${fmtNum(evm.VAC)}`} />
          <Kpi label="PV / EV / AC" value={<span style={{ fontSize: 14 }}>{fmtNum(evm.PV)} / {fmtNum(evm.EV)} / {fmtNum(evm.AC)}</span>} sub={`SV ${fmtNum(evm.SV)} · CV ${fmtNum(evm.CV)}`} />
          <Kpi label="TCPI" value={fmtNum(evm.TCPI, 2)} sub="índice para completar" />
        </div>
      )}

      <Card title="Curva S" actions={<>
        <div className="btn-group">{(["day", "week", "month"] as const).map((p) => <button key={p} className={`btn small${period === p ? " on" : ""}`} onClick={() => setPeriod(p)}>{p === "day" ? "Día" : p === "week" ? "Semana" : "Mes"}</button>)}</div>
        <div className="btn-group">{(["hours", "cost", "count"] as const).map((b) => <button key={b} className={`btn small${basis === b ? " on" : ""}`} onClick={() => setBasis(b)}>{b === "hours" ? "HH" : b === "cost" ? "Costo" : "Cantidad"}</button>)}</div>
        <div className="btn-group"><button className={`btn small${!abs ? " on" : ""}`} onClick={() => setAbs(false)}>%</button><button className={`btn small${abs ? " on" : ""}`} onClick={() => setAbs(true)}>Absoluto</button></div>
      </>}>
        {chartLoading ? <div role="status">Calculando curva S y valor ganado…</div> : chartError ? <div role="alert">No se pudieron calcular los indicadores. <button className="btn" onClick={loadAll}>Reintentar</button></div> : <EChart option={option} height={380} />}
        {sc && <div className="muted small">Presupuesto total: {fmtNum(sc.budget_total)} · Término planificado {fmtDate(sc.planned_finish)} · Término proyectado {fmtDate(sc.forecast_finish)}</div>}
      </Card>

      <Card title={`Registro de avance a la fecha de corte ${fmtDate(project?.data_date ?? dd)}`} actions={<>
        <input className="inp" placeholder="Buscar…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="inp" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
          <option value="abiertas">Iniciadas o por iniciar (≤ corte, no terminadas)</option>
          <option value="vencen">Debieron terminar (atrasadas)</option>
          <option value="todas">Todas</option>
        </select>
        <button className="btn small" disabled={drafts.size === 0} onClick={() => setDrafts(new Map())}>Descartar</button>
        <button className="btn small primary" disabled={drafts.size === 0 || loading || error} onClick={saveProgress}>Guardar avance ({drafts.size})</button>
      </>} bodyStyle={{ padding: 0 }}>
        <div className="tbl-wrap" style={{ maxHeight: 520 }}>
          <table className="tbl">
            <thead><tr><th>Código</th><th>Actividad</th><th>Inicio plan.</th><th>Término plan.</th><th className="num">HH</th><th className="num">% actual</th><th className="num">% nuevo</th><th className="num">HH reales (período)</th><th className="num">Costo real (período)</th><th>Ejecutó</th><th>Inicio real</th><th>Fin real</th><th>Nota</th></tr></thead>
            <tbody>
              {pageRows.map((i) => {
                const d = drafts.get(acts!.ids[i]) ?? {};
                const late = acts && (acts.finish[i] === acts.finish[i] ? acts.finish[i] : acts.start[i]) < dataDay && acts.pct[i] < 100;
                return (
                  <tr key={i} style={{ background: drafts.has(acts!.ids[i]) ? "var(--accent-soft)" : undefined }}>
                    <td className="mono">{acts!.codes[i]}</td>
                    <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis" }} title={acts!.names[i]}>{acts!.names[i]}</td>
                    <td>{fmtDay(acts!.start[i])}</td>
                    <td style={{ color: late ? "var(--critical)" : undefined }}>{fmtDay(acts!.finish[i])}</td>
                    <td className="num">{fmtNum(acts!.hh[i])}</td>
                    <td className="num">{acts!.pct[i].toFixed(0)}</td>
                    <td style={{ width: 80 }}><input className="cell right" aria-label={`Porcentaje de ${acts!.codes[i]}`} type="number" min={0} max={100} value={d.pct ?? ""} placeholder={acts!.pct[i].toFixed(0)}
                      onChange={(e) => setDraft(i, { pct: e.target.value === "" ? undefined : Number(e.target.value) })} /></td>
                    <td style={{ width: 90 }}><input className="cell right" aria-label={`HH reales de ${acts!.codes[i]}`} type="number" min={0} value={d.hh ?? ""} onChange={(e) => setDraft(i, { hh: e.target.value === "" ? undefined : Number(e.target.value) })} /></td>
                    <td style={{ width: 110 }}><input className="cell right" aria-label={`Costo real de ${acts!.codes[i]}`} type="number" min={0} value={d.cost ?? ""} onChange={(e) => setDraft(i, { cost: e.target.value === "" ? undefined : Number(e.target.value) })} /></td>
                    <td style={{ width: 150 }}><select aria-label={`Ejecutor de ${acts!.codes[i]}`} className="cell" value={d.res ?? ""} onChange={(e) => setDraft(i, { res: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">—</option>{workers.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></td>
                    <td style={{ width: 130 }}><input className="cell" aria-label="Inicio real" type="date" value={d.as ?? ""} onChange={(e) => setDraft(i, { as: e.target.value })} /></td>
                    <td style={{ width: 130 }}><input className="cell" aria-label="Fin real" type="date" value={d.af ?? ""} onChange={(e) => setDraft(i, { af: e.target.value })} /></td>
                    <td><input aria-label="Nota de avance" className="cell" value={d.note ?? ""} onChange={(e) => setDraft(i, { note: e.target.value })} /></td>
                  </tr>
                );
              })}
              {pageRows.length === 0 && <tr><td colSpan={13} className="muted" style={{ padding: 16 }}>No hay actividades en este filtro.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="row small" style={{ padding: "8px 14px" }}>
          <span className="muted">{rows.length.toLocaleString("es-CL")} actividades</span>
          <span style={{ flex: 1 }} />
          <button className="btn small" disabled={page === 0} onClick={() => setPage(page - 1)}>◀</button>
          <span>Página {page + 1} de {Math.max(1, Math.ceil(rows.length / PAGE))}</span>
          <button className="btn small" disabled={(page + 1) * PAGE >= rows.length} onClick={() => setPage(page + 1)}>▶</button>
        </div>
      </Card>
    </div>
  );
}
