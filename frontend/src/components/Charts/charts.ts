import type { LoadProfile, SCurve, WorkerKPI } from "../../api/types";
import { baseOption, chartTokens, type EOption } from "./EChart";
import { fmtDate } from "../../utils/dates";

/** Histograma de carga apilado por serie con línea de capacidad total. */
export function loadOption(profile: LoadProfile | null, title: string, maxSeries = 7): EOption {
  const t = chartTokens();
  const base = baseOption();
  if (!profile || profile.labels.length === 0) return { ...base, title: { text: "Sin datos de carga", left: "center", top: "middle", textStyle: { color: t.text3, fontSize: 13 } } };
  // series ordenadas por carga total; las que exceden el máximo se agrupan en "Otros"
  const totals = profile.series.map((s) => s.load.reduce((a, b) => a + b, 0));
  const order = profile.series.map((_, i) => i).sort((a, b) => totals[b] - totals[a]);
  const keep = order.slice(0, maxSeries);
  const rest = order.slice(maxSeries);
  const n = profile.labels.length;
  const series: EOption["series"] = [];
  const stable = [...keep].sort((a, b) => profile.series[a].key.localeCompare(profile.series[b].key));
  stable.forEach((si, k) => {
    series.push({
      name: profile.series[si].key, type: "bar", stack: "carga", barCategoryGap: "20%",
      itemStyle: { color: t.s[k % 8], borderColor: t.surface, borderWidth: 1 },
      data: profile.series[si].load.map((v) => Math.round(v * 10) / 10),
    });
  });
  if (rest.length) {
    const other = new Array<number>(n).fill(0);
    for (const si of rest) profile.series[si].load.forEach((v, i) => { other[i] += v; });
    series.push({ name: "Otros", type: "bar", stack: "carga", itemStyle: { color: t.text3, borderColor: t.surface, borderWidth: 1 }, data: other.map((v) => Math.round(v * 10) / 10) });
  }
  const cap = new Array<number>(n).fill(0);
  for (const s of profile.series) s.capacity.forEach((v, i) => { cap[i] += v; });
  series.push({
    name: "Capacidad", type: "line", step: "middle", symbol: "none", data: cap.map((v) => Math.round(v * 10) / 10),
    lineStyle: { color: t.text, width: 1.5, type: "dashed" }, itemStyle: { color: t.text }, z: 5,
  });
  return {
    ...base,
    title: { text: title, left: 0, top: 0, textStyle: { fontSize: 13, color: t.text, fontWeight: 600 } },
    legend: { ...(base.legend as object), top: 22, type: "scroll" },
    grid: { left: 56, right: 18, top: 58, bottom: 64 },
    xAxis: { type: "category", data: profile.labels.map(fmtDate), axisLabel: { color: t.text3 }, axisLine: { lineStyle: { color: t.border } } },
    yAxis: { type: "value", name: "HH", nameTextStyle: { color: t.text3 }, splitLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.text3 } },
    dataZoom: n > 40 ? [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }] : undefined,
    series,
  };
}

export function scurveOption(sc: SCurve | null, abs: boolean): EOption {
  const t = chartTokens();
  const base = baseOption();
  if (!sc || sc.labels.length === 0) return { ...base, title: { text: "Sin datos para la curva S", left: "center", top: "middle", textStyle: { color: t.text3, fontSize: 13 } } };
  const pick = (a: (number | null)[], b: (number | null)[]) => (abs ? b : a).map((v) => (v === null || v === undefined ? null : Math.round(v * 100) / 100));
  const labels = sc.labels.map(fmtDate);
  const ddIdx = sc.data_date ? sc.labels.findIndex((l) => l >= sc.data_date!) : -1;
  const unit = abs ? (sc.basis === "cost" ? "Costo" : sc.basis === "count" ? "N°" : "HH") : "%";
  return {
    ...base,
    grid: { left: 60, right: 24, top: 40, bottom: 60 },
    xAxis: { type: "category", data: labels, boundaryGap: false, axisLabel: { color: t.text3 }, axisLine: { lineStyle: { color: t.border } } },
    yAxis: { type: "value", name: unit, max: abs ? undefined : (value: { max: number }) => Math.max(100, Math.ceil(value.max / 10) * 10), min: 0, splitLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.text3 } },
    dataZoom: labels.length > 60 ? [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }] : undefined,
    series: [
      {
        name: "Planificada", type: "line", symbol: "none", smooth: false, data: pick(sc.planned_cum, sc.planned_abs),
        lineStyle: { width: 2, color: t.s[0] }, itemStyle: { color: t.s[0] },
        markLine: ddIdx >= 0 ? {
          symbol: "none", silent: true, label: { formatter: "Fecha de corte", color: t.datadate },
          lineStyle: { color: t.datadate, type: "solid", width: 1.5 }, data: [{ xAxis: labels[ddIdx] }],
        } : undefined,
      },
      { name: "Ganada (avance)", type: "line", symbol: "none", data: pick(sc.earned_cum, sc.earned_abs), lineStyle: { width: 2, color: t.s[1] }, itemStyle: { color: t.s[1] } },
      { name: "Real (HH/costo)", type: "line", symbol: "none", data: pick(sc.actual_cum, sc.actual_abs), lineStyle: { width: 2, color: t.s[2] }, itemStyle: { color: t.s[2] } },
      { name: "Proyección", type: "line", symbol: "none", data: pick(sc.forecast_cum, sc.forecast_abs), lineStyle: { width: 2, color: t.s[6], type: "dashed" }, itemStyle: { color: t.s[6] } },
    ],
  };
}

export function performanceOption(k: WorkerKPI[], metric: "compliance_pct" | "hh_efficiency" | "avg_delay_days" | "SPI"): EOption {
  const t = chartTokens();
  const base = baseOption();
  const rows = [...k].filter((x) => x[metric] !== null).sort((a, b) => (b[metric] as number) - (a[metric] as number)).slice(0, 40);
  const labels: Record<string, string> = { compliance_pct: "Cumplimiento %", hh_efficiency: "Eficiencia HH", avg_delay_days: "Atraso promedio (d)", SPI: "SPI" };
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), trigger: "axis", axisPointer: { type: "shadow" } },
    legend: undefined,
    grid: { left: 150, right: 30, top: 16, bottom: 30 },
    xAxis: { type: "value", name: labels[metric], nameLocation: "middle", nameGap: 22, splitLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.text3 } },
    yAxis: { type: "category", inverse: true, data: rows.map((r) => r.name), axisLabel: { color: t.text2 } },
    series: [{
      type: "bar", name: labels[metric], data: rows.map((r) => Math.round((r[metric] as number) * 100) / 100),
      itemStyle: { color: t.s[0], borderRadius: [0, 4, 4, 0] }, barMaxWidth: 16,
      label: { show: rows.length <= 25, position: "right", color: t.text2, fontSize: 11 },
    }],
  };
}
