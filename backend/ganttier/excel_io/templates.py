"""Plantillas autocontenidas y presentación común para libros de intercambio."""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
import argparse
import warnings

from openpyxl.cell import WriteOnlyCell
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.workbook.defined_name import DefinedName

from ganttier.domain import ImportBundle
from .schema import CHOICES, ENTITIES, SCHEMAS, Column
from .interchange import MARKER, ABSENT, encode
from .streaming import StreamingWorkbook as Workbook, create_sheet, close_workbook

NAVY = "17354A"
TEAL = "087E8B"
RED = PatternFill("solid", fgColor="FCE4D6")
DESCRIPTIONS = {
    "date": "Fecha real de Excel o texto AAAA-MM-DD / DD-MM-AAAA / DD/MM/AAAA.",
    "number": "Número mayor o igual a cero; se admite coma decimal.",
    "int": "Días/unidades enteros mayores o iguales a cero.",
    "signed_int": "Entero; se admiten valores negativos (adelantos u holguras).",
    "percent": "Avance físico acumulado de 0 a 100. Escriba 50 para 50 %.",
    "bool": "SI o NO.", "month": "Mes macro AAAA-MM; el backend determina su ventana mensual.",
    "skills": "Habilidades separadas por coma, por ejemplo electrico,protecciones.",
    "week": "Siete dígitos de lunes a domingo: 1111100 = lunes a viernes.",
    "shift": "Días de trabajo x descanso, por ejemplo 7x7; requiere Ancla_Turno.",
    "weekday": "Día preferido: 0=lunes, 1=martes, …, 6=domingo.",
    "monthday": "Día del mes: 1 a 31.", "months": "Meses preferidos: 1,4,7,10.",
    "priority": "Prioridad de 0 a 100; mayor número = mayor prioridad.",
    "positive_int": "Entero mayor o igual a 1; N para las frecuencias ND/NW/NM/NA.",
}


def _literal(cell: Any, value: Any) -> None:
    """Escribe texto como texto: los datos importados nunca se vuelven fórmulas."""
    if isinstance(value, list):
        value = ",".join(map(str, value))
    if isinstance(value, bool):
        value = "SI" if value else "NO"
    if isinstance(value, str) and len(value) > 32767:
        raise ValueError(f'{cell.parent.title}, fila {cell.row}, columna {cell.column_letter}: el texto supera los 32.767 caracteres de Excel.')
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def _name(workbook: Workbook, name: str, formula: str) -> None:
    value = DefinedName(name, attr_text=formula)
    if hasattr(workbook.defined_names, "add"):
        workbook.defined_names.add(value)
    else:
        workbook.defined_names.append(value)


def _lists(workbook: Workbook) -> None:
    sheet = create_sheet(workbook, "Listas", buffered=True)
    for idx, (kind, values) in enumerate(CHOICES.items(), 1):
        letter = get_column_letter(idx)
        sheet.cell(1, idx, kind)
        for row, value in enumerate(values, 2):
            sheet.cell(row, idx, value)
        _name(workbook, "lista_" + kind, f"'Listas'!${letter}$2:${letter}${len(values) + 1}")
    description_column = len(CHOICES) + 2
    sheet.cell(1, description_column, "Frecuencia")
    sheet.cell(1, description_column + 1, "Descripción")
    descriptions = ["Diaria (días corridos)", "Semanal", "Mensual", "Trimestral", "Semestral", "Anual", "Cada N días", "Cada N semanas", "Cada N meses", "Cada N años", "Diaria hábil (calendario de la tarea)"]
    for row, (code, meaning) in enumerate(zip(CHOICES["frequency"], descriptions), 2):
        sheet.cell(row, description_column, code)
        sheet.cell(row, description_column + 1, meaning)
    sheet.sheet_state = "hidden"


def _validation(sheet: Any, column: Column, target: str) -> None:
    kind = column.kind
    if kind in CHOICES:
        validation = DataValidation(type="list", formula1="=lista_" + kind)
    elif kind == "date":
        validation = DataValidation(type="date", operator="between", formula1="1", formula2="2958465")
    elif kind in {"number", "int", "positive_int", "signed_int", "priority", "percent", "weekday", "monthday"}:
        low = 1 if kind in {"positive_int", "monthday"} else (-1000000 if kind == "signed_int" else 0)
        high = {"priority": 100, "percent": 100, "weekday": 6, "monthday": 31}.get(kind, 1e15)
        validation = DataValidation(type="decimal" if kind in {"number", "percent"} else "whole", operator="between", formula1=str(low), formula2=str(high))
        sheet.conditional_formatting.add(target, CellIsRule(operator="lessThan", formula=[str(low)], fill=RED))
        if high != 1e15:
            sheet.conditional_formatting.add(target, CellIsRule(operator="greaterThan", formula=[str(high)], fill=RED))
    else:
        return
    validation.allow_blank = not column.required
    validation.showErrorMessage = True
    validation.showInputMessage = True
    validation.errorTitle = "Revise el valor"
    validation.error = "El valor no cumple el tipo o rango indicado en el encabezado."
    validation.promptTitle = column.title[:32]
    validation.prompt = DESCRIPTIONS.get(kind, "Seleccione una opción de la lista.")[:255]
    validation.errorStyle = "stop"
    sheet.data_validations.append(validation)
    validation.add(target)


def _record_rows(records, fields, extras):
    """Codifica valores repetidos una vez, sin duplicar el recorrido de la fila."""
    missing = object()

    @lru_cache(maxsize=4096, typed=True)
    def scalar(value):
        if extras:
            value = encode({'value': value}, 'value')
        return 'SI' if value is True else 'NO' if value is False else value

    for record in records:
        values = []
        for field in fields:
            value = record.get(field, missing)
            if value is missing:
                value = ABSENT if extras else None
            elif isinstance(value, list):
                value = ((r'\J[]' if field == 'exceptions' else encode(record, field))
                         if extras else ','.join(map(str, value)))
            else:
                value = scalar(value)
            values.append(value)
        yield values


def _table_sheet(workbook: Workbook, title: str, cols: list[Column], records: list[dict], extras=False) -> None:
    sheet = create_sheet(workbook, title)
    sheet.freeze_panes = "C2"
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = '9'
    sheet.page_setup.fitToWidth = 1
    sheet.print_title_rows = "1:1"
    headers = []
    for idx, col in enumerate(cols, 1):
        cell = WriteOnlyCell(sheet, col.title)
        headers.append(cell)
        cell.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        text = DESCRIPTIONS.get(col.kind, "Texto; use los mismos códigos/nombres que en las hojas maestras.")
        if col.kind in CHOICES:
            text += " Opciones: " + ", ".join(CHOICES[col.kind]) + "."
        cell.comment = Comment(("Obligatorio. " if col.required else "Opcional. ") + text, "Ganttier")
        letter = get_column_letter(idx)
        sheet.column_dimensions[letter].width = 38 if col.field in {"name", "notes", "note", "reason"} else max(16, min(26, len(col.title) + 3))
        if col.field == "team_names" or (title == "Equipos" and col.field == "calendar_name"):
            sheet.column_dimensions[letter].width = 48
        dimension = sheet.column_dimensions[letter]
        dimension.font = Font(name="Arial", size=10, color="174F9C")
        dimension.alignment = Alignment(vertical="top", wrap_text=col.field in {"name", "notes", "note"})
        dimension.number_format = {"date": "dd-mm-yyyy", "percent": '0.0" %"',
                                   "number": '#,##0.00', "int": '#,##0',
                                   "signed_int": '#,##0'}.get(col.kind, 'General')
        if col.kind == 'date':
            sheet.date_styles.add(dimension.style_id)
        _validation(sheet, col, f"{letter}2:{letter}1048576")
        if col.field in {"code", "name"} and title in {"Calendarios", "Recursos", "WBS", "Actividades", "Plan_Maestro"}:
            if col.field == "code" or title in {"Calendarios", "Recursos"}:
                formula = f'AND({letter}2<>"",COUNTIF({letter}$2:{letter}$5001,{letter}2)>1)'
                sheet.conditional_formatting.add(f"{letter}2:{letter}5001", FormulaRule(formula=[formula], fill=RED))
    sheet.row_dimensions[1].height = 36
    sheet.append(headers)
    fields = [col.field for col in cols]
    for values in _record_rows(records, fields, extras):
        sheet.append(values)
    last = max(2, len(records) + 1)
    ref = f"A1:{get_column_letter(len(cols))}{last}"
    table = Table(displayName="Tabla_" + title, ref=ref,
                  tableColumns=[TableColumn(id=i, name=c.title) for i, c in enumerate(cols, 1)],
                  autoFilter=AutoFilter(ref=ref))
    # Sólo la tabla declara el filtro (un autoFilter de hoja sobre el mismo rango obliga a Excel a reparar).
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='In write-only mode you must add table columns manually')
        sheet.add_table(table)
    for field, name in (("code", "Codigos_" + title), ("name", "Nombres_" + title)):
        found = next((i for i, c in enumerate(cols, 1) if c.field == field), None)
        if found:
            letter = get_column_letter(found)
            _name(workbook, name, f'Tabla_{title}[{cols[found - 1].title}]')


def _instructions(workbook: Workbook, mode: str, sheets: dict[str, list[Column]]) -> None:
    sheet = create_sheet(workbook, "Instrucciones", 0, buffered=True)
    rows = [
        ("GANTTIER · " + mode.upper(), "Plantilla de planificación eléctrica · datos de ejemplo ficticios"),
        ("1 · Complete Proyecto", "Nombre, modo, fechas del horizonte, calendario, moneda y fecha de corte."),
        ("2 · Defina maestros", "Calendarios y recursos primero. Edite las celdas azules; los encabezados incluyen comentarios y listas."),
        ("3 · Reemplace los ejemplos", "Las filas son demostrativas: reemplácelas o elimínelas antes de importar su proyecto."),
        ("4 · Identificadores", "Los códigos/nombres son únicos, sensibles a mayúsculas. Las referencias deben coincidir exactamente."),
        ("5 · Fechas y números", "Fechas Excel o DD-MM-AAAA, DD/MM/AAAA, AAAA-MM-DD. Coma decimal admitida. Avance 0..100, HH del registro no acumuladas."),
        ("6 · Relaciones", "FS: fin a inicio; SS: inicio a inicio; FF: fin a fin; SF: inicio a fin. Desfase en días laborales del sucesor; negativo = adelanto."),
        ("7 · Restricciones", "ASAP: pronto; ALAP: tarde; SNET/SNLT: inicio no antes/después; FNET/FNLT: fin no antes/después; MSO/MFO: inicio/fin obligatorio. Excepto ASAP/ALAP requieren fecha."),
        ("8 · Mes_Macro", "AAAA-MM se interseca con Ventana_Inicio/Fin: nunca amplía una ventana explícita. Una intersección vacía se rechaza. Ventana_Dura=SI obliga; NO penaliza desviaciones."),
        ("Horas, rutas y rondas", "Lotes por horas por defecto: cantidad/rendimiento por hora; la tasa diaria se divide por Horas_Productivas_Dia o por horas del calendario. Lote_Dia_Completo=SI redondea a jornadas. Rutas contiene un único rendimiento y lote por recorrido; Ruta en Plan_Maestro vincula el checklist sin duplicar demanda. Ronda_Diaria=SI consume la suma de HH fijas de sus ítems, sin lotes. Los códigos de ítems se conservan; programar no acredita ejecución real."),
        ("9 · Mantenimiento", "D diaria en días corridos; DH diaria hábil según calendario de la tarea (feriados y excepciones incluidos). W semanal, M mensual, Q trimestral, S semestral, A anual; ND/NW/NM/NA cada N días/semanas/meses/años. Intervalo=N."),
        ("10 · Avance de mantenimiento", "Use el código de ocurrencia TAREA-0001, TAREA-0002, etc. Debe pertenecer a una tarea del Plan_Maestro. La existencia efectiva se verifica al aplicar la importación."),
        ("11 · Guardar e importar", "Guarde como .xlsx. Pegue las fórmulas como valores antes de importar. Revise los errores antes de confirmar en la app."),
        ("12 · Asignaciones", "Puede indicar Recurso o Habilidad; si indica ambos se conserva la habilidad como atributo de la asignación."),
        ("13 · Campos opcionales", "Las columnas extra al exportar preservan fechas programadas, estados y datos de control del dominio. No cambie sus nombres."),
        ("Exportación exacta", r"En libros exportados: \A = campo ausente, \N = nulo, \J = lista. Reemplace estos marcadores al completar un dato. No elimine la hoja técnica _Ganttier."),
        ("Duplicados", "El color de advertencia revisa las primeras 5.000 filas. Al importar se valida el libro completo, incluidas referencias y ciclos."),
        ("Semáforo del informe", "SPI(t), o SPI si falta SPI(t): ≥ 0,95 verde; 0,85 a < 0,95 amarillo; < 0,85 rojo; sin índice: sin datos."),
        ("Columna", "Descripción / unidad"),
    ]
    if mode == "mantenimiento":
        rows[-1:-1] = [
            ("Supuestos · HH y duración", "HH_por_Ejecucion son HH totales por ocurrencia; no se multiplican por Dotacion. Se reparten entre miembros y días laborables. Duracion_Dias no implica dedicación exclusiva."),
            ("Supuestos · Calendario", "DH crea objetivos solo en días con horas positivas del calendario efectivo. D conserva fines de semana y feriados como objetivos; tolerancia cero puede dejar fechas pendientes. Los índices conservan los días desde el ancla y pueden tener saltos."),
            ("Supuestos · Ventanas", "La duración completa debe caber dentro de la ventana y el horizonte. Si no cabe, se conserva la ocurrencia pendiente: revise tolerancia, duración o calendario; no se elimina silenciosamente."),
            ("Supuestos · Recursos y alcance", "Las filas de ejemplo no acreditan rendimientos ni dotación suficiente. Verifique habilidades, HH, tarifas, traslados, vacaciones y disponibilidad de equipos. Una marca de desconexión no equivale a un permiso autorizado."),
            ("Supuestos · Control", "Con corte inicial los informes futuros son de planificación. No interprete SPI/CPI como desempeño observado sin avance y consumos registrados."),
        ]
    if mode == "mantenimiento":
        rows.extend([
            ("Equipos y cantidad", "Ejemplo: Cantidad_Total=28000, Unidad=paneles, Tamano_Lote=2000, Rendimiento_Dia_Equipo=1000, Equipos=Equipo A,Equipo B. Son 14 lotes mensuales: 7 por equipo, 14000 paneles y 2 días por lote. Complete la hoja Equipos y Primera_Fecha."),
            ("Factibilidad de 7x7", "Con Lote_Continuo_En_Turno=NO, dos bloques de 7 días admiten 7 lotes de 2 días efectivos, continuando después del descanso. Con SI solo caben 6. Nunca se trabaja en días libres."),
            ("Lote_Continuo_En_Turno", "NO por defecto: el lote continúa en los siguientes días de turno del mismo equipo dentro de su ventana. SI exige continuidad sin descanso. Duración y HH solo consumen días efectivos."),
            ("Equidad por ciclo", "Si la frecuencia es menor que el ciclo N+M, se asignan los lotes a equipos con turno en la ventana natural de la ocurrencia. La equidad se revisa por ciclo y por mes completos. Tolerancia_Equidad_Lotes=1 por defecto. En las otras frecuencias el resto rota por ocurrencia."),
            ("Regímenes y feriados", "5x2 significa lunes a viernes, con feriados de Chile no laborables por defecto. En NxM se trabajan feriados por defecto. Feriados_Trabajan permite cambiarlo. Inicio_Ciclo es propio de cada equipo; los equipos son compartidos por las tareas/plantas del mismo plan."),
        ])
    for title, cols in sheets.items():
        for col in cols:
            desc = DESCRIPTIONS.get(col.kind, "Texto o referencia por código/nombre.")
            if col.kind in CHOICES:
                desc += " " + ", ".join(CHOICES[col.kind])
            rows.append((f"{title} · {col.title}", ("Obligatorio. " if col.required else "Opcional. ") + desc))
    for row in rows:
        sheet.append(row)
    sheet.column_dimensions["A"].width = 42
    sheet.column_dimensions["B"].width = 115
    sheet.freeze_panes = "B2"
    sheet.sheet_view.showGridLines = False
    for row in sheet:
        for cell in row:
            cell.font = Font(name="Arial", size=11, color=NAVY)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[row[0].row].height = 45 if row[0].row <= 15 else 32
        if str(row[0].value).startswith("Supuestos") or row[0].value in {"Equipos y cantidad", "Factibilidad de 7x7", "Equidad por ciclo", "Regímenes y feriados"}:
            sheet.row_dimensions[row[0].row].height = 60
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(name="Arial", size=14, bold=True, color="FFFFFF")


def make_workbook(bundle: ImportBundle, extras: bool = False, *, temp_dir=None) -> Workbook:
    """Construye el libro con las mismas hojas y convenciones de las plantillas."""
    workbook = Workbook(write_only=True)
    workbook._ganttier_temp_dir = temp_dir
    try:
        return _populate_workbook(workbook, bundle, extras)
    except BaseException:
        close_workbook(workbook)
        raise


def _populate_workbook(workbook, bundle, extras):
    from .schema import all_columns
    if extras:
        metadata = create_sheet(workbook, '_Ganttier', buffered=True)
        metadata.append([MARKER, bundle.mode])
        metadata.sheet_state = 'veryHidden'
    _lists(workbook)
    project = create_sheet(workbook, "Proyecto", 0, buffered=True)
    project.append(["Clave", "Valor"])
    cols = list(SCHEMAS["Proyecto"])
    if extras:
        cols += [c for c in all_columns("Proyecto") if c.field in bundle.project and c.field not in {x.field for x in cols}]
        unknown = set(bundle.project) - {c.field for c in cols}
        if unknown:
            raise ValueError(f"Campos no reconocidos en Proyecto: {', '.join(sorted(unknown))}.")
    for rownum, col in enumerate(cols, 2):
        project.cell(rownum, 1, col.title)
        value = encode(bundle.project, col.field) if extras else (bundle.mode if col.field == "mode" else bundle.project.get(col.field))
        if col.kind == 'date' and isinstance(value, str) and not value.startswith('\\'):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                pass
        _literal(project.cell(rownum, 2), value)
        project.cell(rownum, 2).number_format = "dd-mm-yyyy" if col.kind == "date" else "General"
        _validation(project, col, f"B{rownum}")
    project.column_dimensions["A"].width = 25
    project.column_dimensions["B"].width = 65
    project.freeze_panes = "B2"
    for row in project:
        for cell in row:
            cell.font = Font(name="Arial", size=11, color="174F9C" if cell.column == 2 else NAVY)
    for cell in project[1]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
    sheets = {}
    preferred = ["Calendarios", "Excepciones_Calendario", "Recursos", "Equipos", "WBS", "Actividades", "Relaciones", "Asignaciones", "Plan_Maestro", "Rutas", "Avance"]
    for title in preferred:
        if title == "Excepciones_Calendario":
            records = [dict(exception, calendar_name=cal["name"]) for cal in bundle.calendars for exception in (cal.get("exceptions") or [])]
        else:
            records = getattr(bundle, ENTITIES[title][0])
            if title in {"Plan_Maestro", "Rutas"}:
                records = [r for r in records if bool(r.get("is_route")) == (title == "Rutas")]
        if title in {"Plan_Maestro", "Rutas", "Equipos"} and bundle.mode == "proyecto" and not records:
            continue
        if title in {"WBS", "Actividades", "Relaciones", "Asignaciones"} and bundle.mode == "mantenimiento" and not records:
            continue
        cols = list(SCHEMAS[title])
        if title == "Actividades" and bundle.mode == "proyecto":
            # Las columnas de lotes son opt-in de mantenimiento. No ensanchar
            # las plantillas tradicionales de proyectos (extras conserva datos existentes).
            cols = [c for c in cols if c.field not in {"team_name", "quantity", "unit", "batch_index", "equity_mode"}]
        if extras:
            known = {c.field for c in cols}
            present = set()
            for record in records:
                present.update(record)
            cols += [c for c in all_columns(title) if c.field in present and c.field not in known]
            unknown = present - {c.field for c in cols} - {"exceptions"}
            if unknown:
                raise ValueError(f"Campos no reconocidos en {title}: {', '.join(sorted(unknown))}.")
        sheets[title] = cols
        _table_sheet(workbook, title, cols, records, extras)
    references = {
        "calendar_name": "Nombres_Calendarios", "resource_name": "Nombres_Recursos",
        "wbs_code": "Codigos_WBS", "parent_code": "Codigos_WBS",
        "pred_code": "Codigos_Actividades", "succ_code": "Codigos_Actividades",
        "activity_code": "Codigos_Actividades", "periodic_task_code": "Codigos_Plan_Maestro",
    }
    calendar_row = next(i for i, col in enumerate(SCHEMAS['Proyecto'], 2) if col.field == 'calendar_name')
    validation = DataValidation(type='list', formula1='=Nombres_Calendarios', allow_blank=True)
    validation.showErrorMessage = True
    validation.error = 'Seleccione un calendario de la hoja Calendarios.'
    project.add_data_validation(validation)
    validation.add(f'B{calendar_row}')
    for sheet_title, cols in sheets.items():
        for index, col in enumerate(cols, 1):
            name = references.get(col.field)
            # En mantenimiento, Avance puede referir ocurrencias aún no materializadas.
            if name and name in workbook.defined_names and not (name == "Codigos_Actividades" and sheet_title == "Avance" and bundle.mode == "mantenimiento"):
                validation = DataValidation(type="list", formula1="=" + name, allow_blank=not col.required)
                validation.showErrorMessage = True
                validation.error = "Seleccione un código/nombre existente en la hoja maestra."
                validation.errorTitle = "Referencia inexistente"
                workbook[sheet_title].data_validations.append(validation)
                letter = get_column_letter(index)
                validation.add(f"{letter}2:{letter}1048576")
    if extras:
        # Los marcadores sólo aparecen en exportaciones, nunca en plantillas.
        workbook['_Ganttier'].append(['Convenciones', r'\A: campo ausente; \N: nulo; \J: lista JSON; \\: texto escapado.'])
    _instructions(workbook, bundle.mode, {"Proyecto": SCHEMAS["Proyecto"], **sheets})
    workbook.active = 0
    return workbook


def example_bundle(mode: str) -> ImportBundle:
    """Ejemplo eléctrico ficticio sin datos personales reales."""
    start = date(2026, 10, 1)
    bundle = ImportBundle(mode=mode, project={"name": "S/E Cordillera · programa eléctrico 220 kV", "start_date": start, "end_date": date(2026, 12, 31), "data_date": date(2026, 10, 9), "calendar_name": "Chile 5x2 · ventana de ejemplo", "currency": "CLP"})
    bundle.calendars = [{"name": "Chile 5x2 · ventana de ejemplo", "workweek": "1111100", "hours_per_day": 8, "holidays_country": "CL", "exceptions": [{"date": date(2026, 10, 3), "working": True, "hours": 8, "reason": "Ventana autorizada de desconexión"}]}]
    skills = ["SSEE", "protecciones", "lineas"]
    bundle.resources = [{"name": name, "kind": "trabajador", "skills": [skill, "electrico"], "capacity_hours_day": 8, "max_units": 1, "calendar_name": "Chile 5x2 · ventana de ejemplo", "cost_hour": 15000, "overtime_cost_hour": 22500, "max_overtime_hours_day": 2, "email": f"tecnico{i}@example.com"} for i, (name, skill) in enumerate(zip(["Técnico SSEE A", "Técnica Protecciones B", "Técnico Líneas C"], skills), 1)]
    names = ["Termografía del transformador de poder 220/66 kV", "Pruebas de relés e interruptor del paño H1 de 110 kV", "Lavado de aislación línea Cordillera 154 kV"]
    if mode == "proyecto":
        bundle.wbs = [{"code": "SE", "name": "S/E Cordillera"}, {"code": "SE.P", "name": "Protecciones", "parent_code": "SE"}, {"code": "LT", "name": "Línea 154 kV"}]
        bundle.activities = [{"code": f"A{i:03d}", "name": name, "wbs_code": wbs, "duration_days": 2, "work_hours": 16, "cost_budget": 240000, "macro_month": "2026-10", "window_hard": False, "constraint_type": "ASAP", "priority": 50, "calendar_name": "Chile 5x2 · ventana de ejemplo", "required_skill": skill, "crew_size": 1, "location": "S/E Cordillera" if i < 3 else "LT Cordillera", "equipment": tag, "voltage_kv": 220, "requires_outage": i == 2, "discipline": "Eléctrica", "notes": "Ejemplo ficticio; reemplazar"} for i, (name, wbs, skill, tag) in enumerate(zip(names, ["SE", "SE.P", "LT"], skills, ["TR-1", "J1", "LT-1"]), 1)]
        bundle.relations = [{"pred_code": "A001", "succ_code": "A002", "type": "FS", "lag_days": 0}, {"pred_code": "A001", "succ_code": "A003", "type": "SS", "lag_days": 1}]
        bundle.assignments = [{"activity_code": f"A{i:03d}", "resource_name": r["name"], "units": 1, "work_hours": 16} for i, r in enumerate(bundle.resources, 1)]
    else:
        bundle.periodic_tasks = [{"code": f"PM{i:03d}", "name": name, "frequency": freq, "interval": 1, "duration_days": 1, "work_hours": 8, "crew_size": 1, "required_skill": skill, "resource_name": r["name"], "first_due": start, "preferred_months": "10,11,12", "tol_before_days": 3, "tol_after_days": 7, "window_hard": False, "location": "S/E Cordillera", "equipment": f"EQ-{i}", "voltage_kv": 220, "requires_outage": i == 2, "discipline": "Eléctrica", "calendar_name": "Chile 5x2 · ventana de ejemplo", "priority": 50, "cost_per_occurrence": 120000} for i, (name, freq, skill, r) in enumerate(zip(names, ["M", "Q", "W"], skills, bundle.resources), 1)]
        for i, name, voltage, tag in [(4, 'Análisis de aceite y gases disueltos del transformador de poder', 66, 'TR-2'), (5, 'Termografía de conexiones en línea de 110 kV', 110, 'LT-2'), (6, 'Medición de tiempos del interruptor de potencia de 220 kV', 220, '52J1')]:
            bundle.periodic_tasks.append(dict(bundle.periodic_tasks[0], code=f'PM{i:03d}', name=name, voltage_kv=voltage, equipment=tag, frequency='S', requires_outage=i == 6))
    for i, record in enumerate(bundle.activities or bundle.periodic_tasks):
        if i < 3:
            record['voltage_kv'] = [220, 110, 154][i]
            record['equipment'] = ['TR-1', '52H1', 'LT-1'][i]
            record['requires_outage'] = i in (1, 2)
            record['location'] = 'LT Cordillera' if i == 2 else 'S/E Cordillera'
    bundle.progress = [{"activity_code": f"A{i:03d}" if mode == "proyecto" else f"PM{i:03d}-0001", "date": date(2026, 10, 9), "percent_complete": pct, "actual_hours": hh, "actual_cost": hh * 15000, "resource_name": r["name"], "actual_start": start if pct else None, "actual_finish": date(2026, 10, 2) if pct == 100 else None, "note": "Registro de ejemplo"} for i, (pct, hh, r) in enumerate(zip([100, 50, 0], [16, 8, 0], bundle.resources), 1)]
    return bundle


def _build(path: str | Path, mode: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = make_workbook(example_bundle(mode), temp_dir=target.parent)
    try:
        workbook.save(target)
    finally:
        close_workbook(workbook)
    return target


def build_project_template(path: str | Path) -> Path:
    """Genera la plantilla de proyecto con ejemplos y validaciones."""
    return _build(path, "proyecto")


def build_maintenance_template(path: str | Path) -> Path:
    """Genera la plantilla de mantenimiento con ejemplos y validaciones."""
    return _build(path, "mantenimiento")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Genera plantillas Excel de Ganttier.')
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[3] / 'plantillas')
    folder = parser.parse_args().output_dir
    for builder, filename in ((build_project_template, "Plantilla_Proyecto.xlsx"), (build_maintenance_template, "Plantilla_Mantenimiento.xlsx")):
        print(builder(folder / filename))
