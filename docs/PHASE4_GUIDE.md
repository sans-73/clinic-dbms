# PHASE 4 GUIDE - handoff to the main agent (read sections 0, 3, 4, 5 first; the rest only when needed)

Written by an independent Phase-4 team member. Goal of this file: let you (the agent with the full project context) **verify, adapt and
merge Phase 4 in a few small steps and few tokens**, without re-reading or regenerating the code.

## 0. Status in 10 lines
* Delivered: P4-1 seed, P4-2 bulk generator, P4-3 benchmarks, P4-4 query showcase + theory, B1 C++ simulators.
* **NOT run on a real MySQL** (none was available). Python logic was dry-run, C++ was compiled and run, SQL syntax was reviewed by hand only.
* Written against **`docs/design/01_er_model.md`** column names (the real schema was generated from the same ER by the other track).
  Every place where my guess may differ is *reported automatically* by `--check` modes, so fixing is a loop of small edits, not a rewrite.
* Phase 4 does **not edit** any file owned by other tasks. It only adds files (section 1). No change to `run_all.sql`.
* Seed is a **Python loader**, not `demo_seed.sql` (reason: D1). Everything else matches the task cards.
* Likely first-run issues: 2-10 wrong column/enum names. Expected effort: 1-3 fix rounds of < 10 lines each.

## 1. Files (all new; copy the zip contents into the repo root)
| Path | Card | Purpose | Edit when |
|---|---|---|---|
| `tools/p4_lib.py` | all | schema-aware loader: reads information_schema, fixes enum spelling, fills event columns, auto-fills other NOT NULL columns, reports differences | rarely |
| `tools/p4_fake.py` | all | fake Indian names, phones, medicine names | never |
| `tools/seed_demo.py` | P4-1 | demo dataset: 8 staff, 29 medicines, ~39 batches, ~41 patients, ~36 visits, 7 stories | **column/enum/procedure name fixes**; section "ADAPTER" at the top |
| `tools/gen_bulk_data.py` | P4-2 | loads `clinic_bench` (schema files only, no triggers) | column names in the `L.bulk(...)` column lists |
| `db/queries/benchmark_queries.sql` | P4-3 | 11 queries + index DDL as `-- @` directives | column names |
| `tools/run_benchmarks.py` | P4-3 | runs before/after, writes `docs/performance/benchmarks.md`, `results.json`, `img/*.png` | rarely |
| `db/queries/showcase.sql` | P4-4 | 53 labelled queries (joins, set ops, division, windows, CTE, recursive CTE...) | column names |
| `docs/queries_theory.md` | P4-4 | relational algebra + TRC/DRC for 10 queries, completeness, optimization | never |
| `docs/performance/index_structures.md` | P4-3 | clustered/secondary/B+/hash/FULLTEXT/partition explained | never |
| `bonus/bplus_tree.cpp`, `extendible_hash.cpp`, `bonus/README.md` | B1 | run-and-watch simulators (tested) | never |
| `tools/check_showcase.sh`, `tools/README.md`, `tools/requirements-phase4.txt` | - | commands | - |
| generated later | | `db/seed/DEMO_CREDENTIALS.md`, `docs/performance/benchmarks.md`, `results.json`, `img/*.png`, `tools/.bench_info.json` | commit all except `.bench_info.json` (add to .gitignore) |

## 2. Decisions I took (so you can overrule them) - each is one line to change
| # | Decision | Why | If you disagree |
|---|---|---|---|
| D1 | Seed = `tools/seed_demo.py` with direct INSERTs **while real triggers are active**; only the shared procedures `raise_alert`, `request_approval`, `decide_approval`, `queue_message`, `next_code` are CALLed | P3-3..P3-6 procedures did not exist; their signatures are unknown. Triggers still prove append-only, hat check, maker-checker | after P3-3..P3-6: swap stories per section 6 table |
| D2 | Every visit / story is a **SAVEPOINT unit**: a failing unit is rolled back alone and listed in the report | first run shows all problems at once | `L.unit` in p4_lib |
| D3 | `clinic_bench` = `db/schema/01..04,09` only, **no triggers/procedures**, FK checks off during load, explicit PKs | 352 triggers would slow 3M inserts 10x and benchmarks measure indexes, not triggers | add `db/logic/01_*.sql` to `SCHEMA_FILES` (not recommended) |
| D4 | Historical seed rows carry `is_backfilled = 1` (occurred_at older than 10 min), today's live visits do not | foundation triggers check `is_backfilled` rules (P3-1 item 3) | change threshold in `Loader.esc_values` |
| D5 | Staff passwords: seeder imports `app.security` and uses the first function it finds in `HASHER_CANDIDATES` | web app (scrypt, CR-B1) not visible to me | set the real function name; else `staff_credential` is skipped with a visible warning |
| D6 | Benchmark indexes are named `bx_*` and created **only in clinic_bench** | never pollute the real schema | after the benchmark, promote winners into `tools/schema_spec.py` (section 7) |
| D7 | Partitioning is demonstrated on a **copy** `bx_audit_part` | InnoDB partitioned tables cannot have FKs, and the PK/UNIQUE keys must contain the partition column | do not partition real tables in V1 |
| D8 | Showcase queries use base tables, own views are prefixed `vs_` | independent of the other track's view names | - |
| D9 | Showcase S50 uses `levenshtein()` from P3-2; S52 FULLTEXT index is commented | cross-phase dependency, kept visible | - |

## 3. Contract: what Phase 4 assumes (verify with the three `--check` commands, never by reading code)
1. Column names = ER model attribute names; PK = `<table>_id`; event tables carry `occurred_at, recorded_at, recorded_by, recorded_by_role, is_backfilled, request_uuid`.
2. Enum-like columns accept the ER spellings (`entry_type: appointment|walk_in|emergency`, statuses of R3.3, `movement_type`, `source_type`, ...). `p4_lib` auto-maps case/underscore/prefix differences and prints them.
3. `@app_role` holds the **role_code string** (e.g. `OWNER`), as in the TASKS.md check `SET @app_user_id=1, @app_role='OWNER'`.
4. A first OWNER exists (bootstrap_owner.py), the roles `OWNER, DOCTOR, RECEPTIONIST, NURSE, PHARMACY, ACCOUNTANT, SYSTEM` exist or can be inserted by the owner, and at least one `staff.principal_kind='system'` row exists (used for slot generation and alerts; falls back to the owner).
5. Weak entities (`prescription_line`, `bill_line`, ...) have a surrogate `<table>_id` as in the ER; seed falls back to a SELECT if `lastrowid` is 0.
6. `doctor_schedule.weekday` is 1-7 (ISO). If the schema uses 0-6 set `WEEKDAY_STYLE = "mysql"` in seed_demo.py.
7. Family link tokens are stored as `sha256(token)` hex. If P3-6 hashes differently, edit **one line** in `story_family`.
8. Docker MySQL is reached on 127.0.0.1:3307 (see docker-compose.yml); env overrides: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`.

## 4. Verification protocol (cheap, in this order)
```bash
pip install -r tools/requirements-phase4.txt
python tools/seed_demo.py --dry-run                 # python only (already passes)
python tools/seed_demo.py --check                   # real DB, rolled back, full contract report
python tools/seed_demo.py                           # real
bash tools/check_showcase.sh                        # only ERROR lines
python tools/gen_bulk_data.py --check --recreate    # seconds
python tools/run_benchmarks.py --check
python tools/gen_bulk_data.py --scale small --recreate && python tools/run_benchmarks.py     # then --scale full
```
Gate for Phase 4: seed report `No failed units`, showcase prints no ERROR, `clinic_bench.patient` >= 100000 (full scale), `benchmarks.md` has numbers and 2 PNGs.

## 5. Fix loop and troubleshooting (token-saving rules for you)
**Never paste whole files back to a model.** Paste only: (a) the report block "COLUMNS I ASSUMED BUT SCHEMA LACKS" / "FAILED UNITS", (b) the `SHOW CREATE TABLE x\G` of the table named in the first line.
Edit the named line; re-run only the same `--check`.

| Symptom (first line of the report) | Cause | Fix |
|---|---|---|
| `table X does not exist` | schema uses another table name | rename in the `L.insert("X"...)`; `grep -n '"X"' tools/seed_demo.py` |
| `unknown column t.c (did you mean: d)` | column renamed | rename key `c` -> `d` in the dict (same for `L.bulk` column lists, `.sql` files) |
| `enum_unmatched t.c: 'v' not in [...]` | status value spelled differently | change value `v` |
| `[table] IntegrityError ... CHECK constraint` | a CHECK from the schema (e.g. dispensing source matrix, event-column rules) | look at the CHECK in `SHOW CREATE TABLE`; adapt the row |
| `unit 'visit#n' FAILED: ... trigger message` | a P3-1/P3-2 trigger refuses (hat check, closed visit, double booking...) | message text names the rule; fix actor/order in `run_case` |
| `NOT NULL columns auto-filled` list is long | schema has extra mandatory columns I do not know | add real values in the row dict (do not leave `n/a`/0 in FK columns) |
| `Access denied` / `Can't connect` | wrong password/port | `.env` has `MYSQL_ROOT_PASSWORD`; port is 3307 |
| bulk: `Data too long` / `Out of range` | width of a column (e.g. receipt_number) | shorten the generated string |
| bench: partition DDL fails | `recorded_at` type/other UNIQUE keys | read the error; `build_partition_copy` handles TIMESTAMP and drops UNIQUE keys; if the table has FK-less assumptions broken, skip Q11 with `--only Q1,...,Q10` |
| EXPLAIN speed-up ~x1 | the real schema already has that index | valid result; write it in the report |

## 6. Cross-phase impact (this is where Phase 4 touches other tasks)
**Needed from P3 (earlier phases)**
* P3-1 `raise_alert`, `request_approval`, `decide_approval`, `queue_message`, `next_code` signatures exactly as in TASKS.md. Request types used: `partial_payment_discharge`, `discount`, `stock_adjustment`; alert types: `ALLERGY_OVERRIDE`, `STAY_OVER_LIMIT`, `DISCOUNT_GIVEN`, `MANUAL_PRICE`, `OTC_PATTERN` (cosmetic strings; change freely).
* P3-2 `levenshtein()` for showcase S50 only.
* `next_code` kinds tried: `patient`, `PATIENT`, `temp_patient`, `TEMP`, `TMP`, `unknown_patient`. If none works the seed formats codes itself and prints a warning: then **set `id_counter` above the highest seeded number** (`SELECT * FROM id_counter;`) or P3-2 `register_patient` will create duplicate codes.

**Provided to later phases**
| Consumer | What Phase 4 gives | Notes |
|---|---|---|
| P3-4 / P3-8 / P6-1 | batch `DEMO-LASTSTRIP` (Zerostock Demo 10, qty 1) for the "last strip sold twice" demo; expired batch `CF-EXPIRED` (Cefix 200); near-expiry `AZ-NEAR`; medicine with no stock `Levoflox 500` | Schedule H items for the OTC-block test: Azithral 500, Amoxiclav 625 |
| P3-8 | rule tests create their own rows; do not depend on the seed | seed only for demos |
| P3-3 | `ft_medicine_names` FULLTEXT recommendation, free-text + allergy story, catalog review queue row | `medicine_suggest` colour logic = showcase [S30] |
| P3-5 | a bill with draft/finalized/settled mix, a partial-payment discharge with `due_promise`, an approval waiting | state logic = showcase [S38] |
| P3-6 | open stay > 24 h (Anil Kumar), unreviewed critical lab report, billed-no-report lab order, OTC pattern, backfilled rows | expected `check_alerts()` hits |
| P3-7 | 8 staff, one per role, password `Demo@1234` | see `db/seed/DEMO_CREDENTIALS.md` after seeding |
| P5-x | 4 live visits today (queue board), duplicate pairs (Ramesh Sharma / Rameshwar Sarma ...), family link tokens + DOB, receipts | everything is listed in DEMO_CREDENTIALS.md |
| P6-4 "A Day at the Clinic" | story IDs file (same) | |
| P7-1 | `benchmarks.md`, `queries_theory.md`, `index_structures.md`, `bonus/` = Module 3 and 6 proof | |

**When P3-3..P3-6 exist, upgrade the seed (optional, 1 small task):** replace in `seed_demo.py`

| Seed function | Replace the raw inserts by |
|---|---|
| `prescribe`, `story_allergy` | prescription create/new-version + item-add procedures + allergy confirmation procedure |
| `dispense`, `story_counter` | `dispense` (3 sources); this also creates stock movement + bill line (then delete the manual `make_bill` medicine lines) |
| `story_stay` | `administer_dose`, `ask_leftover_return`, `decide_leftover` |
| `make_bill`, `finalize_and_pay`, `discharge`, `story_partial` | `ensure_open_bill`, `bill_add_line`, payment procedure, `request_partial_payment_discharge`, `discharge_visit` |
| `story_family` | create-link procedure (token comes from the procedure; print it) |

**Decision for you (D4):** the seed makes the owner dashboard show many "backdating" signals because history is backfilled. For a clean demo either (a) seed history with `occurred_at` = now and accept a flat timeline, or (b) filter seed rows in the R10.5 views by `recorded_by` = demo staff. I recommend (b) later; nothing to do now.

## 7. After the benchmark: promote indexes into the real schema (small CR for the schema track)
Edit **`tools/schema_spec.py`** (do not hand-edit `db/schema/*`), then `python tools/build_db.py`. Candidates (keep only those with speed-up >= x3 in `benchmarks.md`):
`patient(family_name, given_name)`, `patient_phone(phone_number)`, `patient(date_of_birth)`, `visit(occurred_at)`, `stock_batch(expiry_date)`,
`stock_batch(medicine_id, expiry_date)`, `stock_movement(stock_batch_id, quantity_delta)`, `payment(recorded_at)`, `audit_log(recorded_by, recorded_at)`,
`FULLTEXT medicine(brand_name, generic_display_name)`. Skip the MEMORY hash table and partitioning in V1 (D7). Log it as CR-P4-1 in `PROGRESS.md`.

## 8. Honest limitations
* No MySQL available to me: SQL and the loader were never executed against the real schema (see section 0).
* Some `UNIQUE`/`CHECK` rules in the schema may reject a seed row; the unit report names each one.
* Bulk data uses OTC counter sales as the stock-out source (needs only `counter_sale`), not full prescriptions; fine for index benchmarks, not for business reports on clinic_bench.
* Timing numbers depend on the laptop and Docker; always re-run on the demo machine before quoting them.
* Showcase S42 (R6.2) and S47/S48 depend on seed stories that may fail the first time; the queries themselves are still valid SQL.

## 9. Commit sequence (Git Bash, repo root; one commit per card keeps the log readable)
```bash
git add tools/p4_*.py tools/seed_demo.py tools/requirements-phase4.txt tools/README.md db/seed && git commit -m "P4-1: demo seed"
git add tools/gen_bulk_data.py && git commit -m "P4-2: bulk data generator"
git add db/queries/benchmark_queries.sql tools/run_benchmarks.py docs/performance && git commit -m "P4-3: benchmarks"
git add db/queries/showcase.sql docs/queries_theory.md tools/check_showcase.sh && git commit -m "P4-4: query showcase"
git add bonus && git commit -m "B1: C++ index simulators"
git add docs/PHASE4_GUIDE.md && git commit -m "P4: handoff guide" && git push
```

## 10. Paste-ready prompt for the main agent
```
You hold the full project context. Phase 4 was built independently; read ONLY docs/PHASE4_GUIDE.md (sections 0,3,4,5).
Task: (1) tell me the exact commands to verify Phase 4 on our real schema; (2) when I paste a --check report, return the minimal
edits (file, old line -> new line), nothing else; (3) apply the cross-phase items of section 6 that conflict with the real P3 code.
Do not regenerate whole files. If a rule in PROJECT_CONTEXT.md conflicts with my choice (D1-D9), write a CR instead of changing it.
```
