import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ActArrays, RelArrays, RowModel } from "./model";
import type { GanttViewport } from "./viewport";
import { dayParts, fmtDay, isoWeek, MONTHS, WEEKDAYS, todayDay } from "../../utils/dates";

export interface ColumnDef {
  key: string;
  label: string;
  width: number;
  align?: "left" | "right" | "center";
  get: (a: ActArrays, i: number) => string;
  /** Tipo de edición en línea. */
  edit?: "text" | "number" | "date" | "month";
  /** Valor inicial para edición (si difiere del texto mostrado). */
  editValue?: (a: ActArrays, i: number) => string;
}

export interface GanttProps {
  a: ActArrays;
  rows: RowModel;
  rels: RelArrays;
  columns: ColumnDef[];
  dayWidth: number;
  onDayWidth: (w: number) => void;
  dataDay: number;
  isNonWorking?: (day: number) => 0 | 1 | 2; // 0 laborable, 1 fin de semana, 2 feriado
  selected: number;
  onSelect: (actIndex: number) => void;
  onToggleWbs: (wbsId: number) => void;
  collapsed: Set<number>;
  onEditCell?: (actIndex: number, key: string, value: string) => void;
  onMove?: (actIndex: number, deltaDays: number) => void;
  onResize?: (actIndex: number, newFinishDay: number) => void;
  onLink?: (predIndex: number, succIndex: number) => void;
  jump?: { day: number; nonce: number } | null;
  tableWidth: number;
  onTableWidth: (w: number) => void;
  showBaseline: boolean;
  showWindows: boolean;
  showRelations: boolean;
  readOnly?: boolean;
  onViewport?: (viewport: GanttViewport) => void;
}

const ROW_H = 24;
const HEAD_H = 44;
const SCROLL = 14;
const PAD_DAYS = 45;

interface Palette {
  surface: string; surface2: string; grid: string; text: string; text2: string; text3: string; weekend: string;
  holiday: string; bar: string; crit: string; base: string; win: string; winHard: string; prog: string;
  milestone: string; datadate: string; today: string; accentSoft: string; border: string; accent: string;
}

function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    surface: v("--surface"), surface2: v("--surface-2"), grid: v("--grid"), text: v("--text"), text2: v("--text-2"),
    text3: v("--text-3"), weekend: v("--weekend"), holiday: v("--holiday"), bar: v("--bar"), crit: v("--bar-crit"),
    base: v("--bar-base"), win: v("--bar-window"), winHard: v("--bar-window-hard"), prog: v("--bar-progress"),
    milestone: v("--milestone"), datadate: v("--datadate"), today: v("--today"), accentSoft: v("--accent-soft"),
    border: v("--border"), accent: v("--accent-2"),
  };
}

type DragMode = "move" | "resize" | "link";
interface DragState {
  mode: DragMode;
  idx: number;
  startX: number;
  curX: number;
  curY: number;
  origStart: number;
  origFinish: number;
  targetIdx: number;
}

export default function GanttView(p: GanttProps) {
  const { a, rows, rels, columns, dayWidth } = p;
  const rootRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const vRef = useRef<HTMLDivElement>(null);
  const hRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 500 });
  const scrollY = useRef(0);
  const [firstRow, setFirstRow] = useState(0);
  const tableLayer = useRef<HTMLDivElement>(null);
  const stopGesture = useRef<(() => void) | null>(null);
  const editDone = useRef(false);
  const scrollX = useRef(0);
  const drag = useRef<DragState | null>(null);
  const hover = useRef<{ idx: number; x: number; y: number } | null>(null);
  const [tip, setTip] = useState<{ idx: number; x: number; y: number } | null>(null);
  const [colW, setColW] = useState<number[]>(() => columns.map((c) => c.width));
  const [editing, setEditing] = useState<{ idx: number; key: string; value: string } | null>(null);
  const raf = useRef(0);
  const drawRef = useRef<() => void>(() => {});
  const schedule = useCallback(() => {
    if (raf.current) return;
    raf.current = requestAnimationFrame(() => { raf.current = 0; drawRef.current(); });
  }, []);
  useEffect(() => () => { cancelAnimationFrame(raf.current); raf.current = 0; stopGesture.current?.(); }, []);
  const palRef = useRef<Palette | null>(null);

  useEffect(() => { setColW(columns.map((c) => c.width)); }, [columns]);

  const originDay = a.minDay - PAD_DAYS;
  const totalDays = a.maxDay - a.minDay + PAD_DAYS * 2 + 1;
  const bodyH = Math.max(0, size.h - HEAD_H - SCROLL);
  const chartW = Math.max(0, size.w - SCROLL);
  const totalH = rows.rows.length * ROW_H;

  // --- tamaño
  useLayoutEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setSize({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // --- tema
  useEffect(() => {
    palRef.current = readPalette();
    const mo = new MutationObserver(() => { palRef.current = readPalette(); schedule(); });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onMq = () => { palRef.current = readPalette(); schedule(); };
    mq.addEventListener("change", onMq);
    return () => { mo.disconnect(); mq.removeEventListener("change", onMq); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const xOf = useCallback((day: number) => (day - originDay) * dayWidth - scrollX.current, [originDay, dayWidth]);

  // --- dibujo
  const draw = useCallback(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const pal = palRef.current ?? readPalette();
    palRef.current = pal;
    const dpr = window.devicePixelRatio || 1;
    const W = chartW, H = Math.max(0, size.h - SCROLL);
    p.onViewport?.({ originDay, dayWidth, scrollX: scrollX.current, width: W });
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      cv.style.width = `${W}px`; cv.style.height = `${H}px`;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = pal.surface;
    ctx.fillRect(0, 0, W, H);
    const dw = dayWidth;
    const sx = scrollX.current;
    const d0 = Math.floor(originDay + sx / dw) - 1;
    const d1 = Math.ceil(originDay + (sx + W) / dw) + 1;
    const X = (day: number) => (day - originDay) * dw - sx;

    // sombreado no laborable
    if (p.isNonWorking && dw >= 4) {
      for (let d = d0; d <= d1; d++) {
        const k = p.isNonWorking(d);
        if (k) {
          ctx.fillStyle = k === 2 ? pal.holiday : pal.weekend;
          ctx.fillRect(X(d), HEAD_H, dw, H - HEAD_H);
        }
      }
    }

    // encabezado
    ctx.fillStyle = pal.surface2;
    ctx.fillRect(0, 0, W, HEAD_H);
    ctx.strokeStyle = pal.border;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, HEAD_H - 0.5); ctx.lineTo(W, HEAD_H - 0.5); ctx.moveTo(0, 22.5); ctx.lineTo(W, 22.5); ctx.stroke();
    ctx.font = "11.5px Segoe UI, system-ui, sans-serif";
    ctx.textBaseline = "middle";
    type Tier = { start: number; end: number; label: string }[];
    const top: Tier = [], bot: Tier = [];
    const pushMonths = (tier: Tier, withYear: boolean, short: boolean) => {
      let p0 = dayParts(d0); let ms = Math.floor(Date.UTC(p0.y, p0.m, 1) / 86400000);
      while (ms <= d1) {
        const q = dayParts(ms);
        const next = Math.floor(Date.UTC(q.y, q.m + 1, 1) / 86400000);
        const lbl = short ? MONTHS[q.m].slice(0, 1).toUpperCase() : withYear ? `${MONTHS[q.m]} ${q.y}` : MONTHS[q.m];
        tier.push({ start: ms, end: next, label: lbl });
        ms = next;
      }
    };
    const pushYears = (tier: Tier) => {
      let y = dayParts(d0).y;
      while (true) {
        const s = Math.floor(Date.UTC(y, 0, 1) / 86400000);
        if (s > d1) break;
        tier.push({ start: s, end: Math.floor(Date.UTC(y + 1, 0, 1) / 86400000), label: String(y) });
        y++;
      }
    };
    if (dw >= 14) {
      pushMonths(top, true, false);
      for (let d = d0; d <= d1; d++) {
        const q = dayParts(d);
        bot.push({ start: d, end: d + 1, label: dw >= 26 ? `${WEEKDAYS[q.wd].slice(0, 2)} ${q.d}` : String(q.d) });
      }
    } else if (dw >= 3.2) {
      pushMonths(top, true, false);
      let w = d0 - dayParts(d0).wd;
      while (w <= d1) { bot.push({ start: w, end: w + 7, label: dw >= 5 ? `S${isoWeek(w)}` : "" }); w += 7; }
    } else if (dw >= 0.9) {
      pushYears(top);
      pushMonths(bot, false, dw < 1.6);
    } else {
      pushYears(top);
      let y = dayParts(d0).y;
      while (true) {
        let done = false;
        for (let q = 0; q < 4; q++) {
          const s = Math.floor(Date.UTC(y, q * 3, 1) / 86400000);
          if (s > d1) { done = true; break; }
          bot.push({ start: s, end: Math.floor(Date.UTC(y, q * 3 + 3, 1) / 86400000), label: `T${q + 1}` });
        }
        if (done) break;
        y++;
      }
    }
    const drawTier = (tier: Tier, y0: number, h: number, grid: boolean) => {
      for (const t of tier) {
        const xa = X(t.start), xb = X(t.end);
        ctx.strokeStyle = pal.border;
        ctx.beginPath(); ctx.moveTo(Math.round(xa) + 0.5, y0); ctx.lineTo(Math.round(xa) + 0.5, y0 + h); ctx.stroke();
        if (grid) {
          ctx.strokeStyle = pal.grid;
          ctx.beginPath(); ctx.moveTo(Math.round(xa) + 0.5, HEAD_H); ctx.lineTo(Math.round(xa) + 0.5, H); ctx.stroke();
        }
        const wpx = xb - xa;
        if (wpx > 14 && t.label) {
          ctx.fillStyle = pal.text2;
          const tw = ctx.measureText(t.label).width;
          // etiqueta centrada en la parte visible de la celda; se omite si no cabe
          const va = Math.max(xa, 0), vb = Math.min(xb, W);
          if (tw < vb - va - 6) {
            const cx = Math.max(va + 3, Math.min(va + (vb - va) / 2 - tw / 2, vb - tw - 3));
            ctx.fillText(t.label, cx, y0 + h / 2);
          }
        }
      }
    };
    drawTier(top, 0, 22, false);
    drawTier(bot, 22, 22, dw >= 3.2 || dw >= 0.9);

    // filas
    ctx.save();
    ctx.beginPath(); ctx.rect(0, HEAD_H, W, H - HEAD_H); ctx.clip();
    const st = scrollY.current;
    const r0 = Math.max(0, Math.floor(st / ROW_H));
    const r1 = Math.min(rows.rows.length - 1, Math.ceil((st + bodyH) / ROW_H));
    const yOf = (r: number) => HEAD_H + r * ROW_H - st;
    ctx.strokeStyle = pal.grid;
    ctx.beginPath();
    for (let r = r0; r <= r1 + 1; r++) { const y = Math.round(yOf(r)) + 0.5; ctx.moveTo(0, y); ctx.lineTo(W, y); }
    ctx.stroke();

    const sel = p.selected;
    ctx.font = "11.5px Segoe UI, system-ui, sans-serif";
    for (let r = r0; r <= r1; r++) {
      const idx = rows.rows[r];
      const y = yOf(r);
      if (idx < 0) {
        const w = rows.wbsRows[-idx - 1];
        if (!Number.isFinite(w.start)) continue;
        const xa = X(w.start), xb = X(w.finish + 1);
        ctx.fillStyle = pal.text;
        ctx.fillRect(xa, y + 9, Math.max(2, xb - xa), 5);
        ctx.beginPath(); ctx.moveTo(xa, y + 9); ctx.lineTo(xa + 5, y + 9); ctx.lineTo(xa, y + 18); ctx.fill();
        ctx.beginPath(); ctx.moveTo(xb, y + 9); ctx.lineTo(xb - 5, y + 9); ctx.lineTo(xb, y + 18); ctx.fill();
        continue;
      }
      if (idx === sel) { ctx.fillStyle = pal.accentSoft; ctx.globalAlpha = 0.6; ctx.fillRect(0, y, W, ROW_H); ctx.globalAlpha = 1; }
      // ventana macro
      if (p.showWindows && a.ws[idx] === a.ws[idx] && a.we[idx] === a.we[idx]) {
        ctx.fillStyle = a.windowHard[idx] ? pal.winHard : pal.win;
        const xa = X(a.ws[idx]), xb = X(a.we[idx] + 1);
        if (xb > 0 && xa < W) ctx.fillRect(xa, y + 2, xb - xa, ROW_H - 4);
      }
      // línea base
      if (p.showBaseline && a.bs[idx] === a.bs[idx]) {
        const xa = X(a.bs[idx]), xb = X((a.bf[idx] === a.bf[idx] ? a.bf[idx] : a.bs[idx]) + 1);
        if (xb > 0 && xa < W) { ctx.fillStyle = pal.base; ctx.fillRect(xa, y + 17, Math.max(2, xb - xa), 3); }
      }
      let s = a.start[idx], f = a.finish[idx];
      const dg = drag.current;
      if (dg && dg.idx === idx) {
        const dd = Math.round((dg.curX - dg.startX) / dw);
        if (dg.mode === "move") { s += dd; f += dd; }
        else if (dg.mode === "resize") { f = Math.max(s, f + dd); }
      }
      if (s !== s) continue;
      if (f !== f) f = s;
      const xa = X(s), xb = X(f + 1);
      if (xb < -200 || xa > W + 10) continue;
      if (a.milestone[idx]) {
        const cx = X(s), cy = y + ROW_H / 2;
        ctx.fillStyle = a.crit[idx] ? pal.crit : pal.milestone;
        ctx.beginPath(); ctx.moveTo(cx, cy - 6); ctx.lineTo(cx + 6, cy); ctx.lineTo(cx, cy + 6); ctx.lineTo(cx - 6, cy); ctx.closePath(); ctx.fill();
        if (dw * 1 >= 0 && xb + 8 < W) { ctx.fillStyle = pal.text2; ctx.fillText(a.names[idx], cx + 10, cy); }
        continue;
      }
      const bw = Math.max(2, xb - xa);
      ctx.fillStyle = a.crit[idx] ? pal.crit : pal.bar;
      const bh = p.showBaseline ? 11 : 13;
      const by = y + (p.showBaseline ? 5 : 5.5);
      if (bw > 6) {
        ctx.beginPath();
        const rr = 3;
        ctx.moveTo(xa + rr, by); ctx.lineTo(xa + bw - rr, by); ctx.quadraticCurveTo(xa + bw, by, xa + bw, by + rr);
        ctx.lineTo(xa + bw, by + bh - rr); ctx.quadraticCurveTo(xa + bw, by + bh, xa + bw - rr, by + bh);
        ctx.lineTo(xa + rr, by + bh); ctx.quadraticCurveTo(xa, by + bh, xa, by + bh - rr); ctx.lineTo(xa, by + rr);
        ctx.quadraticCurveTo(xa, by, xa + rr, by); ctx.fill();
      } else ctx.fillRect(xa, by, bw, bh);
      const pc = a.pct[idx];
      if (pc > 0) {
        ctx.fillStyle = pal.prog;
        ctx.globalAlpha = 0.75;
        ctx.fillRect(xa, by + bh - 4, bw * Math.min(100, pc) / 100, 4);
        ctx.globalAlpha = 1;
      }
      if (a.outage[idx]) {
        ctx.fillStyle = pal.datadate;
        ctx.fillRect(xa, by - 2, Math.min(bw, 6), 2);
      }
      if (dw >= 1.2 && xb + 6 < W) {
        ctx.fillStyle = pal.text2;
        ctx.fillText(a.names[idx], xb + 6, y + ROW_H / 2);
      }
      if (hover.current && hover.current.idx === idx && !p.readOnly && p.onLink) {
        ctx.strokeStyle = pal.accent; ctx.fillStyle = pal.surface; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(xb + 7, y + ROW_H / 2, 3.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); ctx.lineWidth = 1;
      }
    }

    // relaciones
    if (p.showRelations && rels.n > 0) {
      ctx.strokeStyle = pal.text3;
      ctx.fillStyle = pal.text3;
      ctx.lineWidth = 1;
      for (let row = r0; row <= r1; row++) {
        const act = rows.rows[row];
        if (act < 0) continue;
        for (let edge = rels.offsets[act]; edge < rels.offsets[act + 1]; edge++) {
          const k = rels.edges[edge];
          const pi = rels.pred[k], si = rels.succ[k];
          const rp = rows.rowOfAct[pi], rs = rows.rowOfAct[si];
          if (rp < 0 || rs < 0) continue;
          if (rs < r0 || rs > r1) continue;
          const t = rels.type[k];
          const fromEnd = t === 0 || t === 2; // FS, FF
          const toEnd = t === 2 || t === 3;   // FF, SF
          const ps = a.start[pi], pf = a.finish[pi] === a.finish[pi] ? a.finish[pi] : ps;
          const ss = a.start[si], sf = a.finish[si] === a.finish[si] ? a.finish[si] : ss;
          if (ps !== ps || ss !== ss) continue;
          const x1 = fromEnd && !a.milestone[pi] ? X(pf + 1) : X(ps);
          const x2 = toEnd && !a.milestone[si] ? X(sf + 1) : X(ss);
          const y1 = yOf(rp) + ROW_H / 2, y2 = yOf(rs) + ROW_H / 2;
          const dir1 = fromEnd ? 1 : -1;
          const dir2 = toEnd ? 1 : -1; // lado de llegada: desde la derecha si termina en fin
          const ex = x1 + dir1 * 6;
          const ax = x2 + dir2 * 6;
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(ex, y1);
          if ((dir2 < 0 && ax >= ex) || (dir2 > 0 && ax <= ex)) {
            ctx.lineTo(ex, y2); ctx.lineTo(x2, y2);
          } else {
            const ym = y2 + (y2 > y1 ? -ROW_H / 2 : ROW_H / 2);
            ctx.lineTo(ex, ym); ctx.lineTo(ax, ym); ctx.lineTo(ax, y2); ctx.lineTo(x2, y2);
          }
          ctx.stroke();
          // punta
          ctx.beginPath();
          if (dir2 < 0) { ctx.moveTo(x2, y2); ctx.lineTo(x2 - 5, y2 - 3.5); ctx.lineTo(x2 - 5, y2 + 3.5); }
          else { ctx.moveTo(x2, y2); ctx.lineTo(x2 + 5, y2 - 3.5); ctx.lineTo(x2 + 5, y2 + 3.5); }
          ctx.fill();
        }
      }
    }

    // línea de vínculo en arrastre
    const dg = drag.current;
    if (dg && dg.mode === "link") {
      const r = rows.rowOfAct[dg.idx];
      const x1 = X(a.milestone[dg.idx] ? a.start[dg.idx] : (Number.isFinite(a.finish[dg.idx]) ? a.finish[dg.idx] : a.start[dg.idx]) + 1) + 7;
      const y1 = yOf(r) + ROW_H / 2;
      ctx.strokeStyle = pal.accent; ctx.lineWidth = 2; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(dg.curX, dg.curY); ctx.stroke();
      ctx.setLineDash([]); ctx.lineWidth = 1;
    }
    ctx.restore();

    // fecha de corte y hoy
    const today = todayDay();
    const vline = (day: number, color: string, label: string, dashed: boolean) => {
      const x = Math.round(X(day)) + 0.5;
      if (x < 0 || x > W) return;
      ctx.strokeStyle = color; ctx.lineWidth = 1.5;
      if (dashed) ctx.setLineDash([5, 4]);
      ctx.beginPath(); ctx.moveTo(x, HEAD_H); ctx.lineTo(x, H); ctx.stroke();
      ctx.setLineDash([]); ctx.lineWidth = 1;
      ctx.fillStyle = color; ctx.font = "600 10.5px Segoe UI, system-ui, sans-serif";
      ctx.fillText(label, x + 3, HEAD_H + 8);
    };
    vline(today, pal.today, "Hoy", true);
    if (p.dataDay === p.dataDay) vline(p.dataDay, pal.datadate, "Corte", false);
  }, [a, rows, rels, dayWidth, originDay, chartW, size.h, bodyH, p.selected, p.isNonWorking, p.showBaseline,
    p.showWindows, p.showRelations, p.dataDay, p.onLink, p.readOnly, p.onViewport]);

  useLayoutEffect(() => { drawRef.current = draw; schedule(); }, [draw, schedule]);
  useLayoutEffect(() => {
    if (tableLayer.current) tableLayer.current.style.transform = `translateY(${firstRow * ROW_H - scrollY.current}px)`;
  });
  useEffect(() => {
    stopGesture.current?.(); drag.current = null; setEditing(null); setTip(null);
  }, [a, rows, dayWidth]);

  // --- sincronización de barras de desplazamiento
  useEffect(() => {
    const h = hRef.current;
    if (h && Math.abs(h.scrollLeft - scrollX.current) > 1) h.scrollLeft = scrollX.current;
    if (h) scrollX.current = h.scrollLeft;
    schedule();
  }, [dayWidth, totalDays, chartW, schedule]);

  const clampX = (x: number) => Math.max(0, Math.min(x, Math.max(0, totalDays * dayWidth - chartW)));

  // posición inicial: una semana antes de la primera actividad (una sola vez por montaje)
  const initialScrollDone = useRef(false);
  useEffect(() => {
    if (initialScrollDone.current || chartW <= 0) return;
    initialScrollDone.current = true;
    const x = clampX((PAD_DAYS - 7) * dayWidth);
    scrollX.current = x;
    if (hRef.current) hRef.current.scrollLeft = x;
    schedule();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartW]);

  useEffect(() => {
    if (!p.jump || !Number.isFinite(p.jump.day)) return;
    const x = clampX((p.jump.day - originDay) * dayWidth - chartW / 3);
    scrollX.current = x;
    if (hRef.current) hRef.current.scrollLeft = x;
    schedule();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.jump?.nonce]);

  // selección -> asegurar visible
  useEffect(() => {
    if (p.selected < 0) return;
    const r = rows.rowOfAct[p.selected];
    if (r < 0 || !vRef.current) return;
    const y = r * ROW_H;
    if (y < scrollY.current || y > scrollY.current + bodyH - ROW_H) vRef.current.scrollTop = Math.max(0, y - bodyH / 2);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.selected, rows, bodyH]);

  const onWheel = (e: React.WheelEvent) => {
    if (e.ctrlKey) {
      e.preventDefault();
      const rect = chartRef.current!.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const dayAt = originDay + (scrollX.current + mx) / dayWidth;
      const nw = Math.max(0.15, Math.min(60, dayWidth * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
      scrollX.current = Math.max(0, (dayAt - originDay) * nw - mx);
      p.onDayWidth(nw);
      return;
    }
    if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      if ((e.target as HTMLElement).closest(".gantt-table")) return;
      const d = e.shiftKey ? e.deltaY : e.deltaX;
      if (hRef.current) hRef.current.scrollLeft += d;
      return;
    }
    if (vRef.current) vRef.current.scrollTop += e.deltaY;
  };

  // ctrl+rueda sin desplazamiento de página
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const h = (e: WheelEvent) => { if (e.ctrlKey) e.preventDefault(); };
    el.addEventListener("wheel", h, { passive: false });
    return () => el.removeEventListener("wheel", h);
  }, []);

  // --- interacción con barras
  const hit = (mx: number, my: number): { idx: number; zone: "body" | "end" | "link" | "none"; row: number } => {
    if (my < HEAD_H) return { idx: -1, zone: "none", row: -1 };
    const row = Math.floor((my - HEAD_H + scrollY.current) / ROW_H);
    if (row < 0 || row >= rows.rows.length) return { idx: -1, zone: "none", row: -1 };
    const idx = rows.rows[row];
    if (idx < 0) return { idx: -1, zone: "none", row };
    const s = a.start[idx];
    if (s !== s) return { idx, zone: "none", row };
    const f = a.finish[idx] === a.finish[idx] ? a.finish[idx] : s;
    const xa = xOf(s), xb = xOf(f + 1);
    if (a.milestone[idx]) {
      if (Math.abs(mx - xa) <= 7) return { idx, zone: "body", row };
      if (mx > xa + 7 && mx < xa + 18) return { idx, zone: "link", row };
      return { idx, zone: "none", row };
    }
    if (mx >= xb + 2 && mx <= xb + 12) return { idx, zone: "link", row };
    if (mx >= xb - 5 && mx <= xb + 1) return { idx, zone: "end", row };
    if (mx >= xa && mx <= xb) return { idx, zone: "body", row };
    return { idx, zone: "none", row };
  };

  const onMouseDown = (e: React.MouseEvent) => {
    if (e.target !== canvasRef.current) return;
    e.preventDefault();
    stopGesture.current?.();
    const rect = chartRef.current!.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const h = hit(mx, my);
    if (h.row >= 0) {
      const idx = rows.rows[h.row];
      if (idx >= 0) p.onSelect(idx);
    }
    if (p.readOnly || h.idx < 0 || h.zone === "none" || e.button !== 0) return;
    if (h.zone === "link" && !p.onLink) return;
    if (h.zone === "end" && (!p.onResize || a.milestone[h.idx])) return;
    if (h.zone === "body" && !p.onMove) return;
    drag.current = {
      mode: h.zone === "body" ? "move" : h.zone === "end" ? "resize" : "link",
      idx: h.idx, startX: mx, curX: mx, curY: my, origStart: a.start[h.idx], origFinish: Number.isFinite(a.finish[h.idx]) ? a.finish[h.idx] : a.start[h.idx], targetIdx: -1,
    };
    setTip(null);
    const onMove = (ev: MouseEvent) => {
      const d = drag.current;
      if (!d) return;
      d.curX = ev.clientX - rect.left;
      d.curY = ev.clientY - rect.top;
      if (d.mode === "link") { const target = hit(d.curX, d.curY); d.targetIdx = target.zone === "none" ? -1 : target.idx; }
      schedule();
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      const d = drag.current;
      drag.current = null;
      if (!d) return;
      const dd = Math.round((d.curX - d.startX) / dayWidth);
      if (d.mode === "move" && dd !== 0) p.onMove?.(d.idx, dd);
      else if (d.mode === "resize" && dd !== 0) p.onResize?.(d.idx, Math.max(d.origStart, d.origFinish + dd));
      else if (d.mode === "link" && d.targetIdx >= 0 && d.targetIdx !== d.idx) p.onLink?.(d.idx, d.targetIdx);
      schedule();
    };
    stopGesture.current = () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); drag.current = null; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (drag.current) return;
    const rect = chartRef.current!.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const h = hit(mx, my);
    const el = chartRef.current!;
    el.style.cursor = p.readOnly ? "default" : h.zone === "end" ? "ew-resize" : h.zone === "body" ? "grab" : h.zone === "link" ? "crosshair" : "default";
    const prev = hover.current?.idx ?? -1;
    const idx = h.zone !== "none" ? h.idx : (h.idx >= 0 ? h.idx : -1);
    hover.current = idx >= 0 ? { idx, x: e.clientX, y: e.clientY } : null;
    if (idx !== prev) schedule();
    setTip(h.zone !== "none" && h.idx >= 0 ? { idx: h.idx, x: e.clientX, y: e.clientY } : null);
  };

  const onMouseLeave = () => { hover.current = null; setTip(null); schedule(); };

  // --- tabla
  const r0 = Math.max(0, firstRow);
  const r1 = Math.min(rows.rows.length - 1, firstRow + Math.ceil(bodyH / ROW_H) + 1);
  const tableRows: React.ReactNode[] = [];
  const totalColW = colW.reduce((s, w) => s + w, 0);
  for (let r = r0; r <= r1; r++) {
    const idx = rows.rows[r];
    const top = (r - firstRow) * ROW_H;
    const lv = Math.min(rows.level[r], 12);
    if (idx < 0) {
      const w = rows.wbsRows[-idx - 1];
      const coll = p.collapsed.has(w.node.id);
      tableRows.push(
        <div key={`w${w.node.id}`} className="gt-row wbs" style={{ top, height: ROW_H, width: totalColW }}
          role="button" tabIndex={0} aria-expanded={!coll}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); p.onToggleWbs(w.node.id); } }}
          onClick={() => p.onToggleWbs(w.node.id)}>
          <div style={{ width: totalColW, paddingLeft: 6 + lv * 14, cursor: "pointer" }}>
            <span style={{ width: 14, display: "inline-block" }}>{coll ? "▸" : "▾"}</span>
            {w.node.code} · {w.node.name}
            <span className="muted" style={{ marginLeft: 8, fontWeight: 400 }}>
              {w.count} act. · {fmtDay(w.start)} → {fmtDay(w.finish)} · {w.pct.toFixed(0)} %
            </span>
          </div>
        </div>,
      );
      continue;
    }
    tableRows.push(
      <div key={idx} className={`gt-row${idx === p.selected ? " sel" : ""}`} style={{ top, height: ROW_H, width: totalColW }}
        onMouseDown={() => p.onSelect(idx)}>
        {columns.map((c, ci) => {
          const isEd = editing && editing.idx === idx && editing.key === c.key;
          const txt = c.get(a, idx);
          return (
            <div key={c.key} className={c.align === "right" ? "num" : ""}
              style={{ width: colW[ci], paddingLeft: ci === 1 ? 6 + lv * 14 : undefined }}
              title={txt} tabIndex={c.edit && !p.readOnly ? 0 : undefined}
              aria-label={`${a.codes[idx]}: ${c.label}`}
              onKeyDown={(e) => { if (e.target === e.currentTarget && e.key === "Enter" && c.edit && !p.readOnly) { e.stopPropagation(); editDone.current = false; setEditing({ idx, key: c.key, value: c.editValue ? c.editValue(a, idx) : txt }); } }}
              onDoubleClick={() => {
                if (!c.edit || p.readOnly || !p.onEditCell) return;
                editDone.current = false;
                setEditing({ idx, key: c.key, value: c.editValue ? c.editValue(a, idx) : txt });
              }}>
              {isEd ? (
                <input aria-label={c.label} autoFocus value={editing!.value} type={c.edit === "date" ? "date" : c.edit === "month" ? "month" : "text"}
                  onChange={(e) => setEditing({ ...editing!, value: e.target.value })}
                  onBlur={() => { if (!editDone.current) { editDone.current = true; p.onEditCell?.(idx, c.key, editing!.value); } setEditing(null); }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.stopPropagation(); e.currentTarget.blur(); }
                    if (e.key === "Escape") { e.stopPropagation(); editDone.current = true; setEditing(null); }
                  }} />
              ) : (
                <span className={c.key === "tf" && a.crit[idx] ? "crit" : undefined} style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{txt}</span>
              )}
            </div>
          );
        })}
      </div>,
    );
  }

  const startColResize = (ci: number, e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    const x0 = e.clientX, w0 = colW[ci];
    const mv = (ev: MouseEvent) => setColW((cw) => cw.map((w, k) => (k === ci ? Math.max(30, w0 + ev.clientX - x0) : w)));
    const up = () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
    stopGesture.current?.(); stopGesture.current = up;
    window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
  };

  const startSplit = (e: React.MouseEvent) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = p.tableWidth;
    const mv = (ev: MouseEvent) => p.onTableWidth(Math.max(120, Math.min(window.innerWidth - 300, w0 + ev.clientX - x0)));
    const up = () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
    stopGesture.current?.(); stopGesture.current = up;
    window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
  };

  const tipNode = tip ? (() => {
    const i = tip.idx;
    const rowsInfo: [string, string][] = [
      ["Inicio", fmtDay(a.start[i])], ["Término", fmtDay(a.finish[i])],
    ];
    if (a.dur[i] === a.dur[i]) rowsInfo.push(["Duración", `${a.dur[i]} d`]);
    if (a.hh[i] === a.hh[i]) rowsInfo.push(["HH", a.hh[i].toLocaleString("es-CL")]);
    rowsInfo.push(["Avance", `${a.pct[i].toFixed(0)} %`]);
    if (a.tf[i] === a.tf[i]) rowsInfo.push(["Holgura total", `${a.tf[i]} d${a.crit[i] ? " (crítica)" : ""}`]);
    if (a.bs[i] === a.bs[i]) rowsInfo.push(["Línea base", `${fmtDay(a.bs[i])} → ${fmtDay(a.bf[i])}`]);
    if (a.ws[i] === a.ws[i]) rowsInfo.push(["Ventana", `${fmtDay(a.ws[i])} → ${fmtDay(a.we[i])}`]);
    if (a.skill[i]) rowsInfo.push(["Habilidad", a.skill[i]]);
    if (a.location[i]) rowsInfo.push(["Ubicación", a.location[i]]);
    if (a.outage[i]) rowsInfo.push(["Desconexión", "Requiere"]);
    const left = Math.min(tip.x + 14, window.innerWidth - 370);
    return (
      <div className="gantt-tip" style={{ left, top: tip.y + 14 }}>
        <b>{a.codes[i]} · {a.names[i]}</b>
        <table><tbody>{rowsInfo.map(([k, v]) => <tr key={k}><td className="muted">{k}</td><td>{v}</td></tr>)}</tbody></table>
      </div>
    );
  })() : null;

  return (
    <div className="gantt-root" ref={rootRef} onWheel={onWheel}>
      <div className="gantt-table" style={{ width: p.tableWidth }}>
        <div className="gt-head" style={{ height: HEAD_H, width: totalColW }}>
          {columns.map((c, ci) => (
            <div key={c.key} style={{ width: colW[ci], justifyContent: c.align === "right" ? "flex-end" : undefined }}>
              {c.label}
              <span className="rz" onMouseDown={(e) => startColResize(ci, e)} />
            </div>
          ))}
        </div>
        <div className="gt-body" style={{ flex: "none", height: bodyH, minWidth: totalColW }}>
          <div ref={tableLayer}>{tableRows}</div>
        </div>
      </div>
      <div className="gantt-splitter" onMouseDown={startSplit} />
      <div className="gantt-chart" ref={chartRef} onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseLeave={onMouseLeave}>
        <canvas ref={canvasRef} role="img" aria-label="Cronograma de las actividades de la tabla" />
        <div className="gantt-vscroll" ref={vRef} style={{ top: HEAD_H, bottom: SCROLL }}
          onScroll={(e) => {
            scrollY.current = e.currentTarget.scrollTop;
            const next = Math.floor(scrollY.current / ROW_H);
            if (tableLayer.current) tableLayer.current.style.transform = `translateY(${firstRow * ROW_H - scrollY.current}px)`;
            setFirstRow(old => old === next ? old : next); schedule();
          }}>
          <div style={{ height: totalH, width: 1 }} />
        </div>
        <div className="gantt-hscroll" ref={hRef}
          onScroll={(e) => { scrollX.current = e.currentTarget.scrollLeft; schedule(); }}>
          <div style={{ width: totalDays * dayWidth, height: 1 }} />
        </div>
      </div>
      {tipNode}
    </div>
  );
}
