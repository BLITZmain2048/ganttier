"""Pruebas MOD en proceso: base, copias y artefactos siempre bajo trabajos/."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from ganttier import db, repo
from ganttier.cli._base import CliError, Context
from ganttier.cli.comandos import modelo
from ganttier.cli.comandos._modelo_io import dump_project, REFERENCES

ROOT = Path(__file__).resolve().parents[3]
TEMP = ROOT / "trabajos/_temporales/cli_codex_20261019/MOD"


@pytest.fixture(scope="module")
def parser():
    root = argparse.ArgumentParser()
    modelo.register(root.add_subparsers(dest="group", required=True))
    return root


@pytest.fixture
def ctx(monkeypatch):
    root = TEMP / uuid4().hex
    root.mkdir(parents=True)
    monkeypatch.setenv("GANTTIER_DB", str(root / "model.db"))
    monkeypatch.setenv("GANTTIER_DATA_DIR", str(root / "data"))
    from ganttier import services
    monkeypatch.setattr(services, "DATA_DIR", root / "data")
    with closing(db.connect(root / "model.db")) as conn:
        db.initialize(conn)
        project = repo.create(conn, "projects", {"name": "Modelo", "start_date": "2026-01-05", "data_date": "2026-10-01", "calendar_id": 1})
        conn.commit()
    return Context(root / "model.db", root / "data", project=str(project.id), write=True, no_backup=True)


def call(parser, ctx, *argv, values=None):
    argv = [str(v) for v in argv]
    if values is not None:
        argv.extend(["--json", json.dumps(values, ensure_ascii=False)])
    args = parser.parse_args(argv)
    return args.handler(args, ctx).data


def counts_hash(ctx):
    with closing(ctx.conn()) as conn:
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                  for t in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]}
    return hashlib.sha256(json.dumps(counts, sort_keys=True).encode()).hexdigest()


def activity(parser, ctx, code="A", **kw):
    return call(parser, ctx, "actividades", "agregar", values=dict(code=code, name=code, **kw))


def test_crud_complete_and_filters(parser, ctx):
    a = activity(parser, ctx)
    assert call(parser, ctx, "actividades", "ver", a["id"])["code"] == "A"
    updated = call(parser, ctx, "actividades", "actualizar", a["id"], values={"duration_days": 4, "critical": True})
    assert updated["name"] == "A" and updated["duration_days"] == 4
    page = call(parser, ctx, "actividades", "listar", "--critical", "true", "--campos", "id,code", "--limite", 1)
    assert page == {"items": [{"id": a["id"], "code": "A"}], "total": 1, "offset": 0, "limit": 1}
    assert call(parser, ctx, "actividades", "listar", "--desde", 1)["total"] == 1
    assert call(parser, ctx, "actividades", "eliminar", a["id"])["deleted"]
    assert call(parser, ctx, "actividades", "listar")["total"] == 0


@pytest.mark.parametrize("values,field", [({"code": "A", "name": "A", "duration_days": -1}, "duration_days"),
    ({"code": "A", "name": "A", "unexpected": True}, "unexpected"),
    ({"code": "A", "name": "A", "planned_start": "no-fecha"}, "planned_start"),
    ({"code": "A", "name": "A", "percent_complete": 120}, "percent_complete"),
    ({"name": "A"}, "code")])
def test_validation_errors_by_field(parser, ctx, values, field):
    before = counts_hash(ctx)
    with pytest.raises(CliError) as exc:
        call(parser, ctx, "actividades", "agregar", values=values)
    assert any(e["field"] == field for e in exc.value.detail["errors"])
    assert counts_hash(ctx) == before


def test_safe_simulation_and_write_backup(parser, ctx):
    before = counts_hash(ctx)
    ctx.write = False
    activity(parser, ctx)
    assert ctx.mutation_info.simulated
    assert counts_hash(ctx) == before
    ctx.write, ctx.no_backup = True, False
    activity(parser, ctx)
    assert not ctx.mutation_info.simulated
    assert counts_hash(ctx) != before
    assert Path(ctx.mutation_info.backup).exists()


def test_atomic_batch_errors_with_index_and_field(parser, ctx):
    before = counts_hash(ctx)
    with pytest.raises(CliError) as exc:
        call(parser, ctx, "lote", "aplicar", values=[
            {"op": "crear", "entidad": "actividades", "valores": {"code": "A", "name": "A"}},
            {"op": "crear", "entidad": "actividades", "valores": {"code": "B", "name": "B", "duration_days": -2}},
            {"op": "crear", "entidad": "actividades", "valores": {"code": "C"}},
        ])
    assert {e["index"] for e in exc.value.detail["errors"]} == {1, 2}
    assert exc.value.detail["errors"][0]["field"] == "duration_days"
    assert counts_hash(ctx) == before
    result = call(parser, ctx, "lote", "aplicar", values=[
        {"op": "crear", "entidad": "actividades", "valores": {"code": "A", "name": "A"}}])
    assert len(result["created_ids"]) == 1 and result["atomic"]


def test_batch_rolls_back_update_and_delete(parser, ctx):
    a = activity(parser, ctx)
    b = activity(parser, ctx, "B")
    with pytest.raises(CliError):
        call(parser, ctx, "lote", "aplicar", values=[
            {"op": "actualizar", "entidad": "actividades", "id": a["id"], "valores": {"name": "cambio"}},
            {"op": "eliminar", "entidad": "actividades", "id": b["id"]},
            {"op": "crear", "entidad": "actividades", "valores": {"code": "A", "name": "duplicado"}},
        ])
    assert call(parser, ctx, "actividades", "ver", a["id"])["name"] == "A"
    assert call(parser, ctx, "actividades", "ver", b["id"])["name"] == "B"


def test_project_commands_confirmation_and_scope(parser, ctx):
    a = activity(parser, ctx)
    assert call(parser, ctx, "proyectos", "resumen")["activities"] == 1
    copied = call(parser, ctx, "proyectos", "duplicar")
    assert copied["name"] == "Modelo (copia)"
    ctx.project = str(copied["id"])
    with pytest.raises(CliError):
        call(parser, ctx, "actividades", "actualizar", a["id"], values={"name": "ajeno"})
    with pytest.raises(CliError):
        call(parser, ctx, "proyectos", "eliminar", "--confirmar-nombre", "otro")
    assert call(parser, ctx, "proyectos", "eliminar", "--confirmar-nombre", copied["name"])["deleted"]
    with pytest.raises(CliError) as exc:
        call(parser, ctx, "proyectos", "crear", values={"name": "MT", "mode": "mantenimiento"})
    assert exc.value.detail["errors"][0]["field"] == "end_date"


def test_chain_cycle_and_cross_project_reference(parser, ctx):
    a, b, c = [activity(parser, ctx, code) for code in ("A", "B", "C")]
    call(parser, ctx, "relaciones", "cadena", values={"codigos": ["A", "B", "C"], "tipo": "FS", "desfase": 1})
    assert call(parser, ctx, "relaciones", "listar")["total"] == 2
    with pytest.raises(CliError):
        call(parser, ctx, "relaciones", "agregar", values={"pred_id": c["id"], "succ_id": a["id"]})
    original = ctx.project
    other = call(parser, ctx, "proyectos", "crear", values={"name": "Otro"})
    ctx.project = str(other["id"])
    foreign = activity(parser, ctx, "D")
    ctx.project = original
    with pytest.raises(CliError):
        call(parser, ctx, "relaciones", "agregar", values={"pred_id": a["id"], "succ_id": foreign["id"]})


def test_all_entities_and_protected_history(parser, ctx):
    calendar = call(parser, ctx, "calendarios", "agregar", values={"name": "Especial", "exceptions": [{"date": "2026-01-06", "working": False}]})
    resource = call(parser, ctx, "recursos", "agregar", values={"name": "Ana", "calendar_id": calendar["id"]})
    wbs = call(parser, ctx, "wbs", "agregar", values={"code": "W", "name": "Raíz"})
    periodic = call(parser, ctx, "periodicas", "agregar", values={"code": "P", "name": "Periódica"})
    a = activity(parser, ctx, wbs_id=wbs["id"], periodic_task_id=periodic["id"])
    assignment = call(parser, ctx, "asignaciones", "agregar", values={"activity_id": a["id"], "resource_id": resource["id"]})
    baseline = call(parser, ctx, "lineas-base", "agregar", values={"name": "LB"})
    key = f"{baseline['id']}:{a['id']}"
    call(parser, ctx, "actividades-base", "agregar", values={"baseline_id": baseline["id"], "activity_id": a["id"], "start": "2026-01-05", "finish": "2026-01-05", "work_hours": 8, "cost": 1, "duration_days": 1})
    call(parser, ctx, "actividades-base", "actualizar", key, values={"cost": 2})
    assert call(parser, ctx, "actividades-base", "ver", key)["cost"] == 2
    progress = call(parser, ctx, "avances", "agregar", values={"activity_id": a["id"], "date": "2026-01-05", "percent_complete": 10, "actual_hours": 1})
    with pytest.raises(CliError) as exc:
        call(parser, ctx, "actividades", "eliminar", a["id"])
    assert exc.value.detail["history"]["baseline_rows"] == 1
    assert exc.value.detail["history"]["progress"] == 1
    for entity, ident, patch in [("recursos", resource["id"], {"name": "Ana María"}),
        ("wbs", wbs["id"], {"name": "WBS"}), ("periodicas", periodic["id"], {"name": "PM"}),
        ("asignaciones", assignment["id"], {"units": 2}), ("lineas-base", baseline["id"], {"name": "LB2"}),
        ("avances", progress["id"], {"actual_hours": 2}), ("calendarios", calendar["id"], {"hours_per_day": 7})]:
        assert call(parser, ctx, entity, "actualizar", ident, values=patch)
        assert call(parser, ctx, entity, "listar")["total"] >= 1
    call(parser, ctx, "actividades-base", "eliminar", key)
    call(parser, ctx, "avances", "eliminar", progress["id"])
    call(parser, ctx, "asignaciones", "eliminar", assignment["id"])
    call(parser, ctx, "actividades", "eliminar", a["id"])
    for entity, ident in [("periodicas", periodic["id"]), ("wbs", wbs["id"]), ("recursos", resource["id"]), ("calendarios", calendar["id"]), ("lineas-base", baseline["id"])]:
        assert call(parser, ctx, entity, "eliminar", ident)["deleted"]


def test_teams_routes_absences_and_maintenance_duration(parser, ctx):
    mt = call(parser, ctx, "proyectos", "crear", values={"name": "MT", "mode": "mantenimiento", "start_date": "2026-01-05", "end_date": "2026-12-31"})
    ctx.project = str(mt["id"])
    team = call(parser, ctx, "equipos", "agregar", values={"name": "Equipo", "members": 2})
    assert call(parser, ctx, "equipos", "actualizar", team["id"], values={"members": 3})["members"] == 3
    assert call(parser, ctx, "equipos", "listar")["total"] == 1
    route = call(parser, ctx, "rutas", "agregar", values={"code": "R", "name": "Ruta"})
    assert route["is_route"]
    assert call(parser, ctx, "rutas", "listar")["total"] == 1
    call(parser, ctx, "rutas", "actualizar", route["id"], values={"name": "Ruta 2"})
    person = call(parser, ctx, "recursos", "agregar", values={"name": "Ana"})
    call(parser, ctx, "ausencias", "agregar", values={"resource_id": person["id"], "start": "2026-01-05", "end": "2026-01-06", "reason": "Permiso"})
    absences = call(parser, ctx, "ausencias", "listar", "--resource_id", person["id"])
    assert absences["total"] == 2
    key = absences["items"][0]["id"]
    call(parser, ctx, "ausencias", "actualizar", key, values={"hours": 4})
    assert call(parser, ctx, "ausencias", "ver", key)["hours"] == 4
    assert call(parser, ctx, "ausencias", "eliminar", key)["deleted"]
    a = activity(parser, ctx, planned_start="2026-01-05", planned_finish="2026-01-05")
    assert call(parser, ctx, "actividades", "actualizar", a["id"], values={"duration_days": 3})["planned_finish"] == "2026-01-07"
    call(parser, ctx, "rutas", "eliminar", route["id"])
    call(parser, ctx, "equipos", "eliminar", team["id"])


@pytest.mark.parametrize("example", ["Ejemplo_Proyecto_SE_220kV.xlsx", "Ejemplo_Plan_Mantenimiento.xlsx"])
def test_import_official_examples(parser, ctx, example):
    source = ROOT / "ejemplos" / example
    before = counts_hash(ctx)
    ctx.write = False
    simulated = call(parser, ctx, "importar", "excel", source, "--nuevo-proyecto")
    assert simulated["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert counts_hash(ctx) == before
    ctx.write = True
    result = call(parser, ctx, "importar", "excel", source)
    ctx.project = str(result["project"]["id"])
    assert call(parser, ctx, "actividades", "listar", "--limite", 1)["total"] > 0


def normalize_dump(document):
    tables = document["tables"]
    maps = {t: {row["id"]: i for i, row in enumerate(rows)} for t, rows in tables.items() if t != "baseline_activities"}
    normalized = {}
    for table, rows in tables.items():
        normalized[table] = []
        for source in rows:
            row = dict(source)
            row.pop("id", None)
            for field, target in REFERENCES.items():
                if row.get(field) is not None:
                    row[field] = maps[target][row[field]]
            if "team_ids" in row:
                row["team_ids"] = [maps["resources"][rid] for rid in row["team_ids"]]
            normalized[table].append(row)
    return normalized


def test_json_complete_roundtrip(parser, ctx):
    _roundtrip_setup(parser, ctx)
    path = ctx.db_path.parent / "dump.json"
    call(parser, ctx, "exportar", "json", "--a", path)
    document = json.loads(path.read_text(encoding="utf-8"))
    result = call(parser, ctx, "importar", "json", path)
    assert result["project"]["id"] != int(ctx.project)
    with closing(ctx.conn()) as conn:
        restored = dump_project(conn, result["project"]["id"])
    assert normalize_dump(restored) == normalize_dump(document)


def _roundtrip_setup(parser, ctx):
    """Fixture de intercambio con todas las referencias significativas."""
    cal = call(parser, ctx, "calendarios", "agregar", values={"name": "Propio", "exceptions": [{"date": "2026-01-06", "working": False, "reason": "Ausencia"}]})
    r = call(parser, ctx, "recursos", "agregar", values={"name": "Ana", "calendar_id": cal["id"], "skills": ["eléctrico"]})
    w = call(parser, ctx, "wbs", "agregar", values={"name": "Raíz", "code": "W"})
    child = call(parser, ctx, "wbs", "agregar", values={"name": "Hijo", "code": "W.1", "parent_id": w["id"]})
    task = call(parser, ctx, "periodicas", "agregar", values={"name": "PM", "code": "PM", "resource_id": r["id"]})
    a = activity(parser, ctx, wbs_id=child["id"], periodic_task_id=task["id"], calendar_id=cal["id"], checklist_codes=["X"], planned_start="2026-01-05", planned_finish="2026-01-05")
    b = activity(parser, ctx, "B")
    call(parser, ctx, "relaciones", "agregar", values={"pred_id": a["id"], "succ_id": b["id"]})
    call(parser, ctx, "asignaciones", "agregar", values={"activity_id": a["id"], "resource_id": r["id"]})
    bl = call(parser, ctx, "lineas-base", "agregar", values={"name": "LB"})
    call(parser, ctx, "actividades-base", "agregar", values={"baseline_id": bl["id"], "activity_id": a["id"], "start": "2026-01-05", "finish": "2026-01-05", "work_hours": 8, "cost": 2, "duration_days": 1})
    call(parser, ctx, "proyectos", "actualizar", values={"active_baseline_id": bl["id"]})
    call(parser, ctx, "avances", "agregar", values={"activity_id": a["id"], "resource_id": r["id"], "date": "2026-01-05", "percent_complete": 10, "actual_hours": 1})


def test_json_invalid_reference_atomic(parser, ctx):
    activity(parser, ctx)
    path = ctx.db_path.parent / "invalid.json"
    call(parser, ctx, "exportar", "json", "--a", path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["tables"]["activities"][0]["calendar_id"] = 999999
    path.write_text(json.dumps(doc), encoding="utf-8")
    before = counts_hash(ctx)
    with pytest.raises(CliError):
        call(parser, ctx, "importar", "json", path)
    assert counts_hash(ctx) == before


def test_json_stdin_and_file(parser, ctx, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"name":"Desde stdin"}'))
    assert call(parser, ctx, "proyectos", "crear", "--archivo", "-")["name"] == "Desde stdin"
    path = ctx.db_path.parent / "row.json"
    path.write_text('{"code":"F","name":"Archivo"}', encoding="utf-8-sig")
    assert call(parser, ctx, "actividades", "agregar", "--archivo", path)["name"] == "Archivo"
    with pytest.raises(CliError) as exc:
        call(parser, ctx, "actividades", "agregar", "--json", '{"x":')
    assert exc.value.detail["errors"][0]["line"] == 1


@pytest.mark.parametrize("kind", ["proyecto", "mantenimiento", "inventario"])
def test_templates(parser, ctx, kind):
    path = ctx.db_path.parent / f"{kind}.xlsx"
    result = call(parser, ctx, "plantilla", kind, "--a", path)
    assert path.is_file() and result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_exports_and_demo(parser, ctx):
    activity(parser, ctx, planned_start="2026-01-05", planned_finish="2026-01-05")
    before = counts_hash(ctx)
    for kind in ("xlsx", "gantt"):
        path = ctx.db_path.parent / f"{kind}.xlsx"
        call(parser, ctx, "exportar", kind, "--a", path)
        assert path.read_bytes().startswith(b"PK")
    assert counts_hash(ctx) == before
    ctx.write = False
    call(parser, ctx, "demo", "crear", "mantenimiento", "--n", 3)
    assert counts_hash(ctx) == before


def test_case_json_assumptions_and_detection(parser, ctx):
    path = ctx.db_path.parent / "case.json"
    path.write_text(json.dumps({"time_model": {"horizon_slots": 10, "slot_hours": 8}, "activities": [
        {"id": "A", "name": "A", "duration_slots": 1, "earliest_start_slot": 0, "latest_finish_slot": 10}],
        "personnel": [], "resources": []}), encoding="utf-8")
    assert call(parser, ctx, "importar", "detectar", path)["detections"]
    before = counts_hash(ctx)
    with pytest.raises(CliError) as exc:
        call(parser, ctx, "importar", "json", path)
    assert "preview" in exc.value.detail
    assert counts_hash(ctx) == before
    preview = call(parser, ctx, "importar", "json", path, "--previsualizar")
    assert preview["preview"]["supuestos"]
    result = call(parser, ctx, "importar", "json", path, "--aceptar-supuestos", "--aceptar-advertencias")
    assert result["project"]["id"] and result["assumptions"]


def test_no_qt_imported():
    import sys
    import subprocess
    # pytest-qt importa Qt al arrancar; comprobar el producto en un intérprete limpio.
    result = subprocess.run([sys.executable, "-B", "-c",
        "import sys, argparse; from ganttier.cli.comandos import modelo; "
        "modelo.register(argparse.ArgumentParser().add_subparsers()); "
        "from ganttier.api import schemas; "
        "assert not any(n.startswith('PySide6') for n in sys.modules)"],
        cwd=ROOT / "backend", capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_inventory_import_and_detection(parser, ctx):
    from ganttier.importadores.inventario import exportar_inventario
    path = ctx.db_path.parent / "inventory.xlsx"
    path.write_bytes(exportar_inventario([{"planta": "PL", "bloque": "B1", "objeto": "Panel", "cantidad": 12,
                                         "unidad": "un", "detalle": "Prueba", "fuente": "Sintético"}]))
    detections = call(parser, ctx, "importar", "detectar", path)
    assert any(d.get("formato") == "inventario_equipos" for d in detections["detections"])
    result = call(parser, ctx, "importar", "inventario", path, "--aceptar-supuestos", "--aceptar-advertencias")
    ctx.project = str(result["project"]["id"])
    assert call(parser, ctx, "actividades", "listar")["total"] == 0
    assert result["source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_global_resource_roundtrip(parser, ctx):
    ctx.project = None
    resource = call(parser, ctx, "recursos", "agregar", values={"name": "Global", "skills": ["X"]})
    ctx.project = "1"
    a = activity(parser, ctx)
    call(parser, ctx, "asignaciones", "agregar", values={"activity_id": a["id"], "resource_id": resource["id"]})
    path = ctx.db_path.parent / "global.json"
    call(parser, ctx, "exportar", "json", "--a", path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    result = call(parser, ctx, "importar", "json", path)
    with closing(ctx.conn()) as conn:
        assert normalize_dump(dump_project(conn, result["project"]["id"])) == normalize_dump(doc)


def test_filters_reject_injection_and_projection(parser, ctx):
    a = activity(parser, ctx)
    assert call(parser, ctx, "actividades", "listar", "--code", "' OR 1=1 --")["total"] == 0
    with pytest.raises(CliError):
        call(parser, ctx, "actividades", "listar", "--campos", "id,(SELECT 1)")
    with pytest.raises(CliError):
        call(parser, ctx, "actividades", "listar", "--desde", -1)
    assert call(parser, ctx, "actividades", "ver", a["id"])["code"] == "A"
