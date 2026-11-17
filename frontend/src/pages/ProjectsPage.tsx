import { useState } from "react";
import { api } from "../api/client";
import type { Mode, Project } from "../api/types";
import { useAction, useStore } from "../state/store";
import { Card, Empty, Field, Modal } from "../components/ui";
import { fmtDate, todayIso } from "../utils/dates";

export default function ProjectsPage() {
  const { projects, reloadProjects, go, toast, calendars } = useStore();
  const run = useAction();
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<Partial<Project>>({ name: "", mode: "proyecto", start_date: todayIso(), end_date: null, currency: "CLP" });
  const [confirmDel, setConfirmDel] = useState<Project | null>(null);
  const [demoN, setDemoN] = useState(20000);

  const create = async () => {
    if (!form.name?.trim()) { toast("Ingresa un nombre para el proyecto", "error"); return; }
    if (form.mode === "mantenimiento" && !form.end_date) { toast("En modo mantenimiento debes indicar el fin del horizonte", "error"); return; }
    const p = await run("Creando proyecto…", () => api.createProject(form));
    if (p) { setCreating(false); await reloadProjects(); go("gantt", p.id); }
  };

  const demo = async (kind: Mode) => {
    const p = await run(kind === "mantenimiento" ? `Generando plan de mantenimiento de ${demoN.toLocaleString("es-CL")} actividades…` : "Creando proyecto de demostración…",
      () => api.demo(kind, kind === "mantenimiento" ? demoN : undefined));
    if (p) { toast(`Proyecto de demostración creado: ${p.name}`); await reloadProjects(); go("gantt", p.id); }
  };

  const duplicate = async (p: Project) => {
    const np = await run("Duplicando…", () => api.duplicateProject(p.id));
    if (np) { toast(`Copia creada: ${np.name}`); await reloadProjects(); }
  };

  const remove = async (p: Project) => {
    await run("Eliminando…", () => api.deleteProject(p.id));
    setConfirmDel(null);
    await reloadProjects();
    toast("Proyecto eliminado");
  };

  return (
    <div className="stack">
      <div className="row">
        <button className="btn primary" onClick={() => setCreating(true)}>＋ Nuevo proyecto</button>
        <button className="btn" onClick={() => go("importar")}>⇪ Crear desde plantilla Excel</button>
        <span className="sep" />
        <button className="btn" onClick={() => demo("proyecto")}>Demo: construcción S/E</button>
        <button className="btn" onClick={() => demo("mantenimiento")}>Demo: mantenimiento</button>
        <select className="inp" value={demoN} onChange={(e) => setDemoN(Number(e.target.value))} title="Cantidad de actividades del demo de mantenimiento">
          {[2000, 10000, 20000, 50000, 100000].map((n) => <option key={n} value={n}>{n.toLocaleString("es-CL")} actividades</option>)}
        </select>
      </div>
      <Card title={`Proyectos (${projects.length})`} bodyStyle={{ padding: 0 }}>
        {projects.length === 0 ? (
          <Empty>
            No hay proyectos. Crea uno nuevo, importa una plantilla Excel o prueba un proyecto de demostración.
          </Empty>
        ) : (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>Nombre</th><th>Modo</th><th>Inicio</th><th>Fin / horizonte</th><th>Fecha de corte</th><th className="num">Actividades</th><th /></tr></thead>
              <tbody>
                {projects.map((p) => (
                  <tr key={p.id}>
                    <td><a href={`#/p/${p.id}/gantt`} style={{ color: "var(--accent)", fontWeight: 600, textDecoration: "none" }}>{p.name}</a>
                      {p.description && <div className="muted small">{p.description}</div>}</td>
                    <td><span className={`badge ${p.mode}`}>{p.mode === "mantenimiento" ? "Mantenimiento" : "Proyecto"}</span></td>
                    <td>{fmtDate(p.start_date)}</td>
                    <td>{fmtDate(p.end_date)}</td>
                    <td>{fmtDate(p.data_date)}</td>
                    <td className="num">{p.counts?.activities?.toLocaleString("es-CL") ?? "—"}</td>
                    <td className="right">
                      <button className="btn small" onClick={() => go("gantt", p.id)}>Abrir</button>{" "}
                      <button className="btn small" onClick={() => duplicate(p)}>Duplicar</button>{" "}
                      <button className="btn small danger" onClick={() => setConfirmDel(p)}>Eliminar</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {creating && (
        <Modal title="Nuevo proyecto" onClose={() => setCreating(false)}
          footer={<><button className="btn" onClick={() => setCreating(false)}>Cancelar</button><button className="btn primary" onClick={create}>Crear</button></>}>
          <div className="form-grid">
            <Field label="Nombre" style={{ gridColumn: "1 / -1" }}>
              <input className="inp" autoFocus value={form.name ?? ""} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Modo">
              <select className="inp" value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value as Mode })}>
                <option value="proyecto">Proyecto (CPM, dependencias, recursos)</option>
                <option value="mantenimiento">Mantenimiento (actividades periódicas)</option>
              </select>
            </Field>
            <Field label="Calendario">
              <select className="inp" value={form.calendar_id ?? ""} onChange={(e) => setForm({ ...form, calendar_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">(por defecto)</option>
                {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field>
            <Field label="Fecha de inicio">
              <input className="inp" type="date" value={form.start_date ?? ""} onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
            </Field>
            <Field label={form.mode === "mantenimiento" ? "Fin del horizonte (obligatorio)" : "Fecha de término (opcional)"}>
              <input className="inp" type="date" value={form.end_date ?? ""} onChange={(e) => setForm({ ...form, end_date: e.target.value || null })} />
            </Field>
            <Field label="Moneda">
              <input className="inp" value={form.currency ?? "CLP"} onChange={(e) => setForm({ ...form, currency: e.target.value })} />
            </Field>
            <Field label="Descripción" style={{ gridColumn: "1 / -1" }}>
              <textarea className="inp" rows={2} value={form.description ?? ""} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
          </div>
        </Modal>
      )}

      {confirmDel && (
        <Modal title="Eliminar proyecto" onClose={() => setConfirmDel(null)}
          footer={<><button className="btn" onClick={() => setConfirmDel(null)}>Cancelar</button><button className="btn danger" onClick={() => remove(confirmDel)}>Eliminar definitivamente</button></>}>
          Se eliminará <b>{confirmDel.name}</b> con todas sus actividades, relaciones, líneas base y avances. Esta acción no se puede deshacer.
        </Modal>
      )}
    </div>
  );
}
