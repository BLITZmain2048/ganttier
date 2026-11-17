"""Detección, borrador y confirmación transaccional mediante servicios Ganttier."""
from __future__ import annotations
from pathlib import Path
from dataclasses import fields
from datetime import date
from collections import Counter
import hashlib
import json
from .modelos import Perfil, Borrador
from .perfiles import AlmacenPerfiles
from .om_wbs import LectorOM
from .e2_mp import LectorE2
from .caso_json import CasoJSON
from .inventario import InventarioEquipos
from .construccion import resolver_supuestos, faltantes, plantilla_interna, validar_opciones

LECTORES = {}


def registrar(lector):
    """Extensión explícita: plugins confiables cargados por el programa, nunca por el archivo."""
    if lector.formato in LECTORES:
        raise ValueError(f'Ya existe el lector {lector.formato}.')
    LECTORES[lector.formato] = lector


registrar(LectorOM()); registrar(LectorE2())
registrar(CasoJSON()); registrar(InventarioEquipos())


def _registrar_opcionales():
    """Adaptadores de la ronda 3: si el módulo existe se registra; un error real de importación NO se silencia."""
    import importlib
    for modulo, clase in (('proyecto_externo', 'AdaptadorMSP'), ('proyecto_externo', 'AdaptadorP6'),
                          ('carta_gantt', 'AdaptadorCartaGantt'), ('pauta_equipos', 'AdaptadorPauta')):
        try:
            mod = importlib.import_module('.' + modulo, __package__)
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.endswith(modulo):
                continue
            raise
        registrar(getattr(mod, clase)())


_registrar_opcionales()


def detectar(path):
    results, errors = [], []
    for reader in LECTORES.values():
        if Path(path).suffix.lower() in getattr(reader, 'extensiones', {'.xlsx'}):
            try:
                results.extend(reader.detectar(Path(path)))
            except ValueError as exc:
                # Un lector con límites propios (p. ej. 20.000 filas) no debe impedir que otro,
                # con límites mayores, reconozca el archivo. Si nadie lo reconoce, se informa el primer error.
                errors.append(exc)
    if not results and errors:
        raise errors[0]
    return sorted(results, key=lambda x: -x['puntaje'])


def formatos():
    return [{'formato': key, 'extensiones': sorted(getattr(reader, 'extensiones', {'.xlsx'}))} for key, reader in LECTORES.items()]


def previsualizar(path, perfil=None, supuestos=None):
    path = Path(path)
    if isinstance(perfil, dict): perfil = Perfil.model_validate(perfil)
    if perfil is None or not perfil.mapeo:
        matches = detectar(path)
        if perfil:
            matches = [m for m in matches if m['formato'] == perfil.formato]
        if not matches:
            raise ValueError('Formato no reconocido. Seleccione un perfil compatible o revise los encabezados.')
        detected = Perfil.model_validate(matches[0]['perfil'])
        if perfil:
            detected.id, detected.nombre, detected.version, detected.supuestos = perfil.id, perfil.nombre, perfil.version, perfil.supuestos
        perfil = detected
    if perfil.formato not in LECTORES:
        raise ValueError('El formato del perfil no está instalado.')
    reader = LECTORES[perfil.formato]
    if path.suffix.lower() not in getattr(reader, 'extensiones', {'.xlsx'}):
        raise ValueError('La extensión del archivo no corresponde al formato seleccionado.')
    if hasattr(reader, 'previsualizar'):
        try:
            return reader.previsualizar(path, perfil, supuestos or {})
        except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
            raise ValueError(f'Datos o supuestos inválidos para {perfil.nombre}: revise campos, tipos y referencias.') from exc
    try:
        reading = reader.leer(path, perfil)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        raise ValueError(f'Mapeo inválido para {perfil.nombre}: revise columnas, listas y años.') from exc
    assumptions = resolver_supuestos(reading, supuestos)
    try:
        masters = reader.construir(reading, assumptions)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        raise ValueError(f'Mapeo o supuestos inválidos para {perfil.nombre}: revise tipos y campos requeridos.') from exc
    draft = Borrador(perfil=perfil, fuente={'nombre': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
        maestras=masters, sitios=reading.sitios, wbs=reading.wbs, advertencias=reading.advertencias,
        faltantes=faltantes(reading, assumptions), supuestos=assumptions,
        resumen={'items': len(reading.items), 'maestras': len(masters), **reading.totales},
        excluidos=reading.excluidos, vista_previa=reading.vista_previa)
    if not masters:
        draft.advertencias.append(dict(codigo='sin_tareas', mensaje='No se encontraron tareas programables.', nivel='error'))
    validar_opciones(draft)
    if not draft.tiene_errores:
        if assumptions['equipos'] or assumptions['lotes'] or assumptions['rutas']:
            # La previsualización avanzada resuelve equipos por los servicios reales
            # en memoria y genera demanda; no ejecuta ni guarda corridas del solver.
            from ganttier import db, repo, services
            conn = db.connect(':memory:')
            try:
                db.initialize(conn)
                bundle, _ = plantilla_interna(draft)
                project = services.create_project_from_bundle(conn, bundle, _guardar_modelo)
                rows = conn.execute('SELECT due_date, cost_budget, work_hours FROM activities WHERE project_id=?', (project.id,)).fetchall()
                draft.resumen.update(ocurrencias=len(rows), costo_total=round(sum(r['cost_budget'] for r in rows), 6),
                    hh_total=round(sum(r['work_hours'] for r in rows), 6),
                    ocurrencias_por_anio=dict(Counter(str(r['due_date'])[:4] for r in rows)))
            finally:
                conn.close()
        else:
            _resumir(draft)
    return draft


def _resumir(draft):
    from ganttier.domain import Calendar, PeriodicTask
    from ganttier.calendars import WorkCalendar
    from ganttier.maintenance.generator import generate_occurrences
    s = draft.supuestos
    start, end = date.fromisoformat(s['fecha_inicio']), date.fromisoformat(s['fecha_fin'])
    cal = Calendar(id=1, name='Previsualización', workweek=s['calendario']['semana'], hours_per_day=s['calendario']['horas_dia'], holidays_country=s['calendario']['pais'])
    names = {f.name for f in fields(PeriodicTask)}
    tasks = []
    for i, row in enumerate(draft.maestras, 1):
        values = {k: v for k, v in row.items() if k in names}
        for key in ('first_due', 'horizon_end'):
            if values.get(key): values[key] = date.fromisoformat(values[key])
        tasks.append(PeriodicTask(id=i, project_id=0, calendar_id=1, **values))
    # Límite antes de materializar grandes horizontes diarios.
    upper = sum((end-start).days+1 if t.frequency in {'D', 'DH'} else (end.year-start.year+1)*12 for t in tasks)
    if upper > 500000:
        raise ValueError('La estimación supera 500.000 ocurrencias; reduzca el horizonte.')
    occurrences = generate_occurrences(tasks, start, end, {1: WorkCalendar(cal, start, end)}, 1)
    if len(occurrences) > 100000:
        raise ValueError('El borrador supera el límite de 100.000 ocurrencias.')
    costs = {t.id: t.cost_per_occurrence for t in tasks}
    draft.resumen.update(ocurrencias=len(occurrences),
        ocurrencias_por_anio=dict(sorted(Counter(str(o.due_date.year) for o in occurrences).items())),
        costo_total=round(sum(costs[o.periodic_task_id] for o in occurrences), 6),
        hh_total=round(sum(o.work_hours for o in occurrences), 6))


def confirmar(conn, path, perfil=None, supuestos=None, *, nombre=None, aceptar_supuestos=False, aceptar_advertencias=False, aceptar_revision_inventario=False):
    from ganttier import services, repo, domain
    draft = previsualizar(path, perfil, supuestos)
    if draft.tiene_errores:
        raise ValueError('El borrador contiene errores: ' + '; '.join(a['mensaje'] for a in draft.advertencias if a.get('nivel') == 'error'))
    if not aceptar_supuestos:
        raise ValueError('Confirme expresamente los SUPUESTOS antes de crear el proyecto.')
    if draft.advertencias and not aceptar_advertencias:
        raise ValueError('Revise y acepte las advertencias antes de crear el proyecto.')
    if draft.modelo.get('requiere_revision') and not aceptar_revision_inventario:
        raise ValueError('Confirme expresamente la revisión de las filas del inventario extraídas del PDF.')
    if draft.modelo.get('requiere_revision'):
        draft.modelo['revision_confirmada'] = True
    reader = LECTORES[draft.perfil.formato]
    bundle = reader.convertir(draft, nombre) if hasattr(reader, 'convertir') else plantilla_interna(draft, nombre)[0]
    if bundle.has_errors:
        raise ValueError('El adaptador devolvió errores de validación: '+ '; '.join(i.message for i in bundle.issues))
    with repo.transaction(conn):
        project = services.create_project_from_bundle(conn, bundle, _guardar_modelo)
        trace = {row['code']: row for row in draft.maestras}
        for task in repo.list_entities(conn, 'periodic_tasks', project.id):
            source = trace.get(task.code)
            if not source and task.is_route:
                source = {'origen': {'tipo': 'SUPUESTO: ruta configurada por el usuario',
                    'componentes': [row['origen'] for row in draft.maestras
                        if draft.supuestos['lotes'].get(row['code'], draft.supuestos['lotes'].get(row['codigo_origen'], {})).get('route_code') == task.code]},
                    'frecuencia_origen': 'SUPUESTO: frecuencia de ruta'}
            if not source: continue
            note = 'SUPUESTO: HH, dotación, duración, calendario y ventanas. Fuente: ' + json.dumps(source['origen'], ensure_ascii=False)
            note += '; frecuencia de origen: ' + source.get('frecuencia_origen', '')
            conn.execute('UPDATE activities SET notes=? WHERE project_id=? AND periodic_task_id=?', (note, project.id, task.id))
        # Persistencia en la propia BD: no depende de conservar la carga temporal de la API.
        conn.execute('CREATE TABLE IF NOT EXISTS importaciones_asistidas (project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, esquema INTEGER NOT NULL, documento TEXT NOT NULL)')
        conn.execute('INSERT INTO importaciones_asistidas VALUES (?, 1, ?)', (project.id, json.dumps(draft.to_dict(), ensure_ascii=False)))
    return {'proyecto': domain.to_dict(project), 'resumen': draft.resumen, 'advertencias': draft.advertencias, 'supuestos': draft.supuestos}


def _guardar_modelo(conn, pid, bundle, mode='merge'):
    """Reutiliza persistencia y generación nativas, sin nivelar ni guardar corridas."""
    from ganttier import repo
    from ganttier.calendars import WorkCalendar
    from ganttier.maintenance.generator import generate_occurrences
    repo.save_import_bundle(conn, pid, bundle, mode)
    if bundle.mode != 'mantenimiento' or not bundle.periodic_tasks or bundle.activities:
        return
    project = repo.get(conn, 'projects', pid)
    tasks = repo.list_entities(conn, 'periodic_tasks', pid)
    _limitar_demanda(tasks, project.start_date, project.end_date)
    calendars = {c.id: WorkCalendar(c, project.start_date, project.end_date) for c in repo.list_entities(conn, 'calendars')}
    occurrences = generate_occurrences(tasks, project.start_date, project.end_date, calendars, project.calendar_id,
                                      resources=repo.list_entities(conn, 'resources', pid))
    if len(occurrences) > 100000:
        raise ValueError('El borrador supera el límite de 100.000 ocurrencias.')
    repo.save_occurrences(conn, pid, occurrences, True, return_entities=False)


def _limitar_demanda(tasks, start, end):
    import math
    estimate = 0
    for task in tasks:
        cycles = (end-start).days+1 if task.frequency in {'D','DH','ND'} else (end.year-start.year+1)*12
        batches = math.ceil(task.quantity_total/task.batch_size) if task.quantity_total and task.batch_size else 1
        estimate += cycles*batches
    if estimate > 500000:
        raise ValueError('La estimación supera 500.000 ocurrencias/lotes; reduzca el horizonte o aumente el tamaño de lote.')
