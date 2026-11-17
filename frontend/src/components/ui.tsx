import React from "react";

export function Modal({ title, onClose, children, footer, width }: {
  title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; width?: number;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const close = React.useRef(onClose);
  close.current = onClose;
  React.useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const el = ref.current;
    if (!el) return;
    const focusable = () => Array.from(el.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]'))
      .filter(node => node.getClientRects().length > 0);
    if (!el.contains(document.activeElement)) (focusable()[0] ?? el).focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close.current(); }
      if (event.key !== "Tab") return;
      const nodes = focusable(), first = nodes[0], last = nodes[nodes.length - 1];
      if (!first) { event.preventDefault(); el.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === el)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    el.addEventListener("keydown", key);
    return () => { el.removeEventListener("keydown", key); if (previous?.isConnected) previous.focus(); };
  }, []);
  return (
    <div className="modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} tabIndex={-1} className="modal" style={width ? { width: `min(${width}px, 94vw)` } : undefined} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-h"><span style={{ flex: 1 }}>{title}</span><button className="btn ghost small" onClick={onClose} aria-label="Cerrar">✕</button></div>
        <div className="modal-b">{children}</div>
        {footer && <div className="modal-f">{footer}</div>}
      </div>
    </div>
  );
}

export function Card({ title, actions, children, bodyStyle }: {
  title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; bodyStyle?: React.CSSProperties;
}) {
  return (
    <div className="card">
      {title !== undefined && <div className="card-h">{title}<span className="spacer" />{actions}</div>}
      <div className="card-b" style={bodyStyle}>{children}</div>
    </div>
  );
}

export function Kpi({ label, value, sub, status }: { label: string; value: React.ReactNode; sub?: React.ReactNode; status?: "good" | "warning" | "critical" | null }) {
  return (
    <div className="kpi">
      <div className="l">{label}</div>
      <div className="v">{value}</div>
      {(sub || status) && (
        <div className="s">
          {status && <span className={`sem ${status}`}><i />{status === "good" ? "En plan" : status === "warning" ? "Atención" : "Crítico"}</span>}
          {status && sub ? " · " : ""}{sub}
        </div>
      )}
    </div>
  );
}

export function spiStatus(v: number | null | undefined): "good" | "warning" | "critical" | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  if (v >= 0.95) return "good";
  if (v >= 0.85) return "warning";
  return "critical";
}

export function Field({ label, children, style }: { label: string; children: React.ReactNode; style?: React.CSSProperties }) {
  return <label className="fld" style={style}>{label}{children}</label>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function NumInput({ value, onChange, step, min, className, style }: {
  value: number | null | undefined; onChange: (v: number) => void; step?: number; min?: number; className?: string; style?: React.CSSProperties;
}) {
  return (
    <input className={className ?? "inp"} style={style} type="number" step={step ?? "any"} min={min}
      value={value === null || value === undefined || Number.isNaN(value) ? "" : value}
      onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value.replace(",", ".")))} />
  );
}

export function TagsInput({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [txt, setTxt] = React.useState("");
  const add = () => {
    const parts = txt.split(",").map((s) => s.trim()).filter(Boolean);
    if (parts.length) onChange(Array.from(new Set([...value, ...parts])));
    setTxt("");
  };
  return (
    <div className="inp" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", height: "auto", minHeight: 30, padding: "2px 6px", gap: 2 }}>
      {value.map((t) => <span key={t} className="tag">{t}<button onClick={() => onChange(value.filter((x) => x !== t))} aria-label={`Quitar ${t}`}>×</button></span>)}
      <input style={{ border: 0, outline: 0, flex: 1, minWidth: 80, background: "transparent", height: 24 }} value={txt} placeholder={placeholder}
        onChange={(e) => setTxt(e.target.value)} onBlur={add}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }} />
    </div>
  );
}

/** Descarga un archivo desde una URL de la API. */
export function download(url: string, filename?: string) {
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
