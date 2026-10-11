"""App factory. Routers in app/routes/*.py are discovered automatically (each module exposes `router`),
so adding a screen never requires editing this file.   Run from the repo root:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, db, routes
from .web import error_page, redirect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clinic")


def create_app() -> FastAPI:
    app = FastAPI(title="Clinic Management System", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=config.SESSION_HOURS * 3600,
                       same_site="lax", https_only=config.HTTPS_ONLY, session_cookie="clinic_session")
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.exception_handler(auth.NeedLogin)
    async def _need_login(request: Request, exc: auth.NeedLogin):
        from urllib.parse import quote
        if exc.nxt == "/choose-role":
            return redirect("/choose-role")
        return redirect("/login" if exc.nxt == "/" else f"/login?next={quote(exc.nxt)}")

    @app.exception_handler(auth.Forbidden)
    async def _forbidden(request: Request, exc: auth.Forbidden):
        return error_page(request, auth.actor_from_session(request), str(exc), 403, back="/")

    @app.exception_handler(db.NotReady)
    async def _notready(request: Request, exc: db.NotReady):
        return error_page(request, auth.actor_from_session(request), str(exc), 501)

    @app.exception_handler(db.RuleError)
    async def _rule(request: Request, exc: db.RuleError):
        return error_page(request, auth.actor_from_session(request), str(exc), 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        msg = {404: "Page not found."}.get(exc.status_code, str(exc.detail))
        return error_page(request, auth.actor_from_session(request), msg, exc.status_code, back="/")

    @app.exception_handler(Exception)
    async def _boom(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        msg = "Something went wrong."
        if config.DEBUG_ERRORS:
            msg += f" ({type(exc).__name__}: {exc})"
        return error_page(request, auth.actor_from_session(request), msg, 500)

    for m in sorted(pkgutil.iter_modules(routes.__path__), key=lambda x: x.name):
        mod = importlib.import_module(f"app.routes.{m.name}")
        if hasattr(mod, "router"):
            app.include_router(mod.router)
            log.info("router loaded: %s", m.name)
    return app


app = create_app()
