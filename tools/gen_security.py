#!/usr/bin/env python3
"""Generates db/security/01_roles_and_grants.sql, docs/demos/security_matrix.md and db/tests/forbidden_actions.sh
from (a) the '-- @api roles=...' comment of every procedure and (b) VIEW_ACCESS below.
Rules: no role gets any table privilege; writes happen only through EXECUTE on procedures; reads only through views.
Procedures with roles=* are internal (called by other routines, which run with definer rights) and are NOT granted.
Run from repo root after changing a procedure's roles:  python tools/gen_security.py"""
import os, re, glob
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ROLES = {'OWNER': 'owner', 'DOCTOR': 'doctor', 'RECEPTIONIST': 'receptionist', 'NURSE': 'nurse', 'PHARMACY': 'pharmacy', 'ACCOUNTANT': 'accountant'}
ACCOUNTS = dict(ROLES, login='login', family='family')          # api role token -> account suffix
PASS = lambda s: f'Dev#{s.capitalize()}2026'                      # DEV passwords: change before any real use (docs/pooling/POOLING_GUIDE)

# views each role may SELECT. '*' = every role. Owner also gets all exception/summary views.
ALL_ROLES = list(ROLES)
VIEW_ACCESS = {
 'v_patient_search': ['RECEPTIONIST', 'DOCTOR', 'NURSE', 'PHARMACY', 'ACCOUNTANT', 'OWNER'],
 'v_appointment_current': ['RECEPTIONIST', 'DOCTOR', 'OWNER'],
 'v_free_slots': ['RECEPTIONIST', 'DOCTOR'],
 'v_visit_current': ['RECEPTIONIST', 'DOCTOR', 'NURSE', 'PHARMACY', 'ACCOUNTANT', 'OWNER'],
 'v_queue_today': ALL_ROLES,
 'v_opd_visit': ['DOCTOR', 'OWNER'],
 'v_stay_status': ['DOCTOR', 'NURSE', 'OWNER'],
 'v_stay_over_limit': ['DOCTOR', 'NURSE', 'OWNER'],
 'v_patient_allergy_current': ['DOCTOR', 'NURSE', 'PHARMACY'],
 'v_prescription_current': ['DOCTOR', 'PHARMACY', 'NURSE'],
 'v_prescription_print': ['DOCTOR', 'PHARMACY'],
 'v_catalog_review_pending': ['OWNER', 'PHARMACY'],
 'v_lab_order_current': ['DOCTOR', 'NURSE', 'RECEPTIONIST', 'OWNER'],
 'v_report_unreviewed': ['DOCTOR', 'NURSE', 'OWNER'],
 'v_nurse_tasks': ['NURSE', 'DOCTOR'],
 'v_stock_by_batch': ['OWNER', 'PHARMACY'],                      # contains cost price (G10)
 'v_stock_batch_nocost': ['DOCTOR', 'NURSE', 'RECEPTIONIST', 'ACCOUNTANT'],
 'v_stock_by_medicine': ['DOCTOR', 'NURSE', 'PHARMACY', 'OWNER'],
 'v_expiring_soon': ['OWNER', 'PHARMACY'],
 'v_fefo_batches': ['PHARMACY', 'OWNER'],
 'v_restock_demand': ['OWNER', 'PHARMACY'],
 'v_dispensing_visit': ['PHARMACY', 'NURSE', 'OWNER'],
 'v_dispensing_custody': ['PHARMACY', 'NURSE', 'OWNER'],
 'v_dispense_reconciliation': ['PHARMACY', 'NURSE', 'DOCTOR', 'OWNER', 'ACCOUNTANT'],
 'v_unacknowledged_handoffs': ['PHARMACY', 'NURSE', 'OWNER'],
 'v_visit_medicine_count': ['PHARMACY', 'NURSE', 'DOCTOR', 'OWNER'],
 'v_counter_sale_summary': ['PHARMACY', 'OWNER', 'ACCOUNTANT'],
 'v_bill_summary': ['ACCOUNTANT', 'RECEPTIONIST', 'OWNER', 'DOCTOR'],
 'v_bill_lines': ['ACCOUNTANT', 'RECEPTIONIST', 'OWNER', 'DOCTOR'],
 'v_discharge_checklist': ['DOCTOR', 'RECEPTIONIST', 'ACCOUNTANT', 'NURSE', 'OWNER'],
 'v_due_list': ['ACCOUNTANT', 'RECEPTIONIST', 'OWNER'],
 'v_daily_collection': ['ACCOUNTANT', 'OWNER'],
 'v_approval_inbox': ['OWNER', 'DOCTOR', 'ACCOUNTANT', 'RECEPTIONIST', 'PHARMACY', 'NURSE'],
 'v_alerts_open': ['OWNER'],
 'v_owner_checks': ['OWNER'], 'v_owner_summary': ['OWNER'],
 'v_ex_ordered_not_billed': ['OWNER'], 'v_ex_billed_no_report': ['OWNER'], 'v_ex_discounts_by_staff': ['OWNER'], 'v_ex_free_services': ['OWNER'],
 'v_ex_refunds': ['OWNER'], 'v_ex_backdated_entries': ['OWNER'], 'v_ex_edits_after_payment': ['OWNER'], 'v_ex_shared_logins': ['OWNER'],
 'v_ex_otc_patterns': ['OWNER'], 'v_ex_stock_adjustments': ['OWNER'], 'v_ex_manual_prices': ['OWNER'], 'v_ex_leftover_returns': ['OWNER'],
 'v_ex_weak_rx_flagged_drugs': ['OWNER'],
 'v_patient_alias': [],  # internal only
}
FAMILY_VIEWS = ['v_family_visit', 'v_family_prescription', 'v_family_reports', 'v_family_bill', 'v_family_bill_total', 'v_family_receipts', 'v_family_medicine_count', 'v_family_notes']
INTERNAL_VIEWS = ['v_family_link_valid']

# a few extra read grants the app needs on single-purpose views are covered above; everything else is intentionally NOT readable.


def collect():
    procs = {}
    files = glob.glob(os.path.join(ROOT, 'db/logic/0[2-9]*.sql')) + [os.path.join(ROOT, 'tools/sql_src/01_foundation_head.sql')]
    views = set()
    for f in files:
        t = open(f, encoding='utf-8').read()
        for m in re.finditer(r'--\s*@api\s+roles=(\S*)\s+rules=(\S*)\s*\|\s*([^\n]*)\n\s*CREATE\s+PROCEDURE\s+`?(\w+)`?', t):
            procs[m.group(4)] = (m.group(1).split(','), m.group(2), m.group(3).strip())
        views |= set(re.findall(r'CREATE OR REPLACE VIEW (\w+)', t))
    return procs, views


def main():
    procs, views = collect()
    known = set(VIEW_ACCESS) | set(FAMILY_VIEWS) | set(INTERNAL_VIEWS)
    missing = sorted(views - known)
    if missing:
        raise SystemExit('views without an access decision in gen_security.VIEW_ACCESS: %s' % missing)
    extra = sorted(known - views)
    if extra:
        raise SystemExit('VIEW_ACCESS names unknown views: %s' % extra)
    out = ['-- GENERATED by tools/gen_security.py from the "@api roles=" comments - do not edit by hand.',
           '-- Principle: NO table privileges for anybody. Writes = EXECUTE on procedures. Reads = SELECT on views. DEV passwords below: change them (ALTER USER) outside local demos.',
           'USE clinic_db;', '']
    for suffix in sorted(set(ACCOUNTS.values())):
        out += [f"CREATE ROLE IF NOT EXISTS 'r_{suffix}';",
                f"CREATE USER IF NOT EXISTS 'clinic_{suffix}'@'%' IDENTIFIED BY '{PASS(suffix)}';",
                f"GRANT 'r_{suffix}' TO 'clinic_{suffix}'@'%';", f"SET DEFAULT ROLE 'r_{suffix}' TO 'clinic_{suffix}'@'%';"]
    out.append('')
    matrix = {r: [] for r in ACCOUNTS}
    for name, (roles, rules, doc) in sorted(procs.items()):
        for r in roles:
            if r in ACCOUNTS:
                out.append(f"GRANT EXECUTE ON PROCEDURE clinic_db.{name} TO 'r_{ACCOUNTS[r]}';")
                matrix[r].append(name)
    out.append('')
    for v, roles in sorted(VIEW_ACCESS.items()):
        for r in roles:
            out.append(f"GRANT SELECT ON clinic_db.{v} TO 'r_{ROLES[r]}';")
    for v in FAMILY_VIEWS:
        out.append(f"GRANT SELECT ON clinic_db.{v} TO 'r_family';")
    out.append('')
    os.makedirs(os.path.join(ROOT, 'db/security'), exist_ok=True)
    open(os.path.join(ROOT, 'db/security/01_roles_and_grants.sql'), 'w').write('\n'.join(out) + '\n')

    md = ['# Security matrix (generated by tools/gen_security.py)', '',
          'Every MySQL account has **no table privileges**. A role can only (a) EXECUTE the procedures listed and (b) SELECT the views listed. '
          'Inside each procedure the hat is checked again (`int_require_role`) and triggers enforce the rules a third time.', '',
          '## Procedures (write actions) per role', '']
    for r in list(ROLES) + ['login', 'family']:
        md += [f'### {r}  (account `clinic_{ACCOUNTS[r]}`)', ''] + [f'- `{p}`: {procs[p][2][:110]}' for p in matrix[r]] + ['']
    md += ['## Views (read) per role', '', '| view | roles |', '|---|---|']
    md += [f"| `{v}` | {', '.join(roles) or 'internal only'} |" for v, roles in sorted(VIEW_ACCESS.items())]
    md += [f"| `{v}` | family link account only |" for v in FAMILY_VIEWS]
    md += ['', '## Forbidden-action matrix (each must be refused; run `bash db/tests/forbidden_actions.sh`)', '',
           '| account | attempt | why it must fail |', '|---|---|---|']
    for acct, stmt, why, _ in FORBIDDEN:
        md.append(f'| clinic_{acct} | `{stmt}` | {why} |')
    open(os.path.join(ROOT, 'docs/demos/security_matrix.md'), 'w').write('\n'.join(md) + '\n')

    sh = ['#!/usr/bin/env bash',
          '# GENERATED by tools/gen_security.py. Logs in as each restricted MySQL account and tries forbidden actions. PASS = the server refused.',
          '# Usage: bash db/tests/forbidden_actions.sh      (needs the containers up and db/security loaded; DEV passwords)',
          'cd "$(dirname "$0")/../.."', 'pass=0; fail=0',
          'try() { # account password sql label',
          '  out=$(docker compose exec -T mysql mysql -u"clinic_$1" -p"$2" clinic_db -e "$3" 2>&1)',
          '  if echo "$out" | grep -qiE "denied|ERROR"; then echo "PASS  [$1] $4"; pass=$((pass+1)); else echo "FAIL  [$1] $4 (it worked!)"; fail=$((fail+1)); fi; }']
    for acct, stmt, why, label in FORBIDDEN:
        sh.append(f"try {acct} '{PASS(acct)}' \"SET @app_user_id=1,@app_role='SYSTEM'; {stmt}\" \"{label}\"")
    sh += ['echo "$pass refused as expected, $fail NOT refused"; [ "$fail" -eq 0 ]']
    open(os.path.join(ROOT, 'db/tests/forbidden_actions.sh'), 'w').write('\n'.join(sh) + '\n')
    print(f'procedures granted: {sum(len(v) for v in matrix.values())}, views: {len(VIEW_ACCESS) + len(FAMILY_VIEWS)}')


# (account, statement, why, label)
FORBIDDEN = [
 ('receptionist', 'SELECT * FROM diagnosis LIMIT 1;', 'no table privileges: reception cannot read diagnoses', 'reception reads diagnosis'),
 ('receptionist', 'SELECT * FROM stock_batch LIMIT 1;', 'cost price hidden from reception', 'reception reads stock cost'),
 ('receptionist', 'CALL sp_record_payment(1, 10, \'cash\', @a, @b);', 'payments are the accountant\'s', 'reception records a payment'),
 ('receptionist', 'CALL sp_prescribe_item(1, 1, NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL, @a, @b);', 'prescribing is the doctor\'s', 'reception prescribes'),
 ('nurse', 'CALL sp_prescribe_item(1, 1, NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL, @a, @b);', 'nurse cannot edit prescriptions', 'nurse edits a prescription'),
 ('nurse', 'CALL sp_record_payment(1, 10, \'cash\', @a, @b);', 'nurse cannot touch billing', 'nurse records a payment'),
 ('nurse', 'SELECT cost_price FROM v_stock_by_batch LIMIT 1;', 'cost price only for owner and pharmacy', 'nurse reads cost price'),
 ('nurse', 'UPDATE stock_movement SET quantity_delta = 99 LIMIT 1;', 'no direct table writes', 'nurse updates the stock ledger'),
 ('doctor', 'CALL sp_record_payment(1, 10, \'cash\', @a, @b);', 'doctor cannot record payments', 'doctor records a payment'),
 ('doctor', 'CALL sp_receive_stock(1, NULL, \'X\', \'2099-01-01\', 1, 1, 2, @a);', 'doctor cannot change stock', 'doctor receives stock'),
 ('doctor', 'CALL sp_administer_dose(1, \'given\', NULL, 1, NULL, NULL, NULL, NULL, NULL, NULL, @a);', 'doses are recorded by nurses only', 'doctor records a dose'),
 ('pharmacy', 'CALL sp_clinical_signoff(1, \'x\', @a);', 'pharmacy cannot sign off clinically', 'pharmacy signs off a visit'),
 ('pharmacy', 'CALL sp_record_payment(1, 10, \'cash\', @a, @b);', 'pharmacy records counter-sale payments only', 'pharmacy records a bill payment'),
 ('pharmacy', 'DELETE FROM dispensing LIMIT 1;', 'dispensing records can never be deleted', 'pharmacy deletes a dispensing record'),
 ('accountant', 'CALL sp_prescribe_item(1, 1, NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL, @a, @b);', 'accountant has no clinical rights', 'accountant prescribes'),
 ('accountant', 'SELECT * FROM clinical_note LIMIT 1;', 'no table privileges', 'accountant reads clinical notes'),
 ('owner', 'UPDATE audit_log SET reason = \'x\' LIMIT 1;', 'audit log is insert-only, even for the owner', 'owner edits the audit log'),
 ('owner', 'DELETE FROM payment LIMIT 1;', 'payments are append-only', 'owner deletes a payment'),
 ('owner', 'CALL sp_administer_dose(1, \'given\', NULL, 1, NULL, NULL, NULL, NULL, NULL, NULL, @a);', 'owner cannot edit doses given', 'owner records a dose'),
 ('family', 'SELECT * FROM patient LIMIT 1;', 'family link account reads only family views', 'family reads patient table'),
 ('family', 'CALL sp_record_payment(1, 10, \'cash\', @a, @b);', 'read-only: no write procedures', 'family records a payment'),
]

if __name__ == '__main__':
    main()
