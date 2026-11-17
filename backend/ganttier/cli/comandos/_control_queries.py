"""Consultas CTL: mismos cálculos, caché columnar y paginación que la API."""
from __future__ import annotations

import argparse
import bisect
import csv
from contextlib import closing, nullcontext
from pathlib import Path

from fastapi.encoders import jsonable_encoder

from ...api import analysis, control, insights
from .._base import Context, Result

BASIS = {"horas": "hours", "costo": "cost", "actividades": "count"}
PERIOD = {"dia": "day", "semana": "week", "mes": "month"}


def _summarize(conn, pid, curve, evm):
    labels = curve["labels"]
    keep = {}

    def mark(index, reason):
        if labels:
            keep.setdefault(min(index, len(labels) - 1), set()).add(reason)

    mark(0, "start")
    mark(len(labels) - 1, "finish")
    if curve["data_date"]:
        mark(bisect.bisect_left(labels, curve["data_date"]), "cutoff")
    # Los hitos se representan por el punto del período que los contiene.
    # No interpolamos ni recalculamos valores de la serie original.
    for row in conn.execute("""SELECT COALESCE(b.finish,a.planned_finish,a.early_finish) AS finish
        FROM activities a LEFT JOIN projects p ON p.id=a.project_id
        LEFT JOIN baseline_activities b ON b.baseline_id=p.active_baseline_id AND b.activity_id=a.id
        WHERE a.project_id=? AND a.duration_days=0""", (pid,)):
        if row[0]:
            mark(bisect.bisect_left(labels, row[0]), "milestone")
    indices = sorted(keep)
    result = {key: [value[i] for i in indices] if isinstance(value, list) and len(value) == len(labels) else value
              for key, value in curve.items()}
    result["total_points"] = len(labels)
    result["key_points"] = [{"date": labels[i], "reasons": sorted(keep[i])} for i in indices]
    result["deviations"] = {key: evm[key] for key in ("SV", "CV", "SPI", "CPI", "SPI_t", "VAC", "planned_finish", "forecast_finish")}
    return result


def execute(args: argparse.Namespace, ctx: Context) -> Result:
    """Ejecuta consultas, aislando los efectos persistentes de alertas y CSV."""
    operation = args._ctl.rsplit("/", 1)[-1]
    export = getattr(args, "salida_serie", None)
    mutation = operation == "alertas" or bool(export)
    with (ctx.mutation(args._ctl) if mutation else closing(ctx.conn())) as conn:
        pid = ctx.require_project(conn)
        basis = BASIS.get(getattr(args, "base", "horas"))
        period = PERIOD.get(getattr(args, "periodo", "semana"))
        if operation in {"curva-s", "evm", "tablero"}:
            from ... import repo
            # Una copia descartable no debe dejar un observador de caché abierto.
            with (repo.transaction(conn) if mutation else nullcontext()):
                panel = jsonable_encoder(control.dashboard(pid, period, basis, max_points=None, conn=conn))
            curve = panel["scurve"]
            metadata = None
            if export:
                from ... import services
                from ._control_reports import file_metadata
                target = Path(export).expanduser().resolve()
                if ctx.mutation_info.simulated:
                    target = services.DATA_DIR / "series" / target.name
                target.parent.mkdir(parents=True, exist_ok=True)
                fields = [key for key, value in curve.items() if isinstance(value, list)]
                with target.open("w", encoding="utf-8", newline="") as output:
                    writer = csv.writer(output)
                    writer.writerow(fields)
                    writer.writerows(zip(*(curve[key] for key in fields)))
                metadata = file_metadata(target, ctx.mutation_info.simulated)
            if getattr(args, "resumen", False) or export:
                panel["scurve"] = _summarize(conn, pid, curve, panel["evm"])
            data = panel if operation == "tablero" else panel["scurve" if operation == "curva-s" else "evm"]
            if metadata:
                data = dict(data, series_file=metadata)
        elif operation == "evm-por":
            data = analysis.project_evm_by(pid, {"wbs": "wbs", "instalacion": "location", "habilidad": "skill"}[args.agrupacion], basis, conn)
        elif operation == "pronosticos":
            data = analysis.project_forecasts(pid, basis, args.etc, conn)
        elif operation == "desempeno":
            data = control.performance(pid, args.inicio, args.fin, conn)
        elif operation == "alertas":
            data = insights.alerts_summary(pid, conn) if args.resumen else insights.project_alerts(pid, args.estado, conn)
        elif operation == "calidad":
            if min(args.desfase_largo, args.duracion_larga, args.holgura_alta) < 0:
                raise ValueError("Los umbrales de calidad deben ser no negativos.")
            data = analysis.project_quality(pid, args.perfil, not args.incluir_extremos, args.inicio_id, args.fin_id,
                args.desfase_largo, args.duracion_larga, args.holgura_alta, conn)
        elif operation == "actividades":
            if args.pagina < 1 or not 1 <= args.por_pagina <= 500:
                raise ValueError("La página debe ser positiva y su tamaño entre 1 y 500.")
            data = control.activities_page(pid, (args.pagina - 1) * args.por_pagina, args.por_pagina,
                {"todas": "all", "abiertas": "open", "atrasadas": "late"}[args.alcance], args.buscar, conn)
        else:
            raise ValueError("Consulta de control desconocida.")
        return Result(data=jsonable_encoder(data))
