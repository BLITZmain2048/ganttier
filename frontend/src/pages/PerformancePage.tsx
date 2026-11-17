import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { WorkerKPI } from "../api/types";
import { useStore } from "../state/store";
import { Card, Empty, Kpi } from "../components/ui";
import EChart from "../components/Charts/EChart";
import { performanceOption } from "../components/Charts/charts";
import { fmtNum, periodBounds, todayIso, isoToDay } from "../utils/dates";

type SortKey = keyof WorkerKPI;
const COLS: { k: SortKey; l: string; dec?: number; pct?: boolean }[] = [
  { k: "name", l: "Trabajador" },
  { k: "compliance_pct", l: "Cumplimiento", dec: 1, pct: true },
  { k: "hh_efficiency", l: "Eficiencia HH", dec: 2 },
  { k: "avg_delay_days", l: "Atraso prom. (d)", dec: 1 },
  { k: "SPI", l: "SPI", dec: 2 },
  { k: "CPI", l: "CPI", dec: 2 },
  { k: "due_in_period", l: "Vencen en período" },
  { k: "completed_on_time", l: "Terminadas a tiempo" },
  { k: "completed_activities", l: "Terminadas" },
  { k: "pending_activities", l: "Pendientes" },
  { k: "planned_hours", l: "HH plan.", dec: 0 },
  { k: "earned_hours", l: "HH ganadas", dec: 0 },
  { k: "actual_hours", l: "HH reales", dec: 0 },
];

export default function PerformancePage() {
  const { projectId, project, fail, theme, scheduleVersion } = useStore();
  const [kind, setKind] = useState<"weekly" | "monthly" | "annual" | "custom">("monthly");
  const [ref, setRef] = useState(project?.data_date ?? todayIso());
  const [cs, setCs] = useState(todayIso());
  const [ce, setCe] = useState(todayIso());
  const [data, setData] = useState<WorkerKPI[]>([]);
  const [sort, setSort] = useState<{ k: SortKey; desc: boolean }>({ k: "compliance_pct", desc: true });
  const [metric, setMetric] = useState<"compliance_pct" | "hh_efficiency" | "avg_delay_days" | "SPI">("compliance_pct");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState(0);

  const [start, end] = kind === "custom" ? [cs, ce] : periodBounds(kind, ref);

  const valid = Number.isFinite(isoToDay(start)) && Number.isFinite(isoToDay(end)) && start <= end;
  useEffect(() => {
    let active = true;
    setData([]); setError(false); setLoading(false); setPage(0);
    if (!projectId || !valid) return;
    setLoading(true);
    api.performance(projectId, start, end).then(value => { if (active) setData(value); })
      .catch(e => { if (active) { setError(true); fail(e); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId, start, end, valid, fail, scheduleVersion, project?.data_date, refresh]);

  const sorted = useMemo(() => [...data].sort((a, b) => {
    const va = a[sort.k], vb = b[sort.k];
    if (va == null && vb == null) return 0;
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    const c = typeof va === "string" ? va.localeCompare(vb as string, "es") : (va as number) - (vb as number);
    return sort.desc ? -c : c;
  }), [data, sort]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const option = useMemo(() => performanceOption(data, metric), [data, metric, theme]);

  const tot = useMemo(() => {
    const due = data.reduce((s, x) => s + x.due_in_period, 0), ok = data.reduce((s, x) => s + x.completed_on_time, 0);
    const earned = data.reduce((s, x) => s + x.earned_hours, 0), act = data.reduce((s, x) => s + x.actual_hours, 0);
    return { compliance: due ? (ok / due) * 100 : null, eff: act ? earned / act : null, earned, act };
  }, [data]);

  const cls = (k: SortKey, v: number | null) => {
    if (v === null) return undefined;
    if (k === "compliance_pct") return v >= 90 ? "var(--good)" : v >= 75 ? "#b07800" : "var(--critical)";
    if (k === "hh_efficiency" || k === "SPI" || k === "CPI") return v >= 0.95 ? "var(--good)" : v >= 0.85 ? "#b07800" : "var(--critical)";
    if (k === "avg_delay_days") return v <= 0 ? "var(--good)" : v <= 3 ? "#b07800" : "var(--critical)";
    return undefined;
  };

  return (
    <div className="stack">
      <div className="row">
        <div className="btn-group">
          {([["weekly", "Semana"], ["monthly", "Mes"], ["annual", "Año"], ["custom", "Personalizado"]] as const).map(([k, l]) =>
            <button key={k} className={`btn small${kind === k ? " on" : ""}`} onClick={() => setKind(k)}>{l}</button>)}
        </div>
        {kind === "custom" ? <>
          <input className="inp" aria-label="Inicio del período" type="date" value={cs} onChange={(e) => setCs(e.target.value)} /> → <input className="inp" aria-label="Fin del período" type="date" value={ce} onChange={(e) => setCe(e.target.value)} />
        </> : <>
          <span className="muted">Período que contiene</span>
          <input className="inp" aria-label="Fecha de referencia del período" type="date" value={ref} onChange={(e) => setRef(e.target.value)} />
        </>}
        <b>{start.split("-").reverse().join("-")} → {end.split("-").reverse().join("-")}</b>
        {loading && <span role="status" className="muted">Calculando…</span>}
      </div>
      {!valid && <div role="alert">Selecciona fechas válidas; el inicio debe ser anterior o igual al fin.</div>}
      {error && <div role="alert">No se pudo cargar el desempeño. <button className="btn" onClick={() => setRefresh(v => v + 1)}>Reintentar</button></div>}
      <div className="kpis">
        <Kpi label="Trabajadores evaluados" value={loading || error || !valid ? "—" : data.length} />
        <Kpi label="Cumplimiento global" value={tot.compliance === null ? "—" : `${fmtNum(tot.compliance, 1)} %`} />
        <Kpi label="Eficiencia HH global" value={fmtNum(tot.eff, 2)} sub={`${fmtNum(tot.earned)} HH ganadas / ${fmtNum(tot.act)} reales`} />
      </div>
      <Card title="Indicadores por trabajador" bodyStyle={{ padding: 0 }}>
        {loading ? <Empty>Calculando indicadores…</Empty> : error || !valid ? <Empty>Indicadores no disponibles.</Empty> : data.length === 0 ? <Empty>Sin datos en el período. Registra avance indicando quién ejecutó cada actividad o asigna recursos a las actividades.</Empty> : (
          <div className="tbl-wrap" style={{ maxHeight: 460 }}>
            <table className="tbl">
              <thead><tr>{COLS.map((c) => (
                <th key={c.k} className={`sortable${c.k !== "name" ? " num" : ""}`} tabIndex={0} aria-sort={sort.k === c.k ? (sort.desc ? "descending" : "ascending") : "none"} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPage(0); setSort({ k: c.k, desc: sort.k === c.k ? !sort.desc : true }); } }} onClick={() => { setPage(0); setSort({ k: c.k, desc: sort.k === c.k ? !sort.desc : true }); }}>
                  {c.l}{sort.k === c.k ? (sort.desc ? " ▼" : " ▲") : ""}
                </th>))}</tr></thead>
              <tbody>{sorted.slice(page * 100, (page + 1) * 100).map((r) => (
                <tr key={r.resource_id}>{COLS.map((c) => {
                  const v = r[c.k];
                  if (c.k === "name") return <td key={c.k}><b>{r.name}</b></td>;
                  const n = v as number | null;
                  return <td key={c.k} className="num" style={{ color: cls(c.k, n), fontWeight: cls(c.k, n) ? 600 : undefined }}>{n === null ? "—" : `${fmtNum(n, c.dec ?? 0)}${c.pct ? " %" : ""}`}</td>;
                })}</tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {data.length > 100 && <div className="row"><button className="btn" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Anterior</button><span>Página {page + 1} de {Math.ceil(data.length / 100)}</span><button className="btn" disabled={(page + 1) * 100 >= data.length} onClick={() => setPage(p => p + 1)}>Siguiente</button></div>}
      <Card title="Comparativo (hasta 40 trabajadores con mayor valor)" actions={
        <div className="btn-group">
          {([["compliance_pct", "Cumplimiento"], ["hh_efficiency", "Eficiencia HH"], ["avg_delay_days", "Atraso"], ["SPI", "SPI"]] as const).map(([k, l]) =>
            <button key={k} className={`btn small${metric === k ? " on" : ""}`} onClick={() => setMetric(k)}>{l}</button>)}
        </div>}>
        <EChart option={option} height={Math.max(260, Math.min(40, data.length) * 22 + 60)} />
        <p className="muted small">Cumplimiento = terminadas a tiempo / actividades que vencían en el período. Eficiencia HH = HH ganadas (presupuesto × avance) / HH reales. SPI y CPI calculados con las actividades asignadas a cada trabajador.</p>
      </Card>
    </div>
  );
}
