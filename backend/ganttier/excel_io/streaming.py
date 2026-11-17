"""Adaptador write_only para valores planos, sin objetos XML/celdas por dato.

openpyxl conserva el libro, estilos, encabezados, tablas, validaciones y dibujos.
Sólo sheetData usa serialización por fila: et_xmlfile sin lxml domina el tiempo
en libros de millones de celdas. Integración privada concentrada aquí (3.1.x).
"""
from contextlib import contextmanager
from datetime import date, datetime
from functools import lru_cache
from io import BufferedWriter
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape, quoteattr
import gzip
import re
import shutil

from openpyxl.cell.cell import Cell
from openpyxl import Workbook
from openpyxl.writer.excel import ExcelWriter
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import to_excel
from openpyxl.worksheet._write_only import WriteOnlyWorksheet
from openpyxl.worksheet._writer import WorksheetWriter, ALL_TEMP_FILES
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.xml.constants import SHEET_MAIN_NS
from openpyxl.xml.functions import tostring

INVALID_XML = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


class _WorkbookArchive(ZipFile):
    """Descomprime XML temporal hacia el ZIP final con memoria acotada."""
    def write(self, filename, arcname=None, compress_type=None, compresslevel=None):
        if filename in self.compressed_xml:
            with gzip.open(filename, 'rb') as source, self.open(arcname, 'w', force_zip64=True) as target:
                shutil.copyfileobj(source, target, length=262144)
        else:
            super().write(filename, arcname, compress_type, compresslevel)


class StreamingWorkbook(Workbook):
    def save(self, filename):
        # Nivel 1 conserva el formato ZIP/XLSX; cambia sólo tamaño/CPU.
        for sheet in self:
            if not sheet.closed:
                sheet.close()
        with _WorkbookArchive(filename, 'w', ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            archive.compressed_xml = {sheet._writer.out for sheet in self
                                      if isinstance(getattr(sheet, '_writer', None), _RowWriter)}
            ExcelWriter(self, archive).save()


class _XMLStream:
    def __init__(self, output):
        self.output = output

    def write(self, element):
        self.output.write(escape(element).encode('utf-8') if isinstance(element, str) else tostring(element))

    @contextmanager
    def element(self, name, attrs=None, **kwargs):
        attributes = dict(attrs or {}, **kwargs)
        text = ''.join(f' {key}={quoteattr(str(value))}' for key, value in attributes.items())
        self.output.write(f'<{name}{text}>'.encode('utf-8'))
        try:
            yield
        finally:
            self.output.write(f'</{name}>'.encode('utf-8'))


class _RowWriter(WorksheetWriter):
    def get_stream(self):
        # El XML de 100k actividades ocupa cientos de MB. Comprimir también el
        # spool evita ese tráfico de disco y su costosa eliminación en Windows.
        with open(self.out, 'wb', buffering=262144) as raw, BufferedWriter(gzip.GzipFile(
                fileobj=raw, mode='wb', compresslevel=1, mtime=0), buffer_size=262144) as output:
            stream = _XMLStream(output)
            with stream.element('worksheet', xmlns=SHEET_MAIN_NS):
                try:
                    while True:
                        element = yield
                        if element is True:
                            yield stream
                        elif element is not None:
                            stream.write(element)
                except GeneratorExit:
                    pass


def _writer(sheet, writer_class):
    # Cada exportación mantiene sus temporales junto al destino, sin cambiar
    # tempfile.tempdir global (otras exportaciones pueden correr a la vez).
    suffix = '.xml.gz' if writer_class is _RowWriter else '.xml'
    with NamedTemporaryFile(dir=sheet.parent._ganttier_temp_dir,
                            prefix='.ganttier-', suffix=suffix, delete=False) as handle:
        output = handle.name
    ALL_TEMP_FILES.append(output)
    return writer_class(sheet, out=output)


class _BufferedSheet(Worksheet):
    """Hojas pequeñas normales dentro del libro secuencial."""
    closed = False
    _writer = None

    def close(self):
        if not self.closed:
            self._writer = _writer(self, WorksheetWriter)
            self._writer.write()
            self.closed = True


class _RawSheet(WriteOnlyWorksheet):
    def __init__(self, workbook, title):
        super().__init__(workbook, title)
        self.value_styles = {}
        self.text_styles = {}
        self.column_styles = {}
        self.date_styles = set()

    def _get_writer(self):
        if self._writer is None:
            self._writer = _writer(self, _RowWriter)
            self._writer.write_top()

    def _write_rows(self):
        stream = self._writer.xf.send(True)
        epoch = self.parent.epoch
        value_styles = self.value_styles
        text_styles = self.text_styles
        column_styles = self.column_styles
        date_styles = self.date_styles
        columns = {}

        @lru_cache(maxsize=4096, typed=True)
        def value_xml(value, style):
            if style in date_styles and isinstance(value, str) and not value.startswith('\\'):
                try:
                    value = date.fromisoformat(value)
                except ValueError:
                    pass  # El importador conserva el diagnóstico de fechas inválidas.
            kind = type(value)
            style = column_styles.get(style, value_styles.get(kind, style))
            attr = f' s="{style}"' if style else ''
            if isinstance(value, str):
                if value in text_styles:
                    attr = f' s="{text_styles[value]}"'
                if len(value) > 32767:
                    raise ValueError('El texto supera los 32.767 caracteres de Excel.')
                if INVALID_XML.search(value):
                    raise ValueError('El texto contiene caracteres no permitidos en XML de Excel.')
                # Siempre literal, incluso =... y #DIV/0!; CR debe sobrevivir
                # la normalización de saltos de línea del lector XML.
                text = escape(value).replace('\r', '&#13;')
                space = ' xml:space="preserve"' if value != value.strip() else ''
                return f'<c{attr} t="inlineStr"><is><t{space}>{text}</t></is></c>'
            if isinstance(value, bool):
                return f'<c{attr} t="b"><v>{int(value)}</v></c>'
            if isinstance(value, (datetime, date)):
                if getattr(value, 'tzinfo', None) is not None:
                    raise TypeError('Excel no admite fechas con zona horaria.')
                value = to_excel(value, epoch)
            elif not isinstance(value, (int, float)):
                raise ValueError(f'Tipo no admitido en Excel: {kind.__name__}')
            text = format(value, '.16g') if isfinite(value) else ''
            return f'<c{attr}><v>{text}</v></c>'

        with stream.element('sheetData'):
            rownum = 0
            try:
                while True:
                    row = yield
                    rownum += 1
                    if rownum > 1048576:
                        raise ValueError('La hoja supera las 1.048.576 filas de Excel.')
                    if rownum == 1 and any(isinstance(v, Cell) for v in row):
                        self._writer.write_row(stream, self._values_to_row(row, rownum), rownum)
                        continue
                    if len(row) > 16384:
                        raise ValueError('La hoja supera las 16.384 columnas de Excel.')
                    if len(row) not in columns:
                        columns[len(row)] = [self.column_dimensions[get_column_letter(i)].style_id
                                             for i in range(1, len(row) + 1)]
                    dimension = self.row_dimensions.pop(rownum, None)
                    attrs = ''.join(f' {k}={quoteattr(str(v))}' for k, v in dict(dimension or {}).items())
                    parts = [f'<row r="{rownum}"{attrs}>']
                    # OOXML permite omitir r en celdas consecutivas. Emitir los
                    # huecos explícitos conserva coordenadas sin repetirlas ni
                    # reconstruir millones de fragmentos que son idénticos.
                    parts.extend(value_xml(value, style) if value is not None else '<c/>'
                                 for style, value in zip(columns[len(row)], row))
                    parts.append('</row>')
                    stream.output.write(''.join(parts).encode('utf-8'))
            except GeneratorExit:
                pass
            finally:
                value_xml.cache_clear()
        self._writer.xf.send(None)


def create_sheet(workbook, title, index=None, *, buffered=False):
    sheet = (_BufferedSheet if buffered else _RawSheet)(workbook, title)
    workbook._add_sheet(sheet, index)
    return sheet


def close_workbook(workbook):
    """Libera XML incluso si falla la construcción, antes de workbook.save."""
    for sheet in workbook:
        writer = getattr(sheet, '_writer', None)
        if writer is not None:
            rows = getattr(sheet, '_rows', None)
            if rows is not None:
                rows.close()
            writer.close()
            if Path(writer.out).exists():
                writer.cleanup()
    workbook.close()
