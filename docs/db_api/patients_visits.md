# DB API: patients_visits

_Generated from `db/logic/02_patients_visits.sql` by `tools/gen_db_api_docs.py`. Do not edit by hand._

Own-txn procedures commit by themselves: call them in autocommit mode. Rule violations raise SQLSTATE 45000 with a readable message.

| routine | kind | roles | rules | meaning | parameters |
|---|---|---|---|---|---|
| `levenshtein` | function | * | R1.4 | Levenshtein edit distance (inputs cut to 60 chars) | `a VARCHAR(255), b VARCHAR(255)` |
| `name_similarity` | function | * | R1.4 | Name similarity 0.000-1.000 (1 = identical) from Levenshtein on lower-cased trimmed names | `a VARCHAR(250), b VARCHAR(250)` |
| `patient_match_tier` | function | * | R1.4 | Duplicate tier 1-4 of one patient against search inputs (0 = no match). T1 phone+name, T2 name+DOB, T3 DOB+postal code, T4 Aadhaar last-4 tie-breaker | `p_patient_id BIGINT UNSIGNED, p_name VARCHAR(250), p_dob DATE, p_phone VARCHAR(20), p_aadhaar CHAR(4), p_postal CHAR(6)` |
| `sp_find_patient_candidates` | procedure | RECEPTIONIST,OWNER | R1.4,G12 | Ranked duplicate candidates (tier 1 best). The receptionist confirms; nothing is merged automatically | `IN p_name VARCHAR(250), IN p_dob DATE, IN p_phone VARCHAR(20), IN p_aadhaar CHAR(4), IN p_postal CHAR(6)` |
| `sp_register_patient` | procedure | RECEPTIONIST | R1.1,R1.2,R1.3,R1.4,G12 | own txn. Register a patient. If duplicate candidates exist the call is refused unless p_confirm_not_duplicate=1 (a person confirmed) | `IN p_given VARCHAR(80), IN p_middle VARCHAR(80), IN p_family VARCHAR(80), IN p_gender VARCHAR(10), IN p_dob DATE, IN p_phone VARCHAR(20), IN p_aadhaar CHAR(4), IN p_perm_line VARCHAR(200), IN p_perm_city VARCHAR(80), IN p_perm_postal CHAR(6), IN p_confirm_not_duplicate TINYINT, OUT p_patient_id BIGINT UNSIGNED, OUT p_display_code VARCHAR(30)` |
| `sp_register_unknown_patient` | procedure | RECEPTIONIST,DOCTOR,NURSE | R1.5 | own txn. Emergency Unknown Patient: TMP- code, gender and approximate age only | `IN p_gender VARCHAR(10), IN p_approx_age SMALLINT, OUT p_patient_id BIGINT UNSIGNED, OUT p_display_code VARCHAR(30)` |
| `sp_add_patient_phone` | procedure | RECEPTIONIST | R1.2 | own txn. Add a phone number; a new primary closes the previous primary (history kept) | `IN p_patient_id BIGINT UNSIGNED, IN p_phone VARCHAR(20), IN p_make_primary TINYINT` |
| `sp_add_patient_attendant` | procedure | RECEPTIONIST,NURSE | R1.2,R11.4 | Add an attendant / emergency contact | `IN p_patient_id BIGINT UNSIGNED, IN p_name VARCHAR(120), IN p_relationship VARCHAR(40), IN p_phone VARCHAR(20), IN p_is_emergency TINYINT, OUT p_attendant_id BIGINT UNSIGNED` |
| `sp_update_demographics` | procedure | RECEPTIONIST | R1.6 | Demographic correction (audited old/new in audit_log). A reason is mandatory. Blood group and allergies are clinical and not editable here | `IN p_patient_id BIGINT UNSIGNED, IN p_given VARCHAR(80), IN p_middle VARCHAR(80), IN p_family VARCHAR(80), IN p_gender VARCHAR(10), IN p_dob DATE, IN p_perm_line VARCHAR(200), IN p_perm_city VARCHAR(80), IN p_perm_postal CHAR(6), IN p_reason VARCHAR(300)` |
| `sp_merge_patients` | procedure | RECEPTIONIST,OWNER | R1.5,G12,G2 | own txn. Human-confirmed merge. Per CR-2 event rows are NOT rewritten: the merge log is the redirect (canonical_patient()); phones and attendants (master rows) move to the survivor | `IN p_source BIGINT UNSIGNED, IN p_survivor BIGINT UNSIGNED, IN p_reason VARCHAR(300), IN p_tier VARCHAR(2), OUT p_merge_id BIGINT UNSIGNED` |
| `int_generate_slots` | procedure | * | R3.6 | internal: generate appointment slots for one schedule version for schedule_horizon_days, recorded under SYS-SCHEDULER | `IN p_schedule_id BIGINT UNSIGNED` |
| `sp_add_doctor_schedule` | procedure | OWNER,RECEPTIONIST | R3.6,R3.7 | own txn. Add a weekly session for a doctor (weekday 0=Mon..6=Sun) and generate its slots | `IN p_doctor BIGINT UNSIGNED, IN p_weekday TINYINT, IN p_start TIME, IN p_end TIME, IN p_slot_minutes SMALLINT, IN p_valid_from DATE, OUT p_schedule_id BIGINT UNSIGNED` |
| `sp_request_schedule_change` | procedure | DOCTOR | R3.7 | A doctor requests a change to his own schedule (modify / add_session / withdraw_session). Nothing changes until someone else applies it | `IN p_target_schedule BIGINT UNSIGNED, IN p_kind VARCHAR(20), IN p_weekday TINYINT, IN p_start TIME, IN p_end TIME, IN p_slot_minutes SMALLINT, IN p_valid_from DATE, IN p_reason VARCHAR(300), OUT p_request_id BIGINT UNSIGNED` |
| `sp_apply_schedule_change` | procedure | OWNER,RECEPTIONIST | R3.7 | own txn. Apply or decline a schedule change (never by the requesting doctor). Applying creates a NEW schedule version superseding the old one; old slots stop being bookable | `IN p_request_id BIGINT UNSIGNED, IN p_outcome VARCHAR(10), IN p_reason VARCHAR(300), OUT p_new_schedule_id BIGINT UNSIGNED` |
| `slot_is_effective` | function | * | R3.6,R3.7 | internal: 1 if the slot's schedule version is still effective on the slot date (not superseded) | `p_slot BIGINT UNSIGNED` |
| `int_add_appt_status` | procedure | * | R3.2 | internal: append an appointment status row (assigns status_seq under the appointment lock) | `IN p_appt BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300)` |
| `sp_book_appointment` | procedure | RECEPTIONIST | R3.2,R3.6 | own txn. Book a slot. Doctor row is locked, live claims are checked, appointment + first status + claim are one transaction. Error "Slot already booked for this doctor" if taken | `IN p_patient BIGINT UNSIGNED, IN p_slot BIGINT UNSIGNED, IN p_channel VARCHAR(20), OUT p_appointment_id BIGINT UNSIGNED` |
| `sp_reschedule_appointment` | procedure | RECEPTIONIST | R3.6 | own txn. Move a booked appointment to another slot of the same doctor; the old claim stays as history and the old slot becomes free | `IN p_appt BIGINT UNSIGNED, IN p_new_slot BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `sp_set_appointment_status` | procedure | RECEPTIONIST,DOCTOR | R3.2 | own txn. Move an appointment to its next status (valid transitions only). in_consultation/completed need DOCTOR, the rest RECEPTIONIST | `IN p_appt BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300)` |
| `int_add_visit_status` | procedure | * | R3.3 | internal: append a visit status row (status_seq assigned under the visit lock) | `IN p_visit BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_location VARCHAR(60), IN p_reason VARCHAR(300)` |
| `sp_create_visit` | procedure | RECEPTIONIST,DOCTOR,NURSE | R3.1,R3.3 | own txn. Open a visit (appointment, walk_in or emergency). Inserts the first status (arrived) and, when a doctor is known, the attending assignment | `IN p_patient BIGINT UNSIGNED, IN p_appointment BIGINT UNSIGNED, IN p_doctor BIGINT UNSIGNED, IN p_entry_type VARCHAR(12), OUT p_visit_id BIGINT UNSIGNED` |
| `sp_add_visit_status` | procedure | RECEPTIONIST,DOCTOR,NURSE,PHARMACY,ACCOUNTANT | R3.3 | own txn. Move a visit to waiting / with_doctor / lab / pharmacy / billing (discharged is set only by discharge). Location is a label, no beds | `IN p_visit BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_location VARCHAR(60), IN p_reason VARCHAR(300)` |
| `sp_start_short_stay` | procedure | DOCTOR,NURSE | R3.1,R3.4 | own txn. Add the short-stay episode to a visit (at most one per visit). Location label Observation Area | `IN p_visit BIGINT UNSIGNED, IN p_reason VARCHAR(300), OUT p_episode_id BIGINT UNSIGNED` |
| `sp_close_short_stay` | procedure | DOCTOR,NURSE | R3.1 | End a short-stay episode (the end time is derived from this event) | `IN p_episode BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `sp_request_stay_extension` | procedure | DOCTOR,NURSE | R3.4,G6 | Ask the owner to approve a stay beyond the limit. proposed_until is stored in the request; the extension row is created by sp_apply_stay_extension after approval | `IN p_episode BIGINT UNSIGNED, IN p_until DATETIME, IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `sp_apply_stay_extension` | procedure | DOCTOR,NURSE | R3.4,G6 | After the owner approved: record the extension (trigger re-checks the approval) | `IN p_approval_id BIGINT UNSIGNED` |
| `sp_record_vitals` | procedure | NURSE,DOCTOR | R3.5 | own txn. Record a vitals set: one typed row per supplied measurement (BP = two rows sharing observation_set_uuid) | `IN p_visit BIGINT UNSIGNED, IN p_episode BIGINT UNSIGNED, IN p_bp_sys SMALLINT, IN p_bp_dia SMALLINT, IN p_pulse SMALLINT, IN p_temp DECIMAL(4,1), IN p_spo2 SMALLINT, IN p_resp SMALLINT, IN p_weight DECIMAL(5,1)` |

## Views

- `v_patient_alias`
- `v_patient_search`
- `v_appointment_current`
- `v_free_slots`
- `v_visit_current`
- `v_opd_visit`
- `v_queue_today`
- `v_stay_status`
- `v_stay_over_limit`
