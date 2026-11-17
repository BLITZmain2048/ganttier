"""Banco reproducible; el reloj incluye construcción, XML, ZIP y guardado."""
from dataclasses import asdict
from datetime import date, timedelta
import json
from time import perf_counter

import pytest

from ganttier.domain import Activity
from ganttier.excel_io import export_project, parse_workbook
from ganttier.excel_io.templates import example_bundle


def maintenance_bundle(count):
    bundle = example_bundle('mantenimiento')
    prototype = asdict(Activity(1, 1, 'A', 'Inspección eléctrica', work_hours=8,
                               cost_budget=120000, voltage_kv=220,
                               location='S/E Cordillera', required_skill='SSEE'))
    for key in ('id', 'project_id', 'wbs_id', 'calendar_id', 'periodic_task_id'):
        prototype.pop(key)
    for i in range(count):
        start = date(2026, 10, 1) + timedelta(days=i % 90)
        code = f'PM001-{i + 1:06d}'
        bundle.activities.append(dict(prototype, code=code, name=f'Inspección eléctrica {i}',
            calendar_name=bundle.project['calendar_name'], periodic_task_code='PM001', occurrence_index=i + 1,
            planned_start=start, planned_finish=start, due_date=start, equipment=f'EQ-{i}'))
        bundle.assignments.append(dict(activity_code=code, resource_name=bundle.resources[0]['name'],
                                       units=1, work_hours=8, actual_hours=0))
    return bundle


@pytest.mark.slow
def test_export_100000_activities(tmp_path):
    bundle = maintenance_bundle(100000)
    start = perf_counter()
    path = export_project(bundle, tmp_path / '100000.xlsx')
    elapsed = perf_counter() - start
    (tmp_path / 'benchmark.json').write_text(json.dumps(
        dict(rows=100000, assignments=100000, elapsed_s=elapsed, limit_s=20)), encoding='utf-8')
    print(f'\nExportación XLSX 100.000 actividades: {elapsed:.3f} s', flush=True)
    assert elapsed < 20, f'Exportación: {elapsed:.3f} s; límite: 20 s'
    actual = parse_workbook(path)
    assert not actual.issues, actual.issues[:5]
    assert actual == bundle


def test_maintenance_occurrences_exact_roundtrip(tmp_path):
    bundle = maintenance_bundle(3)
    assert parse_workbook(export_project(bundle, tmp_path / 'roundtrip.xlsx')) == bundle


@pytest.mark.slow
def test_export_100000_with_22339_master_tasks(tmp_path):
    """Incluye la cardinalidad del plan maestro encontrada en la BD de QA."""
    from openpyxl import load_workbook
    bundle = maintenance_bundle(100000)
    prototype = bundle.periodic_tasks[0]
    bundle.periodic_tasks.extend(dict(prototype, code=f'PM{i:06d}',
        name=f'Mantenimiento de equipo {i}', equipment=f'EQ-{i}') for i in range(7, 22340))
    start = perf_counter()
    path = export_project(bundle, tmp_path / 'large-master.xlsx')
    elapsed = perf_counter() - start
    (tmp_path / 'benchmark.json').write_text(json.dumps(dict(
        rows=100000, assignments=100000, periodic_tasks=22339, elapsed_s=elapsed, limit_s=20)), encoding='utf-8')
    print(f'\nExportación 100k + 22.339 tareas maestras: {elapsed:.3f} s', flush=True)
    assert elapsed < 20, f'Exportación con maestros: {elapsed:.3f} s; límite: 20 s'
    wb = load_workbook(path, read_only=True)
    try:
        rows = wb['Plan_Maestro'].iter_rows(min_row=2, values_only=True)
        count, last = 0, None
        for last in rows:
            count += 1
        assert count == 22339 and last[0] == 'PM022339'
    finally:
        wb.close()
