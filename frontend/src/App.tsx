import React, { Suspense } from "react";
import { useStore, type PageKey } from "./state/store";
import ProjectsPage from "./pages/ProjectsPage";
import GanttPage from "./pages/GanttPage";
import MacroPage from "./pages/MacroPage";
import ResourcesPage from "./pages/ResourcesPage";
import CalendarsPage from "./pages/CalendarsPage";
import OptimizePage from "./pages/OptimizePage";
import MaintenancePage from "./pages/MaintenancePage";
import ControlPage from "./pages/ControlPage";
import PerformancePage from "./pages/PerformancePage";
import ReportsPage from "./pages/ReportsPage";
import ImportPage from "./pages/ImportPage";

interface NavItem { key: PageKey; label: string; ico: string; needsProject: boolean; onlyMode?: "mantenimiento" | "proyecto" }

const NAV: { section: string; items: NavItem[] }[] = [
  { section: "General", items: [
    { key: "proyectos", label: "Proyectos", ico: "▦", needsProject: false },
  ] },
  { section: "Planificación", items: [
    { key: "gantt", label: "Actividades y Gantt", ico: "≡", needsProject: true },
    { key: "macro", label: "Programación macro", ico: "▤", needsProject: true },
    { key: "mantenimiento", label: "Plan de mantenimiento", ico: "↻", needsProject: true, onlyMode: "mantenimiento" },
    { key: "optimizar", label: "Optimizar", ico: "⚙", needsProject: true },
    { key: "recursos", label: "Recursos", ico: "☺", needsProject: true },
    { key: "calendarios", label: "Calendarios", ico: "☷", needsProject: false },
  ] },
  { section: "Control", items: [
    { key: "control", label: "Avance y curva S", ico: "∫", needsProject: true },
    { key: "desempeno", label: "Desempeño", ico: "★", needsProject: true },
    { key: "informes", label: "Informes", ico: "▣", needsProject: true },
    { key: "importar", label: "Importar / Exportar", ico: "⇅", needsProject: false },
  ] },
];

const TITLES: Record<PageKey, string> = {
  proyectos: "Proyectos", gantt: "Actividades y Gantt", macro: "Programación macro", recursos: "Recursos",
  calendarios: "Calendarios laborales", optimizar: "Optimización de la programación", mantenimiento: "Plan de mantenimiento periódico",
  control: "Control: avance, curva S y valor ganado", desempeno: "Desempeño por trabajador", informes: "Informes automáticos",
  importar: "Importar y exportar (Excel)",
};

export default function App() {
  const { page, go, project, projectId, toasts, busy, theme, setTheme } = useStore();
  const flush = page === "gantt" || page === "macro";

  let body: React.ReactNode;
  if (!projectId && NAV.flatMap((s) => s.items).find((i) => i.key === page)?.needsProject) body = <ProjectsPage />;
  else switch (page) {
    case "proyectos": body = <ProjectsPage />; break;
    case "gantt": body = <GanttPage />; break;
    case "macro": body = <MacroPage />; break;
    case "recursos": body = <ResourcesPage />; break;
    case "calendarios": body = <CalendarsPage />; break;
    case "optimizar": body = <OptimizePage />; break;
    case "mantenimiento": body = <MaintenancePage />; break;
    case "control": body = <ControlPage />; break;
    case "desempeno": body = <PerformancePage />; break;
    case "informes": body = <ReportsPage />; break;
    case "importar": body = <ImportPage />; break;
    default: body = <ProjectsPage />;
  }

  if (projectId && page !== "proyectos" && project?.id !== projectId) body = <div className="empty" role="status">Cargando proyecto… Si la carga falló, vuelve a Proyectos para reintentar.</div>;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="1" y="2" width="8" height="3" rx="1" fill="#3987e5" /><rect x="5" y="7" width="9" height="3" rx="1" fill="#eb6834" /><rect x="3" y="12" width="6" height="3" rx="1" fill="#1baf7a" />
          </svg>
          <div>Ganttier<small>Planificación y control</small></div>
        </div>
        <nav className="nav">
          {NAV.map((s) => (
            <React.Fragment key={s.section}>
              <div className="nav-section">{s.section}</div>
              {s.items.map((it) => {
                if (it.onlyMode && project && project.mode !== it.onlyMode) return null;
                const disabled = it.needsProject && !projectId;
                return (
                  <button key={it.key} className={page === it.key ? "active" : ""} disabled={disabled}
                    title={disabled ? "Abre un proyecto primero" : it.label} onClick={() => go(it.key)}>
                    <span className="ico">{it.ico}</span>{it.label}
                  </button>
                );
              })}
            </React.Fragment>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span>v1.0</span>
          <button className="btn ghost small" style={{ color: "inherit" }} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title="Cambiar tema">{theme === "dark" ? "☀ Claro" : "☾ Oscuro"}</button>
        </div>
      </aside>
      <main className="main" aria-busy={!!busy} ref={el => { if (el) el.inert = !!busy; }}>
        <header className="topbar">
          <h1>{TITLES[page]}</h1>
          {project && page !== "proyectos" && (
            <>
              <span className="muted">·</span>
              <strong style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{project.name}</strong>
              <span className={`badge ${project.mode}`}>{project.mode === "mantenimiento" ? "Mantenimiento" : "Proyecto"}</span>
              {project.data_date && <span className="badge">Corte: {project.data_date.split("-").reverse().join("-")}</span>}
            </>
          )}
          <span className="spacer" />
        </header>
        <div className={`content${flush ? " flush" : ""}`}>
          <Suspense key={`${projectId ?? "global"}:${page}`} fallback={<div className="empty">Cargando…</div>}>{body}</Suspense>
        </div>
      </main>
      {busy && <div className="busy" role="status"><span className="spinner" />{busy}</div>}
      <div className="toasts" aria-live="polite">{toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>{t.text}</div>)}</div>
    </div>
  );
}
