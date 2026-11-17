import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { api } from "../../api/client";
import { fmtDay } from "../../utils/dates";
import { buildProfile, selectProfile, visiblePeriods, type ProfileModel, type ProfilePeriod } from "./resourceProfileModel";
import type { ViewportChannel } from "./viewport";

interface Props {
  visible: boolean;
  projectId: number;
  scheduleVersion: number;
  period: ProfilePeriod;
  tableWidth: number;
  viewport: ViewportChannel;
}
const number = new Intl.NumberFormat("es-CL", { maximumFractionDigits: 1 });

export default function ResourceProfile({ visible, projectId, scheduleVersion, period, tableWidth, viewport }: Props) {
  const [group, setGroup] = useState<"skill" | "resource">("skill");
  const [height, setHeight] = useState(180);
  const [maxHeight, setMaxHeight] = useState(600);
  const [result, setResult] = useState<{ key: string; model: ProfileModel } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const choices = useRef<Partial<Record<"skill" | "resource", Set<string>>>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const requestKey = `${projectId}:${scheduleVersion}:${group}:${period}:${retry}`;
  const model = result?.key === requestKey ? result.model : null;
  const [tip, setTip] = useState<{ index: number; x: number; y: number } | null>(null);
  const panel = useRef<HTMLElement>(null);
  const chart = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const frame = useRef(0);
  const drawRef = useRef<() => void>(() => {});
  const pointer = useRef<{ y: number; height: number; id: number } | null>(null);
  const schedule = useCallback(() => {
    if (!frame.current) frame.current = requestAnimationFrame(() => {
      frame.current = 0; drawRef.current();
    });
  }, []);

  useEffect(() => {
    if (!visible) { setResult(null); setTip(null); return; }
    let active = true;
    setLoading(true); setError(""); setResult(null); setTip(null);
    api.load(projectId, group, period).then(response => {
      if (!active) return;
      const next = buildProfile(response, period);
      const saved = choices.current[group];
      const keys = new Set(saved === undefined ? next.series.slice(0, 5).map(s => s.key)
        : next.series.filter(s => saved.has(s.key)).map(s => s.key));
      choices.current[group] = keys;
      setSelected(keys); setResult({ key: requestKey, model: next }); setLoading(false);
    }).catch((cause: unknown) => {
      if (active) { setError(cause instanceof Error ? cause.message : "No se pudo cargar el perfil."); setLoading(false); }
    });
    return () => { active = false; };
  }, [visible, projectId, scheduleVersion, group, period, retry, requestKey]);

  const data = useMemo(() => model ? selectProfile(model, selected) : null, [model, selected]);
  useLayoutEffect(() => {
    drawRef.current = () => {
      const cv = canvas.current, host = chart.current, v = viewport.get();
      if (!visible || !cv || !host) return;
      const width = Math.min(v?.width ?? 0, host.clientWidth), h = host.clientHeight;
      const dpr = window.devicePixelRatio || 1;
      const pixelWidth = Math.round(width * dpr), pixelHeight = Math.round(h * dpr);
      if (cv.width !== pixelWidth || cv.height !== pixelHeight) { cv.width = pixelWidth; cv.height = pixelHeight; }
      cv.style.width = `${width}px`; cv.style.height = `${h}px`;
      const ctx = cv.getContext("2d");
      if (!ctx) return;
      const styles = getComputedStyle(document.documentElement);
      const color = (key: string) => styles.getPropertyValue(key).trim();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = color("--surface"); ctx.fillRect(0, 0, width, h);
      // Do not show a previous aggregation while crossing the day/week threshold.
      if (!v || !model || !data || (v.dayWidth >= 4 ? "day" : "week") !== period || !width) return;
      const bottom = Math.max(24, h - 20), top = 20;
      const y = (value: number) => bottom - value / (data.max * 1.1) * (bottom - top);
      const x = (day: number) => (day - v.originDay) * v.dayWidth - v.scrollX;
      const [first, end] = visiblePeriods(model, v.originDay + v.scrollX / v.dayWidth,
        v.originDay + (v.scrollX + width) / v.dayWidth);
      ctx.font = "11px Segoe UI, system-ui, sans-serif";
      ctx.strokeStyle = color("--grid"); ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const gy = y(data.max * i / 4);
        ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(width, gy); ctx.stroke();
      }
      const colors = data.series.map(s => color(`--s${s.color}`));
      for (let i = first; i < end; i++) {
        const left = x(model.days[i]);
        const right = x(model.days[i] + model.span);
        const barWidth = Math.max(0.5, right - left - Math.min(1, (right - left) * 0.15));
        let sum = 0;
        for (let j = 0; j < data.series.length; j++) {
          const value = data.series[j].load[i];
          ctx.fillStyle = colors[j];
          ctx.fillRect(left, y(sum + value), barWidth, y(sum) - y(sum + value));
          sum += value;
        }
        if (sum > data.capacity[i]) {
          ctx.fillStyle = color("--critical");
          ctx.fillRect(left, y(sum), barWidth, y(data.capacity[i]) - y(sum));
        }
      }
      ctx.strokeStyle = color("--text"); ctx.lineWidth = 1.5; ctx.setLineDash([5, 3]);
      ctx.beginPath();
      for (let i = first; i < end; i++) {
        const left = x(model.days[i]), right = x(model.days[i] + model.span), cy = y(data.capacity[i]);
        if (i > first && model.days[i - 1] + model.span === model.days[i]) ctx.lineTo(left, cy);
        else ctx.moveTo(left, cy);
        ctx.lineTo(right, cy);
      }
      ctx.stroke(); ctx.setLineDash([]);
      // Overlay labels rather than adding a left axis margin: date x positions must stay exact.
      ctx.fillStyle = color("--surface"); ctx.fillRect(0, 0, Math.min(width, 170), 17);
      ctx.fillStyle = color("--text-2");
      ctx.fillText(`Máx. ${number.format(data.max)} HH · ${period === "day" ? "día" : "semana"}`, 5, 12);
      ctx.fillText("0", 5, h - 5);
    };
    if (visible) schedule();
  }, [visible, viewport, model, data, period, schedule]);

  useEffect(() => {
    if (!visible) return;
    // Gantt publishes inside its RAF: draw in that same frame, without one-frame lag.
    const unsubscribe = viewport.subscribe(() => { setTip(old => old === null ? old : null); drawRef.current(); });
    const ro = new ResizeObserver(() => { setTip(null); schedule(); });
    if (chart.current) ro.observe(chart.current);
    const mo = new MutationObserver(schedule);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", schedule);
    schedule();
    return () => {
      unsubscribe(); ro.disconnect(); mo.disconnect(); mq.removeEventListener("change", schedule);
      cancelAnimationFrame(frame.current); frame.current = 0;
    };
  }, [visible, viewport, schedule]);

  useLayoutEffect(() => {
    const parent = panel.current?.parentElement;
    if (!parent) return;
    const update = () => setMaxHeight(Math.max(100, parent.clientHeight - 100));
    const ro = new ResizeObserver(update); ro.observe(parent); update();
    return () => ro.disconnect();
  }, []);
  const actualHeight = Math.min(height, maxHeight);
  const resize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault(); event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId);
    pointer.current = { y: event.clientY, height: actualHeight, id: event.pointerId };
  };
  const clearTip = () => setTip(null);

  return <section id="gantt-resource-profile" className="resource-profile" aria-label="Perfil de recursos"
    hidden={!visible} style={{ height: actualHeight }} ref={panel}>
    <div className="resource-profile-resizer" role="separator" tabIndex={0} aria-label="Altura del perfil de recursos"
      aria-orientation="horizontal" aria-valuemin={100} aria-valuemax={maxHeight} aria-valuenow={actualHeight}
      onPointerDown={resize} onPointerMove={event => {
        const start = pointer.current;
        if (start && start.id === event.pointerId) setHeight(Math.max(100, Math.min(maxHeight, start.height + start.y - event.clientY)));
      }} onPointerUp={event => { pointer.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }}
      onPointerCancel={() => { pointer.current = null; }} onLostPointerCapture={() => { pointer.current = null; }}
      onKeyDown={event => {
        if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        setHeight(event.key === "Home" ? 100 : event.key === "End" ? maxHeight
          : Math.max(100, Math.min(maxHeight, actualHeight + (event.key === "ArrowUp" ? 20 : -20))));
      }} />
    <div className="resource-profile-body">
      <div className="resource-profile-sidebar" style={{ width: tableWidth }}>
        <div className="resource-profile-controls">
          <label>Perfil de recursos <select className="inp" value={group} aria-label="Agrupar perfil por"
            onChange={event => { setGroup(event.target.value as typeof group); clearTip(); }}>
            <option value="skill">Habilidad</option><option value="resource">Recurso</option>
          </select></label>
          <span className="small muted">HH · {period === "day" ? "diario" : "semanal"} · todo el proyecto</span>
        </div>
        <div className="resource-profile-series" aria-label="Series del perfil">
          {loading && <div role="status">Cargando perfil…</div>}
          {error && <div role="alert">{error} <button className="btn small" onClick={() => setRetry(v => v + 1)}>Reintentar</button></div>}
          {model?.series.length === 0 && <div className="muted">No hay series de recursos.</div>}
          {model?.series.map(s => <label className="resource-profile-series-row" key={s.key}>
            <input type="checkbox" checked={selected.has(s.key)} onChange={event => {
              const next = new Set(selected);
              if (event.target.checked) next.add(s.key); else next.delete(s.key);
              choices.current[group] = next; setSelected(next); clearTip();
            }} />
            <i style={{ background: `var(--s${s.color})` }} /><span title={s.key}>{s.key || "Sin nombre"}</span>
            <span>{number.format(s.total)} HH</span>
          </label>)}
        </div>
        <div className="resource-profile-legend"><span className="capacity-key" />Capacidad total seleccionada
          <span className="overload-key" />Exceso</div>
      </div>
      <div className="resource-profile-divider" />
      <div className="resource-profile-chart" ref={chart} onMouseLeave={event => {
        if (!(event.relatedTarget instanceof Element) || !event.relatedTarget.closest(".resource-profile-tip")) clearTip();
      }}>
        <canvas ref={canvas} role="img" aria-label="Carga apilada en HH; línea discontinua de capacidad seleccionada y exceso en rojo"
          onMouseMove={event => {
            const v = viewport.get();
            if (!model || !data || !v) return;
            const rect = event.currentTarget.getBoundingClientRect();
            const day = v.originDay + (event.clientX - rect.left + v.scrollX) / v.dayWidth;
            const [index] = visiblePeriods(model, day, day + 0.00001);
            if (index >= model.days.length || model.days[index] > day || !data.series.length) { clearTip(); return; }
            setTip({ index, x: Math.max(4, Math.min(event.clientX + 12, window.innerWidth - 330)),
              y: Math.max(4, Math.min(event.clientY + 12, window.innerHeight - 260)) });
          }} />
        {model && (!model.days.length || !data?.series.length) && <div className="resource-profile-empty">
          {model.days.length ? "Selecciona una serie para mostrar su carga." : "No hay períodos de carga."}
        </div>}
      </div>
    </div>
    {tip && model && data && <div className="gantt-tip resource-profile-tip" role="tooltip" style={{ left: tip.x, top: tip.y }} onMouseLeave={clearTip}>
      <b>{fmtDay(model.days[tip.index])}{model.span > 1 ? ` → ${fmtDay(model.days[tip.index] + model.span - 1)}` : ""}</b>
      <table><tbody>
        {data.series.map(s => <tr key={s.key}><td>{s.key}</td><td>{number.format(s.load[tip.index])} HH</td></tr>)}
        <tr><td>Carga seleccionada</td><td>{number.format(data.load[tip.index])} HH</td></tr>
        <tr><td>Capacidad seleccionada</td><td>{number.format(data.capacity[tip.index])} HH</td></tr>
        <tr><td>Utilización</td><td>{data.capacity[tip.index] > 0
          ? `${number.format(100 * data.load[tip.index] / data.capacity[tip.index])} %`
          : data.load[tip.index] > 0 ? "∞ (sin capacidad)" : "— (sin capacidad)"}</td></tr>
      </tbody></table>
    </div>}
  </section>;
}
