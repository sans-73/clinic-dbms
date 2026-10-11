#!/usr/bin/env bash
# Runs the SQL files in order against the running container and STOPS at the first error, naming the file.
# Usage:  bash tools/run_sql_checked.sh            (reset + all files)      |   bash tools/run_sql_checked.sh --no-reset
set -u
cd "$(dirname "$0")/.."
run() { docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '"$2" < "$1" 2>&1; }
[ "${1:-}" = "--no-reset" ] || { echo "== reset"; docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD"' < db/reset.sql 2>&1 | grep -v "Using a password" ; }
FILES="db/schema/01_core_settings_staff_audit.sql db/schema/02_patients_visits.sql db/schema/03_pharmacy.sql db/schema/04_orders_billing_discharge.sql db/schema/09_foreign_keys.sql db/logic/01_foundation_triggers.sql"
for f in db/logic/0[2-9]_*.sql db/security/*.sql; do [ -f "$f" ] && FILES="$FILES $f"; done
for f in $FILES; do
  out=$(run "$f" clinic_db | grep -v "Using a password")
  if echo "$out" | grep -q "^ERROR"; then echo "FAILED in $f"; echo "$out" | head -5; echo "-> paste this to the AI that owns the file (see docs/pooling/FIX_LOOP.md)"; exit 1; fi
  echo "ok  $f"
done
echo "ALL FILES LOADED"
