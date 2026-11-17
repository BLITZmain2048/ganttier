"""EVM por particiones disjuntas; WBS corresponde al nodo directo asignado."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import fsum

from ganttier.domain import (Activity, ActivityView, AggregatedEVM, BaselineActivity,
                            EVMGroup, EVMReconciliation, ProgressEntry, WBSNode)
from ._core import WorkCalendar
from .evm import evm


def aggregate_evm(acts: list[ActivityView], baseline: dict[int, BaselineActivity] | None,
                  progress: list[ProgressEntry], calendar: WorkCalendar, data_date: date,
                  *, activities: list[Activity] | None = None, wbs: list[WBSNode] | None = None,
                  group: str = "wbs", basis: str = "hours",
                  calendars: dict | None = None,
                  labels: dict[int, str] | None = None) -> AggregatedEVM:
    """Reutiliza evm para cada grupo y concilia contra una llamada independiente.

    WBS usa el nodo directo; skill usa required_skill, sin repartir asignaciones.
    Una línea base activa, incluso vacía, conserva exactamente su alcance.
    labels permite entregar solo location/required_skill, sin cargar fichas completas.
    """
    if group not in {"wbs", "location", "skill"}:
        raise ValueError("La agrupación debe ser 'wbs', 'location' o 'skill'.")
    metadata = {a.id: a for a in (activities or [])} if labels is None and group != "wbs" else {}
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
        elif labels is not None or source is not None:
            label = (labels.get(activity.id, "") if labels is not None else
                     source.location if group == "location" else source.required_skill).strip()
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
