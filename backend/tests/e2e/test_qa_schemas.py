"""Validación de esquemas independiente del arranque de FastAPI."""
import importlib.util
from pathlib import Path

import pytest

pydantic = pytest.importorskip('pydantic', minversion='2')
spec = importlib.util.spec_from_file_location('ganttier.api.schemas', Path(__file__).resolve().parents[2]/'ganttier/api/schemas.py')
schemas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schemas)


@pytest.mark.parametrize('payload', [{'code':'T','name':'T','preferred_months':'0,13'},
    {'code':'T','name':'T','preferred_months':'ene'},
    {'code':'T','name':'T','first_due':'2026-02-01','horizon_end':'2026-01-01'}])
def test_periodic_task_invalid_fields(payload):
    with pytest.raises(pydantic.ValidationError):
        schemas.PeriodicTask.model_validate(payload)


def test_baseline_whitespace_name():
    with pytest.raises(pydantic.ValidationError):
        schemas.BaselineRequest(name=' ')


def test_activity_finished_requires_actual_start():
    with pytest.raises(pydantic.ValidationError):
        schemas.Activity(code='A',name='A',percent_complete=100)
