"""Respaldos de la base de datos (P4-06a): listar, crear, descargar y programar restauración."""
import sqlite3
from dataclasses import asdict

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import backups, db
from .dependencies import connection

router = APIRouter(tags=["Respaldos"])


class BackupRequest(BaseModel):
    reason: str = Field("manual", max_length=60)


def _file(name: str):
    path = backups.backup_dir() / name
    # Sólo nombres generados por Ganttier dentro de la carpeta de respaldos (sin rutas).
    if path.name != name or not name.startswith("ganttier_") or not name.endswith(".db") or not path.exists():
        raise LookupError("No se encontró el respaldo.")
    return path


@router.get("/backups")
def list_backups():
    items = [asdict(b) for b in backups.list_backups()]
    return {"directory": str(backups.backup_dir()), "items": items,
            "last_verified": items[0]["created"] if items else None,
            "pending_restore": backups.pending_restore()}


@router.post("/backups", status_code=201)
def create_backup(body: BackupRequest, conn: sqlite3.Connection = Depends(connection)):
    return asdict(backups.create_backup(conn, body.reason or "manual", "manual"))


@router.get("/backups/{name}/download")
def download_backup(name: str):
    path = _file(name)
    return FileResponse(path, filename=path.name, media_type="application/vnd.sqlite3")


@router.post("/backups/{name}/restore")
def schedule_restore(name: str):
    _file(name)
    backups.schedule_restore(name, db.database_path())
    return {"pending_restore": name,
            "message": "La restauración se aplicará al reiniciar Ganttier; antes se guardará un respaldo de la base actual."}


@router.delete("/backups/pending", status_code=200)
def cancel_restore():
    backups.cancel_restore(db.database_path())
    return {"pending_restore": None}
