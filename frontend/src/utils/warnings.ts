/** Agrupa advertencias del tipo "CODIGO: mensaje" que se repiten para muchas actividades. */
export function summarizeWarnings(list: string[] | null | undefined, maxCodes = 6): string[] {
  if (!list || list.length === 0) return [];
  const groups = new Map<string, string[]>();
  const order: string[] = [];
  for (const w of list) {
    const m = /^([A-Za-z0-9_.\-/]+): (.+)$/.exec(w);
    const key = m ? m[2] : w;
    if (!groups.has(key)) { groups.set(key, []); order.push(key); }
    if (m) groups.get(key)!.push(m[1]);
  }
  return order.map((msg) => {
    const codes = groups.get(msg)!;
    if (codes.length === 0) return msg;
    if (codes.length === 1) return `${codes[0]}: ${msg}`;
    const shown = codes.slice(0, maxCodes).join(", ");
    return `${msg} — ${codes.length} actividades (${shown}${codes.length > maxCodes ? ", …" : ""})`;
  });
}
