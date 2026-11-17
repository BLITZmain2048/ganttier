"""API pública de plantillas e intercambio Excel."""
from pathlib import Path

from .importer import parse_workbook
from .exporter import export_project


def build_project_template(path: str | Path) -> Path:
    """Genera una plantilla editable de proyecto."""
    from .templates import build_project_template as build
    return build(path)


def build_maintenance_template(path: str | Path) -> Path:
    """Genera una plantilla editable de mantenimiento."""
    from .templates import build_maintenance_template as build
    return build(path)


__all__ = ["build_project_template", "build_maintenance_template", "parse_workbook", "export_project"]
