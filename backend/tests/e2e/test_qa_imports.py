"""Contratos Excel/repositorio verificables sin el adaptador HTTP."""
from pathlib import Path

import pytest

from ganttier import db, repo, services
from ganttier.excel_io import parse_workbook

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('path',sorted((ROOT/'ejemplos').glob('*.xlsx'))+sorted((ROOT/'plantillas').glob('*.xlsx')),ids=lambda p:p.name)
def test_workbook_references_and_persistence(tmp_path,monkeypatch,path):
    monkeypatch.setenv('GANTTIER_DB',str(tmp_path/'import.db'))
    monkeypatch.setenv('GANTTIER_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(services,'DATA_DIR',tmp_path)
    conn = db.connect()
    try:
        db.initialize(conn)
        bundle = parse_workbook(path)
        assert not bundle.has_errors, bundle.issues
        p = repo.create(conn,'projects',dict(name=path.stem,mode=bundle.mode,
            start_date=bundle.project['start_date'],end_date=bundle.project.get('end_date'),
            data_date=bundle.project.get('data_date'),calendar_id=5))
        progress = bundle.progress
        if bundle.mode == 'mantenimiento': bundle.progress = []
        repo.save_import_bundle(conn,p.id,bundle)
        if bundle.mode == 'mantenimiento':
            p = repo.get(conn,'projects',p.id)
            services.generate_maintenance(conn,p.id,p.start_date,p.end_date)
            codes = set(repo.activities_columnar(conn,p.id)['codes'])
            assert all(row['activity_code'] in codes for row in progress)
        else:
            assert len(repo.list_entities(conn,'progress',p.id)) == len(progress)
        assert repo.activities_columnar(conn,p.id)['ids']
    finally:
        conn.close()
