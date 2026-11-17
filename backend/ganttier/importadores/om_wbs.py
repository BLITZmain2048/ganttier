"""Matriz OM-WBS: jerarquía, frecuencia original y meses por sitio."""
from __future__ import annotations
import re
from datetime import date
from calendar import monthrange
from .modelos import Perfil, Lectura
from .excel import abrir, normal, columna, valor, origen, vista, aviso

FRECUENCIAS = {'diario': 'Diario', 'mensual': 'Mensual', 'trimestral': 'Trimestral', 'semestral': 'Semestral', 'anual': 'Anual'}
MESES = {name: i for i, names in enumerate(('', 'ene enero', 'feb febrero', 'mar marzo', 'abr abril', 'may mayo', 'jun junio',
         'jul julio', 'ago agosto', 'sep sept septiembre set setiembre', 'oct octubre', 'nov noviembre', 'dic diciembre')) for name in names.split()}


def codigo(value):
    text = str(value).strip() if value is not None else ''
    return text[:-2] if text.endswith('.0') else text


class LectorOM:
    formato = 'om_wbs'

    def detectar(self, path):
        from openpyxl.utils import get_column_letter
        book = abrir(path)
        results = []
        try:
            for ws in book:
                for row in ws.iter_rows(max_row=min(50, ws.max_row)):
                    freqs = {FRECUENCIAS[normal(c.value)]: c.column_letter for c in row if normal(c.value) in FRECUENCIAS}
                    months = [{'columna': c.column_letter, 'mes': MESES[normal(c.value)]} for c in row if normal(c.value) in MESES]
                    if len(freqs) < 3 or len(months) < 3:
                        continue
                    header = row[0].row
                    left = min(columna(c) for c in freqs.values())
                    counts = {c: sum(bool(re.fullmatch(r'\d+(?:\.\d+)*', codigo(ws.cell(r, c).value)))
                              for r in range(header + 1, min(ws.max_row, header + 60) + 1)) for c in range(1, left)}
                    if not counts or max(counts.values()) < 1:
                        continue
                    code_col = max(counts, key=counts.get)
                    year = next((int(ws.cell(r, c).value) for r in range(header-1, 0, -1) for c in range(1, ws.max_column+1)
                                 if isinstance(ws.cell(r, c).value, (int, float)) and 1900 <= ws.cell(r, c).value <= 2100), date.today().year)
                    year_from_source = any(isinstance(ws.cell(r, c).value, (int, float)) and ws.cell(r, c).value == year
                                           for r in range(1, header) for c in range(1, ws.max_column+1))
                    previous = months[0]['mes']
                    for month in months:
                        if month['mes'] < previous:
                            year += 1
                        month['anio'] = year
                        previous = month['mes']
                    sites = []
                    for values in ws.iter_rows(max_row=header-1):
                        for cell in values:
                            match = re.search(r'\[([^\]]+)\]', str(cell.value or ''))
                            if match:
                                sites.append({'codigo': match[1].strip().upper(), 'nombre': str(cell.value), 'celda': cell.coordinate})
                    profile = Perfil(id='matriz-om-wbs', nombre='Matriz OM-WBS', formato=self.formato, hoja=ws.title,
                        fila_encabezado=header, mapeo={'codigo': get_column_letter(code_col),
                        'descripcion': get_column_letter(left-1), 'descripciones_wbs': [get_column_letter(c) for c in range(code_col+1, left)],
                        'frecuencias': freqs, 'meses': months, 'sitios': sites, 'anio_en_fuente': year_from_source,
                        'filas_ignorar': [], 'palabras_ignorar': ['subtotal', 'total'], 'columnas_auxiliares': []})
                    results.append({'formato': self.formato, 'puntaje': min(1.0, .4 + .06*len(freqs) + .025*len(months)),
                                    'motivos': ['Códigos jerárquicos', f'{len(freqs)} frecuencias', f'{len(months)} meses'], 'perfil': profile.model_dump()})
                    break
        finally:
            book.close()
        return results

    def leer(self, path, perfil):
        book = abrir(path)
        result = Lectura(perfil)
        try:
            if perfil.hoja not in book:
                raise ValueError('La hoja seleccionada no existe.')
            ws = book[perfil.hoja]; m = perfil.mapeo
            for key in ('codigo', 'descripcion', 'frecuencias', 'meses'):
                if not m.get(key):
                    raise ValueError(f'Falta el mapeo {key}.')
            frequencies, months = m['frecuencias'], m['meses']
            used = {m['codigo'],m['descripcion'],*frequencies.values(),*(x['columna'] for x in months)}
            if used & set(m.get('columnas_auxiliares', [])):
                raise ValueError('Una columna de datos también está marcada como auxiliar; corrija el mapeo.')
            if any(f not in FRECUENCIAS.values() for f in frequencies):
                raise ValueError('El mapeo contiene una frecuencia no admitida.')
            if not 1 <= len(months) <= 12 or len({v['mes'] for v in months}) != len(months):
                raise ValueError('OM-WBS admite una matriz de hasta 12 meses distintos; divida matrices plurianuales.')
            for month in months:
                if type(month['anio']) is not int or type(month['mes']) is not int or not 1900 <= month['anio'] <= 2100 or not 1 <= month['mes'] <= 12:
                    raise ValueError('Cada mes requiere mes entero 1–12 y año entero 1900–2100.')
                columna(month['columna'])
            rows, seen = [], set()
            ignored = set(m.get('filas_ignorar', []))
            for r in range(perfil.fila_encabezado+1, ws.max_row+1):
                if r in ignored:
                    continue
                code = codigo(valor(ws, r, m['codigo']))
                if not re.fullmatch(r'\d+(?:\.\d+)*', code):
                    continue
                texts = [(col, valor(ws, r, col)) for col in m.get('descripciones_wbs', [m['descripcion']])]
                desc = valor(ws, r, m['descripcion']) or next((v for _, v in reversed(texts) if v), None)
                if not desc or any(normal(desc).startswith(normal(w)) for w in m.get('palabras_ignorar', []) if w):
                    continue
                if code in seen:
                    result.advertencias.append(aviso('codigo_duplicado', f'Código duplicado: {code}.', r, nivel='error'))
                seen.add(code)
                marks = [f for f, col in frequencies.items() if normal(valor(ws, r, col)) in {'x', 'si', '1'}]
                rows.append((r, code, str(desc).strip(), marks))
            known = {row[1] for row in rows}
            sites = {s['codigo'].upper(): dict(s, codigo=s['codigo'].upper()) for s in m.get('sitios', [])}
            for r, code, desc, marks in rows:
                parent = code.rpartition('.')[0]
                result.wbs.append({'code': code, 'name': desc, 'parent_code': parent if parent in known else None})
                leaf = not any(c.startswith(code+'.') for c in known)
                if not leaf and not marks:
                    continue
                if len(marks) != 1:
                    result.advertencias.append(aviso('sin_frecuencia' if not marks else 'varias_frecuencias',
                        f'{code}: seleccione exactamente una frecuencia; hay {len(marks)}.', r, nivel='error'))
                    continue
                by_site = {}
                cells = {'codigo': m['codigo'], 'descripcion': m['descripcion'], 'frecuencia': frequencies[marks[0]]}
                for i, month in enumerate(months):
                    value = valor(ws, r, month['columna'])
                    if value is None or not str(value).strip():
                        continue
                    tokens = [x.strip().upper() for x in re.split(r'[/;,\n]', str(value)) if x.strip()]
                    if tokens == ['X']:
                        if len(sites) == 1:
                            tokens = list(sites)
                        else:
                            result.advertencias.append(aviso('sitio_ambiguo', f'{code}: una X en {month["columna"]}{r} no identifica a qué planta corresponde.', r, nivel='error'))
                            continue
                    for site in tokens:
                        if site not in sites:
                            sites[site] = {'codigo': site, 'nombre': site, 'celda': f"{month['columna']}{r}"}
                            result.advertencias.append(aviso('sitio_sin_encabezado', f'Sitio {site} aparece en las marcas pero no en el encabezado.', r))
                        by_site.setdefault(site, []).append(i)
                    cells[f'mes_{i+1}'] = month['columna']
                if not by_site:
                    result.advertencias.append(aviso('sin_meses', f'{code}: no hay meses/sitios marcados.', r, nivel='error'))
                expected = {'Mensual': 12, 'Trimestral': 4, 'Semestral': 2, 'Anual': 1}.get(marks[0])
                for site, indexes in by_site.items():
                    if expected and len(indexes) != expected:
                        result.advertencias.append(aviso('frecuencia_meses',
                            f'{code}/{site}: {marks[0]} con {len(indexes)} meses marcados; nominal anual {expected}. Se requiere confirmar la política de meses.', r))
                result.items.append({'codigo': code, 'nombre': desc, 'frecuencia': marks[0], 'meses_por_sitio': by_site,
                                     'origen': origen(ws, r, cells)})
            result.sitios = list(sites.values())
            first, last = months[0], months[-1]
            result.sugeridos = {'fecha_inicio': date(first['anio'], first['mes'], 1).isoformat(),
                'fecha_fin': date(last['anio'], last['mes'], monthrange(last['anio'], last['mes'])[1]).isoformat()}
            result.vista_previa = vista(ws, perfil.fila_encabezado, perfil.fila_encabezado+12, range(1, ws.max_column+1))
            return result
        finally:
            book.close()

    def construir(self, lectura, supuestos):
        from .construccion import base_task, esfuerzo_om
        start = date.fromisoformat(supuestos['fecha_inicio']); end = date.fromisoformat(supuestos['fecha_fin'])
        months = lectura.perfil.mapeo['meses']
        if start.month != months[0]['mes']:
            raise ValueError('El mes inicial debe coincidir con el primer mes de la matriz; edite el mapeo de meses si cambió su orden.')
        result = []
        for item in lectura.items:
            hh, crew, duration = esfuerzo_om(item, supuestos)
            for site, indexes in item['meses_por_sitio'].items():
                selected = [months[i]['mes'] for i in indexes]
                offset = (selected[0]-start.month) % 12
                slot = start.year*12+start.month-1+offset
                y, m = divmod(slot, 12)
                daily = item['frecuencia'] == 'Diario'
                task = base_task(item, f"OM-{item['codigo']}-{site}", supuestos, hh, crew, duration)
                task.update(frequency='DH' if daily else 'M', first_due=date(y, m+1, 1 if daily else 15).isoformat(),
                    preferred_months=','.join(map(str, selected)), tol_before_days=0 if daily else 14,
                    tol_after_days=0 if daily else 13, location=site, wbs_code=item['codigo'],
                    horizon_end=end.isoformat(), frecuencia_origen=item['frecuencia'])
                result.append(task)
        return result
