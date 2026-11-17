// Utilidades de fecha sin librerías. Trabajamos con "número de día" = días desde 1970-01-01 (UTC).
const MS_DAY = 86400000;

export const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
export const MONTHS_LONG = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];
export const WEEKDAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];

export function isoToDay(iso: string | null | undefined): number {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return NaN;
  const dt = new Date(iso + "T00:00:00.000Z");
  const ms = dt.getTime();
  return Number.isFinite(ms) && dt.toISOString().slice(0, 10) === iso ? ms / MS_DAY : NaN;
}

export function dayToIso(day: number): string {
  if (!Number.isFinite(day)) return "";
  const dt = new Date(day * MS_DAY);
  const y = dt.getUTCFullYear();
  const m = dt.getUTCMonth() + 1;
  const d = dt.getUTCDate();
  return `${String(y).padStart(4, "0")}-${m < 10 ? "0" : ""}${m}-${d < 10 ? "0" : ""}${d}`;
}

export function dayParts(day: number): { y: number; m: number; d: number; wd: number } {
  const dt = new Date(day * MS_DAY);
  // wd: 0 = lunes ... 6 = domingo
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth(), d: dt.getUTCDate(), wd: (dt.getUTCDay() + 6) % 7 };
}

export function todayIso(): string {
  const n = new Date();
  const m = n.getMonth() + 1, d = n.getDate();
  return `${n.getFullYear()}-${m < 10 ? "0" : ""}${m}-${d < 10 ? "0" : ""}${d}`;
}

export function todayDay(): number {
  return isoToDay(todayIso());
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return `${iso.slice(8, 10)}-${iso.slice(5, 7)}-${iso.slice(0, 4)}`;
}

export function fmtDay(day: number): string {
  return Number.isFinite(day) ? fmtDate(dayToIso(day)) : "";
}

export function monthStartDay(y: number, m: number): number {
  const dt = new Date(0);
  dt.setUTCFullYear(y, m, 1);
  return Math.floor(dt.getTime() / MS_DAY);
}

export function addMonthsDay(day: number, n: number): number {
  const p = dayParts(day);
  return monthStartDay(p.y, p.m + n);
}

export function monthKey(day: number): string {
  const p = dayParts(day);
  return `${String(p.y).padStart(4, "0")}-${p.m + 1 < 10 ? "0" : ""}${p.m + 1}`;
}

export function monthKeyLabel(key: string): string {
  const m = +key.slice(5, 7);
  return `${MONTHS[m - 1]} ${key.slice(2, 4)}`;
}

export function monthRange(key: string): [string, string] {
  const y = +key.slice(0, 4), m = +key.slice(5, 7) - 1;
  const s = monthStartDay(y, m);
  const e = monthStartDay(y, m + 1) - 1;
  return [dayToIso(s), dayToIso(e)];
}

/** Lunes de la semana del día. */
export function weekStart(day: number): number {
  return day - dayParts(day).wd;
}

/** Número de semana ISO. */
export function isoWeek(day: number): number {
  const wd = dayParts(day).wd;
  const thursday = day - wd + 3;
  const y = dayParts(thursday).y;
  const jan4 = monthStartDay(y, 0) + 3;
  const week1Mon = jan4 - dayParts(jan4).wd;
  return Math.floor((thursday - week1Mon) / 7) + 1;
}

export function periodBounds(kind: "weekly" | "monthly" | "annual", iso: string): [string, string] {
  const day = isoToDay(iso);
  if (!Number.isFinite(day)) return ["", ""];
  if (kind === "weekly") {
    const s = weekStart(day);
    return [dayToIso(s), dayToIso(s + 6)];
  }
  const p = dayParts(day);
  if (kind === "monthly") return [dayToIso(monthStartDay(p.y, p.m)), dayToIso(monthStartDay(p.y, p.m + 1) - 1)];
  return [dayToIso(monthStartDay(p.y, 0)), dayToIso(monthStartDay(p.y + 1, 0) - 1)];
}

export function fmtNum(v: number | null | undefined, dec = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return v.toLocaleString("es-CL", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

export function fmtPct(v: number | null | undefined, dec = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${fmtNum(v, dec)} %`;
}
