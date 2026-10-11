-- showcase.sql  (P4-4)  Module 3 proof: 53 clinic queries, each labelled with its syllabus topic.
-- Run:  docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db --force -t' < db/queries/showcase.sql 2>&1 | grep -B2 ERROR
--       (--force continues after an error, so ONE run lists every wrong column name at once)
-- Needs the demo seed (python tools/seed_demo.py). Written against the column names of docs/design/01_er_model.md.
-- "Derived, never stored" (G3): current status, stock, balance are computed here from the append-only rows, not read from columns.
-- Own views start with vs_ so they never clash with the views of the logic files.
-- Needs MySQL 8.4 (INTERSECT/EXCEPT, window functions, recursive CTE).
USE clinic_db;

-- ============================ A. Selection, projection, joins ============================

-- [S01] Selection, projection, ORDER BY, LIMIT | youngest registered patients
SELECT patient_id, display_code, given_name, family_name, date_of_birth
FROM patient
WHERE registration_kind = 'registered' AND date_of_birth IS NOT NULL
ORDER BY date_of_birth DESC
LIMIT 5;

-- [S02] INNER JOIN | every visit with patient name and attending doctor
SELECT v.visit_id, v.occurred_at, p.display_code, CONCAT(p.given_name, ' ', p.family_name) AS patient,
       CONCAT(d.given_name, ' ', d.family_name) AS doctor
FROM visit v
JOIN patient p ON p.patient_id = v.patient_id
JOIN visit_doctor_assignment a ON a.visit_id = v.visit_id AND a.assignment_role = 'attending'
JOIN staff d ON d.staff_id = a.doctor_staff_id
ORDER BY v.occurred_at DESC
LIMIT 10;

-- [S03] LEFT OUTER JOIN + IS NULL (anti-join) | registered patients who never had a visit
SELECT p.patient_id, p.display_code, p.family_name
FROM patient p
LEFT JOIN visit v ON v.patient_id = p.patient_id
WHERE v.visit_id IS NULL;

-- [S04] RIGHT OUTER JOIN | all visits; appointment columns are NULL for walk-ins
SELECT v.visit_id, v.entry_type, a.appointment_id, a.booking_channel
FROM appointment a
RIGHT JOIN visit v ON v.appointment_id = a.appointment_id
ORDER BY v.visit_id
LIMIT 10;

-- [S05] FULL OUTER JOIN (emulated: LEFT UNION RIGHT) | appointments without visit AND visits without appointment
SELECT a.appointment_id, v.visit_id
FROM appointment a LEFT JOIN visit v ON v.appointment_id = a.appointment_id
UNION
SELECT a.appointment_id, v.visit_id
FROM appointment a RIGHT JOIN visit v ON v.appointment_id = a.appointment_id;

-- [S06] CROSS JOIN | complete doctor x entry-type grid, with 0 where nothing happened
SELECT d.family_name AS doctor, t.entry_type, COUNT(v.visit_id) AS visits
FROM (SELECT s.staff_id, s.family_name FROM staff s JOIN doctor_detail dd ON dd.staff_id = s.staff_id) d
CROSS JOIN (SELECT 'appointment' AS entry_type UNION ALL SELECT 'walk_in' UNION ALL SELECT 'emergency') t
LEFT JOIN visit_doctor_assignment a ON a.doctor_staff_id = d.staff_id AND a.assignment_role = 'attending'
LEFT JOIN visit v ON v.visit_id = a.visit_id AND v.entry_type = t.entry_type
GROUP BY d.staff_id, d.family_name, t.entry_type
ORDER BY d.family_name, t.entry_type;

-- [S07] SELF JOIN | patients sharing one phone number (family or duplicate registrations)
SELECT a.phone_number, p1.display_code AS patient_1, p2.display_code AS patient_2
FROM patient_phone a
JOIN patient_phone b ON b.phone_number = a.phone_number AND b.patient_id > a.patient_id
JOIN patient p1 ON p1.patient_id = a.patient_id
JOIN patient p2 ON p2.patient_id = b.patient_id;

-- [S08] Multi-table join chain | which medicine was billed on which bill line (bill -> line -> ledger -> batch -> medicine)
SELECT b.bill_number, bl.line_no, m.brand_name, sb.batch_code, bl.quantity, bl.unit_price_snapshot,
       bl.quantity * bl.unit_price_snapshot AS line_amount
FROM bill b
JOIN bill_line bl ON bl.bill_id = b.bill_id AND bl.line_type = 'medicine'
JOIN stock_movement sm ON sm.stock_movement_id = bl.source_stock_movement_id
JOIN stock_batch sb ON sb.stock_batch_id = sm.stock_batch_id
JOIN medicine m ON m.medicine_id = sb.medicine_id
ORDER BY b.bill_id, bl.line_no
LIMIT 10;

-- [S09] JOIN ... USING | patient with primary phone
SELECT p.display_code, p.family_name, ph.phone_number
FROM patient p JOIN patient_phone ph USING (patient_id)
WHERE ph.is_primary = 1
LIMIT 5;

-- ============================ B. Aggregates, GROUP BY, HAVING ============================

-- [S10] Aggregate functions COUNT/SUM/AVG/MIN/MAX | what do bills look like?
SELECT COUNT(DISTINCT bill_id) AS bills, SUM(quantity * unit_price_snapshot) AS billed_total,
       AVG(quantity * unit_price_snapshot) AS avg_line, MIN(unit_price_snapshot) AS cheapest, MAX(unit_price_snapshot) AS dearest
FROM bill_line;

-- [S11] GROUP BY | visits per attending doctor
SELECT s.family_name AS doctor, COUNT(*) AS visits
FROM visit_doctor_assignment a JOIN staff s ON s.staff_id = a.doctor_staff_id
WHERE a.assignment_role = 'attending'
GROUP BY s.staff_id, s.family_name
ORDER BY visits DESC;

-- [S12] GROUP BY ... HAVING | patients who came more than once
SELECT p.display_code, p.family_name, COUNT(*) AS visits
FROM visit v JOIN patient p ON p.patient_id = v.patient_id
GROUP BY p.patient_id, p.display_code, p.family_name
HAVING COUNT(*) > 1
ORDER BY visits DESC;

-- [S13] GROUP BY ... WITH ROLLUP | collections per payment method with grand total
SELECT COALESCE(method, 'ALL METHODS') AS method, COUNT(*) AS payments, SUM(amount) AS total
FROM payment
WHERE payment_kind = 'payment'
GROUP BY method WITH ROLLUP;

-- [S14] HAVING with a subquery | doctors busier than the average doctor
SELECT a.doctor_staff_id, COUNT(*) AS visits
FROM visit_doctor_assignment a
GROUP BY a.doctor_staff_id
HAVING COUNT(*) > (SELECT COUNT(*) / COUNT(DISTINCT doctor_staff_id) FROM visit_doctor_assignment);

-- [S15] GROUP_CONCAT + GROUP BY | allergies of each patient on one line
SELECT p.display_code, GROUP_CONCAT(COALESCE(i.ingredient_name, al.allergen_text) SEPARATOR ', ') AS allergies
FROM patient p
JOIN patient_allergy al ON al.patient_id = p.patient_id
LEFT JOIN ingredient i ON i.ingredient_id = al.ingredient_id
GROUP BY p.patient_id, p.display_code;

-- ============================ C. Subqueries, EXISTS, ANY/ALL ============================

-- [S16] EXISTS | patients with at least one recorded allergy
SELECT p.display_code, p.family_name
FROM patient p
WHERE EXISTS (SELECT 1 FROM patient_allergy a WHERE a.patient_id = p.patient_id);

-- [S17] NOT EXISTS | batches that were never dispensed
SELECT sb.stock_batch_id, sb.batch_code, sb.expiry_date
FROM stock_batch sb
WHERE NOT EXISTS (SELECT 1 FROM stock_movement sm WHERE sm.stock_batch_id = sb.stock_batch_id AND sm.movement_type = 'dispensed');

-- [S18] IN (nested subquery) | visits of patients allergic to Amoxicillin
SELECT v.visit_id, v.patient_id, v.occurred_at
FROM visit v
WHERE v.patient_id IN (SELECT a.patient_id FROM patient_allergy a JOIN ingredient i ON i.ingredient_id = a.ingredient_id
                       WHERE i.ingredient_name = 'Amoxicillin');

-- [S19] ALL | batches priced above EVERY batch of the cheapest-listed medicine (Parafix 500)
SELECT sb.stock_batch_id, sb.mrp
FROM stock_batch sb
WHERE sb.mrp > ALL (SELECT b2.mrp FROM stock_batch b2 JOIN medicine m ON m.medicine_id = b2.medicine_id WHERE m.brand_name = 'Parafix 500')
ORDER BY sb.mrp
LIMIT 5;

-- [S20] ANY | batches whose MRP equals ANY MRP of an injection
SELECT sb.stock_batch_id, sb.mrp
FROM stock_batch sb
WHERE sb.mrp = ANY (SELECT b2.mrp FROM stock_batch b2 JOIN medicine m ON m.medicine_id = b2.medicine_id WHERE m.dosage_form = 'injection');

-- [S21] Correlated subquery | CURRENT status of every visit = latest row of the status trail (G3: status is derived)
SELECT v.visit_id, (SELECT h.status FROM visit_status_history h WHERE h.visit_id = v.visit_id
                    ORDER BY h.status_seq DESC LIMIT 1) AS current_status
FROM visit v
ORDER BY v.visit_id DESC
LIMIT 10;

-- [S22] Scalar subquery | each patient's age against the clinic average
SELECT display_code, TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()) AS age,
       (SELECT AVG(TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE())) FROM patient WHERE date_of_birth IS NOT NULL) AS avg_age
FROM patient
WHERE date_of_birth IS NOT NULL
LIMIT 5;

-- [S23] Derived table (subquery in FROM) | stock on hand per batch, then only batches running low
SELECT t.stock_batch_id, t.on_hand
FROM (SELECT stock_batch_id, SUM(quantity_delta) AS on_hand FROM stock_movement GROUP BY stock_batch_id) t
WHERE t.on_hand BETWEEN 1 AND 20
ORDER BY t.on_hand;

-- ============================ D. Set operations and division ============================

-- [S24] UNION | every phone number the clinic knows (patients and attendants)
SELECT phone_number AS phone, 'patient' AS owner_kind FROM patient_phone
UNION
SELECT phone, 'attendant' FROM patient_attendant WHERE phone IS NOT NULL;

-- [S25] INTERSECT | patients who had BOTH a lab order and a short stay
SELECT v.patient_id FROM visit v JOIN lab_order lo ON lo.visit_id = v.visit_id
INTERSECT
SELECT v.patient_id FROM visit v JOIN short_stay_episode e ON e.visit_id = v.visit_id;

-- [S26] EXCEPT | patients who visited but NEVER paid anything (reconciliation: visit vs money)
SELECT v.patient_id FROM visit v
EXCEPT
SELECT v.patient_id FROM visit v JOIN bill b ON b.visit_id = v.visit_id JOIN payment p ON p.bill_id = b.bill_id;

-- [S27] DIVISION (double NOT EXISTS) | patients who have been seen by EVERY doctor
SELECT p.display_code, p.family_name
FROM patient p
WHERE NOT EXISTS (
    SELECT 1 FROM doctor_detail dd
    WHERE NOT EXISTS (
        SELECT 1 FROM visit v JOIN visit_doctor_assignment a ON a.visit_id = v.visit_id
        WHERE v.patient_id = p.patient_id AND a.doctor_staff_id = dd.staff_id));

-- [S28] DIVISION (GROUP BY / HAVING COUNT form) | suppliers who supplied batches of EVERY dosage form in the catalog
SELECT s.name
FROM supplier s
JOIN stock_batch sb ON sb.supplier_id = s.supplier_id
JOIN medicine m ON m.medicine_id = sb.medicine_id
GROUP BY s.supplier_id, s.name
HAVING COUNT(DISTINCT m.dosage_form) = (SELECT COUNT(DISTINCT dosage_form) FROM medicine);

-- ============================ E. Views, CTEs, window functions ============================

-- [S29] VIEW | reusable derived stock (G3): on hand per batch, with expiry state
CREATE OR REPLACE VIEW vs_stock_on_hand AS
SELECT sb.stock_batch_id, sb.medicine_id, sb.batch_code, sb.expiry_date, COALESCE(SUM(sm.quantity_delta), 0) AS on_hand,
       CASE WHEN sb.expiry_date < CURDATE() THEN 'expired'
            WHEN sb.expiry_date <= CURDATE() + INTERVAL 60 DAY THEN 'expiring'
            ELSE 'ok' END AS expiry_state
FROM stock_batch sb LEFT JOIN stock_movement sm ON sm.stock_batch_id = sb.stock_batch_id
GROUP BY sb.stock_batch_id, sb.medicine_id, sb.batch_code, sb.expiry_date;

-- [S30] Query on a view + R4.2 colour label | green = in stock, blue = in catalog but out of stock
SELECT m.brand_name,
       COALESCE(SUM(CASE WHEN v.expiry_state <> 'expired' THEN v.on_hand END), 0) AS sellable,
       CASE WHEN COALESCE(SUM(CASE WHEN v.expiry_state <> 'expired' THEN v.on_hand END), 0) > 0 THEN 'green' ELSE 'blue' END AS label
FROM medicine m LEFT JOIN vs_stock_on_hand v ON v.medicine_id = m.medicine_id
GROUP BY m.medicine_id, m.brand_name
ORDER BY label, m.brand_name;

-- [S31] Window RANK | doctors ranked by number of visits
SELECT doctor, visits, RANK() OVER (ORDER BY visits DESC) AS rnk
FROM (SELECT s.family_name AS doctor, COUNT(*) AS visits
      FROM visit_doctor_assignment a JOIN staff s ON s.staff_id = a.doctor_staff_id GROUP BY s.staff_id, s.family_name) t;

-- [S32] Window SUM() OVER (running total) | daily collections and cumulative total
SELECT day, collected, SUM(collected) OVER (ORDER BY day) AS running_total
FROM (SELECT DATE(occurred_at) AS day, SUM(amount) AS collected FROM payment WHERE payment_kind = 'payment' GROUP BY DATE(occurred_at)) d
ORDER BY day;

-- [S33] Window LAG | days between two consecutive visits of the same patient
SELECT patient_id, visit_id, occurred_at,
       DATEDIFF(occurred_at, LAG(occurred_at) OVER (PARTITION BY patient_id ORDER BY occurred_at)) AS days_since_previous
FROM visit
ORDER BY patient_id, occurred_at
LIMIT 15;

-- [S34] Window ROW_NUMBER | latest version of every prescription
SELECT prescription_id, version_number, change_reason
FROM (SELECT pv.*, ROW_NUMBER() OVER (PARTITION BY pv.prescription_id ORDER BY pv.version_number DESC) AS rn
      FROM prescription_version pv) t
WHERE rn = 1;

-- [S35] CTE | days with collections above the average day
WITH daily AS (SELECT DATE(occurred_at) AS day, SUM(amount) AS collected FROM payment WHERE payment_kind = 'payment' GROUP BY DATE(occurred_at))
SELECT day, collected FROM daily WHERE collected > (SELECT AVG(collected) FROM daily) ORDER BY collected DESC;

-- [S36] RECURSIVE CTE (calendar) | visits per day for the last 14 days INCLUDING days with zero visits
WITH RECURSIVE days (d) AS (
    SELECT CURDATE() - INTERVAL 13 DAY
    UNION ALL
    SELECT d + INTERVAL 1 DAY FROM days WHERE d < CURDATE())
SELECT days.d, COUNT(v.visit_id) AS visits
FROM days LEFT JOIN visit v ON DATE(v.occurred_at) = days.d
GROUP BY days.d
ORDER BY days.d;

-- [S37] RECURSIVE CTE (chain) | walk a prescription's version chain 1 -> 2 -> 3 (R4.1)
WITH RECURSIVE chain AS (
    SELECT prescription_id, version_number, change_reason, 1 AS depth FROM prescription_version WHERE version_number = 1
    UNION ALL
    SELECT pv.prescription_id, pv.version_number, pv.change_reason, c.depth + 1
    FROM prescription_version pv JOIN chain c ON pv.prescription_id = c.prescription_id AND pv.version_number = c.version_number + 1)
SELECT * FROM chain ORDER BY prescription_id, version_number;

-- ============================ F. Derived business facts (rules R-numbers) ============================

-- [S38] CASE + derived bill state and balance (G3, R8.3, R8.5) | balance = lines - payments(+reversals) + refunds
SELECT b.bill_number,
       t.total, COALESCE(pay.paid, 0) AS paid, COALESCE(rf.refunded, 0) AS refunded,
       t.total - COALESCE(pay.paid, 0) + COALESCE(rf.refunded, 0) AS balance,
       CASE WHEN bf.bill_id IS NULL THEN 'Draft'
            WHEN t.total - COALESCE(pay.paid, 0) + COALESCE(rf.refunded, 0) <= 0 THEN 'Settled'
            ELSE 'Finalized' END AS state
FROM bill b
JOIN (SELECT bill_id, SUM(quantity * unit_price_snapshot) AS total FROM bill_line GROUP BY bill_id) t ON t.bill_id = b.bill_id
LEFT JOIN (SELECT bill_id, SUM(CASE payment_kind WHEN 'payment' THEN amount WHEN 'reversal' THEN -amount END) AS paid FROM payment GROUP BY bill_id) pay ON pay.bill_id = b.bill_id
LEFT JOIN (SELECT bill_id, SUM(amount) AS refunded FROM refund GROUP BY bill_id) rf ON rf.bill_id = b.bill_id
LEFT JOIN bill_finalization bf ON bf.bill_id = b.bill_id
ORDER BY b.bill_id DESC
LIMIT 15;

-- [S39] Dues list (R9.3) | finalized bills still owing money, with the promised-by date
SELECT b.bill_number, x.balance, dp.promised_by_date
FROM bill b
JOIN (SELECT bl.bill_id, SUM(bl.quantity * bl.unit_price_snapshot) - COALESCE((SELECT SUM(p.amount) FROM payment p WHERE p.bill_id = bl.bill_id AND p.payment_kind = 'payment'), 0) AS balance
      FROM bill_line bl GROUP BY bl.bill_id) x ON x.bill_id = b.bill_id
JOIN bill_finalization bf ON bf.bill_id = b.bill_id
LEFT JOIN due_promise dp ON dp.bill_id = b.bill_id
WHERE x.balance > 0
ORDER BY x.balance DESC;

-- [S40] Ledger SUM (G3, R5.2) | stock on hand per medicine, expired batches excluded
SELECT m.brand_name, SUM(sm.quantity_delta) AS sellable_stock
FROM medicine m
JOIN stock_batch sb ON sb.medicine_id = m.medicine_id AND sb.expiry_date >= CURDATE()
JOIN stock_movement sm ON sm.stock_batch_id = sb.stock_batch_id
GROUP BY m.medicine_id, m.brand_name
ORDER BY sellable_stock;

-- [S41] FEFO (R5.4) | per medicine the batch to sell first: earliest expiry that is not expired and still has stock
SELECT m.brand_name, v.batch_code, v.expiry_date, v.on_hand
FROM vs_stock_on_hand v JOIN medicine m ON m.medicine_id = v.medicine_id
WHERE v.on_hand > 0 AND v.expiry_date >= CURDATE()
  AND v.expiry_date = (SELECT MIN(v2.expiry_date) FROM vs_stock_on_hand v2
                       WHERE v2.medicine_id = v.medicine_id AND v2.on_hand > 0 AND v2.expiry_date >= CURDATE())
ORDER BY m.brand_name;

-- [S42] Reconciliation R6.2 | for each order-linked dispensing: dispensed = given + returned + handed to family ?
SELECT s.dispensing_id, s.dispensed,
       COALESCE(g.given, 0) AS given, COALESCE(r.returned, 0) AS returned, COALESCE(h.to_family, 0) AS to_family,
       s.dispensed - COALESCE(g.given, 0) - COALESCE(r.returned, 0) - COALESCE(h.to_family, 0) AS mismatch
FROM (SELECT dispensing_id, SUM(quantity_allocated) AS dispensed FROM order_item_supply GROUP BY dispensing_id) s
LEFT JOIN (SELECT ois.dispensing_id, SUM(da.quantity_consumed) AS given FROM dose_administration da
           JOIN order_item_supply ois ON ois.order_item_supply_id = da.order_item_supply_id GROUP BY ois.dispensing_id) g ON g.dispensing_id = s.dispensing_id
LEFT JOIN (SELECT lrq.dispensing_id, SUM(sm.quantity_delta) AS returned FROM leftover_return_request lrq
           JOIN leftover_return_decision d ON d.leftover_return_request_id = lrq.leftover_return_request_id AND d.decision = 'accepted'
           JOIN stock_movement sm ON sm.stock_movement_id = d.stock_movement_id GROUP BY lrq.dispensing_id) r ON r.dispensing_id = s.dispensing_id
LEFT JOIN (SELECT ch.dispensing_id, SUM(a.acknowledged_quantity) AS to_family FROM custody_handoff ch
           JOIN custody_handoff_acknowledgement a ON a.custody_handoff_id = ch.custody_handoff_id
           WHERE ch.handoff_purpose = 'leftover_to_family' GROUP BY ch.dispensing_id) h ON h.dispensing_id = s.dispensing_id;

-- [S43] Reconciliation R7.5 (a) | lab tests ordered but never billed
SELECT lo.lab_order_id, lo.test_name, lo.visit_id
FROM lab_order lo
WHERE NOT EXISTS (SELECT 1 FROM bill_line bl WHERE bl.lab_order_id = lo.lab_order_id);

-- [S44] Reconciliation R7.5 (b) | billed lab tests with no report after N days (N = setting billed_no_report_days)
SELECT lo.lab_order_id, lo.test_name, DATEDIFF(CURDATE(), lo.occurred_at) AS days_waiting
FROM lab_order lo
WHERE EXISTS (SELECT 1 FROM bill_line bl WHERE bl.lab_order_id = lo.lab_order_id)
  AND NOT EXISTS (SELECT 1 FROM lab_report r WHERE r.lab_order_id = lo.lab_order_id)
  AND DATEDIFF(CURDATE(), lo.occurred_at) > COALESCE((SELECT CAST(setting_value AS UNSIGNED) FROM clinic_settings WHERE setting_key = 'billed_no_report_days'), 3);

-- [S45] Owner exception (R10.3/R10.5) | discounts per employee: who asked, who approved, how much
SELECT CONCAT(rq.given_name, ' ', rq.family_name) AS requested_by, COUNT(*) AS discounts, SUM(-bl.quantity * bl.unit_price_snapshot) AS amount
FROM bill_line bl
JOIN approval_request ar ON ar.approval_request_id = bl.approval_request_id
JOIN staff rq ON rq.staff_id = ar.recorded_by
WHERE bl.line_type IN ('discount', 'free_service')
GROUP BY rq.staff_id, rq.given_name, rq.family_name;

-- [S46] Owner exception (R10.5) | backfilled (late) entries per person per table family
SELECT s.family_name AS person, 'visit' AS source, COUNT(*) AS backfilled FROM visit v JOIN staff s ON s.staff_id = v.recorded_by WHERE v.is_backfilled = 1 GROUP BY s.staff_id, s.family_name
UNION ALL
SELECT s.family_name, 'payment', COUNT(*) FROM payment p JOIN staff s ON s.staff_id = p.recorded_by WHERE p.is_backfilled = 1 GROUP BY s.staff_id, s.family_name
ORDER BY backfilled DESC;

-- [S47] Maker-checker (G6) | approval requests still waiting for a decision (owner inbox)
SELECT ar.approval_request_id, ar.request_type, ar.requested_amount, ar.reason, ar.recorded_at
FROM approval_request ar
WHERE NOT EXISTS (SELECT 1 FROM approval_decision d WHERE d.approval_request_id = ar.approval_request_id)
ORDER BY ar.recorded_at;

-- [S48] Alerts (R10.4) | safety interrupts nobody has acknowledged yet
SELECT ae.alert_event_id, ae.alert_type, ae.summary, ae.recorded_at
FROM alert_event ae
WHERE ae.alert_class = 'safety_interrupt'
  AND NOT EXISTS (SELECT 1 FROM alert_acknowledgement k WHERE k.alert_event_id = ae.alert_event_id);

-- ============================ G. Functions, JSON, text search ============================

-- [S49] JSON functions | what changed in a patient edit (audit_log keeps old/new values as JSON)
SELECT audit_log_id, entity_type, entity_id, JSON_EXTRACT(old_values, '$') AS old_values, JSON_EXTRACT(new_values, '$') AS new_values
FROM audit_log
WHERE entity_type = 'patient'
ORDER BY audit_log_id DESC
LIMIT 5;

-- [S50] String distance (R1.4 tiers) | near-duplicate patients; levenshtein() is created by db/logic/02_patients_visits.sql (P3-2)
SELECT a.display_code AS patient_a, b.display_code AS patient_b,
       levenshtein(CONCAT(a.given_name, ' ', a.family_name), CONCAT(b.given_name, ' ', b.family_name)) AS name_distance
FROM patient a JOIN patient b ON b.patient_id > a.patient_id AND b.date_of_birth = a.date_of_birth
WHERE levenshtein(CONCAT(a.given_name, ' ', a.family_name), CONCAT(b.given_name, ' ', b.family_name)) <= 3
ORDER BY name_distance;

-- [S51] SOUNDEX pre-filter (R1.4) | the cheap filter used before Levenshtein
SELECT a.display_code, b.display_code
FROM patient a JOIN patient b ON b.patient_id > a.patient_id AND SOUNDEX(a.family_name) = SOUNDEX(b.family_name) AND a.date_of_birth = b.date_of_birth;

-- [S52] FULLTEXT (type-ahead, R4.2/R12.3) | needs the index below; the benchmark (Q9) measures it on 20k medicines
-- ALTER TABLE medicine ADD FULLTEXT INDEX ft_medicine_names (brand_name, generic_display_name);   -- run once, then un-comment:
-- SELECT medicine_id, brand_name FROM medicine WHERE MATCH(brand_name, generic_display_name) AGAINST ('para*' IN BOOLEAN MODE);
SELECT medicine_id, brand_name FROM medicine WHERE brand_name LIKE 'Para%' ORDER BY brand_name;

-- [S53] DISTINCT + LIMIT/OFFSET (pagination) | second page of distinct dosage forms
SELECT DISTINCT dosage_form FROM medicine ORDER BY dosage_form LIMIT 5 OFFSET 5;
