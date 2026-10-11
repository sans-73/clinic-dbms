"""Compares what the web app expects with what the live database really has.
Used by /_diag (owner) and tools/p5_check_contract.py. Read-only."""
from __future__ import annotations

from . import config, contract, db

# tables/columns the data layer reads or writes DIRECTLY (not through procedures). From the ER model (certain) unless
# marked (A-nn): an assumption listed in the guide.
EXPECTED_COLUMNS: dict[str, list[str]] = {
    "patient": ["patient_id", "display_code", "registration_kind", "given_name", "family_name", "date_of_birth", "gender"],
    "patient_phone": ["patient_id", "phone_sequence", "phone_number", "valid_to", "is_primary"],
    "patient_attendant": ["patient_attendant_id", "patient_id", "name", "relationship", "phone"],
    "patient_allergy": ["patient_allergy_id", "patient_id", "ingredient_id", "allergen_text", "reaction", "severity"],
    "patient_allergy_status_event": ["patient_allergy_id", "status"],
    "patient_merge_log": ["source_patient_id", "surviving_patient_id"],
    "staff": ["staff_id", "display_code", "principal_kind", "given_name", "family_name", "login_subject", "active_to"],
    "staff_credential": ["staff_id"],                                      # A-01 (+ a *hash* column)
    "role": ["role_id", "role_code"], "staff_role": ["staff_id", "role_id", "valid_from", "valid_to"],
    "doctor_detail": ["staff_id", "registration_number"],
    "doctor_schedule": ["doctor_schedule_id", "doctor_staff_id", "weekday", "start_time", "end_time", "valid_from"],
    "appointment_slot": ["appointment_slot_id", "doctor_schedule_id", "doctor_staff_id", "slot_start", "slot_end"],
    "appointment": ["appointment_id", "patient_id", "doctor_staff_id"],
    "appointment_slot_claim": ["appointment_slot_claim_id", "appointment_id", "appointment_slot_id"],
    "appointment_status_history": ["appointment_id", "status_seq", "status"],
    "schedule_change_request": ["schedule_change_request_id", "doctor_staff_id", "change_kind"],
    "schedule_change_application": ["schedule_change_request_id", "outcome"],
    "visit": ["visit_id", "patient_id", "appointment_id", "entry_type", "occurred_at"],
    "visit_status_history": ["visit_id", "status_seq", "status", "location_label"],
    "visit_doctor_assignment": ["visit_id", "doctor_staff_id", "assignment_role"],
    "visit_closure": ["visit_id", "closure_kind"],
    "short_stay_episode": ["short_stay_episode_id", "visit_id", "occurred_at"],
    "short_stay_extension": ["short_stay_episode_id", "extended_until"],
    "short_stay_closure": ["short_stay_episode_id"],
    "vitals_observation": ["visit_id", "observation_seq", "observation_type", "numeric_value", "unit"],
    "clinical_note": ["visit_id", "note_text", "sharing_classification"],
    "diagnosis": ["visit_id", "diagnosis_text"],
    "clinical_signoff": ["visit_id", "discharge_instructions"],
    "clinical_emergency_override": ["visit_id", "override_type", "authorized_by_staff_id", "clinical_justification"],
    "discharge": ["visit_id", "clinical_signoff_id", "financial_gate_basis"],
    "prescription": ["prescription_id", "visit_id"],
    "prescription_version": ["prescription_version_id", "prescription_id", "version_number"],
    "prescription_line": ["prescription_line_id", "prescription_id", "line_no"],
    "prescription_item": ["prescription_item_id", "prescription_version_id", "prescription_line_id", "medicine_id",
                          "free_text_medicine_name", "chosen_dose_text", "chosen_schedule", "food_instruction",
                          "duration_days", "quantity_prescribed", "item_change"],
    "prescription_line_status": ["prescription_line_id", "status"],
    "clinical_order": ["clinical_order_id", "visit_id", "order_type"],
    "clinical_order_status": ["clinical_order_id", "status"],
    "clinical_order_item": ["clinical_order_item_id", "clinical_order_id", "item_no", "item_kind", "medicine_id",
                            "free_text_medicine_name", "dose_value", "dose_unit", "route", "first_scheduled_at",
                            "repeat_interval_minutes", "quantity_ordered", "instructions"],
    "clinical_order_item_status": ["clinical_order_item_id", "status"],
    "dose_administration": ["clinical_order_item_id", "order_item_supply_id", "status", "quantity_consumed"],
    "ingredient": ["ingredient_id", "ingredient_name"], "medicine_ingredient": ["medicine_id", "ingredient_id"],
    "medicine": ["medicine_id", "brand_name", "generic_display_name", "strength_value", "strength_unit", "dosage_form",
                 "stock_unit", "schedule_class", "default_dose_text", "default_schedule", "default_food_instruction",
                 "default_duration_days", "returnable", "active"],
    "supplier": ["supplier_id", "name", "phone"],
    "stock_batch": ["stock_batch_id", "medicine_id", "supplier_id", "batch_code", "expiry_date", "cost_price", "mrp"],
    "stock_movement": ["stock_movement_id", "stock_batch_id", "movement_type", "quantity_delta", "unit_price_snapshot",
                       "dispensing_id"],
    "outside_prescription": ["outside_prescription_id", "counter_customer_id", "prescriber_name",
                             "prescriber_registration_no", "prescription_date"],
    "counter_customer": ["counter_customer_id", "phone", "temporary_code", "display_name"],
    "counter_sale": ["counter_sale_id", "counter_customer_id", "sale_number"],
    "counter_sale_payment": ["counter_sale_id", "amount", "method", "receipt_number"],
    "counter_sale_refund": ["counter_sale_id", "amount"],
    "dispensing": ["dispensing_id", "source_type", "medicine_id", "prescription_item_id", "outside_prescription_id",
                   "counter_sale_id"],
    "order_item_supply": ["order_item_supply_id", "clinical_order_item_id", "dispensing_id", "quantity_allocated"],
    "custody_handoff": ["custody_handoff_id", "dispensing_id", "recipient_staff_id", "recipient_attendant_id",
                        "quantity", "handoff_purpose"],
    "custody_handoff_acknowledgement": ["custody_handoff_id", "acknowledged_quantity"],
    "leftover_return_request": ["leftover_return_request_id", "dispensing_id", "quantity"],
    "leftover_return_decision": ["leftover_return_request_id", "decision", "stock_movement_id"],
    "cash_handover_declaration": ["cash_handover_declaration_id", "period_start", "period_end", "declared_amount"],
    "cash_handover_count": ["cash_handover_declaration_id", "counted_amount"],
    "catalog_review_queue": ["catalog_review_queue_id", "prescription_item_id", "submitted_name"],
    "catalog_review_event": ["catalog_review_queue_id", "review_action", "resolved_medicine_id"],
    "outside_lab": ["outside_lab_id", "name", "phone"],
    "lab_order": ["lab_order_id", "visit_id", "outside_lab_id", "test_name"],
    "lab_order_status": ["lab_order_id", "status"],
    "lab_order_price": ["lab_order_id", "entered_price", "is_price_deviation"],
    "lab_report": ["lab_report_id", "lab_order_id", "version_number", "file_path", "content_hash", "source"],
    "lab_report_review": ["lab_report_id"], "lab_report_critical_mark": ["lab_report_id", "mark"],
    "lab_report_retraction": ["lab_report_id"],
    "price_list": ["price_list_id", "item_code", "description", "category", "price", "effective_from", "effective_to",
                   "active"],
    "bill": ["bill_id", "visit_id", "bill_number"],
    "bill_line": ["bill_id", "line_no", "line_type", "description_snapshot", "quantity", "unit_price_snapshot",
                  "is_manual_price", "is_price_deviation", "reason", "approval_request_id"],
    "bill_finalization": ["bill_id"],
    "payment": ["payment_id", "bill_id", "payment_kind", "amount", "method", "receipt_number"],
    "refund": ["refund_id", "bill_id", "amount", "method", "receipt_number"],
    "due_promise": ["due_promise_id", "visit_id", "bill_id", "promised_by_date"],
    "approval_request": ["approval_request_id", "request_type", "visit_id", "bill_id", "requested_amount", "reason"],
    "approval_decision": ["approval_request_id", "decision", "decision_reason"],
    "authority_delegation": ["authority_delegation_id", "delegate_staff_id", "scope", "limit_amount", "valid_from",
                             "valid_to"],
    "authority_delegation_revocation": ["authority_delegation_id"],
    "audit_log": ["audit_log_id", "action", "entity_type", "entity_id", "old_values", "new_values", "reason",
                  "recorded_at", "recorded_by"],
    "alert_event": ["alert_event_id", "alert_class", "alert_type", "severity", "source_entity_type",
                    "source_entity_id", "summary", "recorded_at"],
    "alert_acknowledgement": ["alert_event_id", "note"], "alert_escalation": ["alert_event_id"],
    "login_session_event": ["staff_id", "login_identifier", "event_type", "recorded_at"],
    "message_outbox": ["message_outbox_id", "message_type", "recipient_phone", "safe_template_key", "recorded_at"],
    "message_delivery_event": ["message_outbox_id", "delivery_state"],
    "family_link": ["family_link_id", "visit_id", "attendant_id", "token_hash", "second_factor_kind", "expires_at"],
    "family_link_revocation": ["family_link_id"], "link_access_log": ["family_link_id", "success", "recorded_at"],
    "clinic_settings": ["setting_key", "setting_value"],
    "backup_run": ["backup_kind", "result", "recorded_at"],
}


def contract_report(actor=None) -> list[dict]:
    out = []
    if db.STUB:
        return [{"key": k, "status": "STUB", "proc": "-", "detail": "stub mode"} for k in contract.CONTRACT]
    with db.Session(actor) as s:
        for key, spec in contract.CONTRACT.items():
            row = {"key": key, "task": spec["task"], "known": spec["known"], "wanted": spec["procs"][0]}
            try:
                proc = s.resolve(key)
            except db.NotReady:
                row.update(status="MISSING", proc="-", detail=f"none of {spec['procs']} exists")
                out.append(row)
                continue
            params = s.routine_params(proc)
            names = [db._norm(p["name"]) for p in params if (p["mode"] or "IN").upper() != "OUT"]
            ours = {db._norm(a) for a in spec["args"]}
            unmatched_ours = [a for a in spec["args"]
                              if not any(db._pick({db._norm(a): 1}, n)[1] for n in names)]
            unfed = [n for n in names if n != "request_uuid" and not db._pick({a: 1 for a in ours}, n)[1]]
            status = "OK" if not unfed and not unmatched_ours else "ARGS"
            row.update(status=status, proc=proc, detail=(f"procedure params NOT sent by the web app (will be NULL): "
                                                         f"{unfed}; " if unfed else "") +
                       (f"web args the procedure does not have (ignored): {unmatched_ours}" if unmatched_ours else ""))
            out.append(row)
    return out


def schema_report(actor=None) -> list[dict]:
    if db.STUB:
        return []
    out = []
    with db.Session(actor) as s:
        rows = s.q("SELECT table_name AS t, column_name AS c FROM information_schema.columns WHERE table_schema=%s",
                   (config.DB_NAME,))
        have: dict[str, set] = {}
        for r in rows:
            have.setdefault(r["t"].lower(), set()).add(r["c"].lower())
        for t, cols in EXPECTED_COLUMNS.items():
            if t not in have:
                out.append({"table": t, "status": "TABLE MISSING", "missing": cols})
                continue
            miss = [c for c in cols if c not in have[t]]
            out.append({"table": t, "status": "OK" if not miss else "COLUMNS MISSING", "missing": miss})
    return out
