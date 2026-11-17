"""Cantidad, lotes, carga y alertas se conservan en informes y Gantt por equipo."""
from datetime import date
from openpyxl import load_workbook
import pytest

from ganttier import db, repo, services, teams
from ganttier.excel_io.gantt_xlsx import export_gantt, Options
from ganttier.reports.common import sections
from ganttier.reports.builder import render


@pytest.fixture
def paneles(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "DATA_DIR", tmp_path / "datos")
    conn = db.connect(tmp_path / "reports.db")
    db.initialize(conn)
    first, last = date(2027, 1, 1), date(2027, 12, 31)
    pid = repo.create(conn, "projects", dict(name="Paneles", mode="mantenimiento", start_date=first,
        end_date=last, data_date=first, calendar_id=1)).id
    ids = [teams.save_team(conn, pid, dict(name=f"Equipo {n}", regime="5x2", members=3))["id"] for n in "AB"]
    repo.create(conn, "periodic_tasks", dict(project_id=pid, code="PANEL", name="Limpieza", first_due=first,
        quantity_total=28000, batch_size=2000, productivity_day_team=1000, unit="paneles", team_ids=ids))
    assert services.generate_maintenance(conn, pid, first, last)["applied"]
    yield conn, pid, first, last
    conn.close()


@pytest.mark.parametrize("period,quantity,batches", [("monthly",28000,14),("annual",336000,168)])
def test_report_quantity_and_batches(paneles, tmp_path, period, quantity, batches):
    conn, pid, first, _ = paneles
    report = services.build_report_data(conn, pid, period, first)
    assert sum(r["quantity"] for r in report.team_distribution) == quantity
    assert sum(r["scheduled_batches"] for r in report.team_distribution) == batches
    assert any(s.title == "Reparto por equipo" for s in sections(report))
    path = render(report, "xlsx", tmp_path / f"{period}.xlsx")
    book = load_workbook(path, read_only=True)
    try:
        assert "Reparto por equipo" in book.sheetnames
    finally:
        book.close()


def test_gantt_groups_and_load_use_team_not_physical_tag(paneles, tmp_path):
    conn, pid, first, last = paneles
    path = export_gantt(conn, pid, tmp_path / "gantt.xlsx", Options(mode="nivel", group_by="equipo", scale="month", from_date=first, to_date=last))
    book = load_workbook(path, read_only=True)
    try:
        assert {book["Resumen por nivel"].cell(i,1).value for i in (6,7)} == {"Equipo A", "Equipo B"}
        rows = list(book["Carga por equipo"].values)[1:]
        assert sum(r[2] for r in rows) == 8064
        assert len(list(book["Reparto por equipo"].values)) == 25
    finally:
        book.close()


def test_report_contains_unequal_shift_warning(paneles):
    conn, pid, first, _ = paneles
    for i, team in enumerate(teams.list_teams(conn, pid)):
        teams.save_team(conn, pid, dict(regime="7x7", cycle_start=date(2027,1,1+i*7)), team["id"])
    report = services.build_report_data(conn, pid, "annual", first)
    assert any("días hábiles distintos" in message for message in report.team_alerts)
    assert any(s.title == "Alertas de equipos" for s in sections(report))
