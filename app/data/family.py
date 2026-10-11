"""Family-link data access (P5-7). Public side runs as the SYS-FAMILY system principal (G5). It reads ONLY shareable data:
never cost price, never not_for_sharing notes, never internal approvals. Staff side creates / revokes links and shows the outbox."""
from __future__ import annotations

import hashlib

from .. import config, db
from . import nurse as N
from .clinical import current_prescription

FAMILY = lambda: db.system_actor("FAMILY")


def token_hash(token: str) -> str:
    """ASSUMPTION A-14: family_link.token_hash = sha256 hex of the raw token. Change FAMILY_TOKEN_HASH / this function if P3-6 differs."""
    return hashlib.sha256(token.encode()).hexdigest()


def open_link(token: str, dob: str | None, printed_code: str | None) -> dict:
    """Calls P3-6 open_family_link (lockout, escalating delay and access log live INSIDE the database). Returns link context."""
    fa = FAMILY()
    res = db.call(fa, "open_family_link", token=token, dob=dob, printed_code=printed_code,
                  second_factor=dob or printed_code)
    link = db.one(fa, "SELECT family_link_id, visit_id FROM family_link WHERE token_hash=%s", (token_hash(token),))
    if not link:
        v = res.first_value("visit_id")
        lid = res.first_value("family_link_id")
        if v and lid:
            return {"family_link_id": lid, "visit_id": v}
        raise db.RuleError("This link cannot be opened. Please ask the clinic for a new one.")
    return link


def still_valid(link_id: int) -> bool:
    """Re-checked on every page view so revocation is instant (R11.2)."""
    r = db.one(FAMILY(), """SELECT l.family_link_id FROM family_link l WHERE l.family_link_id=%s AND l.expires_at>UTC_TIMESTAMP()
        AND NOT EXISTS (SELECT 1 FROM family_link_revocation x WHERE x.family_link_id=l.family_link_id)""", (link_id,))
    return bool(r)


def visit_page(visit_id: int) -> dict:
    fa = FAMILY()
    v = db.one(fa, """SELECT v.visit_id, v.occurred_at, p.given_name, p.family_name, p.display_code,
        (SELECT h.status FROM visit_status_history h WHERE h.visit_id=v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS status,
        (SELECT CONCAT_WS(' ',s.given_name,s.family_name) FROM visit_doctor_assignment x JOIN staff s ON s.staff_id=x.doctor_staff_id
           WHERE x.visit_id=v.visit_id AND x.assignment_role='attending' ORDER BY x.visit_doctor_assignment_id DESC LIMIT 1) AS doctor
        FROM visit v JOIN patient p ON p.patient_id=v.patient_id WHERE v.visit_id=%s""", (visit_id,))
    rx = current_prescription(fa, visit_id)
    bill = db.one(fa, """SELECT b.bill_id, b.bill_number,
        COALESCE((SELECT SUM(bl.quantity*bl.unit_price_snapshot) FROM bill_line bl WHERE bl.bill_id=b.bill_id),0) AS total,
        COALESCE((SELECT SUM(CASE WHEN pm.payment_kind='payment' THEN pm.amount ELSE -pm.amount END) FROM payment pm WHERE pm.bill_id=b.bill_id),0) AS paid
        FROM bill b WHERE b.visit_id=%s""", (visit_id,))
    lines, receipts = [], []
    if bill:
        bill["balance"] = bill["total"] - bill["paid"]
        lines = db.q(fa, "SELECT description_snapshot, quantity, unit_price_snapshot, quantity*unit_price_snapshot AS amount FROM bill_line WHERE bill_id=%s ORDER BY line_no", (bill["bill_id"],))
        receipts = db.q(fa, "SELECT receipt_number, amount, method, recorded_at FROM payment WHERE bill_id=%s AND payment_kind='payment' ORDER BY payment_id", (bill["bill_id"],))
    return {
        "v": v, "rx": rx, "bill": bill, "lines": lines, "receipts": receipts,
        "supply": N.reconciliation(visit_id=visit_id, a=fa),
        "reports": db.q(fa, """SELECT lo.test_name, r.lab_report_id, r.recorded_at,
            EXISTS(SELECT 1 FROM lab_report_review w WHERE w.lab_report_id=r.lab_report_id) AS reviewed
            FROM lab_report r JOIN lab_order lo ON lo.lab_order_id=r.lab_order_id WHERE lo.visit_id=%s
            AND NOT EXISTS (SELECT 1 FROM lab_report_retraction x WHERE x.lab_report_id=r.lab_report_id)
            AND NOT EXISTS (SELECT 1 FROM lab_report n WHERE n.supersedes_lab_report_id=r.lab_report_id) ORDER BY r.lab_report_id""", (visit_id,)),
        "notes": db.q(fa, "SELECT note_text, recorded_at FROM clinical_note WHERE visit_id=%s AND sharing_classification='shareable' ORDER BY clinical_note_id DESC", (visit_id,)),
        "signoff": db.one(fa, "SELECT discharge_instructions, recorded_at FROM clinical_signoff WHERE visit_id=%s ORDER BY clinical_signoff_id DESC LIMIT 1", (visit_id,)),
        "discharged": bool(db.one(fa, "SELECT 1 AS x FROM discharge WHERE visit_id=%s", (visit_id,))),
    }


def report_file_for_link(link_id: int, report_id: int):
    fa = FAMILY()
    r = db.one(fa, "SELECT r.file_path FROM lab_report r JOIN lab_order lo ON lo.lab_order_id=r.lab_order_id JOIN family_link l ON l.visit_id=lo.visit_id "
                   "WHERE r.lab_report_id=%s AND l.family_link_id=%s", (report_id, link_id))
    return N.resolve_upload(r["file_path"]) if r else None


# ---------------------------------------------------------------- staff side
def links_for_visit(a, visit_id) -> list[dict]:
    return db.q(a, """SELECT l.family_link_id, l.attendant_id, l.second_factor_kind, l.expires_at, l.recorded_at,
        t.name AS attendant, EXISTS(SELECT 1 FROM family_link_revocation x WHERE x.family_link_id=l.family_link_id) AS revoked,
        (SELECT COUNT(*) FROM link_access_log g WHERE g.family_link_id=l.family_link_id AND g.success=0) AS failures,
        (SELECT COUNT(*) FROM link_access_log g WHERE g.family_link_id=l.family_link_id AND g.success=1) AS opens
        FROM family_link l LEFT JOIN patient_attendant t ON t.patient_attendant_id=l.attendant_id WHERE l.visit_id=%s ORDER BY l.family_link_id DESC""", (visit_id,))


def recent_visits(a) -> list[dict]:
    return db.q(a, """SELECT v.visit_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS name, p.registration_kind,
        (SELECT COUNT(*) FROM family_link l WHERE l.visit_id=v.visit_id) AS links
        FROM visit v JOIN patient p ON p.patient_id=v.patient_id ORDER BY v.visit_id DESC LIMIT 40""")


def attendants(a, visit_id):
    return N.attendants(a, visit_id)


def create_link(a, visit_id, attendant_id, kind, consent_note, request_uuid):
    if not attendant_id:
        raise db.RuleError("Choose who receives the link (their consent is recorded).")
    if not consent_note:
        raise db.RuleError("Write the consent note (for example: 'son agreed by phone').")
    r = db.call(a, "create_family_link", visit_id=visit_id, attendant_id=attendant_id, second_factor_kind=kind,
                consent_note=consent_note, request_uuid=request_uuid)
    token = r.first_value("token")
    code = r.first_value("printed_code")
    ph = db.scalar(a, "SELECT phone FROM patient_attendant WHERE patient_attendant_id=%s", (attendant_id,))
    url = f"{config.PUBLIC_BASE_URL}/v/{token}" if token else None
    if ph and url:
        clinic = db.setting(a, "clinic_name", "the clinic")
        db.call(a, "queue_message", to_phone=ph, template="family_link",
                body=f"Your visit details from {clinic}: {url}", ref_table="family_link", ref_id=r.first_value("family_link_id"))
    return {"url": url, "printed_code": code, "phone": ph}


def revoke(a, link_id, reason):
    db.call(a, "revoke_family_link", family_link_id=link_id, reason=reason or "Revoked by staff")


def outbox(a, limit=60) -> list[dict]:
    return db.q(a, """SELECT m.message_outbox_id, m.message_type, m.recipient_phone, m.safe_template_key, m.recorded_at,
        (SELECT d.delivery_state FROM message_delivery_event d WHERE d.message_outbox_id=m.message_outbox_id ORDER BY d.message_delivery_event_id DESC LIMIT 1) AS state
        FROM message_outbox m ORDER BY m.message_outbox_id DESC LIMIT %s""", (limit,))
