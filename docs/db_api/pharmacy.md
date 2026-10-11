# DB API: pharmacy

_Generated from `db/logic/04_pharmacy.sql` by `tools/gen_db_api_docs.py`. Do not edit by hand._

Own-txn procedures commit by themselves: call them in autocommit mode. Rule violations raise SQLSTATE 45000 with a readable message.

| routine | kind | roles | rules | meaning | parameters |
|---|---|---|---|---|---|
| `sp_add_medicine` | procedure | OWNER,PHARMACY | R4.3,R5.5,R6.3 | Add a catalog medicine (master, audited). schedule_class none/H/H1/X; returnable 0/1 | `IN p_brand VARCHAR(120), IN p_generic VARCHAR(160), IN p_strength_value DECIMAL(10,3), IN p_strength_unit VARCHAR(20), IN p_form VARCHAR(40), IN p_stock_unit VARCHAR(20), IN p_schedule VARCHAR(4), IN p_dose VARCHAR(60), IN p_freq VARCHAR(30), IN p_food VARCHAR(60), IN p_days SMALLINT, IN p_returnable TINYINT, OUT p_medicine_id BIGINT UNSIGNED` |
| `sp_add_ingredient` | procedure | OWNER,PHARMACY | R4.4 | Add an active ingredient (master) | `IN p_name VARCHAR(120), OUT p_ingredient_id BIGINT UNSIGNED` |
| `sp_link_medicine_ingredient` | procedure | OWNER,PHARMACY | R4.4 | Link an ingredient to a medicine (combination drugs have several) | `IN p_medicine BIGINT UNSIGNED, IN p_ingredient BIGINT UNSIGNED, IN p_strength_text VARCHAR(60)` |
| `sp_add_supplier` | procedure | OWNER,PHARMACY | R5.1 | Add a supplier (minimal master; optional on receipts) | `IN p_name VARCHAR(120), IN p_phone VARCHAR(20), IN p_contact VARCHAR(80), IN p_notes VARCHAR(300), OUT p_supplier_id BIGINT UNSIGNED` |
| `sp_resolve_catalog_review` | procedure | OWNER,PHARMACY | R4.5 | Resolve a queued free-text name: mapped / added_to_catalog / rejected | `IN p_queue BIGINT UNSIGNED, IN p_action VARCHAR(20), IN p_medicine BIGINT UNSIGNED, IN p_note VARCHAR(300)` |
| `sp_receive_stock` | procedure | PHARMACY | R5.1,R5.2 | own txn. Receive stock: finds or creates the batch (price fields are fixed per batch) and writes a 'received' movement | `IN p_medicine BIGINT UNSIGNED, IN p_supplier BIGINT UNSIGNED, IN p_batch_code VARCHAR(40), IN p_expiry DATE, IN p_qty DECIMAL(10,2), IN p_cost DECIMAL(12,2), IN p_mrp DECIMAL(12,2), OUT p_batch_id BIGINT UNSIGNED` |
| `sp_request_stock_adjustment` | procedure | PHARMACY | R5.3,G6 | Request an adjustment (signed quantity) or an expired write-off (negative). Needs the owner's approval; execute with sp_apply_stock_adjustment | `IN p_batch BIGINT UNSIGNED, IN p_delta DECIMAL(10,2), IN p_kind VARCHAR(12), IN p_reason VARCHAR(300), OUT p_approval_id BIGINT UNSIGNED` |
| `sp_apply_stock_adjustment` | procedure | PHARMACY | R5.3,G6 | Apply an approved adjustment/write-off to the ledger (one movement per approval) | `IN p_approval BIGINT UNSIGNED` |
| `sp_reverse_stock_movement` | procedure | PHARMACY | R5.2 | Reverse a mistaken 'received' movement (reason mandatory, no approval per N-5). One reversal per movement | `IN p_movement BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `sp_return_to_supplier` | procedure | PHARMACY | R5.2 | Return stock to the supplier (negative movement, reason mandatory) | `IN p_batch BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_reason VARCHAR(300)` |
| `int_check_batch_expiry` | procedure | * | R5.4,R10.2 | internal pre-check: a named expired batch raises an interrupt alert (persisted: call this OUTSIDE a transaction) and refuses | `IN p_batch BIGINT UNSIGNED` |
| `int_dispense_core` | procedure | * | R5.2,R5.4,R5.5,R8.2 | internal (inside a transaction): FEFO allocation over non-expired batches with row locks, one stock-out movement per batch used (price snapshot = batch MRP); adds a bill medicine line when p_visit is given | `IN p_source VARCHAR(25), IN p_medicine BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_batch BIGINT UNSIGNED, IN p_item BIGINT UNSIGNED, IN p_outside BIGINT UNSIGNED, IN p_sale BIGINT UNSIGNED, IN p_visit BIGINT UNSIGNED, OUT p_dispensing_id BIGINT UNSIGNED` |
| `sp_dispense_clinic_prescription` | procedure | PHARMACY | R5.5,R2.5,R6.2,R8.2,R5.2 | own txn. Dispense a clinic prescription item (latest version, catalog medicine only). FEFO unless a batch is named. Creates stock-out movements + bill lines + the custody handoff to a nurse (staff) or attendant. p_order_item links the supply to a short-stay order item (R6.2) | `IN p_item BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_batch BIGINT UNSIGNED, IN p_order_item BIGINT UNSIGNED, IN p_recipient_staff BIGINT UNSIGNED, IN p_recipient_attendant BIGINT UNSIGNED, IN p_purpose VARCHAR(20), OUT p_dispensing_id BIGINT UNSIGNED` |
| `sp_register_counter_customer` | procedure | PHARMACY,RECEPTIONIST | R5.6 | Register a counter buyer (not a patient). A temporary code is always generated | `IN p_name VARCHAR(120), IN p_phone VARCHAR(20), OUT p_customer_id BIGINT UNSIGNED, OUT p_code VARCHAR(30)` |
| `sp_link_counter_customer` | procedure | PHARMACY,RECEPTIONIST,OWNER | R5.6,G12 | Human-confirmed, audited link of a counter customer to a patient | `IN p_customer BIGINT UNSIGNED, IN p_patient BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `sp_start_counter_sale` | procedure | PHARMACY | R5.5,R5.7 | Start a counter sale (header) for a counter customer | `IN p_customer BIGINT UNSIGNED, OUT p_sale_id BIGINT UNSIGNED, OUT p_sale_number VARCHAR(30)` |
| `sp_record_outside_prescription` | procedure | PHARMACY | R5.5,R5.8 | Record a prescription written elsewhere (prescriber name, registration no., optional photo path) for a counter customer | `IN p_customer BIGINT UNSIGNED, IN p_prescriber VARCHAR(120), IN p_reg_no VARCHAR(40), IN p_photo VARCHAR(300), IN p_date DATE, OUT p_outside_id BIGINT UNSIGNED` |
| `sp_dispense_otc` | procedure | PHARMACY | R5.5,R5.8 | own txn. Over-the-counter sale. Schedule H/H1/X is refused by the database | `IN p_sale BIGINT UNSIGNED, IN p_medicine BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_batch BIGINT UNSIGNED, OUT p_dispensing_id BIGINT UNSIGNED` |
| `sp_dispense_outside_rx` | procedure | PHARMACY | R5.5,R5.8 | own txn. Sale against an outside prescription. Scheduled medicines need the prescriber's registration number; a missing photo is flagged for the owner | `IN p_sale BIGINT UNSIGNED, IN p_outside BIGINT UNSIGNED, IN p_medicine BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_batch BIGINT UNSIGNED, OUT p_dispensing_id BIGINT UNSIGNED` |
| `sp_counter_sale_payment` | procedure | PHARMACY | R5.7,R8.5 | own txn. Record a payment for a counter sale (amount cannot exceed the balance). Auto receipt SMS if the buyer has a phone | `IN p_sale BIGINT UNSIGNED, IN p_amount DECIMAL(12,2), IN p_method VARCHAR(5), OUT p_payment_id BIGINT UNSIGNED, OUT p_receipt VARCHAR(30)` |
| `sp_counter_sale_return` | procedure | PHARMACY | R5.7,G6 | own txn. Return medicine from a counter sale: stock comes back (returned_by_patient), a return row is written and a refund approval request is raised for the owner | `IN p_sale BIGINT UNSIGNED, IN p_dispensed_movement BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_reason VARCHAR(300), OUT p_return_id BIGINT UNSIGNED, OUT p_approval_id BIGINT UNSIGNED` |
| `sp_execute_counter_refund` | procedure | PHARMACY | R5.7,G6 | Pay out an approved counter-sale refund (amount = the approved amount; receipt generated) | `IN p_approval BIGINT UNSIGNED, IN p_method VARCHAR(5), OUT p_receipt VARCHAR(30)` |
| `dispensing_remaining` | function | * | R6.2 | internal: units of a dispensing still with the clinic = dispensed - consumed - returned - taken home/handed to family | `p_dispensing BIGINT UNSIGNED` |
| `supply_remaining` | function | * | R6.1 | internal: units left on one order-item supply (allocated - consumed by doses that used it) | `p_supply BIGINT UNSIGNED` |
| `sp_handoff_custody` | procedure | PHARMACY,NURSE | R2.5,R6.2 | Hand medicine to a nurse/attendant (supply_to_nurse or take_home). The giver is the logged-in person (G1); the recipient acknowledges separately | `IN p_dispensing BIGINT UNSIGNED, IN p_recipient_staff BIGINT UNSIGNED, IN p_recipient_attendant BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_purpose VARCHAR(20), OUT p_handoff_id BIGINT UNSIGNED` |
| `sp_acknowledge_handoff` | procedure | NURSE,PHARMACY,DOCTOR,RECEPTIONIST | R2.5,R6.2,G1 | Recipient (or a staff witness for an attendant) acknowledges a handoff. The giver cannot acknowledge his own handoff | `IN p_handoff BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_method VARCHAR(25), IN p_note VARCHAR(300)` |
| `sp_administer_dose` | procedure | NURSE | R6.1,R6.2,R10.2 | own txn. Record one dose event. Ordered values are copied from the order item; actual values may differ only with a reason (above the order also needs a doctor as authorizer and raises an interrupt). Given doses consume units from a dispensed supply | `IN p_item BIGINT UNSIGNED, IN p_status VARCHAR(10), IN p_supply BIGINT UNSIGNED, IN p_consumed DECIMAL(10,2), IN p_actual_value DECIMAL(10,3), IN p_actual_unit VARCHAR(20), IN p_actual_route VARCHAR(30), IN p_reason VARCHAR(300), IN p_authorized_by BIGINT UNSIGNED, IN p_corrects BIGINT UNSIGNED, OUT p_dose_id BIGINT UNSIGNED` |
| `sp_request_leftover_return` | procedure | NURSE | R6.3 | Ask pharmacy to take leftover medicine back (quantity cannot exceed what is left of the dispensing) | `IN p_dispensing BIGINT UNSIGNED, IN p_qty DECIMAL(10,2), IN p_note VARCHAR(300), OUT p_request_id BIGINT UNSIGNED` |
| `sp_decide_leftover` | procedure | PHARMACY | R6.3,R8.3,G6 | own txn. Pharmacist decides a leftover request. ACCEPT (returnable medicine, batch not expired): stock comes back, a return_credit bill line is added, and a refund approval is requested if the bill is already overpaid. REFUSE: hands the units to an attendant (p_attendant required) | `IN p_request BIGINT UNSIGNED, IN p_decision VARCHAR(10), IN p_reason VARCHAR(300), IN p_attendant BIGINT UNSIGNED, OUT p_decision_id BIGINT UNSIGNED` |
| `sp_declare_cash_handover` | procedure | PHARMACY | R5.7 | Declare cash being handed to the accountant for a period. A difference from the system's expected cash is flagged for the owner | `IN p_start DATETIME, IN p_end DATETIME, IN p_declared DECIMAL(12,2), IN p_note VARCHAR(300), OUT p_declaration_id BIGINT UNSIGNED` |
| `sp_count_cash_handover` | procedure | ACCOUNTANT | R5.7 | Count the handed-over cash (must be a different person from the declarer). A difference raises an alert | `IN p_declaration BIGINT UNSIGNED, IN p_counted DECIMAL(12,2), IN p_note VARCHAR(300)` |

## Views

- `v_stock_by_batch`
- `v_stock_batch_nocost`
- `v_stock_by_medicine`
- `v_expiring_soon`
- `v_fefo_batches`
- `v_restock_demand`
- `v_dispensing_visit`
- `v_dispensing_custody`
- `v_dispense_reconciliation`
- `v_unacknowledged_handoffs`
- `v_visit_medicine_count`
- `v_counter_sale_summary`
