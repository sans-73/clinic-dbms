"""Nurse data access (P5-4b): ward task list from finalized orders, dose logging, vitals + trend, medicine custody and the
R6.2 reconciliation (dispensed = administered + returned + handed to family), lab-report attachment."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta
from pathlib import Path

from .. import config, db, timeutil
from .common import in_clause
from ..constants import OBS_TYPES

_LATEST_ORDER = "(SELECT s.status FROM clinical_order_status s WHERE s.clinical_order_id=o.clinical_order_id ORDER BY s.clinical_order_status_id DESC LIMIT 1)"
_LATEST_ITEM = "(SELECT s.status FROM clinical_order_item_status s WHERE s.clinical_order_item_id=i.clinical_order_item_id ORDER BY s.clinical_order_item_status_id DESC LIMIT 1)"
_SUPPLY_OK = "s.reverses_order_item_supply_id IS NULL AND NOT EXISTS (SELECT 1 FROM order_item_supply r WHERE r.reverses_order_item_supply_id=s.order_item_supply_id)"


def ward_tasks(a) -> list[dict]:
    rows = db.q(a, f"""SELECT i.clinical_order_item_id, o.visit_id, e.short_stay_episode_id, p.patient_id, p.display_code,
        CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name,
        COALESCE(m.brand_name, i.free_text_medicine_name) AS medicine, i.medicine_id, i.dose_value, i.dose_unit, i.route,
        i.first_scheduled_at, i.repeat_interval_minutes, i.quantity_ordered, i.instructions,
        (SELECT MAX(d.occurred_at) FROM dose_administration d WHERE d.clinical_order_item_id=i.clinical_order_item_id AND d.status='given') AS last_given,
        (SELECT COUNT(*) FROM dose_administration d WHERE d.clinical_order_item_id=i.clinical_order_item_id AND d.status='given') AS n_given,
        COALESCE((SELECT SUM(s.quantity_allocated) FROM order_item_supply s WHERE s.clinical_order_item_id=i.clinical_order_item_id AND {_SUPPLY_OK}),0) AS supplied,
        COALESCE((SELECT SUM(d.quantity_consumed) FROM dose_administration d WHERE d.clinical_order_item_id=i.clinical_order_item_id),0) AS consumed
        FROM clinical_order_item i JOIN clinical_order o ON o.clinical_order_id=i.clinical_order_id
        JOIN visit v ON v.visit_id=o.visit_id JOIN patient p ON p.patient_id=v.patient_id
        JOIN short_stay_episode e ON e.visit_id=v.visit_id LEFT JOIN medicine m ON m.medicine_id=i.medicine_id
        WHERE i.item_kind='medication' AND {_LATEST_ORDER}='finalized' AND {_LATEST_ITEM}='active'
          AND NOT EXISTS (SELECT 1 FROM short_stay_closure c WHERE c.short_stay_episode_id=e.short_stay_episode_id)
          AND NOT EXISTS (SELECT 1 FROM visit_closure vc WHERE vc.visit_id=v.visit_id)""")
    now = timeutil.utcnow()
    for r in rows:
        iv = r["repeat_interval_minutes"]
        if r["last_given"] and iv:
            due = r["last_given"] + timedelta(minutes=iv)
        elif not r["last_given"]:
            due = r["first_scheduled_at"] or now
        else:
            due = None                                   # one-off already given
        r["next_due"] = due
        r["overdue"] = bool(due and due < now - timedelta(minutes=15))
        r["due_now"] = bool(due and due <= now + timedelta(minutes=15))
        r["remaining"] = r["supplied"] - r["consumed"]
    rows.sort(key=lambda r: (r["next_due"] is None, r["next_due"] or now))
    return rows


def item_for_dose(a, item_id: int) -> dict | None:
    it = db.one(a, """SELECT i.clinical_order_item_id, i.dose_value, i.dose_unit, i.route, i.instructions,
        COALESCE(m.brand_name, i.free_text_medicine_name) AS medicine, m.stock_unit, o.visit_id,
        CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name, p.display_code
        FROM clinical_order_item i JOIN clinical_order o ON o.clinical_order_id=i.clinical_order_id
        JOIN visit v ON v.visit_id=o.visit_id JOIN patient p ON p.patient_id=v.patient_id
        LEFT JOIN medicine m ON m.medicine_id=i.medicine_id WHERE i.clinical_order_item_id=%s""", (item_id,))
    if it:
        it["supplies"] = db.q(a, f"""SELECT s.order_item_supply_id, s.dispensing_id, s.quantity_allocated,
            s.quantity_allocated - COALESCE((SELECT SUM(d.quantity_consumed) FROM dose_administration d WHERE d.order_item_supply_id=s.order_item_supply_id),0) AS remaining
            FROM order_item_supply s WHERE s.clinical_order_item_id=%s AND {_SUPPLY_OK}""", (item_id,))
        it["history"] = db.q(a, """SELECT d.status, d.actual_dose_value, d.actual_dose_unit, d.actual_route, d.occurred_at, d.deviation_reason,
            CONCAT_WS(' ',s.given_name,s.family_name) AS nurse FROM dose_administration d JOIN staff s ON s.staff_id=d.recorded_by
            WHERE d.clinical_order_item_id=%s ORDER BY d.dose_administration_id DESC LIMIT 10""", (item_id,))
    return it


def record_dose(a, item_id, f: dict):
    it = item_for_dose(a, item_id)
    if not it:
        raise db.RuleError("Order item not found.")
    status = f["status"]
    qty = f.get("quantity_consumed")
    if status == "given" and (qty is None or float(qty) <= 0):
        qty = None
    if qty and not f.get("order_item_supply_id"):
        raise db.RuleError("Choose which supplied pack this dose came from.")
    od, ad = it["dose_value"], f.get("actual_dose_value")
    different = status == "given" and ((ad is not None and od is not None and ad != od) or
                                        (f.get("actual_route") and it["route"] and f["actual_route"] != it["route"]))
    if different and not f.get("deviation_reason"):
        raise db.RuleError("The dose or route differs from the order. Write the reason (the owner is alerted if it is above the order).")
    if status in ("missed", "refused", "delayed", "stopped") and not f.get("deviation_reason"):
        raise db.RuleError("Write the reason (why the dose was not given as ordered).")
    db.call(a, "administer_dose", clinical_order_item_id=item_id, order_item_supply_id=f.get("order_item_supply_id"),
            status=status, ordered_dose_value=od, ordered_dose_unit=it["dose_unit"], ordered_route=it["route"],
            actual_dose_value=ad if ad is not None else (od if status == "given" else None),
            actual_dose_unit=f.get("actual_dose_unit") or it["dose_unit"], actual_route=f.get("actual_route") or it["route"],
            quantity_consumed=qty, deviation_reason=f.get("deviation_reason"), deviation_authorized_by=f.get("authorized_by"),
            occurred_at=f.get("occurred_at"), request_uuid=f.get("request_uuid"))


# ---------------------------------------------------------------- vitals
def open_visits(a) -> list[dict]:
    return db.q(a, """SELECT v.visit_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS name,
        EXISTS(SELECT 1 FROM short_stay_episode e WHERE e.visit_id=v.visit_id) AS stay
        FROM visit v JOIN patient p ON p.patient_id=v.patient_id
        WHERE NOT EXISTS (SELECT 1 FROM visit_closure c WHERE c.visit_id=v.visit_id) ORDER BY v.visit_id DESC LIMIT 60""")


def vitals_trend(a, visit_id: int) -> dict:
    rows = db.q(a, """SELECT observation_type, numeric_value, unit, recorded_at FROM vitals_observation
        WHERE visit_id=%s AND numeric_value IS NOT NULL ORDER BY observation_seq DESC LIMIT 200""", (visit_id,))
    by: dict[str, list] = {}
    for r in reversed(rows):
        by.setdefault(r["observation_type"], []).append(r)
    return by


def record_vitals(a, visit_id: int, values: dict, episode_id=None):
    """values: {'bp_systolic':, 'bp_diastolic':, 'pulse':, ...}. BP rows share one observation_set_uuid (ER)."""
    pairs = [(k, v) for k, v in values.items() if v is not None]
    if not pairs:
        raise db.RuleError("Enter at least one reading.")
    if ("bp_systolic" in values and values["bp_systolic"] is not None) != ("bp_diastolic" in values and values["bp_diastolic"] is not None):
        raise db.RuleError("Blood pressure needs both the upper and lower number.")
    units = {"bp_systolic": "mmHg", "bp_diastolic": "mmHg", "pulse": "bpm", "temperature": "°C", "spo2": "%",
             "resp_rate": "/min", "weight": "kg", "height": "cm", "blood_glucose": "mg/dL"}
    set_uuid = str(uuid.uuid4())
    base = str(uuid.uuid4())
    with db.Session(a) as s:
        for n, (k, v) in enumerate(pairs):
            s.call("record_vitals", visit_id=visit_id, short_stay_episode_id=episode_id, observation_type=k,
                   numeric_value=v, unit=units.get(k), observation_set_uuid=set_uuid, request_uuid=f"{base[:30]}{n:02d}")


# ---------------------------------------------------------------- custody and reconciliation (R6.2)
def reconciliation(a, visit_id: int) -> list[dict]:
    """One row per ORDER-LINKED dispensing of the visit (scope rule in ER 5.2). remaining <> 0 blocks discharge."""
    rows = db.q(a, f"""SELECT d.dispensing_id, m.brand_name, m.stock_unit,
        COALESCE((SELECT -SUM(sm.quantity_delta) FROM stock_movement sm WHERE sm.dispensing_id=d.dispensing_id AND sm.movement_type='dispensed'),0) AS dispensed,
        COALESCE((SELECT SUM(x.quantity_consumed) FROM dose_administration x JOIN order_item_supply s ON s.order_item_supply_id=x.order_item_supply_id
                  WHERE s.dispensing_id=d.dispensing_id),0) AS administered,
        COALESCE((SELECT SUM(sm2.quantity_delta) FROM leftover_return_request lr JOIN leftover_return_decision ld ON ld.leftover_return_request_id=lr.leftover_return_request_id
                  JOIN stock_movement sm2 ON sm2.stock_movement_id=ld.stock_movement_id
                  WHERE lr.dispensing_id=d.dispensing_id AND ld.decision='accepted'),0) AS returned,
        COALESCE((SELECT SUM(ack.acknowledged_quantity) FROM custody_handoff ch JOIN custody_handoff_acknowledgement ack ON ack.custody_handoff_id=ch.custody_handoff_id
                  WHERE ch.dispensing_id=d.dispensing_id AND ch.handoff_purpose='leftover_to_family'),0) AS to_family,
        (SELECT COUNT(*) FROM custody_handoff ch2 WHERE ch2.dispensing_id=d.dispensing_id
           AND NOT EXISTS (SELECT 1 FROM custody_handoff_acknowledgement k WHERE k.custody_handoff_id=ch2.custody_handoff_id)) AS unacknowledged
        FROM dispensing d JOIN medicine m ON m.medicine_id=d.medicine_id
        WHERE EXISTS (SELECT 1 FROM order_item_supply s JOIN clinical_order_item i ON i.clinical_order_item_id=s.clinical_order_item_id
                      JOIN clinical_order o ON o.clinical_order_id=i.clinical_order_id WHERE s.dispensing_id=d.dispensing_id AND o.visit_id=%s)""", (visit_id,))
    for r in rows:
        r["remaining"] = r["dispensed"] - r["administered"] - r["returned"] - r["to_family"]
        r["balanced"] = r["remaining"] == 0 and not r["unacknowledged"]
    return rows


def attendants(a, visit_id: int) -> list[dict]:
    return db.q(a, "SELECT t.patient_attendant_id, t.name, t.relationship, t.phone FROM patient_attendant t JOIN visit v ON v.patient_id=t.patient_id WHERE v.visit_id=%s", (visit_id,))


def pending_acks(a) -> list[dict]:
    return db.q(a, """SELECT ch.custody_handoff_id, ch.dispensing_id, ch.quantity, ch.handoff_purpose, ch.recorded_at, m.brand_name,
        CONCAT_WS(' ',g.given_name,g.family_name) AS giver
        FROM custody_handoff ch JOIN dispensing d ON d.dispensing_id=ch.dispensing_id JOIN medicine m ON m.medicine_id=d.medicine_id
        JOIN staff g ON g.staff_id=ch.giver_staff_id
        WHERE ch.recipient_staff_id=%s AND NOT EXISTS (SELECT 1 FROM custody_handoff_acknowledgement k WHERE k.custody_handoff_id=ch.custody_handoff_id)""", (a.staff_id,))


def ack_handoff(a, handoff_id, qty, note=None):
    db.call(a, "ack_handoff", custody_handoff_id=handoff_id, acknowledged_quantity=qty, ack_method="self_logged_staff", witness_note=note)


def handoff_to_family(a, dispensing_id, attendant_id, qty, request_uuid):
    if not attendant_id:
        raise db.RuleError("Choose the family member receiving the medicine.")
    db.call(a, "custody_handoff", dispensing_id=dispensing_id, recipient_attendant_id=attendant_id, quantity=qty,
            handoff_purpose="leftover_to_family", request_uuid=request_uuid)


def ask_leftover(a, dispensing_id, qty, note):
    if not qty or float(qty) <= 0:
        raise db.RuleError("Enter the leftover quantity.")
    db.call(a, "ask_leftover", dispensing_id=dispensing_id, quantity=qty, condition_note=note)


def my_open_dispensings(a) -> list[dict]:
    """Dispensings handed to ME as nurse (supply_to_nurse) or any order-linked one on an open stay."""
    return db.q(a, """SELECT DISTINCT d.dispensing_id, o.visit_id, m.brand_name, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name
        FROM order_item_supply s JOIN dispensing d ON d.dispensing_id=s.dispensing_id JOIN clinical_order_item i ON i.clinical_order_item_id=s.clinical_order_item_id
        JOIN clinical_order o ON o.clinical_order_id=i.clinical_order_id JOIN visit v ON v.visit_id=o.visit_id JOIN patient p ON p.patient_id=v.patient_id
        JOIN medicine m ON m.medicine_id=d.medicine_id WHERE NOT EXISTS (SELECT 1 FROM visit_closure c WHERE c.visit_id=v.visit_id)
        ORDER BY d.dispensing_id DESC""")


# ---------------------------------------------------------------- lab reports in (nurse attaches)
def labs_waiting(a) -> list[dict]:
    return db.q(a, """SELECT lo.lab_order_id, lo.test_name, ol.name AS lab_name, v.visit_id,
        p.display_code, p.date_of_birth, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name,
        (SELECT ph.phone_number FROM patient_phone ph WHERE ph.patient_id=p.patient_id AND ph.valid_to IS NULL ORDER BY ph.phone_sequence DESC LIMIT 1) AS phone,
        (SELECT s.status FROM lab_order_status s WHERE s.lab_order_id=lo.lab_order_id ORDER BY s.lab_order_status_id DESC LIMIT 1) AS status,
        (SELECT COUNT(*) FROM lab_report r WHERE r.lab_order_id=lo.lab_order_id) AS reports
        FROM lab_order lo JOIN outside_lab ol ON ol.outside_lab_id=lo.outside_lab_id JOIN visit v ON v.visit_id=lo.visit_id JOIN patient p ON p.patient_id=v.patient_id
        WHERE (SELECT s.status FROM lab_order_status s WHERE s.lab_order_id=lo.lab_order_id ORDER BY s.lab_order_status_id DESC LIMIT 1)<>'cancelled'
        ORDER BY lo.lab_order_id DESC LIMIT 60""")


def lab_status(a, lab_order_id, status):
    db.call(a, "lab_status", lab_order_id=lab_order_id, status=status, reason=None)


def save_upload(filename: str, data: bytes) -> tuple[str, str]:
    """Store the report file as received; return (relative path, sha256). The hash is what makes it tamper-evident (R7.3)."""
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise db.RuleError(f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    if not data:
        raise db.RuleError("Choose the report file.")
    ext = Path(filename or "").suffix.lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg"):
        raise db.RuleError("Only PDF, PNG or JPG reports are accepted.")
    digest = hashlib.sha256(data).hexdigest()
    sub = config.UPLOAD_DIR / "lab"
    sub.mkdir(parents=True, exist_ok=True)
    name = f"{digest[:16]}-{uuid.uuid4().hex[:8]}{ext}"
    (sub / name).write_bytes(data)
    return f"lab/{name}", digest


def attach_report(a, lab_order_id, data: bytes, filename, source, idents: list[dict], supersedes=None, request_uuid=None):
    good = {i["kind"] for i in idents if i.get("kind") and i.get("value") and i.get("matches")}
    if len(good) < 2:
        raise db.RuleError("Two different identifiers on the report must match the order before it can be attached.")
    path, digest = save_upload(filename, data)
    args = dict(lab_order_id=lab_order_id, file_path=path, content_hash=digest, source=source,
                supersedes_lab_report_id=supersedes, request_uuid=request_uuid)
    import json
    args["identifiers_json"] = json.dumps(idents)
    for n, i in enumerate(idents[:2], 1):
        args[f"identifier_{n}_kind"], args[f"identifier_{n}_value"], args[f"identifier_{n}_matches"] = i.get("kind"), i.get("value"), 1 if i.get("matches") else 0
    db.call(a, "attach_lab_report", **args)


def resolve_upload(rel: str) -> Path | None:
    """Path-traversal safe lookup inside UPLOAD_DIR."""
    if not rel or not re.fullmatch(r"[A-Za-z0-9_\-/\.]+", rel) or ".." in rel:
        return None
    p = (config.UPLOAD_DIR / rel).resolve()
    return p if str(p).startswith(str(config.UPLOAD_DIR.resolve())) and p.is_file() else None
