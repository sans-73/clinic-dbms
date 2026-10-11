-- benchmark_queries.sql  (P4-3)  -  runs against database `clinic_bench` (created by tools/gen_bulk_data.py)
--
-- Read by tools/run_benchmarks.py. Lines starting with "-- @" are runner directives:
--   @query ID | title | structure   start a block          @drop STMT   run BEFORE the "before" measurement (errors ignored)
--   @index STMT                     run between before/after  @after   everything after this line = SQL for the "after" run
--   @special partition              runner builds bx_audit_part (partitioned copy of audit_log) before the block
--   @end                            end of block
-- The plain SELECTs can also be run by hand:  mysql ... clinic_bench < db/queries/benchmark_queries.sql
-- Index names all start with bx_ so they never clash with indexes of the real schema.
-- If the real schema ALREADY has a matching index, "before" is already fast: that is a valid finding, say so in the report.

USE clinic_bench;

-- ---- probe values (taken from rows that really exist, so every query returns something) ----
SET @max_p        = (SELECT MAX(patient_id) FROM patient);
SET @probe_phone  = (SELECT phone_number FROM patient_phone WHERE patient_id = @max_p DIV 2 AND phone_sequence = 1);
SET @probe_given  = (SELECT given_name  FROM patient WHERE patient_id = @max_p DIV 3);
SET @probe_family = (SELECT family_name FROM patient WHERE patient_id = @max_p DIV 3);
SET @probe_dob    = (SELECT date_of_birth FROM patient WHERE patient_id = @max_p DIV 4);
SET @probe_family2= (SELECT family_name FROM patient WHERE patient_id = @max_p DIV 4);
SET @max_m        = (SELECT MAX(medicine_id) FROM medicine);
SET @probe_med    = @max_m DIV 2;
SET @probe_stem   = (SELECT LEFT(brand_name, 4) FROM medicine WHERE medicine_id = @max_m DIV 3);
SET @probe_token  = (SELECT token_hash FROM family_link WHERE family_link_id = (SELECT MAX(family_link_id) DIV 2 FROM family_link));
SET @probe_user   = 5;
SET @m_start      = DATE_FORMAT(NOW() - INTERVAL 6 MONTH, '%Y-%m-01');
SET @m_end        = @m_start + INTERVAL 1 MONTH;

-- @query Q1 | Patient search by name (reception) | secondary B+ tree, composite (family_name, given_name)
-- @drop DROP INDEX bx_patient_name ON patient
-- @index CREATE INDEX bx_patient_name ON patient (family_name, given_name)
SELECT patient_id, display_code, given_name, family_name, date_of_birth
FROM patient
WHERE family_name = @probe_family AND given_name LIKE CONCAT(LEFT(@probe_given, 3), '%');
-- @end

-- @query Q2 | Patient lookup by phone number | secondary B+ tree on patient_phone(phone_number)
-- @drop DROP INDEX bx_phone_number ON patient_phone
-- @index CREATE INDEX bx_phone_number ON patient_phone (phone_number)
SELECT p.patient_id, p.display_code, p.given_name, p.family_name
FROM patient_phone ph JOIN patient p ON p.patient_id = ph.patient_id
WHERE ph.phone_number = @probe_phone;
-- @end

-- @query Q3 | Duplicate candidates, tier T2 (same DOB + similar name via SOUNDEX) | secondary B+ tree on date_of_birth narrows, SOUNDEX filters
-- @drop DROP INDEX bx_patient_dob ON patient
-- @index CREATE INDEX bx_patient_dob ON patient (date_of_birth)
SELECT patient_id, given_name, family_name, date_of_birth
FROM patient
WHERE date_of_birth = @probe_dob AND SOUNDEX(family_name) = SOUNDEX(@probe_family2);
-- @end

-- @query Q4 | Today's queue: visits opened today whose LATEST status is 'Waiting' | secondary B+ tree on visit(occurred_at); status trail already has UNIQUE(visit_id,status_seq)
-- @drop DROP INDEX bx_visit_time ON visit
-- @index CREATE INDEX bx_visit_time ON visit (occurred_at)
SELECT v.visit_id, v.patient_id, h.status, h.occurred_at AS status_since
FROM visit v
JOIN visit_status_history h
  ON h.visit_id = v.visit_id
 AND h.status_seq = (SELECT MAX(h2.status_seq) FROM visit_status_history h2 WHERE h2.visit_id = v.visit_id)
WHERE v.occurred_at >= CURDATE() AND h.status = 'Waiting'
ORDER BY v.occurred_at;
-- @end

-- @query Q5 | Batches expiring within 60 days with stock on hand and supplier phone (owner list) | B+ tree range scan on expiry_date + covering index on the ledger
-- @drop DROP INDEX bx_batch_expiry ON stock_batch
-- @drop DROP INDEX bx_sm_batch_qty ON stock_movement
-- @index CREATE INDEX bx_batch_expiry ON stock_batch (expiry_date)
-- @index CREATE INDEX bx_sm_batch_qty ON stock_movement (stock_batch_id, quantity_delta)
SELECT b.stock_batch_id, m.brand_name, b.batch_code, b.expiry_date, s.phone AS supplier_phone, SUM(sm.quantity_delta) AS on_hand
FROM stock_batch b
JOIN medicine m ON m.medicine_id = b.medicine_id
LEFT JOIN supplier s ON s.supplier_id = b.supplier_id
JOIN stock_movement sm ON sm.stock_batch_id = b.stock_batch_id
WHERE b.expiry_date BETWEEN CURDATE() AND CURDATE() + INTERVAL 60 DAY
GROUP BY b.stock_batch_id, m.brand_name, b.batch_code, b.expiry_date, s.phone
HAVING on_hand > 0
ORDER BY b.expiry_date;
-- @end

-- @query Q6 | FEFO: first batch to sell for one medicine | composite B+ tree (medicine_id, expiry_date) gives the order for free
-- @drop DROP INDEX bx_batch_med_expiry ON stock_batch
-- @index CREATE INDEX bx_batch_med_expiry ON stock_batch (medicine_id, expiry_date)
SELECT b.stock_batch_id, b.batch_code, b.expiry_date,
       (SELECT COALESCE(SUM(sm.quantity_delta), 0) FROM stock_movement sm WHERE sm.stock_batch_id = b.stock_batch_id) AS on_hand
FROM stock_batch b
WHERE b.medicine_id = @probe_med AND b.expiry_date >= CURDATE()
HAVING on_hand > 0
ORDER BY b.expiry_date
LIMIT 1;
-- @end

-- @query Q7 | Owner daily summary: today's collections by payment method | B+ tree on payment(recorded_at)
-- @drop DROP INDEX bx_payment_time ON payment
-- @index CREATE INDEX bx_payment_time ON payment (recorded_at)
SELECT method, COUNT(*) AS payments, SUM(amount) AS total
FROM payment
WHERE payment_kind = 'payment' AND recorded_at >= CURDATE()
GROUP BY method;
-- @end

-- @query Q8 | Audit trail of one user in a date range, newest first | composite B+ tree (recorded_by, recorded_at): equality then range then ORDER BY
-- @drop DROP INDEX bx_audit_user_time ON audit_log
-- @index CREATE INDEX bx_audit_user_time ON audit_log (recorded_by, recorded_at)
SELECT audit_log_id, action, entity_type, entity_id, recorded_at
FROM audit_log
WHERE recorded_by = @probe_user AND recorded_at >= NOW() - INTERVAL 30 DAY
ORDER BY recorded_at DESC
LIMIT 50;
-- @end

-- ================= extra structure demos (Module 6) =================

-- @query Q9 | Medicine type-ahead: LIKE '%text%' versus FULLTEXT | inverted index (FULLTEXT) instead of B+ tree
-- @drop ALTER TABLE medicine DROP INDEX bx_ft_medicine
-- @index ALTER TABLE medicine ADD FULLTEXT INDEX bx_ft_medicine (brand_name, generic_display_name)
SELECT medicine_id, brand_name, generic_display_name
FROM medicine
WHERE brand_name LIKE CONCAT('%', @probe_stem, '%') OR generic_display_name LIKE CONCAT('%', @probe_stem, '%')
LIMIT 20;
-- @after
SELECT medicine_id, brand_name, generic_display_name
FROM medicine
WHERE MATCH(brand_name, generic_display_name) AGAINST (CONCAT(@probe_stem, '*') IN BOOLEAN MODE)
LIMIT 20;
-- @end

-- @query Q10 | Family-link token lookup: B+ tree (UNIQUE key) versus in-memory HASH index | hash index (MEMORY engine): O(1) equality only, no ranges
-- @drop DROP TABLE IF EXISTS bx_token_hash
-- @index CREATE TABLE bx_token_hash (token_hash CHAR(64) NOT NULL, family_link_id BIGINT UNSIGNED NOT NULL, PRIMARY KEY USING HASH (token_hash)) ENGINE=MEMORY
-- @index INSERT INTO bx_token_hash SELECT token_hash, family_link_id FROM family_link
SELECT family_link_id, visit_id FROM family_link WHERE token_hash = @probe_token;
-- @after
SELECT f.family_link_id, f.visit_id
FROM bx_token_hash h JOIN family_link f ON f.family_link_id = h.family_link_id
WHERE h.token_hash = @probe_token;
-- @end

-- @query Q11 | Audit volume of one month: whole table versus date-PARTITIONED copy (partition pruning) | horizontal partitioning
-- @special partition
SELECT COUNT(*) AS rows_in_month, COUNT(DISTINCT recorded_by) AS people
FROM audit_log
WHERE recorded_at >= @m_start AND recorded_at < @m_end;
-- @after
SELECT COUNT(*) AS rows_in_month, COUNT(DISTINCT recorded_by) AS people
FROM bx_audit_part
WHERE recorded_at >= @m_start AND recorded_at < @m_end;
-- @end
