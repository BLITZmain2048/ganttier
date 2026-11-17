import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { Resource, ResourceKind } from "../api/types";
import { useStore } from "../state/store";
import { Card, Empty, Field, Modal, NumInput, TagsInput } from "../components/ui";

const KINDS: { v: ResourceKind; l: string }[] = [
  { v: "trabajador", l: "Trabajador" }, { v: "cuadrilla", l: "Cuadrilla" }, { v: "equipo", l: "Equipo" }, { v: "material", l: "Material" },
];

const EMPTY: Partial<Resource> = {
  name: "", kind: "trabajador", skills: [], capacity_hours_day: 8, max_units: 1, calendar_id: null, cost_hour: 0,
  overtime_cost_hour: 0, max_overtime_hours_day: 2, active: true, email: "",
};

export default function ResourcesPage() {
  const { resources, reloadResources, calendars, projectId, fail, toast, project } = useStore();
  const [edit, setEdit] = useState<Partial<Resource> | null>(null);
  const [q, setQ] = useState("");
  const [skillF, setSkillF] = useState("");

  const skills = useMemo(() => Array.from(new Set(resources.flatMap((r) => r.skills))).sort(), [resources]);
  const list = resources.filter((r) => (!q || r.name.toLowerCase().includes(q.toLowerCase())) && (!skillF || r.skills.includes(skillF)));
  const bySkill = useMemo(() => {
    const m = new Map<string, { n: number; hh: number }>();
    for (const r of resources) if (r.active) for (const s of r.skills) {
      const v = m.get(s) ?? { n: 0, hh: 0 };
      v.n += r.max_units; v.hh += r.capacity_hours_day * r.max_units;
      m.set(s, v);
    }
    return Array.from(m.entries()).sort((a, b) => b[1].hh - a[1].hh);
  }, [resources]);

  const save = async () => {
    if (!edit) return;
    if (!edit.name?.trim()) { fail(new Error("El recurso necesita un nombre")); return; }
    try {
      if (edit.id) await api.updateResource(edit.id, edit);
      else await api.createResource({ ...edit, project_id: edit.project_id === undefined ? projectId : edit.project_id });
      toast("Recurso guardado");
      setEdit(null);
      await reloadResources();
    } catch (e) { fail(e); }
  };

  const remove = async (r: Resource) => {
    if (!window.confirm(`¿Eliminar el recurso ${r.name}? Sus asignaciones se eliminarán.`)) return;
    try { await api.deleteResource(r.id); await reloadResources(); } catch (e) { fail(e); }
  };

  const calName = (id: number | null) => calendars.find((c) => c.id === id)?.name ?? "(del proyecto)";

  return (
    <div className="stack">
      <div className="row">
        <button className="btn primary" onClick={() => setEdit({ ...EMPTY, project_id: projectId ?? null })}>＋ Nuevo recurso</button>
        <input className="inp" placeholder="Buscar…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="inp" value={skillF} onChange={(e) => setSkillF(e.target.value)}>
          <option value="">Todas las habilidades</option>
          {skills.map((s) => <option key={s}>{s}</option>)}
        </select>
        <span className="muted small">Los recursos sin proyecto son globales y se comparten entre proyectos.</span>
      </div>
      <div className="grid2" style={{ gridTemplateColumns: "3fr 1fr" }}>
        <Card title={`Recursos (${list.length})`} bodyStyle={{ padding: 0 }}>
          {list.length === 0 ? <Empty>No hay recursos. Crea trabajadores, cuadrillas o equipos con sus habilidades para nivelar la carga.</Empty> : (
            <div className="tbl-wrap" style={{ maxHeight: "70vh" }}>
              <table className="tbl">
                <thead><tr><th>Nombre</th><th>Tipo</th><th>Habilidades</th><th className="num">HH/día</th><th className="num">Unid.</th><th>Calendario</th><th className="num">Costo HH</th><th className="num">Costo HH extra</th><th className="num">Máx. extra/día</th><th>Ámbito</th><th /></tr></thead>
                <tbody>
                  {list.map((r) => (
                    <tr key={r.id} style={{ opacity: r.active ? 1 : 0.5 }}>
                      <td><b>{r.name}</b>{r.email && <div className="muted small">{r.email}</div>}</td>
                      <td>{KINDS.find((k) => k.v === r.kind)?.l ?? r.kind}</td>
                      <td style={{ whiteSpace: "normal", maxWidth: 260 }}>{r.skills.map((s) => <span key={s} className="tag">{s}</span>)}</td>
                      <td className="num">{r.capacity_hours_day}</td>
                      <td className="num">{r.max_units}</td>
                      <td>{calName(r.calendar_id)}</td>
                      <td className="num">{r.cost_hour.toLocaleString("es-CL")}</td>
                      <td className="num">{r.overtime_cost_hour.toLocaleString("es-CL")}</td>
                      <td className="num">{r.max_overtime_hours_day}</td>
                      <td>{r.project_id ? "Proyecto" : "Global"}</td>
                      <td className="right"><button className="btn small" onClick={() => setEdit({ ...r })}>Editar</button> <button className="btn small ghost" onClick={() => remove(r)}>✕</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Capacidad por habilidad">
          {bySkill.length === 0 ? <span className="muted">Sin habilidades definidas.</span> : (
            <table className="tbl"><thead><tr><th>Habilidad</th><th className="num">Recursos</th><th className="num">HH/día</th></tr></thead>
              <tbody>{bySkill.map(([s, v]) => <tr key={s}><td>{s}</td><td className="num">{v.n}</td><td className="num">{v.hh.toFixed(0)}</td></tr>)}</tbody></table>
          )}
          <p className="muted small">Es la capacidad normal que usa el optimizador al agrupar por habilidad{project?.mode === "mantenimiento" ? " y al asignar trabajadores a las ocurrencias" : ""}.</p>
        </Card>
      </div>

      {edit && (
        <Modal title={edit.id ? `Editar ${edit.name}` : "Nuevo recurso"} onClose={() => setEdit(null)}
          footer={<><button className="btn" onClick={() => setEdit(null)}>Cancelar</button><button className="btn primary" onClick={save}>Guardar</button></>}>
          <div className="form-grid">
            <Field label="Nombre"><input className="inp" autoFocus value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Tipo">
              <select className="inp" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value as ResourceKind })}>
                {KINDS.map((k) => <option key={k.v} value={k.v}>{k.l}</option>)}
              </select>
            </Field>
            <Field label="Correo"><input className="inp" value={edit.email ?? ""} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></Field>
            <Field label="Habilidades / roles (Enter o coma para agregar)" style={{ gridColumn: "1 / -1" }}>
              <TagsInput value={edit.skills ?? []} onChange={(v) => setEdit({ ...edit, skills: v })} placeholder="SSEE, Protecciones, Líneas…" />
            </Field>
            <Field label="Capacidad normal (HH/día por unidad)"><NumInput value={edit.capacity_hours_day} onChange={(v) => setEdit({ ...edit, capacity_hours_day: v })} min={0} /></Field>
            <Field label="Unidades disponibles"><NumInput value={edit.max_units} onChange={(v) => setEdit({ ...edit, max_units: v })} min={0} /></Field>
            <Field label="Calendario">
              <select className="inp" value={edit.calendar_id ?? ""} onChange={(e) => setEdit({ ...edit, calendar_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">(del proyecto)</option>
                {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field>
            <Field label="Costo HH normal"><NumInput value={edit.cost_hour} onChange={(v) => setEdit({ ...edit, cost_hour: v })} min={0} /></Field>
            <Field label="Costo HH extra"><NumInput value={edit.overtime_cost_hour} onChange={(v) => setEdit({ ...edit, overtime_cost_hour: v })} min={0} /></Field>
            <Field label="Máx. HH extra por día"><NumInput value={edit.max_overtime_hours_day} onChange={(v) => setEdit({ ...edit, max_overtime_hours_day: v })} min={0} /></Field>
            <Field label="Ámbito">
              <select className="inp" value={edit.project_id ? "p" : "g"} onChange={(e) => setEdit({ ...edit, project_id: e.target.value === "p" ? projectId : null })}>
                <option value="p">Solo este proyecto</option><option value="g">Global (todos los proyectos)</option>
              </select>
            </Field>
            <label className="chk" style={{ alignSelf: "end" }}><input type="checkbox" checked={edit.active ?? true} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />Activo</label>
          </div>
        </Modal>
      )}
    </div>
  );
}
