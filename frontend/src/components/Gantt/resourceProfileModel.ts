import type { LoadProfile, LoadSeries } from "../../api/types";
import { isoToDay } from "../../utils/dates";

export type ProfilePeriod = "day" | "week";
export interface ProfileSeries extends LoadSeries { total: number; color: number }
export interface ProfileModel {
  days: Float64Array;
  span: number;
  series: ProfileSeries[];
}

export function buildProfile(profile: LoadProfile, period: ProfilePeriod): ProfileModel {
  if (profile.period !== period) throw new Error("El servidor devolvió un período distinto al solicitado.");
  const days = Float64Array.from(profile.labels, label => isoToDay(label));
  const span = period === "day" ? 1 : 7;
  for (let i = 0; i < days.length; i++) {
    if (!Number.isFinite(days[i]) || (i > 0 && days[i] < days[i - 1] + span)) {
      throw new Error("El perfil contiene fechas inválidas, superpuestas o desordenadas.");
    }
  }
  const keys = new Set<string>();
  const series = profile.series.map((s, color) => {
    if (keys.has(s.key) || s.load.length !== days.length || s.capacity.length !== days.length
      || s.load.some(v => !Number.isFinite(v) || v < 0)
      || s.capacity.some(v => !Number.isFinite(v) || v < 0)) {
      throw new Error("El perfil contiene series duplicadas o valores inválidos.");
    }
    keys.add(s.key);
    return { ...s, total: s.load.reduce((sum, v) => sum + v, 0), color: color % 8 + 1 };
  });
  series.sort((a, b) => b.total - a.total || a.key.localeCompare(b.key));
  return { days, span, series };
}

/** Half-open period range; no scan across the whole project during scroll. */
export function visiblePeriods(model: ProfileModel, start: number, end: number): [number, number] {
  const lowerBound = (day: number) => {
    let lo = 0, hi = model.days.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (model.days[mid] < day) lo = mid + 1; else hi = mid;
    }
    return lo;
  };
  let first = lowerBound(start);
  if (first > 0 && model.days[first - 1] + model.span > start) first--;
  return [first, lowerBound(end)];
}

export function selectProfile(model: ProfileModel, keys: Set<string>) {
  const series = model.series.filter(s => keys.has(s.key));
  const load = new Float64Array(model.days.length);
  const capacity = new Float64Array(model.days.length);
  let max = 1;
  for (const s of series) {
    for (let i = 0; i < load.length; i++) {
      load[i] += s.load[i]; capacity[i] += s.capacity[i];
      max = Math.max(max, load[i], capacity[i]);
    }
  }
  return { series, load, capacity, max };
}
