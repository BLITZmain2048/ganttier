"""Comprobaciones estructurales OOXML que Excel exige y openpyxl no valida (D01, 08-10-2026).

Excel «repara» el libro, quitando tablas o gráficos, cuando:
- una hoja con tabla declara además un ``<autoFilter>`` propio (o ``_xlnm._FilterDatabase``);
- un eje de gráfico apunta con ``crossAx`` a un eje inexistente;
- un encabezado de tabla no coincide con el nombre de su columna.
La verificación no sustituye abrir el archivo en Excel, pero detecta estas causas sin necesitarlo.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/package/2006/relationships',
      'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart'}
AXES = ('catAx', 'valAx', 'dateAx', 'serAx')


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _rels(archive: ZipFile, part: str) -> list[tuple[str, str]]:
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    if name not in archive.namelist():
        return []
    base = posixpath.dirname(part)
    out = []
    for rel in ET.fromstring(archive.read(name)):
        target = rel.get('Target', '')
        path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(base, target))
        out.append((rel.get('Type', '').rsplit('/', 1)[-1], path))
    return out


def chart_problems(xml: bytes, name: str = 'chart') -> list[str]:
    root = ET.fromstring(xml)
    plot = next((e for e in root.iter() if _local(e.tag) == 'plotArea'), None)
    if plot is None:
        return [f'{name}: sin plotArea']
    axes, crosses, used = {}, {}, set()
    for element in plot:
        kind = _local(element.tag)
        if kind in AXES:
            ident = next((c.get('val') for c in element if _local(c.tag) == 'axId'), None)
            cross = next((c.get('val') for c in element if _local(c.tag) == 'crossAx'), None)
            axes[ident] = kind
            crosses[ident] = cross
        elif kind.endswith('Chart'):
            used.update(c.get('val') for c in element if _local(c.tag) == 'axId')
    problems = []
    for ident in sorted(used - set(axes)):
        problems.append(f'{name}: la serie usa el eje {ident}, que no existe')
    for ident, cross in crosses.items():
        if cross not in axes:
            problems.append(f'{name}: el eje {ident} cruza el eje {cross}, que no existe')
        elif crosses.get(cross) != ident:
            problems.append(f'{name}: los ejes {ident} y {cross} no se cruzan entre sí')
    return problems


def structure_problems(path: str | Path) -> list[str]:
    """Lista de causas conocidas de «reparar archivo» en Excel; vacía si no se detecta ninguna."""
    problems: list[str] = []
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheets = {s.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'): s.get('name')
                  for s in workbook.find('m:sheets', NS)}
        book_rels = dict(((rid, target) for rid, target in
                          ((r.get('Id'), r.get('Target')) for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels')))))
        filters = {}
        names_el = workbook.find('m:definedNames', NS)
        if names_el is not None:
            for defined in names_el:
                if defined.get('name') == '_xlnm._FilterDatabase':
                    filters[defined.get('localSheetId')] = (defined.text or '')
        for index, (rid, title) in enumerate(sheets.items()):
            target = book_rels[rid]
            part = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            relations = _rels(archive, part)
            tables = [path_ for kind, path_ in relations if kind == 'table']
            if not tables:
                continue
            with archive.open(part) as stream:
                head = stream.read().decode('utf8', 'replace')
            tail = head[head.rfind('</sheetData>'):] if '</sheetData>' in head else head
            if re.search(r'<autoFilter[\s>/]', tail):
                problems.append(f'{title}: la hoja declara autoFilter y además tiene tabla')
            if str(index) in filters:
                problems.append(f'{title}: _xlnm._FilterDatabase junto a una tabla')
        for name in sorted(names):
            if name.startswith('xl/charts/chart') and name.endswith('.xml'):
                problems.extend(chart_problems(archive.read(name), name))
            if name.startswith('xl/tables/table') and name.endswith('.xml'):
                table = ET.fromstring(archive.read(name))
                columns = [c.get('name') for c in table.iter() if _local(c.tag) == 'tableColumn']
                if len(set(columns)) != len(columns):
                    problems.append(f'{name}: nombres de columna duplicados')
    return problems
