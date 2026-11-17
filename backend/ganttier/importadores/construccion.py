"""Supuestos explícitos y adaptación a la plantilla interna validada."""
from __future__ import annotations
from datetime import date
from io import BytesIO
import json
import math
import hashlib
from copy import deepcopy
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .excel import normal

DEFAULT_HH = {'Diario': .15, 'Mensual': 1.0, 'Trimestral': 1.5, 'Semestral': 2.0, 'Anual': 3.0}


class CalendarioSupuesto(BaseModel):
    model_config = ConfigDict(extra='forbid')
    semana: str = Field(default='1111100', pattern=r'^[01]{7}$')
    horas_dia: float = Field(default=8, gt=0, le=24, allow_inf_nan=False)
    pais: str = Field(default='CL', max_length=3)

    @field_validator('semana')
    @classmethod
    def semana_activa(cls, value):
        if '1' not in value:
            raise ValueError('Seleccione al menos un día laboral.')
        return value


class Supuestos(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fecha_inicio: date
    fecha_fin: date
    hh_frecuencia: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_HH))
    hh_actividad: dict[str, float] = Field(default_factory=dict)
    uf_por_hh: float = Field(default=.75, gt=0, allow_inf_nan=False)
    dotacion: int = Field(default=1, ge=1, le=100)
    dotacion_actividad: dict[str, int] = Field(default_factory=dict)
    duracion_actividad: dict[str, int] = Field(default_factory=dict)
    calendario: CalendarioSupuesto = Field(default_factory=CalendarioSupuesto)
    politica_meses: str = 'respetar_marcas'
    politica_esfuerzo: str = 'por_frecuencia'
    equipos: list[dict] = Field(default_factory=list)
    lotes: dict[str, dict] = Field(default_factory=dict)
    rutas: list[dict] = Field(default_factory=list)
    recursos: list[dict] = Field(default_factory=list)
    sitio: str = 'Instalación por confirmar'
    moneda: str = 'CLP'

    @field_validator('hh_frecuencia', 'hh_actividad')
    @classmethod
    def horas_validas(cls, value):
        if any(not math.isfinite(v) or v < 0 for v in value.values()):
            raise ValueError('Las HH deben ser finitas y no negativas.')
        return value

    @field_validator('dotacion_actividad', 'duracion_actividad')
    @classmethod
    def enteros_positivos(cls, value):
        if any(v < 1 for v in value.values()):
            raise ValueError('Dotación y duración deben ser positivas.')
        return value

    @field_validator('politica_meses')
    @classmethod
    def meses_validos(cls, value):
        if value != 'respetar_marcas':
            raise ValueError('Esta versión permite respetar_marcas; corrija el mapeo para otra interpretación.')
        return value

    @field_validator('politica_esfuerzo')
    @classmethod
    def esfuerzo_valido(cls, value):
        if value not in {'por_frecuencia', 'precio_uf', 'referencia_eco'}:
            raise ValueError('Política de esfuerzo desconocida.')
        return value


def resolver_supuestos(lectura, provided):
    values = dict(lectura.sugeridos)
    if lectura.perfil.formato == 'e2_mp':
        values.update(politica_esfuerzo='precio_uf', moneda='UF')
    values.update(lectura.perfil.supuestos)
    values.update(provided or {})
    model = Supuestos.model_validate(values)
    if model.fecha_fin < model.fecha_inicio or (model.fecha_fin-model.fecha_inicio).days > 3660:
        raise ValueError('El horizonte debe ser creciente y no superar diez años.')
    result = model.model_dump(mode='json')
    if not set(DEFAULT_HH) <= set(result['hh_frecuencia']):
        raise ValueError('Indique las HH de las cinco frecuencias.')
    if lectura.perfil.formato == 'om_wbs' and result['politica_esfuerzo'] != 'por_frecuencia':
        raise ValueError('OM-WBS requiere la política por_frecuencia, con excepciones por actividad.')
    if lectura.perfil.formato == 'e2_mp' and result['politica_esfuerzo'] not in {'precio_uf', 'referencia_eco'}:
        raise ValueError('E2-MP requiere precio_uf o referencia_eco, con excepciones por actividad.')
    known = {item['codigo'] for item in lectura.items}
    for key in ('hh_actividad', 'dotacion_actividad', 'duracion_actividad'):
        if set(result[key])-known:
            raise ValueError(f'{key}: códigos desconocidos: {sorted(set(result[key])-known)}.')
    return result


def faltantes(lectura, supuestos):
    labels = {
        'fecha_inicio': ('Fecha inicial del programa', 'La referencia temporal requiere confirmación; en E2-MP año 1 no es una fecha contractual.'),
        'fecha_fin': ('Fin del horizonte', 'Confirmar el horizonte de planificación.'),
        'hh_frecuencia': ('HH totales por frecuencia', 'No hay HH medidas en la matriz.'),
        'hh_actividad': ('HH por actividad (excepciones)', 'Clave: código de origen; sustituye las HH por frecuencia/precio.'),
        'uf_por_hh': ('UF por HH para estimar esfuerzo', 'Coeficiente provisional; el precio puede incluir materiales y equipos.'),
        'dotacion': ('Dotación por ejecución', 'Plazas de planificación; no identifica trabajadores reales.'),
        'dotacion_actividad': ('Dotación por actividad', 'Excepciones por código de origen.'),
        'duracion_actividad': ('Duración por actividad', 'Excepciones en días laborables.'),
        'calendario': ('Calendario', 'Semana, jornada y feriados del país son supuestos editables.'),
        'equipos': ('Equipos de trabajo (opcional)', 'Vacío: no se inventan equipos de trabajo ni integrantes.'),
        'lotes': ('Cantidades y rendimientos para lotes (opcional)', 'Vacío: no se realiza reparto por lotes; requiere cantidades, unidad y rendimiento.'),
        'rutas': ('Rutas (opcional)', 'Vacío: no se agrupan tareas en rutas.'),
        'recursos': ('Recursos de personal (opcional)', 'Vacío: programa sin asignación nominal ni nivelación de capacidad.'),
        'politica_meses': ('Interpretación de meses', 'respetar_marcas conserva la matriz y sus contradicciones advertidas.'),
        'politica_esfuerzo': ('Estimación de esfuerzo', 'por_frecuencia, precio_uf o referencia_eco; nunca se considera un rendimiento observado.'),
        'sitio': ('Instalación', 'E2-MP no contiene un identificador estructurado de sitio.'),
        'moneda': ('Moneda', 'OM-WBS no contiene presupuesto; costo cero significa no informado.'),
    }
    result = [dict(clave=k, etiqueta=label, motivo=reason, valor=supuestos[k], estado='SUPUESTO') for k, (label, reason) in labels.items()]
    for item in lectura.excluidos:
        result.append(dict(clave=f"condicional:{item['codigo']}", etiqueta=item['nombre'], motivo=item['motivo'],
                           valor='Conservar sin programar; requiere completar cantidades en la fuente.', estado='PENDIENTE', origen=item['origen']))
    return result


def esfuerzo_om(item, s):
    code = item['codigo']
    hh = s['hh_actividad'].get(code, s['hh_frecuencia'][item['frecuencia']])
    crew = s['dotacion_actividad'].get(code, s['dotacion'])
    duration = s['duracion_actividad'].get(code, max(1, math.ceil(hh/(crew*s['calendario']['horas_dia']))))
    return hh, crew, duration


def esfuerzo_eco(item, s):
    code = item['codigo']
    hh = s['hh_actividad'].get(code, round(max(.5, item['precio']/s['uf_por_hh']), 6))
    crew = s['dotacion']
    if s['politica_esfuerzo'] == 'referencia_eco':
        eq, activity = normal(item['equipo']), normal(item['nombre'])
        outage = any(k in eq for k in ('gis', 'transformadordepoder', 'desconectador', 'celdas', 'cambiador')) or 'lavadodeaislacion' in activity
        protection = any(k in eq for k in ('proteccion', 'control', 'gabinetes'))
        crew = 3 if hh > 48 else (2 if outage or hh > 8 or protection else 1)
    crew = s['dotacion_actividad'].get(code, crew)
    duration = s['duracion_actividad'].get(code, max(1, math.ceil(hh/(crew*s['calendario']['horas_dia']))))
    return hh, crew, duration


def base_task(item, code, s, hh, crew, duration):
    return dict(code=code, name=item['nombre'], frequency='M', interval=1, duration_days=duration,
        work_hours=hh, crew_size=crew, first_due=s['fecha_inicio'], window_hard=True,
        location=s['sitio'], active=True, cost_per_occurrence=0, origen=deepcopy(item['origen']),
        codigo_origen=item['codigo'], supuestos=['HH', 'dotación', 'duración', 'calendario', 'ventanas', 'disponibilidad de personal'])


def plantilla_interna(draft, nombre=None):
    """Construye un libro en memoria y lo entrega al importador oficial."""
    import openpyxl
    from ganttier.excel_io.schema import SCHEMAS
    from ganttier.excel_io.importer import parse_workbook
    s = draft.supuestos
    book = openpyxl.Workbook(); book.remove(book.active)
    for sheet, cols in SCHEMAS.items():
        book.create_sheet(sheet).append([c.title for c in cols])
    def add(sheet, row):
        values = []
        for col in SCHEMAS[sheet]:
            value = row.get(col.field)
            if isinstance(value, bool): value = 'SI' if value else 'NO'
            if isinstance(value, list): value = ','.join(map(str, value))
            values.append(value)
        book[sheet].append(values)
    calendar_hash = hashlib.sha256(json.dumps(s['calendario'], sort_keys=True).encode()).hexdigest()[:10]
    calname = 'Importación asistida ' + calendar_hash
    project = dict(name=nombre or draft.fuente['nombre'].rsplit('.', 1)[0], mode='mantenimiento',
        start_date=s['fecha_inicio'], end_date=s['fecha_fin'], data_date=s['fecha_inicio'], calendar_name=calname,
        currency=s['moneda'], description='IMPORTACIÓN ASISTIDA. SUPUESTOS de planificación pendientes de validación operacional.\n'
        + json.dumps(s, ensure_ascii=False) + '\nFuente SHA-256: ' + draft.fuente['sha256'])
    # Proyecto es una hoja clave/valor en el contrato existente.
    book['Proyecto'].delete_rows(1, book['Proyecto'].max_row)
    book['Proyecto'].append(['Campo', 'Valor'])
    for col in SCHEMAS['Proyecto']:
        if col.field in project:
            book['Proyecto'].append([col.title, project[col.field]])
    add('Calendarios', dict(name=calname, workweek=s['calendario']['semana'], hours_per_day=s['calendario']['horas_dia'],
                           holidays_country=s['calendario']['pais'], holidays_apply_to_shift=True))
    for w in draft.wbs: add('WBS', w)
    for team in s['equipos']: add('Equipos', team)
    for resource in s['recursos']: add('Recursos', dict(resource, calendar_name=calname))
    for task in draft.maestras:
        row = {**task, 'calendar_name': calname}
        row.update(s['lotes'].get(task['code'], s['lotes'].get(task['codigo_origen'], {})))
        add('Plan_Maestro', row)
    for route in s['rutas']: add('Rutas', dict(route, calendar_name=calname))
    raw = BytesIO(); book.save(raw); book.close(); raw.seek(0)
    bundle = parse_workbook(raw, 'mantenimiento')
    if bundle.has_errors:
        raise ValueError('Plantilla interna inválida: ' + '; '.join(f'{i.sheet}/{i.row}: {i.message}' for i in bundle.issues if i.severity == 'error'))
    # Una celda vacía en la plantilla significa «predeterminado»; aquí la decisión
    # explícita país vacío significa «sin feriados», que el modelo admite como None.
    bundle.calendars[0]['holidays_country'] = s['calendario']['pais'] or None
    return bundle, raw.getvalue()


def validar_opciones(draft):
    from ganttier.excel_io.schema import SCHEMAS
    s = draft.supuestos
    codes = {t['code'] for t in draft.maestras} | {t['codigo_origen'] for t in draft.maestras}
    unknown = set(s['lotes'])-codes
    if unknown:
        raise ValueError('Lotes: códigos desconocidos: '+', '.join(sorted(unknown)))
    allowed_batch = {'quantity_total','unit','batch_size','productivity_day_team','team_names',
        'balance_tolerance_batches','batch_continuous_in_shift','batch_full_day',
        'productivity_hour_team','productive_hours_day','route_code'}
    for code, entry in s['lotes'].items():
        if set(entry)-allowed_batch:
            raise ValueError(f'{code}: campos de lotes no permitidos: {sorted(set(entry)-allowed_batch)}.')
    for key, sheet in [('equipos','Equipos'),('rutas','Rutas'),('recursos','Recursos')]:
        allowed = {c.field for c in SCHEMAS[sheet]}
        for entry in s[key]:
            if set(entry)-allowed:
                raise ValueError(f'{key}: campos desconocidos: {sorted(set(entry)-allowed)}.')
