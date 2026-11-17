"""Compatibilidad del escritor plano con Excel y con el lector público."""
from datetime import date, datetime
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900

from ganttier.excel_io import export_project, parse_workbook
from ganttier.excel_io.schema import SCHEMAS
from ganttier.excel_io.streaming import StreamingWorkbook, create_sheet, close_workbook
from ganttier.excel_io.templates import example_bundle


@pytest.mark.parametrize('epoch', [CALENDAR_WINDOWS_1900, CALENDAR_MAC_1904])
def test_raw_values_match_public_openpyxl(tmp_path, epoch):
    values = ['  Ñ & <paño> Ω 😀  ', '=1+1', '#DIV/0!', None, True, False,
              1234, -1234.567, .125, date(2026, 10, 9), datetime(2026, 10, 9, 18, 30)]
    formats = ['General'] * 8 + ['0.0%', 'dd-mm-yyyy', 'dd-mm-yyyy hh:mm']
    expected = Workbook()
    expected.epoch = epoch
    plain = expected.active
    actual = StreamingWorkbook(write_only=True)
    actual.epoch = epoch
    actual._ganttier_temp_dir = tmp_path
    streamed = create_sheet(actual, 'Prueba')
    for i, (value, fmt) in enumerate(zip(values, formats), 1):
        cell = plain.cell(1, i, value)
        cell.number_format = fmt
        if isinstance(value, str):
            cell.data_type = 's'
        streamed.column_dimensions[cell.column_letter].number_format = fmt
    streamed.append(values)
    actual.save(tmp_path / 'actual.xlsx')
    expected.save(tmp_path / 'expected.xlsx')
    close_workbook(actual)
    expected.close()
    for read_only in (False, True):
        a = load_workbook(tmp_path / 'actual.xlsx', read_only=read_only)
        e = load_workbook(tmp_path / 'expected.xlsx', read_only=read_only)
        # EmptyCell de read_only representa la ausencia de formato como None.
        extract = lambda wb: [(c.value, c.data_type, c.number_format or 'General') for c in next(wb.active.rows)]
        assert extract(a) == extract(e)
        a.close()
        e.close()


@pytest.mark.parametrize('mode', ['proyecto', 'mantenimiento'])
def test_export_preserves_layout_and_full_column_validations(tmp_path, mode):
    bundle = example_bundle(mode)
    path = export_project(bundle, tmp_path / 'layout.xlsx')
    assert parse_workbook(path) == bundle
    wb = load_workbook(path)
    assert wb.sheetnames[:4] == ['Instrucciones', 'Proyecto', '_Ganttier', 'Listas']
    assert wb['_Ganttier'].sheet_state == 'veryHidden'
    assert wb['Listas'].sheet_state == 'hidden'
    for name in ('Calendarios', 'Recursos', 'Avance'):
        sheet = wb[name]
        expected = [c.title for c in SCHEMAS[name]]
        assert [c.value for c in sheet[1]][:len(expected)] == expected
        assert sheet.freeze_panes == 'C2'
        assert sheet['A1'].font.bold and sheet['A1'].comment
        assert sheet.column_dimensions['A'].width >= 16
        table = next(iter(sheet.tables.values()))
        assert table.autoFilter.ref == table.ref
        assert sheet.auto_filter.ref is None  # sólo la tabla declara el filtro: si no, Excel repara el libro (D01)
        for validation in sheet.data_validations.dataValidation:
            ranges = list(validation.sqref.ranges)
            assert len(ranges) == 1
            assert ranges[0].min_row == 2 and ranges[0].max_row == 1048576
            assert ranges[0].min_col == ranges[0].max_col
    assert wb['Avance']['B2'].number_format == 'dd-mm-yyyy'
    assert wb['Avance']['C2'].number_format == '0.0" %"'
    assert wb['Avance']['D2'].number_format == '#,##0.00'
    assert any(v.formula1 == '=Nombres_Recursos' for v in wb['Avance'].data_validations.dataValidation)
    wb.close()
    with ZipFile(path) as archive:
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        styles = ET.fromstring(archive.read('xl/styles.xml'))
        assert len(styles.find('s:cellXfs', ns)) < 50


def test_raw_export_preserves_literal_special_text_and_sparse_fields(tmp_path):
    bundle = example_bundle('proyecto')
    for activity, value in zip(bundle.activities, [' =1+1\r\nΩ\t ', '#DIV/0!', '\\N']):
        activity['notes'] = value
    bundle.activities[0]['planned_start'] = None
    bundle.activities[1]['notes'] = ''
    assert parse_workbook(export_project(bundle, tmp_path / 'literal.xlsx')) == bundle


def test_construction_failure_cleans_xml_and_preserves_target(tmp_path):
    from openpyxl.worksheet._writer import ALL_TEMP_FILES
    bundle = example_bundle('proyecto')
    bundle.activities[-1]['notes'] = 'x' * 32768
    path = tmp_path / 'keep.xlsx'
    path.write_bytes(b'original')
    previous = set(ALL_TEMP_FILES)
    with pytest.raises(ValueError, match='32.767'):
        export_project(bundle, path)
    assert set(ALL_TEMP_FILES) == previous
    assert path.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [path]


def test_zip_failure_cleans_spools_and_preserves_target(tmp_path, monkeypatch):
    from ganttier.excel_io import streaming
    from openpyxl.worksheet._writer import ALL_TEMP_FILES
    original = streaming._WorkbookArchive.write
    def fail(archive, filename, *args, **kwargs):
        original(archive, filename, *args, **kwargs)
        raise OSError('disco lleno')
    monkeypatch.setattr(streaming._WorkbookArchive, 'write', fail)
    previous = set(ALL_TEMP_FILES)
    path = tmp_path / 'keep.xlsx'
    path.write_bytes(b'original')
    with pytest.raises(OSError, match='guardar'):
        export_project(example_bundle('proyecto'), path)
    assert path.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [path]
    assert set(ALL_TEMP_FILES) == previous


def test_empty_streamed_sheet_remains_readable(tmp_path):
    wb = StreamingWorkbook(write_only=True)
    wb._ganttier_temp_dir = tmp_path
    create_sheet(wb, 'Vacía')
    path = tmp_path / 'empty.xlsx'
    try:
        wb.save(path)
    finally:
        close_workbook(wb)
    actual = load_workbook(path)
    assert actual.active['A1'].value is None
    actual.close()


def test_repository_iso_dates_are_native_excel_dates(tmp_path):
    bundle = example_bundle('proyecto')
    bundle.project['start_date'] = '2026-10-01'
    bundle.activities[0]['planned_start'] = '2026-10-02'
    path = export_project(bundle, tmp_path / 'iso.xlsx')
    wb = load_workbook(path)
    project_row = next(row for row in wb['Proyecto'] if row[0].value == 'Fecha_Inicio')
    assert project_row[1].value == datetime(2026, 10, 1)
    col = next(cell.column for cell in wb['Actividades'][1] if cell.value == 'planned_start')
    assert wb['Actividades'].cell(2, col).value == datetime(2026, 10, 2)
    assert wb['Actividades'].cell(2, col).number_format == 'dd-mm-yyyy'
    wb.close()


def test_empty_table_remains_readable(tmp_path):
    bundle = example_bundle('proyecto')
    bundle.activities = bundle.progress = bundle.assignments = bundle.relations = []
    path = export_project(bundle, tmp_path / 'empty-table.xlsx')
    assert parse_workbook(path) == bundle
    actual = load_workbook(path)
    assert actual['Actividades'].tables['Tabla_Actividades'].ref == 'A1:X2'
    assert [c.value for c in actual['Actividades'][1]][-2:] == ['Duracion_Horas', 'Checklist']
    actual.close()
