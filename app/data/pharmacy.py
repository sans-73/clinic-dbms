"""Pharmacy data access (P5-4a): stock views derived from the ledger (G3), receiving, dispensing, counter sales,
leftover decisions, catalog review, cash handover. Cost price is only ever selected for OWNER / PHARMACY (G10)."""
from __future__ import annotations

import random
import string
from datetime import timedelta

from .. import db, timeutil
from .common import in_clause, med_label

_ONHAND = "COALESCE((SELECT SUM(sm.quantity_delta) FROM stock_movement sm WHERE sm.stock_batch_id=b.stock_batch_id),0)"


def can_see_cost(a: db.Actor) -> bool:
    return a.role in ("OWNER", "PHARMACY")


def stock_by_medicine(a) -> list[dict]:
    return db.q(a, """SELECT m.medicine_id, m.brand_name, m.generic_display_name, m.strength_value, m.strength_unit, m.dosage_form,
        m.stock_unit, m.schedule_class, m.returnable,
        COALESCE(SUM(CASE WHEN b.expiry_date >= UTC_DATE() THEN sm.quantity_delta END),0) AS on_hand,
        COALESCE(SUM(CASE WHEN b.expiry_date <  UTC_DATE() THEN sm.quantity_delta END),0) AS expired_qty,
        COUNT(DISTINCT b.stock_batch_id) AS batches
        FROM medicine m LEFT JOIN stock_batch b ON b.medicine_id=m.medicine_id
        LEFT JOIN stock_movement sm ON sm.stock_batch_id=b.stock_batch_id
        WHERE m.active=1 GROUP BY m.medicine_id ORDER BY m.brand_name""")


def batches(a, medicine_id: int | None = None, only_stock=False) -> list[dict]:
    cost = ", b.cost_price" if can_see_cost(a) else ""
    where = "WHERE b.medicine_id=%s" if medicine_id else ""
    rows = db.q(a, f"""SELECT b.stock_batch_id, b.medicine_id, b.batch_code, b.expiry_date, b.mrp{cost}, m.brand_name,
        m.stock_unit, sp.name AS supplier, sp.phone AS supplier_phone, {_ONHAND} AS on_hand
        FROM stock_batch b JOIN medicine m ON m.medicine_id=b.medicine_id LEFT JOIN supplier sp ON sp.supplier_id=b.supplier_id
        {where} ORDER BY b.expiry_date, b.stock_batch_id""", ([medicine_id] if medicine_id else []))
    from datetime import date
    today = date.today()
    near = int(db.setting_num(a, "expiry_alert_days", 60))
    for r in rows:
        r["expired"] = r["expiry_date"] < today
        r["near"] = (not r["expired"]) and (r["expiry_date"] - today).days <= near
    return [r for r in rows if r["on_hand"] > 0] if only_stock else rows


def fefo(a, medicine_id: int) -> list[dict]:
    return [b for b in batches(a, medicine_id, only_stock=True) if not b["expired"]]


def suppliers(a) -> list[dict]:
    return db.q(a, "SELECT supplier_id, name, phone FROM supplier ORDER BY name")


def restock_demand(a) -> list[dict]:
    return db.q(a, """SELECT COALESCE(m.brand_name, pi.free_text_medicine_name) AS name, COUNT(*) AS times,
        MAX(m.medicine_id) IS NOT NULL AS in_catalog,
        COALESCE(MAX((SELECT SUM(sm.quantity_delta) FROM stock_batch b JOIN stock_movement sm ON sm.stock_batch_id=b.stock_batch_id
                      WHERE b.medicine_id=m.medicine_id AND b.expiry_date>=UTC_DATE())),0) AS stock_now
        FROM prescription_line pl
        JOIN prescription_item pi ON pi.prescription_item_id=(SELECT MAX(x.prescription_item_id) FROM prescription_item x WHERE x.prescription_line_id=pl.prescription_line_id)
        LEFT JOIN medicine m ON m.medicine_id=pi.medicine_id
        WHERE (SELECT s.status FROM prescription_line_status s WHERE s.prescription_line_id=pl.prescription_line_id ORDER BY s.prescription_line_status_id DESC LIMIT 1)='bought_outside'
        GROUP BY name ORDER BY times DESC LIMIT 20""")


def receive_stock(a, **f):
    for k in ("medicine_id", "batch_code", "expiry_date", "quantity", "mrp", "cost_price"):
        if f.get(k) in (None, ""):
            raise db.RuleError(f"Please fill: {k.replace('_', ' ')}.")
    db.call(a, "receive_stock", **f)


def request_adjustment(a, batch_id, delta, kind, reason):
    if not reason:
        raise db.RuleError("A reason is mandatory for stock adjustments and write-offs.")
    db.call(a, "stock_adjustment", stock_batch_id=batch_id, quantity_delta=delta, movement_type=kind, reason=reason)


# ---------------------------------------------------------------- dispensing
def pending_prescriptions(a, text: str = "") -> list[dict]:
    like = f"%{text}%"
    extra = "AND (p.display_code LIKE %s OR p.given_name LIKE %s OR p.family_name LIKE %s)" if text else ""
    rows = db.q(a, f"""SELECT pi.prescription_item_id, pi.medicine_id, pi.prescription_line_id,
        COALESCE(m.brand_name, pi.free_text_medicine_name) AS name, m.stock_unit, m.schedule_class,
        pi.quantity_prescribed, pi.chosen_dose_text, pi.chosen_schedule, pi.duration_days,
        v.visit_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name,
        COALESCE((SELECT -SUM(sm.quantity_delta) FROM dispensing d JOIN prescription_item x ON x.prescription_item_id=d.prescription_item_id
                  JOIN stock_movement sm ON sm.dispensing_id=d.dispensing_id AND sm.movement_type='dispensed'
                  WHERE x.prescription_line_id=pi.prescription_line_id),0) AS dispensed
        FROM prescription_item pi
        JOIN prescription_version pv ON pv.prescription_version_id=pi.prescription_version_id
        JOIN prescription pr ON pr.prescription_id=pv.prescription_id
        JOIN visit v ON v.visit_id=pr.visit_id JOIN patient p ON p.patient_id=v.patient_id
        LEFT JOIN medicine m ON m.medicine_id=pi.medicine_id
        WHERE pv.version_number=(SELECT MAX(v2.version_number) FROM prescription_version v2 WHERE v2.prescription_id=pr.prescription_id)
          AND pi.item_change<>'discontinued' AND pi.medicine_id IS NOT NULL
          AND (SELECT s.status FROM prescription_line_status s WHERE s.prescription_line_id=pi.prescription_line_id
               ORDER BY s.prescription_line_status_id DESC LIMIT 1)='prescribed'
          AND NOT EXISTS (SELECT 1 FROM visit_closure c WHERE c.visit_id=v.visit_id)
          {extra}
        ORDER BY v.visit_id DESC, pi.prescription_item_id LIMIT 80""", (like, like, like) if text else ())
    out = []
    for r in rows:
        q = r["quantity_prescribed"]
        r["remaining"] = (q - r["dispensed"]) if q is not None else None
        if r["remaining"] is None or r["remaining"] > 0:
            out.append(r)
    return out


def order_items_for(a, visit_id: int, medicine_id: int) -> list[dict]:
    return db.q(a, """SELECT i.clinical_order_item_id, i.item_no, i.dose_value, i.dose_unit, i.route, i.quantity_ordered
        FROM clinical_order_item i JOIN clinical_order o ON o.clinical_order_id=i.clinical_order_id
        WHERE o.visit_id=%s AND i.medicine_id=%s AND i.item_kind='medication'
          AND (SELECT s.status FROM clinical_order_item_status s WHERE s.clinical_order_item_id=i.clinical_order_item_id
               ORDER BY s.clinical_order_item_status_id DESC LIMIT 1)='active'""", (visit_id, medicine_id))


def dispense(a, source_type, medicine_id, quantity, request_uuid=None, link_order_item=None, **src):
    if not quantity or float(quantity) <= 0:
        raise db.RuleError("Enter a quantity above zero.")
    r = db.call(a, "dispense", source_type=source_type, medicine_id=medicine_id, quantity=quantity,
                request_uuid=request_uuid, **src)
    did = r.first_value("dispensing_id")
    if link_order_item and did:
        db.call(a, "supply_to_order", clinical_order_item_id=link_order_item, dispensing_id=did, quantity_allocated=quantity)
    return did


def new_counter_customer(a, phone, name) -> int:
    if phone:
        ex = db.one(a, "SELECT counter_customer_id FROM counter_customer WHERE phone=%s LIMIT 1", (phone,))
        if ex:
            return ex["counter_customer_id"]
    temp = None if phone else "CC-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return db.insert_master(a, "counter_customer", phone=phone, temporary_code=temp, display_name=name)


def start_counter_sale(a, customer_id) -> int:
    try:
        code = db.call(a, "next_code", kind="counter_sale").first_value("code")
    except db.RuleError:
        code = None
    code = code or "CS-" + timeutil.utcnow().strftime("%y%m%d%H%M%S") + "".join(random.choices(string.digits, k=3))
    return db.insert_event(a, "counter_sale", counter_customer_id=customer_id, sale_number=code)


def add_outside_prescription(a, customer_id, prescriber, reg_no, rx_date, photo_path=None) -> int:
    if not prescriber or not reg_no:
        raise db.RuleError("Doctor name and registration number are required for an outside prescription.")
    return db.insert_event(a, "outside_prescription", counter_customer_id=customer_id, prescriber_name=prescriber,
                           prescriber_registration_no=reg_no, prescription_date=rx_date or timeutil.utcnow().date(),
                           photo_path=photo_path)


def counter_sales(a, limit=30) -> list[dict]:
    return db.q(a, f"""SELECT cs.counter_sale_id, cs.sale_number, cs.recorded_at, cc.display_name, cc.phone, cc.temporary_code,
        {_SALE_TOTAL} AS total,
        COALESCE((SELECT SUM(CASE WHEN pm.corrects_id IS NULL THEN pm.amount ELSE -pm.amount END) FROM counter_sale_payment pm WHERE pm.counter_sale_id=cs.counter_sale_id),0) AS paid
        FROM counter_sale cs JOIN counter_customer cc ON cc.counter_customer_id=cs.counter_customer_id
        ORDER BY cs.counter_sale_id DESC LIMIT %s""", (limit,))


_SALE_TOTAL = """COALESCE((SELECT SUM(-sm.quantity_delta*sm.unit_price_snapshot) FROM dispensing d JOIN stock_movement sm ON sm.dispensing_id=d.dispensing_id
   WHERE d.counter_sale_id=cs.counter_sale_id AND sm.movement_type='dispensed'),0)
 - COALESCE((SELECT SUM(sm2.quantity_delta*sm2.unit_price_snapshot) FROM counter_sale_return r JOIN stock_movement sm2 ON sm2.stock_movement_id=r.returned_stock_movement_id
   WHERE r.counter_sale_id=cs.counter_sale_id),0)"""


def counter_sale(a, sale_id: int) -> dict | None:
    s = db.one(a, f"""SELECT cs.counter_sale_id, cs.sale_number, cs.counter_customer_id, cc.display_name, cc.phone, cc.temporary_code,
        {_SALE_TOTAL} AS total FROM counter_sale cs JOIN counter_customer cc ON cc.counter_customer_id=cs.counter_customer_id
        WHERE cs.counter_sale_id=%s""", (sale_id,))
    if not s:
        return None
    s["lines"] = db.q(a, """SELECT d.dispensing_id, d.source_type, m.brand_name, m.stock_unit,
        -SUM(sm.quantity_delta) AS qty, MAX(sm.unit_price_snapshot) AS unit_price, -SUM(sm.quantity_delta*sm.unit_price_snapshot) AS amount
        FROM dispensing d JOIN medicine m ON m.medicine_id=d.medicine_id
        JOIN stock_movement sm ON sm.dispensing_id=d.dispensing_id AND sm.movement_type='dispensed'
        WHERE d.counter_sale_id=%s GROUP BY d.dispensing_id, d.source_type, m.brand_name, m.stock_unit""", (sale_id,))
    s["payments"] = db.q(a, "SELECT amount, method, receipt_number, recorded_at, corrects_id FROM counter_sale_payment WHERE counter_sale_id=%s ORDER BY counter_sale_payment_id", (sale_id,))
    s["paid"] = sum((p["amount"] if not p["corrects_id"] else -p["amount"]) for p in s["payments"]) if s["payments"] else 0
    s["balance"] = (s["total"] or 0) - s["paid"]
    s["prescriptions"] = db.q(a, "SELECT prescriber_name, prescriber_registration_no, prescription_date FROM outside_prescription WHERE counter_customer_id=%s ORDER BY outside_prescription_id DESC LIMIT 3", (s["counter_customer_id"],))
    return s


def counter_payment(a, sale_id, amount, method, request_uuid=None):
    if not amount or float(amount) <= 0:
        raise db.RuleError("Enter the amount received.")
    r = db.call(a, "counter_sale_payment", counter_sale_id=sale_id, amount=amount, method=method, request_uuid=request_uuid)
    return r.first_value("receipt_number")


def counter_return(a, sale_id, dispensing_id, quantity, reason):
    db.call(a, "counter_sale_return", counter_sale_id=sale_id, dispensing_id=dispensing_id, quantity=quantity, reason=reason)


# ---------------------------------------------------------------- leftovers (pharmacist decides)
def pending_leftovers(a) -> list[dict]:
    return db.q(a, """SELECT r.leftover_return_request_id, r.dispensing_id, r.quantity, r.condition_note, r.recorded_at,
        m.brand_name, m.returnable, m.stock_unit,
        (SELECT CONCAT_WS(' ',p.given_name,p.family_name) FROM prescription_item pi JOIN prescription_version pv ON pv.prescription_version_id=pi.prescription_version_id
           JOIN prescription pr ON pr.prescription_id=pv.prescription_id JOIN visit v ON v.visit_id=pr.visit_id JOIN patient p ON p.patient_id=v.patient_id
           WHERE pi.prescription_item_id=d.prescription_item_id) AS patient_name,
        CONCAT_WS(' ',s.given_name,s.family_name) AS asked_by
        FROM leftover_return_request r JOIN dispensing d ON d.dispensing_id=r.dispensing_id JOIN medicine m ON m.medicine_id=d.medicine_id
        JOIN staff s ON s.staff_id=r.recorded_by
        WHERE NOT EXISTS (SELECT 1 FROM leftover_return_decision x WHERE x.leftover_return_request_id=r.leftover_return_request_id)
        ORDER BY r.leftover_return_request_id""")


def decide_leftover(a, req_id, decision, reason):
    if decision not in ("accepted", "refused"):
        raise db.RuleError("Choose accept or refuse.")
    if not reason:
        raise db.RuleError("Give a reason for your decision.")
    db.call(a, "decide_leftover", leftover_return_request_id=req_id, decision=decision, decision_reason=reason)


# ---------------------------------------------------------------- catalog review
def review_queue(a) -> list[dict]:
    return db.q(a, """SELECT q.catalog_review_queue_id, q.submitted_name, q.recorded_at, pi.prescription_item_id,
        (SELECT COUNT(*) FROM catalog_review_queue q2 WHERE LOWER(q2.submitted_name)=LOWER(q.submitted_name)) AS times
        FROM catalog_review_queue q JOIN prescription_item pi ON pi.prescription_item_id=q.prescription_item_id
        WHERE NOT EXISTS (SELECT 1 FROM catalog_review_event e WHERE e.catalog_review_queue_id=q.catalog_review_queue_id)
        ORDER BY q.catalog_review_queue_id""")


def review_resolve(a, qid, action, medicine_id=None, note=None):
    if action == "mapped" and not medicine_id:
        raise db.RuleError("Choose the catalog medicine to map to.")
    db.insert_event(a, "catalog_review_event", catalog_review_queue_id=qid, review_action=action,
                    resolved_medicine_id=medicine_id, resolution_note=note)


def add_medicine(a, qid, **f) -> int:
    if not f.get("brand_name") or not f.get("stock_unit"):
        raise db.RuleError("Brand name and stock unit (tablet, ml, vial…) are required.")
    mid = db.insert_master(a, "medicine", active=1, **f)
    review_resolve(a, qid, "added_to_catalog", mid, "Added from review queue")
    return mid


# ---------------------------------------------------------------- cash handover (pharmacist declares)
def cash_expected(a, staff_id, start, end):
    r = db.one(a, """SELECT
        COALESCE((SELECT SUM(CASE WHEN corrects_id IS NULL THEN amount ELSE -amount END) FROM counter_sale_payment
                  WHERE method='cash' AND recorded_by=%s AND recorded_at>=%s AND recorded_at<%s),0)
      - COALESCE((SELECT SUM(amount) FROM counter_sale_refund WHERE method='cash' AND recorded_by=%s AND recorded_at>=%s AND recorded_at<%s),0) AS expected""",
               (staff_id, start, end, staff_id, start, end))
    return r["expected"] if r else 0


def my_declarations(a) -> list[dict]:
    return db.q(a, """SELECT d.cash_handover_declaration_id, d.period_start, d.period_end, d.declared_amount, d.note,
        (SELECT c.counted_amount FROM cash_handover_count c WHERE c.cash_handover_declaration_id=d.cash_handover_declaration_id) AS counted
        FROM cash_handover_declaration d WHERE d.recorded_by=%s ORDER BY d.cash_handover_declaration_id DESC LIMIT 15""", (a.staff_id,))


def declare_cash(a, start, end, amount, note):
    if amount is None or start is None or end is None:
        raise db.RuleError("Period and amount are required.")
    db.call(a, "declare_cash", period_start=start, period_end=end, declared_amount=amount, note=note)


def open_declarations(a) -> list[dict]:
    """Accountant side: declarations not yet counted, with the derived expected amount."""
    rows = db.q(a, """SELECT d.cash_handover_declaration_id, d.period_start, d.period_end, d.declared_amount, d.note, d.recorded_by,
        CONCAT_WS(' ',s.given_name,s.family_name) AS pharmacist
        FROM cash_handover_declaration d JOIN staff s ON s.staff_id=d.recorded_by
        WHERE NOT EXISTS (SELECT 1 FROM cash_handover_count c WHERE c.cash_handover_declaration_id=d.cash_handover_declaration_id)
        ORDER BY d.cash_handover_declaration_id""")
    for r in rows:
        r["expected"] = cash_expected(a, r["recorded_by"], r["period_start"], r["period_end"])
    return rows


def count_cash(a, decl_id, counted, note):
    if counted is None:
        raise db.RuleError("Enter the amount you counted.")
    db.call(a, "count_cash", cash_handover_declaration_id=decl_id, counted_amount=counted, note=note)
