-- =====================================================================================
-- P3-3  Allergies, clinical notes, prescriptions (versioned), type-ahead, short-stay orders, outside labs   (Track B)
-- sp_* = public (own txn where the @api text says so); int_* = internal. See docs/db_api/prescriptions_labs.md (generated).
-- Prescription model (ER rev 2): prescription (root) -> prescription_version (full snapshot) x prescription_line (stable lineage)
--   = prescription_item. A revision = new version; lines to drop are marked 'discontinued'; a changed dose = discontinue the line
--   and add a new line (sp_prescribe_item on the new version).
-- =====================================================================================
USE clinic_db;
DELIMITER $$

-- ------------------------------------------------------------------ allergies / blood group
-- @api roles=DOCTOR,NURSE rules=R1.2,R4.4 | Record an allergy (by ingredient and/or free text). Corrections are new rows; status changes use sp_set_allergy_status
CREATE PROCEDURE sp_add_patient_allergy(IN p_patient BIGINT UNSIGNED, IN p_ingredient BIGINT UNSIGNED, IN p_text VARCHAR(100),
                                        IN p_reaction VARCHAR(200), IN p_severity VARCHAR(10), OUT p_allergy_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('DOCTOR,NURSE');
  IF patient_is_merged(p_patient) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This patient record was merged; use the surviving record'; END IF;
  INSERT INTO patient_allergy (patient_id, ingredient_id, allergen_text, reaction, severity)
  VALUES (p_patient, p_ingredient, NULLIF(TRIM(p_text), ''), p_reaction, IFNULL(p_severity, 'unknown'));
  SET p_allergy_id = LAST_INSERT_ID();
END$$

-- @api roles=DOCTOR,NURSE rules=R4.4 | Mark an allergy confirmed / inactive / reactivated / entered_in_error (latest status wins)
CREATE PROCEDURE sp_set_allergy_status(IN p_allergy BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('DOCTOR,NURSE');
  INSERT INTO patient_allergy_status_event (patient_allergy_id, status, reason) VALUES (p_allergy, p_status, p_reason);
END$$

-- @api roles=DOCTOR,NURSE rules=R1.3 | Record blood group (clinical, not identity). A new row corrects the previous one
CREATE PROCEDURE sp_record_blood_group(IN p_patient BIGINT UNSIGNED, IN p_abo VARCHAR(2), IN p_rh VARCHAR(8), IN p_source VARCHAR(20),
                                       IN p_lab_report BIGINT UNSIGNED)
BEGIN
  DECLARE v_prev BIGINT UNSIGNED;
  CALL int_require_role('DOCTOR,NURSE');
  SELECT MAX(patient_blood_group_id) INTO v_prev FROM patient_blood_group WHERE patient_id = p_patient;
  INSERT INTO patient_blood_group (patient_id, abo_group, rh_factor, source, lab_report_id, corrects_id)
  VALUES (p_patient, p_abo, p_rh, p_source, p_lab_report, v_prev);
END$$

-- @api roles=* rules=R4.4 | Allergies currently in force for a patient as text (or 'No allergies recorded')
CREATE FUNCTION allergy_snapshot(p_patient BIGINT UNSIGNED) RETURNS VARCHAR(1000) NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v VARCHAR(1000);
  SELECT GROUP_CONCAT(CONCAT(COALESCE(i.ingredient_name, a.allergen_text), ' (', a.severity, ')') SEPARATOR '; ') INTO v
    FROM v_patient_allergy_current a LEFT JOIN ingredient i ON i.ingredient_id = a.ingredient_id
   WHERE a.canonical_patient_id = canonical_patient(p_patient);
  RETURN LEFT(IFNULL(v, 'No allergies recorded'), 1000);
END$$

-- @api roles=* rules=R4.4 | Names of recorded allergies that clash with a catalog medicine (by ingredient or generic/brand text), or NULL
CREATE FUNCTION fn_allergy_conflict(p_patient BIGINT UNSIGNED, p_medicine BIGINT UNSIGNED) RETURNS VARCHAR(300) NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v VARCHAR(300);
  SELECT GROUP_CONCAT(DISTINCT COALESCE(i.ingredient_name, a.allergen_text) SEPARATOR ', ') INTO v
    FROM v_patient_allergy_current a
    JOIN medicine m ON m.medicine_id = p_medicine
    LEFT JOIN ingredient i ON i.ingredient_id = a.ingredient_id
   WHERE a.canonical_patient_id = canonical_patient(p_patient)
     AND (EXISTS (SELECT 1 FROM medicine_ingredient mi WHERE mi.medicine_id = m.medicine_id AND mi.ingredient_id = a.ingredient_id)
          OR (a.allergen_text IS NOT NULL AND (LOWER(a.allergen_text) = LOWER(m.generic_display_name)
              OR LOWER(m.generic_display_name) LIKE CONCAT('%', LOWER(a.allergen_text), '%')
              OR LOWER(m.brand_name) LIKE CONCAT('%', LOWER(a.allergen_text), '%'))));
  RETURN v;
END$$

-- ------------------------------------------------------------------ notes, diagnosis, sign-off
-- @api roles=DOCTOR rules=R1.6,R11.3 | Clinical note. sharing defaults to not_for_sharing. Correction = new row with corrects_id (amendment)
CREATE PROCEDURE sp_add_clinical_note(IN p_visit BIGINT UNSIGNED, IN p_text TEXT, IN p_sharing VARCHAR(20), IN p_corrects BIGINT UNSIGNED, OUT p_note_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('DOCTOR');
  INSERT INTO clinical_note (visit_id, note_text, sharing_classification, corrects_id) VALUES (p_visit, p_text, IFNULL(p_sharing, 'not_for_sharing'), p_corrects);
  SET p_note_id = LAST_INSERT_ID();
END$$

-- @api roles=DOCTOR rules=R1.6,R2.2 | Diagnosis (hidden from reception views). Correction = new row with corrects_id
CREATE PROCEDURE sp_add_diagnosis(IN p_visit BIGINT UNSIGNED, IN p_text VARCHAR(300), IN p_code VARCHAR(20), IN p_corrects BIGINT UNSIGNED, OUT p_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('DOCTOR');
  INSERT INTO diagnosis (visit_id, diagnosis_text, diagnosis_code, corrects_id) VALUES (p_visit, p_text, p_code, p_corrects);
  SET p_id = LAST_INSERT_ID();
END$$

-- @api roles=DOCTOR rules=R9.4 | Doctor clinical sign-off with discharge instructions (needed before discharge). A later sign-off corrects the earlier one
CREATE PROCEDURE sp_clinical_signoff(IN p_visit BIGINT UNSIGNED, IN p_instructions TEXT, OUT p_signoff_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_prev BIGINT UNSIGNED;
  CALL int_require_role('DOCTOR');
  IF visit_is_closed(p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  SELECT MAX(clinical_signoff_id) INTO v_prev FROM clinical_signoff WHERE visit_id = p_visit;
  INSERT INTO clinical_signoff (visit_id, discharge_instructions, corrects_id) VALUES (p_visit, p_instructions, v_prev);
  SET p_signoff_id = LAST_INSERT_ID();
END$$

-- ------------------------------------------------------------------ prescriptions
-- @api roles=DOCTOR rules=R4.1 | own txn. Create the prescription root and its version 1 (empty) for a visit (one per visit; later changes are new versions)
CREATE PROCEDURE sp_create_prescription(IN p_visit BIGINT UNSIGNED, OUT p_prescription_id BIGINT UNSIGNED, OUT p_version_id BIGINT UNSIGNED)
BEGIN
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('DOCTOR');
  IF visit_is_closed(p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  IF EXISTS (SELECT 1 FROM prescription WHERE visit_id = p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit already has a prescription; revise it instead'; END IF;
  START TRANSACTION;
  INSERT INTO prescription (visit_id) VALUES (p_visit);
  SET p_prescription_id = LAST_INSERT_ID();
  INSERT INTO prescription_version (prescription_id, version_number, change_reason) VALUES (p_prescription_id, 1, 'Initial prescription');
  SET p_version_id = LAST_INSERT_ID();
  COMMIT;
END$$

-- @api roles=DOCTOR rules=R4.1,R4.2,R4.3,R4.4,R4.5,R10.2 | own txn. Add a medicine line to the latest version. Catalog medicine: dose/schedule default from the catalog and can be overridden; allergy clash needs p_override_reason (interrupt alert). Free text: needs p_confirm_allergies=1 and goes to the catalog review queue
CREATE PROCEDURE sp_prescribe_item(IN p_version BIGINT UNSIGNED, IN p_medicine BIGINT UNSIGNED, IN p_free_text VARCHAR(150),
                                   IN p_dose VARCHAR(60), IN p_schedule VARCHAR(30), IN p_food VARCHAR(60), IN p_days SMALLINT,
                                   IN p_qty DECIMAL(10,2), IN p_confirm_allergies TINYINT, IN p_override_reason VARCHAR(300),
                                   OUT p_item_id BIGINT UNSIGNED, OUT p_line_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_rx BIGINT UNSIGNED; DECLARE v_visit BIGINT UNSIGNED; DECLARE v_patient BIGINT UNSIGNED; DECLARE v_ver INT; DECLARE v_max INT;
  DECLARE v_lock BIGINT UNSIGNED; DECLARE v_line INT; DECLARE v_conf VARCHAR(300); DECLARE v_active TINYINT;
  DECLARE v_d VARCHAR(60); DECLARE v_s VARCHAR(30); DECLARE v_f VARCHAR(60); DECLARE v_n SMALLINT; DECLARE v_name VARCHAR(150);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('DOCTOR');
  IF (p_medicine IS NULL) = (p_free_text IS NULL OR TRIM(p_free_text) = '') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Choose a catalog medicine or type a free-text name (not both)'; END IF;
  SELECT v.prescription_id, v.version_number, pr.visit_id INTO v_rx, v_ver, v_visit
    FROM prescription_version v JOIN prescription pr ON pr.prescription_id = v.prescription_id WHERE v.prescription_version_id = p_version;
  IF v_rx IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown prescription version'; END IF;
  SELECT MAX(version_number) INTO v_max FROM prescription_version WHERE prescription_id = v_rx;
  IF v_ver <> v_max THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the latest prescription version can be edited'; END IF;
  IF visit_is_closed(v_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  SELECT patient_id INTO v_patient FROM visit WHERE visit_id = v_visit;
  IF p_medicine IS NOT NULL THEN
    SELECT active, default_dose_text, default_schedule, default_food_instruction, default_duration_days
      INTO v_active, v_d, v_s, v_f, v_n FROM medicine WHERE medicine_id = p_medicine;
    IF v_active IS NULL OR v_active = 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Medicine is not in the active catalog'; END IF;
    SET v_conf = fn_allergy_conflict(v_patient, p_medicine);
    IF v_conf IS NOT NULL AND (p_override_reason IS NULL OR CHAR_LENGTH(TRIM(p_override_reason)) < 5) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Allergy warning: the patient is allergic to a listed ingredient. An override reason is required';
    END IF;
  ELSEIF IFNULL(p_confirm_allergies, 0) <> 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Confirm the patient allergies before saving a free-text medicine';
  END IF;
  START TRANSACTION;
  SELECT prescription_id INTO v_lock FROM prescription WHERE prescription_id = v_rx FOR UPDATE;
  SELECT COALESCE(MAX(line_no), 0) + 1 INTO v_line FROM prescription_line WHERE prescription_id = v_rx;
  INSERT INTO prescription_line (prescription_id, line_no) VALUES (v_rx, v_line);
  SET p_line_id = LAST_INSERT_ID();
  INSERT INTO prescription_item (prescription_version_id, prescription_line_id, prescription_id, item_change, medicine_id, free_text_medicine_name,
                                 chosen_dose_text, chosen_schedule, food_instruction, duration_days, quantity_prescribed)
  VALUES (p_version, p_line_id, v_rx, 'added', p_medicine, NULLIF(TRIM(p_free_text), ''), COALESCE(p_dose, v_d), COALESCE(p_schedule, v_s),
          COALESCE(p_food, v_f), COALESCE(p_days, v_n), p_qty);
  SET p_item_id = LAST_INSERT_ID();
  INSERT INTO prescription_line_status (prescription_line_id, status) VALUES (p_line_id, 'prescribed');
  IF p_medicine IS NULL THEN
    INSERT INTO allergy_confirmation (prescription_item_id, confirmation_type, displayed_allergies_snapshot, was_overridden)
    VALUES (p_item_id, 'free_text_medicine', allergy_snapshot(v_patient), 0);
    INSERT INTO catalog_review_queue (prescription_item_id, submitted_name) VALUES (p_item_id, TRIM(p_free_text));
  ELSEIF v_conf IS NOT NULL THEN
    INSERT INTO allergy_confirmation (prescription_item_id, confirmation_type, displayed_allergies_snapshot, was_overridden, override_reason)
    VALUES (p_item_id, 'generic_name_warning', allergy_snapshot(v_patient), 1, p_override_reason);
    CALL raise_alert('ALLERGY_OVERRIDE', 'INTERRUPT', 'prescription_item', p_item_id, 'A doctor overrode an allergy warning');
  END IF;
  COMMIT;
END$$

-- @api roles=DOCTOR rules=R4.1 | own txn. New prescription version: all lines of the previous version are copied (unchanged) except those listed as discontinued (comma list of line ids, no spaces)
CREATE PROCEDURE sp_revise_prescription(IN p_prescription BIGINT UNSIGNED, IN p_reason VARCHAR(300), IN p_discontinue_lines VARCHAR(200), OUT p_version_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_prev BIGINT UNSIGNED; DECLARE v_no INT; DECLARE v_visit BIGINT UNSIGNED; DECLARE v_lock BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('DOCTOR');
  IF p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A change reason is required'; END IF;
  SELECT visit_id INTO v_visit FROM prescription WHERE prescription_id = p_prescription;
  IF v_visit IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown prescription'; END IF;
  IF visit_is_closed(v_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  START TRANSACTION;
  SELECT prescription_id INTO v_lock FROM prescription WHERE prescription_id = p_prescription FOR UPDATE;
  SELECT prescription_version_id, version_number INTO v_prev, v_no FROM prescription_version WHERE prescription_id = p_prescription ORDER BY version_number DESC LIMIT 1;
  INSERT INTO prescription_version (prescription_id, version_number, change_reason) VALUES (p_prescription, v_no + 1, p_reason);
  SET p_version_id = LAST_INSERT_ID();
  INSERT INTO prescription_item (prescription_version_id, prescription_line_id, prescription_id, item_change, medicine_id, free_text_medicine_name,
                                 chosen_dose_text, chosen_schedule, food_instruction, duration_days, quantity_prescribed)
  SELECT p_version_id, pi.prescription_line_id, pi.prescription_id,
         IF(FIND_IN_SET(pi.prescription_line_id, IFNULL(p_discontinue_lines, '')) > 0, 'discontinued', 'unchanged'),
         pi.medicine_id, pi.free_text_medicine_name, pi.chosen_dose_text, pi.chosen_schedule, pi.food_instruction, pi.duration_days, pi.quantity_prescribed
    FROM prescription_item pi WHERE pi.prescription_version_id = v_prev AND pi.item_change <> 'discontinued';
  INSERT INTO prescription_line_status (prescription_line_id, status, reason)
  SELECT l.prescription_line_id, 'cancelled', p_reason FROM prescription_line l
   WHERE l.prescription_id = p_prescription AND FIND_IN_SET(l.prescription_line_id, IFNULL(p_discontinue_lines, '')) > 0;
  COMMIT;
END$$

-- @api roles=DOCTOR,PHARMACY rules=R4.6 | Line status: bought_outside / not_needed / cancelled / prescribed. 'bought_outside' on a medicine that is in stock is flagged for the owner
CREATE PROCEDURE sp_set_line_status(IN p_line BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300))
BEGIN
  DECLARE v_med BIGINT UNSIGNED; DECLARE v_stock DECIMAL(12,2);
  CALL int_require_role('DOCTOR,PHARMACY');
  INSERT INTO prescription_line_status (prescription_line_id, status, reason) VALUES (p_line, p_status, p_reason);
  IF p_status = 'bought_outside' THEN
    SELECT medicine_id INTO v_med FROM prescription_item WHERE prescription_line_id = p_line AND medicine_id IS NOT NULL ORDER BY prescription_item_id DESC LIMIT 1;
    IF v_med IS NOT NULL THEN
      SELECT COALESCE(sellable_qty, 0) INTO v_stock FROM v_stock_by_medicine WHERE medicine_id = v_med;
      IF IFNULL(v_stock, 0) > 0 THEN CALL raise_alert('OUTSIDE_WHILE_IN_STOCK', 'SUMMARY', 'prescription_line', p_line, 'A medicine was bought outside although it was in stock'); END IF;
    END IF;
  END IF;
END$$

-- @api roles=DOCTOR,PHARMACY,NURSE rules=R4.2 | Type-ahead: catalog matches with state green (in stock) / blue (catalog, out of stock) + text label, default dose, allergy warning flag; last row is red = not in catalog (free text)
CREATE PROCEDURE sp_medicine_suggest(IN p_prefix VARCHAR(60), IN p_patient BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('DOCTOR,PHARMACY,NURSE');
  (SELECT m.medicine_id, m.brand_name, m.generic_display_name, m.strength_value, m.strength_unit, m.dosage_form, m.schedule_class,
          IF(COALESCE(s.sellable_qty, 0) > 0, 'green', 'blue') AS state,
          IF(COALESCE(s.sellable_qty, 0) > 0, CONCAT('In stock: ', REPLACE(CAST(s.sellable_qty AS CHAR), '.00', '')), 'In catalog, out of stock') AS label,
          m.default_dose_text, m.default_schedule, m.default_food_instruction, m.default_duration_days,
          fn_allergy_conflict(p_patient, m.medicine_id) AS allergy_warning
     FROM medicine m LEFT JOIN v_stock_by_medicine s ON s.medicine_id = m.medicine_id
    WHERE m.active = 1 AND (m.brand_name LIKE CONCAT(p_prefix, '%') OR m.generic_display_name LIKE CONCAT(p_prefix, '%'))
    ORDER BY (COALESCE(s.sellable_qty, 0) > 0) DESC, m.brand_name LIMIT 15)
  UNION ALL
  (SELECT NULL, p_prefix, NULL, NULL, NULL, NULL, NULL, 'red', 'Not in catalog (free text)', NULL, NULL, NULL, NULL, NULL);
END$$

-- ------------------------------------------------------------------ short-stay orders
-- @api roles=DOCTOR rules=R4.7 | own txn. New order (header + status ordered). Items are added with sp_add_order_item; sp_finalize_order releases it to nurses
CREATE PROCEDURE sp_create_clinical_order(IN p_visit BIGINT UNSIGNED, IN p_episode BIGINT UNSIGNED, IN p_type VARCHAR(15), OUT p_order_id BIGINT UNSIGNED)
BEGIN
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('DOCTOR');
  IF visit_is_closed(p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  START TRANSACTION;
  INSERT INTO clinical_order (visit_id, short_stay_episode_id, order_type) VALUES (p_visit, p_episode, p_type);
  SET p_order_id = LAST_INSERT_ID();
  INSERT INTO clinical_order_status (clinical_order_id, status) VALUES (p_order_id, 'ordered');
  COMMIT;
END$$

-- @api roles=DOCTOR rules=R4.7,R4.4 | own txn. Add an item (drug, dose, route, first time, repeat interval) to an order that is not finalized yet. A catalog drug that clashes with an allergy needs p_override_reason (interrupt alert)
CREATE PROCEDURE sp_add_order_item(IN p_order BIGINT UNSIGNED, IN p_kind VARCHAR(15), IN p_medicine BIGINT UNSIGNED, IN p_free_text VARCHAR(150),
                                   IN p_price_list BIGINT UNSIGNED, IN p_dose_value DECIMAL(10,3), IN p_dose_unit VARCHAR(20), IN p_route VARCHAR(30),
                                   IN p_first_at DATETIME, IN p_interval_min INT, IN p_qty DECIMAL(10,2), IN p_instructions VARCHAR(300),
                                   IN p_override_reason VARCHAR(300), OUT p_item_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_visit BIGINT UNSIGNED; DECLARE v_patient BIGINT UNSIGNED; DECLARE v_no INT; DECLARE v_lock BIGINT UNSIGNED; DECLARE v_st VARCHAR(12); DECLARE v_conf VARCHAR(300);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('DOCTOR');
  SELECT visit_id INTO v_visit FROM clinical_order WHERE clinical_order_id = p_order;
  IF v_visit IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown order'; END IF;
  IF visit_is_closed(v_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  SELECT status INTO v_st FROM clinical_order_status WHERE clinical_order_id = p_order ORDER BY clinical_order_status_id DESC LIMIT 1;
  IF v_st <> 'ordered' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The order is already finalized or closed; create a new order'; END IF;
  IF p_kind = 'medication' AND (p_medicine IS NULL) = (p_free_text IS NULL) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Choose a catalog medicine or a free-text name for a medication item'; END IF;
  IF p_medicine IS NOT NULL THEN
    SELECT patient_id INTO v_patient FROM visit WHERE visit_id = v_visit;
    SET v_conf = fn_allergy_conflict(v_patient, p_medicine);
    IF v_conf IS NOT NULL AND (p_override_reason IS NULL OR CHAR_LENGTH(TRIM(p_override_reason)) < 5) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Allergy warning: the patient is allergic to a listed ingredient. An override reason is required';
    END IF;
  END IF;
  START TRANSACTION;
  SELECT clinical_order_id INTO v_lock FROM clinical_order WHERE clinical_order_id = p_order FOR UPDATE;
  SELECT COALESCE(MAX(item_no), 0) + 1 INTO v_no FROM clinical_order_item WHERE clinical_order_id = p_order;
  INSERT INTO clinical_order_item (clinical_order_id, item_no, item_kind, medicine_id, free_text_medicine_name, price_list_id, dose_value, dose_unit, route,
                                   first_scheduled_at, repeat_interval_minutes, quantity_ordered, instructions)
  VALUES (p_order, v_no, p_kind, p_medicine, p_free_text, p_price_list, p_dose_value, p_dose_unit, p_route, p_first_at, p_interval_min, p_qty, p_instructions);
  SET p_item_id = LAST_INSERT_ID();
  INSERT INTO clinical_order_item_status (clinical_order_item_id, status) VALUES (p_item_id, 'active');
  IF v_conf IS NOT NULL THEN CALL raise_alert('ALLERGY_OVERRIDE', 'INTERRUPT', 'clinical_order_item', p_item_id, 'A doctor overrode an allergy warning on an order'); END IF;
  COMMIT;
END$$

-- @api roles=DOCTOR rules=R4.7 | Finalize an order: nurses see only finalized orders
CREATE PROCEDURE sp_finalize_order(IN p_order BIGINT UNSIGNED)
BEGIN
  DECLARE v_st VARCHAR(12);
  CALL int_require_role('DOCTOR');
  SELECT status INTO v_st FROM clinical_order_status WHERE clinical_order_id = p_order ORDER BY clinical_order_status_id DESC LIMIT 1;
  IF v_st <> 'ordered' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only an order in draft state can be finalized'; END IF;
  IF NOT EXISTS (SELECT 1 FROM clinical_order_item WHERE clinical_order_id = p_order) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Add at least one item first'; END IF;
  INSERT INTO clinical_order_status (clinical_order_id, status) VALUES (p_order, 'finalized');
END$$

-- @api roles=DOCTOR rules=R4.7 | Stop one order item (status stopped, with reason). Nurses cannot give a stopped item
CREATE PROCEDURE sp_stop_order_item(IN p_item BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('DOCTOR');
  IF p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A reason is required to stop an order item'; END IF;
  INSERT INTO clinical_order_item_status (clinical_order_item_id, status, reason) VALUES (p_item, 'stopped', p_reason);
END$$

-- @api roles=DOCTOR rules=R4.7 | Cancel a whole order
CREATE PROCEDURE sp_cancel_order(IN p_order BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('DOCTOR');
  INSERT INTO clinical_order_status (clinical_order_id, status, reason) VALUES (p_order, 'cancelled', p_reason);
END$$

-- ------------------------------------------------------------------ outside labs
-- @api roles=OWNER,RECEPTIONIST,DOCTOR rules=R7.1 | Add an outside lab (master)
CREATE PROCEDURE sp_add_outside_lab(IN p_name VARCHAR(120), IN p_phone VARCHAR(20), IN p_contact VARCHAR(80), IN p_notes VARCHAR(300), OUT p_lab_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('OWNER,RECEPTIONIST,DOCTOR');
  INSERT INTO outside_lab (name, phone, contact_name, notes) VALUES (p_name, p_phone, p_contact, p_notes);
  SET p_lab_id = LAST_INSERT_ID();
END$$

-- @api roles=DOCTOR rules=R7.1,R8.1,R8.2,R7.5 | own txn. Order an outside lab test. Price comes from the price list (item code) or is entered; a deviation above lab_price_deviation_pct (or a price with no list entry) needs a reason and is flagged. The bill line is generated here
CREATE PROCEDURE sp_create_lab_order(IN p_visit BIGINT UNSIGNED, IN p_lab BIGINT UNSIGNED, IN p_test VARCHAR(150), IN p_price_code VARCHAR(30),
                                     IN p_entered_price DECIMAL(12,2), IN p_reason VARCHAR(300), OUT p_lab_order_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_listed DECIMAL(12,2); DECLARE v_pl BIGINT UNSIGNED; DECLARE v_price DECIMAL(12,2); DECLARE v_dev TINYINT DEFAULT 0; DECLARE v_bill BIGINT UNSIGNED;
  DECLARE v_line BIGINT UNSIGNED; DECLARE v_basis VARCHAR(15); DECLARE v_pct INT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('DOCTOR');
  IF visit_is_closed(p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  SET v_pct = setting_int('lab_price_deviation_pct');
  IF p_price_code IS NOT NULL THEN
    SELECT price_list_id, price INTO v_pl, v_listed FROM price_list
     WHERE item_code = p_price_code AND active = 1 AND effective_from <= CURRENT_DATE AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
     ORDER BY effective_from DESC LIMIT 1;
  END IF;
  SET v_price = COALESCE(p_entered_price, v_listed);
  IF v_price IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Enter the lab price (no price-list entry found)'; END IF;
  IF v_listed IS NOT NULL AND ABS(v_price - v_listed) * 100 > v_pct * v_listed THEN SET v_dev = 1; END IF;
  IF (v_dev = 1 OR v_listed IS NULL) AND (p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A reason is required for a manual or deviating lab price';
  END IF;
  SET v_basis = IF(v_listed IS NULL, 'manual', 'price_list');
  START TRANSACTION;
  INSERT INTO lab_order (visit_id, outside_lab_id, test_name) VALUES (p_visit, p_lab, p_test);
  SET p_lab_order_id = LAST_INSERT_ID();
  INSERT INTO lab_order_status (lab_order_id, status) VALUES (p_lab_order_id, 'ordered');
  INSERT INTO lab_order_price (lab_order_id, entered_price, price_basis, expected_price, is_price_deviation, deviation_reason)
  VALUES (p_lab_order_id, v_price, v_basis, v_listed, v_dev, IF(v_dev = 1 OR v_listed IS NULL, p_reason, NULL));
  CALL ensure_open_bill(p_visit, v_bill);
  CALL int_bill_add_line(v_bill, 'lab_test', v_pl, NULL, p_lab_order_id, NULL, CONCAT('Lab: ', p_test), 1, v_price,
                         IF(v_listed IS NOT NULL AND v_dev = 0, 'price_list', 'lab_entered'), IF(v_listed IS NULL, 1, 0), v_listed, v_dev,
                         IF(v_dev = 1 OR v_listed IS NULL, p_reason, NULL), NULL, NULL, v_line);
  IF v_dev = 1 THEN CALL raise_alert('LAB_PRICE_DEVIATION', 'SUMMARY', 'lab_order', p_lab_order_id, 'A lab test was priced away from the price list'); END IF;
  COMMIT;
END$$

-- @api roles=NURSE,DOCTOR,RECEPTIONIST rules=R7.2 | Lab order step: sample_collected -> sent_to_lab (in this order) or cancelled
CREATE PROCEDURE sp_lab_order_step(IN p_lab_order BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300))
BEGIN
  DECLARE v_cur VARCHAR(20);
  CALL int_require_role('NURSE,DOCTOR,RECEPTIONIST');
  SELECT status INTO v_cur FROM lab_order_status WHERE lab_order_id = p_lab_order ORDER BY lab_order_status_id DESC LIMIT 1;
  IF v_cur IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown lab order'; END IF;
  IF NOT ((v_cur = 'ordered' AND p_status IN ('sample_collected', 'cancelled')) OR (v_cur = 'sample_collected' AND p_status IN ('sent_to_lab', 'cancelled'))
          OR (v_cur = 'sent_to_lab' AND p_status = 'cancelled')) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This lab order step is not allowed now';
  END IF;
  INSERT INTO lab_order_status (lab_order_id, status, reason) VALUES (p_lab_order, p_status, p_reason);
END$$

-- @api roles=* rules=R7.3 | 1 if the identifier value matches the patient of the lab order (patient_name, date_of_birth yyyy-mm-dd, patient_code, phone_last4)
CREATE FUNCTION lab_identifier_matches(p_lab_order BIGINT UNSIGNED, p_kind VARCHAR(20), p_value VARCHAR(120)) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v_pid BIGINT UNSIGNED; DECLARE v_n INT DEFAULT 0;
  SELECT v.patient_id INTO v_pid FROM lab_order o JOIN visit v ON v.visit_id = o.visit_id WHERE o.lab_order_id = p_lab_order;
  IF v_pid IS NULL THEN RETURN 0; END IF;
  IF p_kind = 'patient_name' THEN
    SELECT COUNT(*) INTO v_n FROM patient WHERE patient_id = v_pid AND full_name_search = LOWER(TRIM(p_value));
  ELSEIF p_kind = 'date_of_birth' THEN
    SELECT COUNT(*) INTO v_n FROM patient WHERE patient_id = v_pid AND date_of_birth = STR_TO_DATE(p_value, '%Y-%m-%d');
  ELSEIF p_kind = 'patient_code' THEN
    SELECT COUNT(*) INTO v_n FROM patient WHERE patient_id = v_pid AND display_code = UPPER(TRIM(p_value));
  ELSEIF p_kind = 'phone_last4' THEN
    SELECT COUNT(*) INTO v_n FROM patient_phone WHERE patient_id = v_pid AND CHAR_LENGTH(TRIM(p_value)) = 4 AND RIGHT(phone_number, 4) = TRIM(p_value);
  END IF;
  RETURN v_n > 0;
END$$

-- @api roles=NURSE,DOCTOR rules=R7.3,R10.2 | own txn. Attach an incoming report. Two DIFFERENT identifiers must match the order's patient. On mismatch NO report is stored: a wrong-patient interrupt alert is raised and p_message explains (p_report_id stays NULL). New version of an existing report supersedes it
CREATE PROCEDURE sp_attach_lab_report(IN p_lab_order BIGINT UNSIGNED, IN p_source VARCHAR(20), IN p_file_path VARCHAR(300), IN p_hash CHAR(64),
                                      IN p_kind1 VARCHAR(20), IN p_val1 VARCHAR(120), IN p_kind2 VARCHAR(20), IN p_val2 VARCHAR(120),
                                      OUT p_report_id BIGINT UNSIGNED, OUT p_message VARCHAR(200))
BEGIN
  DECLARE v_m1 TINYINT; DECLARE v_m2 TINYINT; DECLARE v_prev BIGINT UNSIGNED; DECLARE v_no INT; DECLARE v_cur VARCHAR(20); DECLARE v_lock BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('NURSE,DOCTOR');
  SET p_report_id = NULL, p_message = NULL;
  IF p_kind1 = p_kind2 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Confirm two DIFFERENT identifiers (for example name and date of birth)'; END IF;
  IF p_hash NOT REGEXP '^[0-9a-f]{64}$' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The content hash must be a 64-character lower-case SHA-256'; END IF;
  SELECT status INTO v_cur FROM lab_order_status WHERE lab_order_id = p_lab_order ORDER BY lab_order_status_id DESC LIMIT 1;
  IF v_cur IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown lab order'; END IF;
  IF v_cur = 'cancelled' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This lab order was cancelled'; END IF;
  SET v_m1 = lab_identifier_matches(p_lab_order, p_kind1, p_val1), v_m2 = lab_identifier_matches(p_lab_order, p_kind2, p_val2);
  IF v_m1 = 0 OR v_m2 = 0 THEN
    CALL raise_alert('WRONG_PATIENT_REPORT', 'INTERRUPT', 'lab_order', p_lab_order, 'A report did not match the patient of the lab order and was refused');
    SET p_message = 'Identifiers do not match this patient. The report was NOT attached and the owner was alerted.';
  ELSE
    START TRANSACTION;
    SELECT lab_order_id INTO v_lock FROM lab_order WHERE lab_order_id = p_lab_order FOR UPDATE;
    SELECT lab_report_id, version_number INTO v_prev, v_no FROM lab_report WHERE lab_order_id = p_lab_order ORDER BY version_number DESC LIMIT 1;
    INSERT INTO lab_report (lab_order_id, version_number, supersedes_lab_report_id, file_path, content_hash, source)
    VALUES (p_lab_order, IFNULL(v_no, 0) + 1, v_prev, p_file_path, p_hash, p_source);
    SET p_report_id = LAST_INSERT_ID();
    INSERT INTO lab_report_identifier_check (lab_report_id, identifier_kind, value_seen, matches_order) VALUES (p_report_id, p_kind1, p_val1, 1), (p_report_id, p_kind2, p_val2, 1);
    COMMIT;
    SET p_message = 'Report attached';
  END IF;
END$$

-- @api roles=NURSE,DOCTOR rules=R10.2 | Mark a report critical / not_critical (latest mark wins)
CREATE PROCEDURE sp_mark_report_critical(IN p_report BIGINT UNSIGNED, IN p_mark VARCHAR(12), IN p_source VARCHAR(20), IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('NURSE,DOCTOR');
  INSERT INTO lab_report_critical_mark (lab_report_id, mark, mark_source, reason) VALUES (p_report, p_mark, p_source, p_reason);
END$$

-- @api roles=DOCTOR rules=R7.4 | Doctor reviewed the report (own event). Only the latest, non-retracted version can be reviewed
CREATE PROCEDURE sp_review_report(IN p_report BIGINT UNSIGNED, IN p_note VARCHAR(500))
BEGIN
  DECLARE v_order BIGINT UNSIGNED; DECLARE v_max INT; DECLARE v_no INT;
  CALL int_require_role('DOCTOR');
  SELECT lab_order_id, version_number INTO v_order, v_no FROM lab_report WHERE lab_report_id = p_report;
  IF v_order IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown report'; END IF;
  SELECT MAX(version_number) INTO v_max FROM lab_report WHERE lab_order_id = v_order;
  IF v_no <> v_max THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A newer version of this report exists; review that one'; END IF;
  IF EXISTS (SELECT 1 FROM lab_report_retraction WHERE lab_report_id = p_report) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This report was retracted'; END IF;
  INSERT INTO lab_report_review (lab_report_id, review_note) VALUES (p_report, p_note);
END$$

-- @api roles=NURSE,DOCTOR rules=R10.2 | Retract a report (wrong_patient raises an interrupt alert)
CREATE PROCEDURE sp_retract_report(IN p_report BIGINT UNSIGNED, IN p_kind VARCHAR(20), IN p_correct_order BIGINT UNSIGNED, IN p_note VARCHAR(300))
BEGIN
  CALL int_require_role('NURSE,DOCTOR');
  INSERT INTO lab_report_retraction (lab_report_id, retraction_kind, correct_lab_order_id, note) VALUES (p_report, p_kind, p_correct_order, p_note);
  IF p_kind = 'wrong_patient' THEN CALL raise_alert('WRONG_PATIENT_REPORT', 'INTERRUPT', 'lab_report', p_report, 'A stored report was retracted as belonging to the wrong patient'); END IF;
END$$

DELIMITER ;

-- ------------------------------------------------------------------ views
CREATE OR REPLACE VIEW v_patient_allergy_current AS
SELECT a.patient_allergy_id, a.patient_id, canonical_patient(a.patient_id) AS canonical_patient_id, a.ingredient_id, a.allergen_text, a.reaction, a.severity
  FROM patient_allergy a
 WHERE NOT EXISTS (SELECT 1 FROM patient_allergy n WHERE n.corrects_id = a.patient_allergy_id)
   AND COALESCE((SELECT s.status FROM patient_allergy_status_event s WHERE s.patient_allergy_id = a.patient_allergy_id
                  ORDER BY s.patient_allergy_status_event_id DESC LIMIT 1), 'confirmed') IN ('confirmed', 'reactivated');

CREATE OR REPLACE VIEW v_prescription_current AS
SELECT pr.visit_id, pr.prescription_id, v.prescription_version_id, v.version_number, pi.prescription_item_id, pi.prescription_line_id, l.line_no,
       pi.item_change, pi.medicine_id, COALESCE(m.brand_name, pi.free_text_medicine_name) AS medicine_name, m.generic_display_name,
       pi.chosen_dose_text, pi.chosen_schedule, pi.food_instruction, pi.duration_days, pi.quantity_prescribed,
       (SELECT s.status FROM prescription_line_status s WHERE s.prescription_line_id = pi.prescription_line_id ORDER BY s.prescription_line_status_id DESC LIMIT 1) AS line_status
  FROM prescription pr
  JOIN prescription_version v ON v.prescription_id = pr.prescription_id
       AND v.version_number = (SELECT MAX(v2.version_number) FROM prescription_version v2 WHERE v2.prescription_id = pr.prescription_id)
  JOIN prescription_item pi ON pi.prescription_version_id = v.prescription_version_id
  JOIN prescription_line l ON l.prescription_line_id = pi.prescription_line_id
  LEFT JOIN medicine m ON m.medicine_id = pi.medicine_id;

-- Printable prescription (paper style): patient, doctor, items of the latest version
CREATE OR REPLACE VIEW v_prescription_print AS
SELECT c.visit_id, c.version_number, c.line_no, c.medicine_name, c.generic_display_name, c.chosen_dose_text, c.chosen_schedule, c.food_instruction,
       c.duration_days, c.quantity_prescribed, p.display_code AS patient_code, CONCAT_WS(' ', p.given_name, p.family_name) AS patient_name, p.date_of_birth,
       (SELECT CONCAT_WS(' ', d.given_name, d.family_name) FROM visit_doctor_assignment a JOIN staff d ON d.staff_id = a.doctor_staff_id
         WHERE a.visit_id = c.visit_id ORDER BY a.visit_doctor_assignment_id DESC LIMIT 1) AS doctor_name,
       (SELECT dd.registration_number FROM visit_doctor_assignment a JOIN doctor_detail dd ON dd.staff_id = a.doctor_staff_id
         WHERE a.visit_id = c.visit_id ORDER BY a.visit_doctor_assignment_id DESC LIMIT 1) AS doctor_registration_no
  FROM v_prescription_current c JOIN visit v ON v.visit_id = c.visit_id JOIN patient p ON p.patient_id = v.patient_id
 WHERE c.item_change <> 'discontinued' AND COALESCE(c.line_status, 'prescribed') NOT IN ('cancelled');

CREATE OR REPLACE VIEW v_catalog_review_pending AS
SELECT q.catalog_review_queue_id, q.submitted_name, q.prescription_item_id, q.occurred_at AS queued_at
  FROM catalog_review_queue q
 WHERE NOT EXISTS (SELECT 1 FROM catalog_review_event e WHERE e.catalog_review_queue_id = q.catalog_review_queue_id);

CREATE OR REPLACE VIEW v_lab_order_current AS
SELECT o.lab_order_id, o.visit_id, o.outside_lab_id, o.test_name, o.occurred_at AS ordered_at,
       (SELECT s.status FROM lab_order_status s WHERE s.lab_order_id = o.lab_order_id ORDER BY s.lab_order_status_id DESC LIMIT 1) AS step_status,
       (SELECT r.lab_report_id FROM lab_report r WHERE r.lab_order_id = o.lab_order_id ORDER BY r.version_number DESC LIMIT 1) AS latest_report_id,
       (SELECT p.entered_price FROM lab_order_price p WHERE p.lab_order_id = o.lab_order_id ORDER BY p.lab_order_price_id DESC LIMIT 1) AS price
  FROM lab_order o;

-- Latest report version per order that nobody reviewed yet (not retracted), with age and critical flag
CREATE OR REPLACE VIEW v_report_unreviewed AS
SELECT r.lab_report_id, r.lab_order_id, o.visit_id, o.test_name, r.version_number, r.occurred_at AS received_at,
       TIMESTAMPDIFF(HOUR, r.occurred_at, NOW(3)) AS hours_waiting,
       COALESCE((SELECT m.mark FROM lab_report_critical_mark m WHERE m.lab_report_id = r.lab_report_id ORDER BY m.lab_report_critical_mark_id DESC LIMIT 1), 'not_critical') = 'critical' AS is_critical
  FROM lab_report r JOIN lab_order o ON o.lab_order_id = r.lab_order_id
 WHERE r.version_number = (SELECT MAX(r2.version_number) FROM lab_report r2 WHERE r2.lab_order_id = r.lab_order_id)
   AND NOT EXISTS (SELECT 1 FROM lab_report_review v WHERE v.lab_report_id = r.lab_report_id)
   AND NOT EXISTS (SELECT 1 FROM lab_report_retraction x WHERE x.lab_report_id = r.lab_report_id);

-- Nurse task list: items of finalized orders that are still active
CREATE OR REPLACE VIEW v_nurse_tasks AS
SELECT i.clinical_order_item_id, o.clinical_order_id, o.visit_id, o.short_stay_episode_id, i.item_no, i.item_kind,
       COALESCE(m.brand_name, i.free_text_medicine_name) AS item_name, i.medicine_id, i.dose_value, i.dose_unit, i.route, i.first_scheduled_at,
       i.repeat_interval_minutes, i.quantity_ordered, i.instructions
  FROM clinical_order_item i JOIN clinical_order o ON o.clinical_order_id = i.clinical_order_id LEFT JOIN medicine m ON m.medicine_id = i.medicine_id
 WHERE (SELECT s.status FROM clinical_order_status s WHERE s.clinical_order_id = o.clinical_order_id ORDER BY s.clinical_order_status_id DESC LIMIT 1) = 'finalized'
   AND (SELECT s.status FROM clinical_order_item_status s WHERE s.clinical_order_item_id = i.clinical_order_item_id ORDER BY s.clinical_order_item_status_id DESC LIMIT 1) = 'active'
   AND visit_is_closed(o.visit_id) = 0;
