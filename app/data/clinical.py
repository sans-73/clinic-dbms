"""Doctor-side data access (P5-3): visit summary, prescribing, orders, labs, notes, sign-off.
Write-path policy: multi-row / rule-heavy actions use P3-3 procedures; single-row doctor-authored events (note, diagnosis,
sign-off, override) are direct event inserts."""
from __future__ import annotations

import json
from decimal import Decimal

from .. import db, timeutil
from .common import PNAME, current_allergies, in_clause, visit_brief
from . import patients as P


# ---------------------------------------------------------------- doctor queue
def my_queue(a: db.Actor) -> list[dict]:
    rows = P.queue(a)
    mine = db.q(a, "SELECT DISTINCT x.visit_id FROM visit_doctor_assignment x WHERE x.doctor_staff_id=%s", (a.staff_id,))
    ids = {r["visit_id"] for r in mine}
    out = [r for r in rows if r["visit_id"] in ids and not r["closed"]]
    order = {"With Doctor": 0, "Waiting": 1, "Arrived": 2}
    out.sort(key=lambda r: (order.get(r["status"], 5), r["visit_id"]))
    return out


# ---------------------------------------------------------------- summary
def visit_summary(a: db.Actor, vid: int) -> dict | None:
    v = visit_brief(a, vid)
    if not v:
        return None
    pid = v["patient_id"]
    vitals = db.q(a, """SELECT o.observation_type, o.numeric_value, o.text_value, o.unit, o.recorded_at, o.observation_set_uuid
        FROM vitals_observation o WHERE o.visit_id=%s ORDER BY o.observation_seq DESC LIMIT 40""", (vid,))
    latest = {}
    for r in vitals:
        latest.setdefault(r["observation_type"], r)
    return {
        "v": v, "allergies": current_allergies(a, pid),
        "blood_group": db.one(a, "SELECT abo_group, rh_factor FROM patient_blood_group WHERE patient_id=%s ORDER BY patient_blood_group_id DESC LIMIT 1", (pid,)),
        "vitals": latest,
        "notes": db.q(a, "SELECT n.clinical_note_id, n.note_text, n.sharing_classification, n.recorded_at, n.corrects_id, "
                         "CONCAT_WS(' ',s.given_name,s.family_name) AS author FROM clinical_note n JOIN staff s ON s.staff_id=n.recorded_by "
                         "WHERE n.visit_id=%s ORDER BY n.clinical_note_id DESC", (vid,)),
        "diagnoses": db.q(a, "SELECT diagnosis_text, diagnosis_code, recorded_at FROM diagnosis WHERE visit_id=%s ORDER BY diagnosis_id DESC", (vid,)),
        "rx": current_prescription(a, vid),
        "labs": lab_orders(a, "lo.visit_id=%s", (vid,)),
        "orders": clinical_orders(a, vid),
        "signoff": db.one(a, "SELECT discharge_instructions, recorded_at FROM clinical_signoff WHERE visit_id=%s ORDER BY clinical_signoff_id DESC LIMIT 1", (vid,)),
        "history": db.q(a, """SELECT v2.visit_id, v2.occurred_at,
            (SELECT d.diagnosis_text FROM diagnosis d WHERE d.visit_id=v2.visit_id ORDER BY d.diagnosis_id DESC LIMIT 1) AS diagnosis
            FROM visit v2 WHERE v2.patient_id=%s AND v2.visit_id<>%s ORDER BY v2.visit_id DESC LIMIT 6""", (pid, vid)),
    }


def add_note(a, vid, text, sharing):
    if not text:
        raise db.RuleError("Write the note first.")
    db.insert_event(a, "clinical_note", visit_id=vid, note_text=text,
                    sharing_classification=sharing if sharing in ("shareable", "not_for_sharing") else "shareable")


def add_diagnosis(a, vid, text, code=None):
    if not text:
        raise db.RuleError("Write the diagnosis first.")
    db.insert_event(a, "diagnosis", visit_id=vid, diagnosis_text=text, diagnosis_code=code)


def sign_off(a, vid, instructions):
    db.insert_event(a, "clinical_signoff", visit_id=vid, discharge_instructions=instructions)


def emergency_override(a, vid, justification):
    if not justification:
        raise db.RuleError("A clinical justification is required.")
    oid = db.insert_event(a, "clinical_emergency_override", visit_id=vid, override_type="financial_gate_bypass",
                          authorized_by_staff_id=a.staff_id, clinical_justification=justification)
    try:  # R10.2: immediate interrupt. Remove if P3-5 already raises it (guide A-12).
        db.call(a, "raise_alert", type="CLINICAL_EMERGENCY_OVERRIDE", severity="INTERRUPT",
                ref_table="clinical_emergency_override", ref_id=oid, message="Clinical emergency override recorded")
    except db.RuleError:
        pass
    return oid


# ---------------------------------------------------------------- medicines: type-ahead + allergy
def _norm_suggestion(r: dict) -> dict:
    g = lambda *ks: next((r[k] for k in ks if k in r and r[k] is not None), None)
    stock = g("stock", "in_stock", "on_hand", "quantity", "qty")
    state = (str(g("state", "colour", "color", "stock_state") or "")).lower()
    if state not in ("green", "blue", "red"):
        state = "green" if stock and Decimal(str(stock)) > 0 else "blue"
    label = g("label", "stock_label") or {"green": f"In stock: {stock:g}" if isinstance(stock, (int, float, Decimal)) else "In stock",
                                          "blue": "Out of stock", "red": "Not in catalog"}[state]
    name = g("brand_name", "name", "medicine_name", "display_name")
    return {"medicine_id": g("medicine_id", "id"), "name": name, "generic": g("generic_display_name", "generic", "generic_name"),
            "strength": " ".join(str(x) for x in (g("strength_value"), g("strength_unit")) if x),
            "form": g("dosage_form"), "schedule_class": g("schedule_class"), "state": state, "label": str(label),
            "dose": g("default_dose_text", "default_dose", "dose"), "schedule": g("default_schedule", "schedule"),
            "food": g("default_food_instruction", "food_instruction", "food"), "days": g("default_duration_days", "duration_days"),
            "stock_unit": g("stock_unit")}


def suggest_medicines(a, prefix: str, patient_id: int | None) -> list[dict]:
    prefix = (prefix or "").strip()
    if len(prefix) < 2:
        return []
    try:
        res = db.call(a, "medicine_suggest", prefix=prefix, patient_id=patient_id)
        rows = [_norm_suggestion(r) for r in res.rows]
    except db.NotReady:                                   # P3-3 not built yet: same behaviour in plain SQL
        like = f"%{prefix}%"
        raw = db.q(a, """SELECT m.medicine_id, m.brand_name, m.generic_display_name, m.strength_value, m.strength_unit,
            m.dosage_form, m.stock_unit, m.schedule_class, m.default_dose_text, m.default_schedule,
            m.default_food_instruction, m.default_duration_days,
            COALESCE((SELECT SUM(sm.quantity_delta) FROM stock_batch b JOIN stock_movement sm ON sm.stock_batch_id=b.stock_batch_id
                      WHERE b.medicine_id=m.medicine_id AND b.expiry_date >= UTC_DATE()),0) AS stock
            FROM medicine m WHERE m.active=1 AND (m.brand_name LIKE %s OR m.generic_display_name LIKE %s)
            ORDER BY (m.brand_name LIKE %s) DESC, m.brand_name LIMIT 12""", (like, like, prefix + "%"))
        rows = [_norm_suggestion(r) for r in raw]
    for r in rows:
        r["allergy_warning"] = allergy_warnings(a, patient_id, r["medicine_id"]) if patient_id and r["medicine_id"] else []
    return rows


def allergy_warnings(a, patient_id: int, medicine_id: int) -> list[str]:
    """R4.4 generic-name match: ingredient link (works for combination drugs) or allergen text inside the names."""
    rows = db.q(a, """SELECT DISTINCT COALESCE(i.ingredient_name, pa.allergen_text) AS allergen
        FROM patient_allergy pa LEFT JOIN ingredient i ON i.ingredient_id=pa.ingredient_id, medicine m
        WHERE pa.patient_id=%s AND m.medicine_id=%s
          AND COALESCE((SELECT e.status FROM patient_allergy_status_event e WHERE e.patient_allergy_id=pa.patient_allergy_id
                        ORDER BY e.patient_allergy_status_event_id DESC LIMIT 1),'confirmed') IN ('confirmed','reactivated')
          AND ( (pa.ingredient_id IS NOT NULL AND pa.ingredient_id IN (SELECT mi.ingredient_id FROM medicine_ingredient mi WHERE mi.medicine_id=m.medicine_id))
             OR (pa.allergen_text IS NOT NULL AND pa.allergen_text<>'' AND (LOWER(m.generic_display_name) LIKE CONCAT('%%',LOWER(pa.allergen_text),'%%')
                                                  OR LOWER(m.brand_name) LIKE CONCAT('%%',LOWER(pa.allergen_text),'%%'))))""",
                  (patient_id, medicine_id))
    return [r["allergen"] for r in rows if r["allergen"]]


# ---------------------------------------------------------------- prescriptions
def current_prescription(a, vid: int) -> dict | None:
    head = db.one(a, """SELECT pr.prescription_id, pv.prescription_version_id, pv.version_number, pv.change_reason, pv.recorded_at
        FROM prescription pr JOIN prescription_version pv ON pv.prescription_id=pr.prescription_id
        WHERE pr.visit_id=%s ORDER BY pr.prescription_id DESC, pv.version_number DESC LIMIT 1""", (vid,))
    if not head:
        return None
    head["items"] = db.q(a, """SELECT pi.prescription_item_id, pi.prescription_line_id, pi.item_change, pi.medicine_id,
        pi.free_text_medicine_name, pi.chosen_dose_text, pi.chosen_schedule, pi.food_instruction, pi.duration_days,
        pi.quantity_prescribed, pl.line_no, COALESCE(m.brand_name, pi.free_text_medicine_name) AS medicine_name,
        m.generic_display_name, m.strength_value, m.strength_unit, m.dosage_form,
        (SELECT s.status FROM prescription_line_status s WHERE s.prescription_line_id=pi.prescription_line_id
           ORDER BY s.prescription_line_status_id DESC LIMIT 1) AS line_status
        FROM prescription_item pi JOIN prescription_line pl ON pl.prescription_line_id=pi.prescription_line_id
        LEFT JOIN medicine m ON m.medicine_id=pi.medicine_id
        WHERE pi.prescription_version_id=%s ORDER BY pl.line_no""", (head["prescription_version_id"],))
    head["active_items"] = [i for i in head["items"] if i["item_change"] != "discontinued"]
    versions = db.q(a, "SELECT version_number, change_reason, recorded_at FROM prescription_version WHERE prescription_id=%s ORDER BY version_number",
                    (head["prescription_id"],))
    head["versions"] = versions
    return head


def save_prescription(a: db.Actor, vid: int, rows: list[dict], change_reason: str | None, request_uuid: str | None):
    """rows: dicts with keys line_id, medicine_id, free_text, dose, schedule, food, days, qty, remove, confirm, override.
    Server-side safety (G4): free-text needs allergy confirmation; a catalog allergy warning needs an override reason."""
    v = visit_brief(a, vid)
    if not v:
        raise db.RuleError("Visit not found.")
    if v["closed"]:
        raise db.RuleError("This visit is closed. A closed visit cannot get a new prescription.")
    shown = ", ".join(x["allergen"] for x in current_allergies(a, v["patient_id"])) or "none recorded"
    prev = current_prescription(a, vid)
    prev_by_line = {i["prescription_line_id"]: i for i in (prev["items"] if prev else [])}
    clean = []
    for r in rows:
        name_ok = r.get("medicine_id") or r.get("free_text")
        if not name_ok:
            continue
        if r.get("medicine_id") and r.get("free_text"):
            r["free_text"] = None
        item = dict(r)
        item["confirmation_type"] = None
        if not r.get("medicine_id"):
            item["confirmation_type"] = "free_text_medicine"
            if not r.get("confirm"):
                raise db.RuleError(f"Confirm the patient's allergies before adding '{r['free_text']}' (recorded allergies: {shown}).")
        else:
            warn = allergy_warnings(a, v["patient_id"], int(r["medicine_id"]))
            if warn:
                item["confirmation_type"] = "generic_name_warning"
                if not r.get("override"):
                    raise db.RuleError(f"ALLERGY WARNING: patient is allergic to {', '.join(warn)}. Give an override reason or remove the medicine.")
        if r.get("line_id") and int(r["line_id"]) in prev_by_line:
            old = prev_by_line[int(r["line_id"])]
            same = (str(old["medicine_id"] or "") == str(r.get("medicine_id") or "") and
                    (old["free_text_medicine_name"] or "") == (r.get("free_text") or "") and
                    (old["chosen_dose_text"] or "") == (r.get("dose") or "") and
                    (old["chosen_schedule"] or "") == (r.get("schedule") or "") and
                    (old["food_instruction"] or "") == (r.get("food") or "") and
                    str(old["duration_days"] or "") == str(r.get("days") or "") and
                    str(old["quantity_prescribed"] if old["quantity_prescribed"] is not None else "") == str(r.get("qty") or ""))
            item["item_change"] = "discontinued" if r.get("remove") else ("unchanged" if same else "modified")
        else:
            if r.get("remove"):
                continue
            item["item_change"] = "added"
        clean.append(item)
    if not clean:
        raise db.RuleError("Add at least one medicine.")
    with db.Session(a) as s:                              # one connection for the sequence
        if prev:
            res = s.call("new_prescription_version", prescription_id=prev["prescription_id"],
                         change_reason=change_reason or "Revised", request_uuid=request_uuid)
            pid = prev["prescription_id"]
        else:
            res = s.call("create_prescription", visit_id=vid, request_uuid=request_uuid)
            pid = res.first_value("prescription_id")
        ver = res.first_value("prescription_version_id")
        for n, it in enumerate(clean, 1):
            deterministic = f"{request_uuid}-{n}" if request_uuid else None
            s.call("add_prescription_item", prescription_id=pid, prescription_version_id=ver,
                   prescription_line_id=it.get("line_id"), line_no=n if not it.get("line_id") else None,
                   item_change=it["item_change"], medicine_id=it.get("medicine_id"),
                   free_text_medicine_name=it.get("free_text"), chosen_dose_text=it.get("dose"),
                   chosen_schedule=it.get("schedule"), food_instruction=it.get("food"),
                   duration_days=it.get("days"), quantity_prescribed=it.get("qty"),
                   allergy_confirmed=1 if it["confirmation_type"] else None,
                   allergy_override_reason=it.get("override"), displayed_allergies=shown if it["confirmation_type"] else None,
                   request_uuid=deterministic)
    return pid


def set_line_status(a, line_id, status, reason):
    db.call(a, "mark_item_status", prescription_line_id=line_id, status=status, reason=reason)


# ---------------------------------------------------------------- lab
def lab_orders(a, where: str, params: tuple, extra_from: str = "") -> list[dict]:
    rows = db.q(a, f"""SELECT lo.lab_order_id, lo.visit_id, lo.test_name, ol.name AS lab_name, lo.recorded_at AS ordered_at,
        (SELECT s.status FROM lab_order_status s WHERE s.lab_order_id=lo.lab_order_id ORDER BY s.lab_order_status_id DESC LIMIT 1) AS status,
        (SELECT r.lab_report_id FROM lab_report r WHERE r.lab_order_id=lo.lab_order_id
           AND NOT EXISTS (SELECT 1 FROM lab_report_retraction x WHERE x.lab_report_id=r.lab_report_id)
           AND NOT EXISTS (SELECT 1 FROM lab_report n WHERE n.supersedes_lab_report_id=r.lab_report_id)
           ORDER BY r.version_number DESC LIMIT 1) AS report_id,
        p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS patient_name, p.patient_id
        FROM lab_order lo JOIN outside_lab ol ON ol.outside_lab_id=lo.outside_lab_id
        JOIN visit v ON v.visit_id=lo.visit_id JOIN patient p ON p.patient_id=v.patient_id {extra_from}
        WHERE {where} ORDER BY lo.lab_order_id DESC""", params)
    for r in rows:
        if r["report_id"]:
            rep = db.one(a, """SELECT r.version_number, r.source, r.recorded_at, r.content_hash,
                EXISTS(SELECT 1 FROM lab_report_review w WHERE w.lab_report_id=r.lab_report_id) AS reviewed,
                (SELECT k.mark FROM lab_report_critical_mark k WHERE k.lab_report_id=r.lab_report_id ORDER BY k.lab_report_critical_mark_id DESC LIMIT 1) AS crit
                FROM lab_report r WHERE r.lab_report_id=%s""", (r["report_id"],))
            r.update(rep or {})
        r["trail"] = [("Ordered", True), ("Sample collected", r["status"] in ("sample_collected", "sent_to_lab") or bool(r["report_id"])),
                      ("Sent to lab", r["status"] == "sent_to_lab" or bool(r["report_id"])), ("Report received", bool(r["report_id"])),
                      ("Reviewed by doctor", bool(r.get("reviewed")))]
    return rows


def order_lab(a, vid, lab_id, test_name, request_uuid):
    if not lab_id or not test_name:
        raise db.RuleError("Choose the lab and write the test name.")
    db.call(a, "order_lab_test", visit_id=vid, outside_lab_id=lab_id, test_name=test_name, request_uuid=request_uuid)


def reports_to_review(a) -> list[dict]:
    rows = lab_orders(a, "EXISTS (SELECT 1 FROM visit_doctor_assignment x WHERE x.visit_id=lo.visit_id AND x.doctor_staff_id=%s)", (a.staff_id,))
    return [r for r in rows if r["report_id"]]


def review_report(a, report_id, note):
    db.call(a, "review_report", lab_report_id=report_id, review_note=note)


def mark_critical(a, report_id, mark, reason):
    db.call(a, "critical_mark", lab_report_id=report_id, mark=mark if mark in ("critical", "not_critical") else "critical",
            mark_source="doctor_assessment", reason=reason)


def retract_report(a, report_id, kind, note):
    if not note:
        raise db.RuleError("Explain why the report is being retracted.")
    db.call(a, "retract_report", lab_report_id=report_id, retraction_kind=kind, correct_lab_order_id=None, note=note)


def close_stay(a, episode_id, reason):
    db.call(a, "close_stay", short_stay_episode_id=episode_id, reason=reason)


# ---------------------------------------------------------------- clinical orders (short stay)
def clinical_orders(a, vid: int) -> list[dict]:
    orders = db.q(a, """SELECT o.clinical_order_id, o.order_type, o.recorded_at,
        (SELECT s.status FROM clinical_order_status s WHERE s.clinical_order_id=o.clinical_order_id ORDER BY s.clinical_order_status_id DESC LIMIT 1) AS status
        FROM clinical_order o WHERE o.visit_id=%s ORDER BY o.clinical_order_id DESC""", (vid,))
    for o in orders:
        o["items"] = db.q(a, """SELECT i.clinical_order_item_id, i.item_no, i.item_kind, COALESCE(m.brand_name, i.free_text_medicine_name) AS name,
            i.dose_value, i.dose_unit, i.route, i.first_scheduled_at, i.repeat_interval_minutes, i.quantity_ordered, i.instructions,
            (SELECT s.status FROM clinical_order_item_status s WHERE s.clinical_order_item_id=i.clinical_order_item_id ORDER BY s.clinical_order_item_status_id DESC LIMIT 1) AS status
            FROM clinical_order_item i LEFT JOIN medicine m ON m.medicine_id=i.medicine_id WHERE i.clinical_order_id=%s ORDER BY i.item_no""",
                          (o["clinical_order_id"],))
    return orders


def create_order(a, vid, episode_id, kind, f: dict, request_uuid):
    if kind == "medication" and not (f.get("medicine_id") or f.get("free_text")):
        raise db.RuleError("Choose the medicine.")
    with db.Session(a) as s:
        r = s.call("create_order", visit_id=vid, short_stay_episode_id=episode_id, order_type=kind, request_uuid=request_uuid)
        oid = r.first_value("clinical_order_id")
        s.call("add_order_item", clinical_order_id=oid, item_kind=kind, medicine_id=f.get("medicine_id"),
               free_text_medicine_name=f.get("free_text"), dose_value=f.get("dose_value"), dose_unit=f.get("dose_unit"),
               route=f.get("route"), first_scheduled_at=f.get("first_at"), repeat_interval_minutes=f.get("interval"),
               quantity_ordered=f.get("qty"), instructions=f.get("instructions"),
               request_uuid=f"{request_uuid}-1" if request_uuid else None)
        if f.get("finalize"):
            s.call("order_status", clinical_order_id=oid, status="finalized", reason="Doctor finalized")
    return oid


def set_order_status(a, oid, status, reason):
    db.call(a, "order_status", clinical_order_id=oid, status=status, reason=reason)


def stop_order_item(a, iid, reason):
    db.call(a, "order_item_status", clinical_order_item_id=iid, status="stopped", reason=reason)


# ---------------------------------------------------------------- print
def print_data(a, vid: int) -> dict | None:
    v = visit_brief(a, vid)
    if not v:
        return None
    doc = db.one(a, "SELECT d.registration_number, d.specialty, CONCAT_WS(' ',s.given_name,s.family_name) AS name "
                    "FROM doctor_detail d JOIN staff s ON s.staff_id=d.staff_id WHERE d.staff_id=%s", (v["doctor_staff_id"],)) if v.get("doctor_staff_id") else None
    return {"v": v, "doctor": doc, "rx": current_prescription(a, vid), "allergies": current_allergies(a, v["patient_id"]),
            "diagnoses": db.q(a, "SELECT diagnosis_text FROM diagnosis WHERE visit_id=%s ORDER BY diagnosis_id", (vid,)),
            "clinic": {k: db.setting(a, k, "") for k in ("clinic_name", "clinic_address", "clinic_phone")}}
