# Track B change-request / decision log

Format: CR-id | affects | problem | what Track B did | needs your decision?

| id | affects | what | status |
|---|---|---|---|
| CR-B1 | ER Staff | ER stores no credentials, but the app needs login. Added extension table `staff_credential` (password hash, excluded from audit JSON). | applied, please confirm |
| CR-B2 | ER MessageOutbox | Added `rendered_text` (safe SMS text incl. family link) and message type `alert_notice`, so the simulated SMS can be shown on screen. | applied, please confirm |
| CR-B3 | docker-compose.yml | Added `--log-bin-trust-function-creators=1` (needed for triggers/functions with binlog ON) and `--event-scheduler=ON`. | applied |
| CR-B4 | context "argon2 or bcrypt" | Used stdlib scrypt (no dependency; seed tool and app share it). Swap later if wanted. | applied |
| CR-B5 | ER status names | Status values are snake_case codes (`checked_in`, `in_consultation`, `no_show`...). Screens map them to display names. | applied |
| CR-B6 | G5 / ER `recorded_by_role` | Stored as the role CODE (VARCHAR(20), FK to `role.role_code`), which satisfies both "FK Role" and the context column name. The trigger validates the hat against `staff_role`. | applied |
| CR-B7 | ER Visit | ER has no visit display code; screens show `V-<visit_id>`. Add a column if you want CLN1-V000045. | open |
| CR-B8 | ER G6 targets | Triggers verify: decision exists, approved, request type matches, approver has authority (owner hat or valid delegation), approver != requester, single use. They do NOT verify that the request's bill_id/visit_id equals the target row's; the procedures (batch 3-4) check that. Direct INSERTs are not granted to any role. | known gap |
| CR-B9 | ER weekday | `weekday` is 0=Monday..6=Sunday (MySQL WEEKDAY()). | applied |
| CR-B10 | TASKS.md shared interfaces | ER has one bill per VISIT and no bill for counter sales (they use `counter_sale_payment/refund`). So `ensure_open_bill(visit_id, OUT bill_id)` has no counter-customer argument, `bill_add_line` takes ER source types, `bill_add_credit_line` only does `return_credit`. Discounts/corrections have their own approval procedures. | applied |
| CR-B11 | ER AlertEvent | Alerts with the same type + source are not repeated inside `alert_dedupe_hours` (setting). | applied |
| CR-B12 | R7.3 / R10.2 | A report with non-matching identifiers is NOT stored. `sp_attach_lab_report` returns a message (no SQL error) so the interrupt alert survives; the app must show `p_message`. | applied |
| CR-B13 | ER Dispensing | The visit of a clinic dispensing is derived (`v_dispensing_visit`); quantities live on stock-out movements. Take-home to the patient himself needs an attendant row ("Patient (self)") because custody needs a recipient. | applied |
| CR-B14 | R9.2 | Emergency override discharge creates a due promise 7 days out (no approver-supplied date exists). Money step only is bypassed; sign-off, dose reconciliation and report review are still required. | applied, please confirm |
| CR-B15 | counter-sale payment reversal | approval type exists but no procedure yet (refund path is done). | open (batch 7) |

## ER open items (from your HANDOFF) and how Track B treated them
- CR-1 (R6.2 wastage): kept the three-term rule; no wastage bucket. Needs your decision.
- CR-2 (merge vs append-only): resolved as redirect: `patient_merge_log` + `canonical_patient()`. Phones/attendants (master rows) are moved. Event rows are never rewritten.
- CR-3 (counter-sale refund): Pharmacy disburses, owner approval mandatory (`counter_sale_refund` request type).
- CR-4 (family recipients): witnessed acknowledgement; witness must differ from giver (trigger).
- CR-5 (order items vs R5.5): stay medicines are issued as a prescription too; `order_item_supply` links the dispensing to the order item.
- N-1 settings: added `handoff_ack_limit_hours`, `lab_price_deviation_pct`, `schedule_horizon_days` to `clinic_settings` seeds.
- N-3: `id_counter` kept (class O, changed only by `next_code()`).

## Known limitations of this batch
- SQL has not been executed on a real MySQL yet (no MySQL in the build environment). `tools/verify_kit.py` covers structure only.
- Consultation / stay charges need price-list items `CONSULT` and `STAY_DAYCARE` (set with `sp_set_price`); without them a summary alert `NO_PRICE_ITEM` is raised and no line is created.
- Tests run as root with `@app_user_id/@app_role` set; the grant layer is exercised by `db/tests/forbidden_actions.sh`.
- Column existence is checked statically for tables and views; semantics (e.g. every join condition) are only proven by running the tests on MySQL.
