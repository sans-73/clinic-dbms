# Clinic web app (Phase 5)

FastAPI + Jinja2 (server-rendered HTML, a little vanilla JS). **All business rules live in MySQL**; this layer only calls stored procedures
(`app/contract.py`) and read-only SQL (`app/data/*.py`).

```
app/
  main.py          app factory; auto-loads every app/routes/*.py (module must expose `router`)
  config.py        env / .env settings (DB_MODE dev|roles|stub, ports, timezone ...)
  db.py            THE ONLY place that talks to MySQL: sets @app_user_id/@app_role, calls procedures by NAME-matched args
  contract.py      every stored procedure the app calls (names + argument names)  <- edit here when P3 names differ
  auth.py          login, role ("hat") choice, require(...) guard
  web.py           render(), flash, CSRF, do() POST wrapper, form helpers
  data/            SQL + procedure calls per area (patients, clinical, pharmacy, nurse, billing, owner, family)
  routes/          URL handlers per area (session, reception, doctor, pharmacy, nurse, billing, owner, family, files)
  templates/ static/
```
Run (repo root):  `uvicorn app.main:app --reload --port 8000`   |   no database:  `DB_MODE=stub uvicorn app.main:app --reload`
Full instructions: `docs/phase5/START_HERE_P5_HINGLISH.md`; integration guide: `docs/phase5/PHASE5_HANDOFF_GUIDE.md`.
