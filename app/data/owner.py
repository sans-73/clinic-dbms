"""Owner data access (P5-6): banner, daily summary (red / amber / clean), approvals inbox, alerts, exception reports,
audit viewer, price list, settings, staff, delegations, reconciliation. Read-heavy; owner never edits clinical records."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .. import db, passwords, timeutil
from . import patients as P

PENDING_APPROVALS = "NOT EXISTS (SELECT 1 FROM approval_decision d WHERE d.approval_request_id=r.approval_request_id)"
UNACKED = "NOT EXISTS (SELECT 1 FROM alert_acknowledgement k WHERE k.alert_event_id=e.alert_event_id)"


def banner(a) -> dict:
    return {"interrupts": db.scalar(a, f"SELECT COUNT(*) FROM alert_event e WHERE e.alert_class='safety_interrupt' AND {UNACKED}", (), 0),
            "approvals": db.scalar(a, f"SELECT COUNT(*) FROM approval_request r WHERE {PENDING_APPROVALS}", (), 0)}


# ---------------------------------------------------------------- daily summary (R10.1)
_H24 = "recorded_at >= UTC_TIMESTAMP() - INTERVAL 24 HOUR"
RED = [
    ("Approval requests waiting for your decision", f"SELECT COUNT(*) FROM approval_request r WHERE {PENDING_APPROVALS}", "/owner/approvals"),
    ("Patient-safety alerts not acknowledged", f"SELECT COUNT(*) FROM alert_event e WHERE e.alert_class='safety_interrupt' AND {UNACKED}", "/owner/alerts"),
    ("Critical lab reports not yet reviewed by a doctor",
     """SELECT COUNT(*) FROM lab_report r WHERE (SELECT k.mark FROM lab_report_critical_mark k WHERE k.lab_report_id=r.lab_report_id ORDER BY k.lab_report_critical_mark_id DESC LIMIT 1)='critical'
        AND NOT EXISTS (SELECT 1 FROM lab_report_review w WHERE w.lab_report_id=r.lab_report_id)
        AND NOT EXISTS (SELECT 1 FROM lab_report_retraction x WHERE x.lab_report_id=r.lab_report_id)""", "/owner/reconcile"),
    ("Expired medicine still on the shelf",
     """SELECT COUNT(*) FROM (SELECT b.stock_batch_id FROM stock_batch b JOIN stock_movement sm ON sm.stock_batch_id=b.stock_batch_id
        WHERE b.expiry_date<UTC_DATE() GROUP BY b.stock_batch_id HAVING SUM(sm.quantity_delta)>0) t""", "/pharmacy"),
    ("Cash handovers where counted cash differs from declared",
     """SELECT COUNT(*) FROM cash_handover_count c JOIN cash_handover_declaration d ON d.cash_handover_declaration_id=c.cash_handover_declaration_id
        WHERE c.counted_amount<>d.declared_amount AND c.recorded_at >= UTC_TIMESTAMP() - INTERVAL 7 DAY""", "/owner/reconcile"),
]
AMBER = [
    ("Discounts / free services applied (24 h)", f"SELECT COUNT(*) FROM bill_line WHERE line_type IN ('discount','free_service') AND {_H24}", "/owner/reports/discounts"),
    ("Manual (off-list) prices used (24 h)", f"SELECT COUNT(*) FROM bill_line WHERE is_manual_price=1 AND {_H24}", "/owner/reports/manual_prices"),
    ("Refunds issued (24 h)", f"SELECT COUNT(*) FROM refund WHERE {_H24}", "/owner/reports/refunds"),
    ("Stock adjustments / write-offs (24 h)", f"SELECT COUNT(*) FROM stock_movement WHERE movement_type IN ('adjustment','expired_writeoff') AND {_H24}", "/owner/reports/adjustments"),
    ("Over-the-counter sales (24 h)", f"SELECT COUNT(*) FROM dispensing WHERE source_type='otc' AND {_H24}", "/owner/reports/otc"),
    ("Back-filled entries (24 h)", f"SELECT (SELECT COUNT(*) FROM payment WHERE is_backfilled=1 AND {_H24}) + (SELECT COUNT(*) FROM bill_line WHERE is_backfilled=1 AND {_H24}) + (SELECT COUNT(*) FROM dose_administration WHERE is_backfilled=1 AND {_H24})", "/owner/reports/backfilled"),
    ("Lab prices that deviate from usual (24 h)", f"SELECT COUNT(*) FROM lab_order_price WHERE is_price_deviation=1 AND {_H24}", "/owner/reports/lab_deviations"),
    ("Leftover medicine accepted back (24 h)", f"SELECT COUNT(*) FROM leftover_return_decision WHERE decision='accepted' AND {_H24}", "/owner/reports/returns"),
    ("Batches expiring soon", "SELECT COUNT(*) FROM (SELECT b.stock_batch_id FROM stock_batch b JOIN stock_movement sm ON sm.stock_batch_id=b.stock_batch_id WHERE b.expiry_date>=UTC_DATE() AND b.expiry_date<=UTC_DATE()+INTERVAL 60 DAY GROUP BY b.stock_batch_id HAVING SUM(sm.quantity_delta)>0) t", "/pharmacy"),
    ("Free-text medicines waiting for catalog review", "SELECT COUNT(*) FROM catalog_review_queue q WHERE NOT EXISTS (SELECT 1 FROM catalog_review_event e WHERE e.catalog_review_queue_id=q.catalog_review_queue_id)", "/pharmacy/catalog-review"),
    ("Lab tests ordered more than a day ago but not billed", "SELECT COUNT(*) FROM lab_order lo WHERE lo.recorded_at < UTC_TIMESTAMP() - INTERVAL 1 DAY AND NOT EXISTS (SELECT 1 FROM bill_line bl WHERE bl.lab_order_id=lo.lab_order_id)", "/owner/reports/lab_unbilled"),
]


def summary(a) -> dict:
    def run(items):
        out = []
        for label, sql, link in items:
            try:
                n = db.scalar(a, sql, (), 0)
            except db.RuleError:
                n = None
            out.append({"label": label, "n": n, "link": link})
        return out
    red, amber = run(RED), run(AMBER)
    stays = [s for s in P.active_stays(a) if s["over_limit"]]
    red.insert(2, {"label": "Short-stay patients over the time limit", "n": len(stays), "link": "/reception"})
    dues = db.scalar(a, "SELECT COUNT(*) FROM due_promise dp WHERE dp.promised_by_date<UTC_DATE() AND dp.due_promise_id=(SELECT MAX(x.due_promise_id) FROM due_promise x WHERE x.bill_id=dp.bill_id)", (), 0)
    red.append({"label": "Promised payment dates passed", "n": dues, "link": "/billing/dues"})
    return {"red": [x for x in red if x["n"]], "amber": [x for x in amber if x["n"]],
            "clean": [x for x in red + amber if x["n"] == 0], "unknown": [x for x in red + amber if x["n"] is None],
            "stays": stays}


# ---------------------------------------------------------------- approvals inbox
def approvals(a, pending=True, limit=60) -> list[dict]:
    cond = PENDING_APPROVALS if pending else "NOT (" + PENDING_APPROVALS + ")"
    rows = db.q(a, f"""SELECT r.approval_request_id, r.request_type, r.requested_amount, r.requested_quantity, r.reason, r.recorded_at,
        CONCAT_WS(' ',s.given_name,s.family_name) AS requester, rr.role_code AS requester_role,
        b.bill_number, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name, p.display_code,
        d.decision, d.decision_reason, d.recorded_at AS decided_at
        FROM approval_request r JOIN staff s ON s.staff_id=r.recorded_by LEFT JOIN role rr ON rr.role_id=r.recorded_by_role
        LEFT JOIN bill b ON b.bill_id=r.bill_id LEFT JOIN visit v ON v.visit_id=COALESCE(r.visit_id, b.visit_id) LEFT JOIN patient p ON p.patient_id=v.patient_id
        LEFT JOIN approval_decision d ON d.approval_request_id=r.approval_request_id
        WHERE {cond} ORDER BY r.approval_request_id DESC LIMIT %s""", (limit,))
    return rows


def decide(a, approval_id, decision, note):
    if decision not in ("APPROVED", "REJECTED"):
        raise db.RuleError("Choose approve or reject.")
    if decision == "REJECTED" and not note:
        raise db.RuleError("Please give a reason when rejecting.")
    db.call(a, "decide_approval", approval_id=approval_id, decision=decision, note=note)


# ---------------------------------------------------------------- alerts
def alerts(a, only_open=True, limit=80) -> list[dict]:
    rows = db.q(a, f"""SELECT e.alert_event_id, e.alert_class, e.alert_type, e.severity, e.summary, e.source_entity_type, e.source_entity_id, e.recorded_at,
        (SELECT k.recorded_at FROM alert_acknowledgement k WHERE k.alert_event_id=e.alert_event_id ORDER BY k.alert_acknowledgement_id LIMIT 1) AS acked_at,
        (SELECT CONCAT_WS(' ',s.given_name,s.family_name) FROM alert_acknowledgement k JOIN staff s ON s.staff_id=k.recorded_by WHERE k.alert_event_id=e.alert_event_id ORDER BY k.alert_acknowledgement_id LIMIT 1) AS acked_by,
        EXISTS(SELECT 1 FROM alert_escalation x WHERE x.alert_event_id=e.alert_event_id) AS escalated
        FROM alert_event e {('WHERE ' + UNACKED) if only_open else ''} ORDER BY e.alert_event_id DESC LIMIT %s""", (limit,))
    return rows


def ack_alert(a, alert_id, note):
    try:
        db.call(a, "ack_alert", alert_event_id=alert_id, note=note)
    except db.NotReady:
        db.insert_event(a, "alert_acknowledgement", alert_event_id=alert_id, note=note)


def run_checks(a):
    db.call(a, "check_alerts")


# ---------------------------------------------------------------- exception reports (R10.5), last N days
_D = "%s"
REPORTS: dict[str, tuple[str, str, str]] = {
    "discounts": ("Discounts and free services by staff", "Who is giving discounts, how often, how much.",
        "SELECT CONCAT_WS(' ',s.given_name,s.family_name) AS staff, bl.line_type, COUNT(*) AS lines, SUM(bl.quantity*bl.unit_price_snapshot) AS amount FROM bill_line bl JOIN staff s ON s.staff_id=bl.recorded_by WHERE bl.line_type IN ('discount','free_service') AND bl.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY s.staff_id, bl.line_type ORDER BY lines DESC"),
    "refunds": ("Refunds", "Every refund with who recorded it.",
        "SELECT rf.recorded_at, b.bill_number, rf.amount, rf.method, CONCAT_WS(' ',s.given_name,s.family_name) AS staff FROM refund rf JOIN bill b ON b.bill_id=rf.bill_id JOIN staff s ON s.staff_id=rf.recorded_by WHERE rf.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY ORDER BY rf.refund_id DESC"),
    "manual_prices": ("Manual (off-list) prices", "Bill lines priced by hand, with the reason.",
        "SELECT bl.recorded_at, b.bill_number, bl.description_snapshot AS item, bl.unit_price_snapshot AS price, bl.expected_unit_price AS expected, bl.reason, CONCAT_WS(' ',s.given_name,s.family_name) AS staff FROM bill_line bl JOIN bill b ON b.bill_id=bl.bill_id JOIN staff s ON s.staff_id=bl.recorded_by WHERE bl.is_manual_price=1 AND bl.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY ORDER BY bl.recorded_at DESC"),
    "backfilled": ("Back-filled and back-dated entries", "Entries made after the fact (occurred_at far from recorded_at).",
        "SELECT 'payment' AS what, p.recorded_at, p.occurred_at, CONCAT_WS(' ',s.given_name,s.family_name) AS staff FROM payment p JOIN staff s ON s.staff_id=p.recorded_by WHERE (p.is_backfilled=1 OR p.occurred_at < p.recorded_at - INTERVAL 1 HOUR) AND p.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY UNION ALL SELECT 'dose', d.recorded_at, d.occurred_at, CONCAT_WS(' ',s.given_name,s.family_name) FROM dose_administration d JOIN staff s ON s.staff_id=d.recorded_by WHERE (d.is_backfilled=1 OR d.occurred_at < d.recorded_at - INTERVAL 1 HOUR) AND d.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY ORDER BY recorded_at DESC"),
    "otc": ("Over-the-counter sales by pharmacist", "OTC volume and per-person patterns.",
        "SELECT CONCAT_WS(' ',s.given_name,s.family_name) AS pharmacist, COUNT(*) AS otc_sales, COUNT(DISTINCT d.medicine_id) AS different_medicines, MAX(d.recorded_at) AS last_sale FROM dispensing d JOIN staff s ON s.staff_id=d.recorded_by WHERE d.source_type='otc' AND d.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY s.staff_id ORDER BY otc_sales DESC"),
    "adjustments": ("Stock adjustments and write-offs", "Every stock correction with its approval.",
        "SELECT sm.recorded_at, m.brand_name, b.batch_code, sm.movement_type, sm.quantity_delta, sm.reason, CONCAT_WS(' ',s.given_name,s.family_name) AS staff, sm.approval_request_id FROM stock_movement sm JOIN stock_batch b ON b.stock_batch_id=sm.stock_batch_id JOIN medicine m ON m.medicine_id=b.medicine_id JOIN staff s ON s.staff_id=sm.recorded_by WHERE sm.movement_type IN ('adjustment','expired_writeoff','reversal') AND sm.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY ORDER BY sm.stock_movement_id DESC"),
    "returns": ("Leftover returns by pharmacist", "Return patterns (R6.4).",
        "SELECT CONCAT_WS(' ',s.given_name,s.family_name) AS pharmacist, ld.decision, COUNT(*) AS n FROM leftover_return_decision ld JOIN staff s ON s.staff_id=ld.recorded_by WHERE ld.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY s.staff_id, ld.decision ORDER BY n DESC"),
    "lab_deviations": ("Lab prices that deviate", "Outside-lab prices flagged against the usual price.",
        "SELECT lp.recorded_at, lo.test_name, lp.entered_price, lp.expected_price, lp.deviation_reason, CONCAT_WS(' ',s.given_name,s.family_name) AS staff FROM lab_order_price lp JOIN lab_order lo ON lo.lab_order_id=lp.lab_order_id JOIN staff s ON s.staff_id=lp.recorded_by WHERE lp.is_price_deviation=1 AND lp.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY ORDER BY lp.recorded_at DESC"),
    "lab_unbilled": ("Lab tests ordered but not billed", "Reconciliation R7.5.",
        "SELECT lo.lab_order_id, lo.test_name, v.visit_id, lo.recorded_at FROM lab_order lo JOIN visit v ON v.visit_id=lo.visit_id WHERE lo.recorded_at<UTC_TIMESTAMP()-INTERVAL 1 DAY AND lo.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY AND NOT EXISTS (SELECT 1 FROM bill_line bl WHERE bl.lab_order_id=lo.lab_order_id) ORDER BY lo.lab_order_id DESC"),
    "lab_no_report": ("Lab tests billed but no report after N days", "Reconciliation R7.5 (N = setting billed_no_report_days).",
        "SELECT lo.lab_order_id, lo.test_name, lo.recorded_at FROM lab_order lo WHERE EXISTS (SELECT 1 FROM bill_line bl WHERE bl.lab_order_id=lo.lab_order_id) AND NOT EXISTS (SELECT 1 FROM lab_report r WHERE r.lab_order_id=lo.lab_order_id) AND lo.recorded_at<UTC_TIMESTAMP()-INTERVAL 3 DAY AND lo.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY"),
    "shared_logins": ("Possible shared logins", "A staff login used from more than one device/browser on the same day.",
        "SELECT CONCAT_WS(' ',s.given_name,s.family_name) AS staff, DATE(e.recorded_at) AS day, COUNT(DISTINCT e.source_context) AS devices, COUNT(*) AS events FROM login_session_event e JOIN staff s ON s.staff_id=e.staff_id WHERE e.event_type='login_success' AND e.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY e.staff_id, DATE(e.recorded_at) HAVING devices>1 ORDER BY day DESC"),
    "failed_logins": ("Failed and unknown logins", "Who tried to get in.",
        "SELECT e.login_identifier, COUNT(*) AS failures, MAX(e.recorded_at) AS last_try FROM login_session_event e WHERE e.event_type='login_failed' AND e.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY e.login_identifier ORDER BY failures DESC"),
    "master_edits": ("Edits to master records", "Who changed patient / price / staff / catalog data (old and new values are in the audit log).",
        "SELECT CONCAT_WS(' ',s.given_name,s.family_name) AS staff, al.entity_type, COUNT(*) AS edits, MAX(al.recorded_at) AS last_edit FROM audit_log al JOIN staff s ON s.staff_id=al.recorded_by WHERE al.recorded_at>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY s.staff_id, al.entity_type ORDER BY edits DESC"),
}


def run_report(a, key: str, days: int = 30):
    title, desc, sql = REPORTS[key]
    n = sql.count("%s")
    return db.q(a, sql, tuple([days] * n))


# ---------------------------------------------------------------- audit viewer
def audit(a, entity=None, staff_id=None, day_from=None, day_to=None, action=None, page=0, size=60) -> list[dict]:
    where, params = [], []
    if entity:
        where.append("al.entity_type=%s"); params.append(entity)
    if action:
        where.append("al.action LIKE %s"); params.append(f"%{action}%")
    if staff_id:
        where.append("al.recorded_by=%s"); params.append(staff_id)
    if day_from:
        where.append("al.recorded_at>=%s"); params.append(timeutil.local_day_window(day_from)[0])
    if day_to:
        where.append("al.recorded_at<%s"); params.append(timeutil.local_day_window(day_to)[1])
    return db.q(a, f"""SELECT al.audit_log_id, al.recorded_at, al.action, al.entity_type, al.entity_id, al.old_values, al.new_values, al.reason,
        CONCAT_WS(' ',s.given_name,s.family_name) AS staff, al.recorded_by_role
        FROM audit_log al LEFT JOIN staff s ON s.staff_id=al.recorded_by {('WHERE ' + ' AND '.join(where)) if where else ''}
        ORDER BY al.audit_log_id DESC LIMIT %s OFFSET %s""", (*params, size, page * size))


def audit_entities(a) -> list[str]:
    return [r["entity_type"] for r in db.q(a, "SELECT DISTINCT entity_type FROM audit_log ORDER BY entity_type LIMIT 100") if r["entity_type"]]


# ---------------------------------------------------------------- price list (owner-only master, audited)
def prices(a) -> list[dict]:
    return db.q(a, "SELECT * FROM price_list ORDER BY category, description, effective_from DESC")


def save_price(a, price_list_id, **f):
    if f.get("price") is None or Decimal(str(f["price"])) < 0:
        raise db.RuleError("Enter a valid price.")
    if price_list_id:
        db.update_master(a, "price_list", "price_list_id", price_list_id, description=f.get("description"), price=f["price"],
                         effective_to=f.get("effective_to"), active=1 if f.get("active") else 0)
    else:
        if not f.get("item_code") or not f.get("description") or not f.get("effective_from"):
            raise db.RuleError("Item code, description and 'effective from' are required.")
        db.insert_master(a, "price_list", item_code=f["item_code"], description=f["description"], category=f.get("category") or "other",
                         price=f["price"], effective_from=f["effective_from"], effective_to=f.get("effective_to"), active=1)


# ---------------------------------------------------------------- settings
def settings(a) -> list[dict]:
    return db.q(a, "SELECT * FROM clinic_settings ORDER BY setting_key")


def save_setting(a, key, value):
    if value is None:
        raise db.RuleError("Enter a value.")
    db.update_master(a, "clinic_settings", "setting_key", key, setting_value=value)


# ---------------------------------------------------------------- staff
def staff(a) -> list[dict]:
    rows = db.q(a, """SELECT s.staff_id, s.display_code, s.principal_kind, s.given_name, s.family_name, s.phone, s.login_subject, s.active_to,
        EXISTS(SELECT 1 FROM staff_credential c WHERE c.staff_id=s.staff_id) AS has_password,
        (SELECT GROUP_CONCAT(r.role_code ORDER BY r.role_code) FROM staff_role sr JOIN role r ON r.role_id=sr.role_id WHERE sr.staff_id=s.staff_id
           AND sr.valid_from<=UTC_TIMESTAMP() AND (sr.valid_to IS NULL OR sr.valid_to>UTC_TIMESTAMP())) AS roles
        FROM staff s ORDER BY s.principal_kind, s.given_name""")
    return rows


def roles(a) -> list[dict]:
    return db.q(a, "SELECT role_id, role_code, role_name FROM role WHERE role_code<>'SYSTEM' ORDER BY role_code")


def create_staff(a, given, family, phone, login, role_codes: list[str], reg_no, password):
    if not given or not family or not login or not role_codes:
        raise db.RuleError("Name, login and at least one role are required.")
    if not password or len(password) < 8:
        raise db.RuleError("Set a first password of at least 8 characters.")
    r = db.call(a, "create_staff", given_name=given, family_name=family, phone=phone, login_subject=login,
                roles=",".join(role_codes), registration_number=reg_no)
    sid = r.first_value("staff_id") or db.scalar(a, "SELECT staff_id FROM staff WHERE login_subject=%s", (login,))
    if not sid:
        raise db.RuleError("Staff created but id not found; set the password from the list.")
    set_password(a, sid, password)


def set_password(a, staff_id, password):
    if not password or len(password) < 8:
        raise db.RuleError("Password must be at least 8 characters.")
    db.ex(a, "INSERT INTO staff_credential (staff_id, password_hash) VALUES (%s,%s) ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash)",
          (staff_id, passwords.hash_password(password)))


def add_role(a, staff_id, role_id):
    db.insert_master(a, "staff_role", staff_id=staff_id, role_id=role_id, valid_from=timeutil.utcnow(), granted_by=a.staff_id)


def end_role(a, staff_id, role_id):
    db.ex(a, "UPDATE staff_role SET valid_to=UTC_TIMESTAMP() WHERE staff_id=%s AND role_id=%s AND valid_to IS NULL", (staff_id, role_id))


# ---------------------------------------------------------------- delegations (owner grants limited approval authority)
def delegations(a) -> list[dict]:
    return db.q(a, """SELECT d.authority_delegation_id, d.scope, d.limit_amount, d.valid_from, d.valid_to,
        CONCAT_WS(' ',s.given_name,s.family_name) AS delegate,
        EXISTS(SELECT 1 FROM authority_delegation_revocation r WHERE r.authority_delegation_id=d.authority_delegation_id) AS revoked
        FROM authority_delegation d JOIN staff s ON s.staff_id=d.delegate_staff_id ORDER BY d.authority_delegation_id DESC LIMIT 40""")


def grant_delegation(a, delegate, scope, limit, valid_from, valid_to):
    if not delegate or not scope or not valid_from or not valid_to:
        raise db.RuleError("Choose the person, scope and validity dates.")
    try:
        db.call(a, "delegation", delegate_staff_id=delegate, scope=scope, limit_amount=limit, valid_from=valid_from, valid_to=valid_to)
    except db.NotReady:
        db.insert_event(a, "authority_delegation", delegate_staff_id=delegate, scope=scope, limit_amount=limit,
                        valid_from=valid_from, valid_to=valid_to)


def revoke_delegation(a, did, reason):
    try:
        db.call(a, "revoke_delegation", authority_delegation_id=did, reason=reason)
    except db.NotReady:
        db.insert_event(a, "authority_delegation_revocation", authority_delegation_id=did, reason=reason)


# ---------------------------------------------------------------- reconciliation pages (R2.4)
def reconciliation(a) -> dict:
    return {
        "visits_no_bill": db.q(a, "SELECT v.visit_id, v.occurred_at, p.display_code FROM visit v JOIN patient p ON p.patient_id=v.patient_id WHERE v.occurred_at<UTC_TIMESTAMP()-INTERVAL 1 DAY AND v.occurred_at>=UTC_TIMESTAMP()-INTERVAL 30 DAY AND NOT EXISTS (SELECT 1 FROM bill b WHERE b.visit_id=v.visit_id) ORDER BY v.visit_id DESC LIMIT 50"),
        "stock_no_dispensing": db.q(a, "SELECT sm.stock_movement_id, sm.recorded_at, sm.quantity_delta FROM stock_movement sm WHERE sm.movement_type='dispensed' AND NOT EXISTS (SELECT 1 FROM dispensing d WHERE d.dispensing_id=sm.dispensing_id) LIMIT 50"),
        "dispensed_no_billing": db.q(a, "SELECT sm.stock_movement_id, sm.recorded_at, sm.quantity_delta, sm.unit_price_snapshot FROM stock_movement sm JOIN dispensing d ON d.dispensing_id=sm.dispensing_id WHERE sm.movement_type='dispensed' AND d.counter_sale_id IS NULL AND NOT EXISTS (SELECT 1 FROM bill_line bl WHERE bl.source_stock_movement_id=sm.stock_movement_id) AND sm.recorded_at<UTC_TIMESTAMP()-INTERVAL 1 HOUR LIMIT 50"),
        "cash_differences": db.q(a, "SELECT d.recorded_at, CONCAT_WS(' ',s.given_name,s.family_name) AS pharmacist, d.declared_amount, c.counted_amount, c.counted_amount-d.declared_amount AS difference FROM cash_handover_count c JOIN cash_handover_declaration d ON d.cash_handover_declaration_id=c.cash_handover_declaration_id JOIN staff s ON s.staff_id=d.recorded_by WHERE c.counted_amount<>d.declared_amount ORDER BY d.cash_handover_declaration_id DESC LIMIT 30"),
        "unreviewed_critical": db.q(a, """SELECT lo.test_name, r.recorded_at FROM lab_report r JOIN lab_order lo ON lo.lab_order_id=r.lab_order_id
            WHERE (SELECT k.mark FROM lab_report_critical_mark k WHERE k.lab_report_id=r.lab_report_id ORDER BY k.lab_report_critical_mark_id DESC LIMIT 1)='critical'
            AND NOT EXISTS (SELECT 1 FROM lab_report_review w WHERE w.lab_report_id=r.lab_report_id) LIMIT 30"""),
        "backups": db.q(a, "SELECT recorded_at, backup_kind, encrypted, offsite_copy_confirmed, result FROM backup_run ORDER BY backup_run_id DESC LIMIT 8"),
    }
