"""Despacho público de informes con escritura atómica."""
from __future__ import annotations

from importlib import import_module
from pathlib import Path
from tempfile import NamedTemporaryFile

from ganttier.domain import ReportData
from .common import PERIODS


def render(report: ReportData, fmt: str, out_path: Path) -> Path:
    """Genera xlsx/pdf/docx/html; conserva el archivo previo si ocurre un error."""
    fmt = fmt.lower().strip()
    if fmt not in {"xlsx", "pdf", "docx", "html"}:
        raise ValueError("Formato no admitido; use xlsx, pdf, docx o html.")
    if report.period_type not in PERIODS:
        raise ValueError("Período no admitido; use weekly, monthly o annual.")
    if report.period_start > report.period_end:
        raise ValueError("La fecha final del período es anterior a la inicial.")
    curve = report.scurve
    for name in ("planned_cum", "earned_cum", "actual_cum", "forecast_cum", "planned_abs", "earned_abs", "actual_abs", "forecast_abs"):
        if len(getattr(curve, name)) != len(curve.labels):
            raise ValueError(f"Curva S inconsistente: la serie {name} no coincide con las fechas.")
    for series in report.load.series:
        if len(series.load) != len(report.load.labels) or len(series.capacity) != len(report.load.labels):
            raise ValueError(f"Perfil de carga inconsistente para {series.key}.")
    target = Path(out_path)
    temporary = None
    try:
        module = import_module(f"ganttier.reports.{fmt}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, prefix=".ganttier-", suffix="." + fmt, delete=False) as handle:
            temporary = Path(handle.name)
        module.write(report, temporary)
        temporary.replace(target)
    except ImportError as exc:
        raise RuntimeError(f"Falta una dependencia para generar {fmt}: {exc.name}. Instale las dependencias de Ganttier.") from exc
    except OSError as exc:
        raise OSError(f"No se pudo guardar el informe en {target}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
