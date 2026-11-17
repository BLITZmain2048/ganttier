import { useRef, useState } from "react";
import { api } from "../api/client";
import type { ImportResponse } from "../api/types";
import { useAction, useStore } from "../state/store";
import { Card, download } from "../components/ui";

const SUMMARY_LABELS: Record<string, string> = {
  calendars: "Calendarios", resources: "Recursos", wbs: "WBS", activities: "Actividades", relations: "Relaciones",
  assignments: "Asignaciones", periodic_tasks: "Tareas periódicas", progress: "Avance",
  calendarios: "Calendarios", recursos: "Recursos", actividades: "Actividades", relaciones: "Relaciones",
  asignaciones: "Asignaciones", tareas_periodicas: "Tareas periódicas", avance: "Avance",
};

export default function ImportPage() {
  const { projectId, project, toast, bumpSchedule, reloadProjects, go, reloadResources, reloadCalendars, reloadProject } = useStore();
  const run = useAction();
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [mode, setMode] = useState<"merge" | "replace">("merge");
  const [target, setTarget] = useState<"actual" | "nuevo">(projectId ? "actual" : "nuevo");
  const [result, setResult] = useState<ImportResponse | null>(null);
  const inp = useRef<HTMLInputElement>(null);

  const pick = (f: File | null | undefined) => {
    if (!f) return;
    if (!/\.xlsx$/i.test(f.name)) { toast("Selecciona un archivo Excel .xlsx", "error"); return; }
    setFile(f); setResult(null);
  };

  const dry = async () => {
    if (!file || !projectId) return;
    const r = await run("Validando archivo…", () => api.importFile(projectId, file, mode, true));
    if (r) setResult(r);
  };

  const commit = async () => {
    if (!file) return;
    if (target === "nuevo") {
      const p = await run("Creando proyecto desde la plantilla…", () => api.importNewProject(file));
      if (p) { toast(`Proyecto creado: ${p.name}`); await reloadProjects(); go("gantt", p.id); }
      return;
    }
    if (!projectId) return;
    if (!result || result.errors.some(e => e.severity === "error")) return;
    if (mode === "replace" && !window.confirm("Se reemplazará el contenido del proyecto abierto con el archivo validado. ¿Continuar?")) return;
    const r = await run("Importando…", () => api.importFile(projectId, file, mode, false));
    if (r) {
      setResult(r);
      const errs = r.errors.filter((e) => e.severity === "error").length;
      if (errs === 0) { toast("Importación completada"); await Promise.all([reloadResources(), reloadCalendars(), reloadProject()]); bumpSchedule(); }
      else toast(`El archivo tiene ${errs} errores; corrígelos y vuelve a intentar`, "error");
    }
  };

  const errors = result?.errors ?? [];
  const nErr = errors.filter((e) => e.severity === "error").length;

  return (
    <div className="stack">
      <div className="grid2">
        <Card title="1. Descarga una plantilla">
          <p className="muted">Las plantillas incluyen instrucciones, listas desplegables, validaciones y ejemplos del rubro eléctrico. Puedes borrar los ejemplos y completar con tus datos.</p>
          <div className="row">
            <button className="btn" onClick={() => download(api.templateUrl("proyecto"), "Plantilla_Proyecto.xlsx")}>⬇ Plantilla de proyecto</button>
            <button className="btn" onClick={() => download(api.templateUrl("mantenimiento"), "Plantilla_Mantenimiento.xlsx")}>⬇ Plantilla de mantenimiento</button>
          </div>
          {projectId && <>
            <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "16px 0" }} />
            <p className="muted">Exporta el proyecto abierto en el mismo formato (útil para editar masivamente en Excel y volver a importar).</p>
            <button className="btn" onClick={() => download(api.exportUrl(projectId), `${project?.name ?? "proyecto"}.xlsx`)}>⬇ Exportar {project?.name}</button>
          </>}
        </Card>
        <Card title="2. Sube el archivo">
          <div className={`drop${over ? " over" : ""}`} role="button" tabIndex={0} aria-label="Seleccionar archivo Excel" onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inp.current?.click(); } }} onClick={() => inp.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files?.[0]); }}>
            {file ? <><b>{file.name}</b><br /><span className="muted small">{(file.size / 1024).toFixed(0)} KB · clic para cambiar</span></> : <>Arrastra aquí tu archivo .xlsx o haz clic para buscarlo</>}
            <input ref={inp} type="file" accept=".xlsx" hidden onChange={(e) => pick(e.target.files?.[0])} />
          </div>
          <div className="stack" style={{ gap: 8, marginTop: 12 }}>
            <label className="chk"><input type="radio" checked={target === "nuevo"} onChange={() => { setTarget("nuevo"); setResult(null); }} />Crear un proyecto nuevo con el archivo</label>
            <label className="chk"><input type="radio" disabled={!projectId} checked={target === "actual"} onChange={() => { setTarget("actual"); setResult(null); }} />Importar al proyecto abierto{project ? `: ${project.name}` : " (abre uno primero)"}</label>
            {target === "actual" && (
              <div className="row" style={{ paddingLeft: 24 }}>
                <label className="chk"><input type="radio" checked={mode === "merge"} onChange={() => { setMode("merge"); setResult(null); }} />Combinar (actualiza por código y agrega nuevas)</label>
                <label className="chk"><input type="radio" checked={mode === "replace"} onChange={() => { setMode("replace"); setResult(null); }} />Reemplazar todo</label>
              </div>
            )}
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            {target === "actual" && <button className="btn" disabled={!file} onClick={dry}>Validar (sin guardar)</button>}
            <button className="btn primary" disabled={!file || (target === "actual" && (!result || nErr > 0))} onClick={commit}>Importar</button>
          </div>
        </Card>
      </div>
      {result && (
        <Card title={nErr ? `Resultado: ${nErr} errores` : "Resultado de la validación"} bodyStyle={{ padding: 0 }}>
          <div className="row" style={{ padding: "10px 14px" }}>
            {Object.entries(result.summary).map(([k, v]) => <span key={k} className="badge">{SUMMARY_LABELS[k] ?? k.replace(/_/g, " ")}: <b>{String(v)}</b></span>)}
          </div>
          {errors.length > 0 ? (
            <div className="tbl-wrap" style={{ maxHeight: 420 }}>
              <table className="tbl"><thead><tr><th>Tipo</th><th>Hoja</th><th className="num">Fila</th><th>Campo</th><th>Mensaje</th></tr></thead>
                <tbody>{errors.slice(0, 2000).map((e, k) => (
                  <tr key={k}><td style={{ color: e.severity === "error" ? "var(--critical)" : "#b07800", fontWeight: 600 }}>{e.severity === "error" ? "Error" : "Advertencia"}</td>
                    <td>{e.sheet}</td><td className="num">{e.row}</td><td>{e.field}</td><td style={{ whiteSpace: "normal" }}>{e.message}</td></tr>
                ))}</tbody>
              </table>
            </div>
          ) : <div className="empty" style={{ padding: 16 }}>Sin errores. Puedes importar.</div>}
        </Card>
      )}
    </div>
  );
}
