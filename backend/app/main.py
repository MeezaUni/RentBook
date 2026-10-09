"""FastAPI application entrypoint.

In production the same process serves the built React app from `frontend/dist`,
so the browser talks to a single origin (no CORS needed, strict cookies work).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .errors import DomainError

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rentbook")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .migrate import upgrade_to_head
    from .db import SessionLocal
    from .services.seed import ensure_initial_data

    upgrade_to_head()
    with SessionLocal() as db:
        ensure_initial_data(db)
    log.info("Rentbook started (env=%s)", settings.env)
    yield


def create_app(run_lifespan: bool = True) -> FastAPI:
    app = FastAPI(
        title="Rentbook",
        version="1.0.0",
        lifespan=lifespan if run_lifespan else None,
        # Interactive API docs only in development.
        docs_url="/api/docs" if settings.env != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.env != "production" else None,
    )

    @app.exception_handler(DomainError)
    async def _domain(_req: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        # Report field locations and messages only; never echo the submitted values.
        problems = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg", "")}
                    for e in exc.errors()]
        msg = "; ".join(f"{p['field']}: {p['message']}" for p in problems[:5])
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error",
                                                                "message": msg or "Invalid input",
                                                                "details": {"problems": problems}}})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": "http_error", "message": str(exc.detail), "details": {}}})

    @app.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception):
        # Log the type and location only; request bodies and values are never logged.
        log.error("Unhandled %s on %s %s", type(exc).__name__, req.method, req.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"error": {"code": "server_error",
                                                                "message": "Something went wrong. Nothing was saved.",
                                                                "details": {}}})

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; object-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; "
            "form-action 'self'",
        )
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        return response

    from .routers import (
        auth, bank, cash, dashboard, documents, maintenance, misc, properties, rent, reports,
    )

    app.include_router(auth.public)
    for r in (auth.router, properties.router, rent.router, documents.router, cash.router, maintenance.router,
              bank.router, dashboard.router, reports.router, misc.router):
        app.include_router(r)

    _mount_frontend(app, settings.frontend_dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        @app.get("/", include_in_schema=False)
        async def _no_frontend():
            return JSONResponse({"message": "API is running. Build the frontend (see README) or use the Vite dev "
                                            "server at http://localhost:5173."})
        return

    root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Not found",
                                                                    "details": {}}})
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and root in candidate.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in candidate.as_posix() else {}
            return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
