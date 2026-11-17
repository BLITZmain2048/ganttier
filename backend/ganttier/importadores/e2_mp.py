"""Formulario económico: exclusivamente el bloque de precios/cantidades mapeado."""
from __future__ import annotations
from datetime import date
from decimal import Decimal
import math
import re
from .excel import abrir, normal, columna, valor, origen, vista, aviso
from .modelos import Perfil, Lectura


def numero(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class LectorE2:
    formato = 'e2_mp'

    def detectar(self, path):
        from openpyxl.utils import get_column_letter
        book = abrir(path)
        results = []
        try:
            for ws in book:
                for row in ws.iter_rows(max_row=min(50, ws.max_row)):
                    headers = {}
                    for cell in row:
                        if cell.value:
                            headers.setdefault(normal(cell.value), cell.column_letter)
                    eq = headers.get('equipo'); activity = headers.get('actividad')
                    price = next((v for k, v in headers.items() if 'preciounitario' in k), None)
                    qty = [v for k, v in headers.items() if 'cantidad' in k and re.search(r'[123]$', k)]
                    total = next((v for k, v in headers.items() if 'preciototal' in k), None)
                    if not (eq and activity and price and len(qty) >= 1 and total):
                        continue
                    end = columna(total)
                    profile = Perfil(id='formulario-e2-mp', nombre='Formulario E-2-MP', formato=self.formato,
                        hoja=ws.title, fila_encabezado=row[0].row, mapeo={'equipo': eq, 'descripcion': activity,
                        'precio': price, 'cantidades': qty, 'total': total, 'codigo': '',
                        'subtotales': get_column_letter(end+1), 'filas_ignorar': [], 'detener_en_total': True,
                        'palabras_ignorar': ['total', 'subtotal'],
                        'columnas_auxiliares': [get_column_letter(c) for c in range(end+2, ws.max_column+1)]})
                    results.append({'formato': self.formato, 'puntaje': .98,
                                    'motivos': ['Equipo y actividad', 'Precio unitario', f'{len(qty)} cantidades anuales'], 'perfil': profile.model_dump()})
                    break
        finally:
            book.close()
        return results

    def leer(self, path, perfil):
        book = abrir(path); formulas = abrir(path, valores=False)
        result = Lectura(perfil)
        try:
            if perfil.hoja not in book:
                raise ValueError('La hoja seleccionada no existe.')
            ws, fs = book[perfil.hoja], formulas[perfil.hoja]; m = perfil.mapeo
            for key in ('equipo', 'descripcion', 'precio', 'cantidades', 'total'):
                if not m.get(key):
                    raise ValueError(f'Falta el mapeo {key}.')
            used = [m['equipo'], m['descripcion'], m['precio'], m['total'], *m['cantidades']]
            if len(used) != len(set(used)):
                raise ValueError('El mapeo de datos usa columnas repetidas.')
            if set(used) & set(m.get('columnas_auxiliares', [])):
                raise ValueError('Una columna de datos también está marcada como auxiliar; corrija el mapeo.')
            for col in used:
                columna(col)
            seen = set(); groups = {}; total = Decimal(0); declared_total = None; row_costs = {}
            subtotals = []
            for r in range(perfil.fila_encabezado+1, ws.max_row+1):
                if r in m.get('filas_ignorar', []):
                    continue
                eq, desc, price = (valor(ws, r, m[k]) for k in ('equipo', 'descripcion', 'precio'))
                if any(normal(eq).startswith(normal(word)) for word in m.get('palabras_ignorar', []) if word):
                    amount = valor(ws, r, m['total'])
                    if numero(amount):
                        declared_total = amount
                    if m.get('detener_en_total', True) and 'mantenimientopreventivo' in normal(eq):
                        break
                    continue
                if not isinstance(eq, str) or not isinstance(desc, str):
                    continue
                code = str(valor(ws, r, m.get('codigo')) or f'MP{r:03d}')
                source = origen(ws, r, {'equipo': m['equipo'], 'descripcion': m['descripcion'], 'precio': m['precio'],
                                     'total': m['total'], **{f'cantidad_{i+1}': col for i, col in enumerate(m['cantidades'])}})
                errors = [col for col in used if ws.cell(r, columna(col)).data_type == 'e']
                if errors:
                    result.advertencias.append(aviso('error_excel', f'{code}: error de Excel en '+', '.join(f'{c}{r}' for c in errors)+'. Corrija la fuente.', r, nivel='error'))
                    continue
                uncached = [col for col in [m['precio'], *m['cantidades']]
                            if fs.cell(r, columna(col)).data_type == 'f' and valor(ws,r,col) is None]
                if uncached:
                    result.advertencias.append(aviso('formula_sin_valor', f'{code}: precio/cantidad con fórmula sin valor calculado en '+', '.join(f'{c}{r}' for c in uncached)+'. Recalcule una copia en Excel.', r, nivel='error'))
                    continue
                if code in seen:
                    result.advertencias.append(aviso('codigo_duplicado', f'Código duplicado: {code}.', r, nivel='error'))
                seen.add(code)
                if not numero(price) or price < 0:
                    result.advertencias.append(aviso('precio_invalido', f'{code}: precio unitario inválido o sin valor calculado.', r, nivel='error'))
                    continue
                quantities = [valor(ws, r, col) for col in m['cantidades']]
                if not all(numero(q) for q in quantities):
                    excluded = {'codigo': code, 'nombre': desc, 'equipo': eq, 'cantidades': quantities, 'origen': source,
                                'motivo': 'Falta frecuencia numérica o criterio para una actividad condicional.'}
                    result.excluidos.append(excluded)
                    result.advertencias.append(aviso('cantidad_condicional', f'{code}: cantidades no numéricas; se conserva sin programar.', r))
                    continue
                if any(q < 0 or q != int(q) or q not in (0, 1, 2, 4, 6, 12) for q in quantities):
                    result.advertencias.append(aviso('cantidad_invalida', f'{code}: cantidades admitidas por año: 0, 1, 2, 4, 6 o 12.', r, nivel='error'))
                    continue
                cost = Decimal(str(price)) * sum(int(q) for q in quantities)
                cached = valor(ws, r, m['total'])
                if numero(cached) and abs(Decimal(str(cached))-cost) > Decimal('.000001'):
                    result.advertencias.append(aviso('total_fila', f'{code}: precio × cantidades no coincide con el total de la fila.', r,
                        calculado=float(cost), declarado=cached, diferencia=float(cost-Decimal(str(cached)))))
                elif not numero(cached):
                    # Un libro guardado con openpyxl pierde cachés. No confundir ausencia con cero.
                    result.advertencias.append(aviso('formula_sin_cache', f'{code}: total sin valor numérico calculado; se usa precio × cantidades y no se certifica el total declarado.', r))
                groups.setdefault(eq.strip(), {'suma': Decimal(0), 'filas': []})
                groups[eq.strip()]['suma'] += cost; groups[eq.strip()]['filas'].append(r)
                total += cost
                row_costs[r] = cost
                result.items.append({'codigo': code, 'nombre': ' '.join(desc.split()), 'equipo': eq.strip(), 'precio': price,
                                     'cantidades': [int(q) for q in quantities], 'origen': source})
            # Revisa los subtotales del bloque, incluyendo filas condicionales finales.
            if m.get('subtotales'):
                for eq, group in groups.items():
                    candidates = [r for r in range(perfil.fila_encabezado+1, ws.max_row+1)
                                  if str(valor(ws, r, m['equipo']) or '').strip() == eq and numero(valor(ws, r, m['subtotales']))]
                    for r in candidates:
                        declared = valor(ws, r, m['subtotales'])
                        formula = str(valor(fs, r, m['subtotales']) or '')
                        match = re.fullmatch(r'=SUM\(\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)\)', formula, re.I)
                        if not match or match[1].upper() != m['total'].upper() or match[3].upper() != m['total'].upper():
                            result.advertencias.append(aviso('subtotal_no_verificable', 'Subtotal sin rango SUM verificable en la columna de totales mapeada.', r))
                            continue
                        calculated = sum((row_costs.get(n, Decimal(0)) for n in range(int(match[2]), int(match[4])+1)), Decimal(0))
                        delta = calculated-Decimal(str(declared))
                        subtotals.append({'equipo': eq, 'fila': r, 'calculado': float(calculated), 'declarado': declared})
                        if abs(delta) > Decimal('.000001'):
                            result.advertencias.append(aviso('subtotal', f'El subtotal de {eq} no cuadra.', r, diferencia=float(delta)))
            if declared_total is not None and abs(total-Decimal(str(declared_total))) > Decimal('.000001'):
                result.advertencias.append(aviso('total_detalle', 'El total E2-MP no coincide con la suma de las filas.', diferencia=float(total-Decimal(str(declared_total)))))
            summaries = []
            for sheet in book:
                if sheet.title == ws.title:
                    continue
                for row in sheet.iter_rows():
                    label = next((c for c in row if 'serviciodemantenimientopreventivo' in normal(c.value)), None)
                    if label:
                        values = [c for c in row if c.column > label.column and numero(c.value)]
                        if values:
                            declared = values[-1]
                            delta = total - Decimal(str(declared.value))
                            summaries.append({'hoja': sheet.title, 'celda': declared.coordinate, 'total': declared.value})
                            if abs(delta) > Decimal('.000001'):
                                result.advertencias.append(aviso('diferencia_resumen',
                                    f'{sheet.title}!{declared.coordinate}: resumen {declared.value:g} UF; detalle {float(total):g} UF. No se inventan partidas para cuadrarlo.',
                                    label.row, diferencia=round(float(delta), 6), hoja=sheet.title))
            result.totales = {'costo_fuente': round(float(total), 6), 'total_declarado': declared_total,
                              'subtotales': subtotals, 'resumenes': summaries}
            result.wbs = [{'code': f'EQ{i+1:03d}', 'name': eq, 'parent_code': None} for i, eq in enumerate(groups)]
            mapping = {w['name']: w['code'] for w in result.wbs}
            for item in result.items:
                item['wbs_code'] = mapping[item['equipo']]
            # Cantidades «año 1» no fijan el inicio contractual: siempre se pide.
            result.sugeridos = {'fecha_inicio': f'{date.today().year}-01-01',
                                'fecha_fin': f'{date.today().year+len(m["cantidades"])-1}-12-31'}
            result.vista_previa = vista(ws, perfil.fila_encabezado, perfil.fila_encabezado+12, sorted({columna(c) for c in used}))
            return result
        finally:
            book.close(); formulas.close()

    def construir(self, lectura, supuestos):
        from .construccion import base_task, esfuerzo_eco
        start = date.fromisoformat(supuestos['fecha_inicio']); end = date.fromisoformat(supuestos['fecha_fin'])
        n = len(lectura.perfil.mapeo['cantidades'])
        if start.month != 1 or start.day != 1 or end != date(start.year+n-1, 12, 31):
            raise ValueError(f'E2-MP requiere {n} años civiles completos desde el 1 de enero. Los años contractuales parciales requieren otro perfil de formato.')
        result = []
        for item in lectura.items:
            hh, crew, duration = esfuerzo_eco(item, supuestos)
            for i, qty in enumerate(item['cantidades']):
                if not qty:
                    continue
                year = start.year+i
                freq, interval = {12: ('M', 1), 6: ('NM', 2), 4: ('Q', 1), 2: ('S', 1), 1: ('A', 1)}[qty]
                month, day, tolerance = {12: (1,15,13), 6: (2,1,28), 4: (2,15,40), 2: (4,1,80), 1: (7,2,0)}[qty]
                due = date(year, month, day)
                task = base_task(item, f"{item['codigo']}-{year}", supuestos, hh, crew, duration)
                task.update(frequency=freq, interval=interval, first_due=due.isoformat(), horizon_end=f'{year}-12-31',
                    tol_before_days=(due-date(year,1,1)).days if qty == 1 else tolerance,
                    tol_after_days=(date(year,12,31)-due).days if qty == 1 else tolerance,
                    equipment=item['equipo'], wbs_code=item['wbs_code'], cost_per_occurrence=item['precio'],
                    frecuencia_origen=f'{qty} ejecuciones en año {i+1}')
                result.append(task)
        return result
