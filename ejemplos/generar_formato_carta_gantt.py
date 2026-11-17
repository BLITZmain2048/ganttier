"""Ejemplos ficticios reproducibles: ejecutar con Python desde cualquier cwd."""
from pathlib import Path
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Alignment
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation


def generar(destino, cantidad=60, semanal=False, sitios=1, color=False):
    book=Workbook(); ws=book.active; ws.title='Carta'
    n=52 if semanal else 12
    ws.append(['Carta Gantt — datos ficticios']); ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=8)
    ws.cell(2,9,2027); ws.merge_cells(start_row=2,start_column=9,end_row=2,end_column=8+n)
    ws.append(['Código','Actividad','Sitio','Responsable','HH','Duración','Dotación','Habilidad']+[f'S{i}' if semanal else f'Mes {i}' for i in range(1,n+1)])
    for c in ws[3]: c.comment=Comment('Entrada literal. Períodos: marcas X o relleno sólido. HH por tramo/ocurrencia; duración en días laborables.','Ganttier')
    for i in range(cantidad):
        row=4+i
        ws.append([f'{i//10+1}.{i%10+1}',f'Inspección S/E y línea 220 kV {i+1}',f'S/E {i%sitios+1}','Responsable ficticio',8,1,1,''])
        marks=[i%n] if sitios==1 else [0,3,6,9]
        for j in marks:
            cell=ws.cell(row,9+j)
            if color: cell.fill=PatternFill('solid',fgColor='4472C4')
            else: cell.value='X'
    dv=DataValidation(type='list',formula1='"X,✓,●"'); ws.add_data_validation(dv); dv.add(f'I4:{ws.cell(3,8+n).column_letter}{cantidad+3}')
    ws.freeze_panes='I4'; ws.column_dimensions['B'].width=45
    inst=book.create_sheet('Instrucciones')
    for line in ['Datos ficticios chilenos. No representan un plan contractual.',
                 'Elegir interpretación ventana_macro o periodica y aceptar SUPUESTOS.',
                 'Cada fila tiene una marca; matriz de tres sitios: cuatro marcas por fila.',
                 'HH y duración se aplican a cada tramo u ocurrencia. Responsable es metadata.',
                 'El color es relleno sólido directo; no se evalúa formato condicional.',
                 'Año superior combinado; Mes N y S N usan el año del perfil.']:
        inst.append([line])
    book.properties.created=book.properties.modified=datetime(2027,1,1)
    destino=Path(destino); destino.parent.mkdir(parents=True,exist_ok=True); book.save(destino); book.close()


def main():
    root=Path(__file__).resolve().parents[1]
    generar(root/'plantillas/formatos/plantilla_carta_gantt.xlsx',3)
    generar(root/'ejemplos/formatos/carta_anual_60.xlsx',60,color=True)
    generar(root/'ejemplos/formatos/carta_semanal_200.xlsx',200,semanal=True)
    generar(root/'ejemplos/formatos/carta_periodica_3_sitios.xlsx',30,sitios=3)
    generar(root/'ejemplos/formatos/carta_grande_20000.xlsx',20000)

if __name__=='__main__': main()
