# DB API: foundation

_Generated from `tools/sql_src/01_foundation_head.sql` by `tools/gen_db_api_docs.py`. Do not edit by hand._

Own-txn procedures commit by themselves: call them in autocommit mode. Rule violations raise SQLSTATE 45000 with a readable message.

| routine | kind | roles | rules | meaning | parameters |
|---|---|---|---|---|---|
| `app_user` | function | * | G5 | Acting staff id taken from @app_user_id; refuses when the user context is missing | `` |
| `app_role` | function | * | G5,R2.1 | Acting hat from @app_role; verifies the staff member really holds that role today | `` |
| `staff_has_role` | function | * | R2.1 | 1 if the staff member holds the role today | `p_staff_id BIGINT UNSIGNED, p_role VARCHAR(20)` |
| `setting_int` | function | * | G9 | Integer clinic setting (error if the key is missing) | `p_key VARCHAR(60)` |
| `setting_text` | function | * | G9 | Text clinic setting (error if the key is missing) | `p_key VARCHAR(60)` |
| `visit_is_closed` | function | * | R9.4 | 1 if the visit has an active (not reopened) closure | `p_visit_id BIGINT UNSIGNED` |
| `patient_is_merged` | function | * | R1.5 | 1 if the patient record was merged away | `p_patient_id BIGINT UNSIGNED` |
| `canonical_patient` | function | * | R1.5 | Surviving patient id (follows merge redirects, max 5 hops; CR-2) | `p_patient_id BIGINT UNSIGNED` |
| `slot_has_live_claim` | function | * | R3.6 | 1 if another live (not cancelled / no-show) appointment holds the slot | `p_slot BIGINT UNSIGNED, p_except_appt BIGINT UNSIGNED` |
| `approver_has_authority` | function | * | G6,R9.1 | 1 if this person may decide this request (owner hat, or a valid unrevoked delegation covering scope and amount at time p_at) | `p_request_id BIGINT UNSIGNED, p_approver BIGINT UNSIGNED, p_role VARCHAR(20), p_delegation BIGINT UNSIGNED, p_at DATETIME(3)` |
| `approval_is_valid` | function | * | G6 | 1 if the request has an approved decision of one of the listed types (comma list, no spaces) by someone with authority | `p_request_id BIGINT UNSIGNED, p_types VARCHAR(200)` |
| `find_delegation` | function | * | G6 | Delegation id this person can use to decide the request, or NULL | `p_approver BIGINT UNSIGNED, p_request_id BIGINT UNSIGNED` |
| `int_require_role` | procedure | * | G4 | Refuses unless the current hat is in the comma list (no spaces), e.g. 'DOCTOR,OWNER' | `IN p_roles VARCHAR(120)` |
| `next_code` | procedure | * | R1.1,R1.5 | Next display code for a kind (patient, emergency_patient, staff, bill, receipt, counter_sale, counter_customer) | `IN p_kind VARCHAR(20), OUT p_code VARCHAR(30)` |
| `raise_alert_ex` | procedure | * | R10.2,R10.3 | Extended alert insert (class and colour explicit); skips duplicates inside alert_dedupe_hours | `IN p_type VARCHAR(40), IN p_class VARCHAR(30), IN p_color VARCHAR(10), IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED, IN p_message VARCHAR(300)` |
| `raise_alert` | procedure | * | R10.2,R10.3 | Shared interface. severity INTERRUPT = immediate safety alert (red); SUMMARY = daily-summary item (amber) | `IN p_type VARCHAR(40), IN p_severity VARCHAR(10), IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED, IN p_message VARCHAR(300)` |
| `queue_message` | procedure | * | R11.3,R8.5,R9.3,G10 | Shared interface. Writes a simulated SMS (outbox + delivery event). template = family_link|receipt|due_reminder|appointment_reminder|alert_notice | `IN p_to_phone VARCHAR(20), IN p_template VARCHAR(40), IN p_body VARCHAR(500), IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED` |
| `request_approval_ex` | procedure | * | G6,R2.3 | Full approval request with typed context FKs. Notifies the owner immediately (R10.3) | `IN p_type VARCHAR(50), IN p_visit_id BIGINT UNSIGNED, IN p_bill_id BIGINT UNSIGNED, IN p_stock_batch_id BIGINT UNSIGNED, IN p_episode_id BIGINT UNSIGNED, IN p_counter_sale_id BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_qty DECIMAL(10,2), IN p_change VARCHAR(500), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `request_approval` | procedure | * | G6,R2.3 | Shared interface. ref_table is one of visit, bill, stock_batch, short_stay_episode, counter_sale | `IN p_type VARCHAR(40), IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED, IN p_reason VARCHAR(300), IN p_amount DECIMAL(12,2), OUT p_approval_id BIGINT UNSIGNED` |
| `decide_approval` | procedure | OWNER,DOCTOR,RECEPTIONIST,ACCOUNTANT,PHARMACY,NURSE | G6,R2.3,R9.1 | Shared interface. decision APPROVED or REJECTED. The trigger refuses self-approval, system principals and anyone without authority | `IN p_approval_id BIGINT UNSIGNED, IN p_decision VARCHAR(10), IN p_note VARCHAR(300)` |
| `auth_get_credentials` | procedure | login | G4,R2.2 | Login lookup: returns one row (staff_id, password_hash, is_active, roles csv) for a login name | `IN p_username VARCHAR(60)` |
| `auth_log_event` | procedure | login | R10.5 | Records a login/logout/failed-login event under the SYS-AUTH principal | `IN p_staff_id BIGINT UNSIGNED, IN p_identifier VARCHAR(60), IN p_event VARCHAR(20), IN p_claimed_role VARCHAR(20), IN p_session_hash CHAR(64), IN p_source VARCHAR(100)` |
| `create_staff` | procedure | OWNER | R2.1,R2.2 | Create a staff member with roles (comma list, no spaces) and credential hash (hashed by the app). Registration no. required for DOCTOR | `IN p_given VARCHAR(80), IN p_family VARCHAR(80), IN p_phone VARCHAR(20), IN p_login VARCHAR(60), IN p_password_hash VARCHAR(255), IN p_roles_csv VARCHAR(120), IN p_registration_no VARCHAR(40), OUT p_staff_id BIGINT UNSIGNED` |
| `set_setting` | procedure | OWNER | G9 | Change one clinic setting (audited with the reason) | `IN p_key VARCHAR(60), IN p_value VARCHAR(500), IN p_reason VARCHAR(300)` |
