# Clinic Management System (DBMS Project) - Track B kit

Layout equals the repo layout. Read `docs/pooling/TRACK_B_POOLING_GUIDE.pdf` (or `.md`) first.
Quick check: `python tools/verify_kit.py`   |   Rebuild generated SQL: `python tools/build_db.py`   |   Load DB: `bash tools/run_sql_checked.sh`
Single source of truth for the schema: `tools/schema_spec.py` (implements `docs/design/01_er_model.md` rev 2).
> Fake patient data only. Never commit `.env` or password hashes.
