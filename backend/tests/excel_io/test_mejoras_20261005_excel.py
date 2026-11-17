from openpyxl import load_workbook
from ganttier.excel_io import parse_workbook
from ganttier.excel_io.templates import build_maintenance_template


def test_dh_template_roundtrip_and_assumptions(tmp_path):
    path = build_maintenance_template(tmp_path/'dh.xlsx')
    book = load_workbook(path)
    sheet = book['Plan_Maestro']
    col = next(c.column for c in sheet[1] if c.value == 'Frecuencia')
    sheet.cell(2, col, 'DH')
    instructions = ' '.join(str(c.value or '') for row in book['Instrucciones'] for c in row)
    assert 'HH totales' in instructions
    assert 'Supuestos' in instructions
    assert all(book['Instrucciones'].row_dimensions[row[0].row].height >= 50
               for row in book['Instrucciones'] if str(row[0].value).startswith('Supuestos'))
    book.save(path)
    book.close()
    bundle = parse_workbook(path)
    assert not bundle.has_errors
    assert bundle.periodic_tasks[0]['frequency'] == 'DH'
