"""Rutas aditivas de importación de personal y avance."""
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .dependencies import connection
from .files import _temporary_path, _uploaded
from ..excel_io.personal_avance import build_template, import_workbook

router = APIRouter(tags=['Importación adicional'])


def _template(kind, tasks):
    path = _temporary_path('.xlsx')
    try:
        build_template(path, kind)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, filename=f'plantilla-{kind}.xlsx', background=tasks)


@router.get('/templates/personal')
def personal_template(background_tasks: BackgroundTasks):
    return _template('personal', background_tasks)


@router.get('/templates/avance')
def avance_template(background_tasks: BackgroundTasks):
    return _template('avance', background_tasks)


def _import(pid, file, kind, conn, **options):
    with _uploaded(file) as path:
        result = import_workbook(conn, pid, path, kind, **options)
    return JSONResponse(status_code=422, content=result) if result['errors'] and not options['dry_run'] else result


@router.post('/projects/{pid}/import/personal')
def personal(pid: int, file: UploadFile = File(...), dry_run: bool = True,
             modo: Literal['merge', 'replace'] = 'merge', conn: sqlite3.Connection = Depends(connection)):
    return _import(pid, file, 'personal', conn, dry_run=dry_run, modo=modo)


@router.post('/projects/{pid}/import/avance')
def avance(pid: int, file: UploadFile = File(...), dry_run: bool = True,
           allow_regressions: bool = False, conn: sqlite3.Connection = Depends(connection)):
    return _import(pid, file, 'avance', conn, dry_run=dry_run, allow_regressions=allow_regressions)


def install(parent):
    """Prioridad de plantillas estáticas sin editar el módulo histórico de archivos.

    Las rutas se insertan antes del comodín; include_router copia el resto.
    """
    templates = APIRouter()
    templates.routes = [r for r in router.routes if r.path.startswith('/templates/')]
    before = len(parent.routes)
    parent.include_router(templates)
    added = parent.routes[before:]
    del parent.routes[before:]
    parent.routes[0:0] = added
    remaining = APIRouter()
    remaining.routes = [r for r in router.routes if not r.path.startswith('/templates/')]
    return remaining
