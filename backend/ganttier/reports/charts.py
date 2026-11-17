"""Gráficos reproducibles sin interfaz gráfica, con backend Agg y salida PNG."""
from __future__ import annotations

from datetime import date
from io import BytesIO
from threading import RLock

import matplotlib
matplotlib.use("Agg")
from matplotlib import dates as mdates
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
import numpy as np

from ganttier.domain import ReportData
from .common import cutoff, display

_LOCK = RLock()


def load_chart_data(profile):
    """Hasta ocho recursos y Otros; los huecos no se convierten en capacidad."""
    ranked = sorted(profile.series, key=lambda s: sum(float(v) for v in s.load if v is not None and np.isfinite(v)), reverse=True)
    groups = [(s.key, np.asarray(s.load, dtype=float)) for s in ranked[:8]]
    if len(ranked) > 8:
        groups.append(('Otros recursos', np.sum(np.asarray([s.load for s in ranked[8:]], dtype=float), axis=0)))
    capacity = np.sum(np.asarray([s.capacity for s in ranked], dtype=float), axis=0) if ranked else np.full(len(profile.labels), np.nan)
    return groups, capacity


def draw(report: ReportData, kind: str) -> bytes:
    """Dibuja una sección a 150 dpi; preserva huecos de datos con NaN."""
    with _LOCK:
        figure = Figure(figsize=(9, 4.5), dpi=150, layout="constrained")
        FigureCanvasAgg(figure)
        ax = figure.subplots()
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.2)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: display(float(value))))
        if kind == 'skill_load':
            figure.clear()
            profile = report.skill_load
            series = profile.series[:6]
            columns = 2 if len(series) > 3 else 1
            axes = figure.subplots(max(1, (len(series) + columns - 1) // columns), columns, squeeze=False).ravel()
            for extra in axes[len(series):]:
                extra.set_visible(False)
            for axis, item in zip(axes, series):
                x = np.arange(len(profile.labels))
                axis.bar(x, np.asarray(item.load, dtype=float), color='#356A9A', label='Carga HH')
                axis.plot(x, np.asarray(item.capacity, dtype=float), '--', color='#17354A', label='Capacidad HH')
                axis.set_title(str(item.key)[:70], fontsize=10)
                stride = max(1, len(x) // 5)
                axis.set_xticks(x[::stride], profile.labels[::stride], fontsize=7)
                axis.legend(fontsize=7, loc='upper right')
            if not series:
                axes[0].text(.5, .5, 'Sin datos por habilidad', ha='center')
        elif kind == "scurve":
            c = report.scurve
            x = [date.fromisoformat(v) for v in c.labels]
            for name, values, color, style in (("Planificada", c.planned_cum, "#356A9A", "-"), ("Ganada", c.earned_cum, "#17734B", "-"), ("Real", c.actual_cum, "#C78027", "-"), ("Proyección", c.forecast_cum, "#9B528B", "--")):
                if x:
                    ax.plot(x, np.asarray(values, dtype=float), label=name, color=color, linestyle=style, linewidth=2)
            cut = cutoff(report)
            if cut:
                ax.axvline(cut, color="#3B4854", linestyle=":", label=f"Corte {display(cut)}")
            ax.set_ylabel("Porcentaje del presupuesto (%)")
            ax.set_title("Curva S · plan, avance y proyección")
            if x or cut:
                ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m-%Y"))
                ax.legend(loc="best", fontsize=8)
        elif kind == "load":
            load = report.load
            x = np.arange(len(load.labels))
            bottom = np.zeros(len(x))
            groups, capacity = load_chart_data(load)
            for name, values in groups:
                ax.bar(x, values, bottom=bottom, label=str(name)[:48])
                bottom += values
            if len(x):
                ax.plot(x, capacity, color="#263747", linestyle="--", label="Capacidad total", linewidth=2)
                stride = max(1, len(x) // 8)
                ax.set_xticks(x[::stride], [display(date.fromisoformat(v)) for v in load.labels[::stride]], rotation=20, fontsize=8)
            ax.set_ylabel("Horas-hombre (HH)")
            ax.set_title("Carga apilada por recurso o rol")
            if load.series:
                if len(x):
                    finite_values = np.concatenate((bottom, capacity))
                    finite_values = finite_values[np.isfinite(finite_values)]
                    ax.set_ylim(0, max(float(finite_values.max()) if len(finite_values) else 1, 1) * 1.2)
                ax.legend(loc="upper left", fontsize=7, ncols=min(3, len(groups) + 1))
                if not np.all(np.isfinite(bottom)) or not np.all(np.isfinite(capacity)):
                    ax.set_xlabel('Huecos: carga o capacidad incompletas')
        elif kind == "performance":
            workers = report.performance[:20]
            y = np.arange(len(workers))
            values = [p.compliance_pct if p.compliance_pct is not None else np.nan for p in workers]
            ax.barh(y, values, color="#087E8B", height=.55)
            ax.set_yticks(y, [str(p.name or '—')[:45] for p in workers], fontsize=8)
            ax.invert_yaxis()
            if len(workers) < 3:
                ax.set_ylim(2.5, -.5)
            ax.set_xlim(0, 105)
            ax.set_xlabel("Cumplimiento (%) · ausente = sin datos")
            ax.set_title("Desempeño por trabajador" + (" · primeros 20" if len(report.performance) > 20 else ""))
        elif kind == "gantt":
            tasks = report.lookahead[:20]
            ax.set_yticks(range(len(tasks)), [r.code for r in tasks], fontsize=8)
            for i, row in enumerate(tasks):
                if row.start is not None and row.finish is not None:
                    ax.barh(i, max(1, (row.finish - row.start).days + 1), left=mdates.date2num(row.start), color="#356A9A", height=.6)
            if any(r.start and r.finish for r in tasks):
                ax.xaxis_date()
                starts = [r.start for r in tasks if r.start and r.finish]
                finishes = [r.finish for r in tasks if r.start and r.finish]
                ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, ((max(finishes) - min(starts)).days + 1) // 7)))
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m-%Y"))
            ax.invert_yaxis()
            if len(tasks) < 3:
                ax.set_ylim(2.5, -.5)
            ax.set_title("Lookahead del período siguiente" + (" · primeras 20" if len(report.lookahead) > 20 else ""))
        else:
            raise ValueError(f"Gráfico no admitido: {kind}.")
        if kind != "skill_load" and not ax.has_data():
            ax.text(.5, .5, "Sin datos para el período", ha="center", va="center", transform=ax.transAxes)
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=150, facecolor="white")
        figure.clear()
        return buffer.getvalue()
