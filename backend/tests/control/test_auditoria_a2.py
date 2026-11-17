"""Regresiones A2: estado correcto, persistencia atómica y coherencia del control."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from ganttier import autoreports, db, domain as d, repo, services
from ganttier.api import control, schemas
from ganttier.api.dependencies import connection, validated_update
from ganttier.calendars import WorkCalendar
from ganttier.control import evm, worker_performance
from ganttier.excel_io import export_project, parse_workbook
from ganttier.main import create_app


def day(n):
    return date(2026, 1, n)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.initialize(c)
    yield c
    c.close()


@pytest.fixture
def project(conn):
    cal = repo.create(conn, "calendars", dict(name="A2 L-V", workweek="1111100", holidays_country=None))
    return repo.create(conn, "projects", dict(name="A2", start_date=day(1), data_date=day(9), calendar_id=cal.id))


def activity(conn, project, **changes):
    values = dict(project_id=project.id, code="A", name="Actividad", planned_start=day(5),
                  planned_finish=day(9), duration_days=5, work_hours=40, cost_budget=400)
    values.update(changes)
    return repo.create(conn, "activities", values)


def post_progress(conn, project, act, n, percent, **changes):
    body = dict(activity_id=act.id, date=day(n), percent_complete=percent)
    body.update(changes)
    return control.create_progress(project.id, schemas.ProgressEntry(**body), conn)


@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(services, "DATA_DIR", tmp_path)
    app = create_app()
    app.dependency_overrides[connection] = lambda: conn
    # Sin lifespan: no se abre la BD real ni se inicia el scheduler.
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    client.close()


def test_a2_reopened_activity_clears_obsolete_finish(conn, project):
    act = activity(conn, project)
    post_progress(conn, project, act, 7, 100, actual_hours=40)
    post_progress(conn, project, act, 8, 50)  # Correccion descendente admitida.
    view = services.build_activity_views(conn, project.id)[0]
    assert view.percent_complete == 50 and view.actual_finish is None
    result = services.control_result(conn, project.id, "evm")
    assert result.EV == 20
    assert result.percent_complete == 50


def test_a2_activity_snapshot_consumption_included_in_control(conn, project, client):
    response = client.post(f"/api/projects/{project.id}/activities", json=dict(
        code="A", name="Actividad", planned_start="2026-01-05", planned_finish="2026-01-09",
        duration_days=5, work_hours=40, cost_budget=400, actual_start="2026-01-05",
        percent_complete=50, actual_hours=30, actual_cost=300))
    assert response.status_code == 201, response.text
    assert services.build_activity_views(conn, project.id)[0].actual_hours == 30
    assert services.control_result(conn, project.id, "evm").AC == 30
    assert services.control_result(conn, project.id, "evm", basis="cost").AC == 300


def test_a2_activity_calendar_used_by_pv(conn, project):
    seven = repo.create(conn, "calendars", dict(name="A2 siete dias", workweek="1111111", holidays_country=None))
    activity(conn, project, planned_start=day(9), planned_finish=day(12), duration_days=4,
             work_hours=32, calendar_id=seven.id)
    actual = services.control_result(conn, project.id, "evm")
    view = services.build_activity_views(conn, project.id)
    own_calendar = WorkCalendar(seven, day(1), date(2027, 1, 1))
    oracle = evm(view, None, [], own_calendar, day(9))
    assert oracle.PV == 8  # Viernes: uno de cuatro dias laborables, 8 HH/dia.
    assert actual.PV == 8


def test_a2_worker_spi_excludes_nonworking_weekend(conn, project):
    act = activity(conn, project, planned_start=day(9), planned_finish=day(12), duration_days=2, work_hours=16)
    worker = repo.create(conn, "resources", dict(project_id=project.id, name="Ana"))
    repo.create(conn, "assignments", dict(activity_id=act.id, resource_id=worker.id, work_hours=16))
    post_progress(conn, project, act, 9, 50, actual_hours=8, resource_id=worker.id)
    result = services.control_result(conn, project.id, "performance", start=day(9), end=day(12))[0]
    assert services.control_result(conn, project.id, "evm").SPI == 1
    assert result.SPI == 1
    saturday = services.control_result(conn, project.id, "performance", start=day(10), end=day(11))[0]
    assert saturday.planned_hours == 0


def test_a2_equipment_preserves_worker_earned_and_untagged_hours(conn, project):
    act = activity(conn, project, duration_days=1, planned_finish=day(5), work_hours=8)
    worker = repo.create(conn, "resources", dict(project_id=project.id, name="Ana"))
    equipment = repo.create(conn, "resources", dict(project_id=project.id, name="Grua", kind="equipo"))
    for resource in (worker, equipment):
        repo.create(conn, "assignments", dict(activity_id=act.id, resource_id=resource.id, units=1))
    post_progress(conn, project, act, 5, 100, actual_hours=8)
    result = services.control_result(conn, project.id, "performance", start=day(5), end=day(9))[0]
    assert result.planned_hours == 8
    assert result.earned_hours == result.actual_hours == 8


@pytest.mark.parametrize("kind,first,later,omitted", [
    ("weekly", datetime(2026, 12, 28), datetime(2027, 1, 11), "weekly:2026-12-28"),
    ("monthly", datetime(2026, 12, 1), datetime(2027, 2, 1), "monthly:2026-12-01"),
    ("annual", datetime(2026, 1, 1), datetime(2028, 1, 1), "annual:2026-01-01"),
])
def test_a2_scheduler_recovers_periods_after_downtime(conn, project, monkeypatch, kind, first, later, omitted):
    calls = []
    def render(c, pid, period, start, formats):
        calls.append((period, start))
        return [dict(format=formats[0], filename=f"proyecto-{pid}-{period}-{start}.html")]
    monkeypatch.setattr(services, "render_reports", render)
    config = dict(enabled=True, formats=["html"], weekly=False, monthly=False, annual=False)
    config[kind] = True
    autoreports.set_config(conn, project.id, config)
    autoreports.run_project_reports(conn, project.id, first)
    autoreports.run_project_reports(conn, project.id, later)
    log = autoreports.get_log(conn, project.id)
    assert len(calls) == 3
    assert omitted in {row["key"] for row in log}
    assert autoreports.run_project_reports(conn, project.id, later) == []  # No es duplicacion.


def test_a2_delete_periodic_task_protects_executed_history(conn, project, client):
    task = repo.create(conn, "periodic_tasks", dict(project_id=project.id, code="T", name="Tarea"))
    act = activity(conn, project, periodic_task_id=task.id)
    post_progress(conn, project, act, 7, 100, actual_hours=40, actual_cost=400)
    response = client.delete(f"/api/periodic-tasks/{task.id}")
    assert response.status_code == 409
    assert len(repo.list_entities(conn, "activities", project.id)) == 1
    assert len(repo.list_entities(conn, "progress", project.id)) == 1


def test_a2_concurrent_updates_reject_inverted_dates(tmp_path, monkeypatch):
    path = tmp_path / "race.db"
    c = db.connect(path)
    db.initialize(c)
    p = repo.create(c, "projects", dict(name="Carrera", start_date=day(1)))
    act = activity(c, p, planned_start=day(1), planned_finish=day(10))
    c.close()
    gate = Barrier(2)
    def edit(changes):
        local = db.connect(path)
        try:
            gate.wait(timeout=10)
            try:
                validated_update(local, "activities", act.id, changes)
                return "ok"
            except ValueError:
                return "rechazada"
        finally:
            local.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(edit, {"planned_start": day(8)}), pool.submit(edit, {"planned_finish": day(4)})]
        assert sorted(f.result(timeout=15) for f in futures) == ["ok", "rechazada"]
    c = db.connect(path)
    try:
        result = repo.get(c, "activities", act.id)
        assert result.planned_start <= result.planned_finish
    finally:
        c.close()


def workbook_bytes(project_rows, sheets, marker=False):
    book = Workbook()
    main = book.active
    main.title = "Proyecto"
    for row in project_rows:
        main.append(row)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    if marker:
        from ganttier.excel_io.interchange import MARKER
        book.create_sheet("_Ganttier").append([MARKER, "proyecto"])
    data = BytesIO()
    book.save(data)
    book.close()
    return data.getvalue()


def minimal_book(sheets, marker=False):
    return workbook_bytes([("Nombre", "A2"), ("Modo", "proyecto"), ("Fecha_Inicio", "01-01-2026")], sheets, marker)


def test_a2_excel_null_budget_rejected_atomically(conn, project, client, tmp_path):
    data = minimal_book({"Actividades": [
        ["Codigo", "Nombre", "HH", "planned_start", "planned_finish"],
        ["A", "Actividad", r"\N", "05-01-2026", "09-01-2026"],
    ]}, marker=True)
    path = tmp_path / "null.xlsx"
    path.write_bytes(data)
    parsed = parse_workbook(path)
    assert parsed.has_errors
    response = client.post(f"/api/projects/{project.id}/import", files={"file": ("null.xlsx", data)})
    assert response.status_code == 422, response.text
    assert repo.list_entities(conn, "activities", project.id) == []
    assert client.get(f"/api/projects/{project.id}/control/evm").status_code == 200


def test_a2_reimport_worker_correction_rejects_ambiguous_consumption(conn, project, client):
    def payload(worker):
        return minimal_book({
            "Actividades": [["Codigo", "Nombre", "HH", "planned_start", "planned_finish"],
                            ["A", "Actividad", 40, "05-01-2026", "09-01-2026"]],
            "Recursos": [["Nombre"], ["Ana"]],
            "Avance": [["Actividad", "Fecha", "Porcentaje", "HH_Reales", "Costo_Real", "Trabajador"],
                       ["A", "07-01-2026", 50, 8, 80, worker]],
        })
    for worker in (None, None, "Ana"):
        response = client.post(f"/api/projects/{project.id}/import", files={"file": ("avance.xlsx", payload(worker))})
        assert response.status_code == (422 if worker else 200), response.text
        if worker:
            assert "ambigu" in response.text.lower()
        rows = repo.list_entities(conn, "progress", project.id)
        assert len(rows) == 1
        assert sum(row.actual_hours for row in rows) == 8
    result = services.control_result(conn, project.id, "evm")
    assert result.AC == 8
    assert services.control_result(conn, project.id, "evm", basis="cost").AC == 80


def test_a2_reimport_unchanged_row_preserves_latest_percent(conn, project):
    act = activity(conn, project)
    for name in ("Ana", "Luis"):
        repo.create(conn, "resources", dict(project_id=project.id, name=name))
    first = dict(activity_code="A", date=day(7), percent_complete=20, actual_hours=2, resource_name="Ana")
    second = dict(activity_code="A", date=day(7), percent_complete=50, actual_hours=3, resource_name="Luis")
    repo.save_import_bundle(conn, project.id, d.ImportBundle("proyecto", {}, progress=[first, second]))
    assert services.control_result(conn, project.id, "evm").EV == 20
    repo.save_import_bundle(conn, project.id, d.ImportBundle("proyecto", {}, progress=[first]))
    assert services.control_result(conn, project.id, "evm").EV == 20
    assert services.control_result(conn, project.id, "evm").AC == 5


def test_a2_import_calendar_name_collision_rejected(conn, project, client):
    data = workbook_bytes([
        ("Nombre", "Externo"), ("Modo", "proyecto"), ("Fecha_Inicio", "01-01-2026"),
        ("Calendario", "A2 L-V")], {
        "Calendarios": [["Nombre", "Semana_Laboral", "Horas_Dia"], ["A2 L-V", "1111111", 12]],
        "Actividades": [["Codigo", "Nombre", "Calendario"], ["A", "Actividad", "A2 L-V"]],
    })
    response = client.post("/api/import/new-project", files={"file": ("external.xlsx", data)})
    assert response.status_code == 422, response.text
    assert "A2 L-V" in response.text
    assert len(repo.list_entities(conn, "projects")) == 1
    calendar = repo.get(conn, "calendars", project.calendar_id)
    assert calendar.workweek == "1111100" and calendar.hours_per_day == 8


def test_a2_excel_template_merge_preserves_existing_schedule(conn, project, client):
    act = activity(conn, project, notes="Conservar", priority=75, actual_hours=12, actual_cost=120)
    data = minimal_book({"Actividades": [["Codigo", "Nombre", "HH"], ["A", "Actividad renombrada", 40]]})
    response = client.post(f"/api/projects/{project.id}/import", files={"file": ("update.xlsx", data)})
    assert response.status_code == 200, response.text
    updated = repo.get(conn, "activities", act.id)
    assert updated.name == "Actividad renombrada"
    assert (updated.planned_start, updated.planned_finish) == (day(5), day(9))
    assert (updated.notes, updated.priority, updated.actual_hours, updated.actual_cost) == ("Conservar", 75, 12, 120)


def test_a2_excel_dates_decimals_duplicates_and_predecessors_controls(tmp_path):
    data = minimal_book({
        "Actividades": [["Codigo", "Nombre", "HH", "planned_start", "planned_finish"],
                        ["A", "A", "1.234,50", "05-01-2026", "09-01-2026"],
                        ["B", "B", "8,5", "10-01-2026", "12-01-2026"]],
        "Relaciones": [["Predecesora", "Sucesora", "Tipo", "Desfase_Dias"], ["A", "B", "FS", -1]],
    })
    path = tmp_path / "controls.xlsx"
    path.write_bytes(data)
    parsed = parse_workbook(path)
    assert not parsed.has_errors
    assert parsed.activities[0]["work_hours"] == 1234.5
    assert parsed.activities[1]["work_hours"] == 8.5
    assert parsed.activities[0]["planned_start"] == day(5)
    assert parsed.relations[0]["lag_days"] == -1
    bad = minimal_book({"Actividades": [["Codigo", "Nombre"], ["A", "A"], ["A", "B"]],
                        "Relaciones": [["Predecesora", "Sucesora"], ["UNKNOWN", "A"]]})
    path.write_bytes(bad)
    parsed = parse_workbook(path)
    assert parsed.has_errors and len(parsed.issues) >= 2


def test_a2_sql_filters_and_download_traversal_controls(conn, project, client):
    activity(conn, project)
    assert repo.list_entities(conn, "activities", project.id, filters={"q": "' OR 1=1 --"}) == []
    with pytest.raises(ValueError):
        repo.list_entities(conn, "activities", filters={"id OR 1=1": 1})
    from ganttier.api.files import report_file
    for filename in ("../x.pdf", "..\\x.pdf", "C:\\x.pdf", "x.exe"):
        with pytest.raises(ValueError):
            report_file(filename)
    assert client.get("/..%2F..%2Fbackend%2Fganttier%2Frepo.py").status_code == 404


def test_a2_scheduler_metadata_failure_cleans_unconfirmed_files(conn, project, tmp_path, monkeypatch):
    """Inyeccion de fallo solo en persistencia; render y nombres unicos son reales."""
    activity(conn, project)
    monkeypatch.setattr(services, "DATA_DIR", tmp_path)
    from ganttier import reports
    # El contenido no influye: sustituir el render pesado, conservar render_reports real.
    monkeypatch.setattr(reports, "render", lambda report, fmt, path: path.write_text("informe", encoding="utf-8"))
    autoreports.set_config(conn, project.id, dict(enabled=True, formats=["html"], weekly=True,
                                                monthly=False, annual=False))
    write = autoreports._write
    def fail(conn, key, value):
        if key.startswith("autoreports_log:"):
            raise sqlite3.OperationalError("fallo simulado de escritura de metadatos")
        return write(conn, key, value)
    monkeypatch.setattr(autoreports, "_write", fail)
    with pytest.raises(sqlite3.OperationalError):
        autoreports.run_project_reports(conn, project.id, datetime(2026, 1, 12))
    assert len(list((tmp_path / "informes").glob("*.html"))) == 0
    assert autoreports.get_log(conn, project.id) == []
    monkeypatch.setattr(autoreports, "_write", write)
    autoreports.run_project_reports(conn, project.id, datetime(2026, 1, 12))
    assert len(list((tmp_path / "informes").glob("*.html"))) == 1
    assert len(autoreports.get_log(conn, project.id)) == 1


def test_a2_excel_chart_resampling_preserves_cutoff_anchor(conn, project, tmp_path):
    from ganttier.reports import render
    from openpyxl import load_workbook
    act = activity(conn, project)
    repo.update(conn, "projects", project.id, {"data_date": day(7)})
    post_progress(conn, project, act, 7, 50, actual_hours=20)
    report = services.build_report_data(conn, project.id, "weekly", day(5))
    report = replace(report, scurve=services.control_result(conn, project.id, "scurve", period="day"))
    assert report.scurve.earned_cum[report.scurve.labels.index("2026-01-07")] == 50
    path = render(report, "xlsx", tmp_path / "daily.xlsx")
    book = load_workbook(path)
    try:
        detail = list(book["Curva S"].values)
        assert any(row[0] == datetime(2026, 1, 7) and row[2] == .5 for row in detail[1:])
        chart_source = list(book["_Grafico_Curva"].values)
        anchor = next(row for row in chart_source[1:] if row[0] == datetime(2026, 1, 7))
        assert anchor[2] == anchor[4] == .5
        assert all(row[2] is None for row in chart_source[1:] if row[0] > datetime(2026, 1, 7))
    finally:
        book.close()


@pytest.mark.parametrize("fmt", ["xlsx", "docx", "html", "pdf"])
def test_a2_common_report_numbers_controls(conn, project, tmp_path, monkeypatch, fmt):
    import importlib
    from ganttier.reports import render
    act = activity(conn, project)
    post_progress(conn, project, act, 7, 50, actual_hours=25, actual_cost=250)
    report = services.build_report_data(conn, project.id, "weekly", day(5))
    assert (report.evm.BAC, report.evm.PV, report.evm.EV, report.evm.AC) == (40, 40, 20, 25)
    module = importlib.import_module("ganttier.reports." + fmt)
    sections = module.sections
    consumed = []
    def record(report, **kwargs):
        result = sections(report, **kwargs)
        consumed.extend(result)
        return result
    monkeypatch.setattr(module, "sections", record)
    path = render(report, fmt, tmp_path / ("control." + fmt))
    assert path.stat().st_size > 1000
    numbers = {row[0]: row[1] for section in consumed if section.title == "EVM" for row in section.rows}
    assert [numbers[k] for k in ("BAC", "PV", "EV", "AC", "CPI", "EAC")] == [40, 40, 20, 25, .8, 50]
    if fmt == "xlsx":
        from openpyxl import load_workbook
        book = load_workbook(path)
        try:
            values = {row[0]: row[1] for row in list(book["EVM"].values)[1:]}
            assert values == pytest.approx(numbers)  # XLSX serializa 16 cifras significativas.
        finally:
            book.close()
    elif fmt == "docx":
        from docx import Document
        table = next(t for t in Document(path).tables if [c.text for c in t.rows[0].cells] == ["Indicador", "Valor", "Unidad"])
        assert {r.cells[0].text: r.cells[1].text for r in table.rows[1:]}["AC"] == "25,00"
    elif fmt == "html":
        assert "<td>AC</td><td>25,00</td>" in path.read_text(encoding="utf-8")
    else:
        assert path.read_bytes().startswith(b"%PDF")


def test_a2_evm_es_and_zero_denominators_controls(conn, project):
    act = activity(conn, project)
    repo.update(conn, "projects", project.id, {"data_date": day(7)})
    post_progress(conn, project, act, 7, 40, actual_hours=25)
    result = services.control_result(conn, project.id, "evm")
    assert (result.BAC, result.PV, result.EV, result.AC) == (40, 24, 16, 25)
    assert (result.EAC, result.ETC, result.VAC) == (62.5, 37.5, -22.5)
    assert result.SPI == result.SPI_t == pytest.approx(2 / 3)
    assert result.CPI == .64 and result.TCPI == pytest.approx(24 / 15)
    assert result.ES_days == 2 and result.forecast_finish == day(12)
    curve = services.control_result(conn, project.id, "scurve")
    i = curve.labels.index("2026-01-07")
    assert curve.earned_abs[i] == curve.forecast_abs[i] == result.EV
    entry = repo.list_entities(conn, "progress", project.id)[0]
    repo.update(conn, "progress", entry.id, dict(percent_complete=0, actual_hours=40))
    result = services.control_result(conn, project.id, "evm")
    assert result.CPI == 0 and result.TCPI is result.EAC is result.forecast_finish is None


@pytest.mark.parametrize("actual_start", [None, day(5)])
def test_a2_snapshot_ac_fecha_y_sustitucion_por_historial(conn, project, actual_start):
    act = activity(conn, project, actual_start=actual_start, actual_hours=30, actual_cost=300)
    curve = services.control_result(conn, project.id, "scurve", period="day")
    for label, value in zip(curve.labels, curve.actual_abs):
        if label <= day(9).isoformat():
            assert value == (30 if label >= (actual_start or day(9)).isoformat() else 0)
    # Un registro futuro también excluye el respaldo: no se reconstruye AC desde la ficha.
    repo.create(conn, "progress", dict(activity_id=act.id, date=day(12), percent_complete=50,
                                      actual_hours=8, actual_cost=80))
    assert services.control_result(conn, project.id, "evm").AC == 0
    assert services.control_result(conn, project.id, "evm", as_of=day(12)).AC == 8
    assert services.control_result(conn, project.id, "evm", basis="cost", as_of=day(12)).AC == 80


def test_a2_snapshot_no_anticipa_consumo_al_inicio(conn, project):
    activity(conn, project, actual_start=day(8), actual_hours=30, actual_cost=300)
    assert services.control_result(conn, project.id, "evm", as_of=day(7)).AC == 0
    assert services.control_result(conn, project.id, "evm", as_of=day(8)).AC == 30


def test_a2_reapertura_respeta_corte_y_orden_del_dia(conn, project):
    act = activity(conn, project)
    post_progress(conn, project, act, 7, 100, actual_hours=40)
    post_progress(conn, project, act, 8, 60)
    post_progress(conn, project, act, 8, 50)
    assert services.control_result(conn, project.id, "evm", as_of=day(7)).EV == 40
    assert services.control_result(conn, project.id, "evm", as_of=day(8)).EV == 20
    records = list(reversed(repo.list_entities(conn, "progress", project.id)))
    views = services.build_activity_views(conn, project.id)
    cal = WorkCalendar(repo.get(conn, "calendars", project.calendar_id), day(1), date(2027, 1, 1))
    assert evm(views, None, records, cal, day(8)).EV == 20


@pytest.mark.parametrize("baseline", [False, True])
def test_a2_calendarios_mixtos_pv_proyeccion_y_personal(conn, project, baseline):
    seven = repo.create(conn, "calendars", dict(name="Continuo", workweek="1111111", holidays_country=None))
    a = activity(conn, project, planned_start=day(9), planned_finish=day(12), duration_days=2, work_hours=16)
    b = activity(conn, project, code="B", planned_start=day(9), planned_finish=day(12), duration_days=4,
                 work_hours=32, calendar_id=seven.id)
    if baseline:
        services.create_baseline(conn, project.id)
    for act, pct in ((a, 50), (b, 25)):
        person = repo.create(conn, "resources", dict(name=act.code, project_id=project.id))
        repo.create(conn, "assignments", dict(activity_id=act.id, resource_id=person.id, work_hours=act.work_hours))
        post_progress(conn, project, act, 9, pct, actual_hours=8, resource_id=person.id)
    curve = services.control_result(conn, project.id, "scurve", period="day")
    assert curve.labels == [day(n).isoformat() for n in range(9, 13)]
    assert curve.planned_abs == [16, 24, 32, 48]
    assert curve.forecast_abs == [16, 24, 32, 48]
    assert curve.budget_total == curve.planned_abs[-1] == curve.forecast_abs[-1] == 48
    workers = services.control_result(conn, project.id, "performance", start=day(9), end=day(12))
    assert [worker.SPI for worker in workers] == [1, 1]
    weekend = services.control_result(conn, project.id, "performance", start=day(10), end=day(11))
    assert [worker.planned_hours for worker in weekend] == [0, 16]


def test_a2_calendario_excepcion_hito_y_sin_jornadas(conn, project):
    cal = repo.create(conn, "calendars", dict(name="Con excepción", holidays_country=None,
        exceptions=[dict(date=day(12), working=False)]))
    activity(conn, project, planned_start=day(9), planned_finish=day(12), work_hours=16, calendar_id=cal.id)
    milestone = activity(conn, project, code="H", planned_start=day(10), planned_finish=day(10),
                         duration_days=0, work_hours=3)
    weekend = activity(conn, project, code="S", planned_start=day(10), planned_finish=day(11), work_hours=5)
    assert services.control_result(conn, project.id, "evm", as_of=day(9)).PV == 16
    assert services.control_result(conn, project.id, "evm", as_of=day(10)).PV == 19
    assert services.control_result(conn, project.id, "evm", as_of=day(11)).PV == 24
    person = repo.create(conn, "resources", dict(project_id=project.id, name="Ana"))
    for act in (milestone, weekend):
        repo.create(conn, "assignments", dict(activity_id=act.id, resource_id=person.id, work_hours=act.work_hours))
    perf = services.control_result(conn, project.id, "performance", start=day(10), end=day(10))[0]
    assert perf.planned_hours == 3


def test_a2_proyeccion_conserva_bac_exacto_con_fracciones(conn, project):
    for i, (budget, percent) in enumerate(((.1, 17), (.2, 33), (.3, 51))):
        activity(conn, project, code=str(i), work_hours=budget, percent_complete=percent)
    curve = services.control_result(conn, project.id, "scurve", period="day")
    assert curve.forecast_abs[-1] == curve.planned_abs[-1] == curve.budget_total


def test_a2_demanda_generica_conserva_su_participacion(conn, project):
    act = activity(conn, project, duration_days=1, planned_finish=day(5), work_hours=8)
    person = repo.create(conn, "resources", dict(project_id=project.id, name="Ana"))
    crane = repo.create(conn, "resources", dict(project_id=project.id, name="Grúa", kind="equipo"))
    for rid in (person.id, crane.id, None):
        repo.create(conn, "assignments", dict(activity_id=act.id, resource_id=rid, units=1, skill="Montaje"))
    post_progress(conn, project, act, 5, 100, actual_hours=8)
    perf = services.control_result(conn, project.id, "performance", start=day(5), end=day(9))[0]
    assert perf.earned_hours == perf.actual_hours == 4


def test_a2_actualizacion_anidada_preserva_transaccion_y_rollback(conn, project):
    act = activity(conn, project)
    with pytest.raises(RuntimeError):
        with repo.transaction(conn):
            repo.update(conn, "activities", act.id, dict(name="Temporal"))
            with pytest.raises(ValueError):
                validated_update(conn, "activities", act.id, dict(planned_finish=day(4)))
            assert conn.in_transaction
            assert repo.get(conn, "activities", act.id).name == "Temporal"
            raise RuntimeError("Revertir toda la operación")
    assert repo.get(conn, "activities", act.id).name == "Actividad"
    assert not conn.in_transaction


@pytest.mark.parametrize("field", ["work_hours", "cost_budget", "actual_hours", "actual_cost", "duration_days"])
def test_a2_nulos_obligatorios_no_persisten_lote(conn, project, field):
    act = activity(conn, project)
    bundle = d.ImportBundle("proyecto", {}, activities=[
        dict(code="A", name="Renombrada"), dict(code="B", name="Inválida", **{field: None})])
    with pytest.raises(ValueError, match="NULL"):
        repo.save_import_bundle(conn, project.id, bundle)
    assert repo.get(conn, "activities", act.id).name == "Actividad"
    assert len(repo.list_entities(conn, "activities", project.id)) == 1


def test_a2_reimportacion_conserva_ids_y_rechaza_clave_multiple(conn, project):
    act = activity(conn, project)
    first = repo.create(conn, "progress", dict(activity_id=act.id, date=day(7), percent_complete=50, actual_hours=8))
    bundle = d.ImportBundle("proyecto", {}, progress=[dict(activity_code="A", date=day(7), percent_complete=60, actual_hours=9)])
    repo.save_import_bundle(conn, project.id, bundle)
    rows = repo.list_entities(conn, "progress", project.id)
    assert len(rows) == 1 and rows[0].id == first.id and rows[0].actual_hours == 9
    repo.create(conn, "progress", dict(activity_id=act.id, date=day(7), percent_complete=70, actual_hours=2))
    with pytest.raises(ValueError, match="ambiguo"):
        repo.save_import_bundle(conn, project.id, bundle)
    assert sum(row.actual_hours for row in repo.list_entities(conn, "progress", project.id)) == 11


def test_a2_calendarios_iguales_reutilizables_excepciones_distintas_rechazadas(conn, project):
    calendar = repo.get(conn, "calendars", project.calendar_id)
    source = d.to_dict(calendar)
    source.pop("id")
    bundle = d.ImportBundle("proyecto", {}, calendars=[source])
    repo.save_import_bundle(conn, project.id, bundle)
    source["exceptions"] = [dict(date=day(6), working=False, hours=0, reason="Cierre")]
    with pytest.raises(ValueError, match="A2 L-V.*difiere"):
        repo.save_import_bundle(conn, project.id, bundle)
    assert repo.get(conn, "calendars", calendar.id).exceptions == []


def test_a2_scheduler_tope_y_continuacion_persistida(conn, project, monkeypatch):
    calls = []
    def render(c, pid, kind, start, formats):
        calls.append((kind, start))
        return [dict(format=formats[0], filename=f"proyecto-{pid}-{kind}-{start}.html")]
    monkeypatch.setattr(services, "render_reports", render)
    autoreports.set_config(conn, project.id, dict(enabled=True, weekly=True, monthly=False,
                                                 annual=False, formats=["html"], keep_last=1))
    autoreports.run_project_reports(conn, project.id, datetime(2026, 1, 12))
    calls.clear()
    autoreports.run_project_reports(conn, project.id, datetime(2026, 8, 3))
    assert len(calls) == 12
    assert calls[0][1] == day(12)
    previous = set(calls)
    calls.clear()
    autoreports.run_project_reports(conn, project.id, datetime(2026, 8, 3))
    assert len(calls) == 12 and not previous.intersection(calls)


def test_a2_scheduler_reintenta_formato_antiguo_incompleto(conn, project, monkeypatch):
    calls = []
    failing = True
    def render(c, pid, kind, start, formats):
        calls.append((start, formats[0]))
        if failing and formats == ["xlsx"]:
            raise ValueError("Fallo simulado")
        return [dict(format=formats[0], filename=f"proyecto-{pid}-{kind}-{start}.{formats[0]}")]
    monkeypatch.setattr(services, "render_reports", render)
    autoreports.set_config(conn, project.id, dict(enabled=True, weekly=True, monthly=False,
                                                 annual=False, formats=["html", "xlsx"]))
    autoreports.run_project_reports(conn, project.id, datetime(2026, 1, 12))
    failing = False
    calls.clear()
    autoreports.run_project_reports(conn, project.id, datetime(2026, 1, 26))
    assert calls == [(day(5), "xlsx"), (day(12), "html"), (day(12), "xlsx"),
                     (day(19), "html"), (day(19), "xlsx")]


def test_a2_render_fallido_retira_archivos_parciales(conn, project, tmp_path, monkeypatch):
    from ganttier import reports
    activity(conn, project)
    monkeypatch.setattr(services, "DATA_DIR", tmp_path)
    def render(report, fmt, path):
        path.write_text("Parcial", encoding="utf-8")
        if fmt == "xlsx":
            raise ValueError("Fallo simulado")
    monkeypatch.setattr(reports, "render", render)
    with pytest.raises(ValueError, match="simulado"):
        services.render_reports(conn, project.id, "weekly", day(5), ["html", "xlsx"])
    assert list((tmp_path / "informes").iterdir()) == []


def test_verificacion_demo_conserva_cantidad_al_regenerar(conn):
    """Regresión auxiliar del fallo de escala descubierto en la suite obligatoria."""
    project = services.demo_maintenance(17, conn)
    assert len(repo.list_entities(conn, "activities", project.id)) == 17
    services.generate_maintenance(conn, project.id, project.start_date, project.end_date, replace=False)
    assert len(repo.list_entities(conn, "activities", project.id)) == 17
