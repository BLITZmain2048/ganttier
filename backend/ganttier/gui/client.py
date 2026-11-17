"""Cliente tipado de la API REST de Ganttier para la GUI (y para scripts/pruebas).

Todas las respuestas son estructuras JSON (dict/list). Los errores HTTP se convierten en
``ApiError`` con el mensaje en español entregado por el servidor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class ApiError(Exception):
    """Error devuelto por la API (o de conexión)."""

    def __init__(self, status: int, message: str, errors: list[dict] | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.errors = errors or []
        self.body = body

    def detail_text(self, limit: int = 8) -> str:
        """Mensaje con las primeras filas de error (para diálogos)."""
        lines = [self.message]
        for e in self.errors[:limit]:
            where = " · ".join(str(x) for x in (e.get("sheet"), f"fila {e['row']}" if e.get("row") else None, e.get("field")) if x)
            lines.append(f"• {where + ': ' if where else ''}{e.get('message', '')}")
        if len(self.errors) > limit:
            lines.append(f"… y {len(self.errors) - limit} más.")
        return "\n".join(lines)


def _clean(params: dict | None) -> dict | None:
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None and v != ""}


class ApiClient:
    """Envoltorio síncrono de httpx; seguro para usar desde hilos de trabajo (una instancia por hilo o compartida)."""

    def __init__(self, base_url: str, timeout: float = 600.0, transport: httpx.BaseTransport | None = None,
                 token: str | None = None):
        self.base_url = base_url.rstrip("/")
        headers = {"X-Ganttier-Token": token} if token else None
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport, headers=headers)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ núcleo
    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None,
                files: dict | None = None, raw: bool = False, timeout: float | None = None) -> Any:
        try:
            extra = {"timeout": timeout} if timeout is not None else {}
            r = self._http.request(method, "/api" + path, params=_clean(params), json=json, files=files, **extra)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"No se pudo conectar con el servidor de Ganttier: {exc}") from exc
        if r.status_code >= 400:
            body: Any = None
            try:
                body = r.json()
            except ValueError:
                body = r.text
            message, errors = f"Error {r.status_code} del servidor", []
            if isinstance(body, dict):
                d = body.get("detail")
                if isinstance(d, str):
                    message = d
                elif isinstance(d, list):
                    message = "; ".join(str(x.get("msg", x)) for x in d)
                errors = body.get("errors") or []
            elif isinstance(body, str) and body:
                message = body[:300]
            raise ApiError(r.status_code, message, errors, body)
        if raw:
            return r.content
        if r.status_code == 204 or not r.content:
            return None
        if "application/json" in r.headers.get("content-type", ""):
            return r.json()
        return r.content

    def get(self, path, **kw): return self.request("GET", path, **kw)

    def gantt_xlsx(self, pid, **params):
        return self.get(f'/projects/{pid}/gantt.xlsx', params=params, raw=True)
    def post(self, path, json=None, **kw): return self.request("POST", path, json={} if json is None else json, **kw)
    def put(self, path, json, **kw): return self.request("PUT", path, json=json, **kw)
    def patch(self, path, json, **kw): return self.request("PATCH", path, json=json, **kw)
    def delete(self, path, **kw): return self.request("DELETE", path, **kw)

    # ------------------------------------------------------------------ general
    def health(self): return self.get("/health")
    def projects(self): return self.get("/projects")
    def project(self, pid): return self.get(f"/projects/{pid}")
    def create_project(self, data): return self.post("/projects", data)
    def update_project(self, pid, data): return self.put(f"/projects/{pid}", data)
    def delete_project(self, pid): return self.delete(f"/projects/{pid}")
    def duplicate_project(self, pid): return self.post(f"/projects/{pid}/duplicate")
    def demo(self, kind: str, n: int | None = None): return self.post(f"/demo/{kind}", params={"n": n})

    # ------------------------------------------------------------------ calendarios
    def calendars(self, timeout: float | None = None): return self.get("/calendars", timeout=timeout)
    def calendar(self, cid): return self.get(f"/calendars/{cid}")
    def create_calendar(self, data): return self.post("/calendars", data)
    def update_calendar(self, cid, data): return self.put(f"/calendars/{cid}", data)
    def delete_calendar(self, cid): return self.delete(f"/calendars/{cid}")
    def calendar_days(self, cid, start: str, end: str): return self.get(f"/calendars/{cid}/days", params={"start": start, "end": end})

    # ------------------------------------------------------------------ WBS / actividades / relaciones
    def wbs(self, pid): return self.get(f"/projects/{pid}/wbs")
    def create_wbs(self, pid, data): return self.post(f"/projects/{pid}/wbs", data)
    def update_wbs(self, wid, data): return self.put(f"/wbs/{wid}", data)
    def delete_wbs(self, wid): return self.delete(f"/wbs/{wid}")

    def activities(self, pid, **params): return self.get(f"/projects/{pid}/activities", params=params)
    def columnar(self, pid): return self.get(f"/projects/{pid}/activities/columnar")
    def activity(self, aid): return self.get(f"/activities/{aid}")
    def create_activities(self, pid, data): return self.post(f"/projects/{pid}/activities", data)
    def update_activity(self, aid, data): return self.put(f"/activities/{aid}", data)
    def delete_activity(self, aid, force: bool = False):
        return self.delete(f"/activities/{aid}", params={"force": "true"} if force else None)
    def activity_history(self, aid): return self.get(f"/activities/{aid}/history")
    def patch_activities(self, pid, patches: list[dict]): return self.patch(f"/projects/{pid}/activities", patches)

    def relations(self, pid): return self.get(f"/projects/{pid}/relations")
    def create_relation(self, pid, data): return self.post(f"/projects/{pid}/relations", data)
    def delete_relation(self, rid): return self.delete(f"/relations/{rid}")

    # ------------------------------------------------------------------ recursos
    def resources(self, project_id=None): return self.get("/resources", params={"project_id": project_id})
    def create_resource(self, data): return self.post("/resources", data)
    def update_resource(self, rid, data): return self.put(f"/resources/{rid}", data)
    def delete_resource(self, rid): return self.delete(f"/resources/{rid}")
    def assignments(self, pid, activity_id=None): return self.get(f"/projects/{pid}/assignments", params={"activity_id": activity_id})
    def create_assignment(self, pid, data): return self.post(f"/projects/{pid}/assignments", data)
    def update_assignment(self, asg_id, data): return self.put(f"/assignments/{asg_id}", data)
    def delete_assignment(self, asg_id): return self.delete(f"/assignments/{asg_id}")

    # ------------------------------------------------------------------ mantenimiento
    def periodic_tasks(self, pid): return self.get(f"/projects/{pid}/periodic-tasks")
    def create_periodic_task(self, pid, data): return self.post(f"/projects/{pid}/periodic-tasks", data)
    def update_periodic_task(self, tid, data): return self.put(f"/periodic-tasks/{tid}", data)
    def delete_periodic_task(self, tid, force: bool = False):
        return self.delete(f"/periodic-tasks/{tid}", params={"force": "true"} if force else None)
    def periodic_task_history(self, tid): return self.get(f"/periodic-tasks/{tid}/history")
    def generate_occurrences(self, pid, horizon_start: str, horizon_end: str, replace: bool = True):
        return self.post(f"/projects/{pid}/maintenance/generate", {"horizon_start": horizon_start, "horizon_end": horizon_end, "replace": replace})
    def outages(self, pid, date_from=None, date_to=None): return self.get(f"/projects/{pid}/outages", params={"from": date_from, "to": date_to})
    def outages_xlsx(self, pid, date_from=None, date_to=None) -> bytes:
        return self.get(f"/projects/{pid}/outages.xlsx", params={"from": date_from, "to": date_to}, raw=True)

    # ------------------------------------------------------------------ programación
    def cpm(self, pid): return self.post(f"/projects/{pid}/schedule/cpm")
    def optimize(self, pid, weights: dict, options: dict | None = None, apply: bool = False):
        return self.post(f"/projects/{pid}/schedule/optimize", {"weights": weights, "options": options or {}, "apply": apply})
    def runs(self, pid): return self.get(f"/projects/{pid}/schedule/runs")
    def apply_run(self, pid, run_id): return self.post(f"/projects/{pid}/schedule/runs/{run_id}/apply")
    def load(self, pid, group="skill", period="week", date_from=None, date_to=None):
        return self.get(f"/projects/{pid}/load", params={"group": group, "period": period, "from": date_from, "to": date_to})

    # ------------------------------------------------------------------ control
    def baselines(self, pid): return self.get(f"/projects/{pid}/baselines")
    def create_baseline(self, pid, name: str): return self.post(f"/projects/{pid}/baselines", {"name": name})
    def delete_baseline(self, bid): return self.delete(f"/baselines/{bid}")
    def activate_baseline(self, bid): return self.post(f"/baselines/{bid}/activate")
    def progress(self, pid): return self.get(f"/projects/{pid}/progress")
    def add_progress(self, pid, entries: list[dict]): return self.post(f"/projects/{pid}/progress", entries)
    def delete_progress(self, entry_id): return self.delete(f"/progress/{entry_id}")
    def set_data_date(self, pid, data_date: str): return self.put(f"/projects/{pid}/data-date", {"data_date": data_date})
    def scurve(self, pid, period="week", basis="hours"): return self.get(f"/projects/{pid}/control/scurve", params={"period": period, "basis": basis})
    def evm(self, pid, basis="hours"): return self.get(f"/projects/{pid}/control/evm", params={"basis": basis})
    def performance(self, pid, start: str, end: str): return self.get(f"/projects/{pid}/control/performance", params={"start": start, "end": end})

    # ------------------------------------------------------------------ informes
    def reports(self, pid, period_type: str, period_start: str, formats: list[str]):
        return self.post(f"/projects/{pid}/reports", {"period_type": period_type, "period_start": period_start, "formats": formats})
    def download(self, url: str) -> bytes:
        """Descarga un archivo por URL relativa de la API (``/api/...``)."""
        path = url[4:] if url.startswith("/api") else url
        return self.get(path, raw=True)
    def autoreports(self, pid): return self.get(f"/projects/{pid}/autoreports")
    def save_autoreports(self, pid, cfg: dict): return self.put(f"/projects/{pid}/autoreports", cfg)
    def run_autoreports(self, pid): return self.post(f"/projects/{pid}/autoreports/run")
    def autoreports_log(self, pid): return self.get(f"/projects/{pid}/autoreports/log")

    # ------------------------------------------------------------------ Excel
    def template(self, kind: str) -> bytes: return self.get(f"/templates/{kind}", raw=True)
    def export_xlsx(self, pid) -> bytes: return self.get(f"/projects/{pid}/export.xlsx", raw=True)
    def import_file(self, pid, path: str | Path, mode: str = "merge", dry_run: bool = False):
        p = Path(path)
        with p.open("rb") as fh:
            files = {"file": (p.name, fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            return self.request("POST", f"/projects/{pid}/import", params={"mode": mode, "dry_run": str(dry_run).lower()}, files=files)
    def import_new_project(self, path: str | Path):
        p = Path(path)
        with p.open("rb") as fh:
            files = {"file": (p.name, fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            return self.request("POST", "/import/new-project", files=files)
