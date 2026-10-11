#!/usr/bin/env python3
"""Compare table names in an ARCHITECT's 02_table_design.md (or any md) with tools/schema_spec.py.
Usage: python tools/compare_design.py path/to/their_02_table_design.md
Prints: tables only in THEIR doc, tables only in OUR spec. Use the result as the agenda for reconciling (see docs/pooling/POOLING_GUIDE.md)."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import schema_spec as S
if len(sys.argv) < 2: sys.exit(__doc__)
txt = open(sys.argv[1], encoding='utf-8').read()
cands = set(re.findall(r'^#{2,4}\s+`?([a-z][a-z0-9_]+)`?', txt, re.M)) | set(re.findall(r'^\|\s*`([a-z][a-z0-9_]+)`\s*\|', txt, re.M))
mine = {t['name'] for t in S.T}
theirs = {c for c in cands if '_' in c or c in mine}
print('only in their doc  :', sorted(theirs - mine) or 'none')
print('only in our spec   :', sorted(mine - theirs) or 'none')
print(f'common: {len(theirs & mine)}   ours: {len(mine)}   theirs(detected): {len(theirs)}')
