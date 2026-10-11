-- =====================================================================================
-- P3-1 FOUNDATION (hand-written part). Public API in this file:
--   app_user() / app_role()                          acting staff id / validated hat (G5), SIGNAL if missing
--   staff_has_role(staff_id, role_code)              does the person hold this role today
--   setting_int(key) / setting_text(key)             read clinic_settings (G9)
--   next_code(kind, OUT code)                        CLN1-000123 style codes from id_counter
--   raise_alert(type, severity, ref_table, ref_id, message)       shared interface
--   queue_message(to_phone, template, body, ref_table, ref_id)    shared interface
--   request_approval(type, ref_table, ref_id, reason, amount, OUT id)   shared interface
--   decide_approval(id, decision, note)              shared interface (maker-checker enforced by trigger)
--   auth_get_credentials(username) / auth_log_event(...)   login support (P5-1)
--   create_staff(...) / set_setting(...)             owner administration
-- Convention: leaf procedures here do NOT open transactions; top-level procedures (create_staff) do.
-- Request type values are lower-case (see approval_request.request_type).
-- =====================================================================================

-- @api roles=* rules=G5 | Acting staff id taken from @app_user_id; refuses when the user context is missing
CREATE FUNCTION app_user() RETURNS BIGINT UNSIGNED NOT DETERMINISTIC READS SQL DATA
BEGIN
  IF @app_user_id IS NULL OR @app_role IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not logged in: user context (@app_user_id/@app_role) is missing';
  END IF;
  RETURN CAST(@app_user_id AS UNSIGNED);
END$$

-- @api roles=* rules=G5,R2.1 | Acting hat from @app_role; verifies the staff member really holds that role today
CREATE FUNCTION app_role() RETURNS VARCHAR(20) NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v_n INT DEFAULT 0;
  IF @app_user_id IS NULL OR @app_role IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not logged in: user context (@app_user_id/@app_role) is missing';
  END IF;
  SELECT COUNT(*) INTO v_n
    FROM staff_role sr JOIN `role` r ON r.role_id = sr.role_id JOIN staff s ON s.staff_id = sr.staff_id
   WHERE sr.staff_id = CAST(@app_user_id AS UNSIGNED) AND r.role_code = @app_role
     AND sr.valid_from <= CURRENT_DATE AND (sr.valid_to IS NULL OR sr.valid_to >= CURRENT_DATE)
     AND s.active_from <= CURRENT_DATE AND (s.active_to IS NULL OR s.active_to >= CURRENT_DATE);
  IF v_n = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Your account does not hold that role (or is inactive)';
  END IF;
  RETURN @app_role;
END$$

-- @api roles=* rules=R2.1 | 1 if the staff member holds the role today
CREATE FUNCTION staff_has_role(p_staff_id BIGINT UNSIGNED, p_role VARCHAR(20)) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v INT DEFAULT 0;
  SELECT COUNT(*) INTO v
    FROM staff_role sr JOIN `role` r ON r.role_id = sr.role_id
   WHERE sr.staff_id = p_staff_id AND r.role_code = p_role
     AND sr.valid_from <= CURRENT_DATE AND (sr.valid_to IS NULL OR sr.valid_to >= CURRENT_DATE);
  RETURN v > 0;
END$$

-- @api roles=* rules=G9 | Integer clinic setting (error if the key is missing)
CREATE FUNCTION setting_int(p_key VARCHAR(60)) RETURNS INT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v VARCHAR(500);
  SELECT setting_value INTO v FROM clinic_settings WHERE setting_key = p_key;
  IF v IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Missing clinic setting (integer)'; END IF;
  RETURN CAST(v AS SIGNED);
END$$

-- @api roles=* rules=G9 | Text clinic setting (error if the key is missing)
CREATE FUNCTION setting_text(p_key VARCHAR(60)) RETURNS VARCHAR(500) NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v VARCHAR(500);
  SELECT setting_value INTO v FROM clinic_settings WHERE setting_key = p_key;
  IF v IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Missing clinic setting (text)'; END IF;
  RETURN v;
END$$

-- @api roles=* rules=R9.4 | 1 if the visit has an active (not reopened) closure
CREATE FUNCTION visit_is_closed(p_visit_id BIGINT UNSIGNED) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v INT DEFAULT 0;
  IF p_visit_id IS NULL THEN RETURN 0; END IF;
  SELECT COUNT(*) INTO v FROM visit_closure c
   WHERE c.visit_id = p_visit_id AND c.reverses_visit_closure_id IS NULL
     AND NOT EXISTS (SELECT 1 FROM visit_closure r WHERE r.reverses_visit_closure_id = c.visit_closure_id);
  RETURN v > 0;
END$$

-- @api roles=* rules=R1.5 | 1 if the patient record was merged away
CREATE FUNCTION patient_is_merged(p_patient_id BIGINT UNSIGNED) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v INT DEFAULT 0;
  SELECT COUNT(*) INTO v FROM patient_merge_log WHERE source_patient_id = p_patient_id;
  RETURN v > 0;
END$$

-- @api roles=* rules=R1.5 | Surviving patient id (follows merge redirects, max 5 hops; CR-2)
CREATE FUNCTION canonical_patient(p_patient_id BIGINT UNSIGNED) RETURNS BIGINT UNSIGNED NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v_cur BIGINT UNSIGNED; DECLARE v_next BIGINT UNSIGNED; DECLARE i INT DEFAULT 0;
  SET v_cur = p_patient_id;
  WHILE i < 5 DO
    SET v_next = NULL;
    SELECT surviving_patient_id INTO v_next FROM patient_merge_log WHERE source_patient_id = v_cur;
    IF v_next IS NULL THEN RETURN v_cur; END IF;
    SET v_cur = v_next; SET i = i + 1;
  END WHILE;
  RETURN v_cur;
END$$

-- @api roles=* rules=R3.6 | 1 if another live (not cancelled / no-show) appointment holds the slot
CREATE FUNCTION slot_has_live_claim(p_slot BIGINT UNSIGNED, p_except_appt BIGINT UNSIGNED) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v INT DEFAULT 0;
  SELECT COUNT(*) INTO v FROM appointment_slot_claim cl
   WHERE cl.appointment_slot_id = p_slot AND cl.appointment_id <> p_except_appt
     AND NOT EXISTS (SELECT 1 FROM appointment_slot_claim n
                      WHERE n.appointment_id = cl.appointment_id AND n.appointment_slot_claim_id > cl.appointment_slot_claim_id)
     AND COALESCE((SELECT h.status FROM appointment_status_history h WHERE h.appointment_id = cl.appointment_id
                    ORDER BY h.status_seq DESC LIMIT 1), 'booked') NOT IN ('cancelled', 'no_show');
  RETURN v > 0;
END$$

-- @api roles=* rules=G6,R9.1 | 1 if this person may decide this request (owner hat, or a valid unrevoked delegation covering scope and amount at time p_at)
CREATE FUNCTION approver_has_authority(p_request_id BIGINT UNSIGNED, p_approver BIGINT UNSIGNED, p_role VARCHAR(20),
                                       p_delegation BIGINT UNSIGNED, p_at DATETIME(3)) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v_type VARCHAR(50); DECLARE v_amount DECIMAL(12,2); DECLARE v_req BIGINT UNSIGNED; DECLARE v_n INT DEFAULT 0;
  SELECT request_type, requested_amount, recorded_by INTO v_type, v_amount, v_req
    FROM approval_request WHERE approval_request_id = p_request_id;
  IF v_type IS NULL OR v_req = p_approver THEN RETURN 0; END IF;
  IF p_role = 'OWNER' AND staff_has_role(p_approver, 'OWNER') THEN RETURN 1; END IF;
  IF p_delegation IS NULL THEN RETURN 0; END IF;
  SELECT COUNT(*) INTO v_n FROM authority_delegation d
   WHERE d.authority_delegation_id = p_delegation AND d.delegate_staff_id = p_approver
     AND p_at >= d.valid_from AND p_at <= d.valid_to
     AND NOT EXISTS (SELECT 1 FROM authority_delegation_revocation r
                      WHERE r.authority_delegation_id = d.authority_delegation_id AND r.recorded_at <= p_at)
     AND (d.scope = 'approval_backup'
          OR (d.scope = 'discount' AND v_type IN ('discount', 'free_service'))
          OR (d.scope = 'partial_payment' AND v_type = 'partial_payment_discharge'))
     AND (d.limit_amount IS NULL OR IFNULL(v_amount, 0) <= d.limit_amount);
  RETURN v_n > 0;
END$$

-- @api roles=* rules=G6 | 1 if the request has an approved decision of one of the listed types (comma list, no spaces) by someone with authority
CREATE FUNCTION approval_is_valid(p_request_id BIGINT UNSIGNED, p_types VARCHAR(200)) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v_type VARCHAR(50); DECLARE v_dec VARCHAR(10); DECLARE v_by BIGINT UNSIGNED; DECLARE v_role VARCHAR(20);
  DECLARE v_deleg BIGINT UNSIGNED; DECLARE v_at DATETIME(3);
  IF p_request_id IS NULL THEN RETURN 0; END IF;
  SELECT request_type INTO v_type FROM approval_request WHERE approval_request_id = p_request_id;
  IF v_type IS NULL OR FIND_IN_SET(v_type, p_types) = 0 THEN RETURN 0; END IF;
  SELECT decision, recorded_by, recorded_by_role, authority_delegation_id, recorded_at
    INTO v_dec, v_by, v_role, v_deleg, v_at FROM approval_decision WHERE approval_request_id = p_request_id;
  IF v_dec IS NULL OR v_dec <> 'approved' THEN RETURN 0; END IF;
  RETURN approver_has_authority(p_request_id, v_by, v_role, v_deleg, v_at);
END$$

-- @api roles=* rules=G6 | Delegation id this person can use to decide the request, or NULL
CREATE FUNCTION find_delegation(p_approver BIGINT UNSIGNED, p_request_id BIGINT UNSIGNED) RETURNS BIGINT UNSIGNED NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v_type VARCHAR(50); DECLARE v_amount DECIMAL(12,2); DECLARE v_id BIGINT UNSIGNED;
  SELECT request_type, requested_amount INTO v_type, v_amount FROM approval_request WHERE approval_request_id = p_request_id;
  SELECT d.authority_delegation_id INTO v_id FROM authority_delegation d
   WHERE d.delegate_staff_id = p_approver AND NOW(3) >= d.valid_from AND NOW(3) <= d.valid_to
     AND NOT EXISTS (SELECT 1 FROM authority_delegation_revocation r WHERE r.authority_delegation_id = d.authority_delegation_id)
     AND (d.scope = 'approval_backup'
          OR (d.scope = 'discount' AND v_type IN ('discount', 'free_service'))
          OR (d.scope = 'partial_payment' AND v_type = 'partial_payment_discharge'))
     AND (d.limit_amount IS NULL OR IFNULL(v_amount, 0) <= d.limit_amount)
   ORDER BY d.authority_delegation_id DESC LIMIT 1;
  RETURN v_id;
END$$

-- @api roles=* rules=G4 | Refuses unless the current hat is in the comma list (no spaces), e.g. 'DOCTOR,OWNER'
CREATE PROCEDURE int_require_role(IN p_roles VARCHAR(120))
BEGIN
  IF FIND_IN_SET(app_role(), p_roles) = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Your role is not allowed to do this';
  END IF;
END$$

-- @api roles=* rules=R1.1,R1.5 | Next display code for a kind (patient, emergency_patient, staff, bill, receipt, counter_sale, counter_customer)
CREATE PROCEDURE next_code(IN p_kind VARCHAR(20), OUT p_code VARCHAR(30))
BEGIN
  DECLARE v_val BIGINT UNSIGNED; DECLARE v_prefix VARCHAR(20); DECLARE v_width INT;
  UPDATE id_counter SET next_value = LAST_INSERT_ID(next_value + 1) WHERE counter_name = p_kind;
  IF ROW_COUNT() = 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown code kind'; END IF;
  SET v_val = LAST_INSERT_ID() - 1;
  SELECT display_prefix INTO v_prefix FROM id_counter WHERE counter_name = p_kind;
  SET v_prefix = REPLACE(v_prefix, '{CLINIC}', setting_text('clinic_code'));
  SET v_width = IF(p_kind = 'emergency_patient', 5, 6);
  SET p_code = CONCAT(v_prefix, LPAD(v_val, v_width, '0'));
END$$

-- @api roles=* rules=R10.2,R10.3 | Extended alert insert (class and colour explicit); skips duplicates inside alert_dedupe_hours
CREATE PROCEDURE raise_alert_ex(IN p_type VARCHAR(40), IN p_class VARCHAR(30), IN p_color VARCHAR(10),
                                IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED, IN p_message VARCHAR(300))
BEGIN
  DECLARE v_hours INT; DECLARE v_id BIGINT UNSIGNED;
  SET v_hours = setting_int('alert_dedupe_hours');
  IF NOT EXISTS (SELECT 1 FROM alert_event
                  WHERE alert_type = p_type AND source_entity_type <=> p_ref_table
                    AND source_entity_id <=> CAST(p_ref_id AS CHAR)
                    AND recorded_at > NOW(3) - INTERVAL v_hours HOUR) THEN
    INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
    VALUES (p_class, p_type, p_color, p_ref_table, CAST(p_ref_id AS CHAR), p_message);
    SET v_id = LAST_INSERT_ID();
    IF p_class IN ('safety_interrupt', 'approval_pending') THEN
      CALL queue_message(setting_text('owner_phone'), 'alert_notice', 'The clinic needs your attention. Please open the dashboard.', NULL, NULL);
    END IF;
  END IF;
END$$

-- @api roles=* rules=R10.2,R10.3 | Shared interface. severity INTERRUPT = immediate safety alert (red); SUMMARY = daily-summary item (amber)
CREATE PROCEDURE raise_alert(IN p_type VARCHAR(40), IN p_severity VARCHAR(10), IN p_ref_table VARCHAR(40),
                             IN p_ref_id BIGINT UNSIGNED, IN p_message VARCHAR(300))
BEGIN
  IF p_severity = 'INTERRUPT' THEN
    CALL raise_alert_ex(p_type, 'safety_interrupt', 'red', p_ref_table, p_ref_id, p_message);
  ELSEIF p_severity = 'SUMMARY' THEN
    CALL raise_alert_ex(p_type, 'daily_summary_item', 'amber', p_ref_table, p_ref_id, p_message);
  ELSE
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Alert severity must be INTERRUPT or SUMMARY';
  END IF;
END$$

-- @api roles=* rules=R11.3,R8.5,R9.3,G10 | Shared interface. Writes a simulated SMS (outbox + delivery event). template = family_link|receipt|due_reminder|appointment_reminder|alert_notice
CREATE PROCEDURE queue_message(IN p_to_phone VARCHAR(20), IN p_template VARCHAR(40), IN p_body VARCHAR(500),
                               IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_id BIGINT UNSIGNED;
  IF p_to_phone IS NOT NULL AND p_to_phone <> '' THEN
    IF p_template NOT IN ('family_link', 'receipt', 'due_reminder', 'appointment_reminder', 'alert_notice') THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown message template';
    END IF;
    INSERT INTO message_outbox (message_type, recipient_phone, safe_template_key, rendered_text,
                                family_link_id, payment_id, counter_sale_payment_id, due_promise_id)
    VALUES (p_template, p_to_phone, CONCAT(p_template, '_v1'), p_body,
            IF(p_ref_table = 'family_link', p_ref_id, NULL), IF(p_ref_table = 'payment', p_ref_id, NULL),
            IF(p_ref_table = 'counter_sale_payment', p_ref_id, NULL), IF(p_ref_table = 'due_promise', p_ref_id, NULL));
    SET v_id = LAST_INSERT_ID();
    INSERT INTO message_delivery_event (message_outbox_id, delivery_state, details) VALUES (v_id, 'simulated_sent', 'Simulated: not sent to a real network');
  END IF;
END$$

-- @api roles=* rules=G6,R2.3 | Full approval request with typed context FKs. Notifies the owner immediately (R10.3)
CREATE PROCEDURE request_approval_ex(IN p_type VARCHAR(50), IN p_visit_id BIGINT UNSIGNED, IN p_bill_id BIGINT UNSIGNED,
                                     IN p_stock_batch_id BIGINT UNSIGNED, IN p_episode_id BIGINT UNSIGNED,
                                     IN p_counter_sale_id BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_qty DECIMAL(10,2),
                                     IN p_change VARCHAR(500), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  INSERT INTO approval_request (request_type, visit_id, bill_id, stock_batch_id, short_stay_episode_id, counter_sale_id,
                                requested_amount, requested_quantity, proposed_change, reason)
  VALUES (p_type, p_visit_id, p_bill_id, p_stock_batch_id, p_episode_id, p_counter_sale_id, p_amount, p_qty, p_change, p_reason);
  SET p_approval_id = LAST_INSERT_ID();
  CALL raise_alert_ex('APPROVAL_PENDING', 'approval_pending', 'red', 'approval_request', p_approval_id, 'An approval request is waiting for a decision');
END$$

-- @api roles=* rules=G6,R2.3 | Shared interface. ref_table is one of visit, bill, stock_batch, short_stay_episode, counter_sale
CREATE PROCEDURE request_approval(IN p_type VARCHAR(40), IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED,
                                  IN p_reason VARCHAR(300), IN p_amount DECIMAL(12,2), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_bill BIGINT UNSIGNED; DECLARE v_visit BIGINT UNSIGNED;
  IF p_ref_table NOT IN ('visit', 'bill', 'stock_batch', 'short_stay_episode', 'counter_sale') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unsupported approval reference table';
  END IF;
  IF p_type = 'partial_payment_discharge' AND p_ref_table = 'visit' THEN
    SET v_visit = p_ref_id;
    SELECT bill_id INTO v_bill FROM bill WHERE visit_id = p_ref_id;
  ELSEIF p_ref_table = 'bill' THEN SET v_bill = p_ref_id;
  ELSEIF p_ref_table = 'visit' THEN SET v_visit = p_ref_id;
  END IF;
  CALL request_approval_ex(p_type, v_visit, v_bill, IF(p_ref_table = 'stock_batch', p_ref_id, NULL),
       IF(p_ref_table = 'short_stay_episode', p_ref_id, NULL), IF(p_ref_table = 'counter_sale', p_ref_id, NULL),
       p_amount, NULL, NULL, p_reason, p_approval_id);
END$$

-- @api roles=OWNER,DOCTOR,RECEPTIONIST,ACCOUNTANT,PHARMACY,NURSE rules=G6,R2.3,R9.1 | Shared interface. decision APPROVED or REJECTED. The trigger refuses self-approval, system principals and anyone without authority
CREATE PROCEDURE decide_approval(IN p_approval_id BIGINT UNSIGNED, IN p_decision VARCHAR(10), IN p_note VARCHAR(300))
BEGIN
  DECLARE v_deleg BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR 1062 SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This request was already decided';
  IF UPPER(p_decision) NOT IN ('APPROVED', 'REJECTED') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Decision must be APPROVED or REJECTED';
  END IF;
  IF app_role() <> 'OWNER' THEN SET v_deleg = find_delegation(app_user(), p_approval_id); END IF;
  INSERT INTO approval_decision (approval_request_id, decision, decision_channel, authority_delegation_id, decision_reason)
  VALUES (p_approval_id, LOWER(p_decision), IFNULL(@decision_channel, 'in_app'), v_deleg, p_note);
END$$

-- @api roles=login rules=G4,R2.2 | Login lookup: returns one row (staff_id, password_hash, is_active, roles csv) for a login name
CREATE PROCEDURE auth_get_credentials(IN p_username VARCHAR(60))
BEGIN
  SELECT s.staff_id, s.display_code, s.given_name, s.family_name, c.password_hash,
         (s.active_from <= CURRENT_DATE AND (s.active_to IS NULL OR s.active_to >= CURRENT_DATE)) AS is_active,
         (SELECT GROUP_CONCAT(r.role_code ORDER BY r.role_code)
            FROM staff_role sr JOIN `role` r ON r.role_id = sr.role_id
           WHERE sr.staff_id = s.staff_id AND r.role_code <> 'SYSTEM'
             AND sr.valid_from <= CURRENT_DATE AND (sr.valid_to IS NULL OR sr.valid_to >= CURRENT_DATE)) AS roles
    FROM staff s JOIN staff_credential c ON c.staff_id = s.staff_id
   WHERE s.login_subject = p_username AND s.principal_kind = 'human';
END$$

-- @api roles=login rules=R10.5 | Records a login/logout/failed-login event under the SYS-AUTH principal
CREATE PROCEDURE auth_log_event(IN p_staff_id BIGINT UNSIGNED, IN p_identifier VARCHAR(60), IN p_event VARCHAR(20),
                                IN p_claimed_role VARCHAR(20), IN p_session_hash CHAR(64), IN p_source VARCHAR(100))
BEGIN
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN SET @app_user_id = @o_uid; SET @app_role = @o_role; RESIGNAL; END;
  SET @o_uid = @app_user_id; SET @o_role = @app_role;
  SET @app_user_id = 4; SET @app_role = 'SYSTEM';
  INSERT INTO login_session_event (staff_id, claimed_role_id, login_identifier, session_id_hash, event_type, source_context)
  VALUES (p_staff_id, (SELECT role_id FROM `role` WHERE role_code = p_claimed_role), p_identifier, p_session_hash, p_event, p_source);
  SET @app_user_id = @o_uid; SET @app_role = @o_role;
END$$

-- @api roles=OWNER rules=R2.1,R2.2 | Create a staff member with roles (comma list, no spaces) and credential hash (hashed by the app). Registration no. required for DOCTOR
CREATE PROCEDURE create_staff(IN p_given VARCHAR(80), IN p_family VARCHAR(80), IN p_phone VARCHAR(20), IN p_login VARCHAR(60),
                              IN p_password_hash VARCHAR(255), IN p_roles_csv VARCHAR(120), IN p_registration_no VARCHAR(40),
                              OUT p_staff_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_code VARCHAR(30); DECLARE v_found INT; DECLARE v_wanted INT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('OWNER');
  IF p_login IS NULL OR CHAR_LENGTH(p_login) < 3 OR p_password_hash IS NULL OR p_roles_csv IS NULL OR CHAR_LENGTH(p_roles_csv) = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Login name, password and at least one role are required';
  END IF;
  SELECT COUNT(*) INTO v_found FROM `role` WHERE FIND_IN_SET(role_code, p_roles_csv) > 0 AND role_code <> 'SYSTEM';
  SET v_wanted = 1 + CHAR_LENGTH(p_roles_csv) - CHAR_LENGTH(REPLACE(p_roles_csv, ',', ''));
  IF v_found <> v_wanted THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown or forbidden role in the list'; END IF;
  IF FIND_IN_SET('DOCTOR', p_roles_csv) > 0 AND (p_registration_no IS NULL OR p_registration_no = '') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Doctors need a registration number';
  END IF;
  START TRANSACTION;
  CALL next_code('staff', v_code);
  INSERT INTO staff (display_code, principal_kind, given_name, family_name, phone, login_subject)
  VALUES (v_code, 'human', p_given, p_family, p_phone, p_login);
  SET p_staff_id = LAST_INSERT_ID();
  INSERT INTO staff_credential (staff_id, password_hash) VALUES (p_staff_id, p_password_hash);
  INSERT INTO staff_role (staff_id, role_id, granted_by) SELECT p_staff_id, role_id, app_user() FROM `role` WHERE FIND_IN_SET(role_code, p_roles_csv) > 0;
  IF FIND_IN_SET('DOCTOR', p_roles_csv) > 0 THEN INSERT INTO doctor_detail (staff_id, registration_number) VALUES (p_staff_id, p_registration_no); END IF;
  IF FIND_IN_SET('NURSE', p_roles_csv) > 0 THEN INSERT INTO nurse_detail (staff_id, registration_number) VALUES (p_staff_id, NULLIF(p_registration_no, '')); END IF;
  IF FIND_IN_SET('PHARMACY', p_roles_csv) > 0 THEN INSERT INTO pharmacist_detail (staff_id, registration_number) VALUES (p_staff_id, NULLIF(p_registration_no, '')); END IF;
  IF FIND_IN_SET('ACCOUNTANT', p_roles_csv) > 0 THEN INSERT INTO accountant_detail (staff_id) VALUES (p_staff_id); END IF;
  IF FIND_IN_SET('RECEPTIONIST', p_roles_csv) > 0 THEN INSERT INTO receptionist_detail (staff_id) VALUES (p_staff_id); END IF;
  COMMIT;
END$$

-- @api roles=OWNER rules=G9 | Change one clinic setting (audited with the reason)
CREATE PROCEDURE set_setting(IN p_key VARCHAR(60), IN p_value VARCHAR(500), IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('OWNER');
  IF NOT EXISTS (SELECT 1 FROM clinic_settings WHERE setting_key = p_key) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown setting key';
  END IF;
  SET @audit_reason = p_reason;
  UPDATE clinic_settings SET setting_value = p_value WHERE setting_key = p_key;
  SET @audit_reason = NULL;
END$$
