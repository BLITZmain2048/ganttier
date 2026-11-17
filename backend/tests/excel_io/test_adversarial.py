from datetime import date

import pytest
from openpyxl import Workbook, load_workbook

from ganttier.domain import ImportBundle
from ganttier.excel_io import export_project, parse_workbook, build_project_template, build_maintenance_template


def book(tmp_path, sheet, headers, rows):
    path = tmp_path / 'input.xlsx'
    wb = Workbook()
    p = wb.active
    p.title = 'Proyecto'
    for row in [('Nombre', 'Pruebas'), ('Modo', 'proyecto'), ('Fecha_Inicio', '01-10-2026')]:
        p.append(row)
    ws = wb.create_sheet(sheet)
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)
    wb.close()
    return path


@pytest.mark.parametrize('value,expected', [(0, False), (1, True), ('Sí', True), ('NO', False)])
def test_boolean_values(tmp_path, value, expected):
    path = book(tmp_path, 'Actividades', ['Codigo', 'Nombre', 'Ventana_Dura'], [('A', 'Actividad', value)])
    result = parse_workbook(path)
    assert not result.issues
    assert result.activities[0]['window_hard'] is expected


@pytest.mark.parametrize('value', [-1, '-1', 's.i', 'n/o'])
def test_boolean_not_normalized_into_another_value(tmp_path, value):
    result = parse_workbook(book(tmp_path, 'Actividades', ['Codigo', 'Nombre', 'Ventana_Dura'], [('A', 'Actividad', value)]))
    assert any(i.row == 2 and i.field == 'Ventana_Dura' for i in result.issues)


def test_cycle_reports_actual_edge_not_downstream(tmp_path):
    path = book(tmp_path, 'Actividades', ['Codigo', 'Nombre'], [(c, c) for c in 'ABCD'])
    wb = load_workbook(path)
    ws = wb.create_sheet('Relaciones')
    for row in [('Predecesora', 'Sucesora'), ('A', 'B'), ('B', 'C'), ('C', 'A'), ('C', 'D')]:
        ws.append(row)
    wb.save(path)
    result = parse_workbook(path)
    cycles = [i for i in result.issues if 'ciclo' in i.message.lower()]
    assert cycles and all(i.sheet == 'Relaciones' and i.row in (2, 3, 4) and i.field == 'Sucesora' for i in cycles)


def test_formula_and_excel_error_are_never_silently_defaulted(tmp_path):
    result = parse_workbook(book(tmp_path, 'Actividades', ['Codigo', 'Nombre', 'HH'], [('A', 'A', '=1+2'), ('B', 'B', '#DIV/0!')]))
    assert {(i.row, i.field) for i in result.issues} == {(2, 'HH'), (3, 'HH')}
    assert all('fórmula' in i.message or 'Excel' in i.message for i in result.issues)


def test_exact_sparse_roundtrip_nulls_text_and_skills(tmp_path):
    source = ImportBundle('proyecto', {'name': ' Prueba & <S/E> ', 'start_date': date(2026, 1, 1)},
        calendars=[{'name': 'Sin feriados', 'holidays_country': None, 'exceptions': []}],
        resources=[{'name': 'R', 'skills': ['protecciones,control', 'eléctrico'], 'email': ''}],
        activities=[{'code': 'A', 'name': 'A', 'notes': '  =texto\n  ', 'planned_start': None},
                    {'code': 'B', 'name': 'B', 'notes': r'\N'}])
    actual = parse_workbook(export_project(source, tmp_path / 'export.xlsx'))
    assert not actual.issues
    assert actual == source


def test_project_date_error_points_to_real_row(tmp_path):
    path = book(tmp_path, 'Actividades', ['Codigo', 'Nombre'], [])
    wb = load_workbook(path)
    wb['Proyecto'].append(('Fecha_Fin', '01-01-2025'))
    wb.save(path)
    result = parse_workbook(path)
    assert any(i.sheet == 'Proyecto' and i.row == 4 and i.field == 'Fecha_Fin' for i in result.issues)


def test_formatted_empty_rows_do_not_create_defaults(tmp_path):
    path = build_project_template(tmp_path / 'formatted.xlsx')
    wb = load_workbook(path)
    wb['Actividades']['A100000'].number_format = '@'
    wb.save(path)
    result = parse_workbook(path)
    assert not result.issues
    assert len(result.activities) == 3


def test_maintenance_examples_and_self_contained_validations(tmp_path):
    path = build_maintenance_template(tmp_path / 'maintenance.xlsx')
    data = parse_workbook(path)
    assert not data.issues
    assert {66, 110, 154, 220} <= {r['voltage_kv'] for r in data.periodic_tasks}
    text = ' '.join(r['name'].lower() for r in data.periodic_tasks)
    assert all(word in text for word in ['termografía', 'aceite', 'relés', 'lavado', 'interruptor', 'transformador de poder'])
    wb = load_workbook(path)
    assert any(d.formula1 == '=Nombres_Calendarios' for d in wb['Proyecto'].data_validations.dataValidation)
    assert wb.defined_names['Nombres_Calendarios'].attr_text == 'Tabla_Calendarios[Nombre]'
    wb.close()
    project = parse_workbook(build_project_template(tmp_path / 'project.xlsx'))
    line = next(r for r in project.activities if r['wbs_code'] == 'LT')
    wbs = next(r for r in project.wbs if r['code'] == 'LT')
    assert f"{line['voltage_kv']} kV" in wbs['name']


def test_export_rejects_excel_text_truncation(tmp_path):
    source = ImportBundle('proyecto', {'name': 'P', 'start_date': date(2026, 1, 1)},
                          activities=[{'code': 'A', 'name': 'A', 'notes': 'x' * 32768}])
    with pytest.raises(ValueError, match='32.767'):
        export_project(source, tmp_path / 'long.xlsx')


def test_incorrect_worksheet_dimensions_do_not_drop_rows(tmp_path):
    from zipfile import ZipFile
    import re
    path = book(tmp_path, 'Actividades', ['Codigo', 'Nombre', 'HH'], [('A', 'Actividad', 8)])
    with ZipFile(path) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    content['xl/worksheets/sheet2.xml'] = re.sub(b'<dimension ref="[^"]+"', b'<dimension ref="A1:A1"', content['xl/worksheets/sheet2.xml'])
    with ZipFile(path, 'w') as archive:
        for name, data in content.items():
            archive.writestr(name, data)
    result = parse_workbook(path)
    assert not result.issues and len(result.activities) == 1


@pytest.mark.parametrize('value,field,expected', [('50,5 %', 'percent_complete', 50.5), ('1.234,56', 'work_hours', 1234.56), ('1,234.56', 'work_hours', 1234.56), ('2026/10/01', 'planned_start', date(2026, 10, 1)), ('01.10.2026', 'planned_start', date(2026, 10, 1))])
def test_additional_local_formats(tmp_path, value, field, expected):
    result = parse_workbook(book(tmp_path, 'Actividades', ['Codigo', 'Nombre', field], [('A', 'A', value)]))
    assert not result.issues
    assert result.activities[0][field] == expected


@pytest.mark.parametrize('value', ['12.34,56', '1,23.45'])
def test_malformed_grouping_is_not_silently_reinterpreted(tmp_path, value):
    result = parse_workbook(book(tmp_path, 'Actividades', ['Codigo', 'Nombre', 'HH'], [('A', 'A', value)]))
    assert any(i.row == 2 and i.field == 'HH' for i in result.issues)


def test_deep_cycle_does_not_depend_on_python_recursion_limit(tmp_path):
    path = book(tmp_path, 'Actividades', ['Codigo', 'Nombre'], [(f'A{i}', 'A') for i in range(2000)])
    wb = load_workbook(path)
    ws = wb.create_sheet('Relaciones')
    ws.append(['Predecesora', 'Sucesora'])
    for i in range(2000):
        ws.append([f'A{i}', f'A{(i + 1) % 2000}'])
    wb.save(path)
    wb.close()
    result = parse_workbook(path)
    assert any(i.row == 2001 and 'ciclo' in i.message for i in result.issues)


def test_export_failure_preserves_original_file(tmp_path, monkeypatch):
    from ganttier.excel_io import templates
    class BrokenWorkbook:
        def save(self, path):
            path.write_bytes(b'parcial')
            raise OSError('disco lleno')
        def close(self):
            pass
    monkeypatch.setattr(templates, 'make_workbook', lambda *args, **kwargs: BrokenWorkbook())
    path = tmp_path / 'existing.xlsx'
    path.write_bytes(b'original')
    with pytest.raises(OSError, match='guardar'):
        export_project(ImportBundle('proyecto', {}), path)
    assert path.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [path]
