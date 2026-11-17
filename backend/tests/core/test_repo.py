from datetime import date, datetime
import sqlite3
import time

import pytest

from ganttier import db, domain as d, repo, services


def test_connection_migration_idempotency(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    db.initialize(conn)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    repo.set_meta(conn, "test", "dato")
    assert {r["key"]: r["value"] for r in repo.list_entities(conn, "meta")}["test"] == "dato"
    repo.set_meta(conn, "test", None)


def test_crud_macro_exceptions_and_dates(conn, project):
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "A", "name": "Actividad", "macro_month": "2028-02"})
    assert activity.window_start == date(2028, 2, 1)
    assert activity.window_end == date(2028, 2, 29)
    changed = repo.update(conn, "activities", activity.id, {"macro_month": "2026-04"})
    assert changed.window_end == date(2026, 4, 30)
    changed = repo.update(conn, "activities", activity.id, {"window_end": date(2026, 5, 10)})
    assert changed.macro_month is None
    cal = repo.create(conn, "calendars", d.Calendar(0, "Excepciones", holidays_country=None, exceptions=[d.CalendarException(date(2026, 1, 1), True, 5, "Especial")]))
    assert repo.get(conn, "calendars", cal.id).exceptions[0].date == date(2026, 1, 1)
    assert repo.list_entities(conn, "calendars")[-1].exceptions[0].hours == 5
    repo.delete(conn, "activities", activity.id)
    with pytest.raises(LookupError):
        repo.get(conn, "activities", activity.id)


def test_bulk_is_atomic_and_cross_project_references_rejected(conn, project):
    other = repo.create(conn, "projects", {"name": "Otro", "start_date": "2026-01-01"})
    foreign_wbs = repo.create(conn, "wbs", {"project_id": other.id, "code": "1", "name": "Ajena"})
    with pytest.raises(ValueError, match="otro proyecto"):
        repo.create(conn, "activities", {"project_id": project.id, "code": "X", "name": "X", "wbs_id": foreign_wbs.id})
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_many(conn, "activities", [{"project_id": project.id, "code": "A", "name": "A"}] * 2)
    assert repo.list_entities(conn, "activities", project.id) == []
    global_resource = repo.create(conn, "resources", {"name": "Compartido"})
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "A", "name": "A"})
    repo.create(conn, "assignments", {"activity_id": activity.id, "resource_id": global_resource.id})
    assert repo.list_entities(conn, "resources", project.id)[0].id == global_resource.id


def test_multiple_chunks_share_one_transaction(conn, project):
    rows = [{"project_id": project.id, "code": str(i), "name": "Prueba"} for i in range(2001)]
    rows[-1]["code"] = "0"
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_many(conn, "activities", rows)
    assert repo.list_entities(conn, "activities", project.id) == []


def test_baseline_datetime_regression_and_duplication(conn, project):
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "A", "name": "A", "planned_start": "2026-01-05", "planned_finish": "2026-01-07", "work_hours": 24})
    baseline = services.create_baseline(conn, project.id)
    assert isinstance(baseline.created_at, datetime)
    assert repo.activities_columnar(conn, project.id)["baseline_finishes"] == ["2026-01-07"]
    copy = repo.duplicate_project(conn, project.id)
    assert copy.name.endswith("(copia)")
    assert copy.active_baseline_id != baseline.id
    copied = repo.list_entities(conn, "activities", copy.id)[0]
    assert copied.id != activity.id
    assert repo.activities_columnar(conn, copy.id)["baseline_starts"] == ["2026-01-05"]
    with pytest.raises(ValueError, match="otro proyecto"):
        repo.update(conn, "projects", copy.id, {"active_baseline_id": baseline.id})
    repo.delete(conn, "baselines", copy.active_baseline_id)
    assert repo.get(conn, "projects", copy.id).active_baseline_id is None


def test_import_export_roundtrip_merge_replace_and_rollback(conn, project):
    bundle = d.ImportBundle("proyecto", {"name": "Desde Excel"},
        resources=[{"name": "Ana", "skills": ["SSEE"], "calendar_name": "Chile L-V 8h"}],
        wbs=[{"code": "1.1", "name": "Hija", "parent_code": "1"}, {"code": "1", "name": "Raíz"}],
        activities=[{"code": "A", "name": "Primera", "wbs_code": "1.1", "macro_month": "2026-02"}, {"code": "B", "name": "Segunda"}],
        relations=[{"pred_code": "A", "succ_code": "B", "type": "SS", "lag_days": 2}],
        assignments=[{"activity_code": "A", "resource_name": "Ana", "work_hours": 16}],
        progress=[{"activity_code": "A", "resource_name": "Ana", "date": "2026-01-05", "percent_complete": 10, "actual_hours": 2}])
    repo.save_import_bundle(conn, project.id, bundle)
    ids = [a.id for a in repo.list_entities(conn, "activities", project.id)]
    repo.save_import_bundle(conn, project.id, bundle)
    assert [a.id for a in repo.list_entities(conn, "activities", project.id)] == ids
    assert len(repo.list_entities(conn, "assignments", project.id)) == 1
    assert len(repo.list_entities(conn, "progress", project.id)) == 1
    roundtrip = repo.export_bundle(conn, project.id)
    assert roundtrip.relations[0]["pred_code"] == "A"
    assert roundtrip.activities[0]["wbs_code"] == "1.1"
    assert roundtrip.wbs[0]["parent_code"] == "1"
    repo.save_import_bundle(conn, project.id, roundtrip, "replace")
    assert len(repo.list_entities(conn, "activities", project.id)) == 2
    bad = d.ImportBundle("proyecto", {}, activities=[{"code": "X", "name": "X", "wbs_code": "inexistente"}])
    with pytest.raises(ValueError, match="WBS"):
        repo.save_import_bundle(conn, project.id, bad, "replace")
    assert len(repo.list_entities(conn, "activities", project.id)) == 2


def test_wbs_cycles_and_atomic_patch(conn, project):
    parent = repo.create(conn, "wbs", {"project_id": project.id, "code": "1", "name": "Padre"})
    child = repo.create(conn, "wbs", {"project_id": project.id, "code": "2", "name": "Hijo", "parent_id": parent.id})
    with pytest.raises(ValueError, match="ciclo"):
        repo.update(conn, "wbs", parent.id, {"parent_id": child.id})
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "A", "name": "Original"})
    with pytest.raises(LookupError):
        repo.patch_activities(conn, project.id, [{"id": activity.id, "name": "Editada"}, {"id": 999999, "name": "Falla"}])
    assert repo.get(conn, "activities", activity.id).name == "Original"


def test_occurrence_persistence(conn, project):
    task = repo.create(conn, "periodic_tasks", {"project_id": project.id, "code": "MT", "name": "Ensayo", "cost_per_occurrence": 12000, "voltage_kv": 220})
    occurrence = d.Occurrence(task.id, 1, "MT-0001", "Ensayo", date(2026, 2, 10), date(2026, 2, 1), date(2026, 2, 15), 2, 32, 2, "SSEE", None, project.calendar_id)
    saved = repo.save_occurrences(conn, project.id, [occurrence])
    assert saved[0].cost_budget == 12000
    assert saved[0].voltage_kv == 220
    assert repo.list_entities(conn, "assignments", project.id)[0].skill == "SSEE"
    assert repo.save_occurrences(conn, project.id, [occurrence], replace=False) == []


def test_100000_activities_and_single_select_columnar(conn, project):
    rows = [{"project_id": project.id, "code": f"A-{i}", "name": f"Actividad {i}", "duration_days": 1, "macro_month": "2026-06" if i % 7 == 0 else None} for i in range(100000)]
    started = time.perf_counter()
    activities = repo.create_many(conn, "activities", rows)
    insert_seconds = time.perf_counter() - started
    assert len(activities) == 100000
    assert insert_seconds < 20
    statements = []
    conn.set_trace_callback(statements.append)
    started = time.perf_counter()
    result = repo.activities_columnar(conn, project.id)
    columnar_seconds = time.perf_counter() - started
    conn.set_trace_callback(None)
    assert columnar_seconds < 3
    assert len(result["ids"]) == 100000
    assert all(len(column) == 100000 for column in result.values())
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1
    print(f"100k: alta={insert_seconds:.3f}s; Gantt columnar={columnar_seconds:.3f}s")
