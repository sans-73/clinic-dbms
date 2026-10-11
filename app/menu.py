"""Role-based menus. Each entry: (label, url). Route modules own the pages behind them."""
MENUS = {
    "RECEPTIONIST": [("Queue board", "/reception"), ("Patients", "/reception/patients"),
                     ("Appointments", "/reception/appointments"), ("New patient", "/reception/patients/new"),
                     ("Emergency", "/reception/emergency"), ("Schedule changes", "/reception/schedule-changes"),
                     ("Bills", "/billing"), ("Family links", "/family/links")],
    "DOCTOR": [("My queue", "/doctor"), ("My schedule", "/doctor/schedule"), ("Lab reports", "/doctor/reports")],
    "NURSE": [("Ward tasks", "/nurse"), ("Vitals", "/nurse/vitals"), ("Medicine custody", "/nurse/custody"),
              ("Lab reports in", "/nurse/lab"), ("Family links", "/family/links")],
    "PHARMACY": [("Stock", "/pharmacy"), ("Dispense", "/pharmacy/dispense"), ("Receive stock", "/pharmacy/receive"),
                 ("Leftover returns", "/pharmacy/leftovers"), ("Counter sales", "/pharmacy/counter"),
                 ("Catalog review", "/pharmacy/catalog-review"), ("Cash handover", "/pharmacy/cash")],
    "ACCOUNTANT": [("Bills", "/billing"), ("Dues", "/billing/dues"), ("Cash handover", "/billing/cash"),
                   ("Discharge", "/billing/discharge")],
    "OWNER": [("Daily summary", "/owner"), ("Approvals", "/owner/approvals"), ("Alerts", "/owner/alerts"),
              ("Reports", "/owner/reports"), ("Reconciliation", "/owner/reconcile"), ("Audit log", "/owner/audit"),
              ("Price list", "/owner/prices"), ("Settings", "/owner/settings"), ("Staff", "/owner/staff"),
              ("Delegations", "/owner/delegations"), ("Catalog review", "/pharmacy/catalog-review"),
              ("Bills", "/billing"), ("Messages", "/family/outbox"), ("Diagnostics", "/_diag")],
}


def menu_for(role: str):
    return MENUS.get(role, [])
