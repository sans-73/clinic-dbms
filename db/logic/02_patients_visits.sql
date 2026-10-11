-- =====================================================================================
-- P3-2  Patients, scheduling, appointments, visits, short stay, vitals   (Track B)
-- Convention: sp_* = public procedure. Procedures marked "own txn" open/commit their own transaction,
-- so call them in autocommit mode (not inside an open transaction). int_* = internal helpers (not granted to roles).
-- Public API (see docs/db_api/patients_visits.md, generated from the @api comments):
--   levenshtein, name_similarity, patient_match_tier, sp_find_patient_candidates, sp_register_patient,
--   sp_register_unknown_patient, sp_add_patient_phone, sp_add_patient_attendant, sp_update_demographics, sp_merge_patients,
--   sp_add_doctor_schedule, sp_request_schedule_change, sp_apply_schedule_change, sp_book_appointment, sp_reschedule_appointment,
--   sp_set_appointment_status, sp_create_visit, sp_add_visit_status, sp_start_short_stay, sp_close_short_stay,
--   sp_request_stay_extension, sp_apply_stay_extension, sp_record_vitals
--   views: v_patient_alias, v_patient_search, v_appointment_current, v_free_slots, v_visit_current, v_queue_today, v_stay_status, v_stay_over_limit, v_opd_visit
-- =====================================================================================
USE clinic_db;
DELIMITER $$

-- @api roles=* rules=R1.4 | Levenshtein edit distance (inputs cut to 60 chars)
CREATE FUNCTION levenshtein(a VARCHAR(255), b VARCHAR(255)) RETURNS INT DETERMINISTIC
BEGIN
  DECLARE s1 VARCHAR(60); DECLARE s2 VARCHAR(60);
  DECLARE s1_len INT; DECLARE s2_len INT; DECLARE i INT; DECLARE j INT; DECLARE c INT; DECLARE c_temp INT; DECLARE cost INT;
  DECLARE s1_char CHAR(1); DECLARE cv0 VARBINARY(256); DECLARE cv1 VARBINARY(256);
  SET s1 = LEFT(a, 60), s2 = LEFT(b, 60);
  SET s1_len = CHAR_LENGTH(s1), s2_len = CHAR_LENGTH(s2), cv1 = 0x00, j = 1, i = 1, c = 0;
  IF s1 = s2 THEN RETURN 0;
  ELSEIF s1_len = 0 THEN RETURN s2_len;
  ELSEIF s2_len = 0 THEN RETURN s1_len;
  END IF;
  WHILE j <= s2_len DO
    SET cv1 = CONCAT(cv1, UNHEX(HEX(j))), j = j + 1;
  END WHILE;
  WHILE i <= s1_len DO
    SET s1_char = SUBSTRING(s1, i, 1), c = i, cv0 = UNHEX(HEX(i)), j = 1;
    WHILE j <= s2_len DO
      SET c = c + 1;
      IF s1_char = SUBSTRING(s2, j, 1) THEN SET cost = 0; ELSE SET cost = 1; END IF;
      SET c_temp = CONV(HEX(SUBSTRING(cv1, j, 1)), 16, 10) + cost;
      IF c > c_temp THEN SET c = c_temp; END IF;
      SET c_temp = CONV(HEX(SUBSTRING(cv1, j + 1, 1)), 16, 10) + 1;
      IF c > c_temp THEN SET c = c_temp; END IF;
      SET cv0 = CONCAT(cv0, UNHEX(HEX(c))), j = j + 1;
    END WHILE;
    SET cv1 = cv0, i = i + 1;
  END WHILE;
  RETURN c;
END$$

-- @api roles=* rules=R1.4 | Name similarity 0.000-1.000 (1 = identical) from Levenshtein on lower-cased trimmed names
CREATE FUNCTION name_similarity(a VARCHAR(250), b VARCHAR(250)) RETURNS DECIMAL(4,3) DETERMINISTIC
BEGIN
  DECLARE x VARCHAR(250); DECLARE y VARCHAR(250); DECLARE m INT;
  SET x = LOWER(TRIM(a)), y = LOWER(TRIM(b));
  SET m = GREATEST(CHAR_LENGTH(x), CHAR_LENGTH(y), 1);
  RETURN GREATEST(0, 1 - levenshtein(x, y) / m);
END$$

-- @api roles=* rules=R1.4 | Duplicate tier 1-4 of one patient against search inputs (0 = no match). T1 phone+name, T2 name+DOB, T3 DOB+postal code, T4 Aadhaar last-4 tie-breaker
CREATE FUNCTION patient_match_tier(p_patient_id BIGINT UNSIGNED, p_name VARCHAR(250), p_dob DATE, p_phone VARCHAR(20),
                                   p_aadhaar CHAR(4), p_postal CHAR(6)) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v_name VARCHAR(250); DECLARE v_dob DATE; DECLARE v_aad CHAR(4); DECLARE v_pin CHAR(6);
  DECLARE v_sim DECIMAL(4,3); DECLARE v_snd TINYINT; DECLARE v_phone_hit INT DEFAULT 0;
  SELECT full_name_search, date_of_birth, aadhaar_last4, permanent_address_postal_code INTO v_name, v_dob, v_aad, v_pin
    FROM patient WHERE patient_id = p_patient_id;
  IF v_name IS NULL THEN RETURN 0; END IF;
  SET v_sim = name_similarity(v_name, p_name);
  SET v_snd = SOUNDEX(SUBSTRING_INDEX(v_name, ' ', 1)) = SOUNDEX(SUBSTRING_INDEX(TRIM(p_name), ' ', 1));
  IF p_phone IS NOT NULL THEN
    SELECT COUNT(*) INTO v_phone_hit FROM patient_phone WHERE patient_id = p_patient_id AND phone_number = p_phone;
  END IF;
  IF v_phone_hit > 0 AND (v_sim >= 0.6 OR v_snd = 1) THEN RETURN 1; END IF;
  IF p_dob IS NOT NULL AND v_dob = p_dob AND (v_sim >= 0.6 OR v_snd = 1) THEN RETURN 2; END IF;
  IF p_dob IS NOT NULL AND v_dob = p_dob AND v_pin IS NOT NULL AND p_postal = v_pin THEN RETURN 3; END IF;
  IF p_aadhaar IS NOT NULL AND v_aad = p_aadhaar AND v_sim >= 0.4 THEN RETURN 4; END IF;
  RETURN 0;
END$$

-- @api roles=RECEPTIONIST,OWNER rules=R1.4,G12 | Ranked duplicate candidates (tier 1 best). The receptionist confirms; nothing is merged automatically
CREATE PROCEDURE sp_find_patient_candidates(IN p_name VARCHAR(250), IN p_dob DATE, IN p_phone VARCHAR(20),
                                            IN p_aadhaar CHAR(4), IN p_postal CHAR(6))
BEGIN
  CALL int_require_role('RECEPTIONIST,OWNER');
  SELECT p.patient_id, p.display_code, p.full_name_search AS name, p.date_of_birth, p.gender,
         (SELECT ph.phone_number FROM patient_phone ph WHERE ph.patient_id = p.patient_id AND ph.valid_to IS NULL
           ORDER BY ph.is_primary DESC, ph.phone_sequence DESC LIMIT 1) AS phone,
         m.tier, ROUND(name_similarity(p.full_name_search, p_name), 2) AS name_score,
         ROUND((5 - m.tier) * 10 + name_similarity(p.full_name_search, p_name) * 10, 1) AS score
    FROM (SELECT x.patient_id, patient_match_tier(x.patient_id, p_name, p_dob, p_phone, p_aadhaar, p_postal) AS tier
            FROM patient x
           WHERE NOT EXISTS (SELECT 1 FROM patient_merge_log ml WHERE ml.source_patient_id = x.patient_id)
             AND (x.name_prefix = LEFT(LOWER(TRIM(p_name)), 3)
                  OR (p_dob IS NOT NULL AND x.date_of_birth = p_dob)
                  OR (p_aadhaar IS NOT NULL AND x.aadhaar_last4 = p_aadhaar)
                  OR (p_phone IS NOT NULL AND x.patient_id IN (SELECT patient_id FROM patient_phone WHERE phone_number = p_phone)))) m
    JOIN patient p ON p.patient_id = m.patient_id
   WHERE m.tier > 0
   ORDER BY m.tier, score DESC
   LIMIT 20;
END$$

-- @api roles=RECEPTIONIST rules=R1.1,R1.2,R1.3,R1.4,G12 | own txn. Register a patient. If duplicate candidates exist the call is refused unless p_confirm_not_duplicate=1 (a person confirmed)
CREATE PROCEDURE sp_register_patient(IN p_given VARCHAR(80), IN p_middle VARCHAR(80), IN p_family VARCHAR(80), IN p_gender VARCHAR(10),
                                     IN p_dob DATE, IN p_phone VARCHAR(20), IN p_aadhaar CHAR(4), IN p_perm_line VARCHAR(200),
                                     IN p_perm_city VARCHAR(80), IN p_perm_postal CHAR(6), IN p_confirm_not_duplicate TINYINT,
                                     OUT p_patient_id BIGINT UNSIGNED, OUT p_display_code VARCHAR(30))
BEGIN
  DECLARE v_name VARCHAR(250); DECLARE v_n INT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST');
  IF p_given IS NULL OR CHAR_LENGTH(TRIM(p_given)) < 2 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Patient first name is required'; END IF;
  IF p_dob IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Date of birth is required (use Unknown Patient for emergencies)'; END IF;
  SET v_name = LOWER(TRIM(CONCAT_WS(' ', p_given, p_middle, p_family)));
  SELECT COUNT(*) INTO v_n FROM patient x
   WHERE NOT EXISTS (SELECT 1 FROM patient_merge_log ml WHERE ml.source_patient_id = x.patient_id)
     AND (x.name_prefix = LEFT(v_name, 3) OR x.date_of_birth = p_dob
          OR (p_phone IS NOT NULL AND x.patient_id IN (SELECT patient_id FROM patient_phone WHERE phone_number = p_phone)))
     AND patient_match_tier(x.patient_id, v_name, p_dob, p_phone, p_aadhaar, p_perm_postal) > 0;
  IF v_n > 0 AND IFNULL(p_confirm_not_duplicate, 0) <> 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Possible duplicate patient found. Check the candidate list and confirm.';
  END IF;
  START TRANSACTION;
  CALL next_code('patient', p_display_code);
  INSERT INTO patient (display_code, registration_kind, given_name, middle_name, family_name, date_of_birth, gender, aadhaar_last4,
                       permanent_address_line, permanent_address_city, permanent_address_postal_code)
  VALUES (p_display_code, 'registered', TRIM(p_given), NULLIF(TRIM(p_middle), ''), NULLIF(TRIM(p_family), ''), p_dob, p_gender, p_aadhaar,
          p_perm_line, p_perm_city, p_perm_postal);
  SET p_patient_id = LAST_INSERT_ID();
  IF p_phone IS NOT NULL AND p_phone <> '' THEN
    INSERT INTO patient_phone (patient_id, phone_sequence, phone_number, is_primary) VALUES (p_patient_id, 1, p_phone, 1);
  END IF;
  IF v_n > 0 THEN CALL raise_alert('DUPLICATE_OVERRIDE', 'SUMMARY', 'patient', p_patient_id, 'Patient registered after a duplicate warning was confirmed'); END IF;
  COMMIT;
END$$

-- @api roles=RECEPTIONIST,DOCTOR,NURSE rules=R1.5 | own txn. Emergency Unknown Patient: TMP- code, gender and approximate age only
CREATE PROCEDURE sp_register_unknown_patient(IN p_gender VARCHAR(10), IN p_approx_age SMALLINT,
                                             OUT p_patient_id BIGINT UNSIGNED, OUT p_display_code VARCHAR(30))
BEGIN
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST,DOCTOR,NURSE');
  IF p_approx_age IS NULL OR p_approx_age < 0 OR p_approx_age > 120 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Approximate age (0-120) is required'; END IF;
  START TRANSACTION;
  CALL next_code('emergency_patient', p_display_code);
  INSERT INTO patient (display_code, registration_kind, given_name, approximate_age, gender)
  VALUES (p_display_code, 'emergency_unknown', 'Unknown', p_approx_age, IFNULL(p_gender, 'unknown'));
  SET p_patient_id = LAST_INSERT_ID();
  COMMIT;
END$$

-- @api roles=RECEPTIONIST rules=R1.2 | own txn. Add a phone number; a new primary closes the previous primary (history kept)
CREATE PROCEDURE sp_add_patient_phone(IN p_patient_id BIGINT UNSIGNED, IN p_phone VARCHAR(20), IN p_make_primary TINYINT)
BEGIN
  DECLARE v_seq INT; DECLARE v_lock BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST');
  START TRANSACTION;
  SELECT patient_id INTO v_lock FROM patient WHERE patient_id = p_patient_id FOR UPDATE;
  IF v_lock IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown patient'; END IF;
  SELECT COALESCE(MAX(phone_sequence), 0) + 1 INTO v_seq FROM patient_phone WHERE patient_id = p_patient_id;
  IF IFNULL(p_make_primary, 1) = 1 THEN
    UPDATE patient_phone SET is_primary = 0, valid_to = CURRENT_DATE WHERE patient_id = p_patient_id AND valid_to IS NULL;
  END IF;
  INSERT INTO patient_phone (patient_id, phone_sequence, phone_number, is_primary) VALUES (p_patient_id, v_seq, p_phone, IFNULL(p_make_primary, 1));
  COMMIT;
END$$

-- @api roles=RECEPTIONIST,NURSE rules=R1.2,R11.4 | Add an attendant / emergency contact
CREATE PROCEDURE sp_add_patient_attendant(IN p_patient_id BIGINT UNSIGNED, IN p_name VARCHAR(120), IN p_relationship VARCHAR(40),
                                          IN p_phone VARCHAR(20), IN p_is_emergency TINYINT, OUT p_attendant_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('RECEPTIONIST,NURSE');
  INSERT INTO patient_attendant (patient_id, name, relationship, phone, is_emergency_contact) VALUES (p_patient_id, p_name, p_relationship, p_phone, IFNULL(p_is_emergency, 0));
  SET p_attendant_id = LAST_INSERT_ID();
END$$

-- @api roles=RECEPTIONIST rules=R1.6 | Demographic correction (audited old/new in audit_log). A reason is mandatory. Blood group and allergies are clinical and not editable here
CREATE PROCEDURE sp_update_demographics(IN p_patient_id BIGINT UNSIGNED, IN p_given VARCHAR(80), IN p_middle VARCHAR(80), IN p_family VARCHAR(80),
                                        IN p_gender VARCHAR(10), IN p_dob DATE, IN p_perm_line VARCHAR(200), IN p_perm_city VARCHAR(80),
                                        IN p_perm_postal CHAR(6), IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('RECEPTIONIST');
  IF p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A reason is required for a demographic correction'; END IF;
  IF patient_is_merged(p_patient_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This patient record was merged; edit the surviving record'; END IF;
  SET @audit_reason = p_reason;
  UPDATE patient SET given_name = TRIM(p_given), middle_name = NULLIF(TRIM(p_middle), ''), family_name = NULLIF(TRIM(p_family), ''),
         gender = p_gender, date_of_birth = p_dob, permanent_address_line = p_perm_line, permanent_address_city = p_perm_city,
         permanent_address_postal_code = p_perm_postal
   WHERE patient_id = p_patient_id;
  SET @audit_reason = NULL;
END$$

-- @api roles=RECEPTIONIST,OWNER rules=R1.5,G12,G2 | own txn. Human-confirmed merge. Per CR-2 event rows are NOT rewritten: the merge log is the redirect (canonical_patient()); phones and attendants (master rows) move to the survivor
CREATE PROCEDURE sp_merge_patients(IN p_source BIGINT UNSIGNED, IN p_survivor BIGINT UNSIGNED, IN p_reason VARCHAR(300),
                                   IN p_tier VARCHAR(2), OUT p_merge_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_a BIGINT UNSIGNED; DECLARE v_b BIGINT UNSIGNED; DECLARE v_maxseq INT DEFAULT 0; DECLARE v_visits INT; DECLARE v_phones INT; DECLARE v_att INT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST,OWNER');
  IF p_source = p_survivor THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cannot merge a patient into itself'; END IF;
  IF p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A reason is required for a merge'; END IF;
  START TRANSACTION;
  SELECT patient_id INTO v_a FROM patient WHERE patient_id = LEAST(p_source, p_survivor) FOR UPDATE;
  SELECT patient_id INTO v_b FROM patient WHERE patient_id = GREATEST(p_source, p_survivor) FOR UPDATE;
  IF v_a IS NULL OR v_b IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown patient in merge'; END IF;
  IF patient_is_merged(p_source) OR patient_is_merged(p_survivor) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'One of the patients was already merged away'; END IF;
  SELECT COUNT(*) INTO v_visits FROM visit WHERE patient_id = p_source;
  SELECT COALESCE(MAX(phone_sequence), 0) INTO v_maxseq FROM patient_phone WHERE patient_id = p_survivor;
  SELECT COUNT(*) INTO v_phones FROM patient_phone WHERE patient_id = p_source;
  SELECT COUNT(*) INTO v_att FROM patient_attendant WHERE patient_id = p_source;
  SET @audit_reason = CONCAT('merge into patient ', p_survivor);
  UPDATE patient_phone SET patient_id = p_survivor, phone_sequence = phone_sequence + v_maxseq, is_primary = 0 WHERE patient_id = p_source;
  UPDATE patient_attendant SET patient_id = p_survivor WHERE patient_id = p_source;
  SET @audit_reason = NULL;
  INSERT INTO patient_merge_log (source_patient_id, surviving_patient_id, reason, suggested_match_tier) VALUES (p_source, p_survivor, p_reason, NULLIF(p_tier, ''));
  SET p_merge_id = LAST_INSERT_ID();
  CALL raise_alert('PATIENT_MERGED', 'SUMMARY', 'patient_merge_log', p_merge_id,
                   CONCAT('Patients merged; visits redirected: ', v_visits, ', phones moved: ', v_phones, ', attendants moved: ', v_att));
  COMMIT;
END$$

-- ------------------------------------------------------------------ scheduling
-- @api roles=* rules=R3.6 | internal: generate appointment slots for one schedule version for schedule_horizon_days, recorded under SYS-SCHEDULER
CREATE PROCEDURE int_generate_slots(IN p_schedule_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_doc BIGINT UNSIGNED; DECLARE v_wd TINYINT; DECLARE v_start TIME; DECLARE v_end TIME; DECLARE v_min SMALLINT;
  DECLARE v_from DATE; DECLARE v_state VARCHAR(13); DECLARE v_h INT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN SET @app_user_id = @o_uid; SET @app_role = @o_role; RESIGNAL; END;
  SELECT doctor_staff_id, weekday, start_time, end_time, slot_minutes, valid_from, schedule_state
    INTO v_doc, v_wd, v_start, v_end, v_min, v_from, v_state FROM doctor_schedule WHERE doctor_schedule_id = p_schedule_id;
  SET v_h = setting_int('schedule_horizon_days');
  IF v_doc IS NOT NULL AND v_state = 'active' THEN
    SET @o_uid = @app_user_id; SET @o_role = @app_role; SET @app_user_id = 1; SET @app_role = 'SYSTEM';
    INSERT IGNORE INTO appointment_slot (doctor_schedule_id, doctor_staff_id, slot_start, slot_end)
    WITH RECURSIVE days AS (
           SELECT GREATEST(v_from, CURRENT_DATE) AS d
           UNION ALL SELECT d + INTERVAL 1 DAY FROM days WHERE d < CURRENT_DATE + INTERVAL v_h DAY),
         ks AS (SELECT 0 AS k UNION ALL SELECT k + 1 FROM ks WHERE k < 250)
    SELECT p_schedule_id, v_doc, TIMESTAMP(days.d, v_start) + INTERVAL (ks.k * v_min) MINUTE,
           TIMESTAMP(days.d, v_start) + INTERVAL ((ks.k + 1) * v_min) MINUTE
      FROM days JOIN ks ON TIMESTAMP(days.d, v_start) + INTERVAL ((ks.k + 1) * v_min) MINUTE <= TIMESTAMP(days.d, v_end)
     WHERE WEEKDAY(days.d) = v_wd;
    SET @app_user_id = @o_uid; SET @app_role = @o_role;
  END IF;
END$$

-- @api roles=OWNER,RECEPTIONIST rules=R3.6,R3.7 | own txn. Add a weekly session for a doctor (weekday 0=Mon..6=Sun) and generate its slots
CREATE PROCEDURE sp_add_doctor_schedule(IN p_doctor BIGINT UNSIGNED, IN p_weekday TINYINT, IN p_start TIME, IN p_end TIME,
                                        IN p_slot_minutes SMALLINT, IN p_valid_from DATE, OUT p_schedule_id BIGINT UNSIGNED)
BEGIN
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('OWNER,RECEPTIONIST');
  START TRANSACTION;
  INSERT INTO doctor_schedule (doctor_staff_id, weekday, start_time, end_time, slot_minutes, valid_from, schedule_state)
  VALUES (p_doctor, p_weekday, p_start, p_end, p_slot_minutes, IFNULL(p_valid_from, CURRENT_DATE), 'active');
  SET p_schedule_id = LAST_INSERT_ID();
  CALL int_generate_slots(p_schedule_id);
  COMMIT;
END$$

-- @api roles=DOCTOR rules=R3.7 | A doctor requests a change to his own schedule (modify / add_session / withdraw_session). Nothing changes until someone else applies it
CREATE PROCEDURE sp_request_schedule_change(IN p_target_schedule BIGINT UNSIGNED, IN p_kind VARCHAR(20), IN p_weekday TINYINT,
                                            IN p_start TIME, IN p_end TIME, IN p_slot_minutes SMALLINT, IN p_valid_from DATE,
                                            IN p_reason VARCHAR(300), OUT p_request_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('DOCTOR');
  IF p_target_schedule IS NOT NULL AND NOT EXISTS (SELECT 1 FROM doctor_schedule WHERE doctor_schedule_id = p_target_schedule AND doctor_staff_id = app_user()) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'You can only request changes to your own schedule';
  END IF;
  INSERT INTO schedule_change_request (doctor_staff_id, target_doctor_schedule_id, change_kind, proposed_weekday, proposed_start_time,
                                       proposed_end_time, proposed_slot_minutes, proposed_valid_from, reason)
  VALUES (app_user(), p_target_schedule, p_kind, p_weekday, p_start, p_end, p_slot_minutes, p_valid_from, p_reason);
  SET p_request_id = LAST_INSERT_ID();
END$$

-- @api roles=OWNER,RECEPTIONIST rules=R3.7 | own txn. Apply or decline a schedule change (never by the requesting doctor). Applying creates a NEW schedule version superseding the old one; old slots stop being bookable
CREATE PROCEDURE sp_apply_schedule_change(IN p_request_id BIGINT UNSIGNED, IN p_outcome VARCHAR(10), IN p_reason VARCHAR(300),
                                          OUT p_new_schedule_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_doc BIGINT UNSIGNED; DECLARE v_target BIGINT UNSIGNED; DECLARE v_kind VARCHAR(20); DECLARE v_wd TINYINT;
  DECLARE v_s TIME; DECLARE v_e TIME; DECLARE v_m SMALLINT; DECLARE v_from DATE;
  DECLARE t_wd TINYINT; DECLARE t_s TIME; DECLARE t_e TIME; DECLARE t_m SMALLINT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('OWNER,RECEPTIONIST');
  SELECT doctor_staff_id, target_doctor_schedule_id, change_kind, proposed_weekday, proposed_start_time, proposed_end_time,
         proposed_slot_minutes, proposed_valid_from
    INTO v_doc, v_target, v_kind, v_wd, v_s, v_e, v_m, v_from FROM schedule_change_request WHERE schedule_change_request_id = p_request_id;
  IF v_doc IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown schedule change request'; END IF;
  IF v_doc = app_user() THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A doctor cannot apply his own schedule change'; END IF;
  IF p_outcome NOT IN ('applied', 'declined') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Outcome must be applied or declined'; END IF;
  START TRANSACTION;
  IF p_outcome = 'applied' THEN
    IF v_kind = 'withdraw_session' THEN
      SELECT weekday, start_time, end_time, slot_minutes INTO t_wd, t_s, t_e, t_m FROM doctor_schedule WHERE doctor_schedule_id = v_target;
      INSERT INTO doctor_schedule (doctor_staff_id, weekday, start_time, end_time, slot_minutes, valid_from, schedule_state, supersedes_doctor_schedule_id)
      VALUES (v_doc, t_wd, t_s, t_e, t_m, v_from, 'withdrawn', v_target);
    ELSE
      INSERT INTO doctor_schedule (doctor_staff_id, weekday, start_time, end_time, slot_minutes, valid_from, schedule_state, supersedes_doctor_schedule_id)
      VALUES (v_doc, v_wd, v_s, v_e, v_m, v_from, 'active', v_target);
    END IF;
    SET p_new_schedule_id = LAST_INSERT_ID();
    CALL int_generate_slots(p_new_schedule_id);
  END IF;
  INSERT INTO schedule_change_application (schedule_change_request_id, outcome, resulting_doctor_schedule_id, reason)
  VALUES (p_request_id, p_outcome, p_new_schedule_id, p_reason);
  COMMIT;
END$$

-- @api roles=* rules=R3.6,R3.7 | internal: 1 if the slot's schedule version is still effective on the slot date (not superseded)
CREATE FUNCTION slot_is_effective(p_slot BIGINT UNSIGNED) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v INT DEFAULT 0;
  SELECT COUNT(*) INTO v FROM appointment_slot s JOIN doctor_schedule d ON d.doctor_schedule_id = s.doctor_schedule_id
   WHERE s.appointment_slot_id = p_slot AND d.schedule_state = 'active'
     AND NOT EXISTS (SELECT 1 FROM doctor_schedule n WHERE n.supersedes_doctor_schedule_id = d.doctor_schedule_id AND n.valid_from <= DATE(s.slot_start));
  RETURN v > 0;
END$$

-- @api roles=* rules=R3.2 | internal: append an appointment status row (assigns status_seq under the appointment lock)
CREATE PROCEDURE int_add_appt_status(IN p_appt BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300))
BEGIN
  DECLARE v_lock BIGINT UNSIGNED; DECLARE v_seq INT;
  SELECT appointment_id INTO v_lock FROM appointment WHERE appointment_id = p_appt FOR UPDATE;
  IF v_lock IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown appointment'; END IF;
  SELECT COALESCE(MAX(status_seq), 0) + 1 INTO v_seq FROM appointment_status_history WHERE appointment_id = p_appt;
  INSERT INTO appointment_status_history (appointment_id, status_seq, status, reason) VALUES (p_appt, v_seq, p_status, p_reason);
END$$

-- @api roles=RECEPTIONIST rules=R3.2,R3.6 | own txn. Book a slot. Doctor row is locked, live claims are checked, appointment + first status + claim are one transaction. Error "Slot already booked for this doctor" if taken
CREATE PROCEDURE sp_book_appointment(IN p_patient BIGINT UNSIGNED, IN p_slot BIGINT UNSIGNED, IN p_channel VARCHAR(20),
                                     OUT p_appointment_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_doc BIGINT UNSIGNED; DECLARE v_start DATETIME; DECLARE v_lock BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST');
  SELECT doctor_staff_id, slot_start INTO v_doc, v_start FROM appointment_slot WHERE appointment_slot_id = p_slot;
  IF v_doc IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown appointment slot'; END IF;
  START TRANSACTION;
  SELECT staff_id INTO v_lock FROM doctor_detail WHERE staff_id = v_doc FOR UPDATE;
  IF v_start < NOW() - INTERVAL 5 MINUTE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That slot is in the past'; END IF;
  IF NOT slot_is_effective(p_slot) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That slot belongs to an outdated doctor schedule'; END IF;
  IF slot_has_live_claim(p_slot, 0) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Slot already booked for this doctor'; END IF;
  INSERT INTO appointment (patient_id, doctor_staff_id, booking_channel) VALUES (p_patient, v_doc, IFNULL(p_channel, 'reception'));
  SET p_appointment_id = LAST_INSERT_ID();
  INSERT INTO appointment_status_history (appointment_id, status_seq, status, reason) VALUES (p_appointment_id, 1, 'booked', NULL);
  INSERT INTO appointment_slot_claim (appointment_id, appointment_slot_id, claim_reason) VALUES (p_appointment_id, p_slot, 'initial');
  COMMIT;
END$$

-- @api roles=RECEPTIONIST rules=R3.6 | own txn. Move a booked appointment to another slot of the same doctor; the old claim stays as history and the old slot becomes free
CREATE PROCEDURE sp_reschedule_appointment(IN p_appt BIGINT UNSIGNED, IN p_new_slot BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  DECLARE v_doc BIGINT UNSIGNED; DECLARE v_status VARCHAR(20); DECLARE v_lock BIGINT UNSIGNED; DECLARE v_start DATETIME;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST');
  SELECT doctor_staff_id INTO v_doc FROM appointment WHERE appointment_id = p_appt;
  IF v_doc IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown appointment'; END IF;
  START TRANSACTION;
  SELECT staff_id INTO v_lock FROM doctor_detail WHERE staff_id = v_doc FOR UPDATE;
  SELECT status INTO v_status FROM appointment_status_history WHERE appointment_id = p_appt ORDER BY status_seq DESC LIMIT 1;
  IF v_status <> 'booked' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only a booked appointment can be rescheduled'; END IF;
  SELECT slot_start INTO v_start FROM appointment_slot WHERE appointment_slot_id = p_new_slot AND doctor_staff_id = v_doc;
  IF v_start IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'New slot must belong to the same doctor'; END IF;
  IF v_start < NOW() - INTERVAL 5 MINUTE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That slot is in the past'; END IF;
  IF NOT slot_is_effective(p_new_slot) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That slot belongs to an outdated doctor schedule'; END IF;
  IF slot_has_live_claim(p_new_slot, p_appt) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Slot already booked for this doctor'; END IF;
  INSERT INTO appointment_slot_claim (appointment_id, appointment_slot_id, claim_reason) VALUES (p_appt, p_new_slot, 'reschedule');
  COMMIT;
END$$

-- @api roles=RECEPTIONIST,DOCTOR rules=R3.2 | own txn. Move an appointment to its next status (valid transitions only). in_consultation/completed need DOCTOR, the rest RECEPTIONIST
CREATE PROCEDURE sp_set_appointment_status(IN p_appt BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300))
BEGIN
  DECLARE v_cur VARCHAR(20); DECLARE v_ok TINYINT DEFAULT 0; DECLARE v_start DATETIME;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  IF p_status IN ('in_consultation', 'completed') THEN CALL int_require_role('DOCTOR'); ELSE CALL int_require_role('RECEPTIONIST'); END IF;
  SELECT status INTO v_cur FROM appointment_status_history WHERE appointment_id = p_appt ORDER BY status_seq DESC LIMIT 1;
  IF v_cur IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown appointment'; END IF;
  SET v_ok = CASE v_cur
    WHEN 'booked' THEN p_status IN ('checked_in', 'cancelled', 'no_show')
    WHEN 'checked_in' THEN p_status IN ('waiting', 'in_consultation', 'cancelled')
    WHEN 'waiting' THEN p_status IN ('in_consultation', 'cancelled')
    WHEN 'in_consultation' THEN p_status IN ('completed')
    ELSE 0 END;
  IF v_ok = 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This status change is not allowed for the appointment'; END IF;
  IF p_status = 'cancelled' AND (p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A reason is required to cancel'; END IF;
  IF p_status = 'no_show' THEN
    SELECT s.slot_start INTO v_start FROM appointment_slot_claim c JOIN appointment_slot s ON s.appointment_slot_id = c.appointment_slot_id
     WHERE c.appointment_id = p_appt ORDER BY c.appointment_slot_claim_id DESC LIMIT 1;
    IF v_start > NOW() THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No-show can only be marked after the slot time'; END IF;
  END IF;
  START TRANSACTION;
  CALL int_add_appt_status(p_appt, p_status, p_reason);
  COMMIT;
END$$

-- ------------------------------------------------------------------ visits
-- @api roles=* rules=R3.3 | internal: append a visit status row (status_seq assigned under the visit lock)
CREATE PROCEDURE int_add_visit_status(IN p_visit BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_location VARCHAR(60), IN p_reason VARCHAR(300))
BEGIN
  DECLARE v_lock BIGINT UNSIGNED; DECLARE v_seq INT;
  SELECT visit_id INTO v_lock FROM visit WHERE visit_id = p_visit FOR UPDATE;
  IF v_lock IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown visit'; END IF;
  SELECT COALESCE(MAX(status_seq), 0) + 1 INTO v_seq FROM visit_status_history WHERE visit_id = p_visit;
  INSERT INTO visit_status_history (visit_id, status_seq, status, location_label, reason) VALUES (p_visit, v_seq, p_status, p_location, p_reason);
END$$

-- @api roles=RECEPTIONIST,DOCTOR,NURSE rules=R3.1,R3.3 | own txn. Open a visit (appointment, walk_in or emergency). Inserts the first status (arrived) and, when a doctor is known, the attending assignment
CREATE PROCEDURE sp_create_visit(IN p_patient BIGINT UNSIGNED, IN p_appointment BIGINT UNSIGNED, IN p_doctor BIGINT UNSIGNED,
                                 IN p_entry_type VARCHAR(12), OUT p_visit_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_appt_patient BIGINT UNSIGNED; DECLARE v_appt_doc BIGINT UNSIGNED; DECLARE v_status VARCHAR(20); DECLARE v_doc BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST,DOCTOR,NURSE');
  SET v_doc = p_doctor;
  IF p_appointment IS NOT NULL THEN
    SELECT patient_id, doctor_staff_id INTO v_appt_patient, v_appt_doc FROM appointment WHERE appointment_id = p_appointment;
    IF v_appt_patient IS NULL OR v_appt_patient <> p_patient THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Appointment does not belong to this patient'; END IF;
    SELECT status INTO v_status FROM appointment_status_history WHERE appointment_id = p_appointment ORDER BY status_seq DESC LIMIT 1;
    IF v_status NOT IN ('booked', 'checked_in') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Appointment is not open for check-in'; END IF;
    SET v_doc = v_appt_doc;
  END IF;
  START TRANSACTION;
  INSERT INTO visit (patient_id, appointment_id, entry_type) VALUES (p_patient, p_appointment, IF(p_appointment IS NOT NULL, 'appointment', IFNULL(p_entry_type, 'walk_in')));
  SET p_visit_id = LAST_INSERT_ID();
  INSERT INTO visit_status_history (visit_id, status_seq, status) VALUES (p_visit_id, 1, 'arrived');
  IF v_doc IS NOT NULL THEN
    INSERT INTO visit_doctor_assignment (visit_id, doctor_staff_id, assignment_role) VALUES (p_visit_id, v_doc, 'attending');
  END IF;
  IF p_appointment IS NOT NULL AND v_status = 'booked' THEN CALL int_add_appt_status(p_appointment, 'checked_in', 'Checked in at reception'); END IF;
  CALL int_auto_charge(p_visit_id, 'CONSULT', 'price_list_item', NULL);  -- consultation charge from the visit-start event (R8.2)
  COMMIT;
END$$

-- @api roles=RECEPTIONIST,DOCTOR,NURSE,PHARMACY,ACCOUNTANT rules=R3.3 | own txn. Move a visit to waiting / with_doctor / lab / pharmacy / billing (discharged is set only by discharge). Location is a label, no beds
CREATE PROCEDURE sp_add_visit_status(IN p_visit BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_location VARCHAR(60), IN p_reason VARCHAR(300))
BEGIN
  DECLARE v_appt BIGINT UNSIGNED; DECLARE v_astatus VARCHAR(20);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST,DOCTOR,NURSE,PHARMACY,ACCOUNTANT');
  IF p_status NOT IN ('arrived', 'waiting', 'with_doctor', 'lab', 'pharmacy', 'billing') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Status not allowed here (discharge is a separate step)';
  END IF;
  IF visit_is_closed(p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  START TRANSACTION;
  CALL int_add_visit_status(p_visit, p_status, p_location, p_reason);
  SELECT appointment_id INTO v_appt FROM visit WHERE visit_id = p_visit;
  IF v_appt IS NOT NULL AND p_status IN ('waiting', 'with_doctor') THEN
    SELECT status INTO v_astatus FROM appointment_status_history WHERE appointment_id = v_appt ORDER BY status_seq DESC LIMIT 1;
    IF p_status = 'waiting' AND v_astatus = 'checked_in' THEN CALL int_add_appt_status(v_appt, 'waiting', NULL); END IF;
    IF p_status = 'with_doctor' AND v_astatus IN ('checked_in', 'waiting') THEN CALL int_add_appt_status(v_appt, 'in_consultation', NULL); END IF;
  END IF;
  COMMIT;
END$$

-- @api roles=DOCTOR,NURSE rules=R3.1,R3.4 | own txn. Add the short-stay episode to a visit (at most one per visit). Location label Observation Area
CREATE PROCEDURE sp_start_short_stay(IN p_visit BIGINT UNSIGNED, IN p_reason VARCHAR(300), OUT p_episode_id BIGINT UNSIGNED)
BEGIN
  DECLARE EXIT HANDLER FOR 1062 BEGIN ROLLBACK; SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit already has a short-stay episode'; END;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('DOCTOR,NURSE');
  IF p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Admission reason is required'; END IF;
  START TRANSACTION;
  INSERT INTO short_stay_episode (visit_id, admission_reason) VALUES (p_visit, p_reason);
  SET p_episode_id = LAST_INSERT_ID();
  CALL int_add_visit_status(p_visit, 'with_doctor', 'Observation Area', 'Short stay started');
  CALL int_auto_charge(p_visit, 'STAY_DAYCARE', 'stay', p_episode_id);  -- stay charge from the stay-start event (R8.2)
  COMMIT;
END$$

-- @api roles=DOCTOR,NURSE rules=R3.1 | End a short-stay episode (the end time is derived from this event)
CREATE PROCEDURE sp_close_short_stay(IN p_episode BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('DOCTOR,NURSE');
  IF EXISTS (SELECT 1 FROM short_stay_closure WHERE short_stay_episode_id = p_episode) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Stay is already closed'; END IF;
  INSERT INTO short_stay_closure (short_stay_episode_id, reason) VALUES (p_episode, p_reason);
END$$

-- @api roles=DOCTOR,NURSE rules=R3.4,G6 | Ask the owner to approve a stay beyond the limit. proposed_until is stored in the request; the extension row is created by sp_apply_stay_extension after approval
CREATE PROCEDURE sp_request_stay_extension(IN p_episode BIGINT UNSIGNED, IN p_until DATETIME, IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('DOCTOR,NURSE');
  IF NOT EXISTS (SELECT 1 FROM short_stay_episode WHERE short_stay_episode_id = p_episode) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown stay episode'; END IF;
  CALL request_approval_ex('stay_extension', NULL, NULL, NULL, p_episode, NULL, NULL, NULL, DATE_FORMAT(p_until, '%Y-%m-%d %H:%i:%s'), p_reason, p_approval_id);
END$$

-- @api roles=DOCTOR,NURSE rules=R3.4,G6 | After the owner approved: record the extension (trigger re-checks the approval)
CREATE PROCEDURE sp_apply_stay_extension(IN p_approval_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_ep BIGINT UNSIGNED; DECLARE v_until VARCHAR(500); DECLARE v_reason VARCHAR(300);
  CALL int_require_role('DOCTOR,NURSE');
  SELECT short_stay_episode_id, proposed_change, reason INTO v_ep, v_until, v_reason FROM approval_request
   WHERE approval_request_id = p_approval_id AND request_type = 'stay_extension';
  IF v_ep IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not a stay-extension request'; END IF;
  INSERT INTO short_stay_extension (short_stay_episode_id, approval_request_id, extended_until, reason)
  VALUES (v_ep, p_approval_id, STR_TO_DATE(v_until, '%Y-%m-%d %H:%i:%s'), v_reason);
END$$

-- @api roles=NURSE,DOCTOR rules=R3.5 | own txn. Record a vitals set: one typed row per supplied measurement (BP = two rows sharing observation_set_uuid)
CREATE PROCEDURE sp_record_vitals(IN p_visit BIGINT UNSIGNED, IN p_episode BIGINT UNSIGNED, IN p_bp_sys SMALLINT, IN p_bp_dia SMALLINT,
                                  IN p_pulse SMALLINT, IN p_temp DECIMAL(4,1), IN p_spo2 SMALLINT, IN p_resp SMALLINT, IN p_weight DECIMAL(5,1))
BEGIN
  DECLARE v_set CHAR(36); DECLARE v_seq INT; DECLARE v_lock BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('NURSE,DOCTOR');
  IF (p_bp_sys IS NULL) <> (p_bp_dia IS NULL) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Enter both blood pressure values'; END IF;
  IF p_bp_sys IS NOT NULL AND (p_bp_sys NOT BETWEEN 40 AND 280 OR p_bp_dia NOT BETWEEN 20 AND 180) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Blood pressure value out of range'; END IF;
  IF p_pulse IS NOT NULL AND p_pulse NOT BETWEEN 20 AND 250 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Pulse out of range'; END IF;
  IF p_temp IS NOT NULL AND p_temp NOT BETWEEN 30.0 AND 45.0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Temperature (Celsius) out of range'; END IF;
  IF p_spo2 IS NOT NULL AND p_spo2 NOT BETWEEN 30 AND 100 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SpO2 out of range'; END IF;
  IF p_resp IS NOT NULL AND p_resp NOT BETWEEN 4 AND 80 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Respiratory rate out of range'; END IF;
  IF COALESCE(p_bp_sys, p_pulse, p_temp, p_spo2, p_resp, p_weight) IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Enter at least one measurement'; END IF;
  START TRANSACTION;
  SELECT visit_id INTO v_lock FROM visit WHERE visit_id = p_visit FOR UPDATE;
  IF v_lock IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown visit'; END IF;
  SELECT COALESCE(MAX(observation_seq), 0) INTO v_seq FROM vitals_observation WHERE visit_id = p_visit;
  SET v_set = UUID();
  IF p_bp_sys IS NOT NULL THEN
    INSERT INTO vitals_observation (visit_id, observation_seq, short_stay_episode_id, observation_set_uuid, observation_type, numeric_value, unit) VALUES (p_visit, v_seq + 1, p_episode, v_set, 'bp_systolic', p_bp_sys, 'mmHg');
    INSERT INTO vitals_observation (visit_id, observation_seq, short_stay_episode_id, observation_set_uuid, observation_type, numeric_value, unit) VALUES (p_visit, v_seq + 2, p_episode, v_set, 'bp_diastolic', p_bp_dia, 'mmHg');
    SET v_seq = v_seq + 2;
  END IF;
  IF p_pulse IS NOT NULL THEN SET v_seq = v_seq + 1; INSERT INTO vitals_observation (visit_id, observation_seq, short_stay_episode_id, observation_set_uuid, observation_type, numeric_value, unit) VALUES (p_visit, v_seq, p_episode, v_set, 'pulse', p_pulse, '/min'); END IF;
  IF p_temp IS NOT NULL THEN SET v_seq = v_seq + 1; INSERT INTO vitals_observation (visit_id, observation_seq, short_stay_episode_id, observation_set_uuid, observation_type, numeric_value, unit) VALUES (p_visit, v_seq, p_episode, v_set, 'temperature', p_temp, 'C'); END IF;
  IF p_spo2 IS NOT NULL THEN SET v_seq = v_seq + 1; INSERT INTO vitals_observation (visit_id, observation_seq, short_stay_episode_id, observation_set_uuid, observation_type, numeric_value, unit) VALUES (p_visit, v_seq, p_episode, v_set, 'spo2', p_spo2, '%'); END IF;
  IF p_resp IS NOT NULL THEN SET v_seq = v_seq + 1; INSERT INTO vitals_observation (visit_id, observation_seq, short_stay_episode_id, observation_set_uuid, observation_type, numeric_value, unit) VALUES (p_visit, v_seq, p_episode, v_set, 'resp_rate', p_resp, '/min'); END IF;
  IF p_weight IS NOT NULL THEN SET v_seq = v_seq + 1; INSERT INTO vitals_observation (visit_id, observation_seq, short_stay_episode_id, observation_set_uuid, observation_type, numeric_value, unit) VALUES (p_visit, v_seq, p_episode, v_set, 'weight', p_weight, 'kg'); END IF;
  COMMIT;
END$$

DELIMITER ;

-- ------------------------------------------------------------------ views (derived data, G3)
CREATE OR REPLACE VIEW v_patient_alias AS
SELECT p.patient_id AS alias_patient_id, canonical_patient(p.patient_id) AS canonical_patient_id FROM patient p;

-- Reception-safe patient search: no clinical columns (R2.2)
CREATE OR REPLACE VIEW v_patient_search AS
SELECT p.patient_id, p.display_code, p.registration_kind, p.given_name, p.middle_name, p.family_name, p.full_name_search,
       p.date_of_birth, p.approximate_age, p.gender,
       (SELECT ph.phone_number FROM patient_phone ph WHERE ph.patient_id = p.patient_id AND ph.valid_to IS NULL
         ORDER BY ph.is_primary DESC, ph.phone_sequence DESC LIMIT 1) AS current_phone,
       (SELECT COUNT(*) FROM patient_merge_log ml WHERE ml.source_patient_id = p.patient_id) AS is_merged_away,
       canonical_patient(p.patient_id) AS canonical_patient_id
  FROM patient p;

CREATE OR REPLACE VIEW v_appointment_current AS
SELECT a.appointment_id, a.patient_id, a.doctor_staff_id, a.booking_channel,
       (SELECT h.status FROM appointment_status_history h WHERE h.appointment_id = a.appointment_id ORDER BY h.status_seq DESC LIMIT 1) AS current_status,
       s.slot_start, s.slot_end, s.appointment_slot_id
  FROM appointment a
  JOIN appointment_slot_claim c ON c.appointment_slot_claim_id = (SELECT MAX(c2.appointment_slot_claim_id) FROM appointment_slot_claim c2 WHERE c2.appointment_id = a.appointment_id)
  JOIN appointment_slot s ON s.appointment_slot_id = c.appointment_slot_id;

CREATE OR REPLACE VIEW v_free_slots AS
SELECT s.appointment_slot_id, s.doctor_staff_id, CONCAT_WS(' ', st.given_name, st.family_name) AS doctor_name, s.slot_start, s.slot_end,
       IF(slot_has_live_claim(s.appointment_slot_id, 0), 0, 1) AS is_free
  FROM appointment_slot s JOIN staff st ON st.staff_id = s.doctor_staff_id
 WHERE s.slot_start >= NOW() - INTERVAL 5 MINUTE AND slot_is_effective(s.appointment_slot_id);

CREATE OR REPLACE VIEW v_visit_current AS
SELECT v.visit_id, v.patient_id, canonical_patient(v.patient_id) AS canonical_patient_id, v.entry_type, v.occurred_at AS opened_at,
       (SELECT h.status FROM visit_status_history h WHERE h.visit_id = v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS current_status,
       (SELECT h.location_label FROM visit_status_history h WHERE h.visit_id = v.visit_id ORDER BY h.status_seq DESC LIMIT 1) AS current_location,
       (SELECT a.doctor_staff_id FROM visit_doctor_assignment a WHERE a.visit_id = v.visit_id AND a.assignment_role = 'attending'
         ORDER BY a.visit_doctor_assignment_id DESC LIMIT 1) AS attending_doctor_id,
       visit_is_closed(v.visit_id) AS is_closed,
       EXISTS (SELECT 1 FROM short_stay_episode e WHERE e.visit_id = v.visit_id) AS has_short_stay
  FROM visit v;

-- S2: OPD aspect is derived (>= 1 doctor assignment)
CREATE OR REPLACE VIEW v_opd_visit AS
SELECT v.visit_id FROM visit v WHERE EXISTS (SELECT 1 FROM visit_doctor_assignment a WHERE a.visit_id = v.visit_id);

-- Today's queue board: open visits, no clinical columns
CREATE OR REPLACE VIEW v_queue_today AS
SELECT c.visit_id, p.display_code, CONCAT_WS(' ', p.given_name, p.family_name) AS patient_name, c.current_status, c.current_location,
       CONCAT_WS(' ', d.given_name, d.family_name) AS doctor_name, c.opened_at, c.has_short_stay
  FROM v_visit_current c JOIN patient p ON p.patient_id = c.patient_id LEFT JOIN staff d ON d.staff_id = c.attending_doctor_id
 WHERE c.is_closed = 0;

CREATE OR REPLACE VIEW v_stay_status AS
SELECT e.short_stay_episode_id, e.visit_id, e.occurred_at AS started_at,
       GREATEST(e.occurred_at + INTERVAL setting_int('stay_limit_hours') HOUR,
                COALESCE((SELECT MAX(x.extended_until) FROM short_stay_extension x WHERE x.short_stay_episode_id = e.short_stay_episode_id), e.occurred_at)) AS allowed_until,
       (SELECT cl.occurred_at FROM short_stay_closure cl WHERE cl.short_stay_episode_id = e.short_stay_episode_id ORDER BY cl.short_stay_closure_id DESC LIMIT 1) AS ended_at,
       visit_is_closed(e.visit_id) AS visit_closed
  FROM short_stay_episode e;

CREATE OR REPLACE VIEW v_stay_over_limit AS
SELECT s.short_stay_episode_id, s.visit_id, s.started_at, s.allowed_until, TIMESTAMPDIFF(HOUR, s.started_at, NOW(3)) AS hours_so_far
  FROM v_stay_status s WHERE s.ended_at IS NULL AND s.visit_closed = 0 AND NOW(3) > s.allowed_until;
