-- =====================================================================================
-- P3-5  Billing, payments, refunds, discharge, dues   (Track B)
-- ER note (CR-B10): one bill per VISIT; counter sales have no bill (they use counter_sale_payment/refund, see 04).
-- Therefore the shared interfaces from TASKS.md were adapted: ensure_open_bill(visit_id, OUT bill_id)  [no counter-customer argument].
-- Money is derived (G3): balance = net lines - payments (+ reversals) + refunds, see v_bill_summary.
-- =====================================================================================
USE clinic_db;
DELIMITER $$

-- @api roles=* rules=R8.2 | Shared interface: the bill of a visit (created on first use, one per visit). Safe under concurrency (unique visit). Refuses to create a bill for a closed visit
CREATE PROCEDURE ensure_open_bill(IN p_visit_id BIGINT UNSIGNED, OUT p_bill_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_code VARCHAR(30);
  DECLARE EXIT HANDLER FOR 1062 BEGIN SELECT bill_id INTO p_bill_id FROM bill WHERE visit_id = p_visit_id; END;
  SELECT bill_id INTO p_bill_id FROM bill WHERE visit_id = p_visit_id;
  IF p_bill_id IS NULL THEN
    IF NOT EXISTS (SELECT 1 FROM visit WHERE visit_id = p_visit_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown visit'; END IF;
    IF visit_is_closed(p_visit_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already closed; no new entries are allowed'; END IF;
    CALL next_code('bill', v_code);
    INSERT INTO bill (visit_id, bill_number) VALUES (p_visit_id, v_code);
    SET p_bill_id = LAST_INSERT_ID();
  END IF;
END$$

-- @api roles=* rules=R8.2,R8.3 | internal: insert one bill line (line_no assigned under the bill lock). The trigger enforces finalization, sign and approval rules
CREATE PROCEDURE int_bill_add_line(IN p_bill BIGINT UNSIGNED, IN p_type VARCHAR(30), IN p_price_list BIGINT UNSIGNED, IN p_movement BIGINT UNSIGNED,
                                   IN p_lab_order BIGINT UNSIGNED, IN p_episode BIGINT UNSIGNED, IN p_desc VARCHAR(200), IN p_qty DECIMAL(10,2),
                                   IN p_unit_price DECIMAL(12,2), IN p_source VARCHAR(15), IN p_manual TINYINT, IN p_expected DECIMAL(12,2),
                                   IN p_deviation TINYINT, IN p_reason VARCHAR(300), IN p_reverses BIGINT UNSIGNED, IN p_approval BIGINT UNSIGNED,
                                   OUT p_line_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_lock BIGINT UNSIGNED; DECLARE v_no INT;
  SELECT bill_id INTO v_lock FROM bill WHERE bill_id = p_bill FOR UPDATE;
  IF v_lock IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown bill'; END IF;
  SELECT COALESCE(MAX(line_no), 0) + 1 INTO v_no FROM bill_line WHERE bill_id = p_bill;
  INSERT INTO bill_line (bill_id, line_no, line_type, price_list_id, source_stock_movement_id, lab_order_id, short_stay_episode_id, description_snapshot, quantity,
                         unit_price_snapshot, price_source, is_manual_price, expected_unit_price, is_price_deviation, reason, reverses_bill_line_id, approval_request_id)
  VALUES (p_bill, v_no, p_type, p_price_list, p_movement, p_lab_order, p_episode, p_desc, p_qty, p_unit_price, p_source, IFNULL(p_manual, 0), p_expected,
          IFNULL(p_deviation, 0), p_reason, p_reverses, p_approval);
  SET p_line_id = LAST_INSERT_ID();
END$$

-- @api roles=* rules=R8.2 | Shared interface (adapted, CR-B10): add a charge line. source_type: price_list_item (source_id = price_list_id), medicine (stock movement id), lab_test (lab order id), stay (short-stay episode id), manual (needs a reason)
CREATE PROCEDURE bill_add_line(IN p_bill_id BIGINT UNSIGNED, IN p_source_type VARCHAR(30), IN p_source_id BIGINT UNSIGNED, IN p_description VARCHAR(200),
                               IN p_quantity DECIMAL(10,2), IN p_unit_price DECIMAL(12,2), IN p_reason VARCHAR(200))
BEGIN
  DECLARE v_line BIGINT UNSIGNED;
  IF p_source_type NOT IN ('price_list_item', 'medicine', 'lab_test', 'stay', 'manual') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unsupported bill line source'; END IF;
  CALL int_bill_add_line(p_bill_id, p_source_type, IF(p_source_type = 'price_list_item', p_source_id, NULL), IF(p_source_type = 'medicine', p_source_id, NULL),
       IF(p_source_type = 'lab_test', p_source_id, NULL), IF(p_source_type = 'stay', p_source_id, NULL), p_description, p_quantity, p_unit_price,
       IF(p_source_type = 'price_list_item', 'price_list', IF(p_source_type = 'medicine', 'batch_mrp', IF(p_source_type = 'lab_test', 'lab_entered', 'manual'))),
       IF(p_source_type = 'manual', 1, 0), NULL, 0, p_reason, NULL, NULL, v_line);
END$$

-- @api roles=* rules=R6.3,R8.3 | Shared interface (adapted): credit line for a returned medicine. source_type must be return_credit and source_id the bill line being credited; amount is positive
CREATE PROCEDURE bill_add_credit_line(IN p_bill_id BIGINT UNSIGNED, IN p_source_type VARCHAR(30), IN p_source_id BIGINT UNSIGNED, IN p_description VARCHAR(200),
                                      IN p_amount DECIMAL(12,2), IN p_reason VARCHAR(200))
BEGIN
  DECLARE v_line BIGINT UNSIGNED;
  IF p_source_type <> 'return_credit' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Discounts and corrections need an approval: use sp_apply_discount / sp_apply_bill_correction'; END IF;
  CALL int_bill_add_line(p_bill_id, 'return_credit', NULL, NULL, NULL, NULL, p_description, 1, -ABS(p_amount), 'manual', 0, NULL, 0, p_reason, p_source_id, NULL, v_line);
END$$

-- @api roles=* rules=R8.1,R8.2 | internal: generate a fixed-price line from the price list (effective today) for an event such as visit start (CONSULT) or stay start (STAY_DAYCARE). No price entry -> a summary alert, no line
CREATE PROCEDURE int_auto_charge(IN p_visit BIGINT UNSIGNED, IN p_item_code VARCHAR(30), IN p_line_type VARCHAR(30), IN p_episode BIGINT UNSIGNED)
BEGIN
  DECLARE v_pl BIGINT UNSIGNED; DECLARE v_price DECIMAL(12,2); DECLARE v_desc VARCHAR(200); DECLARE v_bill BIGINT UNSIGNED; DECLARE v_line BIGINT UNSIGNED;
  SELECT price_list_id, price, description INTO v_pl, v_price, v_desc FROM price_list
   WHERE item_code = p_item_code AND active = 1 AND effective_from <= CURRENT_DATE AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
   ORDER BY effective_from DESC LIMIT 1;
  IF v_pl IS NULL THEN
    CALL raise_alert('NO_PRICE_ITEM', 'SUMMARY', 'visit', p_visit, CONCAT('No price-list entry for ', p_item_code, ': charge was not generated'));
  ELSE
    CALL ensure_open_bill(p_visit, v_bill);
    IF NOT EXISTS (SELECT 1 FROM bill_finalization WHERE bill_id = v_bill) THEN
      CALL int_bill_add_line(v_bill, p_line_type, v_pl, NULL, NULL, p_episode, v_desc, 1, v_price, 'price_list', 0, NULL, 0, NULL, NULL, NULL, v_line);
    END IF;
  END IF;
END$$

-- @api roles=OWNER rules=R8.1 | own txn. Set a price: closes the previous open price of the item the day before and adds the new effective-dated row (owner only, audited)
CREATE PROCEDURE sp_set_price(IN p_code VARCHAR(30), IN p_description VARCHAR(200), IN p_category VARCHAR(15), IN p_price DECIMAL(12,2), IN p_from DATE, OUT p_price_list_id BIGINT UNSIGNED)
BEGIN
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('OWNER');
  START TRANSACTION;
  UPDATE price_list SET effective_to = p_from - INTERVAL 1 DAY WHERE item_code = p_code AND effective_to IS NULL AND effective_from < p_from;
  INSERT INTO price_list (item_code, description, category, price, effective_from) VALUES (p_code, p_description, p_category, p_price, p_from);
  SET p_price_list_id = LAST_INSERT_ID();
  COMMIT;
END$$

-- @api roles=RECEPTIONIST,DOCTOR,NURSE rules=R8.1,R8.2 | own txn. Add a fixed-price item (procedure, injection, drip, ...) from the price list effective today
CREATE PROCEDURE sp_add_price_list_line(IN p_visit BIGINT UNSIGNED, IN p_item_code VARCHAR(30), IN p_qty DECIMAL(10,2), OUT p_line_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_pl BIGINT UNSIGNED; DECLARE v_price DECIMAL(12,2); DECLARE v_desc VARCHAR(200); DECLARE v_bill BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST,DOCTOR,NURSE');
  SELECT price_list_id, price, description INTO v_pl, v_price, v_desc FROM price_list
   WHERE item_code = p_item_code AND active = 1 AND effective_from <= CURRENT_DATE AND (effective_to IS NULL OR effective_to >= CURRENT_DATE) ORDER BY effective_from DESC LIMIT 1;
  IF v_pl IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No price-list entry for this item today; ask the owner'; END IF;
  START TRANSACTION;
  CALL ensure_open_bill(p_visit, v_bill);
  CALL int_bill_add_line(v_bill, 'price_list_item', v_pl, NULL, NULL, NULL, v_desc, p_qty, v_price, 'price_list', 0, NULL, 0, NULL, NULL, NULL, p_line_id);
  COMMIT;
END$$

-- @api roles=RECEPTIONIST,DOCTOR rules=R8.1 | own txn. Off-list price with a mandatory reason; appears in the manual-price report
CREATE PROCEDURE sp_add_manual_line(IN p_visit BIGINT UNSIGNED, IN p_description VARCHAR(200), IN p_qty DECIMAL(10,2), IN p_price DECIMAL(12,2), IN p_reason VARCHAR(300), OUT p_line_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_bill BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('RECEPTIONIST,DOCTOR');
  IF p_reason IS NULL OR CHAR_LENGTH(TRIM(p_reason)) < 3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A reason is required for an off-list price'; END IF;
  START TRANSACTION;
  CALL ensure_open_bill(p_visit, v_bill);
  CALL int_bill_add_line(v_bill, 'manual', NULL, NULL, NULL, NULL, p_description, p_qty, p_price, 'manual', 1, NULL, 0, p_reason, NULL, NULL, p_line_id);
  CALL raise_alert('MANUAL_PRICE', 'SUMMARY', 'bill_line', p_line_id, 'An off-list price was entered');
  COMMIT;
END$$

-- @api roles=DOCTOR,RECEPTIONIST,ACCOUNTANT rules=R8.4,G6 | Request a discount or free service (positive amount, reason mandatory). The owner (or a delegate within limit) decides; apply with sp_apply_discount
CREATE PROCEDURE sp_request_discount(IN p_bill BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_kind VARCHAR(12), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_visit BIGINT UNSIGNED;
  CALL int_require_role('DOCTOR,RECEPTIONIST,ACCOUNTANT');
  IF p_kind NOT IN ('discount', 'free_service') OR p_amount <= 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Kind must be discount or free_service and the amount positive'; END IF;
  SELECT visit_id INTO v_visit FROM bill WHERE bill_id = p_bill;
  IF v_visit IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown bill'; END IF;
  CALL request_approval_ex(p_kind, v_visit, p_bill, NULL, NULL, NULL, p_amount, NULL, NULL, p_reason, p_approval_id);
END$$

-- @api roles=DOCTOR,RECEPTIONIST,ACCOUNTANT,OWNER rules=R8.4,R8.3 | Apply an approved discount/free service as a separate negative line (bill must not be finalized yet; after that use a correction)
CREATE PROCEDURE sp_apply_discount(IN p_approval BIGINT UNSIGNED, OUT p_line_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_bill BIGINT UNSIGNED; DECLARE v_amt DECIMAL(12,2); DECLARE v_type VARCHAR(30); DECLARE v_reason VARCHAR(300); DECLARE v_net DECIMAL(14,2);
  CALL int_require_role('DOCTOR,RECEPTIONIST,ACCOUNTANT,OWNER');
  SELECT bill_id, requested_amount, request_type, reason INTO v_bill, v_amt, v_type, v_reason FROM approval_request WHERE approval_request_id = p_approval AND request_type IN ('discount', 'free_service');
  IF v_bill IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not a discount request'; END IF;
  SELECT net_total INTO v_net FROM v_bill_summary WHERE bill_id = v_bill;
  IF v_amt > v_net THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The discount is more than the current bill total'; END IF;
  CALL int_bill_add_line(v_bill, v_type, NULL, NULL, NULL, NULL, IF(v_type = 'discount', 'Discount', 'Free service'), 1, -v_amt, 'manual', 0, NULL, 0, v_reason, NULL, p_approval, p_line_id);
END$$

-- @api roles=RECEPTIONIST,ACCOUNTANT,DOCTOR rules=R8.3,G6 | Request a correction to a FINALIZED bill: positive amount = extra charge (debit), negative = credit
CREATE PROCEDURE sp_request_bill_correction(IN p_bill BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_visit BIGINT UNSIGNED;
  CALL int_require_role('RECEPTIONIST,ACCOUNTANT,DOCTOR');
  IF p_amount = 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Amount cannot be zero'; END IF;
  IF NOT EXISTS (SELECT 1 FROM bill_finalization WHERE bill_id = p_bill) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The bill is not finalized; edit it normally'; END IF;
  SELECT visit_id INTO v_visit FROM bill WHERE bill_id = p_bill;
  CALL request_approval_ex('post_finalization_bill_correction', v_visit, p_bill, NULL, NULL, NULL, p_amount, NULL, NULL, p_reason, p_approval_id);
END$$

-- @api roles=RECEPTIONIST,ACCOUNTANT,DOCTOR rules=R8.3,G6 | Apply an approved correction (credit or debit line)
CREATE PROCEDURE sp_apply_bill_correction(IN p_approval BIGINT UNSIGNED, OUT p_line_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_bill BIGINT UNSIGNED; DECLARE v_amt DECIMAL(12,2); DECLARE v_reason VARCHAR(300);
  CALL int_require_role('RECEPTIONIST,ACCOUNTANT,DOCTOR');
  SELECT bill_id, requested_amount, reason INTO v_bill, v_amt, v_reason FROM approval_request WHERE approval_request_id = p_approval AND request_type = 'post_finalization_bill_correction';
  IF v_bill IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not a bill-correction request'; END IF;
  CALL int_bill_add_line(v_bill, IF(v_amt < 0, 'post_finalization_credit', 'post_finalization_debit'), NULL, NULL, NULL, NULL,
       IF(v_amt < 0, 'Correction (credit)', 'Correction (debit)'), 1, v_amt, 'manual', 0, NULL, 0, v_reason, NULL, p_approval, p_line_id);
END$$

-- @api roles=RECEPTIONIST,ACCOUNTANT rules=R8.3 | Finalize the bill (Draft -> Finalized). After this only approved corrections can change it
CREATE PROCEDURE sp_finalize_bill(IN p_bill BIGINT UNSIGNED, IN p_reason VARCHAR(300))
BEGIN
  CALL int_require_role('RECEPTIONIST,ACCOUNTANT');
  IF NOT EXISTS (SELECT 1 FROM bill_line WHERE bill_id = p_bill) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The bill has no lines'; END IF;
  IF EXISTS (SELECT 1 FROM bill_finalization WHERE bill_id = p_bill) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The bill is already finalized'; END IF;
  INSERT INTO bill_finalization (bill_id, reason) VALUES (p_bill, p_reason);
END$$

-- @api roles=* rules=R8.5 | internal: primary phone of the visit's (surviving) patient, or NULL
CREATE FUNCTION visit_phone(p_visit BIGINT UNSIGNED) RETURNS VARCHAR(20) NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v VARCHAR(20);
  SELECT ph.phone_number INTO v FROM visit vi JOIN patient_phone ph ON ph.patient_id = canonical_patient(vi.patient_id)
   WHERE vi.visit_id = p_visit AND ph.valid_to IS NULL ORDER BY ph.is_primary DESC, ph.phone_sequence DESC LIMIT 1;
  RETURN v;
END$$

-- @api roles=ACCOUNTANT rules=R8.5 | own txn. Record a payment (cash/upi/card). Amount cannot exceed the balance. Auto receipt SMS (no patient details in the text)
CREATE PROCEDURE sp_record_payment(IN p_bill BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_method VARCHAR(5), OUT p_payment_id BIGINT UNSIGNED, OUT p_receipt VARCHAR(30))
BEGIN
  DECLARE v_bal DECIMAL(14,2); DECLARE v_visit BIGINT UNSIGNED; DECLARE v_lock BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('ACCOUNTANT');
  START TRANSACTION;
  SELECT bill_id, visit_id INTO v_lock, v_visit FROM bill WHERE bill_id = p_bill FOR UPDATE;
  IF v_lock IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown bill'; END IF;
  SELECT balance INTO v_bal FROM v_bill_summary WHERE bill_id = p_bill;
  IF NOT EXISTS (SELECT 1 FROM bill_line WHERE bill_id = p_bill) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The bill has no lines'; END IF;
  IF p_amount <= 0 OR p_amount > v_bal THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Payment must be more than zero and not more than the balance'; END IF;
  CALL next_code('receipt', p_receipt);
  INSERT INTO payment (bill_id, payment_kind, amount, method, receipt_number) VALUES (p_bill, 'payment', p_amount, p_method, p_receipt);
  SET p_payment_id = LAST_INSERT_ID();
  CALL queue_message(visit_phone(v_visit), 'receipt', CONCAT('Payment received at ', setting_text('clinic_name'), '. Receipt ', p_receipt), 'payment', p_payment_id);
  COMMIT;
END$$

-- @api roles=ACCOUNTANT rules=R8.5,G6 | Request reversal of a payment made by mistake (owner approval needed)
CREATE PROCEDURE sp_request_payment_reversal(IN p_payment BIGINT UNSIGNED, IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_bill BIGINT UNSIGNED; DECLARE v_amt DECIMAL(12,2); DECLARE v_visit BIGINT UNSIGNED; DECLARE v_kind VARCHAR(10);
  CALL int_require_role('ACCOUNTANT');
  SELECT p.bill_id, p.amount, p.payment_kind, b.visit_id INTO v_bill, v_amt, v_kind, v_visit FROM payment p JOIN bill b ON b.bill_id = p.bill_id WHERE p.payment_id = p_payment;
  IF v_bill IS NULL OR v_kind <> 'payment' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only a payment row can be reversed'; END IF;
  IF EXISTS (SELECT 1 FROM payment WHERE corrects_id = p_payment) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This payment was already reversed'; END IF;
  CALL request_approval_ex('payment_reversal', v_visit, v_bill, NULL, NULL, NULL, v_amt, NULL, CAST(p_payment AS CHAR), p_reason, p_approval_id);
END$$

-- @api roles=ACCOUNTANT rules=R8.5,G6 | Execute an approved payment reversal as a reversal row (the original stays)
CREATE PROCEDURE sp_reverse_payment(IN p_approval BIGINT UNSIGNED, OUT p_receipt VARCHAR(30))
BEGIN
  DECLARE v_pay BIGINT UNSIGNED; DECLARE v_bill BIGINT UNSIGNED; DECLARE v_amt DECIMAL(12,2); DECLARE v_method VARCHAR(5);
  CALL int_require_role('ACCOUNTANT');
  SELECT CAST(proposed_change AS UNSIGNED) INTO v_pay FROM approval_request WHERE approval_request_id = p_approval AND request_type = 'payment_reversal';
  IF v_pay IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not a payment-reversal request'; END IF;
  SELECT bill_id, amount, method INTO v_bill, v_amt, v_method FROM payment WHERE payment_id = v_pay;
  CALL next_code('receipt', p_receipt);
  INSERT INTO payment (bill_id, payment_kind, amount, method, receipt_number, approval_request_id, corrects_id) VALUES (v_bill, 'reversal', v_amt, v_method, p_receipt, p_approval, v_pay);
END$$

-- @api roles=ACCOUNTANT,RECEPTIONIST rules=R8.5,G6 | Request a refund (only an overpaid amount can be refunded). Owner approval needed
CREATE PROCEDURE sp_request_refund(IN p_bill BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_bal DECIMAL(14,2); DECLARE v_visit BIGINT UNSIGNED;
  CALL int_require_role('ACCOUNTANT,RECEPTIONIST');
  SELECT balance, visit_id INTO v_bal, v_visit FROM v_bill_summary WHERE bill_id = p_bill;
  IF v_bal IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown bill'; END IF;
  IF p_amount <= 0 OR p_amount > -v_bal THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Refund is more than the overpaid amount'; END IF;
  CALL request_approval_ex('refund', v_visit, p_bill, NULL, NULL, NULL, p_amount, NULL, NULL, p_reason, p_approval_id);
END$$

-- @api roles=ACCOUNTANT rules=R8.5,G6 | Pay out an approved refund (amount = approved amount; receipt generated)
CREATE PROCEDURE sp_execute_refund(IN p_approval BIGINT UNSIGNED, IN p_method VARCHAR(5), OUT p_receipt VARCHAR(30))
BEGIN
  DECLARE v_bill BIGINT UNSIGNED; DECLARE v_amt DECIMAL(12,2); DECLARE v_bal DECIMAL(14,2);
  CALL int_require_role('ACCOUNTANT');
  SELECT bill_id, requested_amount INTO v_bill, v_amt FROM approval_request WHERE approval_request_id = p_approval AND request_type = 'refund';
  IF v_bill IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not a refund request'; END IF;
  SELECT balance INTO v_bal FROM v_bill_summary WHERE bill_id = v_bill;
  IF v_amt > -v_bal THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Refund is more than the overpaid amount'; END IF;
  CALL next_code('receipt', p_receipt);
  INSERT INTO refund (bill_id, amount, method, receipt_number, approval_request_id) VALUES (v_bill, v_amt, p_method, p_receipt, p_approval);
END$$

-- ------------------------------------------------------------------ discharge
-- @api roles=RECEPTIONIST,ACCOUNTANT,DOCTOR rules=R9.1,G6 | Ask the owner (or delegate) to allow discharge with a balance. Stores the promised-by date in the request
CREATE PROCEDURE sp_request_partial_discharge(IN p_visit BIGINT UNSIGNED, IN p_promised_by DATE, IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_bill BIGINT UNSIGNED; DECLARE v_bal DECIMAL(14,2);
  CALL int_require_role('RECEPTIONIST,ACCOUNTANT,DOCTOR');
  IF p_promised_by IS NULL OR p_promised_by < CURRENT_DATE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A promised-by date (today or later) is required'; END IF;
  SELECT bill_id, balance INTO v_bill, v_bal FROM v_bill_summary WHERE visit_id = p_visit;
  IF v_bill IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The visit has no bill'; END IF;
  IF v_bal <= 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Nothing is due; no approval is needed'; END IF;
  CALL request_approval_ex('partial_payment_discharge', p_visit, v_bill, NULL, NULL, NULL, v_bal, NULL, DATE_FORMAT(p_promised_by, '%Y-%m-%d'), p_reason, p_approval_id);
END$$

-- @api roles=DOCTOR,RECEPTIONIST,ACCOUNTANT rules=R9.1,R9.2,R9.4,R9.5 | own txn. Discharge in ONE transaction: checks sign-off, doses reconciled (R6.2) and reports reviewed; money gate = balance cleared, or an approved partial-payment request, or a doctor's logged emergency override (money step only). Then: status discharged, stay closed, bill finalized, discharge row, visit closure, due promise, appointment completed
CREATE PROCEDURE sp_discharge_visit(IN p_visit BIGINT UNSIGNED, IN p_emergency TINYINT, IN p_justification VARCHAR(500), OUT p_discharge_id BIGINT UNSIGNED)
BEGIN
  DECLARE v_lock BIGINT UNSIGNED; DECLARE v_sign BIGINT UNSIGNED; DECLARE v_bill BIGINT UNSIGNED; DECLARE v_bal DECIMAL(14,2); DECLARE v_appr BIGINT UNSIGNED; DECLARE v_basis VARCHAR(30);
  DECLARE v_ovr BIGINT UNSIGNED; DECLARE v_prom VARCHAR(500); DECLARE v_ep BIGINT UNSIGNED; DECLARE v_appt BIGINT UNSIGNED; DECLARE v_ast VARCHAR(20); DECLARE v_due DATE;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  CALL int_require_role('DOCTOR,RECEPTIONIST,ACCOUNTANT');
  IF IFNULL(p_emergency, 0) = 1 THEN
    IF app_role() <> 'DOCTOR' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only a doctor can use the clinical emergency override'; END IF;
    IF p_justification IS NULL OR CHAR_LENGTH(TRIM(p_justification)) < 10 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Describe the clinical emergency (at least 10 characters)'; END IF;
  END IF;
  START TRANSACTION;
  SELECT visit_id, appointment_id INTO v_lock, v_appt FROM visit WHERE visit_id = p_visit FOR UPDATE;
  IF v_lock IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown visit'; END IF;
  IF visit_is_closed(p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This visit is already discharged'; END IF;
  SELECT MAX(clinical_signoff_id) INTO v_sign FROM clinical_signoff WHERE visit_id = p_visit;
  IF v_sign IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Clinical sign-off by the doctor is missing'; END IF;
  IF EXISTS (SELECT 1 FROM v_dispense_reconciliation WHERE visit_id = p_visit AND mismatch_qty <> 0) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Medicine does not reconcile: dispensed is not equal to given + returned + handed over'; END IF;
  IF EXISTS (SELECT 1 FROM v_report_unreviewed WHERE visit_id = p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A lab report is waiting for the doctor review'; END IF;
  CALL ensure_open_bill(p_visit, v_bill);
  IF NOT EXISTS (SELECT 1 FROM bill_finalization WHERE bill_id = v_bill) THEN INSERT INTO bill_finalization (bill_id, reason) VALUES (v_bill, 'Finalized at discharge'); END IF;
  SELECT balance INTO v_bal FROM v_bill_summary WHERE bill_id = v_bill;
  IF v_bal <= 0 THEN SET v_basis = 'bill_settled';
  ELSE
    SELECT ar.approval_request_id, ar.proposed_change INTO v_appr, v_prom FROM approval_request ar
     WHERE ar.visit_id = p_visit AND ar.request_type = 'partial_payment_discharge' AND approval_is_valid(ar.approval_request_id, 'partial_payment_discharge') = 1
       AND NOT EXISTS (SELECT 1 FROM discharge d WHERE d.approval_request_id = ar.approval_request_id) ORDER BY ar.approval_request_id DESC LIMIT 1;
    IF v_appr IS NOT NULL THEN SET v_basis = 'approved_partial_payment';
    ELSEIF IFNULL(p_emergency, 0) = 1 THEN SET v_basis = 'emergency_override';
    ELSE SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Bill is not settled: pay it or get a partial-payment approval';
    END IF;
  END IF;
  IF v_basis = 'emergency_override' THEN
    INSERT INTO clinical_emergency_override (visit_id, override_type, authorized_by_staff_id, clinical_justification) VALUES (p_visit, 'financial_gate_bypass', app_user(), p_justification);
    SET v_ovr = LAST_INSERT_ID();
  END IF;
  CALL int_add_visit_status(p_visit, 'discharged', NULL, 'Discharged');
  SELECT short_stay_episode_id INTO v_ep FROM short_stay_episode e WHERE e.visit_id = p_visit AND NOT EXISTS (SELECT 1 FROM short_stay_closure c WHERE c.short_stay_episode_id = e.short_stay_episode_id);
  IF v_ep IS NOT NULL THEN INSERT INTO short_stay_closure (short_stay_episode_id, reason) VALUES (v_ep, 'Closed at discharge'); END IF;
  INSERT INTO discharge (visit_id, clinical_signoff_id, financial_gate_basis, approval_request_id, clinical_emergency_override_id) VALUES (p_visit, v_sign, v_basis, v_appr, v_ovr);
  SET p_discharge_id = LAST_INSERT_ID();
  INSERT INTO visit_closure (visit_id, closure_kind) VALUES (p_visit, 'discharged');
  IF v_bal > 0 THEN
    SET v_due = IF(v_prom IS NOT NULL, STR_TO_DATE(v_prom, '%Y-%m-%d'), CURRENT_DATE + INTERVAL 7 DAY);
    INSERT INTO due_promise (visit_id, bill_id, promised_by_date, approval_request_id, reason) VALUES (p_visit, v_bill, v_due, v_appr, IF(v_basis = 'emergency_override', 'Emergency override: default 7 days', 'Approved partial payment'));
  END IF;
  IF v_appt IS NOT NULL THEN
    SELECT status INTO v_ast FROM appointment_status_history WHERE appointment_id = v_appt ORDER BY status_seq DESC LIMIT 1;
    IF v_ast IN ('checked_in', 'waiting') THEN CALL int_add_appt_status(v_appt, 'in_consultation', NULL); SET v_ast = 'in_consultation'; END IF;
    IF v_ast = 'in_consultation' THEN CALL int_add_appt_status(v_appt, 'completed', 'Visit discharged'); END IF;
  END IF;
  IF v_basis = 'emergency_override' THEN CALL raise_alert('EMERGENCY_OVERRIDE', 'INTERRUPT', 'visit', p_visit, 'A doctor discharged a patient with the clinical emergency override'); END IF;
  COMMIT;
END$$

-- @api roles=DOCTOR,RECEPTIONIST,ACCOUNTANT rules=R9.4,G6 | Ask the owner to reopen a discharged visit
CREATE PROCEDURE sp_request_visit_reopen(IN p_visit BIGINT UNSIGNED, IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED)
BEGIN
  CALL int_require_role('DOCTOR,RECEPTIONIST,ACCOUNTANT');
  IF NOT visit_is_closed(p_visit) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The visit is not closed'; END IF;
  CALL request_approval_ex('visit_reopen', p_visit, NULL, NULL, NULL, NULL, NULL, NULL, NULL, p_reason, p_approval_id);
END$$

-- @api roles=DOCTOR,RECEPTIONIST,ACCOUNTANT,OWNER rules=R9.4,G6 | Reopen after approval: a reversal row on the closure (the original closure stays)
CREATE PROCEDURE sp_reopen_visit(IN p_approval BIGINT UNSIGNED)
BEGIN
  DECLARE v_visit BIGINT UNSIGNED; DECLARE v_clo BIGINT UNSIGNED;
  CALL int_require_role('DOCTOR,RECEPTIONIST,ACCOUNTANT,OWNER');
  SELECT visit_id INTO v_visit FROM approval_request WHERE approval_request_id = p_approval AND request_type = 'visit_reopen';
  IF v_visit IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not a visit-reopen request'; END IF;
  SELECT c.visit_closure_id INTO v_clo FROM visit_closure c WHERE c.visit_id = v_visit AND c.reverses_visit_closure_id IS NULL
     AND NOT EXISTS (SELECT 1 FROM visit_closure r WHERE r.reverses_visit_closure_id = c.visit_closure_id) ORDER BY c.visit_closure_id DESC LIMIT 1;
  IF v_clo IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The visit is not closed'; END IF;
  INSERT INTO visit_closure (visit_id, closure_kind, reverses_visit_closure_id, approval_request_id, reason) VALUES (v_visit, 'discharged', v_clo, p_approval, 'Reopened with approval');
END$$

-- @api roles=* rules=R9.3 | 1 if the patient (or the surviving record) has an unpaid promised due
CREATE FUNCTION patient_has_dues(p_patient BIGINT UNSIGNED) RETURNS TINYINT NOT DETERMINISTIC READS SQL DATA
BEGIN
  DECLARE v INT DEFAULT 0;
  SELECT COUNT(*) INTO v FROM v_due_list WHERE canonical_patient_id = canonical_patient(p_patient);
  RETURN v > 0;
END$$

-- @api roles=ACCOUNTANT,OWNER rules=R9.3,G10 | Queue (simulated) reminders for dues that reached their promised date; at most one per due per day. Text carries no patient details
CREATE PROCEDURE sp_send_due_reminders()
BEGIN
  CALL int_require_role('ACCOUNTANT,OWNER');
  INSERT INTO message_outbox (message_type, recipient_phone, safe_template_key, rendered_text, due_promise_id)
  SELECT 'due_reminder', d.phone, 'due_reminder_v1', CONCAT('A payment is due at ', setting_text('clinic_name'), '. Please contact the clinic.'), d.due_promise_id
    FROM v_due_list d WHERE d.days_overdue >= 0 AND d.phone IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM message_outbox m WHERE m.due_promise_id = d.due_promise_id AND DATE(m.occurred_at) = CURRENT_DATE);
  INSERT INTO message_delivery_event (message_outbox_id, delivery_state, details)
  SELECT m.message_outbox_id, 'simulated_sent', 'Simulated: not sent to a real network' FROM message_outbox m
   WHERE m.message_type = 'due_reminder' AND NOT EXISTS (SELECT 1 FROM message_delivery_event e WHERE e.message_outbox_id = m.message_outbox_id);
END$$

DELIMITER ;

-- ------------------------------------------------------------------ views
CREATE OR REPLACE VIEW v_bill_summary AS
SELECT t.bill_id, t.bill_number, t.visit_id, t.net_total, t.paid_total, t.refunded_total, t.is_finalized,
       (t.net_total - t.paid_total + t.refunded_total) AS balance,
       CASE WHEN t.is_finalized = 0 THEN 'draft' WHEN (t.net_total - t.paid_total + t.refunded_total) <= 0 THEN 'settled' ELSE 'finalized' END AS state
  FROM (SELECT b.bill_id, b.bill_number, b.visit_id,
               COALESCE((SELECT SUM(l.line_amount) FROM bill_line l WHERE l.bill_id = b.bill_id), 0) AS net_total,
               COALESCE((SELECT SUM(IF(p.payment_kind = 'payment', p.amount, -p.amount)) FROM payment p WHERE p.bill_id = b.bill_id), 0) AS paid_total,
               COALESCE((SELECT SUM(r.amount) FROM refund r WHERE r.bill_id = b.bill_id), 0) AS refunded_total,
               EXISTS (SELECT 1 FROM bill_finalization f WHERE f.bill_id = b.bill_id) AS is_finalized
          FROM bill b) t;

CREATE OR REPLACE VIEW v_bill_lines AS
SELECT l.bill_id, l.line_no, l.line_type, l.description_snapshot, l.quantity, l.unit_price_snapshot, l.line_amount, l.is_manual_price, l.is_price_deviation, l.reason
  FROM bill_line l;

CREATE OR REPLACE VIEW v_discharge_checklist AS
SELECT x.*, (x.clinical_signoff_done AND x.doses_reconciled AND x.lab_reports_reviewed AND (x.balance_clear OR x.partial_approved)) AS ready
  FROM (SELECT v.visit_id,
               EXISTS (SELECT 1 FROM clinical_signoff s WHERE s.visit_id = v.visit_id) AS clinical_signoff_done,
               NOT EXISTS (SELECT 1 FROM v_dispense_reconciliation r WHERE r.visit_id = v.visit_id AND r.mismatch_qty <> 0) AS doses_reconciled,
               NOT EXISTS (SELECT 1 FROM v_report_unreviewed u WHERE u.visit_id = v.visit_id) AS lab_reports_reviewed,
               COALESCE(bs.is_finalized, 0) AS bill_finalized,
               COALESCE(bs.balance, 0) <= 0 AS balance_clear,
               EXISTS (SELECT 1 FROM approval_request ar WHERE ar.visit_id = v.visit_id AND ar.request_type = 'partial_payment_discharge'
                        AND approval_is_valid(ar.approval_request_id, 'partial_payment_discharge') = 1
                        AND NOT EXISTS (SELECT 1 FROM discharge d WHERE d.approval_request_id = ar.approval_request_id)) AS partial_approved,
               COALESCE(bs.balance, 0) AS balance
          FROM visit v LEFT JOIN v_bill_summary bs ON bs.visit_id = v.visit_id WHERE visit_is_closed(v.visit_id) = 0) x;

CREATE OR REPLACE VIEW v_due_list AS
SELECT dp.due_promise_id, dp.visit_id, dp.bill_id, dp.promised_by_date, bs.balance, DATEDIFF(CURRENT_DATE, dp.promised_by_date) AS days_overdue,
       canonical_patient(v.patient_id) AS canonical_patient_id, p.display_code, CONCAT_WS(' ', p.given_name, p.family_name) AS patient_name, visit_phone(dp.visit_id) AS phone
  FROM due_promise dp JOIN v_bill_summary bs ON bs.bill_id = dp.bill_id JOIN visit v ON v.visit_id = dp.visit_id JOIN patient p ON p.patient_id = v.patient_id
 WHERE dp.due_promise_id = (SELECT MAX(x.due_promise_id) FROM due_promise x WHERE x.visit_id = dp.visit_id) AND bs.balance > 0;

CREATE OR REPLACE VIEW v_daily_collection AS
SELECT DATE(p.recorded_at) AS collection_day, 'bill' AS source, p.method, SUM(IF(p.payment_kind = 'payment', p.amount, -p.amount)) AS collected
  FROM payment p GROUP BY DATE(p.recorded_at), p.method
UNION ALL
SELECT DATE(c.recorded_at), 'counter_sale', c.method, SUM(IF(c.corrects_id IS NULL, c.amount, -c.amount)) FROM counter_sale_payment c GROUP BY DATE(c.recorded_at), c.method;

-- approvals waiting for a decision (owner/delegate inbox)
CREATE OR REPLACE VIEW v_approval_inbox AS
SELECT ar.approval_request_id, ar.request_type, ar.visit_id, ar.bill_id, ar.counter_sale_id, ar.stock_batch_id, ar.short_stay_episode_id, ar.requested_amount,
       ar.requested_quantity, ar.reason, ar.recorded_by AS requested_by, ar.recorded_at AS requested_at,
       (SELECT d.decision FROM approval_decision d WHERE d.approval_request_id = ar.approval_request_id) AS decision
  FROM approval_request ar;
