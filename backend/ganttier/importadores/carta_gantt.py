"""Carta genérica: marcas literales o relleno directo, con interpretación explícita."""
from __future__ import annotations
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile
import hashlib
import json
import re
from types import SimpleNamespace
import openpyxl
from openpyxl.utils import get_column_letter, column_index_from_string
from .excel import normal, aviso
from .modelos import Perfil, Borrador
from .om_wbs import MESES, FRECUENCIAS
from ganttier.excel_io.importer import _convert

ALIASES = {'nombre': ('actividad', 'tarea', 'descripcion', 'nombre'), 'codigo': ('codigo', 'wbs', 'id'),
           'hh': ('hh', 'horashombre'), 'duracion': ('duracion', 'duraciondias'),
           'dotacion': ('dotacion', 'cuadrilla'), 'habilidad': ('habilidad', 'especialidad'),
           'sitio': ('sitio', 'instalacion', 'planta'), 'responsable': ('responsable',),
           'categoria': ('categoria',)}
DEFAULTS = dict(interpretacion='ventana_macro', discontinuas='dividir', anio=2027,
                hh=8, duracion=1, dotacion=1, habilidad='', sitio='Por confirmar', moneda='CLP',
                semana='1111100', horas_dia=8, pais='CL', window_hard=False,
                marcas='texto_color', colores=[], tolerancia_color=0)


def abrir(path, valores=True):
    path = Path(path)
    if path.suffix.lower() != '.xlsx' or path.stat().st_size > 30*1024*1024:
        raise ValueError('Seleccione un XLSX de hasta 30 MB.')
    with ZipFile(path) as z:
        if len(z.infolist()) > 10000 or sum(i.file_size for i in z.infolist()) > 150*1024*1024:
            raise ValueError('Libro descomprimido demasiado grande.')
    book = openpyxl.load_workbook(path, read_only=True, data_only=valores, keep_links=False)
    if len(book.worksheets) > 100 or any((w.max_row or 0) > 200000 or (w.max_column or 0) > 200 for w in book):
        book.close()
        raise ValueError('Límite: 100 hojas, 200.000 filas y 200 columnas.')
    return book


def periodo(value, year):
    if isinstance(value, datetime): value = value.date()
    if isinstance(value, date): return value, value+timedelta(days=6)
    text = normal(value)
    match = re.fullmatch(r'(s|semana|mes)(\d{1,2})', text)
    if match:
        n = int(match[2])
        if match[1] == 'mes':
            if not 1 <= n <= 12: return None
            return date(year,n,1), date(year,n,monthrange(year,n)[1])
        if not 1 <= n <= 53: return None
        try: start = date.fromisocalendar(year,n,1)
        except ValueError: return None
        return start, start+timedelta(days=6)
    match = re.fullmatch(r'([a-z]+)(\d{2}|\d{4})?', text)
    if match and match[1] in MESES:
        y = int(match[2]) if match[2] else year
        if y < 100: y += 2000
        m = MESES[match[1]]
        return date(y,m,1), date(y,m,monthrange(y,m)[1])
    if isinstance(value,str):
        for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%d.%m.%Y'):
            try:
                d = datetime.strptime(value,fmt).date()
                return d,d+timedelta(days=6)
            except ValueError: pass
    return None


def marcada(cell, s):
    value = cell.value
    if s['marcas'] != 'color' and value is not None and str(value).strip() and value != 0:
        return True
    fill = getattr(cell, 'fill', None)
    if s['marcas'] == 'texto' or not fill or fill.patternType != 'solid': return False
    color = fill.fgColor
    if not s['colores']:
        return color.type != 'rgb' or color.rgb[-6:].upper() not in {'FFFFFF','000000'}
    if color.type != 'rgb': return False
    rgb = tuple(int(color.rgb[-6:][i:i+2],16) for i in (0,2,4))
    return any(max(abs(a-int(c[i:i+2],16)) for a,i in zip(rgb,(0,2,4))) <= s['tolerancia_color'] for c in s['colores'])


class AdaptadorCartaGantt:
    formato = 'carta_gantt'
    extensiones = {'.xlsx'}

    def detectar(self, path):
        if Path(path).suffix.lower() != '.xlsx': return []
        book = abrir(path)
        results = []
        try:
            for ws in book:
                year = 2027
                mapping = {}
                om_header = False
                for row in ws.iter_rows(max_row=min(ws.max_row or 50,50)):
                    vals = [c.value for c in row]
                    year = next((int(v) for v in vals if isinstance(v,(int,float)) and 1900<=v<=2100),year)
                    names = [normal(v) for v in vals]
                    # E2-MP puede contener códigos S1/S2 dentro del cuerpo.
                    # Su bloque económico tiene prioridad estructural.
                    if ('equipo' in names and 'actividad' in names
                            and any('preciounitario' in n for n in names)
                            and any('preciototal' in n for n in names)
                            and any('cantidad' in n for n in names)):
                        break
                    # OM-WBS tiene columnas separadas de frecuencia; cederle propiedad.
                    if sum(n in FRECUENCIAS for n in names) >= 3: om_header = True
                    if om_header: continue
                    mapping.update({k:get_column_letter(names.index(n)+1) for k,aliases in ALIASES.items() for n in aliases if n in names})
                    found = [(i,periodo(v,year)) for i,v in enumerate(vals,1) if periodo(v,year)]
                    # Integración R3: una grilla de períodos avanza de izquierda a derecha con
                    # saltos de al menos una semana. Fechas sueltas de filas de datos (p. ej.
                    # Fecha / Inicio_Real de un libro de avance) no forman una carta.
                    if any((b[1][0]-a[1][0]).days < 0 or 0 < (b[1][0]-a[1][0]).days < 6 for a,b in zip(found,found[1:])): continue
                    periods = [get_column_letter(i) for i,_ in found]
                    if 'nombre' not in mapping or len(periods)<2: continue
                    profile = Perfil(id='carta-gantt',nombre='Carta Gantt',formato=self.formato,hoja=ws.title,
                                     fila_encabezado=row[0].row,mapeo={**mapping,'periodos':periods},supuestos={'anio':year})
                    results.append(dict(formato=self.formato,puntaje=.9,motivos=['Actividad y al menos dos períodos; sin estructura OM-WBS'],perfil=profile.model_dump()))
                    break
        finally: book.close()
        return results

    def previsualizar(self,path,perfil,supuestos):
        s = dict(DEFAULTS)
        for source in (perfil.supuestos,supuestos):
            if set(source)-s.keys(): raise ValueError('Supuestos desconocidos: '+str(set(source)-s.keys()))
            s.update(source)
        if s['interpretacion'] not in {'ventana_macro','periodica'} or s['discontinuas'] not in {'dividir','envolvente'}:
            raise ValueError('Interpretación o regla de discontinuidades inválida.')
        if s['marcas'] not in {'texto','color','texto_color'}: raise ValueError('Marcas: texto, color o texto_color.')
        if type(s['window_hard']) is not bool: raise ValueError('window_hard debe ser booleano.')
        if not re.fullmatch('[01]{7}',s['semana']) or '1' not in s['semana']: raise ValueError('Semana inválida.')
        for k in ('duracion','dotacion','anio'):
            s[k] = _convert(s[k],SimpleNamespace(kind='positive_int'),None)
        for k in ('hh','horas_dia','tolerancia_color'):
            s[k] = _convert(s[k],SimpleNamespace(kind='number'),None)
        if not 0<s['horas_dia']<=24 or s['tolerancia_color']>255: raise ValueError('Jornada o tolerancia inválida.')
        if not isinstance(s['colores'],list) or any(not re.fullmatch('[0-9a-fA-F]{6}',c) for c in s['colores']): raise ValueError('Colores: lista RGB de seis dígitos.')
        book, formulas = abrir(path), abrir(path,False)
        warnings, excluded, tasks, wbs, preview = [],[],[],[],[]
        seen, stack, groups = set(),[],set()
        try:
            ws = book[perfil.hoja]; fw = formulas[perfil.hoja]; m = perfil.mapeo
            cols = {k:column_index_from_string(v)-1 for k,v in m.items() if k in ALIASES}
            if 'nombre' not in cols: raise ValueError('Falta columna nombre.')
            header = next(ws.iter_rows(min_row=perfil.fila_encabezado,max_row=perfil.fila_encabezado))
            years={}
            for upper in ws.iter_rows(max_row=perfil.fila_encabezado-1):
                current=None
                for idx,cell in enumerate(upper):
                    if isinstance(cell.value,(int,float)) and 1900<=cell.value<=2100: current=int(cell.value)
                    if current: years[idx]=current
            periods=[]
            for col in m['periodos']:
                i=column_index_from_string(col)-1
                p=periodo(header[i].value,years.get(i,s['anio']))
                if not p: raise ValueError(f'{ws.title}/{col}{perfil.fila_encabezado}: período inválido.')
                periods.append((i,*p))
            if len(periods)<2 or any(b[1]<=a[2] for a,b in zip(periods,periods[1:])): raise ValueError('Períodos repetidos, superpuestos o desordenados.')
            first,last=periods[0][1],periods[-1][2]
            if (last-first).days>3660: raise ValueError('Horizonte máximo de diez años.')
            rows=ws.iter_rows(min_row=perfil.fila_encabezado+1)
            frows=fw.iter_rows(min_row=perfil.fila_encabezado+1)
            for r,(row,frow) in enumerate(zip(rows,frows),perfil.fila_encabezado+1):
                if r>200000 or len(row)>200: raise ValueError('Dimensiones excedidas.')
                used=set(cols.values())|{p[0] for p in periods}
                invalid=False
                for i in used:
                    if frow[i].data_type=='f' and row[i].value is None or row[i].data_type=='e':
                        warnings.append(aviso('celda_invalida','Fórmula sin valor cacheado o error Excel.',r,nivel='error',hoja=ws.title,columna=get_column_letter(i+1))); invalid=True
                if invalid: continue
                def val(k, default=''):
                    v=row[cols[k]].value if k in cols else None
                    return default if v is None or v=='' else v
                name=str(val('nombre')).strip()
                origin={'hoja':ws.title,'fila':r,'celdas':{k:f'{get_column_letter(i+1)}{r}' for k,i in cols.items()},'marcas':[]}
                if not name:
                    if any(marcada(row[p[0]],s) for p in periods): warnings.append(aviso('sin_nombre','Marcas sin actividad.',r,nivel='error',hoja=ws.title,columna=get_column_letter(cols['nombre']+1)))
                    continue
                if normal(name) in ALIASES['nombre']:
                    excluded.append(dict(origen=origin,motivo='Encabezado repetido')); continue
                code=str(val('codigo',f'F{r}')).strip()
                if code in seen:
                    warnings.append(aviso('duplicado','Código duplicado: '+code,r,nivel='error',hoja=ws.title,columna=m.get('codigo',m['nombre']))); continue
                seen.add(code)
                raw_name=str(val('nombre'))
                indent=max(int(getattr(row[cols['nombre']].alignment,'indent',0) or 0),
                           (len(raw_name)-len(raw_name.lstrip()))//2)
                while stack and stack[-1][0]>=indent: stack.pop()
                parent=code.rpartition('.')[0] or (stack[-1][1] if stack else None)
                category=str(val('categoria')).strip()
                if category:
                    parent='CAT:'+category
                    if parent not in groups: wbs.append(dict(code=parent,name=category,parent_code=None)); groups.add(parent)
                wbs.append(dict(code=code,name=name,parent_code=parent)); stack.append((indent,code))
                selected=[p for p in periods if marcada(row[p[0]],s)]
                origin['marcas']=[f'{get_column_letter(p[0]+1)}{r}' for p in selected]
                if not selected:
                    excluded.append(dict(origen=origin,motivo='Sin marcas; se conserva como WBS')); continue
                values={}
                try:
                    for key,kind in [('hh','number'),('duracion','positive_int'),('dotacion','positive_int')]:
                        values[key]=_convert(val(key,s[key]),SimpleNamespace(kind=kind),book.epoch)
                except ValueError as exc:
                    warnings.append(aviso('numero_invalido',str(exc),r,nivel='error',hoja=ws.title,columna=m.get(key,m['nombre']))); continue
                runs=[]
                for p in selected:
                    if runs and p[1]==runs[-1][-1][2]+timedelta(days=1): runs[-1].append(p)
                    else: runs.append([p])
                if len(runs)>1: warnings.append(aviso('discontinua',f'{code}: {len(runs)} tramos; regla {s["discontinuas"]}.',r,hoja=ws.title))
                chunks=[[p] for p in selected] if s['interpretacion']=='periodica' else (runs if s['discontinuas']=='dividir' else [selected])
                for j,chunk in enumerate(chunks):
                    lo,hi=chunk[0][1],chunk[-1][2]
                    suffix='-'+get_column_letter(j+1) if len(chunks)>1 else ''
                    tasks.append(dict(code='CG-'+code+suffix,name=name,wbs_code=code,duration_days=values['duracion'],work_hours=values['hh'],crew_size=values['dotacion'],required_skill=str(val('habilidad',s['habilidad'])),location=str(val('sitio',s['sitio'])),window_hard=s['window_hard'],window_start=lo.isoformat(),window_end=hi.isoformat(),origen={**origin,'responsable':str(val('responsable'))}))
                if len(preview)<30: preview.append(dict(fila=r,celdas={'Actividad':name,'Marcas':str(len(selected))}))
        finally:
            book.close(); formulas.close()
        known={w['code'] for w in wbs}
        for w in wbs:
            if w['parent_code'] not in known: w['parent_code']=None
        if not tasks: warnings.append(aviso('sin_tareas','No hay actividades marcadas.',nivel='error'))
        if len(tasks)>100000: raise ValueError('Máximo 100.000 actividades resultantes.')
        return Borrador(perfil=perfil,fuente=dict(nombre=Path(path).name,sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest()),maestras=tasks,sitios=[{'codigo':v,'nombre':v} for v in sorted({t['location'] for t in tasks})],wbs=wbs,advertencias=warnings,faltantes=[dict(clave=k,etiqueta=k,valor=v,estado='SUPUESTO',motivo='Regla de interpretación o valor por defecto; las columnas presentes prevalecen.') for k,v in s.items()],supuestos=s,resumen=dict(items=len(seen),actividades=len(tasks),ocurrencias=len(tasks) if s['interpretacion']=='periodica' else 0),excluidos=excluded,vista_previa=preview,modo='mantenimiento' if s['interpretacion']=='periodica' else 'proyecto',modelo=dict(inicio=first.isoformat(),fin=last.isoformat()))

    def convertir(self,borrador,nombre=None):
        from ganttier.domain import ImportBundle
        if borrador.tiene_errores: raise ValueError('Corrija los errores antes de convertir.')
        s=borrador.supuestos
        cal='Carta '+hashlib.sha256(json.dumps(s,sort_keys=True).encode()).hexdigest()[:12]
        note='SUPUESTOS: '+json.dumps(s,ensure_ascii=False)+'; SHA-256: '+borrador.fuente['sha256']
        bundle=ImportBundle(mode=borrador.modo,project=dict(name=nombre or Path(borrador.fuente['nombre']).stem,start_date=borrador.modelo['inicio'],end_date=borrador.modelo['fin'],calendar_name=cal,currency=s['moneda'],description=note),calendars=[dict(name=cal,workweek=s['semana'],hours_per_day=s['horas_dia'],holidays_country=s['pais'] or None)],wbs=borrador.wbs)
        for task in borrador.maestras:
            row={k:v for k,v in task.items() if k!='origen'}
            row['calendar_name']=cal
            if borrador.modo=='mantenimiento':
                lo,hi=row.pop('window_start'),row.pop('window_end')
                row.update(frequency='A',first_due=lo,horizon_end=hi,tol_before_days=0,tol_after_days=(date.fromisoformat(hi)-date.fromisoformat(lo)).days)
                bundle.periodic_tasks.append(row)
            else:
                row['notes']=note+'; Origen: '+json.dumps(task['origen'],ensure_ascii=False)
                bundle.activities.append(row)
        return bundle
