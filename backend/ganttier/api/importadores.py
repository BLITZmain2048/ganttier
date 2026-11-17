"""Asistente de formatos externos: revisión explícita y confirmación idempotente."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from uuid import uuid4
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Response
from pydantic import BaseModel, ConfigDict, Field
from .. import services, repo
from ..importadores import Perfil, AlmacenPerfiles, detectar, previsualizar, confirmar, formatos
from .dependencies import connection

router = APIRouter(tags=['Importación asistida'])
_LOCK = threading.RLock()


class SolicitudBorrador(BaseModel):
    model_config = ConfigDict(extra='forbid')
    perfil: Perfil | None = None
    perfil_id: str | None = None
    supuestos: dict = Field(default_factory=dict)


class Confirmacion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str
    nombre: str | None = Field(default=None, max_length=200)
    aceptar_supuestos: bool = False
    aceptar_advertencias: bool = False
    aceptar_revision_inventario: bool = False


def _root():
    return services.DATA_DIR / 'importaciones_asistidas'


def _path(ident, suffix):
    if not re.fullmatch(r'[0-9a-f]{32}', ident):
        raise LookupError('Identificador de carga inválido.')
    return _root() / (ident + suffix)


def _source(ident):
    metadata = _path(ident, '.fuente.json')
    if not metadata.is_file():
        raise LookupError('La carga no existe o fue eliminada. Vuelva a seleccionar el archivo.')
    suffix = json.loads(metadata.read_text(encoding='utf-8')).get('extension', '.xlsx')
    if not re.fullmatch(r'\.[a-z0-9]{1,10}', suffix):
        raise ValueError('Extensión de almacenamiento inválida.')
    path = _path(ident, suffix)
    if not path.is_file():
        raise LookupError('La carga no existe o fue eliminada. Vuelva a seleccionar el archivo.')
    return path


def _json_write(path, value):
    temporary = path.with_name(uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@router.get('/importadores/perfiles')
def perfiles():
    return [p.model_dump() for p in AlmacenPerfiles(services.DATA_DIR).listar()]


@router.get('/importadores/formatos')
def formatos_instalados():
    return formatos()


def _excel_response(content, name):
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


@router.get('/importadores/plantillas/inventario.xlsx')
def plantilla_inventario():
    from ..importadores.inventario import exportar_inventario
    return _excel_response(exportar_inventario(), 'Plantilla_inventario.xlsx')


@router.get('/importadores/borradores/{ident}/inventario.xlsx')
def exportar_borrador_inventario(ident: str):
    from ..importadores.inventario import exportar_inventario
    with _LOCK:
        _source(ident)
        path = _path(ident, '.borrador.json')
        if not path.is_file(): raise LookupError('Previsualice el inventario antes de exportarlo.')
        draft = json.loads(path.read_text(encoding='utf-8'))
        if 'inventario' not in draft.get('modelo', {}): raise ValueError('El borrador no contiene inventario.')
        return _excel_response(exportar_inventario(draft['modelo']['inventario']), 'Inventario_para_revision.xlsx')


@router.get('/projects/{pid}/inventario.xlsx')
def exportar_inventario_proyecto(pid: int, conn: sqlite3.Connection = Depends(connection)):
    from ..importadores.inventario import exportar_inventario
    draft = metadatos(pid, conn)
    if 'inventario' not in draft.get('modelo', {}): raise ValueError('El proyecto no contiene inventario importado.')
    return _excel_response(exportar_inventario(draft['modelo']['inventario']), 'Inventario.xlsx')


@router.post('/importadores/perfiles', status_code=201)
def guardar_perfil(body: Perfil):
    from ..importadores.servicio import LECTORES
    if body.formato not in LECTORES:
        raise ValueError('El formato del perfil no está instalado.')
    return AlmacenPerfiles(services.DATA_DIR).guardar(body).model_dump()


@router.delete('/importadores/perfiles/{ident}', status_code=204)
def borrar_perfil(ident: str):
    AlmacenPerfiles(services.DATA_DIR).borrar(ident)
    return Response(status_code=204)


@router.post('/importadores/archivos', status_code=201)
def subir(file: UploadFile = File(...)):
    suffix = Path(file.filename or '').suffix.lower()
    allowed = {ext for entry in formatos() for ext in entry['extensiones']}
    if not file.filename or suffix not in allowed:
        raise ValueError('Seleccione un archivo compatible: '+', '.join(sorted(allowed)))
    ident = uuid4().hex; path = _path(ident, suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        size = 0
        with path.open('wb') as out:
            while chunk := file.file.read(1024*1024):
                size += len(chunk)
                if size > 30*1024*1024:
                    raise ValueError('El archivo supera el límite de 30 MB.')
                out.write(chunk)
        detected = detectar(path)
        metadata = {'nombre': Path(file.filename).name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'extension': suffix}
        _json_write(_path(ident, '.fuente.json'), metadata)
        return {'id': ident, 'fuente': metadata, 'detecciones': detected,
                'advertencias': [] if detected else ['Formato no reconocido; seleccione un perfil compatible.']}
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


@router.post('/importadores/borradores/{ident}')
def borrador(ident: str, body: SolicitudBorrador):
    with _LOCK:
        path = _source(ident)
        if body.perfil is not None and body.perfil_id:
            raise ValueError('Seleccione perfil o perfil_id, no ambos.')
        profile = body.perfil or (AlmacenPerfiles(services.DATA_DIR).obtener(body.perfil_id) if body.perfil_id else None)
        draft = previsualizar(path, profile, body.supuestos)
        source = json.loads(_path(ident, '.fuente.json').read_text(encoding='utf-8'))
        draft.fuente['nombre'] = source['nombre']
        document = draft.to_dict()
        revision = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        document['revision'] = revision
        _json_write(_path(ident, '.borrador.json'), document)
        return document


@router.post('/importadores/confirmar/{ident}', status_code=201)
def crear(ident: str, body: Confirmacion, conn: sqlite3.Connection = Depends(connection)):
    with _LOCK:
        path = _source(ident)
        preview_path = _path(ident, '.borrador.json')
        if not preview_path.is_file():
            raise HTTPException(409, 'Previsualice el borrador antes de confirmar.')
        preview = json.loads(preview_path.read_text(encoding='utf-8'))
        if body.revision != preview['revision']:
            raise HTTPException(409, 'El borrador cambió. Revise nuevamente antes de confirmar.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != preview['fuente']['sha256']:
            raise HTTPException(409, 'El archivo cambió desde la previsualización.')
        if not body.aceptar_supuestos or (preview['advertencias'] and not body.aceptar_advertencias):
            raise ValueError('Confirme los supuestos y las advertencias revisadas.')
        if preview.get('modelo', {}).get('requiere_revision') and not body.aceptar_revision_inventario:
            raise ValueError('Confirme expresamente la revisión de las filas del inventario extraídas del PDF.')
        with repo.transaction(conn):
            conn.execute('CREATE TABLE IF NOT EXISTS importaciones_confirmadas (carga TEXT PRIMARY KEY, revision TEXT NOT NULL, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, resultado TEXT NOT NULL)')
            previous = conn.execute('SELECT revision, resultado FROM importaciones_confirmadas WHERE carga=?', (ident,)).fetchone()
            if previous:
                if previous['revision'] != body.revision:
                    raise HTTPException(409, 'Esta carga ya creó un proyecto. Suba otra copia para crear uno distinto.')
                return json.loads(previous['resultado'])
            result = confirmar(conn, path, preview['perfil'], preview['supuestos'],
                nombre=body.nombre or preview['fuente']['nombre'].rsplit('.',1)[0],
                aceptar_supuestos=True, aceptar_advertencias=body.aceptar_advertencias,
                aceptar_revision_inventario=body.aceptar_revision_inventario)
            if result['resumen'] != preview['resumen']:
                raise HTTPException(409, 'El resultado cambió desde la previsualización. Revise un nuevo borrador.')
            pid = result['proyecto']['id']
            # Conservar el nombre original, no el UUID de almacenamiento.
            metadata = json.loads(conn.execute('SELECT documento FROM importaciones_asistidas WHERE project_id=?', (pid,)).fetchone()[0])
            metadata['fuente']['nombre'] = preview['fuente']['nombre']
            # La revisión vincula filas, modelos y trazabilidad, no solo totales.
            # Se compara dentro de la transacción: una discrepancia revierte todo.
            reviewed = dict(metadata)
            reviewed['modelo'] = dict(metadata.get('modelo', {}))
            reviewed['modelo'].pop('revision_confirmada', None)
            current_revision = hashlib.sha256(json.dumps(reviewed, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if current_revision != body.revision:
                raise HTTPException(409, 'La lectura completa cambió desde la revisión. Prepare y revise un nuevo borrador.')
            if preview.get('modelo', {}).get('requiere_revision'):
                metadata['modelo']['revision_confirmada'] = True
            conn.execute('UPDATE importaciones_asistidas SET documento=? WHERE project_id=?', (json.dumps(metadata, ensure_ascii=False), pid))
            conn.execute('INSERT INTO importaciones_confirmadas VALUES (?, ?, ?, ?)', (ident, body.revision, pid, json.dumps(result, ensure_ascii=False)))
            return result


@router.delete('/importadores/archivos/{ident}', status_code=204)
def eliminar_carga(ident: str):
    with _LOCK:
        path = _source(ident)
        for suffix in (path.suffix, '.fuente.json', '.borrador.json'):
            _path(ident, suffix).unlink(missing_ok=True)
    return Response(status_code=204)


@router.get('/projects/{pid}/importacion-asistida')
def metadatos(pid: int, conn: sqlite3.Connection = Depends(connection)):
    repo.get(conn, 'projects', pid)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='importaciones_asistidas'").fetchone():
        raise LookupError('El proyecto no tiene una importación asistida registrada.')
    row = conn.execute('SELECT documento FROM importaciones_asistidas WHERE project_id=?', (pid,)).fetchone()
    if not row:
        raise LookupError('El proyecto no tiene una importación asistida registrada.')
    return json.loads(row[0])
