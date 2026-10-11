-- =====================================================================================
-- P3-4  Pharmacy: stock ledger, dispensing (3 sources), counter sales, custody chain, nurse doses, leftovers, cash handover  (Track B)
-- Units: quantities are in the medicine's stock_unit (tablet, ml, ...); mrp / cost_price are PER stock unit.
-- Dispensing has no visit column in the ER: the visit of a clinic-prescription dispensing is derived (v_dispensing_visit).
-- Counter sales have their own money tables (counter_sale_payment/refund); they never touch a visit bill.
-- =====================================================================================
USE clinic_db;
DELIMITER $$

-- ------------------------------------------------------------------ masters
-- @api roles=OWNER,PHARMACY rules=R4.3,R5.5,R6.3 | Add a catalog medicine (master, audited). schedule_class none/H/H1/X; returnable 0/1
CREATE PROCEDURE sp_add_medicine(IN p_brand VARCHAR(120), IN p_generic VARCHAR(160), IN p_strength_value DECIMAL(10,3), IN p_strength_unit VARCHAR(20),
                                 IN p_form VARCHAR(40), IN p_stock_unit VARCHAR(20), IN p_schedule VARCHAR(4), IN p_dose VARCHAR(60), IN p_freq VARCHAR(30),
                                 IN p_food VARCHAR(60), IN p_days SMALLINT, IN p_returnable TINYINT, OUT p_medicine_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('OWNER,PHARMACY');
  INSERT INTO medicine (brand_name, generic_display_name, strength_value, strength_unit, dosage_form, stock_unit, schedule_class, default_dose_text,
                        default_schedule, default_food_instruction, default_duration_days, returnable)
  VALUES (p_brand, p_generic, p_strength_value, p_strength_unit, p_form, p_stock_unit, IFNULL(p_schedule, 'none'), p_dose, p_freq, p_food, p_days, IFNULL(p_returnable, 0));
  SET p_medicine_id = LAST_INSERT_ID();
END$$

-- @api roles=OWNER,PHARMACY rules=R4.4 | Add an active ingredient (master)
CREATE PROCEDURE sp_add_ingredient(IN p_name VARCHAR(120), OUT p_ingredient_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('OWNER,PHARMACY');
  INSERT INTO ingredient (ingredient_name) VALUES (TRIM(p_name));
  SET p_ingredient_id = LAST_INSERT_ID();
END$$

-- @api roles=OWNER,PHARMACY rules=R4.4 | Link an ingredient to a medicine (combination drugs have several)
CREATE PROCEDURE sp_link_medicine_ingredient(IN p_medicine BIGINT UNSIGNED, IN p_ingredient BIGINT UNSIGNED, IN p_strength_text VARCHAR(60))
BEGIN
  CALL int_require_role('OWNER,PHARMACY');
  INSERT INTO medicine_ingredient (medicine_id, ingredient_id, strength_text) VALUES (p_medicine, p_ingredient, p_strength_text);
END$$

-- @api roles=OWNER,PHARMACY rules=R5.1 | Add a supplier (minimal master; optional on receipts)
CREATE PROCEDURE sp_add_supplier(IN p_name VARCHAR(120), IN p_phone VARCHAR(20), IN p_contact VARCHAR(80), IN p_notes VARCHAR(300), OUT p_supplier_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('OWNER,PHARMACY');
  INSERT INTO supplier (name, phone, contact_name, notes) VALUES (p_name, p_phone, p_contact, p_notes);
  SET p_supplier_id = LAST_INSERT_ID();
END$$

-- @api roles=OWNER,PHARMACY rules=R4.5 | Resolve a queued free-text name: mapped / added_to_catalog / rejected
CREATE PROCEDURE sp_resolve_catalog_review(IN p_queue BIGINT UNSIGNED, IN p_action VARCHAR(20), IN p_medicine BIGINT UNSIGNED, IN p_note VARCHAR(300))
BEGIN
  CALL int_require_role('OWNER,PHARMACY');
  IF p_action IN ('mapped', 'added_to_catalog') AND p_medicine IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Choose the catalog medicine this name maps to'; END IF;
  INSERT INTO catalog_review_event (catalog_review_queue_id, review_action, resolved_medicine_id, resolution_note) VALUES (p_queue, p_action, p_medicine, p_note);
END$$

-- ------------------------------------------------------------------ stock ledger
-- @api roles=PHARMACY rules=R5.1,R5.2 | own txn. Receive stock: finds or creates the batch (price fields are fixed per batch) and writes a 'received' movement
CREATE PROCEDURE sp_receive_stock(IN p_medicine BIGINT UNSIGNED, IN p_supplier BIGINT UNSIGNED, IN p_batch_code VARCHAR(40), IN p_expiry DATE,
                                  IN p_qty DECIMAL(10,2), IN p_cost DECIMAL(12,2), IN p_mrp DECIMAL(12,2), OUT p_batch_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_cost DECIMAL(12,2); DECLARE v_mrp DECIMAL(12,2); DECLARE v_exp DATE;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('PHARMACY');
  IF p_qty IS NULL OR p_qty <= 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be more than zero'; END IF;
  IF p_expiry <= CURRENT_DATE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cannot receive an already expired batch'; END IF;
  START TRANSACTION;
  SELECT stock_batch_id, cost_price, mrp, expiry_date INTO p_batch_id, v_cost, v_mrp, v_exp FROM stock_batch WHERE medicine_id = p_medicine AND batch_code = p_batch_code FOR UPDATE;
  IF p_batch_id IS NULL THEN
    INSERT INTO stock_batch (medicine_id, supplier_id, batch_code, expiry_date, cost_price, mrp) VALUES (p_medicine, p_supplier, p_batch_code, p_expiry, p_cost, p_mrp);
    SET p_batch_id = LAST_INSERT_ID();
  ELSEIF v_cost <> p_cost OR v_mrp <> p_mrp OR v_exp <> p_expiry THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Batch already exists with different expiry/prices; use a new batch code';
  END IF;
  INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta) VALUES (p_batch_id, 'received', p_qty);
  COMMIT;
END$$

-- @api roles=PHARMACY rules=R5.3,G6 | Request an adjustment (signed quantity) or an expired write-off (negative). Needs the owner's approval; execute with sp_apply_stock_adjustment
CREATE PROCEDURE sp_request_stock_adjustment(IN p_batch BIGINT UNSIGNED, IN p_delta DECIMAL(10,2), IN p_kind VARCHAR(12), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('PHARMACY');
  IF p_kind NOT IN ('adjustment', 'writeoff') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Kind must be adjustment or writeoff'; END IF;
  IF p_delta = 0 OR (p_kind = 'writeoff' AND p_delta > 0) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid quantity for this kind (write-off must be negative)'; END IF;
  CALL request_approval_ex(IF(p_kind = 'writeoff', 'stock_writeoff', 'stock_adjustment'), NULL, NULL, p_batch, NULL, NULL, NULL, p_delta, NULL, p_reason, p_approval_id);
END$$

-- @api roles=PHARMACY rules=R5.3,G6 | Apply an approved adjustment/write-off to the ledger (one movement per approval)
CREATE PROCEDURE sp_apply_stock_adjustment(IN p_approval BIGINT UNSIGNED)
BEGIN
  DECLARE v_batch BIGINT UNSIGNED; DECLARE v_qty DECIMAL(10,2); DECLARE v_type VARCHAR(30); DECLARE v_reason VARCHAR(300);
  CALL int_require_role('PHARMACY');
  SELECT stock_batch_id, requested_quantity, request_type, reason INTO v_batch, v_qty, v_type, v_reason FROM approval_request
   WHERE approval_request_id = p_approval AND request_type IN ('stock_adjustment', 'stock_writeoff');
  IF v_batch IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not a stock adjustment request'; END IF;
  INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, reason, approval_request_id)
  VALUES (v_batch, IF(v_type = 'stock_writeoff', 'expired_writeoff', 'adjustment'), v_qty, v_reason, p_approval);
END$$

-- @api roles=PHARMACY rules=R5.2 | Reverse a mistaken 'received' movement (reason mandatory, no approval per N-5). One reversal per movement
CREATE PROCEDURE sp_reverse_stock_movement(IN p_movement BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  DECLARE v_batch BIGINT UNSIGNED; DECLARE v_qty DECIMAL(10,2); DECLARE v_type VARCHAR(25);
  DECLARE EXIT HANDLER FOR 1062 SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This movement was already reversed';
  CALL int_require_role('PHARMACY');
  SELECT stock_batch_id, quantity_delta, movement_type INTO v_batch, v_qty, v_type FROM stock_movement WHERE stock_movement_id = p_movement;
  IF v_batch IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown stock movement'; END IF;
  IF v_type <> 'received' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only a received movement can be reversed; use an adjustment request for others'; END IF;
  IF EXISTS (SELECT 1 FROM stock_movement WHERE reverses_stock_movement_id = p_movement) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This movement was already reversed'; END IF;
  INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, reverses_stock_movement_id, reason) VALUES (v_batch, 'reversal', -v_qty, p_movement, p_reason);
  CALL raise_alert('STOCK_REVERSAL', 'SUMMARY', 'stock_movement', p_movement, 'A stock receipt was reversed');
END$$

-- @api roles=PHARMACY rules=R5.2 | Return stock to the supplier (negative movement, reason mandatory)
CREATE PROCEDURE sp_return_to_supplier(IN p_batch BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('PHARMACY');
  IF p_qty <= 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be more than zero'; END IF;
  INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, reason) VALUES (p_batch, 'returned_to_supplier', -p_qty, p_reason);
END$$

-- ------------------------------------------------------------------ dispensing core
-- @api roles=* rules=R5.4,R10.2 | internal pre-check: a named expired batch raises an interrupt alert (persisted: call this OUTSIDE a transaction) and refuses
CREATE PROCEDURE int_check_batch_expiry(IN p_batch BIGINT UNSIGNED)
BEGIN
  DECLARE v_exp DATE;
  IF p_batch IS NOT NULL THEN
    SELECT expiry_date INTO v_exp FROM stock_batch WHERE stock_batch_id = p_batch;
    IF v_exp IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown batch'; END IF;
    IF v_exp < CURRENT_DATE THEN
      CALL raise_alert('EXPIRED_DISPENSE_ATTEMPT', 'INTERRUPT', 'stock_batch', p_batch, 'Someone tried to dispense an expired batch');
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Expired batch cannot be dispensed';
    END IF;
  END IF;
END$$

-- @api roles=* rules=R5.2,R5.4,R5.5,R8.2 | internal (inside a transaction): FEFO allocation over non-expired batches with row locks, one stock-out movement per batch used (price snapshot = batch MRP); adds a bill medicine line when p_visit is given
CREATE PROCEDURE int_dispense_core(IN p_source VARCHAR(25), IN p_medicine BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_batch BIGINT UNSIGNED,
                                   IN p_item BIGINT UNSIGNED, IN p_outside BIGINT UNSIGNED, IN p_sale BIGINT UNSIGNED, IN p_visit BIGINT UNSIGNED,
                                   OUT p_dispensing_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_rem DECIMAL(10,2); DECLARE v_b BIGINT UNSIGNED; DECLARE v_mrp DECIMAL(12,2); DECLARE v_have DECIMAL(12,2); DECLARE v_take DECIMAL(10,2);
  DECLARE v_last_exp DATE DEFAULT '1000-01-01'; DECLARE v_last BIGINT UNSIGNED DEFAULT 0; DECLARE v_exp DATE; DECLARE v_mv BIGINT UNSIGNED;
  DECLARE v_bill BIGINT UNSIGNED; DECLARE v_line BIGINT UNSIGNED; DECLARE v_name VARCHAR(120); DECLARE v_first_exp DATE;
  SET v_rem = p_qty;
  SELECT brand_name INTO v_name FROM medicine WHERE medicine_id = p_medicine;
  INSERT INTO dispensing (source_type, medicine_id, prescription_item_id, outside_prescription_id, counter_sale_id)
  VALUES (p_source, p_medicine, p_item, p_outside, p_sale);
  SET p_dispensing_id = LAST_INSERT_ID();
  WHILE v_rem > 0 DO
    SET v_b = NULL;
    SELECT stock_batch_id, mrp, expiry_date INTO v_b, v_mrp, v_exp FROM stock_batch
     WHERE medicine_id = p_medicine AND expiry_date >= CURRENT_DATE AND (p_batch IS NULL OR stock_batch_id = p_batch)
       AND (expiry_date > v_last_exp OR (expiry_date = v_last_exp AND stock_batch_id > v_last))
     ORDER BY expiry_date, stock_batch_id LIMIT 1 FOR UPDATE;
    IF v_b IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not enough stock to dispense this quantity'; END IF;
    SET v_last_exp = v_exp, v_last = v_b;
    SELECT COALESCE(SUM(quantity_delta), 0) INTO v_have FROM stock_movement WHERE stock_batch_id = v_b;
    SET v_take = LEAST(v_have, v_rem);
    IF v_take > 0 THEN
      INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, unit_price_snapshot, dispensing_id)
      VALUES (v_b, 'dispensed', -v_take, v_mrp, p_dispensing_id);
      SET v_mv = LAST_INSERT_ID();
      IF p_visit IS NOT NULL THEN
        CALL ensure_open_bill(p_visit, v_bill);
        CALL int_bill_add_line(v_bill, 'medicine', NULL, v_mv, NULL, NULL, CONCAT('Medicine: ', v_name), v_take, v_mrp, 'batch_mrp', 0, NULL, 0, NULL, NULL, NULL, v_line);
      END IF;
      SET v_rem = v_rem - v_take;
    END IF;
  END WHILE;
  IF p_batch IS NOT NULL THEN
    SELECT MIN(expiry_date) INTO v_first_exp FROM stock_batch b WHERE b.medicine_id = p_medicine AND b.expiry_date >= CURRENT_DATE AND b.stock_batch_id <> p_batch
       AND COALESCE((SELECT SUM(quantity_delta) FROM stock_movement WHERE stock_batch_id = b.stock_batch_id), 0) > 0;
    IF v_first_exp IS NOT NULL AND v_first_exp < (SELECT expiry_date FROM stock_batch WHERE stock_batch_id = p_batch) THEN
      CALL raise_alert('FEFO_BYPASS', 'SUMMARY', 'dispensing', p_dispensing_id, 'A batch was chosen although an earlier-expiry batch had stock');
    END IF;
  END IF;
END$$

-- @api roles=PHARMACY rules=R5.5,R2.5,R6.2,R8.2,R5.2 | own txn. Dispense a clinic prescription item (latest version, catalog medicine only). FEFO unless a batch is named. Creates stock-out movements + bill lines + the custody handoff to a nurse (staff) or attendant. p_order_item links the supply to a short-stay order item (R6.2)
CREATE PROCEDURE sp_dispense_clinic_prescription(IN p_item BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_batch BIGINT UNSIGNED, IN p_order_item BIGINT UNSIGNED,
                                                 IN p_recipient_staff BIGINT UNSIGNED, IN p_recipient_attendant BIGINT UNSIGNED, IN p_purpose VARCHAR(20),
                                                 OUT p_dispensing_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_med BIGINT UNSIGNED; DECLARE v_rx BIGINT UNSIGNED; DECLARE v_line BIGINT UNSIGNED; DECLARE v_change VARCHAR(15); DECLARE v_qtyp DECIMAL(10,2);
  DECLARE v_ver INT; DECLARE v_max INT; DECLARE v_visit BIGINT UNSIGNED; DECLARE v_lstatus VARCHAR(20); DECLARE v_done DECIMAL(12,2); DECLARE v_om BIGINT UNSIGNED; DECLARE v_ov BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('PHARMACY');
  IF p_qty IS NULL OR p_qty <= 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be more than zero'; END IF;
  IF (p_recipient_staff IS NULL) = (p_recipient_attendant IS NULL) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Name exactly one recipient: a staff member or an attendant'; END IF;
  IF p_purpose NOT IN ('supply_to_nurse', 'take_home') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Purpose must be supply_to_nurse or take_home'; END IF;
  SELECT pi.medicine_id, pi.prescription_id, pi.prescription_line_id, pi.item_change, pi.quantity_prescribed, v.version_number, pr.visit_id
    INTO v_med, v_rx, v_line, v_change, v_qtyp, v_ver, v_visit
    FROM prescription_item pi JOIN prescription_version v ON v.prescription_version_id = pi.prescription_version_id JOIN prescription pr ON pr.prescription_id = pi.prescription_id
   WHERE pi.prescription_item_id = p_item;
  IF v_rx IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown prescription item'; END IF;
  SELECT MAX(version_number) INTO v_max FROM prescription_version WHERE prescription_id = v_rx;
  IF v_ver <> v_max OR v_change = 'discontinued' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This item is not in the latest prescription; use the current version'; END IF;
  IF v_med IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Free-text medicine is not in the catalog; map it first or mark it bought outside'; END IF;
  SELECT status INTO v_lstatus FROM prescription_line_status WHERE prescription_line_id = v_line ORDER BY prescription_line_status_id DESC LIMIT 1;
  IF COALESCE(v_lstatus, 'prescribed') <> 'prescribed' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This prescription line is no longer open for dispensing'; END IF;
  IF visit_is_closed(v_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  IF p_order_item IS NOT NULL THEN
    SELECT i.medicine_id, o.visit_id INTO v_om, v_ov FROM clinical_order_item i JOIN clinical_order o ON o.clinical_order_id = i.clinical_order_id WHERE i.clinical_order_item_id = p_order_item;
    IF v_om IS NULL OR v_om <> v_med OR v_ov <> v_visit THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The order item does not match this medicine and visit'; END IF;
  END IF;
  CALL int_check_batch_expiry(p_batch);
  START TRANSACTION;
  SELECT COALESCE(-SUM(sm.quantity_delta), 0) INTO v_done FROM stock_movement sm JOIN dispensing d ON d.dispensing_id = sm.dispensing_id
    JOIN prescription_item pi ON pi.prescription_item_id = d.prescription_item_id WHERE pi.prescription_line_id = v_line AND sm.movement_type = 'dispensed';
  IF v_qtyp IS NOT NULL AND v_done + p_qty > v_qtyp THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'More than the prescribed quantity'; END IF;
  CALL int_dispense_core('clinic_prescription', v_med, p_qty, p_batch, p_item, NULL, NULL, v_visit, p_dispensing_id);
  IF p_order_item IS NOT NULL THEN INSERT INTO order_item_supply (clinical_order_item_id, dispensing_id, quantity_allocated) VALUES (p_order_item, p_dispensing_id, p_qty); END IF;
  INSERT INTO custody_handoff (dispensing_id, giver_staff_id, recipient_staff_id, recipient_attendant_id, quantity, handoff_purpose)
  VALUES (p_dispensing_id, app_user(), p_recipient_staff, p_recipient_attendant, p_qty, p_purpose);
  COMMIT;
END$$

-- ------------------------------------------------------------------ counter customers and sales
-- @api roles=PHARMACY,RECEPTIONIST rules=R5.6 | Register a counter buyer (not a patient). A temporary code is always generated
CREATE PROCEDURE sp_register_counter_customer(IN p_name VARCHAR(120), IN p_phone VARCHAR(20), OUT p_customer_id BIGINT UNSIGNED, OUT p_code VARCHAR(30))
BEGIN
  CALL int_require_role('PHARMACY,RECEPTIONIST');
  CALL next_code('counter_customer', p_code);
  INSERT INTO counter_customer (phone, temporary_code, display_name) VALUES (NULLIF(p_phone, ''), p_code, NULLIF(p_name, ''));
  SET p_customer_id = LAST_INSERT_ID();
END$$

-- @api roles=PHARMACY,RECEPTIONIST,OWNER rules=R5.6,G12 | Human-confirmed, audited link of a counter customer to a patient
CREATE PROCEDURE sp_link_counter_customer(IN p_customer BIGINT UNSIGNED, IN p_patient BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('PHARMACY,RECEPTIONIST,OWNER');
  INSERT INTO counter_customer_link_event (counter_customer_id, patient_id, reason) VALUES (p_customer, p_patient, p_reason);
END$$

-- @api roles=PHARMACY rules=R5.5,R5.7 | Start a counter sale (header) for a counter customer
CREATE PROCEDURE sp_start_counter_sale(IN p_customer BIGINT UNSIGNED, OUT p_sale_id BIGINT UNSIGNED, OUT p_sale_number VARCHAR(30))
BEGIN
  CALL int_require_role('PHARMACY');
  CALL next_code('counter_sale', p_sale_number);
  INSERT INTO counter_sale (counter_customer_id, sale_number) VALUES (p_customer, p_sale_number);
  SET p_sale_id = LAST_INSERT_ID();
END$$

-- @api roles=PHARMACY rules=R5.5,R5.8 | Record a prescription written elsewhere (prescriber name, registration no., optional photo path) for a counter customer
CREATE PROCEDURE sp_record_outside_prescription(IN p_customer BIGINT UNSIGNED, IN p_prescriber VARCHAR(120), IN p_reg_no VARCHAR(40), IN p_photo VARCHAR(300),
                                                IN p_date DATE, OUT p_outside_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('PHARMACY');
  INSERT INTO outside_prescription (counter_customer_id, prescriber_name, prescriber_registration_no, photo_path, prescription_date)
  VALUES (p_customer, p_prescriber, NULLIF(TRIM(p_reg_no), ''), p_photo, p_date);
  SET p_outside_id = LAST_INSERT_ID();
END$$

-- @api roles=PHARMACY rules=R5.5,R5.8 | own txn. Over-the-counter sale. Schedule H/H1/X is refused by the database
CREATE PROCEDURE sp_dispense_otc(IN p_sale BIGINT UNSIGNED, IN p_medicine BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_batch BIGINT UNSIGNED, OUT p_dispensing_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_sched VARCHAR(4);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('PHARMACY');
  IF p_qty IS NULL OR p_qty <= 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be more than zero'; END IF;
  SELECT schedule_class INTO v_sched FROM medicine WHERE medicine_id = p_medicine;
  IF v_sched IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown medicine'; END IF;
  IF v_sched <> 'none' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Schedule H/H1/X medicines cannot be sold over the counter'; END IF;
  CALL int_check_batch_expiry(p_batch);
  START TRANSACTION;
  CALL int_dispense_core('otc', p_medicine, p_qty, p_batch, NULL, NULL, p_sale, NULL, p_dispensing_id);
  COMMIT;
END$$

-- @api roles=PHARMACY rules=R5.5,R5.8 | own txn. Sale against an outside prescription. Scheduled medicines need the prescriber's registration number; a missing photo is flagged for the owner
CREATE PROCEDURE sp_dispense_outside_rx(IN p_sale BIGINT UNSIGNED, IN p_outside BIGINT UNSIGNED, IN p_medicine BIGINT UNSIGNED, IN p_qty DECIMAL(10,2),
                                        IN p_batch BIGINT UNSIGNED, OUT p_dispensing_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_sched VARCHAR(4); DECLARE v_reg VARCHAR(40); DECLARE v_photo VARCHAR(300);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('PHARMACY');
  IF p_qty IS NULL OR p_qty <= 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be more than zero'; END IF;
  SELECT schedule_class INTO v_sched FROM medicine WHERE medicine_id = p_medicine;
  IF v_sched IS NULL OR NOT EXISTS (SELECT 1 FROM outside_prescription o JOIN counter_sale s ON s.counter_customer_id = o.counter_customer_id
                                     WHERE o.outside_prescription_id = p_outside AND s.counter_sale_id = p_sale) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown medicine, or the outside prescription is not of this buyer';
  END IF;
  SELECT prescriber_registration_no, photo_path INTO v_reg, v_photo FROM outside_prescription WHERE outside_prescription_id = p_outside;
  IF v_sched <> 'none' AND v_reg IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Prescriber registration number is required for this scheduled medicine'; END IF;
  CALL int_check_batch_expiry(p_batch);
  START TRANSACTION;
  CALL int_dispense_core('outside_prescription', p_medicine, p_qty, p_batch, NULL, p_outside, p_sale, NULL, p_dispensing_id);
  IF v_sched <> 'none' AND v_photo IS NULL THEN CALL raise_alert('WEAK_RX_FLAGGED_DRUG', 'SUMMARY', 'outside_prescription', p_outside, 'Scheduled medicine sold on an outside prescription without a photo'); END IF;
  COMMIT;
END$$

-- @api roles=PHARMACY rules=R5.7,R8.5 | own txn. Record a payment for a counter sale (amount cannot exceed the balance). Auto receipt SMS if the buyer has a phone
CREATE PROCEDURE sp_counter_sale_payment(IN p_sale BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_method VARCHAR(5), OUT p_payment_id BIGINT UNSIGNED, OUT p_receipt VARCHAR(30))
BEGIN
  DECLARE v_balance DECIMAL(14,2); DECLARE v_phone VARCHAR(20);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('PHARMACY');
  START TRANSACTION;
  SELECT balance, customer_phone INTO v_balance, v_phone FROM v_counter_sale_summary WHERE counter_sale_id = p_sale;
  IF v_balance IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown counter sale'; END IF;
  IF p_amount <= 0 OR p_amount > v_balance THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Payment must be more than zero and not more than the balance'; END IF;
  CALL next_code('receipt', p_receipt);
  INSERT INTO counter_sale_payment (counter_sale_id, amount, method, receipt_number) VALUES (p_sale, p_amount, p_method, p_receipt);
  SET p_payment_id = LAST_INSERT_ID();
  CALL queue_message(v_phone, 'receipt', CONCAT('Payment received at ', setting_text('clinic_name'), '. Receipt ', p_receipt), 'counter_sale_payment', p_payment_id);
  COMMIT;
END$$

-- @api roles=PHARMACY rules=R5.7,G6 | own txn. Return medicine from a counter sale: stock comes back (returned_by_patient), a return row is written and a refund approval request is raised for the owner
CREATE PROCEDURE sp_counter_sale_return(IN p_sale BIGINT UNSIGNED, IN p_dispensed_movement BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_reason VARCHAR(300),
                                        OUT p_return_id BIGINT UNSIGNED, OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_batch BIGINT UNSIGNED; DECLARE v_price DECIMAL(12,2); DECLARE v_orig DECIMAL(10,2); DECLARE v_back DECIMAL(10,2); DECLARE v_mv BIGINT UNSIGNED; DECLARE v_med BIGINT UNSIGNED; DECLARE v_returnable TINYINT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('PHARMACY');
  IF p_qty <= 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be more than zero'; END IF;
  START TRANSACTION;
  SELECT sm.stock_batch_id, sm.unit_price_snapshot, -sm.quantity_delta, d.medicine_id INTO v_batch, v_price, v_orig, v_med
    FROM stock_movement sm JOIN dispensing d ON d.dispensing_id = sm.dispensing_id
   WHERE sm.stock_movement_id = p_dispensed_movement AND sm.movement_type = 'dispensed' AND d.counter_sale_id = p_sale FOR UPDATE;
  IF v_batch IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That movement does not belong to this counter sale'; END IF;
  SELECT returnable INTO v_returnable FROM medicine WHERE medicine_id = v_med;
  IF v_returnable = 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This medicine is not returnable'; END IF;
  SELECT COALESCE(SUM(quantity_delta), 0) INTO v_back FROM stock_movement WHERE returns_stock_movement_id = p_dispensed_movement;
  IF p_qty > v_orig - v_back THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'More than was sold (minus earlier returns)'; END IF;
  INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, unit_price_snapshot, returns_stock_movement_id, reason)
  VALUES (v_batch, 'returned_by_patient', p_qty, v_price, p_dispensed_movement, p_reason);
  SET v_mv = LAST_INSERT_ID();
  INSERT INTO counter_sale_return (counter_sale_id, returned_stock_movement_id, reason) VALUES (p_sale, v_mv, p_reason);
  SET p_return_id = LAST_INSERT_ID();
  CALL request_approval_ex('counter_sale_refund', NULL, NULL, NULL, NULL, p_sale, ROUND(p_qty * v_price, 2), NULL, NULL, p_reason, p_approval_id);
  COMMIT;
END$$

-- @api roles=PHARMACY rules=R5.7,G6 | Pay out an approved counter-sale refund (amount = the approved amount; receipt generated)
CREATE PROCEDURE sp_execute_counter_refund(IN p_approval BIGINT UNSIGNED, IN p_method VARCHAR(5), OUT p_receipt VARCHAR(30))
BEGIN
  DECLARE v_sale BIGINT UNSIGNED; DECLARE v_amt DECIMAL(12,2); DECLARE v_ret BIGINT UNSIGNED;
  CALL int_require_role('PHARMACY');
  SELECT counter_sale_id, requested_amount INTO v_sale, v_amt FROM approval_request WHERE approval_request_id = p_approval AND request_type = 'counter_sale_refund';
  IF v_sale IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not a counter-sale refund request'; END IF;
  SELECT MAX(r.counter_sale_return_id) INTO v_ret FROM counter_sale_return r
   WHERE r.counter_sale_id = v_sale AND NOT EXISTS (SELECT 1 FROM counter_sale_refund f WHERE f.counter_sale_return_id = r.counter_sale_return_id);
  CALL next_code('receipt', p_receipt);
  INSERT INTO counter_sale_refund (counter_sale_id, counter_sale_return_id, amount, method, receipt_number, approval_request_id)
  VALUES (v_sale, v_ret, v_amt, p_method, p_receipt, p_approval);
END$$

-- ------------------------------------------------------------------ custody chain and nurse doses
-- @api roles=* rules=R6.2 | internal: units of a dispensing still with the clinic = dispensed - consumed - returned - taken home/handed to family
CREATE FUNCTION dispensing_remaining(p_dispensing BIGINT UNSIGNED) RETURNS DECIMAL(12,2) NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v DECIMAL(12,2);
  SELECT dispensed_qty - consumed_qty - returned_qty - family_qty INTO v FROM v_dispensing_custody WHERE dispensing_id = p_dispensing;
  RETURN COALESCE(v, 0);
END$$

-- @api roles=* rules=R6.1 | internal: units left on one order-item supply (allocated - consumed by doses that used it)
CREATE FUNCTION supply_remaining(p_supply BIGINT UNSIGNED) RETURNS DECIMAL(12,2) NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v DECIMAL(12,2);
  SELECT s.quantity_allocated - COALESCE((SELECT SUM(da.quantity_consumed) FROM dose_administration da WHERE da.order_item_supply_id = s.order_item_supply_id
            AND NOT EXISTS (SELECT 1 FROM dose_administration n WHERE n.corrects_id = da.dose_administration_id)), 0) INTO v
    FROM order_item_supply s WHERE s.order_item_supply_id = p_supply;
  RETURN COALESCE(v, 0);
END$$

-- @api roles=PHARMACY,NURSE rules=R2.5,R6.2 | Hand medicine to a nurse/attendant (supply_to_nurse or take_home). The giver is the logged-in person (G1); the recipient acknowledges separately
CREATE PROCEDURE sp_handoff_custody(IN p_dispensing BIGINT UNSIGNED, IN p_recipient_staff BIGINT UNSIGNED, IN p_recipient_attendant BIGINT UNSIGNED, IN p_qty DECIMAL(10,2),
                                    IN p_purpose VARCHAR(20), OUT p_handoff_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('PHARMACY,NURSE');
  IF p_purpose NOT IN ('supply_to_nurse', 'take_home') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Purpose must be supply_to_nurse or take_home'; END IF;
  IF p_qty <= 0 OR p_qty > dispensing_remaining(p_dispensing) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity is more than what is left of this dispensing'; END IF;
  INSERT INTO custody_handoff (dispensing_id, giver_staff_id, recipient_staff_id, recipient_attendant_id, quantity, handoff_purpose)
  VALUES (p_dispensing, app_user(), p_recipient_staff, p_recipient_attendant, p_qty, p_purpose);
  SET p_handoff_id = LAST_INSERT_ID();
END$$

-- @api roles=NURSE,PHARMACY,DOCTOR,RECEPTIONIST rules=R2.5,R6.2,G1 | Recipient (or a staff witness for an attendant) acknowledges a handoff. The giver cannot acknowledge his own handoff
CREATE PROCEDURE sp_acknowledge_handoff(IN p_handoff BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_method VARCHAR(25), IN p_note VARCHAR(300))
BEGIN
  CALL int_require_role('NURSE,PHARMACY,DOCTOR,RECEPTIONIST');
  INSERT INTO custody_handoff_acknowledgement (custody_handoff_id, acknowledged_quantity, ack_method, witness_note) VALUES (p_handoff, p_qty, p_method, p_note);
END$$

-- @api roles=NURSE rules=R6.1,R6.2,R10.2 | own txn. Record one dose event. Ordered values are copied from the order item; actual values may differ only with a reason (above the order also needs a doctor as authorizer and raises an interrupt). Given doses consume units from a dispensed supply
CREATE PROCEDURE sp_administer_dose(IN p_item BIGINT UNSIGNED, IN p_status VARCHAR(10), IN p_supply BIGINT UNSIGNED, IN p_consumed DECIMAL(10,2),
                                    IN p_actual_value DECIMAL(10,3), IN p_actual_unit VARCHAR(20), IN p_actual_route VARCHAR(30), IN p_reason VARCHAR(300),
                                    IN p_authorized_by BIGINT UNSIGNED, IN p_corrects BIGINT UNSIGNED, OUT p_dose_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_visit BIGINT UNSIGNED; DECLARE v_ov DECIMAL(10,3); DECLARE v_ou VARCHAR(20); DECLARE v_or VARCHAR(30); DECLARE v_med BIGINT UNSIGNED;
  DECLARE v_ost VARCHAR(12); DECLARE v_ist VARCHAR(12); DECLARE v_sup BIGINT UNSIGNED; DECLARE v_cons DECIMAL(10,2); DECLARE v_dev TINYINT DEFAULT 0; DECLARE v_above TINYINT DEFAULT 0;
  DECLARE v_av DECIMAL(10,3); DECLARE v_ar VARCHAR(30);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('NURSE');
  SELECT o.visit_id, i.dose_value, i.dose_unit, i.route, i.medicine_id INTO v_visit, v_ov, v_ou, v_or, v_med
    FROM clinical_order_item i JOIN clinical_order o ON o.clinical_order_id = i.clinical_order_id WHERE i.clinical_order_item_id = p_item;
  IF v_visit IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown order item'; END IF;
  IF visit_is_closed(v_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
  SELECT s.status INTO v_ost FROM clinical_order_status s JOIN clinical_order_item i ON i.clinical_order_id = s.clinical_order_id WHERE i.clinical_order_item_id = p_item ORDER BY s.clinical_order_status_id DESC LIMIT 1;
  IF v_ost <> 'finalized' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The order is not finalized (or was cancelled)'; END IF;
  SELECT status INTO v_ist FROM clinical_order_item_status WHERE clinical_order_item_id = p_item ORDER BY clinical_order_item_status_id DESC LIMIT 1;
  IF v_ist <> 'active' AND p_status <> 'stopped' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This order item was stopped by the doctor'; END IF;
  SET v_cons = IF(p_status = 'given', IFNULL(p_consumed, 0), 0);
  IF p_status <> 'given' AND (p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A reason is required when the dose was not given'; END IF;
  SET v_av = IF(p_status = 'given', COALESCE(p_actual_value, v_ov), NULL), v_ar = IF(p_status = 'given', COALESCE(p_actual_route, v_or), NULL);
  IF p_status = 'given' THEN
    IF (v_av IS NOT NULL AND v_ov IS NOT NULL AND v_av <> v_ov) OR (v_ar IS NOT NULL AND v_or IS NOT NULL AND v_ar <> v_or) THEN SET v_dev = 1; END IF;
    IF v_av IS NOT NULL AND v_ov IS NOT NULL AND v_av > v_ov THEN SET v_above = 1; END IF;
    IF v_dev = 1 AND (p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Actual dose/route differs from the order: a reason is required'; END IF;
    IF v_above = 1 AND (p_authorized_by IS NULL OR NOT staff_has_role(p_authorized_by, 'DOCTOR') OR p_authorized_by = app_user()) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A dose above the order needs a doctor as authorizer';
    END IF;
  END IF;
  START TRANSACTION;
  SET v_sup = p_supply;
  IF v_cons > 0 THEN
    IF v_sup IS NULL THEN
      SELECT s.order_item_supply_id INTO v_sup FROM order_item_supply s WHERE s.clinical_order_item_id = p_item AND supply_remaining(s.order_item_supply_id) >= v_cons ORDER BY s.order_item_supply_id LIMIT 1 FOR UPDATE;
    END IF;
    IF v_sup IS NULL OR supply_remaining(v_sup) < v_cons THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No dispensed supply left for this item; ask the pharmacy'; END IF;
  ELSE SET v_sup = NULL;
  END IF;
  INSERT INTO dose_administration (clinical_order_item_id, order_item_supply_id, status, ordered_dose_value, ordered_dose_unit, ordered_route, actual_dose_value, actual_dose_unit,
                                   actual_route, quantity_consumed, deviation_reason, deviation_authorized_by, corrects_id)
  VALUES (p_item, v_sup, p_status, v_ov, v_ou, v_or, v_av, IF(p_status = 'given', COALESCE(p_actual_unit, v_ou), NULL), v_ar, v_cons, p_reason, IF(v_above = 1, p_authorized_by, NULL), p_corrects);
  SET p_dose_id = LAST_INSERT_ID();
  IF v_above = 1 THEN CALL raise_alert('DOSE_ABOVE_ORDER', 'INTERRUPT', 'dose_administration', p_dose_id, 'A dose above the ordered dose was recorded'); END IF;
  COMMIT;
END$$

-- @api roles=NURSE rules=R6.3 | Ask pharmacy to take leftover medicine back (quantity cannot exceed what is left of the dispensing)
CREATE PROCEDURE sp_request_leftover_return(IN p_dispensing BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_note VARCHAR(300), OUT p_request_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('NURSE');
  IF p_qty <= 0 OR p_qty > dispensing_remaining(p_dispensing) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity is more than what is left of this dispensing'; END IF;
  INSERT INTO leftover_return_request (dispensing_id, quantity, condition_note) VALUES (p_dispensing, p_qty, p_note);
  SET p_request_id = LAST_INSERT_ID();
END$$

-- @api roles=PHARMACY rules=R6.3,R8.3,G6 | own txn. Pharmacist decides a leftover request. ACCEPT (returnable medicine, batch not expired): stock comes back, a return_credit bill line is added, and a refund approval is requested if the bill is already overpaid. REFUSE: hands the units to an attendant (p_attendant required)
CREATE PROCEDURE sp_decide_leftover(IN p_request BIGINT UNSIGNED, IN p_decision VARCHAR(10), IN p_reason VARCHAR(300), IN p_attendant BIGINT UNSIGNED, OUT p_decision_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_disp BIGINT UNSIGNED; DECLARE v_qty DECIMAL(10,2); DECLARE v_med BIGINT UNSIGNED; DECLARE v_ret TINYINT; DECLARE v_mv BIGINT UNSIGNED; DECLARE v_batch BIGINT UNSIGNED;
  DECLARE v_price DECIMAL(12,2); DECLARE v_exp DATE; DECLARE v_new_mv BIGINT UNSIGNED; DECLARE v_visit BIGINT UNSIGNED; DECLARE v_bill BIGINT UNSIGNED; DECLARE v_bl BIGINT UNSIGNED;
  DECLARE v_line BIGINT UNSIGNED; DECLARE v_bal DECIMAL(14,2); DECLARE v_appr BIGINT UNSIGNED; DECLARE v_h BIGINT UNSIGNED; DECLARE v_name VARCHAR(120);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('PHARMACY');
  IF p_decision NOT IN ('accepted', 'refused') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Decision must be accepted or refused'; END IF;
  IF p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A reason is required'; END IF;
  SELECT dispensing_id, quantity INTO v_disp, v_qty FROM leftover_return_request WHERE leftover_return_request_id = p_request;
  IF v_disp IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown leftover request'; END IF;
  START TRANSACTION;
  IF p_decision = 'accepted' THEN
    SELECT d.medicine_id, m.returnable, m.brand_name INTO v_med, v_ret, v_name FROM dispensing d JOIN medicine m ON m.medicine_id = d.medicine_id WHERE d.dispensing_id = v_disp;
    IF v_ret = 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This medicine is not returnable; refuse and hand it to the family'; END IF;
    SELECT sm.stock_movement_id, sm.stock_batch_id, sm.unit_price_snapshot, b.expiry_date INTO v_mv, v_batch, v_price, v_exp
      FROM stock_movement sm JOIN stock_batch b ON b.stock_batch_id = sm.stock_batch_id
     WHERE sm.dispensing_id = v_disp AND sm.movement_type = 'dispensed'
       AND -sm.quantity_delta - COALESCE((SELECT SUM(r.quantity_delta) FROM stock_movement r WHERE r.returns_stock_movement_id = sm.stock_movement_id), 0) >= v_qty
     ORDER BY sm.stock_movement_id LIMIT 1 FOR UPDATE;
    IF v_mv IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Leftover spans several batches; return it in smaller parts'; END IF;
    IF v_exp < CURRENT_DATE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The batch has expired; refuse the return'; END IF;
    INSERT INTO stock_movement (stock_batch_id, movement_type, quantity_delta, unit_price_snapshot, returns_stock_movement_id, reason) VALUES (v_batch, 'returned_by_patient', v_qty, v_price, v_mv, p_reason);
    SET v_new_mv = LAST_INSERT_ID();
    INSERT INTO leftover_return_decision (leftover_return_request_id, decision, decision_reason, stock_movement_id) VALUES (p_request, 'accepted', p_reason, v_new_mv);
    SET p_decision_id = LAST_INSERT_ID();
    SELECT visit_id INTO v_visit FROM v_dispensing_visit WHERE dispensing_id = v_disp;
    IF v_visit IS NOT NULL THEN
      SELECT bill_id INTO v_bill FROM bill WHERE visit_id = v_visit;
      SELECT bill_line_id INTO v_bl FROM bill_line WHERE source_stock_movement_id = v_mv;
      IF v_bill IS NOT NULL AND v_bl IS NOT NULL THEN
        CALL int_bill_add_line(v_bill, 'return_credit', NULL, NULL, NULL, NULL, CONCAT('Returned unused: ', v_name), v_qty, -v_price, 'batch_mrp', 0, NULL, 0, p_reason, v_bl, NULL, v_line);
        SELECT balance INTO v_bal FROM v_bill_summary WHERE bill_id = v_bill;
        IF v_bal < 0 THEN CALL request_approval_ex('refund', v_visit, v_bill, NULL, NULL, NULL, -v_bal, NULL, NULL, 'Leftover medicine returned after payment', v_appr); END IF;
      END IF;
    END IF;
  ELSE
    IF p_attendant IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Name the attendant who receives the refused leftover'; END IF;
    INSERT INTO leftover_return_decision (leftover_return_request_id, decision, decision_reason) VALUES (p_request, 'refused', p_reason);
    SET p_decision_id = LAST_INSERT_ID();
    INSERT INTO custody_handoff (dispensing_id, giver_staff_id, recipient_attendant_id, quantity, handoff_purpose, leftover_return_decision_id)
    VALUES (v_disp, app_user(), p_attendant, v_qty, 'leftover_to_family', p_decision_id);
  END IF;
  COMMIT;
END$$

-- ------------------------------------------------------------------ cash handover
-- @api roles=PHARMACY rules=R5.7 | Declare cash being handed to the accountant for a period. A difference from the system's expected cash is flagged for the owner
CREATE PROCEDURE sp_declare_cash_handover(IN p_start DATETIME, IN p_end DATETIME, IN p_declared DECIMAL(12,2), IN p_note VARCHAR(300), OUT p_declaration_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_exp DECIMAL(14,2);
  CALL int_require_role('PHARMACY');
  SELECT COALESCE(SUM(IF(p.corrects_id IS NULL, p.amount, -p.amount)), 0) INTO v_exp FROM counter_sale_payment p
   WHERE p.recorded_by = app_user() AND p.method = 'cash' AND p.recorded_at >= p_start AND p.recorded_at < p_end;
  SET v_exp = v_exp - COALESCE((SELECT SUM(f.amount) FROM counter_sale_refund f WHERE f.recorded_by = app_user() AND f.method = 'cash' AND f.recorded_at >= p_start AND f.recorded_at < p_end), 0);
  INSERT INTO cash_handover_declaration (period_start, period_end, declared_amount, note) VALUES (p_start, p_end, p_declared, p_note);
  SET p_declaration_id = LAST_INSERT_ID();
  IF v_exp <> p_declared THEN CALL raise_alert('CASH_DECLARATION_DIFF', 'SUMMARY', 'cash_handover_declaration', p_declaration_id, CONCAT('Declared cash differs from expected by ', ABS(v_exp - p_declared))); END IF;
END$$

-- @api roles=ACCOUNTANT rules=R5.7 | Count the handed-over cash (must be a different person from the declarer). A difference raises an alert
CREATE PROCEDURE sp_count_cash_handover(IN p_declaration BIGINT UNSIGNED, IN p_counted DECIMAL(12,2), IN p_note VARCHAR(300))
BEGIN
  DECLARE v_decl DECIMAL(12,2);
  CALL int_require_role('ACCOUNTANT');
  SELECT declared_amount INTO v_decl FROM cash_handover_declaration WHERE cash_handover_declaration_id = p_declaration;
  IF v_decl IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown cash handover'; END IF;
  INSERT INTO cash_handover_count (cash_handover_declaration_id, counted_amount, note) VALUES (p_declaration, p_counted, p_note);
  IF v_decl <> p_counted THEN CALL raise_alert('CASH_COUNT_DIFF', 'SUMMARY', 'cash_handover_declaration', p_declaration, 'Counted cash differs from the declared amount'); END IF;
END$$

DELIMITER ;

-- ------------------------------------------------------------------ views (derived data, G3)
CREATE OR REPLACE VIEW v_stock_by_batch AS
SELECT b.stock_batch_id, b.medicine_id, m.brand_name, m.generic_display_name, m.strength_value, m.strength_unit, m.dosage_form, m.schedule_class,
       b.batch_code, b.expiry_date, b.cost_price, b.mrp, b.supplier_id,
       COALESCE((SELECT SUM(sm.quantity_delta) FROM stock_movement sm WHERE sm.stock_batch_id = b.stock_batch_id), 0) AS qty_on_hand,
       (b.expiry_date < CURRENT_DATE) AS is_expired
  FROM stock_batch b JOIN medicine m ON m.medicine_id = b.medicine_id;

-- Same without cost price/supplier: for everybody except owner and pharmacy (G10)
CREATE OR REPLACE VIEW v_stock_batch_nocost AS
SELECT stock_batch_id, medicine_id, brand_name, generic_display_name, strength_value, strength_unit, dosage_form, schedule_class, batch_code, expiry_date, mrp, qty_on_hand, is_expired
  FROM v_stock_by_batch;

CREATE OR REPLACE VIEW v_stock_by_medicine AS
SELECT m.medicine_id, m.brand_name, m.generic_display_name, m.strength_value, m.strength_unit, m.dosage_form, m.schedule_class, m.returnable,
       COALESCE((SELECT SUM(x.qty_on_hand) FROM v_stock_by_batch x WHERE x.medicine_id = m.medicine_id AND x.is_expired = 0), 0) AS sellable_qty,
       COALESCE((SELECT SUM(x.qty_on_hand) FROM v_stock_by_batch x WHERE x.medicine_id = m.medicine_id), 0) AS total_qty
  FROM medicine m WHERE m.active = 1;

CREATE OR REPLACE VIEW v_expiring_soon AS
SELECT x.stock_batch_id, x.medicine_id, x.brand_name, x.batch_code, x.expiry_date, x.qty_on_hand, DATEDIFF(x.expiry_date, CURRENT_DATE) AS days_left,
       s.name AS supplier_name, s.phone AS supplier_phone
  FROM v_stock_by_batch x LEFT JOIN supplier s ON s.supplier_id = x.supplier_id
 WHERE x.qty_on_hand > 0 AND x.expiry_date <= CURRENT_DATE + INTERVAL setting_int('expiry_alert_days') DAY;

CREATE OR REPLACE VIEW v_fefo_batches AS
SELECT t.* FROM (SELECT x.stock_batch_id, x.medicine_id, x.brand_name, x.batch_code, x.expiry_date, x.mrp, x.qty_on_hand,
                        ROW_NUMBER() OVER (PARTITION BY x.medicine_id ORDER BY x.expiry_date, x.stock_batch_id) AS fefo_rank
                   FROM v_stock_by_batch x WHERE x.qty_on_hand > 0 AND x.is_expired = 0) t;

CREATE OR REPLACE VIEW v_restock_demand AS
SELECT m.medicine_id, m.brand_name, COUNT(*) AS outside_lines_30d, COALESCE(s.sellable_qty, 0) AS sellable_qty
  FROM prescription_line_status st
  JOIN prescription_item pi ON pi.prescription_item_id = (SELECT MAX(p2.prescription_item_id) FROM prescription_item p2 WHERE p2.prescription_line_id = st.prescription_line_id)
  JOIN medicine m ON m.medicine_id = pi.medicine_id
  LEFT JOIN v_stock_by_medicine s ON s.medicine_id = m.medicine_id
 WHERE st.status = 'bought_outside' AND st.occurred_at >= NOW(3) - INTERVAL 30 DAY
 GROUP BY m.medicine_id, m.brand_name, s.sellable_qty;

-- visit of every clinic-prescription dispensing (derived: item -> prescription -> visit)
CREATE OR REPLACE VIEW v_dispensing_visit AS
SELECT d.dispensing_id, d.medicine_id, pr.visit_id
  FROM dispensing d JOIN prescription_item pi ON pi.prescription_item_id = d.prescription_item_id JOIN prescription pr ON pr.prescription_id = pi.prescription_id
 WHERE d.source_type = 'clinic_prescription';

CREATE OR REPLACE VIEW v_dispensing_custody AS
SELECT d.dispensing_id, d.source_type, d.medicine_id,
       COALESCE((SELECT -SUM(sm.quantity_delta) FROM stock_movement sm WHERE sm.dispensing_id = d.dispensing_id AND sm.movement_type = 'dispensed'), 0) AS dispensed_qty,
       COALESCE((SELECT SUM(da.quantity_consumed) FROM dose_administration da JOIN order_item_supply s ON s.order_item_supply_id = da.order_item_supply_id
                  WHERE s.dispensing_id = d.dispensing_id AND NOT EXISTS (SELECT 1 FROM dose_administration n WHERE n.corrects_id = da.dose_administration_id)), 0) AS consumed_qty,
       COALESCE((SELECT SUM(lr.quantity) FROM leftover_return_request lr JOIN leftover_return_decision ld ON ld.leftover_return_request_id = lr.leftover_return_request_id
                  WHERE lr.dispensing_id = d.dispensing_id AND ld.decision = 'accepted'), 0) AS returned_qty,
       COALESCE((SELECT SUM(h.quantity) FROM custody_handoff h WHERE h.dispensing_id = d.dispensing_id AND h.handoff_purpose IN ('take_home', 'leftover_to_family')), 0) AS family_qty,
       EXISTS (SELECT 1 FROM order_item_supply s WHERE s.dispensing_id = d.dispensing_id) AS is_order_linked
  FROM dispensing d;

-- R6.2 (scope: order-linked dispensings): dispensed = administered + returned + handed to family. Any non-zero mismatch blocks discharge
CREATE OR REPLACE VIEW v_dispense_reconciliation AS
SELECT c.dispensing_id, v.visit_id, c.medicine_id, c.dispensed_qty, c.consumed_qty, c.returned_qty, c.family_qty,
       (c.dispensed_qty - c.consumed_qty - c.returned_qty - c.family_qty) AS mismatch_qty
  FROM v_dispensing_custody c LEFT JOIN v_dispensing_visit v ON v.dispensing_id = c.dispensing_id
 WHERE c.is_order_linked = 1;

-- custody handoffs nobody acknowledged yet
CREATE OR REPLACE VIEW v_unacknowledged_handoffs AS
SELECT h.custody_handoff_id, h.dispensing_id, h.giver_staff_id, h.recipient_staff_id, h.recipient_attendant_id, h.quantity, h.handoff_purpose, h.occurred_at,
       TIMESTAMPDIFF(HOUR, h.occurred_at, NOW(3)) AS hours_waiting
  FROM custody_handoff h WHERE NOT EXISTS (SELECT 1 FROM custody_handoff_acknowledgement a WHERE a.custody_handoff_id = h.custody_handoff_id);

-- family-facing medicine count: supplied / given / remaining per visit and medicine
CREATE OR REPLACE VIEW v_visit_medicine_count AS
SELECT v.visit_id, c.medicine_id, m.brand_name, SUM(c.dispensed_qty) AS supplied_qty, SUM(c.consumed_qty) AS given_qty,
       SUM(c.dispensed_qty - c.consumed_qty - c.returned_qty) AS remaining_qty
  FROM v_dispensing_custody c JOIN v_dispensing_visit v ON v.dispensing_id = c.dispensing_id JOIN medicine m ON m.medicine_id = c.medicine_id
 GROUP BY v.visit_id, c.medicine_id, m.brand_name;

CREATE OR REPLACE VIEW v_counter_sale_summary AS
SELECT s.counter_sale_id, s.sale_number, s.counter_customer_id, cc.phone AS customer_phone, cc.display_name AS customer_name,
       COALESCE((SELECT SUM(-sm.quantity_delta * sm.unit_price_snapshot) FROM stock_movement sm JOIN dispensing d ON d.dispensing_id = sm.dispensing_id
                  WHERE d.counter_sale_id = s.counter_sale_id AND sm.movement_type = 'dispensed'), 0)
       - COALESCE((SELECT SUM(sm.quantity_delta * sm.unit_price_snapshot) FROM counter_sale_return r JOIN stock_movement sm ON sm.stock_movement_id = r.returned_stock_movement_id
                    WHERE r.counter_sale_id = s.counter_sale_id), 0) AS total_amount,
       COALESCE((SELECT SUM(IF(p.corrects_id IS NULL, p.amount, -p.amount)) FROM counter_sale_payment p WHERE p.counter_sale_id = s.counter_sale_id), 0) AS paid_amount,
       COALESCE((SELECT SUM(f.amount) FROM counter_sale_refund f WHERE f.counter_sale_id = s.counter_sale_id), 0) AS refunded_amount,
       COALESCE((SELECT SUM(-sm.quantity_delta * sm.unit_price_snapshot) FROM stock_movement sm JOIN dispensing d ON d.dispensing_id = sm.dispensing_id
                  WHERE d.counter_sale_id = s.counter_sale_id AND sm.movement_type = 'dispensed'), 0)
       - COALESCE((SELECT SUM(sm.quantity_delta * sm.unit_price_snapshot) FROM counter_sale_return r JOIN stock_movement sm ON sm.stock_movement_id = r.returned_stock_movement_id
                    WHERE r.counter_sale_id = s.counter_sale_id), 0)
       - COALESCE((SELECT SUM(IF(p.corrects_id IS NULL, p.amount, -p.amount)) FROM counter_sale_payment p WHERE p.counter_sale_id = s.counter_sale_id), 0)
       + COALESCE((SELECT SUM(f.amount) FROM counter_sale_refund f WHERE f.counter_sale_id = s.counter_sale_id), 0) AS balance
  FROM counter_sale s JOIN counter_customer cc ON cc.counter_customer_id = s.counter_customer_id;
