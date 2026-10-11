#!/usr/bin/env python3
"""gen_bulk_data.py - P4-2 bulk FAKE data into a SEPARATE database `clinic_bench` (never touches clinic_db).

    python tools/gen_bulk_data.py --scale small  --recreate     # ~2 min, try this first
    python tools/gen_bulk_data.py --scale full   --recreate     # 100k patients / 300k visits / 1M audit rows
    python tools/gen_bulk_data.py --check --recreate             # seconds: proves my column names match your schema

clinic_bench is built from db/schema/*.sql ONLY (tables + FKs, NO triggers/procedures): benchmarking indexes must not be
slowed by 352 triggers, and the bulk loader supplies the event columns itself. Rows get explicit primary keys, so
foreign keys are consistent even though FK checks are switched off during the load.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p4_fake as fk                                                  # noqa: E402
from p4_lib import ROOT, Actor, Loader, connect, run_sql_text, utcnow  # noqa: E402

SCALES = {  # patients, visits, medicines, audit rows, alerts, family links
    "small": dict(patients=5_000, visits=15_000, medicines=2_000, audit=50_000, alerts=2_000, links=3_000),
    "medium": dict(patients=30_000, visits=90_000, medicines=10_000, audit=300_000, alerts=8_000, links=15_000),
    "full": dict(patients=100_000, visits=300_000, medicines=20_000, audit=1_000_000, alerts=20_000, links=50_000),
}
SCHEMA_FILES = ["01_core_settings_staff_audit.sql", "02_patients_visits.sql", "03_pharmacy.sql",
                "04_orders_billing_discharge.sql", "09_foreign_keys.sql"]
BENCH = "clinic_bench"
TODAY_VISITS = 150          # visits "today" (feeds the queue benchmark)
HISTORY_DAYS = 730


def recreate(db: str):
    print(f"Re-creating {db} from db/schema/*.sql (no triggers) ...")
    run_sql_text(f"DROP DATABASE IF EXISTS {db}; CREATE DATABASE {db} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;")
    for f in SCHEMA_FILES:
        txt = (ROOT / "db" / "schema" / f).read_text(encoding="utf-8").replace("USE clinic_db;", f"USE {db};")
        run_sql_text(txt, db)
        print("  loaded", f)


def ensure_ref(L: Loader, owner_id: int):
    """roles + 13 staff with fixed ids (1 owner, 2-4 doctors, 5 recep, 6-7 nurse, 8 pharm, 9 acct, 10-13 system)."""
    cur = L.cur
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    roles = ["OWNER", "DOCTOR", "RECEPTIONIST", "NURSE", "PHARMACY", "ACCOUNTANT", "SYSTEM"]
    have = {}
    cur.execute("SELECT role_code, role_id FROM role")
    have.update({c: i for c, i in cur.fetchall()})
    L.act(Actor(owner_id, "OWNER"))
    L.esc = False
    for code in roles:
        if code not in have:
            have[code] = L.insert("role", dict(role_code=code, role_name=code.title()))
    L._roles = have
    plan = [(1, "OWNER"), (2, "DOCTOR"), (3, "DOCTOR"), (4, "DOCTOR"), (5, "RECEPTIONIST"), (6, "NURSE"), (7, "NURSE"),
            (8, "PHARMACY"), (9, "ACCOUNTANT"), (10, "SYSTEM"), (11, "SYSTEM"), (12, "SYSTEM"), (13, "SYSTEM")]
    for sid, role in plan:
        cur.execute("SELECT 1 FROM staff WHERE staff_id=%s", (sid,))
        if cur.fetchone():
            continue
        L.insert("staff", dict(staff_id=sid, display_code=f"BNCH-{sid:02d}", principal_kind="system" if role == "SYSTEM" else "human",
                               given_name=f"Bench{sid}", family_name="Staff", active_from=utcnow().date() - timedelta(days=800),
                               login_subject=None if role == "SYSTEM" else f"bench{sid}"))
        L.insert("staff_role", dict(staff_id=sid, role_id=have[role], valid_from=utcnow().date() - timedelta(days=800), granted_by=1))
    L.conn.commit()
    L.esc = True
    return have


class Esc:
    """Builds the standard event columns for a table once; .cols / .vals(t, staff_id, role)."""

    def __init__(self, L: Loader, table: str, roles: dict):
        info = L.schema.cols(table)
        self.cols = [c for c in ("occurred_at", "recorded_at", "recorded_by", "recorded_by_role", "is_backfilled", "request_uuid") if c in info]
        self.L, self.roles, self.info = L, roles, info

    def vals(self, t: datetime, staff: int, role: str = "RECEPTIONIST"):
        out = []
        for c in self.cols:
            if c in ("occurred_at", "recorded_at"):
                out.append(t)
            elif c == "recorded_by":
                out.append(staff)
            elif c == "recorded_by_role":
                out.append(role if self.info[c].data_type in ("varchar", "char", "enum") else self.roles[role])
            elif c == "is_backfilled":
                out.append(0)
            else:
                u = uuid.uuid4()
                out.append(u.bytes if self.info[c].data_type in ("binary", "varbinary") else str(u))
        return tuple(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", choices=SCALES, default="medium")
    for k in ("patients", "visits", "medicines", "audit", "alerts", "links"):
        ap.add_argument(f"--{k}", type=int)
    ap.add_argument("--recreate", action="store_true", help="drop and rebuild clinic_bench first")
    ap.add_argument("--check", action="store_true", help="tiny load (50 patients, 200 visits) to prove column names work; combine with --recreate")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    n = dict(SCALES[a.scale])
    if a.check:     # tiny load: proves every column name works, takes seconds
        n = dict(patients=50, visits=200, medicines=20, audit=100, alerts=20, links=10)
    for k in n:
        if getattr(a, k) is not None:
            n[k] = getattr(a, k)
    if a.recreate:
        recreate(BENCH)
    conn = connect(BENCH)
    L = Loader(conn, BENCH)
    try:
        L.cur.execute("SET SESSION sql_log_bin=0")
    except Exception:
        pass
    L.cur.execute("SET FOREIGN_KEY_CHECKS=0, UNIQUE_CHECKS=0")
    rng = random.Random(a.seed)
    roles = ensure_ref(L, 1)
    now, today = utcnow(), utcnow().date()
    midnight = datetime.combine(today, datetime.min.time())

    def rand_time(days_back_max=HISTORY_DAYS):
        return midnight - timedelta(days=rng.randint(1, days_back_max)) + timedelta(hours=rng.randint(9, 18), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))

    t0 = datetime.now()
    # ---------------- suppliers, medicines, batches
    E = lambda table: Esc(L, table, roles)                      # noqa: E731
    L.bulk("supplier", ["supplier_id", "name", "phone", "contact_name", "notes"],
           [(i, f"Supplier {i}", fk.phone(rng), f"Contact {i}", "bench") for i in range(1, 21)])
    meds = fk.medicine_names(rng, n["medicines"])
    forms = ["tablet", "capsule", "syrup", "injection", "ointment"]
    L.bulk("medicine", ["medicine_id", "brand_name", "generic_display_name", "strength_value", "strength_unit", "dosage_form",
                        "stock_unit", "schedule_class", "returnable", "active"],
           [(i, b, g, 500, "mg", forms[i % 5], "tablet", ("H" if i % 3 == 0 else "none"), i % 4 == 0, 1) for i, (b, g) in enumerate(meds, 1)])
    nb = len(meds) * 2
    batches = []                                                  # (batch_id, medicine_id, mrp)
    def gen_batches():
        bid = 0
        for m in range(1, len(meds) + 1):
            for k in range(2):
                bid += 1
                mrp = round(rng.uniform(2, 250), 2)
                exp = today + timedelta(days=rng.randint(-60, 900))
                batches.append((bid, m, mrp))
                yield (bid, m, rng.randint(1, 20), f"BN{m}-{k}", exp, round(mrp * .65, 2), mrp)
    L.bulk("stock_batch", ["stock_batch_id", "medicine_id", "supplier_id", "batch_code", "expiry_date", "cost_price", "mrp"], gen_batches())
    esm = E("stock_movement")
    L.bulk("stock_movement", ["stock_batch_id", "movement_type", "quantity_delta"] + esm.cols,
           ((b, "received", rng.randint(50, 300)) + esm.vals(now - timedelta(days=rng.randint(30, 700)), 8, "PHARMACY") for b, _, _ in batches))

    # ---------------- patients
    pcols = ["patient_id", "display_code", "registration_kind", "given_name", "family_name", "date_of_birth", "gender"]
    probes = {}
    def gen_patients():
        for i in range(1, n["patients"] + 1):
            g, f, gender = fk.person(rng)
            if i == n["patients"] // 3:
                probes["name"] = (g, f)
            yield (i, f"CLN1-{i:06d}", "registered", g, f, fk.dob(rng, today), gender)
    L.bulk("patient", pcols, gen_patients())
    phones = []
    def gen_phones():
        for i in range(1, n["patients"] + 1):
            p = fk.phone(rng)
            phones.append(p)
            yield (i, 1, p, today - timedelta(days=rng.randint(10, 700)), 1)
            if rng.random() < 0.2:
                yield (i, 2, fk.phone(rng), today - timedelta(days=5), 0)
    L.bulk("patient_phone", ["patient_id", "phone_sequence", "phone_number", "valid_from", "is_primary"], gen_phones())
    pa = E("patient_allergy")
    L.bulk("patient_allergy", ["patient_id", "allergen_text", "reaction", "severity"] + pa.cols,
           ((rng.randint(1, n["patients"]), rng.choice(["Penicillin", "Sulfa", "Dust", "Egg"]), "Rash", "mild") + pa.vals(rand_time(), 2, "DOCTOR")
            for _ in range(n["patients"] // 7)))

    # ---------------- visits, assignment, status history, bills, lines, payments
    ev, eh, ea, eb, el, ep = (E("visit"), E("visit_status_history"), E("visit_doctor_assignment"), E("bill"), E("bill_line"), E("payment"))
    vtimes = {}
    def visit_time(i):
        if i <= TODAY_VISITS:
            return midnight + timedelta(hours=8, minutes=rng.randint(0, max(1, int((now - midnight).total_seconds() // 60) - 480)))
        return rand_time()
    def gen_visits():
        for i in range(1, n["visits"] + 1):
            t = visit_time(i)
            vtimes[i] = t
            yield (i, rng.randint(1, n["patients"]), rng.choice(["appointment", "walk_in", "walk_in", "emergency"])) + ev.vals(t, 5)
    L.bulk("visit", ["visit_id", "patient_id", "entry_type"] + ev.cols, gen_visits())
    L.bulk("visit_doctor_assignment", ["visit_id", "doctor_staff_id", "assignment_role"] + ea.cols,
           ((i, rng.randint(2, 4), "attending") + ea.vals(vtimes[i], 5) for i in range(1, n["visits"] + 1)))
    def gen_hist():
        full = ["Arrived", "Waiting", "With Doctor", "Discharged"]
        for i in range(1, n["visits"] + 1):
            k = 4 if i > TODAY_VISITS else rng.randint(2, 3)      # today's visits are still in progress
            for s in range(k):
                yield (i, s + 1, full[s]) + eh.vals(vtimes[i] + timedelta(minutes=12 * s), 5)
    L.bulk("visit_status_history", ["visit_id", "status_seq", "status"] + eh.cols, gen_hist())
    L.bulk("bill", ["bill_id", "visit_id", "bill_number"] + eb.cols,
           ((i, i, f"B-{i:07d}") + eb.vals(vtimes[i] + timedelta(minutes=35), 5) for i in range(1, n["visits"] + 1)))
    def gen_lines():
        for i in range(1, n["visits"] + 1):
            t = vtimes[i] + timedelta(minutes=35)
            yield (i, 1, "price_list_item", "General consultation", 1, 300, "price_list", 0, 0) + el.vals(t, 5)
            if rng.random() < .7:
                b, _, mrp = batches[rng.randrange(len(batches))]
                yield (i, 2, "medicine", "Medicine", rng.randint(1, 15), mrp, "batch_mrp", 0, 0) + el.vals(t, 8, "PHARMACY")
    L.bulk("bill_line", ["bill_id", "line_no", "line_type", "description_snapshot", "quantity", "unit_price_snapshot", "price_source",
                         "is_manual_price", "is_price_deviation"] + el.cols, gen_lines())
    def gen_pay():
        k = 0
        for i in range(1, n["visits"] + 1):
            if rng.random() < .92:
                k += 1
                yield (i, "payment", rng.choice([300, 450, 600, 820, 1250]), rng.choice(["cash", "upi", "upi", "card"]), f"R-{k:07d}") + ep.vals(vtimes[i] + timedelta(minutes=40), 9, "ACCOUNTANT")
    L.bulk("payment", ["bill_id", "payment_kind", "amount", "method", "receipt_number"] + ep.cols, gen_pay())

    # ---------------- pharmacy sales (OTC) -> dispensing + stock_movement(dispensed)
    ncs = max(1000, n["visits"] // 4)
    ecc, ecs, ed, em = E("counter_customer"), E("counter_sale"), E("dispensing"), E("stock_movement")
    L.bulk("counter_customer", ["counter_customer_id", "phone", "display_name"], ((i, fk.phone(rng), f"Buyer {i}") for i in range(1, 5001)))
    otc_meds = [i for i in range(1, len(meds) + 1) if i % 3 != 0]
    def gen_sales():
        for i in range(1, ncs + 1):
            yield (i, rng.randint(1, 5000), f"CS-{i:07d}") + ecs.vals(rand_time(), 8, "PHARMACY")
    L.bulk("counter_sale", ["counter_sale_id", "counter_customer_id", "sale_number"] + ecs.cols, gen_sales())
    disp_rows, mov_rows = [], []
    def gen_disp():
        for i in range(1, ncs * 2 + 1):
            m = rng.choice(otc_meds)
            t = rand_time()
            b = batches[(m - 1) * 2 + rng.randint(0, 1)]
            disp_rows.append((i, m, b, t))
            yield (i, "otc", m, (i + 1) // 2) + ed.vals(t, 8, "PHARMACY")
    L.bulk("dispensing", ["dispensing_id", "source_type", "medicine_id", "counter_sale_id"] + ed.cols, gen_disp())
    L.bulk("stock_movement", ["stock_batch_id", "movement_type", "quantity_delta", "unit_price_snapshot", "dispensing_id"] + em.cols,
           ((b[0], "dispensed", -rng.randint(1, 10), b[2], d) + em.vals(t, 8, "PHARMACY") for d, m, b, t in disp_rows))

    # ---------------- alerts, audit, family links
    eal, eau, efl = E("alert_event"), E("audit_log"), E("family_link")
    def gen_alerts():
        for i in range(n["alerts"]):
            t = (midnight + timedelta(hours=rng.randint(6, 11), minutes=rng.randint(0, 59))) if i < 12 else rand_time(90)
            red = rng.random() < .3
            yield ("daily_summary_item", rng.choice(["DISCOUNT_GIVEN", "MANUAL_PRICE", "OTC_PATTERN", "STAY_OVER_LIMIT", "REFUND_LARGE"]),
                   "red" if red else "amber", "bill", rng.randint(1, n["visits"]), "bench alert") + eal.vals(t, 11, "SYSTEM")
    L.bulk("alert_event", ["alert_class", "alert_type", "severity", "source_entity_type", "source_entity_id", "summary"] + eal.cols, gen_alerts())
    probes["audit_user"] = 5
    def gen_audit():
        for i in range(n["audit"]):
            sid = rng.randint(1, 9)
            pid = rng.randint(1, n["patients"])
            yield ("UPDATE", "patient", pid, json.dumps({"phone": "old"}), json.dumps({"phone": "new"}), "demographic correction") + eau.vals(rand_time(), sid, "RECEPTIONIST")
    L.bulk("audit_log", ["action", "entity_type", "entity_id", "old_values", "new_values", "reason"] + eau.cols, gen_audit())
    tokens = []
    def gen_links():
        for i in range(1, n["links"] + 1):
            tok = hashlib.sha256(f"tok{i}".encode()).hexdigest()
            yield (i, rng.randint(1, n["visits"]), tok, "date_of_birth", now + timedelta(days=30), "bench") + efl.vals(rand_time(30), 5)
    L.bulk("family_link", ["family_link_id", "visit_id", "token_hash", "second_factor_kind", "expires_at", "consent_note"] + efl.cols, gen_links())

    L.cur.execute("SET FOREIGN_KEY_CHECKS=1, UNIQUE_CHECKS=1")
    print("Running ANALYZE TABLE (fresh optimizer statistics) ...")
    for t in ["patient", "patient_phone", "visit", "visit_status_history", "stock_batch", "stock_movement", "medicine", "payment",
              "bill", "bill_line", "alert_event", "audit_log", "family_link", "dispensing"]:
        L.cur.execute(f"ANALYZE TABLE `{t}`")
        L.cur.fetchall()
    conn.commit()
    (ROOT / "tools" / ".bench_info.json").write_text(json.dumps(dict(scale=a.scale, rows=n, probes=probes), indent=2))
    L.print_report()
    print(f"\nDone in {(datetime.now() - t0).total_seconds() / 60:.1f} min. Row counts:", dict(L.counts))


if __name__ == "__main__":
    main()
