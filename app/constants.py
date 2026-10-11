"""String constants that must match the ER model / procedures. Change here only."""
ROLES = ["OWNER", "DOCTOR", "RECEPTIONIST", "NURSE", "PHARMACY", "ACCOUNTANT"]
ROLE_LABEL = {"OWNER": "Owner / Admin", "DOCTOR": "Doctor", "RECEPTIONIST": "Reception", "NURSE": "Nurse",
              "PHARMACY": "Pharmacy", "ACCOUNTANT": "Accountant", "SYSTEM": "System"}
APPOINTMENT_STATUSES = ["Booked", "Checked-in", "Waiting", "In consultation", "Completed", "Cancelled", "No-show"]
VISIT_STATUSES = ["Arrived", "Waiting", "With Doctor", "Lab", "Pharmacy", "Billing", "Discharged"]
DOSE_STATUSES = ["given", "missed", "refused", "delayed", "stopped"]
APPROVAL_TYPES = ["discount", "free_service", "post_finalization_bill_correction", "refund", "payment_reversal",
                  "counter_sale_refund", "counter_sale_payment_reversal", "stock_adjustment", "stock_writeoff",
                  "visit_reopen", "stay_extension", "partial_payment_discharge"]
# which logical contract key completes an APPROVED request (the 'target row' of G6)
APPROVAL_COMPLETER = {"discount": "apply_discount", "free_service": "apply_discount",
                      "post_finalization_bill_correction": "post_final_line", "refund": "record_refund",
                      "payment_reversal": "reverse_payment", "partial_payment_discharge": "discharge_visit"}
# tables that hold approval_request_id (a request is 'consumed' once a row references it)
APPROVAL_TARGET_TABLES = ["bill_line", "refund", "payment", "discharge", "due_promise", "short_stay_extension",
                          "stock_movement", "visit_closure", "counter_sale_refund", "counter_sale_payment"]
PAYMENT_METHODS = ["cash", "upi", "card"]
SCHEDULE_CLASSES = ["none", "H", "H1", "X"]
ROUTES = ["oral", "IV", "IM", "SC", "topical", "inhaled", "other"]
OBS_TYPES = [("bp", "Blood pressure (sys/dia)", "mmHg"), ("pulse", "Pulse", "bpm"), ("temperature", "Temperature", "°C"),
             ("spo2", "SpO2", "%"), ("resp_rate", "Resp. rate", "/min"), ("weight", "Weight", "kg"),
             ("height", "Height", "cm"), ("blood_glucose", "Blood glucose", "mg/dL")]
IDENTIFIER_KINDS = [("patient_name", "Patient name"), ("date_of_birth", "Date of birth"),
                    ("patient_code", "Patient code"), ("phone_last4", "Phone last 4"), ("age_gender", "Age / gender")]
