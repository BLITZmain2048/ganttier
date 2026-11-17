"""Informe HTML autocontenido y preparado para impresión."""
from __future__ import annotations

from base64 import b64encode
from html import escape
from pathlib import Path

from ganttier.domain import ReportData
from .charts import draw
from .common import LIMIT, SEMÁFORO, cover, display, notice, sections, title, report_traffic, chart_description

CSS = """
*{box-sizing:border-box}body{margin:0;background:#edf2f5;color:#17354a;font:14px Arial,sans-serif}
main{max-width:1150px;margin:28px auto;padding:40px;background:white}h1{font-size:34px}h2{border-bottom:2px solid #087e8b;padding:12px 0;font-size:23px}
header{padding-bottom:30px;border-bottom:5px solid #087e8b}p{line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere}
table{width:100%;border-collapse:collapse;margin:16px 0;font-size:12px;table-layout:fixed}th{background:#17354a;color:white;text-align:left}
td,th{padding:8px;border:1px solid #d6e0e7;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}tr:nth-child(even){background:#f2f6f8}
img{display:block;width:100%;height:auto}.notice{color:#795a08;background:#fff5ce;padding:12px}.status{font-weight:bold}
@page{size:A4;margin:15mm}@media print{body{background:white}main{margin:0;padding:0;max-width:none}header{break-after:page}h2{break-after:avoid}thead{display:table-header-group}tr,img{break-inside:avoid}table{font-size:9px}td,th{padding:4px}img{max-height:100mm;object-fit:contain}a{color:inherit}}
"""


def write(report: ReportData, path: Path) -> None:
    """Escribe texto escapado y gráficos PNG embebidos, sin recursos externos."""
    light, color = report_traffic(report)
    parts = [f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title(report))}</title><style>{CSS}</style></head><body><main><header>', f"<h1>{escape(title(report))}</h1>"]
    parts.extend(f"<p><strong>{escape(key)}:</strong> {escape(display(value))}</p>" for key, value in cover(report))
    parts.append(f'<p class="status" style="color:#{color}">Semáforo de plazo: {light}</p><p>{escape(SEMÁFORO)}</p></header>')
    for section in sections(report):
        parts.append(f"<section><h2>{escape(section.title)}</h2>")
        if section.chart:
            encoded = b64encode(draw(report, section.chart)).decode("ascii")
            parts.append(f'<img alt="{escape(chart_description(section.chart))}" src="data:image/png;base64,{encoded}">')
        if (section.total_rows or len(section.rows)) > LIMIT:
            parts.append(f'<p class="notice">{notice(section.total_rows or len(section.rows))}</p>')
        if section.rows:
            parts.append("<table><thead><tr>" + "".join(f'<th scope="col">{escape(h)}</th>'  for h in section.headers) + "</tr></thead><tbody>")
            for row in section.rows[:LIMIT]:
                parts.append("<tr>" + "".join(f"<td>{escape(display(v, i in section.percent_columns))}</td>" for i, v in enumerate(row)) + "</tr>")
            parts.append("</tbody></table>")
        else:
            parts.append("<p>Sin registros para el período.</p>")
        parts.append("</section>")
    parts.append(f"<section><h2>Notas</h2><p>{escape(report.notes or 'Sin notas.')}</p></section></main></body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")
