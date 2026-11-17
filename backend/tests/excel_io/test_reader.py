from datetime import datetime
from zipfile import ZipFile
import re

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900

from ganttier.excel_io.reader import rows, load_read_only


@pytest.mark.parametrize('epoch', [CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900])
@pytest.mark.parametrize('iso_dates', [False, True])
def test_fast_reader_matches_openpyxl_types_styles_and_rich_text(tmp_path, epoch, iso_dates):
    path = tmp_path / 'types.xlsx'
    wb = Workbook(iso_dates=iso_dates)
    wb.epoch = epoch
    ws = wb.active
    ws.append(['Texto', 'Compartido', 'Fecha', 'Porcentaje', 'Booleano', 'Fórmula', 'Error'])
    ws.append(['texto rico', 'texto compartido', datetime(2026, 10, 1), .5, False, '=1+2', '#DIV/0!'])
    ws['D2'].number_format = '0.0%'
    ws['A10'] = 'Fila con hueco'
    wb.save(path)
    with ZipFile(path) as z:
        contents = {name: z.read(name) for name in z.namelist()}
    sheet = contents['xl/worksheets/sheet1.xml']
    sheet = re.sub(b'<c r="A2".*?</c>', b'<c r="A2" t="inlineStr"><is><r><t>texto </t></r><r><t>rico</t></r><rPh sb="0" eb="1"><t>ignorar</t></rPh></is></c>', sheet)
    sheet = re.sub(b'<c r="B2".*?</c>', b'<c r="B2" t="s"><v>0</v></c>', sheet)
    contents['xl/worksheets/sheet1.xml'] = sheet
    contents['xl/sharedStrings.xml'] = b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>texto compartido</t></si></sst>'
    contents['[Content_Types].xml'] = contents['[Content_Types].xml'].replace(b'</Types>', b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
    with ZipFile(path, 'w') as z:
        for name, data in contents.items():
            z.writestr(name, data)
    wb = load_workbook(path, read_only=True, data_only=False)
    standard = {i: [(c.value, c.data_type, c.number_format) for c in row] for i, row in enumerate(wb.active.iter_rows(), 1)}
    for rownum, cells in rows(wb.active):
        assert list(cells) == standard[rownum][:len(cells)]
    assert [rownum for rownum, _ in rows(wb.active)] == [1, 2, 10]
    wb.close()
    wb = load_read_only(path)
    for rownum, cells in rows(wb.active):
        assert list(cells) == standard[rownum][:len(cells)]
    wb.close()


def test_workbook_without_dimensions_does_not_scan_on_open(tmp_path, monkeypatch):
    from openpyxl.worksheet._reader import WorkSheetParser
    path = tmp_path / 'stream.xlsx'
    wb = Workbook(write_only=True)
    ws = wb.create_sheet('Actividades')
    ws.append(['Codigo', 'Nombre'])
    ws.append(['A', 'Actividad'])
    wb.save(path)
    def forbidden(*args, **kwargs):
        raise AssertionError('La apertura no debe recorrer toda la hoja para buscar dimensiones.')
    monkeypatch.setattr(WorkSheetParser, 'parse_dimensions', forbidden)
    wb = load_read_only(path)
    assert len(list(rows(wb.active))) == 2
    wb.close()


def test_sheet_dtd_is_rejected_without_entity_expansion(tmp_path):
    from ganttier.excel_io import build_project_template, parse_workbook
    path = build_project_template(tmp_path / 'entity.xlsx')
    with ZipFile(path) as z:
        content = {name: z.read(name) for name in z.namelist()}
    for name in list(content):
        if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
            content[name] = b'<!DOCTYPE worksheet [<!ENTITY external SYSTEM "file:///not-read">]>' + content[name]
    with ZipFile(path, 'w') as z:
        for name, data in content.items():
            z.writestr(name, data)
    bundle = parse_workbook(path)
    assert bundle.has_errors
    assert any('DTD' in i.message for i in bundle.issues)
