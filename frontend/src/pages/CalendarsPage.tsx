import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Calendar, CalendarDay, CalendarException } from "../api/types";
import { useStore } from "../state/store";
import { Card, Field, NumInput } from "../components/ui";
import { dayParts, dayToIso, isoToDay, MONTHS_LONG, WEEKDAYS } from "../utils/dates";

const EMPTY: Partial<Calendar> = { name: "", workweek: "1111100", hours_per_day: 8, holidays_country: "CL", shift_pattern: null, shift_anchor: null, holidays_apply_to_shift: false, exceptions: [] };

export default function CalendarsPage() {
  const { calendars, reloadCalendars, fail, toast } = useStore();
  const [selId, setSelId] = useState<number | null>(null);
  const [edit, setEdit] = useState<Partial<Calendar> | null>(null);
  const [year, setYear] = useState(new Date().getFullYear());
  const [days, setDays] = useState<CalendarDay[]>([]);
  const [newEx, setNewEx] = useState<CalendarException>({ date: "", working: false, hours: 0, reason: "" });

  useEffect(() => { if (selId === null && calendars.length) setSelId(calendars[0].id); }, [calendars, selId]);
  useEffect(() => {
    const c = calendars.find((x) => x.id === selId);
    setEdit(selId === 0 ? { ...EMPTY, exceptions: [] } : c ? { ...c, exceptions: [...(c.exceptions ?? [])] } : null);
  }, [selId, calendars]);
  useEffect(() => {
    let active = true; setDays([]);
    if (!selId) return;
    api.calendarDays(selId, `${year}-01-01`, `${year}-12-31`).then(value => { if (active) setDays(value); }).catch(e => { if (active) fail(e); });
    return () => { active = false; };
  }, [selId, year, fail, calendars]);

  const dayMap = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);
  const totals = useMemo(() => {
    const w = days.filter((d) => d.working);
    return { days: w.length, hours: w.reduce((s, d) => s + d.hours, 0), holidays: days.filter((d) => !d.working && d.reason && !/fin de semana|descanso/i.test(d.reason)).length };
  }, [days]);

  const save = async () => {
    if (!edit) return;
    if (!edit.name?.trim()) { fail(new Error("El calendario necesita un nombre")); return; }
    if (!/^[01]{7}$/.test(edit.workweek ?? "")) { fail(new Error("La semana laboral debe tener 7 dígitos 0/1 (lunes a domingo)")); return; }
    if (edit.shift_pattern && !/^\d+x\d+$/.test(edit.shift_pattern)) { fail(new Error("El turno debe tener la forma NxM, por ejemplo 7x7")); return; }
    try {
      const c = edit.id ? await api.updateCalendar(edit.id, edit) : await api.createCalendar(edit);
      toast("Calendario guardado");
      await reloadCalendars();
      setSelId(c.id);
    } catch (e) { fail(e); }
  };

  const remove = async () => {
    if (!edit?.id || !window.confirm(`¿Eliminar el calendario ${edit.name}?`)) return;
    try { await api.deleteCalendar(edit.id); setSelId(null); await reloadCalendars(); } catch (e) { fail(e); }
  };

  const toggleWd = (k: number) => {
    if (!edit) return;
    const w = (edit.workweek ?? "1111100").split("");
    w[k] = w[k] === "1" ? "0" : "1";
    setEdit({ ...edit, workweek: w.join("") });
  };

  const addEx = () => {
    if (!edit || !newEx.date) return;
    const ex = (edit.exceptions ?? []).filter((e) => e.date !== newEx.date);
    setEdit({ ...edit, exceptions: [...ex, newEx].sort((a, b) => a.date.localeCompare(b.date)) });
    setNewEx({ date: "", working: false, hours: 0, reason: "" });
  };

  const toggleDay = (iso: string) => {
    if (!edit) return;
    const d = dayMap.get(iso);
    const exists = (edit.exceptions ?? []).find((e) => e.date === iso);
    if (exists) setEdit({ ...edit, exceptions: (edit.exceptions ?? []).filter((e) => e.date !== iso) });
    else setEdit({ ...edit, exceptions: [...(edit.exceptions ?? []), { date: iso, working: !(d?.working ?? true), hours: d?.working ? 0 : edit.hours_per_day ?? 8, reason: d?.working ? "Día no laborable" : "Día laborable especial" }] });
  };

  return (
    <div className="grid2" style={{ gridTemplateColumns: "260px 1fr" }}>
      <Card title="Calendarios" actions={<button className="btn small" onClick={() => { setSelId(0); setEdit({ ...EMPTY, exceptions: [] }); }}>＋</button>} bodyStyle={{ padding: 0 }}>
        {calendars.map((c) => (
          <div key={c.id} onClick={() => setSelId(c.id)} style={{ padding: "8px 14px", cursor: "pointer", borderBottom: "1px solid var(--grid)", background: c.id === selId ? "var(--accent-soft)" : undefined }}>
            <b>{c.name}</b>
            <div className="muted small">{c.shift_pattern ? `Turno ${c.shift_pattern}` : c.workweek.split("").map((x, k) => (x === "1" ? WEEKDAYS[k].slice(0, 1).toUpperCase() : "·")).join(" ")} · {c.hours_per_day} h{c.holidays_country ? ` · feriados ${c.holidays_country}` : ""}</div>
          </div>
        ))}
      </Card>
      {edit ? (
        <div className="stack">
          <Card title={edit.id ? edit.name : "Nuevo calendario"} actions={<>{edit.id && <button className="btn small danger" onClick={remove}>Eliminar</button>}<button className="btn small primary" onClick={save}>Guardar</button></>}>
            <div className="form-grid">
              <Field label="Nombre"><input className="inp" value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="Horas por día laborable"><NumInput value={edit.hours_per_day} onChange={(v) => setEdit({ ...edit, hours_per_day: v })} min={0} /></Field>
              <Field label="Feriados de país">
                <select className="inp" value={edit.holidays_country ?? ""} onChange={(e) => setEdit({ ...edit, holidays_country: e.target.value || null })}>
                  <option value="CL">Chile (feriados legales, incluye móviles)</option><option value="">Sin feriados</option>
                </select>
              </Field>
              <Field label="Turno (NxM, vacío = semana laboral)"><input className="inp" placeholder="7x7" value={edit.shift_pattern ?? ""} onChange={(e) => setEdit({ ...edit, shift_pattern: e.target.value || null })} /></Field>
              <Field label="Inicio del ciclo de turno"><input className="inp" type="date" value={edit.shift_anchor ?? ""} onChange={(e) => setEdit({ ...edit, shift_anchor: e.target.value || null })} /></Field>
              <label className="chk" style={{ alignSelf: "end" }}><input type="checkbox" checked={edit.holidays_apply_to_shift ?? false} onChange={(e) => setEdit({ ...edit, holidays_apply_to_shift: e.target.checked })} />Aplicar feriados al turno</label>
            </div>
            {!edit.shift_pattern && (
              <div className="row" style={{ marginTop: 12 }}>
                <span className="muted">Días laborables:</span>
                {WEEKDAYS.map((w, k) => (
                  <label key={w} className="chk"><input type="checkbox" checked={(edit.workweek ?? "")[k] === "1"} onChange={() => toggleWd(k)} />{w}</label>
                ))}
              </div>
            )}
          </Card>
          <Card title="Excepciones" bodyStyle={{ padding: 0 }}>
            <table className="tbl"><thead><tr><th>Fecha</th><th>Tipo</th><th className="num">Horas</th><th>Motivo</th><th /></tr></thead>
              <tbody>
                {(edit.exceptions ?? []).map((e) => (
                  <tr key={e.date}><td>{e.date.split("-").reverse().join("-")}</td><td>{e.working ? "Laborable" : "No laborable"}</td><td className="num">{e.hours}</td><td>{e.reason}</td>
                    <td><button className="btn small ghost" onClick={() => setEdit({ ...edit, exceptions: (edit.exceptions ?? []).filter((x) => x.date !== e.date) })}>✕</button></td></tr>
                ))}
                <tr>
                  <td><input className="cell" type="date" value={newEx.date} onChange={(e) => setNewEx({ ...newEx, date: e.target.value })} /></td>
                  <td><select className="cell" value={newEx.working ? "1" : "0"} onChange={(e) => setNewEx({ ...newEx, working: e.target.value === "1", hours: e.target.value === "1" ? edit.hours_per_day ?? 8 : 0 })}><option value="0">No laborable</option><option value="1">Laborable</option></select></td>
                  <td><input className="cell right" type="number" value={newEx.hours} onChange={(e) => setNewEx({ ...newEx, hours: Number(e.target.value) })} /></td>
                  <td><input className="cell" placeholder="p. ej. mantención mayor" value={newEx.reason} onChange={(e) => setNewEx({ ...newEx, reason: e.target.value })} /></td>
                  <td><button className="btn small" onClick={addEx}>＋</button></td>
                </tr>
              </tbody>
            </table>
            <div className="muted small" style={{ padding: "8px 14px" }}>Tras modificar, presiona Guardar. En la vista anual puedes hacer clic en un día para alternarlo como excepción.</div>
          </Card>
          {edit.id && (
            <Card title={`Vista anual ${year}`} actions={<>
              <button className="btn small" onClick={() => setYear(year - 1)}>◀</button>
              <button className="btn small" onClick={() => setYear(year + 1)}>▶</button>
              <span className="muted small">{totals.days} días laborables · {totals.hours.toLocaleString("es-CL")} h · {totals.holidays} feriados</span>
            </>}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 14 }}>
                {Array.from({ length: 12 }, (_, m) => {
                  const first = Math.floor(Date.UTC(year, m, 1) / 86400000);
                  const last = Math.floor(Date.UTC(year, m + 1, 1) / 86400000) - 1;
                  const off = dayParts(first).wd;
                  const cells: JSX.Element[] = [];
                  for (let k = 0; k < off; k++) cells.push(<div key={`o${k}`} />);
                  for (let d = first; d <= last; d++) {
                    const iso = dayToIso(d);
                    const info = dayMap.get(iso);
                    const isEx = (edit.exceptions ?? []).some((e) => e.date === iso);
                    const holiday = info && !info.working && info.reason && !/fin de semana|descanso/i.test(info.reason);
                    cells.push(
                      <div key={iso} onClick={() => toggleDay(iso)} title={`${iso.split("-").reverse().join("-")}: ${info ? (info.working ? `${info.hours} h` : info.reason || "No laborable") : ""}${isEx ? " (excepción)" : ""}`}
                        style={{
                          height: 22, fontSize: 11, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 4, cursor: "pointer",
                          background: holiday ? "var(--holiday)" : info && !info.working ? "var(--weekend)" : "var(--accent-soft)",
                          color: holiday ? "var(--critical)" : info && !info.working ? "var(--text-3)" : "var(--text)",
                          outline: isEx ? "1.5px solid var(--datadate)" : undefined, fontWeight: isoToDay(iso) === Math.floor(Date.now() / 86400000) ? 700 : 400,
                        }}>{dayParts(d).d}</div>,
                    );
                  }
                  return (
                    <div key={m}>
                      <div style={{ fontWeight: 600, marginBottom: 4, textTransform: "capitalize" }}>{MONTHS_LONG[m]}</div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 2 }}>
                        {WEEKDAYS.map((w) => <div key={w} className="muted" style={{ fontSize: 10, textAlign: "center" }}>{w.slice(0, 2)}</div>)}
                        {cells}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="legend" style={{ marginTop: 10 }}>
                <span><i style={{ background: "var(--accent-soft)" }} />Laborable</span>
                <span><i style={{ background: "var(--weekend)" }} />No laborable</span>
                <span><i style={{ background: "var(--holiday)" }} />Feriado</span>
                <span><i style={{ outline: "1.5px solid var(--datadate)" }} />Excepción</span>
              </div>
            </Card>
          )}
        </div>
      ) : <div className="empty">Selecciona o crea un calendario.</div>}
    </div>
  );
}
