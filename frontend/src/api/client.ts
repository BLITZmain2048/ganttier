import type { AutoReportConfig,
  Activity, ActivityPatch, Assignment, Baseline, Calendar, CalendarDay, Columnar, CPMResponse,
  EVMResult, ImportResponse, LoadProfile, ObjectiveWeights, OptimizeOptions, OptimizeResponse,
  PeriodicTask, ProgressEntry, Project, Relation, ReportFile, Resource, SCurve, ScheduleRun,
  WBSNode, WorkerKPI, ApiErrorBody,
} from "./types";

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody | null;
  constructor(status: number, message: string, body: ApiErrorBody | null) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

const BASE = "/api";

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

function errorMessage(body: ApiErrorBody | null, status: number): string {
  if (!body) return `Error ${status} del servidor`;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
  return `Error ${status}`;
}

async function request<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) {
      init.body = body as FormData;
    } else {
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
  }
  let res: Response;
  try {
    res = await fetch(BASE + path, init);
  } catch {
    throw new ApiError(0, "No se pudo conectar con el servidor de Ganttier. ¿Está en ejecución iniciar.bat?", null);
  }
  if (!res.ok) {
    let parsed: ApiErrorBody | null = null;
    try { parsed = (await res.json()) as ApiErrorBody; } catch { parsed = null; }
    throw new ApiError(res.status, errorMessage(parsed, res.status), parsed);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try { return (await res.json()) as T; }
    catch { throw new ApiError(res.status, "El servidor devolvió JSON inválido.", null); }
  }
  const text = await res.text();
  if (!text && method !== "GET") return undefined as T;
  throw new ApiError(res.status, "El servidor devolvió una respuesta que no es JSON. Revisa la ruta de la API.", null);
}

const get = <T,>(p: string) => request<T>("GET", p);
const post = <T,>(p: string, b?: unknown) => request<T>("POST", p, b ?? {});
const put = <T,>(p: string, b: unknown) => request<T>("PUT", p, b);
const patch = <T,>(p: string, b: unknown) => request<T>("PATCH", p, b);
const del = <T,>(p: string) => request<T>("DELETE", p);

export const api = {
  health: () => get<{ status: string }>("/health"),

  // proyectos
  projects: () => get<Project[]>("/projects"),
  project: (id: number) => get<Project>(`/projects/${id}`),
  createProject: (p: Partial<Project>) => post<Project>("/projects", p),
  updateProject: (id: number, p: Partial<Project>) => put<Project>(`/projects/${id}`, p),
  deleteProject: (id: number) => del<void>(`/projects/${id}`),
  duplicateProject: (id: number) => post<Project>(`/projects/${id}/duplicate`),
  demo: (kind: "proyecto" | "mantenimiento", n?: number) => post<Project>(`/demo/${kind}${qs({ n })}`),

  // calendarios
  calendars: () => get<Calendar[]>("/calendars"),
  createCalendar: (c: Partial<Calendar>) => post<Calendar>("/calendars", c),
  updateCalendar: (id: number, c: Partial<Calendar>) => put<Calendar>(`/calendars/${id}`, c),
  deleteCalendar: (id: number) => del<void>(`/calendars/${id}`),
  calendarDays: (id: number, start: string, end: string) =>
    get<CalendarDay[]>(`/calendars/${id}/days${qs({ start, end })}`),

  // wbs
  wbs: (pid: number) => get<WBSNode[]>(`/projects/${pid}/wbs`),
  createWbs: (pid: number, w: Partial<WBSNode>) => post<WBSNode>(`/projects/${pid}/wbs`, w),
  updateWbs: (id: number, w: Partial<WBSNode>) => put<WBSNode>(`/wbs/${id}`, w),
  deleteWbs: (id: number) => del<void>(`/wbs/${id}`),

  // actividades
  activities: (pid: number, params: Record<string, string | number | undefined> = {}) =>
    get<Activity[] | { items: Activity[]; total: number }>(`/projects/${pid}/activities${qs(params)}`),
  // SPEC has no GET /activities/{id}; use the documented paginated collection.
  activity: async (pid: number, id: number, code: string): Promise<Activity> => {
    let offset = 0;
    while (true) {
      const response = await get<Activity[] | { items: Activity[]; total: number }>(`/projects/${pid}/activities${qs({ offset, limit: 1000, q: code })}`);
      const items = asList(response), found = items.find(a => a.id === id);
      if (found) return found;
      offset += items.length;
      if (!items.length || (!Array.isArray(response) && offset >= response.total)) break;
    }
    throw new ApiError(404, "La actividad ya no está disponible. Actualiza la vista.", null);
  },
  columnar: (pid: number) => get<Columnar>(`/projects/${pid}/activities/columnar`),
  createActivities: (pid: number, a: Partial<Activity> | Partial<Activity>[]) =>
    post<Activity | Activity[]>(`/projects/${pid}/activities`, a),
  updateActivity: (id: number, a: Partial<Activity>) => put<Activity>(`/activities/${id}`, a),
  deleteActivity: (id: number) => del<void>(`/activities/${id}`),
  patchActivities: (pid: number, list: ActivityPatch[]) => patch<unknown>(`/projects/${pid}/activities`, list),

  // relaciones
  relations: (pid: number) => get<Relation[]>(`/projects/${pid}/relations`),
  createRelation: (pid: number, r: Partial<Relation>) => post<Relation>(`/projects/${pid}/relations`, r),
  deleteRelation: (id: number) => del<void>(`/relations/${id}`),

  // recursos
  resources: (projectId?: number) => get<Resource[]>(`/resources${qs({ project_id: projectId })}`),
  createResource: (r: Partial<Resource>) => post<Resource>("/resources", r),
  updateResource: (id: number, r: Partial<Resource>) => put<Resource>(`/resources/${id}`, r),
  deleteResource: (id: number) => del<void>(`/resources/${id}`),

  // asignaciones
  assignments: (pid: number, activityId?: number) => get<Assignment[]>(`/projects/${pid}/assignments${qs({ activity_id: activityId })}`),
  createAssignment: (pid: number, a: Partial<Assignment>) => post<Assignment>(`/projects/${pid}/assignments`, a),
  updateAssignment: (id: number, a: Partial<Assignment>) => put<Assignment>(`/assignments/${id}`, a),
  deleteAssignment: (id: number) => del<void>(`/assignments/${id}`),

  // mantenimiento
  periodicTasks: (pid: number) => get<PeriodicTask[]>(`/projects/${pid}/periodic-tasks`),
  createPeriodicTask: (pid: number, t: Partial<PeriodicTask>) => post<PeriodicTask>(`/projects/${pid}/periodic-tasks`, t),
  updatePeriodicTask: (id: number, t: Partial<PeriodicTask>) => put<PeriodicTask>(`/periodic-tasks/${id}`, t),
  deletePeriodicTask: (id: number) => del<void>(`/periodic-tasks/${id}`),
  generateOccurrences: (pid: number, body: { horizon_start: string; horizon_end: string; replace: boolean }) =>
    post<{ created: number }>(`/projects/${pid}/maintenance/generate`, body),

  // programación
  cpm: (pid: number) => post<CPMResponse>(`/projects/${pid}/schedule/cpm`),
  optimize: (pid: number, weights: ObjectiveWeights, options: OptimizeOptions, apply: boolean) =>
    post<OptimizeResponse>(`/projects/${pid}/schedule/optimize`, { weights, options, apply }),
  runs: (pid: number) => get<ScheduleRun[]>(`/projects/${pid}/schedule/runs`),
  applyRun: (pid: number, runId: number) => post<unknown>(`/projects/${pid}/schedule/runs/${runId}/apply`).then(() => true),
  load: (pid: number, group: "resource" | "skill", period: "day" | "week" | "month", from?: string, to?: string) =>
    get<LoadProfile>(`/projects/${pid}/load${qs({ group, period, from, to })}`),

  // control
  baselines: (pid: number) => get<Baseline[]>(`/projects/${pid}/baselines`),
  createBaseline: (pid: number, name: string) => post<Baseline>(`/projects/${pid}/baselines`, { name }),
  deleteBaseline: (id: number) => del<void>(`/baselines/${id}`),
  activateBaseline: (id: number) => post<unknown>(`/baselines/${id}/activate`).then(() => true),
  progress: (pid: number) => get<ProgressEntry[]>(`/projects/${pid}/progress`),
  addProgress: (pid: number, list: Partial<ProgressEntry>[]) => post<unknown>(`/projects/${pid}/progress`, list).then(() => true),
  deleteProgress: (id: number) => del<void>(`/progress/${id}`),
  setDataDate: (pid: number, data_date: string) => put<unknown>(`/projects/${pid}/data-date`, { data_date }).then(() => true),
  scurve: (pid: number, period: string, basis: string) =>
    get<SCurve>(`/projects/${pid}/control/scurve${qs({ period, basis })}`),
  evm: (pid: number, basis?: string) => get<EVMResult>(`/projects/${pid}/control/evm${qs({ basis })}`),
  performance: (pid: number, start: string, end: string) =>
    get<WorkerKPI[]>(`/projects/${pid}/control/performance${qs({ start, end })}`),

  // informes
  reports: (pid: number, period_type: string, period_start: string, formats: string[]) =>
    post<ReportFile[]>(`/projects/${pid}/reports`, { period_type, period_start, formats }),

  // importación / exportación
  templateUrl: (kind: "proyecto" | "mantenimiento") => `${BASE}/templates/${kind}`,
  exportUrl: (pid: number) => `${BASE}/projects/${pid}/export.xlsx`,
  autoreports: (pid: number) => get<AutoReportConfig>(`/projects/${pid}/autoreports`),
  saveAutoreports: (pid: number, c: AutoReportConfig) => put<AutoReportConfig>(`/projects/${pid}/autoreports`, c),
  runAutoreports: (pid: number) => post<unknown>(`/projects/${pid}/autoreports/run`),
  autoreportsLog: (pid: number) => get<unknown>(`/projects/${pid}/autoreports/log`),
  outagesUrl: (pid: number, from?: string, to?: string) => `${BASE}/projects/${pid}/outages.xlsx${qs({ from, to })}`,
  importFile: (pid: number, file: File, mode: "merge" | "replace", dryRun: boolean) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<ImportResponse>("POST", `/projects/${pid}/import${qs({ mode, dry_run: dryRun })}`, fd, true);
  },
  importNewProject: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Project>("POST", "/import/new-project", fd, true);
  },
};

/** Normaliza respuestas paginadas o listas simples. */
export function asList<T>(r: T[] | { items: T[]; total: number }): T[] {
  return Array.isArray(r) ? r : r.items;
}
