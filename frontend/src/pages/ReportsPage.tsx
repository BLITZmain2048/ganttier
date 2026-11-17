import { useState } from "react";
import { api } from "../api/client";
import type { ReportFile } from "../api/types";
import { useAction, useStore } from "../state/store";
import { Card, download, Field } from "../components/ui";
import { periodBounds, todayIso } from "../utils/dates";
import AutoReportsCard from "../components/AutoReportsCard";

const FORMATS: { k: string; l: string; d: string }[] = [
  { k: "pdf", l: "PDF", d: "Listo para enviar, con gráficos" },
  { k: "xlsx", l: "Excel", d: "Tablas y gráficos editables" },
  { k: "docx", l: "Word", d: "Editable para informes formales" },
  { k: "html", l: "HTML", d: "Para ver en pantalla e imprimir" },
];

export default function ReportsPage() {
  const { projectId, project, toast } = useStore();
  const run = useAction();
  const [kind, setKind] = useState<"weekly" | "monthly" | "annual">("weekly");
  const [ref, setRef] = useState(project?.data_date ?? todayIso());
  const [fmts, setFmts] = useState<Set<string>>(new Set(["pdf", "xlsx", "html"]));
  const [files, setFiles] = useState<ReportFile[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [s, e] = periodBounds(kind, ref);

  const toggle = (k: string) => setFmts((f) => { const n = new Set(f); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  const generate = async () => {
    if (!projectId || !s || !e || fmts.size === 0) return;
    const r = await run("Generando informe…", () => api.reports(projectId, kind, s, Array.from(fmts)));
    if (r) {
      const names = new Set(r.map(f => f.filename));
      setFiles(current => [...r, ...current.filter(f => !names.has(f.filename))]);
      const html = r.find((f) => f.format === "html");
      if (html) setPreview(html.url);
      toast(`Informe generado en ${r.length} formato(s)`);
    }
  };

  const [os, setOs] = useState(project?.start_date ?? todayIso());
  const [oe, setOe] = useState(project?.end_date ?? "");

  return (
    <div className="stack">
      <Card title="Generar informe">
        <div className="form-grid">
          <Field label="Tipo de informe">
            <select className="inp" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
              <option value="weekly">Semanal</option><option value="monthly">Mensual</option><option value="annual">Anual</option>
            </select>
          </Field>
          <Field label="Período que contiene la fecha"><input className="inp" type="date" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
          <Field label="Período del informe"><div className="inp" style={{ display: "flex", alignItems: "center" }}>{s.split("-").reverse().join("-")} → {e.split("-").reverse().join("-")}</div></Field>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          {FORMATS.map((f) => (
            <label key={f.k} className="chk card" style={{ padding: "8px 12px", borderColor: fmts.has(f.k) ? "var(--accent-2)" : undefined }}>
              <input type="checkbox" checked={fmts.has(f.k)} onChange={() => toggle(f.k)} /><b>{f.l}</b><span className="muted small">{f.d}</span>
            </label>
          ))}
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn primary" disabled={fmts.size === 0 || !s || !e} onClick={generate}>▣ Generar informe</button>
          <span className="muted small">Incluye resumen ejecutivo con KPI y semáforo, curva S, valor ganado, actividades terminadas, programadas, atrasadas y próximas, hitos, carga por recurso y desempeño por trabajador. Usa la fecha de corte del proyecto ({project?.data_date?.split("-").reverse().join("-") ?? "sin definir"}).</span>
        </div>
      </Card>
      {projectId && <AutoReportsCard pid={projectId} />}
      <Card title="Programa de trabajos con desconexión (base para solicitudes)">
        <p className="muted small" style={{ marginTop: 0 }}>
          Agrupa por instalación y fecha los trabajos que requieren desconexión o indisponibilidad (campo «Requiere desconexión»),
          con equipos, HH y responsables. Sirve de base para preparar las solicitudes ante el Coordinador Eléctrico Nacional;
          verifica el formato oficial y los plazos de solicitud en la normativa y los procedimientos vigentes.
        </p>
        <div className="row">
          <Field label="Desde"><input className="inp" type="date" value={os} onChange={(e) => setOs(e.target.value)} /></Field>
          <Field label="Hasta"><input className="inp" type="date" value={oe} onChange={(e) => setOe(e.target.value)} /></Field>
          <button className="btn primary" style={{ alignSelf: "end" }} disabled={!projectId}
            onClick={() => projectId && download(api.outagesUrl(projectId, os || undefined, oe || undefined), "programa_desconexiones.xlsx")}>⬇ Descargar Excel</button>
        </div>
      </Card>
      {files.length > 0 && (
        <Card title="Archivos generados" bodyStyle={{ padding: 0 }}>
          <table className="tbl"><tbody>{files.map((f) => (
            <tr key={f.filename}><td><span className="badge">{f.format.toUpperCase()}</span></td><td className="mono">{f.filename}</td>
              <td className="right">{f.format === "html" && <button className="btn small" onClick={() => setPreview(f.url)}>Ver aquí</button>}{" "}
                <button className="btn small primary" onClick={() => download(f.url, f.filename)}>Descargar</button></td></tr>
          ))}</tbody></table>
        </Card>
      )}
      {preview && (
        <Card title="Vista previa" actions={<><button className="btn small" onClick={() => window.open(preview, "_blank")}>Abrir en pestaña</button><button className="btn small ghost" onClick={() => setPreview(null)}>✕</button></>}>
          <iframe sandbox="" className="report" src={preview} title="Informe" />
        </Card>
      )}
    </div>
  );
}
