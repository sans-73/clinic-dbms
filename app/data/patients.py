"""Reception / patient / appointment / visit data access (P5-2). Reads = SQL on ER columns; writes = procedures
from P3-2 (contract keys) except simple master edits."""
from __future__ import annotations

from datetime import date, datetime

from .. import db, timeutil
from ..constants import APPOINTMENT_STATUSES
from .common import NOT_MERGED, PNAME, in_clause, visit_brief, current_allergies

EDITABLE_DEMOGRAPHICS = ["given_name", "middle_name", "family_name", "gender", "date_of_birth", "approximate_age",
                         "aadhaar_last4"]


# ---------------------------------------------------------------- patients
def search(a, text: str, limit: int = 40) -> list[dict]:
    toks = [t for t in (text or "").split() if t][:4]
    if not toks:
        return db.q(a, f"""SELECT p.patient_id, p.display_code, p.given_name, p.family_name, p.date_of_birth, p.gender,
            p.registration_kind, {_PHONE} AS phone FROM patient p WHERE {NOT_MERGED} ORDER BY p.patient_id DESC LIMIT %s""", (limit,))
    where, params = [NOT_MERGED], []
    for t in toks:
        like = f"%{t}%"
        where.append("(p.display_code LIKE %s OR p.given_name LIKE %s OR p.family_name LIKE %s OR p.middle_name LIKE %s OR "
                     "EXISTS (SELECT 1 FROM patient_phone ph WHERE ph.patient_id=p.patient_id AND ph.phone_number LIKE %s))")
        params += [like] * 5
    return db.q(a, f"""SELECT p.patient_id, p.display_code, p.given_name, p.family_name, p.date_of_birth, p.gender,
        p.registration_kind, {_PHONE} AS phone FROM patient p WHERE {' AND '.join(where)}
        ORDER BY p.patient_id DESC LIMIT %s""", (*params, limit))


_PHONE = ("(SELECT ph.phone_number FROM patient_phone ph WHERE ph.patient_id=p.patient_id AND ph.valid_to IS NULL "
          "ORDER BY ph.is_primary DESC, ph.phone_sequence DESC LIMIT 1)")


def profile(a, pid: int) -> dict | None:
    p = db.one(a, f"SELECT p.*, {_PHONE} AS current_phone, {PNAME} AS name FROM patient p WHERE p.patient_id=%s", (pid,))
    if not p:
        return None
    merged = db.one(a, "SELECT surviving_patient_id FROM patient_merge_log WHERE source_patient_id=%s", (pid,))
    return {
        "p": p, "merged_into": merged["surviving_patient_id"] if merged else None,
        "phones": db.q(a, "SELECT phone_number, valid_from, valid_to, is_primary FROM patient_phone WHERE patient_id=%s "
                          "ORDER BY phone_sequence DESC", (pid,)),
        "attendants": db.q(a, "SELECT * FROM patient_attendant WHERE patient_id=%s", (pid,)),
        "allergies": current_allergies(a, pid),
        "blood_group": db.one(a, "SELECT abo_group, rh_factor, source FROM patient_blood_group WHERE patient_id=%s "
                                 "ORDER BY patient_blood_group_id DESC LIMIT 1", (pid,)),
        "visits": db.q(a, """SELECT v.visit_id, v.occurred_at, v.entry_type,
            (SELECT h.status FROM visit_status_history h WHERE h.visit_id=v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS status
            FROM visit v WHERE v.patient_id=%s ORDER BY v.visit_id DESC LIMIT 10""", (pid,)),
        "open_dues": _open_dues(a, pid),
    }


def _open_dues(a, pid: int) -> list[dict]:
    """R9.3 visible flag at next visit: bills of this patient with money still due."""
    rows = db.q(a, """SELECT b.bill_id, b.bill_number,
        COALESCE((SELECT SUM(bl.quantity*bl.unit_price_snapshot) FROM bill_line bl WHERE bl.bill_id=b.bill_id),0)
        - COALESCE((SELECT SUM(CASE WHEN pm.payment_kind='payment' THEN pm.amount ELSE -pm.amount END) FROM payment pm WHERE pm.bill_id=b.bill_id),0)
        + COALESCE((SELECT SUM(rf.amount) FROM refund rf WHERE rf.bill_id=b.bill_id),0) AS balance,
        (SELECT MAX(dp.promised_by_date) FROM due_promise dp WHERE dp.bill_id=b.bill_id) AS promised_by
        FROM bill b JOIN visit v ON v.visit_id=b.visit_id
        WHERE v.patient_id=%s AND EXISTS (SELECT 1 FROM discharge d WHERE d.visit_id=v.visit_id)""", (pid,))
    return [r for r in rows if r["balance"] and r["balance"] > 0]


def find_candidates(a, **f) -> list[dict]:
    try:
        res = db.call(a, "find_candidates", **f)
    except db.NotReady:
        return _fallback_candidates(a, f)
    out = []
    for r in res.rows:
        r = dict(r)
        if not r.get("name"):
            r["name"] = " ".join(x for x in (r.get("given_name"), r.get("family_name")) if x)
        out.append(r)
    return out


def _fallback_candidates(a, f: dict) -> list[dict]:
    """Used only if find_patient_candidates is missing: simple T1/T2 style SQL so reception is never blocked."""
    gn, fn, dob, ph = (f.get("given_name") or ""), (f.get("family_name") or ""), f.get("date_of_birth"), f.get("phone")
    rows = db.q(a, f"""SELECT p.patient_id, p.display_code, p.given_name, p.family_name, p.date_of_birth, {_PHONE} AS phone,
        CASE WHEN %s IS NOT NULL AND EXISTS (SELECT 1 FROM patient_phone ph WHERE ph.patient_id=p.patient_id AND ph.phone_number=%s) THEN 'T1'
             WHEN %s IS NOT NULL AND p.date_of_birth=%s THEN 'T2' ELSE 'T3' END AS tier
        FROM patient p WHERE {NOT_MERGED} AND (
          (%s IS NOT NULL AND EXISTS (SELECT 1 FROM patient_phone ph WHERE ph.patient_id=p.patient_id AND ph.phone_number=%s))
          OR (%s IS NOT NULL AND p.date_of_birth=%s)
          OR SOUNDEX(p.family_name)=SOUNDEX(%s)) LIMIT 15""", (ph, ph, dob, dob, ph, ph, dob, dob, fn))
    for r in rows:
        r["name"] = f"{r['given_name']} {r['family_name']}"
        r["score"] = None
    return rows


def register(a, **f):
    r = db.call(a, "register_patient", **f)
    return r.first_value("patient_id"), r.first_value("display_code")


def register_unknown(a, gender, approximate_age, request_uuid=None):
    r = db.call(a, "register_unknown", gender=gender, approximate_age=approximate_age, request_uuid=request_uuid)
    return r.first_value("patient_id"), r.first_value("display_code")


def merge(a, source_id, survivor_id, reason, tier=None, request_uuid=None):
    if int(source_id) == int(survivor_id):
        raise db.RuleError("Source and surviving patient must be different people.")
    db.call(a, "merge_patients", source_patient_id=source_id, surviving_patient_id=survivor_id, reason=reason,
            suggested_match_tier=tier, request_uuid=request_uuid)


def update_demographics(a, pid: int, **f):
    cols = {k: v for k, v in f.items() if k in EDITABLE_DEMOGRAPHICS and v is not None}
    if not cols:
        raise db.RuleError("Nothing to change.")
    if cols.get("aadhaar_last4") and (not str(cols["aadhaar_last4"]).isdigit() or len(str(cols["aadhaar_last4"])) != 4):
        raise db.RuleError("Aadhaar: enter only the LAST 4 digits.")
    db.update_master(a, "patient", "patient_id", pid, **cols)       # audit trigger writes old/new values (R1.6)


def add_phone(a, pid, phone, is_primary=1, request_uuid=None):
    db.call(a, "add_phone", patient_id=pid, phone=phone, is_primary=is_primary, request_uuid=request_uuid)


def add_attendant(a, pid, name, relationship, phone, emergency=1):
    db.call(a, "add_attendant", patient_id=pid, name=name, relationship=relationship, phone=phone,
            is_emergency_contact=emergency)


def add_allergy(a, pid, allergen_text, reaction, severity, ingredient_id=None):
    """Direct event insert (single row, no cross-table logic). Current status = derived, so no status row needed."""
    if not allergen_text and not ingredient_id:
        raise db.RuleError("Enter the allergen.")
    db.insert_event(a, "patient_allergy", patient_id=pid, allergen_text=allergen_text, ingredient_id=ingredient_id,
                    reaction=reaction, severity=severity)


# ---------------------------------------------------------------- doctor slots and appointments
def slots(a, doctor_id: int, day: date) -> list[dict]:
    s, e = timeutil.local_day_window(day)
    rows = db.q(a, """SELECT sl.appointment_slot_id, sl.slot_start, sl.slot_end,
      (SELECT ds.schedule_state FROM doctor_schedule ds WHERE ds.doctor_schedule_id=sl.doctor_schedule_id) AS sched_state,
      EXISTS(SELECT 1 FROM doctor_schedule s2 WHERE s2.supersedes_doctor_schedule_id=sl.doctor_schedule_id
             AND s2.valid_from <= DATE(sl.slot_start)) AS superseded,
      (SELECT c.appointment_id FROM appointment_slot_claim c WHERE c.appointment_slot_id=sl.appointment_slot_id
         AND c.appointment_slot_claim_id=(SELECT MAX(c2.appointment_slot_claim_id) FROM appointment_slot_claim c2 WHERE c2.appointment_id=c.appointment_id)
         AND (SELECT h.status FROM appointment_status_history h WHERE h.appointment_id=c.appointment_id ORDER BY h.status_seq DESC LIMIT 1)
             NOT IN ('Cancelled','No-show') LIMIT 1) AS booked_appointment_id
      FROM appointment_slot sl WHERE sl.doctor_staff_id=%s AND sl.slot_start>=%s AND sl.slot_start<%s
      ORDER BY sl.slot_start""", (doctor_id, s, e))
    ids = [r["booked_appointment_id"] for r in rows if r["booked_appointment_id"]]
    names = {}
    if ids:
        ph, pr = in_clause(ids)
        for r in db.q(a, f"SELECT ap.appointment_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS name "
                         f"FROM appointment ap JOIN patient p ON p.patient_id=ap.patient_id WHERE ap.appointment_id IN {ph}", pr):
            names[r["appointment_id"]] = r
    now = timeutil.utcnow()
    for r in rows:
        b = names.get(r["booked_appointment_id"])
        r["booked_name"] = f"{b['name']} ({b['display_code']})" if b else None
        r["bookable"] = (not r["booked_appointment_id"] and not r["superseded"] and r["sched_state"] != "withdrawn"
                         and r["slot_start"] >= now)
    return rows


def appointments(a, day: date, doctor_id: int | None = None) -> list[dict]:
    s, e = timeutil.local_day_window(day)
    dfilter = "WHERE ap.doctor_staff_id=%s" if doctor_id else ""
    sql = f"""SELECT * FROM (SELECT ap.appointment_id, ap.patient_id, ap.doctor_staff_id, ap.booking_channel,
        p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS name,
        (SELECT sl.slot_start FROM appointment_slot_claim c JOIN appointment_slot sl ON sl.appointment_slot_id=c.appointment_slot_id
           WHERE c.appointment_id=ap.appointment_id ORDER BY c.appointment_slot_claim_id DESC LIMIT 1) AS slot_start,
        (SELECT h.status FROM appointment_status_history h WHERE h.appointment_id=ap.appointment_id ORDER BY h.status_seq DESC LIMIT 1) AS status,
        (SELECT CONCAT_WS(' ',s.given_name,s.family_name) FROM staff s WHERE s.staff_id=ap.doctor_staff_id) AS doctor_name,
        (SELECT v.visit_id FROM visit v WHERE v.appointment_id=ap.appointment_id LIMIT 1) AS visit_id
        FROM appointment ap JOIN patient p ON p.patient_id=ap.patient_id
        {dfilter}) x WHERE x.slot_start>=%s AND x.slot_start<%s ORDER BY x.slot_start"""
    params = ([doctor_id] if doctor_id else []) + [s, e]
    return db.q(a, sql, params)


def book(a, patient_id, doctor_id, slot_id, channel="reception", request_uuid=None):
    r = db.call(a, "book_appointment", patient_id=patient_id, doctor_staff_id=doctor_id, appointment_slot_id=slot_id,
                booking_channel=channel, request_uuid=request_uuid)
    return r.first_value("appointment_id")


def set_appointment_status(a, appointment_id, status, reason=None, request_uuid=None):
    if status not in APPOINTMENT_STATUSES:
        raise db.RuleError("Unknown appointment status.")
    db.call(a, "appointment_status", appointment_id=appointment_id, status=status, reason=reason, request_uuid=request_uuid)


def reschedule(a, appointment_id, slot_id, reason, request_uuid=None):
    db.call(a, "reschedule", appointment_id=appointment_id, appointment_slot_id=slot_id, reason=reason,
            request_uuid=request_uuid)


def check_in(a, appointment_id, request_uuid=None):
    """Checked-in status, then the visit (R3.2: appointment is optional, visit is the parent of everything)."""
    ap = db.one(a, "SELECT patient_id, doctor_staff_id, (SELECT v.visit_id FROM visit v WHERE v.appointment_id=%s LIMIT 1) AS visit_id "
                   "FROM appointment WHERE appointment_id=%s", (appointment_id, appointment_id))
    if not ap:
        raise db.RuleError("Appointment not found.")
    set_appointment_status(a, appointment_id, "Checked-in", None, None)
    if not ap["visit_id"]:
        r = db.call(a, "start_visit", patient_id=ap["patient_id"], appointment_id=appointment_id, entry_type="appointment",
                    doctor_staff_id=ap["doctor_staff_id"], request_uuid=request_uuid)
        return r.first_value("visit_id")
    return ap["visit_id"]


def generate_slots(a, doctor_id, from_date, to_date):
    db.call(a, "generate_slots", doctor_staff_id=doctor_id, from_date=from_date, to_date=to_date)


# ---------------------------------------------------------------- visits and queue
def queue(a, day: date | None = None) -> list[dict]:
    s, e = timeutil.local_day_window(day)
    return db.q(a, f"""SELECT v.visit_id, v.occurred_at, v.entry_type, v.patient_id, p.display_code,
        CONCAT_WS(' ',p.given_name,p.family_name) AS name,
        (SELECT h.status FROM visit_status_history h WHERE h.visit_id=v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS status,
        (SELECT h.location_label FROM visit_status_history h WHERE h.visit_id=v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS location_label,
        (SELECT h.occurred_at FROM visit_status_history h WHERE h.visit_id=v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS since,
        (SELECT CONCAT_WS(' ',s.given_name,s.family_name) FROM visit_doctor_assignment x JOIN staff s ON s.staff_id=x.doctor_staff_id
           WHERE x.visit_id=v.visit_id AND x.assignment_role='attending' ORDER BY x.visit_doctor_assignment_id DESC LIMIT 1) AS doctor_name,
        EXISTS(SELECT 1 FROM short_stay_episode e WHERE e.visit_id=v.visit_id) AS short_stay,
        EXISTS(SELECT 1 FROM visit_closure c WHERE c.visit_id=v.visit_id) AS closed
        FROM visit v JOIN patient p ON p.patient_id=v.patient_id
        WHERE v.occurred_at>=%s AND v.occurred_at<%s ORDER BY v.visit_id""", (s, e))


def start_visit(a, patient_id, entry_type, doctor_id=None, request_uuid=None):
    r = db.call(a, "start_visit", patient_id=patient_id, appointment_id=None, entry_type=entry_type,
                doctor_staff_id=doctor_id, request_uuid=request_uuid)
    return r.first_value("visit_id")


def set_visit_status(a, visit_id, status, location_label=None, reason=None, request_uuid=None):
    db.call(a, "visit_status", visit_id=visit_id, status=status, location_label=location_label, reason=reason,
            request_uuid=request_uuid)


# ---------------------------------------------------------------- schedule changes (R3.7)
def pending_schedule_changes(a) -> list[dict]:
    return db.q(a, """SELECT r.schedule_change_request_id, r.doctor_staff_id, r.change_kind, r.proposed_weekday,
        r.proposed_start_time, r.proposed_end_time, r.proposed_slot_minutes, r.proposed_valid_from, r.reason, r.recorded_at,
        CONCAT_WS(' ',s.given_name,s.family_name) AS doctor_name
        FROM schedule_change_request r JOIN staff s ON s.staff_id=r.doctor_staff_id
        WHERE NOT EXISTS (SELECT 1 FROM schedule_change_application x WHERE x.schedule_change_request_id=r.schedule_change_request_id)
        ORDER BY r.schedule_change_request_id""")


def apply_schedule_change(a, req_id, outcome, reason):
    db.call(a, "apply_schedule_change", schedule_change_request_id=req_id, outcome=outcome, reason=reason)


def my_schedule(a, doctor_id) -> list[dict]:
    return db.q(a, """SELECT ds.doctor_schedule_id, ds.weekday, ds.start_time, ds.end_time, ds.slot_minutes, ds.valid_from,
        ds.schedule_state,
        EXISTS(SELECT 1 FROM doctor_schedule n WHERE n.supersedes_doctor_schedule_id=ds.doctor_schedule_id) AS superseded
        FROM doctor_schedule ds WHERE ds.doctor_staff_id=%s ORDER BY ds.doctor_schedule_id DESC""", (doctor_id,))


def my_schedule_requests(a, doctor_id) -> list[dict]:
    return db.q(a, """SELECT r.schedule_change_request_id, r.change_kind, r.proposed_weekday, r.proposed_start_time,
        r.proposed_end_time, r.proposed_valid_from, r.reason,
        (SELECT x.outcome FROM schedule_change_application x WHERE x.schedule_change_request_id=r.schedule_change_request_id) AS outcome
        FROM schedule_change_request r WHERE r.doctor_staff_id=%s ORDER BY r.schedule_change_request_id DESC LIMIT 15""",
                (doctor_id,))


def request_schedule_change(a, **f):
    db.call(a, "request_schedule_change", doctor_staff_id=a.staff_id, **f)


# ---------------------------------------------------------------- short stay
def start_short_stay(a, visit_id, reason, request_uuid=None):
    db.call(a, "start_short_stay", visit_id=visit_id, admission_reason=reason, request_uuid=request_uuid)


def request_extension(a, episode_id, extended_until_utc, reason):
    """R3.4: P3-2's stay-extension procedure creates the approval request (owner decides in /owner/approvals)."""
    db.call(a, "stay_extension", short_stay_episode_id=episode_id, extended_until=extended_until_utc, reason=reason)


def active_stays(a) -> list[dict]:
    limit = db.setting_num(a, "stay_limit_hours", 24)
    rows = db.q(a, f"""SELECT e.short_stay_episode_id, e.visit_id, e.occurred_at AS started_at, e.admission_reason,
        p.patient_id, p.display_code, CONCAT_WS(' ',p.given_name,p.family_name) AS name,
        (SELECT MAX(x.extended_until) FROM short_stay_extension x WHERE x.short_stay_episode_id=e.short_stay_episode_id) AS extended_until
        FROM short_stay_episode e JOIN visit v ON v.visit_id=e.visit_id JOIN patient p ON p.patient_id=v.patient_id
        WHERE NOT EXISTS (SELECT 1 FROM short_stay_closure c WHERE c.short_stay_episode_id=e.short_stay_episode_id)
          AND NOT EXISTS (SELECT 1 FROM visit_closure vc WHERE vc.visit_id=e.visit_id) ORDER BY e.occurred_at""")
    now = timeutil.utcnow()
    from datetime import timedelta
    for r in rows:
        allowed = r["started_at"] + timedelta(hours=limit)
        if r["extended_until"] and r["extended_until"] > allowed:
            allowed = r["extended_until"]
        r["allowed_until"] = allowed
        r["over_limit"] = now > allowed
        r["hours"] = round((now - r["started_at"]).total_seconds() / 3600, 1)
    return rows
