"""Shared web helpers: template rendering, flash messages, CSRF, safe redirects, 'do this action' wrapper."""
from __future__ import annotations

import hmac
import json
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from decimal import Decimal
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import config, db, timeutil
from .menu import menu_for

from .jinja_env import env, TEMPLATE_DIR  # noqa: F401


# ---------------------------------------------------------------- flash / csrf / redirect
def flash(request: Request, msg: str, kind: str = "ok") -> None:
    request.session.setdefault("_flash", []).append([kind, msg])


def csrf_token(request: Request) -> str:
    t = request.session.get("csrf")
    if not t:
        t = secrets.token_urlsafe(24)
        request.session["csrf"] = t
    return t


async def csrf_protect(request: Request) -> None:
    """Router dependency: every POST must carry the session's CSRF token (form field or X-CSRF-Token header)."""
    if request.method != "POST":
        return
    sent = request.headers.get("x-csrf-token")
    if not sent:
        form = await request.form()
        sent = form.get("csrf_token")
    if not sent or not hmac.compare_digest(str(sent), request.session.get("csrf", "")):
        raise HTTPException(status_code=403, detail="Form expired. Go back, refresh the page and try again.")


def safe_next(request: Request, nxt: str | None, default: str = "/") -> str:
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    ref = request.headers.get("referer", "")
    if ref:
        from urllib.parse import urlparse
        u = urlparse(ref)
        if u.path.startswith("/"):
            return u.path + (("?" + u.query) if u.query else "")
    return default


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ---------------------------------------------------------------- settings cache (clinic name etc.)
_cache: dict[str, tuple[float, object]] = {}


def clinic_name() -> str:
    hit = _cache.get("name")
    if hit and time.time() - hit[0] < 60:
        return str(hit[1])
    try:
        v = db.setting(None, "clinic_name", None) if not db.STUB else None
    except Exception:
        v = None
    v = v or "Clinic"
    _cache["name"] = (time.time(), v)
    return v


# ---------------------------------------------------------------- render
def render(request: Request, template: str, actor: db.Actor | None = None, status: int = 200, **ctx) -> HTMLResponse:
    flashes = request.session.pop("_flash", []) if "session" in request.scope else []
    banner = None
    if actor and actor.role == "OWNER":
        try:
            from .data import owner as owner_data
            banner = owner_data.banner(actor)
        except Exception:
            banner = None
    base = {"request": request, "user": actor, "menu": menu_for(actor.role) if actor else [],
            "flashes": flashes, "csrf": csrf_token(request), "clinic_name": clinic_name(), "banner": banner,
            "path": request.url.path, "stub": db.STUB, "config": config}
    base.update(ctx)
    html = env.get_template(template).render(**base)
    return HTMLResponse(html, status_code=status)


def error_page(request: Request, actor, message: str, status: int = 400, back: str | None = None):
    return render(request, "error.html", actor, status=status, message=message, back=back, status_code=status)


# ---------------------------------------------------------------- the standard POST wrapper
def do(request: Request, fn: Callable, *args, ok: str = "Done.", nxt: str | None = None, default: str = "/", **kw):
    """Run a data-layer action; flash success or the readable rule error; redirect (POST-redirect-GET)."""
    target = safe_next(request, nxt, default)
    try:
        result = fn(*args, **kw)
        msg = ok.format(r=result) if isinstance(ok, str) and "{r" in ok else ok
        flash(request, msg, "ok")
    except db.RuleError as e:
        flash(request, str(e), "err")
    return redirect(target)


# ---------------------------------------------------------------- form helpers
async def formdata(request: Request):
    """Dependency: the parsed form (Starlette FormData; supports .get and .getlist)."""
    return await request.form()


def fs(form, key: str, default=None):
    v = form.get(key)
    if isinstance(v, str):
        v = v.strip()
        return v if v else default
    return default


def fi(form, key: str, default=None):
    v = fs(form, key)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def fd(form, key: str, default=None):
    v = fs(form, key)
    try:
        return Decimal(v) if v is not None else default
    except Exception:
        return default


def fdt(form, key: str):
    """datetime-local typed in clinic time -> UTC."""
    return timeutil.local_input_to_utc(fs(form, key))


def qi(request: Request, name: str, default=None):
    """Integer query parameter that tolerates an empty value (a blank <select> sends '?x=')."""
    v = request.query_params.get(name, "")
    try:
        return int(v) if v != "" else default
    except ValueError:
        return default
