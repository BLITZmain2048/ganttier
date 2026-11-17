"""Pruebas CTL en proceso y contra HTTP, exclusivamente con bases temporales."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from contextlib import closing
from datetime import date
from pathlib import Path
import time
import uuid
import zipfile

import pytest

from ganttier.cli._base import CliError, Context
from ganttier.cli.comandos.control import register


ROOT = Path(__file__).resolve().parents[3]
TEMP = ROOT / "trabajos/_temporales/cli_codex_20261019/CTL"


def invoke(ctx, *argv):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(required=True))
    args = parser.parse_args(list(argv))
    return args.handler(args, ctx)


@pytest.fixture
def case(monkeypatch):
    from fastapi.testclient import TestClient
    from ganttier import db, repo, services
    from ganttier.api import control, schemas
    from ganttier.api.dependencies import connection
    from ganttier.control.cache import clear
    from ganttier.main import create_app

    directory = TEMP / uuid.uuid4().hex
    directory.mkdir(parents=True)
    path = directory / "control.sqlite"
    monkeypatch.setenv("GANTTIER_DB", str(path))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(directory))
    monkeypatch.setattr(services, "DATA_DIR", directory)
    with closing(db.connect(path)) as conn:
        db.initialize(conn)
        project = services.demo_project(conn)
        pid = project.id
        control.data_date(pid, schemas.DataDateRequest(data_date=date(2027, 2, 6)), conn)
        activity = repo.list_entities(conn, "activities", pid)[0]
        control.create_progress(pid, schemas.ProgressEntry(activity_id=activity.id, date=date(2027, 2, 6),
            percent_complete=40, actual_hours=12), conn)
        services.create_baseline(conn, pid, "CTL inicial")
        conn.commit()
    ctx = Context(path, directory, str(pid), no_backup=True)
    app = create_app(token="ctl-test")

    def connect():
        with closing(db.connect(path)) as conn:
            yield conn

    app.dependency_overrides[connection] = connect
    # Sin lifespan: no scheduler ni respaldos automáticos en pruebas.
    client = TestClient(app, headers={"X-Ganttier-Token": "ctl-test"})
    yield ctx, client, pid, activity
    client.close()
    clear()


@pytest.mark.parametrize("command,route,params", [
    (("curva-s",), "control/scurve", {}),
    (("curva-s", "--base", "costo", "--periodo", "mes"), "control/scurve", {"basis": "cost", "period": "month"}),
    (("curva-s", "--base", "actividades", "--periodo", "dia"), "control/scurve", {"basis": "count", "period": "day"}),
    (("evm",), "control/evm", {}),
    (("evm", "--base", "costo"), "control/evm", {"basis": "cost"}),
    (("evm", "--base", "actividades"), "control/evm", {"basis": "count"}),
    (("tablero",), "control/dashboard", {}),
    (("evm-por", "wbs"), "control/evm-by", {}),
    (("evm-por", "instalacion"), "control/evm-by", {"group": "location"}),
    (("evm-por", "habilidad"), "control/evm-by", {"group": "skill"}),
    (("pronosticos", "--etc", "75"), "control/forecasts", {"etc": "75"}),
    (("desempeno",), "control/performance", {}),
    (("calidad",), "control/quality", {}),
    (("actividades", "--pagina", "2", "--por-pagina", "7", "--alcance", "todas"), "control/activities", {"offset": 7, "limit": 7, "scope": "all"}),
    (("avance", "listar"), "progress", {}),
    (("linea-base", "listar"), "baselines", {}),
])
def test_read_equivalence(case, command, route, params):
    ctx, client, pid, _ = case
    expected = client.get(f"/api/projects/{pid}/{route}", params=params)
    assert expected.status_code == 200, expected.text
    assert invoke(ctx, "control", *command).data == expected.json()


def snapshot(ctx, *, ignore_sequences=False):
    with closing(ctx.conn()) as conn:
        return "\n".join(line for line in conn.iterdump()
                         if not ignore_sequences or '"sqlite_sequence"' not in line)


def test_progress_simulation_and_http_equivalence(case):
    ctx, client, pid, activity = case
    before = snapshot(ctx)
    row = {"codigo": activity.code, "fecha": "2027-02-10", "porcentaje": 100, "hh_reales": 5}
    actual = invoke(ctx, "control", "avance", "registrar", "--json", json.dumps(row)).data
    assert ctx.mutation_info.simulated
    assert snapshot(ctx) == before
    expected = client.post(f"/api/projects/{pid}/progress", json={"activity_id": activity.id,
        "date": row["fecha"], "percent_complete": 100, "actual_hours": 5})
    assert expected.status_code == 201, expected.text
    assert actual == expected.json()
    # Un registro futuro se conserva, pero no adelanta el estado al corte.
    assert client.get(f"/api/activities/{activity.id}").json()["percent_complete"] == 40
    ctx.write = True
    updated = invoke(ctx, "control", "fecha-corte", "fijar", "2027-02-10").data
    assert updated == client.put(f"/api/projects/{pid}/data-date", json={"data_date": "2027-02-10"}).json()
    assert client.get(f"/api/activities/{activity.id}").json()["percent_complete"] == 100
    assert invoke(ctx, "control", "fecha-corte", "ver").data == {"data_date": "2027-02-10"}


@pytest.mark.parametrize("extension", ["json", "csv"])
def test_batch_atomic_and_equivalent(case, extension):
    ctx, client, pid, activity = case
    ctx.write = True
    rows = [{"actividad_id": activity.id, "fecha": "2027-02-06", "porcentaje": 60, "hh_reales": 4},
            {"actividad_id": activity.id, "fecha": "2027-02-05", "porcentaje": 50, "hh_reales": 3}]
    path = ctx.data_dir / ("lote." + extension)

    def save(values):
        if extension == "json":
            path.write_text(json.dumps(values), encoding="utf-8")
        else:
            with path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=list(values[0]))
                writer.writeheader()
                writer.writerows(values)

    invalid = [rows[0], dict(rows[1], porcentaje=101)]
    save(invalid)
    before = snapshot(ctx, ignore_sequences=True)
    with pytest.raises(CliError) as error:
        invoke(ctx, "control", "avance", "lote", "--archivo", str(path))
    assert error.value.detail["errors"][0]["row"] == (3 if extension == "csv" else 2)
    # Context.mutation inicializa antes de ceder el control: INSERT OR IGNORE
    # de calendarios consume sqlite_sequence. Se audita en CONFLICTOS.md.
    assert snapshot(ctx, ignore_sequences=True) == before
    # Error de referencia detectado por repo después de validar los esquemas.
    if extension == "json":
        save([rows[0], dict(rows[1], resource_id=999999)])
        with pytest.raises(CliError) as reference_error:
            invoke(ctx, "control", "avance", "lote", "--archivo", str(path))
        assert [error["row"] for error in reference_error.value.detail["errors"]] == [2]
        assert snapshot(ctx, ignore_sequences=True) == before
    save(rows)
    ctx.write = False
    actual = invoke(ctx, "control", "avance", "lote", "--archivo", str(path)).data
    assert snapshot(ctx, ignore_sequences=True) == before
    expected = client.post(f"/api/projects/{pid}/progress", json=[{"activity_id": r["actividad_id"],
        "date": r["fecha"], "percent_complete": r["porcentaje"], "actual_hours": r["hh_reales"]} for r in rows])
    assert expected.status_code == 201, expected.text
    assert actual == expected.json()


def test_baseline_and_alerts(case):
    ctx, client, pid, _ = case
    before = snapshot(ctx)
    baseline = invoke(ctx, "control", "linea-base", "crear", "--nombre", "CTL nueva").data
    assert snapshot(ctx) == before
    expected = client.post(f"/api/projects/{pid}/baselines", json={"name": "CTL nueva"}).json()
    # created_at lo asigna SQLite: comparar todos los demás campos de la captura.
    assert {k: v for k, v in baseline.items() if k != "created_at"} == {k: v for k, v in expected.items() if k != "created_at"}
    ctx.write = True
    assert invoke(ctx, "control", "linea-base", "activar", str(expected["id"])).data == client.post(f"/api/baselines/{expected['id']}/activate").json()
    # Sembrar primero hace estables first_seen/updated_at, conservados por evaluate.
    expected = client.get(f"/api/projects/{pid}/alerts").json()
    ctx.write = False
    before = snapshot(ctx)
    assert invoke(ctx, "control", "alertas").data == expected
    assert snapshot(ctx) == before
    assert invoke(ctx, "control", "alertas", "--resumen").data == client.get(f"/api/projects/{pid}/alerts/summary").json()


def validate_file(path):
    data = path.read_bytes()
    if path.suffix in {".xlsx", ".docx"}:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert archive.testzip() is None
            assert "[Content_Types].xml" in archive.namelist()
        if path.suffix == ".xlsx":
            from openpyxl import load_workbook
            book = load_workbook(path, read_only=True)
            assert book.sheetnames
            book.close()
        else:
            from docx import Document
            assert Document(path).paragraphs
    elif path.suffix == ".pdf":
        assert data.startswith(b"%PDF-") and b"%%EOF" in data[-1024:]
    else:
        assert "<html" in data.decode("utf-8").lower()


@pytest.mark.parametrize("kind,api_kind", [("semanal", "weekly"), ("mensual", "monthly"), ("anual", "annual")])
def test_reports_all_formats_and_api(case, kind, api_kind, monkeypatch):
    ctx, client, pid, _ = case
    from ganttier import reports, services
    seen = []
    original = reports.render

    def capture(report, fmt, path):
        from fastapi.encoders import jsonable_encoder
        value = jsonable_encoder(report)
        value.pop("generated_at", None)
        seen.append((fmt, value))
        return original(report, fmt, path)

    monkeypatch.setattr(reports, "render", capture)
    ctx.write = True
    output = ctx.data_dir / "salida"
    actual = invoke(ctx, "informes", "generar", "--tipo", kind, "--inicio", "2027-02-02", "--a", str(output)).data
    cli_data = list(seen)
    seen.clear()
    expected = client.post(f"/api/projects/{pid}/reports", json={"period_type": api_kind,
        "period_start": "2027-02-02", "formats": ["pdf", "xlsx", "docx", "html"]})
    assert expected.status_code == 200, expected.text
    assert cli_data == seen  # Mismo ReportData exacto entregado a cada renderizador.
    assert [r["format"] for r in actual] == [r["format"] for r in expected.json()]
    for entry in actual:
        path = Path(entry["path"])
        assert path.parent == output
        assert path.stat().st_size == entry["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        validate_file(path)
    for entry in expected.json():
        validate_file(services.DATA_DIR / "informes" / entry["filename"])


def test_report_simulation_and_roster(case, monkeypatch):
    ctx, client, pid, _ = case
    from ganttier.cli.comandos import _control_reports
    original = _control_reports.file_metadata
    checked = []

    def inspect(path, simulated):
        validate_file(path)
        checked.append(path)
        return original(path, simulated)

    monkeypatch.setattr(_control_reports, "file_metadata", inspect)
    before = snapshot(ctx)
    destination = ctx.data_dir / "no_debe_existir"
    files = invoke(ctx, "informes", "generar", "--tipo", "semanal", "--inicio", "2027-02-02", "--a", str(destination)).data
    assert len(checked) == 4
    assert all(entry["simulated"] and not Path(entry["path"]).exists() for entry in files)
    assert not destination.exists() and snapshot(ctx) == before
    ctx.write = True
    roster = invoke(ctx, "informes", "plantel-semanal", "--inicio", "2027-02-02", "--a", str(ctx.data_dir / "plantel.xlsx")).data
    expected = client.get(f"/api/projects/{pid}/reports/weekly-roster", params={"week_start": "2027-02-02"})
    assert expected.status_code == 200
    from openpyxl import load_workbook
    a = load_workbook(roster["path"])
    b = load_workbook(io.BytesIO(expected.content))
    assert a.sheetnames == b.sheetnames
    for name in a.sheetnames:
        assert list(a[name].values) == list(b[name].values)
    a.close()
    b.close()


def test_automatic_reports(case):
    ctx, client, pid, _ = case
    assert invoke(ctx, "informes", "automaticos", "ver").data == client.get(f"/api/projects/{pid}/autoreports").json()
    config = {"enabled": True, "weekly": True, "monthly": False, "annual": False, "formats": ["html"]}
    ctx.write = True
    assert invoke(ctx, "informes", "automaticos", "configurar", "--json", json.dumps(config)).data == client.put(f"/api/projects/{pid}/autoreports", json=config).json()
    ctx.write = False
    before = snapshot(ctx)
    simulated = invoke(ctx, "informes", "automaticos", "ejecutar").data
    assert simulated and snapshot(ctx) == before
    expected = client.post(f"/api/projects/{pid}/autoreports/run").json()
    for a, b in zip(simulated, expected):
        assert {k: v for k, v in a.items() if k not in {"emitted_at", "files"}} == {k: v for k, v in b.items() if k not in {"emitted_at", "files"}}
        assert [f["format"] for f in a["files"]] == [f["format"] for f in b["files"]]
    ctx.write = True
    assert invoke(ctx, "informes", "automaticos", "ejecutar").data == []


def test_series_summary_export(case):
    ctx, _, _, _ = case
    full = invoke(ctx, "control", "curva-s", "--periodo", "dia").data
    ctx.write = True
    target = ctx.data_dir / "curva.csv"
    reduced = invoke(ctx, "control", "curva-s", "--periodo", "dia", "--salida-serie", str(target)).data
    assert len(reduced["labels"]) < reduced["total_points"] == len(full["labels"])
    assert {"start", "cutoff", "finish", "milestone"} <= {r for p in reduced["key_points"] for r in p["reasons"]}
    with target.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert [r["labels"] for r in rows] == full["labels"]
    assert [float(r["planned_abs"]) for r in rows] == full["planned_abs"]
    for i, label in enumerate(reduced["labels"]):
        assert reduced["planned_abs"][i] == full["planned_abs"][full["labels"].index(label)]
    ctx.write = False
    simulated = invoke(ctx, "control", "curva-s", "--salida-serie", str(ctx.data_dir / "no.csv")).data
    assert simulated["series_file"]["simulated"]
    assert not (ctx.data_dir / "no.csv").exists()


@pytest.mark.parametrize("values", [
    {"actual_hours": -1},
    {"actual_hours": None},
    {"actual_start": "2027-02-07"},
    {"percent_complete": 101},
])
def test_progress_validation_matches_api(case, values):
    ctx, client, pid, activity = case
    row = dict(activity_id=activity.id, date="2027-02-06", percent_complete=50)
    row.update(values)
    before = snapshot(ctx)
    expected = client.post(f"/api/projects/{pid}/progress", json=row)
    assert expected.status_code == 422
    with pytest.raises(CliError) as error:
        invoke(ctx, "control", "avance", "registrar", "--json", json.dumps(row))
    assert error.value.code == "datos_invalidos"
    assert error.value.detail["errors"][0]["row"] == 1
    assert snapshot(ctx) == before


def test_scale_100000(case):
    ctx, client, pid, _ = case
    from ganttier import db
    from ganttier.control.cache import clear
    with closing(db.connect(ctx.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM activities WHERE project_id=?", (pid,)).fetchone()[0]
        conn.executemany("""INSERT INTO activities(project_id,code,name,duration_days,work_hours,planned_start,planned_finish)
            VALUES (?,?,?,1,8,'2027-02-02','2027-02-02')""",
            ((pid, f"PERF-{i}", f"Actividad {i}") for i in range(100000 - count)))
        # Medir el alcance completo; la línea base demo solo cubre sus actividades.
        conn.execute("UPDATE projects SET active_baseline_id=NULL WHERE id=?", (pid,))
        conn.commit()
    clear()
    start = time.perf_counter()
    page = invoke(ctx, "control", "actividades", "--alcance", "todas", "--pagina", "500").data
    paged = time.perf_counter() - start
    start = time.perf_counter()
    dashboard = invoke(ctx, "control", "tablero").data
    cold = time.perf_counter() - start
    start = time.perf_counter()
    assert invoke(ctx, "control", "tablero").data == dashboard
    warm = time.perf_counter() - start
    assert page["total"] == 100000 and len(page["rows"]) <= 200
    assert page == client.get(f"/api/projects/{pid}/control/activities", params={"scope": "all", "offset": 99800, "limit": 200}).json()
    assert dashboard["evm"] == client.get(f"/api/projects/{pid}/control/evm").json()
    assert dashboard["scurve"] == client.get(f"/api/projects/{pid}/control/scurve").json()
    evidence = {"activities": page["total"], "page_s": paged, "dashboard_cold_s": cold, "dashboard_warm_s": warm}
    (TEMP / "performance.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
