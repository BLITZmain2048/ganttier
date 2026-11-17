"""Ejemplos ficticios reproducibles de S/E 220 kV; ejecutar con Python -B."""
from __future__ import annotations
import csv
from datetime import date, timedelta, datetime
import json
from pathlib import Path
import argparse
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ES = ['ID','Nombre de tarea','Duración','Comienzo','Fin','Predecesoras','Nombres de los recursos',
      '% completado','Nivel de esquema','Resumen','Costo','Trabajo','Comienzo real','Fin real',
      'Trabajo real','Restricción','Fecha de restricción','Calendario','Notas']
EN = ['ID','Task Name','Duration','Start','Finish','Predecessors','Resource Names','% Complete',
      'Outline Level','Summary','Cost','Work','Actual Start','Actual Finish','Actual Work',
      'Constraint Type','Constraint Date','Calendar','Notes']
P6 = ['Activity ID','Activity Name','Original Duration','Remaining Duration','Start','Finish','Predecessors',
      'Resource Names','Physical % Complete','Activity Type','WBS','Calendar','Budgeted Total Cost',
      'Budgeted Labor Units','Actual Start','Actual Finish','Actual Work','Primary Constraint','Primary Constraint Date']
TASK = ['task_id','task_code','task_name','target_drtn_hr_cnt','remain_drtn_hr_cnt','target_start_date',
        'target_end_date','phys_complete_pct','task_type','wbs_id','clndr_id','target_work_qty',
        'act_start_date','act_end_date','act_work_qty','cstr_type','cstr_date','target_cost']
BLOCKS = ['Ingeniería y permisos', 'Obras civiles', 'Montaje electromecánico', 'Protecciones y puesta en servicio']
BASE = date(2027, 1, 4)


def workday(n):
    weeks, rest = divmod(n, 5)
    return BASE + timedelta(days=weeks*7+rest)


def dataset(n=40):
    if n < 8 or n % 4: raise ValueError('Use un múltiplo de cuatro, de al menos ocho actividades.')
    result = []
    size = n//4
    for group in range(4):
        for i in range(size):
            code = f'A{group*size+i+1:05}'
            milestone = i == size-1
            day = workday(i).isoformat()
            result.append(dict(code=code, id=group*size+i+1, group=group+1,
                name=f'S/E Los Maitenes 220 kV · {BLOCKS[group]} · '+('Hito de recepción' if milestone else f'Frente {i+1:03}'),
                duration=0 if milestone else 1, start=day, finish=day,
                pred=f'A{group*size+i:05}' if i else '', complete=100 if i == 0 else 0,
                actual=day if i == 0 else '', actual_work=8 if i == 0 else 0,
                resources='' if milestone else f'Técnico {group+1}[50%];Especialista {group+1}[50%]',
                work=0 if milestone else 8, cost=0 if milestone else 160000,
                constraint='SNET' if i == 0 else 'ASAP', constraint_date=day if i == 0 else '',
                wbs=f'SE.P{group+1}'))
    return result


def msp_rows(data, lang):
    es = lang == 'es'; out = []
    out.append(['S0', 'S/E Los Maitenes 220 kV', '', '', '', '', '', '', 1, 'SI' if es else 'Yes'])
    for group in range(1, 5):
        out.append([f'S{group}', BLOCKS[group-1], '', '', '', '', '', '', 2, 'SI' if es else 'Yes'])
        for a in data:
            if a['group'] != group: continue
            out.append([a['code'],a['name'],f"{a['duration']} d",a['start'],a['finish'],
                a['pred']+('FC' if es else 'FS') if a['pred'] else '',a['resources'],a['complete'],3,'NO' if es else 'No',
                a['cost'],f"{a['work']} h",a['actual'],a['actual'],a['actual_work'],
                ('No comenzar antes del' if es else 'Start No Earlier Than') if a['constraint']=='SNET' else ('Lo antes posible' if es else 'As Soon As Possible'),
                a['constraint_date'],'Jornada SE','Ejemplo ficticio; valores de demostración.'])
    return out


def single_rows(data):
    return [[a['code'],a['name'],a['duration'],0 if a['complete'] else a['duration'],a['start'],a['finish'],
             a['pred']+' FS' if a['pred'] else '',a['resources'],a['complete'],'Finish Milestone' if not a['duration'] else 'Task Dependent',
             a['wbs'],'Jornada SE',a['cost'],a['work'],a['actual'],a['actual'],a['actual_work'],a['constraint'],a['constraint_date']] for a in data]


def tables_multi(data):
    tasks = [[a['id'],a['code'],a['name'],a['duration']*8,0 if a['complete'] else a['duration']*8,a['start'],a['finish'],
              a['complete'],'TT_FinMile' if not a['duration'] else 'TT_Task',a['group']*10,1,a['work'],a['actual'],a['actual'],
              a['actual_work'],'CS_SNET' if a['constraint']=='SNET' else '',a['constraint_date'],a['cost']] for a in data]
    wbs = [[1,'SE','S/E Los Maitenes 220 kV',0]] + [[i*10,f'SE.P{i}',name,1] for i,name in enumerate(BLOCKS,1)]
    resources = [[2*g-1,f'Técnico {g}','RT_Labor',1,1,8,'',20000] for g in range(1,5)]
    resources += [[2*g,f'Especialista {g}','RT_Labor',1,1,8,'',20000] for g in range(1,5)]
    assignments = [[a['id'],rid,0.5,4,4 if a['complete'] else 0] for a in data if a['duration'] for rid in (a['group']*2-1,a['group']*2)]
    pred = [[a['id']-1,a['id'],'PR_FS',0] for a in data if a['pred']]
    return {'TASK':(TASK,tasks), 'TASKPRED':(['pred_task_id','task_id','pred_type','lag_hr_cnt'],pred),
        'PROJWBS':(['wbs_id','wbs_short_name','wbs_name','parent_wbs_id'],wbs),
        'RSRC':(['rsrc_id','rsrc_name','rsrc_type','clndr_id','max_units','hours_per_day','skills','cost_per_qty'],resources),
        'TASKRSRC':(['task_id','rsrc_id','target_qty_per_hr','target_qty','act_reg_qty'],assignments),
        'CALENDAR':(['clndr_id','clndr_name','workweek','day_hr_cnt','holidays_country','exceptions'],[[1,'Jornada SE','1111100',8,'','[]']])}


def workbook(path, tables, *, template=False):
    wb = Workbook(); wb.remove(wb.active)
    wb.properties.created = wb.properties.modified = datetime(2026,11,9)
    ins = wb.create_sheet('Instrucciones')
    lines = ['Formato MSP/P6 para Ganttier — ejemplo ficticio S/E 220 kV.',
        'Mantenga los encabezados; ID único, nombre y duración son obligatorios. Fechas AAAA-MM-DD o DD-MM-AAAA.',
        'Duración: días laborables, semanas u horas. Hitos: cero días y cero HH. Las filas resumen forman WBS.',
        'Predecesoras: A00001FS+2d;A00002SS-1d. Equivalencias ES: FC, CC, FF, CF.',
        'Recursos: Nombre[50%];Otro. Los porcentajes sin formato Excel se expresan entre 0 y 100.',
        'Revise SUPUESTOS antes de confirmar: jornada, moneda, corte, HH ausentes y redondeo.',
        'Calendar sin tabla CALENDAR solo aporta el nombre; no declara la semana laboral.',
        'TASK usa horas en *_hr_cnt. CALENDAR simple: workweek lunes–domingo, day_hr_cnt y exceptions JSON.',
        'Los ejemplos tienen 4 cadenas independientes, 4 hitos, 8 recursos y 4 actividades terminadas.',
        'Plantilla vacía: complete las hojas de datos; las instrucciones no se importan.' if template else 'No usar como un programa real de obra.']
    for line in lines: ins.append([line])
    ins.column_dimensions['A'].width=115
    for title,(headers, rows) in tables.items():
        ws=wb.create_sheet(title); ws.append(headers)
        for cell in ws[1]:
            cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='164B65')
            cell.comment=Comment(f'{cell.value}. Consulte Instrucciones y docs/decisiones/formato_msp_p6.md. Valores literales; no fórmulas.','Ganttier')
        for row in ([] if template else rows): ws.append(row)
        ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width=26
        for j,h in enumerate(headers,1):
            if h in ('Resumen','Summary'):
                dv=DataValidation(type='list',formula1='"SI,NO,Yes,No"'); ws.add_data_validation(dv); dv.add(f'{ws.cell(1,j).column_letter}2:{ws.cell(1,j).column_letter}200001')
            elif h in ('Activity Type','task_type'):
                choices='Task Dependent,Resource Dependent,Start Milestone,Finish Milestone,WBS Summary,Level of Effort' if h=='Activity Type' else 'TT_Task,TT_Rsrc,TT_Mile,TT_FinMile,TT_WBSsum,TT_LOE'
                dv=DataValidation(type='list',formula1='"'+choices+'"'); ws.add_data_validation(dv); dv.add(f'{ws.cell(1,j).column_letter}2:{ws.cell(1,j).column_letter}200001')
    path.parent.mkdir(parents=True,exist_ok=True); wb.save(path); wb.close()


def generate(root=None, sizes=(40,2000)):
    root=Path(root or Path(__file__).resolve().parents[1])
    examples=root/'ejemplos'/'formatos'; templates=root/'plantillas'/'formatos'
    examples.mkdir(parents=True,exist_ok=True)
    manifest={}
    for n in sizes:
        data=dataset(n)
        variants={'msp_es':{'Tareas':(ES,msp_rows(data,'es'))}, 'msp_en':{'Tasks':(EN,msp_rows(data,'en'))},
                  'p6_unica':{'Activities':(P6,single_rows(data))}, 'p6_multi':tables_multi(data)}
        counts=dict(actividades=n,relaciones=n-4,wbs=5,hitos=4,recursos=8,asignaciones=2*(n-4),avances=4,calendarios=2,hh_total=8*(n-4))
        for variant,tables in variants.items():
            stem=f'se220_{variant}_{n}'
            workbook(examples/(stem+'.xlsx'),tables)
            manifest[stem+'.xlsx']=dict(counts,items=n+5 if variant.startswith('msp') else n)
            if variant!='p6_multi':
                headers,rows=next(iter(tables.values()))
                with (examples/(stem+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
                    w=csv.writer(f,delimiter=';'); w.writerow(headers); w.writerows(rows)
                manifest[stem+'.csv']=manifest[stem+'.xlsx'].copy()
            if n==sizes[0]: workbook(templates/(f'plantilla_{variant}.xlsx'),tables,template=True)
    (examples/'conteos_msp_p6.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--destino',type=Path)
    args=parser.parse_args(); print(json.dumps(generate(args.destino),ensure_ascii=False,indent=2))
