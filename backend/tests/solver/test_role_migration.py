"""Upgrade a real v2 database through the existing transactional migration chain."""
import sqlite3

from ganttier import db, migrations


def test_v2_roles_migration_preserves_demands_and_is_idempotent(tmp_path):
    path = tmp_path/'v2.db'
    conn = db.connect(path)
    conn.executescript(db.Path(db.__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    migrations._v2(conn)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version','2')")
    conn.execute("INSERT INTO projects(id,name,start_date) VALUES (1,'Existing','2027-01-01')")
    conn.execute("INSERT INTO activities(id,project_id,code,name) VALUES (1,1,'A','A')")
    conn.execute("INSERT INTO assignments(activity_id,skill,units) VALUES (1,'I',2)")
    conn.commit()
    original = [tuple(r) for r in conn.execute('SELECT * FROM assignments')]
    assert migrations.migrate(conn) == [3, 4, 5, 6]
    assert migrations.current_version(conn) == db.SCHEMA_VERSION == 6
    assert [tuple(r) for r in conn.execute('SELECT * FROM assignments')] == original
    assert conn.execute('SELECT COUNT(*) FROM role_allocations').fetchone()[0] == 0
    assert migrations.migrate(conn) == []
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()


def test_v3_failure_rolls_back_tables_and_version(tmp_path, monkeypatch):
    conn = db.connect(tmp_path/'rollback.db')
    conn.executescript(db.Path(db.__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    migrations._v2(conn)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version','2')")
    conn.commit()
    real = migrations._v3

    def broken(connection):
        real(connection)
        raise RuntimeError('injected after role tables')

    monkeypatch.setattr(migrations, 'STEPS', [(3, 'roles', broken)])
    import pytest
    with pytest.raises(RuntimeError, match='injected'):
        migrations.migrate(conn)
    assert migrations.current_version(conn) == 2
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'role_%'").fetchall()
    conn.close()
