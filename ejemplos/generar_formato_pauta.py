"""Ejemplos ficticios reproducibles: python -B ejemplos/generar_formato_pauta.py."""
from pathlib import Path
from datetime import datetime
import openpyxl
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
HEADERS = ['Instalación', 'TAG', 'Tipo de equipo', 'Tensión', 'Tarea', 'Frecuencia', 'HH', 'Cuadrilla', 'Habilidad', 'Requiere desconexión', 'Duración (h/d)', 'Última ejecución', 'Prioridad', 'Notas']


def sheet(book, name, headers):
    ws = book.create_sheet(name); ws.append(headers); ws.freeze_panes = 'A2'
    for c in ws[1]:
        c.comment = Comment(f'{c.value}. Valores literales. HH totales por ejecución; duración con sufijo h o d. Fechas DD/MM/AAAA. Vacíos opcionales se declaran SUPUESTO.', 'Ganttier')
        ws.column_dimensions[c.column_letter].width = 24
    if 'Frecuencia' in headers:
        col = openpyxl.utils.get_column_letter(headers.index('Frecuencia')+1)
        dv = DataValidation(type='list', formula1='"Semanal,Mensual,Trimestral,Semestral,Anual,bienal,5 años,cada 4 meses"')
        ws.add_data_validation(dv); dv.add(f'{col}2:{col}200001')
    if 'Requiere desconexión' in headers:
        col = openpyxl.utils.get_column_letter(headers.index('Requiere desconexión')+1)
        dv = DataValidation(type='list', formula1='"SI,NO"'); ws.add_data_validation(dv); dv.add(f'{col}2:{col}200001')
    return ws


def create(path, count=40, sites=1, paired=False):
    book = openpyxl.Workbook(); book.remove(book.active)
    book.properties.created = book.properties.modified = datetime(2027, 1, 1)
    ws = book.create_sheet('Instrucciones')
    for line in ['Datos ficticios chilenos, no representan instalaciones reales.',
                 'Horizonte SUPUESTO: 01/01/2027 a 31/12/2027. Sin última ejecución: primer vencimiento al inicio.',
                 'A: equipo × tarea. B: cada pauta se aplica a los equipos del mismo tipo.',
                 'HH totales, no multiplicar por cuadrilla. Duración redondeada hacia arriba a días laborables.',
                 'Personal opcional: Nombre, Habilidades (separadas por ;). Ausencia: dotación mínima anual SUPUESTA.',
                 'Revise SUPUESTOS y cantidad prevista antes de confirmar. Tope: 100.000 ocurrencias.',
                 'A: semanal + mensual = 65/año por equipo. B: mensual + anual = 13/año por equipo.']:
        ws.append([line])
    ws.column_dimensions['A'].width = 120
    if paired:
        pautas = sheet(book, 'Pautas', ['Tipo de equipo', 'Tarea', 'Frecuencia', 'HH', 'Cuadrilla', 'Habilidad', 'Requiere desconexión'])
        for i in range(12):
            for task, freq in [('Inspección', 'Mensual'), ('Ensayo', 'Anual')]:
                pautas.append([f'Tipo {i+1}', task, freq, .25, 1, 'Mantenimiento', 'NO'])
        eq = sheet(book, 'Equipos', ['TAG', 'Tipo de equipo', 'Instalación', 'Tensión'])
        for i in range(count): eq.append([f'EQ-{i+1:05}', f'Tipo {i%12+1}', f'S/E Ficticia {i%sites+1}', 220])
    else:
        ws = sheet(book, 'Pauta', HEADERS)
        for i in range(count):
            for task, freq in [('Inspección', 'Semanal'), ('Revisión', 'Mensual')]:
                ws.append([f'S/E Ficticia {i%sites+1}', f'EQ-{i+1:05}', 'Interruptor', 220, task, freq, .25, 1, 'Mantenimiento', 'NO', '1 h', None, 50, 'Ejemplo ficticio'])
    path.parent.mkdir(parents=True, exist_ok=True); book.save(path); book.close()


def main():
    create(ROOT/'plantillas/formatos/plantilla_pauta_A.xlsx', 1)
    create(ROOT/'plantillas/formatos/plantilla_pauta_B.xlsx', 12, paired=True)
    create(ROOT/'ejemplos/formatos/pauta_1se_40.xlsx')
    create(ROOT/'ejemplos/formatos/pauta_5se_800.xlsx', 800, 5)
    create(ROOT/'ejemplos/formatos/pauta_B_12x300.xlsx', 3600, 5, True)


if __name__ == '__main__': main()
