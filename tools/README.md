# tools/ - Phase 4 tools (seed, bulk data, benchmarks)

Owned by Phase 4: `p4_lib.py`, `p4_fake.py`, `seed_demo.py`, `gen_bulk_data.py`, `run_benchmarks.py`, `check_showcase.sh`, `requirements-phase4.txt`.
(Other tools in this folder belong to the schema/logic track - do not overwrite them.)

## One-time install (Git Bash, repo root)
```bash
python -m venv .venv && source .venv/Scripts/activate      # Mac/Linux: source .venv/bin/activate
pip install -r tools/requirements-phase4.txt
```
MySQL is reachable from Python on **127.0.0.1:3307** (docker-compose maps 3307->3306). The password is read from `.env`.

## A. Demo seed (clinic_db)  - P4-1
```bash
docker compose down -v && docker compose up -d && docker compose ps       # wait for "healthy"
bash tools/run_sql_checked.sh                                              # schema + logic (other track)
python tools/bootstrap_owner.py ...                                        # first OWNER must exist (other track)
python tools/seed_demo.py --dry-run     # no DB: proves the python logic runs
python tools/seed_demo.py --check       # real DB, everything rolled back: prints the CONTRACT REPORT
python tools/seed_demo.py               # real seed; writes db/seed/DEMO_CREDENTIALS.md
```
Success: report says `No failed units.`, `SELECT COUNT(*) FROM patient;` is about 41.

## B. Bulk data (clinic_bench)  - P4-2
```bash
python tools/gen_bulk_data.py --check --recreate      # seconds: proves column names fit your schema
python tools/gen_bulk_data.py --scale small  --recreate   # 5k patients, ~2 min
python tools/gen_bulk_data.py --scale full   --recreate   # 100k patients / 300k visits / 1M audit rows (~15-30 min)
```
Check: `docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT COUNT(*) FROM clinic_bench.patient"'`.

## C. Benchmarks  - P4-3
```bash
python tools/run_benchmarks.py --check     # EXPLAINs all queries, lists wrong column names
python tools/run_benchmarks.py             # writes docs/performance/benchmarks.md + results.json + img/*.png
```

## D. Query showcase  - P4-4
```bash
bash tools/check_showcase.sh               # prints only errors; empty = all 53 queries run
```
Full output for screenshots: `docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db -t' < db/queries/showcase.sql`

Troubleshooting: see `docs/PHASE4_GUIDE.md` section 5.
