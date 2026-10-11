-- =====================================================================================
-- P3-6  Owner alerts and daily summary, exception reports (R10.5), family links (R11)   (Track B)
-- Scheduled work runs under the SYS-ALERT principal (staff_id 2); public family-link access under SYS-FAMILY (staff_id 3).
-- MySQL EVENTs are created DISABLED. To switch on:  ALTER EVENT ev_check_alerts ENABLE;  (event scheduler is ON via docker-compose)
-- =====================================================================================
USE clinic_db;
DELIMITER $$

-- @api roles=OWNER rules=R10.2,R10.3,R10.4,R7.4,R7.5 | Run all alert checks now (also run by a MySQL EVENT as SYS-ALERT): stays over limit, unreviewed/critical reports, dispense mismatch at billing, expiring batches, lab ordered-not-billed / billed-no-report, unacknowledged handoffs, overdue dues; then escalates unacknowledged interrupts to the backup person
CREATE PROCEDURE sp_check_alerts()
BEGIN
  DECLARE v_h INT; DECLARE v_rl INT; DECLARE v_days INT; DECLARE v_ack INT; DECLARE v_ho INT; DECLARE v_n INT DEFAULT 0; DECLARE v_deleg BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN SET @app_user_id = @o_uid; SET @app_role = @o_role; RESIGNAL; END;
  IF @app_user_id IS NOT NULL THEN CALL int_require_role('OWNER'); END IF;
  SET @o_uid = @app_user_id; SET @o_role = @app_role; SET @app_user_id = 2; SET @app_role = 'SYSTEM';
  SET v_h = setting_int('alert_dedupe_hours'), v_rl = setting_int('report_review_limit_hours'), v_days = setting_int('billed_no_report_days'),
      v_ack = setting_int('owner_ack_timeout_minutes'), v_ho = setting_int('handoff_ack_limit_hours');
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'safety_interrupt', 'STAY_OVER_LIMIT', 'red', 'short_stay_episode', CAST(s.short_stay_episode_id AS CHAR), 'A short stay is over its time limit'
    FROM v_stay_over_limit s WHERE NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'STAY_OVER_LIMIT' AND a.source_entity_id = CAST(s.short_stay_episode_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  SET v_n = v_n + ROW_COUNT();
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'safety_interrupt', 'CRITICAL_REPORT_UNREVIEWED', 'red', 'lab_report', CAST(u.lab_report_id AS CHAR), 'A critical lab report is still not reviewed by a doctor'
    FROM v_report_unreviewed u WHERE u.is_critical = 1 AND u.hours_waiting >= v_rl
     AND NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'CRITICAL_REPORT_UNREVIEWED' AND a.source_entity_id = CAST(u.lab_report_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  SET v_n = v_n + ROW_COUNT();
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'safety_interrupt', 'DISPENSE_MISMATCH', 'red', 'visit', CAST(r.visit_id AS CHAR), 'Dispensed medicine does not reconcile at the billing stage'
    FROM (SELECT DISTINCT x.visit_id FROM v_dispense_reconciliation x JOIN v_visit_current c ON c.visit_id = x.visit_id WHERE x.mismatch_qty <> 0 AND c.current_status = 'billing') r
   WHERE NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'DISPENSE_MISMATCH' AND a.source_entity_id = CAST(r.visit_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  SET v_n = v_n + ROW_COUNT();
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'daily_summary_item', 'REPORT_UNREVIEWED', 'amber', 'lab_report', CAST(u.lab_report_id AS CHAR), 'A lab report is waiting for the doctor review'
    FROM v_report_unreviewed u WHERE u.is_critical = 0 AND u.hours_waiting >= v_rl
     AND NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'REPORT_UNREVIEWED' AND a.source_entity_id = CAST(u.lab_report_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'daily_summary_item', 'BATCH_EXPIRING', 'amber', 'stock_batch', CAST(e.stock_batch_id AS CHAR), 'A stock batch is close to expiry or expired'
    FROM v_expiring_soon e WHERE NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'BATCH_EXPIRING' AND a.source_entity_id = CAST(e.stock_batch_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'daily_summary_item', 'LAB_NOT_BILLED', 'amber', 'lab_order', CAST(x.lab_order_id AS CHAR), 'A lab test was ordered but not billed'
    FROM v_ex_ordered_not_billed x WHERE NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'LAB_NOT_BILLED' AND a.source_entity_id = CAST(x.lab_order_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'daily_summary_item', 'LAB_BILLED_NO_REPORT', 'amber', 'lab_order', CAST(x.lab_order_id AS CHAR), 'A billed lab test has no report after the allowed days'
    FROM v_ex_billed_no_report x WHERE NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'LAB_BILLED_NO_REPORT' AND a.source_entity_id = CAST(x.lab_order_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'daily_summary_item', 'HANDOFF_UNACKNOWLEDGED', 'amber', 'custody_handoff', CAST(h.custody_handoff_id AS CHAR), 'A medicine handoff was not acknowledged'
    FROM v_unacknowledged_handoffs h WHERE h.hours_waiting >= v_ho AND NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'HANDOFF_UNACKNOWLEDGED' AND a.source_entity_id = CAST(h.custody_handoff_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  INSERT INTO alert_event (alert_class, alert_type, severity, source_entity_type, source_entity_id, summary)
  SELECT 'daily_summary_item', 'DUE_OVERDUE', 'amber', 'due_promise', CAST(d.due_promise_id AS CHAR), 'A promised payment is overdue'
    FROM v_due_list d WHERE d.days_overdue > 0 AND NOT EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_type = 'DUE_OVERDUE' AND a.source_entity_id = CAST(d.due_promise_id AS CHAR) AND a.recorded_at > NOW(3) - INTERVAL v_h HOUR);
  IF v_n > 0 THEN CALL queue_message(setting_text('owner_phone'), 'alert_notice', 'The clinic needs your attention. Please open the dashboard.', NULL, NULL); END IF;
  -- escalation of unanswered interrupts (R10.4)
  SELECT d.delegate_staff_id INTO v_deleg FROM authority_delegation d
   WHERE d.scope IN ('alert_escalation_backup', 'approval_backup') AND NOW(3) BETWEEN d.valid_from AND d.valid_to
     AND NOT EXISTS (SELECT 1 FROM authority_delegation_revocation r WHERE r.authority_delegation_id = d.authority_delegation_id)
   ORDER BY d.authority_delegation_id DESC LIMIT 1;
  IF v_deleg IS NOT NULL THEN
    INSERT INTO alert_escalation (alert_event_id, escalated_to_staff_id, reason)
    SELECT a.alert_event_id, v_deleg, 'No acknowledgement within the timeout' FROM alert_event a
     WHERE a.alert_class = 'safety_interrupt' AND a.recorded_at < NOW(3) - INTERVAL v_ack MINUTE
       AND NOT EXISTS (SELECT 1 FROM alert_acknowledgement k WHERE k.alert_event_id = a.alert_event_id)
       AND NOT EXISTS (SELECT 1 FROM alert_escalation e WHERE e.alert_event_id = a.alert_event_id);
    IF ROW_COUNT() > 0 THEN CALL queue_message((SELECT phone FROM staff WHERE staff_id = v_deleg), 'alert_notice', 'The clinic needs your attention. Please open the dashboard.', NULL, NULL); END IF;
  ELSEIF EXISTS (SELECT 1 FROM alert_event a WHERE a.alert_class = 'safety_interrupt' AND a.recorded_at < NOW(3) - INTERVAL v_ack MINUTE
                  AND NOT EXISTS (SELECT 1 FROM alert_acknowledgement k WHERE k.alert_event_id = a.alert_event_id)) THEN
    CALL raise_alert('ESCALATION_NO_BACKUP', 'SUMMARY', 'alert_event', NULL, 'An interrupt is unanswered and no backup person is delegated');
    CALL queue_message(setting_text('backup_owner_phone'), 'alert_notice', 'The clinic needs your attention. Please open the dashboard.', NULL, NULL);
  END IF;
  SET @app_user_id = @o_uid; SET @app_role = @o_role;
END$$

-- @api roles=* rules=R3.6 | Generate appointment slots for all current schedule versions for schedule_horizon_days (run daily by an EVENT; recorded as SYS-SCHEDULER)
CREATE PROCEDURE sp_extend_slot_horizon()
BEGIN
  DECLARE v_id BIGINT UNSIGNED DEFAULT 0; DECLARE v_next BIGINT UNSIGNED;
  loop_all: WHILE TRUE DO
    SET v_next = NULL;
    SELECT d.doctor_schedule_id INTO v_next FROM doctor_schedule d
     WHERE d.doctor_schedule_id > v_id AND d.schedule_state = 'active'
       AND NOT EXISTS (SELECT 1 FROM doctor_schedule n WHERE n.supersedes_doctor_schedule_id = d.doctor_schedule_id AND n.valid_from <= CURRENT_DATE)
     ORDER BY d.doctor_schedule_id LIMIT 1;
    IF v_next IS NULL THEN LEAVE loop_all; END IF;
    CALL int_generate_slots(v_next);
    SET v_id = v_next;
  END WHILE;
END$$

-- @api roles=OWNER rules=R10.4 | Acknowledge an alert (owner, or a person with a valid backup delegation)
CREATE PROCEDURE sp_acknowledge_alert(IN p_alert BIGINT UNSIGNED, IN p_note VARCHAR(300))
BEGIN
  IF app_role() <> 'OWNER' AND NOT EXISTS (SELECT 1 FROM authority_delegation d WHERE d.delegate_staff_id = app_user() AND d.scope IN ('alert_escalation_backup', 'approval_backup')
        AND NOW(3) BETWEEN d.valid_from AND d.valid_to AND NOT EXISTS (SELECT 1 FROM authority_delegation_revocation r WHERE r.authority_delegation_id = d.authority_delegation_id)) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the owner or a delegated backup can acknowledge alerts';
  END IF;
  IF EXISTS (SELECT 1 FROM alert_acknowledgement WHERE alert_event_id = p_alert) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This alert is already acknowledged'; END IF;
  INSERT INTO alert_acknowledgement (alert_event_id, note) VALUES (p_alert, p_note);
END$$

-- ------------------------------------------------------------------ family links
-- @api roles=RECEPTIONIST,NURSE,DOCTOR rules=R11.1,R11.2,R11.3,R11.4 | Create a read-only link for ONE visit. Returns the plain token (shown once; only its SHA-256 is stored) and, for printed_code, the printed code. Consent note mandatory. If the attendant has a phone a simulated SMS is queued with the link only
CREATE PROCEDURE sp_create_family_link(IN p_visit BIGINT UNSIGNED, IN p_attendant BIGINT UNSIGNED, IN p_kind VARCHAR(15), IN p_consent VARCHAR(300),
                                       OUT p_token VARCHAR(64), OUT p_printed_code VARCHAR(12), OUT p_link_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_dob DATE; DECLARE v_reg VARCHAR(20); DECLARE v_phone VARCHAR(20); DECLARE v_days INT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST,NURSE,DOCTOR');
  IF p_consent IS NULL OR CHAR_LENGTH(TRIM(p_consent)) < 3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Record who received the link and that they agreed (consent note)'; END IF;
  IF p_kind NOT IN ('date_of_birth', 'printed_code') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Second factor must be date_of_birth or printed_code'; END IF;
  SELECT p.date_of_birth, p.registration_kind INTO v_dob, v_reg FROM visit v JOIN patient p ON p.patient_id = canonical_patient(v.patient_id) WHERE v.visit_id = p_visit;
  IF v_reg IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown visit'; END IF;
  IF p_kind = 'date_of_birth' AND v_dob IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This patient has no date of birth yet: use a printed code'; END IF;
  SET v_days = setting_int('link_expiry_days');
  SET p_token = LOWER(HEX(RANDOM_BYTES(24)));
  SET p_printed_code = IF(p_kind = 'printed_code', UPPER(SUBSTRING(HEX(RANDOM_BYTES(5)), 1, 8)), NULL);
  START TRANSACTION;
  INSERT INTO family_link (visit_id, attendant_id, token_hash, second_factor_kind, printed_code_hash, expires_at, consent_note)
  VALUES (p_visit, p_attendant, SHA2(p_token, 256), p_kind, IF(p_printed_code IS NULL, NULL, SHA2(p_printed_code, 256)), NOW(3) + INTERVAL v_days DAY, p_consent);
  SET p_link_id = LAST_INSERT_ID();
  IF p_attendant IS NOT NULL THEN
    SELECT phone INTO v_phone FROM patient_attendant WHERE patient_attendant_id = p_attendant;
    CALL queue_message(v_phone, 'family_link', CONCAT('Your visit details from ', setting_text('clinic_name'), ': ', setting_text('public_base_url'), '/v/', p_token), 'family_link', p_link_id);
  END IF;
  COMMIT;
END$$

-- @api roles=RECEPTIONIST,NURSE,DOCTOR,OWNER rules=R11.2 | Revoke a link instantly (the link row is untouched; a revocation event is added)
CREATE PROCEDURE sp_revoke_family_link(IN p_link BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('RECEPTIONIST,NURSE,DOCTOR,OWNER');
  INSERT INTO family_link_revocation (family_link_id, reason) VALUES (p_link, p_reason);
END$$

-- @api roles=family rules=R11.1,R11.4 | PUBLIC entry for a family link (runs under SYS-FAMILY). Checks token + second factor (DOB yyyy-mm-dd or printed code). outcome: ok | invalid | expired | revoked | locked | bad_credential. After link_max_failed_attempts failures the link locks with a doubling delay. EVERY attempt is logged
CREATE PROCEDURE sp_open_family_link(IN p_token VARCHAR(80), IN p_factor VARCHAR(40), IN p_source VARCHAR(100),
                                     OUT p_outcome VARCHAR(15), OUT p_visit_id BIGINT UNSIGNED, OUT p_link_id BIGINT UNSIGNED, OUT p_retry_seconds INT)
BEGIN
  DECLARE v_hash CHAR(64); DECLARE v_fp VARCHAR(16); DECLARE v_kind VARCHAR(15); DECLARE v_pc CHAR(64); DECLARE v_exp DATETIME(3); DECLARE v_max INT; DECLARE v_base INT;
  DECLARE v_fails INT; DECLARE v_last DATETIME(3); DECLARE v_delay INT; DECLARE v_ok INT DEFAULT 0; DECLARE v_unlock DATETIME(3);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN SET @app_user_id = @o_uid; SET @app_role = @o_role; RESIGNAL; END;
  SET @o_uid = @app_user_id; SET @o_role = @app_role; SET @app_user_id = 3; SET @app_role = 'SYSTEM';
  SET p_outcome = NULL, p_visit_id = NULL, p_link_id = NULL, p_retry_seconds = 0;
  SET v_hash = SHA2(LOWER(TRIM(p_token)), 256), v_fp = LEFT(v_hash, 16), v_max = setting_int('link_max_failed_attempts'), v_base = setting_int('link_lock_base_minutes');
  SELECT family_link_id, visit_id, second_factor_kind, printed_code_hash, expires_at INTO p_link_id, p_visit_id, v_kind, v_pc, v_exp FROM family_link WHERE token_hash = v_hash;
  IF p_link_id IS NULL THEN
    SET p_outcome = 'invalid';
    INSERT INTO link_access_log (family_link_id, token_fingerprint, success, failure_reason, source_context) VALUES (NULL, v_fp, 0, 'invalid', p_source);
  ELSEIF EXISTS (SELECT 1 FROM family_link_revocation WHERE family_link_id = p_link_id) THEN
    SET p_outcome = 'revoked', p_visit_id = NULL;
    INSERT INTO link_access_log (family_link_id, token_fingerprint, success, failure_reason, source_context) VALUES (p_link_id, v_fp, 0, 'revoked', p_source);
  ELSEIF v_exp < NOW(3) THEN
    SET p_outcome = 'expired', p_visit_id = NULL;
    INSERT INTO link_access_log (family_link_id, token_fingerprint, success, failure_reason, source_context) VALUES (p_link_id, v_fp, 0, 'expired', p_source);
  ELSE
    SELECT COUNT(*), MAX(recorded_at) INTO v_fails, v_last FROM link_access_log
     WHERE family_link_id = p_link_id AND success = 0 AND failure_reason = 'bad_credential'
       AND link_access_log_id > COALESCE((SELECT MAX(s.link_access_log_id) FROM link_access_log s WHERE s.family_link_id = p_link_id AND s.success = 1), 0);
    IF v_fails >= v_max THEN
      SET v_delay = LEAST(v_base * POW(2, v_fails - v_max), 1440), v_unlock = v_last + INTERVAL v_delay MINUTE;
      IF NOW(3) < v_unlock THEN
        SET p_outcome = 'locked', p_visit_id = NULL, p_retry_seconds = TIMESTAMPDIFF(SECOND, NOW(3), v_unlock);
        INSERT INTO link_access_log (family_link_id, token_fingerprint, success, failure_reason, source_context) VALUES (p_link_id, v_fp, 0, 'locked', p_source);
      END IF;
    END IF;
    IF p_outcome IS NULL THEN
      IF v_kind = 'date_of_birth' THEN
        SELECT COUNT(*) INTO v_ok FROM visit v JOIN patient p ON p.patient_id = canonical_patient(v.patient_id)
         WHERE v.visit_id = p_visit_id AND p.date_of_birth = STR_TO_DATE(TRIM(p_factor), '%Y-%m-%d');
      ELSE
        SET v_ok = (v_pc IS NOT NULL AND v_pc = SHA2(UPPER(TRIM(p_factor)), 256));
      END IF;
      IF v_ok > 0 THEN
        SET p_outcome = 'ok';
        INSERT INTO link_access_log (family_link_id, token_fingerprint, success, failure_reason, source_context) VALUES (p_link_id, v_fp, 1, NULL, p_source);
      ELSE
        SET p_outcome = 'bad_credential', p_visit_id = NULL;
        INSERT INTO link_access_log (family_link_id, token_fingerprint, success, failure_reason, source_context) VALUES (p_link_id, v_fp, 0, 'bad_credential', p_source);
      END IF;
    END IF;
  END IF;
  SET @app_user_id = @o_uid; SET @app_role = @o_role;
END$$


-- ------------------------------------------------------------------ owner administration: delegation and staff roles
-- @api roles=OWNER rules=R2.3,R9.1,R10.4,G6 | Delegate limited approval authority (scope discount | partial_payment | approval_backup | alert_escalation_backup) to another person for a time window, with an optional amount limit
CREATE PROCEDURE sp_delegate_authority(IN p_delegate BIGINT UNSIGNED, IN p_scope VARCHAR(30), IN p_limit DECIMAL(12,2), IN p_from DATETIME, IN p_to DATETIME, OUT p_delegation_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('OWNER');
  IF p_delegate = app_user() THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'You cannot delegate to yourself'; END IF;
  IF NOT EXISTS (SELECT 1 FROM staff WHERE staff_id = p_delegate AND principal_kind = 'human') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The delegate must be a staff member'; END IF;
  INSERT INTO authority_delegation (delegate_staff_id, scope, limit_amount, valid_from, valid_to) VALUES (p_delegate, p_scope, p_limit, p_from, p_to);
  SET p_delegation_id = LAST_INSERT_ID();
END$$

-- @api roles=OWNER rules=G6 | Revoke a delegation immediately (decisions already made stay valid)
CREATE PROCEDURE sp_revoke_delegation(IN p_delegation BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('OWNER');
  INSERT INTO authority_delegation_revocation (authority_delegation_id, reason) VALUES (p_delegation, p_reason);
END$$

-- @api roles=OWNER rules=R2.1 | Give a staff member an additional role from today (staff_role is a master row, audited)
CREATE PROCEDURE sp_grant_role(IN p_staff BIGINT UNSIGNED, IN p_role_code VARCHAR(20), IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('OWNER');
  IF p_role_code IN ('SYSTEM') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This role cannot be granted'; END IF;
  SET @audit_reason = p_reason;
  INSERT INTO staff_role (staff_id, role_id, granted_by) SELECT p_staff, role_id, app_user() FROM `role` WHERE role_code = p_role_code;
  IF ROW_COUNT() = 0 THEN SET @audit_reason = NULL; SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown role'; END IF;
  SET @audit_reason = NULL;
END$$

-- @api roles=OWNER rules=R2.1 | End a staff member's role from today (history kept)
CREATE PROCEDURE sp_end_role(IN p_staff BIGINT UNSIGNED, IN p_role_code VARCHAR(20), IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('OWNER');
  SET @audit_reason = p_reason;
  UPDATE staff_role sr JOIN `role` r ON r.role_id = sr.role_id SET sr.valid_to = CURRENT_DATE - INTERVAL 1 DAY
   WHERE sr.staff_id = p_staff AND r.role_code = p_role_code AND (sr.valid_to IS NULL OR sr.valid_to >= CURRENT_DATE);
  SET @audit_reason = NULL;
END$$

-- @api roles=OWNER rules=R2.1,R12.4 | Deactivate a staff member from today (no deletion). The owner cannot deactivate himself
CREATE PROCEDURE sp_deactivate_staff(IN p_staff BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('OWNER');
  IF p_staff = app_user() THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'You cannot deactivate yourself'; END IF;
  SET @audit_reason = p_reason;
  UPDATE staff SET active_to = CURRENT_DATE - INTERVAL 1 DAY WHERE staff_id = p_staff AND principal_kind = 'human';
  SET @audit_reason = NULL;
END$$

DELIMITER ;

-- ------------------------------------------------------------------ events (created DISABLED)
CREATE EVENT IF NOT EXISTS ev_check_alerts ON SCHEDULE EVERY 5 MINUTE DISABLE COMMENT 'runs sp_check_alerts as SYS-ALERT' DO CALL sp_check_alerts();
CREATE EVENT IF NOT EXISTS ev_extend_slots ON SCHEDULE EVERY 1 DAY DISABLE COMMENT 'keeps appointment slots generated' DO CALL sp_extend_slot_horizon();

-- ------------------------------------------------------------------ owner views
CREATE OR REPLACE VIEW v_alerts_open AS
SELECT a.alert_event_id, a.alert_class, a.alert_type, a.severity, a.summary, a.source_entity_type, a.source_entity_id, a.recorded_at,
       TIMESTAMPDIFF(MINUTE, a.recorded_at, NOW(3)) AS minutes_open,
       EXISTS (SELECT 1 FROM alert_acknowledgement k WHERE k.alert_event_id = a.alert_event_id) AS acknowledged,
       EXISTS (SELECT 1 FROM alert_escalation e WHERE e.alert_event_id = a.alert_event_id) AS escalated
  FROM alert_event a
 WHERE (a.alert_class = 'safety_interrupt' AND NOT EXISTS (SELECT 1 FROM alert_acknowledgement k WHERE k.alert_event_id = a.alert_event_id))
    OR (a.alert_class = 'approval_pending' AND NOT EXISTS (SELECT 1 FROM approval_decision d WHERE CAST(d.approval_request_id AS CHAR) = a.source_entity_id));

CREATE OR REPLACE VIEW v_ex_ordered_not_billed AS
SELECT o.lab_order_id, o.visit_id, o.test_name, o.occurred_at FROM lab_order o
 WHERE NOT EXISTS (SELECT 1 FROM bill_line l WHERE l.lab_order_id = o.lab_order_id)
   AND COALESCE((SELECT s.status FROM lab_order_status s WHERE s.lab_order_id = o.lab_order_id ORDER BY s.lab_order_status_id DESC LIMIT 1), 'ordered') <> 'cancelled';

CREATE OR REPLACE VIEW v_ex_billed_no_report AS
SELECT o.lab_order_id, o.visit_id, o.test_name, o.occurred_at, TIMESTAMPDIFF(DAY, o.occurred_at, NOW(3)) AS days_waiting FROM lab_order o
 WHERE EXISTS (SELECT 1 FROM bill_line l WHERE l.lab_order_id = o.lab_order_id)
   AND NOT EXISTS (SELECT 1 FROM lab_report r WHERE r.lab_order_id = o.lab_order_id)
   AND COALESCE((SELECT s.status FROM lab_order_status s WHERE s.lab_order_id = o.lab_order_id ORDER BY s.lab_order_status_id DESC LIMIT 1), 'ordered') <> 'cancelled'
   AND o.occurred_at < NOW(3) - INTERVAL setting_int('billed_no_report_days') DAY;

CREATE OR REPLACE VIEW v_ex_discounts_by_staff AS
SELECT ar.recorded_by AS staff_id, DATE_FORMAT(ar.recorded_at, '%Y-%m') AS month, COUNT(*) AS requests, SUM(ar.requested_amount) AS total_amount
  FROM approval_request ar WHERE ar.request_type IN ('discount', 'free_service') GROUP BY ar.recorded_by, DATE_FORMAT(ar.recorded_at, '%Y-%m');

CREATE OR REPLACE VIEW v_ex_free_services AS
SELECT l.bill_id, l.line_no, l.line_amount, l.reason, l.recorded_by, l.recorded_at FROM bill_line l WHERE l.line_type = 'free_service';

CREATE OR REPLACE VIEW v_ex_refunds AS
SELECT r.refund_id, r.bill_id, r.amount, r.method, r.recorded_by, r.recorded_at FROM refund r
UNION ALL SELECT f.counter_sale_refund_id, NULL, f.amount, f.method, f.recorded_by, f.recorded_at FROM counter_sale_refund f;

CREATE OR REPLACE VIEW v_ex_backdated_entries AS
SELECT 'payment' AS source_table, p.payment_id AS row_id, p.occurred_at, p.recorded_at, p.recorded_by, p.is_backfilled FROM payment p WHERE p.is_backfilled = 1 OR p.recorded_at > p.occurred_at + INTERVAL 1 HOUR
UNION ALL SELECT 'dose_administration', d.dose_administration_id, d.occurred_at, d.recorded_at, d.recorded_by, d.is_backfilled FROM dose_administration d WHERE d.is_backfilled = 1 OR d.recorded_at > d.occurred_at + INTERVAL 1 HOUR
UNION ALL SELECT 'stock_movement', m.stock_movement_id, m.occurred_at, m.recorded_at, m.recorded_by, m.is_backfilled FROM stock_movement m WHERE m.is_backfilled = 1 OR m.recorded_at > m.occurred_at + INTERVAL 1 HOUR;

CREATE OR REPLACE VIEW v_ex_edits_after_payment AS
SELECT l.bill_id, l.line_no, l.line_type, l.line_amount, l.recorded_by, l.recorded_at FROM bill_line l
 WHERE l.line_type IN ('post_finalization_credit', 'post_finalization_debit') AND EXISTS (SELECT 1 FROM payment p WHERE p.bill_id = l.bill_id AND p.recorded_at < l.recorded_at)
UNION ALL SELECT p.bill_id, p.payment_id, 'payment_reversal', -p.amount, p.recorded_by, p.recorded_at FROM payment p WHERE p.payment_kind = 'reversal';

CREATE OR REPLACE VIEW v_ex_shared_logins AS
SELECT e.staff_id, DATE(e.recorded_at) AS login_day, COUNT(DISTINCT e.source_context) AS distinct_sources, COUNT(*) AS logins
  FROM login_session_event e WHERE e.event_type = 'login_success' AND e.staff_id IS NOT NULL
 GROUP BY e.staff_id, DATE(e.recorded_at) HAVING COUNT(DISTINCT e.source_context) >= 2;

CREATE OR REPLACE VIEW v_ex_otc_patterns AS
SELECT d.recorded_by AS pharmacist_id, DATE(d.recorded_at) AS sale_day, COUNT(*) AS otc_dispensings
  FROM dispensing d WHERE d.source_type = 'otc' GROUP BY d.recorded_by, DATE(d.recorded_at);

CREATE OR REPLACE VIEW v_ex_stock_adjustments AS
SELECT m.stock_movement_id, m.stock_batch_id, m.movement_type, m.quantity_delta, m.reason, m.recorded_by, m.recorded_at FROM stock_movement m
 WHERE m.movement_type IN ('adjustment', 'expired_writeoff', 'reversal', 'returned_to_supplier');

CREATE OR REPLACE VIEW v_ex_manual_prices AS
SELECT 'bill_line' AS source, l.bill_id AS ref_id, l.description_snapshot AS what, l.unit_price_snapshot AS price, l.reason, l.recorded_by, l.recorded_at FROM bill_line l WHERE l.is_manual_price = 1
UNION ALL SELECT 'lab_price', p.lab_order_id, 'lab price off list', p.entered_price, p.deviation_reason, p.recorded_by, p.recorded_at FROM lab_order_price p WHERE p.is_price_deviation = 1 OR p.price_basis = 'manual';

CREATE OR REPLACE VIEW v_ex_leftover_returns AS
SELECT d.recorded_by AS pharmacist_id, d.decision, COUNT(*) AS decisions, DATE_FORMAT(d.recorded_at, '%Y-%m') AS month FROM leftover_return_decision d GROUP BY d.recorded_by, d.decision, DATE_FORMAT(d.recorded_at, '%Y-%m');

CREATE OR REPLACE VIEW v_ex_weak_rx_flagged_drugs AS
SELECT o.outside_prescription_id, o.counter_customer_id, o.prescriber_name, o.prescriber_registration_no, o.photo_path, d.medicine_id, m.brand_name, m.schedule_class, d.recorded_by, d.recorded_at
  FROM dispensing d JOIN outside_prescription o ON o.outside_prescription_id = d.outside_prescription_id JOIN medicine m ON m.medicine_id = d.medicine_id
 WHERE m.schedule_class <> 'none' AND (o.photo_path IS NULL OR o.prescriber_registration_no IS NULL);

CREATE OR REPLACE VIEW v_owner_checks AS
SELECT 'approvals_waiting' AS check_code, 'red' AS severity, 'Approval requests waiting for a decision' AS title, (SELECT COUNT(*) FROM v_approval_inbox WHERE decision IS NULL) AS cnt
UNION ALL SELECT 'interrupts_unacknowledged', 'red', 'Patient-safety interrupts not acknowledged', (SELECT COUNT(*) FROM v_alerts_open WHERE alert_class = 'safety_interrupt')
UNION ALL SELECT 'stays_over_limit', 'red', 'Short stays over the time limit', (SELECT COUNT(*) FROM v_stay_over_limit)
UNION ALL SELECT 'critical_reports_unreviewed', 'red', 'Critical reports not reviewed', (SELECT COUNT(*) FROM v_report_unreviewed WHERE is_critical = 1)
UNION ALL SELECT 'dispense_mismatch_billing', 'red', 'Medicine not reconciled at billing stage', (SELECT COUNT(DISTINCT x.visit_id) FROM v_dispense_reconciliation x JOIN v_visit_current c ON c.visit_id = x.visit_id WHERE x.mismatch_qty <> 0 AND c.current_status = 'billing')
UNION ALL SELECT 'dues_overdue', 'red', 'Promised payments overdue', (SELECT COUNT(*) FROM v_due_list WHERE days_overdue > 0)
UNION ALL SELECT 'discounts_today', 'amber', 'Discounts / free services requested today', (SELECT COUNT(*) FROM approval_request WHERE request_type IN ('discount', 'free_service') AND DATE(recorded_at) = CURRENT_DATE)
UNION ALL SELECT 'refunds_7d', 'amber', 'Refunds in the last 7 days', (SELECT COUNT(*) FROM v_ex_refunds WHERE recorded_at >= NOW(3) - INTERVAL 7 DAY)
UNION ALL SELECT 'stock_adjustments_7d', 'amber', 'Stock adjustments / write-offs / reversals in the last 7 days', (SELECT COUNT(*) FROM v_ex_stock_adjustments WHERE recorded_at >= NOW(3) - INTERVAL 7 DAY)
UNION ALL SELECT 'manual_prices_7d', 'amber', 'Manual or deviating prices in the last 7 days', (SELECT COUNT(*) FROM v_ex_manual_prices WHERE recorded_at >= NOW(3) - INTERVAL 7 DAY)
UNION ALL SELECT 'backdated_entries_7d', 'amber', 'Backfilled or late entries in the last 7 days', (SELECT COUNT(*) FROM v_ex_backdated_entries WHERE recorded_at >= NOW(3) - INTERVAL 7 DAY)
UNION ALL SELECT 'otc_heavy_days', 'amber', 'Pharmacist-days with many OTC sales', (SELECT COUNT(*) FROM v_ex_otc_patterns WHERE otc_dispensings >= setting_int('otc_daily_alert_count') AND sale_day >= CURRENT_DATE - INTERVAL 7 DAY)
UNION ALL SELECT 'shared_logins_7d', 'amber', 'Logins from several places on one day (possible shared login)', (SELECT COUNT(*) FROM v_ex_shared_logins WHERE login_day >= CURRENT_DATE - INTERVAL 7 DAY)
UNION ALL SELECT 'reports_unreviewed', 'amber', 'Non-critical reports waiting for review', (SELECT COUNT(*) FROM v_report_unreviewed WHERE is_critical = 0 AND hours_waiting >= setting_int('report_review_limit_hours'))
UNION ALL SELECT 'lab_ordered_not_billed', 'amber', 'Lab tests ordered but not billed', (SELECT COUNT(*) FROM v_ex_ordered_not_billed)
UNION ALL SELECT 'lab_billed_no_report', 'amber', 'Billed lab tests without a report', (SELECT COUNT(*) FROM v_ex_billed_no_report)
UNION ALL SELECT 'outside_while_in_stock_7d', 'amber', 'Medicines bought outside although in stock', (SELECT COUNT(*) FROM alert_event WHERE alert_type = 'OUTSIDE_WHILE_IN_STOCK' AND recorded_at >= NOW(3) - INTERVAL 7 DAY)
UNION ALL SELECT 'weak_rx_flagged_drugs_7d', 'amber', 'Scheduled medicines sold on weak outside prescriptions', (SELECT COUNT(*) FROM v_ex_weak_rx_flagged_drugs WHERE recorded_at >= NOW(3) - INTERVAL 7 DAY)
UNION ALL SELECT 'handoffs_unacknowledged', 'amber', 'Medicine handoffs not acknowledged', (SELECT COUNT(*) FROM v_unacknowledged_handoffs WHERE hours_waiting >= setting_int('handoff_ack_limit_hours'))
UNION ALL SELECT 'batches_expiring', 'amber', 'Batches expired or close to expiry', (SELECT COUNT(*) FROM v_expiring_soon);

-- Owner daily summary: red (needs decision) / amber (unusual) / clean, ranked
CREATE OR REPLACE VIEW v_owner_summary AS
SELECT c.check_code, c.title, c.cnt, IF(c.cnt = 0, 'clean', c.severity) AS status,
       CASE WHEN c.cnt = 0 THEN 3 WHEN c.severity = 'red' THEN 1 ELSE 2 END AS rank_no
  FROM v_owner_checks c;

-- ------------------------------------------------------------------ family-facing views (only for a currently valid link; no cost prices, no internal notes)
CREATE OR REPLACE VIEW v_family_link_valid AS
SELECT l.family_link_id, l.visit_id FROM family_link l
 WHERE l.expires_at > NOW(3) AND NOT EXISTS (SELECT 1 FROM family_link_revocation r WHERE r.family_link_id = l.family_link_id);

CREATE OR REPLACE VIEW v_family_visit AS
SELECT k.family_link_id, k.visit_id, CONCAT_WS(' ', p.given_name, p.family_name) AS patient_name, c.opened_at, c.current_status,
       setting_text('clinic_name') AS clinic_name
  FROM v_family_link_valid k JOIN v_visit_current c ON c.visit_id = k.visit_id JOIN patient p ON p.patient_id = c.canonical_patient_id;

CREATE OR REPLACE VIEW v_family_prescription AS
SELECT k.family_link_id, c.line_no, c.medicine_name, c.chosen_dose_text AS dose, c.chosen_schedule AS schedule, c.food_instruction, c.duration_days
  FROM v_family_link_valid k JOIN v_prescription_current c ON c.visit_id = k.visit_id
 WHERE c.item_change <> 'discontinued' AND COALESCE(c.line_status, 'prescribed') <> 'cancelled';

CREATE OR REPLACE VIEW v_family_reports AS
SELECT k.family_link_id, o.test_name, r.version_number, r.occurred_at AS received_at, r.file_path,
       EXISTS (SELECT 1 FROM lab_report_review v WHERE v.lab_report_id = r.lab_report_id) AS reviewed_by_doctor
  FROM v_family_link_valid k JOIN lab_order o ON o.visit_id = k.visit_id JOIN lab_report r ON r.lab_order_id = o.lab_order_id
 WHERE r.version_number = (SELECT MAX(r2.version_number) FROM lab_report r2 WHERE r2.lab_order_id = o.lab_order_id)
   AND NOT EXISTS (SELECT 1 FROM lab_report_retraction x WHERE x.lab_report_id = r.lab_report_id);

CREATE OR REPLACE VIEW v_family_bill AS
SELECT k.family_link_id, l.line_no, l.description_snapshot AS description, l.quantity, l.unit_price_snapshot AS unit_price, l.line_amount
  FROM v_family_link_valid k JOIN bill b ON b.visit_id = k.visit_id JOIN bill_line l ON l.bill_id = b.bill_id;

CREATE OR REPLACE VIEW v_family_bill_total AS
SELECT k.family_link_id, s.bill_number, s.net_total, s.paid_total, s.balance, s.state
  FROM v_family_link_valid k JOIN v_bill_summary s ON s.visit_id = k.visit_id;

CREATE OR REPLACE VIEW v_family_receipts AS
SELECT k.family_link_id, p.receipt_number, p.payment_kind, p.amount, p.method, p.occurred_at AS paid_at
  FROM v_family_link_valid k JOIN bill b ON b.visit_id = k.visit_id JOIN payment p ON p.bill_id = b.bill_id;

CREATE OR REPLACE VIEW v_family_medicine_count AS
SELECT k.family_link_id, m.brand_name, m.supplied_qty, m.given_qty, m.remaining_qty
  FROM v_family_link_valid k JOIN v_visit_medicine_count m ON m.visit_id = k.visit_id;

CREATE OR REPLACE VIEW v_family_notes AS
SELECT k.family_link_id, 'note' AS kind, n.note_text AS body, n.occurred_at AS at_time
  FROM v_family_link_valid k JOIN clinical_note n ON n.visit_id = k.visit_id
 WHERE n.sharing_classification = 'shareable' AND NOT EXISTS (SELECT 1 FROM clinical_note x WHERE x.corrects_id = n.clinical_note_id)
UNION ALL
SELECT k.family_link_id, 'discharge_instructions', s.discharge_instructions, s.occurred_at
  FROM v_family_link_valid k JOIN clinical_signoff s ON s.visit_id = k.visit_id
 WHERE s.discharge_instructions IS NOT NULL AND NOT EXISTS (SELECT 1 FROM clinical_signoff x WHERE x.corrects_id = s.clinical_signoff_id);
