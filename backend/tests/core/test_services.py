from datetime import date, timedelta
import json
import sys
import types

import numpy as np

import pytest

from ganttier import domain as d, repo, services


def engine_stub(monkeypatch, **overrides):
    module = types.ModuleType("ganttier.engine")
    module.validate_network = lambda problem: []
    def compute(problem):
        entries = {a.id: d.CPMActivityResult(a.id, date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 5), date(2026, 1, 6), 0, 0, True) for a in problem.activities}
        return d.CPMResult(entries, date(2026, 1, 6), list(entries))
    module.compute_cpm = compute
    module.optimize = lambda problem, weights, options: d.OptimizeResult({a.id: date(2026, 1, 7) for a in problem.activities}, {a.id: date(2026, 1, 8) for a in problem.activities}, status="factible", method_used="stub")
    module.__dict__.update(overrides)
    monkeypatch.setitem(sys.modules, "ganttier.engine", module)
    return module


def control_stub(monkeypatch):
    module = types.ModuleType("ganttier.control")
    evm_fields = {f.name: None for f in d.EVMResult.__dataclass_fields__.values()}
    evm_fields.update(data_date=date(2026, 1, 15), basis="hours", BAC=100, PV=50, EV=40, AC=45, SV=-10, CV=-5, percent_planned=50, percent_complete=40)
    module.evm = lambda acts, baseline, progress, calendar, data_date, basis="hours", **kwargs: d.EVMResult(**evm_fields)
    module.scurve = lambda *args, **kwargs: d.SCurve(kwargs.get("period", "week"), "hours", [], [], [], [], [], [], [], [], [], 100, date(2026, 1, 15), None, None)
    module.worker_performance = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "ganttier.control", module)


def test_schedule_persistence_apply_and_project_isolation(conn, project, local_dir, monkeypatch):
    monkeypatch.setattr(services, "DATA_DIR", local_dir)
    engine_stub(monkeypatch)
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "A", "name": "A", "duration_days": 2})
    summary = services.run_cpm(conn, project.id)
    assert summary["critical_count"] == 1
    after_cpm = repo.get(conn, "activities", activity.id)
    assert after_cpm.critical and after_cpm.planned_start == date(2026, 1, 5)
    assert (local_dir / "runs" / f"{summary['run_id']}.json").is_file()
    optimized = services.run_optimize(conn, project.id)
    assert repo.get(conn, "activities", activity.id).planned_start == date(2026, 1, 5)
    services.apply_run(conn, project.id, optimized["run_id"])
    assert repo.get(conn, "activities", activity.id).planned_start == date(2026, 1, 7)
    with pytest.raises(LookupError):
        services.apply_run(conn, project.id + 100, optimized["run_id"])
    repo.delete(conn, "activities", activity.id)
    with pytest.raises(ValueError, match="eliminadas|cambió desde que se simuló"):
        services.apply_run(conn, project.id, optimized["run_id"])


def test_failed_network_is_not_saved(conn, project, monkeypatch):
    engine_stub(monkeypatch, validate_network=lambda problem: ["La red contiene un ciclo."])
    with pytest.raises(ValueError, match="ciclo"):
        services.run_cpm(conn, project.id)
    assert repo.list_entities(conn, "schedule_runs", project.id) == []


def test_numpy_metrics_are_serialized(conn, project, local_dir, monkeypatch):
    monkeypatch.setattr(services, "DATA_DIR", local_dir)
    metrics = d.OptimizeMetrics(date(2026, 1, 1), np.float64(8), 0, 0, 0, 0, np.int64(2), np.int64(1), 0)
    engine_stub(monkeypatch, optimize=lambda *args: d.OptimizeResult({}, {}, after=metrics, status="factible"))
    result = services.run_optimize(conn, project.id)
    assert json.loads(json.dumps(result))["after"]["days_out_of_window"] == 2
    assert repo.list_entities(conn, "schedule_runs", project.id)[0]["metrics_after"]["activities_out_of_window"] == 1


def test_missing_module_has_clear_error_and_demo_does_not_reimplement(conn, monkeypatch):
    monkeypatch.setitem(sys.modules, "ganttier.maintenance", None)
    with pytest.raises(services.ModuleUnavailable, match="mantenimiento"):
        services.demo_maintenance(100, conn)
    assert repo.list_entities(conn, "projects") == []


def test_load_profile_conserves_hours_and_clips_without_redistribution(conn, project):
    resource = repo.create(conn, "resources", {"project_id": project.id, "name": "Ana", "skills": ["SSEE"], "capacity_hours_day": 8, "calendar_id": project.calendar_id})
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "A", "name": "A", "duration_days": 4, "work_hours": 32, "planned_start": "2026-01-05", "planned_finish": "2026-01-08"})
    repo.create(conn, "assignments", {"activity_id": activity.id, "resource_id": resource.id, "work_hours": 32, "skill": "SSEE"})
    full = services.load_profile(conn, project.id, "resource", "day", date(2026, 1, 5), date(2026, 1, 8))
    assert full.series[0].load == [8, 8, 8, 8]
    assert full.series[0].capacity == [8, 8, 8, 8]
    clipped = services.load_profile(conn, project.id, "skill", "week", date(2026, 1, 6), date(2026, 1, 7))
    assert clipped.labels == ["2026-01-05"]
    assert clipped.series[0].key == "SSEE"
    assert clipped.series[0].load == [16]
    assert clipped.series[0].capacity == [16]


def test_progress_uses_cutoff_and_increments_once(conn, project):
    activity = repo.create(conn, "activities", {"project_id": project.id, "code": "A", "name": "A", "work_hours": 80})
    repo.create_many(conn, "progress", [{"activity_id": activity.id, "date": "2026-01-05", "percent_complete": 20, "actual_hours": 16, "actual_cost": 100}, {"activity_id": activity.id, "date": "2026-01-12", "percent_complete": 50, "actual_hours": 24, "actual_cost": 150}, {"activity_id": activity.id, "date": "2026-02-01", "percent_complete": 100, "actual_hours": 40, "actual_cost": 250}])
    view = services.build_activity_views(conn, project.id)[0]
    assert (view.percent_complete, view.actual_hours, view.actual_cost) == (50, 40, 250)
    problem = services.build_problem(conn, project.id)
    assert problem.activities[0].percent_complete == 50
    assert problem.activities[0].status == "en_curso"
    services.sync_progress(conn, project.id)
    assert repo.activities_columnar(conn, project.id)["pct"] == [50]
    repo.update(conn, "projects", project.id, {"data_date": date(2026, 1, 1)})
    services.sync_progress(conn, project.id)
    assert repo.activities_columnar(conn, project.id)["pct"] == [0]


@pytest.mark.parametrize("kind,start,end", [("weekly", date(2026, 1, 12), date(2026, 1, 18)), ("monthly", date(2026, 1, 1), date(2026, 1, 31)), ("annual", date(2026, 1, 1), date(2026, 12, 31))])
def test_report_periods_and_rows(conn, project, monkeypatch, kind, start, end):
    control_stub(monkeypatch)
    repo.create_many(conn, "activities", [
        {"project_id": project.id, "code": "A", "name": "Atrasada", "planned_start": "2026-01-05", "planned_finish": "2026-01-10"},
        {"project_id": project.id, "code": "B", "name": "En período", "planned_start": "2026-01-12", "planned_finish": "2026-01-16"},
        {"project_id": project.id, "code": "C", "name": "Terminada", "planned_start": "2026-01-12", "planned_finish": "2026-01-14", "actual_finish": "2026-01-14", "percent_complete": 100},
        {"project_id": project.id, "code": "H", "name": "Hito", "duration_days": 0, "planned_start": "2026-01-20", "planned_finish": "2026-01-20"},
    ])
    report = services.build_report_data(conn, project.id, kind, date(2026, 1, 14))
    assert (report.period_start, report.period_end) == (start, end)
    assert [row.code for row in report.delayed] == ["A"]
    assert [row.code for row in report.completed_in_period] == ["C"]
    assert [row.code for row in report.milestones] == ["H"]
    if kind == "weekly":
        assert [row.code for row in report.lookahead] == ["H"]


def test_demo_project_and_maintenance_with_public_stub(conn, monkeypatch):
    project = services.demo_project(conn)
    # la demo usa ejemplos/Ejemplo_Proyecto_SE_220kV.xlsx (red realista con WBS, recursos y avance)
    assert len(repo.list_entities(conn, "activities", project.id)) >= 100
    assert len(repo.list_entities(conn, "resources", project.id)) >= 10
    assert "FS" in {r.type for r in repo.list_entities(conn, "relations", project.id)}
    module = types.ModuleType("ganttier.maintenance")
    def generate(tasks, start, end, calendars, default):
        return [d.Occurrence(task.id, index, f"{task.code}-{index:04d}", task.name, start + timedelta(days=index), start, end, 1, 8, 1, task.required_skill, None, default) for task in tasks for index in range(1, 13)]
    module.generate_occurrences = generate
    module.level_periodic = lambda *args: d.OptimizeResult({}, {}, status="factible")
    monkeypatch.setitem(sys.modules, "ganttier.maintenance", module)
    plan = services.demo_maintenance(123, conn)
    assert len(repo.list_entities(conn, "activities", plan.id)) == 123
    assert 0 < len(repo.list_entities(conn, "periodic_tasks", plan.id)) <= 123
    assert len(repo.list_entities(conn, "resources", plan.id)) >= 5
