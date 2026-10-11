#!/usr/bin/env python3
"""Generate db/schema/*.sql, db/schema/09_foreign_keys.sql, db/logic/01_foundation_triggers.sql and the baseline design doc.
Run from the repo root:  python tools/build_db.py
"""
import os, re, sys, collections
sys.path.insert(0, os.path.dirname(__file__))
import schema_spec as S

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CLUSTER_FILES = {1: '01_core_settings_staff_audit', 2: '02_patients_visits', 3: '03_pharmacy', 4: '04_orders_billing_discharge'}
TYPES = {'B': 'BIGINT UNSIGNED', 'D': 'DATE', 'DT': 'DATETIME(3)', 'DT0': 'DATETIME', 'T': 'TIME', 'I': 'INT', 'SI': 'SMALLINT',
         'TI': 'TINYINT(1)', 'TN': 'TINYINT', 'M': 'DECIMAL(12,2)', 'Q': 'DECIMAL(10,2)', 'N3': 'DECIMAL(10,3)', 'TXT': 'TEXT', 'J': 'JSON'}


def parse_col(spec):
    m = re.match(r'^(\w+)(\?)?:(.*)$', spec)
    if not m:
        raise SystemExit(f'bad column spec: {spec}')
    name, nullable, rest = m.group(1), bool(m.group(2)), m.group(3)
    fk = None
    if '->' in rest:
        rest, fk = rest.split('->', 1)
        fk = fk.strip()
    default = None
    if '=' in rest and not rest.startswith('E('):
        rest, default = rest.split('=', 1)
    elif rest.startswith('E(') and ')=' in rest:
        rest, default = rest.split(')=', 1)
        rest += ')'
    rest = rest.strip()
    enum = None
    if rest.startswith('E('):
        enum = rest[2:-1].split('|')
        sql = f'VARCHAR({max(len(v) for v in enum)})'
        sql = f'VARCHAR({max(len(v) for v in enum) + 4})'
    elif re.match(r'^V\d+$', rest):
        sql = f'VARCHAR({rest[1:]})'
    elif re.match(r'^C\d+$', rest):
        sql = f'CHAR({rest[1:]})'
    elif rest.startswith('H') and rest[1:].isdigit():
        sql = f'CHAR({rest[1:]})'
    elif rest in TYPES:
        sql = TYPES[rest]
    elif rest.startswith('DECIMAL'):
        sql = rest
    else:
        raise SystemExit(f'unknown type {rest} in {spec}')
    return dict(name=name, nullable=nullable, sql=sql, default=default, fk=fk, enum=enum, is_bool=(rest == 'TI'))


def col_sql(c):
    s = f"`{c['name']}` {c['sql']} {'NULL' if c['nullable'] else 'NOT NULL'}"
    if c['default'] is not None:
        d = c['default']
        s += f' DEFAULT {d}' if (d.startswith('(') or d.startswith('CURRENT_TIMESTAMP') or re.match(r"^-?\d+(\.\d+)?$", d) or d.startswith("'")) else f" DEFAULT '{d}'"
    elif c['nullable']:
        s += ' DEFAULT NULL'
    return s


ESC = ['occurred_at:DT=CURRENT_TIMESTAMP(3)', 'recorded_at:DT=CURRENT_TIMESTAMP(3)', 'recorded_by:B=0->staff',
       'recorded_by_role:V20=\'\'->role.role_code', 'is_backfilled:TI=0', 'request_uuid:C36=(UUID())']


def pk_of(t):
    return t['pk'] if t['pk'] else [f"{t['name']}_id"]


def build_model():
    tabs = {}
    for t in S.T:
        if t['name'] in tabs:
            raise SystemExit('duplicate table ' + t['name'])
        cols = []
        if not t['pk']:
            cols.append(dict(name=f"{t['name']}_id", nullable=False, sql='BIGINT UNSIGNED', default=None, fk=None, enum=None, is_bool=False, auto=True))
        for s in t['cols']:
            cols.append(parse_col(s))
        if t['kind'] == 'E':
            for s in ESC:
                cols.append(parse_col(s))
            if t['corrects']:
                cols.append(parse_col(f"corrects_id?:B->{t['name']}"))
        elif t['kind'] == 'M':
            cols += [parse_col('created_at:DT=CURRENT_TIMESTAMP(3)'), parse_col('created_by?:B'),
                     dict(parse_col('updated_at:DT=CURRENT_TIMESTAMP(3)'), onupdate=True), parse_col('updated_by?:B')]
        t['columns'] = cols
        t['pkcols'] = pk_of(t)
        tabs[t['name']] = t
    return tabs


def fk_list(t, tabs):
    """returns list of dict(name, cols, parent, pcols)"""
    out, used = [], set()
    def add(cols, parent, pcols, tag=''):
        base = f"fk_{t['name']}_{parent}{tag}"
        nm = base if base not in used else f"fk_{t['name']}_{'_'.join(cols)}"
        nm = nm[:64]
        used.add(nm)
        out.append(dict(name=nm, cols=cols, parent=parent, pcols=pcols))
    for c in t['columns']:
        if c['fk']:
            parent, _, pcol = c['fk'].partition('.')
            if parent not in tabs:
                raise SystemExit(f"{t['name']}.{c['name']} -> unknown table {parent}")
            pcols = [pcol] if pcol else tabs[parent]['pkcols']
            add([c['name']], parent, pcols)
    for cols, parent, pcols in t['fk_extra']:
        add(cols, parent, pcols, '_c')
    return out


def check_list(t):
    chk = []
    for c in t['columns']:
        if c['enum']:
            vals = ','.join(f"'{v}'" for v in c['enum'])
            expr = f"`{c['name']}` IN ({vals})"
            chk.append(f"`{c['name']}` IS NULL OR {expr}" if c['nullable'] else expr)
        if c['is_bool']:
            chk.append(f"`{c['name']}` IN (0,1)" if not c['nullable'] else f"`{c['name']}` IS NULL OR `{c['name']}` IN (0,1)")
    chk += t['checks']
    return chk


def create_table_sql(t, tabs, deferred_fks, created):
    lines = []
    for c in t['columns']:
        s = col_sql(c)
        if c.get('auto'):
            s += ' AUTO_INCREMENT'
        if c.get('onupdate'):
            s += ' ON UPDATE CURRENT_TIMESTAMP(3)'
        lines.append(s)
    lines += t['raw']
    lines.append('PRIMARY KEY (' + ', '.join(f'`{c}`' for c in t['pkcols']) + ')')
    for i, u in enumerate(t['uniques'], 1):
        lines.append(f"UNIQUE KEY `uq_{t['name']}_{i}` (" + ', '.join(f'`{c}`' for c in u) + ')')
    if t['kind'] == 'E':
        lines.append(f"UNIQUE KEY `uq_{t['name']}_request_uuid` (`request_uuid`)")
    for i, ix in enumerate(t['indexes'], 1):
        lines.append(f"KEY `ix_{t['name']}_{i}` (" + ', '.join(f'`{c}`' for c in ix) + ')')
    for fk in fk_list(t, tabs):
        stmt = (f"CONSTRAINT `{fk['name']}` FOREIGN KEY (" + ', '.join(f'`{c}`' for c in fk['cols']) +
                f") REFERENCES `{fk['parent']}` (" + ', '.join(f'`{c}`' for c in fk['pcols']) + ')')
        if fk['parent'] == t['name'] or fk['parent'] in created:
            lines.append(stmt)
        else:
            deferred_fks.append((t['name'], stmt))
    for i, ck in enumerate(check_list(t), 1):
        lines.append(f"CONSTRAINT `ck_{t['name']}_{i}` CHECK ({ck})")
    body = ',\n  '.join(lines)
    cm = t['comment'].replace("'", "''")
    return (f"CREATE TABLE `{t['name']}` (\n  {body}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
            f"COLLATE=utf8mb4_0900_ai_ci COMMENT='{cm}';\n")


def order_cluster(names, tabs, created_before):
    """topological order inside one cluster; cycles are broken by deferring FKs"""
    remaining, order = list(names), []
    created = set(created_before)
    while remaining:
        progressed = False
        for n in list(remaining):
            deps = {fk['parent'] for fk in fk_list(tabs[n], tabs)} - {n}
            deps_in_cluster = {d for d in deps if d in names}
            if deps_in_cluster <= created:
                order.append(n); created.add(n); remaining.remove(n); progressed = True
        if not progressed:  # cycle: take the first, FKs to uncreated parents get deferred
            n = remaining.pop(0); order.append(n); created.add(n)
    return order


def gen_schema():
    tabs = build_model()
    created, deferred, build_order = set(), [], []
    for cl in sorted(CLUSTER_FILES):
        names = [t['name'] for t in S.T if t['cluster'] == cl]
        order = order_cluster(set(names), tabs, created)
        # keep original order where possible: re-sort by topo result
        parts = [f"-- GENERATED by tools/build_db.py from tools/schema_spec.py - do not edit by hand.\n"
                 f"-- Cluster {cl}: {CLUSTER_FILES[cl]}   ({len(order)} tables)\nUSE clinic_db;\nSET FOREIGN_KEY_CHECKS = 1;\n"]
        local_created = set(created)
        for n in order:
            parts.append(create_table_sql(tabs[n], tabs, deferred, local_created))
            local_created.add(n)
            build_order.append(n)
        created = local_created
        if cl in S.SEEDS:
            parts.append('-- ---- seed rows (run before triggers exist) ----\n' + S.SEEDS[cl].strip() + '\n')
        write(f'db/schema/{CLUSTER_FILES[cl]}.sql', '\n'.join(parts))
    fk_lines = ['-- GENERATED by tools/build_db.py - cross-file / cyclic foreign keys (context section 9).', 'USE clinic_db;']
    for child, stmt in deferred:
        fk_lines.append(f"ALTER TABLE `{child}` ADD {stmt};")
    write('db/schema/09_foreign_keys.sql', '\n'.join(fk_lines) + '\n')
    return tabs, deferred, build_order


# ------------------------------------------------------------------ triggers
def split_declares(snip):
    decl = []
    s = snip.strip()
    while s.startswith('DECLARE'):
        i = s.index(';')
        decl.append(s[:i + 1]); s = s[i + 1:].strip()
    return decl, s


def trig(name, timing, event, table, body, decls=()):
    d = '\n  '.join(decls)
    return f"CREATE TRIGGER `{name}` {timing} {event} ON `{table}` FOR EACH ROW\nBEGIN\n  {d}\n  {body}\nEND$$\n"


def json_obj(prefix, t):
    gen = {c.split()[0] for c in t['raw']}
    cols = [c['name'] for c in t['columns'] if c['name'] not in t['audit_exclude'] and c['name'] not in gen]
    return 'JSON_OBJECT(' + ', '.join(f"'{c}', {prefix}.`{c}`" for c in cols) + ')'


def entity_id(prefix, t):
    parts = [f"CAST({prefix}.`{c}` AS CHAR)" for c in t['pkcols']]
    return parts[0] if len(parts) == 1 else 'CONCAT_WS(\'-\', ' + ', '.join(parts) + ')'


def gen_triggers(tabs):
    out = []
    for t in S.T:
        n = t['name']
        decls, bi = split_declares(t['bi'])
        _, bu = split_declares(t['bu'])
        bu_decls, bu = split_declares(t['bu'])
        if t['kind'] == 'E':
            esc = f"""SET NEW.recorded_by = app_user();
  SET NEW.recorded_by_role = app_role();
  SET NEW.recorded_at = NOW(3);
  IF NEW.occurred_at IS NULL THEN SET NEW.occurred_at = NOW(3); END IF;
  IF @request_uuid IS NOT NULL THEN
    SET @req_seq = IFNULL(@req_seq, 0) + 1;
    SET NEW.request_uuid = CONCAT(LEFT(@request_uuid, 23), '-', LEFT(MD5(CONCAT('{n}', @req_seq)), 12));
  END IF;
  IF NEW.occurred_at > NOW(3) + INTERVAL 5 MINUTE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Event time cannot be in the future'; END IF;
  IF NEW.is_backfilled = 0 AND NEW.occurred_at < NOW(3) - INTERVAL setting_int('backfill_threshold_minutes') MINUTE THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Old entry: mark it as backfilled (paper fallback) to record it';
  END IF;
  {bi}"""
            out.append(trig(f'trg_{n}_bi', 'BEFORE', 'INSERT', n, esc, decls))
            out.append(trig(f'trg_{n}_bu', 'BEFORE', 'UPDATE', n, "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This record cannot be changed; add a correction instead';"))
            out.append(trig(f'trg_{n}_bd', 'BEFORE', 'DELETE', n, "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This record cannot be deleted; add a correction instead';"))
        elif t['kind'] == 'M':
            has_created = any(c['name'] == 'created_by' for c in t['columns'])
            body = f"SET NEW.created_by = app_user(); SET NEW.updated_by = app_user();\n  {bi}"
            out.append(trig(f'trg_{n}_bi', 'BEFORE', 'INSERT', n, body, decls))
            out.append(trig(f'trg_{n}_bu', 'BEFORE', 'UPDATE', n, f"SET NEW.updated_by = app_user();\n  {bu}", bu_decls))
            out.append(trig(f'trg_{n}_bd', 'BEFORE', 'DELETE', n, "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Records are never deleted; mark inactive instead';"))
            out.append(trig(f'trg_{n}_ai', 'AFTER', 'INSERT', n,
                            f"INSERT INTO audit_log (action, entity_type, entity_id, old_values, new_values, reason)\n  VALUES ('insert', '{n}', {entity_id('NEW', t)}, NULL, {json_obj('NEW', t)}, @audit_reason);"))
            out.append(trig(f'trg_{n}_au', 'AFTER', 'UPDATE', n,
                            f"INSERT INTO audit_log (action, entity_type, entity_id, old_values, new_values, reason)\n  VALUES ('update', '{n}', {entity_id('NEW', t)}, {json_obj('OLD', t)}, {json_obj('NEW', t)}, @audit_reason);"))
        else:  # operational
            out.append(trig(f'trg_{n}_bd', 'BEFORE', 'DELETE', n, "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Counters are never deleted';"))
    return out


def gen_logic01(tabs):
    head = open(os.path.join(ROOT, 'tools/sql_src/01_foundation_head.sql')).read()
    trigs = gen_triggers(tabs)
    txt = ("-- GENERATED by tools/build_db.py  (head: tools/sql_src/01_foundation_head.sql  +  triggers from tools/schema_spec.py)\n"
           "USE clinic_db;\nDELIMITER $$\n\n" + head.replace('DELIMITER $$', '').replace('DELIMITER ;', '') +
           "\n-- ===================== generated triggers =====================\n" + '\n'.join(trigs) + "\nDELIMITER ;\n")
    write('db/logic/01_foundation_triggers.sql', txt)
    return len(trigs)


# ------------------------------------------------------------------ docs
def gen_doc(tabs, deferred, order):
    L = ['# 02_table_design.BASELINE.md  (generated, Track B)', '',
         '> Generated by `tools/build_db.py` from `tools/schema_spec.py`, which implements `docs/design/01_er_model.md` revision 2.',
         '> It is a **baseline for P1-3**: compare it with your ARCHITECT output using `python tools/compare_design.py <their_02_table_design.md>`.', '']
    kinds = collections.Counter(t['kind'] for t in S.T)
    L += [f'**Total tables: {len(S.T)}** (events {kinds["E"]}, masters {kinds["M"]}, operational {kinds["O"]}). '
          f'Extension tables outside the ER: ' + ', '.join(t['name'] for t in S.T if t['extension']) + '.', '',
          f'**Cross-file / cyclic FKs (in `09_foreign_keys.sql`): {len(deferred)}**', '', '## Build order', '',
          ', '.join(f'`{n}`' for n in order), '', '## Tables', '']
    for t in S.T:
        L.append(f"### `{t['name']}`  - {t['kind']} - cluster {CLUSTER_FILES[t['cluster']]}")
        L.append(t['comment'] + (f"  Rules: {t['rules']}" if t['rules'] else ''))
        L.append('')
        L.append('| column | type | null | default / FK |'); L.append('|---|---|---|---|')
        for c in t['columns']:
            extra = (f"FK -> {c['fk']}" if c['fk'] else '') + (f" default {c['default']}" if c['default'] else '')
            L.append(f"| `{c['name']}` | {c['sql']}{' enum' if c['enum'] else ''} | {'Y' if c['nullable'] else ''} | {extra} |")
        for r in t['raw']:
            L.append(f"| `{r.split()[0]}` | generated | | `{' '.join(r.split()[1:])}` |")
        L.append(f"\nPK: {', '.join(t['pkcols'])}. UNIQUE: {t['uniques'] or 'none'}. CHECKs: {len(check_list(t))}.\n")
    L += ['## Rule-to-table traceability', '', '| rule | tables |', '|---|---|']
    byrule = collections.defaultdict(list)
    for t in S.T:
        for r in [x for x in t['rules'].split(',') if x]:
            byrule[r.strip()].append(t['name'])
    for r in sorted(byrule, key=lambda x: [(0, int(p), '') if p.isdigit() else (1, 0, p) for p in re.split(r'[.R]', x) if p]):
        L.append(f"| {r} | {', '.join(byrule[r])} |")
    write('docs/design/02_table_design.BASELINE.md', '\n'.join(L) + '\n')


def write(rel, text):
    p = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


if __name__ == '__main__':
    tabs, deferred, order = gen_schema()
    n = gen_logic01(tabs)
    gen_doc(tabs, deferred, order)
    print(f'tables={len(S.T)} deferred_fks={len(deferred)} triggers={n}')
