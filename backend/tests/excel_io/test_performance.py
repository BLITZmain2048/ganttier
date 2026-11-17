"""Criterio de aceptación: lectura de 100 000 filas en menos de 60 segundos."""
from datetime import date
from time import perf_counter
import json
import pytest

from openpyxl import Workbook

from ganttier.excel_io import parse_workbook
from ganttier.excel_io.schema import SCHEMAS


@pytest.mark.slow
def test_import_100000_rows(tmp_path):
    workbook = Workbook(write_only=True)
    project = workbook.create_sheet("Proyecto")
    for row in [("Clave", "Valor"), ("Nombre", "Banco de rendimiento 100 000"), ("Modo", "proyecto"), ("Fecha_Inicio", date(2026, 1, 1))]:
        project.append(row)
    activities = workbook.create_sheet("Actividades")
    cols = SCHEMAS["Actividades"]
    activities.append([c.title for c in cols])
    for i in range(100000):
        record = dict(code=f"A{i:06d}", name=f"Inspección eléctrica {i}", duration_days=1, work_hours=8, cost_budget=120000, macro_month="2026-10", window_hard=False, constraint_type="ASAP", priority=50, required_skill="SSEE", crew_size=1, location="S/E Cordillera", equipment=f"EQ-{i}", voltage_kv=220, requires_outage=False, discipline="Eléctrica", notes="Prueba de carga")
        activities.append([record.get(c.field) for c in cols])
    path = tmp_path / "100000.xlsx"
    workbook.save(path)
    start = perf_counter()
    bundle = parse_workbook(path)
    elapsed = perf_counter() - start
    (tmp_path / "benchmark.json").write_text(json.dumps({"rows": len(bundle.activities), "elapsed_s": elapsed, "limit_s": 60, "issues": len(bundle.issues)}, indent=2), encoding="utf-8")
    print(f"\nImportación de 100 000 filas: {elapsed:.3f} s")
    assert not bundle.issues, bundle.issues[:5]
    assert len(bundle.activities) == 100000
    assert elapsed < 60, f"Importación: {elapsed:.3f} s; límite: 60 s"
