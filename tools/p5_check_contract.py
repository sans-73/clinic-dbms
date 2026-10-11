"""Compares the web app's expectations with the REAL database. Run after every schema/logic change.
    python tools/p5_check_contract.py            -> procedures + columns report
Needs MySQL running and .env set (same settings the app uses). Read-only."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db, diagnostics  # noqa: E402


def main() -> int:
    print("== Stored procedures (app/contract.py) ==")
    rows = diagnostics.contract_report(None)
    bad = 0
    for r in rows:
        mark = "OK     " if r["status"] == "OK" else r["status"].ljust(7)
        if r["status"] != "OK":
            bad += 1
        print(f"{mark} {r['key']:<26} task {r.get('task', ''):<5} -> {r.get('proc', '-'):<34} {r.get('detail', '')}")
    print(f"\n{len(rows) - bad} OK, {bad} need attention\n")
    print("== Tables and columns used directly by SQL ==")
    sbad = 0
    for r in diagnostics.schema_report(None):
        if r["status"] != "OK":
            sbad += 1
            print(f"{r['status']:<16} {r['table']:<34} missing: {', '.join(r['missing'])}")
    print(f"\n{sbad} table(s) need attention")
    return 1 if (bad or sbad) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except db.RuleError as e:
        print("Cannot check:", e)
        sys.exit(2)
