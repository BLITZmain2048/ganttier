"""D3 (borrados que alteran la historia), D4 (corte de informes), migraciones (P4-07) y respaldos (P4-06a)."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from ganttier import backups, db, migrations, repo, services


# ------------------------------------------------------------------------------------------- D3
def _activity_in_baseline(conn, project, code="A"):
    act = repo.create(conn, "activities", {"project_id": project.id, "code": code, "name": "Tendido",
                                           "duration_days": 5, "work_hours": 40,
                                           "planned_start": "2026-01-05", "planned_finish": "2026-01-09"})
    services.create_baseline(conn, project.id, "LB0")
    return act


def test_d3_borrar_actividad_de_linea_base_requiere_confirmacion(conn, project):
    act = _activity_in_baseline(conn, project)
    with pytest.raises(repo.ProtectedDeleteError) as info:
        repo.delete(conn, "activities", act.id)
    assert "línea(s) base" in str(info.value) and info.value.history["baseline_rows"] == 1
    assert repo.get(conn, "activities", act.id)  # sigue existiendo
    repo.delete(conn, "activities", act.id, force=True)
    with pytest.raises(LookupError):
        repo.get(conn, "activities", act.id)
    log = conn.execute("SELECT action, entity, entity_id FROM audit_log").fetchall()
    assert [tuple(r) for r in log] == [("eliminacion_forzada", "activities", act.id)]


def test_d3_actividad_sin_historia_se_borra_sin_friccion(conn, project):
    act = repo.create(conn, "activities", {"project_id": project.id, "code": "B", "name": "Libre"})
    repo.delete(conn, "activities", act.id)
    assert not conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]


def test_d3_avance_registrado_tambien_protege(conn, project):
    act = repo.create(conn, "activities", {"project_id": project.id, "code": "C", "name": "Con avance",
                                           "planned_start": "2026-01-05", "planned_finish": "2026-01-09", "work_hours": 16})
    repo.create(conn, "progress", {"activity_id": act.id, "date": "2026-01-06", "percent_complete": 20, "actual_hours": 6})
    assert repo.activity_history(conn, [act.id])["progress"] == 1
    with pytest.raises(repo.ProtectedDeleteError):
        repo.delete(conn, "activities", act.id)


def test_d3_api_responde_409_y_force_borra(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import importlib
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    import ganttier.main as main
    importlib.reload(main)
    with TestClient(main.create_app()) as client:
        p = client.post("/api/projects", json={"name": "P", "start_date": "2026-01-05"}).json()
        a = client.post(f"/api/projects/{p['id']}/activities", json=[{"code": "A", "name": "A", "work_hours": 8,
                        "planned_start": "2026-01-05", "planned_finish": "2026-01-05"}]).json()[0]
        assert client.post(f"/api/projects/{p['id']}/baselines", json={"name": "LB"}).status_code == 201
        assert client.get(f"/api/activities/{a['id']}/history").json()["baseline_rows"] == 1
        r = client.delete(f"/api/activities/{a['id']}")
        assert r.status_code == 409 and r.json()["protected"] and r.json()["history"]["baselines"] == 1
        assert client.delete(f"/api/activities/{a['id']}", params={"force": "true"}).status_code == 204


# ------------------------------------------------------------------------------------------- D4
def test_d4_informe_de_periodo_cerrado_no_usa_avances_posteriores(conn, project):
    act = repo.create(conn, "activities", {"project_id": project.id, "code": "M", "name": "Montaje", "duration_days": 20,
                                           "work_hours": 100, "planned_start": "2026-02-02", "planned_finish": "2026-02-27"})
    services.create_baseline(conn, project.id, "LB")
    repo.create(conn, "progress", {"activity_id": act.id, "date": "2026-02-06", "percent_complete": 20, "actual_hours": 20})
    repo.create(conn, "progress", {"activity_id": act.id, "date": "2026-03-04", "percent_complete": 90, "actual_hours": 70})
    repo.update(conn, "projects", project.id, {"data_date": date(2026, 3, 4)})
    services.sync_progress(conn, project.id)
    february = services.build_report_data(conn, project.id, "monthly", date(2026, 2, 1))
    assert february.project.data_date == date(2026, 2, 28)
    assert february.evm.data_date == date(2026, 2, 28)
    assert february.evm.EV == pytest.approx(20) and february.evm.AC == pytest.approx(20)
    assert "corte 28-02-2026" in february.notes
    march = services.build_report_data(conn, project.id, "monthly", date(2026, 3, 1))
    assert march.project.data_date == date(2026, 3, 4) and march.evm.EV == pytest.approx(90)
    # Regenerar febrero después de más avances entrega las mismas cifras.
    repo.create(conn, "progress", {"activity_id": act.id, "date": "2026-03-10", "percent_complete": 100, "actual_hours": 10})
    again = services.build_report_data(conn, project.id, "monthly", date(2026, 2, 1))
    assert (again.evm.EV, again.evm.AC, again.evm.PV) == (february.evm.EV, february.evm.AC, february.evm.PV)


# ------------------------------------------------------------------------------- Migraciones P4-07
def _v1_database(path):
    """Base creada por la versión anterior de Ganttier (solo schema.sql, versión 1)."""
    conn = sqlite3.connect(path)
    schema = (db.Path(db.__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    conn.executescript(schema + "\nINSERT OR REPLACE INTO meta VALUES ('schema_version','1');")
    conn.execute("INSERT INTO calendars(name) VALUES ('L-V')")
    conn.execute("INSERT INTO projects(name, start_date, calendar_id) VALUES ('Antiguo', '2026-01-05', 1)")
    conn.execute("INSERT INTO activities(project_id, code, name) VALUES (1, 'A', 'Existente')")
    conn.commit()
    conn.close()


def _schema(conn):
    return sorted((r[0], r[1]) for r in conn.execute("SELECT name, COALESCE(sql,'') FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))


def test_migracion_v1_y_base_nueva_quedan_iguales(tmp_path):
    old = tmp_path / "antigua.db"
    _v1_database(old)
    upgraded = db.connect(old)
    db.initialize(upgraded)
    fresh = db.connect(tmp_path / "nueva.db")
    db.initialize(fresh)
    assert migrations.current_version(upgraded) == migrations.current_version(fresh) == db.SCHEMA_VERSION == migrations.LATEST
    assert _schema(upgraded) == _schema(fresh)
    assert upgraded.execute("SELECT name FROM activities").fetchone()[0] == "Existente"  # sin pérdida de datos
    # Se creó un respaldo verificado antes de migrar la base con datos.
    pre = [b for b in backups.list_backups(tmp_path / "respaldos") if b.kind == "pre"]
    assert len(pre) == 1 and pre[0].schema_version == 1 and pre[0].verified
    assert migrations.migrate(upgraded) == []  # reiniciar no repite pasos
    upgraded.close(); fresh.close()


def test_migracion_fallida_deja_la_version_anterior(tmp_path, monkeypatch):
    path = tmp_path / "falla.db"
    _v1_database(path)

    def broken(conn):
        conn.execute("CREATE TABLE parcial (x)")
        raise RuntimeError("falla inducida a mitad del paso")
    monkeypatch.setattr(migrations, "STEPS", [(2, "rota", broken)])
    monkeypatch.setattr(migrations, "LATEST", 2)
    conn = db.connect(path)
    with pytest.raises(RuntimeError):
        migrations.migrate(conn)
    assert migrations.current_version(conn) == 1
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name='parcial'").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
    conn.close()


def test_base_de_version_futura_se_rechaza_sin_cambios(tmp_path):
    path = tmp_path / "futura.db"
    conn = db.connect(path)
    db.initialize(conn)
    conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    conn.commit()
    tables = _schema(conn)
    with pytest.raises(ValueError, match="más reciente"):
        db.initialize(conn)
    assert _schema(conn) == tables
    conn.close()


# --------------------------------------------------------------------------------- Respaldos P4-06a
def test_respaldo_con_escrituras_wal_activas_es_consistente(conn, project, local_dir):
    repo.create(conn, "activities", {"project_id": project.id, "code": "W", "name": "En WAL"})
    other = db.connect(local_dir / "test.db")  # otra conexión con una transacción abierta
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO activities(project_id, code, name) VALUES (?, 'NOCONF', 'Sin confirmar')", (project.id,))
    info = backups.create_backup(conn, "prueba", "manual")
    other.rollback(); other.close()
    copy = sqlite3.connect(local_dir / "respaldos" / info.file)
    codes = {r[0] for r in copy.execute("SELECT code FROM activities")}
    copy.close()
    assert "W" in codes and "NOCONF" not in codes
    assert info.verified and backups.verify_file(local_dir / "respaldos" / info.file)[0]
    assert backups.last_backup(local_dir / "respaldos").file == info.file


def test_respaldo_diario_y_retencion(conn, project, local_dir):
    first = backups.ensure_daily(conn)
    assert first is not None and first.kind == "auto"
    assert backups.ensure_daily(conn) is None  # menos de 24 h
    assert backups.ensure_daily(conn, now=datetime.now() + timedelta(hours=25)) is not None
    directory = local_dir / "respaldos"
    for i in range(14):
        backups.create_backup(conn, f"m{i}", "pre")
    assert len([b for b in backups.list_backups(directory) if b.kind == "pre"]) == backups.RETENTION["pre"]


def test_restauracion_programada_se_aplica_al_iniciar(local_dir):
    path = local_dir / "r.db"
    conn = db.connect(path)
    db.initialize(conn)
    cid = repo.list_entities(conn, "calendars")[0].id
    repo.create(conn, "projects", {"name": "Original", "start_date": "2026-01-05", "calendar_id": cid})
    info = backups.create_backup(conn, "antes", "manual")
    repo.create(conn, "projects", {"name": "Error posterior", "start_date": "2026-01-05", "calendar_id": cid})
    backups.schedule_restore(info.file, path)
    assert backups.pending_restore(path) == info.file
    conn.close()
    assert backups.apply_pending_restore(path) == info.file
    restored = db.connect(path)
    assert [r[0] for r in restored.execute("SELECT name FROM projects")] == ["Original"]
    restored.close()
    # Se guardó la base reemplazada antes de restaurar.
    assert any(b.reason == "pre-restauracion" for b in backups.list_backups(local_dir / "respaldos"))
    assert backups.pending_restore(path) is None


# ------------------------------------------------------------------------------- Seguridad P4-14
def test_token_de_sesion_y_host(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import importlib
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "tok.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    import ganttier.main as main
    importlib.reload(main)
    with TestClient(main.create_app(token="secreto", instance="abc")) as client:
        health = client.get("/api/health")
        assert health.status_code == 200 and health.json()["instance"] == "abc"
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/projects", headers={"X-Ganttier-Token": "otro"}).status_code == 401
        assert client.get("/api/projects", headers={"X-Ganttier-Token": "secreto"}).status_code == 200
        assert client.post("/api/projects", json={"name": "X", "start_date": "2026-01-05"}).status_code == 401
        assert client.get("/api/projects", headers={"X-Ganttier-Token": "secreto", "Host": "evil.test"}).status_code == 400


def test_resumen_liviano_de_proyecto(conn, project):
    repo.create(conn, "activities", {"project_id": project.id, "code": "S1", "name": "Con fechas", "work_hours": 10,
                                     "planned_start": "2026-01-05", "planned_finish": "2026-01-09"})
    summary = services.project_summary(conn, project.id)
    assert summary["activities"] == 1 and summary["scheduled"] and summary["finish"] == "2026-01-09"
    assert summary["work_hours"] == 10 and summary["evm"]["BAC"] == 10
    repo.create(conn, "activities", {"project_id": project.id, "code": "S2", "name": "Sin fechas", "work_hours": 5})
    summary = services.project_summary(conn, project.id)
    assert not summary["scheduled"] and summary["unscheduled"] == 1 and summary["evm"] is None
