"""Inventario revisado de equipos, reglas por bloque y enlaces a maestras/rutas."""
from __future__ import annotations
from copy import deepcopy
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_CEILING
from io import BytesIO
import hashlib
import json
import math
import re
from .modelos import Perfil, Borrador
from .excel import abrir, normal, columna, aviso
from .caso_json import numero

COLUMNAS={'planta':'Planta','bloque':'Bloque','objeto':'Objeto/Tipo','cantidad':'Cantidad','unidad':'Unidad','detalle':'Detalle','fuente':'Fuente'}
ALIAS={'modulo':'Modulos','modulos':'Modulos','combinerbox':'SCB','scb':'SCB','inversor':'SKID','inversores':'SKID','skid':'SKID',
    'tracker':'Trackers','trackers':'Trackers','controltrackers':'Control Trackers','meteostation':'Meteo Station','meteocontrol':'Meteo Control',
    'paneldessaa':'Panel SSAA','panelssaa':'Panel SSAA','ups':'UPS','cctv':'CCTV','strings':'Strings','hileras':'Hileras',
    'sistemasdemedicionyproteccion':'Sistema de medición y protección','sistemademedicionyproteccion':'Sistema de medición y protección'}

def objeto(value):
    return ALIAS.get(normal(value),str(value).strip())

def es_total(value):
    return normal(value).startswith(('total','subtotal'))

def validar_filas(rows):
    if not isinstance(rows,list) or len(rows)>20000: raise ValueError('Inventario: se requiere una lista de hasta 20.000 filas.')
    result=[]; seen=set(); grouped=defaultdict(set)
    for i,row in enumerate(rows,1):
        if not isinstance(row,dict) or set(row)-set(COLUMNAS): raise ValueError(f'Inventario fila {i}: columnas desconocidas.')
        r={k:str(row.get(k,'') or '').strip() for k in COLUMNAS if k!='cantidad'}
        for k in ('planta','bloque','objeto','unidad'):
            if not r[k]: raise ValueError(f'Inventario fila {i}: falta {COLUMNAS[k]}.')
        if es_total(r['objeto']): raise ValueError('Una fila de total no puede incluirse como equipo.')
        r['objeto']=objeto(r['objeto'])
        q=numero(row.get('cantidad'),'Cantidad',0)
        if int(q)!=q: raise ValueError('Las cantidades de equipos deben ser enteras; no se redondea la fuente.')
        r['cantidad']=int(q)
        key=(r['planta'],r['bloque'],r['objeto'])
        if key in seen: raise ValueError('Inventario duplicado por planta/bloque/objeto: '+str(key))
        seen.add(key); grouped[(r['planta'],r['objeto'])].add(r['bloque'])
        result.append(r)
    if any('PLANTA' in blocks and len(blocks)>1 for blocks in grouped.values()):
        raise ValueError('No mezcle total de PLANTA y detalle por bloque del mismo objeto.')
    return result


def calcular_cantidad(rows,link):
    allowed={'codigo','destino','planta','bloque','objeto','regla','porcentaje','cantidad'}
    if set(link)-allowed: raise ValueError('Vínculo: campos desconocidos: '+str(sorted(set(link)-allowed)))
    selected=[r for r in rows if r['planta']==link.get('planta') and r['objeto']==objeto(link.get('objeto','')) and (not link.get('bloque') or r['bloque']==str(link['bloque']))]
    if not selected: raise ValueError('No existe el objeto de inventario para la planta/bloque seleccionada.')
    if len({r['unidad'] for r in selected})!=1: raise ValueError('El objeto mezcla unidades incompatibles.')
    rule=link.get('regla','total')
    if rule=='total': return sum(r['cantidad'] for r in selected)
    if rule=='fija':
        quantity=numero(link.get('cantidad'),'Cantidad fija',0)
        if int(quantity)!=quantity: raise ValueError('La cantidad fija debe ser entera.')
        return int(quantity)
    if rule=='porcentaje_por_bloque':
        pct=numero(link.get('porcentaje'),'Porcentaje',0,100)
        if any(r['bloque']=='PLANTA' for r in selected): raise ValueError('El porcentaje por bloque necesita el desglose por bloque; no se reparte un total de planta por inferencia.')
        return sum(int((Decimal(r['cantidad'])*Decimal(str(pct))/100).to_integral_value(rounding=ROUND_CEILING)) for r in selected)
    raise ValueError('Regla desconocida: use total, porcentaje_por_bloque o fija.')

def exportar_inventario(rows=None):
    """Datos de entrada literales; sin fórmulas ejecutables ni ejemplos importables ocultos."""
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    import openpyxl
    book=openpyxl.Workbook(); ws=book.active; ws.title='Inventario'
    ws.append(list(COLUMNAS.values()))
    for r in rows or []:
        ws.append([r.get(k,'') for k in COLUMNAS])
        for cell in ws[ws.max_row]:
            if isinstance(cell.value,str): cell.data_type='s'
    ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
    for i,width in enumerate([18,16,32,16,18,65,65],1): ws.column_dimensions[get_column_letter(i)].width=width
    for cell in ws[1]: cell.fill=PatternFill('solid',fgColor='183B56'); cell.font=Font(name='Arial',bold=True,color='FFFFFF')
    for row in ws.iter_rows(min_row=2):
        for c in row: c.font=Font(name='Arial',color='0000FF')
    guide=book.create_sheet('Instrucciones')
    guide.append(['Plantilla de inventario de equipos'])
    guide.append(['Edite Inventario desde la fila 2. Una fila por Planta/Bloque/Objeto. No incluya totales junto al detalle.'])
    guide.append(['Cantidad: entero no negativo. Bloque: identificador; use PLANTA solo cuando no exista desglose.'])
    guide.append(['Fuente: documento/página/fila. Los ejemplos de esta hoja NO se importan.'])
    guide.append(['Ejemplo: valores ilustrativos, no observados. Copie y sustituya con su inventario.'])
    guide.append(list(COLUMNAS.values())); guide.append(['Planta ejemplo','1','SCB',16,'unidades','Ejemplo editable','Ejemplo, sustituir por fuente real'])
    guide.column_dimensions['A'].width=100
    for row in guide:
        for cell in row: cell.font=Font(name='Arial',size=11)
    out=BytesIO(); book.save(out); book.close(); return out.getvalue()


def detectar_excel(path):
    book=abrir(path)
    try:
        for ws in book:
            if normal(ws.title)=='instrucciones': continue
            for row in ws.iter_rows(min_row=1,max_row=min(ws.max_row,40)):
                found={normal(c.value):c.column_letter for c in row if c.value is not None}
                mapping={k:found[normal(title)] for k,title in COLUMNAS.items() if normal(title) in found}
                if len(mapping)==len(COLUMNAS): return ws.title,row[0].row,mapping
    finally: book.close()
    return None


def leer_excel(path,profile):
    book=abrir(path); formulas=abrir(path,valores=False)
    rows=[]; excluded=[]; preview=[]
    try:
        ws=book[profile.hoja]; fs=formulas[profile.hoja]
        mapping=profile.mapeo
        if set(mapping)!=set(COLUMNAS): raise ValueError('Mapee las siete columnas del inventario.')
        for i in range(profile.fila_encabezado+1,ws.max_row+1):
            r={k:ws.cell(i,columna(c)).value for k,c in mapping.items()}
            if all(v in (None,'') for v in r.values()): continue
            preview.append(dict(fila=i,celdas={COLUMNAS[k]:str(v if v is not None else '') for k,v in r.items()}))
            if es_total(r['objeto']):
                preview[-1]['celdas']['Tratamiento']='TOTAL DE CONTROL · NO IMPORTAR'
                excluded.append(dict(fila=i,motivo='Fila de total: control, no equipo',valores=r)); continue
            if any(ws.cell(i,columna(c)).data_type=='e' for c in mapping.values()): raise ValueError(f'Fila {i}: error de Excel en inventario.')
            if fs.cell(i,columna(mapping['cantidad'])).data_type=='f' and r['cantidad'] is None:
                raise ValueError(f'Fila {i}: fórmula de cantidad sin valor calculado; recalcule el libro.')
            if not r['fuente']: r['fuente']=f'Excel, {ws.title}, fila {i}'
            rows.append(r)
    except (KeyError,TypeError,IndexError) as exc: raise ValueError('Mapeo de inventario inválido.') from exc
    finally: book.close(); formulas.close()
    return validar_filas(rows),excluded,preview,[]


def leer_pdf(path):
    from .pdf_texto import extraer_paginas
    pages,engine=extraer_paginas(path)
    rows=[]; excluded=[]; preview=[]; warnings=[]; summaries={}; plant=None; blocks={}; table=None; last=None; table_rows=[]
    plants={'Desierto':'DST','Los Andes':'AND','Sol del Norte':'SDN'}
    n=r'(?:\d{1,3}(?:[,\.]\d{3})+|\d+)'
    def integer(v): return int(v.replace(',','').replace('.',''))
    for page,text in enumerate(pages,1):
        table=None; last=None; table_rows=[]
        if '\ufffe' in text or '\uffff' in text:
            warnings.append(aviso('separador_pdf',f'Página {page}: separador de palabra no representable en Excel reemplazado por guion; revisar Detalle.'))
            text=text.replace('\ufffe','-').replace('\uffff','-')
        lines=[re.sub(r'\s+',' ',line).strip() for line in text.replace('\r','\n').splitlines() if line.strip()]
        joined=' '.join(lines)
        for full,abbr in plants.items():
            if re.search(r'\d+ Planta '+re.escape(full)+r'\b',joined): plant=abbr
        if plant:
            match=re.search(r'divisi[oó]n en (\d+) (?:parte|bloques)',joined,re.I)
            if match: blocks[plant]=int(match.group(1))
        for line in lines:
            if line.startswith('Cuadro '):
                table=line; last=None; table_rows=[]
                continue
            if line.startswith('Tipo '): continue
            if not table or line.startswith(('Confidencial','INFORME','Figura')): continue
            summary=re.fullmatch(r'(.+?)\s+('+n+r')\s+('+n+r')\s+('+n+r')\s+('+n+r')',line)
            if summary and (plant is None or '3 plantas' in table):
                label=summary.group(1); quantities=list(map(integer,summary.groups()[1:]))
                preview.append(dict(fila=f'p{page}: {table}',celdas={'Objeto':label,'DST':str(quantities[0]),'AND':str(quantities[1]),'SDN':str(quantities[2]),'Total':str(quantities[3])}))
                if sum(quantities[:3])!=quantities[3]: warnings.append(aviso('total_horizontal','Suma horizontal inconsistente: '+line,nivel='error'))
                if es_total(label):
                    preview[-1]['celdas']['Tratamiento']='TOTAL DE CONTROL · NO IMPORTAR'
                    computed=[sum(q[i] for _,q in table_rows) for i in range(4)]
                    without_modules=[sum(q[i] for obj,q in table_rows if obj!='Modulos') for i in range(4)]
                    scope='todas las filas del cuadro'
                    if quantities!=computed:
                        if any(obj=='Modulos' for obj,_ in table_rows) and quantities==without_modules:
                            scope='excluye módulos'
                            warnings.append(aviso('total_sin_modulos',f'{table}: el total excluye módulos; valores declarados {quantities}. Revisar el alcance.'))
                        else:
                            warnings.append(aviso('total_no_coincide',f'{table}: total declarado {quantities}, suma del detalle {computed}.',nivel='error'))
                    excluded.append(dict(pagina=page,cuadro=table,motivo='Fila de total: no importar; '+scope,valores=line,
                        declarado=quantities,suma_detalle=computed,suma_sin_modulos=without_modules))
                else:
                    preview[-1]['celdas']['Tratamiento']='Resumen: comparar, no duplicar el detalle'
                    summaries[objeto(label)]=dict(zip(('DST','AND','SDN'),quantities[:3]))
                    table_rows.append((objeto(label),quantities))
                continue
            match=re.fullmatch(r'(.+?)\s+('+n+r'|[-—])\s+('+n+r')\s*(.*)',line)
            if plant and table and match:
                label,per,total,detail=match.groups()
                if es_total(label):
                    excluded.append(dict(pagina=page,cuadro=table,motivo='Fila de total: no importar',valores=line)); continue
                total=integer(total); num_blocks=blocks.get(plant)
                if not num_blocks or not 1<=num_blocks<=100: raise ValueError('No se pudo identificar el número de bloques; use la plantilla Excel.')
                if per in {'-','—'}:
                    if not re.search(r'solo bloque 1',detail,re.I): raise ValueError('Cantidad sin desglose por bloque; revise mediante Excel.')
                    quantities=[total]+[0]*(num_blocks-1)
                else:
                    per=integer(per)
                    if per*num_blocks!=total: raise ValueError('Cantidad por bloque no coincide con el total de planta: '+line)
                    quantities=[per]*num_blocks
                last=[]
                for block,q in enumerate(quantities,1):
                    r=dict(planta=plant,bloque=str(block),objeto=objeto(label),cantidad=q,unidad='unidades',detalle=detail,fuente=f'PDF, página {page}, {table}')
                    rows.append(r); last.append(r)
                preview.append(dict(fila=f'p{page}: {table}',celdas={'Planta':plant,'Objeto':label,'Por bloque':str(per),'Planta total':str(total),'Detalle':detail}))
            elif last and not re.match(r'^\d+(?:\.\d+)? ',line):
                for r in last: r['detalle']+=' '+line
    if not rows: raise ValueError('Extracción no confiable: no se reconocieron tablas con planta, bloque y cantidad. Use la plantilla Excel de inventario.')
    # Los resúmenes controlan el detalle; nunca se suman ambos.
    for obj,totals in summaries.items():
        for plant,total in totals.items():
            selected=[r for r in rows if r['planta']==plant and r['objeto']==obj]
            if not selected and total==0:
                for block in range(1,blocks.get(plant,0)+1): rows.append(dict(planta=plant,bloque=str(block),objeto=obj,cantidad=0,unidad='unidades',detalle='Cero explícito en cuadro resumen',fuente='PDF, página 4, cuadro resumen'))
            elif sum(r['cantidad'] for r in selected)!=total:
                warnings.append(aviso('resumen_detalle',f'{plant}/{obj}: detalle no coincide con resumen {total}.',nivel='error'))
    warnings.append(aviso('revision_pdf',f'Extracción asistida con {engine}. Revise cada fila y sus totales; no se importa sin confirmación de revisión.'))
    warnings.append(aviso('strings_por_scb','La fuente indica 4 strings por SCB. Se conserva en Detalle; confirme strings_por_SCB antes de derivar cantidades de strings.'))
    return validar_filas(rows),excluded,preview,warnings


def derivar(rows,strings_per_scb,hileras_per_tracker):
    out=deepcopy(rows)
    for base,new,factor in [('SCB','Strings',strings_per_scb),('Trackers','Hileras',hileras_per_tracker)]:
        if factor is None: continue
        numero(factor,new,1,100000)
        if int(factor)!=factor: raise ValueError(f'{new}: el multiplicador debe ser entero.')
        # Una exportación identifica nuestros derivados en Detalle. Se pueden
        # recalcular con un parámetro nuevo; cantidades observadas conservan prioridad.
        out=[r for r in out if not (r['objeto']==new and r['detalle'].startswith('SUPUESTO editable:'))]
        observed={(r['planta'],r['bloque']) for r in out if r['objeto']==new}
        for r in rows:
            if r['objeto']==base and (r['planta'],r['bloque']) not in observed:
                out.append(dict(r,objeto=new,cantidad=r['cantidad']*int(factor),detalle=f'SUPUESTO editable: {factor} {new} por {base}. '+r['detalle']))
    return validar_filas(out)


class InventarioEquipos:
    formato='inventario_equipos'
    extensiones={'.xlsx','.pdf'}

    def detectar(self,path):
        if path.suffix.lower()=='.xlsx':
            result=detectar_excel(path)
            if not result: return []
            sheet,row,mapping=result; confidence=1; reasons=['Encabezados Planta/Bloque/Objeto/Tipo/Cantidad/Unidad/Detalle/Fuente']
        else:
            if not path.read_bytes().startswith(b'%PDF-'): raise ValueError('El archivo no es un PDF válido.')
            sheet,row,mapping='',1,{'extraccion':'tablas_con_revision'}; confidence=.5; reasons=['PDF: la extracción requiere revisión y puede requerir plantilla Excel']
        p=Perfil(id='inventario-equipos',nombre='Inventario de equipos',formato=self.formato,hoja=sheet,fila_encabezado=row,mapeo=mapping)
        return [dict(formato=self.formato,puntaje=confidence,motivos=reasons,perfil=p.model_dump())]

    def previsualizar(self,path,perfil,supuestos):
        is_pdf=path.suffix.lower()=='.pdf'
        s=dict(fecha_inicio='2027-01-01',fecha_fin='2027-12-31',calendario=dict(semana='1111100',horas_dia=8,pais='CL'),
            moneda='CLP',strings_por_SCB=None,hileras_por_tracker=None,filas_revisadas=[],vinculos=[],maestras=[],equipos=[],lotes={},rutas=[],recursos=[])
        for source in (perfil.supuestos,supuestos):
            if set(source)-s.keys(): raise ValueError('Supuestos de inventario desconocidos: '+str(sorted(set(source)-s.keys())))
            s.update(deepcopy(source))
        start=date.fromisoformat(s['fecha_inicio']); end=date.fromisoformat(s['fecha_fin'])
        if not 0<=(end-start).days<=3660: raise ValueError('Horizonte inválido, máximo diez años.')
        from .construccion import CalendarioSupuesto
        from ganttier.excel_io.schema import SCHEMAS
        s['calendario']=CalendarioSupuesto.model_validate(s['calendario']).model_dump()
        allowed_master={c.field for c in SCHEMAS['Plan_Maestro']}
        for task in s['maestras']:
            if not isinstance(task,dict) or set(task)-allowed_master:
                raise ValueError('Tarea maestra: campos desconocidos; use los nombres de Plan_Maestro.')
        try:
            rows,excluded,preview,warnings=leer_pdf(path) if is_pdf else leer_excel(path,perfil)
        except ValueError as exc:
            if not is_pdf: raise
            rows,excluded,preview=[],[],[]
            warnings=[aviso('extraccion_no_confiable',str(exc)+' Descargue /api/importadores/plantillas/inventario.xlsx.',nivel='error')]
        if s['filas_revisadas']:
            rows=validar_filas(s['filas_revisadas'])
            warnings=[w for w in warnings if w['codigo'] not in {'extraccion_no_confiable','resumen_detalle'}]
            warnings.append(aviso('correccion_usuario','Las filas fueron corregidas por el usuario; revisar contra los cuadros originales.'))
        if 'hileras_por_tracker' not in supuestos and 'hileras_por_tracker' not in perfil.supuestos:
            factors={int(m.group(1)) for r in rows if r['objeto']=='Trackers' for m in re.finditer(r'(\d+)\s+(?:rows|hileras)',r['detalle'],re.I)}
            if len(factors)==1: s['hileras_por_tracker']=factors.pop()
        raw_rows=deepcopy(rows)
        rows=derivar(rows,s['strings_por_SCB'],s['hileras_por_tracker'])
        if not rows and not any(w.get('nivel')=='error' for w in warnings):
            warnings.append(aviso('inventario_vacio','El inventario no contiene filas de equipos; complete la plantilla.',nivel='error'))
        masters=[]
        for task in s['maestras']:
            masters.append(dict(task,codigo_origen=task['code'],origen={'tipo':'SUPUESTO: maestra definida por el usuario'},frecuencia_origen=task.get('frequency','M')))
        codes={t['code'] for t in masters}; routes={r['code']:r for r in s['rutas']}
        if len(codes)!=len(masters) or len(routes)!=len(s['rutas']) or codes&routes.keys(): raise ValueError('Códigos de maestras/rutas duplicados.')
        seen=set(); links=[]
        for link in s['vinculos']:
            code=link.get('codigo'); dest=link.get('destino','actividad')
            if (dest,code) in seen: raise ValueError('Más de un vínculo para el mismo destino.')
            seen.add((dest,code))
            if dest not in {'actividad','ruta'} or code not in (codes if dest=='actividad' else routes): raise ValueError('Destino del vínculo desconocido: '+str(code))
            quantity=calcular_cantidad(rows,link)
            unit=next(r['unidad'] for r in rows if r['planta']==link['planta'] and r['objeto']==objeto(link['objeto']))
            if quantity<=0: raise ValueError('Cantidad cero: retire la maestra/ruta de esa planta o cambie el vínculo; no se crea trabajo ficticio.')
            if dest=='ruta':
                routes[code].update(quantity_total=quantity,unit=unit,equipment=link['objeto'],location=link['planta'])
                for master in masters:
                    route_code=s['lotes'].get(master['code'],{}).get('route_code',master.get('route_code'))
                    if route_code==code:
                        # El vínculo de ruta establece su contexto para componentes
                        # sin objeto/planta; las contradicciones explícitas se validan.
                        master.setdefault('equipment',link['objeto'])
                        master.setdefault('location',link['planta'])
            else:
                s['lotes'].setdefault(code,{}).update(quantity_total=quantity,unit=unit)
                next(t for t in masters if t['code']==code).update(equipment=link['objeto'],location=link['planta'])
            links.append(dict(link,Cantidad_Total=quantity,unidad=unit))
        totals={}
        for r in rows: totals.setdefault(r['planta'],{}).setdefault(r['objeto'],0); totals[r['planta']][r['objeto']]+=r['cantidad']
        labels={'fecha_inicio':'Inicio SUPUESTO','fecha_fin':'Fin SUPUESTO','calendario':'Calendario SUPUESTO','moneda':'Moneda SUPUESTA',
            'strings_por_SCB':'Strings por SCB (confirmar)','hileras_por_tracker':'Hileras por tracker (confirmar)',
            'filas_revisadas':'Correcciones del inventario (lista opcional)','vinculos':'Vínculos a actividades/rutas','maestras':'Tareas maestras',
            'equipos':'Equipos de trabajo','lotes':'Lotes y rendimientos','rutas':'Rutas','recursos':'Recursos nominales'}
        reasons={'filas_revisadas':'Lista completa de filas corregidas. Vacío conserva la extracción; nunca habilita importación sin confirmar revisión.',
            'vinculos':'codigo, destino actividad/ruta, planta, objeto; regla total, porcentaje_por_bloque (porcentaje) o fija (cantidad).',
            'strings_por_SCB':'Sin confirmar no se generan Strings. La referencia de aceptación usa 24; no se infiere de los módulos.',
            'hileras_por_tracker':'El PDF de referencia indica 27 hileras por motor de tracker; confirme para su inventario.',
            'maestras':'Estructura de Plan_Maestro: code, name, frequency, first_due, work_hours, crew_size. Vacío guarda solo inventario.',
            'lotes':'Por código de maestra: batch_size, productivity_day_team, team_names. Cantidad_Total proviene del vínculo.',
            'rutas':'Estructura nativa Rutas. Un vínculo con destino ruta actualiza quantity_total de esa ruta.'}
        draft=Borrador(perfil=perfil,fuente=dict(nombre=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()),maestras=masters,
            sitios=[dict(codigo=p,nombre=p) for p in totals],wbs=[],advertencias=warnings,
            faltantes=[dict(clave=k,etiqueta=v,valor=s[k],estado='SUPUESTO',motivo=reasons.get(k,'Parámetro editable de planificación; no es un dato medido.')) for k,v in labels.items()],
            supuestos=s,resumen=dict(items=len(rows),maestras=len(masters),inventario_por_planta=totals,vinculos=links,ocurrencias=0),
            excluidos=excluded,vista_previa=preview[:300],modelo=dict(inventario=rows,inventario_base=raw_rows,requiere_revision=is_pdf,plantilla='/api/importadores/plantillas/inventario.xlsx'))
        if masters or routes:
            from .construccion import validar_opciones
            validar_opciones(draft)
            from ganttier import db,services
            from .servicio import _guardar_modelo
            conn=db.connect(':memory:')
            try:
                db.initialize(conn); project=services.create_project_from_bundle(conn,self.convertir(draft),_guardar_modelo)
                draft.resumen['ocurrencias']=conn.execute('SELECT COUNT(*) FROM activities WHERE project_id=?',(project.id,)).fetchone()[0]
            finally: conn.close()
        return draft

    def convertir(self,draft,nombre=None):
        from .construccion import plantilla_interna
        return plantilla_interna(draft,nombre)[0]
