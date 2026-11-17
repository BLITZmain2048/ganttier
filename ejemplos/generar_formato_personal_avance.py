"""Ejemplos ficticios reproducibles; ejecutar desde app/backend con PYTHONPATH=."""
from pathlib import Path
from openpyxl import load_workbook
from ganttier.excel_io.personal_avance import build_template


def main():
    root = Path(__file__).resolve().parents[1]
    templates = root / 'plantillas' / 'formatos'
    examples = root / 'ejemplos' / 'formatos'
    templates.mkdir(parents=True, exist_ok=True)
    examples.mkdir(parents=True, exist_ok=True)
    for kind in ('personal', 'avance'):
        path = templates / f'{kind}.xlsx'
        build_template(path, kind)
        for size, count in (('pequeno', 4), ('grande', 5000)):
            wb = load_workbook(path)
            for i in range(count):
                if kind == 'personal':
                    wb['Personal'].append([f'Trabajador {i+1:05}', 'eléctrico', 'SSEE,protecciones', 'S/E Los Andes', '7x7',
                        '2027-01-04', None, 8, 10000, 15000, 2, 1])
                else:
                    wb['Avance'].append([f'SE-{i+1:05}', '2027-01-05', 50, 0, 0, None, '2027-01-04', None, 'Ejemplo ficticio S/E 220 kV'])
                    wb['Avance_Trabajador'].append([f'SE-{i+1:05}', '2027-01-05', f'Trabajador {i+1:05}', 8, 'Inspección'])
            if kind == 'personal':
                wb['Calendarios'].append(['S/E Los Andes', '2027-01-06', 'Feriado local ficticio'])
                wb['Ausencias'].append(['Trabajador 00001', '2027-01-05', '2027-01-05', 'capacitación', 4])
            wb.save(examples / f'{kind}_{size}.xlsx')
            wb.close()


if __name__ == '__main__':
    main()
