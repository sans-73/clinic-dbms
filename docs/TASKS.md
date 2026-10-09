# TASKS: one card per job

Copy **one card** (plus `PROJECT_CONTEXT.md`) into the Standard Prompt from `00_START_HERE.md`.
Each card says: **Role, Needs** (what must be finished first), **Parallel with**, **Paste** (input files), **Do**, **Create** (exact files), **You check** (how you verify), and **Commit** message.

## Shortcuts used in the "You check" lines
- **[RESET]** = `docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" < /db/reset.sql'`
- **[RUNALL]** = `docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" < /db/run_all.sql'`
- **[SQL path]** = `docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db < /db/path'` (example: `/db/seed/demo_seed.sql`)
- **[MYSQL]** = `docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db'`
- "Zero errors" = the output contains no line starting with `ERROR`.

## Standard rules for every SQL task
Follow section 9 of `PROJECT_CONTEXT.md`. Plus: every logic file begins with a comment block listing its public procedures/functions/views with arguments and a one-line meaning. Every procedure that changes data uses a transaction and `SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '<clear message>'` for rule violations. Messages must be readable by a receptionist ("Slot already booked for this doctor").

## Shared interfaces (so parallel AIs can call each other's code)
Owned by **P3-1**; every other DB-LOGIC task may call these exact signatures:
- `raise_alert(IN p_type VARCHAR(40), IN p_severity VARCHAR(10), IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED, IN p_message VARCHAR(300))` severity is `INTERRUPT` or `SUMMARY`.
- `queue_message(IN p_to_phone VARCHAR(20), IN p_template VARCHAR(40), IN p_body VARCHAR(500), IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED)`
- `request_approval(IN p_type VARCHAR(40), IN p_ref_table VARCHAR(40), IN p_ref_id BIGINT UNSIGNED, IN p_reason VARCHAR(300), IN p_amount DECIMAL(12,2), OUT p_approval_id BIGINT UNSIGNED)`
- `decide_approval(IN p_approval_id BIGINT UNSIGNED, IN p_decision VARCHAR(10), IN p_note VARCHAR(300))` decision `APPROVED` or `REJECTED`; refuses if approver = requester.
- `next_code(IN p_kind VARCHAR(20), OUT p_code VARCHAR(30))` builds codes like `CLN1-000123` from `clinic_settings` + `id_counter`.
- `setting_int(p_key VARCHAR(60))` and `setting_text(p_key VARCHAR(60))` read `clinic_settings`.
Owned by **P3-5** (billing); callable by pharmacy/lab/stay code:
- `ensure_open_bill(IN p_visit_id BIGINT UNSIGNED, IN p_counter_customer_id BIGINT UNSIGNED, OUT p_bill_id BIGINT UNSIGNED)` (exactly one of the two IDs non-NULL)
- `bill_add_line(IN p_bill_id BIGINT UNSIGNED, IN p_source_type VARCHAR(30), IN p_source_id BIGINT UNSIGNED, IN p_description VARCHAR(200), IN p_quantity DECIMAL(10,2), IN p_unit_price DECIMAL(12,2), IN p_reason VARCHAR(200))`
- `bill_add_credit_line(IN p_bill_id BIGINT UNSIGNED, IN p_source_type VARCHAR(30), IN p_source_id BIGINT UNSIGNED, IN p_description VARCHAR(200), IN p_amount DECIMAL(12,2), IN p_reason VARCHAR(200))`

---

# PHASE 0: Setup (you, no AI needed)

### P0-1 Install tools
- **Role:** You. **Needs:** nothing. **Parallel with:** P1-6.
- **Do:** Install Git (Windows: Git for Windows, includes Git Bash), Docker Desktop, VS Code, Python 3.11+.
- **You check:** In Git Bash run `git --version`, `docker --version`, `python --version`. All print a version.
- **Stuck?** Paste the error to any AI: "I'm a beginner on <Windows/Mac/Linux>, fix this install error".

### P0-2 GitHub repo
- **Do:** Create a GitHub account and an **empty** repo named `clinic-dbms` (private is fine for now).
- **You check:** You can see the empty repo page with its URL.

### P0-3 First commit
- **Do:** Unzip this kit into a folder `clinic-dbms`; follow section 3 of `00_START_HERE.md` (init, commit, push).
- **You check:** Refresh GitHub: you see `docs/`, `db/`, `README.md`.
- **Commit:** `P0: starter kit`

### P0-4 Start MySQL
- **Do:** `cp .env.example .env`, set a password in `.env`, `docker compose up -d`.
- **You check:** `docker compose ps` shows `healthy`. [MYSQL] opens a prompt; `SHOW DATABASES;` lists `clinic_db`.

---

# PHASE 1: Design (documents only, no code)

### P1-6 DOCS: Module 1 theory notes
- **Role:** DOCS. **Needs:** nothing. **Parallel with:** everything in Phase 0-1.
- **Paste:** CONTEXT sections 1, 2, 10.
- **Do:** Write clear study notes, each tied to our clinic project with one example: DBMS historical perspective; file system vs DBMS; advantages; three-level architecture; physical and logical data independence; DDL/DML/DCL and interfaces; DBA and database user types; comparison of data models (hierarchical, network, relational, document, key-value, graph) with a table; protection and security.
- **Create:** `docs/theory/module1_notes.md`
- **You check:** File exists, readable, has all the topics above.
- **Commit:** `P1-6: module 1 theory notes`

### P1-1 ARCHITECT: ER and Extended ER model
- **Needs:** P0-3. **Parallel with:** P1-6.
- **Paste:** CONTEXT (all).
- **Do:** Produce the conceptual design: entities, attributes (mark key, composite, multivalued, derived), relationships with cardinality and participation, weak entities (e.g. status history, vitals), specialization/generalization (Staff -> Doctor/Nurse/etc., overlapping via multiple roles; Visit -> OPD/Short-Stay as partial overlapping), a ternary relationship where it truly fits, and a "design for large enterprise" note explaining how the model would be split into subject areas (identity, scheduling, clinical orders, pharmacy, billing, audit). Draw one Mermaid `erDiagram` per subject area (GitHub renders them) plus one overview. Include a table mapping **every rule R-number to the entities that support it**.
- **Create:** `docs/design/01_er_model.md`
- **You check:** Paste the Mermaid blocks into https://mermaid.live to see they render. Skim: every feature F1-F12 appears.
- **Commit:** `P1-1: ER model`

### P1-2 REVIEWER: review the ER model
- **Needs:** P1-1. Use a **different AI** than P1-1.
- **Paste:** CONTEXT + `docs/design/01_er_model.md`.
- **Do:** Check each R-rule: is it supportable by the model? List gaps, wrong cardinalities, missing entities, rules that cannot be enforced, over-complications. Output a numbered issue list with severity (must-fix / should-fix / nice).
- **Create:** `docs/design/review_01_er.md`
- **You check:** If any must-fix exists, send the list to the P1-1 AI: "Fix these, return the complete corrected file", then re-review once. Repeat until no must-fix.
- **Commit:** `P1-2: ER review closed`

### P1-3 ARCHITECT: tables (ER to relational mapping)
- **Needs:** P1-2 closed.
- **Paste:** CONTEXT + `01_er_model.md`.
- **Do:** Convert to tables. For **every table**: purpose, columns with MySQL types, PK, FKs, UNIQUE, CHECKs, indexes, whether **master or event** table, and which **schema file cluster** it belongs to: `01_core_settings_staff_audit`, `02_patients_visits`, `03_pharmacy`, `04_orders_billing_discharge`. Rules to bake in: standard event columns (context section 9); a `bill` belongs to **exactly one** of `visit_id` or `counter_customer_id` (CHECK); dispensing has exactly one source (CHECK); Aadhaar last-4 only; patient code and temp code; family link scoped to one visit. Also list: the **build order**, all **cross-file FKs** (these go in `09_foreign_keys.sql`), the **total table count**, and a **rule-to-table traceability table** (R-number -> table/column).
- **Create:** `docs/design/02_table_design.md`
- **You check:** Table count is stated; every table has a cluster; no table without a PK.
- **Commit:** `P1-3: table design`

### P1-4 ARCHITECT (can be a different AI): normalization and FD analysis
- **Needs:** P1-3.
- **Paste:** CONTEXT + `02_table_design.md`.
- **Do:** For the key tables list functional dependencies, candidate keys, and the highest normal form (aim BCNF). Show **at least three "messy to normalized" walkthroughs**: (1) one comma-separated allergies column -> 1NF; (2) a messy prescription/visit sheet through 2NF, 3NF, BCNF; (3) a medicine catalog (brand/generic/strength). Include one genuine **4NF multivalued dependency** example (e.g. staff languages vs staff specialties) and one **5NF/join dependency** discussion. Prove one **lossless join** and one **dependency preservation** check with small example data. Cover NULL-value problems and dangling tuples with our schema. Justify deliberate choices that look like redundancy (e.g. price snapshot on bill lines is history, not duplication).
- **Create:** `docs/design/03_normalization.md`
- **You check:** All items above are present with worked examples, not just definitions.
- **Commit:** `P1-4: normalization analysis`

### P1-5 REVIEWER: review table design and normalization
- **Needs:** P1-3, P1-4. Different AI than the authors.
- **Paste:** CONTEXT + `02_table_design.md` + `03_normalization.md`.
- **Do:** Check every R-rule against columns and constraints; find missing tables/columns, wrong types, impossible FKs, circular FK problems, event tables lacking standard columns, anything that violates G1-G12.
- **Create:** `docs/design/review_02_tables.md`
- **You check:** Fix loop as in P1-2 until no must-fix.
- **Gate 1:** Commit, then `git tag design-v1` and `git push --tags`.
- **Commit:** `P1-5: design review closed (Gate 1)`

---

# PHASE 2: Build the tables (4 AIs in parallel)

All four tasks: **Needs** Gate 1 (`design-v1`). **Parallel with** each other. **Paste:** CONTEXT + `02_table_design.md` (+ `01_er_model.md` if helpful). Each AI builds **only the tables assigned to its cluster**. FKs to tables in another file go in `09_foreign_keys.sql` (see P2-5), listed by the AI in its HANDOFF NOTE as "late FKs".

### P2-1 DB-BUILDER: cluster 01 core
- **Create:** `db/schema/01_core_settings_staff_audit.sql`
- **Do:** Tables of cluster 01 (clinic settings with the policy keys of context section 7 seeded as default rows, id counter, roles, staff, staff-role link, staff detail tables, audit log, approval request/decision, alert event, message outbox).
- **You check:** [RESET], then `docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db < /db/schema/01_core_settings_staff_audit.sql'` gives zero errors; in [MYSQL] `SHOW TABLES;` lists the cluster's tables.
- **Commit:** `P2-1: core schema`

### P2-2 DB-BUILDER: cluster 02 patients and visits
- **Create:** `db/schema/02_patients_visits.sql`
- **Do:** Patient, phones, allergies, attendants, merge log, appointments, schedule changes, visits, status history, short-stay episode and extensions, vitals, family links and link access log.
- **You check / Commit:** as P2-1 (`P2-2: patients and visits schema`). It may fail alone if it needs cluster 01 tables first; run 01 then 02.

### P2-3 DB-BUILDER: cluster 03 pharmacy
- **Create:** `db/schema/03_pharmacy.sql`
- **Do:** Medicine catalog (generic, brand, strength, form, schedule flag, returnable flag, default dose/schedule, MRP rules as designed), suppliers, stock batches, stock movement ledger, counter customers, outside prescriptions, dispensing, dose administration, leftover handling, catalog review queue, cash handover.
- **You check / Commit:** as P2-1 (`P2-3: pharmacy schema`).

### P2-4 DB-BUILDER: cluster 04 orders, billing, discharge
- **Create:** `db/schema/04_orders_billing_discharge.sql`
- **Do:** Prescription (versioned) and items, outside labs, lab orders with status trail, lab reports (with hash and version), price list, bills and lines, payments, refunds, discharge records, dues.
- **You check / Commit:** as P2-1 (`P2-4: orders and billing schema`).

### P2-5 You + DB-BUILDER: integration and late foreign keys
- **Needs:** P2-1 to P2-4. 
- **Do (you):** In `db/run_all.sql` uncomment the four schema lines. Ask any DB-BUILDER AI (paste the four HANDOFF NOTES' "late FKs" lists + the table design) to produce `db/schema/09_foreign_keys.sql` and uncomment its line.
- **Create:** `db/schema/09_foreign_keys.sql`
- **You check:** [RESET] then [RUNALL]: **zero errors**. In [MYSQL], `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='clinic_db';` equals the table count in `02_table_design.md`. If errors appear, paste the exact error and the file name to that cluster's AI.
- **Commit:** `P2-5: schema integrates cleanly`

### P2-6 REVIEWER: schema vs design
- **Needs:** P2-5. Different AI than the builders.
- **Paste:** CONTEXT + `02_table_design.md` + the five SQL files.
- **Do:** List every difference between design and SQL (missing columns, wrong types, missing constraints, event tables missing standard columns). 
- **Create:** `docs/design/review_03_schema.md`
- **You check:** Fix all must-fix with the owning AI, rerun [RESET] + [RUNALL].
- **Gate 2:** `git tag schema-v1`, `git push --tags`.
- **Commit:** `P2-6: schema review closed (Gate 2)`

---

# PHASE 3: Database rules (triggers, procedures, views)

Every DB-LOGIC task: **Paste** CONTEXT + `02_table_design.md` + the schema SQL files (all five) + the "Shared interfaces" section above. Each also writes a short API note in `docs/db_api/<domain>.md` listing every procedure/view with arguments, result, and error messages, so WEB AIs can call them.

### P3-1 DB-LOGIC: foundation (do this one ALONE first)
- **Needs:** Gate 2.
- **Create:** `db/logic/01_foundation_triggers.sql`, `docs/db_api/foundation.md`
- **Do:** (1) Functions `app_user()` and `app_role()` that read `@app_user_id`/`@app_role` and **SIGNAL if NULL**. (2) For **every event table**: BEFORE UPDATE and BEFORE DELETE triggers that always SIGNAL "records cannot be changed; add a correction". (3) BEFORE INSERT triggers on event tables that fill `recorded_at`, `recorded_by`, `recorded_by_role` (user cannot override) and check `is_backfilled` rules. (4) Audit triggers on master tables writing old/new values as JSON to `audit_log`. (5) The shared interfaces: `raise_alert`, `queue_message`, `request_approval`, `decide_approval` (reject self-approval), `next_code`, `setting_int`, `setting_text`. (6) `audit_log` itself insert-only.
- **You check:** [RESET] [RUNALL] (with the new line uncommented) zero errors. In [MYSQL]: `SET @app_user_id=1, @app_role='OWNER';` then try `UPDATE audit_log SET ...` -> error message appears. Try inserting an event row without setting `@app_user_id` -> error.
- **Commit:** `P3-1: foundation triggers and shared interfaces`

### P3-2 DB-LOGIC: patients and visits
- **Needs:** P3-1. **Parallel with:** P3-3, P3-4, P3-5, P3-6, P5-1.
- **Create:** `db/logic/02_patients_visits.sql`, `docs/db_api/patients_visits.md`
- **Do:** Function `levenshtein(a,b)`. `register_patient`, `find_patient_candidates` (tiers T1-T4 from R1.4, returns ranked candidates with tier and score), `register_unknown_patient`, `merge_patients` (single transaction, moves all linked rows, writes merge log, audited). Appointments: `book_appointment` (impossible to double-book, uses locking), status change procedure with **valid-transition checks**, reschedule that keeps history, `request_schedule_change` / apply. Visits: `start_visit`, `add_visit_status`, view of current status, `start_short_stay`, stay extension request, view `v_stay_over_limit`. Vitals insert procedure.
- **You check:** After running, in [MYSQL] call `find_patient_candidates` with a misspelled name from sample data you insert via `register_patient`; try booking the same doctor/slot twice -> second fails with a readable message.
- **Commit:** `P3-2: patient and visit logic`

### P3-3 DB-LOGIC: prescriptions and labs
- **Needs:** P3-1. **Parallel with:** P3-2, P3-4, P3-5, P3-6.
- **Create:** `db/logic/03_prescriptions_labs.sql`, `docs/db_api/prescriptions_labs.md`
- **Do:** Prescription create / **new version** procedures; item add with free-text support; `medicine_suggest(prefix, patient_id)` returning matches with **stock state (green/blue/red) and label** and default dose; allergy check (catalog generic match + recorded allergies) and **allergy confirmation record**; interrupt via `raise_alert` on override; free-text -> catalog review queue; "bought outside" marking and in-stock flag; short-stay orders. Labs: `order_lab_test`, status steps, `attach_lab_report` requiring **two identifiers** and storing a hash, versioning, `mark_report_reviewed`, patient-brought tagging.
- **You check:** Call `medicine_suggest('par', <patient>)` -> list with states. Try attaching a report with wrong identifiers -> refused.
- **Commit:** `P3-3: prescription and lab logic`

### P3-4 DB-LOGIC: pharmacy
- **Needs:** P3-1. **Parallel with:** P3-2, P3-3, P3-5, P3-6.
- **Create:** `db/logic/04_pharmacy.sql`, `docs/db_api/pharmacy.md`
- **Do:** Views: stock by batch (derived from ledger), by medicine, expiring soon (with supplier phone), FEFO suggestion. Procedures: `receive_stock`, `request_stock_adjustment` (via approval), reversal, `dispense` for the 3 sources with **row locking** so the last strip cannot be sold twice, expired-batch block trigger (+ interrupt alert), Schedule H/H1/X OTC block, call `ensure_open_bill` + `bill_add_line` using batch MRP. `administer_dose` (all statuses, ordered vs actual, reasons). Leftover flow: `ask_leftover_return`, `decide_leftover` (accept: stock movement + `bill_add_credit_line`; refuse: handover record). View `v_dispense_reconciliation` (dispensed = administered + returned + handed to family) and flagging of mismatch.
- **You check:** With sample batch of qty 1, run two `dispense` calls in two terminals at once: only one succeeds (see Phase 6 for the full demo). Try dispensing an expired batch -> blocked.
- **Commit:** `P3-4: pharmacy logic`

### P3-5 DB-LOGIC: billing, payments, discharge
- **Needs:** P3-1. **Parallel with:** P3-2, P3-3, P3-4, P3-6.
- **Create:** `db/logic/05_billing_discharge.sql`, `docs/db_api/billing_discharge.md`
- **Do:** Implement the three billing interfaces exactly as specified above. Fixed-price line creation from `price_list` by effective date; outside-lab price with deviation flag; bill states Draft -> Finalized -> Settled; after finalization only credit/debit lines; discount/free requests via approval (doctor limit from settings); payments (append-only, reversals), computed balance view, auto receipt via `queue_message`; refund request and approved refund; pharmacy counter-sale payment procedure (only for counter bills); daily collection and **cash handover** with difference alert. Discharge: view `v_discharge_checklist` (all items from R9.4), `request_partial_payment_discharge`, `discharge_visit` (one transaction closing visit, stay, bill; logged emergency override; creates `due` with promised-by date).
- **You check:** Create a bill with one line, try editing after finalizing -> refused; discharge with balance > 0 -> refused; with approved partial approval -> succeeds and a due row exists.
- **Commit:** `P3-5: billing and discharge logic`

### P3-6 DB-LOGIC: owner alerts, reports, family links
- **Needs:** P3-1. **Parallel with:** P3-2 to P3-5.
- **Create:** `db/logic/06_owner_alerts_links.sql`, `docs/db_api/owner_links.md`
- **Do:** `check_alerts()` procedure (stays over limit, unreviewed reports, unbilled visits, billed-no-report, etc.) and MySQL EVENT definitions (disabled by default). Acknowledge and **escalation** procedures for interrupts. Daily summary views ranked red/amber/clean; exception views for the R10.5 list (discounts per employee, refunds, backdating, edits after payment, shared logins, OTC patterns, frequent adjustments, services free often, ordered-not-billed). Family links: create link (random token, scope, expiry), `open_family_link(token, dob)` with **lockout after N failures and escalating delay**, access log, revoke, printed-code fallback, and a **view exposing only shareable data** for one visit.
- **You check:** Call `open_family_link` with wrong DOB 5 times -> locked; correct DOB after cooldown -> works; access log has every attempt.
- **Commit:** `P3-6: owner alerts and family links`

### P3-7 SECURITY: roles, grants, forbidden-action demo
- **Needs:** P3-2 to P3-6.
- **Paste:** CONTEXT + design + all logic files + all `docs/db_api/*.md`.
- **Create:** `db/security/01_roles_and_grants.sql`, `docs/demos/security_matrix.md`
- **Do:** MySQL roles and one account per role group; `GRANT EXECUTE` on only the procedures each role may call; `GRANT SELECT` only on role-specific **views** (reception view with no clinical columns; no cost price for non-owner/pharmacy); no direct write grants on event tables. Write a **forbidden-action script** per role (e.g. reception tries to read diagnosis, nurse tries to edit a prescription, doctor tries to record a payment) and a matrix of role vs action with expected result.
- **You check:** Uncomment the security line in `run_all.sql`; [RESET] [RUNALL] zero errors. Run the forbidden-action script: every attempt prints an "access denied" or rule error (never succeeds).
- **Commit:** `P3-7: roles and grants`

### P3-8 QA-DEMO: rule tests
- **Needs:** P3-2 to P3-7.
- **Paste:** CONTEXT + db_api notes + logic files.
- **Create:** `db/tests/rule_tests.sql` (one test per R-rule that is enforceable) and `db/tests/README.md` (how to run, how to read results).
- **Do:** Each test prints `PASS <rule> <name>` or `FAIL ...`, using handlers so an *expected* error counts as PASS. Include setup and cleanup so tests can run twice.
- **You check:** [SQL tests/rule_tests.sql]: all lines PASS. Any FAIL goes to the AI that owns that logic file (the test names the rule).
- **Gate 3:** `git tag logic-v1`.
- **Commit:** `P3-8: rule tests (Gate 3)`

---

# PHASE 4: Data and performance

### P4-1 DATA-PERF: demo seed data
- **Needs:** Gate 3. **Parallel with:** P4-4.
- **Paste:** CONTEXT + design + db_api notes.
- **Create:** `db/seed/demo_seed.sql`
- **Do:** Small, realistic **fake** dataset created through the procedures (so rules are respected): 1 owner, 3 doctors, receptionist, 2 nurses, pharmacist, accountant; ~40 patients with Indian-style names incl. deliberate near-duplicates; medicines (some Schedule H, some returnable), batches (one near expiry, one expired), suppliers, outside labs, price list, appointments, ~30 visits including short stays, prescriptions, dispensing, doses, bills, payments, one partial-payment discharge, a few alerts. Also staff logins with **known demo passwords** documented in the file header (hash storage per the WEB task).
- **You check:** [RESET] [RUNALL] then [SQL seed/demo_seed.sql] zero errors; `SELECT COUNT(*) FROM patient;` about 40.
- **Commit:** `P4-1: demo seed`

### P4-2 DATA-PERF: bulk data generator
- **Needs:** Gate 3. **Parallel with:** P4-3 (needs output), P5 tasks.
- **Create:** `tools/gen_bulk_data.py`, `tools/README.md`
- **Do:** Python script (seeded random, fake Indian names, no real data) that loads **100k+ patients, 300k+ visits, millions of ledger/audit rows if feasible** into a **separate database `clinic_bench`** (same schema), using batched inserts for speed. Parameters for row counts. Document runtime and how to install packages (`pip install ...`) as a beginner.
- **You check:** Follow `tools/README.md`; afterwards `SELECT COUNT(*) FROM clinic_bench.patient;` is 100000+.
- **Commit:** `P4-2: bulk data generator`

### P4-3 DATA-PERF: indexing benchmarks
- **Needs:** P4-2.
- **Paste:** CONTEXT + design + `tools/README.md`.
- **Create:** `db/queries/benchmark_queries.sql`, `docs/performance/benchmarks.md`, `tools/run_benchmarks.py`
- **Do:** Pick the 8 key queries (patient search by name, by phone, duplicate candidates, today's queue, stock by expiry, FEFO batch, owner daily summary, audit by user and date). For each: `EXPLAIN` / `EXPLAIN ANALYZE` **before** indexes, add the index (B-tree composite, FULLTEXT for medicine type-ahead, a hash-style lookup using the MEMORY engine for token lookup as a demo, date partitioning for a big event table), `EXPLAIN` **after**, timings table, and charts (PNG via matplotlib). Explain in plain words which structure is used (clustered primary index, secondary index, multi-level B+ tree) with a diagram.
- **You check:** `docs/performance/benchmarks.md` shows before/after numbers and images open.
- **Commit:** `P4-3: benchmarks`

### P4-4 DB-LOGIC: SQL query showcase (Module 3 proof)
- **Needs:** P4-1.
- **Paste:** CONTEXT + design + seed.
- **Create:** `db/queries/showcase.sql`, `docs/queries_theory.md`
- **Do:** 35+ clinic-relevant queries, each labelled with its syllabus topic: all join types, aggregates, GROUP BY/HAVING, EXISTS/NOT EXISTS, IN/ANY/ALL, set operations (UNION, INTERSECT, EXCEPT), **division** ("suppliers who supplied every category", "patients who have had every visit type"-style "for all" queries), nested and correlated subqueries, views, window functions, CTEs and a recursive CTE (e.g. chain of prescription versions). In `queries_theory.md` write at least 8 of them also as **relational algebra** and **tuple/domain relational calculus**, with an SQL-completeness note and a short query-optimization discussion pointing to the benchmark results.
- **You check:** [SQL queries/showcase.sql] runs without errors and prints results.
- **Commit:** `P4-4: query showcase`

### B1 BONUS (C++): index and hashing simulator
- **Role:** DATA-PERF. **Needs:** nothing. **Anytime.**
- **Create:** `bonus/bplus_tree.cpp`, `bonus/extendible_hash.cpp`, `bonus/README.md`
- **Do:** Small console programs in C++17: a B+ tree (insert, search, range scan, print levels) and **extendible hashing** (directory doubling, bucket split, print state). Tiny example data from our schema (patient codes). README explains how each relates to MySQL indexes and to Module 6.
- **You check:** `g++ -std=c++17 bonus/bplus_tree.cpp -o bplus && ./bplus` prints a tree. Same for the hash program.
- **Commit:** `B1: C++ index simulators`

---

# PHASE 5: Web app (screens)

Rules for all WEB tasks: Python 3.11+, FastAPI, Jinja2 templates, HTMX for small interactions, **parameterized queries only**, call the stored procedures and views listed in `docs/db_api/*.md` (do **not** re-implement rules in Python). Every request: set `@app_user_id` and `@app_role` on the connection before any query. Show database rule errors as friendly messages. Mobile-friendly, plain, fast screens.

### P5-1 WEB: skeleton, login, role menu (parallel with Phase 3 logic)
- **Needs:** Gate 2 and P3-1. **Parallel with:** P3-2 to P3-6.
- **Paste:** CONTEXT + `02_table_design.md` + `docs/db_api/foundation.md`.
- **Create:** `app/main.py`, `app/config.py`, `app/db.py`, `app/auth.py`, `app/templates/base.html`, `app/templates/login.html`, `app/templates/error.html`, `app/static/style.css`, `app/requirements.txt`, `app/README.md`
- **Do:** App factory that **auto-discovers routers** in `app/routes/*.py` (each exposes `router`) so later tasks never edit `main.py`. Login with password **hashing** (argon2 or bcrypt), signed session cookie, logout. `db.py`: per-request connection that sets `@app_user_id`/`@app_role` and picks the MySQL account by role when `DB_MODE=roles`, or one dev account when `DB_MODE=dev` (default until P3-7 is done). Base layout with role-based menu, error page that turns rule errors (SQLSTATE 45000) into plain messages. Config from `.env` only.
- **You check:** Follow `app/README.md` (create venv, `pip install -r app/requirements.txt`, run `uvicorn`). Open http://localhost:8000, log in with a demo user, see the menu for that role.
- **Commit:** `P5-1: web skeleton`

Slices below: **Needs** P5-1, Gate 3, the relevant `docs/db_api` notes, and `P4-1` seed data to test. **Parallel with** each other. Each slice owns **only** `app/routes/<name>.py` and `app/templates/<name>/`. Shared files (`base.html`, `db.py`, `auth.py`) belong to P5-1; if a change is needed, write a CHANGE REQUEST.

### P5-2 WEB: reception
- **Create:** `app/routes/reception.py`, `app/templates/reception/*`
- **Do:** Patient search and registration with **duplicate candidates shown before saving** (tier and score visible, receptionist confirms), emergency Unknown Patient, merge screen, appointment booking with live slot grid, reschedule/cancel/no-show, check-in and **queue board** (status per patient), demographic correction (audited).
- **You check:** As receptionist: register "Ramesh Sharma", then "Rameshwar Sarma" same DOB -> candidate list appears; book the same slot twice -> readable error.
- **Commit:** `P5-2: reception screens`

### P5-3 WEB: doctor
- **Create:** `app/routes/doctor.py`, `app/templates/doctor/*`
- **Do:** Doctor's queue; patient summary (allergies, history, reports); the **distraction-free prescribing screen** (type-ahead using `medicine_suggest`, **green/blue/red with text labels**, default dose editable, allergy confirmation dialog showing recorded allergies, "bought outside" marking), new prescription version, lab orders, short-stay orders, reassessment notes, report review, discharge instructions, **paper-style printable prescription**.
- **You check:** As doctor: type "par" -> colored suggestions appear in under a second; add a free-text medicine -> allergy dialog shows the patient's allergies.
- **Commit:** `P5-3: doctor screens`

### P5-4 WEB: pharmacy and nurse
- **Create:** `app/routes/pharmacy.py`, `app/routes/nurse.py`, `app/templates/pharmacy/*`, `app/templates/nurse/*`
- **Do:** Pharmacy: receive stock, dispense (prescription / outside prescription / over-the-counter), FEFO suggestion, expiry alerts, returns, leftover decisions, catalog review queue, counter-sale payment, daily cash handover. Nurse: task list from orders, dose logging (all statuses, reasons), vitals entry with trend, leftover request, family handover, incoming lab report attachment (two-identifier check).
- **You check:** Dispense a medicine -> stock drops in the stock view; nurse logs a dose -> "supplied / given / remaining" updates.
- **Commit:** `P5-4: pharmacy and nurse screens`

### P5-5 WEB: billing and discharge
- **Create:** `app/routes/billing.py`, `app/templates/billing/*`
- **Do:** Bill view (lines, balance), payments (cash/UPI/card/partial), receipts, refund request, discount request, **discharge checklist screen** (live ticks, one-click discharge when ready, partial-payment approval request, emergency override), dues list, daily collection and cash handover receive.
- **You check:** Try discharge with balance -> blocked; request approval -> shows pending; after owner approval (next task) -> discharge works in a couple of clicks.
- **Commit:** `P5-5: billing and discharge screens`

### P5-6 WEB: owner
- **Create:** `app/routes/owner.py`, `app/templates/owner/*`
- **Do:** **Daily summary dashboard** (red/amber/clean), interrupt banner with **acknowledge** button, approvals inbox (approve/reject; works on phone), exception reports, audit log viewer (read-only), price list and clinic settings admin, staff and role admin, catalog review, lab/stock/billing reconciliation pages.
- **You check:** As owner: see seeded alerts; acknowledge one; approve a pending discharge request from a phone-sized browser window.
- **Commit:** `P5-6: owner screens`

### P5-7 WEB: family link pages (public)
- **Create:** `app/routes/family.py`, `app/templates/family/*`
- **Do:** Public routes `/v/<token>`: DOB gate, lockout messages, visit page (prescription, reports, bill, receipts, medicine count "supplied / given / remaining", discharge summary), printed-code fallback, plus a staff page showing the **simulated message outbox** ("sent to 98xxxx"). Responsive for phones. No login.
- **You check:** Open a seeded link in a private window: wrong DOB x5 -> locked; correct DOB -> visit shown; cost price nowhere visible.
- **Commit:** `P5-7: family link pages`

---

# PHASE 6: Proof demos (what you show your teacher)

### P6-1 QA-DEMO: concurrency lab (Module 5)
- **Needs:** Gate 3. **Parallel with:** P6-2.
- **Paste:** CONTEXT + db_api notes + logic files.
- **Create:** `docs/demos/concurrency.md`, `db/tests/concurrency/*.sql`
- **Do:** Step-by-step two-terminal scripts (Session A / Session B, numbered steps, expected output after each step) showing: lost update, dirty read, non-repeatable read, phantom read, deadlock, then the fix with isolation level or locking (`SELECT ... FOR UPDATE`); double-booking of one appointment slot; last strip dispensed twice. Explain with schedules, serial vs serializable schedules, conflict serializability, strict two-phase locking and how InnoDB's MVCC differs.
- **You check:** Follow the document literally in two Git Bash windows ([MYSQL] in each). Outputs match the expected ones.
- **Commit:** `P6-1: concurrency lab`

### P6-2 QA-DEMO: recovery lab (Module 5)
- **Needs:** Gate 3.
- **Create:** `docs/demos/recovery.md`, `tools/backup.sh`, `tools/restore_pitr.sh`
- **Do:** (1) Full backup with `mysqldump --single-transaction --flush-logs --source-data`. (2) Make "oops": delete rows as root at a known time. (3) **Point-in-time recovery** using the binary log up to just before the mistake. (4) Transaction-failure demo: a merge or discharge forced to fail midway, proving **nothing partial** remains. (5) Explain redo/undo logs, checkpoints, and how this maps to our setup. Scripts must work in Git Bash with `docker compose exec`.
- **You check:** Run the document top to bottom; the deleted rows return and row counts match the "before" numbers.
- **Commit:** `P6-2: recovery lab`

### P6-3 QA-DEMO: security demo
- **Needs:** P3-7, P5-1.
- **Create:** `docs/demos/security_demo.md`
- **Do:** Demonstrate: SQL injection payloads (list them) against search boxes fail because of parameterized queries; forbidden actions per role are refused by MySQL itself (from `security_matrix.md`); audit log cannot be altered even by the owner account; family-link lockout; same-login-twice detection. Each demo has exact steps and expected result.
- **You check:** Run each step; results match.
- **Commit:** `P6-3: security demo`

### P6-4 QA-DEMO: "A Day at the Clinic" end-to-end script
- **Needs:** P5-2 to P5-7.
- **Create:** `docs/demos/day_at_the_clinic.md`
- **Do:** A 15-20 step walkthrough using the seed data: who logs in, what they click, the expected screen, and **which rule/syllabus topic each step proves** (e.g. duplicate warning, free-text medicine + allergy dialog, dispense, nurse dose, leftover return, partial-payment approval from phone, owner daily summary, family link, 2-minute discharge). Include a one-page cheat-sheet of demo logins.
- **You check:** Run the whole script once yourself. Fix anything that does not match via the owning WEB/DB-LOGIC task.
- **Gate 4:** `git tag demo-ready`.
- **Commit:** `P6-4: end-to-end demo script (Gate 4)`

---

# PHASE 7: Documents, deployment, release

### P7-1 DOCS: report, viva questions, final diagrams
- **Needs:** Gate 4. **Parallel with:** P7-2, P7-3.
- **Paste:** CONTEXT + all `docs/design/*`, demos, performance notes.
- **Create:** `docs/REPORT.md`, `docs/VIVA_QA.md`, `docs/SYLLABUS_COVERAGE.md`
- **Do:** Project report (problem, requirements, ER, schema, normalization, SQL, triggers/views, security, transactions/recovery, indexing results, screenshots list, limitations, future work). 60 viva questions with short model answers across Modules 1-6, tied to our project. `SYLLABUS_COVERAGE.md`: every syllabus topic -> file/demo that proves it (extend context section 10 with real file links).
- **You check:** Every syllabus topic in your list appears; links point to real files.
- **Commit:** `P7-1: report, viva, coverage`

### P7-2 WEB: free-hosted live demo
- **Needs:** Gate 4. **Parallel with:** P7-1.
- **Create:** `Dockerfile`, `docs/DEPLOY.md`
- **Do:** Use an AI **with web search** to check **current** free tiers (they change often) for hosting a Python app and a MySQL database; recommend one working combination and its limits (sleeping, storage, no binary-log access), then write step-by-step deployment for a beginner, loading **demo seed only** (fake data). State clearly that benchmarks and recovery demos run locally.
- **You check:** Open the public URL, log in with a demo user, run two screens.
- **Commit:** `P7-2: deployment`

### P7-3 DOCS + WEB: Version 2 preview and future notes
- **Needs:** Gate 4. **Parallel with:** P7-1, P7-2.
- **Create:** `docs/FUTURE.md`, `app/routes/v2_preview.py`, `app/templates/v2_preview/*`
- **Do:** A clearly labelled **non-working** "Version 2 preview" (route `/v2-preview`): choose-a-clinic and onboarding screens (clinic name/code, staff, price list, policy settings) that show how a second clinic would start, with no real logic. `FUTURE.md` explains **where things change later**: clinic identity and policy values from `clinic_settings`; each clinic gets its own database with the same schema (no `clinic_id` columns); IDs carry a clinic code; patient sharing across clinics needs consent and a linking layer; chain dashboards read across databases; per-clinic SMS sender, time zone, currency; backups per clinic; same migration scripts for every clinic. Also list Version 1.5 (offline prescription drafts using client UUIDs, append-only events, idempotent writes) and Version 2 (beds, in-house lab, structured results, patient portal, real SMS/WhatsApp).
- **You check:** `/v2-preview` loads and every button is clearly marked "preview".
- **Commit:** `P7-3: v2 preview and future notes`

### P7-4 REVIEWER: traceability audit
- **Needs:** P7-1.
- **Paste:** CONTEXT + design + logic + tests + routes list.
- **Create:** `docs/TRACEABILITY.md`
- **Do:** Matrix: every R-rule -> table(s) -> trigger/procedure/view -> screen -> test. List every rule with a **missing** link as a gap.
- **You check:** Gaps list is short; for each gap, create a fix task for the owning role (add to `PROGRESS.md`) and re-run this review.
- **Commit:** `P7-4: traceability matrix`

### P7-5 You: release
- **Needs:** P7-1 to P7-4.
- **Do:** Polish `README.md` (what it is, screenshots, how to run, links to docs and demos), clean unused files, make sure `.env` is not committed (`git ls-files | grep .env` shows only `.env.example`), tag and publish.
- **You check:** Fresh clone test: `git clone` into a new folder, follow the README, system starts.
- **Release:** `git tag v1.0 && git push --tags`, then create a GitHub Release.
- **Commit:** `P7-5: release v1.0`
