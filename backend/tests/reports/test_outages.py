"""Contrato HTTP, Excel y escala del programa de desconexiones; sin BD de usuario."""
from datetime import date
from io import BytesIO
from time import perf_counter
import tempfile

import pytest
from openpyxl import load_workbook

from ganttier import db, repo, services
from ganttier.reports.outages import NOTE, PROGRAM_HEADERS, TITLE, build_program, xlsx_bytes


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "outages.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(services, "DATA_DIR", tmp_path)
    # También los XML temporales de openpyxl quedan en el directorio de pytest.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    conn = db.connect()
    db.initialize(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def environment(database):
    pytest.importorskip("fastapi", reason="La integración HTTP requiere FastAPI instalado")
    pytest.importorskip("httpx", reason="TestClient requiere HTTPX instalado")
    from fastapi.testclient import TestClient
    from ganttier.main import create_app

    with TestClient(create_app()) as client:
        yield client, database


def project(conn, mode="mantenimiento"):
    return repo.create(conn, "projects", {
        "name": "Mantenimiento S/E Sur", "mode": mode,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }).id


def activity(conn, pid, code, **values):
    return repo.create(conn, "activities", {
        "project_id": pid, "code": code, "name": f"Trabajo {code}",
        "location": "S/E Sur", "equipment": f"Equipo {code}", "requires_outage": True,
        "planned_start": "2026-01-30", "planned_finish": "2026-01-30",
        "work_hours": 8.0, "voltage_kv": 66, **values,
    })


@pytest.fixture
def sample(environment):
    client, conn = environment
    pid = project(conn)
    a = activity(conn, pid, "A", planned_finish="2026-02-02", voltage_kv=220,
                 early_start="2025-12-01", early_finish="2025-12-02", work_hours=16)
    activity(conn, pid, "B", work_hours=12)
    activity(conn, pid, "C", location="S/E Norte", voltage_kv=None,
             planned_start=None, planned_finish=None, early_start="2026-02-03",
             early_finish="2026-02-03")
    activity(conn, pid, "D", planned_start="2026-02-10", planned_finish="2026-02-11")
    activity(conn, pid, "LIVE", requires_outage=False)
    activity(conn, pid, "UNSCHEDULED", planned_start=None, planned_finish=None,
             due_date="2026-01-30", actual_start="2026-01-01", actual_finish="2026-01-02")
    activity(conn, pid, "PARTIAL", planned_finish=None)
    other = project(conn, "proyecto")
    activity(conn, other, "OTHER", work_hours=9000)
    ana = repo.create(conn, "resources", {"project_id": pid, "name": "Ana", "kind": "trabajador"})
    luis = repo.create(conn, "resources", {"name": "Luis", "kind": "trabajador", "active": False})
    crew = repo.create(conn, "resources", {"project_id": pid, "name": "Cuadrilla SSEE", "kind": "cuadrilla"})
    machine = repo.create(conn, "resources", {"project_id": pid, "name": "Grúa", "kind": "equipo"})
    repo.create_many(conn, "assignments", [
        {"activity_id": a.id, "resource_id": r, "work_hours": 999}
        for r in (ana.id, ana.id, luis.id, crew.id, machine.id, None)
    ])
    return client, conn, pid


def test_json_groups_assignments_dates_and_totals(sample):
    client, _, pid = sample
    response = client.get(f"/api/projects/{pid}/outages")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["title"] == TITLE and data["note"] == NOTE
    assert data["excluded_activity_count"] == 2
    assert len(data["groups"]) == 3
    first, second, third = data["groups"]
    assert first["location"] == "S/E Sur" and first["voltage_kv"] == 220
    assert (first["start"], first["finish"], first["duration_days"]) == ("2026-01-30", "2026-02-02", 4)
    assert first["grouped"] is True and first["activity_count"] == 2
    assert first["work_hours"] == 28  # Nunca se suman HH de asignaciones duplicadas.
    assert [a["code"] for a in first["activities"]] == ["A", "B"]
    assert [w["name"] for w in first["activities"][0]["workers"]] == ["Ana", "Luis", "Cuadrilla SSEE"]
    assert first["activities"][1]["workers"] == []
    assert second["voltage_kv"] is None and second["duration_days"] == 1
    assert second["start"] == "2026-02-03" and not third["grouped"]
    summary = data["summary"]
    assert summary["outage_count"] == 3 and summary["activity_count"] == 4
    assert summary["average_activities_per_outage"] == pytest.approx(4 / 3)
    assert summary["work_hours"] == 44 and summary["grouped_activity_count"] == 2
    assert [(r["month"], r["work_hours"]) for r in summary["by_month"]] == [("2026-01", 28), ("2026-02", 16)]
    assert sum(r["activity_count"] for r in summary["by_voltage"]) == 4
    assert summary["by_voltage"][-1]["voltage_kv"] is None


@pytest.mark.parametrize("params,codes", [
    ({"from": "2026-02-02", "to": "2026-02-02"}, ["A", "B"]),
    ({"to": "2026-01-30"}, ["A", "B"]),
    ({"from": "2026-02-03"}, ["C", "D"]),
    ({"from": "2027-01-01"}, []),
])
def test_overlap_keeps_whole_outages_in_both_formats(sample, params, codes):
    client, _, pid = sample
    data = client.get(f"/api/projects/{pid}/outages", params=params).json()
    assert [a["code"] for g in data["groups"] for a in g["activities"]] == codes
    response = client.get(f"/api/projects/{pid}/outages.xlsx", params=params)
    assert response.status_code == 200
    with BytesIO(response.content) as stream:
        book = load_workbook(stream)
        assert [row[3] for row in book["Detalle"].iter_rows(min_row=2, values_only=True)] == codes
        book.close()


@pytest.mark.parametrize("suffix", ["outages", "outages.xlsx"])
def test_validation_missing_and_empty_projects(environment, suffix):
    client, conn = environment
    pid = project(conn, "proyecto")
    url = f"/api/projects/{pid}/{suffix}"
    for params in ({"from": "2026-12-01", "to": "2026-01-01"}, {"from": "no-fecha"}, {"to": "2026-02-30"}):
        response = client.get(url, params=params)
        assert response.status_code == 422 and "detail" in response.json()
    assert client.get(f"/api/projects/999999/{suffix}").status_code == 404
    response = client.get(url)
    assert response.status_code == 200
    if suffix == "outages":
        assert response.json()["groups"] == []
        assert response.json()["summary"]["average_activities_per_outage"] == 0
    else:
        book = load_workbook(BytesIO(response.content))
        assert book["Programa"].max_row == book["Detalle"].max_row == 1
        book.close()


def test_workbook_layout_and_values(sample):
    client, _, pid = sample
    response = client.get(f"/api/projects/{pid}/outages.xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert f'desconexiones-{pid}.xlsx' in response.headers["content-disposition"]
    book = load_workbook(BytesIO(response.content))
    assert book.sheetnames == ["Resumen", "Programa", "Detalle"]
    summary, schedule, detail = (book[name] for name in book.sheetnames)
    assert summary["A1"].value == TITLE and summary["A2"].value == NOTE
    assert [c.value for c in schedule[1]] == list(PROGRAM_HEADERS)
    assert schedule.max_row == 4 and detail.max_row == 5
    assert schedule["D2"].value.date() == date(2026, 1, 30)
    assert schedule["E2"].value.date() == date(2026, 2, 2)
    assert schedule["F2"].value == 4 and schedule["J2"].value == 28
    assert schedule["K2"].value == "Ana; Luis; Cuadrilla SSEE"
    assert schedule["L2"].value is None
    assert schedule["L2"].fill.fgColor.rgb.endswith("FFF2CC")
    assert schedule["D2"].number_format == detail["G2"].number_format == "dd-mm-yyyy"
    assert schedule.freeze_panes == detail.freeze_panes == "C2"
    assert schedule.auto_filter.ref == "A1:L4" and detail.auto_filter.ref == "A1:L5"
    assert schedule["A1"].font.bold and schedule.column_dimensions["I"].width >= 50
    assert summary["B11"].value == 3 and summary["B16"].value == 44
    book.close()


def test_project_mode_mixed_fallback_missing_location_and_literal_text(environment):
    client, conn = environment
    pid = project(conn, "proyecto")
    text = '=HYPERLINK("https://example.invalid", "texto")'
    activity(conn, pid, "=1+1", name=text, location="", equipment="@TAG",
             planned_finish=None, early_finish="2026-01-31", voltage_kv=0)
    activity(conn, pid, "SIN", requires_outage=False)
    data = client.get(f"/api/projects/{pid}/outages").json()
    assert data["groups"][0]["voltage_kv"] == 0
    assert data["groups"][0]["finish"] == "2026-01-31"
    assert data["groups"][0]["location"] == "" and data["summary"]["activity_count"] == 1
    response = client.get(f"/api/projects/{pid}/outages.xlsx")
    book = load_workbook(BytesIO(response.content))
    assert book["Detalle"]["D2"].value == "=1+1"
    assert book["Detalle"]["E2"].value == text
    assert book["Detalle"]["E2"].data_type == book["Detalle"]["D2"].data_type == "s"
    book.close()


def test_demo_and_constant_query_count(database):
    conn = database
    pid = services.demo_maintenance(300, conn).id
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        program = build_program(conn, pid)
    finally:
        conn.set_trace_callback(None)
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 4
    expected = conn.execute("SELECT COUNT(*) FROM activities WHERE project_id=? AND requires_outage=1", (pid,)).fetchone()[0]
    assert program["summary"]["activity_count"] + program["excluded_activity_count"] == expected


def test_direct_report_readonly_dates_assignments_and_workbook(database):
    conn = database
    pid = project(conn)
    a = activity(conn, pid, "A", planned_finish="2026-02-02", voltage_kv=220, work_hours=16,
                 early_start="2025-01-01", early_finish="2025-01-02")
    activity(conn, pid, "B", voltage_kv=None, work_hours=12)
    activity(conn, pid, "C", planned_start=None, planned_finish=None,
             early_start="2026-02-03", early_finish="2026-02-03")
    activity(conn, pid, "U", planned_start=None, planned_finish=None, due_date="2026-01-30")
    activity(conn, pid, "I", planned_finish=None, early_finish="2025-01-01")
    activity(conn, pid, "L", requires_outage=False)
    worker = repo.create(conn, "resources", {"name": "Ana"})
    repo.create_many(conn, "assignments", [{"activity_id": a.id, "resource_id": worker.id, "work_hours": 900} for _ in range(2)])
    changes = conn.total_changes
    data = build_program(conn, pid, date(2026, 2, 2), date(2026, 2, 2))
    assert conn.total_changes == changes
    assert data["excluded_activity_count"] == 2
    group, = data["groups"]
    assert group["duration_days"] == 4 and group["voltage_kv"] == 220
    assert group["work_hours"] == 28 and group["activity_count"] == 2 and group["grouped"]
    assert [w["name"] for w in group["activities"][0]["workers"]] == ["Ana"]
    assert data["summary"]["by_month"][0]["month"] == "2026-01"
    book = load_workbook(BytesIO(xlsx_bytes(data)))
    assert book.sheetnames == ["Resumen", "Programa", "Detalle"]
    assert book["Resumen"]["A1"].value == TITLE and book["Resumen"]["A2"].value == NOTE
    assert book["Resumen"]["B16"].value == 28
    ws = book["Programa"]
    assert [c.value for c in ws[1]] == list(PROGRAM_HEADERS)
    assert ws["D2"].value.date() == date(2026, 1, 30)
    assert ws["D2"].number_format == "dd-mm-yyyy"
    assert ws.auto_filter.ref == "A1:L2" and ws.freeze_panes == "C2"
    assert ws["L2"].value is None and ws["L2"].fill.fgColor.rgb.endswith("FFF2CC")
    assert book["Detalle"].max_row == 3
    book.close()
    with pytest.raises(ValueError, match="desde"):
        build_program(conn, pid, date(2026, 2, 1), date(2026, 1, 1))
    with pytest.raises(LookupError):
        build_program(conn, 999999)


def test_direct_large_group_empty_report_and_literal_text(database):
    conn = database
    pid = project(conn, "proyecto")
    empty = build_program(conn, pid)
    assert empty["groups"] == [] and empty["summary"]["average_activities_per_outage"] == 0
    book = load_workbook(BytesIO(xlsx_bytes(empty)))
    assert book["Programa"].max_row == book["Detalle"].max_row == 1
    book.close()
    repo.create_many(conn, "activities", [{
        "project_id": pid, "code": f"={i}", "name": "=1+1", "equipment": f"TAG-{i:05d}",
        "location": "", "requires_outage": True, "planned_start": "2026-01-01",
        "planned_finish": "2026-01-01", "work_hours": 0,
    } for i in range(4000)], return_entities=False)
    data = build_program(conn, pid)
    assert data["groups"][0]["voltage_kv"] is None
    assert data["summary"]["activity_count"] == 4000
    book = load_workbook(BytesIO(xlsx_bytes(data)))
    assert "lista abreviada" in book["Programa"]["I2"].value
    assert book["Detalle"].max_row == 4001
    assert book["Detalle"]["D2"].value == "=0" and book["Detalle"]["D2"].data_type == "s"
    assert book["Detalle"]["E2"].value == "=1+1" and book["Detalle"]["E2"].data_type == "s"
    book.close()


@pytest.mark.slow
@pytest.mark.parametrize("transport", ["direct", "http"])
def test_100000_activities_export(database, request, transport):
    """100 000 trabajos elegibles; 10 000 desconexiones y Detalle completo."""
    conn = database
    client = request.getfixturevalue("environment")[0] if transport == "http" else None
    pid = project(conn)
    for offset in range(0, 100000, 2000):
        repo.create_many(conn, "activities", [
            {"project_id": pid, "code": f"A{i:06d}", "name": "Mantenimiento interruptor",
             "location": f"S/E {i // 10:05d}", "equipment": f"52-{i}", "requires_outage": True,
             "planned_start": "2026-01-05", "planned_finish": "2026-01-05",
             "work_hours": 8, "voltage_kv": 220}
            for i in range(offset, offset + 2000)
        ], return_entities=False)
    begin = perf_counter()
    if client:
        response = client.get(f"/api/projects/{pid}/outages")
        assert response.status_code == 200
        data = response.json()
        del response
    else:
        data = repo.json_value(build_program(conn, pid))
    assert data["summary"]["activity_count"] == 100000
    assert data["summary"]["outage_count"] == 10000
    assert data["summary"]["work_hours"] == 800000
    json_seconds = perf_counter() - begin
    del data
    begin = perf_counter()
    if client:
        response = client.get(f"/api/projects/{pid}/outages.xlsx")
        assert response.status_code == 200
        content = response.content
    else:
        content = xlsx_bytes(build_program(conn, pid))
    xlsx_seconds = perf_counter() - begin
    book = load_workbook(BytesIO(content), read_only=True, data_only=True)
    assert sum(1 for _ in book["Detalle"].iter_rows()) == 100001
    assert sum(1 for _ in book["Programa"].iter_rows()) == 10001
    assert book["Resumen"]["B16"].value == 800000
    book.close()
    print(f"100000 actividades ({transport}): JSON {json_seconds:.2f}s; XLSX {xlsx_seconds:.2f}s; {len(content)} bytes")
