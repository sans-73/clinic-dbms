"""Small shared reads used by several screens."""
from __future__ import annotations

from .. import db

NOT_MERGED = "NOT EXISTS (SELECT 1 FROM patient_merge_log mm WHERE mm.source_patient_id = p.patient_id)"
PNAME = "CONCAT_WS(' ', p.given_name, p.middle_name, p.family_name)"


def in_clause(ids) -> tuple[str, list]:
    ids = [i for i in ids if i is not None]
    if not ids:
        return "(NULL)", []
    return "(" + ",".join(["%s"] * len(ids)) + ")", list(ids)


def doctors(a: db.Actor | None) -> list[dict]:
    return db.q(a, "SELECT s.staff_id, CONCAT_WS(' ', s.given_name, s.family_name) AS name, d.registration_number "
                   "FROM staff s JOIN doctor_detail d ON d.staff_id = s.staff_id "
                   "WHERE s.principal_kind='human' AND (s.active_to IS NULL OR s.active_to > UTC_TIMESTAMP()) "
                   "ORDER BY s.given_name")


def staff_with_role(a: db.Actor | None, role_code: str) -> list[dict]:
    return db.q(a, "SELECT s.staff_id, CONCAT_WS(' ', s.given_name, s.family_name) AS name FROM staff s "
                   "JOIN staff_role sr ON sr.staff_id=s.staff_id JOIN role r ON r.role_id=sr.role_id "
                   "WHERE r.role_code=%s AND s.principal_kind='human' AND sr.valid_from<=UTC_TIMESTAMP() "
                   "AND (sr.valid_to IS NULL OR sr.valid_to>UTC_TIMESTAMP()) ORDER BY s.given_name", (role_code,))


def patient_brief(a, patient_id: int) -> dict | None:
    return db.one(a, f"SELECT p.patient_id, p.display_code, p.given_name, p.middle_name, p.family_name, "
                     f"p.date_of_birth, p.approximate_age, p.gender, p.registration_kind, {PNAME} AS name "
                     f"FROM patient p WHERE p.patient_id=%s", (patient_id,))


def visit_brief(a, visit_id: int) -> dict | None:
    return db.one(a, f"""SELECT v.visit_id, v.patient_id, v.entry_type, v.occurred_at, v.appointment_id,
        p.display_code, p.given_name, p.family_name, p.date_of_birth, p.gender, {PNAME} AS name,
        (SELECT h.status FROM visit_status_history h WHERE h.visit_id=v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS status,
        (SELECT h.location_label FROM visit_status_history h WHERE h.visit_id=v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS location_label,
        (SELECT a2.doctor_staff_id FROM visit_doctor_assignment a2 WHERE a2.visit_id=v.visit_id AND a2.assignment_role='attending'
           ORDER BY a2.visit_doctor_assignment_id DESC LIMIT 1) AS doctor_staff_id,
        (SELECT CONCAT_WS(' ', s.given_name, s.family_name) FROM visit_doctor_assignment a2 JOIN staff s ON s.staff_id=a2.doctor_staff_id
           WHERE a2.visit_id=v.visit_id AND a2.assignment_role='attending' ORDER BY a2.visit_doctor_assignment_id DESC LIMIT 1) AS doctor_name,
        EXISTS(SELECT 1 FROM visit_closure c WHERE c.visit_id=v.visit_id) AS closed,
        (SELECT e.short_stay_episode_id FROM short_stay_episode e WHERE e.visit_id=v.visit_id LIMIT 1) AS short_stay_episode_id
        FROM visit v JOIN patient p ON p.patient_id=v.patient_id WHERE v.visit_id=%s""", (visit_id,))


def current_allergies(a, patient_id: int) -> list[dict]:
    return db.q(a, """SELECT pa.patient_allergy_id, pa.ingredient_id, COALESCE(i.ingredient_name, pa.allergen_text) AS allergen,
        pa.reaction, pa.severity,
        COALESCE((SELECT e.status FROM patient_allergy_status_event e WHERE e.patient_allergy_id=pa.patient_allergy_id
                  ORDER BY e.patient_allergy_status_event_id DESC LIMIT 1), 'confirmed') AS status
        FROM patient_allergy pa LEFT JOIN ingredient i ON i.ingredient_id=pa.ingredient_id
        WHERE pa.patient_id=%s HAVING status IN ('confirmed','reactivated') ORDER BY pa.patient_allergy_id""", (patient_id,))


def outside_labs(a) -> list[dict]:
    return db.q(a, "SELECT outside_lab_id, name, phone FROM outside_lab ORDER BY name")


def medicines(a, only_active=True) -> list[dict]:
    return db.q(a, "SELECT medicine_id, brand_name, generic_display_name, strength_value, strength_unit, dosage_form, "
                   "stock_unit, schedule_class, returnable FROM medicine " + ("WHERE active=1 " if only_active else "") +
                   "ORDER BY brand_name")


def med_label(m: dict) -> str:
    s = f"{m.get('brand_name') or ''}"
    if m.get("strength_value") is not None:
        s += f" {m['strength_value']:g}{m.get('strength_unit') or ''}" if isinstance(m["strength_value"], (int, float)) \
            else f" {m['strength_value']}{m.get('strength_unit') or ''}"
    if m.get("dosage_form"):
        s += f" ({m['dosage_form']})"
    return s.strip()
