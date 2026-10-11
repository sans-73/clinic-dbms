"""Writes docs/phase5/DB_CONTRACT.md from app/contract.py (the exact list of database procedures the web app calls).
    python tools/p5_gen_contract_doc.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.contract import CONTRACT  # noqa: E402

out = ["# DB contract: stored procedures the Phase 5 web app calls", "",
       "Generated from `app/contract.py` by `tools/p5_gen_contract_doc.py`. Do not edit by hand.", "",
       "**How matching works:** the web app finds the procedure by the first name that exists in MySQL, then passes arguments **by parameter name** "
       "(leading `p_` ignored, common synonyms handled). Missing parameters get NULL. `request_uuid` is auto-filled. OUT parameters are read back by name.",
       "", "A DB-LOGIC task can either implement these exact names/arguments, or the contract can be changed to match the real procedures.", ""]
by_task: dict[str, list] = {}
for k, v in CONTRACT.items():
    by_task.setdefault(v["task"], []).append((k, v))
for task in sorted(by_task):
    out += [f"## {task}", "", "| Key | Procedure (candidates) | Arguments the web app sends | OUT read back | Notes |", "|---|---|---|---|---|"]
    for k, v in by_task[task]:
        out.append(f"| `{k}`{' (FIXED in TASKS.md)' if v['known'] else ''} | {' / '.join('`' + p + '`' for p in v['procs'])} | "
                   f"{', '.join(v['args']) or '-'} | {', '.join(v['out']) or '-'} | {v['note']} |")
    out.append("")
p = ROOT / "docs" / "phase5" / "DB_CONTRACT.md"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text("\n".join(out), encoding="utf-8")
print("wrote", p, f"({len(CONTRACT)} procedures)")
