"""Intercambio del modelo: Excel nativo, importadores asistidos y volcado JSON v1.

El volcado ganttier.project/1 conserva todas las entidades de repo.ENTITIES del
proyecto y los calendarios y recursos globales visibles. Los ids son referencias
locales al documento y se remapean al importar un proyecto nuevo. No es un backup
de SQLite: corridas del solver, archivos de informes y bitácoras operativas no son
entidades de modelo y se conservan mediante el respaldo de la base, no este formato.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from .._base import CliError, Context, Result
from ._modelo_crud import _schema, model_error

FORMAT = "ganttier.project"
VERSION = 1
ORDER = ("calendars", "projects", "resources", "wbs", "periodic_tasks", "activities",
         "relations", "assignments", "baselines", "baseline_activities", "progress")
REFERENCES = {"calendar_id": "calendars", "project_id": "projects", "parent_id": "wbs",
              "wbs_id": "wbs", "resource_id": "resources", "team_id": "resources",
              "periodic_task_id": "periodic_tasks", "pred_id": "activities", "succ_id": "activities",
              "activity_id": "activities", "baseline_id": "baselines", "active_baseline_id": "baselines"}


def dump_project(conn: sqlite3.Connection, pid: int) -> dict:
    """Exporta entidades y referencias sin transformar por nombres ni perder campos."""
    from ... import repo
    project = repo.get(conn, "projects", pid)
    tables = {"projects": [repo.json_value(project)]}
    for table in ORDER:
        if table == "projects":
            continue
        if table == "baseline_activities":
            rows = [repo._decode(table, r) for r in conn.execute(
                "SELECT b.* FROM baseline_activities b JOIN baselines l ON l.id=b.baseline_id WHERE l.project_id=? ORDER BY b.baseline_id,b.activity_id", (pid,))]
        else:
            rows = repo.list_entities(conn, table, None if table == "calendars" else pid)
        tables[table] = repo.json_value(rows)
    # Sólo calendarios efectivamente referenciados (incluye ausencias personales).
    cids = {r["calendar_id"] for rows in tables.values() for r in rows if r.get("calendar_id") is not None}
    if not project.calendar_id:
        first = conn.execute("SELECT id FROM calendars ORDER BY id LIMIT 1").fetchone()
        if first:
            cids.add(first[0])
    tables["calendars"] = [r for r in tables["calendars"] if r["id"] in cids]
    return {"format": FORMAT, "version": VERSION, "tables": tables}


def restore_project(conn: sqlite3.Connection, document: dict) -> dict:
    """Importa un volcado validado en una transacción, remapeando cada referencia."""
    from ... import repo, domain
    from ...api.dependencies import validated_update
    if set(document) != {"format", "version", "tables"} or document.get("format") != FORMAT or document.get("version") != VERSION:
        raise ValueError("Formato o versión de volcado no compatible; se requiere ganttier.project versión 1.")
    tables = document["tables"]
    if not isinstance(tables, dict) or set(tables) != set(ORDER):
        raise ValueError("El volcado debe incluir exactamente todas las tablas del formato v1.")
    if not isinstance(tables["projects"], list) or len(tables["projects"]) != 1:
        raise ValueError("El volcado debe contener exactamente un proyecto.")
    # Validar todas las filas antes de remapear evita aceptar claves o tipos desconocidos.
    clean, maps = {}, {table: {} for table in ORDER}
    for table in ORDER:
        if not isinstance(tables[table], list):
            raise ValueError(f"La tabla {table} debe ser una lista.")
        clean[table] = []
        seen = set()
        for index, row in enumerate(tables[table]):
            try:
                data = _schema(table).model_validate(row).model_dump()
                key = (data["baseline_id"], data["activity_id"]) if table == "baseline_activities" else data["id"]
                if key in seen or (table != "baseline_activities" and (not isinstance(key, int) or key <= 0)):
                    raise ValueError("Identificador duplicado o inválido en el volcado.")
                seen.add(key)
                clean[table].append(data)
            except (ValueError, TypeError) as exc:
                error = model_error(exc, index=index)
                error.detail["table"] = table
                raise error from exc
    original = clean["projects"][0]
    original_pid = original["id"]
    active_baseline = original["active_baseline_id"]
    for table, rows in clean.items():
        if table not in {"projects", "calendars"}:
            for row in rows:
                if "project_id" in row and row["project_id"] != original_pid and not (table == "resources" and row["project_id"] is None):
                    raise ValueError(f"La tabla {table} contiene registros de otro proyecto.")

    def remap(row: dict) -> dict:
        row = dict(row)
        row.pop("id", None)
        for key, target in REFERENCES.items():
            if row.get(key) is not None:
                old = row[key]
                if old not in maps[target]:
                    raise ValueError(f"Referencia inexistente en {key}: {old}.")
                row[key] = maps[target][old]
        if "team_ids" in row:
            try:
                row["team_ids"] = [maps["resources"][rid] for rid in row["team_ids"]]
            except KeyError as exc:
                raise ValueError("Referencia inexistente en team_ids.") from exc
        return row

    with repo.transaction(conn):
        for table in ORDER:
            pending = list(clean[table])
            while pending:
                progressed = False
                remaining = []
                for source in pending:
                    if table == "wbs" and source.get("parent_id") is not None and source["parent_id"] not in maps["wbs"]:
                        remaining.append(source)
                        continue
                    data = dict(source)
                    if table == "projects":
                        data["active_baseline_id"] = None
                    values = remap(data)
                    if table == "calendars":
                        matches = repo.list_entities(conn, table, filters={"name": values["name"]})
                        if matches:
                            existing = repo.json_value(matches[0])
                            existing.pop("id")
                            if existing != repo.json_value(values):
                                # Nunca sobrescribir un calendario compartido de otro proyecto.
                                base, n = values["name"], 1
                                while repo.list_entities(conn, table, filters={"name": values["name"]}):
                                    values["name"] = f"{base} (importado {n})"
                                    n += 1
                            else:
                                maps[table][source["id"]] = matches[0].id
                                progressed = True
                                continue
                    if table == "resources" and values.get("project_id") is None:
                        # Reusar un recurso global idéntico evita duplicarlo en una ida y vuelta.
                        identical = next((r for r in repo.list_entities(conn, table, filters={"name": values["name"]})
                                          if r.project_id is None and {k: v for k, v in repo.json_value(r).items() if k != "id"} == repo.json_value(values)), None)
                        if identical is not None:
                            maps[table][source["id"]] = identical.id
                            progressed = True
                            continue
                    if table == "baseline_activities":
                        bid, aid = values["baseline_id"], values["activity_id"]
                        if repo.get(conn, "baselines", bid).project_id != repo.get(conn, "activities", aid).project_id:
                            raise ValueError("La línea base y actividad pertenecen a proyectos distintos.")
                        repo.save_baseline_activities(conn, [domain.BaselineActivity(**values)])
                    else:
                        obj = repo.create(conn, table, values)
                        maps[table][source["id"]] = obj.id
                    progressed = True
                pending = remaining
                if not progressed:
                    raise ValueError("La jerarquía WBS contiene referencias inexistentes o ciclos.")
        pid = maps["projects"][original_pid]
        if active_baseline is not None:
            if active_baseline not in maps["baselines"]:
                raise ValueError("Referencia inexistente en active_baseline_id.")
            validated_update(conn, "projects", pid, {"active_baseline_id": maps["baselines"][active_baseline]})
        return {"project": repo.json_value(repo.get(conn, "projects", pid)),
                "counts": {t: len(rows) for t, rows in clean.items()}, "id_map": maps}


def _source(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path.resolve()), "sha256": digest.hexdigest()}


def import_handler(args: Any, ctx: Context) -> Result:
    """Importa por los mismos lectores y servicios que la API y declara su fuente."""
    from ... import repo, services
    path = Path(args.source).resolve()
    try:
        source = _source(path)
        if path.suffix.lower() == ".pdf":
            from importlib.util import find_spec
            if not any(find_spec(name) for name in ("pdfplumber", "pypdf")):
                raise CliError("dependencia_faltante", "Importar PDF sin Qt requiere pdfplumber o pypdf; use la plantilla Excel de inventario.", exit_code=6)
        if args.kind == "detectar":
            from ...importadores.servicio import detectar
            detections = detectar(path)
            if path.suffix.lower() == ".json":
                doc = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(doc, dict) and doc.get("format") == FORMAT:
                    detections.insert(0, {"format": FORMAT, "version": doc.get("version"), "score": 1})
            if path.suffix.lower() == ".xlsx" and not detections:
                from ...excel_io import parse_workbook
                bundle = parse_workbook(path)
                if not bundle.has_errors:
                    detections.append({"format": "ganttier.excel", "mode": bundle.mode, "score": 1})
            return Result({"source": source, "detections": detections})
        doc = json.loads(path.read_text(encoding="utf-8-sig")) if args.kind == "json" else None
        with ctx.mutation(f"CLI importar {args.kind}") as conn, repo.transaction(conn):
            if isinstance(doc, dict) and doc.get("format") == FORMAT:
                result = restore_project(conn, doc)
                result["assumptions"] = []
            elif args.kind == "excel":
                from ...excel_io import parse_workbook
                from ...api.files import _save_bundle, _progress_sources, _import_result, ImportValidationError
                pid = ctx.require_project(conn) if args.en_proyecto else None
                mode = repo.get(conn, "projects", pid).mode if pid is not None else args.modo
                bundle = parse_workbook(path, mode)
                _progress_sources(path, bundle)
                if bundle.has_errors:
                    raise CliError("datos_invalidos", "El libro contiene errores de validación.", detail=_import_result(bundle))
                try:
                    if pid is None:
                        project = services.create_project_from_bundle(conn, bundle, _save_bundle)
                    else:
                        _save_bundle(conn, pid, bundle, args.mode)
                        services.sync_progress(conn, pid)
                        project = repo.get(conn, "projects", pid)
                except ImportValidationError as exc:
                    raise CliError("datos_invalidos", str(exc), detail=_import_result(bundle)) from exc
                result = dict(project=repo.json_value(project), **_import_result(bundle), assumptions=[])
            else:
                from ...importadores.servicio import previsualizar, confirmar
                profile = json.loads(Path(args.perfil).read_text(encoding="utf-8-sig")) if args.perfil else None
                assumptions = json.loads(args.supuestos) if args.supuestos else {}
                if args.supuestos_archivo:
                    assumptions = json.loads(Path(args.supuestos_archivo).read_text(encoding="utf-8-sig"))
                draft = previsualizar(path, profile, assumptions)
                expected = {"json": "caso_json", "inventario": "inventario_equipos"}.get(args.kind)
                if expected and draft.perfil.formato != expected:
                    raise ValueError(f"El archivo no corresponde al formato {expected}.")
                if args.previsualizar:
                    return Result({"source": source, "preview": draft.to_dict()})
                if not args.aceptar_supuestos or (draft.advertencias and not args.aceptar_advertencias) or (draft.modelo.get("requiere_revision") and not args.aceptar_revision_inventario):
                    raise CliError("datos_invalidos", "Revise y acepte los supuestos y advertencias del importador.",
                                   detail={"source": source, "preview": draft.to_dict()},
                                   hint="Use --previsualizar; después --aceptar-supuestos, --aceptar-advertencias y, para PDF, --aceptar-revision-inventario.")
                result = confirmar(conn, path, draft.perfil, assumptions, nombre=args.nombre or path.stem,
                                   aceptar_supuestos=args.aceptar_supuestos, aceptar_advertencias=args.aceptar_advertencias,
                                   aceptar_revision_inventario=args.aceptar_revision_inventario)
                result = {"project": result["proyecto"], "summary": result["resumen"],
                          "warnings": result["advertencias"], "assumptions": result["supuestos"]}
            if _source(path)["sha256"] != source["sha256"]:
                raise ValueError("La fuente cambió durante la importación; se revirtió el proyecto.")
            result["source"] = source
        return Result(result)
    except (ValueError, LookupError, sqlite3.IntegrityError, CliError) as exc:
        raise model_error(exc) from exc
    except OSError as exc:
        raise CliError("datos_invalidos", f"No se pudo leer la fuente: {exc}") from exc


def export_handler(args: Any, ctx: Context) -> Result:
    """Exporta sin modificar la base, reemplazando el archivo sólo al terminar."""
    from ... import repo
    path = Path(args.a).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{uuid4().hex}{path.suffix}")
    try:
        with closing(ctx.conn()) as conn:
            pid = ctx.require_project(conn)
            if args.kind == "json":
                tmp.write_text(json.dumps(dump_project(conn, pid), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            elif args.kind == "xlsx":
                from ...excel_io import export_project
                export_project(repo.export_bundle(conn, pid, nominal=args.nominal), tmp)
            else:
                from ...excel_io.gantt_xlsx import export_gantt, parse_options
                options = parse_options(args.modo, args.agrupar, args.escala, args.desde, args.hasta, args.critica)
                export_gantt(conn, pid, tmp, options)
        tmp.replace(path)
        return Result({"file": str(path), "sha256": _source(path)["sha256"], "format": FORMAT if args.kind == "json" else args.kind})
    except (ValueError, LookupError, CliError) as exc:
        raise model_error(exc) from exc
    finally:
        tmp.unlink(missing_ok=True)


def template_handler(args: Any, ctx: Context) -> Result:
    """Genera una plantilla nativa en la ruta explícita del usuario."""
    path = Path(args.a).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{uuid4().hex}{path.suffix}")
    try:
        if args.kind == "inventario":
            from ...importadores.inventario import exportar_inventario
            tmp.write_bytes(exportar_inventario())
        else:
            from ...excel_io import build_project_template, build_maintenance_template
            (build_project_template if args.kind == "proyecto" else build_maintenance_template)(tmp)
        tmp.replace(path)
        return Result({"file": str(path), "sha256": _source(path)["sha256"]})
    finally:
        tmp.unlink(missing_ok=True)
