"""Aceptación P5-01 leyendo el archivo Excel, sin usar la GUI."""
from datetime import date, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from ganttier import db, repo
from ganttier.api.dependencies import connection
from ganttier.main import create_app
from ganttier.reports.weekly_roster import weekly_roster


@pytest.fixture
def conn():
    value = db.connect(":memory:")
    db.initialize(value)
    yield value
    value.close()


def make(conn, table, **values):
    return repo.create_many(conn, table, [values])[0]


@pytest.fixture
def example(conn):
    cal = make(conn, "calendars", name="Lunes a viernes", holidays_country=None)
    rest = make(conn, "calendars", name="Descanso martes", holidays_country=None,
                exceptions=[dict(date="2026-10-06", working=False, reason="Descanso")])
    project = make(conn, "projects", name="Pruebas S/E", start_date="2026-09-01", calendar_id=cal.id)
    ana = make(conn, "resources", name="Ana", project_id=project.id, calendar_id=rest.id)
    namesake = make(conn, "resources", name="Ana", project_id=project.id)
    crew = make(conn, "resources", name="Cuadrilla S/E", kind="cuadrilla", project_id=project.id, max_units=4)
    make(conn, "resources", name="Sin tarea", project_id=project.id, skills=["protecciones"])
    acts = {}
    for code, start, finish in [("A", "2026-10-05", "2026-10-06"), ("B", "2026-10-07", "2026-10-07"),
                                ("C", "2026-10-08", "2026-10-08"), ("D", "2026-10-02", "2026-10-05"),
                                ("E", "2026-10-09", "2026-10-09")]:
        acts[code] = make(conn, "activities", project_id=project.id, code=code, name="Actividad " + code,
                          planned_start=start, planned_finish=finish, work_hours=16, location="S/E Norte")
    for code, resource, hours in [("A", ana, 16), ("B", ana, 8), ("D", namesake, 16), ("E", crew, 32)]:
        make(conn, "assignments", activity_id=acts[code].id, resource_id=resource.id, work_hours=hours)
    make(conn, "assignments", activity_id=acts["C"].id, skill="protecciones", work_hours=16)
    return project, ana, namesake, crew, acts


def workbook(conn, pid, day=date(2026, 10, 5)):
    return load_workbook(BytesIO(weekly_roster(conn, pid, day, extracted_at=datetime(2026, 10, 3, 12))))


def sections(sheet):
    rows = list(sheet.values)
    crew = next(i for i, row in enumerate(rows) if row[0] == "Cuadrillas")
    unassigned = next(i for i, row in enumerate(rows) if row[0] == "Sin asignación nominal")
    return rows[1:crew], rows[crew + 2:unassigned], rows[unassigned + 2:]


def test_acceptance_nominal_matrix_detail_and_no_changes(conn, example):
    project, ana, namesake, crew, acts = example
    before = "\n".join(conn.iterdump())
    changes = conn.total_changes
    book = workbook(conn, project.id)
    assert book.sheetnames == ["Plan semanal", "Detalle"]
    nominal, crews, generic = sections(book["Plan semanal"])
    workers = {row[0]: row for row in nominal if isinstance(row[0], int)}
    assert set(workers) == {ana.id, namesake.id}
    assert workers[ana.id][1] == workers[namesake.id][1] == "Ana"
    assert workers[ana.id][2] == "A · Actividad A (S/E Norte)"
    assert "A · Actividad A (S/E Norte)" in workers[ana.id][3]
    assert "(no disponible)" in workers[ana.id][3]
    assert workers[ana.id][4] == "B · Actividad B (S/E Norte)"
    assert workers[namesake.id][2] == "D · Actividad D (S/E Norte)"
    assert all("Actividad C" not in str(row) for row in nominal + crews)
    assert any("C · Actividad C (S/E Norte)" in str(row) for row in generic)
    assert any(row[0] == crew.id and "E · Actividad E (S/E Norte)" in str(row) for row in crews)
    assert any("E · Actividad E (S/E Norte)" in str(row) for row in generic)
    details = list(book["Detalle"].values)[1:]
    ana_rows = [r for r in details if r[0] == ana.id]
    assert [(r[2].date(), r[3], r[6]) for r in ana_rows] == [
        (date(2026, 10, 5), "A", 8), (date(2026, 10, 6), "A", 8), (date(2026, 10, 7), "B", 8)]
    assert ana_rows[1][9] == "(no disponible)"
    assert next(r for r in details if r[3] == "D")[6] == 8  # No asigna las 16 HH a esta semana.
    assert len(details) == 6
    assert conn.total_changes == changes and "\n".join(conn.iterdump()) == before
    book.close()


def test_xlsx_formatting_and_monday_normalization(conn, example):
    project = example[0]
    book = workbook(conn, project.id, date(2026, 10, 11))
    plan, detail = book["Plan semanal"], book["Detalle"]
    assert plan["C1"].value == "Lunes\n05-10-2026"
    assert plan["I1"].value == "Domingo\n11-10-2026"
    for sheet in book:
        assert all(cell.font.bold for cell in sheet[1])
        assert sheet.auto_filter.ref
        assert sheet.print_title_rows == "$1:$1"
        assert "03-10-2026" in sheet.oddFooter.left.text
        assert sheet.column_dimensions["B"].width >= 14
    assert detail["C2"].number_format == "dd-mm-yyyy"
    assert detail["G2"].number_format == "0.00"
    book.close()


def test_activity_calendar_early_dates_and_units_fallback(conn, example):
    project, ana, _, _, _ = example
    special = make(conn, "calendars", name="Actividad especial", holidays_country=None,
                   exceptions=[dict(date="2026-10-07", working=False, reason="Sin acceso")])
    activity = make(conn, "activities", project_id=project.id, code="F", name="Respaldo temprano",
                    early_start="2026-10-06", early_finish="2026-10-08", calendar_id=special.id)
    make(conn, "assignments", activity_id=activity.id, resource_id=ana.id, work_hours=0, units=2)
    book = workbook(conn, project.id)
    rows = [r for r in list(book["Detalle"].values)[1:] if r[3] == "F"]
    assert [(r[2].day, r[6]) for r in rows] == [(6, 16), (8, 16)]
    assert "(no disponible)" in rows[0][9]
    book.close()


def test_http_download_and_empty_week(conn, example):
    app = create_app()
    app.dependency_overrides[connection] = lambda: conn
    client = TestClient(app)
    response = client.get(f"/api/projects/{example[0].id}/reports/weekly-roster",
                           params={"week_start": "2026-10-07"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert 'filename="plan_semanal_2026-10-05.xlsx"' in response.headers["content-disposition"]
    book = load_workbook(BytesIO(response.content))
    assert "Detalle" in book.sheetnames
    book.close()
    empty = workbook(conn, example[0].id, date(2027, 1, 4))
    assert empty["Detalle"].max_row == 1
    empty.close()
    assert client.get("/api/projects/999999/reports/weekly-roster", params={"week_start": "2026-10-05"}).status_code == 404


@pytest.mark.parametrize("kind", ["workers", "activities"])
def test_actual_limits_return_422_without_truncation(conn, kind):
    project = make(conn, "projects", name="Límites", start_date="2026-10-05")
    count = 1001 if kind == "activities" else 1
    acts = repo.create_many(conn, "activities", [dict(project_id=project.id, code=f"A{i}", name="Actividad",
                                                    planned_start="2026-10-05", planned_finish="2026-10-05")
                                                for i in range(count)])
    if kind == "workers":
        workers = repo.create_many(conn, "resources", [dict(name=f"Persona {i}", project_id=project.id) for i in range(251)])
        repo.create_many(conn, "assignments", [dict(activity_id=acts[0].id, resource_id=r.id) for r in workers])
    app = create_app()
    app.dependency_overrides[connection] = lambda: conn
    response = TestClient(app).get(f"/api/projects/{project.id}/reports/weekly-roster", params={"week_start": "2026-10-05"})
    assert response.status_code == 422
    assert ("250 trabajadores" if kind == "workers" else "1.000 actividades") in response.json()["detail"]


def test_external_projects_unscheduled_and_literal_excel_text(conn, example):
    project, ana, _, _, _ = example
    other = make(conn, "projects", name="Otro", start_date="2026-10-05")
    make(conn, "activities", project_id=other.id, code="EXTERNA", name="Ajena",
         planned_start="2026-10-05", planned_finish="2026-10-05", work_hours=8)
    make(conn, "activities", project_id=project.id, code="SINFECHA", name="No programada", work_hours=8)
    literal = make(conn, "activities", project_id=project.id, code="=1+1", name="Texto literal",
                   planned_start="2026-10-05", planned_finish="2026-10-05", work_hours=8)
    make(conn, "assignments", activity_id=literal.id, resource_id=ana.id, work_hours=8)
    book = workbook(conn, project.id)
    for sheet in book:
        assert "EXTERNA" not in str(list(sheet.values))
        assert "SINFECHA" not in str(list(sheet.values))
        assert all(c.data_type != "f" for row in sheet for c in row)
    assert any(c.value == "=1+1" for row in book["Detalle"] for c in row)
    book.close()
