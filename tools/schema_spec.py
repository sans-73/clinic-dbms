"""
SINGLE SOURCE OF TRUTH for the physical schema (Track B, derived from docs/design/01_er_model.md rev 2).

Edit THIS file, then run:  python tools/build_db.py
It regenerates db/schema/*.sql, db/schema/09_foreign_keys.sql, db/logic/01_foundation_triggers.sql
and docs/design/02_table_design.BASELINE.md.  Never hand-edit the generated files.

Column mini-language:   name[?]:TYPE[=default][->parent[.col]]
   '?' = nullable.   TYPE aliases are in build_db.py (V120, B, D, DT, M, Q, E(a|b|c) ...).
Table kinds: 'M' master (editable + audited), 'E' event (append-only), 'O' operational (system only).
Cluster: 1 core, 2 patients+visits, 3 pharmacy, 4 orders+billing+discharge.
"""

# ---------- reusable SQL fragments used inside trigger snippets ----------
V_CLOSED = ("IF visit_is_closed(NEW.visit_id) THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;")

T = []  # list of dict tables


def table(name, cluster, kind, comment, cols, **kw):
    t = dict(name=name, cluster=cluster, kind=kind, comment=comment, cols=cols,
             uniques=[], checks=[], indexes=[], raw=[], fk_extra=[], pk=None,
             corrects=False, rules='', bi='', bu='', audit_exclude=[], extension=False)
    t.update(kw)
    T.append(t)


# =====================================================================
# CLUSTER 1  core: settings, staff, roles, approvals, audit, alerts, ops
# =====================================================================
table('clinic_settings', 1, 'M', 'Policy values and thresholds (G9). Edits audited.',
      ['setting_key:V60', 'setting_value:V500', 'value_type:E(int|text|decimal|bool|phone)=text', 'description?:V200'],
      pk=['setting_key'], rules='G9,R13.1',
      bi="IF app_role() <> 'OWNER' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the owner can change clinic settings'; END IF;",
      bu="IF app_role() <> 'OWNER' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the owner can change clinic settings'; END IF;")

table('id_counter', 1, 'O', 'Concurrency-safe allocator for display codes (CLN1-000123, TMP-00123). Changed only by next_code().',
      ['counter_name:V30', 'next_value:B=1', 'display_prefix:V20'], pk=['counter_name'], rules='R1.1,R1.5')

table('role', 1, 'M', 'Role catalog (hats).', ['role_code:V20', 'role_name:V60'], uniques=[['role_code']], rules='R2.1')

table('staff', 1, 'M', 'Every actor: humans and 4 seeded system principals (G5).',
      ['display_code:V30', 'principal_kind:E(human|system)=human', 'given_name:V80', 'family_name?:V80', 'phone?:V20',
       'active_from:D=(CURRENT_DATE)', 'active_to?:D', 'login_subject?:V60'],
      uniques=[['display_code'], ['login_subject']],
      checks=["principal_kind = 'human' OR login_subject IS NULL"], rules='R2.1,G5')

table('staff_credential', 1, 'M', 'EXTENSION (CR-B1): password hash for app login; not in ER because ER stores no credentials on Staff.',
      ['staff_id:B->staff', 'password_hash:V255'], uniques=[['staff_id']], audit_exclude=['password_hash'], extension=True, rules='G4,R2.2')

table('staff_role', 1, 'M', 'Staff x Role (overlapping roles allowed).',
      ['staff_id:B->staff', 'role_id:B->role', 'valid_from:D=(CURRENT_DATE)', 'valid_to?:D', 'granted_by:B->staff'],
      uniques=[['staff_id', 'role_id', 'valid_from']], rules='R2.1')

for tn, role, cols in [('doctor_detail', 'DOCTOR', ['registration_number:V40', 'specialty?:V80']),
                       ('nurse_detail', 'NURSE', ['registration_number?:V40']),
                       ('pharmacist_detail', 'PHARMACY', ['registration_number?:V40']),
                       ('accountant_detail', 'ACCOUNTANT', ['accounting_reference?:V40']),
                       ('receptionist_detail', 'RECEPTIONIST', ['desk_label?:V40'])]:
    snip = (f"IF NOT staff_has_role(NEW.staff_id, '{role}') THEN SIGNAL SQLSTATE '45000' "
            f"SET MESSAGE_TEXT = 'Staff member does not hold the {role} role'; END IF;")
    table(tn, 1, 'M', f'Subtype of Staff (S1) for {role}.', ['staff_id:B->staff'] + cols, pk=['staff_id'], bi=snip, rules='R2.1')

table('authority_delegation', 1, 'E', 'Owner delegates limited approval authority.',
      ['delegate_staff_id:B->staff', 'scope:E(discount|partial_payment|approval_backup|alert_escalation_backup)',
       'limit_amount?:M', 'valid_from:DT', 'valid_to:DT'],
      checks=['valid_to > valid_from'], rules='R2.3,R9.1,R10.4,G6',
      bi="IF app_role() <> 'OWNER' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the owner can delegate authority'; END IF;")
table('authority_delegation_revocation', 1, 'E', 'Early revocation of a delegation.',
      ['authority_delegation_id:B->authority_delegation', 'reason:V300'], uniques=[['authority_delegation_id']], rules='G6')

table('approval_request', 1, 'E', 'Maker side of maker-checker (G6). Carries payload; target row holds the FK.',
      ['request_type:E(discount|free_service|post_finalization_bill_correction|refund|payment_reversal|counter_sale_refund|counter_sale_payment_reversal|stock_adjustment|stock_writeoff|visit_reopen|stay_extension|partial_payment_discharge)',
       'visit_id?:B->visit', 'bill_id?:B->bill', 'stock_batch_id?:B->stock_batch', 'short_stay_episode_id?:B->short_stay_episode',
       'counter_sale_id?:B->counter_sale', 'requested_amount?:M', 'requested_quantity?:Q', 'proposed_change?:V500', 'reason:V300'],
      checks=["CHAR_LENGTH(TRIM(reason)) >= 3",
              "(request_type IN ('discount','free_service','post_finalization_bill_correction','refund','payment_reversal') AND bill_id IS NOT NULL) "
              "OR (request_type = 'partial_payment_discharge' AND bill_id IS NOT NULL AND visit_id IS NOT NULL) "
              "OR (request_type IN ('counter_sale_refund','counter_sale_payment_reversal') AND counter_sale_id IS NOT NULL) "
              "OR (request_type IN ('stock_adjustment','stock_writeoff') AND stock_batch_id IS NOT NULL) "
              "OR (request_type = 'visit_reopen' AND visit_id IS NOT NULL) "
              "OR (request_type = 'stay_extension' AND short_stay_episode_id IS NOT NULL)"],
      rules='R2.3,R5.3,R8.4,R8.5,R9.1,R3.4,G6')
table('approval_decision', 1, 'E', 'Checker side of maker-checker. One final decision per request.',
      ['approval_request_id:B->approval_request', 'decision:E(approved|rejected)', 'decision_channel:E(in_app|phone_remote)=in_app',
       'authority_delegation_id?:B->authority_delegation', 'decision_reason?:V300'],
      uniques=[['approval_request_id']], rules='R2.3,R9.1,G6',
      bi="DECLARE v_req BIGINT UNSIGNED; DECLARE v_kind VARCHAR(10); "
         "SELECT recorded_by INTO v_req FROM approval_request WHERE approval_request_id = NEW.approval_request_id; "
         "SELECT principal_kind INTO v_kind FROM staff WHERE staff_id = NEW.recorded_by; "
         "IF v_kind <> 'human' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'System principals cannot approve'; END IF; "
         "IF v_req = NEW.recorded_by THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'You cannot approve your own request'; END IF; "
         "IF NOT approver_has_authority(NEW.approval_request_id, NEW.recorded_by, NEW.recorded_by_role, NEW.authority_delegation_id, NEW.recorded_at) "
         "THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'You are not allowed to approve this request (owner or valid delegation needed)'; END IF;")

table('audit_log', 1, 'E', 'Insert-only audit of master edits (R10.6) - nobody can change it, not even the owner.',
      ['action:V30', 'entity_type:V64', 'entity_id:V64', 'old_values?:J', 'new_values?:J', 'reason?:V300'],
      indexes=[['entity_type', 'entity_id'], ['recorded_at']], rules='R1.6,R10.6,G2')
table('alert_event', 1, 'E', 'Safety interrupts, approval-pending notices and daily-summary items.',
      ['alert_class:E(safety_interrupt|approval_pending|daily_summary_item)', 'alert_type:V40', 'severity:E(red|amber)',
       'source_entity_type?:V64', 'source_entity_id?:V64', 'summary:V300'],
      indexes=[['alert_class', 'recorded_at'], ['alert_type', 'source_entity_type', 'source_entity_id']], rules='R10.1,R10.2,R10.3')
table('alert_acknowledgement', 1, 'E', 'Someone acknowledged an alert.', ['alert_event_id:B->alert_event', 'note?:V300'], rules='R10.4')
table('alert_escalation', 1, 'E', 'Unanswered interrupt escalated to a backup person.',
      ['alert_event_id:B->alert_event', 'escalated_to_staff_id:B->staff', 'reason:V300'], rules='R10.4')
table('login_session_event', 1, 'E', 'Logins, failures, logouts, role switches (feeds shared-login report).',
      ['staff_id?:B->staff', 'claimed_role_id?:B->role', 'login_identifier?:V60', 'session_id_hash?:H64',
       'event_type:E(login_success|login_failed|logout|role_switch|session_timeout)', 'source_context?:V100'],
      indexes=[['staff_id', 'recorded_at']], rules='R10.5,G4')
table('message_outbox', 1, 'E', 'Simulated SMS/WhatsApp. rendered_text is an EXTENSION (CR-B2) so the screen can show the message.',
      ['message_type:E(family_link|receipt|due_reminder|appointment_reminder|alert_notice)', 'recipient_phone:V20', 'safe_template_key:V40',
       'rendered_text?:V500', 'family_link_id?:B->family_link', 'payment_id?:B->payment', 'counter_sale_payment_id?:B->counter_sale_payment',
       'due_promise_id?:B->due_promise'], rules='R11.3,R8.5,R9.3,G10')
table('message_delivery_event', 1, 'E', 'Delivery state trail of an outbox message.',
      ['message_outbox_id:B->message_outbox', 'delivery_state:E(queued|simulated_sent|failed)', 'details?:V300'], rules='R11.3')
table('backup_run', 1, 'E', 'Backups and restore drills.',
      ['backup_kind:E(full|binlog|restore_drill)', 'encrypted:TI=1', 'offsite_copy_confirmed:TI=0', 'result:E(success|failed)',
       'restored_from_backup_run_id?:B->backup_run'], rules='R12.2')
table('benchmark_run', 1, 'E', 'Evidence for indexing benchmarks.',
      ['dataset_size:I', 'query_name:V80', 'before_plan?:TXT', 'after_plan?:TXT', 'elapsed_ms?:DECIMAL(12,3)', 'index_configuration?:V300'], rules='R12.3')

# =====================================================================
# CLUSTER 2  patients, scheduling, visits, family links
# =====================================================================
ADDR = lambda p: [f'{p}_address_line?:V200', f'{p}_address_locality?:V100', f'{p}_address_city?:V80',
                  f'{p}_address_state?:V60', f'{p}_address_postal_code?:C6']
table('patient', 2, 'M', 'Patient demographics (identity only). Blood group is clinical -> patient_blood_group.',
      ['display_code:V30', 'registration_kind:E(registered|emergency_unknown)=registered', 'given_name:V80', 'middle_name?:V80',
       'family_name?:V80', 'date_of_birth?:D', 'approximate_age?:SI', 'gender:E(male|female|other|unknown)=unknown', 'aadhaar_last4?:C4']
      + ADDR('permanent') + ADDR('temporary'),
      raw=["full_name_search VARCHAR(250) GENERATED ALWAYS AS (LOWER(TRIM(CONCAT_WS(' ', given_name, middle_name, family_name)))) STORED",
           "name_prefix CHAR(3) GENERATED ALWAYS AS (LEFT(LOWER(given_name), 3)) STORED"],
      uniques=[['display_code']], indexes=[['name_prefix'], ['date_of_birth'], ['aadhaar_last4']],
      checks=["aadhaar_last4 IS NULL OR aadhaar_last4 REGEXP '^[0-9]{4}$'",
              "registration_kind = 'emergency_unknown' OR date_of_birth IS NOT NULL",
              "registration_kind = 'registered' OR approximate_age IS NOT NULL OR date_of_birth IS NOT NULL",
              "permanent_address_postal_code IS NULL OR permanent_address_postal_code REGEXP '^[0-9]{6}$'"],
      audit_exclude=['full_name_search', 'name_prefix'], rules='R1.1,R1.2,R1.3,R1.5,R1.6',
      bi="IF NEW.date_of_birth IS NOT NULL AND NEW.date_of_birth > CURRENT_DATE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Date of birth cannot be in the future'; END IF;")
table('patient_phone', 2, 'M', 'Phone history (weak on patient). Phone is never an identifier.',
      ['patient_id:B->patient', 'phone_sequence:SI', 'phone_number:V20', 'valid_from:D=(CURRENT_DATE)', 'valid_to?:D', 'is_primary:TI=1'],
      uniques=[['patient_id', 'phone_sequence']], indexes=[['phone_number']],
      checks=["phone_number REGEXP '^[0-9+ -]{8,20}$'"], rules='R1.2')
table('patient_attendant', 2, 'M', 'Attendants / emergency contacts / family-side recipients.',
      ['patient_id:B->patient', 'name:V120', 'relationship:V40', 'phone?:V20', 'is_emergency_contact:TI=0'], rules='R1.2,R11.4')
table('patient_allergy', 2, 'E', 'Allergies (clinical). Linked to ingredient for combination-drug matching.',
      ['patient_id:B->patient', 'ingredient_id?:B->ingredient', 'allergen_text?:V100', 'reaction?:V200',
       'severity:E(mild|moderate|severe|unknown)=unknown'], corrects=True,
      checks=['ingredient_id IS NOT NULL OR allergen_text IS NOT NULL'], rules='R1.2,R4.4')
table('patient_allergy_status_event', 2, 'E', 'Allergy status trail; latest row = current.',
      ['patient_allergy_id:B->patient_allergy', 'status:E(confirmed|inactive|reactivated|entered_in_error)', 'reason?:V300'], rules='R4.4')
table('patient_blood_group', 2, 'E', 'Blood group is a clinical record, not identity (R1.3).',
      ['patient_id:B->patient', 'abo_group:E(A|B|AB|O)', 'rh_factor:E(positive|negative)',
       'source:E(patient_reported|card_seen|lab_confirmed)', 'lab_report_id?:B->lab_report'], corrects=True, rules='R1.3')
table('patient_merge_log', 2, 'E', 'Audited merge; the redirect for merged-away patients (CR-2: event rows are never rewritten).',
      ['source_patient_id:B->patient', 'surviving_patient_id:B->patient', 'reason:V300', 'suggested_match_tier?:E(T1|T2|T3|T4)'],
      uniques=[['source_patient_id']], checks=['source_patient_id <> surviving_patient_id'], rules='R1.5,G12',
      bi="IF EXISTS (SELECT 1 FROM patient_merge_log WHERE source_patient_id = NEW.surviving_patient_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Survivor patient was itself merged away'; END IF;")

table('doctor_schedule', 2, 'E', 'Versioned weekly schedule; never edited (R3.7).',
      ['doctor_staff_id:B->doctor_detail.staff_id', 'weekday:TN', 'start_time:T', 'end_time:T', 'slot_minutes:SI', 'valid_from:D',
       'schedule_state:E(active|withdrawn)=active', 'supersedes_doctor_schedule_id?:B->doctor_schedule'],
      uniques=[['doctor_schedule_id', 'doctor_staff_id']],
      checks=['weekday BETWEEN 0 AND 6', 'end_time > start_time', 'slot_minutes BETWEEN 5 AND 120'], rules='R3.6,R3.7')
table('appointment_slot', 2, 'E', 'Generated bookable unit; lock anchor + declarative uniqueness for R3.6.',
      ['doctor_schedule_id:B', 'doctor_staff_id:B', 'slot_start:DT0', 'slot_end:DT0'],
      fk_extra=[(['doctor_schedule_id', 'doctor_staff_id'], 'doctor_schedule', ['doctor_schedule_id', 'doctor_staff_id'])],
      uniques=[['doctor_staff_id', 'slot_start']], checks=['slot_end > slot_start'], rules='R3.6')
table('schedule_change_request', 2, 'E', 'Doctor requests a schedule change (R3.7).',
      ['doctor_staff_id:B->doctor_detail.staff_id', 'target_doctor_schedule_id?:B->doctor_schedule',
       'change_kind:E(modify|add_session|withdraw_session)', 'proposed_weekday?:TN', 'proposed_start_time?:T', 'proposed_end_time?:T',
       'proposed_slot_minutes?:SI', 'proposed_valid_from:D', 'reason:V300'],
      checks=["change_kind = 'add_session' OR target_doctor_schedule_id IS NOT NULL"], rules='R3.7')
table('schedule_change_application', 2, 'E', 'An authorized person (not the requester) applies or declines the change.',
      ['schedule_change_request_id:B->schedule_change_request', 'outcome:E(applied|declined)',
       'resulting_doctor_schedule_id?:B->doctor_schedule', 'reason?:V300'],
      uniques=[['schedule_change_request_id'], ['resulting_doctor_schedule_id']],
      checks=["outcome <> 'applied' OR resulting_doctor_schedule_id IS NOT NULL"], rules='R3.7')
table('appointment', 2, 'E', 'Immutable booking header; time comes from the claimed slot.',
      ['patient_id:B->patient', 'doctor_staff_id:B->doctor_detail.staff_id', 'booking_channel:E(reception|phone|walk_in|doctor)=reception'],
      rules='R3.2,R3.6',
      bi="IF patient_is_merged(NEW.patient_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This patient record was merged; use the surviving record'; END IF;")
table('appointment_slot_claim', 2, 'E', 'Appointment x Slot. Trigger repeats the live-claim check (R3.6).',
      ['appointment_id:B->appointment', 'appointment_slot_id:B->appointment_slot', 'claim_reason:E(initial|reschedule)'], rules='R3.6',
      bi="DECLARE v_d1 BIGINT UNSIGNED; DECLARE v_d2 BIGINT UNSIGNED; "
         "SELECT doctor_staff_id INTO v_d1 FROM appointment WHERE appointment_id = NEW.appointment_id; "
         "SELECT doctor_staff_id INTO v_d2 FROM appointment_slot WHERE appointment_slot_id = NEW.appointment_slot_id; "
         "IF v_d1 <> v_d2 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Slot belongs to a different doctor'; END IF; "
         "IF slot_has_live_claim(NEW.appointment_slot_id, NEW.appointment_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Slot already booked for this doctor'; END IF;")
table('appointment_status_history', 2, 'E', 'Weak on appointment. Latest row = current status.',
      ['appointment_id:B->appointment', 'status_seq:SI', 'status:E(booked|checked_in|waiting|in_consultation|completed|cancelled|no_show)', 'reason?:V300'],
      uniques=[['appointment_id', 'status_seq']], rules='R3.2')
table('visit', 2, 'E', 'Immutable parent of everything clinical/financial (R3.1).',
      ['patient_id:B->patient', 'appointment_id?:B->appointment', 'entry_type:E(appointment|walk_in|emergency)'],
      uniques=[['appointment_id']], rules='R3.1',
      bi="IF patient_is_merged(NEW.patient_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This patient record was merged; use the surviving record'; END IF;")
table('visit_doctor_assignment', 2, 'E', 'Attending / covering doctor of a visit.',
      ['visit_id:B->visit', 'doctor_staff_id:B->doctor_detail.staff_id', 'assignment_role:E(attending|covering)=attending', 'reason?:V300'],
      bi=V_CLOSED, rules='R3.3')
table('visit_status_history', 2, 'E', 'Weak on visit. Latest row = current status/location. No beds.',
      ['visit_id:B->visit', 'status_seq:SI', 'status:E(arrived|waiting|with_doctor|lab|pharmacy|billing|discharged)', 'location_label?:V60', 'reason?:V300'],
      uniques=[['visit_id', 'status_seq']], bi=V_CLOSED, rules='R3.3')
table('short_stay_episode', 2, 'E', 'Subtype of visit (S2). Extension/closure are separate events.',
      ['visit_id:B->visit', 'admission_reason:V300'], uniques=[['visit_id'], ['short_stay_episode_id', 'visit_id']],
      bi=V_CLOSED, rules='R3.1,R3.4')
table('short_stay_extension', 2, 'E', 'Approved extension beyond stay_limit_hours (R3.4).',
      ['short_stay_episode_id:B->short_stay_episode', 'approval_request_id:B->approval_request', 'extended_until:DT', 'reason:V300'],
      uniques=[['approval_request_id']], rules='R3.4,G6',
      bi="IF NOT approval_is_valid(NEW.approval_request_id, 'stay_extension') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved extension request is required'; END IF;")
table('short_stay_closure', 2, 'E', 'Ends a short stay.', ['short_stay_episode_id:B->short_stay_episode', 'reason?:V300'], rules='R3.1')
table('vitals_observation', 2, 'E', 'Weak on visit; one typed row per measurement (BP = two rows sharing observation_set_uuid).',
      ['visit_id:B->visit', 'observation_seq:I', 'short_stay_episode_id?:B', 'observation_set_uuid:C36',
       'observation_type:E(bp_systolic|bp_diastolic|pulse|temperature|spo2|resp_rate|weight|height|blood_glucose|other)',
       'numeric_value?:DECIMAL(8,2)', 'text_value?:V100', 'unit?:V20'], corrects=True,
      fk_extra=[(['short_stay_episode_id', 'visit_id'], 'short_stay_episode', ['short_stay_episode_id', 'visit_id'])],
      uniques=[['visit_id', 'observation_seq']], checks=['numeric_value IS NOT NULL OR text_value IS NOT NULL'],
      bi=V_CLOSED, rules='R3.5')
table('family_link', 2, 'E', 'Read-only tokenized link scoped to one visit (R11). Only hashes stored.',
      ['visit_id:B->visit', 'attendant_id?:B->patient_attendant', 'token_hash:H64', 'second_factor_kind:E(date_of_birth|printed_code)',
       'printed_code_hash?:H64', 'expires_at:DT', 'consent_note:V300'],
      uniques=[['token_hash']],
      checks=["(second_factor_kind = 'printed_code') = (printed_code_hash IS NOT NULL)"], rules='R11.1,R11.2,R11.4')
table('family_link_revocation', 2, 'E', 'Instant revocation without touching the link.',
      ['family_link_id:B->family_link', 'reason:V300'], uniques=[['family_link_id']], rules='R11.2')
table('link_access_log', 2, 'E', 'Every attempt, success or not (R11.4). Lockout derived from it.',
      ['family_link_id?:B->family_link', 'token_fingerprint:V16', 'success:TI', 'failure_reason?:V60', 'source_context?:V100'],
      indexes=[['family_link_id', 'recorded_at']], rules='R11.1,R11.4')

# =====================================================================
# CLUSTER 3  pharmacy, custody, counter sales
# =====================================================================
table('ingredient', 3, 'M', 'Active-ingredient master for allergy matching.', ['ingredient_name:V120'], uniques=[['ingredient_name']], rules='R4.4')
table('medicine', 3, 'M', 'Medicine catalog (defaults are only suggestions).',
      ['brand_name:V120', 'generic_display_name:V160', 'strength_value?:N3', 'strength_unit?:V20', 'dosage_form:V40', 'stock_unit:V20',
       'schedule_class:E(none|H|H1|X)=none', 'default_dose_text?:V60', 'default_schedule?:V30', 'default_food_instruction?:V60',
       'default_duration_days?:SI', 'returnable:TI=0', 'active:TI=1'],
      indexes=[['brand_name'], ['generic_display_name']], uniques=[['brand_name', 'strength_value', 'strength_unit', 'dosage_form']],
      rules='R4.2,R4.3,R5.5,R6.3')
table('medicine_ingredient', 3, 'M', 'Medicine x Ingredient (combination drugs).',
      ['medicine_id:B->medicine', 'ingredient_id:B->ingredient', 'strength_text?:V60'], pk=['medicine_id', 'ingredient_id'], rules='R4.4')
table('supplier', 3, 'M', 'Minimal supplier (no accounting).', ['name:V120', 'phone?:V20', 'contact_name?:V80', 'notes?:V300'], rules='R5.1')
table('stock_batch', 3, 'M', 'A batch of a medicine. Quantity is derived from stock_movement. Cost price owner/pharmacy only.',
      ['medicine_id:B->medicine', 'supplier_id?:B->supplier', 'batch_code:V40', 'expiry_date:D', 'cost_price:M', 'mrp:M'],
      uniques=[['medicine_id', 'batch_code']], checks=['cost_price >= 0', 'mrp > 0'], rules='R5.1,R5.4,G10')
table('stock_movement', 3, 'E', 'Append-only stock ledger (R5.2). Price snapshot per stock-out movement.',
      ['stock_batch_id:B->stock_batch',
       'movement_type:E(received|dispensed|returned_by_patient|returned_to_supplier|expired_writeoff|adjustment|reversal)',
       'quantity_delta:DECIMAL(10,2)', 'unit_price_snapshot?:M', 'dispensing_id?:B->dispensing',
       'returns_stock_movement_id?:B->stock_movement', 'reverses_stock_movement_id?:B->stock_movement', 'reason?:V300',
       'approval_request_id?:B->approval_request'],
      uniques=[['approval_request_id']], indexes=[['stock_batch_id', 'recorded_at']],
      checks=['quantity_delta <> 0',
              "(movement_type IN ('received','returned_by_patient') AND quantity_delta > 0) OR (movement_type IN ('dispensed','returned_to_supplier','expired_writeoff') AND quantity_delta < 0) OR movement_type IN ('adjustment','reversal')",
              "movement_type NOT IN ('adjustment','expired_writeoff','reversal','returned_to_supplier') OR (reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason)) >= 3)",
              "movement_type NOT IN ('dispensed','returned_by_patient') OR unit_price_snapshot IS NOT NULL",
              "(movement_type = 'dispensed') = (dispensing_id IS NOT NULL)",
              "(movement_type = 'returned_by_patient') = (returns_stock_movement_id IS NOT NULL)",
              "(movement_type = 'reversal') = (reverses_stock_movement_id IS NOT NULL)",
              "movement_type NOT IN ('adjustment','expired_writeoff') OR approval_request_id IS NOT NULL"],
      rules='R5.2,R5.3,R5.4,G3',
      bi="DECLARE v_onhand DECIMAL(12,2); DECLARE v_exp DATE; DECLARE v_mrp DECIMAL(12,2); DECLARE v_med BIGINT UNSIGNED; DECLARE v_dmed BIGINT UNSIGNED; "
         "SELECT expiry_date, mrp, medicine_id INTO v_exp, v_mrp, v_med FROM stock_batch WHERE stock_batch_id = NEW.stock_batch_id FOR UPDATE; "
         "IF NEW.movement_type = 'dispensed' THEN "
         "  IF v_exp < CURRENT_DATE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Expired batch cannot be dispensed'; END IF; "
         "  SELECT medicine_id INTO v_dmed FROM dispensing WHERE dispensing_id = NEW.dispensing_id; "
         "  IF v_dmed <> v_med THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Batch belongs to a different medicine'; END IF; "
         "  IF NEW.unit_price_snapshot <> v_mrp THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Dispensed price must equal the batch MRP'; END IF; "
         "END IF; "
         "IF NEW.movement_type IN ('adjustment','expired_writeoff') AND NOT approval_is_valid(NEW.approval_request_id, 'stock_adjustment,stock_writeoff') "
         "THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved adjustment/write-off request is required'; END IF; "
         "SELECT COALESCE(SUM(quantity_delta),0) INTO v_onhand FROM stock_movement WHERE stock_batch_id = NEW.stock_batch_id; "
         "IF v_onhand + NEW.quantity_delta < 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not enough stock in this batch'; END IF;")
table('counter_customer', 3, 'M', 'Counter buyer; NOT a patient (R5.6).', ['phone?:V20', 'temporary_code?:V30', 'display_name?:V120'],
      uniques=[['temporary_code']], checks=['phone IS NOT NULL OR temporary_code IS NOT NULL'], rules='R5.6')
table('counter_customer_link_event', 3, 'E', 'Audited, human-confirmed link of a counter customer to a patient.',
      ['counter_customer_id:B->counter_customer', 'patient_id:B->patient', 'reason:V300'], rules='R5.6')
table('outside_prescription', 3, 'E', 'Prescription written elsewhere; has a buyer (counter customer).',
      ['counter_customer_id:B->counter_customer', 'prescriber_name:V120', 'prescriber_registration_no?:V40', 'photo_path?:V300', 'prescription_date:D'],
      rules='R5.5,R5.8')
table('counter_sale', 3, 'E', 'Header for every pharmacy sale to a counter customer (OTC or outside Rx).',
      ['counter_customer_id:B->counter_customer', 'sale_number:V30'], uniques=[['sale_number']], rules='R5.5,R5.7')
table('counter_sale_payment', 3, 'E', 'Pharmacy records payments for counter sales only (R5.7). Reversal = row with corrects_id + approval.',
      ['counter_sale_id:B->counter_sale', 'amount:M', 'method:E(cash|upi|card)', 'receipt_number:V30', 'approval_request_id?:B->approval_request'],
      corrects=True, uniques=[['receipt_number'], ['approval_request_id']], checks=['amount > 0', '(corrects_id IS NULL) = (approval_request_id IS NULL)'], rules='R5.7,R8.5',
      bi="IF NEW.corrects_id IS NOT NULL AND NOT approval_is_valid(NEW.approval_request_id, 'counter_sale_payment_reversal') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved reversal request is required'; END IF;")
table('counter_sale_return', 3, 'E', 'Stock side of a counter-sale return.',
      ['counter_sale_id:B->counter_sale', 'returned_stock_movement_id:B->stock_movement', 'reason:V300'],
      uniques=[['returned_stock_movement_id']], rules='R5.7')
table('counter_sale_refund', 3, 'E', 'Money side of a counter-sale return; owner approval mandatory (CR-3).',
      ['counter_sale_id:B->counter_sale', 'counter_sale_return_id?:B->counter_sale_return', 'amount:M', 'method:E(cash|upi|card)',
       'receipt_number:V30', 'approval_request_id:B->approval_request'],
      corrects=True, uniques=[['receipt_number'], ['approval_request_id']], checks=['amount > 0'], rules='R5.7,G6',
      bi="IF NOT approval_is_valid(NEW.approval_request_id, 'counter_sale_refund') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved refund request is required'; END IF;")
table('dispensing', 3, 'E', 'Header of one dispensing act (quantities live on stock-out movements). Exactly one source (S3).',
      ['source_type:E(clinic_prescription|outside_prescription|otc)', 'medicine_id:B->medicine', 'prescription_item_id?:B->prescription_item',
       'outside_prescription_id?:B->outside_prescription', 'counter_sale_id?:B->counter_sale'], corrects=True,
      checks=["(source_type='clinic_prescription' AND prescription_item_id IS NOT NULL AND outside_prescription_id IS NULL AND counter_sale_id IS NULL) "
              "OR (source_type='outside_prescription' AND prescription_item_id IS NULL AND outside_prescription_id IS NOT NULL AND counter_sale_id IS NOT NULL) "
              "OR (source_type='otc' AND prescription_item_id IS NULL AND outside_prescription_id IS NULL AND counter_sale_id IS NOT NULL)"],
      rules='R5.5,R5.4',
      bi="DECLARE v_sched VARCHAR(4); DECLARE v_active TINYINT; "
         "SELECT schedule_class, active INTO v_sched, v_active FROM medicine WHERE medicine_id = NEW.medicine_id; "
         "IF v_active = 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This medicine is inactive in the catalog'; END IF; "
         "IF NEW.source_type = 'otc' AND v_sched <> 'none' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Schedule H/H1/X medicines cannot be sold over the counter'; END IF;")
table('order_item_supply', 3, 'E', 'Order item x Dispensing: marks a dispensing as order-linked (R6.2 scope).',
      ['clinical_order_item_id:B->clinical_order_item', 'dispensing_id:B->dispensing', 'quantity_allocated:Q',
       'reverses_order_item_supply_id?:B->order_item_supply'],
      uniques=[['order_item_supply_id', 'clinical_order_item_id']], checks=['quantity_allocated > 0'], rules='R2.5,R6.2')
table('custody_handoff', 3, 'E', 'Ternary: dispensing x giver x recipient (staff or attendant). Giver records (G1).',
      ['dispensing_id:B->dispensing', 'giver_staff_id:B->staff', 'recipient_staff_id?:B->staff', 'recipient_attendant_id?:B->patient_attendant',
       'quantity:Q', 'handoff_purpose:E(supply_to_nurse|take_home|leftover_to_family)', 'leftover_return_decision_id?:B->leftover_return_decision'],
      checks=['quantity > 0', 'giver_staff_id = recorded_by',
              '(recipient_staff_id IS NULL) <> (recipient_attendant_id IS NULL)',
              "(handoff_purpose = 'leftover_to_family') = (leftover_return_decision_id IS NOT NULL)"], rules='R2.5,R6.2,R6.3')
table('custody_handoff_acknowledgement', 3, 'E', 'Recipient side (G1). Witness must differ from giver (CR-4).',
      ['custody_handoff_id:B->custody_handoff', 'acknowledged_quantity:Q', 'ack_method:E(self_logged_staff|witnessed_attendant)', 'witness_note?:V300'],
      uniques=[['custody_handoff_id']], checks=['acknowledged_quantity >= 0'], rules='R2.5,R6.2',
      bi="DECLARE v_giver BIGINT UNSIGNED; SELECT giver_staff_id INTO v_giver FROM custody_handoff WHERE custody_handoff_id = NEW.custody_handoff_id; "
         "IF v_giver = NEW.recorded_by THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The giver cannot acknowledge their own handoff'; END IF;")
table('leftover_return_request', 3, 'E', 'Nurse asks pharmacy to take leftover medicine back (R6.3).',
      ['dispensing_id:B->dispensing', 'quantity:Q', 'condition_note?:V300'], checks=['quantity > 0'], rules='R6.3')
table('leftover_return_decision', 3, 'E', 'Pharmacist decides (second actor).',
      ['leftover_return_request_id:B->leftover_return_request', 'decision:E(accepted|refused)', 'decision_reason:V300',
       'stock_movement_id?:B->stock_movement'],
      uniques=[['leftover_return_request_id'], ['stock_movement_id']],
      checks=["(decision = 'accepted') = (stock_movement_id IS NOT NULL)"], rules='R6.3')
table('cash_handover_declaration', 3, 'E', 'Pharmacist declares cash being handed to accountant (R5.7).',
      ['period_start:DT', 'period_end:DT', 'declared_amount:M', 'note?:V300'], checks=['declared_amount >= 0', 'period_end > period_start'], rules='R5.7')
table('cash_handover_count', 3, 'E', 'Accountant counts the cash.',
      ['cash_handover_declaration_id:B->cash_handover_declaration', 'counted_amount:M', 'note?:V300'],
      uniques=[['cash_handover_declaration_id']], checks=['counted_amount >= 0'], rules='R5.7',
      bi="DECLARE v_dec BIGINT UNSIGNED; SELECT recorded_by INTO v_dec FROM cash_handover_declaration WHERE cash_handover_declaration_id = NEW.cash_handover_declaration_id; "
         "IF v_dec = NEW.recorded_by THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Declarer and counter must be different people'; END IF;")

# =====================================================================
# CLUSTER 4  clinical orders, labs, billing, discharge
# =====================================================================
table('clinical_note', 4, 'E', 'Doctor notes; correction = amendment row.',
      ['visit_id:B->visit', 'note_text:TXT', 'sharing_classification:E(shareable|not_for_sharing)=not_for_sharing'], corrects=True, rules='R1.6,R11.3')
table('diagnosis', 4, 'E', 'Diagnosis (hidden from reception views).',
      ['visit_id:B->visit', 'diagnosis_text:V300', 'diagnosis_code?:V20'], corrects=True, rules='R2.2')
table('prescription', 4, 'E', 'Immutable root of all prescription versions.', ['visit_id:B->visit'], bi=V_CLOSED, rules='R4.1')
table('prescription_version', 4, 'E', 'One row per version; each is a complete snapshot of all lines.',
      ['prescription_id:B->prescription', 'version_number:SI', 'change_reason?:V300'],
      uniques=[['prescription_id', 'version_number'], ['prescription_version_id', 'prescription_id']], rules='R4.1')
table('prescription_line', 4, 'E', 'Weak on prescription: stable lineage anchor of one prescribed medicine across versions.',
      ['prescription_id:B->prescription', 'line_no:SI'],
      uniques=[['prescription_id', 'line_no'], ['prescription_line_id', 'prescription_id']], rules='R4.1,R4.6')
table('prescription_item', 4, 'E', 'Version x Line. Stores what the doctor chose (R4.3).',
      ['prescription_version_id:B', 'prescription_line_id:B', 'prescription_id:B', 'item_change:E(added|modified|unchanged|discontinued)',
       'medicine_id?:B->medicine', 'free_text_medicine_name?:V150', 'chosen_dose_text?:V60', 'chosen_schedule?:V30',
       'food_instruction?:V60', 'duration_days?:SI', 'quantity_prescribed?:Q'],
      fk_extra=[(['prescription_version_id', 'prescription_id'], 'prescription_version', ['prescription_version_id', 'prescription_id']),
                (['prescription_line_id', 'prescription_id'], 'prescription_line', ['prescription_line_id', 'prescription_id'])],
      uniques=[['prescription_version_id', 'prescription_line_id']],
      checks=['(medicine_id IS NULL) <> (free_text_medicine_name IS NULL)'], rules='R4.1,R4.2,R4.3,R4.7')
table('prescription_line_status', 4, 'E', 'Line status (prescribed / bought_outside / ...). Latest = current (R4.6).',
      ['prescription_line_id:B->prescription_line', 'status:E(prescribed|bought_outside|not_needed|cancelled)', 'reason?:V300'], rules='R4.6')
table('allergy_confirmation', 4, 'E', 'Snapshot of allergies shown to the doctor (R4.4). Override = safety interrupt.',
      ['prescription_item_id:B->prescription_item', 'confirmation_type:E(free_text_medicine|generic_name_warning)',
       'displayed_allergies_snapshot:V1000', 'was_overridden:TI=0', 'override_reason?:V300'],
      checks=['was_overridden = 0 OR override_reason IS NOT NULL'], rules='R4.4,R10.2')
table('catalog_review_queue', 4, 'E', 'Free-text medicine names awaiting mapping (R4.5).',
      ['prescription_item_id:B->prescription_item', 'submitted_name:V150'], uniques=[['prescription_item_id']], rules='R4.5')
table('catalog_review_event', 4, 'E', 'Resolution of a queued name. Latest = outcome.',
      ['catalog_review_queue_id:B->catalog_review_queue', 'review_action:E(mapped|added_to_catalog|rejected)',
       'resolved_medicine_id?:B->medicine', 'resolution_note?:V300'], rules='R4.5')
table('clinical_order', 4, 'E', 'Doctor-authored order header for short stay (R4.7).',
      ['visit_id:B->visit', 'short_stay_episode_id?:B', 'order_type:E(medication|procedure|observation|iv_fluid)'],
      fk_extra=[(['short_stay_episode_id', 'visit_id'], 'short_stay_episode', ['short_stay_episode_id', 'visit_id'])], bi=V_CLOSED, rules='R4.7')
table('clinical_order_status', 4, 'E', 'Order lifecycle; nurses see finalized orders.',
      ['clinical_order_id:B->clinical_order', 'status:E(ordered|finalized|cancelled|completed)', 'reason?:V300'], rules='R4.7')
table('clinical_order_item', 4, 'E', 'Weak on order; a change is a new item superseding the old.',
      ['clinical_order_id:B->clinical_order', 'item_no:SI', 'item_kind:E(medication|procedure|observation)', 'medicine_id?:B->medicine',
       'free_text_medicine_name?:V150', 'price_list_id?:B->price_list', 'dose_value?:N3', 'dose_unit?:V20', 'route?:V30',
       'first_scheduled_at?:DT', 'repeat_interval_minutes?:I', 'quantity_ordered?:Q', 'instructions?:V300',
       'supersedes_clinical_order_item_id?:B->clinical_order_item'],
      uniques=[['clinical_order_id', 'item_no']], rules='R4.7,R6.1')
table('clinical_order_item_status', 4, 'E', 'Item stop/change by the doctor. Latest = current.',
      ['clinical_order_item_id:B->clinical_order_item', 'status:E(active|stopped|superseded)', 'reason?:V300'], rules='R4.7')
table('dose_administration', 4, 'E', 'Nurse records each dose; ordered vs actual stored (R6.1).',
      ['clinical_order_item_id:B->clinical_order_item', 'order_item_supply_id?:B', 'status:E(given|missed|refused|delayed|stopped)',
       'ordered_dose_value?:N3', 'ordered_dose_unit?:V20', 'ordered_route?:V30', 'actual_dose_value?:N3', 'actual_dose_unit?:V20',
       'actual_route?:V30', 'quantity_consumed:Q=0', 'deviation_reason?:V300', 'deviation_authorized_by?:B->staff'],
      corrects=True,
      fk_extra=[(['order_item_supply_id', 'clinical_order_item_id'], 'order_item_supply', ['order_item_supply_id', 'clinical_order_item_id'])],
      checks=['quantity_consumed >= 0', "quantity_consumed = 0 OR (status = 'given' AND order_item_supply_id IS NOT NULL)",
              "status = 'given' OR deviation_reason IS NOT NULL OR corrects_id IS NOT NULL"],
      rules='R6.1,R6.2,R10.2')
table('clinical_signoff', 4, 'E', 'Doctor sign-off + discharge instructions (G1).',
      ['visit_id:B->visit', 'discharge_instructions?:TXT'], corrects=True, rules='R9.4')
table('clinical_emergency_override', 4, 'E', 'Logged override of the financial gate (R9.2). Raises an interrupt.',
      ['visit_id:B->visit', 'override_type:E(financial_gate_bypass)', 'authorized_by_staff_id:B->staff', 'clinical_justification:V500'],
      rules='R9.2,R10.2')
table('visit_closure', 4, 'E', 'Closing never mutates Visit. Reopen = reversal row with approval.',
      ['visit_id:B->visit', 'closure_kind:E(discharged|administrative)', 'reverses_visit_closure_id?:B->visit_closure',
       'approval_request_id?:B->approval_request', 'reason?:V300'],
      uniques=[['approval_request_id']], checks=['(reverses_visit_closure_id IS NULL) = (approval_request_id IS NULL)'], rules='R9.4,G6',
      bi="IF NEW.reverses_visit_closure_id IS NOT NULL AND NOT approval_is_valid(NEW.approval_request_id, 'visit_reopen') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved reopen request is required'; END IF;")
table('discharge', 4, 'E', 'One transaction closes visit, stay and bill (R9.4).',
      ['visit_id:B->visit', 'clinical_signoff_id:B->clinical_signoff', 'financial_gate_basis:E(bill_settled|approved_partial_payment|emergency_override)',
       'approval_request_id?:B->approval_request', 'clinical_emergency_override_id?:B->clinical_emergency_override'],
      uniques=[['visit_id'], ['clinical_emergency_override_id'], ['approval_request_id']],
      checks=["(financial_gate_basis = 'approved_partial_payment') = (approval_request_id IS NOT NULL)",
              "(financial_gate_basis = 'emergency_override') = (clinical_emergency_override_id IS NOT NULL)"],
      rules='R9.1,R9.2,R9.4',
      bi="IF NEW.approval_request_id IS NOT NULL AND NOT approval_is_valid(NEW.approval_request_id, 'partial_payment_discharge') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved partial-payment request is required'; END IF;")

table('outside_lab', 4, 'M', 'Minimal outside-lab master (no in-house lab in V1).', ['name:V120', 'phone?:V20', 'contact_name?:V80', 'notes?:V300'], rules='R7.1')
table('lab_order', 4, 'E', 'Doctor-authored lab order. Billing/status live elsewhere.',
      ['visit_id:B->visit', 'outside_lab_id:B->outside_lab', 'clinical_order_id?:B->clinical_order', 'test_name:V150'], bi=V_CLOSED, rules='R7.1')
table('lab_order_status', 4, 'E', 'Order lifecycle steps only; report/review facts live on their own tables.',
      ['lab_order_id:B->lab_order', 'status:E(ordered|sample_collected|sent_to_lab|cancelled)', 'reason?:V300'], rules='R7.2')
table('lab_order_price', 4, 'E', 'Per-test price with deviation flag (R8.1).',
      ['lab_order_id:B->lab_order', 'entered_price:M', 'price_basis:E(price_list|previous_price|manual)', 'expected_price?:M',
       'is_price_deviation:TI=0', 'deviation_reason?:V300'], corrects=True,
      checks=['entered_price >= 0', 'is_price_deviation = 0 OR deviation_reason IS NOT NULL'], rules='R8.1,R7.5')
table('lab_report', 4, 'E', 'Report stored as received with hash; correction = new version (R7.3).',
      ['lab_order_id:B->lab_order', 'version_number:SI', 'supersedes_lab_report_id?:B->lab_report', 'file_path:V300', 'content_hash:H64',
       'source:E(from_lab|brought_by_patient)'], uniques=[['lab_order_id', 'version_number']],
      checks=["content_hash REGEXP '^[0-9a-f]{64}$'"], rules='R7.3,R7.6')
table('lab_report_identifier_check', 4, 'E', 'Identifiers confirmed against the order (>= 2 distinct kinds).',
      ['lab_report_id:B->lab_report', 'identifier_kind:E(patient_name|date_of_birth|patient_code|phone_last4|age_gender)',
       'value_seen:V120', 'matches_order:TI'], rules='R7.3,R10.2')
table('lab_report_critical_mark', 4, 'E', 'Marks a report critical (R10.2). Latest wins.',
      ['lab_report_id:B->lab_report', 'mark:E(critical|not_critical)', 'mark_source:E(lab_stamp|nurse_observation|doctor_assessment)', 'reason?:V300'], rules='R10.2')
table('lab_report_review', 4, 'E', 'Doctor reviewed (own event, R7.4).', ['lab_report_id:B->lab_report', 'review_note?:V500'],
      uniques=[['lab_report_id']], rules='R7.4')
table('lab_report_retraction', 4, 'E', 'Wrong-patient/order retraction.',
      ['lab_report_id:B->lab_report', 'retraction_kind:E(wrong_patient|wrong_order|corrupt_file|superseded_by_lab)',
       'correct_lab_order_id?:B->lab_order', 'note?:V300'], uniques=[['lab_report_id']], rules='R10.2')

table('price_list', 4, 'M', 'Owner-only effective-dated price list (R8.1).',
      ['item_code:V30', 'description:V200', 'category:E(consultation|stay|procedure|injection|drip|other)', 'price:M', 'effective_from:D',
       'effective_to?:D', 'active:TI=1'], uniques=[['item_code', 'effective_from']],
      checks=['price >= 0', 'effective_to IS NULL OR effective_to >= effective_from'], rules='R8.1',
      bi="IF app_role() <> 'OWNER' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the owner can change the price list'; END IF;",
      bu="IF app_role() <> 'OWNER' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the owner can change the price list'; END IF;")
table('bill', 4, 'E', 'Immutable bill header; one bill per visit in V1.', ['visit_id:B->visit', 'bill_number:V30'],
      uniques=[['visit_id'], ['bill_number']], rules='R8.2,R8.3')
table('bill_line', 4, 'E', 'Weak on bill. Price snapshot; generated from events (R8.2). line_amount is a generated column.',
      ['bill_id:B->bill', 'line_no:SI',
       'line_type:E(price_list_item|medicine|lab_test|stay|discount|free_service|return_credit|post_finalization_credit|post_finalization_debit|manual)',
       'price_list_id?:B->price_list', 'source_stock_movement_id?:B->stock_movement', 'lab_order_id?:B->lab_order',
       'short_stay_episode_id?:B->short_stay_episode', 'description_snapshot:V200', 'quantity:Q', 'unit_price_snapshot:M',
       'price_source:E(price_list|batch_mrp|lab_entered|manual)', 'is_manual_price:TI=0', 'expected_unit_price?:M',
       'is_price_deviation:TI=0', 'reason?:V300', 'reverses_bill_line_id?:B->bill_line', 'approval_request_id?:B->approval_request'],
      raw=['line_amount DECIMAL(14,2) GENERATED ALWAYS AS (ROUND(quantity * unit_price_snapshot, 2)) STORED'],
      uniques=[['bill_id', 'line_no'], ['source_stock_movement_id'], ['approval_request_id']], indexes=[['lab_order_id']],
      checks=['quantity > 0', 'is_manual_price = 0 OR (reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason)) >= 3)',
              "(line_type IN ('discount','free_service','return_credit','post_finalization_credit') AND unit_price_snapshot <= 0) OR (line_type NOT IN ('discount','free_service','return_credit','post_finalization_credit') AND unit_price_snapshot >= 0)",
              "line_type NOT IN ('discount','free_service','post_finalization_credit','post_finalization_debit') OR approval_request_id IS NOT NULL"],
      audit_exclude=['line_amount'], rules='R8.2,R8.3,R8.4,R6.3',
      bi="DECLARE v_fin INT; SELECT COUNT(*) INTO v_fin FROM bill_finalization WHERE bill_id = NEW.bill_id; "
         "IF v_fin > 0 AND NEW.line_type NOT IN ('post_finalization_credit','post_finalization_debit','return_credit') THEN "
         "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Bill is finalized; only approved credit/debit corrections are allowed'; END IF; "
         "IF NEW.line_type IN ('discount','free_service') AND NOT approval_is_valid(NEW.approval_request_id, 'discount,free_service') THEN "
         "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved discount request is required'; END IF; "
         "IF NEW.line_type IN ('post_finalization_credit','post_finalization_debit') AND NOT approval_is_valid(NEW.approval_request_id, 'post_finalization_bill_correction') THEN "
         "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved correction request is required'; END IF;")
table('bill_finalization', 4, 'E', 'Finalizes a bill (Draft -> Finalized).', ['bill_id:B->bill', 'reason?:V300'], uniques=[['bill_id']], rules='R8.3')
table('payment', 4, 'E', 'Accountant-recorded payment. Completed payments are corrected only by an approved reversal row.',
      ['bill_id:B->bill', 'payment_kind:E(payment|reversal)=payment', 'amount:M', 'method:E(cash|upi|card)', 'receipt_number:V30',
       'approval_request_id?:B->approval_request'], corrects=True, uniques=[['receipt_number']], indexes=[['bill_id']],
      checks=['amount > 0', "(payment_kind = 'reversal') = (approval_request_id IS NOT NULL)"], rules='R8.5,G6',
      bi="IF NEW.payment_kind = 'reversal' AND NOT approval_is_valid(NEW.approval_request_id, 'payment_reversal') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved reversal request is required'; END IF;")
table('refund', 4, 'E', 'Approved refund. Balance = total - payments + refunds.',
      ['bill_id:B->bill', 'payment_id?:B->payment', 'credit_bill_line_id?:B->bill_line', 'amount:M', 'method:E(cash|upi|card)',
       'receipt_number:V30', 'approval_request_id:B->approval_request'], corrects=True,
      uniques=[['receipt_number'], ['approval_request_id']], checks=['amount > 0'], rules='R8.5,R6.3,G6',
      bi="IF NOT approval_is_valid(NEW.approval_request_id, 'refund') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An approved refund request is required'; END IF;")
table('due_promise', 4, 'E', 'Persists the promised-by date (R9.1/R9.3). Latest wins.',
      ['visit_id:B->visit', 'bill_id:B->bill', 'promised_by_date:D', 'approval_request_id?:B->approval_request', 'reason?:V300'], rules='R9.1,R9.3')

# =====================================================================
# Seeds appended to cluster files (executed BEFORE triggers exist)
# =====================================================================
SEEDS = {1: """
INSERT INTO role (role_code, role_name) VALUES
 ('OWNER','Owner/Admin'),('DOCTOR','Doctor'),('RECEPTIONIST','Receptionist'),('NURSE','Nurse'),
 ('PHARMACY','Pharmacy'),('ACCOUNTANT','Accountant'),('SYSTEM','System principal');
INSERT INTO staff (staff_id, display_code, principal_kind, given_name, family_name) VALUES
 (1,'SYS-SCHEDULER','system','System','Scheduler'),(2,'SYS-ALERT','system','System','Alert'),
 (3,'SYS-FAMILY','system','System','Family link'),(4,'SYS-AUTH','system','System','Authentication');
INSERT INTO staff_role (staff_id, role_id, granted_by) SELECT s.staff_id, r.role_id, 1 FROM staff s JOIN role r ON r.role_code='SYSTEM' WHERE s.principal_kind='system';
INSERT INTO clinic_settings (setting_key, setting_value, value_type, description) VALUES
 ('clinic_name','Demo Clinic','text','Shown on printouts and SMS'),
 ('clinic_code','CLN1','text','Prefix of display codes'),
 ('timezone','Asia/Kolkata','text','Display time zone (storage is UTC)'),
 ('currency','INR','text','Currency code'),
 ('stay_limit_hours','24','int','Short-stay limit before the owner is alerted (R3.4)'),
 ('expiry_alert_days','60','int','Batches this close to expiry go on the owner list (R5.4)'),
 ('link_expiry_days','30','int','Family link lifetime (R11.2)'),
 ('link_max_failed_attempts','5','int','Failures before lockout (R11.1)'),
 ('link_lock_base_minutes','1','int','First lockout delay; doubles per extra failure'),
 ('doctor_discount_limit','500','decimal','Default discount a delegated doctor may approve'),
 ('owner_ack_timeout_minutes','15','int','Escalate unacknowledged interrupts after this (R10.4)'),
 ('report_review_limit_hours','24','int','Reports unreviewed past this raise alerts (R7.4)'),
 ('billed_no_report_days','7','int','Billed lab test without report after N days (R7.5)'),
 ('backfill_threshold_minutes','1440','int','Older occurred_at must be flagged is_backfilled (G7)'),
 ('owner_phone','9000000001','phone','Simulated owner SMS number'),
 ('backup_owner_phone','9000000002','phone','Escalation number'),
 ('public_base_url','http://localhost:8000','text','Base URL used in family-link SMS'),
 ('alert_dedupe_hours','24','int','Do not repeat the same alert inside this window'),
 ('handoff_ack_limit_hours','4','int','Unacknowledged custody handoffs older than this are flagged (N-1)'),
 ('lab_price_deviation_pct','10','int','Lab price deviation threshold in percent (N-1)'),
 ('schedule_horizon_days','30','int','How many days of appointment slots are generated (N-1)'),
 ('otc_daily_alert_count','10','int','OTC sales per pharmacist per day that turn amber'),
 ('retention_years','7','int','Record retention period (to be confirmed before selling, R12.4)');
INSERT INTO id_counter (counter_name, next_value, display_prefix) VALUES
 ('patient',1,'{CLINIC}-'),('emergency_patient',1,'TMP-'),('staff',1,'{CLINIC}-S'),('bill',1,'{CLINIC}-B'),
 ('receipt',1,'{CLINIC}-R'),('counter_sale',1,'{CLINIC}-C'),('counter_customer',1,'{CLINIC}-K');
"""}
