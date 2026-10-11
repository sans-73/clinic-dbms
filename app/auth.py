"""Login, session, role ('hat') selection and the require() guard used by every staff route.

Session cookie (signed by Starlette) holds: staff_id, name, display_code, roles{code:role_id}, role (active hat).
ASSUMPTIONS (see guide): A-01 table staff_credential(staff_id, <a column containing 'hash'>);
A-03 system principals exist; A-04 role codes are OWNER/DOCTOR/RECEPTIONIST/NURSE/PHARMACY/ACCOUNTANT.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict, deque

from fastapi import Request

from . import config, db
from .passwords import verify_password

log = logging.getLogger("clinic.auth")


class NeedLogin(Exception):
    def __init__(self, nxt: str = "/"):
        self.nxt = nxt


class Forbidden(Exception):
    pass


# ---------------------------------------------------------------- stub users (DB_MODE=stub only)
STUB_USERS = {
    "owner": (1, "Demo Owner", ["OWNER", "DOCTOR"]), "doctor": (2, "Dr. Demo", ["DOCTOR"]),
    "reception": (3, "Demo Reception", ["RECEPTIONIST"]), "nurse": (4, "Demo Nurse", ["NURSE"]),
    "pharmacy": (5, "Demo Pharmacist", ["PHARMACY"]), "accountant": (6, "Demo Accountant", ["ACCOUNTANT"]),
}

_fails: dict[str, deque] = defaultdict(deque)


def _throttled(ident: str) -> bool:
    now = time.time()
    dq = _fails[ident.lower()]
    while dq and now - dq[0] > 600:
        dq.popleft()
    return len(dq) >= 8


def _auth_actor():
    return db.system_actor("AUTH")


def _log_event(event: str, ident: str, staff_id=None, role_id=None, request: Request | None = None):
    """login_session_event is written under the SYS-AUTH principal (G5). Best effort: never blocks a login."""
    if db.STUB:
        return
    a = _auth_actor()
    if not a:
        return
    try:
        sid = hashlib.sha256((request.session.get("csrf", "") if request else "").encode()).hexdigest()[:32]
        db.insert_event(a, "login_session_event", staff_id=staff_id, claimed_role_id=role_id, login_identifier=ident,
                        session_id_hash=sid, event_type=event,
                        source_context=(request.headers.get("user-agent", "")[:80] if request else None))
    except Exception as e:
        log.warning("could not write login_session_event: %s", e)


# ---------------------------------------------------------------- authenticate
def authenticate(ident: str, password: str, request: Request) -> dict:
    ident = (ident or "").strip()
    if not ident or not password:
        raise db.RuleError("Enter your login and password.")
    if _throttled(ident):
        raise db.RuleError("Too many wrong attempts. Wait 10 minutes and try again.")
    if db.STUB:
        u = STUB_USERS.get(ident.lower())
        if not u or password != "demo":
            raise db.RuleError("Stub mode logins: owner / doctor / reception / nurse / pharmacy / accountant, password: demo")
        return {"staff_id": u[0], "name": u[1], "display_code": ident.upper(), "roles": {r: i for i, r in enumerate(u[2], 1)}}
    s = db.one(None, "SELECT staff_id, display_code, given_name, family_name, active_from, active_to FROM staff "
                     "WHERE principal_kind='human' AND (login_subject=%s OR display_code=%s) LIMIT 1", (ident, ident))
    ok = False
    if s:
        cred = db.one(None, "SELECT * FROM staff_credential WHERE staff_id=%s", (s["staff_id"],))
        hcol = next((k for k in (cred or {}) if "hash" in k.lower()), None)
        ok = bool(cred and hcol and verify_password(password, cred[hcol]))
        if ok and s.get("active_to") is not None:
            from .timeutil import utcnow
            if s["active_to"] <= utcnow():
                ok = False
    if not ok:
        _fails[ident.lower()].append(time.time())
        _log_event("login_failed", ident, s["staff_id"] if s else None, None, request)
        raise db.RuleError("Wrong login or password.")
    roles = db.q(None, "SELECT r.role_id, r.role_code FROM staff_role sr JOIN role r ON r.role_id=sr.role_id "
                       "WHERE sr.staff_id=%s AND sr.valid_from <= UTC_TIMESTAMP() "
                       "AND (sr.valid_to IS NULL OR sr.valid_to > UTC_TIMESTAMP())", (s["staff_id"],))
    roles = {r["role_code"].upper(): r["role_id"] for r in roles if r["role_code"].upper() != "SYSTEM"}
    if not roles:
        raise db.RuleError("Your account has no active role. Ask the owner.")
    return {"staff_id": s["staff_id"], "name": f"{s['given_name']} {s['family_name']}".strip(),
            "display_code": s["display_code"], "roles": roles}


def start_session(request: Request, info: dict, role: str | None = None, ident: str = ""):
    keep = {k: v for k, v in request.session.items() if k in ("csrf",)}
    request.session.clear()
    request.session.update(keep)
    request.session.update({"staff_id": info["staff_id"], "name": info["name"], "display_code": info["display_code"],
                            "roles": info["roles"]})
    if role:
        request.session["role"] = role
        _log_event("login_success", ident or info["display_code"], info["staff_id"], info["roles"].get(role), request)


def set_role(request: Request, role: str):
    roles = request.session.get("roles", {})
    if role not in roles:
        raise db.RuleError("You do not have that role.")
    old = request.session.get("role")
    request.session["role"] = role
    if old and old != role:
        _log_event("role_switch", request.session.get("display_code", ""), request.session.get("staff_id"),
                   roles.get(role), request)


def logout(request: Request):
    _log_event("logout", request.session.get("display_code", ""), request.session.get("staff_id"),
               request.session.get("roles", {}).get(request.session.get("role")), request)
    keep = request.session.get("csrf")
    request.session.clear()
    if keep:
        request.session["csrf"] = keep


# ---------------------------------------------------------------- guards
def actor_from_session(request: Request) -> db.Actor | None:
    s = request.session
    if not s.get("staff_id") or not s.get("role"):
        return None
    roles = s.get("roles", {})
    return db.Actor(staff_id=int(s["staff_id"]), role=s["role"], name=s.get("name", ""),
                    display_code=s.get("display_code", ""), role_id=roles.get(s["role"]), roles=list(roles))


def current_actor(request: Request) -> db.Actor | None:
    return actor_from_session(request)


def require(*roles: str):
    """FastAPI dependency factory: login required; if roles given, the ACTIVE hat must be one of them."""
    wanted = {r.upper() for r in roles}

    def dep(request: Request) -> db.Actor:
        a = actor_from_session(request)
        if not a:
            nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
            if request.session.get("staff_id"):          # logged in but no hat chosen yet
                raise NeedLogin("/choose-role")
            raise NeedLogin(nxt)
        if wanted and a.role not in wanted:
            raise Forbidden(f"The '{a.role}' hat cannot open this page. Switch role (top right) or ask the owner.")
        return a

    return dep
