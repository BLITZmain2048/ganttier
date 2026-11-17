// Tipos espejo de backend/ganttier/domain.py (contrato SPEC §4 y §10).
export type ISODate = string; // "YYYY-MM-DD"
export type Mode = "proyecto" | "mantenimiento";
export type RelType = "FS" | "SS" | "FF" | "SF";
export type ConstraintType = "ASAP" | "ALAP" | "SNET" | "SNLT" | "FNET" | "FNLT" | "MSO" | "MFO";
export type Status = "no_iniciada" | "en_curso" | "terminada";

export interface CalendarException {
  date: ISODate;
  working: boolean;
  hours: number;
  reason: string;
}

export interface Calendar {
  id: number;
  name: string;
  workweek: string;
  hours_per_day: number;
  holidays_country: string | null;
  shift_pattern: string | null;
  shift_anchor: ISODate | null;
  holidays_apply_to_shift: boolean;
  exceptions: CalendarException[];
}

export interface CalendarDay {
  date: ISODate;
  hours: number;
  working: boolean;
  reason: string;
}

export interface Project {
  id: number;
  name: string;
  mode: Mode;
  start_date: ISODate;
  end_date: ISODate | null;
  data_date: ISODate | null;
  calendar_id: number | null;
  description: string;
  currency: string;
  active_baseline_id: number | null;
  counts?: Record<string, number>;
}

export type ResourceKind = "trabajador" | "cuadrilla" | "equipo" | "material";

export interface Resource {
  id: number;
  name: string;
  kind: ResourceKind;
  skills: string[];
  capacity_hours_day: number;
  max_units: number;
  calendar_id: number | null;
  cost_hour: number;
  overtime_cost_hour: number;
  max_overtime_hours_day: number;
  project_id: number | null;
  active: boolean;
  email: string;
}

export interface WBSNode {
  id: number;
  project_id: number;
  code: string;
  name: string;
  parent_id: number | null;
  sort_order: number;
}

export interface Activity {
  id: number;
  project_id: number;
  code: string;
  name: string;
  wbs_id: number | null;
  duration_days: number;
  work_hours: number;
  cost_budget: number;
  calendar_id: number | null;
  macro_month: string | null;
  window_start: ISODate | null;
  window_end: ISODate | null;
  window_hard: boolean;
  constraint_type: ConstraintType;
  constraint_date: ISODate | null;
  priority: number;
  planned_start: ISODate | null;
  planned_finish: ISODate | null;
  early_start: ISODate | null;
  early_finish: ISODate | null;
  late_start: ISODate | null;
  late_finish: ISODate | null;
  total_float: number | null;
  free_float: number | null;
  critical: boolean;
  actual_start: ISODate | null;
  actual_finish: ISODate | null;
  percent_complete: number;
  remaining_duration: number | null;
  actual_hours: number;
  actual_cost: number;
  status: Status;
  periodic_task_id: number | null;
  occurrence_index: number | null;
  due_date: ISODate | null;
  location: string;
  equipment: string;
  voltage_kv: number | null;
  requires_outage: boolean;
  discipline: string;
  required_skill: string;
  crew_size: number;
  notes: string;
  locked: boolean;
}

export type ActivityPatch = Partial<Activity> & { id: number };

export interface Columnar {
  ids: number[];
  codes: string[];
  names: string[];
  starts: (ISODate | null)[];
  finishes: (ISODate | null)[];
  es?: (ISODate | null)[];
  ef?: (ISODate | null)[];
  ls?: (ISODate | null)[];
  lf?: (ISODate | null)[];
  total_float?: (number | null)[];
  critical?: (boolean | number)[];
  pct?: number[];
  wbs_ids?: (number | null)[];
  baseline_starts?: (ISODate | null)[];
  baseline_finishes?: (ISODate | null)[];
  window_starts?: (ISODate | null)[];
  window_ends?: (ISODate | null)[];
  kinds?: string[];
  // Optional extensions; backend contract pending (frontend_revision.md).
  durations?: (number | null)[];
  work_hours?: (number | null)[];
  macro_months?: (string | null)[];
  skills?: string[];
  locations?: string[];
  statuses?: string[];
  outages?: (boolean | number)[];
  window_hards?: (boolean | number)[];
}

export interface Relation {
  id: number;
  project_id: number;
  pred_id: number;
  succ_id: number;
  type: RelType;
  lag_days: number;
}

export interface Assignment {
  id: number;
  activity_id: number;
  resource_id: number | null;
  skill: string;
  units: number;
  work_hours: number;
  actual_hours: number;
}

export type Frequency = "D" | "W" | "M" | "Q" | "S" | "A" | "ND" | "NW" | "NM" | "NA";

export interface PeriodicTask {
  id: number;
  project_id: number;
  code: string;
  name: string;
  frequency: Frequency;
  interval: number;
  duration_days: number;
  work_hours: number;
  crew_size: number;
  required_skill: string;
  resource_id: number | null;
  first_due: ISODate | null;
  preferred_months: string;
  preferred_weekday: number | null;
  day_of_month: number | null;
  tol_before_days: number;
  tol_after_days: number;
  window_hard: boolean;
  location: string;
  equipment: string;
  voltage_kv: number | null;
  requires_outage: boolean;
  discipline: string;
  calendar_id: number | null;
  wbs_code: string;
  priority: number;
  cost_per_occurrence: number;
  active: boolean;
  horizon_end: ISODate | null;
}

export interface Baseline {
  id: number;
  project_id: number;
  name: string;
  created_at: string;
}

export interface ProgressEntry {
  id: number;
  activity_id: number;
  date: ISODate;
  percent_complete: number;
  actual_hours: number;
  actual_cost: number;
  resource_id: number | null;
  actual_start: ISODate | null;
  actual_finish: ISODate | null;
  note: string;
}

export interface ObjectiveWeights {
  level: number;
  makespan: number;
  window: number;
  cost: number;
}

export interface OptimizeOptions {
  method: "auto" | "heuristic" | "cpsat";
  time_limit_s: number;
  level_period: "day" | "week";
  group_by: "resource" | "skill";
  respect_resource_limits: boolean;
  allow_overtime: boolean;
  assign_workers: boolean;
  cpsat_max_activities: number;
  seed: number;
  group_outages: boolean;
}

export interface LoadSeries {
  key: string;
  capacity: number[];
  load: number[];
}

export interface LoadProfile {
  period: string;
  labels: ISODate[];
  series: LoadSeries[];
}

export interface OptimizeMetrics {
  finish: ISODate | null;
  peak_load: number;
  load_std: number;
  overload_hours: number;
  overtime_hours: number;
  overtime_cost: number;
  days_out_of_window: number;
  activities_out_of_window: number;
  objective_value: number;
}

export interface OptimizeResponse {
  starts: Record<number, ISODate>;
  finishes: Record<number, ISODate>;
  worker_assignment: Record<number, number>;
  run_id?: number;
  before: OptimizeMetrics | null;
  after: OptimizeMetrics | null;
  profile_before: LoadProfile | null;
  profile_after: LoadProfile | null;
  method_used: string;
  status: string;
  elapsed_s: number;
  warnings: string[];
  applied?: boolean;
}

export interface ScheduleRun {
  id: number;
  project_id: number;
  created_at: string;
  kind: string;
  weights: ObjectiveWeights | string | null;
  metrics_before: OptimizeMetrics | string | null;
  metrics_after: OptimizeMetrics | string | null;
  applied: boolean | number;
  status: string | null;
  elapsed_s: number | null;
}

export interface CPMResponse {
  run_id?: number;
  finish: ISODate | null;
  critical_count: number;
  warnings: string[];
}

export interface SCurve {
  period: string;
  basis: string;
  labels: ISODate[];
  planned_cum: number[];
  earned_cum: (number | null)[];
  actual_cum: (number | null)[];
  forecast_cum: (number | null)[];
  planned_abs: number[];
  earned_abs: (number | null)[];
  actual_abs: (number | null)[];
  forecast_abs: (number | null)[];
  budget_total: number;
  data_date: ISODate | null;
  forecast_finish: ISODate | null;
  planned_finish: ISODate | null;
}

export interface EVMResult {
  data_date: ISODate | null;
  basis: string;
  BAC: number;
  PV: number;
  EV: number;
  AC: number;
  SV: number;
  CV: number;
  SPI: number | null;
  CPI: number | null;
  ES_days: number | null;
  SPI_t: number | null;
  EAC: number | null;
  ETC: number | null;
  VAC: number | null;
  TCPI: number | null;
  planned_finish: ISODate | null;
  forecast_finish: ISODate | null;
  percent_planned: number;
  percent_complete: number;
}

export interface WorkerKPI {
  resource_id: number;
  name: string;
  assigned_activities: number;
  completed_activities: number;
  completed_on_time: number;
  due_in_period: number;
  compliance_pct: number | null;
  planned_hours: number;
  earned_hours: number;
  actual_hours: number;
  hh_efficiency: number | null;
  avg_delay_days: number | null;
  SPI: number | null;
  CPI: number | null;
  pending_activities: number;
}

export interface ReportFile {
  format: string;
  filename: string;
  url: string;
}

export interface ImportIssue {
  sheet: string;
  row: number;
  field: string;
  message: string;
  severity: "error" | "advertencia";
}

export interface ImportResponse {
  summary: Record<string, number | string>;
  errors: ImportIssue[];
}

export interface ApiErrorBody {
  detail: string | { msg: string }[];
  errors?: { row?: number; field?: string; message: string; sheet?: string }[];
}

export interface AutoReportConfig {
  enabled: boolean;
  weekly: boolean;
  monthly: boolean;
  annual: boolean;
  formats: string[];
  weekday: number;
  keep_last: number;
}
