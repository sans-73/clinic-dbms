"""P5-6 Owner: daily summary (red / amber / clean), approvals inbox (phone friendly), alerts with acknowledge, exception reports,
audit viewer (read-only), price list, settings, staff and roles, delegations, reconciliation pages."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request

from .. import auth, db, timeutil
from ..data import common, owner as O
from ..web import csrf_protect, do, flash, formdata, fd, fi, fs, qi, redirect, render, error_page

router = APIRouter(prefix="/owner", dependencies=[Depends(csrf_protect)])
OWN = auth.require("OWNER")


@router.get("")
def dashboard(request: Request, user: db.Actor = Depends(OWN)):
    return render(request, "owner/dashboard.html", user, s=O.summary(user), today=timeutil.fmt(timeutil.utcnow(), "%A, %d %B %Y"))


@router.post("/run-checks")
def run_checks(request: Request, user: db.Actor = Depends(OWN)):
    return do(request, O.run_checks, user, ok="Checks run.", default="/owner")


@router.get("/approvals")
def approvals(request: Request, user: db.Actor = Depends(OWN)):
    return render(request, "owner/approvals.html", user, pending=O.approvals(user, True), done=O.approvals(user, False, 15), refresh=45)


@router.post("/approvals/{aid}")
def decide(request: Request, aid: int, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    d = "APPROVED" if fs(f, "decision") == "approve" else "REJECTED"
    return do(request, O.decide, user, aid, d, fs(f, "note"), ok="Approved." if d == "APPROVED" else "Rejected.", default="/owner/approvals")


@router.get("/alerts")
def alerts(request: Request, all: int = 0, user: db.Actor = Depends(OWN)):
    return render(request, "owner/alerts.html", user, rows=O.alerts(user, not all), all=all)


@router.post("/alerts/{eid}/ack")
def ack(request: Request, eid: int, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    return do(request, O.ack_alert, user, eid, fs(f, "note"), ok="Acknowledged.", default="/owner/alerts")


@router.get("/reports")
def reports_index(request: Request, user: db.Actor = Depends(OWN)):
    return render(request, "owner/reports_index.html", user, reports=O.REPORTS)


@router.get("/reports/{key}")
def report(request: Request, key: str, days: int = 30, user: db.Actor = Depends(OWN)):
    if key not in O.REPORTS:
        return error_page(request, user, "Unknown report.", 404, "/owner/reports")
    days = min(max(days, 1), 365)
    rows = O.run_report(user, key, days)
    return render(request, "owner/report.html", user, key=key, title=O.REPORTS[key][0], desc=O.REPORTS[key][1], rows=rows, days=days,
                  cols=list(rows[0].keys()) if rows else [])


@router.get("/audit")
def audit(request: Request, entity: str = "", action: str = "", page: int = 0, user: db.Actor = Depends(OWN)):
    staff_id = qi(request, "staff_id")
    qp = request.query_params
    rows = O.audit(user, entity or None, staff_id, timeutil.parse_date(qp.get("from")), timeutil.parse_date(qp.get("to")), action or None, page)
    return render(request, "owner/audit.html", user, rows=rows, entity=entity, staff_id=staff_id, action=action, page=page,
                  entities=O.audit_entities(user), staff=O.staff(user), dfrom=qp.get("from", ""), dto=qp.get("to", ""))


@router.get("/prices")
def prices(request: Request, user: db.Actor = Depends(OWN)):
    return render(request, "owner/prices.html", user, rows=O.prices(user), today=date.today().isoformat())


@router.post("/prices")
def price_save(request: Request, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    return do(request, O.save_price, user, fi(f, "price_list_id"), item_code=fs(f, "item_code"), description=fs(f, "description"),
              category=fs(f, "category"), price=fd(f, "price"), effective_from=fs(f, "effective_from"),
              effective_to=fs(f, "effective_to"), active=bool(fs(f, "active")), ok="Price list saved (audited).", default="/owner/prices")


@router.get("/settings")
def settings(request: Request, user: db.Actor = Depends(OWN)):
    return render(request, "owner/settings.html", user, rows=O.settings(user))


@router.post("/settings")
def setting_save(request: Request, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    return do(request, O.save_setting, user, fs(f, "setting_key"), fs(f, "setting_value"), ok="Setting saved (audited).", default="/owner/settings")


@router.get("/staff")
def staff(request: Request, user: db.Actor = Depends(OWN)):
    return render(request, "owner/staff.html", user, rows=O.staff(user), roles=O.roles(user))


@router.post("/staff/create")
def staff_create(request: Request, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    return do(request, O.create_staff, user, fs(f, "given_name"), fs(f, "family_name"), fs(f, "phone"), fs(f, "login_subject"),
              f.getlist("roles"), fs(f, "registration_number"), fs(f, "password"), ok="Staff account created.", default="/owner/staff")


@router.post("/staff/{sid}/password")
def staff_pw(request: Request, sid: int, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    return do(request, O.set_password, user, sid, fs(f, "password"), ok="Password set.", default="/owner/staff")


@router.post("/staff/{sid}/role")
def staff_role(request: Request, sid: int, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    if fs(f, "op") == "end":
        return do(request, O.end_role, user, sid, fi(f, "role_id"), ok="Role ended.", default="/owner/staff")
    return do(request, O.add_role, user, sid, fi(f, "role_id"), ok="Role added.", default="/owner/staff")


@router.get("/delegations")
def delegations(request: Request, user: db.Actor = Depends(OWN)):
    return render(request, "owner/delegations.html", user, rows=O.delegations(user), staff=O.staff(user), today=date.today().isoformat())


@router.post("/delegations")
def delegation_grant(request: Request, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    return do(request, O.grant_delegation, user, fi(f, "delegate_staff_id"), fs(f, "scope"), fd(f, "limit_amount"),
              fs(f, "valid_from"), fs(f, "valid_to"), ok="Authority granted.", default="/owner/delegations")


@router.post("/delegations/{did}/revoke")
def delegation_revoke(request: Request, did: int, f=Depends(formdata), user: db.Actor = Depends(OWN)):
    return do(request, O.revoke_delegation, user, did, fs(f, "reason") or "Revoked by owner", ok="Revoked.", default="/owner/delegations")


@router.get("/reconcile")
def reconcile(request: Request, user: db.Actor = Depends(OWN)):
    return render(request, "owner/reconcile.html", user, r=O.reconciliation(user))
