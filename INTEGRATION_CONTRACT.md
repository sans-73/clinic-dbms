# INTEGRATION CONTRACT (paste this into EVERY AI session that works on Phase 4, 5, 6 or 7)

You are adding work to an existing system. The database layer is finished and verified statically. **Do not re-implement rules; call the database.**

## 1. Facts you must rely on (do not invent others)
- Tables: exactly the 104 in `docs/design/02_table_design.BASELINE.md` (generated from `tools/schema_spec.py`). Column names are final unless a CR says otherwise.
- Public API: every stored procedure and view in `docs/db_api/*.md` (generated). Use ONLY those names and argument lists. Roles allowed per procedure are in the same files.
- Event tables are append-only and every write goes through a procedure (`sp_*`). There are NO table privileges for any account.
- Status values are lower-case snake_case codes (`checked_in`, `in_consultation`, `bought_outside` ...).

## 2. Connection rules (web app, scripts, seed, tests)
1. Use autocommit (own-transaction procedures start their own transaction).
2. First statements on every connection: `SET time_zone = '+00:00'; SET @app_user_id = <staff_id>; SET @app_role = '<ROLE>';` (role = the "hat" the person chose; the database re-validates it).
3. Optional per request: `SET @request_uuid = '<uuid>'; SET @req_seq = 0;` so a double submit becomes one write (duplicate-key error 1062 = "already saved").
4. MySQL account = `clinic_<role>` (e.g. `clinic_nurse`); login lookup uses `clinic_login` (`auth_get_credentials`, `auth_log_event`); public family pages use `clinic_family` (`sp_open_family_link` and `v_family_*` views only).
5. Procedures with OUT parameters: pass user variables (`CALL sp_x(%s, @o1)` then `SELECT @o1`). Use the helper `call(conn, "sp_x", args, outs=n)` in `app/db.py` (created by task P5-1; the verifier checks every name used this way against the SQL).
6. Rule violations arrive as SQLSTATE 45000 with a readable message: show it as is. Errors 1142/1370 = not allowed for this role. 1062 = duplicate submit.
7. Parameterized queries only. Never build SQL from user text.

## 3. What each phase must deliver
- **P4-1 seed**: only through procedures (set the session context per actor). Fake data only. Demo staff are created with `create_staff` (password hashes from `app/security.py`, never plain text). Needs price list items `CONSULT` and `STAY_DAYCARE` (via `sp_set_price`) or visits get no automatic charge.
- **P4-2 bulk data**: load into a SEPARATE database `clinic_bench` created from `db/schema/*.sql` only (no triggers). Because triggers are absent, fill `recorded_by` (any staff id), `recorded_by_role` (a role code), `request_uuid` explicitly.
- **P4-3 benchmarks**: queries come from the views/procedures in `docs/db_api`; record results in `benchmark_run` (or markdown) and never change table definitions without a CR.
- **P4-4 showcase**: read-only SQL on tables/views from the baseline; label each query with its syllabus topic.
- **P5 app**: `app/routes/<name>.py` + `app/templates/<name>/`; never edit shared files (`main.py`, `db.py`, `auth.py`, `base.html`) without a CR. Screens only call API names from `docs/db_api`.
- **P6/P7 docs/demos**: use procedure names and rule numbers exactly as in `docs/db_api` and `db/tests/rule_tests.sql`.

## 4. Before you hand back
Run `python tools/verify_kit.py` (must print "all checks passed"). If you needed anything the API does not offer, do NOT work around it: write a CR (`CR: title | Affects | Problem | Proposal`).
End with a HANDOFF NOTE (Done / Files / How to check / Assumptions / Open issues).
