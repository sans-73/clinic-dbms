"""P5-7 Family link. PUBLIC pages under /v/<token> (no login; token + date of birth or printed code), plus staff pages under
/family (create / revoke link, simulated message outbox). The database enforces lockout (5 failures, escalating delay) and logs every attempt."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from .. import auth, db
from ..data import family as F
from ..web import csrf_protect, do, flash, formdata, fi, fs, qi, redirect, render, error_page

router = APIRouter(dependencies=[Depends(csrf_protect)])
STAFF = auth.require("RECEPTIONIST", "NURSE", "OWNER")


def _session_key(token: str) -> str:
    return "fam_" + F.token_hash(token)[:16]


# ---------------------------------------------------------------- public
@router.get("/v/{token}")
def public_gate(request: Request, token: str):
    key = _session_key(token)
    lid = request.session.get(key)
    if lid and F.still_valid(int(lid)):
        return redirect(f"/v/{token}/visit")
    return render(request, "family/gate.html", None, token=token)


@router.post("/v/{token}")
def public_open(request: Request, token: str, f=Depends(formdata)):
    try:
        link = F.open_link(token, fs(f, "dob"), fs(f, "printed_code"))
        request.session[_session_key(token)] = int(link["family_link_id"])
    except db.RuleError as e:
        flash(request, str(e), "err")           # includes lockout text from the database
        return redirect(f"/v/{token}")
    return redirect(f"/v/{token}/visit")


def _link(request: Request, token: str):
    lid = request.session.get(_session_key(token))
    if not lid or not F.still_valid(int(lid)):
        request.session.pop(_session_key(token), None)
        return None
    return int(lid)


@router.get("/v/{token}/visit")
def public_visit(request: Request, token: str):
    lid = _link(request, token)
    if not lid:
        flash(request, "This link has expired or was switched off. Please contact the clinic.", "err")
        return redirect(f"/v/{token}")
    link = db.one(F.FAMILY(), "SELECT visit_id FROM family_link WHERE family_link_id=%s", (lid,))
    data = F.visit_page(link["visit_id"]) if link else {}
    return render(request, "family/visit.html", None, d=data, token=token)


@router.get("/v/{token}/report/{rid}")
def public_report(request: Request, token: str, rid: int):
    lid = _link(request, token)
    p = F.report_file_for_link(lid, rid) if lid else None
    if not p:
        return error_page(request, None, "Report not available.", 404)
    return FileResponse(str(p), headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- staff
@router.get("/family/links")
def links(request: Request, user: db.Actor = Depends(STAFF)):
    visit_id = qi(request, "visit_id")
    return render(request, "family/links.html", user, visits=F.recent_visits(user), visit_id=visit_id,
                  links=F.links_for_visit(user, visit_id) if visit_id else [], att=F.attendants(user, visit_id) if visit_id else [],
                  created=request.session.pop("_created_link", None))


@router.post("/family/links/create")
def link_create(request: Request, f=Depends(formdata), user: db.Actor = Depends(STAFF)):
    vid = fi(f, "visit_id")
    try:
        res = F.create_link(user, vid, fi(f, "attendant_id"), fs(f, "second_factor_kind", "date_of_birth"),
                            fs(f, "consent_note"), fs(f, "request_uuid"))
        request.session["_created_link"] = res          # shown ONCE, never stored in the database in clear
        flash(request, "Link created and the message was queued.")
    except db.RuleError as e:
        flash(request, str(e), "err")
    return redirect(f"/family/links?visit_id={vid}")


@router.post("/family/links/{lid}/revoke")
def link_revoke(request: Request, lid: int, f=Depends(formdata), user: db.Actor = Depends(STAFF)):
    return do(request, F.revoke, user, lid, fs(f, "reason"), ok="Link switched off immediately.", nxt=fs(f, "next"), default="/family/links")


@router.get("/family/outbox")
def outbox(request: Request, user: db.Actor = Depends(STAFF)):
    return render(request, "family/outbox.html", user, rows=F.outbox(user))
