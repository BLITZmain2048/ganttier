"""A4: cortes históricos, pronósticos y conciliación con Inicio."""
from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook

from ganttier import db, repo, services, tools
from ganttier.api.analysis import project_forecasts
from ganttier.control.aggregation import aggregate_evm
from ganttier.maintenance.kpis import kpis
from ganttier.reports.weekly_roster import weekly_roster


@pytest.fixture
def data():
    conn = db.connect(":memory:")
    db.initialize(conn)
    cal = repo.create(conn, "calendars", {"name": "A4 sin feriados", "holidays_country": None})
    p = repo.create(conn, "projects", {"name": "A4", "start_date": "2026-01-05",
        "end_date": "2026-12-31", "data_date": "2026-03-31", "calendar_id": cal.id})
    yield conn, p, cal
    conn.close()


def activity(conn, pid, code="A", **kw):
    return repo.create(conn, "activities", {"project_id": pid, "code": code, "name": code,
        "planned_start": "2026-01-05", "planned_finish": "2026-01-09",
        "duration_days": 5, "work_hours": 100, **kw})


def test_closed_report_does_not_earn_snapshot_before_actual_start(data):
    conn, p, _ = data
    activity(conn, p.id, percent_complete=100, actual_hours=100,
             actual_start="2026-03-02", actual_finish="2026-03-06")
    # Snapshot de ficha/importación válido en marzo; no hay registros en progress.
    assert services.control_result(conn, p.id, "evm").EV == 100
    february = services.build_report_data(conn, p.id, "monthly", date(2026, 2, 1))
    assert february.project.data_date == date(2026, 2, 28)
    assert february.evm.EV == 0, "Antes de comenzar realmente el trabajo, el informe gana el 100 %"


def test_completed_duration_forecasts_use_actual_completion_time(data):
    conn, p, _ = data
    activity(conn, p.id, percent_complete=100, actual_hours=100,
             actual_start="2026-01-05", actual_finish="2026-01-06")
    results = {row.name: row for row in project_forecasts(p.id, conn=conn)}
    assert results["IEAC_t"].value == pytest.approx(2)
    assert results["duracion_remanente"].value == pytest.approx(2)
    assert results["duracion_remanente"].finish_date == date(2026, 1, 6)


def test_forecast_eac_tcpi_and_ieac_formulas_for_incomplete_work(data):
    conn, p, _ = data
    repo.update(conn, "projects", p.id, {"data_date": "2026-01-06"})
    activity(conn, p.id, percent_complete=25, actual_hours=50, actual_start="2026-01-05")
    result = {r.name: r for r in project_forecasts(p.id, etc="30", conn=conn)}
    # BAC=100, EV=25, AC=50, PV=40; CPI=0,5 y SPI=0,625.
    expected = {"EAC_CPI": 200, "EAC_presupuesto": 125, "EAC_combinado": 290,
                "EAC_reestimado": 80, "TCPI_EAC_CPI": 0.5, "TCPI_EAC_presupuesto": 1,
                "TCPI_EAC_combinado": 0.3125, "TCPI_EAC_reestimado": 2.5,
                "IEAC_t": 8, "duracion_remanente": 5.75}
    assert {key: result[key].value for key in expected} == pytest.approx(expected)
    assert result["IEAC_t"].finish_date == date(2026, 1, 12)


def test_summary_keeps_control_evm_when_new_out_of_baseline_task_is_unscheduled(data):
    conn, p, _ = data
    activity(conn, p.id)
    services.create_baseline(conn, p.id, "LB")
    activity(conn, p.id, code="Nueva", planned_start=None, planned_finish=None)
    expected = services.control_result(conn, p.id, "evm")
    assert expected.BAC == 100
    summary = services.project_summary(conn, p.id)
    assert summary["unscheduled"] == 1
    assert summary["evm"] is not None, "Inicio oculta indicadores que Control sí puede calcular con la LB activa"
    assert summary["evm"]["BAC"] == expected.BAC


def test_finished_activity_is_not_reported_overdue_by_quality_or_alerts(data):
    from ganttier.control import alerts
    from ganttier.control.schedule_quality import schedule_quality

    conn, p, _ = data
    activity(conn, p.id, actual_start="2026-01-05", actual_finish="2026-01-09")
    # Fechas reales válidas de una ficha; el campo de porcentaje conserva su valor predeterminado.
    assert services.control_result(conn, p.id, "evm").percent_complete == 100
    quality = schedule_quality(repo.list_entities(conn, "activities", p.id), [], [], date(2026, 3, 31))
    persisted = alerts.evaluate(conn, p.id, today=date(2026, 3, 31))
    erroneous = [r.rule for r in quality.findings if r.rule == "actividades_vencidas"]
    erroneous += [r["rule"] for r in persisted if r["rule"] == "actividad_vencida"]
    assert erroneous == []


def test_chain_cycle_rolls_back_every_group_and_preserves_outer_transaction(data):
    conn, p, _ = data
    acts = [activity(conn, p.id, code=code) for code in ("A", "B", "C", "D")]
    tools.relation_chain(conn, p.id, [["B", "A"]])
    before = conn.execute("SELECT * FROM relations").fetchall()
    conn.execute("BEGIN")
    conn.execute("UPDATE projects SET description='pendiente del llamador' WHERE id=?", (p.id,))
    with pytest.raises(ValueError, match="ciclo"):
        tools.relation_chain(conn, p.id, [["C", "D"], ["A", "B"]])
    assert conn.in_transaction
    assert repo.get(conn, "projects", p.id).description == "pendiente del llamador"
    assert conn.execute("SELECT * FROM relations").fetchall() == before
    conn.rollback()
    assert repo.get(conn, "projects", p.id).description == ""


def test_bulk_patch_cannot_write_another_project_or_partially_apply(data):
    conn, p, _ = data
    other = repo.create(conn, "projects", {"name": "Otro", "start_date": "2026-01-05"})
    a, b = activity(conn, p.id), activity(conn, other.id)
    with pytest.raises(ValueError, match="pertenece"):
        repo.patch_activities(conn, p.id, [{"id": a.id, "work_hours": 20}, {"id": b.id, "work_hours": 20}])
    assert repo.get(conn, "activities", a.id).work_hours == 100
    assert repo.get(conn, "activities", b.id).work_hours == 100


def test_kpi_denominator_includes_due_unexecuted_and_excludes_future(data):
    conn, p, _ = data
    task = repo.create(conn, "periodic_tasks", {"project_id": p.id, "code": "PM", "name": "PM"})
    for code, due, pct in (("Done", "2026-02-01", 100), ("Pending", "2026-02-01", 0),
                           ("Future", "2026-12-01", 0)):
        activity(conn, p.id, code=code, periodic_task_id=task.id, due_date=due, percent_complete=pct,
                 actual_start="2026-01-05" if pct else None,
                 actual_finish="2026-01-09" if pct else None)
    result = kpis(conn, p.id, end=date(2026, 12, 31))
    assert result["compliance"]["due"] == 2
    assert result["compliance"]["total_pct"] == 50
    assert result["backlog"]["remaining_hours"] == 100


def test_weekly_roster_clips_hours_without_redistributing_whole_assignment(data):
    conn, p, cal = data
    a = activity(conn, p.id, planned_finish="2026-01-16", duration_days=10, work_hours=80)
    r = repo.create(conn, "resources", {"project_id": p.id, "name": "Trabajador", "calendar_id": cal.id})
    repo.create(conn, "assignments", {"activity_id": a.id, "resource_id": r.id, "work_hours": 80})
    book = load_workbook(BytesIO(weekly_roster(conn, p.id, date(2026, 1, 7))))
    try:
        rows = list(book["Detalle"].values)[1:]
        assert [r[2].date() for r in rows] == [date(2026, 1, day) for day in range(5, 10)]
        assert [r[6] for r in rows] == [8] * 5
        assert sum(r[6] for r in rows) == 40
        assert book["Detalle"]["C2"].number_format == "dd-mm-yyyy"
        assert "05-01-2026" in book["Plan semanal"]["C1"].value
    finally:
        book.close()


def test_evm_groups_reconcile_with_active_baseline_and_activity_calendars(data):
    conn, p, _ = data
    activity(conn, p.id, location="Norte")
    activity(conn, p.id, code="B", location="Sur", work_hours=70)
    services.create_baseline(conn, p.id)
    activity(conn, p.id, code="C", location="Fuera LB", work_hours=300)
    inputs = services.control_inputs(conn, p.id)
    result = aggregate_evm(inputs["acts"], inputs["baseline"], inputs["progress"], inputs["calendar"],
        inputs["cutoff"], activities=repo.list_entities(conn, "activities", p.id), group="location",
        calendars=inputs["calendars"])
    assert result.total.BAC == 170
    assert result.reconciliation.matches
