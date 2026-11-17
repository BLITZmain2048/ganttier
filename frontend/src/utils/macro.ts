import { dayParts } from "./dates";

export function monthOffset(day: number, first: number): number {
  if (!Number.isFinite(day) || !Number.isFinite(first)) return NaN;
  const p = dayParts(day), f = dayParts(first);
  return (p.y - f.y) * 12 + p.m - f.m;
}

/** Calendar-day estimate, inclusive endpoints. O(activities + months). */
export function monthlyHours(months: { s: number; e: number }[], count: number,
  get: (i: number) => { start: number; finish: number; hours: number }): Float64Array {
  const totals = new Float64Array(months.length), diff = new Float64Array(months.length + 1);
  if (!months.length) return totals;
  const first = months[0].s, last = months[months.length - 1].e;
  for (let i = 0; i < count; i++) {
    const { start, finish, hours } = get(i);
    if (![start, finish, hours].every(Number.isFinite) || finish < start || finish < first || start > last) continue;
    const s = Math.max(start, first), e = Math.min(finish, last), rate = hours / (finish - start + 1);
    const lo = monthOffset(s, first), hi = monthOffset(e, first);
    if (lo === hi) { totals[lo] += rate * (e - s + 1); continue; }
    totals[lo] += rate * (months[lo].e - s + 1);
    totals[hi] += rate * (e - months[hi].s + 1);
    diff[lo + 1] += rate; diff[hi] -= rate;
  }
  let rate = 0;
  for (let k = 0; k < months.length; k++) { rate += diff[k]; totals[k] += rate * (months[k].e - months[k].s + 1); }
  return totals;
}
