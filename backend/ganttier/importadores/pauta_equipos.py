"""Pautas por TAG o por tipo; expansión acotada y trazable, sin ejecutar fórmulas."""
from __future__ import annotations

from collections import defaultdict
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile
import hashlib
import json
import math
import re

from openpyxl.utils import get_column_letter, column_index_from_string
from .modelos import Perfil, Borrador
from .excel import normal, aviso
from .construccion import CalendarioSupuesto, plantilla_interna
from ganttier.excel_io.reader import load_read_only, rows
from ganttier.excel_io.importer import _convert
from ganttier.excel_io.schema import Column
from openpyxl.utils.datetime import WINDOWS_EPOCH
from ganttier.domain import PeriodicTask, Calendar
from ganttier.calendars import WorkCalendar
from ganttier.maintenance.generator import _dates

ALIASES = {
    'location': ['Instalación', 'Planta'], 'equipment': ['Equipo', 'TAG', 'Equipo/TAG'],
    'type': ['Tipo de equipo', 'Tipo'], 'name': ['Tarea'], 'frequency': ['Frecuencia'],
    'work_hours': ['HH'], 'crew_size': ['Cuadrilla', 'Dotación'],
    'required_skill': ['Habilidad'], 'requires_outage': ['Requiere desconexión', 'Requiere desconexión (SI/NO)'],
    'duration': ['Duración', 'Duración (h/d)'], 'last': ['Última ejecución'],
    'commissioned': ['Fecha de puesta en servicio', 'Puesta en servicio'],
    'priority': ['Prioridad'], 'notes': ['Notas'], 'voltage_kv': ['Tensión', 'Tensión kV'],
    'person': ['Nombre', 'Persona'], 'skills': ['Habilidades'],
}
LOOKUP = {normal(v): k for k, values in ALIASES.items() for v in values}
LIMIT = 100000


def frecuencia(value):
    key = normal(value)
    fixed = {'diaria': 'D', 'diario': 'D', 'diariohabil': 'DH', 'diariahabil': 'DH',
             'semanal': 'W', 'mensual': 'M', 'trimestral': 'Q', 'semestral': 'S', 'anual': 'A'}
    if key in fixed: return fixed[key], 1
    if key.upper() in {'D', 'DH', 'W', 'M', 'Q', 'S', 'A'}: return key.upper(), 1
    if key == 'bienal': return 'NA', 2
    match = re.fullmatch(r'(?:cada)?(\d+)(dias?|semanas?|mes|meses|anos?)', key)
    if match and 1 <= int(match[1]) <= 1000:
        unit = match[2]
        return ('ND' if unit.startswith('dia') else 'NW' if unit.startswith('semana') else 'NM' if unit.startswith('mes') else 'NA'), int(match[1])
    raise ValueError('Frecuencia desconocida; use diaria, semanal, mensual, trimestral, semestral, anual o cada N meses/años.')


def fecha(value):
    return _convert(value, Column('date', 'Fecha', 'date'), WINDOWS_EPOCH)


def numero(value, low=0, high=1e9):
    if isinstance(value, bool): raise ValueError('Se requiere un número.')
    try: result = _convert(value, Column('value', 'Número', 'number'), WINDOWS_EPOCH)
    except (ValueError, TypeError): raise ValueError('Número inválido.') from None
    if not math.isfinite(result) or not low <= result <= high: raise ValueError('Número fuera de rango.')
    return result


def siguiente(anchor, freq, interval):
    if freq in {'D', 'DH', 'ND', 'W', 'NW'}:
        return anchor + timedelta(days=(7 if freq in {'W', 'NW'} else 1) * interval)
    step = {'M': 1, 'NM': interval, 'Q': 3, 'S': 6, 'A': 12, 'NA': 12 * interval}[freq]
    year, month = divmod(anchor.year * 12 + anchor.month - 1 + step, 12)
    return date(year, month + 1, min(anchor.day, monthrange(year, month + 1)[1]))


def abrir(path):
    path = Path(path)
    if path.suffix.lower() != '.xlsx' or path.stat().st_size > 30 * 1024**2:
        raise ValueError('Seleccione un XLSX de hasta 30 MB.')
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(x.file_size for x in entries) > 150 * 1024**2:
            raise ValueError('ZIP excede los límites de seguridad.')
        for entry in entries:
            if entry.filename.startswith('/') or '..' in Path(entry.filename).parts:
                raise ValueError('Ruta ZIP insegura.')
    book = load_read_only(path)
    if len(book.worksheets) > 100:
        book.close(); raise ValueError('Máximo 100 hojas.')
    return book


def mapping(cells):
    result = {}
    for i, cell in enumerate(cells, 1):
        key = LOOKUP.get(normal(cell.value))
        if key:
            if key in result: raise ValueError(f'Encabezado repetido: {key}.')
            result[key] = get_column_letter(i)
    return result


def table(book, sheet, header, override, warnings, excluded, path):
    if sheet not in book:
        matches = [s for s in book.sheetnames if normal(s) == normal(sheet)]
        if len(matches) != 1: raise ValueError(f'Hoja inexistente o ambigua: {sheet}.')
        sheet = matches[0]
    result = []; cols = {}; cache = None; cache_row = 0; cache_values = ()
    # read_only no expone merges: inspección acotada del XML, sin árbol completo.
    with ZipFile(path) as archive, archive.open(book[sheet]._worksheet_path) as stream:
        tail = b''
        while chunk := stream.read(65536):
            data = tail + chunk
            for match in re.finditer(rb'<(?:\w+:)?mergeCell\s+ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"', data):
                if int(match[4]) >= header:
                    warnings.append(aviso('celdas_combinadas', 'Descombine las celdas de la tabla antes de importar.', int(match[2]), hoja=sheet, columna=match[1].decode(), nivel='error'))
            tail = data[-256:]
    try:
        for n, cells in rows(book[sheet]):
            if n > 200001 or len(cells) > 200: raise ValueError(f'{sheet}: máximo 200.000 filas de datos y 200 columnas.')
            if n < header: continue
            if n == header:
                cols = mapping(cells); cols.update(override)
                if len(set(cols.values())) != len(cols): raise ValueError('Mapeo con columnas repetidas.')
                for col in cols.values():
                    if not 1 <= column_index_from_string(col) <= 200: raise ValueError('Columna fuera de rango.')
                continue
            if not any(c.value is not None for c in cells): continue
            item = {}; bad = False
            for key, col in cols.items():
                idx = column_index_from_string(col) - 1
                cell = cells[idx] if idx < len(cells) else None
                value = cell.value if cell else None
                if cell and cell.data_type == 'f':
                    if cache is None:
                        import openpyxl
                        cache = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
                        cache_rows = cache[sheet].iter_rows(values_only=True)
                    while cache_row < n:
                        cache_values = next(cache_rows, ()); cache_row += 1
                    value = cache_values[idx] if idx < len(cache_values) else None
                    if value is None:
                        warnings.append(aviso('formula_sin_cache', 'Fórmula sin valor cacheado.', n, nivel='error', hoja=sheet, columna=col)); bad = True
                if cell and cell.data_type == 'e':
                    warnings.append(aviso('celda_error', str(value), n, nivel='error', hoja=sheet, columna=col)); bad = True
                if key in {'last', 'commissioned'} and isinstance(value, (int, float)) and not isinstance(value, bool):
                    try: value = _convert(value, Column(key, key, 'date'), book.epoch)
                    except ValueError as exc:
                        warnings.append(aviso('fecha_invalida', str(exc), n, nivel='error', hoja=sheet, columna=col)); bad = True
                item[key] = value
            item['_origin'] = dict(hoja=sheet, fila=n, celdas={k: f'{v}{n}' for k, v in cols.items()})
            if bad: excluded.append(dict(origen=item['_origin'], motivo='Celda inválida.'))
            else: result.append(item)
    finally:
        if cache: cache.close()
    return result


class AdaptadorPauta:
    formato = 'pauta_equipos'
    extensiones = {'.xlsx'}

    def detectar(self, path):
        if Path(path).suffix.lower() != '.xlsx': return []
        book = abrir(path)
        try:
            for ws in book:
                for n, cells in rows(ws):
                    if n > 50: break
                    try: cols = mapping(cells)
                    except ValueError: continue
                    base = {'name', 'frequency'} <= cols.keys()
                    flat = {'location', 'equipment'} <= cols.keys()
                    paired = 'type' in cols and any(normal(s) == 'equipos' for s in book.sheetnames)
                    if base and (flat or paired):
                        mode = 'A' if flat else 'B'
                        p = Perfil(id='pauta-equipos', nombre='Pauta de equipos', formato=self.formato,
                                   hoja=ws.title, fila_encabezado=n, mapeo=dict(formato=mode, columnas=cols))
                        return [dict(formato=self.formato, puntaje=1.0, motivos=['Tarea y frecuencia', 'TAG e instalación' if flat else 'Tipo y hoja Equipos'], perfil=p.model_dump())]
            return []
        finally: book.close()

    def previsualizar(self, path, perfil, supuestos):
        path = Path(path)
        s = dict(fecha_inicio='2027-01-01', fecha_fin='2027-12-31', calendario=dict(semana='1111100', horas_dia=8, pais='CL'),
                 moneda='CLP', hh=1, dotacion=1, habilidad='Mantenimiento', desconexion=False, prioridad=50,
                 tolerancia_dias=7, ancla=None, duracion=None, recursos=[], equipos=[], lotes={}, rutas=[])
        for provided in (perfil.supuestos, supuestos or {}):
            if set(provided) - s.keys(): raise ValueError('SUPUESTOS desconocidos: ' + str(sorted(set(provided)-s.keys())))
            s.update(provided)
        if any(s[k] for k in ('equipos', 'lotes', 'rutas')):
            raise ValueError('Pauta de equipos no admite lotes, rutas ni equipos de trabajo; use recursos de personal.')
        start, end = fecha(s['fecha_inicio']), fecha(s['fecha_fin'])
        if not 0 <= (end-start).days <= 3660: raise ValueError('Horizonte creciente, máximo diez años.')
        s['fecha_inicio'], s['fecha_fin'] = start.isoformat(), end.isoformat()
        s['calendario'] = CalendarioSupuesto.model_validate(s['calendario']).model_dump()
        if type(s['desconexion']) is not bool: raise ValueError('desconexion debe ser booleano.')
        for k, lo, hi in [('hh', 0, 1e9), ('dotacion', 1, 100), ('prioridad', 0, 100), ('tolerancia_dias', 0, 3660)]:
            s[k] = numero(s[k], lo, hi)
            if k != 'hh' and s[k] != int(s[k]): raise ValueError(f'{k} debe ser entero.')
        warnings, excluded = [], []
        book = abrir(path)
        try:
            m = perfil.mapeo
            source = table(book, perfil.hoja or 'Pautas', perfil.fila_encabezado, m.get('columnas', {}), warnings, excluded, path)
            mode = m.get('formato', 'A')
            if mode not in {'A', 'B'}: raise ValueError('Formato debe ser A o B.')
            equipment = table(book, m.get('hoja_equipos', 'Equipos'), m.get('fila_equipos', 1), m.get('columnas_equipos', {}), warnings, excluded, path) if mode == 'B' else []
            personnel = table(book, m.get('hoja_personal', 'Personal'), m.get('fila_personal', 1), m.get('columnas_personal', {}), warnings, excluded, path) if any(normal(n) == normal(m.get('hoja_personal', 'Personal')) for n in book.sheetnames) else None
        finally: book.close()
        # Producto por tipo mediante índice; nunca recorre todas las pautas por equipo.
        groups = defaultdict(list)
        for row in source: groups[normal(row.get('type'))].append(row)
        pairs = ((row, None) for row in source)
        if mode == 'B':
            pairs = ((p, e) for e in equipment for p in groups.get(normal(e.get('type')), []))
            seen = set()
            for e in equipment:
                key = (e.get('location'), e.get('equipment'))
                reason = 'Equipo duplicado.' if key in seen else 'Tipo sin pauta.' if normal(e.get('type')) not in groups else ''
                seen.add(key)
                if reason:
                    warnings.append(aviso('equipo_invalido', reason, e['_origin']['fila'], hoja=e['_origin']['hoja'], columna='type', nivel='error'))
                    excluded.append(dict(origen=e['_origin'], motivo=reason))
        masters = []; count = 0; cache = {}; missing = set(); seen_tasks = set(); demand = defaultdict(float); crews = defaultdict(int)
        cal = WorkCalendar(Calendar(id=1, name='Pauta', workweek=s['calendario']['semana'], hours_per_day=s['calendario']['horas_dia'], holidays_country=s['calendario']['pais'] or None), start, end)
        for source_row, eq in pairs:
            row = dict(source_row)
            if eq:
                row.update({k: v for k, v in eq.items() if k != '_origin' and v is not None})
            origin = dict(source_row['_origin'])
            if eq: origin['equipo'] = eq['_origin']
            key = 'name'
            try:
                for key in ('name', 'location', 'equipment', 'frequency'):
                    if not str(row.get(key) or '').strip(): raise ValueError('Campo obligatorio vacío.')
                duplicate = (row['location'], row['equipment'], row['name'])
                if duplicate in seen_tasks: raise ValueError('Equipo × tarea duplicado.')
                seen_tasks.add(duplicate)
                key = 'frequency'; freq, interval = frecuencia(row[key])
                def get(field, assumption):
                    if row.get(field) is not None: return row[field]
                    missing.add(assumption); return s[assumption]
                key = 'work_hours'; hh = numero(get(key, 'hh'))
                key = 'crew_size'; crew = numero(get(key, 'dotacion'), 1, 100)
                if crew != int(crew): raise ValueError('Cuadrilla debe ser entera.')
                key = 'priority'; priority = numero(get(key, 'prioridad'), 0, 100)
                if priority != int(priority): raise ValueError('Prioridad debe ser entera.')
                key = 'requires_outage'; raw = get(key, 'desconexion')
                if normal(str(raw)) not in {'si', 'no', 'true', 'false', '1', '0'}: raise ValueError('Use SI o NO.')
                outage = normal(str(raw)) in {'si', 'true', '1'}
                key = 'last'; anchor = fecha(row[key]) if row.get(key) is not None else None
                day = anchor.day if anchor and freq not in {'D','DH','ND','W','NW'} else None
                if anchor: first = siguiente(anchor, freq, interval)
                elif row.get('commissioned') is not None:
                    key = 'commissioned'; anchor = fecha(row[key]); first = siguiente(anchor, freq, interval)
                    day = anchor.day if freq not in {'D','DH','ND','W','NW'} else None
                else: first = fecha(s['ancla']) if s['ancla'] else start; missing.add('ancla')
                key = 'duration'; duration = max(1, math.ceil(hh / crew / s['calendario']['horas_dia']))
                if row.get(key) is None:
                    missing.add('duracion')
                    if s['duracion'] is not None: duration = math.ceil(numero(s['duracion'], 1, 3660))
                else:
                    match = re.fullmatch(r'\s*([\d.,]+)\s*(h|d|horas?|dias?)?\s*', str(row[key]), re.I)
                    if not match: raise ValueError('Duración inválida: use 8 h o 1 d.')
                    duration = max(1, math.ceil(numero(match[1], .001) / (s['calendario']['horas_dia'] if (match[2] or 'd').lower().startswith('h') else 1)))
                skill = str(get('required_skill', 'habilidad')).strip()
                if not skill: raise ValueError('Habilidad vacía.')
                code = f'PA-{len(masters)+1:06d}'
                task = dict(code=code, name=str(row['name']), frequency=freq, interval=interval,
                            first_due=first.isoformat(), day_of_month=day, work_hours=hh, crew_size=int(crew),
                            duration_days=duration, required_skill=skill, location=str(row['location']), equipment=str(row['equipment']),
                            requires_outage=outage, priority=int(priority), tol_before_days=int(s['tolerancia_dias']), tol_after_days=int(s['tolerancia_dias']))
                key = 'voltage_kv'
                if row.get(key) is not None: task[key] = numero(row[key])
                signature = (freq, interval, first, day)
                if signature not in cache:
                    native = PeriodicTask(id=0, project_id=0, **{**task, 'first_due': first})
                    dates = _dates(native, start, end, set())
                    cache[signature] = sum(1 for _, d in dates if freq != 'DH' or cal.is_workday(date.fromordinal(d)))
                occurrences = cache[signature]; count += occurrences
                if count > LIMIT or len(masters) >= LIMIT:
                    raise OverflowError(f'La expansión supera {LIMIT:,} ocurrencias o maestras (al menos {count:,}). Reduzca horizonte/equipos.')
                origin.update(notas=str(row.get('notes') or ''), tipo=str(row.get('type') or ''))
                masters.append(dict(task, origen=origin, codigo_origen=code, frecuencia_origen=str(row['frequency'])))
                demand[(task['location'], skill)] += occurrences * hh
                crews[(task['location'], skill)] = max(crews[(task['location'], skill)], int(crew))
            except (ValueError, OverflowError) as exc:
                loc = eq['_origin'] if eq and key in eq else source_row['_origin']
                column = loc['celdas'].get(key, key)
                warnings.append(aviso('fila_invalida', str(exc), loc['fila'], hoja=loc['hoja'], columna=column, nivel='error'))
                excluded.append(dict(origen=origin, motivo=str(exc)))
                if isinstance(exc, OverflowError): break
        warnings.insert(0, aviso('expansion_estimada', f'Expansión prevista: {count} ocurrencias; {len(masters)} maestras. Revisar antes de confirmar.'))
        if not masters: warnings.append(aviso('sin_tareas', 'No hay tareas válidas.', nivel='error'))
        resources = list(s['recursos'])
        if personnel is None: missing.add('recursos')
        if personnel is not None:
            names = set()
            for p in personnel:
                name = str(p.get('person') or '').strip(); skills = str(p.get('skills') or p.get('required_skill') or '').strip()
                if not name or not skills or name in names:
                    warnings.append(aviso('personal_invalido', 'Nombre/habilidades vacíos o persona duplicada.', p['_origin']['fila'], hoja=p['_origin']['hoja'], columna='Nombre/Habilidades', nivel='error')); continue
                names.add(name)
                # s.recursos ya contiene el resultado al confirmar la revisión HTTP.
                if not any(r.get('name') == name for r in resources):
                    resources.append(dict(name=name, skills=[v.strip() for v in re.split('[;,]', skills) if v.strip()], max_units=1))
        elif not resources:
            capacity = sum(s['calendario']['horas_dia'] for i in range((end-start).days+1) if cal.is_workday(start+timedelta(days=i)))
            for (site, skill), hours in sorted(demand.items()):
                n = max(crews[(site, skill)], math.ceil(hours / max(1, capacity)))
                for i in range(n): resources.append(dict(name=f'SUPUESTO {site} {skill} {i+1}', skills=[skill], max_units=1))
        s['recursos'] = resources
        labels = {k: s[k] for k in ('fecha_inicio','fecha_fin','calendario','moneda','tolerancia_dias')}
        labels.update({k: s[k] for k in sorted(missing)})
        labels['costos_personal'] = 'Sin costos informados: cero significa desconocido.'
        labels['ventanas'] = 'Ventanas blandas; tolerancia editable en días. No constituye autorización de desconexión.'
        draft = Borrador(perfil=perfil, fuente=dict(nombre=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
                         maestras=masters, sitios=[dict(codigo=x, nombre=x) for x in sorted({t['location'] for t in masters})], wbs=[],
                         advertencias=warnings, faltantes=[dict(clave=k, etiqueta=k, valor=v, estado='SUPUESTO', motivo='Propuesta editable; no es una observación de la fuente.') for k,v in labels.items()],
                         supuestos=s, resumen=dict(items=len(source), equipos=len(equipment) if mode=='B' else len({(t['location'],t['equipment']) for t in masters}), maestras=len(masters), ocurrencias=count),
                         excluidos=excluded, vista_previa=masters[:50], modelo=dict(formato=mode, personal=personnel or []))
        return draft

    def convertir(self, borrador, nombre=None):
        if borrador.tiene_errores: raise ValueError('Corrija los errores del borrador antes de convertir.')
        bundle = plantilla_interna(borrador, nombre)[0]
        bundle.project['description'] += '\nSUPUESTOS: ' + json.dumps(borrador.faltantes, ensure_ascii=False)
        return bundle
