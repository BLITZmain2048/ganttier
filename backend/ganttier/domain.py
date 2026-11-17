"""Objetos de dominio compartidos entre módulos de Ganttier.

CONTRATO: estos tipos son la interfaz entre backend núcleo (A1), motor (A2), mantenimiento (A3),
control (A4) e informes/Excel (A5). No renombrar ni quitar campos. Se pueden AGREGAR campos
opcionales al final de cada dataclass (con valor por defecto) documentándolo en docs/decisiones.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, Literal, Optional

Mode = Literal["proyecto", "mantenimiento"]
RelType = Literal["FS", "SS", "FF", "SF"]
ConstraintType = Literal["ASAP", "ALAP", "SNET", "SNLT", "FNET", "FNLT", "MSO", "MFO"]
Status = Literal["no_iniciada", "en_curso", "terminada"]


def to_dict(obj: Any) -> dict:
    """Serializa dataclass -> dict JSON-friendly (fechas en ISO)."""
    def conv(v):
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        return v
    return conv(asdict(obj))


# --------------------------------------------------------------------------- maestros
@dataclass
class Calendar:
    id: int
    name: str
    workweek: str = "1111100"            # lunes..domingo, 1 = laborable
    hours_per_day: float = 8.0
    holidays_country: Optional[str] = "CL"
    shift_pattern: Optional[str] = None  # "7x7", "4x3", "14x14"...
    shift_anchor: Optional[date] = None
    holidays_apply_to_shift: bool = False
    exceptions: list["CalendarException"] = field(default_factory=list)


@dataclass
class CalendarException:
    date: date
    working: bool
    hours: float = 0.0
    reason: str = ""


@dataclass
class Project:
    id: int
    name: str
    mode: Mode = "proyecto"
    start_date: date = date.today()
    end_date: Optional[date] = None          # horizonte (obligatorio en mantenimiento)
    data_date: Optional[date] = None         # fecha de corte (control)
    calendar_id: Optional[int] = None
    description: str = ""
    currency: str = "CLP"
    active_baseline_id: Optional[int] = None


@dataclass
class Resource:
    id: int
    name: str
    kind: Literal["trabajador", "cuadrilla", "equipo", "material"] = "trabajador"
    skills: list[str] = field(default_factory=list)   # roles/habilidades: "electrico", "SSEE", "protecciones"...
    capacity_hours_day: float = 8.0                    # capacidad normal por día laborable (por unidad)
    max_units: float = 1.0                             # unidades disponibles (cuadrilla de 4 -> 4)
    calendar_id: Optional[int] = None
    cost_hour: float = 0.0
    overtime_cost_hour: float = 0.0
    max_overtime_hours_day: float = 0.0
    project_id: Optional[int] = None                   # None = recurso global (compartido)
    active: bool = True
    email: str = ""
    is_work_team: bool = False              # cuadrilla con reparto por cantidad (v4)


@dataclass
class WBSNode:
    id: int
    project_id: int
    code: str
    name: str
    parent_id: Optional[int] = None
    sort_order: int = 0


@dataclass
class Activity:
    id: int
    project_id: int
    code: str
    name: str
    wbs_id: Optional[int] = None
    duration_days: int = 1
    work_hours: float = 0.0                      # HH totales presupuestadas
    cost_budget: float = 0.0
    calendar_id: Optional[int] = None
    # programación macro / ventana
    macro_month: Optional[str] = None            # "YYYY-MM"
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    window_hard: bool = False
    # restricciones
    constraint_type: ConstraintType = "ASAP"
    constraint_date: Optional[date] = None
    priority: int = 50                           # 0..100 (mayor = más prioritaria)
    # resultado de programación
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    early_start: Optional[date] = None
    early_finish: Optional[date] = None
    late_start: Optional[date] = None
    late_finish: Optional[date] = None
    total_float: Optional[int] = None
    free_float: Optional[int] = None
    critical: bool = False
    # avance
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    percent_complete: float = 0.0                # 0..100 (físico)
    remaining_duration: Optional[int] = None
    actual_hours: float = 0.0
    actual_cost: float = 0.0
    status: Status = "no_iniciada"
    # atributos de mantenimiento / eléctricos (opcionales)
    periodic_task_id: Optional[int] = None
    occurrence_index: Optional[int] = None
    due_date: Optional[date] = None
    location: str = ""                           # instalación / subestación / sitio
    equipment: str = ""                          # equipo / paño / TAG
    voltage_kv: Optional[float] = None
    requires_outage: bool = False
    discipline: str = ""
    required_skill: str = ""                     # rol requerido si no hay asignación explícita
    crew_size: float = 1.0
    notes: str = ""
    locked: bool = False                         # el optimizador no la mueve
    batch_index: Optional[int] = None
    quantity: Optional[float] = None
    unit: str = ""
    team_id: Optional[int] = None            # recurso cuadrilla, no activo físico/TAG
    equity_mode: str = "occurrence"         # occurrence | cycle
    duration_hours: Optional[float] = None   # horas del equipo, HH = horas × integrantes
    checklist_codes: list[str] = field(default_factory=list)


@dataclass
class Relation:
    id: int
    project_id: int
    pred_id: int
    succ_id: int
    type: RelType = "FS"
    lag_days: int = 0


@dataclass
class Assignment:
    id: int
    activity_id: int
    resource_id: Optional[int] = None
    skill: str = ""                  # si resource_id es None: demanda genérica por rol
    units: float = 1.0
    work_hours: float = 0.0          # HH de esta asignación (0 -> units * duración * horas/día)
    actual_hours: float = 0.0


@dataclass
class PeriodicTask:
    id: int
    project_id: int
    code: str
    name: str
    frequency: Literal["D", "W", "M", "Q", "S", "A", "ND", "NW", "NM", "NA", "DH"] = "M"
    interval: int = 1
    duration_days: int = 1
    work_hours: float = 8.0                   # HH por ocurrencia
    crew_size: float = 1.0
    required_skill: str = ""
    resource_id: Optional[int] = None         # asignación fija opcional
    first_due: Optional[date] = None
    preferred_months: str = ""                # "1,4,7,10"
    preferred_weekday: Optional[int] = None   # 0 = lunes
    day_of_month: Optional[int] = None
    tol_before_days: int = 7
    tol_after_days: int = 7
    window_hard: bool = False
    location: str = ""
    equipment: str = ""
    voltage_kv: Optional[float] = None
    requires_outage: bool = False
    discipline: str = ""
    calendar_id: Optional[int] = None
    wbs_code: str = ""
    priority: int = 50
    cost_per_occurrence: float = 0.0
    active: bool = True
    horizon_end: Optional[date] = None        # no generar después de esta fecha
    quantity_total: Optional[float] = None
    unit: str = ""
    batch_size: Optional[float] = None
    productivity_day_team: Optional[float] = None
    team_ids: list[int] = field(default_factory=list)
    balance_tolerance_batches: int = 1
    batch_continuous_in_shift: bool = False
    batch_full_day: bool = False
    productivity_hour_team: Optional[float] = None
    productive_hours_day: Optional[float] = None
    route_code: str = ""
    is_route: bool = False
    is_daily_round: bool = False


@dataclass
class Occurrence:
    """Ocurrencia generada; A1 la persiste como Activity (periodic_task_id, occurrence_index)."""
    periodic_task_id: int
    occurrence_index: int
    code: str                  # f"{task.code}-{index:04d}"
    name: str
    due_date: date
    window_start: date
    window_end: date
    duration_days: int
    work_hours: float
    crew_size: float
    required_skill: str
    resource_id: Optional[int]
    calendar_id: Optional[int]
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    window_hard: bool = False
    location: str = ""
    equipment: str = ""
    requires_outage: bool = False
    priority: int = 50
    activity_id: Optional[int] = None     # si ya existe en BD
    batch_index: Optional[int] = None
    quantity: Optional[float] = None
    unit: str = ""
    team_id: Optional[int] = None            # recurso cuadrilla, no activo físico/TAG
    equity_mode: str = "occurrence"         # occurrence | cycle
    batch_continuous_in_shift: bool = False
    duration_hours: Optional[float] = None   # horas del equipo, HH = horas × integrantes
    checklist_codes: list[str] = field(default_factory=list)


@dataclass
class Baseline:
    id: int
    project_id: int
    name: str
    created_at: datetime


@dataclass
class BaselineActivity:
    baseline_id: int
    activity_id: int
    start: Optional[date]
    finish: Optional[date]
    work_hours: float
    cost: float
    duration_days: int


@dataclass
class ProgressEntry:
    id: int
    activity_id: int
    date: date                       # fecha del registro (normalmente = fecha de corte)
    percent_complete: float          # % físico acumulado a esa fecha
    actual_hours: float = 0.0        # HH reales del período del registro (NO acumuladas)
    actual_cost: float = 0.0
    resource_id: Optional[int] = None   # trabajador que ejecutó (desempeño)
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    note: str = ""


# --------------------------------------------------------------------------- motor
@dataclass
class ScheduleProblem:
    project: Project
    activities: list[Activity]
    relations: list[Relation]
    resources: list[Resource]
    assignments: list[Assignment]
    calendars: dict[int, Any]            # id -> calendars.WorkCalendar
    default_calendar_id: int
    horizon_start: date
    horizon_end: date
    data_date: Optional[date] = None
    periodic_tasks: list[PeriodicTask] = field(default_factory=list)


@dataclass
class CPMActivityResult:
    activity_id: int
    early_start: date
    early_finish: date
    late_start: date
    late_finish: date
    total_float: int
    free_float: int
    critical: bool


@dataclass
class CPMResult:
    results: dict[int, CPMActivityResult]
    project_finish: date
    critical_path: list[int]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ObjectiveWeights:
    level: float = 0.0       # nivelar carga
    makespan: float = 0.0    # minimizar plazo
    window: float = 1.0      # respetar ventanas macro
    cost: float = 0.0        # minimizar costo / HH extra
    peak: float = 0.0        # minimizar pico diario/semanal por habilidad (opt-in)

    def normalized(self) -> "ObjectiveWeights":
        s = self.level + self.makespan + self.window + self.cost + self.peak
        if s <= 0:
            return ObjectiveWeights(0, 0, 1, 0)
        return ObjectiveWeights(self.level / s, self.makespan / s, self.window / s, self.cost / s, self.peak / s)


@dataclass
class OptimizeOptions:
    method: Literal["auto", "heuristic", "cpsat"] = "auto"
    time_limit_s: float = 30.0
    level_period: Literal["day", "week"] = "day"
    group_by: Literal["resource", "skill"] = "skill"
    respect_resource_limits: bool = True     # capacidad como restricción dura (si False, solo penaliza)
    allow_overtime: bool = True
    assign_workers: bool = True              # mantenimiento: asignar trabajador concreto por habilidad
    cpsat_max_activities: int = 3000
    seed: int = 0
    group_outages: bool = True
    role_mode: Literal["fractional", "indivisible"] = "fractional"
    block_hours: float = 4.0
    horizon_blocks: Optional[int] = None
    target_code: Optional[str] = None


@dataclass
class LoadSeries:
    key: str                  # nombre del recurso o rol
    capacity: list[float]     # por período
    load: list[float]


@dataclass
class LoadProfile:
    period: str               # "day" | "week" | "month"
    labels: list[str]         # fechas ISO de inicio de cada período
    series: list[LoadSeries]


@dataclass
class OptimizeMetrics:
    finish: Optional[date]
    peak_load: float
    load_std: float                    # desviación estándar de la carga (promedio de series)
    overload_hours: float              # HH sobre capacidad normal
    overtime_hours: float
    overtime_cost: float
    days_out_of_window: int
    activities_out_of_window: int
    objective_value: float


@dataclass
class OptimizeResult:
    starts: dict[int, date]                        # activity_id -> nuevo inicio (para ocurrencias sin id: índice en la lista)
    finishes: dict[int, date]
    worker_assignment: dict[int, int] = field(default_factory=dict)   # activity_id -> resource_id
    before: Optional[OptimizeMetrics] = None
    after: Optional[OptimizeMetrics] = None
    profile_before: Optional[LoadProfile] = None
    profile_after: Optional[LoadProfile] = None
    method_used: str = ""
    status: str = ""                   # "optimo", "factible", "heuristico", "infactible"
    elapsed_s: float = 0.0
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- control
@dataclass
class ActivityView:
    """Actividad con fechas vigentes para control (A1 la arma desde Activity)."""
    id: int
    code: str
    name: str
    start: Optional[date]
    finish: Optional[date]
    duration_days: int
    work_hours: float
    cost_budget: float
    percent_complete: float
    actual_start: Optional[date]
    actual_finish: Optional[date]
    actual_hours: float
    actual_cost: float
    calendar_id: Optional[int]
    wbs_id: Optional[int] = None
    resource_ids: list[int] = field(default_factory=list)


@dataclass
class SCurve:
    period: str
    basis: str
    labels: list[str]                  # fin de cada período (ISO)
    planned_cum: list[float]           # % acumulado planificado (línea base)
    earned_cum: list[Optional[float]]  # % ganado acumulado (hasta data_date, luego None)
    actual_cum: list[Optional[float]]  # HH/costo real acumulado como % del presupuesto
    forecast_cum: list[Optional[float]]  # proyección desde data_date
    planned_abs: list[float]
    earned_abs: list[Optional[float]]
    actual_abs: list[Optional[float]]
    forecast_abs: list[Optional[float]]
    budget_total: float
    data_date: Optional[date]
    forecast_finish: Optional[date]
    planned_finish: Optional[date]


@dataclass
class EVMResult:
    data_date: Optional[date]
    basis: str
    BAC: float
    PV: float
    EV: float
    AC: float
    SV: float
    CV: float
    SPI: Optional[float]
    CPI: Optional[float]
    ES_days: Optional[float]           # earned schedule (días desde inicio)
    SPI_t: Optional[float]
    EAC: Optional[float]
    ETC: Optional[float]
    VAC: Optional[float]
    TCPI: Optional[float]
    planned_finish: Optional[date]
    forecast_finish: Optional[date]
    percent_planned: float
    percent_complete: float


@dataclass
class WorkerKPI:
    resource_id: int
    name: str
    assigned_activities: int
    completed_activities: int
    completed_on_time: int
    due_in_period: int
    compliance_pct: Optional[float]    # cumplimiento
    planned_hours: float
    earned_hours: float
    actual_hours: float
    hh_efficiency: Optional[float]     # HH ganadas / HH reales
    avg_delay_days: Optional[float]
    SPI: Optional[float]
    CPI: Optional[float]
    pending_activities: int


# --------------------------------------------------------------------------- informes
@dataclass
class ReportRow:
    code: str
    name: str
    start: Optional[date]
    finish: Optional[date]
    percent_complete: float
    status: str
    responsible: str = ""
    baseline_finish: Optional[date] = None
    delay_days: Optional[int] = None
    location: str = ""


@dataclass
class ReportData:
    project: Project
    period_type: Literal["weekly", "monthly", "annual"]
    period_start: date
    period_end: date
    generated_at: datetime
    evm: EVMResult
    scurve: SCurve
    load: LoadProfile
    performance: list[WorkerKPI]
    scheduled_in_period: list[ReportRow]
    completed_in_period: list[ReportRow]
    delayed: list[ReportRow]
    lookahead: list[ReportRow]            # próximas del período siguiente
    milestones: list[ReportRow]
    kpis: dict[str, Any] = field(default_factory=dict)   # textos/valores libres para el resumen
    notes: str = ""
    team_distribution: list[dict] = field(default_factory=list)
    team_alerts: list[str] = field(default_factory=list)
    route_checklist: list[dict] = field(default_factory=list)
    critical: list[ReportRow] = field(default_factory=list)   # R3: actividades críticas del programa vigente
    skill_load: LoadProfile | None = None                      # R3: carga semanal por habilidad


# --------------------------------------------------------------------------- importación
@dataclass
class ImportIssue:
    sheet: str
    row: int
    field: str
    message: str
    severity: Literal["error", "advertencia"] = "error"


@dataclass
class ImportBundle:
    """Datos leídos de una plantilla. Las referencias entre entidades usan `code`/`name` (no ids).

    Cada lista contiene dicts con los nombres de campo de la dataclass correspondiente, más:
      activities[i]["wbs_code"], relations[i]["pred_code"]/["succ_code"],
      assignments[i]["activity_code"]/["resource_name"], progress[i]["activity_code"]/["resource_name"],
      resources[i]["calendar_name"], periodic_tasks[i]["resource_name"]/["calendar_name"].
    """
    mode: Mode
    project: dict
    calendars: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    wbs: list[dict] = field(default_factory=list)
    activities: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    assignments: list[dict] = field(default_factory=list)
    periodic_tasks: list[dict] = field(default_factory=list)
    progress: list[dict] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)

    teams: list[dict] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)


# ---------------------------------------------------------------- semántica de la fecha de corte
# La fecha de corte se interpreta al FINAL de la jornada, igual que el control (EVM, curva S, avance
# registrado «al corte» incluye ese día) y la «fecha de estado» de MS Project: el trabajo remanente y
# las actividades no iniciadas se programan desde el día siguiente. Excepción: un corte igual (o
# anterior) al inicio del proyecto significa «aún no comienza» y permite iniciar ese mismo día.

def schedule_floor(cutoff, project_start):
    """Primer día en que puede programarse trabajo aún no ejecutado."""
    from datetime import timedelta
    if cutoff is None or cutoff <= project_start:
        return project_start
    return cutoff + timedelta(days=1)


def remaining_work_start(cutoff, actual_start):
    """Primer día del remanente de una actividad iniciada (el día de corte ya está informado)."""
    from datetime import timedelta
    if cutoff is None or actual_start > cutoff:
        return actual_start
    return cutoff + timedelta(days=1)


@dataclass
class ForecastScenario:
    """Escenario de control con causa explícita cuando no puede calcularse."""
    name: str
    formula: str
    value: Optional[float]
    status: str
    cause: Optional[str] = None
    finish_date: Optional[date] = None
    date_cause: Optional[str] = None


@dataclass
class QualityActivity:
    """Referencia trazable a una actividad evaluada."""
    id: int
    code: str


@dataclass
class QualityFinding:
    """Resultado explicable de una regla; el conteo usa la unidad indicada."""
    rule: str
    severity: str
    activities: list[QualityActivity]
    population: int
    count: int
    action: str
    description: str
    status: str = "hallazgo"
    population_unit: str = "actividades"
    relation_ids: list[int] = field(default_factory=list)


@dataclass
class ScheduleQualityResult:
    """Hallazgos y cobertura de todas las reglas del diagnóstico."""
    profile: str
    data_date: date
    findings: list[QualityFinding]
    rules: list[QualityFinding]


@dataclass
class EVMGroup:
    """Indicadores recalculados para un grupo disjunto de actividades."""
    key: str
    name: str
    activity_ids: list[int]
    evm: EVMResult


@dataclass
class EVMReconciliation:
    """Suma de grupos y diferencias respecto del cálculo completo del proyecto."""
    name: str
    BAC: float
    PV: float
    EV: float
    AC: float
    SPI: Optional[float]
    CPI: Optional[float]
    differences: dict[str, float]
    matches: bool
    tolerance: float = 1e-6


@dataclass
class AggregatedEVM:
    """EVM por grupo, total independiente y fila de conciliación."""
    group: str
    basis: str
    groups: list[EVMGroup]
    total: EVMResult
    reconciliation: EVMReconciliation


@dataclass
class WorkTeam:
    """Vista de intercambio del recurso cuadrilla y su calendario asociado."""
    name: str
    regime: str = "5x2"
    cycle_start: Optional[date] = None
    members: int = 1
    hours_day: float = 8.0
    holidays_work: Optional[bool] = None
    calendar_name: str = ""
