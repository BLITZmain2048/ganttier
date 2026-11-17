"""Plan semanal nominal basado exclusivamente en asignaciones explícitas."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import repo
from ..calendars import WorkCalendar

MAX_WORKERS = 250
MAX_ACTIVITIES = 1000
DAYS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _append(sheet, values):
    sheet.append(values)
    # Los textos del usuario son literales, incluso si empiezan por '='.
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"


def _format(sheet, header_rows, extracted_at):
    for row in sheet:
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(cell.value, (date, datetime)):
                cell.number_format = "dd-mm-yyyy"
    for row_index in header_rows:
        for cell in sheet[row_index]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="24445C")
    for column in sheet.columns:
        width = max((len(line) for cell in column for line in str(cell.value or "").split("\n")), default=10)
        sheet.column_dimensions[get_column_letter(column[0].column)].width = min(48, max(14, width + 2))
    sheet.freeze_panes = "C2" if sheet.title == "Plan semanal" else "D2"
    sheet.print_title_rows = "1:1"
    sheet.print_options.horizontalCentered = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.oddFooter.left.text = f"Fecha de extracción: {extracted_at:%d-%m-%Y %H:%M}"
    sheet.oddFooter.right.text = "Página &P de &N"
    sheet.print_area = sheet.dimensions


def weekly_roster(conn: sqlite3.Connection, pid: int, week_start: date,
                  *, extracted_at: datetime | None = None) -> bytes:
    """Devuelve XLSX en memoria; no cambia fechas, asignaciones ni la base."""
    start = monday(week_start)
    if start > date.max - timedelta(days=6):
        raise ValueError("La semana solicitada excede el rango de fechas admitido.")
    dates = [start + timedelta(days=i) for i in range(7)]
    with repo.transaction(conn):
        project = repo.get(conn, "projects", pid)
        candidates = repo.list_entities(conn, "activities", pid,
                                        filters={"from": start, "to": dates[-1]})
        # Solo se consideran fechas programadas (con respaldo early_* por extremo).
        scheduled = []
        for activity in candidates:
            first = activity.planned_start or activity.early_start
            last = activity.planned_finish or activity.early_finish
            if first and last and first <= dates[-1] and last >= start:
                if last < first:
                    raise ValueError(f"{activity.code}: las fechas programadas están invertidas.")
                scheduled.append((activity, first, last))
        if len(scheduled) > MAX_ACTIVITIES:
            raise ValueError("El plan semanal admite como máximo 1.000 actividades en la semana; acote el período.")
        resources = {r.id: r for r in repo.list_entities(conn, "resources", pid)}
        assignments = repo.list_entities(conn, "assignments", pid)
        calendars = repo.list_entities(conn, "calendars")
    default = project.calendar_id or next((c.id for c in calendars if c.name == "Chile L-V 8h"),
                                          calendars[0].id if calendars else None)
    by_calendar = {c.id: c for c in calendars}
    low = min([start] + [first for _, first, _ in scheduled])
    high = max([dates[-1]] + [last for _, _, last in scheduled])
    cache = {}

    def calendar(cid):
        cid = cid or default
        if cid not in by_calendar:
            raise ValueError("El plan semanal necesita un calendario efectivo válido.")
        if cid not in cache:
            cache[cid] = WorkCalendar(by_calendar[cid], low, high)
        return cache[cid]

    by_activity = defaultdict(list)
    for assignment in assignments:
        by_activity[assignment.activity_id].append(assignment)
    matrices = {"trabajador": {}, "cuadrilla": {}}
    unassigned, detail = [], []
    for activity, first, last in sorted(scheduled, key=lambda item: (item[0].code, item[0].id)):
        cal = calendar(activity.calendar_id)
        indices = [i for i, day in enumerate(dates) if first <= day <= last and cal.is_workday(day)]
        if not indices:
            continue
        label = f"{activity.code} · {activity.name} ({activity.location})"
        nominal = False
        for assignment in by_activity[activity.id]:
            resource = resources.get(assignment.resource_id)
            if resource and resource.kind == "trabajador":
                nominal = True
            if resource and resource.kind in matrices:
                rows = matrices[resource.kind]
                row = rows.setdefault(resource.id, [[] for _ in dates])
                if resource.kind == "trabajador" and len(rows) > MAX_WORKERS:
                    raise ValueError("El plan semanal admite como máximo 250 trabajadores con asignación en la semana.")
                resource_cal = calendar(resource.calendar_id)
                for i in indices:
                    if label not in row[i]:
                        row[i].append(label)
            else:
                resource_cal = None
            count = cal.workdays_between(first, last)
            for i in indices:
                # Cero HH representa demanda por unidades según el contrato de Assignment.
                hours = assignment.work_hours / count if assignment.work_hours else assignment.units * cal.hours(dates[i])
                unavailable = resource_cal is not None and not resource_cal.is_workday(dates[i])
                detail.append([resource.id if resource else None,
                               resource.name if resource else "Sin asignación nominal",
                               dates[i], activity.code, activity.name, activity.location, hours,
                               resource.kind if resource else "Demanda genérica", assignment.id,
                               "(no disponible)" if unavailable else ""])
        if activity.work_hours > 0 and not nominal:
            unassigned.append([activity.id, activity.code] + [label if i in indices else "" for i in range(7)])

    book = Workbook()
    plan = book.active
    plan.title = "Plan semanal"
    headers = ["ID trabajador", "Trabajador"] + [f"{name}\n{day:%d-%m-%Y}" for name, day in zip(DAYS, dates)]
    _append(plan, headers)
    bold = [1]
    for kind in ("trabajador", "cuadrilla"):
        if kind == "cuadrilla":
            plan.append([])
            _append(plan, ["Cuadrillas"])
            bold.append(plan.max_row)
            _append(plan, ["ID cuadrilla", "Cuadrilla"] + headers[2:])
            bold.append(plan.max_row)
        for rid, cells in sorted(matrices[kind].items(), key=lambda item: (resources[item[0]].name, item[0])):
            cal = calendar(resources[rid].calendar_id)
            values = ["\n".join(cell + (["(no disponible)"] if not cal.is_workday(day) else []))
                      for cell, day in zip(cells, dates)]
            _append(plan, [rid, resources[rid].name] + values)
            plan.row_dimensions[plan.max_row].height = max(32, 15 * max(v.count("\n") + 1 for v in values))
        if kind == "trabajador":
            plan.auto_filter.ref = f"A1:I{max(1, plan.max_row)}"
    plan.append([])
    _append(plan, ["Sin asignación nominal"])
    bold.append(plan.max_row)
    _append(plan, ["ID actividad", "Código"] + headers[2:])
    bold.append(plan.max_row)
    for row in unassigned:
        _append(plan, row)
    plan.append([])
    _append(plan, ["Las tareas comunican coincidencias diarias; no establecen secuencia horaria ni HH reales."])
    details = book.create_sheet("Detalle")
    _append(details, ["ID recurso", "Trabajador / recurso", "Fecha", "Código", "Actividad", "Instalación",
                      "HH de la asignación", "Tipo de recurso", "ID asignación", "Disponibilidad"])
    for row in detail:
        _append(details, row)
        details.cell(details.max_row, 7).number_format = "0.00"
    details.auto_filter.ref = details.dimensions
    extracted_at = extracted_at or datetime.now()
    _format(plan, bold, extracted_at)
    _format(details, [1], extracted_at)
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()
