#!/usr/bin/env python3
"""seed_demo.py  -  P4-1 demo seed (FAKE data only).

Run order (from repo root, Git Bash):
    docker compose down -v && docker compose up -d      # fresh DB
    bash tools/run_sql_checked.sh                        # schema + logic (other agent's tool)
    python tools/bootstrap_owner.py ...                  # first OWNER must exist (other agent's tool)
    python tools/seed_demo.py --check                    # dry contract check: nothing is saved
    python tools/seed_demo.py                            # real seed

WHY PYTHON AND NOT A .sql FILE: the procedures for prescriptions/pharmacy/billing (P3-3..P3-6) did not exist
when this was written, so the seed inserts rows directly with the REAL triggers still active (append-only,
hat check, maker-checker all fire). Only the stable shared procedures from TASKS.md are CALLed
(raise_alert, request_approval, decide_approval, queue_message, next_code).
Each "unit" (one visit / one story) runs in a SAVEPOINT: a failure rolls back that unit only and is printed
in the final report, so the first run shows ALL problems at once instead of stopping at the first.

Also writes db/seed/DEMO_CREDENTIALS.md (logins, family-link tokens, key IDs).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import random
import secrets
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p4_fake as fk                                                    # noqa: E402
from p4_lib import ROOT, Actor, Loader, connect, utcnow                 # noqa: E402

# ======================= ADAPTER SECTION: edit here if the schema/app differs =======================
DEMO_PASSWORD = "Demo@1234"
CRED_TABLE, CRED_STAFF_COL, CRED_HASH_COL = "staff_credential", "staff_id", "password_hash"
HASHER_CANDIDATES = ("hash_password", "make_password_hash", "get_password_hash", "hash_pw")   # in app/security.py
WEEKDAY_STYLE = "iso"      # doctor_schedule.weekday: "iso" = Mon=1..Sun=7 ; "mysql" = Mon=0..Sun=6
STORY_ALLERGY_INGREDIENT = "Amoxicillin"
# ====================================================================================================

NOW = utcnow()
TODAY = NOW.date()
MIDNIGHT = datetime.combine(TODAY, datetime.min.time())
rng = random.Random(20260710)


def at(days: int, hour: int = 10, minute: int = 0) -> datetime:
    return MIDNIGHT + timedelta(days=days, hours=hour, minutes=minute)


def mins_ago(m: int) -> datetime:
    return NOW - timedelta(minutes=m)


class DryLoader(Loader):
    """--dry-run: no database at all; exercises the Python logic only."""

    def __init__(self):
        self.report, self.failures, self.counts = defaultdict(set), [], defaultdict(int)
        self.actor, self._n = None, 1000

    def act(self, actor):
        self.actor = actor

    def insert(self, table, row, actor=None):
        self.counts[table] += 1
        self._n += 1
        return self._n

    def call(self, proc, *a, outs=0, actor=None):
        self.counts["CALL " + proc] += 1
        self._n += 1
        return self._n if outs <= 1 else tuple([self._n] * outs)

    def one(self, sql, args=()):
        return 1

    def pick(self, table, col, preferred):
        return preferred[0]

    def role_id(self, code):
        return 1

    @contextlib.contextmanager
    def unit(self, name, critical=False):
        try:
            yield
        except Exception as e:
            self.failures.append((name, repr(e)))
            if critical:
                raise


class S:           # shared state holder
    pass


# ------------------------------------------------------------------ stage 1: reference data
ROLES = [("OWNER", "Owner"), ("DOCTOR", "Doctor"), ("RECEPTIONIST", "Receptionist"), ("NURSE", "Nurse"),
         ("PHARMACY", "Pharmacy"), ("ACCOUNTANT", "Accountant"), ("SYSTEM", "System")]
STAFF = [  # key, given, family, role, login, detail table, detail columns
    ("dr_mehta", "Anil", "Mehta", "DOCTOR", "dr.mehta", "doctor_detail", dict(registration_number="MP-12345", specialty="General Medicine")),
    ("dr_iyer", "Lakshmi", "Iyer", "DOCTOR", "dr.iyer", "doctor_detail", dict(registration_number="MP-23456", specialty="Paediatrics")),
    ("dr_khan", "Farzana", "Khan", "DOCTOR", "dr.khan", "doctor_detail", dict(registration_number="MP-34567", specialty="Family Medicine")),
    ("recep", "Pooja", "Tiwari", "RECEPTIONIST", "reception", "receptionist_detail", dict(desk_label="Front desk")),
    ("nurse1", "Sunita", "Yadav", "NURSE", "nurse.sunita", "nurse_detail", dict(registration_number="N-1001")),
    ("nurse2", "Rekha", "Soni", "NURSE", "nurse.rekha", "nurse_detail", dict(registration_number="N-1002")),
    ("pharm", "Rajesh", "Gupta", "PHARMACY", "pharmacy", "pharmacist_detail", dict(registration_number="PH-2001")),
    ("acct", "Savita", "Jain", "ACCOUNTANT", "accounts", "accountant_detail", dict(accounting_reference="ACC-01")),
]


def find_actor(L, role_code, system=False):
    if system:
        r = L.one("SELECT staff_id FROM staff WHERE principal_kind='system' ORDER BY staff_id LIMIT 1")
    else:
        r = L.one("SELECT s.staff_id FROM staff s JOIN staff_role sr ON sr.staff_id=s.staff_id "
                  "JOIN role r ON r.role_id=sr.role_id WHERE r.role_code=%s ORDER BY s.staff_id LIMIT 1", (role_code,))
    return Actor(int(r), role_code) if r else None


def get_hasher():
    sys.path.insert(0, str(ROOT))
    try:
        import importlib
        m = importlib.import_module("app.security")
        for n in HASHER_CANDIDATES:
            if hasattr(m, n):
                return getattr(m, n)
    except Exception:
        pass
    return None


def stage_reference(L, s):
    s.owner = find_actor(L, "OWNER")
    if not s.owner:
        sys.exit("No OWNER staff found. Create the first owner with tools/bootstrap_owner.py, then re-run.")
    L.act(s.owner)
    for code, name in ROLES:
        if L.role_id(code) is None:
            L.insert("role", dict(role_code=code, role_name=name))
    L.refresh_roles()
    s.sys = find_actor(L, "SYSTEM", system=True) or s.owner
    hasher = get_hasher()
    if not hasher:
        print("  (!) app.security hash function not found -> staff_credential NOT seeded. Set HASHER_CANDIDATES.")
    s.staff, s.cred = {}, []
    for i, (key, gn, fn, role, login, dtab, dcols) in enumerate(STAFF, 1):
        sid = L.insert("staff", dict(display_code=f"DEMO-{i:02d}", principal_kind="human", given_name=gn,
                                     family_name=fn, phone=fk.phone(rng), active_from=TODAY - timedelta(days=400),
                                     login_subject=login), s.owner)
        L.insert("staff_role", dict(staff_id=sid, role_id=L.role_id(role), valid_from=TODAY - timedelta(days=400),
                                    granted_by=s.owner.staff_id), s.owner)
        L.insert(dtab, dict(staff_id=sid, **dcols), s.owner)
        s.staff[key] = Actor(sid, role)
        if hasher:
            with L.unit(f"credential {login}"):
                L.insert(CRED_TABLE, {CRED_STAFF_COL: sid, CRED_HASH_COL: hasher(DEMO_PASSWORD)}, s.owner)
        s.cred.append((login, role, f"{gn} {fn}"))
    st = s.staff
    s.recep, s.pharm, s.acct, s.nurse1, s.nurse2 = st["recep"], st["pharm"], st["acct"], st["nurse1"], st["nurse2"]
    s.doctors = [st["dr_mehta"], st["dr_iyer"], st["dr_khan"]]
    s.clinic_code = str(L.one("SELECT setting_value FROM clinic_settings WHERE setting_key='clinic_code'") or "CLN1")


# ------------------------------------------------------------------ stage 2: catalog, stock
MEDS = [  # key, brand, generic, strength, unit, form, stock_unit, schedule, returnable, dose, sched, food, days, mrp, ingredients
    ("para", "Parafix 500", "Paracetamol", 500, "mg", "tablet", "tablet", "none", 0, "1 tablet", "1-1-1", "after food", 3, "2.00", ["Paracetamol"]),
    ("parasy", "Parafix Syrup 125", "Paracetamol", 125, "mg", "syrup", "ml", "none", 0, "5 ml", "1-1-1", "after food", 3, "1.20", ["Paracetamol"]),
    ("ceti", "Cetrin 10", "Cetirizine", 10, "mg", "tablet", "tablet", "none", 0, "1 tablet", "0-0-1", "after food", 5, "1.50", ["Cetirizine"]),
    ("amox", "Amoxiclav 625", "Amoxicillin + Clavulanic acid", 625, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-0-1", "after food", 5, "19.00", ["Amoxicillin", "Clavulanic acid"]),
    ("azith", "Azithral 500", "Azithromycin", 500, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-0-0", "after food", 3, "24.00", ["Azithromycin"]),
    ("cefi", "Cefix 200", "Cefixime", 200, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-0-1", "after food", 5, "16.00", ["Cefixime"]),
    ("panto", "Pantoplus 40", "Pantoprazole", 40, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-0-0", "before food", 14, "9.00", ["Pantoprazole"]),
    ("ome", "Omez 20", "Omeprazole", 20, "mg", "capsule", "capsule", "H", 0, "1 capsule", "1-0-0", "before food", 10, "6.00", ["Omeprazole"]),
    ("dom", "Domstal 10", "Domperidone", 10, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-1-1", "before food", 3, "3.50", ["Domperidone"]),
    ("ondem", "Ondem 4", "Ondansetron", 4, "mg", "tablet", "tablet", "H", 1, "1 tablet", "1-1-1", "any time", 1, "5.00", ["Ondansetron"]),
    ("ibu", "Ibugesic 400", "Ibuprofen", 400, "mg", "tablet", "tablet", "none", 0, "1 tablet", "1-0-1", "after food", 3, "2.50", ["Ibuprofen"]),
    ("diclo", "Diclomax 50", "Diclofenac", 50, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-0-1", "after food", 3, "3.00", ["Diclofenac"]),
    ("metro", "Metrogyl 400", "Metronidazole", 400, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-1-1", "after food", 5, "2.80", ["Metronidazole"]),
    ("cipro", "Ciplox 500", "Ciprofloxacin", 500, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-0-1", "after food", 5, "7.50", ["Ciprofloxacin"]),
    ("mont", "Montair 10", "Montelukast", 10, "mg", "tablet", "tablet", "H", 0, "1 tablet", "0-0-1", "after food", 10, "12.00", ["Montelukast"]),
    ("alpr", "Alprazen 0.25", "Alprazolam", 0.25, "mg", "tablet", "tablet", "H1", 0, "1 tablet", "0-0-1", "any time", 5, "4.00", ["Alprazolam"]),
    ("tram", "Tramazac 50", "Tramadol", 50, "mg", "capsule", "capsule", "H1", 0, "1 capsule", "1-0-1", "after food", 3, "8.00", ["Tramadol"]),
    ("ors", "ORS Sachet", "Oral rehydration salts", 21, "g", "powder", "sachet", "none", 1, "1 sachet in 1 litre", "1-1-1", "any time", 2, "18.00", ["ORS"]),
    ("ns", "Normal Saline 500", "Sodium chloride 0.9%", 500, "ml", "infusion", "bag", "H", 1, "500 ml", "stat", "any time", 1, "45.00", ["Sodium chloride"]),
    ("dns", "Dextrose 5% 500", "Dextrose 5%", 500, "ml", "infusion", "bag", "H", 1, "500 ml", "stat", "any time", 1, "48.00", ["Dextrose"]),
    ("ceftri", "Ceftrax 1g Inj", "Ceftriaxone", 1, "g", "injection", "vial", "H", 1, "1 vial", "1-0-1", "any time", 3, "62.00", ["Ceftriaxone"]),
    ("pantiv", "Pantop IV 40", "Pantoprazole", 40, "mg", "injection", "vial", "H", 1, "1 vial", "1-0-1", "any time", 2, "38.00", ["Pantoprazole"]),
    ("diclinj", "Diclomax Inj 75", "Diclofenac", 75, "mg", "injection", "ampoule", "H", 1, "1 ampoule", "stat", "any time", 1, "11.00", ["Diclofenac"]),
    ("povi", "Betacure 5% Oint", "Povidone iodine", 5, "%", "ointment", "tube", "none", 1, "apply locally", "1-0-1", "any time", 5, "42.00", ["Povidone iodine"]),
    ("bcom", "Neurobion-B Forte", "Vitamin B complex", 1, "tab", "tablet", "tablet", "none", 0, "1 tablet", "1-0-0", "after food", 30, "2.20", ["Vitamin B complex"]),
    ("cal", "Calcirol D3", "Calcium + Vitamin D3", 500, "mg", "tablet", "tablet", "none", 0, "1 tablet", "0-1-0", "after food", 30, "4.50", ["Calcium", "Vitamin D3"]),
    ("salb", "Asthavent Inhaler", "Salbutamol", 100, "mcg", "inhaler", "inhaler", "H", 0, "2 puffs", "SOS", "any time", 10, "135.00", ["Salbutamol"]),
    ("levo", "Levoflox 500", "Levofloxacin", 500, "mg", "tablet", "tablet", "H", 0, "1 tablet", "1-0-0", "after food", 5, "14.00", ["Levofloxacin"]),   # NO stock on purpose (blue label)
    ("last", "Zerostock Demo 10", "Demo generic", 10, "mg", "tablet", "tablet", "none", 0, "1 tablet", "1-0-0", "any time", 1, "1.00", ["Demo generic"]),  # batch qty 1 (concurrency demo)
]
NO_STOCK = {"levo"}


def stage_catalog(L, s):
    L.act(s.owner)
    for code, desc, cat, price in [("CONSULT-GEN", "General consultation", "consultation", "300.00"),
                                   ("CONSULT-FU", "Follow-up consultation", "consultation", "150.00"),
                                   ("STAY-DAY", "Day-care stay (up to 24 h)", "stay", "800.00"),
                                   ("INJ-IMIV", "Injection administration", "injection", "50.00"),
                                   ("DRIP-STD", "IV drip set", "drip", "250.00"),
                                   ("PROC-DRESS", "Wound dressing", "procedure", "200.00"),
                                   ("PROC-NEB", "Nebulisation", "procedure", "120.00"),
                                   ("PROC-ECG", "ECG", "procedure", "250.00")]:
        pid = L.insert("price_list", dict(item_code=code, description=desc, category=cat, price=D(price),
                                          effective_from=TODAY - timedelta(days=365), active=1))
        s.price[code] = (pid, D(price), desc)
    for name, phone_, contact in [("Bhopal Pharma Distributors", fk.phone(rng), "Mr. Verma"),
                                  ("Narmada Medico Agency", fk.phone(rng), "Mr. Chouhan"),
                                  ("City Surgical & Drugs", fk.phone(rng), "Ms. Dubey")]:
        s.suppliers.append(L.insert("supplier", dict(name=name, phone=phone_, contact_name=contact, notes="demo supplier")))
    s.labs = [L.insert("outside_lab", dict(name=n, phone=fk.phone(rng), contact_name=c, notes="demo lab"))
              for n, c in [("Sanjivani Diagnostics", "Reception desk"), ("Arogya Path Lab", "Mr. Saxena")]]
    ing_ids = {}
    for m in MEDS:
        for ing in m[14]:
            if ing not in ing_ids:
                ing_ids[ing] = L.insert("ingredient", dict(ingredient_name=ing))
    s.ing = ing_ids
    for (key, brand, gen, sv, su, form, sunit, sched, ret, dose, sch, food, days, mrp, ings) in MEDS:
        mid = L.insert("medicine", dict(brand_name=brand, generic_display_name=gen, strength_value=sv, strength_unit=su,
                                        dosage_form=form, stock_unit=sunit, schedule_class=sched, returnable=ret,
                                        default_dose_text=dose, default_schedule=sch, default_food_instruction=food,
                                        default_duration_days=days, active=1))
        s.med[key] = dict(id=mid, brand=brand, mrp=D(mrp), sched=sched, dose=dose, sch=sch, food=food, days=days, unit=sunit)
        for ing in ings:
            L.insert("medicine_ingredient", dict(medicine_id=mid, ingredient_id=ing_ids[ing]))
    # stock receipts by pharmacy
    def receive(key, code, expiry, qty, days_ago, tag=""):
        m = s.med[key]
        cost = (m["mrp"] * D("0.65")).quantize(D("0.01"))
        bid = L.insert("stock_batch", dict(medicine_id=m["id"], supplier_id=rng.choice(s.suppliers), batch_code=code,
                                           expiry_date=expiry, cost_price=cost, mrp=m["mrp"]), s.pharm)
        L.insert("stock_movement", dict(stock_batch_id=bid, movement_type="received", quantity_delta=qty,
                                        occurred_at=at(-days_ago, 11)), s.pharm)
        s.batches.append(dict(id=bid, key=key, med=m["id"], expiry=expiry, left=qty, mrp=m["mrp"], tag=tag, code=code))
        return bid
    for i, m in enumerate(MEDS):
        key = m[0]
        if key in NO_STOCK:
            continue
        if key == "last":
            s.last_batch = receive(key, "DEMO-LASTSTRIP", TODAY + timedelta(days=500), 1, 5, "last")
            continue
        qty = rng.choice([40, 60, 100, 150, 200]) if m[6] not in ("vial", "ampoule", "bag") else rng.choice([20, 30, 50])
        receive(key, f"B{i:02d}A", TODAY + timedelta(days=rng.randint(300, 800)), qty, 60)
        if rng.random() < 0.4:
            receive(key, f"B{i:02d}B", TODAY + timedelta(days=rng.randint(120, 260)), qty // 2, 20)
    receive("azith", "AZ-NEAR", TODAY + timedelta(days=30), 20, 150, "near_expiry")
    receive("cefi", "CF-EXPIRED", TODAY - timedelta(days=20), 15, 300, "expired")


# ------------------------------------------------------------------ stage 3: patients
DUPS = [  # (given, family, dob, phone_key) first of each pair is the "original"
    (("Ramesh", "Sharma", "1985-03-12", "A"), ("Rameshwar", "Sarma", "1985-03-12", "A")),
    (("Priya", "Verma", "1992-07-04", "B"), ("Priya", "Varma", "1992-07-04", "B2")),
    (("Mohammad", "Irfan", "1978-11-21", "C"), ("Mohd", "Irfan", "1978-11-21", "C2")),
    (("Sunita", "Devi", "1969-01-30", "D"), ("Sunitha", "Devi", "1969-01-30", "D")),
    (("Anil", "Kumar", "2001-05-15", "E"), ("Anil", "Kumaar", "2001-05-15", "E2")),
    (("Gurpreet", "Singh", "1988-09-09", "F"), ("Gurpreet", "Sing", "1988-09-09", "F2")),
]


def make_code(L, s, kind_names, fallback):
    for k in kind_names:
        with contextlib.suppress(Exception):
            return L.call("next_code", k, outs=1)
    s.seq += 1
    s.code_fallback = True
    return fallback % s.seq


def add_patient(L, s, given, family, dob, gender, phone_, key=None, aadhaar=None):
    code = make_code(L, s, ["patient", "PATIENT"], s.clinic_code + "-%06d")
    pid = L.insert("patient", dict(display_code=code, registration_kind="registered", given_name=given,
                                   family_name=family, date_of_birth=dob, gender=L.pick("patient", "gender", [gender]),
                                   aadhaar_last4=aadhaar), s.recep)
    L.insert("patient_phone", dict(patient_id=pid, phone_sequence=1, phone_number=phone_, valid_from=TODAY - timedelta(days=300),
                                   is_primary=1), s.recep)
    s.patients.append(dict(id=pid, given=given, family=family, dob=dob, phone=phone_, code=code))
    return pid


def stage_patients(L, s):
    L.act(s.recep)
    phones = {}
    for pair in DUPS:
        for (g, f, d, pk) in pair:
            phones.setdefault(pk, fk.phone(rng))
            gender = "female" if g in fk.FEMALE or g.startswith("Sunit") else "male"
            pid = add_patient(L, s, g, f, datetime.strptime(d, "%Y-%m-%d").date(), gender, phones[pk])
            s.byname[f"{g} {f}"] = pid
    for _ in range(28):
        g, f, gender = fk.person(rng)
        s.byname[f"{g} {f} #{len(s.patients)}"] = add_patient(L, s, g, f, fk.dob(rng, TODAY), gender, fk.phone(rng),
                                                              aadhaar=f"{rng.randint(0, 9999):04d}" if rng.random() < .3 else None)
    # story patient with a penicillin allergy (used by the allergy story)
    s.allergic = add_patient(L, s, "Meena", "Patel", TODAY - timedelta(days=365 * 41), "female", fk.phone(rng))
    # emergency unknown patient
    code = make_code(L, s, ["temp_patient", "TEMP", "TMP", "unknown_patient"], "TMP-%05d")
    s.unknown = L.insert("patient", dict(display_code=code, registration_kind="emergency_unknown", approximate_age=35,
                                         gender=L.pick("patient", "gender", ["male"])), s.recep)
    # allergies recorded by doctor, attendants by reception
    d0 = s.staff["dr_mehta"]
    a1 = L.insert("patient_allergy", dict(patient_id=s.allergic, ingredient_id=s.ing[STORY_ALLERGY_INGREDIENT],
                                          reaction="Skin rash and itching", severity="moderate"), d0)
    L.insert("patient_allergy_status_event", dict(patient_allergy_id=a1, status="confirmed", reason="Patient reported"), d0)
    for p in rng.sample(s.patients, 7):
        a = L.insert("patient_allergy", dict(patient_id=p["id"], allergen_text=rng.choice(["Sulfa drugs", "Dust", "Egg", "Peanuts", "Penicillin"]),
                                             reaction="Rash", severity=rng.choice(["mild", "moderate"])), d0)
        L.insert("patient_allergy_status_event", dict(patient_allergy_id=a, status="confirmed", reason="Patient reported"), d0)
    for p in rng.sample(s.patients, 10):
        s.attendant[p["id"]] = L.insert("patient_attendant", dict(patient_id=p["id"], name=f"{rng.choice(fk.MALE)} {p['family']}",
                                                                  relationship=rng.choice(["Husband", "Son", "Father", "Brother"]),
                                                                  phone=fk.phone(rng), is_emergency_contact=1), s.recep)


# ------------------------------------------------------------------ stage 4: schedules and slots
def stage_schedule(L, s):
    L.act(s.owner)
    sched = {}
    for d in s.doctors:
        for wd in range(1, 7):
            sid = L.insert("doctor_schedule", dict(doctor_staff_id=d.staff_id, weekday=wd if WEEKDAY_STYLE == "iso" else wd - 1,
                                                   start_time="10:00:00", end_time="13:00:00", slot_minutes=15,
                                                   valid_from=TODAY - timedelta(days=60), schedule_state="active",
                                                   occurred_at=at(-60, 9)), s.owner)
            sched[(d.staff_id, wd)] = sid
    L.act(s.sys)
    for off in range(-14, 15):
        day = TODAY + timedelta(days=off)
        wd = day.isoweekday()
        if wd == 7:
            continue
        for d in s.doctors:
            for k in range(12):
                start = datetime.combine(day, datetime.min.time()) + timedelta(hours=10, minutes=15 * k)
                slot = L.insert("appointment_slot", dict(doctor_schedule_id=sched[(d.staff_id, wd)], doctor_staff_id=d.staff_id,
                                                         slot_start=start, slot_end=start + timedelta(minutes=15),
                                                         occurred_at=at(-60, 9)), s.sys)
                s.slots[(d.staff_id, off)].append([slot, start, False])


def pick_slot(s, doc, off):
    for sl in s.slots.get((doc.staff_id, off), []):
        if not sl[2]:
            sl[2] = True
            return sl[0], sl[1]
    return None


# ------------------------------------------------------------------ clinical helpers
def pick_batch(s, med_id, qty, day):
    ok = [b for b in s.batches if b["med"] == med_id and b["expiry"] > day and b["left"] >= qty and b["tag"] != "expired"]
    return min(ok, key=lambda b: b["expiry"]) if ok else None


def trail(L, vid, steps, t0):
    for i, (st, actor, m) in enumerate(steps, 1):
        L.insert("visit_status_history", dict(visit_id=vid, status_seq=i, status=st, occurred_at=t0 + timedelta(minutes=m)), actor)


def prescribe(L, s, vid, doc, items, t):
    A = s.staff_by_id[doc.staff_id]
    p = L.insert("prescription", dict(visit_id=vid, occurred_at=t), A)
    ver = L.insert("prescription_version", dict(prescription_id=p, version_number=1, change_reason="Initial prescription", occurred_at=t), A)
    out = []
    for n, it in enumerate(items, 1):
        m = s.med[it["key"]] if it.get("key") else None
        ln = L.insert("prescription_line", dict(prescription_id=p, line_no=n, occurred_at=t), A) or \
            L.one("SELECT prescription_line_id FROM prescription_line WHERE prescription_id=%s AND line_no=%s", (p, n))
        row = dict(prescription_version_id=ver, prescription_line_id=ln, prescription_id=p, item_change="added",
                   medicine_id=m["id"] if m else None, free_text_medicine_name=it.get("free"),
                   chosen_dose_text=it.get("dose") or (m and m["dose"]), chosen_schedule=it.get("sch") or (m and m["sch"]),
                   food_instruction=it.get("food") or (m and m["food"]), duration_days=it.get("days") or (m and m["days"]),
                   quantity_prescribed=it["qty"], occurred_at=t)
        itid = L.insert("prescription_item", row, A)
        L.insert("prescription_line_status", dict(prescription_line_id=ln, status="prescribed", occurred_at=t), A)
        out.append(dict(item=itid, key=it.get("key"), med=m["id"] if m else None, qty=it["qty"], line=ln))
    return p, ver, out


def dispense(L, s, item, t):
    if not item["med"]:
        return None
    b = pick_batch(s, item["med"], item["qty"], t.date())
    if not b:
        return None
    d = L.insert("dispensing", dict(source_type="clinic_prescription", medicine_id=item["med"], prescription_item_id=item["item"], occurred_at=t), s.pharm)
    m = L.insert("stock_movement", dict(stock_batch_id=b["id"], movement_type="dispensed", quantity_delta=-item["qty"],
                                        unit_price_snapshot=b["mrp"], dispensing_id=d, occurred_at=t), s.pharm)
    b["left"] -= item["qty"]
    return dict(dispensing=d, movement=m, qty=item["qty"], price=b["mrp"], key=item["key"], batch=b["id"])


def make_bill(L, s, vid, t, lines):
    s.bill_no += 1
    bill = L.insert("bill", dict(visit_id=vid, bill_number=f"B-{s.bill_no:06d}", occurred_at=t), s.recep)
    total = D("0")
    for n, ln in enumerate(lines, 1):
        L.insert("bill_line", dict(bill_id=bill, line_no=n, line_type=ln["type"], price_list_id=ln.get("pl"),
                                   source_stock_movement_id=ln.get("mv"), lab_order_id=ln.get("lab"),
                                   short_stay_episode_id=ln.get("stay"), description_snapshot=ln["desc"], quantity=ln["qty"],
                                   unit_price_snapshot=ln["price"], price_source=ln["src"], is_manual_price=0,
                                   is_price_deviation=0, occurred_at=t), ln.get("by", s.recep))
        total += D(ln["qty"]) * D(ln["price"])
    return bill, total, len(lines)


def finalize_and_pay(L, s, bill, total, t, mode):
    """mode: full | partial | none | draft. Returns paid amount."""
    if mode == "draft":
        return D("0")
    L.insert("bill_finalization", dict(bill_id=bill, reason="Bill checked", occurred_at=t), s.acct)
    paid = D("0")
    if mode in ("full", "partial") and total > 0:
        amt = total if mode == "full" else (total * D("0.6")).quantize(D("1"))
        first = amt if rng.random() < .7 else (amt / 2).quantize(D("0.01"))
        for a in ([amt] if first == amt else [first, amt - first]):
            s.rcpt += 1
            L.insert("payment", dict(bill_id=bill, payment_kind="payment", amount=a, method=rng.choice(["cash", "upi", "upi", "card"]),
                                     receipt_number=f"R-{s.rcpt:06d}", occurred_at=t + timedelta(minutes=2)), s.acct)
            paid += a
    return paid


def discharge(L, s, vid, doc, t, basis="bill_settled", approval=None, extra=None):
    A = s.staff_by_id[doc.staff_id]
    sig = L.insert("clinical_signoff", dict(visit_id=vid, discharge_instructions="Rest, plenty of fluids, review if fever persists > 3 days.", occurred_at=t), A)
    row = dict(visit_id=vid, clinical_signoff_id=sig, financial_gate_basis=basis, approval_request_id=approval, occurred_at=t + timedelta(minutes=3))
    if extra:
        row.update(extra)
    L.insert("discharge", row, s.recep)
    L.insert("visit_closure", dict(visit_id=vid, closure_kind="discharged", occurred_at=t + timedelta(minutes=3)), s.recep)


# ------------------------------------------------------------------ visit "case" runner
def run_case(L, s, c):
    doc, pid, off = c["doctor"], c["patient"], c["off"]
    A = s.staff_by_id[doc.staff_id]
    appt, base = None, c["when"]
    if c.get("appointment"):
        slot = pick_slot(s, doc, off)
        if slot:
            base = slot[1]
            tb = base - timedelta(days=1)
            appt = L.insert("appointment", dict(patient_id=pid, doctor_staff_id=doc.staff_id, booking_channel="phone", occurred_at=tb), s.recep)
            L.insert("appointment_slot_claim", dict(appointment_id=appt, appointment_slot_id=slot[0], claim_reason="initial", occurred_at=tb), s.recep)
            L.insert("appointment_status_history", dict(appointment_id=appt, status_seq=1, status="Booked", occurred_at=tb), s.recep)
            L.insert("appointment_status_history", dict(appointment_id=appt, status_seq=2, status="Checked-in", occurred_at=base - timedelta(minutes=8)), s.recep)
    t = base
    vid = L.insert("visit", dict(patient_id=pid, appointment_id=appt, entry_type="appointment" if appt else c.get("entry", "walk_in"),
                                 occurred_at=t - timedelta(minutes=8)), s.recep)
    L.insert("visit_doctor_assignment", dict(visit_id=vid, doctor_staff_id=doc.staff_id, assignment_role="attending", occurred_at=t - timedelta(minutes=8)), s.recep)
    s.visits.append(dict(id=vid, patient=pid, doc=doc, t=t, tag=c.get("tag", "")))
    if c.get("live"):                                   # in-progress visit today (queue board demo)
        steps = [("Arrived", s.recep, 0), ("Waiting", s.recep, 2)] + ([("With Doctor", A, 12)] if c["live"] == "with_doctor" else [])
        trail(L, vid, steps, t - timedelta(minutes=8))
        return vid
    steps = [("Arrived", s.recep, 0), ("Waiting", s.recep, 2), ("With Doctor", A, 10)]
    L.insert("diagnosis", dict(visit_id=vid, diagnosis_text=c.get("dx", rng.choice(["Viral fever", "Acute gastritis", "URTI", "Allergic rhinitis", "Migraine", "Back strain"])),
                               occurred_at=t + timedelta(minutes=12)), A)
    L.insert("clinical_note", dict(visit_id=vid, note_text="Examined; advice given.", sharing_classification="shareable", occurred_at=t + timedelta(minutes=14)), A)
    lines = [dict(type="price_list_item", desc=s.price["CONSULT-GEN"][2], qty=1, price=s.price["CONSULT-GEN"][1],
                  src="price_list", pl=s.price["CONSULT-GEN"][0], by=s.recep)]
    if c.get("meds"):
        items = [dict(key=k, qty=q) for k, q in c["meds"]]
        _, _, pres = prescribe(L, s, vid, doc, items, t + timedelta(minutes=15))
        steps.append(("Pharmacy", s.pharm, 25))
        for it in pres:
            d = dispense(L, s, it, t + timedelta(minutes=28))
            if d:
                lines.append(dict(type="medicine", desc=s.med[d["key"]]["brand"], qty=d["qty"], price=d["price"], src="batch_mrp", mv=d["movement"], by=s.pharm))
    for lab in c.get("labs", []):
        lines += lab_flow(L, s, vid, pid, doc, t, lab)
    steps.append(("Billing", s.recep, 35))
    trail(L, vid, steps, t)
    mode = c.get("pay", "full")
    bill, total, _ = make_bill(L, s, vid, t + timedelta(minutes=35), lines)
    s.bills[vid] = dict(bill=bill, total=total)
    paid = finalize_and_pay(L, s, bill, total, t + timedelta(minutes=38), mode)
    if mode == "full" and c.get("discharge", True):
        discharge(L, s, vid, doc, t + timedelta(minutes=42))
        L.insert("visit_status_history", dict(visit_id=vid, status_seq=len(steps) + 1, status="Discharged", occurred_at=t + timedelta(minutes=45)), s.recep)
    return vid


# ------------------------------------------------------------------ labs
def lab_flow(L, s, vid, pid, doc, t, lab):
    """lab: dict(test, price, report: none|reviewed|critical_unreviewed, billed: bool, days_ago)"""
    A = s.staff_by_id[doc.staff_id]
    t0 = t + timedelta(minutes=16)
    lid = L.insert("lab_order", dict(visit_id=vid, outside_lab_id=rng.choice(s.labs), test_name=lab["test"], occurred_at=t0), A)
    L.insert("lab_order_status", dict(lab_order_id=lid, status="ordered", occurred_at=t0), A)
    L.insert("lab_order_status", dict(lab_order_id=lid, status="sample_collected", occurred_at=t0 + timedelta(minutes=10)), s.nurse1)
    L.insert("lab_order_status", dict(lab_order_id=lid, status="sent_to_lab", occurred_at=t0 + timedelta(minutes=30)), s.nurse1)
    expected = D(lab["price"])
    entered = D(lab.get("entered", lab["price"]))
    dev = 1 if abs(entered - expected) > expected * D("0.2") else 0
    L.insert("lab_order_price", dict(lab_order_id=lid, entered_price=entered, price_basis="manual", expected_price=expected,
                                     is_price_deviation=dev, deviation_reason="Lab charged more this time" if dev else None,
                                     occurred_at=t0 + timedelta(minutes=31)), s.recep)
    rep = lab.get("report", "none")
    if rep != "none":
        tr = t0 + timedelta(hours=20)
        rid = L.insert("lab_report", dict(lab_order_id=lid, version_number=1, file_path=f"demo_reports/{lid}_{lab['test'].replace(' ', '_')}.pdf",
                                          content_hash=hashlib.sha256(f"demo-{lid}".encode()).hexdigest(), source="from_lab", occurred_at=tr), s.nurse1)
        L.insert("lab_report_identifier_check", dict(lab_report_id=rid, identifier_kind="patient_name", value_seen="as on order", matches_order=1, occurred_at=tr), s.nurse1)
        L.insert("lab_report_identifier_check", dict(lab_report_id=rid, identifier_kind="date_of_birth", value_seen="as on order", matches_order=1, occurred_at=tr), s.nurse1)
        if rep == "critical_unreviewed":
            L.insert("lab_report_critical_mark", dict(lab_report_id=rid, mark="critical", mark_source="nurse_observation", reason="Lab flagged value as critical", occurred_at=tr), s.nurse1)
        else:
            L.insert("lab_report_review", dict(lab_report_id=rid, review_note="Reviewed. Advice given to patient.", occurred_at=tr + timedelta(hours=2)), A)
    if lab.get("billed", True):
        return [dict(type="lab_test", desc=f"Lab: {lab['test']}", qty=1, price=entered, src="lab_entered", lab=lid, by=s.recep)]
    return []


# ------------------------------------------------------------------ stage 5: ordinary visits
def stage_visits(L, s):
    pats = [p["id"] for p in s.patients]
    cases = []
    medsets = [[("para", 10)], [("para", 10), ("ceti", 5)], [("amox", 10), ("para", 6), ("panto", 14)], [("azith", 3), ("cetr", 5)],
               [("ibu", 9), ("panto", 10)], [("dom", 9), ("ors", 6)], [("metro", 15), ("ome", 10)], [("cipro", 10), ("para", 6)]]
    medsets = [[(k if k != "cetr" else "ceti", q) for k, q in ms] for ms in medsets]
    for i in range(22):
        off = -rng.randint(1, 13)
        day = TODAY + timedelta(days=off)
        r = rng.random()
        cases.append(dict(doctor=rng.choice(s.doctors), patient=rng.choice(pats), off=off, when=datetime.combine(day, datetime.min.time()) + timedelta(hours=10, minutes=rng.randint(0, 150)),
                          appointment=rng.random() < .6, meds=rng.choice(medsets), pay="full" if r < .7 else ("partial" if r < .85 else "draft"),
                          tag="filler"))
    # one patient who has seen EVERY doctor (relational division demo)
    div_p = s.byname["Priya Verma"]
    for j, d in enumerate(s.doctors):
        cases.append(dict(doctor=d, patient=div_p, off=-(3 + j), when=at(-(3 + j), 11, 5 * j), meds=[("para", 6)], pay="full", tag="division"))
    # today's live queue (real-time, not backfilled)
    live_p = rng.sample(pats, 4)
    for j, (pid, kind) in enumerate(zip(live_p, ["waiting", "waiting", "with_doctor", "with_doctor"])):
        cases.append(dict(doctor=s.doctors[j % 3], patient=pid, off=0, when=mins_ago(30 - j * 6), live=kind, tag="today_live"))
    # lab cases
    cases += [
        dict(doctor=s.doctors[0], patient=rng.choice(pats), off=-6, when=at(-6, 10, 30), meds=[("para", 6)], tag="lab_reviewed",
             labs=[dict(test="CBC", price="350", report="reviewed")]),
        dict(doctor=s.doctors[1], patient=rng.choice(pats), off=-10, when=at(-10, 11, 0), meds=[("ceti", 5)], tag="lab_billed_no_report",
             labs=[dict(test="Widal test", price="300", entered="480", report="none")]),
        dict(doctor=s.doctors[2], patient=rng.choice(pats), off=-3, when=at(-3, 12, 0), meds=[("para", 6)], tag="lab_critical_unreviewed",
             labs=[dict(test="Blood sugar fasting", price="120", report="critical_unreviewed", billed=False)]),
    ]
    for i, c in enumerate(cases):
        with L.unit(f"visit#{i} {c.get('tag')}"):
            run_case(L, s, c)


# ------------------------------------------------------------------ stage 6: stories
def story_allergy(L, s):
    doc = s.doctors[0]
    A = s.staff_by_id[doc.staff_id]
    t = at(-2, 10, 40)
    vid = L.insert("visit", dict(patient_id=s.allergic, entry_type="walk_in", occurred_at=t), s.recep)
    L.insert("visit_doctor_assignment", dict(visit_id=vid, doctor_staff_id=doc.staff_id, assignment_role="attending", occurred_at=t), s.recep)
    trail(L, vid, [("Arrived", s.recep, 0), ("With Doctor", A, 8)], t)
    _, _, items = prescribe(L, s, vid, doc, [dict(key="amox", qty=10), dict(free="Zyloflam-X forte", dose="1 tab", sch="1-0-1", food="after food", days=5, qty=10)], t + timedelta(minutes=15))
    cat, free = items
    shown = "Amoxicillin: rash and itching (moderate)"
    c1 = L.insert("allergy_confirmation", dict(prescription_item_id=cat["item"], confirmation_type="generic_name_warning", displayed_allergies_snapshot=shown,
                                               was_overridden=1, override_reason="Tolerated before under supervision; patient counselled", occurred_at=t + timedelta(minutes=15)), A)
    L.insert("allergy_confirmation", dict(prescription_item_id=free["item"], confirmation_type="free_text_medicine", displayed_allergies_snapshot=shown,
                                          was_overridden=0, occurred_at=t + timedelta(minutes=15)), A)
    L.insert("catalog_review_queue", dict(prescription_item_id=free["item"], submitted_name="Zyloflam-X forte", occurred_at=t + timedelta(minutes=15)), A)
    L.call("raise_alert", "ALLERGY_OVERRIDE", "INTERRUPT", "allergy_confirmation", c1, "Allergy warning overridden for Amoxiclav", actor=A)
    L.insert("prescription_line_status", dict(prescription_line_id=free["line"], status="bought_outside", reason="Not in clinic stock", occurred_at=t + timedelta(minutes=30)), s.pharm)
    s.story["allergy_visit"] = vid


def story_stay(L, s):
    doc = s.doctors[0]
    A = s.staff_by_id[doc.staff_id]
    pid = s.byname["Ramesh Sharma"]
    t = at(-2, 9, 30)
    vid = L.insert("visit", dict(patient_id=pid, entry_type="walk_in", occurred_at=t), s.recep)
    L.insert("visit_doctor_assignment", dict(visit_id=vid, doctor_staff_id=doc.staff_id, assignment_role="attending", occurred_at=t), s.recep)
    st = []
    st.append(("Arrived", s.recep, 0)); st.append(("With Doctor", A, 10))
    trail(L, vid, st, t)
    stay = L.insert("short_stay_episode", dict(visit_id=vid, admission_reason="Dehydration - IV fluids and observation", occurred_at=t + timedelta(minutes=20)), A)
    seq = 0
    for k, (sbp, dbp, pulse, temp, spo2) in enumerate([(100, 66, 104, 38.2, 97), (108, 70, 98, 37.8, 98), (114, 74, 90, 37.2, 98), (120, 78, 84, 36.9, 99)]):
        setid = str(uuid.uuid4())
        for typ, val, unit in [("bp_systolic", sbp, "mmHg"), ("bp_diastolic", dbp, "mmHg"), ("pulse", pulse, "bpm"), ("temperature", temp, "C"), ("spo2", spo2, "%")]:
            seq += 1
            L.insert("vitals_observation", dict(visit_id=vid, observation_seq=seq, short_stay_episode_id=stay, observation_set_uuid=setid,
                                                observation_type=typ, numeric_value=val, unit=unit, occurred_at=t + timedelta(minutes=30 + 90 * k)), s.nurse1)
    _, _, pres = prescribe(L, s, vid, doc, [dict(key="ondem", qty=10)], t + timedelta(minutes=22))
    co = L.insert("clinical_order", dict(visit_id=vid, short_stay_episode_id=stay, order_type="medication", occurred_at=t + timedelta(minutes=23)), A)
    L.insert("clinical_order_status", dict(clinical_order_id=co, status="ordered", occurred_at=t + timedelta(minutes=23)), A)
    L.insert("clinical_order_status", dict(clinical_order_id=co, status="finalized", occurred_at=t + timedelta(minutes=24)), A)
    m = s.med["ondem"]
    oi = L.insert("clinical_order_item", dict(clinical_order_id=co, item_no=1, item_kind="medication", medicine_id=m["id"], dose_value=4, dose_unit="mg",
                                              route="oral", first_scheduled_at=t + timedelta(minutes=60), repeat_interval_minutes=480,
                                              quantity_ordered=10, instructions="Ondansetron 4 mg every 8 h if vomiting", occurred_at=t + timedelta(minutes=23)), A)
    L.insert("clinical_order_item_status", dict(clinical_order_item_id=oi, status="active", occurred_at=t + timedelta(minutes=23)), A)
    d = dispense(L, s, pres[0], t + timedelta(minutes=35))
    sup = L.insert("order_item_supply", dict(clinical_order_item_id=oi, dispensing_id=d["dispensing"], quantity_allocated=10, occurred_at=t + timedelta(minutes=36)), s.pharm)
    h = L.insert("custody_handoff", dict(dispensing_id=d["dispensing"], giver_staff_id=s.pharm.staff_id, recipient_staff_id=s.nurse1.staff_id, quantity=10,
                                         handoff_purpose="supply_to_nurse", occurred_at=t + timedelta(minutes=37)), s.pharm)
    L.insert("custody_handoff_acknowledgement", dict(custody_handoff_id=h, acknowledged_quantity=10, ack_method="self_logged_staff", occurred_at=t + timedelta(minutes=38)), s.nurse1)
    for k, status in enumerate(["given", "given", "refused", "given"]):
        qty = 1 if status == "given" else 0
        L.insert("dose_administration", dict(clinical_order_item_id=oi, order_item_supply_id=sup, status=status, ordered_dose_value=4, ordered_dose_unit="mg", ordered_route="oral",
                                             actual_dose_value=4 if qty else 0, actual_dose_unit="mg", actual_route="oral", quantity_consumed=qty,
                                             deviation_reason="Patient refused (nausea)" if status == "refused" else None,
                                             occurred_at=t + timedelta(minutes=60 + 120 * k)), s.nurse1)
    # leftover: 4 returned (accepted), 3 handed to family (refused as opened)  -> 10 = 3 given + 4 + 3
    r1 = L.insert("leftover_return_request", dict(dispensing_id=d["dispensing"], quantity=4, condition_note="Strips intact, sealed", occurred_at=t + timedelta(hours=8)), s.nurse1)
    mv = L.insert("stock_movement", dict(stock_batch_id=d["batch"], movement_type="returned_by_patient", quantity_delta=4, unit_price_snapshot=d["price"],
                                         returns_stock_movement_id=d["movement"], reason="Sealed leftover accepted", occurred_at=t + timedelta(hours=8, minutes=10)), s.pharm)
    next(b for b in s.batches if b["id"] == d["batch"])["left"] += 4
    L.insert("leftover_return_decision", dict(leftover_return_request_id=r1, decision="accepted", decision_reason="Sealed and returnable", stock_movement_id=mv,
                                              occurred_at=t + timedelta(hours=8, minutes=10)), s.pharm)
    r2 = L.insert("leftover_return_request", dict(dispensing_id=d["dispensing"], quantity=3, condition_note="Strip opened", occurred_at=t + timedelta(hours=8, minutes=5)), s.nurse1)
    dec2 = L.insert("leftover_return_decision", dict(leftover_return_request_id=r2, decision="refused", decision_reason="Strip opened - cannot return to stock",
                                                     occurred_at=t + timedelta(hours=8, minutes=12)), s.pharm)
    att = s.attendant.get(pid) or L.insert("patient_attendant", dict(patient_id=pid, name="Sunita Sharma", relationship="Wife", phone=fk.phone(rng), is_emergency_contact=1), s.recep)
    h2 = L.insert("custody_handoff", dict(dispensing_id=d["dispensing"], giver_staff_id=s.nurse1.staff_id, recipient_attendant_id=att, quantity=3,
                                          handoff_purpose="leftover_to_family", leftover_return_decision_id=dec2, occurred_at=t + timedelta(hours=8, minutes=20)), s.nurse1)
    L.insert("custody_handoff_acknowledgement", dict(custody_handoff_id=h2, acknowledged_quantity=3, ack_method="witnessed_attendant",
                                                     witness_note="Handed to wife, witnessed by reception", occurred_at=t + timedelta(hours=8, minutes=21)), s.recep)
    lines = [dict(type="price_list_item", desc=s.price["CONSULT-GEN"][2], qty=1, price=s.price["CONSULT-GEN"][1], src="price_list", pl=s.price["CONSULT-GEN"][0]),
             dict(type="stay", desc=s.price["STAY-DAY"][2], qty=1, price=s.price["STAY-DAY"][1], src="price_list", pl=s.price["STAY-DAY"][0], stay=stay),
             dict(type="price_list_item", desc=s.price["DRIP-STD"][2], qty=2, price=s.price["DRIP-STD"][1], src="price_list", pl=s.price["DRIP-STD"][0], by=s.nurse1),
             dict(type="medicine", desc=m["brand"], qty=10, price=d["price"], src="batch_mrp", mv=d["movement"], by=s.pharm),
             dict(type="return_credit", desc=f"Return credit: {m['brand']}", qty=-4, price=d["price"], src="batch_mrp", mv=mv, by=s.pharm)]
    bill, total, _ = make_bill(L, s, vid, t + timedelta(hours=9), lines)
    finalize_and_pay(L, s, bill, total, t + timedelta(hours=9, minutes=5), "full")
    L.insert("short_stay_closure", dict(short_stay_episode_id=stay, reason="Treatment completed", occurred_at=t + timedelta(hours=9, minutes=10)), A)
    discharge(L, s, vid, doc, t + timedelta(hours=9, minutes=15))
    s.story["stay_visit"], s.story["stay_patient"], s.story["stay_attendant"] = vid, pid, att
    s.story["stay_dispensing"] = d["dispensing"]
    return vid


def story_open_long_stay(L, s):
    doc = s.doctors[1]
    A = s.staff_by_id[doc.staff_id]
    pid = s.byname["Anil Kumar"]
    t = NOW - timedelta(hours=30)
    vid = L.insert("visit", dict(patient_id=pid, entry_type="emergency", occurred_at=t), s.recep)
    L.insert("visit_doctor_assignment", dict(visit_id=vid, doctor_staff_id=doc.staff_id, assignment_role="attending", occurred_at=t), s.recep)
    trail(L, vid, [("Arrived", s.recep, 0), ("With Doctor", A, 5), ("Observation Area", s.nurse2, 30)], t)
    stay = L.insert("short_stay_episode", dict(visit_id=vid, admission_reason="Observation after head injury", occurred_at=t + timedelta(minutes=20)), A)
    L.call("raise_alert", "STAY_OVER_LIMIT", "INTERRUPT", "short_stay_episode", stay, "Stay is over the 24 h limit", actor=s.sys)
    s.story["long_stay_visit"] = vid


def story_partial(L, s):
    doc = s.doctors[2]
    pid = s.byname["Sunita Devi"]
    t = at(-1, 12, 10)
    lines_meds = [("amox", 10), ("para", 10), ("panto", 14)]
    vid = None
    c = dict(doctor=doc, patient=pid, off=-1, when=t, meds=lines_meds, pay="partial", discharge=False, tag="partial_payment")
    vid = run_case(L, s, c)
    b = s.bills[vid]
    due = (b["total"] * D("0.4")).quantize(D("1"))
    aid = L.call("request_approval", "partial_payment_discharge", "visit", vid, "Family will pay the balance after salary on the 5th", due, outs=1, actor=s.recep)
    L.call("decide_approval", aid, "APPROVED", "Known family, balance promised", actor=s.owner)
    L.insert("due_promise", dict(visit_id=vid, bill_id=b["bill"], promised_by_date=TODAY + timedelta(days=7), approval_request_id=aid,
                                 reason="Balance after salary", occurred_at=t + timedelta(minutes=50)), s.recep)
    discharge(L, s, vid, doc, t + timedelta(minutes=55), basis="approved_partial_payment", approval=aid)
    s.story["partial_visit"] = vid


def story_pending_approvals(L, s):
    vid = next(v for v in s.visits if v["tag"] == "filler")["id"]
    b = s.bills.get(vid)
    if b:
        L.call("request_approval", "discount", "bill", b["bill"], "Senior citizen concession requested", D("100.00"), outs=1, actor=s.recep)
    batch = next(x for x in s.batches if x["key"] == "para")
    L.call("request_approval", "stock_adjustment", "stock_batch", batch["id"], "Count mismatch found during shelf check (-5 strips)", D("0"), outs=1, actor=s.pharm)


def story_counter(L, s):
    P = s.pharm
    cust = L.insert("counter_customer", dict(phone=fk.phone(rng), display_name="Walk-in buyer"), P)
    for k in range(6):                                  # many OTC sales to one customer (pattern for R5.8)
        t = at(-rng.randint(1, 10), 15, rng.randint(0, 50))
        sale = L.insert("counter_sale", dict(counter_customer_id=cust, sale_number=f"CS-{s.cs_no + 1:06d}", occurred_at=t), P)
        s.cs_no += 1
        key = rng.choice(["para", "ors", "ceti", "bcom"])
        b = pick_batch(s, s.med[key]["id"], 6, t.date())
        d = L.insert("dispensing", dict(source_type="otc", medicine_id=s.med[key]["id"], counter_sale_id=sale, occurred_at=t), P)
        L.insert("stock_movement", dict(stock_batch_id=b["id"], movement_type="dispensed", quantity_delta=-6, unit_price_snapshot=b["mrp"], dispensing_id=d, occurred_at=t), P)
        b["left"] -= 6
        s.rcpt += 1
        L.insert("counter_sale_payment", dict(counter_sale_id=sale, amount=b["mrp"] * 6, method="cash", receipt_number=f"R-{s.rcpt:06d}", occurred_at=t), P)
    cust2 = L.insert("counter_customer", dict(phone=fk.phone(rng), display_name="Outside prescription buyer"), P)
    op = L.insert("outside_prescription", dict(counter_customer_id=cust2, prescriber_name="Dr. R. K. Saxena", prescriber_registration_no="MP-99887",
                                               prescription_date=TODAY - timedelta(days=1), occurred_at=at(-1, 16)), P)
    t = at(-1, 16, 5)
    sale = L.insert("counter_sale", dict(counter_customer_id=cust2, sale_number=f"CS-{s.cs_no + 1:06d}", occurred_at=t), P)
    s.cs_no += 1
    b = pick_batch(s, s.med["azith"]["id"], 3, t.date())
    d = L.insert("dispensing", dict(source_type="outside_prescription", medicine_id=s.med["azith"]["id"], outside_prescription_id=op, counter_sale_id=sale, occurred_at=t), P)
    L.insert("stock_movement", dict(stock_batch_id=b["id"], movement_type="dispensed", quantity_delta=-3, unit_price_snapshot=b["mrp"], dispensing_id=d, occurred_at=t), P)
    b["left"] -= 3
    s.rcpt += 1
    L.insert("counter_sale_payment", dict(counter_sale_id=sale, amount=b["mrp"] * 3, method="upi", receipt_number=f"R-{s.rcpt:06d}", occurred_at=t), P)
    L.insert("cash_handover_declaration", dict(period_start=at(-1, 0), period_end=at(0, 0), declared_amount=D("500.00"), note="Demo handover", occurred_at=at(0, 0, 5)), P)


def story_family(L, s):
    vid = s.story.get("stay_visit")
    pid = s.story.get("stay_patient")
    if not vid:
        raise RuntimeError("stay story missing, no family link")
    p = next(x for x in s.patients if x["id"] == pid)
    token = secrets.token_urlsafe(24)
    fid = L.insert("family_link", dict(visit_id=vid, attendant_id=s.story.get("stay_attendant"), token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                       second_factor_kind="date_of_birth", expires_at=NOW + timedelta(days=30), consent_note="Wife agreed to receive link",
                                       occurred_at=NOW - timedelta(hours=1)), s.recep)
    s.links.append((token, p["dob"], p["given"] + " " + p["family"], vid))
    with contextlib.suppress(Exception):
        L.call("queue_message", fk.phone(rng), "family_link", "Your visit details from Demo Clinic: link", "family_link", fid, actor=s.recep)


def stage_stories(L, s):
    for name, fn in [("allergy", story_allergy), ("short stay + leftovers", story_stay), ("open long stay", story_open_long_stay),
                     ("partial payment discharge", story_partial), ("counter sales", story_counter),
                     ("pending approvals", story_pending_approvals), ("family link", story_family)]:
        with L.unit("story: " + name):
            fn(L, s)
    for typ, sev, msg in [("DISCOUNT_GIVEN", "SUMMARY", "Discount of 100 given on a bill"), ("MANUAL_PRICE", "SUMMARY", "Manual price used on a lab test"),
                          ("OTC_PATTERN", "SUMMARY", "Same customer bought OTC 6 times this week")]:
        with L.unit("alert " + typ):
            L.call("raise_alert", typ, sev, "bill", 1, msg, actor=s.sys)


# ------------------------------------------------------------------ outputs
def write_cheatsheet(s, path: Path):
    lines = ["# DEMO CREDENTIALS (FAKE DATA ONLY)", "", f"Generated by `tools/seed_demo.py` on {NOW:%Y-%m-%d %H:%M} UTC.", "",
             f"Password for every demo staff login: `{DEMO_PASSWORD}`", "", "| login | role | person |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in s.cred]
    lines += ["", "The OWNER login is the account you created with `tools/bootstrap_owner.py`.", "", "## Family links (DOB is the second factor)", ""]
    for tok, dob_, name, vid in s.links:
        lines.append(f"- visit {vid} ({name}): `/v/{tok}`  | DOB `{dob_}`")
    lines += ["", "## Demo story IDs", ""] + [f"- {k}: {v}" for k, v in s.story.items()]
    lines += ["", "## Things planted on purpose", "",
              "- Near-duplicate patients: Ramesh Sharma/Rameshwar Sarma, Priya Verma/Varma, Mohammad/Mohd Irfan, Sunita/Sunitha Devi, Anil Kumar/Kumaar, Gurpreet Singh/Sing",
              "- Medicine with NO stock (blue label): Levoflox 500. Last-strip batch (qty 1) for concurrency demo: batch code DEMO-LASTSTRIP (Zerostock Demo 10)",
              "- Near-expiry batch AZ-NEAR (Azithral, 30 days); expired batch CF-EXPIRED (Cefix 200)",
              "- Patient Meena Patel has an Amoxicillin allergy; allergy-override story visit exists",
              "- Priya Verma has seen all 3 doctors (relational division query)",
              "- Open stay older than 24 h (Anil Kumar); partial-payment discharge (Sunita Devi); 3 lab cases; 6 OTC sales to one customer",
              "- 4 live visits today for the queue board; 2 pending approval requests for the owner inbox"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="clinic_db")
    ap.add_argument("--check", action="store_true", help="lenient run, then ROLLBACK (nothing saved); prints contract report")
    ap.add_argument("--dry-run", action="store_true", help="no database; tests python logic only")
    a = ap.parse_args()
    s = S()
    s.price, s.suppliers, s.med, s.batches, s.patients, s.byname = {}, [], {}, [], [], {}
    s.attendant, s.slots, s.visits, s.bills, s.story, s.links = {}, defaultdict(list), [], {}, {}, []
    s.seq = s.bill_no = s.rcpt = s.cs_no = 0
    s.code_fallback = False
    conn = None
    if a.dry_run:
        L = DryLoader()
    else:
        conn = connect(a.db)
        L = Loader(conn, a.db, lenient=a.check)
    stage_reference(L, s)
    s.staff_by_id = {v.staff_id: v for v in s.staff.values()}
    with L.unit("catalog", critical=True):
        stage_catalog(L, s)
    with L.unit("patients", critical=True):
        stage_patients(L, s)
    with L.unit("schedule"):
        stage_schedule(L, s)
    stage_visits(L, s)
    stage_stories(L, s)
    L.print_report()
    if s.code_fallback:
        print("\n(!) next_code() could not be used for some codes -> display codes were generated by the seeder. "
              "Update id_counter.next_value or later registrations may collide (see docs/PHASE4_GUIDE.md).")
    if a.dry_run:
        return
    if a.check:
        conn.rollback()
        print("\n--check: everything rolled back. Fix the report above, then run without --check.")
        return
    conn.commit()
    write_cheatsheet(s, ROOT / "db" / "seed" / "DEMO_CREDENTIALS.md")
    print("\nSeed committed. Credentials/IDs written to db/seed/DEMO_CREDENTIALS.md")


if __name__ == "__main__":
    main()
