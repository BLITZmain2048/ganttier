"""Programa de desconexiones: instantánea del programa vigente, sin modificarlo."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from io import BytesIO
from typing import Iterable

from .. import repo

TITLE = "Programa de trabajos con desconexión (base para solicitudes)"
NOTE = (
    "Esta planilla es una base de preparación de solicitudes, no un formato oficial. "
    "El formato oficial y los plazos de solicitud deben verificarse en la normativa "
    "vigente y los procedimientos del Coordinador Eléctrico Nacional."
)
PROGRAM_HEADERS = (
    "N°", "Instalación", "Tensión kV", "Inicio", "Término", "Días", "N° trabajos",
    "Equipos", "Descripción de trabajos", "HH", "Responsable(s)", "Observaciones",
)
DETAIL_HEADERS = (
    "N° desconexión", "Instalación", "Tensión kV", "Código", "Actividad", "Equipo",
    "Inicio", "Término", "Días", "HH", "Responsable(s)", "Agrupado",
)


def _kpis(groups: list[dict]) -> dict:
    count = len(groups)
    jobs = sum(g["activity_count"] for g in groups)
    return {
        "outage_count": count,
        "activity_count": jobs,
        "grouped_outage_count": sum(g["grouped"] for g in groups),
        "grouped_activity_count": sum(g["activity_count"] for g in groups if g["grouped"]),
        "average_activities_per_outage": jobs / count if count else 0.0,
        "work_hours": sum(g["work_hours"] for g in groups),
    }


def build_program(conn: sqlite3.Connection, pid: int,
                  start: date | None = None, end: date | None = None) -> dict:
    """Agrupa primero y filtra después: nunca corta una desconexión por el filtro."""
    if start and end and start > end:
        raise ValueError("La fecha desde no puede ser posterior a la fecha hasta.")
    project = repo.get(conn, "projects", pid)
    resources = {r.id: {"id": r.id, "name": r.name, "kind": r.kind}
                 for r in repo.list_entities(conn, "resources", pid)
                 if r.kind in {"trabajador", "cuadrilla"}}
    assigned = defaultdict(set)
    for assignment in repo.list_entities(conn, "assignments", pid):
        if assignment.resource_id in resources:
            assigned[assignment.activity_id].add(assignment.resource_id)

    by_key = {}
    excluded = 0
    for activity in repo.list_entities(conn, "activities", pid, filters={"requires_outage": True}):
        first = activity.planned_start or activity.early_start
        last = activity.planned_finish or activity.early_finish
        if first is None or last is None or last < first:
            excluded += 1
            continue
        key = (activity.location, first)
        if key not in by_key:
            by_key[key] = {
                "location": activity.location, "voltage_kv": None,
                "start": first, "finish": last, "activities": [], "work_hours": 0.0,
            }
        group = by_key[key]
        group["finish"] = max(group["finish"], last)
        if activity.voltage_kv is not None:
            group["voltage_kv"] = max(group["voltage_kv"] or 0, activity.voltage_kv)
        group["work_hours"] += activity.work_hours
        group["activities"].append({
            "id": activity.id, "code": activity.code, "name": activity.name,
            "equipment": activity.equipment, "voltage_kv": activity.voltage_kv,
            "start": first, "finish": last, "duration_days": (last - first).days + 1,
            "work_hours": activity.work_hours,
            "workers": [resources[rid] for rid in sorted(assigned.get(activity.id, ()))],
        })

    groups = []
    for group in by_key.values():
        if (start and group["finish"] < start) or (end and group["start"] > end):
            continue
        group["duration_days"] = (group["finish"] - group["start"]).days + 1
        group["activity_count"] = len(group["activities"])
        group["grouped"] = group["activity_count"] >= 2
        groups.append(group)
    groups.sort(key=lambda g: (g["start"], g["location"] or ""))
    months, voltages = defaultdict(list), defaultdict(list)
    for number, group in enumerate(groups, 1):
        group["number"] = number
        months[group["start"].strftime("%Y-%m")].append(group)
        voltages[group["voltage_kv"]].append(group)
    return {
        "title": TITLE, "note": NOTE, "project_id": pid, "project_name": project.name,
        "from": start, "to": end, "groups": groups,
        "excluded_activity_count": excluded,
        "summary": {
            **_kpis(groups),
            "by_month": [{"month": key, **_kpis(value)} for key, value in sorted(months.items())],
            "by_voltage": [{"voltage_kv": key, **_kpis(voltages[key])}
                           for key in sorted(voltages, key=lambda kv: (kv is None, kv or 0))],
        },
    }


def _joined(values: Iterable[str]) -> str:
    """Vista compacta; Detalle conserva las actividades cuando se supera la celda Excel."""
    value = "; ".join(dict.fromkeys(v for v in values if v))
    if len(value) > 32000:
        return value[:31940] + " … [lista abreviada; consultar Detalle]"
    return value


def write_xlsx(program: dict, target) -> None:
    """Exportación secuencial: no construye millones de celdas residentes en memoria.

    Los valores son una instantánea de BD, incluidos los KPI; Observaciones es editable.
    """
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.worksheet import Worksheet

    book = Workbook(write_only=True)
    book.properties.title = TITLE
    book.properties.creator = "Ganttier"
    font = Font(name="Arial", size=10, color="172B4D")
    heading_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    heading_fill = PatternFill("solid", fgColor="17365D")
    band_fill = PatternFill("solid", fgColor="F0F5FA")
    input_fill = PatternFill("solid", fgColor="FFF2CC")
    alignment = Alignment(vertical="top", wrap_text=True)
    styles = {}

    def sheet(name, widths):
        ws = book.create_sheet(name)
        ws.freeze_panes = "C2" if name != "Resumen" else "B2"
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = Worksheet.PAPERSIZE_A3
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_title_rows = "1:1"
        for index, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(index)].width = width
        return ws

    def append(ws, values, *, header=False, band=False, editable=None, height=None):
        # write_only requiere dimensiones antes de emitir cada fila.
        row_number = row_counts[ws.title] + 1
        if height:
            ws.row_dimensions[row_number].height = height
        cells = []
        for index, value in enumerate(values, 1):
            if isinstance(value, str):
                value = ILLEGAL_CHARACTERS_RE.sub("", value)
                if len(value) > 32767:
                    value = value[:32710] + " … [texto abreviado por límite Excel]"
            cell = WriteOnlyCell(ws, value=value)
            # Los nombres/códigos ingresados por usuarios siempre son texto, incluso '=...'.
            if isinstance(value, str):
                cell.data_type = "s"
            fill_kind = "header" if header else "input" if index == editable else "band" if band else "plain"
            number_format = "dd-mm-yyyy" if isinstance(value, date) else '#,##0.00' if isinstance(value, float) else "General"
            key = (fill_kind, number_format)
            if key not in styles:
                template = WriteOnlyCell(ws)
                template.font = heading_font if header else font
                template.alignment = alignment
                template.number_format = number_format
                if fill_kind != "plain":
                    template.fill = {"header": heading_fill, "input": input_fill, "band": band_fill}[fill_kind]
                styles[key] = template._style
            # Estilos inmutables compartidos: evita registrar fuente/relleno por cada celda.
            cell._style = styles[key]
            cells.append(cell)
        ws.append(cells)
        row_counts[ws.title] = row_number

    summary = sheet("Resumen", [76, 24, 22, 24, 26, 22])
    schedule = sheet("Programa", [8, 30, 15, 15, 15, 10, 15, 40, 64, 16, 38, 48])
    detail = sheet("Detalle", [18, 30, 15, 24, 64, 35, 15, 15, 10, 16, 38, 13])
    row_counts = {ws.title: 0 for ws in book.worksheets}
    append(summary, [TITLE], header=True, height=42)
    append(summary, [NOTE], height=80)
    append(summary, ["Proyecto", program["project_name"]])
    append(summary, ["Desde (solapamiento inclusivo)", program["from"]])
    append(summary, ["Hasta (solapamiento inclusivo)", program["to"]])
    append(summary, ["Fuente: programa vigente de Ganttier. Fechas planned; respaldo early. "
                     "Días corridos inclusivos; HH presupuestadas de actividades."], height=46)
    append(summary, ["Instantánea: regenerar tras cambiar el programa. Completar las celdas "
                     "amarillas de Observaciones. Ejemplo: coordinar acceso con operación."], height=46)
    append(summary, ["Agrupación por instalación e inicio; no acredita viabilidad operacional. "
                     "Instalación vacía = sin identificar. Responsable(s) = personal asignado; "
                     "no designa al responsable formal de una solicitud."], height=64)
    append(summary, ["Actividades excluidas por fechas incompletas o invertidas (todo el proyecto)",
                     program["excluded_activity_count"]], height=32)
    append(summary, ["Indicador", "Valor"], header=True)
    kpis = program["summary"]
    for label, key in (
        ("N° desconexiones", "outage_count"), ("N° trabajos", "activity_count"),
        ("Desconexiones agrupadas (≥2 trabajos)", "grouped_outage_count"),
        ("Trabajos en desconexiones agrupadas", "grouped_activity_count"),
        ("Trabajos / desconexión promedio", "average_activities_per_outage"),
        ("HH totales", "work_hours"),
    ):
        append(summary, [label, kpis[key]])
    append(summary, ["Por mes de inicio y tensión máxima de cada desconexión. "
                     "No se prorratean HH entre meses. Tensión vacía = sin informar."], height=46)
    for title, rows, key in (("Mes de inicio", kpis["by_month"], "month"),
                             ("Tensión máxima kV", kpis["by_voltage"], "voltage_kv")):
        append(summary, [title, "N° desconexiones", "N° trabajos", "Trabajos agrupados",
                         "Trabajos / desconexión", "HH"], header=True, height=30)
        for row in rows:
            append(summary, [row[key] if row[key] is not None else "Sin informar",
                             row["outage_count"], row["activity_count"], row["grouped_activity_count"],
                             row["average_activities_per_outage"], row["work_hours"]])

    append(schedule, PROGRAM_HEADERS, header=True, height=30)
    append(detail, DETAIL_HEADERS, header=True, height=30)
    for group in program["groups"]:
        jobs = group["activities"]
        number = group["number"]
        append(schedule, [number, group["location"], group["voltage_kv"], group["start"],
                          group["finish"], group["duration_days"], group["activity_count"],
                          _joined(a["equipment"] for a in jobs),
                          _joined(f'{a["code"]}: {a["name"]}' for a in jobs), group["work_hours"],
                          _joined(w["name"] for a in jobs for w in a["workers"]), None],
               band=number % 2 == 0, editable=12, height=48)
        for activity in jobs:
            append(detail, [number, group["location"], activity["voltage_kv"], activity["code"],
                            activity["name"], activity["equipment"], activity["start"], activity["finish"],
                            activity["duration_days"], activity["work_hours"],
                            _joined(w["name"] for w in activity["workers"]),
                            "Sí" if group["grouped"] else "No"], band=number % 2 == 0)
    for ws, headers in ((schedule, PROGRAM_HEADERS), (detail, DETAIL_HEADERS)):
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row_counts[ws.title]}"
    book.save(target)


def xlsx_bytes(program: dict) -> bytes:
    """El ZIP final se sirve sin archivos persistentes de descarga."""
    output = BytesIO()
    write_xlsx(program, output)
    return output.getvalue()
