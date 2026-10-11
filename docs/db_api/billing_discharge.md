# DB API: billing_discharge

_Generated from `db/logic/05_billing_discharge.sql` by `tools/gen_db_api_docs.py`. Do not edit by hand._

Own-txn procedures commit by themselves: call them in autocommit mode. Rule violations raise SQLSTATE 45000 with a readable message.

| routine | kind | roles | rules | meaning | parameters |
|---|---|---|---|---|---|
| `ensure_open_bill` | procedure | * | R8.2 | Shared interface: the bill of a visit (created on first use, one per visit). Safe under concurrency (unique visit). Refuses to create a bill for a closed visit | `IN p_visit_id BIGINT UNSIGNED, OUT p_bill_id BIGINT UNSIGNED` |
| `int_bill_add_line` | procedure | * | R8.2,R8.3 | internal: insert one bill line (line_no assigned under the bill lock). The trigger enforces finalization, sign and approval rules | `IN p_bill BIGINT UNSIGNED, IN p_type VARCHAR(30), IN p_price_list BIGINT UNSIGNED, IN p_movement BIGINT UNSIGNED, IN p_lab_order BIGINT UNSIGNED, IN p_episode BIGINT UNSIGNED, IN p_desc VARCHAR(200), IN p_qty DECIMAL(10,2), IN p_unit_price DECIMAL(12,2), IN p_source VARCHAR(15), IN p_manual TINYINT, IN p_expected DECIMAL(12,2), IN p_deviation TINYINT, IN p_reason VARCHAR(300), IN p_reverses BIGINT UNSIGNED, IN p_approval BIGINT UNSIGNED, OUT p_line_id BIGINT UNSIGNED` |
| `bill_add_line` | procedure | * | R8.2 | Shared interface (adapted, CR-B10): add a charge line. source_type: price_list_item (source_id = price_list_id), medicine (stock movement id), lab_test (lab order id), stay (short-stay episode id), manual (needs a reason) | `IN p_bill_id BIGINT UNSIGNED, IN p_source_type VARCHAR(30), IN p_source_id BIGINT UNSIGNED, IN p_description VARCHAR(200), IN p_quantity DECIMAL(10,2), IN p_unit_price DECIMAL(12,2), IN p_reason VARCHAR(200)` |
| `bill_add_credit_line` | procedure | * | R6.3,R8.3 | Shared interface (adapted): credit line for a returned medicine. source_type must be return_credit and source_id the bill line being credited; amount is positive | `IN p_bill_id BIGINT UNSIGNED, IN p_source_type VARCHAR(30), IN p_source_id BIGINT UNSIGNED, IN p_description VARCHAR(200), IN p_amount DECIMAL(12,2), IN p_reason VARCHAR(200)` |
| `int_auto_charge` | procedure | * | R8.1,R8.2 | internal: generate a fixed-price line from the price list (effective today) for an event such as visit start (CONSULT) or stay start (STAY_DAYCARE). No price entry -> a summary alert, no line | `IN p_visit BIGINT UNSIGNED, IN p_item_code VARCHAR(30), IN p_line_type VARCHAR(30), IN p_episode BIGINT UNSIGNED` |
| `sp_set_price` | procedure | OWNER | R8.1 | own txn. Set a price: closes the previous open price of the item the day before and adds the new effective-dated row (owner only, audited) | `IN p_code VARCHAR(30), IN p_description VARCHAR(200), IN p_category VARCHAR(15), IN p_price DECIMAL(12,2), IN p_from DATE, OUT p_price_list_id BIGINT UNSIGNED` |
| `sp_add_price_list_line` | procedure | RECEPTIONIST,DOCTOR,NURSE | R8.1,R8.2 | own txn. Add a fixed-price item (procedure, injection, drip, ...) from the price list effective today | `IN p_visit BIGINT UNSIGNED, IN p_item_code VARCHAR(30), IN p_qty DECIMAL(10,2), OUT p_line_id BIGINT UNSIGNED` |
| `sp_add_manual_line` | procedure | RECEPTIONIST,DOCTOR | R8.1 | own txn. Off-list price with a mandatory reason; appears in the manual-price report | `IN p_visit BIGINT UNSIGNED, IN p_description VARCHAR(200), IN p_qty DECIMAL(10,2), IN p_price DECIMAL(12,2), IN p_reason VARCHAR(300), OUT p_line_id BIGINT UNSIGNED` |
| `sp_request_discount` | procedure | DOCTOR,RECEPTIONIST,ACCOUNTANT | R8.4,G6 | Request a discount or free service (positive amount, reason mandatory). The owner (or a delegate within limit) decides; apply with sp_apply_discount | `IN p_bill BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_kind VARCHAR(12), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `sp_apply_discount` | procedure | DOCTOR,RECEPTIONIST,ACCOUNTANT,OWNER | R8.4,R8.3 | Apply an approved discount/free service as a separate negative line (bill must not be finalized yet; after that use a correction) | `IN p_approval BIGINT UNSIGNED, OUT p_line_id BIGINT UNSIGNED` |
| `sp_request_bill_correction` | procedure | RECEPTIONIST,ACCOUNTANT,DOCTOR | R8.3,G6 | Request a correction to a FINALIZED bill: positive amount = extra charge (debit), negative = credit | `IN p_bill BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `sp_apply_bill_correction` | procedure | RECEPTIONIST,ACCOUNTANT,DOCTOR | R8.3,G6 | Apply an approved correction (credit or debit line) | `IN p_approval BIGINT UNSIGNED, OUT p_line_id BIGINT UNSIGNED` |
| `sp_finalize_bill` | procedure | RECEPTIONIST,ACCOUNTANT | R8.3 | Finalize the bill (Draft -> Finalized). After this only approved corrections can change it | `IN p_bill BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `visit_phone` | function | * | R8.5 | internal: primary phone of the visit's (surviving) patient, or NULL | `p_visit BIGINT UNSIGNED` |
| `sp_record_payment` | procedure | ACCOUNTANT | R8.5 | own txn. Record a payment (cash/upi/card). Amount cannot exceed the balance. Auto receipt SMS (no patient details in the text) | `IN p_bill BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_method VARCHAR(5), OUT p_payment_id BIGINT UNSIGNED, OUT p_receipt VARCHAR(30)` |
| `sp_request_payment_reversal` | procedure | ACCOUNTANT | R8.5,G6 | Request reversal of a payment made by mistake (owner approval needed) | `IN p_payment BIGINT UNSIGNED, IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `sp_reverse_payment` | procedure | ACCOUNTANT | R8.5,G6 | Execute an approved payment reversal as a reversal row (the original stays) | `IN p_approval BIGINT UNSIGNED, OUT p_receipt VARCHAR(30)` |
| `sp_request_refund` | procedure | ACCOUNTANT,RECEPTIONIST | R8.5,G6 | Request a refund (only an overpaid amount can be refunded). Owner approval needed | `IN p_bill BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `sp_execute_refund` | procedure | ACCOUNTANT | R8.5,G6 | Pay out an approved refund (amount = approved amount; receipt generated) | `IN p_approval BIGINT UNSIGNED, IN p_method VARCHAR(5), OUT p_receipt VARCHAR(30)` |
| `sp_request_partial_discharge` | procedure | RECEPTIONIST,ACCOUNTANT,DOCTOR | R9.1,G6 | Ask the owner (or delegate) to allow discharge with a balance. Stores the promised-by date in the request | `IN p_visit BIGINT UNSIGNED, IN p_promised_by DATE, IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `sp_discharge_visit` | procedure | DOCTOR,RECEPTIONIST,ACCOUNTANT | R9.1,R9.2,R9.4,R9.5 | own txn. Discharge in ONE transaction: checks sign-off, doses reconciled (R6.2) and reports reviewed; money gate = balance cleared, or an approved partial-payment request, or a doctor's logged emergency override (money step only). Then: status discharged, stay closed, bill finalized, discharge row, visit closure, due promise, appointment completed | `IN p_visit BIGINT UNSIGNED, IN p_emergency TINYINT, IN p_justification VARCHAR(500), OUT p_discharge_id BIGINT UNSIGNED` |
| `sp_request_visit_reopen` | procedure | DOCTOR,RECEPTIONIST,ACCOUNTANT | R9.4,G6 | Ask the owner to reopen a discharged visit | `IN p_visit BIGINT UNSIGNED, IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `sp_reopen_visit` | procedure | DOCTOR,RECEPTIONIST,ACCOUNTANT,OWNER | R9.4,G6 | Reopen after approval: a reversal row on the closure (the original closure stays) | `IN p_approval BIGINT UNSIGNED` |
| `patient_has_dues` | function | * | R9.3 | 1 if the patient (or the surviving record) has an unpaid promised due | `p_patient BIGINT UNSIGNED` |
| `sp_send_due_reminders` | procedure | ACCOUNTANT,OWNER | R9.3,G10 | Queue (simulated) reminders for dues that reached their promised date; at most one per due per day. Text carries no patient details | `` |

## Views

- `v_bill_summary`
- `v_bill_lines`
- `v_discharge_checklist`
- `v_due_list`
- `v_daily_collection`
- `v_approval_inbox`
