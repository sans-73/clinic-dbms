# Fix loop for SQL / test failures
1. `bash tools/run_sql_checked.sh` (stops at the first error and prints the file).
2. Generated file (`db/schema/*`, `db/logic/01_*`)? Fix in `tools/schema_spec.py` or `tools/sql_src/01_foundation_head.sql`, then `python tools/build_db.py`.
   Hand-written file (`db/logic/02..06`)? Fix the file directly.
3. `python tools/verify_kit.py` must pass before you commit.
4. Test FAIL? The test name starts with the rule number. Run the failing call by hand:
   `SET @app_user_id = <staff id>, @app_role = '<ROLE>';` then `CALL ...;` in the MySQL prompt.
Prompt for any AI: "This is the exact error from MySQL 8.4: <paste>. File: <name>. Return the complete corrected file. Do not change signatures."
