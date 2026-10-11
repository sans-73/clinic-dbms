# DB API: owner_links

_Generated from `db/logic/06_owner_alerts_links.sql` by `tools/gen_db_api_docs.py`. Do not edit by hand._

Own-txn procedures commit by themselves: call them in autocommit mode. Rule violations raise SQLSTATE 45000 with a readable message.

| routine | kind | roles | rules | meaning | parameters |
|---|---|---|---|---|---|
| `sp_check_alerts` | procedure | OWNER | R10.2,R10.3,R10.4,R7.4,R7.5 | Run all alert checks now (also run by a MySQL EVENT as SYS-ALERT): stays over limit, unreviewed/critical reports, dispense mismatch at billing, expiring batches, lab ordered-not-billed / billed-no-report, unacknowledged handoffs, overdue dues; then escalates unacknowledged interrupts to the backup person | `` |
| `sp_extend_slot_horizon` | procedure | * | R3.6 | Generate appointment slots for all current schedule versions for schedule_horizon_days (run daily by an EVENT; recorded as SYS-SCHEDULER) | `` |
| `sp_acknowledge_alert` | procedure | OWNER | R10.4 | Acknowledge an alert (owner, or a person with a valid backup delegation) | `IN p_alert BIGINT UNSIGNED, IN p_note VARCHAR(300)` |
| `sp_create_family_link` | procedure | RECEPTIONIST,NURSE,DOCTOR | R11.1,R11.2,R11.3,R11.4 | Create a read-only link for ONE visit. Returns the plain token (shown once; only its SHA-256 is stored) and, for printed_code, the printed code. Consent note mandatory. If the attendant has a phone a simulated SMS is queued with the link only | `IN p_visit BIGINT UNSIGNED, IN p_attendant BIGINT UNSIGNED, IN p_kind VARCHAR(15), IN p_consent VARCHAR(300), OUT p_token VARCHAR(64), OUT p_printed_code VARCHAR(12), OUT p_link_id BIGINT UNSIGNED` |
| `sp_revoke_family_link` | procedure | RECEPTIONIST,NURSE,DOCTOR,OWNER | R11.2 | Revoke a link instantly (the link row is untouched; a revocation event is added) | `IN p_link BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `sp_open_family_link` | procedure | family | R11.1,R11.4 | PUBLIC entry for a family link (runs under SYS-FAMILY). Checks token + second factor (DOB yyyy-mm-dd or printed code). outcome: ok | invalid | expired | revoked | locked | bad_credential. After link_max_failed_attempts failures the link locks with a doubling delay. EVERY attempt is logged | `IN p_token VARCHAR(80), IN p_factor VARCHAR(40), IN p_source VARCHAR(100), OUT p_outcome VARCHAR(15), OUT p_visit_id BIGINT UNSIGNED, OUT p_link_id BIGINT UNSIGNED, OUT p_retry_seconds INT` |
| `sp_delegate_authority` | procedure | OWNER | R2.3,R9.1,R10.4,G6 | Delegate limited approval authority (scope discount | partial_payment | approval_backup | alert_escalation_backup) to another person for a time window, with an optional amount limit | `IN p_delegate BIGINT UNSIGNED, IN p_scope VARCHAR(30), IN p_limit DECIMAL(12,2), IN p_from DATETIME, IN p_to DATETIME, OUT p_delegation_id BIGINT UNSIGNED` |
| `sp_revoke_delegation` | procedure | OWNER | G6 | Revoke a delegation immediately (decisions already made stay valid) | `IN p_delegation BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |
| `sp_grant_role` | procedure | OWNER | R2.1 | Give a staff member an additional role from today (staff_role is a master row, audited) | `IN p_staff BIGINT UNSIGNED, IN p_role_code VARCHAR(20), IN p_reason VARCHAR(300)` |
| `sp_end_role` | procedure | OWNER | R2.1 | End a staff member's role from today (history kept) | `IN p_staff BIGINT UNSIGNED, IN p_role_code VARCHAR(20), IN p_reason VARCHAR(300)` |
| `sp_deactivate_staff` | procedure | OWNER | R2.1,R12.4 | Deactivate a staff member from today (no deletion). The owner cannot deactivate himself | `IN p_staff BIGINT UNSIGNED, IN p_reason VARCHAR(300)` |

## Views

- `v_alerts_open`
- `v_ex_ordered_not_billed`
- `v_ex_billed_no_report`
- `v_ex_discounts_by_staff`
- `v_ex_free_services`
- `v_ex_refunds`
- `v_ex_backdated_entries`
- `v_ex_edits_after_payment`
- `v_ex_shared_logins`
- `v_ex_otc_patterns`
- `v_ex_stock_adjustments`
- `v_ex_manual_prices`
- `v_ex_leftover_returns`
- `v_ex_weak_rx_flagged_drugs`
- `v_owner_checks`
- `v_owner_summary`
- `v_family_link_valid`
- `v_family_visit`
- `v_family_prescription`
- `v_family_reports`
- `v_family_bill`
- `v_family_bill_total`
- `v_family_receipts`
- `v_family_medicine_count`
- `v_family_notes`
