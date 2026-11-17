"""Adaptadores literales MS Project y Primavera P6; no acceden a SQLite."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path

from openpyxl.utils.cell import get_column_letter
from .modelos import Perfil, Borrador
from ._p6_msp_lectura import abrir_tablas, issue
from ._p6_msp_campos import (LOOKUP, table, identifier, number, boolean, fecha, normal,
                            percent, duration, integral, work, predecessors,
                            resource_names, CONSTRAINTS)
from ._p6_msp_modelo import aux_rows, calendars, wbs_rows, check_graph, valid_week, valid_hours

DEFAULTS = dict(fecha_inicio=None, fecha_fin=None, fecha_corte=None,
                calendario=dict(semana='1111100', horas_dia=8.0, pais=''),
                moneda='CLP', dotacion=1.0, habilidad='',
                redondeo_dias='superior', fechas_como_ventana=False,
                hh_ausentes='duracion_por_jornada_y_dotacion',
                recursos_ausentes='sin_recursos_nominales', costo_ausente=0.0)
LABELS = {
    'fecha_inicio': 'Inicio del horizonte (propuesta desde fechas de la fuente o 2027-01-04)',
    'fecha_fin': 'Fin del horizonte (propuesto desde la fuente o dos años)',
    'fecha_corte': 'Corte del avance (propuesto desde fechas reales o inicio)',
    'calendario': 'Calendario para datos ausentes: semana, horas/día y país; nombre solo no define jornada',
    'moneda': 'Moneda de costos; sin conversión monetaria',
    'dotacion': 'Dotación si no hay asignaciones; capacidades nominales ausentes = una unidad',
    'habilidad': 'Habilidad cuando la fuente no informa especialidad',
    'redondeo_dias': 'Días enteros: superior redondea hacia +infinito; rechazar bloquea fracciones',
    'fechas_como_ventana': 'Convertir fechas previstas en ventanas duras (desactivado por defecto)',
    'hh_ausentes': 'HH ausentes = duración exacta × jornada × unidades; reparto proporcional por unidades',
    'recursos_ausentes': 'No inventar personas; recurso nombrado sin atributos usa jornada y capacidad de una unidad',
    'costo_ausente': 'Costo no informado: cero no significa costo real medido',
}


def assumptions(profile, provided):
    s = deepcopy(DEFAULTS)
    for values in (profile.supuestos, provided):
        if set(values) - set(s): raise ValueError('Supuestos desconocidos: ' + str(sorted(set(values) - set(s))))
        for k, v in values.items():
            if k == 'calendario':
                if not isinstance(v, dict) or set(v) - set(s[k]): raise ValueError('Calendario supuesto inválido.')
                s[k].update(v)
            else: s[k] = v
    s['calendario']['semana'] = valid_week(s['calendario']['semana'])
    s['calendario']['horas_dia'] = valid_hours(s['calendario']['horas_dia'])
    if s['calendario']['pais'] not in ('', 'CL', None): raise ValueError('País de feriados: CL o vacío.')
    s['dotacion'] = number(s['dotacion'])
    if not 0 < s['dotacion'] <= 10000: raise ValueError('Dotación fuera de rango (0, 10000].')
    if s['redondeo_dias'] not in ('superior', 'rechazar'): raise ValueError('Redondeo desconocido.')
    s['fechas_como_ventana'] = boolean(s['fechas_como_ventana'])
    if not isinstance(s['moneda'], str) or not s['moneda'].strip(): raise ValueError('Indique moneda.')
    if not isinstance(s['habilidad'], str): raise ValueError('Habilidad debe ser texto.')
    for k in ('hh_ausentes', 'recursos_ausentes'):
        if s[k] != DEFAULTS[k]: raise ValueError(f'Política {k} no admitida.')
    s['costo_ausente'] = number(s['costo_ausente'])
    for k in ('fecha_inicio', 'fecha_fin', 'fecha_corte'):
        if s[k] is not None: s[k] = fecha(s[k])
    return s


class _Adaptador:
    extensiones = {'.xlsx', '.csv'}

    def detectar(self, path):
        if Path(path).suffix.lower() not in self.extensiones: return []
        found = []
        try:
            with abrir_tablas(path, []) as tables:
                for sheet in tables.names:
                    if normal(sheet) in {'instrucciones', 'instructions'}: continue
                    rows = tables.rows(sheet)
                    try:
                        for i, cells in rows:
                            if i > 30: break
                            keys = {normal(c.value) for c in cells}
                            if self.formato == 'msp_excel':
                                distinctive = bool(keys & {'nombredetarea', 'taskname'}) and bool(keys & {'id', 'uniqueid', 'idunico'})
                            else:
                                distinctive = ({'activityid', 'activityname'} <= keys or {'taskcode', 'taskname', 'taskid'} <= keys)
                                # task_name is a P6 identifier only with task_id and task_code.
                            mapping = {LOOKUP[k]: get_column_letter(j) for j, c in enumerate(cells, 1) if (k := normal(c.value)) in LOOKUP}
                            if not distinctive or not {'code', 'name', 'duration'} <= set(mapping): continue
                            if self.formato == 'msp_excel' and {'taskcode', 'taskid'} <= keys: continue
                            p = Perfil(id=self.formato.replace('_', '-'), nombre=self.nombre, formato=self.formato,
                                       hoja=sheet, fila_encabezado=i, mapeo=mapping)
                            found.append(dict(formato=self.formato, puntaje=90 + min(9, len(mapping)-3),
                                              motivos=['Identificador, nombre y duración específicos del exporte'], perfil=p.model_dump()))
                            break
                    finally: rows.close()
        except (ValueError, OSError): return []
        return found

    def previsualizar(self, path, perfil, supuestos):
        path = Path(path); errors = []; excluded = []
        s = assumptions(perfil, supuestos)
        with abrir_tablas(path, errors) as tables:
            source = dict(nombre=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            sheet = perfil.hoja or ('CSV' if path.suffix.lower() == '.csv' else tables.names[0])
            rows = list(table(tables, sheet, errors, perfil.mapeo, perfil.fila_encabezado))
            aux = {key: aux_rows(tables, key, errors) for key in ('CALENDAR', 'PROJWBS', 'RSRC', 'TASKPRED', 'TASKRSRC')} if self.formato == 'p6_excel' else {}
        cals, calrefs, default_cal = calendars(aux.get('CALENDAR', []), s)
        cal_by_name = {c['name']: c for c in cals}
        wbs, wbsrefs = wbs_rows(aux.get('PROJWBS', []))
        wbs_codes = {w['code'] for w in wbs}
        activities, relations, resources, assignments, progress = [], [], [], [], []
        act_by_code, task_ids, resource_ids, resource_by_name = {}, {}, {}, {}
        row_by_code, metadata, pending, levels, seen = {}, {}, [], [], set()

        def calendar_for(row):
            ref = row.get('calendar')
            if not ref: return default_cal
            if ref in calrefs: return calrefs[ref]
            if aux.get('CALENDAR') or normal(row.headers[row.mapping['calendar']-1]) == 'clndrid':
                row.error('calendar', f'Calendario inexistente: {ref}.', 'referencia_inexistente')
                return default_cal
            # Single-sheet Calendar is only a name, not its working-time definition.
            name = ref + ' · SUPUESTO ' + default_cal.rsplit(' · ', 1)[-1]
            if name not in cal_by_name:
                cal = dict(cal_by_name[default_cal], name=name)
                cals.append(cal); cal_by_name[name] = cal
                row.error('calendar', f'{ref}: jornada no informada; se aplica el calendario SUPUESTO.', 'calendario_supuesto', 'advertencia')
            calrefs[ref] = name
            return name

        def days(row, field, cal, required=False):
            raw = row.get(field, default=None, required=required)
            if raw is None: return None, None
            try:
                exact = duration(raw, cal['hours_per_day'], cal['workweek'], raw_hours=row.raw_hours(field))
                if exact > 36600: raise ValueError('Duración superior a 36.600 días.')
                n = integral(exact, s['redondeo_dias'])
                if not math.isclose(exact, n): row.error(field, f'{exact:g} días → {n} días por SUPUESTO de redondeo.', 'redondeo', 'advertencia')
                return n, exact
            except ValueError as exc: row.error(field, str(exc)); return None, None

        for row in rows:
            code, name = row.get('code', required=True), row.get('name', required=True)
            if not code or not name:
                excluded.append(dict(origen=row.origin(), motivo='ID o nombre ausente; importación bloqueada.')); continue
            if code in seen: row.error('code', f'Código duplicado: {code}.'); continue
            seen.add(code)
            raw_name = str(row.cell('name').value)
            level = row.get('level', number)
            if level is None: level = 1 + (len(raw_name) - len(raw_name.lstrip(' \t'))) // 2
            if level < 1 or level > 64 or level != int(level): row.error('level', 'Nivel de esquema: entero entre 1 y 64.'); level = 1
            levels.append((row, code, name, int(level)))

        stack = []
        for idx, (row, code, name, level) in enumerate(levels):
            while stack and stack[-1][0] >= level: stack.pop()
            if level > (stack[-1][0] + 1 if stack else 1): row.error('level', 'Salto de nivel sin resumen padre.', 'jerarquia_invalida')
            typ = normal(row.get('type', default=''))
            summary = row.get('summary', boolean, False) or typ in {'wbssummary', 'ttwbssum'}
            child = idx+1 < len(levels) and levels[idx+1][3] > level
            if child: summary = True
            if summary:
                summary_ref = row.get('wbs_id')
                wc = wbsrefs.get(summary_ref) if summary_ref else row.get('wbs', default='WBS_' + code)
                if summary_ref and wc is None:
                    row.error('wbs_id', f'WBS inexistente: {summary_ref}.', 'referencia_inexistente')
                    wc = 'WBS_' + code
                if wc in wbs_codes and not summary_ref: row.error('wbs', f'Código WBS duplicado: {wc}.')
                elif wc in wbs_codes: pass  # PROJWBS ya conserva nombre y padre.
                else:
                    wbs.append(dict(code=wc, name=name, parent_code=stack[-1][1] if stack else None, sort_order=len(wbs)))
                    wbs_codes.add(wc)
                stack.append((level, wc))
                excluded.append(dict(codigo=code, nombre=name, origen=row.origin(), motivo='Fila resumen conservada como WBS; no suma duración ni HH.'))
                if row.get('predecessors') or row.get('resources'):
                    row.error('summary', 'Relaciones/asignaciones en resumen requieren trasladarse a actividades.', 'resumen_con_demanda')
                continue
            if typ and typ not in {'taskdependent', 'resourcedependent', 'startmilestone', 'finishmilestone', 'levelofeffort',
                                   'tttask', 'ttrsrc', 'ttmile', 'ttfinmile', 'ttloe'}:
                row.error('type', f'Tipo de actividad no admitido: {row.get("type")}.')
            if typ in {'levelofeffort', 'ttloe', 'resourcedependent', 'ttrsrc'}:
                row.error('type', 'Se conserva duración explícita; Ganttier no reproduce reglas automáticas P6 de este tipo.', 'tipo_aproximado', 'advertencia')
            calname = calendar_for(row); cal = cal_by_name[calname]
            dur, exact = days(row, 'duration', cal, required=True)
            remaining, _ = days(row, 'remaining', cal)
            milestone = row.get('milestone', boolean, False) or typ in {'startmilestone', 'finishmilestone', 'ttmile', 'ttfinmile'}
            if milestone and dur not in (None, 0): row.error('duration', 'Un hito debe tener duración cero.')
            dur, exact = (0, 0.0) if milestone else (dur or 0, exact or 0.0)
            wc = row.get('wbs', default=stack[-1][1] if stack else None)
            wref = row.get('wbs_id')
            if wref:
                wc = wbsrefs.get(wref)
                if wc is None: row.error('wbs_id', f'WBS inexistente: {wref}.', 'referencia_inexistente')
            elif wc and wc not in wbs_codes:
                # Dot-delimited codes produce explicit parent nodes.
                parent = None
                parts = wc.split('.')
                if len(parts) > 64 or any(not part for part in parts):
                    row.error('wbs', 'WBS: máximo 64 niveles, sin componentes vacíos.')
                    parts = []
                for part in parts:
                    full = part if parent is None else parent + '.' + part
                    if full not in wbs_codes:
                        wbs.append(dict(code=full, name=full, parent_code=parent, sort_order=len(wbs))); wbs_codes.add(full)
                    parent = full
            a = dict(code=code, name=name, wbs_code=wc, calendar_name=calname, duration_days=dur,
                     duration_hours=exact*cal['hours_per_day'], remaining_duration=remaining,
                     cost_budget=row.get('cost', number, s['costo_ausente']), crew_size=s['dotacion'],
                     required_skill=s['habilidad'], window_hard=s['fechas_como_ventana'])
            for field, target in [('start','planned_start'), ('finish','planned_finish'), ('actual_start','actual_start'),
                                  ('actual_finish','actual_finish'), ('constraint_date','constraint_date')]:
                a[target] = row.get(field, lambda v: fecha(v, row.epoch))
            for lo, hi in [('planned_start', 'planned_finish'), ('actual_start', 'actual_finish')]:
                if a[lo] and a[hi] and a[lo] > a[hi]: row.error('finish' if lo == 'planned_start' else 'actual_finish', 'Fin anterior al inicio.')
            c = row.get('constraint', default='ASAP')
            a['constraint_type'] = CONSTRAINTS.get(normal(c), 'ASAP')
            if normal(c) not in CONSTRAINTS: row.error('constraint', f'Restricción no admitida: {c}.')
            if a['constraint_type'] not in {'ASAP','ALAP'} and not a['constraint_date']: row.error('constraint_date', 'La restricción requiere fecha.')
            if s['fechas_como_ventana']: a.update(window_start=a['planned_start'], window_end=a['planned_finish'])
            pct = row.get('percent', lambda _: percent(row.cell('percent')))
            if pct is None:
                pct = 100.0 if a['actual_finish'] else 0.0
                if a['actual_finish']: row.error('percent', 'Fin real sin porcentaje: se propone 100 %.', 'avance_inferido', 'advertencia')
            if a['actual_finish'] and pct != 100: row.error('percent', 'Fin real requiere avance de 100 %.')
            if a['actual_finish'] and not a['actual_start']: row.error('actual_start', 'Fin real requiere inicio real.')
            a.update(percent_complete=pct, status='terminada' if pct == 100 else 'en_curso' if pct > 0 or a['actual_start'] else 'no_iniciada',
                     actual_hours=row.get('actual_hours', lambda v: work(v, cal['hours_per_day'], cal['workweek']), 0.0))
            source_work = row.get('work', lambda v: work(v, cal['hours_per_day'], cal['workweek']))
            a['work_hours'] = source_work if source_work is not None else exact*cal['hours_per_day']*s['dotacion']
            if dur == 0 and a['work_hours'] > 0: row.error('work', 'Un hito no puede consumir HH.')
            if remaining is not None and pct == 100 and remaining != 0: row.error('remaining', 'Actividad terminada con duración restante no nula.')
            a['notes'] = (row.get('notes', default='') + '\nFuente SHA-256: ' + source['sha256'] + '; origen: ' + json.dumps(row.origin(), ensure_ascii=False))
            activities.append(a); act_by_code[code] = a; row_by_code[code] = row
            tid = row.get('internal_id', default=code)
            if tid in task_ids: row.error('internal_id', f'ID interno duplicado: {tid}.')
            task_ids[tid] = code
            metadata[code] = dict(exact=exact, source_work=source_work)
            pending.append((row, a, cal))

        for row in aux.get('RSRC', []):
            rid, name = row.get('id', required=True), row.get('name', required=True)
            if not rid or not name: continue
            if rid in resource_ids or name in resource_by_name: row.error('id', 'ID o nombre de recurso duplicado.'); continue
            kind = normal(row.get('type', default='RT_Labor'))
            kinds = {'rtlabor':'trabajador', 'rtnonlabor':'equipo', 'rtmat':'material', 'trabajador':'trabajador', 'equipo':'equipo', 'material':'material'}
            if kind not in kinds: row.error('type', 'Tipo de recurso no admitido.')
            calname = calendar_for(row)
            r = dict(name=name, kind=kinds.get(kind, 'trabajador'), calendar_name=calname,
                     skills=[x.strip() for x in row.get('skills', default='').split(',') if x.strip()],
                     capacity_hours_day=row.get('capacity', valid_hours, cal_by_name[calname]['hours_per_day']),
                     max_units=row.get('units', number, 1.0), cost_hour=row.get('cost', number, 0.0))
            if r['max_units'] <= 0: row.error('units', 'Capacidad en unidades debe ser positiva.')
            resources.append(r); resource_by_name[name] = r; resource_ids[rid] = name

        def add_resource(name, cal):
            if name not in resource_by_name:
                r = dict(name=name, kind='trabajador', calendar_name=cal['name'], skills=[s['habilidad']] if s['habilidad'] else [],
                         capacity_hours_day=cal['hours_per_day'], max_units=1.0, cost_hour=0.0)
                resource_by_name[name] = r; resources.append(r)

        assignment_keys = set()
        for row, a, cal in pending:
            names = row.get('resources')
            if not names: continue
            try: named = list(resource_names(names))
            except ValueError as exc: row.error('resources', str(exc)); continue
            total_units = sum(u for _, u in named)
            a['crew_size'] = total_units or s['dotacion']
            if metadata[a['code']]['source_work'] is None: a['work_hours'] = metadata[a['code']]['exact'] * cal['hours_per_day'] * total_units
            for name, units in named:
                key = (a['code'], name)
                if key in assignment_keys: row.error('resources', f'Asignación duplicada: {name}.'); continue
                if units <= 0: row.error('resources', 'Las unidades deben ser positivas.'); continue
                add_resource(name, cal); assignment_keys.add(key)
                assignments.append(dict(activity_code=a['code'], resource_name=name, units=units,
                                        work_hours=a['work_hours']*units/total_units))
        assigned_hours, assigned_units = {}, {}
        for row in aux.get('TASKRSRC', []):
            task, resource = row.get('task', required=True), row.get('resource', required=True)
            code, name = task_ids.get(task), resource_ids.get(resource)
            if code is None: row.error('task', f'Actividad inexistente: {task}.', 'referencia_inexistente')
            if name is None: row.error('resource', f'Recurso inexistente: {resource}.', 'referencia_inexistente')
            if code is None or name is None: continue
            key = (code, name)
            if key in assignment_keys: row.error('resource', 'Asignación repetida entre tablas.'); continue
            assignment_keys.add(key)
            a = act_by_code[code]; cal = cal_by_name[a['calendar_name']]
            units = row.get('units', number, 1.0)
            if units <= 0: row.error('units', 'Las unidades deben ser positivas.')
            hh = row.get('work', number, metadata[code]['exact']*cal['hours_per_day']*units)
            if not a['duration_days'] and hh: row.error('work', 'Un hito no puede consumir HH.')
            assignments.append(dict(activity_code=code, resource_name=name, units=units, work_hours=hh,
                                    actual_hours=row.get('actual', number, 0.0)))
            assigned_hours[code] = assigned_hours.get(code, 0) + hh
            assigned_units[code] = assigned_units.get(code, 0) + units
        for code, hh in assigned_hours.items():
            a = act_by_code[code]
            if metadata[code]['source_work'] is None: a['work_hours'] = hh
            elif not math.isclose(a['work_hours'], hh, abs_tol=1e-6): row_by_code[code].error('work', 'HH de actividad y asignaciones difieren; se conservan ambas.', 'hh_distintas', 'advertencia')
            a['crew_size'] = assigned_units[code]

        edges, rel_keys = [], set()
        def add_relation(pred, succ, typ, lag, row, field):
            key = (pred, succ)
            if key in rel_keys: row.error(field, 'Relación duplicada; se admite una por pareja.'); return
            rel_keys.add(key)
            try:
                n = integral(lag, s['redondeo_dias'], lag=True)
                if abs(n) > 36600: raise ValueError('Desfase superior a 36.600 días.')
                if not math.isclose(n, lag): row.error(field, f'Desfase {lag:g} → {n} días por SUPUESTO.', 'redondeo', 'advertencia')
            except ValueError as exc: row.error(field, str(exc)); return
            relations.append(dict(pred_code=pred, succ_code=succ, type=typ, lag_days=n))
            edges.append((pred, succ, row))
        for row, a, cal in pending:
            text = row.get('predecessors')
            if text:
                try:
                    for pred, typ, lag in predecessors(text, act_by_code, cal['hours_per_day'], cal['workweek']):
                        add_relation(pred, a['code'], typ, lag, row, 'predecessors')
                except ValueError as exc: row.error('predecessors', str(exc))
        for row in aux.get('TASKPRED', []):
            raw_pred, raw_succ = row.get('pred', required=True), row.get('succ', required=True)
            pred, succ = task_ids.get(raw_pred), task_ids.get(raw_succ)
            if pred is None: row.error('pred', f'Predecesora inexistente: {raw_pred}.', 'referencia_inexistente')
            if succ is None: row.error('succ', f'Sucesora inexistente: {raw_succ}.', 'referencia_inexistente')
            if pred is None or succ is None: continue
            typ = row.get('type', default='PR_FS').upper().removeprefix('PR_')
            if typ not in {'FS','SS','FF','SF'}: row.error('type', 'Relación debe ser PR_FS/PR_SS/PR_FF/PR_SF.'); continue
            cal = cal_by_name[act_by_code[succ]['calendar_name']]
            try: lag = duration(row.get('lag', default='0'), cal['hours_per_day'], cal['workweek'], raw_hours=True, signed=True)
            except ValueError as exc: row.error('lag', str(exc)); continue
            add_relation(pred, succ, typ, lag, row, 'pred')
        # Use each table's physical predecessor column for errors.
        for key in ('predecessors', 'pred'):
            relevant = [(a,b,r) for a,b,r in edges if key in r.mapping]
            # SCC uses the entire graph, including edges from both table styles.
            if relevant:
                from ._p6_msp_modelo import components
                groups, sizes = components(act_by_code, [(a,b) for a,b,_ in edges])
                for pred, succ, row in relevant:
                    if pred not in act_by_code or succ not in act_by_code: row.error(key, f'Referencia inexistente: {pred} → {succ}.', 'referencia_inexistente')
                    elif groups[pred] == groups[succ] and (pred == succ or sizes[groups[pred]] > 1): row.error(key, f'Ciclo: {pred} → {succ}.', 'ciclo')

        starts = [a['planned_start'] for a in activities if a['planned_start']]
        finishes = [a['planned_finish'] for a in activities if a['planned_finish']]
        actuals = [a[k] for a in activities for k in ('actual_start', 'actual_finish') if a[k]]
        s['fecha_inicio'] = s['fecha_inicio'] or min(starts + actuals, default='2027-01-04')
        s['fecha_fin'] = s['fecha_fin'] or max(finishes + [s['fecha_inicio']])
        if not finishes and not supuestos.get('fecha_fin') and not perfil.supuestos.get('fecha_fin'):
            s['fecha_fin'] = (date.fromisoformat(s['fecha_inicio']) + timedelta(days=730)).isoformat()
        s['fecha_corte'] = s['fecha_corte'] or max(actuals, default=s['fecha_inicio'])
        if not 0 <= (date.fromisoformat(s['fecha_fin']) - date.fromisoformat(s['fecha_inicio'])).days <= 3660:
            raise ValueError('El horizonte debe ser creciente y de hasta diez años.')
        if s['fecha_corte'] < s['fecha_inicio'] or s['fecha_corte'] > s['fecha_fin']:
            raise ValueError('La fecha de corte debe estar dentro del horizonte.')
        note = '\nSUPUESTOS: ' + json.dumps(s, ensure_ascii=False, sort_keys=True)
        for a in activities:
            a['notes'] += note
            if a['percent_complete'] or a['actual_start'] or a['actual_finish'] or a['actual_hours']:
                for key in ('actual_start', 'actual_finish'):
                    if a[key] and a[key] > s['fecha_corte']: row_by_code[a['code']].error(key, 'Fecha real posterior al corte.')
                progress.append(dict(activity_code=a['code'], date=s['fecha_corte'], percent_complete=a['percent_complete'],
                                     actual_start=a['actual_start'], actual_finish=a['actual_finish'], actual_hours=a['actual_hours'],
                                     note='Saldo inicial importado; no representa períodos anteriores. ' + a['notes']))
        if not activities: issue(errors, sheet, perfil.fila_encabezado + 1, 'A', 'No hay actividades programables.', 'sin_actividades')
        counts = dict(items=len(rows), actividades=len(activities), relaciones=len(relations), recursos=len(resources),
                      asignaciones=len(assignments), avances=len(progress), wbs=len(wbs), calendarios=len(cals),
                      hitos=sum(a['duration_days'] == 0 for a in activities), hh_total=round(sum(a['work_hours'] for a in activities), 6))
        model = dict(activities=activities, relations=relations, resources=resources, assignments=assignments,
                     progress=progress, calendars=cals, default_calendar=default_cal,
                     origen_auxiliar={key: [row.origin() for row in values] for key,values in aux.items() if values})
        return Borrador(perfil=perfil, fuente=source, maestras=[], sitios=[], wbs=wbs, advertencias=errors,
                        faltantes=[dict(clave=k, etiqueta=label, motivo='Decisión de importación editable; no es un dato observado.', valor=s[k], estado='SUPUESTO') for k,label in LABELS.items()],
                        supuestos=s, resumen=counts, excluidos=excluded, modo='proyecto', modelo=model,
                        vista_previa=[dict(fila=r.index, hoja=r.sheet, celdas={get_column_letter(i): str(c.value or '') for i,c in enumerate(r.cells,1)}) for r in rows[:100]])

    def convertir(self, borrador, nombre=None):
        from ganttier.domain import ImportBundle
        if borrador.tiene_errores: raise ValueError('El borrador contiene errores por fila; no se puede convertir.')
        m, s = borrador.modelo, borrador.supuestos
        return ImportBundle(mode='proyecto', project=dict(name=nombre or Path(borrador.fuente['nombre']).stem,
            start_date=s['fecha_inicio'], end_date=s['fecha_fin'], data_date=s['fecha_corte'], currency=s['moneda'],
            calendar_name=m['default_calendar'], description='Importación MSP/P6. Fuente SHA-256: '+borrador.fuente['sha256']+'\nSUPUESTOS: '+json.dumps(s, ensure_ascii=False, sort_keys=True)),
            wbs=deepcopy(borrador.wbs), **{k: deepcopy(m[k]) for k in ('calendars','resources','activities','relations','assignments','progress')})


class AdaptadorMSP(_Adaptador):
    formato = 'msp_excel'
    nombre = 'MS Project Excel / CSV'


class AdaptadorP6(_Adaptador):
    formato = 'p6_excel'
    nombre = 'Primavera P6 Excel / CSV'
