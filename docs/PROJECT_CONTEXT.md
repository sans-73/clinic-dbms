# PROJECT_CONTEXT: Clinic Management System (DBMS Project)

> **Paste this whole file at the start of every AI session**, then paste the one task card you want done.
> Authority: this file overrides anything an AI "remembers". If an AI wants to change a rule here, it must write a **CHANGE REQUEST** (see `00_START_HERE.md`) instead of silently changing it.
> Current progress lives in `PROGRESS.md`, not here.

---

## 1. The project in one paragraph
A web-based management system for a **single small clinic in India** (1-3 doctors, 4-6 staff). Version 1 supports **OPD consultations plus short-stay/day-care treatment (max ~24 h)**. It is a DBMS course project that must apply the whole syllabus (Modules 1-6) at a non-beginner level, and be polished enough to sell later. The heart of the product is **trust**: strict separation of duties, no silent edits, a complete chain of accountability for medicine and money, and automatic exception reports for the owner.

## 2. Scope
**In Version 1:** patient registry, staff and roles, appointments/visits/queue, short-stay episodes with vitals, prescriptions and orders, pharmacy stock and dispensing (incl. counter sales), nurse dose administration, outside-lab order/report tracking, billing and payments, discharge, owner audit and alert reports, read-only family links, backup/recovery, performance benchmarks.
**Not in Version 1 (do not build):** bed/room/ward management, in-house lab processing (no lab technician role), supplier accounting/GST on purchases, structured lab result values, offline mode, patient login portal, multi-clinic logic, desktop app, real SMS/WhatsApp sending, biometrics.

## 3. Stack and environment
- **Database:** MySQL 8.4 (InnoDB, utf8mb4), run locally via Docker (`docker-compose.yml`). Binary log ON (needed for point-in-time recovery demo).
- **Backend:** Python 3.11+ with FastAPI. **Screens:** server-rendered HTML (Jinja2) + light JavaScript (HTMX). No React.
- **Principle:** business rules live **in MySQL** (triggers, procedures, views, grants). The web layer is thin.
- **Two environments:** local Docker (full system, 100k-row benchmarks, recovery demo) and a free hosted tier (live demo with fake sample data; provider chosen at deployment time).
- **SMS/WhatsApp:** simulated. Every message is written to a `message_outbox` table and shown on screen.
- **C++:** optional bonus only (B+ tree / extendible hashing simulator).
- **Repo layout:** `docs/`, `db/schema`, `db/logic`, `db/security`, `db/seed`, `db/queries`, `db/tests`, `app/`, `tools/`, `bonus/`.

## 4. Golden principles (never break these)
- **G1 Separation of duties.** The person who performs an action records it; nobody can rewrite another role's record.
- **G2 Two kinds of tables.** *Master tables* (patient demographics, staff, catalog, price list) are editable but every edit is audit-logged with old and new values. *Event tables* (prescriptions, dispensing, doses, payments, stock movements, status history, vitals, lab reports, approvals, audit) are **append-only**: `UPDATE`/`DELETE` blocked by trigger **and** not granted. A correction is a **new row** pointing to the one it replaces (`corrects_id`) or a **reversal row**.
- **G3 Derived, never stored.** Stock quantity, bill balance, current visit status, medicine remaining, discharge checklist are **computed** (views/queries). No editable counters.
- **G4 Three enforcement layers.** Database (grants, views, triggers, procedures) + application role checks + audit log.
- **G5 Who did it.** The app sets session variables `@app_user_id` and `@app_role` on every database connection it uses. Insert triggers on event tables fill `recorded_by` / `recorded_by_role` from them and **refuse the write if they are NULL**.
- **G6 Maker-checker.** Refunds, discounts, free services, stock adjustments/write-offs, corrections to finalized records and partial-payment discharge need a **second, different person** to approve. Never self-approved. Request and approval are both recorded.
- **G7 Time.** Event tables have `occurred_at` (what the user says) and `recorded_at` (server time, cannot be set by the user). Entries made after a downtime carry `is_backfilled = 1`. Store UTC.
- **G8 Money.** `DECIMAL(12,2)` only. Bill lines snapshot the price at creation.
- **G9 No hardcoding per clinic.** Clinic name, policy numbers and limits live in `clinic_settings`. IDs carry a clinic code (e.g. `CLN1-000123`). **No `clinic_id` columns.**
- **G10 Privacy.** Fake data only. Aadhaar optional, last 4 digits only. No biometrics. SMS text never contains patient details. Cost price visible only to owner and pharmacy.
- **G11 Idempotent writes.** Every event table has `request_uuid` (unique) so a repeated submit creates one row.
- **G12 Human in the loop.** Identity matching suggests candidates; a person confirms. Never auto-merge.

## 5. Roles (8) and the core permission idea
| Role | Does | Must NOT |
|---|---|---|
| Owner/Admin | Oversight, approvals, price list, staff accounts, audit and exception reports | Edit clinical notes, doses given, finalized reports, completed payments directly (approves corrections instead) |
| Doctor | Consultation, diagnosis, prescriptions, orders, lab requests, review reports, discharge instructions | Change payments, stock, dispensed or administered records, appointment history |
| Receptionist | Patient registration/search, appointments, check-in, queue, demographic corrections (audited), basic bill creation | Touch clinical data or payments |
| Nurse | See finalized orders, record doses (given/missed/refused/delayed/stopped), vitals, observations, ask pharmacy about leftovers, attach incoming lab reports | Edit prescriptions, billing, stock |
| Pharmacy | Receive stock, dispense, record returns, counter sales (and payments for counter sales only), daily cash handover | Change prescriptions, diagnosis, other payments; delete dispensing records |
| Accountant | Record payments, receipts, partial payments, approved refunds, reconcile daily collections | Clinical records, prescriptions |
| Patient/Family | **Read-only tokenized link** (no login in V1) | Any edit |
| *(Lab technician)* | **Deferred to Version 2** (V1 uses outside labs) | n/a |

One person can hold several roles; every action records which role ("hat") was used.

## 6. Business rules by feature (R-numbers are used for traceability)

### F1 Patient registry and identity
- **R1.1** System-generated `patient_id`; display code `CLN1-000123`. Phone is never an identifier.
- **R1.2** Phone numbers (with history), allergies, attendants/emergency contacts are **separate tables** (1NF). Address is composite (permanent and temporary).
- **R1.3** Aadhaar optional, **last 4 digits only**. No fingerprints. Blood group is clinical, not identity.
- **R1.4** Duplicate detection in tiers: T1 phone + similar name; T2 name + DOB; T3 DOB + address (phone changed); T4 Aadhaar last-4/ID as tie-breaker. Fuzzy name match = SOUNDEX/prefix pre-filter + a Levenshtein stored function. Receptionist sees ranked candidates and confirms.
- **R1.5** Emergency "Unknown Patient": temp code `TMP-00123`, only gender + approximate age. Later an **audited merge** (single transaction moves every linked row, writes a merge log).
- **R1.6** Demographic edits are audited (old/new). Clinical corrections are amendments, never overwrites.

### F2 Staff and access
- **R2.1** Staff specialization: Staff -> Doctor / Nurse / Pharmacist / Accountant / Receptionist details; roles via a `staff_role` link (overlapping membership allowed).
- **R2.2** One MySQL account per role group; app picks it from the logged-in role. Role-specific **views** hide columns (e.g. reception view has no diagnosis; cost price hidden).
- **R2.3** Maker-checker (G6) tables: `approval_request` + approval record.
- **R2.4** Anti-leakage by reconciliation: visits vs bills, stock out vs dispensing, daily cash close, auto receipts to patient.
- **R2.5** Medicine custody chain: prescribed -> dispensed -> handed to nurse/family -> each dose logged -> family sees "10 supplied, 2 given, 8 remaining".

### F3 Appointments, visits, flow
- **R3.1** **Visit** is the parent of everything. A visit starts as OPD and may gain one **Short-Stay Episode** (own start/end) without creating a new visit.
- **R3.2** Appointment is optional (walk-ins, emergencies). Statuses: Booked, Checked-in, Waiting, In consultation, Completed, Cancelled, No-show.
- **R3.3** `visit_status_history` is append-only (visit, status, timestamp, user, reason); current status = latest row. Flow: Arrived, Waiting, With Doctor, Lab, Pharmacy, Billing, Discharged. Location is only a status like "Observation Area". **No beds.**
- **R3.4** Stay over 24 h (clinic setting) is flagged to the owner; extension needs an approved reason.
- **R3.5** Vitals/observations (BP, pulse, temperature, SpO2...) are append-only rows with the recording nurse.
- **R3.6** Double-booking a doctor's slot is impossible at DB level (unique rule on active bookings + locking).
- **R3.7** Schedule changes: a doctor *requests*, an authorized person *applies*; old timing stays traceable.

### F4 Prescriptions and orders
- **R4.1** Prescription = header + items (header/detail). Changes create a **new version** row; old stays.
- **R4.2** Doctor may type **free text** medicines. Type-ahead shows catalog matches: **green = in stock, blue = in catalog but out of stock, red = not in catalog**, each with a **text label** ("In stock: 40").
- **R4.3** Catalog entries carry default dose/schedule (Indian notation like 1-0-1, after food, 5 days). Doctor can edit; the prescription stores what the doctor chose.
- **R4.4** Free-text/outside medicine triggers an **allergy confirmation** that shows the patient's recorded allergies; confirmation is logged. For catalog medicines, an automatic generic-name allergy warning also applies; overriding it is an interrupt event (F10).
- **R4.5** Free-text names go to a **catalog review queue**; owner or pharmacy maps or adds them later.
- **R4.6** Item status "bought outside" feeds a restock-demand list. "Outside" on an item that was **in stock** is flagged.
- **R4.7** Printable paper-style prescription. Short-stay patients get **orders** the nurse acts on (drug, dose, route, time, repeat interval).

### F5 Pharmacy and inventory
- **R5.1** Stock received = medicine, batch, expiry, quantity, cost price, MRP (selling price), receiver, timestamp. Supplier is a minimal table (name, phone, contact, notes) and **optional** on a receipt.
- **R5.2** `stock_movement` is an append-only ledger (received, dispensed, returned_by_patient, returned_to_supplier, expired_writeoff, adjustment, reversal). Current stock = SUM per batch. Errors fixed by reversal rows.
- **R5.3** Adjustments/write-offs need owner approval + mandatory reason.
- **R5.4** Expired batches cannot be dispensed (trigger). Suggest **FEFO** (first-expiry-first-out). Batches within 60 days of expiry (setting) go on an owner list with supplier phone.
- **R5.5** Dispensing has exactly one source: clinic prescription item, **outside prescription** (doctor name, registration no., optional photo path), or **over-the-counter**. Schedule H/H1/X catalog items **cannot** be sold OTC (DB-enforced).
- **R5.6** Counter buyers live in `counter_customer` (phone or temp ID), not `patient`. Optional audited link if they later become a patient.
- **R5.7** Pharmacy may record payments for **counter sales only**; auto receipt if phone given. **Daily cash handover** pharmacy -> accountant; differences flagged to owner.
- **R5.8** Owner reports watch OTC volume, per-pharmacist patterns, weak prescription details on flagged drugs.

### F6 Nurse administration and leftovers
- **R6.1** Each dose is an append-only event: status (given/missed/refused/delayed/stopped), dose, route, time, nurse, reason. If different from the order, store **ordered and actual** values plus reason and responsible person.
- **R6.2** Leftover rule: **dispensed = administered + returned to stock + handed to family**. Any mismatch **blocks the discharge checklist**.
- **R6.3** Nurse asks pharmacy whether leftover is acceptable (catalog `returnable` flag + condition rules; pharmacist decides, reason recorded). Accepted: a `returned_by_patient` stock movement + a **negative bill line**, plus a refund request if already paid. Refused: a logged handover to family.
- **R6.4** Owner reports watch return patterns.

### F7 Laboratory (outside labs only)
- **R7.1** Clinic orders tests, outside lab processes them, clinic receives reports. Minimal `outside_lab` table (name, phone, contact, notes).
- **R7.2** `lab_order` append-only status trail: Ordered, Sample collected, Sent to lab, Report received, Reviewed by doctor.
- **R7.3** Incoming report stored as received with a **hash**, never edited; correction = new version. Attaching requires confirming **two identifiers** against the order (logged). Reports brought by patients are tagged `brought_by_patient`.
- **R7.4** "Doctor reviewed" is its own event; unreviewed reports past a time limit raise alerts.
- **R7.5** Reconciliations: ordered-not-billed; billed-no-report after N days.
- **R7.6** Structured result values are deferred (leave a design hook only).

### F8 Billing and payments
- **R8.1** Owner-only `price_list` with effective dates for fixed items (consultation, stay/hospitality, procedures, injections, drips). Variable items: medicine price = batch MRP x quantity; outside lab price entered per test with a **deviation flag**; any other off-list price needs a reason and appears in a manual-price report.
- **R8.2** Bill lines are generated from events (dispense, lab order, stay start), not typed. Each line snapshots price.
- **R8.3** Bill states Draft -> Finalized -> Settled. After finalization only **credit/debit lines** with reason, never edits.
- **R8.4** Discounts and free services need owner (or doctor authorized up to a limit) approval; stored as separate negative lines with reason + approver.
- **R8.5** Payments append-only (cash/UPI/card/partial); balance = bill total - payments + refunds, **computed**. Mistakes reversed by reversal rows. Auto receipt on every payment. Refunds need owner approval.

### F9 Discharge
- **R9.1** Default: discharge needs the bill settled. Exception: owner (or delegate within limit) approves partial payment; record has approver, paid, due, reason, **promised-by date**; remote approval from a phone; never self-approved.
- **R9.2** Clinical emergency can bypass the money step via a **logged override**. Medical documents are never withheld.
- **R9.3** Dues list with reminders; visible flag at next visit.
- **R9.4** Checklist computed live: clinical sign-off, doses reconciled (R6.2), lab reports reviewed, bill finalized, balance zero or approved. Discharge closes visit, stay and bill in **one transaction**.
- **R9.5** Goal: discharge in about 2 minutes from any authorized device.

### F10 Audit and anti-leakage
- **R10.1** Owner's **daily summary**: ranked red (needs decision) / amber (unusual pattern) / clean checks.
- **R10.2** **Immediate interrupts only for patient-safety events:** dispensing mismatch; dose above order; allergy-warning override; report attached to wrong patient; critical report unreviewed past limit; stay over 24 h; clinical emergency override; attempt to dispense expired batch.
- **R10.3** Money events (large refund, discount, write-off, manual price) wait for the daily summary. **Approval requests** notify the owner/delegate immediately (someone is waiting).
- **R10.4** Interrupts are tracked to **acknowledgement** and escalate to a backup person if unanswered.
- **R10.5** Weekly and monthly pattern reports (discounts, returns, counter sales, edits after payment, backdating, shared logins).
- **R10.6** Audit tables are insert-only for **everyone including the owner**.

### F11 Patient and family links
- **R11.1** Read-only link = unguessable random token + patient **DOB** as second factor. 5 failed attempts lock with escalating delay; all attempts logged.
- **R11.2** Link scoped to **one visit/stay**, expires (setting, default 30 days), revocable instantly.
- **R11.3** Hides cost prices, internal notes, and clinical notes marked not-for-sharing. SMS text: only "Your visit details from [Clinic]: link".
- **R11.4** Every opening is logged. Patients with no real DOB (emergency) use a printed code until merged. The attendant receiving the link is recorded (consent).

### F12 Backup, recovery, performance
- **R12.1** Cloud-hosted, **online-only in V1**. Outages: paper fallback form + backfilled entries (G7). Mobile hotspot as backup link.
- **R12.2** Daily encrypted full backups + binary log for **point-in-time recovery**; off-site copy; a tested restore drill.
- **R12.3** Performance plan: 100k+ synthetic rows, `EXPLAIN` before/after indexing, FULLTEXT for medicine type-ahead, date partitioning for large event tables, graphs.
- **R12.4** No hard deletes. Retention period is a setting, to be confirmed before selling.
- **R12.5** Future: offline prescription drafts (V1.5) built on client UUIDs, append-only events, idempotent writes.

### Scope and growth
- **R13.1** Single clinic now. Future multi-clinic = **one database per clinic** (same schema). Settings table + clinic code in IDs keep this possible. A non-working "Version 2 preview" onboarding screen is a late demo item.

## 7. Policy values (live in `clinic_settings`, never in code)
`stay_limit_hours=24`, `expiry_alert_days=60`, `link_expiry_days=30`, `link_max_failed_attempts=5`, `doctor_discount_limit`, `owner_ack_timeout_minutes`, `report_review_limit_hours`, `billed_no_report_days`, `clinic_name`, `clinic_code` (e.g. CLN1), `timezone`, `currency`.

## 8. Draft entity list (the Architect finalizes names in `docs/design/`)
Patient, PatientPhone, PatientAllergy, PatientAttendant, CounterCustomer, PatientMergeLog, Staff, Role, StaffRole, Doctor/Nurse/etc. detail tables, Appointment, ScheduleChange, Visit, VisitStatusHistory, ShortStayEpisode, VitalsObservation, Medicine (catalog), Supplier, StockBatch, StockMovement, Prescription, PrescriptionItem, CatalogReviewQueue, OutsidePrescription, Dispensing, DoseAdministration, LeftoverHandling, OutsideLab, LabOrder, LabOrderStatus, LabReport, PriceList, Bill, BillLine, Payment, Refund, ApprovalRequest, Discharge, Due, CashHandover, MessageOutbox, FamilyLink, LinkAccessLog, AlertEvent, AuditLog, ClinicSettings, IdCounter.

## 9. Conventions for all AIs
- **SQL:** MySQL 8.4 syntax, InnoDB, utf8mb4. Tables and columns `snake_case`, tables **singular**. PK `<table>_id BIGINT UNSIGNED AUTO_INCREMENT`. FKs named `fk_<child>_<parent>`. Domain rules as `CHECK` constraints. Every table has a `COMMENT`. Files start with `USE clinic_db;`.
- **Event table standard columns:** `occurred_at`, `recorded_at`, `recorded_by`, `recorded_by_role`, `is_backfilled`, `request_uuid`, and `corrects_id` where corrections apply.
- **Cross-file foreign keys:** a FK that points to a table created in a *later* file goes in `db/schema/09_foreign_keys.sql`.
- **Python:** type hints, parameterized queries only (no string-built SQL), config from environment, small files.
- **Every AI deliverable:** complete files with exact paths, no `...` placeholders, plus a HANDOFF NOTE.

## 10. Syllabus coverage map (the proof for your teacher)
| Module | Topic | Where it is shown |
|---|---|---|
| 1 | History, file vs DBMS, architecture, data independence, data models comparison, DBA, users, languages | `docs/theory/module1_notes.md`; DDL/DML/DCL scripts; roles/grants demo |
| 1 | Protection, security | Roles, views, grants, audit, injection test |
| 2 | ER, Extended ER (specialization, weak entities), large-enterprise design, ER to tables | `docs/design/01_er_model.md`, `02_table_design.md` |
| 3 | Constraints, relational algebra and calculus, SQL (joins, aggregates, GROUP BY/HAVING, EXISTS, set ops, division, nested), views, triggers, query optimization | `db/queries/`, `docs/queries_theory.md`, `db/logic/`, EXPLAIN reports |
| 4 | FDs, decomposition, lossless join, dependency preservation, NULL problems, dangling tuples, 1NF-5NF | `docs/design/03_normalization.md` |
| 5 | Transactions, schedules, failure, recovery, concurrency, locking protocols | `docs/demos/concurrency.md`, `docs/demos/recovery.md`, merge/discharge/dispense transactions |
| 6 | File organization, primary/secondary/multi-level indexes, hash and dynamic hashing, B+ trees | `docs/performance/`, `bonus/` simulator |
