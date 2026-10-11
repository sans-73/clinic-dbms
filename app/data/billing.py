"""Billing, payments, approvals-in-context, discharge checklist, dues (P5-5). Balance is ALWAYS computed (G3):
balance = SUM(line qty x price snapshot) - payments (+ reversals negative) + refunds."""
from __future__ import annotations

import re
from decimal import Decimal

from .. import db, timeutil
from ..constants import APPROVAL_COMPLETER, APPROVAL_TARGET_TABLES
from . import nurse as N

TOTAL = "COALESCE((SELECT SUM(bl.quantity*bl.unit_price_snapshot) FROM bill_line bl WHERE bl.bill_id=b.bill_id),0)"
PAID = "COALESCE((SELECT SUM(CASE WHEN pm.payment_kind='payment' THEN pm.amount ELSE -pm.amount END) FROM payment pm WHERE pm.bill_id=b.bill_id),0)"
REFUNDED = "COALESCE((SELECT SUM(rf.amount) FROM refund rf WHERE rf.bill_id=b.bill_id),0)"
BAL = f"({TOTAL} - {PAID} + {REFUNDED})"
FINAL = "EXISTS (SELECT 1 FROM bill_finalization bf WHERE bf.bill_id=b.bill_id)"
_CONSUMED = " OR ".join(f"EXISTS (SELECT 1 FROM `{t}` x WHERE x.approval_request_id=r.approval_request_id)" for t in APPROVAL_TARGET_TABLES)


def bills(a, q: str = "", only_due=False, limit=60) -> list[dict]:
    where, params = [], []
    for t in [x for x in (q or "").split() if x][:3]:
        where.append("(b.bill_number LIKE %s OR p.display_code LIKE %s OR p.given_name LIKE %s OR p.family_name LIKE %s)")
        params += [f"%{t}%"] * 4
    having = "HAVING balance > 0" if only_due else ""
    rows = db.q(a, f"""SELECT b.bill_id, b.bill_number, b.visit_id, v.patient_id, p.display_code,
        CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name, b.recorded_at,
        {TOTAL} AS total, {PAID} AS paid, {BAL} AS balance, {FINAL} AS finalized
        FROM bill b JOIN visit v ON v.visit_id=b.visit_id JOIN patient p ON p.patient_id=v.patient_id
        {('WHERE ' + ' AND '.join(where)) if where else ''} {having} ORDER BY b.bill_id DESC LIMIT %s""", (*params, limit))
    for r in rows:
        r["state"] = "Settled" if r["finalized"] and r["balance"] <= 0 else ("Finalized" if r["finalized"] else "Draft")
    return rows


def visits_without_bill(a) -> list[dict]:
    s, e = timeutil.local_day_window()
    return db.q(a, """SELECT v.visit_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name
        FROM visit v JOIN patient p ON p.patient_id=v.patient_id
        WHERE v.occurred_at>=%s AND v.occurred_at<%s AND NOT EXISTS (SELECT 1 FROM bill b WHERE b.visit_id=v.visit_id)
        ORDER BY v.visit_id DESC""", (s, e))


def open_bill_for_visit(a, visit_id) -> int | None:
    r = db.call(a, "ensure_open_bill", visit_id=visit_id, counter_customer_id=None)
    return r.first_value("bill_id")


def bill(a, bill_id: int) -> dict | None:
    b = db.one(a, f"""SELECT b.bill_id, b.bill_number, b.visit_id, v.patient_id, p.display_code,
        CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name,
        (SELECT ph.phone_number FROM patient_phone ph WHERE ph.patient_id=p.patient_id AND ph.valid_to IS NULL ORDER BY ph.phone_sequence DESC LIMIT 1) AS phone,
        {TOTAL} AS total, {PAID} AS paid, {REFUNDED} AS refunded, {BAL} AS balance, {FINAL} AS finalized,
        EXISTS(SELECT 1 FROM discharge d WHERE d.visit_id=v.visit_id) AS discharged
        FROM bill b JOIN visit v ON v.visit_id=b.visit_id JOIN patient p ON p.patient_id=v.patient_id WHERE b.bill_id=%s""", (bill_id,))
    if not b:
        return None
    b["state"] = "Settled" if b["finalized"] and b["balance"] <= 0 else ("Finalized" if b["finalized"] else "Draft")
    b["lines"] = db.q(a, """SELECT line_no, line_type, description_snapshot, quantity, unit_price_snapshot, quantity*unit_price_snapshot AS amount,
        is_manual_price, is_price_deviation, reason FROM bill_line WHERE bill_id=%s ORDER BY line_no""", (bill_id,))
    b["payments"] = db.q(a, "SELECT payment_id, payment_kind, amount, method, receipt_number, recorded_at FROM payment WHERE bill_id=%s ORDER BY payment_id", (bill_id,))
    b["refunds"] = db.q(a, "SELECT refund_id, amount, method, receipt_number, recorded_at FROM refund WHERE bill_id=%s ORDER BY refund_id", (bill_id,))
    b["promises"] = db.q(a, "SELECT promised_by_date, reason, recorded_at FROM due_promise WHERE bill_id=%s ORDER BY due_promise_id DESC", (bill_id,))
    b["approvals"] = approvals_for(a, bill_id=bill_id)
    return b


def price_items(a) -> list[dict]:
    return db.q(a, """SELECT price_list_id, item_code, description, category, price FROM price_list
        WHERE active=1 AND effective_from<=UTC_DATE() AND (effective_to IS NULL OR effective_to>=UTC_DATE()) ORDER BY category, description""")


def add_price_line(a, bill_id, price_list_id, qty):
    pl = db.one(a, "SELECT description, price FROM price_list WHERE price_list_id=%s", (price_list_id,))
    if not pl:
        raise db.RuleError("Choose an item from the price list.")
    db.call(a, "bill_add_line", bill_id=bill_id, source_type="price_list", source_id=price_list_id,
            description=pl["description"], quantity=qty or 1, unit_price=pl["price"], reason=None)


def finalize(a, bill_id, reason=None):
    db.call(a, "finalize_bill", bill_id=bill_id, reason=reason)


def pay(a, bill_id, amount, method, request_uuid=None):
    if not amount or Decimal(amount) <= 0:
        raise db.RuleError("Enter the amount received.")
    r = db.call(a, "record_payment", bill_id=bill_id, amount=amount, method=method, request_uuid=request_uuid)
    return r.first_value("receipt_number")


# ---------------------------------------------------------------- approvals (maker side) and completing the target row
def _tag(reason: str, **kv) -> str:
    return "".join(f"[{k}:{v}]" for k, v in kv.items() if v not in (None, "")) + " " + (reason or "")


def _untag(reason: str | None) -> dict:
    return dict(re.findall(r"\[(\w+):([^\]]*)\]", reason or ""))


def request_money_approval(a, kind: str, bill_id, amount, reason, **tags):
    if not reason:
        raise db.RuleError("A reason is required.")
    if kind in ("discount", "free_service", "refund", "payment_reversal", "post_finalization_bill_correction") and (amount is None):
        raise db.RuleError("Enter the amount.")
    r = db.call(a, "request_approval", type=kind, ref_table="bill", ref_id=bill_id, reason=_tag(reason, **tags), amount=amount)
    return r.first_value("approval_id")


def approvals_for(a, bill_id=None, visit_id=None) -> list[dict]:
    cond, params = [], []
    if bill_id:
        cond.append("r.bill_id=%s"); params.append(bill_id)
    if visit_id:
        cond.append("r.visit_id=%s"); params.append(visit_id)
    rows = db.q(a, f"""SELECT r.approval_request_id, r.request_type, r.requested_amount, r.reason, r.recorded_at, r.bill_id, r.visit_id,
        d.decision, d.decision_reason, ({_CONSUMED}) AS consumed
        FROM approval_request r LEFT JOIN approval_decision d ON d.approval_request_id=r.approval_request_id
        WHERE {' OR '.join(cond) or '1=0'} ORDER BY r.approval_request_id DESC""", params)
    for r in rows:
        r["status"] = ("consumed" if r["consumed"] else "approved" if r["decision"] == "approved" else
                       "rejected" if r["decision"] == "rejected" else "pending")
        r["tags"] = _untag(r["reason"])
        r["can_complete"] = r["status"] == "approved" and r["request_type"] in APPROVAL_COMPLETER and r["request_type"] != "partial_payment_discharge"
    return rows


def complete_approved(a, approval_id, method=None):
    r = db.one(a, "SELECT approval_request_id, request_type, requested_amount, reason, bill_id FROM approval_request WHERE approval_request_id=%s", (approval_id,))
    if not r:
        raise db.RuleError("Request not found.")
    dec = db.one(a, "SELECT decision FROM approval_decision WHERE approval_request_id=%s", (approval_id,))
    if not dec or dec["decision"] != "approved":
        raise db.RuleError("This request has not been approved by the owner.")
    t, tags = r["request_type"], _untag(r["reason"])
    key = APPROVAL_COMPLETER.get(t)
    if not key:
        raise db.RuleError("Nothing to complete for this request type.")
    clean_reason = re.sub(r"^(\[\w+:[^\]]*\])+\s*", "", r["reason"] or "")
    if key == "record_refund":
        db.call(a, "record_refund", bill_id=r["bill_id"], payment_id=tags.get("payment"), amount=r["requested_amount"],
                method=method or tags.get("method") or "cash", approval_request_id=approval_id)
    elif key == "reverse_payment":
        db.call(a, "reverse_payment", payment_id=tags.get("payment"), approval_request_id=approval_id, reason=clean_reason)
    elif key == "apply_discount":
        db.call(a, "apply_discount", bill_id=r["bill_id"], amount=r["requested_amount"], kind=t, approval_request_id=approval_id, reason=clean_reason)
    elif key == "post_final_line":
        db.call(a, "post_final_line", bill_id=r["bill_id"], line_type=tags.get("line_type") or "post_finalization_credit",
                description=clean_reason, amount=r["requested_amount"], approval_request_id=approval_id, reason=clean_reason)


# ---------------------------------------------------------------- discharge checklist (R9.4) and discharge
def checklist(a, visit_id: int) -> dict:
    v = db.one(a, f"""SELECT v.visit_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name,
        EXISTS(SELECT 1 FROM discharge d WHERE d.visit_id=v.visit_id) AS discharged,
        EXISTS(SELECT 1 FROM clinical_signoff s WHERE s.visit_id=v.visit_id) AS signed,
        (SELECT b.bill_id FROM bill b WHERE b.visit_id=v.visit_id) AS bill_id,
        (SELECT co.clinical_emergency_override_id FROM clinical_emergency_override co WHERE co.visit_id=v.visit_id ORDER BY co.clinical_emergency_override_id DESC LIMIT 1) AS override_id
        FROM visit v JOIN patient p ON p.patient_id=v.patient_id WHERE v.visit_id=%s""", (visit_id,))
    if not v:
        return {}
    rec = N.reconciliation(a, visit_id)
    unreviewed = db.one(a, """SELECT COUNT(*) AS n FROM lab_report r JOIN lab_order lo ON lo.lab_order_id=r.lab_order_id
        WHERE lo.visit_id=%s AND NOT EXISTS (SELECT 1 FROM lab_report_retraction x WHERE x.lab_report_id=r.lab_report_id)
          AND NOT EXISTS (SELECT 1 FROM lab_report n WHERE n.supersedes_lab_report_id=r.lab_report_id)
          AND NOT EXISTS (SELECT 1 FROM lab_report_review w WHERE w.lab_report_id=r.lab_report_id)""", (visit_id,))["n"]
    b = None
    if v["bill_id"]:
        b = db.one(a, f"SELECT {TOTAL} AS total, {BAL} AS balance, {FINAL} AS finalized FROM bill b WHERE b.bill_id=%s", (v["bill_id"],))
    partial = None
    if v["bill_id"]:
        partial = next((x for x in approvals_for(a, bill_id=v["bill_id"]) if x["request_type"] == "partial_payment_discharge"), None)
    balance = b["balance"] if b else Decimal(0)
    money_ok = bool(b) and balance <= 0
    basis = "bill_settled" if money_ok else ("approved_partial_payment" if partial and partial["status"] in ("approved",) else
                                              ("emergency_override" if v["override_id"] else None))
    items = [
        ("Clinical sign-off by the doctor", bool(v["signed"]), "Doctor must sign off on the visit page."),
        ("Medicine reconciled (given + returned + handed to family = dispensed)", all(r["balanced"] for r in rec), f"{sum(1 for r in rec if not r['balanced'])} item(s) do not balance. Open nurse custody."),
        ("Lab reports reviewed by doctor", unreviewed == 0, f"{unreviewed} report(s) waiting for review."),
        ("Bill finalized", bool(b and b["finalized"]), "Finalize the bill first."),
        ("Balance zero, or owner-approved partial payment, or clinical emergency override", basis is not None,
         f"Balance due {balance}. Request partial-payment approval or ask the doctor for an emergency override."),
    ]
    ready = all(ok for _, ok, _ in items) if not v["discharged"] else False
    return {"v": v, "items": items, "ready": ready, "basis": basis, "balance": balance, "partial": partial, "bill": b, "rec": rec}


def open_visits_for_discharge(a) -> list[dict]:
    return db.q(a, f"""SELECT v.visit_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name,
        (SELECT h.status FROM visit_status_history h WHERE h.visit_id=v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS status,
        (SELECT b.bill_id FROM bill b WHERE b.visit_id=v.visit_id) AS bill_id
        FROM visit v JOIN patient p ON p.patient_id=v.patient_id
        WHERE NOT EXISTS (SELECT 1 FROM discharge d WHERE d.visit_id=v.visit_id) ORDER BY v.visit_id DESC LIMIT 60""")


def request_partial(a, visit_id, bill_id, deferred, promised_by, reason):
    if not bill_id or deferred is None or not promised_by or not reason:
        raise db.RuleError("Amount to defer, promised-by date and reason are all required.")
    db.call(a, "request_partial_discharge", visit_id=visit_id, bill_id=bill_id, deferred_amount=deferred,
            promised_by_date=promised_by, reason=reason)


def discharge(a, visit_id, promised_by=None):
    c = checklist(a, visit_id)
    if not c:
        raise db.RuleError("Visit not found.")
    if not c["ready"]:
        raise db.RuleError("The discharge checklist is not complete.")
    appr = c["partial"]["approval_request_id"] if c["basis"] == "approved_partial_payment" and c["partial"] else None
    db.call(a, "discharge_visit", visit_id=visit_id, financial_gate_basis=c["basis"], approval_request_id=appr,
            clinical_emergency_override_id=c["v"]["override_id"] if c["basis"] == "emergency_override" else None,
            promised_by_date=promised_by)


# ---------------------------------------------------------------- dues, receipts, cash
def dues(a) -> list[dict]:
    rows = db.q(a, f"""SELECT b.bill_id, b.bill_number, v.patient_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name,
        (SELECT ph.phone_number FROM patient_phone ph WHERE ph.patient_id=p.patient_id AND ph.valid_to IS NULL ORDER BY ph.phone_sequence DESC LIMIT 1) AS phone,
        {BAL} AS balance, (SELECT MAX(dp.promised_by_date) FROM due_promise dp WHERE dp.bill_id=b.bill_id) AS promised_by
        FROM bill b JOIN visit v ON v.visit_id=b.visit_id JOIN patient p ON p.patient_id=v.patient_id
        WHERE EXISTS (SELECT 1 FROM discharge d WHERE d.visit_id=v.visit_id) HAVING balance > 0 ORDER BY promised_by IS NULL, promised_by""")
    from datetime import date
    for r in rows:
        r["overdue"] = bool(r["promised_by"] and r["promised_by"] < date.today())
    return rows


def send_reminder(a, bill_id):
    r = db.one(a, f"""SELECT (SELECT ph.phone_number FROM patient_phone ph WHERE ph.patient_id=v.patient_id AND ph.valid_to IS NULL ORDER BY ph.phone_sequence DESC LIMIT 1) AS phone
        FROM bill b JOIN visit v ON v.visit_id=b.visit_id WHERE b.bill_id=%s""", (bill_id,))
    if not r or not r["phone"]:
        raise db.RuleError("No phone number on file for this patient.")
    clinic = db.setting(a, "clinic_name", "the clinic")
    db.call(a, "queue_message", to_phone=r["phone"], template="due_reminder",
            body=f"A payment is pending at {clinic}. Please contact the clinic.", ref_table="bill", ref_id=bill_id)


def receipt(a, payment_id) -> dict | None:
    p = db.one(a, """SELECT pm.payment_id, pm.amount, pm.method, pm.receipt_number, pm.recorded_at, pm.payment_kind, b.bill_number, b.bill_id,
        CONCAT_WS(' ',pt.given_name,pt.family_name) AS patient_name, pt.display_code,
        CONCAT_WS(' ',s.given_name,s.family_name) AS received_by
        FROM payment pm JOIN bill b ON b.bill_id=pm.bill_id JOIN visit v ON v.visit_id=b.visit_id JOIN patient pt ON pt.patient_id=v.patient_id
        JOIN staff s ON s.staff_id=pm.recorded_by WHERE pm.payment_id=%s""", (payment_id,))
    if p:
        p["clinic"] = db.setting(a, "clinic_name", "")
    return p
