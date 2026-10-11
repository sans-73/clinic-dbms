#!/usr/bin/env python3
"""Generates db/tests/rule_tests.sql (one PASS/FAIL line per enforceable rule).  Add tests by appending to TESTS.
Kinds: ('error', pattern)  action must raise an error whose text contains pattern
       ('ok',)             action must succeed
       ('assert', expr)    run action, then expr (SQL boolean using procedure variables) must be true
'step' entries just run SQL (setup) without reporting."""
import os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CTX = lambda uid, role: f"SET @app_user_id = {uid}, @app_role = '{role}';"
S = []   # ('step', sql) | (rule, name, action_sql, kind...)
def step(sql): S.append(('step', sql))
def test(rule, name, action, *kind): S.append((rule, name, action, kind))

# ---------------- setup (unique suffix per run so the tests can be re-run on the same database)
step("SET v_sfx = SUBSTRING(MD5(UUID()), 1, 8);")
step(CTX(1, 'SYSTEM'))
step("CALL next_code('staff', v_code);")
step("INSERT INTO staff (display_code, principal_kind, given_name, family_name, login_subject) VALUES (v_code, 'human', 'Test', 'Owner', CONCAT('t_own_', v_sfx));")
step("SET v_owner = LAST_INSERT_ID();")
step("INSERT INTO staff_credential (staff_id, password_hash) VALUES (v_owner, 'x');")
step("INSERT INTO staff_role (staff_id, role_id, granted_by) SELECT v_owner, role_id, 1 FROM `role` WHERE role_code = 'OWNER';")
step(CTX('v_owner', 'OWNER'))
for var, role, nm in [('v_doc', 'DOCTOR', 'doc'), ('v_rec', 'RECEPTIONIST', 'rec'), ('v_nur', 'NURSE', 'nur'), ('v_pha', 'PHARMACY', 'pha')]:
    step(f"CALL create_staff('Test', '{nm.title()}', '9100000001', CONCAT('t_{nm}_', v_sfx), 'x', '{role}', 'REG1', {var});")
step(CTX(1, 'SYSTEM'))
step("INSERT INTO medicine (brand_name, generic_display_name, dosage_form, stock_unit) VALUES (CONCAT('TestMed', v_sfx), 'testgeneric', 'tablet', 'tablet');")
step("SET v_med = LAST_INSERT_ID();")
step("INSERT INTO stock_batch (medicine_id, batch_code, expiry_date, cost_price, mrp) VALUES (v_med, CONCAT('B', v_sfx), CURRENT_DATE + INTERVAL 1 YEAR, 1.00, 2.00);")
step("SET v_batch = LAST_INSERT_ID();")

# ---------------- foundation
step("SET @app_user_id = NULL, @app_role = NULL;")
test('G5', 'event insert without user context is refused',
     "INSERT INTO alert_event (alert_class, alert_type, severity, summary) VALUES ('daily_summary_item', 'T', 'amber', 'x');", 'error', 'Not logged in')
step(CTX('v_rec', 'RECEPTIONIST'))
test('G5/R2.1', 'a user cannot claim a role he does not hold',
     "SET @app_role = 'OWNER'; INSERT INTO alert_event (alert_class, alert_type, severity, summary) VALUES ('daily_summary_item', 'T', 'amber', 'x');", 'error', 'does not hold that role')
step(CTX('v_rec', 'RECEPTIONIST'))
test('G5', 'event insert fills recorded_by and role from the session',
     "CALL raise_alert('TEST_ALERT', 'SUMMARY', 'test', v_rec, CONCAT('t', v_sfx));", 'assert',
     "(SELECT recorded_by FROM alert_event WHERE alert_type='TEST_ALERT' AND source_entity_id = CAST(v_rec AS CHAR) ORDER BY alert_event_id DESC LIMIT 1) = v_rec AND (SELECT recorded_by_role FROM alert_event WHERE alert_type='TEST_ALERT' AND source_entity_id = CAST(v_rec AS CHAR) ORDER BY alert_event_id DESC LIMIT 1) = 'RECEPTIONIST'")
test('G2', 'UPDATE of an event row is blocked', "UPDATE alert_event SET summary = 'hacked' WHERE alert_type = 'TEST_ALERT';", 'error', 'cannot be changed')
test('G2', 'DELETE of an event row is blocked', "DELETE FROM alert_event WHERE alert_type = 'TEST_ALERT';", 'error', 'cannot be deleted')
step(CTX('v_owner', 'OWNER'))
test('R10.6', 'audit_log cannot be updated, even by the owner', "UPDATE audit_log SET reason = 'x' LIMIT 1;", 'error', 'cannot be changed')
test('R10.6', 'audit_log cannot be deleted, even by the owner', "DELETE FROM audit_log LIMIT 1;", 'error', 'cannot be deleted')
step(CTX('v_rec', 'RECEPTIONIST'))
test('G7', 'old entry without backfill flag is refused',
     "INSERT INTO alert_event (occurred_at, alert_class, alert_type, severity, summary) VALUES (NOW(3) - INTERVAL 3 DAY, 'daily_summary_item', 'T', 'amber', 'old');", 'error', 'backfilled')
test('G7', 'old entry with backfill flag is accepted',
     "INSERT INTO alert_event (occurred_at, is_backfilled, alert_class, alert_type, severity, summary) VALUES (NOW(3) - INTERVAL 3 DAY, 1, 'daily_summary_item', 'T', 'amber', 'old');", 'ok')
test('G9', 'only the owner can change clinic settings', "UPDATE clinic_settings SET setting_value = '99' WHERE setting_key = 'stay_limit_hours';", 'error', 'Only the owner')
test('R2.2', 'receptionist cannot create staff', "CALL create_staff('A','B','9100000009','x_x','x','NURSE',NULL,v_tmp);", 'error', 'not allowed')
# maker-checker
step(CTX('v_pha', 'PHARMACY'))
step("CALL request_approval_ex('stock_adjustment', NULL, NULL, v_batch, NULL, NULL, NULL, 2, '-2', 'count mismatch', v_appr);")
test('R10.3', 'approval request raises an immediate approval_pending alert',
     "SELECT 1 INTO v_tmp;", 'assert', "EXISTS (SELECT 1 FROM alert_event WHERE alert_class = 'approval_pending' AND source_entity_id = CAST(v_appr AS CHAR))")
test('G6', 'nobody can approve his own request', "CALL decide_approval(v_appr, 'APPROVED', 'ok');", 'error', 'own request')
step(CTX('v_doc', 'DOCTOR'))
test('G6', 'a doctor without delegation cannot approve', "CALL decide_approval(v_appr, 'APPROVED', 'ok');", 'error', 'not allowed to approve')
step(CTX('v_owner', 'OWNER'))
test('G6', 'owner approves a different person\'s request', "CALL decide_approval(v_appr, 'APPROVED', 'ok');", 'assert', "approval_is_valid(v_appr, 'stock_adjustment,stock_writeoff') = 1")
test('G6', 'a request can be decided only once', "CALL decide_approval(v_appr, 'REJECTED', 'again');", 'error', 'already decided')
step(CTX('v_pha', 'PHARMACY'))
test('R5.2', 'stock cannot go below zero (ledger trigger)',
     "INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, reason, approval_request_id) VALUES (v_batch, 'adjustment', -2, 'count mismatch', v_appr);", 'error', 'Not enough stock')
test('R5.1', 'receiving stock works and stock is derived by SUM',
     "INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta) VALUES (v_batch, 'received', 10);", 'assert',
     "(SELECT SUM(quantity_delta) FROM stock_movement WHERE stock_batch_id = v_batch) = 10")
test('R5.3', 'approved adjustment is accepted with its approval',
     "INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, reason, approval_request_id) VALUES (v_batch, 'adjustment', -2, 'count mismatch', v_appr);", 'assert',
     "(SELECT SUM(quantity_delta) FROM stock_movement WHERE stock_batch_id = v_batch) = 8")
test('R5.3', 'the same approval cannot be used twice',
     "INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, reason, approval_request_id) VALUES (v_batch, 'adjustment', -1, 'again', v_appr);", 'error', 'Duplicate')
test('R5.3', 'adjustment without any approval is refused',
     "INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, reason) VALUES (v_batch, 'adjustment', -1, 'sneaky');", 'error', 'approved adjustment')
# ---------------- patients
test('G12', 'levenshtein(kitten, sitting) = 3', "SELECT 1 INTO v_tmp;", 'assert', "levenshtein('kitten', 'sitting') = 3")
step(CTX('v_rec', 'RECEPTIONIST'))
step("SET v_phone = CONCAT('98', LPAD(FLOOR(RAND() * 100000000), 8, '0'));")
step("SET v_dob = DATE('1950-01-01') + INTERVAL FLOOR(RAND() * 15000) DAY;")
step("CALL sp_register_patient(CONCAT('Zq', v_sfx), NULL, 'Sharma', 'male', v_dob, v_phone, NULL, 'Main Road', 'Indore', '452001', 0, v_p1, v_code);")
test('R1.4', 'duplicate registration is refused until a person confirms',
     "CALL sp_register_patient(CONCAT('Zq', v_sfx), NULL, 'Sharma', 'male', v_dob, v_phone, NULL, 'Main Road', 'Indore', '452001', 0, v_p2, v_code);", 'error', 'Possible duplicate')
test('R1.4/G12', 'confirmed duplicate is registered', "CALL sp_register_patient(CONCAT('Zq', v_sfx), NULL, 'Sharma', 'male', v_dob, v_phone, NULL, 'Main Road', 'Indore', '452001', 1, v_p2, v_code);", 'assert', "v_p2 IS NOT NULL AND v_p2 <> v_p1")
test('R1.4', 'tier 2 (name+DOB) catches a misspelled name', "SELECT 1 INTO v_tmp;", 'assert',
     "patient_match_tier(v_p1, CONCAT('zq', v_sfx, 'x sarma'), v_dob, NULL, NULL, NULL) IN (1,2)")
test('R1.6', 'demographic correction needs a reason', "CALL sp_update_demographics(v_p1, 'Zq', NULL, 'Sharma', 'male', v_dob, 'x', 'Indore', '452001', '');", 'error', 'reason is required')
test('R1.6', 'demographic correction is audited with old and new values',
     "CALL sp_update_demographics(v_p1, CONCAT('Zq', v_sfx), NULL, 'Sharma', 'male', v_dob, 'New Road', 'Indore', '452001', 'address corrected');", 'assert',
     "EXISTS (SELECT 1 FROM audit_log WHERE entity_type = 'patient' AND entity_id = CAST(v_p1 AS CHAR) AND action = 'update' AND reason = 'address corrected' AND old_values IS NOT NULL AND new_values IS NOT NULL)")
test('R1.5', 'emergency Unknown Patient gets a TMP- code',
     "CALL sp_register_unknown_patient('male', 40, v_p3, v_code);", 'assert', "v_code LIKE 'TMP-%'")
test('R1.5', 'merge redirects the source to the survivor (no event row rewritten)',
     "CALL sp_merge_patients(v_p2, v_p1, 'same person', 'T2', v_tmp);", 'assert', "canonical_patient(v_p2) = v_p1 AND patient_is_merged(v_p2) = 1")
test('R1.5', 'no new visit for a merged-away patient', "CALL sp_create_visit(v_p2, NULL, v_doc, 'walk_in', v_visit);", 'error', 'merged')
test('G11', 'repeated submit with the same request_uuid creates one set of rows',
     "SET @request_uuid = UUID(); SET @req_seq = 0; CALL sp_register_unknown_patient('female', 30, v_tmp, v_code); SET @req_seq = 0; CALL sp_register_unknown_patient('female', 30, v_tmp, v_code);", 'error', 'Duplicate')
step("SET @request_uuid = NULL; SET @req_seq = NULL;")
# ---------------- scheduling and visits
step(CTX('v_owner', 'OWNER'))
step("CALL sp_add_doctor_schedule(v_doc, WEEKDAY(CURRENT_DATE + INTERVAL 1 DAY), '09:00:00', '12:00:00', 15, CURRENT_DATE, v_sched);")
step("SELECT MIN(appointment_slot_id) INTO v_slot FROM appointment_slot WHERE doctor_staff_id = v_doc AND slot_start > NOW() AND doctor_schedule_id = v_sched;")
test('R3.6', 'slots were generated for the schedule', "SELECT 1 INTO v_tmp;", 'assert', "v_slot IS NOT NULL")
step(CTX('v_rec', 'RECEPTIONIST'))
step("CALL sp_book_appointment(v_p1, v_slot, 'reception', v_appt);")
test('R3.6', 'double booking the same doctor slot is refused', "CALL sp_book_appointment(v_p3, v_slot, 'reception', v_tmp);", 'error', 'Slot already booked')
test('R3.2', 'cancel needs a reason', "CALL sp_set_appointment_status(v_appt, 'cancelled', '');", 'error', 'reason is required')
test('R3.2', 'booked -> cancelled is allowed', "CALL sp_set_appointment_status(v_appt, 'cancelled', 'patient called');", 'assert',
     "(SELECT current_status FROM v_appointment_current WHERE appointment_id = v_appt) = 'cancelled'")
test('R3.6', 'a cancelled appointment frees the slot', "CALL sp_book_appointment(v_p3, v_slot, 'reception', v_appt2);", 'assert', "v_appt2 IS NOT NULL")
test('R3.2', 'invalid status jump (booked -> completed) is refused', "CALL sp_set_appointment_status(v_appt2, 'completed', NULL);", 'error', 'not allowed')
step(CTX('v_doc', 'DOCTOR'))
test('R3.2', 'receptionist-only status cannot be set by a doctor', "CALL sp_set_appointment_status(v_appt2, 'checked_in', NULL);", 'error', 'not allowed')
step(CTX('v_doc', 'DOCTOR'))
step("CALL sp_request_schedule_change(v_sched, 'withdraw_session', NULL, NULL, NULL, NULL, CURRENT_DATE + INTERVAL 1 DAY, 'leave', v_req);")
test('R3.7', 'a doctor cannot apply his own schedule change', "CALL sp_apply_schedule_change(v_req, 'applied', 'ok', v_tmp);", 'error', 'not allowed')
step(CTX('v_rec', 'RECEPTIONIST'))
test('R3.7', 'applying creates a new version; the old slot is no longer bookable',
     "CALL sp_apply_schedule_change(v_req, 'applied', 'ok', v_newsched);", 'assert', "slot_is_effective(v_slot) = 0 AND v_newsched IS NOT NULL")
test('R3.6', 'a slot of a superseded schedule cannot be booked', "CALL sp_book_appointment(v_p1, v_slot, 'reception', v_tmp);", 'error', 'Slot already booked')
step("CALL sp_create_visit(v_p1, NULL, v_doc, 'walk_in', v_visit);")
test('R3.3', 'a new visit starts as arrived with the attending doctor',
     "SELECT 1 INTO v_tmp;", 'assert', "(SELECT current_status FROM v_visit_current WHERE visit_id = v_visit) = 'arrived' AND (SELECT attending_doctor_id FROM v_visit_current WHERE visit_id = v_visit) = v_doc")
test('R3.3', 'discharged status cannot be set by hand', "CALL sp_add_visit_status(v_visit, 'discharged', NULL, NULL);", 'error', 'separate step')
step(CTX('v_nur', 'NURSE'))
test('R3.5', 'vitals out of range are refused', "CALL sp_record_vitals(v_visit, NULL, 400, 80, NULL, NULL, NULL, NULL, NULL);", 'error', 'out of range')
test('R3.5', 'vitals are stored as typed rows (BP = 2 rows)', "CALL sp_record_vitals(v_visit, NULL, 120, 80, 72, 37.0, 98, NULL, NULL);", 'assert',
     "(SELECT COUNT(*) FROM vitals_observation WHERE visit_id = v_visit) = 5 AND (SELECT COUNT(DISTINCT observation_set_uuid) FROM vitals_observation WHERE visit_id = v_visit) = 1")
step("CALL sp_start_short_stay(v_visit, 'observation after injection', v_ep);")
test('R3.1', 'a visit gets at most one short-stay episode', "CALL sp_start_short_stay(v_visit, 'again', v_tmp);", 'error', 'already has')
test('R3.4', 'stay limit comes from clinic settings', "SELECT 1 INTO v_tmp;", 'assert',
     "(SELECT allowed_until FROM v_stay_status WHERE short_stay_episode_id = v_ep) = (SELECT started_at FROM v_stay_status WHERE short_stay_episode_id = v_ep) + INTERVAL setting_int('stay_limit_hours') HOUR")


# =====================================================================================================
# batch 3-6 tests: prescriptions, orders, pharmacy, billing, discharge, labs, family links, alerts
# =====================================================================================================
step(CTX('v_owner', 'OWNER'))
step("CALL create_staff('Test', 'Acc', '9100000002', CONCAT('t_acc_', v_sfx), 'x', 'ACCOUNTANT', NULL, v_acc);")
step(CTX('v_pha', 'PHARMACY'))
step("CALL sp_add_ingredient(CONCAT('ing', v_sfx), v_ing);")
step("CALL sp_add_medicine(CONCAT('AllergyMed', v_sfx), 'allergygeneric', 500, 'mg', 'tablet', 'tablet', 'none', '1 tab', '1-0-1', 'after food', 5, 1, v_med2);")
step("CALL sp_link_medicine_ingredient(v_med2, v_ing, '500 mg');")
step("CALL sp_add_medicine(CONCAT('PlainMed', v_sfx), 'plaingeneric', 250, 'mg', 'tablet', 'tablet', 'none', '1 tab', '1-0-1', 'after food', 5, 1, v_med3);")
step("CALL sp_add_medicine(CONCAT('SchedH', v_sfx), 'hgeneric', 10, 'mg', 'tablet', 'tablet', 'H', '1 tab', '0-0-1', NULL, 3, 0, v_medH);")
step("CALL sp_receive_stock(v_med2, NULL, CONCAT('P', v_sfx), CURRENT_DATE + INTERVAL 1 YEAR, 20, 1.00, 2.50, v_b2);")
step("CALL sp_receive_stock(v_med3, NULL, CONCAT('Q', v_sfx), CURRENT_DATE + INTERVAL 1 YEAR, 20, 1.00, 3.00, v_b3);")
step("CALL sp_receive_stock(v_medH, NULL, CONCAT('H', v_sfx), CURRENT_DATE + INTERVAL 1 YEAR, 20, 1.00, 5.00, v_bH);")
step(CTX(1, 'SYSTEM'))
step("INSERT INTO stock_batch (medicine_id, batch_code, expiry_date, cost_price, mrp) VALUES (v_med3, CONCAT('EXP', v_sfx), CURRENT_DATE - INTERVAL 1 DAY, 1.00, 3.00);")
step("SET v_bexp = LAST_INSERT_ID();")
step("INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta) VALUES (v_bexp, 'received', 5);")
test('R5.4', 'expired stock is not counted as sellable', "SELECT 1 INTO v_tmp;", 'assert',
     "(SELECT sellable_qty FROM v_stock_by_medicine WHERE medicine_id = v_med3) = 20 AND (SELECT total_qty FROM v_stock_by_medicine WHERE medicine_id = v_med3) = 25")
step(CTX('v_doc', 'DOCTOR'))
step("CALL sp_add_patient_allergy(v_p1, v_ing, NULL, 'rash', 'severe', v_al);")
step("CALL sp_create_prescription(v_visit, v_rx, v_ver);")
test('R4.1', 'one prescription root per visit', "CALL sp_create_prescription(v_visit, v_tmp, v_tmp2);", 'error', 'already has a prescription')
test('R4.4', 'catalog medicine that clashes with an allergy is refused',
     "CALL sp_prescribe_item(v_ver, v_med2, NULL, NULL, NULL, NULL, NULL, 10, 0, NULL, v_item, v_line);", 'error', 'Allergy warning')
test('R4.4/R10.2', 'allergy override with a reason is stored and raises an interrupt alert',
     "CALL sp_prescribe_item(v_ver, v_med2, NULL, NULL, NULL, NULL, NULL, 10, 0, 'benefit outweighs risk, patient informed', v_item, v_line);", 'assert',
     "EXISTS (SELECT 1 FROM alert_event WHERE alert_type = 'ALLERGY_OVERRIDE' AND alert_class = 'safety_interrupt' AND source_entity_id = CAST(v_item AS CHAR)) AND EXISTS (SELECT 1 FROM allergy_confirmation WHERE prescription_item_id = v_item AND was_overridden = 1)")
step("SET v_line_ov = v_line;")
test('R4.4', 'free-text medicine needs allergy confirmation', "CALL sp_prescribe_item(v_ver, NULL, CONCAT('Herbal', v_sfx), '1 spoon', '1-0-1', NULL, 5, NULL, 0, NULL, v_itemft, v_tmp);", 'error', 'Confirm the patient allergies')
test('R4.4/R4.5', 'confirmed free-text medicine is saved and queued for catalog review',
     "CALL sp_prescribe_item(v_ver, NULL, CONCAT('Herbal', v_sfx), '1 spoon', '1-0-1', NULL, 5, NULL, 1, NULL, v_itemft, v_tmp);", 'assert',
     "EXISTS (SELECT 1 FROM catalog_review_queue WHERE prescription_item_id = v_itemft) AND EXISTS (SELECT 1 FROM v_catalog_review_pending WHERE prescription_item_id = v_itemft)")
test('R4.3', 'catalog defaults (dose, schedule) are copied into the prescription and can be overridden later',
     "CALL sp_prescribe_item(v_ver, v_med3, NULL, NULL, NULL, NULL, NULL, 10, 0, NULL, v_item3, v_line3);", 'assert',
     "(SELECT chosen_dose_text FROM prescription_item WHERE prescription_item_id = v_item3) = '1 tab' AND (SELECT chosen_schedule FROM prescription_item WHERE prescription_item_id = v_item3) = '1-0-1'")
test('R4.2', 'in-stock medicine qualifies for the green state (sellable stock > 0)', "SELECT 1 INTO v_tmp;", 'assert',
     "(SELECT COUNT(*) FROM v_stock_by_medicine WHERE medicine_id = v_med3 AND sellable_qty > 0) = 1")
test('R4.1', 'revision creates version 2 and keeps version 1',
     "CALL sp_revise_prescription(v_rx, 'stop the allergic drug', CAST(v_line_ov AS CHAR), v_ver2);", 'assert',
     "(SELECT MAX(version_number) FROM prescription_version WHERE prescription_id = v_rx) = 2 AND (SELECT COUNT(*) FROM prescription_item WHERE prescription_version_id = v_ver) >= 3 AND (SELECT item_change FROM prescription_item WHERE prescription_version_id = v_ver2 AND prescription_line_id = v_line_ov) = 'discontinued'")
test('R4.1', 'only the latest version can be edited', "CALL sp_prescribe_item(v_ver, v_med3, NULL, NULL, NULL, NULL, NULL, 1, 0, NULL, v_tmp, v_tmp2);", 'error', 'latest prescription version')
step("SELECT prescription_item_id INTO v_item3b FROM prescription_item WHERE prescription_version_id = v_ver2 AND prescription_line_id = v_line3;")
# ---- orders
step("CALL sp_create_clinical_order(v_visit, v_ep, 'medication', v_ord);")
step("CALL sp_add_order_item(v_ord, 'medication', v_med3, NULL, NULL, 1, 'tablet', 'oral', NOW() + INTERVAL 1 HOUR, 480, 3, 'after food', NULL, v_oi);")
step(CTX('v_nur', 'NURSE'))
test('R4.7', 'nurses cannot act on a draft order', "CALL sp_administer_dose(v_oi, 'given', NULL, 1, NULL, NULL, NULL, NULL, NULL, NULL, v_dose);", 'error', 'not finalized')
step(CTX('v_doc', 'DOCTOR'))
step("CALL sp_finalize_order(v_ord);")
# ---- pharmacy
step(CTX('v_rec', 'RECEPTIONIST'))
step("CALL sp_add_patient_attendant(v_p1, 'Ram', 'father', '9111111111', 1, v_att);")
step(CTX('v_pha', 'PHARMACY'))
test('R5.4/R10.2', 'naming an expired batch is refused', "CALL sp_dispense_clinic_prescription(v_item3b, 2, v_bexp, v_oi, v_nur, NULL, 'supply_to_nurse', v_disp);", 'error', 'Expired batch')
test('R5.4/R10.2', 'the expired-dispense attempt left an interrupt alert (persisted)', "SELECT 1 INTO v_tmp;", 'assert',
     "EXISTS (SELECT 1 FROM alert_event WHERE alert_type = 'EXPIRED_DISPENSE_ATTEMPT' AND alert_class = 'safety_interrupt' AND source_entity_id = CAST(v_bexp AS CHAR))")
test('R4.1/R5.5', 'items of an old prescription version cannot be dispensed', "CALL sp_dispense_clinic_prescription(v_itemft, 1, NULL, NULL, v_nur, NULL, 'supply_to_nurse', v_disp);", 'error', 'not in the latest prescription')
test('R5.5/R2.5/R8.2', 'dispensing creates stock-out movement, bill line and custody handoff in one transaction',
     "CALL sp_dispense_clinic_prescription(v_item3b, 6, NULL, v_oi, v_nur, NULL, 'supply_to_nurse', v_disp);", 'assert',
     "(SELECT -SUM(quantity_delta) FROM stock_movement WHERE dispensing_id = v_disp) = 6 AND EXISTS (SELECT 1 FROM bill_line l JOIN stock_movement m ON m.stock_movement_id = l.source_stock_movement_id WHERE m.dispensing_id = v_disp AND l.line_amount = 18.00) AND EXISTS (SELECT 1 FROM custody_handoff WHERE dispensing_id = v_disp AND recipient_staff_id = v_nur)")
test('R5.4', 'FEFO: stock was taken from the non-expired batch only', "SELECT 1 INTO v_tmp;", 'assert',
     "(SELECT SUM(quantity_delta) FROM stock_movement WHERE stock_batch_id = v_b3) = 14 AND (SELECT SUM(quantity_delta) FROM stock_movement WHERE stock_batch_id = v_bexp) = 5")
test('R4.6', 'cannot dispense more than prescribed', "CALL sp_dispense_clinic_prescription(v_item3b, 5, NULL, v_oi, v_nur, NULL, 'supply_to_nurse', v_tmp);", 'error', 'More than the prescribed')
test('R5.5', 'Schedule H cannot be sold over the counter (database rule)',
     "CALL sp_register_counter_customer('Walk In', NULL, v_cc, v_ccode); CALL sp_start_counter_sale(v_cc, v_sale, v_snum); CALL sp_dispense_otc(v_sale, v_medH, 1, NULL, v_tmp);", 'error', 'Schedule H')
test('R5.5/R5.7', 'OTC sale of a plain medicine is priced from the batch MRP',
     "CALL sp_dispense_otc(v_sale, v_med3, 2, NULL, v_d2);", 'assert', "(SELECT total_amount FROM v_counter_sale_summary WHERE counter_sale_id = v_sale) = 6.00")
test('R5.7', 'counter payment above the balance is refused', "CALL sp_counter_sale_payment(v_sale, 10, 'cash', v_tmp, v_rcpt);", 'error', 'not more than the balance')
test('R5.7', 'pharmacy records payment for a counter sale', "CALL sp_counter_sale_payment(v_sale, 6, 'cash', v_tmp, v_rcpt);", 'assert',
     "(SELECT balance FROM v_counter_sale_summary WHERE counter_sale_id = v_sale) = 0.00")
step("SELECT custody_handoff_id INTO v_hand FROM custody_handoff WHERE dispensing_id = v_disp LIMIT 1;")
test('G1/R6.2', 'the giver cannot acknowledge his own handoff', "CALL sp_acknowledge_handoff(v_hand, 6, 'self_logged_staff', NULL);", 'error', 'not allowed')
step(CTX('v_nur', 'NURSE'))
test('G1/R6.2', 'the recipient nurse acknowledges the handoff', "CALL sp_acknowledge_handoff(v_hand, 6, 'self_logged_staff', NULL);", 'assert',
     "EXISTS (SELECT 1 FROM custody_handoff_acknowledgement WHERE custody_handoff_id = v_hand)")
test('R6.1', 'a given dose consumes units from the dispensed supply', "CALL sp_administer_dose(v_oi, 'given', NULL, 1, NULL, NULL, NULL, NULL, NULL, NULL, v_dose);", 'assert',
     "(SELECT consumed_qty FROM v_dispensing_custody WHERE dispensing_id = v_disp) = 1")
test('R6.1', 'a dose that is not given needs a reason', "CALL sp_administer_dose(v_oi, 'refused', NULL, 0, NULL, NULL, NULL, NULL, NULL, NULL, v_tmp);", 'error', 'reason is required')
test('R10.2', 'a dose above the order needs a doctor as authorizer',
     "CALL sp_administer_dose(v_oi, 'given', NULL, 2, 2, NULL, NULL, 'doctor said double', NULL, NULL, v_tmp);", 'error', 'needs a doctor')
test('R10.2/R6.1', 'authorised higher dose is stored with ordered vs actual and raises an interrupt',
     "CALL sp_administer_dose(v_oi, 'given', NULL, 2, 2, NULL, NULL, 'doctor said double', v_doc, NULL, v_dose);", 'assert',
     "EXISTS (SELECT 1 FROM alert_event WHERE alert_type = 'DOSE_ABOVE_ORDER' AND alert_class = 'safety_interrupt' AND source_entity_id = CAST(v_dose AS CHAR)) AND (SELECT ordered_dose_value FROM dose_administration WHERE dose_administration_id = v_dose) = 1 AND (SELECT actual_dose_value FROM dose_administration WHERE dose_administration_id = v_dose) = 2")
test('R6.2', 'unreconciled medicine shows as a mismatch', "SELECT 1 INTO v_tmp;", 'assert', "(SELECT mismatch_qty FROM v_dispense_reconciliation WHERE dispensing_id = v_disp) = 3")
test('R6.3', 'leftover request cannot exceed what is left', "CALL sp_request_leftover_return(v_disp, 4, 'sealed strip', v_lreq);", 'error', 'more than what is left')
step("CALL sp_request_leftover_return(v_disp, 3, 'sealed strip', v_lreq);")
step(CTX('v_pha', 'PHARMACY'))
test('R6.3', 'accepted leftover returns stock, adds a credit bill line and reconciles',
     "CALL sp_decide_leftover(v_lreq, 'accepted', 'sealed and in date', NULL, v_ldec);", 'assert',
     "(SELECT mismatch_qty FROM v_dispense_reconciliation WHERE dispensing_id = v_disp) = 0 AND (SELECT sellable_qty FROM v_stock_by_medicine WHERE medicine_id = v_med3) = 15 AND EXISTS (SELECT 1 FROM bill_line WHERE line_type = 'return_credit' AND line_amount = -9.00)")
# ---- billing
step("SELECT bill_id INTO v_bill FROM bill WHERE visit_id = v_visit;")
step(CTX('v_owner', 'OWNER'))
step("CALL sp_set_price(CONCAT('CONS', v_sfx), 'Consultation', 'consultation', 300, CURRENT_DATE, v_pl);")
step(CTX('v_rec', 'RECEPTIONIST'))
test('R8.1', 'only the owner can write the price list', "INSERT INTO price_list (item_code, description, category, price, effective_from) VALUES ('X1', 'x', 'other', 1, CURRENT_DATE);", 'error', 'Only the owner')
test('R8.1/R8.2', 'a fixed-price line comes from the price list', "CALL sp_add_price_list_line(v_visit, CONCAT('CONS', v_sfx), 1, v_l1);", 'assert',
     "(SELECT net_total FROM v_bill_summary WHERE bill_id = v_bill) = 309.00")
test('R8.1', 'off-list price needs a reason', "CALL sp_add_manual_line(v_visit, 'extra', 1, 10, '', v_tmp);", 'error', 'reason is required')
step("CALL sp_request_discount(v_bill, 50, 'discount', 'senior citizen', v_dappr);")
test('R8.4/G6', 'a discount line without approved request is refused', "CALL sp_apply_discount(v_dappr, v_dline);", 'error', 'approved discount request')
step(CTX('v_owner', 'OWNER'))
step("CALL decide_approval(v_dappr, 'APPROVED', 'ok');")
step(CTX('v_rec', 'RECEPTIONIST'))
test('R8.4', 'approved discount becomes a separate negative line', "CALL sp_apply_discount(v_dappr, v_dline);", 'assert',
     "(SELECT net_total FROM v_bill_summary WHERE bill_id = v_bill) = 259.00 AND (SELECT line_amount FROM bill_line WHERE bill_line_id = v_dline) = -50.00")
step("CALL sp_finalize_bill(v_bill, 'ready to pay');")
test('R8.3', 'after finalization a normal charge line is refused', "CALL sp_add_manual_line(v_visit, 'late', 1, 10, 'forgot', v_tmp);", 'error', 'finalized')
step(CTX('v_acc', 'ACCOUNTANT'))
test('R8.5', 'payment above the balance is refused', "CALL sp_record_payment(v_bill, 1000, 'cash', v_pay, v_rcpt);", 'error', 'not more than the balance')
test('R8.5', 'payment reduces the computed balance and queues a receipt SMS',
     "CALL sp_record_payment(v_bill, 200, 'cash', v_pay, v_rcpt);", 'assert',
     "(SELECT balance FROM v_bill_summary WHERE bill_id = v_bill) = 59.00 AND EXISTS (SELECT 1 FROM message_outbox WHERE payment_id = v_pay AND message_type = 'receipt')")
step("CALL sp_request_payment_reversal(v_pay, 'wrong amount entered', v_rap);")
test('R8.5/G6', 'payment reversal without approval is refused', "CALL sp_reverse_payment(v_rap, v_rcpt2);", 'error', 'approved reversal request')
step(CTX('v_owner', 'OWNER'))
step("CALL decide_approval(v_rap, 'APPROVED', 'ok');")
step(CTX('v_acc', 'ACCOUNTANT'))
test('R8.5', 'approved reversal is a new row; the original payment stays', "CALL sp_reverse_payment(v_rap, v_rcpt2);", 'assert',
     "(SELECT paid_total FROM v_bill_summary WHERE bill_id = v_bill) = 0.00 AND EXISTS (SELECT 1 FROM payment WHERE payment_id = v_pay AND payment_kind = 'payment')")
step("CALL sp_record_payment(v_bill, 200, 'cash', v_pay2, v_rcpt);")
# ---- discharge
step(CTX('v_doc', 'DOCTOR'))
test('R9.4', 'discharge needs the clinical sign-off', "CALL sp_discharge_visit(v_visit, 0, NULL, v_did);", 'error', 'sign-off')
step("CALL sp_clinical_signoff(v_visit, 'Rest and plenty of fluids', v_so);")
step(CTX('v_rec', 'RECEPTIONIST'))
test('R9.1', 'discharge with a balance is refused', "CALL sp_discharge_visit(v_visit, 0, NULL, v_did);", 'error', 'not settled')
step("CALL sp_request_partial_discharge(v_visit, CURRENT_DATE + INTERVAL 3 DAY, 'will pay on Friday', v_papp);")
test('R9.1', 'a request that is not approved yet does not open the gate', "CALL sp_discharge_visit(v_visit, 0, NULL, v_did);", 'error', 'not settled')
step(CTX('v_doc', 'DOCTOR'))
test('G6/R9.1', 'a doctor without delegation cannot approve a partial-payment discharge', "CALL decide_approval(v_papp, 'APPROVED', 'ok');", 'error', 'not allowed to approve')
step(CTX('v_owner', 'OWNER'))
step("CALL decide_approval(v_papp, 'APPROVED', 'ok');")
step(CTX('v_rec', 'RECEPTIONIST'))
test('R9.1/R9.4', 'approved partial payment: discharge closes the visit in one transaction and keeps a due with the promised date',
     "CALL sp_discharge_visit(v_visit, 0, NULL, v_did);", 'assert',
     "visit_is_closed(v_visit) = 1 AND (SELECT financial_gate_basis FROM discharge WHERE discharge_id = v_did) = 'approved_partial_payment' AND EXISTS (SELECT 1 FROM v_due_list WHERE visit_id = v_visit AND balance = 59.00 AND promised_by_date = CURRENT_DATE + INTERVAL 3 DAY) AND (SELECT status FROM visit_status_history WHERE visit_id = v_visit ORDER BY status_seq DESC LIMIT 1) = 'discharged' AND EXISTS (SELECT 1 FROM short_stay_closure WHERE short_stay_episode_id = v_ep)")
test('R9.3', 'the patient is flagged as having dues', "SELECT 1 INTO v_tmp;", 'assert', "patient_has_dues(v_p1) = 1")
test('R9.4', 'a closed visit accepts no new status', "CALL sp_add_visit_status(v_visit, 'billing', NULL, NULL);", 'error', 'closed')
step(CTX('v_doc', 'DOCTOR'))
test('R9.4', 'a closed visit accepts no new prescription lines', "CALL sp_prescribe_item(v_ver2, v_med3, NULL, NULL, NULL, NULL, NULL, 1, 0, NULL, v_tmp, v_tmp2);", 'error', 'closed')
step(CTX('v_rec', 'RECEPTIONIST'))
step("CALL sp_request_visit_reopen(v_visit, 'discharged by mistake', v_rap2);")
step(CTX('v_owner', 'OWNER'))
step("CALL decide_approval(v_rap2, 'APPROVED', 'ok');")
step(CTX('v_rec', 'RECEPTIONIST'))
test('R9.4/G6', 'reopening needs an approval and adds a reversal row (closure stays)', "CALL sp_reopen_visit(v_rap2);", 'assert',
     "visit_is_closed(v_visit) = 0 AND (SELECT COUNT(*) FROM visit_closure WHERE visit_id = v_visit) = 2")
# emergency override
step("CALL sp_create_visit(v_p3, NULL, v_doc, 'emergency', v_visit2);")
step("CALL sp_add_manual_line(v_visit2, 'Emergency procedure', 1, 100, 'procedure not on list', v_tmp);")
step(CTX('v_doc', 'DOCTOR'))
step("CALL sp_clinical_signoff(v_visit2, 'Referred', v_so2);")
step(CTX('v_rec', 'RECEPTIONIST'))
test('R9.2', 'only a doctor can use the clinical emergency override', "CALL sp_discharge_visit(v_visit2, 1, 'critical patient referred urgently', v_did2);", 'error', 'Only a doctor')
step(CTX('v_doc', 'DOCTOR'))
test('R9.2', 'the override needs a real justification', "CALL sp_discharge_visit(v_visit2, 1, 'urgent', v_did2);", 'error', 'at least 10')
test('R9.2/R10.2', 'emergency override discharges, is logged and raises an interrupt',
     "CALL sp_discharge_visit(v_visit2, 1, 'critical patient referred urgently', v_did2);", 'assert',
     "(SELECT financial_gate_basis FROM discharge WHERE discharge_id = v_did2) = 'emergency_override' AND EXISTS (SELECT 1 FROM clinical_emergency_override WHERE visit_id = v_visit2) AND EXISTS (SELECT 1 FROM alert_event WHERE alert_type = 'EMERGENCY_OVERRIDE' AND alert_class = 'safety_interrupt' AND source_entity_id = CAST(v_visit2 AS CHAR))")
# ---- labs
step(CTX('v_rec', 'RECEPTIONIST'))
step("CALL sp_add_outside_lab(CONCAT('Lab', v_sfx), '9222222222', 'Mr Lab', NULL, v_lab);")
step("CALL sp_create_visit(v_p1, NULL, v_doc, 'walk_in', v_visit3);")
step(CTX('v_doc', 'DOCTOR'))
test('R8.1', 'lab price without list entry needs a reason', "CALL sp_create_lab_order(v_visit3, v_lab, 'CBC', NULL, 400, '', v_lo);", 'error', 'reason is required')
test('R7.1/R8.2', 'lab order creates its status, price and bill line',
     "CALL sp_create_lab_order(v_visit3, v_lab, 'CBC', NULL, 400, 'outside lab rate', v_lo);", 'assert',
     "EXISTS (SELECT 1 FROM bill_line WHERE lab_order_id = v_lo AND line_type = 'lab_test' AND is_manual_price = 1 AND line_amount = 400.00) AND (SELECT step_status FROM v_lab_order_current WHERE lab_order_id = v_lo) = 'ordered'")
step(CTX('v_nur', 'NURSE'))
test('R7.2', 'lab steps follow the order (cannot skip sample collection)', "CALL sp_lab_order_step(v_lo, 'sent_to_lab', NULL);", 'error', 'not allowed now')
step("CALL sp_lab_order_step(v_lo, 'sample_collected', NULL);")
step("CALL sp_lab_order_step(v_lo, 'sent_to_lab', NULL);")
test('R7.3/R10.2', 'a report with wrong identifiers is NOT attached and an interrupt is raised',
     "CALL sp_attach_lab_report(v_lo, 'from_lab', '/files/r1.pdf', REPEAT('a', 64), 'patient_name', 'Somebody Else', 'date_of_birth', '1901-01-01', v_rep, v_msg);", 'assert',
     "v_rep IS NULL AND v_msg LIKE '%NOT attached%' AND EXISTS (SELECT 1 FROM alert_event WHERE alert_type = 'WRONG_PATIENT_REPORT' AND alert_class = 'safety_interrupt' AND source_entity_id = CAST(v_lo AS CHAR))")
test('R7.3', 'two identifiers must be of different kinds',
     "CALL sp_attach_lab_report(v_lo, 'from_lab', '/files/r1.pdf', REPEAT('a', 64), 'patient_name', 'x', 'patient_name', 'y', v_rep, v_msg);", 'error', 'DIFFERENT')
test('R7.3', 'matching identifiers store the report with its hash and version 1',
     "CALL sp_attach_lab_report(v_lo, 'from_lab', '/files/r1.pdf', REPEAT('a', 64), 'patient_name', CONCAT('Zq', v_sfx, ' Sharma'), 'date_of_birth', DATE_FORMAT(v_dob, '%Y-%m-%d'), v_rep, v_msg);", 'assert',
     "v_rep IS NOT NULL AND (SELECT version_number FROM lab_report WHERE lab_report_id = v_rep) = 1 AND (SELECT content_hash FROM lab_report WHERE lab_report_id = v_rep) = REPEAT('a', 64)")
test('R7.4', 'an attached report waits for the doctor review', "SELECT 1 INTO v_tmp;", 'assert', "EXISTS (SELECT 1 FROM v_report_unreviewed WHERE lab_report_id = v_rep)")
test('R10.2', 'critical mark is recorded', "CALL sp_mark_report_critical(v_rep, 'critical', 'nurse_observation', 'very high value');", 'assert',
     "(SELECT is_critical FROM v_report_unreviewed WHERE lab_report_id = v_rep) = 1")
step(CTX('v_doc', 'DOCTOR'))
test('R7.4', 'doctor review is its own event and clears the waiting list', "CALL sp_review_report(v_rep, 'noted, will call patient');", 'assert',
     "NOT EXISTS (SELECT 1 FROM v_report_unreviewed WHERE lab_report_id = v_rep)")
# ---- family links
step(CTX('v_rec', 'RECEPTIONIST'))
test('R11.4', 'a family link needs a consent note', "CALL sp_create_family_link(v_visit3, v_att, 'date_of_birth', '', v_tok, v_pc, v_link);", 'error', 'consent')
test('R11.1/R11.3', 'link creation stores only a hash and queues a link-only SMS',
     "CALL sp_create_family_link(v_visit3, v_att, 'date_of_birth', 'Father Ram agreed', v_tok, v_pc, v_link);", 'assert',
     "CHAR_LENGTH(v_tok) = 48 AND (SELECT token_hash FROM family_link WHERE family_link_id = v_link) = SHA2(v_tok, 256) AND EXISTS (SELECT 1 FROM message_outbox WHERE family_link_id = v_link AND rendered_text LIKE '%/v/%' AND rendered_text NOT LIKE '%Sharma%')")
step("CALL sp_open_family_link(v_tok, '1900-01-01', 'test', v_out, v_vis, v_lid, v_retry);")
step("CALL sp_open_family_link(v_tok, '1900-01-01', 'test', v_out, v_vis, v_lid, v_retry);")
step("CALL sp_open_family_link(v_tok, '1900-01-01', 'test', v_out, v_vis, v_lid, v_retry);")
step("CALL sp_open_family_link(v_tok, '1900-01-01', 'test', v_out, v_vis, v_lid, v_retry);")
step("CALL sp_open_family_link(v_tok, '1900-01-01', 'test', v_out, v_vis, v_lid, v_retry);")
test('R11.1', 'after 5 wrong attempts even the correct DOB is locked out',
     "CALL sp_open_family_link(v_tok, DATE_FORMAT(v_dob, '%Y-%m-%d'), 'test', v_out, v_vis, v_lid, v_retry);", 'assert', "v_out = 'locked' AND v_retry > 0 AND v_vis IS NULL")
test('R11.4', 'every attempt is logged (5 bad + 1 locked)', "SELECT 1 INTO v_tmp;", 'assert', "(SELECT COUNT(*) FROM link_access_log WHERE family_link_id = v_link) = 6")
step("CALL sp_create_family_link(v_visit3, NULL, 'date_of_birth', 'second attendant agreed', v_tok2, v_pc, v_link2);")
test('R11.1/R11.2', 'a second link with the correct DOB opens only its own visit',
     "CALL sp_open_family_link(v_tok2, DATE_FORMAT(v_dob, '%Y-%m-%d'), 'test', v_out, v_vis, v_lid, v_retry);", 'assert', "v_out = 'ok' AND v_vis = v_visit3 AND v_lid = v_link2")
test('R11.3', 'family views show the visit and the shareable bill without cost prices', "SELECT 1 INTO v_tmp;", 'assert',
     "(SELECT COUNT(*) FROM v_family_visit WHERE family_link_id = v_link2) = 1 AND (SELECT COUNT(*) FROM v_family_bill WHERE family_link_id = v_link2) >= 1")
test('R11.1', 'an unknown token is just invalid', "CALL sp_open_family_link(REPEAT('0', 48), '1900-01-01', 'test', v_out, v_vis, v_lid, v_retry);", 'assert', "v_out = 'invalid'")
step("CALL sp_revoke_family_link(v_link2, 'attendant left');")
test('R11.2', 'a revoked link stops working at once', "CALL sp_open_family_link(v_tok2, DATE_FORMAT(v_dob, '%Y-%m-%d'), 'test', v_out, v_vis, v_lid, v_retry);", 'assert',
     "v_out = 'revoked' AND (SELECT COUNT(*) FROM v_family_visit WHERE family_link_id = v_link2) = 0")
# ---- owner alerts
step(CTX('v_owner', 'OWNER'))
test('R10.1', 'alert checks run without error', "CALL sp_check_alerts();", 'ok')
test('R10.1', 'daily summary ranks red/amber/clean checks', "SELECT 1 INTO v_tmp;", 'assert',
     "(SELECT COUNT(*) FROM v_owner_summary) >= 20 AND EXISTS (SELECT 1 FROM v_owner_summary WHERE check_code = 'approvals_waiting')")
test('R10.4', 'owner acknowledges an interrupt once', "SELECT alert_event_id INTO v_tmp FROM alert_event WHERE alert_class = 'safety_interrupt' AND alert_type = 'EMERGENCY_OVERRIDE' ORDER BY alert_event_id DESC LIMIT 1; CALL sp_acknowledge_alert(v_tmp, 'seen');", 'assert',
     "EXISTS (SELECT 1 FROM alert_acknowledgement WHERE alert_event_id = v_tmp)")
test('R10.4', 'a second acknowledgement is refused', "CALL sp_acknowledge_alert(v_tmp, 'again');", 'error', 'already acknowledged')
step(CTX('v_pha', 'PHARMACY'))
step("CALL sp_declare_cash_handover(NOW(3) - INTERVAL 1 DAY, NOW(3) + INTERVAL 1 HOUR, 6.00, 'day end', v_decl);")
test('R5.7', 'declared cash equal to the counter-sale cash raises no difference alert', "SELECT 1 INTO v_tmp;", 'assert',
     "NOT EXISTS (SELECT 1 FROM alert_event WHERE alert_type = 'CASH_DECLARATION_DIFF' AND source_entity_id = CAST(v_decl AS CHAR))")
test('R5.7', 'only the accountant counts the cash', "CALL sp_count_cash_handover(v_decl, 6.00, 'ok');", 'error', 'not allowed')
step(CTX('v_acc', 'ACCOUNTANT'))
test('R5.7', 'a counted amount that differs from the declaration raises an alert', "CALL sp_count_cash_handover(v_decl, 5.00, 'one note missing');", 'assert',
     "EXISTS (SELECT 1 FROM alert_event WHERE alert_type = 'CASH_COUNT_DIFF' AND source_entity_id = CAST(v_decl AS CHAR))")
# ---- delegation
step(CTX('v_owner', 'OWNER'))
step("CALL sp_delegate_authority(v_doc, 'discount', 100, NOW(3) - INTERVAL 1 MINUTE, NOW(3) + INTERVAL 1 DAY, v_dg);")
step("SELECT bill_id INTO v_bill3 FROM bill WHERE visit_id = v_visit3;")
step(CTX('v_rec', 'RECEPTIONIST'))
step("CALL sp_request_discount(v_bill3, 50, 'discount', 'regular patient', v_da1);")
step("CALL sp_request_discount(v_bill3, 500, 'discount', 'big discount', v_da2);")
step(CTX('v_doc', 'DOCTOR'))
test('R2.3/R8.4', 'a delegated doctor approves a discount inside his limit', "CALL decide_approval(v_da1, 'APPROVED', 'ok');", 'assert', "approval_is_valid(v_da1, 'discount,free_service') = 1")
test('R2.3/G6', 'the same doctor cannot approve above the delegated limit', "CALL decide_approval(v_da2, 'APPROVED', 'ok');", 'error', 'not allowed to approve')
step(CTX('v_rec', 'RECEPTIONIST'))
step("CALL request_approval_ex('refund', NULL, v_bill3, NULL, NULL, NULL, 10, NULL, NULL, 'refund test', v_da3);")
step(CTX('v_doc', 'DOCTOR'))
test('G6', 'a discount delegation does not cover a refund request', "CALL decide_approval(v_da3, 'APPROVED', 'ok');", 'error', 'not allowed to approve')
step(CTX('v_owner', 'OWNER'))
step("CALL sp_revoke_delegation(v_dg, 'test over');")
step(CTX('v_rec', 'RECEPTIONIST'))
step("CALL sp_request_discount(v_bill3, 20, 'discount', 'another', v_da4);")
step(CTX('v_doc', 'DOCTOR'))
test('G6', 'a revoked delegation no longer works', "CALL decide_approval(v_da4, 'APPROVED', 'ok');", 'error', 'not allowed to approve')



def block(rule, name, action, kind):
    name = name.replace("'", "''")
    ins = lambda o, d: f"INSERT INTO t_results (outcome, rule, name, detail) VALUES ({o}, '{rule}', '{name}', {d});"
    if kind[0] == 'error':
        pat = kind[1].replace("'", "''")
        return (f"  BEGIN\n    DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN\n      GET DIAGNOSTICS CONDITION 1 @e = MESSAGE_TEXT;\n"
                f"      {ins(f'''IF(@e LIKE '%{pat}%', 'PASS', 'FAIL')''', '@e')}\n    END;\n    {action}\n    {ins(chr(39)+'FAIL'+chr(39), chr(39)+'no error was raised'+chr(39))}\n  END;")
    if kind[0] == 'ok':
        return (f"  BEGIN\n    DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN\n      GET DIAGNOSTICS CONDITION 1 @e = MESSAGE_TEXT;\n      {ins(chr(39)+'FAIL'+chr(39), '@e')}\n    END;\n"
                f"    {action}\n    {ins(chr(39)+'PASS'+chr(39), chr(39)+chr(39))}\n  END;")
    return (f"  BEGIN\n    DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN\n      GET DIAGNOSTICS CONDITION 1 @e = MESSAGE_TEXT;\n      {ins(chr(39)+'FAIL'+chr(39), '@e')}\n    END;\n"
            f"    {action}\n    {ins(f'IF(({kind[1]}), {chr(39)}PASS{chr(39)}, {chr(39)}FAIL{chr(39)})', chr(39)+chr(39))}\n  END;")


vars_ = ("v_sfx VARCHAR(8); v_code VARCHAR(30); v_owner BIGINT UNSIGNED; v_doc BIGINT UNSIGNED; v_rec BIGINT UNSIGNED; v_nur BIGINT UNSIGNED; v_pha BIGINT UNSIGNED; "
         "v_med BIGINT UNSIGNED; v_batch BIGINT UNSIGNED; v_appr BIGINT UNSIGNED; v_tmp BIGINT UNSIGNED; v_dob DATE; v_p1 BIGINT UNSIGNED; v_p2 BIGINT UNSIGNED; "
         "v_p3 BIGINT UNSIGNED; v_sched BIGINT UNSIGNED; v_newsched BIGINT UNSIGNED; v_slot BIGINT UNSIGNED; v_appt BIGINT UNSIGNED; v_appt2 BIGINT UNSIGNED; "
         "v_req BIGINT UNSIGNED; v_visit BIGINT UNSIGNED; v_ep BIGINT UNSIGNED; v_phone VARCHAR(20); v_acc BIGINT UNSIGNED; v_ing BIGINT UNSIGNED; v_med2 BIGINT UNSIGNED; v_med3 BIGINT UNSIGNED; v_medH BIGINT UNSIGNED; v_b2 BIGINT UNSIGNED; v_b3 BIGINT UNSIGNED; v_bH BIGINT UNSIGNED; v_bexp BIGINT UNSIGNED; v_al BIGINT UNSIGNED; v_rx BIGINT UNSIGNED; v_ver BIGINT UNSIGNED; v_ver2 BIGINT UNSIGNED; v_item BIGINT UNSIGNED; v_line BIGINT UNSIGNED; v_line_ov BIGINT UNSIGNED; v_itemft BIGINT UNSIGNED; v_item3 BIGINT UNSIGNED; v_line3 BIGINT UNSIGNED; v_item3b BIGINT UNSIGNED; v_ord BIGINT UNSIGNED; v_oi BIGINT UNSIGNED; v_att BIGINT UNSIGNED; v_disp BIGINT UNSIGNED; v_cc BIGINT UNSIGNED; v_ccode VARCHAR(30); v_sale BIGINT UNSIGNED; v_snum VARCHAR(30); v_d2 BIGINT UNSIGNED; v_hand BIGINT UNSIGNED; v_dose BIGINT UNSIGNED; v_lreq BIGINT UNSIGNED; v_ldec BIGINT UNSIGNED; v_bill BIGINT UNSIGNED; v_pl BIGINT UNSIGNED; v_l1 BIGINT UNSIGNED; v_dappr BIGINT UNSIGNED; v_dline BIGINT UNSIGNED; v_pay BIGINT UNSIGNED; v_pay2 BIGINT UNSIGNED; v_rcpt VARCHAR(30); v_rcpt2 VARCHAR(30); v_rap BIGINT UNSIGNED; v_rap2 BIGINT UNSIGNED; v_papp BIGINT UNSIGNED; v_did BIGINT UNSIGNED; v_did2 BIGINT UNSIGNED; v_so BIGINT UNSIGNED; v_so2 BIGINT UNSIGNED; v_visit2 BIGINT UNSIGNED; v_visit3 BIGINT UNSIGNED; v_lab BIGINT UNSIGNED; v_lo BIGINT UNSIGNED; v_rep BIGINT UNSIGNED; v_msg VARCHAR(200); v_tok VARCHAR(64); v_tok2 VARCHAR(64); v_pc VARCHAR(12); v_link BIGINT UNSIGNED; v_link2 BIGINT UNSIGNED; v_dg BIGINT UNSIGNED; v_bill3 BIGINT UNSIGNED; v_da1 BIGINT UNSIGNED; v_da2 BIGINT UNSIGNED; v_da3 BIGINT UNSIGNED; v_da4 BIGINT UNSIGNED; v_out VARCHAR(15); v_decl BIGINT UNSIGNED; v_vis BIGINT UNSIGNED; v_lid BIGINT UNSIGNED; v_retry INT; v_tmp2 BIGINT UNSIGNED").split('; ')
out = ["-- GENERATED by tools/gen_rule_tests.py - rule tests for foundation + patients/visits (Track B batch 1-2).",
       "-- Run: docker compose exec -T mysql sh -c 'mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" clinic_db' < db/tests/rule_tests.sql",
       "-- Re-runnable: every run uses a fresh random suffix. Output: one row per test (PASS/FAIL), then a summary row.",
       "USE clinic_db;", "DROP PROCEDURE IF EXISTS t_run_rule_tests;", "DELIMITER $$", "CREATE PROCEDURE t_run_rule_tests()", "BEGIN"]
out += [f"  DECLARE {v.strip().rstrip(';')};" for v in vars_]
out += ["  DROP TEMPORARY TABLE IF EXISTS t_results;",
        "  CREATE TEMPORARY TABLE t_results (seq INT AUTO_INCREMENT PRIMARY KEY, outcome VARCHAR(4), rule VARCHAR(20), name VARCHAR(120), detail VARCHAR(250));"]
for item in S:
    out.append('  ' + item[1] if item[0] == 'step' else block(item[0], item[1], item[2], item[3]))
out += ["  SET @app_user_id = NULL, @app_role = NULL;",
        "  SELECT outcome, rule, name, detail FROM t_results ORDER BY seq;",
        "  SELECT CONCAT(SUM(outcome = 'PASS'), ' passed, ', SUM(outcome = 'FAIL'), ' failed') AS summary FROM t_results;",
        "END$$", "DELIMITER ;", "CALL t_run_rule_tests();", "DROP PROCEDURE t_run_rule_tests;"]
open(os.path.join(ROOT, 'db/tests/rule_tests.sql'), 'w').write('\n'.join(out) + '\n')
print('tests:', sum(1 for s in S if s[0] != 'step'))
