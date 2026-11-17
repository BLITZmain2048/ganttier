"""Aplicación local FastAPI, frontend SPA y arranque en Windows."""
from __future__ import annotations

import hmac
import logging
import json
import socket
import sqlite3
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.request import urlopen

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import ValidationError

from . import db, repo, services
from .api import router
from .autoreports import scheduler


def _validation_response(exc) -> JSONResponse:
    errors = []
    translations = {"missing": "Campo obligatorio.", "int_parsing": "Debe ser un número entero.", "float_parsing": "Debe ser un número.", "date_from_datetime_parsing": "Debe ser una fecha YYYY-MM-DD.", "extra_forbidden": "Campo no permitido.", "literal_error": "Valor no permitido.", "finite_number": "Debe ser un número finito."}
    for error in exc.errors():
        location = error.get("loc", ())
        message = str(error.get("ctx", {}).get("error", "")) or translations.get(error["type"], "Valor inválido para este campo.")
        errors.append({"row": next((x for x in location if isinstance(x, int)), None), "field": ".".join(map(str, location)), "message": message})
    return JSONResponse(status_code=422, content={"detail": "Revise los datos enviados.", "errors": errors})


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1", "testserver"}
TOKEN_HEADER = "X-Ganttier-Token"


def _allowed_hosts() -> set[str]:
    import os
    extra = {h.strip().lower() for h in os.environ.get("GANTTIER_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return LOCAL_HOSTS | extra


def create_app(token: str | None = None, instance: str | None = None) -> FastAPI:
    """Construye la app sin abrir la base ni importar motores al importar el módulo.

    ``token``: si se entrega, toda ruta /api/* (salvo /api/health) exige la cabecera X-Ganttier-Token.
    Siempre se valida el encabezado Host contra 127.0.0.1/localhost (protección contra DNS rebinding);
    ``GANTTIER_ALLOWED_HOSTS`` agrega nombres para un servidor explícitamente compartido.
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from . import backups
        try:
            restored = backups.apply_pending_restore()  # antes de abrir cualquier conexión
            if restored:
                logging.getLogger(__name__).warning("Base restaurada desde el respaldo %s", restored)
        except Exception:
            logging.getLogger(__name__).exception("No se pudo aplicar la restauración programada")
        conn = db.connect()
        try:
            db.initialize(conn)
        finally:
            conn.close()
        # Emisión automática de informes (al iniciar y cada hora) mientras el servidor está activo.
        with scheduler(db.connect):
            yield

    application = FastAPI(title="Ganttier", version="1.0.0", lifespan=lifespan)
    application.state.instance = instance
    application.add_middleware(GZipMiddleware, minimum_size=1000)
    allowed = _allowed_hosts()

    @application.middleware("http")
    async def local_security(request: Request, call_next):
        host = (request.headers.get("host") or "").lower()
        hostname = host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]
        if hostname not in allowed:
            return JSONResponse(status_code=400, content={"detail": "Host no permitido para la API local de Ganttier."})
        if token and request.url.path.startswith("/api/") and request.url.path != "/api/health":
            supplied = request.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse(status_code=401, content={"detail": "Falta la credencial de sesión de Ganttier."})
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    @application.exception_handler(ValidationError)
    async def validation_error(request: Request, exc):
        return _validation_response(exc)

    @application.exception_handler(services.ModuleUnavailable)
    async def module_error(request: Request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @application.exception_handler(ImportError)
    async def dependency_error(request: Request, exc):
        return JSONResponse(status_code=503, content={"detail": f"Falta una dependencia para esta operación: {exc.name or str(exc)}. Ejecute instalar.bat."})

    @application.exception_handler(LookupError)
    async def missing_error(request: Request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(repo.ProtectedDeleteError)
    async def protected_delete(request: Request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc), "protected": True, "history": exc.history})

    @application.exception_handler(ValueError)
    async def input_error(request: Request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request: Request, exc):
        return JSONResponse(status_code=409, content={"detail": "La operación duplica un registro, usa una referencia inválida o afecta datos relacionados."})

    @application.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, exc):
        logging.getLogger(__name__).exception("Error de acceso a SQLite", exc_info=exc)
        return JSONResponse(status_code=503, content={"detail": "No se pudo acceder a la base de datos. Reintente la operación."})

    application.include_router(router, prefix="/api")
    dist = db.ROOT / "frontend" / "dist"

    @application.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "No se encontró la ruta de API solicitada."})
        target = (dist / path).resolve()
        root = dist.resolve()
        if not target.is_relative_to(root):
            return JSONResponse(status_code=404, content={"detail": "Archivo no encontrado."})
        if target.is_file():
            return FileResponse(target)
        index = dist / "index.html"
        if index.is_file():
            if Path(path).suffix:
                return JSONResponse(status_code=404, content={"detail": "Archivo no encontrado."})
            return FileResponse(index)
        return HTMLResponse("<!doctype html><html lang='es'><meta charset='utf-8'><title>Ganttier</title><body><h1>Ganttier está iniciado</h1><p>La interfaz aún no está compilada. Genere frontend/dist desde el proyecto del frontend.</p><p>Puede consultar la <a href='/docs'>documentación de la API</a>.</p></body></html>")

    return application


app = create_app()


def _open_when_ready(url: str) -> None:
    """Abre la interfaz solo después de verificar salud, durante hasta 20 s."""
    deadline = time.monotonic() + 20
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            with urlopen(url + "/api/health", timeout=min(1, remaining)) as response:
                body = json.loads(response.read())
                if response.status == 200 and body.get("status") == "ok" and body.get("app") == "Ganttier":
                    webbrowser.open(url)
                    return
        except (OSError, ValueError):
            pass
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    logging.getLogger(__name__).warning("No se abrió el navegador: el servidor no respondió en 20 segundos (%s).", url)


def main() -> None:
    """Reserva el primer puerto libre local (8765–8775) y anuncia la URL real."""
    import uvicorn
    for port in range(8765, 8776):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(2048)
            listener.setblocking(False)
        except OSError:
            listener.close()
            continue
        url = f"http://127.0.0.1:{port}"
        print(f"Ganttier está disponible en {url}", flush=True)
        try:
            threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
            uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port)).run(sockets=[listener])
        finally:
            listener.close()
        return
    raise RuntimeError("No hay puertos disponibles entre 8765 y 8775. Cierre otra instancia e inténtelo de nuevo.")


if __name__ == "__main__":
    main()
