"""Datos de presentación comunes, sin recalcular los indicadores del motor."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any
from collections.abc import Sequence
from heapq import nlargest
import json
import re

from ganttier.domain import ReportData

LIMIT = 500
PERIODS = {"weekly": "semanal", "monthly": "mensual", "annual": "anual"}
STATUS = {"no_iniciada": "No iniciada", "en_curso": "En curso", "terminada": "Terminada"}


@dataclass
class Section:
    title: str
    headers: list[str]
    rows: Sequence
    percent_columns: tuple[int, ...] = ()
    chart: str | None = None
    total_rows: int | None = None


class MappedRows(Sequence):
    """Vista repetible: no duplica 100.000 actividades antes de escribirlas."""
    def __init__(self, records, convert):
        self.records, self.convert = records, convert

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self.convert(r) for r in self.records[index]]
        return self.convert(self.records[index])


def clean_text(value):
    # XML 1.0 no permite estos controles en XLSX/DOCX/PDF.
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '�', str(value))


def finite(value):
    return isinstance(value, (int, float)) and isfinite(value)


def cutoff(report):
    return report.project.data_date or report.evm.data_date or report.scurve.data_date


def conclusions(report):
    e = report.evm
    if e.planned_finish and e.forecast_finish:
        delay = (e.forecast_finish - e.planned_finish).days
        situation = (f'presenta un atraso pronosticado de {display(delay)} días corridos respecto de la línea base' if delay > 0
                     else f'presenta un adelanto pronosticado de {display(-delay)} días corridos respecto de la línea base' if delay < 0
                     else 'mantiene el término pronosticado en la fecha de línea base')
        text = f'El proyecto {situation}; SPI(t)={display(e.SPI_t)}.'
    else:
        text = f'No se puede determinar el atraso respecto de la línea base: falta el término base o el pronóstico; SPI(t)={display(e.SPI_t)}.'
    top = nlargest(5, report.delayed, key=lambda r: r.delay_days if finite(r.delay_days) else float('-inf'))
    if top:
        text += f' Las {len(top)} actividades más atrasadas son: ' + '; '.join(f'{display(r.code)} — {display(r.name)} ({display(r.delay_days)} días)' for r in top) + '.'
        text += ' Priorizar estas actividades y confirmar responsables y ventanas de desconexión.'
    else:
        text += ' No se informan actividades atrasadas en este corte.'
    overload = sum(max(0, load - capacity) for s in report.load.series for load, capacity in zip(s.load, s.capacity) if finite(load) and finite(capacity))
    if overload:
        text += f' Se detectan {display(float(overload))} HH de sobrecarga por recurso; redistribuir trabajo o acordar capacidad adicional.'
    if any(not finite(v) for s in report.load.series for v in (*s.load, *s.capacity)):
        text += ' Hay carga o capacidad sin datos; los totales y la sobrecarga son incompletos.'
    return text


def title(report: ReportData) -> str:
    return f"Informe {PERIODS[report.period_type]} de avance"


def traffic_light(spi: float | None) -> tuple[str, str]:
    if spi is None or not isfinite(spi):
        return "Sin datos", "687787"
    if spi >= .95:
        return "Verde", "17734B"
    if spi >= .85:
        return "Amarillo", "956400"
    return "Rojo", "B33135"


def report_traffic(report):
    """SPI vuelve a 1 al terminar, aunque el proyecto haya terminado tarde."""
    value = report.evm.SPI_t if finite(report.evm.SPI_t) else report.evm.SPI
    return traffic_light(value)


def display(value: Any, percent: bool = False) -> str:
    if value is None or (isinstance(value, float) and not isfinite(value)):
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + (" %" if percent else "")
    if isinstance(value, int) and not isinstance(value, bool):
        return f'{value:,}'.replace(',', '.') + (' %' if percent else '')
    if isinstance(value, (dict, list, tuple)):
        return clean_text(json.dumps(value, ensure_ascii=False, default=str))
    return clean_text(value) + (" %" if percent else "")


def notice(count: int) -> str:
    return f"Se muestran las primeras {LIMIT} de {count} filas. El archivo Excel contiene el detalle completo."


def sections(report: ReportData, *, full_detail=False) -> list[Section]:
    """Orden editorial, unidades y tablas comunes a los cuatro formatos."""
    e = report.evm
    unit = {"hours": "HH", "cost": report.project.currency, "count": "actividades"}.get(e.basis, e.basis)
    light = report_traffic(report)[0]
    summary = [
        ['Conclusiones y acciones', conclusions(report)],
        ["Avance planificado", display(e.percent_planned, True)], ["Avance real (ganado)", display(e.percent_complete, True)],
        ["SPI", e.SPI], ["CPI", e.CPI], ["SPI(t)", e.SPI_t], [f"EAC ({unit})", e.EAC],
        ["Término pronosticado", e.forecast_finish], ["Término de línea base", e.planned_finish],
        ["Actividades terminadas en el período", len(report.completed_in_period)],
        ["Actividades atrasadas", len(report.delayed)], ["Semáforo de plazo", light],
        ['Semáforo de costo (CPI)', traffic_light(e.CPI)[0]],
        ['Base del semáforo', 'SPI(t)' if finite(e.SPI_t) else 'SPI (SPI(t) no disponible)'],
    ]
    summary.extend([str(key), value] for key, value in (report.kpis or {}).items())
    result = [Section("Resumen ejecutivo", ["Indicador", "Valor"], summary)]
    if report.route_checklist:
        result.append(Section("Checklist de rutas", ["Ruta", "Ítem OM-WBS", "Ocurrencia", "Programado", "Cumplido real", "Fin programado"],
            [[r['route'],r['item'],r['occurrence'],r['scheduled'],r['completed'],r['finish']] for r in report.route_checklist]))
    if report.team_distribution:
        result.append(Section("Reparto por equipo", ["Mes / ocurrencia", "Equipo", "Cantidad / unidad", "Lotes demanda / programados", "Días requeridos / disponibles", "HH demanda / programadas"],
            [[f"{r['month']} / {r['occurrence']}", r['team'], f"{r['quantity']:g} {r['unit']}",
              f"{r['batches']} / {r.get('scheduled_batches', 0)}", f"{r['required_days']} / {r['available_days']}",
              f"{r['work_hours']:g} / {r.get('scheduled_hours', 0):g}"] for r in report.team_distribution]))
    if report.team_alerts:
        result.append(Section("Alertas de equipos", ["Detalle y acción requerida"], [[w] for w in report.team_alerts]))
    curve = report.scurve
    result.append(Section("Curva S", ["Fecha", "Planificado %", "Ganado %", "Real %", "Proyección %"], [[date.fromisoformat(label), *values] for label, *values in zip(curve.labels, curve.planned_cum, curve.earned_cum, curve.actual_cum, curve.forecast_cum)], (1, 2, 3, 4), "scurve"))
    evm_rows = [[key, getattr(e, key), unit if key in {"BAC", "PV", "EV", "AC", "SV", "CV", "EAC", "ETC", "VAC"} else ("días corridos" if key == "ES_days" else "índice")] for key in ("BAC", "PV", "EV", "AC", "SV", "CV", "SPI", "CPI", "ES_days", "SPI_t", "EAC", "ETC", "VAC", "TCPI")]
    result.append(Section("EVM", ["Indicador", "Valor", "Unidad"], evm_rows))
    headers = ["Código / actividad", "Inicio / fin", "Avance %", "Estado", "Responsable / instalación", "Fin línea base", "Atraso (días)"]
    for heading, records, chart in (
        ("Terminadas en el período", report.completed_in_period, None),
        ("Programadas en el período", report.scheduled_in_period, None),
        ("Atrasadas", sorted(report.delayed, key=lambda x: (finite(x.delay_days), x.delay_days or 0), reverse=True) if full_detail else nlargest(LIMIT, report.delayed, key=lambda x: x.delay_days if finite(x.delay_days) else float('-inf')), None),
        ("Lookahead del período siguiente", report.lookahead, "gantt"),
        ("Hitos", report.milestones, None),
        ("Actividades críticas", getattr(report, "critical", []), None),
    ):
        result.append(Section(heading, headers, MappedRows(records, lambda r: [f"{display(r.code)}\n{display(r.name)}", f"{display(r.start)}\n{display(r.finish)}", r.percent_complete, STATUS.get(r.status, r.status), f"{display(r.responsible)}\n{display(r.location)}", r.baseline_finish, r.delay_days]), (2,), chart, len(report.delayed) if heading == "Atrasadas" else len(records)))
    if not hasattr(report, 'critical'):
        result.append(Section('Disponibilidad de ruta crítica', ['Estado'], [['Datos de ruta crítica no disponibles en este informe; no se infiere criticidad a partir del atraso.']]))
    skill = getattr(report, 'skill_load', None)
    if skill is not None:
        result.append(Section('Carga semanal por habilidad', ['Semana', 'Habilidad', 'Carga (HH)', 'Capacidad (HH)'], MappedRows(range(len(skill.labels) * len(skill.series)), lambda j: [date.fromisoformat(skill.labels[j // len(skill.series)]), skill.series[j % len(skill.series)].key, skill.series[j % len(skill.series)].load[j // len(skill.series)], skill.series[j % len(skill.series)].capacity[j // len(skill.series)]]), chart='skill_load'))
    else:
        result.append(Section('Disponibilidad de carga por habilidad', ['Estado'], [['Perfil semanal por habilidad no disponible; se presenta la carga por recurso.']]))
    load = report.load
    result.append(Section("Carga por recurso o rol", ["Período", "Recurso / rol", "Carga (HH)", "Capacidad (HH)"], MappedRows(range(len(load.labels) * len(load.series)), lambda j: [date.fromisoformat(load.labels[j // len(load.series)]), load.series[j % len(load.series)].key, load.series[j % len(load.series)].load[j // len(load.series)], load.series[j % len(load.series)].capacity[j // len(load.series)]]), chart="load"))
    # Dos tablas legibles en A4, ambas conservan el detalle por trabajador.
    result.append(Section("Desempeño por trabajador", ["Trabajador", "Cumplimiento %", "HH plan", "HH ganadas", "HH reales", "Eficiencia HH", "SPI", "CPI"], [[p.name, p.compliance_pct, p.planned_hours, p.earned_hours, p.actual_hours, p.hh_efficiency, p.SPI, p.CPI] for p in report.performance], (1,), "performance"))
    result.append(Section("Actividades por trabajador", ["Trabajador", "Asignadas", "Terminadas", "A tiempo", "Vencen período", "Pendientes", "Atraso medio (días)"], [[p.name, p.assigned_activities, p.completed_activities, p.completed_on_time, p.due_in_period, p.pending_activities, p.avg_delay_days] for p in report.performance]))
    return result


def cover(report: ReportData) -> list[tuple[str, Any]]:
    return [("Proyecto", report.project.name), ("Período", f"{display(report.period_start)} al {display(report.period_end)}"), ("Fecha de corte", cutoff(report)), ("Fecha de generación", report.generated_at), ("Versión del informe", "R3 / 1"), ("Semáforo de costo (CPI)", traffic_light(report.evm.CPI)[0] + " · " + display(report.evm.CPI))]


SEMÁFORO = "Semáforo de plazo: SPI(t), o SPI si falta SPI(t). Verde ≥ 0,95; amarillo ≥ 0,85 y < 0,95; rojo < 0,85. Sin valor: sin datos."


def chart_description(kind):
    return {
        'scurve': 'Curva S acumulada de plan, avance ganado, consumo real y proyección, en porcentaje del presupuesto; huecos = sin datos.',
        'load': 'Carga apilada por recurso o rol y capacidad del período, en HH. El total no demuestra factibilidad individual.',
        'skill_load': 'Carga semanal y capacidad por habilidad en HH; primeras seis habilidades. Las capacidades pueden compartir personas: no sumarlas como dotación.',
        'performance': 'Cumplimiento por trabajador en porcentaje; primeros 20 de la tabla. Ausente = sin datos.',
        'gantt': 'Fechas programadas de las primeras 20 actividades del próximo período.',
    }[kind] + ' Valores disponibles en la tabla siguiente.'
