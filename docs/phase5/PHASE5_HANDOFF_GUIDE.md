# Phase 5 hand-off guide (for the main agent that owns the project context)

**Author:** independent WEB team member. **Scope delivered:** P5-1 skeleton/login/role menu, P5-2 reception, P5-3 doctor, P5-4 pharmacy + nurse, P5-5 billing + discharge, P5-6 owner, P5-7 family link pages.
**Owner of this guide's files:** `app/**`, `tools/p5_*.py`, `docs/phase5/**` only. Nothing in `db/`, `docs/design/`, `app/security.py` or the other tracks' tools was touched.

> **Read this first.** The web app was written WITHOUT access to the real schema SQL, to `docs/db_api/*.md`, or to a running MySQL. It was built from `01_er_model.md` (column names), `TASKS.md` (shared interface signatures), `PROJECT_CONTEXT.md` (rules) and the Track-B hand-off text.
> Everything that depends on a guess is isolated in **one contract file** (`app/contract.py`) or listed in the **assumptions register** (section 5). Your job is to make the contract true (change the contract, or change the procedures), then run the checks in section 8.

---

## 1. What was and was not verified

| Check | Result | How to re-run |
|---|---|---|
| Python syntax + undefined names, all `app/**` and `tools/p5_*` | PASS | `python tools/p5_static_check.py` |
| All 53 templates render, in "everything present" and "everything empty" mode | PASS | `python tools/p5_smoke_templates.py` |
| Procedure lookup + argument matching by name, OUT params, auto `request_uuid`, NotReady error (fake DB connection) | PASS (7 asserts) | `python tools/p5_selftest_db.py` |
| FastAPI app boot, routing, dependency injection | **NOT RUN** (FastAPI not installable in the authoring sandbox) | `uvicorn app.main:app` |
| Any SQL against MySQL 8.4 | **NOT RUN** | `python tools/p5_check_contract.py` then click through |
| Real procedures from P3-3..P3-6 | do not exist yet | `docs/phase5/DB_CONTRACT.md` is what the web app needs from them |

Expect a first-run list of small fixes: a column name, a procedure argument name, a status string. Section 8 gives the loop.

---

## 2. Architecture in one page

```
browser ──HTTP──> app/routes/*.py   (thin: parse form, check role, call data layer, flash, redirect)
                       │
                       ▼
                 app/data/*.py      (SQL reads on ER column names + stored-procedure calls by contract key)
                       │
                       ▼
                 app/db.py          (ONLY MySQL access point)
                       │ sets @app_user_id, @app_role on every connection (G5), SET time_zone='+00:00'
                       │ translates errors: 1644 (SIGNAL 45000) -> RuleError(message), 1305/1146/1054 -> NotReady
                       ▼
                 MySQL 8.4 (triggers / procedures / views own the rules)
```

Principles kept (from `PROJECT_CONTEXT.md`):
* **G3**: no stored counters. Stock, bill balance, visit status, appointment status, medicine remaining, discharge checklist, approval status are all computed in SQL (`data/*.py`).
* **G4/G5**: DB mode `roles` selects a MySQL account per active role; session variables are set before anything runs.
* **G6**: web app never approves. It creates requests via `request_approval`, shows status, and only **completes** an action after an `approved` decision exists (`data/billing.complete_approved`).
* **G11**: every form carries a `request_uuid` (JS fills a UUID per rendered form; `db.call` fills one if the procedure takes it and none was sent). Double-click protection in `static/app.js`.
* **G10**: cost price only selected for OWNER/PHARMACY (`data/pharmacy.can_see_cost`); family pages select only shareable data; SMS text built from clinic name + link only.
* Parameterized SQL everywhere. The only f-string SQL pieces are fixed fragments (table names from constants, `IN (%s,%s)` placeholders).
* CSRF token on every POST (`web.csrf_protect`), signed session cookie, login throttling in `auth.py`, upload extension + size + SHA-256 + path-traversal guard.

### Write-path policy (important when you reconcile with P3)
| Situation | Web app does |
|---|---|
| Multi-row, cross-table, or rule-heavy action (book, dispense, administer dose, discharge, merge, receive stock, prescriptions, orders, lab attach, family link ...) | **calls a stored procedure** (contract key) |
| One simple row of a doctor/clinic-authored **event** with no cross-table logic (clinical note, diagnosis, sign-off, emergency override, allergy, catalog review event, counter sale header, outside prescription, delegation grant/revoke, alert acknowledgement fallback, login_session_event) | **direct `INSERT`** via `db.insert_event` (triggers fill `recorded_*`) |
| **Master** edits (patient demographics, price list, clinic settings, staff_role, counter_customer, medicine) | direct `INSERT/UPDATE` via `db.insert_master/update_master` (audit triggers log old/new) |

The complete list of direct writes is in section 6. In `DB_MODE=roles` (after P3-7) each of those needs either an `INSERT` grant on exactly that table for exactly that role, or a small procedure wrapper. **That is a P3-7 decision**; the web layer needs no change if you wrap them (add the wrapper to `contract.py` and switch the one call in `data/*.py`).

---

## 3. File map

| Path | Purpose |
|---|---|
| `app/main.py` | App factory, auto-discovers `app/routes/*.py`, error handlers |
| `app/config.py` | `.env` settings. `DB_MODE=dev|roles|stub`, host/port (default **3307**, from docker-compose), timezone, upload dir |
| `app/db.py` | `Session`, `call()`, `q/one/scalar/ex`, `insert_event/insert_master/update_master`, `Actor`, `RuleError`, `NotReady`, `system_actor()` |
| `app/contract.py` | **THE contract**: 73 logical procedure keys -> candidate names + argument names + owning task |
| `app/constants.py` | Status strings, approval types, role labels |
| `app/auth.py`, `app/passwords.py` | Login, hat selection, `require(*roles)`, scrypt hashing (delegates to `app.security` if present) |
| `app/web.py`, `app/jinja_env.py`, `app/menu.py`, `app/timeutil.py` | rendering, filters, flash, CSRF, menus, UTC/IST handling |
| `app/diagnostics.py` | contract + schema comparison used by `/_diag` and `tools/p5_check_contract.py` (`EXPECTED_COLUMNS` = every column the SQL touches directly) |
| `app/data/{common,patients,clinical,pharmacy,nurse,billing,owner,family}.py` | all SQL + procedure calls, one file per area |
| `app/routes/{session,reception,doctor,pharmacy,nurse,billing,owner,family,files}.py` | URL handlers (full table: `docs/phase5/ROUTE_MAP.md`, generated) |
| `app/templates/**`, `app/static/{style.css,app.js,rx.js}` | UI. `rx.js` = prescribing screen (type-ahead + allergy dialog). No framework, no CDN, works offline |
| `tools/p5_check_contract.py` | live comparison vs MySQL (**run first**) |
| `tools/p5_static_check.py`, `p5_smoke_templates.py`, `p5_selftest_db.py` | no-database checks |
| `tools/p5_gen_contract_doc.py`, `p5_gen_route_map.py` | regenerate `DB_CONTRACT.md` and `ROUTE_MAP.md` after any change |

---

## 4. How the contract layer works (so you can fix things in one place)

`db.Session.call(key, **kwargs)`:
1. `resolve(key)`: first name in `contract.CONTRACT[key]["procs"]` that exists in `information_schema.routines`. If none: `NotReady("... belongs to task P3-x")` (the screen shows this message).
2. Reads the procedure's real parameters from `information_schema.parameters`.
3. For each IN parameter: value = the kwarg with the same name **ignoring a leading `p_`**, or a synonym (`_SYN` list in `db.py`: `first_name`~`given_name`, `dob`~`date_of_birth`, `doctor_id`~`doctor_staff_id`, `qty`~`quantity`, `reason`~`note`, `slot_id`~`appointment_slot_id` ...). Missing -> NULL. Blank string -> NULL. `request_uuid` -> auto UUID.
4. OUT / INOUT parameters are read back into `CallResult.out` by name (`res.first_value("patient_id")` also searches the first result row).
5. Result sets (`SELECT` inside the procedure) are collected in `CallResult.sets`.
6. Arguments the procedure does not have are **logged and ignored** (never fatal). Run the app with logging visible to see them.

**Therefore: argument ORDER never matters, extra arguments never break, and renamed arguments are fixed by adding a synonym or editing the `args` list.** `tools/p5_check_contract.py` prints, per key: `OK`, `MISSING` (no such procedure), or `ARGS` (procedure parameters the web app does not send, which will be NULL, and web arguments the procedure does not have).

If a real procedure returns its id under a different name, the screen still works but loses the id (e.g. redirect to the new patient). Fix with `first_value("alt_name")` in the data function, or rename the OUT parameter.

---

## 5. Assumptions register (verify each; "Fix" says where)

| ID | Assumption | Used in | Verify | Fix if wrong |
|---|---|---|---|---|
| A-01 | Table `staff_credential(staff_id, <column containing "hash">)` exists (Track-B added it for login). `staff.login_subject` or `staff.display_code` is the login name | `auth.authenticate`, `data/owner.set_password` | `/login` with the bootstrap owner | `auth.py` SELECT; `owner.set_password` column name `password_hash` |
| A-02 | Hash format: delegated to `app.security.verify_password(password, stored)` / `hash_password` if that module exposes them, else own scrypt format `scrypt$n$r$p$salt$hash` | `passwords.py` | log in as bootstrap owner | adapt `passwords.py` to the real function names (Track-B note says stdlib scrypt, CR-B?) |
| A-03 | Seeded system principals: `staff.principal_kind='system'` and `display_code` contains `AUTH` / `FAMILY` (also `ALERT`, `SCHEDULER`) | `db.system_actor`, login events, public family pages | `SELECT display_code FROM staff WHERE principal_kind='system'` | change the LIKE patterns in `db.system_actor` |
| A-04 | Role codes: `OWNER, DOCTOR, RECEPTIONIST, NURSE, PHARMACY, ACCOUNTANT` (+`SYSTEM`) and `@app_role` must be that **role_code** | `auth`, `db.Session` | P3-1 `app_role()` check trigger passes | `constants.ROLES`, `menu.MENUS`, or map in `Session.__init__` |
| A-05 | All timestamps passed to / read from procedures are **UTC naive**; the UI converts to/from `APP_TIMEZONE` (IST) | `timeutil`, every `datetime-local` field | book/dose screens show right time | `timeutil.local_input_to_utc` |
| A-06 | String values: gender `Male/Female/Other`; `entry_type` `walk_in/emergency/appointment`; appointment statuses exactly as `constants.APPOINTMENT_STATUSES`; visit statuses `VISIT_STATUSES`; dose statuses lower-case; `item_kind` `medication/procedure/observation`; payment methods `cash/upi/card`; severities `mild/moderate/severe` | forms | CHECK constraint errors on save | `constants.py`, `reception.GENDERS`, template option lists |
| A-07 | `find_patient_candidates` returns rows with `patient_id, display_code, tier, score` and either `name` or `given_name`+`family_name`. If the procedure is missing, a simple SQL fallback (phone / DOB / SOUNDEX) is used | `data/patients.find_candidates` | register a near-duplicate | column names in `new.html`, normaliser in `find_candidates` |
| A-08 | **Prescribing sequence**: `create_prescription(visit_id)` OUT `prescription_id` (+ optionally `prescription_version_id`) -> `add_prescription_item(...)` once per medicine; revision = `new_prescription_version(prescription_id, change_reason)` OUT version id then `add_prescription_item` for **every** line (unchanged ones with `item_change='unchanged'` and the existing `prescription_line_id`; removed ones `discontinued`). Allergy confirmation fields travel on the item call (`allergy_confirmed, allergy_override_reason, displayed_allergies`) and the procedure writes `AllergyConfirmation`, `CatalogReviewQueue` for free text, and raises the override interrupt. If P3-3 uses a different shape (one call with a JSON array) change only `data/clinical.save_prescription` | `data/clinical.save_prescription` | prescribe a drug + a free-text drug | that one function |
| A-09 | **Order sequence**: `create_order` OUT `clinical_order_id` -> `add_order_item` -> optional `order_status('finalized')` | `data/clinical.create_order` | create a short-stay order | that function |
| A-10 | `medicine_suggest(prefix, patient_id)` returns rows (any of: `medicine_id/id`, `brand_name/name`, `generic_display_name`, `stock/in_stock/on_hand`, `state/colour`, `label`, `default_dose_text`...). Missing -> SQL fallback. The **allergy warning for catalog items is always computed by the web layer** (`clinical.allergy_warnings`, ingredient link + text match) and is re-checked server-side on save | `clinical._norm_suggestion` | type "par" | `_norm_suggestion` key list |
| A-11 | Emergency override: web inserts `clinical_emergency_override` directly and then calls `raise_alert(..., 'INTERRUPT')`. If P3-5 `discharge_visit` or a trigger already raises the interrupt, delete the `raise_alert` call in `clinical.emergency_override` | `clinical.emergency_override` | alert count after override | that function |
| A-12 | **Counter sale**: web creates `counter_customer` and the `counter_sale` header itself (`sale_number` from `next_code(kind='counter_sale')`, fallback `CS-yymmddHHMMSS`) and then calls `dispense(source_type, medicine_id, quantity, counter_sale_id, counter_customer_id, outside_prescription_id, ...)`. Outside prescription row is inserted directly | `data/pharmacy` | OTC sale | if `dispense` creates the sale itself, drop `start_counter_sale` and read `counter_sale_id` from OUT |
| A-13 | Dispensing against short-stay orders: after `dispense` the web calls `supply_to_order(clinical_order_item_id, dispensing_id, quantity_allocated)` (`OrderItemSupply`). If `dispense` accepts an order-item argument instead, pass it there (CR-5 in the ER) | `pharmacy.dispense` | stay medicine flow | that function |
| A-14 | **Family link**: `family_link.token_hash` = hex SHA-256 of the raw token (`data/family.token_hash`); `open_family_link(token, dob | printed_code)` enforces lockout/logging and either returns a row with `visit_id` / `family_link_id` or raises; `create_family_link` returns OUT `token` (+`printed_code`, `family_link_id`) | `data/family.py` | open link 5x wrong, then right | `token_hash()`, `open_link()` |
| A-15 | `request_approval(type, ref_table, ref_id, reason, amount)` stores context so that `approval_request.bill_id` (ER context FK) is set for ref_table `bill`. Where extra data is needed at completion (payment id, refund method, correction type) the web app prefixes the reason with tags like `[payment:12][method:cash]` and parses them back in `billing.complete_approved` | `data/billing` | refund + reversal flow | if the procedure keeps `proposed_change`, move the tags there |
| A-16 | Approval is "consumed" when any of these tables has a row with that `approval_request_id`: `bill_line, refund, payment, discharge, due_promise, short_stay_extension, stock_movement, visit_closure, counter_sale_refund, counter_sale_payment` | `constants.APPROVAL_TARGET_TABLES` | | the list |
| A-17 | Primary keys follow `<table>_id`; "latest status row" is `ORDER BY <table>_id DESC` or `status_seq DESC` (e.g. `patient_allergy_status_event_id`, `lab_order_status_id`, `clinical_order_status_id`, `clinical_order_item_status_id`, `prescription_line_status_id`, `visit_doctor_assignment_id`, `appointment_slot_claim_id`, `lab_report_critical_mark_id`, `alert_acknowledgement_id`, `message_delivery_event_id`, `patient_blood_group_id`, `diagnosis_id`, `clinical_note_id`, `clinical_signoff_id`, `due_promise_id`, `authority_delegation_id`) | many subqueries | `p5_check_contract.py` columns report | global search/replace |
| A-18 | Money signs: `payment.amount`, `refund.amount`, `counter_sale_payment.amount` stored **positive**; reversals are `payment_kind='reversal'` (bill) or rows with `corrects_id` (counter); `balance = Σ(line.quantity*unit_price_snapshot) − Σpayments(+ reversals negative) + Σrefunds`; credit / discount lines carry negative amount via negative price or quantity | `data/billing`, `pharmacy` | bill with discount + refund | `billing.TOTAL/PAID/REFUNDED` constants |
| A-19 | `stock_movement.quantity_delta` is **negative** for `dispensed` and positive for `returned_by_patient`; `unit_price_snapshot` set on those | `data/pharmacy`, `nurse.reconciliation` | dispense one strip | SQL signs |
| A-20 | Reads are plain `SELECT`s on tables. In `DB_MODE=roles` every role needs `SELECT` on what its screens read (or role views). Cost price columns are never selected outside OWNER/PHARMACY | all | P3-7 | grants / views |
| A-21 | `alert_event.alert_class='safety_interrupt'`, `severity` `red/amber`; acknowledgement uses `acknowledge_alert(alert_event_id, note)` if present, else a direct `alert_acknowledgement` insert | `data/owner.py` | seed an alert | constants in `owner.py` |
| A-22 | `clinic_settings` keys used: `clinic_name`, `clinic_address`, `clinic_phone` (optional), `stay_limit_hours`, `expiry_alert_days`; `setting_value` is text | `data/*` | | |
| A-23 | `dispensing.source_type` = `clinic_prescription / outside_prescription / otc`; `custody_handoff.handoff_purpose` = `supply_to_nurse / take_home / leftover_to_family`; `leftover_return_decision.decision` = `accepted / refused`; `lab_order_status.status` = `ordered / sample_collected / sent_to_lab / cancelled` | SQL | | strings in SQL |
| A-24 | Reversed/superseded `order_item_supply` rows: net supply excludes rows that are reversals or have been reversed (`reverses_order_item_supply_id`) | `data/nurse._SUPPLY_OK` | | |
| A-25 | `patient_phone.valid_to IS NULL` = current phone; `add_patient_phone` assigns `phone_sequence` | `data/patients` | | |
| A-26 | `lab_report_identifier` handling: web sends up to 2 identifier triplets **and** an `identifiers_json` string; the procedure enforces "≥ 2 distinct kinds with matches_order=1". The web also pre-checks this | `data/nurse.attach_report` | wrong identifiers refused | arg names in contract |
| A-27 | Uploaded lab report files are stored under `uploads/lab/` (path relative, saved in `lab_report.file_path`, SHA-256 in `content_hash`). `uploads/` must be git-ignored and backed up with the DB | `data/nurse.save_upload` | | `UPLOAD_DIR` |
| A-28 | `staff_role` rows are master rows editable by the owner (`valid_from`, `granted_by` set by the web app). If P3-1 requires a procedure, switch `owner.add_role/end_role` | `data/owner.py` | | |
| A-29 | Maximum **one bill per visit** (ER N-4): `ensure_open_bill(visit_id, NULL)` returns that bill (OUT `bill_id`) | billing | | |
| A-30 | Schedule changes: `request_schedule_change`, `apply_schedule_change(request_id, outcome applied/declined, reason)`; slot generation by `generate_slots(doctor_staff_id, from_date, to_date)` (name unknown) | reception/doctor | | contract keys |

---

## 6. Direct writes (tables the web app writes WITHOUT a procedure)

Event tables via `insert_event` (`request_uuid` and `occurred_at` added automatically; triggers fill `recorded_by/role/at`):
`patient_allergy`, `clinical_note`, `diagnosis`, `clinical_signoff`, `clinical_emergency_override`, `counter_sale`, `outside_prescription`, `catalog_review_event`, `authority_delegation`, `authority_delegation_revocation`, `alert_acknowledgement` (fallback only), `login_session_event` (as the AUTH system principal).

Master tables: `patient` (UPDATE of name/gender/DOB/approx age/Aadhaar last-4), `counter_customer` (INSERT), `medicine` (INSERT from the catalog-review screen), `price_list` (INSERT/UPDATE), `clinic_settings` (UPDATE of `setting_value`), `staff_role` (INSERT / `UPDATE valid_to`), `staff_credential` (UPSERT of the password hash).

For P3-7: either grant exactly these, or wrap them. Suggested wrappers (any name; add to `contract.py` and swap the call): `add_clinical_note`, `add_diagnosis`, `clinical_signoff`, `record_emergency_override`, `add_patient_allergy`, `create_counter_sale`, `add_outside_prescription`, `resolve_catalog_review`, `grant_delegation`, `revoke_delegation`, `update_patient_demographics`, `set_staff_password`.

---

## 7. What each screen needs from the database

Generated tables: **`docs/phase5/ROUTE_MAP.md`** (URL -> role -> data functions) and **`docs/phase5/DB_CONTRACT.md`** (procedure -> arguments -> task). In short:

* **P3-2 (done by Track B)**: register_patient, find_patient_candidates, register_unknown_patient, merge_patients, add_patient_phone / attendant, generate_slots, book_appointment, appointment status, reschedule, schedule change request/apply, start_visit, add_visit_status, start_short_stay, stay extension request, record vitals. *Names and argument names must be checked with `p5_check_contract.py`.*
* **P3-3 pending**: medicine_suggest, create/revise prescription, add item, line status, create order/item, order status, order-item status, order_lab_test, lab status, attach_lab_report, review, critical mark, retract.
* **P3-4 pending**: receive_stock, request_stock_adjustment, dispense, administer_dose, order supply link, custody handoff + acknowledgement, ask_leftover_return, decide_leftover, counter-sale return.
* **P3-5 pending**: ensure_open_bill, bill_add_line (fixed signatures), finalize_bill, record_payment, reverse_payment, record_refund, apply_discount, post-finalization line, request_partial_payment_discharge, discharge_visit, counter-sale payment, declare/receive cash handover.
* **P3-6 pending**: acknowledge_alert, check_alerts, create/open/revoke family link, delegations.
* **P3-1 (done)**: raise_alert, queue_message, request_approval, decide_approval, next_code, create_staff.

The **R6.2 reconciliation**, **discharge checklist**, **stock on hand**, **bill balance**, **appointment/visit current status**, **owner daily summary** and **dues** are computed by Phase-5 SQL (`data/nurse.reconciliation`, `data/billing.checklist`, `pharmacy.stock_by_medicine`, `billing.BAL`, `owner.RED/AMBER`). If P3-4/P3-5/P3-6 build official views (`v_dispense_reconciliation`, `v_discharge_checklist`, daily summary views), swap those functions to `SELECT * FROM v_...` so there is a single source of truth. The numbers must match: **run both and compare** (a mismatch is a real bug somewhere).

---

## 8. Integration loop (do exactly this)

```bash
docker compose down -v && docker compose up -d
bash tools/run_sql_checked.sh                      # schema + logic (Track B)
python tools/bootstrap_owner.py ...                # first owner (Track B), note login + password
python tools/p5_check_contract.py > p5_contract_report.txt
```
1. Open `p5_contract_report.txt`.
   * **TABLE/COLUMNS MISSING** -> fix the web SQL (the schema is the truth for `01_er_model.md`-derived columns; if the schema deviates from the ER, change `app/data/*.py` and `diagnostics.EXPECTED_COLUMNS`).
   * **MISSING procedure** -> either P3-x has not built it yet (expected, the screen says so) or it has another name: add the name to `procs` in `contract.py`.
   * **ARGS** -> add a synonym to `db._SYN` or edit the `args` list; decide whether a NULL parameter is acceptable.
2. Start `uvicorn app.main:app --reload --port 8000`. Log in. Walk the "Day at the clinic" path below. Every `NotReady`/`RuleError` shown on screen includes the DB message (when `APP_DEBUG=1`).
3. After each fix: `python tools/p5_static_check.py && python tools/p5_smoke_templates.py && python tools/p5_selftest_db.py`, then regenerate docs: `python tools/p5_gen_contract_doc.py && python tools/p5_gen_route_map.py`.

**Walk-through (matches P6-4):** reception registers "Ramesh Sharma" then "Rameshwar Sarma" same DOB (candidate list) -> books slot twice (second refused) -> check-in -> doctor opens visit, adds diagnosis, prescribes catalog drug + free-text drug (allergy dialog) -> orders a lab test -> pharmacy dispenses (stock drops, bill line) -> short stay: doctor starts stay + finalized order -> pharmacy dispenses and links to the order -> nurse records a dose (supplied/given/left updates) -> nurse asks leftover return -> pharmacy decides -> accountant bill: payment, discount request -> owner approves on a phone-width window -> accountant completes the approved discount -> discharge checklist ticks -> partial-payment request -> owner approves -> discharge -> dues list -> family link: 5 wrong DOBs lock, correct one opens.

### Integration prompt (paste to the main agent with this guide, `DB_CONTRACT.md`, `ROUTE_MAP.md`, `p5_contract_report.txt`)
```
ROLE: DB-LOGIC + WEB integrator.
The Phase 5 web app (folder app/, tools/p5_*, docs/phase5/) was written independently from the ER model, TASKS.md and PROJECT_CONTEXT.md,
without the real SQL. Read docs/phase5/PHASE5_HANDOFF_GUIDE.md fully. Then:
1. Run python tools/p5_check_contract.py and fix every MISSING/ARGS/COLUMNS item. Prefer changing app/contract.py or app/data/*.py
   when the web guess is wrong; prefer changing the stored procedure when the web call expresses the TASKS.md / rule intent better.
2. For each assumption A-01..A-30 in the guide, confirm or fix it. Record each decision as a CR in PROGRESS.md if it changes a rule.
3. Build P3-3..P3-6 so that docs/phase5/DB_CONTRACT.md is satisfied (names/args), or update the contract to what you built.
4. Replace direct writes (guide section 6) with grants or wrappers consistent with P3-7; do not weaken G1-G12.
5. Run the day-at-the-clinic walk-through; fix what breaks. Keep changes minimal and return COMPLETE changed files.
6. Re-run the three offline checks and regenerate the two generated docs. End with a HANDOFF NOTE.
```

---

## 9. Known gaps and deviations (honest list)

**Deviations from TASKS.md P5:**
* HTMX is **not** used. Small interactions use ~60 lines of vanilla JS (`app.js`, `rx.js`) so the app has zero CDN dependency. Swapping to HTMX later is optional.
* `DB_MODE=stub` exists (not in the card): no database, empty screens. Useful for UI review and demos.
* Doctor/nurse/receptionist/accountant roles get **separate menus**; the OWNER cannot reach clinical or pharmacy-operation pages (only reads, approvals, reports, settings), in line with the permission table.
* Short stay is **started by the doctor** (`/doctor/visit/{id}`); extension requests by doctor or nurse; ending by the doctor.

**Features specified in the rules but without a screen yet** (data layer / contract keys exist for some):
* G-1 lab price entry per test + deviation flag (R8.1 lab) (`lab_price` key unused); lab critical-mark by NURSE (doctor can mark).
* G-2 allergy status change (inactive / entered in error) and blood-group entry (display only).
* G-3 counter-customer -> patient audited link (R5.6).
* G-4 counter-sale refund and counter payment reversal (approval flow is implemented for **bill** money only; the pattern in `billing.complete_approved` extends directly).
* G-5 visit reopen (`visit_reopen`), stock-adjustment **completion** from the pharmacy side after owner approval (depends on how `request_stock_adjustment` is built).
* G-6 doctor-assignment change / covering doctor (`assign_doctor` key unused); appointment "Waiting / In consultation" buttons (visit status can be set from the queue board).
* G-7 alert escalation controls (escalation happens in DB procedures; the owner sees `escalated`).
* G-8 Version-2 preview (`P7-3`) was not in scope.
* G-9 paper-fallback / back-fill entry form (R12.1): `is_backfilled` is only displayed in reports.

**Security notes:** session cookie is signed, not encrypted: do not put secrets in it (the one-time created family link URL is shown via a session flash; if that bothers you, store it server-side). `APP_SECRET_KEY` must be changed outside local use. `APP_DEBUG=1` shows technical DB detail in error messages; set `0` for any public demo. Public family routes run as the `SYS-FAMILY` principal and select only shareable columns; verify with a "no cost price anywhere" grep on the rendered HTML during P6-3.

---

## 10. HANDOFF NOTES (standard format)

```
HANDOFF P5-1
- Done: app factory with router auto-discovery, login (throttled, hashed, signed session), role/hat selection + switch, role menus, error page mapping SQLSTATE 45000 to plain text, DB layer setting @app_user_id/@app_role, DB_MODE dev|roles|stub, CSRF, /healthz, /_diag.
- Files: app/{main,config,db,auth,passwords,web,jinja_env,menu,timeutil,contract,constants,diagnostics}.py, app/routes/session.py, app/templates/{base,macros,login,choose_role,error,diag}.html, app/static/*, app/requirements.txt, app/README.md
- How to check: DB_MODE=stub uvicorn app.main:app ; log in owner/demo. Real: python tools/p5_check_contract.py
- Assumptions: A-01..A-05, A-17, A-20
- Open issues / CRs: CR-P5-1 (grants for reads in roles mode), CR-P5-2 (password hash format)

HANDOFF P5-2  Reception: queue board, patient search/registration with duplicate candidates before saving (confirm box), emergency unknown patient, merge (human confirmed), demographic correction (audited), phones/attendants/allergies, appointments with live slot grid + book/check-in/cancel/no-show/reschedule, slot generation, schedule-change apply, short-stay panel.   Files: app/routes/reception.py, app/data/patients.py, app/templates/reception/*   Assumptions A-05..A-07, A-25, A-30
HANDOFF P5-3  Doctor: queue, visit summary, prescribing screen (type-ahead green/blue/red WITH text, editable defaults, allergy dialog, free text, new version), line status (bought outside), lab orders, reports review/critical/retract, notes/diagnosis/sign-off/emergency override, short-stay orders, schedule requests, printable prescription.   Files: app/routes/doctor.py, app/data/clinical.py, app/templates/doctor/*, app/static/rx.js   Assumptions A-08..A-11
HANDOFF P5-4  Pharmacy: stock from ledger, batches, FEFO, expiry flags, receive, adjustment requests, dispense (clinic Rx with order linking), counter sales (OTC / outside Rx, payments, returns), leftover decisions, catalog review, cash declaration. Nurse: ward task list (due/overdue), dose logging (ordered vs actual, reasons), vitals + trend sparkline, custody reconciliation (R6.2), leftover request, family handover, acknowledgement, lab report attach (2 identifiers, SHA-256), extension request.   Files: app/routes/{pharmacy,nurse,files}.py, app/data/{pharmacy,nurse}.py, templates   Assumptions A-12, A-13, A-19, A-23, A-24, A-26, A-27
HANDOFF P5-5  Billing: bill list/view, price-list lines, finalize, payments/receipt, owner-approval requests with "complete after approval", discharge checklist (live), partial-payment request, discharge, dues + reminder, cash count.   Files: app/routes/billing.py, app/data/billing.py, templates   Assumptions A-15, A-16, A-18, A-29
HANDOFF P5-6  Owner: red/amber/clean summary, interrupt banner, approvals inbox, alerts + acknowledge, 13 exception reports, audit viewer, price list, settings, staff/roles/passwords, delegations, reconciliation.   Files: app/routes/owner.py, app/data/owner.py, templates   Assumptions A-21, A-22, A-28
HANDOFF P5-7  Family: public /v/<token> (DOB or printed code, DB lockout), visit page (Rx, supply counts, reports, bill, receipts, instructions), revalidation on every view (instant revoke), staff link create/revoke, message outbox (masked numbers).   Files: app/routes/family.py, app/data/family.py, templates   Assumption A-14
```

**Change requests proposed** (format: `CR: title | Affects | Problem | Proposal`):
* `CR-P5-1: Read grants for role accounts | P3-7 | web reads many tables directly | create role views or grant SELECT per role; web switches via data/*.py`
* `CR-P5-2: Password hash contract | P3-1/bootstrap_owner.py | web must verify what bootstrap writes | expose verify_password/hash_password in app/security.py (the web delegates to them)`
* `CR-P5-3: Procedure wrappers for simple event inserts | P3-3/P3-7 | section 6 direct writes | add wrapper procedures, then no direct INSERT grants are needed`
* `CR-P5-4: approval_request must keep ref context | P3-1 | completion needs payment id / refund method | store in proposed_change or typed columns; web then drops reason tags`
* `CR-P5-5: Official views for reconciliation, discharge checklist, daily summary | P3-4/5/6 | duplicated SQL | publish v_ views; web calls them`

---

## 11. Quick troubleshooting

| Symptom | Likely cause | Where |
|---|---|---|
| Screen says "needs a database object that is missing or different" | errno 1305/1146/1054: missing procedure/table/column | `/_diag`, `p5_check_contract.py` |
| "Database procedure for 'x' is not installed yet ... task P3-n" | P3-n not built | build it or fix `procs` list |
| 422 "Input should be a valid integer" | a GET with an empty select value | use `web.qi(request, name)` (already done for known cases) |
| Login fails for the bootstrap owner | A-01 / A-02 | `staff_credential`, `passwords.py` |
| Everything shows 1 hour/5.5 hours off | A-05 | `APP_TIMEZONE`, `timeutil` |
| Duplicate rows after double click | form without `request_uuid` hidden input or procedure without the parameter | `macros.form`, procedure signature |
| "Form expired" (403) | CSRF token missing after session reset | refresh the page; forms must be built with `m.form(...)` |
