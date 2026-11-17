import { useState } from "react";
import { api } from "../api/client";
import type { WBSNode } from "../api/types";
import { useStore } from "../state/store";
import { Modal } from "./ui";

/** Administración de la estructura WBS del proyecto (crear, renombrar, mover y eliminar nodos). */
export default function WbsManager({ pid, wbs, onClose, onChanged }: {
  pid: number; wbs: WBSNode[]; onClose: () => void; onChanged: () => void;
}) {
  const { fail, toast } = useStore();
  const [rows, setRows] = useState<WBSNode[]>(() => [...wbs].sort((a, b) => a.code.localeCompare(b.code, "es", { numeric: true })));
  const [draft, setDraft] = useState<{ code: string; name: string; parent_id: number | null }>({ code: "", name: "", parent_id: null });
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState<Set<number>>(new Set());

  const byId = new Map(rows.map((w) => [w.id, w]));
  const label = (w: WBSNode) => `${w.code} · ${w.name}`;
  const edit = (id: number, patch: Partial<WBSNode>) => {
    setRows((r) => r.map((w) => (w.id === id ? { ...w, ...patch } : w)));
    setDirty((d) => new Set(d).add(id));
  };

  const add = async () => {
    if (!draft.code.trim() || !draft.name.trim()) { fail(new Error("Ingresa código y nombre del nodo WBS.")); return; }
    setBusy(true);
    try {
      const n = await api.createWbs(pid, { project_id: pid, code: draft.code.trim(), name: draft.name.trim(), parent_id: draft.parent_id, sort_order: rows.length });
      setRows((r) => [...r, n].sort((a, b) => a.code.localeCompare(b.code, "es", { numeric: true })));
      setDraft({ code: "", name: "", parent_id: draft.parent_id });
      onChanged();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const saveAll = async () => {
    setBusy(true);
    try {
      for (const id of dirty) {
        const w = byId.get(id);
        if (w) await api.updateWbs(id, { code: w.code, name: w.name, parent_id: w.parent_id, sort_order: w.sort_order });
      }
      setDirty(new Set());
      toast("Estructura WBS guardada");
      onChanged();
      onClose();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const remove = async (w: WBSNode) => {
    if (!window.confirm(`¿Eliminar el nodo ${label(w)}? Sus subnodos también se eliminan y sus actividades quedan sin WBS.`)) return;
    setBusy(true);
    try {
      await api.deleteWbs(w.id);
      const removed = new Set<number>([w.id]);
      let grew = true;
      while (grew) { grew = false; for (const x of rows) if (x.parent_id !== null && removed.has(x.parent_id) && !removed.has(x.id)) { removed.add(x.id); grew = true; } }
      setRows((r) => r.filter((x) => !removed.has(x.id)));
      onChanged();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  return (
    <Modal title="Estructura WBS" onClose={onClose} width={860}
      footer={<><button className="btn" onClick={onClose}>Cerrar</button><button className="btn primary" disabled={busy || dirty.size === 0} onClick={saveAll}>Guardar cambios ({dirty.size})</button></>}>
      <table className="tbl">
        <thead><tr><th style={{ width: 120 }}>Código</th><th>Nombre</th><th style={{ width: 240 }}>Nodo padre</th><th className="num" style={{ width: 70 }}>Orden</th><th style={{ width: 40 }} /></tr></thead>
        <tbody>
          {rows.map((w) => (
            <tr key={w.id} style={{ background: dirty.has(w.id) ? "var(--accent-soft)" : undefined }}>
              <td><input className="cell mono" value={w.code} onChange={(e) => edit(w.id, { code: e.target.value })} aria-label="Código WBS" /></td>
              <td><input className="cell" value={w.name} onChange={(e) => edit(w.id, { name: e.target.value })} aria-label="Nombre WBS" /></td>
              <td>
                <select className="cell" value={w.parent_id ?? ""} onChange={(e) => edit(w.id, { parent_id: e.target.value ? Number(e.target.value) : null })} aria-label="Nodo padre">
                  <option value="">(raíz)</option>
                  {rows.filter((x) => x.id !== w.id).map((x) => <option key={x.id} value={x.id}>{label(x)}</option>)}
                </select>
              </td>
              <td><input className="cell right" type="number" value={w.sort_order} onChange={(e) => edit(w.id, { sort_order: Number(e.target.value) || 0 })} aria-label="Orden" /></td>
              <td><button className="btn small ghost" disabled={busy} onClick={() => remove(w)} aria-label={`Eliminar ${w.code}`}>✕</button></td>
            </tr>
          ))}
          <tr>
            <td><input className="cell mono" placeholder="p. ej. 1.2" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} aria-label="Nuevo código" /></td>
            <td><input className="cell" placeholder="Nombre del nodo" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") void add(); }} aria-label="Nuevo nombre" /></td>
            <td>
              <select className="cell" value={draft.parent_id ?? ""} onChange={(e) => setDraft({ ...draft, parent_id: e.target.value ? Number(e.target.value) : null })} aria-label="Padre del nuevo nodo">
                <option value="">(raíz)</option>
                {rows.map((x) => <option key={x.id} value={x.id}>{label(x)}</option>)}
              </select>
            </td>
            <td />
            <td><button className="btn small" disabled={busy} onClick={add}>＋</button></td>
          </tr>
        </tbody>
      </table>
      <p className="muted small">Asigna cada actividad a su nodo desde el detalle de la actividad (Enter) o con la columna Codigo_WBS de la plantilla Excel.</p>
    </Modal>
  );
}
