"""Gantt nativo de Excel, agregado antes de escribir y sin modificar el programa."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from math import ceil
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import numpy as np
from openpyxl.cell import WriteOnlyCell
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._write_only import WriteOnlyWorksheet
from openpyxl.worksheet._writer import WorksheetWriter

from .. import repo
from ..calendars import WorkCalendar
from .streaming import StreamingWorkbook, _writer, close_workbook, INVALID_XML

MAX_ROWS = 500
MAX_PERIODS = 520
MAX_DAYS = 20 * 366
BLUE = '4472C4'
RED = 'C0504D'
GRAY = 'E7E6E6'
GREEN = 'C6E0B4'
ORANGE = 'F4B183'
FILL = {c: PatternFill('solid', fgColor=c) for c in (BLUE, RED, GRAY, GREEN, ORANGE, 'FFF2CC')}
DATE_FORMAT = 'dd-mm-yyyy'
NORMAL_FONT = Font(name='Arial', size=10, color='263445')
HEADER_FONT = Font(name='Arial', size=10, bold=True, color='FFFFFF')
LEFT = Alignment(vertical='center',wrap_text=True,horizontal='left')
CENTER = Alignment(vertical='center',wrap_text=True,horizontal='center')


@dataclass(frozen=True)
class Options:
    mode: str = 'nivel'
    group_by: str = 'wbs'
    scale: str = 'week'
    from_date: date | None = None
    to_date: date | None = None
    critical: bool = False

    def __post_init__(self):
        if self.mode not in {'nivel', 'trabajador'}:
            raise ValueError('El modo debe ser nivel o trabajador.')
        if self.scale not in {'day', 'week', 'month'}:
            raise ValueError('La escala debe ser day (día), week (semana) o month (mes).')
        if not re.fullmatch(r'(wbs(?::[1-9][0-9]?)?|sitio|instalacion|equipo|habilidad)(/wbs(?::[1-9][0-9]?)?)?', self.group_by):
            raise ValueError('Agrupación inválida: use wbs[:nivel], sitio, instalacion, equipo o habilidad; puede añadir /wbs[:nivel].')
        if type(self.critical) is not bool:
            raise ValueError('Ruta crítica debe ser true o false.')
        for value in (self.from_date, self.to_date):
            if value is not None and (type(value) is not date or not 1900 <= value.year <= 9998):
                raise ValueError('Las fechas deben estar entre 01-01-1900 y 31-12-9998.')
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError('La fecha desde no puede ser posterior a hasta.')


def parse_options(mode='nivel', group_by='wbs', scale='week', first=None, last=None, critical='false'):
    def parse(value, label):
        if value in (None, ''):
            return None
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError(f'La fecha {label} debe tener formato AAAA-MM-DD.')
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f'La fecha {label} no es válida.') from exc
    if critical not in ('true', 'false'):
        raise ValueError('Ruta crítica debe ser true o false.')
    return Options(mode, group_by, scale, parse(first, 'desde'), parse(last, 'hasta'), critical == 'true')


@lru_cache(maxsize=4096)
def _day(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _bounds(conn, pid, project, options):
    row = conn.execute('''SELECT MIN(COALESCE(planned_start,early_start)),
        MAX(COALESCE(planned_finish,early_finish)) FROM activities WHERE project_id=?''', (pid,)).fetchone()
    first = options.from_date or _day(row[0]) or project.start_date
    last = options.to_date or _day(row[1]) or project.end_date or first
    if first > last:
        raise ValueError('La fecha desde no puede ser posterior a hasta.')
    if first.year < 1900 or last.year > 9998 or (last-first).days >= MAX_DAYS:
        raise ValueError('El rango debe estar entre 1900 y 9998 y abarcar como máximo 20 años; acote desde/hasta.')
    periods = []
    current = first
    while current <= last:
        if options.scale == 'day':
            end = current
        elif options.scale == 'week':
            end = current + timedelta(days=6-current.weekday())
        else:
            end = (current.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        periods.append((current, min(last, end)))
        if len(periods) > MAX_PERIODS:
            raise ValueError(f'El rango supera {MAX_PERIODS} columnas temporales; elija semana/mes o acote desde/hasta.')
        current = end + timedelta(days=1)
    return first, last, periods, min(first, _day(row[0]) or first), max(last, _day(row[1]) or last)


@dataclass
class Group:
    name: str
    count: int = 0
    start: date | None = None
    finish: date | None = None
    progress: float = 0
    weight: float = 0
    critical: bool = False
    milestones: dict = field(default_factory=lambda: defaultdict(int))

    def merge(self, other):
        self.count += other.count
        self.start = min(x for x in (self.start, other.start) if x)
        self.finish = max(x for x in (self.finish, other.finish) if x)
        self.progress += other.progress
        self.weight += other.weight
        self.critical |= other.critical
        for key, value in other.milestones.items():
            self.milestones[key] += value


def _groups(conn, pid, options, first, last, periods):
    nodes = {n.id: n for n in repo.list_entities(conn, 'wbs', pid)}
    cache = {}
    def wbs(wid, part):
        key = wid, part
        if key in cache:
            return cache[key]
        chain, seen = [], set()
        while wid in nodes and wid not in seen:
            seen.add(wid)
            chain.append(nodes[wid])
            wid = nodes[wid].parent_id
        chain.reverse()
        if not chain:
            result = 'Sin WBS'
        else:
            level = int(part.split(':')[1]) if ':' in part else len(chain)
            node = chain[min(level, len(chain))-1]
            result = f'{node.code} · {node.name}'
        cache[key] = result
        return result
    teams = {r.id: r.name for r in repo.list_entities(conn, "resources", pid) if r.is_work_team}
    groups = {}
    excluded = 0
    starts = [p[0] for p in periods]
    for row in conn.execute('''SELECT wbs_id,location,equipment,required_skill,team_id,
        COALESCE(planned_start,early_start) AS inicio, COALESCE(planned_finish,early_finish) AS fin,
        duration_days,work_hours,percent_complete,critical FROM activities WHERE project_id=? ORDER BY id''', (pid,)):
        start, finish = _day(row['inicio']), _day(row['fin'])
        if not start or not finish:
            excluded += 1
            continue
        if finish < start:
            raise ValueError('Hay actividades con fin anterior al inicio; revise el programa.')
        parts = []
        for part in options.group_by.split('/'):
            parts.append(teams[row['team_id']] if part == 'equipo' and row['team_id'] in teams else wbs(row['wbs_id'], part) if part.startswith('wbs') else
                         row[{'sitio':'location', 'instalacion':'location', 'equipo':'equipment', 'habilidad':'required_skill'}[part]] or 'Sin clasificar')
        name = ' / '.join(parts)
        group = groups.get(name)
        if group is None:
            group = groups[name] = Group(name)
        weight = row['work_hours'] or max(row['duration_days'], 1)
        group.count += 1
        group.start = min(group.start, start) if group.start else start
        group.finish = max(group.finish, finish) if group.finish else finish
        if row['percent_complete'] is not None:
            group.progress += row['percent_complete'] * weight
            group.weight += weight
        group.critical |= bool(row['critical'])
        if row['duration_days'] == 0 and first <= start <= last:
            group.milestones[bisect_right(starts, start)-1] += 1
    rows = sorted((g for g in groups.values() if g.finish >= first and g.start <= last), key=lambda r: r.name.casefold())
    original = len(rows)
    if original > MAX_ROWS:
        size = ceil(original / MAX_ROWS)
        combined = []
        for i in range(0, original, size):
            chunk = rows[i:i+size]
            merged = Group(f'{chunk[0].name} … {chunk[-1].name} ({len(chunk)} grupos)')
            for item in chunk:
                merged.merge(item)
            combined.append(merged)
        rows = combined
    return rows, original, excluded


class _LocalSheet(WriteOnlyWorksheet):
    """XML temporal junto al destino, nunca en el TEMP del usuario."""
    def _get_writer(self):
        if self._writer is None:
            self._writer = _writer(self, WorksheetWriter)
            self._writer.write_top()


def _sheet(book, name, widths, freeze='H6'):
    sheet = _LocalSheet(book, name)
    book._add_sheet(sheet)
    sheet.freeze_panes = freeze
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 85
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = '8'  # A3 en OOXML; WriteOnlyWorksheet no expone las constantes.
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = '1:5'
    sheet.oddHeader.center.text = 'GANTTIER · ' + name
    sheet.oddFooter.right.text = 'Página &P de &N'
    for i, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    return sheet


def _append(sheet, values, *, header=False, formats=None, fills=None, cutoff=None, timeline=8):
    number = getattr(sheet,'_gantt_row',0)+1
    sheet._gantt_row = number
    sheet.row_dimensions[number].height = 32 if header or number > 5 else 28
    styles = sheet.parent._gantt_styles
    cells = []
    for i, value in enumerate(values, 1):
        if isinstance(value, str) and (len(value)>32767 or INVALID_XML.search(value)):
            raise ValueError('Un texto excede 32.767 caracteres o contiene caracteres no permitidos en Excel.')
        cell = WriteOnlyCell(sheet, value=value[0] if isinstance(value, tuple) else value)
        if isinstance(value, str):
            # Sólo nuestras fórmulas internas se pasan como tupla, nunca texto del proyecto.
            cell.data_type = 's'
        if isinstance(value, tuple):
            cell.value = value[0]
        shade = fills.get(i) if fills else None
        fmt = (formats or {}).get(i, DATE_FORMAT if isinstance(value,date) else 'General')
        cut = cutoff is not None and i == timeline + cutoff
        separate = number > 5 and i >= timeline
        key = header, i == 1, shade, fmt, cut, separate
        if key not in styles:
            cell.font = HEADER_FONT if header else NORMAL_FONT
            cell.alignment = LEFT if i == 1 else CENTER
            if header:
                cell.fill = FILL[BLUE]
            if shade:
                cell.fill = FILL[shade]
                cell.font = Font(name='Arial', size=10, bold=header, color='263445')
            cell.number_format = fmt
            if cut or separate:
                cell.border = Border(left=Side(style='medium', color='E36C09') if cut else Side(),
                                     bottom=Side(style='thin',color='FFFFFF') if separate else Side())
            styles[key] = cell._style
        else:
            cell._style = styles[key]
        cells.append(cell)
    sheet.append(cells)


def _head(sheet, project, periods, headers, calendar, cutoff, first_col=8):
    _append(sheet, [project.name], header=True)
    _append(sheet, ['Fecha de corte', project.data_date, 'Programa vigente'])
    days = [calendar.workdays_between(a,b) for a,b in periods]
    _append(sheet, ['Días laborables'] + [None]*(first_col-2) + days)
    shades = {first_col+i: GRAY for i,(a,b) in enumerate(periods) if a == b and (a.weekday() >= 5 or not calendar.is_workday(a))}
    _append(sheet, ['Inicio del período'] + [None]*(first_col-2) + [a for a,b in periods], header=True, fills=shades, cutoff=cutoff, timeline=first_col)
    _append(sheet, headers + [b for a,b in periods], header=True, fills=shades, cutoff=cutoff, timeline=first_col)
    return shades


def _level(book, project, rows, periods, calendar, options, cutoff):
    sheet = _sheet(book, 'Resumen por nivel', [48,12,14,14,12,12,12] + [13]*len(periods))
    shades = _head(sheet, project, periods, ['Grupo', 'Actividades', 'Inicio programa', 'Fin programa', 'Avance', 'Crítico', 'Hitos'], calendar, cutoff)
    for group in rows:
        values = [group.name, group.count, group.start, group.finish,
                  group.progress / group.weight / 100 if group.weight else None,
                  'Sí' if group.critical else 'No', sum(group.milestones.values())]
        grid = [f'◆ {group.milestones[i]}' if i in group.milestones else None for i in range(len(periods))]
        if group.start < periods[0][0]:
            grid[0] = (grid[0] or '') + ' ◀'
        if group.finish > periods[-1][1]:
            grid[-1] = (grid[-1] or '') + ' ▶'
        _append(sheet, values + grid, formats={5:'0.0%'}, fills=shades, cutoff=cutoff)
    end = 5+len(rows)
    if rows:
        area = f'H6:{get_column_letter(7+len(periods))}{end}'
        base = 'AND(H$4<=$D6,H$5>=$C6,H$3>0)'
        if options.critical:
            sheet.conditional_formatting.add(area, FormulaRule(formula=[f'AND({base},$F6="Sí")'], fill=FILL[RED], stopIfTrue=True))
        sheet.conditional_formatting.add(area, FormulaRule(formula=[base], fill=FILL[BLUE]))
    sheet.auto_filter.ref = f'A5:{get_column_letter(7+len(periods))}{max(5,end)}'
    return sheet


def _workers(conn, pid, calendars, default, first, last, periods):
    """Una persona a la vez; diferencias por calendario, sin matriz actividad × día."""
    resources = repo.list_entities(conn, 'resources', pid)
    count = (last-first).days+1
    inverse = np.empty(count, dtype=int)
    for i,(a,b) in enumerate(periods):
        inverse[(a-first).days:(b-first).days+1] = i
    # Los roles aplicados guardan una asignación nominal separada de la demanda.
    role_rows = {}
    has_roles = conn.execute("SELECT 1 FROM sqlite_master WHERE name='role_schedules'").fetchone()
    if has_roles and conn.execute('SELECT 1 FROM role_schedules WHERE project_id=?', (pid,)).fetchone():
        from ..role_schedules import nominal_assignments
        for item in nominal_assignments(conn, pid):
            role_rows.setdefault(item.resource_id, []).append(item)
    role_activities = {row['id']: row for row in conn.execute('''SELECT id,calendar_id,duration_days,
        COALESCE(planned_start,early_start) inicio,COALESCE(planned_finish,early_finish) fin
        FROM activities WHERE project_id=?''', (pid,))} if role_rows else {}
    for resource in sorted(resources, key=lambda r:(r.name.casefold(), r.id)):
        if resource.kind != 'trabajador':
            continue
        diffs = {}
        def add(row, hours, units):
            start, finish = _day(row['inicio']), _day(row['fin'])
            if not start or not finish or finish < first or start > last or row['duration_days'] == 0:
                return
            cid = row['calendar_id'] or default
            cal = calendars[cid]
            days = cal.workdays_between(start, finish)
            if not days:
                return
            daily = hours / days + units * cal.cal.hours_per_day
            diff = diffs.setdefault(cid, np.zeros(count+1))
            diff[max(0,(start-first).days)] += daily
            diff[min(count,(finish-first).days+1)] -= daily
        if role_rows:
            # Sólo se usa para proyectos con asignación nominal aplicada y válida.
            for assignment in role_rows.get(resource.id, []):
                row = role_activities[assignment.activity_id]
                add(row, assignment.work_hours, 0 if assignment.work_hours else assignment.units)
        else:
            for row in conn.execute('''SELECT a.calendar_id,1 duration_days,COALESCE(a.planned_start,a.early_start) inicio,
                COALESCE(a.planned_finish,a.early_finish) fin,SUM(s.work_hours) work_hours,
                SUM(CASE WHEN s.work_hours=0 THEN s.units ELSE 0 END) units
                FROM assignments s JOIN activities a ON a.id=s.activity_id
                WHERE a.project_id=? AND s.resource_id=? AND a.duration_days<>0
                GROUP BY a.calendar_id,inicio,fin''', (pid,resource.id)):
                add(row,row['work_hours'],row['units'])
        demand = np.zeros(count)
        for cid,diff in diffs.items():
            cal = calendars[cid]
            offset = (first-cal.horizon_start).days
            demand += np.cumsum(diff[:-1]) * (cal.day_hours_vector()[offset:offset+count] > 0)
        cal = calendars[resource.calendar_id or default]
        offset = (first-cal.horizon_start).days
        hours = cal.day_hours_vector()[offset:offset+count]
        capacity = np.minimum(hours, resource.capacity_hours_day) * resource.max_units if resource.active else np.zeros(count)
        load = np.maximum(0, np.bincount(inverse, weights=demand, minlength=len(periods)))
        cap = np.bincount(inverse, weights=capacity, minlength=len(periods))
        yield resource, load.tolist(), cap.tolist()


def _worker_sheets(book, conn, pid, project, calendars, default, first, last, periods, cutoff):
    total = 0
    sheets = []
    for resource, demand, capacity in _workers(conn,pid,calendars,default,first,last,periods):
        if total % MAX_ROWS == 0:
            index = total // MAX_ROWS + 1
            suffix = f' {index}' if index > 1 else ''
            load = _sheet(book, 'Por trabajador'+suffix, [34,12,16,16,14]+[13]*len(periods), 'F6')
            cap = _sheet(book, 'Capacidad'+suffix, [34,12,16,16,14]+[13]*len(periods), 'F6')
            shades = _head(load,project,periods,['Persona','ID','Total HH','Capacidad HH','Uso'],calendars[default],cutoff,6)
            _head(cap,project,periods,['Persona','ID','Total HH','', ''],calendars[default],cutoff,6)
            sheets.append((load,cap,0))
        row = 6 + total % MAX_ROWS
        lastcol = get_column_letter(5+len(periods))
        _append(cap,[resource.name,resource.id,(f'=SUM(F{row}:{lastcol}{row})',),None,None]+capacity, formats={i:'0.00' for i in range(3,6+len(periods))},fills=shades,cutoff=cutoff,timeline=6)
        lookup = f"INDEX('{cap.title}'!$C$6:$C${MAX_ROWS+5},MATCH($B{row},'{cap.title}'!$B$6:$B${MAX_ROWS+5},0))"
        _append(load,[resource.name,resource.id,(f'=SUM(F{row}:{lastcol}{row})',),(f'={lookup}',),
                      (f'=IF(D{row}=0,IF(C{row}>0,"Sin capacidad",0),C{row}/D{row})',)]+demand,formats={**{i:'0.00' for i in range(3,6+len(periods))},5:'0.0%'},fills=shades,cutoff=cutoff,timeline=6)
        sheets[-1] = load,cap,row
        total += 1
    for load,cap,end in sheets:
        area = f'F6:{lastcol}{end}'
        # INDIRECT admite referencias entre hojas en reglas Excel/LibreOffice.
        reference = (f'INDEX(INDIRECT("\'{cap.title}\'!"&ADDRESS(6,COLUMN())&":"&ADDRESS({MAX_ROWS+5},COLUMN())),'
                     f'MATCH($B6,INDIRECT("\'{cap.title}\'!$B$6:$B${MAX_ROWS+5}"),0))')
        for formula, color in ((f'F6>{reference}+0.000001',RED), (f'AND(F6>0,F6>={reference}*0.8)',ORANGE), ('F6>0',GREEN)):
            load.conditional_formatting.add(area,FormulaRule(formula=[formula],fill=FILL[color],stopIfTrue=True))
        for sheet in (load,cap):
            sheet.auto_filter.ref = f'A5:{lastcol}{end}'
            values = ['Total del período',None]+[(f'=SUM({get_column_letter(c)}6:{get_column_letter(c)}{end})',) if c != 5 else None for c in range(3,6+len(periods))]
            _append(sheet,values,header=True,formats={i:'0.00' for i in range(3,6+len(periods))})
    if not total:
        sheet = _sheet(book,'Por trabajador',[65],'A2')
        _append(sheet,['No hay recursos de tipo trabajador en este proyecto.'])
    else:
        summary = _sheet(book,'Totales por período',[16,16,20,20],'A2')
        _append(summary,['Desde','Hasta','Carga HH','Capacidad HH'],header=True)
        for i,(a,b) in enumerate(periods,6):
            col = get_column_letter(i)
            demands = ','.join(f"'{load.title}'!{col}{end+1}" for load,cap,end in sheets)
            capacities = ','.join(f"'{cap.title}'!{col}{end+1}" for load,cap,end in sheets)
            _append(summary,[a,b,(f'=SUM({demands})',),(f'=SUM({capacities})',)],formats={3:'0.00',4:'0.00'})
        from openpyxl.chart import BarChart, LineChart, Reference
        bars = BarChart()
        bars.title, bars.y_axis.title = 'Carga y capacidad por período', 'HH'
        bars.add_data(Reference(summary, min_col=3, min_row=1, max_row=len(periods)+1), titles_from_data=True)
        bars.set_categories(Reference(summary, min_col=1, min_row=2, max_row=len(periods)+1))
        capacity_chart = LineChart()
        capacity_chart.add_data(Reference(summary, min_col=4, min_row=1, max_row=len(periods)+1), titles_from_data=True)
        capacity_chart.set_categories(Reference(summary, min_col=1, min_row=2, max_row=len(periods)+1))
        capacity_chart.series[0].graphicalProperties.line.solidFill = '17354A'
        capacity_chart.series[0].graphicalProperties.line.prstDash = 'sysDash'
        bars += capacity_chart
        bars.width, bars.height = 24, 12
        summary.add_chart(bars, 'F2')
    return total


def _export_gantt(conn, pid: int, path: str | Path, options: Options | None = None) -> Path:
    """Exporta una instantánea vigente. La conexión debe exponer sqlite3.Row."""
    options = options or Options()
    project = repo.get(conn,'projects',pid)
    first,last,periods,lower,upper = _bounds(conn,pid,project,options)
    if (upper-lower).days > MAX_DAYS:
        raise ValueError('El programa abarca más de 20 años; revise sus fechas antes de exportar.')
    calendars = {c.id:WorkCalendar(c,lower,upper) for c in repo.list_entities(conn,'calendars')}
    default = project.calendar_id or next(iter(calendars),None)
    if default not in calendars:
        raise ValueError('El proyecto necesita un calendario válido para exportar.')
    cutoff = next((i for i,(a,b) in enumerate(periods) if project.data_date and a<=project.data_date<=b),None)
    target = Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    book = StreamingWorkbook(write_only=True)
    book._ganttier_temp_dir = target.parent
    book._gantt_styles = {}
    temporary = None
    notes = []
    try:
        if options.mode == 'nivel':
            rows,original,excluded = _groups(conn,pid,options,first,last,periods)
            _level(book,project,rows,periods,calendars[default],options,cutoff)
            notes += [('Agrupación',options.group_by),('Grupos originales',original),('Filas de resumen',len(rows)),('Actividades sin fechas (excluidas)',excluded)]
            if original > MAX_ROWS:
                notes.append(('Agrupamiento automático',f'{original} grupos reunidos en {len(rows)} intervalos alfabéticos contiguos; se conservan fechas, HH ponderadas, hitos y cantidades.'))
        else:
            count = _worker_sheets(book,conn,pid,project,calendars,default,first,last,periods,cutoff)
            notes += [('Personas',count),('Partición',f'Una fila por ID de trabajador; una pareja de hojas cada {MAX_ROWS} personas. Totales por hoja.')]
            unassigned = conn.execute('''SELECT COUNT(*) FROM activities a WHERE a.project_id=? AND
                NOT EXISTS(SELECT 1 FROM assignments s WHERE s.activity_id=a.id AND s.resource_id IS NOT NULL)''',(pid,)).fetchone()[0]
            notes += [('Actividades sin recurso explícito',unassigned),('Demanda genérica','No se atribuye a una persona. Los roles nominales aplicados y válidos se utilizan cuando existen.')]
        if conn.execute("SELECT 1 FROM periodic_tasks WHERE project_id=? AND quantity_total IS NOT NULL LIMIT 1", (pid,)).fetchone():
            from ..teams import summary
            from ..services import load_profile
            distribution = summary(conn, pid)
            demand = _sheet(book, 'Reparto por equipo', [16,26,26,18,18,18,18,18,18,18], 'A2')
            _append(demand, ['Mes','Ocurrencia','Equipo','Cantidad','Unidad','Lotes demanda','Programados','Días requeridos','Días disponibles','HH demanda'], header=True)
            for r in distribution['rows']:
                if r['window_start'] <= last and r['window_end'] >= first:
                    _append(demand, [r['month'],r['occurrence'],r['team'],r['quantity'],r['unit'],r['batches'],r['scheduled_batches'],r['required_days'],r['available_days'],r['work_hours']])
            team_names = {r.name for r in repo.list_entities(conn, 'resources', pid) if r.is_work_team}
            profile = load_profile(conn, pid, 'resource', options.scale, first, last)
            load_sheet = _sheet(book, 'Carga por equipo', [18,32,20,20], 'A2')
            _append(load_sheet, ['Período','Equipo','Carga programada HH','Capacidad HH'], header=True)
            for series in profile.series:
                if series.key in team_names:
                    for label, load, capacity in zip(profile.labels, series.load, series.capacity):
                        _append(load_sheet, [date.fromisoformat(label),series.key,load,capacity])
            for warning in [*distribution['warnings'], *(v['detail'] for v in distribution['violations'])]:
                notes.append(('Alerta de equipos', warning))
        from ..routes import checklist_report
        checklist = _sheet(book, 'Checklist de rutas', [28,28,16,18,18,18], 'A2')
        _append(checklist, ['Ruta','Ítem OM-WBS','Ocurrencia','Programado','Cumplido real','Fin programado'], header=True)
        for row in checklist_report(conn,pid):
            _append(checklist,[row['route'],row['item'],row['occurrence'],row['scheduled'],row['completed'],row['finish']])
        calendar = _sheet(book,'Calendario' ,[15,18,18,50],'A2')
        _append(calendar,['Fecha','Día de semana','Horas proyecto','Motivo'],header=True)
        cal = calendars[default]
        for i in range((last-first).days+1):
            day = first+timedelta(days=i)
            ex = cal.exceptions.get(day)
            reason = ex.reason if ex else cal.holidays.get(day,'')
            if not reason and not cal.is_workday(day):
                reason = 'Descanso de calendario'
            _append(calendar,[day,('lunes','martes','miércoles','jueves','viernes','sábado','domingo')[day.weekday()],cal.hours(day),reason],fills={1:GRAY} if not cal.is_workday(day) else None)
        legend = _sheet(book,'Leyenda',[34,110],'A2')
        _append(legend,['Concepto','Descripción'],header=True)
        notes = [('Proyecto',project.name),('Modo','Resumen por nivel' if options.mode=='nivel' else 'Por trabajador'),('Desde',first),('Hasta',last),('Escala',{'day':'Día','week':'Semana (lunes a domingo)','month':'Mes'}[options.scale]),('Fecha de corte',project.data_date),
                 ('Azul','Intervalo del grupo entre inicio y fin del programa; no implica ocupación continua.'),('Rojo','Grupo con alguna actividad crítica (si se solicitó); en carga, HH superiores a capacidad del período.'),('Verde / naranja','Carga positiva inferior al 80 % / entre 80 % y 100 % de capacidad.'),('◆ / ◀ / ▶','Hitos (cantidad), continúa antes / después del rango. Los hitos se conservan aunque el día no sea laborable.'),('Línea naranja','Período que contiene la fecha de corte; si el corte queda fuera del rango no se dibuja.'),('Gris','Fin de semana o día no laborable del calendario del proyecto. Calendario detalla feriados y excepciones; turnos pueden trabajar fines de semana.'),('Fechas','planned_start/finish, con respaldo early_start/finish. No se inventan fechas reales ejecutadas. Rango inclusivo; semanas/meses extremos parciales.'),('Avance','Media ponderada por HH; sin HH se pondera por duración (hitos: peso 1). Cero es avance cero registrado; no se reconstruye avance histórico.'),('Capacidad','Calendario personal y excepciones: mínimo entre horas de calendario y capacidad diaria × unidades. Recursos inactivos tienen capacidad cero.'),('Carga HH','HH de asignación uniformes por días laborables de la actividad; si faltan, unidades × horas/día. Recortar el rango no redistribuye HH.'),('Límites',f'{MAX_ROWS} filas por resumen/hoja de personas; {MAX_PERIODS} períodos; rango hasta 20 años. Sin imágenes. Escritura secuencial.'),('Uso','Instantánea de consulta. Editar este libro no reprograma ni reimporta Ganttier. Totales con fórmulas recalculadas por Excel al abrir.')]+notes
        for row in notes:
            _append(legend,list(row))
        with NamedTemporaryFile(dir=target.parent,prefix='.gantt-',suffix='.xlsx',delete=False) as handle:
            temporary = Path(handle.name)
        book.save(temporary)
        temporary.replace(target)
    finally:
        close_workbook(book)
        if temporary:
            temporary.unlink(missing_ok=True)
    return target


def export_gantt(conn, pid: int, path: str | Path, options: Options | None = None) -> Path:
    """Instantánea de lectura consistente; conserva la transacción del llamante."""
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute('BEGIN')
    try:
        return _export_gantt(conn, pid, path, options)
    finally:
        if own_transaction:
            conn.rollback()  # Sólo finaliza nuestra transacción de lectura.
