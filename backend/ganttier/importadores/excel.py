"""Lectura acotada de Excel. No ejecuta macros, fórmulas ni enlaces externos."""
from pathlib import Path
import re
import unicodedata
from zipfile import ZipFile, BadZipFile


def normal(value):
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').lower()) if not unicodedata.combining(c)))


def abrir(path, *, valores=True):
    import openpyxl
    path = Path(path)
    if path.suffix.lower() != '.xlsx':
        raise ValueError('Seleccione un archivo Excel .xlsx.')
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError('El archivo supera el límite de 30 MB.')
    try:
        with ZipFile(path) as archive:
            if sum(z.file_size for z in archive.infolist()) > 150 * 1024 * 1024:
                raise ValueError('El contenido descomprimido supera 150 MB.')
        book = openpyxl.load_workbook(path, data_only=valores, read_only=False, keep_links=False)
    except (BadZipFile, KeyError, OSError) as exc:
        raise ValueError('El archivo no es un libro Excel válido.') from exc
    if len(book.worksheets) > 100 or any(s.max_row > 20000 or s.max_column > 512 for s in book):
        book.close()
        raise ValueError('La planilla supera los límites: 100 hojas, 20.000 filas o 512 columnas.')
    return book


def columna(value):
    from openpyxl.utils import column_index_from_string
    try:
        index = column_index_from_string(str(value).strip().upper())
    except ValueError as exc:
        raise ValueError(f'Columna inválida: {value}. Use una letra Excel, por ejemplo D.') from exc
    if index > 512:
        raise ValueError('La columna excede el límite admitido.')
    return index


def valor(ws, row, col):
    return ws.cell(row, columna(col)).value if col else None


def origen(ws, row, cols):
    return {'hoja': ws.title, 'fila': row, 'celdas': {key: f'{col}{row}' for key, col in cols.items() if col}}


def vista(ws, first, last, columns):
    from openpyxl.utils import get_column_letter
    return [{'fila': r, 'celdas': {get_column_letter(c): str(ws.cell(r, c).value or '') for c in columns}}
            for r in range(max(1, first), min(ws.max_row, last) + 1)]


def aviso(code, message, row=None, *, nivel='advertencia', **extra):
    return dict(codigo=code, mensaje=message, fila=row, nivel=nivel, **extra)
