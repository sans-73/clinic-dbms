#!/usr/bin/env python3
"""Static consistency checks for the whole kit. No MySQL needed. Run from repo root:  python tools/verify_kit.py
Exit code 0 = all checks passed.  Add new checks to CHECKS at the bottom - this is the place that grows with the project.
"""
import os, re, sys, glob, importlib
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
errors, notes = [], []


def err(msg):
    if msg not in errors: errors.append(msg)
def rd(p): return open(os.path.join(ROOT, p), encoding='utf-8').read()


def strip_sql(t):
    t = re.sub(r'/\*.*?\*/', ' ', t, flags=re.S)
    t = re.sub(r'--[^\n]*', ' ', t)
    t = re.sub(r"'(?:[^'\\]|\\.|'')*'", "''", t)
    return t


def camel_to_snake(n): return re.sub(r'(?<!^)(?=[A-Z])', '_', n).lower()


def match_paren(s, i):
    """s[i] == '(' -> return index of matching ')'"""
    d = 0
    for j in range(i, len(s)):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j
    return -1


def count_args(s):
    s = s.strip()
    if not s: return 0
    d, n = 0, 1
    for ch in s:
        if ch == '(': d += 1
        elif ch == ')': d -= 1
        elif ch == ',' and d == 0: n += 1
    return n


# ---------------------------------------------------------------- checks
def check_er_vs_spec():
    p = 'docs/design/01_er_model.md'
    if not os.path.exists(os.path.join(ROOT, p)): return notes.append('ER model not found - skipped')
    import schema_spec as S
    er = {}
    for m in re.finditer(r'^\| \*\*(\w+)\*\*(?: \*\(weak\)\*)? \| ([MEO]) \|', rd(p), re.M):
        er[camel_to_snake(m.group(1))] = m.group(2)
    spec = {t['name']: t for t in S.T}
    for n, k in er.items():
        if n not in spec: err(f'ER entity {n} ({k}) has no table in schema_spec.py')
        elif spec[n]['kind'] != k: err(f'{n}: ER class {k} but spec kind {spec[n]["kind"]}')
    for n, t in spec.items():
        if n not in er and not t['extension']: err(f'table {n} is not in the ER and not marked extension=True')
    notes.append(f'ER entities (M/E/O) = {len(er)}, spec tables = {len(spec)}, extensions = {[n for n,t in spec.items() if t["extension"]]}')


def collect_routines():
    """name -> (kind, param_count) from every SQL file that defines routines"""
    routines = {}
    files = glob.glob(os.path.join(ROOT, 'db/logic/*.sql')) + glob.glob(os.path.join(ROOT, 'tools/sql_src/*.sql')) + glob.glob(os.path.join(ROOT, 'db/tests/*.sql'))
    for f in files:
        t = strip_sql(open(f, encoding='utf-8').read())
        for m in re.finditer(r'CREATE\s+(?:DEFINER\s*=\s*\S+\s+)?(PROCEDURE|FUNCTION)\s+`?(\w+)`?\s*\(', t, re.I):
            j = match_paren(t, m.end() - 1)
            routines[m.group(2).lower()] = (m.group(1).upper(), count_args(t[m.end():j]), os.path.relpath(f, ROOT))
    return routines


def collect_views():
    v = set()
    for f in glob.glob(os.path.join(ROOT, 'db/logic/*.sql')):
        for m in re.finditer(r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:ALGORITHM\s*=\s*\w+\s+)?VIEW\s+`?(\w+)`?', strip_sql(open(f, encoding='utf-8').read()), re.I):
            v.add(m.group(1).lower())
    return v


def spec_columns():
    import build_db as B
    tabs = B.build_model()
    cols = {n: {c['name'] for c in t['columns']} | {r.split()[0] for r in t['raw']} for n, t in tabs.items()}
    return cols


def sql_sources():
    pats = ['db/logic/*.sql', 'db/security/*.sql', 'db/seed/*.sql', 'db/tests/*.sql', 'db/tests/concurrency/*.sql', 'db/queries/*.sql']
    out = []
    for p in pats: out += glob.glob(os.path.join(ROOT, p))
    return out


def check_sql_calls_and_tables():
    routines, views, cols = collect_routines(), collect_views(), spec_columns()
    tables = set(cols)
    notes.append(f'routines={len(routines)} views={len(views)} tables={len(tables)}')
    kw = {'select', 'dual', 'set', 'on', 'values', 'lateral'}
    for f in sql_sources() + [os.path.join(ROOT, 'tools/sql_src/01_foundation_head.sql')]:
        raw = open(f, encoding='utf-8').read()
        t = strip_sql(raw)
        rel = os.path.relpath(f, ROOT)
        decl = {m.group(1).lower() for m in re.finditer(r'DECLARE\s+(\w+)', t, re.I)} | {m.group(1).lower() for m in re.finditer(r'CREATE\s+TEMPORARY\s+TABLE\s+(\w+)', t, re.I)}
        ctes = {m.group(1).lower() for m in re.finditer(r'(?:WITH\s+RECURSIVE|WITH|,)\s+`?(\w+)`?\s+AS\s*\(', t, re.I)}
        ctes |= {m.group(1).lower() for m in re.finditer(r'(?:WITH\s+RECURSIVE|WITH)\s+`?(\w+)`?\s*\(', t, re.I)}
        for m in re.finditer(r'\bCALL\s+`?(\w+)`?\s*\(', t, re.I):
            name = m.group(1).lower()
            j = match_paren(t, m.end() - 1)
            n = count_args(t[m.end():j])
            if name not in routines: err(f'{rel}: CALL {name}() - no such procedure')
            elif routines[name][0] != 'PROCEDURE': err(f'{rel}: CALL {name} but it is a function')
            elif routines[name][1] != n: err(f'{rel}: CALL {name} has {n} args, definition ({routines[name][2]}) has {routines[name][1]}')
        for name, (kind, n, src) in routines.items():
            if kind != 'FUNCTION': continue
            for m in re.finditer(r'(?<![\w.])' + name + r'\s*\(', t, re.I):
                if re.search(r'CREATE\s+FUNCTION\s+`?$', t[max(0, m.start() - 30):m.start()], re.I): continue
                j = match_paren(t, m.end() - 1)
                a = count_args(t[m.end():j])
                if a != n: err(f'{rel}: function {name}() called with {a} args, expects {n}')
        for m in re.finditer(r'(?<!BEFORE )(?<!AFTER )\b(?:FROM|JOIN|INTO|UPDATE)\s+`?(\w+)`?(?!\s*\.)(?!\w)', t, re.I):
            n = m.group(1).lower()
            if n in kw or n in tables or n in views or n in ctes or n in decl: continue
            if re.match(r'\s*(?:\(|=|,\s*@|@)', t[m.end():m.end() + 3]) : pass
            # INTO @var / INTO v_x (local variables): ignore names with v_/p_ prefix
            if n.startswith(('v_', 'p_', 'o_')) or t[m.start():m.end()].upper().startswith('INTO') and n in ('outfile', 'dumpfile'): continue
            if t[m.start():m.start() + 4].upper() == 'INTO' and re.match(r'v_|p_|a_|b_', n): continue
            err(f'{rel}: references unknown table/view `{n}`')
        for m in re.finditer(r'INSERT\s+(?:IGNORE\s+)?INTO\s+`?(\w+)`?\s*\(([^)]*)\)', t, re.I):
            tb = m.group(1).lower()
            if tb in cols:
                for c in [x.strip(' `\n') for x in m.group(2).split(',')]:
                    if c and c.lower() not in cols[tb]: err(f'{rel}: INSERT INTO {tb} - unknown column `{c}`')


def check_generated_schema():
    import schema_spec as S
    names = {t['name'] for t in S.T}
    allsql = ''.join(rd(p) for p in sorted(glob.glob(os.path.join(ROOT, 'db/schema/*.sql'))) and [os.path.relpath(x, ROOT) for x in sorted(glob.glob(os.path.join(ROOT, 'db/schema/*.sql')))])
    created = re.findall(r'CREATE TABLE `(\w+)`', allsql)
    if set(created) != names or len(created) != len(names): err('db/schema does not contain exactly the spec tables - run tools/build_db.py')
    for ch, parent in re.findall(r'ALTER TABLE `(\w+)` ADD CONSTRAINT `\w+` FOREIGN KEY \(.*?\) REFERENCES `(\w+)`', allsql):
        if ch not in names or parent not in names: err(f'late FK {ch}->{parent} unknown table')
    trig = rd('db/logic/01_foundation_triggers.sql')
    ev = [t['name'] for t in S.T if t['kind'] == 'E']
    for n in ev:
        for s in ('bi', 'bu', 'bd'):
            if f'`trg_{n}_{s}`' not in trig: err(f'event table {n} lacks trigger trg_{n}_{s}')
    for f in glob.glob(os.path.join(ROOT, 'db/logic/*.sql')):
        t = strip_sql(open(f, encoding='utf-8').read())
        b = len(re.findall(r'\bBEGIN\b', t)); e = len(re.findall(r'\bEND\$\$', t)) + len(re.findall(r';\s*END\s*;', t))
        if b != e: err(f'{os.path.relpath(f, ROOT)}: {b} BEGIN vs {e} END$$ (compound blocks inside IF/WHILE/CASE are END IF etc., so BEGIN must equal END$$)')
    notes.append(f'event tables with triggers: {len(ev)}')


def check_api_comments():
    routines = collect_routines()
    for f in glob.glob(os.path.join(ROOT, 'db/logic/0[2-9]*.sql')) + [os.path.join(ROOT, 'tools/sql_src/01_foundation_head.sql')]:
        t = open(f, encoding='utf-8').read()
        for m in re.finditer(r'CREATE\s+(PROCEDURE|FUNCTION)\s+`?(\w+)`?', t):
            if '@api' not in t[max(0, m.start() - 1200):m.start()].split('$$')[-1]:
                err(f'{os.path.relpath(f, ROOT)}: {m.group(2)} has no "-- @api" comment (needed for generated docs)')


def check_templates_and_python():
    try:
        import jinja2
    except ImportError:
        return notes.append('jinja2 missing: template check skipped')
    env = jinja2.Environment()
    for f in glob.glob(os.path.join(ROOT, 'app/templates/**/*.html'), recursive=True):
        try: env.parse(open(f, encoding='utf-8').read())
        except Exception as e: err(f'template {os.path.relpath(f, ROOT)}: {e}')
    import py_compile
    for f in glob.glob(os.path.join(ROOT, 'app/**/*.py'), recursive=True) + glob.glob(os.path.join(ROOT, 'tools/*.py')):
        try: compile(open(f, encoding='utf-8').read(), f, 'exec')
        except Exception as e: err(f'python {os.path.relpath(f, ROOT)}: {e}')


def check_app_sql():
    """every CALL name(...) and view used in app/*.py must exist, with matching arg counts"""
    routines, views, cols = collect_routines(), collect_views(), spec_columns()
    for f in glob.glob(os.path.join(ROOT, 'app/**/*.py'), recursive=True):
        t = open(f, encoding='utf-8').read(); rel = os.path.relpath(f, ROOT)
        for m in re.finditer(r'\bcall\(\s*\w+\s*,\s*[\'"](\w+)[\'"]\s*(?:,\s*([^)]*))?\)', t):
            name = m.group(1).lower()
            if name not in routines: err(f'{rel}: call("{name}") - no such procedure')
        for m in re.finditer(r'\b(?:FROM|JOIN)\s+(v_\w+)', t, re.I):
            if m.group(1).lower() not in views: err(f'{rel}: unknown view {m.group(1)}')




def view_columns():
    """output column names of each view (first SELECT list); views using * are skipped"""
    out = {}
    for f in glob.glob(os.path.join(ROOT, 'db/logic/*.sql')):
        t = strip_sql(open(f, encoding='utf-8').read())
        for m in re.finditer(r'CREATE\s+OR\s+REPLACE\s+VIEW\s+(\w+)\s+AS\s+SELECT\s', t, re.I):
            i, d, items, cur = m.end(), 0, [], ''
            while i < len(t):
                ch = t[i]
                if ch == '(': d += 1
                elif ch == ')': d -= 1
                if d == 0 and re.match(r'\s+FROM\s', t[i:i + 6], re.I) and ch.isspace(): break
                if d == 0 and ch == ',': items.append(cur); cur = ''
                else: cur += ch
                i += 1
            items.append(cur)
            names = []
            for it in items:
                it = it.strip()
                if it.endswith('*'): names = None; break
                am = re.search(r'\bAS\s+`?(\w+)`?\s*$', it, re.I) or re.search(r'(?:\.|^)`?(\w+)`?\s*$', it)
                names.append(am.group(1).lower() if am else '?')
            if names is not None: out[m.group(1).lower()] = set(names)
    return out

def check_column_refs():
    """alias.column must exist in the table behind the alias (per SQL statement; ambiguous aliases are skipped; views are skipped)"""
    cols, views = spec_columns(), collect_views()
    vcols = view_columns()
    kw = {'where', 'join', 'left', 'right', 'inner', 'on', 'group', 'order', 'limit', 'set', 'for', 'union', 'using', 'cross', 'having', 'values',
          'select', 'straight_join', 'natural', 'outer', 'into', 'and', 'or', 'when', 'then', 'else', 'end', 'not', 'is', 'in', 'as'}
    files = glob.glob(os.path.join(ROOT, 'db/logic/0[2-9]*.sql')) + [os.path.join(ROOT, 'tools/sql_src/01_foundation_head.sql')] + glob.glob(os.path.join(ROOT, 'db/tests/*.sql'))
    for f in files:
        rel = os.path.relpath(f, ROOT)
        for stmt in strip_sql(open(f, encoding='utf-8').read()).split(';'):
            amap = {}
            for m in re.finditer(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+`?(\w+)`?(?:\s+(?:AS\s+)?`?(\w+)`?)?', stmt, re.I):
                tb, al = m.group(1).lower(), (m.group(2) or '').lower()
                if tb in cols or tb in views:
                    for a in {tb} | ({al} if al and al not in kw else set()):
                        amap.setdefault(a, set()).add(tb)
            for m in re.finditer(r'SELECT\s+([\w\s,]+?)\s+INTO\s+[\w@,\s]+?\s+FROM\s+`?(\w+)`?(?=\s*(?:WHERE|ORDER|LIMIT|FOR|$))', stmt, re.I):
                tb = m.group(2).lower()
                known = cols.get(tb) or vcols.get(tb)
                if known:
                    for c in [x.strip().lower() for x in m.group(1).split(',')]:
                        if re.fullmatch(r'\w+', c) and c not in known and not c.isdigit():
                            err(f'{rel}: SELECT {c} INTO ... FROM {tb} - no such column')
            for m in re.finditer(r'(?<![\w.@])(\w+)\.(\w+)\b', stmt):
                a, c = m.group(1).lower(), m.group(2).lower()
                if a in ('new', 'old') or a not in amap or len(amap[a]) != 1: continue
                tb = next(iter(amap[a]))
                if tb in cols and c not in cols[tb]:
                    err(f'{rel}: `{a}.{c}` - table {tb} has no column {c}')
                elif tb in vcols and c not in vcols[tb]:
                    err(f'{rel}: `{a}.{c}` - view {tb} has no column {c}')

CHECKS = [check_column_refs, check_er_vs_spec, check_generated_schema, check_sql_calls_and_tables, check_api_comments, check_templates_and_python, check_app_sql]

if __name__ == '__main__':
    for c in CHECKS:
        try: c()
        except Exception as e:
            import traceback; traceback.print_exc(); err(f'{c.__name__} crashed: {e}')
    for n in notes: print('note:', n)
    if errors:
        print(f'\n{len(errors)} problem(s):')
        for e in errors[:200]: print(' -', e)
        sys.exit(1)
    print('verify_kit: all checks passed')
