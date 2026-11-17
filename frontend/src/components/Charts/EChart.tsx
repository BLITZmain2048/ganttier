import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { useStore } from "../../state/store";

export type EOption = echarts.EChartsOption;

/** Lee tokens de color CSS para que los gráficos sigan el tema claro/oscuro. */
export function chartTokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    text: v("--text"), text2: v("--text-2"), text3: v("--text-3"), grid: v("--grid"), border: v("--border"),
    surface: v("--surface"),
    s: [v("--s1"), v("--s2"), v("--s3"), v("--s4"), v("--s5"), v("--s6"), v("--s7"), v("--s8")],
    good: v("--good"), warning: v("--warning"), critical: v("--critical"), datadate: v("--datadate"),
  };
}

export function baseOption(): EOption {
  const t = chartTokens();
  return {
    textStyle: { fontFamily: "Segoe UI, system-ui, sans-serif", color: t.text2 },
    grid: { left: 56, right: 24, top: 36, bottom: 56, containLabel: false },
    tooltip: {
      trigger: "axis", backgroundColor: t.surface, borderColor: t.border, textStyle: { color: t.text },
      axisPointer: { type: "line", lineStyle: { color: t.text3 } },
    },
    legend: { top: 4, textStyle: { color: t.text2 }, icon: "roundRect", itemWidth: 14, itemHeight: 8 },
  };
}

export default function EChart({ option, height = 320 }: { option: EOption; height?: number | string }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const { theme } = useStore();

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.current?.dispose(); chart.current = null; };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option, theme]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
