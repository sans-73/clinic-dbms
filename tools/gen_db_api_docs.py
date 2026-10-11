#!/usr/bin/env python3
"""Generate docs/db_api/<file>.md from the '-- @api' comments of every routine (so docs can never drift from SQL)."""
import os, re, glob
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = [('foundation', 'tools/sql_src/01_foundation_head.sql'), ('patients_visits', 'db/logic/02_patients_visits.sql'),
       ('prescriptions_labs', 'db/logic/03_prescriptions_labs.sql'), ('pharmacy', 'db/logic/04_pharmacy.sql'),
       ('billing_discharge', 'db/logic/05_billing_discharge.sql'), ('owner_links', 'db/logic/06_owner_alerts_links.sql')]
for name, rel in SRC:
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p): continue
    t = open(p, encoding='utf-8').read()
    out = [f'# DB API: {name}', '', f'_Generated from `{rel}` by `tools/gen_db_api_docs.py`. Do not edit by hand._', '',
           'Own-txn procedures commit by themselves: call them in autocommit mode. Rule violations raise SQLSTATE 45000 with a readable message.', '',
           '| routine | kind | roles | rules | meaning | parameters |', '|---|---|---|---|---|---|']
    for m in re.finditer(r'--\s*@api\s+roles=(\S*)\s+rules=(\S*)\s*\|\s*([^\n]*)\n\s*CREATE\s+(PROCEDURE|FUNCTION)\s+`?(\w+)`?\s*\(', t):
        i = m.end(); d = 1
        while d: d += {'(': 1, ')': -1}.get(t[i], 0); i += 1
        params = re.sub(r'\s+', ' ', t[m.end():i - 1]).strip()
        out.append(f'| `{m.group(5)}` | {m.group(4).lower()} | {m.group(1)} | {m.group(2)} | {m.group(3).strip()} | `{params}` |')
    views = re.findall(r'CREATE OR REPLACE VIEW (\w+)', t)
    if views: out += ['', '## Views', ''] + [f'- `{v}`' for v in views]
    os.makedirs(os.path.join(ROOT, 'docs/db_api'), exist_ok=True)
    open(os.path.join(ROOT, 'docs/db_api', name + '.md'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    print('wrote docs/db_api/%s.md' % name)
