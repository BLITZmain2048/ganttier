"""v3 → v4 conserva los datos y usa el respaldo SHA-256 existente."""
import hashlib
import json
import pytest
from ganttier import db, migrations


def version_three(path):
    conn = db.connect(path)
    conn.executescript(db.Path(db.__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    migrations._v2(conn)
    migrations._v3(conn)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version','3')")
    conn.execute("INSERT INTO projects(id,name,start_date) VALUES(1,'Existente','2027-01-01')")
    conn.execute("INSERT INTO periodic_tasks(id,project_id,code,name) VALUES(1,1,'T','Tradicional')")
    conn.commit()
    return conn


def test_v4_preserves_v3_and_backup(tmp_path):
    conn = version_three(tmp_path / "v3.db")
    assert migrations.migrate(conn) == [4, 5, 6]
    assert migrations.current_version(conn) == 6
    assert tuple(conn.execute("SELECT quantity_total,team_ids,balance_tolerance_batches FROM periodic_tasks").fetchone()) == (None, "[]", 1)
    assert migrations.migrate(conn) == []
    manifests = list((tmp_path / "respaldos").glob("*.json"))
    assert len(manifests) == 1
    metadata = json.loads(manifests[0].read_text(encoding="utf-8"))
    backup = manifests[0].parent / metadata["file"]
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == metadata["sha256"]
    old = db.connect(backup)
    assert migrations.current_version(old) == 3
    old.close()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_v4_failure_rolls_back(tmp_path, monkeypatch):
    conn = version_three(tmp_path / "rollback.db")
    def fail(connection):
        migrations._v4(connection)
        raise RuntimeError("interrupción simulada")
    monkeypatch.setattr(migrations, "STEPS", [(4, "equipos", fail)])
    with pytest.raises(RuntimeError, match="simulada"):
        migrations.migrate(conn)
    assert migrations.current_version(conn) == 3
    assert "quantity_total" not in {row[1] for row in conn.execute("PRAGMA table_info(periodic_tasks)")}
    conn.close()


def test_v5_default_preserves_existing_dates_and_rolls_back_failure(tmp_path, monkeypatch):
    conn = version_three(tmp_path / "v4.db")
    migrations._v4(conn)
    conn.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    conn.execute("INSERT INTO activities(id,project_id,code,name,planned_start,planned_finish) VALUES(1,1,'A','Guardada','2027-01-04','2027-01-05')")
    conn.commit()
    old_columns = ','.join(row[1] for row in conn.execute('PRAGMA table_info(activities)'))
    original = [tuple(row) for row in conn.execute(f'SELECT {old_columns} FROM activities')]
    def fail(connection):
        migrations._v5(connection)
        raise RuntimeError('fallo v5')
    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'STEPS', [(5, 'continuidad', fail)])
        with pytest.raises(RuntimeError, match='fallo v5'):
            migrations.migrate(conn)
    assert migrations.current_version(conn) == 4
    assert 'batch_continuous_in_shift' not in {row[1] for row in conn.execute('PRAGMA table_info(periodic_tasks)')}
    assert migrations.migrate(conn) == [5, 6]
    assert conn.execute('SELECT batch_continuous_in_shift FROM periodic_tasks').fetchone()[0] == 0
    assert [tuple(row) for row in conn.execute(f'SELECT {old_columns} FROM activities')] == original
    assert tuple(conn.execute('SELECT duration_hours,checklist_codes FROM activities').fetchone()) == (None, '[]')
    assert migrations.migrate(conn) == []
    conn.close()


def test_v6_failure_rolls_back_and_preserves_saved_schedule(tmp_path, monkeypatch):
    conn = version_three(tmp_path / 'v5.db')
    migrations._v4(conn)
    migrations._v5(conn)
    conn.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
    conn.execute("INSERT INTO activities(id,project_id,code,name,planned_start,planned_finish) VALUES(1,1,'A','Guardada','2027-01-04','2027-01-05')")
    conn.commit()
    old_columns = ','.join(row[1] for row in conn.execute('PRAGMA table_info(activities)'))
    original = [tuple(row) for row in conn.execute(f'SELECT {old_columns} FROM activities')]

    def fail(connection):
        migrations._v6(connection)
        raise RuntimeError('fallo v6')

    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'STEPS', [(6, 'horas y rutas', fail)])
        with pytest.raises(RuntimeError, match='fallo v6'):
            migrations.migrate(conn)
    assert migrations.current_version(conn) == 5
    assert 'duration_hours' not in {row[1] for row in conn.execute('PRAGMA table_info(activities)')}
    assert 'route_code' not in {row[1] for row in conn.execute('PRAGMA table_info(periodic_tasks)')}
    assert migrations.migrate(conn) == [6]
    assert [tuple(row) for row in conn.execute(f'SELECT {old_columns} FROM activities')] == original
    assert tuple(conn.execute('SELECT batch_full_day,productivity_hour_team,productive_hours_day,route_code,is_route,is_daily_round FROM periodic_tasks').fetchone()) == (0, None, None, '', 0, 0)
    assert migrations.migrate(conn) == []
    conn.close()
