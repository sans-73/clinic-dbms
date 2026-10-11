# DB API: prescriptions_labs

_Generated from `db/logic/03_prescriptions_labs.sql` by `tools/gen_db_api_docs.py`. Do not edit by hand._

Own-txn procedures commit by themselves: call them in autocommit mode. Rule violations raise SQLSTATE 45000 with a readable message.

| routine | kind | roles | rules | meaning | parameters |
|---|---|---|---|---|---|
| `sp_add_patient_allergy` | procedure | DOCTOR,NURSE | R1.2,R4.4 | Record an allergy (by ingredient and/or free text). Corrections are new rows; status changes use sp_set_allergy_status | `IN p_patient BIGINT UNSIGNED, IN p_ingredient BIGINT UNSIGNED, IN p_text VARCHAR(100), IN p_reaction VARCHAR(200), IN p_severity VARCHAR(10), OUT p_allergy_id BIGINT UNSIGNED` |
| `sp_set_allergy_status` | procedure | DOCTOR,NURSE | R4.4 | Mark an allergy confirmed / inactive / reactivated / entered_in_error (latest status wins) | `IN p_allergy BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300)` |
| `sp_record_blood_group` | procedure | DOCTOR,NURSE | R1.3 | Record blood group (clinical, not identity). A new row corrects the previous one | `IN p_patient BIGINT UNSIGNED, IN p_abo VARCHAR(2), IN p_rh VARCHAR(8), IN p_source VARCHAR(20), IN p_lab_report BIGINT UNSIGNED` |
| `allergy_snapshot` | function | * | R4.4 | Allergies currently in force for a patient as text (or 'No allergies recorded') | `p_patient BIGINT UNSIGNED` |
| `fn_allergy_conflict` | function | * | R4.4 | Names of recorded allergies that clash with a catalog medicine (by ingredient or generic/brand text), or NULL | `p_patient BIGINT UNSIGNED, p_medicine BIGINT UNSIGNED` |
| `sp_add_clinical_note` | procedure | DOCTOR | R1.6,R11.3 | Clinical note. sharing defaults to not_for_sharing. Correction = new row with corrects_id (amendment) | `IN p_visit BIGINT UNSIGNED, IN p_text TEXT, IN p_sharing VARCHAR(20), IN p_corrects BIGINT UNSIGNED, OUT p_note_id BIGINT UNSIGNED` |
| `sp_add_diagnosis` | procedure | DOCTOR | R1.6,R2.2 | Diagnosis (hidden from reception views). Correction = new row with corrects_id | `IN p_visit BIGINT UNSIGNED, IN p_text VARCHAR(300), IN p_code VARCHAR(20), IN p_corrects BIGINT UNSIGNED, OUT p_id BIGINT UNSIGNED` |
| `sp_clinical_signoff` | procedure | DOCTOR | R9.4 | Doctor clinical sign-off with discharge instructions (needed before discharge). A later sign-off corrects the earlier one | `IN p_visit BIGINT UNSIGNED, IN p_instructions TEXT, OUT p_signoff_id BIGINT UNSIGNED` |
| `sp_create_prescription` | procedure | DOCTOR | R4.1 | own txn. Create the prescription root and its version 1 (empty) for a visit (one per visit; later changes are new versions) | `IN p_visit BIGINT UNSIGNED, OUT p_prescription_id BIGINT UNSIGNED, OUT p_version_id BIGINT UNSIGNED` |
| `sp_prescribe_item` | procedure | DOCTOR | R4.1,R4.2,R4.3,R4.4,R4.5,R10.2 | own txn. Add a medicine line to the latest version. Catalog medicine: dose/schedule default from the catalog and can be overridden; allergy clash needs p_override_reason (interrupt alert). Free text: needs p_confirm_allergies=1 and goes to the catalog review queue | `IN p_version BIGINT UNSIGNED, IN p_medicine BIGINT UNSIGNED, IN p_free_text VARCHAR(150), IN p_dose VARCHAR(60), IN p_schedule VARCHAR(30), IN p_food VARCHAR(60), IN p_days SMALLINT, IN p_qty DECIMAL(10,2), IN p_confirm_allergies TINYINT, IN p_override_reason VARCHAR(300), OUT p_item_id BIGINT UNSIGNED, OUT p_line_id BIGINT UNSIGNED` |
| `sp_revise_prescription` | procedure | DOCTOR | R4.1 | own txn. New prescription version: all lines of the previous version are copied (unchanged) except those listed as discontinued (comma list of line ids, no spaces) | `IN p_prescription BIGINT UNSIGNED, IN p_reason VARCHAR(300), IN p_discontinue_lines VARCHAR(200), OUT p_version_id BIGINT UNSIGNED` |
| `sp_set_line_status` | procedure | DOCTOR,PHARMACY | R4.6 | Line status: bought_outside / not_needed / cancelled / prescribed. 'bought_outside' on a medicine that is in stock is flagged for the owner | `IN p_line BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300)` |
| `sp_medicine_suggest` | procedure | DOCTOR,PHARMACY,NURSE | R4.2 | Type-ahead: catalog matches with state green (in stock) / blue (catalog, out of stock) + text label, default dose, allergy warning flag; last row is red = not in catalog (free text) | `IN p_prefix VARCHAR(60), IN p_patient BIGINT UNSIGNED` |
| `sp_create_clinical_order` | procedure | DOCTOR | R4.7 | own txn. New order (header + status ordered). Items are added with sp_add_order_item; sp_finalize_order releases it to nurses | `IN p_visit BIGINT UNSIGNED, IN p_episode BIGINT UNSIGNED, IN p_type VARCHAR(15), OUT p_order_id BIGINT UNSIGNED` |
| `sp_add_order_item` | procedure | DOCTOR | R4.7,R4.4 | own txn. Add an item (drug, dose, route, first time, repeat interval) to an order that is not finalized yet. A catalog drug that clashes with an allergy needs p_override_reason (interrupt alert) | `IN p_order BIGINT UNSIGNED, IN p_kind VARCHAR(15), IN p_medicine BIGINT UNSIGNED, IN p_free_text VARCHAR(150), IN p_price_list BIGINT UNSIGNED, IN p_dose_value DECIMAL(10,3), IN p_dose_unit VARCHAR(20), IN p_route VARCHAR(30), IN p_first_at DATETIME, IN p_interval_min INT, IN p_qty DECIMAL(10,2), IN p_instructions VARCHAR(300), IN p_override_reason VARCHAR(300), OUT p_item_id BIGINT UNSIGNED` |
| `sp_finalize_order` | procedure | DOCTOR | R4.7 | Finalize an order: nurses see only finalized orders | `IN p_order BIGINT UNSIGNED` |
| `sp_stop_order_item` | procedure | DOCTOR | R4.7 | Stop one order item (status stopped, with reason). Nurses cannot give a stopped item | `IN p_item BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `sp_cancel_order` | procedure | DOCTOR | R4.7 | Cancel a whole order | `IN p_order BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `sp_add_outside_lab` | procedure | OWNER,RECEPTIONIST,DOCTOR | R7.1 | Add an outside lab (master) | `IN p_name VARCHAR(120), IN p_phone VARCHAR(20), IN p_contact VARCHAR(80), IN p_notes VARCHAR(300), OUT p_lab_id BIGINT UNSIGNED` |
| `sp_create_lab_order` | procedure | DOCTOR | R7.1,R8.1,R8.2,R7.5 | own txn. Order an outside lab test. Price comes from the price list (item code) or is entered; a deviation above lab_price_deviation_pct (or a price with no list entry) needs a reason and is flagged. The bill line is generated here | `IN p_visit BIGINT UNSIGNED, IN p_lab BIGINT UNSIGNED, IN p_test VARCHAR(150), IN p_price_code VARCHAR(30), IN p_entered_price DECIMAL(12,2), IN p_reason VARCHAR(300), OUT p_lab_order_id BIGINT UNSIGNED` |
| `sp_lab_order_step` | procedure | NURSE,DOCTOR,RECEPTIONIST | R7.2 | Lab order step: sample_collected -> sent_to_lab (in this order) or cancelled | `IN p_lab_order BIGINT UNSIGNED, IN p_status VARCHAR(20), IN p_reason VARCHAR(300)` |
| `lab_identifier_matches` | function | * | R7.3 | 1 if the identifier value matches the patient of the lab order (patient_name, date_of_birth yyyy-mm-dd, patient_code, phone_last4) | `p_lab_order BIGINT UNSIGNED, p_kind VARCHAR(20), p_value VARCHAR(120)` |
| `sp_attach_lab_report` | procedure | NURSE,DOCTOR | R7.3,R10.2 | own txn. Attach an incoming report. Two DIFFERENT identifiers must match the order's patient. On mismatch NO report is stored: a wrong-patient interrupt alert is raised and p_message explains (p_report_id stays NULL). New version of an existing report supersedes it | `IN p_lab_order BIGINT UNSIGNED, IN p_source VARCHAR(20), IN p_file_path VARCHAR(300), IN p_hash CHAR(64), IN p_kind1 VARCHAR(20), IN p_val1 VARCHAR(120), IN p_kind2 VARCHAR(20), IN p_val2 VARCHAR(120), OUT p_report_id BIGINT UNSIGNED, OUT p_message VARCHAR(200)` |
| `sp_mark_report_critical` | procedure | NURSE,DOCTOR | R10.2 | Mark a report critical / not_critical (latest mark wins) | `IN p_report BIGINT UNSIGNED, IN p_mark VARCHAR(12), IN p_source VARCHAR(20), IN p_reason VARCHAR(300)` |
| `sp_review_report` | procedure | DOCTOR | R7.4 | Doctor reviewed the report (own event). Only the latest, non-retracted version can be reviewed | `IN p_report BIGINT UNSIGNED, IN p_note VARCHAR(500)` |
| `sp_retract_report` | procedure | NURSE,DOCTOR | R10.2 | Retract a report (wrong_patient raises an interrupt alert) | `IN p_report BIGINT UNSIGNED, IN p_kind VARCHAR(20), IN p_correct_order BIGINT UNSIGNED, IN p_note VARCHAR(300)` |

## Views

- `v_patient_allergy_current`
- `v_prescription_current`
- `v_prescription_print`
- `v_catalog_review_pending`
- `v_lab_order_current`
- `v_report_unreviewed`
- `v_nurse_tasks`
