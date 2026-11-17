import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AutoReportConfig, ReportFile } from "../api/types";
import { useAction, useStore } from "../state/store";
import { Card, download } from "./ui";

const DEFAULT: AutoReportConfig = { enabled: false, weekly: true, monthly: true, annual: true, formats: ["pdf", "xlsx"], weekday: 0, keep_last: 12 };
const DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"];

interface LogEntry { period_type?: string; period_start?: string; period_end?: string; generated_at?: string; files?: ReportFile[]; error?: string | null }

function asLog(v: unknown): LogEntry[] {
  if (Array.isArray(v)) return v as LogEntry[];
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    for (const k of ["items", "log", "entries", "history"]) if (Array.isArray(o[k])) return o[k] as LogEntry[];
  }
  return [];
}

/** Emisión automática de informes al cerrar cada semana, mes y año (se ejecuta mientras Ganttier está abierto). */
export default function AutoReportsCard({ pid }: { pid: number }) {
  const { toast } = useStore();
  const run = useAction();
  const [cfg, setCfg] = useState<AutoReportConfig | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [available, setAvailable] = useState(true);

  const load = () => {
    api.autoreports(pid).then((c) => { setCfg({ ...DEFAULT, ...c }); setAvailable(true); }).catch(() => { setAvailable(false); setCfg(DEFAULT); });
    api.autoreportsLog(pid).then((l) => setLog(asLog(l))).catch(() => setLog([]));
  };
  useEffect(load, [pid]);

  if (!cfg) return null;
  const set = (p: Partial<AutoReportConfig>) => setCfg({ ...cfg, ...p });
  const toggleFmt = (f: string) => set({ formats: cfg.formats.includes(f) ? cfg.formats.filter((x) => x !== f) : [...cfg.formats, f] });

  const save = async () => {
    const r = await run("Guardando configuración…", () => api.saveAutoreports(pid, cfg));
    if (r) { toast(cfg.enabled ? "Emisión automática activada" : "Configuración guardada"); load(); }
  };
  const now = async () => {
    const r = await run("Emitiendo informes pendientes…", () => api.runAutoreports(pid));
    if (r !== undefined) { toast("Revisión de informes pendientes completada"); load(); }
  };

  return (
    <Card title="Emisión automática de informes" actions={<>
      <button className="btn small" disabled={!available} onClick={now}>Emitir pendientes ahora</button>
      <button className="btn small primary" disabled={!available} onClick={save}>Guardar</button>
    </>}>
      {!available && <p className="muted small">La emisión automática no está disponible en este servidor.</p>}
      <div className="row">
        <label className="chk"><input type="checkbox" checked={cfg.enabled} onChange={(e) => set({ enabled: e.target.checked })} /><b>Activar</b></label>
        <label className="chk"><input type="checkbox" checked={cfg.weekly} onChange={(e) => set({ weekly: e.target.checked })} />Semanal</label>
        <label className="chk"><input type="checkbox" checked={cfg.monthly} onChange={(e) => set({ monthly: e.target.checked })} />Mensual</label>
        <label className="chk"><input type="checkbox" checked={cfg.annual} onChange={(e) => set({ annual: e.target.checked })} />Anual</label>
        <span className="muted">Formatos:</span>
        {["pdf", "xlsx", "docx", "html"].map((f) => (
          <label key={f} className="chk"><input type="checkbox" checked={cfg.formats.includes(f)} onChange={() => toggleFmt(f)} />{f.toUpperCase()}</label>
        ))}
        <label className="chk">Semanal el
          <select className="inp" value={cfg.weekday} onChange={(e) => set({ weekday: Number(e.target.value) })}>
            {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </select>
        </label>
      </div>
      <p className="muted small">Mientras Ganttier esté en ejecución, al iniciar y cada hora se emiten los informes de los períodos cerrados (semana, mes o año anterior) que aún no existan. Quedan en la carpeta datos/informes y en el historial de abajo.</p>
      {log.length > 0 && (
        <div className="tbl-wrap" style={{ maxHeight: 240 }}>
          <table className="tbl"><thead><tr><th>Emitido</th><th>Tipo</th><th>Período</th><th>Archivos</th></tr></thead>
            <tbody>{log.slice(0, 50).map((e, k) => (
              <tr key={k}>
                <td>{e.generated_at?.replace("T", " ").slice(0, 16) ?? ""}</td>
                <td>{e.period_type === "weekly" ? "Semanal" : e.period_type === "monthly" ? "Mensual" : e.period_type === "annual" ? "Anual" : e.period_type}</td>
                <td>{e.period_start?.split("-").reverse().join("-")} → {e.period_end?.split("-").reverse().join("-")}</td>
                <td>{e.error ? <span style={{ color: "var(--critical)" }}>{e.error}</span> : (e.files ?? []).map((f) => (
                  <button key={f.filename} className="btn small" style={{ marginRight: 4 }} onClick={() => download(f.url, f.filename)}>{f.format.toUpperCase()}</button>
                ))}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
