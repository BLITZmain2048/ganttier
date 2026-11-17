"""Caso JSON de proyecto: bloques abstractos, roles y expansión verificable."""
from __future__ import annotations
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
import re
import string
from .modelos import Perfil, Borrador
from .excel import aviso


def leer_json(path):
    if path.stat().st_size > 30*1024*1024:
        raise ValueError('El JSON supera 30 MB.')
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('El archivo no contiene JSON válido.') from exc


def entero(value, label, low=0, high=100000):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label}: se requiere un entero entre {low} y {high}.')
    return value


def numero(value, label, low=0, high=1e12):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}: número fuera del intervalo {low}–{high}.')
    return value


def codigo_ocurrencia(series, index):
    fmt = series.get('occurrence_id_format','{series_id}_{occurrence:02d}')
    if not isinstance(fmt,str) or len(fmt)>160:
        raise ValueError('occurrence_id_format inválido.')
    for _, field, spec, conversion in string.Formatter().parse(fmt):
        if field is not None and (field not in {'series_id','occurrence'} or conversion or len(spec)>8):
            raise ValueError('occurrence_id_format solo admite series_id y occurrence, sin atributos.')
        if field == 'series_id' and spec or field == 'occurrence' and spec and not re.fullmatch(r'0?[1-9]?\d?d|d',spec):
            raise ValueError('Formato de ocurrencia: use entero con ancho máximo de dos dígitos (por ejemplo 02d).')
    try: result=fmt.format(series_id=series['id'],occurrence=index)
    except (ValueError,KeyError,TypeError) as exc: raise ValueError('occurrence_id_format inválido.') from exc
    if not result or len(result)>200: raise ValueError('Código de ocurrencia vacío o demasiado largo.')
    return result


def expandir(data):
    """No usa soluciones previas; cada predecesora de serie usa SU formato de ID."""
    h=entero(data['time_model']['horizon_slots'],'horizon_slots',1,3660)
    sh=numero(data['time_model']['slot_hours'],'slot_hours',0.01,24)
    activities=deepcopy(data['activities'])
    series=data.get('periodic_series',[])
    if not isinstance(activities,list) or not isinstance(series,list): raise ValueError('activities y periodic_series deben ser listas.')
    smap={s['id']:s for s in series}
    if len(smap)!=len(series): raise ValueError('Identificadores de series duplicados.')
    counts=dict(one_off_work_activities=sum(not a.get('is_milestone',False) for a in activities),
                milestones=sum(bool(a.get('is_milestone',False)) for a in activities),periodic_occurrences=0)
    for s in series:
        n=entero(s['occurrence_count'],'occurrence_count',1)
        first=entero(s.get('occurrence_index_starts_at',1),'occurrence_index_starts_at')
        period=entero(s['period_slots'],'period_slots',1,3660)
        window=entero(s['window_length_slots'],'window_length_slots',1,3660)
        lo=entero(s['first_window_start_slot'],'first_window_start_slot',0,h)
        if len(activities)+n>100000: raise ValueError('La expansión supera 100.000 actividades.')
        for offset in range(n):
            k=first+offset
            preds=[]
            for p in s.get('predecessors',[]):
                if p['series_id'] not in smap: raise ValueError('Serie predecesora desconocida: '+p['series_id'])
                index=k+entero(p.get('occurrence_offset',0),'occurrence_offset',-100000)
                preds.append(dict(activity_id=codigo_ocurrencia(smap[p['series_id']],index),type=p.get('type','FS'),lag_slots=p.get('lag_slots',0)))
            activities.append(dict(s,id=codigo_ocurrencia(s,k),name=f"{s['name']} #{k}",
                earliest_start_slot=lo+period*offset,latest_finish_slot=lo+period*offset+window,
                predecessors=preds,is_milestone=False,origen=f"periodic_series/{s['id']}/{k}"))
        counts['periodic_occurrences']+=n
    counts['total_activities_including_milestones']=len(activities)
    ids=[a['id'] for a in activities]
    if any(not isinstance(v,str) or not v or len(v)>200 for v in ids) or len(set(ids))!=len(ids):
        raise ValueError('Identificadores de actividades vacíos, duplicados o inválidos.')
    people=data['personnel']; resources=data['resources']
    if len(people)+len(resources)>10000: raise ValueError('Exceso de recursos.')
    names=[p['name'] for p in people]+[r['id'] for r in resources]
    if len(set(names))!=len(names): raise ValueError('Nombres de personal o recursos duplicados.')
    if len({p['id'] for p in people})!=len(people): raise ValueError('Identificadores de personal duplicados.')
    roles=set(data.get('roles',{}))|{skill for p in people for skill in p['skills']}
    rids={r['id'] for r in resources}
    for p in people:
        numero(p['cost_per_slot'],'cost_per_slot')
        if not isinstance(p['skills'],list) or any(not isinstance(s,str) or not s for s in p['skills']): raise ValueError('skills debe contener habilidades con nombre.')
    for r in resources:
        entero(r['capacity'],'capacity',1)
        if r.get('renewable',True) is not True: raise ValueError('Solo se admiten equipos renovables.')
    for p in people+resources:
        intervals=[p.get('availability_window',[0,h])]+p.get('unavailable_intervals',[])
        for interval in intervals:
            if not isinstance(interval,list) or len(interval)!=2: raise ValueError('Intervalo inválido; use [inicio,fin).')
            lo,hi=interval
            entero(lo,'inicio de disponibilidad',0,h); entero(hi,'fin de disponibilidad',0,h)
            if hi<lo: raise ValueError('Intervalo de disponibilidad invertido.')
    relations=[]
    for a in activities:
        dur=entero(a['duration_slots'],'duration_slots',0,h)
        lo=entero(a['earliest_start_slot'],'earliest_start_slot',0,h)
        hi=entero(a['latest_finish_slot'],'latest_finish_slot',0,h)
        if hi<lo+dur: raise ValueError('Ventana insuficiente: '+a['id'])
        if bool(a.get('is_milestone',False)) != (dur==0): raise ValueError('Un hito requiere duración cero: '+a['id'])
        for key,known in [('personnel_requirements',roles),('resource_requirements',rids)]:
            req=a.get(key,{})
            if not isinstance(req,dict): raise ValueError(f'{key} debe ser un objeto por rol/recurso.')
            for name,units in req.items():
                if name not in known: raise ValueError(f'Requerimiento desconocido: {name}.')
                entero(units,f'Unidades de {name}',1)
            if not dur and req: raise ValueError('Un hito no puede consumir recursos.')
        for p in a.get('predecessors',[]):
            if p.get('type','FS')!='FS': raise ValueError('Este perfil admite predecesoras FS.')
            if p['activity_id'] not in ids: raise ValueError('Predecesora desconocida: '+p['activity_id'])
            relations.append(dict(pred_code=p['activity_id'],succ_code=a['id'],type='FS',lag_days=entero(p.get('lag_slots',0),'lag_slots',-3660,3660)))
    # Kahn: los ciclos no pueden llegar al planificador.
    from collections import defaultdict, deque
    indegree=dict.fromkeys(ids,0); edges=defaultdict(list)
    for r in relations: edges[r['pred_code']].append(r['succ_code']); indegree[r['succ_code']]+=1
    queue=deque(k for k,v in indegree.items() if not v); visited=0
    while queue:
        node=queue.popleft(); visited+=1
        for succ in edges[node]:
            indegree[succ]-=1
            if not indegree[succ]: queue.append(succ)
    if visited!=len(ids): raise ValueError('La red de predecesoras contiene ciclos.')
    return activities,relations,counts,h,sh


class CasoJSON:
    formato='caso_json'
    extensiones={'.json'}

    def detectar(self,path):
        data=leer_json(path)
        if not isinstance(data,dict) or not {'time_model','activities','personnel','resources'}<=data.keys(): return []
        profile=Perfil(id='caso-json-proyecto',nombre='Caso JSON de proyecto',formato=self.formato,mapeo={'estructura':'time_model/activities/periodic_series/personnel/resources'})
        return [dict(formato=self.formato,puntaje=1,motivos=['Estructura de proyecto con bloques y requerimientos por rol'],perfil=profile.model_dump())]

    def previsualizar(self,path,perfil,supuestos):
        data=leer_json(path)
        try: acts,rels,counts,h,sh=expandir(data)
        except (KeyError,TypeError,AttributeError,IndexError,OverflowError) as exc:
            raise ValueError('Caso JSON incompleto o con tipos inválidos: '+str(exc)) from exc
        s=dict(fecha_base='2027-01-01',equivalencia_bloque_dia='synthetic_day_per_block',
            role_mode='indivisible' if data['personnel'] and any(a.get('personnel_requirements') for a in acts) else 'fractional',moneda='CLP')
        for source in (perfil.supuestos,supuestos):
            if set(source)-s.keys(): raise ValueError('Supuestos JSON desconocidos: '+str(sorted(set(source)-s.keys())))
            s.update(source)
        try: date.fromisoformat(s['fecha_base'])+timedelta(days=h)
        except (ValueError,TypeError,OverflowError) as exc: raise ValueError('Fecha base inválida.') from exc
        if s['equivalencia_bloque_dia']!='synthetic_day_per_block': raise ValueError('La equivalencia admitida es synthetic_day_per_block: un bloque por día abstracto.')
        if s['role_mode'] not in {'indivisible','fractional'}: raise ValueError('role_mode debe ser indivisible o fractional.')
        warnings=[aviso('calendario_abstracto',f'SUPUESTO: cada bloque de {sh:g} h equivale a un día abstracto continuo 7x7 desde {s["fecha_base"]}; no son fechas civiles intradía.')]
        expected=data.get('expected_expanded_counts')
        if expected is None: warnings.append(aviso('sin_conteos','La fuente no informa expected_expanded_counts; conteos sin control externo.'))
        elif not isinstance(expected,dict) or set(expected)!=set(counts) or any(type(expected[k]) is not int or expected[k]!=v for k,v in counts.items()):
            warnings.append(aviso('conteos_expandidos','expected_expanded_counts no coincide con la expansión: '+json.dumps(counts),nivel='error'))
        missing=[dict(clave=k,etiqueta=label,valor=s[k],estado='SUPUESTO',motivo=reason) for k,label,reason in [
            ('fecha_base','Fecha base SUPUESTA','No hay fecha civil en la fuente; editar antes de confirmar.'),
            ('equivalencia_bloque_dia','Equivalencia bloque ↔ día','synthetic_day_per_block: 1 bloque = 1 día, sin feriados ni descansos; no representa dos bloques por día civil.'),
            ('role_mode','Modo propuesto para resolver roles','indivisible mantiene personas distintas por rol; se entrega como recomendación, no ejecuta el solver.'),
            ('moneda','Moneda SUPUESTA','La fuente no declara moneda para cost_per_slot.')]]
        return Borrador(perfil=perfil,fuente=dict(nombre=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
            maestras=[],sitios=[],wbs=[dict(code='P',name='Proyecto',parent_code=None)],advertencias=warnings,faltantes=missing,supuestos=s,
            resumen=dict(items=len(acts),actividades=len(acts),relaciones=len(rels),personas=len(data['personnel']),equipos=len(data['resources']),conteos_expandidos=counts,
                solicitud_solver_sugerida=dict(options=dict(role_mode=s['role_mode'],block_hours=sh,horizon_blocks=h))),modo='proyecto',
            modelo=dict(actividades=acts,relaciones=rels,personal=data['personnel'],recursos=data['resources'],horizon_slots=h,slot_hours=sh,nombre=data.get('name',path.stem)),
            vista_previa=[dict(fila=a['id'],celdas={'Nombre':a['name'],'Duración (bloques)':str(a['duration_slots']),'Roles':json.dumps(a.get('personnel_requirements',{})),'Ventana':f"[{a['earliest_start_slot']}, {a['latest_finish_slot']})"}) for a in acts[:300]])

    def convertir(self,draft,nombre=None):
        from ganttier.domain import ImportBundle
        m=draft.modelo; s=draft.supuestos; base=date.fromisoformat(s['fecha_base']); h=m['horizon_slots']; sh=m['slot_hours']
        dt=lambda t:(base+timedelta(days=t)).isoformat()
        signature=hashlib.sha256(json.dumps([m,s],sort_keys=True).encode()).hexdigest()[:12]
        cal='Bloques continuos '+signature
        calendar=dict(name=cal,workweek='1111111',hours_per_day=sh,holidays_country=None)
        note='SUPUESTOS: '+json.dumps(s,ensure_ascii=False)+'; Fuente SHA-256: '+draft.fuente['sha256']
        bundle=ImportBundle(mode='proyecto',project=dict(name=nombre or m['nombre'],start_date=dt(0),end_date=dt(h-1),calendar_name=cal,currency=s['moneda'],description=note),calendars=[calendar],wbs=draft.wbs,relations=m['relaciones'])
        for p,kind in [(p,'trabajador') for p in m['personal']]+[(r,'equipo') for r in m['recursos']]:
            name=p['name'] if kind=='trabajador' else p['id']
            lo,hi=p.get('availability_window',[0,h])
            intervals=p.get('unavailable_intervals',[])+[[0,lo],[hi,h]]
            days=sorted({day for start,end in intervals for day in range(start,end)})
            rcal=cal
            if days:
                rcal=cal+' · '+name
                bundle.calendars.append(dict(calendar,name=rcal,exceptions=[dict(date=dt(day),working=False,hours=0,reason='Indisponibilidad de la fuente JSON') for day in days]))
            cost=p['cost_per_slot']/sh if kind=='trabajador' else 0
            bundle.resources.append(dict(name=name,kind=kind,skills=p.get('skills',[]),capacity_hours_day=sh,max_units=1 if kind=='trabajador' else p['capacity'],calendar_name=rcal,cost_hour=cost,overtime_cost_hour=cost,max_overtime_hours_day=0))
        for a in m['actividades']:
            crew=sum(a.get('personnel_requirements',{}).values()); dur=a['duration_slots']
            bundle.activities.append(dict(code=a['id'],name=a['name'],wbs_code='P',duration_days=dur,work_hours=dur*sh*crew,crew_size=max(1,crew),
                calendar_name=cal,window_start=dt(a['earliest_start_slot']) if a['earliest_start_slot'] else None,
                window_end=dt(a['latest_finish_slot']-(1 if dur else 0)) if a['latest_finish_slot']<h or a.get('origen') else None,
                window_hard=True,priority=50,notes=note+'; origen: '+a.get('origen','activities/'+a['id'])))
            for key,field in [('personnel_requirements','skill'),('resource_requirements','resource_name')]:
                for name,units in a.get(key,{}).items():
                    bundle.assignments.append(dict(activity_code=a['id'],**{field:name},units=units,work_hours=dur*sh*units))
        return bundle
