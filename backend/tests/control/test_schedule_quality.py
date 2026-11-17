"""Diagnóstico sintético trazable, perfiles, umbrales y ciclos."""
from copy import deepcopy
from datetime import date

from ganttier.control.schedule_quality import schedule_quality
from ganttier.domain import Activity, Assignment, Relation


def activity(aid, **kwargs):
    values = dict(id=aid, project_id=1, code=f"A{aid}", name=f"Actividad {aid}",
                  planned_start=date(2026, 1, 1), planned_finish=date(2026, 2, 1))
    values.update(kwargs)
    return Activity(**values)


def test_exactly_eight_distinct_traceable_findings():
    acts = [activity(i) for i in range(1, 10)]
    acts[1].constraint_type = "MSO"
    acts[3].duration_days = 45
    acts[4].total_float = 45
    acts[5].actual_start = date(2026, 1, 11)
    relations = [Relation(i, 1, i, i + 1) for i in range(1, 8)]
    relations[1].lag_days = -1
    relations[2].lag_days = 11
    original = deepcopy((acts, relations))
    result = schedule_quality(acts, relations, [], date(2026, 1, 10), start_ids={1}, end_ids={8})
    expected = {"sin_predecesora", "sin_sucesora", "restricciones_duras", "desfases_negativos",
                "desfases_largos", "duraciones_largas", "holgura_alta", "fechas_reales_futuras"}
    assert {finding.rule for finding in result.findings} == expected
    assert len(result.findings) == 8
    for finding in result.findings:
        assert finding.population >= finding.count > 0
        assert finding.action and finding.description
        assert finding.severity in {"info", "advertencia", "crítica"}
        assert all(a.code == f"A{a.id}" for a in finding.activities)
    assert (acts, relations) == original


def test_maintenance_without_relations_marks_logic_not_applicable():
    result = schedule_quality([activity(1), activity(2)], [], [], date(2026, 1, 10), profile="mantenimiento")
    logic = {"sin_predecesora", "sin_sucesora", "desfases_negativos", "desfases_largos", "ciclos", "relaciones_duplicadas"}
    rows = {r.rule: r for r in result.rules}
    assert all(rows[name].status == "no aplica" for name in logic)
    assert rows["holgura_alta"].status == "no aplica"
    assert not result.findings
    related = schedule_quality([activity(1), activity(2)], [Relation(1, 1, 1, 2)], [],
                               date(2026, 1, 10), profile="mantenimiento")
    assert all(r.status != "no aplica" for r in related.rules if r.rule in logic)


def test_overdue_labor_cycles_and_duplicate_relationships():
    acts = [activity(i) for i in range(1, 5)]
    acts[0].planned_finish = date(2026, 1, 9)
    acts[0].work_hours = 8
    relations = [Relation(1, 1, 1, 2), Relation(2, 1, 2, 1), Relation(3, 1, 2, 3),
                 Relation(4, 1, 2, 3, lag_days=1), Relation(5, 1, 3, 4)]
    result = schedule_quality(acts, relations, [], date(2026, 1, 10))
    rows = {r.rule: r for r in result.findings}
    assert {a.id for a in rows["ciclos"].activities} == {1, 2}
    assert rows["ciclos"].relation_ids == [1, 2]
    assert rows["relaciones_duplicadas"].count == 1
    assert rows["relaciones_duplicadas"].relation_ids == [3, 4]
    assert [a.id for a in rows["actividades_vencidas"].activities] == [1]
    assert [a.id for a in rows["hh_sin_asignacion"].activities] == [1]


def test_configurable_thresholds_terminal_exclusions_and_assignments():
    acts = [activity(1, duration_days=44, total_float=44, work_hours=8),
            activity(2, work_hours=8, required_skill="electricidad"), activity(3, work_hours=8)]
    relations = [Relation(1, 1, 1, 2, lag_days=10), Relation(2, 1, 2, 3)]
    assignments = [Assignment(1, 1, resource_id=5), Assignment(2, 3, skill="montaje")]
    result = schedule_quality(acts, relations, assignments, date(2026, 1, 10))
    assert not result.findings
    result = schedule_quality(acts, relations, assignments, date(2026, 1, 10),
                              long_lag_days=9, long_duration_days=43, high_float_days=43,
                              exclude_terminals=False)
    assert {r.rule for r in result.findings} == {"sin_predecesora", "sin_sucesora", "desfases_largos",
                                              "duraciones_largas", "holgura_alta"}


def test_cycle_detection_handles_long_network_without_recursion():
    acts = [activity(i) for i in range(1500)]
    relations = [Relation(i, 1, i, i + 1) for i in range(1499)]
    relations.append(Relation(1500, 1, 1499, 1498))
    result = schedule_quality(acts, relations, [], date(2026, 1, 10))
    cycles = next(r for r in result.findings if r.rule == "ciclos")
    assert {a.id for a in cycles.activities} == {1498, 1499}
