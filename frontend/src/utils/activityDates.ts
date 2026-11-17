import type { CalendarDay } from "../api/types";
import { isoToDay } from "./dates";

export function scheduledDates(days: CalendarDay[], requested: string, duration: number): { planned_start: string; planned_finish: string } {
  if (!Number.isFinite(isoToDay(requested)) || !Number.isInteger(duration) || duration < 0) throw new Error("Fecha o duración inválida.");
  if (duration === 0) return { planned_start: requested, planned_finish: requested };
  const working = days.filter(d => d.working && d.date >= requested).sort((a, b) => a.date.localeCompare(b.date));
  if (working.length < duration) throw new Error("No hay suficientes días laborables dentro del horizonte del proyecto.");
  return { planned_start: working[0].date, planned_finish: working[duration - 1].date };
}
