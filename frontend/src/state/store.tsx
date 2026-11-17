import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Calendar, Project, Resource } from "../api/types";

export type PageKey =
  | "proyectos" | "gantt" | "macro" | "recursos" | "calendarios" | "optimizar"
  | "mantenimiento" | "control" | "desempeno" | "informes" | "importar";

export interface Toast {
  id: number;
  kind: "ok" | "error" | "info";
  text: string;
}

interface Store {
  page: PageKey;
  go: (p: PageKey, projectId?: number) => void;
  projectId: number | null;
  project: Project | null;
  projects: Project[];
  reloadProjects: () => Promise<void>;
  reloadProject: () => Promise<void>;
  calendars: Calendar[];
  reloadCalendars: () => Promise<void>;
  resources: Resource[];
  reloadResources: () => Promise<void>;
  toasts: Toast[];
  toast: (text: string, kind?: Toast["kind"]) => void;
  fail: (e: unknown) => void;
  /** Se incrementa cuando cambian las fechas de las actividades (para recargar vistas). */
  scheduleVersion: number;
  bumpSchedule: () => void;
  theme: "light" | "dark";
  setTheme: (t: "light" | "dark") => void;
  busy: string | null;
  setBusy: (s: string | null) => void;
}

const Ctx = createContext<Store | null>(null);

function parseHash(): { page: PageKey; projectId: number | null } {
  const h = window.location.hash.replace(/^#\/?/, "");
  const parts = h.split("/");
  if (parts[0] === "p" && parts[1]) {
    const id = Number(parts[1]);
    const candidate = parts[2] || "gantt";
    const page = (["proyectos", "gantt", "macro", "recursos", "calendarios", "optimizar", "mantenimiento", "control", "desempeno", "informes", "importar"].includes(candidate) ? candidate : "gantt") as PageKey;
    return { page, projectId: Number.isSafeInteger(id) && id > 0 ? id : null };
  }
  if (parts[0] === "importar") return { page: "importar", projectId: null };
  if (parts[0] === "calendarios") return { page: "calendarios", projectId: null };
  return { page: "proyectos", projectId: null };
}

function readTheme(): "light" | "dark" {
  try {
    const t = localStorage.getItem("ganttier.theme");
    if (t === "light" || t === "dark") return t;
  } catch { /* sin almacenamiento */ }
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const initial = parseHash();
  const [page, setPage] = useState<PageKey>(initial.page);
  const [projectId, setProjectId] = useState<number | null>(initial.projectId);
  const [project, setProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [resources, setResources] = useState<Resource[]>([]);
  const [resourcesScope, setResourcesScope] = useState<number | null | undefined>(undefined);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [scheduleVersion, setScheduleVersion] = useState(0);
  const [theme, setThemeState] = useState<"light" | "dark">(readTheme);
  const [busy, setBusy] = useState<string | null>(null);
  const toastId = useRef(1);
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  const projectRequest = useRef(0), resourceRequest = useRef(0);
  const timers = useRef(new Set<number>());
  useEffect(() => () => { timers.current.forEach(clearTimeout); projectRequest.current++; resourceRequest.current++; }, []);

  const toast = useCallback((text: string, kind: Toast["kind"] = "ok") => {
    const id = toastId.current++;
    setToasts((t) => [...t, { id, kind, text }]);
    const timer = window.setTimeout(() => { timers.current.delete(timer); setToasts(t => t.filter(x => x.id !== id)); }, kind === "error" ? 9000 : 4000);
    timers.current.add(timer);
  }, []);

  const fail = useCallback((e: unknown) => {
    if (e instanceof ApiError) {
      const extra = e.body?.errors?.slice(0, 3).map((x) => `${x.row ? `fila ${x.row}: ` : ""}${x.message}`).join(" · ");
      toast(extra ? `${e.message} — ${extra}` : e.message, "error");
    } else if (e instanceof Error) toast(e.message, "error");
    else toast(String(e), "error");
  }, [toast]);

  const go = useCallback((p: PageKey, pid?: number) => {
    const id = pid ?? projectId;
    if (p === "proyectos") window.location.hash = "#/";
    else if ((p === "calendarios" || p === "importar") && !id) window.location.hash = `#/${p}`;
    else if (id) window.location.hash = `#/p/${id}/${p}`;
  }, [projectId]);

  useEffect(() => {
    const onHash = () => {
      const h = parseHash();
      setPage(h.page);
      setProjectId(h.projectId);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const reloadProjects = useCallback(async () => {
    try { setProjects(await api.projects()); } catch (e) { fail(e); }
  }, [fail]);

  const reloadProject = useCallback(async () => {
    if (!projectId) { setProject(null); return; }
    const request = ++projectRequest.current;
    try { const result = await api.project(projectId); if (request === projectRequest.current && currentProject.current === projectId) setProject(result); } catch (e) { if (request === projectRequest.current && currentProject.current === projectId) { fail(e); setProject(null); } }
  }, [projectId, fail]);

  const reloadCalendars = useCallback(async () => {
    try { setCalendars(await api.calendars()); } catch (e) { fail(e); }
  }, [fail]);

  const reloadResources = useCallback(async () => {
    const request = ++resourceRequest.current;
    try { const result = await api.resources(projectId ?? undefined); if (request === resourceRequest.current && currentProject.current === projectId) { setResources(result); setResourcesScope(projectId); } } catch (e) { if (request === resourceRequest.current && currentProject.current === projectId) { setResources([]); fail(e); } }
  }, [projectId, fail]);

  useEffect(() => { void reloadProjects(); void reloadCalendars(); }, [reloadProjects, reloadCalendars]);
  useEffect(() => { void reloadProject(); void reloadResources(); }, [reloadProject, reloadResources]);

  const setTheme = useCallback((t: "light" | "dark") => {
    document.documentElement.setAttribute("data-theme", t);
    setThemeState(t);
    try { localStorage.setItem("ganttier.theme", t); } catch { /* sin almacenamiento */ }
  }, []);

  useEffect(() => { document.documentElement.setAttribute("data-theme", theme); }, [theme]);

  const bumpSchedule = useCallback(() => setScheduleVersion((v) => v + 1), []);

  const value = useMemo<Store>(() => ({
    page, go, projectId, project: project?.id === projectId ? project : null, projects, reloadProjects, reloadProject, calendars, reloadCalendars,
    resources: resourcesScope === projectId ? resources : [], reloadResources, toasts, toast, fail, scheduleVersion, bumpSchedule, theme, setTheme, busy, setBusy,
  }), [page, go, projectId, project, projects, reloadProjects, reloadProject, calendars, reloadCalendars,
    resources, resourcesScope, reloadResources, toasts, toast, fail, scheduleVersion, bumpSchedule, theme, setTheme, busy]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("StoreProvider ausente");
  return s;
}

/** Ejecuta una acción asíncrona mostrando indicador de ocupado y errores. */
export function useAction() {
  const running = useRef(false);
  const { setBusy, fail } = useStore();
  return useCallback(async <T,>(label: string, fn: () => Promise<T>): Promise<T | undefined> => {
    if (running.current) return undefined;
    running.current = true; setBusy(label);
    try { return await fn(); } catch (e) { fail(e); return undefined; } finally { running.current = false; setBusy(null); }
  }, [setBusy, fail]);
}
