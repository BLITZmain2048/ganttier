"""Exportación editable compatible con parse_workbook."""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from ganttier.domain import ImportBundle
from .streaming import close_workbook


def export_project(bundle: ImportBundle, path: str | Path) -> Path:
    """Exporta datos de intercambio, conservando extensiones de dominio conocidas."""
    from .templates import make_workbook
    if bundle.mode not in {"proyecto", "mantenimiento"}:
        raise ValueError("Modo inválido; use proyecto o mantenimiento.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = make_workbook(bundle, extras=True, temp_dir=target.parent)
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix='.ganttier-', suffix='.xlsx', delete=False) as handle:
            temporary = Path(handle.name)
        workbook.save(temporary)
        temporary.replace(target)
    except OSError as exc:
        raise OSError(f"No se pudo guardar el archivo Excel en {target}: {exc}") from exc
    finally:
        if hasattr(workbook, 'worksheets'):
            close_workbook(workbook)
        else:
            workbook.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
