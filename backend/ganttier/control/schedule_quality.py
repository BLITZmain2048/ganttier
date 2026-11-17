"""Diagnóstico explicable del programa, sin programar ni modificar actividades."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ganttier.domain import (Activity, Assignment, ProgressEntry, QualityActivity,
                            QualityFinding, Relation, ScheduleQualityResult)


def _load_activities(conn, pid: int) -> list[Activity]:
    """Proyección de solo los campos usados por calidad y reglas de alertas.

    No es una ficha completa para persistir: evita decodificar los campos de
    programación, ventanas y mantenimiento que estos diagnósticos no consumen.
    """
    rows = conn.execute("""SELECT id,code,duration_days,work_hours,planned_start,
        planned_finish,early_start,early_finish,actual_start,actual_finish,
        percent_complete,constraint_type,total_float,required_skill,critical,status
        FROM activities WHERE project_id=? ORDER BY id""", (pid,))
    result = []
    for (aid, code, duration, work, start, finish, early_start, early_finish,
         actual_start, actual_finish, percent, constraint, total_float, skill, critical, status) in rows:
        result.append(Activity(
            aid, pid, code, "", duration_days=duration, work_hours=work,
            planned_start=date.fromisoformat(start) if start else None,
            planned_finish=date.fromisoformat(finish) if finish else None,
            early_start=date.fromisoformat(early_start) if early_start else None,
            early_finish=date.fromisoformat(early_finish) if early_finish else None,
            actual_start=date.fromisoformat(actual_start) if actual_start else None,
            actual_finish=date.fromisoformat(actual_finish) if actual_finish else None,
            percent_complete=percent, constraint_type=constraint, total_float=total_float,
            required_skill=skill, critical=bool(critical), status=status))
    return result


def _assigned_activity_ids(conn, pid: int) -> set[int]:
    # strip de Python conserva también el tratamiento de espacios Unicode.
    return {aid for aid, rid, skill in conn.execute("""
        SELECT activity_id,resource_id,skill FROM assignments
        WHERE activity_id IN (SELECT id FROM activities WHERE project_id=?)
        """, (pid,)) if rid is not None or skill.strip()}


def _cycle_components(ids: set[int], relations: list[Relation]) -> list[set[int]]:
    """Encuentra componentes cíclicas sin recursión ni incluir sus descendientes."""
    edges, reverse = defaultdict(set), defaultdict(set)
    for relation in relations:
        if relation.pred_id in ids and relation.succ_id in ids:
            edges[relation.pred_id].add(relation.succ_id)
            reverse[relation.succ_id].add(relation.pred_id)
    visited, order = set(), []
    # Los nodos aislados nunca pertenecen a una componente cíclica.
    for root in sorted(edges.keys() | reverse.keys()):
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


def schedule_quality(activities: list[Activity], relations: list[Relation],
                     assignments: list[Assignment], data_date: date, *,
                     profile: str = "proyecto", exclude_terminals: bool = True,
                     start_ids: set[int] | None = None, end_ids: set[int] | None = None,
                     long_lag_days: int = 10, long_duration_days: int = 44,
                     high_float_days: int = 44,
                     progress: list[ProgressEntry] | None = None,
                     views: dict | None = None,
                     assigned_ids: set[int] | None = None) -> ScheduleQualityResult:
    """Evalúa reglas con umbrales estrictos; duraciones y desfases son hábiles.

    Por defecto excluye de extremos abiertos el primer inicio y último término
    fechados (prefiere hitos en empates). Los conjuntos explícitos los reemplazan.
    La holgura se evalúa solo donde total_float contiene un resultado de CPM.
    views sustituye solo el porcentaje al corte, sin ocultar fechas reales futuras.
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
        na = (logic and no_logic) or not applicable
        if na:
            affected, count = [], 0
            description = ("El perfil de mantenimiento no tiene relaciones." if logic and no_logic
                           else "No hay actividades con holgura de CPM calculada.")
        else:
            affected = sorted(set(affected) & ids)
            count = len(affected) if count is None else count
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

    def unfinished(a) -> bool:
        """A4-09: mismo criterio de terminación al corte que Control (100 % o término real informado)."""
        if views is not None:
            view = views[a.id]
            return view.percent_complete < 100 and not view.actual_finish
        return a.percent_complete < 100 and not (a.actual_finish and a.actual_finish <= data_date)
    record("actividades_vencidas", [a.id for a in dated if (a.planned_finish or a.early_finish) < data_date
                                     and unfinished(a)], len(dated),
           "Revisar el avance y actualizar el trabajo remanente.",
           "Término planificado anterior al corte y avance menor que 100 %.")
    assigned = (assigned_ids if assigned_ids is not None else
                {a.activity_id for a in assignments if a.resource_id is not None or a.skill.strip()})
    labor = [a for a in activities if a.work_hours > 0]
    record("hh_sin_asignacion", [a.id for a in labor if not a.required_skill.strip() and a.id not in assigned],
           len(labor), "Asignar una habilidad requerida o un recurso a la actividad.",
           "Actividad con HH positivas, sin habilidad ni recurso asignado.")
    cycles = _cycle_components(ids, relations)
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
