"""Importación aditiva de personal y avance sobre proyectos existentes."""
from __future__ import annotations

from collections import defaultdict, namedtuple
from datetime import date
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from zipfile import ZipFile
import hashlib

from .. import repo, services
from .reader import load_read_only, rows
from .schema import columns, normalize, SCHEMAS
from .importer import _convert

PERSONAL = columns('name:Nombre:text!|role:Rol|skills:Habilidades:skills|calendar_name:Calendario|shift:Turno|anchor:Ancla:date|pattern:Patron|hours:Horas_Dia:number|cost_hour:Costo_HH:number|overtime_cost_hour:Costo_HH_Extra:number|max_overtime_hours_day:Max_HH_Extra_Dia:number|max_units:Unidades:number')
SHEETS = {
    'Personal': PERSONAL,
    'Ausencias': columns('name:Persona:text!|start:Desde:date!|end:Hasta:date!|reason:Tipo:text!|hours:Horas_Disponibles:number'),
    'Calendarios': columns('name:Calendario:text!|date:Fecha:date!|reason:Motivo'),
    'Equipos': SCHEMAS['Equipos'],
    'Avance': SCHEMAS['Avance'] + columns('percent_mode:Modo_Porcentaje'),
    'Avance_Trabajador': columns('activity_code:Actividad:text!|date:Fecha:date!|resource_name:Trabajador:text!|actual_hours:HH_Reales:number!|note:Nota'),
}


def _issue(result, sheet, row, column, message, severity='error'):
    result['errors' if severity == 'error' else 'warnings'].append(dict(
        sheet=sheet, row=row, column=column, message=str(message), severity=severity))


def _read(path, kind, result):
    wanted = ('Personal', 'Ausencias', 'Calendarios', 'Equipos') if kind == 'personal' else ('Avance', 'Avance_Trabajador')
    output = {s: [] for s in wanted}
    with ZipFile(path) as archive:
        info = archive.infolist()
        if len(info) > 2000 or sum(i.file_size for i in info) > 512 * 1024**2:
            raise ValueError('Libro demasiado grande (máximo 512 MiB descomprimidos).')
        if any(i.file_size > 1024 * max(i.compress_size, 1) for i in info):
            raise ValueError('Compresión ZIP excesiva.')
    book = load_read_only(path)
    convert = lru_cache(maxsize=8192)(_convert)
    try:
        names = {normalize(s): s for s in wanted}
        found = set()
        total = 0
        for ws in book:
            canonical = names.get(normalize(ws.title))
            if not canonical:
                continue
            if canonical in found:
                _issue(result, ws.title, 1, 'Hoja', 'Hoja duplicada.')
                continue
            found.add(canonical)
            specs = SHEETS[canonical]
            lookup = {normalize(alias): col for col in specs for alias in (col.field, col.title)}
            iterator = iter(rows(ws))
            _, header = next(iterator, (1, []))
            if len(header) > 200:
                raise ValueError('Límite: 200 columnas.')
            mapping, seen = {}, set()
            for idx, cell in enumerate(header):
                col = lookup.get(normalize(cell.value))
                if col:
                    if col.field in seen:
                        _issue(result, ws.title, 1, col.title, 'Encabezado duplicado.')
                    seen.add(col.field)
                    mapping[idx] = col
                elif cell.value:
                    _issue(result, ws.title, 1, str(cell.value), 'Columna desconocida.', 'warning')
            for col in specs:
                if col.required and col.field not in seen:
                    _issue(result, ws.title, 1, col.title, 'Falta columna obligatoria.')
            for number, cells in iterator:
                if not any(c.value is not None for c in cells):
                    continue
                total += 1
                if total > 200000 or len(cells) > 200:
                    raise ValueError('Límite: 200 000 filas y 200 columnas.')
                item, bad = {}, False
                for idx, col in mapping.items():
                    cell = cells[idx] if idx < len(cells) else None
                    value = cell.value if cell else None
                    try:
                        if cell and cell.data_type in ('f', 'e'):
                            raise ValueError('Pegue valores literales; no se admiten fórmulas ni errores Excel.')
                        if value is None or value == '':
                            if col.required:
                                raise ValueError('Campo obligatorio vacío.')
                            continue
                        if col.kind == 'percent' and isinstance(value, (int, float)) and '%' in cell.number_format:
                            value *= 100
                        item[col.field] = convert(value, col, book.epoch)
                    except (ValueError, TypeError) as exc:
                        _issue(result, ws.title, number, col.title, exc)
                        bad = True
                if not bad:
                    output[canonical].append((number, item))
        if wanted[0] not in found:
            _issue(result, wanted[0], 1, 'Hoja', 'Falta la hoja principal.')
    finally:
        book.close()
    return output


def _unique(items, label):
    result = {}
    for item in items:
        if item.name in result:
            raise ValueError(f'{label} ambiguo: {item.name}.')
        result[item.name] = item
    return result


def _progress(conn, pid, data, result, allow_regressions, *, collect=False):
    from ..api.schemas import ProgressEntry
    normalize_mode = lru_cache(maxsize=16)(normalize)
    project = repo.get(conn, 'projects', pid)
    activities = {a.code: a for a in repo.list_entities(conn, 'activities', pid)}
    by_id = {a.id: a for a in activities.values()}
    Worker = namedtuple('Worker', 'id name kind')
    workers = _unique((Worker(*r) for r in conn.execute(
        'SELECT id,name,kind FROM resources WHERE project_id=? OR project_id IS NULL', (pid,))), 'Trabajador')
    @lru_cache(maxsize=4096)
    def validated_values(values):
        # Los identificadores ya se resolvieron contra el proyecto. Reutilizar la
        # validación de valores repetidos evita 100 000 modelos idénticos.
        return ProgressEntry(activity_id=0, **dict(values)).model_dump(exclude={'id', 'activity_id', 'resource_id'})
    old = repo.list_entities(conn, 'progress', pid)
    existing = defaultdict(list)
    for p in old:
        existing[(p.activity_id, p.date, p.resource_id)].append(p)
    incoming, sources = {}, {}
    for sheet in ('Avance', 'Avance_Trabajador'):
        for number, source in data[sheet]:
            row = dict(source)
            column = 'Actividad'
            try:
                activity = activities.get(row.pop('activity_code'))
                if activity is None:
                    raise ValueError('La actividad no pertenece al proyecto.')
                column = 'Trabajador'
                name = row.pop('resource_name', None)
                worker = workers.get(name) if name else None
                if name and (worker is None or worker.kind not in ('trabajador', 'cuadrilla')):
                    raise ValueError('Trabajador inexistente o de tipo incompatible.')
                row.update(activity_id=activity.id, resource_id=worker.id if worker else None)
                column = 'Fecha'
                if row['date'] > (project.data_date or date.today()):
                    raise ValueError('La fecha supera el corte al fin de jornada.')
                key = (activity.id, row['date'], row['resource_id'])
                if key in incoming:
                    raise ValueError('Clave actividad/fecha/trabajador duplicada en el libro.')
                incoming[key] = row
                sources[key] = (sheet, number)
            except (ValueError, KeyError) as exc:
                _issue(result, sheet, number, column, exc)
    # Historial completo ordenado: las correcciones no pueden crear retrocesos posteriores.
    events = defaultdict(list)
    for p in old:
        key = (p.activity_id, p.date, p.resource_id)
        if key not in incoming:
            events[p.activity_id].append((p.date, 0, p.id, key, p))
    for i, (key, row) in enumerate(incoming.items()):
        events[key[0]].append((key[1], 1, i, key, row))
    detailed_days = {(k[0], k[1]) for k in (*incoming, *existing) if k[2] is not None}
    for key, row in incoming.items():
        if key[2] is None and key[:2] in detailed_days and row.get('actual_hours', 0):
            sheet, number = sources[key]
            _issue(result, sheet, number, 'HH_Reales', 'Con detalle por trabajador, las HH sin trabajador deben ser cero para evitar doble conteo.')
        if key[2] is not None and (key[0], key[1], None) not in incoming:
            if any(p.actual_hours for p in existing.get((key[0], key[1], None), [])):
                sheet, number = sources[key]
                _issue(result, sheet, number, 'HH_Reales', 'El historial tiene HH sin trabajador ese día; incluya su corrección a cero antes del detalle.')
    changes = []
    for aid, history in events.items():
        previous = 0.0
        start = None
        current_day, day_base, day_percent = None, 0, None
        last_source = None
        activity = by_id[aid]
        for day, new, _, key, value in sorted(history):
            if day != current_day:
                current_day, day_base, day_percent = day, previous, None
            if not new:
                if value.percent_complete < previous and last_source:
                    _issue(result, *last_source, 'Porcentaje', 'El cambio supera un porcentaje posterior del historial.',
                           'warning' if allow_regressions else 'error')
                previous = value.percent_complete
                day_percent = previous
                if value.actual_start:
                    start = min(start, value.actual_start) if start else value.actual_start
                continue
            sheet, number = sources[key]
            last_source = (sheet, number)
            try:
                mode = normalize_mode(value.pop('percent_mode', 'acumulado'))
                if mode not in ('acumulado', 'periodo'):
                    raise ValueError('Modo_Porcentaje debe ser acumulado o periodo.')
                pct = value.get('percent_complete', previous)
                if mode == 'periodo':
                    # Base anterior al día, no al trabajador: un físico por actividad y fecha.
                    pct += day_base
                if 'percent_complete' in value and day_percent is not None and pct != day_percent:
                    raise ValueError('Los trabajadores de una actividad y fecha deben informar el mismo porcentaje físico.')
                if 'percent_complete' in value:
                    day_percent = pct
                if pct < previous:
                    _issue(result, sheet, number, 'Porcentaje', 'Retroceso del porcentaje físico.',
                           'warning' if allow_regressions else 'error')
                value['percent_complete'] = pct
                previous = pct
                if pct > 0 and not value.get('actual_start'):
                    candidates = [x for x in (start, activity.actual_start) if x and x <= day]
                    value['actual_start'] = min(candidates) if candidates else min(activity.planned_start or day, day)
                if pct >= 100 and not value.get('actual_finish'):
                    value['actual_finish'] = day
                if value.get('actual_finish') and not value.get('actual_start'):
                    value['actual_start'] = start or activity.actual_start
                    if not value['actual_start']:
                        raise ValueError('Una actividad terminada requiere un inicio real.')
                if value.get('actual_start'):
                    start = min(start, value['actual_start']) if start else value['actual_start']
                validated = validated_values(tuple((k, v) for k, v in value.items() if k not in ('activity_id', 'resource_id')))
                if any(validated.get(f) and validated[f] > day for f in ('actual_start', 'actual_finish')):
                    raise ValueError('Las fechas reales no pueden superar la fecha del registro.')
                matches = existing.get(key, [])
                if len(matches) > 1:
                    _issue(result, sheet, number, 'Actividad', 'El historial tiene múltiples registros para esta clave; resuelva la ambigüedad antes de importar.')
                elif matches and all(getattr(matches[0], field) == val for field, val in validated.items()):
                    result['ignored'] += 1
                else:
                    result['updated' if matches else 'created'] += 1
                    if collect:
                        changes.append((matches[0].id if matches else None,
                                        dict(validated, activity_id=aid, resource_id=key[2])))
            except ValueError as exc:
                _issue(result, sheet, number, 'Porcentaje/Fechas/HH', exc)
    return changes


class _Preview(Exception):
    pass


def _personal(conn, pid, data, result, modo):
    from ..api import schemas
    from ..resource_absences import set_absence
    from ..teams import save_team
    resources = _unique([r for r in repo.list_entities(conn, 'resources', pid) if r.project_id == pid], 'Persona')
    global_names = {r.name for r in repo.list_entities(conn, 'resources', pid) if r.project_id is None}
    calendars = _unique(repo.list_entities(conn, 'calendars'), 'Calendario')
    retained, seen = set(), set()
    holidays = defaultdict(list)
    for number, row in data['Calendarios']:
        holidays[row['name']].append(dict(date=row['date'], working=False, hours=0, reason=row.get('reason', 'Feriado propio')))
    for number, source in data['Personal']:
        try:
            row = dict(source)
            name = row['name']
            if name in global_names:
                raise ValueError('El nombre coincide con un recurso global; use un nombre local inequívoco.')
            if name in seen:
                raise ValueError('Persona duplicada.')
            seen.add(name)
            previous = resources.get(name)
            if previous and previous.is_work_team:
                raise ValueError('El nombre corresponde a un equipo de trabajo.')
            calendar_name = row.pop('calendar_name', None)
            shift, anchor, pattern = row.pop('shift', None), row.pop('anchor', None), row.pop('pattern', None)
            hours = row.pop('hours', previous.capacity_hours_day if previous else 8)
            if not 0 < hours <= 24:
                raise ValueError('Horas_Dia debe estar entre 0 y 24, excluido cero.')
            role = row.pop('role', None)
            if role:
                row['skills'] = list(dict.fromkeys([role, *row.get('skills', [])]))
            cid = previous.calendar_id if previous else None
            if calendar_name:
                if calendar_name not in calendars and calendar_name not in holidays:
                    raise ValueError('Calendario desconocido.')
                if calendar_name in calendars:
                    cid = calendars[calendar_name].id
            if shift or calendar_name in holidays or 'hours' in source:
                cid = cid or repo.get(conn, 'projects', pid).calendar_id
                definition = dict(name=f'I4 · {pid} · {name} · {result["source_sha256"][:12]}', hours_per_day=hours,
                                  workweek='1111100', holidays_country='CL')
                if cid:
                    definition.update({k: getattr(repo.get(conn, 'calendars', cid), k) for k in
                                       ('workweek', 'shift_pattern', 'shift_anchor', 'holidays_country', 'holidays_apply_to_shift', 'exceptions')})
                if shift:
                    shift = shift.lower()
                    if shift == 'personalizado':
                        shift = pattern
                    definition.update(shift_pattern=None if shift == '5x2' else shift, shift_anchor=anchor)
                    if shift != '5x2' and not shift:
                        raise ValueError('El turno personalizado requiere Patron NxM y Ancla.')
                if calendar_name in holidays:
                    merged = {e.date: repo.json_value(e) for e in definition.get('exceptions', [])}
                    for ex in holidays[calendar_name]:
                        merged[ex['date']] = ex
                    definition['exceptions'] = list(merged.values())
                definition = schemas.Calendar(**definition).model_dump(exclude={'id'})
                oldcal = calendars.get(definition['name'])
                if oldcal:
                    cid = oldcal.id
                else:
                    created = repo.create(conn, 'calendars', definition)
                    calendars[created.name] = created
                    cid = created.id
            row.update(project_id=pid, capacity_hours_day=hours, calendar_id=cid, active=True)
            values = {**(repo.json_value(previous) if previous else {}), **row}
            values = schemas.Resource(**values).model_dump(exclude={'id'})
            if previous:
                resource = repo.update(conn, 'resources', previous.id, values)
                result['updated'] += 1
            else:
                resource = repo.create(conn, 'resources', values)
                result['created'] += 1
            resources[name] = resource
            retained.add(resource.id)
        except (ValueError, LookupError) as exc:
            _issue(result, 'Personal', number, 'Nombre/Calendario/Turno/Horas', exc)
    for number, row in data['Equipos']:
        try:
            prior = resources.get(row['name'])
            team = save_team(conn, pid, row, prior.id if prior else None)
            retained.add(team['id'])
            result['updated' if prior else 'created'] += 1
        except (ValueError, LookupError) as exc:
            _issue(result, 'Equipos', number, 'Equipo', exc)
    referenced_calendars = {row.get('calendar_name') for _, row in data['Personal']}
    for number, row in data['Calendarios']:
        if row['name'] not in referenced_calendars:
            _issue(result, 'Calendarios', number, 'Calendario', 'El feriado debe referenciarse desde Personal; no se modifican calendarios compartidos directamente.')
    for number, row in data['Ausencias']:
        try:
            if normalize(row['reason']) not in ('vacaciones', 'licencia', 'permiso', 'capacitacion'):
                raise ValueError('Tipo debe ser vacaciones, licencia, permiso o capacitación.')
            person = resources.get(row['name'])
            if not person:
                raise ValueError('Persona inexistente en este proyecto.')
            set_absence(conn, person.id, row['start'], row['end'], row.get('hours', 0), row['reason'])
            result['updated'] += 1
        except (ValueError, LookupError) as exc:
            _issue(result, 'Ausencias', number, 'Persona/Desde/Hasta/Horas_Disponibles', exc)
    if modo == 'replace':
        for resource in resources.values():
            if resource.id not in retained and resource.kind in ('trabajador', 'cuadrilla') and resource.active:
                repo.update(conn, 'resources', resource.id, {'active': False})
                result['deactivated'] += 1


def import_workbook(conn, pid, path, kind, *, dry_run=True, modo='merge', allow_regressions=False):
    """Devuelve diagnóstico; no persiste filas si existe algún error."""
    if kind not in ('personal', 'avance') or modo not in ('merge', 'replace'):
        raise ValueError('Formato o modo inválido.')
    repo.get(conn, 'projects', pid)
    started = perf_counter()
    result = dict(dry_run=dry_run, applied=False, created=0, updated=0, ignored=0, deactivated=0,
                  errors=[], warnings=[], backup=None, source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
    try:
        data = _read(path, kind, result)
    except Exception as exc:
        _issue(result, 'Libro', 0, 'Archivo', exc)
        data = {}
    if not result['errors']:
        # Preview personal reutiliza validaciones transaccionales de calendarios, ausencias y equipos.
        try:
            with repo.transaction(conn):
                if kind == 'personal':
                    _personal(conn, pid, data, result, modo)
                else:
                    _progress(conn, pid, data, result, allow_regressions)
                raise _Preview
        except _Preview:
            pass
        if not dry_run and not result['errors']:
            if conn.in_transaction:
                raise ValueError('Confirme la transacción previa antes de importar con respaldo.')
            from ..backups import create_backup
            result['backup'] = repo.json_value(create_backup(conn, f'pre-importacion-{kind}-p{pid}', 'pre'))
            with repo.transaction(conn, immediate=True):
                if kind == 'personal':
                    result.update(created=0, updated=0, ignored=0, deactivated=0)
                    _personal(conn, pid, data, result, modo)
                    if result['errors']:
                        raise ValueError('El estado cambió después de la previsualización; no se aplicó el libro.')
                else:
                    # Revalidar bajo reserva de escritura frente a cambios concurrentes.
                    result.update(created=0, updated=0, ignored=0)
                    changes = _progress(conn, pid, data, result, allow_regressions, collect=True)
                    if result['errors']:
                        raise ValueError('El estado cambió después de la previsualización; no se aplicó el libro.')
                    from ..api.control import create_progress
                    from ..api.schemas import ProgressEntry
                    for old_id, row in changes:
                        if old_id:
                            repo.delete(conn, 'progress', old_id)
                    if changes:
                        create_progress(pid, [ProgressEntry(**row) for _, row in changes], conn)
                result['applied'] = True
    result['elapsed_s'] = perf_counter() - started
    return result


def build_template(path, kind):
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    book = Workbook()
    instructions = book.active
    instructions.title = 'Instrucciones'
    for line in ('Ganttier · ' + kind, 'Proyecto existente. Simulación por defecto; revisar errores antes de aplicar.',
                 'Fechas AAAA-MM-DD o DD/MM/AAAA; números con coma decimal. Solo valores, sin fórmulas.',
                 'Porcentaje acumulado 0..100. HH del período, nunca acumuladas. Una fila por actividad/fecha/trabajador.',
                 'Avance_Trabajador complementa Avance: no repita las mismas HH en ambas hojas.',
                 'Horas_Disponibles: disponibilidad restante por día (0 = ausencia completa).',
                 'Rol se agrega a Habilidades. Unidades=1 para ausencias. 5x2=lunes a viernes.',
                 'Calendarios: feriados propios aplicados a copias privadas de las personas que lo referencian.'):
        instructions.append([line])
    names = ('Personal', 'Ausencias', 'Calendarios', 'Equipos') if kind == 'personal' else ('Avance', 'Avance_Trabajador')
    for name in names:
        sheet = book.create_sheet(name)
        sheet.append([col.title for col in SHEETS[name]])
        sheet.freeze_panes = 'A2'
        for cell, col in zip(sheet[1], SHEETS[name]):
            cell.comment = Comment(f'{col.title} ({col.field}). Tipo: {col.kind}. ' + ('Obligatorio.' if col.required else 'Opcional; conserva el valor existente o usa el predeterminado del dominio.'), 'Ganttier')
            cell.font = Font(color='FFFFFF', bold=True)
            cell.fill = PatternFill('solid', fgColor='17354A')
            sheet.column_dimensions[cell.column_letter].width = 24
            choices = {'shift': '5x2,7x7,14x14,personalizado', 'reason': 'vacaciones,licencia,permiso,capacitación', 'percent_mode': 'acumulado,periodo'}.get(col.field)
            if choices and name != 'Calendarios':
                validation = DataValidation(type='list', formula1='"' + choices + '"')
                sheet.add_data_validation(validation)
                validation.add(f'{cell.column_letter}2:{cell.column_letter}200001')
    book.save(path)
    book.close()
