"""Pruebas de archivos reales, validación e ida y vuelta."""
from datetime import date
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from ganttier.excel_io import build_project_template, build_maintenance_template, parse_workbook, export_project


@pytest.mark.parametrize("builder,mode", [(build_project_template, "proyecto"), (build_maintenance_template, "mantenimiento")])
def test_templates_and_roundtrip(tmp_path, builder, mode):
    path = builder(tmp_path / "template.xlsx")
    bundle = parse_workbook(path)
    assert not bundle.issues, bundle.issues
    assert bundle.mode == mode
    assert bundle.progress[0]["percent_complete"] == 100
    assert bundle.calendars[0]["exceptions"][0]["working"] is True
    again = parse_workbook(export_project(bundle, tmp_path / "export.xlsx"))
    assert not again.issues
    assert bundle == again
    workbook = load_workbook(path)
    assert workbook["Listas"].sheet_state == "hidden"
    assert workbook["Recursos"].freeze_panes
    assert len(workbook["Recursos"].data_validations.dataValidation) > 2
    assert workbook["Recursos"]["A1"].comment
    workbook.close()


@pytest.mark.parametrize("number_format,value", [('0.0" %"', 50), ('0.0%', .5), ('0.0\\%', 50)])
def test_percentage_formats(tmp_path, number_format, value):
    path = build_project_template(tmp_path / "percentage.xlsx")
    workbook = load_workbook(path)
    workbook["Avance"]["C3"] = value
    workbook["Avance"]["C3"].number_format = number_format
    workbook.save(path)
    workbook.close()
    bundle = parse_workbook(path)
    assert bundle.progress[1]["percent_complete"] == 50


def test_reordered_headers_locale_and_dates(tmp_path):
    path = build_project_template(tmp_path / "locale.xlsx")
    workbook = load_workbook(path)
    sheet = workbook["Actividades"]
    columns = list(zip(*list(sheet.values)))
    sheet.delete_rows(1, sheet.max_row)
    for row in zip(*reversed(columns)):
        sheet.append(row)
    positions = {c.value: c.column for c in sheet[1]}
    sheet.cell(1, positions["Codigo"], "CÓDIGO")
    sheet.cell(1, positions["Duracion_Dias"], "duración días")
    sheet.cell(2, positions["HH"], "16,5")
    sheet.cell(2, positions["Requiere_Desconexion"], "Sí")
    sheet.cell(2, positions["Ventana_Inicio"], "01/10/2026")
    sheet.cell(2, positions["Ventana_Fin"], "31-10-2026")
    workbook.save(path)
    workbook.close()
    bundle = parse_workbook(path)
    assert not bundle.issues
    activity = bundle.activities[0]
    assert activity["work_hours"] == 16.5
    assert activity["requires_outage"] is True
    assert activity["window_start"] == date(2026, 10, 1)
    assert activity["window_end"] == date(2026, 10, 31)


@pytest.mark.parametrize("sheet,row,header,value,fragment", [
    ("Actividades", 3, "Codigo", "A001", "duplicado"),
    ("Actividades", 2, "Codigo_WBS", "FALTA", "inexistente"),
    ("Relaciones", 2, "Predecesora", "FALTA", "inexistente"),
    ("Asignaciones", 2, "Recurso", "FALTA", "inexistente"),
    ("Recursos", 2, "Calendario", "FALTA", "inexistente"),
    ("Actividades", 2, "Ventana_Inicio", "31/02/2026", "Fecha inválida"),
    ("Actividades", 2, "Duracion_Dias", -1, "rango"),
    ("Actividades", 2, "Prioridad", 101, "rango"),
    ("Actividades", 2, "Duracion_Dias", 1.5, "entero"),
    ("Actividades", 2, "HH", "NaN", "finito"),
    ("Actividades", 2, "Mes_Macro", "2026-13", "inexistente"),
    ("Actividades", 2, "Requiere_Desconexion", "tal vez", "SI/NO"),
    ("Actividades", 2, "Restriccion", "MSO", "requiere"),
    ("Relaciones", 2, "Tipo", "XX", "inválido"),
    ("Calendarios", 2, "Semana_Laboral", "11111", "siete"),
    ("Calendarios", 2, "Turno", "7x7", "anclaje"),
    ("WBS", 2, "Codigo_Padre", "SE.P", "ciclo"),
])
def test_injected_errors(tmp_path, sheet, row, header, value, fragment):
    path = build_project_template(tmp_path / "bad.xlsx")
    workbook = load_workbook(path)
    worksheet = workbook[sheet]
    column = next(c.column for c in worksheet[1] if c.value == header)
    worksheet.cell(row, column, value)
    workbook.save(path)
    workbook.close()
    bundle = parse_workbook(path)
    assert bundle.has_errors
    assert any(i.sheet == sheet and fragment in i.message for i in bundle.issues), bundle.issues
    assert all(i.row >= 1 and i.field for i in bundle.issues)


def test_domain_extension_roundtrip_and_formula_text(tmp_path):
    source = parse_workbook(build_project_template(tmp_path / "source.xlsx"))
    source.activities[0].update(planned_start=date(2026, 10, 1), planned_finish=date(2026, 10, 5), total_float=-3, critical=True, percent_complete=33, remaining_duration=2, locked=True, notes='=HYPERLINK("https://example.com","literal")')
    source.wbs[0]["sort_order"] = 4
    result = parse_workbook(export_project(source, tmp_path / "result.xlsx"))
    assert not result.issues
    assert result == source
    workbook = load_workbook(tmp_path / "result.xlsx")
    assert not any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row)
    workbook.close()


def test_missing_sheet_and_unreadable_file(tmp_path):
    file = tmp_path / "bad.xlsx"
    file.write_text("No es un archivo Excel", encoding="utf-8")
    assert parse_workbook(file).has_errors
    path = build_project_template(tmp_path / "missing.xlsx")
    workbook = load_workbook(path)
    del workbook["Proyecto"]
    workbook.save(path)
    workbook.close()
    assert any(i.sheet == "Proyecto" for i in parse_workbook(path).issues)


def test_mode_conflict_and_maintenance_occurrence(tmp_path):
    path = build_maintenance_template(tmp_path / "maintenance.xlsx")
    assert parse_workbook(path, mode="proyecto").has_errors
    assert parse_workbook(path, mode="invalid").has_errors
    workbook = load_workbook(path)
    workbook["Avance"]["A2"] = "DESCONOCIDA-0001"
    workbook["Plan_Maestro"]["C2"] = "BAD"
    workbook.save(path)
    workbook.close()
    bundle = parse_workbook(path)
    assert any(i.sheet == "Avance" for i in bundle.issues)
    assert any(i.sheet == "Plan_Maestro" for i in bundle.issues)


def test_dates_and_relation_advance(tmp_path):
    path = build_project_template(tmp_path / "dates.xlsx")
    workbook = load_workbook(path)
    workbook["Relaciones"]["D2"] = -2
    sheet = workbook["Actividades"]
    positions = {cell.value: cell.column for cell in sheet[1]}
    sheet.cell(2, positions["Ventana_Inicio"], date(2026, 10, 31))
    sheet.cell(2, positions["Ventana_Fin"], date(2026, 10, 1))
    workbook.save(path)
    workbook.close()
    bundle = parse_workbook(path)
    assert bundle.relations[0]["lag_days"] == -2
    assert any("anterior" in i.message for i in bundle.issues)


def test_broken_xml_returns_issue(tmp_path):
    path = build_project_template(tmp_path / "xml.xlsx")
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    name = "xl/worksheets/sheet4.xml"
    entries[name] = entries[name].replace(b"</worksheet>", b"<invalid></worksheet>")
    with ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    bundle = parse_workbook(path)
    assert bundle.has_errors
    assert any(i.sheet == "Libro" for i in bundle.issues)


@pytest.mark.parametrize("builder", [build_project_template, build_maintenance_template])
def test_plantillas_y_exportacion_no_requieren_reparacion_en_excel(tmp_path, builder):
    """D01: una hoja con tabla no puede declarar además su propio autoFilter."""
    from ganttier.excel_io.compat import structure_problems
    path = builder(tmp_path / "template.xlsx")
    assert structure_problems(path) == []
    bundle = parse_workbook(path)
    assert structure_problems(export_project(bundle, tmp_path / "export.xlsx")) == []
