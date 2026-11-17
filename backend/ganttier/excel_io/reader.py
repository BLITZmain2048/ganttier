"""Filas XLSX secuenciales con metadatos de openpyxl read_only.

Expat evita construir un árbol y objetos de texto enriquecido por celda. El
libro, estilos, shared strings y conversiones de fechas siguen siendo de
openpyxl. No se evalúan fórmulas, DTD ni entidades externas. La comparación con
el lector público cubre los tipos del contrato y los dos sistemas de fechas.
"""
from collections import deque, namedtuple
from xml.parsers import expat
from xml.etree.ElementTree import ParseError

from openpyxl.styles.numbers import BUILTIN_FORMATS
from openpyxl.utils.cell import column_index_from_string
from openpyxl.utils.datetime import from_excel, from_ISO8601
from openpyxl.reader.excel import ExcelReader
from openpyxl.worksheet._read_only import ReadOnlyWorksheet

Cell = namedtuple('Cell', 'value data_type number_format')
EMPTY = Cell(None, 'n', 'General')
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
ROW, CELL, VALUE, TEXT, FORMULA = [NS + name for name in ('row', 'c', 'v', 't', 'f')]
SHEET_DATA, PHONETIC = NS + 'sheetData', NS + 'rPh'


class _SequentialWorksheet(ReadOnlyWorksheet):
    def _get_size(self):
        # Sin <dimension>, openpyxl recorre sheetData entero al abrir el libro.
        # No necesitamos dimensiones: rows() usa las coordenadas físicas.
        self._min_row = self._min_column = 1
        self._max_row = self._max_column = None


class _SequentialExcelReader(ExcelReader):
    def read_worksheets(self):
        for sheet, rel in self.parser.find_sheets():
            if rel.target not in self.valid_files or 'chartsheet' in rel.Type:
                continue
            worksheet = _SequentialWorksheet(self.wb, sheet.name, rel.target, self.shared_strings)
            worksheet.sheet_state = sheet.state
            self.wb._sheets.append(worksheet)


def load_read_only(path):
    """Mismo lector de libro openpyxl, sin la pasada anticipada de dimensiones."""
    reader = _SequentialExcelReader(path, read_only=True, data_only=False, keep_links=False)
    try:
        reader.read()
        return reader.wb
    except Exception:
        reader.archive.close()
        raise


def rows(worksheet):
    """Entrega (fila real, celdas) en bloques acotados, ignorando dimensiones."""
    workbook = worksheet.parent
    formats = []
    for style in workbook._cell_styles:
        fmt = style.numFmtId
        formats.append(BUILTIN_FORMATS.get(fmt, 'General') if fmt < 164 else workbook._number_formats[fmt - 164])
    shared = worksheet._shared_strings
    date_styles, duration_styles = workbook._date_formats, workbook._timedelta_formats
    pending = deque()
    rownum = column = style_id = 0
    cells = []
    kind = 'n'
    value_parts, text_parts, formula_parts = [], [], []
    capture = None
    in_data = in_cell = formula = phonetic = inline_present = False

    def start(tag, attrs):
        nonlocal in_data, in_cell, rownum, cells, column, kind, style_id
        nonlocal value_parts, text_parts, formula_parts, capture, formula, phonetic, inline_present
        if tag == SHEET_DATA:
            in_data = True
        elif in_data and tag == ROW:
            rownum = int(attrs.get('r', rownum + 1))
            cells, column = [], 0
        elif in_data and tag == CELL:
            in_cell = True
            coordinate = attrs.get('r')
            column = column_index_from_string(coordinate.rstrip('0123456789')) if coordinate else column + 1
            if not 1 <= column <= 16384:
                raise ParseError('Columna Excel fuera de rango.')
            kind, style_id = attrs.get('t', 'n'), int(attrs.get('s', 0))
            value_parts, text_parts, formula_parts = [], [], []
            formula = False
            inline_present = False
        elif in_cell:
            if tag == NS + 'is':
                inline_present = True
            elif tag == PHONETIC:
                phonetic = True
            elif tag == VALUE:
                capture = value_parts
            elif tag == TEXT and not phonetic:
                capture = text_parts
            elif tag == FORMULA:
                formula = True
                capture = formula_parts

    def characters(value):
        if capture is not None:
            capture.append(value)

    def end(tag):
        nonlocal in_data, in_cell, capture, phonetic
        if tag in (VALUE, TEXT, FORMULA):
            capture = None
        elif tag == PHONETIC:
            phonetic = False
        elif tag == CELL and in_cell:
            datatype = kind
            raw = ''.join(value_parts)
            try:
                number_format = formats[style_id]
                if formula:
                    datatype, value = 'f', '=' + ''.join(formula_parts)
                elif kind == 'inlineStr':
                    datatype, value = 's', ''.join(text_parts) if inline_present else None
                elif not raw:
                    value = None
                elif kind == 's':
                    value = shared[int(raw)]
                elif kind == 'b':
                    value = bool(int(raw))
                elif kind == 'n':
                    value = float(raw) if any(c in raw for c in '.Ee') else int(raw)
                    if style_id in date_styles:
                        datatype = 'd'
                        value = from_excel(value, workbook.epoch, timedelta=style_id in duration_styles)
                elif kind == 'd':
                    value = from_ISO8601(raw)
                else:
                    value = raw
                    if kind == 'str':
                        datatype = 's'
            except (ValueError, OverflowError, IndexError):
                datatype, value, number_format = 'e', 'Valor o formato de celda Excel inv\u00e1lido', 'General'
            if len(cells) < column:
                cells.extend([EMPTY] * (column - len(cells)))
            cells[column - 1] = Cell(value, datatype, number_format)
            in_cell = False
        elif tag == ROW and in_data:
            pending.append((rownum, cells))
        elif tag == SHEET_DATA:
            in_data = False

    def forbidden(*args):
        raise ParseError('No se permiten DTD ni entidades externas en Excel.')

    parser = expat.ParserCreate(namespace_separator='}')
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    with worksheet._get_source() as source:
        try:
            while True:
                chunk = source.read(65536)
                parser.Parse(chunk, not chunk)
                while pending:
                    yield pending.popleft()
                if not chunk:
                    break
        except expat.ExpatError as exc:
            raise ParseError(str(exc)) from exc
