"""Contrato de columnas y conversiones compartido por plantillas e importación."""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any
import re
import unicodedata

from ganttier import domain


@dataclass(frozen=True)
class Column:
    field: str
    title: str
    kind: str = "text"
    required: bool = False


def normalize(value: Any) -> str:
    """Normaliza encabezados sin alterar los identificadores del usuario."""
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)))


def columns(spec: str) -> list[Column]:
    result = []
    for item in spec.split("|"):
        parts = item.strip().split(":")
        field, title = parts[:2]
        kind = parts[2] if len(parts) > 2 else "text"
        result.append(Column(field, title, kind.rstrip("!"), kind.endswith("!")))
    return result


SCHEMAS = {
    "Equipos": columns("name:Equipo:text!|regime:Regimen:shift!|cycle_start:Inicio_Ciclo:date!|members:Integrantes:positive_int!|hours_day:Horas_Dia:number!|holidays_work:Feriados_Trabajan:bool|calendar_name:Calendario"),
    "Calendarios": columns("name:Nombre:text!|workweek:Semana_Laboral:week|hours_per_day:Horas_Dia:number|holidays_country:Pais_Feriados|shift_pattern:Turno:shift|shift_anchor:Ancla_Turno:date|holidays_apply_to_shift:Feriados_En_Turno:bool"),
    "Excepciones_Calendario": columns("calendar_name:Calendario:text!|date:Fecha:date!|working:Laborable:bool!|hours:HH:number|reason:Motivo"),
    "Recursos": columns("name:Nombre:text!|kind:Tipo:resource|skills:Habilidades:skills|capacity_hours_day:Capacidad_HH_Dia:number|max_units:Unidades:number|calendar_name:Calendario|cost_hour:Costo_HH:number|overtime_cost_hour:Costo_HH_Extra:number|max_overtime_hours_day:Max_HH_Extra_Dia:number|email:Email|active:Activo:bool"),
    "WBS": columns("code:Codigo:text!|name:Nombre:text!|parent_code:Codigo_Padre|sort_order:Orden:int"),
    "Actividades": columns("code:Codigo:text!|name:Nombre:text!|wbs_code:Codigo_WBS|duration_days:Duracion_Dias:int|work_hours:HH:number|cost_budget:Costo:number|macro_month:Mes_Macro:month|window_start:Ventana_Inicio:date|window_end:Ventana_Fin:date|window_hard:Ventana_Dura:bool|constraint_type:Restriccion:constraint|constraint_date:Fecha_Restriccion:date|priority:Prioridad:priority|calendar_name:Calendario|required_skill:Habilidad_Requerida|crew_size:Dotacion:number|location:Ubicacion|equipment:Equipo|voltage_kv:Tension_kV:number|requires_outage:Requiere_Desconexion:bool|discipline:Disciplina|notes:Notas|team_name:Equipo_Trabajo|quantity:Cantidad:number|unit:Unidad|batch_index:Lote:int|equity_mode:Equidad|duration_hours:Duracion_Horas:number|checklist_codes:Checklist:skills"),
    "Relaciones": columns("pred_code:Predecesora:text!|succ_code:Sucesora:text!|type:Tipo:relation|lag_days:Desfase_Dias:signed_int"),
    "Asignaciones": columns("activity_code:Actividad:text!|resource_name:Recurso|skill:Habilidad|units:Unidades:number|work_hours:HH:number|actual_hours:HH_Reales:number"),
    "Avance": columns("activity_code:Actividad:text!|date:Fecha:date!|percent_complete:Porcentaje:percent!|actual_hours:HH_Reales:number|actual_cost:Costo_Real:number|resource_name:Trabajador|actual_start:Inicio_Real:date|actual_finish:Fin_Real:date|note:Nota"),
    "Plan_Maestro": columns("code:Codigo:text!|name:Nombre:text!|frequency:Frecuencia:frequency|interval:Intervalo:positive_int|duration_days:Duracion_Dias:int|work_hours:HH_por_Ejecucion:number|crew_size:Dotacion:number|required_skill:Habilidad|resource_name:Trabajador_Fijo|first_due:Primera_Fecha:date|preferred_months:Meses_Preferidos:months|preferred_weekday:Dia_Semana_Preferido:weekday|day_of_month:Dia_del_Mes:monthday|tol_before_days:Tolerancia_Antes:int|tol_after_days:Tolerancia_Despues:int|window_hard:Ventana_Dura:bool|location:Instalacion|equipment:Equipo|voltage_kv:Tension_kV:number|requires_outage:Requiere_Desconexion:bool|discipline:Disciplina|calendar_name:Calendario|priority:Prioridad:priority|cost_per_occurrence:Costo_por_Ejecucion:number|active:Activo:bool|horizon_end:Fin_Horizonte:date|wbs_code:Codigo_WBS|quantity_total:Cantidad_Total:number|unit:Unidad|batch_size:Tamano_Lote:number|productivity_day_team:Rendimiento_Dia_Equipo:number|team_names:Equipos:skills|balance_tolerance_batches:Tolerancia_Equidad_Lotes:int|batch_continuous_in_shift:Lote_Continuo_En_Turno:bool|batch_full_day:Lote_Dia_Completo:bool|productivity_hour_team:Rendimiento_Hora_Equipo:number|productive_hours_day:Horas_Productivas_Dia:number|route_code:Ruta|is_route:Es_Ruta:bool|is_daily_round:Ronda_Diaria:bool"),
    "Proyecto": columns("name:Nombre:text!|mode:Modo:mode!|start_date:Fecha_Inicio:date!|end_date:Fecha_Fin:date|calendar_name:Calendario|currency:Moneda|data_date:Fecha_Corte:date|description:Descripcion"),
}

# Ruta reutiliza el contrato de recurrencia; la hoja es su editor dedicado.
SCHEMAS["Rutas"] = [Column(c.field, {"equipment":"Objeto", "location":"Planta"}.get(c.field,c.title),c.kind,c.required)
                    for c in SCHEMAS["Plan_Maestro"] if c.field != "route_code"]

ENTITIES = {
    "Rutas": ("periodic_tasks", domain.PeriodicTask),
    "Equipos": ("teams", domain.WorkTeam),
    "Calendarios": ("calendars", domain.Calendar), "Recursos": ("resources", domain.Resource),
    "WBS": ("wbs", domain.WBSNode), "Actividades": ("activities", domain.Activity),
    "Relaciones": ("relations", domain.Relation), "Asignaciones": ("assignments", domain.Assignment),
    "Avance": ("progress", domain.ProgressEntry), "Plan_Maestro": ("periodic_tasks", domain.PeriodicTask),
    "Proyecto": ("project", domain.Project),
}
CHOICES = {
    "bool": ["SI", "NO"], "resource": ["trabajador", "cuadrilla", "equipo", "material"],
    "constraint": ["ASAP", "ALAP", "SNET", "SNLT", "FNET", "FNLT", "MSO", "MFO"],
    "relation": ["FS", "SS", "FF", "SF"], "frequency": ["D", "W", "M", "Q", "S", "A", "ND", "NW", "NM", "NA", "DH"],
    "mode": ["proyecto", "mantenimiento"], "status": ["no_iniciada", "en_curso", "terminada"],
}


def extra_column(field: str) -> Column:
    """Tipos de campos de dominio opcionales conservados durante la exportación."""
    if field.endswith(("_start", "_finish", "_date")) or field in {"first_due", "horizon_end", "date"}:
        kind = "date"
    elif field in {"critical", "locked", "active", "window_hard", "requires_outage", "is_work_team"}:
        kind = "bool"
    elif field in {"total_float", "free_float"}:
        kind = "signed_int"
    elif field in {"remaining_duration", "occurrence_index", "sort_order"} or field.endswith("_id") or field == "id":
        kind = "int"
    elif field == "percent_complete":
        kind = "percent"
    elif field in {"actual_hours", "actual_cost", "quantity"}:
        kind = "number"
    elif field == "status":
        kind = "status"
    else:
        kind = "text"
    return Column(field, field, kind)


def all_columns(sheet: str) -> list[Column]:
    """Columnas visibles y extensiones del dominio aceptadas por el lector."""
    result = list(SCHEMAS[sheet])
    if sheet in ENTITIES:
        known = {c.field for c in result}
        result.extend(extra_column(f.name) for f in fields(ENTITIES[sheet][1]) if f.name not in known)
        if sheet == "Actividades":
            result.append(Column("periodic_task_code", "Tarea_Periodica"))
    return result
