"""Los análisis (EVM por grupo, pronósticos) usan las mismas entradas que el control (calendarios por actividad)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from ganttier import db, repo, services
from ganttier.main import create_app


def test_evm_por_grupo_coincide_con_evm_del_proyecto_con_calendarios_mixtos(tmp_path, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "c.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    with TestClient(create_app()) as client:
        conn = db.connect()
        lv = repo.create(conn, "calendars", {"name": "LV prueba", "workweek": "1111100", "holidays_country": None})
        siete = repo.create(conn, "calendars", {"name": "7d prueba", "workweek": "1111111", "holidays_country": None})
        p = repo.create(conn, "projects", {"name": "Mixto", "start_date": date(2026, 1, 5), "data_date": date(2026, 1, 9),
                                            "calendar_id": lv.id})
        repo.create(conn, "activities", {"project_id": p.id, "code": "A", "name": "LV", "location": "Norte", "duration_days": 4,
                                         "work_hours": 32, "planned_start": date(2026, 1, 9), "planned_finish": date(2026, 1, 14)})
        repo.create(conn, "activities", {"project_id": p.id, "code": "B", "name": "7d", "location": "Sur", "duration_days": 4,
                                         "work_hours": 32, "calendar_id": siete.id,
                                         "planned_start": date(2026, 1, 9), "planned_finish": date(2026, 1, 12)})
        conn.close()
        total = client.get(f"/api/projects/{p.id}/control/evm").json()
        by = client.get(f"/api/projects/{p.id}/control/evm-by", params={"group": "location"}).json()
        assert by["reconciliation"]["matches"]
        assert by["total"]["PV"] == pytest.approx(total["PV"])
        # B (7 días) reparte 32 HH en 4 jornadas corridas: al corte del viernes 9 lleva 8 HH planificadas.
        sur = next(g for g in by["groups"] if g["name"] == "Sur")
        assert sur["evm"]["PV"] == pytest.approx(8)
