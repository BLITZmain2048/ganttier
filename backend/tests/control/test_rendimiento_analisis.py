"""Equivalencia exacta frente a las funciones anteriores a O1 y aceptación 100k.

Las referencias congeladas al final conservan la ruta anterior (incluidas sus
lecturas repetidas). No dependen de snapshots ni archivos temporales externos.
"""
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from math import fsum, isfinite
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from ganttier import db, repo, services
from ganttier.api.analysis import project_quality, project_evm_by
from ganttier.control import alerts
from ganttier.control.alerts import (DELAY_DAYS, STALE_DAYS, MIN_SPI, BACKLOG_DAYS,
                                     MAX_ITEM_ALERTS, _d, list_alerts)
from ganttier.control.evm import evm
from ganttier.domain import (Activity, ActivityView, AggregatedEVM, Assignment,
    BaselineActivity, EVMGroup, EVMReconciliation, ProgressEntry, QualityActivity,
    QualityFinding, Relation, ScheduleQualityResult, WBSNode)
from ganttier.main import create_app
from ganttier.maintenance.kpis import kpis
from ganttier.control._core import WorkCalendar

AGE_BANDS = ("0–7", "8–30", "31–90", ">90")


def _now():
    return "2026-05-01T12:00:00.000000+00:00"


@pytest.fixture
def sample():
    conn = db.connect(":memory:")
    db.initialize(conn)
    cal = repo.create(conn, "calendars", {"name": "7 días", "workweek": "1111111",
                                         "holidays_country": None})
    other_cal = repo.create(conn, "calendars", {"name": "L-V", "workweek": "1111100",
                                               "holidays_country": None})
    cut = date(2026, 5, 1)
    project = repo.create(conn, "projects", {"name": "Equivalencia O1", "start_date": date(2026, 1, 1),
        "end_date": date(2026, 12, 31), "data_date": cut, "calendar_id": cal.id})
    task = repo.create(conn, "periodic_tasks", {"project_id": project.id, "code": "P", "name": "P",
                                              "tol_after_days": 2})
    node = repo.create(conn, "wbs", {"project_id": project.id, "code": "1", "name": "WBS"})
    ages = [0, 7, 8, 30, 31, 90, 91, 130, -20]
    activities = repo.create_many(conn, "activities", [{
        "project_id": project.id, "code": f"A{i:02}", "name": f"Actividad {i}",
        "duration_days": 0 if i % 8 == 0 else 50 if i % 8 == 1 else 3,
        "work_hours": [0, .1, 13.7, 12.0][i % 4], "cost_budget": 30.7 + i,
        "calendar_id": cal.id if i % 2 else other_cal.id,
        "planned_start": cut - timedelta(days=60), "planned_finish": cut - timedelta(days=i % 10),
        "periodic_task_id": task.id, "due_date": cut - timedelta(days=ages[i % len(ages)]),
        "percent_complete": [0, 37.5, 100, 80][i % 4],
        "actual_finish": cut - timedelta(days=2) if i % 6 == 0 else None,
        "priority": [39, 40, 69, 70][i % 4], "location": [" Norte ", "Sur", "\u2003"][i % 3],
        "required_skill": ["", " Eléctrica ", "Mecánica"][i % 3],
        "wbs_id": node.id if i % 2 else None, "critical": i % 4 == 0,
        "constraint_type": "MFO" if i % 3 == 0 else "ASAP",
        "constraint_date": cut if i % 3 == 0 else None,
        "total_float": 45 if i % 5 == 0 else None,
        "status": "en_curso" if i % 4 == 0 else "no_iniciada",
    } for i in range(36)])
    for i, activity in enumerate(activities):
        if i % 4 == 0:
            repo.create_many(conn, "progress", [
                {"activity_id": activity.id, "date": cut - timedelta(days=20), "percent_complete": 25,
                 "actual_hours": .1, "actual_cost": 3.7, "actual_start": cut - timedelta(days=25)},
                {"activity_id": activity.id, "date": cut - timedelta(days=20), "percent_complete": 50,
                 "actual_hours": .2, "actual_cost": 4.9},
                {"activity_id": activity.id, "date": cut + timedelta(days=5), "percent_complete": 100,
                 "actual_finish": cut + timedelta(days=5)}])
        elif i % 4 == 1:
            repo.create(conn, "progress", {"activity_id": activity.id, "date": cut + timedelta(days=3),
                "percent_complete": 100, "actual_finish": cut + timedelta(days=3)})
        elif i % 4 == 2:
            repo.create(conn, "progress", {"activity_id": activity.id, "date": cut, "percent_complete": 100})
        repo.create(conn, "assignments", {"activity_id": activity.id, "skill": "\u2003" if i % 2 else "Rol"})
    # Datos heredados: futuras fechas reales, lógica circular y early_*.
    conn.execute("UPDATE activities SET actual_finish='2026-06-01' WHERE id=?", (activities[3].id,))
    conn.execute("UPDATE activities SET early_start=planned_start, early_finish=planned_finish, "
                 "planned_start=NULL, planned_finish=NULL WHERE id=?", (activities[5].id,))
    conn.executemany("INSERT INTO relations(project_id,pred_id,succ_id,type,lag_days) VALUES (?,?,?,'FS',?)", [
        (project.id, activities[0].id, activities[1].id, -1),
        (project.id, activities[1].id, activities[0].id, 11),
        (project.id, activities[1].id, activities[2].id, 0)])
    conn.commit()
    try:
        yield conn, project, activities
    finally:
        conn.close()


def _reference_alerts(conn, pid, **kwargs):
    # Misma BD y mismos identificadores, sin dejar efectos de la ruta de referencia.
    conn.execute("SAVEPOINT legacy_comparison")
    try:
        return _legacy_evaluate(conn, pid, **kwargs)
    finally:
        conn.execute("ROLLBACK TO legacy_comparison")
        conn.execute("RELEASE legacy_comparison")


@pytest.mark.parametrize("mode", ["proyecto", "mantenimiento"])
@pytest.mark.parametrize("baseline_kind", ["none", "empty", "partial", "full"])
def test_exact_equivalence_with_legacy_routes(sample, monkeypatch, mode, baseline_kind):
    conn, project, activities = sample
    pid, cut = project.id, project.data_date
    repo.update(conn, "projects", pid, {"mode": mode})
    if baseline_kind != "none":
        services.create_baseline(conn, pid)
        if baseline_kind == "empty":
            conn.execute("DELETE FROM baseline_activities")
        elif baseline_kind == "partial":
            conn.execute("DELETE FROM baseline_activities WHERE activity_id % 2 = 0")
        conn.execute("UPDATE activities SET planned_finish='2026-06-01' WHERE critical=1")
    data = services.control_inputs(conn, pid)
    original_activities = repo.list_entities(conn, "activities", pid)
    views = {v.id: v for v in data["acts"]}
    relations = repo.list_entities(conn, "relations", pid)
    assignments = repo.list_entities(conn, "assignments", pid)
    for options in ({}, {"exclude_terminals": False}, {"profile": "mantenimiento"},
                    {"start_ids": {activities[0].id}, "end_ids": set(), "high_float_days": 0}):
        expected = _legacy_schedule_quality(
            [replace(a, percent_complete=views[a.id].percent_complete) for a in original_activities],
            relations, assignments, cut, progress=data["progress"], **{"profile": mode, **options})
        actual = project_quality(pid, conn=conn, **{
            "start_ids": None, "end_ids": None, "long_lag_days": 10,
            "long_duration_days": 44, "high_float_days": 44, **options})
        assert actual == expected
    for start, end, cutoff in ((None, None, cut), (date.min, date(2026, 12, 31), cut),
                               (date(2026, 4, 1), cut, date(2026, 4, 15))):
        expected = _legacy_kpis(conn, pid, start, end, as_of=cutoff)
        assert kpis(conn, pid, start, end, as_of=cutoff) == expected
        shared = services.control_inputs(conn, pid, as_of=cutoff)
        assert kpis(conn, pid, start, end, as_of=cutoff, project=shared["project"],
                    progress=shared["progress"], views={a.id: a for a in shared["acts"]}) == expected
    for basis in ("hours", "cost", "count"):
        for group in ("wbs", "location", "skill"):
            expected = _legacy_aggregate_evm(data["acts"], data["baseline"], data["progress"],
                data["calendar"], cut, calendars=data["calendars"], activities=original_activities,
                wbs=repo.list_entities(conn, "wbs", pid), group=group, basis=basis)
            assert project_evm_by(pid, group=group, basis=basis, conn=conn) == expected
    monkeypatch.setattr(alerts, "_now", _now)
    for _ in range(2):
        expected = _reference_alerts(conn, pid, today=cut)
        assert alerts.evaluate(conn, pid, today=cut) == expected
        changes = conn.total_changes
        assert alerts.evaluate(conn, pid, today=cut) == expected
        assert conn.total_changes == changes
        if expected:
            alerts.set_state(conn, expected[0]["id"], "reconocida")
    # Resolución/reactivación y errores de EVM (presupuesto sin fechas).
    for percent, finish in ((100, "2026-04-01"), (0, None)):
        conn.execute("DELETE FROM progress")
        conn.execute("UPDATE activities SET percent_complete=?,actual_start=NULL,actual_finish=?", (percent, finish))
        conn.execute("UPDATE activities SET planned_start=NULL,early_start=NULL WHERE id=?", (activities[1].id,))
        expected = _reference_alerts(conn, pid, today=cut)
        assert alerts.evaluate(conn, pid, today=cut) == expected


def test_empty_project_and_preparation_error(sample, monkeypatch):
    conn, project, _ = sample
    monkeypatch.setattr(alerts, "_now", _now)
    def invalid_calendar(*args, **kwargs):
        raise ValueError("Calendario inválido de prueba")
    monkeypatch.setattr(services, "control_inputs", invalid_calendar)
    expected = _reference_alerts(conn, project.id, today=project.data_date)
    assert alerts.evaluate(conn, project.id, today=project.data_date) == expected
    empty = repo.create(conn, "projects", {"name": "Vacío", "start_date": date(2026, 1, 1)})
    assert alerts.evaluate(conn, empty.id, today=project.data_date) == _reference_alerts(
        conn, empty.id, today=project.data_date)
    assert kpis(conn, empty.id, as_of=project.data_date) == _legacy_kpis(conn, empty.id, as_of=project.data_date)


def test_alerts_write_lock_precedes_reads(sample):
    conn, project, _ = sample
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        alerts.evaluate(conn, project.id, today=project.data_date)
    finally:
        conn.set_trace_callback(None)
    sql = [statement.upper() for statement in statements]
    first_read = next(i for i, statement in enumerate(sql) if statement.startswith("SELECT"))
    assert sql.index("UPDATE ALERTS SET STATE=STATE WHERE 0") < first_read


@pytest.mark.slow
def test_100000_analysis_response_times(tmp_path, monkeypatch):
    monkeypatch.setenv("GANTTIER_DB", str(tmp_path / "performance.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(tmp_path / "datos"))
    with TestClient(create_app()) as client:
        demo = client.post("/api/demo/mantenimiento?n=100000")
        assert demo.status_code == 201, demo.text
        pid = demo.json()["id"]
        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM activities WHERE project_id=?", (pid,)).fetchone()[0] == 100000
        # Incluye lectura, cálculo, serialización y HTTP; sin calentamiento por ruta.
        for route, goal in (("alerts", 3), ("control/quality", 2),
                            ("maintenance/kpis", 1.5), ("control/evm-by", 2)):
            started = perf_counter()
            response = client.get(f"/api/projects/{pid}/{route}")
            elapsed = perf_counter() - started
            assert response.status_code == 200, response.text
            print(f"{route}: {elapsed:.3f} s (objetivo {goal} s; tolerancia 2x)")
            assert elapsed <= 2 * goal, (route, elapsed)


# Referencias anteriores a O1: mantener sin optimizaciones para detectar regresiones.

def _legacy_cycle_components(ids: set[int], relations: list[Relation]) -> list[set[int]]:
    """Encuentra componentes cíclicas sin recursión ni incluir sus descendientes."""
    edges, reverse = defaultdict(set), defaultdict(set)
    for relation in relations:
        if relation.pred_id in ids and relation.succ_id in ids:
            edges[relation.pred_id].add(relation.succ_id)
            reverse[relation.succ_id].add(relation.pred_id)
    visited, order = set(), []
    for root in sorted(ids):
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(sorted(edges[root])))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                order.append(node)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(sorted(edges[child]))))
    visited, cycles = set(), []
    for root in reversed(order):
        if root in visited:
            continue
        component, pending = set(), [root]
        visited.add(root)
        while pending:
            node = pending.pop()
            component.add(node)
            for parent in reverse[node]:
                if parent not in visited:
                    visited.add(parent)
                    pending.append(parent)
        if len(component) > 1 or root in edges[root]:
            cycles.append(component)
    return cycles


def _legacy_schedule_quality(activities: list[Activity], relations: list[Relation],
                     assignments: list[Assignment], data_date: date, *,
                     profile: str = "proyecto", exclude_terminals: bool = True,
                     start_ids: set[int] | None = None, end_ids: set[int] | None = None,
                     long_lag_days: int = 10, long_duration_days: int = 44,
                     high_float_days: int = 44,
                     progress: list[ProgressEntry] | None = None) -> ScheduleQualityResult:
    """Evalúa reglas con umbrales estrictos; duraciones y desfases son hábiles.

    Por defecto excluye de extremos abiertos el primer inicio y último término
    fechados (prefiere hitos en empates). Los conjuntos explícitos los reemplazan.
    La holgura se evalúa solo donde total_float contiene un resultado de CPM.
    """
    if profile not in {"proyecto", "mantenimiento"}:
        raise ValueError("El perfil debe ser 'proyecto' o 'mantenimiento'.")
    if min(long_lag_days, long_duration_days, high_float_days) < 0:
        raise ValueError("Los umbrales no pueden ser negativos.")
    index = {a.id: a for a in activities}
    ids = set(index)
    starts = [a for a in activities if a.planned_start or a.early_start]
    ends = [a for a in activities if a.planned_finish or a.early_finish]
    if start_ids is None:
        first = min(starts, key=lambda a: (a.planned_start or a.early_start,
                                           a.duration_days != 0, a.id), default=None)
        start_ids = {first.id} if first else set()
    if end_ids is None:
        last = max(ends, key=lambda a: (a.planned_finish or a.early_finish,
                                        a.duration_days == 0, a.id), default=None)
        end_ids = {last.id} if last else set()
    if not exclude_terminals:
        start_ids, end_ids = set(), set()
    has_pred = {r.succ_id for r in relations}
    has_succ = {r.pred_id for r in relations}
    rules: list[QualityFinding] = []
    no_logic = profile == "mantenimiento" and not relations

    def record(rule, affected, population, action, description, *, severity="advertencia",
               logic=False, count=None, unit="actividades", rels=(), applicable=True):
        affected = sorted(set(affected) & ids)
        count = len(affected) if count is None else count
        na = (logic and no_logic) or not applicable
        if na:
            affected, count = [], 0
            description = ("El perfil de mantenimiento no tiene relaciones." if logic and no_logic
                           else "No hay actividades con holgura de CPM calculada.")
        rules.append(QualityFinding(
            rule, "info" if na else severity, [QualityActivity(aid, index[aid].code) for aid in affected],
            population, count, action, description,
            "no aplica" if na else "hallazgo" if count else "sin hallazgos", unit,
            [r.id for r in rels]))

    record("sin_predecesora", ids - has_pred - start_ids, len(ids - start_ids),
           "Vincular la actividad a su predecesora o declararla inicio del programa.",
           "Actividad sin relación entrante, excluidos los inicios configurados.", logic=True)
    record("sin_sucesora", ids - has_succ - end_ids, len(ids - end_ids),
           "Vincular la actividad a su sucesora o declararla fin del programa.",
           "Actividad sin relación saliente, excluidos los finales configurados.", logic=True)
    record("restricciones_duras", [a.id for a in activities if a.constraint_type in {"MSO", "MFO", "SNLT", "FNLT"}],
           len(ids), "Revisar la justificación y reemplazar la restricción por relaciones cuando corresponda.",
           "MSO/MFO fijan fechas; SNLT/FNLT imponen límites que pueden reemplazar la lógica.")
    for rule, matches, description in (
        ("desfases_negativos", [r for r in relations if r.lag_days < 0], "Desfase menor que cero días hábiles."),
        ("desfases_largos", [r for r in relations if r.lag_days > long_lag_days],
         f"Desfase mayor que {long_lag_days} días hábiles."),
    ):
        record(rule, [aid for r in matches for aid in (r.pred_id, r.succ_id)], len(relations),
               "Revisar el desfase y representar el trabajo o la espera con una actividad explícita.",
               description, logic=True, count=len(matches), unit="relaciones", rels=matches)
    record("duraciones_largas", [a.id for a in activities if a.duration_days > long_duration_days], len(ids),
           "Descomponer la actividad en entregables de menor duración.",
           f"Duración mayor que {long_duration_days} días hábiles.")
    cpm = [a for a in activities if a.total_float is not None]
    record("holgura_alta", [a.id for a in cpm if a.total_float > high_float_days], len(cpm),
           "Revisar relaciones faltantes y coherencia de la ruta lógica.",
           f"Holgura total calculada mayor que {high_float_days} días.", applicable=bool(cpm))
    future = {a.id for a in activities if any(d and d > data_date for d in (a.actual_start, a.actual_finish))}
    future.update(entry.activity_id for entry in (progress or [])
                  if any(d and d > data_date for d in (entry.actual_start, entry.actual_finish)))
    record("fechas_reales_futuras", future, len(ids),
           "Corregir las fechas reales o revisar la fecha de corte.",
           "Inicio o término real posterior a la fecha de corte.", severity="crítica")
    dated = [a for a in activities if a.planned_finish or a.early_finish]
    record("actividades_vencidas", [a.id for a in dated if (a.planned_finish or a.early_finish) < data_date
                                     and a.percent_complete < 100], len(dated),
           "Revisar el avance y actualizar el trabajo remanente.",
           "Término planificado anterior al corte y avance menor que 100 %.")
    assigned = {a.activity_id for a in assignments if a.resource_id is not None or a.skill.strip()}
    labor = [a for a in activities if a.work_hours > 0]
    record("hh_sin_asignacion", [a.id for a in labor if not a.required_skill.strip() and a.id not in assigned],
           len(labor), "Asignar una habilidad requerida o un recurso a la actividad.",
           "Actividad con HH positivas, sin habilidad ni recurso asignado.")
    cycles = _legacy_cycle_components(ids, relations)
    cyclic_ids = set().union(*cycles) if cycles else set()
    membership = {aid: n for n, component in enumerate(cycles) for aid in component}
    cycle_rels = [r for r in relations if r.pred_id in cyclic_ids and
                  membership.get(r.pred_id) == membership.get(r.succ_id)]
    record("ciclos", cyclic_ids, len(ids), "Eliminar la dependencia circular y validar la secuencia.",
           "Actividades que pertenecen a componentes con dependencia circular.",
           severity="crítica", logic=True, rels=cycle_rels)
    duplicates = defaultdict(list)
    for relation in relations:
        duplicates[(relation.pred_id, relation.succ_id, relation.type)].append(relation)
    repeated = [r for group in duplicates.values() if len(group) > 1 for r in group]
    record("relaciones_duplicadas", [aid for r in repeated for aid in (r.pred_id, r.succ_id)], len(relations),
           "Conservar una relación por par y tipo; revisar los desfases antes de retirar duplicados.",
           "Relaciones con igual predecesora, sucesora y tipo, aunque difiera el desfase.",
           logic=True, count=sum(len(group) - 1 for group in duplicates.values()), unit="relaciones",
           rels=repeated)
    return ScheduleQualityResult(profile, data_date, [r for r in rules if r.status == "hallazgo"], rules)


def _legacy_compliance(rows):
    due = [r for r in rows if r["due_at_cutoff"]]
    total = len(due)
    completed = sum(r["completed"] for r in due)
    unknown = sum(r["completed"] and r["actual_finish"] is None for r in due)
    timely = sum(r["on_time"] for r in due)
    return {
        "status": "calculado" if total else "sin datos",
        "population": len(rows), "due": total, "completed": completed,
        "completed_on_time": timely, "completion_date_missing": unknown,
        "on_time_status": "sin datos" if not total or unknown else "calculado",
        "on_time_pct": 100 * timely / total if total and not unknown else None,
        "total_pct": 100 * completed / total if total else None,
    }


def _legacy_backlog_totals(rows):
    return {"count": len(rows), "remaining_hours": fsum(r["remaining_hours"] for r in rows)}


def _legacy_group(rows, key, summarize):
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return [{"key": key, **summarize(members)} for key, members in sorted(groups.items())]


def _legacy_kpis(conn, pid: int, start: date | None = None, end: date | None = None,
         *, as_of: date | None = None) -> dict:
    """El período filtra due_date; end nunca sustituye la fecha de corte.

    La tolerancia es tol_after_days (días corridos). Un 100 % sin fecha real
    permite contar la terminación, pero no demostrar su puntualidad.
    """
    project = repo.get(conn, "projects", pid)
    cutoff = as_of or project.data_date or date.today()
    start, end = start or project.start_date, end or cutoff
    if end < start:
        raise ValueError("El fin del período debe ser igual o posterior al inicio.")
    tasks = {t.id: t for t in repo.list_entities(conn, "periodic_tasks", pid)}
    progress = repo.list_entities(conn, "progress", pid)
    views = {a.id: a for a in services.build_activity_views(conn, pid, progress=progress, as_of=cutoff)}
    history = defaultdict(list)
    for entry in progress:
        history[entry.activity_id].append(entry)
    rows = []
    for activity in repo.list_entities(conn, "activities", pid):
        if activity.periodic_task_id is None or activity.due_date is None:
            continue
        if not start <= activity.due_date <= end:
            continue
        view = views[activity.id]
        entries = [e for e in history[activity.id] if e.date <= cutoff]
        latest = max(entries, key=lambda e: (e.date, e.id), default=None)
        # No usar la fecha de registro que build_activity_views infiere como fin.
        finish = latest.actual_finish if latest else activity.actual_finish
        if history[activity.id] and not entries:
            finish = None
        future_finish = finish is not None and finish > cutoff
        if future_finish:
            finish = None
        percent = max(0.0, min(100.0, view.percent_complete))
        completed = not future_finish and (finish is not None or percent >= 100)
        task = tasks.get(activity.periodic_task_id)
        tolerance = max(0, getattr(task, "tol_after_days", getattr(task, "tolerance_days", 0)))
        age = (cutoff - activity.due_date).days
        rows.append({
            "activity_id": activity.id, "code": activity.code, "name": activity.name,
            "due_date": activity.due_date, "month": activity.due_date.strftime("%Y-%m"),
            "location": activity.location.strip() or "Sin instalación",
            "skill": activity.required_skill.strip() or "Sin habilidad",
            "priority": "alta" if activity.priority >= 70 else "media" if activity.priority >= 40 else "baja",
            "percent_complete": percent, "actual_finish": finish,
            "due_at_cutoff": age >= 0, "completed": completed,
            "on_time": bool(completed and finish and (finish - activity.due_date).days <= tolerance),
            "tolerance_days": tolerance, "age_days": age,
            "age_band": AGE_BANDS[0 if age <= 7 else 1 if age <= 30 else 2 if age <= 90 else 3],
            "remaining_hours": max(0.0, activity.work_hours) * (1 - percent / 100),
        })
    pending = [r for r in rows if r["due_at_cutoff"] and not r["completed"]]
    compliance = _legacy_compliance(rows)
    return {
        "status": compliance["status"], "project_id": pid, "data_date": cutoff,
        "start": start, "end": end, "compliance": compliance,
        "by_month": _legacy_group(rows, "month", _legacy_compliance),
        "by_location": _legacy_group(rows, "location", _legacy_compliance),
        "by_skill": _legacy_group(rows, "skill", _legacy_compliance),
        "backlog": {
            "status": compliance["status"], **_legacy_backlog_totals(pending),
            "by_age": [{"key": band, **_legacy_backlog_totals([r for r in pending if r["age_band"] == band])}
                       for band in AGE_BANDS],
            "by_priority": [{"key": level, **_legacy_backlog_totals([r for r in pending if r["priority"] == level])}
                            for level in ("alta", "media", "baja")],
            "by_location": _legacy_group(pending, "location", _legacy_backlog_totals),
            "by_age_days": _legacy_group(pending, "age_days", _legacy_backlog_totals),
            "oldest": sorted(pending, key=lambda r: (r["due_date"], r["activity_id"]))[:20],
        },
    }


def _legacy_aggregate_evm(acts: list[ActivityView], baseline: dict[int, BaselineActivity] | None,
                  progress: list[ProgressEntry], calendar: WorkCalendar, data_date: date,
                  *, activities: list[Activity], wbs: list[WBSNode] | None = None,
                  group: str = "wbs", basis: str = "hours",
                  calendars: dict | None = None) -> AggregatedEVM:
    """Reutiliza evm para cada grupo y concilia contra una llamada independiente.

    WBS usa el nodo directo; skill usa required_skill, sin repartir asignaciones.
    Una línea base activa, incluso vacía, conserva exactamente su alcance.
    """
    if group not in {"wbs", "location", "skill"}:
        raise ValueError("La agrupación debe ser 'wbs', 'location' o 'skill'.")
    metadata = {a.id: a for a in activities}
    nodes = {node.id: node for node in (wbs or [])}
    partitions = defaultdict(list)
    names = {"unclassified": "Sin clasificar"}
    for activity in acts:
        if baseline is not None and activity.id not in baseline:
            continue
        source = metadata.get(activity.id)
        key = "unclassified"
        if group == "wbs":
            node = nodes.get(activity.wbs_id)
            if node is not None:
                key = f"wbs:{node.id}"
                names[key] = f"{node.code} — {node.name}"
        elif source is not None:
            label = (source.location if group == "location" else source.required_skill).strip()
            if label:
                key = group + ":" + label
                names[key] = label
        partitions[key].append(activity)
    partitions.setdefault("unclassified", [])
    history = defaultdict(list)
    for entry in progress:
        history[entry.activity_id].append(entry)
    groups = []
    for key, members in sorted(partitions.items()):
        ids = [a.id for a in members]
        plan = None if baseline is None else {aid: baseline[aid] for aid in ids}
        entries = [entry for aid in ids for entry in history[aid]]
        metrics = evm(members, plan, entries, calendar, data_date, basis=basis, calendars=calendars)
        groups.append(EVMGroup(key, names[key], ids, metrics))
    total = evm(acts, baseline, progress, calendar, data_date, basis=basis, calendars=calendars)
    sums = {field: fsum(getattr(row.evm, field) for row in groups)
            for field in ("BAC", "PV", "EV", "AC")}
    differences = {field: value - getattr(total, field) for field, value in sums.items()}
    reconciliation = EVMReconciliation(
        name="Conciliación", **sums,
        SPI=sums["EV"] / sums["PV"] if sums["PV"] else None,
        CPI=sums["EV"] / sums["AC"] if sums["AC"] else None,
        differences=differences, matches=all(abs(delta) <= 1e-6 for delta in differences.values()))
    return AggregatedEVM(group, basis, groups, total, reconciliation)


def _legacy_evaluate(conn, pid: int, *, today: date | None = None,
             delay_days: int = DELAY_DAYS, stale_days: int = STALE_DAYS,
             min_spi: float = MIN_SPI, backlog_days: int = BACKLOG_DAYS) -> list[dict]:
    """Evalúa al corte; conserva reconocimiento, resuelve y reactiva por clave.

    El término proyectado es el fin del programa vigente (planned/early_finish),
    o el fin real al corte. No se reparte el pronóstico global entre actividades.
    """
    if any(not isfinite(value) or value < 0 for value in (delay_days, stale_days, min_spi, backlog_days)):
        raise ValueError("Los umbrales deben ser finitos y no negativos.")
    today = today or date.today()
    with repo.transaction(conn):
        # Tomar el bloqueo de escritura ANTES de leer: en WAL, una transacción que primero lee y luego
        # intenta escribir falla de inmediato («database is locked») si otra conexión escribió entre
        # medio (p. ej., dos evaluaciones simultáneas). Una escritura vacía espera con busy_timeout.
        conn.execute("UPDATE alerts SET state=state WHERE 0")
        project = repo.get(conn, "projects", pid)
        cutoff = project.data_date or today
        activities = repo.list_entities(conn, "activities", pid)
        progress = repo.list_entities(conn, "progress", pid)
        assignments = repo.list_entities(conn, "assignments", pid)
        views = {v.id: v for v in services.build_activity_views(
            conn, pid, progress=progress, assignments=assignments, as_of=cutoff)}
        baseline = {b.activity_id: b for b in repo.list_entities(
            conn, "baseline_activities", filters={"baseline_id": project.active_baseline_id})
        } if project.active_baseline_id is not None else {}
        latest = {}
        for entry in progress:
            if entry.date <= cutoff:
                latest[entry.activity_id] = max(latest.get(entry.activity_id, entry.date), entry.date)
        active = {}

        def add(rule, key, severity, title, detail):
            active[(rule, key)] = (severity, title, detail, cutoff.isoformat())

        overdue, stale = [], []   # se emiten por actividad solo si son pocas (evita cientos de alertas)

        for activity in activities:
            view = views[activity.id]
            key = f"act:{activity.id}"
            plan = baseline.get(activity.id)
            projected = view.actual_finish or activity.planned_finish or activity.early_finish
            if (activity.critical or activity.duration_days == 0) and plan and plan.finish and projected:
                delay = (projected - plan.finish).days
                if delay > delay_days:
                    add("retraso_linea_base", key, "crítica", "Retraso respecto de línea base",
                        f"{activity.code}: término vigente {_d(projected)}; línea base {_d(plan.finish)}; "
                        f"retraso {delay} días (umbral > {delay_days}).")
            finish = activity.planned_finish or activity.early_finish
            if finish and finish < cutoff and view.percent_complete < 100:
                overdue.append((key, activity.code, (cutoff - finish).days,
                                f"{activity.code}: término planificado {_d(finish)}; corte {_d(cutoff)}; "
                                f"{(cutoff - finish).days} días vencida; avance {view.percent_complete:g} %."))
            in_progress = view.percent_complete < 100 and not view.actual_finish and (
                view.percent_complete > 0 or view.actual_start is not None or activity.status == "en_curso")
            last = latest.get(activity.id)
            age = (cutoff - last).days if last else None
            if in_progress and (last is None or age > stale_days):
                evidence = (f"último registro {_d(last)}, hace {age} días" if last else "0 registros al corte")
                stale.append((key, activity.code, age if age is not None else 10**6,
                              f"{activity.code}: avance {view.percent_complete:g} %; {evidence}; "
                              f"corte {_d(cutoff)}; ventana {stale_days} días."))
        # Mantenimiento: las ocurrencias vencidas se resumen siempre (el backlog ya las detalla por antigüedad).
        limit = 0 if project.mode == "mantenimiento" else MAX_ITEM_ALERTS
        for rule, items, title, plural in (
                ("actividad_vencida", overdue, "Actividad vencida", "actividades vencidas sin terminar"),
                ("avance_desactualizado", stale, "Avance sin actualizar", "actividades en curso sin avance reciente")):
            if not items:
                continue
            if len(items) <= limit:
                for key, _code, _days, detail in items:
                    add(rule, key, "advertencia", title, detail)
            else:
                worst = sorted(items, key=lambda item: -item[2])[:10]
                add(rule, "resumen", "advertencia", f"{len(items)} {plural}",
                    f"{len(items)} {plural} al corte {_d(cutoff)}. Las de mayor antigüedad: "
                    + ", ".join(f"{code} ({days} días)" if days < 10**6 else f"{code} (sin registros)"
                                for _key, code, days, _detail in worst) + ".")
        if project.data_date is None:
            add("corte_desactualizado", "proyecto", "advertencia", "Fecha de corte sin definir",
                f"0 fechas de corte informadas; evaluación provisional al {_d(cutoff)}.")
        elif (today - cutoff).days > stale_days:
            add("corte_desactualizado", "proyecto", "advertencia", "Fecha de corte desactualizada",
                f"Corte {_d(cutoff)}; hoy {_d(today)}; antigüedad {(today - cutoff).days} días (umbral > {stale_days}).")
        cause = None
        try:
            metrics = services.control_result(conn, pid, "evm", as_of=cutoff)
        except ValueError as exc:
            # EVM valida, entre otros datos, las fechas del presupuesto positivo.
            metrics, cause = None, str(exc)
        indicators = [] if metrics is None else [(name, value) for name, value in (("SPI(t)", metrics.SPI_t), ("SPI", metrics.SPI))
                      if value is not None and isfinite(value)]
        if not indicators:
            evidence = cause or f"BAC={metrics.BAC:g}, PV={metrics.PV:g}, EV={metrics.EV:g}."
            add("spi_datos_insuficientes", "spi", "advertencia", "Datos insuficientes para SPI",
                f"SPI(t) y SPI no calculables al {_d(cutoff)}; {evidence} Revisar plan y avance.")
        elif any(value < min_spi for _, value in indicators):
            evidence = "; ".join(f"{name}={value:.4f}" for name, value in indicators)
            add("spi_bajo", "spi", "advertencia", "Desempeño de plazo bajo",
                f"{evidence}; umbral < {min_spi:g}; corte {_d(cutoff)}.")
        if project.mode == "mantenimiento":
            # Toda la cartera vencida, incluso compromisos anteriores al inicio.
            backlog = _legacy_kpis(conn, pid, start=date.min, end=cutoff, as_of=cutoff)["backlog"]
            aged = [r for r in backlog["by_age_days"] if r["key"] > backlog_days]
            if aged:
                add("backlog_antiguo", "proyecto", "advertencia", "Backlog de mantenimiento antiguo",
                    f"{sum(r['count'] for r in aged)} ocurrencias con antigüedad > {backlog_days} días; "
                    f"máxima {max(r['key'] for r in aged)} días; "
                    f"HH remanentes {sum(r['remaining_hours'] for r in aged):g}.")
        quality = _legacy_schedule_quality(
            [replace(a, percent_complete=views[a.id].percent_complete) for a in activities],
            repo.list_entities(conn, "relations", pid), assignments, cutoff,
            profile=project.mode, progress=progress)
        for finding in quality.findings:
            if finding.severity == "crítica":
                add("calidad_critica", finding.rule, "crítica", "Hallazgo crítico de calidad",
                    f"{finding.rule}: {finding.count} de {finding.population} {finding.population_unit}. "
                    f"{finding.description} {finding.action}")
        now = _now()
        for (rule, key), values in active.items():
            conn.execute("""INSERT INTO alerts
                (project_id,rule,object_key,severity,title,detail,data_date,state,first_seen,updated_at)
                VALUES (?,?,?,?,?,?,?,'nueva',?,?)
                ON CONFLICT(project_id,rule,object_key) DO UPDATE SET
                severity=excluded.severity,title=excluded.title,detail=excluded.detail,
                data_date=excluded.data_date,
                state=CASE WHEN alerts.state='resuelta' THEN 'nueva' ELSE alerts.state END,
                updated_at=excluded.updated_at
                WHERE alerts.state='resuelta' OR alerts.severity<>excluded.severity
                OR alerts.title<>excluded.title OR alerts.detail<>excluded.detail
                OR alerts.data_date IS NOT excluded.data_date""", (pid, rule, key, *values, now, now))
        for row in conn.execute("SELECT id,rule,object_key FROM alerts WHERE project_id=? AND state<>'resuelta'", (pid,)).fetchall():
            if (row["rule"], row["object_key"]) not in active:
                conn.execute("UPDATE alerts SET state='resuelta',data_date=?,updated_at=? WHERE id=?",
                             (cutoff.isoformat(), now, row["id"]))
        return list_alerts(conn, pid)


