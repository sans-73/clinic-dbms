"""P5-1: login, logout, role (hat) choice, landing page, health check, owner diagnostics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from .. import auth, db, diagnostics
from ..menu import MENUS
from ..web import csrf_protect, flash, redirect, render, safe_next

router = APIRouter(dependencies=[Depends(csrf_protect)])

LANDING = {"OWNER": "/owner", "DOCTOR": "/doctor", "RECEPTIONIST": "/reception", "NURSE": "/nurse",
           "PHARMACY": "/pharmacy", "ACCOUNTANT": "/billing"}


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if auth.actor_from_session(request):
        return redirect("/")
    return render(request, "login.html", None, next=next)


@router.post("/login")
def login_submit(request: Request, identifier: str = Form(""), password: str = Form(""), next: str = Form("/")):
    try:
        info = auth.authenticate(identifier, password, request)
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect("/login")
    roles = list(info["roles"])
    if len(roles) == 1:
        auth.start_session(request, info, roles[0], identifier)
        return redirect(safe_next(request, next, LANDING.get(roles[0], "/")) if next != "/" else LANDING.get(roles[0], "/"))
    auth.start_session(request, info, None, identifier)
    request.session["after_login"] = next
    return redirect("/choose-role")


@router.get("/choose-role")
def choose_role_page(request: Request):
    roles = request.session.get("roles")
    if not roles:
        return redirect("/login")
    return render(request, "choose_role.html", None, roles=list(roles), name=request.session.get("name"))


@router.post("/choose-role")
def choose_role_submit(request: Request, role: str = Form("")):
    try:
        auth.set_role(request, role)
        auth._log_event("login_success", request.session.get("display_code", ""), request.session.get("staff_id"),
                        request.session["roles"].get(role), request)
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect("/choose-role")
    nxt = request.session.pop("after_login", "/")
    return redirect(safe_next(request, nxt, LANDING.get(role, "/")) if nxt != "/" else LANDING.get(role, "/"))


@router.post("/switch-role")
def switch_role(request: Request, role: str = Form("")):
    try:
        auth.set_role(request, role)
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect("/")
    return redirect(LANDING.get(role, "/"))


@router.post("/logout")
def logout(request: Request):
    auth.logout(request)
    return redirect("/login")


@router.get("/")
def home(request: Request, user: db.Actor = Depends(auth.require())):
    return redirect(LANDING.get(user.role, "/login"))


@router.get("/healthz")
def healthz():
    from fastapi.responses import JSONResponse
    if db.STUB:
        return JSONResponse({"ok": True, "db": "stub"})
    try:
        db.q(None, "SELECT 1")
        return JSONResponse({"ok": True, "db": "up"})
    except Exception as e:
        return JSONResponse({"ok": False, "db": str(e)}, status_code=503)


@router.get("/_diag")
def diag(request: Request, user: db.Actor = Depends(auth.require("OWNER"))):
    try:
        contract_rows = diagnostics.contract_report(user)
        schema_rows = diagnostics.schema_report(user)
        err = None
    except db.RuleError as e:
        contract_rows, schema_rows, err = [], [], str(e)
    return render(request, "diag.html", user, contract_rows=contract_rows, schema_rows=schema_rows, err=err)
