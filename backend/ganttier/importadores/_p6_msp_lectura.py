"""Lectura literal, secuencial y acotada de las tablas externas MSP/P6."""
from __future__ import annotations

import csv
import io
import re
from collections import deque
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from xml.parsers import expat
from zipfile import BadZipFile, ZipFile

from openpyxl.styles.numbers import BUILTIN_FORMATS
from openpyxl.utils.cell import column_index_from_string
from openpyxl.utils.datetime import from_excel, from_ISO8601, WINDOWS_EPOCH
from ganttier.excel_io.reader import Cell, EMPTY, load_read_only

MAX_ROWS, MAX_COLS = 200_000, 200
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def issue(errors, sheet, row, column, message, code='celda_invalida', level='error'):
    """Diagnóstico con coordenadas físicas, sin depender de HTTP ni SQLite."""
    errors.append(dict(codigo=code, mensaje=message, nivel=level,
                       hoja=sheet, fila=row, columna=column))


def _rows(ws, errors):
    """Una pasada XML; lee el resultado guardado de fórmulas, nunca las evalúa."""
    wb = ws.parent
    formats = [BUILTIN_FORMATS.get(s.numFmtId, 'General') if s.numFmtId < 164
               else wb._number_formats[s.numFmtId - 164] for s in wb._cell_styles]
    pending = deque()
    st = dict(data=False, cell=False, row=0, col=0, style=0, kind='n',
              formula=False, raw=[], text=[], capture=None, cells=[], phonetic=False)

    def start(tag, attrs):
        tag = tag.removeprefix(NS)
        if tag == 'sheetData':
            st['data'] = True
        elif tag == 'row' and st['data']:
            st.update(row=int(attrs.get('r', st['row'] + 1)), cells=[], col=0)
            if st['row'] > MAX_ROWS + 1000:
                raise ValueError('La hoja supera 200.000 filas de datos.')
        elif tag == 'c' and st['data']:
            coordinate = attrs.get('r')
            col = column_index_from_string(coordinate.rstrip('0123456789')) if coordinate else st['col'] + 1
            if not 1 <= col <= MAX_COLS:
                raise ValueError('La hoja supera 200 columnas.')
            st.update(cell=True, col=col, style=int(attrs.get('s', 0)), kind=attrs.get('t', 'n'),
                      formula=False, raw=[], text=[], capture=None)
        elif st['cell']:
            if tag == 'f': st['formula'] = True
            elif tag == 'v': st['capture'] = st['raw']
            elif tag == 'rPh': st['phonetic'] = True
            elif tag == 't' and not st['phonetic']: st['capture'] = st['text']
        elif tag == 'mergeCell':
            ref = attrs.get('ref', '')
            m = re.match(r'([A-Z]+)(\d+)', ref)
            if m:
                issue(errors, ws.title, int(m[2]), m[1],
                      f'Celdas combinadas {ref}: descombine la tabla.', 'celdas_combinadas')

    def chars(text):
        if st['capture'] is not None: st['capture'].append(text)

    def end(tag):
        tag = tag.removeprefix(NS)
        if tag in {'v', 't', 'f'}: st['capture'] = None
        elif tag == 'rPh': st['phonetic'] = False
        elif tag == 'c' and st['cell']:
            raw, kind, style = ''.join(st['raw']), st['kind'], st['style']
            try:
                fmt = formats[style]
                if st['formula'] and not raw:
                    value, kind = 'Fórmula sin resultado guardado; recalcule y pegue valores.', 'e'
                elif kind == 'inlineStr': value, kind = ''.join(st['text']), 's'
                elif not raw: value = None
                elif kind == 's': value = ws._shared_strings[int(raw)]
                elif kind == 'b': value = bool(int(raw))
                elif kind == 'n':
                    value = float(raw) if any(c in raw for c in '.Ee') else int(raw)
                    if style in wb._date_formats: value, kind = from_excel(value, wb.epoch), 'd'
                elif kind == 'd': value = from_ISO8601(raw)
                else: value = raw
            except (ValueError, IndexError, OverflowError):
                value, kind, fmt = 'Valor o formato de Excel inválido.', 'e', 'General'
            cells, col = st['cells'], st['col']
            cells.extend([EMPTY] * max(0, col - len(cells)))
            cells[col - 1] = Cell(value, kind, fmt)
            st['cell'] = False
        elif tag == 'row' and st['data']: pending.append((st['row'], st['cells']))
        elif tag == 'sheetData': st['data'] = False

    def forbidden(*args):
        raise ValueError('No se admiten DTD ni entidades externas en Excel.')

    parser = expat.ParserCreate(namespace_separator='}')
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = chars
    parser.StartDoctypeDeclHandler = parser.ExternalEntityRefHandler = forbidden
    with ws._get_source() as source:
        while True:
            chunk = source.read(65536)
            parser.Parse(chunk, not chunk)
            while pending: yield pending.popleft()
            if not chunk: break


class Tablas:
    def __init__(self, path, errors):
        self.path, self.errors, self.book = Path(path), errors, None
        self.epoch = WINDOWS_EPOCH
        if self.path.stat().st_size > 30 * 1024 * 1024: raise ValueError('El archivo supera 30 MB.')
        if self.path.suffix.lower() == '.csv':
            raw = self.path.read_bytes()
            for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
                try:
                    self.text = raw.decode(encoding)
                    break
                except UnicodeDecodeError: pass
            try: self.delimiter = csv.Sniffer().sniff(self.text[:65536], delimiters=';,\t').delimiter
            except csv.Error:
                self.delimiter = max(';,\t', key=lambda c: self.text.splitlines()[0].count(c)) if self.text else ';'
            self.names = ['CSV']
        elif self.path.suffix.lower() == '.xlsx':
            try:
                with ZipFile(self.path) as archive:
                    infos = archive.infolist()
                    if len(infos) > 5000 or sum(z.file_size for z in infos) > 150 * 1024 * 1024:
                        raise ValueError('El contenido ZIP supera los límites de lectura.')
                    for z in infos:
                        p = PurePosixPath(z.filename.replace('\\', '/'))
                        if p.is_absolute() or '..' in p.parts or z.flag_bits & 1:
                            raise ValueError('Entrada ZIP insegura.')
                        if z.file_size > 1024 * 1024 and z.file_size > max(1, z.compress_size) * 1000:
                            raise ValueError('Compresión ZIP excesiva.')
                self.book = load_read_only(self.path)
                if len(self.book.sheetnames) > 100:
                    self.book.close()
                    raise ValueError('El libro supera 100 hojas.')
                self.names, self.epoch = self.book.sheetnames, self.book.epoch
            except (BadZipFile, KeyError, OSError, expat.ExpatError) as exc:
                raise ValueError('El archivo no es un libro Excel válido.') from exc
        else: raise ValueError('Seleccione .xlsx o .csv; .xer no está admitido.')

    def rows(self, name):
        if name not in self.names: raise ValueError(f'No existe la hoja {name}.')
        if self.book is not None:
            yield from _rows(self.book[name], self.errors)
        else:
            reader = csv.reader(io.StringIO(self.text, newline=''), delimiter=self.delimiter, strict=True)
            try:
                for i, row in enumerate(reader, 1):
                    if i > MAX_ROWS + 1000 or len(row) > MAX_COLS:
                        raise ValueError('CSV: límite de 200.000 filas y 200 columnas.')
                    yield i, [Cell(v, 's', 'General') for v in row]
            except csv.Error as exc:
                raise ValueError(f'CSV inválido cerca de la línea {reader.line_num}: {exc}') from exc


@contextmanager
def abrir_tablas(path, errors):
    tables = Tablas(path, errors)
    try: yield tables
    finally:
        if tables.book is not None: tables.book.close()
