# DB contract: stored procedures the Phase 5 web app calls

Generated from `app/contract.py` by `tools/p5_gen_contract_doc.py`. Do not edit by hand.

**How matching works:** the web app finds the procedure by the first name that exists in MySQL, then passes arguments **by parameter name** (leading `p_` ignored, common synonyms handled). Missing parameters get NULL. `request_uuid` is auto-filled. OUT parameters are read back by name.

A DB-LOGIC task can either implement these exact names/arguments, or the contract can be changed to match the real procedures.

## P3-1

| Key | Procedure (candidates) | Arguments the web app sends | OUT read back | Notes |
|---|---|---|---|---|
| `raise_alert` (FIXED in TASKS.md) | `raise_alert` | type, severity, ref_table, ref_id, message | - |  |
| `queue_message` (FIXED in TASKS.md) | `queue_message` | to_phone, template, body, ref_table, ref_id | - |  |
| `request_approval` (FIXED in TASKS.md) | `request_approval` | type, ref_table, ref_id, reason, amount | approval_id | type values: see constants.APPROVAL_TYPES |
| `decide_approval` (FIXED in TASKS.md) | `decide_approval` | approval_id, decision, note | - | decision APPROVED | REJECTED |
| `next_code` (FIXED in TASKS.md) | `next_code` | kind | code |  |
| `create_staff` | `create_staff` / `sp_create_staff` | given_name, family_name, phone, login_subject, roles, registration_number | staff_id | roles = comma separated role codes |

## P3-2

| Key | Procedure (candidates) | Arguments the web app sends | OUT read back | Notes |
|---|---|---|---|---|
| `register_patient` | `register_patient` / `sp_register_patient` | given_name, middle_name, family_name, date_of_birth, gender, phone, aadhaar_last4, address_line, locality, city, state, postal_code, attendant_name, attendant_relationship, attendant_phone, request_uuid | patient_id, display_code |  |
| `find_candidates` | `find_patient_candidates` | given_name, family_name, date_of_birth, phone, aadhaar_last4, address_line | - | returns rows: patient_id, display_code, name, tier, score (names may vary) |
| `register_unknown` | `register_unknown_patient` | gender, approximate_age, request_uuid | patient_id, display_code |  |
| `merge_patients` | `merge_patients` | source_patient_id, surviving_patient_id, reason, suggested_match_tier, request_uuid | - |  |
| `add_phone` | `add_patient_phone` / `sp_add_patient_phone` | patient_id, phone, is_primary, request_uuid | - |  |
| `add_attendant` | `add_patient_attendant` / `sp_add_patient_attendant` | patient_id, name, relationship, phone, is_emergency_contact | - |  |
| `generate_slots` | `generate_slots` / `generate_appointment_slots` / `sp_generate_slots` | doctor_staff_id, from_date, to_date | - |  |
| `book_appointment` | `book_appointment` / `sp_book_appointment` | patient_id, doctor_staff_id, appointment_slot_id, booking_channel, request_uuid | appointment_id |  |
| `appointment_status` | `set_appointment_status` / `change_appointment_status` / `appointment_set_status` | appointment_id, status, reason, request_uuid | - |  |
| `reschedule` | `reschedule_appointment` / `sp_reschedule_appointment` | appointment_id, appointment_slot_id, reason, request_uuid | - |  |
| `request_schedule_change` | `request_schedule_change` | doctor_staff_id, change_kind, target_doctor_schedule_id, weekday, start_time, end_time, slot_minutes, valid_from, reason | - |  |
| `apply_schedule_change` | `apply_schedule_change` / `decide_schedule_change` | schedule_change_request_id, outcome, reason | - | outcome applied | declined |
| `start_visit` | `start_visit` / `sp_create_visit` / `create_visit` | patient_id, appointment_id, entry_type, doctor_staff_id, request_uuid | visit_id | entry_type appointment | walk_in | emergency |
| `visit_status` | `add_visit_status` / `set_visit_status` | visit_id, status, location_label, reason, request_uuid | - |  |
| `assign_doctor` | `assign_visit_doctor` / `add_visit_doctor_assignment` | visit_id, doctor_staff_id, assignment_role, reason | - |  |
| `start_short_stay` | `start_short_stay` / `start_short_stay_episode` | visit_id, admission_reason, request_uuid | short_stay_episode_id |  |
| `stay_extension` | `request_stay_extension` / `extend_short_stay` / `request_short_stay_extension` | short_stay_episode_id, extended_until, reason, approval_request_id | - |  |
| `close_stay` | `close_short_stay` / `end_short_stay` | short_stay_episode_id, reason | - |  |
| `record_vitals` | `record_vitals` / `add_vitals` / `insert_vitals` / `add_vitals_observation` | visit_id, short_stay_episode_id, observation_type, numeric_value, text_value, unit, observation_set_uuid, request_uuid | - | called once per observation; BP = two calls sharing observation_set_uuid |

## P3-3

| Key | Procedure (candidates) | Arguments the web app sends | OUT read back | Notes |
|---|---|---|---|---|
| `medicine_suggest` | `medicine_suggest` | prefix, patient_id | - | rows: medicine_id, name, state (green/blue/red), label, default dose... |
| `create_prescription` | `create_prescription` / `sp_issue_prescription` / `issue_prescription` | visit_id, request_uuid | prescription_id, prescription_version_id |  |
| `new_prescription_version` | `new_prescription_version` / `revise_prescription` / `sp_revise_prescription` | prescription_id, change_reason, request_uuid | prescription_version_id |  |
| `add_prescription_item` | `add_prescription_item` / `add_rx_item` | prescription_id, prescription_version_id, prescription_line_id, line_no, item_change, medicine_id, free_text_medicine_name, chosen_dose_text, chosen_schedule, food_instruction, duration_days, quantity_prescribed, allergy_confirmed, allergy_override_reason, displayed_allergies, request_uuid | prescription_item_id |  |
| `mark_item_status` | `set_prescription_line_status` / `mark_prescription_item_status` | prescription_line_id, status, reason | - | status prescribed|bought_outside|not_needed|cancelled |
| `create_order` | `create_clinical_order` / `sp_create_order` / `create_order` | visit_id, short_stay_episode_id, order_type, request_uuid | clinical_order_id |  |
| `add_order_item` | `add_clinical_order_item` / `add_order_item` | clinical_order_id, item_kind, medicine_id, free_text_medicine_name, price_list_id, dose_value, dose_unit, route, first_scheduled_at, repeat_interval_minutes, quantity_ordered, instructions, supersedes_clinical_order_item_id, request_uuid | clinical_order_item_id |  |
| `order_status` | `set_clinical_order_status` / `set_order_status` | clinical_order_id, status, reason | - | ordered|finalized|cancelled|completed |
| `order_item_status` | `set_clinical_order_item_status` / `stop_order_item` | clinical_order_item_id, status, reason | - | active|stopped|superseded |
| `order_lab_test` | `order_lab_test` / `sp_create_lab_order` / `create_lab_order` | visit_id, outside_lab_id, test_name, clinical_order_id, request_uuid | lab_order_id |  |
| `lab_status` | `add_lab_order_status` / `set_lab_order_status` / `lab_order_status` | lab_order_id, status, reason | - | sample_collected|sent_to_lab|cancelled |
| `attach_lab_report` | `attach_lab_report` / `sp_attach_lab_report` | lab_order_id, file_path, content_hash, source, identifier_1_kind, identifier_1_value, identifier_1_matches, identifier_2_kind, identifier_2_value, identifier_2_matches, identifiers_json, supersedes_lab_report_id, request_uuid | lab_report_id |  |
| `review_report` | `mark_report_reviewed` / `review_lab_report` | lab_report_id, review_note | - |  |
| `critical_mark` | `mark_report_critical` / `mark_lab_report_critical` | lab_report_id, mark, mark_source, reason | - |  |
| `retract_report` | `retract_lab_report` / `retract_report` | lab_report_id, retraction_kind, correct_lab_order_id, note | - |  |
| `lab_price` | `set_lab_order_price` / `record_lab_price` / `add_lab_order_price` | lab_order_id, entered_price, price_basis, deviation_reason | - |  |

## P3-4

| Key | Procedure (candidates) | Arguments the web app sends | OUT read back | Notes |
|---|---|---|---|---|
| `receive_stock` | `receive_stock` / `sp_receive_stock` | medicine_id, batch_code, expiry_date, quantity, cost_price, mrp, supplier_id, request_uuid | stock_batch_id |  |
| `stock_adjustment` | `request_stock_adjustment` | stock_batch_id, quantity_delta, movement_type, reason, approval_request_id | - | movement_type adjustment | expired_writeoff; needs approved request |
| `dispense` | `dispense` / `sp_dispense` | source_type, medicine_id, quantity, prescription_item_id, outside_prescription_id, counter_customer_id, counter_sale_id, stock_batch_id, visit_id, request_uuid | dispensing_id, counter_sale_id | source_type clinic_prescription|outside_prescription|otc |
| `administer_dose` | `administer_dose` / `sp_administer_dose` | clinical_order_item_id, order_item_supply_id, status, ordered_dose_value, ordered_dose_unit, ordered_route, actual_dose_value, actual_dose_unit, actual_route, quantity_consumed, deviation_reason, deviation_authorized_by, occurred_at, request_uuid | - |  |
| `supply_to_order` | `allocate_order_supply` / `link_dispensing_to_order` / `add_order_item_supply` | clinical_order_item_id, dispensing_id, quantity_allocated | - |  |
| `custody_handoff` | `record_custody_handoff` / `custody_handoff` / `handoff_medicine` | dispensing_id, recipient_staff_id, recipient_attendant_id, quantity, handoff_purpose, request_uuid | custody_handoff_id |  |
| `ack_handoff` | `acknowledge_handoff` / `acknowledge_custody_handoff` | custody_handoff_id, acknowledged_quantity, ack_method, witness_note | - |  |
| `ask_leftover` | `ask_leftover_return` | dispensing_id, quantity, condition_note | leftover_return_request_id |  |
| `decide_leftover` | `decide_leftover` | leftover_return_request_id, decision, decision_reason | - | decision accepted | refused |
| `counter_sale_return` | `counter_sale_return` / `record_counter_sale_return` | counter_sale_id, dispensing_id, quantity, reason | - |  |

## P3-5

| Key | Procedure (candidates) | Arguments the web app sends | OUT read back | Notes |
|---|---|---|---|---|
| `counter_sale_payment` | `record_counter_sale_payment` / `counter_sale_payment` | counter_sale_id, amount, method, request_uuid | counter_sale_payment_id, receipt_number |  |
| `ensure_open_bill` (FIXED in TASKS.md) | `ensure_open_bill` | visit_id, counter_customer_id | bill_id |  |
| `bill_add_line` (FIXED in TASKS.md) | `bill_add_line` | bill_id, source_type, source_id, description, quantity, unit_price, reason | - |  |
| `bill_add_credit_line` (FIXED in TASKS.md) | `bill_add_credit_line` | bill_id, source_type, source_id, description, amount, reason | - |  |
| `finalize_bill` | `finalize_bill` / `bill_finalize` | bill_id, reason | - |  |
| `record_payment` | `record_payment` / `sp_record_payment` | bill_id, amount, method, request_uuid | payment_id, receipt_number |  |
| `reverse_payment` | `reverse_payment` / `record_payment_reversal` | payment_id, approval_request_id, reason | - |  |
| `record_refund` | `record_refund` / `issue_refund` | bill_id, payment_id, amount, method, approval_request_id, credit_bill_line_id | refund_id, receipt_number |  |
| `apply_discount` | `apply_discount` / `bill_apply_discount` / `apply_bill_discount` | bill_id, amount, kind, approval_request_id, reason | - | kind discount | free_service -> negative bill line |
| `post_final_line` | `add_post_finalization_line` / `bill_add_post_finalization_line` | bill_id, line_type, description, amount, approval_request_id, reason | - |  |
| `request_partial_discharge` | `request_partial_payment_discharge` | visit_id, bill_id, deferred_amount, promised_by_date, reason | approval_id |  |
| `discharge_visit` | `discharge_visit` / `sp_discharge_visit` | visit_id, financial_gate_basis, approval_request_id, clinical_emergency_override_id, promised_by_date, request_uuid | - |  |
| `declare_cash` | `declare_cash_handover` / `cash_handover_declare` | period_start, period_end, declared_amount, note | cash_handover_declaration_id |  |
| `count_cash` | `receive_cash_handover` / `count_cash_handover` / `cash_handover_receive` | cash_handover_declaration_id, counted_amount, note | - |  |
| `due_promise` | `add_due_promise` / `extend_due_promise` | visit_id, bill_id, promised_by_date, reason | - |  |

## P3-6

| Key | Procedure (candidates) | Arguments the web app sends | OUT read back | Notes |
|---|---|---|---|---|
| `ack_alert` | `acknowledge_alert` | alert_event_id, note | - |  |
| `check_alerts` | `check_alerts` | - | - |  |
| `create_family_link` | `create_family_link` / `sp_create_family_link` | visit_id, attendant_id, second_factor_kind, consent_note, request_uuid | family_link_id, token, printed_code |  |
| `open_family_link` | `open_family_link` | token, dob, printed_code, second_factor | - | returns a row for the visit when OK; lockout handled inside |
| `revoke_family_link` | `revoke_family_link` | family_link_id, reason | - |  |
| `delegation` | `grant_delegation` / `create_authority_delegation` | delegate_staff_id, scope, limit_amount, valid_from, valid_to | - |  |
| `revoke_delegation` | `revoke_delegation` | authority_delegation_id, reason | - |  |
