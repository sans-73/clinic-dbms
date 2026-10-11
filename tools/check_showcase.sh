#!/usr/bin/env bash
# Runs db/queries/showcase.sql with --force and prints ONLY the errors (and which query block they belong to).
# Usage (repo root, Git Bash):  bash tools/check_showcase.sh
docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db --force -t' < db/queries/showcase.sql 2>&1 \
  | grep -E "^ERROR" | sed 's/^/  /' || true
echo "--- done. (no ERROR lines above = every showcase query ran)"
