"""MODO IMPORTAR / EXPORTAR — prueba de todas sus funciones a través de la API.

Cubre: descarga de plantillas (hojas, validaciones y ejemplos), creación de proyectos desde ambas
plantillas y desde los libros de ejemplo, carga de una LISTA DE TAREAS escrita en Excel sin fechas y
generación posterior de un programa factible, validación en seco (no escribe), detección de errores por
hoja/fila/campo (códigos duplicados, referencias inexistentes, fechas y números inválidos, ciclos),
combinar vs reemplazar, exportación e ida y vuelta exacta, avance de mantenimiento con ocurrencias
inexistentes y rendimiento de importación masiva.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import openpyxl
import pytest

from .conftest import check_project_schedule, err, ok

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ROOT = Path(__file__).resolve().parents[3]


def _upload(content: bytes, name="libro.xlsx"):
    return {"file": (name, content, XLSX)}


def _book(client, kind: str) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(ok(client.get(f"/api/templates/{kind}"))))


def _clear(ws):
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)


def _save(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _set_project(wb, **values):
    ws = wb["Proyecto"]
    keys = {ws.cell(r, 1).value: r for r in range(2, ws.max_row + 1)}
    for k, v in values.items():
        ws.cell(keys[k], 2).value = v


def _task_list_book(client, n=40) -> bytes:
    """Libro de proyecto con SOLO la lista de tareas (sin fechas planificadas)."""
    wb = _book(client, "proyecto")
    _set_project(wb, Nombre="Lista desde Excel", Modo="proyecto", Fecha_Inicio="2027-03-01", Fecha_Fin=None, Fecha_Corte=None)
    for name in ("Actividades", "Relaciones", "Asignaciones", "Avance", "Recursos", "WBS"):
        _clear(wb[name])
    wb["WBS"].append(["P", "Proyecto", None, 0])
    wb["Recursos"].append(["Cuadrilla montaje", "cuadrilla", "montaje", 8, 2, None, 20000, 30000, 0, None, "SI"])
    wb["Recursos"].append(["Cuadrilla civil", "cuadrilla", "civil", 8, 2, None, 18000, 27000, 0, None, "SI"])
    hdr = [c.value for c in wb["Actividades"][1]]
    for i in range(n):
        row = {"Codigo": f"X{i:03d}", "Nombre": f"Tarea {i}", "Codigo_WBS": "P", "Duracion_Dias": 1 + i % 6,
               "HH": (1 + i % 6) * 8, "Habilidad_Requerida": "montaje" if i % 2 else "civil", "Dotacion": 1}
        wb["Actividades"].append([row.get(h) for h in hdr])
    for i in range(1, n):
        if i % 3:
            wb["Relaciones"].append([f"X{i - 1:03d}", f"X{i:03d}", "FS", 0])
    return _save(wb)


def test_plantillas_hojas_validaciones_y_ejemplos(client):
    for kind, sheets in (("proyecto", {"Instrucciones", "Proyecto", "Calendarios", "Recursos", "WBS", "Actividades", "Relaciones", "Asignaciones", "Avance"}),
                         ("mantenimiento", {"Instrucciones", "Proyecto", "Calendarios", "Recursos", "Plan_Maestro", "Avance"})):
        wb = _book(client, kind)
        assert sheets <= set(wb.sheetnames), (kind, wb.sheetnames)
        main = wb["Actividades" if kind == "proyecto" else "Plan_Maestro"]
        assert main.max_row >= 2, "la plantilla debe traer ejemplos"
        assert len(main.data_validations.dataValidation) >= 5, "faltan listas desplegables/validaciones"
        assert main.freeze_panes is not None


@pytest.mark.parametrize("kind", ["proyecto", "mantenimiento"])
def test_crear_proyecto_desde_plantilla(client, kind):
    p = ok(client.post("/api/import/new-project", files=_upload(ok(client.get(f"/api/templates/{kind}")))))
    assert p["mode"] == kind
    assert ok(client.get(f"/api/projects/{p['id']}"))["counts"]["activities"] > 0


@pytest.mark.parametrize("name", ["Ejemplo_Proyecto_SE_220kV.xlsx", "Ejemplo_Plan_Mantenimiento.xlsx"])
def test_crear_proyecto_desde_ejemplos(client, name):
    path = ROOT / "ejemplos" / name
    if not path.exists():
        pytest.skip("ejemplo no incluido")
    p = ok(client.post("/api/import/new-project", files=_upload(path.read_bytes(), name)))
    counts = ok(client.get(f"/api/projects/{p['id']}"))["counts"]
    assert counts["activities"] > 50


def test_lista_de_tareas_en_excel_y_programa_factible(client):
    p = ok(client.post("/api/import/new-project", files=_upload(_task_list_book(client))))
    pid = p["id"]
    acts = ok(client.get(f"/api/projects/{pid}/activities", params={"limit": 1000}))
    assert len(acts) == 40 and all(a["planned_start"] is None for a in acts)
    res = ok(client.post(f"/api/projects/{pid}/schedule/solve", json={"weights": {"makespan": 1, "level": 0.3},
                                                                       "options": {"respect_resource_limits": True, "allow_overtime": False}, "apply": True}))
    assert res["feasible"] is True, res
    cid = ok(client.get(f"/api/projects/{pid}"))["calendar_id"]
    assert check_project_schedule(client, pid, cid, capacity_per_skill={"montaje": 16, "civil": 16}) == []


def test_validacion_en_seco_y_errores_por_fila(client):
    target = ok(client.post("/api/projects", json={"name": "Destino", "mode": "proyecto", "start_date": "2027-03-01"}))
    good = _task_list_book(client, n=10)
    dry = ok(client.post(f"/api/projects/{target['id']}/import", params={"dry_run": "true"}, files=_upload(good)))
    assert not [e for e in dry["errors"] if e["severity"] == "error"]
    assert ok(client.get(f"/api/projects/{target['id']}/activities")) == []          # en seco no escribe
    wb = openpyxl.load_workbook(io.BytesIO(good))
    ws = wb["Actividades"]
    hdr = [c.value for c in ws[1]]
    ws.cell(3, hdr.index("Codigo") + 1).value = "X000"                              # código duplicado
    ws.cell(4, hdr.index("Duracion_Dias") + 1).value = "abc"                        # número inválido
    ws.cell(5, hdr.index("Codigo_WBS") + 1).value = "NO_EXISTE"                     # WBS inexistente
    ws.cell(6, hdr.index("Ventana_Inicio") + 1).value = "31-02-2027"                # fecha imposible
    wb["Relaciones"].append(["X008", "X007", "FS", 0])                              # cierra un ciclo X007→X008→X007
    wb["Relaciones"].append(["X007", "NOPE", "FS", 0])                              # sucesora inexistente
    bad = _save(wb)
    dry = ok(client.post(f"/api/projects/{target['id']}/import", params={"dry_run": "true"}, files=_upload(bad)))
    errors = [e for e in dry["errors"] if e["severity"] == "error"]
    assert len(errors) >= 5, errors
    for e in errors:
        assert e["sheet"] and e["message"]
    sheets_rows = {(e["sheet"], e["row"]) for e in errors}
    assert ("Actividades", 3) in sheets_rows and ("Actividades", 4) in sheets_rows
    assert any("ciclo" in e["message"].lower() for e in errors), errors
    body = err(client.post(f"/api/projects/{target['id']}/import", files=_upload(bad)), 422)
    assert body["errors"]
    assert ok(client.get(f"/api/projects/{target['id']}/activities")) == []


def test_combinar_y_reemplazar(client):
    target = ok(client.post("/api/projects", json={"name": "Merge", "mode": "proyecto", "start_date": "2027-03-01"}))
    pid = target["id"]
    ok(client.post(f"/api/projects/{pid}/activities", json={"code": "PREVIA", "name": "Existente", "duration_days": 1}))
    ok(client.post(f"/api/projects/{pid}/import", params={"mode": "merge"}, files=_upload(_task_list_book(client, n=10))))
    codes = {a["code"] for a in ok(client.get(f"/api/projects/{pid}/activities"))}
    assert "PREVIA" in codes and len(codes) == 11
    ok(client.post(f"/api/projects/{pid}/import", params={"mode": "replace"}, files=_upload(_task_list_book(client, n=10))))
    codes = {a["code"] for a in ok(client.get(f"/api/projects/{pid}/activities"))}
    assert "PREVIA" not in codes and len(codes) == 10


def test_exportar_e_ida_y_vuelta_exacta(client):
    p = ok(client.post("/api/demo/proyecto"))
    pid = p["id"]
    ok(client.post(f"/api/projects/{pid}/progress", json=[{"activity_id": ok(client.get(f"/api/projects/{pid}/activities"))[0]["id"],
                                                            "date": "2027-01-22", "percent_complete": 30, "actual_hours": 5}]))
    x = ok(client.get(f"/api/projects/{pid}/export.xlsx"))
    assert x[:2] == b"PK"
    q = ok(client.post("/api/import/new-project", files=_upload(x, "export.xlsx")))
    def snap(i):
        acts = sorted(ok(client.get(f"/api/projects/{i}/activities", params={"limit": 10000})), key=lambda a: a["code"])
        keep = ("code", "name", "duration_days", "work_hours", "cost_budget", "macro_month", "window_start", "window_end",
                "constraint_type", "constraint_date", "required_skill", "crew_size", "location", "equipment", "requires_outage")
        rels = sorted((r["type"], r["lag_days"]) for r in ok(client.get(f"/api/projects/{i}/relations")))
        res = sorted((r["name"], tuple(r["skills"]), r["capacity_hours_day"], r["max_units"]) for r in ok(client.get("/api/resources", params={"project_id": i})))
        return [tuple(a[k] for k in keep) for a in acts], rels, res
    assert snap(pid) == snap(q["id"])
    assert len(ok(client.get(f"/api/projects/{q['id']}/progress"))) == len(ok(client.get(f"/api/projects/{pid}/progress")))


def test_avance_de_mantenimiento_con_ocurrencia_inexistente(client):
    wb = _book(client, "mantenimiento")
    ws = wb["Avance"]
    _clear(ws)
    ws.append(["NO-EXISTE-0001", "2026-02-01", 50, 4, None, None, None, None, None])
    body = err(client.post("/api/import/new-project", files=_upload(_save(wb))), 422)
    assert "no existe" in str(body).lower() or "ocurrencia" in str(body).lower()


@pytest.mark.slow
def test_importacion_masiva_100000(client):
    wb = _book(client, "mantenimiento")
    for s in ("Plan_Maestro", "Avance"):
        _clear(wb[s])
    hdr = [c.value for c in wb["Plan_Maestro"][1]]
    for i in range(100000):
        row = {"Codigo": f"T{i:06d}", "Nombre": "Inspección", "Frecuencia": "A", "Intervalo": 1, "Duracion_Dias": 1,
               "HH_por_Ejecucion": 2, "Dotacion": 1, "Habilidad": "SSEE", "Primera_Fecha": "2026-03-02"}
        wb["Plan_Maestro"].append([row.get(h) for h in hdr])
    data = _save(wb)
    t = time.perf_counter()
    target = ok(client.post("/api/projects", json={"name": "Masivo", "mode": "mantenimiento", "start_date": "2026-01-01", "end_date": "2026-12-31"}))
    ok(client.post(f"/api/projects/{target['id']}/import", params={"mode": "replace"}, files=_upload(data)))
    assert time.perf_counter() - t < 90
    assert len(ok(client.get(f"/api/projects/{target['id']}/periodic-tasks"))) == 100000
