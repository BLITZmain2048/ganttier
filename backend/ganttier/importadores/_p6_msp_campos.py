"""Vocabulario externo y conversiones con las reglas numéricas nativas."""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from functools import lru_cache
from openpyxl.utils.cell import get_column_letter, column_index_from_string
from openpyxl.utils.datetime import WINDOWS_EPOCH
from ganttier.excel_io.importer import _convert
from ganttier.excel_io.schema import Column
from .excel import normal as _normal
from ._p6_msp_lectura import EMPTY, MAX_ROWS, MAX_COLS, issue


@lru_cache(maxsize=8192)
def normal(value):
    return _normal(value) if value else ''

ALIASES = {
    'code': 'ID|Unique ID|Id único|Activity ID|task_code',
    'internal_id': 'task_id',
    'name': 'Nombre de tarea|Task Name|Activity Name|Nombre de actividad|task_name',
    'duration': 'Duración|Duration|Original Duration|Planned Duration|target_drtn_hr_cnt',
    'remaining': 'Duración restante|Remaining Duration|remain_drtn_hr_cnt',
    'start': 'Comienzo|Start|Planned Start|target_start_date',
    'finish': 'Fin|Finish|Planned Finish|target_end_date',
    'actual_start': 'Comienzo real|Inicio real|Actual Start|act_start_date',
    'actual_finish': 'Fin real|Actual Finish|act_end_date',
    'actual_hours': 'Trabajo real|Actual Work|act_work_qty',
    'predecessors': 'Predecesoras|Predecessors',
    'resources': 'Nombres de los recursos|Resource Names',
    'percent': '% completado|% Complete|Physical % Complete|phys_complete_pct',
    'level': 'Nivel de esquema|Outline Level',
    'summary': 'Resumen|Summary',
    'milestone': 'Hito|Milestone',
    'type': 'Activity Type|task_type',
    'wbs': 'WBS|WBS Code|EDT',
    'wbs_id': 'wbs_id',
    'calendar': 'Calendar|Calendario|clndr_id',
    'constraint': 'Restricción|Tipo de restricción|Constraint Type|Primary Constraint|cstr_type',
    'constraint_date': 'Fecha de restricción|Constraint Date|Primary Constraint Date|cstr_date',
    'cost': 'Costo|Cost|Budgeted Total Cost|target_cost',
    'work': 'Trabajo|Work|Budgeted Labor Units|target_work_qty',
    'notes': 'Notas|Notes',
}
LOOKUP = {normal(v): key for key, values in ALIASES.items() for v in values.split('|')}
CONSTRAINTS = {}
for code, names in {
    'ASAP': 'ASAP|Lo antes posible|As Soon As Possible|0',
    'ALAP': 'ALAP|Lo más tarde posible|As Late As Possible|1|CS_ALAP',
    'MSO': 'MSO|Debe comenzar el|Must Start On|Start On|Mandatory Start|2|CS_MSO',
    'MFO': 'MFO|Debe finalizar el|Must Finish On|Finish On|Mandatory Finish|3|CS_MEO|CS_MFO',
    'SNET': 'SNET|No comenzar antes del|Start No Earlier Than|Start On or After|4|CS_SNET',
    'SNLT': 'SNLT|No comenzar después del|Start No Later Than|Start On or Before|5|CS_SNLT',
    'FNET': 'FNET|No finalizar antes del|Finish No Earlier Than|Finish On or After|6|CS_FNET',
    'FNLT': 'FNLT|No finalizar después del|Finish No Later Than|Finish On or Before|7|CS_FNLT',
}.items():
    CONSTRAINTS.update({normal(v): code for v in names.split('|')})


def identifier(v):
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    return str(v).strip()


def number(v):
    return float(_convert(v, Column('', '', 'number'), WINDOWS_EPOCH))


def boolean(v):
    if normal(v) in {'yes', 'y', 's'}: return True
    if normal(v) in {'n'}: return False
    return _convert(v, Column('', '', 'bool'), WINDOWS_EPOCH)


@lru_cache(maxsize=8192)
def fecha(v, epoch=WINDOWS_EPOCH):
    if isinstance(v, str):
        v = v.strip()
        # P6 exports can include the time; preserve the civil date, not timezone shifts.
        if re.match(r'^\d{4}-\d\d-\d\d[ T]\d\d:', v):
            v = v[:10]
    return _convert(v, Column('', '', 'date'), epoch).isoformat()


def percent(cell):
    v = cell.value
    if isinstance(v, (float, int)) and '%' in cell.number_format:
        v *= 100
    return float(_convert(v, Column('', '', 'percent'), WINDOWS_EPOCH))


@lru_cache(maxsize=8192)
def duration(v, hours, week, *, raw_hours=False, signed=False):
    """Fractional workdays stay exact here; the caller chooses an explicit rounding policy."""
    text = str(v).strip()
    match = re.fullmatch(r'([+-]?[\d.,]+)\s*([A-Za-záéíóúÁÉÍÓÚ]+)?\??', text)
    if not match: raise ValueError('Duración inválida; use días, semanas u horas (p. ej. 4 d).')
    n = number(match[1].lstrip('+-'))
    if match[1].startswith('-'): n = -n
    if n < 0 and not signed: raise ValueError('La duración no puede ser negativa.')
    unit = normal(match[2] or ('h' if raw_hours else 'd'))
    if unit in {'h', 'hr', 'hrs', 'hour', 'hours', 'hora', 'horas'}: return n / hours
    if unit in {'d', 'day', 'days', 'dia', 'dias'}: return n
    if unit in {'w', 'wk', 'week', 'weeks', 'sem', 'semana', 'semanas'}: return n * week.count('1')
    raise ValueError('Unidad no admitida; días transcurridos y meses requieren conversión explícita.')


def work(v, hours, week):
    return duration(v, hours, week, raw_hours=True) * hours


def integral(value, policy, *, lag=False):
    if math.isclose(value, round(value), abs_tol=1e-9): return int(round(value))
    if policy == 'rechazar': raise ValueError('El motor usa días enteros; seleccione redondeo explícito o corrija la fuente.')
    return math.ceil(value)


def predecessors(text, known, hours, week):
    # Semicolon disambiguates decimal commas in lags; comma is otherwise a list separator.
    parts = re.split(r';|,(?!\d+\s*(?:d|h|w|sem)\b)', str(text), flags=re.I)
    for part in parts:
        part = part.strip()
        if not part: continue
        if part in known:
            yield part, 'FS', 0.0
            continue
        match = re.fullmatch(r'(.+?)\s*(FS|SS|FF|SF|FC|CC|CF)\s*([+-]\s*[\d.,]+\s*[a-záéíóú]*)?', part, re.I)
        if not match:
            if re.search(r'\s|[+]', part): raise ValueError(f'Predecesora inválida: {part}.')
            yield part, 'FS', 0.0
            continue
        pred, typ, lag = match.groups()
        typ = {'FC': 'FS', 'CC': 'SS', 'CF': 'SF'}.get(typ.upper(), typ.upper())
        yield pred.strip(), typ, duration(lag.replace(' ', ''), hours, week, signed=True) if lag else 0.0


def resource_names(text):
    for token in re.split(r';|,(?!\d+\s*%?\])', str(text)):
        if not token.strip(): continue
        match = re.fullmatch(r'\s*([^\[\]]+?)\s*(?:\[\s*([\d.,]+)\s*%\s*\])?\s*', token)
        if not match: raise ValueError(f'Recurso inválido: {token}; use Nombre[50%].')
        yield match[1].strip(), number(match[2]) / 100 if match[2] else 1.0


class Row:
    __slots__ = ('sheet', 'index', 'cells', 'mapping', 'headers', 'errors', 'epoch')
    def __init__(self, sheet, index, cells, mapping, headers, errors, epoch):
        self.sheet, self.index, self.cells = sheet, index, cells
        self.mapping, self.headers, self.errors, self.epoch = mapping, headers, errors, epoch

    def cell(self, key):
        pos = self.mapping.get(key, 0)
        return self.cells[pos - 1] if pos and pos <= len(self.cells) else EMPTY

    def get(self, key, converter=identifier, default=None, required=False):
        if key not in self.mapping:
            if required: self.error(key, 'Columna obligatoria ausente.')
            return default
        value = self.cell(key).value
        if value is None or (isinstance(value, str) and not value.strip()):
            if required: self.error(key, 'Campo obligatorio vacío.')
            return default
        try: return converter(value)
        except (ValueError, TypeError, OverflowError) as exc:
            self.error(key, str(exc))
            return default

    def error(self, key, message, code='dato_invalido', level='error'):
        issue(self.errors, self.sheet, self.index, get_column_letter(self.mapping.get(key, 1)), message, code, level)

    def origin(self):
        values = {}
        for key, pos in self.mapping.items():
            value = self.cells[pos - 1].value if pos <= len(self.cells) else None
            if value is not None: values[key] = identifier(value)
        return dict(hoja=self.sheet, fila=self.index,
                    celdas={k: f'{get_column_letter(v)}{self.index}' for k, v in self.mapping.items()},
                    valores=values)

    def raw_hours(self, key):
        pos = self.mapping.get(key, 0)
        return bool(pos and self.headers[pos - 1].endswith('hrcnt'))


def table(tables, sheet, errors, mapping=None, header_row=1, aliases=None):
    """Yield physical rows; configured mappings accept Excel letters or header labels."""
    headers, resolved, count = [], {}, 0
    lookup = aliases or LOOKUP
    found = False
    for i, cells in tables.rows(sheet):
        if i < header_row: continue
        if i == header_row:
            found = True
            headers = [normal(c.value) for c in cells]
            seen = set()
            for j, h in enumerate(headers, 1):
                if h and h in seen: issue(errors, sheet, i, get_column_letter(j), 'Encabezado repetido.', 'encabezado_repetido')
                seen.add(h)
                if h in lookup:
                    key = lookup[h]
                    if key in resolved: issue(errors, sheet, i, get_column_letter(j), f'Dos columnas representan {key}.', 'mapeo_ambiguo')
                    resolved[key] = j
            for key, value in (mapping or {}).items():
                if key not in set(lookup.values()): raise ValueError(f'Campo de mapeo desconocido: {key}.')
                if value in (None, ''):
                    resolved.pop(key, None)
                    continue
                if normal(value) in headers: pos = headers.index(normal(value)) + 1
                else:
                    try: pos = column_index_from_string(str(value).upper())
                    except ValueError: raise ValueError(f'Columna inexistente: {value}.') from None
                if pos > MAX_COLS: raise ValueError('Mapeo fuera de las 200 columnas admitidas.')
                resolved[key] = pos
            continue
        if not any(c.value is not None and c.value != '' for c in cells): continue
        if headers and cells and normal(cells[0].value) == headers[0] and [normal(c.value) for c in cells] == headers:
            issue(errors, sheet, i, 'A', 'Encabezado repetido entre datos.', 'encabezado_repetido')
            continue
        count += 1
        if count > MAX_ROWS: raise ValueError('La tabla supera 200.000 filas de datos.')
        for j, cell in enumerate(cells, 1):
            if cell.data_type == 'e': issue(errors, sheet, i, get_column_letter(j), str(cell.value), 'celda_excel')
        yield Row(sheet, i, cells, resolved, headers, errors, tables.epoch)
    if not found: issue(errors, sheet, header_row, 'A', 'No existe la fila de encabezado.', 'hoja_vacia')
