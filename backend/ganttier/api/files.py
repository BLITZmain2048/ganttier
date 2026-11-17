"""Excel e informes con archivos temporales locales y descargas restringidas."""
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .. import domain, repo, services
from . import schemas
from .dependencies import connection

router = APIRouter(tags=["Archivos"])


def _temporary_path(suffix: str) -> Path:
    directory = services.DATA_DIR / "tmp"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (uuid4().hex + suffix)


@contextmanager
def _uploaded(file: UploadFile):
    if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
        raise ValueError("Seleccione un libro Excel con extensión .xlsx.")
    path = _temporary_path(".xlsx")
    try:
        with path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination, length=1024 * 1024)
        yield path
    finally:
        path.unlink(missing_ok=True)
        file.file.close()


def _summary(bundle: domain.ImportBundle) -> dict:
    names = {"calendars": "calendarios", "resources": "recursos", "wbs": "wbs",
             "activities": "actividades", "relations": "relaciones", "assignments": "asignaciones",
             "periodic_tasks": "tareas_periodicas", "progress": "avance"}
    return {name: len(getattr(bundle, table)) for table, name in names.items()}


class ImportValidationError(ValueError):
    """Errores por fila descubiertos al resolver las ocurrencias en la BD."""


def _import_result(bundle: domain.ImportBundle) -> dict:
    return {"summary": _summary(bundle), "errors": [domain.to_dict(issue) for issue in bundle.issues]}


def _progress_sources(path: Path, bundle: domain.ImportBundle) -> None:
    """Conserva filas físicas, incluso con filas vacías, sin alterar el contrato Excel."""
    if bundle.mode != "mantenimiento" or not bundle.progress or bundle.has_errors:
        return
    from ..excel_io.reader import load_read_only, rows
    from ..excel_io.importer import normalize
    book = load_read_only(path)
    try:
        sheet = next((name for name in book.sheetnames if normalize(name) == normalize("Avance")), None)
        if sheet is None:
            return
        positions = (number for number, cells in rows(book[sheet]) if number > 1 and
            any(cell.value is not None and (not isinstance(cell.value, str) or cell.value.strip()) for cell in cells))
        for source, number in zip(bundle.progress, positions):
            source["_source_sheet"], source["_source_row"] = sheet, number
    finally:
        book.close()


@router.post("/projects/{pid}/reports")
def reports(pid: int, body: schemas.ReportRequest, conn: sqlite3.Connection = Depends(connection)):
    try:
        return services.render_reports(conn, pid, body.period_type, body.period_start, body.formats)
    except (ImportError, RuntimeError) as exc:
        pending, seen = [exc], set()
        while pending:
            cause = pending.pop()
            if id(cause) in seen:
                continue
            seen.add(id(cause))
            if isinstance(cause, (ImportError, services.ModuleUnavailable)):
                raise HTTPException(503, f"Falta una dependencia para generar el informe: {cause}. Ejecute instalar.bat.") from exc
            pending.extend(e for e in (cause.__cause__, cause.__context__) if e is not None)
        raise HTTPException(500, f"No se pudo generar el informe: {exc}") from exc
    except OSError as exc:
        raise HTTPException(500, f"No se pudo guardar el informe: {exc}") from exc
    except (ValueError, LookupError, sqlite3.Error):
        raise  # Conserva los errores de entrada y de BD con sus manejadores.
    except Exception as exc:
        raise HTTPException(500, f"No se pudo generar el informe: {exc}") from exc


@router.get("/reports/files/{filename}")
def report_file(filename: str):
    root = (services.DATA_DIR / "informes").resolve()
    if "/" in filename or "\\" in filename or Path(filename).name != filename:
        raise ValueError("Nombre de archivo inválido.")
    path = (root / filename).resolve()
    if path.parent != root or path.suffix not in {".pdf", ".xlsx", ".docx", ".html"}:
        raise ValueError("Archivo de informe inválido.")
    if not path.is_file():
        raise LookupError("No se encontró el informe solicitado.")
    # HTML en línea para la vista previa dentro de la app; los demás formatos como descarga
    inline = path.suffix in {".html", ".pdf"}
    return FileResponse(path, filename=filename, media_type="text/html; charset=utf-8" if path.suffix == ".html" else None,
                        content_disposition_type="inline" if inline else "attachment")


@router.get("/templates/{kind}")
def template(kind: str, background_tasks: BackgroundTasks):
    if kind not in {"proyecto", "mantenimiento"}:
        raise ValueError("El tipo de plantilla debe ser proyecto o mantenimiento.")
    build_project_template, build_maintenance_template, _, _ = services.excel_functions()
    path = _temporary_path(".xlsx")
    try:
        (build_project_template if kind == "proyecto" else build_maintenance_template)(path)
        if not path.is_file():
            raise ValueError("El generador no creó la plantilla.")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    background_tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, filename=f"plantilla-{kind}.xlsx", background=background_tasks)


def _save_bundle(conn: sqlite3.Connection, pid: int, bundle: domain.ImportBundle, mode: str = "merge") -> None:
    """Guarda el libro; en mantenimiento genera ocurrencias antes de aplicar el avance por código de ocurrencia."""
    if bundle.mode != "mantenimiento" or not bundle.progress:
        repo.save_import_bundle(conn, pid, bundle, mode)
        if bundle.mode == "mantenimiento":
            _ensure_occurrences(conn, pid, bundle)
        return
    progress = bundle.progress
    bundle.progress = []
    try:
        repo.save_import_bundle(conn, pid, bundle, mode)
        _ensure_occurrences(conn, pid, bundle)
        codes = {row[0] for row in conn.execute("SELECT code FROM activities WHERE project_id=?", (pid,))}
        for index, row in enumerate(progress, start=2):
            if row.get("activity_code") not in codes:
                bundle.issues.append(domain.ImportIssue(row.get("_source_sheet", "Avance"),
                    row.get("_source_row", index), "activity_code",
                    f"La ocurrencia {row.get('activity_code')} no existe en el horizonte del proyecto"))
        if bundle.has_errors:
            raise ImportValidationError("El libro contiene errores de validación.")
        repo.save_import_bundle(conn, pid, domain.ImportBundle(mode=bundle.mode, project={}, progress=progress), "merge")
    finally:
        bundle.progress = progress


def _ensure_occurrences(conn: sqlite3.Connection, pid: int, bundle: domain.ImportBundle) -> None:
    project = repo.get(conn, "projects", pid)
    count = conn.execute("SELECT COUNT(*) FROM activities WHERE project_id=? AND periodic_task_id IS NOT NULL", (pid,)).fetchone()[0]
    if count == 0 and bundle.periodic_tasks and project.end_date:
        services.generate_maintenance(conn, pid, project.start_date, project.end_date, True)


@router.post("/projects/{pid}/import")
def import_project(pid: int, file: UploadFile = File(...), mode: str = Query("merge", pattern="^(merge|replace)$"), dry_run: bool = False, conn: sqlite3.Connection = Depends(connection)):
    project = repo.get(conn, "projects", pid)
    _, _, parse_workbook, _ = services.excel_functions()
    with _uploaded(file) as path:
        bundle = parse_workbook(path, project.mode)
        _progress_sources(path, bundle)
    result = _import_result(bundle)
    if bundle.has_errors:
        if dry_run:
            return result
        return JSONResponse(status_code=422, content={"detail": "El libro contiene errores de validación.", **result})
    # Una validación fallida revierte también reemplazos, generación y maestros.
    class RollbackPreview(Exception):
        pass
    if mode == "replace" and not dry_run:
        # P4-06a: reemplazar borra actividades, líneas base y avances; antes se deja un respaldo verificado.
        from .. import backups
        try:
            backups.create_backup(conn, f"pre-importacion-reemplazo-p{pid}", "pre")
        except ValueError:
            pass  # base en memoria (pruebas): no hay archivo que respaldar
    try:
        with repo.transaction(conn):
            _save_bundle(conn, pid, bundle, mode)
            if dry_run:
                raise RollbackPreview
            services.sync_progress(conn, pid)
    except RollbackPreview:
        pass
    except ImportValidationError:
        result = _import_result(bundle)
        return result if dry_run else JSONResponse(status_code=422, content={"detail": "El libro contiene errores de validación.", **result})
    return _import_result(bundle)


@router.post("/import/new-project", status_code=201)
def import_new_project(file: UploadFile = File(...), mode: str | None = Query(None, pattern="^(proyecto|mantenimiento)$"), conn: sqlite3.Connection = Depends(connection)):
    _, _, parse_workbook, _ = services.excel_functions()
    with _uploaded(file) as path:
        bundle = parse_workbook(path, mode)
        _progress_sources(path, bundle)
    if bundle.has_errors:
        return JSONResponse(status_code=422, content={"detail": "El libro contiene errores de validación.", "errors": [domain.to_dict(issue) for issue in bundle.issues]})
    try:
        return services.create_project_from_bundle(conn, bundle, _save_bundle)
    except ImportValidationError:
        return JSONResponse(status_code=422, content={"detail": "El libro contiene errores de validación.", **_import_result(bundle)})


@router.get("/projects/{pid}/export.xlsx")
def export_project(pid: int, background_tasks: BackgroundTasks, conn: sqlite3.Connection = Depends(connection), nominal: bool = False):
    _, _, _, export_project = services.excel_functions()
    bundle = repo.export_bundle(conn, pid, nominal=nominal)
    path = _temporary_path(".xlsx")
    try:
        export_project(bundle, path)
        if not path.is_file():
            raise ValueError("El exportador no creó el libro.")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    background_tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, filename=f"proyecto-{pid}.xlsx", background=background_tasks)


@router.get("/projects/{pid}/gantt.xlsx")
def gantt_excel(pid: int, background_tasks: BackgroundTasks,
                mode: str = 'nivel', group_by: str = 'wbs', scale: str = 'week',
                first: str | None = Query(None, alias='from'),
                last: str | None = Query(None, alias='to'), critical: str = 'false',
                conn: sqlite3.Connection = Depends(connection)):
    from ..excel_io.gantt_xlsx import export_gantt, parse_options
    try:
        options = parse_options(mode, group_by, scale, first, last, critical)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    path = _temporary_path('.xlsx')
    try:
        export_gantt(conn, pid, path, options)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        path.unlink(missing_ok=True)
        raise
    background_tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, filename=f'gantt-{pid}-{mode}.xlsx',
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        background=background_tasks)
